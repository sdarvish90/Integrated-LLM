#!/usr/bin/env python3
"""
bootstrap_projects.py — Build unified_projects from learning pipeline output
=============================================================================
Replaces the old core_database.db's unified_projects with
a claims-driven registry. Every entry is backed by structured claims.

Three phases:
  Phase 1 — Classify: LLM reviews each distinct company_name in claims
             and labels it: project_developer | university | contractor |
             government_agency | out_of_scope
             Only project_developers get entries.

  Phase 2 — Build: Create unified_projects rows from classified companies.
             Populate fields from their highest-signal claims.
             State from doe_status if available.
             Technology, capacity, stage from COMPETITOR/FID_SIGNAL claims.

  Phase 3 — Reconcile: Compare against old unified_projects from
             core_database.db.
             - Confirmed unmatched international projects (Yara, Shell,
               BASF, etc.) kept as stubs if real
             - Garbage entries (news headlines as developer names) dropped
             - No duplicates: if new registry already covers them, skip

Usage:
    python bootstrap_projects.py                  # full run
    python bootstrap_projects.py --classify-only  # phase 1 only
    python bootstrap_projects.py --build-only     # phases 2+3 (needs phase 1)
    python bootstrap_projects.py --reconcile-only # phase 3 only
    python bootstrap_projects.py --dry-run        # show what would happen
    python bootstrap_projects.py --stats          # current state
"""

import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import os
from groq import Groq

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT    = Path(__file__).resolve().parent.parent          # corpus_frequnecy/
CORE_DB  = _ROOT / 'data' / 'core_database.db'

GROQ_MODEL  = 'llama-3.3-70b-versatile'
GROQ_SLEEP  = 0.4

_GROQ_KEY = os.environ.get("GROQ_API_KEY") or (
    (_ROOT / "groq_api_token.txt").read_text().strip()
    if (_ROOT / "groq_api_token.txt").exists()
    else None
)

DRY_RUN        = '--dry-run'        in sys.argv
STATS_ONLY     = '--stats'          in sys.argv
CLASSIFY_ONLY  = '--classify'       in sys.argv
REGISTER_ONLY  = '--register'       in sys.argv
ENRICH_ONLY    = '--enrich'         in sys.argv
ENRICH_STAGES  = '--enrich-stages'  in sys.argv
APPLY_SIGNALS  = '--apply-signals'  in sys.argv
LABEL_TECH     = '--label-tech'     in sys.argv
VALIDATE_ONLY  = '--validate'       in sys.argv
AUDIT_ONLY     = '--audit'          in sys.argv
RECONCILE_ONLY = '--reconcile'      in sys.argv

# No hardcoded allow/deny lists — reconcile routes all entries through
# company_classifications (LLM, cached). Only project_developer entries
# are imported. Everything else is skipped regardless of origin.

# ── Classification prompt ─────────────────────────────────────────────────────
_CLASSIFY_PROMPT = """You are classifying company names from regulatory filings and news articles
to determine which ones represent companies that develop or operate hydrogen,
ammonia, or carbon capture projects.

Company name: "{company_name}"
Claim types seen for this company: {claim_types}
Sample claim text: "{sample_claim}"

Classify as exactly one of:
  project_developer  — company that owns/develops/operates H2, NH3, CCS, or
                       clean energy projects (including EPC contractors WITH
                       their own projects like Linde, Air Products)
  university         — academic institution, research lab, or university
  government_agency  — DOE, EPA, national lab (Argonne, NREL, PNNL, etc.)
  contractor         — pure EPC/engineering firm with no own project footprint
                       in the claims (Technip, Worley, pure consultants)
  out_of_scope       — unrelated company, financial firm, or not identifiable

Return JSON only:
{{
  "classification": "project_developer | university | government_agency | contractor | out_of_scope",
  "confidence": 0.0-1.0,
  "reasoning": "one sentence"
}}"""


# ── Parent company normalization (copied from migrate_companies.py) ──────────
_LEGAL_SUFFIXES_RE = re.compile(
    r'\b(llc|inc|incorporated|corp|corporation|company|co|limited|ltd|'
    r'holdings|group|energy|and|the|&|lp)\b', re.I
)


def _normalize_stripped(name: str) -> str:
    """Normalize with legal suffix stripping — for company-level detection."""
    if not name:
        return ''
    n = name.lower().strip()
    n = _LEGAL_SUFFIXES_RE.sub(' ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


PARENT_NAME_MAP = {
    'american air liquide holdings': 'air_liquide',
    'air liquide large industries us': 'air_liquide',
    'air liquide large industries u s': 'air_liquide',
    'air liquide usa': 'air_liquide', 'air liquide america': 'air_liquide',
    'valero': 'valero_energy', 'valero energy': 'valero_energy',
    'bp america': 'bp', 'bp products north america': 'bp',
    'conocophillips': 'conocophillips',
    'calumet specialty products partners': 'calumet',
    'calumet lubricants': 'calumet', 'calumet shreveport lubricants waxes': 'calumet',
    'chs': 'chs', 'ergon': 'ergon', 'koch industries': 'koch_industries',
    'suncor usa': 'suncor_energy', 'suncor': 'suncor_energy',
    'hess': 'hess', 'hess corproation': 'hess',
    'ascend performance materials': 'ascend_performance',
    'cvr': 'cvr_energy',
    'evonik': 'evonik', 'evonik industries': 'evonik', 'evonik goldschmidt': 'evonik',
    'motiva enterprises': 'motiva', 'celanese': 'celanese',
    'hunt consolidated': 'hunt_consolidated', 'hunt crude oil supply': 'hunt_consolidated',
    'olin': 'olin', 'alon usa': 'alon_usa', 'par pacific': 'par_pacific',
    'san joaquin refining': 'san_joaquin_refining',
    'pdv america': 'pdv_citgo', 'pdv holding': 'pdv_citgo',
    'solvay chemicals': 'solvay', 'solvay holding': 'solvay', 'solvay': 'solvay',
    'arctic slope regional': 'asrc', 'asrc': 'asrc',
    'lion oil': 'lion_oil', 'united refining': 'united_refining',
    'martin midstream partners': 'martin_midstream',
    'national cooperative refinery assoc': 'ncra',
    'national cooperative refining ass': 'ncra',
    'island services': 'island_energy', 'island investor': 'island_energy',
    'lyondellbasell acetyls': 'lyondellbasell', 'lyondellbasell': 'lyondellbasell',
    'buckeye port reading terminal': 'buckeye_partners',
    'montana refining': 'montana_refining',
    'red apple': 'red_apple_group', 'connacher oil gas': 'connacher',
    'husky': 'husky_energy',
    'energy transfer': 'energy_transfer',
    'enterprise products partners': 'enterprise_products',
    'enlink midstream': 'enlink_midstream',
    'dcp midstream': 'dcp_midstream', 'dcp midstream partners': 'dcp_midstream',
    'targa resources': 'targa_resources',
    'eagleclaw midstream services': 'eagleclaw_midstream',
    'dow chemical': 'dow', 'dow': 'dow',
    'devon': 'devon_energy', 'devon energy': 'devon_energy',
    'occidental petroleum': 'occidental_petroleum', 'basf': 'basf',
    'eog resources': 'eog_resources', 'kinder morgan': 'kinder_morgan',
    'crestwood equity partners': 'crestwood_midstream',
    'crestwood midstream partners': 'crestwood_midstream',
    'oci partners': 'oci_partners',
    'exxon mobil': 'exxon_mobil', 'exxonmobil': 'exxon_mobil',
    'shell petroleum': 'shell', 'shell oil': 'shell',
    'chevron': 'chevron_usa',
    'linde gas north america': 'linde', 'matheson tri gas': 'linde',
    'enbridge us': 'enbridge',
    'marathon petroleum': 'marathon_petroleum',
    'martinez refining': 'marathon_petroleum',
    'hollyfrontier': 'hf_sinclair', 'sinclair companies': 'hf_sinclair',
    'sinclair cos': 'hf_sinclair', 'sinclair oil': 'hf_sinclair',
    'western refining': 'andeavor', 'western refining southwest': 'andeavor',
    'frontier refining marketing': 'andeavor',
    'phillips 66': 'phillips_66', 'delek us': 'delek_us',
    'rentech nitrogen partners': 'cvr_energy', 'rentech': 'cvr_energy',
    'air products': 'air_products', 'air products chemicals': 'air_products',
    'praxair': 'linde',
    'tesoro': 'andeavor',
}


def _normalize_parent_company(raw: str, key_to_name: dict) -> str | None:
    """Normalize a parent company name through PARENT_NAME_MAP → company_key → display name."""
    pn = _normalize_stripped(raw)
    key = PARENT_NAME_MAP.get(pn)
    if not key:
        words = pn.split()
        if len(words) >= 2:
            for length in range(len(words) - 1, 1, -1):
                prefix = ' '.join(words[:length])
                key = PARENT_NAME_MAP.get(prefix)
                if key:
                    break
    if key and key in key_to_name:
        return key_to_name[key]
    return None



def _classify_one(client, company_name: str, claim_types: str = '',
                  sample_claim: str = '') -> tuple[str, float, str]:
    """
    Call Groq to classify a single company name.
    Returns (classification, confidence, reasoning).
    Shared by phase_classify and phase_reconcile so both use identical logic.
    """
    prompt = _CLASSIFY_PROMPT.format(
        company_name = company_name,
        claim_types  = claim_types[:200],
        sample_claim = sample_claim[:150],
    )
    try:
        resp = client.chat.completions.create(
            model       = GROQ_MODEL,
            messages    = [{'role': 'user', 'content': prompt}],
            temperature = 0.0,
            max_tokens  = 150,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
        raw = re.sub(r'\s*```$',          '', raw, flags=re.DOTALL)
        result = json.loads(raw)
        return (
            result.get('classification', 'out_of_scope'),
            float(result.get('confidence', 0.5)),
            result.get('reasoning', ''),
        )
    except Exception as e:
        return 'out_of_scope', 0.0, f'error: {e}'



import hashlib


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_developer_key(name: str) -> str:
    """Slugify company name to developer_key format."""
    key = name.lower().strip()
    key = re.sub(r'[^\w\s-]', '', key)
    key = re.sub(r'\s+', '_', key)
    key = re.sub(r'_+', '_', key).strip('_')
    return key[:80]


def deterministic_project_id(developer_key: str) -> str:
    """Stable project_id: MD5(developer_key)[:12].
    Same company always gets the same ID on every run so
    connect.py foreign keys in project_claims never break."""
    return hashlib.md5(developer_key.encode()).hexdigest()[:12]


GULF_STATES   = {'LA', 'TX', 'MS', 'AL'}
MIDWEST       = {'OH', 'IN', 'IL', 'IA', 'KS', 'NE', 'MN', 'WI', 'MO', 'MI'}
NORTHEAST     = {'PA', 'NY', 'NJ', 'MA', 'CT', 'RI', 'NH', 'VT', 'ME', 'DE', 'MD'}
WEST          = {'CA', 'WA', 'OR', 'NV', 'AZ', 'CO', 'UT', 'NM', 'WY', 'MT', 'ID'}
ALL_US_STATES = (
    GULF_STATES | MIDWEST | NORTHEAST | WEST |
    {'AK', 'HI', 'DC', 'WV', 'VA', 'NC', 'SC', 'GA', 'FL',
     'TN', 'KY', 'AR', 'OK', 'ND', 'SD', 'NM', 'AZ', 'MT', 'WY'}
)


def state_to_region(state: str) -> str | None:
    abbr = (state or '').strip().upper()[:2]
    if abbr in GULF_STATES:   return 'Gulf Coast'
    if abbr in MIDWEST:       return 'Midwest'
    if abbr in NORTHEAST:     return 'Northeast'
    if abbr in WEST:          return 'West'
    return None


def fid_prob_from_stage(stage: str | None) -> float:
    return {'operational': 1.0, 'construction': 0.85,
            'fid': 0.90, 'announced': 0.15}.get(stage or '', 0.10)


def extract_state(text: str) -> str | None:
    m = re.search(r'\b([A-Z]{2})\b(?:\s|$|,|\.)', text)
    if m and m.group(1) in ALL_US_STATES:
        return m.group(1)
    m2 = re.search(r'(?:located in|in|at)\s+([A-Za-z\s]{3,40})', text)
    if m2:
        return m2.group(1).strip()[:50]
    return None


# ── Tier 1 claim type vocabulary ──────────────────────────────────────────────
STAGE_CLAIM_TYPES = {
    'construction_commenced':                 'construction',
    'construction_commencement':              'construction',
    'notice_to_proceed':                      'construction',
    'commercial_project_under_construction':  'construction',
    'final_investment_decision':              'fid',
    'final_investment_decision_made':         'fid',
    'financial_close':                        'fid',
    'financing_arrangements':                 'fid',
    'financing_commitments':                  'fid',
    'financing_structure':                    'fid',
    'low_carbon_ammonia_sales_began':         'operational',
    'commercial_operations':                  'operational',
    'commercial_operations_commenced':        'operational',
    'co2_capture_operational':                'operational',
    'site_construction_completed':            'operational',
    'support_facility_construction_completed':'operational',
    'project_announced':                      'announced',
    'blue_point_joint_venture_announced':     'announced',
}

TECHNOLOGY_CLAIM_TYPES = {
    'technology_description', 'project_technology',
    'technology_approach',    'production_technology',
    'technology_development', 'technology_focus_area',
    'carbon_capture_project', 'technology_partnership',
}

CAPACITY_CLAIM_TYPES = {
    'project_capacity',                      'production_capacity',
    'capacity_mtpa',                         'train_capacity',
    'project_capacity_specification',        'electrolyser_capacity',
    'hydrogen_production_target',            'production_target',
    'ammonia_production_facility_capacity',  'ammonia_annual_production',
    'ammonia_production_milestone',
}

# ── Tier 2 extended stage vocabulary for LLM inference ───────────────────────
STAGE_DEFINITIONS = {
    'pre_announcement': 'Only rumoured or analyst-mentioned; no company confirmation',
    'announced':        'Company formally confirmed project via press release or filing',
    'feasibility':      'Feasibility or concept study underway or completed',
    'feed':             'Front-End Engineering Design contracted or underway',
    'permitted':        'Environmental or construction permits filed or granted',
    'awarded':          'Government grant or DOE/federal award received for this project',
    'financed':         'Equity committed, debt arranged, or financing secured',
    'fid':              'Final investment decision made',
    'construction':     'Physical construction underway on site',
    'commissioning':    'Mechanical completion reached; startup testing underway',
    'operational':      'Producing and in commercial operation',
    'on_hold':          'Project explicitly paused or delayed indefinitely',
    'cancelled':        'Project explicitly terminated',
    'unknown':          'Insufficient evidence to determine stage',
}

STAGE_FID_PROB = {
    'pre_announcement': 0.05,
    'announced':        0.15,
    'feasibility':      0.20,
    'feed':             0.35,
    'permitted':        0.45,
    'awarded':          0.30,
    'financed':         0.75,
    'fid':              0.90,
    'construction':     0.85,
    'commissioning':    0.95,
    'operational':      1.00,
    'on_hold':          0.10,
    'cancelled':        0.00,
    'unknown':          0.10,
}

# ── Stage transition rules ────────────────────────────────────────────────────
# Forward progress: announced → awarded → permitted → fid → construction → operational
# Terminal states: cancelled, on_hold (can only be set, not overwritten by earlier stages)
# Rule: a newer date always wins UNLESS the transition is invalid (regression).
#
# VALID_STAGE_TRANSITIONS[current] = set of stages that can replace it.
# If a proposed stage is NOT in the set, the transition is blocked regardless of date.
VALID_STAGE_TRANSITIONS: dict[str, set[str]] = {
    'unknown':          {'pre_announcement', 'announced', 'feasibility', 'feed',
                         'awarded', 'permitted', 'financed', 'fid', 'construction',
                         'commissioning', 'operational', 'on_hold', 'cancelled'},
    'pre_announcement': {'announced', 'feasibility', 'feed', 'awarded', 'permitted',
                         'financed', 'fid', 'construction', 'commissioning',
                         'operational', 'on_hold', 'cancelled'},
    'announced':        {'feasibility', 'feed', 'awarded', 'permitted', 'financed',
                         'fid', 'construction', 'commissioning', 'operational',
                         'on_hold', 'cancelled'},
    'feasibility':      {'feed', 'awarded', 'permitted', 'financed', 'fid',
                         'construction', 'commissioning', 'operational',
                         'on_hold', 'cancelled'},
    'feed':             {'awarded', 'permitted', 'financed', 'fid', 'construction',
                         'commissioning', 'operational', 'on_hold', 'cancelled'},
    'awarded':          {'permitted', 'financed', 'fid', 'construction',
                         'commissioning', 'operational', 'on_hold', 'cancelled'},
    'permitted':        {'financed', 'fid', 'construction', 'commissioning',
                         'operational', 'on_hold', 'cancelled'},
    'financed':         {'fid', 'construction', 'commissioning', 'operational',
                         'on_hold', 'cancelled'},
    'fid':              {'construction', 'commissioning', 'operational',
                         'on_hold', 'cancelled'},
    'construction':     {'commissioning', 'operational', 'on_hold', 'cancelled'},
    'commissioning':    {'operational', 'on_hold', 'cancelled'},
    'operational':      {'on_hold', 'cancelled'},
    'on_hold':          {'announced', 'feasibility', 'feed', 'awarded', 'permitted',
                         'financed', 'fid', 'construction', 'commissioning',
                         'operational', 'cancelled'},  # can resume
    'cancelled':        set(),  # terminal — nothing overwrites cancelled
}


def should_update_stage(current_stage: str | None, current_date: str | None,
                        new_stage: str, new_date: str | None,
                        force: bool = False) -> bool:
    """Decide whether a proposed stage update should be applied.

    Rules (in priority order):
      1. If force=True, always update (used by DOE termination override).
      2. If current stage is None/'unknown', accept any new stage.
      3. If the transition is invalid (regression), block it.
      4. If the new evidence is dated same or later, accept forward transitions.
      5. If the new evidence is older than current, block it —
         old news shouldn't overwrite newer status.
    """
    cur = (current_stage or 'unknown').strip().lower()
    new = new_stage.strip().lower()

    if force:
        return True

    if cur == 'unknown' or cur == '' or cur is None:
        return True

    if cur == new:
        return False  # no change needed

    # Check transition validity
    valid_targets = VALID_STAGE_TRANSITIONS.get(cur, set())
    if new not in valid_targets:
        return False  # invalid regression (e.g. operational → announced)

    # Date comparison: newer evidence wins for valid forward transitions
    if new_date and current_date:
        return new_date >= current_date
    elif new_date and not current_date:
        return True   # any dated evidence beats undated
    else:
        return True   # no dates to compare, allow valid forward transition


STAGE_ALIASES = {
    'operating': 'operational',
    'in_operation': 'operational',
    'under_construction': 'construction',
}


def apply_stage_update(db, project_id: str, new_stage: str,
                       new_date: str | None, reasoning: str | None,
                       confidence: float | None = None,
                       force: bool = False) -> bool:
    """Conditionally update a project's stage using temporal + transition rules.

    Returns True if the update was applied, False if blocked.
    """
    # Normalize aliases and validate vocabulary
    new_stage = STAGE_ALIASES.get(new_stage, new_stage)
    if new_stage not in VALID_STAGE_TRANSITIONS:
        return False

    row = db.execute(
        "SELECT stage, stage_latest_evidence_date FROM unified_projects WHERE project_id=?",
        (project_id,)
    ).fetchone()
    if not row:
        return False

    current_stage = row['stage']
    current_date = row['stage_latest_evidence_date']

    if not should_update_stage(current_stage, current_date,
                               new_stage, new_date, force=force):
        return False

    fid_prob = STAGE_FID_PROB.get(new_stage, 0.10)
    now = now_iso()
    updates = [
        "stage=?", "fid_probability=?",
        "stage_latest_evidence_date=COALESCE(?, stage_latest_evidence_date)",
        "updated_at=?"
    ]
    params = [new_stage, fid_prob, new_date, now]

    if reasoning:
        updates.append("stage_reasoning=?")
        params.append(reasoning[:500])
    if confidence is not None:
        updates.append("stage_confidence=?")
        params.append(confidence)

    params.append(project_id)
    db.execute(
        f"UPDATE unified_projects SET {', '.join(updates)} WHERE project_id=?",
        params
    )
    return True


_STAGE_INFERENCE_PROMPT = """You are assessing the development stage of a hydrogen,
ammonia, or carbon capture project based on regulatory filings and news articles.

Company: {company}
State: {state}
Technology: {technology}

Most informative claims (highest confidence first):
{claims_text}

Choose the single most accurate stage:
{stage_options}

Rules:
- "awarded" means a government/DOE grant was received — use this if the main
  evidence is a federal award with no further construction signals
- "announced" means the company itself confirmed the project exists
- Only use "feasibility" or "feed" if there is explicit language about studies
- Prefer more advanced stages when evidence supports it
- Use "unknown" only when claims give no project-stage signal at all
- If confidence is below 0.5, return "unknown"

Return JSON only:
{{
  "stage": "<stage from list above>",
  "confidence": 0.0-1.0,
  "reasoning": "one sentence citing the specific claim that drove this decision"
}}"""


# ── Schema migration ──────────────────────────────────────────────────────────
def migrate_schema(db: sqlite3.Connection) -> None:
    """Create company_classifications and unified_projects if not present."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS company_classifications (
            company_name       TEXT PRIMARY KEY,
            classification     TEXT,
            confidence         REAL,
            reasoning          TEXT,
            claim_count        INTEGER,
            classified_at      TEXT
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS unified_projects (
            project_id                TEXT PRIMARY KEY,
            project_name              TEXT,
            developer_key             TEXT,
            developer_name            TEXT,
            state                     TEXT,
            city                      TEXT,
            region                    TEXT,
            technology                TEXT,
            product                   TEXT,
            capacity_raw              TEXT,
            capacity_mtpa_h2          REAL,
            stage                     TEXT,
            stage_confidence          REAL,
            stage_evidence_count      INTEGER DEFAULT 0,
            stage_reasoning           TEXT,
            stage_latest_evidence_date TEXT,
            fid_probability           REAL,
            epc_contractor            TEXT,
            co_developers_json        TEXT DEFAULT '[]',
            fid_date                  TEXT,
            cod_date                  TEXT,
            construction_start        TEXT,
            evidence_gap_flags_json   TEXT DEFAULT '[]',
            created_at                TEXT,
            updated_at                TEXT,
            last_evidence_date        TEXT,
            source_count              INTEGER DEFAULT 0,
            value_chain               TEXT,
            end_use_sector            TEXT,
            fid_status                TEXT DEFAULT 'unknown',
            quarantined               INTEGER DEFAULT 0,
            quarantine_reason         TEXT,
            technology_label          TEXT,
            bootstrap_source          TEXT,  -- 'claims' | 'old_pipeline_stub'
            country                   TEXT DEFAULT 'US'
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_up_devkey ON unified_projects(developer_key)")

    # Source documents table — single source of truth for all ingested docs.
    # Previously lived in core_database.db is now the single source of truth.
    db.executescript("""
        CREATE TABLE IF NOT EXISTS regulatory_evidence (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name        TEXT,
            company_cik         TEXT,
            document_type       TEXT,
            document_date       TEXT,
            document_url        TEXT,
            raw_text_excerpt    TEXT,
            excerpt_char_count  INTEGER,
            source_system       TEXT,
            ingested_at         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_re_company ON regulatory_evidence(company_name);
        CREATE INDEX IF NOT EXISTS idx_re_cik     ON regulatory_evidence(company_cik);
        CREATE INDEX IF NOT EXISTS idx_re_type    ON regulatory_evidence(document_type);
        CREATE INDEX IF NOT EXISTS idx_re_date    ON regulatory_evidence(document_date);
    """)

    # New columns on regulatory_evidence for structured EPA enrichment
    for col_def in [
        ('operator_name',      'TEXT'),
        ('llc_entity',         'TEXT'),
        ('parent_company',     'TEXT'),
        ('derived_stage',      'TEXT'),
        ('derived_technology', 'TEXT'),
    ]:
        try:
            db.execute(f"ALTER TABLE regulatory_evidence ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # technology_label on unified_projects (LLM-inferred short label)
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN technology_label TEXT")
    except sqlite3.OperationalError:
        pass

    # country column on unified_projects (default US, for international expansion)
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN country TEXT DEFAULT 'US'")
    except sqlite3.OperationalError:
        pass

    # EPA facility address — stores the official EPA address when it differs from city
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN epa_facility_address TEXT")
    except sqlite3.OperationalError:
        pass

    # Permit ID — verbatim from LLM-extracted claims (EPA permits, UIC Class VI, etc.)
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN permit_id TEXT")
    except sqlite3.OperationalError:
        pass

    # Parent company — from LLM-extracted claims (ownership, subsidiary relationships)
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN parent_company TEXT")
    except sqlite3.OperationalError:
        pass

    # Structured per-permit table — one row per permit per facility
    db.executescript("""
        CREATE TABLE IF NOT EXISTS epa_permits (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id      TEXT NOT NULL,
            facility_name    TEXT,
            facility_street  TEXT,
            facility_city    TEXT,
            facility_state   TEXT,
            facility_zip     TEXT,
            permit_id        TEXT,
            statute          TEXT,
            epa_system       TEXT,
            permit_class     TEXT,
            permit_status    TEXT,
            permit_areas     TEXT,
            permit_expiration TEXT,
            naics_code       TEXT,
            sic_code         TEXT,
            collected_at     TEXT,
            UNIQUE(registry_id, permit_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ep_registry ON epa_permits(registry_id);
        CREATE INDEX IF NOT EXISTS idx_ep_statute  ON epa_permits(statute);
    """)

    # Compliance summary — one row per statute per facility
    db.executescript("""
        CREATE TABLE IF NOT EXISTS epa_compliance_summary (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            registry_id      TEXT NOT NULL,
            statute          TEXT,
            source_id        TEXT,
            current_snc      TEXT,
            quarters_in_nc   INTEGER,
            inspections_3yr  INTEGER,
            last_inspection  TEXT,
            formal_actions   INTEGER,
            total_penalties  REAL,
            collected_at     TEXT,
            UNIQUE(registry_id, source_id, statute)
        );
        CREATE INDEX IF NOT EXISTS idx_ecs_registry ON epa_compliance_summary(registry_id);
    """)

    # ── Data quality fixes (idempotent) ──────────────────────────────────────
    # Issue 8: Normalize "operating" → "operational", fix fid_probability
    db.execute("""
        UPDATE unified_projects SET stage='operational', fid_probability=1.0
        WHERE stage='operating'
    """)
    # Issue 1: Clear orphaned free-text technology on company_portfolio with 0 tech claims
    db.execute("""
        UPDATE unified_projects SET technology=NULL
        WHERE project_type='company_portfolio'
          AND length(technology) > 60
          AND project_id NOT IN (
              SELECT DISTINCT pc.project_id FROM project_claims pc
              JOIN claims c ON pc.claim_id = c.claim_id
              WHERE c.claim_type IN ('technology','capture_technology','production_technology')
                AND pc.resolution_method != 'unresolved'
          )
    """)
    # Issue 1b: Clear DOE project descriptions written as technology by old sub-pass 5
    db.execute("""
        UPDATE unified_projects SET technology=NULL
        WHERE project_type='doe_award'
          AND length(technology) > 60
    """)
    # Issue 2: Set derived_technology for existing Subpart P records
    db.execute("""
        UPDATE regulatory_evidence SET derived_technology='hydrogen_production'
        WHERE source_system='epa_ghgrp' AND document_type='ghgrp_subpart_p'
          AND (derived_technology IS NULL OR derived_technology='')
    """)
    # Issue 4a: Clear well-count capacity (bare numbers without units, < 10 chars)
    db.execute("""
        UPDATE unified_projects SET capacity_raw=NULL
        WHERE capacity_raw IS NOT NULL
          AND capacity_raw NOT LIKE '%MW%' AND capacity_raw NOT LIKE '%GW%'
          AND capacity_raw NOT LIKE '%MTPA%' AND capacity_raw NOT LIKE '%ton%'
          AND capacity_raw NOT LIKE '%mt/%' AND capacity_raw NOT LIKE '%bbl%'
          AND capacity_raw NOT LIKE '%kg%'
          AND capacity_raw NOT GLOB '*[0-9]* million*ton*'
          AND length(capacity_raw) < 10
    """)
    # Issue 4b: Clear capacity_raw without recognized number+unit pattern
    db.execute("""
        UPDATE unified_projects SET capacity_raw=NULL
        WHERE capacity_raw IS NOT NULL
          AND capacity_raw NOT LIKE '%MW%' AND capacity_raw NOT LIKE '%GW%'
          AND capacity_raw NOT LIKE '%MTPA%' AND capacity_raw NOT LIKE '%ton%'
          AND capacity_raw NOT LIKE '%mt/%' AND capacity_raw NOT LIKE '%bbl%'
          AND capacity_raw NOT LIKE '%kg%' AND capacity_raw NOT LIKE '%tCO2%'
          AND capacity_raw NOT GLOB '*[0-9]* million*'
          AND capacity_raw NOT GLOB '*[0-9]*M *'
    """)
    # Issue 7: Fix county-in-city — extract just the city portion (before first comma)
    db.execute("""
        UPDATE unified_projects
        SET city = SUBSTR(city, 1, INSTR(city, ',') - 1)
        WHERE city LIKE '%County%' AND city LIKE '%,%'
    """)
    # Issue 3b: Clear circular parent_company (parent = developer)
    db.execute("""
        UPDATE unified_projects SET parent_company=NULL
        WHERE parent_company IS NOT NULL
          AND UPPER(TRIM(parent_company)) = UPPER(TRIM(developer_name))
    """)
    # Issue 5: Clear future dates on operational company_portfolio projects
    db.execute("""
        UPDATE unified_projects SET cod_date=NULL
        WHERE project_type='company_portfolio' AND stage='operational'
          AND cod_date > '2026'
    """)
    db.execute("""
        UPDATE unified_projects SET construction_start=NULL
        WHERE project_type='company_portfolio' AND stage='operational'
          AND construction_start > '2025'
    """)
    # Technology normalization for existing short values
    _TECH_NORMALIZE = {
        'smr': 'steam_methane_reforming',
        'steam methane reforming': 'steam_methane_reforming',
        'atr': 'autothermal_reforming',
        'autothermal reforming': 'autothermal_reforming',
        'pox': 'partial_oxidation',
        'partial oxidation': 'partial_oxidation',
        'smr, pox': 'hydrogen_production',
        'smr, pox, or other processes': 'hydrogen_production',
        'steam methane reforming (smr), partial oxidation (pox)': 'hydrogen_production',
        'steam methane reforming (smr), partial oxidation (pox), or other processes': 'hydrogen_production',
        'smr | pox': 'hydrogen_production',
        'ccs': 'point_source_ccs',
        'carbon capture': 'point_source_ccs',
        'carbon capture and storage': 'point_source_ccs',
        'dac': 'direct_air_capture',
        'direct air capture': 'direct_air_capture',
        'electrolysis': 'electrolysis',
    }
    for old_val, new_val in _TECH_NORMALIZE.items():
        db.execute("""
            UPDATE unified_projects SET technology=?
            WHERE LOWER(TRIM(technology)) = ?
        """, (new_val, old_val))

    # ── New tables for Stage 1 data source integration ────────────────────

    # TCEQ Central Registry permits
    db.executescript("""
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
        );
        CREATE INDEX IF NOT EXISTS idx_tp_rn ON tceq_permits(rn_number);
        CREATE INDEX IF NOT EXISTS idx_tp_entity ON tceq_permits(entity_name);
    """)

    # PHMSA pipeline infrastructure
    db.executescript("""
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
        );
        CREATE INDEX IF NOT EXISTS idx_pi_operator ON pipeline_infrastructure(operator_id);
        CREATE INDEX IF NOT EXISTS idx_pi_commodity ON pipeline_infrastructure(commodity);
    """)

    # RRC UIC CO2 well inventory (Stage 2 — collect_rrc_uic.py)
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

    # source_keyword on claims — LLM cites which phrase drove its classification
    try:
        db.execute("ALTER TABLE claims ADD COLUMN source_keyword TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists

    # Additional columns on regulatory_evidence for ERCOT/PHMSA structured fields
    for col_def in [
        ('facility_id',   'TEXT'),
        ('permit_id',     'TEXT'),
        ('project_name',  'TEXT'),
    ]:
        try:
            db.execute(f"ALTER TABLE regulatory_evidence ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass  # column already exists

    # company_id FK on unified_projects (for _match_company direct enrichment)
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN company_id TEXT")
    except sqlite3.OperationalError:
        pass

    db.commit()
    print("  Schema ready.")


# ── Phase 1: Classify companies ──────────────────────────────────────────────
def classify_companies(db: sqlite3.Connection) -> dict[str, str]:
    """
    For each distinct company_name in claims, call LLM to classify.
    Returns {company_name: classification}.
    Skips already-classified entries.
    """
    print('\n── Phase 1: Company Classification ───────────────────────────')
    client = Groq(api_key=_GROQ_KEY)

    # All distinct companies with their claim profile
    companies = db.execute("""
        SELECT
            company_name,
            COUNT(*) claim_count,
            GROUP_CONCAT(DISTINCT claim_type) claim_types,
            MAX(claim_text) sample_claim
        FROM claims
        WHERE superseded_by IS NULL
        GROUP BY company_name
        ORDER BY claim_count DESC
    """).fetchall()

    already_done = {
        r['company_name']: r['classification']
        for r in db.execute("SELECT company_name, classification FROM company_classifications")
    }

    print(f'  {len(companies)} distinct companies  '
          f'({len(already_done)} already classified)')

    classifications = dict(already_done)
    new_classified  = 0
    cost_total      = 0.0

    for row in companies:
        cname = row['company_name']
        if cname in already_done:
            continue

        claim_types  = (row['claim_types'] or '')[:200]
        sample_claim = (row['sample_claim'] or '')[:150]

        prompt = _CLASSIFY_PROMPT.format(
            company_name = cname,
            claim_types  = claim_types,
            sample_claim = sample_claim,
        )

        try:
            resp = Groq(api_key=_GROQ_KEY).chat.completions.create(
                model   = GROQ_MODEL,
                messages= [{'role': 'user', 'content': prompt}],
                temperature  = 0.0,
                max_tokens   = 150,
            )
            raw  = resp.choices[0].message.content.strip()
            raw  = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw  = re.sub(r'\s*```$', '', raw, flags=re.DOTALL)
            result = json.loads(raw)

            classification = result.get('classification', 'out_of_scope')
            confidence     = float(result.get('confidence', 0.5))
            reasoning      = result.get('reasoning', '')

            # Estimate cost (rough)
            cost = (len(prompt) / 4 / 1_000_000) * 0.59 + (50 / 1_000_000) * 0.79
            cost_total += cost

        except Exception as e:
            print(f'  LLM error for {cname}: {e}')
            classification = 'out_of_scope'
            confidence     = 0.0
            reasoning      = f'error: {e}'

        if not DRY_RUN:
            db.execute("""
                INSERT OR REPLACE INTO company_classifications
                (company_name, classification, confidence, reasoning,
                 claim_count, classified_at)
                VALUES (?,?,?,?,?,?)
            """, (cname, classification, confidence, reasoning,
                  row['claim_count'], now_iso()))
            db.commit()

        classifications[cname] = classification
        new_classified += 1

        marker = '✓' if classification == 'project_developer' else '·'
        print(f'  {marker} [{row["claim_count"]:3d} claims]  '
              f'{cname[:40]:<40}  → {classification}')
        time.sleep(GROQ_SLEEP)

    print(f'\n  Classified: {new_classified}  Estimated cost: ${cost_total:.3f}')

    # Summary
    from collections import Counter
    counts = Counter(classifications.values())
    print('\n  Classification breakdown:')
    for cls, n in counts.most_common():
        print(f'    {cls:<20} {n}')

    return classifications


# ── Phase 2: Register project stubs ──────────────────────────────────────────

def _sync_classification(db: sqlite3.Connection,
                         canonical_name: str,
                         raw_names_csv: str) -> None:
    """
    Ensure company_classifications has a row for canonical_name.

    When phase_enrich promotes a raw claim name to a canonical name
    (e.g. "air products" → "Air Products"), the join between
    unified_projects and company_classifications breaks unless we
    copy the classification over to the canonical form.

    Strategy:
      1. If canonical_name already classified — nothing to do.
      2. If any raw_name variant is classified — copy to canonical.
      3. If nothing found — insert as project_developer with low confidence
         (reconcile already ran LLM; this is a last-resort fallback).
    """
    if not canonical_name:
        return

    # Already classified under this exact name?
    existing = db.execute(
        "SELECT 1 FROM company_classifications WHERE company_name = ?",
        (canonical_name,)
    ).fetchone()
    if existing:
        return

    # Try each raw name variant
    for raw in raw_names_csv.split(','):
        raw = raw.strip()
        if not raw or raw == canonical_name:
            continue
        row = db.execute(
            "SELECT classification, confidence, reasoning "
            "FROM company_classifications WHERE company_name = ?",
            (raw,)
        ).fetchone()
        if row:
            db.execute("""
                INSERT OR IGNORE INTO company_classifications
                (company_name, classification, confidence, reasoning,
                 claim_count, classified_at)
                VALUES (?,?,?,?,0,?)
            """, (canonical_name, row['classification'],
                  row['confidence'], row['reasoning'], now_iso()))
            db.commit()
            return

    # Last resort: insert as project_developer (it reached this point via
    # reconcile's LLM gate, so it was already validated as a developer)
    db.execute("""
        INSERT OR IGNORE INTO company_classifications
        (company_name, classification, confidence, reasoning,
         claim_count, classified_at)
        VALUES (?,?,?,?,0,?)
    """, (canonical_name, 'project_developer', 0.70,
          'auto-synced from canonical name resolution', now_iso()))
    db.commit()


def phase_register(db: sqlite3.Connection) -> int:
    """Create one stub per project_developer. Deterministic IDs. Idempotent."""
    print('\n── Phase 2: Register project stubs ──────────────────────────────')

    developers = [
        r['company_name']
        for r in db.execute(
            "SELECT company_name FROM company_classifications "
            "WHERE classification = 'project_developer'"
        )
    ]
    print(f'  {len(developers)} project_developer names')

    doe_state: dict[str, str] = {}
    try:
        for r in db.execute(
            "SELECT company_name, state FROM doe_status WHERE state IS NOT NULL"
        ):
            if r['company_name'] not in doe_state:
                doe_state[r['company_name']] = r['state']
    except sqlite3.OperationalError:
        print('  (doe_status not found — states will be empty)')

    created = existing = 0
    now = now_iso()

    for company in developers:
        dev_key    = make_developer_key(company)
        project_id = deterministic_project_id(dev_key)
        state      = doe_state.get(company)
        if not state:
            state = MANUALLY_VERIFIED_FIELDS.get(company, {}).get('state')
        region     = state_to_region(state) if state else None

        if DRY_RUN:
            print(f'  [dry-run] {dev_key:<40}  id={project_id}  state={state}')
            created += 1
            continue

        # Dedup: skip if a normalized variant already exists.
        # Prevents DOE name variants like 'Air Products & Chemicals' and
        # 'Air Products and Chemicals' from creating ghost stubs alongside
        # the canonical 'Air Products' entry.
        dev_key_norm = re.sub(r'[^a-z0-9]', '', dev_key)  # strip all non-alnum
        ghost = db.execute("""
            SELECT developer_key FROM unified_projects
            WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                      LOWER(developer_key),
                      '_and_',''), '&',''), '_inc',''), '_llc',''), '_corp','')
                = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(
                      LOWER(?),
                      '_and_',''), '&',''), '_inc',''), '_llc',''), '_corp','')
              AND developer_key != ?
        """, (dev_key, dev_key)).fetchone()
        if ghost:
            existing += 1
            continue

        result = db.execute("""
            INSERT OR IGNORE INTO unified_projects
            (project_id, project_name, developer_key, developer_name,
             company_id, project_type,
             state, region, stage, fid_probability,
             co_developers_json, evidence_gap_flags_json,
             source_count, fid_status, quarantined,
             created_at, updated_at, bootstrap_source)
            VALUES (?,?,?,?,?,?,?,?,'unknown',0.10,'[]','[]',0,'unknown',0,?,?,'claims')
        """, (project_id, company, dev_key, company,
              project_id, 'company_portfolio',  # company_id == project_id for company_portfolio
              state, region, now, now))

        if result.rowcount:
            created  += 1
            # Also ensure a companies row exists
            db.execute("""
                INSERT OR IGNORE INTO companies
                (company_id, company_key, company_name, company_type, country,
                 created_at, updated_at)
                VALUES (?, ?, ?, 'developer', 'US', ?, ?)
            """, (project_id, dev_key, company, now, now))
        else:
            existing += 1
            # Backfill company_id if missing on existing projects
            db.execute("""
                UPDATE unified_projects
                SET company_id = ?, project_type = COALESCE(project_type, 'company_portfolio')
                WHERE project_id = ? AND company_id IS NULL
            """, (project_id, project_id))

    db.commit()
    print(f'  Created: {created}  Already existed: {existing}')
    return created


# ── Phase 3: Enrich from resolved claims ─────────────────────────────────────
def phase_enrich(db: sqlite3.Connection) -> None:
    """Update fields from post-connect.py resolved claims.
    COALESCE: never overwrites existing non-null values.
    Collapses alias variants into the canonical deterministic-ID row."""
    print('\n── Phase 3: Enrich from resolved claims ─────────────────────────')

    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone():
        print('  project_claims not found — run connect.py first')
        return

    resolved = db.execute("""
        SELECT pc.project_id, e.canonical_name,
               COUNT(DISTINCT pc.claim_id)           AS claim_count,
               GROUP_CONCAT(DISTINCT c.company_name) AS raw_names
        FROM   project_claims pc
        JOIN   claims c  ON c.claim_id  = pc.claim_id
        LEFT JOIN entities e ON e.entity_id = pc.entity_id
        WHERE  pc.resolution_method != 'unresolved'
        GROUP  BY pc.project_id
        ORDER  BY claim_count DESC
    """).fetchall()

    print(f'  {len(resolved)} project_ids with resolved claims')
    updated = inserted = 0

    for proj in resolved:
        project_id     = proj['project_id']
        canonical      = proj['canonical_name'] or ''
        raw_names      = proj['raw_names'] or ''
        developer_name = canonical or raw_names.split(',')[0].strip()
        dev_key        = make_developer_key(developer_name)
        det_id         = deterministic_project_id(dev_key)

        # Sync classification: if canonical name differs from raw names,
        # copy the classification record to the canonical name so downstream
        # joins on developer_name never break.
        _sync_classification(db, developer_name, raw_names)

        claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence,
                   c.document_date, c.document_url
            FROM   project_claims pc
            JOIN   claims c ON c.claim_id = pc.claim_id
            WHERE  pc.project_id = ?
              AND  pc.resolution_method != 'unresolved'
              AND  c.superseded_by IS NULL
            ORDER  BY c.confidence DESC, c.document_date DESC
        """, (project_id,)).fetchall()

        stage = stage_confidence = stage_reasoning = stage_date = None
        location = technology = capacity_raw = capacity_mtpa = None

        for c in claims:
            ct, text, conf = c['claim_type'] or '', c['claim_text'] or '', c['confidence'] or 0.5

            if stage is None and ct in STAGE_CLAIM_TYPES:
                stage, stage_confidence = STAGE_CLAIM_TYPES[ct], conf
                stage_reasoning, stage_date = text[:200], c['document_date']

            if location is None and ct in ('facility_location',
                                            'project_location', 'facility_name'):
                location = extract_state(text)

            if technology is None and ct in TECHNOLOGY_CLAIM_TYPES:
                technology = text[:200]

            if capacity_raw is None and ct in CAPACITY_CLAIM_TYPES:
                _cap_parsed, _cap_mtpa_parsed = _parse_capacity(text)
                if _cap_parsed:
                    capacity_raw = _cap_parsed
                    capacity_mtpa = _cap_mtpa_parsed
                # Legacy fallback for MTPA extraction only
                m = re.search(r'([\d,\.]+)\s*(?:mtpa|mt/yr|million\s+ton)',
                              text, re.I)
                if m and capacity_mtpa is None:
                    try:
                        capacity_mtpa = float(m.group(1).replace(',', ''))
                    except ValueError:
                        pass

        if not location and raw_names:
            names = [n.strip() for n in raw_names.split(',')]
            ph = ','.join('?' * len(names))
            try:
                row = db.execute(
                    f"SELECT state FROM doe_status "
                    f"WHERE company_name IN ({ph}) AND state IS NOT NULL LIMIT 1",
                    names
                ).fetchone()
                if row:
                    location = row['state']
            except sqlite3.OperationalError:
                pass

        region   = state_to_region(location) if location else None
        fid_prob = fid_prob_from_stage(stage)
        now      = now_iso()

        if DRY_RUN:
            print(f'  [dry-run] {project_id}  {developer_name[:30]:<30}  '
                  f'stage={stage}  state={location}  n={len(claims)}')
            updated += 1
            continue

        has_det  = db.execute(
            "SELECT 1 FROM unified_projects WHERE project_id=?", (det_id,)
        ).fetchone()
        has_conn = db.execute(
            "SELECT 1 FROM unified_projects WHERE project_id=?", (project_id,)
        ).fetchone()

        if not has_det and not has_conn:
            db.execute("""
                INSERT OR IGNORE INTO unified_projects
                (project_id, project_name, developer_key, developer_name,
                 state, region, technology, capacity_raw, capacity_mtpa_h2,
                 stage, stage_confidence, stage_evidence_count,
                 stage_reasoning, stage_latest_evidence_date, fid_probability,
                 co_developers_json, evidence_gap_flags_json,
                 source_count, fid_status, quarantined,
                 created_at, updated_at, bootstrap_source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'[]','[]',?,'unknown',0,?,?,'claims')
            """, (
                det_id, developer_name, dev_key, developer_name,
                location, region, technology, capacity_raw, capacity_mtpa,
                stage or 'unknown', stage_confidence, len(claims),
                stage_reasoning, stage_date, fid_prob,
                len(claims), now, now
            ))
            inserted += 1
        else:
            target_id = det_id if has_det else project_id
            # Update non-stage fields with COALESCE (first-write-wins for data)
            db.execute("""
                UPDATE unified_projects SET
                    developer_name             = ?,
                    developer_key              = ?,
                    state      = COALESCE(NULLIF(state,''), ?),
                    region     = COALESCE(NULLIF(region,''), ?),
                    technology = COALESCE(NULLIF(technology,''), ?),
                    capacity_raw               = COALESCE(NULLIF(capacity_raw,''), ?),
                    capacity_mtpa_h2           = COALESCE(capacity_mtpa_h2, ?),
                    stage_evidence_count       = ?,
                    source_count               = ?,
                    updated_at                 = ?
                WHERE project_id = ?
            """, (
                developer_name, dev_key,
                location, region, technology, capacity_raw, capacity_mtpa,
                len(claims), len(claims), now,
                target_id
            ))
            # Stage update: date-aware with transition rules
            if stage and stage != 'unknown':
                apply_stage_update(
                    db, target_id, stage, stage_date,
                    reasoning=stage_reasoning, confidence=stage_confidence
                )
            # Only merge alias-variant rows for company_portfolio projects.
            # Never collapse facility or doe_award sub-projects into their parent.
            if has_det and has_conn and det_id != project_id:
                proj_type = db.execute(
                    "SELECT project_type FROM unified_projects WHERE project_id=?",
                    (project_id,)
                ).fetchone()
                if not proj_type or proj_type[0] == 'company_portfolio':
                    db.execute(
                        "UPDATE project_claims SET project_id=? WHERE project_id=?",
                        (det_id, project_id)
                    )
                    db.execute(
                        "DELETE FROM unified_projects WHERE project_id=?", (project_id,)
                    )
            updated += 1

    db.commit()
    print(f'  Updated: {updated}  Inserted (new from connect): {inserted}')


# ── Phase 4: Enrich stages via LLM inference ─────────────────────────────────
def phase_enrich_stages(db: sqlite3.Connection) -> None:
    """
    Tier 2 stage inference for projects still 'unknown' after tier 1.

    For each unknown project:
      1. Pull up to 15 highest-confidence resolved claims
      2. Call LLM with full extended stage vocabulary + claim context
      3. Write stage, confidence, reasoning if confidence >= 0.5

    Extended vocabulary adds: pre_announcement, announced, feasibility,
    feed, permitted, awarded, financed, commissioning, on_hold, cancelled.

    The 'awarded' stage correctly captures DOE grant recipients that tier 1
    misses because their claim types are award/research, not construction/FID.

    Idempotent: only processes projects where stage='unknown'
    AND stage_confidence IS NULL (never yet through tier 2).
    """
    print('\n── Phase 4: Enrich stages (LLM inference) ───────────────────────')

    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone():
        print('  project_claims not found — run connect.py and --enrich first')
        return

    unknown_projects = db.execute("""
        SELECT up.project_id, up.developer_name, up.state,
               up.technology, up.source_count
        FROM   unified_projects up
        WHERE  up.stage = 'unknown'
          AND  up.stage_confidence IS NULL
          AND  up.bootstrap_source = 'claims'
        ORDER  BY up.source_count DESC
    """).fetchall()

    print(f'  {len(unknown_projects)} projects to infer stage for')
    if not unknown_projects:
        print('  Nothing to do.')
        return

    stage_options = '\n'.join(
        f'  {s:<20} — {d}' for s, d in STAGE_DEFINITIONS.items()
    )

    client     = Groq(api_key=_GROQ_KEY)
    inferred   = 0
    no_claims  = 0
    low_conf   = 0
    cost_total = 0.0
    now        = now_iso()

    for proj in unknown_projects:
        project_id = proj['project_id']

        claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence,
                   c.document_type, c.document_date
            FROM   project_claims pc
            JOIN   claims c ON c.claim_id = pc.claim_id
            WHERE  pc.project_id = ?
              AND  pc.resolution_method != 'unresolved'
              AND  c.superseded_by IS NULL
            ORDER  BY c.confidence DESC, c.document_date DESC
            LIMIT  15
        """, (project_id,)).fetchall()

        if not claims:
            no_claims += 1
            if not DRY_RUN:
                db.execute("""
                    UPDATE unified_projects
                    SET stage_confidence = 0.0,
                        stage_reasoning  = 'no resolved claims available',
                        updated_at       = ?
                    WHERE project_id = ?
                """, (now, project_id))
            continue

        claims_text = ''
        for i, c in enumerate(claims, 1):
            claims_text += (
                f'{i}. [{c["claim_type"]}] '
                f'(conf={c["confidence"]:.2f}, '
                f'doc={c["document_type"]}, '
                f'date={c["document_date"] or "?"})\n'
                f'   {(c["claim_text"] or "")[:200]}\n'
            )

        prompt = _STAGE_INFERENCE_PROMPT.format(
            company      = proj['developer_name'] or '',
            state        = proj['state'] or 'unknown',
            technology   = proj['technology'] or 'unknown',
            claims_text  = claims_text,
            stage_options= stage_options,
        )

        if DRY_RUN:
            print(f'  [dry-run] {(proj["developer_name"] or "")[:40]:<40}  '
                  f'n_claims={len(claims)}')
            inferred += 1
            continue

        try:
            resp = client.chat.completions.create(
                model       = GROQ_MODEL,
                messages    = [{'role': 'user', 'content': prompt}],
                temperature = 0.0,
                max_tokens  = 200,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw = re.sub(r'\s*```$',          '', raw, flags=re.DOTALL)
            result = json.loads(raw)

            stage      = result.get('stage', 'unknown')
            confidence = float(result.get('confidence', 0.0))
            reasoning  = result.get('reasoning', '')
            cost_total += (len(prompt) / 4 / 1_000_000) * 0.59 + (200 / 1_000_000) * 0.79

            if stage not in STAGE_DEFINITIONS:
                stage = 'unknown'

        except Exception as e:
            print(f'  LLM error for {proj["developer_name"]}: {e}')
            stage, confidence, reasoning = 'unknown', 0.0, f'error: {e}'

        if confidence < 0.5:
            low_conf += 1
            stage     = 'unknown'

        marker   = '✓' if stage != 'unknown' else '·'
        evidence_date = claims[0]['document_date'] if claims else None

        if stage != 'unknown':
            apply_stage_update(
                db, project_id, stage, new_date=evidence_date,
                reasoning=reasoning, confidence=confidence
            )
        else:
            # Still record that LLM tried and returned unknown
            db.execute("""
                UPDATE unified_projects SET
                    stage_confidence = ?, stage_reasoning = ?, updated_at = ?
                WHERE project_id = ?
            """, (confidence, reasoning, now, project_id))
        db.commit()

        inferred += 1
        print(f'  {marker} {(proj["developer_name"] or "")[:40]:<40}  '
              f'→ {stage:<20}  conf={confidence:.2f}')
        time.sleep(GROQ_SLEEP)

    print(f'\n  Inferred:         {inferred}')
    print(f'  Low confidence:   {low_conf}')
    print(f'  No claims:        {no_claims}')
    print(f'  Estimated cost:   ${cost_total:.3f}')



# ── Phase 5: Apply accumulator signals + extract from claims + EPA permits ────

def _parse_city_from_text(text: str) -> str | None:
    """Extract city name from EPA location text."""
    if not text:
        return None
    # "The facility is located in CITYNAME, ST." (case-insensitive, allows dots for ST. JAMES)
    m = re.search(r'(?:located (?:in|at))\s+([A-Za-z][A-Za-z.\s]+?),\s*(?:[A-Z]{2}|[A-Z])', text, re.I)
    if m:
        return m.group(1).strip().title()
    # "The facility location is CITYNAME, ST." (alternate phrasing)
    m = re.search(r'(?:location is)\s+([A-Za-z][A-Za-z.\s]+?),\s*(?:[A-Z]{2}|[A-Z])', text, re.I)
    if m:
        return m.group(1).strip().title()
    # "Location: CITYNAME, COUNTY County, ST"
    m = re.search(r'Location:\s*([A-Za-z][A-Za-z.\s]+?),\s', text, re.I)
    if m:
        return m.group(1).strip().title()
    # "Facility: ... Location: CITYNAME, ST"
    m = re.search(r'Location:\s*([A-Za-z][A-Za-z.\s]+?)(?:,|\s*$)', text, re.I)
    if m:
        return m.group(1).strip().title()
    # "Address: ..., CITY, ST ZIP" — city is the second-to-last comma segment before state+zip
    m = re.search(r',\s*([A-Za-z][A-Za-z.\s]+?),\s*[A-Z]{2}\s+\d{5}', text)
    if m:
        return m.group(1).strip().title()
    return None


def _parse_capacity(text: str) -> tuple[str | None, float | None]:
    """Extract capacity_raw and capacity_mtpa_h2 from claim text.

    Only returns raw text if it contains a recognized number+unit pattern
    or is very short (<= 30 chars with digits).  Rejects sentence-length text.
    """
    if not text:
        return None, None
    mtpa = None
    m_mtpa = re.search(
        r'([\d,\.]+)\s*(?:mtpa|mt/yr|million\s+(?:metric\s+)?ton)', text, re.I)
    if m_mtpa:
        try:
            mtpa = float(m_mtpa.group(1).replace(',', ''))
        except ValueError:
            pass
    # Extract a capacity substring with number+unit
    m_cap = re.search(
        r'[\d,\.]+\s*(?:MW|GW|MTPA|mt/yr|'
        r'ton(?:ne)?s?(?:/(?:yr|year|day))?|bbl|mcf|kg(?:/day)?|'
        r'million\s+(?:metric\s+)?ton)',
        text, re.I)
    if m_cap:
        return m_cap.group(0).strip(), mtpa
    # Short text with digits + recognized unit — allow as-is
    if len(text.strip()) <= 30 and re.search(
            r'\d.*(?:MW|GW|MTPA|mt|ton|bbl|mcf|kg|million)', text, re.I):
        return text.strip(), mtpa
    return None, mtpa


def _parse_epc_contractor(text: str) -> str | None:
    """Extract EPC contractor name from claim text."""
    if not text:
        return None
    # "entered into ... with Bechtel" / "EPC contract ... Bechtel"
    m = re.search(r'(?:EPC|FEED|engineering|construction)\s+(?:contract|agreement).*?(?:with|to)\s+([A-Z][A-Za-z\s&]+?)(?:\s+(?:for|to|a |,|\.))', text, re.I)
    if m:
        return m.group(1).strip()[:80]
    # Known EPC names in text
    for name in ['Bechtel', 'Fluor', 'Technip', 'KBR', 'Worley', 'Jacobs',
                 'Black & Veatch', 'McDermott', 'Samsung', 'Kiewit', 'CTCI']:
        if name.lower() in text.lower():
            return name
    return None


def _parse_date(text: str, kind: str = 'any') -> str | None:
    """Extract a date from timeline claim text. Returns YYYY or YYYY-MM-DD."""
    if not text:
        return None
    # "expected in 2027" / "commissioning expected in 2029" / "began operations"
    if kind in ('cod', 'any'):
        m = re.search(r'(?:commercial\s+operat|commission|completion|online|start.up|began\s+operat).*?(\d{4})', text, re.I)
        if m:
            return m.group(1)
    if kind in ('fid', 'any'):
        m = re.search(r'(?:FID|final\s+investment\s+decision|positive\s+FID).*?(\d{4})', text, re.I)
        if m:
            return m.group(1)
    if kind in ('construction_start', 'any'):
        m = re.search(r'(?:begin\s+construction|construction\s+start|break\s+ground|begin\s+construction\s+of).*?(\d{4})', text, re.I)
        if m:
            return m.group(1)
    # Generic date fallback — but SKIP award dates (DOE award ≠ FID/COD/construction)
    if 'award was made' not in text.lower():
        m = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if m:
            return m.group(1)
    return None


# ── Validation Rules ──────────────────────────────────────────────────────────

# Major US cities by state for D6 city-in-state validation.
# Covers industrial cities that appear in hydrogen/CCS/ammonia data.
US_CITIES: dict[str, set[str]] = {
    'AL': {'birmingham', 'huntsville', 'mobile', 'montgomery', 'tuscaloosa', 'decatur', 'wilsonville'},
    'AK': {'anchorage', 'fairbanks', 'juneau'},
    'AZ': {'phoenix', 'tucson', 'mesa', 'chandler', 'scottsdale', 'tempe', 'glendale', 'gilbert'},
    'AR': {'little rock', 'fayetteville', 'fort smith', 'jonesboro', 'pine bluff'},
    'CA': {'los angeles', 'san francisco', 'san diego', 'sacramento', 'san jose', 'fresno', 'oakland',
           'long beach', 'bakersfield', 'anaheim', 'stockton', 'riverside', 'irvine', 'rodeo',
           'martinez', 'richmond', 'torrance', 'carson', 'wilmington', 'el segundo', 'benicia',
           'yuba city', 'lebec', 'menlo park'},
    'CO': {'denver', 'colorado springs', 'aurora', 'boulder', 'fort collins', 'pueblo', 'lakewood'},
    'CT': {'hartford', 'new haven', 'bridgeport', 'stamford', 'waterbury', 'danbury', 'norwalk',
           'north haven', 'wallingford'},
    'DE': {'wilmington', 'dover', 'newark'},
    'FL': {'miami', 'orlando', 'tampa', 'jacksonville', 'st. petersburg', 'fort lauderdale',
           'tallahassee', 'jupiter', 'mulberry', 'lakeland'},
    'GA': {'atlanta', 'savannah', 'augusta', 'columbus', 'macon', 'norcross', 'kennesaw'},
    'HI': {'honolulu', 'hilo'},
    'ID': {'boise', 'idaho falls', 'nampa'},
    'IL': {'chicago', 'aurora', 'rockford', 'joliet', 'springfield', 'peoria', 'decatur', 'naperville'},
    'IN': {'indianapolis', 'fort wayne', 'evansville', 'south bend', 'gary', 'bloomington',
           'mitchell', 'middletown'},
    'IA': {'des moines', 'cedar rapids', 'davenport', 'sioux city', 'iowa city', 'shenandoah'},
    'KS': {'wichita', 'overland park', 'kansas city', 'topeka'},
    'KY': {'louisville', 'lexington', 'bowling green', 'catlettsburg', 'ashland', 'paducah'},
    'LA': {'new orleans', 'baton rouge', 'shreveport', 'lafayette', 'lake charles', 'kenner',
           'donaldsonville', 'geismar', 'taft', 'waggaman', 'sterlington', 'killona',
           'addis', 'plaquemine', 'gramercy', 'gonzales', 'belle chasse', 'norco', 'uncle sam',
           'westlake', 'port allen', 'convent', 'st. james'},
    'ME': {'portland', 'bangor', 'lewiston'},
    'MD': {'baltimore', 'silver spring', 'bethesda', 'rockville', 'annapolis', 'columbia'},
    'MA': {'boston', 'worcester', 'springfield', 'cambridge', 'lowell', 'somerville', 'holyoke'},
    'MI': {'detroit', 'grand rapids', 'warren', 'ann arbor', 'lansing', 'flint', 'dearborn',
           'plymouth', 'okemos', 'midland'},
    'MN': {'minneapolis', 'st. paul', 'rochester', 'duluth', 'bloomington'},
    'MS': {'jackson', 'gulfport', 'biloxi', 'hattiesburg', 'yazoo city'},
    'MO': {'kansas city', 'st. louis', 'springfield', 'columbia', 'jefferson city'},
    'MT': {'billings', 'missoula', 'great falls', 'helena'},
    'NE': {'omaha', 'lincoln', 'bellevue'},
    'NV': {'las vegas', 'reno', 'henderson', 'sparks'},
    'NH': {'manchester', 'nashua', 'concord'},
    'NJ': {'newark', 'jersey city', 'paterson', 'elizabeth', 'trenton', 'edison', 'linden'},
    'NM': {'albuquerque', 'santa fe', 'las cruces', 'farmington'},
    'NY': {'new york', 'buffalo', 'rochester', 'yonkers', 'syracuse', 'albany', 'schenectady',
           'niskayuna', 'massena', 'latham'},
    'NC': {'charlotte', 'raleigh', 'greensboro', 'durham', 'winston-salem', 'cary', 'pendleton'},
    'ND': {'fargo', 'bismarck', 'grand forks', 'minot', 'center'},
    'OH': {'columbus', 'cleveland', 'cincinnati', 'toledo', 'akron', 'dayton', 'middletown',
           'canton', 'youngstown'},
    'OK': {'oklahoma city', 'tulsa', 'norman', 'broken arrow', 'ponca city'},
    'OR': {'portland', 'eugene', 'salem', 'bend', 'corvallis', 'medford'},
    'PA': {'philadelphia', 'pittsburgh', 'allentown', 'erie', 'reading', 'scranton', 'lancaster',
           'harrisburg', 'tyrone'},
    'RI': {'providence', 'warwick', 'cranston'},
    'SC': {'charleston', 'columbia', 'greenville', 'anderson', 'pendleton'},
    'SD': {'sioux falls', 'rapid city', 'aberdeen'},
    'TN': {'nashville', 'memphis', 'knoxville', 'chattanooga', 'brentwood', 'murfreesboro'},
    'TX': {'houston', 'dallas', 'san antonio', 'austin', 'fort worth', 'el paso', 'arlington',
           'corpus christi', 'baytown', 'beaumont', 'freeport', 'texas city', 'la porte',
           'pasadena', 'deer park', 'port arthur', 'port neches', 'sweeny', 'old ocean',
           'odessa', 'midland', 'amarillo', 'borger', 'ingleside', 'gregory', 'channelview',
           'mont belvieu', 'sugar land', 'chambers county', 'italy', 'big spring',
           'bridge city', 'nederland', 'orange', 'longview'},
    'UT': {'salt lake city', 'north salt lake', 'provo', 'west jordan', 'ogden', 'sandy'},
    'VT': {'burlington', 'south burlington', 'rutland'},
    'VA': {'virginia beach', 'norfolk', 'richmond', 'arlington', 'alexandria', 'chesapeake'},
    'WA': {'seattle', 'spokane', 'tacoma', 'vancouver', 'bellevue', 'everett', 'kent', 'renton'},
    'WV': {'charleston', 'huntington', 'morgantown', 'parkersburg', 'ravenswood'},
    'WI': {'milwaukee', 'madison', 'green bay', 'kenosha', 'racine'},
    'WY': {'cheyenne', 'casper', 'laramie', 'gillette'},
    'DC': {'washington'},
}

# Non-US location keywords for E4 country detection
NON_US_LOCATIONS: dict[str, str] = {
    'alberta': 'CA', 'ontario': 'CA', 'british columbia': 'CA', 'quebec': 'CA',
    'saskatchewan': 'CA', 'manitoba': 'CA', 'nova scotia': 'CA', 'canada': 'CA',
    'peace river': 'CA', 'edmonton': 'CA', 'calgary': 'CA', 'toronto': 'CA',
    'united kingdom': 'GB', 'england': 'GB', 'scotland': 'GB', 'wales': 'GB', 'london': 'GB',
    'norway': 'NO', 'oslo': 'NO', 'stavanger': 'NO',
    'australia': 'AU', 'melbourne': 'AU', 'sydney': 'AU', 'perth': 'AU',
    'germany': 'DE', 'berlin': 'DE', 'hamburg': 'DE',
    'netherlands': 'NL', 'rotterdam': 'NL', 'amsterdam': 'NL',
    'japan': 'JP', 'tokyo': 'JP',
    'south korea': 'KR', 'korea': 'KR',
    'saudi arabia': 'SA', 'neom': 'SA',
    'uae': 'AE', 'abu dhabi': 'AE', 'dubai': 'AE',
    'chile': 'CL', 'brazil': 'BR', 'india': 'IN', 'china': 'CN',
}

# Known major corporate names for E3 filter
CORPORATE_NAMES = {
    'shell', 'chevron', 'exxonmobil', 'exxon_mobil', 'bp', 'total', 'totalenergies',
    'equinor', 'enbridge', 'conocophillips', 'marathon', 'valero', 'phillips_66',
    'basf', 'dow', 'dupont', 'siemens', 'mitsubishi', 'samsung', 'hyundai',
    'linde', 'air_liquide', 'air_products',
}


def _rule_a1_overlapping_claims(db: sqlite3.Connection) -> list[dict]:
    """Flag projects that share the same state AND overlapping source documents."""
    issues = []
    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone():
        return issues

    pairs = db.execute("""
        SELECT p1.project_id AS pid1, p1.project_name AS name1,
               p2.project_id AS pid2, p2.project_name AS name2,
               p1.state,
               COUNT(DISTINCT c1.source_id) AS shared_sources
        FROM unified_projects p1
        JOIN unified_projects p2 ON p1.state = p2.state AND p1.project_id < p2.project_id
        JOIN project_claims pc1 ON pc1.project_id = p1.project_id
        JOIN project_claims pc2 ON pc2.project_id = p2.project_id
        JOIN claims c1 ON c1.claim_id = pc1.claim_id
        JOIN claims c2 ON c2.claim_id = pc2.claim_id
        WHERE c1.source_id = c2.source_id
          AND p1.quarantined = 0 AND p2.quarantined = 0
        GROUP BY p1.project_id, p2.project_id
        HAVING shared_sources > 0
    """).fetchall()

    for pair in pairs:
        # Quarantine the one with fewer resolved claims
        cnt1 = db.execute(
            "SELECT COUNT(*) FROM project_claims WHERE project_id=? AND resolution_method != 'unresolved'",
            (pair['pid1'],)).fetchone()[0]
        cnt2 = db.execute(
            "SELECT COUNT(*) FROM project_claims WHERE project_id=? AND resolution_method != 'unresolved'",
            (pair['pid2'],)).fetchone()[0]
        loser_id = pair['pid2'] if cnt1 >= cnt2 else pair['pid1']
        loser_name = pair['name2'] if cnt1 >= cnt2 else pair['name1']
        winner_name = pair['name1'] if cnt1 >= cnt2 else pair['name2']
        issues.append({
            'rule': 'A1', 'project_id': loser_id, 'project_name': loser_name,
            'detail': f'shares {pair["shared_sources"]} sources with {winner_name} in {pair["state"]}',
            'action': 'quarantine',
        })
    return issues


def _rule_a2_same_entity(db: sqlite3.Connection) -> list[dict]:
    """Flag projects in the same state that share an entity_id."""
    issues = []
    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone():
        return issues

    pairs = db.execute("""
        SELECT p1.project_id AS pid1, p1.project_name AS name1,
               p2.project_id AS pid2, p2.project_name AS name2,
               p1.state, pc1.entity_id
        FROM unified_projects p1
        JOIN unified_projects p2 ON p1.state = p2.state AND p1.project_id < p2.project_id
        JOIN project_claims pc1 ON pc1.project_id = p1.project_id
        JOIN project_claims pc2 ON pc2.project_id = p2.project_id
        WHERE pc1.entity_id = pc2.entity_id
          AND pc1.entity_id IS NOT NULL
          AND p1.quarantined = 0 AND p2.quarantined = 0
        GROUP BY p1.project_id, p2.project_id
    """).fetchall()

    seen = set()
    for pair in pairs:
        key = tuple(sorted([pair['pid1'], pair['pid2']]))
        if key in seen:
            continue
        seen.add(key)
        cnt1 = db.execute(
            "SELECT COUNT(*) FROM project_claims WHERE project_id=? AND resolution_method != 'unresolved'",
            (pair['pid1'],)).fetchone()[0]
        cnt2 = db.execute(
            "SELECT COUNT(*) FROM project_claims WHERE project_id=? AND resolution_method != 'unresolved'",
            (pair['pid2'],)).fetchone()[0]
        loser_id = pair['pid2'] if cnt1 >= cnt2 else pair['pid1']
        loser_name = pair['name2'] if cnt1 >= cnt2 else pair['name1']
        winner_name = pair['name1'] if cnt1 >= cnt2 else pair['name2']
        issues.append({
            'rule': 'A2', 'project_id': loser_id, 'project_name': loser_name,
            'detail': f'shares entity with {winner_name} in {pair["state"]}',
            'action': 'quarantine',
        })
    return issues


def _rule_a3_same_facility(db: sqlite3.Connection) -> list[dict]:
    """Flag projects that share the same EPA registry_id."""
    issues = []
    for tbl in ('project_claims', 'regulatory_evidence', 'epa_permits'):
        if not db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
        ).fetchone():
            return issues

    pairs = db.execute("""
        SELECT p1.project_id AS pid1, p1.project_name AS name1,
               p2.project_id AS pid2, p2.project_name AS name2,
               ep.registry_id
        FROM unified_projects p1
        JOIN project_claims pc1 ON pc1.project_id = p1.project_id
        JOIN claims c1 ON c1.claim_id = pc1.claim_id
        JOIN regulatory_evidence re1 ON re1.id = c1.source_id
        JOIN epa_permits ep ON ep.registry_id = REPLACE(re1.document_url, 'https://echo.epa.gov/detailed-facility-report?fid=', '')
        JOIN unified_projects p2 ON p2.project_id != p1.project_id
        JOIN project_claims pc2 ON pc2.project_id = p2.project_id
        JOIN claims c2 ON c2.claim_id = pc2.claim_id
        JOIN regulatory_evidence re2 ON re2.id = c2.source_id
        WHERE REPLACE(re2.document_url, 'https://echo.epa.gov/detailed-facility-report?fid=', '') = ep.registry_id
          AND p1.project_id < p2.project_id
          AND p1.quarantined = 0 AND p2.quarantined = 0
        GROUP BY p1.project_id, p2.project_id
    """).fetchall()

    seen = set()
    for pair in pairs:
        key = tuple(sorted([pair['pid1'], pair['pid2']]))
        if key in seen:
            continue
        seen.add(key)
        cnt1 = db.execute(
            "SELECT COUNT(*) FROM project_claims WHERE project_id=? AND resolution_method != 'unresolved'",
            (pair['pid1'],)).fetchone()[0]
        cnt2 = db.execute(
            "SELECT COUNT(*) FROM project_claims WHERE project_id=? AND resolution_method != 'unresolved'",
            (pair['pid2'],)).fetchone()[0]
        loser_id = pair['pid2'] if cnt1 >= cnt2 else pair['pid1']
        loser_name = pair['name2'] if cnt1 >= cnt2 else pair['name1']
        winner_name = pair['name1'] if cnt1 >= cnt2 else pair['name2']
        issues.append({
            'rule': 'A3', 'project_id': loser_id, 'project_name': loser_name,
            'detail': f'shares EPA facility {pair["registry_id"]} with {winner_name}',
            'action': 'quarantine',
        })
    return issues


def _rule_d1_date_order(db: sqlite3.Connection) -> list[dict]:
    """Flag projects where fid_date > construction_start or construction_start > cod_date."""
    issues = []
    rows = db.execute("""
        SELECT project_id, project_name, fid_date, construction_start, cod_date
        FROM unified_projects
        WHERE (fid_date IS NOT NULL OR construction_start IS NOT NULL OR cod_date IS NOT NULL)
          AND quarantined = 0
    """).fetchall()

    for r in rows:
        dates = {}
        for col in ('fid_date', 'construction_start', 'cod_date'):
            val = r[col]
            if val:
                try:
                    dates[col] = int(val[:4])  # extract year
                except (ValueError, IndexError):
                    continue

        violated = False
        if 'fid_date' in dates and 'construction_start' in dates:
            if dates['fid_date'] > dates['construction_start']:
                violated = True
        if 'construction_start' in dates and 'cod_date' in dates:
            if dates['construction_start'] > dates['cod_date']:
                violated = True
        if 'fid_date' in dates and 'cod_date' in dates:
            if dates['fid_date'] > dates['cod_date']:
                violated = True

        if violated:
            issues.append({
                'rule': 'D1', 'project_id': r['project_id'], 'project_name': r['project_name'],
                'detail': f'date order violated: fid={r["fid_date"]}, construction={r["construction_start"]}, cod={r["cod_date"]}',
                'action': 'null_dates',
            })
    return issues


def _rule_d2_date_range(db: sqlite3.Connection) -> list[dict]:
    """Flag dates outside 2010-2040 range."""
    issues = []
    rows = db.execute("""
        SELECT project_id, project_name, fid_date, construction_start, cod_date
        FROM unified_projects
        WHERE (fid_date IS NOT NULL OR construction_start IS NOT NULL OR cod_date IS NOT NULL)
          AND quarantined = 0
    """).fetchall()

    for r in rows:
        bad_cols = []
        for col in ('fid_date', 'construction_start', 'cod_date'):
            val = r[col]
            if val:
                try:
                    year = int(val[:4])
                    if year < 2010 or year > 2040:
                        bad_cols.append(f'{col}={val}')
                except (ValueError, IndexError):
                    bad_cols.append(f'{col}={val} (unparseable)')

        if bad_cols:
            issues.append({
                'rule': 'D2', 'project_id': r['project_id'], 'project_name': r['project_name'],
                'detail': f'out-of-range dates: {", ".join(bad_cols)}',
                'action': 'null_bad_dates',
                'bad_cols': [c.split('=')[0] for c in bad_cols],
            })
    return issues


def _rule_d6_city_state(db: sqlite3.Connection) -> list[dict]:
    """Flag city values that don't match their state."""
    issues = []
    rows = db.execute("""
        SELECT project_id, project_name, city, state
        FROM unified_projects
        WHERE city IS NOT NULL AND city != '' AND state IS NOT NULL AND state != ''
          AND quarantined = 0
    """).fetchall()

    for r in rows:
        state = r['state'].strip().upper()
        city_norm = r['city'].strip().lower()
        valid_cities = US_CITIES.get(state, set())
        if valid_cities and city_norm not in valid_cities:
            issues.append({
                'rule': 'D6', 'project_id': r['project_id'], 'project_name': r['project_name'],
                'detail': f'city "{r["city"]}" not found in {state} city list',
                'action': 'null_city',
            })
    return issues


def _rule_e1_specificity(db: sqlite3.Connection) -> list[dict]:
    """Flag projects with zero specificity: no city, state, capacity, technology, or permit data."""
    issues = []
    rows = db.execute("""
        SELECT project_id, project_name
        FROM unified_projects
        WHERE quarantined = 0
          AND (city IS NULL OR city = '')
          AND (state IS NULL OR state = '')
          AND (capacity_raw IS NULL OR capacity_raw = '')
          AND (technology IS NULL OR technology = '')
    """).fetchall()

    for r in rows:
        issues.append({
            'rule': 'E1', 'project_id': r['project_id'], 'project_name': r['project_name'],
            'detail': 'no city, state, capacity, or technology data',
            'action': 'quarantine',
        })
    return issues


def _rule_e2_claim_count(db: sqlite3.Connection) -> list[dict]:
    """Flag projects with very few resolved claims and minimal data."""
    issues = []
    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone():
        return issues

    rows = db.execute("""
        SELECT up.project_id, up.project_name,
               COUNT(CASE WHEN pc.resolution_method != 'unresolved' THEN 1 END) AS resolved_count,
               up.city, up.state, up.technology, up.capacity_raw
        FROM unified_projects up
        LEFT JOIN project_claims pc ON pc.project_id = up.project_id
        WHERE up.quarantined = 0
        GROUP BY up.project_id
        HAVING resolved_count < 2
    """).fetchall()

    for r in rows:
        # Count non-null fields
        filled = sum(1 for f in (r['city'], r['state'], r['technology'], r['capacity_raw']) if f)
        if filled <= 1:
            issues.append({
                'rule': 'E2', 'project_id': r['project_id'], 'project_name': r['project_name'],
                'detail': f'{r["resolved_count"]} resolved claims, {filled} data fields',
                'action': 'quarantine',
            })
    return issues


def _rule_e3_corporate_filter(db: sqlite3.Connection) -> list[dict]:
    """Flag corporate-name projects with no city and few claims."""
    issues = []
    has_pc = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone()

    rows = db.execute("""
        SELECT project_id, project_name, developer_key, city
        FROM unified_projects
        WHERE quarantined = 0 AND (city IS NULL OR city = '')
    """).fetchall()

    for r in rows:
        key = r['developer_key'] or ''
        if key not in CORPORATE_NAMES:
            continue
        claim_count = 0
        if has_pc:
            claim_count = db.execute(
                "SELECT COUNT(*) FROM project_claims WHERE project_id=?",
                (r['project_id'],)).fetchone()[0]
        if claim_count <= 3:
            issues.append({
                'rule': 'E3', 'project_id': r['project_id'], 'project_name': r['project_name'],
                'detail': f'corporate name "{key}" with no city and only {claim_count} claims',
                'action': 'quarantine',
            })
    return issues


def _match_non_us_project_location(text: str) -> str | None:
    """Check if text indicates a PROJECT is physically located outside the US.

    Only matches when a non-US location appears near project/facility context words
    like 'located in', 'facility in', 'plant in', 'mill in', 'project in'.
    This avoids false positives from international companies operating US-based projects.
    """
    if not text:
        return None
    text_lower = text.lower()
    for keyword, country_code in NON_US_LOCATIONS.items():
        kw_pattern = r'\b' + re.escape(keyword) + r'\b'
        # Match: "[facility context] ... [non-US location]" within ~50 chars
        for ctx in (
            r'(?:located|facility|plant|mill|project|site|refinery|terminal|hub)\s+(?:in|at|near)\s+.{0,50}?',
            r'(?:at the|at our|at its)\s+.{0,40}?',
        ):
            if re.search(ctx + kw_pattern, text_lower):
                return country_code
    return None


def _rule_e4_country(db: sqlite3.Connection) -> list[dict]:
    """Detect non-US projects by scanning technology/claim text for foreign project locations.

    IMPORTANT: Only flags projects whose PHYSICAL LOCATION is outside the US.
    Does NOT flag US projects operated by international companies (e.g. Shell, Messer, BP).
    If a project already has a valid US state, it is always treated as a US project.
    """
    issues = []
    rows = db.execute("""
        SELECT project_id, project_name, technology, state
        FROM unified_projects
        WHERE quarantined = 0 AND (country IS NULL OR country = 'US')
    """).fetchall()

    for r in rows:
        # If the project has a valid US state → it's a US project, skip
        if r['state'] and r['state'].strip().upper() in ALL_US_STATES:
            continue

        text_to_scan = ' '.join(filter(None, [r['technology']]))
        detected_country = _match_non_us_project_location(text_to_scan)

        if not detected_country:
            # Check claim text — require 2+ claims with facility-context non-US location
            try:
                claims = db.execute("""
                    SELECT c.claim_text
                    FROM project_claims pc
                    JOIN claims c ON c.claim_id = pc.claim_id
                    WHERE pc.project_id = ?
                    LIMIT 20
                """, (r['project_id'],)).fetchall()
                country_hits: dict[str, int] = {}
                for claim in claims:
                    cc = _match_non_us_project_location(claim['claim_text'] or '')
                    if cc:
                        country_hits[cc] = country_hits.get(cc, 0) + 1
                # Only flag if 2+ claims reference the same non-US project location
                for cc, count in country_hits.items():
                    if count >= 2:
                        detected_country = cc
                        break
            except Exception:
                pass

        if detected_country:
            issues.append({
                'rule': 'E4', 'project_id': r['project_id'], 'project_name': r['project_name'],
                'detail': f'non-US project location detected → country={detected_country}',
                'action': 'set_country',
                'country': detected_country,
            })
    return issues


def _rule_e5_single_source(db: sqlite3.Connection) -> list[dict]:
    """Quarantine projects backed by only one source system and no crossrefs.

    Single-source projects lack corroboration. They are recoverable: when new
    data adds cross-source evidence, crossref.py Phase 7 un-quarantines them.
    """
    issues = []
    has_pc = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone()
    if not has_pc:
        return issues

    rows = db.execute("""
        SELECT up.project_id, up.project_name,
               COALESCE(src.sys_count, 0) AS sys_count,
               COALESCE(up.crossref_source_count, 0) AS xref_count,
               src.sys_name
        FROM unified_projects up
        LEFT JOIN (
            SELECT pc.project_id,
                   COUNT(DISTINCT cd.source_system) AS sys_count,
                   GROUP_CONCAT(DISTINCT cd.source_system) AS sys_name
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            JOIN cleaned_documents cd ON c.source_id = cd.source_id
            GROUP BY pc.project_id
        ) src ON src.project_id = up.project_id
        WHERE up.quarantined = 0
          AND COALESCE(src.sys_count, 0) <= 1
          AND COALESCE(up.crossref_source_count, 0) = 0
    """).fetchall()

    for r in rows:
        sys_name = r['sys_name'] or 'none'
        issues.append({
            'rule': 'E5', 'project_id': r['project_id'],
            'project_name': r['project_name'],
            'detail': f'single source system: {sys_name}',
            'action': 'quarantine',
        })
    return issues


def phase_validate(db: sqlite3.Connection) -> None:
    """Run all validation rules on unified_projects. Quarantine or fix bad data."""
    print('\n── Phase V: Validate unified_projects ───────────────────────────')

    all_issues: list[dict] = []

    # Deduplication rules
    print('  Running dedup rules (A1, A2, A3)...')
    all_issues.extend(_rule_a1_overlapping_claims(db))
    all_issues.extend(_rule_a2_same_entity(db))
    all_issues.extend(_rule_a3_same_facility(db))

    # Data sanity rules
    print('  Running data sanity rules (D1, D2, D6)...')
    all_issues.extend(_rule_d1_date_order(db))
    all_issues.extend(_rule_d2_date_range(db))
    all_issues.extend(_rule_d6_city_state(db))

    # Evidence rules
    print('  Running evidence rules (E1, E2, E3, E4, E5)...')
    all_issues.extend(_rule_e1_specificity(db))
    all_issues.extend(_rule_e2_claim_count(db))
    all_issues.extend(_rule_e3_corporate_filter(db))
    all_issues.extend(_rule_e4_country(db))
    all_issues.extend(_rule_e5_single_source(db))

    if not all_issues:
        print('  No validation issues found.')
        return

    # Deduplicate issues by project_id + rule (keep first)
    seen_keys = set()
    unique_issues = []
    for issue in all_issues:
        key = (issue['project_id'], issue['rule'])
        if key not in seen_keys:
            seen_keys.add(key)
            unique_issues.append(issue)
    all_issues = unique_issues

    # Report
    print(f'\n  Found {len(all_issues)} validation issues:')
    for issue in all_issues:
        marker = 'DRY' if DRY_RUN else 'FIX'
        print(f'    [{marker}] {issue["rule"]:>3} | {issue["project_name"]:<45} | {issue["detail"]}')

    if DRY_RUN:
        print(f'\n  Dry run — no changes applied. Re-run without --dry-run to fix.')
        return

    # Apply fixes
    now = now_iso()
    quarantined_count = 0
    date_fixes = 0
    city_fixes = 0
    country_fixes = 0

    for issue in all_issues:
        action = issue['action']
        pid = issue['project_id']

        if action == 'quarantine':
            db.execute("""
                UPDATE unified_projects
                SET quarantined = 1, quarantine_reason = ?, updated_at = ?
                WHERE project_id = ? AND quarantined = 0
            """, (f'{issue["rule"]}: {issue["detail"]}', now, pid))
            quarantined_count += 1

        elif action == 'null_dates':
            db.execute("""
                UPDATE unified_projects
                SET fid_date = NULL, construction_start = NULL, cod_date = NULL,
                    quarantine_reason = COALESCE(quarantine_reason || '; ', '') || ?,
                    updated_at = ?
                WHERE project_id = ?
            """, (f'{issue["rule"]}: {issue["detail"]}', now, pid))
            date_fixes += 1

        elif action == 'null_bad_dates':
            for col in issue.get('bad_cols', []):
                if col in ('fid_date', 'construction_start', 'cod_date'):
                    db.execute(
                        f"UPDATE unified_projects SET {col} = NULL, updated_at = ? WHERE project_id = ?",
                        (now, pid))
            date_fixes += 1

        elif action == 'null_city':
            db.execute("""
                UPDATE unified_projects SET city = NULL, updated_at = ?
                WHERE project_id = ?
            """, (now, pid))
            city_fixes += 1

        elif action == 'set_country':
            db.execute("""
                UPDATE unified_projects SET country = ?, updated_at = ?
                WHERE project_id = ?
            """, (issue.get('country', 'US'), now, pid))
            country_fixes += 1

    db.commit()
    print(f'\n  Applied: quarantined={quarantined_count}, date_fixes={date_fixes}, '
          f'city_fixes={city_fixes}, country_fixes={country_fixes}')


# ── Data Corrections ──────────────────────────────────────────────────────────

# Known data errors discovered during provenance audits.
# These are fixes for values set outside the pipeline (manual SQL or earlier
# script versions) that are either wrong or came from incorrect sources.
#
# DELEK US HOLDINGS: doe_status has state=TN (corporate HQ in Brentwood, TN),
#   but the actual project is at Big Spring, TX refinery per claim text.
# OCI CLEAN AMMONIA: city=Beaumont was set manually but claims all say
#   "NEDERLAND, TX" (the actual plant location). Beaumont is the nearby city
#   but the facility is in Nederland.
# CALPINE CALIFORNIA CCUS: doe_status has state=TX (USAspending lists recipient
#   state = Calpine's Houston HQ), but this is a California project.
KNOWN_CORRECTIONS = [
    {'project_name': 'DELEK US HOLDINGS, INC', 'field': 'state', 'wrong': 'TN', 'correct': 'TX',
     'reason': 'doe_status has TN (corporate HQ) but project is at Big Spring, TX refinery'},
    {'project_name': 'OCI CLEAN AMMONIA PRODUCTION FACILITY', 'field': 'city', 'wrong': 'Nederland', 'correct': 'Beaumont',
     'reason': 'Industry name is Beaumont (Woodside "Beaumont New Ammonia"); EPA address is Nederland, TX 77627',
     'epa_facility_address': 'Nederland, TX 77627'},
]

# Multi-facility developers hack REMOVED — now that facilities are separate
# sub-projects with their own city/state, each gets its own location.
# Kept as empty set for backward compat with any downstream references.
MULTI_FACILITY_DEVELOPERS: set[str] = set()

# Projects whose state was manually verified but can't be traced through the
# automated pipeline (USAspending awards don't include place_of_performance_state).
# Audit accepts these as valid provenance.
MANUALLY_VERIFIED_FIELDS: dict[str, dict[str, str]] = {
    'CALPINE TEXAS CCUS':                   {'state': 'TX'},   # Baytown Energy Center, TX
    'HYVELOCITY':                            {'state': 'TX'},   # DOE H2 Hub, Houston, TX
    'NextDecade Corp':                       {'state': 'TX'},   # Rio Grande LNG, Brownsville, TX
    'ORSTED STAR P2X':                       {'state': 'TX'},   # E-methanol project, TX Gulf Coast
    'HEARTLAND HYDROGEN HUB':               {'state': 'ND'},   # DOE H2 Hub, North Dakota
    'MID-ATLANTIC CLEAN HYDROGEN HUB':      {'state': 'PA'},   # DOE H2 Hub, PA/DE/NJ
    'MIDWEST ALLIANCE FOR CLEAN HYDROGEN':  {'state': 'IL'},   # MACHH2, Illinois
    'OCI CLEAN AMMONIA PRODUCTION FACILITY': {'city': 'Beaumont'},  # Industry name; EPA address is Nederland, TX
}

DOE_STATUS_CORRECTIONS = [
    {'company_name': 'CALPINE CALIFORNIA CCUS', 'field': 'state', 'wrong': 'TX', 'correct': None,
     'reason': 'USAspending lists TX (Calpine HQ), but this is a CA project — NULL out to avoid contamination'},
]


def _apply_known_corrections(db: sqlite3.Connection) -> None:
    """Apply known data corrections before audit. Idempotent."""
    now = now_iso()
    applied = 0
    for fix in KNOWN_CORRECTIONS:
        row = db.execute(
            f"SELECT project_id, {fix['field']} FROM unified_projects WHERE project_name = ?",
            (fix['project_name'],)).fetchone()
        if row and row[fix['field']] == fix['wrong']:
            if not DRY_RUN:
                db.execute(
                    f"UPDATE unified_projects SET {fix['field']} = ?, updated_at = ? WHERE project_id = ?",
                    (fix['correct'], now, row['project_id']))
                # Store EPA facility address if provided
                if fix.get('epa_facility_address'):
                    db.execute(
                        "UPDATE unified_projects SET epa_facility_address = ? WHERE project_id = ?",
                        (fix['epa_facility_address'], row['project_id']))
            applied += 1
            print(f'  [{"DRY" if DRY_RUN else "FIX"}] {fix["project_name"]}: '
                  f'{fix["field"]} {fix["wrong"]!r} → {fix["correct"]!r} ({fix["reason"]})')

    for fix in DOE_STATUS_CORRECTIONS:
        row = db.execute(
            f"SELECT source_id, {fix['field']} FROM doe_status WHERE company_name = ?",
            (fix['company_name'],)).fetchone()
        if row and row[fix['field']] == fix['wrong']:
            if not DRY_RUN:
                db.execute(
                    f"UPDATE doe_status SET {fix['field']} = ? WHERE company_name = ?",
                    (fix['correct'], fix['company_name']))
            applied += 1
            print(f'  [{"DRY" if DRY_RUN else "FIX"}] doe_status[{fix["company_name"]}]: '
                  f'{fix["field"]} {fix["wrong"]!r} → {fix["correct"]!r} ({fix["reason"]})')

    if applied and not DRY_RUN:
        # Also update region for state corrections
        for fix in KNOWN_CORRECTIONS:
            if fix['field'] == 'state':
                region = state_to_region(fix['correct'])
                db.execute(
                    "UPDATE unified_projects SET region = ? WHERE project_name = ?",
                    (region, fix['project_name']))
        db.commit()
    if applied:
        print(f'  Applied {applied} known corrections.')
    else:
        print('  No pending corrections.')


# ── Provenance Audit ──────────────────────────────────────────────────────────

def phase_audit(db: sqlite3.Connection) -> None:
    """Trace every populated field in unified_projects back to its source claim.

    For each project, for each non-empty field, checks whether the value
    can be found in at least one resolved claim's text. Flags mismatches
    where the field value has NO supporting evidence in the claim trail.

    Read-only — does not modify any data. Just prints a report.
    """
    print('\n══ PROVENANCE AUDIT ══════════════════════════════════════════════')

    # Apply known data corrections first (idempotent)
    print('  Applying known corrections...')
    _apply_known_corrections(db)
    print()

    print('  Tracing every field value back to its source claim...\n')

    has_pc = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone()
    if not has_pc:
        print('  ERROR: project_claims table not found. Run connect.py first.')
        return

    projects = db.execute("""
        SELECT project_id, project_name, developer_name, city, state, technology,
               technology_label, capacity_raw, capacity_mtpa_h2, stage,
               epc_contractor, fid_date, cod_date, construction_start
        FROM unified_projects
        ORDER BY project_name
    """).fetchall()

    # Fields to audit and how to check them against claim text
    AUDITABLE_FIELDS = [
        'city', 'state', 'technology', 'capacity_raw',
        'epc_contractor', 'fid_date', 'cod_date', 'construction_start',
    ]

    total_fields_checked = 0
    total_issues = 0
    all_issues: list[dict] = []

    for proj in projects:
        pid = proj['project_id']
        pname = proj['project_name']

        # Get all resolved claims for this project
        claims = db.execute("""
            SELECT c.claim_id, c.claim_type, c.claim_text, c.confidence,
                   c.document_type, c.document_url, c.source_id
            FROM project_claims pc
            JOIN claims c ON c.claim_id = pc.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.superseded_by IS NULL
            ORDER BY c.confidence DESC
        """, (pid,)).fetchall()

        # Also get EPA sources linked to this project
        epa_texts = []
        for claim in claims:
            if claim['source_id']:
                re_row = db.execute("""
                    SELECT raw_text_excerpt, operator_name, llc_entity,
                           derived_stage, derived_technology
                    FROM regulatory_evidence WHERE id = ?
                """, (claim['source_id'],)).fetchone()
                if re_row:
                    epa_texts.append(re_row)

        # Also check epa_permits if linked
        epa_permit_cities = set()
        epa_permit_states = set()
        for claim in claims:
            if claim['source_id']:
                permits = db.execute("""
                    SELECT ep.facility_city, ep.facility_state
                    FROM regulatory_evidence re
                    JOIN epa_permits ep ON ep.registry_id =
                        REPLACE(re.document_url, 'https://echo.epa.gov/detailed-facility-report?fid=', '')
                    WHERE re.id = ?
                """, (claim['source_id'],)).fetchall()
                for p in permits:
                    if p['facility_city']:
                        epa_permit_cities.add(p['facility_city'].strip().lower())
                    if p['facility_state']:
                        epa_permit_states.add(p['facility_state'].strip().upper())

        # Build combined searchable text from all sources
        all_claim_text = ' '.join(c['claim_text'] or '' for c in claims).lower()
        all_epa_text = ' '.join(
            ' '.join(filter(None, [r['raw_text_excerpt'], r['operator_name'],
                                    r['llc_entity'], r['derived_technology']]))
            for r in epa_texts
        ).lower()
        all_text = all_claim_text + ' ' + all_epa_text

        # Check doe_status table for state provenance
        # Look up by developer_name AND by raw company names from claims
        doe_state = None
        try:
            doe_row = db.execute(
                "SELECT state FROM doe_status WHERE company_name = ? AND state IS NOT NULL",
                (proj['developer_name'],)).fetchone()
            if doe_row:
                doe_state = doe_row[0]
            else:
                # Fallback: check by raw company names in claims
                raw_names = db.execute("""
                    SELECT DISTINCT c.company_name
                    FROM project_claims pc JOIN claims c ON c.claim_id = pc.claim_id
                    WHERE pc.project_id = ?
                """, (pid,)).fetchall()
                for rn in raw_names:
                    if rn['company_name']:
                        dr = db.execute(
                            "SELECT state FROM doe_status WHERE company_name = ? AND state IS NOT NULL",
                            (rn['company_name'],)).fetchone()
                        if dr:
                            doe_state = dr[0]
                            break
        except sqlite3.OperationalError:
            pass

        # Check project_signal_accumulator for location signals
        signal_texts = []
        try:
            sigs = db.execute("""
                SELECT asserted_value FROM project_signal_accumulator
                WHERE project_id = ? AND signal_type = 'location'
            """, (pid,)).fetchall()
            signal_texts = [(s['asserted_value'] or '').lower() for s in sigs]
        except sqlite3.OperationalError:
            pass

        # Check each field
        for field in AUDITABLE_FIELDS:
            value = proj[field]
            if not value or str(value).strip() == '':
                continue

            total_fields_checked += 1
            val_str = str(value).strip()
            val_lower = val_str.lower()
            found = False
            source_hint = ''

            if field == 'city':
                # City could come from claims, EPA permits, or signal accumulator
                if val_lower in all_text:
                    found = True
                    source_hint = 'claim text'
                elif val_lower in epa_permit_cities or any(val_lower in c for c in epa_permit_cities):
                    found = True
                    source_hint = 'EPA permit'
                elif any(val_lower in sig for sig in signal_texts):
                    found = True
                    source_hint = 'signal accumulator'
                else:
                    # Check if city appears in any EPA permit city (which includes county info)
                    for epa_city in epa_permit_cities:
                        if val_lower in epa_city or epa_city.startswith(val_lower):
                            found = True
                            source_hint = 'EPA permit (partial)'
                            break

            elif field == 'state':
                # State could come from doe_status, claims, EPA, or signals
                if doe_state and doe_state.strip().upper() == val_str.upper():
                    found = True
                    source_hint = 'doe_status table'
                elif val_str.upper() in all_text.upper():
                    found = True
                    source_hint = 'claim text'
                elif val_str.upper() in epa_permit_states:
                    found = True
                    source_hint = 'EPA permit'
                elif any(val_str.lower() in sig for sig in signal_texts):
                    found = True
                    source_hint = 'signal accumulator'

            elif field == 'technology':
                # Technology is directly copied from claim text (first 200 chars)
                # Check if it appears verbatim or substantially in any claim
                if val_lower[:50] in all_text:
                    found = True
                    source_hint = 'claim text (verbatim)'
                else:
                    # Check first 30 chars as fuzzy match
                    if val_lower[:30] in all_text:
                        found = True
                        source_hint = 'claim text (partial)'
                    # Check EPA derived_technology
                    for epa in epa_texts:
                        if epa['derived_technology'] and epa['derived_technology'].lower() in val_lower:
                            found = True
                            source_hint = 'EPA derived_technology'
                            break

            elif field == 'capacity_raw':
                # Capacity_raw is directly from claim text
                if val_lower[:40] in all_text:
                    found = True
                    source_hint = 'claim text (verbatim)'
                else:
                    # Try first few significant words
                    words = val_lower.split()[:5]
                    if len(words) >= 3 and ' '.join(words[:3]) in all_text:
                        found = True
                        source_hint = 'claim text (partial)'

            elif field == 'epc_contractor':
                if val_lower in all_text:
                    found = True
                    source_hint = 'claim text'

            elif field in ('fid_date', 'cod_date', 'construction_start'):
                # Dates: check if the year appears in timeline-related claims
                year = val_str[:4]
                if year in all_text:
                    found = True
                    source_hint = 'claim text (year found)'
                else:
                    # Check full date string
                    if val_str in all_text:
                        found = True
                        source_hint = 'claim text (exact date)'

            # Check manually verified fields as last resort
            if not found:
                manual = MANUALLY_VERIFIED_FIELDS.get(pname, {})
                if manual.get(field) and manual[field].upper() == val_str.upper():
                    found = True
                    source_hint = 'manually verified'

            if not found:
                total_issues += 1
                # Find the best candidate claim for context
                best_claim = None
                for c in claims:
                    ct = (c['claim_type'] or '').lower()
                    if field == 'city' and 'location' in ct:
                        best_claim = c
                        break
                    elif field == 'state' and 'location' in ct:
                        best_claim = c
                        break
                    elif field == 'technology' and 'technology' in ct:
                        best_claim = c
                        break
                    elif field == 'capacity_raw' and 'capacity' in ct:
                        best_claim = c
                        break
                    elif field == 'epc_contractor' and 'epc' in ct:
                        best_claim = c
                        break
                    elif field in ('fid_date', 'cod_date', 'construction_start') and 'timeline' in ct:
                        best_claim = c
                        break

                nearest_claim_text = ''
                if best_claim:
                    nearest_claim_text = (best_claim['claim_text'] or '')[:120]
                elif claims:
                    nearest_claim_text = (claims[0]['claim_text'] or '')[:120]

                issue = {
                    'project_name': pname,
                    'field': field,
                    'value': val_str[:80],
                    'claim_count': len(claims),
                    'nearest_claim': nearest_claim_text,
                }
                all_issues.append(issue)

    # Also audit: projects with NO resolved claims at all
    orphan_projects = db.execute("""
        SELECT up.project_id, up.project_name, up.stage,
               COUNT(pc.claim_id) AS total_claims,
               SUM(CASE WHEN pc.resolution_method != 'unresolved' THEN 1 ELSE 0 END) AS resolved
        FROM unified_projects up
        LEFT JOIN project_claims pc ON pc.project_id = up.project_id
        GROUP BY up.project_id
        HAVING resolved = 0 OR total_claims = 0
        ORDER BY up.project_name
    """).fetchall()

    # Print report
    print(f'  Projects scanned:    {len(projects)}')
    print(f'  Fields checked:      {total_fields_checked}')
    print(f'  Unsupported values:  {total_issues}')
    print(f'  Orphan projects:     {len(orphan_projects)} (no resolved claims)')

    if all_issues:
        print(f'\n── Unsupported Field Values ({total_issues}) ─────────────────────────')
        print(f'  {"PROJECT":<40} {"FIELD":<20} {"VALUE":<40} {"CLAIMS":<6} NEAREST CLAIM')
        print(f'  {"─"*40} {"─"*20} {"─"*40} {"─"*6} {"─"*50}')
        for issue in all_issues:
            print(f'  {issue["project_name"]:<40} {issue["field"]:<20} '
                  f'{issue["value"]:<40} {issue["claim_count"]:<6} '
                  f'{issue["nearest_claim"][:50]}')

    if orphan_projects:
        print(f'\n── Orphan Projects ({len(orphan_projects)}) ──────────────────────────────')
        print(f'  {"PROJECT":<45} {"STAGE":<15} {"TOTAL":<8} {"RESOLVED":<8}')
        print(f'  {"─"*45} {"─"*15} {"─"*8} {"─"*8}')
        for op in orphan_projects:
            print(f'  {op["project_name"]:<45} {op["stage"] or "?":<15} '
                  f'{op["total_claims"]:<8} {op["resolved"]:<8}')

    # Summary by issue type
    if all_issues:
        print(f'\n── Summary by Field ────────────────────────────────────────────')
        from collections import Counter
        field_counts = Counter(i['field'] for i in all_issues)
        for field, count in field_counts.most_common():
            print(f'  {field:<25} {count} unsupported values')

    print(f'\n══ AUDIT COMPLETE ════════════════════════════════════════════════')


# ── Phase 5: Apply accumulator signals + extract from claims + EPA permits ────

def phase_apply_signals(db: sqlite3.Connection) -> None:
    """Consume project_signal_accumulator into unified_projects.

    For each unapplied signal:
      - location → city (parsed from text)
      - stage → stage (if higher priority than current)
      - capacity → capacity_raw, capacity_mtpa_h2
    Marks signals as applied_to_db=1 after processing.
    """
    print('\n── Phase 5a: Apply accumulator signals ──────────────────────────')

    signals = db.execute("""
        SELECT psa.id, psa.project_id, psa.signal_type, psa.asserted_value,
               psa.accumulated_conf,
               up.city, up.stage, up.capacity_raw
        FROM project_signal_accumulator psa
        JOIN unified_projects up ON psa.project_id = up.project_id
        WHERE psa.applied_to_db = 0
        ORDER BY psa.accumulated_conf DESC
    """).fetchall()

    if not signals:
        print('  No unapplied signals.')
    else:
        print(f'  {len(signals)} unapplied signals to process')

    applied = {'city': 0, 'stage': 0, 'capacity': 0, 'technology': 0}
    now = now_iso()

    for sig in signals:
        pid = sig['project_id']
        stype = sig['signal_type']
        val = sig['asserted_value'] or ''

        if stype == 'location':
            city = _parse_city_from_text(val)
            if city and not sig['city']:
                if not DRY_RUN:
                    db.execute(
                        "UPDATE unified_projects SET city=?, updated_at=? WHERE project_id=? AND (city IS NULL OR city='')",
                        (city, now, pid)
                    )
                applied['city'] += 1
                print(f'    city: {city:<20} → {pid}')

        elif stype == 'stage':
            new_stage = val.strip().lower()
            cur_stage = (sig['stage'] or 'unknown').strip().lower()
            if not DRY_RUN:
                updated = apply_stage_update(
                    db, pid, new_stage, new_date=None,
                    reasoning=f'Signal accumulator: {val}',
                    confidence=sig['accumulated_conf']
                )
            else:
                updated = should_update_stage(cur_stage, None, new_stage, None)
            if updated:
                applied['stage'] += 1
                print(f'    stage: {cur_stage} → {new_stage} for {pid}')

        elif stype == 'capacity':
            cap_raw, cap_mtpa = _parse_capacity(val)
            if cap_raw and not sig['capacity_raw']:
                if not DRY_RUN:
                    db.execute("""
                        UPDATE unified_projects
                        SET capacity_raw=COALESCE(NULLIF(capacity_raw,''), ?),
                            capacity_mtpa_h2=COALESCE(capacity_mtpa_h2, ?),
                            updated_at=?
                        WHERE project_id=?
                    """, (cap_raw, cap_mtpa, now, pid))
                applied['capacity'] += 1

        elif stype == 'technology':
            if val and not DRY_RUN:
                db.execute("""
                    UPDATE unified_projects
                    SET technology=COALESCE(NULLIF(technology,''), ?),
                        updated_at=?
                    WHERE project_id=?
                """, (val[:200], now, pid))
            applied['technology'] += 1

        # Mark signal as applied
        if not DRY_RUN:
            db.execute("UPDATE project_signal_accumulator SET applied_to_db=1 WHERE id=?", (sig['id'],))

    if not DRY_RUN:
        db.commit()

    print(f'  Applied: city={applied["city"]}, stage={applied["stage"]}, capacity={applied["capacity"]}, technology={applied["technology"]}')

    # ── DOE termination → stage=cancelled ──────────────────────────────────
    # doe_status.doe_award_status='terminated' is a hard signal that overrides
    # any current stage. The LLM-inferred stage doesn't see termination data.
    #
    # SCOPED: DOE terminations only cancel doe_award and company_portfolio
    # projects — never facility projects (operational plants are independent
    # of DOE award status).
    #
    # Primary match: doe_award_url (most reliable)
    # Fallback: 4-tier matching scoped to project_type IN ('doe_award', 'company_portfolio')

    # Collect all terminated awards
    term_awards = db.execute("""
        SELECT company_name, doe_termination_date, source_id, document_url
        FROM doe_status
        WHERE doe_award_status = 'terminated'
    """).fetchall()

    # Build {project_id: (term_date, doe_company, match_tier)} — latest term_date wins per project
    projects_to_cancel: dict[str, tuple] = {}

    for award in term_awards:
        term_date = award['doe_termination_date']
        doe_company = award['company_name']

        # PRIMARY: find specific DOE award project by URL
        if award['document_url']:
            doe_proj = db.execute("""
                SELECT project_id, project_name, stage
                FROM unified_projects
                WHERE doe_award_url = ? AND project_type = 'doe_award'
                  AND stage != 'cancelled' AND quarantined = 0
            """, (award['document_url'],)).fetchone()
            if doe_proj:
                pid = doe_proj['project_id']
                existing = projects_to_cancel.get(pid)
                if not existing or term_date > existing[0]:
                    projects_to_cancel[pid] = (term_date, doe_company, 'doe_award_url',
                                               doe_proj['project_name'], doe_proj['stage'])
                continue

        # FALLBACK: 4-tier matching, scoped to doe_award and company_portfolio
        scope_filter = "AND up.project_type IN ('doe_award', 'company_portfolio')"

        # Tier 1: source_id → claims → project_claims → unified_projects
        tier1 = db.execute(f"""
            SELECT DISTINCT up.project_id, up.project_name, up.stage
            FROM regulatory_evidence re
            JOIN claims c ON c.source_id = re.id
            JOIN project_claims pc ON pc.claim_id = c.claim_id
            JOIN unified_projects up ON up.project_id = pc.project_id
            WHERE re.id = ?
              AND up.stage != 'cancelled'
              AND up.quarantined = 0
              {scope_filter}
        """, (award['source_id'],)).fetchall()

        for row in tier1:
            pid = row['project_id']
            existing = projects_to_cancel.get(pid)
            if not existing or term_date > existing[0]:
                projects_to_cancel[pid] = (term_date, doe_company, 'source_id',
                                           row['project_name'], row['stage'])

        if tier1:
            continue  # source_id matched — skip less reliable tiers

        # Tier 2: document_url chain
        tier2 = db.execute(f"""
            SELECT DISTINCT up.project_id, up.project_name, up.stage
            FROM regulatory_evidence re
            JOIN claims c ON c.source_id = re.id
            JOIN project_claims pc ON pc.claim_id = c.claim_id
            JOIN unified_projects up ON up.project_id = pc.project_id
            WHERE re.document_url = ?
              AND up.stage != 'cancelled'
              AND up.quarantined = 0
              {scope_filter}
        """, (award['document_url'],)).fetchall()

        for row in tier2:
            pid = row['project_id']
            existing = projects_to_cancel.get(pid)
            if not existing or term_date > existing[0]:
                projects_to_cancel[pid] = (term_date, doe_company, 'document_url',
                                           row['project_name'], row['stage'])

        if tier2:
            continue

        # Tier 3: exact name match (scoped)
        tier3 = db.execute(f"""
            SELECT project_id, project_name, stage
            FROM unified_projects
            WHERE UPPER(developer_name) = UPPER(?)
              AND stage != 'cancelled'
              AND quarantined = 0
              AND project_type IN ('doe_award', 'company_portfolio')
        """, (doe_company,)).fetchall()

        for row in tier3:
            pid = row['project_id']
            existing = projects_to_cancel.get(pid)
            if not existing or term_date > existing[0]:
                projects_to_cancel[pid] = (term_date, doe_company, 'exact_name',
                                           row['project_name'], row['stage'])

        if tier3:
            continue

        # Tier 4: prefix name match (scoped)
        doe_upper = doe_company.upper()
        tier4 = db.execute(f"""
            SELECT project_id, developer_name, project_name, stage
            FROM unified_projects
            WHERE stage != 'cancelled'
              AND quarantined = 0
              AND project_type IN ('doe_award', 'company_portfolio')
              AND (UPPER(developer_name) LIKE ? || '%'
                   OR ? LIKE UPPER(developer_name) || '%')
        """, (doe_upper, doe_upper)).fetchall()

        for row in tier4:
            pid = row['project_id']
            existing = projects_to_cancel.get(pid)
            if not existing or term_date > existing[0]:
                projects_to_cancel[pid] = (term_date, doe_company, 'prefix_name',
                                           row['project_name'], row['stage'])

    if projects_to_cancel:
        for pid, (term_date, doe_co, tier, proj_name, old_stage) in sorted(
                projects_to_cancel.items(), key=lambda x: x[1][1]):
            reasoning = f'DOE award terminated on {term_date} (matched via {tier})'
            if not DRY_RUN:
                apply_stage_update(
                    db, pid, 'cancelled', new_date=term_date,
                    reasoning=reasoning, confidence=1.0, force=True
                )
            print(f'    stage: {old_stage} → cancelled  {proj_name[:40]:<40}  '
                  f'(DOE terminated {term_date}, match={tier})')
        if not DRY_RUN:
            db.commit()
        print(f'  DOE termination → cancelled: {len(projects_to_cancel)} projects')


def phase_enrich_from_extracted(db: sqlite3.Connection) -> None:
    """Phase 5b: Fill project fields from LLM-extracted structured data.

    Reads the extracted_* columns that the LLM populated during step3.
    Runs BEFORE the regex-based phase_enrich_from_claims so LLM-tagged
    structured data takes priority. Only fills NULL/empty fields.
    """
    _CAPACITY_SKIP_TYPES = {
        'injection_capacity', 'capture_capacity', 'well_count',
        'injection_wells', 'number_of_wells',
    }
    rows = db.execute("""
        SELECT pc.project_id, c.extracted_city, c.extracted_state,
               c.extracted_capacity, c.extracted_technology, c.extracted_stage,
               c.extracted_date, c.extracted_date_type,
               c.extracted_permit_id, c.extracted_parent_company,
               c.confidence, c.corroboration_count, c.document_date,
               COALESCE(pc.claim_type, c.claim_type) AS claim_type
        FROM project_claims pc
        JOIN claims c ON c.claim_id = pc.claim_id
        WHERE pc.resolution_method != 'unresolved'
          AND (c.extracted_city IS NOT NULL
               OR c.extracted_state IS NOT NULL
               OR c.extracted_capacity IS NOT NULL
               OR c.extracted_technology IS NOT NULL
               OR c.extracted_stage IS NOT NULL
               OR c.extracted_permit_id IS NOT NULL
               OR c.extracted_parent_company IS NOT NULL
               OR c.extracted_date IS NOT NULL)
        ORDER BY c.corroboration_count DESC, c.confidence DESC
    """).fetchall()

    # Build pid → developer_name lookup for circular parent_company check
    _dev_names = {}
    _proj_info = {}  # pid → (stage, project_type) for date guards
    for r in db.execute("SELECT project_id, developer_name, stage, project_type FROM unified_projects"):
        _dev_names[r['project_id']] = (r['developer_name'] or '').strip().upper()
        _proj_info[r['project_id']] = (r['stage'], r['project_type'])

    updated = stage_updated = 0
    for row in rows:
        pid = row['project_id']
        updates = []
        params = []
        for db_col, ext_col in [
            ('city', 'extracted_city'),
            ('state', 'extracted_state'),
            ('capacity_raw', 'extracted_capacity'),
            ('technology', 'extracted_technology'),
            ('permit_id', 'extracted_permit_id'),
            # parent_company handled separately below (temporal ordering + normalization)
        ]:
            val = row[ext_col]
            if val:
                # Skip permit types misidentified as permit IDs
                if db_col == 'permit_id' and val.strip().upper() in (
                    'CLASS VI', 'CLASS II', 'UIC CLASS VI', 'UIC CLASS II',
                ):
                    continue
                # Skip well-count/injection capacity from being written as capacity_raw
                if db_col == 'capacity_raw' and row['claim_type'] in _CAPACITY_SKIP_TYPES:
                    continue
                # Validate capacity has number+unit pattern
                if db_col == 'capacity_raw' and not re.search(
                        r'\d.*(?:MW|GW|MTPA|mt|ton|bbl|mcf|kg|million)', val, re.I):
                    continue
                updates.append(f"{db_col} = COALESCE(NULLIF({db_col},''), ?)")
                params.append(val)

        # Dates by type — guard against future dates on operational portfolios
        if row['extracted_date'] and row['extracted_date_type']:
            date_col_map = {
                'fid': 'fid_date', 'cod': 'cod_date',
                'construction_start': 'construction_start',
            }
            col = date_col_map.get(row['extracted_date_type'])
            if col:
                _pstage, _ptype = _proj_info.get(pid, (None, None))
                _is_op_port = (_pstage == 'operational' and _ptype == 'company_portfolio')
                skip_date = False
                if _is_op_port:
                    if col == 'cod_date' and row['extracted_date'] > '2026':
                        skip_date = True
                    if col == 'construction_start' and row['extracted_date'] > '2025':
                        skip_date = True
                if not skip_date:
                    updates.append(f"{col} = COALESCE(NULLIF({col},''), ?)")
                    params.append(row['extracted_date'])

        if updates:
            db.execute(f"""
                UPDATE unified_projects
                SET {', '.join(updates)}, updated_at=?
                WHERE project_id=? AND quarantined=0
            """, params + [now_iso(), pid])
            updated += 1

        # Stage from extracted fields — date-aware
        ext_stage = row['extracted_stage']
        if ext_stage and not DRY_RUN:
            doc_date = row['document_date'] or row['extracted_date']
            applied = apply_stage_update(
                db, pid, ext_stage, new_date=doc_date,
                reasoning=f'LLM extracted stage: {ext_stage}',
                confidence=row['confidence']
            )
            if applied:
                stage_updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Phase 5b — enrich from extracted fields: {updated} field updates, '
          f'{stage_updated} stage updates from {len(rows)} claims')

    # ── Parent company: separate pass with temporal ordering + normalization ──
    _key_to_name = {}
    for r in db.execute("SELECT company_key, company_name FROM companies"):
        _key_to_name[r['company_key']] = r['company_name']

    parent_rows = db.execute("""
        SELECT pc.project_id, c.extracted_parent_company, c.document_date
        FROM project_claims pc
        JOIN claims c ON pc.claim_id = c.claim_id
        WHERE pc.resolution_method != 'unresolved'
          AND c.extracted_parent_company IS NOT NULL
          AND c.extracted_parent_company != ''
        ORDER BY c.document_date DESC, c.confidence DESC
    """).fetchall()

    seen_pids = set()
    parent_updated = 0
    now = now_iso()
    for row in parent_rows:
        pid = row['project_id']
        if pid in seen_pids:
            continue
        seen_pids.add(pid)
        val = row['extracted_parent_company']
        # Skip circular (same as developer_name)
        if val.strip().upper() == _dev_names.get(pid, ''):
            continue
        # Normalize through PARENT_NAME_MAP
        normalized = _normalize_parent_company(val, _key_to_name)
        # LLM fallback for long unresolved strings (Change 13)
        if not normalized and len(val) > 20 and _GROQ_KEY:
            try:
                first_word = val.split()[0].lower() if val.split() else ''
                relevant = [n for n in _key_to_name.values()
                            if first_word in n.lower()]
                if not relevant:
                    relevant = list(_key_to_name.values())[:20]
                known_companies = ', '.join(sorted(set(relevant)))
                _pc_prompt = (
                    f'Extract the canonical parent company name from this text.\n'
                    f'If it matches one of these known companies, use that exact name:\n'
                    f'{known_companies}\n\n'
                    f'Text: "{val}"\n\n'
                    f'Return JSON only: {{"parent_company": "<name or null if not identifiable>"}}'
                )
                _pc_client = Groq(api_key=_GROQ_KEY)
                _pc_resp = _pc_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{'role': 'user', 'content': _pc_prompt}],
                    temperature=0.0, max_tokens=60)
                _pc_raw = _pc_resp.choices[0].message.content.strip()
                _pc_raw = re.sub(r'^```(?:json)?\s*', '', _pc_raw, flags=re.DOTALL)
                _pc_raw = re.sub(r'\s*```$',          '', _pc_raw, flags=re.DOTALL)
                _pc_result = json.loads(_pc_raw)
                if _pc_result.get('parent_company') and _pc_result['parent_company'] != 'null':
                    normalized = _pc_result['parent_company']
                time.sleep(GROQ_SLEEP)
            except Exception:
                pass
        final = normalized if normalized else val
        # Post-normalization circular check
        if final.strip().upper() == _dev_names.get(pid, ''):
            continue
        if not DRY_RUN:
            db.execute("""
                UPDATE unified_projects SET parent_company=?, updated_at=?
                WHERE project_id=?
            """, (final, now, pid))
        parent_updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Phase 5b — parent_company (temporal, normalized): {parent_updated}')


def phase_enrich_from_claims(db: sqlite3.Connection) -> None:
    """Extract city, technology, capacity, epc_contractor, timeline from resolved claims.

    Scans all resolved claims with canonical types and extracts structured values
    into unified_projects. Only fills NULL/empty fields (COALESCE semantics).
    """
    print('\n── Phase 5b: Enrich from resolved claims ────────────────────────')

    if not db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_claims'"
    ).fetchone():
        print('  project_claims not found — run connect.py first')
        return

    projects = db.execute("SELECT project_id, developer_name, city, technology, capacity_raw, epc_contractor, fid_date, cod_date, construction_start, stage, project_type FROM unified_projects").fetchall()
    print(f'  {len(projects)} projects to check')

    now = now_iso()
    filled = {'city': 0, 'technology': 0, 'capacity': 0, 'epc': 0, 'fid_date': 0, 'cod_date': 0, 'construction_start': 0}

    for proj in projects:
        pid = proj['project_id']
        updates = {}

        claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence, c.document_date
            FROM project_claims pc
            JOIN claims c ON c.claim_id = pc.claim_id
            WHERE pc.project_id = ?
              AND c.superseded_by IS NULL
            ORDER BY c.confidence DESC
        """, (pid,)).fetchall()

        # Count distinct location claims — skip city for multi-facility portfolios
        loc_count = sum(1 for c in claims if (c['claim_type'] or '') == 'location')
        dev_name = proj['developer_name'] or ''
        skip_city = loc_count > 3 or dev_name in MULTI_FACILITY_DEVELOPERS

        for c in claims:
            ct = c['claim_type'] or ''
            text = c['claim_text'] or ''

            # City from location claims (skip multi-facility companies)
            if not proj['city'] and 'city' not in updates and ct == 'location' and not skip_city:
                city = _parse_city_from_text(text)
                if city:
                    updates['city'] = city
                    filled['city'] += 1

            # Technology from technology claims (reject narratives > 60 chars)
            if not proj['technology'] and 'technology' not in updates and ct == 'technology':
                tech = text[:200].strip()
                if tech and len(tech) >= 3 and len(tech) <= 60:
                    updates['technology'] = tech
                    filled['technology'] += 1

            # Capacity
            if not proj['capacity_raw'] and 'capacity_raw' not in updates and ct == 'capacity':
                cap_raw, cap_mtpa = _parse_capacity(text)
                if cap_raw:
                    updates['capacity_raw'] = cap_raw
                    updates['capacity_mtpa_h2'] = cap_mtpa
                    filled['capacity'] += 1

            # EPC contractor
            if not proj['epc_contractor'] and 'epc_contractor' not in updates and ct == 'epc_contractor':
                epc = _parse_epc_contractor(text)
                if epc:
                    updates['epc_contractor'] = epc
                    filled['epc'] += 1

            # Timeline: FID date, COD date, construction start
            # Guard: skip future dates for operational company_portfolio projects
            _is_operational_portfolio = (
                proj['stage'] == 'operational' and proj['project_type'] == 'company_portfolio')
            if ct == 'timeline':
                if not proj['cod_date'] and 'cod_date' not in updates:
                    cod = _parse_date(text, 'cod')
                    if cod and not (_is_operational_portfolio and cod > '2026'):
                        updates['cod_date'] = cod
                        filled['cod_date'] += 1
                if not proj['fid_date'] and 'fid_date' not in updates:
                    fid = _parse_date(text, 'fid')
                    if fid:
                        updates['fid_date'] = fid
                        filled['fid_date'] += 1
                if not proj['construction_start'] and 'construction_start' not in updates:
                    cs = _parse_date(text, 'construction_start')
                    if cs and not (_is_operational_portfolio and cs > '2025'):
                        updates['construction_start'] = cs
                        filled['construction_start'] += 1

            # Stage claims — collect all candidates, pick most-advanced after loop
            if ct == 'stage':
                stage_text = text.lower().strip()
                for keyword, stage_val in [
                    ('cancelled', 'cancelled'), ('terminated', 'cancelled'),
                    ('withdrawn', 'cancelled'), ('on hold', 'on_hold'),
                    ('paused', 'on_hold'),
                    ('operating', 'operational'), ('operational', 'operational'),
                    ('commissioning', 'commissioning'),
                    ('notice to proceed', 'construction'),
                    ('under construction', 'construction'),
                    ('construction', 'construction'), ('permitted', 'permitted'),
                    ('fid', 'fid'), ('financed', 'financed'),
                    ('awarded', 'awarded'), ('announced', 'announced'),
                ]:
                    if keyword in stage_text:
                        if 'stage_candidates' not in updates:
                            updates['stage_candidates'] = []
                        updates['stage_candidates'].append(
                            (stage_val, c['document_date'], text[:200], c['confidence']))
                        break

        # Pick the most-advanced stage from collected candidates
        _STAGE_ORDER = ['announced', 'awarded', 'permitted',
                        'financed', 'fid', 'construction', 'commissioning', 'operational']
        if 'stage_candidates' in updates:
            candidates = updates.pop('stage_candidates')
            forward = [c for c in candidates if c[0] in _STAGE_ORDER]
            regressive = [c for c in candidates if c[0] not in _STAGE_ORDER]
            if forward:
                best = max(forward, key=lambda x: (_STAGE_ORDER.index(x[0]), x[1] or ''))
            else:
                best = max(regressive, key=lambda x: x[1] or '')
            applied = apply_stage_update(
                db, pid, best[0], new_date=best[1],
                reasoning=best[2], confidence=best[3])
            if applied:
                filled.setdefault('stage', 0)
                filled['stage'] += 1

        if updates and not DRY_RUN:
            set_clauses = []
            params = []
            for col in ['city', 'technology', 'capacity_raw', 'capacity_mtpa_h2',
                         'epc_contractor', 'fid_date', 'cod_date', 'construction_start']:
                if col in updates:
                    set_clauses.append(f"{col}=COALESCE(NULLIF({col},''), ?)")
                    params.append(updates[col])
            if set_clauses:
                set_clauses.append("updated_at=?")
                params.append(now)
                params.append(pid)
                db.execute(
                    f"UPDATE unified_projects SET {', '.join(set_clauses)} WHERE project_id=?",
                    params
                )

    if not DRY_RUN:
        db.commit()

    print(f'  Filled: city={filled["city"]}, technology={filled["technology"]}, '
          f'capacity={filled["capacity"]}, epc={filled["epc"]}, '
          f'fid_date={filled["fid_date"]}, cod_date={filled["cod_date"]}, '
          f'construction_start={filled["construction_start"]}, '
          f'stage={filled.get("stage", 0)}')


def phase_enrich_from_epa(db: sqlite3.Connection) -> None:
    """Fill city and technology from structured EPA permits and regulatory_evidence.

    Sources:
      - epa_permits.facility_city → unified_projects.city
      - regulatory_evidence.derived_technology → unified_projects.technology
    Links via: claims.source_id → regulatory_evidence.id → document_url → registry_id → epa_permits
    """
    print('\n── Phase 5c: Enrich from EPA structured data ────────────────────')

    # Build registry_id map from regulatory_evidence
    epa_sources = db.execute("""
        SELECT id, document_url, derived_technology
        FROM regulatory_evidence
        WHERE source_system = 'epa_echo'
    """).fetchall()

    re_to_reg = {}
    re_to_tech = {}
    for r in epa_sources:
        url = r['document_url'] or ''
        if 'fid=' in url:
            reg_id = url.split('fid=')[-1]
            re_to_reg[r['id']] = reg_id
        if r['derived_technology']:
            re_to_tech[r['id']] = r['derived_technology']

    # Build registry_id → best city from epa_permits
    reg_to_city = {}
    for row in db.execute("""
        SELECT registry_id, facility_city, facility_state
        FROM epa_permits
        WHERE facility_city IS NOT NULL AND facility_city != ''
    """):
        reg = row['registry_id']
        raw_city = row['facility_city'] or ''
        # Clean: "FREEPORT, BRAZORIA COUNTY County, TX" → "Freeport"
        m = re.match(r'^([A-Z][A-Z\s]+?)(?:,|\s*$)', raw_city)
        if m:
            clean_city = m.group(1).strip().title()
            if reg not in reg_to_city:
                reg_to_city[reg] = (clean_city, row['facility_state'])

    # Map projects → EPA source_ids
    proj_sources = db.execute("""
        SELECT DISTINCT pc.project_id, c.source_id
        FROM project_claims pc
        JOIN claims c ON c.claim_id = pc.claim_id
        WHERE c.source_id IS NOT NULL
    """).fetchall()

    proj_epa_cities = {}  # project_id → (city, state)
    proj_epa_tech = {}    # project_id → technology
    for row in proj_sources:
        sid = row['source_id']
        pid = row['project_id']
        if sid in re_to_reg and re_to_reg[sid] in reg_to_city:
            if pid not in proj_epa_cities:
                proj_epa_cities[pid] = reg_to_city[re_to_reg[sid]]
        if sid in re_to_tech:
            if pid not in proj_epa_tech:
                proj_epa_tech[pid] = re_to_tech[sid]

    print(f'  EPA city available for {len(proj_epa_cities)} projects')
    print(f'  EPA technology available for {len(proj_epa_tech)} projects')

    now = now_iso()
    filled_city = filled_tech = 0

    # Build set of multi-facility project_ids to skip city assignment
    multi_fac_pids = set()
    for dev in MULTI_FACILITY_DEVELOPERS:
        row = db.execute("SELECT project_id FROM unified_projects WHERE developer_name=?", (dev,)).fetchone()
        if row:
            multi_fac_pids.add(row['project_id'])

    for pid, (city, state) in proj_epa_cities.items():
        if pid in multi_fac_pids:
            continue
        if not DRY_RUN:
            r = db.execute("""
                UPDATE unified_projects
                SET city=COALESCE(NULLIF(city,''), ?),
                    updated_at=?
                WHERE project_id=? AND (city IS NULL OR city='')
            """, (city, now, pid)).rowcount
            if r:
                filled_city += 1

    for pid, tech in proj_epa_tech.items():
        # Convert codes to readable form
        tech_readable = tech.replace('_', ' ').title()
        if not DRY_RUN:
            r = db.execute("""
                UPDATE unified_projects
                SET technology=COALESCE(NULLIF(technology,''), ?),
                    updated_at=?
                WHERE project_id=? AND (technology IS NULL OR technology='')
            """, (tech_readable, now, pid)).rowcount
            if r:
                filled_tech += 1

    if not DRY_RUN:
        db.commit()

    print(f'  Filled from EPA: city={filled_city}, technology={filled_tech}')


# ── Phase 5d: Technology & Capacity enrichment (multi-source) ────────────────

def phase_enrich_technology_capacity(db: sqlite3.Connection) -> None:
    """Fill technology and capacity_raw from multiple verbatim sources.

    Six sub-passes, each filling only NULL/empty fields (no normalization):
      1: regulatory_evidence.derived_technology (all source_systems, priority-ordered)
      2: claims.extracted_technology (confidence >= 0.7, no GHGRP boilerplate)
      3: claims.extracted_capacity (highest confidence)
      4: GHGRP Subpart P reporting → hydrogen production
      5: DOE award project_description → technology
      6: Facility name keywords → technology
    """
    print('\n── Phase 5d: Technology & capacity enrichment (multi-source) ─────')

    now = now_iso()
    totals = {}

    # ── Sub-pass 1: derived_technology from ALL regulatory_evidence ────────
    # Superset of phase_enrich_from_epa (which only handles epa_echo).
    # Priority: doe_oced_detail > tx_rrc > epa_uic > epa_echo > other.
    # NOTE: derived_technology is the MOST authoritative source (set by
    # collectors from actual regulatory data), so it OVERWRITES whatever
    # Phase 5b may have set (e.g., a DOE project description paragraph).
    all_projects = db.execute("""
        SELECT project_id FROM unified_projects
    """).fetchall()

    updated_1 = 0
    for p in all_projects:
        pid = p['project_id']
        row = db.execute("""
            SELECT re.derived_technology, re.source_system
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            JOIN cleaned_documents cd ON c.source_id = cd.source_id
            JOIN regulatory_evidence re ON re.id = cd.source_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND re.derived_technology IS NOT NULL AND re.derived_technology != ''
            ORDER BY
                CASE re.source_system
                    WHEN 'doe_oced_detail' THEN 1
                    WHEN 'tx_rrc' THEN 2
                    WHEN 'epa_uic' THEN 3
                    WHEN 'epa_echo' THEN 4
                    ELSE 5
                END,
                re.id
            LIMIT 1
        """, (pid,)).fetchone()

        if row:
            if not DRY_RUN:
                db.execute(
                    "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                    (row['derived_technology'], now, pid)
                )
            updated_1 += 1

    if not DRY_RUN:
        db.commit()
    totals['derived_tech'] = updated_1
    print(f'    Sub-pass 1 (derived_technology): {updated_1}')

    # ── Sub-pass 2: extracted_technology (filtered) ────────────────────────
    # confidence >= 0.7, exclude GHGRP boilerplate, most frequent value.
    projects_null_tech = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE technology IS NULL OR technology = ''
    """).fetchall()

    updated_2 = 0
    for p in projects_null_tech:
        pid = p['project_id']
        row = db.execute("""
            SELECT c.extracted_technology, COUNT(*) as cnt
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.extracted_technology IS NOT NULL AND c.extracted_technology != ''
              AND c.confidence >= 0.7
              AND c.claim_text NOT LIKE 'This facility reports under GHGRP%'
            GROUP BY c.extracted_technology
            ORDER BY cnt DESC
            LIMIT 1
        """, (pid,)).fetchone()

        if row:
            if not DRY_RUN:
                db.execute(
                    "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                    (row['extracted_technology'], now, pid)
                )
            updated_2 += 1

    if not DRY_RUN:
        db.commit()
    totals['extracted_tech'] = updated_2
    print(f'    Sub-pass 2 (extracted_technology): {updated_2}')

    # ── Sub-pass 3: extracted_capacity (highest confidence, regulatory-first) ─
    _CAPACITY_EXCLUDE_TYPES = (
        'injection_capacity', 'capture_capacity', 'well_count',
        'injection_wells', 'number_of_wells',
    )
    _cap_placeholders = ','.join('?' * len(_CAPACITY_EXCLUDE_TYPES))

    projects_null_cap = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE capacity_raw IS NULL OR capacity_raw = ''
    """).fetchall()

    updated_3 = 0
    for p in projects_null_cap:
        pid = p['project_id']
        row = db.execute(f"""
            SELECT c.extracted_capacity, c.confidence, c.claim_type
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            LEFT JOIN cleaned_documents cd ON c.source_id = cd.source_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.extracted_capacity IS NOT NULL AND c.extracted_capacity != ''
              AND COALESCE(pc.claim_type,'') NOT IN ({_cap_placeholders})
            ORDER BY
              CASE WHEN cd.source_system IN ('epa_ghgrp','epa_echo','epa_uic',
                   'eia_860m','doe_oced_detail','tx_rrc') THEN 0 ELSE 1 END,
              c.confidence DESC, c.claim_id
            LIMIT 1
        """, (pid, *_CAPACITY_EXCLUDE_TYPES)).fetchone()

        if row:
            _cap_val = row['extracted_capacity']
            # Validate: must contain number + recognized unit
            if not re.search(r'\d.*(?:MW|GW|MTPA|mt|ton|bbl|mcf|kg|million)', _cap_val, re.I):
                continue
            if not DRY_RUN:
                db.execute(
                    "UPDATE unified_projects SET capacity_raw=?, updated_at=? WHERE project_id=?",
                    (_cap_val, now, pid)
                )
            updated_3 += 1

    if not DRY_RUN:
        db.commit()
    totals['capacity'] = updated_3
    print(f'    Sub-pass 3 (extracted_capacity): {updated_3}')

    # ── Sub-pass 4: GHGRP Subpart P → hydrogen production ─────────────────
    rows_sp = db.execute("""
        SELECT DISTINCT up.project_id, up.project_name, c.claim_text
        FROM unified_projects up
        JOIN project_claims pc ON up.project_id = pc.project_id
        JOIN claims c ON pc.claim_id = c.claim_id
        WHERE (up.technology IS NULL OR up.technology = '')
          AND pc.resolution_method != 'unresolved'
          AND c.claim_type = 'hydrogen_production_method'
          AND c.claim_text LIKE '%Subpart P%Hydrogen Production%'
        ORDER BY up.project_name
    """).fetchall()

    updated_4 = 0
    for r in rows_sp:
        if not DRY_RUN:
            db.execute(
                "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                ('Hydrogen Production (GHGRP Subpart P)', now, r['project_id'])
            )
        updated_4 += 1

    if not DRY_RUN:
        db.commit()
    totals['ghgrp_subpart_p'] = updated_4
    print(f'    Sub-pass 4 (GHGRP Subpart P): {updated_4}')

    # ── Sub-pass 5: DOE award project_description ──────────────────────────
    # NOTE: Skipped — raw project descriptions are not technology values.
    # Phase 5e (LLM classification) handles DOE award technology extraction
    # from all available claims, including project_description claims.
    totals['doe_description'] = 0
    print(f'    Sub-pass 5 (DOE description): skipped — delegated to Phase 5e LLM')

    # ── Sub-pass 6: Facility name keywords ─────────────────────────────────
    FACILITY_KEYWORDS = [
        ('HYDROGEN', 'hydrogen_production'),
        ('AMMONIA', 'ammonia_production'),
        ('CCS', 'ccs'),
        ('CARBON CAPTURE', 'carbon_capture'),
        ('CO2', 'co2_processing'),
    ]

    fac_projects = db.execute("""
        SELECT project_id, project_name, source_identity FROM unified_projects
        WHERE (technology IS NULL OR technology = '')
          AND source_identity IS NOT NULL AND source_identity != ''
    """).fetchall()

    updated_6 = 0
    for p in fac_projects:
        name_upper = (p['source_identity'] or '').upper()
        for keyword, tech in FACILITY_KEYWORDS:
            if keyword in name_upper:
                if not DRY_RUN:
                    db.execute(
                        "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                        (tech, now, p['project_id'])
                    )
                updated_6 += 1
                break  # first match wins

    if not DRY_RUN:
        db.commit()
    totals['facility_name'] = updated_6
    print(f'    Sub-pass 6 (facility name): {updated_6}')

    # ── Sub-pass 7: Dates from project_events ──────────────────────────────
    # project_events is populated by migrate_companies.py Phase 6 but bootstrap
    # never reads them back. event_type values: fid_date(2), cod_date(4),
    # construction_start(1).
    DATE_EVENT_TYPES = [
        ('fid_date', 'fid_date'),
        ('cod_date', 'cod_date'),
        ('construction_start', 'construction_start'),
    ]

    updated_7 = 0
    _op_portfolio_pids = {r['project_id'] for r in db.execute("""
        SELECT project_id FROM unified_projects
        WHERE stage='operational' AND project_type='company_portfolio'
    """)}
    for col, event_type in DATE_EVENT_TYPES:
        projects_null = db.execute(f"""
            SELECT project_id FROM unified_projects
            WHERE {col} IS NULL OR {col} = ''
        """).fetchall()

        for p in projects_null:
            row = db.execute("""
                SELECT event_date, confidence
                FROM project_events
                WHERE project_id = ?
                  AND event_type = ?
                ORDER BY confidence DESC, event_date DESC
                LIMIT 1
            """, (p['project_id'], event_type)).fetchone()

            if row and row['event_date']:
                # Guard: skip future dates on operational company_portfolio
                if p['project_id'] in _op_portfolio_pids:
                    if col == 'cod_date' and row['event_date'] > '2026':
                        continue
                    if col == 'construction_start' and row['event_date'] > '2025':
                        continue
                if not DRY_RUN:
                    db.execute(f"""
                        UPDATE unified_projects SET {col}=?, updated_at=?
                        WHERE project_id=?
                    """, (row['event_date'], now, p['project_id']))
                updated_7 += 1

    if not DRY_RUN:
        db.commit()
    totals['dates_from_events'] = updated_7
    print(f'    Sub-pass 7 (dates from project_events): {updated_7}')

    # ── Sub-pass 8: State and capacity from EIA-860M ───────────────────────
    # EIA-860M raw_text_excerpt has structured format:
    # "Plant: X | Entity: Y | County: Wharton, TX | Capacity: 260.0 MW | ..."
    # Only write state and capacity_raw (no county→city substitution).
    eia_rows = db.execute("""
        SELECT DISTINCT re.raw_text_excerpt, pc.project_id
        FROM project_claims pc
        JOIN claims c ON pc.claim_id = c.claim_id
        JOIN cleaned_documents cd ON c.source_id = cd.source_id
        JOIN regulatory_evidence re ON re.id = cd.source_id
        WHERE re.source_system = 'eia_860m'
          AND pc.resolution_method != 'unresolved'
    """).fetchall()

    updated_8 = 0
    for r in eia_rows:
        pid = r['project_id']
        text = r['raw_text_excerpt'] or ''

        # Parse state from "County: {county}, {state_abbrev}"
        eia_state = None
        m = re.search(r'County:\s*[^,|]+,\s*([A-Z]{2})', text)
        if m:
            eia_state = m.group(1)

        # Parse capacity from "Capacity: {value} {unit}"
        eia_cap = None
        m = re.search(r'Capacity:\s*([\d.]+\s*MW)', text)
        if m:
            eia_cap = m.group(1)

        if eia_state or eia_cap:
            sets = []
            params = []
            if eia_state:
                sets.append("state=COALESCE(NULLIF(state,''), ?)")
                params.append(eia_state)
            if eia_cap:
                # EIA-860M is authoritative nameplate data — overwrite unconditionally
                sets.append("capacity_raw=?")
                params.append(eia_cap)
            sets.append("updated_at=?")
            params.append(now)
            params.append(pid)

            if not DRY_RUN:
                db.execute(
                    f"UPDATE unified_projects SET {', '.join(sets)} WHERE project_id=?",
                    params
                )
            updated_8 += 1

    if not DRY_RUN:
        db.commit()
    totals['eia_860m'] = updated_8
    print(f'    Sub-pass 8 (EIA-860M state/capacity): {updated_8}')

    print(f'  Phase 5d totals: {totals}')


# ── Phase 5e: LLM technology classification ──────────────────────────────────

_TECH_CLASSIFY_PROMPT = """Given these technology descriptions extracted from regulatory
and financial documents for a hydrogen/CCS/ammonia project, classify the PRIMARY
production technology.

Project: {project_name}
Developer: {developer_name}

Technology-related claims:
{claims_text}

Choose from ONLY these canonical values:
- steam_methane_reforming (SMR — gray or blue hydrogen from natural gas)
- autothermal_reforming (ATR — similar to SMR but with oxygen injection)
- electrolysis (green hydrogen from water using electricity)
- partial_oxidation (POX — hydrogen from heavy hydrocarbons)
- gasification (hydrogen from coal, petcoke, or biomass)
- ammonia_production (ammonia synthesis, often from hydrogen)
- point_source_ccs (CO2 capture from industrial flue gas)
- direct_air_capture (DAC — CO2 capture from ambient air)
- co2_transport_storage (CO2 pipeline or sequestration only)
- hydrogen_production (generic — when specific process is unknown)
- methanol_production (methanol synthesis)
- nuclear_smr (small modular nuclear reactor)
- fuel_cell (hydrogen fuel cell for power generation)
- hydrogen_storage (underground or tank storage of H2)
- other (none of the above)

Return JSON only:
{{"primary": "<value from list>", "secondary": ["<optional additional values>"],
 "confidence": 0.0-1.0}}"""


def phase_classify_technology(db: sqlite3.Connection) -> None:
    """Use LLM to classify technology for projects with NULL/empty/verbose technology."""
    print('\n── Phase 5e: LLM technology classification ──────────────────────')

    rows = db.execute("""
        SELECT project_id, project_name, developer_name, technology
        FROM unified_projects
        WHERE technology IS NULL OR technology = '' OR length(technology) > 60
    """).fetchall()

    if not rows:
        print('  Nothing to classify — all projects have clean technology values.')
        return

    print(f'  {len(rows)} projects need technology classification')

    if DRY_RUN:
        for r in rows:
            print(f'    {r["project_name"]:<40} tech={r["technology"] or "(NULL)"}')
        return

    groq_client = Groq(api_key=_GROQ_KEY) if _GROQ_KEY else None
    if not groq_client:
        print('  ERROR: No GROQ_API_KEY — cannot classify technologies.')
        return

    classified = 0
    for r in rows:
        pid = r['project_id']
        # Gather all technology-related claims for this project
        claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence, c.document_date
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.claim_type IN ('technology', 'capture_technology',
                                   'production_technology', 'feedstock',
                                   'product', 'process_description',
                                   'project_description')
            ORDER BY c.confidence DESC
            LIMIT 10
        """, (pid,)).fetchall()

        if not claims:
            continue

        claims_text = ''
        for i, c in enumerate(claims, 1):
            claims_text += (
                f'{i}. [{c["claim_type"]}] '
                f'(conf={c["confidence"]:.2f}, date={c["document_date"] or "?"})\n'
                f'   {(c["claim_text"] or "")[:200]}\n'
            )

        prompt = _TECH_CLASSIFY_PROMPT.format(
            project_name=r['project_name'] or '',
            developer_name=r['developer_name'] or '',
            claims_text=claims_text,
        )

        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.0,
                max_tokens=150,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw = re.sub(r'\s*```$',          '', raw, flags=re.DOTALL)
            result = json.loads(raw)

            primary = result.get('primary', '').strip()
            confidence = float(result.get('confidence', 0.0))

            _VALID_TECHS = {
                'steam_methane_reforming', 'autothermal_reforming', 'electrolysis',
                'partial_oxidation', 'gasification', 'ammonia_production',
                'point_source_ccs', 'direct_air_capture', 'co2_transport_storage',
                'hydrogen_production', 'methanol_production', 'nuclear_smr',
                'fuel_cell', 'hydrogen_storage', 'other',
            }

            if primary in _VALID_TECHS and confidence >= 0.5:
                db.execute("""
                    UPDATE unified_projects
                    SET technology=?, updated_at=?
                    WHERE project_id=?
                """, (primary, now_iso(), pid))
                classified += 1
                print(f'    {r["project_name"]:<40} → {primary}  (conf={confidence:.2f})')
            else:
                print(f'    {r["project_name"]:<40} → skipped (primary={primary}, conf={confidence:.2f})')

        except Exception as e:
            print(f'    {r["project_name"]:<40} → ERROR: {e}')

        time.sleep(GROQ_SLEEP)

    db.commit()
    print(f'  Classified {classified}/{len(rows)} projects')


# ── Phase 5e2: LLM stage refinement for ambiguous cases ─────────────────────

_STAGE_REFINE_PROMPT = """You are assessing the development stage of a hydrogen,
ammonia, or carbon capture project based on regulatory filings and news articles.

Company: {company}
State: {state}
Technology: {technology}

Most informative claims (highest confidence first):
{claims_text}

Additional context — these stage keywords were found in claims:
{keyword_signals}

Consider hedge language carefully:
- "subject to final board approval" → pre-FID, not FID
- "commenced site preparation" → may be pre-construction
- "received regulatory approvals and expects to begin" → permitted, not construction
- "operations ramping to nameplate" → commissioning, not operational

Choose the single most accurate stage:
{stage_options}

Rules:
- "awarded" means a government/DOE grant was received — use this if the main
  evidence is a federal award with no further construction signals
- "announced" means the company itself confirmed the project exists
- Only use "feasibility" or "feed" if there is explicit language about studies
- Prefer more advanced stages when evidence supports it
- Use "unknown" only when claims give no project-stage signal at all
- If confidence is below 0.5, return "unknown"

Return JSON only:
{{
  "stage": "<stage from list above>",
  "confidence": 0.0-1.0,
  "reasoning": "one sentence citing the specific claim that drove this decision"
}}"""


def phase_refine_stages(db: sqlite3.Connection) -> None:
    """LLM refinement pass for projects with ambiguous stage signals.

    Only fires when:
    - Project has both forward AND regressive stage signals, OR
    - Highest forward stage is from a document >2 years old (stale)
    """
    print('\n── Phase 5e2: LLM stage refinement (ambiguous cases) ────────────')

    _STAGE_ORDER = ['announced', 'awarded', 'permitted',
                    'financed', 'fid', 'construction', 'commissioning', 'operational']

    stage_options = '\n'.join(f'  - {s}' for s in STAGE_DEFINITIONS)

    projects = db.execute("""
        SELECT project_id, project_name, developer_name, state, technology, stage
        FROM unified_projects
    """).fetchall()

    if DRY_RUN:
        print(f'  {len(projects)} projects to check for ambiguity')
        return

    groq_client = Groq(api_key=_GROQ_KEY) if _GROQ_KEY else None
    if not groq_client:
        print('  ERROR: No GROQ_API_KEY — cannot refine stages.')
        return

    refined = 0
    checked = 0
    cutoff_year = str(int(datetime.now().strftime('%Y')) - 2)

    for proj in projects:
        pid = proj['project_id']

        # Gather stage-related claims
        stage_claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence, c.document_date
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.claim_type IN ('stage', 'timeline', 'final_investment_decision',
                                   'construction_commencement', 'commercial_operations')
            ORDER BY c.confidence DESC, c.document_date DESC
        """, (pid,)).fetchall()

        if len(stage_claims) < 2:
            continue

        # Extract keyword signals from stage claims
        candidates = []
        for c in stage_claims:
            text = (c['claim_text'] or '').lower().strip()
            for keyword, stage_val in [
                ('cancelled', 'cancelled'), ('terminated', 'cancelled'),
                ('withdrawn', 'cancelled'), ('on hold', 'on_hold'),
                ('paused', 'on_hold'),
                ('operating', 'operational'), ('operational', 'operational'),
                ('commissioning', 'commissioning'),
                ('notice to proceed', 'construction'),
                ('under construction', 'construction'),
                ('construction', 'construction'), ('permitted', 'permitted'),
                ('fid', 'fid'), ('financed', 'financed'),
                ('awarded', 'awarded'), ('announced', 'announced'),
            ]:
                if keyword in text:
                    candidates.append((stage_val, c['document_date'],
                                       (c['claim_text'] or '')[:200], c['confidence']))
                    break

        if not candidates:
            continue

        forward = [c for c in candidates if c[0] in _STAGE_ORDER]
        regressive = [c for c in candidates if c[0] not in _STAGE_ORDER]

        # Check ambiguity conditions
        has_regressive = len(regressive) > 0
        has_forward = len(forward) > 0
        ambiguous = has_regressive and has_forward

        if not ambiguous and forward:
            best_forward = max(forward, key=lambda x: _STAGE_ORDER.index(x[0]))
            if best_forward[1] and best_forward[1] < cutoff_year:
                ambiguous = True

        if not ambiguous:
            continue

        checked += 1

        # Build claims text for LLM
        all_claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence,
                   c.document_type, c.document_date
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.superseded_by IS NULL
            ORDER BY c.confidence DESC, c.document_date DESC
            LIMIT 15
        """, (pid,)).fetchall()

        claims_text = ''
        for i, c in enumerate(all_claims, 1):
            claims_text += (
                f'{i}. [{c["claim_type"]}] '
                f'(conf={c["confidence"]:.2f}, '
                f'doc={c["document_type"]}, '
                f'date={c["document_date"] or "?"})\n'
                f'   {(c["claim_text"] or "")[:200]}\n'
            )

        keyword_signals = ', '.join(f'{c[0]} ({c[1] or "no date"})' for c in candidates)

        prompt = _STAGE_REFINE_PROMPT.format(
            company=proj['developer_name'] or '',
            state=proj['state'] or 'unknown',
            technology=proj['technology'] or 'unknown',
            claims_text=claims_text,
            keyword_signals=keyword_signals,
            stage_options=stage_options,
        )

        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.0,
                max_tokens=200,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw = re.sub(r'\s*```$',          '', raw, flags=re.DOTALL)
            result = json.loads(raw)

            stage = result.get('stage', 'unknown')
            confidence = float(result.get('confidence', 0.0))
            reasoning = result.get('reasoning', '')

            if stage not in STAGE_DEFINITIONS:
                stage = 'unknown'

            if stage != 'unknown' and confidence >= 0.5:
                evidence_date = all_claims[0]['document_date'] if all_claims else None
                applied = apply_stage_update(
                    db, pid, stage, new_date=evidence_date,
                    reasoning=f'[LLM-refined] {reasoning}', confidence=confidence
                )
                if applied:
                    refined += 1
                    print(f'    {proj["project_name"]:<40} → {stage}  (conf={confidence:.2f})')
                else:
                    print(f'    {proj["project_name"]:<40} → no change (transition blocked)')
            else:
                print(f'    {proj["project_name"]:<40} → skipped (stage={stage}, conf={confidence:.2f})')

        except Exception as e:
            print(f'    {proj["project_name"]:<40} → ERROR: {e}')

        db.commit()
        time.sleep(GROQ_SLEEP)

    print(f'  Checked {checked} ambiguous projects, refined {refined}')


# ── Phase 5f: Claim staleness detection ──────────────────────────────────────

_STALE_CHECK_PROMPT = """Do these two claims about the same project contradict
or supersede each other?

Claim A ({date_a}): {text_a}
Claim B ({date_b}): {text_b}

Respond JSON only:
{{"relationship": "SUPERSEDES|CONTRADICTS|INDEPENDENT|CORROBORATES",
 "explanation": "one sentence"}}"""


def phase_detect_stale_claims(db: sqlite3.Connection) -> None:
    """Detect contradictory or stale claims spanning >18 months.

    Pairs the highest-stage claim with the most-recent claim per project.
    If LLM says SUPERSEDES or CONTRADICTS, flags in evidence_gap_flags_json.
    """
    print('\n── Phase 5f: Claim staleness detection ─────────────────────────')

    _STAGE_ORDER = ['announced', 'awarded', 'permitted',
                    'financed', 'fid', 'construction', 'commissioning', 'operational']

    # Pre-filter: projects with stage claims spanning > 540 days
    candidates = db.execute("""
        SELECT pc.project_id,
               MIN(c.document_date) AS oldest,
               MAX(c.document_date) AS newest,
               COUNT(*) AS claim_count
        FROM project_claims pc
        JOIN claims c ON pc.claim_id = c.claim_id
        WHERE pc.resolution_method != 'unresolved'
          AND c.claim_type IN ('stage', 'timeline', 'final_investment_decision',
                               'construction_commencement', 'commercial_operations')
          AND c.document_date IS NOT NULL AND c.document_date != ''
        GROUP BY pc.project_id
        HAVING julianday(MAX(c.document_date)) - julianday(MIN(c.document_date)) > 540
    """).fetchall()

    if not candidates:
        print('  No projects with stage claims spanning > 18 months.')
        return

    print(f'  {len(candidates)} projects with wide claim spans')

    if DRY_RUN:
        for c in candidates:
            print(f'    {c["project_id"][:30]:<30} oldest={c["oldest"]} newest={c["newest"]}')
        return

    groq_client = Groq(api_key=_GROQ_KEY) if _GROQ_KEY else None
    if not groq_client:
        print('  ERROR: No GROQ_API_KEY — cannot detect stale claims.')
        return

    flagged = 0
    for cand in candidates:
        pid = cand['project_id']

        # Get all stage-related claims for this project
        claims = db.execute("""
            SELECT c.claim_type, c.claim_text, c.confidence, c.document_date
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.claim_type IN ('stage', 'timeline', 'final_investment_decision',
                                   'construction_commencement', 'commercial_operations')
              AND c.document_date IS NOT NULL AND c.document_date != ''
            ORDER BY c.document_date DESC
        """, (pid,)).fetchall()

        if len(claims) < 2:
            continue

        # Assign stage values to claims via keyword matching
        staged_claims = []
        for c in claims:
            text = (c['claim_text'] or '').lower()
            stage_val = None
            for keyword, sv in [
                ('cancelled', 'cancelled'), ('terminated', 'cancelled'),
                ('withdrawn', 'cancelled'), ('on hold', 'on_hold'),
                ('paused', 'on_hold'),
                ('operational', 'operational'), ('operating', 'operational'),
                ('commissioning', 'commissioning'),
                ('notice to proceed', 'construction'),
                ('under construction', 'construction'),
                ('construction', 'construction'), ('permitted', 'permitted'),
                ('fid', 'fid'), ('financed', 'financed'),
                ('awarded', 'awarded'), ('announced', 'announced'),
            ]:
                if keyword in text:
                    stage_val = sv
                    break
            if stage_val:
                staged_claims.append({
                    'stage': stage_val,
                    'date': c['document_date'],
                    'text': (c['claim_text'] or '')[:200],
                })

        if len(staged_claims) < 2:
            continue

        # Find highest-stage claim and most-recent claim
        forward_claims = [c for c in staged_claims if c['stage'] in _STAGE_ORDER]
        if not forward_claims:
            continue

        highest_stage = max(forward_claims,
                           key=lambda x: _STAGE_ORDER.index(x['stage']))
        most_recent = max(staged_claims, key=lambda x: x['date'] or '')

        # Only LLM-check if they differ
        if highest_stage['text'] == most_recent['text']:
            continue

        prompt = _STALE_CHECK_PROMPT.format(
            date_a=highest_stage['date'],
            text_a=highest_stage['text'],
            date_b=most_recent['date'],
            text_b=most_recent['text'],
        )

        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.0,
                max_tokens=100,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw = re.sub(r'\s*```$',          '', raw, flags=re.DOTALL)
            result = json.loads(raw)

            relationship = result.get('relationship', '').upper()
            explanation = result.get('explanation', '')

            if relationship in ('SUPERSEDES', 'CONTRADICTS'):
                # Read current flags
                row = db.execute(
                    "SELECT evidence_gap_flags_json FROM unified_projects WHERE project_id=?",
                    (pid,)
                ).fetchone()
                flags = json.loads(row['evidence_gap_flags_json'] or '[]') if row else []

                # Add stale flag if not already present
                if not any(f.get('type') == 'stale_contradiction' for f in flags):
                    flags.append({
                        'type': 'stale_contradiction',
                        'relationship': relationship,
                        'explanation': explanation,
                        'highest_stage': highest_stage['stage'],
                        'highest_date': highest_stage['date'],
                        'recent_date': most_recent['date'],
                        'detected_at': now_iso(),
                    })
                    db.execute("""
                        UPDATE unified_projects
                        SET evidence_gap_flags_json=?, updated_at=?
                        WHERE project_id=?
                    """, (json.dumps(flags), now_iso(), pid))
                    flagged += 1

                    proj_row = db.execute(
                        "SELECT project_name FROM unified_projects WHERE project_id=?",
                        (pid,)
                    ).fetchone()
                    name = proj_row['project_name'] if proj_row else pid
                    print(f'    {name:<40} {relationship}: {explanation[:60]}')

        except Exception as e:
            print(f'    {pid[:30]:<30} → ERROR: {e}')

        time.sleep(GROQ_SLEEP)

    db.commit()
    print(f'  Flagged {flagged} projects with stale contradictions')


# ── Phase: LLM technology labeling ───────────────────────────────────────────

_TECH_LABEL_PROMPT = """Read the following text that describes a project's technology.
Return a short technology label (1-4 words) that captures the core technology.

Examples of good labels: "hydrogen production", "carbon capture and storage",
"hydrogen fuel cell", "SMR nuclear", "hydrogen storage", "hydrogen engine",
"ammonia production", "direct air capture", "CO2 sequestration", "hydrogen electrolyzer",
"green hydrogen", "blue hydrogen", "hydrogen sensor", "solid oxide fuel cell"

Project: {project_name}
Text: "{tech_text}"

Return JSON only:
{{"label": "<short technology label>"}}"""


def phase_label_technologies(db: sqlite3.Connection) -> None:
    """Use LLM to infer a short technology_label from verbose technology text."""
    print('\n── Label technologies (LLM) ────────────────────────────────────')

    rows = db.execute("""
        SELECT project_id, project_name, technology
        FROM unified_projects
        WHERE technology IS NOT NULL AND technology != ''
          AND (technology_label IS NULL OR technology_label = '')
    """).fetchall()

    if not rows:
        print('  Nothing to label — all technology fields already have labels.')
        return

    print(f'  {len(rows)} projects need technology labels')

    if DRY_RUN:
        for r in rows:
            print(f'    {r["project_name"]:<40} tech={r["technology"][:60]}...')
        return

    groq_client = Groq(api_key=_GROQ_KEY) if _GROQ_KEY else None
    if not groq_client:
        print('  ERROR: No GROQ_API_KEY — cannot label technologies.')
        return

    labeled = 0
    for r in rows:
        tech_text = r['technology'][:500]
        prompt = _TECH_LABEL_PROMPT.format(
            project_name=r['project_name'],
            tech_text=tech_text,
        )
        try:
            resp = groq_client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{'role': 'user', 'content': prompt}],
                temperature=0.0,
                max_tokens=60,
            )
            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw = re.sub(r'\s*```$',          '', raw, flags=re.DOTALL)
            result = json.loads(raw)
            label = result.get('label', '').strip()
            if label:
                db.execute(
                    "UPDATE unified_projects SET technology_label=? WHERE project_id=?",
                    (label, r['project_id']),
                )
                labeled += 1
                print(f'    {r["project_name"]:<40} → {label}')
            else:
                print(f'    {r["project_name"]:<40} → (empty response)')
        except Exception as e:
            print(f'    {r["project_name"]:<40} → ERROR: {e}')

        time.sleep(GROQ_SLEEP)

    db.commit()
    print(f'  Labeled {labeled}/{len(rows)} projects')


# ── Phase 3: Reconcile with old unified_projects ─────────────────────────────
def reconcile_old_projects(db: sqlite3.Connection,
                            intel: sqlite3.Connection | None = None) -> None:
    """
    Migration from core_database.db migration is complete.
    All unified_projects now live in core_database.db.
    This function is retained for the --reconcile flag but is a no-op.
    """
    print('\n── Reconcile: migration complete ────────────────────────────────')
    print('  core_database.db is the single source of truth. Nothing to import.')
    print('  Nothing to do.')



def print_stats(db: sqlite3.Connection) -> None:
    print('\n=== BOOTSTRAP — CURRENT STATE ===')

    try:
        n = db.execute("SELECT COUNT(*) FROM company_classifications").fetchone()[0]
        print(f'\n  company_classifications: {n}')
        for r in db.execute("""
            SELECT classification, COUNT(*) n
            FROM company_classifications
            GROUP BY classification ORDER BY n DESC
        """):
            print(f'    {r[0]:<25} {r[1]}')
    except:
        print('  company_classifications: not yet created')

    try:
        n = db.execute("SELECT COUNT(*) FROM unified_projects").fetchone()[0]
        print(f'\n  unified_projects: {n}')
        for r in db.execute("""
            SELECT stage, COUNT(*) n FROM unified_projects
            GROUP BY stage ORDER BY n DESC
        """):
            print(f'    {r[0] or "NULL":<20} {r[1]}')

        for r in db.execute("""
            SELECT bootstrap_source, COUNT(*) n FROM unified_projects
            GROUP BY bootstrap_source ORDER BY n DESC
        """):
            print(f'    source={r[0]:<25} {r[1]}')

        # Field fill rates
        print('\n  Field fill rates in unified_projects:')
        total = n
        for col in ('city', 'state', 'technology', 'technology_label',
                     'capacity_raw', 'capacity_mtpa_h2', 'stage',
                     'epc_contractor', 'fid_date', 'cod_date',
                     'construction_start'):
            try:
                filled = db.execute(
                    f"SELECT COUNT(*) FROM unified_projects "
                    f"WHERE [{col}] IS NOT NULL AND [{col}] != '' AND [{col}] != 'unknown'"
                ).fetchone()[0]
                bar = '█' * int(20 * filled / total) if total else ''
                print(f'    {col:<22} {filled:>3}/{total}  ({100*filled/total:4.0f}%)  {bar}')
            except sqlite3.OperationalError:
                pass

        # Quarantine stats
        try:
            q = db.execute("SELECT COUNT(*) FROM unified_projects WHERE quarantined = 1").fetchone()[0]
            print(f'\n  Quarantined: {q}')
            if q > 0:
                for r in db.execute("""
                    SELECT project_name, quarantine_reason FROM unified_projects
                    WHERE quarantined = 1 ORDER BY project_name
                """):
                    print(f'    {r[0]:<40} {(r[1] or "")[:60]}')
        except sqlite3.OperationalError:
            pass

        # Country breakdown
        try:
            print('\n  Country breakdown:')
            for r in db.execute("""
                SELECT COALESCE(country, 'US') AS c, COUNT(*) n
                FROM unified_projects GROUP BY c ORDER BY n DESC
            """):
                print(f'    {r[0]:<5} {r[1]}')
        except sqlite3.OperationalError:
            pass

    except:
        print('  unified_projects: not yet created')


# ── Entry point ───────────────────────────────────────────────────────────────


def run() -> None:
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row


    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    print(f"{'DRY RUN — no writes' if DRY_RUN else 'RUNNING BOOTSTRAP'}")
    migrate_schema(db)  # idempotent — always run to ensure columns exist

    run_all = not any([CLASSIFY_ONLY, REGISTER_ONLY, ENRICH_ONLY,
                       ENRICH_STAGES, APPLY_SIGNALS, LABEL_TECH,
                       VALIDATE_ONLY, AUDIT_ONLY, RECONCILE_ONLY])

    if run_all or CLASSIFY_ONLY:
        classify_companies(db)

    if run_all or REGISTER_ONLY:
        phase_register(db)

    if run_all or ENRICH_ONLY:
        phase_enrich(db)

    if run_all or ENRICH_STAGES:
        phase_enrich_stages(db)

    if run_all or APPLY_SIGNALS:
        phase_apply_signals(db)
        phase_enrich_from_extracted(db)
        phase_enrich_from_claims(db)
        phase_enrich_from_epa(db)
        phase_enrich_technology_capacity(db)
        phase_classify_technology(db)
        phase_refine_stages(db)
        phase_detect_stale_claims(db)

    if run_all or VALIDATE_ONLY:
        phase_validate(db)

    if run_all or LABEL_TECH:
        phase_label_technologies(db)

    if run_all or AUDIT_ONLY:
        phase_audit(db)

    if run_all or RECONCILE_ONLY:
        reconcile_old_projects(db)

    print_stats(db)

    if run_all or REGISTER_ONLY:
        print('\n  ⚠  Run connect.py, then:')
        print('     python bootstrap_projects.py --enrich')
        print('     python bootstrap_projects.py --enrich-stages')

    db.close()


if __name__ == '__main__':
    run()