#!/usr/bin/env python3
"""
normalize_claim_types.py — Consolidate 1,100+ LLM-generated claim types into ~30 canonical categories.

Sends all unique claim_type values to an LLM for mapping, then applies the mapping
by updating claims.claim_type in-place and storing the original in a mapping table.

Usage:
    python normalize_claim_types.py              # full run: classify + apply
    python normalize_claim_types.py --dry-run    # classify only, don't apply
    python normalize_claim_types.py --stats      # show current mapping stats
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import GROQ_API_KEY, GROQ_MODEL

from groq import Groq

DRY_RUN    = '--dry-run' in sys.argv
STATS_ONLY = '--stats'   in sys.argv
BATCH_SIZE = 80
GROQ_SLEEP = 1.0

# ── Canonical taxonomy ──────────────────────────────────────────────────────
CANONICAL_TYPES = [
    "epa_facility_id",       # EPA/FRS registry IDs, facility identification
    "epa_permit_id",         # All permit IDs: CAA, CWA, RCRA, TRI, TSCA, CEDRI, EIS, RMP, GHGRP, ICIS, NPDES
    "epa_compliance",        # Compliance status, non-compliance, penalties, inspections
    "epa_naics_sic",         # NAICS codes, SIC codes
    "location",              # City, state, address, facility location, site
    "technology",            # Hydrogen production method, CCS, ammonia, electrolysis, SMR, ATR, fuel type
    "capacity",              # Production volume, MW, MTPA, tons/day, plant capacity
    "stage",                 # Construction status, operational, permitted, FID, COD, cancelled
    "timeline",              # Target dates, construction start, completion year, COD date
    "partnership",           # JV, offtake agreement, co-developer, acquisition, merger
    "epc_contractor",        # EPC contract, FEED contract, engineering firm, construction contractor
    "financing",             # Investment amount, loan, funding round, project cost, bond offering
    "research_award",        # DOE award, grant, cooperative agreement, research project, funding
    "project_description",   # Project name, objective, scope, focus area, description
    "corporate_governance",  # Board changes, officer appointments, director elections, compensation
    "legal_financial",       # Tax agreements, credit facilities, warrants, registration, SEC filings, debt
    "regulatory",            # Environmental compliance, permits (non-EPA), regulatory filings
    "news_industry",         # Industry trends, market data, RSS feed content, general news
    "noise",                 # No useful content, boilerplate, irrelevant
]

PROMPT_TEMPLATE = """Map each claim_type to exactly ONE canonical category from this list:

{categories}

Rules:
- Any EPA/FRS ID → epa_facility_id
- Any permit ID (CAA, CWA, RCRA, TRI, TSCA, CEDRI, EIS, RMP, GHGRP, ICIS, NPDES) → epa_permit_id
- Compliance/non-compliance/penalties → epa_compliance
- NAICS/SIC codes → epa_naics_sic
- City/state/address/facility location → location
- Hydrogen/CCS/ammonia/electrolysis/fuel type → technology
- Production volume/MW/MTPA → capacity
- Construction/operational/FID/COD → stage
- Dates/timeline/schedule → timeline
- JV/offtake/acquisition/merger → partnership
- EPC/FEED/construction contract → epc_contractor
- Loan/bond/investment/funding amount → financing
- DOE award/grant/cooperative agreement → research_award
- Project name/objective/scope → project_description
- Board/officer/director/compensation → corporate_governance
- Tax/credit/warrant/SEC filing/debt terms → legal_financial
- Environmental/regulatory → regulatory
- Industry trends/market/RSS → news_industry
- Boilerplate/irrelevant → noise

Return ONLY a JSON object mapping each input type to its canonical type. No commentary.

CLAIM TYPES TO MAP:
{types_list}
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_mapping_table(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS claim_type_mapping (
            original_type TEXT PRIMARY KEY,
            canonical_type TEXT NOT NULL,
            claim_count INTEGER,
            mapped_at TEXT
        )
    """)
    db.commit()


def run_mapping(db: sqlite3.Connection, client: Groq) -> dict[str, str]:
    """Send all unique types to LLM for mapping."""
    types = db.execute(
        "SELECT claim_type, COUNT(*) as cnt FROM claims GROUP BY claim_type ORDER BY cnt DESC"
    ).fetchall()

    all_mappings = {}
    total_batches = (len(types) + BATCH_SIZE - 1) // BATCH_SIZE

    categories_str = "\n".join(f"  - {c}" for c in CANONICAL_TYPES)

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        batch = types[start:start + BATCH_SIZE]

        types_list = "\n".join(f"  {r[0]} (×{r[1]})" for r in batch)

        prompt = PROMPT_TEMPLATE.format(
            categories=categories_str,
            types_list=types_list
        )

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4000,
                    temperature=0.0,
                )
                raw = response.choices[0].message.content
                # Extract JSON object
                js_start = raw.find('{')
                js_end = raw.rfind('}') + 1
                if js_start < 0 or js_end <= js_start:
                    raise ValueError("No JSON object in response")
                mapping = json.loads(raw[js_start:js_end])

                # Validate and add
                for r in batch:
                    original = r[0]
                    canonical = mapping.get(original, 'noise')
                    if canonical not in CANONICAL_TYPES:
                        canonical = 'noise'
                    all_mappings[original] = canonical

                break
            except Exception as e:
                print(f"  [error] Batch {batch_idx+1}, attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                else:
                    for r in batch:
                        all_mappings[r[0]] = 'noise'

        if (batch_idx + 1) % 5 == 0 or batch_idx == total_batches - 1:
            print(f"  Batch {batch_idx+1}/{total_batches} done ({len(all_mappings)}/{len(types)} types mapped)")

        time.sleep(GROQ_SLEEP)

    return all_mappings


def save_mapping(db: sqlite3.Connection, mappings: dict[str, str]):
    """Save mapping to table."""
    now = now_iso()
    type_counts = dict(db.execute(
        "SELECT claim_type, COUNT(*) FROM claims GROUP BY claim_type"
    ).fetchall())

    for original, canonical in mappings.items():
        db.execute("""
            INSERT OR REPLACE INTO claim_type_mapping
            (original_type, canonical_type, claim_count, mapped_at)
            VALUES (?, ?, ?, ?)
        """, (original, canonical, type_counts.get(original, 0), now))
    db.commit()
    print(f"  Saved {len(mappings)} mappings to claim_type_mapping table")


def apply_mapping(db: sqlite3.Connection):
    """Apply canonical types to claims table. Store original in mapping table."""
    mappings = dict(db.execute(
        "SELECT original_type, canonical_type FROM claim_type_mapping"
    ).fetchall())

    if not mappings:
        print("No mappings found. Run without --stats first.")
        return

    # Add original_claim_type column if not exists
    try:
        db.execute("ALTER TABLE claims ADD COLUMN original_claim_type TEXT")
        print("  Added original_claim_type column")
    except sqlite3.OperationalError:
        pass

    # Backup originals and apply
    updated = 0
    for original, canonical in mappings.items():
        if original == canonical:
            continue
        n = db.execute("""
            UPDATE claims
            SET original_claim_type = COALESCE(original_claim_type, claim_type),
                claim_type = ?
            WHERE claim_type = ?
        """, (canonical, original)).rowcount
        updated += n

    db.commit()
    print(f"  Updated {updated} claims with canonical types")

    # Show result
    new_types = db.execute("SELECT COUNT(DISTINCT claim_type) FROM claims").fetchone()[0]
    print(f"  Unique claim types now: {new_types}")


def print_stats(db: sqlite3.Connection):
    """Show mapping and current type distribution."""
    # Check if mapping exists
    has_mapping = db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='claim_type_mapping'"
    ).fetchone()[0]

    if has_mapping:
        total_mapped = db.execute("SELECT COUNT(*) FROM claim_type_mapping").fetchone()[0]
        print(f"\nMapping table: {total_mapped} entries")
        print(f"\nMapping distribution:")
        rows = db.execute("""
            SELECT canonical_type, COUNT(*) as types, SUM(claim_count) as claims
            FROM claim_type_mapping
            GROUP BY canonical_type
            ORDER BY claims DESC
        """).fetchall()
        print(f"  {'Canonical Type':25s} {'Types':>6} {'Claims':>7}")
        print(f"  {'─'*45}")
        for r in rows:
            print(f"  {r[0]:25s} {r[1]:>6} {r[2]:>7}")

    # Current state
    print(f"\nCurrent claims table:")
    total = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    types = db.execute("SELECT COUNT(DISTINCT claim_type) FROM claims").fetchone()[0]
    print(f"  {total} claims, {types} unique types")

    # Has original_claim_type?
    has_orig = False
    try:
        db.execute("SELECT original_claim_type FROM claims LIMIT 1")
        has_orig = True
    except:
        pass

    if has_orig:
        orig_types = db.execute(
            "SELECT COUNT(DISTINCT original_claim_type) FROM claims WHERE original_claim_type IS NOT NULL"
        ).fetchone()[0]
        print(f"  {orig_types} original types preserved in original_claim_type")

    print(f"\nCurrent type distribution:")
    rows = db.execute("""
        SELECT claim_type, COUNT(*) as cnt
        FROM claims
        GROUP BY claim_type
        ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        pct = 100 * r[1] / total
        bar = '█' * int(pct / 2)
        print(f"  {r[1]:5d} ({pct:5.1f}%) {r[0]:25s} {bar}")


def run():
    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row
    create_mapping_table(db)

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    # Check if already mapped
    already = db.execute("SELECT COUNT(*) FROM claim_type_mapping").fetchone()[0]
    if already > 0:
        print(f"Already have {already} mappings.")
        if not DRY_RUN:
            print("Applying mapping to claims...")
            apply_mapping(db)
            print_stats(db)
        else:
            print_stats(db)
        db.close()
        return

    print(f"{'DRY RUN' if DRY_RUN else 'FULL RUN'}")
    print(f"Mapping {db.execute('SELECT COUNT(DISTINCT claim_type) FROM claims').fetchone()[0]} types to {len(CANONICAL_TYPES)} canonical categories\n")

    client = Groq(api_key=GROQ_API_KEY)

    print("── Phase 1: LLM type mapping ──")
    mappings = run_mapping(db, client)
    save_mapping(db, mappings)

    if not DRY_RUN:
        print("\n── Phase 2: Apply mapping ──")
        apply_mapping(db)

    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
