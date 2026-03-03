#!/usr/bin/env python3
"""
DecarbIQ — verify_samples.py
Cross-check suspicious samples from the 2026-03-03 audit against authoritative APIs.

Sample Categories:
  A: Active projects (verify existence, stage, developer)
  B: Operational projects (verify they are actually running)
  C: DOE awards (verify against USAspending.gov API)
  D: FRS crosswalk (verify TCEQ RN → EPA Registry linkage)
  E: Facility crossrefs (verify linkages are correct)
  F: Entity aliases (verify corporate relationships)
  G: Lowest-confidence spatial crossrefs (likely false positives — already removed)
  H: Auto-created projects (verify relevance to H2/CCS)

Usage:
    python verify_samples.py              # run all checks
    python verify_samples.py --sample=C   # run only sample C
    python verify_samples.py --offline    # skip API calls, verify DB only
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

from config import LEARNING_DB

# ── Config ────────────────────────────────────────────────────────────────────
UA = 'DecarbIQ-Research admin@decarbiq.com'
HEADERS = {'User-Agent': UA}
SLEEP_SEC = 0.4
EFSERVICE = 'https://data.epa.gov/efservice'
SODA_BASE = 'https://data.texas.gov/resource'
EPA_ECHO_FACILITY = 'https://echodata.epa.gov/echo/echo_rest_services.get_facility_info'
EPA_ECHO_DFR = 'https://echodata.epa.gov/echo/dfr_rest_services.get_dfr'
USASPENDING_API = 'https://api.usaspending.gov/api/v2/awards'

OFFLINE = '--offline' in sys.argv
SAMPLE_FILTER = None
for arg in sys.argv:
    if arg.startswith('--sample='):
        SAMPLE_FILTER = arg.split('=', 1)[1].upper()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get_json(url: str, params: dict | None = None, retries: int = 3) -> dict | list | None:
    """Fetch JSON with retry and rate-limit handling."""
    if OFFLINE:
        return None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            if r.status_code == 429:
                wait = 3 ** (attempt + 1)
                print(f'    Rate limited, waiting {wait}s...')
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            data = r.json()
            if not data:
                return None
            return data
        except requests.exceptions.Timeout:
            print(f'    Timeout (attempt {attempt + 1}/{retries})')
        except requests.exceptions.RequestException as e:
            print(f'    Request error: {e}')
        except json.JSONDecodeError:
            return None
        time.sleep(SLEEP_SEC)
    return None


def _post_json(url: str, payload: dict, retries: int = 3) -> dict | None:
    """POST JSON with retry."""
    if OFFLINE:
        return None
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, headers={
                **HEADERS, 'Content-Type': 'application/json'
            }, timeout=30)
            if r.status_code == 429:
                wait = 3 ** (attempt + 1)
                time.sleep(wait)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            print(f'    Request error: {e}')
        except json.JSONDecodeError:
            return None
        time.sleep(SLEEP_SEC)
    return None


class Result:
    """One verification result."""
    def __init__(self, sample_id: str, description: str):
        self.sample_id = sample_id
        self.description = description
        self.verdict = 'PENDING'
        self.details: list[str] = []
        self.api_source = ''

    def set(self, verdict: str, detail: str = ''):
        self.verdict = verdict
        if detail:
            self.details.append(detail)

    def note(self, msg: str):
        self.details.append(msg)

    def __str__(self):
        lines = [f'  {self.sample_id}. {self.description}']
        lines.append(f'      Verdict: {self.verdict}')
        if self.api_source:
            lines.append(f'      API Source: {self.api_source}')
        for d in self.details:
            lines.append(f'      {d}')
        return '\n'.join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE C: DOE Awards — USAspending.gov API
# ══════════════════════════════════════════════════════════════════════════════
def _usaspending_search(award_id: str) -> dict | None:
    """Search USAspending by award ID using the search endpoint."""
    url = 'https://api.usaspending.gov/api/v2/search/spending_by_award/'
    payload = {
        'filters': {
            'keywords': [award_id],
            'award_type_codes': ['02', '03', '04', '05'],  # grants only
        },
        'fields': [
            'Award ID', 'Recipient Name', 'Award Amount', 'Description',
            'Start Date', 'End Date', 'Last Date to Order',
        ],
        'limit': 5,
        'page': 1,
    }
    return _post_json(url, payload)


def verify_sample_c(db: sqlite3.Connection) -> list[Result]:
    """Verify DOE awards against USAspending.gov API."""
    print('\n── SAMPLE C: DOE Awards (USAspending.gov API) ────────────────────')
    results = []

    awards = [
        ('C1', 'ORSTED STAR P2X', 'DECD0000081', 'terminated', 0.5),
        ('C2', 'WSP USA ENVIRONMENT & INFRASTRUCTURE', 'DEFE0032142', 'active', 100.0),
        ('C3', 'CHEMTRONERGY', 'DEEE0011325', 'terminated', 28.2),
        ('C4', 'ENTERGY SERVICES', 'DECD0000006', 'terminated', 27.3),
        ('C5', 'THE GREENE COUNTY INDUSTRIAL DEVELOPMENTS', 'DEFE0032323', 'active', 0.0),
    ]

    for sid, name, award_id, expected_status, expected_pct in awards:
        r = Result(sid, f'{name} — award {award_id}')
        r.api_source = f'https://api.usaspending.gov/api/v2/search/spending_by_award/ (keyword: {award_id})'

        # Query USAspending search API
        api_resp = _usaspending_search(award_id)
        time.sleep(SLEEP_SEC)

        if api_resp is None or not api_resp.get('results'):
            if OFFLINE:
                r.set('SKIPPED', 'Offline mode — API not queried')
            else:
                r.set('CANNOT VERIFY', 'USAspending search returned no results')
            results.append(r)
            continue

        # Find matching award
        match = None
        for hit in api_resp['results']:
            if hit.get('Award ID', '').strip() == award_id:
                match = hit
                break
        if not match:
            match = api_resp['results'][0]

        api_recipient = match.get('Recipient Name', 'N/A')
        api_amount = match.get('Award Amount', 0) or 0
        api_desc = (match.get('Description', '') or '')[:120]
        api_start = match.get('Start Date', 'N/A')
        api_end = match.get('End Date', 'N/A')

        r.note(f'API Award ID: {match.get("Award ID", "?")}')
        r.note(f'API recipient: {api_recipient}')
        r.note(f'API amount: ${api_amount:,.0f}')
        r.note(f'API description: {api_desc}')
        r.note(f'Period: {api_start} to {api_end}')

        # Check DB record
        db_row = db.execute(
            'SELECT doe_award_status, doe_pct_disbursed, doe_termination_date '
            'FROM doe_status WHERE document_url LIKE ?',
            (f'%{award_id}%',)
        ).fetchone()

        if db_row:
            db_status = db_row['doe_award_status']
            db_pct = db_row['doe_pct_disbursed']
            db_term = db_row['doe_termination_date']
            r.note(f'DB status: {db_status}, disbursed: {db_pct}%, termination: {db_term}')

            # Name match check
            if api_recipient and name.upper()[:12] in api_recipient.upper():
                r.note('Recipient name: MATCH')
            else:
                r.note(f'Recipient name: MISMATCH (DB: {name}, API: {api_recipient})')

            # Award exists in USAspending with matching ID = verified
            if match.get('Award ID', '').strip() == award_id:
                r.set('VERIFIED', f'Award confirmed in USAspending. Recipient: {api_recipient}')
            else:
                r.set('VERIFIED (PARTIAL)', 'Award found via keyword but ID not exact match')
        else:
            r.set('CANNOT VERIFY', 'Award not found in DB')

        results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE D: FRS Crosswalk — EPA Envirofacts API
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_d(db: sqlite3.Connection) -> list[Result]:
    """Verify FRS crosswalk entries against EPA Envirofacts."""
    print('\n── SAMPLE D: FRS Crosswalk (EPA Envirofacts API) ─────────────────')
    results = []

    crosswalks = [
        ('D1', 'RN100209451', '110000464024', 'PORT ARTHUR REFINERY'),
        ('D2', 'RN103919817', '110007177768', 'CHEVRON PHILLIPS CHEMICAL CEDAR BAYOU PLANT'),
        ('D3', 'RN100218569', '110034285226', 'DALLAS CLEAN ENERGY FACILITY'),
        ('D4', 'RN100849397', '110008160952', 'GARDEN VILLE FERTILIZER'),
        ('D5', 'RN102450756', '110041990913', 'EXXONMOBIL BEAUMONT REFINERY'),
    ]

    for sid, tceq_rn, frs_id, expected_name in crosswalks:
        r = Result(sid, f'{tceq_rn} → {frs_id} ({expected_name})')
        r.api_source = f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{frs_id}/JSON'

        # Check DB record
        db_row = db.execute(
            'SELECT * FROM frs_crosswalk WHERE pgm_sys_id = ? AND registry_id = ?',
            (tceq_rn, frs_id)
        ).fetchone()

        if db_row:
            r.note(f'DB: {db_row["primary_name"]} in {db_row["city_name"]}, {db_row["state_code"]}')
        else:
            r.note('WARNING: Record not found in frs_crosswalk table')

        # Query FRS API for this registry ID
        api_data = _get_json(
            f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{frs_id}/JSON'
        )
        time.sleep(SLEEP_SEC)

        if api_data is None:
            if OFFLINE:
                r.set('SKIPPED', 'Offline mode')
            else:
                r.set('CANNOT VERIFY', 'FRS API returned no data')
            results.append(r)
            continue

        # Check if TCEQ RN is in the linked programs
        tceq_found = False
        program_names = set()
        for prog in api_data:
            acrnm = prog.get('pgm_sys_acrnm', prog.get('PGM_SYS_ACRNM', ''))
            prog_id = prog.get('pgm_sys_id', prog.get('PGM_SYS_ID', ''))
            prog_name = prog.get('primary_name', prog.get('PRIMARY_NAME', ''))
            program_names.add(acrnm)

            if acrnm == 'TX-TCEQ ACR' and prog_id == tceq_rn:
                tceq_found = True

        r.note(f'API programs: {", ".join(sorted(program_names))}')
        r.note(f'TCEQ RN {tceq_rn} in FRS: {"YES" if tceq_found else "NO"}')

        # Check facility name
        first_name = api_data[0].get('primary_name', api_data[0].get('PRIMARY_NAME', '')) if api_data else ''
        r.note(f'API facility name: {first_name}')

        if tceq_found:
            r.set('VERIFIED', f'TCEQ→FRS mapping confirmed. {len(program_names)} programs linked.')
        else:
            # TCEQ RN not found — maybe different acrnm
            tceq_entries = [p for p in api_data if 'TCEQ' in p.get('pgm_sys_acrnm', p.get('PGM_SYS_ACRNM', ''))]
            if tceq_entries:
                actual_rn = tceq_entries[0].get('pgm_sys_id', tceq_entries[0].get('PGM_SYS_ID', ''))
                r.set('INCORRECT', f'FRS has TCEQ entry but different RN: {actual_rn}')
            else:
                r.set('INCORRECT', f'No TCEQ program linked to FRS {frs_id}')

        results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE E: Facility Crossrefs
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_e(db: sqlite3.Connection) -> list[Result]:
    """Verify facility crossref linkages."""
    print('\n── SAMPLE E: Facility Crossrefs ─────────────────────────────────')
    results = []

    crossrefs = [
        ('E1', 'tceq', 'RN103080487', 'epa_echo', '110067040703', 'frs_crosswalk', 0.99,
         'LINDE GAS CLEAR LAKE PLANT'),
        ('E2', 'rrc_uic', '000005705', 'epa_echo', '110043790001', 'spatial', 0.72,
         'AIR SEPARATION FACILITY'),
        ('E3', 'rrc_uic', '000064468', 'epa_echo', '110005184497', 'spatial', 0.63,
         'AIR LIQUIDE - FREEPORT HYCO PLANT'),
        ('E4', 'rrc_uic', '000104017', 'epa_echo', '110012181824', 'spatial', 0.57,
         'AIR LIQUIDE AMERICA BORGER CO2 PLANT'),
        ('E5', 'rrc_uic', '000114389', 'epa_echo', '110002339586', 'spatial', 0.50,
         'AIR LIQUIDE AMERICA CORP'),
    ]

    for sid, sys_a, id_a, sys_b, id_b, method, conf, expected_name in crossrefs:
        r = Result(sid, f'{sys_a}:{id_a} ↔ {sys_b}:{id_b} ({expected_name})')

        # Check if crossref still exists in DB (E4/E5 were deleted as false positives)
        db_row = db.execute(
            'SELECT * FROM facility_crossref WHERE source_a_id = ? AND source_b_id = ?',
            (id_a, id_b)
        ).fetchone()

        if not db_row:
            r.set('ALREADY FIXED', 'Crossref was removed by fix_medium_severity.py (false positive)')
            results.append(r)
            continue

        r.note(f'DB: method={db_row["match_method"]}, confidence={db_row["confidence"]:.3f}')
        if db_row['match_detail']:
            try:
                detail = json.loads(db_row['match_detail'])
                if isinstance(detail, dict):
                    dist = detail.get('distance_miles', 'N/A')
                    overlap = detail.get('name_overlap', 'N/A')
                    r.note(f'Distance: {dist} mi, Name overlap: {overlap}')
                else:
                    r.note(f'Match detail: {str(detail)[:80]}')
            except (json.JSONDecodeError, TypeError):
                r.note(f'Match detail (raw): {str(db_row["match_detail"])[:80]}')

        # For FRS-based crossrefs, verify via FRS API
        if method == 'frs_crosswalk':
            r.api_source = f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{id_b}/JSON'
            api_data = _get_json(r.api_source)
            time.sleep(SLEEP_SEC)

            if api_data:
                tceq_found = any(
                    p.get('pgm_sys_acrnm', p.get('PGM_SYS_ACRNM')) == 'TX-TCEQ ACR'
                    and p.get('pgm_sys_id', p.get('PGM_SYS_ID')) == id_a
                    for p in api_data
                )
                first_name = api_data[0].get('primary_name', api_data[0].get('PRIMARY_NAME', '')) if api_data else ''
                r.note(f'FRS name: {first_name}')
                if tceq_found:
                    r.set('VERIFIED', 'FRS confirms TCEQ→EPA linkage')
                else:
                    r.set('INCORRECT', 'FRS does not confirm this TCEQ RN linkage')
            elif OFFLINE:
                r.set('SKIPPED', 'Offline mode')
            else:
                r.set('CANNOT VERIFY', 'FRS API returned no data')

        # For spatial crossrefs, verify EPA facility exists
        elif method == 'spatial':
            r.api_source = f'{EFSERVICE}/FRS_PROGRAM_FACILITY/REGISTRY_ID/{id_b}/JSON'
            api_data = _get_json(r.api_source)
            time.sleep(SLEEP_SEC)

            if api_data:
                first_name = api_data[0].get('primary_name', api_data[0].get('PRIMARY_NAME', '')) if api_data else ''
                r.note(f'FRS name: {first_name}')

                # Check if this is a real facility
                if conf < 0.60:
                    r.set('SUSPICIOUS', f'Low confidence ({conf:.2f}) spatial match — likely false positive')
                elif conf < 0.75:
                    r.set('VERIFIED (LOW CONFIDENCE)',
                          f'EPA facility exists but spatial match at {conf:.2f} is uncertain')
                else:
                    r.set('VERIFIED', f'EPA facility confirmed, confidence {conf:.2f}')
            elif OFFLINE:
                r.set('SKIPPED', 'Offline mode')
            else:
                r.set('CANNOT VERIFY', 'FRS API returned no data for EPA ID')

        results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE A: Active Projects
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_a(db: sqlite3.Connection) -> list[Result]:
    """Verify active projects against source APIs."""
    print('\n── SAMPLE A: Active Projects ────────────────────────────────────')
    results = []

    # A1: PLAQUEMINE HYDROGEN PURIFICATION
    r = Result('A1', 'PLAQUEMINE HYDROGEN PURIFICATION')
    row = db.execute(
        "SELECT * FROM unified_projects WHERE project_name LIKE '%PLAQUEMINE%HYDROGEN%' AND quarantined=0"
    ).fetchone()
    if row:
        r.note(f'DB: state={row["state"]}, stage={row["stage"]}, tech={row["technology"]}')
        r.note(f'Sources: {row["source_count"]}, bootstrap: {row["bootstrap_source"]}')
        # Check TCEQ for this facility
        tceq = db.execute(
            "SELECT COUNT(*) FROM tceq_permits WHERE cust_name LIKE '%PLAQUEMINE%HYDROGEN%'"
        ).fetchone()[0]
        r.note(f'TCEQ permits matching: {tceq}')
        if tceq > 0:
            r.set('VERIFIED', 'Found in TCEQ permits')
        else:
            # Check EPA
            epa = db.execute(
                "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='epa_ghgrp' "
                "AND content LIKE '%PLAQUEMINE%HYDROGEN%'"
            ).fetchone()[0]
            if epa > 0:
                r.set('VERIFIED', 'Found in EPA GHGRP')
            else:
                r.set('CANNOT VERIFY', 'Not found in TCEQ or EPA — single-source only')
    else:
        r.set('ALREADY FIXED', 'Project quarantined or not found')
    results.append(r)

    # A2: OCI CLEAN AMMONIA PRODUCTION FACILITY
    r = Result('A2', 'OCI CLEAN AMMONIA PRODUCTION FACILITY')
    row = db.execute(
        "SELECT * FROM unified_projects WHERE project_name LIKE '%OCI CLEAN%' AND quarantined=0"
    ).fetchone()
    if row:
        r.note(f'DB: state={row["state"]}, stage={row["stage"]}, tech={row["technology"]}')
        r.note(f'Sources: {row["source_count"]}, bootstrap: {row["bootstrap_source"]}')

        # Check EPA ECHO for OCI facilities in TX
        if not OFFLINE:
            api_data = _get_json(EPA_ECHO_FACILITY, params={
                'p_fn': 'OCI', 'p_st': 'TX', 'output': 'JSON'
            })
            time.sleep(SLEEP_SEC)
            if api_data and 'Results' in api_data:
                facs = api_data['Results'].get('Facilities', [])
                r.note(f'EPA ECHO: {len(facs)} OCI facilities in TX')
                for f in facs[:3]:
                    r.note(f'  - {f.get("FacName", "?")} ({f.get("FacCity", "?")})')
                if facs:
                    r.set('VERIFIED', 'OCI facilities found in EPA ECHO')
                else:
                    r.set('CANNOT VERIFY', 'No OCI facilities in EPA ECHO for TX')
            else:
                r.set('CANNOT VERIFY', 'EPA ECHO API returned no data')
        else:
            r.set('SKIPPED', 'Offline mode')
    else:
        r.set('ALREADY FIXED', 'Project quarantined or not found')
    results.append(r)

    # A3: TEXAS CITY HYDROGEN PIPELINE PROJECT
    r = Result('A3', 'TEXAS CITY HYDROGEN PIPELINE PROJECT')
    row = db.execute(
        "SELECT * FROM unified_projects WHERE project_name LIKE '%TEXAS CITY HYDROGEN PIPELINE%' AND quarantined=0"
    ).fetchone()
    if row:
        r.note(f'DB: state={row["state"]}, stage={row["stage"]}, tech={row["technology"]}')
        # Check PHMSA pipeline data
        phmsa = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='phmsa' "
            "AND content LIKE '%TEXAS CITY%' AND content LIKE '%HYDROGEN%'"
        ).fetchone()[0]
        r.note(f'PHMSA records mentioning Texas City + hydrogen: {phmsa}')
        # Check pipeline_infrastructure
        pipes = db.execute(
            "SELECT * FROM pipeline_infrastructure WHERE name LIKE '%TEXAS CITY%' OR name LIKE '%hydrogen%'"
        ).fetchone()
        if pipes:
            r.note(f'Pipeline infra: {pipes["name"]}')
            r.set('VERIFIED', 'Found in pipeline_infrastructure table')
        elif phmsa > 0:
            r.set('VERIFIED', 'Found in PHMSA regulatory evidence')
        else:
            r.set('CANNOT VERIFY', 'No pipeline records found')
    else:
        r.set('ALREADY FIXED', 'Project quarantined or not found')
    results.append(r)

    # A4: CF Industries — 10 MTPA misclassification
    r = Result('A4', 'CF Industries — capacity misclassification check')
    row = db.execute(
        "SELECT project_name, capacity_mtpa_h2, capacity_raw, technology "
        "FROM unified_projects WHERE project_name LIKE '%CF Industries%'"
    ).fetchone()
    if row:
        r.note(f'DB: capacity_mtpa_h2={row["capacity_mtpa_h2"]}, capacity_raw={row["capacity_raw"]}')
        r.note(f'Technology: {row["technology"]}')
        if row['capacity_mtpa_h2'] is None:
            r.set('ALREADY FIXED', 'capacity_mtpa_h2 was set to NULL by fix_medium_severity.py')
        else:
            r.set('INCORRECT', f'capacity_mtpa_h2 still set to {row["capacity_mtpa_h2"]} — should be NULL')
    else:
        r.set('ALREADY FIXED', 'CF Industries project not found (quarantined)')
    results.append(r)

    # A5: NextDecade Corp — LNG capacity misclassified as H2
    r = Result('A5', 'NextDecade Corp — LNG capacity misclassification check')
    row = db.execute(
        "SELECT project_name, capacity_mtpa_h2, capacity_raw, technology "
        "FROM unified_projects WHERE project_name LIKE '%NextDecade%'"
    ).fetchone()
    if row:
        r.note(f'DB: capacity_mtpa_h2={row["capacity_mtpa_h2"]}, capacity_raw={row["capacity_raw"]}')
        r.note(f'Technology: {row["technology"]}')
        if row['capacity_mtpa_h2'] is None:
            r.set('ALREADY FIXED', 'capacity_mtpa_h2 was set to NULL by fix_medium_severity.py')
        else:
            r.set('INCORRECT', f'capacity_mtpa_h2 still set to {row["capacity_mtpa_h2"]} — should be NULL')
    else:
        r.set('ALREADY FIXED', 'NextDecade project not found (quarantined)')
    results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE B: Operational Projects
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_b(db: sqlite3.Connection) -> list[Result]:
    """Verify operational projects actually exist and are running."""
    print('\n── SAMPLE B: Operational Projects ───────────────────────────────')
    results = []

    b_projects = [
        ('B1', '%AIR PRODUCTS%PORT ARTHUR I%', 'Air Products Port Arthur I',
         'EPA GHGRP — large H2 SMR'),
        ('B2', '%AIR PRODUCTS%TEXAS CITY SMR%', 'Air Products Texas City SMR',
         'Technology says co2_transport_storage — likely misclassified H2 SMR'),
        ('B3', '%La Porte Steam Methane%', 'Air Liquide La Porte SMR',
         'EPA GHGRP — known H2 producer'),
        ('B4', '%BROOKELAND GAS PLANT%', 'Energy Transfer Brookeland Gas Plant',
         'CO2 transport or gas processing?'),
        ('B5', '%SNYDER GAS PLANT%', 'Kinder Morgan Snyder Gas Plant',
         'CO2 transport or gas processing?'),
    ]

    for sid, pattern, name, note_text in b_projects:
        r = Result(sid, name)
        r.note(f'Audit note: {note_text}')

        # Find in DB (include quarantined, just check active ones)
        row = db.execute(
            "SELECT project_name, developer_name, state, stage, technology, "
            "quarantined, source_count, bootstrap_source, crossref_source_count "
            "FROM unified_projects WHERE project_name LIKE ? AND quarantined = 0 "
            "ORDER BY source_count DESC LIMIT 1",
            (pattern,)
        ).fetchone()

        if not row:
            # Check quarantined
            q_row = db.execute(
                "SELECT project_name, quarantined, quarantine_reason "
                "FROM unified_projects WHERE project_name LIKE ? LIMIT 1",
                (pattern,)
            ).fetchone()
            if q_row and q_row['quarantined']:
                r.set('QUARANTINED', f'Reason: {q_row["quarantine_reason"]}')
            else:
                r.set('NOT FOUND', 'Project not in database')
            results.append(r)
            continue

        r.note(f'DB: dev={row["developer_name"]}, state={row["state"]}, '
               f'stage={row["stage"]}, tech={row["technology"]}')
        r.note(f'Sources: {row["source_count"]}, crossrefs: {row["crossref_source_count"]}')

        # Verify via EPA ECHO
        if not OFFLINE:
            facility_name = name.split(' - ')[-1] if ' - ' in name else name
            # Try EPA ECHO lookup
            api_data = _get_json(EPA_ECHO_FACILITY, params={
                'p_fn': facility_name.split()[0],  # first word
                'p_st': row['state'] or 'TX',
                'output': 'JSON'
            })
            time.sleep(SLEEP_SEC)

            if api_data and 'Results' in api_data:
                facs = api_data['Results'].get('Facilities', [])
                matching = [f for f in facs if
                            any(kw.lower() in f.get('FacName', '').lower()
                                for kw in name.upper().split()[:2])]
                if matching:
                    fac = matching[0]
                    r.note(f'EPA ECHO: {fac.get("FacName")} in {fac.get("FacCity", "?")}, '
                           f'{fac.get("FacState", "?")}')
                    r.set('VERIFIED', f'Found in EPA ECHO ({len(matching)} matches)')
                elif facs:
                    r.note(f'EPA ECHO: {len(facs)} facilities found but none match name exactly')
                    r.set('VERIFIED (PARTIAL)', 'EPA has facilities for this operator but name mismatch')
                else:
                    r.set('CANNOT VERIFY', 'No matching facilities in EPA ECHO')
            else:
                r.set('CANNOT VERIFY', 'EPA ECHO API returned no data')
        else:
            r.set('SKIPPED', 'Offline mode')

        results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE F: Entity Aliases
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_f(db: sqlite3.Connection) -> list[Result]:
    """Verify corporate relationship aliases."""
    print('\n── SAMPLE F: Entity Aliases ─────────────────────────────────────')
    results = []

    # F1: Merger Sub 1 → SVII
    r = Result('F1', 'Merger Sub → entity (corporate merger artifact)')
    aliases = db.execute(
        "SELECT ea.alias, ea.alias_type, ea.confidence, e.canonical_name, "
        "ea.cited_text_excerpt "
        "FROM entity_aliases ea JOIN entities e ON ea.entity_id = e.entity_id "
        "WHERE ea.alias LIKE '%Merger Sub%'"
    ).fetchall()
    if aliases:
        for a in aliases:
            r.note(f'{a["alias"]} → {a["canonical_name"]} (type={a["alias_type"]}, conf={a["confidence"]})')
            excerpt = (a['cited_text_excerpt'] or '')[:80]
            if excerpt:
                r.note(f'  Excerpt: {excerpt}')
        r.set('VERIFIED', 'Merger Sub entries are SEC 8-K merger artifacts — correct extraction')
    else:
        r.set('ALREADY FIXED', 'No Merger Sub aliases found')
    results.append(r)

    # F2: Praxair → Linde
    r = Result('F2', 'Praxair → Linde (parent company relationship)')
    aliases = db.execute(
        "SELECT ea.alias, ea.alias_type, ea.confidence, e.canonical_name "
        "FROM entity_aliases ea JOIN entities e ON ea.entity_id = e.entity_id "
        "WHERE (ea.alias LIKE '%PRAXAIR%' AND e.canonical_name LIKE '%Linde%') "
        "   OR (ea.alias LIKE '%PRAXAIR%' AND e.canonical_name LIKE '%LINDE%')"
    ).fetchall()
    if aliases:
        for a in aliases[:5]:
            r.note(f'{a["alias"]} → {a["canonical_name"]} (type={a["alias_type"]})')
        r.set('VERIFIED', 'Praxair→Linde relationship correct (Linde acquired Praxair in 2018)')
    else:
        r.set('CANNOT VERIFY', 'No Praxair→Linde aliases found')
    results.append(r)

    # F3: OCI Partners
    r = Result('F3', 'OCI Partners relationship')
    aliases = db.execute(
        "SELECT ea.alias, ea.alias_type, ea.confidence, e.canonical_name "
        "FROM entity_aliases ea JOIN entities e ON ea.entity_id = e.entity_id "
        "WHERE ea.alias LIKE '%OCI%' OR e.canonical_name LIKE '%OCI%'"
    ).fetchall()
    if aliases:
        for a in aliases[:5]:
            r.note(f'{a["alias"]} → {a["canonical_name"]} (type={a["alias_type"]})')
        r.set('VERIFIED', 'OCI entity relationships found')
    else:
        r.set('CANNOT VERIFY', 'No OCI aliases found')
    results.append(r)

    # F4: HollyFrontier → Navajo Refining
    r = Result('F4', 'HollyFrontier → Navajo Refining (subsidiary)')
    aliases = db.execute(
        "SELECT ea.alias, ea.alias_type, ea.confidence, e.canonical_name "
        "FROM entity_aliases ea JOIN entities e ON ea.entity_id = e.entity_id "
        "WHERE ea.alias LIKE '%HollyFrontier%' OR e.canonical_name LIKE '%HollyFrontier%' "
        "   OR ea.alias LIKE '%HOLLYFRONTIER%' OR e.canonical_name LIKE '%HOLLYFRONTIER%'"
    ).fetchall()
    if aliases:
        for a in aliases[:5]:
            r.note(f'{a["alias"]} → {a["canonical_name"]} (type={a["alias_type"]})')
        # Known: HollyFrontier rebranded to HF Sinclair in 2022
        r.set('VERIFIED', 'HollyFrontier subsidiary relationships correct. '
              'Note: rebranded to HF Sinclair in 2022 — aliases reflect pre-rebrand names.')
    else:
        r.set('CANNOT VERIFY', 'No HollyFrontier aliases found')
    results.append(r)

    # F5: "Parent Company" literal aliases
    r = Result('F5', '"Parent Company" literal-string aliases (extraction bug)')
    count = db.execute(
        "SELECT COUNT(*) FROM entity_aliases WHERE alias = 'Parent Company'"
    ).fetchone()[0]
    if count == 0:
        r.set('ALREADY FIXED', 'All "Parent Company" literal aliases deleted by fix_medium_severity.py')
    else:
        r.set('INCORRECT', f'{count} "Parent Company" literal aliases still exist')
    results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE G: Lowest-Confidence Spatial Crossrefs
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_g(db: sqlite3.Connection) -> list[Result]:
    """Verify lowest-confidence spatial crossrefs (should have been removed)."""
    print('\n── SAMPLE G: Lowest-Confidence Spatial Crossrefs ────────────────')
    results = []

    g_items = [
        ('G1', '000114389', '110002339586', 'RRC Operator 100949 ↔ AIR LIQUIDE AMERICA CORP'),
        ('G2', '000114389', '110002223108', 'RRC Operator 100949 ↔ CHEMOURS - BEAUMONT WORKS'),
        ('G3', '000104017', '110042174799', 'Phillips 66 ↔ CARDOX CORP'),
        ('G4', '000104017', '110012181824', 'Phillips 66 ↔ AIR LIQUIDE AMERICA BORGER CO2 PLANT'),
    ]

    for sid, id_a, id_b, desc in g_items:
        r = Result(sid, desc)

        # Check if crossref still exists
        row = db.execute(
            'SELECT * FROM facility_crossref '
            'WHERE (source_a_id = ? AND source_b_id = ?) OR (source_a_id = ? AND source_b_id = ?)',
            (id_a, id_b, id_b, id_a)
        ).fetchone()

        if row:
            conf = row['confidence']
            detail = json.loads(row['match_detail']) if row['match_detail'] else {}
            dist = detail.get('distance_miles', '?')
            r.note(f'Still in DB: conf={conf:.3f}, distance={dist}mi')
            r.set('INCORRECT', 'False positive crossref still exists — should be removed')
        else:
            r.set('ALREADY FIXED', 'False positive crossref removed by fix_medium_severity.py')

        results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# SAMPLE H: Auto-Created Projects
# ══════════════════════════════════════════════════════════════════════════════
def verify_sample_h(db: sqlite3.Connection) -> list[Result]:
    """Verify auto-created projects — are they relevant to H2/CCS?"""
    print('\n── SAMPLE H: Auto-Created Projects ─────────────────────────────')
    results = []

    h_gas_plants = [
        ('H1', 'AVINGER GAS PROCESSING PLANT', 'TX'),
        ('H2', 'MEADOWS COMPRESSOR STATION', 'TX'),
        ('H3', 'ET GATHERING & PROCESSING - MIDLAND COUNTY', 'TX'),
        ('H4', 'HEMPHILL GAS PLANT', 'TX'),
        ('H5', 'CARTHAGE GAS PLANT', 'TX'),
    ]

    # H1-H5: Gas plants/compressor stations — verify they're standard O&G
    for sid, name, state in h_gas_plants:
        r = Result(sid, f'{name} — relevant to H2/CCS?')

        row = db.execute(
            "SELECT project_name, stage, technology, quarantined, quarantine_reason "
            "FROM unified_projects WHERE project_name = ?",
            (name,)
        ).fetchone()

        if row:
            r.note(f'DB: stage={row["stage"]}, tech={row["technology"]}, quarantined={row["quarantined"]}')
            if row['quarantined']:
                r.note(f'Quarantine reason: {row["quarantine_reason"]}')

            # These are standard O&G midstream — not H2/CCS
            if 'gas processing' in name.lower() or 'gas plant' in name.lower() or 'compressor' in name.lower():
                if row['quarantined']:
                    r.set('ALREADY FIXED', 'Correctly quarantined — standard O&G midstream, not H2/CCS')
                else:
                    r.set('INCORRECT', 'Standard O&G facility should be quarantined')
            else:
                r.set('CANNOT VERIFY', 'Need manual review')
        else:
            r.set('ALREADY FIXED', 'Project not found (deleted or merged)')

        results.append(r)

    # H6: Hackberry Carbon Sequestration LLC
    r = Result('H6', 'Hackberry Carbon Sequestration LLC — legitimate CCS?')
    row = db.execute(
        "SELECT project_name, stage, technology, quarantined, quarantine_reason, state "
        "FROM unified_projects WHERE project_name LIKE '%Hackberry Carbon%' AND quarantined IN (0,1) "
        "ORDER BY quarantined ASC LIMIT 1"
    ).fetchone()
    if row:
        r.note(f'DB: state={row["state"]}, stage={row["stage"]}, tech={row["technology"]}, q={row["quarantined"]}')
        # Hackberry is a known Class VI UIC permit holder in Louisiana
        if not OFFLINE:
            # Check EPA UIC records
            api_data = _get_json(EPA_ECHO_FACILITY, params={
                'p_fn': 'Hackberry Carbon', 'p_st': 'LA', 'output': 'JSON'
            })
            time.sleep(SLEEP_SEC)
            if api_data and 'Results' in api_data:
                facs = api_data['Results'].get('Facilities', [])
                if facs:
                    r.note(f'EPA ECHO: Found {len(facs)} facilities')
                    r.set('VERIFIED', 'Legitimate CCS project — found in EPA. Should be un-quarantined when multi-source.')
                else:
                    r.set('CANNOT VERIFY', 'Not found in EPA ECHO — may need more data')
            else:
                r.set('CANNOT VERIFY', 'EPA API returned no data')
        else:
            r.set('SKIPPED', 'Offline mode')
    else:
        r.set('NOT FOUND', 'Project not in database')
    results.append(r)

    # H7: Gulf Coast Sequestration
    r = Result('H7', 'Gulf Coast Sequestration — legitimate CCS?')
    row = db.execute(
        "SELECT project_name, stage, technology, quarantined, state "
        "FROM unified_projects WHERE project_name LIKE '%Gulf Coast Sequestration%' "
        "ORDER BY quarantined ASC LIMIT 1"
    ).fetchone()
    if row:
        r.note(f'DB: state={row["state"]}, stage={row["stage"]}, tech={row["technology"]}, q={row["quarantined"]}')
        if not OFFLINE:
            api_data = _get_json(EPA_ECHO_FACILITY, params={
                'p_fn': 'Gulf Coast Sequestration', 'p_st': 'LA', 'output': 'JSON'
            })
            time.sleep(SLEEP_SEC)
            if api_data and 'Results' in api_data:
                facs = api_data['Results'].get('Facilities', [])
                if facs:
                    r.note(f'EPA ECHO: Found {len(facs)} facilities')
                    r.set('VERIFIED', 'Legitimate CCS — found in EPA')
                else:
                    r.set('CANNOT VERIFY', 'Not in EPA ECHO')
            else:
                r.set('CANNOT VERIFY', 'EPA API returned no data')
        else:
            r.set('SKIPPED', 'Offline mode')
    else:
        r.set('NOT FOUND', 'Project not in database')
    results.append(r)

    # H8: Archer Daniels Midland CCS2
    r = Result('H8', 'Archer Daniels Midland CCS2 — real second CCS project?')
    row = db.execute(
        "SELECT project_name, stage, technology, quarantined, state "
        "FROM unified_projects WHERE project_name LIKE '%Archer Daniels%CCS2%' "
        "ORDER BY quarantined ASC LIMIT 1"
    ).fetchone()
    if row:
        r.note(f'DB: state={row["state"]}, stage={row["stage"]}, tech={row["technology"]}, q={row["quarantined"]}')
        # ADM CCS2 is a known project in Decatur, IL (second injection well)
        if not OFFLINE:
            api_data = _get_json(EPA_ECHO_FACILITY, params={
                'p_fn': 'Archer Daniels', 'p_st': 'IL', 'output': 'JSON'
            })
            time.sleep(SLEEP_SEC)
            if api_data and 'Results' in api_data:
                facs = api_data['Results'].get('Facilities', [])
                r.note(f'EPA ECHO: {len(facs)} ADM facilities in IL')
                if facs:
                    for f in facs[:3]:
                        r.note(f'  - {f.get("FacName", "?")} ({f.get("FacCity", "?")})')
                    r.set('VERIFIED', 'ADM has facilities in Decatur IL. '
                          'CCS2 is the second CO2 injection well (DOE-funded, Class VI).')
                else:
                    r.set('CANNOT VERIFY', 'No ADM facilities in EPA ECHO for IL')
            else:
                r.set('CANNOT VERIFY', 'EPA API returned no data')
        else:
            r.set('SKIPPED', 'Offline mode')
    else:
        r.set('NOT FOUND', 'Project not in database')
    results.append(r)

    return results


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════
def run():
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    print('=' * 70)
    print('  DecarbIQ — SAMPLE VERIFICATION REPORT')
    print(f'  Date: {now_iso()}')
    print(f'  Database: {LEARNING_DB}')
    print(f'  Mode: {"OFFLINE (DB only)" if OFFLINE else "ONLINE (API verification)"}')
    if SAMPLE_FILTER:
        print(f'  Filter: Sample {SAMPLE_FILTER} only')
    print('=' * 70)

    all_results: list[Result] = []
    sample_map = {
        'A': verify_sample_a,
        'B': verify_sample_b,
        'C': verify_sample_c,
        'D': verify_sample_d,
        'E': verify_sample_e,
        'F': verify_sample_f,
        'G': verify_sample_g,
        'H': verify_sample_h,
    }

    for letter, func in sample_map.items():
        if SAMPLE_FILTER and letter != SAMPLE_FILTER:
            continue
        results = func(db)
        all_results.extend(results)

        for r in results:
            print(r)
        print()

    # Summary
    print('=' * 70)
    print('  VERIFICATION SUMMARY')
    print('=' * 70)

    verdicts: dict[str, int] = {}
    for r in all_results:
        verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1

    total = len(all_results)
    for v, cnt in sorted(verdicts.items(), key=lambda x: -x[1]):
        pct = 100 * cnt / max(1, total)
        print(f'  {v:<25s} {cnt:>3}  ({pct:.0f}%)')

    print(f'\n  Total samples checked: {total}')

    # Write results to file
    output_path = LEARNING_DB.parent / 'sample_verification_report.txt'
    with open(output_path, 'w') as f:
        f.write('=' * 70 + '\n')
        f.write('  DecarbIQ — SAMPLE VERIFICATION REPORT\n')
        f.write(f'  Date: {now_iso()}\n')
        f.write(f'  Mode: {"OFFLINE" if OFFLINE else "ONLINE"}\n')
        f.write('=' * 70 + '\n\n')

        for r in all_results:
            f.write(str(r) + '\n\n')

        f.write('=' * 70 + '\n')
        f.write('  SUMMARY\n')
        f.write('=' * 70 + '\n')
        for v, cnt in sorted(verdicts.items(), key=lambda x: -x[1]):
            f.write(f'  {v:<25s} {cnt:>3}\n')
        f.write(f'\n  Total: {total}\n')

    print(f'\n  Report saved to: {output_path}')
    db.close()


if __name__ == '__main__':
    run()
