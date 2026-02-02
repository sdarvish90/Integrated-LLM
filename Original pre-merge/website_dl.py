#!/usr/bin/env python3
from __future__ import annotations

import os
import re
import json
import time
import hashlib
import urllib.parse
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timedelta

import requests
import yaml
from bs4 import BeautifulSoup


# -------------------------
# SOURCE DISCOVERY CONFIG
# -------------------------
@dataclass
class DiscoveryConfig:
    """Configuration for automatic source discovery."""
    enabled: bool = True
    max_new_sources_per_run: int = 10
    search_delay: float = 2.0  # seconds between searches
    
    # Countries/regions to research
    target_regions: List[str] = field(default_factory=lambda: ["Oman", "Chile"])
    
    # Topics to search for
    topics: List[str] = field(default_factory=lambda: [
        "hydrogen strategy",
        "green hydrogen",
        "renewable energy report",
        "energy outlook",
        "electricity sector report",
        "ammonia production",
        "national energy plan",
        "hidrogeno verde",  # Spanish
        "energía renovable",  # Spanish
        "estrategia energetica",  # Spanish
    ])
    
    # TRUSTED DOMAINS - accept pages from these
    trusted_domains: List[str] = field(default_factory=lambda: [
        # International Organizations
        "iea.org",
        "irena.org",
        "worldbank.org",
        "ifc.org",
        "adb.org",
        "ebrd.com",
        "oecd.org",
        "un.org", "unido.org", "unctad.org",
        "imf.org",
        "weforum.org",
        
        # Government domains (by pattern)
        ".gov", ".gob",  # US, Latin America gov
        ".go.",  # Various gov (e.g., .go.jp)
        ".govt.",  # NZ, etc.
        ".gc.ca",  # Canada
        ".europa.eu",
        
        # Oman specific
        "hydrom.om",
        "oemo.om",
        "apsr.om",
        "omanpwp.om",
        "mep.gov.om",
        "ncsi.gov.om",
        "omanobserver.om",
        "timesofoman.com",
        
        # Chile specific
        "energia.gob.cl",
        "cne.cl",
        "coordinador.cl",
        "minenergia.cl",
        "corfo.cl",
        "generadoras.cl",
        "planhidrogenoverde.cl",
        "h2chile.cl",
        "4echile.cl",
        
        # Research & Academia
        "nrel.gov",
        "sandia.gov",
        "pnnl.gov",
        "researchgate.net",
        "sciencedirect.com",
        "springer.com",
        "nature.com",
        "mdpi.com",
        
        # Industry associations
        "hydrogencouncil.com",
        "gh2.org",
        "fuelcellsworks.com",
        "h2-view.com",
        "hydrogeninsight.com",
        "rechargenews.com",
        
        # Energy think tanks
        "energypolicy.columbia.edu",
        "kapsarc.org",
        "oxfordenergy.org",
        "rmi.org",
        "bnef.com",
        "woodmac.com",
        "spglobal.com",
        
        # Regional energy bodies
        "olade.org",  # Latin America
        "afrec-energy.org",  # Africa
        "aseanenergy.org",  # ASEAN
        "erranet.org",  # Energy regulators
    ])
    
    # BLOCKED DOMAINS - never accept from these
    blocked_domains: List[str] = field(default_factory=lambda: [
        "facebook.com",
        "twitter.com", "x.com",
        "linkedin.com",
        "instagram.com",
        "youtube.com",
        "tiktok.com",
        "reddit.com",
        "pinterest.com",
        "medium.com",  # User-generated
        "blogspot.com",
        "scribd.com",  # Paywalled/user-uploaded
        "slideshare.net",
        "academia.edu",  # Requires login
        "quora.com",
        "wikipedia.org",  # Not primary source
        "amazon.com",
        "ebay.com",
    ])
    
    # Keywords that indicate a report/publication page
    report_indicators: List[str] = field(default_factory=lambda: [
        "report", "publication", "study", "analysis", "outlook",
        "strategy", "roadmap", "plan", "whitepaper", "white paper",
        "assessment", "review", "survey", "brief", "paper",
        "informe", "publicacion", "estudio", "estrategia",  # Spanish
        "documento", "reporte",  # Spanish
    ])
    
    # Keywords that indicate download availability
    download_indicators: List[str] = field(default_factory=lambda: [
        "download", "pdf", "read more", "full report", "access",
        "view report", "get the report", "read the report",
        "descargar", "leer más", "ver informe",  # Spanish
        "documento completo", "acceder",  # Spanish
    ])
    
    # Year filter - only recent publications
    min_year: int = 2020


def is_trusted_domain(url: str, config: DiscoveryConfig) -> bool:
    """Check if URL is from a trusted domain."""
    url_lower = url.lower()
    parsed = urllib.parse.urlparse(url_lower)
    domain = parsed.netloc
    
    # Check blocked first
    for blocked in config.blocked_domains:
        if blocked in domain:
            return False
    
    # Check trusted
    for trusted in config.trusted_domains:
        if trusted in domain or domain.endswith(trusted):
            return True
    
    return False


def looks_like_report_page(url: str, title: str, snippet: str, config: DiscoveryConfig) -> bool:
    """
    Check if URL/title/snippet looks like a report or publication page.
    More lenient than just checking for PDF links.
    """
    combined = (url + " " + title + " " + snippet).lower()
    
    # Check for report indicators
    has_report_indicator = any(ind in combined for ind in config.report_indicators)
    
    # Check for download indicators
    has_download_indicator = any(ind in combined for ind in config.download_indicators)
    
    # Direct PDF link
    is_direct_pdf = ".pdf" in url.lower()
    
    # Publication-style URL patterns
    pub_patterns = [
        "/publication", "/report", "/document", "/study",
        "/publications/", "/reports/", "/documents/",
        "/informe", "/publicacion", "/documento",
        "/news/", "/press/", "/media/",
        "/resources/", "/library/",
    ]
    has_pub_url = any(pat in url.lower() for pat in pub_patterns)
    
    return is_direct_pdf or has_report_indicator or has_download_indicator or has_pub_url


def extract_year_from_text(text: str) -> Optional[int]:
    """Extract publication year from text."""
    # Look for 4-digit years
    years = re.findall(r"20[12][0-9]", text)
    if years:
        return max(int(y) for y in years)  # Return most recent
    return None


def build_search_queries(config: DiscoveryConfig) -> List[str]:
    """Build search queries for source discovery."""
    queries = []
    current_year = datetime.now().year
    
    for region in config.target_regions:
        for topic in config.topics:
            # Basic query
            queries.append(f"{region} {topic}")
            # With year
            queries.append(f"{region} {topic} {current_year}")
            queries.append(f"{region} {topic} {current_year - 1}")
            # Government specific
            queries.append(f"{region} government {topic}")
            queries.append(f"{region} ministry energy {topic}")
    
    # Add specific organization queries
    org_queries = [
        "IEA hydrogen report",
        "IEA energy outlook",
        "IRENA renewable energy report",
        "IRENA hydrogen",
        "IRENA World Energy Transitions Outlook",
        "World Bank energy transition",
        "HYDROM Oman news",
        "HYDROM Oman report",
        "Chile ministerio energia hidrogeno",
        "Chile green hydrogen news",
        "green hydrogen strategy 2024",
        "green hydrogen strategy 2025",
        "hydrogen economy report",
        "ammonia energy report",
        "clean hydrogen production",
    ]
    queries.extend(org_queries)
    
    return queries


def discover_sources_from_search(
    session: requests.Session,
    query: str,
    config: DiscoveryConfig,
    existing_urls: Set[str],
) -> List[Dict]:
    """
    Discover new report/publication sources using web search.
    Returns list of {url, title, snippet, source_type} dicts.
    
    Looks for report PAGES (not just PDFs) that likely have downloadable content.
    """
    discovered = []
    
    # Use DuckDuckGo HTML (no API key needed)
    search_url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = session.get(search_url, headers=headers, timeout=30)
        r.raise_for_status()
        
        soup = BeautifulSoup(r.text, "html.parser")
        
        # DuckDuckGo results
        for result in soup.select(".result"):
            link_el = result.select_one(".result__a")
            snippet_el = result.select_one(".result__snippet")
            
            if not link_el:
                continue
                
            href = link_el.get("href", "")
            title = link_el.get_text(strip=True)
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            
            if not href:
                continue
            
            # DuckDuckGo wraps URLs - extract actual URL
            if "uddg=" in href:
                match = re.search(r"uddg=([^&]+)", href)
                if match:
                    href = urllib.parse.unquote(match.group(1))
            
            # Must be from trusted domain
            if not is_trusted_domain(href, config):
                continue
            
            # Check not already known
            # Normalize URL for comparison
            normalized = href.split("?")[0].rstrip("/")
            if href in existing_urls or normalized in existing_urls:
                continue
            
            # Check if it looks like a report/publication page
            if not looks_like_report_page(href, title, snippet, config):
                continue
            
            # Check year if extractable
            year = extract_year_from_text(title + " " + snippet + " " + href)
            if year and year < config.min_year:
                continue
            
            # Determine source type
            if ".pdf" in href.lower():
                source_type = "direct_pdf"
            else:
                source_type = "generic_scraper"
            
            discovered.append({
                "url": href,
                "title": title[:200],
                "snippet": snippet[:300],
                "query": query,
                "source_type": source_type,
            })
            
    except Exception as e:
        print(f"  Search error for '{query}': {e}")
    
    return discovered


def verify_page_accessible(session: requests.Session, url: str) -> Tuple[bool, str]:
    """
    Verify page is accessible and try to determine if it has downloadable content.
    Returns (is_valid, source_type)
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        r = session.get(url, headers=headers, timeout=20)
        
        if r.status_code != 200:
            return False, ""
        
        ctype = r.headers.get("Content-Type", "").lower()
        
        # Direct PDF
        if "pdf" in ctype:
            return True, "direct_pdf"
        
        # HTML page - check for download links
        if "html" in ctype or "text" in ctype:
            html_lower = r.text.lower()
            
            # Look for download indicators in the page
            download_patterns = [
                r'href="[^"]*\.pdf"',
                r"href='[^']*\.pdf'",
                r'download',
                r'descargar',
                r'full.?report',
                r'read.?more',
                r'view.?report',
                r'get.?the.?report',
            ]
            
            has_download = any(re.search(pat, html_lower) for pat in download_patterns)
            
            if has_download:
                return True, "generic_scraper"
            
            # Even without obvious download, if it's from a trusted pub domain, keep it
            if any(x in url.lower() for x in ["/publication", "/report", "/document"]):
                return True, "generic_scraper"
        
        return False, ""
        
    except Exception:
        return False, ""


def run_source_discovery(
    session: requests.Session,
    yaml_path: str,
    config: Optional[DiscoveryConfig] = None,
) -> List[Dict]:
    """
    Run source discovery and return newly found sources.
    Does NOT modify the YAML file - returns candidates for review.
    """
    if config is None:
        config = DiscoveryConfig()
    
    if not config.enabled:
        return []
    
    print("\n" + "=" * 60)
    print("SOURCE DISCOVERY")
    print("=" * 60)
    
    # Load existing URLs from YAML
    existing_urls: Set[str] = set()
    try:
        with open(yaml_path, "r", encoding="utf-8") as f:
            yaml_data = yaml.safe_load(f)
        
        for site in yaml_data.get("sites", []):
            for url in site.get("start_urls", []):
                existing_urls.add(url)
                # Also add normalized version
                existing_urls.add(url.split("?")[0].rstrip("/"))
                
    except Exception as e:
        print(f"Warning: Could not load YAML: {e}")
    
    print(f"Existing sources in YAML: {len(existing_urls)}")
    print(f"Target regions: {', '.join(config.target_regions)}")
    
    # Build queries
    queries = build_search_queries(config)
    print(f"Search queries to run: {len(queries)}")
    
    # Discover
    all_discovered: List[Dict] = []
    seen_urls: Set[str] = set(existing_urls)
    
    for i, query in enumerate(queries):
        if len(all_discovered) >= config.max_new_sources_per_run:
            print(f"\nReached max new sources limit ({config.max_new_sources_per_run})")
            break
        
        print(f"\n  [{i+1}/{len(queries)}] Searching: {query[:50]}...")
        
        found = discover_sources_from_search(session, query, config, seen_urls)
        
        for item in found:
            url = item["url"]
            normalized = url.split("?")[0].rstrip("/")
            
            if url in seen_urls or normalized in seen_urls:
                continue
            
            # Verify page is accessible and has content
            print(f"    Checking: {item['title'][:50]}...")
            is_valid, source_type = verify_page_accessible(session, url)
            
            if is_valid:
                item["source_type"] = source_type
                all_discovered.append(item)
                seen_urls.add(url)
                seen_urls.add(normalized)
                print(f"    ✓ Found [{source_type}]: {item['title'][:50]}...")
                
                if len(all_discovered) >= config.max_new_sources_per_run:
                    break
            else:
                print(f"    ✗ Skipped (not accessible or no downloads)")
        
        time.sleep(config.search_delay)
    
    print(f"\n{'=' * 60}")
    print(f"Discovered {len(all_discovered)} new source(s)")
    print("=" * 60)
    
    return all_discovered


def add_discovered_to_yaml(
    yaml_path: str,
    discovered: List[Dict],
    auto_add: bool = False,
) -> None:
    """
    Add discovered sources to YAML file.
    If auto_add=False, just prints what would be added.
    """
    if not discovered:
        print("No new sources to add.")
        return
    
    # Load existing YAML
    with open(yaml_path, "r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)
    
    # Group by inferred region/category
    new_entries = []
    for item in discovered:
        url = item["url"]
        title = item["title"]
        source_type = item.get("source_type", "generic_scraper")
        
        # Infer region from URL or title
        region = "Other"
        url_lower = url.lower()
        title_lower = title.lower()
        
        if any(x in url_lower or x in title_lower for x in ["oman", ".om/", "hydrom", "muscat"]):
            region = "Oman"
        elif any(x in url_lower or x in title_lower for x in ["chile", ".cl/", "santiago", "hidrogeno"]):
            region = "Chile"
        elif any(x in url_lower for x in ["iea.org", "irena.org", "worldbank"]):
            region = "International"
        
        entry = {
            "name": f"[AUTO] {title[:80]}",
            "type": source_type,
            "start_urls": [url],
            "_discovered": datetime.now().isoformat(),
            "_region": region,
        }
        
        # Add max_items for scrapers
        if source_type == "generic_scraper":
            entry["max_items"] = 20
        
        new_entries.append(entry)
    
    if auto_add:
        # Add to YAML
        yaml_data["sites"].extend(new_entries)
        
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(yaml_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        
        print(f"\n✓ Added {len(new_entries)} new sources to {yaml_path}")
        for entry in new_entries:
            print(f"  + [{entry['_region']}] {entry['name']}")
    else:
        # Just print
        print("\n" + "-" * 60)
        print("DISCOVERED SOURCES (not auto-added)")
        print("Run with --auto-add to add these to YAML")
        print("-" * 60)
        
        for entry in new_entries:
            print(f"\n  [{entry['_region']}] {entry['name']}")
            print(f"    Type: {entry['type']}")
            print(f"    URL: {entry['start_urls'][0]}")
        
        print("\n" + "-" * 60)
        print("To auto-add these sources, run with: --discover --auto-add")
        print("-" * 60)


# -------------------------
# Requests headers (browser-like)
# -------------------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

SLEEP = 0.4
STATE_FILE = "_state.json"


# -------------------------
# DUPLICATE DETECTION - SCAN EXISTING FILES
# -------------------------
ADDITIONAL_SCAN_DIRS = [
    "/Users/Shadi/Dropbox/SHARE_Model_LLM/Search Engine/pdf",
    "/Users/Shadi/Dropbox/SHARE_Model_LLM/Search Engine/data",
]


def scan_existing_files_for_duplicates(out_dir: str) -> set:
    """
    Scan existing files in output directory and additional directories.
    Returns a set of file content hashes to check against before downloading.
    """
    existing_hashes = set()
    
    # Directories to scan
    dirs_to_scan = [out_dir] + ADDITIONAL_SCAN_DIRS
    
    print("\n" + "=" * 60)
    print("SCANNING EXISTING FILES FOR DUPLICATE DETECTION")
    print("=" * 60)
    
    for scan_dir in dirs_to_scan:
        if not os.path.exists(scan_dir):
            print(f"  [SKIP] Directory not found: {scan_dir}")
            continue
        
        file_count = 0
        print(f"  Scanning: {scan_dir}")
        
        for root, dirs, files in os.walk(scan_dir):
            for filename in files:
                if filename.startswith('.') or filename == STATE_FILE:
                    continue
                
                filepath = os.path.join(root, filename)
                
                try:
                    with open(filepath, 'rb') as f:
                        file_hash = hashlib.sha256(f.read()).hexdigest()
                    existing_hashes.add(file_hash)
                    file_count += 1
                except (OSError, PermissionError):
                    pass
        
        print(f"    Found {file_count} files")
    
    print(f"\nTotal unique file hashes: {len(existing_hashes)}")
    print("=" * 60 + "\n")
    
    return existing_hashes


# Global variable to store existing hashes (populated at startup)
EXISTING_FILE_HASHES = set()


# -------------------------
# Data Extraction Functions
# -------------------------
def extract_tables_from_html(html: str, page_url: str) -> List[Dict]:
    """
    Extract all tables from HTML page.
    Returns list of dicts with 'headers' and 'rows'.
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = []
    
    for table in soup.select("table"):
        # Skip navigation/layout tables
        if table.get("role") == "presentation":
            continue
        
        # Extract headers
        headers = []
        header_row = table.select_one("thead tr") or table.select_one("tr")
        if header_row:
            for th in header_row.select("th, td"):
                headers.append(th.get_text(" ", strip=True))
        
        # Extract rows
        rows = []
        tbody = table.select_one("tbody") or table
        for tr in tbody.select("tr"):
            # Skip header row if we already got it
            if tr.select("th") and not tr.select("td"):
                continue
            
            row = []
            for td in tr.select("td, th"):
                row.append(td.get_text(" ", strip=True))
            
            if row and len(row) > 0:
                rows.append(row)
        
        if rows:  # Only add tables with data
            tables.append({
                "headers": headers,
                "rows": rows,
                "row_count": len(rows),
            })
    
    return tables


def find_data_export_links(html: str, page_url: str) -> List[Tuple[str, str]]:
    """
    Find export/download links for data (CSV, Excel, JSON, XML).
    Returns list of (label, url) tuples.
    """
    soup = BeautifulSoup(html, "html.parser")
    exports = []
    
    # Common export link patterns
    export_patterns = [
        # By href content
        r"\.(csv|xlsx|xls|json|xml|tsv)(\?|$|#)",
        r"export", r"download", r"format=csv", r"format=excel",
        r"type=csv", r"type=excel", r"output=csv",
    ]
    
    # By link text
    export_texts = [
        "download", "export", "csv", "excel", "xlsx", "xls",
        "full data", "all data", "download data", "export data",
        "descargar", "exportar",  # Spanish
    ]
    
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        text = (a.get_text(" ", strip=True) or "").lower()
        aria = (a.get("aria-label") or "").lower()
        title = (a.get("title") or "").lower()
        
        if not href:
            continue
        
        href_lower = href.lower()
        combined_text = f"{text} {aria} {title}"
        
        # Check patterns
        is_export = False
        
        # Check href patterns
        for pattern in export_patterns:
            if re.search(pattern, href_lower, re.I):
                is_export = True
                break
        
        # Check text patterns
        if not is_export:
            for exp_text in export_texts:
                if exp_text in combined_text:
                    is_export = True
                    break
        
        if is_export:
            full_url = norm_url(page_url, href)
            label = text or aria or title or "export"
            exports.append((label[:100], full_url))
    
    # Also check buttons with data attributes
    for btn in soup.select("button[data-href], button[data-url], [onclick]"):
        text = (btn.get_text(" ", strip=True) or "").lower()
        
        if any(x in text for x in export_texts):
            # Try to extract URL from data attributes or onclick
            href = btn.get("data-href") or btn.get("data-url") or ""
            
            if not href:
                onclick = btn.get("onclick") or ""
                match = re.search(r"['\"]([^'\"]+\.(csv|xlsx|xls|json))['\"]", onclick, re.I)
                if match:
                    href = match.group(1)
            
            if href:
                full_url = norm_url(page_url, href)
                exports.append((text[:100], full_url))
    
    # Dedupe
    seen = set()
    deduped = []
    for label, url in exports:
        if url not in seen:
            seen.add(url)
            deduped.append((label, url))
    
    return deduped


def find_pagination_links(html: str, page_url: str) -> List[str]:
    """
    Find pagination links (next page, page numbers).
    Returns list of URLs for other pages.
    """
    soup = BeautifulSoup(html, "html.parser")
    pages = []
    
    # Common pagination selectors
    pagination_selectors = [
        ".pagination a",
        ".pager a",
        "[class*='pagination'] a",
        "[class*='paging'] a",
        "nav[aria-label*='pagination'] a",
        ".page-numbers a",
        ".page-link",
    ]
    
    for selector in pagination_selectors:
        for a in soup.select(selector):
            href = a.get("href", "").strip()
            if href and href != "#":
                pages.append(norm_url(page_url, href))
    
    # Also look for "next" links
    next_patterns = ["next", "siguiente", ">>", "›", "→"]
    for a in soup.select("a[href]"):
        text = (a.get_text(" ", strip=True) or "").lower()
        rel = (a.get("rel") or "").lower() if isinstance(a.get("rel"), str) else ""
        aria = (a.get("aria-label") or "").lower()
        
        combined = f"{text} {rel} {aria}"
        
        if any(p in combined for p in next_patterns):
            href = a.get("href", "").strip()
            if href and href != "#":
                pages.append(norm_url(page_url, href))
    
    # Dedupe while preserving order
    seen = set()
    deduped = []
    for url in pages:
        if url not in seen and url != page_url:
            seen.add(url)
            deduped.append(url)
    
    return deduped


def scrape_all_table_pages(
    session: requests.Session,
    start_url: str,
    max_pages: int = 50,
) -> Tuple[List[Dict], List[Tuple[str, str]]]:
    """
    Scrape all pages of a data table, following pagination.
    Returns (all_tables, export_links).
    """
    all_tables: List[Dict] = []
    all_exports: List[Tuple[str, str]] = []
    visited: Set[str] = set()
    to_visit: List[str] = [start_url]
    
    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        
        if url in visited:
            continue
        visited.add(url)
        
        try:
            html = fetch_html(session, url, referer=start_url)
        except Exception as e:
            print(f"    Failed to fetch {url}: {e}")
            continue
        
        # Extract tables from this page
        tables = extract_tables_from_html(html, url)
        for t in tables:
            t["source_url"] = url
            t["page_number"] = len(visited)
        all_tables.extend(tables)
        
        # Find export links
        exports = find_data_export_links(html, url)
        all_exports.extend(exports)
        
        # Find pagination links
        if len(visited) < max_pages:
            next_pages = find_pagination_links(html, url)
            for next_url in next_pages:
                if next_url not in visited and next_url not in to_visit:
                    to_visit.append(next_url)
        
        time.sleep(SLEEP)
    
    # Dedupe exports
    seen = set()
    deduped_exports = []
    for label, url in all_exports:
        if url not in seen:
            seen.add(url)
            deduped_exports.append((label, url))
    
    return all_tables, deduped_exports


def save_tables_as_csv(
    tables: List[Dict],
    out_dir: str,
    base_name: str,
) -> List[str]:
    """
    Save extracted tables as CSV files.
    Returns list of saved file paths.
    """
    import csv
    
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    
    for i, table in enumerate(tables):
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        
        if not rows:
            continue
        
        # Generate filename
        suffix = f"_table{i+1}" if len(tables) > 1 else ""
        page_num = table.get("page_number", 1)
        if page_num > 1:
            suffix += f"_page{page_num}"
        
        filename = safe_filename(f"{base_name}{suffix}.csv")
        filepath = os.path.join(out_dir, filename)
        
        # Write CSV
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            
            if headers:
                writer.writerow(headers)
            
            for row in rows:
                writer.writerow(row)
        
        saved.append(filepath)
    
    return saved


def merge_tables(tables: List[Dict]) -> Dict:
    """
    Merge multiple tables (from pagination) into one.
    Assumes all tables have same structure.
    """
    if not tables:
        return {"headers": [], "rows": []}
    
    # Use headers from first table
    headers = tables[0].get("headers", [])
    
    # Combine all rows
    all_rows = []
    for table in tables:
        all_rows.extend(table.get("rows", []))
    
    return {
        "headers": headers,
        "rows": all_rows,
        "row_count": len(all_rows),
        "source_tables": len(tables),
    }


def download_data_file(
    session: requests.Session,
    url: str,
    out_dir: str,
    default_name: str,
    state: dict,
) -> Optional[str]:
    """
    Download a data file (CSV, Excel, JSON, XML).
    Similar to download_file but for data formats.
    """
    if url in state["seen_download_urls"]:
        return None
    
    try:
        r = session.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except Exception as e:
        print(f"    Failed to download {url}: {e}")
        return None
    
    data = r.content
    h = sha256_bytes(data)
    
    # Check against existing files on disk (scanned at startup)
    if h in EXISTING_FILE_HASHES:
        print(f"    [SKIP - DUPLICATE] File with same content already exists")
        state["seen_download_urls"].append(url)
        state["seen_hashes"].append(h)
        return None
    
    if h in state["seen_hashes"]:
        state["seen_download_urls"].append(url)
        return None
    
    # Determine filename
    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.I)
    if m:
        fname = urllib.parse.unquote(m.group(1))
    else:
        path = urllib.parse.urlparse(url).path
        fname = os.path.basename(path) or default_name
    
    fname = safe_filename(fname)
    
    # Ensure proper extension based on content type
    ctype = (r.headers.get("Content-Type") or "").lower()
    root, ext = os.path.splitext(fname)
    
    if not ext:
        if "csv" in ctype or "csv" in url.lower():
            ext = ".csv"
        elif "excel" in ctype or "spreadsheet" in ctype or "xlsx" in url.lower():
            ext = ".xlsx"
        elif "json" in ctype:
            ext = ".json"
        elif "xml" in ctype:
            ext = ".xml"
        elif "pdf" in ctype:
            ext = ".pdf"
        else:
            ext = ".dat"
    
    final = f"{root}__{h[:10]}{ext}"
    
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, final)
    
    if os.path.exists(out_path):
        state["seen_download_urls"].append(url)
        state["seen_hashes"].append(h)
        return None
    
    with open(out_path, "wb") as f:
        f.write(data)
    
    state["seen_download_urls"].append(url)
    state["seen_hashes"].append(h)
    return out_path


@dataclass(frozen=True)
class Item:
    title: str
    page_url: str
    site_name: str


def safe_filename(name: str) -> str:
    name = re.sub(r"[^\w\-. ()]+", "_", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:180] if len(name) > 180 else name


def norm_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def title_matches(title: str, keywords: List[str]) -> bool:
    t = (title or "").lower()
    return any(k.lower() in t for k in keywords)


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_state(out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, STATE_FILE)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_pages": [], "seen_download_urls": [], "seen_hashes": []}


def save_state(out_dir: str, state: dict) -> None:
    p = os.path.join(out_dir, STATE_FILE)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def fetch_html(session: requests.Session, url: str, referer: Optional[str] = None) -> str:
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer

    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = session.get(url, headers=h, timeout=30)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            time.sleep(1.2 * (attempt + 1))

    raise last_err if last_err else RuntimeError(f"Failed to fetch {url}")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def download_file(
    session: requests.Session,
    url: str,
    out_dir: str,
    default_name: str,
    state: dict,
) -> Optional[str]:
    """
    Download a file to flat out_dir (no subfolders).
    Only downloads PDFs.
    """
    if url in state["seen_download_urls"]:
        return None

    # Skip non-PDF URLs early (by extension check)
    url_lower = url.lower()
    looks_like_pdf = ".pdf" in url_lower or "pdf" in url_lower

    r = session.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    
    ctype = (r.headers.get("Content-Type") or "").lower()
    
    # Only save PDFs
    if "pdf" not in ctype and not looks_like_pdf:
        return None
    
    data = r.content
    h = sha256_bytes(data)

    # Check against existing files on disk (scanned at startup)
    if h in EXISTING_FILE_HASHES:
        print(f"    [SKIP - DUPLICATE] File with same content already exists")
        state["seen_download_urls"].append(url)
        state["seen_hashes"].append(h)
        return None

    if h in state["seen_hashes"]:
        state["seen_download_urls"].append(url)
        return None

    cd = r.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd, flags=re.I)
    if m:
        fname = urllib.parse.unquote(m.group(1))
    else:
        path = urllib.parse.urlparse(url).path
        fname = os.path.basename(path) or default_name

    fname = safe_filename(fname)
    
    # Ensure .pdf extension
    root, ext = os.path.splitext(fname)
    if ext.lower() != ".pdf":
        ext = ".pdf"
    final = f"{root}__{h[:10]}{ext}"

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, final)

    if os.path.exists(out_path):
        state["seen_download_urls"].append(url)
        state["seen_hashes"].append(h)
        return None

    with open(out_path, "wb") as f:
        f.write(data)


def download_file_playwright(
    url: str,
    out_dir: str,
    default_name: str,
    state: dict,
) -> Optional[str]:
    """
    Download a file for sites that block requests (like IRENA).
    Uses Playwright to click the download button and select PDF format.
    """
    if url in state["seen_download_urls"]:
        return None

    # Skip non-PDF URLs early
    url_lower = url.lower()
    looks_like_pdf = ".pdf" in url_lower or "/-/media/" in url_lower
    
    if not looks_like_pdf:
        return None
    
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("      Playwright not available")
        return None
    
    data = None
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            
            # Set up download handling - expect a download when we click PDF
            with page.expect_download(timeout=120000) as download_info:
                # Navigate directly to the PDF URL
                page.goto(url, timeout=60000)
            
            download = download_info.value
            
            # Wait for download to complete
            download_path = download.path()
            
            if download_path:
                # Read the downloaded file
                with open(download_path, 'rb') as f:
                    data = f.read()
            
            browser.close()
            
    except Exception as e:
        print(f"      Playwright download failed: {e}")
        return None
    
    if not data:
        print(f"      No data received")
        return None
    
    # Verify it's actually a PDF
    if not data.startswith(b'%PDF'):
        print(f"      Not a valid PDF file")
        return None
    
    h = sha256_bytes(data)

    # Check against existing files on disk (scanned at startup)
    if h in EXISTING_FILE_HASHES:
        print(f"    [SKIP - DUPLICATE] File with same content already exists")
        state["seen_download_urls"].append(url)
        state["seen_hashes"].append(h)
        return None

    if h in state["seen_hashes"]:
        state["seen_download_urls"].append(url)
        return None

    # Get filename from URL
    path = urllib.parse.urlparse(url).path
    fname = os.path.basename(path) or default_name
    fname = safe_filename(fname)
    
    # Ensure .pdf extension
    root, ext = os.path.splitext(fname)
    if ext.lower() != ".pdf":
        ext = ".pdf"
    final = f"{root}__{h[:10]}{ext}"

    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, final)

    if os.path.exists(out_path):
        state["seen_download_urls"].append(url)
        state["seen_hashes"].append(h)
        return None

    with open(out_path, "wb") as f:
        f.write(data)
    
    state["seen_download_urls"].append(url)
    state["seen_hashes"].append(h)
    
    return out_path


def download_irena_pdf_via_button(page_url: str, out_dir: str, default_name: str, state: dict) -> Optional[str]:
    """
    Download IRENA PDF by:
    1. Opening the publication page
    2. Hovering over "Download full report" button
    3. Clicking on "PDF" link
    4. Waiting for new tab with Chrome PDF viewer
    5. Using Ctrl+Shift+S to download the PDF
    """
    if page_url in state.get("seen_download_urls", []):
        return None
        
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("        Playwright not available")
        return None
    
    download_path = None
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                downloads_path=os.path.abspath(out_dir)  # Set download directory
            )
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            
            print(f"        Opening publication page...")
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            
            # Step 1: Find "Download full report" button
            print(f"        Looking for 'Download full report'...")
            found = False
            btn = None
            
            try:
                btn = page.get_by_text("Download full report", exact=False).first
                if btn.is_visible():
                    print("        Found 'Download full report' button")
                    btn.hover()
                    page.wait_for_timeout(2000)
                    found = True
            except Exception as e:
                pass
            
            if not found:
                try:
                    btn = page.locator("text=/download.*report/i").first
                    if btn.is_visible():
                        print("        Found download button via regex")
                        btn.hover()
                        page.wait_for_timeout(2000)
                        found = True
                except Exception as e:
                    pass
            
            if not found:
                print("        No download button found")
                browser.close()
                return None
            
            # Step 2: Find and click PDF link
            print(f"        Looking for PDF link...")
            pdf_link = page.locator("a:has-text('PDF')").first
            
            if not pdf_link.is_visible():
                print("        PDF link not visible, clicking download button...")
                btn.click()
                page.wait_for_timeout(2000)
                pdf_link = page.locator("a:has-text('PDF')").first
            
            if not pdf_link.is_visible():
                print("        No PDF link found")
                browser.close()
                return None
            
            print("        Found PDF link, clicking...")
            
            # Step 3: Click PDF and wait for new tab
            with context.expect_page(timeout=60000) as new_page_info:
                pdf_link.click()
            
            new_page = new_page_info.value
            print(f"        New tab opened!")
            
            # Wait for PDF to load in Chrome viewer
            print("        Waiting 10 seconds for PDF to load...")
            new_page.wait_for_timeout(10000)
            
            pdf_url = new_page.url
            print(f"        PDF URL: {pdf_url[:80]}...")
            
            # Extract filename from URL
            filename = pdf_url.split('/')[-1]
            if '?' in filename:
                filename = filename.split('?')[0]
            if not filename.endswith('.pdf'):
                filename = filename + '.pdf'
            
            # Step 4: Download using Cmd+S (Mac) or Ctrl+S (Windows)
            print("        Pressing Cmd+S to download...")
            
            try:
                with new_page.expect_download(timeout=60000) as download_info:
                    # Use Meta for Mac (Cmd key)
                    new_page.keyboard.press("Meta+s")
                
                download = download_info.value
                print(f"        Download triggered: {download.suggested_filename}")
                
                # Save the file
                save_path = os.path.join(out_dir, download.suggested_filename or filename)
                download.save_as(save_path)
                download_path = save_path
                print(f"        Saved to: {save_path}")
                
            except Exception as e:
                print(f"        Cmd+S failed: {e}, trying Cmd+Shift+S...")
                
                try:
                    with new_page.expect_download(timeout=60000) as download_info:
                        new_page.keyboard.press("Meta+Shift+s")
                    
                    download = download_info.value
                    save_path = os.path.join(out_dir, download.suggested_filename or filename)
                    download.save_as(save_path)
                    download_path = save_path
                    print(f"        Saved to: {save_path}")
                    
                except Exception as e2:
                    print(f"        Cmd+Shift+S failed: {e2}, trying click...")
                    
                    try:
                        viewport = new_page.viewport_size
                        if viewport:
                            x = int(viewport['width'] * 0.92)
                            y = 40
                            
                            with new_page.expect_download(timeout=60000) as download_info:
                                new_page.mouse.click(x, y)
                            
                            download = download_info.value
                            save_path = os.path.join(out_dir, download.suggested_filename or filename)
                            download.save_as(save_path)
                            download_path = save_path
                            print(f"        Saved to: {save_path}")
                            
                    except Exception as e3:
                        print(f"        Click failed: {e3}")
            
            browser.close()
            
    except Exception as e:
        print(f"        Download failed: {e}")
        import traceback
        traceback.print_exc()
        return None
    
    if download_path and os.path.exists(download_path):
        # Check if this file content already exists
        with open(download_path, 'rb') as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        
        if file_hash in EXISTING_FILE_HASHES or file_hash in state.get("seen_hashes", []):
            print(f"        [SKIP - DUPLICATE] File with same content already exists")
            os.remove(download_path)  # Delete the just-downloaded duplicate
            state.setdefault("seen_download_urls", []).append(page_url)
            state.setdefault("seen_hashes", []).append(file_hash)
            return None
        
        file_size = os.path.getsize(download_path)
        print(f"        SUCCESS! Downloaded {file_size} bytes")
        
        # Update state
        state.setdefault("seen_download_urls", []).append(page_url)
        state.setdefault("seen_hashes", []).append(file_hash)
        EXISTING_FILE_HASHES.add(file_hash)  # Add to runtime set
        
        return download_path
    else:
        print("        FAILED to download the PDF")
        return None


# -------------------------
# Robust download link extractor (fixes Compass "Download" button/link patterns)
# -------------------------
def extract_download_links_generic(html: str, page_url: str) -> List[Tuple[str, str]]:
    """
    Robust download link finder:
    - catches <a> where visible text is nested/spans
    - catches button-like elements with onclick URLs or descendant <a>
    - catches WordPress-style ?attachment_id=#### links
    - catches data-href, data-url, data-download attributes
    - catches data export links (CSV, Excel, JSON)
    - uses aria-label/title as label fallback
    """
    soup = BeautifulSoup(html, "html.parser")
    out: List[Tuple[str, str]] = []

    social_domains = ["twitter.com", "facebook.com", "linkedin.com", "mailto:", "instagram.com", "youtube.com"]
    
    # File extensions to capture
    file_extensions = r"\.(pdf|xlsx|xls|csv|zip|pptx|ppt|docx|doc|txt|json|xml|tsv)(\?|#|$)"
    
    # Export/download keywords
    export_keywords = [
        "download", "export", "descargar", "exportar",
        "full data", "all data", "complete data",
        "csv", "excel", "xlsx",
    ]

    def add(label: str, href: str) -> None:
        if not href:
            return
        full = norm_url(page_url, href.strip())
        if any(x in full.lower() for x in social_domains):
            return
        out.append((label[:120] if label else "download", full))

    # 1) normal anchors with href
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        # label fallback: text OR aria-label OR title
        label = (a.get_text(" ", strip=True) or "").strip()
        if not label:
            label = (a.get("aria-label") or a.get("title") or "").strip()

        url_lower = href.lower()
        label_lower = label.lower()

        is_file = bool(re.search(file_extensions, href, flags=re.I))
        looks_like_download = any(kw in label_lower or kw in url_lower for kw in export_keywords)
        looks_like_wp_attachment = ("attachment_id=" in url_lower)
        has_download_attr = a.has_attr("download")
        
        # Check for export format indicators in URL
        is_export_url = any(x in url_lower for x in ["format=csv", "format=excel", "format=json", "export=", "output="])

        if is_file or looks_like_download or looks_like_wp_attachment or has_download_attr or is_export_url:
            add(label or "download", href)

    # 2) elements with data-href, data-url, data-download, data-file attributes
    for attr in ["data-href", "data-url", "data-download", "data-file", "data-pdf"]:
        for el in soup.select(f"[{attr}]"):
            href = (el.get(attr) or "").strip()
            if not href:
                continue
            label = (el.get_text(" ", strip=True) or "").strip()
            if not label:
                label = (el.get("aria-label") or el.get("title") or "").strip()
            is_file = bool(re.search(r"\.(pdf|xlsx|xls|csv|zip|pptx|ppt|docx|doc)(\?|#|$)", href, flags=re.I))
            if is_file or "download" in label.lower():
                add(label or "download", href)

    # 3) button-like elements
    candidates = soup.select("button, [role='button'], div[onclick], span[onclick], a[onclick]")
    for el in candidates:
        text = (el.get_text(" ", strip=True) or "").strip()
        if "download" not in text.lower():
            continue

        # a) descendant anchor
        a = el.select_one("a[href]")
        if a and a.get("href"):
            add(text or "download", a["href"])
            continue

        # b) onclick URL - look for window.open or location.href patterns
        onclick = (el.get("onclick") or "").strip()
        if onclick:
            # Match window.open('url'), location.href='url', or simple 'url'
            patterns = [
                r"window\.open\s*\(\s*['\"]([^'\"]+)['\"]",
                r"location\.href\s*=\s*['\"]([^'\"]+)['\"]",
                r"['\"]([^'\"]+\.(pdf|xlsx|csv|zip|pptx|docx))['\"]",
            ]
            for pat in patterns:
                m = re.search(pat, onclick, flags=re.I)
                if m:
                    add(text or "download", m.group(1))
                    break

    # 4) raw fallback: attachment_id patterns anywhere in HTML
    for m in re.finditer(r"""\?attachment_id=\d+""", html):
        add("download", m.group(0))

    # 5) Look for direct PDF/file links in script tags (common in SPAs)
    for script in soup.select("script"):
        script_text = script.string or ""
        for m in re.finditer(r'["\'](https?://[^"\']+\.(pdf|xlsx|csv|zip))["\']', script_text, flags=re.I):
            add("download", m.group(1))

    # de-dupe by URL preserving order
    seen = set()
    deduped: List[Tuple[str, str]] = []
    for label, url in out:
        if url not in seen:
            seen.add(url)
            deduped.append((label, url))
    return deduped


# -------------------------
# SITE: Direct PDF downloads (no scraping needed)
# -------------------------
def site_direct_pdf_items(
    start_urls: List[str],
    site_name: str,
) -> List[Item]:
    """
    For sites where we have direct PDF URLs - just return them as items.
    The download will happen directly.
    """
    items = []
    for url in start_urls:
        # Extract filename from URL for title
        path = urllib.parse.urlparse(url).path
        filename = os.path.basename(path) or "document"
        title = filename.replace(".pdf", "").replace("-", " ").replace("_", " ")
        items.append(Item(title=title, page_url=url, site_name=site_name))
    return items


# -------------------------
# SITE: Generic scraper for any website
# -------------------------
def site_generic_scraper_items(
    session: requests.Session,
    start_url: str,
    site_name: str,
    keywords: List[str],
    max_items: int,
) -> List[Item]:
    """
    Generic scraper that finds PDF links and pages matching keywords.
    Works for most standard websites.
    """
    items: List[Item] = []
    seen: Set[str] = set()
    
    try:
        html = fetch_html(session, start_url)
        soup = BeautifulSoup(html, "html.parser")
        
        # Find all links
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            text = (a.get_text(" ", strip=True) or "").strip()
            
            if not href:
                continue
            
            url = norm_url(start_url, href)
            
            # Skip external social media, etc.
            if any(x in url.lower() for x in ["twitter.com", "facebook.com", "linkedin.com", "instagram.com", "youtube.com"]):
                continue
            
            if url in seen:
                continue
            
            # Check if it's a PDF link
            is_pdf = ".pdf" in href.lower()
            
            # Check if title matches keywords
            matches_keywords = title_matches(text, keywords) if text else False
            
            if is_pdf or matches_keywords:
                title = text if text else os.path.basename(urllib.parse.urlparse(url).path)
                items.append(Item(title=title[:200], page_url=url, site_name=site_name))
                seen.add(url)
            
            if len(items) >= max_items:
                break
                
    except Exception as e:
        print(f"  Error scraping {start_url}: {e}")
    
    return items


# -------------------------
# SITE: Hydrogen Council Compass (public) - uses Playwright for JS-rendered downloads
# -------------------------
def site_hydrogen_council_compass_public_items(
    session: requests.Session,
    start_url: str,
    site_name: str,
    keywords: List[str],
) -> List[Item]:
    """
    Hydrogen Council Compass is a single-page app with download buttons.
    We return it as an item and will use Playwright to extract downloads.
    """
    return [Item(title="Hydrogen Council Compass", page_url=start_url, site_name=site_name)]


def extract_compass_downloads_playwright(page_url: str) -> List[Tuple[str, str]]:
    """
    Use Playwright to render Compass page and find download links.
    The Compass site often has JavaScript-rendered download buttons.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    downloads: List[Tuple[str, str]] = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        try:
            page.goto(page_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)  # Wait for JS to render
            
            # Scroll to load lazy content
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            
            # Get all anchors with their attributes
            anchors = page.eval_on_selector_all(
                "a",
                """els => els.map(a => ({
                    href: a.getAttribute('href') || '',
                    text: (a.innerText || '').trim(),
                    download: a.getAttribute('download') || '',
                    ariaLabel: a.getAttribute('aria-label') || ''
                }))"""
            )
            
            # Also check for buttons with data attributes
            buttons = page.eval_on_selector_all(
                "button, [role='button']",
                """els => els.map(b => ({
                    text: (b.innerText || '').trim(),
                    dataHref: b.getAttribute('data-href') || b.getAttribute('data-url') || '',
                    onclick: b.getAttribute('onclick') || ''
                }))"""
            )
            
            for a in anchors:
                href = a.get("href", "").strip()
                text = a.get("text", "").strip() or a.get("ariaLabel", "").strip()
                
                if not href:
                    continue
                    
                is_file = bool(re.search(r"\.(pdf|xlsx|csv|zip|pptx)(\?|#|$)", href, flags=re.I))
                is_download = "download" in text.lower() or "download" in href.lower() or a.get("download")
                
                if is_file or is_download:
                    full_url = norm_url(page_url, href)
                    downloads.append((text or "download", full_url))
            
            for btn in buttons:
                text = btn.get("text", "").strip()
                href = btn.get("dataHref", "").strip()
                
                if href and ("download" in text.lower() or ".pdf" in href.lower()):
                    full_url = norm_url(page_url, href)
                    downloads.append((text or "download", full_url))
                    
        except Exception as e:
            print(f"  Playwright error on Compass: {e}")
        finally:
            browser.close()
    
    # Dedupe
    seen = set()
    deduped = []
    for label, url in downloads:
        if url not in seen:
            seen.add(url)
            deduped.append((label, url))
    return deduped


# -------------------------
# SITE: IRENA Publications via Playwright (search, filter, paginate)
# -------------------------
def site_irena_publications_items_playwright(
    start_url: str,
    site_name: str,
    keywords: List[str],
    max_items: int,
) -> List[Item]:
    """
    Uses Playwright to:
    1. Go to IRENA publications page
    2. Scroll and click to load all content
    3. Extract publication links using JavaScript evaluate
    4. Filter by keywords and year (2020+)
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright not installed. Run:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium"
        ) from e

    items: List[Item] = []
    seen: Set[str] = set()
    
    with sync_playwright() as p:
        # MUST use headless=False - IRENA's site doesn't work in headless mode
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        try:
            print(f"    Loading IRENA publications page...")
            page.goto("https://www.irena.org/Publications", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            
            # Scroll to load more content
            print(f"    Scrolling to load content...")
            for i in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1000)
            
            # Use JavaScript evaluate to get links - this is proven to work

                        # Wait for publications links to appear (content is often injected after hydration)
            try:
                page.wait_for_selector('a[href*="/Publications/"]', timeout=15000)
            except Exception:
                # We'll fall back to parsing HTML below
                pass

            print(f"    Extracting publication links...")

            # 1) Try DOM extraction first
            links_data = page.evaluate("""
() => {
  const links = Array.from(document.querySelectorAll('a[href]'))
    .map(a => ({ href: a.getAttribute('href') || '', text: (a.textContent || '').trim() }))
    .filter(x => x.href.includes('/Publications/'));
  return links;
}
""")

            # 2) Fallback: parse raw HTML if DOM extraction returns nothing
            if not links_data:
                html = page.content()
                import re as _re
                hrefs = _re.findall(r'href="([^"]*?/Publications/[^"]+)"', html)
                # de-dup while preserving order
                seen_h = set()
                hrefs = [h for h in hrefs if not (h in seen_h or seen_h.add(h))]
                links_data = [{"href": h, "text": ""} for h in hrefs]

            print(f"      Found {len(links_data)} publication links")

            # print(f"    Extracting publication links...")
            # links_data = page.evaluate("""
            # () => {
            #     const anchors = Array.from(document.querySelectorAll('a[href]'));
            #     const links = anchors.filter(a =>
            #     (a.getAttribute('href') || '').toLowerCase().includes('/publications/')
            # );
            # return links.map(a => ({
            #     href: a.getAttribute('href') || '',
            #     text: (a.textContent || '').trim().slice(0, 200)
            # }));
            # }
            # """)


            
            # print(f"      Found {len(links_data)} publication links")
            
            for link in links_data:
                href = link.get('href', '')
                text = link.get('text', '')
                
                if not href:
                    continue
                
                # Skip the main /Publications page link
                if href == '/Publications' or href == '/Publications/':
                    continue
                
                # Extract year from URL - format: /Publications/YYYY/Mon/Title
                year_match = re.search(r'/publications/(\d{4})/', href, flags=re.IGNORECASE)
                if not year_match:
                    continue
                
                year = int(year_match.group(1))
                if year < 2020:
                    continue
                
                # Make absolute URL
                if href.startswith('/'):
                    url = f"https://www.irena.org{href}"
                else:
                    url = href
                
                # Skip if already seen
                if url in seen:
                    continue
                
                # Get title from URL if text is too short
                if len(text) < 15:
                    # Extract from URL path: /Publications/2026/Jan/Title-Here -> Title Here
                    path_parts = href.split('/')
                    if len(path_parts) >= 5:
                        title_part = path_parts[-1]
                        text = title_part.replace('-', ' ').replace('_', ' ')
                
                if not text:
                    text = f"IRENA Publication {year}"
                
                # Check keyword match (be generous - match on URL too)
                combined = (text + " " + href).lower()
                
                matches_keyword = any(kw.lower() in combined for kw in keywords)
                
                # Also match common energy terms
                energy_terms = ["energy", "power", "renewable", "hydrogen", "solar", "wind", 
                               "electricity", "transition", "climate", "carbon", "green",
                               "ammonia", "fuel", "storage", "grid", "generation", "outlook",
                               "cost", "finance", "investment", "policy", "jobs", "auction"]
                matches_energy = any(term in combined for term in energy_terms)
                
                if matches_keyword or matches_energy:
                    items.append(Item(title=text[:200], page_url=url, site_name=site_name))
                    seen.add(url)
                    
                    if len(items) >= max_items:
                        break
                        
        except Exception as e:
            print(f"    ERROR loading IRENA: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()
    
    print(f"    Found {len(items)} IRENA publications matching keywords")
    return items


def extract_irena_downloads_playwright(page_url: str) -> List[Tuple[str, str]]:
    """
    Extract download links from an IRENA publication detail page using Playwright.
    IRENA PDFs are served from /-/media/Files/... paths
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    downloads: List[Tuple[str, str]] = []
    
    with sync_playwright() as p:
        # MUST use headless=False for IRENA - they block headless browsers
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        
        try:
            print(f"      Fetching IRENA page with Playwright...")
            page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            
            # Scroll to load all content
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1500)
            
            # Get all potential download links using page.evaluate
            anchors = page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.getAttribute('href') || '',
                        text: (a.innerText || '').trim().substring(0, 100)
                    }));
                }
            """)
            
            for a in anchors:
                href = a.get("href", "").strip()
                text = a.get("text", "").strip()
                
                if not href:
                    continue
                
                href_lower = href.lower()
                
                # IRENA specific: PDFs are served from /-/media/Files/
                is_irena_media = "/-/media/" in href_lower
                
                # Also check for direct file extensions
                is_pdf = href_lower.endswith(".pdf")
                is_pptx = href_lower.endswith(".pptx") or href_lower.endswith(".ppt")
                is_data = any(href_lower.endswith(ext) for ext in [".csv", ".xlsx", ".xls", ".json", ".xml", ".zip"])
                
                if is_irena_media or is_pdf or is_pptx or is_data:
                    # Build full URL
                    if href.startswith('/'):
                        full_url = f"https://www.irena.org{href}"
                    elif href.startswith('http'):
                        full_url = href
                    else:
                        full_url = f"https://www.irena.org/{href}"
                    
                    # Get label from text or filename
                    if text and len(text) > 2:
                        label = text
                    else:
                        # Extract filename from URL
                        label = href.split('/')[-1].replace('.pdf', '').replace('-', ' ')[:80]
                    
                    downloads.append((label, full_url))
                    print(f"        Found: {label[:50]}... -> {href[-50:]}")
                        
        except Exception as e:
            print(f"      Playwright error on IRENA detail: {e}")
        finally:
            browser.close()
    
    # Dedupe
    seen = set()
    deduped = []
    for label, url in downloads:
        if url not in seen:
            seen.add(url)
            deduped.append((label, url))
    
    print(f"      Found {len(deduped)} download links")
    return deduped


# -------------------------
# SITE: CARB Annual Hydrogen Evaluation
# -------------------------
def site_carb_annual_h2_eval_items(
    session: requests.Session,
    start_url: str,
    site_name: str,
) -> List[Item]:
    """
    CARB Annual Hydrogen Evaluation page lists multiple annual reports.
    We'll find all years/reports and return them as items.
    """
    items: List[Item] = []
    
    try:
        html = fetch_html(session, start_url)
        soup = BeautifulSoup(html, "html.parser")
        
        # Look for links to PDFs or report pages
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            text = (a.get_text(" ", strip=True) or "").strip()
            
            if not href:
                continue
                
            # Check if it's a PDF link or contains "hydrogen" and year patterns
            is_pdf = ".pdf" in href.lower()
            is_hydrogen_report = ("hydrogen" in text.lower() or "evaluation" in text.lower()) and re.search(r"20\d{2}", text)
            
            if is_pdf or is_hydrogen_report:
                url = norm_url(start_url, href)
                title = text if text else f"CARB Hydrogen Report"
                items.append(Item(title=title, page_url=url, site_name=site_name))
        
        # If no specific items found, return the main page as a single item
        if not items:
            items.append(Item(title="CARB Annual Hydrogen Evaluation", page_url=start_url, site_name=site_name))
            
    except Exception as e:
        print(f"  Error scraping CARB: {e}")
        items.append(Item(title="CARB Annual Hydrogen Evaluation", page_url=start_url, site_name=site_name))
    
    # Dedupe
    seen = set()
    deduped = []
    for item in items:
        if item.page_url not in seen:
            seen.add(item.page_url)
            deduped.append(item)
    
    return deduped


# -------------------------
# SITE: EIA Today in Energy
# -------------------------
def site_eia_today_in_energy_items(
    session: requests.Session,
    start_url: str,
    site_name: str,
    keywords: List[str],
    max_items: int,
) -> List[Item]:
    """
    EIA Today in Energy - scrapes article listings and filters by keywords.
    Also handles pagination if available.
    """
    html = fetch_html(session, start_url)
    soup = BeautifulSoup(html, "html.parser")

    items: List[Item] = []
    seen = set()

    # Process current page
    for a in soup.select('a[href*="/todayinenergy/detail.php"]'):
        href = (a.get("href") or "").strip()
        text = (a.get_text(" ", strip=True) or "").strip()
        if not href or not text:
            continue

        url = norm_url(start_url, href)
        if url in seen:
            continue

        if title_matches(text, keywords):
            items.append(Item(title=text, page_url=url, site_name=site_name))
            seen.add(url)

        if len(items) >= max_items:
            break

    # Try to get more from archive/pagination if we need more items
    if len(items) < max_items:
        # Look for pagination or archive links
        archive_links = soup.select('a[href*="archive"]') or soup.select('a[href*="page="]')
        for archive_a in archive_links[:3]:  # Check up to 3 archive pages
            archive_href = archive_a.get("href", "")
            if not archive_href:
                continue
                
            archive_url = norm_url(start_url, archive_href)
            try:
                archive_html = fetch_html(session, archive_url, referer=start_url)
                archive_soup = BeautifulSoup(archive_html, "html.parser")
                
                for a in archive_soup.select('a[href*="/todayinenergy/detail.php"]'):
                    href = (a.get("href") or "").strip()
                    text = (a.get_text(" ", strip=True) or "").strip()
                    if not href or not text:
                        continue

                    url = norm_url(start_url, href)
                    if url in seen:
                        continue

                    if title_matches(text, keywords):
                        items.append(Item(title=text, page_url=url, site_name=site_name))
                        seen.add(url)

                    if len(items) >= max_items:
                        break
                        
                time.sleep(SLEEP)
                
            except Exception:
                continue
                
            if len(items) >= max_items:
                break

    return items


# -------------------------
# SITE: EIA Reports via Playwright (search, filter, paginate)
# -------------------------
def site_eia_reports_items_playwright(
    start_url: str,
    site_name: str,
    keywords: List[str],
    max_items: int,
    min_year: int = 2020,
) -> List[Item]:
    """
    Uses Playwright to:
    1. Go to EIA reports page
    2. Extract all report links
    3. Filter by keywords and year
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise RuntimeError(
            "Playwright not installed. Run:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium"
        ) from e

    items: List[Item] = []
    seen: Set[str] = set()
    
    # Report sections to scrape
    eia_sections = [
        "https://www.eia.gov/outlooks/aeo/",  # Annual Energy Outlook
        "https://www.eia.gov/outlooks/steo/",  # Short-Term Energy Outlook  
        "https://www.eia.gov/outlooks/ieo/",   # International Energy Outlook
        "https://www.eia.gov/petroleum/weekly/",  # Weekly Petroleum
        "https://www.eia.gov/naturalgas/weekly/", # Natural Gas Weekly
        "https://www.eia.gov/electricity/monthly/", # Electric Power Monthly
        "https://www.eia.gov/totalenergy/data/monthly/", # Monthly Energy Review
        "https://www.eia.gov/renewable/",  # Renewable energy
        "https://www.eia.gov/energyexplained/hydrogen/", # Hydrogen
        start_url,  # Main reports page
    ]
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Use new_page directly without context (this works!)
        page = browser.new_page()
        
        for section_url in eia_sections:
            if len(items) >= max_items:
                break
                
            print(f"    Checking EIA section: {section_url[:50]}...")
            
            try:
                page.goto(section_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2000)
                
                # Scroll to load content
                for _ in range(3):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(500)
                
                # Use JavaScript evaluate to get all links (most reliable)
                anchors = page.evaluate('''
                    () => {
                        const links = document.querySelectorAll('a[href]');
                        return Array.from(links).map(a => ({
                            href: a.getAttribute('href') || '',
                            text: (a.innerText || '').trim().substring(0, 200)
                        }));
                    }
                ''')
                
                for a in anchors:
                    href = a.get('href', '')
                    text = a.get('text', '')
                    
                    if not href or len(text) < 10:
                        continue
                    
                    href_lower = href.lower()
                    text_lower = text.lower()
                    
                    # Skip navigation/tool links
                    skip = ["/tools/", "/about/", "javascript:", "mailto:", "#", 
                           "twitter.com", "facebook.com", "/a-z/", "/faqs/"]
                    if any(s in href_lower for s in skip):
                        continue
                    
                    # Check if it's a report link
                    is_report = any(p in href_lower for p in [
                        "/outlook", "/report", "/analysis", "/petroleum/", 
                        "/naturalgas/", "/electricity/", "/renewable/",
                        "/coal/", "/nuclear/", "/consumption/", "/environment/",
                        ".pdf", "/todayinenergy/", "/energyexplained/"
                    ])
                    
                    # Check keyword match
                    matches_kw = any(kw.lower() in text_lower or kw.lower() in href_lower for kw in keywords)
                    
                    if is_report or matches_kw:
                        # Make absolute URL
                        if href.startswith('/'):
                            full_url = f"https://www.eia.gov{href}"
                        elif not href.startswith('http'):
                            full_url = f"https://www.eia.gov/{href}"
                        else:
                            full_url = href
                        
                        if full_url not in seen:
                            # Check year if extractable
                            year_match = re.search(r'20[12][0-9]', text + " " + href)
                            year_ok = True
                            if year_match:
                                year = int(year_match.group())
                                if year < min_year:
                                    year_ok = False
                            
                            if year_ok:
                                items.append(Item(title=text[:200], page_url=full_url, site_name=site_name))
                                seen.add(full_url)
                                
                                if len(items) >= max_items:
                                    break
                    
            except Exception as e:
                print(f"      Error loading {section_url}: {e}")
                continue
        
        browser.close()
    
    print(f"    Found {len(items)} EIA reports")
    return items


def extract_eia_downloads(session: requests.Session, page_url: str) -> List[Tuple[str, str]]:
    """
    Extract download links from an EIA report page.
    EIA pages typically have:
    - Direct PDF links
    - Excel/CSV data downloads
    - XLS data files
    """
    downloads: List[Tuple[str, str]] = []
    
    try:
        html = fetch_html(session, page_url)
        soup = BeautifulSoup(html, "html.parser")
        
        for a in soup.select("a[href]"):
            href = (a.get("href") or "").strip()
            text = (a.get_text(" ", strip=True) or "").strip()
            aria = (a.get("aria-label") or "").strip()
            title_attr = (a.get("title") or "").strip()
            
            if not href:
                continue
            
            label = text or aria or title_attr or "download"
            href_lower = href.lower()
            label_lower = label.lower()
            
            # Check for file downloads
            is_pdf = ".pdf" in href_lower
            is_data = any(ext in href_lower for ext in [".csv", ".xlsx", ".xls", ".json", ".xml", ".zip"])
            is_download = "download" in label_lower or a.has_attr("download")
            
            # EIA specific patterns
            is_eia_data = any(x in href_lower for x in ["/xls/", "/csv/", "/data/"])
            
            if is_pdf or is_data or is_download or is_eia_data:
                full_url = norm_url(page_url, href)
                
                # Skip external links
                if "eia.gov" not in full_url.lower() and not full_url.startswith(page_url[:30]):
                    # Allow relative links
                    if not href.startswith("/"):
                        continue
                
                downloads.append((label[:100], full_url))
                
    except Exception as e:
        print(f"  Error extracting EIA downloads: {e}")
    
    # Dedupe
    seen = set()
    deduped = []
    for label, url in downloads:
        if url not in seen:
            seen.add(url)
            deduped.append((label, url))
    return deduped


def save_html_as_file(out_path: str, html: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)


# -------------------------
# SITE: ACER CHEST category crawler
# -------------------------
def site_acer_chest_items(
    session: requests.Session,
    start_url: str,
    site_name: str,
    keywords: List[str],
    max_depth: int,
    max_items: int,
) -> List[Item]:
    """
    ACER AEGIS CHEST crawler - navigates category pages and collects data items.
    Handles both category navigation and data item extraction.
    """
    queue: List[Tuple[str, int]] = [(start_url, 0)]
    visited: Set[str] = set()
    items: List[Item] = []

    while queue and len(items) < max_items:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            html = fetch_html(session, url, referer=start_url)
        except Exception as e:
            print(f"  Failed to fetch {url}: {e}")
            continue

        soup = BeautifulSoup(html, "html.parser")

        # Navigate to sub-categories if not at max depth
        if depth < max_depth:
            # Multiple selectors for category links
            category_selectors = [
                'a[href*="/chest/category/"]',
                'a[href*="/category/"]',
                '.category-link',
                '[class*="category"] a'
            ]
            for selector in category_selectors:
                for a in soup.select(selector):
                    nxt = norm_url(url, a.get("href", ""))
                    if nxt and nxt not in visited and "/chest/" in nxt:
                        queue.append((nxt, depth + 1))

        # Extract data items - multiple possible selectors
        item_selectors = [
            'a[href*="/chest/dataitems/"]',
            'a[href*="/dataitems/"]',
            'a[href*="/chest/dataitem/"]',
            '.data-item a',
            '[class*="report"] a',
            'table a[href*="chest"]'
        ]
        
        for selector in item_selectors:
            for a in soup.select(selector):
                href = (a.get("href") or "").strip()
                title = (a.get_text(" ", strip=True) or "").strip()
                
                # Try to get title from parent or sibling elements if empty
                if not title:
                    parent = a.find_parent(['tr', 'li', 'div'])
                    if parent:
                        title = parent.get_text(" ", strip=True)[:200]
                
                if not href:
                    continue
                    
                item_url = norm_url(url, href)
                
                # Skip if already seen
                if item_url in visited:
                    continue
                    
                # Skip navigation/category links
                if "/category/" in item_url and "/dataitems/" not in item_url:
                    continue

                if title_matches(title, keywords):
                    items.append(Item(title=title or "ACER Report", page_url=item_url, site_name=site_name))
                    visited.add(item_url)
                    
                    if len(items) >= max_items:
                        break
                        
            if len(items) >= max_items:
                break

        time.sleep(SLEEP)

    return items


# -------------------------
# Runner
# -------------------------
def run_site(
    session: requests.Session,
    site_cfg: dict,
    keywords: List[str],
    out_dir: str,
    state: dict,
) -> None:
    site_name = site_cfg["name"]
    site_type = site_cfg["type"]
    start_urls = site_cfg.get("start_urls", [])

    print(f"\n=== {site_name} ({site_type}) ===")

    found_items: List[Item] = []

    for start_url in start_urls:
        if site_type == "direct_pdf":
            # For direct PDF, we pass all URLs at once
            found_items += site_direct_pdf_items(start_urls, site_name)
            break  # Only process once since we pass all URLs

        elif site_type == "generic_scraper":
            found_items += site_generic_scraper_items(
                session,
                start_url,
                site_name,
                keywords,
                max_items=int(site_cfg.get("max_items", 50)),
            )

        elif site_type == "hydrogen_council_compass_public":
            found_items += site_hydrogen_council_compass_public_items(session, start_url, site_name, keywords)

        elif site_type == "irena_publications_playwright":
            found_items += site_irena_publications_items_playwright(
                start_url=start_url,
                site_name=site_name,
                keywords=keywords,
                max_items=int(site_cfg.get("max_items", 50)),
            )

        elif site_type == "carb_annual_h2_eval":
            found_items += site_carb_annual_h2_eval_items(session, start_url, site_name)

        elif site_type == "eia_today_in_energy":
            found_items += site_eia_today_in_energy_items(
                session,
                start_url,
                site_name,
                keywords,
                max_items=int(site_cfg.get("max_items", 50)),
            )

        elif site_type == "acer_chest_category":
            found_items += site_acer_chest_items(
                session,
                start_url,
                site_name,
                keywords,
                max_depth=int(site_cfg.get("max_depth", 3)),
                max_items=int(site_cfg.get("max_items", 200)),
            )

        elif site_type == "eia_reports_playwright":
            found_items += site_eia_reports_items_playwright(
                start_url=start_url,
                site_name=site_name,
                keywords=keywords,
                max_items=int(site_cfg.get("max_items", 100)),
                min_year=int(site_cfg.get("min_year", 2020)),
            )

        else:
            raise ValueError(f"Unsupported site type: {site_type}")

    # de-dupe by page_url
    uniq: Dict[str, Item] = {}
    for it in found_items:
        uniq.setdefault(it.page_url, it)
    items = list(uniq.values())

    print(f"Matched {len(items)} item(s) by title keywords: {keywords}")

    for it in items:
        if it.page_url in state["seen_pages"]:
            print(f"[SKIP ITEM] already processed: {it.title}")
            continue

        print(f"\n[ITEM] {it.title}\n      {it.page_url}")

        # For direct PDF: the page_url IS the PDF, download directly
        if site_type == "direct_pdf":
            dl_links = [(it.title, it.page_url)]
        # For Compass: use Playwright to extract downloads (JS-rendered)
        elif site_type == "hydrogen_council_compass_public":
            dl_links = extract_compass_downloads_playwright(it.page_url)
            if not dl_links:
                # Fallback to generic extraction
                try:
                    html = fetch_html(session, it.page_url, referer=it.page_url)
                    dl_links = extract_download_links_generic(html, it.page_url)
                except Exception:
                    dl_links = []
        # For IRENA: use button-based download directly (they block direct PDF links)
        elif site_type == "irena_publications_playwright":
            # Don't extract links - directly download via the Download button on the page
            pdf_folder = os.path.join(out_dir, "pdf")
            saved = download_irena_pdf_via_button(it.page_url, pdf_folder, safe_filename(it.title), state)
            if saved:
                print(f"  [DOWNLOADED] {it.title} -> {saved}")
            else:
                print(f"  [SKIP] Could not download PDF")
            state["seen_pages"].append(it.page_url)
            time.sleep(SLEEP)
            continue
        # For CARB: check if item URL is already a PDF
        elif site_type == "carb_annual_h2_eval":
            if it.page_url.lower().endswith(".pdf"):
                dl_links = [(it.title, it.page_url)]
            else:
                try:
                    html = fetch_html(session, it.page_url, referer=it.page_url)
                    dl_links = extract_download_links_generic(html, it.page_url)
                except Exception as e:
                    print(f"  ! Failed to open item page: {e}")
                    dl_links = []
        # For EIA Reports: use dedicated extractor
        elif site_type == "eia_reports_playwright":
            # Check if URL is already a PDF
            if it.page_url.lower().endswith(".pdf"):
                dl_links = [(it.title, it.page_url)]
            else:
                dl_links = extract_eia_downloads(session, it.page_url)
                if not dl_links:
                    # Fallback to generic extraction
                    try:
                        html = fetch_html(session, it.page_url, referer=it.page_url)
                        dl_links = extract_download_links_generic(html, it.page_url)
                    except Exception:
                        dl_links = []
        # Otherwise: open item page and find downloadable links
        else:
            try:
                html = fetch_html(session, it.page_url, referer=it.page_url)
            except Exception as e:
                print(f"  ! Failed to open item page: {e}")
                state["seen_pages"].append(it.page_url)
                continue

            dl_links = extract_download_links_generic(html, it.page_url)

        if not dl_links:
            # No direct download links - check for data tables
            print("  (No download links detected, checking for data tables...)")
            
            try:
                if 'html' not in dir():
                    html = fetch_html(session, it.page_url, referer=it.page_url)
                
                # Check if page has tables or data export options
                tables, export_links = scrape_all_table_pages(session, it.page_url, max_pages=20)
                
                if export_links:
                    print(f"  Found {len(export_links)} data export link(s)")
                    data_folder = os.path.join(out_dir, "data")
                    for label, url in export_links:
                        try:
                            saved = download_data_file(session, url, data_folder, default_name=safe_filename(it.title), state=state)
                            if saved:
                                print(f"  [DATA DOWNLOADED] {label} -> {saved}")
                            else:
                                print(f"  [SKIP] {label}")
                        except Exception as e:
                            print(f"  [FAIL] {label} -> {url} ({e})")
                        time.sleep(SLEEP)
                
                if tables:
                    # Merge tables from all pages
                    merged = merge_tables(tables)
                    total_rows = merged.get("row_count", 0)
                    
                    if total_rows > 0:
                        print(f"  Found {total_rows} rows of data across {len(tables)} table(s)")
                        
                        # Save as CSV
                        data_folder = os.path.join(out_dir, "data")
                        saved_files = save_tables_as_csv([merged], data_folder, safe_filename(it.title))
                        
                        for f in saved_files:
                            print(f"  [DATA SAVED] {f}")
                
                if not export_links and not tables:
                    print("  (No data found on this page)")
                    
            except Exception as e:
                print(f"  ! Error extracting data: {e}")
            
            # mark page as seen so we don't hammer it every run
            state["seen_pages"].append(it.page_url)
            time.sleep(SLEEP)
            continue

        # Download PDFs and data files
        pdf_folder = os.path.join(out_dir, "pdf")
        data_folder = os.path.join(out_dir, "data")
        
        # Use Playwright for IRENA since they block requests
        use_playwright = site_type == "irena_publications_playwright"
        
        for label, url in dl_links:
            try:
                url_lower = url.lower()
                
                # Determine if it's a PDF or data file
                is_pdf = ".pdf" in url_lower or "pdf" in label.lower() or "/-/media/" in url_lower
                is_data = any(ext in url_lower for ext in [".csv", ".xlsx", ".xls", ".json", ".xml", ".tsv"])
                
                if is_pdf:
                    if use_playwright:
                        saved = download_file_playwright(url, pdf_folder, default_name=safe_filename(it.title), state=state)
                    else:
                        saved = download_file(session, url, pdf_folder, default_name=safe_filename(it.title), state=state)
                elif is_data:
                    saved = download_data_file(session, url, data_folder, default_name=safe_filename(it.title), state=state)
                else:
                    # Try PDF first, then data
                    if use_playwright:
                        saved = download_file_playwright(url, pdf_folder, default_name=safe_filename(it.title), state=state)
                    else:
                        saved = download_file(session, url, pdf_folder, default_name=safe_filename(it.title), state=state)
                    if not saved:
                        saved = download_data_file(session, url, data_folder, default_name=safe_filename(it.title), state=state)
                
                if saved:
                    print(f"  [DOWNLOADED] {label} -> {saved}")
                else:
                    print(f"  [SKIP] {label}")
            except Exception as e:
                print(f"  [FAIL] {label} -> {url} ({e})")
            time.sleep(SLEEP)

        state["seen_pages"].append(it.page_url)
        time.sleep(SLEEP)


def main() -> None:
    import argparse
    
    parser = argparse.ArgumentParser(description="Energy Reports Scraper with Source Discovery")
    parser.add_argument("--discover", action="store_true", 
                        help="Run source discovery to find new PDF sources")
    parser.add_argument("--auto-add", action="store_true",
                        help="Automatically add discovered sources to YAML (use with --discover)")
    parser.add_argument("--discover-only", action="store_true",
                        help="Only run discovery, skip downloading")
    parser.add_argument("--max-discover", type=int, default=10,
                        help="Max new sources to discover per run (default: 10)")
    parser.add_argument("--regions", type=str, default="Oman,Chile",
                        help="Comma-separated regions to research (default: Oman,Chile)")
    parser.add_argument("--yaml", type=str, default="sites.yaml",
                        help="Path to sites.yaml file")
    
    args = parser.parse_args()
    
    yaml_path = args.yaml
    cfg = load_yaml(yaml_path)
    keywords = cfg.get("keywords", [])
    out_dir = cfg.get("output_dir", "downloads")

    session = requests.Session()
    
    # ---- SOURCE DISCOVERY ----
    if args.discover or args.discover_only:
        discovery_config = DiscoveryConfig(
            enabled=True,
            max_new_sources_per_run=args.max_discover,
            target_regions=[r.strip() for r in args.regions.split(",")],
        )
        
        discovered = run_source_discovery(session, yaml_path, discovery_config)
        
        if discovered:
            add_discovered_to_yaml(yaml_path, discovered, auto_add=args.auto_add)
            
            # Reload YAML if we auto-added
            if args.auto_add:
                cfg = load_yaml(yaml_path)
        
        if args.discover_only:
            print("\nDiscovery complete. Skipping downloads (--discover-only).")
            return
    
    # ---- NORMAL DOWNLOAD RUN ----
    
    # Scan existing files for duplicate detection
    global EXISTING_FILE_HASHES
    EXISTING_FILE_HASHES = scan_existing_files_for_duplicates(out_dir)
    
    state = load_state(out_dir)

    for site in cfg.get("sites", []):
        try:
            run_site(session, site, keywords, out_dir, state)
        except Exception as e:
            print(f"\n[ERROR] Site failed: {site.get('name')} ({site.get('type')}) -> {e}")

    save_state(out_dir, state)
    print(f"\nDone. Output folder: {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()