"""
Rescore Stale FID Assessments
==============================
Rebuilds evidence summaries using the generalized evidence selector,
then re-runs LLM scoring for all stale fid_assessments.

Propagates updated scores to unified_projects using normalized company
name matching — no hardcoded company names.

Usage:
    python rescore_stale_assessments_v2.py --db blue_h2_intelligence.db
    python rescore_stale_assessments_v2.py --db blue_h2_intelligence.db --dry-run
    python rescore_stale_assessments_v2.py --db blue_h2_intelligence.db --all

Requires:
    - company_normalizer.py in same directory
    - sec_evidence_fixes_v2.py in same directory
    - GROQ_API_KEY or ANTHROPIC_API_KEY environment variable
"""

import sqlite3
import json
import os
import sys
import time
import argparse
from datetime import datetime

# Import v2 modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from company_normalizer import build_company_index, normalize, is_match
from sec_evidence_fixes_v2 import build_evidence_summary, build_keyword_set


# ── LLM backend ────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY')

FID_SCORING_PROMPT = """You are a blue hydrogen and ammonia project analyst specializing in FID (Final Investment Decision) risk assessment.

Given the following regulatory and news evidence for a project, assess:
1. Project stage (one of: pre-announcement, announced, feed, construction, operational)
2. FID probability (0.0 to 1.0) — likelihood the project reaches or has reached FID
3. Confidence (LOW / MEDIUM / HIGH) based on evidence quality
4. Key evidence points (list of 3-5 specific facts from the evidence)
5. Reasoning (2-3 sentences explaining your assessment)

FID probability guidance:
- 0.90–1.00: Under construction or operational, clear FID made, strong capex commitment
- 0.60–0.89: FEED complete, offtake signed, permits in place, FID imminent
- 0.40–0.59: Advanced development, feasibility complete, funding largely secured
- 0.20–0.39: Announced with credible developer, some milestones, real but early
- 0.10–0.19: Mentioned in filings, limited specifics, plausible but uncertain
- 0.01–0.09: Only tangential mention, no project-specific details
- 0.00: Evidence explicitly shows project cancelled or developer insolvent

IMPORTANT: If the evidence shows DOE grant termination, cancelled offtake, or developer
financial distress (emergency shareholder votes, going concern warnings), reduce FID probability significantly.

Respond ONLY with a JSON object:
{{
  "project_name": "string",
  "stage": "string",
  "fid_probability": float,
  "confidence": "LOW|MEDIUM|HIGH",
  "key_evidence": ["point1", "point2", "point3"],
  "reasoning": "string"
}}

Company: {company_name}
Evidence:
{evidence_summary}
"""


def call_groq(evidence_summary: str, company_name: str) -> dict:
    """Call Groq API for FID scoring."""
    import requests

    prompt = FID_SCORING_PROMPT.format(
        evidence_summary=evidence_summary[:6000],
        company_name=company_name)

    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 800,
            "response_format": {"type": "json_object"}
        },
        timeout=30
    )
    response.raise_for_status()
    data = response.json()
    raw = data['choices'][0]['message']['content']
    tokens = data.get('usage', {}).get('total_tokens', 0)
    result = json.loads(raw)
    result['_tokens'] = tokens
    result['_raw'] = raw
    return result


def call_anthropic(evidence_summary: str, company_name: str) -> dict:
    """Call Anthropic API for FID scoring."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = FID_SCORING_PROMPT.format(
        evidence_summary=evidence_summary[:8000],
        company_name=company_name)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text
    # Strip JSON fences if present
    raw_clean = raw.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
    result = json.loads(raw_clean)
    result['_tokens'] = message.usage.input_tokens + message.usage.output_tokens
    result['_raw'] = raw
    return result


def score_with_llm(evidence_summary: str, company_name: str) -> dict:
    """Try Groq first, fall back to Anthropic."""
    if GROQ_API_KEY:
        try:
            return call_groq(evidence_summary, company_name)
        except Exception as e:
            print(f"  [WARN] Groq failed: {e} — trying Anthropic")
    if ANTHROPIC_API_KEY:
        try:
            return call_anthropic(evidence_summary, company_name)
        except Exception as e:
            print(f"  [ERROR] Anthropic failed: {e}")
    raise RuntimeError("No LLM backend available. Set GROQ_API_KEY or ANTHROPIC_API_KEY.")


def propagate_to_unified_projects(
    conn: sqlite3.Connection,
    company_name: str,
    new_fid: float,
    new_stage: str,
) -> int:
    """
    Propagate FID score to unified_projects using normalized company name matching.
    Returns the number of rows updated.
    """
    # Fetch all pre_fid, non-quarantined projects
    projects = conn.execute("""
        SELECT project_id, developer_key, developer_name
        FROM unified_projects
        WHERE fid_status = 'pre_fid'
        AND quarantined = 0
    """).fetchall()

    updated = 0
    for proj in projects:
        dev_key = proj['developer_key'] or ''
        dev_name = proj['developer_name'] or ''

        # Use normalized matching — no LIKE '%first_word%' hack
        if is_match(company_name, dev_key) or is_match(company_name, dev_name):
            conn.execute("""
                UPDATE unified_projects
                SET fid_probability = ?,
                    stage = CASE WHEN ? != 'unknown' THEN ? ELSE stage END,
                    updated_at = ?
                WHERE project_id = ?
            """, (
                new_fid,
                new_stage, new_stage,
                datetime.utcnow().isoformat(),
                proj['project_id']
            ))
            updated += 1

    return updated


def rescore_assessments(db_path: str, stale_only: bool = True, dry_run: bool = False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Build index and keywords once for the whole run
    print("Building company index and keyword set...")
    index = build_company_index(conn)
    keywords = build_keyword_set(conn)
    print(f"  Index entries: {len(index)} | Keywords: {len(keywords)}")

    # Fetch assessments to re-score
    where = "WHERE stale=1" if stale_only else ""
    assessments = conn.execute(f"SELECT * FROM fid_assessments {where} ORDER BY id").fetchall()

    print(f"\nAssessments to rescore: {len(assessments)}")
    if not assessments:
        print("Nothing to do.")
        return

    results = []
    for assessment in assessments:
        company = assessment['company_name']
        print(f"\n{'─'*60}")
        print(f"Company: {company} | current fid={assessment['fid_probability']} | "
              f"stale_reason={assessment['stale_reason']}")

        # Step 1: Rebuild evidence summary using v2 selector + normalizer
        print(f"  → Rebuilding evidence summary with v2 selector...")
        new_summary, new_ids = build_evidence_summary(
            conn, company, index=index, keywords=keywords, scope='broad'
        )

        if new_summary and len(new_summary) > 100 and 'No evidence found' not in new_summary:
            print(f"  → New summary: {len(new_summary)} chars | evidence_ids={new_ids}")
            first_src_idx = new_summary.find('Source:')
            if first_src_idx >= 0:
                print(f"  → First source: {new_summary[first_src_idx:first_src_idx+80]}")
        else:
            print(f"  → No new evidence found, using existing summary")
            new_summary = assessment['evidence_summary']
            new_ids = json.loads(assessment['evidence_ids'] or '[]')

        if not new_summary:
            print(f"  [SKIP] No evidence available")
            continue

        if dry_run:
            print(f"  [DRY RUN] Would call LLM and update assessment")
            print(f"  Evidence preview: {new_summary[:300].replace(chr(10), ' ')}")
            continue

        # Step 2: Call LLM
        print(f"  → Calling LLM...")
        try:
            time.sleep(0.5)  # Rate limit
            llm_result = score_with_llm(new_summary, company)
        except Exception as e:
            print(f"  [ERROR] LLM call failed: {e}")
            continue

        # Step 3: Update fid_assessments
        new_fid = float(llm_result.get('fid_probability', assessment['fid_probability']))
        new_stage = llm_result.get('stage', assessment['stage'])
        new_confidence = llm_result.get('confidence', 'LOW')
        new_evidence_points = json.dumps(llm_result.get('key_evidence', []))
        new_reasoning = llm_result.get('reasoning', '')
        tokens = llm_result.get('_tokens', 0)

        print(f"  → LLM result: fid={new_fid} | stage={new_stage} | confidence={new_confidence}")
        print(f"  → Reasoning: {new_reasoning[:150]}")
        delta = new_fid - float(assessment['fid_probability'])
        print(f"  → Score change: {assessment['fid_probability']} → {new_fid} (Δ{delta:+.2f})")

        conn.execute("""
            UPDATE fid_assessments SET
                evidence_summary = ?,
                evidence_ids = ?,
                stage = ?,
                fid_probability = ?,
                confidence = ?,
                key_evidence_points = ?,
                reasoning = ?,
                llm_raw_response = ?,
                llm_tokens_used = ?,
                assessed_date = ?,
                stale = 0,
                stale_reason = NULL,
                prompt_version = 2
            WHERE id = ?
        """, (
            new_summary,
            json.dumps(new_ids),
            new_stage,
            new_fid,
            new_confidence,
            new_evidence_points,
            new_reasoning,
            llm_result.get('_raw', ''),
            tokens,
            datetime.utcnow().isoformat(),
            assessment['id']
        ))

        # Step 4: Propagate to unified_projects using normalized matching
        updated_count = propagate_to_unified_projects(conn, company, new_fid, new_stage)
        print(f"  → Propagated to {updated_count} unified_projects row(s)")

        conn.commit()
        results.append({
            'company': company,
            'old_fid': float(assessment['fid_probability']),
            'new_fid': new_fid,
            'stage': new_stage,
            'confidence': new_confidence,
            'delta': delta,
            'propagated': updated_count
        })

    # Summary
    if results:
        print(f"\n{'='*60}")
        print(f"RESCORE COMPLETE — {len(results)} assessments updated\n")
        for r in sorted(results, key=lambda x: -abs(x['delta'])):
            arrow = "▲" if r['delta'] > 0 else ("▼" if r['delta'] < 0 else "=")
            print(f"  {arrow} {r['company']:<40} {r['old_fid']:.2f} → {r['new_fid']:.2f} "
                  f"| {r['stage']} | {r['confidence']} | propagated={r['propagated']}")
    elif not dry_run:
        print("\nNo assessments were updated.")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description='Rescore stale FID assessments with generalized evidence')
    parser.add_argument('--db', required=True)
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    parser.add_argument('--all', action='store_true', help='Rescore all, not just stale')
    args = parser.parse_args()

    if not args.dry_run and not GROQ_API_KEY and not ANTHROPIC_API_KEY:
        print("[ERROR] Set GROQ_API_KEY or ANTHROPIC_API_KEY environment variable")
        sys.exit(1)

    rescore_assessments(
        db_path=args.db,
        stale_only=not args.all,
        dry_run=args.dry_run
    )


if __name__ == '__main__':
    main()
