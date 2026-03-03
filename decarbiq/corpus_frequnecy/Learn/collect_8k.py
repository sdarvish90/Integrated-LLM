#!/usr/bin/env python3
"""
DecarbIQ — collect_8k.py
Fetch 8-K filings from SEC EDGAR for companies in unified_projects.

For each company in unified_projects that has a known CIK (from regulatory_evidence),
fetches 8-K filings from 2020-present and inserts new rows into regulatory_evidence
in core_database.db.

Usage:
    python collect_8k.py              # full run — all unknown projects with CIKs
    python collect_8k.py --all        # all projects (not just unknown)
    python collect_8k.py --dry-run    # show what would be fetched, no writes
    python collect_8k.py --stats      # show current coverage
"""
from __future__ import annotations

import json
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
DATE_FROM    = '2020-01-01'
EDGAR_UA     = 'DecarbIQ-Research admin@decarbiq.com'   # required by SEC
SLEEP_SEC    = 0.15     # SEC allows ~10 req/sec; 0.15s is polite
MAX_PER_CIK  = 50       # cap per company — prevents one filer dominating
DRY_RUN      = '--dry-run' in sys.argv
ALL_PROJECTS = '--all'  in sys.argv
STATS_ONLY   = '--stats' in sys.argv

# 8-K exhibit types to fetch text for
# Items 1.01–9.01 cover project announcements, FID, construction, press releases
TARGET_FORMS = {'8-K', '8-K/A'}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(url: str, as_json: bool = False, retries: int = 3):
    """GET with rate-limit retry and SEC User-Agent."""
    headers = {'User-Agent': EDGAR_UA, 'Accept-Encoding': 'gzip, deflate'}
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f'  [rate limit] sleeping {wait}s')
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json() if as_json else resp.text
        except requests.RequestException as e:
            if attempt == retries - 1:
                print(f'  [fetch error] {url}: {e}')
                return None
            time.sleep(1)
    return None


def fetch_filings_for_cik(cik: str) -> list[dict]:
    """
    Return list of 8-K filing metadata for a CIK from 2020-present.
    Each dict: {accession, date, form, primary_document, filing_url}
    """
    cik_padded = str(cik).lstrip('0').zfill(10)
    url = f'https://data.sec.gov/submissions/CIK{cik_padded}.json'
    data = _get(url, as_json=True)
    if not data:
        return []

    filings = []
    recent = data.get('filings', {}).get('recent', {})

    forms       = recent.get('form', [])
    dates       = recent.get('filingDate', [])
    accessions  = recent.get('accessionNumber', [])
    primary_docs= recent.get('primaryDocument', [])

    for form, date, accn, pdoc in zip(forms, dates, accessions, primary_docs):
        if form not in TARGET_FORMS:
            continue
        if date < DATE_FROM:
            continue
        acc_clean = accn.replace('-', '')
        filing_url = (
            f'https://www.sec.gov/Archives/edgar/data/'
            f'{cik_padded.lstrip("0")}/{acc_clean}/{pdoc}'
        )
        filings.append({
            'accession':   accn,
            'date':        date,
            'form':        form,
            'primary_doc': pdoc,
            'filing_url':  filing_url,
        })
        if len(filings) >= MAX_PER_CIK:
            break

    # Also check older filings if available
    if len(filings) < MAX_PER_CIK:
        for file_ref in data.get('filings', {}).get('files', []):
            older_url = f"https://data.sec.gov/submissions/{file_ref['name']}"
            older = _get(older_url, as_json=True)
            if not older:
                continue
            for form, date, accn, pdoc in zip(
                older.get('form', []),
                older.get('filingDate', []),
                older.get('accessionNumber', []),
                older.get('primaryDocument', []),
            ):
                if form not in TARGET_FORMS or date < DATE_FROM:
                    continue
                acc_clean = accn.replace('-', '')
                filing_url = (
                    f'https://www.sec.gov/Archives/edgar/data/'
                    f'{cik_padded.lstrip("0")}/{acc_clean}/{pdoc}'
                )
                filings.append({
                    'accession': accn, 'date': date, 'form': form,
                    'primary_doc': pdoc, 'filing_url': filing_url,
                })
            if len(filings) >= MAX_PER_CIK:
                break

    return filings[:MAX_PER_CIK]


def fetch_text(url: str) -> str | None:
    """Fetch filing text, strip HTML tags."""
    raw = _get(url)
    if not raw:
        return None

    if '<html' in raw.lower() or '<body' in raw.lower():
        raw = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'<[^>]+>', ' ', raw)
        for ent, rep in [('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'),
                         ('&gt;', '>'), ('&#\d+;', '')]:
            raw = re.sub(ent, rep, raw)

    raw = re.sub(r'[ \t]+', ' ', raw)
    raw = re.sub(r'\n{3,}', '\n\n', raw).strip()
    return raw if len(raw) > 100 else None


def print_stats(db: sqlite3.Connection) -> None:
    print('\n' + '=' * 60)
    print('COLLECT 8-K — CURRENT COVERAGE')
    print('=' * 60)

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE document_type='8-K'"
    ).fetchone()[0]
    print(f'\n  8-K filings in regulatory_evidence: {total}')

    print('\n  By company (top 15):')
    for r in db.execute("""
        SELECT company_name, COUNT(*) n,
               MIN(document_date) earliest, MAX(document_date) latest
        FROM regulatory_evidence
        WHERE document_type = '8-K'
        GROUP BY company_name ORDER BY n DESC LIMIT 15
    """):
        print(f'    {r[0]:<45} n={r[1]:>3}  {r[2]} → {r[3]}')

    print('\n  Unknown projects with CIKs (most likely to benefit):')
    for r in db.execute("""
        SELECT up.developer_name,
               COUNT(DISTINCT re.id)              existing_docs,
               COUNT(DISTINCT CASE WHEN re.document_type='8-K'
                                   THEN re.id END) existing_8k,
               MIN(re.company_cik)                cik
        FROM unified_projects up
        LEFT JOIN regulatory_evidence re ON re.company_name = up.developer_name
        WHERE up.stage = 'unknown'
        GROUP BY up.project_id
        HAVING cik IS NOT NULL
        ORDER BY existing_docs DESC
        LIMIT 15
    """):
        print(f'    {r[0]:<45} existing={r[1]:>3}  8k={r[2]:>3}  CIK={r[3]}')


def run() -> None:
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    # ── Build target list: unknown projects with CIKs ─────────────────────────
    stage_filter = "" if ALL_PROJECTS else "WHERE up.stage = 'unknown'"

    targets = db.execute(f"""
        SELECT up.project_id, up.developer_name,
               MIN(re.company_cik) cik
        FROM unified_projects up
        JOIN regulatory_evidence re
          ON re.company_name = up.developer_name
         AND re.company_cik IS NOT NULL
         AND re.company_cik != ''
        {stage_filter}
        GROUP BY up.project_id
        ORDER BY up.developer_name
    """).fetchall()

    print(f'{"DRY RUN — " if DRY_RUN else ""}Targeting {len(targets)} companies for 8-K fetch')
    print(f'Date range: {DATE_FROM} → present\n')

    # Pre-load existing URLs to avoid duplicates
    existing_urls = {
        r[0] for r in db.execute(
            "SELECT document_url FROM regulatory_evidence WHERE document_url IS NOT NULL"
        )
    }

    inserted = skipped = failed = 0

    for i, target in enumerate(targets, 1):
        cik  = target['cik']
        name = target['developer_name']
        print(f'[{i}/{len(targets)}] {name} (CIK={cik})')

        filings = fetch_filings_for_cik(cik)
        if not filings:
            print(f'  No 8-K filings found (2020–present)')
            continue

        new_filings = [f for f in filings if f['filing_url'] not in existing_urls]
        print(f'  Found {len(filings)} filings, {len(new_filings)} new')

        if DRY_RUN:
            for f in new_filings[:3]:
                print(f"  [dry-run] {f['date']}  {f['form']}  {f['filing_url'][:70]}")
            inserted += len(new_filings)
            time.sleep(SLEEP_SEC)
            continue

        for filing in new_filings:
            text = fetch_text(filing['filing_url'])
            if not text:
                failed += 1
                continue

            db.execute("""
                INSERT INTO regulatory_evidence
                (company_name, company_cik, document_type, document_date,
                 document_url, raw_text_excerpt, excerpt_char_count,
                 source_system, ingested_at)
                VALUES (?,?,?,?,?,?,?,'edgar_8k',?)
            """, (
                name, cik, filing['form'], filing['date'],
                filing['filing_url'],
                text[:50000], min(len(text), 50000),
                now_iso(),
            ))
            existing_urls.add(filing['filing_url'])
            inserted += 1
            time.sleep(SLEEP_SEC)

        db.commit()
        print(f'  Inserted: {len(new_filings)}')
        time.sleep(SLEEP_SEC)

    print(f'\n{"=" * 60}')
    print(f'EDGAR 8-K COLLECTION COMPLETE')
    print(f'{"=" * 60}')
    print(f'  Inserted:  {inserted}')
    print(f'  Skipped:   {skipped}  (already in DB)')
    print(f'  Failed:    {failed}')
    print(f'\nNext step: run the full pipeline manually:')
    print(f'  python step1_preprocess.py')
    print(f'  python step3_read_pass.py')
    print(f'  python connect.py')
    print(f'  python bootstrap_projects.py --enrich')
    print(f'  python bootstrap_projects.py --enrich-stages')

    db.close()


if __name__ == '__main__':
    run()
