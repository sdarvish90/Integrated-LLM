#!/usr/bin/env python3
"""
DecarbIQ — collect_epa_uic.py
Fetch EPA UIC Class VI (CO2 geologic sequestration) permit data.

No REST API exists for Class VI. Data is scraped from:
  Source A: EPA Class VI Permit Tracker PDF (monthly update)
  Source B: EPA regional program pages (Regions 5, 6, 9)
  Source C: Individual EPA project pages (issued permits)
  Source D: State primacy agencies (ND DMR, LA DENR, WY DEQ)

Writes to regulatory_evidence in core_database.db with source_system='epa_uic'.

Usage:
    python collect_epa_uic.py              # full run
    python collect_epa_uic.py --dry-run    # show what would be inserted
    python collect_epa_uic.py --stats      # current UIC coverage
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# Optional PDF parsing — gracefully degrade if not installed
try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'
PDF_DIR = _ROOT / 'data' / 'uic_pdfs'

# ── Config ───────────────────────────────────────────────────────────────────
UA        = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC = 0.5

DRY_RUN    = '--dry-run' in sys.argv
STATS_ONLY = '--stats'   in sys.argv

# --limit N: cap total inserts (for testing)
_LIMIT = 0
for _a in sys.argv:
    if _a.startswith('--limit='):
        _LIMIT = int(_a.split('=', 1)[1])
    elif _a == '--limit':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _LIMIT = int(sys.argv[_idx + 1])

# ── Permit Tracker PDF URL (updated monthly) ────────────────────────────────
# Pattern: /system/files/documents/YYYY-MM/class-vi-permit-tracker_M-D-YY.pdf
# We try the most recent known URL first, then fall back to a search.
TRACKER_PDF_URLS = [
    'https://www.epa.gov/system/files/documents/2026-02/class-vi-permit-tracker_2-19-26.pdf',
    'https://www.epa.gov/system/files/documents/2026-01/class-vi-permit-tracker_1-2-26.pdf',
    'https://www.epa.gov/system/files/documents/2025-03/class-vi-permit-tracker_3-14-25.pdf',
]

# ── EPA Regional Pages ──────────────────────────────────────────────────────
EPA_REGIONAL_PAGES = {
    'R5': 'https://www.epa.gov/uic/class-vi-permit-applications-region-5',
    'R6': 'https://www.epa.gov/uic/underground-injection-control-epa-region-6-ar-la-nm-ok-and-tx',
    'R9': 'https://www.epa.gov/uic/r9-uic-permits',
}

# ── Individual EPA Project Pages (issued permits) ───────────────────────────
EPA_PROJECT_PAGES = [
    ('Carbon TerraVault JV', 'CA', 'https://www.epa.gov/uic/carbon-terravault-jv-storage-company-sub-1-llc'),
    ('Archer Daniels Midland CCS1', 'IL', 'https://www.epa.gov/uic/archer-daniels-midland-ccs1-class-vi-permit-documents'),
    ('Archer Daniels Midland CCS2', 'IL', 'https://www.epa.gov/uic/archer-daniels-midland-ccs2-class-vi-permit-documents'),
    ('Wabash Carbon Services', 'IN', 'https://www.epa.gov/uic/wabash-carbon-services-class-vi-permit'),
]

# ── EPA News Releases (final permit announcements) ─────────────────────────
EPA_NEWS_PAGES = [
    ('ExxonMobil Rose Project', 'TX',
     'https://www.epa.gov/newsreleases/epa-issues-three-class-vi-permits-exxonmobil-jefferson-county-texas'),
    ('Carbon TerraVault CTV-1', 'CA',
     'https://www.epa.gov/newsreleases/epa-issues-first-ever-underground-injection-permits-carbon-sequestration-california'),
    ('Oxy STRATOS DAC', 'TX',
     'https://www.epa.gov/newsreleases/epa-issues-final-permits-geologic-sequestration-carbon-dioxide-texas'),
]

# ── State Primacy Agency Pages ──────────────────────────────────────────────
STATE_PAGES = {
    'ND': 'https://www.dmr.nd.gov/dmr/oilgas/ClassVI',
    'WY': 'https://deq.wyoming.gov/water-quality/groundwater/uic/class-vi/',
    'LA': 'https://www.denr.louisiana.gov/page/permits-and-applications',
}

# ── Known Class VI Projects (structured data from research) ─────────────────
# These are embedded as known data to ensure coverage even if scraping fails.
KNOWN_PROJECTS = [
    # EPA-issued final permits
    {'operator': 'Archer Daniels Midland', 'project': 'CCS1 Decatur',
     'state': 'IL', 'county': 'Macon', 'city': 'Decatur', 'wells': 1,
     'permit': 'IL-115-6A-0001', 'status': 'final_permit',
     'date': '2014-09-26', 'region': 'EPA R5'},
    {'operator': 'Archer Daniels Midland', 'project': 'CCS2 Decatur',
     'state': 'IL', 'county': 'Macon', 'city': 'Decatur', 'wells': 1,
     'permit': 'IL-115-6A-0002', 'status': 'final_permit',
     'date': '2017-01-01', 'region': 'EPA R5'},
    {'operator': 'Carbon TerraVault JV Storage Co Sub 1 LLC', 'project': 'CTV-1 26R Elk Hills',
     'state': 'CA', 'county': 'Kern', 'city': 'Elk Hills', 'wells': 4,
     'permit': 'R9UIC-CA6-FY22-1.1', 'status': 'final_permit',
     'date': '2024-12-30', 'region': 'EPA R9'},
    {'operator': 'Wabash Carbon Services', 'project': 'Wabash CCS',
     'state': 'IN', 'county': 'Vigo', 'city': 'West Terre Haute', 'wells': 2,
     'permit': 'IN-167-6A-0001', 'status': 'final_permit',
     'date': '2024-01-01', 'region': 'EPA R5'},
    {'operator': 'ExxonMobil Low Carbon Solutions', 'project': 'Rose Project',
     'state': 'TX', 'county': 'Jefferson', 'wells': 3,
     'permit': 'EPA-R06-OW-2025-0421', 'status': 'final_permit',
     'date': '2025-10-21', 'region': 'EPA R6'},
    {'operator': 'Oxy Low Carbon Ventures LLC', 'project': 'Brown Pelican STRATOS DAC',
     'state': 'TX', 'county': 'Ector', 'wells': 3,
     'status': 'final_permit', 'date': '2025-04-07', 'region': 'EPA R6'},
    # North Dakota (state primacy)
    {'operator': 'Red Trail Energy LLC', 'project': 'Richardton Ethanol Broom Creek',
     'state': 'ND', 'county': 'Stark', 'wells': 1,
     'status': 'operational', 'region': 'ND DMR'},
    {'operator': 'Minnkota Power Cooperative', 'project': 'Center MRYS Broom Creek',
     'state': 'ND', 'county': 'Oliver', 'wells': 1,
     'status': 'approved', 'region': 'ND DMR'},
    {'operator': 'Minnkota Power Cooperative', 'project': 'Center MRYS Deadwood',
     'state': 'ND', 'county': 'Oliver', 'wells': 1,
     'status': 'approved', 'region': 'ND DMR'},
    {'operator': 'Dakota Gasification Company', 'project': 'DGC Beulah Broom Creek',
     'state': 'ND', 'county': 'Mercer', 'wells': 6,
     'status': 'operational', 'region': 'ND DMR'},
    {'operator': 'Blue Flint Sequester Company LLC', 'project': 'Underwood Broom Creek',
     'state': 'ND', 'county': 'McLean', 'wells': 1,
     'status': 'operational', 'region': 'ND DMR'},
    {'operator': 'Summit Carbon Storage #1 LLC', 'project': 'TB Leingang Broom Creek',
     'state': 'ND', 'county': 'Mercer', 'wells': 1,
     'status': 'approved', 'region': 'ND DMR'},
    {'operator': 'Summit Carbon Storage #2 LLC', 'project': 'BK Fischer Broom Creek',
     'state': 'ND', 'county': 'Oliver', 'wells': 1,
     'status': 'approved', 'region': 'ND DMR'},
    {'operator': 'Summit Carbon Storage #3 LLC', 'project': 'KJ Hintz Broom Creek',
     'state': 'ND', 'county': 'Oliver', 'wells': 1,
     'status': 'approved', 'region': 'ND DMR'},
    {'operator': 'DCC West Project LLC', 'project': 'DCC West Center Broom Creek',
     'state': 'ND', 'county': 'Oliver', 'wells': 1,
     'status': 'approved', 'region': 'ND DMR'},
    # Wyoming (state primacy)
    {'operator': 'Frontier Carbon Solutions', 'project': 'Sweetwater Carbon Storage Hub',
     'state': 'WY', 'county': 'Sweetwater', 'wells': 3,
     'status': 'approved', 'region': 'WY DEQ'},
    # EPA Region 9 — under review
    {'operator': 'California Resources Corp', 'project': 'CTV I Elk Hills A1-A2',
     'state': 'CA', 'county': 'Kern', 'wells': 2,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'California Resources Corp', 'project': 'CTV II',
     'state': 'CA', 'county': 'San Joaquin', 'wells': 5,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'California Resources Corp', 'project': 'CTV III',
     'state': 'CA', 'county': 'San Joaquin', 'wells': 6,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'California Resources Corp', 'project': 'CTV IV',
     'state': 'CA', 'county': 'Sacramento', 'wells': 8,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'California Resources Corp', 'project': 'CTV V',
     'state': 'CA', 'county': 'San Joaquin', 'wells': 6,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'California Resources Corp', 'project': 'CTV VI',
     'state': 'CA', 'county': 'Fresno', 'wells': 7,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'CarbonFrontier / Aera Energy LLC', 'project': 'CarbonFrontier',
     'state': 'CA', 'county': 'Kern', 'wells': 9,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'Chevron', 'project': 'Kern River Eastridge CCS',
     'state': 'CA', 'county': 'Kern', 'wells': 4,
     'status': 'on_hold', 'region': 'EPA R9'},
    {'operator': 'Montezuma Carbon LLC', 'project': 'NorCal Carbon Hub',
     'state': 'CA', 'county': 'Solano', 'wells': 1,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'Pelican Renewables LLC', 'project': 'Pelican Renewables',
     'state': 'CA', 'county': 'San Joaquin', 'wells': 2,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'Calpine California CCUS Holdings', 'project': 'Sutter Decarbonization',
     'state': 'CA', 'county': 'Sutter', 'wells': 3,
     'status': 'under_review', 'region': 'EPA R9'},
    {'operator': 'TCCSP LLC', 'project': 'Tulare County Carbon Storage',
     'state': 'CA', 'county': 'Tulare', 'wells': 2,
     'status': 'under_review', 'region': 'EPA R9'},
    # EPA Region 5 — under review
    {'operator': 'Heartland Greenway Carbon Storage', 'project': 'Vervain',
     'state': 'IL', 'county': 'McLean', 'status': 'under_review', 'region': 'EPA R5'},
    {'operator': 'Marquis Energy', 'project': 'Marquis CCS',
     'state': 'IL', 'county': 'Putnam', 'status': 'under_review', 'region': 'EPA R5'},
    {'operator': 'One Earth Sequestration LLC', 'project': 'One Earth CCS',
     'state': 'IL', 'county': 'McLean', 'status': 'under_review', 'region': 'EPA R5'},
    {'operator': 'Heartland Greenway Carbon Storage', 'project': 'Christian County CCS',
     'state': 'IL', 'county': 'Christian', 'status': 'under_review', 'region': 'EPA R5'},
    {'operator': 'Archer Daniels Midland', 'project': 'Maroa CCS',
     'state': 'IL', 'county': 'Macon', 'status': 'under_review', 'region': 'EPA R5'},
    {'operator': 'Lorain Carbon Zero Solutions', 'project': 'Lorain CCS',
     'state': 'OH', 'county': 'Lorain', 'status': 'under_review', 'region': 'EPA R5'},
    # Louisiana (state primacy)
    {'operator': 'Hackberry Carbon Sequestration LLC', 'project': 'Hackberry Sequestration',
     'state': 'LA', 'county': 'Cameron', 'wells': 1,
     'status': 'approved', 'date': '2025-09-05', 'region': 'LA DENR'},
    {'operator': 'Air Products Blue Energy LLC', 'project': 'LCEC South',
     'state': 'LA', 'county': 'St. John the Baptist', 'wells': 5,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'BKVerde LLC', 'project': 'Donaldsonville',
     'state': 'LA', 'county': 'Ascension', 'wells': 1,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'CapturePoint Solutions LLC', 'project': 'CCS 1 Wilcox',
     'state': 'LA', 'county': 'Rapides', 'wells': 6,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'CapturePoint Solutions LLC', 'project': 'CCS 2 Wilcox 2',
     'state': 'LA', 'county': 'Vernon', 'wells': 6,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Cleco Power LLC', 'project': 'Diamond Vault',
     'state': 'LA', 'county': 'Rapides', 'wells': 6,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'ExxonMobil Low Carbon Solutions', 'project': 'Pecan Island',
     'state': 'LA', 'county': 'Vermilion', 'wells': 2,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'ExxonMobil Low Carbon Solutions', 'project': 'Hummingbird',
     'state': 'LA', 'county': 'Allen', 'wells': 5,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'ExxonMobil Low Carbon Solutions', 'project': 'Mockingbird',
     'state': 'LA', 'county': 'Allen', 'wells': 4,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Gulf Coast Sequestration', 'project': 'Minerva',
     'state': 'LA', 'county': 'Cameron', 'wells': 4,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Gulf Coast Sequestration', 'project': 'Goose Lake',
     'state': 'LA', 'county': 'Calcasieu', 'wells': 2,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Harvest Bend CCS LLC', 'project': 'White Castle',
     'state': 'LA', 'county': 'Iberville', 'wells': 3,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Live Oak CCS LLC', 'project': 'Live Oak CCS Hub',
     'state': 'LA', 'county': 'West Baton Rouge', 'wells': 8,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Louisiana Green Fuels LLC', 'project': 'LGF Columbia',
     'state': 'LA', 'county': 'Caldwell', 'wells': 3,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Magnolia Sequestration Hub LLC', 'project': 'Magnolia Sequestration',
     'state': 'LA', 'county': 'Allen', 'wells': 4,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Onstream CO2 LLC', 'project': 'GeoDura',
     'state': 'LA', 'county': 'Cameron', 'wells': 6,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Pelican Sequestration Hub LLC', 'project': 'Pelican Sequestration',
     'state': 'LA', 'county': 'Livingston', 'wells': 5,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'River Parish Sequestration LLC', 'project': 'RPN 1-5 and RPS 1-2',
     'state': 'LA', 'county': 'Ascension', 'wells': 7,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Shell US Power and Gas LLC', 'project': 'El Camino',
     'state': 'LA', 'county': 'St. Helena', 'wells': 2,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Lapis Energy', 'project': 'Libra CO2 Storage Solutions',
     'state': 'LA', 'county': 'St. Charles', 'wells': 3,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'High West Sequestration LLC', 'project': 'High West CCS',
     'state': 'LA', 'county': 'St. Charles', 'wells': 5,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'DT Midstream Holdings LLC', 'project': 'LA CCS',
     'state': 'LA', 'county': 'Sabine', 'wells': 1,
     'status': 'under_review', 'region': 'LA DENR'},
    {'operator': 'Aethon Energy Operating LLC', 'project': 'LA Wilcox',
     'state': 'LA', 'county': 'Vernon', 'wells': 6,
     'status': 'under_review', 'region': 'LA DENR'},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, as_bytes: bool = False, retries: int = 3):
    """Fetch URL content. Returns text, bytes, or None."""
    headers = {'User-Agent': UA}
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 403 and attempt < retries - 1:
                time.sleep(2)
                continue
            r.raise_for_status()
            return r.content if as_bytes else r.text
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
    # Remove navigation, sidebar, header, footer elements to avoid
    # LLM extracting RSS feed names and nav boilerplate as claims
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


def format_project_excerpt(proj: dict) -> str:
    """Format a known Class VI project into structured text for LLM extraction."""
    parts = []
    parts.append(f'EPA UIC Class VI Well Permit')
    parts.append(f'Operator: {proj.get("operator", "Unknown")}')
    parts.append(f'Project: {proj.get("project", "")}')
    parts.append(f'State: {proj.get("state", "")}')
    if proj.get('county'):
        parts.append(f'County: {proj["county"]}')
    if proj.get('city'):
        parts.append(f'City: {proj["city"]}')
    if proj.get('wells'):
        parts.append(f'Number of Wells: {proj["wells"]}')
    if proj.get('permit'):
        parts.append(f'Permit Number: {proj["permit"]}')

    status = proj.get('status', '')
    status_labels = {
        'final_permit': 'Final Permit Issued',
        'approved': 'Approved',
        'operational': 'Operational (injecting)',
        'under_review': 'Under Review',
        'on_hold': 'On Hold',
        'withdrawn': 'Withdrawn',
    }
    parts.append(f'Status: {status_labels.get(status, status)}')

    if proj.get('date'):
        parts.append(f'Date: {proj["date"]}')
    if proj.get('region'):
        parts.append(f'Regulatory Authority: {proj["region"]}')
    if proj.get('co2_volume'):
        parts.append(f'CO2 Storage Capacity: {proj["co2_volume"]}')

    parts.append('')
    parts.append('UIC Class VI permits authorize injection of CO2 into deep '
                 'geologic formations for long-term storage (carbon capture and storage).')

    if proj.get('region', '').startswith('EPA'):
        parts.append('This permit was issued/reviewed by the U.S. EPA under the '
                     'Safe Drinking Water Act, Underground Injection Control program.')
    elif 'ND' in proj.get('region', ''):
        parts.append('North Dakota has Class VI primacy (granted April 2018). '
                     'Permits issued by ND Department of Mineral Resources.')
    elif 'WY' in proj.get('region', ''):
        parts.append('Wyoming has Class VI primacy (granted October 2020). '
                     'Permits issued by WY Department of Environmental Quality.')
    elif 'LA' in proj.get('region', ''):
        parts.append('Louisiana has Class VI primacy (granted December 2023). '
                     'Permits issued by LA Department of Energy and Natural Resources.')
    elif 'TX' in proj.get('region', ''):
        parts.append('Texas has Class VI primacy (granted December 2025). '
                     'Permits issued by TX Railroad Commission.')

    return '\n'.join(parts)


# ── Source A: Known Projects (structured data) ──────────────────────────────
def collect_known_projects(db: sqlite3.Connection,
                           existing_urls: set) -> tuple[int, int]:
    """Insert all known Class VI projects from curated list."""
    print('\n── Source A: Known Class VI Projects ──────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    for proj in KNOWN_PROJECTS:
        operator = proj.get('operator', 'Unknown')
        project = proj.get('project', '')
        state = proj.get('state', '')
        permit = proj.get('permit', '')

        # Construct a stable dedup URL
        slug = f'{operator}_{project}_{state}'.replace(' ', '_').lower()
        doc_url = f'https://epa.gov/uic/class-vi/{slug}'

        if doc_url in existing_urls:
            skipped += 1
            continue

        text = format_project_excerpt(proj)
        doc_date = proj.get('date') or now[:10]
        status = proj.get('status', 'under_review')

        # Map status to derived_stage
        stage_map = {
            'final_permit': 'permitted',
            'approved': 'permitted',
            'operational': 'operational',
            'under_review': 'permitting',
            'on_hold': 'on_hold',
            'withdrawn': 'cancelled',
        }
        derived_stage = stage_map.get(status, '')

        if DRY_RUN:
            print(f'  [dry-run] {operator[:40]:<40} {project[:25]:<25} '
                  f'{state:<3} {status}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_stage, derived_technology,
             permit_id, project_name)
            VALUES (?,NULL,'uic_class_vi_permit',?,?,?,?,'epa_uic',?,?,?,
                    ?,?)
        """, (operator, doc_date, doc_url, text[:50000], len(text),
              now, derived_stage, 'ccs',
              permit if permit else None,
              project if project else None))
        existing_urls.add(doc_url)
        inserted += 1

    if not DRY_RUN:
        db.commit()

    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source B: EPA Regional Pages ────────────────────────────────────────────
def collect_regional_pages(db: sqlite3.Connection,
                           existing_urls: set) -> tuple[int, int]:
    """Scrape EPA regional pages for Class VI project info."""
    print('\n── Source B: EPA Regional Pages ─────────────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    for region, url in EPA_REGIONAL_PAGES.items():
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 200:
            continue

        # Prepend region context
        text = f'EPA {region} UIC Class VI Program Page\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] EPA {region} page  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES ('EPA UIC',NULL,'uic_regional_page',?,?,?,?,'epa_uic',?)
        """, (now[:10], url, text[:50000], len(text), now))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source C: EPA Project Pages + News Releases ─────────────────────────────
def collect_project_pages(db: sqlite3.Connection,
                          existing_urls: set) -> tuple[int, int]:
    """Scrape individual EPA project pages and news releases."""
    print('\n── Source C: EPA Project Pages & News Releases ────────────────')
    inserted = skipped = 0
    now = now_iso()

    all_pages = [(name, st, url, 'uic_project_page')
                 for name, st, url in EPA_PROJECT_PAGES]
    all_pages += [(name, st, url, 'uic_news_release')
                  for name, st, url in EPA_NEWS_PAGES]

    for company, state, url, doc_type in all_pages:
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 100:
            continue

        text = f'EPA UIC Class VI: {company} ({state})\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] {company[:40]:<40} {state}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_technology)
            VALUES (?,NULL,?,?,?,?,?,'epa_uic',?,?)
        """, (company, doc_type, now[:10], url, text[:50000], len(text), now, 'ccs'))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source D: State Primacy Pages ───────────────────────────────────────────
def collect_state_pages(db: sqlite3.Connection,
                        existing_urls: set) -> tuple[int, int]:
    """Scrape state agency pages for Class VI data."""
    print('\n── Source D: State Primacy Agency Pages ──────────────────────')
    inserted = skipped = 0
    now = now_iso()

    for state, url in STATE_PAGES.items():
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 100:
            continue

        label = {'ND': 'ND DMR', 'WY': 'WY DEQ', 'LA': 'LA DENR'}.get(state, state)
        text = f'{label} UIC Class VI Program Page\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] {label} page  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,'uic_state_page',?,?,?,?,'epa_uic',?)
        """, (label, now[:10], url, text[:50000], len(text), now))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source E: Permit Tracker PDF ────────────────────────────────────────────
def collect_tracker_pdf(db: sqlite3.Connection,
                        existing_urls: set) -> tuple[int, int]:
    """Download and parse the EPA Class VI Permit Tracker PDF."""
    if not HAS_PDF:
        print('\n── Source E: Permit Tracker PDF (SKIPPED — pip install pdfplumber) ──')
        return 0, 0

    print('\n── Source E: Permit Tracker PDF ─────────────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    # Try each PDF URL
    pdf_data = None
    pdf_url = None
    for url in TRACKER_PDF_URLS:
        if url in existing_urls:
            skipped += 1
            continue
        data = _get(url, as_bytes=True)
        if data and len(data) > 1000:
            pdf_data = data
            pdf_url = url
            break

    if not pdf_data:
        print('  Could not fetch any Permit Tracker PDF')
        return 0, skipped

    # Save PDF locally
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = PDF_DIR / pdf_url.rsplit('/', 1)[-1]
    pdf_path.write_bytes(pdf_data)
    print(f'  Saved: {pdf_path.name} ({len(pdf_data):,} bytes)')

    # Extract text from PDF
    try:
        text_parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ''
                if page_text.strip():
                    text_parts.append(page_text)
        text = '\n\n'.join(text_parts)
    except Exception as e:
        print(f'  PDF parse error: {e}')
        return 0, 0

    if len(text) < 100:
        print('  PDF text too short — Gantt chart format may need different parser')
        return 0, 0

    text = f'EPA UIC Class VI Permit Tracker (official monthly update)\n\n{text}'
    print(f'  Extracted {len(text):,} chars from {len(text_parts)} pages')

    if DRY_RUN:
        print(f'  [dry-run] Permit Tracker PDF  ({len(text)} chars)')
        return 1, skipped

    db.execute("""
        INSERT INTO regulatory_evidence
        (company_name, company_cik, document_type, document_date,
         document_url, raw_text_excerpt, excerpt_char_count,
         source_system, ingested_at)
        VALUES ('EPA UIC',NULL,'uic_permit_tracker_pdf',?,?,?,?,'epa_uic',?)
    """, (now[:10], pdf_url, text[:50000], len(text), now))
    existing_urls.add(pdf_url)
    db.commit()

    print(f'  Inserted permit tracker PDF')
    return 1, skipped


def print_stats(db: sqlite3.Connection):
    """Show current UIC Class VI coverage."""
    print('\n=== UIC CLASS VI COVERAGE ===')
    for doc_type in ['uic_class_vi_permit', 'uic_regional_page',
                     'uic_project_page', 'uic_news_release',
                     'uic_state_page', 'uic_permit_tracker_pdf']:
        cnt = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence WHERE document_type=?",
            (doc_type,)).fetchone()[0]
        if cnt:
            print(f'  {doc_type:<30} {cnt:>4} records')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='epa_uic'"
    ).fetchone()[0]
    print(f'\n  Total epa_uic records: {total}')

    # By state
    rows = db.execute("""
        SELECT raw_text_excerpt FROM regulatory_evidence
        WHERE source_system='epa_uic' AND document_type='uic_class_vi_permit'
    """).fetchall()
    states: dict[str, int] = {}
    for r in rows:
        m = re.search(r'State: ([A-Z]{2})', r[0] or '')
        if m:
            st = m.group(1)
            states[st] = states.get(st, 0) + 1
    if states:
        print('\n  By state:')
        for st in sorted(states, key=lambda s: -states[s]):
            print(f'    {st}: {states[st]}')


def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system='epa_uic'"
    )}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — EPA UIC Class VI')
    print(f'  Existing epa_uic records: {len(existing)}')

    total_ins = total_skip = 0

    def _done():
        return _LIMIT > 0 and total_ins >= _LIMIT

    ins, skip = collect_known_projects(db, existing)
    total_ins += ins; total_skip += skip

    if not _done():
        ins, skip = collect_regional_pages(db, existing)
        total_ins += ins; total_skip += skip

    if not _done():
        ins, skip = collect_project_pages(db, existing)
        total_ins += ins; total_skip += skip

    if not _done():
        ins, skip = collect_state_pages(db, existing)
        total_ins += ins; total_skip += skip

    if not _done():
        ins, skip = collect_tracker_pdf(db, existing)
        total_ins += ins; total_skip += skip

    print(f'\n  TOTAL: Inserted={total_ins}  Skipped={total_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
