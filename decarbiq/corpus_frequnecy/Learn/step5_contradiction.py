"""
DecarbIQ Learning Layer — Step 5: Contradiction Detection
=========================================================
Finds conflicting claims, applies confident auto-resolve rules (termination,
wording deduplication), detects confidence inversions, and surfaces everything
else for human review.

Scope restriction (Fix 2):
  Multi-record entity false positives (Air Products facilities, CMS
  subsidiaries, DOE award recipients with multiple projects) suppressed.
  Comparison gated by same source_id for intrinsically multi-valued types.

Wording deduplication (Fix 3):
  Claims of the same type whose texts are >=70% similar by word overlap are
  auto-resolved as DUPLICATE instead of flagged UNKNOWN.

Extended context + LLM pre-check (Fix 6):
  The LLM is now called DURING detection, not at review time.  Every pair
  that survives the rule-based filters is passed to the LLM with full source
  context (CONTEXT_WINDOW_SENTENCES surrounding sentences from the original
  document) before any decision is made.

  High-confidence LLM decisions (confidence == "high") are auto-resolved and
  never reach the human review queue.  The LLM reasoning is stored in the
  contradiction_log so you can audit it.

  Low/medium-confidence pairs land in the review queue pre-populated with the
  LLM's reasoning — you see WHY it was uncertain without re-calling the LLM.

  Interactive review (--review) shows context + stored reasoning and lets you
  accept or override.  Use --no-llm to skip LLM calls entirely (fast mode).

Usage:
    python step5_contradiction.py                    # detection + LLM pre-check
    python step5_contradiction.py --no-llm           # detection only, no LLM
    python step5_contradiction.py --review           # review queue (no re-LLM)
    python step5_contradiction.py --review --no-llm  # review without LLM context

Reads:  data/core_database.db (claims table, already populated by step3)
Writes: data/core_database.db (contradiction_log table — REPLACES prior run)
"""

import sqlite3
import re
import sys
import json
import textwrap
from datetime import datetime, timezone
from collections import defaultdict

from config import LEARNING_DB, CONFIDENCE_DROP_THRESHOLD

# ── Config ───────────────────────────────────────────────────────────────────

# Claim types where multiple different claims of the same type within ONE
# document are always a list (subsidiaries, tasks, material properties) —
# never contradictions, even from the same source_id. Skip entirely.
ALWAYS_ADDITIVE_TYPES = {
    "subsidiary_identification",   # EX-21.1 lists every subsidiary
    "subsidiary_establishment",    # EX-21.1 lists every subsidiary (alternate LLM label)
    "subsidiary_ownership",
    "project_task",                # Award doc lists multiple tasks
    "material_property",           # Doc lists many SiC/material properties
    "research_area_identified",    # Doc lists many research areas
    "primary_objective",           # Multiple objectives per project
    "contact_information",         # Press release: IR contact + media contact
    "loan_facility",               # Project finance stacks have multiple tranches
                                   # (e.g. NEOM: SAR variable, USD variable, USD stated)
                                   # — always additive even within the same filing
}

# Claim types that are intrinsically multi-valued ACROSS documents for one
# company — two claims from DIFFERENT source_ids are additive, not
# contradicting. Only compare within the same source_id for these.
MULTI_VALUE_TYPES = {
    # Facility records — one company has many plants across docs
    "facility_location", "facility_name", "sic_code", "facility_sic_code",
    "facility_type", "facility_relevance", "facility_sic_codes",
    # Corporate (cross-doc additive)
    "company_jurisdiction",
    # Award / project — recipients have many projects across different docs
    "award_received", "award_amount", "project_objective", "project_goal",
    "project_location", "project_focus", "project_scope", "project_funding",
    "project_funding_source", "project_title", "research_focus",
    "agreement_type", "funding_agency", "funding_awarded", "grant_type",
    "project_description", "project_partnership",
    "award_type", "funding_amount", "project_purpose", "research_area",
    "research_objective", "research_project", "project_duration",
}

# Minimum word-overlap ratio to call two claim_texts "same claim, different wording"
DEDUP_THRESHOLD = 0.70

# Sentences of surrounding context to show per claim in interactive review
CONTEXT_WINDOW_SENTENCES = 5

# Documents shorter than this are shown in full rather than windowed —
# short docs (DOE awards avg 521 chars) contain no filler worth hiding
SHORT_DOC_THRESHOLD = 1200  # chars

# LLM model for contradiction comparison (same provider as step3)
LLM_MODEL = "llama-3.3-70b-versatile"


# ── Helpers ──────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_numbers(text: str) -> str:
    """Strip thousands commas so $23,291,563 and $23291563 compare equal."""
    return re.sub(r'(?<=\d),(?=\d{3})', '', text)


def word_overlap(a: str, b: str) -> float:
    """
    Jaccard-style word overlap: |intersection| / |union|.
    Ignores stopwords and punctuation for cleaner comparison.
    Numbers are normalised before tokenisation so $23,291,563 == $23291563.
    """
    stopwords = {
        "the", "a", "an", "is", "are", "was", "were", "has", "have", "had",
        "of", "in", "for", "to", "and", "or", "with", "at", "by", "from",
        "its", "it", "as", "on", "that", "this", "be", "will", "not",
        "between", "into", "than", "more", "also", "been",
    }

    def tokens(text: str) -> set:
        words = re.findall(r"[a-z0-9]+", normalize_numbers(text).lower())
        return {w for w in words if w not in stopwords and len(w) > 1}

    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def contains_termination_keyword(text: str) -> bool:
    kw = ["terminat", "cancel", "writeoff", "write-off", "no_longer",
          "suspend", "abandon", "exit", "wind down", "wind-down"]
    low = text.lower()
    return any(k in low for k in kw)


def detect_contradiction_type(type_a: str, text_a: str,
                               type_b: str, text_b: str) -> str:
    """
    Returns one of: TERMINATION | VALUE_CHANGE | STATUS_CHANGE | UNKNOWN
    """
    if contains_termination_keyword(type_b) or contains_termination_keyword(text_b):
        return "TERMINATION"

    # Value change: both texts contain dollar or percentage figures that differ
    nums = re.compile(r"[\$\u20ac\u00a3]?[\d,]+(?:\.\d+)?[MBK%]?\b")
    vals_a = set(nums.findall(text_a))
    vals_b = set(nums.findall(text_b))
    if vals_a and vals_b and vals_a != vals_b:
        return "VALUE_CHANGE"

    if "status" in type_b.lower() or "status" in type_a.lower():
        return "STATUS_CHANGE"

    return "UNKNOWN"


def get_source_context(db: sqlite3.Connection, source_id: int,
                       claim_text: str, window: int = CONTEXT_WINDOW_SENTENCES) -> str:
    """
    Fetch surrounding text from the cleaned source document.

    Strategy:
      - Short docs (≤ SHORT_DOC_THRESHOLD chars): show the entire document.
        DOE award records average ~521 chars — there is nothing to hide.
      - Longer docs: split on sentence boundaries AND newlines (DOE format),
        find the sentence with the highest word overlap against claim_text,
        return `window` sentences before and after it.

    Returns a plain string ready to print.
    """
    row = db.execute(
        "SELECT clean_text FROM cleaned_documents WHERE source_id=?", (source_id,)
    ).fetchone()
    if not row or not row[0]:
        return "  [source text unavailable]"

    doc = row[0]

    # Short doc — show everything
    if len(doc) <= SHORT_DOC_THRESHOLD:
        wrapped = textwrap.fill(doc, width=100,
                                initial_indent="  ", subsequent_indent="  ")
        return f"  [full document — {len(doc)} chars]\n{wrapped}"

    # Longer doc — find the best-matching passage and show a window around it
    # Split on sentence-ending punctuation OR newlines (covers DOE award format)
    sentences = re.split(r'(?<=[.!?])\s+|\n+', doc.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    def _tokens(t: str) -> set:
        return set(re.findall(r"[a-z0-9]+", t.lower()))

    claim_tok = _tokens(claim_text)
    best_idx, best_score = 0, -1.0
    for i, sent in enumerate(sentences):
        sent_tok = _tokens(sent)
        if not sent_tok:
            continue
        score = len(claim_tok & sent_tok) / max(len(claim_tok | sent_tok), 1)
        if score > best_score:
            best_score, best_idx = score, i

    start = max(0, best_idx - window)
    end   = min(len(sentences), best_idx + window + 1)
    chunk = " ".join(sentences[start:end])

    wrapped = textwrap.fill(chunk, width=100,
                            initial_indent="  ", subsequent_indent="  ")
    match_info = (
        f"  [sentence {best_idx+1}/{len(sentences)}, "
        f"showing {start+1}–{end}, overlap={best_score:.2f}, "
        f"doc_len={len(doc)}]"
    )
    return match_info + "\n" + wrapped


def llm_compare_claims(
    context_a: str, claim_a: str, date_a: str,
    context_b: str, claim_b: str, date_b: str,
    claim_type: str, company: str,
) -> dict:
    """
    Ask the LLM to compare two claim contexts and suggest a resolution.

    Returns a dict:
      {
        "suggestion":  "SUPERSEDE" | "KEEP_BOTH" | "IGNORE",
        "confidence":  "high" | "medium" | "low",
        "reason":      "<one sentence>",
        "detail":      "<up to 3 sentences of supporting reasoning>",
      }
    Returns {"suggestion": "UNKNOWN", ...} on any error.
    """
    try:
        from groq import Groq
        from config import GROQ_API_KEY
        client = Groq(api_key=GROQ_API_KEY)
    except Exception as e:
        return {"suggestion": "UNKNOWN", "confidence": "low",
                "reason": f"LLM unavailable: {e}", "detail": ""}

    prompt = f"""You are a hydrogen energy intelligence analyst reviewing two extracted claims from regulatory filings.

COMPANY: {company}
CLAIM TYPE: {claim_type}

--- CLAIM A (filed {date_a or 'unknown date'}) ---
Extracted claim: {claim_a}

Source context:
{context_a}

--- CLAIM B (filed {date_b or 'unknown date'}) ---
Extracted claim: {claim_b}

Source context:
{context_b}

---
Are these two claims describing the SAME fact/event, DIFFERENT facts/events, or is claim B an UPDATE that supersedes claim A?

Respond ONLY with a JSON object, no other text:
{{
  "suggestion": "SUPERSEDE" | "KEEP_BOTH" | "IGNORE",
  "confidence": "high" | "medium" | "low",
  "reason": "<one sentence explaining the suggestion>",
  "detail": "<up to 3 sentences of supporting analysis>"
}}

Rules:
- SUPERSEDE: Claim B is a newer version of the same fact and claim A should be marked outdated.
- KEEP_BOTH: They describe different aspects or events and both are valid intelligence.
- IGNORE: They are essentially the same claim and one is redundant (no meaningful difference).
- If same document date and different content, prefer KEEP_BOTH unless one clearly corrects the other.
- If the claims describe sequential steps in a project (e.g. task A and task B), use KEEP_BOTH.
"""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
        raw = re.sub(r'\s*```$', '', raw, flags=re.DOTALL)
        return json.loads(raw)
    except Exception as e:
        return {"suggestion": "UNKNOWN", "confidence": "low",
                "reason": f"Parse error: {e}", "detail": ""}


# ── Interactive review ───────────────────────────────────────────────────────

def print_review(db: sqlite3.Connection, interactive: bool = False,
                 use_llm: bool = True):
    """
    Print all unresolved contradictions for human review.

    Each item shows:
      - Full extracted claim text for both sides
      - CONTEXT_WINDOW_SENTENCES of surrounding text from the source doc
      - LLM pre-analysis reasoning stored during detection (no re-call)

    If interactive=True, prompt for a decision on each one.
    Press Enter to accept the LLM suggestion if one was stored, or type s/k/i.

    use_llm is accepted for API compatibility but ignored here — the LLM
    already ran during detection and its output is in contradiction_log.resolution.
    """
    rows = db.execute("""
        SELECT cl.id, cl.claim_id_a, cl.claim_id_b, cl.company_name,
               cl.claim_type, cl.contradiction_type, cl.resolution
        FROM contradiction_log cl
        WHERE cl.reviewed_at IS NULL AND cl.auto_resolved = 0
        ORDER BY cl.company_name, cl.claim_type
    """).fetchall()

    if not rows:
        print("\n  No contradictions flagged for review.")
        return

    print(f"\n=== CONTRADICTIONS FLAGGED FOR YOUR REVIEW ({len(rows)}) ===\n")

    now = now_iso()
    valid_decisions = {"s": "SUPERSEDE", "k": "KEEP_BOTH", "i": "IGNORE"}

    for idx, row in enumerate(rows, 1):
        cid               = row["id"]
        id_a              = row["claim_id_a"]
        id_b              = row["claim_id_b"]
        company           = row["company_name"]
        ctype             = row["claim_type"]
        ctr_type          = row["contradiction_type"]
        stored_resolution = row["resolution"] or ""

        claim_a = db.execute(
            "SELECT source_id, document_date, claim_text, confidence FROM claims WHERE claim_id=?",
            (id_a,)
        ).fetchone()
        claim_b = db.execute(
            "SELECT source_id, document_date, claim_text, confidence FROM claims WHERE claim_id=?",
            (id_b,)
        ).fetchone()

        if not claim_a or not claim_b:
            continue

        sep = "─" * 70
        print(sep)
        print(f"[{idx}/{len(rows)}] {company} — {ctype}  [{ctr_type}]")
        print()

        # ── Claim A with context ──────────────────────────────────────────────
        print(f"  OLDER  {claim_a['document_date'] or 'no-date'}"
              f"  conf={claim_a['confidence']:.2f}  [claim_id: {id_a[:8]}]")
        print(f"  Claim: \"{claim_a['claim_text']}\"")
        print("  Context:")
        print(get_source_context(db, claim_a["source_id"], claim_a["claim_text"]))
        print()

        # ── Claim B with context ──────────────────────────────────────────────
        print(f"  NEWER  {claim_b['document_date'] or 'no-date'}"
              f"  conf={claim_b['confidence']:.2f}  [claim_id: {id_b[:8]}]")
        print(f"  Claim: \"{claim_b['claim_text']}\"")
        print("  Context:")
        print(get_source_context(db, claim_b["source_id"], claim_b["claim_text"]))
        print()

        # ── LLM pre-analysis (stored during detection) ────────────────────────
        # resolution column holds e.g. "LLM(medium): suggested KEEP_BOTH — reason..."
        suggestion = None
        if stored_resolution.startswith("LLM("):
            print(f"  LLM pre-analysis: {stored_resolution}")
            for candidate in ("SUPERSEDE", "KEEP_BOTH", "IGNORE"):
                if candidate in stored_resolution:
                    suggestion = candidate
                    break
            print()
        elif not stored_resolution:
            print("  LLM pre-analysis: not available (re-run without --no-llm to enable)")
            print()

        # ── Prompt ───────────────────────────────────────────────────────────
        if interactive:
            if suggestion:
                sug_key = next(k for k, v in valid_decisions.items() if v == suggestion)
                prompt_str = (
                    f"  [S]upersede / [K]eep both / [I]gnore / [Q]uit"
                    f"  (Enter = accept '{suggestion}'): "
                )
            else:
                sug_key   = None
                prompt_str = "  [S]upersede older / [K]eep both / [I]gnore / [Q]uit review? "

            while True:
                choice = input(prompt_str).strip().lower()
                if choice == "" and sug_key:
                    choice = sug_key
                if choice == "q":
                    print("  Review paused. Re-run with --review to continue.")
                    db.commit()
                    return
                if choice in valid_decisions:
                    break
                print("  Invalid choice. Enter s, k, i, or q.")

            decision = valid_decisions[choice]
            db.execute("""
                UPDATE contradiction_log
                SET reviewed_at = ?, review_decision = ?
                WHERE id = ?
            """, (now, decision, cid))

            if decision == "SUPERSEDE":
                db.execute(
                    "UPDATE claims SET superseded_by = ? WHERE claim_id = ?",
                    (id_b, id_a)
                )
            elif decision == "IGNORE":
                # Keep the richer claim, supersede the thinner one
                len_a = len(claim_a["claim_text"] or "")
                len_b = len(claim_b["claim_text"] or "")
                if len_a >= len_b:
                    # a is richer: b is the redundant one
                    db.execute(
                        "UPDATE claims SET superseded_by = ? WHERE claim_id = ?",
                        (id_a, id_b)
                    )
                else:
                    # b is richer: a is the redundant one
                    db.execute(
                        "UPDATE claims SET superseded_by = ? WHERE claim_id = ?",
                        (id_b, id_a)
                    )

            accepted = " (accepted LLM)" if suggestion and decision == suggestion else ""
            print(f"  -> {decision}{accepted}")
        else:
            print("  Options: SUPERSEDE older | KEEP_BOTH | IGNORE")

        print()

    if interactive:
        db.commit()
        reviewed  = db.execute(
            "SELECT COUNT(*) FROM contradiction_log WHERE reviewed_at IS NOT NULL"
        ).fetchone()[0]
        remaining = db.execute(
            "SELECT COUNT(*) FROM contradiction_log WHERE reviewed_at IS NULL AND auto_resolved=0"
        ).fetchone()[0]
        print(f"  Review complete. Total reviewed: {reviewed}. Remaining: {remaining}.")


def run(interactive: bool = False, use_llm: bool = True):
    conn = sqlite3.connect(str(LEARNING_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Clear previous run
    cur.execute("DELETE FROM contradiction_log")
    conn.commit()

    # Load all claims, ordered oldest-first so claim_id_a is always older
    claims = cur.execute("""
        SELECT claim_id, source_id, company_name, document_type,
               document_date, claim_type, claim_text, confidence
        FROM claims
        ORDER BY document_date ASC, extracted_at ASC
    """).fetchall()

    print(f"Loaded {len(claims)} claims. Building contradiction index...")

    # Group by (company_name, claim_type) for pairing
    index = defaultdict(list)
    for c in claims:
        key = (c["company_name"], c["claim_type"])
        index[key].append(c)

    inserted = 0
    auto_resolved = 0
    deduped = 0
    confidence_inversions = 0
    skipped_multi_value = 0
    llm_auto_resolved = 0

    for (company, ctype), group in index.items():
        if len(group) < 2:
            continue

        for i, ca in enumerate(group):
            for cb in group[i + 1:]:
                # ca is older (earlier index), cb is newer

                # ── Always-additive types (skip entirely) ────────────────
                if ctype in ALWAYS_ADDITIVE_TYPES:
                    skipped_multi_value += 1
                    continue

                # ── Scope restriction (cross-doc multi-value) ────────────
                if ctype in MULTI_VALUE_TYPES and ca["source_id"] != cb["source_id"]:
                    skipped_multi_value += 1
                    continue

                text_a = ca["claim_text"] or ""
                text_b = cb["claim_text"] or ""

                # ── Wording deduplication ────────────────────────────────
                overlap = word_overlap(text_a, text_b)
                if overlap >= DEDUP_THRESHOLD:
                    cur.execute("""
                        INSERT INTO contradiction_log
                        (claim_id_a, claim_id_b, company_name, claim_type,
                         contradiction_type, auto_resolved, resolution,
                         resolution_rule, flagged_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        ca["claim_id"], cb["claim_id"],
                        company, ctype,
                        "DUPLICATE",
                        1,  # auto_resolved
                        "Claims are the same fact in different wording",
                        f"word_overlap={overlap:.2f} >= {DEDUP_THRESHOLD}",
                        now_iso(),
                    ))
                    deduped += 1
                    inserted += 1
                    auto_resolved += 1
                    continue

                # ── Detect contradiction type ────────────────────────────
                ctype_detected = detect_contradiction_type(
                    ca["claim_type"], text_a,
                    cb["claim_type"], text_b,
                )

                # ── Auto-resolve: TERMINATION ────────────────────────────
                if ctype_detected == "TERMINATION":
                    cur.execute("""
                        INSERT INTO contradiction_log
                        (claim_id_a, claim_id_b, company_name, claim_type,
                         contradiction_type, auto_resolved, resolution,
                         resolution_rule, flagged_at)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, (
                        ca["claim_id"], cb["claim_id"],
                        company, ctype,
                        "TERMINATION",
                        1,  # auto_resolved
                        "Later termination/suspension supersedes earlier announcement",
                        "claim_type or claim_text contains termination keyword",
                        now_iso(),
                    ))
                    # Mark older claim as superseded
                    cur.execute("""
                        UPDATE claims SET superseded_by=? WHERE claim_id=?
                    """, (cb["claim_id"], ca["claim_id"]))
                    auto_resolved += 1
                    inserted += 1
                    continue

                # ── Confidence inversion ─────────────────────────────────
                conf_a = ca["confidence"]
                conf_b = cb["confidence"]
                if conf_a is not None and conf_b is not None:
                    drop = conf_a - conf_b
                    if drop > CONFIDENCE_DROP_THRESHOLD:
                        cur.execute("""
                            INSERT INTO contradiction_log
                            (claim_id_a, claim_id_b, company_name, claim_type,
                             contradiction_type, auto_resolved, resolution,
                             flagged_at)
                            VALUES (?,?,?,?,?,?,?,?)
                        """, (
                            ca["claim_id"], cb["claim_id"],
                            company, ctype,
                            "CONFIDENCE_INVERSION",
                            0,  # needs review
                            f"Confidence dropped from {conf_a:.2f} to {conf_b:.2f} (delta={drop:.2f})",
                            now_iso(),
                        ))
                        cur.execute(
                            "UPDATE claims SET flagged_for_review=1, flag_reason=? WHERE claim_id IN (?, ?)",
                            (f"Confidence inversion: {conf_a:.2f} -> {conf_b:.2f}",
                             ca["claim_id"], cb["claim_id"]),
                        )
                        confidence_inversions += 1
                        inserted += 1
                        continue

                # ── LLM pre-check ────────────────────────────────────────
                # Call LLM with full source context before deciding whether
                # to auto-resolve or escalate to human review.
                llm_suggestion = None
                llm_confidence = None
                llm_reason     = None

                if use_llm:
                    ctx_a = get_source_context(conn, ca["source_id"], text_a)
                    ctx_b = get_source_context(conn, cb["source_id"], text_b)
                    llm_result = llm_compare_claims(
                        ctx_a, text_a, ca["document_date"],
                        ctx_b, text_b, cb["document_date"],
                        ctype, company,
                    )
                    llm_suggestion = llm_result.get("suggestion", "UNKNOWN")
                    llm_confidence = llm_result.get("confidence", "low")
                    llm_reason     = llm_result.get("reason", "")
                    llm_detail     = llm_result.get("detail", "")
                    llm_reason_full = f"{llm_reason} {llm_detail}".strip()

                    # High-confidence → auto-resolve, never reaches human queue
                    if llm_confidence == "high" and llm_suggestion in ("SUPERSEDE", "KEEP_BOTH", "IGNORE"):
                        cur.execute("""
                            INSERT INTO contradiction_log
                            (claim_id_a, claim_id_b, company_name, claim_type,
                             contradiction_type, auto_resolved, resolution,
                             resolution_rule, flagged_at)
                            VALUES (?,?,?,?,?,?,?,?,?)
                        """, (
                            ca["claim_id"], cb["claim_id"],
                            company, ctype,
                            ctype_detected,
                            1,  # auto_resolved
                            llm_reason_full,
                            f"llm:high:{llm_suggestion}",
                            now_iso(),
                        ))

                        if llm_suggestion == "SUPERSEDE":
                            # Newer (cb) supersedes older (ca)
                            cur.execute(
                                "UPDATE claims SET superseded_by=? WHERE claim_id=?",
                                (cb["claim_id"], ca["claim_id"]),
                            )

                        elif llm_suggestion == "IGNORE":
                            # One claim is redundant — keep the richer one,
                            # supersede the thinner one regardless of age.
                            len_a = len(text_a)
                            len_b = len(text_b)
                            if len_a >= len_b:
                                # a is richer: supersede b → a
                                cur.execute(
                                    "UPDATE claims SET superseded_by=? WHERE claim_id=?",
                                    (ca["claim_id"], cb["claim_id"]),
                                )
                            else:
                                # b is richer: supersede a → b
                                cur.execute(
                                    "UPDATE claims SET superseded_by=? WHERE claim_id=?",
                                    (cb["claim_id"], ca["claim_id"]),
                                )

                        auto_resolved += 1
                        inserted += 1
                        llm_auto_resolved += 1
                        continue

                # Low/medium confidence or no LLM → flag for human review.
                # Store LLM reasoning so reviewer sees it without re-calling.
                review_note = None
                if llm_suggestion and llm_suggestion != "UNKNOWN":
                    review_note = (
                        f"LLM({llm_confidence}): suggested {llm_suggestion} — {llm_reason_full}"
                    )

                cur.execute("""
                    INSERT INTO contradiction_log
                    (claim_id_a, claim_id_b, company_name, claim_type,
                     contradiction_type, auto_resolved, resolution, flagged_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    ca["claim_id"], cb["claim_id"],
                    company, ctype,
                    ctype_detected,
                    0,  # needs human review
                    review_note,
                    now_iso(),
                ))
                inserted += 1

    conn.commit()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\n{'='*55}")
    print(f"CONTRADICTION DETECTION COMPLETE")
    print(f"{'='*55}")
    print(f"  Total pairs evaluated:           {inserted}")
    print(f"  Auto-resolved (DUPLICATE):       {deduped}")
    print(f"  Auto-resolved (TERMINATION):     {auto_resolved - deduped - llm_auto_resolved}")
    print(f"  Auto-resolved (LLM high-conf):   {llm_auto_resolved}")
    print(f"  Confidence inversions flagged:   {confidence_inversions}")
    print(f"  Skipped (multi-value):           {skipped_multi_value}")
    print(f"  Needs your review:               {inserted - auto_resolved}")
    print(f"\nFlagged for review by type:")

    rows = conn.execute("""
        SELECT contradiction_type, COUNT(*) n
        FROM contradiction_log
        WHERE auto_resolved=0
        GROUP BY contradiction_type ORDER BY n DESC
    """).fetchall()
    for r in rows:
        print(f"    {r[0]:<25} {r[1]}")

    print(f"\nAuto-resolved breakdown:")
    rows2 = conn.execute("""
        SELECT contradiction_type, COUNT(*) n
        FROM contradiction_log
        WHERE auto_resolved=1
        GROUP BY contradiction_type ORDER BY n DESC
    """).fetchall()
    for r in rows2:
        print(f"    {r[0]:<25} {r[1]}")

    # ── Interactive review (if requested) ────────────────────────────────────
    print_review(conn, interactive=interactive, use_llm=use_llm)
    conn.close()


if __name__ == "__main__":
    review_mode = "--review" in sys.argv
    no_llm      = "--no-llm" in sys.argv
    run(interactive=review_mode, use_llm=not no_llm)