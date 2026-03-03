"""
Scraper 2D: Indirect Triangulation Sources (Enhanced)
======================================================
1. SEC EDGAR — TSP earnings calls / 10-K / 10-Q with structured NLP extraction
   of queue sizes, signed GW, load type breakdowns, CapEx plans
2. Earnings Call Transcript NLP — regex extraction of key metrics from text
3. ERCOT Constraints Report — annual legislative report parsing
4. Permit Cross-Reference Framework — config for matching permits to electricity signals

TSP targets: Sempra (Oncor), CenterPoint, AEP
"""

import re
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field, asdict

from scrapers.utils import (
    fetch_url, fetch_page_soup, download_file, extract_pdf_text,
    content_hash, score_relevance, is_new_content, mark_seen, ScrapeResult,
)
from config.settings import (
    TSP_SEC_ENTITIES, ERCOT_PLANNING_URL, STORAGE_DIR, OUTPUTS_DIR,
    KNOWN_DEVELOPERS, GULF_COAST_COUNTIES, REQUEST_HEADERS,
)

SEC_DOCS_DIR = STORAGE_DIR / "sec_filings"
ERCOT_DOCS_DIR = STORAGE_DIR / "ercot_docs"


@dataclass
class TSPQueueMetrics:
    """Structured metrics extracted from TSP disclosures."""
    entity: str = ""
    report_date: str = ""
    total_queue_gw: float = 0.0
    signed_agreements_gw: float = 0.0
    high_confidence_gw: float = 0.0
    data_center_gw: float = 0.0
    data_center_pct: float = 0.0
    industrial_gw: float = 0.0
    crypto_gw: float = 0.0
    oil_gas_gw: float = 0.0
    large_load_customers: int = 0
    capex_plan_billions: float = 0.0
    capex_additional_billions: float = 0.0
    raw_excerpts: list = field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 2D.1: SEC EDGAR Filing Search
# ══════════════════════════════════════════════════════════════════════════════

def scrape_sec_edgar_filings(lookback_days: int = 90) -> list[ScrapeResult]:
    print("[2D.1] Searching SEC EDGAR for TSP filings...")
    results = []
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=lookback_days)

    for entity_key, entity in TSP_SEC_ENTITIES.items():
        print(f"  Searching {entity_key} ({entity['ticker']})...")
        entity_results = _search_edgar(entity, start_date, end_date)
        results.extend(entity_results)
        print(f"    -> {len(entity_results)} filings found")
    return results


def _search_edgar(entity: dict, start_date: datetime, end_date: datetime) -> list[ScrapeResult]:
    results = []
    queries = [
        f'"{entity["name"]}" "large load" interconnection',
        f'"{entity["name"]}" "data center" Texas grid',
    ]
    for query in queries:
        url = (f"https://efts.sec.gov/LATEST/search-index"
               f"?q={query.replace(' ', '+')}"
               f"&dateRange=custom"
               f"&startdt={start_date.strftime('%Y-%m-%d')}"
               f"&enddt={end_date.strftime('%Y-%m-%d')}")
        resp = fetch_url(url)
        if resp is None:
            continue
        try:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
        except (json.JSONDecodeError, AttributeError):
            hits = []

        for hit in hits[:5]:
            source = hit.get("_source", {})
            filing_url = source.get("file_name", "")
            if filing_url and not filing_url.startswith("http"):
                filing_url = f"https://www.sec.gov/Archives/edgar/data/{entity['cik']}/{filing_url}"
            form_type = source.get("form_type", "unknown")
            filed_date = source.get("file_date", "")
            filing_hash = content_hash(f"{entity['cik']}_{filing_url}_{filed_date}")
            if not is_new_content(filing_hash):
                continue
            result = ScrapeResult(
                source="sec_edgar", doc_type=f"sec_{form_type}",
                title=f"[{entity['ticker']}] {form_type}",
                url=filing_url,
                date=filed_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=filing_hash, text_excerpt="",
                relevance_score=0.5, matched_keywords=entity["keywords"][:3],
                metadata={"entity": entity["name"], "ticker": entity["ticker"],
                          "cik": entity["cik"], "form_type": form_type},
                is_new=True,
            )
            results.append(result)
            mark_seen(filing_hash)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 2D.2: Earnings Call / Filing NLP Extraction
# ══════════════════════════════════════════════════════════════════════════════

def extract_tsp_metrics(text: str, entity_key: str) -> TSPQueueMetrics:
    """
    Regex-based NLP to extract queue sizes, signed GW, load breakdowns,
    customer counts, and CapEx signals from earnings call / 10-K text.
    """
    m = TSPQueueMetrics(entity=entity_key)
    tl = text.lower()

    def _first_match(patterns, txt):
        for p in patterns:
            match = re.search(p, txt)
            if match:
                return float(match.group(1).replace(",", "")), match
        return 0.0, None

    # Queue total
    val, match = _first_match([
        r'(?:queue|pipeline|interconnection\s+request)[^0-9]{0,60}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
        r'(\d[\d,.]+)\s*(?:gw|gigawatt)[^.]{0,60}?(?:queue|pipeline|interconnection)',
        r'(?:exploring|seeking|requesting)\s+(?:grid\s+)?interconnection[^0-9]{0,40}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
    ], tl)
    if val:
        m.total_queue_gw = val
        m.raw_excerpts.append(f"queue: ...{tl[max(0,match.start()-20):match.end()+20]}...")

    # Signed agreements
    val, _ = _first_match([
        r'(?:signed|executed)\s+(?:interconnection\s+)?agreement[s]?[^0-9]{0,40}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
        r'signed[^0-9]{0,20}(\d[\d,.]+)\s*(?:gw|gigawatt)',
    ], tl)
    if val: m.signed_agreements_gw = val

    # High confidence
    val, _ = _first_match([
        r'high[\-\s]?confidence[^0-9]{0,40}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
    ], tl)
    if val: m.high_confidence_gw = val

    # Data centers
    val, _ = _first_match([
        r'data\s+center[s]?[^0-9]{0,30}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
        r'(\d[\d,.]+)\s*(?:gw|gigawatt)[^.]{0,30}?data\s+center',
    ], tl)
    if val: m.data_center_gw = val

    dc_pct = re.search(r'data\s+center[s]?[^0-9]{0,20}?(\d+)\s*%', tl)
    if dc_pct: m.data_center_pct = float(dc_pct.group(1))

    # Industrial
    val, _ = _first_match([
        r'(?:industrial|c&i|commercial\s+and\s+industrial)[^0-9]{0,30}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
    ], tl)
    if val: m.industrial_gw = val

    # Crypto
    val, _ = _first_match([
        r'(?:crypto|cryptocurrency|mining)[^0-9]{0,30}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
    ], tl)
    if val: m.crypto_gw = val

    # Oil & Gas
    val, _ = _first_match([
        r'(?:oil\s+(?:and|&)\s+gas|o&g)[^0-9]{0,30}?(\d[\d,.]+)\s*(?:gw|gigawatt)',
    ], tl)
    if val: m.oil_gas_gw = val

    # Customer count
    cust = re.search(r'(\d[\d,]+)\s+(?:large\s+load\s+)?customer[s]?\s+in\s+(?:its\s+)?(?:interconnection\s+)?queue', tl)
    if cust: m.large_load_customers = int(cust.group(1).replace(",", ""))

    # CapEx
    val, _ = _first_match([
        r'(?:capital\s+plan|capex)[^0-9$]{0,40}?\$?(\d[\d,.]+)\s*billion',
        r'\$(\d[\d,.]+)\s*billion[^.]{0,40}?(?:capital\s+plan|capex)',
    ], tl)
    if val: m.capex_plan_billions = val

    add_capex = re.search(r'(?:add|additional)[^0-9$]{0,30}?\$?(\d[\d,.]+)\s*billion[^.]{0,30}?capital', tl)
    if add_capex: m.capex_additional_billions = float(add_capex.group(1).replace(",", ""))

    return m


def process_filing_with_nlp(result: ScrapeResult) -> ScrapeResult:
    """Download a filing and run NLP extraction."""
    if not result.url:
        return result

    if not result.local_path:
        local = download_file(result.url, SEC_DOCS_DIR)
        if local:
            result.local_path = str(local)
        else:
            return result

    local = Path(result.local_path)
    text = ""
    if str(local).lower().endswith(".pdf"):
        text = extract_pdf_text(local, max_pages=50)
    elif str(local).lower().endswith((".htm", ".html", ".txt")):
        raw = local.read_text(errors="ignore")
        from bs4 import BeautifulSoup
        text = BeautifulSoup(raw, "lxml").get_text(separator=" ", strip=True)

    if not text:
        return result

    entity_key = result.metadata.get("ticker", "unknown")
    metrics = extract_tsp_metrics(text, entity_key)
    metrics_dict = {k: v for k, v in asdict(metrics).items()
                    if v and v != 0 and v != 0.0 and v != [] and v != ""}
    result.metadata["extracted_metrics"] = metrics_dict
    result.text_excerpt = text[:2000]

    relevance, matched = score_relevance(text)
    result.relevance_score = relevance
    result.matched_keywords = matched
    return result


# ══════════════════════════════════════════════════════════════════════════════
# 2D.3: ERCOT Constraints Report
# ══════════════════════════════════════════════════════════════════════════════

def scrape_ercot_constraints_report() -> list[ScrapeResult]:
    print("[2D.3] Scraping ERCOT Constraints Report...")
    results = []
    soup = fetch_page_soup(ERCOT_PLANNING_URL)
    if soup is None:
        return results

    links = []
    for a in soup.find_all("a", href=True):
        href, text = a["href"], a.get_text(strip=True)
        if ("constraint" in text.lower() and "need" in text.lower()) or \
           "Report-on-Existing" in href:
            full = href if href.startswith("http") else f"https://www.ercot.com{href}"
            links.append({"url": full, "text": text})

    print(f"  Found {len(links)} constraints report links")
    for link in links:
        url = link["url"]
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue
        local = download_file(url, ERCOT_DOCS_DIR)
        if local is None:
            continue
        text = extract_pdf_text(local, max_pages=50)
        doc_hash = content_hash(text) if text else url_hash
        relevance, matched = score_relevance(text)
        report_data = _extract_constraints_data(text)

        result = ScrapeResult(
            source="ercot_constraints_report", doc_type="legislative_report",
            title=link["text"] or "ERCOT Constraints and Needs Report",
            url=url, date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=doc_hash, text_excerpt=text[:3000] if text else "",
            relevance_score=max(relevance, 0.4), matched_keywords=matched,
            metadata={"extracted_data": report_data},
            local_path=str(local), is_new=is_new_content(doc_hash),
        )
        results.append(result)
        mark_seen(url_hash)
        mark_seen(doc_hash)
    return results


def _extract_constraints_data(text: str) -> dict:
    data = {}
    tl = text.lower()
    extractors = {
        "total_large_load_gw": r'(?:large\s+load[^0-9]*?(?:queue|interconnection)[^0-9]*?)(\d[\d,.]+)\s*gw',
        "officer_letter_loads_gw": r'officer\s+letter\s+load[s]?[^0-9]*?(\d[\d,.]+)\s*gw',
        "peak_demand_gw": r'(?:peak\s+demand|system\s+peak)[^0-9]*?(?:could|projected|forecast)[^0-9]*?(\d[\d,.]+)\s*gw',
        "generation_queue_gw": r'generation\s+interconnection[^0-9]*?(\d[\d,.]+)\s*gw',
        "permian_basin_load_gw": r'permian\s+basin[^0-9]*?(?:load|demand)[^0-9]*?(\d[\d,.]+)\s*gw',
    }
    for key, pattern in extractors.items():
        match = re.search(pattern, tl)
        if match:
            try:
                data[key] = float(match.group(1).replace(",", ""))
            except ValueError:
                pass

    loc_mentions = {c: tl.count(c.lower()) for c in GULF_COAST_COUNTIES if tl.count(c.lower()) > 0}
    if loc_mentions:
        data["gulf_coast_county_mentions"] = loc_mentions
    return data


# ══════════════════════════════════════════════════════════════════════════════
# 2D.4: Permit Cross-Reference Config
# ══════════════════════════════════════════════════════════════════════════════

def generate_permit_crossref_config() -> dict:
    """Generate matching config for connecting permit data to electricity signals."""
    print("[2D.4] Generating permit cross-reference configuration...")
    config = {
        "description": "Maps electricity signals to permit databases for cross-referencing",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "matching_rules": [
            {"name": "County Match", "permit_field": "facility_county",
             "electricity_field": "county", "match_type": "exact",
             "priority_counties": GULF_COAST_COUNTIES},
            {"name": "Developer Match", "permit_field": "applicant_name",
             "electricity_field": "interconnecting_entity", "match_type": "fuzzy",
             "known_entities": KNOWN_DEVELOPERS},
            {"name": "MW Cross-Reference", "permit_field": "estimated_power_mw",
             "electricity_field": "capacity_mw", "match_type": "range", "tolerance_pct": 20},
            {"name": "Facility Type Filter", "permit_field": "facility_type",
             "include_types": ["hydrogen production", "ammonia production", "carbon capture",
                               "CO2 compression", "steam methane reformer", "autothermal reformer",
                               "air separation unit", "chemical plant", "petrochemical"]},
        ],
        "permit_sources": {
            "epa_echo": {"url": "https://echo.epa.gov/",
                         "api": "https://echodata.epa.gov/echo/dfr_rest_services.get_dfr",
                         "filter": "FAC_STATE=TX"},
            "tceq_air_permits": {"url": "https://www.tceq.texas.gov/permitting/air",
                                 "search": "https://www15.tceq.texas.gov/crpub/index.cfm"},
        },
        "ercot_zone_to_county": {
            "COAST": ["Brazoria", "Galveston", "Matagorda", "Calhoun", "Aransas",
                       "Nueces", "San Patricio", "Kleberg", "Refugio", "Jackson"],
            "HOUSTON": ["Harris", "Fort Bend", "Chambers", "Liberty", "Wharton"],
            "SOUTH": ["Nueces", "San Patricio", "Kleberg", "Victoria"],
            "EAST": ["Jefferson", "Orange"],
        },
    }
    output = OUTPUTS_DIR / "permit_crossref_config.json"
    output.write_text(json.dumps(config, indent=2))
    print(f"  [OK] Config saved to {output}")
    return config


# ── Entry Point ──────────────────────────────────────────────────────────────

def run_all_2d() -> list[ScrapeResult]:
    results = []

    # 2D.1 + 2D.2: SEC EDGAR search + NLP extraction
    sec_results = scrape_sec_edgar_filings(lookback_days=90)
    print("[2D.2] Running NLP extraction on SEC filings...")
    processed = 0
    for i, r in enumerate(sec_results):
        try:
            sec_results[i] = process_filing_with_nlp(r)
            if sec_results[i].metadata.get("extracted_metrics"):
                processed += 1
        except Exception as e:
            print(f"  [WARN] NLP failed for {r.title}: {e}")
    print(f"  [OK] Extracted metrics from {processed}/{len(sec_results)} filings")
    results.extend(sec_results)

    # Save metrics summary
    metrics = [r.metadata["extracted_metrics"] for r in sec_results
               if r.metadata.get("extracted_metrics")]
    if metrics:
        (OUTPUTS_DIR / "tsp_queue_metrics.json").write_text(
            json.dumps(metrics, indent=2, default=str))

    # 2D.3: Constraints report
    results.extend(scrape_ercot_constraints_report())

    # 2D.4: Permit cross-ref config
    generate_permit_crossref_config()

    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_2d()
    print(f"\n[2D TOTAL] {len(all_results)} items collected")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:70]} | relevance={r.relevance_score:.2f}")
        if r.metadata.get("extracted_metrics"):
            m = r.metadata["extracted_metrics"]
            for k in ["total_queue_gw", "signed_agreements_gw", "data_center_gw"]:
                if m.get(k): print(f"    {k}: {m[k]} GW")
