#!/usr/bin/env python3
"""
step6_articles.py — Ingest articles from blue_h2_intelligence.db through
the step 1-5 learning pipeline into core_database.db
==========================================================================
Articles are a second document source alongside regulatory_evidence.
They've never been through step 1–5, so claims about Yara, Shell, Wabash,
AM Green, KBR, Dow, and others are missing from the learning DB.

This script is the article-specific equivalent of steps 1 + 3 combined:
  Phase A — Preprocess: register article text in cleaned_documents
             (only articles with full_text ≥ 200 chars processed directly;
              snippet-only articles attempt a re-fetch of the original URL
              before falling back to snippet)
  Phase B — Read pass: call Groq on each qualifying article → claims

Source IDs: articles get source_ids starting at ARTICLE_SOURCE_ID_BASE (1000)
            so they never collide with regulatory_evidence (max id=411).
            Mapping stored in article_source_map table.

Then run step5_contradiction.py normally to incorporate new claims into
the contradiction log.

Usage:
    python step6_articles.py                   # full run (phases A + B)
    python step6_articles.py --preprocess-only # phase A only
    python step6_articles.py --read-only       # phase B only (needs A done)
    python step6_articles.py --stats           # show current state
    python step6_articles.py --resume 1012     # resume read pass from source_id
"""

import json
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from groq import Groq

# ── Paths & constants ────────────────────────────────────────────────────────
_ROOT    = Path(__file__).resolve().parent.parent          # corpus_frequnecy/
CORE_DB  = _ROOT / 'data' / 'core_database.db'
INTEL_DB = _ROOT / 'blue_h2_intelligence.db'

GROQ_MODEL             = 'llama-3.3-70b-versatile'
GROQ_INPUT_COST_PER_M  = 0.59
GROQ_OUTPUT_COST_PER_M = 0.79
GROQ_SLEEP             = 0.5
GROQ_MAX_RETRIES       = 3

ARTICLE_SOURCE_ID_BASE = 1000
MIN_CONTENT_CHARS      = 200
REFETCH_MAX_CHARS      = 12_000

# ── Read-pass prompt (identical to step3) ───────────────────────────────────
SYSTEM_PROMPT = """\
You are a document analyst. Your job is to read a news article or regulatory
filing and extract every claim that carries intelligence about a company's
projects, commitments, financial position, or regulatory status.

You must NOT use any knowledge outside this document.
You name the claim type yourself from the content — do not use a predefined list.
Be specific. "project_milestone" is too vague. "construction_commenced" or
"offtake_agreement_signed" is correct.

For each claim:
- claim_type: your name for what kind of claim this is (snake_case)
- claim_text: the specific claim, in plain language, as a single sentence
- confidence: 0.0–1.0 based on the strength of language used
  - 0.9–1.0: definitive past-tense facts ("broke ground", "signed agreement")
  - 0.7–0.89: strong present-tense ("is under construction", "has committed")
  - 0.5–0.69: announced intentions ("plans to", "expects to", "is negotiating")
  - 0.3–0.49: speculative or forward-looking ("may", "could", "if completed")
  - 0.1–0.29: hedged or uncertain ("subject to", "no assurance can be given")
- commitment_level: factual | indicative | speculative
- time_sensitivity: current | historical | forward_looking

Return a JSON array. Every notable claim gets its own entry.
Nothing should be left out, including claims with frequency=1.
"""

USER_TEMPLATE = """\
Company: {company_name}
Document type: {document_type}
Document date: {document_date}
Source: {source}

{clean_text}

Extract all claims. Return JSON array only, no commentary."""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ── URL fetcher (same logic as step1) ───────────────────────────────────────
def fetch_url(url: str, max_chars: int = REFETCH_MAX_CHARS) -> str | None:
    if not url:
        return None
    try:
        headers = {
            'User-Agent': 'DecarbIQ-Research admin@decarbiq.com',
            'Accept': 'text/html, application/xhtml+xml, text/plain',
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        raw = resp.text

        if '<html' in raw.lower() or '<body' in raw.lower():
            raw = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<nav[^>]*>[\s\S]*?</nav>', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<aside[^>]*>[\s\S]*?</aside>', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<header[^>]*>[\s\S]*?</header>', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<footer[^>]*>[\s\S]*?</footer>', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'<[^>]+>', ' ', raw)
            raw = re.sub(r'&nbsp;', ' ', raw)
            raw = re.sub(r'&amp;', '&', raw)
            raw = re.sub(r'&lt;', '<', raw)
            raw = re.sub(r'&gt;', '>', raw)
            raw = re.sub(r'&#\d+;', '', raw)

        raw = re.sub(r'[ \t]+', ' ', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        raw = raw.strip()

        if len(raw) > max_chars:
            cut = raw[:max_chars]
            last_period = cut.rfind('.')
            raw = cut[:last_period + 1] if last_period > max_chars * 0.8 else cut

        return raw if len(raw) > 50 else None
    except Exception as e:
        print(f'    FETCH FAILED: {e}')
        return None


def map_document_type(source: str, title: str) -> str:
    """Map article source to a document_type label consistent with step3."""
    s = (source or '').lower()
    t = (title or '').lower()
    if 'sec edgar' in s or '8-k' in t or '10-k' in t:
        # Determine SEC form type from title
        for form in ('8-K', '10-K', '10-Q', 'S-4', 'EX-21'):
            if form.lower() in t:
                return form
        return '8-K'
    if 'ammonia energy' in s:
        return 'article_ammonia'
    if 'eia' in s:
        return 'article_eia'
    if 'decarbonfuse' in s:
        return 'article_news'
    if 'fuelcells' in s:
        return 'article_news'
    if 'hydrogen central' in s:
        return 'article_news'
    if 'doe news' in s:
        return 'article_doe'
    return 'article_news'


def parse_date(raw_date: str | None) -> str | None:
    """Normalize article published_date to YYYY-MM-DD."""
    if not raw_date:
        return None
    # Try common formats
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%a, %d %b %Y %H:%M:%S %Z',
                '%Y-%m-%d', '%Y-%m-%dT%H:%M:%S'):
        try:
            from datetime import datetime as dt
            return dt.strptime(raw_date.strip(), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    # Last resort: grab first 10 chars if looks like a date
    if raw_date[:4].isdigit():
        return raw_date[:10]
    return None


# ── LLM wrapper ──────────────────────────────────────────────────────────────
def call_groq(client, user_message: str) -> list[dict]:
    """Call Groq and return parsed claims list."""
    for attempt in range(1, GROQ_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {'role': 'system', 'content': SYSTEM_PROMPT},
                    {'role': 'user', 'content': user_message},
                ],
                temperature=0.1,
                max_tokens=4096,
                response_format={'type': 'json_object'},
            )
            raw = response.choices[0].message.content
            raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.DOTALL)
            raw = re.sub(r'\s*```$', '', raw, flags=re.DOTALL)
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                return next((v for v in parsed.values() if isinstance(v, list)), [])
            return []
        except Exception as e:
            if '429' in str(e) or 'rate' in str(e).lower():
                wait = 2 ** attempt
                print(f'    Rate limited, waiting {wait}s (attempt {attempt})')
                time.sleep(wait)
                if attempt == GROQ_MAX_RETRIES:
                    raise
            else:
                raise


# ── DB setup ─────────────────────────────────────────────────────────────────
def migrate_schema(db: sqlite3.Connection) -> None:
    """Ensure article_source_map table exists."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS article_source_map (
            source_id   INTEGER PRIMARY KEY,   -- 1000 + article.id
            article_id  INTEGER UNIQUE,
            title       TEXT,
            url         TEXT,
            source      TEXT,
            quality_flag TEXT,
            registered_at TEXT
        )
    """)
    db.commit()


# ── Phase A: Preprocess ──────────────────────────────────────────────────────
def preprocess_articles(db: sqlite3.Connection,
                        intel: sqlite3.Connection) -> list[dict]:
    """
    Register articles in cleaned_documents + article_source_map.
    Returns list of dicts for articles that qualified for read pass.
    """
    migrate_schema(db)
    now = now_iso()

    articles = intel.execute("""
        SELECT id, title, url, source, snippet, full_text,
               published_date, llm_developer, category, paywall_blocked
        FROM articles
        ORDER BY id
    """).fetchall()

    print(f'\n── Phase A: Preprocess ({len(articles)} articles) ──────────────')

    already_done = {
        r['article_id']
        for r in db.execute('SELECT article_id FROM article_source_map')
    }

    stats = {'FULLTEXT': 0, 'REFETCHED': 0, 'SNIPPET': 0, 'SHORT': 0, 'FAILED': 0}
    qualified = []

    for art in articles:
        art_id = art['id']
        if art_id in already_done:
            continue

        source_id = ARTICLE_SOURCE_ID_BASE + art_id
        title     = art['title'] or ''
        url       = art['url'] or ''
        source    = art['source'] or ''
        doc_type  = map_document_type(source, title)
        doc_date  = parse_date(art['published_date'])

        # Determine company_name from llm_developer or source
        company = art['llm_developer'] or ''
        if not company:
            # Try to infer from SEC EDGAR titles like "Air Products 8-K Filing"
            m = re.match(r'^(.+?)\s+(?:8-K|10-K|FORM)', title, re.IGNORECASE)
            if m:
                company = m.group(1).strip()
            else:
                company = source  # fallback

        # ── Determine clean_text ─────────────────────────────────────────────
        full_text = art['full_text'] or ''
        snippet   = art['snippet'] or ''
        quality_flag = None
        clean_text   = None

        if len(full_text) >= MIN_CONTENT_CHARS:
            clean_text   = full_text[:REFETCH_MAX_CHARS]
            quality_flag = 'FULLTEXT'
            stats['FULLTEXT'] += 1
            print(f'  [{art_id:3d}] FULLTEXT  {title[:55]:<55}  {len(clean_text)} chars')

        elif url and 'paywall' not in url.lower() and not art['paywall_blocked']:
            print(f'  [{art_id:3d}] Fetching  {title[:55]:<55}', end='', flush=True)
            fetched = fetch_url(url)
            if fetched and len(fetched) >= MIN_CONTENT_CHARS:
                clean_text   = fetched
                quality_flag = 'REFETCHED'
                stats['REFETCHED'] += 1
                print(f'  → {len(fetched)} chars')
            else:
                # Fall back to snippet
                if len(snippet) >= MIN_CONTENT_CHARS:
                    clean_text   = snippet
                    quality_flag = 'SNIPPET'
                    stats['SNIPPET'] += 1
                    print(f'  → snippet ({len(snippet)} chars)')
                else:
                    quality_flag = 'FAILED'
                    clean_text   = snippet or ''
                    stats['FAILED'] += 1
                    print(f'  → FAILED')
            time.sleep(0.3)

        elif len(snippet) >= MIN_CONTENT_CHARS:
            clean_text   = snippet
            quality_flag = 'SNIPPET'
            stats['SNIPPET'] += 1
            print(f'  [{art_id:3d}] SNIPPET   {title[:55]:<55}  {len(snippet)} chars')

        else:
            quality_flag = 'SHORT'
            clean_text   = snippet or full_text or ''
            stats['SHORT'] += 1
            print(f'  [{art_id:3d}] SHORT     {title[:55]:<55}  skipped')

        # ── Write to cleaned_documents ───────────────────────────────────────
        db.execute("""
            INSERT OR IGNORE INTO cleaned_documents
            (source_id, company_name, document_type, document_date,
             document_url, original_chars, clean_text, clean_chars,
             quality_flag, processed_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            source_id, company, doc_type, doc_date,
            url,
            len(clean_text),
            clean_text, len(clean_text),
            quality_flag, now
        ))

        # ── Write to article_source_map ──────────────────────────────────────
        db.execute("""
            INSERT OR IGNORE INTO article_source_map
            (source_id, article_id, title, url, source, quality_flag, registered_at)
            VALUES (?,?,?,?,?,?,?)
        """, (source_id, art_id, title, url, source, quality_flag, now))

        if quality_flag in ('FULLTEXT', 'REFETCHED', 'SNIPPET'):
            qualified.append({
                'source_id':   source_id,
                'company':     company,
                'doc_type':    doc_type,
                'doc_date':    doc_date,
                'url':         url,
                'clean_text':  clean_text,
                'quality_flag': quality_flag,
            })

    db.commit()

    print(f'\n  Phase A summary:')
    for flag, n in stats.items():
        if n:
            print(f'    {flag:<12} {n}')
    print(f'  Qualified for read pass: {len(qualified)}')
    return qualified


# ── Phase B: Read pass ───────────────────────────────────────────────────────
def read_pass(db: sqlite3.Connection,
              qualified: list[dict],
              resume_from: int | None = None) -> None:

    print(f'\n── Phase B: Read Pass ({len(qualified)} documents) ───────────')
    client = Groq()
    print('  LLM: Groq')
    now = now_iso()

    already_done = {
        r[0] for r in db.execute(
            "SELECT source_id FROM read_pass_log WHERE status='SUCCESS'"
        )
    }

    total_claims = 0
    processed = failed = skipped = 0

    for doc in qualified:
        sid = doc['source_id']

        if sid in already_done:
            skipped += 1
            continue
        if resume_from and sid < resume_from:
            skipped += 1
            continue

        clean_text = doc['clean_text'] or ''
        if len(clean_text.strip()) < 20:
            db.execute("""
                INSERT OR REPLACE INTO read_pass_log
                (source_id, company_name, document_type, status, claims_extracted,
                 prompt_tokens, completion_tokens, cost_usd, error_message, processed_at)
                VALUES (?,?,?,'SKIPPED',0,0,0,0,'Text too short',?)
            """, (sid, doc['company'], doc['doc_type'], now))
            db.commit()
            skipped += 1
            continue

        user_msg = USER_TEMPLATE.format(
            company_name = doc['company'],
            document_type = doc['doc_type'],
            document_date = doc['doc_date'] or 'unknown',
            source        = doc.get('url', ''),
            clean_text    = clean_text,
        )

        try:
            print(f'  [{sid}] {doc["company"][:30]:<30}  {doc["doc_type"]:<14}  '
                  f'{len(clean_text)} chars...', end='', flush=True)

            claims = call_groq(client, user_msg)

            for claim in claims:
                db.execute("""
                    INSERT INTO claims
                    (claim_id, source_id, company_name, document_type, document_date,
                     document_url, claim_type, claim_text, confidence, commitment_level,
                     time_sensitivity, extraction_model, extracted_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    str(uuid.uuid4()),
                    sid, doc['company'], doc['doc_type'], doc['doc_date'],
                    doc['url'],
                    claim.get('claim_type', 'unknown'),
                    claim.get('claim_text', ''),
                    claim.get('confidence', 0.5),
                    claim.get('commitment_level', 'indicative'),
                    claim.get('time_sensitivity', 'current'),
                    f'groq/{GROQ_MODEL}',
                    now,
                ))

                db.execute("""
                    INSERT INTO scheme_frequency
                        (document_type, claim_type, occurrence_count, first_seen, last_seen)
                    VALUES (?,?,1,?,?)
                    ON CONFLICT(document_type, claim_type) DO UPDATE SET
                        occurrence_count = occurrence_count + 1,
                        last_seen = excluded.last_seen
                """, (doc['doc_type'], claim.get('claim_type', 'unknown'),
                      doc['doc_date'], doc['doc_date']))

            db.execute("""
                INSERT OR REPLACE INTO read_pass_log
                (source_id, company_name, document_type, status, claims_extracted,
                 prompt_tokens, completion_tokens, cost_usd, error_message, processed_at)
                VALUES (?,?,?,'SUCCESS',?,0,0,0,NULL,?)
            """, (sid, doc['company'], doc['doc_type'], len(claims), now))

            db.commit()
            total_claims += len(claims)
            processed    += 1
            print(f'  {len(claims):2d} claims  [{provider}]')
            time.sleep(GROQ_SLEEP)

        except Exception as e:
            err = str(e)[:500]
            db.execute("""
                INSERT OR REPLACE INTO read_pass_log
                (source_id, company_name, document_type, status, claims_extracted,
                 prompt_tokens, completion_tokens, cost_usd, error_message, processed_at)
                VALUES (?,?,?,'FAILED',0,0,0,0,?,?)
            """, (sid, doc['company'], doc['doc_type'], err, now))
            db.commit()
            failed += 1
            print(f'  FAILED: {err[:80]}')

    print(f'\n  Phase B summary:')
    print(f'    Processed:    {processed}')
    print(f'    Skipped:      {skipped}')
    print(f'    Failed:       {failed}')
    print(f'    Total claims: {total_claims}')


def print_stats(db: sqlite3.Connection) -> None:
    print('\n=== STEP 6 — CURRENT STATE ===')
    try:
        total = db.execute('SELECT COUNT(*) FROM article_source_map').fetchone()[0]
        print(f'  Articles registered: {total}')
        for r in db.execute("""
            SELECT quality_flag, COUNT(*) n FROM article_source_map
            GROUP BY quality_flag ORDER BY n DESC
        """):
            print(f'    {r[0]:<12} {r[1]}')
    except:
        print('  article_source_map not yet created')

    try:
        done = db.execute("""
            SELECT status, COUNT(*) n FROM read_pass_log
            WHERE source_id >= ?
            GROUP BY status ORDER BY n DESC
        """, (ARTICLE_SOURCE_ID_BASE,)).fetchall()
        print(f'\n  Read pass results (source_id >= {ARTICLE_SOURCE_ID_BASE}):')
        for r in done:
            print(f'    {r[0]:<10} {r[1]}')
    except:
        pass

    try:
        new_claims = db.execute(
            "SELECT COUNT(*), COUNT(DISTINCT company_name) FROM claims WHERE source_id >= ?",
            (ARTICLE_SOURCE_ID_BASE,)
        ).fetchone()
        print(f'\n  Claims from articles: {new_claims[0]}  '
              f'distinct companies: {new_claims[1]}')
    except:
        pass


# ── Entry point ──────────────────────────────────────────────────────────────
def run(preprocess_only=False, read_only=False,
        stats_only=False, resume_from=None):

    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)
    if not INTEL_DB.exists():
        print(f'ERROR: {INTEL_DB} not found'); sys.exit(1)

    db    = sqlite3.connect(str(CORE_DB))
    intel = sqlite3.connect(str(INTEL_DB))
    db.row_factory = intel.row_factory = sqlite3.Row

    if stats_only:
        print_stats(db)
        db.close(); intel.close()
        return

    qualified = []

    if not read_only:
        qualified = preprocess_articles(db, intel)
    else:
        # Load already-preprocessed articles
        qualified = [
            {
                'source_id':  r['source_id'],
                'company':    r['company_name'],
                'doc_type':   r['document_type'],
                'doc_date':   r['document_date'],
                'url':        r['document_url'],
                'clean_text': r['clean_text'],
                'quality_flag': r['quality_flag'],
            }
            for r in db.execute("""
                SELECT cd.source_id, cd.company_name, cd.document_type,
                       cd.document_date, cd.document_url, cd.clean_text,
                       cd.quality_flag
                FROM cleaned_documents cd
                WHERE cd.source_id >= ?
                  AND cd.quality_flag IN ('FULLTEXT', 'REFETCHED', 'SNIPPET')
                ORDER BY cd.source_id
            """, (ARTICLE_SOURCE_ID_BASE,))
        ]
        print(f'Loaded {len(qualified)} pre-processed articles for read pass')

    if not preprocess_only:
        read_pass(db, qualified, resume_from=resume_from)

    print_stats(db)

    # Reminder to run step5
    print('\n  ⚠  Run step5_contradiction.py to incorporate new claims '
          'into contradiction detection.')

    db.close()
    intel.close()


if __name__ == '__main__':
    args = sys.argv[1:]
    run(
        preprocess_only = '--preprocess-only' in args,
        read_only       = '--read-only'        in args,
        stats_only      = '--stats'            in args,
        resume_from     = int(args[args.index('--resume') + 1])
                          if '--resume' in args else None,
    )
