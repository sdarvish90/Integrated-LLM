#!/usr/bin/env python3
"""
DecarbIQ — collect_epa_permits.py
Fetch EPA environmental permits for blue H2 / blue ammonia facilities in Texas.

Uses EPA ECHO REST API (public, no auth) with four data sources:
  Source A: NAICS-based discovery — TX facilities by industry code
  Source B: Name-based targeted — developer names from unified_projects
  Source C: DFR enrichment — Detailed Facility Reports for structured permit data
  Source D: GHGRP enrichment — parent company from GHG Reporting Program

Writes structured data to:
  - regulatory_evidence: facility records + operator_name/llc_entity/parent_company/stage/technology
  - epa_permits: per-permit rows with status, areas, dates
  - epa_compliance_summary: compliance & enforcement by statute

Usage:
    python collect_epa_permits.py              # full run: A + B + C + D
    python collect_epa_permits.py --dry-run    # show what would be inserted
    python collect_epa_permits.py --stats      # show current EPA coverage
    python collect_epa_permits.py --naics      # Source A only
    python collect_epa_permits.py --names      # Source B only
    python collect_epa_permits.py --dfr        # Source C only (DFR enrichment)
    python collect_epa_permits.py --ghgrp      # Source D only (GHGRP parent)
    python collect_epa_permits.py --dfr --re-enrich  # re-process already-enriched
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import requests
from groq import Groq

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_SLEEP_BETWEEN_CALLS,
)

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── EPA ECHO API ─────────────────────────────────────────────────────────────
EPA_FACILITY_URL = 'https://echodata.epa.gov/echo/echo_rest_services.get_facility_info'
EPA_DFR_URL      = 'https://echodata.epa.gov/echo/dfr_rest_services.get_dfr'
UA               = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC        = 0.5

# ── NAICS codes for blue H2 / blue ammonia / CCS ────────────────────────────
NAICS_CODES = {
    '325120': 'Industrial gas manufacturing (hydrogen)',
    '325311': 'Nitrogenous fertilizer manufacturing (ammonia)',
    '325188': 'Other basic inorganic chemical manufacturing',
    '325194': 'Cyclic crude/intermediate organic chemical mfg',
    '211130': 'Natural gas extraction (SMR feedstock)',
    '221210': 'Natural gas distribution (H2 blending)',
    '486210': 'Pipeline transportation of natural gas (CO2/CCS)',
    '541712': 'R&D in physical sciences (pilot/demo H2 plants)',
}

# ── CLI flags ────────────────────────────────────────────────────────────────
DRY_RUN    = '--dry-run'    in sys.argv
STATS_ONLY = '--stats'     in sys.argv
NAICS_ONLY = '--naics'     in sys.argv
NAMES_ONLY = '--names'     in sys.argv
DFR_ONLY   = '--dfr'       in sys.argv
NO_FILTER  = '--no-filter' in sys.argv

# ── LLM filter settings ──────────────────────────────────────────────────────
FILTER_BATCH_SIZE = 40

FILTER_SYSTEM = """\
You filter EPA-registered facilities for relevance to blue hydrogen, \
blue ammonia, and carbon capture/storage (CCS) projects.

RELEVANT — include if the facility is plausibly involved in:
- Hydrogen production (SMR, ATR, electrolysis, gasification, hyco plants)
- Ammonia production or manufacturing
- Carbon capture, utilization, or storage (CCUS/CCS)
- CO2 pipeline, injection wells, or sequestration
- Industrial gas manufacturing (hydrogen, syngas, oxygen for gasification)
- Major refinery or petrochemical operations that produce hydrogen
- Large-scale natural gas processing (potential SMR feedstock)
- Fertilizer plants (ammonia-based)

NOT RELEVANT — exclude:
- Gas stations, convenience stores, retail fuel outlets
- Small natural gas wells, pump jacks, field compressor stations
- Recycling centers, waste facilities, landfills
- Chemical distributors or suppliers (not manufacturers)
- Welding supply shops, auto care, car washes
- Small drilling operations, well sites, tank batteries
- Generic offices, warehouses, or storage yards

Return ONLY a JSON array of the facility numbers that ARE relevant.
Example: [1, 4, 7]
If none are relevant, return: []
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── HTTP helper ──────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = 3) -> dict | None:
    """GET with rate-limit retry. Returns parsed JSON or None."""
    headers = {'User-Agent': UA, 'Accept-Encoding': 'gzip, deflate'}
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f'  [rate limit] sleeping {wait}s')
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f'  [fetch error] {e}')
                return None
            time.sleep(1)
    return None


# ── Facility text formatting ────────────────────────────────────────────────

def classify_permit_type(fac: dict) -> str:
    """Classify primary permit type from facility flags."""
    if fac.get('GHGIDs'):
        return 'ghg_report'
    caa = (fac.get('CAAComplianceStatus') or '')
    cwa = (fac.get('CWAComplianceStatus') or '')
    rcra = (fac.get('RCRAComplianceStatus') or '')
    if caa and caa not in ('Not Applicable', 'Not a CAAFacility'):
        return 'air_permit'
    if cwa and cwa not in ('Not Applicable', 'Not a CWAFacility'):
        return 'water_permit'
    if rcra and rcra not in ('Not Applicable', 'Not a RCRAFacility'):
        return 'rcra_permit'
    return 'epa_facility'


def _best_date(fac: dict) -> str:
    """Extract the best available date from facility data."""
    for key in ('FacDateLastPenalty', 'FacDateLastInspection',
                'FacDateLastFormalAct', 'FacDateLastInformalAct'):
        val = fac.get(key)
        if val and val.strip() and val.strip() != 'None':
            # EPA dates are often MM/DD/YYYY — normalize to YYYY-MM-DD
            try:
                dt = datetime.strptime(val.strip(), '%m/%d/%Y')
                return dt.strftime('%Y-%m-%d')
            except ValueError:
                pass
            # Already in YYYY-MM-DD?
            if re.match(r'^\d{4}-\d{2}-\d{2}$', val.strip()):
                return val.strip()
    return now_iso()[:10]


def format_facility_excerpt(fac: dict, matched_project: str = None) -> str:
    """Format EPA facility into structured text excerpt.
    Location data placed FIRST for LLM extraction priority.
    """
    parts = []

    if matched_project:
        parts.append(f'Matched Project: {matched_project}')
        parts.append('')

    # Location block — most important for downstream matching
    parts.append(f"EPA Facility: {fac.get('FacName') or 'Unknown'}")
    city = fac.get('FacCity') or ''
    county = fac.get('FacCounty') or ''
    state = fac.get('FacState') or 'TX'
    county_clean = re.sub(r'\s*county\s*$', '', county, flags=re.I).strip()
    parts.append(f"Location: {city}, {county_clean} County, {state}")
    parts.append(f"Address: {fac.get('FacStreet') or ''}, {city}, {state} {fac.get('FacZip') or ''}")
    lat = fac.get('FacLat') or ''
    lon = fac.get('FacLong') or ''
    if lat and lon:
        parts.append(f"Coordinates: {lat}, {lon}")
    parts.append(f"EPA Registry ID: {fac.get('RegistryID', '')}")

    # Classification
    naics = fac.get('FacNAICSCodes') or ''
    sic = fac.get('FacSICCodes') or ''
    if naics:
        parts.append(f"NAICS Codes: {naics}")
    if sic:
        parts.append(f"SIC Codes: {sic}")

    # Compliance status — permit intelligence
    for label, key in [
        ('Overall Compliance', 'FacComplianceStatus'),
        ('Clean Air Act Status', 'CAAComplianceStatus'),
        ('Clean Water Act Status', 'CWAComplianceStatus'),
        ('RCRA Status', 'RCRAComplianceStatus'),
        ('SDWA Status', 'SDWAComplianceStatus'),
    ]:
        val = fac.get(key) or ''
        if val and val not in ('Not Applicable', 'Not a CAAFacility',
                                'Not a CWAFacility', 'Not a RCRAFacility'):
            parts.append(f"{label}: {val}")

    # Violation & enforcement signals
    if fac.get('FacSNCFlg') == 'Y':
        parts.append("SIGNIFICANT NON-COMPLIANCE FLAG: Yes")
    qtrs = fac.get('FacQtrsWithNC') or ''
    if qtrs and str(qtrs) != '0':
        parts.append(f"Quarters with Non-Compliance: {qtrs}")
    penalties = fac.get('FacPenaltyCount') or ''
    if penalties and str(penalties) != '0':
        parts.append(f"Penalties: {penalties}")

    # Environmental program flags
    if fac.get('AIRFlag') == 'Y':
        parts.append("Has Air Permits: Yes")
    ghg_ids = fac.get('GHGIDs') or ''
    if ghg_ids:
        parts.append(f"GHG Reporting IDs: {ghg_ids}")
    if fac.get('TRIFlag') == 'Y':
        tri = fac.get('TRIReleasesTransfers') or ''
        parts.append(f"TRI Releases/Transfers: {tri}" if tri else "TRI Reporting: Yes")

    return '\n'.join(parts)


# ── Name extraction for EPA search ──────────────────────────────────────────

def _extract_search_names(developer_name: str) -> list[str]:
    """Extract EPA-searchable facility name variants from developer_name."""
    name = developer_name.strip()
    terms = [name]

    words = name.split()
    if len(words) > 1:
        terms.append(words[0])
    if len(words) > 2:
        terms.append(' '.join(words[:2]))

    stripped = re.sub(
        r'\s+(FACILITY|PLANT|UNIT|TERMINAL|PROJECT|PIPELINE|PRODUCTION|'
        r'LLC|INC|CORP|CORPORATION|LP|LTD)\.?$',
        '', name, flags=re.IGNORECASE
    ).strip()
    if stripped != name and stripped:
        terms.append(stripped)

    return list(dict.fromkeys(terms))


# ── LLM relevance filter ──────────────────────────────────────────────────────

def _call_filter_llm(claude_client, groq_client, user_msg: str) -> list[int]:
    """Call LLM to filter facilities. Returns list of relevant facility numbers."""
    raw = None

    if claude_client:
        try:
            resp = claude_client.messages.create(
                model=CLAUDE_MODEL,
                system=FILTER_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
                temperature=0.0,
                max_tokens=512,
            )
            raw = resp.content[0].text
        except Exception as e:
            if groq_client:
                print(f'    [filter] Claude failed ({str(e)[:40]}), trying Groq')
            else:
                raise

    if raw is None and groq_client:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": FILTER_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        raw = resp.choices[0].message.content
        time.sleep(GROQ_SLEEP_BETWEEN_CALLS)

    if not raw:
        return []

    # Parse JSON array from response
    raw = raw.strip()
    raw = re.sub(r'```(?:json)?\s*\n?', '', raw)
    raw = re.sub(r'\n?```', '', raw)
    raw = raw.strip()

    # Find the array
    start = raw.find('[')
    end = raw.rfind(']')
    if start != -1 and end > start:
        try:
            result = json.loads(raw[start:end + 1])
            if isinstance(result, list):
                return [x for x in result if isinstance(x, int)]
        except json.JSONDecodeError:
            pass
    return []


def filter_relevant(facilities: list[dict], claude_client, groq_client) -> list[dict]:
    """Use LLM to filter facilities for H2/ammonia/CCS relevance.
    Returns filtered list of facility dicts.
    """
    if NO_FILTER or not facilities:
        return facilities

    if not claude_client and not groq_client:
        return facilities

    # Build compact facility summaries for LLM
    entries = []
    for i, fac in enumerate(facilities):
        name = fac.get('FacName') or 'Unknown'
        city = fac.get('FacCity') or ''
        naics = fac.get('FacNAICSCodes') or ''
        entries.append(f"{i+1}. {name} | {city}, TX | NAICS: {naics}")

    # Process in batches
    relevant_indices = set()
    for batch_start in range(0, len(entries), FILTER_BATCH_SIZE):
        batch = entries[batch_start:batch_start + FILTER_BATCH_SIZE]
        user_msg = "Which facilities are relevant?\n\n" + "\n".join(batch)

        try:
            nums = _call_filter_llm(claude_client, groq_client, user_msg)
            relevant_indices.update(nums)
        except Exception as e:
            print(f'    [filter error] {str(e)[:60]} — accepting batch as-is')
            for j in range(batch_start + 1, batch_start + len(batch) + 1):
                relevant_indices.add(j)

        time.sleep(SLEEP_SEC)

    # Map back to facilities (1-indexed)
    filtered = [fac for i, fac in enumerate(facilities) if (i + 1) in relevant_indices]
    rejected = len(facilities) - len(filtered)
    if rejected > 0:
        print(f'    LLM filter: {len(filtered)} relevant, {rejected} rejected')
    return filtered


# ── Source A: NAICS-based discovery ──────────────────────────────────────────

def collect_naics_facilities(db: sqlite3.Connection, existing_urls: set,
                             claude_client=None, groq_client=None) -> tuple[int, int]:
    """Search EPA ECHO for TX facilities by NAICS codes."""
    print('\n── Source A: NAICS-based discovery ─────────────────────────────')
    inserted = skipped_dup = 0

    for naics, description in NAICS_CODES.items():
        print(f'\n  NAICS {naics}: {description}')

        data = _get(EPA_FACILITY_URL, params={
            'p_st': 'TX',
            'p_ncs': naics,
            'output': 'JSON',
        })
        if not data:
            print(f'    No response')
            continue

        results = data.get('Results', {})
        facilities = results.get('Facilities', [])
        query_rows = results.get('QueryRows', '0')
        print(f'    {query_rows} matches, {len(facilities)} returned')

        # LLM relevance filter
        facilities = filter_relevant(facilities, claude_client, groq_client)

        for fac in facilities:
            registry_id = fac.get('RegistryID') or ''
            if not registry_id:
                continue

            doc_url = f'https://echo.epa.gov/detailed-facility-report?fid={registry_id}'
            if doc_url in existing_urls:
                skipped_dup += 1
                continue

            fac_name = fac.get('FacName') or 'Unknown'
            doc_type = classify_permit_type(fac)
            doc_date = _best_date(fac)
            text = format_facility_excerpt(fac)

            if DRY_RUN:
                city = fac.get('FacCity') or ''
                print(f'    [dry-run] {fac_name[:45]:<45}  {city:<20}  {doc_type}')
                inserted += 1
                existing_urls.add(doc_url)
                continue

            db.execute("""
                INSERT INTO regulatory_evidence
                (company_name, company_cik, document_type, document_date,
                 document_url, raw_text_excerpt, excerpt_char_count,
                 source_system, ingested_at)
                VALUES (?,?,?,?,?,?,?,'epa_echo',?)
            """, (
                fac_name, registry_id, doc_type, doc_date,
                doc_url, text[:50000], min(len(text), 50000),
                now_iso(),
            ))
            existing_urls.add(doc_url)
            inserted += 1

        db.commit()
        time.sleep(SLEEP_SEC)

    print(f'\n  NAICS totals: inserted={inserted}  skipped(dup)={skipped_dup}')
    return inserted, skipped_dup


# ── Source B: Name-based targeted search ─────────────────────────────────────

def collect_name_facilities(db: sqlite3.Connection, existing_urls: set,
                            claude_client=None, groq_client=None) -> tuple[int, int]:
    """Search EPA ECHO by developer names from unified_projects WHERE state='TX'."""
    print('\n── Source B: Name-based targeted search ────────────────────────')

    targets = db.execute("""
        SELECT DISTINCT developer_name
        FROM unified_projects
        WHERE state = 'TX'
        ORDER BY developer_name
    """).fetchall()

    print(f'  {len(targets)} Texas developer names to search')
    inserted = skipped_dup = 0

    for target in targets:
        dev_name = target['developer_name']
        search_terms = _extract_search_names(dev_name)

        for term in search_terms:
            data = _get(EPA_FACILITY_URL, params={
                'p_st': 'TX',
                'p_fn': term,
                'output': 'JSON',
            })
            if not data:
                continue

            facilities = data.get('Results', {}).get('Facilities', [])
            if not facilities:
                continue

            # LLM relevance filter
            facilities = filter_relevant(facilities, claude_client, groq_client)

            for fac in facilities:
                registry_id = fac.get('RegistryID') or ''
                if not registry_id:
                    continue

                doc_url = f'https://echo.epa.gov/detailed-facility-report?fid={registry_id}'
                if doc_url in existing_urls:
                    skipped_dup += 1
                    continue

                fac_name = fac.get('FacName') or 'Unknown'
                doc_type = classify_permit_type(fac)
                doc_date = _best_date(fac)
                text = format_facility_excerpt(fac, matched_project=dev_name)

                if DRY_RUN:
                    print(f'    [dry-run] {dev_name[:25]:<25} -> {fac_name[:40]:<40}  {doc_type}')
                    inserted += 1
                    existing_urls.add(doc_url)
                    continue

                db.execute("""
                    INSERT INTO regulatory_evidence
                    (company_name, company_cik, document_type, document_date,
                     document_url, raw_text_excerpt, excerpt_char_count,
                     source_system, ingested_at)
                    VALUES (?,?,?,?,?,?,?,'epa_echo',?)
                """, (
                    fac_name, registry_id, doc_type, doc_date,
                    doc_url, text[:50000], min(len(text), 50000),
                    now_iso(),
                ))
                existing_urls.add(doc_url)
                inserted += 1

            time.sleep(SLEEP_SEC)
            # If first term found results, skip remaining variants
            if facilities:
                break

        db.commit()

    print(f'\n  Name-based totals: inserted={inserted}  skipped(dup)={skipped_dup}')
    return inserted, skipped_dup


# ── Source C: DFR enrichment ─────────────────────────────────────────────────

def enrich_with_dfr(db: sqlite3.Connection) -> int:
    """Fetch Detailed Facility Reports — extract structured permit data.

    Writes to:
      - epa_permits (per-permit rows)
      - epa_compliance_summary (per-statute compliance)
      - regulatory_evidence (operator_name, llc_entity, derived_stage, derived_technology, text)
    """
    print('\n── Source C: DFR enrichment (enhanced) ─────────────────────────')

    # If --re-enrich, process all; otherwise only un-enriched
    if RE_ENRICH:
        epa_rows = db.execute("""
            SELECT id, company_cik, company_name, raw_text_excerpt, document_date
            FROM regulatory_evidence
            WHERE source_system = 'epa_echo'
              AND company_cik IS NOT NULL AND company_cik != ''
            ORDER BY id
        """).fetchall()
    else:
        epa_rows = db.execute("""
            SELECT id, company_cik, company_name, raw_text_excerpt, document_date
            FROM regulatory_evidence
            WHERE source_system = 'epa_echo'
              AND company_cik IS NOT NULL AND company_cik != ''
              AND raw_text_excerpt NOT LIKE '%--- PERMITS ---%'
            ORDER BY id
        """).fetchall()

    print(f'  {len(epa_rows)} EPA facilities to enrich {"(re-enrich)" if RE_ENRICH else ""}')
    enriched = 0
    permit_count = 0
    compliance_count = 0

    for row in epa_rows:
        registry_id = row['company_cik']
        fac_name = row['company_name'] or 'Unknown'

        data = _get(EPA_DFR_URL, params={'p_id': registry_id, 'output': 'JSON'})
        if not data:
            time.sleep(SLEEP_SEC)
            continue

        dfr = data.get('Results', {})
        parsed = _parse_dfr_full(dfr)

        if not parsed['permits'] and not parsed['permits_text']:
            time.sleep(SLEEP_SEC)
            continue

        # ── Write per-permit rows to epa_permits ──
        # Get facility address from the raw_text_excerpt
        existing_text = row['raw_text_excerpt'] or ''
        fac_street = _extract_field(existing_text, 'Address:')
        loc_full = _extract_field(existing_text, 'Location:')
        fac_city = loc_full.split(',')[0].strip() if loc_full else ''
        fac_state = 'TX'  # all our EPA data is TX
        fac_zip = ''
        addr_match = re.search(r'Address:.*?(\d{5})', existing_text)
        if addr_match:
            fac_zip = addr_match.group(1)

        now = now_iso()
        for perm in parsed['permits']:
            if not perm['permit_id']:
                continue
            if not DRY_RUN:
                db.execute("""
                    INSERT OR REPLACE INTO epa_permits
                    (registry_id, facility_name, facility_street, facility_city,
                     facility_state, facility_zip, permit_id, statute, epa_system,
                     permit_class, permit_status, permit_areas, permit_expiration,
                     naics_code, sic_code, collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    registry_id,
                    perm.get('facility_name') or fac_name,
                    fac_street, fac_city, fac_state, fac_zip,
                    perm['permit_id'], perm['statute'], perm['epa_system'],
                    perm['permit_class'], perm['permit_status'],
                    perm['permit_areas'], perm['permit_expiration'],
                    perm['naics_code'], perm['sic_code'], now,
                ))
                permit_count += 1

        # ── Write compliance rows ──
        for comp in parsed['compliance']:
            if not comp.get('source_id') and not comp.get('statute'):
                continue
            # Merge with enforcement data by statute match
            enf = next(
                (e for e in parsed['enforcement']
                 if e.get('statute') == comp.get('statute')),
                {}
            )
            if not DRY_RUN:
                db.execute("""
                    INSERT OR REPLACE INTO epa_compliance_summary
                    (registry_id, statute, source_id, current_snc, quarters_in_nc,
                     inspections_3yr, last_inspection, formal_actions,
                     total_penalties, collected_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (
                    registry_id,
                    comp.get('statute', ''),
                    comp['source_id'],
                    comp.get('current_snc', ''),
                    comp.get('quarters_in_nc'),
                    enf.get('inspections_3yr'),
                    enf.get('last_inspection'),
                    enf.get('formal_actions'),
                    enf.get('total_penalties'),
                    now,
                ))
                compliance_count += 1

        # ── Derive stage and technology ──
        derived_stage = derive_stage_from_permits(
            parsed['stage_signals'], parsed['tech_signals']
        )
        derived_tech = ', '.join(sorted(parsed['tech_signals'])) if parsed['tech_signals'] else None

        # ── Update regulatory_evidence ──
        updates = {}
        entity = parsed['entity']
        if entity['operator_name']:
            updates['operator_name'] = entity['operator_name']
        if entity['llc_entity']:
            updates['llc_entity'] = entity['llc_entity']
        if derived_stage:
            updates['derived_stage'] = derived_stage
        if derived_tech:
            updates['derived_technology'] = derived_tech

        # Update text: strip old permits section, add new
        if parsed['permits_text']:
            # Remove existing --- PERMITS --- section if re-enriching
            base_text = re.sub(
                r'\n*--- PERMITS ---[\s\S]*$', '', existing_text
            ).rstrip()
            updated_text = (base_text + '\n\n' + parsed['permits_text'])[:50000]
            updates['raw_text_excerpt'] = updated_text
            updates['excerpt_char_count'] = len(updated_text)

        # Update document_date if DFR provides a real permit date
        current_date = row['document_date'] or ''
        if parsed['best_date'] and (not current_date or current_date == now_iso()[:10]):
            updates['document_date'] = parsed['best_date']

        if updates and not DRY_RUN:
            set_clause = ', '.join(f'{k} = ?' for k in updates)
            vals = list(updates.values()) + [row['id']]
            db.execute(
                f"UPDATE regulatory_evidence SET {set_clause} WHERE id = ?",
                vals,
            )
            enriched += 1
            stage_str = derived_stage or '-'
            op_str = (entity['operator_name'] or '-')[:25]
            print(f'  [{row["id"]}] {fac_name[:30]:<30} op={op_str:<25} stage={stage_str}')
        elif updates and DRY_RUN:
            enriched += 1
            stage_str = derived_stage or '-'
            op_str = (entity['operator_name'] or '-')[:25]
            print(f'  [dry-run] [{row["id"]}] {fac_name[:30]:<30} op={op_str:<25} stage={stage_str}')

        time.sleep(SLEEP_SEC)

    db.commit()
    print(f'  Enriched: {enriched}  Permits written: {permit_count}  Compliance: {compliance_count}')
    return enriched


def _extract_field(text: str, label: str) -> str:
    """Extract a field value from structured text by label."""
    match = re.search(rf'{re.escape(label)}\s*(.+?)(?:\n|$)', text)
    return match.group(1).strip() if match else ''


def _parse_dfr(dfr: dict) -> tuple[str, str | None]:
    """Extract permit text and best permit date from DFR response.
    Returns (permits_text, best_date_str).
    """
    parts = []
    best_date = None

    # Look through various DFR sections for permit data
    for section_key in ('Permits', 'AirCompliance', 'WaterCompliance',
                        'ComplianceSummary', 'CWAPermits', 'CAASourceData'):
        section = dfr.get(section_key)
        if not section:
            continue

        # Handle different response shapes
        items = []
        if isinstance(section, list):
            items = section
        elif isinstance(section, dict):
            for v in section.values():
                if isinstance(v, list):
                    items = v
                    break

        for item in items[:15]:
            if not isinstance(item, dict):
                continue

            # Extract permit info
            statute = item.get('Statute', item.get('ProgramCode', ''))
            system = item.get('EPASystem', item.get('PermitType', ''))
            source_id = item.get('SourceID', item.get('PermitNumber', ''))
            universe = item.get('Universe', item.get('FacilityClass', ''))

            if statute or system or source_id:
                parts.append(f'  {statute} / {system}: ID={source_id} Class={universe}')

            # Extract permit dates
            for date_key in ('PermitIssuedDate', 'PermitEffectiveDate',
                             'PermitExpirationDate', 'IssuanceDate',
                             'EffectiveDate', 'ExpirationDate'):
                raw_date = item.get(date_key, '')
                if not raw_date or raw_date == 'None':
                    continue
                parsed = _parse_date(raw_date)
                if parsed and (best_date is None or parsed > best_date):
                    best_date = parsed

    # GHG data
    ghg = dfr.get('GHGData', dfr.get('GHGEmissions', ''))
    if ghg and isinstance(ghg, (list, dict)):
        ghg_str = str(ghg)[:500]
        parts.append(f'\nGHG Reporting Data: {ghg_str}')

    permits_text = ''
    if parts:
        permits_text = '--- PERMITS ---\n' + '\n'.join(parts)

    return permits_text, best_date


def _parse_date(raw: str) -> str | None:
    """Try to parse various date formats into YYYY-MM-DD."""
    raw = raw.strip()
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%m-%d-%Y'):
        try:
            return datetime.strptime(raw, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


# ── Enhanced DFR parser ──────────────────────────────────────────────────────

def _parse_dfr_full(dfr: dict) -> dict:
    """Extract structured permit, entity, stage, technology, compliance data.

    Returns dict with keys:
        permits       — list of permit dicts (one per permit row)
        entity        — {operator_name, llc_entity} best names found
        stage_signals — set of strings e.g. {'operating', 'title_v_permit'}
        tech_signals  — set of strings e.g. {'hydrogen_production'}
        compliance    — list of compliance summary dicts
        enforcement   — list of enforcement summary dicts
        permits_text  — formatted text block for raw_text_excerpt
        best_date     — best YYYY-MM-DD date found across all permits
    """
    permits = []
    compliance_rows = []
    enforcement_rows = []
    stage_signals = set()
    tech_signals = set()
    entity = {'operator_name': None, 'llc_entity': None}
    best_date = None
    text_parts = []

    # ── Parse Permits section ──
    permits_section = dfr.get('Permits')
    if permits_section:
        items = []
        if isinstance(permits_section, list):
            items = permits_section
        elif isinstance(permits_section, dict):
            for v in permits_section.values():
                if isinstance(v, list):
                    items = v
                    break

        for item in items:
            if not isinstance(item, dict):
                continue

            statute = item.get('Statute', '') or ''
            epa_system = item.get('EPASystem', '') or ''
            source_id = item.get('SourceID', '') or ''
            universe = item.get('Universe', '') or ''
            fac_status = item.get('FacilityStatus', '') or ''
            fac_name = item.get('FacilityName', '') or ''
            areas = item.get('Areas', '') or ''
            naics = item.get('NAICSCodes', '') or ''
            sic = item.get('SICCodes', '') or ''

            # Permit expiration date (field is 'ExpDate' in DFR response)
            exp_raw = item.get('ExpDate', item.get('ExpirationDate', '')) or ''
            exp_date = _parse_date(exp_raw) if exp_raw else None

            permit_row = {
                'permit_id': source_id,
                'statute': statute,
                'epa_system': epa_system,
                'permit_class': universe,
                'permit_status': fac_status,
                'permit_areas': areas,
                'permit_expiration': exp_date,
                'naics_code': naics,
                'sic_code': sic,
                'facility_name': fac_name,
            }
            permits.append(permit_row)

            # ── Entity extraction ──
            # ICIS-Air and ICIS-NPDES names are typically corporate operator names
            if epa_system in ('ICIS-Air', 'ICIS-NPDES') and fac_name:
                if not entity['operator_name']:
                    entity['operator_name'] = fac_name
            # RCRAInfo/TRI/RMP names often include LLC/LP/Inc (legal entities)
            if epa_system in ('RCRAInfo', 'TRI', 'RMP') and fac_name:
                llc_match = re.search(
                    r'(?:LLC|L\.L\.C|LP|L\.P|INC|CORP|CORPORATION|LTD|CO\b)',
                    fac_name, re.IGNORECASE
                )
                if llc_match and not entity['llc_entity']:
                    entity['llc_entity'] = fac_name

            # ── Stage signals from permit data ──
            status_lower = fac_status.lower() if fac_status else ''
            if 'operating' in status_lower or 'effective' in status_lower:
                stage_signals.add('operating')
            if status_lower.startswith('inactive') or 'terminated' in status_lower:
                stage_signals.add('inactive')
            if 'active' in status_lower and 'inactive' not in status_lower:
                stage_signals.add('operating')

            areas_lower = areas.lower() if areas else ''
            if 'construction stormwater' in areas_lower:
                stage_signals.add('construction_stormwater')
            if 'caatvp' in areas_lower or 'title v' in areas_lower:
                stage_signals.add('title_v_permit')
            if 'psd' in areas_lower:
                stage_signals.add('psd_permit')

            # ── Technology signals from GHGRP Areas ──
            if 'hydrogen production' in areas_lower:
                tech_signals.add('hydrogen_production')
            if 'ammonia manufacturing' in areas_lower:
                tech_signals.add('ammonia_manufacturing')
            if 'petroleum refin' in areas_lower:
                tech_signals.add('petroleum_refining')
            if 'carbon dioxide' in areas_lower or 'co2' in areas_lower:
                tech_signals.add('carbon_capture')

            # Build text line
            status_str = f' [{fac_status}]' if fac_status else ''
            areas_str = f' Areas={areas}' if areas else ''
            exp_str = f' Exp={exp_date}' if exp_date else ''
            text_parts.append(
                f'  {statute}/{epa_system}: ID={source_id} Class={universe}'
                f'{status_str}{areas_str}{exp_str}'
            )
            if fac_name and fac_name != permits[0].get('facility_name', ''):
                text_parts.append(f'    Permit Holder: {fac_name}')

            # Extract dates for best_date
            for date_key in ('ExpDate', 'ExpirationDate',
                             'PermitIssuedDate', 'PermitEffectiveDate',
                             'IssuanceDate', 'EffectiveDate'):
                raw_date = item.get(date_key, '') or ''
                if raw_date and raw_date != 'None':
                    parsed = _parse_date(raw_date)
                    if parsed and (best_date is None or parsed > best_date):
                        best_date = parsed

    # Check GHGRP reporting status (stage signal)
    for section_key in ('Permits',):
        section = dfr.get(section_key)
        if not section:
            continue
        items = section if isinstance(section, list) else []
        if isinstance(section, dict):
            for v in section.values():
                if isinstance(v, list):
                    items = v
                    break
        for item in items:
            if not isinstance(item, dict):
                continue
            sys = (item.get('EPASystem') or '').upper()
            if sys in ('E-GGRT', 'GHGRP'):
                stage_signals.add('ghgrp_reporting')
                # Check for technology in GHGRP areas
                ghg_areas = (item.get('Areas') or '').lower()
                if 'hydrogen production' in ghg_areas:
                    tech_signals.add('hydrogen_production')
                if 'ammonia' in ghg_areas:
                    tech_signals.add('ammonia_manufacturing')

    # ── Parse ComplianceSummary ──
    comp_section = dfr.get('ComplianceSummary')
    if comp_section:
        sources = []
        if isinstance(comp_section, dict):
            sources = comp_section.get('Source', [])
            if isinstance(sources, dict):
                sources = [sources]
        elif isinstance(comp_section, list):
            sources = comp_section

        for src in sources:
            if not isinstance(src, dict):
                continue
            comp_row = {
                'statute': src.get('Statute', '') or '',
                'source_id': src.get('SourceID', '') or '',
                'current_snc': src.get('CurrentSNC', '') or '',
                'quarters_in_nc': _safe_int(src.get('QtrsInNC')),
            }
            compliance_rows.append(comp_row)

    # ── Parse EnforcementComplianceSummaries ──
    enf_section = dfr.get('EnforcementComplianceSummaries')
    if enf_section:
        summaries = []
        if isinstance(enf_section, dict):
            summaries = enf_section.get('Summaries', [])
            if isinstance(summaries, dict):
                summaries = [summaries]
        elif isinstance(enf_section, list):
            summaries = enf_section

        for summ in summaries:
            if not isinstance(summ, dict):
                continue
            enf_row = {
                'statute': summ.get('Statute', '') or '',
                'source_id': summ.get('SourceID', '') or '',
                'inspections_3yr': _safe_int(summ.get('Inspections',
                                   summ.get('InspectionCount'))),
                'last_inspection': _parse_date(summ.get('LastInspection', '') or ''),
                'formal_actions': _safe_int(summ.get('FormalActions',
                                  summ.get('FormalCount'))),
                'total_penalties': _safe_float(summ.get('TotalPenalties',
                                   summ.get('TotalCasePenalties'))),
            }
            enforcement_rows.append(enf_row)

    # ── GHG data ──
    ghg = dfr.get('GHGData', dfr.get('GHGEmissions', ''))
    if ghg and isinstance(ghg, (list, dict)):
        ghg_str = str(ghg)[:500]
        text_parts.append(f'\nGHG Reporting Data: {ghg_str}')

    # ── Build permits_text ──
    permits_text = ''
    if text_parts:
        header_lines = []
        if entity['operator_name']:
            header_lines.append(f'Operator: {entity["operator_name"]}')
        if entity['llc_entity']:
            header_lines.append(f'Legal Entity: {entity["llc_entity"]}')
        if stage_signals:
            header_lines.append(f'Stage Signals: {", ".join(sorted(stage_signals))}')
        if tech_signals:
            header_lines.append(f'Technology: {", ".join(sorted(tech_signals))}')
        header_lines.append(f'Collected: {now_iso()[:10]}')

        permits_text = '--- PERMITS ---\n'
        if header_lines:
            permits_text += '\n'.join(header_lines) + '\n\n'
        permits_text += '\n'.join(text_parts)

    return {
        'permits': permits,
        'entity': entity,
        'stage_signals': stage_signals,
        'tech_signals': tech_signals,
        'compliance': compliance_rows,
        'enforcement': enforcement_rows,
        'permits_text': permits_text,
        'best_date': best_date,
    }


def _safe_int(val) -> int | None:
    """Convert to int or return None."""
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    """Convert to float or return None."""
    if val is None:
        return None
    try:
        return float(str(val).replace(',', '').replace('$', ''))
    except (ValueError, TypeError):
        return None


# ── Stage derivation from permit signals ─────────────────────────────────────

def derive_stage_from_permits(stage_signals: set, tech_signals: set) -> str | None:
    """Derive project stage from permit signal set.

    Priority-based rules (highest wins):
      operating + (hydrogen/ammonia tech)  → operational
      ghgrp_reporting + hydrogen           → operational
      title_v_permit + operating           → operational
      construction_stormwater              → construction
      psd_permit                           → permitted
      title_v_permit (no operating)        → permitted
      ICIS-Air permit only                 → permitted
      inactive                             → on_hold
    """
    has_tech = bool(tech_signals & {'hydrogen_production', 'ammonia_manufacturing'})
    has_operating = 'operating' in stage_signals
    has_title_v = 'title_v_permit' in stage_signals
    has_ghgrp = 'ghgrp_reporting' in stage_signals
    has_construction = 'construction_stormwater' in stage_signals
    has_psd = 'psd_permit' in stage_signals
    has_inactive = 'inactive' in stage_signals

    # Priority cascade
    if has_operating and has_tech:
        return 'operational'
    if has_ghgrp and has_tech:
        return 'operational'
    if has_title_v and has_operating:
        return 'operational'
    if has_construction:
        return 'construction'
    if has_psd:
        return 'permitted'
    if has_title_v and not has_operating:
        return 'permitted'
    if has_inactive:
        return 'on_hold'

    return None


# ── Source D: GHGRP Parent Company enrichment ────────────────────────────────

EPA_GHGRP_URL = 'https://data.epa.gov/efservice/PUB_DIM_FACILITY/FACILITY_ID/{ghg_id}/JSON'

GHGRP_ONLY = '--ghgrp' in sys.argv
RE_ENRICH  = '--re-enrich' in sys.argv


def enrich_with_ghgrp(db: sqlite3.Connection) -> int:
    """Fetch GHGRP parent company data for facilities with GHG Reporting IDs."""
    print('\n── Source D: GHGRP parent company enrichment ────────────────────')

    # Find EPA facilities that have GHG IDs
    epa_rows = db.execute("""
        SELECT id, company_cik, company_name, raw_text_excerpt
        FROM regulatory_evidence
        WHERE source_system = 'epa_echo'
          AND raw_text_excerpt LIKE '%GHG Reporting IDs:%'
          AND (parent_company IS NULL OR parent_company = '')
        ORDER BY id
    """).fetchall()

    print(f'  {len(epa_rows)} facilities with GHG IDs (no parent_company yet)')
    enriched = 0

    for row in epa_rows:
        text = row['raw_text_excerpt'] or ''
        # Extract GHG ID(s) from text
        ghg_match = re.search(r'GHG Reporting IDs:\s*([^\n]+)', text)
        if not ghg_match:
            continue

        ghg_ids_str = ghg_match.group(1).strip()
        # May be comma-separated or space-separated
        ghg_ids = [g.strip() for g in re.split(r'[,\s]+', ghg_ids_str) if g.strip()]

        parent_company = None
        for ghg_id in ghg_ids[:3]:  # limit to first 3
            url = EPA_GHGRP_URL.format(ghg_id=ghg_id)
            try:
                headers = {'User-Agent': UA, 'Accept': 'application/json'}
                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 429:
                    time.sleep(5)
                    resp = requests.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                if isinstance(data, list) and data:
                    # Take most recent year entry (field is lowercase 'year')
                    entry = sorted(
                        data,
                        key=lambda x: x.get('year') or x.get('YEAR') or 0,
                        reverse=True
                    )[0]
                    # Field is lowercase 'parent_company' in API response
                    pc = (entry.get('parent_company')
                          or entry.get('PARENT_COMPANY_NAME')
                          or '')
                    if pc.strip():
                        parent_company = pc.strip()
                        break
            except Exception as e:
                print(f'  [{row["id"]}] GHGRP fetch failed for {ghg_id}: {e}')

            time.sleep(0.3)

        if parent_company:
            if not DRY_RUN:
                db.execute(
                    "UPDATE regulatory_evidence SET parent_company = ? WHERE id = ?",
                    (parent_company, row['id'])
                )
                enriched += 1
                print(f'  [{row["id"]}] {row["company_name"][:35]:<35} → parent: {parent_company[:50]}')
            else:
                enriched += 1
                print(f'  [dry-run] [{row["id"]}] {row["company_name"][:35]} → {parent_company[:50]}')

        time.sleep(0.3)

    db.commit()
    print(f'  GHGRP enriched: {enriched}')
    return enriched


# ── Stats ────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection) -> None:
    print('\n' + '=' * 60)
    print('COLLECT EPA — CURRENT COVERAGE')
    print('=' * 60)

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='epa_echo'"
    ).fetchone()[0]
    print(f'\n  Total EPA documents: {total}')

    if total > 0:
        print('\n  By document_type:')
        for r in db.execute("""
            SELECT document_type, COUNT(*) n
            FROM regulatory_evidence
            WHERE source_system = 'epa_echo'
            GROUP BY document_type ORDER BY n DESC
        """):
            print(f'    {r[0]:<20} {r[1]}')

        # ── Structured permit stats ──
        try:
            perm_count = db.execute("SELECT COUNT(*) FROM epa_permits").fetchone()[0]
            print(f'\n  Structured permits (epa_permits): {perm_count}')
            if perm_count > 0:
                print('  By EPA system:')
                for r in db.execute("""
                    SELECT epa_system, COUNT(*) n FROM epa_permits
                    GROUP BY epa_system ORDER BY n DESC
                """):
                    print(f'    {(r[0] or "unknown"):<20} {r[1]}')
                print('  By statute:')
                for r in db.execute("""
                    SELECT statute, COUNT(*) n FROM epa_permits
                    GROUP BY statute ORDER BY n DESC
                """):
                    print(f'    {(r[0] or "unknown"):<20} {r[1]}')
        except sqlite3.OperationalError:
            print('\n  epa_permits table not yet created')

        try:
            comp_count = db.execute("SELECT COUNT(*) FROM epa_compliance_summary").fetchone()[0]
            print(f'\n  Compliance summaries: {comp_count}')
        except sqlite3.OperationalError:
            pass

        # ── Enrichment coverage ──
        print('\n  Enrichment coverage:')
        for col, label in [
            ('operator_name', 'Operator name'),
            ('llc_entity', 'LLC entity'),
            ('parent_company', 'Parent company'),
            ('derived_stage', 'Derived stage'),
            ('derived_technology', 'Derived technology'),
        ]:
            try:
                filled = db.execute(f"""
                    SELECT COUNT(*) FROM regulatory_evidence
                    WHERE source_system='epa_echo' AND {col} IS NOT NULL AND {col} != ''
                """).fetchone()[0]
                pct = (filled / total * 100) if total else 0
                print(f'    {label:<22} {filled:4d} / {total}  ({pct:.0f}%)')
            except sqlite3.OperationalError:
                print(f'    {label:<22} column not yet added')

        # ── Stage distribution ──
        try:
            print('\n  Derived stage distribution:')
            for r in db.execute("""
                SELECT COALESCE(derived_stage, 'none'), COUNT(*) n
                FROM regulatory_evidence
                WHERE source_system = 'epa_echo'
                GROUP BY derived_stage ORDER BY n DESC
            """):
                print(f'    {(r[0] or "none"):<20} {r[1]}')
        except sqlite3.OperationalError:
            pass

        # ── Sample facilities ──
        print('\n  Sample enriched facilities (top 10):')
        try:
            for r in db.execute("""
                SELECT company_name, operator_name, derived_stage, derived_technology
                FROM regulatory_evidence
                WHERE source_system = 'epa_echo'
                  AND operator_name IS NOT NULL
                ORDER BY company_name
                LIMIT 10
            """):
                tech = (r[3] or '-')[:20]
                print(f'    {(r[0] or "")[:30]:<30} op={r[1] or "-":<25} stage={r[2] or "-":<12} tech={tech}')
        except sqlite3.OperationalError:
            pass

    print('\n  TX projects in unified_projects:')
    tx_count = db.execute(
        "SELECT COUNT(*) FROM unified_projects WHERE state='TX'"
    ).fetchone()[0]
    print(f'    {tx_count} projects')


# ── Entry point ──────────────────────────────────────────────────────────────

def run() -> None:
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found')
        sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    # ── LLM clients for relevance filtering ──
    claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None
    groq_client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
    filter_status = 'disabled (--no-filter)' if NO_FILTER else (
        f'Claude + Groq fallback' if claude_client and groq_client else
        'Claude' if claude_client else
        'Groq' if groq_client else
        'disabled (no API keys)'
    )

    print(f'{"DRY RUN — " if DRY_RUN else ""}EPA ECHO permit collection')
    print(f'  Database: {CORE_DB}')
    print(f'  NAICS codes: {len(NAICS_CODES)}')
    print(f'  LLM filter: {filter_status}')

    existing_urls = {
        r[0] for r in db.execute(
            "SELECT document_url FROM regulatory_evidence WHERE document_url IS NOT NULL"
        )
    }
    print(f'  Existing URLs in DB: {len(existing_urls)}')

    totals = {'inserted': 0, 'skipped_dup': 0}
    run_all = not any([NAICS_ONLY, NAMES_ONLY, DFR_ONLY, GHGRP_ONLY])

    if run_all or NAICS_ONLY:
        ins, sd = collect_naics_facilities(db, existing_urls, claude_client, groq_client)
        totals['inserted'] += ins
        totals['skipped_dup'] += sd

    if run_all or NAMES_ONLY:
        ins, sd = collect_name_facilities(db, existing_urls, claude_client, groq_client)
        totals['inserted'] += ins
        totals['skipped_dup'] += sd

    if run_all or DFR_ONLY:
        enriched = enrich_with_dfr(db)
        print(f'  DFR enriched: {enriched}')

    if run_all or GHGRP_ONLY:
        ghgrp_enriched = enrich_with_ghgrp(db)
        print(f'  GHGRP enriched: {ghgrp_enriched}')

    print(f'\n{"=" * 60}')
    print('EPA ECHO COLLECTION COMPLETE')
    print(f'{"=" * 60}')
    print(f'  Inserted:     {totals["inserted"]}')
    print(f'  Skipped(dup): {totals["skipped_dup"]}')
    print(f'\nNext steps:')
    print(f'  python3 step1_preprocess.py')
    print(f'  python3 step3_read_pass.py')
    print(f'  python3 ../Connect/connect.py')
    print(f'  python3 bootstrap_projects.py --enrich')
    print(f'  python3 bootstrap_projects.py --enrich-stages')

    db.close()


if __name__ == '__main__':
    run()
