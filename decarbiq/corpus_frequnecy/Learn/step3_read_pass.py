"""
DecarbIQ Learning Layer — Step 3: Read Pass
One open-ended extraction call per document (or per chunk for long docs).
LLM names claim types from content — no predefined vocabulary.
Primary: Claude (Anthropic). Fallback: Groq (Llama).

Structured fields are optional per-claim — the LLM fills them when confident.
Cross-document corroboration tracks repeated facts without duplicating claims.
Inline chunking splits long documents into overlapping windows.
Gap-driven re-read (--fill-gaps) targets projects with missing critical fields.
"""

import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

import anthropic
from groq import Groq

from config import (
    LEARNING_DB,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    CLAUDE_INPUT_COST_PER_M,
    CLAUDE_OUTPUT_COST_PER_M,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_INPUT_COST_PER_M,
    GROQ_OUTPUT_COST_PER_M,
    GROQ_SLEEP_BETWEEN_CALLS,
    GROQ_MAX_RETRIES,
)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a document analyst. Your job is to read a document about energy, hydrogen,
carbon capture, or industrial projects and extract every piece of useful intelligence.

You must NOT use any knowledge outside this document.

For each notable piece of information, create a claim entry with:

- claim_type: a specific snake_case name you choose. Be precise:
  "construction_commenced" not "project_milestone",
  "offtake_agreement_signed" not "business_update".

  For consistency, prefer these names when they fit (but invent new ones when needed):
    Location:    facility_location, project_site, headquarters_location
    Capacity:    production_capacity, injection_capacity, storage_capacity, capture_capacity
    Technology:  hydrogen_production_method, capture_technology, electrolysis_technology
    Stage:       construction_commenced, fid_announced, commercial_operation_date,
                 project_announced, project_cancelled, permit_received, permit_applied
    Dates:       construction_start_date, expected_cod, fid_date, operational_date
    Finance:     federal_award, funding_amount, capital_expenditure, offtake_agreement
    Corporate:   parent_company, subsidiary, joint_venture, epc_contractor, partner
    Regulatory:  permit_status, epa_class_vi_permit, ghgrp_reporting, uic_permit
    Emissions:   co2_emissions, co2_captured, co2_stored, ghg_reduction

- claim_text: the COMPLETE sentence from the source, preserved verbatim.
  NEVER split a sentence. Keep multi-clause sentences whole.
  Preserve original wording — length is fine.

- confidence: 0.0–1.0 based on language strength
  0.9–1.0: definitive facts ("broke ground", "received permit")
  0.7–0.89: strong present-tense ("is under construction")
  0.5–0.69: announced intentions ("plans to", "expects to")
  0.3–0.49: speculative ("may", "could")
  0.1–0.29: hedged ("subject to", "no assurance")

- commitment_level: factual | indicative | speculative
- time_sensitivity: current | historical | forward_looking

- structured (OPTIONAL object — include ONLY fields you can confidently extract
  from this specific claim. Omit the entire key if nothing concrete to extract.
  Omit individual fields you're not confident about. Never guess.):
  {
    "company": "...",
    "project": "...",
    "city": "...",
    "state": "two-letter code",
    "county": "...",
    "capacity": "raw text like '1.5 MTPA' or '500 MW'",
    "technology": "smr | atr | electrolysis | ccs | dac | blue_ammonia | pox | ...",
    "stage": "operational | construction | permitted | fid | announced | cancelled | ...",
    "date": "YYYY or YYYY-MM-DD",
    "date_type": "fid | cod | construction_start | permit_issued | ...",
    "permit_id": "...",
    "parent_company": "...",
    "partners": ["...", "..."],
    "funding": "raw text like 'Up to $270M federal'",
    "source_keyword": "the exact phrase from the source text that drove this classification"
  }

Return JSON: {"claims": [...]}
Every notable fact, number, date, name, relationship, or status gets its own claim.
A document may produce 1 claim or 30 — extract ALL of them. Leave nothing out.
"""

TARGETED_SYSTEM_PROMPT = """\
You are a document analyst performing a TARGETED re-read.
The project database is missing specific fields for this company.
Re-read the document and extract ONLY claims that help fill the gaps listed below.
Use the same JSON format: {"claims": [...]} with claim_type, claim_text, confidence,
commitment_level, time_sensitivity, and structured fields.
If you find nothing relevant to the missing fields, return {"claims": []}.
"""

USER_TEMPLATE = """\
Company: {company_name}
Document type: {document_type}
Document date: {document_date}

{clean_text}

Extract all claims. Return JSON object with a "claims" key only, no commentary."""

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

CHUNK_SIZE = 40_000      # ~10K tokens per chunk
OVERLAP    = 4_000       # ~1K token overlap


def _chunk_text(text: str) -> list[str]:
    """Split long text into overlapping chunks. Returns [text] if short enough."""
    if len(text) <= CHUNK_SIZE:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        if end < len(text):
            # Don't cut mid-sentence
            last_period = text[start:end].rfind(". ")
            if last_period > CHUNK_SIZE * 0.5:
                end = start + last_period + 2
        chunks.append(text[start:end])
        start = end - OVERLAP
    return chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_cost(prompt_tokens: int, completion_tokens: int, model_tag: str) -> float:
    if model_tag.startswith("claude/"):
        in_cost, out_cost = CLAUDE_INPUT_COST_PER_M, CLAUDE_OUTPUT_COST_PER_M
    else:
        in_cost, out_cost = GROQ_INPUT_COST_PER_M, GROQ_OUTPUT_COST_PER_M
    return (
        (prompt_tokens / 1_000_000) * in_cost
        + (completion_tokens / 1_000_000) * out_cost
    )


def _fix_unquoted_strings(text: str) -> str:
    """Fix Llama's common JSON error: unquoted string values after keys."""
    lines = text.split("\n")
    fixed_lines = []
    for line in lines:
        m = re.match(r'^(\s*"[\w]+":\s*)(.*?)(\s*,?\s*)$', line)
        if m:
            key_part, value, trailing = m.group(1), m.group(2), m.group(3)
            stripped = value.strip()
            if stripped and not (
                stripped.startswith('"')
                or stripped.startswith("{")
                or stripped.startswith("[")
                or stripped in ("true", "false", "null")
                or re.match(r'^-?\d+\.?\d*$', stripped)
            ):
                escaped = stripped.replace("\\", "\\\\").replace('"', '\\"')
                line = f'{key_part}"{escaped}"{trailing}'
        fixed_lines.append(line)
    return "\n".join(fixed_lines)


def _extract_json(text: str) -> list | dict | None:
    """Try to extract valid JSON from LLM response, tolerating common issues."""
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fixed = _fix_unquoted_strings(text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    for start_char, end_char in [("{", "}"), ("[", "]")]:
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end > start:
            candidate = text[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                fixed_candidate = _fix_unquoted_strings(candidate)
                try:
                    return json.loads(fixed_candidate)
                except json.JSONDecodeError:
                    continue

    # Truncated response — try to salvage complete claim objects
    arr_start = text.find("[")
    if arr_start != -1:
        last_brace = text.rfind("}")
        if last_brace > arr_start:
            candidate = text[arr_start : last_brace + 1] + "]"
            candidate = _fix_unquoted_strings(candidate)
            try:
                return {"claims": json.loads(candidate)}
            except json.JSONDecodeError:
                pass

    return None


def _parse_claims(raw_content: str) -> list[dict]:
    """Parse LLM response into a list of claim dicts."""
    parsed = _extract_json(raw_content)
    if parsed is None:
        raise ValueError(f"Could not parse JSON from response: {raw_content[:200]}")

    if isinstance(parsed, list):
        return parsed
    elif isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
        return [parsed]
    return []


def _parse_one_claim(raw: dict) -> dict:
    """Parse one claim dict, stripping malformed structured fields.

    Tolerates Groq/Llama producing invalid types for structured fields —
    only keeps string values (and list for partners).
    """
    claim = {
        "claim_type":       raw.get("claim_type", "unknown"),
        "claim_text":       raw.get("claim_text", ""),
        "confidence":       raw.get("confidence", 0.5),
        "commitment_level": raw.get("commitment_level", "indicative"),
        "time_sensitivity": raw.get("time_sensitivity", "current"),
        "structured":       {},
    }
    # Clamp confidence to valid range
    try:
        claim["confidence"] = max(0.0, min(1.0, float(claim["confidence"])))
    except (ValueError, TypeError):
        claim["confidence"] = 0.5

    s = raw.get("structured")
    if isinstance(s, dict):
        valid = {}
        for k, v in s.items():
            if k == "partners":
                if isinstance(v, list):
                    valid[k] = v
            elif isinstance(v, str) and v.strip():
                valid[k] = v.strip()
        claim["structured"] = valid
    return claim


# ---------------------------------------------------------------------------
# LLM Calls
# ---------------------------------------------------------------------------

def _call_claude_raw(client: anthropic.Anthropic, system_prompt: str,
                     user_message: str) -> tuple[str | None, int, int, str]:
    """Call Claude API. Returns (raw_text, input_tokens, output_tokens, model_tag)."""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            temperature=0.1,
            max_tokens=4096,
        )
        return (
            response.content[0].text,
            response.usage.input_tokens,
            response.usage.output_tokens,
            f"claude/{CLAUDE_MODEL}",
        )
    except Exception as e:
        err = str(e)
        if "429" in err or "rate" in err.lower() or "overloaded" in err.lower():
            print(f"\n    Claude rate limited, falling back to Groq")
        else:
            print(f"\n    Claude error: {err[:80]}, falling back to Groq")
        return None, 0, 0, ""


def _call_groq_raw(client: Groq, system_prompt: str,
                    user_message: str) -> tuple[str | None, int, int, str]:
    """Call Groq API. Returns (raw_text, input_tokens, output_tokens, model_tag)."""
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.1,
            max_tokens=4096,
        )
        return (
            response.choices[0].message.content,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            f"groq/{GROQ_MODEL}",
        )
    except Exception as e:
        err = str(e)
        print(f"\n    Groq error: {err[:80]}")
        return None, 0, 0, ""


def _call_llm_raw(claude_client, groq_client, system_prompt: str,
                   user_message: str) -> tuple[str | None, int, int, str]:
    """Try Claude first, fall back to Groq. Returns raw text (not parsed)."""
    for attempt in range(1, GROQ_MAX_RETRIES + 1):
        if claude_client:
            raw, pt, ct, tag = _call_claude_raw(claude_client, system_prompt, user_message)
            if raw is not None:
                return raw, pt, ct, tag

        if groq_client:
            raw, pt, ct, tag = _call_groq_raw(groq_client, system_prompt, user_message)
            if raw is not None:
                return raw, pt, ct, tag
            # Rate limit retry for Groq
            wait = 2 ** attempt
            print(f"\n    Groq failed, waiting {wait}s (attempt {attempt}/{GROQ_MAX_RETRIES})")
            time.sleep(wait)

    return None, 0, 0, ""


# ---------------------------------------------------------------------------
# Shared claim processing (Change 8a)
# ---------------------------------------------------------------------------

def _process_document(db, claude_client, groq_client,
                      sid: int, company: str, doc_type: str,
                      doc_date: str, doc_url: str, text: str,
                      system_prompt: str) -> tuple[int, int, int, float]:
    """Shared LLM extraction: call model, parse claims, insert/dedup, return stats.

    Used by both the main read pass and the targeted gap-fill pass.
    The only difference between passes is the system_prompt and the text content.

    Returns (claims_inserted, prompt_tokens, completion_tokens, cost).
    """
    user_msg = USER_TEMPLATE.format(
        company_name=company,
        document_type=doc_type,
        document_date=doc_date,
        clean_text=text,
    )

    # Call LLM with failover
    raw_content, p_tokens, c_tokens, model_tag = _call_llm_raw(
        claude_client, groq_client, system_prompt, user_msg
    )
    if raw_content is None:
        _log_pass(db, sid, company, doc_type, "FAILED", 0, 0, 0, 0.0, "all_models_failed")
        return 0, 0, 0, 0.0

    # Parse JSON response
    try:
        claims_raw = _parse_claims(raw_content)
    except ValueError:
        # Retry once on JSON failure
        raw_content2, p2, c2, tag2 = _call_llm_raw(
            claude_client, groq_client, system_prompt, user_msg
        )
        if raw_content2:
            p_tokens += p2
            c_tokens += c2
            model_tag = tag2
            try:
                claims_raw = _parse_claims(raw_content2)
            except ValueError:
                claims_raw = None
        else:
            claims_raw = None

        if not claims_raw:
            _log_pass(db, sid, company, doc_type, "FAILED", 0, p_tokens, c_tokens, 0.0, "json_parse_failed")
            return 0, p_tokens, c_tokens, 0.0

    claims = [_parse_one_claim(c) for c in claims_raw]

    # ── Hallucination filters ─────────────────────────────────────────────
    before = len(claims)

    # Filter 1: RSS feed / news sidebar noise
    _RSS_NOISE = re.compile(
        r'\bRSS\b|Today in Energy|FuelCellsWorks|Hydrogen Central|'
        r'Utility Dive|Power Engineering|Ammonia Energy|DOE News RSS|EIA\s+RSS',
        re.I,
    )
    claims = [c for c in claims
              if not _RSS_NOISE.search(c.get("claim_text", ""))
              and not _RSS_NOISE.search((c.get("structured") or {}).get("company", ""))]

    # Filter 2: Metadata echo — LLM parroting the prompt template fields
    _META_ECHO = re.compile(
        r'^(Document (date|type)|Company:)\s*', re.I,
    )
    claims = [c for c in claims if not _META_ECHO.match(c.get("claim_text", ""))]

    # Filter 3: Grounding check — claim text must overlap with source document
    # Use a 25-char sliding window; if no window matches, reject the claim
    text_lower = text.lower() if text else ""
    if len(text_lower) > 50:
        def _is_grounded(claim_text: str) -> bool:
            ct = (claim_text or "").strip()
            if len(ct) < 25:
                return True  # too short to check
            for i in range(0, min(len(ct) - 25, 300), 8):
                if ct[i:i+25].lower() in text_lower:
                    return True
            return False

        claims = [c for c in claims if _is_grounded(c.get("claim_text", ""))]

    filtered = before - len(claims)
    if filtered:
        print(f" [filtered {filtered} hallucinated claims]", end="")

    # Within-doc dedup set (Change 4)
    existing_claims = {
        (r[0], r[1][:100] if r[1] else "")
        for r in db.execute(
            "SELECT claim_type, claim_text FROM claims WHERE source_id=?",
            (sid,),
        )
    }

    now = now_iso()
    inserted = 0

    for claim in claims:
        key = (claim["claim_type"], claim["claim_text"][:100])
        s = claim.get("structured") or {}

        # 1. Within-doc dedup (Change 4)
        if key in existing_claims:
            if any(s.values()):
                db.execute("""
                    UPDATE claims SET
                        extracted_company = COALESCE(extracted_company, ?),
                        extracted_project = COALESCE(extracted_project, ?),
                        extracted_city = COALESCE(extracted_city, ?),
                        extracted_state = COALESCE(extracted_state, ?),
                        extracted_county = COALESCE(extracted_county, ?),
                        extracted_capacity = COALESCE(extracted_capacity, ?),
                        extracted_technology = COALESCE(extracted_technology, ?),
                        extracted_stage = COALESCE(extracted_stage, ?),
                        extracted_date = COALESCE(extracted_date, ?),
                        extracted_date_type = COALESCE(extracted_date_type, ?),
                        extracted_permit_id = COALESCE(extracted_permit_id, ?),
                        extracted_parent_company = COALESCE(extracted_parent_company, ?),
                        extracted_partners = COALESCE(extracted_partners, ?),
                        extracted_funding = COALESCE(extracted_funding, ?)
                    WHERE source_id=? AND claim_type=? AND claim_text LIKE ?
                """, (
                    s.get("company"), s.get("project"), s.get("city"), s.get("state"),
                    s.get("county"), s.get("capacity"), s.get("technology"), s.get("stage"),
                    s.get("date"), s.get("date_type"), s.get("permit_id"),
                    s.get("parent_company"),
                    json.dumps(s["partners"]) if s.get("partners") else None,
                    s.get("funding"),
                    sid, claim["claim_type"], claim["claim_text"][:100] + "%",
                ))
            continue

        # 2. Cross-document corroboration (Change 11b)
        if s.get("company"):
            existing_cross = db.execute("""
                SELECT claim_id, source_id, corroboration_count,
                       corroborating_sources
                FROM claims
                WHERE claim_type = ?
                  AND extracted_company = ?
                  AND claim_text LIKE ?
                  AND source_id != ?
                LIMIT 1
            """, (
                claim["claim_type"],
                s["company"],
                claim["claim_text"][:80] + "%",
                sid,
            )).fetchone()

            if existing_cross:
                sources = json.loads(existing_cross["corroborating_sources"] or "[]")
                if sid not in sources:
                    sources.append(sid)
                    db.execute("""
                        UPDATE claims
                        SET corroboration_count = ?,
                            corroborating_sources = ?
                        WHERE claim_id = ?
                    """, (
                        len(sources) + 1,
                        json.dumps(sources),
                        existing_cross["claim_id"],
                    ))
                continue

        # 3. Insert new claim with structured fields
        claim_id = str(uuid.uuid4())
        db.execute("""
            INSERT INTO claims
            (claim_id, source_id, company_name, document_type, document_date,
             document_url, claim_type, claim_text, confidence,
             commitment_level, time_sensitivity, extraction_model, extracted_at,
             extracted_company, extracted_project,
             extracted_city, extracted_state, extracted_county,
             extracted_capacity, extracted_technology, extracted_stage,
             extracted_date, extracted_date_type, extracted_permit_id,
             extracted_parent_company, extracted_partners, extracted_funding,
             source_keyword)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            claim_id, sid, company, doc_type, doc_date, doc_url,
            claim["claim_type"], claim["claim_text"], claim["confidence"],
            claim["commitment_level"], claim["time_sensitivity"],
            model_tag, now,
            s.get("company"), s.get("project"),
            s.get("city"), s.get("state"), s.get("county"),
            s.get("capacity"), s.get("technology"), s.get("stage"),
            s.get("date"), s.get("date_type"), s.get("permit_id"),
            s.get("parent_company"),
            json.dumps(s["partners"]) if s.get("partners") else None,
            s.get("funding"),
            s.get("source_keyword"),
        ))

        # Upsert scheme_frequency
        db.execute("""
            INSERT INTO scheme_frequency (document_type, claim_type, occurrence_count, first_seen, last_seen)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(document_type, claim_type) DO UPDATE SET
                occurrence_count = occurrence_count + 1,
                last_seen = excluded.last_seen
        """, (doc_type, claim["claim_type"], doc_date, doc_date))

        existing_claims.add(key)
        inserted += 1

    cost = estimate_cost(p_tokens, c_tokens, model_tag) if model_tag else 0.0
    db.commit()
    return inserted, p_tokens, c_tokens, cost


def _save_chunk(db, sid: int, chunk_idx: int, chunk_text: str):
    """Save document chunk for FTS search. UNIQUE(source_id, chunk_index) prevents dupes."""
    db.execute("""
        INSERT OR IGNORE INTO document_chunks
        (chunk_id, source_id, chunk_index, chunk_text, chunk_chars,
         overlap_prev, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (
        str(uuid.uuid4()), sid, chunk_idx, chunk_text,
        len(chunk_text), 1 if chunk_idx > 0 else 0, now_iso(),
    ))


def _log_pass(db, sid, company, doc_type, status, claims_n,
              p_tokens, c_tokens, cost, error_msg=None):
    """Write to read_pass_log."""
    db.execute("""
        INSERT OR REPLACE INTO read_pass_log
        (source_id, company_name, document_type, status, claims_extracted,
         prompt_tokens, completion_tokens, cost_usd, error_message, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (sid, company, doc_type, status, claims_n,
          p_tokens, c_tokens, cost, error_msg, now_iso()))
    db.commit()


# ---------------------------------------------------------------------------
# FTS search functions (Change 10c)
# ---------------------------------------------------------------------------

def search_claims(db, query: str, limit: int = 20) -> list:
    """Full-text search across claims."""
    return db.execute("""
        SELECT c.claim_id, c.company_name, c.claim_type, c.claim_text,
               c.confidence, c.document_type, c.document_date
        FROM claims_fts fts
        JOIN claims c ON c.rowid = fts.rowid
        WHERE claims_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit)).fetchall()


def search_chunks(db, query: str, limit: int = 10) -> list:
    """Full-text search across document chunks."""
    return db.execute("""
        SELECT dc.source_id, dc.chunk_index, dc.chunk_text,
               cd.company_name, cd.document_type
        FROM chunks_fts fts
        JOIN document_chunks dc ON dc.rowid = fts.rowid
        JOIN cleaned_documents cd ON cd.source_id = dc.source_id
        WHERE chunks_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit)).fetchall()


# ---------------------------------------------------------------------------
# Main read pass (Changes 8b + 9c)
# ---------------------------------------------------------------------------

def run(resume_from: int | None = None):
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

    if not claude_client and not groq_client:
        print("ERROR: No API keys found. Set ANTHROPIC_API_KEY or GROQ_API_KEY.")
        return

    primary = "Claude" if claude_client else "Groq"
    fallback = "Groq" if claude_client and groq_client else "none"
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    # Get documents to process, ordered cheapest first
    type_order = "CASE document_type WHEN 'award' THEN 1 WHEN 'facility' THEN 2 ELSE 3 END"
    docs = db.execute(f"""
        SELECT source_id, company_name, document_type, document_date,
               document_url, clean_text, clean_chars, quality_flag
        FROM cleaned_documents
        WHERE quality_flag IN ('CLEAN', 'REFETCHED', 'SHORT', 'TRUNCATED')
        ORDER BY {type_order}, source_id
    """).fetchall()

    # Check what's already processed (for resume)
    already_done = set()
    if resume_from is None:
        for r in db.execute("SELECT source_id FROM read_pass_log WHERE status='SUCCESS'"):
            already_done.add(r[0])

    total_claims = 0
    total_cost = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    processed = 0
    skipped = 0
    failed = 0

    print(f"STEP 3 — READ PASS")
    print(f"  Documents to process: {len(docs)}")
    print(f"  Already done: {len(already_done)}")
    print(f"  Primary: {primary}  Fallback: {fallback}")
    print("=" * 60)

    for doc in docs:
        sid = doc["source_id"]

        if sid in already_done:
            continue

        if resume_from is not None and sid < resume_from:
            continue

        company = doc["company_name"]
        doc_type = doc["document_type"]
        doc_date = doc["document_date"] or "unknown"
        doc_url = doc["document_url"]
        clean_text = doc["clean_text"]

        if not clean_text or len(clean_text.strip()) < 20:
            _log_pass(db, sid, company, doc_type, "SKIPPED", 0, 0, 0, 0.0, "Text too short")
            skipped += 1
            continue

        # Skip binary/corrupted documents (raw PDF bytes stored as text)
        if clean_text[:20].startswith('%PDF') or '\x00' in clean_text[:500]:
            _log_pass(db, sid, company, doc_type, "SUCCESS", 0, 0, 0, 0.0,
                      "Skipped: binary/PDF content, not extractable")
            skipped += 1
            continue

        try:
            print(f"[{sid:3d}] {company[:30]:30s}  {doc_type:12s}  {len(clean_text):6d} chars...", end="", flush=True)

            # Chunk long documents (Change 9c)
            chunks = _chunk_text(clean_text)
            doc_claims = 0
            doc_cost = 0.0
            doc_p_tokens = 0
            doc_c_tokens = 0

            for chunk_idx, chunk in enumerate(chunks):
                # Save chunk for FTS (every doc gets at least one row)
                _save_chunk(db, sid, chunk_idx, chunk)

                # Process via shared function
                inserted, pt, ct, cost = _process_document(
                    db, claude_client, groq_client,
                    sid, company, doc_type, doc_date, doc_url,
                    chunk, SYSTEM_PROMPT,
                )
                doc_claims += inserted
                doc_cost += cost
                doc_p_tokens += pt
                doc_c_tokens += ct

                if len(chunks) > 1:
                    time.sleep(GROQ_SLEEP_BETWEEN_CALLS)

            # Log success for this document
            _log_pass(db, sid, company, doc_type, "SUCCESS",
                      doc_claims, doc_p_tokens, doc_c_tokens, doc_cost)

            total_claims += doc_claims
            total_cost += doc_cost
            total_prompt_tokens += doc_p_tokens
            total_completion_tokens += doc_c_tokens
            processed += 1

            chunk_note = f" ({len(chunks)} chunks)" if len(chunks) > 1 else ""
            print(f"  {doc_claims:2d} claims{chunk_note}  ${doc_cost:.4f}")
            time.sleep(GROQ_SLEEP_BETWEEN_CALLS)

        except Exception as e:
            error_msg = str(e)[:500]
            _log_pass(db, sid, company, doc_type, "FAILED", 0, 0, 0, 0.0, error_msg)
            failed += 1
            print(f"  FAILED: {error_msg[:80]}")

    db.close()

    print("\n" + "=" * 60)
    print("STEP 3 — READ PASS COMPLETE")
    print("=" * 60)
    print(f"  Processed:     {processed}")
    print(f"  Skipped:       {skipped}")
    print(f"  Failed:        {failed}")
    print(f"  Total claims:  {total_claims}")
    print(f"  Prompt tokens: {total_prompt_tokens:,}")
    print(f"  Compl. tokens: {total_completion_tokens:,}")
    print(f"  Total cost:    ${total_cost:.4f}")


# ---------------------------------------------------------------------------
# Gap-driven re-read (Change 8c)
# ---------------------------------------------------------------------------

def run_targeted():
    """Re-read documents for projects missing critical fields."""
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

    if not claude_client and not groq_client:
        print("ERROR: No API keys found.")
        return

    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    gaps = db.execute("""
        SELECT up.project_id, up.developer_name,
               CASE WHEN up.technology IS NULL OR up.technology='' THEN 1 ELSE 0 END as need_tech,
               CASE WHEN up.stage='unknown' THEN 1 ELSE 0 END as need_stage,
               CASE WHEN up.capacity_raw IS NULL THEN 1 ELSE 0 END as need_capacity,
               CASE WHEN up.city IS NULL THEN 1 ELSE 0 END as need_city
        FROM unified_projects up
        WHERE up.quarantined=0
          AND (up.technology IS NULL OR up.technology=''
               OR up.stage='unknown'
               OR up.capacity_raw IS NULL
               OR up.city IS NULL)
    """).fetchall()

    print(f"STEP 3 — GAP-DRIVEN RE-READ")
    print(f"  Projects with gaps: {len(gaps)}")
    print("=" * 60)

    total_claims = 0
    total_docs = 0

    for proj in gaps:
        missing = []
        if proj["need_tech"]:     missing.append("technology/production method")
        if proj["need_stage"]:    missing.append("project stage/status")
        if proj["need_capacity"]: missing.append("production or storage capacity")
        if proj["need_city"]:     missing.append("city or specific location")

        source_ids = db.execute("""
            SELECT DISTINCT c.source_id
            FROM project_claims pc
            JOIN claims c ON c.claim_id = pc.claim_id
            WHERE pc.project_id=? AND pc.resolution_method != 'unresolved'
        """, (proj["project_id"],)).fetchall()

        for row in source_ids:
            sid = row[0]
            doc = db.execute(
                "SELECT * FROM cleaned_documents WHERE source_id=?", (sid,)
            ).fetchone()
            if not doc or not doc["clean_text"]:
                continue

            # Prepend gap context to the document text
            gap_header = (
                f"MISSING FIELDS for {proj['developer_name']}: "
                f"{', '.join(missing)}.\n\n"
            )
            text = gap_header + doc["clean_text"][:100000]

            print(f"  [{sid:3d}] {proj['developer_name'][:30]:30s}  missing: {', '.join(missing)[:40]}...", end="", flush=True)

            inserted, _, _, cost = _process_document(
                db, claude_client, groq_client,
                sid, doc["company_name"], doc["document_type"],
                doc["document_date"], doc["document_url"],
                text, TARGETED_SYSTEM_PROMPT,
            )

            total_claims += inserted
            total_docs += 1
            print(f"  {inserted:2d} new claims  ${cost:.4f}")
            time.sleep(GROQ_SLEEP_BETWEEN_CALLS)

    db.close()

    print("\n" + "=" * 60)
    print("STEP 3 — GAP RE-READ COMPLETE")
    print("=" * 60)
    print(f"  Documents re-read: {total_docs}")
    print(f"  New claims:        {total_claims}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if "--fill-gaps" in sys.argv:
        run_targeted()
    else:
        resume = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
        run(resume_from=resume)

    # Auto-run hallucination audit after extraction
    print("\n" + "=" * 60)
    print("Running post-extraction hallucination audit...")
    print("=" * 60)
    from audit_claims import audit
    result = audit(fix=True)
    total = sum(result.values())
    if total:
        print(f"  Cleaned {total} hallucinated claims.")
    else:
        print(f"  All claims clean.")
