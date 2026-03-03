#!/usr/bin/env python3
"""
DecarbIQ — collect_rrc_uic.py
Fetch TX Railroad Commission UIC CO2 injection well data from SODA API
on data.texas.gov + H-10 injection monitoring records.

Two SODA datasets:
  - givw-z9t4: UIC Well Location (~1,260 CO2 wells of 126K total)
  - qq2j-f2zm: H-10 Injection Monitoring (~20K post-2020 CO2 records)

All Type 5 (CO2 injection) and Type 6 (geologic sequestration) wells
are 100% CCS-relevant — no keyword/NAICS filtering needed.

Writes to regulatory_evidence with source_system='rrc_uic'.
Also writes structured well data to rrc_uic_wells table.
Conservative direct enrichment for stage/technology.

Usage:
    python collect_rrc_uic.py              # full run
    python collect_rrc_uic.py --dry-run    # show what would be inserted
    python collect_rrc_uic.py --stats      # current RRC UIC coverage
    python collect_rrc_uic.py --limit=50   # cap inserts
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA        = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC = 0.5
PAGE_SIZE = 1000   # SODA default max
SOURCE_SYSTEM = 'rrc_uic'

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

# ── SODA datasets ────────────────────────────────────────────────────────────
SODA_BASE     = 'https://data.texas.gov/resource'
WELL_DATASET  = 'givw-z9t4'   # UIC Well Location
H10_DATASET   = 'qq2j-f2zm'   # H-10 Injection Monitoring

# UIC Type 5 = CO2 injection, Type 6 = geologic sequestration
WELL_FILTER = "uic_type_injection IN ('5','6')"
H10_FILTER  = "type_uic IN ('5','6') AND formatted_date > '2020-01-01T00:00:00'"

# ── Operator name seed ───────────────────────────────────────────────────────
# Populated from first --dry-run output + manual RRC website lookup.
# operator_number → company_name for known CCS operators.
SEED_OPERATORS: dict[str, str] = {
    # Populated from --dry-run output + texas-drilling.com (current RRC P-5 data).
    # Top 20 operators covering ~85% of 1,260 CO2 wells.
    '36599':  'Atmos Pipeline - Texas',                   # 62 wells, D05, Tri-Cities Gas Storage
    '100240': 'Energy Transfer Spindletop LLC',            # 10 wells, D03, GCCI LLC lease
    '100323': 'ConocoPhillips',                            # 8 wells, D03, Mt. Belvieu Storage
    '101477': 'Trinity Gas Storage Inc',                   # 20 wells, D06, Trinity Gas Storage
    '101829': 'Dow Infraco, LLC',                          # 30 wells, D03, Stevens Tract / Dow Fee
    '148106': 'Chevron Phillips Chemical Co LP',           # 30 wells, D03, Clemens Salt Dome
    '227525': 'Dow Chemical Company',                      # 21 wells, D03, Stevens Tract / Dow Fee
    '252017': 'Energy Transfer Company',                   # 22 wells, D09, Callie-Parker / Fields
    '252142': 'Energy Transfer Mt Belv NGLs LP',           # 47 wells, D03, Lone Star NGL Mont Belvieu
    '253316': 'Equistar Chemicals, LP',                    # 16 wells, D03, LyondellBasell subsidiary
    '253368': 'Enterprise Products Operating LLC',         # 26 wells, D03, Almeda Underground Strg
    '388445': 'Hill-Lake Gas Storage, LLC',                # 25 wells, D7B, Williams/NorTex subsidiary
    '404520': 'Houston Pipe Line Company LP',              # 16 wells, D03, Bammel gas storage (ET)
    '576980': 'Mont Belvieu Caverns, LLC',                 # 61 wells, D03, East/Central Storage Terminal
    '623855': 'ONEOK Texas Gas Storage, L.L.C.',           # 27 wells, D8A, Loop/Felmac GSU
    '661634': 'Phillips 66 Company',                       # 25 wells, D10, Borger Underground Strg
    '665753': 'Pinto Energy Partners, L.P.',               # 35 wells, D03, Stratton Ridge Energy Hub
    '836027': 'Targa Downstream LLC',                      # 48 wells, D03, Targa Resources subsidiary
    '875426': 'U.S. Department of Energy',                 # 76 wells, D03, SPR Bryan Mound/Big Hill
    '942345': 'Worsham-Steed Gas Storage, LLC',            # 44 wells, D09, Williams/NorTex subsidiary
    # Op=600971 (80 wells, D06) — unresolved, not on texas-drilling.com
    # Op=828880 (20 wells, D05, Pickton) — unresolved
    # Op=623785 (19 wells, D03) — unresolved
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


# ── Operator resolution ──────────────────────────────────────────────────────

_has_lat_lon: bool | None = None  # cached schema check

def _find_nearby_project(db: sqlite3.Connection,
                         lat: float, lon: float,
                         radius_miles: float = 5) -> str | None:
    """Bounding-box proximity match against unified_projects.

    Returns developer_name or None.
    Silently returns None if unified_projects lacks lat/lon columns.
    """
    global _has_lat_lon
    if _has_lat_lon is None:
        cols = {r[1] for r in db.execute("PRAGMA table_info(unified_projects)")}
        _has_lat_lon = 'latitude' in cols and 'longitude' in cols
    if not _has_lat_lon:
        return None

    deg = radius_miles * 0.015  # ~0.015 degrees per mile at TX latitudes
    row = db.execute("""
        SELECT developer_name FROM unified_projects
        WHERE latitude BETWEEN ? AND ?
          AND longitude BETWEEN ? AND ?
          AND developer_name IS NOT NULL
        LIMIT 1
    """, (lat - deg, lat + deg, lon - deg, lon + deg)).fetchone()
    return row['developer_name'] if row else None


def _resolve_operator(db: sqlite3.Connection, well: dict) -> str:
    """3-tier operator name resolution."""
    op_num = str(well.get('operator_number') or '').strip()

    # Tier 1: hardcoded seed lookup
    if op_num in SEED_OPERATORS:
        return SEED_OPERATORS[op_num]

    # Tier 2: spatial cross-reference (low hit rate expected)
    lat = well.get('latitude_nad83')
    lon = well.get('longitude_nad83')
    if lat and lon:
        try:
            match = _find_nearby_project(db, float(lat), float(lon))
            if match:
                return match
        except (ValueError, TypeError):
            pass

    # Tier 3: placeholder — LLM pipeline handles entity resolution
    return f"RRC Operator {op_num}" if op_num else "Unknown RRC Operator"


# ── Stage derivation ─────────────────────────────────────────────────────────

def _derive_stage(well: dict, latest_h10: dict | None) -> str | None:
    """Derive project stage from well activation + injection activity."""
    activated = str(well.get('activated_flag') or '').lower()
    if activated in ('false', '0', 'n', 'no'):
        return 'cancelled'

    # Active injection in last 12 months → operational
    twelve_months_ago = (datetime.now(timezone.utc) - timedelta(days=365)).strftime('%Y-%m-%d')
    if latest_h10:
        try:
            vol = float(latest_h10.get('vol_liq') or 0) + float(latest_h10.get('vol_gas') or 0)
        except (ValueError, TypeError):
            vol = 0
        date = (latest_h10.get('formatted_date') or '')[:10]
        if vol > 0 and date >= twelve_months_ago:
            return 'operational'

    # Has permit date but no recent injection → permitted
    if well.get('w14_date'):
        return 'permitted'

    return None


# ── Excerpt formatting ───────────────────────────────────────────────────────

def format_well_excerpt(well: dict, operator_name: str,
                        latest_h10: dict | None) -> str:
    """Format RRC UIC CO2 well into structured text for LLM."""
    uic_type = well.get('uic_type_injection', '')
    type_label = 'Geologic Sequestration (6)' if str(uic_type) == '6' else 'CO2 Injection (5)'

    parts = [
        f"RRC UIC CO2 Well: {well.get('uic_number', '')}",
        f"Operator: {operator_name} (RRC #{well.get('operator_number', '')})",
        f"Lease: {well.get('lease_name', '')} | API: {well.get('api_no', '')}",
        f"Type: {type_label}",
    ]

    district = well.get('district_code', '')
    lat = well.get('latitude_nad83', '')
    lon = well.get('longitude_nad83', '')
    if district or (lat and lon):
        loc_parts = []
        if district:
            loc_parts.append(f"District: {district}")
        if lat and lon:
            loc_parts.append(f"Location: {lat}, {lon}")
        parts.append(' | '.join(loc_parts))

    top_zone = well.get('top_inj_zone', '')
    bot_zone = well.get('bot_inj_zone', '')
    if top_zone or bot_zone:
        parts.append(f"Injection Zone: {top_zone} - {bot_zone} ft")

    max_liq = well.get('max_liq_inj_pressure', '')
    max_gas = well.get('max_gas_inj_pressure', '')
    if max_liq or max_gas:
        pressure_parts = []
        if max_liq:
            pressure_parts.append(f"{max_liq} psi (liquid)")
        if max_gas:
            pressure_parts.append(f"{max_gas} psi (gas)")
        parts.append(f"Max Pressure: {' / '.join(pressure_parts)}")

    permit_date = well.get('w14_date', '')
    if permit_date:
        parts.append(f"Permit Date: {permit_date[:10]}")

    activated = str(well.get('activated_flag') or '').lower()
    status = 'Active' if activated not in ('false', '0', 'n', 'no') else 'Inactive'
    parts.append(f"Status: {status}")

    if latest_h10:
        date = (latest_h10.get('formatted_date') or '')[:10]
        vol_liq = latest_h10.get('vol_liq', '0')
        vol_gas = latest_h10.get('vol_gas', '0')
        avg_press = latest_h10.get('inj_press_avg', '')
        parts.append(
            f"H-10 Latest ({date}): Liquid {vol_liq} BBL | "
            f"Gas {vol_gas} MCF | Avg Pressure {avg_press} psi"
        )

    return '\n'.join(parts)


# ── Company name matching (reused from collect_tceq.py pattern) ──────────────

def _match_company(db: sqlite3.Connection, name: str) -> str | None:
    """Try exact → normalized → prefix match against companies table."""
    cn = (name or '').strip()
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

def _direct_enrich(db: sqlite3.Connection, operator_name: str,
                   stage: str | None, source_url: str,
                   evidence_date: str | None) -> bool:
    """Match operator → companies → unified_projects, apply stage/technology."""
    from bootstrap_projects import apply_stage_update

    company_id = _match_company(db, operator_name)
    if not company_id:
        return False

    projects = db.execute(
        "SELECT project_id, stage, technology FROM unified_projects WHERE company_id=?",
        (company_id,)).fetchall()

    enriched = False
    now = now_iso()
    confidence_map = {'operational': 0.85, 'permitted': 0.70}

    for proj in projects:
        pid = proj['project_id']

        if stage:
            updated = apply_stage_update(
                db, pid, stage, evidence_date,
                f'rrc_uic_enrichment: {source_url}',
                confidence=confidence_map.get(stage, 0.60))
            if updated:
                enriched = True

        # Technology: always co2_transport_storage for CO2 wells
        if not (proj['technology'] or '').strip():
            db.execute("""
                UPDATE unified_projects SET technology=?, updated_at=?
                WHERE project_id=? AND (technology IS NULL OR technology='')
            """, ('co2_transport_storage', now, pid))
            enriched = True

    return enriched


# ── Collection ───────────────────────────────────────────────────────────────

def collect_wells(db: sqlite3.Connection, existing_urls: set) -> int:
    """Phase A+B: Fetch CO2 wells + merge latest H-10 monitoring data."""

    # ── Phase B prep: batch-fetch all H-10 records first ──
    print('\n── Phase B: Fetching H-10 injection monitoring ──────────')
    h10_rows = _paginate_soda(H10_DATASET, where=H10_FILTER)
    print(f'  Fetched: {len(h10_rows)} H-10 records (Type 5/6, post-2020)')

    # Index by uic_no — keep only the latest record per well
    h10_by_well: dict[str, dict] = {}
    for h in h10_rows:
        uic = str(h.get('uic_no') or '').strip()
        if not uic:
            continue
        existing = h10_by_well.get(uic)
        if existing is None:
            h10_by_well[uic] = h
        else:
            new_date = (h.get('formatted_date') or '')[:10]
            old_date = (existing.get('formatted_date') or '')[:10]
            if new_date > old_date:
                h10_by_well[uic] = h

    print(f'  Wells with H-10 data: {len(h10_by_well)}')

    # ── Phase A: Fetch all CO2 wells ──
    print(f'\n── Phase A: Fetching UIC CO2 wells ──────────')
    wells = _paginate_soda(WELL_DATASET, where=WELL_FILTER)
    print(f'  Fetched: {len(wells)} CO2 wells (Type 5/6)')

    inserted = skipped = 0
    now = now_iso()

    # Track operator_number frequency for dry-run analysis
    op_counts: dict[str, int] = {}

    for well in wells:
        if _LIMIT and inserted >= _LIMIT:
            break

        uic = str(well.get('uic_number') or '').strip()
        if not uic:
            continue

        doc_url = f'rrc_uic://well_{uic}'
        if doc_url in existing_urls:
            skipped += 1
            continue

        # Resolve operator name
        operator_name = _resolve_operator(db, well)
        latest_h10 = h10_by_well.get(uic)
        stage = _derive_stage(well, latest_h10)

        # Track operator frequency
        op_num = str(well.get('operator_number') or '').strip()
        op_counts[op_num] = op_counts.get(op_num, 0) + 1

        # Permit date as document_date
        permit_date = (well.get('w14_date') or '')[:10]
        doc_date = permit_date if permit_date else now[:10]

        text = format_well_excerpt(well, operator_name, latest_h10)

        if DRY_RUN:
            lease = (well.get('lease_name') or '')[:30]
            uic_type = well.get('uic_type_injection', '')
            h10_info = ''
            if latest_h10:
                h10_date = (latest_h10.get('formatted_date') or '')[:10]
                h10_info = f'H10={h10_date}'
            print(f'  [dry-run] UIC={uic:<8} Type={uic_type} '
                  f'Op={op_num:<8} {operator_name[:30]:<30} '
                  f'Lease={lease:<30} '
                  f'stage={stage or "-":<10} {h10_info}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        # Insert into regulatory_evidence
        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, facility_id,
             derived_technology, derived_stage)
            VALUES (?,NULL,'rrc_uic_well',?,?,?,?,?,?,?,?,?)
        """, (operator_name, doc_date, doc_url,
              text[:50000], len(text), SOURCE_SYSTEM, now,
              uic, 'co2_transport_storage', stage))

        # Insert into rrc_uic_wells
        activated_flag = str(well.get('activated_flag') or '').lower()
        activated_int = 0 if activated_flag in ('false', '0', 'n', 'no') else 1

        try:
            db.execute("""
                INSERT OR REPLACE INTO rrc_uic_wells
                (uic_number, api_number, well_type, operator_number,
                 operator_name, lease_name, district_code,
                 latitude, longitude,
                 top_injection_zone, bottom_injection_zone,
                 max_liquid_pressure, max_gas_pressure,
                 permit_date, activated,
                 latest_injection_date, latest_injection_volume_liq,
                 latest_injection_volume_gas, latest_injection_pressure,
                 collected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                uic,
                well.get('api_no'),
                str(well.get('uic_type_injection', '')),
                op_num or None,
                operator_name,
                well.get('lease_name'),
                well.get('district_code'),
                _safe_float(well.get('latitude_nad83')),
                _safe_float(well.get('longitude_nad83')),
                _safe_float(well.get('top_inj_zone')),
                _safe_float(well.get('bot_inj_zone')),
                _safe_float(well.get('max_liq_inj_pressure')),
                _safe_float(well.get('max_gas_inj_pressure')),
                permit_date or None,
                activated_int,
                (latest_h10.get('formatted_date') or '')[:10] if latest_h10 else None,
                _safe_float(latest_h10.get('vol_liq')) if latest_h10 else None,
                _safe_float(latest_h10.get('vol_gas')) if latest_h10 else None,
                _safe_float(latest_h10.get('inj_press_avg')) if latest_h10 else None,
                now,
            ))
        except sqlite3.IntegrityError:
            pass

        existing_urls.add(doc_url)
        inserted += 1

        # Direct enrichment
        if stage:
            _direct_enrich(db, operator_name, stage, doc_url, doc_date)

    if not DRY_RUN:
        db.commit()

    print(f'\n  Wells: Inserted={inserted}  Skipped={skipped}')

    # Show operator frequency for SEED_OPERATORS population
    if DRY_RUN and op_counts:
        print(f'\n  ── Operator frequency (top 20 for SEED_OPERATORS) ──')
        sorted_ops = sorted(op_counts.items(), key=lambda x: -x[1])
        for op_num, count in sorted_ops[:20]:
            resolved = SEED_OPERATORS.get(op_num, '(unresolved)')
            print(f'    operator_number={op_num:<10} wells={count:>4}  → {resolved}')
        total_resolved = sum(
            count for op, count in op_counts.items() if op in SEED_OPERATORS)
        print(f'\n    Resolved by SEED: {total_resolved}/{sum(op_counts.values())} '
              f'({total_resolved/max(sum(op_counts.values()),1):.0%})')

    return inserted


def _safe_float(val) -> float | None:
    """Convert to float or None."""
    if val is None or val == '':
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ── Stats ────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection):
    """Show current RRC UIC coverage."""
    print('\n=== RRC UIC COVERAGE ===')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Total rrc_uic records: {total}')

    # Well type breakdown
    try:
        by_type = db.execute("""
            SELECT well_type, COUNT(*) cnt, SUM(activated) active
            FROM rrc_uic_wells GROUP BY well_type
        """).fetchall()
        for row in by_type:
            print(f'    Type {row["well_type"]}: {row["cnt"]} wells '
                  f'({row["active"]} active)')
    except sqlite3.OperationalError:
        print('  (rrc_uic_wells table not yet created)')

    # Operator resolution stats
    try:
        stats = db.execute("""
            SELECT
              COUNT(*) total,
              SUM(CASE WHEN operator_name NOT LIKE 'RRC Operator%'
                        AND operator_name != 'Unknown RRC Operator'
                  THEN 1 ELSE 0 END) resolved,
              SUM(CASE WHEN operator_name LIKE 'RRC Operator%'
                        OR operator_name = 'Unknown RRC Operator'
                  THEN 1 ELSE 0 END) unresolved
            FROM rrc_uic_wells
        """).fetchone()
        if stats and stats['total']:
            print(f'\n  Operator resolution: {stats["resolved"]}/{stats["total"]} resolved '
                  f'({stats["resolved"]/stats["total"]:.0%})')
    except sqlite3.OperationalError:
        pass

    # Stage/technology breakdown
    with_stage = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence "
        "WHERE source_system=? AND derived_stage IS NOT NULL",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'\n  With derived_stage: {with_stage}')

    by_stage = db.execute("""
        SELECT derived_stage, COUNT(*) cnt FROM regulatory_evidence
        WHERE source_system=? AND derived_stage IS NOT NULL
        GROUP BY derived_stage ORDER BY cnt DESC
    """, (SOURCE_SYSTEM,)).fetchall()
    for row in by_stage:
        print(f'    {row["derived_stage"]:<15} {row["cnt"]:>5}')


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

    # Ensure rrc_uic_wells table exists
    db.executescript("""
        CREATE TABLE IF NOT EXISTS rrc_uic_wells (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uic_number TEXT NOT NULL,
            api_number TEXT,
            well_type TEXT,
            operator_number TEXT,
            operator_name TEXT,
            lease_name TEXT,
            district_code TEXT,
            latitude REAL,
            longitude REAL,
            top_injection_zone REAL,
            bottom_injection_zone REAL,
            max_liquid_pressure REAL,
            max_gas_pressure REAL,
            permit_date TEXT,
            activated INTEGER,
            latest_injection_date TEXT,
            latest_injection_volume_liq REAL,
            latest_injection_volume_gas REAL,
            latest_injection_pressure REAL,
            collected_at TEXT,
            UNIQUE(uic_number)
        );
        CREATE INDEX IF NOT EXISTS idx_ruw_operator ON rrc_uic_wells(operator_number);
        CREATE INDEX IF NOT EXISTS idx_ruw_type ON rrc_uic_wells(well_type);
    """)

    # Load existing URLs for dedup
    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,))}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — RRC UIC CO2 Wells')
    print(f'  Existing rrc_uic records: {len(existing)}')

    total = collect_wells(db, existing)

    print(f'\n  TOTAL: Inserted={total}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
