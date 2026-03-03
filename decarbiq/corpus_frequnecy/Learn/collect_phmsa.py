#!/usr/bin/env python3
"""
DecarbIQ — collect_phmsa.py
Fetch PHMSA hazardous-liquid pipeline annual report data for CO2/H2 operators.

Data sourced from Zenodo archive of PHMSA annual reports (mirrored from
phmsa.dot.gov).  Focuses on CO2 and H2-related pipeline operators with
Texas operations.

Uses CSV files from the hazardous-liquid annual report ZIP:
  Part A–E: operator info, commodity, state, total mileage
  Part H:   per-state mileage by pipe diameter

Writes to regulatory_evidence (source_system='phmsa') and pipeline_infrastructure.

Usage:
    python collect_phmsa.py              # full run (2020-present)
    python collect_phmsa.py --dry-run    # show what would be inserted
    python collect_phmsa.py --stats      # current coverage
    python collect_phmsa.py --limit=50   # cap inserts
    python collect_phmsa.py --year=2023  # single year only
"""
from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA            = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC     = 0.5
SOURCE_SYSTEM = 'phmsa'
MIN_YEAR      = 2020

# Zenodo archive of PHMSA hazardous-liquid annual reports (2010-present)
ZENODO_RECORD_ID = '14745187'
HL_ZIP_KEY = 'phmsagas_hazardous_liquid_2010_present.zip'
ZENODO_URL = (f'https://zenodo.org/api/records/{ZENODO_RECORD_ID}'
              f'/files/{HL_ZIP_KEY}/content')

# Commodities relevant to H2/CCS
RELEVANT_COMMODITIES = {'CO2', 'CARBON DIOXIDE'}
# HVL can include hydrogen but also propane/butane — too noisy for default
# Include via --include-hvl flag
INCLUDE_HVL = '--include-hvl' in sys.argv

DRY_RUN    = '--dry-run' in sys.argv
STATS_ONLY = '--stats'   in sys.argv

_LIMIT = 0
for _a in sys.argv:
    if _a.startswith('--limit='):
        _LIMIT = int(_a.split('=', 1)[1])
    elif _a == '--limit':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _LIMIT = int(sys.argv[_idx + 1])

_SINGLE_YEAR = 0
for _a in sys.argv:
    if _a.startswith('--year='):
        _SINGLE_YEAR = int(_a.split('=', 1)[1])
    elif _a == '--year':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _SINGLE_YEAR = int(sys.argv[_idx + 1])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Download & extract ───────────────────────────────────────────────────────

def _download_zip() -> zipfile.ZipFile | None:
    """Download PHMSA hazardous-liquid ZIP from Zenodo into memory."""
    print(f'  Downloading {HL_ZIP_KEY} from Zenodo...')
    try:
        r = requests.get(ZENODO_URL, headers={'User-Agent': UA},
                         timeout=300, stream=True)
        r.raise_for_status()
        buf = io.BytesIO(r.content)
        return zipfile.ZipFile(buf)
    except Exception as e:
        print(f'  ERROR downloading ZIP: {e}')
        return None


def _read_csv_from_zip(zf: zipfile.ZipFile, filename: str) -> list[dict]:
    """Read a CSV file from within the ZIP archive."""
    try:
        with zf.open(filename) as f:
            text = io.TextIOWrapper(f, encoding='utf-8', errors='replace')
            reader = csv.DictReader(text)
            return list(reader)
    except KeyError:
        print(f'  WARNING: {filename} not found in ZIP')
        return []
    except Exception as e:
        print(f'  ERROR reading {filename}: {e}')
        return []


# ── Relevance filter ─────────────────────────────────────────────────────────

def _is_relevant(row: dict) -> bool:
    """Check if this operator report is relevant (CO2/H2 commodity + TX)."""
    commodity = (row.get('PARTA5COMMODITY') or '').upper().strip()

    if commodity in RELEVANT_COMMODITIES:
        pass  # relevant
    elif INCLUDE_HVL and commodity == 'HVL':
        pass  # included via flag
    else:
        return False

    # Must have Texas in interstate or intrastate states
    inter = (row.get('PARTA7INTER') or '').upper()
    intra = (row.get('PARTA7INTRA') or '').upper()
    return 'TEXAS' in inter or 'TEXAS' in intra


# ── Technology mapping ───────────────────────────────────────────────────────

PHMSA_TECH_MAP = {
    'CO2': 'co2_transport_storage',
    'CARBON DIOXIDE': 'co2_transport_storage',
    'HVL': None,  # too ambiguous
}


# ── Excerpt formatting ───────────────────────────────────────────────────────

def format_phmsa_excerpt(row: dict, diameter_info: str | None = None) -> str:
    """Format PHMSA operator report into structured text for LLM."""
    parts = [
        f'PHMSA Hazardous Liquid Annual Report',
        f'Report Year: {row.get("REPORT_YEAR", "")}',
        f'Operator: {row.get("PARTA2NAMEOFCOMP", "")}',
        f'Operator ID: {row.get("OPERATOR_ID", "")}',
        f'Commodity: {row.get("PARTA5COMMODITY", "")}',
        f'Address: {row.get("PARTA4STREET", "")}, {row.get("PARTA4CITY", "")} '
        f'{row.get("PARTA4STATE", "")} {row.get("PARTA4ZIP", "")}',
        f'Interstate States: {row.get("PARTA7INTER", "")}',
        f'Intrastate States: {row.get("PARTA7INTRA", "")}',
        f'HCA Offshore Miles: {row.get("PARTBHCAOFFSHORE", "")}',
        f'HCA Onshore Miles: {row.get("PARTBHCAONSHORE", "")}',
        f'Total Pipeline Miles: {row.get("PARTDTOTALMILES", "")}',
    ]

    # Part C volumes (onshore CO2)
    co2_on = row.get('PARTCONCO2') or ''
    co2_off = row.get('PARTCOFFCO2') or ''
    if co2_on or co2_off:
        parts.append(f'CO2 Volume Onshore (barrels): {co2_on}')
        parts.append(f'CO2 Volume Offshore (barrels): {co2_off}')

    if diameter_info:
        parts.append('')
        parts.append(f'Pipe Diameter Detail (Texas):')
        parts.append(diameter_info)

    parts.append('')
    parts.append('This report is from the PHMSA Form 7000-1.1 annual filing '
                 'for hazardous liquid or CO2 pipeline systems.')

    return '\n'.join(p for p in parts if p is not None)


# ── Company name matching ────────────────────────────────────────────────────

def _match_company(db: sqlite3.Connection, entity_name: str) -> str | None:
    """Try exact → normalized → prefix match against companies table."""
    cn = (entity_name or '').strip()
    if not cn:
        return None
    cn_upper = cn.upper()

    co = db.execute(
        "SELECT company_id FROM companies WHERE UPPER(company_name)=?",
        (cn_upper,)).fetchone()
    if co:
        return co['company_id']

    normalized = re.sub(
        r'\b(llc|l\.l\.c|lp|l\.p|inc|incorporated|corp|corporation|'
        r'ltd|limited|company|co|usa|u\.s\.a|u\.s)\b\.?',
        '', cn_upper, flags=re.I).strip().rstrip(',').strip()
    if normalized != cn_upper:
        co = db.execute(
            "SELECT company_id FROM companies WHERE UPPER(company_name)=?",
            (normalized,)).fetchone()
        if co:
            return co['company_id']

    words = normalized.split()
    for length in range(len(words) - 1, 1, -1):
        prefix = ' '.join(words[:length])
        co = db.execute(
            "SELECT company_id FROM companies WHERE UPPER(company_name) LIKE ?",
            (prefix + '%',)).fetchone()
        if co:
            return co['company_id']

    return None


# ── Direct enrichment ────────────────────────────────────────────────────────

def _direct_enrich(db: sqlite3.Connection, row: dict,
                   source_url: str) -> bool:
    """Match operator → companies → unified_projects for conservative enrichment."""
    from bootstrap_projects import apply_stage_update

    company_name = row.get('PARTA2NAMEOFCOMP', '')
    company_id = _match_company(db, company_name)
    if not company_id:
        return False

    projects = db.execute(
        "SELECT project_id, stage, technology, capacity_raw "
        "FROM unified_projects WHERE company_id=?",
        (company_id,)).fetchall()

    enriched = False
    now = now_iso()
    commodity = (row.get('PARTA5COMMODITY') or '').upper().strip()
    technology = PHMSA_TECH_MAP.get(commodity)

    for p in projects:
        pid = p['project_id']

        # Technology — only for unambiguous CO2 → co2_transport_storage
        if technology and not (p['technology'] or '').strip():
            db.execute("""
                UPDATE unified_projects SET technology=?, updated_at=?
                WHERE project_id=? AND (technology IS NULL OR technology='')
            """, (technology, now, pid))
            enriched = True

        # Pipeline operators with active reports → at minimum "operational"
        total_miles = _safe_float(row.get('PARTDTOTALMILES'))
        if total_miles and total_miles > 0:
            updated = apply_stage_update(
                db, pid, 'operational', None,
                f'phmsa: {source_url}', confidence=0.65)
            if updated:
                enriched = True

    return enriched


def _safe_float(val) -> float | None:
    try:
        return float(val) if val else None
    except (ValueError, TypeError):
        return None


# ── Pipeline infrastructure table ────────────────────────────────────────────

def _ensure_tables(db: sqlite3.Connection):
    """Create pipeline_infrastructure table if not exists."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_infrastructure (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operator_id TEXT NOT NULL,
            operator_name TEXT,
            pipeline_name TEXT,
            commodity TEXT,
            state TEXT,
            counties TEXT,
            diameter_inches REAL,
            length_miles REAL,
            maop_psig REAL,
            status TEXT,
            install_date TEXT,
            report_year INTEGER,
            collected_at TEXT,
            UNIQUE(operator_id, commodity, state, report_year)
        )
    """)
    db.commit()


def _insert_infrastructure(db: sqlite3.Connection, row: dict,
                           tx_miles: float | None, diameter_info: str | None):
    """Insert into pipeline_infrastructure table."""
    operator_id = row.get('OPERATOR_ID', '')
    commodity = (row.get('PARTA5COMMODITY') or '').strip()
    report_year = int(row.get('REPORT_YEAR', 0))

    try:
        db.execute("""
            INSERT OR IGNORE INTO pipeline_infrastructure
            (operator_id, operator_name, commodity, state,
             length_miles, report_year, collected_at)
            VALUES (?,?,?,?,?,?,?)
        """, (operator_id, row.get('PARTA2NAMEOFCOMP', ''),
              commodity, 'TX', tx_miles, report_year, now_iso()))
    except sqlite3.IntegrityError:
        pass  # duplicate


# ── Per-state diameter data (Part H) ─────────────────────────────────────────

def _build_diameter_index(zf: zipfile.ZipFile, year: int) -> dict:
    """Build index of (operator_id, commodity) → TX diameter breakdown."""
    filename = f'HL AR {year} Part H.csv'
    rows = _read_csv_from_zip(zf, filename)
    if not rows:
        return {}

    index: dict[tuple, str] = {}
    for row in rows:
        state = (row.get('STATE_NAME') or '').upper().strip()
        if state != 'TEXAS':
            continue

        key = (row.get('OPERATOR_ID', ''), row.get('PARTA5COMMODITY', ''))

        # Build diameter summary
        diameters = []
        for col_suffix, diam in [
            ('4LESS', '≤4"'), ('6', '6"'), ('8', '8"'), ('10', '10"'),
            ('12', '12"'), ('14', '14"'), ('16', '16"'), ('18', '18"'),
            ('20', '20"'), ('22', '22"'), ('24', '24"'), ('26', '26"'),
            ('28', '28"'), ('30', '30"'), ('32', '32"'), ('36', '36"'),
            ('40', '40"'), ('42', '42"'), ('48', '48"'),
        ]:
            on_val = _safe_float(row.get(f'PARTHON{col_suffix}'))
            if on_val and on_val > 0:
                diameters.append(f'  {diam}: {on_val:.2f} mi (onshore)')
            off_val = _safe_float(row.get(f'PARTHOFF{col_suffix}'))
            if off_val and off_val > 0:
                diameters.append(f'  {diam}: {off_val:.2f} mi (offshore)')

        if diameters:
            index[key] = '\n'.join(diameters)

    return index


# ── Collection ───────────────────────────────────────────────────────────────

def collect_year(db: sqlite3.Connection, zf: zipfile.ZipFile,
                 year: int, existing_urls: set,
                 diameter_index: dict) -> tuple[int, int]:
    """Collect all TX CO2/H2 operators for a given year."""
    filename = f'HL AR {year} Part A to E.csv'
    rows = _read_csv_from_zip(zf, filename)
    if not rows:
        print(f'  Year {year}: no data')
        return 0, 0

    relevant = [r for r in rows if _is_relevant(r)]
    print(f'  Year {year}: {len(rows)} total operators, '
          f'{len(relevant)} TX CO2/H2 relevant')

    inserted = skipped = 0
    now = now_iso()

    for row in relevant:
        if _LIMIT and inserted >= _LIMIT:
            break

        operator_id = row.get('OPERATOR_ID', '')
        commodity = (row.get('PARTA5COMMODITY') or '').strip()
        report_year = row.get('REPORT_YEAR', '')
        doc_url = f'phmsa://operator_{operator_id}/commodity_{commodity}/state_TX/{report_year}'

        if doc_url in existing_urls:
            skipped += 1
            continue

        company = row.get('PARTA2NAMEOFCOMP', 'Unknown')
        total_miles = _safe_float(row.get('PARTDTOTALMILES'))
        diam_key = (operator_id, row.get('PARTA5COMMODITY', ''))
        diameter_info = diameter_index.get(diam_key)
        text = format_phmsa_excerpt(row, diameter_info)
        technology = PHMSA_TECH_MAP.get(commodity.upper())

        if DRY_RUN:
            print(f'  [dry-run] {company[:40]:<40} opID={operator_id:<6} '
                  f'{commodity:<10} {total_miles or 0:>8.1f} mi  '
                  f'tech={technology or "-"}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, facility_id,
             derived_technology, operator_name)
            VALUES (?,NULL,'phmsa_annual_report',?,?,?,?,?,?,?,?,?)
        """, (company, f'{report_year}-12-31', doc_url,
              text[:50000], len(text), SOURCE_SYSTEM, now,
              operator_id, technology, company))

        # Pipeline infrastructure table
        _insert_infrastructure(db, row, total_miles, diameter_info)

        existing_urls.add(doc_url)
        inserted += 1

        # Direct enrichment
        _direct_enrich(db, row, doc_url)

    if not DRY_RUN:
        db.commit()

    return inserted, skipped


# ── Stats ────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection):
    """Show current PHMSA coverage."""
    print('\n=== PHMSA COVERAGE ===')
    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Total phmsa records: {total}')

    distinct = db.execute(
        "SELECT COUNT(DISTINCT company_name) FROM regulatory_evidence "
        "WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Distinct operators: {distinct}')

    # Check pipeline_infrastructure table
    try:
        infra = db.execute(
            "SELECT COUNT(*) FROM pipeline_infrastructure").fetchone()[0]
        infra_ops = db.execute(
            "SELECT COUNT(DISTINCT operator_id) FROM pipeline_infrastructure"
        ).fetchone()[0]
        print(f'  Pipeline infrastructure records: {infra} '
              f'({infra_ops} operators)')
    except sqlite3.OperationalError:
        pass  # table doesn't exist yet


# ── Entry point ──────────────────────────────────────────────────────────────

def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found')
        sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    _ensure_tables(db)

    # Load existing URLs for dedup
    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,))}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — PHMSA Pipeline Data')
    print(f'  Existing phmsa records: {len(existing)}')

    # Download ZIP
    zf = _download_zip()
    if not zf:
        db.close()
        return

    # Determine years to process
    if _SINGLE_YEAR:
        years = [_SINGLE_YEAR]
    else:
        years = list(range(MIN_YEAR, 2025))  # 2020-2024

    total_ins = total_skip = 0
    for year in years:
        if _LIMIT and total_ins >= _LIMIT:
            break

        # Build diameter index for this year
        diam_index = _build_diameter_index(zf, year)
        ins, skip = collect_year(db, zf, year, existing, diam_index)
        total_ins += ins
        total_skip += skip

    zf.close()

    print(f'\n  TOTAL: Inserted={total_ins}  Skipped={total_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
