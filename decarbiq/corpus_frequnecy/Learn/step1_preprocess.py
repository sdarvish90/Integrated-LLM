"""
DecarbIQ Learning Layer — Step 1: Pre-Processing
Clean boilerplate, refetch truncated documents, qualify text.
No LLM cost. Reads and writes core_database.db (regulatory_evidence → cleaned_documents).
"""

import re
import sqlite3
import time
from datetime import datetime, timezone

import requests

from config import (
    LEARNING_DB,
    REFETCHED_DIR,
    TRUNCATION_CEILING,
    MIN_CONTENT_CHARS,
)

# ── Boilerplate stripping ────────────────────────────────────────────────────
# SEC filings begin with "---" followed by forward-looking disclaimer language.
# Independent reimplementation — no import from production code.

_SEC_HEADER_RE = re.compile(
    r"^---\s*\n",
    re.MULTILINE,
)


def strip_boilerplate(text: str) -> str:
    """Remove leading '---' header from SEC filings."""
    if not text:
        return text
    m = _SEC_HEADER_RE.match(text)
    if m:
        # Remove the "---\n" prefix and return the rest
        text = text[m.end():]
    return text.strip()


# ── IDs from the plan ────────────────────────────────────────────────────────
BOILERPLATE_IDS = {
    3, 35, 9, 1, 6, 29, 2, 4, 5, 32,
    8, 10, 17, 25, 26, 27, 31, 46, 47,
    13, 14, 15, 16, 18, 19,
}

TRUNCATED_IDS = {
    3, 35, 9, 1, 6, 29, 20, 43, 32,
    8, 10, 17, 22, 28, 31, 46, 47,
    12, 13, 14, 15, 16, 18, 19,
}


# ── Content validation gate ──────────────────────────────────────────────────
# Detects documents that will cause LLM hallucination BEFORE they enter the
# extraction pipeline.  Returns (possibly cleaned text, updated quality_flag).

# Navigation / boilerplate phrases found in web page scrapes
_NAV_PHRASES = [
    "skip to main content", "toggle navigation", "search form",
    "breadcrumb", "cookie policy", "privacy policy", "terms of use",
    "sign in", "sign up", "subscribe", "newsletter", "rss feed",
    "follow us on", "share this", "back to top",
    # Government / agency site nav patterns
    "about us", "contact us", "careers", "faqs",
    "doing business with us", "audience pages",
]

def _validate_content(text: str, flag: str, rid: int, company: str) -> tuple[str, str]:
    """Validate cleaned text quality. Returns (text, flag).

    Possible new flags:
      BINARY   – raw PDF / binary data, not extractable
      NAV_ONLY – mostly web navigation boilerplate, no useful content
    """
    if not text:
        return text, flag

    # 1. Binary / raw PDF detection
    if text[:20].startswith('%PDF') or '\x00' in text[:1000]:
        print(f"[{rid}] BINARY: {company} — raw PDF/binary data ({len(text)} chars)")
        return text, "BINARY"

    # 2. Navigation-heavy web page detection
    #    Count how many nav phrases appear relative to total content
    text_lower = text.lower()
    nav_hits = sum(1 for phrase in _NAV_PHRASES if phrase in text_lower)

    #    Also count short lines (< 30 chars) — navigation menus have many
    lines = text.split('\n')
    non_empty_lines = [l for l in lines if l.strip()]
    short_lines = [l for l in non_empty_lines if len(l.strip()) < 30]
    short_ratio = len(short_lines) / max(len(non_empty_lines), 1)

    if nav_hits >= 3 and short_ratio > 0.5:
        # Try to salvage: extract only lines that look like content
        # (lines > 40 chars that aren't nav phrases)
        content_lines = [
            l for l in non_empty_lines
            if len(l.strip()) > 40
            and not any(p in l.lower() for p in _NAV_PHRASES)
        ]
        if content_lines:
            salvaged = '\n'.join(content_lines)
            print(f"[{rid}] NAV_HEAVY: {company} — salvaged {len(salvaged)} of {len(text)} chars")
            return salvaged, flag  # keep original flag, text is now cleaned
        else:
            print(f"[{rid}] NAV_ONLY: {company} — no useful content ({nav_hits} nav phrases, {short_ratio:.0%} short lines)")
            return text, "NAV_ONLY"

    return text, flag


# ── SEC full-text fetcher ────────────────────────────────────────────────────

def fetch_full_text(url: str) -> str | None:
    """Fetch a SEC filing URL and return full cleaned text.

    No character limit — full sentences are always preserved.
    """
    if not url:
        return None
    try:
        headers = {
            "User-Agent": "DecarbIQ-Research admin@decarbiq.com",
            "Accept": "text/html, application/xhtml+xml, text/plain",
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        raw = resp.text

        # Strip HTML tags if present
        if "<html" in raw.lower() or "<body" in raw.lower():
            raw = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", raw, flags=re.IGNORECASE)
            # Remove nav/sidebar/header/footer to prevent RSS feed noise
            raw = re.sub(r"<nav[^>]*>[\s\S]*?</nav>", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<aside[^>]*>[\s\S]*?</aside>", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<header[^>]*>[\s\S]*?</header>", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<footer[^>]*>[\s\S]*?</footer>", "", raw, flags=re.IGNORECASE)
            raw = re.sub(r"<[^>]+>", " ", raw)
            raw = re.sub(r"&nbsp;", " ", raw)
            raw = re.sub(r"&amp;", "&", raw)
            raw = re.sub(r"&lt;", "<", raw)
            raw = re.sub(r"&gt;", ">", raw)
            raw = re.sub(r"&#\d+;", "", raw)

        # Collapse whitespace
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        raw = raw.strip()

        # Strip boilerplate header if present
        raw = strip_boilerplate(raw)

        return raw if len(raw) > 50 else None

    except Exception as e:
        print(f"  FETCH FAILED for {url}: {e}")
        return None


# ── Main processing ──────────────────────────────────────────────────────────

def run():
    REFETCHED_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure core_database.db has the cleaned_documents table
    dst = sqlite3.connect(str(LEARNING_DB))
    dst.execute("""
        CREATE TABLE IF NOT EXISTS cleaned_documents (
            source_id       INTEGER PRIMARY KEY,
            company_name    TEXT,
            document_type   TEXT,
            document_date   TEXT,
            document_url    TEXT,
            original_chars  INTEGER,
            clean_text      TEXT,
            clean_chars     INTEGER,
            quality_flag    TEXT,
            processed_at    TEXT,
            source_system   TEXT
        )
    """)
    dst.execute("DELETE FROM cleaned_documents")  # idempotent re-run
    dst.commit()

    # Read source
    src = sqlite3.connect(str(LEARNING_DB))
    src.row_factory = sqlite3.Row
    src.execute("PRAGMA query_only = ON")  # safety: read phase only
    rows = src.execute("""
        SELECT id, company_name, document_type, document_date, document_url,
               raw_text_excerpt, excerpt_char_count, source_system
        FROM regulatory_evidence
        ORDER BY id
    """).fetchall()
    src.close()

    stats = {"CLEAN": 0, "REFETCHED": 0, "SHORT": 0, "FAILED": 0, "TRUNCATED": 0,
             "BINARY": 0, "NAV_ONLY": 0}
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        rid = row["id"]
        text = row["raw_text_excerpt"] or ""
        char_count = row["excerpt_char_count"] or len(text)
        url = row["document_url"] or ""
        quality_flag = None
        clean_text = text

        # ── Task A: Boilerplate strip ──
        if rid in BOILERPLATE_IDS:
            clean_text = strip_boilerplate(clean_text)

        # ── Task B: Refetch truncated documents ──
        # API-sourced records (e.g. epa_echo) already contain complete structured
        # text built from the API response.  Their document_url points to a
        # JS-heavy page that yields only website boilerplate when scraped, so we
        # skip the refetch branch entirely for these sources.
        source_sys = row["source_system"] or ""
        skip_refetch = source_sys in (
            "epa_echo", "ercot", "puc_texas", "sec_grid",
            "eia_860m", "tsp_earnings", "interconnection_fyi",
            # Stage 1+2 collectors use synthetic URL schemes (tceq://, rrc_uic://, etc.)
            "tceq", "ercot_gis", "phmsa", "rrc_uic", "federal_register",
            "doe_oced", "doe_oced_detail", "epa_ghgrp", "tx_rrc", "epa_uic",
        )

        # Detect truncation: explicit ceiling, manual IDs, OR text ends mid-sentence
        # (no sentence-ending punctuation in last 20 chars → likely cut off).
        tail = clean_text.strip()[-20:] if clean_text.strip() else ""
        ends_mid_sentence = bool(tail) and not re.search(r'[.!?)\]"\']\s*$', tail)
        is_truncated = (
            (char_count >= TRUNCATION_CEILING)
            or (rid in TRUNCATED_IDS)
            or (ends_mid_sentence and len(clean_text) > MIN_CONTENT_CHARS)
        )

        if is_truncated and url and not skip_refetch:
            print(f"[{rid}] Refetching {row['company_name']} ({row['document_type']})...")
            fetched = fetch_full_text(url)
            if fetched and len(fetched) > char_count:
                # Save raw refetched text to disk
                safe_name = f"{rid}_{row['document_type'].replace('/', '_')}.txt"
                refetch_path = REFETCHED_DIR / safe_name
                refetch_path.write_text(fetched, encoding="utf-8")
                clean_text = fetched
                quality_flag = "REFETCHED"
                print(f"  → REFETCHED: {len(fetched)} chars (was {char_count})")
                time.sleep(0.3)  # polite to source servers
            elif fetched:
                # Fetched but not longer — use stripped original
                clean_text = strip_boilerplate(text) if rid in BOILERPLATE_IDS else text
                quality_flag = "CLEAN" if len(clean_text) >= MIN_CONTENT_CHARS else "SHORT"
                print(f"  → Refetch not longer, using original ({len(clean_text)} chars)")
            else:
                # Fetch failed or returned nothing — keep original as-is
                if ends_mid_sentence:
                    # Source is mid-sentence but can't be refetched — mark it
                    quality_flag = "TRUNCATED"
                    print(f"  → Source truncated mid-sentence, cannot refetch ({len(clean_text)} chars)")
                else:
                    quality_flag = "FAILED"
                    print(f"  → FAILED to refetch")
        elif is_truncated and not url:
            if ends_mid_sentence:
                quality_flag = "TRUNCATED"
                print(f"[{rid}] Truncated mid-sentence, no URL ({len(clean_text)} chars)")
            else:
                quality_flag = "FAILED"
                print(f"[{rid}] Truncated but no URL — FAILED")

        # ── Task C: Qualify text ──
        if quality_flag is None:
            clean_chars = len(clean_text)
            # EIA-860M: structured data records are short but valid
            if source_sys == "eia_860m" and clean_text.startswith("Plant:"):
                quality_flag = "CLEAN"
            # Structured collector excerpts are pre-formatted — always CLEAN
            elif source_sys in ('tceq', 'ercot_gis', 'phmsa', 'rrc_uic', 'federal_register'):
                quality_flag = "CLEAN"
            elif clean_chars < MIN_CONTENT_CHARS:
                quality_flag = "SHORT"
            else:
                quality_flag = "CLEAN"

        # ── Task D: Content validation gate ──
        # Detect documents that will cause LLM hallucination and flag them
        # so step3 can skip or handle them differently.
        clean_text, quality_flag = _validate_content(
            clean_text, quality_flag, rid, row["company_name"]
        )

        clean_chars = len(clean_text)
        stats[quality_flag] += 1

        dst.execute("""
            INSERT INTO cleaned_documents
            (source_id, company_name, document_type, document_date, document_url,
             original_chars, clean_text, clean_chars, quality_flag, processed_at,
             source_system)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rid, row["company_name"], row["document_type"],
            row["document_date"], url,
            char_count, clean_text, clean_chars,
            quality_flag, now, row["source_system"],
        ))

    dst.commit()
    dst.close()

    # ── Summary ──
    print("\n" + "=" * 60)
    print("STEP 1 — PRE-PROCESSING COMPLETE")
    print("=" * 60)
    total = sum(stats.values())
    for flag, count in sorted(stats.items()):
        print(f"  {flag:12s}  {count:4d}")
    print(f"  {'TOTAL':12s}  {total:4d}")

    # Acceptance criteria checks
    print("\n--- Acceptance Criteria ---")
    dst = sqlite3.connect(str(LEARNING_DB))

    award_fails = dst.execute(
        "SELECT COUNT(*) FROM cleaned_documents WHERE document_type='award' AND quality_flag='FAILED'"
    ).fetchone()[0]
    print(f"  DOE award failures:    {award_fails}  {'✓' if award_fails == 0 else '✗'}")

    facility_fails = dst.execute(
        "SELECT COUNT(*) FROM cleaned_documents WHERE document_type='facility' AND quality_flag='FAILED'"
    ).fetchone()[0]
    print(f"  EPA facility failures: {facility_fails}  {'✓' if facility_fails == 0 else '✗'}")

    refetched = dst.execute(
        "SELECT COUNT(*) FROM cleaned_documents WHERE quality_flag='REFETCHED'"
    ).fetchone()[0]
    refetched_ok = dst.execute(
        "SELECT COUNT(*) FROM cleaned_documents WHERE quality_flag='REFETCHED' AND clean_chars > 4000"
    ).fetchone()[0]
    print(f"  Refetched docs:        {refetched}  (>4000 chars: {refetched_ok})")

    total_fails = dst.execute(
        "SELECT COUNT(*) FROM cleaned_documents WHERE quality_flag='FAILED'"
    ).fetchone()[0]
    if total_fails > 0:
        print(f"\n  FAILED documents ({total_fails}):")
        for r in dst.execute(
            "SELECT source_id, company_name, document_type FROM cleaned_documents WHERE quality_flag='FAILED'"
        ):
            print(f"    id={r[0]}  {r[1]}  {r[2]}")

    dst.close()


if __name__ == "__main__":
    run()