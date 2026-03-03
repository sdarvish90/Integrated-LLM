"""
Scraper 2B: PUCT SB6 Docket Monitor
=====================================
Monitors the Texas PUC Interchange for:
- SB6 implementation dockets (58317, 58479, 58481, 58482, 58484)
- Major transmission CCN filings (765-kV projects)
- New filings, orders, and stakeholder comments

The PUC Interchange at interchange.puc.texas.gov serves docket filings
as HTML pages with links to PDF/DOC documents.
"""

import re
from datetime import datetime, timezone
from pathlib import Path

from scrapers.utils import (
    fetch_page_soup, fetch_url, download_file, extract_pdf_text,
    content_hash, score_relevance, is_new_content, mark_seen, ScrapeResult,
)
from config.settings import (
    PUC_INTERCHANGE_BASE, PUC_FILING_SEARCH_URL,
    SB6_DOCKETS, TRANSMISSION_CCN_DOCKETS, STORAGE_DIR,
)

PUC_DOCS_DIR = STORAGE_DIR / "puc_filings"


def scrape_docket_filings(control_number: str, docket_name: str,
                           max_filings: int = 20) -> list[ScrapeResult]:
    """
    Scrape filings for a specific PUC control number.

    The PUC Interchange filing page at:
      https://interchange.puc.texas.gov/Search/Filings?ControlNumber=XXXXX
    lists filings in a table with columns: Item, Filed By, Description,
    Filed Date, and links to documents.
    """
    print(f"  Scraping PUC docket {control_number}: {docket_name}...")
    results = []

    url = f"{PUC_INTERCHANGE_BASE}/Search/Filings?ControlNumber={control_number}"
    soup = fetch_page_soup(url)
    if soup is None:
        print(f"    [ERROR] Could not fetch docket {control_number}")
        return results

    # Find the filings table — PUC Interchange uses a table with filing rows
    # Each row typically has: item number, filed by, description, date, document links
    rows = soup.find_all("tr")

    filing_count = 0
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        # Extract filing info from cells
        item_text = cells[0].get_text(strip=True) if len(cells) > 0 else ""
        filed_by = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        description = cells[2].get_text(strip=True) if len(cells) > 2 else ""
        filed_date = cells[3].get_text(strip=True) if len(cells) > 3 else ""

        # Find document links in this row
        doc_links = []
        for a_tag in row.find_all("a", href=True):
            href = a_tag["href"]
            if "/Documents/" in href or href.lower().endswith((".pdf", ".doc", ".docx", ".xlsx")):
                full_url = href if href.startswith("http") else PUC_INTERCHANGE_BASE + href
                doc_links.append({
                    "url": full_url,
                    "text": a_tag.get_text(strip=True),
                })

        if not description and not doc_links:
            continue

        # Build a content identifier for dedup
        filing_id = f"{control_number}_{item_text}_{filed_date}"
        filing_hash = content_hash(filing_id)

        if not is_new_content(filing_hash):
            continue

        # Download the first document (usually the main filing) for text extraction
        text = ""
        local_path = ""
        if doc_links:
            primary_doc = doc_links[0]
            local = download_file(primary_doc["url"], PUC_DOCS_DIR)
            if local:
                local_path = str(local)
                if str(local).lower().endswith(".pdf"):
                    text = extract_pdf_text(local, max_pages=20)

        # Score relevance
        combined_text = f"{description} {filed_by} {text}"
        relevance, matched = score_relevance(combined_text)

        # Parse date
        parsed_date = _parse_puc_date(filed_date)

        result = ScrapeResult(
            source="puc_sb6",
            doc_type="puc_filing",
            title=f"[{control_number}] {description[:100]}" if description else f"[{control_number}] Item {item_text}",
            url=url,
            date=parsed_date,
            content_hash=filing_hash,
            text_excerpt=text[:2000] if text else description,
            relevance_score=relevance,
            matched_keywords=matched,
            metadata={
                "control_number": control_number,
                "docket_name": docket_name,
                "item_number": item_text,
                "filed_by": filed_by,
                "description": description,
                "document_urls": [d["url"] for d in doc_links],
                "document_count": len(doc_links),
            },
            local_path=local_path,
            is_new=True,
        )
        results.append(result)
        mark_seen(filing_hash)

        filing_count += 1
        if filing_count >= max_filings:
            break

    return results


def _parse_puc_date(date_str: str) -> str:
    """Parse PUC date formats to ISO."""
    for fmt in ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d, %Y"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── CCN Filing Search ────────────────────────────────────────────────────────

def search_new_ccn_filings(keywords: list[str] = None) -> list[ScrapeResult]:
    """
    Search PUC Interchange for new CCN filings related to transmission.

    This searches the general filing search with keywords like "CCN" + "transmission"
    to discover new transmission project filings beyond the known dockets.
    """
    print("  Searching PUC for new CCN/transmission filings...")
    results = []

    if keywords is None:
        keywords = ["CCN transmission", "certificate convenience necessity transmission"]

    for kw in keywords:
        search_url = f"{PUC_FILING_SEARCH_URL}?q={kw.replace(' ', '+')}"
        soup = fetch_page_soup(search_url)
        if soup is None:
            continue

        # Extract control numbers from search results
        # The search results page lists dockets with control numbers
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            # Look for links to specific dockets
            cn_match = re.search(r'ControlNumber=(\d{5})', href)
            if cn_match:
                cn = cn_match.group(1)
                # Skip ones we already track
                if cn in SB6_DOCKETS or cn in TRANSMISSION_CCN_DOCKETS:
                    continue

                cn_hash = content_hash(f"ccn_search_{cn}")
                if not is_new_content(cn_hash):
                    continue

                case_text = a_tag.get_text(strip=True)
                relevance, matched = score_relevance(case_text)

                if relevance > 0.05 or "transmission" in case_text.lower():
                    result = ScrapeResult(
                        source="puc_ccn_search",
                        doc_type="ccn_filing",
                        title=f"[{cn}] {case_text[:120]}",
                        url=f"{PUC_INTERCHANGE_BASE}/Search/Filings?ControlNumber={cn}",
                        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        content_hash=cn_hash,
                        text_excerpt=case_text,
                        relevance_score=relevance,
                        matched_keywords=matched,
                        metadata={
                            "control_number": cn,
                            "discovered_via": f"search: {kw}",
                        },
                        is_new=True,
                    )
                    results.append(result)
                    mark_seen(cn_hash)

    return results


# ── Entry Point ──────────────────────────────────────────────────────────────

def run_all_2b() -> list[ScrapeResult]:
    """Run all 2B scrapers: SB6 dockets + CCN dockets + CCN search."""
    results = []

    # Monitor SB6 implementation dockets
    print("[2B.1] Monitoring SB6 implementation dockets...")
    for cn, name in SB6_DOCKETS.items():
        docket_results = scrape_docket_filings(cn, name)
        results.extend(docket_results)
        print(f"    Docket {cn}: {len(docket_results)} new filings")

    # Monitor known transmission CCN dockets
    print("[2B.2] Monitoring known transmission CCN dockets...")
    for cn, name in TRANSMISSION_CCN_DOCKETS.items():
        docket_results = scrape_docket_filings(cn, name, max_filings=10)
        results.extend(docket_results)
        print(f"    Docket {cn}: {len(docket_results)} new filings")

    # Search for new CCN filings
    print("[2B.3] Searching for new CCN filings...")
    ccn_results = search_new_ccn_filings()
    results.extend(ccn_results)
    print(f"    Found {len(ccn_results)} new CCN dockets")

    print(f"  [OK] {len(results)} total PUC results")
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_2b()
    print(f"\n[2B TOTAL] {len(all_results)} filings collected")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:70]} | date={r.date} | relevance={r.relevance_score:.2f}")
