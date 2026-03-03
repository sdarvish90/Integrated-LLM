#!/usr/bin/env python3
"""
DecarbIQ — collect_federal_register.py
Fetch Environmental Impact Statements and Environmental Assessments
from the Federal Register API for TX hydrogen/CCS projects.

API: https://www.federalregister.gov/api/v1/documents.json
JSON, paginated, no auth required.

Search queries scope to H2/CCS topics; client-side filter only requires
Texas mention. ~7-20 relevant results expected.

Writes to regulatory_evidence with source_system='federal_register'.
Company name uses lead agency (LLM pipeline extracts real proponents).

Usage:
    python collect_federal_register.py              # full run
    python collect_federal_register.py --dry-run    # show what would be inserted
    python collect_federal_register.py --stats      # current coverage
    python collect_federal_register.py --limit=10   # cap inserts
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
UA        = 'DecarbIQ-Research admin@decarbiq.com'
SLEEP_SEC = 1.0   # Be polite to Federal Register API
SOURCE_SYSTEM = 'federal_register'

DRY_RUN    = '--dry-run' in sys.argv
STATS_ONLY = '--stats'   in sys.argv

_LIMIT = 0
for _a in sys.argv:
    if _a.startswith('--limit='):
        _LIMIT = int(_a.split('=', 1)[1])
    elif _a == '--limit':
        _idx = sys.argv.index(_a)
        if _idx + 1 < len(sys.argv):
            _LIMIT = int(sys.argv[_idx + 1])

# ── API endpoint ─────────────────────────────────────────────────────────────
FR_API = 'https://www.federalregister.gov/api/v1/documents.json'

# Topic-scoped queries — these already constrain to H2/CCS/EIS
SEARCH_QUERIES = [
    '"environmental impact statement" Texas hydrogen',
    '"environmental impact statement" Texas "carbon capture"',
    '"environmental impact statement" Texas CO2 sequestration',
    '"environmental assessment" Texas hydrogen',
    '"environmental assessment" Texas "carbon capture"',
    'DOE hydrogen hub Texas',
    # Additional queries for known TX projects / EIS/EA
    'Class VI primacy Texas',
    'Rio Grande LNG Texas environmental',
    'Gulf Coast hydrogen hub',
    'clean hydrogen hub environmental impact',
    '"carbon capture" Texas DOE environmental',
    'notice of intent environmental impact statement Texas hydrogen',
    'notice of intent environmental impact statement Texas "carbon capture"',
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── Relevance filter ─────────────────────────────────────────────────────────

def _is_relevant(doc: dict) -> bool:
    """Only require Texas mention — queries already scope to H2/CCS topics."""
    text = ((doc.get('title') or '') + ' ' + (doc.get('abstract') or '')).lower()
    return ('texas' in text or ', tx' in text or '(tx)' in text
            or 'gulf coast' in text or 'houston' in text or 'baytown' in text)


# ── Technology mapping ───────────────────────────────────────────────────────

def _derive_technology(doc: dict) -> str | None:
    text = ((doc.get('title') or '') + ' ' + (doc.get('abstract') or '')).lower()
    if 'hydrogen' in text:
        return 'hydrogen_production'
    if any(kw in text for kw in ['carbon capture', 'co2', 'sequestration', 'ccs']):
        return 'co2_transport_storage'
    if 'ammonia' in text:
        return 'ammonia_production'
    return None


# ── Stage mapping ────────────────────────────────────────────────────────────

def _derive_stage(doc: dict) -> str | None:
    title = (doc.get('title') or '').lower()
    if 'record of decision' in title:
        return 'permitted'
    if 'final environmental impact' in title:
        return 'permitted'
    if 'draft environmental impact' in title:
        return 'announced'
    if 'notice of intent' in title:
        return 'announced'
    return None


# ── Company name extraction ──────────────────────────────────────────────────

def _extract_company(doc: dict) -> str:
    """Use lead agency — always available. LLM pipeline extracts real company names."""
    agencies = doc.get('agencies', [])
    if agencies and isinstance(agencies[0], dict):
        return agencies[0].get('name') or agencies[0].get('raw_name') or 'Unknown Agency'
    return 'Unknown Agency'


# ── Excerpt formatting ───────────────────────────────────────────────────────

def format_excerpt(doc: dict) -> str:
    """Format Federal Register document into structured text for LLM."""
    parts = [
        f"Federal Register: {doc.get('title', '')}",
        f"Document Number: {doc.get('document_number', '')}",
    ]

    doc_type = doc.get('type', '')
    pub_date = doc.get('publication_date', '')
    if doc_type or pub_date:
        parts.append(f"Type: {doc_type} | Published: {pub_date}")

    agencies = doc.get('agencies', [])
    if agencies:
        agency_names = []
        for a in agencies:
            if isinstance(a, dict):
                agency_names.append(a.get('name') or a.get('raw_name') or '')
            else:
                agency_names.append(str(a))
        parts.append(f"Agencies: {', '.join(n for n in agency_names if n)}")

    html_url = doc.get('html_url', '')
    if html_url:
        parts.append(f"URL: {html_url}")

    abstract = doc.get('abstract', '')
    if abstract:
        parts.append(f"\nAbstract:\n{abstract}")

    return '\n'.join(parts)


# ── Company name matching (reused from collect_tceq.py pattern) ──────────────

def _match_company(db: sqlite3.Connection, name: str) -> str | None:
    """Try exact → normalized → prefix match against companies table."""
    cn = (name or '').strip()
    if not cn:
        return None
    cn_upper = cn.upper()

    co = db.execute(
        "SELECT company_id FROM companies WHERE UPPER(company_name)=?",
        (cn_upper,)).fetchone()
    if co:
        return co['company_id']

    normalized = re.sub(
        r'\b(llc|l\.l\.c|lp|l\.p|inc|incorporated|corp|corporation|'
        r'ltd|limited|company|co|usa|u\.s\.a|u\.s)\b\.?',
        '', cn_upper, flags=re.I).strip().rstrip(',').strip()
    if normalized != cn_upper:
        co = db.execute(
            "SELECT company_id FROM companies WHERE UPPER(company_name)=?",
            (normalized,)).fetchone()
        if co:
            return co['company_id']

    words = normalized.split()
    for length in range(len(words) - 1, 1, -1):
        prefix = ' '.join(words[:length])
        co = db.execute(
            "SELECT company_id FROM companies WHERE UPPER(company_name) LIKE ?",
            (prefix + '%',)).fetchone()
        if co:
            return co['company_id']

    return None


# ── Direct enrichment ────────────────────────────────────────────────────────

def _direct_enrich(db: sqlite3.Connection, company_name: str,
                   stage: str | None, technology: str | None,
                   source_url: str, evidence_date: str | None) -> bool:
    """Match company → unified_projects, apply stage/technology."""
    from bootstrap_projects import apply_stage_update

    if not stage and not technology:
        return False

    company_id = _match_company(db, company_name)
    if not company_id:
        return False

    projects = db.execute(
        "SELECT project_id, stage, technology FROM unified_projects WHERE company_id=?",
        (company_id,)).fetchall()

    enriched = False
    now = now_iso()

    for proj in projects:
        pid = proj['project_id']

        if stage:
            updated = apply_stage_update(
                db, pid, stage, evidence_date,
                f'federal_register_enrichment: {source_url}',
                confidence=0.65)
            if updated:
                enriched = True

        if technology and not (proj['technology'] or '').strip():
            db.execute("""
                UPDATE unified_projects SET technology=?, updated_at=?
                WHERE project_id=? AND (technology IS NULL OR technology='')
            """, (technology, now, pid))
            enriched = True

    return enriched


# ── Collection ───────────────────────────────────────────────────────────────

def collect_documents(db: sqlite3.Connection, existing_urls: set) -> int:
    """Fetch Federal Register documents matching H2/CCS queries for Texas."""

    # Collect unique documents across all queries
    seen_doc_numbers: set[str] = set()
    all_docs: list[dict] = []

    for query in SEARCH_QUERIES:
        print(f'\n  Query: {query}')
        params = {
            'conditions[term]': query,
            'conditions[publication_date][gte]': '2020-01-01',
            'per_page': 100,
            'page': 1,
        }

        try:
            r = requests.get(FR_API, params=params,
                             headers={'User-Agent': UA}, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f'    ERROR: {e}')
            continue

        results = data.get('results', [])
        count = data.get('count', 0)
        print(f'    Results: {len(results)} (total: {count})')

        for doc in results:
            doc_num = doc.get('document_number', '')
            if not doc_num or doc_num in seen_doc_numbers:
                continue
            seen_doc_numbers.add(doc_num)

            if _is_relevant(doc):
                all_docs.append(doc)

        time.sleep(SLEEP_SEC)

    print(f'\n  Unique relevant documents: {len(all_docs)}')

    # Insert
    inserted = skipped = 0
    now = now_iso()

    for doc in all_docs:
        if _LIMIT and inserted >= _LIMIT:
            break

        doc_num = doc.get('document_number', '')
        doc_url = f'federal_register://FR-{doc_num}'

        if doc_url in existing_urls:
            skipped += 1
            continue

        company = _extract_company(doc)
        technology = _derive_technology(doc)
        stage = _derive_stage(doc)
        text = format_excerpt(doc)
        pub_date = doc.get('publication_date', now[:10])

        if DRY_RUN:
            title = (doc.get('title') or '')[:70]
            print(f'  [dry-run] {doc_num:<15} {pub_date} '
                  f'stage={stage or "-":<10} tech={technology or "-":<25} '
                  f'{title}')
            inserted += 1
            existing_urls.add(doc_url)
            continue

        db.execute("""
            INSERT INTO regulatory_evidence
            (company_name, company_cik, document_type, document_date,
             document_url, raw_text_excerpt, excerpt_char_count,
             source_system, ingested_at,
             derived_technology, derived_stage)
            VALUES (?,NULL,'federal_register_eis',?,?,?,?,?,?,?,?)
        """, (company, pub_date, doc_url,
              text[:50000], len(text), SOURCE_SYSTEM, now,
              technology, stage))

        existing_urls.add(doc_url)
        inserted += 1

        # Direct enrichment (unlikely to match — agency names won't hit companies)
        if stage or technology:
            _direct_enrich(db, company, stage, technology, doc_url, pub_date)

    if not DRY_RUN:
        db.commit()

    print(f'\n  Inserted: {inserted}  Skipped: {skipped}')
    return inserted


# ── Stats ────────────────────────────────────────────────────────────────────

def print_stats(db: sqlite3.Connection):
    """Show current Federal Register coverage."""
    print('\n=== FEDERAL REGISTER COVERAGE ===')

    total = db.execute(
        "SELECT COUNT(*) FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,)).fetchone()[0]
    print(f'  Total federal_register records: {total}')

    if total:
        with_stage = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence "
            "WHERE source_system=? AND derived_stage IS NOT NULL",
            (SOURCE_SYSTEM,)).fetchone()[0]
        with_tech = db.execute(
            "SELECT COUNT(*) FROM regulatory_evidence "
            "WHERE source_system=? AND derived_technology IS NOT NULL",
            (SOURCE_SYSTEM,)).fetchone()[0]
        print(f'  With derived_stage: {with_stage}')
        print(f'  With derived_technology: {with_tech}')

        by_company = db.execute("""
            SELECT company_name, COUNT(*) cnt FROM regulatory_evidence
            WHERE source_system=?
            GROUP BY company_name ORDER BY cnt DESC
        """, (SOURCE_SYSTEM,)).fetchall()
        print(f'\n  By agency/company:')
        for row in by_company:
            print(f'    {row["company_name"]:<45} {row["cnt"]:>3}')


# ── Entry point ──────────────────────────────────────────────────────────────

def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found')
        sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    # Load existing URLs for dedup
    existing = {r[0] for r in db.execute(
        "SELECT document_url FROM regulatory_evidence WHERE source_system=?",
        (SOURCE_SYSTEM,))}
    print(f'{"DRY RUN" if DRY_RUN else "COLLECTING"} — Federal Register EIS/EA')
    print(f'  Existing federal_register records: {len(existing)}')

    total = collect_documents(db, existing)

    print(f'\n  TOTAL: Inserted={total}')
    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
