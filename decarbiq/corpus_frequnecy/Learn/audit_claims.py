#!/usr/bin/env python3
"""
DecarbIQ — audit_claims.py
Post-pipeline hallucination audit. Runs against claims already in the database
and flags or deletes those that fail grounding checks.

Can be run standalone or called from the pipeline after step3.

Usage:
    python audit_claims.py              # audit + report (no changes)
    python audit_claims.py --fix        # audit + delete hallucinated claims
    python audit_claims.py --since 24h  # only check claims from last 24 hours
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import LEARNING_DB

# ── Hallucination patterns ──────────────────────────────────────────────────

# Pattern 1: RSS / news feed sidebar names in company_name or claim_text
_RSS_NOISE = re.compile(
    r'\bRSS\b|Today in Energy|FuelCellsWorks|Hydrogen Central|'
    r'Utility Dive|Power Engineering|Ammonia Energy|DOE News RSS|EIA\s+RSS|'
    r'decarbonfuse|H2\s*Bulletin',
    re.I,
)

# Pattern 2: Metadata echo — LLM parroting prompt template fields
_META_ECHO = re.compile(r'^(Document (date|type)|Company:)\s*', re.I)

# Pattern 3: Empty or near-empty claims
MIN_CLAIM_LEN = 10


# ── Grounding check ────────────────────────────────────────────────────────

def _is_grounded(claim_text: str, source_text: str) -> bool:
    """Check if claim text has meaningful overlap with source document.

    Uses a two-tier approach:
      1. Verbatim / sliding window match (strong grounding)
      2. Key data point overlap (catches acceptable paraphrases)
    """
    ct = (claim_text or "").strip()
    src = (source_text or "").lower()

    if len(ct) < 20 or len(src) < 50:
        return True  # too short to check reliably

    # Tier 1: Verbatim match (first 60 chars)
    if ct[:60].lower() in src:
        return True

    # Tier 2: Sliding window — any 25-char substring from claim in source?
    for i in range(0, min(len(ct) - 25, 300), 8):
        if ct[i:i+25].lower() in src:
            return True

    # Tier 3: Key data point overlap — numbers + proper nouns from claim in source
    # This catches acceptable paraphrases ("The award was made on 2024-11-01"
    # when source has "2024-11-01" in structured format)
    numbers = set(re.findall(r'\b\d[\d,.]+\b', ct))
    proper = set(w for w in re.findall(r'\b[A-Z][a-zA-Z]+\b', ct) if len(w) > 3)
    tokens = numbers | proper

    if not tokens:
        return False  # pure generic prose with no data points

    found = sum(1 for t in tokens if t.lower() in src)
    return found >= 2  # at least 2 data points from claim exist in source


def _company_mismatch(claim_company: str, source_company: str) -> bool:
    """Check if claim company name differs significantly from source company."""
    cc = (claim_company or "").strip().lower()
    sc = (source_company or "").strip().lower()
    if not cc or not sc:
        return False
    # Allow if one contains the other
    if cc in sc or sc in cc:
        return False
    return cc != sc


# ── Main audit ──────────────────────────────────────────────────────────────

def audit(db_path: Path = LEARNING_DB,
          fix: bool = False,
          since: str | None = None) -> dict:
    """Run hallucination audit on claims in the database.

    Args:
        db_path: Path to core_database.db
        fix: If True, delete flagged claims. If False, report only.
        since: Only check claims extracted after this ISO timestamp.

    Returns:
        dict with counts per category.
    """
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    where_clause = ""
    params: list = []
    if since:
        where_clause = "AND c.extracted_at >= ?"
        params.append(since)

    rows = db.execute(f"""
        SELECT c.claim_id, c.source_id, c.company_name AS claim_co,
               c.claim_text, c.confidence, c.extracted_at,
               re.company_name AS source_co, re.source_system,
               cd.clean_text
        FROM claims c
        JOIN regulatory_evidence re ON c.source_id = re.id
        JOIN cleaned_documents cd ON c.source_id = cd.source_id
        WHERE 1=1 {where_clause}
        ORDER BY c.source_id
    """, params).fetchall()

    flagged = {
        'rss_noise': [],
        'metadata_echo': [],
        'empty_claim': [],
        'company_mismatch': [],
        'ungrounded': [],
    }

    for r in rows:
        ct = (r['claim_text'] or "").strip()
        claim_id = r['claim_id']

        # Check 1: RSS noise
        if _RSS_NOISE.search(ct) or _RSS_NOISE.search(r['claim_co'] or ""):
            flagged['rss_noise'].append(claim_id)
            continue

        # Check 2: Metadata echo
        if _META_ECHO.match(ct):
            flagged['metadata_echo'].append(claim_id)
            continue

        # Check 3: Empty claims
        if len(ct) < MIN_CLAIM_LEN:
            flagged['empty_claim'].append(claim_id)
            continue

        # Check 4: Company name mismatch (strong fabrication signal)
        if _company_mismatch(r['claim_co'], r['source_co']):
            flagged['company_mismatch'].append(claim_id)
            continue

        # Check 5: Grounding — claim text not found in source document
        if not _is_grounded(ct, r['clean_text']):
            flagged['ungrounded'].append(claim_id)
            continue

    # ── Report ──────────────────────────────────────────────────────────────
    total_checked = len(rows)
    total_flagged = sum(len(v) for v in flagged.values())

    print(f"\n{'='*60}")
    print(f"HALLUCINATION AUDIT — {total_checked} claims checked")
    if since:
        print(f"  (since {since})")
    print(f"{'='*60}")

    for category, ids in flagged.items():
        status = f"  FAIL" if ids else f"  PASS"
        print(f"{status}  {category:25s}: {len(ids):5d}")

    print(f"\n  TOTAL FLAGGED: {total_flagged} of {total_checked} "
          f"({total_flagged/max(total_checked,1)*100:.2f}%)")

    if total_flagged == 0:
        print(f"\n  All claims grounded. No hallucination detected.")

    # ── Fix mode ────────────────────────────────────────────────────────────
    if fix and total_flagged > 0:
        all_flagged = []
        for ids in flagged.values():
            all_flagged.extend(ids)

        # Delete in batches to avoid huge IN clauses
        batch_size = 500
        deleted_pc = 0
        deleted_c = 0
        for i in range(0, len(all_flagged), batch_size):
            batch = all_flagged[i:i+batch_size]
            placeholders = ','.join('?' * len(batch))
            deleted_pc += db.execute(
                f"DELETE FROM project_claims WHERE claim_id IN ({placeholders})",
                batch,
            ).rowcount
            deleted_c += db.execute(
                f"DELETE FROM claims WHERE claim_id IN ({placeholders})",
                batch,
            ).rowcount

        db.commit()
        print(f"\n  FIXED: Deleted {deleted_c} claims + {deleted_pc} project_claims")
    elif fix:
        print(f"\n  Nothing to fix.")

    db.close()

    return {k: len(v) for k, v in flagged.items()}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _parse_since(since_str: str) -> str:
    """Parse relative time like '24h', '7d', '1h' into ISO timestamp."""
    m = re.match(r'^(\d+)([hd])$', since_str)
    if not m:
        return since_str  # assume ISO timestamp
    val, unit = int(m.group(1)), m.group(2)
    delta = timedelta(hours=val) if unit == 'h' else timedelta(days=val)
    return (datetime.now(timezone.utc) - delta).isoformat()


if __name__ == '__main__':
    fix = '--fix' in sys.argv
    since = None
    for i, arg in enumerate(sys.argv):
        if arg == '--since' and i + 1 < len(sys.argv):
            since = _parse_since(sys.argv[i + 1])

    result = audit(fix=fix, since=since)

    # Exit code: 0 if clean, 1 if hallucinations found
    total = sum(result.values())
    sys.exit(1 if total > 0 and not fix else 0)
