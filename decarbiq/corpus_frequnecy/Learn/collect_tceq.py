#!/usr/bin/env python3
"""
DecarbIQ — collect_tceq.py
Fetch Texas Commission on Environmental Quality Central Registry data
for hydrogen, CCS, ammonia, and related industrial facilities.

Uses SODA API on data.texas.gov (5 regional datasets + statewide NOV).
Only collects records from 2020 onward.

Writes to regulatory_evidence with source_system='tceq'.
Also writes structured permit data to tceq_permits table.
Conservative direct enrichment for unambiguous stage/technology mappings.

Usage:
    python collect_tceq.py              # full run
    python collect_tceq.py --dry-run    # show what would be inserted
    python collect_tceq.py --stats      # current TCEQ coverage
    python collect_tceq.py --limit=100  # cap inserts
"""
from __future__ import annotations

import json
import re
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
SLEEP_SEC = 0.5
PAGE_SIZE = 1000   # SODA default max
SOURCE_SYSTEM = 'tceq'
MIN_YEAR  = 2020

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

# ── SODA datasets — all 5 TCEQ Central Registry regions ─────────────────────
SODA_DATASETS = {
    'coastal_east_tx': 'tzyg-j7q4',  # Region 10,12,14 — Gulf Coast H2 corridor (CRITICAL)
    'border_permian':  '9iad-hrn8',   # Region 7,15,16 — Permian Basin
    'central_tx':      'msah-s2rv',   # Region 11,13 — San Antonio, Austin
    'dallas_fw':       't34q-qzi3',   # Region 4,5 — DFW metro
    'north_tx':        '5eqq-7nad',   # Region 3,9 — Amarillo, Lubbock, Wichita Falls
}
NOV_DATASET = 'mwzi-gyw7'  # Notices of Violation (statewide)

SODA_BASE = 'https://data.texas.gov/resource'

# ── Relevance filters ────────────────────────────────────────────────────────
# NAICS-code-based filter — deterministic, no keyword ambiguity.
# indus_type_cd_name format: "325120 - Industrial Gas Manufacturing"
# We extract the leading NAICS code and match against known-relevant prefixes.

# NAICS prefixes for H2/CCS-relevant industries (matched left-to-right)
RELEVANT_NAICS = (
    '3241',   # Petroleum Refineries — produce/consume H2
    '3251',   # Basic Chemical Mfg — H2, ammonia, industrial gases, petrochemicals
    '486',    # Pipeline Transportation — CO2/H2 transport infrastructure
    '22121',  # Natural Gas Distribution — H2 blending
    '2379',   # Other Heavy & Civil Engineering Construction — facility construction
)

# Entity-name keywords (for facilities whose NAICS code is generic/missing
# but whose name reveals H2/CCS relevance)
ENTITY_NAME_KEYWORDS = [
    'hydrogen', 'ammonia', 'ccs', 'carbon capture', 'sequestration',
    'reforming', 'electrolysis', 'lng', 'h2',
]

TCEQ_STAGE_MAP = {
    'ACTIVE': None,        # ambiguous — entity registration active ≠ facility operational
    'ISSUED': 'permitted', # permit issued → permitted is safe
    'PENDING': None,       # could be any pre-permit stage — leave for LLM
    'IN REVIEW': None,     # same
    'EXPIRED': 'cancelled',
    'TERMINATED': 'cancelled',
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── SODA API ─────────────────────────────────────────────────────────────────

def _fetch_soda(dataset_id: str, offset: int = 0,
                where: str = '') -> list[dict]:
    """Fetch one page from a SODA dataset."""
    url = f'{SODA_BASE}/{dataset_id}.json'
    params = {
        '$limit': PAGE_SIZE,
        '$offset': offset,
        '$order': ':id',
    }
    if where:
        params['$where'] = where
    try:
        r = requests.get(url, params=params,
                         headers={'User-Agent': UA}, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f'  ERROR fetching {dataset_id} offset={offset}: {e}')
        return []


def _paginate_soda(dataset_id: str, where: str = '',
                   max_rows: int = 50000) -> list[dict]:
    """Fetch all rows from a SODA dataset with pagination."""
    all_rows: list[dict] = []
    offset = 0
    while offset < max_rows:
        page = _fetch_soda(dataset_id, offset, where)
        if not page:
            break
        all_rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        time.sleep(SLEEP_SEC)
    return all_rows


# ── Relevance filter ─────────────────────────────────────────────────────────

def _extract_naics(industry_field: str) -> str:
    """Extract NAICS code from 'NNNNNN - Description' format."""
    code = (industry_field or '').split('-')[0].strip().split(' ')[0].strip()
    # Only return if it looks like a NAICS code (digits only)
    return code if code.isdigit() else ''


def _is_relevant(row: dict) -> bool:
    """Check if TCEQ entity is industry-relevant for H2/CCS/ammonia.

    Uses NAICS code matching (deterministic) + entity name fallback.
    No keyword matching on industry descriptions — avoids false positives
    from substring collisions like 'natural gas' matching E&P wells.
    """
    industry = row.get('indus_type_cd_name') or ''
    naics = _extract_naics(industry)

    # NAICS code match — primary filter
    if naics:
        for prefix in RELEVANT_NAICS:
            if naics.startswith(prefix):
                return True

    # Entity name fallback (catches facilities with generic/missing NAICS
    # but H2/CCS-specific names like "XYZ Hydrogen Plant")
    entity_name = (row.get('reg_ent_name') or '').lower()
    for kw in ENTITY_NAME_KEYWORDS:
        if kw in entity_name:
            return True

    return False


# ── Technology/stage derivation ──────────────────────────────────────────────

def _derive_technology(row: dict) -> str | None:
    """Conservative technology mapping from NAICS code."""
    naics = _extract_naics(row.get('indus_type_cd_name') or '')
    if not naics:
        return None
    # 325120 Industrial Gas Manufacturing → hydrogen_production
    if naics.startswith('32512'):
        return 'hydrogen_production'
    # 325311 Nitrogenous Fertilizer Manufacturing → ammonia_production
    if naics.startswith('32531'):
        return 'ammonia_production'
    # All other codes are too broad for direct technology assignment
    return None


def _derive_stage(row: dict) -> str | None:
    """Conservative stage mapping from permit/entity status."""
    status = (row.get('additional_id_status') or
              row.get('reg_ent_status_txt') or '').upper().strip()
    return TCEQ_STAGE_MAP.get(status)


# ── Excerpt formatting ───────────────────────────────────────────────────────

def format_tceq_excerpt(row: dict) -> str:
    """Format TCEQ Central Registry record into structured text for LLM."""
    parts = []
    parts.append(f'TCEQ Regulated Entity: {row.get("reg_ent_name", "Unknown")}')
    parts.append(f'RN: {row.get("ref_num_txt", "")}')

    status = row.get('reg_ent_status_txt', '')
    if status:
        parts.append(f'Entity Status: {status}')

    city = row.get('re_phys_loc_city', '')
    county = row.get('re_phys_loc_addr_county', '')
    state = row.get('re_phys_loc_addr_state', 'TX')
    if city or county:
        loc_parts = []
        if city:
            loc_parts.append(city)
        if county:
            loc_parts.append(f'{county} County')
        loc_parts.append(state)
        parts.append(f'Location: {", ".join(loc_parts)}')

    addr = row.get('re_phys_loc_addr_line_1') or row.get('re_phys_loc_desc', '')
    if addr and addr != 'NO LOCATION ON FILE':
        parts.append(f'Address: {addr}')

    industry = row.get('indus_type_cd_name', '')
    if industry:
        parts.append(f'Industry: {industry}')

    program = row.get('program_code', '')
    if program:
        parts.append(f'Program: {program}')

    permit_id = row.get('additional_id_text', '')
    permit_status = row.get('additional_id_status', '')
    if permit_id:
        parts.append(f'Permit ID: {permit_id} (Status: {permit_status})')

    region = row.get('tceq_region_number', '')
    if region:
        parts.append(f'TCEQ Region: {region}')

    cn = row.get('ref_num_txt_1', '')
    if cn:
        parts.append(f'CN: {cn}')

    principal = row.get('princ_name') or row.get('princ_legal_name', '')
    if principal:
        parts.append(f'Principal: {principal}')

    begin_dt = row.get('affil_begin_dt', '')
    if begin_dt:
        parts.append(f'Affiliation Start: {begin_dt[:10]}')

    status_dt = row.get('status_dt', '')
    if status_dt:
        parts.append(f'Status Date: {status_dt[:10]}')

    return '\n'.join(parts)


def format_nov_excerpt(row: dict) -> str:
    """Format TCEQ Notice of Violation record."""
    parts = []
    parts.append(f'TCEQ Notice of Violation')
    parts.append(f'Facility: {row.get("rn_name", "Unknown")}')
    parts.append(f'RN: {row.get("rn_number", "")}')
    parts.append(f'Business Type: {row.get("business", "")}')
    parts.append(f'County: {row.get("county", "")}')
    parts.append(f'Investigation: {row.get("investigation_no", "")}')

    approved = row.get('invest_approved_dt', '')
    if approved:
        parts.append(f'Investigation Approved: {approved[:10]}')

    viol_date = row.get('violation_status_date', '')
    if viol_date:
        parts.append(f'Violation Date: {viol_date[:10]}')

    viol_cnt = row.get('viol_cnt', '')
    if viol_cnt:
        parts.append(f'Violation Count: {int(float(viol_cnt))}')

    citations = row.get('c_viol_citations', '')
    if citations:
        parts.append(f'Citations: {citations}')

    return '\n'.join(parts)


# ── Company name matching ────────────────────────────────────────────────────

def _match_company(db: sqlite3.Connection, entity_name: str) -> str | None:
    """Try exact → normalized → prefix match against companies table.
    Returns company_id or None."""
    cn = (entity_name or '').strip()
    if not cn:
        return None
    cn_upper = cn.upper()

    # Exact match
    co = db.execute(
        "SELECT company_id FROM companies WHERE UPPER(company_name)=?",
        (cn_upper,)).fetchone()
    if co:
        return co['company_id']

    # Normalized (strip LLC, LP, Inc, Corp, etc.)
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

    # Prefix match: "Air Liquide Large Industries U.S. LP" → "Air Liquide"
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

def _direct_enrich(db: sqlite3.Connection, entity_name: str,
                   stage: str | None, technology: str | None,
                   source_url: str, evidence_date: str | None) -> bool:
    """Match entity → companies → unified_projects, apply stage/technology."""
    from bootstrap_projects import apply_stage_update

    if not stage and not technology:
        return False

    company_id = _match_company(db, entity_name)
    if not company_id:
        return False

    projects = db.execute(
        "SELECT project_id, stage, technology FROM unified_projects WHERE company_id=?",
        (company_id,)).fetchall()

    enriched = False
    now = now_iso()

    for proj in projects:
        pid = proj['project_id']

        if stage:
            updated = apply_stage_update(
                db, pid, stage, evidence_date,
                f'tceq_enrichment: {source_url}',
                confidence=0.70)
            if updated:
                enriched = True

        if technology and not (proj['technology'] or '').strip():
            db.execute("""
                UPDATE unified_projects SET technology=?, updated_at=?
                WHERE project_id=? AND (technology IS NULL OR technology='')
            """, (technology, now, pid))
            enriched = True

    return enriched


# ── Table creation ───────────────────────────────────────────────────────────

def _ensure_tables(db: sqlite3.Connection):
    """Create tceq_permits table if it doesn't exist."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS tceq_permits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rn_number TEXT NOT NULL,
            cn_number TEXT,
            entity_name TEXT,
            city TEXT,
            county TEXT,
            industry_type TEXT,
            program_code TEXT,
            permit_number TEXT,
            permit_status TEXT,
            region_number TEXT,
            status_date TEXT,
            collected_at TEXT,
            UNIQUE(rn_number, program_code, permit_number)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_tp_rn ON tceq_permits(rn_number)
    """)
    db.commit()


# ── Collection functions ─────────────────────────────────────────────────────

def collect_registry(db: sqlite3.Connection,
                     existing_urls: set) -> tuple[int, int]:
    """Collect from all 5 TCEQ Central Registry SODA datasets."""
    total_inserted = total_skipped = total_filtered = 0
    now = now_iso()

    # Build SODA WHERE clause using NAICS code prefixes (deterministic).
    # indus_type_cd_name starts with the NAICS code, so LIKE 'NNNN%' works.
    _soda_naics_clauses = ' OR '.join(
        f"indus_type_cd_name LIKE '{prefix}%'"
        for prefix in RELEVANT_NAICS)
    _soda_entity_clauses = ' OR '.join(
        f"UPPER(reg_ent_name) LIKE '%{kw.upper()}%'"
        for kw in ENTITY_NAME_KEYWORDS)
    _soda_filter = (f"status_dt >= '2020-01-01T00:00:00' AND "
                    f"({_soda_naics_clauses} OR {_soda_entity_clauses})")

    for region_name, dataset_id in SODA_DATASETS.items():
        print(f'\n── TCEQ Registry: {region_name} ({dataset_id}) ──────────')

        rows = _paginate_soda(dataset_id, where=_soda_filter)
        print(f'  Fetched: {len(rows)} relevant records (2020+)')

        inserted = skipped = filtered = 0
        for row in rows:
            if _LIMIT and (total_inserted + inserted) >= _LIMIT:
                break

            # Relevance filter
            if not _is_relevant(row):
                filtered += 1
                continue

            rn = row.get('ref_num_txt', '')
            program = row.get('program_code', '')
            permit_id = row.get('additional_id_text', '')
            doc_url = f'tceq://RN{rn}/{program}/{permit_id}'

            if doc_url in existing_urls:
                skipped += 1
                continue

            entity_name = row.get('reg_ent_name') or 'Unknown'
            text = format_tceq_excerpt(row)

            # Document date from status_dt or affil_begin_dt
            status_dt = row.get('status_dt', '')
            doc_date = status_dt[:10] if status_dt else now[:10]

            technology = _derive_technology(row)
            stage = _derive_stage(row)

            if DRY_RUN:
                city = row.get('re_phys_loc_city', '')
                county = row.get('re_phys_loc_addr_county', '')
                print(f'  [dry-run] {entity_name[:45]:<45} {city:<15} '
                      f'{county:<12} {program:<10} '
                      f'stage={stage or "-":<10} tech={technology or "-"}')
                inserted += 1
                existing_urls.add(doc_url)
                continue

            # Insert into regulatory_evidence
            db.execute("""
                INSERT INTO regulatory_evidence
                (company_name, company_cik, document_type, document_date,
                 document_url, raw_text_excerpt, excerpt_char_count,
                 source_system, ingested_at, facility_id, permit_id,
                 derived_technology, derived_stage)
                VALUES (?,NULL,?,?,?,?,?,?,?,?,?,?,?)
            """, (entity_name, f'tceq_registry_{program.lower()}' if program else 'tceq_registry',
                  doc_date, doc_url, text[:50000], len(text),
                  SOURCE_SYSTEM, now,
                  rn or None, permit_id or None,
                  technology, stage))

            # Insert into tceq_permits
            try:
                db.execute("""
                    INSERT OR IGNORE INTO tceq_permits
                    (rn_number, cn_number, entity_name, city, county,
                     industry_type, program_code, permit_number,
                     permit_status, region_number, status_date, collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (rn, row.get('ref_num_txt_1', ''),
                      entity_name,
                      row.get('re_phys_loc_city', ''),
                      row.get('re_phys_loc_addr_county', ''),
                      row.get('indus_type_cd_name', ''),
                      program, permit_id,
                      row.get('additional_id_status', ''),
                      row.get('tceq_region_number', ''),
                      doc_date, now))
            except sqlite3.IntegrityError:
                pass

            existing_urls.add(doc_url)
            inserted += 1

            # Direct enrichment (conservative)
            if stage or technology:
                _direct_enrich(db, entity_name, stage, technology,
                               doc_url, doc_date)

        if not DRY_RUN:
            db.commit()

        print(f'  Inserted: {inserted}  Skipped: {skipped}  '
              f'Filtered (irrelevant): {filtered}')
        total_inserted += inserted
        total_skipped += skipped
        total_filtered += filtered

    print(f'\n  Registry total: Inserted={total_inserted}  '
          f'Skipped={total_skipped}  Filtered={total_filtered}')
    return total_inserted, total_skipped


def collect_violations(db: sqlite3.Connection,
                       existing_urls: set) -> tuple[int, int]:
    """Collect TCEQ Notices of Violation (statewide)."""
    print(f'\n── TCEQ Notices of Violation ({NOV_DATASET}) ──────────')

    where = f"invest_approved_dt >= '2020-01-01T00:00:00'"
    rows = _paginate_soda(NOV_DATASET, where=where)
    print(f'  Fetched: {len(rows)} violations (2020+)')

    inserted = skipped = 0
    now = now_iso()

    # We only care about violations at facilities we've already collected
    known_rns = {r[0] for r in db.execute(
        "SELECT DISTINCT facility_id FROM regulatory_evidence "
        "WHERE source_system=? AND facility_id IS NOT NULL", (SOURCE_SYSTEM,))}

    for row in rows:
        if _LIMIT and inserted >= _LIMIT:
            break

        rn = row.get('rn_number', '')
        if not rn:
            continue

        # Only collect violations for entities we already track
        if rn not in known_rns:
            skipped += 1
            continue

        inv_no = row.get('investigation_no', '')
        doc_url = f'tceq://NOV/{rn}/{inv_no}'

        if doc_url in existing_urls:
            skipped += 1
            continue

        entity_name = row.get('rn_name') or 'Unknown'
        text = format_nov_excerpt(row)

        approved_dt = row.get('invest_approved_dt', '')
        doc_date = approved_dt[:10] if approved_dt else now[:10]

        if DRY_RUN:
            county = row.get('county', '')
            viol_cnt = row.get('viol_cnt', '0')
            print(f'  [dry-run] NOV {entity_name[:40]:<40} {county:<12} '
                  f'violations={int(float(viol_cnt))}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, facility_id)
            VALUES (?,NULL,'tceq_violation',?,?,?,?,?,?,?)
        """, (entity_name, doc_date, doc_url,
              text[:50000], len(text), SOURCE_SYSTEM, now, rn))

        existing_urls.add(doc_url)
        inserted += 1

    if not DRY_RUN:
        db.commit()

    print(f'  NOV Inserted: {inserted}  Skipped: {skipped}')
    return inserted, skipped


# ── Stats ────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection):
    """Show current TCEQ coverage."""
    print('\n=== TCEQ COVERAGE ===')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Total tceq records: {total}')

    by_type = db.execute("""
        SELECT document_type, COUNT(*) as cnt
        FROM regulatory_evidence WHERE source_system=?
        GROUP BY document_type ORDER BY cnt DESC
    """, (SOURCE_SYSTEM,)).fetchall()
    for row in by_type:
        print(f'    {row["document_type"]:<30} {row["cnt"]:>5}')

    permits = db.execute(
        "SELECT COUNT(*) FROM tceq_permits").fetchone()[0]
    print(f'\n  tceq_permits table: {permits} rows')

    distinct_entities = db.execute(
        "SELECT COUNT(DISTINCT company_name) FROM regulatory_evidence "
        "WHERE source_system=?", (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Distinct entities: {distinct_entities}')

    # Show breakdown by derived fields
    with_tech = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence "
        "WHERE source_system=? AND derived_technology IS NOT NULL",
        (SOURCE_SYSTEM,)).fetchone()[0]
    with_stage = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence "
        "WHERE source_system=? AND derived_stage IS NOT NULL",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  With derived_technology: {with_tech}')
    print(f'  With derived_stage: {with_stage}')


# ── Entry point ──────────────────────────────────────────────────────────────

def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found')
        sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    _ensure_tables(db)

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    # Load existing URLs for dedup
    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,))}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — TCEQ Central Registry')
    print(f'  Existing tceq records: {len(existing)}')

    reg_ins, reg_skip = collect_registry(db, existing)
    nov_ins, nov_skip = collect_violations(db, existing)

    print(f'\n  TOTAL: Inserted={reg_ins + nov_ins}  '
          f'Skipped={reg_skip + nov_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
