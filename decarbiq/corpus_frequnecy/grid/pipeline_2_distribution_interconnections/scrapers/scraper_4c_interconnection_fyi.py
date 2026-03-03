"""
Scraper 4C: Interconnection.fyi — Aggregated Queue Data
=========================================================
Interconnection.fyi tracks queue requests across US ISOs with daily updates.
It provides:
- ERCOT generation interconnection queue (solar, storage, wind, gas)
- DG interconnection queue
- Data center projects by state
- Year-by-year project counts and status breakdowns

This is the best single source for tracking the generation side of the
Texas grid buildout — which directly correlates with large load growth
(you don't build 100+ GW of new generation unless load is coming).
"""

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers.utils import (
    fetch_page_soup, fetch_url, download_file, extract_all_links,
    content_hash, is_new_content, mark_seen,
)
from config.settings import (
    STORAGE_DIR, OUTPUTS_DIR, ScrapeResult,
    GULF_COAST_COUNTIES, KNOWN_DEVELOPERS,
)

IFYI_BASE = "https://www.interconnection.fyi"
IFYI_ERCOT = f"{IFYI_BASE}/ercot"
IFYI_DATA_CENTERS = f"{IFYI_BASE}/?type=data_center"
IFYI_DIR = STORAGE_DIR / "interconnection_fyi"
IFYI_DIR.mkdir(parents=True, exist_ok=True)


def scrape_interconnection_fyi_ercot() -> list[ScrapeResult]:
    """
    Scrape interconnection.fyi for ERCOT queue data.
    The site renders with JS but provides downloadable CSV data.
    We try the page + look for data download links.
    """
    print("[4C.1] Scraping interconnection.fyi ERCOT queue...")
    results = []

    # Try fetching the ERCOT page
    resp = fetch_url(IFYI_ERCOT)
    if resp is None:
        # Try base page
        resp = fetch_url(IFYI_BASE)

    if resp is None:
        print("  [WARN] Could not reach interconnection.fyi")
        return results

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "lxml")
    body_text = soup.get_text(separator=" ", strip=True)

    # Look for download links (CSV/Excel)
    all_links = extract_all_links(soup, extensions=[".csv", ".xlsx", ".json"])

    # Also look for API-style data endpoints
    api_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/api/" in href or "/data/" in href or "/download" in href:
            full = href if href.startswith("http") else f"{IFYI_BASE}{href}"
            api_links.append({"url": full, "text": a.get_text(strip=True)})

    print(f"  Found {len(all_links)} data files, {len(api_links)} API links")

    # Download any data files
    for link in (all_links + api_links)[:10]:
        url = link["url"]
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        local = download_file(url, IFYI_DIR)
        if local:
            file_hash = content_hash(local.read_bytes())
            result = ScrapeResult(
                source="interconnection_fyi",
                doc_type="queue_data_file",
                title=f"interconnection.fyi: {link['text'][:80] or local.name}",
                url=url,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=file_hash,
                text_excerpt=f"Data file: {local.name}",
                relevance_score=0.5,
                matched_keywords=["ERCOT", "interconnection queue"],
                metadata={"file_type": local.suffix, "file_name": local.name},
                local_path=str(local),
                is_new=is_new_content(file_hash),
            )
            results.append(result)
            mark_seen(url_hash)
            mark_seen(file_hash)

    # Extract any visible statistics from the page text
    stats = _extract_page_stats(body_text)
    if stats:
        page_hash = content_hash(json.dumps(stats, sort_keys=True))
        result = ScrapeResult(
            source="interconnection_fyi",
            doc_type="queue_page_snapshot",
            title=f"interconnection.fyi ERCOT Snapshot",
            url=IFYI_ERCOT,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=page_hash,
            text_excerpt=json.dumps(stats, indent=2),
            relevance_score=0.5,
            matched_keywords=["ERCOT", "queue"],
            metadata={"extracted_stats": stats},
            is_new=is_new_content(page_hash),
        )
        results.append(result)
        mark_seen(page_hash)

    print(f"  [OK] {len(results)} items from interconnection.fyi")
    return results


def scrape_data_center_projects() -> list[ScrapeResult]:
    """
    Scrape interconnection.fyi data center project tracker.
    This tracks data center interconnection requests by state —
    important context for understanding what fraction of the queue
    is data centers vs industrial/H2/ammonia.
    """
    print("[4C.2] Scraping interconnection.fyi data center projects...")
    results = []

    resp = fetch_url(IFYI_DATA_CENTERS)
    if resp is None:
        return results

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(resp.text, "lxml")
    body_text = soup.get_text(separator=" ", strip=True)

    # Try to extract Texas-specific data center stats
    stats = {}
    tx_match = re.search(r'texas[:\s]+(\d[\d,]+)\s*(?:gw|mw|projects?|requests?)', body_text.lower())
    if tx_match:
        stats["texas_data_center_figure"] = tx_match.group(1)

    # Count mentions of Texas
    tx_count = body_text.lower().count("texas")
    if tx_count > 0:
        stats["texas_mentions"] = tx_count

    if stats or len(body_text) > 100:
        page_hash = content_hash(body_text[:5000])
        result = ScrapeResult(
            source="interconnection_fyi_dc",
            doc_type="data_center_tracker",
            title="interconnection.fyi: Data Center Projects",
            url=IFYI_DATA_CENTERS,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=page_hash,
            text_excerpt=body_text[:2000],
            relevance_score=0.4,
            matched_keywords=["data center", "interconnection"],
            metadata=stats,
            is_new=is_new_content(page_hash),
        )
        results.append(result)
        mark_seen(page_hash)

    print(f"  [OK] {len(results)} data center items")
    return results


def _extract_page_stats(text: str) -> dict:
    """Extract any visible queue statistics from page text."""
    stats = {}
    text_lower = text.lower()

    patterns = {
        "total_queue_gw": r'(?:ercot|texas)[^0-9]{0,50}(\d[\d,.]+)\s*gw',
        "active_projects": r'(\d[\d,]+)\s+active\s+(?:projects?|requests?)',
        "withdrawn_projects": r'(\d[\d,]+)\s+withdrawn',
        "operational_projects": r'(\d[\d,]+)\s+operational',
        "solar_gw": r'solar[^0-9]{0,20}(\d[\d,.]+)\s*gw',
        "storage_gw": r'(?:storage|battery)[^0-9]{0,20}(\d[\d,.]+)\s*gw',
        "wind_gw": r'wind[^0-9]{0,20}(\d[\d,.]+)\s*gw',
        "gas_gw": r'(?:natural\s+gas|gas\s+turbine)[^0-9]{0,20}(\d[\d,.]+)\s*gw',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text_lower)
        if match:
            val = match.group(1).replace(",", "")
            try:
                stats[key] = float(val)
            except ValueError:
                stats[key] = val

    return stats


def run_all_4c() -> list[ScrapeResult]:
    results = []
    results.extend(scrape_interconnection_fyi_ercot())
    results.extend(scrape_data_center_projects())
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_4c()
    print(f"\n[4C TOTAL] {len(all_results)} items")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:70]}")
