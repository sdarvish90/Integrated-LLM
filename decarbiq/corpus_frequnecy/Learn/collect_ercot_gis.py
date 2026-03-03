#!/usr/bin/env python3
"""
DecarbIQ — collect_ercot_gis.py
Fetch ERCOT Generation Interconnection Status (GIS) report data
for hydrogen, CCS, and large gas generation projects in Texas.

Downloads monthly GIS report Excel files via ERCOT MIS API.
source_system='ercot_gis' (distinct from 'ercot' used by collect_grid.py).

Usage:
    python collect_ercot_gis.py              # full run (latest report)
    python collect_ercot_gis.py --dry-run    # show what would be inserted
    python collect_ercot_gis.py --stats      # current coverage
    python collect_ercot_gis.py --limit=50   # cap inserts
    python collect_ercot_gis.py --all-months # process all available monthly reports
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import openpyxl
import requests

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA          = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC   = 1.0
SOURCE_SYSTEM = 'ercot_gis'
MIN_YEAR    = 2020
HEADER_ROW  = 31  # 1-indexed row number containing column headers
DATA_START  = 36  # 1-indexed first data row

# ERCOT MIS endpoints (HTTPS preferred, HTTP fallback)
MIS_LIST_URL = 'https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=15933'
MIS_DOWNLOAD = 'https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={doc_id}'

DRY_RUN    = '--dry-run'    in sys.argv
STATS_ONLY = '--stats'      in sys.argv
ALL_MONTHS = '--all-months' in sys.argv

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


# ── H2/CCS relevance filter ─────────────────────────────────────────────────

H2_CCS_KEYWORDS = [
    'hydrogen', 'ammonia', 'ccs', 'carbon capture', 'sequestration',
    'reforming', 'electrolysis', 'blue hydrogen', 'green hydrogen', 'h2',
    'methanol', 'syngas', 'gasification',
]

# Fuel types that are NEVER H2/CCS relevant — exclude before keyword check
NON_H2_FUELS = {'SOL', 'SUN', 'WIN', 'WND', 'BAT', 'ESS', 'DFO', 'OIL', 'BIO', 'WAS'}

# GIM Study Phase values are like "SS Completed, FIS Completed, IA"
# meaning: Screening Study done, Full Interconnection Study done, IA signed.
# These DON'T directly map to project construction stages.
# Stage is derived from milestone dates + the IA status instead.

# Fuel codes
ERCOT_FUEL_MAP = {
    'GAS': 'natural_gas', 'NG': 'natural_gas',
    'SOL': None, 'SUN': None,  # solar — not relevant
    'WIN': None, 'WND': None,  # wind — not relevant
    'BAT': None, 'ESS': None,  # battery — not relevant
    'NUC': 'nuclear_smr',
    'H2': 'hydrogen_production',
    'BIO': None, 'WAS': None,  # biomass — rarely relevant
    'DFO': None, 'OIL': None,  # diesel/oil — not relevant
}


def _is_relevant(row: dict) -> bool:
    """Check if ERCOT GIS entry is H2/CCS relevant."""
    name = (row.get('project_name') or '').lower()
    fuel = (row.get('fuel') or '').upper()
    tech = (row.get('technology') or '').lower()
    capacity_mw = row.get('capacity_mw') or 0

    # Direct H2 fuel type
    if fuel in ('H2', 'HYD'):
        return True

    # Exclude solar/wind/battery before keyword check — avoids
    # "Blue Jay Solar" matching 'blue hydrogen'
    if fuel in NON_H2_FUELS:
        return False

    # Keywords in project name
    if any(kw in name for kw in H2_CCS_KEYWORDS):
        return True

    # Technology keywords
    if any(kw in tech for kw in ['hydrogen', 'fuel cell', 'h2']):
        return True

    # Large gas projects (>100 MW) — potential SMR/ATR facilities
    if fuel in ('GAS', 'NG') and isinstance(capacity_mw, (int, float)) and capacity_mw >= 100:
        return True

    return False


def _derive_stage(row: dict) -> str | None:
    """Derive stage from milestone dates and IA status.

    GIM Study Phase values like "SS Completed, FIS Completed, IA" describe
    interconnection STUDY progress — NOT construction/operational stages.
    Stage is derived from actual milestone dates instead:
      - construction_end filled → operational (construction done)
      - construction_start filled, no end → construction
      - ia_signed filled → permitted (interconnection agreement executed)
      - GIM phase ends with ", IA" (IA signed) → permitted
      - Everything else → None (leave for LLM pipeline)
    """
    construction_end = (row.get('construction_end') or '').strip()
    construction_start = (row.get('construction_start') or '').strip()
    ia_signed = (row.get('ia_signed') or '').strip()
    phase = (row.get('gim_study_phase') or '').strip()

    if construction_end:
        return 'operational'
    if construction_start:
        return 'construction'
    if ia_signed:
        return 'permitted'
    # GIM phase ending with ", IA" means IA was signed (no separate date column)
    if phase.endswith(', IA'):
        return 'permitted'
    return None


# ── MIS API ──────────────────────────────────────────────────────────────────

def _get_json(url: str) -> dict | list | None:
    """Fetch JSON from ERCOT MIS API. Try HTTPS, fall back to HTTP."""
    for attempt_url in [url, url.replace('https://', 'http://')]:
        try:
            r = requests.get(attempt_url, headers={'User-Agent': UA}, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            continue
    print(f'  ERROR: Could not fetch {url[:80]}')
    return None


def _list_gis_reports() -> list[dict]:
    """List available GIS report files from ERCOT MIS."""
    data = _get_json(MIS_LIST_URL)
    if not data:
        return []
    doc_list = data.get('ListDocsByRptTypeRes', {}).get('DocumentList', [])
    # Filter to GIS_Report files only (exclude co-located battery reports)
    gis_reports = []
    for entry in doc_list:
        doc = entry.get('Document', {})
        name = doc.get('FriendlyName', '')
        if 'GIS_Report' in name and 'Battery' not in name:
            gis_reports.append(doc)
    # Sort by publish date descending
    gis_reports.sort(key=lambda d: d.get('PublishDate', ''), reverse=True)
    return gis_reports


def _download_report(doc_id: str) -> Path | None:
    """Download GIS report Excel file to temp location."""
    url = MIS_DOWNLOAD.format(doc_id=doc_id)
    for attempt_url in [url, url.replace('https://', 'http://')]:
        try:
            r = requests.get(attempt_url, headers={'User-Agent': UA}, timeout=120)
            if r.status_code == 200 and len(r.content) > 10000:
                tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
                tmp.write(r.content)
                tmp.close()
                return Path(tmp.name)
        except Exception:
            continue
    print(f'  ERROR: Could not download report {doc_id}')
    return None


# ── Excel parsing ────────────────────────────────────────────────────────────

def _parse_gis_excel(xlsx_path: Path) -> list[dict]:
    """Parse GIS report Excel into list of project dicts."""
    wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
    projects = []

    for sheet_name in ['Project Details - Large Gen', 'Project Details - Small Gen']:
        if sheet_name not in wb.sheetnames:
            continue

        ws = wb[sheet_name]
        gen_size = 'large' if 'Large' in sheet_name else 'small'

        # Read header row
        header_cells = list(ws.iter_rows(
            min_row=HEADER_ROW, max_row=HEADER_ROW, values_only=True))[0]
        headers = []
        for i, h in enumerate(header_cells):
            if h:
                headers.append(str(h).strip())
            else:
                headers.append(f'_col_{i}')

        # Build column index map
        col_map = {}
        for i, h in enumerate(headers):
            h_lower = h.lower()
            if h_lower == 'inr':
                col_map['inr'] = i
            elif 'project name' in h_lower:
                col_map['project_name'] = i
            elif 'gim study phase' in h_lower or 'study phase' in h_lower:
                col_map['gim_study_phase'] = i
            elif 'interconnecting entity' in h_lower:
                col_map['entity'] = i
            elif 'poi location' in h_lower:
                col_map['poi'] = i
            elif h_lower == 'county':
                col_map['county'] = i
            elif 'projected cod' in h_lower:
                col_map['projected_cod'] = i
            elif h_lower == 'fuel':
                col_map['fuel'] = i
            elif h_lower == 'technology':
                col_map['technology'] = i
            elif 'capacity' in h_lower and 'mw' in h_lower:
                col_map['capacity_mw'] = i
            elif 'construction start' in h_lower:
                col_map['construction_start'] = i
            elif 'construction end' in h_lower:
                col_map['construction_end'] = i
            elif 'ia signed' in h_lower:
                col_map['ia_signed'] = i
            elif 'air permit' in h_lower:
                col_map['air_permit'] = i
            elif 'ghg permit' in h_lower:
                col_map['ghg_permit'] = i
            elif 'comment' in h_lower:
                col_map['comment'] = i

        # Read data rows
        for row in ws.iter_rows(min_row=DATA_START, values_only=True):
            vals = list(row)
            inr = vals[col_map['inr']] if 'inr' in col_map and col_map['inr'] < len(vals) else None
            if not inr:
                continue  # skip empty rows

            def _get(key):
                if key in col_map and col_map[key] < len(vals):
                    return vals[col_map[key]]
                return None

            def _date_str(val):
                """Convert datetime to YYYY-MM-DD string."""
                if val is None:
                    return None
                if isinstance(val, datetime):
                    return val.strftime('%Y-%m-%d')
                s = str(val).strip()
                if len(s) >= 10:
                    return s[:10]
                return s if s else None

            capacity = _get('capacity_mw')
            try:
                capacity = float(capacity) if capacity else None
            except (ValueError, TypeError):
                capacity = None

            projects.append({
                'inr': str(inr).strip(),
                'project_name': str(_get('project_name') or '').strip(),
                'gim_study_phase': str(_get('gim_study_phase') or '').strip(),
                'entity': str(_get('entity') or '').strip(),
                'poi': str(_get('poi') or '').strip(),
                'county': str(_get('county') or '').strip(),
                'projected_cod': _date_str(_get('projected_cod')),
                'fuel': str(_get('fuel') or '').strip(),
                'technology': str(_get('technology') or '').strip(),
                'capacity_mw': capacity,
                'construction_start': _date_str(_get('construction_start')),
                'construction_end': _date_str(_get('construction_end')),
                'ia_signed': _date_str(_get('ia_signed')),
                'air_permit': _date_str(_get('air_permit')),
                'ghg_permit': _date_str(_get('ghg_permit')),
                'comment': str(_get('comment') or '').strip()[:500],
                'gen_size': gen_size,
            })

    wb.close()
    return projects


# ── Excerpt formatting ───────────────────────────────────────────────────────

def format_ercot_excerpt(proj: dict) -> str:
    """Format ERCOT GIS queue entry into structured text for LLM."""
    parts = [
        f'ERCOT GIS Queue Entry',
        f'INR: {proj["inr"]}',
        f'Project: {proj["project_name"]}',
        f'Interconnecting Entity: {proj["entity"]}',
        f'Location: {proj["county"]} County, TX',
        f'POI: {proj["poi"]}',
        f'Capacity: {proj["capacity_mw"]} MW' if proj["capacity_mw"] else '',
        f'Fuel: {proj["fuel"]}',
        f'Technology: {proj["technology"]}',
        f'GIM Study Phase: {proj["gim_study_phase"]}',
        f'Projected COD: {proj["projected_cod"]}' if proj["projected_cod"] else '',
        f'Construction Start: {proj["construction_start"]}' if proj["construction_start"] else '',
        f'Construction End: {proj["construction_end"]}' if proj["construction_end"] else '',
        f'IA Signed: {proj["ia_signed"]}' if proj["ia_signed"] else '',
        f'Air Permit: {proj["air_permit"]}' if proj["air_permit"] else '',
        f'GHG Permit: {proj["ghg_permit"]}' if proj["ghg_permit"] else '',
        f'Comment: {proj["comment"]}' if proj["comment"] else '',
        f'Generator Size: {proj["gen_size"]}',
    ]
    return '\n'.join(p for p in parts if p)


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

def _direct_enrich(db: sqlite3.Connection, proj: dict,
                   source_url: str) -> bool:
    """Match entity → companies → unified_projects, apply conservative enrichment."""
    from bootstrap_projects import apply_stage_update

    company_id = _match_company(db, proj['entity'])
    if not company_id:
        return False

    projects = db.execute(
        "SELECT project_id, stage, technology, capacity_raw, cod_date, "
        "evidence_gap_flags_json FROM unified_projects WHERE company_id=?",
        (company_id,)).fetchall()

    enriched = False
    now = now_iso()
    stage = _derive_stage(proj)

    for p in projects:
        pid = p['project_id']

        # Stage (conservative — only unambiguous mappings)
        if stage:
            updated = apply_stage_update(
                db, pid, stage, proj.get('projected_cod'),
                f'ercot_gis: {source_url}', confidence=0.70)
            if updated:
                enriched = True

        # Capacity — unambiguous (direct MW from queue)
        if proj['capacity_mw'] and not (p['capacity_raw'] or '').strip():
            cap_str = f'{proj["capacity_mw"]} MW'
            db.execute("""
                UPDATE unified_projects SET capacity_raw=?, updated_at=?
                WHERE project_id=? AND (capacity_raw IS NULL OR capacity_raw='')
            """, (cap_str, now, pid))
            enriched = True

        # COD — conservative: store with provenance flag, not as authoritative
        if proj['projected_cod'] and not (p['cod_date'] or '').strip():
            cod_year = proj['projected_cod'][:4]
            raw_flags = json.loads(p['evidence_gap_flags_json'] or '{}')
            flags = raw_flags if isinstance(raw_flags, dict) else {}
            flags['cod_source'] = 'ercot_gis_proposed'
            db.execute("""
                UPDATE unified_projects
                SET cod_date=?, evidence_gap_flags_json=?, updated_at=?
                WHERE project_id=? AND (cod_date IS NULL OR cod_date='')
            """, (cod_year, json.dumps(flags), now, pid))
            enriched = True

    return enriched


# ── Collection ───────────────────────────────────────────────────────────────

def collect_gis_report(db: sqlite3.Connection, doc: dict,
                       existing_urls: set) -> tuple[int, int]:
    """Download, parse, and ingest one GIS report."""
    doc_id = doc['DocID']
    name = doc.get('FriendlyName', '')
    print(f'\n── ERCOT GIS: {name} (DocID={doc_id}) ──────────')

    # Download
    xlsx_path = _download_report(str(doc_id))
    if not xlsx_path:
        return 0, 0

    # Parse
    projects = _parse_gis_excel(xlsx_path)
    xlsx_path.unlink(missing_ok=True)
    print(f'  Parsed: {len(projects)} queue entries')

    # Filter for H2/CCS relevance
    relevant = [p for p in projects if _is_relevant(p)]
    print(f'  Relevant (H2/CCS/large gas): {len(relevant)}')

    inserted = skipped = 0
    now = now_iso()

    for proj in relevant:
        if _LIMIT and inserted >= _LIMIT:
            break

        doc_url = f'ercot_gis://INR_{proj["inr"]}'
        if doc_url in existing_urls:
            skipped += 1
            continue

        entity = proj['entity'] or 'Unknown'
        text = format_ercot_excerpt(proj)
        doc_date = proj['projected_cod'] or now[:10]
        technology = ERCOT_FUEL_MAP.get(proj['fuel'].upper())
        stage = _derive_stage(proj)

        if DRY_RUN:
            print(f'  [dry-run] {proj["inr"]:<15} {proj["project_name"][:35]:<35} '
                  f'{entity[:25]:<25} {proj["county"]:<12} '
                  f'{proj["capacity_mw"] or "-":>8} MW  '
                  f'fuel={proj["fuel"]:<4} stage={stage or "-"}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, facility_id, project_name,
             derived_technology, derived_stage, operator_name)
            VALUES (?,NULL,'gis_queue_entry',?,?,?,?,?,?,?,?,?,?,?)
        """, (entity, doc_date, doc_url,
              text[:50000], len(text), SOURCE_SYSTEM, now,
              proj['inr'], proj['project_name'],
              technology, stage, entity))

        existing_urls.add(doc_url)
        inserted += 1

        # Direct enrichment
        _direct_enrich(db, proj, doc_url)

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Stats ────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection):
    """Show current ERCOT GIS coverage."""
    print('\n=== ERCOT GIS COVERAGE ===')
    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Total ercot_gis records: {total}')

    distinct = db.execute(
        "SELECT COUNT(DISTINCT company_name) FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Distinct entities: {distinct}')

    by_stage = db.execute("""
        SELECT derived_stage, COUNT(*) FROM regulatory_evidence
        WHERE source_system=? GROUP BY derived_stage ORDER BY COUNT(*) DESC
    """, (SOURCE_SYSTEM,)).fetchall()
    if by_stage:
        print('  By stage:')
        for row in by_stage:
            print(f'    {row[0] or "NULL":<15} {row[1]:>5}')


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

    # Load existing URLs for dedup
    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,))}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — ERCOT GIS Queue')
    print(f'  Existing ercot_gis records: {len(existing)}')

    # List available GIS reports
    reports = _list_gis_reports()
    if not reports:
        print('  ERROR: No GIS reports found from MIS API')
        db.close()
        return

    print(f'  Available GIS reports: {len(reports)}')

    if ALL_MONTHS:
        # Process all reports
        to_process = reports
    else:
        # Just the latest
        to_process = reports[:1]

    total_ins = total_skip = 0
    for doc in to_process:
        if _LIMIT and total_ins >= _LIMIT:
            break
        ins, skip = collect_gis_report(db, doc, existing)
        total_ins += ins
        total_skip += skip
        time.sleep(SLEEP_SEC)

    print(f'\n  TOTAL: Inserted={total_ins}  Skipped={total_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
