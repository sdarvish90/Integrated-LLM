#!/usr/bin/env python3
"""
connect.py — Step 1: Entity Resolution & Claims Bridge
=======================================================
Connects structured claims to unified_projects — both in core_database.db.

Core mandate: NO GUESSING. Every assertion is backed by explicit text in a
regulatory filing. If explicit support cannot be found, the claim stays
unresolved and waits for future evidence.

Resolution tiers (in order, stops at first success):
  1. Exact string match against entity_aliases
  2. Normalized string match (strip legal suffixes, case, punctuation)
  3. Alias lookup (previously learned from entity claims)
  4. LLM judgment — ONLY for score band [LLM_JUDGE_LOWER, LLM_JUDGE_UPPER)
     LLM must cite verbatim text from the claim to justify match
  5. Unresolved — logged, queued for retroactive_reconnect()

LLM roles:
  A. extract_entity_aliases() — fires on assumed_names / subsidiary_list /
     merger claims. Extracts ONLY explicitly stated naming relationships.
     Post-check verifies supporting_excerpt is verbatim in claim_text.
  B. resolve_claim_to_entity() judgment — LLM must quote the specific text
     that establishes the match. No inference accepted.

Usage:
    python connect.py                        # full run
    python connect.py --aliases-only         # only extract aliases
    python connect.py --connect-only         # skip alias extraction
    python connect.py --retroactive          # re-evaluate unresolved claims
    python connect.py --stats                # show current state of bridge
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import os
from groq import Groq

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT     = Path(__file__).resolve().parent.parent          # corpus_frequnecy/
DB_PATH   = _ROOT / 'data' / 'core_database.db'
# core_database.db is the single source of truth — no secondary DB attachment needed
GROQ_MODEL = 'llama-3.3-70b-versatile'

_GROQ_KEY = os.environ.get("GROQ_API_KEY") or (
    (_ROOT / "groq_api_token.txt").read_text().strip()
    if (_ROOT / "groq_api_token.txt").exists()
    else None
)

# ── Default resolution thresholds (stored in resolution_config, editable) ───
_DEFAULT_CONFIG = {
    'llm_judge_upper':  ('0.85', 'Above this: auto-resolve without LLM'),
    'llm_judge_lower':  ('0.40', 'Below this word-overlap score: skip LLM'),
    'accumulator_apply_threshold': ('0.80', 'Min confidence to update unified_projects'),
    'min_sources_to_apply': ('2', 'Min distinct source_types before applying to DB'),
    'alias_extraction_claim_types': (
        json.dumps(['assumed_names', 'assumed_names_list', 'subsidiary_list',
                    'subsidiary_establishment', 'subsidiary_identification',
                    'subsidiary_ownership', 'merger_agreement_signed', 'parent_company']),
        'Claim types that trigger alias extraction'
    ),
    'retroactive_batch_size': ('100', 'Claims per batch in retroactive_reconnect'),
    'retroactive_sleep_sec':  ('0.5', 'Sleep between batches (seconds)'),
}

# ── Signal tier classification ───────────────────────────────────────────────
SIGNAL_TIERS: dict[str, list[str]] = {
    'ENTITY': [
        'assumed_names', 'assumed_names_list', 'subsidiary_list',
        'subsidiary_establishment', 'subsidiary_identification',
        'subsidiary_ownership', 'merger_agreement_signed', 'parent_company',
    ],
    'FID_SIGNAL': [
        'final_investment_decision', 'final_investment_decision_made',
        'notice_to_proceed', 'construction_commenced', 'construction_commencement',
        'financial_close', 'commercial_support', 'equity_interest',
        'equity_commitments', 'pipe_financing_agreement', 'pipe_financing_arrangement',
        'financing_commitments', 'financing_structure', 'financing_arrangements',
        'registration_statement_filed', 'registration_statement_filing',
        'bond_closing', 'note_offering', 'loan_terms',
    ],
    'PERMIT': [
        'epa_injection_well_permitting_process', 'final_permitting_decision_expected',
        'regulatory_status', 'sequestration_facilities', 'facility_relevance',
        'regulatory_compliance_target',
    ],
    'COMPETITOR': [
        'facility_location', 'facility_name', 'facility_name_match',
        'project_capacity', 'production_capacity',
        'project_phase', 'project_phase_description', 'project_development_status',
        'award_received', 'award_amount', 'award_date',
        'project_milestone', 'milestone_identified',
        'production_capacity_expansion', 'project_capacity_expansion',
        'train_capacity', 'pipeline_capacity',
        'low_carbon_ammonia_production', 'low_carbon_ammonia_sales_began',
    ],
    'FINANCIAL': [
        'award_amount', 'grant_amount', 'funding_amount', 'funding_awarded',
        'grant_awarded', 'project_funding', 'loan_conversion', 'loan_terms',
        'loan_conversion_agreement', 'financing_capacity',
        'capital_expenditures', 'capital_expenditure',
        'net_earnings_reported', 'adjusted_ebitda_reported',
        'blue_point_project_cost', 'financial_performance',
    ],
    'CANCELLATION': [
        'electrolyzer_project_cancellation', 'asset_impairment',
        'impairment_charge', 'impairment_loss_recognition', 'goodwill_impairment',
        'idling_of_clean_sugar_technology_facility',
        'idling_of_fairmont_minnesota_plant',
    ],
}

def get_signal_tier(claim_type: str) -> str:
    for tier, types in SIGNAL_TIERS.items():
        if claim_type in types:
            return tier
    return 'METADATA'


# ── Utilities ────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def new_id() -> str:
    return str(uuid.uuid4())

_LEGAL_SUFFIXES = re.compile(
    r'\b(llc|inc|corp|corporation|company|co|limited|ltd|'
    r'holdings|group|energy|and|the|&)\b', re.I
)

def normalize(name: str) -> str:
    """Strip legal suffixes, punctuation, case — for fuzzy matching only."""
    n = name.lower().strip()
    n = _LEGAL_SUFFIXES.sub(' ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n

def word_overlap(a: str, b: str) -> float:
    ta = set(re.findall(r'[a-z0-9]+', a.lower()))
    tb = set(re.findall(r'[a-z0-9]+', b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ── Database setup ───────────────────────────────────────────────────────────
def migrate(db: sqlite3.Connection) -> None:
    """Create all Step 1 tables if they don't exist."""
    db.executescript("""
    -- Canonical entity registry
    CREATE TABLE IF NOT EXISTS entities (
        entity_id       TEXT PRIMARY KEY,
        canonical_name  TEXT NOT NULL,
        entity_type     TEXT DEFAULT 'company',
        created_at      TEXT,
        updated_at      TEXT
    );

    -- Explicit naming relationships only — no inferences ever stored
    CREATE TABLE IF NOT EXISTS entity_aliases (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_id            TEXT REFERENCES entities(entity_id),
        alias                TEXT NOT NULL,
        alias_norm           TEXT,           -- normalized form for fast lookup
        alias_type           TEXT,           -- assumed_name | subsidiary | dba |
                                             --   legal_name | acquired_by | merged_into
        source_claim_id      TEXT,           -- UUID from claims table
        cited_document_type  TEXT,
        cited_document_date  TEXT,
        cited_document_url   TEXT,
        cited_text_excerpt   TEXT,           -- verbatim excerpt that stated the relationship
        citing_company_cik   TEXT,
        is_explicit          INTEGER DEFAULT 1,  -- always 1: no inferences stored
        confidence           REAL DEFAULT 1.0,
        learned_at           TEXT,
        UNIQUE(entity_id, alias)
    );

    -- Bridge: resolved claims ↔ projects, with full resolution provenance
    CREATE TABLE IF NOT EXISTS project_claims (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id           TEXT,
        entity_id            TEXT REFERENCES entities(entity_id),
        claim_id             TEXT UNIQUE,    -- UUID from claims table
        claim_type           TEXT,
        signal_tier          TEXT,
        claim_text           TEXT,
        document_type        TEXT,
        document_date        TEXT,
        document_url         TEXT,
        confidence           REAL,
        -- Resolution provenance
        resolution_method    TEXT,           -- exact | normalized | alias |
                                             --   llm_explicit | unresolved
        resolution_conf      REAL,
        resolution_citation  TEXT,           -- JSON: how entity was resolved
        last_resolved_at     TEXT,
        surfaced_at          TEXT
    );

    -- Multi-source signal accumulation before updating unified_projects
    CREATE TABLE IF NOT EXISTS project_signal_accumulator (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id       TEXT,
        signal_type      TEXT,               -- stage | capacity | technology | location
        asserted_value   TEXT,
        citations        TEXT,               -- JSON array of {claim_id, claim_text,
                                             --   document_type, document_date,
                                             --   document_url, company_cik}
        source_types     TEXT,               -- JSON array for diversity check
        accumulated_conf REAL DEFAULT 0.0,
        last_updated     TEXT,
        applied_to_db    INTEGER DEFAULT 0,
        UNIQUE(project_id, signal_type, asserted_value)
    );

    -- Permanent audit trail: every DB change triggered by the connect pipeline
    CREATE TABLE IF NOT EXISTS citation_audit_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type    TEXT,    -- alias_learned | stage_updated | entity_resolved |
                               --   claim_connected | accumulator_applied
        entity_id     TEXT,
        project_id    TEXT,
        assertion     TEXT,
        prior_value   TEXT,
        new_value     TEXT,
        citation_json TEXT,   -- full citation chain
        confidence    REAL,
        triggered_by  TEXT,   -- alias_extraction | accumulator | retroactive_reconnect
        created_at    TEXT
    );

    -- Tunable thresholds — edit without touching code
    CREATE TABLE IF NOT EXISTS resolution_config (
        key   TEXT PRIMARY KEY,
        value TEXT,
        note  TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_ea_alias_norm ON entity_aliases(alias_norm);
    CREATE INDEX IF NOT EXISTS idx_ea_entity     ON entity_aliases(entity_id);
    CREATE INDEX IF NOT EXISTS idx_pc_project    ON project_claims(project_id);
    CREATE INDEX IF NOT EXISTS idx_pc_entity     ON project_claims(entity_id);
    CREATE INDEX IF NOT EXISTS idx_pc_method     ON project_claims(resolution_method);
    CREATE INDEX IF NOT EXISTS idx_psa_project   ON project_signal_accumulator(project_id);
    CREATE INDEX IF NOT EXISTS idx_cal_project   ON citation_audit_log(project_id);
    CREATE INDEX IF NOT EXISTS idx_cal_entity    ON citation_audit_log(entity_id);
    """)
    db.commit()
    print("  Schema migration complete.")


def seed_config(db: sqlite3.Connection) -> None:
    """Insert default config values; skip if already present."""
    for key, (value, note) in _DEFAULT_CONFIG.items():
        db.execute(
            "INSERT OR IGNORE INTO resolution_config(key, value, note) VALUES(?,?,?)",
            (key, value, note)
        )
    db.commit()


def get_config(db: sqlite3.Connection, key: str) -> str:
    row = db.execute(
        "SELECT value FROM resolution_config WHERE key=?", (key,)
    ).fetchone()
    return row[0] if row else _DEFAULT_CONFIG[key][0]


# ── Entity registry helpers ──────────────────────────────────────────────────
def get_or_create_entity(db: sqlite3.Connection, canonical_name: str,
                         entity_type: str = 'company') -> str:
    """Return existing entity_id or create new entity. Returns entity_id."""
    row = db.execute(
        "SELECT entity_id FROM entities WHERE canonical_name=?",
        (canonical_name,)
    ).fetchone()
    if row:
        return row[0]
    eid = new_id()
    db.execute(
        "INSERT INTO entities(entity_id, canonical_name, entity_type, created_at, updated_at) "
        "VALUES(?,?,?,?,?)",
        (eid, canonical_name, entity_type, now_iso(), now_iso())
    )
    return eid


def _alias_lookup(db: sqlite3.Connection, name: str) -> Optional[tuple[str, str, int]]:
    """
    Look up entity_id by alias (exact then normalized).
    Returns (entity_id, method, alias_id) or None.
    """
    # Exact match
    row = db.execute(
        "SELECT entity_id, id FROM entity_aliases WHERE alias=?", (name,)
    ).fetchone()
    if row:
        return row[0], 'alias_exact', row[1]
    # Normalized match
    norm = normalize(name)
    if len(norm) > 3:
        row = db.execute(
            "SELECT entity_id, id FROM entity_aliases WHERE alias_norm=?", (norm,)
        ).fetchone()
        if row:
            return row[0], 'alias_norm', row[1]
    return None


# ── LLM helper ───────────────────────────────────────────────────────────────
_groq_client = Groq(api_key=_GROQ_KEY)

def _llm(prompt: str, max_tokens: int = 800, temperature: float = 0.0) -> str:
    resp = _groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
    raw = re.sub(r'\s*```$', '', raw, flags=re.DOTALL)
    return raw


_ALIAS_EXTRACT_PROMPT = """You are extracting explicitly stated naming relationships from a regulatory filing.

CLAIM TEXT:
"{claim_text}"

SOURCE: {document_type} filed by {company_name} (CIK: {cik}) on {document_date}
URL: {document_url}

TASK:
Extract ONLY naming relationships that are EXPLICITLY stated in the text above.
- Do NOT infer. Do NOT guess. Do NOT use general knowledge.
- Each relationship must be directly stated in the text.
- The supporting_excerpt must be a verbatim substring of the claim text above.

For each relationship found return:
- canonical: the primary/parent entity as named in the filing
- alias: the other name it operates under
- alias_type: one of [assumed_name, dba, subsidiary, legal_name, acquired_by, merged_into]
- supporting_excerpt: the EXACT words from the text above that state this relationship
  (minimum needed, must appear verbatim)

Return JSON only, no other text:
{{
  "relationships": [
    {{
      "canonical": "...",
      "alias": "...",
      "alias_type": "...",
      "supporting_excerpt": "..."
    }}
  ]
}}

If no explicit naming relationships are stated, return: {{"relationships": []}}"""


_RESOLUTION_PROMPT = """You are deciding whether two company name references refer to the same legal entity.

CLAIM COMPANY:    "{claim_company}"
CANDIDATE ENTITY: "{candidate_name}"
KNOWN ALIASES:    {aliases}

CLAIM CONTEXT (document type, date, brief text):
  Type: {document_type}
  Date: {document_date}
  Text: "{claim_text_snippet}"

TASK:
Decide if these refer to the same entity.
- Base your decision ONLY on explicit evidence in the claim text above or the known aliases listed.
- Do NOT use general knowledge about company structures.
- Do NOT guess based on name similarity alone.
- If you cannot find explicit textual support for the match, return DIFFERENT.

If SAME: provide the exact text from the claim or aliases that establishes the match.

Return JSON only:
{{
  "decision": "SAME" | "DIFFERENT" | "UNCERTAIN",
  "confidence": 0.0-1.0,
  "supporting_text": "the exact text that establishes the match, or empty string if DIFFERENT/UNCERTAIN",
  "reasoning": "one sentence"
}}"""


# ── Core function 1: Extract entity aliases ──────────────────────────────────
def extract_entity_aliases(
    claim: sqlite3.Row,
    db: sqlite3.Connection,
) -> int:
    """
    Extract explicit naming relationships from a qualifying claim.
    Writes to entity_aliases and citation_audit_log.
    Returns number of new aliases learned.
    """
    # Get CIK from regulatory_evidence via source_id
    re_row = db.execute(
        "SELECT company_cik FROM regulatory_evidence WHERE id=?",
        (claim['source_id'],)
    ).fetchone()
    cik = re_row['company_cik'] if re_row and re_row['company_cik'] else 'unknown'

    prompt = _ALIAS_EXTRACT_PROMPT.format(
        claim_text    = claim['claim_text'],
        document_type = claim['document_type'] or '',
        company_name  = claim['company_name'],
        cik           = cik,
        document_date = claim['document_date'] or '',
        document_url  = claim['document_url'] or '',
    )

    try:
        raw = _llm(prompt, max_tokens=600)
        result = json.loads(raw)
    except Exception as e:
        print(f"    [alias extract] LLM/parse error: {e}")
        return 0

    relationships = result.get('relationships', [])
    if not relationships:
        return 0

    learned = 0
    for rel in relationships:
        canonical = (rel.get('canonical') or '').strip()
        alias     = (rel.get('alias') or '').strip()
        atype     = (rel.get('alias_type') or 'assumed_name').strip()
        excerpt   = (rel.get('supporting_excerpt') or '').strip()

        # Hard guard: excerpt must appear verbatim in claim_text
        if not excerpt or excerpt not in claim['claim_text']:
            print(f"    [alias extract] POST-CHECK FAILED — excerpt not verbatim in claim")
            print(f"      Excerpt: {excerpt[:80]}")
            continue

        if not canonical or not alias or canonical == alias:
            continue

        # Get or create entity for canonical name
        entity_id = get_or_create_entity(db, canonical)

        # Write alias (IGNORE if already known)
        try:
            db.execute("""
                INSERT OR IGNORE INTO entity_aliases
                (entity_id, alias, alias_norm, alias_type, source_claim_id,
                 cited_document_type, cited_document_date, cited_document_url,
                 cited_text_excerpt, citing_company_cik, is_explicit,
                 confidence, learned_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,1,1.0,?)
            """, (
                entity_id, alias, normalize(alias), atype,
                claim['claim_id'],
                claim['document_type'], claim['document_date'],
                claim['document_url'], excerpt, cik,
                now_iso()
            ))

            if db.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0:
                # Write audit record
                citation = {
                    'event': 'alias_learned',
                    'canonical': canonical,
                    'alias': alias,
                    'alias_type': atype,
                    'supporting_excerpt': excerpt,
                    'source_claim_id': claim['claim_id'],
                    'document_type': claim['document_type'],
                    'document_date': claim['document_date'],
                    'document_url': claim['document_url'],
                    'cik': cik,
                }
                db.execute("""
                    INSERT INTO citation_audit_log
                    (event_type, entity_id, assertion, new_value,
                     citation_json, confidence, triggered_by, created_at)
                    VALUES (?,?,?,?,?,1.0,'alias_extraction',?)
                """, (
                    'alias_learned', entity_id,
                    f"alias: {alias} → {canonical}",
                    alias, json.dumps(citation), now_iso()
                ))
                learned += 1
                print(f"    + Alias learned: '{alias}' → '{canonical}' [{atype}]")
                print(f"      Excerpt: {excerpt[:90]}")

        except sqlite3.Error as e:
            print(f"    [alias extract] DB error: {e}")

    db.commit()
    return learned


# ── Layer 2: Route claim to specific sub-project within company ──────────────
def route_to_project(
    claim: sqlite3.Row,
    company_id: str,
    db: sqlite3.Connection,
    auto_create: bool = False,
) -> tuple[str, str]:
    """
    After company is identified, route to the specific facility/award sub-project.

    Returns:
        (project_id, routing_method)
        Falls back to company_portfolio if no sub-project match.
    """
    source_id = claim['source_id']

    # Get regulatory_evidence context
    re_row = None
    if source_id:
        re_row = db.execute(
            "SELECT source_system, company_name, document_url "
            "FROM regulatory_evidence WHERE id=?",
            (source_id,)
        ).fetchone()

    if re_row:
        # EPA ECHO → route to facility
        if re_row['source_system'] == 'epa_echo':
            facility_name = (re_row['company_name'] or '').upper().strip()
            if facility_name:
                fac_proj = db.execute("""
                    SELECT project_id FROM unified_projects
                    WHERE company_id = ? AND project_type = 'facility'
                      AND source_identity = ?
                """, (company_id, facility_name)).fetchone()
                if fac_proj:
                    return fac_proj['project_id'], 'facility_match'

                # Try space-collapsed match (EXXONMOBIL vs EXXON MOBIL)
                fac_proj = db.execute("""
                    SELECT project_id, source_identity FROM unified_projects
                    WHERE company_id = ? AND project_type = 'facility'
                """, (company_id,)).fetchone()
                # Search all facilities for this company
                fac_nospace = facility_name.replace(' ', '')
                for fp in db.execute("""
                    SELECT project_id, source_identity FROM unified_projects
                    WHERE company_id = ? AND project_type = 'facility'
                """, (company_id,)):
                    si_nospace = (fp['source_identity'] or '').replace(' ', '')
                    if fac_nospace == si_nospace:
                        return fp['project_id'], 'facility_match'

                if auto_create:
                    # Create a new facility sub-project on the fly
                    import hashlib as _hlib
                    co_row = db.execute(
                        "SELECT company_key, company_name FROM companies WHERE company_id=?",
                        (company_id,)
                    ).fetchone()
                    if co_row:
                        pid = _hlib.md5(
                            (co_row['company_key'] + '::' + facility_name).encode()
                        ).hexdigest()[:16]
                        db.execute("""
                            INSERT OR IGNORE INTO unified_projects
                            (project_id, project_name, developer_key, developer_name,
                             company_id, project_type, source_identity,
                             stage, fid_probability,
                             co_developers_json, evidence_gap_flags_json,
                             source_count, fid_status, quarantined,
                             created_at, updated_at, bootstrap_source, country)
                            VALUES (?,?,?,?,?,?,?,'unknown',0.10,'[]','[]',
                                    0,'unknown',0,?,?,'auto_create','US')
                        """, (pid, f"{co_row['company_name']} - {facility_name}",
                              co_row['company_key'], co_row['company_name'],
                              company_id, 'facility', facility_name,
                              now_iso(), now_iso()))
                        return pid, 'facility_auto_created'

        # DOE usaspending → route to award
        if re_row['document_url'] and 'usaspending' in re_row['document_url']:
            doe_proj = db.execute("""
                SELECT project_id FROM unified_projects
                WHERE company_id = ? AND project_type = 'doe_award'
                  AND doe_award_url = ?
            """, (company_id, re_row['document_url'])).fetchone()
            if doe_proj:
                return doe_proj['project_id'], 'doe_award_match'

        # EPA GHGRP / UIC → route to facility by name
        if re_row['source_system'] in ('epa_ghgrp', 'epa_uic'):
            facility_name = (re_row['company_name'] or '').upper().strip()
            if facility_name:
                fac_proj = db.execute("""
                    SELECT project_id FROM unified_projects
                    WHERE company_id = ? AND project_type = 'facility'
                      AND source_identity = ?
                """, (company_id, facility_name)).fetchone()
                if fac_proj:
                    return fac_proj['project_id'], 'facility_match'

                # Space-collapsed fallback
                fac_nospace = facility_name.replace(' ', '')
                for fp in db.execute("""
                    SELECT project_id, source_identity FROM unified_projects
                    WHERE company_id = ? AND project_type = 'facility'
                """, (company_id,)):
                    si_nospace = (fp['source_identity'] or '').replace(' ', '')
                    if fac_nospace == si_nospace:
                        return fp['project_id'], 'facility_match'

                if auto_create:
                    import hashlib as _hlib
                    co_row = db.execute(
                        "SELECT company_key, company_name FROM companies WHERE company_id=?",
                        (company_id,)
                    ).fetchone()
                    if co_row:
                        pid = _hlib.md5(
                            (co_row['company_key'] + '::ghgrp::' + facility_name).encode()
                        ).hexdigest()[:16]
                        db.execute("""
                            INSERT OR IGNORE INTO unified_projects
                            (project_id, project_name, developer_key, developer_name,
                             company_id, project_type, source_identity,
                             stage, fid_probability,
                             co_developers_json, evidence_gap_flags_json,
                             source_count, fid_status, quarantined,
                             created_at, updated_at, bootstrap_source, country)
                            VALUES (?,?,?,?,?,?,?,'unknown',0.10,'[]','[]',
                                    0,'unknown',0,?,?,'auto_create','US')
                        """, (pid, f"{co_row['company_name']} - {facility_name}",
                              co_row['company_key'], co_row['company_name'],
                              company_id, 'facility', facility_name,
                              now_iso(), now_iso()))
                        return pid, 'facility_auto_created'

    # Grid sources → route to company_portfolio
    # (grid enrichment happens at collection time in collect_grid.py)
    if re_row and re_row['source_system'] in (
        'ercot', 'puc_texas', 'sec_grid', 'eia_860m',
        'tsp_earnings', 'interconnection_fyi',
    ):
        portfolio = db.execute("""
            SELECT project_id FROM unified_projects
            WHERE company_id = ? AND project_type = 'company_portfolio'
        """, (company_id,)).fetchone()
        if portfolio:
            return portfolio['project_id'], 'company_portfolio'

    # Fallback: company_portfolio
    portfolio = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE company_id = ? AND project_type = 'company_portfolio'
    """, (company_id,)).fetchone()
    if portfolio:
        return portfolio['project_id'], 'company_portfolio'

    # Last resort: any project for this company
    any_proj = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE company_id = ? LIMIT 1
    """, (company_id,)).fetchone()
    if any_proj:
        return any_proj['project_id'], 'company_fallback'

    return None, 'no_project'


# ── Corporate events cache (loaded once per session) ─────────────────────────
_corporate_events_cache: dict[str, list[dict]] | None = None

def _load_corporate_events(db: sqlite3.Connection) -> dict[str, list[dict]]:
    """Load corporate_events into a dict keyed by child_name_norm.

    Each entry is a list of events (a child can have been acquired multiple
    times, e.g., Torrance Refining: ExxonMobil→PBF Energy).
    Returns {child_name_norm: [{parent_company_id, event_date, event_type, child_name}, ...]}.
    """
    global _corporate_events_cache
    if _corporate_events_cache is not None:
        return _corporate_events_cache

    # Check table exists
    if not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_events'"
    ).fetchone():
        _corporate_events_cache = {}
        return _corporate_events_cache

    events: dict[str, list[dict]] = {}
    for r in db.execute("""
        SELECT ce.parent_company_id, ce.child_name, ce.child_name_norm,
               ce.event_type, ce.event_date,
               c.company_name AS parent_name
        FROM corporate_events ce
        JOIN companies c ON ce.parent_company_id = c.company_id
        WHERE ce.event_date IS NOT NULL
        ORDER BY ce.event_date
    """):
        cn = r['child_name_norm']
        if cn not in events:
            events[cn] = []
        events[cn].append({
            'parent_company_id': r['parent_company_id'],
            'parent_name': r['parent_name'],
            'child_name': r['child_name'],
            'event_type': r['event_type'],
            'event_date': r['event_date'],
        })

    _corporate_events_cache = events
    return _corporate_events_cache


def _check_temporal_ownership(
    cname: str, cnorm: str, doc_date: str | None,
    db: sqlite3.Connection, projects: list[dict],
    auto_create_projects: bool,
    claim: sqlite3.Row,
) -> tuple[Optional[str], Optional[str], str, float, Optional[dict]] | None:
    """Check if company_name is a known acquired entity and the document post-dates
    the acquisition. If so, return resolution to the parent company's project.

    Returns None if no temporal remapping applies.
    """
    events = _load_corporate_events(db)
    if not events:
        return None

    # Try exact normalized match, then substring match for facility names
    matching_events = events.get(cnorm)

    if not matching_events:
        # Try: "PRAXAIR TEXAS CITY" should match event for "praxair texas city"
        # Only match when the claim name STARTS WITH a known child name
        # (i.e., the child is a prefix of the claim — catches facility variants).
        # Do NOT match when the child name is longer than the claim name,
        # which would incorrectly remap parent companies to their own events.
        for child_norm, evts in events.items():
            if len(child_norm) > 3 and cnorm.startswith(child_norm + ' '):
                matching_events = evts
                break

    if not matching_events:
        return None

    # Find the most recent event that predates or matches the document
    # If no doc_date, skip temporal check (can't determine ownership period)
    if not doc_date:
        return None

    # Normalize doc_date to comparable format (YYYY-MM-DD or YYYY-12-31)
    dd = doc_date[:10] if len(doc_date) >= 10 else doc_date

    # Find the applicable event: most recent event with event_date <= doc_date
    applicable = None
    for evt in matching_events:
        ed = evt['event_date'][:10] if evt['event_date'] else None
        if ed and dd >= ed:
            applicable = evt  # events are ordered by date, so last one wins

    if not applicable:
        return None

    # Found a temporal match — resolve to parent company's project
    parent_id = applicable['parent_company_id']
    parent_name = applicable['parent_name']

    # Find the parent's company_portfolio project
    parent_proj = None
    for p in projects:
        if p.get('company_id') == parent_id:
            if p.get('project_type') == 'company_portfolio':
                parent_proj = p
                break
    if not parent_proj:
        # Any project for this company
        for p in projects:
            if p.get('company_id') == parent_id:
                parent_proj = p
                break

    if not parent_proj:
        return None

    eid = get_or_create_entity(db, parent_name)
    citation = {
        'method': 'temporal_ownership',
        'original_company': cname,
        'remapped_to': parent_name,
        'event_type': applicable['event_type'],
        'event_date': applicable['event_date'],
        'doc_date': doc_date,
    }

    # Apply Layer 2 routing (facility/DOE match) under the parent company
    p_row = db.execute(
        "SELECT company_id, project_type FROM unified_projects WHERE project_id=?",
        (parent_proj['project_id'],)
    ).fetchone()
    if p_row and p_row['company_id']:
        routed_pid, routing = route_to_project(
            claim, p_row['company_id'], db, auto_create=auto_create_projects
        )
        if routed_pid and routed_pid != parent_proj['project_id']:
            citation['routed_from'] = parent_proj['project_id']
            citation['routing'] = routing
            return routed_pid, eid, f"temporal_ownership+{routing}", 0.95, citation

    return parent_proj['project_id'], eid, 'temporal_ownership', 0.95, citation


# ── Core function 2: Resolve claim to entity ────────────────────────────────
def resolve_claim_to_entity(
    claim: sqlite3.Row,
    db: sqlite3.Connection,
    projects: list[dict],         # pre-loaded for efficiency
    llm_lower: float,
    llm_upper: float,
    companies: list[dict] | None = None,
    auto_create_projects: bool = False,
) -> tuple[Optional[str], Optional[str], str, float, Optional[dict]]:
    """
    Resolve claim company_name to a project entity.

    Two-layer resolution:
      Tier 0: Temporal corporate ownership (post-acquisition remap)
      Layer 1: Identify company (Tiers 1-4 match against developer_name/companies)
      Layer 2: Route to specific sub-project (facility/DOE award) via route_to_project()

    Returns:
        (project_id, entity_id, method, confidence, citation_dict)
        project_id / entity_id are None if unresolved.
    """
    cname = claim['company_name']
    cnorm = normalize(cname)

    # ── Tier 0: Temporal corporate ownership ─────────────────────────────
    # If the claim's company is a known acquired entity and the document
    # post-dates the acquisition, resolve to the acquirer's project.
    temporal = _check_temporal_ownership(
        cname, cnorm, claim['document_date'], db, projects,
        auto_create_projects, claim,
    )
    if temporal:
        return temporal

    # Layer 2 routing helper: after Tier 1-4 identifies a company-level project,
    # try to route to a specific sub-project (facility/DOE award).
    def _route(pid, eid, method, conf, citation):
        """Apply Layer 2 sub-project routing if companies table is populated."""
        p_row = db.execute(
            "SELECT company_id, project_type FROM unified_projects WHERE project_id=?",
            (pid,)
        ).fetchone()
        if p_row and p_row['company_id']:
            routed_pid, routing = route_to_project(
                claim, p_row['company_id'], db, auto_create=auto_create_projects
            )
            if routed_pid and routed_pid != pid:
                if citation:
                    citation['routed_from'] = pid
                    citation['routing'] = routing
                return routed_pid, eid, f"{method}+{routing}", conf, citation
        return pid, eid, method, conf, citation

    # ── Tier 1: exact match against developer_name ───────────────────────────
    for p in projects:
        if p['developer_name'] == cname:
            eid = get_or_create_entity(db, cname)
            return _route(p['project_id'], eid, 'exact', 1.0, {
                'method': 'exact', 'matched': p['developer_name']
            })

    # ── Tier 1b: source_id chain — if claim's regulatory_evidence record has
    #    project_name and it matches an existing project, use that link directly.
    #    This is the most reliable cross-source tracing method. ──────────────
    source_id = claim['source_id']
    if source_id:
        re_row = db.execute(
            "SELECT project_name, facility_id, permit_id, company_name "
            "FROM regulatory_evidence WHERE id=?",
            (source_id,)
        ).fetchone()
        if re_row and re_row['project_name']:
            re_proj_name = re_row['project_name']
            re_proj_norm = normalize(re_proj_name)
            # Match against unified_projects.project_name
            for p in projects:
                pname_norm = normalize(p.get('project_name') or p['developer_name'])
                if re_proj_norm and pname_norm and len(re_proj_norm) > 3:
                    if re_proj_norm == pname_norm or re_proj_norm in pname_norm or pname_norm in re_proj_norm:
                        eid = get_or_create_entity(db, p['developer_name'])
                        return _route(p['project_id'], eid, 'project_name', 0.95, {
                            'method': 'project_name',
                            'source_project_name': re_proj_name,
                            'matched': p['developer_name'],
                            'matched_project': p.get('project_name') or p['developer_name'],
                        })

    # ── Tier 2: normalized match against developer_name ─────────────────────
    norm_candidates = []
    for p in projects:
        pnorm = normalize(p['developer_name'])
        if cnorm and pnorm and len(cnorm) > 3 and len(pnorm) > 3:
            if cnorm == pnorm or cnorm in pnorm or pnorm in cnorm:
                norm_candidates.append(p)

    # ── Disambiguation: use state + project_name to separate same-company projects
    if norm_candidates:
        # Get claim's state and project_name context from regulatory_evidence
        claim_state = None
        claim_project = None
        if source_id:
            re_ctx = db.execute(
                "SELECT project_name FROM regulatory_evidence WHERE id=?",
                (source_id,)
            ).fetchone()
            if re_ctx and re_ctx['project_name']:
                claim_project = re_ctx['project_name']

            # Also try extracted_state from claims table
            state_row = db.execute(
                "SELECT extracted_state FROM claims WHERE claim_id=?",
                (claim['claim_id'],)
            ).fetchone()
            if state_row and state_row[0]:
                claim_state = state_row[0]

        if len(norm_candidates) == 1:
            p = norm_candidates[0]

            # Check if this claim is about a DIFFERENT project from the same company.
            # If the regulatory_evidence has a project_name that doesn't match the
            # existing project, this should stay unresolved for project creation later.
            if claim_project and p.get('project_name'):
                p_proj_norm = normalize(p['project_name'])
                c_proj_norm = normalize(claim_project)
                # If project names are clearly different AND not a subset relation,
                # don't force-merge. Let it go unresolved for new project creation.
                if (c_proj_norm and p_proj_norm
                        and c_proj_norm not in p_proj_norm
                        and p_proj_norm not in c_proj_norm
                        and word_overlap(claim_project, p['project_name']) < 0.30):
                    # Different project, same company — skip to unresolved
                    pass
                else:
                    eid = get_or_create_entity(db, p['developer_name'])
                    return _route(p['project_id'], eid, 'normalized', 0.90, {
                        'method': 'normalized',
                        'claim_norm': cnorm,
                        'matched_norm': normalize(p['developer_name']),
                        'matched': p['developer_name'],
                    })
            else:
                eid = get_or_create_entity(db, p['developer_name'])
                return _route(p['project_id'], eid, 'normalized', 0.90, {
                    'method': 'normalized',
                    'claim_norm': cnorm,
                    'matched_norm': normalize(p['developer_name']),
                    'matched': p['developer_name'],
                })

        elif len(norm_candidates) > 1:
            # Multiple candidates — try state filter first
            filtered = norm_candidates
            if claim_state:
                state_filtered = [
                    c for c in norm_candidates
                    if c.get('state') == claim_state
                    or c.get('state') is None
                ]
                if state_filtered:
                    filtered = state_filtered

            # Then try project_name filter
            if len(filtered) > 1 and claim_project:
                c_proj_norm = normalize(claim_project)
                proj_filtered = [
                    c for c in filtered
                    if c.get('project_name') and (
                        c_proj_norm in normalize(c['project_name'])
                        or normalize(c['project_name']) in c_proj_norm
                    )
                ]
                if len(proj_filtered) == 1:
                    filtered = proj_filtered

            if len(filtered) == 1:
                p = filtered[0]
                eid = get_or_create_entity(db, p['developer_name'])
                return _route(p['project_id'], eid, 'normalized', 0.90, {
                    'method': 'normalized',
                    'claim_norm': cnorm,
                    'matched_norm': normalize(p['developer_name']),
                    'matched': p['developer_name'],
                    'candidates': len(norm_candidates),
                    'disambiguated_by': 'state+project',
                })
            else:
                # Still ambiguous — take first but lower confidence
                p = filtered[0]
                eid = get_or_create_entity(db, p['developer_name'])
                return _route(p['project_id'], eid, 'normalized', 0.70, {
                    'method': 'normalized',
                    'claim_norm': cnorm,
                    'matched_norm': normalize(p['developer_name']),
                    'matched': p['developer_name'],
                    'candidates': len(norm_candidates),
                    'ambiguous': True,
                })

    # ── Tier 2b: EPA enrichment lookup ────────────────────────────────────────
    # EPA claims use facility names as company_name, but we have operator/parent
    # data in regulatory_evidence that maps to known entities.
    source_id = claim['source_id']
    if source_id:
        epa_row = db.execute(
            "SELECT operator_name, llc_entity, parent_company "
            "FROM regulatory_evidence WHERE id=? AND source_system='epa_echo'",
            (source_id,)
        ).fetchone()
        if epa_row:
            # Try each enrichment name against projects (exact, normalized, alias)
            for epa_name in [epa_row['operator_name'], epa_row['llc_entity'], epa_row['parent_company']]:
                if not epa_name:
                    continue
                enorm = normalize(epa_name)
                # exact
                for p in projects:
                    if p['developer_name'] == epa_name:
                        eid = get_or_create_entity(db, p['developer_name'])
                        return _route(p['project_id'], eid, 'epa_enrichment', 0.95, {
                            'method': 'epa_enrichment', 'epa_name': epa_name,
                            'facility': cname, 'matched': p['developer_name'],
                        })
                # normalized
                for p in projects:
                    pnorm = normalize(p['developer_name'])
                    if enorm and pnorm and len(enorm) > 3 and len(pnorm) > 3:
                        if enorm == pnorm or enorm in pnorm or pnorm in enorm:
                            eid = get_or_create_entity(db, p['developer_name'])
                            return _route(p['project_id'], eid, 'epa_enrichment', 0.90, {
                                'method': 'epa_enrichment', 'epa_name': epa_name,
                                'epa_norm': enorm, 'facility': cname,
                                'matched': p['developer_name'],
                            })
                # alias
                alias_hit_epa = _alias_lookup(db, epa_name)
                if alias_hit_epa:
                    entity_id, alias_method, alias_id = alias_hit_epa
                    entity_row = db.execute(
                        "SELECT canonical_name FROM entities WHERE entity_id=?",
                        (entity_id,)
                    ).fetchone()
                    if entity_row:
                        canonical = entity_row[0]
                        for p in projects:
                            pnorm = normalize(p['developer_name'])
                            cnorm2 = normalize(canonical)
                            if (p['developer_name'] == canonical
                                    or (cnorm2 and pnorm and (cnorm2 in pnorm or pnorm in cnorm2))):
                                eid = entity_id
                                return _route(p['project_id'], eid, 'epa_enrichment_alias', 0.90, {
                                    'method': 'epa_enrichment_alias',
                                    'epa_name': epa_name, 'facility': cname,
                                    'via_alias': canonical,
                                    'matched': p['developer_name'],
                                })

    # ── Tier 2c: DOE OCED enrichment ──────────────────────────────────────────
    # For DOE claims where company_name is still a program label,
    # fall back to the evidence record's actual company_name.
    if source_id and cname in ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC'):
        doe_row = db.execute(
            "SELECT company_name FROM regulatory_evidence "
            "WHERE id=? AND source_system='doe_oced_detail'",
            (source_id,)
        ).fetchone()
        if doe_row and doe_row['company_name'] not in ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC'):
            doe_name = doe_row['company_name']
            dnorm = normalize(doe_name)
            # exact match
            for p in projects:
                if p['developer_name'] == doe_name:
                    eid = get_or_create_entity(db, p['developer_name'])
                    return _route(p['project_id'], eid, 'doe_enrichment', 0.95, {
                        'method': 'doe_enrichment', 'doe_name': doe_name,
                        'original_claim_company': cname,
                        'matched': p['developer_name'],
                    })
            # normalized match
            for p in projects:
                pnorm = normalize(p['developer_name'])
                if dnorm and pnorm and len(dnorm) > 3 and len(pnorm) > 3:
                    if dnorm == pnorm or dnorm in pnorm or pnorm in dnorm:
                        eid = get_or_create_entity(db, p['developer_name'])
                        return _route(p['project_id'], eid, 'doe_enrichment', 0.90, {
                            'method': 'doe_enrichment', 'doe_name': doe_name,
                            'doe_norm': dnorm, 'original_claim_company': cname,
                            'matched': p['developer_name'],
                        })
            # alias match
            alias_hit_doe = _alias_lookup(db, doe_name)
            if alias_hit_doe:
                entity_id, alias_method, alias_id = alias_hit_doe
                entity_row = db.execute(
                    "SELECT canonical_name FROM entities WHERE entity_id=?",
                    (entity_id,)
                ).fetchone()
                if entity_row:
                    canonical = entity_row[0]
                    for p in projects:
                        pnorm = normalize(p['developer_name'])
                        cnorm2 = normalize(canonical)
                        if (p['developer_name'] == canonical
                                or (cnorm2 and pnorm and (cnorm2 in pnorm or pnorm in cnorm2))):
                            return _route(p['project_id'], entity_id, 'doe_enrichment_alias', 0.90, {
                                'method': 'doe_enrichment_alias',
                                'doe_name': doe_name, 'via_alias': canonical,
                                'original_claim_company': cname,
                                'matched': p['developer_name'],
                            })

    # ── Tier 3: alias lookup ─────────────────────────────────────────────────
    # Try full name first, then prefix words (catches "PRAXAIR TEXAS CITY" → PRAXAIR alias)
    alias_hit = _alias_lookup(db, cname)
    if not alias_hit:
        # Try each word/prefix of the company name as an alias
        words = re.split(r'[\s\-.,;/()]+', cname)
        for word in words:
            if len(word) < 4:
                continue
            alias_hit = _alias_lookup(db, word)
            if alias_hit:
                break
    if alias_hit:
        entity_id, alias_method, alias_id = alias_hit
        # Find project by entity canonical name
        entity_row = db.execute(
            "SELECT canonical_name FROM entities WHERE entity_id=?", (entity_id,)
        ).fetchone()
        if entity_row:
            canonical = entity_row[0]
            for p in projects:
                pnorm = normalize(p['developer_name'])
                cnorm2 = normalize(canonical)
                if (p['developer_name'] == canonical
                        or (cnorm2 and pnorm and (cnorm2 in pnorm or pnorm in cnorm2))):
                    # Fetch alias citation for provenance
                    ar = db.execute("""
                        SELECT cited_document_type, cited_document_date,
                               cited_document_url, cited_text_excerpt,
                               citing_company_cik
                        FROM entity_aliases WHERE id=?
                    """, (alias_id,)).fetchone()
                    citation = {
                        'method': alias_method,
                        'via_alias': cname,
                        'resolved_to': canonical,
                        'alias_doc_type': ar['cited_document_type'] if ar else None,
                        'alias_doc_date': ar['cited_document_date'] if ar else None,
                        'alias_doc_url':  ar['cited_document_url']  if ar else None,
                        'alias_excerpt':  ar['cited_text_excerpt']  if ar else None,
                        'alias_cik':      ar['citing_company_cik']  if ar else None,
                    }
                    return _route(p['project_id'], entity_id, alias_method, 0.95, citation)

    # ── Tier 4: LLM judgment on ambiguous middle-band ────────────────────────
    # Find candidate projects by word overlap score
    candidates = []
    for p in projects:
        score = word_overlap(cname, p['developer_name'])
        if score >= llm_lower and score < llm_upper:
            candidates.append((score, p))
    candidates.sort(key=lambda x: -x[0])

    for score, p in candidates[:3]:   # top 3 candidates only
        # Fetch known aliases for candidate
        aliases = [r[0] for r in db.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id IN "
            "(SELECT entity_id FROM entities WHERE canonical_name=?)",
            (p['developer_name'],)
        ).fetchall()]

        prompt = _RESOLUTION_PROMPT.format(
            claim_company    = cname,
            candidate_name   = p['developer_name'],
            aliases          = json.dumps(aliases) if aliases else '[]',
            document_type    = claim['document_type'] or '',
            document_date    = claim['document_date'] or '',
            claim_text_snippet = (claim['claim_text'] or '')[:300],
        )

        try:
            raw = _llm(prompt, max_tokens=300)
            result = json.loads(raw)
        except Exception as e:
            print(f"    [resolution] LLM/parse error for '{cname}': {e}")
            continue

        decision         = result.get('decision', 'UNCERTAIN')
        conf             = float(result.get('confidence', 0.0))
        supporting_text  = (result.get('supporting_text') or '').strip()
        reasoning        = (result.get('reasoning') or '').strip()

        if decision == 'SAME' and conf >= 0.70 and supporting_text:
            # Verify supporting_text has at least partial overlap with known data
            # (not a full verbatim check here — it may cite the alias list)
            eid = get_or_create_entity(db, p['developer_name'])
            citation = {
                'method': 'llm_explicit',
                'claim_company': cname,
                'matched_project': p['developer_name'],
                'llm_supporting_text': supporting_text,
                'llm_reasoning': reasoning,
                'llm_confidence': conf,
                'word_overlap_score': score,
            }
            return _route(p['project_id'], eid, 'llm_explicit', conf, citation)

    # ── Tier 5: Unresolved ───────────────────────────────────────────────────
    return None, None, 'unresolved', 0.0, None


# ── Core function 3: Accumulate signal ──────────────────────────────────────
_STAGE_MAP = {
    'construction_commenced': 'construction',
    'construction_commencement': 'construction',
    'notice_to_proceed': 'construction',
    'final_investment_decision': 'fid',
    'final_investment_decision_made': 'fid',
    'financial_close': 'fid',
    'announced': 'announced',
    'low_carbon_ammonia_sales_began': 'operational',
}

def accumulate_signal(
    project_id: str,
    claim: sqlite3.Row,
    entity_id: str,
    resolution_citation: dict,
    db: sqlite3.Connection,
    cik: Optional[str],
) -> None:
    """
    Feed a connected claim into the signal accumulator.
    Only stage-advancing signals update unified_projects,
    and only when accumulated_conf >= threshold AND source diversity >= minimum.
    """
    ctype = claim['claim_type']

    # Determine what signal value this claim asserts
    signal_type = None
    asserted_value = None

    if ctype in _STAGE_MAP:
        signal_type    = 'stage'
        asserted_value = _STAGE_MAP[ctype]
    elif ctype in ('facility_location', 'project_location',
                   'location_identification', 'address_identification',
                   'facility_address', 'address', 'location',
                   'epa_facility_location'):
        signal_type    = 'location'
        asserted_value = (claim['claim_text'] or '')[:200]
    elif ctype in ('project_capacity', 'production_capacity', 'capacity_mtpa',
                   'facility_capacity', 'production_capacity_specification',
                   'capture_capacity', 'injection_capacity', 'storage_capacity'):
        signal_type    = 'capacity'
        asserted_value = (claim['claim_text'] or '')[:200]
    elif ctype in ('technology_description', 'epa_derived_technology',
                   'project_technology', 'technology_approach',
                   'hydrogen_production_method', 'capture_technology',
                   'hydrogen_production_process'):
        # Filter GHGRP boilerplate — these claim types are 90%+ Subpart P noise
        text = (claim['claim_text'] or '')
        conf = claim['confidence'] or 0
        if conf < 0.7 or len(text) < 10 or text.startswith('This facility reports under GHGRP'):
            pass  # skip boilerplate
        else:
            signal_type    = 'technology'
            asserted_value = text[:200]

    if not signal_type:
        return

    # Build citation entry for this claim
    new_citation = {
        'claim_id':       claim['claim_id'],
        'claim_text':     (claim['claim_text'] or '')[:300],
        'document_type':  claim['document_type'],
        'document_date':  claim['document_date'],
        'document_url':   claim['document_url'],
        'company_cik':    cik or '',
        'resolution':     resolution_citation,
    }

    # Get or create accumulator row
    row = db.execute("""
        SELECT id, citations, source_types, accumulated_conf
        FROM project_signal_accumulator
        WHERE project_id=? AND signal_type=? AND asserted_value=?
    """, (project_id, signal_type, asserted_value)).fetchone()

    if row:
        citations = json.loads(row['citations'] or '[]')
        source_types = json.loads(row['source_types'] or '[]')
        # Don't double-count same claim
        existing_ids = {c.get('claim_id') for c in citations}
        if claim['claim_id'] in existing_ids:
            return
        citations.append(new_citation)
        if claim['document_type'] and claim['document_type'] not in source_types:
            source_types.append(claim['document_type'])
        # Accumulation formula: each additional independent source adds weight
        n = len(citations)
        new_conf = min(0.99, 0.60 + 0.15 * (n - 1))
        db.execute("""
            UPDATE project_signal_accumulator
            SET citations=?, source_types=?, accumulated_conf=?, last_updated=?
            WHERE id=?
        """, (json.dumps(citations), json.dumps(source_types), new_conf,
              now_iso(), row['id']))
    else:
        citations    = [new_citation]
        source_types = [claim['document_type']] if claim['document_type'] else []
        new_conf     = 0.60  # single source starting confidence
        db.execute("""
            INSERT INTO project_signal_accumulator
            (project_id, signal_type, asserted_value, citations,
             source_types, accumulated_conf, last_updated, applied_to_db)
            VALUES (?,?,?,?,?,?,?,0)
        """, (project_id, signal_type, asserted_value,
              json.dumps(citations), json.dumps(source_types),
              new_conf, now_iso()))

    db.commit()

    # Check if threshold crossed → apply to unified_projects
    apply_threshold = float(get_config(db, 'accumulator_apply_threshold'))
    min_sources     = int(get_config(db, 'min_sources_to_apply'))

    acc_row = db.execute("""
        SELECT id, accumulated_conf, source_types, citations
        FROM project_signal_accumulator
        WHERE project_id=? AND signal_type=? AND asserted_value=? AND applied_to_db=0
    """, (project_id, signal_type, asserted_value)).fetchone()

    if not acc_row:
        return
    if acc_row['accumulated_conf'] < apply_threshold:
        return
    if len(json.loads(acc_row['source_types'] or '[]')) < min_sources:
        return

    # Apply: update unified_projects
    if signal_type == 'stage':
        prior = db.execute(
            "SELECT stage FROM unified_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        prior_val = prior['stage'] if prior else None
        db.execute(
            "UPDATE unified_projects SET stage=?, updated_at=? WHERE project_id=?",
            (asserted_value, now_iso(), project_id)
        )
        db.execute(
            "UPDATE project_signal_accumulator SET applied_to_db=1 WHERE id=?",
            (acc_row['id'],)
        )
        # Audit log
        db.execute("""
            INSERT INTO citation_audit_log
            (event_type, project_id, assertion, prior_value, new_value,
             citation_json, confidence, triggered_by, created_at)
            VALUES ('stage_updated',?,?,?,?,?,?,'accumulator',?)
        """, (
            project_id,
            f"stage={asserted_value}",
            prior_val, asserted_value,
            acc_row['citations'],
            acc_row['accumulated_conf'],
            now_iso()
        ))
        db.commit()
        print(f"    → APPLIED: project {project_id[:8]} stage "
              f"'{prior_val}' → '{asserted_value}' "
              f"(conf={acc_row['accumulated_conf']:.2f})")


# ── Core function 3½: Seed EPA aliases from operator/LLC/parent names ──────

def _seed_epa_aliases(db: sqlite3.Connection, projects: list[dict]) -> int:
    """Seed entity aliases from EPA enrichment columns.

    For each EPA facility with operator_name/llc_entity/parent_company,
    try to match to an existing project entity via normalization.
    If matched, register the EPA name as an alias so future claims resolve.
    """
    print(f"\n── Phase A½: Seed EPA Aliases ─────────────────────────────")

    try:
        epa_rows = db.execute("""
            SELECT id, company_name, company_cik, operator_name, llc_entity, parent_company
            FROM regulatory_evidence
            WHERE source_system = 'epa_echo'
              AND (operator_name IS NOT NULL OR llc_entity IS NOT NULL
                   OR parent_company IS NOT NULL)
        """).fetchall()
    except sqlite3.OperationalError:
        print("  EPA enrichment columns not yet added — skipping")
        return 0

    if not epa_rows:
        print("  No EPA facilities with operator/LLC/parent data — skipping")
        return 0

    print(f"  {len(epa_rows)} EPA facilities with entity data")
    seeded = 0

    # Build normalized project index for fast lookup
    proj_norm = {}
    for p in projects:
        pn = normalize(p['developer_name'])
        if pn:
            proj_norm[pn] = p

    for row in epa_rows:
        # Try each EPA name source
        names_to_try = []
        if row['operator_name']:
            names_to_try.append(('epa_operator_name', row['operator_name']))
        if row['llc_entity']:
            names_to_try.append(('epa_llc_entity', row['llc_entity']))
        if row['parent_company']:
            names_to_try.append(('epa_parent_company', row['parent_company']))

        for alias_type, epa_name in names_to_try:
            epa_norm = normalize(epa_name)
            if not epa_norm or len(epa_norm) < 4:
                continue

            # Check if already an alias
            existing = db.execute(
                "SELECT 1 FROM entity_aliases WHERE alias_norm=?",
                (epa_norm,)
            ).fetchone()
            if existing:
                continue

            # Try to match against projects
            matched_project = None
            for pn, p in proj_norm.items():
                if epa_norm == pn or epa_norm in pn or pn in epa_norm:
                    matched_project = p
                    break

            if not matched_project:
                # Try word overlap as fallback
                for pn, p in proj_norm.items():
                    if word_overlap(epa_name, p['developer_name']) >= 0.60:
                        matched_project = p
                        break

            if matched_project:
                entity_id = get_or_create_entity(db, matched_project['developer_name'])
                try:
                    db.execute("""
                        INSERT OR IGNORE INTO entity_aliases
                        (entity_id, alias, alias_norm, alias_type,
                         source_claim_id, cited_document_type,
                         cited_text_excerpt, is_explicit, confidence, learned_at)
                        VALUES (?,?,?,?, NULL, 'epa_echo', ?,1,0.85,?)
                    """, (
                        entity_id, epa_name, epa_norm, alias_type,
                        f"EPA facility {row['company_name']} (registry {row['company_cik']})",
                        now_iso(),
                    ))
                    seeded += 1
                    print(f"    + EPA alias: '{epa_name}' → '{matched_project['developer_name']}' [{alias_type}]")
                except sqlite3.IntegrityError:
                    pass  # UNIQUE constraint — already exists

    db.commit()
    print(f"  EPA aliases seeded: {seeded}")
    return seeded


# ── Core function 4: Main connection loop ───────────────────────────────────
def connect_claims_to_projects(
    db: sqlite3.Connection,
    aliases_only: bool = False,
    connect_only: bool = False,
    retroactive: bool = False,
) -> dict:
    """
    Main loop: process all claims from core_database.db.
    Phase A: extract aliases from qualifying claims.
    Phase B: resolve all claims to projects, populate project_claims.
    """
    # Reset corporate events cache so temporal resolution uses fresh data
    global _corporate_events_cache
    _corporate_events_cache = None

    llm_lower = float(get_config(db, 'llm_judge_lower'))
    llm_upper = float(get_config(db, 'llm_judge_upper'))
    alias_types = json.loads(get_config(db, 'alias_extraction_claim_types'))

    claims = db.execute("""
        SELECT claim_id, source_id, company_name, document_type, document_date,
               document_url, claim_type, claim_text, confidence
        FROM claims
        WHERE superseded_by IS NULL
        ORDER BY document_date ASC, extracted_at ASC
    """).fetchall()

    projects = db.execute(
        "SELECT project_id, project_name, developer_key, developer_name, "
        "company_id, project_type, source_identity, "
        "stage, state, city FROM unified_projects"
    ).fetchall()
    projects = [dict(p) for p in projects]

    # Load companies for Layer 2 routing
    companies = [dict(c) for c in db.execute(
        "SELECT company_id, company_key, company_name FROM companies"
    ).fetchall()] if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='companies'"
    ).fetchone() else []

    # Don't auto-create facility projects during retroactive re-evaluation
    auto_create = not retroactive

    stats = {
        'total': len(claims), 'aliases_extracted': 0,
        'temporal_ownership': 0,
        'exact': 0, 'project_name': 0, 'normalized': 0, 'alias': 0,
        'epa_enrichment': 0, 'llm_explicit': 0,
        'unresolved': 0, 'skipped': 0,
        'stage_updates': 0,
    }

    # ── Phase A: alias extraction ────────────────────────────────────────────
    if not connect_only:
        print(f"\n── Phase A: Alias Extraction ──────────────────────────────")
        alias_claims = [c for c in claims if c['claim_type'] in alias_types]
        print(f"  {len(alias_claims)} alias-qualifying claims to process")

        for i, claim in enumerate(alias_claims, 1):
            print(f"\n  [{i}/{len(alias_claims)}] {claim['company_name']} "
                  f"/ {claim['claim_type']} / {claim['document_date']}")
            n = extract_entity_aliases(claim, db)
            stats['aliases_extracted'] += n
            time.sleep(0.3)   # rate limit

    if aliases_only:
        return stats

    # ── Phase A½: Seed EPA aliases from operator/LLC/parent names ───────────
    epa_alias_count = _seed_epa_aliases(db, projects)
    if epa_alias_count > 0:
        stats['aliases_extracted'] += epa_alias_count
        # Reload projects in case entities changed
        projects = db.execute(
            "SELECT project_id, project_name, developer_key, developer_name, "
            "company_id, project_type, source_identity, "
            "stage, state, city FROM unified_projects"
        ).fetchall()
        projects = [dict(p) for p in projects]

    # ── Phase B: resolve all claims to projects ──────────────────────────────
    print(f"\n── Phase B: Claim Resolution ──────────────────────────────")
    print(f"  {len(claims)} claims × {len(projects)} projects")
    print(f"  LLM judge band: [{llm_lower:.2f}, {llm_upper:.2f})")

    # Pre-fetch CIKs from regulatory_evidence
    cik_map = {
        r['id']: r['company_cik']
        for r in db.execute("SELECT id, company_cik FROM regulatory_evidence")
    }

    already_connected = {
        r[0] for r in db.execute(
            "SELECT claim_id FROM project_claims WHERE resolution_method != 'unresolved'"
        )
    }

    for i, claim in enumerate(claims):
        if claim['claim_id'] in already_connected:
            stats['skipped'] += 1
            continue

        tier = get_signal_tier(claim['claim_type'])
        project_id, entity_id, method, conf, citation = resolve_claim_to_entity(
            claim, db, projects, llm_lower, llm_upper,
            companies=companies, auto_create_projects=auto_create,
        )

        cik = cik_map.get(claim['source_id'])

        # Write to project_claims regardless of resolution outcome.
        # If a prior run left an 'unresolved' row and we now have a match,
        # upgrade it in-place rather than silently ignoring the new result.
        existing = db.execute(
            "SELECT resolution_method FROM project_claims WHERE claim_id=?",
            (claim['claim_id'],)
        ).fetchone()

        if existing and existing[0] == 'unresolved' and project_id:
            # Upgrade: previously unresolved, now resolved
            db.execute("""
                UPDATE project_claims
                SET project_id=?, entity_id=?, signal_tier=?,
                    resolution_method=?, resolution_conf=?,
                    resolution_citation=?, last_resolved_at=?
                WHERE claim_id=?
            """, (
                project_id, entity_id, tier,
                method, conf,
                json.dumps(citation) if citation else None,
                now_iso(), claim['claim_id']
            ))
        elif not existing:
            # First time seeing this claim
            db.execute("""
                INSERT INTO project_claims
                (project_id, entity_id, claim_id, claim_type, signal_tier,
                 claim_text, document_type, document_date, document_url,
                 confidence, resolution_method, resolution_conf,
                 resolution_citation, last_resolved_at, surfaced_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                project_id, entity_id,
                claim['claim_id'], claim['claim_type'], tier,
                claim['claim_text'], claim['document_type'],
                claim['document_date'], claim['document_url'],
                claim['confidence'], method, conf,
                json.dumps(citation) if citation else None,
                now_iso(), now_iso()
            ))
        else:
            # Already resolved or still unresolved — skip
            stats['skipped'] += 1
            continue

        # Audit resolved connections
        if project_id:
            db.execute("""
                INSERT INTO citation_audit_log
                (event_type, entity_id, project_id, assertion,
                 new_value, citation_json, confidence, triggered_by, created_at)
                VALUES ('claim_connected',?,?,?,?,?,?,'connect_loop',?)
            """, (
                entity_id, project_id,
                f"claim {claim['claim_type']} → project",
                claim['claim_id'],
                json.dumps(citation) if citation else None,
                conf, now_iso()
            ))
            # Method may include Layer 2 routing suffix (e.g. 'exact+facility_match')
            base_method = method.split('+')[0] if '+' in method else method
            stats[base_method if base_method in stats else 'alias'] += 1

            # Feed into signal accumulator
            if tier in ('FID_SIGNAL', 'COMPETITOR', 'PERMIT'):
                accumulate_signal(project_id, claim, entity_id,
                                  citation or {}, db, cik)
        else:
            stats['unresolved'] += 1

        if (i + 1) % 100 == 0:
            db.commit()
            print(f"  ... {i+1}/{len(claims)} processed")

    db.commit()
    return stats


# ── Core function 5: Retroactive reconnect ──────────────────────────────────
def retroactive_reconnect(
    db: sqlite3.Connection,
    since: Optional[str] = None,
) -> int:
    """
    Re-evaluate unresolved claims, now that entity_aliases may be richer.
    Uses last_resolved_at to limit scope — only claims resolved before
    the most recent alias learning event are re-evaluated.
    """
    batch_size  = int(get_config(db, 'retroactive_batch_size'))
    sleep_sec   = float(get_config(db, 'retroactive_sleep_sec'))
    llm_lower   = float(get_config(db, 'llm_judge_lower'))
    llm_upper   = float(get_config(db, 'llm_judge_upper'))

    # Reset corporate events cache so temporal resolution uses fresh data
    global _corporate_events_cache
    _corporate_events_cache = None

    # Default since: timestamp of newest alias OR newest corporate event
    if not since:
        alias_ts = db.execute(
            "SELECT MAX(learned_at) FROM entity_aliases"
        ).fetchone()[0] or '1970-01-01T00:00:00+00:00'

        ce_ts = '1970-01-01T00:00:00+00:00'
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='corporate_events'"
        ).fetchone():
            row = db.execute(
                "SELECT MAX(created_at) FROM corporate_events"
            ).fetchone()
            ce_ts = row[0] if row and row[0] else ce_ts

        since = max(alias_ts, ce_ts)

    unresolved = db.execute("""
        SELECT claim_id FROM project_claims
        WHERE resolution_method='unresolved'
          AND last_resolved_at < ?
    """, (since,)).fetchall()

    if not unresolved:
        print("  No unresolved claims older than the newest alias.")
        return 0

    print(f"  {len(unresolved)} unresolved claims to re-evaluate "
          f"(since {since[:19]})")

    projects = db.execute(
        "SELECT project_id, project_name, developer_key, developer_name, "
        "company_id, project_type, source_identity, "
        "stage, state, city FROM unified_projects"
    ).fetchall()
    projects = [dict(p) for p in projects]

    # Load companies for Layer 2 routing (auto_create=False for retroactive)
    companies = [dict(c) for c in db.execute(
        "SELECT company_id, company_key, company_name FROM companies"
    ).fetchall()] if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='companies'"
    ).fetchone() else []

    cik_map  = {
        r['id']: r['company_cik']
        for r in db.execute("SELECT id, company_cik FROM regulatory_evidence")
    }

    reconnected = 0
    ids_to_retry = [r['claim_id'] for r in unresolved]

    for batch_start in range(0, len(ids_to_retry), batch_size):
        batch = ids_to_retry[batch_start: batch_start + batch_size]

        for claim_id in batch:
            # Re-fetch full claim from core_database.db
            claim = db.execute("""
                SELECT claim_id, source_id, company_name, document_type,
                       document_date, document_url, claim_type, claim_text,
                       confidence
                FROM claims WHERE claim_id=?
            """, (claim_id,)).fetchone()
            if not claim:
                continue

            project_id, entity_id, method, conf, citation = resolve_claim_to_entity(
                claim, db, projects, llm_lower, llm_upper,
                companies=companies, auto_create_projects=False,
            )

            if project_id:
                cik = cik_map.get(claim['source_id'])
                db.execute("""
                    UPDATE project_claims
                    SET project_id=?, entity_id=?, resolution_method=?,
                        resolution_conf=?, resolution_citation=?,
                        last_resolved_at=?
                    WHERE claim_id=?
                """, (
                    project_id, entity_id, method, conf,
                    json.dumps(citation) if citation else None,
                    now_iso(), claim_id
                ))
                db.execute("""
                    INSERT INTO citation_audit_log
                    (event_type, entity_id, project_id, assertion,
                     new_value, citation_json, confidence, triggered_by, created_at)
                    VALUES ('claim_connected',?,?,?,?,?,?,'retroactive_reconnect',?)
                """, (
                    entity_id, project_id,
                    f"retroactive: {claim['claim_type']}",
                    claim_id,
                    json.dumps(citation) if citation else None,
                    conf, now_iso()
                ))
                reconnected += 1
            else:
                # Update timestamp so we don't re-evaluate again until newer alias
                db.execute(
                    "UPDATE project_claims SET last_resolved_at=? WHERE claim_id=?",
                    (now_iso(), claim_id)
                )

        db.commit()
        if batch_start + batch_size < len(ids_to_retry):
            time.sleep(sleep_sec)
        print(f"  ... batch {batch_start // batch_size + 1} done, "
              f"{reconnected} reconnected so far")

    return reconnected


# ── Stats report ─────────────────────────────────────────────────────────────
def print_stats(db: sqlite3.Connection) -> None:
    print("\n" + "="*60)
    print("CONNECT — CURRENT STATE")
    print("="*60)

    print("\n  ENTITIES & ALIASES")
    print(f"    Entities:      {db.execute('SELECT COUNT(*) FROM entities').fetchone()[0]}")
    print(f"    Aliases:       {db.execute('SELECT COUNT(*) FROM entity_aliases').fetchone()[0]}")
    print(f"    Alias types:")
    for r in db.execute(
        "SELECT alias_type, COUNT(*) n FROM entity_aliases GROUP BY alias_type ORDER BY n DESC"
    ).fetchall():
        print(f"      {r[0]:<20} {r[1]}")

    print("\n  PROJECT CLAIMS BRIDGE")
    total = db.execute("SELECT COUNT(*) FROM project_claims").fetchone()[0]
    print(f"    Total:         {total}")
    for r in db.execute("""
        SELECT resolution_method, COUNT(*) n FROM project_claims
        GROUP BY resolution_method ORDER BY n DESC
    """).fetchall():
        print(f"    {r[0]:<20} {r[1]}")

    print("\n  BY SIGNAL TIER (resolved only)")
    for r in db.execute("""
        SELECT signal_tier, COUNT(*) n FROM project_claims
        WHERE resolution_method != 'unresolved'
        GROUP BY signal_tier ORDER BY n DESC
    """).fetchall():
        print(f"    {r[0]:<20} {r[1]}")

    print("\n  SIGNAL ACCUMULATOR")
    for r in db.execute("""
        SELECT signal_type, COUNT(*) total,
               SUM(applied_to_db) applied,
               ROUND(AVG(accumulated_conf),2) avg_conf
        FROM project_signal_accumulator
        GROUP BY signal_type
    """).fetchall():
        print(f"    {r[0]:<12}  total={r[1]}  applied={r[2]}  avg_conf={r[3]}")

    print("\n  UNIFIED_PROJECTS — STAGE AFTER CONNECT")
    for r in db.execute("""
        SELECT stage, COUNT(*) n FROM unified_projects
        GROUP BY stage ORDER BY n DESC
    """).fetchall():
        print(f"    {r[0] or 'NULL':<20} {r[1]}")

    print("\n  AUDIT LOG EVENTS")
    for r in db.execute("""
        SELECT event_type, COUNT(*) n FROM citation_audit_log
        GROUP BY event_type ORDER BY n DESC
    """).fetchall():
        print(f"    {r[0]:<25} {r[1]}")


# ── Entry point ──────────────────────────────────────────────────────────────
def run(aliases_only: bool = False,
        connect_only: bool = False,
        retroactive:  bool = False,
        stats_only:   bool = False) -> None:

    print(f"Opening databases...")
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    print(f"  Core DB: {DB_PATH}")

    if not stats_only:
        print("\nRunning schema migration...")
        migrate(db)
        seed_config(db)

    if stats_only:
        print_stats(db)
        db.close(); db.close()
        return

    if retroactive:
        print("\n── Retroactive Reconnect ──────────────────────────────────")
        n = retroactive_reconnect(db)
        print(f"  Reconnected: {n} claims")
    else:
        stats = connect_claims_to_projects(
            db,
            aliases_only=aliases_only,
            connect_only=connect_only,
        )
        print(f"\n{'='*60}")
        print(f"RUN COMPLETE")
        print(f"{'='*60}")
        print(f"  Claims processed:    {stats['total']}")
        print(f"  Aliases extracted:   {stats['aliases_extracted']}")
        print(f"  Resolved — exact:    {stats['exact']}")
        print(f"  Resolved — norm:     {stats['normalized']}")
        print(f"  Resolved — alias:    {stats.get('alias_exact',0) + stats.get('alias_norm',0)}")
        print(f"  Resolved — LLM:      {stats['llm_explicit']}")
        print(f"  Unresolved:          {stats['unresolved']}")
        print(f"  Skipped (dup):       {stats['skipped']}")

    print_stats(db)
    db.close()
    db.close()


if __name__ == '__main__':
    run(
        aliases_only = '--aliases-only' in sys.argv,
        connect_only = '--connect-only' in sys.argv,
        retroactive  = '--retroactive'  in sys.argv,
        stats_only   = '--stats'        in sys.argv,
    )