#!/usr/bin/env python3
"""
DecarbIQ — collect_epa_ghgrp.py
Fetch EPA Greenhouse Gas Reporting Program data for hydrogen and CCS facilities.

Two subparts of interest:
  Subpart P  — Hydrogen Production (SMR, POX, etc.)
  Subpart RR — Geologic Sequestration of Carbon Dioxide

Uses EPA Envirofacts REST API:
  https://data.epa.gov/efservice/PUB_DIM_FACILITY/...

Writes to regulatory_evidence in core_database.db with source_system='epa_ghgrp'.

Usage:
    python collect_epa_ghgrp.py              # full run
    python collect_epa_ghgrp.py --dry-run    # show what would be inserted
    python collect_epa_ghgrp.py --stats      # current GHGRP coverage
    python collect_epa_ghgrp.py --subpart-p  # Subpart P only
    python collect_epa_ghgrp.py --subpart-rr # Subpart RR only
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA        = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC = 0.3
PAGE_SIZE = 500  # Envirofacts max rows per request

EFSERVICE = 'https://data.epa.gov/efservice'

DRY_RUN      = '--dry-run'    in sys.argv
STATS_ONLY   = '--stats'      in sys.argv
SUBPART_P    = '--subpart-p'  in sys.argv
SUBPART_RR   = '--subpart-rr' in sys.argv

# --limit N: cap total inserts (for testing)
_LIMIT = 0
for _a in sys.argv:
    if _a.startswith('--limit='):
        _LIMIT = int(_a.split('=', 1)[1])
    elif _a == '--limit':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _LIMIT = int(sys.argv[_idx + 1])

# --min-year YYYY: only collect records from this year onward
_MIN_YEAR = 0
for _a in sys.argv:
    if _a.startswith('--min-year='):
        _MIN_YEAR = int(_a.split('=', 1)[1])
    elif _a == '--min-year':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _MIN_YEAR = int(sys.argv[_idx + 1])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str) -> list | dict | None:
    """Fetch JSON from Envirofacts API."""
    try:
        r = requests.get(url, headers={'User-Agent': UA}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  ERROR fetching {url[:80]}: {e}')
        return None


def _paginate(table: str, filters: str = '', max_rows: int = 10000) -> list[dict]:
    """Fetch all rows from an Envirofacts table with pagination."""
    all_rows = []
    offset = 0
    while offset < max_rows:
        end = offset + PAGE_SIZE - 1
        url = f'{EFSERVICE}/{table}/{filters}rows/{offset}:{end}/JSON'
        data = _get_json(url)
        if not data or isinstance(data, dict):
            break
        all_rows.extend(data)
        if len(data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(SLEEP_SEC)
    return all_rows


def _has_subpart(reported_subparts: str, target: str) -> bool:
    """Check if a facility reports under a specific subpart.
    reported_subparts is a comma-separated string like 'C,P,Y' or 'C,II,PP'.
    We need exact match for single-letter subparts.
    """
    parts = [p.strip() for p in (reported_subparts or '').split(',')]
    return target in parts


def format_ghgrp_excerpt(fac: dict, subpart: str) -> str:
    """Format GHGRP facility into structured text for LLM extraction."""
    parts = []
    parts.append(f'EPA GHGRP Facility: {fac.get("facility_name", "Unknown")}')
    parts.append(f'Reporting Year: {fac.get("year", "")}')
    parts.append(f'Location: {fac.get("city", "")}, {fac.get("state", "")} {fac.get("zip", "")}')
    parts.append(f'County: {fac.get("county", "")}')

    addr = fac.get('address1') or ''
    if addr:
        parts.append(f'Address: {addr}')

    lat = fac.get('latitude')
    lon = fac.get('longitude')
    if lat and lon:
        parts.append(f'Coordinates: {lat}, {lon}')

    parts.append(f'NAICS Code: {fac.get("naics_code", "")}')
    parts.append(f'Reported Subparts: {fac.get("reported_subparts", "")}')

    parent = fac.get('parent_company') or ''
    if parent:
        parts.append(f'Parent Company: {parent}')

    frs = fac.get('frs_id') or fac.get('program_sys_id') or ''
    if frs:
        parts.append(f'FRS ID: {frs}')

    ghgrp_id = fac.get('facility_id') or ''
    if ghgrp_id:
        parts.append(f'GHGRP Facility ID: {ghgrp_id}')

    # CO2 capture info
    co2_cap = fac.get('co2_captured')
    if co2_cap:
        parts.append(f'CO2 Captured: {co2_cap}')

    # Subpart-specific context
    if subpart == 'P':
        parts.append('')
        parts.append('This facility reports under GHGRP Subpart P (Hydrogen Production).')
        parts.append('Subpart P covers facilities that produce hydrogen by steam methane '
                      'reforming (SMR), partial oxidation (POX), or other processes, and '
                      'emit ≥25,000 metric tons CO2e per year.')
    elif subpart == 'RR':
        parts.append('')
        parts.append('This facility reports under GHGRP Subpart RR (Geologic Sequestration of CO2).')
        parts.append('Subpart RR covers facilities that inject CO2 into subsurface geologic '
                      'formations for long-term containment (carbon capture and storage).')
        mrv_url = fac.get('rr_mrv_plan_url') or ''
        if mrv_url:
            parts.append(f'MRV Plan URL: {mrv_url}')

    return '\n'.join(parts)


def collect_subpart(db: sqlite3.Connection, subpart: str,
                    existing_urls: set) -> tuple[int, int]:
    """Collect all facilities reporting under a given GHGRP subpart."""
    label = {'P': 'Hydrogen Production', 'RR': 'CO2 Geologic Sequestration'}
    print(f'\n── Subpart {subpart}: {label.get(subpart, subpart)} ──────────')

    # Fetch facilities that CONTAIN this subpart in their reported_subparts
    # Note: CONTAINING/P also matches PP, so we filter in Python
    all_facs = _paginate('PUB_DIM_FACILITY',
                         f'REPORTED_SUBPARTS/CONTAINING/{subpart}/')

    # Filter to exact subpart match
    facs = [f for f in all_facs if _has_subpart(f.get('reported_subparts', ''), subpart)]

    # Filter by min year if set
    if _MIN_YEAR:
        facs = [f for f in facs if (f.get('year') or 0) >= _MIN_YEAR]

    # Deduplicate by (facility_id, year) — keep latest year per facility
    by_facility: dict[int, dict] = {}
    for f in facs:
        fid = f.get('facility_id')
        year = f.get('year', 0)
        if fid not in by_facility or year > by_facility[fid].get('year', 0):
            by_facility[fid] = f

    # Also keep all years as separate records for historical data
    print(f'  {len(facs)} total records ({len(by_facility)} unique facilities)')

    inserted = skipped = 0
    now = now_iso()

    for fac in facs:
        if _LIMIT and inserted >= _LIMIT:
            break

        fid = fac.get('facility_id', '')
        year = fac.get('year', '')
        doc_url = f'https://ghgdata.epa.gov/ghgp/service/facilityDetail?id={fid}&year={year}'

        if doc_url in existing_urls:
            skipped += 1
            continue

        company = fac.get('facility_name') or 'Unknown'
        text = format_ghgrp_excerpt(fac, subpart)
        doc_date = f'{year}-12-31' if year else now[:10]

        if DRY_RUN:
            print(f'  [dry-run] {company[:50]:<50}  {fac.get("city",""):<15} '
                  f'{fac.get("state",""):<3} year={year}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        frs = fac.get('frs_id') or fac.get('program_sys_id') or ''
        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, facility_id, permit_id,
             parent_company, derived_technology)
            VALUES (?,NULL,?,?,?,?,?,'epa_ghgrp',?,?,?,?,?)
        """, (company, f'ghgrp_subpart_{subpart.lower()}', doc_date, doc_url,
              text[:50000], len(text), now,
              str(fid) if fid else None,
              frs if frs else None,
              fac.get('parent_company') or None,
              'hydrogen_production' if subpart == 'P' else None))
        existing_urls.add(doc_url)
        inserted += 1

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


def print_stats(db: sqlite3.Connection):
    """Show current GHGRP coverage in regulatory_evidence."""
    print('\n=== GHGRP COVERAGE ===')
    for doc_type in ['ghgrp_subpart_p', 'ghgrp_subpart_rr']:
        cnt = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence WHERE document_type=?",
            (doc_type,)).fetchone()[0]
        distinct = db.execute(
            "SELECT COUNT(DISTINCT company_name) FROM regulatory_evidence WHERE document_type=?",
            (doc_type,)).fetchone()[0]
        print(f'  {doc_type:<25} {cnt:>4} records  ({distinct} distinct facilities)')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='epa_ghgrp'"
    ).fetchone()[0]
    print(f'\n  Total epa_ghgrp records: {total}')


def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    # Load existing URLs for dedup
    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system='epa_ghgrp'"
    )}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — EPA GHGRP')
    print(f'  Existing epa_ghgrp records: {len(existing)}')

    run_all = not SUBPART_P and not SUBPART_RR
    total_ins = total_skip = 0

    if run_all or SUBPART_P:
        ins, skip = collect_subpart(db, 'P', existing)
        total_ins += ins; total_skip += skip

    if (run_all or SUBPART_RR) and not (_LIMIT and total_ins >= _LIMIT):
        ins, skip = collect_subpart(db, 'RR', existing)
        total_ins += ins; total_skip += skip

    print(f'\n  TOTAL: Inserted={total_ins}  Skipped={total_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
