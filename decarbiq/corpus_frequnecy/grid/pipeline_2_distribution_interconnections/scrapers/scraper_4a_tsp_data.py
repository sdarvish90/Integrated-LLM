"""
Scraper 4A: TSP Distribution & Interconnection Queue Data
===========================================================
1. Oncor quarterly earnings press releases — structured extraction of
   LC&I queue, generation POI queue, circuit miles, premises growth
2. CenterPoint newsroom — queue totals and load type breakdown
3. AEP Texas press releases — signed agreements, pipeline GW
4. TSP DG interconnection pages — track application forms and process changes

Oncor is the richest source: quarterly reports include exact request counts,
GW by type (data centers, industrial, crypto, O&G), generation mix percentages,
and customer collateral figures.
"""

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from scrapers.utils import (
    fetch_page_soup, fetch_url, download_file,
    content_hash, is_new_content, mark_seen, extract_all_links,
)
from config.settings import (
    TSP_SOURCES, STORAGE_DIR, OUTPUTS_DIR, ScrapeResult,
    GULF_COAST_COUNTIES, KNOWN_DEVELOPERS,
)


# ══════════════════════════════════════════════════════════════════════════════
# 4A.1: Oncor Quarterly Earnings — Structured Queue Extraction
# ══════════════════════════════════════════════════════════════════════════════

def scrape_oncor_earnings() -> list[ScrapeResult]:
    """
    Scrape Oncor quarterly earnings press releases for structured queue data.
    
    Oncor publishes detailed interconnection queue metrics quarterly:
    - LC&I request count and GW breakdown (data centers, industrial, etc.)
    - Generation POI queue (storage %, solar %, wind %, gas %)
    - Circuit miles built/rebuilt/upgraded
    - New premises served
    - Customer collateral held
    """
    print("[4A.1] Scraping Oncor quarterly earnings releases...")
    results = []
    oncor = TSP_SOURCES["oncor"]

    # First, try to discover earnings URLs from newsroom
    earnings_urls = list(oncor.get("earnings_urls", []))

    newsroom_soup = fetch_page_soup(oncor["newsroom"])
    if newsroom_soup:
        for a in newsroom_soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"]
            if "quarter" in text and "result" in text:
                full = href if href.startswith("http") else f"https://www.oncor.com{href}"
                if full not in earnings_urls:
                    earnings_urls.append(full)

    print(f"  Found {len(earnings_urls)} earnings release URLs")

    for url in earnings_urls:
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        resp = fetch_url(url)
        if resp is None:
            continue

        text = resp.text
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(text, "lxml")
        body_text = soup.get_text(separator=" ", strip=True)

        # Extract quarter and year from title/URL
        quarter_match = re.search(r'(first|second|third|fourth)\s+quarter\s+(\d{4})', body_text.lower())
        quarter_str = ""
        if quarter_match:
            q_map = {"first": "Q1", "second": "Q2", "third": "Q3", "fourth": "Q4"}
            quarter_str = f"{q_map[quarter_match.group(1)]} {quarter_match.group(2)}"

        # Extract structured metrics using configured patterns
        metrics = {}
        for field_name, pattern in oncor["queue_fields"].items():
            match = re.search(pattern, body_text, re.IGNORECASE)
            if match:
                val = match.group(1).replace(",", "")
                try:
                    metrics[field_name] = float(val) if "." in val else int(val)
                except ValueError:
                    metrics[field_name] = val

        doc_hash = content_hash(body_text)
        result = ScrapeResult(
            source="oncor_earnings",
            doc_type="quarterly_earnings",
            title=f"Oncor {quarter_str} Earnings" if quarter_str else soup.title.string[:80] if soup.title else "Oncor Earnings",
            url=url,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=doc_hash,
            text_excerpt=body_text[:3000],
            relevance_score=0.7 if metrics else 0.3,
            matched_keywords=["large load", "interconnection", "distribution"],
            metadata={
                "quarter": quarter_str,
                "extracted_metrics": metrics,
                "tsp": "oncor",
            },
            local_path="",
            is_new=is_new_content(doc_hash),
        )
        results.append(result)
        mark_seen(url_hash)
        mark_seen(doc_hash)

    # Save time series of Oncor metrics
    oncor_metrics = [r.metadata["extracted_metrics"] for r in results
                     if r.metadata.get("extracted_metrics")]
    if oncor_metrics:
        for i, m in enumerate(oncor_metrics):
            m["quarter"] = results[i].metadata.get("quarter", "")
        ts_path = OUTPUTS_DIR / "oncor_queue_timeseries.json"
        ts_path.write_text(json.dumps(oncor_metrics, indent=2))
        print(f"  [OK] Oncor time series saved ({len(oncor_metrics)} quarters)")

    print(f"  [OK] {len(results)} Oncor earnings processed")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 4A.2: CenterPoint Queue Data
# ══════════════════════════════════════════════════════════════════════════════

def scrape_centerpoint_data() -> list[ScrapeResult]:
    """Scrape CenterPoint investor news for queue data."""
    print("[4A.2] Scraping CenterPoint data...")
    results = []
    cnp = TSP_SOURCES["centerpoint"]

    soup = fetch_page_soup(cnp["newsroom"])
    if soup is None:
        print("  [WARN] Could not fetch CenterPoint newsroom")
        return results

    # Find earnings-related links
    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"]
        if ("quarter" in text and "result" in text) or "earnings" in text:
            full = href if href.startswith("http") else f"https://investors.centerpointenergy.com{href}"
            url_hash = content_hash(full)
            if not is_new_content(url_hash):
                continue

            resp = fetch_url(full)
            if resp is None:
                continue

            from bs4 import BeautifulSoup
            page_soup = BeautifulSoup(resp.text, "lxml")
            body_text = page_soup.get_text(separator=" ", strip=True)

            metrics = {}
            for field_name, pattern in cnp["queue_fields"].items():
                match = re.search(pattern, body_text, re.IGNORECASE)
                if match:
                    metrics[field_name] = float(match.group(1).replace(",", ""))

            if metrics:
                doc_hash = content_hash(body_text)
                result = ScrapeResult(
                    source="centerpoint_earnings",
                    doc_type="quarterly_earnings",
                    title=a.get_text(strip=True)[:100],
                    url=full, date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    content_hash=doc_hash, text_excerpt=body_text[:2000],
                    relevance_score=0.6, matched_keywords=["large load", "Houston", "interconnection"],
                    metadata={"extracted_metrics": metrics, "tsp": "centerpoint"},
                    is_new=is_new_content(doc_hash),
                )
                results.append(result)
                mark_seen(url_hash)

    print(f"  [OK] {len(results)} CenterPoint items processed")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 4A.3: AEP Texas Queue Data
# ══════════════════════════════════════════════════════════════════════════════

def scrape_aep_data() -> list[ScrapeResult]:
    """Scrape AEP press releases for Texas queue data."""
    print("[4A.3] Scraping AEP Texas data...")
    results = []
    aep = TSP_SOURCES["aep_texas"]

    soup = fetch_page_soup(aep["newsroom"])
    if soup is None:
        print("  [WARN] Could not fetch AEP newsroom")
        return results

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True).lower()
        href = a["href"]
        if ("quarter" in text or "earnings" in text) and "result" in text:
            full = href if href.startswith("http") else f"https://www.aep.com{href}"
            url_hash = content_hash(full)
            if not is_new_content(url_hash):
                continue

            resp = fetch_url(full)
            if resp is None:
                continue

            from bs4 import BeautifulSoup
            page_soup = BeautifulSoup(resp.text, "lxml")
            body_text = page_soup.get_text(separator=" ", strip=True)

            metrics = {}
            for field_name, pattern in aep["queue_fields"].items():
                match = re.search(pattern, body_text, re.IGNORECASE)
                if match:
                    metrics[field_name] = float(match.group(1).replace(",", ""))

            if metrics:
                doc_hash = content_hash(body_text)
                result = ScrapeResult(
                    source="aep_earnings", doc_type="quarterly_earnings",
                    title=a.get_text(strip=True)[:100],
                    url=full, date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    content_hash=doc_hash, text_excerpt=body_text[:2000],
                    relevance_score=0.6, matched_keywords=["large load", "AEP Texas", "interconnection"],
                    metadata={"extracted_metrics": metrics, "tsp": "aep_texas"},
                    is_new=is_new_content(doc_hash),
                )
                results.append(result)
                mark_seen(url_hash)

    print(f"  [OK] {len(results)} AEP items processed")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 4A.4: TSP DG Interconnection Pages — Track Form/Process Changes
# ══════════════════════════════════════════════════════════════════════════════

def track_tsp_dg_pages() -> list[ScrapeResult]:
    """Monitor TSP DG interconnection pages for form or process changes."""
    print("[4A.4] Tracking TSP DG interconnection pages...")
    results = []

    pages = {
        "oncor_dg": {
            "url": TSP_SOURCES["oncor"]["dg_interconnection_page"],
            "tsp": "oncor",
        },
    }

    for key, info in pages.items():
        soup = fetch_page_soup(info["url"])
        if soup is None:
            continue

        body_text = soup.get_text(separator=" ", strip=True)
        page_hash = content_hash(body_text)

        if is_new_content(page_hash):
            # Extract links to application forms
            form_links = extract_all_links(soup, extensions=[".pdf", ".xlsx"])
            result = ScrapeResult(
                source=f"{info['tsp']}_dg_page",
                doc_type="dg_interconnection_page",
                title=f"{info['tsp'].title()} DG Interconnection Page",
                url=info["url"],
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=page_hash,
                text_excerpt=body_text[:2000],
                relevance_score=0.3,
                matched_keywords=["distribution", "interconnection", "DG"],
                metadata={
                    "tsp": info["tsp"],
                    "form_links": [l["url"] for l in form_links[:10]],
                    "form_count": len(form_links),
                },
                is_new=True,
            )
            results.append(result)
            mark_seen(page_hash)

    print(f"  [OK] {len(results)} DG page changes detected")
    return results


def run_all_4a() -> list[ScrapeResult]:
    results = []
    results.extend(scrape_oncor_earnings())
    results.extend(scrape_centerpoint_data())
    results.extend(scrape_aep_data())
    results.extend(track_tsp_dg_pages())
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_4a()
    print(f"\n[4A TOTAL] {len(all_results)} items")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:60]} | metrics={bool(r.metadata.get('extracted_metrics'))}")
