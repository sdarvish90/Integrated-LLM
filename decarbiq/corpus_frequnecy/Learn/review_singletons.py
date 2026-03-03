#!/usr/bin/env python3
"""
review_singletons.py — Deep LLM review of all singleton claim types.

Reads each singleton claim + its full source document, sends to LLM for
contextual analysis. Classifies each as VALUABLE, CONTEXT_NEEDED,
WRONG_PROJECT, NEW_PROJECT, NOISE_REMOVE, or THIN_REMOVE.

Phase 1: Auto-mark claims with no/thin source doc as THIN_REMOVE (no LLM needed)
Phase 2: Batch-send remaining claims to Groq for deep review
Phase 3: Save results to singleton_review table for downstream action

Usage:
    python review_singletons.py              # full run
    python review_singletons.py --dry-run    # estimate only
    python review_singletons.py --apply      # apply verdicts (delete noise, reclassify)
    python review_singletons.py --stats      # show results
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import GROQ_API_KEY, GROQ_MODEL

from groq import Groq

DRY_RUN    = '--dry-run' in sys.argv
APPLY      = '--apply'   in sys.argv
STATS_ONLY = '--stats'   in sys.argv
BATCH_SIZE = 10
GROQ_SLEEP = 1.0  # seconds between batches
MAX_DOC_CHARS = 1500  # chars of source doc to send to LLM

PROMPT_TEMPLATE = """You are a senior energy industry analyst reviewing claims extracted from regulatory filings.
Your job is a DEEP CONTEXT REVIEW of each claim.

For each claim I provide: the extracted claim sentence, the FULL SOURCE DOCUMENT context,
the project it's linked to, and the project's current data gaps.

For each claim, return a JSON object with:

1. "verdict": One of:
   - "VALUABLE" — contains actionable project intelligence (location, technology, capacity, partnership, contractor, timeline, milestone)
   - "CONTEXT_NEEDED" — claim sentence is thin but source doc has useful context not yet extracted
   - "WRONG_PROJECT" — claim describes a DIFFERENT project/entity than what it's linked to
   - "NEW_PROJECT" — describes a project/entity NOT in our database that should be tracked separately
   - "NOISE_REMOVE" — corporate boilerplate, governance, legal mechanics, financial plumbing. No project intelligence.

2. "canonical_type": Best category: location | stage | capacity | technology | financing | partnership | timeline | corporate_governance | legal_financial | regulatory | noise

3. "extracted_value": If VALUABLE/CONTEXT_NEEDED, the specific data point (names, numbers, dates, locations). Otherwise null.

4. "entities_found": List of company/project/facility names mentioned that are NOT the linked project. Empty list if none.

5. "could_fill": List of project fields this could populate: city, technology, capacity_mtpa_h2, epc_contractor, fid_date, cod_date, construction_start, product, value_chain. Empty list if none.

6. "reasoning": 1-2 sentences on WHY.

Return ONLY a JSON array of objects. No markdown, no commentary.

CLAIMS:
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def create_review_table(db: sqlite3.Connection):
    db.execute("""
        CREATE TABLE IF NOT EXISTS singleton_review (
            claim_id TEXT PRIMARY KEY,
            claim_type TEXT,
            verdict TEXT,
            canonical_type TEXT,
            extracted_value TEXT,
            entities_found TEXT,
            could_fill TEXT,
            reasoning TEXT,
            reviewed_at TEXT
        )
    """)
    db.commit()


def load_singletons(db: sqlite3.Connection) -> list[dict]:
    """Load all singleton claims with their full context."""
    rows = db.execute('''
        SELECT c.claim_id, c.claim_type, c.claim_text, c.company_name,
               c.source_id, c.document_type, c.document_date, c.document_url,
               c.confidence,
               pc.project_id, pc.resolution_method,
               up.project_name, up.stage, up.city, up.technology,
               up.capacity_mtpa_h2, up.epc_contractor, up.fid_date,
               re.raw_text_excerpt, re.excerpt_char_count,
               cd.clean_text, cd.quality_flag, cd.clean_chars
        FROM claims c
        INNER JOIN (
            SELECT claim_type FROM claims GROUP BY claim_type HAVING COUNT(*) = 1
        ) s ON c.claim_type = s.claim_type
        JOIN project_claims pc ON c.claim_id = pc.claim_id
        LEFT JOIN unified_projects up ON pc.project_id = up.project_id
        LEFT JOIN regulatory_evidence re ON c.source_id = re.id
        LEFT JOIN cleaned_documents cd ON cd.source_id = c.source_id
        ORDER BY c.claim_id
    ''').fetchall()

    results = []
    for r in rows:
        raw = r['raw_text_excerpt'] or ''
        cleaned = r['clean_text'] or ''
        full_text = cleaned if len(cleaned) > len(raw) else raw

        results.append({
            'claim_id': r['claim_id'],
            'claim_type': r['claim_type'],
            'claim_text': r['claim_text'] or '',
            'company': r['company_name'] or '',
            'source_id': r['source_id'],
            'doc_type': r['document_type'] or '',
            'doc_date': r['document_date'] or '',
            'doc_url': r['document_url'] or '',
            'project_id': r['project_id'],
            'project': r['project_name'] or '(unresolved)',
            'stage': r['stage'] or '?',
            'resolution': r['resolution_method'] or '',
            'quality_flag': r['quality_flag'] or '',
            'full_text': full_text,
            'full_text_len': len(full_text),
            'gaps': {
                'city': r['city'],
                'technology': (r['technology'] or '')[:80] if r['technology'] else None,
                'capacity': str(r['capacity_mtpa_h2']) if r['capacity_mtpa_h2'] else None,
                'epc_contractor': r['epc_contractor'],
                'fid_date': r['fid_date'],
            }
        })
    return results


def phase1_thin_remove(singletons: list[dict]) -> tuple[list[dict], list[dict]]:
    """Auto-classify claims with no/thin source documents as THIN_REMOVE."""
    thin = []
    remaining = []
    for s in singletons:
        if s['full_text_len'] < 50:
            s['verdict'] = 'THIN_REMOVE'
            s['canonical_type'] = 'noise'
            s['extracted_value'] = None
            s['entities_found'] = []
            s['could_fill'] = []
            s['reasoning'] = f"Source document empty or too short ({s['full_text_len']} chars)."
            thin.append(s)
        else:
            remaining.append(s)
    return thin, remaining


def phase2_llm_review(singletons: list[dict], client: Groq) -> list[dict]:
    """Send claims in batches to Groq for deep context review."""
    reviewed = []
    total_batches = (len(singletons) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * BATCH_SIZE
        batch = singletons[start:start + BATCH_SIZE]

        claims_text = ""
        for s in batch:
            doc_preview = s['full_text'][:MAX_DOC_CHARS] if s['full_text'] else "(EMPTY)"
            claims_text += f"""
---
CLAIM: {s['claim_type']}
  sentence: "{s['claim_text'][:300]}"
  linked_project: {s['project']} (stage={s['stage']}, resolution={s['resolution']})
  source: {s['doc_type']} dated {s['doc_date']}
  project_gaps: {json.dumps(s['gaps'])}

  SOURCE DOCUMENT ({s['full_text_len']} chars, first {MAX_DOC_CHARS}):
  {doc_preview}
---
"""

        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": PROMPT_TEMPLATE + claims_text}],
                    max_tokens=5000,
                    temperature=0.1,
                )
                raw = response.choices[0].message.content
                # Extract JSON
                js_start = raw.find('[')
                js_end = raw.rfind(']') + 1
                if js_start < 0 or js_end <= js_start:
                    raise ValueError("No JSON array in response")
                results = json.loads(raw[js_start:js_end])

                if len(results) != len(batch):
                    print(f"  [warn] Batch {batch_idx+1}: got {len(results)} results for {len(batch)} claims")
                    # Pad or trim
                    while len(results) < len(batch):
                        results.append({
                            'verdict': 'NOISE_REMOVE',
                            'canonical_type': 'noise',
                            'extracted_value': None,
                            'entities_found': [],
                            'could_fill': [],
                            'reasoning': 'LLM returned fewer results than expected.'
                        })
                    results = results[:len(batch)]

                for s, r in zip(batch, results):
                    s['verdict'] = r.get('verdict', 'NOISE_REMOVE')
                    s['canonical_type'] = r.get('canonical_type', 'noise')
                    s['extracted_value'] = r.get('extracted_value')
                    s['entities_found'] = r.get('entities_found', [])
                    s['could_fill'] = r.get('could_fill', [])
                    s['reasoning'] = r.get('reasoning', '')
                    reviewed.append(s)

                break  # success
            except Exception as e:
                print(f"  [error] Batch {batch_idx+1}, attempt {attempt+1}: {e}")
                if attempt < 2:
                    time.sleep(3 * (attempt + 1))
                else:
                    # Mark entire batch as error
                    for s in batch:
                        s['verdict'] = 'NOISE_REMOVE'
                        s['canonical_type'] = 'noise'
                        s['extracted_value'] = None
                        s['entities_found'] = []
                        s['could_fill'] = []
                        s['reasoning'] = f'LLM review failed: {e}'
                        reviewed.append(s)

        if (batch_idx + 1) % 10 == 0 or batch_idx == total_batches - 1:
            print(f"  Batch {batch_idx+1}/{total_batches} done "
                  f"({len(reviewed)}/{len(singletons)} claims reviewed)")

        time.sleep(GROQ_SLEEP)

    return reviewed


def save_results(db: sqlite3.Connection, all_reviewed: list[dict]):
    """Save review results to singleton_review table."""
    now = now_iso()
    for s in all_reviewed:
        db.execute("""
            INSERT OR REPLACE INTO singleton_review
            (claim_id, claim_type, verdict, canonical_type, extracted_value,
             entities_found, could_fill, reasoning, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s['claim_id'], s['claim_type'], s['verdict'], s['canonical_type'],
            json.dumps(s['extracted_value']) if s['extracted_value'] else None,
            json.dumps(s['entities_found']),
            json.dumps(s['could_fill']),
            s['reasoning'], now
        ))
    db.commit()
    print(f"  Saved {len(all_reviewed)} results to singleton_review table")


def print_stats(db: sqlite3.Connection):
    """Print review statistics."""
    total = db.execute("SELECT COUNT(*) FROM singleton_review").fetchone()[0]
    if total == 0:
        print("No review results yet. Run without --stats first.")
        return

    print(f"\n{'='*70}")
    print(f"SINGLETON REVIEW RESULTS")
    print(f"{'='*70}")
    print(f"Total reviewed: {total}")

    print(f"\nBy verdict:")
    rows = db.execute("""
        SELECT verdict, COUNT(*) as cnt
        FROM singleton_review
        GROUP BY verdict
        ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        pct = 100 * r[1] / total
        print(f"  {r[0]:20s} {r[1]:5d} ({pct:5.1f}%)")

    print(f"\nBy canonical_type:")
    rows = db.execute("""
        SELECT canonical_type, COUNT(*) as cnt
        FROM singleton_review
        GROUP BY canonical_type
        ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        pct = 100 * r[1] / total
        print(f"  {r[0]:25s} {r[1]:5d} ({pct:5.1f}%)")

    # Removable
    removable = db.execute(
        "SELECT COUNT(*) FROM singleton_review WHERE verdict LIKE '%REMOVE%'"
    ).fetchone()[0]
    valuable = db.execute(
        "SELECT COUNT(*) FROM singleton_review WHERE verdict IN ('VALUABLE','CONTEXT_NEEDED','NEW_PROJECT')"
    ).fetchone()[0]
    wrong = db.execute(
        "SELECT COUNT(*) FROM singleton_review WHERE verdict = 'WRONG_PROJECT'"
    ).fetchone()[0]
    print(f"\nActionable summary:")
    print(f"  Removable (NOISE+THIN):  {removable} ({100*removable/total:.1f}%)")
    print(f"  Valuable+Context+New:    {valuable} ({100*valuable/total:.1f}%)")
    print(f"  Wrong project:           {wrong} ({100*wrong/total:.1f}%)")

    # Could-fill breakdown
    print(f"\nCould-fill field distribution (VALUABLE/CONTEXT_NEEDED only):")
    rows = db.execute("""
        SELECT could_fill FROM singleton_review
        WHERE verdict IN ('VALUABLE', 'CONTEXT_NEEDED')
        AND could_fill IS NOT NULL AND could_fill != '[]'
    """).fetchall()
    field_counts = {}
    for r in rows:
        try:
            fields = json.loads(r[0])
            for f in fields:
                field_counts[f] = field_counts.get(f, 0) + 1
        except:
            pass
    for f, n in sorted(field_counts.items(), key=lambda x: -x[1]):
        print(f"    {f:25s} {n:4d}")

    # Entities found
    print(f"\nNew entities discovered (top 20):")
    rows = db.execute("""
        SELECT entities_found FROM singleton_review
        WHERE verdict IN ('VALUABLE', 'CONTEXT_NEEDED', 'NEW_PROJECT', 'WRONG_PROJECT')
        AND entities_found IS NOT NULL AND entities_found != '[]'
    """).fetchall()
    entity_counts = {}
    for r in rows:
        try:
            entities = json.loads(r[0])
            for e in entities:
                if isinstance(e, str) and len(e) > 2:
                    entity_counts[e] = entity_counts.get(e, 0) + 1
        except:
            pass
    for e, n in sorted(entity_counts.items(), key=lambda x: -x[1])[:20]:
        print(f"    {e:45s} {n:3d}")


def apply_verdicts(db: sqlite3.Connection):
    """Apply review verdicts: delete NOISE_REMOVE and THIN_REMOVE claims."""
    to_delete = db.execute("""
        SELECT sr.claim_id, sr.verdict
        FROM singleton_review sr
        WHERE sr.verdict IN ('NOISE_REMOVE', 'THIN_REMOVE')
    """).fetchall()

    if not to_delete:
        print("Nothing to apply.")
        return

    claim_ids = [r[0] for r in to_delete]
    print(f"Deleting {len(claim_ids)} noise/thin claims...")

    # Batch delete in chunks
    chunk = 500
    for i in range(0, len(claim_ids), chunk):
        ids = claim_ids[i:i+chunk]
        placeholders = ','.join(['?'] * len(ids))
        db.execute(f"DELETE FROM project_claims WHERE claim_id IN ({placeholders})", ids)
        db.execute(f"DELETE FROM claims WHERE claim_id IN ({placeholders})", ids)

    db.commit()

    remaining = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    print(f"  Deleted {len(claim_ids)} claims")
    print(f"  Remaining claims: {remaining}")


def run():
    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row
    create_review_table(db)

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    if APPLY:
        apply_verdicts(db)
        db.close()
        return

    # Check if already reviewed
    already = db.execute("SELECT COUNT(*) FROM singleton_review").fetchone()[0]
    if already > 0:
        print(f"Already have {already} reviewed singletons. "
              f"Drop singleton_review table to re-run, or use --stats / --apply.")
        print_stats(db)
        db.close()
        return

    print(f"{'DRY RUN' if DRY_RUN else 'FULL REVIEW'}")
    print(f"Loading singletons...")

    singletons = load_singletons(db)
    print(f"  {len(singletons)} singleton claims loaded")

    # Phase 1: auto-classify thin
    print(f"\n── Phase 1: Auto-classify thin/empty source docs ──")
    thin, remaining = phase1_thin_remove(singletons)
    print(f"  THIN_REMOVE (no source doc): {len(thin)}")
    print(f"  Remaining for LLM review:    {len(remaining)}")

    if DRY_RUN:
        batches = (len(remaining) + BATCH_SIZE - 1) // BATCH_SIZE
        est_tokens = batches * 7000  # rough estimate
        print(f"\n  Would process {len(remaining)} claims in {batches} batches")
        print(f"  Estimated tokens: ~{est_tokens:,} input")
        print(f"  Estimated cost:   ~${est_tokens * 0.59/1e6 + batches * 1000 * 0.79/1e6:.2f}")
        print(f"  Estimated time:   ~{batches * 1.5 / 60:.1f} minutes")
        db.close()
        return

    # Phase 2: LLM review
    print(f"\n── Phase 2: LLM deep review ({len(remaining)} claims) ──")
    client = Groq(api_key=GROQ_API_KEY)
    reviewed = phase2_llm_review(remaining, client)

    # Combine
    all_reviewed = thin + reviewed
    print(f"\n── Phase 3: Save results ──")
    save_results(db, all_reviewed)

    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
