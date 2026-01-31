#!/usr/bin/env python3
"""
Energy Intelligence API & Web Collector (Incremental)
======================================================

Discovers and downloads energy reports, data, and learnings from multiple sources.

SOURCES COVERED:
----------------
1. EIA (US Energy Information Administration) - API + Web
2. IEA (International Energy Agency) - Web
3. IRENA (International Renewable Energy Agency) - Web
4. DOE/NREL (US Department of Energy / National Renewable Energy Lab) - Web + API
5. Hydrogen Council - Web
6. Ammonia Energy Association - RSS + Web
7. Hydrogen News Sites - RSS
8. Regional: Oman (HYDROM), Chile, EU
9. World Bank / IFC - Web

API KEYS:
---------
Set these environment variables for enhanced data access:

    export EIA_API_KEY="your_eia_key"        # Required for EIA data API
    export NREL_API_KEY="your_nrel_key"      # Optional for NREL datasets

Get your free API keys:
    - EIA: https://www.eia.gov/opendata/register.php
    - NREL: https://developer.nrel.gov/signup/

OUTPUTS:
--------
- state/energy_intel_state.json          (incremental tracking)
- data/raw/<source>/...                  (raw HTML + downloaded files)
- data/curated/energy_intel/learnings.jsonl  (normalized text records)

USAGE:
------
    python API_discovery.py                     # Run all sources
    python API_discovery.py --source eia        # Run specific source
    python API_discovery.py --source eia iea    # Run multiple sources
    python API_discovery.py --list-sources      # Show available sources
    python API_discovery.py --check-api-keys    # Verify API key setup
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
import hashlib
import logging
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlencode

import requests
from bs4 import BeautifulSoup

# ============================================================================
# API KEYS (from config file or environment variables)
# ============================================================================

CONFIG_FILE = Path("api_keys.json")  # Default config file location
CONFIG_FILE_ALT = Path.home() / ".hydrogen_intel" / "api_keys.json"  # Alternative in home dir

def load_api_keys() -> Dict[str, str]:
    """
    Load API keys from config file or environment variables.
    
    Priority:
    1. api_keys.json in current directory
    2. ~/.hydrogen_intel/api_keys.json
    3. Environment variables
    
    Config file format (api_keys.json):
    {
        "EIA_API_KEY": "your_eia_key_here",
        "NREL_API_KEY": "your_nrel_key_here"
    }
    """
    keys = {
        "EIA_API_KEY": "",
        "NREL_API_KEY": "",
    }
    
    # Try loading from config files
    config_paths = [CONFIG_FILE, CONFIG_FILE_ALT]
    
    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    file_keys = json.load(f)
                    for key_name in keys:
                        if key_name in file_keys and file_keys[key_name]:
                            keys[key_name] = file_keys[key_name]
                    logging.info(f"Loaded API keys from: {config_path}")
                    break
            except Exception as e:
                logging.warning(f"Could not load {config_path}: {e}")
    
    # Environment variables override config file
    for key_name in keys:
        env_value = os.environ.get(key_name, "")
        if env_value:
            keys[key_name] = env_value
    
    return keys


def create_config_template():
    """Create a template config file for API keys"""
    template = {
        "EIA_API_KEY": "YOUR_EIA_KEY_HERE",
        "NREL_API_KEY": "YOUR_NREL_KEY_HERE",
        "_comment": "Get your free API keys from:",
        "_eia_signup": "https://www.eia.gov/opendata/register.php",
        "_nrel_signup": "https://developer.nrel.gov/signup/"
    }
    
    config_path = CONFIG_FILE
    
    if config_path.exists():
        print(f"Config file already exists: {config_path}")
        return
    
    with open(config_path, 'w') as f:
        json.dump(template, f, indent=2)
    
    print(f"Created config template: {config_path}")
    print("Edit this file to add your API keys.")
    print("\n⚠️  Add 'api_keys.json' to your .gitignore to keep keys private!")


# Load API keys at module import
_API_KEYS = load_api_keys()
EIA_API_KEY = _API_KEYS.get("EIA_API_KEY", "")
NREL_API_KEY = _API_KEYS.get("NREL_API_KEY", "")

# ============================================================================
# USER CONFIG
# ============================================================================

KEYWORDS = [
    # Hydrogen specific
    "hydrogen", "green hydrogen", "blue hydrogen", "clean hydrogen",
    "electrolyzer", "electrolysis", "PEM", "alkaline", "SOEC",
    "LCOH", "levelized cost",
    "ammonia", "green ammonia",
    "fuel cell", "FCEV",
    
    # Renewable energy
    "renewable", "wind", "solar", "photovoltaic",
    "offshore wind", "onshore wind",
    "capacity factor", "curtailment",
    
    # Power/Grid
    "electricity price", "wholesale", "PPA", "power purchase",
    "grid", "transmission", "interconnection",
    
    # Economics/Policy
    "subsidy", "tax credit", "45V", "IRA", "incentive",
    "CAPEX", "OPEX", "NPV", "IRR",
    
    # Regions
    "Oman", "Duqm", "Chile", "Magallanes", "NEOM", "Saudi",
    "Gulf", "Middle East", "MENA",
    
    # Gas (for blue hydrogen context)
    "natural gas", "henry hub", "LNG",
    
    # Storage/Transport
    "hydrogen storage", "pipeline", "liquefaction",
]

START_DATE = "2021-01-01"
END_DATE = "2025-12-31"

MAX_ITEMS_PER_SOURCE = 500
MAX_DOWNLOADS_PER_RUN = 300
REQUEST_SLEEP_SECONDS = 0.5
HTTP_TIMEOUT = 60
REFRESH_STALE_AFTER_DAYS = 3

TOOL_VERSION = "2.0.0"

# ============================================================================
# SOURCE DEFINITIONS
# ============================================================================

@dataclass
class SourceConfig:
    """Configuration for a data source"""
    name: str
    enabled: bool = True
    requires_api_key: bool = False
    api_key_env_var: str = ""
    rss_feeds: Dict[str, str] = field(default_factory=dict)
    index_pages: Dict[str, str] = field(default_factory=dict)
    api_endpoints: Dict[str, str] = field(default_factory=dict)
    allowed_domains: List[str] = field(default_factory=list)
    description: str = ""


SOURCES: Dict[str, SourceConfig] = {
    
    # =========================================================================
    # EIA - US Energy Information Administration
    # =========================================================================
    "eia": SourceConfig(
        name="EIA",
        description="US Energy Information Administration - Official US energy data and analysis",
        requires_api_key=True,
        api_key_env_var="EIA_API_KEY",
        allowed_domains=["eia.gov"],
        rss_feeds={
            "today_in_energy": "https://www.eia.gov/rss/todayinenergy.xml",
            "whats_new": "https://www.eia.gov/rss/whatsnew.xml",
            "press_releases": "https://www.eia.gov/rss/press_rss.xml",
            "petroleum": "https://www.eia.gov/rss/petroleum_rss.xml",
            "natural_gas": "https://www.eia.gov/rss/ng_rss.xml",
            "electricity": "https://www.eia.gov/rss/electricity_rss.xml",
            "renewable": "https://www.eia.gov/rss/renewable_rss.xml",
        },
        index_pages={
            "steo_report": "https://www.eia.gov/outlooks/steo/report/",
            "steo_archives": "https://www.eia.gov/outlooks/steo/outlook.php",
            "steo_data": "https://www.eia.gov/outlooks/steo/data.php",
            "aeo_index": "https://www.eia.gov/outlooks/aeo/",
            "aeo_tables": "https://www.eia.gov/outlooks/aeo/tables_ref.php",
            "ieo_index": "https://www.eia.gov/outlooks/ieo/",
            "hydrogen": "https://www.eia.gov/energyexplained/hydrogen/",
            "electricity_data": "https://www.eia.gov/electricity/data.php",
            "renewable_data": "https://www.eia.gov/renewable/data.php",
        },
        api_endpoints={
            # EIA API v2 endpoints - these need API key
            "electricity_retail_sales": "https://api.eia.gov/v2/electricity/retail-sales/data/",
            "electricity_generation": "https://api.eia.gov/v2/electricity/electric-power-operational-data/data/",
            "natural_gas_prices": "https://api.eia.gov/v2/natural-gas/pri/sum/data/",
            "steo_data": "https://api.eia.gov/v2/steo/data/",
        },
    ),
    
    # =========================================================================
    # IEA - International Energy Agency
    # =========================================================================
    "iea": SourceConfig(
        name="IEA",
        description="International Energy Agency - Global energy analysis and policy",
        allowed_domains=["iea.org"],
        rss_feeds={
            "news": "https://www.iea.org/rss/news.xml",
        },
        index_pages={
            "hydrogen_reports": "https://www.iea.org/reports?topic=hydrogen",
            "hydrogen_fuels": "https://www.iea.org/fuels-and-technologies/hydrogen",
            "renewables": "https://www.iea.org/fuels-and-technologies/renewables",
            "electricity": "https://www.iea.org/fuels-and-technologies/electricity",
            "all_reports": "https://www.iea.org/reports",
            "data_tools": "https://www.iea.org/data-and-statistics",
            "weo": "https://www.iea.org/reports/world-energy-outlook-2024",
            "global_hydrogen_review": "https://www.iea.org/reports/global-hydrogen-review-2024",
            "net_zero": "https://www.iea.org/reports/net-zero-by-2050",
        },
    ),
    
    # =========================================================================
    # IRENA - International Renewable Energy Agency
    # =========================================================================
    "irena": SourceConfig(
        name="IRENA",
        description="International Renewable Energy Agency - Renewable energy data and analysis",
        allowed_domains=["irena.org"],
        rss_feeds={
            "news": "https://www.irena.org/rss/news",
        },
        index_pages={
            "publications": "https://www.irena.org/Publications",
            "hydrogen": "https://www.irena.org/Energy-Transition/Technology/Hydrogen",
            "statistics": "https://www.irena.org/Statistics",
            "costs": "https://www.irena.org/costs",
            "data_downloads": "https://www.irena.org/Data/Downloads",
            "renewable_capacity": "https://www.irena.org/Statistics/View-Data-by-Topic/Capacity-and-Generation/Country-Rankings",
            "green_hydrogen_cost": "https://www.irena.org/publications/2020/Dec/Green-hydrogen-cost-reduction",
        },
    ),
    
    # =========================================================================
    # DOE - US Department of Energy
    # =========================================================================
    "doe": SourceConfig(
        name="DOE",
        description="US Department of Energy - Hydrogen and Fuel Cell Technologies Office",
        allowed_domains=["energy.gov", "hydrogen.energy.gov"],
        rss_feeds={
            "news": "https://www.energy.gov/rss/articles.xml",
        },
        index_pages={
            "hydrogen_program": "https://www.energy.gov/eere/fuelcells/hydrogen-and-fuel-cell-technologies-office",
            "h2_production": "https://www.energy.gov/eere/fuelcells/hydrogen-production",
            "h2_storage": "https://www.energy.gov/eere/fuelcells/hydrogen-storage",
            "h2_hubs": "https://www.energy.gov/oced/regional-clean-hydrogen-hubs",
            "loan_programs": "https://www.energy.gov/lpo/loan-programs-office",
            "h2_shot": "https://www.energy.gov/eere/fuelcells/hydrogen-shot",
            "doe_hydrogen": "https://www.hydrogen.energy.gov/",
        },
    ),
    
    # =========================================================================
    # NREL - National Renewable Energy Laboratory
    # =========================================================================
    "nrel": SourceConfig(
        name="NREL",
        description="National Renewable Energy Laboratory - Research and technical analysis",
        requires_api_key=False,  # API key optional, enhances access
        api_key_env_var="NREL_API_KEY",
        allowed_domains=["nrel.gov"],
        rss_feeds={
            "news": "https://www.nrel.gov/news/rss.xml",
        },
        index_pages={
            "hydrogen": "https://www.nrel.gov/hydrogen/",
            "h2_production": "https://www.nrel.gov/hydrogen/production.html",
            "h2_analysis": "https://www.nrel.gov/hydrogen/systems-analysis.html",
            "publications": "https://www.nrel.gov/publications/",
            "data": "https://www.nrel.gov/research/data-tools.html",
            "atb": "https://atb.nrel.gov/",
            "h2a": "https://www.nrel.gov/hydrogen/h2a-production-models.html",
        },
        api_endpoints={
            # NREL Developer APIs (key optional but recommended)
            "utility_rates": "https://api.nrel.gov/utility_rates/v3.json",
            "solar_resource": "https://api.nrel.gov/solar/solar_resource/v1.json",
        },
    ),
    
    # =========================================================================
    # Hydrogen Council
    # =========================================================================
    "hydrogen_council": SourceConfig(
        name="Hydrogen Council",
        description="Global CEO-led initiative for hydrogen",
        allowed_domains=["hydrogencouncil.com"],
        rss_feeds={},
        index_pages={
            "insights": "https://hydrogencouncil.com/en/insights/",
            "reports": "https://hydrogencouncil.com/en/reports/",
            "news": "https://hydrogencouncil.com/en/news/",
            "hydrogen_insights": "https://hydrogencouncil.com/en/hydrogen-insights-2024/",
        },
    ),
    
    # =========================================================================
    # Ammonia Energy Association
    # =========================================================================
    "ammonia_energy": SourceConfig(
        name="Ammonia Energy",
        description="Ammonia Energy Association - Ammonia as energy carrier",
        allowed_domains=["ammoniaenergy.org"],
        rss_feeds={
            "main": "https://ammoniaenergy.org/feed/",
        },
        index_pages={
            "articles": "https://ammoniaenergy.org/articles/",
            "papers": "https://ammoniaenergy.org/papers/",
            "events": "https://ammoniaenergy.org/events/",
        },
    ),
    
    # =========================================================================
    # Hydrogen News Sites
    # =========================================================================
    "h2_news": SourceConfig(
        name="Hydrogen News",
        description="Hydrogen industry news aggregators",
        allowed_domains=["hydrogeninsight.com", "h2-view.com", "fuelcellsworks.com", 
                        "rechargenews.com", "hydrogenfuelnews.com"],
        rss_feeds={
            "hydrogen_insight": "https://www.hydrogeninsight.com/rss",
            "h2_view": "https://www.h2-view.com/feed/",
            "fuelcellsworks": "https://fuelcellsworks.com/feed/",
            "recharge": "https://www.rechargenews.com/rss",
            "h2_fuel_news": "https://www.hydrogenfuelnews.com/feed/",
        },
        index_pages={
            "hydrogen_insight_news": "https://www.hydrogeninsight.com/",
            "h2_view_news": "https://www.h2-view.com/",
        },
    ),
    
    # =========================================================================
    # S&P Global / Platts (Public content only)
    # =========================================================================
    "spglobal": SourceConfig(
        name="S&P Global",
        description="S&P Global Commodity Insights - Public hydrogen content",
        allowed_domains=["spglobal.com"],
        rss_feeds={},
        index_pages={
            "hydrogen": "https://www.spglobal.com/commodityinsights/en/market-insights/topics/hydrogen",
            "energy_transition": "https://www.spglobal.com/commodityinsights/en/market-insights/topics/energy-transition",
        },
    ),
    
    # =========================================================================
    # Regional: Oman
    # =========================================================================
    "oman": SourceConfig(
        name="Oman Energy",
        description="Oman hydrogen and energy sources",
        allowed_domains=["hydrom.om", "oq.com", "mep.gov.om", "ncsi.gov.om", 
                        "omanobserver.om", "timesofoman.com"],
        rss_feeds={},
        index_pages={
            "hydrom": "https://hydrom.om/",
            "hydrom_projects": "https://hydrom.om/projects/",
            "oq_hydrogen": "https://www.oq.com/en/our-business/alternative-energy/hydrogen",
            "mep": "https://www.mep.gov.om/",
        },
    ),
    
    # =========================================================================
    # Regional: Chile
    # =========================================================================
    "chile": SourceConfig(
        name="Chile Energy",
        description="Chile hydrogen and energy sources",
        allowed_domains=["energia.gob.cl", "h2chile.cl", "corfo.cl", "generadoras.cl"],
        rss_feeds={},
        index_pages={
            "estrategia_h2": "https://energia.gob.cl/hidrogeno-verde",
            "h2chile": "https://h2chile.cl/",
            "corfo_h2": "https://www.corfo.cl/sites/cpp/hidrogeno-verde",
        },
    ),
    
    # =========================================================================
    # Regional: EU
    # =========================================================================
    "eu": SourceConfig(
        name="EU Energy",
        description="European Union hydrogen strategy and policy",
        allowed_domains=["ec.europa.eu", "clean-hydrogen.europa.eu", "europa.eu"],
        rss_feeds={},
        index_pages={
            "eu_hydrogen": "https://energy.ec.europa.eu/topics/energy-systems-integration/hydrogen_en",
            "clean_hydrogen_jti": "https://www.clean-hydrogen.europa.eu/",
            "repowereu": "https://commission.europa.eu/strategy-and-policy/priorities-2019-2024/european-green-deal/repowereu-affordable-secure-and-sustainable-energy-europe_en",
            "eu_hydrogen_bank": "https://energy.ec.europa.eu/topics/energy-systems-integration/hydrogen/european-hydrogen-bank_en",
        },
    ),
    
    # =========================================================================
    # World Bank / IFC
    # =========================================================================
    "worldbank": SourceConfig(
        name="World Bank",
        description="World Bank and IFC energy/hydrogen reports",
        allowed_domains=["worldbank.org", "ifc.org"],
        rss_feeds={
            "wb_news": "https://www.worldbank.org/en/news/rss.xml",
        },
        index_pages={
            "wb_energy": "https://www.worldbank.org/en/topic/energy",
            "wb_hydrogen": "https://www.worldbank.org/en/topic/energy/brief/hydrogen",
            "ifc_infra": "https://www.ifc.org/en/what-we-do/sector-expertise/infrastructure",
        },
    ),
    
    # =========================================================================
    # Lazard (Public reports)
    # =========================================================================
    "lazard": SourceConfig(
        name="Lazard",
        description="Lazard levelized cost analyses",
        allowed_domains=["lazard.com"],
        rss_feeds={},
        index_pages={
            "lcoe": "https://www.lazard.com/research-insights/levelized-cost-of-energyplus/",
            "lcoh": "https://www.lazard.com/research-insights/levelized-cost-of-hydrogen/",
        },
    ),
}


# ============================================================================
# PATHS
# ============================================================================

DATA_DIR = Path("data")
RAW_DIR = DATA_DIR / "raw"
CURATED_DIR = DATA_DIR / "curated" / "energy_intel"
STATE_DIR = Path("state")
LOG_DIR = Path("logs")

STATE_PATH = STATE_DIR / "energy_intel_state.json"
PLAN_PATH = STATE_DIR / "energy_intel_plan.json"
LEARNINGS_JSONL = CURATED_DIR / "learnings.jsonl"
LOG_PATH = LOG_DIR / "energy_intel_collector.log"


# ============================================================================
# LOGGING
# ============================================================================

def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logging.getLogger("").addHandler(console)


# ============================================================================
# HELPERS
# ============================================================================

def pace() -> None:
    if REQUEST_SLEEP_SECONDS > 0:
        time.sleep(REQUEST_SLEEP_SECONDS)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_date_guess(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    fmts = [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%d",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d %B %Y",
        "%d/%m/%Y",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass
    return None


def in_window(dt: datetime, start: datetime, end: datetime) -> bool:
    return start <= dt <= end


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def safe_slug(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:180] if len(s) > 180 else s


def domain_allowed(url: str, allowed_domains: List[str]) -> bool:
    try:
        host = urlparse(url).netloc.lower()
        return any(host.endswith(d) or host == d for d in allowed_domains)
    except Exception:
        return False


def keyword_score(text: str, keywords: List[str]) -> float:
    t = (text or "").lower()
    score = 0.0
    for kw in keywords:
        kw = kw.lower().strip()
        if not kw:
            continue
        if kw in t:
            score += 5.0
        tokens = kw.split()
        for tok in tokens:
            score += len(re.findall(rf"\b{re.escape(tok)}\b", t)) * 2.0
    return score


def load_state() -> Dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        return {
            "tool_version": TOOL_VERSION,
            "last_run_utc": None,
            "seen": {},
        }
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def http_get(url: str, headers: Optional[dict] = None, params: Optional[dict] = None, 
             max_retries: int = 4) -> requests.Response:
    default_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    if headers:
        default_headers.update(headers)
    
    backoff = 2.0
    last_exc = None
    
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=default_headers, params=params, timeout=HTTP_TIMEOUT)
            if r.status_code in (429, 503, 502):
                time.sleep(backoff)
                backoff *= 2
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt == max_retries - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
    
    raise RuntimeError(f"HTTP failed: {last_exc}")


def write_raw(source_name: str, url_slug: str, filename: str, content: bytes) -> Path:
    dt = datetime.now(timezone.utc)
    out_dir = RAW_DIR / source_name / url_slug / f"{dt:%Y}" / f"{dt:%m}"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / filename
    p.write_bytes(content)
    return p


def guess_ext(content_type: str, url: str) -> str:
    url_lower = url.lower()
    if url_lower.endswith(".pdf"):
        return ".pdf"
    if url_lower.endswith(".xlsx"):
        return ".xlsx"
    if url_lower.endswith(".xls"):
        return ".xls"
    if url_lower.endswith(".csv"):
        return ".csv"
    if url_lower.endswith(".zip"):
        return ".zip"
    
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return ".pdf"
    if "excel" in ct or "spreadsheet" in ct:
        return ".xlsx"
    if "csv" in ct:
        return ".csv"
    if "zip" in ct:
        return ".zip"
    if "xml" in ct:
        return ".xml"
    if "json" in ct:
        return ".json"
    return ".bin"


def extract_text_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    txt = soup.get_text("\n", strip=True)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def try_extract_text_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    try:
        import io
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for page in reader.pages[:80]:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text if len(text) > 100 else None
    except Exception:
        return None


def is_downloadable(url: str) -> bool:
    u = url.lower()
    return any(u.endswith(ext) for ext in (".pdf", ".xlsx", ".xls", ".csv", ".zip", ".pptx"))


# ============================================================================
# API-SPECIFIC FUNCTIONS
# ============================================================================

def fetch_eia_api_data(endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
    """
    Fetch data from EIA API v2.
    Requires EIA_API_KEY environment variable.
    """
    if not EIA_API_KEY:
        logging.warning("[EIA API] No API key set. Set EIA_API_KEY environment variable.")
        return None
    
    params["api_key"] = EIA_API_KEY
    
    try:
        r = http_get(endpoint, params=params)
        data = r.json()
        
        if "response" in data:
            return data["response"]
        return data
    except Exception as e:
        logging.error(f"[EIA API] Failed: {e}")
        return None


def fetch_nrel_api_data(endpoint: str, params: Dict[str, Any]) -> Optional[Dict]:
    """
    Fetch data from NREL API.
    API key optional but recommended.
    """
    if NREL_API_KEY:
        params["api_key"] = NREL_API_KEY
    
    try:
        r = http_get(endpoint, params=params)
        return r.json()
    except Exception as e:
        logging.error(f"[NREL API] Failed: {e}")
        return None


def discover_from_eia_api(state: Dict, start_dt: datetime, end_dt: datetime) -> List['Candidate']:
    """
    Discover data from EIA API endpoints.
    Returns candidates with API data.
    """
    candidates = []
    
    if not EIA_API_KEY:
        logging.info("[EIA API] Skipping API endpoints (no API key)")
        return candidates
    
    logging.info("[EIA API] Fetching from API endpoints...")
    
    # Example: Get STEO data (Short-Term Energy Outlook)
    steo_params = {
        "frequency": "monthly",
        "data[0]": "value",
        "facets[seriesId][]": ["STEO.PAPR_WORLD.M", "STEO.NGPRPUS.M"],  # Oil price, gas price
        "start": start_dt.strftime("%Y-%m"),
        "end": end_dt.strftime("%Y-%m"),
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
        "length": 100,
    }
    
    try:
        data = fetch_eia_api_data(SOURCES["eia"].api_endpoints["steo_data"], steo_params)
        if data and "data" in data:
            # Save as learning
            append_jsonl(LEARNINGS_JSONL, {
                "run_utc": state.get("last_run_utc"),
                "source": "EIA",
                "source_key": "eia",
                "url": "eia_api:steo_data",
                "kind": "api_data",
                "title": "EIA STEO Data",
                "text": json.dumps(data["data"][:50], indent=2),
                "score": 10.0,
            })
            logging.info(f"[EIA API] Fetched STEO data: {len(data.get('data', []))} records")
    except Exception as e:
        logging.error(f"[EIA API] STEO fetch failed: {e}")
    
    return candidates


# ============================================================================
# RSS PARSING
# ============================================================================

def parse_rss_items(xml_bytes: bytes) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(xml_bytes, "xml")
    items = []
    
    # Try RSS format
    for it in soup.find_all("item"):
        title = (it.title.text.strip() if it.title else "")
        link = (it.link.text.strip() if it.link else "")
        pub = (it.pubDate.text.strip() if it.pubDate else "")
        desc = (it.description.text.strip() if it.description else "")
        items.append({"title": title, "link": link, "pubdate": pub, "summary": desc})
    
    # Try Atom format if no items found
    if not items:
        for entry in soup.find_all("entry"):
            title = (entry.title.text.strip() if entry.title else "")
            link_el = entry.find("link")
            link = link_el.get("href", "") if link_el else ""
            pub = ""
            if entry.published:
                pub = entry.published.text.strip()
            elif entry.updated:
                pub = entry.updated.text.strip()
            summary = (entry.summary.text.strip() if entry.summary else "")
            items.append({"title": title, "link": link, "pubdate": pub, "summary": summary})
    
    return items


# ============================================================================
# DISCOVERY
# ============================================================================

@dataclass
class Candidate:
    url: str
    kind: str
    source: str
    source_key: str
    title: str = ""
    summary: str = ""
    published_utc: Optional[str] = None
    score: float = 0.0


def discover_from_rss(
    source_key: str,
    source_name: str,
    feed_name: str,
    rss_url: str,
    allowed_domains: List[str],
    start_dt: datetime,
    end_dt: datetime
) -> List[Candidate]:
    logging.info(f"[RSS] {source_name}/{feed_name}: {rss_url}")
    
    try:
        r = http_get(rss_url)
        pace()
    except Exception as e:
        logging.warning(f"[RSS] Failed to fetch {rss_url}: {e}")
        return []
    
    items = parse_rss_items(r.content)
    out: List[Candidate] = []
    
    for it in items[:MAX_ITEMS_PER_SOURCE]:
        link = it.get("link", "").strip()
        if not link:
            continue
        
        if allowed_domains and not domain_allowed(link, allowed_domains):
            continue
        
        dt = parse_date_guess(it.get("pubdate", ""))
        if dt and not in_window(dt, start_dt, end_dt):
            continue
        
        text_blob = f"{it.get('title', '')} {it.get('summary', '')}"
        sc = keyword_score(text_blob, KEYWORDS)
        
        if sc < 1.0:
            continue
        
        out.append(Candidate(
            url=link,
            kind="file" if is_downloadable(link) else "html",
            source=source_name,
            source_key=source_key,
            title=it.get("title", ""),
            summary=it.get("summary", "")[:500],
            published_utc=(dt.isoformat() if dt else None),
            score=sc,
        ))
    
    logging.info(f"[RSS] {source_name}/{feed_name}: {len(out)} candidates")
    return out


def discover_from_index_page(
    source_key: str,
    source_name: str,
    page_name: str,
    page_url: str,
    allowed_domains: List[str],
    start_dt: datetime,
    end_dt: datetime
) -> List[Candidate]:
    logging.info(f"[INDEX] {source_name}/{page_name}: {page_url}")
    
    try:
        r = http_get(page_url)
        pace()
    except Exception as e:
        logging.warning(f"[INDEX] Failed to fetch {page_url}: {e}")
        return []
    
    html = r.text
    soup = BeautifulSoup(html, "html.parser")
    out: List[Candidate] = []
    
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        
        url = urljoin(page_url, href)
        
        if allowed_domains and not domain_allowed(url, allowed_domains):
            continue
        
        anchor_text = a.get_text(" ", strip=True) or ""
        text_blob = f"{anchor_text} {url}"
        sc = keyword_score(text_blob, KEYWORDS)
        
        if is_downloadable(url):
            if sc >= 0.5:
                out.append(Candidate(
                    url=url, kind="file", source=source_name, source_key=source_key,
                    title=anchor_text[:200], summary="", published_utc=None, score=sc,
                ))
        else:
            if sc >= 2.0:
                out.append(Candidate(
                    url=url, kind="html", source=source_name, source_key=source_key,
                    title=anchor_text[:200], summary="", published_utc=None, score=sc,
                ))
    
    # Dedupe
    uniq: Dict[str, Candidate] = {}
    for c in out:
        if c.url not in uniq or c.score > uniq[c.url].score:
            uniq[c.url] = c
    
    result = sorted(uniq.values(), key=lambda x: x.score, reverse=True)[:MAX_ITEMS_PER_SOURCE]
    logging.info(f"[INDEX] {source_name}/{page_name}: {len(result)} candidates")
    return result


def extract_downloads_from_html(page_url: str, html: str, allowed_domains: List[str]) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    downloads: Set[str] = set()
    
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        url = urljoin(page_url, href)
        
        if allowed_domains and not domain_allowed(url, allowed_domains):
            continue
        
        if is_downloadable(url):
            downloads.add(url)
    
    return sorted(downloads)


# ============================================================================
# INCREMENTAL FETCH
# ============================================================================

def is_stale(seen_rec: Dict[str, Any]) -> bool:
    try:
        last = seen_rec.get("last_checked_utc")
        if not last:
            return True
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
        return age_days > REFRESH_STALE_AFTER_DAYS
    except Exception:
        return True


def mark_seen(state: Dict[str, Any], url: str, kind: str, content_type: str, sha: str, source: str) -> None:
    seen = state.setdefault("seen", {})
    rec = seen.get(url) or {}
    if not rec.get("first_seen_utc"):
        rec["first_seen_utc"] = utcnow_iso()
    rec["last_checked_utc"] = utcnow_iso()
    rec["kind"] = kind
    rec["content_type"] = content_type
    rec["sha256"] = sha
    rec["source"] = source
    seen[url] = rec


def fetch_and_store_candidate(
    state: Dict[str, Any],
    cand: Candidate,
    allowed_domains: List[str],
    downloads_counter: List[int]
) -> List[str]:
    url = cand.url
    seen = state.get("seen", {}).get(url)
    
    if seen and not is_stale(seen):
        logging.debug(f"[SKIP] fresh: {url}")
        return []
    
    try:
        r = http_get(url)
        pace()
    except Exception as e:
        logging.warning(f"[FETCH] Failed {url}: {e}")
        return []
    
    content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    body = r.content
    sha = sha256_bytes(body)
    
    if seen and seen.get("sha256") == sha:
        mark_seen(state, url, cand.kind, content_type, sha, cand.source)
        logging.debug(f"[UNCHANGED] {url}")
        return []
    
    slug = safe_slug(urlparse(url).path.strip("/")) or safe_slug(urlparse(url).netloc)
    ext = guess_ext(content_type, url)
    new_downloads: List[str] = []
    
    # Handle file downloads
    if cand.kind == "file" or is_downloadable(url) or content_type in ("application/pdf", "application/zip"):
        if downloads_counter[0] >= MAX_DOWNLOADS_PER_RUN:
            logging.warning("[DL] Hit download cap")
            return []
        
        fname = f"{safe_slug(cand.source_key)}_{slug}{ext}"
        p = write_raw(cand.source_key, slug, fname, body)
        downloads_counter[0] += 1
        logging.info(f"[DL] {p}")
        
        text = None
        if ext == ".pdf" or "pdf" in content_type:
            text = try_extract_text_from_pdf(body)
        
        if text:
            append_jsonl(LEARNINGS_JSONL, {
                "run_utc": state.get("last_run_utc"),
                "source": cand.source,
                "source_key": cand.source_key,
                "url": url,
                "kind": "pdf_text",
                "title": cand.title,
                "published_utc": cand.published_utc,
                "score": cand.score,
                "text": text[:300000],
            })
        
        mark_seen(state, url, "file", content_type, sha, cand.source)
        return []
    
    # Handle HTML pages
    html = body.decode("utf-8", errors="replace")
    fname = f"{safe_slug(cand.source_key)}_{slug}.html"
    p = write_raw(cand.source_key, slug, fname, body)
    logging.info(f"[PAGE] {p}")
    
    text = extract_text_from_html(html)
    if text and len(text) > 200:
        append_jsonl(LEARNINGS_JSONL, {
            "run_utc": state.get("last_run_utc"),
            "source": cand.source,
            "source_key": cand.source_key,
            "url": url,
            "kind": "html_text",
            "title": cand.title,
            "published_utc": cand.published_utc,
            "score": cand.score,
            "text": text[:300000],
        })
    
    for dl in extract_downloads_from_html(url, html, allowed_domains):
        new_downloads.append(dl)
    
    mark_seen(state, url, "html", content_type, sha, cand.source)
    return new_downloads


# ============================================================================
# MAIN
# ============================================================================

def check_api_keys():
    """Check and display API key status"""
    print("\n" + "=" * 60)
    print("API KEY STATUS")
    print("=" * 60)
    
    # Check config file locations
    print("\nConfig file locations checked:")
    for path in [CONFIG_FILE, CONFIG_FILE_ALT]:
        status = "✓ Found" if path.exists() else "✗ Not found"
        print(f"  {status}: {path}")
    
    print("\nAPI Keys:")
    keys = [
        ("EIA_API_KEY", EIA_API_KEY, "https://www.eia.gov/opendata/register.php"),
        ("NREL_API_KEY", NREL_API_KEY, "https://developer.nrel.gov/signup/"),
    ]
    
    for name, value, signup_url in keys:
        if value:
            masked = value[:4] + "..." + value[-4:] if len(value) > 8 else "***"
            print(f"  ✓ {name}: Set ({masked})")
        else:
            print(f"  ✗ {name}: Not set")
            print(f"    Get your free key: {signup_url}")
    
    print("\n" + "-" * 60)
    print("To set up API keys, choose one method:\n")
    print("Method 1 - Config file (recommended):")
    print("  python API_discovery.py --create-config")
    print("  # Then edit api_keys.json with your keys\n")
    print("Method 2 - Environment variables:")
    print('  export EIA_API_KEY="your_key_here"')
    print('  export NREL_API_KEY="your_key_here"')
    print("=" * 60 + "\n")


def list_sources():
    """List available sources"""
    print("\n" + "=" * 60)
    print("AVAILABLE SOURCES")
    print("=" * 60)
    
    for key, cfg in SOURCES.items():
        status = "✓" if cfg.enabled else "○"
        api_note = " [API KEY]" if cfg.requires_api_key else ""
        feeds = len(cfg.rss_feeds)
        pages = len(cfg.index_pages)
        apis = len(cfg.api_endpoints)
        
        print(f"\n  {status} {key}{api_note}")
        print(f"    {cfg.name}: {cfg.description}")
        print(f"    RSS: {feeds} | Pages: {pages} | APIs: {apis}")
        print(f"    Domains: {', '.join(cfg.allowed_domains[:3])}")
    
    print("\n" + "=" * 60 + "\n")


def run_collection(source_keys: Optional[List[str]] = None):
    """Main collection routine"""
    setup_logging()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CURATED_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    
    state = load_state()
    state["tool_version"] = TOOL_VERSION
    state["last_run_utc"] = utcnow_iso()
    
    start_dt = datetime.fromisoformat(START_DATE).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(END_DATE).replace(tzinfo=timezone.utc)
    
    # Determine sources to run
    if source_keys:
        sources_to_run = {k: v for k, v in SOURCES.items() if k in source_keys and v.enabled}
    else:
        sources_to_run = {k: v for k, v in SOURCES.items() if v.enabled}
    
    logging.info("=" * 60)
    logging.info(f"Energy Intelligence Collector v{TOOL_VERSION}")
    logging.info(f"Sources: {list(sources_to_run.keys())}")
    logging.info(f"Window: {START_DATE} to {END_DATE}")
    logging.info(f"EIA API Key: {'Set' if EIA_API_KEY else 'Not set'}")
    logging.info("=" * 60)
    
    # Discover candidates
    all_candidates: List[Candidate] = []
    
    for source_key, cfg in sources_to_run.items():
        logging.info(f"\n[SOURCE] {cfg.name} ({source_key})")
        
        # RSS feeds
        for feed_name, feed_url in cfg.rss_feeds.items():
            try:
                candidates = discover_from_rss(
                    source_key, cfg.name, feed_name, feed_url,
                    cfg.allowed_domains, start_dt, end_dt
                )
                all_candidates.extend(candidates)
            except Exception as e:
                logging.exception(f"[RSS] {feed_name} failed: {e}")
        
        # Index pages
        for page_name, page_url in cfg.index_pages.items():
            try:
                candidates = discover_from_index_page(
                    source_key, cfg.name, page_name, page_url,
                    cfg.allowed_domains, start_dt, end_dt
                )
                all_candidates.extend(candidates)
            except Exception as e:
                logging.exception(f"[INDEX] {page_name} failed: {e}")
        
        # API endpoints (EIA)
        if source_key == "eia" and cfg.api_endpoints:
            discover_from_eia_api(state, start_dt, end_dt)
    
    # Dedupe
    best: Dict[str, Candidate] = {}
    for c in all_candidates:
        if c.url not in best or c.score > best[c.url].score:
            best[c.url] = c
    
    candidates = sorted(best.values(), key=lambda x: x.score, reverse=True)
    logging.info(f"\n[PLAN] Total candidates: {len(candidates)}")
    
    # Save plan
    plan = {
        "tool_version": TOOL_VERSION,
        "created_utc": utcnow_iso(),
        "sources": list(sources_to_run.keys()),
        "candidate_count": len(candidates),
        "top_50": [
            {"url": c.url, "source": c.source, "score": c.score, "title": c.title[:100]}
            for c in candidates[:50]
        ],
    }
    PLAN_PATH.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    
    # Fetch and store
    downloads_counter = [0]
    queue = candidates[:]
    queued: Set[str] = {c.url for c in queue}
    processed = 0
    
    while queue:
        cand = queue.pop(0)
        processed += 1
        
        source_cfg = SOURCES.get(cand.source_key)
        allowed_domains = source_cfg.allowed_domains if source_cfg else []
        
        try:
            new_downloads = fetch_and_store_candidate(state, cand, allowed_domains, downloads_counter)
            save_state(state)
            
            for dl in new_downloads:
                if downloads_counter[0] >= MAX_DOWNLOADS_PER_RUN:
                    break
                if dl in queued:
                    continue
                sc = keyword_score(dl, KEYWORDS)
                queue.append(Candidate(
                    url=dl, kind="file", source=cand.source, source_key=cand.source_key,
                    title="(embedded)", summary="", published_utc=cand.published_utc,
                    score=max(sc, cand.score * 0.5),
                ))
                queued.add(dl)
        
        except Exception as e:
            logging.exception(f"[FETCH] {cand.url}: {e}")
        
        if processed % 25 == 0:
            logging.info(f"[PROGRESS] processed={processed} queue={len(queue)} downloads={downloads_counter[0]}")
        
        if downloads_counter[0] >= MAX_DOWNLOADS_PER_RUN:
            logging.warning("[STOP] Download cap reached")
            break
    
    save_state(state)
    logging.info(f"\n[DONE] Processed {processed} items, downloaded {downloads_counter[0]} files")
    logging.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Energy Intelligence API & Web Collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python API_discovery.py                      # Run all sources
    python API_discovery.py --source eia         # Run EIA only
    python API_discovery.py --source eia iea     # Run EIA and IEA
    python API_discovery.py --list-sources       # List all sources
    python API_discovery.py --check-api-keys     # Check API key status
    python API_discovery.py --create-config      # Create api_keys.json template

API Keys (choose one method):
    
    Method 1 - Config file (recommended):
        python API_discovery.py --create-config
        # Then edit api_keys.json with your keys
    
    Method 2 - Environment variables:
        export EIA_API_KEY="your_key_here"
        export NREL_API_KEY="your_key_here"

Get free API keys:
    EIA:  https://www.eia.gov/opendata/register.php
    NREL: https://developer.nrel.gov/signup/
        """
    )
    
    parser.add_argument("--source", nargs="*", help="Specific source(s) to run")
    parser.add_argument("--list-sources", action="store_true", help="List available sources")
    parser.add_argument("--check-api-keys", action="store_true", help="Check API key configuration")
    parser.add_argument("--create-config", action="store_true", help="Create api_keys.json template")
    
    args = parser.parse_args()
    
    if args.create_config:
        create_config_template()
        return
    
    if args.check_api_keys:
        check_api_keys()
        return
    
    if args.list_sources:
        list_sources()
        return
    
    run_collection(source_keys=args.source)


if __name__ == "__main__":
    main()