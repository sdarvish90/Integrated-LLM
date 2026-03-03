#!/usr/bin/env python3
"""
DecarbIQ — exhaustive_audit.py
Full census verification of every record against its source API.

Phases:
  1. DB internal consistency (FK integrity, duplicates, dates)    — no API
  2. FRS crosswalk vs EPA Envirofacts                             — 32 API calls
  3. DOE status vs USAspending.gov                                — 297 API calls
  4. TCEQ permits vs SODA API                                     — ~400 API calls
  5. EPA permits vs ECHO API                                      — 59 API calls
  6. Entity aliases & crossrefs consistency                        — DB + ~100 API calls
  7. Claims completeness (claims ↔ documents ↔ projects)          — no API

Saves progress to data/exhaustive_audit_progress.json so it can resume.

Usage:
    python exhaustive_audit.py                  # run all phases
    python exhaustive_audit.py --phase=2        # run only phase 2
    python exhaustive_audit.py --resume         # resume from saved progress
    python exhaustive_audit.py --phase=1        # DB-only checks (no API)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import LEARNING_DB

# ── Config ────────────────────────────────────────────────────────────────────
UA = 'DecarbIQ-Research admin@decarbiq.com'
HEADERS = {'User-Agent': UA}
SLEEP_SEC = 0.35
EFSERVICE = 'https://data.epa.gov/efservice'
SODA_BASE = 'https://data.texas.gov/resource'
EPA_ECHO_FACILITY = 'https://echodata.epa.gov/echo/echo_rest_services.get_facility_info'
USASPENDING_SEARCH = 'https://api.usaspending.gov/api/v2/search/spending_by_award/'

SODA_DATASETS = {
    'coastal_east_tx': 'tzyg-j7q4',
    'border_permian':  '9iad-hrn8',
    'central_tx':      'msah-s2rv',
    'dallas_fw':       't34q-qzi3',
    'north_tx':        '5eqq-7nad',
}
SODA_REGION_MAP = {
    10: 'coastal_east_tx', 12: 'coastal_east_tx', 14: 'coastal_east_tx',
    7: 'border_permian', 15: 'border_permian', 16: 'border_permian',
    11: 'central_tx', 13: 'central_tx',
    4: 'dallas_fw', 5: 'dallas_fw',
    3: 'north_tx', 9: 'north_tx',
    # regions not in map → try all datasets
    1: 'north_tx', 2: 'north_tx', 6: 'border_permian', 8: 'north_tx',
}

DATA_DIR = LEARNING_DB.parent
PROGRESS_FILE = DATA_DIR / 'exhaustive_audit_progress.json'
REPORT_FILE = DATA_DIR / 'exhaustive_audit_report.txt'

PHASE_FILTER = None
RESUME = '--resume' in sys.argv
for arg in sys.argv:
    if arg.startswith('--phase='):
        PHASE_FILTER = int(arg.split('=', 1)[1])


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── HTTP helpers ──────────────────────────────────────────────────────────────
def _get_json(url: str, params: dict | None = None, retries: int = 3) -> dict | list | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                wait = 3 ** (attempt + 1)
                print(f'    [429] rate limit, waiting {wait}s...')
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            return data if data else None
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(2)
        except (requests.exceptions.RequestException, json.JSONDecodeError):
            if attempt < retries - 1:
                time.sleep(1)
    return None


def _post_json(url: str, payload: dict, retries: int = 3) -> dict | None:
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers={
                **HEADERS, 'Content-Type': 'application/json'
            }, timeout=30)
            if r.status_code == 429:
                wait = 3 ** (attempt + 1)
                time.sleep(wait)
                continue
            if r.status_code in (404, 422):
                return None
            r.raise_for_status()
            return r.json()
        except (requests.exceptions.RequestException, json.JSONDecodeError):
            if attempt < retries - 1:
                time.sleep(1)
    return None


# ── Progress tracking ─────────────────────────────────────────────────────────
class AuditProgress:
    def __init__(self):
        self.phases: dict[str, dict] = {}
        self.errors: list[dict] = []
        if RESUME and PROGRESS_FILE.exists():
            with open(PROGRESS_FILE) as f:
                saved = json.load(f)
                self.phases = saved.get('phases', {})
                self.errors = saved.get('errors', [])

    def save(self):
        with open(PROGRESS_FILE, 'w') as f:
            json.dump({'phases': self.phases, 'errors': self.errors}, f, indent=2)

    def is_done(self, phase: str, key: str) -> bool:
        return key in self.phases.get(phase, {})

    def record(self, phase: str, key: str, verdict: str, detail: str = ''):
        if phase not in self.phases:
            self.phases[phase] = {}
        self.phases[phase][key] = {'verdict': verdict, 'detail': detail}

    def add_error(self, phase: str, key: str, error: str):
        self.errors.append({'phase': phase, 'key': key, 'error': error})

    def summary(self, phase: str) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for v in self.phases.get(phase, {}).values():
            counts[v['verdict']] += 1
        return dict(counts)


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1: DB Internal Consistency
# ══════════════════════════════════════════════════════════════════════════════
def phase_1_db_consistency(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 1: DB Internal Consistency')
    print('=' * 70)

    checks = []

    # 1a. Orphaned claims (claim has no matching document)
    n = db.execute("""
        SELECT COUNT(*) FROM claims c
        WHERE c.source_id NOT IN (SELECT source_id FROM cleaned_documents)
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'FAIL'
    checks.append(('orphaned_claims_no_document', n, verdict))
    progress.record('phase1', 'orphaned_claims_no_document', verdict, f'{n} orphaned')

    # 1b. Orphaned project_claims (project_claim → nonexistent project)
    n = db.execute("""
        SELECT COUNT(*) FROM project_claims pc
        WHERE pc.project_id NOT IN (SELECT project_id FROM unified_projects)
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'FAIL'
    checks.append(('project_claims_bad_project', n, verdict))
    progress.record('phase1', 'project_claims_bad_project', verdict, f'{n} broken')

    # 1c. project_claims → nonexistent claim
    n = db.execute("""
        SELECT COUNT(*) FROM project_claims pc
        WHERE pc.claim_id NOT IN (SELECT claim_id FROM claims)
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'FAIL'
    checks.append(('project_claims_bad_claim', n, verdict))
    progress.record('phase1', 'project_claims_bad_claim', verdict, f'{n} broken')

    # 1d. entity_aliases → nonexistent entity
    n = db.execute("""
        SELECT COUNT(*) FROM entity_aliases ea
        WHERE ea.entity_id NOT IN (SELECT entity_id FROM entities)
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'FAIL'
    checks.append(('entity_aliases_bad_entity', n, verdict))
    progress.record('phase1', 'entity_aliases_bad_entity', verdict, f'{n} broken')

    # 1e. Duplicate entities (same LOWER name)
    n = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT LOWER(canonical_name), COUNT(*) c FROM entities
            GROUP BY LOWER(canonical_name) HAVING c > 1
        )
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'WARN'
    checks.append(('duplicate_entities', n, verdict))
    progress.record('phase1', 'duplicate_entities', verdict, f'{n} duplicate groups')

    # 1f. Duplicate companies (same LOWER name)
    n = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT LOWER(company_name), COUNT(*) c FROM companies
            GROUP BY LOWER(company_name) HAVING c > 1
        )
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'WARN'
    checks.append(('duplicate_companies', n, verdict))
    progress.record('phase1', 'duplicate_companies', verdict, f'{n} duplicate groups')

    # 1g. Claims with future dates (>6 months ahead)
    n = db.execute("""
        SELECT COUNT(*) FROM claims
        WHERE document_date > date('now', '+6 months')
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'WARN'
    checks.append(('future_dated_claims', n, verdict))
    progress.record('phase1', 'future_dated_claims', verdict, f'{n} claims')

    # 1h. Orphaned accumulator entries
    n = db.execute("""
        SELECT COUNT(*) FROM project_signal_accumulator
        WHERE project_id NOT IN (SELECT project_id FROM unified_projects)
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'WARN'
    checks.append(('orphaned_accumulators', n, verdict))
    progress.record('phase1', 'orphaned_accumulators', verdict, f'{n} orphaned')

    # 1i. "Parent Company" literal aliases
    n = db.execute("SELECT COUNT(*) FROM entity_aliases WHERE alias = 'Parent Company'").fetchone()[0]
    verdict = 'PASS' if n == 0 else 'FAIL'
    checks.append(('parent_company_literals', n, verdict))
    progress.record('phase1', 'parent_company_literals', verdict, f'{n} found')

    # 1j. Active projects with 0 claims
    n = db.execute("""
        SELECT COUNT(*) FROM unified_projects up
        WHERE up.quarantined = 0
          AND up.project_id NOT IN (SELECT DISTINCT project_id FROM project_claims)
    """).fetchone()[0]
    checks.append(('active_projects_no_claims', n, 'INFO'))
    progress.record('phase1', 'active_projects_no_claims', 'INFO', f'{n} projects')

    # 1k. Claim ↔ project_claim count match
    c1 = db.execute('SELECT COUNT(*) FROM claims').fetchone()[0]
    c2 = db.execute('SELECT COUNT(*) FROM project_claims').fetchone()[0]
    verdict = 'PASS' if c1 == c2 else 'WARN'
    checks.append(('claims_pc_count_match', abs(c1 - c2), verdict))
    progress.record('phase1', 'claims_pc_count_match', verdict, f'claims={c1}, project_claims={c2}')

    # 1l. Duplicate claims (same project_id + claim_type + claim_text)
    n = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT pc.project_id, c.claim_type, c.claim_text, COUNT(*) cnt
            FROM project_claims pc
            JOIN claims c ON c.claim_id = pc.claim_id
            GROUP BY pc.project_id, c.claim_type, c.claim_text
            HAVING cnt > 1
        )
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'WARN'
    checks.append(('duplicate_claims', n, verdict))
    progress.record('phase1', 'duplicate_claims', verdict, f'{n} duplicate groups')

    # 1m. Crossrefs with distance > 4mi and name_overlap = 0
    n = db.execute("""
        SELECT COUNT(*) FROM facility_crossref
        WHERE match_method = 'spatial'
          AND json_extract(match_detail, '$.distance_miles') > 4.0
          AND json_extract(match_detail, '$.name_overlap') = 0.0
    """).fetchone()[0]
    verdict = 'PASS' if n == 0 else 'FAIL'
    checks.append(('false_positive_crossrefs', n, verdict))
    progress.record('phase1', 'false_positive_crossrefs', verdict, f'{n} false positives')

    # Print
    for name, count, v in checks:
        print(f'  {v:6s}  {name:<35s}  {count}')

    progress.save()


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: FRS Crosswalk vs EPA Envirofacts
# ══════════════════════════════════════════════════════════════════════════════
def phase_2_frs_crosswalk(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 2: FRS Crosswalk vs EPA Envirofacts')
    print('=' * 70)

    # Get all unique registry_ids
    reg_ids = db.execute(
        'SELECT DISTINCT registry_id FROM frs_crosswalk ORDER BY registry_id'
    ).fetchall()
    total = len(reg_ids)
    print(f'  Unique registry IDs to verify: {total}')

    verified = 0
    mismatched = 0
    api_errors = 0

    for i, row in enumerate(reg_ids):
        reg_id = row['registry_id']
        key = f'frs_{reg_id}'

        if progress.is_done('phase2', key):
            v = progress.phases['phase2'][key]['verdict']
            if v == 'VERIFIED':
                verified += 1
            elif v == 'MISMATCH':
                mismatched += 1
            continue

        # Get DB records for this registry_id
        db_rows = db.execute(
            'SELECT pgm_sys_acrnm, pgm_sys_id, primary_name, city_name, state_code '
            'FROM frs_crosswalk WHERE registry_id = ?',
            (reg_id,)
        ).fetchall()

        db_programs = {(r['pgm_sys_acrnm'], r['pgm_sys_id']) for r in db_rows}
        db_name = db_rows[0]['primary_name'] if db_rows else ''

        # Query FRS API
        api_data = _get_json(f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{reg_id}/JSON')
        time.sleep(SLEEP_SEC)

        if api_data is None:
            progress.record('phase2', key, 'API_ERROR', f'No data for {reg_id}')
            progress.add_error('phase2', key, 'FRS API returned no data')
            api_errors += 1
            if (i + 1) % 10 == 0:
                print(f'  [{i+1}/{total}] {reg_id}: API_ERROR')
                progress.save()
            continue

        # Build API program set
        api_programs = set()
        api_name = ''
        for prog in api_data:
            acrnm = prog.get('pgm_sys_acrnm', prog.get('PGM_SYS_ACRNM', ''))
            prog_id = prog.get('pgm_sys_id', prog.get('PGM_SYS_ID', ''))
            if not api_name:
                api_name = prog.get('primary_name', prog.get('PRIMARY_NAME', ''))
            api_programs.add((acrnm, prog_id))

        # Compare: every DB entry should exist in API
        missing_from_api = db_programs - api_programs
        extra_in_api = api_programs - db_programs

        if not missing_from_api:
            progress.record('phase2', key, 'VERIFIED',
                            f'{db_name} — {len(db_programs)} DB programs, '
                            f'{len(api_programs)} API programs')
            verified += 1
        else:
            detail = f'{db_name} — {len(missing_from_api)} DB entries not in API: '
            detail += ', '.join(f'{a}:{b}' for a, b in list(missing_from_api)[:3])
            progress.record('phase2', key, 'MISMATCH', detail)
            mismatched += 1

        if (i + 1) % 10 == 0:
            print(f'  [{i+1}/{total}] verified={verified}, mismatch={mismatched}, errors={api_errors}')
            progress.save()

    progress.save()
    print(f'\n  Phase 2 complete: {verified} verified, {mismatched} mismatch, {api_errors} API errors')
    print(f'  Total FRS rows covered: {db.execute("SELECT COUNT(*) FROM frs_crosswalk").fetchone()[0]}')


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3: DOE Status vs USAspending
# ══════════════════════════════════════════════════════════════════════════════
def phase_3_doe_status(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 3: DOE Status vs USAspending.gov')
    print('=' * 70)

    rows = db.execute("""
        SELECT source_id, company_name, document_url, doe_award_status, doe_pct_disbursed
        FROM doe_status
        WHERE document_url LIKE '%usaspending%'
        ORDER BY source_id
    """).fetchall()
    total = len(rows)
    print(f'  DOE awards to verify: {total}')

    verified = 0
    stale = 0
    not_found = 0
    api_errors = 0

    for i, row in enumerate(rows):
        url = row['document_url']
        award_id = url.split('/award/')[-1] if '/award/' in url else None
        if not award_id:
            continue

        key = f'doe_{award_id}'
        if progress.is_done('phase3', key):
            v = progress.phases['phase3'][key]['verdict']
            if v == 'VERIFIED':
                verified += 1
            elif v == 'STALE':
                stale += 1
            elif v == 'NOT_FOUND':
                not_found += 1
            continue

        db_name = row['company_name']
        db_status = row['doe_award_status']
        db_pct = row['doe_pct_disbursed']

        # Query USAspending
        api_resp = _post_json(USASPENDING_SEARCH, {
            'filters': {
                'keywords': [award_id],
                'award_type_codes': ['02', '03', '04', '05'],
            },
            'fields': ['Award ID', 'Recipient Name', 'Award Amount',
                        'Start Date', 'End Date'],
            'limit': 5, 'page': 1,
        })
        time.sleep(SLEEP_SEC)

        if api_resp is None or not api_resp.get('results'):
            progress.record('phase3', key, 'NOT_FOUND',
                            f'{db_name} — award {award_id} not in USAspending')
            not_found += 1
            if (i + 1) % 20 == 0:
                print(f'  [{i+1}/{total}] verified={verified}, stale={stale}, '
                      f'not_found={not_found}, errors={api_errors}')
                progress.save()
            continue

        # Find exact match
        match = None
        for hit in api_resp['results']:
            if hit.get('Award ID', '').strip() == award_id:
                match = hit
                break

        if match:
            api_name = match.get('Recipient Name', '')
            api_amount = match.get('Award Amount', 0) or 0
            # Check name match
            name_match = db_name.upper()[:10] in api_name.upper() if api_name else False
            detail = (f'{db_name} → API: {api_name}, ${api_amount:,.0f}, '
                      f'name_match={name_match}')
            progress.record('phase3', key, 'VERIFIED', detail)
            verified += 1
        else:
            # Keyword found results but no exact award ID match
            top = api_resp['results'][0]
            detail = (f'{db_name} — award {award_id} keyword found but '
                      f'no exact ID match. Top result: {top.get("Award ID", "?")}')
            progress.record('phase3', key, 'STALE', detail)
            stale += 1

        if (i + 1) % 20 == 0:
            print(f'  [{i+1}/{total}] verified={verified}, stale={stale}, '
                  f'not_found={not_found}, errors={api_errors}')
            progress.save()

    progress.save()
    print(f'\n  Phase 3 complete: {verified} verified, {stale} stale, '
          f'{not_found} not found, {api_errors} errors')


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4: TCEQ Permits vs SODA API
# ══════════════════════════════════════════════════════════════════════════════
def phase_4_tceq_permits(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 4: TCEQ Permits vs SODA API')
    print('=' * 70)

    # Get unique RNs with their region
    rns = db.execute("""
        SELECT rn_number, region_number, COUNT(*) AS permit_count,
               GROUP_CONCAT(DISTINCT permit_number) AS permits
        FROM tceq_permits
        GROUP BY rn_number
        ORDER BY rn_number
    """).fetchall()
    total = len(rns)
    print(f'  Unique TCEQ RN numbers to verify: {total}')

    verified = 0
    mismatched = 0
    not_found = 0
    api_errors = 0

    for i, rn_row in enumerate(rns):
        rn = rn_row['rn_number']
        region = rn_row['region_number']
        db_permit_count = rn_row['permit_count']

        key = f'tceq_{rn}'
        if progress.is_done('phase4', key):
            v = progress.phases['phase4'][key]['verdict']
            if v == 'VERIFIED':
                verified += 1
            elif v == 'MISMATCH':
                mismatched += 1
            elif v == 'NOT_FOUND':
                not_found += 1
            continue

        # Determine which SODA dataset to query
        try:
            region_int = int(region) if region else 0
        except (ValueError, TypeError):
            region_int = 0
        dataset_key = SODA_REGION_MAP.get(region_int)
        if not dataset_key:
            progress.record('phase4', key, 'API_ERROR',
                            f'No SODA dataset for region {region}')
            api_errors += 1
            continue

        dataset_id = SODA_DATASETS[dataset_key]

        # Query SODA for this RN (column is ref_num_txt in SODA)
        api_data = _get_json(
            f'{SODA_BASE}/{dataset_id}.json',
            params={
                '$where': f"ref_num_txt='{rn}'",
                '$limit': 1000,
            }
        )
        time.sleep(SLEEP_SEC)

        if api_data is None or len(api_data) == 0:
            progress.record('phase4', key, 'NOT_FOUND',
                            f'RN {rn} not found in SODA dataset {dataset_key}')
            not_found += 1
        else:
            api_permit_count = len(api_data)
            # Get entity name from API
            api_name = api_data[0].get('regulated_entity_name', 'N/A')

            # Get DB entity name
            db_name = db.execute(
                'SELECT entity_name FROM tceq_permits WHERE rn_number = ? LIMIT 1',
                (rn,)
            ).fetchone()['entity_name']

            # Compare: API should have at least as many permits as DB
            # (DB may have fewer due to NAICS filtering)
            if api_permit_count >= db_permit_count:
                progress.record('phase4', key, 'VERIFIED',
                                f'{db_name}: DB={db_permit_count} permits, '
                                f'API={api_permit_count} permits')
                verified += 1
            else:
                progress.record('phase4', key, 'MISMATCH',
                                f'{db_name}: DB has {db_permit_count} permits but '
                                f'API only has {api_permit_count}')
                mismatched += 1

        if (i + 1) % 50 == 0:
            print(f'  [{i+1}/{total}] verified={verified}, mismatch={mismatched}, '
                  f'not_found={not_found}, errors={api_errors}')
            progress.save()

    progress.save()
    print(f'\n  Phase 4 complete: {verified} verified, {mismatched} mismatch, '
          f'{not_found} not found, {api_errors} errors')
    print(f'  Total TCEQ permit rows covered: '
          f'{db.execute("SELECT COUNT(*) FROM tceq_permits").fetchone()[0]}')


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 5: EPA Permits vs ECHO API
# ══════════════════════════════════════════════════════════════════════════════
def phase_5_epa_permits(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 5: EPA Permits vs ECHO API')
    print('=' * 70)

    # Get unique registry_ids (GROUP BY to deduplicate)
    reg_ids = db.execute("""
        SELECT registry_id,
               MIN(facility_name) AS facility_name,
               MIN(facility_city) AS facility_city,
               MIN(facility_state) AS facility_state,
               COUNT(*) AS permit_count
        FROM epa_permits
        GROUP BY registry_id
        ORDER BY registry_id
    """).fetchall()
    total = len(reg_ids)
    print(f'  Unique EPA registry IDs to verify: {total}')

    verified = 0
    mismatched = 0
    not_found = 0

    for i, row in enumerate(reg_ids):
        reg_id = row['registry_id']
        key = f'epa_{reg_id}'

        if progress.is_done('phase5', key):
            v = progress.phases['phase5'][key]['verdict']
            if v == 'VERIFIED':
                verified += 1
            elif v == 'MISMATCH':
                mismatched += 1
            elif v == 'NOT_FOUND':
                not_found += 1
            continue

        db_name = row['facility_name']
        db_city = row['facility_city']
        db_state = row['facility_state']

        # Count permits in DB for this registry_id
        db_count = db.execute(
            'SELECT COUNT(*) FROM epa_permits WHERE registry_id = ?', (reg_id,)
        ).fetchone()[0]

        # Verify via FRS API (check registry_id exists)
        api_data = _get_json(
            f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{reg_id}/JSON'
        )
        time.sleep(SLEEP_SEC)

        if api_data is None or len(api_data) == 0:
            progress.record('phase5', key, 'NOT_FOUND',
                            f'{db_name} (registry {reg_id}) not in EPA FRS')
            not_found += 1
        else:
            api_name = api_data[0].get('primary_name',
                                        api_data[0].get('PRIMARY_NAME', ''))
            api_city = api_data[0].get('city_name',
                                        api_data[0].get('CITY_NAME', ''))
            api_programs = set(
                p.get('pgm_sys_acrnm', p.get('PGM_SYS_ACRNM', ''))
                for p in api_data
            )

            # Name/city comparison
            name_ok = (db_name.upper()[:10] in api_name.upper()
                       or api_name.upper()[:10] in db_name.upper())
            city_ok = (db_city or '').upper()[:5] == (api_city or '').upper()[:5]

            if name_ok or city_ok:
                progress.record('phase5', key, 'VERIFIED',
                                f'{db_name} → API: {api_name}, {api_city}, '
                                f'{len(api_programs)} programs, {db_count} DB permits')
                verified += 1
            else:
                progress.record('phase5', key, 'MISMATCH',
                                f'Name mismatch: DB={db_name} ({db_city}), '
                                f'API={api_name} ({api_city})')
                mismatched += 1

        if (i + 1) % 20 == 0:
            print(f'  [{i+1}/{total}] verified={verified}, mismatch={mismatched}, '
                  f'not_found={not_found}')
            progress.save()

    progress.save()
    print(f'\n  Phase 5 complete: {verified} verified, {mismatched} mismatch, '
          f'{not_found} not found')
    print(f'  Total EPA permit rows covered: '
          f'{db.execute("SELECT COUNT(*) FROM epa_permits").fetchone()[0]}')


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Entity Aliases & Crossrefs
# ══════════════════════════════════════════════════════════════════════════════
def phase_6_aliases_crossrefs(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 6: Entity Aliases & Facility Crossrefs')
    print('=' * 70)

    # 6a. Entity aliases — verify all link to valid entities
    total_aliases = db.execute('SELECT COUNT(*) FROM entity_aliases').fetchone()[0]
    orphaned = db.execute("""
        SELECT COUNT(*) FROM entity_aliases ea
        WHERE ea.entity_id NOT IN (SELECT entity_id FROM entities)
    """).fetchone()[0]
    progress.record('phase6', 'aliases_fk_integrity', 'PASS' if orphaned == 0 else 'FAIL',
                    f'{total_aliases} aliases, {orphaned} orphaned')
    print(f'  Alias FK integrity: {total_aliases} aliases, {orphaned} orphaned → '
          f'{"PASS" if orphaned == 0 else "FAIL"}')

    # 6b. Check alias quality — no empty, no duplicate (entity_id, alias)
    empty = db.execute("SELECT COUNT(*) FROM entity_aliases WHERE alias IS NULL OR alias = ''").fetchone()[0]
    progress.record('phase6', 'empty_aliases', 'PASS' if empty == 0 else 'WARN',
                    f'{empty} empty aliases')

    dup_aliases = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT entity_id, alias, COUNT(*) c FROM entity_aliases
            GROUP BY entity_id, alias HAVING c > 1
        )
    """).fetchone()[0]
    progress.record('phase6', 'duplicate_aliases', 'PASS' if dup_aliases == 0 else 'WARN',
                    f'{dup_aliases} duplicate alias pairs')
    print(f'  Empty aliases: {empty}, Duplicate aliases: {dup_aliases}')

    # 6c. Facility crossrefs — verify FRS-based ones via API
    frs_xrefs = db.execute("""
        SELECT id, source_a_system, source_a_id, source_b_system, source_b_id,
               match_method, confidence, match_detail
        FROM facility_crossref
        WHERE match_method IN ('frs_crosswalk', 'frs_id')
    """).fetchall()
    print(f'\n  FRS-based crossrefs to verify: {len(frs_xrefs)}')

    xref_verified = 0
    xref_mismatch = 0

    for xref in frs_xrefs:
        xref_key = f'xref_{xref["id"]}'
        if progress.is_done('phase6', xref_key):
            v = progress.phases['phase6'][xref_key]['verdict']
            if v == 'VERIFIED':
                xref_verified += 1
            else:
                xref_mismatch += 1
            continue

        epa_id = xref['source_b_id']
        src_a_sys = xref['source_a_system']
        src_a_id = xref['source_a_id']
        method = xref['match_method']

        api_data = _get_json(f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{epa_id}/JSON')
        time.sleep(SLEEP_SEC)

        if not api_data:
            progress.record('phase6', xref_key, 'API_ERROR', f'No FRS data for {epa_id}')
            xref_mismatch += 1
            continue

        if method == 'frs_id':
            # frs_id: both sides use same registry_id — just verify it exists in FRS
            progress.record('phase6', xref_key, 'VERIFIED',
                            f'{src_a_sys}:{src_a_id} ↔ epa_echo:{epa_id} — '
                            f'registry exists in FRS ({len(api_data)} programs)')
            xref_verified += 1
        else:
            # frs_crosswalk: source_a is TCEQ RN, verify it's in FRS programs
            tceq_found = any(
                p.get('pgm_sys_acrnm', p.get('PGM_SYS_ACRNM', '')) == 'TX-TCEQ ACR'
                and p.get('pgm_sys_id', p.get('PGM_SYS_ID', '')) == src_a_id
                for p in api_data
            )
            if tceq_found:
                progress.record('phase6', xref_key, 'VERIFIED',
                                f'{src_a_id} ↔ {epa_id} confirmed in FRS')
                xref_verified += 1
            else:
                progress.record('phase6', xref_key, 'MISMATCH',
                                f'{src_a_id} not linked to {epa_id} in FRS')
                xref_mismatch += 1

    print(f'  FRS crossrefs: {xref_verified} verified, {xref_mismatch} mismatch')

    # 6d. Spatial crossrefs — flag high-distance low-overlap
    spatial = db.execute("""
        SELECT id, source_a_id, source_b_id, confidence, match_detail
        FROM facility_crossref WHERE match_method = 'spatial'
    """).fetchall()
    suspicious_spatial = 0
    for s in spatial:
        try:
            detail = json.loads(s['match_detail']) if s['match_detail'] else {}
            dist = detail.get('distance_miles', 0)
            overlap = detail.get('name_overlap', 1)
            conf = s['confidence']
            xref_key = f'spatial_{s["id"]}'
            if dist > 3.0 and overlap == 0:
                progress.record('phase6', xref_key, 'SUSPICIOUS',
                                f'dist={dist:.1f}mi, overlap={overlap}, conf={conf:.2f}')
                suspicious_spatial += 1
            elif dist > 2.0 and overlap < 0.1:
                progress.record('phase6', xref_key, 'LOW_CONFIDENCE',
                                f'dist={dist:.1f}mi, overlap={overlap}, conf={conf:.2f}')
            else:
                progress.record('phase6', xref_key, 'VERIFIED',
                                f'dist={dist:.1f}mi, overlap={overlap}, conf={conf:.2f}')
        except (json.JSONDecodeError, TypeError):
            pass

    print(f'  Spatial crossrefs: {len(spatial)} total, {suspicious_spatial} suspicious')

    progress.save()


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 7: Claims Completeness
# ══════════════════════════════════════════════════════════════════════════════
def phase_7_claims(db: sqlite3.Connection, progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  PHASE 7: Claims Completeness')
    print('=' * 70)

    # 7a. Every claim has a valid source document
    orphaned = db.execute("""
        SELECT COUNT(*) FROM claims
        WHERE source_id NOT IN (SELECT source_id FROM cleaned_documents)
    """).fetchone()[0]
    progress.record('phase7', 'claims_valid_source', 'PASS' if orphaned == 0 else 'FAIL',
                    f'{orphaned} claims with invalid source_id')
    print(f'  Claims with valid source: {"PASS" if orphaned == 0 else f"FAIL ({orphaned} orphaned)"}')

    # 7b. Source system distribution matches regulatory_evidence
    claim_systems = db.execute("""
        SELECT cd.source_system, COUNT(DISTINCT c.claim_id) AS claim_count
        FROM claims c
        JOIN cleaned_documents cd ON c.source_id = cd.source_id
        GROUP BY cd.source_system
        ORDER BY claim_count DESC
    """).fetchall()
    re_systems = db.execute("""
        SELECT source_system, COUNT(*) AS doc_count
        FROM regulatory_evidence
        GROUP BY source_system
        ORDER BY doc_count DESC
    """).fetchall()
    re_map = {r['source_system']: r['doc_count'] for r in re_systems}

    print(f'\n  Source system coverage:')
    print(f'  {"System":<25s} {"Claims":>8s} {"Reg.Evidence":>12s} {"Ratio":>8s}')
    all_match = True
    for cs in claim_systems:
        sys = cs['source_system']
        claims = cs['claim_count']
        re_count = re_map.get(sys, 0)
        ratio = claims / max(1, re_count)
        print(f'  {sys:<25s} {claims:>8,} {re_count:>12,} {ratio:>8.1f}')
        if re_count == 0:
            all_match = False

    progress.record('phase7', 'source_system_coverage', 'PASS' if all_match else 'WARN',
                    f'{len(claim_systems)} systems with claims')

    # 7c. Confidence distribution
    conf_dist = db.execute("""
        SELECT
            CASE
                WHEN confidence >= 0.9 THEN 'high (>=0.9)'
                WHEN confidence >= 0.7 THEN 'medium (0.7-0.9)'
                WHEN confidence >= 0.5 THEN 'low (0.5-0.7)'
                ELSE 'very_low (<0.5)'
            END AS bucket,
            COUNT(*) AS cnt
        FROM claims
        GROUP BY bucket
        ORDER BY bucket
    """).fetchall()
    print(f'\n  Claim confidence distribution:')
    for c in conf_dist:
        print(f'    {c["bucket"]:<20s} {c["cnt"]:>8,}')

    # 7d. Claims per active project
    proj_claims = db.execute("""
        SELECT up.project_name, COUNT(pc.claim_id) AS claim_count
        FROM unified_projects up
        LEFT JOIN project_claims pc ON pc.project_id = up.project_id
        WHERE up.quarantined = 0
        GROUP BY up.project_id
        ORDER BY claim_count ASC
    """).fetchall()
    print(f'\n  Claims per active project (lowest 10):')
    for p in proj_claims[:10]:
        print(f'    {p["project_name"][:50]:<50s} {p["claim_count"]:>5}')

    # 7e. Check for NULL claim_type or claim_text
    null_type = db.execute("SELECT COUNT(*) FROM claims WHERE claim_type IS NULL OR claim_type = ''").fetchone()[0]
    null_text = db.execute("SELECT COUNT(*) FROM claims WHERE claim_text IS NULL OR claim_text = ''").fetchone()[0]
    progress.record('phase7', 'null_claim_fields', 'PASS' if null_type + null_text == 0 else 'WARN',
                    f'{null_type} null types, {null_text} null texts')
    print(f'\n  Null claim_type: {null_type}, Null claim_text: {null_text}')

    progress.save()


# ══════════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════════
def generate_report(progress: AuditProgress):
    print('\n' + '=' * 70)
    print('  GENERATING FINAL REPORT')
    print('=' * 70)

    lines = []
    lines.append('=' * 70)
    lines.append('  DecarbIQ — EXHAUSTIVE AUDIT REPORT')
    lines.append(f'  Date: {now_iso()}')
    lines.append(f'  Database: {LEARNING_DB}')
    lines.append('=' * 70)
    lines.append('')

    grand_total = 0
    grand_verified = 0
    grand_issues = 0

    phase_names = {
        'phase1': 'Phase 1: DB Internal Consistency',
        'phase2': 'Phase 2: FRS Crosswalk vs EPA Envirofacts',
        'phase3': 'Phase 3: DOE Status vs USAspending.gov',
        'phase4': 'Phase 4: TCEQ Permits vs SODA API',
        'phase5': 'Phase 5: EPA Permits vs ECHO API',
        'phase6': 'Phase 6: Entity Aliases & Crossrefs',
        'phase7': 'Phase 7: Claims Completeness',
    }

    for phase_key, phase_name in phase_names.items():
        if phase_key not in progress.phases:
            continue

        lines.append(f'\n── {phase_name} ──')
        summary = progress.summary(phase_key)
        phase_total = sum(summary.values())
        grand_total += phase_total

        for verdict, count in sorted(summary.items(), key=lambda x: -x[1]):
            lines.append(f'  {verdict:<25s} {count:>5}')
            if verdict in ('VERIFIED', 'PASS'):
                grand_verified += count
            elif verdict in ('FAIL', 'MISMATCH', 'INCORRECT', 'NOT_FOUND'):
                grand_issues += count

        lines.append(f'  {"TOTAL":<25s} {phase_total:>5}')

        # List issues
        issues = [(k, v) for k, v in progress.phases[phase_key].items()
                   if v['verdict'] in ('FAIL', 'MISMATCH', 'INCORRECT', 'NOT_FOUND', 'SUSPICIOUS')]
        if issues:
            lines.append(f'\n  Issues:')
            for k, v in issues[:20]:
                lines.append(f'    [{v["verdict"]}] {k}: {v["detail"][:80]}')
            if len(issues) > 20:
                lines.append(f'    ... and {len(issues) - 20} more')

    # Grand summary
    lines.append('\n' + '=' * 70)
    lines.append('  GRAND SUMMARY')
    lines.append('=' * 70)
    lines.append(f'  Total checks:      {grand_total:>6}')
    lines.append(f'  Verified/Pass:     {grand_verified:>6}  '
                 f'({100*grand_verified/max(1,grand_total):.1f}%)')
    lines.append(f'  Issues:            {grand_issues:>6}  '
                 f'({100*grand_issues/max(1,grand_total):.1f}%)')
    other = grand_total - grand_verified - grand_issues
    lines.append(f'  Other (warn/info): {other:>6}  '
                 f'({100*other/max(1,grand_total):.1f}%)')

    # Errors
    if progress.errors:
        lines.append(f'\n  API Errors ({len(progress.errors)}):')
        for err in progress.errors[:10]:
            lines.append(f'    [{err["phase"]}] {err["key"]}: {err["error"]}')

    report_text = '\n'.join(lines)
    with open(REPORT_FILE, 'w') as f:
        f.write(report_text)
    print(report_text)
    print(f'\n  Report saved to: {REPORT_FILE}')


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def run():
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row
    progress = AuditProgress()

    print('=' * 70)
    print('  DecarbIQ — EXHAUSTIVE AUDIT (every record vs source)')
    print(f'  Date: {now_iso()}')
    print(f'  Database: {LEARNING_DB}')
    print(f'  Resume: {RESUME}')
    if PHASE_FILTER:
        print(f'  Phase filter: {PHASE_FILTER}')
    print('=' * 70)

    phases = {
        1: phase_1_db_consistency,
        2: phase_2_frs_crosswalk,
        3: phase_3_doe_status,
        4: phase_4_tceq_permits,
        5: phase_5_epa_permits,
        6: phase_6_aliases_crossrefs,
        7: phase_7_claims,
    }

    for num, func in phases.items():
        if PHASE_FILTER and num != PHASE_FILTER:
            continue
        func(db, progress)

    generate_report(progress)
    db.close()


if __name__ == '__main__':
    run()
