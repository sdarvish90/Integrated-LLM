#!/usr/bin/env python3
"""
DecarbIQ — collect_grid.py
Ingest grid pipeline outputs (Pipeline 1 & 2) into regulatory_evidence.

Reads JSON/CSV outputs from the grid/ directory and inserts into
core_database.db. Also performs direct enrichment of unified_projects
for sources with structured stage/technology data (EIA-860M, PUC SB6,
ERCOT large load queue).

Usage:
    python collect_grid.py              # ingest all grid outputs
    python collect_grid.py --dry-run    # show what would be inserted
    python collect_grid.py --stats      # current grid coverage
    python collect_grid.py --eia-only   # only EIA-860M CSV files
"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT       = Path(__file__).resolve().parent.parent          # corpus_frequnecy/
CORE_DB     = _ROOT / 'data' / 'core_database.db'
GRID_DIR    = _ROOT / 'grid'
P1_OUTPUTS  = GRID_DIR / 'pipeline_1_large_load_queue' / 'outputs'
P2_OUTPUTS  = GRID_DIR / 'pipeline_2_distribution_interconnections' / 'outputs'

# EIA CSVs live at grid root level
EIA_CSV_PATHS = sorted(GRID_DIR.glob('eia860m_*.csv'))

DRY_RUN    = '--dry-run'   in sys.argv
STATS_ONLY = '--stats'     in sys.argv
EIA_ONLY   = '--eia-only'  in sys.argv

# ── Source mapping ───────────────────────────────────────────────────────────
# Maps ScrapeResult.source → DB source_system
_SOURCE_MAP = {
    # Pipeline 1
    'ercot_monthly_overview': 'ercot', 'ercot_board': 'ercot',
    'ercot_rpg': 'ercot', 'ercot_large_load_page': 'ercot',
    'ercot_ll_version_track': 'ercot', 'ercot_standalone_gen': 'ercot',
    'ercot_market_notice': 'ercot', 'ercot_planning_guide': 'ercot',
    'ercot_constraints_report': 'ercot',
    'puc_sb6': 'puc_texas', 'puc_ccn_search': 'puc_texas',
    'sec_edgar': 'sec_grid',
    # Pipeline 2
    'oncor_earnings': 'tsp_earnings', 'centerpoint_earnings': 'tsp_earnings',
    'aep_earnings': 'tsp_earnings',
    'eia_860m': 'eia_860m', 'eia_860_annual': 'eia_860m',
    'interconnection_fyi': 'interconnection_fyi',
    'interconnection_fyi_dc': 'interconnection_fyi',
}

# ERCOT queue-specific sources (applicant-level, not macro reports)
_ERCOT_QUEUE_SOURCES = {'ercot_large_load_page', 'ercot_ll_version_track'}

# Pipeline 1 minimum relevance score for ingestion
_P1_MIN_RELEVANCE = 0.15

# ── EIA-860M status code → project stage ─────────────────────────────────────
EIA_STATUS_TO_STAGE = {
    'P':  'announced',     # Planned
    'L':  'permitted',     # Regulatory approvals pending
    'T':  'construction',  # Under construction
    'V':  'construction',  # Under construction/testing
    'TS': 'operational',   # Construction complete, not yet commercial
    'U':  'operational',   # Operating
    'SB': 'on_hold',       # Standby
    'OA': 'cancelled',     # Out of service (awaiting decommission)
    'OS': 'cancelled',     # Out of service (indefinitely)
}

# H2-relevant energy source codes → technology
EIA_TECH_MAP = {
    'NG':  'natural_gas',
    'OG':  'other_gas',
    'SUN': 'solar',
    'WND': 'wind',
    'MWH': 'battery_storage',
    'WAT': 'hydroelectric',
    'NUC': 'nuclear',
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_source_system(source: str) -> str | None:
    """Map ScrapeResult.source field → DB source_system."""
    sys_name = _SOURCE_MAP.get(source)
    if sys_name is None and source.endswith('_dg_page'):
        sys_name = 'tsp_earnings'
    if sys_name is None:
        print(f'  SKIP unknown grid source: {source}')
    return sys_name


def _flatten_metadata(meta: dict, prefix: str = '') -> list[str]:
    """Flatten nested metadata dict into 'key: value' lines."""
    lines = []
    for k, v in meta.items():
        key = k if not prefix else f'{prefix} > {k}'
        if isinstance(v, dict):
            lines.extend(_flatten_metadata(v, key))
        elif v is not None:
            lines.append(f'{key}: {v}')
    return lines


def _extract_company(item: dict) -> str:
    """Extract company_name from a ScrapeResult dict based on source."""
    source = item.get('source', '')
    meta = item.get('metadata', {})

    # PUC filings → filer
    if source in ('puc_sb6', 'puc_ccn_search'):
        return (meta.get('filer') or 'PUC Texas').strip()

    # SEC filings → company name from metadata
    if source == 'sec_edgar':
        return (meta.get('company') or 'Unknown TSP').strip()

    # TSP earnings → TSP name
    if source in ('oncor_earnings', 'centerpoint_earnings', 'aep_earnings') \
       or source.endswith('_dg_page'):
        return (meta.get('tsp') or source.split('_')[0].title()).strip()

    # Interconnection.fyi → ERCOT or entity
    if source.startswith('interconnection_fyi'):
        return 'ERCOT'

    # EIA sources
    if source.startswith('eia_'):
        return (meta.get('entity_name') or 'Unknown').strip()

    # ERCOT queue sources → applicant from metadata only (conservative)
    if source in _ERCOT_QUEUE_SOURCES:
        applicant = meta.get('applicant') or meta.get('company') or ''
        if applicant:
            return applicant.strip()
        # Fall through to default 'ERCOT'

    # Default for all ERCOT reports
    return 'ERCOT'


def _doc_type_from_source(source: str) -> str:
    """Map ScrapeResult.source → document_type for regulatory_evidence."""
    if source in ('puc_sb6', 'puc_ccn_search'):
        return 'ccn_application'
    if source == 'sec_edgar':
        return 'sec_grid_filing'
    if source in ('oncor_earnings', 'centerpoint_earnings', 'aep_earnings'):
        return 'earnings_data'
    if source.endswith('_dg_page'):
        return 'dg_page'
    if source.startswith('interconnection_fyi'):
        return 'queue_data'
    if source.startswith('eia_'):
        return 'planned_generator'
    if source in _ERCOT_QUEUE_SOURCES:
        return 'large_load_queue_entry'
    return 'grid_constraints_report'


# ── Direct enrichment ────────────────────────────────────────────────────────

def _direct_enrich(db: sqlite3.Connection, company_name: str,
                   stage: str | None, technology: str | None,
                   source_url: str, evidence_date: str | None,
                   capacity: str | None = None) -> bool:
    """Match company_name → unified_projects and apply stage/technology/capacity.

    Uses apply_stage_update() from bootstrap_projects for temporal + transition
    rule enforcement. Returns True if any project was enriched.
    """
    from bootstrap_projects import apply_stage_update

    if not stage and not technology and not capacity:
        return False

    cn = (company_name or '').strip()
    if not cn:
        return False

    # Resolve company_name → company_id (exact or normalized match)
    cn_upper = cn.upper()
    co = db.execute(
        "SELECT company_id FROM companies WHERE UPPER(company_name) = ?",
        (cn_upper,)
    ).fetchone()
    if not co:
        # Try LIKE prefix match
        co = db.execute(
            "SELECT company_id FROM companies WHERE UPPER(company_name) LIKE ?",
            (cn_upper + '%',)
        ).fetchone()
    if not co:
        return False

    company_id = co['company_id']
    enriched = False

    # Find projects for this company with gaps to fill
    projects = db.execute("""
        SELECT project_id, stage, technology, capacity_raw
        FROM unified_projects
        WHERE company_id = ?
    """, (company_id,)).fetchall()

    now = now_iso()
    for proj in projects:
        pid = proj['project_id']

        # Stage update (if we have a stage signal)
        if stage:
            updated = apply_stage_update(
                db, pid, stage, evidence_date,
                f'grid_enrichment: {source_url}',
                confidence=0.75
            )
            if updated:
                enriched = True

        # Technology update (COALESCE — only fill if empty)
        if technology and not (proj['technology'] or '').strip():
            db.execute("""
                UPDATE unified_projects
                SET technology = ?, updated_at = ?
                WHERE project_id = ? AND (technology IS NULL OR technology = '')
            """, (technology, now, pid))
            enriched = True

        # Capacity update (COALESCE — only fill if empty)
        if capacity and not (proj['capacity_raw'] or '').strip():
            db.execute("""
                UPDATE unified_projects
                SET capacity_raw = ?, updated_at = ?
                WHERE project_id = ? AND (capacity_raw IS NULL OR capacity_raw = '')
            """, (capacity, now, pid))
            enriched = True

    return enriched


def _enrich_puc_docket(db: sqlite3.Connection, item: dict) -> None:
    """Match PUC SB6 filer → company + facility, apply stage signal.

    CCN application filed → 'feed' (company committed enough to file)
    CCN approved/granted  → 'permitted' (PUC approved the permit)
    """
    meta = item.get('metadata', {})
    filer = (meta.get('filer') or '').strip()
    if not filer:
        return

    # Determine stage from docket status
    status = (meta.get('status') or '').lower()
    text_lower = (item.get('text_excerpt') or '').lower()
    if any(kw in status or kw in text_lower
           for kw in ('grant', 'approv', 'issued', 'order granting')):
        stage = 'permitted'
    else:
        stage = 'feed'

    _direct_enrich(db, filer, stage, None, item.get('url', ''),
                   item.get('date'))


def _process_ercot_queue_item(db: sqlite3.Connection, item: dict) -> str:
    """Extract applicant from ERCOT queue entry; return company_name.

    Safety rule: metadata-only extraction. If applicant can't be extracted
    reliably, default to 'ERCOT'. A missed signal is safer than a false one.
    """
    source = item.get('source', '')
    if source not in _ERCOT_QUEUE_SOURCES:
        return 'ERCOT'

    meta = item.get('metadata', {})
    applicant = meta.get('applicant') or meta.get('company') or ''
    applicant = applicant.strip()

    if applicant:
        _direct_enrich(db, applicant, 'permitted', None,
                       item.get('url', ''), item.get('date'))

    return applicant or 'ERCOT'


# ── Pipeline 1 ingestion ─────────────────────────────────────────────────────

def collect_pipeline1(db: sqlite3.Connection,
                      existing_urls: set) -> int:
    """Ingest Pipeline 1 latest_results.json → regulatory_evidence."""
    json_path = P1_OUTPUTS / 'latest_results.json'
    if not json_path.exists():
        print(f'  Pipeline 1: no output file at {json_path}')
        return 0

    with open(json_path) as f:
        items = json.load(f)

    print(f'\n── Pipeline 1: {len(items)} items ──────────────────')
    inserted = skipped = filtered = 0
    now = now_iso()

    for item in items:
        source = item.get('source', '')
        source_system = _resolve_source_system(source)
        if not source_system:
            skipped += 1
            continue

        # Relevance filter
        if (item.get('relevance_score') or 0) < _P1_MIN_RELEVANCE:
            filtered += 1
            continue

        url = item.get('url', '')
        if url in existing_urls:
            skipped += 1
            continue

        company = _extract_company(item)
        doc_type = _doc_type_from_source(source)
        doc_date = item.get('date') or now[:10]

        # Build excerpt with metadata as readable text
        text = item.get('text_excerpt', '') or ''
        meta = item.get('metadata')
        if meta:
            meta_lines = _flatten_metadata(meta)
            if meta_lines:
                text += '\n\n--- Additional Data ---\n' + '\n'.join(meta_lines)

        if DRY_RUN:
            print(f'  [dry-run] {source_system:<20} {company[:40]:<40} '
                  f'score={item.get("relevance_score", 0):.2f}')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,?,?,?,?,?,?,?)
        """, (company, doc_type, doc_date, url,
              text[:50000], len(text), source_system, now))
        existing_urls.add(url)
        inserted += 1

        # Direct enrichment for specific sources
        if source in ('puc_sb6', 'puc_ccn_search'):
            _enrich_puc_docket(db, item)
        elif source in _ERCOT_QUEUE_SOURCES:
            _process_ercot_queue_item(db, item)

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped: {skipped}  '
          f'Filtered (low relevance): {filtered}')
    return inserted


# ── Pipeline 2 ingestion ─────────────────────────────────────────────────────

def collect_pipeline2_json(db: sqlite3.Connection,
                           existing_urls: set) -> int:
    """Ingest Pipeline 2 latest_results.json → regulatory_evidence."""
    json_path = P2_OUTPUTS / 'latest_results.json'
    if not json_path.exists():
        print(f'  Pipeline 2: no output file at {json_path}')
        return 0

    with open(json_path) as f:
        items = json.load(f)

    print(f'\n── Pipeline 2 JSON: {len(items)} items ──────────────')
    inserted = skipped = 0
    now = now_iso()

    for item in items:
        source = item.get('source', '')
        source_system = _resolve_source_system(source)
        if not source_system:
            skipped += 1
            continue

        url = item.get('url', '')
        if url in existing_urls:
            skipped += 1
            continue

        company = _extract_company(item)
        doc_type = _doc_type_from_source(source)
        doc_date = item.get('date') or now[:10]

        text = item.get('text_excerpt', '') or ''
        meta = item.get('metadata')
        if meta:
            meta_lines = _flatten_metadata(meta)
            if meta_lines:
                text += '\n\n--- Additional Data ---\n' + '\n'.join(meta_lines)

        if DRY_RUN:
            print(f'  [dry-run] {source_system:<20} {company[:40]:<40}')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,?,?,?,?,?,?,?)
        """, (company, doc_type, doc_date, url,
              text[:50000], len(text), source_system, now))
        existing_urls.add(url)
        inserted += 1

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped: {skipped}')
    return inserted


# ── Pipeline 2 alerts ─────────────────────────────────────────────────────────

def collect_alerts(db: sqlite3.Connection,
                   existing_urls: set) -> int:
    """Ingest Pipeline 2 queue_alerts.json → regulatory_evidence."""
    json_path = P2_OUTPUTS / 'queue_alerts.json'
    if not json_path.exists():
        print(f'  Pipeline 2 alerts: no output file at {json_path}')
        return 0

    with open(json_path) as f:
        alerts = json.load(f)

    if not isinstance(alerts, list):
        alerts = [alerts]

    print(f'\n── Pipeline 2 Alerts: {len(alerts)} items ────────────')
    inserted = skipped = 0
    now = now_iso()

    for alert in alerts:
        url = alert.get('url', f'alert://{alert.get("type", "unknown")}_{now}')
        if url in existing_urls:
            skipped += 1
            continue

        entity = alert.get('entity') or 'ERCOT'
        text = alert.get('text_excerpt') or alert.get('message') or ''
        if alert.get('metadata'):
            meta_lines = _flatten_metadata(alert['metadata'])
            if meta_lines:
                text += '\n\n--- Additional Data ---\n' + '\n'.join(meta_lines)

        if DRY_RUN:
            print(f'  [dry-run] alert: {entity[:40]}')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,?,?,?,?,?,?,?)
        """, (entity, 'queue_alert', alert.get('date', now[:10]), url,
              text[:50000], len(text), 'interconnection_fyi', now))
        existing_urls.add(url)
        inserted += 1

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped: {skipped}')
    return inserted


# ── EIA-860M CSV ingestion ────────────────────────────────────────────────────

def collect_eia_csv(db: sqlite3.Connection,
                    csv_paths: list[Path],
                    existing_urls: set) -> int:
    """Ingest EIA-860M CSV files → regulatory_evidence + direct enrichment."""
    if not csv_paths:
        print('  EIA-860M: no CSV files found')
        return 0

    print(f'\n── EIA-860M CSV: {len(csv_paths)} files ──────────────')
    inserted = skipped = enriched_count = 0
    unmatched_entities: list[tuple] = []
    now = now_iso()

    for csv_path in csv_paths:
        print(f'  Processing: {csv_path.name}')
        with open(csv_path, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                entity_name = (row.get('Entity Name') or '').strip()
                plant_name = (row.get('Plant Name') or '').strip()
                plant_id = row.get('Plant ID', '').strip()

                # Build canonical URL for dedup
                doc_url = (f'https://www.eia.gov/electricity/data/eia860m/'
                           f'#plant_{plant_id}')
                if doc_url in existing_urls:
                    skipped += 1
                    continue

                # Extract stage from status code
                status_raw = row.get('Status', '')
                status_match = re.match(r'\((\w+)\)', status_raw)
                status_code = status_match.group(1) if status_match else None
                stage = EIA_STATUS_TO_STAGE.get(status_code)

                # Extract technology from energy source code
                energy_code = (row.get('Energy Source Code') or '').strip()
                technology = EIA_TECH_MAP.get(energy_code)

                # Build evidence date
                try:
                    year = row.get('Planned Operation Year', '')
                    month = row.get('Planned Operation Month', '1')
                    year_f = float(year) if year else None
                    month_f = float(month) if month else 1.0
                    if year_f:
                        doc_date = f'{int(year_f)}-{int(month_f):02d}-01'
                    else:
                        doc_date = now[:10]
                except (ValueError, TypeError):
                    doc_date = now[:10]

                # Build structured excerpt
                county = (row.get('County') or '').strip()
                capacity = (row.get('Nameplate Capacity (MW)') or '').strip()
                tech_name = (row.get('Technology') or '').strip()
                lat = (row.get('Latitude') or '').strip()
                lon = (row.get('Longitude') or '').strip()
                state = (row.get('Plant State') or '').strip()

                raw_text = (
                    f'Plant: {plant_name} | Entity: {entity_name} | '
                    f'County: {county}, {state} | '
                    f'Capacity: {capacity} MW | Tech: {tech_name} | '
                    f'Energy: {energy_code} | Status: {status_raw} | '
                    f'Stage: {stage or "unknown"}'
                )
                if lat and lon:
                    raw_text += f' | Coords: {lat}, {lon}'

                if DRY_RUN:
                    print(f'    [dry-run] {entity_name[:35]:<35} '
                          f'{plant_name[:30]:<30} {capacity:>6} MW  '
                          f'{stage or "?":>12}')
                    inserted += 1
                    existing_urls.add(doc_url)
                    continue

                db.execute("""
                    INSERT INTO regulatory_evidence
                    (company_name, company_cik, document_type, document_date,
                     document_url, raw_text_excerpt, excerpt_char_count,
                     source_system, ingested_at, facility_id)
                    VALUES (?,NULL,?,?,?,?,?,?,?,?)
                """, (entity_name, 'planned_generator', doc_date, doc_url,
                      raw_text, len(raw_text), 'eia_860m', now,
                      plant_id if plant_id else None))
                existing_urls.add(doc_url)
                inserted += 1

                # Direct enrichment → unified_projects
                cap_str = f'{capacity} MW' if capacity else None
                if stage or technology or cap_str:
                    was_enriched = _direct_enrich(
                        db, entity_name, stage, technology,
                        doc_url, doc_date, capacity=cap_str
                    )
                    if was_enriched:
                        enriched_count += 1
                    elif entity_name:
                        unmatched_entities.append((
                            entity_name, plant_name, county, capacity, tech_name
                        ))

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped: {skipped}  '
          f'Enriched projects: {enriched_count}')

    if unmatched_entities:
        # Deduplicate by entity name for summary
        unique_entities = {}
        for ent in unmatched_entities:
            key = ent[0].upper()
            if key not in unique_entities:
                unique_entities[key] = ent
        print(f'\n  {len(unique_entities)} EIA entities not matched to '
              f'companies table:')
        for name, plant, county, cap, tech in sorted(
                unique_entities.values(), key=lambda x: x[0]):
            print(f'    {name[:40]:<40} {plant[:25]:<25} '
                  f'{county[:15]:<15} {cap:>6} MW')

    return inserted


# ── Stats ─────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection):
    """Show current grid coverage in regulatory_evidence."""
    grid_systems = ('ercot', 'puc_texas', 'sec_grid',
                    'eia_860m', 'tsp_earnings', 'interconnection_fyi')
    print('\n=== GRID COVERAGE ===')
    total = 0
    for sys_name in grid_systems:
        cnt = db.execute(
            'SELECT COUNT(*) FROM regulatory_evidence WHERE source_system=?',
            (sys_name,)
        ).fetchone()[0]
        total += cnt
        if cnt > 0:
            distinct = db.execute(
                'SELECT COUNT(DISTINCT company_name) FROM regulatory_evidence '
                'WHERE source_system=?', (sys_name,)
            ).fetchone()[0]
            print(f'  {sys_name:<25} {cnt:>5} records  '
                  f'({distinct} distinct companies)')
    print(f'\n  Total grid records: {total}')


# ── Main ──────────────────────────────────────────────────────────────────────

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

    # Load existing URLs for dedup (across all grid source_systems)
    grid_systems = ('ercot', 'puc_texas', 'sec_grid',
                    'eia_860m', 'tsp_earnings', 'interconnection_fyi')
    existing = set()
    for sys_name in grid_systems:
        for r in db.execute(
            'SELECT document_url FROM regulatory_evidence WHERE source_system=?',
            (sys_name,)
        ):
            existing.add(r[0])

    mode = 'DRY RUN' if DRY_RUN else 'COLLECTING'
    print(f'{mode} — Grid Pipeline Integration')
    print(f'  Existing grid records: {len(existing)}')

    total = 0

    if not EIA_ONLY:
        total += collect_pipeline1(db, existing)
        total += collect_pipeline2_json(db, existing)
        total += collect_alerts(db, existing)

    total += collect_eia_csv(db, EIA_CSV_PATHS, existing)

    print(f'\n  TOTAL INSERTED: {total}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
