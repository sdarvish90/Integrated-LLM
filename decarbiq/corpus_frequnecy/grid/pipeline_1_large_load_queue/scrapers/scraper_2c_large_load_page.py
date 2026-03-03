"""
Scraper 2C: ERCOT Large Load Integration Page — Deep Monitor
==============================================================
Goes beyond simple document downloading (which 2A already does) to:

1. Track document version changes over time (hash-based diffing)
2. Extract structured data from the Stand-Alone Generation Resources list
   (Excel file mapping generation resources subject to net metering review)
3. Parse the Load Information Form template to understand required fields
4. Monitor ERCOT Market Notices for large load policy changes
5. Track PGRR115 / NPRR1234 implementation status via Planning Guide updates

Key process milestones tracked (per PGRR115 Section 9):
  1. LLIS requested by TSP → ERCOT begins study
  2. Steady-state study completed
  3. Stability study completed
  4. Planning Studies Approved by ERCOT
  5. Interconnection agreements executed
  6. Construction of required facilities
  7. Approval to Energize granted
"""

import re
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scrapers.utils import (
    fetch_page_soup, fetch_url, download_file, extract_pdf_text,
    extract_all_links, content_hash, score_relevance,
    is_new_content, mark_seen, load_seen_hashes, save_seen_hashes,
    ScrapeResult,
)
from config.settings import (
    ERCOT_LARGE_LOAD_PAGE, STORAGE_DIR, OUTPUTS_DIR,
    GULF_COAST_COUNTIES, KNOWN_DEVELOPERS,
)

ERCOT_DOCS_DIR = STORAGE_DIR / "ercot_docs"
VERSION_TRACKER_FILE = STORAGE_DIR / "snapshots" / "doc_versions.json"

# ERCOT Market Notice page
ERCOT_MARKET_NOTICES_URL = "https://www.ercot.com/services/comm/mkt_notices"

# Known document URL patterns on the Large Load Integration page
TRACKED_DOCUMENTS = {
    "ll_qa": {
        "pattern": "Large-Load-Interconnection-Process-Q-A",
        "name": "Large Load Interconnection Process Q&A",
        "type": "pdf",
    },
    "standalone_gen": {
        "pattern": "Stand-Alone-Generation-Resources",
        "name": "Stand-Alone Generation Resources List",
        "type": "xlsx",
    },
    "load_info_form": {
        "pattern": "Load-Information-Form",
        "name": "Load Information Form",
        "type": "xlsx",
    },
    "energization_request": {
        "pattern": "Energization_Request",
        "name": "Energization Request Form",
        "type": "xlsx",
    },
    "net_metering_form": {
        "pattern": "Net-Metering-Arrangement",
        "name": "Net Metering Arrangement Notice Form",
        "type": "xlsx",
    },
    "communication_instructions": {
        "pattern": "Instructions-for-Communication",
        "name": "Instructions for Communication of Large Load Information",
        "type": "pdf",
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# 2C.1: Document Version Tracking
# ══════════════════════════════════════════════════════════════════════════════

def _load_version_tracker() -> dict:
    """Load document version history."""
    if VERSION_TRACKER_FILE.exists():
        return json.loads(VERSION_TRACKER_FILE.read_text())
    return {}


def _save_version_tracker(tracker: dict):
    VERSION_TRACKER_FILE.write_text(json.dumps(tracker, indent=2, default=str))


def track_document_versions() -> list[ScrapeResult]:
    """
    Download all tracked documents from the Large Load Integration page
    and compare content hashes against previous versions to detect changes.
    """
    print("[2C.1] Tracking document versions on Large Load Integration page...")
    results = []

    soup = fetch_page_soup(ERCOT_LARGE_LOAD_PAGE)
    if soup is None:
        print("  [ERROR] Could not fetch Large Load Integration page")
        return results

    all_links = extract_all_links(soup, extensions=[".pdf", ".xlsx", ".xls", ".docx"])
    tracker = _load_version_tracker()
    now = datetime.now(timezone.utc).isoformat()

    for doc_key, doc_info in TRACKED_DOCUMENTS.items():
        # Find matching link
        matching = [l for l in all_links if doc_info["pattern"].lower() in l["url"].lower()]
        if not matching:
            print(f"  [WARN] Document not found: {doc_info['name']}")
            continue

        link = matching[0]
        url = link["url"]

        # Download
        local = download_file(url, ERCOT_DOCS_DIR)
        if local is None:
            continue

        # Compute content hash
        file_bytes = local.read_bytes()
        current_hash = content_hash(file_bytes)

        # Compare with previous version
        prev = tracker.get(doc_key, {})
        prev_hash = prev.get("hash", "")
        is_changed = current_hash != prev_hash
        version_num = prev.get("version", 0) + (1 if is_changed else 0)

        # Update tracker
        tracker[doc_key] = {
            "name": doc_info["name"],
            "url": url,
            "hash": current_hash,
            "version": version_num,
            "last_checked": now,
            "last_changed": now if is_changed else prev.get("last_changed", now),
            "local_path": str(local),
        }

        if is_changed:
            # Extract text for relevance scoring
            text = ""
            if doc_info["type"] == "pdf":
                text = extract_pdf_text(local, max_pages=30)

            relevance, matched = score_relevance(text) if text else (0.3, ["large load"])

            result = ScrapeResult(
                source="ercot_ll_version_track",
                doc_type=f"version_change_{doc_info['type']}",
                title=f"[UPDATED v{version_num}] {doc_info['name']}",
                url=url,
                date=now[:10],
                content_hash=current_hash,
                text_excerpt=text[:2000] if text else f"[Binary: {local.name}]",
                relevance_score=relevance,
                matched_keywords=matched,
                metadata={
                    "doc_key": doc_key,
                    "version": version_num,
                    "previous_hash": prev_hash,
                    "change_detected": True,
                },
                local_path=str(local),
                is_new=True,
            )
            results.append(result)
            print(f"  [CHANGED] {doc_info['name']} → version {version_num}")
        else:
            print(f"  [OK] {doc_info['name']} — no changes (v{version_num})")

    _save_version_tracker(tracker)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 2C.2: Stand-Alone Generation Resources List Parsing
# ══════════════════════════════════════════════════════════════════════════════

def parse_standalone_gen_resources() -> list[ScrapeResult]:
    """
    Parse the Stand-Alone Generation Resources Excel file.
    
    This list identifies generation resources registered with ERCOT as of
    Sept 1, 2025 that are subject to the net metering review process under
    PURA §39.169. If a Large Load wants to co-locate with one of these
    generators, it must go through additional ERCOT/PUCT review.

    This is valuable intelligence because:
    - It maps existing generation that could be paired with new H2/ammonia loads
    - Co-location with existing gas generation is a common blue H2 strategy
    - Changes to this list signal new net metering arrangements being proposed
    """
    print("[2C.2] Parsing Stand-Alone Generation Resources list...")
    results = []

    soup = fetch_page_soup(ERCOT_LARGE_LOAD_PAGE)
    if soup is None:
        return results

    all_links = extract_all_links(soup, extensions=[".xlsx", ".xls"])
    gen_links = [l for l in all_links if "stand-alone" in l["url"].lower()
                 or "generation-resources" in l["url"].lower()]

    if not gen_links:
        print("  [WARN] Stand-Alone Gen Resources file not found")
        return results

    url = gen_links[0]["url"]
    local = download_file(url, ERCOT_DOCS_DIR, filename="standalone_gen_resources.xlsx")
    if local is None:
        return results

    try:
        df = pd.read_excel(local, engine="openpyxl")
        print(f"  Parsed {len(df)} generation resources")

        # Standardize column names
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

        # Look for resources in Gulf Coast counties
        county_col = None
        for col in df.columns:
            if "county" in col:
                county_col = col
                break

        gulf_coast_resources = pd.DataFrame()
        if county_col:
            gulf_coast_resources = df[
                df[county_col].astype(str).str.strip().isin(GULF_COAST_COUNTIES)
            ]
            print(f"  Found {len(gulf_coast_resources)} resources in Gulf Coast counties")

        # Look for resources owned by known H2/ammonia developers
        owner_col = None
        for col in df.columns:
            if any(kw in col for kw in ["owner", "entity", "company", "resource_entity"]):
                owner_col = col
                break

        developer_matches = pd.DataFrame()
        if owner_col:
            for dev in KNOWN_DEVELOPERS:
                matches = df[df[owner_col].astype(str).str.contains(dev, case=False, na=False)]
                if not matches.empty:
                    developer_matches = pd.concat([developer_matches, matches])

            if not developer_matches.empty:
                developer_matches = developer_matches.drop_duplicates()
                print(f"  Found {len(developer_matches)} resources owned by known developers")

        # Create summary result
        file_hash = content_hash(local.read_bytes())

        summary_data = {
            "total_resources": len(df),
            "columns": list(df.columns),
            "gulf_coast_count": len(gulf_coast_resources),
            "developer_match_count": len(developer_matches),
        }

        # Add Gulf Coast resource details
        if not gulf_coast_resources.empty:
            summary_data["gulf_coast_resources"] = gulf_coast_resources.head(50).to_dict(orient="records")

        if not developer_matches.empty:
            summary_data["developer_matched_resources"] = developer_matches.head(20).to_dict(orient="records")

        # Save parsed data
        parsed_output = OUTPUTS_DIR / "standalone_gen_resources_parsed.json"
        parsed_output.write_text(json.dumps(summary_data, indent=2, default=str))

        result = ScrapeResult(
            source="ercot_standalone_gen",
            doc_type="generation_resource_list",
            title=f"Stand-Alone Gen Resources: {len(df)} total, {len(gulf_coast_resources)} Gulf Coast",
            url=url,
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            content_hash=file_hash,
            text_excerpt=f"Total: {len(df)} resources. Gulf Coast: {len(gulf_coast_resources)}. "
                         f"Developer matches: {len(developer_matches)}. "
                         f"Columns: {', '.join(df.columns[:10])}",
            relevance_score=0.6,
            matched_keywords=["large load", "generation resource", "net metering"],
            metadata=summary_data,
            local_path=str(local),
            is_new=is_new_content(file_hash),
        )
        results.append(result)
        mark_seen(file_hash)

    except Exception as e:
        print(f"  [ERROR] Failed to parse Gen Resources: {e}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# 2C.3: ERCOT Market Notices Monitoring
# ══════════════════════════════════════════════════════════════════════════════

def scrape_market_notices() -> list[ScrapeResult]:
    """
    Monitor ERCOT Market Notices for large load policy changes,
    PGRR/NPRR implementations, and planning guide updates.

    Market notices are published at:
    https://www.ercot.com/services/comm/mkt_notices

    Key notice types to watch:
    - M-A (Market Administration) — process changes
    - M-C (Market Credit) — financial requirements
    - Implementation notices for PGRR115, NPRR1234
    """
    print("[2C.3] Scraping ERCOT Market Notices...")
    results = []

    soup = fetch_page_soup(ERCOT_MARKET_NOTICES_URL)
    if soup is None:
        print("  [WARN] Could not fetch Market Notices page")
        return results

    # Market notices page contains links to notice PDFs or pages
    all_links = extract_all_links(soup)

    # Filter for large load and planning guide related notices
    keywords = [
        "large load", "PGRR115", "NPRR1234", "planning guide",
        "section 9", "interconnection", "75 mw", "load interconnection",
        "sb 6", "sb6", "senate bill 6",
    ]

    relevant_links = []
    for link in all_links:
        combined = f"{link['text']} {link['url']}".lower()
        if any(kw.lower() in combined for kw in keywords):
            relevant_links.append(link)

    print(f"  Found {len(relevant_links)} potentially relevant market notices")

    for link in relevant_links[:20]:
        url = link["url"]
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        # Try to get the notice content
        text = link["text"]
        local_path = ""

        if url.lower().endswith(".pdf"):
            local = download_file(url, ERCOT_DOCS_DIR)
            if local:
                text = extract_pdf_text(local, max_pages=5) or text
                local_path = str(local)
        elif not url.startswith("mailto:"):
            resp = fetch_url(url)
            if resp and "text/html" in resp.headers.get("content-type", ""):
                from bs4 import BeautifulSoup
                notice_soup = BeautifulSoup(resp.text, "lxml")
                body = notice_soup.find("body")
                if body:
                    text = body.get_text(separator=" ", strip=True)[:3000]

        relevance, matched = score_relevance(text)
        doc_hash = content_hash(text)

        if relevance >= 0.05:
            result = ScrapeResult(
                source="ercot_market_notice",
                doc_type="market_notice",
                title=link["text"][:120] or "Market Notice",
                url=url,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=doc_hash,
                text_excerpt=text[:2000],
                relevance_score=relevance,
                matched_keywords=matched,
                metadata={"notice_text": link["text"]},
                local_path=local_path,
                is_new=is_new_content(doc_hash),
            )
            results.append(result)
            mark_seen(url_hash)
            mark_seen(doc_hash)

    print(f"  [OK] {len(results)} relevant market notices")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# 2C.4: PGRR115 / Planning Guide Section 9 Status Tracker
# ══════════════════════════════════════════════════════════════════════════════

def track_planning_guide_updates() -> list[ScrapeResult]:
    """
    Check for updates to ERCOT Planning Guide, specifically Section 9
    (Large Load Interconnection process) introduced by PGRR115.

    Planning Guide is posted at:
    https://www.ercot.com/mktrules/guides/planning
    """
    print("[2C.4] Checking Planning Guide updates...")
    results = []

    planning_guide_url = "https://www.ercot.com/mktrules/guides/planning"
    soup = fetch_page_soup(planning_guide_url)
    if soup is None:
        # Try alternative URL
        soup = fetch_page_soup("https://www.ercot.com/mktrules/guides")
        if soup is None:
            print("  [WARN] Could not fetch Planning Guide page")
            return results

    # Find links to Planning Guide PDFs
    all_links = extract_all_links(soup, extensions=[".pdf"])
    pg_links = [l for l in all_links if "planning" in l["text"].lower()
                and "guide" in l["text"].lower()]

    # Also look for revision requests (PGRRs)
    pgrr_links = [l for l in all_links if "PGRR" in l["text"] or "pgrr" in l["url"].lower()]

    for link in (pg_links + pgrr_links)[:10]:
        url = link["url"]
        url_hash = content_hash(url)
        if not is_new_content(url_hash):
            continue

        local = download_file(url, ERCOT_DOCS_DIR)
        if local is None:
            continue

        text = ""
        if str(local).lower().endswith(".pdf"):
            text = extract_pdf_text(local, max_pages=10)

        # Check if this relates to large load / Section 9
        if text:
            relevance, matched = score_relevance(text)
            # Boost if it mentions Section 9 or PGRR115
            if "section 9" in text.lower() or "pgrr115" in text.lower() or "pgrr 115" in text.lower():
                relevance = min(relevance + 0.3, 1.0)
                matched.append("Section 9 / PGRR115")
        else:
            relevance, matched = 0.1, []

        if relevance >= 0.1:
            doc_hash = content_hash(text) if text else content_hash(local.read_bytes())
            result = ScrapeResult(
                source="ercot_planning_guide",
                doc_type="planning_guide_update",
                title=link["text"][:120] or "Planning Guide Document",
                url=url,
                date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                content_hash=doc_hash,
                text_excerpt=text[:2000] if text else f"[File: {local.name}]",
                relevance_score=relevance,
                matched_keywords=matched,
                metadata={"document_type": "planning_guide" if "guide" in link["text"].lower() else "pgrr"},
                local_path=str(local),
                is_new=is_new_content(doc_hash),
            )
            results.append(result)
            mark_seen(url_hash)
            mark_seen(doc_hash)

    print(f"  [OK] {len(results)} planning guide updates")
    return results


# ── Entry Point ──────────────────────────────────────────────────────────────

def run_all_2c() -> list[ScrapeResult]:
    """Run all 2C scrapers."""
    results = []
    results.extend(track_document_versions())
    results.extend(parse_standalone_gen_resources())
    results.extend(scrape_market_notices())
    results.extend(track_planning_guide_updates())
    return results


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    all_results = run_all_2c()
    print(f"\n[2C TOTAL] {len(all_results)} items collected")
    for r in all_results:
        print(f"  [{r.source}] {r.title[:70]} | relevance={r.relevance_score:.2f}")
