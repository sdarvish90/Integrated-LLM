"""
Pipeline 2 Configuration: Distribution-Level Interconnections Tracker
======================================================================
Covers Section 4 of the ERCOT/Texas Data Collection Guide.

Sources:
  4A - TSP-specific: Oncor, CenterPoint, AEP Texas quarterly disclosures,
       DG interconnection pages, generation POI queue data
  4B - EIA Form 860/860M: planned generators ≥1 MW in Texas
  4C - Interconnection.fyi aggregated queue data
  4D - Oncor quarterly earnings press releases (structured queue data)
"""

from pathlib import Path
from dataclasses import dataclass, field

BASE_DIR = Path(__file__).resolve().parent.parent
STORAGE_DIR = BASE_DIR / "storage"
OUTPUTS_DIR = BASE_DIR / "outputs"
for d in [STORAGE_DIR, OUTPUTS_DIR,
          STORAGE_DIR / "oncor", STORAGE_DIR / "centerpoint",
          STORAGE_DIR / "aep", STORAGE_DIR / "eia", STORAGE_DIR / "snapshots"]:
    d.mkdir(parents=True, exist_ok=True)

REQUEST_HEADERS = {
    "User-Agent": "DecarbIQ-Monitor/1.0 (energy research; contact@ecodecarb.com)"
}
REQUEST_TIMEOUT = 30
RATE_LIMIT_SECONDS = 2

# ══════════════════════════════════════════════════════════════════════════════
# 4A: TSP-SPECIFIC DATA SOURCES
# ══════════════════════════════════════════════════════════════════════════════

TSP_SOURCES = {
    "oncor": {
        "name": "Oncor Electric Delivery",
        "territory": "DFW, Central TX, West TX (Permian Basin)",
        "parent": "Sempra",
        "dg_interconnection_page": "https://www.oncor.com/content/oncorwww/us/en/home/smart-energy/renewables-solar-and-more/energy-system-developers.html",
        "newsroom": "https://www.oncor.com/content/oncorwww/wire/en/home/newsroom.html",
        # Quarterly earnings releases contain structured queue data
        "earnings_urls": [
            "https://www.oncor.com/content/oncorwww/wire/en/home/newsroom/oncor-reports-first-quarter-2025-results.html",
            "https://www.oncor.com/content/oncorwww/wire/en/home/newsroom/oncor-reports-second-quarter-2025-results.html",
            "https://www.oncor.com/content/oncorwww/wire/en/home/newsroom/oncor-reports-third-quarter-2025-results.html",
        ],
        # Key metrics to extract from Oncor
        "queue_fields": {
            "lci_queue_requests": r'active\s+LC&I\s+interconnection\s+queue\s+(?:had|included)\s+(?:over\s+)?(\d[\d,]+)\s+requests',
            "data_center_gw": r'(\d[\d,.]+)\s+gigawatts?\s+from\s+data\s+centers',
            "industrial_gw": r'(?:over\s+)?(\d[\d,.]+)\s+gigawatts?\s+of\s+load\s+from\s+(?:diverse\s+)?(?:various\s+)?(?:other\s+)?industrial',
            "gen_poi_requests": r'(\d[\d,]+)\s+active\s+generation\s+POI\s+requests',
            "gen_storage_pct": r'(\d+)%\s+were\s+storage',
            "gen_solar_pct": r'(\d+)%\s+were\s+solar',
            "gen_wind_pct": r'(\d+)%\s+were\s+wind',
            "gen_gas_pct": r'(\d+)%\s+were\s+gas',
            "circuit_miles": r'(?:built|rebuilt|upgraded)\s+(?:approximately\s+)?(\d[\d,]+)\s+circuit\s+miles',
            "new_premises": r'increased\s+premises\s+by\s+(?:nearly\s+)?(\d[\d,]+)',
            "customer_collateral_billions": r'\$(\d[\d,.]+)\s+billion\s+in\s+customer\s+collateral',
        },
    },
    "centerpoint": {
        "name": "CenterPoint Energy",
        "territory": "Greater Houston area",
        "parent": "CenterPoint Energy Inc",
        "newsroom": "https://investors.centerpointenergy.com/news-releases",
        "queue_fields": {
            "total_queue_gw": r'interconnection\s+requests\s+for\s+(?:about\s+)?(\d[\d,.]+)\s+gigawatts',
            "data_center_gw": r'(\d[\d,.]+)\s+gigawatts?\s+of\s+which\s+are\s+from\s+data\s+centers',
            "current_system_gw": r'(?:system|electricity\s+use)\s+is\s+currently\s+(?:a\s+little\s+more\s+than\s+)?(\d[\d,.]+)\s+gigawatts?',
        },
    },
    "aep_texas": {
        "name": "AEP Texas (North & Central)",
        "territory": "Corpus Christi, Abilene, South/West TX",
        "parent": "American Electric Power Co Inc",
        "newsroom": "https://www.aep.com/newsroom/resources/press-releases",
        "queue_fields": {
            "signed_gw": r'(?:signed\s+agreements?\s+(?:to\s+receive\s+)?(?:service\s+)?(?:requiring\s+)?(?:about\s+)?)?(\d[\d,.]+)\s+gigawatts?\s+(?:of\s+electricity\s+)?(?:have\s+)?signed\s+agreements?',
            "pipeline_gw": r'(\d[\d,.]+)\s+gigawatts?\s+of\s+potential\s+demand\s+in\s+(?:line|pipeline)',
            "system_gw": r'(\d[\d,.]+)[\-\s]gigawatt\s+system',
        },
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# 4B: EIA FORM 860/860M
# ══════════════════════════════════════════════════════════════════════════════

EIA_860M_URL = "https://www.eia.gov/electricity/data/eia860m/"
EIA_860_URL = "https://www.eia.gov/electricity/data/eia860/"

# Direct download links (updated monthly)
# The EIA publishes the 860M as an Excel file; the URL pattern changes monthly
EIA_860M_DOWNLOAD_PATTERN = "https://www.eia.gov/electricity/data/eia860m/xls/july_generator{year}.xlsx"

# Fields of interest in EIA 860M
EIA_PLANNED_FIELDS = [
    "Entity ID", "Entity Name", "Plant ID", "Plant Name",
    "State", "County", "Nameplate Capacity (MW)",
    "Net Summer Capacity (MW)", "Technology",
    "Energy Source Code", "Planned Operation Month", "Planned Operation Year",
    "Status", "Sector",
]

# Texas ERCOT region states
EIA_STATE_FILTER = ["TX"]

# ══════════════════════════════════════════════════════════════════════════════
# RELEVANCE FILTERING
# ══════════════════════════════════════════════════════════════════════════════

# Reuse from Pipeline 1's keyword sets
PRIMARY_KEYWORDS = [
    "hydrogen", "blue hydrogen", "green hydrogen",
    "ammonia", "blue ammonia",
    "carbon capture", "CCS", "CCUS", "CO2",
    "SMR", "ATR", "air separation unit", "ASU",
    "haber-bosch", "sequestration",
]

SECONDARY_KEYWORDS = [
    "large load", "industrial load",
    "Gulf Coast", "ship channel", "freeport", "texas city",
    "port arthur", "beaumont", "corpus christi", "victoria",
    "petrochemical", "refinery", "chemical plant",
    "data center", "crypto",
]

GULF_COAST_COUNTIES = [
    "Brazoria", "Galveston", "Harris", "Chambers", "Jefferson",
    "Orange", "Nueces", "San Patricio", "Calhoun", "Matagorda",
    "Victoria", "Jackson", "Wharton", "Fort Bend", "Liberty",
    "Aransas", "Kleberg", "Refugio",
]

KNOWN_DEVELOPERS = [
    "Air Products", "Air Liquide", "Linde",
    "CF Industries", "LSB Industries", "OCI",
    "Plug Power", "Green Hydrogen International",
    "ExxonMobil", "Chevron", "Shell",
    "Occidental", "Oxy", "Denbury", "Talos Energy",
    "NET Power",
]


@dataclass
class ScrapeResult:
    source: str
    doc_type: str
    title: str
    url: str
    date: str
    content_hash: str
    text_excerpt: str = ""
    relevance_score: float = 0.0
    matched_keywords: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    local_path: str = ""
    is_new: bool = True
