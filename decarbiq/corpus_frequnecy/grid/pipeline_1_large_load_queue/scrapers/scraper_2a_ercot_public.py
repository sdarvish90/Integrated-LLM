"""
Scraper 2A: ERCOT Public Documents
====================================
1. Monthly Operational Overview PDFs (large load queue aggregates)
2. Board of Directors meeting presentations (System Planning updates)
3. RPG (Regional Planning Group) meeting materials
4. Large Load Integration page documents (Q&A, forms, gen resource list)

All scraped from ercot.com, PDFs downloaded and text-extracted,
scored for relevance to blue H2/ammonia/CCS.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from scrapers.utils import (
    fetch_page_soup, download_file, extract_pdf_text, extract_pdf_links,
    extract_all_links, content_hash, score_relevance, is_new_content,
    mark_seen, ScrapeResult,
)
from config.settings import (
    ERCOT_RESOURCE_ADEQUACY_URL, ERCOT_PLANNING_URL, ERCOT_CALENDAR_URL,
    ERCOT_LARGE_LOAD_PAGE, ERCOT_FILES_BASE, BOARD_PRESENTATION_KEYWORDS,
    RPG_KEYWORDS, STORAGE_DIR,
)

ERCOT_DOCS_DIR = STORAGE_DIR / "ercot_docs"


# ── 2A.1: Monthly Operational Overview ───────────────────────────────────────

def scrape_monthly_operational_overviews() -> list[ScrapeResult]:
    """
    Scrape ERCOT Resource Adequacy page for Monthly Operational Overview PDFs.
    These contain the aggregate large load queue chart.
    """
    print("[2A.1] Scraping ERCOT Monthly Operational Overviews...")
    results = []

    soup = fetch_page_soup(ERCOT_RESOURCE_ADEQUACY_URL)
    if soup is None:
        print("  [ERROR] Could not fetch Resource Adequacy page")
        return results

    pdf_links = extract_pdf_links(soup)
    overview_links = [
        l for l in pdf_links
        if "operational-overview" in l["url"].lower()
        or "monthly-operational" in l["url"].lower()
        or "Monthly-Operational" in l["url"]
    ]

    print(f"  Found {len(overview_links)} Operational Overview PDFs")

    for link in overview_links:
        url = link["url"]
        title = link["text"] or url.split("/")[-1]

        # Check if we've already processed this URL
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        # Download PDF
        local = download_file(url, ERCOT_DOCS_DIR)
        if local is None:
            continue

        # Extract text
        text = extract_pdf_text(local, max_pages=15)
        doc_hash = content_hash(text) if text else url_hash

        # Score relevance
        relevance, matched = score_relevance(text)

        # Extract date from filename if possible
        date_match = re.search(r'(\w+)-(\d{4})', title)
        doc_date = ""
        if date_match:
            try:
                month_name = date_match.group(1)
                year = date_match.group(2)
                doc_date = datetime.strptime(f"{month_name} {year}", "%B %Y").strftime("%Y-%m-01")
            except ValueError:
                doc_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Extract large load queue numbers from text
        queue_data = _extract_large_load_queue_data(text)

        result = ScrapeResult(
            source="ercot_monthly_overview",
            doc_type="operational_overview_pdf",
            title=title,
            url=url,
            date=doc_date,
            content_hash=doc_hash,
            text_excerpt=text[:2000] if text else "",
            relevance_score=relevance,
            matched_keywords=matched,
            metadata={"queue_data": queue_data, "pages_extracted": len(text.split("\n\n"))},
            local_path=str(local),
            is_new=is_new_content(doc_hash),
        )
        results.append(result)
        mark_seen(url_hash)
        mark_seen(doc_hash)

    print(f"  [OK] {len(results)} new overviews processed")
    return results


def _extract_large_load_queue_data(text: str) -> dict:
    """
    Attempt to extract large load queue MW figures from operational overview text.
    These appear in the 'Current Large Load Interconnection Queue' section.
    """
    data = {}
    text_lower = text.lower()

    # Look for MW figures near large load queue mentions
    patterns = {
        "observed_energized_mw": r'observed\s+energized[^0-9]*?(\d[\d,]+)\s*(?:mw|gw)',
        "approved_to_energize_mw": r'approved\s+to\s+energize[^0-9]*?(\d[\d,]+)\s*(?:mw|gw)',
        "planning_studies_approved_mw": r'planning\s+studies\s+approved[^0-9]*?(\d[\d,]+)\s*(?:mw|gw)',
        "under_review_mw": r'under\s+(?:ercot\s+)?review[^0-9]*?(\d[\d,]+)\s*(?:mw|gw)',
        "no_studies_mw": r'no\s+studies\s+submitted[^0-9]*?(\d[\d,]+)\s*(?:mw|gw)',
        "total_large_load_gw": r'(?:total|queue)[^0-9]*?(\d[\d,.]+)\s*gw',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text_lower)
        if match:
            val = match.group(1).replace(",", "")
            try:
                data[key] = float(val)
            except ValueError:
                pass

    return data


# ── 2A.2: Board of Directors Presentations ───────────────────────────────────

def scrape_board_presentations() -> list[ScrapeResult]:
    """
    Scrape ERCOT calendar/website for Board of Directors meeting materials.
    Focus on System Planning and Weatherization Updates which contain
    large load queue breakdowns by type.
    """
    print("[2A.2] Scraping ERCOT Board Presentations...")
    results = []

    soup = fetch_page_soup(ERCOT_CALENDAR_URL)
    if soup is None:
        print("  [ERROR] Could not fetch ERCOT Calendar page")
        return results

    # Find links to Board of Directors meetings
    all_links = extract_all_links(soup, extensions=[".pdf"])
    board_links = [
        l for l in all_links
        if any(kw.lower() in l["text"].lower() or kw.lower() in l["url"].lower()
               for kw in BOARD_PRESENTATION_KEYWORDS)
    ]

    # Also try to find Board meeting landing pages that link to presentations
    meeting_links = [
        l for l in extract_all_links(soup)
        if "board" in l["text"].lower() and "director" in l["text"].lower()
    ]

    # For each meeting page, extract presentation PDFs
    for meeting in meeting_links[:5]:  # limit to recent meetings
        meeting_soup = fetch_page_soup(meeting["url"])
        if meeting_soup is None:
            continue
        meeting_pdfs = extract_pdf_links(meeting_soup)
        for pdf_link in meeting_pdfs:
            if any(kw.lower() in pdf_link["text"].lower() or kw.lower() in pdf_link["url"].lower()
                   for kw in BOARD_PRESENTATION_KEYWORDS):
                board_links.append(pdf_link)

    print(f"  Found {len(board_links)} relevant Board presentation PDFs")

    for link in board_links:
        url = link["url"]
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        local = download_file(url, ERCOT_DOCS_DIR)
        if local is None:
            continue

        text = extract_pdf_text(local, max_pages=30)
        doc_hash = content_hash(text) if text else url_hash
        relevance, matched = score_relevance(text)
        queue_data = _extract_large_load_queue_data(text)

        # Extract load type breakdown if present
        load_breakdown = _extract_load_type_breakdown(text)

        result = ScrapeResult(
            source="ercot_board",
            doc_type="board_presentation",
            title=link["text"] or url.split("/")[-1],
            url=url,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=doc_hash,
            text_excerpt=text[:2000] if text else "",
            relevance_score=relevance,
            matched_keywords=matched,
            metadata={
                "queue_data": queue_data,
                "load_breakdown": load_breakdown,
            },
            local_path=str(local),
            is_new=is_new_content(doc_hash),
        )
        results.append(result)
        mark_seen(url_hash)
        mark_seen(doc_hash)

    print(f"  [OK] {len(results)} new Board presentations processed")
    return results


def _extract_load_type_breakdown(text: str) -> dict:
    """Extract large load breakdown by type (data center, industrial, crypto, O&G)."""
    data = {}
    text_lower = text.lower()

    patterns = {
        "data_center_pct": r'data\s+center[s]?\s*[:\-–]?\s*(?:approximately?\s*)?(\d+)\s*%',
        "data_center_gw": r'data\s+center[s]?\s*[:\-–]?\s*(?:approximately?\s*)?(\d+[\d,.]*)\s*gw',
        "industrial_gw": r'(?:industrial|commercial\s+and\s+industrial|c&i)\s*[:\-–]?\s*(\d+[\d,.]*)\s*gw',
        "crypto_gw": r'(?:crypt[o]?\s*(?:currency|mining)?)\s*[:\-–]?\s*(\d+[\d,.]*)\s*gw',
        "oil_gas_gw": r'(?:oil\s*(?:and|&)\s*gas|o&g)\s*[:\-–]?\s*(\d+[\d,.]*)\s*gw',
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text_lower)
        if match:
            try:
                data[key] = float(match.group(1).replace(",", ""))
            except ValueError:
                pass

    return data


# ── 2A.3: RPG Meeting Materials ──────────────────────────────────────────────

def scrape_rpg_materials() -> list[ScrapeResult]:
    """
    Scrape ERCOT calendar for RPG meeting materials.
    RPG reviews all significant transmission projects before Board endorsement.
    """
    print("[2A.3] Scraping ERCOT RPG Meeting Materials...")
    results = []

    soup = fetch_page_soup(ERCOT_CALENDAR_URL)
    if soup is None:
        return results

    # Find RPG meeting pages
    all_links = extract_all_links(soup)
    rpg_pages = [
        l for l in all_links
        if any(kw.lower() in l["text"].lower() for kw in RPG_KEYWORDS)
    ]

    for meeting in rpg_pages[:5]:
        meeting_soup = fetch_page_soup(meeting["url"])
        if meeting_soup is None:
            continue

        pdf_links = extract_pdf_links(meeting_soup)
        for link in pdf_links:
            url = link["url"]
            url_hash = content_hash(url)
            if not is_new_content(url_hash):
                continue

            local = download_file(url, ERCOT_DOCS_DIR)
            if local is None:
                continue

            text = extract_pdf_text(local, max_pages=20)
            doc_hash = content_hash(text) if text else url_hash
            relevance, matched = score_relevance(text)

            # Only keep if moderately relevant
            if relevance < 0.05:
                mark_seen(url_hash)
                continue

            result = ScrapeResult(
                source="ercot_rpg",
                doc_type="rpg_presentation",
                title=link["text"] or url.split("/")[-1],
                url=url,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=doc_hash,
                text_excerpt=text[:2000] if text else "",
                relevance_score=relevance,
                matched_keywords=matched,
                metadata={"meeting_title": meeting["text"]},
                local_path=str(local),
                is_new=is_new_content(doc_hash),
            )
            results.append(result)
            mark_seen(url_hash)
            mark_seen(doc_hash)

    print(f"  [OK] {len(results)} new RPG materials processed")
    return results


# ── 2A.4: Large Load Integration Page ────────────────────────────────────────

def scrape_large_load_integration_page() -> list[ScrapeResult]:
    """
    Monitor the ERCOT Large Load Integration page for new/updated documents.
    Tracks: Q&A docs, stand-alone gen resource list, LLIS forms, DWG surveys.
    """
    print("[2A.4] Scraping ERCOT Large Load Integration page...")
    results = []

    soup = fetch_page_soup(ERCOT_LARGE_LOAD_PAGE)
    if soup is None:
        return results

    all_links = extract_all_links(soup, extensions=[".pdf", ".xlsx", ".xls", ".docx", ".doc"])

    for link in all_links:
        url = link["url"]
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        # Download
        local = download_file(url, ERCOT_DOCS_DIR)
        if local is None:
            continue

        # Extract text if PDF
        text = ""
        if str(local).lower().endswith(".pdf"):
            text = extract_pdf_text(local, max_pages=30)

        doc_hash = content_hash(text) if text else content_hash(local.read_bytes())
        relevance, matched = score_relevance(text) if text else (0.3, ["large load"])

        result = ScrapeResult(
            source="ercot_large_load_page",
            doc_type="large_load_document",
            title=link["text"] or url.split("/")[-1],
            url=url,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=doc_hash,
            text_excerpt=text[:2000] if text else f"[Binary file: {local.name}]",
            relevance_score=relevance,
            matched_keywords=matched,
            metadata={"file_type": local.suffix},
            local_path=str(local),
            is_new=is_new_content(doc_hash),
        )
        results.append(result)
        mark_seen(url_hash)
        mark_seen(doc_hash)

    print(f"  [OK] {len(results)} new large load documents processed")
    return results


# ── Entry Point ──────────────────────────────────────────────────────────────

def run_all_2a() -> list[ScrapeResult]:
    """Run all 2A scrapers and return combined results."""
    results = []
    results.extend(scrape_monthly_operational_overviews())
    results.extend(scrape_board_presentations())
    results.extend(scrape_rpg_materials())
    results.extend(scrape_large_load_integration_page())
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_2a()
    print(f"\n[2A TOTAL] {len(all_results)} documents collected")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:60]} | relevance={r.relevance_score:.2f} | new={r.is_new}")
