#!/usr/bin/env python3
"""
News → Structured Market Signal Parser
=======================================
Parses collected articles into structured ProjectSignal objects,
normalizes capacities, classifies technology/status, and deduplicates
into a persistent projects table.

Fixes applied (v2):
  1. Real project name extraction from titles (not just generic inference)
  2. Smarter dedup — absorbs empty-region signals, capacity-mismatch safeguard
  3. Uses full_text when available for richer extraction
  4. Context-aware status detection (hedge/confirm/negate words)
  5. Multi-developer / JV handling
  6. Three-layer confidence model (completeness + article quality + corroboration)
  7. Capacity unit disambiguation (H2 vs NH3 context proximity)

Fixes applied (v3 — Option 4 gaps):
  8.  Known-project database for canonical naming & dedup anchoring
  9.  Nuclear SMR vs hydrogen SMR disambiguation
  10. Capacity validation (realistic ranges, known-project cross-check)
  11. Word-boundary matching for short status keywords (fid, feed)
  12. Relevance filter — skip generic signals without project specifics
  13. Known-project technology override (ExxonMobil Baytown = ATR, etc.)
  14. Better dedup — known-project anchoring, Unknown-tech absorption

Usage:
    from news_market_signal_parser import NewsMarketSignalParser
    parser = NewsMarketSignalParser()
    signals = parser.parse_recent(days=7)
    projects = parser.deduplicate_to_projects(signals)
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
DATABASE_NAME = str(_HERE / 'blue_h2_intelligence.db')

_log = logging.getLogger(__name__)


def _parse_any_date(date_str: str) -> Optional[datetime]:
    """Parse a date string in any common format (RFC 2822, ISO, etc.) to datetime.

    Returns None if unparseable.
    """
    if not date_str or not date_str.strip():
        return None
    # Try ISO first (fast, no imports)
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00').split('+')[0].split('Z')[0])
    except (ValueError, AttributeError):
        pass
    # Try RFC 2822 (email format from RSS feeds)
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        pass
    # Try dateutil as last resort
    try:
        from dateutil import parser as dp
        return dp.parse(date_str, fuzzy=True).replace(tzinfo=None)
    except Exception:
        return None

# ---------------------------------------------------------------------------
# OPTIONAL NLP DEPENDENCIES  (graceful fallback to regex-only if not installed)
# ---------------------------------------------------------------------------
# Required:  pip3 install spacy && python3 -m spacy download en_core_web_lg
#            pip3 install sentence-transformers   (pulls in torch ~800MB)
# Total footprint: ~1.5 GB (torch + spaCy model + MiniLM)
# ---------------------------------------------------------------------------

_SPACY_AVAILABLE = False
_SBERT_AVAILABLE = False

try:
    import spacy
    _SPACY_AVAILABLE = True
except ImportError:
    spacy = None  # type: ignore[assignment]

try:
    from sentence_transformers import SentenceTransformer, util as sbert_util
    _SBERT_AVAILABLE = True
except ImportError:
    SentenceTransformer = None  # type: ignore[assignment,misc]
    sbert_util = None  # type: ignore[assignment]


class _NLPEngine:
    """Lazy-loading wrapper for spaCy and sentence-transformers models.

    Both models are loaded on first use, not at import time, so the parser
    still works (regex-only) when the NLP libraries are absent.
    """

    _spacy_nlp = None
    _sbert_model = None
    _embedding_cache: Dict[str, object] = {}  # project_name -> tensor

    @classmethod
    def spacy(cls):
        """Return the spaCy Language pipeline (en_core_web_lg)."""
        if cls._spacy_nlp is None:
            if not _SPACY_AVAILABLE:
                return None
            try:
                cls._spacy_nlp = spacy.load('en_core_web_lg')
                _log.info("spaCy en_core_web_lg loaded")
            except OSError:
                _log.warning(
                    "spaCy model 'en_core_web_lg' not found. "
                    "Run: python3 -m spacy download en_core_web_lg")
                return None
        return cls._spacy_nlp

    @classmethod
    def sbert(cls):
        """Return the sentence-transformers model (all-MiniLM-L6-v2)."""
        if cls._sbert_model is None:
            if not _SBERT_AVAILABLE:
                return None
            try:
                cls._sbert_model = SentenceTransformer('all-MiniLM-L6-v2')
                _log.info("SentenceTransformer all-MiniLM-L6-v2 loaded")
            except Exception as exc:
                _log.warning("Failed to load sentence-transformers model: %s", exc)
                return None
        return cls._sbert_model

    @classmethod
    def embed(cls, text: str):
        """Return embedding vector for *text*, cached."""
        if text in cls._embedding_cache:
            return cls._embedding_cache[text]
        model = cls.sbert()
        if model is None:
            return None
        vec = model.encode(text, convert_to_tensor=True)
        cls._embedding_cache[text] = vec
        return vec

    @classmethod
    def cosine_sim(cls, a_text: str, b_text: str) -> Optional[float]:
        """Cosine similarity between two strings via sentence-transformers."""
        va = cls.embed(a_text)
        vb = cls.embed(b_text)
        if va is None or vb is None:
            return None
        return float(sbert_util.cos_sim(va, vb)[0][0])


# ---------------------------------------------------------------------------
# spaCy NER helper
# ---------------------------------------------------------------------------

def _spacy_extract(text: str) -> Dict[str, List[str]]:
    """Run spaCy NER on *text* and return entities grouped by label.

    Returns dict with keys: ORG, GPE, MONEY, DATE, PERSON, LOC.
    Each value is a deduplicated list of entity strings.
    Falls back to empty dict if spaCy is unavailable.
    """
    nlp = _NLPEngine.spacy()
    if nlp is None:
        return {}
    # Truncate to 100k chars to avoid spaCy's memory issues on huge text
    doc = nlp(text[:100_000])
    result: Dict[str, List[str]] = {}
    for ent in doc.ents:
        label = ent.label_
        if label in ('ORG', 'GPE', 'MONEY', 'DATE', 'PERSON', 'LOC'):
            result.setdefault(label, [])
            # Deduplicate within each label
            if ent.text not in result[label]:
                result[label].append(ent.text)
    return result

# ---------------------------------------------------------------------------
# LITERATURE-SOURCED CONSTANTS
# ---------------------------------------------------------------------------

# Capacity conversions — all normalized to MTPA H2 equivalent
# Source: IEA "The Future of Hydrogen" (2019), Section 2.3
NH3_TO_H2_MASS_RATIO = 0.178  # H2 is 17.8% of NH3 by mass (stoichiometric)

# Source: IRENA "Green Hydrogen Cost Reduction" (2020)
SMR_CAPACITY_FACTOR = 0.95
ELECTROLYSIS_CAPACITY_FACTOR = 0.50  # mid-range for electrolysis

# Gas feedstock: MTPA H2 -> bcf/d incremental gas demand
# Source: NETL "Assessment of Hydrogen Production with CO2 Capture Vol 1" (2010), Table 3-1
# SMR: ~56 MMBtu/tonne H2 (feed + fuel) -> 0.155 bcf/d per MTPA
GAS_DEMAND_BCFD_PER_MTPA_SMR = 0.155

# Source: IEAGHG "Techno-Economic Evaluation of SMR Based Standalone H2 Plant with CCS" (2017)
# ATR: ~49 MMBtu/tonne H2 -> 0.135 bcf/d per MTPA
GAS_DEMAND_BCFD_PER_MTPA_ATR = 0.135

# Status -> FID completion probability (default fallback values)
# These are overridden per-project by FIDProbabilityEngine when available
STATUS_FID_PROBABILITY = {
    'Announced':      0.10,
    'Pre-FEED':       0.20,
    'FEED':           0.35,
    'FID':            0.70,
    'EPC Award':      0.85,
    'Construction':   0.90,
    'Commissioning':  0.95,
    'Operational':    1.00,
    'Cancelled':      0.00,
    'Delayed':        0.12,
    'Unknown':        0.10,
}

# Equipment lead times (months from FID)
# Source: Wood Mackenzie "Blue Hydrogen Guide" (2023); Technip Energies project data
EQUIPMENT_LEAD_TIMES = {
    'reformer_order':      {'SMR': 6, 'ATR': 8},
    'ccs_equipment':       {'SMR': 8, 'ATR': 8},
    'ammonia_converter':   12,
    'refractory_lining':   {'SMR': 8, 'ATR': 10},
    'general_procurement': 12,
}

# Status ordering (higher = more advanced)
_STATUS_ORDER = {
    'Cancelled': -1, 'Delayed': 0, 'Unknown': 0,
    'Announced': 1, 'Pre-FEED': 2, 'FEED': 3, 'FID': 4,
    'EPC Award': 5, 'Construction': 6, 'Commissioning': 7, 'Operational': 8,
}

# Status downgrade map (one level back for hedged/speculative mentions)
_STATUS_DOWNGRADE = {
    'Operational':   'Construction',
    'Commissioning': 'Construction',
    'Construction':  'EPC Award',
    'EPC Award':     'FID',
    'FID':           'Pre-FEED',
    'FEED':          'Pre-FEED',
    'Pre-FEED':      'Announced',
    'Announced':     'Announced',
    'Cancelled':     'Delayed',
    'Delayed':       'Delayed',
}


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class ProjectSignal:
    """Structured signal extracted from a single article."""
    article_id: int
    article_title: str = ''
    article_url: str = ''
    article_date: str = ''
    project_name: Optional[str] = None
    developer: Optional[str] = None
    co_developers: List[str] = field(default_factory=list)  # Fix 5: JV partners
    capacity_mtpa_h2: Optional[float] = None
    capacity_raw: Optional[str] = None
    capacity_unit_uncertain: bool = False  # Fix 7: ambiguous H2/NH3
    technology: Optional[str] = None       # SMR | ATR | Electrolysis | Unknown
    product: Optional[str] = None          # Hydrogen | Ammonia | Methanol | SAF | Mixed
    location_state: Optional[str] = None
    location_subregion: Optional[str] = None
    region: str = ''
    status: Optional[str] = None
    status_confirmed: bool = False         # Fix 4: True if confirmed by past tense
    fid_date: Optional[str] = None
    cod_date: Optional[str] = None
    epc_contractor: Optional[str] = None
    offtake_buyer: Optional[str] = None
    deal_value_usd: Optional[float] = None
    policy_signal: Optional[str] = None
    confidence: float = 0.0
    tags: List[str] = field(default_factory=list)
    parsing_notes: List[str] = field(default_factory=list)  # audit trail
    known_project_match: Optional[str] = None  # v3: canonical name if matched


@dataclass
class Project:
    """Deduplicated project record (merged from multiple signals)."""
    project_name: str
    developer: Optional[str] = None
    co_developers: List[str] = field(default_factory=list)  # Fix 5
    capacity_mtpa_h2: Optional[float] = None
    capacity_raw: Optional[str] = None
    capacity_unit_uncertain: bool = False  # Fix 7
    technology: Optional[str] = None
    product: Optional[str] = None
    location_state: Optional[str] = None
    location_subregion: Optional[str] = None
    region: str = ''
    status: Optional[str] = None
    fid_date: Optional[str] = None
    cod_date: Optional[str] = None
    epc_contractor: Optional[str] = None
    fid_probability: float = 0.10
    confidence: float = 0.0
    last_updated: str = ''
    source_article_ids: List[int] = field(default_factory=list)
    source_article_titles: List[str] = field(default_factory=list)
    source_article_urls: List[str] = field(default_factory=list)
    merge_notes: List[str] = field(default_factory=list)  # dedup audit trail

    @property
    def gas_demand_bcfd(self) -> Optional[float]:
        if self.capacity_mtpa_h2 is None:
            return None
        rate = GAS_DEMAND_BCFD_PER_MTPA_ATR if self.technology == 'ATR' else GAS_DEMAND_BCFD_PER_MTPA_SMR
        return self.capacity_mtpa_h2 * rate


# ---------------------------------------------------------------------------
# KNOWN ENTITIES
# ---------------------------------------------------------------------------

_DEVELOPERS = [
    'air products', 'exxonmobil', 'exxon mobil', 'chevron', 'cf industries',
    'linde', 'shell', 'bp', 'equinor', 'ineos', 'oci', 'sabic',
    'totalenergies', 'engie', 'repsol', 'eni', 'dow', 'basf',
    'plug power', 'bloom energy', 'fortescue', 'air liquide', 'yara',
    'thyssenkrupp', 'mitsubishi', 'mitsui', 'jera', 'ihi',
    'samsung engineering', 'sk energy', 'sk e&s', 'sk group', 'posco',
]

_EPC_CONTRACTORS = [
    'worley', 'technip energies', 'technip', 'kbr', 'mcdermott',
    'bechtel', 'fluor', 'wood', 'saipem', 'samsung engineering',
    'maire tecnimont', 'haldor topsoe', 'topsoe', 'thyssenkrupp',
    'linde engineering',
]

_STATUS_KEYWORDS = {
    'Operational':    ['operational', 'producing', 'online', 'commenced production'],
    'Commissioning':  ['commissioning', 'first molecule', 'startup'],
    'Construction':   ['construction', 'under construction', 'building', 'groundbreaking'],
    'EPC Award':      ['epc award', 'epc contract', 'awarded epc', 'engineering procurement'],
    'FID':            ['final investment decision', 'financial close', 'sanctioned'],
    'FEED':           ['front-end engineering', 'front end engineering'],
    'Pre-FEED':       ['pre-feed', 'pre feed', 'feasibility study', 'feasibility'],
    'Cancelled':      ['cancelled', 'canceled', 'scrapped', 'abandoned', 'terminated'],
    'Delayed':        ['delayed', 'postponed', 'deferred', 'paused', 'suspended'],
    'Announced':      ['announced', 'proposed', 'planned', 'plans to build'],
}

# Short keywords that need word-boundary matching to avoid false positives.
# "fid" can match "confident", "feed" can match "feedback" — so these use \b regex.
_STATUS_KEYWORDS_SHORT = {
    'FID':  [r'\bfid\b'],
    'FEED': [r'\bfeed\b'],
}

_TECHNOLOGY_KEYWORDS = {
    'SMR': ['steam methane reform', 'steam reforming'],
    'ATR': ['autothermal reform', 'auto-thermal'],
    'Electrolysis': ['electroly', 'pem', 'alkaline', 'soec', 'green hydrogen'],
}

# Short tech keywords needing word-boundary (SMR/ATR are 3 chars)
_TECHNOLOGY_KEYWORDS_SHORT = {
    'SMR': [r'\bsmr\b'],
    'ATR': [r'\batr\b'],
}

_PRODUCT_KEYWORDS = {
    'Ammonia': ['ammonia', 'nh3'],
    'Methanol': ['methanol'],
    'SAF': ['sustainable aviation fuel', 'saf', 'e-kerosene'],
    'Hydrogen': ['hydrogen', 'h2'],
}

_POLICY_KEYWORDS = ['45q', '45v', 'ira', 'inflation reduction act', 'doe',
                    'oced', 'hydrogen hub', 'clean hydrogen']

_SUBREGION_MAP = {
    # Texas Gulf Coast
    'port arthur': ('Texas', 'Port Arthur', 'Gulf Coast TX'),
    'freeport': ('Texas', 'Freeport', 'Gulf Coast TX'),
    'baytown': ('Texas', 'Baytown', 'Gulf Coast TX'),
    'beaumont': ('Texas', 'Beaumont', 'Gulf Coast TX'),
    'corpus christi': ('Texas', 'Corpus Christi', 'Gulf Coast TX'),
    'houston': ('Texas', 'Houston', 'Gulf Coast TX'),
    'galveston': ('Texas', 'Galveston', 'Gulf Coast TX'),
    'texas city': ('Texas', 'Texas City', 'Gulf Coast TX'),
    'deer park': ('Texas', 'Deer Park', 'Gulf Coast TX'),
    'pasadena': ('Texas', 'Pasadena', 'Gulf Coast TX'),
    'la porte': ('Texas', 'La Porte', 'Gulf Coast TX'),
    'orange': ('Texas', 'Orange', 'Gulf Coast TX'),
    # Louisiana Gulf Coast
    'lake charles': ('Louisiana', 'Lake Charles', 'Gulf Coast LA'),
    'baton rouge': ('Louisiana', 'Baton Rouge', 'Gulf Coast LA'),
    'new orleans': ('Louisiana', 'New Orleans', 'Gulf Coast LA'),
    'donaldsonville': ('Louisiana', 'Donaldsonville', 'Gulf Coast LA'),
    'geismar': ('Louisiana', 'Geismar', 'Gulf Coast LA'),
    'gonzales': ('Louisiana', 'Gonzales', 'Gulf Coast LA'),
    'norco': ('Louisiana', 'Norco', 'Gulf Coast LA'),
    # Appalachia
    'charleston': ('West Virginia', 'Charleston', 'Appalachia'),
    'morgantown': ('West Virginia', 'Morgantown', 'Appalachia'),
    'pittsburgh': ('Pennsylvania', 'Pittsburgh', 'Appalachia'),
    'marietta': ('Ohio', 'Marietta', 'Appalachia'),
    # Midwest hydrogen hubs / ethanol / industrial
    'decatur': ('Illinois', 'Decatur', 'Midwest'),
    'whiting': ('Indiana', 'Whiting', 'Midwest'),
    'toledo': ('Ohio', 'Toledo', 'Midwest'),
    'lima': ('Ohio', 'Lima', 'Midwest'),
    'joliet': ('Illinois', 'Joliet', 'Midwest'),
    'detroit': ('Michigan', 'Detroit', 'Midwest'),
    'chicago': ('Illinois', 'Chicago', 'Midwest'),
    'minneapolis': ('Minnesota', 'Minneapolis', 'Midwest'),
    'omaha': ('Nebraska', 'Omaha', 'Midwest'),
    'des moines': ('Iowa', 'Des Moines', 'Midwest'),
    'st. louis': ('Missouri', 'St. Louis', 'Midwest'),
    'kansas city': ('Missouri', 'Kansas City', 'Midwest'),
    # West Coast
    'los angeles': ('California', 'Los Angeles', 'West Coast'),
    'long beach': ('California', 'Long Beach', 'West Coast'),
    'martinez': ('California', 'Martinez', 'West Coast'),
    'richmond': ('California', 'Richmond', 'West Coast'),
    'torrance': ('California', 'Torrance', 'West Coast'),
    'sacramento': ('California', 'Sacramento', 'West Coast'),
    # Pacific Northwest
    'seattle': ('Washington', 'Seattle', 'Pacific Northwest'),
    'portland': ('Oregon', 'Portland', 'Pacific Northwest'),
    'kennewick': ('Washington', 'Kennewick', 'Pacific Northwest'),
    # Mountain West
    'salt lake city': ('Utah', 'Salt Lake City', 'Mountain West'),
    'delta': ('Utah', 'Delta', 'Mountain West'),
    'cheyenne': ('Wyoming', 'Cheyenne', 'Mountain West'),
    'casper': ('Wyoming', 'Casper', 'Mountain West'),
    'denver': ('Colorado', 'Denver', 'Mountain West'),
    # Southeast
    'savannah': ('Georgia', 'Savannah', 'Southeast'),
    'mobile': ('Alabama', 'Mobile', 'Southeast'),
    'pascagoula': ('Mississippi', 'Pascagoula', 'Southeast'),
    'jacksonville': ('Florida', 'Jacksonville', 'Southeast'),
    # Northeast
    'philadelphia': ('Pennsylvania', 'Philadelphia', 'Northeast'),
    'marcus hook': ('Pennsylvania', 'Marcus Hook', 'Northeast'),
    'new york': ('New York', 'New York', 'Northeast'),
    'newark': ('New Jersey', 'Newark', 'Northeast'),
    'linden': ('New Jersey', 'Linden', 'Northeast'),
}

# Comprehensive US state → region mapping
_STATE_REGION_MAP = {
    # Gulf Coast TX
    'texas': ('Texas', 'Gulf Coast TX'),
    # Gulf Coast LA
    'louisiana': ('Louisiana', 'Gulf Coast LA'),
    # Appalachia
    'west virginia': (None, 'Appalachia'),
    'ohio': (None, 'Appalachia'),
    'pennsylvania': (None, 'Appalachia'),
    'kentucky': (None, 'Appalachia'),
    # Midwest
    'illinois': ('Illinois', 'Midwest'),
    'indiana': ('Indiana', 'Midwest'),
    'iowa': ('Iowa', 'Midwest'),
    'kansas': ('Kansas', 'Midwest'),
    'michigan': ('Michigan', 'Midwest'),
    'minnesota': ('Minnesota', 'Midwest'),
    'missouri': ('Missouri', 'Midwest'),
    'nebraska': ('Nebraska', 'Midwest'),
    'north dakota': ('North Dakota', 'Midwest'),
    'south dakota': ('South Dakota', 'Midwest'),
    'wisconsin': ('Wisconsin', 'Midwest'),
    # West Coast
    'california': ('California', 'West Coast'),
    # Pacific Northwest
    'washington': ('Washington', 'Pacific Northwest'),
    'oregon': ('Oregon', 'Pacific Northwest'),
    # Mountain West
    'colorado': ('Colorado', 'Mountain West'),
    'utah': ('Utah', 'Mountain West'),
    'wyoming': ('Wyoming', 'Mountain West'),
    'montana': ('Montana', 'Mountain West'),
    'idaho': ('Idaho', 'Mountain West'),
    'nevada': ('Nevada', 'Mountain West'),
    'new mexico': ('New Mexico', 'Mountain West'),
    'arizona': ('Arizona', 'Mountain West'),
    # Southeast
    'alabama': ('Alabama', 'Southeast'),
    'arkansas': ('Arkansas', 'Southeast'),
    'florida': ('Florida', 'Southeast'),
    'georgia': ('Georgia', 'Southeast'),
    'mississippi': ('Mississippi', 'Southeast'),
    'north carolina': ('North Carolina', 'Southeast'),
    'south carolina': ('South Carolina', 'Southeast'),
    'tennessee': ('Tennessee', 'Southeast'),
    'virginia': ('Virginia', 'Southeast'),
    # Northeast
    'connecticut': ('Connecticut', 'Northeast'),
    'delaware': ('Delaware', 'Northeast'),
    'maine': ('Maine', 'Northeast'),
    'maryland': ('Maryland', 'Northeast'),
    'massachusetts': ('Massachusetts', 'Northeast'),
    'new hampshire': ('New Hampshire', 'Northeast'),
    'new jersey': ('New Jersey', 'Northeast'),
    'new york': ('New York', 'Northeast'),
    'rhode island': ('Rhode Island', 'Northeast'),
    'vermont': ('Vermont', 'Northeast'),
    # Oklahoma / Plains
    'oklahoma': ('Oklahoma', 'Plains'),
    # Alaska / Hawaii
    'alaska': ('Alaska', 'Alaska'),
    'hawaii': ('Hawaii', 'Hawaii'),
}

# State abbreviations → full name (for NER and text matching)
_STATE_ABBREVS = {
    'al': 'alabama', 'ak': 'alaska', 'az': 'arizona', 'ar': 'arkansas',
    'ca': 'california', 'co': 'colorado', 'ct': 'connecticut', 'de': 'delaware',
    'fl': 'florida', 'ga': 'georgia', 'hi': 'hawaii', 'id': 'idaho',
    'il': 'illinois', 'in': 'indiana', 'ia': 'iowa', 'ks': 'kansas',
    'ky': 'kentucky', 'la': 'louisiana', 'me': 'maine', 'md': 'maryland',
    'ma': 'massachusetts', 'mi': 'michigan', 'mn': 'minnesota', 'ms': 'mississippi',
    'mo': 'missouri', 'mt': 'montana', 'ne': 'nebraska', 'nv': 'nevada',
    'nh': 'new hampshire', 'nj': 'new jersey', 'nm': 'new mexico', 'ny': 'new york',
    'nc': 'north carolina', 'nd': 'north dakota', 'oh': 'ohio', 'ok': 'oklahoma',
    'or': 'oregon', 'pa': 'pennsylvania', 'ri': 'rhode island', 'sc': 'south carolina',
    'sd': 'south dakota', 'tn': 'tennessee', 'tx': 'texas', 'ut': 'utah',
    'vt': 'vermont', 'va': 'virginia', 'wa': 'washington', 'wv': 'west virginia',
    'wi': 'wisconsin', 'wy': 'wyoming',
}

# Fix 4: Context words for status detection
_HEDGE_WORDS = [
    'considering', 'evaluating', 'may', 'could', 'exploring', 'potential',
    'possible', 'expected to', 'might', 'studying', 'assessing',
]
_FUTURE_TENSE = [
    'will', 'plans to', 'targeting', 'aiming for', 'intends to',
    'planning to', 'looking to', 'hopes to', 'set to',
]
_CONFIRM_WORDS = [
    'reached', 'achieved', 'took', 'taken', 'awarded', 'commenced',
    'signed', 'completed', 'secured', 'finalized', 'approved',
    'has reached', 'has taken', 'has awarded', 'has signed',
]
_NEGATE_WORDS = [
    'not', 'failed', 'unlikely', 'rejected', 'denied', 'reversed',
    'no longer', 'pulled out', "won't", 'will not', 'did not',
]

# Fix 5: JV detection patterns
_JV_PATTERNS = [
    r'(?:joint venture|jv|partnership|consortium)\s+(?:between|of|with)\s+',
    r'(\w+)\s+and\s+(\w+)\s+(?:joint venture|partnership|jv)',
    r'led by\s+',
    r'operated by\s+',
]


# ---------------------------------------------------------------------------
# KNOWN PROJECTS DATABASE  (v3 — Gap fixes 1,2,4,5,6,7)
# ---------------------------------------------------------------------------
# Curated database of confirmed blue H2/ammonia projects.
# Used for: canonical naming, technology override, cluster anchoring, dedup.
# Sources: IEA Hydrogen Projects Database, company filings, DOE OCED.

_KNOWN_PROJECTS = [
    {
        'canonical_name': 'ExxonMobil Baytown Hydrogen',
        'aliases': ['baytown hydrogen', 'exxonmobil baytown', 'exxon baytown',
                    'baytown low-carbon hydrogen', 'baytown blue hydrogen',
                    'baytown h2'],
        'developer': 'exxonmobil',
        'co_developers': [],
        'location_subregion': 'Baytown',
        'location_state': 'Texas',
        'region': 'Gulf Coast TX',
        'technology': 'ATR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 1.0,
    },
    {
        'canonical_name': 'Air Products Louisiana Clean Hydrogen',
        'aliases': ['air products louisiana', 'air products blue hydrogen',
                    'air products clean hydrogen', 'air products ascension',
                    'air products clean energy complex'],
        'developer': 'air products',
        'co_developers': [],
        'location_subregion': None,
        'location_state': 'Louisiana',
        'region': 'Gulf Coast LA',
        'technology': 'SMR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.75,
    },
    {
        'canonical_name': 'CF Industries Blue Ammonia',
        'aliases': ['cf industries ammonia', 'cf industries blue ammonia',
                    'cf donaldsonville', 'cf industries donaldsonville',
                    'cf industries blue'],
        'developer': 'cf industries',
        'co_developers': [],
        'location_subregion': None,
        'location_state': 'Louisiana',
        'region': 'Gulf Coast LA',
        'technology': 'ATR',
        'product': 'Ammonia',
        'max_capacity_mtpa': 0.36,
    },
    {
        'canonical_name': 'Linde/OCI Beaumont Blue Hydrogen',
        'aliases': ['linde beaumont', 'oci beaumont', 'linde oci beaumont',
                    'beaumont blue hydrogen', 'linde oci hydrogen',
                    'linde oci'],
        'developer': 'linde',
        'co_developers': ['oci'],
        'location_subregion': 'Beaumont',
        'location_state': 'Texas',
        'region': 'Gulf Coast TX',
        'technology': 'SMR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.5,
    },
    {
        'canonical_name': 'Chevron Bayou Bend CCS',
        'aliases': ['chevron bayou bend', 'bayou bend ccs', 'chevron hydrogen texas',
                    'chevron blue hydrogen', 'bayou bend'],
        'developer': 'chevron',
        'co_developers': [],
        'location_subregion': None,
        'location_state': 'Texas',
        'region': 'Gulf Coast TX',
        'technology': 'SMR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.5,
    },
    {
        'canonical_name': 'Lake Charles Methanol II',
        'aliases': ['lake charles methanol', 'lcm ii', 'lake charles blue methanol',
                    'lake charles clean methanol', 'lake charles methanol ii'],
        'developer': None,
        'co_developers': [],
        'location_subregion': 'Lake Charles',
        'location_state': 'Louisiana',
        'region': 'Gulf Coast LA',
        'technology': 'ATR',
        'product': 'Methanol',
        'max_capacity_mtpa': 0.30,
    },
    {
        'canonical_name': 'Dow Path2Zero Freeport',
        'aliases': ['dow freeport', 'path2zero', 'dow path2zero',
                    'dow blue hydrogen', 'dow freeport hydrogen'],
        'developer': 'dow',
        'co_developers': [],
        'location_subregion': 'Freeport',
        'location_state': 'Texas',
        'region': 'Gulf Coast TX',
        'technology': 'ATR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.6,
    },
    {
        'canonical_name': 'Shell Norco Hydrogen',
        'aliases': ['shell norco', 'shell blue hydrogen louisiana',
                    'shell louisiana hydrogen'],
        'developer': 'shell',
        'co_developers': [],
        'location_subregion': None,
        'location_state': 'Louisiana',
        'region': 'Gulf Coast LA',
        'technology': 'SMR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.4,
    },
    {
        'canonical_name': 'Air Liquide Gulf Coast Blue Hydrogen',
        'aliases': ['air liquide blue hydrogen', 'air liquide gulf coast',
                    'air liquide texas hydrogen'],
        'developer': 'air liquide',
        'co_developers': [],
        'location_subregion': None,
        'location_state': 'Texas',
        'region': 'Gulf Coast TX',
        'technology': 'SMR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.5,
    },
    {
        'canonical_name': 'Equinor Appalachia Blue Hydrogen',
        'aliases': ['equinor blue hydrogen', 'equinor appalachia',
                    'equinor hydrogen appalachia'],
        'developer': 'equinor',
        'co_developers': [],
        'location_subregion': None,
        'location_state': None,
        'region': 'Appalachia',
        'technology': 'ATR',
        'product': 'Hydrogen',
        'max_capacity_mtpa': 0.8,
    },
]

# ---------------------------------------------------------------------------
# NUCLEAR SMR DISAMBIGUATION  (v3 — Gap 4)
# ---------------------------------------------------------------------------

_NUCLEAR_INDICATORS = [
    'nuclear', 'small modular reactor', 'nuclear reactor', 'nrc',
    'nuclear regulatory', 'nuscale', 'x-energy', 'kairos', 'terrapower',
    'oklo', 'uranium', 'fission', 'atomic energy', 'nuclear power',
    'reactor design', 'light water reactor', 'molten salt',
    'nuclear energy', 'nuclear plant',
]


def _is_nuclear_smr(text_lower: str) -> bool:
    """Check if SMR references in text refer to nuclear Small Modular Reactors."""
    if 'small modular reactor' in text_lower:
        return True
    for m in re.finditer(r'\bsmr\b', text_lower):
        start = max(0, m.start() - 120)
        end = min(len(text_lower), m.end() + 120)
        context = text_lower[start:end]
        if any(ind in context for ind in _NUCLEAR_INDICATORS):
            return True
    return False


# ---------------------------------------------------------------------------
# CAPACITY VALIDATION  (v3 — Gap 3)
# ---------------------------------------------------------------------------

_CAPACITY_LIMITS = {
    'min_mtpa': 0.001,         # 1 kt/y — below this is likely extraction error
    'max_single_plant': 3.0,   # No single blue H2 plant exceeds ~3 MTPA
    'max_electrolyzer': 0.5,   # Electrolyzers are much smaller scale
}


def _validate_capacity(mtpa: float, technology: Optional[str],
                       known_proj: Optional[dict] = None) -> Tuple[Optional[float], List[str]]:
    """Validate extracted capacity against realistic ranges.

    Returns (validated_mtpa_or_None, list_of_validation_notes).
    """
    notes: List[str] = []

    if mtpa < _CAPACITY_LIMITS['min_mtpa']:
        notes.append(f"Capacity {mtpa:.6f} MTPA below minimum — rejected")
        return None, notes

    if mtpa > _CAPACITY_LIMITS['max_single_plant']:
        notes.append(f"Capacity {mtpa:.4f} MTPA exceeds max single plant "
                     f"({_CAPACITY_LIMITS['max_single_plant']} MTPA) — likely unit confusion")
        if known_proj and known_proj.get('max_capacity_mtpa'):
            ref = known_proj['max_capacity_mtpa']
            notes.append(f"  Using known project reference: {ref:.2f} MTPA")
            return ref, notes
        return None, notes

    if technology == 'Electrolysis' and mtpa > _CAPACITY_LIMITS['max_electrolyzer']:
        notes.append(f"Capacity {mtpa:.4f} MTPA too large for electrolyzer — rejected")
        return None, notes

    # Cross-check against known project reference capacity
    if known_proj and known_proj.get('max_capacity_mtpa'):
        ref = known_proj['max_capacity_mtpa']
        if mtpa > 0 and ref > 0:
            ratio = mtpa / ref
            if ratio > 5.0:
                notes.append(f"Capacity {mtpa:.4f} MTPA is {ratio:.1f}x known reference "
                             f"({ref:.2f} MTPA) — using reference")
                return ref, notes
            elif ratio < 0.05:
                notes.append(f"Capacity {mtpa:.6f} MTPA is {ratio:.4f}x known reference "
                             f"({ref:.2f} MTPA) — too small, rejected")
                return None, notes

    return mtpa, notes


# ---------------------------------------------------------------------------
# KNOWN PROJECT MATCHING  (v3 — Gaps 1,2,6,7)
# ---------------------------------------------------------------------------

def _match_known_project(text_lower: str, developer: Optional[str] = None,
                         subregion: Optional[str] = None) -> Optional[dict]:
    """Match article text against the known projects database.

    Returns the matching project dict or None.
    Priority: alias match > developer+location match > developer+state.
    """
    # Pass 1: Check aliases (most specific)
    for proj in _KNOWN_PROJECTS:
        for alias in proj['aliases']:
            if alias in text_lower:
                return proj

    # Pass 2: Developer + subregion match
    if developer and subregion:
        dev_l = developer.lower().strip()
        sub_l = subregion.lower().strip()
        for proj in _KNOWN_PROJECTS:
            p_dev = (proj['developer'] or '').lower()
            p_sub = (proj.get('location_subregion') or '').lower()
            if p_dev and p_dev == dev_l and p_sub and p_sub == sub_l:
                return proj

    # Pass 3: Developer + state match (weaker — only if unambiguous)
    if developer:
        dev_l = developer.lower().strip()
        state_from_text = None
        # Check subregion cities first (most specific)
        for kw, (state, _, _) in _SUBREGION_MAP.items():
            if kw in text_lower:
                state_from_text = state
                break
        # Then check all 50 state names
        if not state_from_text:
            for state_name, (state_val, _) in _STATE_REGION_MAP.items():
                if state_name in text_lower:
                    state_from_text = state_val
                    break
        if state_from_text:
            matches = [p for p in _KNOWN_PROJECTS
                       if (p['developer'] or '').lower() == dev_l
                       and (p.get('location_state') or '') == state_from_text]
            if len(matches) == 1:  # Unambiguous
                return matches[0]

    return None


# ---------------------------------------------------------------------------
# EXTRACTION FUNCTIONS
# ---------------------------------------------------------------------------

def _match_first(text_lower: str, keyword_map: dict) -> Optional[str]:
    """Match first keyword from keyword_map found in text."""
    for label, keywords in keyword_map.items():
        for kw in keywords:
            if kw in text_lower:
                return label
    return None


def _match_first_with_boundaries(text_lower: str, keyword_map: dict,
                                  short_keyword_map: dict) -> Optional[str]:
    """Match keywords with word-boundary support for short keywords.

    keyword_map: long keywords matched by substring (e.g., 'steam methane reform')
    short_keyword_map: short keywords matched by regex with \\b boundaries
    """
    # Long keywords first (more specific)
    result = _match_first(text_lower, keyword_map)
    if result:
        return result
    # Short keywords with word boundaries
    for label, patterns in short_keyword_map.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                return label
    return None


# Fix 1: Real project name extraction (v3: known project lookup added as Pattern 0)
def _extract_project_name(title: str, text: str,
                          developer: Optional[str],
                          subregion: Optional[str],
                          product: Optional[str],
                          known_proj: Optional[dict] = None) -> Tuple[Optional[str], str]:
    """Extract a real project name from the article title and text.

    Returns (project_name, pattern_used).
    Priority:
      0. Known project canonical name (v3)
      1. Quoted names in title: "Bayou Bend CCS"
      2. "announces/unveils/launches/proposes [Name] project/plant/facility"
      3. Possessive: "ExxonMobil's Baytown hydrogen facility"
      4. "the [Name] project/plant/facility/hub"
      5. Developer + Location combo from title
      6. Fallback: generic Developer + Product + "Project"
    """
    # Pattern 0: Known project canonical name
    if known_proj:
        return known_proj['canonical_name'], 'Pattern 0: known project DB'

    # Pattern 1: Quoted project names
    m = re.search(r'["\u201c]([^"\u201d]{5,60})["\u201d]', title)
    if m:
        candidate = m.group(1).strip()
        if any(w in candidate.lower() for w in ['project', 'plant', 'facility', 'hub',
                                                  'complex', 'terminal', 'hydrogen',
                                                  'ammonia', 'energy', 'ccs']):
            return candidate, 'Pattern 1: quoted name in title'

    # Pattern 2: "announces/unveils/launches/proposes [Name]"
    announce_verbs = r'(?:announces?|unveils?|launches?|proposes?|reveals?|introduces?|greenlights?)'
    m = re.search(
        rf'{announce_verbs}\s+(.{{5,80}}?)\s*(?:project|plant|facility|hub|complex)',
        title, re.IGNORECASE)
    if m:
        name = m.group(1).strip().rstrip(',')
        name = re.sub(r'^(?:the|a|an|its|new)\s+', '', name, flags=re.IGNORECASE)
        if len(name) > 3:
            return name + ' Project', 'Pattern 2: announce verb'

    # Pattern 3: Possessive — "ExxonMobil's Baytown hydrogen facility"
    m = re.search(
        r"(\w[\w\s]{2,30})'s\s+(.{3,50}?)\s*(?:project|plant|facility|hub|complex)",
        title, re.IGNORECASE)
    if m:
        owner = m.group(1).strip()
        name_part = m.group(2).strip()
        return f"{owner} {name_part} Project", 'Pattern 3: possessive'

    # Pattern 4: "the [Name] project/plant/facility"
    m = re.search(
        r'the\s+(.{3,60}?)\s*(?:project|plant|facility|hub|complex)',
        title, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        generic = {'new', 'proposed', 'planned', 'large', 'major', 'first', 'largest'}
        words = set(name.lower().split())
        if not words.issubset(generic):
            return name + ' Project', 'Pattern 4: the X project'

    # Pattern 5: Developer + Location from title
    if developer and subregion:
        title_lower = title.lower()
        if developer.lower() in title_lower and subregion.lower() in title_lower:
            return f"{developer.title()} {subregion} Project", 'Pattern 5: developer + location'

    # Pattern 6: Fallback — generic inference
    parts = []
    if developer:
        parts.append(developer.title())
    if subregion:
        parts.append(subregion)
    elif product:
        parts.append(product)
    if parts:
        return ' '.join(parts) + ' Project', 'Pattern 6: generic fallback'
    return None, 'no pattern matched'


# Fix 4: Context-aware status detection (v3: word boundaries for short keywords)
def _extract_status_with_context(text_lower: str) -> Tuple[Optional[str], bool]:
    """Detect project status with context awareness.

    Returns (status, is_confirmed).
    - Checks for hedge/future tense words → downgrade one level
    - Checks for confirmation words → keep as-is, mark confirmed
    - Checks for negation words → skip this status entirely
    v3: Uses word-boundary regex for short keywords (fid, feed).
    """
    best_status = None
    best_order = -2
    best_confirmed = False

    def _evaluate_match(pos: int, kw_len: int, status_label: str):
        nonlocal best_status, best_order, best_confirmed

        start = max(0, pos - 60)
        end = min(len(text_lower), pos + kw_len + 60)
        context = text_lower[start:end]

        # Negation (strongest — reject this match)
        if any(neg in context for neg in _NEGATE_WORDS):
            return

        is_confirmed = any(conf in context for conf in _CONFIRM_WORDS)
        is_hedged = (any(h in context for h in _HEDGE_WORDS) or
                     any(f in context for f in _FUTURE_TENSE))

        effective_status = status_label
        if is_hedged and not is_confirmed:
            effective_status = _STATUS_DOWNGRADE.get(status_label, status_label)

        order = _STATUS_ORDER.get(effective_status, 0)

        if order > best_order or (order == best_order and is_confirmed and not best_confirmed):
            best_status = effective_status
            best_order = order
            best_confirmed = is_confirmed

    # Long keywords (substring match)
    for status_label, keywords in _STATUS_KEYWORDS.items():
        for kw in keywords:
            pos = text_lower.find(kw)
            if pos == -1:
                continue
            _evaluate_match(pos, len(kw), status_label)

    # Short keywords (word-boundary regex)
    for status_label, patterns in _STATUS_KEYWORDS_SHORT.items():
        for pat in patterns:
            m = re.search(pat, text_lower)
            if not m:
                continue
            _evaluate_match(m.start(), m.end() - m.start(), status_label)

    return best_status, best_confirmed


# Fix 5: Multi-developer / JV extraction
def _extract_developers(text_lower: str) -> Tuple[Optional[str], List[str]]:
    """Extract primary developer and co-developers (JV partners).

    Returns (primary_developer, co_developers_list).
    """
    all_devs = [e for e in _DEVELOPERS
                if (re.search(r'\b' + re.escape(e) + r'\b', text_lower)
                    if len(e) <= 4 else e in text_lower)]
    if not all_devs:
        return None, []

    primary = all_devs[0]
    co_devs = all_devs[1:] if len(all_devs) > 1 else []

    # "led by X" — X becomes primary
    m = re.search(r'led by\s+(\w[\w\s]*)', text_lower)
    if m:
        leader = m.group(1).strip().lower()
        for dev in all_devs:
            if dev in leader:
                primary = dev
                co_devs = [d for d in all_devs if d != dev]
                break

    # "operated by Y" — Y becomes primary
    m = re.search(r'operated by\s+(\w[\w\s]*)', text_lower)
    if m:
        operator = m.group(1).strip().lower()
        for dev in all_devs:
            if dev in operator:
                primary = dev
                co_devs = [d for d in all_devs if d != dev]
                break

    return primary, co_devs


# Fix 7: Context-aware capacity extraction (v3: validation added)
def _extract_capacity(text: str, known_proj: Optional[dict] = None,
                      technology: Optional[str] = None) -> Tuple[Optional[float], Optional[str], bool, List[str]]:
    """Extract capacity and normalize to MTPA H2 equivalent.

    Returns (mtpa_h2, raw_text, unit_uncertain, validation_notes).
    v3: Adds validation ranges and known-project cross-check.
    """
    text_l = text.lower()
    validation_notes: List[str] = []
    patterns = [
        (r'(\d+\.?\d*)\s*mtpa\s*(h2|hydrogen)', 'mtpa_h2'),
        (r'(\d+\.?\d*)\s*mtpa\s*(ammonia|nh3)', 'mtpa_nh3'),
        (r'(\d+\.?\d*)\s*mtpa', 'mtpa_unknown'),
        (r'(\d+\.?\d*)\s*(?:million\s+tonn?e?s?\s*(?:per\s+(?:year|annum)|p\.?a\.?|/y(?:ear)?))', 'mtpa_unknown'),
        (r'(\d+\.?\d*)\s*tpd\s*(h2|hydrogen)', 'tpd_h2'),
        (r'(\d+\.?\d*)\s*tpd\s*(ammonia|nh3)', 'tpd_nh3'),
        (r'(\d+\.?\d*)\s*tpd', 'tpd_unknown'),
        (r'(\d+\.?\d*)\s*(?:kt|kiloton)', 'kt'),
        (r'(\d+\.?\d*)\s*(?:gw|gigawatt)', 'gw'),
        (r'(\d+\.?\d*)\s*(?:mw|megawatt)', 'mw'),
    ]
    for pat, unit_type in patterns:
        m = re.search(pat, text_l)
        if not m:
            continue
        val = float(m.group(1))
        raw = m.group(0).strip()
        uncertain = False
        mtpa = None

        if unit_type == 'mtpa_h2':
            mtpa = val
        elif unit_type == 'mtpa_nh3':
            mtpa = val * NH3_TO_H2_MASS_RATIO
        elif unit_type == 'mtpa_unknown':
            # Check ±20 chars around the match for context
            start = max(0, m.start() - 20)
            end = min(len(text_l), m.end() + 20)
            context = text_l[start:end]
            if 'ammonia' in context or 'nh3' in context:
                mtpa = val * NH3_TO_H2_MASS_RATIO
            elif 'hydrogen' in context or 'h2' in context:
                mtpa = val
            else:
                if 'ammonia' in text_l or 'nh3' in text_l:
                    mtpa = val * NH3_TO_H2_MASS_RATIO
                    uncertain = True
                else:
                    mtpa = val
                    uncertain = True
        elif unit_type == 'tpd_h2':
            mtpa = val * 365 / 1e6
        elif unit_type == 'tpd_nh3':
            mtpa = val * 365 / 1e6 * NH3_TO_H2_MASS_RATIO
        elif unit_type == 'tpd_unknown':
            start = max(0, m.start() - 20)
            end = min(len(text_l), m.end() + 20)
            context = text_l[start:end]
            if 'ammonia' in context or 'nh3' in context:
                mtpa = val * 365 / 1e6 * NH3_TO_H2_MASS_RATIO
            elif 'hydrogen' in context or 'h2' in context:
                mtpa = val * 365 / 1e6
            else:
                if 'ammonia' in text_l or 'nh3' in text_l:
                    mtpa = val * 365 / 1e6 * NH3_TO_H2_MASS_RATIO
                    uncertain = True
                else:
                    mtpa = val * 365 / 1e6
                    uncertain = True
        elif unit_type == 'kt':
            mtpa = val / 1000
        elif unit_type == 'gw':
            # GW likely refers to electrolyzer power — use electrolysis conversion
            # 1 GW electrolyzer ≈ 0.15 MTPA H2 at 50% capacity factor
            mtpa = val * 0.15
            uncertain = True
            validation_notes.append("GW→MTPA: assumed electrolyzer (1 GW ≈ 0.15 MTPA H2)")
        elif unit_type == 'mw':
            # MW likely refers to electrolyzer power
            # 1000 MW = 1 GW ≈ 0.15 MTPA H2
            mtpa = val * 0.00015
            uncertain = True
            validation_notes.append("MW→MTPA: assumed electrolyzer (1000 MW ≈ 0.15 MTPA H2)")

        if mtpa is not None:
            # v3: Validate against realistic ranges
            validated, v_notes = _validate_capacity(mtpa, technology, known_proj)
            validation_notes.extend(v_notes)
            if validated is not None:
                return validated, raw, uncertain, validation_notes
            else:
                # Validation rejected — try next pattern
                validation_notes.append(f"  (rejected raw extraction: {raw} → {mtpa:.4f} MTPA)")
                continue

    return None, None, False, validation_notes


def _extract_date(text: str) -> Optional[str]:
    """Try to extract a year or date from text."""
    m = re.search(r'(20\d{2}[-/]\d{1,2}[-/]\d{1,2})', text)
    if m:
        return m.group(1)
    m = re.search(r'Q([1-4])\s*(20\d{2})', text)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    m = re.search(r'\b(202[4-9]|203[0-5])\b', text)
    if m:
        return m.group(1)
    return None


def _extract_dollar_value(text: str) -> Optional[float]:
    """Extract dollar amounts."""
    m = re.search(r'\$\s*(\d+\.?\d*)\s*(billion|bn|b)', text, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1e9
    m = re.search(r'\$\s*(\d+\.?\d*)\s*(million|mn|m)', text, re.IGNORECASE)
    if m:
        return float(m.group(1)) * 1e6
    return None


# Country keywords for non-US article detection (mirrors blue_h2_news_collector)
_COUNTRY_KEYWORDS = [
    ('Canada', ['canada', 'canadian', 'alberta', 'ontario', 'quebec',
                'british columbia', 'saskatchewan', 'nova scotia',
                'calgary', 'edmonton', 'toronto', 'montreal', 'vancouver',
                'saskatoon', 'winnipeg', 'ottawa', 'new brunswick']),
    ('Mexico', ['mexico', 'mexican']),
    ('United Kingdom', ['united kingdom', 'uk ', ' uk,', 'british', 'england',
                        'scotland', 'wales', 'london', 'kent', 'sussex',
                        'aberdeen', 'manchester', 'birmingham', 'liverpool']),
    ('Germany', ['germany', 'german', 'berlin', 'hamburg', 'munich']),
    ('Netherlands', ['netherlands', 'dutch', 'rotterdam', 'amsterdam']),
    ('France', ['france', 'french', 'paris', 'marseille']),
    ('Spain', ['spain', 'spanish', 'madrid', 'barcelona']),
    ('Italy', ['italy', 'italian', 'milan', 'rome']),
    ('Norway', ['norway', 'norwegian', 'oslo', 'bergen']),
    ('Sweden', ['sweden', 'swedish', 'stockholm']),
    ('Denmark', ['denmark', 'danish', 'copenhagen']),
    ('Finland', ['finland', 'finnish', 'helsinki', 'oulu', 'tampere']),
    ('Estonia', ['estonia', 'estonian', 'tallinn']),
    ('Slovenia', ['slovenia', 'slovenian', 'ljubljana']),
    ('Belgium', ['belgium', 'belgian', 'antwerp']),
    ('Austria', ['austria', 'austrian', 'vienna']),
    ('Switzerland', ['switzerland', 'swiss', 'zurich']),
    ('Poland', ['poland', 'polish', 'warsaw']),
    ('Japan', ['japan', 'japanese', 'tokyo', 'osaka', 'fukushima']),
    ('South Korea', ['south korea', 'korean', 'seoul']),
    ('China', ['china', 'chinese', 'beijing', 'shanghai', 'shenzhen']),
    ('India', ['india', 'indian', 'mumbai', 'delhi', 'chennai']),
    ('Australia', ['australia', 'australian', 'sydney', 'melbourne']),
    ('Saudi Arabia', ['saudi arabia', 'saudi', 'riyadh', 'neom', 'jeddah']),
    ('UAE', ['united arab emirates', ' uae', 'abu dhabi', 'dubai']),
    ('Oman', ['oman', 'muscat']),
    ('Qatar', ['qatar', 'doha']),
    ('Egypt', ['egypt', 'egyptian', 'cairo']),
    ('Algeria', ['algeria', 'algerian', 'algiers']),
    ('Morocco', ['morocco', 'moroccan']),
    ('Namibia', ['namibia', 'namibian']),
    ('South Africa', ['south africa', 'south african']),
    ('Chile', ['chile', 'chilean', 'santiago']),
    ('Brazil', ['brazil', 'brazilian', 'sao paulo']),
    ('Argentina', ['argentina', 'argentinian', 'buenos aires']),
    ('Colombia', ['colombia', 'colombian']),
    ('Indonesia', ['indonesia', 'indonesian', 'jakarta']),
    ('Singapore', ['singapore']),
    ('Malaysia', ['malaysia', 'malaysian', 'kuala lumpur']),
    ('Vietnam', ['vietnam', 'vietnamese']),
    ('Thailand', ['thailand', 'thai', 'bangkok']),
    ('New Zealand', ['new zealand']),
    # EU catch-all LAST — specific countries above take priority
    ('EU', ['european union', ' eu ', "eu's", 'brussels', 'europe', 'european']),
]

# Company name → primary region (fallback when no state/city/country found)
_COMPANY_REGION_MAP = [
    # (company_name_keywords, state, subregion, region)
    # --- SEC tracked companies ---
    (['air products'], 'Louisiana', None, 'Gulf Coast LA'),
    (['cf industries'], 'Louisiana', 'Donaldsonville', 'Gulf Coast LA'),
    (['exxonmobil', 'exxon mobil'], 'Texas', 'Baytown', 'Gulf Coast TX'),
    (['chevron'], 'California', None, 'West Coast'),
    (['dow chemical', 'dow inc'], 'Texas', 'Freeport', 'Gulf Coast TX'),
    (['shell plc', 'shell usa'], 'Texas', None, 'Gulf Coast TX'),
    (['bp ', 'bp,', "bp's"], 'Indiana', 'Whiting', 'Midwest'),
    (['linde plc', 'linde '], 'Connecticut', None, 'Northeast'),
    # --- US utilities ---
    (['duke energy'], 'North Carolina', None, 'Southeast'),
    (['plug power'], 'New York', None, 'Northeast'),
    (['tva ', 'tva,', "tva's"], 'Tennessee', None, 'Southeast'),
    (['exelon'], 'Illinois', 'Chicago', 'Midwest'),
    (['xcel energy'], 'Minnesota', None, 'Midwest'),
    (['southern company'], 'Georgia', None, 'Southeast'),
    (['sempra'], 'Texas', None, 'Gulf Coast TX'),
    (['cheniere'], 'Texas', None, 'Gulf Coast TX'),
    (['freeport lng'], 'Texas', 'Freeport', 'Gulf Coast TX'),
    (['marathon petroleum'], 'Ohio', None, 'Midwest'),
    (['valero'], 'Texas', None, 'Gulf Coast TX'),
    # --- Other H2/ammonia companies ---
    (['denbury'], 'Texas', None, 'Gulf Coast TX'),
    (['sasol'], 'Louisiana', 'Lake Charles', 'Gulf Coast LA'),
    (['next decade', 'nextdecade'], 'Texas', None, 'Gulf Coast TX'),
    # --- International companies ---
    (['thyssenkrupp'], None, None, 'Germany'),
    (['mitsubishi heavy', ' mhi '], None, None, 'Japan'),
    (['cell impact'], None, None, 'Sweden'),
    (['smoltek'], None, None, 'Sweden'),
    (['metacon'], None, None, 'Sweden'),
    (['rolls-royce'], None, None, 'United Kingdom'),
    (['hysa infra'], None, None, 'South Africa'),
]


def _extract_location(text_lower: str) -> Tuple[Optional[str], Optional[str], str]:
    """Returns (state, subregion, region).

    Cascade:
    1. Subregion city match (most specific — city implies state + region)
    2. Full US state name match → US region
    3. Safe state abbreviation match (skips ambiguous ones)
    4. Appalachia / New England keywords
    5. Generic US mention (EIA/DOE/EPA patterns)
    6. Known company name → company's primary region
    7. Country keyword match → country name as region
    8. 'Unknown' fallback
    """
    # 1. Subregion city match (most specific — city name implies state + region)
    for kw, (state, sub, region) in _SUBREGION_MAP.items():
        if kw in text_lower:
            return state, sub, region

    # 2. Full state name match — check all 50 states
    for state_name, (state_val, region) in _STATE_REGION_MAP.items():
        if state_name in text_lower:
            return state_val, None, region

    # 3. State abbreviation match (e.g., " NE ", " WY ", " TX ")
    #    Require word boundaries to avoid false positives (e.g., "in" for Indiana)
    for abbrev, state_name in _STATE_ABBREVS.items():
        # Skip very common words that are also state abbreviations
        if len(abbrev) == 2 and abbrev in ('in', 'or', 'me', 'ok', 'hi', 'id', 'al',
                                            'ma', 'de', 'co', 'mo', 'pa'):
            continue
        pattern = f' {abbrev} '
        if pattern in text_lower:
            state_val, region = _STATE_REGION_MAP[state_name]
            return state_val, None, region

    # 4. Appalachia / New England keywords
    if 'appalachia' in text_lower:
        return None, None, 'Appalachia'
    if 'new england' in text_lower:
        return None, None, 'Northeast'
    if 'tennessee valley authority' in text_lower:
        return None, None, 'Southeast'

    # 5. Generic US mention (EIA/DOE/EPA patterns)
    us_markers = ['united states', 'u.s.', 'lower 48', 'henry hub',
                  'eia forecast', 'eia report', 'eia expects', 'eia forecasts',
                  'u.s. retail gasoline', 'u.s. crude oil',
                  'u.s. electricity demand', 'u.s. electric power',
                  'u.s. benchmark', 'u.s. energy information',
                  'u.s. nuclear', 'u.s. wholesale']
    for marker in us_markers:
        if marker in text_lower:
            return None, None, 'US Other'

    # 6. Known company name → company's primary region
    for company_kws, state, sub, region in _COMPANY_REGION_MAP:
        for kw in company_kws:
            if kw in text_lower:
                return state, sub, region

    # 7. Country keyword match → country name as region
    for country, keywords in _COUNTRY_KEYWORDS:
        for kw in keywords:
            if kw in text_lower:
                return None, None, country

    # 8. Nothing found
    return None, None, 'Unknown'


def _match_entities(text_lower: str, entity_list: list) -> List[str]:
    return [e for e in entity_list if e in text_lower]


# ---------------------------------------------------------------------------
# CONFIDENCE DECAY
# ---------------------------------------------------------------------------

def _confidence_decay(base: float, published_date: str) -> float:
    """Decay confidence by 5% per month since publication."""
    if not published_date:
        return base * 0.8
    try:
        from dateutil import parser as dp
        d = dp.parse(published_date)
        months = (datetime.now() - d.replace(tzinfo=None)).days / 30.0
        return base * (0.95 ** max(0, months))
    except Exception:
        return base * 0.8


# ---------------------------------------------------------------------------
# PARSER CLASS
# ---------------------------------------------------------------------------

class NewsMarketSignalParser:
    """Parse articles into structured ProjectSignals and maintain projects table."""

    def __init__(self, db_path: str = DATABASE_NAME):
        self.db_path = db_path

    def parse_article(self, article: dict) -> Optional[ProjectSignal]:
        """Parse a single article dict into a ProjectSignal.

        v3: Returns None if the article is too generic to produce a useful signal
        (relevance filter — Gap 5).
        """
        title = article.get('title', '')
        snippet = article.get('snippet', '')
        full_text = article.get('full_text', '') or ''
        # Primary text = title + snippet (most reliable, directly about this article)
        # Full text is a fallback — may contain newsletter digests with unrelated stories
        primary_text = f"{title} {snippet}".strip()
        primary_lower = primary_text.lower()
        text = f"{title} {snippet} {full_text}".strip()
        text_lower = text.lower()
        pub_date = article.get('published_date', '') or article.get('published', '')
        priority_score = article.get('priority_score', 0) or 0

        notes: List[str] = []

        # -------------------------------------------------------------------
        # v3 Gap 4: Nuclear SMR exclusion — check early and reject
        # -------------------------------------------------------------------
        if _is_nuclear_smr(text_lower):
            # Check if article is primarily about nuclear, not hydrogen
            h2_count = text_lower.count('hydrogen') + text_lower.count(' h2 ')
            nuclear_count = sum(text_lower.count(ind) for ind in
                               ['nuclear', 'reactor', 'nuscale', 'x-energy'])
            if nuclear_count > h2_count:
                notes.append(f"REJECTED: Nuclear SMR article (nuclear={nuclear_count} > hydrogen={h2_count})")
                return None

        # Multi-developer extraction — title+snippet first, full_text fallback
        primary_dev, co_devs = _extract_developers(primary_lower)
        dev_source = 'title+snippet'
        if not primary_dev and full_text:
            primary_dev, co_devs = _extract_developers(text_lower)
            if primary_dev:
                dev_source = 'full_text fallback'
        if primary_dev:
            if co_devs:
                notes.append(f"Developer: '{primary_dev}' (primary, {dev_source}), JV partners: {co_devs}")
            else:
                notes.append(f"Developer: '{primary_dev}' ({dev_source})")
        else:
            notes.append("Developer: not detected")

        contractors = _match_entities(text_lower, _EPC_CONTRACTORS)
        if contractors:
            notes.append(f"EPC contractor(s): {contractors}")

        # Location — title+snippet first, full_text fallback
        # (full_text may be a newsletter digest with unrelated stories)
        state, subregion, region = _extract_location(primary_lower)
        loc_source = 'title+snippet'
        if region in ('Unknown', 'US Other') and full_text:
            ft_state, ft_sub, ft_region = _extract_location(text_lower)
            if ft_region not in ('Unknown', 'US Other'):
                state, subregion, region = ft_state, ft_sub, ft_region
                loc_source = 'full_text fallback'
        if subregion:
            notes.append(f"Location: {subregion}, {state or '?'} → region '{region}' ({loc_source})")
        elif state:
            notes.append(f"Location: {state} → region '{region}' ({loc_source})")
        elif region and region != 'Unknown':
            notes.append(f"Location: region '{region}' (no sub-region, {loc_source})")
        else:
            notes.append("Location: not detected")

        # -------------------------------------------------------------------
        # v3 Gaps 1,2,6,7: Known project matching
        # -------------------------------------------------------------------
        known_proj = _match_known_project(text_lower, primary_dev, subregion)
        if known_proj:
            notes.append(f"KNOWN PROJECT MATCH: '{known_proj['canonical_name']}'")
            # Override location/developer from known project if missing
            if not primary_dev and known_proj.get('developer'):
                primary_dev = known_proj['developer']
                notes.append(f"  → Developer filled from known DB: '{primary_dev}'")
            if not subregion and known_proj.get('location_subregion'):
                subregion = known_proj['location_subregion']
                notes.append(f"  → Subregion filled from known DB: '{subregion}'")
            if not state and known_proj.get('location_state'):
                state = known_proj['location_state']
            if not region and known_proj.get('region'):
                region = known_proj['region']
                notes.append(f"  → Region filled from known DB: '{region}'")
            # Add co-developers from known project
            if known_proj.get('co_developers'):
                for cd in known_proj['co_developers']:
                    if cd not in co_devs and cd != primary_dev:
                        co_devs.append(cd)
                        notes.append(f"  → Co-developer from known DB: '{cd}'")
        else:
            notes.append("Known project: no match")

        # Technology detection — title+snippet first, full_text fallback
        technology = _match_first_with_boundaries(
            primary_lower, _TECHNOLOGY_KEYWORDS, _TECHNOLOGY_KEYWORDS_SHORT)
        if not technology and full_text:
            technology = _match_first_with_boundaries(
                text_lower, _TECHNOLOGY_KEYWORDS, _TECHNOLOGY_KEYWORDS_SHORT)

        # v3 Gap 4: If SMR detected, verify it's not nuclear SMR
        if technology == 'SMR' and _is_nuclear_smr(text_lower):
            notes.append("Technology: SMR detected but nuclear context found → reset to Unknown")
            technology = None

        # v3 Gap 7: Known project technology override
        if known_proj and known_proj.get('technology'):
            known_tech = known_proj['technology']
            if technology is None or technology == 'Unknown':
                technology = known_tech
                notes.append(f"Technology: '{known_tech}' (from known project DB)")
            elif technology != known_tech:
                notes.append(f"Technology: detected '{technology}' but known project says "
                             f"'{known_tech}' — using known project override")
                technology = known_tech
            else:
                notes.append(f"Technology: '{technology}' (confirmed by known project DB)")
        elif technology:
            notes.append(f"Technology: {technology}")
        else:
            notes.append("Technology: not detected → default 'Unknown'")

        product = _match_first(primary_lower, _PRODUCT_KEYWORDS)
        if not product and full_text:
            product = _match_first(text_lower, _PRODUCT_KEYWORDS)
        # v3: Known project product override
        if known_proj and known_proj.get('product'):
            product = known_proj['product']
        notes.append(f"Product: {product or 'Hydrogen (default)'}")

        # Context-aware status (v3: word boundaries for fid/feed)
        status, status_confirmed = _extract_status_with_context(text_lower)
        if status:
            note = f"Status: '{status}'"
            if status_confirmed:
                note += " — confirmed (past-tense/achievement verb in context)"
            notes.append(note)
            notes.append(f"  → FID probability: {STATUS_FID_PROBABILITY.get(status, 0.10):.0%} "
                         f"(default — override via FID Probability Engine)")
        else:
            notes.append("Status: not detected → default 'Announced' (FID prob 10%)")

        # Capacity with validation (v3: ranges + known project cross-check)
        # Try title+snippet first — full_text may contain unrelated capacity figures
        cap_mtpa, cap_raw, cap_uncertain, cap_v_notes = _extract_capacity(
            primary_text, known_proj, technology)
        cap_source = 'title+snippet'
        if cap_mtpa is None and full_text:
            cap_mtpa, cap_raw, cap_uncertain, cap_v_notes = _extract_capacity(
                text, known_proj, technology)
            if cap_mtpa is not None:
                cap_source = 'full_text fallback'
        if cap_mtpa is not None:
            note = f"Capacity: raw='{cap_raw}' → {cap_mtpa:.4f} MTPA H2 ({cap_source})"
            if cap_uncertain:
                note += " (UNCERTAIN: no H2/NH3 qualifier)"
            notes.append(note)
        else:
            notes.append("Capacity: not detected")
        if cap_v_notes:
            for vn in cap_v_notes:
                notes.append(f"  Validation: {vn}")

        deal_val = _extract_dollar_value(text)
        if deal_val:
            notes.append(f"Deal value: ${deal_val:,.0f}")

        fid_date = _extract_date(text) if status in ('FID', 'EPC Award', 'Construction') else None
        cod_date = None
        if fid_date:
            notes.append(f"FID/milestone date: {fid_date}")

        # Policy
        policies = [p for p in _POLICY_KEYWORDS if p in text_lower]
        policy_signal = ', '.join(policies) if policies else None
        if policy_signal:
            notes.append(f"Policy signals: {policy_signal}")

        # -------------------------------------------------------------------
        # NLP SUPPLEMENT: spaCy NER  (fills gaps only — regex takes priority)
        # -------------------------------------------------------------------
        ner_entities = _spacy_extract(text)
        ner_used = False

        if ner_entities:
            notes.append("--- spaCy NER results ---")
            for label in ('ORG', 'GPE', 'MONEY', 'DATE'):
                ents = ner_entities.get(label, [])
                if ents:
                    notes.append(f"  {label}: {ents[:8]}"
                                 + (" ..." if len(ents) > 8 else ""))

            # NER → Developer discovery
            if not primary_dev and ner_entities.get('ORG'):
                for org in ner_entities['ORG']:
                    org_l = org.lower().strip()
                    for dev in _DEVELOPERS:
                        if dev in org_l or org_l in dev:
                            primary_dev = dev
                            ner_used = True
                            notes.append(f"  → NER filled developer gap: '{dev}' "
                                         f"(matched ORG entity '{org}')")
                            break
                    if primary_dev:
                        break

            # NER → EPC contractor discovery
            if not contractors and ner_entities.get('ORG'):
                for org in ner_entities['ORG']:
                    org_l = org.lower().strip()
                    for epc in _EPC_CONTRACTORS:
                        if epc in org_l or org_l in epc:
                            contractors = [epc]
                            ner_used = True
                            notes.append(f"  → NER filled EPC gap: '{epc}' "
                                         f"(matched ORG entity '{org}')")
                            break
                    if contractors:
                        break

            # NER → Location discovery (uses module-level _STATE_REGION_MAP)
            if not region and ner_entities.get('GPE'):
                for gpe in ner_entities['GPE']:
                    gpe_l = gpe.lower().strip()
                    if gpe_l in _SUBREGION_MAP:
                        state, subregion, region = _SUBREGION_MAP[gpe_l]
                        ner_used = True
                        notes.append(f"  → NER filled location gap: '{gpe}' "
                                     f"→ {subregion}, {state} ({region})")
                        break
                    if gpe_l in _STATE_REGION_MAP:
                        st, reg = _STATE_REGION_MAP[gpe_l]
                        state = st
                        region = reg
                        ner_used = True
                        notes.append(f"  → NER filled location gap: '{gpe}' → region '{reg}'")
                        break

            # NER → Deal value discovery
            if deal_val is None and ner_entities.get('MONEY'):
                for money_str in ner_entities['MONEY']:
                    m_val = _extract_dollar_value(money_str)
                    if m_val:
                        deal_val = m_val
                        ner_used = True
                        notes.append(f"  → NER filled deal value gap: "
                                     f"${deal_val:,.0f} (from '{money_str}')")
                        break

            # NER → Novel ORGs
            known_lower = set(_DEVELOPERS + _EPC_CONTRACTORS)
            novel_orgs = [o for o in ner_entities.get('ORG', [])
                          if o.lower().strip() not in known_lower
                          and len(o) > 2
                          and not any(k in o.lower() for k in known_lower)]
            if novel_orgs:
                notes.append(f"  Novel ORGs (not in known lists): {novel_orgs[:5]}")

            if not ner_used:
                notes.append("  → NER: no gaps filled (regex covered all fields)")
        else:
            if _SPACY_AVAILABLE:
                notes.append("spaCy NER: ran but found no entities")

        # -------------------------------------------------------------------
        # LLM SUPPLEMENT: Ollama extraction  (fills gaps + corroboration)
        # -------------------------------------------------------------------
        llm_used = False
        llm_corroborations = 0
        if article.get('llm_extracted'):
            notes.append("--- LLM extraction results ---")

            # LLM → Developer
            llm_dev = article.get('llm_developer')
            if llm_dev and isinstance(llm_dev, str) and llm_dev.strip():
                llm_dev = llm_dev.strip().lower()
                if not primary_dev:
                    primary_dev = llm_dev
                    llm_used = True
                    notes.append(f"  → LLM filled developer gap: '{llm_dev}'")
                elif llm_dev == primary_dev:
                    llm_corroborations += 1
                    notes.append(f"  → LLM corroborates developer: '{primary_dev}'")
                else:
                    notes.append(f"  → LLM developer '{llm_dev}' differs from "
                                 f"regex '{primary_dev}' — keeping regex")

            # LLM → Co-developers
            llm_co = article.get('llm_co_developers')
            if llm_co:
                try:
                    co_list = json.loads(llm_co) if isinstance(llm_co, str) else llm_co
                    if isinstance(co_list, list):
                        for cd in co_list:
                            if isinstance(cd, str) and cd.strip():
                                cd_lower = cd.strip().lower()
                                if cd_lower not in co_devs and cd_lower != primary_dev:
                                    co_devs.append(cd_lower)
                                    llm_used = True
                                    notes.append(f"  → LLM added co-developer: '{cd_lower}'")
                except (json.JSONDecodeError, TypeError):
                    pass

            # LLM → EPC contractor
            llm_epc = article.get('llm_epc_contractor')
            if llm_epc and isinstance(llm_epc, str) and llm_epc.strip():
                llm_epc = llm_epc.strip().lower()
                if not contractors:
                    contractors = [llm_epc]
                    llm_used = True
                    notes.append(f"  → LLM filled EPC gap: '{llm_epc}'")
                elif llm_epc == contractors[0]:
                    llm_corroborations += 1
                    notes.append(f"  → LLM corroborates EPC: '{contractors[0]}'")

            # LLM → Location
            llm_state = article.get('llm_location_state')
            llm_sub = article.get('llm_location_subregion')
            llm_reg = article.get('llm_region')
            if llm_state and isinstance(llm_state, str) and llm_state.strip():
                llm_state = llm_state.strip()
                if not state:
                    state = llm_state
                    llm_used = True
                    notes.append(f"  → LLM filled state gap: '{llm_state}'")
            if llm_sub and isinstance(llm_sub, str) and llm_sub.strip():
                llm_sub = llm_sub.strip()
                if not subregion:
                    subregion = llm_sub
                    llm_used = True
                    notes.append(f"  → LLM filled subregion gap: '{llm_sub}'")
            if llm_reg and isinstance(llm_reg, str) and llm_reg.strip():
                llm_reg = llm_reg.strip()
                if not region:
                    region = llm_reg
                    llm_used = True
                    notes.append(f"  → LLM filled region gap: '{llm_reg}'")

            # LLM → Technology
            llm_tech = article.get('llm_technology')
            if llm_tech and isinstance(llm_tech, str) and llm_tech.strip():
                llm_tech = llm_tech.strip()
                if not technology or technology == 'Unknown':
                    technology = llm_tech
                    llm_used = True
                    notes.append(f"  → LLM filled technology gap: '{llm_tech}'")
                elif llm_tech.upper() == technology.upper():
                    llm_corroborations += 1
                    notes.append(f"  → LLM corroborates technology: '{technology}'")
                else:
                    notes.append(f"  → LLM technology '{llm_tech}' differs from "
                                 f"'{technology}' — keeping existing")

            # LLM → Product
            llm_prod = article.get('llm_product')
            if llm_prod and isinstance(llm_prod, str) and llm_prod.strip():
                llm_prod = llm_prod.strip()
                if not product:
                    product = llm_prod
                    llm_used = True
                    notes.append(f"  → LLM filled product gap: '{llm_prod}'")

            # LLM → Status (with advancement logic)
            llm_status_val = article.get('llm_status')
            llm_status_conf = article.get('llm_status_confirmed')
            if llm_status_val and isinstance(llm_status_val, str) and llm_status_val.strip():
                llm_status_val = llm_status_val.strip()
                if not status:
                    status = llm_status_val
                    llm_used = True
                    notes.append(f"  → LLM filled status gap: '{llm_status_val}'")
                    if llm_status_conf:
                        status_confirmed = True
                        notes.append("  → LLM confirms status")
                elif llm_status_val == status:
                    llm_corroborations += 1
                    notes.append(f"  → LLM corroborates status: '{status}'")
                    if llm_status_conf and not status_confirmed:
                        status_confirmed = True
                        notes.append("  → LLM confirms status (upgraded)")
                else:
                    # If LLM found a more advanced status, use it
                    llm_ord = _STATUS_ORDER.get(llm_status_val, -99)
                    reg_ord = _STATUS_ORDER.get(status, -99)
                    if llm_ord > reg_ord and llm_ord != -99:
                        notes.append(f"  → LLM status '{llm_status_val}' more advanced "
                                     f"than regex '{status}' — upgrading")
                        status = llm_status_val
                        llm_used = True
                        if llm_status_conf:
                            status_confirmed = True
                    else:
                        notes.append(f"  → LLM status '{llm_status_val}' vs regex "
                                     f"'{status}' — keeping existing")

            # LLM → Capacity
            llm_cap = article.get('llm_capacity_mtpa_h2')
            llm_cap_raw = article.get('llm_capacity_raw')
            if llm_cap is not None and cap_mtpa is None:
                try:
                    llm_cap_val = float(llm_cap)
                    if 0.0001 <= llm_cap_val <= 10.0:  # Sanity range
                        cap_mtpa = llm_cap_val
                        cap_raw = llm_cap_raw or f"{llm_cap_val} MTPA"
                        cap_uncertain = False
                        llm_used = True
                        notes.append(f"  → LLM filled capacity gap: {llm_cap_val:.4f} MTPA "
                                     f"(raw: '{cap_raw}')")
                    else:
                        notes.append(f"  → LLM capacity {llm_cap_val} MTPA out of sane "
                                     f"range [0.0001–10] — ignored")
                except (ValueError, TypeError):
                    pass

            # LLM → Deal value
            llm_deal = article.get('llm_deal_value_usd')
            if llm_deal is not None and deal_val is None:
                try:
                    llm_deal_val = float(llm_deal)
                    if llm_deal_val > 0:
                        deal_val = llm_deal_val
                        llm_used = True
                        notes.append(f"  → LLM filled deal value gap: ${llm_deal_val:,.0f}")
                except (ValueError, TypeError):
                    pass

            # LLM → Dates
            llm_fid = article.get('llm_fid_date')
            llm_cod = article.get('llm_cod_date')
            if llm_fid and isinstance(llm_fid, str) and llm_fid.strip() and not fid_date:
                fid_date = llm_fid.strip()
                llm_used = True
                notes.append(f"  → LLM filled FID date gap: '{fid_date}'")
            if llm_cod and isinstance(llm_cod, str) and llm_cod.strip() and not cod_date:
                cod_date = llm_cod.strip()
                llm_used = True
                notes.append(f"  → LLM filled COD date gap: '{cod_date}'")

            if not llm_used and llm_corroborations == 0:
                notes.append("  → LLM: no gaps filled, no corroborations")
            elif llm_corroborations > 0:
                notes.append(f"  → LLM corroborations: {llm_corroborations} field(s) confirmed")

        # Project name extraction (v3: known project first)
        # v4: LLM project name used as fallback if regex fails
        llm_project_name = None
        if article.get('llm_extracted'):
            llm_pn = article.get('llm_project_name')
            if llm_pn and isinstance(llm_pn, str) and llm_pn.strip():
                llm_project_name = llm_pn.strip()
        project_name, name_pattern = _extract_project_name(
            title, text, primary_dev, subregion, product, known_proj)
        if project_name:
            notes.append(f"Project name: '{project_name}' ({name_pattern})")
        elif llm_project_name:
            project_name = llm_project_name
            name_pattern = 'llm_extraction'
            llm_used = True
            notes.append(f"Project name: '{project_name}' (from LLM extraction)")
        else:
            notes.append("Project name: not extracted")

        # -------------------------------------------------------------------
        # v3 Gap 5: Relevance filter — skip generic signals
        # -------------------------------------------------------------------
        # A signal is "generic" if it has no developer, no location, no capacity,
        # and no status beyond Announced. These create noise in the project pipeline.
        has_specifics = (
            primary_dev
            or cap_mtpa is not None
            or subregion
            or (status and status not in ('Announced', 'Unknown'))
            or contractors
            or known_proj
        )
        if not has_specifics:
            notes.append("FILTERED: No project-specific information detected — "
                         "skipping generic signal")
            return None

        # Confidence model
        notes.append("--- Confidence calculation ---")
        # Layer 1: Signal completeness
        confidence_score = 0.10
        l1_parts = ["base=0.10"]
        if primary_dev:
            confidence_score += 0.15
            l1_parts.append("developer +0.15")
        if cap_mtpa:
            confidence_score += 0.15
            l1_parts.append("capacity +0.15")
            if cap_uncertain:
                confidence_score -= 0.05
                l1_parts.append("uncertain_unit -0.05")
        if technology and technology != 'Unknown':
            confidence_score += 0.10
            l1_parts.append("technology +0.10")
        if status:
            confidence_score += 0.10
            l1_parts.append("status +0.10")
        if subregion:
            confidence_score += 0.10
            l1_parts.append("location +0.10")
        if contractors:
            confidence_score += 0.10
            l1_parts.append("epc +0.10")
        if known_proj:
            confidence_score += 0.10
            l1_parts.append("known_project +0.10")
        notes.append(f"  Layer 1 (completeness): {' | '.join(l1_parts)} = {confidence_score:.2f}")

        # Layer 2: Article quality
        l2_add = 0.0
        l2_label = ''
        if priority_score >= 10:
            l2_add = 0.15
            l2_label = f'CRITICAL (score={priority_score})'
        elif priority_score >= 5:
            l2_add = 0.10
            l2_label = f'HIGH (score={priority_score})'
        elif priority_score >= 2:
            l2_add = 0.05
            l2_label = f'MEDIUM (score={priority_score})'
        else:
            l2_label = f'LOW (score={priority_score})'
        confidence_score += l2_add
        notes.append(f"  Layer 2 (article quality): {l2_label} → +{l2_add:.2f}")

        if status_confirmed:
            confidence_score += 0.05
            notes.append("  Status confirmed bonus: +0.05")

        # Layer 3: LLM extraction bonus
        if article.get('llm_extracted'):
            if llm_corroborations >= 2:
                confidence_score += 0.10
                notes.append(f"  LLM corroboration bonus: +0.10 ({llm_corroborations} fields confirmed)")
            elif llm_used:
                confidence_score += 0.05
                notes.append("  LLM gap-fill bonus: +0.05")

        pre_decay = min(1.0, confidence_score)
        confidence_score = _confidence_decay(pre_decay, pub_date)
        if pub_date:
            try:
                from dateutil import parser as dp
                d = dp.parse(pub_date)
                months = (datetime.now() - d.replace(tzinfo=None)).days / 30.0
                notes.append(f"  Decay: {months:.1f} months old → ×{0.95 ** max(0, months):.3f} "
                             f"({pre_decay:.2f} → {confidence_score:.2f})")
            except Exception:
                notes.append(f"  Decay: date unparseable → ×0.80 penalty ({pre_decay:.2f} → {confidence_score:.2f})")
        else:
            notes.append(f"  Decay: no date → ×0.80 penalty ({pre_decay:.2f} → {confidence_score:.2f})")
        notes.append(f"  Final confidence: {confidence_score:.1%}")

        # Tags
        tags = []
        if status:
            tags.append('project_status')
        if contractors:
            tags.append('epc_signal')
        if cap_mtpa:
            tags.append('capacity_signal')
        if policy_signal:
            tags.append('policy_signal')
        if deal_val:
            tags.append('deal_signal')
        if cap_uncertain:
            tags.append('capacity_uncertain')
        if co_devs:
            tags.append('joint_venture')
        if known_proj:
            tags.append('known_project')
        if article.get('llm_extracted'):
            tags.append('llm_extracted')
            if llm_corroborations >= 2:
                tags.append('llm_corroborated')

        return ProjectSignal(
            article_id=article.get('id', 0),
            article_title=title,
            article_url=article.get('url', ''),
            article_date=pub_date,
            project_name=project_name,
            developer=primary_dev,
            co_developers=co_devs,
            capacity_mtpa_h2=cap_mtpa,
            capacity_raw=cap_raw,
            capacity_unit_uncertain=cap_uncertain,
            technology=technology or 'Unknown',
            product=product or 'Hydrogen',
            location_state=state,
            location_subregion=subregion,
            region=region or article.get('region', ''),
            status=status or 'Announced',
            status_confirmed=status_confirmed,
            fid_date=fid_date,
            cod_date=cod_date,
            epc_contractor=contractors[0] if contractors else None,
            offtake_buyer=None,
            deal_value_usd=deal_val,
            policy_signal=policy_signal,
            confidence=confidence_score,
            tags=tags,
            parsing_notes=notes,
            known_project_match=known_proj['canonical_name'] if known_proj else None,
        )

    def parse_recent(self, days: int = 7, min_score: int = 0,
                     region: Optional[str] = None) -> List[ProjectSignal]:
        """Parse recent articles from the database.

        Args:
            days: How many days back to look.
            min_score: Minimum priority_score.
            region: If set, only parse articles from this region.
                    Use exact region name (e.g., 'Midwest', 'Gulf Coast TX',
                    'Germany'). None = all regions.

        v3: parse_article now returns None for generic/irrelevant articles,
        so we filter those out.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        if region:
            rows = conn.execute('''
                SELECT * FROM articles
                WHERE priority_score >= ? AND region = ?
                ORDER BY priority_score DESC
            ''', (min_score, region)).fetchall()
        else:
            rows = conn.execute('''
                SELECT * FROM articles
                WHERE priority_score >= ?
                ORDER BY priority_score DESC
            ''', (min_score,)).fetchall()
        conn.close()

        from datetime import timedelta
        cutoff_dt = datetime.now() - timedelta(days=days)
        filtered_rows = []
        for r in rows:
            d = dict(r)
            date_str = d.get('published_date') or d.get('fetched_date') or ''
            art_dt = _parse_any_date(date_str)
            if art_dt and art_dt >= cutoff_dt:
                filtered_rows.append(d)
        rows = filtered_rows

        signals = []
        nuclear_skipped = 0
        generic_skipped = 0
        for art in rows:
            sig = self.parse_article(art)
            if sig is None:
                # v3: parse_article returned None (nuclear or generic)
                title_l = (art.get('title', '') or '').lower()
                if _is_nuclear_smr(title_l):
                    nuclear_skipped += 1
                else:
                    generic_skipped += 1
                continue
            if sig.developer or sig.capacity_mtpa_h2 or sig.status != 'Announced':
                signals.append(sig)

        if nuclear_skipped:
            _log.info("Skipped %d nuclear SMR articles", nuclear_skipped)
        if generic_skipped:
            _log.info("Skipped %d generic articles (no project specifics)", generic_skipped)

        # Populate deals and risks tables from parsed signals
        self._record_deals_and_risks(signals)
        return signals

    def _record_deals_and_risks(self, signals: List[ProjectSignal]):
        """Insert deal and risk records from parsed signals into the DB."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        for sig in signals:
            if sig.deal_value_usd or sig.status in ('EPC Award', 'FID'):
                deal_type = sig.status or 'Unknown'
                if sig.epc_contractor:
                    deal_type = f"EPC Award ({sig.epc_contractor})"
                parties = sig.developer or ''
                if sig.co_developers:
                    parties += ', ' + ', '.join(sig.co_developers)
                exists = c.execute(
                    'SELECT 1 FROM deals WHERE source_article_id = ?',
                    (sig.article_id,)).fetchone()
                if not exists:
                    c.execute('''INSERT INTO deals
                        (deal_date, deal_type, parties, value_usd, region,
                         description, signal, source_article_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                        (sig.article_date, deal_type, parties,
                         sig.deal_value_usd, sig.region,
                         sig.article_title[:200], sig.status,
                         sig.article_id))

            if sig.status in ('Cancelled', 'Delayed'):
                risk_cat = 'Project Risk'
                risk_desc = f"{(sig.developer or 'Unknown').title()} — " \
                            f"{sig.project_name or 'project'}: {sig.status}"
                exists = c.execute(
                    'SELECT 1 FROM risks WHERE source_article_id = ?',
                    (sig.article_id,)).fetchone()
                if not exists:
                    c.execute('''INSERT INTO risks
                        (risk_category, risk_description, status, trend,
                         impact_level, last_updated, source_article_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (risk_cat, risk_desc, sig.status,
                         'Deteriorating' if sig.status == 'Cancelled' else 'Uncertain',
                         'HIGH' if sig.status == 'Cancelled' else 'MEDIUM',
                         datetime.now().isoformat(), sig.article_id))

            if sig.policy_signal and any(w in (sig.policy_signal or '').lower()
                                         for w in ['repeal', 'rollback', 'expire']):
                exists = c.execute(
                    'SELECT 1 FROM risks WHERE source_article_id = ? AND risk_category = ?',
                    (sig.article_id, 'Policy Risk')).fetchone()
                if not exists:
                    c.execute('''INSERT INTO risks
                        (risk_category, risk_description, status, trend,
                         impact_level, last_updated, source_article_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        ('Policy Risk', f"Policy signal: {sig.policy_signal}",
                         'Active', 'Watch',
                         'HIGH', datetime.now().isoformat(), sig.article_id))

        conn.commit()
        conn.close()

    # -- Deduplication -------------------------------------------------------

    def deduplicate_to_projects(self, signals: List[ProjectSignal],
                                return_clusters: bool = False):
        """Merge multiple signals about the same project into Project records.

        v3: Known-project-aware clustering — signals matching the same known
        project always land in the same cluster. Unknown-tech signals absorbed
        into tech-specific clusters for the same developer+location.
        """
        clusters: Dict[str, List[ProjectSignal]] = {}

        for sig in signals:
            key = self._cluster_key(sig, clusters)
            if key not in clusters:
                clusters[key] = []
            clusters[key].append(sig)

        projects = []
        project_clusters: Dict[str, List[ProjectSignal]] = {}
        for key, sigs in clusters.items():
            proj = self._merge_signals(sigs)
            projects.append(proj)
            project_clusters[proj.project_name] = sigs

        self._save_projects(projects)

        if return_clusters:
            return projects, project_clusters
        return projects

    def _cluster_key(self, sig: ProjectSignal, existing: Dict[str, list]) -> str:
        """Find matching cluster or create new key.

        v3 improvements:
          0. Known project match → canonical key (strongest anchor)
          1. Exact match on (dev, loc, tech) → merge
          2. Same dev + same tech, one has empty region → merge (absorb)
          2b. Same dev + same loc, one has Unknown tech → merge (absorb)
          3. Fuzzy project name match → merge (with location guard)
          4. JV: any developer overlap at same location → merge
        """
        dev = (sig.developer or '').lower().strip()
        loc = (sig.location_subregion or sig.region or '').lower().strip()
        tech = (sig.technology or 'unknown').lower().strip()

        # -------------------------------------------------------------------
        # v3 Rule 0: Known project anchor — strongest dedup signal
        # -------------------------------------------------------------------
        if sig.known_project_match:
            canonical = sig.known_project_match
            # Find existing cluster that has the same known project
            for ex_key, ex_sigs in existing.items():
                for es in ex_sigs:
                    if es.known_project_match == canonical:
                        return ex_key
            # No existing cluster — create one keyed by canonical name
            return f"known|{canonical}"

        # All developers for this signal
        sig_all_devs = {dev} | {d.lower().strip() for d in sig.co_developers}
        sig_all_devs.discard('')

        for ex_key, ex_sigs in existing.items():
            # Parse existing key
            if ex_key.startswith('known|'):
                # Known-project cluster — check if this signal should join it
                ex_canonical = ex_key[6:]
                # Find the known project entry
                kp = None
                for p in _KNOWN_PROJECTS:
                    if p['canonical_name'] == ex_canonical:
                        kp = p
                        break
                if kp:
                    # Check if developer matches
                    kp_dev = (kp.get('developer') or '').lower()
                    if dev and kp_dev and dev == kp_dev:
                        kp_loc = (kp.get('location_subregion') or kp.get('region') or '').lower()
                        # Same developer, compatible location
                        if not loc or not kp_loc or loc == kp_loc:
                            return ex_key
                continue

            parts = ex_key.split('|')
            if len(parts) != 3:
                continue
            ex_dev, ex_loc, ex_tech = parts

            # All devs in existing cluster
            ex_all_devs = {ex_dev}
            for s in ex_sigs:
                ex_all_devs.update(d.lower().strip() for d in s.co_developers)
            ex_all_devs.discard('')

            # Rule 1: Exact match (dev + loc + tech)
            if dev and ex_dev and dev == ex_dev and loc and ex_loc and loc == ex_loc:
                if tech == ex_tech:
                    return ex_key

            # Rule 2: Same developer, same tech, one has empty region → absorb
            if dev and ex_dev and dev == ex_dev and tech == ex_tech and (not loc or not ex_loc):
                location_conflict = False
                if loc:
                    for s in ex_sigs:
                        s_loc = (s.location_subregion or s.region or '').lower().strip()
                        if s_loc and s_loc != loc:
                            location_conflict = True
                            break
                if not location_conflict and not self._capacity_conflict(sig, ex_sigs):
                    return ex_key

            # Rule 2b (v3): Same dev + same loc, one has Unknown tech → absorb
            if dev and ex_dev and dev == ex_dev and loc and ex_loc and loc == ex_loc:
                if tech == 'unknown' or ex_tech == 'unknown':
                    if not self._capacity_conflict(sig, ex_sigs):
                        return ex_key

            # Location guard for remaining rules
            if loc and ex_loc and loc != ex_loc:
                continue

            # Rule 3: Project name similarity
            if sig.project_name and ex_sigs:
                ex_name = ex_sigs[0].project_name or ''
                if ex_name:
                    sem_score = _NLPEngine.cosine_sim(
                        sig.project_name.lower(), ex_name.lower())
                    if sem_score is not None:
                        if sem_score > 0.70:
                            return ex_key
                    else:
                        ratio = difflib.SequenceMatcher(
                            None, sig.project_name.lower(),
                            ex_name.lower()).ratio()
                        if ratio > 0.75:
                            return ex_key

            # Rule 4: JV overlap at same location
            if sig_all_devs and ex_all_devs and sig_all_devs & ex_all_devs:
                if loc and ex_loc and loc == ex_loc:
                    if not self._capacity_conflict(sig, ex_sigs):
                        return ex_key

        return f"{dev}|{loc}|{tech}"

    def _capacity_conflict(self, sig: ProjectSignal, ex_sigs: List[ProjectSignal]) -> bool:
        """Check if capacities conflict (>2x difference → likely different projects)."""
        if sig.capacity_mtpa_h2 is None:
            return False
        for s in ex_sigs:
            if s.capacity_mtpa_h2 is not None and s.capacity_mtpa_h2 > 0:
                ratio = sig.capacity_mtpa_h2 / s.capacity_mtpa_h2
                if ratio < 0.5 or ratio > 2.0:
                    return True
        return False

    def _merge_signals(self, sigs: List[ProjectSignal]) -> Project:
        """Merge a cluster of signals into a single Project."""
        merge_notes: List[str] = []

        sigs_sorted = sorted(sigs, key=lambda s: s.article_date or '', reverse=True)
        latest = sigs_sorted[0]

        merge_notes.append(f"Merged {len(sigs)} signal(s) into one project record")
        if len(sigs) > 1:
            dates = [s.article_date for s in sigs_sorted if s.article_date]
            if dates:
                merge_notes.append(f"  Date range: {dates[-1]} to {dates[0]}")

            if _SBERT_AVAILABLE and _NLPEngine.sbert() is not None:
                merge_notes.append("  Dedup method: semantic similarity (all-MiniLM-L6-v2, threshold=0.70)")
            else:
                merge_notes.append("  Dedup method: difflib SequenceMatcher (threshold=0.75)")

            ner_fills = sum(1 for s in sigs
                           if any('NER filled' in n for n in s.parsing_notes))
            if ner_fills:
                merge_notes.append(f"  NER gap-fills: {ner_fills}/{len(sigs)} signal(s)")

            # v3: Report known project matches
            known_matches = [s.known_project_match for s in sigs if s.known_project_match]
            if known_matches:
                merge_notes.append(f"  Known project matches: {len(known_matches)}/{len(sigs)} signals")

        # Best status (most advanced confirmed)
        def status_sort_key(s):
            order = _STATUS_ORDER.get(s.status or 'Unknown', 0)
            confirmed_bonus = 0.5 if s.status_confirmed else 0
            return order + confirmed_bonus
        best_status_sig = max(sigs, key=status_sort_key)

        def first_non_none(attr: str):
            for s in sigs_sorted:
                v = getattr(s, attr, None)
                if v is not None and v != '' and v != 'Unknown':
                    return v
            return getattr(latest, attr, None)

        status = best_status_sig.status or 'Announced'
        fid_prob = STATUS_FID_PROBABILITY.get(status, 0.10)

        # Status resolution log
        status_counts: Dict[str, int] = {}
        for s in sigs:
            st = s.status or 'Announced'
            status_counts[st] = status_counts.get(st, 0) + 1
        if len(status_counts) > 1:
            breakdown = ', '.join(f"{st}={n}" for st, n in sorted(status_counts.items(),
                                  key=lambda x: -_STATUS_ORDER.get(x[0], 0)))
            merge_notes.append(f"  Status resolution: chose '{status}' "
                               f"({'confirmed' if best_status_sig.status_confirmed else 'unconfirmed'}) "
                               f"from [{breakdown}]")
        else:
            merge_notes.append(f"  Status: '{status}' (unanimous across signals)")
        merge_notes.append(f"  → FID probability: {fid_prob:.0%}")

        # Collect co-developers
        all_co_devs: set = set()
        for s in sigs:
            all_co_devs.update(s.co_developers)
        primary_dev = first_non_none('developer')
        if primary_dev:
            all_co_devs.discard(primary_dev)

        # v3: Use known project name if available
        project_name = None
        for s in sigs_sorted:
            if s.known_project_match:
                project_name = s.known_project_match
                break
        if not project_name:
            project_name = first_non_none('project_name') or 'Unknown Project'

        # Capacity resolution log
        caps = [(s.capacity_mtpa_h2, s.capacity_raw, s.article_title[:50])
                for s in sigs if s.capacity_mtpa_h2 is not None]
        chosen_cap = first_non_none('capacity_mtpa_h2')
        if len(caps) > 1:
            cap_strs = [f"{c:.4f} ('{r}' from \"{t}\")" for c, r, t in caps]
            merge_notes.append(f"  Capacity: {len(caps)} signals reported capacity — "
                               f"used {chosen_cap:.4f} MTPA (most recent non-None)")
            for cs in cap_strs:
                merge_notes.append(f"    - {cs}")
        elif caps:
            merge_notes.append(f"  Capacity: {chosen_cap:.4f} MTPA (single source)")
        else:
            merge_notes.append("  Capacity: not reported in any signal")

        # Layer 3: Corroboration boost
        base_conf = sum(s.confidence for s in sigs) / len(sigs) if sigs else 0.0
        n = len(sigs)
        if n >= 12:
            corroboration = 1.25
        elif n >= 8:
            corroboration = 1.20
        elif n >= 5:
            corroboration = 1.15
        elif n >= 3:
            corroboration = 1.10
        elif n >= 2:
            corroboration = 1.05
        else:
            corroboration = 1.0
        conf = min(1.0, base_conf * corroboration)

        merge_notes.append(f"  Confidence: avg per-signal={base_conf:.2f} × "
                           f"corroboration({n} arts)={corroboration:.2f} → {conf:.1%}")

        any_uncertain = any(s.capacity_unit_uncertain for s in sigs)
        if any_uncertain:
            merge_notes.append("  Warning: capacity unit uncertain in one or more signals")

        # v3: Technology — prefer known project, then most common non-Unknown
        tech = None
        for s in sigs_sorted:
            if s.known_project_match:
                kp = _match_known_project('', s.developer, s.location_subregion)
                if kp and kp.get('technology'):
                    tech = kp['technology']
                    break
        if not tech:
            # Most common non-Unknown technology across signals
            tech_counts: Dict[str, int] = {}
            for s in sigs:
                t = s.technology or 'Unknown'
                if t != 'Unknown':
                    tech_counts[t] = tech_counts.get(t, 0) + 1
            if tech_counts:
                tech = max(tech_counts, key=tech_counts.get)
            else:
                tech = first_non_none('technology')

        return Project(
            project_name=project_name,
            developer=primary_dev,
            co_developers=sorted(all_co_devs),
            capacity_mtpa_h2=chosen_cap,
            capacity_raw=first_non_none('capacity_raw'),
            capacity_unit_uncertain=any_uncertain,
            technology=tech,
            product=first_non_none('product'),
            location_state=first_non_none('location_state'),
            location_subregion=first_non_none('location_subregion'),
            region=first_non_none('region') or '',
            status=status,
            fid_date=first_non_none('fid_date'),
            cod_date=first_non_none('cod_date'),
            epc_contractor=first_non_none('epc_contractor'),
            fid_probability=fid_prob,
            confidence=conf,
            last_updated=datetime.now().isoformat(),
            source_article_ids=[s.article_id for s in sigs],
            source_article_titles=[s.article_title for s in sigs if s.article_title],
            source_article_urls=[s.article_url for s in sigs if s.article_url],
            merge_notes=merge_notes,
        )

    def _save_projects(self, projects: List[Project]):
        """Persist projects to the projects table."""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('DELETE FROM projects')

        try:
            c.execute('ALTER TABLE projects ADD COLUMN co_developers TEXT DEFAULT "[]"')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE projects ADD COLUMN capacity_unit_uncertain BOOLEAN DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE projects ADD COLUMN source_article_titles TEXT DEFAULT "[]"')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE projects ADD COLUMN source_article_urls TEXT DEFAULT "[]"')
        except sqlite3.OperationalError:
            pass

        for p in projects:
            c.execute('''
                INSERT INTO projects
                (project_name, developer, co_developers, capacity_mtpa_h2, capacity_raw,
                 capacity_unit_uncertain, technology, product, location_state,
                 location_subregion, region, status, fid_date, cod_date, epc_contractor,
                 fid_probability, confidence, last_updated, source_article_ids,
                 source_article_titles, source_article_urls)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_name, developer, region, technology) DO UPDATE SET
                    co_developers = excluded.co_developers,
                    capacity_mtpa_h2 = COALESCE(excluded.capacity_mtpa_h2, projects.capacity_mtpa_h2),
                    capacity_raw = COALESCE(excluded.capacity_raw, projects.capacity_raw),
                    capacity_unit_uncertain = excluded.capacity_unit_uncertain,
                    product = COALESCE(excluded.product, projects.product),
                    location_state = COALESCE(excluded.location_state, projects.location_state),
                    location_subregion = COALESCE(excluded.location_subregion, projects.location_subregion),
                    status = excluded.status,
                    fid_date = COALESCE(excluded.fid_date, projects.fid_date),
                    cod_date = COALESCE(excluded.cod_date, projects.cod_date),
                    epc_contractor = COALESCE(excluded.epc_contractor, projects.epc_contractor),
                    fid_probability = excluded.fid_probability,
                    confidence = excluded.confidence,
                    last_updated = excluded.last_updated,
                    source_article_ids = excluded.source_article_ids,
                    source_article_titles = excluded.source_article_titles,
                    source_article_urls = excluded.source_article_urls
            ''', (
                p.project_name, p.developer, json.dumps(p.co_developers),
                p.capacity_mtpa_h2, p.capacity_raw, p.capacity_unit_uncertain,
                p.technology, p.product, p.location_state, p.location_subregion,
                p.region, p.status, p.fid_date, p.cod_date, p.epc_contractor,
                p.fid_probability, p.confidence, p.last_updated,
                json.dumps(p.source_article_ids), json.dumps(p.source_article_titles),
                json.dumps(p.source_article_urls),
            ))
        conn.commit()
        conn.close()

    # -- Query methods -------------------------------------------------------

    def get_project_pipeline(self) -> List[Project]:
        """Load all projects from persistent table."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute('SELECT * FROM projects ORDER BY fid_probability DESC').fetchall()
        conn.close()
        return [self._row_to_project(r) for r in rows]

    def get_pipeline_by_region(self) -> Dict[str, List[Project]]:
        projects = self.get_project_pipeline()
        by_region: Dict[str, List[Project]] = {}
        for p in projects:
            by_region.setdefault(p.region or 'Unknown', []).append(p)
        return by_region

    def get_pipeline_by_status(self) -> Dict[str, List[Project]]:
        projects = self.get_project_pipeline()
        by_status: Dict[str, List[Project]] = {}
        for p in projects:
            by_status.setdefault(p.status or 'Unknown', []).append(p)
        return by_status

    def get_pipeline_by_developer(self) -> Dict[str, List[Project]]:
        projects = self.get_project_pipeline()
        by_dev: Dict[str, List[Project]] = {}
        for p in projects:
            by_dev.setdefault(p.developer or 'Unknown', []).append(p)
        return by_dev

    def _row_to_project(self, row) -> Project:
        d = dict(row)

        def _load_json_list(key, default=None):
            val = d.get(key, '[]')
            try:
                return json.loads(val) if isinstance(val, str) else (val or default or [])
            except (json.JSONDecodeError, TypeError):
                return default or []

        return Project(
            project_name=d.get('project_name', ''),
            developer=d.get('developer'),
            co_developers=_load_json_list('co_developers'),
            capacity_mtpa_h2=d.get('capacity_mtpa_h2'),
            capacity_raw=d.get('capacity_raw'),
            capacity_unit_uncertain=bool(d.get('capacity_unit_uncertain', False)),
            technology=d.get('technology'),
            product=d.get('product'),
            location_state=d.get('location_state'),
            location_subregion=d.get('location_subregion'),
            region=d.get('region', ''),
            status=d.get('status'),
            fid_date=d.get('fid_date'),
            cod_date=d.get('cod_date'),
            epc_contractor=d.get('epc_contractor'),
            fid_probability=d.get('fid_probability', 0.10),
            confidence=d.get('confidence', 0.0),
            last_updated=d.get('last_updated', ''),
            source_article_ids=_load_json_list('source_article_ids'),
            source_article_titles=_load_json_list('source_article_titles'),
            source_article_urls=_load_json_list('source_article_urls'),
        )
