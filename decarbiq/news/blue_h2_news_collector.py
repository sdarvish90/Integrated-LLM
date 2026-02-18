#!/usr/bin/env python3
"""
Blue Hydrogen / Ammonia News Intelligence Collector
====================================================
Collects, scores, deduplicates, and stores news articles about blue hydrogen
and ammonia projects, focused on Gulf Coast / ERCOT region.

News sources are loaded from sites.yaml (single source of truth).
No hardcoded RSS feeds — everything is tag-driven.

v2 additions:
  - Google News URL resolver (base64 decode + HTTP fallback + cache)
  - SEC EDGAR 8-K monitoring for relevant public companies
  - newspaper3k for robust full-text extraction
  - Content hashing for cross-source deduplication
  - Paywall detection
  - RSS feed discovery for company newsrooms
  - Expanded search queries (project-specific, EPC, policy)

v3 additions:
  - Ollama LLM integration for structured extraction during collection
  - LLM extracts: project_name, developer, status, capacity, technology, etc.
  - Runs locally (FREE), no API costs
  - Graceful fallback if Ollama not available

Usage:
    from blue_h2_news_collector import BlueH2NewsCollector
    collector = BlueH2NewsCollector()

    # With LLM extraction (default for full collection)
    n = collector.collect_all(max_articles=200, use_llm=True)

    # Without LLM (faster, regex-only)
    n = collector.collect_all(max_articles=200, use_llm=False)

    # RSS-only (LLM off by default for speed)
    n = collector.collect_rss_only(use_llm=False)

Setup Ollama (one-time):
    curl -fsSL https://ollama.com/install.sh | sh
    ollama serve &
    ollama pull llama3.1:8b
"""
from __future__ import annotations

import base64
import os
import ssl
import certifi
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

# newspaper3k for full-text extraction (graceful fallback)
try:
    from newspaper import Article as _Newspaper3kArticle, ArticleException
    _NEWSPAPER3K_AVAILABLE = True
except ImportError:
    _NEWSPAPER3K_AVAILABLE = False
    _Newspaper3kArticle = None  # type: ignore[assignment,misc]
    ArticleException = Exception  # type: ignore[assignment,misc]

# 5-layer NLP pipeline (graceful fallback to keyword scoring)
try:
    from nlp.semantic_classifier import get_classifier as _get_classifier
    from nlp.entity_extractor import get_extractor as _get_extractor
    from nlp.article_store import get_store as _get_store
    _NLP_AVAILABLE = True
except ImportError:
    _NLP_AVAILABLE = False
    _get_classifier = None  # type: ignore[assignment]
    _get_extractor = None   # type: ignore[assignment]
    _get_store = None        # type: ignore[assignment]

# ---------------------------------------------------------------------------
# SSL fix for macOS
# ---------------------------------------------------------------------------
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

import urllib.request
_ssl_ctx = ssl.create_default_context(cafile=certifi.where())
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_ctx))
)

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
DATABASE_NAME = str(_HERE / 'blue_h2_intelligence.db')
SITES_YAML = str(_HERE / 'sites.yaml')

# Tags that this collector activates (loaded from sites.yaml)
ACTIVE_TAGS = {
    'blue_h2', 'general_hydrogen', 'us_energy', 'gulf_coast', 'us_policy',
    'press_release', 'project_specific', 'epc', 'company_ir',
}

# ---------------------------------------------------------------------------
# OLLAMA LLM CONFIGURATION (v3 — FREE local LLM extraction)
# ---------------------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"  # Good balance of speed/quality
OLLAMA_TIMEOUT = 120  # seconds per article (first call may be slow — cold start)

# Minimum priority score to trigger LLM extraction (saves time on low-value articles)
LLM_MIN_SCORE = 15

# LLM extraction prompt — tuned for consistent JSON output
_LLM_EXTRACTION_PROMPT = """You are extracting structured data from a hydrogen/ammonia industry news article.
Return ONLY valid JSON matching this exact schema. No explanation, no markdown, just JSON.

{
  "project_name": "official project name as stated, or null if not mentioned",
  "developer": "primary developer company name lowercase, or null",
  "co_developers": ["array of JV/partner company names lowercase, empty array if none"],
  "status": "one of: Announced|Pre-FEED|FEED|FID|EPC Award|Construction|Operational|Cancelled|Delayed|null",
  "status_confirmed": true if status is explicitly confirmed/past tense, false if speculative/planned,
  "capacity_raw": "exact capacity text as stated (e.g. '750 TPD', '1.2 MTPA'), or null",
  "capacity_mtpa_h2": converted capacity in MTPA hydrogen equivalent as number, or null,
  "technology": "SMR|ATR|Electrolysis|null (SMR=steam methane reforming, ATR=autothermal reforming)",
  "product": "Hydrogen|Ammonia|Methanol|SAF|null",
  "epc_contractor": "EPC/engineering contractor name, or null",
  "location_state": "US state name, or null",
  "location_subregion": "city or area name (e.g. Baytown, Lake Charles), or null",
  "region": "Gulf Coast TX|Gulf Coast LA|Appalachia|West Coast|Pacific Northwest|Midwest|Mountain West|Southeast|Northeast|Plains|US Other|International|null",
  "fid_date": "FID date/year if mentioned (e.g. '2025', 'Q3 2025'), or null",
  "cod_date": "commercial operation date if mentioned, or null",
  "deal_value_usd": investment/deal value in USD as number, or null
}

Conversion rules:
- 1 TPD H2 = 0.000365 MTPA H2
- 1 MTPA NH3 (ammonia) = 0.178 MTPA H2 equivalent
- 1 GW electrolyzer = 0.16 MTPA H2 (at 50% capacity factor)
- "Autothermal reforming" or "ATR" = ATR technology
- "Steam methane reforming" or "SMR" = SMR technology

Article to extract from:
"""


def _parse_any_date(date_str: str) -> Optional[datetime]:
    """Parse a date string in any common format (RFC 2822, ISO, etc.) to datetime."""
    if not date_str or not date_str.strip():
        return None
    # ISO first
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00').split('+')[0])
    except (ValueError, AttributeError):
        pass
    # RFC 2822
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str).replace(tzinfo=None)
    except Exception:
        pass
    # dateutil fallback
    try:
        from dateutil import parser as dp
        return dp.parse(date_str, fuzzy=True).replace(tzinfo=None)
    except Exception:
        return None

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# ---------------------------------------------------------------------------
# GOOGLE NEWS URL RESOLVER (Gap 7)
# ---------------------------------------------------------------------------
_URL_CACHE: Dict[str, str] = {}


def _decode_google_news_url(url: str) -> Optional[str]:
    """Extract real URL from Google News base64 payload without HTTP request.

    Google News RSS URLs have format: .../articles/CBMi<base64>...
    The payload contains the actual article URL encoded in base64url.
    """
    match = re.search(r'/articles/(CBMi[A-Za-z0-9_-]+)', url)
    if not match:
        return None
    try:
        encoded = match.group(1)
        # Add padding if needed
        padding = 4 - len(encoded) % 4
        if padding != 4:
            encoded += '=' * padding
        decoded = base64.urlsafe_b64decode(encoded)
        # Extract URL from decoded bytes
        urls = re.findall(rb'https?://[^\x00-\x1f\x7f-\xff"<>\s]+', decoded)
        if urls:
            return urls[0].decode('utf-8', errors='ignore').rstrip('/')
    except Exception:
        pass
    return None


def _resolve_google_news_url(url: str, timeout: int = 8) -> str:
    """Resolve Google News redirect URL. Try decode first, then HTTP fallback.

    Caches results to avoid repeated resolution.
    """
    if url in _URL_CACHE:
        return _URL_CACHE[url]

    if 'news.google.com' not in url:
        return url

    # Try base64 decode first (no HTTP request needed)
    decoded = _decode_google_news_url(url)
    if decoded:
        _URL_CACHE[url] = decoded
        return decoded

    # HTTP fallback (HEAD request)
    try:
        resp = requests.head(url, headers=_HEADERS, timeout=timeout,
                             allow_redirects=True)
        if resp.url and 'news.google.com' not in resp.url:
            _URL_CACHE[url] = resp.url
            return resp.url
    except Exception:
        pass

    # GET fallback
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout,
                            allow_redirects=True, stream=True)
        final = resp.url
        resp.close()
        if final and 'news.google.com' not in final:
            _URL_CACHE[url] = final
            return final
    except Exception:
        pass

    _URL_CACHE[url] = url
    return url


# ---------------------------------------------------------------------------
# KEYWORDS — Blue Hydrogen / Ammonia / Gulf Coast
# ---------------------------------------------------------------------------
KEYWORDS: Dict[str, List[str]] = {
    'CRITICAL': [
        'FID', 'final investment decision', 'financial close',
        'blue hydrogen', 'blue ammonia',
        'SMR', 'steam methane reformer', 'ATR', 'autothermal reformer',
        'EPC contract', 'EPC awarded', 'FEED contract', 'front-end engineering',
        'construction start', 'groundbreaking', 'notice to proceed', 'NTP',
        'commercial operation date', 'COD', 'first production',
        'CCS', 'carbon capture', 'CCUS', '45Q', '45V',
        'offtake agreement', 'binding contract', 'signed contract',
    ],
    'HIGH': [
        'Gulf Coast', 'Texas', 'Louisiana', 'Port Arthur', 'Freeport',
        'Baytown', 'Lake Charles', 'Beaumont', 'Corpus Christi',
        'Appalachia', 'Ohio Valley',
        'Air Products', 'ExxonMobil', 'Chevron', 'CF Industries', 'Linde',
        'Shell', 'BP', 'Equinor', 'Ineos', 'OCI',
        'Worley', 'Technip Energies', 'KBR', 'McDermott', 'Bechtel', 'Fluor', 'Wood',
        'hydrogen hub', 'H2Hub', 'DOE hydrogen',
        'ammonia plant', 'ammonia capacity', 'MTPA', 'TPD',
        'reformer', 'syngas', 'hydrogen pipeline',
    ],
    'MEDIUM': [
        'hydrogen project', 'hydrogen capacity', 'hydrogen production',
        'ammonia project', 'ammonia production', 'ammonia export',
        'natural gas feedstock', 'gas supply agreement',
        'carbon sequestration', 'CO2 storage', 'saline aquifer',
        'IRA', 'Inflation Reduction Act', 'DOE loan', 'OCED',
        'methanol', 'sustainable aviation fuel', 'SAF',
        'electrolyzer', 'green hydrogen',
    ],
    'LOW': [
        'hydrogen economy', 'hydrogen strategy', 'decarbonization',
        'energy transition', 'net zero', 'low carbon',
        'clean energy', 'climate policy',
    ],
}

# ---------------------------------------------------------------------------
# SEARCH QUERIES — expanded (Gap 9)
# ---------------------------------------------------------------------------
SEARCH_QUERIES = [
    # Broad queries
    'blue hydrogen SMR ATR projects FID 2025 2026 United States',
    'Gulf Coast Louisiana Texas hydrogen ammonia project announced',
    'EPC contract hydrogen ammonia Worley Technip KBR awarded',
    'blue hydrogen project capacity MTPA announced',
    'blue hydrogen CCS project Gulf Coast',
    '45Q tax credit hydrogen ammonia',
    'hydrogen hub DOE OCED award',
    'ammonia export terminal United States',
    'blue ammonia Japan Korea offtake',
    'SMR reformer project construction 2025 2026',
    'Air Products blue hydrogen',
    'ExxonMobil hydrogen Baytown',
    'natural gas hydrogen feedstock supply',
    # Project-specific (backfill)
    'Air Products Louisiana clean hydrogen complex',
    'CF Industries blue ammonia Donaldsonville',
    'Linde clean hydrogen project US',
    'Lake Charles methanol hydrogen project',
    'Chevron Bayou Bend CCS hydrogen',
    'OCI clean ammonia Beaumont Texas',
    # EPC-specific
    'Worley hydrogen ammonia FEED EPC 2025 2026',
    'KBR ammonia technology license hydrogen',
    'Technip Energies blue hydrogen ATR',
    'Bechtel hydrogen ammonia Gulf Coast',
    # Policy-specific
    'IRA hydrogen 45V clean fuel production tax credit',
    'DOE hydrogen hub funding update 2025 2026',
    '45Q carbon capture storage hydrogen project',
    'FERC hydrogen pipeline interstate',
]

# ---------------------------------------------------------------------------
# REGION DETECTION — comprehensive US + International
# ---------------------------------------------------------------------------
_REGION_RULES: List[Tuple[str, List[str], Optional[str]]] = [
    # (region_name, keywords_to_match, subregion_if_matched)
    # --- Gulf Coast TX (city-level first, then state) ---
    ('Gulf Coast TX', ['port arthur'], 'Port Arthur'),
    ('Gulf Coast TX', ['freeport'], 'Freeport'),
    ('Gulf Coast TX', ['baytown'], 'Baytown'),
    ('Gulf Coast TX', ['beaumont'], 'Beaumont'),
    ('Gulf Coast TX', ['corpus christi'], 'Corpus Christi'),
    ('Gulf Coast TX', ['houston'], 'Houston'),
    ('Gulf Coast TX', ['galveston'], 'Galveston'),
    ('Gulf Coast TX', ['texas city'], 'Texas City'),
    ('Gulf Coast TX', ['deer park'], 'Deer Park'),
    ('Gulf Coast TX', ['la porte'], 'La Porte'),
    ('Gulf Coast TX', ['texas', ' tx '], None),
    # --- Gulf Coast LA ---
    ('Gulf Coast LA', ['lake charles'], 'Lake Charles'),
    ('Gulf Coast LA', ['baton rouge'], 'Baton Rouge'),
    ('Gulf Coast LA', ['new orleans'], 'New Orleans'),
    ('Gulf Coast LA', ['donaldsonville'], 'Donaldsonville'),
    ('Gulf Coast LA', ['geismar'], 'Geismar'),
    ('Gulf Coast LA', ['louisiana', ' la '], None),
    # --- Appalachia ---
    ('Appalachia', ['appalachia', 'ohio valley'], None),
    ('Appalachia', ['west virginia'], None),
    ('Appalachia', ['pennsylvania'], None),
    ('Appalachia', ['kentucky'], None),
    ('Appalachia', ['pittsburgh'], 'Pittsburgh'),
    # --- Midwest ---
    ('Midwest', ['midwest'], None),
    ('Midwest', ['nebraska'], None),
    ('Midwest', ['iowa'], None),
    ('Midwest', ['illinois'], None),
    ('Midwest', ['indiana'], None),
    ('Midwest', ['michigan'], None),
    ('Midwest', ['minnesota'], None),
    ('Midwest', ['missouri'], None),
    ('Midwest', ['wisconsin'], None),
    ('Midwest', ['kansas'], None),
    ('Midwest', ['north dakota'], None),
    ('Midwest', ['south dakota'], None),
    ('Midwest', ['chicago'], 'Chicago'),
    ('Midwest', ['detroit'], 'Detroit'),
    ('Midwest', ['decatur'], 'Decatur'),
    ('Midwest', ['omaha'], 'Omaha'),
    ('Midwest', ['des moines'], 'Des Moines'),
    # --- West Coast ---
    ('West Coast', ['california'], None),
    ('West Coast', ['los angeles'], 'Los Angeles'),
    ('West Coast', ['martinez'], 'Martinez'),
    # --- Pacific Northwest ---
    ('Pacific Northwest', ['washington state'], None),
    ('Pacific Northwest', ['oregon'], None),
    ('Pacific Northwest', ['seattle'], 'Seattle'),
    ('Pacific Northwest', ['portland'], 'Portland'),
    # --- Mountain West ---
    ('Mountain West', ['colorado'], None),
    ('Mountain West', ['utah'], None),
    ('Mountain West', ['wyoming'], None),
    ('Mountain West', ['montana'], None),
    ('Mountain West', ['idaho'], None),
    ('Mountain West', ['nevada'], None),
    ('Mountain West', ['new mexico'], None),
    ('Mountain West', ['arizona'], None),
    ('Mountain West', ['salt lake city'], 'Salt Lake City'),
    ('Mountain West', ['denver'], 'Denver'),
    # --- Southeast ---
    ('Southeast', ['alabama'], None),
    ('Southeast', ['arkansas'], None),
    ('Southeast', ['florida'], None),
    ('Southeast', ['georgia'], None),
    ('Southeast', ['mississippi'], None),
    ('Southeast', ['north carolina'], None),
    ('Southeast', ['south carolina'], None),
    ('Southeast', ['tennessee'], None),
    ('Southeast', ['virginia'], None),
    ('Southeast', ['savannah'], 'Savannah'),
    ('Southeast', ['jacksonville'], 'Jacksonville'),
    # --- Northeast ---
    ('Northeast', ['connecticut'], None),
    ('Northeast', ['delaware'], None),
    ('Northeast', ['maryland'], None),
    ('Northeast', ['massachusetts'], None),
    ('Northeast', ['new hampshire'], None),
    ('Northeast', ['new jersey'], None),
    ('Northeast', ['new york'], None),
    ('Northeast', ['rhode island'], None),
    ('Northeast', ['vermont'], None),
    ('Northeast', ['philadelphia'], 'Philadelphia'),
    # --- Plains ---
    ('Plains', ['oklahoma'], None),
    # --- Ohio (Appalachia, not Midwest for hydrogen context) ---
    ('Appalachia', ['ohio'], None),
    # --- Generic US (includes federal agency and regulatory mentions) ---
    ('US Other', ['united states', 'u.s.', 'usa', 'american',
                  'department of energy', 'epa ', 'eia forecast', 'eia report',
                  'eia raise', 'eia expects', 'eia forecasts',
                  'ferc ', 'cisa ', 'doe doubles', 'doe award',
                  'carbon pipeline', 'coal ash', 'lower 48',
                  'henry hub', 'natural gas spot price',
                  'wholesale day-ahead electricity',
                  'u.s. retail gasoline', 'u.s. crude oil',
                  'u.s. electricity demand', 'u.s. electric power',
                  'u.s. benchmark', 'u.s. energy information',
                  'u.s. nuclear', 'u.s. wholesale'], None),
    # NOTE: International/country detection is handled by _COUNTRY_KEYWORDS
    # below, which returns the specific country name instead of generic
    # "International". The cascade in detect_region() is:
    # _REGION_RULES (US states) → _COMPANY_REGION_MAP → _COUNTRY_KEYWORDS
    # → URL TLD → "Unknown"

    # --- Additional US regional keywords ---
    ('Northeast', ['new england'], None),
    ('Southeast', ['tennessee valley authority'], None),
]

# ---------------------------------------------------------------------------
# COMPANY → PRIMARY REGION MAP (fallback when title/snippet lack state names)
# ---------------------------------------------------------------------------
# Maps known company names to their primary US hydrogen/energy region.
# Used as a fallback step in detect_region() when _REGION_RULES doesn't match
# a specific state/city. This resolves SEC filings and news articles that only
# mention the company name without geographic context.
_COMPANY_REGION_MAP: List[Tuple[List[str], str, Optional[str]]] = [
    # (company_name_keywords, region, subregion)
    # --- SEC tracked companies (primary H2/ammonia project locations) ---
    (['air products'], 'Gulf Coast LA', None),
    (['cf industries'], 'Gulf Coast LA', 'Donaldsonville'),
    (['exxonmobil', 'exxon mobil'], 'Gulf Coast TX', 'Baytown'),
    (['chevron'], 'West Coast', None),
    (['dow chemical', 'dow inc'], 'Gulf Coast TX', 'Freeport'),
    (['shell plc', 'shell usa'], 'Gulf Coast TX', None),
    (['bp ', 'bp,', "bp's"], 'Midwest', 'Whiting'),
    (['linde plc', 'linde '], 'Northeast', None),
    # --- US utilities (from news articles) ---
    (['duke energy'], 'Southeast', None),
    (['plug power'], 'Northeast', None),
    (['tva ', 'tva,', "tva's"], 'Southeast', None),
    (['exelon'], 'Midwest', 'Chicago'),
    (['xcel energy'], 'Midwest', None),
    (['southern company'], 'Southeast', None),
    (['sempra'], 'Gulf Coast TX', None),
    (['cheniere'], 'Gulf Coast TX', None),
    (['freeport lng'], 'Gulf Coast TX', 'Freeport'),
    (['marathon petroleum'], 'Midwest', None),
    (['valero'], 'Gulf Coast TX', None),
    # --- Other known H2/ammonia companies with US projects ---
    (['denbury'], 'Gulf Coast TX', None),
    (['sasol'], 'Gulf Coast LA', 'Lake Charles'),
    (['next decade', 'nextdecade'], 'Gulf Coast TX', None),
    # --- International companies (when text has no other geo signal) ---
    (['thyssenkrupp'], 'Germany', None),
    (['mitsubishi heavy', ' mhi '], 'Japan', None),
    (['cell impact'], 'Sweden', None),
    (['smoltek'], 'Sweden', None),
    (['metacon'], 'Sweden', None),
    (['rolls-royce'], 'United Kingdom', None),
    (['hysa infra'], 'South Africa', None),
]


# Country → display label mapping for non-US articles
_COUNTRY_KEYWORDS: List[Tuple[str, List[str]]] = [
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

# URL domain TLDs that indicate country
_TLD_COUNTRY = {
    '.ca': 'Canada', '.uk': 'United Kingdom', '.co.uk': 'United Kingdom',
    '.de': 'Germany', '.fr': 'France', '.nl': 'Netherlands',
    '.es': 'Spain', '.it': 'Italy', '.no': 'Norway', '.se': 'Sweden',
    '.dk': 'Denmark', '.fi': 'Finland', '.jp': 'Japan', '.kr': 'South Korea',
    '.cn': 'China', '.in': 'India', '.au': 'Australia', '.sa': 'Saudi Arabia',
    '.ae': 'UAE', '.br': 'Brazil', '.cl': 'Chile', '.mx': 'Mexico',
    '.sg': 'Singapore', '.my': 'Malaysia', '.id': 'Indonesia',
    '.nz': 'New Zealand', '.za': 'South Africa', '.ee': 'Estonia',
    '.si': 'Slovenia', '.be': 'Belgium', '.at': 'Austria', '.ch': 'Switzerland',
    '.pl': 'Poland',
}


def detect_region(text: str, url: str = '') -> Tuple[str, Optional[str]]:
    """Detect region and sub-region from text.

    Returns (region, subregion). Cascade:
    1. US state/city match → US region
    2. Known company name → company's primary region
    3. Country keyword match → country name as region
    4. URL TLD hint → country name as region
    5. 'Unknown' fallback
    """
    text_lower = f' {text.lower()} '

    # 1. US state/city/region keywords
    for region, keywords, subregion in _REGION_RULES:
        for kw in keywords:
            if kw in text_lower:
                return region, subregion

    # 2. Known company name → primary region (SEC companies, utilities, etc.)
    for company_kws, mapped_region, mapped_sub in _COMPANY_REGION_MAP:
        for kw in company_kws:
            if kw in text_lower:
                return mapped_region, mapped_sub

    # 3. Country keyword detection
    for country, keywords in _COUNTRY_KEYWORDS:
        for kw in keywords:
            if kw in text_lower:
                return country, None

    # 4. URL TLD hint (e.g., .de → Germany, .jp → Japan)
    if url:
        url_lower = url.lower()
        for tld, country in _TLD_COUNTRY.items():
            # Check domain ends with TLD before path
            # e.g., "https://example.co.uk/article" → .co.uk
            domain_part = url_lower.split('://')[1].split('/')[0] if '://' in url_lower else ''
            if domain_part.endswith(tld):
                return country, None

    return 'Unknown', None


# ---------------------------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------------------------

def setup_database(db_path: str = DATABASE_NAME):
    """Create tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        url TEXT UNIQUE NOT NULL,
        url_hash TEXT,
        content_hash TEXT,
        source TEXT,
        snippet TEXT,
        full_text TEXT,
        category TEXT,
        priority_score INTEGER,
        published_date TEXT,
        fetched_date TEXT,
        alerted BOOLEAN DEFAULT FALSE,
        keywords TEXT,
        region TEXT,
        subregion TEXT,
        investigated BOOLEAN DEFAULT FALSE,
        paywall_blocked BOOLEAN DEFAULT FALSE
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS competitors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT NOT NULL,
        company TEXT, region TEXT, capacity_mw REAL,
        status TEXT, fid_date TEXT, cod_date TEXT,
        offtake_status TEXT, land_status TEXT,
        last_updated TEXT, source_article_id INTEGER,
        notes TEXT,
        FOREIGN KEY (source_article_id) REFERENCES articles(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS offtakers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL, type TEXT,
        region TEXT, total_demand_kt REAL,
        secured_kt REAL, available_kt REAL,
        last_activity TEXT, last_updated TEXT,
        source_article_id INTEGER, notes TEXT,
        FOREIGN KEY (source_article_id) REFERENCES articles(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS risks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        risk_category TEXT NOT NULL, risk_description TEXT,
        status TEXT, trend TEXT, impact_level TEXT,
        probability TEXT, last_updated TEXT,
        mitigation_actions TEXT, source_article_id INTEGER,
        FOREIGN KEY (source_article_id) REFERENCES articles(id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS deals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        deal_date TEXT, deal_type TEXT, parties TEXT,
        value_usd REAL, region TEXT, description TEXT,
        signal TEXT, source_article_id INTEGER,
        FOREIGN KEY (source_article_id) REFERENCES articles(id)
    )''')

    # Persistent project table (populated by parser, not collector)
    c.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_name TEXT NOT NULL,
        developer TEXT,
        capacity_mtpa_h2 REAL,
        capacity_raw TEXT,
        technology TEXT,
        product TEXT,
        location_state TEXT,
        location_subregion TEXT,
        region TEXT,
        status TEXT,
        fid_date TEXT,
        cod_date TEXT,
        epc_contractor TEXT,
        fid_probability REAL,
        confidence REAL,
        last_updated TEXT,
        source_article_ids TEXT,
        UNIQUE(project_name, developer, region, technology)
    )''')

    # Source performance tracking
    c.execute('''CREATE TABLE IF NOT EXISTS source_stats (
        source_name TEXT PRIMARY KEY,
        total_articles INTEGER DEFAULT 0,
        high_priority_articles INTEGER DEFAULT 0,
        avg_score REAL DEFAULT 0,
        last_fetched TEXT,
        fetch_frequency TEXT DEFAULT 'on_demand'
    )''')

    # Migrate existing DB: add new columns if missing (must run before index creation)
    try:
        c.execute('ALTER TABLE articles ADD COLUMN content_hash TEXT')
    except sqlite3.OperationalError:
        pass  # column already exists
    try:
        c.execute('ALTER TABLE articles ADD COLUMN paywall_blocked BOOLEAN DEFAULT FALSE')
    except sqlite3.OperationalError:
        pass

    # LLM extraction columns (v3)
    llm_columns = [
        ('llm_extracted', 'BOOLEAN DEFAULT FALSE'),
        ('llm_project_name', 'TEXT'),
        ('llm_developer', 'TEXT'),
        ('llm_co_developers', 'TEXT'),
        ('llm_status', 'TEXT'),
        ('llm_status_confirmed', 'BOOLEAN'),
        ('llm_capacity_raw', 'TEXT'),
        ('llm_capacity_mtpa_h2', 'REAL'),
        ('llm_technology', 'TEXT'),
        ('llm_product', 'TEXT'),
        ('llm_epc_contractor', 'TEXT'),
        ('llm_location_state', 'TEXT'),
        ('llm_location_subregion', 'TEXT'),
        ('llm_region', 'TEXT'),
        ('llm_fid_date', 'TEXT'),
        ('llm_cod_date', 'TEXT'),
        ('llm_deal_value_usd', 'REAL'),
        ('market_impact', 'TEXT'),
    ]
    for col_name, col_type in llm_columns:
        try:
            c.execute(f'ALTER TABLE articles ADD COLUMN {col_name} {col_type}')
        except sqlite3.OperationalError:
            pass

    c.execute('CREATE INDEX IF NOT EXISTS idx_url_hash ON articles(url_hash)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_content_hash ON articles(content_hash)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_category ON articles(category)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_published_date ON articles(published_date)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_region ON articles(region)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_llm_extracted ON articles(llm_extracted)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_projects_region ON projects(region)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)')

    # ------------------------------------------------------------------
    # REGULATORY EVIDENCE (FID Probability Engine)
    # ------------------------------------------------------------------
    c.execute('''CREATE TABLE IF NOT EXISTS regulatory_evidence (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        company_cik TEXT,
        source TEXT NOT NULL,
        document_type TEXT,
        document_id TEXT,
        document_url TEXT,
        document_date TEXT,
        fetched_date TEXT NOT NULL,
        raw_text_excerpt TEXT,
        full_text_hash TEXT,
        excerpt_char_count INTEGER,
        content_hash TEXT,
        state TEXT,
        stale BOOLEAN DEFAULT FALSE,
        stale_reason TEXT,
        UNIQUE(company_name, source, document_id)
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS fid_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        project_name TEXT DEFAULT 'Primary Hydrogen Project',
        assessed_date TEXT NOT NULL,
        evidence_ids TEXT,
        evidence_summary TEXT,
        llm_backend TEXT,
        llm_raw_response TEXT,
        stage TEXT,
        fid_probability REAL,
        confidence TEXT,
        key_evidence_points TEXT,
        reasoning TEXT,
        stale BOOLEAN DEFAULT FALSE,
        stale_reason TEXT,
        prompt_version INTEGER DEFAULT 1,
        llm_tokens_used INTEGER DEFAULT 0,
        UNIQUE(company_name, project_name)
    )''')

    c.execute('CREATE INDEX IF NOT EXISTS idx_reg_evidence_company ON regulatory_evidence(company_name)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_reg_evidence_source ON regulatory_evidence(source)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_reg_evidence_hash ON regulatory_evidence(content_hash)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_fid_assess_company ON fid_assessments(company_name)')

    # Migrate: add discovery_method column to regulatory_evidence
    try:
        c.execute('ALTER TABLE regulatory_evidence ADD COLUMN discovery_method TEXT')
    except sqlite3.OperationalError:
        pass
    c.execute('CREATE INDEX IF NOT EXISTS idx_reg_evidence_discovery ON regulatory_evidence(discovery_method)')

    # Migrate: add stage/timeline/project columns to regulatory_evidence
    for col, coltype in [
        ('stage', 'TEXT'),
        ('stage_confidence', 'TEXT'),
        ('stage_reasoning', 'TEXT'),
        ('fid_date', 'TEXT'),
        ('construction_start', 'TEXT'),
        ('operations_start', 'TEXT'),
        ('milestones', 'TEXT'),        # JSON
        ('project_name', 'TEXT'),
    ]:
        try:
            c.execute(f'ALTER TABLE regulatory_evidence ADD COLUMN {col} {coltype}')
        except sqlite3.OperationalError:
            pass

    c.execute('CREATE INDEX IF NOT EXISTS idx_reg_evidence_doc_id ON regulatory_evidence(document_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_reg_evidence_stage ON regulatory_evidence(stage)')

    # ------------------------------------------------------------------
    # COLLECTION RUNS (EFTS filing scan tracking)
    # ------------------------------------------------------------------
    c.execute('''CREATE TABLE IF NOT EXISTS collection_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_type TEXT NOT NULL,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        lookback_days INTEGER,
        queries_executed INTEGER DEFAULT 0,
        filings_found INTEGER DEFAULT 0,
        filings_relevant INTEGER DEFAULT 0,
        filings_new INTEGER DEFAULT 0,
        new_companies_discovered INTEGER DEFAULT 0,
        duration_seconds REAL,
        strategy_stats TEXT,
        error TEXT
    )''')

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# SITES.YAML LOADER
# ---------------------------------------------------------------------------

def load_sites(yaml_path: str = SITES_YAML, tags: Set[str] = None) -> List[dict]:
    """Load sites from sites.yaml, optionally filtering by tags."""
    if not Path(yaml_path).exists():
        print(f"  Warning: {yaml_path} not found, using built-in search queries only")
        return []

    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    sites = data.get('sites', [])
    if tags is None:
        return sites

    # Filter to sites that have at least one matching tag
    filtered = []
    for site in sites:
        site_tags = set(site.get('tags', []))
        if site_tags & tags:
            filtered.append(site)
    return filtered


# ---------------------------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------------------------

def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode entities from a string (e.g., RSS snippets)."""
    if not text or '<' not in text:
        return text
    soup = BeautifulSoup(text, 'html.parser')
    clean = soup.get_text(separator=' ', strip=True)
    # Collapse multiple spaces
    return re.sub(r'\s{2,}', ' ', clean).strip()


def _extract_company_names(text: str) -> List[str]:
    """Extract known company names from text."""
    companies = []
    text_lower = text.lower()
    known = [
        'air products', 'exxonmobil', 'exxon mobil', 'chevron', 'cf industries',
        'linde', 'shell', 'bp', 'equinor', 'ineos', 'oci', 'sabic',
        'totalenergies', 'engie', 'repsol', 'eni',
        'worley', 'technip energies', 'technip', 'kbr', 'mcdermott',
        'bechtel', 'fluor', 'wood', 'saipem',
        'plug power', 'bloom energy', 'fortescue',
        'air liquide', 'yara', 'thyssenkrupp', 'samsung engineering',
        'dow', 'basf', 'mitsubishi', 'mitsui', 'jera', 'ihi',
    ]
    for company in known:
        if company in text_lower:
            companies.append(company)
    return companies


def _extract_capacity_text(text: str) -> Optional[str]:
    """Extract raw capacity mention (e.g., '1.2 MTPA', '500 MW')."""
    patterns = [
        r'(\d+\.?\d*)\s*(mtpa|MTPA)',
        r'(\d+\.?\d*)\s*(tpd|TPD)',
        r'(\d+\.?\d*)\s*(kt|KT)',
        r'(\d+\.?\d*)\s*(mw|MW|megawatt)',
        r'(\d+\.?\d*)\s*(gw|GW|gigawatt)',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


# ---------------------------------------------------------------------------
# URL NORMALIZATION + CONTENT HASHING (cross-source dedup)
# ---------------------------------------------------------------------------

# Tracking params to strip during URL normalization
_TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'ref', 'source', 'fbclid', 'gclid', 'mc_cid', 'mc_eid', 'ns_campaign',
}


def _normalize_url(url: str) -> str:
    """Normalize URL for deduplication — strip tracking params, lowercase domain."""
    if not url:
        return url
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    # Remove tracking parameters
    query_params = parse_qs(parsed.query)
    clean_params = {k: v for k, v in query_params.items()
                    if k.lower() not in _TRACKING_PARAMS}
    clean_query = urlencode(clean_params, doseq=True)
    path = parsed.path.rstrip('/')
    return f"{scheme}://{netloc}{path}{'?' + clean_query if clean_query else ''}"


def _content_hash(title: str, snippet: str = '') -> str:
    """Generate content hash for deduplication across sources.

    Catches same article syndicated across multiple outlets.
    """
    text = f"{title or ''} {snippet or ''}".lower()
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return hashlib.md5(text[:200].encode()).hexdigest()


# ---------------------------------------------------------------------------
# SCORING / CATEGORIZATION
# ---------------------------------------------------------------------------

def categorize_article(title: str, snippet: str = '',
                       url: str = '', full_text: str = '',
                       ) -> Tuple[str, int, List[str], str, Optional[str]]:
    """Score and categorize an article. Returns (category, score, keywords, region, subregion)."""
    text_lower = f"{title} {snippet}".lower()
    found_keywords: List[str] = []
    total_score = 0
    category = 'LOW'

    weight_map = {'CRITICAL': 10, 'HIGH': 5, 'MEDIUM': 2, 'LOW': 1}
    priority_order = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']

    for level in priority_order:
        for kw in KEYWORDS[level]:
            if kw.lower() in text_lower:
                found_keywords.append(kw)
                total_score += weight_map[level]
                if priority_order.index(level) < priority_order.index(category):
                    category = level

    # Try title+snippet first, then include full_text for deeper search
    region, subregion = detect_region(f"{title} {snippet}", url=url)
    if region == 'Unknown' and full_text:
        region, subregion = detect_region(
            f"{title} {snippet} {full_text}", url=url)
    return category, total_score, list(set(found_keywords)), region, subregion


# ---------------------------------------------------------------------------
# DEDUPLICATION (enhanced with URL normalization + content hashing)
# ---------------------------------------------------------------------------

def _load_existing_url_hashes(db_path: str = DATABASE_NAME) -> Set[str]:
    """Load all url_hash values already stored in the articles table."""
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute('SELECT url_hash FROM articles WHERE url_hash IS NOT NULL').fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _load_existing_content_hashes(db_path: str = DATABASE_NAME) -> Set[str]:
    """Load all content_hash values already stored in the articles table."""
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute('SELECT content_hash FROM articles WHERE content_hash IS NOT NULL').fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _deduplicate(articles: List[dict],
                 existing_hashes: Optional[Set[str]] = None,
                 existing_content_hashes: Optional[Set[str]] = None) -> List[dict]:
    """Remove duplicates by URL hash, normalized URL, and content hash.

    If *existing_hashes* is provided, also skips articles whose URL hash
    is already in the database (avoids re-scoring and re-fetching).
    """
    seen_urls: Set[str] = set()
    if existing_hashes:
        seen_urls.update(existing_hashes)
    seen_normalized: Set[str] = set()
    seen_content: Set[str] = set()
    if existing_content_hashes:
        seen_content.update(existing_content_hashes)
    seen_sigs: Set[str] = set()
    unique = []
    removed = 0
    skipped_existing = 0

    event_words = ['cancel', 'fid', 'final investment', 'exit', 'withdraw',
                   'delay', 'postpone', 'sign', 'agree', 'announce',
                   'epc', 'feed', 'construction', 'operational']

    for art in articles:
        url = art.get('url', '')
        if not url:
            continue

        # URL hash check
        h = _url_hash(url)
        if h in seen_urls:
            if existing_hashes and h in existing_hashes:
                skipped_existing += 1
            else:
                removed += 1
            continue

        # Normalized URL check
        norm = _normalize_url(url)
        if norm in seen_normalized:
            removed += 1
            continue

        # Content hash check (catches syndicated articles)
        title = art.get('title', '')
        snippet = art.get('snippet', '')
        ch = _content_hash(title, snippet)
        if ch in seen_content:
            removed += 1
            continue

        # Legacy signature check
        title_lower = title.lower()
        companies = _extract_company_names(title_lower)
        cap = _extract_capacity_text(title_lower)
        keys = sorted(set(w for w in event_words if w in title_lower))
        published = art.get('published', '')
        date_sig = published[:7] if published else ''

        if companies and keys:
            sig = f"{'-'.join(sorted(companies))}_{'-'.join(keys)}_{date_sig}"
            if cap:
                sig += f"_{cap}"
            if sig in seen_sigs:
                removed += 1
                continue
            seen_sigs.add(sig)

        seen_urls.add(h)
        seen_normalized.add(norm)
        seen_content.add(ch)
        art['url_hash'] = h
        art['content_hash'] = ch
        unique.append(art)

    if skipped_existing:
        print(f"  Skipped {skipped_existing} articles already in database")
    if removed:
        print(f"  Removed {removed} duplicate articles (within batch)")
    return unique


# ---------------------------------------------------------------------------
# SEARCH ENGINES
# ---------------------------------------------------------------------------

def _resolve_ddg_url(raw_url: str) -> str:
    """Extract the actual destination URL from a DuckDuckGo redirect link."""
    if not raw_url:
        return raw_url
    if raw_url.startswith('//'):
        raw_url = 'https:' + raw_url
    parsed = urlparse(raw_url)
    if 'duckduckgo.com' in parsed.netloc:
        qs = parse_qs(parsed.query)
        uddg = qs.get('uddg', [None])[0]
        if uddg:
            return unquote(uddg)
    return raw_url


def _search_duckduckgo(query: str, num_pages: int = 5) -> List[dict]:
    results = []
    try:
        for page in range(num_pages):
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            if page > 0:
                url += f"&s={page * 30}"
            resp = requests.get(url, headers=_HEADERS, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')
            divs = soup.find_all('div', class_='result')
            if not divs:
                break
            for div in divs:
                try:
                    a = div.find('a', class_='result__a')
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    link = _resolve_ddg_url(a.get('href', ''))
                    snip_el = div.find('a', class_='result__snippet')
                    snip = snip_el.get_text(strip=True) if snip_el else ''
                    if link and title:
                        results.append({'title': title, 'url': link,
                                        'snippet': snip, 'source': 'duckduckgo'})
                except Exception:
                    continue
            time.sleep(1)
    except Exception as e:
        print(f"    DuckDuckGo error: {e}")
    return results


def _search_google_news_rss(query: str) -> List[dict]:
    """Search Google News via RSS, resolving redirect URLs.

    Uses requests to fetch (bypasses feedparser SSL issues), then parses.
    Gracefully handles Google 503 bot-blocking.
    """
    results = []
    try:
        rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en&gl=US&ceid=US:en"
        try:
            resp = requests.get(rss_url, headers=_HEADERS, timeout=15)
            if resp.status_code != 200:
                return results  # Google blocking — skip silently
            feed = feedparser.parse(resp.text)
        except requests.exceptions.RequestException:
            feed = feedparser.parse(rss_url, request_headers=_HEADERS)

        for entry in feed.entries[:50]:
            raw_snippet = entry.get('summary', '')
            raw_link = entry.get('link', '')
            resolved_link = _resolve_google_news_url(raw_link)
            results.append({
                'title': entry.get('title', ''),
                'url': resolved_link,
                'snippet': _strip_html(raw_snippet),
                'source': 'google_news',
                'published': entry.get('published', ''),
            })
            if resolved_link != raw_link:
                time.sleep(0.3)
    except Exception as e:
        print(f"    Google News RSS error: {e}")
    return results


def deep_web_search(query: str, num_pages: int = 5) -> List[dict]:
    """Search DuckDuckGo + Google News RSS, deduplicate by URL."""
    print(f"  Searching: '{query}' (up to {num_pages} pages)...")
    ddg = _search_duckduckgo(query, num_pages)
    gnews = _search_google_news_rss(query)
    seen: Set[str] = set()
    unique = []
    for r in ddg + gnews:
        u = r.get('url', '')
        if u and u not in seen:
            seen.add(u)
            unique.append(r)
    print(f"    Found {len(unique)} unique results")
    return unique


# ---------------------------------------------------------------------------
# WEB SCRAPERS (enhanced with fallback selectors — Gap 8)
# ---------------------------------------------------------------------------

def _generic_scrape(url: str, source_name: str, max_articles: int = 30) -> List[dict]:
    """Generic article scraper with fallback selectors."""
    results = []
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Try <article> tags first
        articles = soup.find_all('article', limit=max_articles)

        # Fallback: common article class selectors
        if not articles:
            articles = soup.select(
                '.post, .article, .story, .news-item, .entry, .card, .list-item',
            )[:max_articles]

        # Fallback: links within main content area
        if not articles:
            main = soup.select_one('main, [role="main"], .content, #content')
            if main:
                articles = main.find_all('a', href=True, limit=max_articles)

        for art in articles:
            title_el = art.find(['h1', 'h2', 'h3', 'h4'])
            link_el = art.find('a') if art.name != 'a' else art
            if not title_el:
                # For <a> tags used as fallback, title is the link text
                title_el = link_el
            if title_el and link_el:
                title = title_el.get_text(strip=True)
                href = link_el.get('href', '')
                if not href:
                    continue
                link = urljoin(url, href)
                if title and len(title) > 10:  # Skip very short nav links
                    results.append({'title': title, 'url': link, 'snippet': '',
                                    'source': source_name})
        print(f"  Scraped {len(results)} articles from {source_name}")
    except Exception as e:
        print(f"  {source_name} scrape error: {e}")
    return results


_SCRAPER_URLS = {
    'decarbonfuse':       'https://decarbonfuse.com/issues',
    'hydrogen_insight':   'https://www.hydrogeninsight.com',
    'recharge_news':      'https://www.rechargenews.com/energy-transition',
    'ammonia_energy':     'https://ammoniaenergy.org/articles/',
    'energy_storage_news': 'https://www.energy-storage.news',
    'pv_magazine':        'https://www.pv-magazine.com/category/markets-policy/hydrogen/',
}


# ---------------------------------------------------------------------------
# FULL TEXT FETCHING (newspaper3k + paywall detection — Gaps 1 & 3)
# ---------------------------------------------------------------------------

# Domains behind paywalls — skip fetching
PAYWALL_DOMAINS = {
    'reuters.com', 'bloomberg.com', 'wsj.com', 'ft.com',
    'spglobal.com', 'argusmedia.com', 'platts.com', 'woodmac.com', 'icis.com',
}

PAYWALL_INDICATORS = [
    'subscribe to read', 'subscription required', 'premium content',
    'sign in to continue', 'create an account', 'paywall', 'register to read',
    'for subscribers only', 'exclusive content',
]

MAX_BODY_CHARS = 50_000


def _is_paywalled(url: str, text: str) -> bool:
    """Detect if content is paywalled."""
    domain = urlparse(url).netloc.lower()
    if any(pw in domain for pw in PAYWALL_DOMAINS):
        return True
    if text and len(text) < 500:
        if any(ind in text.lower() for ind in PAYWALL_INDICATORS):
            return True
    return False


def _fetch_sec_filing_text(url: str, timeout: int = 15) -> Optional[dict]:
    """Fetch and extract text from an SEC EDGAR filing document.

    SEC filings are often iXBRL-wrapped HTML with SEC-specific structure.
    This function handles that format and also resolves index pages to
    the actual document when needed.
    """
    try:
        resp = requests.get(url, headers=_SEC_HEADERS, timeout=timeout)
        resp.raise_for_status()
        html = resp.text

        # If we landed on the filing index page (directory listing), find
        # the primary document link and follow it
        if 'Directory List' in html or 'Filing Detail' in html:
            soup = BeautifulSoup(html, 'html.parser')
            # Look for the primary document link in the filing table
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                # Primary docs are usually .htm files (not .xml, .xsd, .json)
                if href.endswith(('.htm', '.html')):
                    doc_url = urljoin(url + '/', href)
                    resp = requests.get(doc_url, headers=_SEC_HEADERS, timeout=timeout)
                    resp.raise_for_status()
                    html = resp.text
                    break
            else:
                return None  # Could not find a document link

        # Parse the filing HTML — strip iXBRL tags and SEC chrome
        soup = BeautifulSoup(html, 'html.parser')

        # Remove script, style, and iXBRL processing instruction elements
        for tag in soup.find_all(['script', 'style', 'noscript',
                                   'ix:header', 'ix:references']):
            tag.decompose()

        # iXBRL inline tags (ix:nonNumeric, ix:nonFraction, etc.) —
        # unwrap them to keep the visible text content
        for ix_tag in soup.find_all(re.compile(r'^ix:')):
            ix_tag.unwrap()

        # Try to find the filing body content
        body = None
        # SEC filings often use <body> directly or a main div
        for selector in ['[role="main"]', '.filing-content', 'body']:
            el = soup.select_one(selector)
            if el:
                body = el.get_text(separator='\n', strip=True)
                break
        if not body:
            body = soup.get_text(separator='\n', strip=True)

        # Clean up: collapse whitespace but preserve paragraph breaks
        body = re.sub(r'[ \t]+', ' ', body)
        body = re.sub(r'\n{3,}', '\n\n', body)
        body = body.strip()

        if len(body) < 50:
            return None

        return {'text': body[:MAX_BODY_CHARS], 'paywalled': False}

    except Exception as e:
        logger.debug(f"SEC filing fetch error for {url}: {e}")
        return None


def _fetch_full_text(url: str, timeout: int = 10) -> Optional[dict]:
    """Fetch and extract article body text using newspaper3k, with BS4 fallback.

    Returns dict with 'text', 'paywalled' keys, or None on failure.
    Rate-limited externally (caller sleeps between calls).
    """
    try:
        domain = urlparse(url).netloc.lower()
        if any(pw in domain for pw in PAYWALL_DOMAINS):
            return {'text': None, 'paywalled': True}

        # SEC EDGAR filings get dedicated handling
        if 'sec.gov' in domain:
            return _fetch_sec_filing_text(url, timeout=15)

        # Try newspaper3k first (more robust extraction)
        if _NEWSPAPER3K_AVAILABLE:
            try:
                article = _Newspaper3kArticle(url)
                article.download()
                article.parse()
                text = article.text

                if _is_paywalled(url, text or ''):
                    return {'text': None, 'paywalled': True}

                if text and len(text) >= 100:
                    return {'text': text[:MAX_BODY_CHARS], 'paywalled': False}
            except Exception:
                pass  # Fall through to BS4 fallback

        # BeautifulSoup fallback
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        resp.raise_for_status()

        ctype = resp.headers.get('Content-Type', '')
        if 'html' not in ctype.lower() and 'text' not in ctype.lower():
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'header',
                                   'aside', 'iframe', 'noscript']):
            tag.decompose()

        body = None
        for selector in ['article', '[role="main"]', '.article-body',
                         '.post-content', '.entry-content', '.story-body', 'main']:
            el = soup.select_one(selector)
            if el:
                body = el.get_text(separator=' ', strip=True)
                break
        if not body:
            body = soup.get_text(separator=' ', strip=True)

        body = re.sub(r'\s+', ' ', body).strip()

        if _is_paywalled(url, body):
            return {'text': None, 'paywalled': True}

        if len(body) < 50:
            return None

        return {'text': body[:MAX_BODY_CHARS], 'paywalled': False}

    except Exception:
        return None


def _fetch_full_texts(articles: List[dict], min_score: int = 5) -> int:
    """Fetch full text for high-priority articles. Returns count fetched.

    Only fetches for articles with priority_score >= min_score.
    Skips articles that already have full_text.
    Rate-limited: 1.0 second delay between fetches.
    """
    targets = [a for a in articles
               if a.get('priority_score', 0) >= min_score
               and not a.get('full_text')]
    if not targets:
        return 0

    fetched = 0
    for art in targets:
        url = art.get('url', '')
        if not url:
            continue

        result = _fetch_full_text(url)
        if result:
            if result.get('paywalled'):
                art['paywall_blocked'] = True
            elif result.get('text'):
                art['full_text'] = result['text']
                fetched += 1

        time.sleep(1.0)  # Rate limit

    return fetched


# ---------------------------------------------------------------------------
# SEC EDGAR MONITORING (free standard practice)
# ---------------------------------------------------------------------------

SEC_COMPANY_CIKS = {
    'Air Products': '0000002969',
    'CF Industries': '0001324404',
    'Linde': '0001707925',
    'ExxonMobil': '0000034088',
    'Chevron': '0000093410',
    'Dow': '0000029915',
    'Shell': '0001306965',
    'BP': '0000313807',
}

SEC_KEYWORDS = ['hydrogen', 'ammonia', 'carbon capture', 'CCS', 'blue', 'low-carbon',
                'FID', 'final investment', 'EPC', 'project', 'reformer', 'SMR', 'ATR']

_SEC_HEADERS = {'User-Agent': 'BlueH2Monitor research@decarbiq.com'}


_SEC_FORM_TYPES = {'8-K', '8-K/A', '10-K', '10-K/A', '10-Q', '10-Q/A'}

_SEC_FORM_LABELS = {
    '8-K': 'Material Event',
    '8-K/A': 'Material Event (Amended)',
    '10-K': 'Annual Report',
    '10-K/A': 'Annual Report (Amended)',
    '10-Q': 'Quarterly Report',
    '10-Q/A': 'Quarterly Report (Amended)',
}


def collect_sec_filings(days_back: int = 30) -> List[dict]:
    """Fetch recent 8-K, 10-K, and 10-Q filings from SEC EDGAR.

    8-K:  Material events — FID, EPC awards, project announcements
    10-K: Annual reports — capex, project updates, MD&A, risk factors
    10-Q: Quarterly reports — progress updates, spending, timeline changes

    SEC EDGAR is free, legal, and often has info before press releases.
    10-K/10-Q use a longer lookback (90 days) since they're filed less frequently.
    """
    articles: List[dict] = []
    cutoff_8k = datetime.now() - timedelta(days=days_back)
    # 10-K/10-Q filed quarterly/annually — use wider window
    cutoff_periodic = datetime.now() - timedelta(days=max(days_back, 90))

    for company, cik in SEC_COMPANY_CIKS.items():
        try:
            url = f"https://data.sec.gov/submissions/CIK{cik}.json"
            resp = requests.get(url, headers=_SEC_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            recent = data.get('filings', {}).get('recent', {})
            forms = recent.get('form', [])
            dates = recent.get('filingDate', [])
            accessions = recent.get('accessionNumber', [])
            descriptions = recent.get('primaryDocDescription', [])
            primary_docs = recent.get('primaryDocument', [])

            for i, (form, date_str, accession, desc) in enumerate(
                zip(forms, dates, accessions, descriptions)
            ):
                if i > 40:  # Scan more entries to catch periodic filings
                    break
                if form not in _SEC_FORM_TYPES:
                    continue

                try:
                    filing_date = datetime.strptime(date_str, '%Y-%m-%d')
                except ValueError:
                    continue

                # Use appropriate cutoff based on form type
                is_periodic = form.startswith('10-')
                cutoff = cutoff_periodic if is_periodic else cutoff_8k
                if filing_date < cutoff:
                    continue

                accession_clean = accession.replace('-', '')
                cik_num = cik.lstrip('0')
                # Build URL to actual filing document (not the index page)
                primary_doc = primary_docs[i] if i < len(primary_docs) else ''
                if primary_doc:
                    filing_url = (f"https://www.sec.gov/Archives/edgar/data/"
                                  f"{cik_num}/{accession_clean}/{primary_doc}")
                else:
                    # Fallback: index page URL (will try to resolve in _fetch_full_text)
                    filing_url = (f"https://www.sec.gov/Archives/edgar/data/"
                                  f"{cik_num}/{accession_clean}")

                form_label = _SEC_FORM_LABELS.get(form, form)
                articles.append({
                    'title': f"{company} {form} Filing: {desc or form_label}",
                    'url': filing_url,
                    'source': 'SEC EDGAR',
                    'snippet': (f"SEC {form} filing by {company} on {date_str}. "
                                f"{desc or form_label}"),
                    'published': date_str,
                    'sec_form_type': form,
                })

            time.sleep(0.2)  # Respect SEC rate limits

        except Exception as e:
            print(f"  SEC EDGAR error for {company}: {e}")

    return articles


def _score_sec_filing(article: dict) -> int:
    """Score SEC filing based on form type and hydrogen/ammonia relevance.

    8-K filings get higher base score (material events are more actionable).
    10-K/10-Q get lower base but can score high if keywords match.
    I10 fix: deduplicate keyword hits to avoid double-counting.
    """
    text = f"{article.get('title', '')} {article.get('snippet', '')}".lower()
    form = article.get('sec_form_type', '8-K')

    # Base score by form type
    if form.startswith('8-K'):
        score = 5   # Material events — inherently noteworthy
    elif form.startswith('10-K'):
        score = 3   # Annual — useful for capex/project updates
    else:
        score = 2   # 10-Q — quarterly check-in

    # I10 fix: count each keyword only once (presence, not frequency)
    matched = set()
    for kw in SEC_KEYWORDS:
        kw_lower = kw.lower()
        if kw_lower not in matched and kw_lower in text:
            matched.add(kw_lower)
            score += 10
    return min(score, 50)


# ---------------------------------------------------------------------------
# RSS FEED DISCOVERY (for company newsrooms)
# ---------------------------------------------------------------------------

def _discover_rss_feed(base_url: str) -> Optional[str]:
    """Discover RSS feed URL from a website by checking <link> tags and common paths."""
    try:
        resp = requests.get(base_url, headers=_HEADERS, timeout=10)
        soup = BeautifulSoup(resp.text, 'html.parser')

        # Check <link> tags for RSS/Atom feeds
        for link in soup.find_all('link', type=True):
            link_type = link.get('type', '').lower()
            if 'rss' in link_type or 'atom' in link_type or 'xml' in link_type:
                href = link.get('href')
                if href:
                    return urljoin(base_url, href)

        # Check common RSS paths
        common_paths = ['/feed', '/rss', '/feed.xml', '/rss.xml',
                        '/feeds/posts/default', '/news/feed', '/blog/feed', '/atom.xml']
        for path in common_paths:
            try:
                test_url = urljoin(base_url, path)
                test_resp = requests.head(test_url, headers=_HEADERS, timeout=5)
                if test_resp.status_code == 200:
                    ct = test_resp.headers.get('content-type', '').lower()
                    if 'xml' in ct or 'rss' in ct:
                        return test_url
            except Exception:
                pass

        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# OLLAMA LLM EXTRACTION (v3 — FREE local LLM)
# ---------------------------------------------------------------------------

_OLLAMA_AVAILABLE: Optional[bool] = None  # Cached availability check


def _auto_start_ollama() -> bool:
    """Attempt to start Ollama server automatically."""
    import shutil
    import subprocess
    import time

    ollama_path = shutil.which('ollama')
    if not ollama_path:
        return False

    print("  Ollama not running — starting automatically...")
    try:
        subprocess.Popen(
            [ollama_path, 'serve'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            time.sleep(0.5)
            try:
                resp = requests.get("http://localhost:11434/api/tags", timeout=2)
                if resp.status_code == 200:
                    print("  Ollama started successfully.")
                    return True
            except Exception:
                continue
        print("  WARNING: Ollama started but not responding after 10s.")
        return False
    except Exception as e:
        print(f"  WARNING: Could not auto-start Ollama: {e}")
        return False


def _check_ollama_available() -> bool:
    """Check if Ollama is running locally; auto-start if installed but not running."""
    global _OLLAMA_AVAILABLE
    if _OLLAMA_AVAILABLE is not None:
        return _OLLAMA_AVAILABLE
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        _OLLAMA_AVAILABLE = resp.status_code == 200
    except Exception:
        _OLLAMA_AVAILABLE = _auto_start_ollama()
    return _OLLAMA_AVAILABLE


def _check_ollama_model_exists(model: str = OLLAMA_MODEL) -> bool:
    """Check if the specified model is available in Ollama."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            models = [m.get('name', '').split(':')[0] for m in data.get('models', [])]
            return model.split(':')[0] in models
    except Exception:
        pass
    return False


_OLLAMA_WARMED_UP = False


def _warm_up_ollama():
    """Send a tiny request to load the model into memory (cold start fix)."""
    global _OLLAMA_WARMED_UP
    if _OLLAMA_WARMED_UP:
        return
    print("  Warming up Ollama model (first load may take 15-30s)...")
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": "Say OK",
                "stream": False,
                "options": {"num_predict": 5},
            },
            timeout=180,  # generous for cold start
        )
        if response.status_code == 200:
            _OLLAMA_WARMED_UP = True
            print("  Ollama model loaded and ready.")
        else:
            print(f"  WARNING: Ollama warm-up returned HTTP {response.status_code}")
    except requests.exceptions.Timeout:
        print("  WARNING: Ollama warm-up timed out (model may be too large for this machine)")
    except Exception as e:
        print(f"  WARNING: Ollama warm-up error: {e}")


def _ollama_generate(prompt: str, num_predict: int = 600,
                     timeout: int = OLLAMA_TIMEOUT) -> str:
    """Low-level Ollama generate call with retry. Returns raw response text or ''."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
        }
    }
    for attempt in range(2):  # 1 retry on timeout
        try:
            response = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
            if response.status_code == 200:
                return response.json().get("response", "")
        except requests.exceptions.Timeout:
            if attempt == 0:
                continue  # silent retry
        except Exception as e:
            print(f"    Ollama error: {e}")
            break
    return ""


def _ollama_extract(title: str, snippet: str, full_text: str = "") -> dict:
    """Extract structured data using local Ollama model.

    Returns dict with extracted fields, or empty dict on failure.
    """
    text = f"Title: {title}\n\nSnippet: {snippet}"
    if full_text:
        text += f"\n\nFull article:\n{full_text}"

    raw = _ollama_generate(_LLM_EXTRACTION_PROMPT + text, num_predict=600)
    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Strip stray backslashes the LLM sometimes appends
    cleaned = raw.replace('\\', '')
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        json_match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
    return {}


_LLM_RELEVANCE_PROMPT = """Classify if this article relates to hydrogen, ammonia, or CCS projects.

YES if about: H2/NH3 projects, SMR (Steam Methane Reforming — NOT nuclear), ATR (Autothermal Reforming), electrolysis, CCS/CCUS, hydrogen hubs, EPC/FEED/FID, methanol/SAF from H2, reformer equipment, refractory/high-temp materials for reformers (Inconel, HP/HK alloys, catalyst tubes), ammonia synthesis, CO2 capture, 45V/45Q/IRA policy, H2 pipelines/storage, offtake deals.

NO if about: oil/gas prices, crude tankers, solar/wind (no H2 link), EVs/batteries, nuclear SMR (Small Modular Reactors), general economics, stock markets.

Return ONLY JSON: {"relevant": true/false, "reason": "why"}

Article:
"""


def _llm_relevance_filter(articles: List[dict], min_score: int = 2,
                          skip_above: int = 10) -> int:
    """Use Ollama to filter out irrelevant articles during scoring.

    Smart filtering:
      - Articles with score >= skip_above: auto-keep (clearly relevant keywords)
      - Articles with score min_score..skip_above-1: LLM checks relevance
      - Articles with score < min_score: untouched (already low priority)

    Sets priority_score to 0 and category to 'FILTERED' for irrelevant articles.
    Returns count of articles removed.
    """
    if not _check_ollama_available():
        return 0

    # Only LLM-check borderline articles — high-scoring ones are clearly relevant
    candidates = [a for a in articles
                  if min_score <= a.get('priority_score', 0) < skip_above]

    auto_kept = sum(1 for a in articles if a.get('priority_score', 0) >= skip_above)

    if not candidates:
        if auto_kept:
            print(f"  LLM filter: {auto_kept} high-confidence articles auto-kept, "
                  f"0 borderline to check")
        return 0

    _warm_up_ollama()

    print(f"  LLM relevance filter: {auto_kept} auto-kept (score>={skip_above}), "
          f"checking {len(candidates)} borderline articles...")
    removed = 0

    for art in candidates:
        title = art.get('title', '')
        snippet = art.get('snippet', '')
        full_text = art.get('full_text', '')
        text = f"Title: {title}\nSnippet: {snippet}"
        if full_text:
            text += f"\n\nFull article text:\n{full_text}"

        raw = _ollama_generate(_LLM_RELEVANCE_PROMPT + text,
                               num_predict=80, timeout=60)
        if not raw:
            continue  # keep article if LLM fails

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            continue

        is_relevant = result.get('relevant', True)
        reason = result.get('reason', '')

        if not is_relevant:
            art['priority_score'] = 0
            art['category'] = 'FILTERED'
            removed += 1
            print(f"    FILTERED: {title[:60]}...")
            print(f"      Reason: {reason}")

    print(f"  Relevance filter complete: kept {len(candidates) - removed + auto_kept}, "
          f"removed {removed}")
    return removed


def _extract_with_llm(articles: List[dict], min_score: int = LLM_MIN_SCORE) -> int:
    """Run Ollama LLM extraction on high-priority articles.

    Modifies articles in-place, adding llm_* fields.
    Returns count of successfully extracted articles.
    """
    if not _check_ollama_available():
        print("  Ollama not running. Skipping LLM extraction.")
        print("    To enable: ollama serve && ollama pull llama3.1:8b")
        return 0

    if not _check_ollama_model_exists():
        print(f"  Ollama model '{OLLAMA_MODEL}' not found.")
        print(f"    To install: ollama pull {OLLAMA_MODEL}")
        return 0

    targets = [a for a in articles
               if a.get('priority_score', 0) >= min_score
               and not a.get('llm_extracted')]

    if not targets:
        print("  No articles need LLM extraction (all below threshold or already extracted)")
        return 0

    _warm_up_ollama()

    print(f"  Running Ollama LLM extraction on {len(targets)} articles...")
    print(f"     Model: {OLLAMA_MODEL}, Min score: {min_score}")

    extracted = 0
    start_time = time.time()

    for i, art in enumerate(targets):
        title = art.get('title', '')

        llm_data = _ollama_extract(
            title,
            art.get('snippet', ''),
            art.get('full_text', '')
        )

        if llm_data:
            art['llm_extracted'] = True
            art['llm_project_name'] = llm_data.get('project_name')
            art['llm_developer'] = llm_data.get('developer')
            art['llm_co_developers'] = json.dumps(llm_data.get('co_developers', []))
            art['llm_status'] = llm_data.get('status')
            art['llm_status_confirmed'] = llm_data.get('status_confirmed', False)
            art['llm_capacity_raw'] = llm_data.get('capacity_raw')
            art['llm_capacity_mtpa_h2'] = llm_data.get('capacity_mtpa_h2')
            art['llm_technology'] = llm_data.get('technology')
            art['llm_product'] = llm_data.get('product')
            art['llm_epc_contractor'] = llm_data.get('epc_contractor')
            art['llm_location_state'] = llm_data.get('location_state')
            art['llm_location_subregion'] = llm_data.get('location_subregion')
            art['llm_region'] = llm_data.get('region')
            art['llm_fid_date'] = llm_data.get('fid_date')
            art['llm_cod_date'] = llm_data.get('cod_date')
            art['llm_deal_value_usd'] = llm_data.get('deal_value_usd')
            extracted += 1

            project = llm_data.get('project_name') or 'Unknown'
            dev = llm_data.get('developer') or '?'
            status = llm_data.get('status') or '?'
            print(f"    [{i+1}/{len(targets)}] {project[:30]} | {dev} | {status}")
        else:
            art['llm_extracted'] = False
            print(f"    [{i+1}/{len(targets)}] Failed: {title[:50]}...")

    elapsed = time.time() - start_time
    print(f"  LLM extraction complete: {extracted}/{len(targets)} in {elapsed:.1f}s "
          f"({elapsed/len(targets):.1f}s/article)")

    return extracted


# ---------------------------------------------------------------------------
# STORAGE
# ---------------------------------------------------------------------------

def _store_articles(articles: List[dict], db_path: str = DATABASE_NAME) -> int:
    """Store articles in SQLite, skipping old and duplicate articles."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    new_count = 0
    old_count = 0
    cutoff_year = datetime.now().year - 1  # e.g., 2025 if now is 2026

    # I13 fix: import once outside loop
    try:
        from dateutil import parser as dp
    except ImportError:
        dp = None

    for art in articles:
        pub = art.get('published', '')
        if pub:
            try:
                if dp is None:
                    raise ValueError("dateutil not available")
                d = dp.parse(pub)
                if d.replace(tzinfo=None).year < cutoff_year:
                    old_count += 1
                    continue
            except Exception:
                for yr in range(2020, cutoff_year):
                    if str(yr) in pub:
                        old_count += 1
                        break
                else:
                    pass  # keep article if can't parse

        # Last-chance region detection: if still Unknown, try with full_text + URL
        if art.get('region', 'Unknown') == 'Unknown' and art.get('full_text'):
            region2, sub2 = detect_region(
                f"{art.get('title', '')} {art.get('snippet', '')} "
                f"{art.get('full_text', '')}",
                url=art.get('url', ''))
            if region2 != 'Unknown':
                art['region'] = region2
                if sub2:
                    art['subregion'] = sub2

        try:
            cursor.execute('''
                INSERT INTO articles
                (title, url, url_hash, content_hash, source, snippet, full_text,
                 category, priority_score, published_date, fetched_date,
                 keywords, region, subregion, paywall_blocked,
                 llm_extracted, llm_project_name, llm_developer, llm_co_developers,
                 llm_status, llm_status_confirmed, llm_capacity_raw, llm_capacity_mtpa_h2,
                 llm_technology, llm_product, llm_epc_contractor, llm_location_state,
                 llm_location_subregion, llm_region, llm_fid_date, llm_cod_date,
                 llm_deal_value_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                art['title'], art['url'],
                art.get('url_hash', _url_hash(art['url'])),
                art.get('content_hash', _content_hash(art.get('title', ''), art.get('snippet', ''))),
                art.get('source', 'unknown'), art.get('snippet', ''),
                art.get('full_text'),
                art.get('category', 'LOW'), art.get('priority_score', 0),
                pub, datetime.now().isoformat(),
                art.get('keywords', ''), art.get('region', 'Unknown'),
                art.get('subregion', ''),
                1 if art.get('paywall_blocked') else 0,
                # LLM fields
                1 if art.get('llm_extracted') else 0,
                art.get('llm_project_name'),
                art.get('llm_developer'),
                art.get('llm_co_developers'),
                art.get('llm_status'),
                1 if art.get('llm_status_confirmed') else 0,
                art.get('llm_capacity_raw'),
                art.get('llm_capacity_mtpa_h2'),
                art.get('llm_technology'),
                art.get('llm_product'),
                art.get('llm_epc_contractor'),
                art.get('llm_location_state'),
                art.get('llm_location_subregion'),
                art.get('llm_region'),
                art.get('llm_fid_date'),
                art.get('llm_cod_date'),
                art.get('llm_deal_value_usd'),
            ))
            new_count += 1
        except sqlite3.IntegrityError:
            continue
        except Exception as e:
            print(f"  Store error: {e}")

    if old_count:
        print(f"  Skipped {old_count} old articles (before {cutoff_year})")

    conn.commit()
    conn.close()
    return new_count


def _store_single_article(art: dict, cursor, dp=None) -> bool:
    """Persist a single article to SQLite using an existing cursor.

    Returns True if inserted, False if duplicate/skipped.
    Caller is responsible for conn.commit().
    """
    cutoff_year = datetime.now().year - 1

    pub = art.get('published', '')
    if pub:
        try:
            if dp is None:
                raise ValueError("dateutil not available")
            d = dp.parse(pub)
            if d.replace(tzinfo=None).year < cutoff_year:
                return False
        except Exception:
            for yr in range(2020, cutoff_year):
                if str(yr) in pub:
                    return False

    # Last-chance region detection
    if art.get('region', 'Unknown') == 'Unknown' and art.get('full_text'):
        region2, sub2 = detect_region(
            f"{art.get('title', '')} {art.get('snippet', '')} "
            f"{art.get('full_text', '')}",
            url=art.get('url', ''))
        if region2 != 'Unknown':
            art['region'] = region2
            if sub2:
                art['subregion'] = sub2

    try:
        cursor.execute('''
            INSERT INTO articles
            (title, url, url_hash, content_hash, source, snippet, full_text,
             category, priority_score, published_date, fetched_date,
             keywords, region, subregion, paywall_blocked,
             llm_extracted, llm_project_name, llm_developer, llm_co_developers,
             llm_status, llm_status_confirmed, llm_capacity_raw, llm_capacity_mtpa_h2,
             llm_technology, llm_product, llm_epc_contractor, llm_location_state,
             llm_location_subregion, llm_region, llm_fid_date, llm_cod_date,
             llm_deal_value_usd, market_impact)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            art['title'], art['url'],
            art.get('url_hash', _url_hash(art['url'])),
            art.get('content_hash', _content_hash(art.get('title', ''), art.get('snippet', ''))),
            art.get('source', 'unknown'), art.get('snippet', ''),
            art.get('full_text'),
            art.get('category', 'LOW'), art.get('priority_score', 0),
            pub, datetime.now().isoformat(),
            art.get('keywords', ''), art.get('region', 'Unknown'),
            art.get('subregion', ''),
            1 if art.get('paywall_blocked') else 0,
            1 if art.get('llm_extracted') else 0,
            art.get('llm_project_name'),
            art.get('llm_developer'),
            art.get('llm_co_developers'),
            art.get('llm_status'),
            1 if art.get('llm_status_confirmed') else 0,
            art.get('llm_capacity_raw'),
            art.get('llm_capacity_mtpa_h2'),
            art.get('llm_technology'),
            art.get('llm_product'),
            art.get('llm_epc_contractor'),
            art.get('llm_location_state'),
            art.get('llm_location_subregion'),
            art.get('llm_region'),
            art.get('llm_fid_date'),
            art.get('llm_cod_date'),
            art.get('llm_deal_value_usd'),
            art.get('market_impact', 'LOW'),
        ))
        return True
    except sqlite3.IntegrityError:
        return False
    except Exception as e:
        logger.warning(f"Store single article error: {e}")
        return False


def _update_article_llm_fields(art: dict, cursor) -> bool:
    """Update LLM-extracted fields for an already-stored article.

    Called after Layer 5 LLM refinement to update the row in-place.
    Caller is responsible for conn.commit().
    """
    url_hash = art.get('url_hash', _url_hash(art.get('url', '')))
    try:
        cursor.execute('''
            UPDATE articles SET
                llm_extracted = ?,
                llm_project_name = ?, llm_developer = ?, llm_co_developers = ?,
                llm_status = ?, llm_status_confirmed = ?,
                llm_capacity_raw = ?, llm_capacity_mtpa_h2 = ?,
                llm_technology = ?, llm_product = ?, llm_epc_contractor = ?,
                llm_location_state = ?, llm_location_subregion = ?, llm_region = ?,
                llm_fid_date = ?, llm_cod_date = ?, llm_deal_value_usd = ?,
                priority_score = ?, category = ?, market_impact = ?
            WHERE url_hash = ?
        ''', (
            1 if art.get('llm_extracted') else 0,
            art.get('llm_project_name'),
            art.get('llm_developer'),
            art.get('llm_co_developers'),
            art.get('llm_status'),
            1 if art.get('llm_status_confirmed') else 0,
            art.get('llm_capacity_raw'),
            art.get('llm_capacity_mtpa_h2'),
            art.get('llm_technology'),
            art.get('llm_product'),
            art.get('llm_epc_contractor'),
            art.get('llm_location_state'),
            art.get('llm_location_subregion'),
            art.get('llm_region'),
            art.get('llm_fid_date'),
            art.get('llm_cod_date'),
            art.get('llm_deal_value_usd'),
            art.get('priority_score', 0),
            art.get('category', 'LOW'),
            art.get('market_impact', 'LOW'),
            url_hash,
        ))
        return cursor.rowcount > 0
    except Exception as e:
        logger.warning(f"Update LLM fields error: {e}")
        return False


def _update_source_stats(articles: List[dict], db_path: str = DATABASE_NAME):
    """Update source_stats table based on collected articles."""
    stats: Dict[str, dict] = {}
    for art in articles:
        src = art.get('source', 'unknown')
        if src not in stats:
            stats[src] = {'total': 0, 'high': 0, 'scores': []}
        stats[src]['total'] += 1
        score = art.get('priority_score', 0)
        stats[src]['scores'].append(score)
        if score >= 5:
            stats[src]['high'] += 1

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    now = datetime.now().isoformat()
    for src, s in stats.items():
        avg = sum(s['scores']) / len(s['scores']) if s['scores'] else 0
        c.execute('''
            INSERT INTO source_stats (source_name, total_articles, high_priority_articles, avg_score, last_fetched)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_name) DO UPDATE SET
                total_articles = total_articles + excluded.total_articles,
                high_priority_articles = high_priority_articles + excluded.high_priority_articles,
                avg_score = (avg_score + excluded.avg_score) / 2.0,
                last_fetched = excluded.last_fetched
        ''', (src, s['total'], s['high'], avg, now))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# 5-LAYER SEMANTIC PIPELINE HELPERS
# ---------------------------------------------------------------------------

def _compute_priority_score(art: dict) -> int:
    """Compute priority score from semantic relevance + extraction quality.

    Score breakdown (per plan):
      base = relevance_score * 50   (0-50)
      + developer found:             +10
      + FID/EPC/Construction status:  +15
      + FEED/Pre-FEED status:         +10
      + capacity detected:            +10
      + deal_value > $100M:           +10
    """
    base = int(art.get('relevance_score', 0) * 50)

    bonus = 0
    if art.get('llm_developer'):
        bonus += 10
    status = (art.get('llm_status') or '').lower()
    if status in ('fid', 'epc award', 'construction'):
        bonus += 15
    elif status in ('feed', 'pre-feed'):
        bonus += 10
    if art.get('llm_capacity_mtpa_h2'):
        bonus += 10
    deal = art.get('llm_deal_value_usd')
    if deal and deal > 100_000_000:
        bonus += 10

    return base + bonus


def _classify_market_impact(art: dict) -> str:
    """Classify market impact: HIGH, MEDIUM, or LOW.

    HIGH → triggers Layer 5 LLM refinement.
    I11 fix: requires relevance_score >= 0.50 to qualify for HIGH/MEDIUM,
    preventing barely-relevant articles from triggering expensive LLM.
    """
    relevance = art.get('relevance_score', 0)
    status = (art.get('llm_status') or '').lower()
    deal = art.get('llm_deal_value_usd')

    # Must be at least moderately relevant to justify LLM cost
    if relevance < 0.50:
        return 'LOW'

    if status in ('fid', 'epc award') or (deal and deal > 500_000_000):
        return 'HIGH'
    if status in ('construction', 'feed') or art.get('llm_capacity_mtpa_h2'):
        return 'MEDIUM'
    return 'LOW'


def _run_semantic_pipeline(articles: List[dict], db_path: str = DATABASE_NAME,
                           use_llm: bool = True) -> List[dict]:
    """Process articles through the 5-layer semantic pipeline.

    Saves incrementally: each article is persisted to SQLite + ChromaDB
    immediately after extraction and scoring, so nothing is lost if the
    process is interrupted.

    Optimized ordering (C8 fix):
      1. Embedding classification → filter relevant
      2. ChromaDB semantic dedup (BEFORE full-text fetch — saves network I/O)
      3. Fetch full text (only for non-duplicate, relevant articles)
      4. GLiNER NER + GLiREL relation extraction  }  per-article,
      5. Priority scoring + market impact          }  saved immediately
      6. LLM refinement (HIGH market_impact only) → updates saved rows
      7. ChromaDB + SQLite already populated incrementally

    Modifies articles in-place and returns only relevant ones.
    """
    if not articles:
        return []

    classifier = _get_classifier()
    extractor = _get_extractor()
    store = _get_store()

    # ---- Layer 1: Embedding Classification ----
    print(f"  Layer 1: Embedding classification ({len(articles)} articles)...")
    start = time.time()
    classifications = classifier.classify_batch(articles)

    relevant = []
    for art, cls in zip(articles, classifications):
        art['relevance_score'] = cls['score']
        art['embedding'] = cls['embedding']
        if cls['relevant']:
            relevant.append(art)

    elapsed = time.time() - start
    print(f"    {len(relevant)}/{len(articles)} relevant "
          f"(threshold={classifier._threshold:.2f}) in {elapsed:.1f}s")

    if not relevant:
        print("    No articles passed embedding classification.")
        return []

    # ---- Layer 4: Semantic Dedup BEFORE full-text fetch (C8 fix) ----
    print(f"  Layer 4: Semantic dedup ({len(relevant)} articles)...")
    deduped = store.batch_dedup(relevant, threshold=0.95)
    dups = len(relevant) - len(deduped)
    print(f"    {len(relevant)} → {len(deduped)} after semantic dedup "
          f"({dups} duplicates removed)")

    if not deduped:
        return []

    # ---- Fetch full text (only non-duplicate, relevant articles) ----
    no_text = [a for a in deduped if not a.get('full_text')]
    if no_text:
        print(f"  Fetching full text for {len(no_text)} articles...")
        n_fetched = _fetch_full_texts(deduped, min_score=0)
        print(f"    Fetched {n_fetched}/{len(no_text)} article bodies")

    # ---- Incremental processing: extract + score + save per article ----
    # Open a persistent SQLite connection for the duration of the pipeline
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    try:
        from dateutil import parser as dp
    except ImportError:
        dp = None

    print(f"  Layers 2+3: Entity/relation extraction ({len(deduped)} articles)...")
    start = time.time()
    saved_count = 0

    for i, art in enumerate(deduped):
        # -- Extract entities --
        text = art.get('full_text') or art.get('snippet', '')
        title = art.get('title', '')
        try:
            extraction = extractor.extract(text, title)
            art.update(extraction.to_dict())
        except Exception as e:
            print(f"    Extraction error: {e}")
            art['llm_extracted'] = False

        # -- Priority scoring --
        art['priority_score'] = _compute_priority_score(art)
        art['market_impact'] = _classify_market_impact(art)
        score = art['priority_score']
        if score >= 40:
            art['category'] = 'CRITICAL'
        elif score >= 25:
            art['category'] = 'HIGH'
        elif score >= 10:
            art['category'] = 'MEDIUM'
        else:
            art['category'] = 'LOW'

        # -- Save immediately to SQLite + ChromaDB --
        if _store_single_article(art, cursor, dp=dp):
            saved_count += 1
        conn.commit()

        emb = art.get('embedding')
        if emb is not None:
            meta = {}
            for k in ['title', 'source', 'priority_score', 'category',
                       'region', 'published', 'relevance_score']:
                v = art.get(k)
                if v is not None:
                    meta[k] = v if isinstance(v, (str, int, float, bool)) \
                        else str(v)
            store.add_article(art['url'], emb, meta)

        # Progress indicator every 10 articles
        if (i + 1) % 10 == 0:
            print(f"    Processed & saved {i + 1}/{len(deduped)} articles...")

    elapsed = time.time() - start
    extracted_count = sum(1 for a in deduped if a.get('llm_extracted'))
    print(f"    Extracted entities from {extracted_count}/{len(deduped)} "
          f"articles in {elapsed:.1f}s")
    print(f"    Incrementally saved {saved_count} articles to SQLite")

    # ---- Layer 5: LLM Refinement (HIGH market_impact only) ----
    if use_llm:
        high_articles = [a for a in deduped if a.get('market_impact') == 'HIGH']
        if high_articles:
            print(f"  Layer 5: LLM refinement ({len(high_articles)} HIGH-impact articles)...")
            n_refined = _extract_with_llm(high_articles, min_score=0)
            # Update already-saved rows with LLM-refined data
            for art in high_articles:
                art['priority_score'] = _compute_priority_score(art)
                _update_article_llm_fields(art, cursor)
                # Also update ChromaDB metadata
                emb = art.get('embedding')
                if emb is not None:
                    meta = {}
                    for k in ['title', 'source', 'priority_score', 'category',
                               'region', 'published', 'relevance_score']:
                        v = art.get(k)
                        if v is not None:
                            meta[k] = v if isinstance(v, (str, int, float, bool)) \
                                else str(v)
                    store.add_article(art['url'], emb, meta)
            conn.commit()
            print(f"    LLM refined {n_refined} articles (rows updated)")
        else:
            print("  Layer 5: No HIGH-impact articles — skipping LLM")

    conn.close()
    print(f"  ChromaDB: {store.count()} total articles stored")

    deduped.sort(key=lambda x: x.get('priority_score', 0), reverse=True)
    return deduped


# ---------------------------------------------------------------------------
# PUBLIC API: BlueH2NewsCollector
# ---------------------------------------------------------------------------

class BlueH2NewsCollector:
    """Main news collector for blue hydrogen / ammonia intelligence."""

    def __init__(self, db_path: str = DATABASE_NAME, sites_yaml: str = SITES_YAML):
        self.db_path = db_path
        self.sites_yaml = sites_yaml
        self.active_tags = ACTIVE_TAGS
        setup_database(db_path)

    # -- Collection methods --------------------------------------------------

    def collect_rss(self) -> List[dict]:
        """Collect articles from RSS feeds defined in sites.yaml.

        Fetches RSS via requests (bypasses feedparser SSL issues on macOS),
        then parses the XML with feedparser.
        Resolves Google News redirect URLs.
        Returns scored articles — LLM filtering handled by callers.
        """
        sites = load_sites(self.sites_yaml, self.active_tags)
        rss_sites = [s for s in sites if s.get('type') == 'rss_feed']
        articles: List[dict] = []
        google_blocked = 0
        google_news_dead = False  # Once one 503, skip the rest

        for site in rss_sites:
            name = site.get('name', 'unknown')
            for url in site.get('start_urls', []):
                try:
                    is_google_news = 'news.google.com' in url

                    # Skip remaining Google News feeds if already blocked
                    if is_google_news and google_news_dead:
                        google_blocked += 1
                        continue

                    print(f"  Fetching RSS: {name}...")

                    # Fetch with requests (handles SSL properly), then parse
                    try:
                        resp = requests.get(url, headers=_HEADERS, timeout=15)
                        if resp.status_code == 503 and is_google_news:
                            google_blocked += 1
                            google_news_dead = True
                            continue  # Google News bot-blocking — skip silently
                        if resp.status_code != 200:
                            print(f"    WARNING: {name} HTTP {resp.status_code}")
                            continue
                        feed = feedparser.parse(resp.text)
                    except requests.exceptions.RequestException:
                        # Fallback to feedparser's own fetching
                        feed = feedparser.parse(url, request_headers=_HEADERS)

                    count = len(feed.entries)
                    if count == 0:
                        print(f"    WARNING: {name} returned 0 entries")
                        continue
                    else:
                        print(f"    {name}: {count} entries")
                    max_items = site.get('max_items', 50)

                    for entry in feed.entries[:max_items]:
                        raw_snippet = entry.get('summary', '')
                        raw_link = entry.get('link', '')
                        # Resolve Google News redirects
                        if is_google_news and raw_link:
                            resolved_link = _resolve_google_news_url(raw_link)
                            if resolved_link != raw_link:
                                time.sleep(0.3)
                        else:
                            resolved_link = raw_link
                        articles.append({
                            'title': entry.get('title', ''),
                            'url': resolved_link,
                            'snippet': _strip_html(raw_snippet),
                            'source': name,
                            'published': entry.get('published', ''),
                        })
                except Exception as e:
                    print(f"    RSS error ({name}): {e}")

        if google_blocked:
            print(f"\n  NOTE: Google News blocked {google_blocked} RSS feeds (503).")
            print(f"  This is normal — Google blocks automated RSS access.")
            print(f"  Articles will be collected via web search instead.\n")

        # Keyword scoring — skip when NLP pipeline will handle it (C10/M8 fix)
        if not _NLP_AVAILABLE:
            for art in articles:
                cat, score, kws, region, subregion = categorize_article(
                    art.get('title', ''), art.get('snippet', ''),
                    url=art.get('url', ''), full_text=art.get('full_text', ''))
                art['category'] = cat
                art['priority_score'] = score
                art['keywords'] = ','.join(kws)
                art['region'] = region
                art['subregion'] = subregion or ''

        return articles

    def collect_search(self, queries: Optional[List[str]] = None) -> List[dict]:
        """Run deep web search for each query."""
        queries = queries or SEARCH_QUERIES
        articles: List[dict] = []
        for q in queries:
            results = deep_web_search(q, num_pages=5)
            articles.extend(results)
            time.sleep(2)
        if not _NLP_AVAILABLE:
            for art in articles:
                cat, score, kws, region, subregion = categorize_article(
                    art.get('title', ''), art.get('snippet', ''),
                    url=art.get('url', ''), full_text=art.get('full_text', ''))
                art['category'] = cat
                art['priority_score'] = score
                art['keywords'] = ','.join(kws)
                art['region'] = region
                art['subregion'] = subregion or ''
        return articles

    def collect_scrapers(self) -> List[dict]:
        """Run web scrapers on known aggregator sites."""
        articles: List[dict] = []
        for name, url in _SCRAPER_URLS.items():
            results = _generic_scrape(url, name)
            articles.extend(results)
        if not _NLP_AVAILABLE:
            for art in articles:
                if not art.get('category'):
                    cat, score, kws, region, subregion = categorize_article(
                        art.get('title', ''), art.get('snippet', ''),
                        url=art.get('url', ''), full_text=art.get('full_text', ''))
                    art['category'] = cat
                    art['priority_score'] = score
                    art['keywords'] = ','.join(kws)
                    art['region'] = region
                    art['subregion'] = subregion or ''
        return articles

    def collect_sec_edgar(self, days_back: int = 30) -> List[dict]:
        """Collect SEC EDGAR filings (8-K, 10-K, 10-Q) for relevant companies."""
        print("  Collecting SEC EDGAR filings...")
        sec_articles = collect_sec_filings(days_back=days_back)
        # Only keyword-score if NLP pipeline won't handle it (C10/I14 fix)
        if not _NLP_AVAILABLE:
            for art in sec_articles:
                art['priority_score'] = _score_sec_filing(art)
                art['category'] = 'HIGH' if art['priority_score'] >= 15 else 'MEDIUM'
                region, subregion = detect_region(
                    f"{art.get('title', '')} {art.get('snippet', '')}",
                    url=art.get('url', ''))
                art['region'] = region
                art['subregion'] = subregion or ''
        print(f"  SEC EDGAR: {len(sec_articles)} filings")
        return sec_articles

    def collect_all(self, max_articles: int = 200,
                    skip_search_if_rss_sufficient: bool = True,
                    use_llm: bool = True,
                    llm_min_score: int = LLM_MIN_SCORE) -> int:
        """Full collection: RSS + SEC EDGAR + optionally search + scrapers.

        Uses the 5-layer semantic pipeline when NLP dependencies are available,
        falling back to keyword scoring + LLM when they are not.

        Layers (when NLP available):
          1. Embedding classification (sentence-transformers) — ~5ms/doc
          2. GLiNER zero-shot NER — ~30ms/doc
          3. GLiREL relation extraction — ~30ms/doc
          4. ChromaDB semantic dedup + storage
          5. LLM refinement — HIGH market_impact only

        Args:
            max_articles: Maximum articles to store.
            skip_search_if_rss_sufficient: Skip web search if RSS returns >50.
            use_llm: Run LLM refinement on HIGH-impact articles.
            llm_min_score: Minimum priority score for LLM (fallback mode).

        Returns count of new articles stored.
        """
        pipeline_mode = 'SEMANTIC' if _NLP_AVAILABLE else 'KEYWORD+LLM'
        print(f"\n{'=' * 70}")
        print(f"BLUE H2/AMMONIA INTELLIGENCE COLLECTION [{pipeline_mode}]")
        print(f"Target: up to {max_articles} articles | LLM: {'ON' if use_llm else 'OFF'}")
        print(f"{'=' * 70}\n")

        # Pre-load known hashes from DB so we skip them early
        existing = _load_existing_url_hashes(self.db_path)
        existing_content = _load_existing_content_hashes(self.db_path)
        if existing:
            print(f"Database: {len(existing)} articles already stored — will skip known URLs\n")

        all_articles: List[dict] = []

        # 1. RSS
        print("RSS feeds...")
        rss = self.collect_rss()
        all_articles.extend(rss)
        print(f"  RSS total: {len(rss)} articles\n")

        # 2. SEC EDGAR
        sec = self.collect_sec_edgar(days_back=30)
        all_articles.extend(sec)
        print()

        # 3. Deep search (conditionally)
        if skip_search_if_rss_sufficient and len(rss) > 50:
            print(f"RSS returned {len(rss)} articles — skipping web search\n")
        else:
            print("Deep web search...")
            search = self.collect_search()
            all_articles.extend(search)
            print(f"  Search: {len(search)} articles\n")

        # 4. Scrapers
        print("Web scrapers...")
        scraped = self.collect_scrapers()
        all_articles.extend(scraped)
        print(f"  Scrapers: {len(scraped)} articles\n")

        # URL / content dedup against database
        print("Deduplicating against database...")
        unique = _deduplicate(all_articles, existing_hashes=existing,
                              existing_content_hashes=existing_content)
        print(f"  {len(all_articles)} collected → {len(unique)} new\n")

        if not unique:
            print("  No new articles to process.")
            print(f"{'=' * 70}\n")
            return 0

        # ---------- 5-LAYER SEMANTIC PIPELINE ----------
        if _NLP_AVAILABLE:
            start_time = time.time()
            final = _run_semantic_pipeline(unique, db_path=self.db_path,
                                           use_llm=use_llm)
            if not final:
                print("  Semantic pipeline returned 0 articles.")
                print(f"{'=' * 70}\n")
                return 0

            final = final[:max_articles]
            elapsed = time.time() - start_time
            print(f"\n  Semantic pipeline: {len(final)} articles in {elapsed:.1f}s "
                  f"({elapsed/max(len(unique),1)*1000:.0f}ms/article)")
        else:
            # ---------- FALLBACK: keyword scoring + LLM ----------
            print("  NLP modules not available — using keyword scoring fallback\n")

            if use_llm:
                print(f"LLM Relevance Filter ({len(unique)} new articles)...")
                _warm_up_ollama()
                _llm_relevance_filter(unique, min_score=2, skip_above=10)
                unique = [a for a in unique if a.get('category') != 'FILTERED']
                print(f"  After LLM filter: {len(unique)} relevant\n")

                if not unique:
                    print("  All new articles were irrelevant.")
                    print(f"{'=' * 70}\n")
                    return 0

            unique.sort(key=lambda x: x.get('priority_score', 0), reverse=True)
            final = unique[:max_articles]

            # Fetch full text
            no_text = [a for a in final if not a.get('full_text')]
            if no_text:
                print(f"Fetching full text for {len(no_text)} articles...")
                n_fetched = _fetch_full_texts(final, min_score=0)
                print(f"  Fetched {n_fetched}/{len(no_text)} article bodies\n")

            # LLM extraction
            if use_llm:
                print("LLM Extraction (Ollama)...")
                n_extracted = _extract_with_llm(final, min_score=llm_min_score)
                print(f"  Extracted {n_extracted} articles with LLM\n")

        # Category summary
        counts = {}
        for a in final:
            c = a.get('category', 'LOW')
            counts[c] = counts.get(c, 0) + 1
        print(f"\n  Final: {len(final)} articles")
        for c in ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']:
            if counts.get(c, 0):
                print(f"    {c}: {counts[c]}")

        # Store in SQLite — semantic pipeline already saved incrementally,
        # so only the fallback path needs batch storage here.
        if _NLP_AVAILABLE:
            stored = len(final)  # already saved article-by-article
        else:
            stored = _store_articles(final, self.db_path)
        _update_source_stats(final, self.db_path)

        print(f"\n{'=' * 70}")
        print(f"Collection complete: {stored} new articles stored")
        extracted_count = sum(1 for a in final if a.get('llm_extracted'))
        if extracted_count:
            print(f"   Entities extracted: {extracted_count} articles")
        if _NLP_AVAILABLE:
            try:
                store = _get_store()
                print(f"   ChromaDB total: {store.count()} articles")
            except Exception:
                pass
        print(f"{'=' * 70}\n")
        return stored

    def collect_rss_only(self, use_llm: bool = True,
                         llm_min_score: int = LLM_MIN_SCORE) -> int:
        """RSS-only collection with 5-layer semantic pipeline.

        Uses semantic pipeline when NLP dependencies available,
        falls back to keyword + LLM otherwise.

        Flow (semantic mode):
          1. Fetch RSS feeds (instant)
          2. URL/content dedup against DB
          3. 5-layer semantic pipeline (embed → extract → dedup → score → LLM)
          4. Store in ChromaDB + SQLite

        Returns count of new articles stored.
        """
        pipeline_mode = 'SEMANTIC' if _NLP_AVAILABLE else 'KEYWORD+LLM'
        print(f"\n  RSS Collection [{pipeline_mode}]")

        # Pre-load known hashes from DB
        existing = _load_existing_url_hashes(self.db_path)
        existing_content = _load_existing_content_hashes(self.db_path)
        if existing:
            print(f"  Database: {len(existing)} articles already stored\n")

        # 1. Fetch RSS (just fetch + score, no LLM)
        rss = self.collect_rss()
        print(f"  RSS total: {len(rss)} articles")

        # 2. Dedup FIRST — only new articles proceed
        unique = _deduplicate(rss, existing_hashes=existing,
                              existing_content_hashes=existing_content)
        print(f"  {len(rss)} collected → {len(unique)} new\n")

        if not unique:
            print("  No new articles to process — database is up to date.")
            return 0

        # ---------- 5-LAYER SEMANTIC PIPELINE ----------
        if _NLP_AVAILABLE:
            start_time = time.time()
            final = _run_semantic_pipeline(unique, db_path=self.db_path,
                                           use_llm=use_llm)
            if not final:
                print("  Semantic pipeline returned 0 articles.")
                return 0

            elapsed = time.time() - start_time
            print(f"  Semantic pipeline: {len(final)} articles in {elapsed:.1f}s")
        else:
            # ---------- FALLBACK: keyword scoring + LLM ----------
            if use_llm:
                print(f"  LLM Relevance Filter ({len(unique)} new articles)...")
                _warm_up_ollama()
                _llm_relevance_filter(unique, min_score=2, skip_above=10)
                unique = [a for a in unique if a.get('category') != 'FILTERED']
                print(f"  After LLM filter: {len(unique)} relevant\n")

                if not unique:
                    print("  All new articles were irrelevant.")
                    return 0

            no_text = [a for a in unique if not a.get('full_text')]
            if no_text:
                print(f"  Fetching full text for {len(no_text)} articles...")
                n = _fetch_full_texts(unique, min_score=0)
                print(f"  Fetched {n}/{len(no_text)} article bodies")

            if use_llm:
                print("  LLM Extraction (Ollama)...")
                n_extracted = _extract_with_llm(unique, min_score=llm_min_score)
                print(f"  Extracted {n_extracted} articles with LLM")

            final = unique

        # Store in SQLite — semantic pipeline already saved incrementally,
        # so only the fallback path needs batch storage here.
        if _NLP_AVAILABLE:
            stored = len(final)
        else:
            stored = _store_articles(final, self.db_path)
        _update_source_stats(final, self.db_path)
        print(f"  RSS collection complete: {stored} new articles stored.")
        return stored

    # -- Query methods -------------------------------------------------------

    def get_recent_articles(self, days: int = 7, min_score: int = 0) -> List[dict]:
        """Retrieve recent articles from the database.

        Filters by *published_date* (when the article was actually published),
        falling back to fetched_date for articles without a publish date.
        Date comparison done in Python because published_date may be RFC 2822
        while fetched_date is ISO — SQLite string comparison doesn't work.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''
            SELECT * FROM articles
            WHERE priority_score >= ?
            ORDER BY priority_score DESC
        ''', (min_score,)).fetchall()
        conn.close()

        cutoff_dt = datetime.now() - timedelta(days=days)
        result = []
        for r in rows:
            d = dict(r)
            date_str = d.get('published_date') or d.get('fetched_date') or ''
            art_dt = _parse_any_date(date_str)
            if art_dt and art_dt >= cutoff_dt:
                result.append(d)
        # Sort by parsed date descending, then score
        result.sort(key=lambda x: (
            _parse_any_date(x.get('published_date') or x.get('fetched_date') or '') or datetime.min
        ), reverse=True)
        return result

    def get_llm_extracted_articles(self, min_score: int = 0) -> List[dict]:
        """Retrieve articles with successful LLM extraction."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute('''
            SELECT * FROM articles
            WHERE llm_extracted = 1 AND priority_score >= ?
            ORDER BY priority_score DESC
        ''', (min_score,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_statistics(self) -> dict:
        """Database statistics."""
        conn = sqlite3.connect(self.db_path)
        stats = {}
        stats['total_articles'] = conn.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        stats['by_category'] = {r[0]: r[1] for r in conn.execute(
            'SELECT category, COUNT(*) FROM articles GROUP BY category').fetchall()}
        stats['by_region'] = {r[0]: r[1] for r in conn.execute(
            'SELECT region, COUNT(*) FROM articles WHERE region != "" GROUP BY region').fetchall()}
        stats['by_source'] = {r[0]: r[1] for r in conn.execute(
            'SELECT source, COUNT(*) FROM articles GROUP BY source ORDER BY COUNT(*) DESC LIMIT 20').fetchall()}
        stats['total_projects'] = conn.execute('SELECT COUNT(*) FROM projects').fetchone()[0]
        stats['paywalled'] = conn.execute(
            'SELECT COUNT(*) FROM articles WHERE paywall_blocked = 1').fetchone()[0]
        try:
            stats['llm_extracted'] = conn.execute(
                'SELECT COUNT(*) FROM articles WHERE llm_extracted = 1').fetchone()[0]
        except Exception:
            stats['llm_extracted'] = 0
        conn.close()
        return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Blue H2 News Collector')
    parser.add_argument('--rss-only', action='store_true',
                        help='RSS-only mode (faster)')
    parser.add_argument('--no-llm', action='store_true',
                        help='Disable LLM extraction')
    parser.add_argument('--llm-min-score', type=int, default=LLM_MIN_SCORE,
                        help=f'Minimum score for LLM extraction (default: {LLM_MIN_SCORE})')
    parser.add_argument('--max-articles', type=int, default=200,
                        help='Maximum articles to collect (default: 200)')

    args = parser.parse_args()

    collector = BlueH2NewsCollector()

    if args.rss_only:
        n = collector.collect_rss_only(
            use_llm=not args.no_llm,
            llm_min_score=args.llm_min_score
        )
    else:
        n = collector.collect_all(
            max_articles=args.max_articles,
            use_llm=not args.no_llm,
            llm_min_score=args.llm_min_score
        )

    print(f"\nStored {n} new articles.")
    stats = collector.get_statistics()
    print(f"Total in DB: {stats['total_articles']}")
    print(f"By category: {stats['by_category']}")
    print(f"LLM extracted: {stats['llm_extracted']}")
    print(f"Paywalled: {stats['paywalled']}")
