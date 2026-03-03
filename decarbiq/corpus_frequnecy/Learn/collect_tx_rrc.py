#!/usr/bin/env python3
"""
DecarbIQ — collect_tx_rrc.py
Fetch Texas Railroad Commission (RRC) CO2 injection / CCS permit data.

Texas received Class VI primacy December 15, 2025. No REST API exists.

Sources:
  Source A: CO2 Notices page — approved and draft Class VI permits (HTML scrape)
  Source B: Class VI Application List PDF (downloadable from RRC website)
  Source C: Known CCS projects — structured data from research
  Source D: RRC UIC Online Query page — metadata only (automated queries prohibited)

Writes to regulatory_evidence in core_database.db with source_system='tx_rrc'.

Usage:
    python collect_tx_rrc.py              # full run
    python collect_tx_rrc.py --dry-run    # show what would be inserted
    python collect_tx_rrc.py --stats      # current TX RRC coverage
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
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

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

# ── Source A: CO2 Notices (HTML) ─────────────────────────────────────────────
CO2_NOTICES_URL = 'https://www.rrc.texas.gov/oil-and-gas/applications-and-permits/injection-storage-permits/co2-storage/co2-notices/'
CO2_STORAGE_URL = 'https://www.rrc.texas.gov/oil-and-gas/applications-and-permits/injection-storage-permits/co2-storage/'

# ── Source B: Class VI Application List PDF ──────────────────────────────────
# The filename changes with updates — try known patterns
CLASS_VI_PDF_URLS = [
    'https://www.rrc.texas.gov/media/13nfisyj/class-vi-application-list-22426.pdf',
    'https://www.rrc.texas.gov/media/class-vi-application-list.pdf',
]

# ── Source C: Known TX CCS Projects ──────────────────────────────────────────
KNOWN_TX_PROJECTS = [
    # RRC Class VI — Final Permits
    {'operator': 'Oxy Low Carbon Ventures LLC',
     'project': 'Brown Pelican CO2 Sequestration',
     'county': 'Ector', 'permit': '55294', 'wells': 3,
     'status': 'final_permit', 'date': '2025-10-16',
     'capacity': '0.385 MMTPA initial, 0.77 MMTPA expanded (8.5 MMT total)',
     'depth': 'Permian Lower San Andres Fm, 4500-5100 ft',
     'notes': 'First Class VI permit issued by TX RRC. Three wells: BRP CCS1, CCS2, CCS3.'},
    # RRC Class VI — Draft Permits
    {'operator': 'ExxonMobil Low C Sol On Stor LLC',
     'project': 'Rose CCS Project',
     'county': 'Jefferson', 'permit': '57803', 'wells': 3,
     'status': 'draft_permit', 'date': '2025-11-01',
     'capacity': '~4 MMTPA for 13 years (53 MMT total)',
     'depth': 'Fleming & Upper Frio sands, 3400-7525 ft',
     'notes': 'Three wells: LaBelle Props #1, Bead Farm Co #2, Bead Farm #3.'},
    # Known EPA-transferred applications (now under RRC review)
    {'operator': 'Denbury Carbon Solutions',
     'project': 'Denbury TX CCS (transferred from EPA)',
     'county': 'Various', 'status': 'under_review',
     'notes': 'Transferred from EPA R6 to TX RRC upon primacy grant.'},
    {'operator': 'Orchard Storage Company LLC',
     'project': 'Orchard Storage',
     'status': 'under_review',
     'notes': 'Public notice issued September 2025 (EPA R6). May be transferred to RRC.'},
    # Class II CO2 EOR (enhanced oil recovery) — major operations
    {'operator': 'Denbury Inc',
     'project': 'Hastings CO2 EOR',
     'county': 'Brazoria', 'status': 'operational',
     'notes': 'Class II permit. CO2 injection for enhanced oil recovery. '
              'Denbury operates largest CO2 EOR network in the Gulf Coast.'},
    {'operator': 'Denbury Inc',
     'project': 'Oyster Bayou CO2 EOR',
     'county': 'Chambers', 'status': 'operational',
     'notes': 'Class II permit. CO2 injection for enhanced oil recovery.'},
    {'operator': 'Denbury Inc',
     'project': 'Thompson CO2 EOR',
     'county': 'Fort Bend', 'status': 'operational',
     'notes': 'Class II permit. CO2 injection for enhanced oil recovery.'},
    {'operator': 'Denbury Inc',
     'project': 'West Hastings CO2 EOR',
     'county': 'Brazoria', 'status': 'operational',
     'notes': 'Class II permit. DOE CarbonSAFE partner site. '
              'Receives anthropogenic CO2 from industrial sources.'},
    # Additional TX CCS projects in development
    {'operator': 'Talos Energy',
     'project': 'Bayou Bend CCS Hub',
     'county': 'Jefferson', 'status': 'pre_fid',
     'notes': 'Offshore-adjacent CCS hub. Joint venture with Chevron and Carbonvert. '
              'Application submitted to EPA R6 before TX primacy.'},
    {'operator': 'Enterprise Products Partners',
     'project': 'Enterprise CO2 Sequestration',
     'county': 'Chambers', 'status': 'pre_fid',
     'notes': 'CO2 sequestration near Mont Belvieu. Leverages existing pipeline network.'},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, as_bytes: bool = False, retries: int = 3):
    """Fetch URL content."""
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


def format_tx_project(proj: dict) -> str:
    """Format a known TX CCS project into structured text."""
    parts = []
    parts.append('Texas Railroad Commission — CO2 Injection / CCS Permit')
    parts.append(f'Operator: {proj.get("operator", "Unknown")}')
    parts.append(f'Project: {proj.get("project", "")}')
    parts.append(f'State: TX')

    if proj.get('county'):
        parts.append(f'County: {proj["county"]}')
    if proj.get('permit'):
        parts.append(f'RRC Permit Number: {proj["permit"]}')
    if proj.get('wells'):
        parts.append(f'Number of Wells: {proj["wells"]}')

    status = proj.get('status', '')
    status_labels = {
        'final_permit': 'Final Permit Approved',
        'draft_permit': 'Draft Permit (Public Comment)',
        'under_review': 'Under Review',
        'operational': 'Operational',
        'pre_fid': 'Pre-FID / Application Filed',
    }
    parts.append(f'Status: {status_labels.get(status, status)}')

    if proj.get('date'):
        parts.append(f'Date: {proj["date"]}')
    if proj.get('capacity'):
        parts.append(f'CO2 Storage Capacity: {proj["capacity"]}')
    if proj.get('depth'):
        parts.append(f'Injection Zone: {proj["depth"]}')
    if proj.get('notes'):
        parts.append(f'\n{proj["notes"]}')

    parts.append('')
    parts.append('Texas received Class VI well primacy from EPA on December 15, 2025. '
                 'The TX Railroad Commission now issues Class VI permits for CO2 geologic '
                 'sequestration within the state. Previously, these permits were issued '
                 'by EPA Region 6.')

    return '\n'.join(parts)


# ── Source A: CO2 Notices HTML page ──────────────────────────────────────────
def collect_co2_notices(db: sqlite3.Connection,
                        existing_urls: set) -> tuple[int, int]:
    """Scrape the RRC CO2 Notices page for permit announcements."""
    print('\n── Source A: RRC CO2 Notices ───────────────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    for url, label in [(CO2_NOTICES_URL, 'CO2 Notices'),
                       (CO2_STORAGE_URL, 'CO2 Storage Program')]:
        if url in existing_urls:
            skipped += 1
            continue

        html = _get(url)
        if not html:
            continue

        text = strip_html(html)
        if len(text) < 200:
            continue

        text = f'Texas RRC {label}\n\n{text}'

        if DRY_RUN:
            print(f'  [dry-run] {label}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_technology)
            VALUES ('TX RRC',NULL,'rrc_co2_notice',?,?,?,?,'tx_rrc',?,?)
        """, (now[:10], url, text[:50000], len(text), now, 'ccs'))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    if not DRY_RUN:
        db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped}')
    return inserted, skipped


# ── Source B: Class VI Application List PDF ──────────────────────────────────
def collect_class_vi_pdf(db: sqlite3.Connection,
                         existing_urls: set) -> tuple[int, int]:
    """Download and parse the Class VI Application List PDF."""
    if not HAS_PDF:
        print('\n── Source B: Class VI PDF (SKIPPED — pip install pdfplumber) ──')
        return 0, 0

    print('\n── Source B: Class VI Application List PDF ────────────────────')
    inserted = skipped = 0
    now = now_iso()

    pdf_data = None
    pdf_url = None
    for url in CLASS_VI_PDF_URLS:
        if url in existing_urls:
            skipped += 1
            continue
        data = _get(url, as_bytes=True)
        if data and len(data) > 500:
            pdf_data = data
            pdf_url = url
            break

    if not pdf_data:
        print('  Could not fetch Class VI Application List PDF')
        return 0, skipped

    pdf_dir = _ROOT / 'data' / 'rrc_pdfs'
    pdf_dir.mkdir(parents=True, exist_ok=True)
    fname = pdf_url.rsplit('/', 1)[-1]
    pdf_path = pdf_dir / fname
    pdf_path.write_bytes(pdf_data)
    print(f'  Saved: {pdf_path.name} ({len(pdf_data):,} bytes)')

    try:
        text_parts = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                pt = page.extract_text() or ''
                if pt.strip():
                    text_parts.append(pt)
        text = '\n\n'.join(text_parts)
    except Exception as e:
        print(f'  PDF parse error: {e}')
        return 0, 0

    if len(text) < 100:
        print('  PDF text too short')
        return 0, 0

    text = f'TX RRC Class VI Application List (official)\n\n{text}'
    print(f'  Extracted {len(text):,} chars from {len(text_parts)} pages')

    if DRY_RUN:
        print(f'  [dry-run] Class VI Application List  ({len(text)} chars)')
        return 1, skipped

    db.execute("""
        INSERT INTO regulatory_evidence
        (company_name, company_cik, document_type, document_date,
         document_url, raw_text_excerpt, excerpt_char_count,
         source_system, ingested_at, derived_technology)
        VALUES ('TX RRC',NULL,'rrc_class_vi_list',?,?,?,?,'tx_rrc',?,?)
    """, (now[:10], pdf_url, text[:50000], len(text), now, 'ccs'))
    existing_urls.add(pdf_url)
    db.commit()

    print(f'  Inserted Class VI Application List PDF')
    return 1, skipped


# ── Source C: Known TX CCS Projects ──────────────────────────────────────────
def collect_known_projects(db: sqlite3.Connection,
                           existing_urls: set) -> tuple[int, int]:
    """Insert structured data for known TX CCS/EOR projects."""
    print('\n── Source C: Known TX CCS Projects ─────────────────────────────')
    inserted = skipped = 0
    now = now_iso()

    for proj in KNOWN_TX_PROJECTS:
        operator = proj.get('operator', 'Unknown')
        project = proj.get('project', '')
        slug = f'{operator}_{project}'.replace(' ', '_').lower()
        doc_url = f'https://rrc.texas.gov/ccs/{slug}'

        if doc_url in existing_urls:
            skipped += 1
            continue

        text = format_tx_project(proj)
        doc_date = proj.get('date') or now[:10]
        status = proj.get('status', '')

        stage_map = {
            'final_permit': 'permitted',
            'draft_permit': 'permitting',
            'under_review': 'permitting',
            'operational': 'operational',
            'pre_fid': 'pre_fid',
        }
        derived_stage = stage_map.get(status, '')

        if DRY_RUN:
            print(f'  [dry-run] {operator[:35]:<35} {project[:30]:<30} {status}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        permit = proj.get('permit', '')
        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at, derived_stage, derived_technology,
             permit_id, project_name)
            VALUES (?,NULL,'rrc_ccs_project',?,?,?,?,'tx_rrc',?,?,?,
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


def print_stats(db: sqlite3.Connection):
    """Show current TX RRC coverage."""
    print('\n=== TX RRC CCS COVERAGE ===')
    for doc_type in ['rrc_co2_notice', 'rrc_class_vi_list', 'rrc_ccs_project']:
        cnt = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence WHERE document_type=?",
            (doc_type,)).fetchone()[0]
        if cnt:
            print(f'  {doc_type:<30} {cnt:>4} records')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system='tx_rrc'"
    ).fetchone()[0]
    print(f'\n  Total tx_rrc records: {total}')

    # By status
    rows = db.execute("""
        SELECT raw_text_excerpt FROM regulatory_evidence
        WHERE source_system='tx_rrc' AND document_type='rrc_ccs_project'
    """).fetchall()
    statuses: dict[str, int] = {}
    for r in rows:
        m = re.search(r'Status: (.+)', r[0] or '')
        if m:
            st = m.group(1).strip()
            statuses[st] = statuses.get(st, 0) + 1
    if statuses:
        print('\n  By permit status:')
        for st in sorted(statuses, key=lambda s: -statuses[s]):
            print(f'    {st}: {statuses[st]}')


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
        "SELECT document_url FROM regulatory_evidence WHERE source_system='tx_rrc'"
    )}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — TX RRC CCS')
    print(f'  Existing tx_rrc records: {len(existing)}')

    total_ins = total_skip = 0

    def _done():
        return _LIMIT > 0 and total_ins >= _LIMIT

    ins, skip = collect_co2_notices(db, existing)
    total_ins += ins; total_skip += skip

    if not _done():
        ins, skip = collect_class_vi_pdf(db, existing)
        total_ins += ins; total_skip += skip

    if not _done():
        ins, skip = collect_known_projects(db, existing)
        total_ins += ins; total_skip += skip

    print(f'\n  TOTAL: Inserted={total_ins}  Skipped={total_skip}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
