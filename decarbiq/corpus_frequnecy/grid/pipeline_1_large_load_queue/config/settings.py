"""
Pipeline 1 Configuration: Large Load Interconnection Queue Monitor
===================================================================
Covers Sections 2A-2D of the ERCOT/Texas Data Collection Guide.

Sources:
  2A - ERCOT Monthly Operational Overview, Board/RPG presentations
  2B - PUCT SB6 dockets (58317, 58479, 58481, 58482, 58484)
  2C - ERCOT Large Load Integration page (process docs, forms)
  2D - Indirect: TSP earnings calls (SEC EDGAR), ERCOT constraints report
"""

from pathlib import Path
from dataclasses import dataclass, field

# ── Directories ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
OUTPUTS_DIR = BASE_DIR / "outputs"
for d in [STORAGE_DIR, OUTPUTS_DIR, STORAGE_DIR / "ercot_docs", STORAGE_DIR / "puc_filings",
          STORAGE_DIR / "sec_filings", STORAGE_DIR / "snapshots"]:
    d.mkdir(parents=True, exist_ok=True)

# ── HTTP Settings ────────────────────────────────────────────────────────────
REQUEST_HEADERS = {
    "User-Agent": "DecarbIQ-Monitor/1.0 (energy research; contact@ecodecarb.com)"
}
REQUEST_TIMEOUT = 30  # seconds
RATE_LIMIT_SECONDS = 2  # pause between requests to same domain

# ══════════════════════════════════════════════════════════════════════════════
# 2A: ERCOT PUBLIC DATA SOURCES
# ══════════════════════════════════════════════════════════════════════════════

# Resource Adequacy page (links to operational overviews + GIS reports)
ERCOT_RESOURCE_ADEQUACY_URL = "https://www.ercot.com/gridinfo/resource"

# Planning page (RTP, constraints report, LTSA)
ERCOT_PLANNING_URL = "https://www.ercot.com/gridinfo/planning"

# Calendar (Board meetings, RPG meetings)
ERCOT_CALENDAR_URL = "https://www.ercot.com/calendar"

# Large Load Integration page
ERCOT_LARGE_LOAD_PAGE = "https://www.ercot.com/services/rq/large-load-integration"

# Known PDF URL patterns for monthly operational overviews
# Actual URLs discovered by scraping the resource adequacy page
ERCOT_FILES_BASE = "https://www.ercot.com/files/docs"

# Board meeting presentation keywords to look for
BOARD_PRESENTATION_KEYWORDS = [
    "System Planning and Weatherization Update",
    "Large Load",
    "load growth",
    "interconnection queue",
    "large load interconnection",
    "demand forecast",
]

# RPG meeting keywords
RPG_KEYWORDS = [
    "RPG Meeting",
    "Regional Planning Group",
    "transmission project",
    "765-kV",
    "STEP",
]

# ══════════════════════════════════════════════════════════════════════════════
# 2B: PUCT SB6 DOCKET MONITORING
# ══════════════════════════════════════════════════════════════════════════════

PUC_INTERCHANGE_BASE = "https://interchange.puc.texas.gov"
PUC_FILING_SEARCH_URL = f"{PUC_INTERCHANGE_BASE}/Search/Filings"
PUC_FILING_BY_CONTROL = f"{PUC_INTERCHANGE_BASE}/Search/Filings?ControlNumber={{}}"

# SB6 implementation dockets to monitor
SB6_DOCKETS = {
    "58317": "SB 6 Implementation (umbrella)",
    "58481": "Large-load interconnection standards",
    "58479": "Net-metering / co-location arrangements",
    "58482": "Large-load reliability / demand reduction",
    "58484": "Transmission cost allocation (4CP review)",
}

# Additional PUC dockets of interest (CCN filings for major transmission)
TRANSMISSION_CCN_DOCKETS = {
    "58561": "Oncor 765-kV Dinosaur-Longshore-Drill Hole",
    "58545": "CPS/AEP 765-kV Howard-Solstice",
}

# ══════════════════════════════════════════════════════════════════════════════
# 2C: ERCOT LARGE LOAD INTEGRATION PAGE
# ══════════════════════════════════════════════════════════════════════════════

# Documents to track for changes (check hashes)
LARGE_LOAD_TRACKED_DOCS = [
    {
        "name": "Large Load Interconnection Process Q&A",
        "url_pattern": "Large-Load-Interconnection-Process-Q-A",
    },
    {
        "name": "Stand-Alone Generation Resources List",
        "url_pattern": "Stand-Alone-Generation-Resources",
    },
]

# ══════════════════════════════════════════════════════════════════════════════
# 2D: INDIRECT TRIANGULATION SOURCES
# ══════════════════════════════════════════════════════════════════════════════

# SEC EDGAR full-text search API for earnings call transcripts / 10-K / 10-Q
SEC_EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
SEC_EDGAR_FILINGS = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_EDGAR_FULLTEXT = "https://efts.sec.gov/LATEST/search-index?q={query}&dateRange=custom&startdt={start}&enddt={end}&forms={forms}"

# TSPs and their SEC CIK numbers
TSP_SEC_ENTITIES = {
    "Sempra": {  # parent of Oncor
        "cik": "0001032208",
        "ticker": "SRE",
        "name": "Sempra",
        "keywords": ["oncor", "large load", "interconnection", "data center",
                      "industrial load", "hydrogen", "ammonia"],
    },
    "CenterPoint": {
        "cik": "0001130310",
        "ticker": "CNP",
        "name": "CenterPoint Energy Inc",
        "keywords": ["large load", "interconnection", "data center",
                      "industrial", "hydrogen", "ship channel"],
    },
    "AEP": {
        "cik": "0000004904",
        "ticker": "AEP",
        "name": "American Electric Power Co Inc",
        "keywords": ["aep texas", "large load", "interconnection",
                      "data center", "industrial", "hydrogen"],
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# RELEVANCE FILTERING — H2 / AMMONIA / CCS KEYWORDS
# ══════════════════════════════════════════════════════════════════════════════

# Primary keywords — direct mentions of target project types
PRIMARY_KEYWORDS = [
    "hydrogen", "blue hydrogen", "green hydrogen",
    "ammonia", "blue ammonia",
    "carbon capture", "CCS", "CCUS", "CO2",
    "SMR", "ATR", "autothermal reform",
    "air separation unit", "ASU",
    "haber-bosch", "haber bosch",
    "low-carbon fuel", "clean fuel",
    "sequestration",
]

# Secondary keywords — infrastructure signals that may relate to H2/ammonia
SECONDARY_KEYWORDS = [
    "large load", "industrial load", "large industrial",
    "Gulf Coast", "ship channel", "freeport", "texas city",
    "port arthur", "beaumont", "corpus christi", "victoria",
    "petrochemical", "refinery", "chemical plant",
    "pipeline", "export terminal",
    "75 MW", "100 MW", "150 MW", "200 MW",
    "interconnection study", "LLIS",
    "energization request",
    "transmission upgrade", "transmission constraint",
]

# Gulf Coast counties most relevant for blue H2/ammonia siting
GULF_COAST_COUNTIES = [
    "Brazoria", "Galveston", "Harris", "Chambers", "Jefferson",
    "Orange", "Nueces", "San Patricio", "Calhoun", "Matagorda",
    "Victoria", "Jackson", "Wharton", "Fort Bend", "Liberty",
    "Aransas", "Kleberg", "Refugio",
]

# Known H2/ammonia developers to watch
KNOWN_DEVELOPERS = [
    "Air Products", "Air Liquide", "Linde",
    "CF Industries", "LSB Industries", "OCI",
    "Plug Power", "Green Hydrogen International",
    "HIF Global", "Nacero",
    "ExxonMobil", "Chevron", "Shell",
    "Sempra Infrastructure", "NextDecade",
    "Freeport LNG",  # adjacent infrastructure
    "Venture Global",  # adjacent infrastructure
    "Denbury",  # CO2 pipelines
    "Talos Energy",  # CCS
    "Oxy", "Occidental",  # CCS
    "NET Power",
    "Jupiter Power",
]


@dataclass
class ScrapeResult:
    """Standard output from any scraper module."""
    source: str  # e.g. "ercot_board", "puc_sb6", "sec_edgar"
    doc_type: str  # e.g. "presentation", "filing", "notice"
    title: str
    url: str
    date: str  # ISO format
    content_hash: str  # SHA-256 of raw content for dedup
    text_excerpt: str = ""  # first ~2000 chars or summary
    relevance_score: float = 0.0  # 0-1, based on keyword matching
    matched_keywords: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)  # source-specific fields
    local_path: str = ""  # path to downloaded file
    is_new: bool = True  # False if we've seen this hash before
