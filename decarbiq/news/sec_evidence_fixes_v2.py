"""
SEC Evidence Quality Module for DecarbIQ
==========================================
Selects the highest-signal evidence from regulatory_evidence for each project
and extracts relevant text paragraphs for the FID scoring LLM.

DESIGN PRINCIPLES
-----------------
1. No company names, project names, or IDs hardcoded — all logic is data-driven.
2. SEC structural knowledge (8-K item types, exhibit patterns) is hardcoded
   because it reflects stable SEC filing conventions, not current data.
3. Domain vocabulary (hydrogen, ammonia, carbon capture) is hardcoded as the
   domain focus is fixed. Project-specific names come from the database.
4. All thresholds are named constants at the top of the file.
5. The company_normalizer module handles all cross-table name matching.

Usage:
    from sec_evidence_fixes_v2 import build_evidence_summary
    summary, ids = build_evidence_summary(conn, company_key, index)
"""

import re
import sqlite3
from typing import Optional

from company_normalizer import (
    build_company_index,
    find_evidence_for_company,
    normalize,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS — SEC structural knowledge (stable, not data-specific)
# ─────────────────────────────────────────────────────────────────────────────

# Document types ranked by signal quality for hydrogen/ammonia FID assessment.
# Based on SEC filing conventions — this ranking is universally valid.
EVIDENCE_PRIORITY = {
    'EX-99.1':   10,   # Press releases: FID, capex, offtake announcements
    'EX-99.2':    8,   # Investor presentations: project slides, guidance
    'EX-10.1':    8,   # Material contracts: offtake, JV, EPC agreements
    'EX-96.3':    7,   # Technical reports: reserve/resource estimates
    'S-4':        6,   # Merger filings: asset descriptions
    '10-K':       5,   # Annual report: Business section, capex
    '10-K/A':     5,
    '10-Q':       4,   # Quarterly: project updates, capex
    'award':      4,   # DOE awards: technology, funding commitment
    'facility':   3,   # EPA facility: confirms existence, no stage/economics
    '8-K':        1,   # Cover form: rarely has content; exhibits do
    'EX-21.1':    0,   # Subsidiary list: zero signal
    'EX-23.1':    0,   # Auditor consent: zero signal
    'EX-31':      0,   # SOX CEO/CFO cert: zero signal
    'EX-32':      0,   # SOX cert: zero signal
}

# 8-K Item types whose content lives ONLY in an attached exhibit (not the form).
# These are permanent SEC form conventions — safe to hardcode.
EXHIBIT_ONLY_ITEMS = {
    'Item 2.02',   # Earnings — content in Exhibit 99.1
    'Item 7.01',   # Reg FD — content in Exhibit 99.1
    'Item 9.01',   # Financial statements index
    'Item 5.07',   # Shareholder vote results
    'Item 5.02',   # Director/officer appointment or departure
    'Item 2.05',   # Exit or disposal cost (restructuring)
    'Item 2.06',   # Material impairments
    'Item 4.02',   # Non-reliance on financial statements
}

# 8-K Item types that MAY contain direct project-relevant content.
DIRECT_CONTENT_ITEMS = {
    'Item 1.01',   # Entry into material agreement (offtake, EPC, JV)
    'Item 8.01',   # Other events (varies — check for project keywords)
    'Item 1.02',   # Termination of material agreement
    'Item 2.01',   # Completion of acquisition or disposal
    'Item 2.03',   # Creation of direct financial obligation
}

# Section headers to jump to when extracting 10-K / 10-Q / EX-99.1 text.
# These are permanent SEC filing structure conventions.
TARGET_SECTION_PATTERNS = [
    r'Recent\s+News',
    r'Business\s+Highlights?',
    r'(?:Key\s+)?Highlights?',
    r'Quarterly\s+Highlights?',
    r'Capital\s+Expenditure',
    r'Capital\s+Projects?',
    r'Project\s+(?:Development|Update|Status)',
    r'Our\s+Projects?',
    r'Item\s+1[\.\s]\s*Business',           # 10-K Business description
    r'Item\s+7[\.\s].*(?:Discussion|MD&A)',  # Management Discussion & Analysis
    r'Results?\s+of\s+Operations?',
    r'Operational\s+Update',
    r'Strategic\s+Update',
    r'Forward[- ]Looking',                   # Fallback — often precedes guidance
]

# Minimum chars for a paragraph to be considered (avoids headers, page numbers)
MIN_PARA_CHARS = 40

# Max evidence records per company (prevents token bloat)
MAX_EVIDENCE_RECORDS = 5

# Max chars to extract per evidence record
MAX_CHARS_PER_RECORD = 2000

# Minimum token overlap score to consider a paragraph project-relevant
MIN_KEYWORD_SCORE = 1


# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN VOCABULARY — fixed to the domain, not to current data
# Project-specific names are loaded from the database dynamically (see below).
# ─────────────────────────────────────────────────────────────────────────────

# Core domain terms — stable for blue hydrogen/ammonia intelligence
DOMAIN_TERMS = frozenset([
    # Technologies
    'hydrogen', 'ammonia', 'carbon capture', 'ccus', 'ccs', 'dac',
    'blue hydrogen', 'green hydrogen', 'low-carbon', 'low emission',
    'smr', 'atr', 'electrolysis', 'gasification', 'syngas', 'methane reforming',
    'blue ammonia', 'clean ammonia', 'low-carbon ammonia',
    'liquefied natural gas', 'lng',
    # Commercial milestones
    'final investment decision', 'fid',
    'offtake agreement', 'offtake',
    'epc contract', 'engineering procurement construction',
    'front-end engineering', 'feed study', 'feasibility study',
    'groundbreaking', 'first production', 'commissioning',
    'construction', 'under construction',
    # Financial signals
    'capital expenditure', 'capex',
    'project cost', 'billion investment', 'million investment',
    'funding secured', 'financial close', 'loan guarantee',
    # Units — presence of these with numbers = quantitative project data
    'mtpa', 'metric ton', 'million ton', 'tonne',
    'megawatt', 'gigawatt', 'mw', 'gw',
    'mmscfd',
    # Risk signals (negative)
    'cancelled', 'terminated', 'suspended', 'going concern',
    'impairment', 'writeoff', 'write-off', 'no longer probable',
    'workforce reduction', 'restructuring',
])


def load_project_names_from_db(conn: sqlite3.Connection) -> frozenset:
    """
    Load known project names and developer names from the database to augment
    keyword matching. Purely data-driven — no hardcoding.

    Returns a frozenset of lowercase name fragments (>4 chars) worth matching.
    """
    conn.row_factory = sqlite3.Row
    names = set()

    # From unified_projects
    rows = conn.execute("""
        SELECT project_name, developer_name, developer_key
        FROM unified_projects
        WHERE quarantined = 0
          AND project_name NOT LIKE '%Unnamed%'
          AND project_name NOT LIKE '%unknown%'
    """).fetchall()
    for r in rows:
        for field in [r['project_name'], r['developer_name'], r['developer_key']]:
            if field:
                # Extract meaningful tokens (skip short words)
                tokens = re.split(r'[\s_\-,]+', field.lower())
                names.update(t for t in tokens if len(t) > 4)

    # From articles with llm_project_name populated
    rows = conn.execute("""
        SELECT llm_project_name, llm_developer
        FROM articles
        WHERE llm_project_name IS NOT NULL
          AND LENGTH(llm_project_name) > 5
          AND llm_project_name NOT LIKE '%Unnamed%'
    """).fetchall()
    for r in rows:
        for field in [r['llm_project_name'], r['llm_developer']]:
            if field:
                tokens = re.split(r'[\s_\-,]+', field.lower())
                names.update(t for t in tokens if len(t) > 4)

    return frozenset(names)


def build_keyword_set(conn: sqlite3.Connection) -> frozenset:
    """
    Build the complete keyword set: domain terms + current project names from DB.
    Call this once per session or per run.
    """
    project_names = load_project_names_from_db(conn)
    return DOMAIN_TERMS | project_names


# ─────────────────────────────────────────────────────────────────────────────
# TEXT EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _keyword_score(text: str, keywords: frozenset) -> int:
    """Score a text chunk by keyword hits + financial signal patterns."""
    if not text:
        return 0
    text_lower = text.lower()
    score = sum(1 for kw in keywords if kw in text_lower)

    # Bonus for dollar figures or tonnage (quantitative project data)
    if re.search(r'\$\s*[\d,.]+\s*(?:million|billion|\bM\b|\bB\b)', text, re.I):
        score += 3
    if re.search(r'\d[\d,]*\s*(?:mtpa|kt\b|MW\b|GW\b|mmscfd|tonne)', text, re.I):
        score += 3

    # Bonus for action verbs (milestone language)
    if re.search(
        r'\b(?:announced?|signed?|awarded?|commenced?|completed?|approved?|'
        r'broke?\s+ground|reached?\s+fid|entered?\s+into|agreed?\s+to|'
        r'under\s+construction|on\s+schedule|on\s+budget|achieved?\s+fid)\b',
        text, re.I
    ):
        score += 2

    return score


def _extract_keyword_paragraphs(
    text: str,
    keywords: frozenset,
    max_chars: int = MAX_CHARS_PER_RECORD,
) -> str:
    """
    Split text into paragraphs, rank by keyword density, return top chunks.
    Restores reading order of selected paragraphs.
    """
    paragraphs = re.split(r'\n{2,}|\n(?=[A-Z\u2022\-])', text)

    scored = []
    for i, para in enumerate(paragraphs):
        para = para.strip()
        if len(para) < MIN_PARA_CHARS:
            continue
        score = _keyword_score(para, keywords)
        if score >= MIN_KEYWORD_SCORE:
            scored.append((score, i, para))

    if not scored:
        return ''

    # Take best paragraphs up to char limit, preserving reading order
    scored.sort(key=lambda x: -x[0])
    selected = []
    total = 0
    for score, idx, para in scored:
        if total + len(para) > max_chars:
            break
        selected.append((idx, para))
        total += len(para)

    selected.sort(key=lambda x: x[0])
    return '\n\n'.join(p for _, p in selected)


# Boilerplate patterns at the START of SEC filings (before real content)
_SEC_HEADER_RE = re.compile(
    r'^.*?(?='
    r'(?:Item\s+\d|BUSINESS\b|MANAGEMENT.{0,40}DISCUSSION|'
    r'RESULTS\s+OF\s+OPERATIONS?|Capital\s+Expenditure|Recent\s+News|'
    r'PRESS\s+RELEASE|FOR\s+IMMEDIATE\s+RELEASE|'
    r'About\s+\w[\w\s]{2,40}(?:Corporation|Inc|Energy|Holdings|Company|Ltd))'
    r')',
    re.DOTALL | re.IGNORECASE
)


def extract_signal_text(
    text: str,
    doc_type: str,
    keywords: frozenset,
    max_chars: int = MAX_CHARS_PER_RECORD,
) -> str:
    """
    Extract the most signal-rich text from a regulatory filing excerpt.

    Handles each document type according to its SEC structural conventions:
      EX-99.1/99.2  → Find "Recent News"/"Highlights", then keyword paragraphs
      10-K/10-Q     → Jump to Business/MD&A/Capex section, then keyword paragraphs
      8-K cover     → Extract project paragraphs; if none, return diagnostic note
      facility/award → Return as-is (already compact structured records)
    """
    if not text or len(text) < 50:
        return text or ''

    doc_type = (doc_type or '').upper()

    # ── Compact structured records: return as-is ─────────────────────────────
    if doc_type in ('FACILITY', 'AWARD', 'PERMIT', 'INSPECTION'):
        return text[:max_chars]

    # ── Strip universal SEC cover page boilerplate ────────────────────────────
    stripped = _SEC_HEADER_RE.sub('', text, count=1).strip()
    if len(stripped) < 100:
        stripped = text  # Stripping removed too much — use original

    # ── Press releases (EX-99.x) ─────────────────────────────────────────────
    if re.match(r'EX-99', doc_type):
        for pattern in TARGET_SECTION_PATTERNS:
            m = re.search(pattern, stripped, re.IGNORECASE)
            if m:
                chunk = stripped[m.start():m.start() + max_chars]
                if _keyword_score(chunk, keywords) >= MIN_KEYWORD_SCORE:
                    return chunk.strip()
        return _extract_keyword_paragraphs(stripped, keywords, max_chars) or stripped[:max_chars]

    # ── Annual and quarterly reports ──────────────────────────────────────────
    if re.match(r'10-[KQ]', doc_type) or doc_type in ('S-4', 'S-4/A', '20-F'):
        for pattern in TARGET_SECTION_PATTERNS:
            m = re.search(pattern, stripped, re.IGNORECASE)
            if m:
                chunk = stripped[m.start():m.start() + max_chars]
                if _keyword_score(chunk, keywords) >= MIN_KEYWORD_SCORE:
                    return chunk.strip()
        return _extract_keyword_paragraphs(stripped, keywords, max_chars) or stripped[:max_chars]

    # ── Material agreements and contracts ─────────────────────────────────────
    if re.match(r'EX-10', doc_type):
        # Contract exhibits: return keyword paragraphs — they're already content
        result = _extract_keyword_paragraphs(stripped, keywords, max_chars)
        return result or stripped[:max_chars]

    # ── 8-K cover form ────────────────────────────────────────────────────────
    if doc_type == '8-K':
        result = _extract_keyword_paragraphs(stripped, keywords, max_chars)
        if result:
            return result
        # Diagnostic note — tell LLM what item types were found
        items_found = re.findall(r'Item\s+\d+\.\d+[^\n]{0,60}', text)
        items_str = '; '.join(items_found[:4]) if items_found else 'none detected'
        return f"[8-K cover only — no project content. Items: {items_str}. Exhibit 99.1 required.]"

    # ── Default: keyword paragraphs ───────────────────────────────────────────
    return _extract_keyword_paragraphs(stripped, keywords, max_chars) or stripped[:max_chars]


def score_8k_usefulness(text: str) -> int:
    """
    Returns 0 if 8-K cover is exhibit-only (skip it), 1 if has direct content.
    Based on which Item type the 8-K reports under.
    """
    if not text:
        return 0
    exhibit_count = sum(1 for item in EXHIBIT_ONLY_ITEMS if item in text)
    direct_count  = sum(1 for item in DIRECT_CONTENT_ITEMS if item in text)
    if direct_count > 0 and exhibit_count == 0:
        return 1
    if 'Item 8.01' in text:
        return 0  # Item 8.01 is catch-all — check content quality via keywords
    return 0 if exhibit_count > 0 else 1


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE SELECTOR
# ─────────────────────────────────────────────────────────────────────────────

def select_best_evidence(
    evidence_records: list,
    keywords: frozenset,
    max_records: int = MAX_EVIDENCE_RECORDS,
) -> list:
    """
    Rank and filter a list of regulatory_evidence dicts by signal quality.

    Args:
        evidence_records: list of dicts from regulatory_evidence table
        keywords: keyword set from build_keyword_set()
        max_records: max records to return

    Returns:
        Filtered, ranked list of evidence dicts.
    """
    scored = []

    for record in evidence_records:
        doc_type = (record.get('document_type') or 'unknown').upper()
        base_priority = EVIDENCE_PRIORITY.get(doc_type, 2)

        # Skip zero-signal types entirely
        if base_priority == 0:
            continue

        # Skip 8-K covers that are exhibit-only
        if doc_type == '8-K':
            if score_8k_usefulness(record.get('raw_text_excerpt') or '') == 0:
                # Still allow if it has direct project keywords
                kw_score = _keyword_score(record.get('raw_text_excerpt') or '', keywords)
                if kw_score < 2:
                    continue

        # Keyword score from the actual text
        text = record.get('raw_text_excerpt') or ''
        kw_score = _keyword_score(text, keywords)
        final_score = base_priority + min(kw_score, 5)

        scored.append((final_score, record))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Deduplicate: allow max 2 EX-99.1 (Q3 and Q4 earnings releases),
    # max 1 of each other type
    result = []
    type_counts = {}
    for score, record in scored:
        doc_type = (record.get('document_type') or 'unknown').upper()
        max_per_type = 2 if doc_type == 'EX-99.1' else 1
        if type_counts.get(doc_type, 0) < max_per_type:
            result.append(record)
            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1
        if len(result) >= max_records:
            break

    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def build_evidence_summary(
    conn: sqlite3.Connection,
    company_key: str,
    index: Optional[dict] = None,
    keywords: Optional[frozenset] = None,
    scope: str = 'specific',
    max_records: int = MAX_EVIDENCE_RECORDS,
    max_chars_per_record: int = MAX_CHARS_PER_RECORD,
) -> tuple:
    """
    Build a clean, signal-rich evidence summary for the FID scoring LLM.

    Args:
        conn:          SQLite connection
        company_key:   developer_key from unified_projects OR company_name
        index:         Company index from build_company_index(). Built lazily if None.
        keywords:      Keyword set from build_keyword_set(). Built lazily if None.
        scope:         'specific' = only exact company match;
                       'broad' = fall back to parent company if no specific evidence
        max_records:   Max evidence records to include
        max_chars_per_record: Max chars of text per record

    Returns:
        (summary_text: str, evidence_ids: list[int])
    """
    conn.row_factory = sqlite3.Row

    # Build index/keywords lazily (callers should pass these in for performance)
    if index is None:
        index = build_company_index(conn)
    if keywords is None:
        keywords = build_keyword_set(conn)

    # Fetch evidence records for this company
    all_records = find_evidence_for_company(conn, company_key, index, scope=scope)

    if not all_records:
        return (f"No evidence found for '{company_key}'.", [])

    # Select the best records
    best_records = select_best_evidence(all_records, keywords, max_records)

    if not best_records:
        return (f"No useful evidence found for '{company_key}' (all records zero-signal).", [])

    # Build summary text
    sections = []
    evidence_ids = []

    for i, record in enumerate(best_records, 1):
        doc_type = record.get('document_type', 'unknown')
        raw_text = record.get('raw_text_excerpt') or ''

        clean_text = extract_signal_text(raw_text, doc_type, keywords, max_chars_per_record)

        section = (
            f"--- Evidence #{i} ---\n"
            f"Source: {record.get('source', 'unknown')} ({doc_type})\n"
            f"Company: {record.get('company_name', 'unknown')}\n"
            f"Date: {record.get('document_date', 'unknown')}\n"
            f"URL: {record.get('document_url', 'N/A')}\n"
            f"Text:\n{clean_text}\n"
            f"---"
        )
        sections.append(section)
        if record.get('id'):
            evidence_ids.append(record['id'])

    return ('\n\n'.join(sections), evidence_ids)


# ─────────────────────────────────────────────────────────────────────────────
# CLI self-test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys, json

    db_path = sys.argv[1] if len(sys.argv) > 1 else 'blue_h2_intelligence.db'
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print("Building index and keyword set...")
    idx = build_company_index(conn)
    kws = build_keyword_set(conn)
    print(f"  Index entries: {len(idx)} | Domain keywords: {len(DOMAIN_TERMS)} | DB keywords: {len(kws - DOMAIN_TERMS)}\n")

    # Test all companies in fid_assessments
    assessments = conn.execute("SELECT company_name, fid_probability FROM fid_assessments").fetchall()
    for a in assessments:
        print(f"{'='*60}")
        print(f"Company: {a['company_name']} (current fid={a['fid_probability']})")
        summary, ids = build_evidence_summary(conn, a['company_name'], idx, kws)
        print(f"  evidence_ids: {ids}")
        # Show first evidence source
        first_src = summary[summary.find('Source:'):summary.find('Source:')+60] if 'Source:' in summary else 'N/A'
        print(f"  First source: {first_src}")
        print(f"  Summary length: {len(summary)} chars")
        # Show first 300 chars
        print(f"  Preview: {summary[:300].replace(chr(10),' ')}")
        print()

    conn.close()
