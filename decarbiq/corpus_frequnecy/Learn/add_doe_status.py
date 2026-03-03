#!/usr/bin/env python3
"""
add_doe_status.py — Targeted migration: DOE status fields + CIK
===============================================================
Pulls fields from blue_h2_intelligence.db regulatory_evidence that
were never processed through step 1-5 and adds them to core_database.db:

  New table: doe_status
    source_id         INTEGER  — links to cleaned_documents.source_id
    company_name      TEXT
    company_cik       TEXT     — SEC CIK (40/411 filled)
    document_url      TEXT
    document_type     TEXT
    document_date     TEXT
    state             TEXT     — facility/project state (360/411 filled)
    doe_award_status  TEXT     — active | terminated | completed
    doe_pct_disbursed REAL     — % of award funds paid out
    doe_termination_date TEXT  — when DOE cancelled the award
    migrated_at       TEXT

These fields carry intelligence the learning pipeline never extracted:
  - DOE termination = strong FID-negative signal for 174 projects
  - pct_disbursed = proxy for project health/progress
  - state = location data for 360 documents

Usage:
    python add_doe_status.py
    python add_doe_status.py --dry-run
    python add_doe_status.py --verify
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT    = Path(__file__).resolve().parent.parent          # corpus_frequnecy/
CORE_DB  = _ROOT / 'data' / 'core_database.db'
INTEL_DB = _ROOT / 'blue_h2_intelligence.db'

DRY_RUN = '--dry-run' in sys.argv
VERIFY  = '--verify'  in sys.argv


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def verify(core):
    print("\n=== VERIFICATION ===")
    try:
        total = core.execute("SELECT COUNT(*) FROM doe_status").fetchone()[0]
        with_cik = core.execute(
            "SELECT COUNT(*) FROM doe_status WHERE company_cik IS NOT NULL AND company_cik != ''"
        ).fetchone()[0]
        terminated = core.execute(
            "SELECT COUNT(*) FROM doe_status WHERE doe_award_status='terminated'"
        ).fetchone()[0]
        with_state = core.execute(
            "SELECT COUNT(*) FROM doe_status WHERE state IS NOT NULL AND state != ''"
        ).fetchone()[0]
        print(f"  doe_status rows:        {total}")
        print(f"  with CIK:               {with_cik}")
        print(f"  terminated DOE awards:  {terminated}")
        print(f"  with state:             {with_state}")

        # Spot-check: CMS Energy CIK
        r = core.execute(
            "SELECT company_cik FROM doe_status WHERE source_id=36"
        ).fetchone()
        if r and r[0]:
            print(f"  CIK spot-check id=36:  {r[0]} ✓  (expected 811156)")
        else:
            print(f"  CIK spot-check id=36:  not found ✗")

        # Sample terminated awards
        print("\n  Sample terminated DOE awards:")
        for r in core.execute("""
            SELECT company_name, doe_pct_disbursed, doe_termination_date, state
            FROM doe_status
            WHERE doe_award_status='terminated'
            ORDER BY doe_termination_date DESC LIMIT 5
        """):
            print(f"    {r[0][:35]:<35}  pct={r[1]}  "
                  f"terminated={r[2]}  state={r[3]}")
    except sqlite3.OperationalError as e:
        print(f"  ERROR: {e}")


def run():
    if not CORE_DB.exists():
        print(f"ERROR: {CORE_DB} not found"); sys.exit(1)
    if not INTEL_DB.exists():
        print(f"ERROR: {INTEL_DB} not found"); sys.exit(1)

    core  = sqlite3.connect(str(CORE_DB))
    intel = sqlite3.connect(str(INTEL_DB))
    core.row_factory = intel.row_factory = sqlite3.Row

    print(f"{'DRY RUN — no writes' if DRY_RUN else 'MIGRATING'}")
    print(f"  Source: {INTEL_DB}")
    print(f"  Target: {CORE_DB}")

    # ── Create doe_status table ──────────────────────────────────────────────
    if not DRY_RUN:
        core.execute("""
            CREATE TABLE IF NOT EXISTS doe_status (
                source_id            INTEGER PRIMARY KEY,
                company_name         TEXT,
                company_cik          TEXT,
                document_url         TEXT,
                document_type        TEXT,
                document_date        TEXT,
                state                TEXT,
                doe_award_status     TEXT,
                doe_pct_disbursed    REAL,
                doe_termination_date TEXT,
                migrated_at          TEXT
            )
        """)
        core.execute(
            "CREATE INDEX IF NOT EXISTS idx_ds_cik ON doe_status(company_cik)"
        )
        core.execute(
            "CREATE INDEX IF NOT EXISTS idx_ds_status ON doe_status(doe_award_status)"
        )
        core.commit()
        print("\n  Table doe_status created.")

    # ── Pull from regulatory_evidence ────────────────────────────────────────
    rows = intel.execute("""
        SELECT id, company_name, company_cik, document_url, document_type,
               document_date, state, doe_award_status, doe_pct_disbursed,
               doe_termination_date
        FROM regulatory_evidence
        ORDER BY id
    """).fetchall()

    print(f"  {len(rows)} rows in regulatory_evidence to migrate")

    # Field fill summary
    stats = {
        'with_cik': sum(1 for r in rows if r['company_cik']),
        'with_state': sum(1 for r in rows if r['state']),
        'with_doe_status': sum(1 for r in rows if r['doe_award_status']),
        'terminated': sum(1 for r in rows if r['doe_award_status'] == 'terminated'),
        'with_pct': sum(1 for r in rows if r['doe_pct_disbursed'] is not None),
    }
    for k, v in stats.items():
        print(f"    {k:<25} {v}/{len(rows)}")

    if DRY_RUN:
        print("\n[dry-run] No writes performed.")
        intel.close(); core.close()
        return

    now = now_iso()
    inserted = 0
    skipped  = 0
    for r in rows:
        existing = core.execute(
            "SELECT 1 FROM doe_status WHERE source_id=?", (r['id'],)
        ).fetchone()
        if existing:
            skipped += 1
            continue

        core.execute("""
            INSERT INTO doe_status
            (source_id, company_name, company_cik, document_url, document_type,
             document_date, state, doe_award_status, doe_pct_disbursed,
             doe_termination_date, migrated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r['id'], r['company_name'], r['company_cik'], r['document_url'],
            r['document_type'], r['document_date'], r['state'],
            r['doe_award_status'], r['doe_pct_disbursed'],
            r['doe_termination_date'], now
        ))
        inserted += 1

    core.commit()
    print(f"\n  Inserted: {inserted}  Skipped (already present): {skipped}")

    verify(core)

    intel.close()
    core.close()
    print("\nDone. Run with --verify to re-check anytime.")


if __name__ == '__main__':
    if VERIFY:
        core = sqlite3.connect(str(CORE_DB))
        core.row_factory = sqlite3.Row
        verify(core)
        core.close()
    else:
        run()
