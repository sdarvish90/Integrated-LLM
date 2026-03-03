"""
DecarbIQ Migration: Separate Companies from Projects

Splits the 1-row-per-company unified_projects into a proper two-layer model:
  companies → unified_projects (company_portfolio | facility | doe_award)

Seven phases, all idempotent (INSERT OR IGNORE / UPDATE ... WHERE).
Run after step2_schema.py has added the new tables and columns.

Usage:
    cd Learn/
    python3 migrate_companies.py [--dry-run] [--phase N]
"""

import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone

from config import LEARNING_DB

# ── Helpers ──────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def make_developer_key(name: str) -> str:
    """Slugify company name — mirrors bootstrap_projects.make_developer_key."""
    key = name.lower().strip()
    key = re.sub(r'[^\w\s-]', '', key)
    key = re.sub(r'\s+', '_', key)
    key = re.sub(r'_+', '_', key).strip('_')
    return key[:80]


def deterministic_project_id(developer_key: str) -> str:
    """MD5[:12] — same as bootstrap_projects for backward compat."""
    return hashlib.md5(developer_key.encode()).hexdigest()[:12]


def sub_project_id(composite_key: str) -> str:
    """Deterministic 16-char hex ID for facility/award sub-projects."""
    return hashlib.md5(composite_key.encode()).hexdigest()[:16]


def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    if not name:
        return ''
    n = name.lower().strip()
    n = re.sub(r'[^\w\s]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


_LEGAL_SUFFIXES = re.compile(
    r'\b(llc|inc|incorporated|corp|corporation|company|co|limited|ltd|'
    r'holdings|group|energy|and|the|&|lp)\b', re.I
)


def normalize_stripped(name: str) -> str:
    """Normalize with legal suffix stripping — for company-level detection."""
    if not name:
        return ''
    n = name.lower().strip()
    n = _LEGAL_SUFFIXES.sub(' ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


# ── Phase 0: Pre-seed known corporate mergers ───────────────────────────────

KNOWN_MERGERS = [
    # (canonical_name, alias, alias_type, description)
    ('Linde', 'PRAXAIR', 'acquired_by', 'Linde acquired Praxair in 2018'),
    ('Linde', 'UNION CARBIDE CORP LINDE DIV', 'subsidiary', 'Historical Linde division'),
    ('Linde', 'UCIG INC LINDE DIV', 'subsidiary', 'Historical UCIG/Linde division'),
    ('Linde', 'WESTERN INTERNATIONAL GAS & CYLINDERS', 'subsidiary', 'Linde subsidiary'),
]


# Map of normalized parent_company variants → company_key in companies table.
# Used by _resolve_parent_company() and _resolve_ghgrp_parent() for variants
# that can't be resolved by suffix-stripping alone.
PARENT_NAME_MAP = {
    # Air Liquide variants
    'american air liquide holdings': 'air_liquide',
    'air liquide large industries us': 'air_liquide',
    'air liquide large industries u s': 'air_liquide',
    'air liquide usa': 'air_liquide',
    'air liquide america': 'air_liquide',
    # Valero variants
    'valero': 'valero_energy',
    'valero energy': 'valero_energy',
    # BP variants
    'bp america': 'bp',
    'bp products north america': 'bp',
    # ConocoPhillips
    'conocophillips': 'conocophillips',
    # Calumet variants
    'calumet specialty products partners': 'calumet',
    'calumet lubricants': 'calumet',
    'calumet shreveport lubricants waxes': 'calumet',
    # CHS
    'chs': 'chs',
    # Ergon
    'ergon': 'ergon',
    # Koch Industries
    'koch industries': 'koch_industries',
    # Suncor
    'suncor usa': 'suncor_energy',
    'suncor': 'suncor_energy',
    # Hess
    'hess': 'hess',
    'hess corproation': 'hess',  # typo in EPA data
    # Ascend Performance Materials
    'ascend performance materials': 'ascend_performance',
    # CVR Energy
    'cvr': 'cvr_energy',
    # Evonik variants
    'evonik': 'evonik',
    'evonik industries': 'evonik',
    'evonik goldschmidt': 'evonik',
    # Motiva
    'motiva enterprises': 'motiva',
    # Celanese
    'celanese': 'celanese',
    # Hunt Consolidated
    'hunt consolidated': 'hunt_consolidated',
    'hunt crude oil supply': 'hunt_consolidated',
    # Olin
    'olin': 'olin',
    # ALON USA
    'alon usa': 'alon_usa',
    # Par Pacific
    'par pacific': 'par_pacific',
    # San Joaquin Refining
    'san joaquin refining': 'san_joaquin_refining',
    # PDV / CITGO
    'pdv america': 'pdv_citgo',
    'pdv holding': 'pdv_citgo',
    # Solvay
    'solvay chemicals': 'solvay',
    'solvay holding': 'solvay',
    'solvay': 'solvay',
    # Arctic Slope / ASRC
    'arctic slope regional': 'asrc',
    'arctic slope regional corporations': 'asrc',
    'asrc': 'asrc',
    # Lion Oil
    'lion oil': 'lion_oil',
    # United Refining
    'united refining': 'united_refining',
    # Martin Midstream
    'martin midstream partners': 'martin_midstream',
    # NCRA
    'national cooperative refinery assoc': 'ncra',
    'national cooperative refining ass': 'ncra',
    # Island Energy
    'island services': 'island_energy',
    'island investor': 'island_energy',
    # LyondellBasell
    'lyondellbasell acetyls': 'lyondellbasell',
    'lyondellbasell': 'lyondellbasell',
    # Buckeye
    'buckeye port reading terminal': 'buckeye_partners',
    # Montana Refining
    'montana refining': 'montana_refining',
    # Red Apple Group
    'red apple': 'red_apple_group',
    # Connacher
    'connacher oil gas': 'connacher',
    # Husky Energy
    'husky': 'husky_energy',
    # Midstream/chemical companies
    'energy transfer': 'energy_transfer',
    'enterprise products partners': 'enterprise_products',
    'enlink midstream': 'enlink_midstream',
    'dcp midstream': 'dcp_midstream',
    'dcp midstream partners': 'dcp_midstream',
    'targa resources': 'targa_resources',
    'eagleclaw midstream services': 'eagleclaw_midstream',
    'dow chemical': 'dow',
    'dow': 'dow',
    'devon': 'devon_energy',
    'devon energy': 'devon_energy',
    'occidental petroleum': 'occidental_petroleum',
    'basf': 'basf',
    'eog resources': 'eog_resources',
    'kinder morgan': 'kinder_morgan',
    'crestwood equity partners': 'crestwood_midstream',
    'crestwood midstream partners': 'crestwood_midstream',
    'oci partners': 'oci_partners',
    # Also map already-tracked variants that fail suffix-stripping
    'exxon mobil': 'exxon_mobil',
    'exxonmobil': 'exxon_mobil',
    'shell petroleum': 'shell',
    'shell oil': 'shell',
    'chevron': 'chevron_usa',
    'linde gas north america': 'linde',
    'matheson tri gas': 'linde',
    'enbridge us': 'enbridge',
    'marathon petroleum': 'marathon_petroleum',
    'martinez refining': 'marathon_petroleum',
    'hollyfrontier': 'hf_sinclair',
    'sinclair companies': 'hf_sinclair',
    'sinclair cos': 'hf_sinclair',
    'sinclair oil': 'hf_sinclair',
    'western refining': 'andeavor',
    'western refining southwest': 'andeavor',
    'frontier refining marketing': 'andeavor',
    'phillips 66': 'phillips_66',
    'delek us': 'delek_us',
    # Rentech (defunct, but map to avoid unresolved)
    'rentech nitrogen partners': 'cvr_energy',  # CVR acquired Rentech Nitrogen
    'rentech': 'cvr_energy',
}


def _resolve_by_parent_map(parent_name: str, companies_by_key: dict) -> dict | None:
    """Look up parent name in PARENT_NAME_MAP, return company dict or None."""
    pn = normalize_stripped(parent_name)
    key = PARENT_NAME_MAP.get(pn)
    if key and key in companies_by_key:
        return companies_by_key[key]
    # Try progressively shorter prefixes (drop trailing words)
    words = pn.split()
    for length in range(len(words) - 1, 0, -1):
        prefix = ' '.join(words[:length])
        key = PARENT_NAME_MAP.get(prefix)
        if key and key in companies_by_key:
            return companies_by_key[key]
    return None


def phase_0_seed_mergers(db: sqlite3.Connection) -> int:
    """Seed entity_aliases with known corporate mergers not captured by LLM."""
    print('\n── Phase 0: Pre-seed known corporate mergers ─────────────────')
    seeded = 0
    now = now_iso()

    for canonical, alias, alias_type, description in KNOWN_MERGERS:
        # Find entity_id for the canonical name
        row = db.execute(
            "SELECT entity_id FROM entities WHERE canonical_name = ?",
            (canonical,)
        ).fetchone()
        if not row:
            print(f'  WARNING: entity not found for {canonical!r}, skipping')
            continue

        entity_id = row['entity_id']
        alias_norm = normalize_name(alias)

        result = db.execute("""
            INSERT OR IGNORE INTO entity_aliases
            (entity_id, alias, alias_norm, alias_type,
             is_explicit, confidence, cited_document_type, learned_at)
            VALUES (?, ?, ?, ?, 0, 0.90, 'manual_seed', ?)
        """, (entity_id, alias, alias_norm, alias_type, now))

        if result.rowcount:
            seeded += 1
            print(f'  + {alias} → {canonical} ({alias_type})')
        else:
            print(f'  = {alias} → {canonical} (already exists)')

    db.commit()
    print(f'  Seeded: {seeded}')
    return seeded


# ── Phase 1: Create companies from existing projects ────────────────────────

def phase_1_create_companies(db: sqlite3.Connection) -> int:
    """Create one companies row per existing unified_projects developer."""
    print('\n── Phase 1: Create companies from existing projects ──────────')
    now = now_iso()
    created = existing = 0

    projects = db.execute(
        "SELECT project_id, developer_key, developer_name FROM unified_projects"
    ).fetchall()

    for p in projects:
        dev_name = p['developer_name']
        dev_key = p['developer_key']
        # company_id matches old project_id for backward compat
        company_id = p['project_id']

        # Find entity_id if one exists
        entity_row = db.execute(
            "SELECT entity_id FROM entities WHERE canonical_name = ?",
            (dev_name,)
        ).fetchone()
        entity_id = entity_row['entity_id'] if entity_row else None

        # Find CIK if available
        cik_row = db.execute(
            "SELECT DISTINCT company_cik FROM regulatory_evidence "
            "WHERE company_cik IS NOT NULL AND company_cik != '' "
            "AND company_name = ?",
            (dev_name,)
        ).fetchone()
        cik = cik_row['company_cik'] if cik_row else None

        result = db.execute("""
            INSERT OR IGNORE INTO companies
            (company_id, company_key, company_name, entity_id, cik,
             company_type, country, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'developer', 'US', ?, ?)
        """, (company_id, dev_key, dev_name, entity_id, cik, now, now))

        if result.rowcount:
            created += 1
        else:
            existing += 1

        # Set company_id and project_type on unified_projects
        db.execute("""
            UPDATE unified_projects
            SET company_id = ?, project_type = 'company_portfolio'
            WHERE project_id = ? AND (company_id IS NULL OR company_id = '')
        """, (company_id, p['project_id']))

    db.commit()
    print(f'  Companies created: {created}  Already existed: {existing}')

    # Verify
    null_count = db.execute(
        "SELECT COUNT(*) as c FROM unified_projects WHERE company_id IS NULL"
    ).fetchone()['c']
    if null_count:
        print(f'  WARNING: {null_count} projects still have NULL company_id')

    return created


# ── Phase 2: Split EPA facilities into separate projects ────────────────────

def _resolve_parent_company(facility_name: str, db: sqlite3.Connection,
                            companies: list[dict]) -> dict | None:
    """
    4-tier resolution to find the parent company of an EPA facility.
    Returns company dict or None.
    """
    facility_upper = facility_name.upper().strip()
    facility_norm = normalize_name(facility_name)

    # Get enrichment fields from regulatory_evidence
    re_row = db.execute("""
        SELECT parent_company, operator_name
        FROM regulatory_evidence
        WHERE source_system = 'epa_echo' AND company_name = ?
        LIMIT 1
    """, (facility_name,)).fetchone()

    parent_company = re_row['parent_company'] if re_row and re_row['parent_company'] else None
    operator_name = re_row['operator_name'] if re_row and re_row['operator_name'] else None

    # Tier A: parent_company field
    if parent_company:
        # Handle multi-owner: take majority owner (first semicolon-delimited part)
        parts = parent_company.split(';')
        best_name = None
        best_pct = 0
        for part in parts:
            part = part.strip()
            m = re.search(r'\((\d+\.?\d*)%\)', part)
            pct = float(m.group(1)) if m else 0
            name = re.sub(r'\s*\([^)]*\)\s*$', '', part).strip()
            if pct > best_pct:
                best_pct = pct
                best_name = name
        if not best_name:
            best_name = re.sub(r'\s*\(\d+%?\)\s*$', '', parent_company).strip()

        parent_clean = best_name

        # Check PARENT_NAME_MAP first
        by_key = {co['company_key']: co for co in companies}
        mapped = _resolve_by_parent_map(parent_clean, by_key)
        if mapped:
            return mapped

        # Also strip common suffixes like CORP, INC, LLC
        parent_norm = normalize_name(parent_clean)
        parent_nospace = parent_norm.replace(' ', '')
        for co in companies:
            co_norm = normalize_name(co['company_name'])
            co_nospace = co_norm.replace(' ', '')
            if co_norm == parent_norm or co_nospace == parent_nospace:
                return co
            # Try with common suffix stripping
            parent_base = re.sub(r'\b(corp|inc|llc|ltd|co|company)\b', '', parent_norm).strip()
            co_base = re.sub(r'\b(corp|inc|llc|ltd|co|company)\b', '', co_norm).strip()
            if parent_base and co_base and (
                parent_base == co_base or
                parent_base.replace(' ', '') == co_base.replace(' ', '')
            ):
                return co

    # Tier B: operator_name
    if operator_name:
        op_norm = normalize_name(operator_name)
        for co in companies:
            if normalize_name(co['company_name']) == op_norm:
                return co

    # Tier C: entity_aliases lookup
    # Check if facility name or significant words match a known alias
    alias_row = db.execute("""
        SELECT ea.entity_id, e.canonical_name
        FROM entity_aliases ea
        JOIN entities e ON e.entity_id = ea.entity_id
        WHERE ea.alias_norm = ?
    """, (facility_norm,)).fetchone()
    if alias_row:
        canonical = alias_row['canonical_name']
        for co in companies:
            if co['company_name'] == canonical:
                return co

    # Also check if any word-segment of the facility name is an alias
    # e.g. "PRAXAIR INC.-LINDE DIVISION" contains "PRAXAIR"
    words = re.split(r'[\s\-.,;/()]+', facility_upper)
    for word in words:
        if len(word) < 4:
            continue
        word_norm = normalize_name(word)
        alias_row = db.execute("""
            SELECT ea.entity_id, e.canonical_name
            FROM entity_aliases ea
            JOIN entities e ON e.entity_id = ea.entity_id
            WHERE ea.alias_norm = ?
        """, (word_norm,)).fetchone()
        if alias_row:
            canonical = alias_row['canonical_name']
            for co in companies:
                if co['company_name'] == canonical:
                    return co

    # Tier D: Facility name prefix/contains matching
    # Also try space-collapsed variants (EXXONMOBIL vs EXXON MOBIL)
    facility_nospace = facility_norm.replace(' ', '')
    for co in companies:
        co_norm = normalize_name(co['company_name'])
        if len(co_norm) < 4:
            continue
        if facility_norm.startswith(co_norm) or co_norm in facility_norm:
            return co
        co_nospace = co_norm.replace(' ', '')
        if len(co_nospace) >= 4 and (
            facility_nospace.startswith(co_nospace) or co_nospace in facility_nospace
        ):
            return co

    return None


def phase_2_split_epa_facilities(db: sqlite3.Connection) -> int:
    """Create one unified_projects row per distinct EPA facility name."""
    print('\n── Phase 2: Split EPA facilities into sub-projects ──────────')
    now = now_iso()

    companies = [dict(r) for r in db.execute(
        "SELECT company_id, company_key, company_name FROM companies"
    ).fetchall()]

    # Get all distinct EPA facility names
    facilities = db.execute("""
        SELECT DISTINCT company_name
        FROM regulatory_evidence
        WHERE source_system = 'epa_echo' AND company_name IS NOT NULL
        ORDER BY company_name
    """).fetchall()

    created = skipped = existing = 0

    for fac in facilities:
        facility_name = fac['company_name']
        facility_upper = facility_name.upper().strip()

        # Find parent company
        parent = _resolve_parent_company(facility_name, db, companies)
        if not parent:
            skipped += 1
            continue

        company_id = parent['company_id']
        company_key = parent['company_key']

        # Generate sub-project ID
        pid = sub_project_id(company_key + '::' + facility_upper)

        # Check if already exists
        exists = db.execute(
            "SELECT 1 FROM unified_projects WHERE project_id = ?", (pid,)
        ).fetchone()
        if exists:
            existing += 1
            continue

        # Extract FRS IDs from claim text linked to this facility
        frs_ids = set()
        claims_for_facility = db.execute("""
            SELECT c.claim_text
            FROM claims c
            JOIN regulatory_evidence re ON c.source_id = re.id
            WHERE re.source_system = 'epa_echo' AND re.company_name = ?
        """, (facility_name,)).fetchall()
        for claim_row in claims_for_facility:
            text = claim_row['claim_text'] or ''
            for m in re.finditer(r'Registry ID:\s*(\d+)', text):
                frs_ids.add(m.group(1))

        registry_ids_json = json.dumps(sorted(frs_ids)) if frs_ids else None

        # Get location from epa_permits via FRS IDs
        city = state = None
        for frs_id in frs_ids:
            loc_row = db.execute("""
                SELECT facility_city, facility_state, facility_zip
                FROM epa_permits WHERE registry_id = ? LIMIT 1
            """, (frs_id,)).fetchone()
            if loc_row:
                city = loc_row['facility_city']
                state = loc_row['facility_state']
                break

        # Create the facility project
        project_name = f"{parent['company_name']} - {facility_name}"
        db.execute("""
            INSERT OR IGNORE INTO unified_projects
            (project_id, project_name, developer_key, developer_name,
             company_id, project_type, source_identity, source_registry_ids,
             state, city, stage, fid_probability,
             co_developers_json, evidence_gap_flags_json,
             source_count, fid_status, quarantined,
             created_at, updated_at, bootstrap_source, country)
            VALUES (?,?,?,?,?,?,?,?,?,?,'unknown',0.10,'[]','[]',0,'unknown',0,?,?,'migration','US')
        """, (pid, project_name, company_key, parent['company_name'],
              company_id, 'facility', facility_upper, registry_ids_json,
              state, city, now, now))

        created += 1
        if created <= 10 or created % 25 == 0:
            print(f'  + [{parent["company_name"][:20]:<20}] {facility_name[:50]}')

    db.commit()
    print(f'  Created: {created}  Skipped (no parent): {skipped}  '
          f'Already existed: {existing}')
    return created


# ── Phase 2b: Split GHGRP facilities into separate projects ─────────────────

def _resolve_ghgrp_parent(facility_name: str, db: sqlite3.Connection,
                          companies: list[dict]) -> dict | None:
    """
    Resolve GHGRP facility to parent company using PARENT_NAME_MAP,
    parent_company field, corporate_events, entity_aliases, and prefix matching.
    """
    # Build company_key lookup once (cached on function attribute)
    if not hasattr(_resolve_ghgrp_parent, '_by_key'):
        _resolve_ghgrp_parent._by_key = {}
    by_key = {co['company_key']: co for co in companies}
    _resolve_ghgrp_parent._by_key = by_key

    # Get parent_company field from regulatory_evidence (GHGRP)
    re_rows = db.execute("""
        SELECT DISTINCT parent_company
        FROM regulatory_evidence
        WHERE source_system = 'epa_ghgrp' AND company_name = ?
          AND parent_company IS NOT NULL AND parent_company != ''
        LIMIT 10
    """, (facility_name,)).fetchall()

    for re_row in re_rows:
        # Handle multi-owner: pick majority owner
        parts = re_row['parent_company'].split(';')
        best_name = None
        best_pct = 0
        for part in parts:
            part = part.strip()
            m = re.search(r'\((\d+\.?\d*)%\)', part)
            pct = float(m.group(1)) if m else 0
            name = re.sub(r'\s*\([^)]*\)\s*$', '', part).strip()
            if pct > best_pct:
                best_pct = pct
                best_name = name
        if not best_name:
            best_name = re.sub(r'\s*\([^)]*\)', '', re_row['parent_company']).strip()

        parent_clean = best_name
        if not parent_clean:
            continue

        # Check PARENT_NAME_MAP first (handles all known variants)
        mapped = _resolve_by_parent_map(parent_clean, by_key)
        if mapped:
            return mapped

        parent_norm = normalize_name(parent_clean)
        parent_nospace = parent_norm.replace(' ', '')

        # Direct match
        for co in companies:
            co_norm = normalize_name(co['company_name'])
            co_nospace = co_norm.replace(' ', '')
            if co_norm == parent_norm or co_nospace == parent_nospace:
                return co
            # Suffix-stripped match
            parent_base = re.sub(r'\b(corp|inc|llc|ltd|co|company)\b', '', parent_norm).strip()
            co_base = re.sub(r'\b(corp|inc|llc|ltd|co|company)\b', '', co_norm).strip()
            if parent_base and co_base and (
                parent_base == co_base or
                parent_base.replace(' ', '') == co_base.replace(' ', '')
            ):
                return co

        # Corporate events: parent_company might be a known child name
        ce_row = db.execute("""
            SELECT c.company_id, c.company_key, c.company_name
            FROM corporate_events ce
            JOIN companies c ON ce.parent_company_id = c.company_id
            WHERE ce.child_name_norm = ?
            LIMIT 1
        """, (normalize_stripped(parent_clean),)).fetchone()
        if ce_row:
            return dict(ce_row)

    # Fallback: reuse the EPA ECHO 4-tier resolver (handles entity_aliases, prefix)
    result = _resolve_parent_company(facility_name, db, companies)
    if result:
        return result

    return None


def phase_2b_split_ghgrp_facilities(db: sqlite3.Connection) -> int:
    """Create one unified_projects row per distinct GHGRP facility name."""
    print('\n── Phase 2b: Split GHGRP facilities into sub-projects ────────')
    now = now_iso()

    companies = [dict(r) for r in db.execute(
        "SELECT company_id, company_key, company_name FROM companies"
    ).fetchall()]

    # Existing facility source_identities (from Phase 2 EPA ECHO)
    existing_identities = {}
    for r in db.execute("""
        SELECT project_id, source_identity, company_id
        FROM unified_projects WHERE project_type = 'facility'
    """):
        if r['source_identity']:
            existing_identities[r['source_identity']] = {
                'project_id': r['project_id'],
                'company_id': r['company_id'],
            }

    # Get all distinct GHGRP facility names
    facilities = db.execute("""
        SELECT DISTINCT company_name
        FROM regulatory_evidence
        WHERE source_system = 'epa_ghgrp' AND company_name IS NOT NULL
        ORDER BY company_name
    """).fetchall()

    created = skipped_no_parent = skipped_company_level = skipped_existing = 0
    overlap_updated = 0

    for fac in facilities:
        facility_name = fac['company_name']
        facility_upper = facility_name.upper().strip()

        # Resolve parent company
        parent = _resolve_ghgrp_parent(facility_name, db, companies)
        if not parent:
            skipped_no_parent += 1
            continue

        company_id = parent['company_id']
        company_key = parent['company_key']

        # Company-level detection: facility normalizes same as resolved company
        if normalize_stripped(facility_name) == normalize_stripped(parent['company_name']):
            skipped_company_level += 1
            continue

        # Check for existing EPA ECHO facility with same source_identity
        if facility_upper in existing_identities:
            # Reuse existing — just store GHGRP facility_id
            existing_pid = existing_identities[facility_upper]['project_id']
            ghgrp_fids = set()
            for r in db.execute("""
                SELECT DISTINCT facility_id
                FROM regulatory_evidence
                WHERE source_system = 'epa_ghgrp' AND company_name = ?
                  AND facility_id IS NOT NULL
            """, (facility_name,)):
                ghgrp_fids.add(r['facility_id'])
            if ghgrp_fids:
                # Merge with any existing facility_ids_json
                existing_fids = db.execute(
                    "SELECT facility_ids_json FROM unified_projects WHERE project_id = ?",
                    (existing_pid,)
                ).fetchone()
                old_fids = set(json.loads(existing_fids['facility_ids_json'] or '[]')) if existing_fids and existing_fids['facility_ids_json'] else set()
                merged_fids = sorted(old_fids | ghgrp_fids)
                db.execute("""
                    UPDATE unified_projects SET facility_ids_json = ?
                    WHERE project_id = ?
                """, (json.dumps(merged_fids), existing_pid))
                overlap_updated += 1
            skipped_existing += 1
            continue

        # Generate sub-project ID (different namespace from EPA ECHO)
        pid = sub_project_id(company_key + '::ghgrp::' + facility_upper)

        # Check if already exists (idempotent)
        if db.execute("SELECT 1 FROM unified_projects WHERE project_id = ?", (pid,)).fetchone():
            skipped_existing += 1
            continue

        # Collect GHGRP facility_ids
        ghgrp_fids = set()
        for r in db.execute("""
            SELECT DISTINCT facility_id
            FROM regulatory_evidence
            WHERE source_system = 'epa_ghgrp' AND company_name = ?
              AND facility_id IS NOT NULL
        """, (facility_name,)):
            ghgrp_fids.add(r['facility_id'])
        facility_ids_json = json.dumps(sorted(ghgrp_fids)) if ghgrp_fids else None

        # Extract city/state from raw_text_excerpt
        city = state = None
        excerpt_row = db.execute("""
            SELECT raw_text_excerpt
            FROM regulatory_evidence
            WHERE source_system = 'epa_ghgrp' AND company_name = ?
              AND raw_text_excerpt IS NOT NULL
            LIMIT 1
        """, (facility_name,)).fetchone()
        if excerpt_row and excerpt_row['raw_text_excerpt']:
            loc_match = re.search(
                r'Location:\s*(.+?),\s*(\w{2})\s+(\d{5})',
                excerpt_row['raw_text_excerpt']
            )
            if loc_match:
                city = loc_match.group(1).strip()
                state = loc_match.group(2).strip()

        # Create the facility project
        project_name = f"{parent['company_name']} - {facility_name}"
        db.execute("""
            INSERT OR IGNORE INTO unified_projects
            (project_id, project_name, developer_key, developer_name,
             company_id, project_type, source_identity, facility_ids_json,
             state, city, stage, fid_probability,
             co_developers_json, evidence_gap_flags_json,
             source_count, fid_status, quarantined,
             created_at, updated_at, bootstrap_source, country)
            VALUES (?,?,?,?,?,?,?,?,?,?,'unknown',0.10,'[]','[]',0,'unknown',0,?,?,'migration_ghgrp','US')
        """, (pid, project_name, company_key, parent['company_name'],
              company_id, 'facility', facility_upper, facility_ids_json,
              state, city, now, now))

        created += 1
        if created <= 10 or created % 25 == 0:
            loc = f"{city}, {state}" if city else "?"
            print(f'  + [{parent["company_name"][:20]:<20}] {facility_name[:45]:<45} {loc}')

    db.commit()
    print(f'  Created: {created}  ECHO overlap (updated): {overlap_updated}  '
          f'Company-level: {skipped_company_level}  No parent: {skipped_no_parent}  '
          f'Already existed: {skipped_existing}')
    return created


# ── Phase 3: Split DOE awards into separate projects ────────────────────────

def phase_3_split_doe_awards(db: sqlite3.Connection) -> int:
    """Create one unified_projects row per DOE award (usaspending URL)."""
    print('\n── Phase 3: Split DOE awards into sub-projects ──────────────')
    now = now_iso()

    companies = [dict(r) for r in db.execute(
        "SELECT company_id, company_key, company_name FROM companies"
    ).fetchall()]

    # Build lookup by normalized name
    co_by_norm = {}
    for co in companies:
        co_by_norm[normalize_name(co['company_name'])] = co

    awards = db.execute("""
        SELECT source_id, company_name, doe_award_status, doe_termination_date,
               document_date, document_url, state
        FROM doe_status
        WHERE document_url LIKE '%usaspending%'
    """).fetchall()

    created = skipped = existing = 0

    for award in awards:
        url = award['document_url']
        award_id = url.rstrip('/').split('/')[-1]  # e.g. DEEE0011525
        company_name = award['company_name']

        # Find parent company
        parent = None
        # Try exact match
        co_norm = normalize_name(company_name)
        if co_norm in co_by_norm:
            parent = co_by_norm[co_norm]

        # Try prefix match
        if not parent:
            for co in companies:
                cn = normalize_name(co['company_name'])
                if len(cn) >= 4 and (co_norm.startswith(cn) or cn in co_norm):
                    parent = co
                    break

        # Try alias lookup
        if not parent:
            alias_row = db.execute("""
                SELECT e.canonical_name
                FROM entity_aliases ea
                JOIN entities e ON e.entity_id = ea.entity_id
                WHERE ea.alias_norm = ?
            """, (co_norm,)).fetchone()
            if alias_row:
                canonical_norm = normalize_name(alias_row['canonical_name'])
                if canonical_norm in co_by_norm:
                    parent = co_by_norm[canonical_norm]

        if not parent:
            skipped += 1
            continue

        company_key = parent['company_key']
        pid = sub_project_id(company_key + '::doe::' + award_id)

        # Check if already exists
        exists = db.execute(
            "SELECT 1 FROM unified_projects WHERE project_id = ?", (pid,)
        ).fetchone()
        if exists:
            existing += 1
            continue

        # Map DOE status to project stage
        doe_status = award['doe_award_status'] or ''
        if doe_status == 'terminated':
            stage = 'cancelled'
        elif doe_status == 'active':
            stage = 'awarded'
        elif doe_status in ('at_risk', 'reduced', 'uncertain'):
            stage = 'awarded'
        else:
            stage = 'unknown'

        project_name = f"{parent['company_name']} - DOE {award_id}"
        state = award['state']

        db.execute("""
            INSERT OR IGNORE INTO unified_projects
            (project_id, project_name, developer_key, developer_name,
             company_id, project_type, doe_award_url,
             state, stage, fid_probability,
             co_developers_json, evidence_gap_flags_json,
             source_count, fid_status, quarantined,
             created_at, updated_at, bootstrap_source, country)
            VALUES (?,?,?,?,?,?,?,?,?,0.10,'[]','[]',0,'unknown',0,?,?,'migration','US')
        """, (pid, project_name, parent['company_key'], parent['company_name'],
              parent['company_id'], 'doe_award', url,
              state, stage, now, now))

        created += 1

        # Insert project_events for award date and termination date
        if award['document_date']:
            db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, source_id, source_url, description, created_at)
                VALUES (?, ?, 'award_date', ?, 'doe_status', ?, ?, ?, ?)
            """, (pid, parent['company_id'], award['document_date'],
                  str(award['source_id']), url,
                  f"DOE award {award_id} for {company_name}", now))

        if award['doe_termination_date']:
            db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, source_id, source_url, description, created_at)
                VALUES (?, ?, 'termination_date', ?, 'doe_status', ?, ?, ?, ?)
            """, (pid, parent['company_id'], award['doe_termination_date'],
                  str(award['source_id']), url,
                  f"DOE award {award_id} terminated", now))

        if created <= 10 or created % 25 == 0:
            print(f'  + [{parent["company_name"][:20]:<20}] DOE {award_id} → {stage}')

    db.commit()
    print(f'  Created: {created}  Skipped (no parent): {skipped}  '
          f'Already existed: {existing}')
    return created


# ── Phase 4: Merge confirmed duplicate EPA facilities ───────────────────────

CONFIRMED_MERGES = [
    # (primary_source_identity, secondary_source_identity, reason)
    # EPA ECHO naming variants
    ('LINDE - PA 497', 'LINDE FACILITY 0497', '2555 Savannah Ave, Port Arthur TX 77641'),
    ('LINDE-SWEENY', 'LINDE- SWEENY HYDROGEN PLANT', '8985 W CR 359, Sweeny TX 77480'),
    ('PRAXAIR INC.-LINDE DIVISION', 'UNION CARBIDE CORP LINDE DIV', '6710 Hogaboom Rd, Groves TX 77619'),
    ('WESTERN INTERNATIONAL GAS & CYLINDERS, INC.', 'WESTERN INTERNATIONAL GAS & CYLINDERS', '7173 Hwy 159E, Bellville TX 77418'),
    # ECHO↔GHGRP cross-reference (matched via shared FRS ID)
    ('AIR PRODUCTS LLC (PORT ARTHUR I)', 'AIR PRODUCTS PORT ARTHUR FACILITY', 'FRS 110043802819'),
    ('AIR PRODUCTS BAYTOWN II PLANT', 'AIR PRODUCTS BAYTOWN PLANT', 'FRS 110012710423'),
    ('PRAXAIR PORT ARTHUR', 'PRAXAIR PORT ARTHUR #379', 'FRS 110060241885'),
    ('AIR PRODUCTS, BAYTOWN III', 'AIR PRODUCTS BAYTOWN 3 FACILITY', 'FRS 110070200837'),
    ('LINDE - PA 497', 'PRAXAIR PORT ARTHUR FACILITY', 'FRS 110070081820'),
    ('PRAXAIR TEXAS CITY', 'LINDE TEXAS CITY', 'FRS 110000825037'),
    ('AIR PRODUCTS LLC', 'AIR PRODUCTS LLC - CORPUS CHRISTI', 'FRS 110067124258'),
    # GHGRP facility-id merges (same physical plant, renamed over time)
    ('LINDE INC TEXAS CITY HYDROGEN COMPLEX', 'PRAXAIR TEXAS CITY HYDROGEN COMPLEX', 'GHGRP 1000043'),
    ('LINDE FACILITY 0497', 'LINDE - PA 497', 'GHGRP 1002023'),
    ('MATHESON - LIMA I PLANT', 'LINDE GAS NORTH AMERICA LLC, LIMA I PLANT', 'GHGRP 1002042'),
    ('MATHESON - LIMA II PLANT', 'LINDE GAS NORTH AMERICA LLC, LIMA II PLANT', 'GHGRP 1002043'),
    ('MATHESON - LEMONT PLANT', 'LINDE GAS NORTH AMERICA LLC, LEMONT PLANT', 'GHGRP 1002044'),
    ('LINDE GAS NORTH AMERICA LLC, LA PORTE PLANT', 'LYONDELLBASELL ACETYLS LLC, LA PORTE SYNGAS PLANT', 'GHGRP 1002072'),
    ('LINDE - WHITING, IN 1-4', 'PRAXAIR - WHITING, IN 1-4', 'GHGRP 1002119'),
    ('LINDE - WHITING, IN 5&6', 'PRAXAIR - WHITING, IN 5&6', 'GHGRP 1002120'),
    ('LINDE INC - GEISMAR HYCO FACILITY', 'PRAXAIR INC - GEISMAR HYCO FACILITY', 'GHGRP 1003255'),
    ('MARATHON EL PASO REFINERY', 'ANDEAVOR EL PASO ALL SITES', 'GHGRP 1003564'),
    ('MARATHON EL PASO REFINERY', 'WESTERN REFINING EL PASO ALL SITES', 'GHGRP 1003564'),
    ('HF SINCLAIR EL DORADO REFINING LLC', 'HOLLYFRONTIER EL DORADO REFINING LLC', 'GHGRP 1004291'),
    ('LOS ANGELES REFINERY (LAR)', 'TESORO CARSON REFINERY', 'GHGRP 1006627'),
    ('LINDE ONTARIO CA', 'PRAXAIR ONTARIO CA', 'GHGRP 1006732'),
    ('EXXONMOBIL REFINING AND SUPPLY BILLINGS REFINERY', 'EXXONMOBIL FUELS & LUBRICANTS COMPANY BILLINGS REFINERY', 'GHGRP 1007000'),
    ('EXXONMOBIL REFINING AND SUPPLY BILLINGS REFINERY', 'PAR MONTANA, LLC BILLINGS REFINERY', 'GHGRP 1007000'),
    ('MATHESON - SARALAND PLANT', 'LINDE GAS NORTH AMERICA LLC, SARALAND PLANT', 'GHGRP 1007230'),
    ('LINDE DECATUR', 'LINDE GAS NORTH AMERICA LLC, DECATUR PLANT', 'GHGRP 1008002'),
    ('LINDE INC, ST. CHARLES FACILITY', 'PRAXAIR, ST. CHARLES FACILITY', 'GHGRP 1010879'),
    ('LINDE FACILITY 0379', 'PRAXAIR PORT ARTHUR', 'GHGRP 1011080'),
]
# NOTE: ExxonMobil BMRF (1795 Burt St) and BMCP (2775 Gulf States Rd) are at different
# addresses in Beaumont TX — legitimately separate facilities, NOT duplicates.


def phase_4_merge_duplicates(db: sqlite3.Connection) -> int:
    """Merge confirmed duplicate facility projects."""
    print('\n── Phase 4: Merge confirmed duplicate facilities ────────────')
    merged = 0

    for primary_identity, secondary_identity, reason in CONFIRMED_MERGES:
        primary = db.execute("""
            SELECT project_id, source_registry_ids, facility_ids_json
            FROM unified_projects
            WHERE source_identity = ? AND project_type = 'facility'
        """, (primary_identity,)).fetchone()

        secondary = db.execute("""
            SELECT project_id, source_registry_ids, facility_ids_json
            FROM unified_projects
            WHERE source_identity = ? AND project_type = 'facility'
        """, (secondary_identity,)).fetchone()

        if not primary:
            print(f'  SKIP: primary not found: {primary_identity}')
            continue
        if not secondary:
            print(f'  SKIP: secondary not found: {secondary_identity}')
            continue

        # Merge source_registry_ids (FRS IDs)
        pri_ids = set(json.loads(primary['source_registry_ids'] or '[]'))
        sec_ids = set(json.loads(secondary['source_registry_ids'] or '[]'))
        merged_ids = sorted(pri_ids | sec_ids)

        # Merge facility_ids_json (GHGRP facility IDs)
        pri_fids = set(json.loads(primary['facility_ids_json'] or '[]'))
        sec_fids = set(json.loads(secondary['facility_ids_json'] or '[]'))
        merged_fids = sorted(pri_fids | sec_fids)

        db.execute("""
            UPDATE unified_projects
            SET source_registry_ids = ?,
                facility_ids_json = ?
            WHERE project_id = ?
        """, (json.dumps(merged_ids),
              json.dumps(merged_fids) if merged_fids else None,
              primary['project_id']))

        # Remap any claims from secondary → primary
        moved = db.execute("""
            UPDATE project_claims SET project_id = ?
            WHERE project_id = ?
        """, (primary['project_id'], secondary['project_id'])).rowcount

        # Delete the secondary project
        db.execute(
            "DELETE FROM unified_projects WHERE project_id = ?",
            (secondary['project_id'],)
        )

        merged += 1
        claims_note = f', {moved} claims moved' if moved else ''
        print(f'  MERGED: {secondary_identity} → {primary_identity}  ({reason}{claims_note})')

    db.commit()
    print(f'  Merged: {merged}')
    return merged


def suggest_merges(db: sqlite3.Connection) -> None:
    """Print potential duplicates for manual review (does NOT auto-merge)."""
    print('\n── Suggested merges (manual review required) ─────────────────')
    rows = db.execute("""
        SELECT up.company_id, c.company_name,
               ep.facility_zip, up.source_identity,
               up.project_id
        FROM unified_projects up
        JOIN companies c ON c.company_id = up.company_id
        LEFT JOIN epa_permits ep ON ep.registry_id IN (
            SELECT value FROM json_each(COALESCE(up.source_registry_ids, '[]'))
        )
        WHERE up.project_type = 'facility' AND ep.facility_zip IS NOT NULL
        ORDER BY up.company_id, ep.facility_zip
    """).fetchall()

    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        key = (r['company_id'], r['facility_zip'])
        groups[key].append((r['source_identity'], r['project_id']))

    found = 0
    for (cid, zip5), items in sorted(groups.items()):
        if len(items) > 1:
            co_name = db.execute(
                "SELECT company_name FROM companies WHERE company_id = ?", (cid,)
            ).fetchone()['company_name']
            print(f'  {co_name} @ {zip5}:')
            for identity, pid in items:
                print(f'    - {identity} ({pid})')
            found += 1

    if not found:
        print('  No potential duplicates found.')


# ── Phase 5: Remap existing project_claims ──────────────────────────────────

def phase_5_remap_claims(db: sqlite3.Connection) -> int:
    """Remap resolved project_claims from company_portfolio to sub-projects."""
    print('\n── Phase 5: Remap existing project_claims ───────────────────')

    # Build lookup maps
    facility_map = {}  # source_identity → project_id
    facility_pids = set()  # all facility project_ids (for counter)
    for row in db.execute("""
        SELECT project_id, source_identity
        FROM unified_projects WHERE project_type = 'facility'
    """):
        facility_map[row['source_identity']] = row['project_id']
        facility_pids.add(row['project_id'])

    # Add merged secondary names: the secondary facility was deleted in Phase 4,
    # but claims still reference re.company_name which matches the secondary identity.
    for primary_id, secondary_id, _reason in CONFIRMED_MERGES:
        primary_pid = facility_map.get(primary_id)
        if primary_pid and secondary_id not in facility_map:
            facility_map[secondary_id] = primary_pid

    # Also build a registry_id → project_id map for merged facilities
    registry_map = {}  # registry_id → project_id
    for row in db.execute("""
        SELECT project_id, source_registry_ids
        FROM unified_projects WHERE project_type = 'facility'
    """):
        if row['source_registry_ids']:
            for rid in json.loads(row['source_registry_ids']):
                registry_map[rid] = row['project_id']

    doe_map = {}  # doe_award_url → project_id
    for row in db.execute("""
        SELECT project_id, doe_award_url
        FROM unified_projects WHERE project_type = 'doe_award'
    """):
        doe_map[row['doe_award_url']] = row['project_id']

    # Get all resolved project_claims pointing to company_portfolio projects
    claims_to_remap = db.execute("""
        SELECT pc.claim_id, pc.project_id, c.source_id
        FROM project_claims pc
        JOIN unified_projects up ON up.project_id = pc.project_id
        JOIN claims c ON c.claim_id = pc.claim_id
        WHERE up.project_type = 'company_portfolio'
          AND pc.project_id IS NOT NULL
    """).fetchall()

    remapped_facility = remapped_doe = stayed = 0

    for pc in claims_to_remap:
        source_id = pc['source_id']
        if not source_id:
            stayed += 1
            continue

        # Look up the regulatory_evidence record
        re_row = db.execute("""
            SELECT source_system, company_name, document_url
            FROM regulatory_evidence WHERE id = ?
        """, (source_id,)).fetchone()
        if not re_row:
            stayed += 1
            continue

        new_pid = None

        # EPA ECHO → route to facility
        if re_row['source_system'] == 'epa_echo':
            facility_name = (re_row['company_name'] or '').upper().strip()
            new_pid = facility_map.get(facility_name)

            # If not found by name (merged), try via registry IDs in claim text
            if not new_pid:
                claim_text = db.execute(
                    "SELECT claim_text FROM claims WHERE source_id = ? LIMIT 1",
                    (source_id,)
                ).fetchone()
                if claim_text:
                    for m in re.finditer(r'Registry ID:\s*(\d+)', claim_text['claim_text'] or ''):
                        rid = m.group(1)
                        if rid in registry_map:
                            new_pid = registry_map[rid]
                            break

        # EPA GHGRP → route to facility by name
        elif re_row['source_system'] in ('epa_ghgrp', 'epa_uic'):
            facility_name = (re_row['company_name'] or '').upper().strip()
            new_pid = facility_map.get(facility_name)

        # DOE (usaspending) → route to award
        elif re_row['document_url'] and 'usaspending' in re_row['document_url']:
            new_pid = doe_map.get(re_row['document_url'])

        if new_pid and new_pid != pc['project_id']:
            db.execute("""
                UPDATE project_claims SET project_id = ?
                WHERE claim_id = ?
            """, (new_pid, pc['claim_id']))
            if new_pid in facility_pids:
                remapped_facility += 1
            else:
                remapped_doe += 1
        else:
            stayed += 1

    db.commit()
    print(f'  Remapped to facility: {remapped_facility}')
    print(f'  Remapped to DOE award: {remapped_doe}')
    print(f'  Stayed on company_portfolio: {stayed}')

    # Integrity check
    orphans = db.execute("""
        SELECT COUNT(*) as c FROM project_claims pc
        WHERE pc.project_id NOT IN (SELECT project_id FROM unified_projects)
    """).fetchone()['c']
    if orphans:
        print(f'  ERROR: {orphans} orphan claims pointing to deleted projects!')
    else:
        print(f'  Integrity check passed: 0 orphan claims')

    return remapped_facility + remapped_doe


# ── Phase 6: Collect event dates ────────────────────────────────────────────

def _parse_date(text: str, kind: str = 'any') -> str | None:
    """Extract a date from claim text. Mirrors bootstrap_projects._parse_date."""
    if not text:
        return None
    if kind in ('cod', 'any'):
        m = re.search(
            r'(?:commercial\s+operat|commission|completion|online|start.up|began\s+operat).*?(\d{4})',
            text, re.I)
        if m:
            return m.group(1)
    if kind in ('fid', 'any'):
        m = re.search(
            r'(?:FID|final\s+investment\s+decision|positive\s+FID).*?(\d{4})',
            text, re.I)
        if m:
            return m.group(1)
    if kind in ('construction_start', 'any'):
        m = re.search(
            r'(?:begin\s+construction|construction\s+start|break\s+ground).*?(\d{4})',
            text, re.I)
        if m:
            return m.group(1)
    if 'award was made' not in text.lower():
        m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if m:
            return m.group(1)
    return None


# Claim types that signal specific event types
CLAIM_EVENT_MAP = {
    'construction_commenced': 'construction_start',
    'construction_start': 'construction_start',
    'construction_milestone': 'construction_start',
    'commercial_operations': 'cod_date',
    'commercial_operation_date': 'cod_date',
    'fid_reached': 'fid_date',
    'fid_decision': 'fid_date',
    'permit_received': 'permit_date',
    'permit_approved': 'permit_date',
    'operational_status': 'operational_date',
}


def phase_6_collect_events(db: sqlite3.Connection) -> int:
    """Populate project_events from structured sources."""
    print('\n── Phase 6: Collect event dates ─────────────────────────────')
    now = now_iso()
    inserted = 0

    # 6a: EPA permits → permit_expiration events
    facility_projects = db.execute("""
        SELECT project_id, company_id, source_registry_ids
        FROM unified_projects
        WHERE project_type = 'facility' AND source_registry_ids IS NOT NULL
    """).fetchall()

    for fp in facility_projects:
        frs_ids = json.loads(fp['source_registry_ids'] or '[]')
        for frs_id in frs_ids:
            permits = db.execute("""
                SELECT permit_expiration, permit_id, statute
                FROM epa_permits
                WHERE registry_id = ? AND permit_expiration IS NOT NULL
            """, (frs_id,)).fetchall()
            for permit in permits:
                result = db.execute("""
                    INSERT OR IGNORE INTO project_events
                    (project_id, company_id, event_type, event_date,
                     event_source, source_id, description, created_at)
                    VALUES (?, ?, 'permit_expiration', ?, 'epa_permits', ?,
                            ?, ?)
                """, (fp['project_id'], fp['company_id'],
                      permit['permit_expiration'], frs_id,
                      f"{permit['statute']} permit {permit['permit_id'] or ''}".strip(),
                      now))
                inserted += result.rowcount

    # 6b: Existing unified_projects date fields → events
    date_projects = db.execute("""
        SELECT project_id, company_id, fid_date, cod_date, construction_start
        FROM unified_projects
        WHERE fid_date IS NOT NULL OR cod_date IS NOT NULL
              OR construction_start IS NOT NULL
    """).fetchall()

    for p in date_projects:
        if p['fid_date']:
            result = db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, description, created_at)
                VALUES (?, ?, 'fid_date', ?, 'unified_projects',
                        'From existing project field', ?)
            """, (p['project_id'], p['company_id'], p['fid_date'], now))
            inserted += result.rowcount

        if p['cod_date']:
            result = db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, description, created_at)
                VALUES (?, ?, 'cod_date', ?, 'unified_projects',
                        'From existing project field', ?)
            """, (p['project_id'], p['company_id'], p['cod_date'], now))
            inserted += result.rowcount

        if p['construction_start']:
            result = db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, description, created_at)
                VALUES (?, ?, 'construction_start', ?, 'unified_projects',
                        'From existing project field', ?)
            """, (p['project_id'], p['company_id'],
                  p['construction_start'], now))
            inserted += result.rowcount

    # 6c: Claim-text date extraction (bootstrap)
    stage_claims = db.execute("""
        SELECT pc.project_id, up.company_id, c.claim_type, c.claim_text, c.claim_id
        FROM project_claims pc
        JOIN unified_projects up ON up.project_id = pc.project_id
        JOIN claims c ON c.claim_id = pc.claim_id
        WHERE c.claim_type IN (
            'construction_commenced', 'construction_start', 'construction_milestone',
            'commercial_operations', 'commercial_operation_date',
            'fid_reached', 'fid_decision',
            'permit_received', 'permit_approved',
            'operational_status'
        )
    """).fetchall()

    for sc in stage_claims:
        event_type = CLAIM_EVENT_MAP.get(sc['claim_type'])
        if not event_type:
            continue

        date = _parse_date(sc['claim_text'], kind=event_type.replace('_date', ''))
        if not date:
            continue

        result = db.execute("""
            INSERT OR IGNORE INTO project_events
            (project_id, company_id, event_type, event_date,
             event_source, source_id, description, confidence, created_at)
            VALUES (?, ?, ?, ?, 'claims', ?, ?, 0.70, ?)
        """, (sc['project_id'], sc['company_id'], event_type, date,
              sc['claim_id'],
              f"Extracted from {sc['claim_type']} claim", now))
        inserted += result.rowcount

    # 6d: regulatory_evidence.document_date → first/latest evidence
    evidence_dates = db.execute("""
        SELECT pc.project_id, up.company_id,
               MIN(re.document_date) as first_date,
               MAX(re.document_date) as latest_date
        FROM project_claims pc
        JOIN unified_projects up ON up.project_id = pc.project_id
        JOIN claims c ON c.claim_id = pc.claim_id
        JOIN regulatory_evidence re ON re.id = c.source_id
        WHERE re.document_date IS NOT NULL
        GROUP BY pc.project_id
    """).fetchall()

    for ed in evidence_dates:
        if ed['first_date']:
            result = db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, description, confidence, created_at)
                VALUES (?, ?, 'first_evidence', ?, 'regulatory_evidence',
                        'Earliest document date', 0.90, ?)
            """, (ed['project_id'], ed['company_id'], ed['first_date'], now))
            inserted += result.rowcount

        if ed['latest_date'] and ed['latest_date'] != ed['first_date']:
            result = db.execute("""
                INSERT OR IGNORE INTO project_events
                (project_id, company_id, event_type, event_date,
                 event_source, description, confidence, created_at)
                VALUES (?, ?, 'latest_evidence', ?, 'regulatory_evidence',
                        'Latest document date', 0.90, ?)
            """, (ed['project_id'], ed['company_id'], ed['latest_date'], now))
            inserted += result.rowcount

    db.commit()
    print(f'  Events inserted: {inserted}')
    return inserted


# ── Main ─────────────────────────────────────────────────────────────────────

def run_verification(db: sqlite3.Connection) -> None:
    """Run all verification queries after migration."""
    print('\n── Verification ─────────────────────────────────────────────')

    # 1. Every project has a company
    null_co = db.execute(
        "SELECT COUNT(*) as c FROM unified_projects WHERE company_id IS NULL"
    ).fetchone()['c']
    print(f'  Projects with NULL company_id: {null_co}  {"OK" if null_co == 0 else "FAIL"}')

    # 2. Project type breakdown
    print('  Project type breakdown:')
    for row in db.execute(
        "SELECT project_type, COUNT(*) as c FROM unified_projects GROUP BY project_type ORDER BY c DESC"
    ):
        print(f'    {row["project_type"]:<25} {row["c"]}')

    # 3. Linde check
    linde = db.execute("""
        SELECT project_type, stage, COUNT(*) as c
        FROM unified_projects
        WHERE company_id = (SELECT company_id FROM companies WHERE company_name = 'Linde')
        GROUP BY project_type, stage
    """).fetchall()
    if linde:
        print('  Linde breakdown:')
        for row in linde:
            print(f'    {row["project_type"]:<20} {row["stage"]:<15} {row["c"]}')

    # 4. No claims lost
    total_claims = db.execute("SELECT COUNT(*) as c FROM project_claims").fetchone()['c']
    print(f'  Total project_claims: {total_claims}')

    # 5. No orphan claims
    orphans = db.execute("""
        SELECT COUNT(*) as c FROM project_claims pc
        WHERE pc.project_id NOT IN (SELECT project_id FROM unified_projects)
    """).fetchone()['c']
    print(f'  Orphan claims: {orphans}  {"OK" if orphans == 0 else "FAIL"}')

    # 6. Event dates
    print('  Event types:')
    for row in db.execute(
        "SELECT event_type, COUNT(*) as c FROM project_events GROUP BY event_type ORDER BY c DESC"
    ):
        print(f'    {row["event_type"]:<25} {row["c"]}')

    # 7. Companies count
    co_count = db.execute("SELECT COUNT(*) as c FROM companies").fetchone()['c']
    print(f'  Companies: {co_count}')

    # 8. Unresolved EPA facilities
    unresolved = db.execute("""
        SELECT COUNT(DISTINCT re.company_name) as c
        FROM regulatory_evidence re
        WHERE re.source_system = 'epa_echo'
          AND UPPER(re.company_name) NOT IN (
            SELECT source_identity FROM unified_projects WHERE project_type = 'facility'
          )
    """).fetchone()['c']
    print(f'  Unresolved EPA facility names: {unresolved}')


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Migrate companies/projects')
    parser.add_argument('--dry-run', action='store_true', help='Print actions without writing')
    parser.add_argument('--phase', type=str, default=None, help='Run only this phase (0-6, 2b)')
    parser.add_argument('--suggest-merges', action='store_true', help='Print potential duplicates')
    args = parser.parse_args()

    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    if args.suggest_merges:
        suggest_merges(db)
        db.close()
        return

    phases = [
        (0,   'Pre-seed mergers',          phase_0_seed_mergers),
        (1,   'Create companies',          phase_1_create_companies),
        (2,   'Split EPA facilities',      phase_2_split_epa_facilities),
        ('2b', 'Split GHGRP facilities',   phase_2b_split_ghgrp_facilities),
        (3,   'Split DOE awards',          phase_3_split_doe_awards),
        (4,   'Merge duplicates',          phase_4_merge_duplicates),
        (5,   'Remap claims',              phase_5_remap_claims),
        (6,   'Collect events',            phase_6_collect_events),
    ]

    phase_lookup = {str(p[0]): p for p in phases}

    if args.phase is not None:
        key = str(args.phase)
        if key not in phase_lookup:
            print(f'Invalid phase: {args.phase}. Valid: {", ".join(str(p[0]) for p in phases)}')
            sys.exit(1)
        _, name, fn = phase_lookup[key]
        print(f'Running phase {key}: {name}')
        fn(db)
    else:
        print('='*60)
        print('  DecarbIQ Migration: Companies / Projects Separation')
        print('='*60)
        for phase_id, name, fn in phases:
            fn(db)

    # Always run verification
    run_verification(db)

    db.close()
    print('\nDone.')


if __name__ == '__main__':
    main()
