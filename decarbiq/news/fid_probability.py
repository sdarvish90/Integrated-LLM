"""
FID Probability Assessment Engine (v2 — LLM-based)

Fetches actual regulatory document text from SEC EDGAR, EPA ECHO, and
DOE LPO, stores evidence in a per-company SQLite database, and uses
an LLM (Groq → Ollama fallback) to determine project stage and FID
probability from the evidence.

Cross-source dedup: the LLM sees ALL evidence at once and synthesizes
a single assessment — an SEC filing and EPA permit for the same project
are not double-counted.

Data Sources:
- SEC EDGAR filings (10-K, 8-K text)
- EPA ECHO facility details
- DOE Loan Programs Office pages
- State PUC search URLs (reference only)

Usage:
    engine = FIDProbabilityEngine()
    result = engine.assess_project("Plug Power", "NY", status="Announced")
    # result['probability'], result['stage'], result['reasoning']
"""

import hashlib
import json
import re
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from llm_client import llm_client

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
DATABASE_PATH = str(_HERE / 'blue_h2_intelligence.db')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# SEC EDGAR rate limit (10 req/s officially; we stay conservative)
_SEC_SLEEP = 0.12

# Per-document excerpt limit (fits comfortably in Groq 128K context)
MAX_EXCERPT_CHARS = 4000

# Evidence freshness
EVIDENCE_MAX_AGE_DAYS = 14
ASSESSMENT_MAX_AGE_DAYS = 7

# Current prompt version — bump when prompt changes to invalidate old assessments
PROMPT_VERSION = 1

# SEC filing sections to extract (prioritized)
_SEC_SECTION_PATTERNS = [
    # 8-K item headers
    (r'(?i)(Item\s+1\.01[^A-Z]*(?:Entry\s+into|Material\s+Definitive))', 1.0),
    (r'(?i)(Item\s+2\.03[^A-Z]*(?:Creation|Direct\s+Financial))', 0.95),
    (r'(?i)(Item\s+8\.01[^A-Z]*Other\s+Events)', 0.90),
    (r'(?i)(Item\s+2\.01[^A-Z]*(?:Completion|Acquisition))', 0.85),
    # 10-K / 10-Q section headers
    (r'(?i)(Management.{0,5}s?\s+Discussion\s+and\s+Analysis)', 0.80),
    (r'(?i)(Liquidity\s+and\s+Capital\s+Resources)', 0.80),
    (r'(?i)(Capital\s+Commitments|Capital\s+Expenditures)', 0.85),
    (r'(?i)(Commitments\s+and\s+Contingencies)', 0.75),
    (r'(?i)(Risk\s+Factors)', 0.50),
]

# H2 keywords for relevance filtering
_H2_KEYWORDS = [
    'hydrogen', 'h2', 'ammonia', 'nh3', 'blue hydrogen', 'clean hydrogen',
    'carbon capture', 'ccus', 'ccs', 'steam methane reform', 'smr',
    'autothermal reform', 'atr', 'electrolyzer', 'electrolysis',
    'final investment decision', 'fid', 'feed study', 'front-end engineering',
    'epc contract', 'construction', 'commissioning', 'commercial operation',
    'groundbreaking', 'loan guarantee', 'title 17', '45v', '45q',
    'hydrogen hub', 'clean energy',
]

# Evidence type priority for ranking
_TYPE_PRIORITY = {
    '8-K': 1.0, '8-K/A': 1.0,
    '10-K': 0.8, '10-Q': 0.7,
    'S-1': 0.6, 'S-4': 0.6,
    'facility': 0.5,
    'lpo_mention': 0.6,
    'puc_reference': 0.3,
    'award': 0.7,              # DOE USASpending award
    'lpo_project': 0.8,        # DOE LPO portfolio project
    'h2hub_project': 0.85,     # DOE H2Hub — strong government backing
    'press_release': 0.5,      # DOE press release
    'ferc_filing': 0.6,        # FERC regulatory filing
    'regulatory_notice': 0.55, # EPA/DOE/PHMSA regulatory notice
}

# ---------------------------------------------------------------------------
# EFTS Broad Discovery Configuration
# ---------------------------------------------------------------------------

# EFTS endpoint (verified working with proper User-Agent header)
EFTS_ENDPOINT = 'https://efts.sec.gov/LATEST/search-index'
EFTS_FORM_TYPES = '8-K,10-K,10-Q,S-1,S-4'
EFTS_PAGE_SIZE = 100            # EFTS hard cap per response
EFTS_RETRY_COUNT = 3
EFTS_RETRY_BACKOFF = [2, 4, 8]  # seconds
EFTS_INITIAL_LOOKBACK_DAYS = 180
EFTS_SUBSEQUENT_LOOKBACK_DAYS = 7

# RSS backup endpoint
SEC_RSS_URL = 'https://www.sec.gov/cgi-bin/browse-edgar'

# Strategy 1: Core keyword queries
EFTS_CORE_QUERIES = [
    '"blue hydrogen"',
    '"clean hydrogen" production',
    '"low carbon hydrogen"',
    '"blue ammonia"',
    '"clean ammonia"',
    'ammonia "carbon capture"',
    'hydrogen "carbon capture" storage',
    '"hydrogen production" "final investment"',
    'hydrogen "EPC contract"',
    '"hydrogen hub"',
    '"autothermal reform" hydrogen',
    '"steam methane reform" hydrogen',
]

# Strategy 2: Extended technical/policy queries
EFTS_EXTENDED_QUERIES = [
    '"steam methane reforming" "carbon capture"',
    '"autothermal reforming" CCS',
    '"ATR" "blue hydrogen"',
    '"SMR" "carbon capture"',
    '"Haber-Bosch" "low-carbon"',
    'ammonia electrolysis',
    '"45Q" hydrogen',
    '"45V" "clean hydrogen"',
    '"hydrogen offtake"',
    '"clean fuel standard" hydrogen',
]

# Strategy 3: Capex signal queries
EFTS_CAPEX_QUERIES = [
    '"capital expenditure" hydrogen',
    '"project finance" hydrogen',
    '"capital commitment" hydrogen',
    '"capital expenditure" ammonia',
    '"capital allocation" hydrogen ammonia',
    '"construction spend" hydrogen',
]

# Strategy 4: NAICS/SIC industry queries (via EFTS full-text search)
EFTS_NAICS_QUERIES = [
    'SIC 2813 hydrogen',          # Industrial Gases
    'SIC 2819 ammonia',           # Industrial Inorganic Chemicals
    'SIC 2873 ammonia',           # Nitrogenous Fertilizers
    'SIC 2911 hydrogen',          # Petroleum Refining
    'NAICS 325120 hydrogen',      # Industrial Gas Manufacturing
    'NAICS 325311 ammonia',       # Nitrogenous Fertilizer Manufacturing
]

# SIC codes for industry-aware company detection
H2_SIC_CODES = {
    '2813': 'Industrial Gases',
    '2819': 'Industrial Inorganic Chemicals',
    '2873': 'Nitrogenous Fertilizers',
    '2911': 'Petroleum Refining',
}

# All possible discovery_method values
DISCOVERY_METHODS = [
    # EFTS SEC (existing)
    'efts_core',        # Strategy 1: core keywords
    'efts_extended',    # Strategy 2: technical/policy terms
    'efts_capex',       # Strategy 3: capex signals
    'efts_naics',       # Strategy 4: SIC/NAICS industry
    'efts_llm',         # Strategy 5: LLM-generated queries
    'rss',              # Strategy 6: RSS feed backup
    # EPA broad
    'epa_sic', 'epa_naics', 'epa_keyword', 'epa_ghg_sic',
    # DOE broad
    'usaspending_keyword', 'usaspending_naics',
    'doe_lpo_scrape', 'doe_h2hub_scrape', 'doe_press_scrape',
    # Federal regulatory (regulations.gov)
    'regsgov_ferc', 'regsgov_doe', 'regsgov_eere',
    'regsgov_phmsa', 'regsgov_epa', 'regsgov_broad',
]

# RSS keyword filter for company names/titles
RSS_COMPANY_KEYWORDS = [
    'hydrogen', 'ammonia', 'air products', 'linde', 'cf industries',
    'plug power', 'exxon', 'chevron', 'shell', 'bp ', 'denbury',
    'carbon capture', 'ccs', 'clean energy',
]

# Company name suffixes to strip for normalization
_COMPANY_SUFFIXES = [
    ', Inc.', ', Inc', ' Inc.', ' Inc', ', LLC', ' LLC',
    ', Ltd.', ', Ltd', ' Ltd.', ' Ltd',
    ', Corp.', ', Corp', ' Corp.', ' Corp',
    ', L.P.', ' L.P.', ', LP', ' LP',
    ', PLC', ' PLC', ', plc', ' plc',
    ', N.V.', ' N.V.', ', S.A.', ' S.A.',
    ' Holdings', ' Group', ' International',
    ' Company', ' Corporation', ' Incorporated',
    ' & Co.', ' & Co',
]

# LLM relevance filter for EFTS filings
_EFTS_RELEVANCE_SYSTEM = (
    'You are a senior energy analyst screening SEC filings for relevance to '
    'blue hydrogen and ammonia projects. Be precise and conservative.'
)

_EFTS_RELEVANCE_PROMPT = """Determine if this SEC filing excerpt is relevant to BLUE HYDROGEN or BLUE AMMONIA project development.

INCLUDE (relevant = true):
- Blue hydrogen production facilities (SMR or ATR with CCS)
- Blue ammonia production or export projects
- Carbon capture paired with hydrogen/ammonia production
- SMR/ATR + CCS project development, FID, construction, commissioning
- Hydrogen/ammonia project capital expenditure, EPC contracts, FEED studies
- 45Q/45V tax credit claims for H2/NH3 projects

EXCLUDE (relevant = false):
- Green hydrogen (electrolysis only, no fossil feedstock)
- Fuel cell vehicles or stationary fuel cells
- Hydrogen bonds (chemistry/pharma context)
- Hydrogen peroxide manufacturing
- General "hydrogen economy" mentions without specific project details
- Ammonia as fertilizer without CCS/clean production context
- Oil & gas operations that mention hydrogen incidentally

Filing:
Company: {entity_name}
Form: {form_type}
Date: {filed_at}
Excerpt: {excerpt}

Return ONLY JSON (no other text):
{{"relevant": true, "reason": "brief explanation", "project_name": "if identifiable or null", "company_normalized": "clean company name"}}
or
{{"relevant": false, "reason": "brief explanation"}}"""

# LLM query generation prompt (Strategy 5)
_EFTS_QUERY_GEN_PROMPT = """Based on these recently discovered blue hydrogen/ammonia filings, suggest 3-5 additional EFTS search queries that might find MORE relevant filings.

Recent discoveries:
{recent_discoveries}

RULES for query format:
- Use quoted phrases for exact matches: "blue hydrogen"
- Words without quotes are AND'd together: hydrogen "carbon capture" means both must appear
- NO boolean operators (no OR, AND, NOT, parentheses)
- Each query should be max ~5 words
- Focus on: company names seen, project names, technology terms, geographic terms

Return ONLY a JSON array of strings: ["query1", "query2", "query3"]"""

# ---------------------------------------------------------------------------
# EPA Broad Discovery (verified 2026-02-16)
# 2-step: get_facilities returns QID → get_qid returns facilities with pagesize
# ---------------------------------------------------------------------------
EPA_ECHO_FACILITIES = 'https://echodata.epa.gov/echo/echo_rest_services.get_facilities'
EPA_ECHO_QID = 'https://echodata.epa.gov/echo/echo_rest_services.get_qid'
EPA_SLEEP = 0.5
EPA_RETRY_COUNT = 3
EPA_RETRY_BACKOFF = [2, 4, 8]
EPA_PAGE_SIZE = 100
EPA_MAX_PAGES = 20            # 20 × 100 = 2000 facilities max per query

EPA_SIC_CODES = {
    '2813': 'Industrial Gases',
    '2819': 'Industrial Inorganic Chemicals NEC',
    '2873': 'Nitrogenous Fertilizers',
    '2911': 'Petroleum Refining',
}

EPA_NAICS_CODES = {
    '325120': 'Industrial Gas Manufacturing',
    '325311': 'Nitrogenous Fertilizer Manufacturing',
    '324110': 'Petroleum Refineries',
}

EPA_FACILITY_KEYWORDS = [
    '%hydrogen%', '%ammonia%', '%carbon capture%',
    '%syngas%', '%reformer%', '%reforming%',
]

EPA_GHG_SIC_COMBOS = ['2813', '2819', '2873', '2911']

_EPA_RELEVANCE_SYSTEM = (
    'You are a senior energy infrastructure analyst screening EPA facility records '
    'for relevance to blue hydrogen and ammonia production projects.'
)

_EPA_RELEVANCE_PROMPT = """Determine if this EPA facility is relevant to BLUE HYDROGEN or BLUE AMMONIA production, and assess its project stage.

INCLUDE (relevant = true):
- Hydrogen production facilities (SMR, ATR, or electrolysis)
- Ammonia production plants (especially with CCS/CCUS)
- Carbon capture facilities paired with hydrogen/ammonia
- Industrial gas facilities producing hydrogen at scale
- Petroleum refineries with dedicated hydrogen units
- Facilities with GHG reporting for hydrogen processes

EXCLUDE (relevant = false):
- General petroleum refineries with no hydrogen project signals
- Fertilizer plants with no clean production/CCS context
- Hydrogen peroxide, hydrogen bonds, fuel stations only

Facility: {facility_name}
Location: {city}, {state}
SIC: {sic_code} ({sic_desc})
Programs: {programs} (AIR=air permit, GHG=emissions reporting, CWA=water, RCRA=waste)
Compliance: {compliance_status}
Details: {detail_text}

Return ONLY JSON:
{{"relevant": true/false, "reason": "brief explanation",
  "company_normalized": "clean company name",
  "project_name": "specific project name or null",
  "stage": "Permitting|FEED|FID|Construction|Commissioning|Operational|Expanding|Unknown",
  "stage_confidence": "HIGH|MEDIUM|LOW",
  "stage_reasoning": "why this stage"}}"""

# ---------------------------------------------------------------------------
# DOE Broad Discovery (verified 2026-02-16)
# ---------------------------------------------------------------------------
USASPENDING_ENDPOINT = 'https://api.usaspending.gov/api/v2/search/spending_by_award/'
USASPENDING_SLEEP = 1.0
USASPENDING_RETRY_COUNT = 3
USASPENDING_RETRY_BACKOFF = [2, 4, 8]
DOE_INITIAL_LOOKBACK_DAYS = 365
DOE_SUBSEQUENT_LOOKBACK_DAYS = 30   # Awards move slowly

DOE_SPENDING_KEYWORDS = [
    'hydrogen', 'ammonia production', 'carbon capture',
    'clean hydrogen', 'blue hydrogen', 'hydrogen hub',
    'steam methane reforming', 'autothermal reforming', 'CCUS',
]

DOE_SPENDING_NAICS = ['325120', '325311', '324110']

# Grants and loans must be queried separately (API constraint)
DOE_GRANT_CODES = ['02', '03', '04', '05']   # Block/Formula/Project Grant, Cooperative Agreement
DOE_LOAN_CODES = ['07', '08']                 # Direct Loan, Guaranteed/Insured Loan

DOE_LPO_PAGES = [
    ('lpo-portfolio', 'https://www.energy.gov/lpo/articles/lpo-portfolio'),
    ('title-17', 'https://www.energy.gov/lpo/title-17'),
]

DOE_H2HUB_PAGES = [
    ('h2hub-selections', 'https://www.energy.gov/oced/regional-clean-hydrogen-hubs-selections-award-negotiations'),
]

DOE_PRESS_PAGES = [
    ('lpo-articles', 'https://www.energy.gov/lpo/articles'),
    ('oced-articles', 'https://www.energy.gov/oced/articles'),
    ('hfto-articles', 'https://www.energy.gov/eere/fuelcells/articles'),
]
DOE_SCRAPE_SLEEP = 2.0

USASPENDING_FIELDS = [
    'Award ID', 'Recipient Name', 'Award Amount', 'Total Outlays',
    'Description', 'Start Date', 'End Date', 'Awarding Agency',
    'Awarding Sub Agency', 'Award Type',
    'Place of Performance State Code', 'Place of Performance City Name',
]

_DOE_RELEVANCE_SYSTEM = (
    'You are a senior energy policy analyst screening DOE awards and federal funding '
    'for relevance to blue hydrogen and ammonia projects.'
)

_DOE_RELEVANCE_PROMPT = """Determine if this DOE award is relevant to BLUE HYDROGEN or BLUE AMMONIA, and assess project stage.

INCLUDE: LPO loans for H2/NH3, OCED hydrogen hub awards, CCS+H2 grants, SMR/ATR+CCS funding, 45V/45Q demos
EXCLUDE: Fuel cell vehicle grants, university research, fertilizer without CCS, generic clean energy

Award:
Recipient: {recipient_name}
Amount: ${amount}
Agency: {sub_agency}
Description: {description}
Location: {state}
Date: {start_date} to {end_date}
Type: {award_type}

Return ONLY JSON:
{{"relevant": true/false, "reason": "brief explanation",
  "company_normalized": "clean company name",
  "project_name": "specific project name or null",
  "stage": "Permitting|FEED|FID|Construction|Commissioning|Operational|Unknown",
  "stage_confidence": "HIGH|MEDIUM|LOW",
  "stage_reasoning": "why this stage"}}"""

# ---------------------------------------------------------------------------
# Federal Regulatory Filings (via regulations.gov, verified 2026-02-16)
# DEMO_KEY: 1000 req/hr, X-Ratelimit-Limit: 10 per window
# ---------------------------------------------------------------------------
REGSGOV_ENDPOINT = 'https://api.regulations.gov/v4/documents'
REGSGOV_API_KEY = 'DEMO_KEY'
REGSGOV_SLEEP = 6.5           # Conservative: ~9 req/min (under 10/min limit)
REGSGOV_RETRY_COUNT = 3
REGSGOV_RETRY_BACKOFF = [3, 6, 12]
REGSGOV_PAGE_SIZE = 25
REGSGOV_MAX_PAGES = 10
REGSGOV_INITIAL_LOOKBACK_DAYS = 365
REGSGOV_SUBSEQUENT_LOOKBACK_DAYS = 14

REGSGOV_AGENCIES = {
    'FERC': 'Federal Energy Regulatory Commission',
    'DOE': 'Department of Energy',
    'EERE': 'Energy Efficiency and Renewable Energy',
    'PHMSA': 'Pipeline and Hazardous Materials Safety',
    'EPA': 'Environmental Protection Agency',
}

# Strategy 1: Agency-specific keyword searches
REGSGOV_QUERIES = {
    'FERC': ['hydrogen', 'hydrogen pipeline', 'ammonia production',
             'clean hydrogen', 'hydrogen export', 'hydrogen storage'],
    'DOE': ['hydrogen hub', 'blue hydrogen', 'clean hydrogen production',
            'hydrogen production facility', 'ammonia carbon capture'],
    'EERE': ['hydrogen production', 'clean hydrogen', 'hydrogen hub'],
    'PHMSA': ['hydrogen pipeline', 'hydrogen transport', 'ammonia pipeline'],
    'EPA': ['"blue hydrogen"', '"hydrogen production" permit',
            '"ammonia production" "carbon capture"'],
}

# Strategy 2: Cross-agency broad keywords
REGSGOV_BROAD_QUERIES = [
    '"blue hydrogen"',
    '"clean hydrogen" production',
    '"hydrogen hub"',
    'hydrogen "final investment"',
    '"blue ammonia"',
]

_REGSGOV_RELEVANCE_SYSTEM = (
    'You are a senior energy regulatory analyst screening federal filings '
    'for relevance to blue hydrogen and ammonia project development.'
)

_REGSGOV_RELEVANCE_PROMPT = """Determine if this filing is relevant to BLUE HYDROGEN or BLUE AMMONIA, and assess project stage.

INCLUDE: Pipeline certificates for H2/NH3, EIS for H2 facilities, siting permits, FERC hydrogen orders, DOE H2Hub reviews, PHMSA H2 pipeline safety, EPA H2 production permits
EXCLUDE: Generic gas pipeline ops, unrelated rate cases, incidental hydrogen mentions

Filing:
Agency: {agency_id}
Title: {title}
Type: {document_type} / {subtype}
Date: {posted_date}
Docket: {docket_id}

Return ONLY JSON:
{{"relevant": true/false, "reason": "brief explanation",
  "company_normalized": "company name or null",
  "project_name": "project name or null",
  "stage": "Permitting|FEED|FID|Construction|Operational|Unknown",
  "stage_confidence": "HIGH|MEDIUM|LOW",
  "stage_reasoning": "why this stage"}}"""

# ---------------------------------------------------------------------------
# Shared Stage Assessment Prompt (used by all broad collectors)
# ---------------------------------------------------------------------------
_STAGE_ASSESSMENT_PROMPT = """Based on this {source_type} record, determine the project stage:

{context_text}

Stages:
- **Permitting**: Active permit applications, no operations
- **FEED**: Front-End Engineering Design in progress
- **FID**: Final Investment Decision announced or imminent
- **Construction**: Permits approved, facility under construction
- **Commissioning**: Construction complete, testing/startup
- **Operational**: Active H2/NH3 production
- **Expanding**: Existing facility adding H2/NH3 capacity
- **Unknown**: Insufficient information

Return ONLY JSON:
{{"stage": "...", "confidence": "HIGH|MEDIUM|LOW", "reasoning": "brief explanation", "key_indicators": [...]}}"""


# Status-based fallback probabilities (used when no evidence / no LLM)
STATUS_FID_DEFAULTS = {
    'Announced': 0.10, 'Pre-FEED': 0.20, 'FEED': 0.35,
    'FID': 0.70, 'EPC Award': 0.85, 'Construction': 0.90,
    'Commissioning': 0.95, 'Operational': 1.00,
    'Cancelled': 0.0, 'Delayed': 0.12,
}

# LLM system prompt for regulatory document analysis
_FID_SYSTEM_PROMPT = (
    'You are a senior energy infrastructure analyst specializing in hydrogen, '
    'ammonia, and clean energy project development. You analyze regulatory '
    'filings, permits, and government documents to determine the development '
    'stage and FID (Final Investment Decision) probability of energy projects. '
    'Be precise, evidence-based, and conservative in your assessments.'
)

# LLM assessment prompt template
_FID_ASSESSMENT_PROMPT = """You are assessing the development stage and FID probability for hydrogen/ammonia projects based on regulatory evidence.

COMPANY: {company_name}
REPORTED STATUS: {current_status}

REGULATORY EVIDENCE ({evidence_count} documents from {source_count} sources):

{evidence_blocks}

INSTRUCTIONS:
1. Read ALL evidence above. Multiple documents may refer to the same project — do NOT double-count. Synthesize across sources.
2. If evidence conflicts (e.g., FID announced then project delayed), use the MOST RECENT evidence as ground truth. Note the conflict in reasoning.
3. This company may have MULTIPLE hydrogen/ammonia projects. Provide a SEPARATE assessment for each distinct project you identify.

STAGE DEFINITIONS (use ONLY these):
- "Announced": Company mentioned project publicly, no engineering work confirmed
- "Pre-FEED": Feasibility studies ongoing, site selection, no binding commitments
- "FEED": Front-End Engineering Design contract awarded or underway
- "FID": Board approved construction, significant capital committed (look for: "reached FID", "sanctioned", "approved investment", "board approved")
- "EPC Award": EPC contract signed (look for: "awarded EPC", "selected contractor", "entered into EPC agreement"). If FID not explicitly mentioned, use this stage with probability 0.85
- "Construction": Active construction underway (look for: "broke ground", "commenced construction", "piling begun", "civil works", "notice to proceed")
- "Commissioning": Testing/startup phase (look for: "mechanical completion", "first gas", "commissioning", "pre-commissioning")
- "Operational": Producing product commercially (look for: "commenced operations", "first production", "commercial operations")
- "Cancelled": Project terminated (look for: "cancelled", "shelved", "abandoned", "write-down", "indefinitely delayed")

PROBABILITY GUIDELINES:
- Strong evidence from multiple sources = higher probability
- Single source or vague language = lower probability
- Government backing (DOE LPO) or active EPA permits = positive signal
- Old filings with no recent activity = negative signal

Respond in EXACTLY this JSON format (no other text):
[
  {{
    "project_name": "<specific project name if identifiable, otherwise 'Primary Hydrogen Project'>",
    "stage": "<stage from list above>",
    "fid_probability": <float 0.00-1.00>,
    "confidence": "<HIGH|MEDIUM|LOW>",
    "key_evidence": [
      "<most decisive evidence point #1>",
      "<most decisive evidence point #2>",
      "<most decisive evidence point #3>"
    ],
    "reasoning": "<2-4 sentence explanation>"
  }}
]"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _text_hash(text: str) -> str:
    """SHA-256 hash of text."""
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def _parse_llm_json(raw: str) -> Optional[list]:
    """Robust JSON array parser — handles markdown fences, prose, etc."""
    if not raw:
        return None
    # Strip markdown fences
    text = re.sub(r'```(?:json)?\s*', '', raw)
    text = text.strip().rstrip('`')
    # Try direct parse
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:
        pass
    # Strip stray backslashes the LLM sometimes appends to strings
    text_clean = text.replace('\\', '')
    try:
        obj = json.loads(text_clean)
        return obj if isinstance(obj, list) else [obj]
    except json.JSONDecodeError:
        pass
    # Find JSON array in text
    match = re.search(r'\[.*\]', text_clean, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    # Find JSON object in text
    match = re.search(r'\{.*\}', text_clean, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            return [obj]
        except json.JSONDecodeError:
            pass
    # Last resort: regex extraction
    stage_m = re.search(r'"stage"\s*:\s*"([^"]+)"', text)
    prob_m = re.search(r'"fid_probability"\s*:\s*([\d.]+)', text)
    if stage_m and prob_m:
        return [{
            'project_name': 'Primary Hydrogen Project',
            'stage': stage_m.group(1),
            'fid_probability': float(prob_m.group(1)),
            'confidence': 'LOW',
            'key_evidence': [],
            'reasoning': 'Parsed via regex fallback — review raw LLM response.'
        }]
    return None


_COMPANY_NAME_CACHE = {}


def _normalize_company_name(raw_name: str, use_llm: bool = True) -> str:
    """Normalize company name with LLM enhancement + regex fallback."""
    if not raw_name:
        return ''
    if raw_name in _COMPANY_NAME_CACHE:
        return _COMPANY_NAME_CACHE[raw_name]

    if use_llm:
        try:
            prompt = (f'Normalize this company name to canonical form:\n'
                      f'"{raw_name}"\n'
                      f'Rules:\n'
                      f'- Remove legal suffixes (Inc, LLC, Corp, Ltd, PLC, Holdings, etc.)\n'
                      f'- Standardize: & → and, Co. → Company\n'
                      f'- Use official name (e.g., "ExxonMobil" not "Exxon Mobil")\n'
                      f'- For subsidiaries, return parent if recognizable\n'
                      f'Return ONLY the normalized name, no explanation.')
            normalized = llm_client.generate(
                'You are a company name standardization expert.',
                prompt, max_tokens=30).strip().strip('"\'')
            if normalized and len(normalized) < 100:
                _COMPANY_NAME_CACHE[raw_name] = normalized
                return normalized
        except Exception:
            pass

    # Fallback: regex
    name = raw_name.strip()
    for suffix in _COMPANY_SUFFIXES:
        if name.lower().endswith(suffix.lower()):
            name = name[:-len(suffix)].strip()
    normalized = re.sub(r'\s+', ' ', name).strip()
    _COMPANY_NAME_CACHE[raw_name] = normalized
    return normalized


def _days_since(date_str: str) -> float:
    """Days since a date string (ISO format). Returns 9999 on parse failure."""
    try:
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return (datetime.now(dt.tzinfo) - dt).total_seconds() / 86400
    except Exception:
        try:
            dt = datetime.strptime(date_str[:10], '%Y-%m-%d')
            return (datetime.now() - dt).total_seconds() / 86400
        except Exception:
            return 9999.0


# ---------------------------------------------------------------------------
# FID Probability Engine
# ---------------------------------------------------------------------------

class FIDProbabilityEngine:
    """Fetches regulatory evidence, stores in DB, LLM-assesses FID probability."""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._checkpoint_path = str(Path(db_path).parent / '.efts_checkpoint.json')
        self.headers = {
            'User-Agent': 'Eco Decarb Forward (contact@ecodecarb.com)'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    # -- checkpoint helpers -------------------------------------------------

    def _save_checkpoint(self, state: dict):
        """Persist strategy progress to disk so interrupted runs can resume."""
        with open(self._checkpoint_path, 'w') as f:
            json.dump(state, f)

    def _load_checkpoint(self, lookback_days: int) -> Optional[dict]:
        """Load checkpoint if it matches the current lookback window."""
        try:
            with open(self._checkpoint_path) as f:
                state = json.load(f)
            if state.get('lookback_days') == lookback_days:
                return state
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        return None

    def _clear_checkpoint(self):
        """Remove checkpoint file after a successful full run."""
        try:
            Path(self._checkpoint_path).unlink(missing_ok=True)
        except OSError:
            pass

    # ===================================================================
    # SEC EDGAR — find CIK + fetch filing text
    # ===================================================================

    def _find_cik(self, company_name: str) -> Optional[str]:
        """Find SEC CIK number for a company."""
        try:
            url = 'https://efts.sec.gov/LATEST/search-index?q=%s&dateRange=custom&startdt=2023-01-01&forms=8-K,10-K' % requests.utils.quote(company_name)
            # Try the full-text search API first (more reliable)
            resp = self.session.get(
                'https://efts.sec.gov/LATEST/search-index',
                params={'q': company_name, 'forms': '8-K,10-K'},
                timeout=60)
            time.sleep(_SEC_SLEEP)
        except Exception:
            pass

        # Fallback: EDGAR company search
        try:
            resp = self.session.get(
                'https://www.sec.gov/cgi-bin/browse-edgar',
                params={'action': 'getcompany', 'company': company_name, 'output': 'xml'},
                timeout=60)
            if resp.status_code == 200:
                match = re.search(r'<CIK>(\d+)</CIK>', resp.text)
                if match:
                    return match.group(1)
            time.sleep(_SEC_SLEEP)
        except Exception as e:
            logger.error(f"CIK lookup error: {e}")
        return None

    def _fetch_sec_filing_text(self, cik: str, accession: str,
                               primary_doc: str) -> str:
        """Fetch and extract relevant sections from an SEC filing document.

        Returns structured text excerpt (max MAX_EXCERPT_CHARS).
        Handles .htm/.html, .txt, and falls back to raw truncation.
        """
        # Build URL: accession numbers have dashes, but the path uses no dashes
        acc_nodash = accession.replace('-', '')
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary_doc}"

        try:
            resp = self.session.get(url, timeout=60)
            time.sleep(_SEC_SLEEP)
            if resp.status_code != 200:
                logger.warning(f"SEC fetch {resp.status_code}: {url}")
                return ''

            raw = resp.text
            # Detect format
            if primary_doc.endswith(('.htm', '.html')):
                return self._parse_html_sections(raw)
            elif primary_doc.endswith('.txt'):
                return self._parse_text_sections(raw)
            else:
                # XBRL or unknown — extract raw text
                soup = BeautifulSoup(raw, 'html.parser')
                text = soup.get_text(separator='\n', strip=True)
                return self._extract_relevant_window(text)
        except Exception as e:
            logger.error(f"SEC filing fetch error: {e}")
            return ''

    def _parse_html_sections(self, html: str) -> str:
        """Extract prioritized sections from an HTML SEC filing."""
        soup = BeautifulSoup(html, 'html.parser')
        # Remove scripts, styles, signatures
        for tag in soup.find_all(['script', 'style']):
            tag.decompose()

        full_text = soup.get_text(separator='\n', strip=True)
        sections = []

        for pattern, priority in _SEC_SECTION_PATTERNS:
            for match in re.finditer(pattern, full_text):
                start = match.start()
                # Extract section: from header to next section header or 3000 chars
                end = start + 3000
                # Try to find next section header
                next_header = re.search(
                    r'\n(?:Item\s+\d|ITEM\s+\d|PART\s+[IVX]|SIGNATURES)',
                    full_text[start + len(match.group()):start + 5000])
                if next_header:
                    end = start + len(match.group()) + next_header.start()
                section_text = full_text[start:end].strip()
                if section_text:
                    sections.append((priority, section_text))

        if not sections:
            # No sections found — fall back to keyword windowing
            return self._extract_relevant_window(full_text)

        # Sort by priority, deduplicate overlapping sections
        sections.sort(key=lambda x: -x[0])
        result = ''
        for _prio, text in sections:
            if len(result) + len(text) > MAX_EXCERPT_CHARS:
                remaining = MAX_EXCERPT_CHARS - len(result)
                if remaining > 200:
                    result += '\n\n---\n\n' + text[:remaining]
                break
            result += '\n\n---\n\n' + text
        return result.strip()

    def _parse_text_sections(self, raw_text: str) -> str:
        """Extract sections from a plain-text SEC filing (ALL-CAPS headers)."""
        sections = []
        for pattern, priority in _SEC_SECTION_PATTERNS:
            # Also check ALL-CAPS versions for .txt filings
            upper_pattern = pattern.replace('(?i)', '')
            for pat in [pattern, upper_pattern]:
                for match in re.finditer(pat, raw_text, re.IGNORECASE):
                    start = match.start()
                    end = min(start + 3000, len(raw_text))
                    sections.append((priority, raw_text[start:end].strip()))

        if not sections:
            return self._extract_relevant_window(raw_text)

        sections.sort(key=lambda x: -x[0])
        result = ''
        for _prio, text in sections:
            if len(result) + len(text) > MAX_EXCERPT_CHARS:
                break
            result += '\n\n---\n\n' + text
        return result.strip()

    def _extract_relevant_window(self, text: str, window: int = 500) -> str:
        """Fallback: extract windows around H2 keywords."""
        text_lower = text.lower()
        windows = []
        used_ranges = []

        for kw in _H2_KEYWORDS:
            for match in re.finditer(re.escape(kw), text_lower):
                start = max(0, match.start() - window)
                end = min(len(text), match.end() + window)
                # Check overlap with existing windows
                overlaps = False
                for us, ue in used_ranges:
                    if start < ue and end > us:
                        overlaps = True
                        break
                if not overlaps:
                    windows.append(text[start:end])
                    used_ranges.append((start, end))

        result = '\n\n...\n\n'.join(windows)
        return result[:MAX_EXCERPT_CHARS]

    def search_sec_filings(self, company_name: str,
                           cik: Optional[str] = None) -> Dict:
        """Search SEC EDGAR, fetch filing text, store evidence."""
        logger.info(f"SEC EDGAR: searching {company_name}")
        results = {'company': company_name, 'cik': cik, 'filings_stored': 0}

        try:
            if not cik:
                cik = self._find_cik(company_name)
                results['cik'] = cik
            if not cik:
                logger.warning(f"No CIK found for {company_name}")
                return results

            cik_padded = cik.zfill(10)
            resp = self.session.get(
                f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
                timeout=60)
            time.sleep(_SEC_SLEEP)

            if resp.status_code != 200:
                return results

            data = resp.json()
            recent = data.get('filings', {}).get('recent', {})
            cutoff = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')

            # Collect filings, grouping by type for limits
            type_counts = {}
            type_limits = {'8-K': 3, '8-K/A': 2, '10-K': 1, '10-Q': 1,
                           'S-1': 1, 'S-4': 1}
            key_types = set(type_limits.keys())

            for i, form_type in enumerate(recent.get('form', [])):
                if form_type not in key_types:
                    continue
                filing_date = recent['filingDate'][i]
                if filing_date < cutoff:
                    continue

                count = type_counts.get(form_type, 0)
                if count >= type_limits.get(form_type, 2):
                    continue
                type_counts[form_type] = count + 1

                accession = recent['accessionNumber'][i]
                primary_doc = recent['primaryDocument'][i]

                # Amendment detection: 8-K/A supersedes 8-K with same base
                if form_type == '8-K/A':
                    self._mark_amended_stale(company_name, accession)

                # Fetch actual text
                text = self._fetch_sec_filing_text(cik, accession, primary_doc)
                if not text:
                    continue

                # Store evidence
                acc_nodash = accession.replace('-', '')
                doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{primary_doc}"
                self._store_evidence(
                    company_name=company_name,
                    source='sec_edgar',
                    document_type=form_type,
                    document_id=accession,
                    document_url=doc_url,
                    document_date=filing_date,
                    raw_text=text,
                    full_text=text,  # for hashing — same as excerpt for SEC
                    state='',
                    company_cik=cik)
                results['filings_stored'] += 1

            logger.info(f"SEC: stored {results['filings_stored']} filings for {company_name}")
        except Exception as e:
            logger.error(f"SEC search error: {e}")

        return results

    def _mark_amended_stale(self, company_name: str, amendment_accession: str):
        """Mark original filing as stale when an amendment (e.g. 8-K/A) arrives."""
        try:
            conn = sqlite3.connect(self.db_path)
            # The amendment's accession usually shares the base with the original
            base = amendment_accession[:amendment_accession.rfind('-')]
            conn.execute(
                "UPDATE regulatory_evidence SET stale=1, stale_reason='superseded_by_amendment' "
                "WHERE company_name=? AND source='sec_edgar' AND document_id LIKE ? "
                "AND document_type NOT LIKE '%/A'",
                (company_name, base + '%'))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Amendment stale-mark error: {e}")

    # ===================================================================
    # EFTS Broad Filing Discovery
    # ===================================================================

    def _parse_efts_hit(self, hit: dict) -> dict:
        """Extract filing info from an EFTS search hit."""
        source = hit.get('_source', {})
        id_parts = hit.get('_id', '').split(':')
        # Extract company name from display_names
        # e.g. "PLUG POWER INC  (PLUG)  (CIK 0001093691)"
        display = (source.get('display_names') or [''])[0]
        company_raw = re.sub(r'\s*\(.*?\)\s*', '', display).strip()
        return {
            'accession': id_parts[0] if id_parts else source.get('adsh', ''),
            'filename': id_parts[1] if len(id_parts) > 1 else '',
            'cik': (source.get('ciks') or [''])[0].lstrip('0'),
            'company_raw': company_raw,
            'company_normalized': _normalize_company_name(company_raw),
            'form_type': source.get('file_type', '') or (source.get('root_forms') or [''])[0],
            'filing_date': source.get('file_date', ''),
            'sics': source.get('sics', []),
            'items': source.get('items', []),
            'file_description': source.get('file_description', ''),
        }

    def _efts_search(self, query: str, start_date: str, end_date: str,
                     from_offset: int = 0) -> dict:
        """Single EFTS API call with retry + exponential backoff.

        Returns raw Elasticsearch JSON response dict.
        """
        params = {
            'q': query, 'forms': EFTS_FORM_TYPES,
            'dateRange': 'custom', 'startdt': start_date, 'enddt': end_date,
        }
        if from_offset > 0:
            params['from'] = str(from_offset)

        for attempt in range(EFTS_RETRY_COUNT):
            try:
                resp = self.session.get(EFTS_ENDPOINT, params=params, timeout=30)
                time.sleep(_SEC_SLEEP)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    wait = EFTS_RETRY_BACKOFF[attempt]
                    logger.warning(f"EFTS rate limited, sleeping {wait}s")
                    time.sleep(wait)
                    continue
                else:
                    logger.warning(f"EFTS HTTP {resp.status_code} for '{query}'")
                    if attempt < EFTS_RETRY_COUNT - 1:
                        time.sleep(EFTS_RETRY_BACKOFF[attempt])
                        continue
                    return {'hits': {'hits': [], 'total': {'value': 0}}}
            except requests.exceptions.Timeout:
                logger.warning(f"EFTS timeout for '{query}' (attempt {attempt + 1})")
                if attempt < EFTS_RETRY_COUNT - 1:
                    time.sleep(EFTS_RETRY_BACKOFF[attempt])
            except Exception as e:
                logger.error(f"EFTS error: {e}")
                if attempt < EFTS_RETRY_COUNT - 1:
                    time.sleep(EFTS_RETRY_BACKOFF[attempt])
        return {'hits': {'hits': [], 'total': {'value': 0}}}

    def _efts_search_all(self, query: str, start_date: str,
                         end_date: str) -> List[dict]:
        """Paginate through ALL EFTS results for a query (100 per page)."""
        all_hits: List[dict] = []
        offset = 0
        while True:
            data = self._efts_search(query, start_date, end_date,
                                     from_offset=offset)
            hits = data.get('hits', {}).get('hits', [])
            all_hits.extend(hits)
            total = data.get('hits', {}).get('total', {}).get('value', 0)
            offset += EFTS_PAGE_SIZE
            if offset >= total or not hits:
                break
        return all_hits

    @staticmethod
    def _generate_monthly_windows(lookback_days: int) -> List[tuple]:
        """Split lookback into ~30-day windows for EFTS queries."""
        from datetime import date
        windows: List[tuple] = []
        end = date.today()
        start_limit = end - timedelta(days=lookback_days)
        while end > start_limit:
            window_start = max(end - timedelta(days=30), start_limit)
            windows.append((window_start.isoformat(), end.isoformat()))
            end = window_start - timedelta(days=1)
        windows.reverse()
        return windows

    def _collect_from_rss(self, form_types: Optional[List[str]] = None) -> List[dict]:
        """RSS backup: fetch SEC getcurrent Atom feed, filter by H2 keywords."""
        import xml.etree.ElementTree as ET
        form_types = form_types or ['8-K', '10-K']
        filings: List[dict] = []
        for form_type in form_types:
            try:
                resp = self.session.get(
                    SEC_RSS_URL,
                    params={'action': 'getcurrent', 'type': form_type,
                            'count': '100', 'output': 'atom'},
                    timeout=60)
                time.sleep(_SEC_SLEEP)
                if resp.status_code != 200:
                    continue
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                root = ET.fromstring(resp.text)
                for entry in root.findall('atom:entry', ns):
                    title = entry.findtext('atom:title', '', ns)
                    link = entry.find('atom:link', ns)
                    summary = entry.findtext('atom:summary', '', ns)
                    href = link.get('href', '') if link is not None else ''
                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in RSS_COMPANY_KEYWORDS):
                        continue
                    # Parse title: "8-K - COMPANY NAME (CIK) (Filer)"
                    parts = title.split(' - ', 1)
                    company_raw = parts[1].split('(')[0].strip() if len(parts) > 1 else ''
                    cik_match = re.search(r'\((\d{7,10})\)', title)
                    cik = cik_match.group(1) if cik_match else ''
                    date_match = re.search(r'Filed:\s*(\d{4}-\d{2}-\d{2})', summary)
                    filing_date = date_match.group(1) if date_match else ''
                    acc_match = re.search(r'AccNo:\s*(\d{10}-\d{2}-\d{6})', summary)
                    accession = acc_match.group(1) if acc_match else ''
                    filings.append({
                        'accession': accession,
                        'cik': cik.lstrip('0'),
                        'company_raw': company_raw,
                        'company_normalized': _normalize_company_name(company_raw),
                        'form_type': form_type,
                        'filing_date': filing_date,
                        'filing_url': href,
                        'filename': '',
                        'sics': [], 'items': [],
                        'file_description': '',
                        '_strategy': 'rss',
                    })
            except Exception as e:
                logger.warning(f"RSS error for {form_type}: {e}")
        return filings

    def _efts_relevance_check(self, entity_name: str, form_type: str,
                              filed_at: str, excerpt: str) -> dict:
        """LLM relevance check for a single EFTS hit.

        Returns dict: {relevant, reason, project_name, company_normalized}
        """
        prompt = _EFTS_RELEVANCE_PROMPT.format(
            entity_name=entity_name, form_type=form_type,
            filed_at=filed_at, excerpt=excerpt[:2000])

        raw = llm_client.generate(_EFTS_RELEVANCE_SYSTEM, prompt, max_tokens=200)
        if not raw:
            # LLM unavailable — keyword fallback
            text_lower = excerpt.lower()
            has_h2 = any(kw in text_lower for kw in
                         ['blue hydrogen', 'blue ammonia', 'carbon capture',
                          'smr', 'atr', 'steam methane', 'autothermal',
                          'final investment decision', 'epc contract'])
            return {
                'relevant': has_h2,
                'reason': 'keyword-fallback',
                'project_name': None,
                'company_normalized': _normalize_company_name(entity_name),
            }

        # Try to parse JSON from response
        try:
            text = re.sub(r'```(?:json)?\s*', '', raw).strip().rstrip('`')
            r = json.loads(text)
            if isinstance(r, list):
                r = r[0]
            return {
                'relevant': r.get('relevant', False),
                'reason': r.get('reason', ''),
                'project_name': r.get('project_name'),
                'company_normalized': r.get('company_normalized',
                                            _normalize_company_name(entity_name)),
            }
        except (json.JSONDecodeError, IndexError, KeyError):
            # Try regex fallback
            rel_match = re.search(r'"relevant"\s*:\s*(true|false)', raw, re.I)
            return {
                'relevant': rel_match and rel_match.group(1).lower() == 'true',
                'reason': 'parse-fallback',
                'project_name': None,
                'company_normalized': _normalize_company_name(entity_name),
            }

    def _generate_llm_queries(self, recent_discoveries: List[str]) -> List[str]:
        """Strategy 5: Ask LLM to suggest additional EFTS queries."""
        if not recent_discoveries:
            return []
        discoveries_text = '\n'.join(f'- {d}' for d in recent_discoveries[:10])
        prompt = _EFTS_QUERY_GEN_PROMPT.format(recent_discoveries=discoveries_text)
        raw = llm_client.generate(_EFTS_RELEVANCE_SYSTEM, prompt, max_tokens=300)
        if not raw:
            return []
        try:
            text = re.sub(r'```(?:json)?\s*', '', raw).strip().rstrip('`')
            # Strip trailing backslashes the LLM sometimes appends
            text = text.replace('\\', '')
            queries = json.loads(text)
            if isinstance(queries, list):
                return [q.strip() for q in queries
                        if isinstance(q, str) and re.match(r'[a-zA-Z]', q.strip())
                        and len(q.strip()) > 3][:5]
        except json.JSONDecodeError:
            # Fallback: extract quoted strings, tolerating backslashes
            cleaned = raw.replace('\\', '')
            matches = re.findall(r'"([a-zA-Z][^"]{3,})"', cleaned)
            return [m.strip() for m in matches][:5]
        return []

    def _get_last_collection_run_by_type(self, run_type: str) -> Optional[str]:
        """Get finished_at of the last successful collection run for a given type."""
        try:
            conn = sqlite3.connect(self.db_path)
            row = conn.execute(
                "SELECT finished_at FROM collection_runs "
                "WHERE run_type=? AND error IS NULL "
                "ORDER BY finished_at DESC LIMIT 1",
                (run_type,)).fetchone()
            conn.close()
            return row[0] if row else None
        except Exception:
            return None

    def _get_last_collection_run(self) -> Optional[str]:
        """Get finished_at of the last successful EFTS collection run."""
        return self._get_last_collection_run_by_type('efts_sec')

    def _store_collection_run(self, run_data: dict) -> int:
        """Store a collection run record."""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute('''INSERT INTO collection_runs
                (run_type, started_at, finished_at, lookback_days,
                 queries_executed, filings_found, filings_relevant,
                 filings_new, new_companies_discovered, duration_seconds,
                 strategy_stats, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (run_data.get('run_type', 'efts_sec'),
                       run_data.get('started_at'),
                       run_data.get('finished_at'),
                       run_data.get('lookback_days'),
                       run_data.get('queries_executed', 0),
                       run_data.get('filings_found', 0),
                       run_data.get('filings_relevant', 0),
                       run_data.get('filings_new', 0),
                       run_data.get('new_companies_discovered', 0),
                       run_data.get('duration_seconds'),
                       json.dumps(run_data.get('strategy_stats', {})),
                       run_data.get('error')))
            row_id = c.lastrowid
            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Collection run store error: {e}")
            return -1

    def _load_existing_accessions(self) -> set:
        """Load already-stored accession numbers for dedup."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT document_id FROM regulatory_evidence "
                "WHERE source IN ('sec_edgar', 'sec_edgar_efts')"
            ).fetchall()
            conn.close()
            return {r[0] for r in rows if r[0]}
        except Exception:
            return set()

    def _load_known_companies(self) -> set:
        """Load normalized company names already in evidence table."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT DISTINCT company_name FROM regulatory_evidence"
            ).fetchall()
            conn.close()
            return {r[0].lower() for r in rows if r[0]}
        except Exception:
            return set()

    # ===================================================================
    # EPA ECHO — facility details
    # ===================================================================

    def search_epa_permits(self, company_name: str, state: str) -> Dict:
        """Search EPA ECHO for facilities, fetch details, store evidence."""
        logger.info(f"EPA ECHO: searching {company_name} in {state}")
        results = {'company': company_name, 'state': state, 'facilities_stored': 0}

        if not state:
            return results

        try:
            resp = self.session.get(
                'https://echodata.epa.gov/echo/rest_services.get_facilities',
                params={'output': 'JSON', 'p_fn': company_name,
                        'p_st': state, 'responseset': '20'},
                timeout=60)
            time.sleep(0.5)

            if resp.status_code != 200:
                return results

            data = resp.json()
            facilities = data.get('Results', {}).get('Results', [])

            for fac in facilities[:5]:
                registry_id = fac.get('RegistryID', '')
                if not registry_id:
                    continue

                # Fetch detailed facility info
                detail_text = self._fetch_epa_facility_details(registry_id)
                # Build summary from metadata + details
                fac_summary = (
                    f"Facility: {fac.get('FacName', 'Unknown')}\n"
                    f"Location: {fac.get('FacCity', '')}, {fac.get('FacState', '')}\n"
                    f"Programs: {fac.get('ActivePrograms', 'N/A')}\n"
                    f"Registry ID: {registry_id}\n"
                )
                if detail_text:
                    fac_summary += f"\nDetail:\n{detail_text}"

                self._store_evidence(
                    company_name=company_name,
                    source='epa_echo',
                    document_type='facility',
                    document_id=registry_id,
                    document_url=f"https://echo.epa.gov/detailed-facility-report?fid={registry_id}",
                    document_date=datetime.now().strftime('%Y-%m-%d'),
                    raw_text=fac_summary[:MAX_EXCERPT_CHARS],
                    full_text=fac_summary,
                    state=state)
                results['facilities_stored'] += 1

            logger.info(f"EPA: stored {results['facilities_stored']} facilities for {company_name}")
        except Exception as e:
            logger.error(f"EPA search error: {e}")

        return results

    def _fetch_epa_facility_details(self, registry_id: str) -> str:
        """Fetch detailed facility info from EPA ECHO."""
        try:
            resp = self.session.get(
                'https://echodata.epa.gov/echo/rest_services.get_facility_info',
                params={'p_id': registry_id, 'output': 'JSON'},
                timeout=60)
            time.sleep(0.5)

            if resp.status_code != 200:
                return ''

            data = resp.json()
            results = data.get('Results', {})
            # Extract useful fields
            parts = []
            for fac in results.get('Facilities', results.get('Results', [])):
                if isinstance(fac, dict):
                    for key in ['AIRFlag', 'CWAFlag', 'RCRAFlag', 'SDWAFlag',
                                'TRIFlag', 'GHGFlag', 'CurrSvFlag',
                                'QtrsWithNC', 'InspectionCount',
                                'FormalActionCount', 'LastInspectionDate']:
                        val = fac.get(key)
                        if val:
                            parts.append(f"{key}: {val}")
            return '\n'.join(parts)
        except Exception as e:
            logger.warning(f"EPA detail fetch error: {e}")
            return ''

    # ===================================================================
    # DOE LPO — page text extraction
    # ===================================================================

    def search_doe_lpo(self, company_name: str) -> Dict:
        """Search DOE LPO pages, extract context paragraphs, store evidence."""
        logger.info(f"DOE LPO: searching {company_name}")
        results = {'company': company_name, 'mentions_stored': 0}

        programs = [
            ('lpo-portfolio', 'https://www.energy.gov/lpo/articles/lpo-portfolio'),
            ('title-17', 'https://www.energy.gov/lpo/title-17'),
            ('atvm', 'https://www.energy.gov/lpo/advanced-technology-vehicles-manufacturing-atvm-loan-program'),
        ]

        for prog_name, url in programs:
            time.sleep(1)
            try:
                resp = self.session.get(url, timeout=60)
                if resp.status_code != 200:
                    continue
                if company_name.lower() not in resp.text.lower():
                    continue

                # Extract paragraphs around the company mention
                context = self._extract_paragraphs_around(
                    resp.text, company_name, n_context=2)

                self._store_evidence(
                    company_name=company_name,
                    source='doe_lpo',
                    document_type='lpo_mention',
                    document_id=f"lpo_{prog_name}",
                    document_url=url,
                    document_date=datetime.now().strftime('%Y-%m-%d'),
                    raw_text=context[:MAX_EXCERPT_CHARS],
                    full_text=context,
                    state='')
                results['mentions_stored'] += 1
                logger.info(f"DOE LPO: found {company_name} in {prog_name}")
            except Exception as e:
                logger.warning(f"DOE LPO error for {url}: {e}")

        return results

    def _extract_paragraphs_around(self, html: str, keyword: str,
                                   n_context: int = 2) -> str:
        """Extract paragraphs containing keyword + surrounding context."""
        soup = BeautifulSoup(html, 'html.parser')
        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p')]
        kw_lower = keyword.lower()
        matches = []
        for i, p in enumerate(paragraphs):
            if kw_lower in p.lower():
                start = max(0, i - n_context)
                end = min(len(paragraphs), i + n_context + 1)
                matches.extend(paragraphs[start:end])
        # Deduplicate while preserving order
        seen = set()
        result = []
        for p in matches:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return '\n\n'.join(result)

    # ===================================================================
    # State PUC (reference only — no text fetching)
    # ===================================================================

    def search_state_puc(self, company_name: str, state: str) -> Dict:
        """Provide PUC search URLs for manual lookup."""
        puc_configs = {
            'AL': ('Alabama PSC', 'https://www.psc.state.al.us/'),
            'AK': ('Regulatory Commission of Alaska', 'https://rca.alaska.gov/'),
            'AZ': ('Arizona Corporation Commission', 'https://www.azcc.gov/'),
            'AR': ('Arkansas PSC', 'https://www.apscservices.info/'),
            'CA': ('California PUC', 'https://docs.cpuc.ca.gov/SearchRes.aspx'),
            'CO': ('Colorado PUC', 'https://www.dora.state.co.us/pls/efi/EFI.homepage'),
            'CT': ('Connecticut PURA', 'https://www.ct.gov/pura/'),
            'DE': ('Delaware PSC', 'https://depsc.delaware.gov/'),
            'FL': ('Florida PSC', 'https://www.floridapsc.com/'),
            'GA': ('Georgia PSC', 'https://psc.ga.gov/'),
            'HI': ('Hawaii PUC', 'https://puc.hawaii.gov/'),
            'ID': ('Idaho PUC', 'https://www.puc.idaho.gov/'),
            'IL': ('Illinois Commerce Commission', 'https://www.icc.illinois.gov/'),
            'IN': ('Indiana URC', 'https://www.in.gov/iurc/'),
            'IA': ('Iowa Utilities Board', 'https://iub.iowa.gov/'),
            'KS': ('Kansas Corporation Commission', 'https://kcc.ks.gov/'),
            'KY': ('Kentucky PSC', 'https://psc.ky.gov/'),
            'LA': ('Louisiana PSC', 'https://www.lpsc.louisiana.gov/'),
            'ME': ('Maine PUC', 'https://www.maine.gov/mpuc/'),
            'MD': ('Maryland PSC', 'https://www.psc.state.md.us/'),
            'MA': ('Massachusetts DPU', 'https://www.mass.gov/orgs/department-of-public-utilities'),
            'MI': ('Michigan PSC', 'https://www.michigan.gov/mpsc'),
            'MN': ('Minnesota PUC', 'https://mn.gov/puc/'),
            'MS': ('Mississippi PSC', 'https://www.psc.ms.gov/'),
            'MO': ('Missouri PSC', 'https://www.psc.mo.gov/'),
            'MT': ('Montana PSC', 'https://psc.mt.gov/'),
            'NE': ('Nebraska Power Review Board', 'https://powerreview.nebraska.gov/'),
            'NV': ('Public Utilities Commission of Nevada', 'https://puc.nv.gov/'),
            'NH': ('New Hampshire PUC', 'https://www.puc.nh.gov/'),
            'NJ': ('New Jersey BPU', 'https://www.nj.gov/bpu/'),
            'NM': ('New Mexico PRC', 'https://www.nmprc.state.nm.us/'),
            'NY': ('New York PSC', 'https://documents.dps.ny.gov/'),
            'NC': ('North Carolina UC', 'https://www.ncuc.gov/'),
            'ND': ('North Dakota PSC', 'https://www.psc.nd.gov/'),
            'OH': ('Public Utilities Commission of Ohio', 'https://puco.ohio.gov/'),
            'OK': ('Oklahoma Corporation Commission', 'https://www.occeweb.com/'),
            'OR': ('Oregon PUC', 'https://www.oregon.gov/puc/'),
            'PA': ('Pennsylvania PUC', 'https://www.puc.pa.gov/'),
            'RI': ('Rhode Island PUC', 'https://ripuc.ri.gov/'),
            'SC': ('South Carolina PSC', 'https://www.psc.sc.gov/'),
            'SD': ('South Dakota PUC', 'https://puc.sd.gov/'),
            'TN': ('Tennessee PUC', 'https://www.tn.gov/tra.html'),
            'TX': ('Texas PUC', 'https://www.puc.texas.gov/'),
            'UT': ('Utah PSC', 'https://psc.utah.gov/'),
            'VT': ('Vermont PUC', 'https://puc.vermont.gov/'),
            'VA': ('Virginia SCC', 'https://www.scc.virginia.gov/'),
            'WA': ('Washington UTC', 'https://www.utc.wa.gov/'),
            'WV': ('West Virginia PSC', 'https://www.psc.state.wv.us/'),
            'WI': ('Public Service Commission of Wisconsin', 'https://psc.wi.gov/'),
            'WY': ('Wyoming PSC', 'https://psc.wyo.gov/'),
            'DC': ('DC PSC', 'https://dcpsc.org/'),
        }
        if state in puc_configs:
            name, url = puc_configs[state]
            return {'name': name, 'search_url': url, 'note': f'Manual search: {name}'}
        return {'note': f'PUC not configured for state: {state}'}

    # ===================================================================
    # Evidence storage layer
    # ===================================================================

    def _store_evidence(self, company_name: str, source: str,
                        document_type: str, document_id: str,
                        document_url: str, document_date: str,
                        raw_text: str, full_text: str = '',
                        state: str = '', company_cik: str = '',
                        discovery_method: str = '',
                        stage: str = '', stage_confidence: str = '',
                        stage_reasoning: str = '',
                        fid_date: str = '', construction_start: str = '',
                        operations_start: str = '',
                        milestones: str = '', project_name: str = '') -> int:
        """Store a piece of regulatory evidence. Returns row ID.

        Also cascades: marks existing fid_assessments for this company as stale.
        """
        excerpt = raw_text[:MAX_EXCERPT_CHARS]
        content_hash = _text_hash(excerpt)
        full_hash = _text_hash(full_text) if full_text else content_hash

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            c.execute('''INSERT OR REPLACE INTO regulatory_evidence
                (company_name, company_cik, source, document_type, document_id,
                 document_url, document_date, fetched_date, raw_text_excerpt,
                 full_text_hash, excerpt_char_count, content_hash, state,
                 stale, stale_reason, discovery_method,
                 stage, stage_confidence, stage_reasoning,
                 fid_date, construction_start, operations_start,
                 milestones, project_name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (company_name, company_cik or None, source, document_type,
                       document_id, document_url, document_date,
                       datetime.now().isoformat(), excerpt,
                       full_hash, len(excerpt), content_hash, state or None,
                       discovery_method or None,
                       stage or None, stage_confidence or None,
                       stage_reasoning or None,
                       fid_date or None, construction_start or None,
                       operations_start or None,
                       milestones or None, project_name or None))
            row_id = c.lastrowid

            # Cascade: mark assessments stale
            c.execute(
                "UPDATE fid_assessments SET stale=1, stale_reason='new_evidence_added' "
                "WHERE company_name=? AND stale=0",
                (company_name,))

            conn.commit()
            conn.close()
            return row_id
        except Exception as e:
            logger.error(f"Evidence store error: {e}")
            return -1

    def _load_evidence(self, company_name: str,
                       max_age_days: int = EVIDENCE_MAX_AGE_DAYS) -> List[Dict]:
        """Load non-stale evidence within freshness window."""
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM regulatory_evidence "
                "WHERE company_name=? AND stale=0 AND fetched_date>=? "
                "ORDER BY document_date DESC",
                (company_name, cutoff)).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error(f"Evidence load error: {e}")
            return []

    def batch_load_evidence(self, company_names: List[str],
                            max_age_days: int = EVIDENCE_MAX_AGE_DAYS
                            ) -> Dict[str, List[Dict]]:
        """Load non-stale evidence for multiple companies in one query.

        Returns dict keyed by company_name (lowercase) -> list of evidence dicts.
        Uses parameterized queries to prevent SQL injection.
        """
        if not company_names:
            return {}
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        result: Dict[str, List[Dict]] = {}
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            # Deduplicate and lowercase all names for matching
            unique_lowered = list(set(n.lower() for n in company_names if n))
            if not unique_lowered:
                conn.close()
                return {}
            placeholders = ','.join(['?'] * len(unique_lowered))
            rows = conn.execute(
                f"SELECT * FROM regulatory_evidence "
                f"WHERE LOWER(company_name) IN ({placeholders}) "
                f"AND stale=0 AND fetched_date>=? "
                f"ORDER BY document_date DESC",
                [*unique_lowered, cutoff]).fetchall()
            conn.close()
            for r in rows:
                d = dict(r)
                key = d['company_name'].lower()
                result.setdefault(key, []).append(d)
        except Exception as e:
            logger.error(f"Batch evidence load error: {e}")
        return result

    def _is_evidence_fresh(self, company_name: str) -> bool:
        """Check if we have recent non-stale evidence for this company."""
        cutoff = (datetime.now() - timedelta(days=EVIDENCE_MAX_AGE_DAYS)).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            count = conn.execute(
                "SELECT COUNT(*) FROM regulatory_evidence "
                "WHERE company_name=? AND stale=0 AND fetched_date>=?",
                (company_name, cutoff)).fetchone()[0]
            conn.close()
            return count > 0
        except Exception:
            return False

    # ===================================================================
    # Evidence ranking
    # ===================================================================

    def _rank_evidence(self, evidence: List[Dict]) -> List[Dict]:
        """Rank evidence by relevance: type priority × recency. Top 10."""
        for e in evidence:
            days = _days_since(e.get('document_date', ''))
            recency = max(0, 1.0 - (days / 365) * 0.3)  # recent = higher
            type_score = _TYPE_PRIORITY.get(e.get('document_type', ''), 0.3)
            e['_relevance'] = type_score * recency
        evidence.sort(key=lambda x: -x.get('_relevance', 0))
        return evidence[:10]

    # ===================================================================
    # LLM assessment
    # ===================================================================

    def _load_cached_assessment(self, company_name: str) -> Optional[List[Dict]]:
        """Load cached, non-stale LLM assessment if fresh."""
        cutoff = (datetime.now() - timedelta(days=ASSESSMENT_MAX_AGE_DAYS)).isoformat()
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM fid_assessments "
                "WHERE company_name=? AND stale=0 AND assessed_date>=? "
                "AND prompt_version>=?",
                (company_name, cutoff, PROMPT_VERSION)).fetchall()
            conn.close()
            if not rows:
                return None
            results = []
            for row in rows:
                r = dict(row)
                results.append({
                    'project_name': r.get('project_name', 'Primary Hydrogen Project'),
                    'probability': r.get('fid_probability', 0.0),
                    'stage': r.get('stage', 'Unknown'),
                    'confidence': r.get('confidence', 'LOW'),
                    'evidence_details': json.loads(r.get('key_evidence_points', '[]')),
                    'reasoning': r.get('reasoning', ''),
                    'source': 'fid_engine_cached',
                    'llm_backend': r.get('llm_backend', ''),
                    'evidence_count': len(json.loads(r.get('evidence_ids', '[]'))),
                })
            return results
        except Exception as e:
            logger.error(f"Assessment cache load error: {e}")
            return None

    def batch_load_assessments(self, company_names: List[str]
                               ) -> Dict[str, List[Dict]]:
        """Load cached, non-stale FID assessments for multiple companies.

        Returns dict keyed by company_name (lowercase) -> list of assessment dicts.
        Uses parameterized queries to prevent SQL injection.
        """
        if not company_names:
            return {}
        cutoff = (datetime.now() - timedelta(days=ASSESSMENT_MAX_AGE_DAYS)).isoformat()
        result: Dict[str, List[Dict]] = {}
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            unique_lowered = list(set(n.lower() for n in company_names if n))
            if not unique_lowered:
                conn.close()
                return {}
            placeholders = ','.join(['?'] * len(unique_lowered))
            rows = conn.execute(
                f"SELECT * FROM fid_assessments "
                f"WHERE LOWER(company_name) IN ({placeholders}) "
                f"AND stale=0 AND assessed_date>=? "
                f"AND prompt_version>=?",
                [*unique_lowered, cutoff, PROMPT_VERSION]).fetchall()
            conn.close()
            for row in rows:
                r = dict(row)
                key = r['company_name'].lower()
                result.setdefault(key, []).append({
                    'project_name': r.get('project_name', 'Primary Hydrogen Project'),
                    'probability': r.get('fid_probability', 0.0),
                    'stage': r.get('stage', 'Unknown'),
                    'confidence': r.get('confidence', 'LOW'),
                    'evidence_details': json.loads(r.get('key_evidence_points', '[]')),
                    'reasoning': r.get('reasoning', ''),
                    'source': 'fid_engine_cached',
                    'evidence_count': len(json.loads(r.get('evidence_ids', '[]'))),
                })
        except Exception as e:
            logger.error(f"Batch assessment load error: {e}")
        return result

    def _llm_assess_fid(self, company_name: str, evidence: List[Dict],
                        current_status: str = 'Announced') -> List[Dict]:
        """Send all evidence to LLM for unified stage determination.

        Returns list of per-project assessment dicts.
        """
        # Build evidence blocks
        source_types = set()
        evidence_blocks = ''
        evidence_ids = []
        for i, e in enumerate(evidence, 1):
            source_types.add(e.get('source', 'unknown'))
            evidence_ids.append(e.get('id', 0))
            evidence_blocks += (
                f"\n--- Evidence #{i} ---\n"
                f"Source: {e.get('source', '?')} ({e.get('document_type', '?')})\n"
                f"Date: {e.get('document_date', '?')}\n"
                f"URL: {e.get('document_url', 'N/A')}\n"
                f"Text:\n{e.get('raw_text_excerpt', '(no text)')}\n"
                f"---\n"
            )

        prompt = _FID_ASSESSMENT_PROMPT.format(
            company_name=company_name,
            current_status=current_status,
            evidence_count=len(evidence),
            source_count=len(source_types),
            evidence_blocks=evidence_blocks)

        # Call LLM
        backend = llm_client.check()
        raw_response = llm_client.generate(
            _FID_SYSTEM_PROMPT, prompt, max_tokens=2000)

        if not raw_response:
            logger.warning("LLM returned empty response for FID assessment")
            return []

        # Parse response
        assessments = _parse_llm_json(raw_response)
        if not assessments:
            logger.warning(f"Failed to parse LLM JSON: {raw_response[:200]}")
            return []

        # Store each assessment
        results = []
        for a in assessments:
            proj_name = a.get('project_name', 'Primary Hydrogen Project')
            stage = a.get('stage', 'Unknown')
            prob = float(a.get('fid_probability', 0.0))
            conf = a.get('confidence', 'LOW')
            key_ev = a.get('key_evidence', [])
            reasoning = a.get('reasoning', '')

            # Clamp probability
            prob = max(0.0, min(1.0, prob))

            # Store in DB
            try:
                conn = sqlite3.connect(self.db_path)
                conn.execute(
                    '''INSERT OR REPLACE INTO fid_assessments
                    (company_name, project_name, assessed_date, evidence_ids,
                     evidence_summary, llm_backend, llm_raw_response,
                     stage, fid_probability, confidence, key_evidence_points,
                     reasoning, stale, stale_reason, prompt_version,
                     llm_tokens_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, ?, ?)''',
                    (company_name, proj_name, datetime.now().isoformat(),
                     json.dumps(evidence_ids, sort_keys=True),
                     evidence_blocks[:2000],
                     backend, raw_response,
                     stage, prob, conf,
                     json.dumps(key_ev, sort_keys=True),
                     reasoning, PROMPT_VERSION,
                     llm_client.total_tokens))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"Assessment store error: {e}")

            results.append({
                'project_name': proj_name,
                'probability': prob,
                'stage': stage,
                'confidence': conf,
                'evidence_details': key_ev,
                'reasoning': reasoning,
                'source': 'fid_engine+llm',
                'llm_backend': backend,
                'evidence_count': len(evidence),
            })

        return results

    # ===================================================================
    # Shared LLM Extraction Methods (used by EPA, DOE, regulatory)
    # ===================================================================

    def _extract_stage_llm(self, text: str, source_type: str) -> dict:
        """LLM stage assessment. Falls back to keyword heuristics."""
        if not text or len(text) < 50:
            return {'stage': 'Unknown', 'confidence': 'LOW', 'reasoning': 'Insufficient text'}

        backend = llm_client.check()
        if backend:
            try:
                prompt = _STAGE_ASSESSMENT_PROMPT.format(
                    source_type=source_type, context_text=text[:2000])
                raw = llm_client.generate(
                    'You are a senior energy project stage analyst.', prompt, max_tokens=200)
                return json.loads(raw)
            except Exception:
                pass

        # Keyword fallback
        text_lower = text.lower()
        if any(k in text_lower for k in ['commissioning', 'startup', 'first production']):
            return {'stage': 'Commissioning', 'confidence': 'MEDIUM', 'reasoning': 'Keyword match'}
        if any(k in text_lower for k in ['under construction', 'construction began', 'construction started']):
            return {'stage': 'Construction', 'confidence': 'MEDIUM', 'reasoning': 'Keyword match'}
        if any(k in text_lower for k in ['final investment decision', 'fid', 'sanctioned']):
            return {'stage': 'FID', 'confidence': 'MEDIUM', 'reasoning': 'Keyword match'}
        if any(k in text_lower for k in ['feed study', 'feed complete', 'front-end engineering']):
            return {'stage': 'FEED', 'confidence': 'MEDIUM', 'reasoning': 'Keyword match'}
        if any(k in text_lower for k in ['permit application', 'applied for permit', 'environmental review']):
            return {'stage': 'Permitting', 'confidence': 'MEDIUM', 'reasoning': 'Keyword match'}
        if any(k in text_lower for k in ['operational', 'producing', 'production capacity']):
            return {'stage': 'Operational', 'confidence': 'MEDIUM', 'reasoning': 'Keyword match'}
        return {'stage': 'Unknown', 'confidence': 'LOW', 'reasoning': 'No stage keywords found'}

    def _extract_timeline_llm(self, text: str) -> dict:
        """Extract project timeline milestones from text."""
        if not text or len(text) < 100:
            return {}

        prompt = (f'Extract project timeline milestones from this text:\n\n'
                  f'{text[:1500]}\n\n'
                  f'Find dates/timeframes for:\n'
                  f'- FID (Final Investment Decision)\n'
                  f'- FEED completion\n'
                  f'- Construction start\n'
                  f'- Commissioning / Operations start\n'
                  f'- Permit deadlines\n\n'
                  f'Return JSON:\n'
                  f'{{"fid_date": "YYYY-QN or YYYY-MM or null",\n'
                  f'  "construction_start": "YYYY-QN or null",\n'
                  f'  "operations_start": "YYYY or null",\n'
                  f'  "milestones": [{{"event": "...", "date": "..."}}]}}\n'
                  f'If no timeline information, return {{}}.')

        backend = llm_client.check()
        if not backend:
            return {}
        try:
            raw = llm_client.generate(
                'You are a project timeline analyst.', prompt, max_tokens=300)
            return json.loads(raw)
        except Exception:
            return {}

    def _extract_project_name_llm(self, text: str, company: str, location: str = '') -> str:
        """Extract or construct meaningful project name from text."""
        if not text or len(text) < 50:
            return f"{company} {location} Facility" if location else f"{company} Project"

        prompt = (f'Extract the hydrogen/ammonia project name from this text:\n'
                  f'Company: {company}\nLocation: {location}\n'
                  f'Text: {text[:500]}\n\n'
                  f'Rules:\n'
                  f'- If a specific project name exists, return it\n'
                  f'- If none, construct: [Location] + [Type] (e.g., "Louisiana Blue Hydrogen Facility")\n'
                  f'- Keep concise (3-7 words), do NOT include company name\n'
                  f'Return ONLY the project name.')

        backend = llm_client.check()
        if backend:
            try:
                name = llm_client.generate(
                    'You are a project naming expert.', prompt, max_tokens=30).strip().strip('"\'')
                if name and len(name) < 100:
                    return name
            except Exception:
                pass
        return f"{location} Hydrogen Facility" if location else "Hydrogen Project"

    def _generate_content_hash(self, content: str) -> str:
        """Generate dedup hash from content string. Returns first 16 chars of SHA-256."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    # ===================================================================
    # Broad EFTS Filing Collection
    # ===================================================================

    def collect_filings(self) -> dict:
        """Broad EFTS filing discovery — scan ALL recent filings for H2/ammonia.

        Hybrid 6-strategy approach:
          1. Core keyword queries
          2. Extended technical/policy queries
          3. Capex signal queries
          4. NAICS/SIC industry queries
          5. LLM-generated queries (adaptive, needs 5+ discoveries)
          6. RSS feed backup

        Returns dict with run stats.  Supports resuming from checkpoint
        if a previous run was interrupted.
        """
        from datetime import date
        start_time = time.time()
        run_start = datetime.now().isoformat()

        # Determine lookback
        last_run = self._get_last_collection_run()
        lookback_days = EFTS_SUBSEQUENT_LOOKBACK_DAYS if last_run else EFTS_INITIAL_LOOKBACK_DAYS
        print(f"  EFTS: {'Incremental' if last_run else 'Initial'} run "
              f"(lookback: {lookback_days}d)")

        # Monthly windows for initial backfill, single window for incremental
        if lookback_days > 30:
            windows = self._generate_monthly_windows(lookback_days)
        else:
            windows = [(
                (date.today() - timedelta(days=lookback_days)).isoformat(),
                date.today().isoformat()
            )]
        print(f"  EFTS: {len(windows)} time window(s)")

        # --- Resume from checkpoint if available ---
        ckpt = self._load_checkpoint(lookback_days)
        if ckpt:
            seen_accessions: set = set(ckpt.get('seen_accessions', []))
            all_hits: List[dict] = ckpt.get('all_hits', [])
            strategy_stats: dict = ckpt.get('strategy_stats',
                                            {m: 0 for m in DISCOVERY_METHODS})
            queries_executed: int = ckpt.get('queries_executed', 0)
            completed_strategies: set = set(ckpt.get('completed_strategies', []))
            print(f"  Resuming from checkpoint — "
                  f"skipping {len(completed_strategies)} completed strategies: "
                  f"{', '.join(sorted(completed_strategies))}")
        else:
            seen_accessions = set()
            all_hits = []
            strategy_stats = {m: 0 for m in DISCOVERY_METHODS}
            queries_executed = 0
            completed_strategies = set()

        existing_accessions = self._load_existing_accessions()
        known_companies = self._load_known_companies()

        def _run_efts_queries(queries, strategy_name):
            nonlocal queries_executed
            count = 0
            for qi, query in enumerate(queries):
                print(f"    [{qi + 1}/{len(queries)}] {query}", end='', flush=True)
                for start_dt, end_dt in windows:
                    hits = self._efts_search_all(query, start_dt, end_dt)
                    queries_executed += 1
                    for hit in hits:
                        parsed = self._parse_efts_hit(hit)
                        acc = parsed['accession']
                        if acc and acc not in seen_accessions:
                            seen_accessions.add(acc)
                            parsed['_strategy'] = strategy_name
                            all_hits.append(parsed)
                            count += 1
                print(f" ({count} unique)")
            strategy_stats[strategy_name] = count
            return count

        def _checkpoint_after(strategy_key):
            """Save progress after each strategy so we can resume."""
            completed_strategies.add(strategy_key)
            self._save_checkpoint({
                'lookback_days': lookback_days,
                'seen_accessions': list(seen_accessions),
                'all_hits': all_hits,
                'strategy_stats': strategy_stats,
                'queries_executed': queries_executed,
                'completed_strategies': list(completed_strategies),
            })

        # Strategy 1: Core keywords
        if 'efts_core' not in completed_strategies:
            print(f"  Strategy 1/6: Core keywords ({len(EFTS_CORE_QUERIES)} queries)...")
            _run_efts_queries(EFTS_CORE_QUERIES, 'efts_core')
            _checkpoint_after('efts_core')
        else:
            print(f"  Strategy 1/6: Core keywords — cached ({strategy_stats.get('efts_core', 0)} hits)")

        # Strategy 2: Extended technical/policy
        if 'efts_extended' not in completed_strategies:
            print(f"  Strategy 2/6: Extended terms ({len(EFTS_EXTENDED_QUERIES)} queries)...")
            _run_efts_queries(EFTS_EXTENDED_QUERIES, 'efts_extended')
            _checkpoint_after('efts_extended')
        else:
            print(f"  Strategy 2/6: Extended terms — cached ({strategy_stats.get('efts_extended', 0)} hits)")

        # Strategy 3: Capex signals
        if 'efts_capex' not in completed_strategies:
            print(f"  Strategy 3/6: Capex signals ({len(EFTS_CAPEX_QUERIES)} queries)...")
            _run_efts_queries(EFTS_CAPEX_QUERIES, 'efts_capex')
            _checkpoint_after('efts_capex')
        else:
            print(f"  Strategy 3/6: Capex signals — cached ({strategy_stats.get('efts_capex', 0)} hits)")

        # Strategy 4: NAICS/SIC industry
        if 'efts_naics' not in completed_strategies:
            print(f"  Strategy 4/6: NAICS/SIC codes ({len(EFTS_NAICS_QUERIES)} queries)...")
            _run_efts_queries(EFTS_NAICS_QUERIES, 'efts_naics')
            _checkpoint_after('efts_naics')
        else:
            print(f"  Strategy 4/6: NAICS/SIC codes — cached ({strategy_stats.get('efts_naics', 0)} hits)")

        # Strategy 5: LLM-generated (only if 5+ new discoveries so far)
        if 'efts_llm' not in completed_strategies:
            relevant_so_far = [h for h in all_hits
                               if h['accession'] not in existing_accessions]
            if len(relevant_so_far) >= 5:
                print("  Strategy 5/6: LLM-generated queries...")
                summaries = [
                    f"{h['company_normalized']} ({h['form_type']}, {h['filing_date']})"
                    for h in relevant_so_far[:10]
                ]
                llm_queries = self._generate_llm_queries(summaries)
                if llm_queries:
                    print(f"    LLM suggested: {llm_queries}")
                    _run_efts_queries(llm_queries, 'efts_llm')
                else:
                    print("    LLM returned no queries")
            else:
                print(f"  Strategy 5/6: Skipped (need 5+ new discoveries, "
                      f"have {len(relevant_so_far)})")
            _checkpoint_after('efts_llm')
        else:
            print(f"  Strategy 5/6: LLM-generated — cached ({strategy_stats.get('efts_llm', 0)} hits)")

        # Strategy 6: RSS backup
        if 'rss' not in completed_strategies:
            print("  Strategy 6/6: RSS feed backup...")
            rss_filings = self._collect_from_rss()
            for rf in rss_filings:
                acc = rf['accession']
                if acc and acc not in seen_accessions:
                    seen_accessions.add(acc)
                    all_hits.append(rf)
                    strategy_stats['rss'] += 1
            print(f"    RSS: {strategy_stats['rss']} unique hits")
            _checkpoint_after('rss')
        else:
            print(f"  Strategy 6/6: RSS — cached ({strategy_stats.get('rss', 0)} hits)")

        total_hits = len(all_hits)
        print(f"\n  Total unique hits: {total_hits}")

        # Filter out already-stored accessions
        new_hits = [h for h in all_hits
                    if h['accession'] not in existing_accessions]
        print(f"  New (not in DB): {len(new_hits)} "
              f"(skipped {total_hits - len(new_hits)})")

        if not new_hits:
            run_data = {
                'run_type': 'efts_sec', 'started_at': run_start,
                'finished_at': datetime.now().isoformat(),
                'lookback_days': lookback_days,
                'queries_executed': queries_executed,
                'filings_found': total_hits, 'filings_relevant': 0,
                'filings_new': 0, 'new_companies_discovered': 0,
                'duration_seconds': time.time() - start_time,
                'strategy_stats': strategy_stats,
            }
            self._store_collection_run(run_data)
            self._clear_checkpoint()
            print("  No new filings to process.")
            return run_data

        # Process each new hit: fetch text → LLM relevance → store
        filings_relevant = 0
        filings_new = 0
        new_companies: set = set()

        for i, filing in enumerate(new_hits):
            if (i + 1) % 10 == 0:
                print(f"    Progress: {i + 1}/{len(new_hits)} processed...")

            print(f"    [{i + 1}/{len(new_hits)}] "
                  f"{filing['company_normalized'][:40]} | "
                  f"{filing['form_type']} | {filing['filing_date']} | "
                  f"{filing['_strategy']}")

            # Fetch filing text via existing method
            text = ''
            if filing['cik'] and filing['accession'] and filing['filename']:
                text = self._fetch_sec_filing_text(
                    filing['cik'], filing['accession'], filing['filename'])
            if not text:
                text = filing.get('file_description', '')

            # LLM relevance check
            relevance = self._efts_relevance_check(
                filing['company_raw'], filing['form_type'],
                filing['filing_date'], text[:2000])

            if not relevance.get('relevant', False):
                logger.debug(f"Filtered: {filing['company_normalized']} — "
                             f"{relevance.get('reason', '')}")
                continue

            filings_relevant += 1
            normalized = relevance.get('company_normalized',
                                       filing['company_normalized'])

            # Detect new companies
            if normalized.lower() not in known_companies:
                new_companies.add(normalized)
                known_companies.add(normalized.lower())

            # Amendment detection
            if filing['form_type'] in ('8-K/A',):
                self._mark_amended_stale(normalized, filing['accession'])

            # Build doc URL
            acc_nodash = filing['accession'].replace('-', '')
            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{filing['cik']}/{acc_nodash}/{filing['filename']}"
            )

            # Store evidence
            row_id = self._store_evidence(
                company_name=normalized,
                source='sec_edgar_efts',
                document_type=filing['form_type'],
                document_id=filing['accession'],
                document_url=doc_url,
                document_date=filing['filing_date'],
                raw_text=text[:MAX_EXCERPT_CHARS],
                full_text=text,
                company_cik=filing['cik'],
                discovery_method=filing['_strategy'])
            if row_id > 0:
                filings_new += 1

        # Log new companies
        if new_companies:
            print(f"\n  NEW companies discovered ({len(new_companies)}):")
            for c in sorted(new_companies):
                print(f"    + {c}")

        # Store collection run
        duration = time.time() - start_time
        run_data = {
            'run_type': 'efts_sec', 'started_at': run_start,
            'finished_at': datetime.now().isoformat(),
            'lookback_days': lookback_days,
            'queries_executed': queries_executed,
            'filings_found': total_hits,
            'filings_relevant': filings_relevant,
            'filings_new': filings_new,
            'new_companies_discovered': len(new_companies),
            'duration_seconds': duration,
            'strategy_stats': strategy_stats,
        }
        self._store_collection_run(run_data)
        self._clear_checkpoint()  # all strategies done — remove checkpoint

        print(f"\n  Collection complete: {filings_new} stored "
              f"({filings_relevant} relevant / {total_hits} found) "
              f"in {duration:.1f}s")
        return run_data

    # ===================================================================
    # Comprehensive search (fetch all sources + store evidence)
    # ===================================================================

    def comprehensive_search(self, company_name: str, state: str,
                             cik: Optional[str] = None) -> Dict:
        """Fetch evidence from all regulatory sources and store in DB."""
        logger.info(f"Comprehensive search: {company_name} ({state})")

        sec = self.search_sec_filings(company_name, cik)
        epa = self.search_epa_permits(company_name, state)
        lpo = self.search_doe_lpo(company_name)
        puc = self.search_state_puc(company_name, state)

        total = (sec.get('filings_stored', 0)
                 + epa.get('facilities_stored', 0)
                 + lpo.get('mentions_stored', 0))

        return {
            'company': company_name,
            'state': state,
            'search_date': datetime.now().isoformat(),
            'evidence_stored': total,
            'sec': sec,
            'epa': epa,
            'lpo': lpo,
            'puc': puc,
        }

    # ===================================================================
    # Broad EPA Permit Discovery
    # ===================================================================

    def _echo_search(self, params: dict) -> tuple:
        """EPA ECHO get_facilities → returns (QID, total_count)."""
        params['output'] = 'JSON'
        for attempt in range(EPA_RETRY_COUNT):
            try:
                resp = self.session.get(EPA_ECHO_FACILITIES, params=params, timeout=60)
                time.sleep(EPA_SLEEP)
                if resp.status_code == 200:
                    results = resp.json().get('Results', {})
                    return results.get('QueryID', ''), int(results.get('QueryRows', '0'))
                elif attempt < EPA_RETRY_COUNT - 1:
                    time.sleep(EPA_RETRY_BACKOFF[attempt])
            except Exception as e:
                logger.warning(f"ECHO search error: {e}")
                if attempt < EPA_RETRY_COUNT - 1:
                    time.sleep(EPA_RETRY_BACKOFF[attempt])
        return None, 0

    def _echo_get_page(self, qid: str, page: int) -> list:
        """EPA ECHO get_qid → returns facilities for a page."""
        for attempt in range(EPA_RETRY_COUNT):
            try:
                resp = self.session.get(EPA_ECHO_QID,
                    params={'output': 'JSON', 'qid': qid,
                            'pageno': str(page), 'pagesize': str(EPA_PAGE_SIZE)},
                    timeout=60)
                time.sleep(EPA_SLEEP)
                if resp.status_code == 200:
                    return resp.json().get('Results', {}).get('Facilities', [])
                elif attempt < EPA_RETRY_COUNT - 1:
                    time.sleep(EPA_RETRY_BACKOFF[attempt])
            except Exception as e:
                logger.warning(f"ECHO get_qid error: {e}")
                if attempt < EPA_RETRY_COUNT - 1:
                    time.sleep(EPA_RETRY_BACKOFF[attempt])
        return []

    def _echo_get_all(self, qid: str, total: int) -> list:
        """Paginate all EPA ECHO facilities for a QID."""
        all_facs = []
        pages = min((total + EPA_PAGE_SIZE - 1) // EPA_PAGE_SIZE, EPA_MAX_PAGES)
        for p in range(1, pages + 1):
            facs = self._echo_get_page(qid, p)
            all_facs.extend(facs)
            if not facs:
                break
        return all_facs

    def _parse_echo_facility(self, fac: dict) -> dict:
        """Normalize EPA ECHO facility response fields."""
        return {
            'registry_id': fac.get('RegistryID', ''),
            'facility_name': fac.get('FacName', ''),
            'company_normalized': _normalize_company_name(fac.get('FacName', '')),
            'city': fac.get('FacCity', ''),
            'state': fac.get('FacState', ''),
            'sic_codes': fac.get('FacSICCodes', ''),
            'naics_codes': fac.get('FacNAICSCodes', ''),
            'air_flag': fac.get('AIRFlag', ''),
            'ghg_flag': fac.get('GHGFlag', ''),
            'cwa_flag': fac.get('CWAFlag', ''),
            'rcra_flag': fac.get('RCRAFlag', ''),
            'tri_flag': fac.get('TRIFlag', ''),
            'compliance_status': fac.get('FacComplianceStatus', ''),
        }

    def _epa_relevance_check(self, facility: dict, detail_text: str) -> dict:
        """Combined LLM relevance + stage check for an EPA facility."""
        programs = []
        for flag, label in [('air_flag', 'AIR'), ('ghg_flag', 'GHG'),
                            ('cwa_flag', 'CWA'), ('rcra_flag', 'RCRA'),
                            ('tri_flag', 'TRI')]:
            if facility.get(flag, '') == 'Y':
                programs.append(label)

        sic = facility.get('sic_codes', '')
        sic_desc = ''
        for code, desc in EPA_SIC_CODES.items():
            if code in sic:
                sic_desc = desc
                break

        backend = llm_client.check()
        if backend:
            try:
                prompt = _EPA_RELEVANCE_PROMPT.format(
                    facility_name=facility['facility_name'],
                    city=facility['city'], state=facility['state'],
                    sic_code=sic, sic_desc=sic_desc,
                    programs=', '.join(programs) if programs else 'None',
                    compliance_status=facility.get('compliance_status', ''),
                    detail_text=detail_text[:1500])
                raw = llm_client.generate(_EPA_RELEVANCE_SYSTEM, prompt, max_tokens=250)
                result = json.loads(raw)
                return result
            except Exception:
                pass

        # Keyword fallback
        name_lower = facility['facility_name'].lower()
        if any(kw.strip('%') in name_lower for kw in ['hydrogen', 'ammonia', 'reformer', 'syngas']):
            return {'relevant': True, 'reason': 'Keyword match in facility name',
                    'company_normalized': facility['company_normalized'],
                    'project_name': None,
                    'stage': 'Unknown', 'stage_confidence': 'LOW',
                    'stage_reasoning': 'Keyword match only'}
        return {'relevant': False, 'reason': 'No H2/NH3 keywords found'}

    def _load_existing_epa_ids(self) -> set:
        """Load existing EPA registry IDs from DB for dedup."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT document_id FROM regulatory_evidence "
                "WHERE source IN ('epa_echo', 'epa_echo_broad')").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def collect_permits(self) -> dict:
        """Broad EPA ECHO facility discovery — scan by SIC/NAICS/keyword.

        4-strategy approach:
          1. SIC code scan (2813, 2819, 2873, 2911)
          2. NAICS code scan (325120, 325311, 324110)
          3. Facility name keywords (%hydrogen%, %ammonia%, etc.)
          4. GHG reporter + SIC cross-filter

        Returns dict with counts of facilities found/relevant/stored.
        """
        # Check freshness
        last_run = self._get_last_collection_run_by_type('epa_echo_broad')
        if last_run:
            last_dt = datetime.fromisoformat(last_run)
            if (datetime.now() - last_dt).days < 7:
                logger.info(f"EPA broad scan fresh (last: {last_run[:10]}), skipping")
                return {'status': 'fresh', 'filings_new': 0}

        run_start = datetime.now().isoformat()
        existing_ids = self._load_existing_epa_ids()
        all_facilities = {}  # registry_id → parsed facility
        strategy_counts = {}

        # Strategy 1: SIC code scan
        for sic_code, sic_desc in EPA_SIC_CODES.items():
            qid, total = self._echo_search({'p_sic': sic_code})
            strategy_counts[f'sic_{sic_code}'] = total
            if qid and total > 0:
                facs = self._echo_get_all(qid, total)
                for f in facs:
                    parsed = self._parse_echo_facility(f)
                    rid = parsed['registry_id']
                    if rid and rid not in all_facilities:
                        parsed['_strategy'] = f'epa_sic'
                        all_facilities[rid] = parsed
            logger.info(f"EPA SIC {sic_code} ({sic_desc}): {total} facilities")

        # Strategy 2: NAICS code scan
        for naics_code, naics_desc in EPA_NAICS_CODES.items():
            qid, total = self._echo_search({'p_naic': naics_code})
            strategy_counts[f'naics_{naics_code}'] = total
            if qid and total > 0:
                facs = self._echo_get_all(qid, total)
                for f in facs:
                    parsed = self._parse_echo_facility(f)
                    rid = parsed['registry_id']
                    if rid and rid not in all_facilities:
                        parsed['_strategy'] = 'epa_naics'
                        all_facilities[rid] = parsed
            logger.info(f"EPA NAICS {naics_code} ({naics_desc}): {total} facilities")

        # Strategy 3: Facility name keywords
        for keyword in EPA_FACILITY_KEYWORDS:
            qid, total = self._echo_search({'p_fn': keyword})
            strategy_counts[f'keyword_{keyword.strip("%")}'] = total
            if qid and total > 0:
                facs = self._echo_get_all(qid, total)
                for f in facs:
                    parsed = self._parse_echo_facility(f)
                    rid = parsed['registry_id']
                    if rid and rid not in all_facilities:
                        parsed['_strategy'] = 'epa_keyword'
                        all_facilities[rid] = parsed
            logger.info(f"EPA keyword '{keyword}': {total} facilities")

        # Strategy 4: GHG reporter + SIC cross-filter
        for sic_code in EPA_GHG_SIC_COMBOS:
            qid, total = self._echo_search({'p_sic': sic_code, 'p_ghg': 'Y'})
            strategy_counts[f'ghg_sic_{sic_code}'] = total
            if qid and total > 0:
                facs = self._echo_get_all(qid, total)
                for f in facs:
                    parsed = self._parse_echo_facility(f)
                    rid = parsed['registry_id']
                    if rid and rid not in all_facilities:
                        parsed['_strategy'] = 'epa_ghg_sic'
                        all_facilities[rid] = parsed
            logger.info(f"EPA GHG+SIC {sic_code}: {total} facilities")

        # Filter already-stored
        new_facilities = {rid: f for rid, f in all_facilities.items()
                          if rid not in existing_ids}

        logger.info(f"EPA broad: {len(all_facilities)} total, {len(new_facilities)} new")

        # Process each new facility
        stored = 0
        relevant = 0
        for rid, facility in new_facilities.items():
            # Fetch detail
            detail_text = self._fetch_epa_facility_details(rid)

            # Combined relevance + stage check
            check = self._epa_relevance_check(facility, detail_text)
            if not check.get('relevant', False):
                continue

            relevant += 1

            # Extract timeline
            combined_text = f"{facility['facility_name']} {detail_text}"
            timeline = self._extract_timeline_llm(combined_text)

            # Get project name from check result or extract
            proj_name = check.get('project_name') or self._extract_project_name_llm(
                combined_text,
                check.get('company_normalized', facility['company_normalized']),
                f"{facility['city']}, {facility['state']}")

            # Build summary text
            summary = (f"Facility: {facility['facility_name']}\n"
                       f"Location: {facility['city']}, {facility['state']}\n"
                       f"SIC: {facility['sic_codes']}\n"
                       f"Relevance: {check.get('reason', '')}\n"
                       f"Detail: {detail_text[:500]}")

            self._store_evidence(
                company_name=check.get('company_normalized', facility['company_normalized']),
                source='epa_echo_broad',
                document_type='facility',
                document_id=rid,
                document_url=f"https://echo.epa.gov/detailed-facility-report?fid={rid}",
                document_date=datetime.now().strftime('%Y-%m-%d'),
                raw_text=summary,
                full_text=detail_text,
                state=facility['state'],
                discovery_method=facility.get('_strategy', 'epa_keyword'),
                stage=check.get('stage', ''),
                stage_confidence=check.get('stage_confidence', ''),
                stage_reasoning=check.get('stage_reasoning', ''),
                fid_date=timeline.get('fid_date', ''),
                construction_start=timeline.get('construction_start', ''),
                operations_start=timeline.get('operations_start', ''),
                milestones=json.dumps(timeline.get('milestones', [])),
                project_name=proj_name or '')
            stored += 1

        # Store collection run
        self._store_collection_run({
            'run_type': 'epa_echo_broad',
            'started_at': run_start,
            'finished_at': datetime.now().isoformat(),
            'lookback_days': 0,  # EPA has no date filter
            'queries_executed': len(strategy_counts),
            'filings_found': len(all_facilities),
            'filings_relevant': relevant,
            'filings_new': stored,
            'new_companies_discovered': 0,
            'strategies_used': json.dumps(strategy_counts),
        })

        return {
            'status': 'completed',
            'facilities_total': len(all_facilities),
            'facilities_new': len(new_facilities),
            'filings_relevant': relevant,
            'filings_new': stored,
            'strategies': strategy_counts,
        }

    # ===================================================================
    # Broad DOE Award Discovery
    # ===================================================================

    def _usaspending_search(self, filters: dict, page: int = 1) -> dict:
        """Single USASpending API call with retry."""
        payload = {
            'filters': filters,
            'fields': USASPENDING_FIELDS,
            'page': page,
            'limit': 100,
            'sort': 'Award Amount',
            'order': 'desc',
        }
        # Loans use different sort field
        award_types = filters.get('award_type_codes', [])
        if set(award_types) & set(DOE_LOAN_CODES):
            payload['sort'] = 'Subsidy Cost'

        for attempt in range(USASPENDING_RETRY_COUNT):
            try:
                resp = self.session.post(USASPENDING_ENDPOINT,
                                         json=payload, timeout=30)
                time.sleep(USASPENDING_SLEEP)
                if resp.status_code == 200:
                    return resp.json()
                elif attempt < USASPENDING_RETRY_COUNT - 1:
                    time.sleep(USASPENDING_RETRY_BACKOFF[attempt])
            except Exception as e:
                logger.warning(f"USASpending error: {e}")
                if attempt < USASPENDING_RETRY_COUNT - 1:
                    time.sleep(USASPENDING_RETRY_BACKOFF[attempt])
        return {'results': [], 'page_metadata': {'hasNext': False}}

    def _usaspending_search_all(self, filters: dict) -> list:
        """Paginate USASpending results (safety cap 20 pages)."""
        all_results = []
        for page in range(1, 21):
            data = self._usaspending_search(filters, page)
            results = data.get('results', [])
            all_results.extend(results)
            if not data.get('page_metadata', {}).get('hasNext', False):
                break
            if not results:
                break
        return all_results

    def _scrape_doe_page_projects(self, url: str, page_name: str) -> list:
        """Scrape DOE page for H2/ammonia projects using LLM extraction."""
        try:
            resp = self.session.get(url, timeout=20)
            time.sleep(DOE_SCRAPE_SLEEP)
            if resp.status_code != 200:
                logger.warning(f"DOE page {url} returned {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, 'html.parser')
            content = soup.get_text(separator='\n', strip=True)

            backend = llm_client.check()
            if not backend:
                logger.warning("LLM unavailable for DOE page scraping")
                return []

            prompt = (f'Extract hydrogen/ammonia projects from this DOE page:\n'
                      f'URL: {url}\n'
                      f'Content (first 4000 chars):\n'
                      f'{content[:4000]}\n\n'
                      f'Find: company names, project names, locations, funding amounts, stages/timelines.\n\n'
                      f'Return JSON array:\n'
                      f'[{{"company": "...", "project_name": "...", "location": "...",\n'
                      f'   "funding_amount": "...", "description": "...",\n'
                      f'   "stage": "Permitting|FEED|FID|Construction|Operational|Unknown"}}]\n'
                      f'If none found, return [].')

            raw = llm_client.generate(
                'You are a DOE funding analyst extracting project data.', prompt, max_tokens=1500)
            projects = json.loads(raw)
            if not isinstance(projects, list):
                projects = [projects] if isinstance(projects, dict) else []
            for p in projects:
                p['source_url'] = url
                p['page_name'] = page_name
                p['document_id'] = self._generate_content_hash(
                    f"{p.get('company','')}|{p.get('project_name','')}|{p.get('location','')}")
            return projects
        except Exception as e:
            logger.warning(f"DOE scrape error for {url}: {e}")
            return []

    def _doe_relevance_check(self, award: dict) -> dict:
        """Combined relevance + stage check for a USASpending award."""
        backend = llm_client.check()
        if backend:
            try:
                prompt = _DOE_RELEVANCE_PROMPT.format(
                    recipient_name=award.get('Recipient Name', ''),
                    amount=award.get('Award Amount', '0'),
                    sub_agency=award.get('Awarding Sub Agency', ''),
                    description=award.get('Description', '')[:1000],
                    state=award.get('Place of Performance State Code', ''),
                    start_date=award.get('Start Date', ''),
                    end_date=award.get('End Date', ''),
                    award_type=award.get('Award Type', ''))
                raw = llm_client.generate(_DOE_RELEVANCE_SYSTEM, prompt, max_tokens=250)
                return json.loads(raw)
            except Exception:
                pass

        # Keyword fallback
        desc = (award.get('Description', '') or '').lower()
        if any(kw in desc for kw in ['hydrogen', 'ammonia', 'carbon capture', 'ccus']):
            return {'relevant': True, 'reason': 'Keyword match in description',
                    'company_normalized': _normalize_company_name(award.get('Recipient Name', '')),
                    'project_name': None,
                    'stage': 'Unknown', 'stage_confidence': 'LOW',
                    'stage_reasoning': 'Keyword match only'}
        return {'relevant': False, 'reason': 'No H2/NH3 keywords'}

    def _load_existing_doe_ids(self) -> set:
        """Load existing DOE document IDs from DB for dedup."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT document_id FROM regulatory_evidence "
                "WHERE source IN ('doe_lpo', 'doe_usaspending', 'doe_lpo_broad', 'doe_oced')").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def collect_doe_awards(self) -> dict:
        """Broad DOE award discovery via USASpending + DOE page scraping.

        5-strategy approach:
          1. USASpending keyword (DOE grants)
          2. USASpending NAICS (DOE grants)
          3. USASpending loans (separate query)
          4. DOE LPO/H2Hub page scrape
          5. DOE press page scrape

        Returns dict with counts of awards found/relevant/stored.
        """
        # Check freshness
        last_run = self._get_last_collection_run_by_type('doe_awards')
        if last_run:
            last_dt = datetime.fromisoformat(last_run)
            if (datetime.now() - last_dt).days < 7:
                logger.info(f"DOE awards fresh (last: {last_run[:10]}), skipping")
                return {'status': 'fresh', 'filings_new': 0}

        run_start = datetime.now().isoformat()
        existing_ids = self._load_existing_doe_ids()

        # Determine lookback
        prev_run = self._get_last_collection_run_by_type('doe_awards')
        lookback = DOE_INITIAL_LOOKBACK_DAYS if not prev_run else DOE_SUBSEQUENT_LOOKBACK_DAYS
        date_start = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')

        all_awards = {}  # Award ID → award dict
        strategy_counts = {}

        # Strategy 1: USASpending keyword (DOE grants)
        for keyword in DOE_SPENDING_KEYWORDS:
            filters = {
                'keywords': [keyword],
                'agencies': [{'type': 'awarding', 'tier': 'toptier', 'name': 'Department of Energy'}],
                'award_type_codes': DOE_GRANT_CODES,
                'time_period': [{'start_date': date_start, 'end_date': datetime.now().strftime('%Y-%m-%d')}],
            }
            results = self._usaspending_search_all(filters)
            strategy_counts[f'keyword_{keyword}'] = len(results)
            for r in results:
                aid = r.get('Award ID', '')
                if aid and aid not in all_awards:
                    r['_strategy'] = 'usaspending_keyword'
                    all_awards[aid] = r
            logger.info(f"DOE keyword '{keyword}': {len(results)} awards")

        # Strategy 2: USASpending NAICS (DOE grants)
        for naics in DOE_SPENDING_NAICS:
            filters = {
                'naics_codes': [{'naics_code': naics}],
                'agencies': [{'type': 'awarding', 'tier': 'toptier', 'name': 'Department of Energy'}],
                'award_type_codes': DOE_GRANT_CODES,
                'time_period': [{'start_date': date_start, 'end_date': datetime.now().strftime('%Y-%m-%d')}],
            }
            results = self._usaspending_search_all(filters)
            strategy_counts[f'naics_{naics}'] = len(results)
            for r in results:
                aid = r.get('Award ID', '')
                if aid and aid not in all_awards:
                    r['_strategy'] = 'usaspending_naics'
                    all_awards[aid] = r
            logger.info(f"DOE NAICS {naics}: {len(results)} awards")

        # Strategy 3: USASpending loans (separate query)
        for keyword in DOE_SPENDING_KEYWORDS[:4]:  # Top keywords only for loans
            filters = {
                'keywords': [keyword],
                'agencies': [{'type': 'awarding', 'tier': 'toptier', 'name': 'Department of Energy'}],
                'award_type_codes': DOE_LOAN_CODES,
                'time_period': [{'start_date': date_start, 'end_date': datetime.now().strftime('%Y-%m-%d')}],
            }
            results = self._usaspending_search_all(filters)
            strategy_counts[f'loan_{keyword}'] = len(results)
            for r in results:
                aid = r.get('Award ID', '')
                if aid and aid not in all_awards:
                    r['_strategy'] = 'usaspending_keyword'
                    all_awards[aid] = r
            logger.info(f"DOE loan '{keyword}': {len(results)} awards")

        # Filter already-stored (USASpending)
        new_awards = {aid: a for aid, a in all_awards.items()
                      if aid not in existing_ids}

        logger.info(f"DOE USASpending: {len(all_awards)} total, {len(new_awards)} new")

        # Process USASpending awards
        stored = 0
        relevant = 0
        for aid, award in new_awards.items():
            check = self._doe_relevance_check(award)
            if not check.get('relevant', False):
                continue
            relevant += 1

            desc = award.get('Description', '') or ''
            timeline = self._extract_timeline_llm(desc)

            proj_name = check.get('project_name') or self._extract_project_name_llm(
                desc,
                check.get('company_normalized', _normalize_company_name(award.get('Recipient Name', ''))),
                award.get('Place of Performance State Code', ''))

            summary = (f"Recipient: {award.get('Recipient Name', '')}\n"
                       f"Amount: ${award.get('Award Amount', 0):,.0f}\n"
                       f"Agency: {award.get('Awarding Sub Agency', '')}\n"
                       f"Type: {award.get('Award Type', '')}\n"
                       f"Description: {desc[:500]}")

            self._store_evidence(
                company_name=check.get('company_normalized',
                    _normalize_company_name(award.get('Recipient Name', ''))),
                source='doe_usaspending',
                document_type='award',
                document_id=aid,
                document_url=f"https://www.usaspending.gov/award/{aid}",
                document_date=award.get('Start Date', datetime.now().strftime('%Y-%m-%d')),
                raw_text=summary,
                state=award.get('Place of Performance State Code', ''),
                discovery_method=award.get('_strategy', 'usaspending_keyword'),
                stage=check.get('stage', ''),
                stage_confidence=check.get('stage_confidence', ''),
                stage_reasoning=check.get('stage_reasoning', ''),
                fid_date=timeline.get('fid_date', ''),
                construction_start=timeline.get('construction_start', ''),
                operations_start=timeline.get('operations_start', ''),
                milestones=json.dumps(timeline.get('milestones', [])),
                project_name=proj_name or '')
            stored += 1

        # Strategy 4: DOE LPO/H2Hub page scrape
        scraped_stored = 0
        for page_name, url in DOE_LPO_PAGES + DOE_H2HUB_PAGES:
            projects = self._scrape_doe_page_projects(url, page_name)
            strategy_counts[page_name] = len(projects)
            for p in projects:
                doc_id = p.get('document_id', '')
                if doc_id in existing_ids:
                    continue
                company = _normalize_company_name(p.get('company', ''))
                if not company:
                    continue
                source = 'doe_oced' if 'h2hub' in page_name else 'doe_lpo_broad'
                doc_type = 'h2hub_project' if 'h2hub' in page_name else 'lpo_project'

                summary = (f"Company: {p.get('company', '')}\n"
                           f"Project: {p.get('project_name', '')}\n"
                           f"Location: {p.get('location', '')}\n"
                           f"Funding: {p.get('funding_amount', '')}\n"
                           f"Description: {p.get('description', '')}")

                self._store_evidence(
                    company_name=company,
                    source=source,
                    document_type=doc_type,
                    document_id=doc_id,
                    document_url=url,
                    document_date=datetime.now().strftime('%Y-%m-%d'),
                    raw_text=summary,
                    discovery_method=f'doe_{page_name.replace("-", "_")}_scrape',
                    stage=p.get('stage', ''),
                    project_name=p.get('project_name', ''))
                scraped_stored += 1

        # Strategy 5: DOE press page scrape
        for page_name, url in DOE_PRESS_PAGES:
            projects = self._scrape_doe_page_projects(url, page_name)
            strategy_counts[page_name] = len(projects)
            for p in projects:
                doc_id = p.get('document_id', '')
                if doc_id in existing_ids:
                    continue
                company = _normalize_company_name(p.get('company', ''))
                if not company:
                    continue

                summary = (f"Company: {p.get('company', '')}\n"
                           f"Project: {p.get('project_name', '')}\n"
                           f"Description: {p.get('description', '')}")

                self._store_evidence(
                    company_name=company,
                    source='doe_lpo_broad',
                    document_type='press_release',
                    document_id=doc_id,
                    document_url=url,
                    document_date=datetime.now().strftime('%Y-%m-%d'),
                    raw_text=summary,
                    discovery_method='doe_press_scrape',
                    stage=p.get('stage', ''),
                    project_name=p.get('project_name', ''))
                scraped_stored += 1

        total_stored = stored + scraped_stored

        # Store collection run
        self._store_collection_run({
            'run_type': 'doe_awards',
            'started_at': run_start,
            'finished_at': datetime.now().isoformat(),
            'lookback_days': lookback,
            'queries_executed': len(strategy_counts),
            'filings_found': len(all_awards) + sum(
                strategy_counts.get(p, 0) for p, _ in DOE_LPO_PAGES + DOE_H2HUB_PAGES + DOE_PRESS_PAGES),
            'filings_relevant': relevant + scraped_stored,
            'filings_new': total_stored,
            'new_companies_discovered': 0,
            'strategies_used': json.dumps(strategy_counts),
        })

        return {
            'status': 'completed',
            'usaspending_total': len(all_awards),
            'usaspending_new': len(new_awards),
            'usaspending_relevant': relevant,
            'usaspending_stored': stored,
            'scraped_stored': scraped_stored,
            'filings_new': total_stored,
            'strategies': strategy_counts,
        }

    # ===================================================================
    # Broad Federal Regulatory Filing Discovery (regulations.gov)
    # ===================================================================

    def _regsgov_search(self, params: dict) -> dict:
        """Single regulations.gov API call with retry and rate limiting."""
        params['api_key'] = REGSGOV_API_KEY
        params['page[size]'] = str(REGSGOV_PAGE_SIZE)
        for attempt in range(REGSGOV_RETRY_COUNT):
            try:
                resp = self.session.get(REGSGOV_ENDPOINT, params=params, timeout=20)
                time.sleep(REGSGOV_SLEEP)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    wait = REGSGOV_RETRY_BACKOFF[attempt]
                    logger.warning(f"regulations.gov rate limited, sleeping {wait}s")
                    time.sleep(wait)
                    continue
                elif attempt < REGSGOV_RETRY_COUNT - 1:
                    time.sleep(REGSGOV_RETRY_BACKOFF[attempt])
            except Exception as e:
                logger.warning(f"regulations.gov error: {e}")
                if attempt < REGSGOV_RETRY_COUNT - 1:
                    time.sleep(REGSGOV_RETRY_BACKOFF[attempt])
        return {'data': [], 'meta': {'totalElements': 0, 'hasNextPage': False}}

    def _regsgov_search_all(self, params: dict) -> list:
        """Paginate regulations.gov results (cap at REGSGOV_MAX_PAGES)."""
        all_docs = []
        for page_num in range(1, REGSGOV_MAX_PAGES + 1):
            params['page[number]'] = str(page_num)
            data = self._regsgov_search(params)
            docs = data.get('data', [])
            all_docs.extend(docs)
            if not docs:
                break
            meta = data.get('meta', {})
            if not meta.get('hasNextPage', False):
                break
        return all_docs

    def _parse_regsgov_doc(self, doc: dict) -> dict:
        """Extract key fields from a regulations.gov document."""
        attrs = doc.get('attributes', {})
        return {
            'object_id': doc.get('id', attrs.get('objectId', '')),
            'title': attrs.get('title', ''),
            'agency_id': attrs.get('agencyId', ''),
            'document_type': attrs.get('documentType', ''),
            'subtype': attrs.get('subtype', ''),
            'posted_date': (attrs.get('postedDate', '') or '')[:10],
            'docket_id': attrs.get('docketId', ''),
            'highlighted_content': attrs.get('highlightedContent', ''),
        }

    def _regsgov_relevance_check(self, parsed_doc: dict) -> dict:
        """Combined relevance + stage check for a regulations.gov document."""
        backend = llm_client.check()
        if backend:
            try:
                prompt = _REGSGOV_RELEVANCE_PROMPT.format(
                    agency_id=parsed_doc['agency_id'],
                    title=parsed_doc['title'],
                    document_type=parsed_doc['document_type'],
                    subtype=parsed_doc['subtype'],
                    posted_date=parsed_doc['posted_date'],
                    docket_id=parsed_doc['docket_id'])
                raw = llm_client.generate(_REGSGOV_RELEVANCE_SYSTEM, prompt, max_tokens=250)
                return json.loads(raw)
            except Exception:
                pass

        # Keyword fallback
        title_lower = parsed_doc['title'].lower()
        if any(kw in title_lower for kw in ['hydrogen', 'ammonia', 'carbon capture']):
            return {'relevant': True, 'reason': 'Keyword match in title',
                    'company_normalized': None, 'project_name': None,
                    'stage': 'Unknown', 'stage_confidence': 'LOW',
                    'stage_reasoning': 'Keyword match only'}
        return {'relevant': False, 'reason': 'No H2/NH3 keywords'}

    def _load_existing_regsgov_ids(self) -> set:
        """Load existing regulations.gov object IDs from DB for dedup."""
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT document_id FROM regulatory_evidence "
                "WHERE source LIKE 'regsgov_%'").fetchall()
            conn.close()
            return {r[0] for r in rows}
        except Exception:
            return set()

    def collect_regulatory_filings(self) -> dict:
        """Broad federal regulatory filing discovery via regulations.gov.

        2-strategy approach:
          1. Agency-specific keyword searches (FERC, DOE, EERE, PHMSA, EPA)
          2. Cross-agency broad keyword searches

        Returns dict with counts of filings found/relevant/stored.
        """
        # Check freshness
        last_run = self._get_last_collection_run_by_type('regsgov')
        if last_run:
            last_dt = datetime.fromisoformat(last_run)
            if (datetime.now() - last_dt).days < 7:
                logger.info(f"Regulations.gov fresh (last: {last_run[:10]}), skipping")
                return {'status': 'fresh', 'filings_new': 0}

        run_start = datetime.now().isoformat()
        existing_ids = self._load_existing_regsgov_ids()

        # Determine lookback
        prev_run = self._get_last_collection_run_by_type('regsgov')
        lookback = REGSGOV_INITIAL_LOOKBACK_DAYS if not prev_run else REGSGOV_SUBSEQUENT_LOOKBACK_DAYS
        date_start = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')

        all_docs = {}  # object_id → parsed doc
        strategy_counts = {}

        # Strategy 1: Agency-specific keyword searches
        for agency_id, agency_name in REGSGOV_AGENCIES.items():
            keywords = REGSGOV_QUERIES.get(agency_id, [])
            for keyword in keywords:
                params = {
                    'filter[searchTerm]': keyword,
                    'filter[agencyId]': agency_id,
                    'filter[postedDate][ge]': date_start,
                    'sort': '-postedDate',
                }
                docs = self._regsgov_search_all(params)
                strat_key = f'{agency_id}_{keyword[:20]}'
                strategy_counts[strat_key] = len(docs)
                for d in docs:
                    parsed = self._parse_regsgov_doc(d)
                    oid = parsed['object_id']
                    if oid and oid not in all_docs:
                        parsed['_strategy'] = f'regsgov_{agency_id.lower()}'
                        all_docs[oid] = parsed
                logger.info(f"RegsGov {agency_id} '{keyword}': {len(docs)} docs")

        # Strategy 2: Cross-agency broad keywords
        for keyword in REGSGOV_BROAD_QUERIES:
            params = {
                'filter[searchTerm]': keyword,
                'filter[postedDate][ge]': date_start,
                'sort': '-postedDate',
            }
            docs = self._regsgov_search_all(params)
            strat_key = f'broad_{keyword[:20]}'
            strategy_counts[strat_key] = len(docs)
            for d in docs:
                parsed = self._parse_regsgov_doc(d)
                oid = parsed['object_id']
                if oid and oid not in all_docs:
                    parsed['_strategy'] = 'regsgov_broad'
                    all_docs[oid] = parsed
            logger.info(f"RegsGov broad '{keyword}': {len(docs)} docs")

        # Filter already-stored
        new_docs = {oid: d for oid, d in all_docs.items()
                    if oid not in existing_ids}

        logger.info(f"RegsGov: {len(all_docs)} total, {len(new_docs)} new")

        # Process each new document
        stored = 0
        relevant = 0
        for oid, parsed_doc in new_docs.items():
            check = self._regsgov_relevance_check(parsed_doc)
            if not check.get('relevant', False):
                continue
            relevant += 1

            # Extract timeline from title + highlighted content
            combined_text = f"{parsed_doc['title']} {parsed_doc.get('highlighted_content', '')}"
            timeline = self._extract_timeline_llm(combined_text)

            proj_name = check.get('project_name') or ''

            # Map document_type to our schema
            doc_type_map = {
                'Rule': 'regulatory_notice',
                'Proposed Rule': 'regulatory_notice',
                'Notice': 'regulatory_notice',
                'Supporting & Related Material': 'regulatory_notice',
                'Other': 'regulatory_notice',
            }
            doc_type = doc_type_map.get(parsed_doc['document_type'], 'regulatory_notice')
            if parsed_doc['agency_id'] == 'FERC':
                doc_type = 'ferc_filing'

            summary = (f"Title: {parsed_doc['title']}\n"
                       f"Agency: {parsed_doc['agency_id']}\n"
                       f"Type: {parsed_doc['document_type']} / {parsed_doc['subtype']}\n"
                       f"Date: {parsed_doc['posted_date']}\n"
                       f"Docket: {parsed_doc['docket_id']}\n"
                       f"Relevance: {check.get('reason', '')}")

            company = check.get('company_normalized', '') or ''

            self._store_evidence(
                company_name=company,
                source=parsed_doc.get('_strategy', f'regsgov_{parsed_doc["agency_id"].lower()}'),
                document_type=doc_type,
                document_id=oid,
                document_url=f"https://www.regulations.gov/document/{oid}",
                document_date=parsed_doc['posted_date'],
                raw_text=summary,
                discovery_method=parsed_doc.get('_strategy', 'regsgov_broad'),
                stage=check.get('stage', ''),
                stage_confidence=check.get('stage_confidence', ''),
                stage_reasoning=check.get('stage_reasoning', ''),
                fid_date=timeline.get('fid_date', ''),
                construction_start=timeline.get('construction_start', ''),
                operations_start=timeline.get('operations_start', ''),
                milestones=json.dumps(timeline.get('milestones', [])),
                project_name=proj_name)
            stored += 1

        # Store collection run
        self._store_collection_run({
            'run_type': 'regsgov',
            'started_at': run_start,
            'finished_at': datetime.now().isoformat(),
            'lookback_days': lookback,
            'queries_executed': len(strategy_counts),
            'filings_found': len(all_docs),
            'filings_relevant': relevant,
            'filings_new': stored,
            'new_companies_discovered': 0,
            'strategies_used': json.dumps(strategy_counts),
        })

        return {
            'status': 'completed',
            'documents_total': len(all_docs),
            'documents_new': len(new_docs),
            'filings_relevant': relevant,
            'filings_new': stored,
            'strategies': strategy_counts,
        }

    # ===================================================================
    # Cross-Source Evidence Synthesis
    # ===================================================================

    def _format_evidence_summary(self, items: list, max_items: int = 5) -> str:
        """Format evidence items for synthesis prompt."""
        if not items:
            return '  (none)'
        lines = []
        for e in items[:max_items]:
            stage_str = f" | Stage: {e.get('stage', '?')}" if e.get('stage') else ''
            lines.append(f"  - [{e.get('document_type', '?')}] {e.get('document_date', '?')}: "
                         f"{e.get('raw_text_excerpt', '')[:200]}...{stage_str}")
        if len(items) > max_items:
            lines.append(f"  ... and {len(items) - max_items} more")
        return '\n'.join(lines)

    def _synthesize_company_evidence(self, company_name: str) -> dict:
        """LLM synthesizes evidence from all sources into project assessments."""
        evidence = self._load_evidence(company_name)
        if not evidence:
            return {'projects': [], 'overall_assessment': 'No evidence found.'}

        sec_items = [e for e in evidence if 'sec' in (e.get('source', '') or '')]
        epa_items = [e for e in evidence if 'epa' in (e.get('source', '') or '')]
        doe_items = [e for e in evidence if 'doe' in (e.get('source', '') or '')]
        reg_items = [e for e in evidence if 'regsgov' in (e.get('source', '') or '')]

        prompt = (f'Synthesize hydrogen/ammonia project evidence for {company_name}:\n\n'
                  f'SEC FILINGS ({len(sec_items)}):\n'
                  f'{self._format_evidence_summary(sec_items)}\n\n'
                  f'EPA FACILITIES ({len(epa_items)}):\n'
                  f'{self._format_evidence_summary(epa_items)}\n\n'
                  f'DOE AWARDS ({len(doe_items)}):\n'
                  f'{self._format_evidence_summary(doe_items)}\n\n'
                  f'REGULATORY FILINGS ({len(reg_items)}):\n'
                  f'{self._format_evidence_summary(reg_items)}\n\n'
                  f'Tasks:\n'
                  f'1. Identify distinct projects (deduplicate across sources)\n'
                  f'2. For each project, determine stage and FID probability\n'
                  f'3. Cross-reference evidence (e.g., SEC FID + EPA permit = strong signal)\n'
                  f'4. Identify risks and recent momentum\n\n'
                  f'Return JSON:\n'
                  f'{{"projects": [\n'
                  f'    {{"project_name": "...", "location": "...",\n'
                  f'      "stage": "...", "fid_probability": 0.0-1.0,\n'
                  f'      "fid_date": "YYYY-QN or null",\n'
                  f'      "evidence_sources": ["source_docid", ...],\n'
                  f'      "key_evidence": ["...", "..."],\n'
                  f'      "risks": ["...", "..."],\n'
                  f'      "momentum": "Positive|Negative|Neutral"}}\n'
                  f'  ],\n'
                  f'  "overall_assessment": "1-2 sentence summary"}}')

        try:
            raw = llm_client.generate(
                'You are a senior energy project analyst with expertise in hydrogen infrastructure.',
                prompt, max_tokens=2000)
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Synthesis failed for {company_name}: {e}")
            return {'projects': [], 'overall_assessment': f'Synthesis failed: {e}'}

    # ===================================================================
    # Main entry point: assess_project
    # ===================================================================

    def assess_project(self, company_name: str, state: str,
                       project_name: str = '',
                       status: str = 'Announced',
                       cik: Optional[str] = None) -> Dict:
        """One-call method: check cache → fetch evidence → LLM assess → return.

        Returns dict with keys:
            probability, stage, confidence, evidence_details, reasoning,
            source, llm_backend, evidence_count
        For multi-project companies, returns assessment for the first/best match.
        Full list available via assess_project_multi().
        """
        # 1. Check cached assessment
        cached = self._load_cached_assessment(company_name)
        if cached:
            # If project_name specified, try to match
            if project_name:
                for c in cached:
                    if project_name.lower() in c.get('project_name', '').lower():
                        logger.info(f"Using cached assessment for {company_name}/{project_name}")
                        return c
            logger.info(f"Using cached assessment for {company_name}")
            return cached[0]

        # 2. Fetch per-company EPA/DOE evidence if not fresh
        #    (Broad discovery comes from collect_permits/collect_doe_awards/collect_regulatory_filings)
        if not self._is_evidence_fresh(company_name):
            print(f"    Fetching EPA/DOE evidence for {company_name}...")
            self.search_epa_permits(company_name, state)
            self.search_doe_lpo(company_name)

        # 3. Load and synthesize evidence across all sources
        if not self._is_evidence_fresh(company_name):
            logger.info(f"Evidence stale for {company_name} — run collect_all to refresh")

        synthesis = self._synthesize_company_evidence(company_name)
        evidence = self._load_evidence(company_name)

        if not evidence:
            # No evidence found — distinct from LLM failure
            default_prob = STATUS_FID_DEFAULTS.get(status, 0.10)
            if status == 'Cancelled':
                default_prob = 0.0
            if status == 'Operational':
                default_prob = 1.0
            return {
                'project_name': project_name or 'Primary Hydrogen Project',
                'probability': default_prob,
                'stage': status,
                'confidence': 'NONE',
                'evidence_details': ['No regulatory filings or permits found in public databases'],
                'reasoning': (f'No SEC EDGAR, EPA, DOE, or regulatory evidence found for {company_name}. '
                              f'Using status-based default ({status} → {default_prob:.0%}).'),
                'source': 'no_evidence_found',
                'llm_backend': '',
                'evidence_count': 0,
            }

        ranked = self._rank_evidence(evidence)

        # 4. LLM assessment (enriched with synthesis context)
        assessments = self._llm_assess_fid(company_name, ranked,
                                           current_status=status)

        # Enrich assessments with synthesis data
        if synthesis.get('projects'):
            for a in (assessments or []):
                for sp in synthesis['projects']:
                    if (a.get('project_name', '').lower() in sp.get('project_name', '').lower()
                            or sp.get('project_name', '').lower() in a.get('project_name', '').lower()):
                        if sp.get('momentum'):
                            a['momentum'] = sp['momentum']
                        if sp.get('risks'):
                            a['risks'] = sp['risks']
                        if sp.get('fid_date') and not a.get('fid_date'):
                            a['fid_date'] = sp['fid_date']
                        break

        if not assessments:
            # LLM unavailable — return default with evidence count
            default_prob = STATUS_FID_DEFAULTS.get(status, 0.10)
            return {
                'project_name': project_name or 'Primary Hydrogen Project',
                'probability': default_prob,
                'stage': status,
                'confidence': 'LOW',
                'evidence_details': [f'{len(evidence)} evidence items found but LLM unavailable'],
                'reasoning': (f'Found {len(evidence)} regulatory documents but LLM assessment '
                              f'failed. Using status-based default ({status} → {default_prob:.0%}).'),
                'source': 'fid_engine_default',
                'llm_backend': '',
                'evidence_count': len(evidence),
            }

        # 5. Return best match
        if project_name:
            for a in assessments:
                if project_name.lower() in a.get('project_name', '').lower():
                    return a
        return assessments[0]

    def assess_project_multi(self, company_name: str, state: str,
                             status: str = 'Announced',
                             cik: Optional[str] = None) -> List[Dict]:
        """Like assess_project but returns ALL project assessments for a company."""
        # Same flow as assess_project, but returns full list
        cached = self._load_cached_assessment(company_name)
        if cached:
            return cached

        if not self._is_evidence_fresh(company_name):
            self.search_epa_permits(company_name, state)
            self.search_doe_lpo(company_name)

        # Synthesize cross-source evidence
        synthesis = self._synthesize_company_evidence(company_name)
        evidence = self._load_evidence(company_name)
        if not evidence:
            return [{
                'project_name': 'Primary Hydrogen Project',
                'probability': STATUS_FID_DEFAULTS.get(status, 0.10),
                'stage': status,
                'confidence': 'NONE',
                'evidence_details': ['No regulatory evidence found'],
                'reasoning': 'No evidence in public databases.',
                'source': 'no_evidence_found',
                'llm_backend': '',
                'evidence_count': 0,
            }]

        ranked = self._rank_evidence(evidence)
        assessments = self._llm_assess_fid(company_name, ranked,
                                           current_status=status)

        # Enrich with synthesis
        if assessments and synthesis.get('projects'):
            for a in assessments:
                for sp in synthesis['projects']:
                    if (a.get('project_name', '').lower() in sp.get('project_name', '').lower()
                            or sp.get('project_name', '').lower() in a.get('project_name', '').lower()):
                        if sp.get('momentum'):
                            a['momentum'] = sp['momentum']
                        if sp.get('risks'):
                            a['risks'] = sp['risks']
                        if sp.get('fid_date') and not a.get('fid_date'):
                            a['fid_date'] = sp['fid_date']
                        break

        return assessments if assessments else [{
            'project_name': 'Primary Hydrogen Project',
            'probability': STATUS_FID_DEFAULTS.get(status, 0.10),
            'stage': status,
            'confidence': 'LOW',
            'evidence_details': [f'{len(evidence)} evidence items, LLM unavailable'],
            'reasoning': 'LLM assessment failed.',
            'source': 'fid_engine_default',
            'llm_backend': '',
            'evidence_count': len(evidence),
        }]

    # ===================================================================
    # Export utility
    # ===================================================================

    def export_results(self, results: Dict, filename: str = None) -> str:
        """Export results to JSON file."""
        if not filename:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            company = results.get('company', 'unknown').replace(' ', '_')
            filename = f"fid_search_{company}_{ts}.json"
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2, default=str)
        logger.info(f"Exported to {filename}")
        return filename


# ===================================================================
# CLI testing
# ===================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(name)s %(levelname)s %(message)s')

    engine = FIDProbabilityEngine()

    # Run assessment
    company = "Plug Power"
    state = "NY"
    print(f"\n{'='*60}")
    print(f"FID ASSESSMENT: {company} ({state})")
    print(f"{'='*60}")

    result = engine.assess_project(company, state, status='Announced')

    print(f"\nProject:     {result.get('project_name')}")
    print(f"Stage:       {result.get('stage')}")
    print(f"Probability: {result.get('probability', 0):.0%}")
    print(f"Confidence:  {result.get('confidence')}")
    print(f"Source:      {result.get('source')}")
    print(f"Backend:     {result.get('llm_backend')}")
    print(f"Evidence:    {result.get('evidence_count')} documents")

    if result.get('evidence_details'):
        print(f"\nKey Evidence:")
        for e in result['evidence_details']:
            print(f"  - {e}")

    if result.get('reasoning'):
        print(f"\nReasoning: {result['reasoning']}")
