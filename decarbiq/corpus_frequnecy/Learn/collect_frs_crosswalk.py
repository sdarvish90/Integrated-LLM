#!/usr/bin/env python3
"""
DecarbIQ — collect_frs_crosswalk.py
Fetch EPA FRS (Facility Registry Service) program crosswalk for
TCEQ-registered facilities in Texas.

Two-phase collection:
  Phase A: TCEQ RN → FRS Registry ID lookup
  Phase B: Registry ID → all linked program IDs (E-GGRT, RCRAINFO, etc.)

Uses Envirofacts REST API (public, no auth):
  https://data.epa.gov/efservice/FRS_PROGRAM_FACILITY/...

Writes to frs_crosswalk table in core_database.db.

Usage:
    python collect_frs_crosswalk.py              # full run (A + B)
    python collect_frs_crosswalk.py --dry-run    # show what would be found
    python collect_frs_crosswalk.py --stats      # current FRS crosswalk state
    python collect_frs_crosswalk.py --phase-a    # Phase A only (RN → FRS)
    python collect_frs_crosswalk.py --phase-b    # Phase B only (expand programs)
    python collect_frs_crosswalk.py --limit=50   # cap RN lookups
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA        = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC = 0.3
EFSERVICE = 'https://data.epa.gov/efservice'
TCEQ_ACR  = 'TX-TCEQ ACR'

DRY_RUN    = '--dry-run'  in sys.argv
STATS_ONLY = '--stats'    in sys.argv
PHASE_A    = '--phase-a'  in sys.argv
PHASE_B    = '--phase-b'  in sys.argv

_LIMIT = 0
for _a in sys.argv:
    if _a.startswith('--limit='):
        _LIMIT = int(_a.split('=', 1)[1])
    elif _a == '--limit':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _LIMIT = int(sys.argv[_idx + 1])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, retries: int = 3) -> list | dict | None:
    """Fetch JSON from Envirofacts API with retry."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=30)
            if r.status_code == 429:
                wait = 3 ** (attempt + 1)
                print(f'  Rate limited, waiting {wait}s...')
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            # Envirofacts returns empty list or None for no results
            if not data:
                return None
            return data
        except requests.exceptions.JSONDecodeError:
            return None
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            print(f'  ERROR fetching {url[:90]}: {e}')
            return None
    return None


# ── Schema ───────────────────────────────────────────────────────────────────
def _ensure_tables(db: sqlite3.Connection):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS frs_crosswalk (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id     TEXT NOT NULL,
            pgm_sys_acrnm   TEXT NOT NULL,
            pgm_sys_id      TEXT NOT NULL,
            primary_name    TEXT,
            city_name       TEXT,
            county_name     TEXT,
            state_code      TEXT DEFAULT 'TX',
            postal_code     TEXT,
            source_of_data  TEXT,
            collected_at    TEXT NOT NULL,
            UNIQUE(registry_id, pgm_sys_acrnm, pgm_sys_id)
        );
        CREATE INDEX IF NOT EXISTS idx_frs_registry ON frs_crosswalk(registry_id);
        CREATE INDEX IF NOT EXISTS idx_frs_pgm_id ON frs_crosswalk(pgm_sys_id);
    """)


def _insert_row(db: sqlite3.Connection, rec: dict, ts: str) -> bool:
    """Insert a single FRS record. Returns True if new row inserted."""
    try:
        db.execute("""
            INSERT OR IGNORE INTO frs_crosswalk
            (registry_id, pgm_sys_acrnm, pgm_sys_id, primary_name,
             city_name, county_name, state_code, postal_code,
             source_of_data, collected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec.get('registry_id', ''),
            rec.get('pgm_sys_acrnm', ''),
            rec.get('pgm_sys_id', ''),
            rec.get('primary_name'),
            rec.get('city_name'),
            rec.get('county_name'),
            rec.get('state_code', 'TX'),
            rec.get('postal_code'),
            rec.get('source_of_data'),
            ts,
        ))
        return db.total_changes > 0  # approximate — works for single inserts
    except sqlite3.IntegrityError:
        return False


# ── Phase A: TCEQ RN → FRS Registry ID ──────────────────────────────────────
def collect_phase_a(db: sqlite3.Connection) -> tuple[int, int, int]:
    """Look up each TCEQ RN number in FRS. Returns (found, not_found, skipped)."""
    # Get all distinct RN numbers from tceq_permits
    rn_numbers = [r[0] for r in db.execute(
        "SELECT DISTINCT rn_number FROM tceq_permits WHERE rn_number IS NOT NULL"
    ).fetchall()]

    # Already collected RNs (skip if re-running)
    existing = set(r[0] for r in db.execute(
        "SELECT DISTINCT pgm_sys_id FROM frs_crosswalk WHERE pgm_sys_acrnm = ?",
        (TCEQ_ACR,)
    ).fetchall())

    todo = [rn for rn in rn_numbers if rn not in existing]
    if _LIMIT:
        todo = todo[:_LIMIT]

    print(f"  TCEQ RN numbers: {len(rn_numbers)} total, {len(existing)} already in FRS, {len(todo)} to look up")

    found = not_found = skipped = 0
    ts = now_iso()
    acrnm_encoded = quote(TCEQ_ACR)

    for i, rn in enumerate(todo, 1):
        url = f'{EFSERVICE}/FRS_PROGRAM_FACILITY/PGM_SYS_ACRNM/{acrnm_encoded}/PGM_SYS_ID/{rn}/JSON'
        data = _get_json(url)

        if data and isinstance(data, list) and len(data) > 0:
            rec = data[0]
            reg_id = rec.get('registry_id')
            if reg_id:
                if DRY_RUN:
                    print(f'    [dry] {rn} → {reg_id}  {rec.get("primary_name", "?")}')
                else:
                    _insert_row(db, rec, ts)
                found += 1
            else:
                not_found += 1
        else:
            not_found += 1

        if i % 50 == 0:
            print(f'    ... {i}/{len(todo)} ({found} found, {not_found} missing)')
            if not DRY_RUN:
                db.commit()

        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()

    skipped = len(existing)
    return found, not_found, skipped


# ── Phase B: Expand Registry IDs → All Programs ─────────────────────────────
def collect_phase_b(db: sqlite3.Connection) -> int:
    """For each registry_id from Phase A, fetch all linked program IDs."""
    # Get unique registry_ids that have TCEQ entries but haven't been expanded
    registry_ids = [r[0] for r in db.execute("""
        SELECT DISTINCT registry_id FROM frs_crosswalk
        WHERE pgm_sys_acrnm = ?
    """, (TCEQ_ACR,)).fetchall()]

    # Count existing programs per registry_id to detect already-expanded
    expanded = set()
    for r in db.execute("""
        SELECT registry_id, COUNT(DISTINCT pgm_sys_acrnm) AS cnt
        FROM frs_crosswalk
        GROUP BY registry_id
        HAVING cnt > 1
    """).fetchall():
        expanded.add(r[0])

    todo = [rid for rid in registry_ids if rid not in expanded]
    if _LIMIT:
        todo = todo[:_LIMIT]

    print(f"  Registry IDs: {len(registry_ids)} total, {len(expanded)} already expanded, {len(todo)} to expand")

    added = 0
    ts = now_iso()

    for i, reg_id in enumerate(todo, 1):
        url = f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{reg_id}/JSON'
        data = _get_json(url)

        if data and isinstance(data, list):
            for rec in data:
                pgm = rec.get('pgm_sys_acrnm', '')
                pid = rec.get('pgm_sys_id', '')
                if not pgm or not pid:
                    continue
                if DRY_RUN:
                    pass  # don't print every program link (too verbose)
                else:
                    before = db.total_changes
                    _insert_row(db, rec, ts)
                    if db.total_changes > before:
                        added += 1

            if DRY_RUN:
                progs = set(r.get('pgm_sys_acrnm', '') for r in data if r.get('pgm_sys_acrnm'))
                print(f'    [dry] {reg_id}: {len(data)} programs ({", ".join(sorted(progs)[:5])}...)')
                added += len(data)

        if i % 50 == 0:
            print(f'    ... {i}/{len(todo)} ({added} programs added)')
            if not DRY_RUN:
                db.commit()

        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()

    return added


# ── Stats ────────────────────────────────────────────────────────────────────
def print_stats(db: sqlite3.Connection):
    print("\n" + "=" * 60)
    print("FRS CROSSWALK — STATISTICS")
    print("=" * 60)

    total = db.execute("SELECT COUNT(*) FROM frs_crosswalk").fetchone()[0]
    registries = db.execute("SELECT COUNT(DISTINCT registry_id) FROM frs_crosswalk").fetchone()[0]
    print(f"\n  Total rows: {total}")
    print(f"  Unique registry IDs: {registries}")

    print(f"\n  By program system:")
    for r in db.execute("""
        SELECT pgm_sys_acrnm, COUNT(*), COUNT(DISTINCT registry_id)
        FROM frs_crosswalk
        GROUP BY pgm_sys_acrnm
        ORDER BY COUNT(*) DESC
    """).fetchall():
        print(f"    {r[0]:20s}  {r[1]:5d} records  ({r[2]} facilities)")

    # TCEQ RN coverage
    tceq_total = db.execute("SELECT COUNT(DISTINCT rn_number) FROM tceq_permits").fetchone()[0]
    tceq_in_frs = db.execute("""
        SELECT COUNT(DISTINCT pgm_sys_id) FROM frs_crosswalk
        WHERE pgm_sys_acrnm = ?
    """, (TCEQ_ACR,)).fetchone()[0]
    print(f"\n  TCEQ coverage: {tceq_in_frs}/{tceq_total} RN numbers found in FRS ({100*tceq_in_frs/max(1,tceq_total):.1f}%)")

    # High-value: TCEQ + E-GGRT overlap
    overlap = db.execute("""
        SELECT fc_tceq.pgm_sys_id AS tceq_rn,
               fc_ggrt.pgm_sys_id AS ghgrp_id,
               fc_tceq.primary_name,
               fc_tceq.county_name,
               fc_tceq.registry_id
        FROM frs_crosswalk fc_tceq
        JOIN frs_crosswalk fc_ggrt ON fc_tceq.registry_id = fc_ggrt.registry_id
        WHERE fc_tceq.pgm_sys_acrnm = ?
          AND fc_ggrt.pgm_sys_acrnm = 'E-GGRT'
        GROUP BY fc_tceq.registry_id
    """, (TCEQ_ACR,)).fetchall()

    if overlap:
        print(f"\n  HIGH-VALUE: {len(overlap)} facilities with TCEQ permits + GHGRP emissions:")
        for r in overlap[:15]:
            print(f"    {r[0]:15s}  GHGRP:{r[1]:10s}  {r[2][:45]:45s}  {r[3] or '?'}")
        if len(overlap) > 15:
            print(f"    ... and {len(overlap) - 15} more")


# ── Entry point ──────────────────────────────────────────────────────────────
def run():
    db = sqlite3.connect(str(CORE_DB))
    _ensure_tables(db)

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    run_a = PHASE_A or (not PHASE_A and not PHASE_B)
    run_b = PHASE_B or (not PHASE_A and not PHASE_B)

    prefix = "DRY RUN — " if DRY_RUN else ""
    print(f"{prefix}FRS Crosswalk Collector")
    print(f"  Database: {CORE_DB}")

    if run_a:
        print(f"\n── Phase A: TCEQ RN → FRS Registry ID {'(dry run)' if DRY_RUN else ''} ──")
        found, not_found, skipped = collect_phase_a(db)
        print(f"  Found: {found}  Not in FRS: {not_found}  Skipped (already collected): {skipped}")

    if run_b:
        print(f"\n── Phase B: Expand Registry IDs → All Programs {'(dry run)' if DRY_RUN else ''} ──")
        added = collect_phase_b(db)
        print(f"  Program links added: {added}")

    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
