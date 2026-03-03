#!/usr/bin/env python3
"""
DecarbIQ — collect_doe_oced.py
Enhanced DOE OCED collector — individual project pages, IDP sectors, CC programs.

The existing collect_doe.py handles the top-level OCED API (broken JS SPA) and
EERE award pages. This collector fills the gap by scraping:
  Source A: H2Hubs — 7 individual hub detail pages
  Source B: Industrial Demonstrations Program (IDP) — 5 sector pages
  Source C: Carbon Capture programs — demos, pilots, FEED studies
  Source D: Award Wednesdays — Phase 1 announcement articles
  Source E: Fact sheet PDFs (hub + CC project fact sheets)

Writes to regulatory_evidence in core_database.db with source_system='doe_oced_detail'.

Usage:
    python collect_doe_oced.py              # full run
    python collect_doe_oced.py --dry-run    # show what would be inserted
    python collect_doe_oced.py --stats      # current OCED detail coverage
    python collect_doe_oced.py --hubs       # Source A only
    python collect_doe_oced.py --idp        # Source B only
    python collect_doe_oced.py --cc         # Source C only
    python collect_doe_oced.py --awards     # Source D only
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA        = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC = 0.6       # be polite to energy.gov
DRY_RUN      = '--dry-run' in sys.argv
STATS_ONLY   = '--stats'   in sys.argv
HUBS_ONLY    = '--hubs'    in sys.argv
IDP_ONLY     = '--idp'     in sys.argv
CC_ONLY      = '--cc'      in sys.argv
AWARDS_ONLY  = '--awards'  in sys.argv
BACKFILL     = '--backfill' in sys.argv

# --limit N: cap total inserts (for testing)
_LIMIT = 0
for _a in sys.argv:
    if _a.startswith('--limit='):
        _LIMIT = int(_a.split('=', 1)[1])
    elif _a == '--limit':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _LIMIT = int(sys.argv[_idx + 1])

# ── Source A: H2Hubs detail pages ────────────────────────────────────────────
# (hub_name, states, url, funding, lead_org, selection_date)
# Selection announced Oct 13, 2023; Phase 1 awards vary by hub.
H2HUB_PAGES = [
    ('ARCHES', 'CA', 'https://www.energy.gov/oced/california-hydrogen-hub-arches',
     'Up to $1.2B', 'Alliance for Renewable Clean Hydrogen Energy Systems', '2023-10-13'),
    ('ARCH2', 'WV,OH,PA', 'https://www.energy.gov/oced/appalachian-hydrogen-hub-arch2',
     'Up to $925M', 'Battelle', '2023-10-13'),
    ('Gulf Coast', 'TX', 'https://www.energy.gov/oced/gulf-coast-hydrogen-hub',
     'Up to $1.2B', 'HyVelocity LLC', '2023-10-13'),
    ('MachH2', 'IL,IN,MI', 'https://www.energy.gov/oced/midwest-hydrogen-hub',
     'Up to $1B', 'Midwest Alliance for Clean Hydrogen LLC', '2023-10-13'),
    ('PNWH2', 'WA,OR,MT', 'https://www.energy.gov/oced/pacific-northwest-hydrogen-hub-pnwh2',
     'Up to $1B', 'Pacific Northwest Hydrogen Association', '2023-10-13'),
    ('Heartland', 'ND,MN,SD,CO', 'https://www.energy.gov/oced/heartland-hydrogen-hub',
     'Up to $925M', 'Heartland Hydrogen Hub LLC', '2023-10-13'),
    ('MACH2', 'PA,DE,NJ', 'https://www.energy.gov/oced/mid-atlantic-hydrogen-hub',
     'Up to $750M', 'MACH2', '2023-10-13'),
]

# ── Source B: IDP sector pages ───────────────────────────────────────────────
IDP_PAGES = [
    ('Iron and Steel',
     'https://www.energy.gov/oced/industrial-demonstrations-program-selections-award-negotiations-iron-and-steel'),
    ('Chemicals and Refining',
     'https://www.energy.gov/oced/industrial-demonstrations-program-selections-award-negotiations-chemicals-and-refining'),
    ('Cement and Concrete',
     'https://www.energy.gov/oced/industrial-demonstrations-program-selections-award-negotiations-cement-and-concrete'),
    ('Glass and Glass Fiber',
     'https://www.energy.gov/oced/industrial-demonstrations-program-selections-award-negotiations-glass-and-glass-fiber'),
    ('Heat (Crosscutting)',
     'https://www.energy.gov/oced/industrial-demonstrations-program-selections-award-negotiations-heat'),
]

# Master list page (all IDP projects)
IDP_MASTER = 'https://www.energy.gov/oced/industrial-demonstrations-program-selected-and-awarded-projects'

# ── Source C: Carbon Capture pages ───────────────────────────────────────────
CC_PAGES = [
    ('CC Demos (3 projects)',
     'https://www.energy.gov/oced/carbon-capture-demonstration-projects-program'),
    ('CC Large-Scale Pilots (4 projects)',
     'https://www.energy.gov/oced/carbon-capture-large-scale-pilots'),
    ('CC FEED Studies (8 projects)',
     'https://www.energy.gov/oced/carbon-capture-demonstration-projects-program-front-end-engineering-design-feed-studies'),
    ('CC Selected Projects',
     'https://www.energy.gov/oced/carbon-capture-demonstrations-projects-selected-and-awarded-projects'),
    ('CC Pilot Selected Projects',
     'https://www.energy.gov/oced/carbon-capture-large-scale-pilot-selected-and-awarded-projects'),
]

# ── Source D: Award Wednesdays listing pages ─────────────────────────────────
AWARD_WEDNESDAYS_BASE = 'https://www.energy.gov/oced/listings/oced-award-wednesdays'

# ── Source E: Known Fact Sheet PDF URLs ──────────────────────────────────────
FACT_SHEET_PDFS = [
    ('ARCHES', 'https://www.energy.gov/sites/default/files/2024-07/H2Hubs%20ARCHES_Award%20Fact%20Sheet.pdf'),
    ('ARCH2', 'https://www.energy.gov/sites/default/files/2024-07/H2Hubs%20Appalachian%20Factsheet%20Booklet_update.pdf'),
    ('PNWH2', 'https://www.energy.gov/sites/default/files/2024-07/H2Hubs%20PNW%20Booklet_Factsheet_7.23.24.pdf'),
    ('ARCHES v2', 'https://www.energy.gov/sites/default/files/2024-08/H2Hubs%20Arches%20Fact%20Sheet_8-1.pdf'),
    ('Gulf Coast Phase 1', 'https://www.energy.gov/sites/default/files/2024-12/Gulf%20Coast%20Phase%201%20Award%20Briefing_FINAL.pdf'),
    ('Baytown CCS', 'https://www.energy.gov/sites/default/files/2024-07/Baytown_CCS_Factsheet_0.pdf'),
    ('Project Tundra', 'https://www.energy.gov/sites/default/files/2024-01/01.09.2024%20OCED%20CCS%20Demos%20Briefing%20-%20Project%20Tundra.pdf'),
    ('Baytown CCS Award', 'https://www.energy.gov/sites/default/files/2024-01/01.10.2024%20OCED%20CCS%20Demos%20Briefing%20-%20Baytown%20CCS.pdf'),
    ('CC Pilot Vicksburg', 'https://www.energy.gov/sites/default/files/2024-03/2024%20OCED%20Carbon%20Capture%20Pilot%20at%20Vicksburg%20Containerboard%20Briefing.pdf'),
    ('CC Pilot Cane Run', 'https://www.energy.gov/sites/default/files/2024-02/2024%20OCED%20Carbon%20Capture%20Pilot%20at%20Cane%20Run%20Briefing.pdf'),
    ('CC Pilot Big Spring', 'https://www.energy.gov/sites/default/files/2024-03/2024%20OCED%20Carbon%20Capture%20Pilot%20at%20Big%20Spring%20Briefing.pdf'),
]

# ── Known IDP projects with hydrogen/CCS relevance ──────────────────────────
# These ensure coverage even if page scraping fails (403 errors)
# IDP selections announced March 25, 2024.
KNOWN_IDP_PROJECTS = [
    {'company': 'Cleveland-Cliffs', 'project': 'H2-Ready DRI Plant',
     'location': 'Middletown, OH', 'funding': 'Up to $500M',
     'sector': 'Iron and Steel', 'technology': 'hydrogen_dri',
     'co2_reduction': '1M tCO2/yr', 'date': '2024-03-25'},
    {'company': 'ExxonMobil', 'project': 'Baytown Olefins Plant',
     'location': 'Baytown, TX', 'funding': 'Up to $331.9M',
     'sector': 'Chemicals and Refining', 'technology': 'hydrogen_fuel_switching',
     'co2_reduction': '2.7M tCO2/yr', 'date': '2024-03-25'},
    {'company': 'Dow Chemical', 'project': 'Novel CO2 Utilization',
     'location': 'Gulf Coast', 'funding': 'Up to $95M',
     'sector': 'Chemicals and Refining', 'technology': 'ccs', 'date': '2024-03-25'},
    {'company': 'Orsted', 'project': 'Star e-Methanol',
     'location': 'TX Gulf Coast', 'funding': 'Up to $99M',
     'sector': 'Chemicals and Refining', 'technology': 'green_hydrogen', 'date': '2024-03-25'},
    {'company': 'Technip / LanzaTech', 'project': 'SECURE',
     'location': 'Gulf Coast', 'funding': 'Up to $200M',
     'sector': 'Chemicals and Refining', 'technology': 'co2_utilization', 'date': '2024-03-25'},
    {'company': 'BASF', 'project': 'Syngas from Recycled Byproducts',
     'location': 'Freeport, TX', 'funding': 'Up to $75M',
     'sector': 'Chemicals and Refining', 'technology': 'hydrogen_gasification', 'date': '2024-03-25'},
    {'company': 'National Cement', 'project': 'Lebec Net Zero',
     'location': 'Lebec, CA', 'funding': 'Up to $500M',
     'sector': 'Cement and Concrete', 'technology': 'ccs',
     'co2_reduction': '950K tCO2/yr', 'date': '2024-03-25'},
    {'company': 'Heidelberg Materials', 'project': 'Mitchell Plant CCS',
     'location': 'Mitchell, IN', 'funding': 'Up to $500M',
     'sector': 'Cement and Concrete', 'technology': 'ccs',
     'co2_reduction': '2M tCO2/yr', 'date': '2024-03-25'},
]

# ── Fact-sheet label → actual recipient company ──────────────────────────────
# Built from H2HUB_PAGES lead_org + KNOWN_CC_PROJECTS company fields.
_FACT_SHEET_COMPANY = {
    'ARCHES':             'Alliance for Renewable Clean Hydrogen Energy Systems',
    'ARCHES v2':          'Alliance for Renewable Clean Hydrogen Energy Systems',
    'ARCH2':              'Battelle',
    'PNWH2':              'Pacific Northwest Hydrogen Association',
    'Gulf Coast Phase 1': 'HyVelocity LLC',
    'Baytown CCS':        'Calpine Texas CCUS Holdings',
    'Baytown CCS Award':  'Calpine Texas CCUS Holdings',
    'Project Tundra':     'Dakota Carbon Center East',
    'CC Pilot Vicksburg': 'RTI International',
    'CC Pilot Cane Run':  'Kentucky Utilities',
    'CC Pilot Big Spring':'Delek US Holdings',
}

# ── Known CC Demo/Pilot projects ────────────────────────────────────────────
# CC Demo selections: March 2023. CC Pilot selections: Feb-Mar 2024.
KNOWN_CC_PROJECTS = [
    {'company': 'Calpine Texas CCUS Holdings', 'project': 'Baytown CCS',
     'location': 'Baytown, TX', 'funding': 'Up to $270M',
     'program': 'CC Demo', 'capacity': '~2M tCO2/yr', 'date': '2023-03-13'},
    {'company': 'Sutter CCUS', 'project': 'Sutter Decarbonization',
     'location': 'Yuba City, CA', 'funding': 'Up to $270M',
     'program': 'CC Demo', 'capacity': '~1.75M tCO2/yr', 'date': '2023-03-13'},
    {'company': 'Dakota Carbon Center East', 'project': 'Project Tundra',
     'location': 'Center, ND', 'funding': 'Up to $350M',
     'program': 'CC Demo', 'capacity': '~4M tCO2/yr', 'date': '2023-03-13'},
    {'company': 'RTI International', 'project': 'CC Pilot Vicksburg Containerboard',
     'location': 'Redwood, MS', 'funding': 'Up to $88M',
     'program': 'CC Pilot', 'capacity': '120K tCO2/yr', 'date': '2024-02-29'},
    {'company': 'Kentucky Utilities', 'project': 'CC Pilot Cane Run',
     'location': 'Louisville, KY', 'funding': 'Up to $72M',
     'program': 'CC Pilot', 'date': '2024-02-29'},
    {'company': 'Delek US Holdings', 'project': 'CC Pilot Big Spring Refinery',
     'location': 'Big Spring, TX', 'funding': 'Up to $95M',
     'program': 'CC Pilot', 'capacity': '145K tCO2/yr', 'date': '2024-03-11'},
    {'company': 'TDA Research', 'project': 'CC Pilot Dry Fork Power Station',
     'location': 'Gillette, WY', 'funding': 'Up to $49M',
     'program': 'CC Pilot', 'capacity': '158K tCO2/yr', 'date': '2024-03-11'},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, retries: int = 3) -> str | None:
    """Fetch URL content as text."""
    headers = {
        'User-Agent': UA,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 403 and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                print(f'  [fetch error] {url[:80]}: {e}')
                return None
            time.sleep(1)
    return None


def strip_html(html: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.I)
    text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', text, flags=re.I)
    text = re.sub(r'<nav[^>]*>[\s\S]*?</nav>', '', text, flags=re.I)
    text = re.sub(r'<aside[^>]*>[\s\S]*?</aside>', '', text, flags=re.I)
    text = re.sub(r'<header[^>]*>[\s\S]*?</header>', '', text, flags=re.I)
    text = re.sub(r'<footer[^>]*>[\s\S]*?</footer>', '', text, flags=re.I)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'&#\d+;', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _extract_article_links(html: str, base: str = 'https://www.energy.gov') -> list[str]:
    """Extract article links from an Award Wednesdays listing page."""
    links = []
    # Match href patterns like /oced/articles/award-wednesdays-...
    for m in re.finditer(r'href="(/oced/articles/[^"]+)"', html):
        path = m.group(1)
        full_url = base + path if path.startswith('/') else path
        if full_url not in links:
            links.append(full_url)
    return links


def _format_known_project(proj: dict, category: str) -> str:
    """Format a known project into structured text."""
    parts = [f'DOE OCED {category}']
    parts.append(f'Company: {proj["company"]}')
    parts.append(f'Project: {proj["project"]}')
    if proj.get('location'):
        parts.append(f'Location: {proj["location"]}')
    if proj.get('funding'):
        parts.append(f'Federal Cost Share: {proj["funding"]}')
    if proj.get('sector'):
        parts.append(f'Sector: {proj["sector"]}')
    if proj.get('program'):
        parts.append(f'Program: {proj["program"]}')
    if proj.get('capacity'):
        parts.append(f'CO2 Capture Capacity: {proj["capacity"]}')
    if proj.get('co2_reduction'):
        parts.append(f'CO2 Reduction: {proj["co2_reduction"]}')
    if proj.get('technology'):
        parts.append(f'Technology: {proj["technology"]}')
    return '\n'.join(parts)


# ── Source A: H2Hubs ─────────────────────────────────────────────────────────
def collect_h2hubs(db: sqlite3.Connection,
                   existing_urls: set) -> tuple[int, int]:
    """Scrape individual H2Hub detail pages."""
    print('\n── Source A: H2Hubs Detail Pages ──────────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    for hub_name, states, url, funding, lead_org, selection_date in H2HUB_PAGES:
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if html:
            text = strip_html(html)
        else:
            # Fallback: create structured text from known data
            text = (f'DOE OCED Regional Clean Hydrogen Hub\n'
                    f'Hub: {hub_name}\n'
                    f'Lead Organization: {lead_org}\n'
                    f'States: {states}\n'
                    f'Federal Cost Share: {funding}\n\n'
                    f'Selected under the Bipartisan Infrastructure Law for the '
                    f'Regional Clean Hydrogen Hubs (H2Hubs) program. H2Hubs are '
                    f'intended to demonstrate large-scale clean hydrogen production, '
                    f'delivery, storage, and end-use across a network of facilities.')

        text = f'DOE OCED H2Hub: {hub_name} ({states})\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] {hub_name:<15} {lead_org[:35]:<35} {states}')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_technology, project_name)
            VALUES (?,NULL,'doe_h2hub',?,?,?,?,'doe_oced_detail',?,?,?)
        """, (lead_org, selection_date, url, text[:50000], len(text), now,
              'hydrogen', hub_name))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source B: IDP Sector Pages ───────────────────────────────────────────────
def collect_idp(db: sqlite3.Connection,
                existing_urls: set) -> tuple[int, int]:
    """Scrape IDP sector pages and insert known projects."""
    print('\n── Source B: Industrial Demonstrations Program ─────────────────')
    inserted = skipped = 0
    now = now_iso()

    # First: scrape sector pages
    for sector, url in IDP_PAGES:
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 200:
            continue

        text = f'DOE OCED Industrial Demonstrations: {sector}\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] IDP {sector}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES ('DOE OCED IDP',NULL,'doe_idp_sector',?,?,?,?,'doe_oced_detail',?)
        """, (now[:10], url, text[:50000], len(text), now))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    # Also try master page
    if IDP_MASTER not in existing_urls:
        html = _get(IDP_MASTER)
        if html:
            text = f'DOE OCED IDP All Projects\n\n{strip_html(html)}'
            if len(text) > 200:
                if DRY_RUN:
                    print(f'  [dry-run] IDP master page  ({len(text)} chars)')
                else:
                    db.execute("""
                        INSERT INTO regulatory_evidence
                        (company_name, company_cik, document_type, document_date,
                         document_url, raw_text_excerpt, excerpt_char_count,
                         source_system, ingested_at)
                        VALUES ('DOE OCED IDP',NULL,'doe_idp_master',?,?,?,?,'doe_oced_detail',?)
                    """, (now[:10], IDP_MASTER, text[:50000], len(text), now))
                existing_urls.add(IDP_MASTER)
                inserted += 1

    # Insert known H2/CCS-relevant IDP projects as structured records
    for proj in KNOWN_IDP_PROJECTS:
        slug = f'{proj["company"]}_{proj["project"]}'.replace(' ', '_').lower()
        doc_url = f'https://energy.gov/oced/idp/{slug}'
        if doc_url in existing_urls:
            skipped += 1
            continue

        text = _format_known_project(proj, 'Industrial Demonstrations Program')

        if DRY_RUN:
            print(f'  [dry-run] IDP: {proj["company"][:30]:<30} {proj["project"][:25]}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_technology, project_name)
            VALUES (?,NULL,'doe_idp_project',?,?,?,?,'doe_oced_detail',?,?,?)
        """, (proj['company'], proj.get('date', now[:10]), doc_url, text[:50000], len(text),
              now, proj.get('technology', ''), proj['project']))
        existing_urls.add(doc_url)
        inserted += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source C: Carbon Capture Programs ────────────────────────────────────────
def collect_cc_programs(db: sqlite3.Connection,
                        existing_urls: set) -> tuple[int, int]:
    """Scrape CC demo, pilot, and FEED study pages."""
    print('\n── Source C: Carbon Capture Programs ───────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    # Scrape program pages
    for label, url in CC_PAGES:
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 200:
            continue

        text = f'DOE OCED Carbon Capture: {label}\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] CC: {label}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_technology)
            VALUES ('DOE OCED CC',NULL,'doe_cc_program',?,?,?,?,'doe_oced_detail',?,?)
        """, (now[:10], url, text[:50000], len(text), now, 'ccs'))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    # Insert known CC projects as structured records
    for proj in KNOWN_CC_PROJECTS:
        slug = f'{proj["company"]}_{proj["project"]}'.replace(' ', '_').lower()
        doc_url = f'https://energy.gov/oced/cc/{slug}'
        if doc_url in existing_urls:
            skipped += 1
            continue

        text = _format_known_project(proj, 'Carbon Capture')

        if DRY_RUN:
            print(f'  [dry-run] CC: {proj["company"][:30]:<30} {proj["project"][:25]}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_technology, project_name)
            VALUES (?,NULL,'doe_cc_project',?,?,?,?,'doe_oced_detail',?,?,?)
        """, (proj['company'], proj.get('date', now[:10]), doc_url, text[:50000], len(text), now,
              'ccs', proj['project']))
        existing_urls.add(doc_url)
        inserted += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source D: Award Wednesdays ───────────────────────────────────────────────
def collect_award_wednesdays(db: sqlite3.Connection,
                              existing_urls: set) -> tuple[int, int]:
    """Crawl Award Wednesdays listing and individual articles."""
    print('\n── Source D: Award Wednesdays ──────────────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    # Collect all article links from paginated listing
    all_article_urls: list[str] = []
    for page_num in range(5):  # pages 0-4
        list_url = f'{AWARD_WEDNESDAYS_BASE}?page={page_num}'
        html = _get(list_url)
        if not html:
            break
        links = _extract_article_links(html)
        if not links:
            break
        all_article_urls.extend(links)
        time.sleep(SLEEP_SEC)

    print(f'  Found {len(all_article_urls)} Award Wednesday articles')

    for article_url in all_article_urls:
        if article_url in existing_urls:
            skipped += 1
            continue

        html = _get(article_url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 200:
            continue

        text = f'DOE OCED Award Wednesday\n\n{text}'

        if DRY_RUN:
            slug = article_url.rsplit('/', 1)[-1][:50]
            print(f'  [dry-run] {slug}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(article_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES ('DOE OCED',NULL,'doe_award_wednesday',?,?,?,?,'doe_oced_detail',?)
        """, (now[:10], article_url, text[:50000], len(text), now))
        existing_urls.add(article_url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source E: Fact Sheet PDFs ────────────────────────────────────────────────
def collect_fact_sheets(db: sqlite3.Connection,
                        existing_urls: set) -> tuple[int, int]:
    """Download fact sheet PDFs and store metadata (text extraction requires pdfplumber)."""
    print('\n── Source E: Fact Sheet PDFs ───────────────────────────────────')

    try:
        import pdfplumber
        has_pdf = True
    except ImportError:
        has_pdf = False

    inserted = skipped = 0
    now = now_iso()
    pdf_dir = _ROOT / 'data' / 'oced_pdfs'

    for label, url in FACT_SHEET_PDFS:
        if url in existing_urls:
            skipped += 1
            continue

        # Download PDF
        try:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=30)
            if r.status_code != 200:
                print(f'  [skip] {label}: HTTP {r.status_code}')
                continue
            pdf_bytes = r.content
        except Exception as e:
            print(f'  [fetch error] {label}: {e}')
            continue

        # Try to extract text
        text = f'DOE OCED Fact Sheet: {label}\n\n'
        if has_pdf and len(pdf_bytes) > 100:
            pdf_dir.mkdir(parents=True, exist_ok=True)
            fname = url.rsplit('/', 1)[-1]
            pdf_path = pdf_dir / fname
            pdf_path.write_bytes(pdf_bytes)

            try:
                with pdfplumber.open(str(pdf_path)) as pdf:
                    pages_text = []
                    for page in pdf.pages:
                        pt = page.extract_text() or ''
                        if pt.strip():
                            pages_text.append(pt)
                    text += '\n\n'.join(pages_text)
            except Exception as e:
                text += f'[PDF parse failed: {e}]'
        else:
            text += f'[PDF binary — {len(pdf_bytes):,} bytes — install pdfplumber for text extraction]'

        if DRY_RUN:
            print(f'  [dry-run] {label:<35} ({len(pdf_bytes):,} bytes)')
            inserted += 1
            existing_urls.add(url)
            continue

        company = _FACT_SHEET_COMPANY.get(label, 'DOE OCED')
        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,'doe_fact_sheet_pdf',?,?,?,?,'doe_oced_detail',?)
        """, (company, now[:10], url, text[:50000], len(text), now))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


def backfill_doe_company_names(db: sqlite3.Connection):
    """One-time fix: update existing records where company_name is a program label.

    Cascades through regulatory_evidence → cleaned_documents → claims.
    Only touches records with company_name IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC').
    Award Wednesday articles (doc type 'doe_award_wednesday') are left as-is.
    """
    print('\n── Backfill: Fix DOE company attribution ──────────────────────')

    # 1. Fix fact-sheet records using _FACT_SHEET_COMPANY + document_url
    updated_re = 0
    for label, url in FACT_SHEET_PDFS:
        company = _FACT_SHEET_COMPANY.get(label)
        if not company:
            continue
        cur = db.execute("""
            UPDATE regulatory_evidence
            SET company_name = ?
            WHERE document_url = ?
              AND company_name IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
              AND source_system = 'doe_oced_detail'
        """, (company, url))
        updated_re += cur.rowcount

    # 2. Fix IDP sector pages using KNOWN_IDP_PROJECTS — match by project name in text
    for proj in KNOWN_IDP_PROJECTS:
        cur = db.execute("""
            UPDATE regulatory_evidence
            SET company_name = ?
            WHERE company_name IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
              AND source_system = 'doe_oced_detail'
              AND document_type IN ('doe_idp_sector', 'doe_idp_master')
              AND raw_text_excerpt LIKE ?
        """, (proj['company'], f'%{proj["project"]}%'))
        updated_re += cur.rowcount

    # 3. Fix CC program pages using KNOWN_CC_PROJECTS — match by project name in text
    for proj in KNOWN_CC_PROJECTS:
        cur = db.execute("""
            UPDATE regulatory_evidence
            SET company_name = ?
            WHERE company_name IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
              AND source_system = 'doe_oced_detail'
              AND document_type IN ('doe_cc_program')
              AND raw_text_excerpt LIKE ?
        """, (proj['company'], f'%{proj["project"]}%'))
        updated_re += cur.rowcount

    print(f'  regulatory_evidence: {updated_re} records updated')

    # 4. Cascade to cleaned_documents
    cur_cd = db.execute("""
        UPDATE cleaned_documents
        SET company_name = (
            SELECT re.company_name FROM regulatory_evidence re
            WHERE re.id = cleaned_documents.source_id
        )
        WHERE source_system = 'doe_oced_detail'
          AND company_name IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
          AND EXISTS (
            SELECT 1 FROM regulatory_evidence re
            WHERE re.id = cleaned_documents.source_id
              AND re.company_name NOT IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
          )
    """)
    print(f'  cleaned_documents:   {cur_cd.rowcount} records updated')

    # 5. Cascade to claims
    cur_cl = db.execute("""
        UPDATE claims
        SET company_name = (
            SELECT cd.company_name FROM cleaned_documents cd
            WHERE cd.source_id = claims.source_id
        )
        WHERE company_name IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
          AND EXISTS (
            SELECT 1 FROM cleaned_documents cd
            WHERE cd.source_id = claims.source_id
              AND cd.company_name NOT IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
          )
    """)
    print(f'  claims:              {cur_cl.rowcount} records updated')

    db.commit()
    print('  Backfill complete.')


def cross_reference_doe_status(db: sqlite3.Connection):
    """Log which DOE OCED companies have/lack doe_status tracking."""
    print('\n── Cross-reference: DOE OCED vs doe_status ─────────────────────')

    detail_companies = db.execute("""
        SELECT DISTINCT company_name FROM regulatory_evidence
        WHERE source_system = 'doe_oced_detail'
          AND company_name NOT IN ('DOE OCED', 'DOE OCED IDP', 'DOE OCED CC')
    """).fetchall()

    active = terminated = no_status = 0
    for row in detail_companies:
        name = row['company_name']
        match = db.execute("""
            SELECT doe_award_status, doe_termination_date
            FROM doe_status
            WHERE UPPER(company_name) LIKE UPPER(?)
            ORDER BY document_date DESC LIMIT 1
        """, (f'%{name[:15]}%',)).fetchone()

        if match and match['doe_award_status'] == 'terminated':
            print(f'  TERMINATED: {name}')
            terminated += 1
        elif match:
            print(f'  ACTIVE:     {name} (status={match["doe_award_status"]})')
            active += 1
        else:
            print(f'  NO STATUS:  {name} — not tracked in doe_status')
            no_status += 1

    print(f'\n  Summary: {active} active, {terminated} terminated, {no_status} untracked')


def print_stats(db: sqlite3.Connection):
    """Show current OCED detail coverage."""
    print('\n=== DOE OCED DETAIL COVERAGE ===')
    for doc_type in ['doe_h2hub', 'doe_idp_sector', 'doe_idp_master',
                     'doe_idp_project', 'doe_cc_program', 'doe_cc_project',
                     'doe_award_wednesday', 'doe_fact_sheet_pdf']:
        cnt = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence WHERE document_type=?",
            (doc_type,)).fetchone()[0]
        if cnt:
            print(f'  {doc_type:<30} {cnt:>4} records')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='doe_oced_detail'"
    ).fetchone()[0]
    print(f'\n  Total doe_oced_detail records: {total}')

    # Also show original collect_doe.py records
    orig = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system LIKE 'doe%'"
    ).fetchone()[0]
    print(f'  Total all DOE records: {orig}')


def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    if BACKFILL:
        backfill_doe_company_names(db)
        cross_reference_doe_status(db)
        print_stats(db)
        db.close()
        return

    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system='doe_oced_detail'"
    )}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — DOE OCED Detail')
    print(f'  Existing doe_oced_detail records: {len(existing)}')

    run_all = not any([HUBS_ONLY, IDP_ONLY, CC_ONLY, AWARDS_ONLY])
    total_ins = total_skip = 0

    def _done():
        return _LIMIT > 0 and total_ins >= _LIMIT

    if (run_all or HUBS_ONLY) and not _done():
        ins, skip = collect_h2hubs(db, existing)
        total_ins += ins; total_skip += skip

    if (run_all or IDP_ONLY) and not _done():
        ins, skip = collect_idp(db, existing)
        total_ins += ins; total_skip += skip

    if (run_all or CC_ONLY) and not _done():
        ins, skip = collect_cc_programs(db, existing)
        total_ins += ins; total_skip += skip

    if (run_all or AWARDS_ONLY) and not _done():
        ins, skip = collect_award_wednesdays(db, existing)
        total_ins += ins; total_skip += skip

    if run_all and not _done():
        ins, skip = collect_fact_sheets(db, existing)
        total_ins += ins; total_skip += skip

    print(f'\n  TOTAL: Inserted={total_ins}  Skipped={total_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
