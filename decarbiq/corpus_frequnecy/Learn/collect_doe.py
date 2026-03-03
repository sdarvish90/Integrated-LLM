#!/usr/bin/env python3
"""
DecarbIQ — collect_doe.py
Scrape DOE Office of Clean Energy Demonstrations (OCED) project pages.

Two sources:
  A. OCED project portfolio API  — https://oced-projects.energy.gov/
  B. EERE hydrogen funding award pages

Filter: skip university-only awardees. Keep company-only and company+university
joint awards (university as sub-awardee is fine).

Writes to regulatory_evidence in core_database.db.

Usage:
    python collect_doe.py              # full run
    python collect_doe.py --dry-run    # show what would be inserted
    python collect_doe.py --stats      # current DOE coverage
"""
from __future__ import annotations

import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

# ── Config ───────────────────────────────────────────────────────────────────
UA         = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC  = 0.5
DRY_RUN    = '--dry-run' in sys.argv
STATS_ONLY = '--stats'   in sys.argv

# ── University / research-only detection ─────────────────────────────────────
# Skip if LEAD awardee is a university/national lab/research org.
# Two checks: (1) regex for common patterns, (2) DB lookup in company_classifications.
_UNIV_RE = re.compile(
    r'\b('
    r'university|college|institute of technology|polytechnic|'
    r'regents of|board of trustees|board of regents|'
    r'massachusetts inst|caltech|georgia tech|virginia tech|'
    r'penn state|carnegie mellon|purdue|stanford|harvard|'
    r'ucla|ucsd|uc davis|uc berkeley|ohio state|'
    r'michigan state|iowa state|texas a&m|arizona state|'
    r'colorado school|national renewable energy|nrel|'
    r'argonne national|pacific northwest national|pnnl|'
    r'oak ridge national|ornl|lawrence livermore|'
    r'lawrence berkeley|lbnl|sandia national|los alamos national'
    r')\b',
    re.IGNORECASE,
)

# Loaded lazily from company_classifications table
_CLASSIFIED_SKIP: set[str] | None = None


def _load_skip_set(db: sqlite3.Connection) -> set[str]:
    """Load company names classified as university/government/out_of_scope."""
    global _CLASSIFIED_SKIP
    if _CLASSIFIED_SKIP is None:
        rows = db.execute(
            "SELECT UPPER(company_name) FROM company_classifications "
            "WHERE classification IN ('university', 'government_agency')"
        ).fetchall()
        _CLASSIFIED_SKIP = {r[0] for r in rows}
    return _CLASSIFIED_SKIP


def is_university_only(name: str, db: sqlite3.Connection | None = None) -> bool:
    """True if awardee is a university/research org that should be skipped."""
    if not name:
        return False
    # Check DB classification first (authoritative)
    if db is not None:
        skip_set = _load_skip_set(db)
        if name.upper().strip() in skip_set:
            return True
    # Fallback: regex for university patterns
    return bool(_UNIV_RE.search(name))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, as_json: bool = False, retries: int = 3):
    headers = {'User-Agent': UA}
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=25)
            if resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json() if as_json else resp.text
        except Exception as e:
            if attempt == retries - 1:
                print(f'  [fetch error] {url}: {e}')
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
    text = text.replace('&lt;', '<').replace('&gt;', '>')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── Source A: OCED project portfolio ─────────────────────────────────────────
OCED_API = 'https://oced-projects.energy.gov/api/projects'


def collect_oced_projects(db: sqlite3.Connection,
                          existing_urls: set) -> tuple[int, int, int]:
    """Fetch all OCED projects from the public API (with HTML fallback)."""
    print('\n── Source A: OCED Project Portfolio ──────────────────────────')
    inserted = skipped_univ = skipped_dup = 0
    page = 1
    all_projects: list = []

    while True:
        url  = f'{OCED_API}?page={page}&per_page=100'
        data = _get(url, as_json=True)
        if not data:
            break
        if isinstance(data, list):
            all_projects.extend(data)
            break
        projects = (data.get('data') or data.get('projects') or
                    data.get('results') or [])
        if not projects:
            break
        all_projects.extend(projects)
        total_pages = data.get('total_pages') or data.get('pages') or 1
        if page >= total_pages:
            break
        page += 1
        time.sleep(SLEEP_SEC)

    if not all_projects:
        print('  OCED API returned no results — trying fallback HTML scrape')
        return collect_oced_html(db, existing_urls)

    print(f'  {len(all_projects)} projects from OCED API')

    for proj in all_projects:
        awardee = (
            proj.get('awardee') or proj.get('recipient') or
            proj.get('company') or proj.get('organization') or ''
        ).strip()

        if is_university_only(awardee, db):
            skipped_univ += 1
            continue

        title    = proj.get('title') or proj.get('project_title') or proj.get('name') or ''
        date     = (proj.get('award_date') or proj.get('date') or
                    proj.get('start_date') or '')
        amount   = proj.get('federal_funding') or proj.get('award_amount') or ''
        status   = proj.get('status') or proj.get('project_status') or ''
        proj_url = (proj.get('url') or proj.get('project_url') or
                    proj.get('link') or '')
        state    = proj.get('state') or proj.get('location') or ''

        parts = []
        if title:    parts.append(f'Project: {title}')
        if awardee:  parts.append(f'Awardee: {awardee}')
        if state:    parts.append(f'Location: {state}')
        if amount:   parts.append(f'Federal funding: {amount}')
        if status:   parts.append(f'Status: {status}')
        if proj_url: parts.append(f'Project page: {proj_url}')
        text = '\n'.join(parts)

        doc_url = proj_url or f'https://oced-projects.energy.gov/project/{proj.get("id","")}'

        if doc_url in existing_urls:
            skipped_dup += 1
            continue

        if DRY_RUN:
            print(f'  [dry-run] {awardee[:50]:<50}  {date[:10]}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,'doe_award',?,?,?,?,'doe_oced',?)
        """, (awardee, date[:10] if date else None, doc_url,
              text[:50000], len(text), now_iso()))
        existing_urls.add(doc_url)
        inserted += 1

    db.commit()
    print(f'  Inserted: {inserted}  Skipped (university): {skipped_univ}'
          f'  Skipped (dup): {skipped_dup}')
    return inserted, skipped_univ, skipped_dup


def collect_oced_html(db: sqlite3.Connection,
                      existing_urls: set) -> tuple[int, int, int]:
    """Fallback: scrape key OCED program pages."""
    program_pages = [
        'https://www.energy.gov/oced/hydrogen-hubs',
        'https://www.energy.gov/oced/clean-hydrogen-electrolysis-program',
        'https://www.energy.gov/oced/industrial-demonstrations-program',
        'https://www.energy.gov/fuelcells/hydrogen-shot-funding-and-awards',
    ]
    inserted = skipped_dup = 0
    for page_url in program_pages:
        if page_url in existing_urls:
            skipped_dup += 1
            continue
        html = _get(page_url)
        if not html:
            continue
        text = strip_html(html)
        if DRY_RUN:
            print(f'  [dry-run] {page_url[:70]}')
            inserted += 1
            existing_urls.add(page_url)
            continue
        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES ('DOE OCED',NULL,'doe_program_page',?,?,?,?,'doe_oced',?)
        """, (now_iso()[:10], page_url, text[:50000], len(text), now_iso()))
        existing_urls.add(page_url)
        inserted += 1
        time.sleep(SLEEP_SEC)
    db.commit()
    return inserted, 0, skipped_dup


# ── Source B: EERE hydrogen award pages ──────────────────────────────────────
H2_AWARD_PAGES = [
    'https://www.energy.gov/eere/fuelcells/hydrogen-shot-funding-and-awards',
    'https://www.energy.gov/eere/fuelcells/h2-scale-awards',
    'https://www.energy.gov/oced/articles/oced-selects-projects-hydrogen-hubs',
]


def collect_eere_awards(db: sqlite3.Connection,
                        existing_urls: set) -> tuple[int, int, int]:
    """Scrape EERE hydrogen award pages."""
    print('\n── Source B: EERE Hydrogen Award Pages ────────────────────────')
    inserted = skipped_dup = 0
    for page_url in H2_AWARD_PAGES:
        if page_url in existing_urls:
            skipped_dup += 1
            continue
        html = _get(page_url)
        if not html:
            continue
        text = strip_html(html)
        if DRY_RUN:
            print(f'  [dry-run] {page_url[:70]}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(page_url)
            continue
        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES ('DOE EERE',NULL,'doe_award_page',?,?,?,?,'doe_eere',?)
        """, (now_iso()[:10], page_url, text[:50000], len(text), now_iso()))
        existing_urls.add(page_url)
        inserted += 1
        time.sleep(SLEEP_SEC)
        print(f'  Inserted: {page_url[:70]}')
    db.commit()
    print(f'  Total inserted: {inserted}  Skipped (dup): {skipped_dup}')
    return inserted, 0, skipped_dup


# ── Source C: DOE project search for no-CIK unknowns ─────────────────────────
def collect_doe_project_pages(db: sqlite3.Connection,
                               existing_urls: set) -> tuple[int, int]:
    """Search DOE for unknown projects that have no CIK and no resolved claims."""
    print('\n── Source C: DOE search for no-CIK unknown projects ───────────')

    unknowns = db.execute("""
        SELECT up.project_id, up.developer_name, up.state
        FROM unified_projects up
        WHERE up.stage = 'unknown'
          AND NOT EXISTS (
              SELECT 1 FROM project_claims pc
              WHERE pc.project_id = up.project_id
                AND pc.resolution_method != 'unresolved'
          )
          AND NOT EXISTS (
              SELECT 1 FROM regulatory_evidence re
              WHERE re.company_name = up.developer_name
                AND re.company_cik IS NOT NULL
                AND re.company_cik != ''
          )
        ORDER BY up.developer_name
        LIMIT 50
    """).fetchall()

    print(f'  {len(unknowns)} no-CIK unknown projects to search')
    inserted = skipped_dup = 0

    for proj in unknowns:
        name  = proj['developer_name']
        query = name.replace(' ', '+')
        url   = f'https://www.energy.gov/search/site/{query}'

        if url in existing_urls:
            skipped_dup += 1
            continue

        html = _get(url)
        if not html:
            continue
        text = strip_html(html)
        if len(text) < 200:
            continue

        if DRY_RUN:
            print(f'  [dry-run] {name[:45]:<45}  ({len(text)} chars)')
            inserted += 1
            existing_urls.add(url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at)
            VALUES (?,NULL,'doe_search_result',?,?,?,?,'doe_search',?)
        """, (name, now_iso()[:10], url, text[:50000], len(text), now_iso()))
        existing_urls.add(url)
        inserted += 1
        time.sleep(SLEEP_SEC)

    db.commit()
    print(f'  Inserted: {inserted}  Skipped (dup): {skipped_dup}')
    return inserted, skipped_dup


def print_stats(db: sqlite3.Connection) -> None:
    print('\n' + '=' * 60)
    print('COLLECT DOE — CURRENT COVERAGE')
    print('=' * 60)

    print('\n  DOE rows by source_system:')
    for r in db.execute("""
        SELECT source_system, COUNT(*) n
        FROM regulatory_evidence
        WHERE source_system LIKE 'doe%'
        GROUP BY source_system ORDER BY n DESC
    """):
        print(f'    {r[0]:<30} {r[1]}')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system LIKE 'doe%'"
    ).fetchone()[0]
    print(f'\n  Total DOE documents: {total}')

    print('\n  Unknown projects with no docs at all (prime targets):')
    for r in db.execute("""
        SELECT up.developer_name, up.state
        FROM unified_projects up
        WHERE up.stage = 'unknown'
          AND NOT EXISTS (
              SELECT 1 FROM regulatory_evidence re
              WHERE re.company_name = up.developer_name
          )
        ORDER BY up.developer_name LIMIT 15
    """):
        print(f'    {r[0]:<45} state={r[1] or "?"}')


def run() -> None:
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found')
        sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    print(f'{"DRY RUN — " if DRY_RUN else ""}DOE evidence collection\n')

    existing_urls = {
        r[0] for r in db.execute(
            "SELECT document_url FROM regulatory_evidence WHERE document_url IS NOT NULL"
        )
    }

    totals: dict[str, int] = {'inserted': 0, 'skipped_univ': 0, 'skipped_dup': 0}

    ins, su, sd = collect_oced_projects(db, existing_urls)
    totals['inserted'] += ins
    totals['skipped_univ'] += su
    totals['skipped_dup'] += sd

    ins, su, sd = collect_eere_awards(db, existing_urls)
    totals['inserted'] += ins
    totals['skipped_dup'] += sd

    ins, sd = collect_doe_project_pages(db, existing_urls)
    totals['inserted'] += ins
    totals['skipped_dup'] += sd

    print(f'\n{"=" * 60}')
    print('DOE COLLECTION COMPLETE')
    print(f'{"=" * 60}')
    print(f'  Inserted:             {totals["inserted"]}')
    print(f'  Skipped (univ-only):  {totals["skipped_univ"]}')
    print(f'  Skipped (duplicate):  {totals["skipped_dup"]}')
    print(f'\nNext steps:')
    print(f'  python step1_preprocess.py')
    print(f'  python step3_read_pass.py')
    print(f'  python connect.py')
    print(f'  python bootstrap_projects.py --enrich')
    print(f'  python bootstrap_projects.py --enrich-stages')

    db.close()


if __name__ == '__main__':
    run()
