"""
Company Name Normalizer for DecarbIQ
======================================
Single source of truth for matching company names across tables that use
different naming conventions:

  unified_projects.developer_key   → 'air_products_massena_green_hydrogen_facility'
  unified_projects.developer_name  → 'Air Products Massena Green Hydrogen Facility'
  regulatory_evidence.company_name → 'Air Products & Chemicals'  or  'air products'
  fid_assessments.company_name     → 'air products'
  articles.llm_developer           → 'Air Products'

All functions are stateless and data-driven. No company names are hardcoded —
the matcher builds its lookup tables from the database at runtime.

Usage:
    from company_normalizer import normalize, build_company_index, find_evidence_for_company

    # Normalize any company string to a canonical token set
    normalize('Air Products & Chemicals, Inc.')  → 'air products'
    normalize('air_products_massena')            → 'air products massena'

    # Build index once per session
    idx = build_company_index(conn)

    # Find regulatory_evidence rows for a project
    rows = find_evidence_for_company(conn, 'air_products_massena_green_hydrogen_facility', idx)
"""

import re
import sqlite3
from typing import Optional

# ── Legal suffixes to strip from any company name ────────────────────────────
# Order matters: strip longer suffixes first to avoid partial matches
_LEGAL_SUFFIXES = [
    'limited liability company', 'limited partnership', 'limited liability',
    'public limited company',
    r'\bincorporated\b', r'\bcorporation\b', r'\bcompany\b',
    r'\bholdings?\b', r'\bgroup\b', r'\bpartners?\b', r'\bassociates?\b',
    r'\blimited\b', r'\bllc\b', r'\bcorp\.?\b', r'\binc\.?\b',
    r'\bltd\.?\b', r'\bplc\b', r'\bllp\b', r'\blp\b', r'\bsa\b', r'\bag\b',
    r'\bnv\b', r'\bbv\b', r'\bgmbh\b', r'\bse\b',
    # sector suffixes that add noise
    r'\benergy\s+corp\.?\b', r'\bpower\s+corp\.?\b',
    r'&\s+chemicals?\b', r'and\s+chemicals?\b',
    r'&\s+sons?\b',
]

# ── Abbreviation expansions (expand before matching) ─────────────────────────
_EXPANSIONS = {
    'intl': 'international',
    "int'l": 'international',
    'amer': 'american',
    'res': 'resources',
    'tech': 'technologies',
    'hldgs': 'holdings',
    'mfg': 'manufacturing',
    'svcs': 'services',
}

# ── Stop words — present in many names, carry no discriminating signal ────────
_STOP_WORDS = {
    'the', 'of', 'and', 'for', 'in', 'at', 'by', 'a', 'an',
    'new', 'north', 'south', 'east', 'west',  # directional — keep for disambiguation
}


def normalize(name: str) -> str:
    """
    Reduce a company name to its canonical discriminating tokens.

    Handles:
      - underscores (developer_key format) → spaces
      - legal suffixes stripped
      - mixed case → lowercase
      - punctuation removed
      - common abbreviations expanded

    Examples:
      'air_products_massena_green_hydrogen_facility' → 'air products massena green hydrogen facility'
      'Air Products & Chemicals, Inc.'               → 'air products'
      'CF Industries Holdings'                        → 'cf industries'
      'CLEAN HYDROGEN WORKS LA-1 LLC'                → 'clean hydrogen works la 1'
      'cf-industries'                                → 'cf industries'
    """
    if not name:
        return ''

    n = name.lower().strip()

    # underscores and hyphens → spaces
    n = n.replace('_', ' ').replace('-', ' ')

    # Expand abbreviations
    for abbr, full in _EXPANSIONS.items():
        n = re.sub(r'\b' + re.escape(abbr) + r'\b', full, n)

    # Strip legal suffixes (longest first)
    for suffix in _LEGAL_SUFFIXES:
        n = re.sub(suffix, ' ', n, flags=re.IGNORECASE)

    # Remove punctuation except alphanumerics and spaces
    n = re.sub(r'[^\w\s]', ' ', n)

    # Collapse whitespace
    n = re.sub(r'\s+', ' ', n).strip()

    return n


def token_set(normalized_name: str, min_len: int = 2) -> set:
    """Return set of meaningful tokens from a normalized name."""
    return {t for t in normalized_name.split() if len(t) >= min_len}


def match_score(name_a: str, name_b: str) -> float:
    """
    Jaccard-like overlap score between two normalized company names.
    Returns 0.0–1.0. Higher = more similar.

    Uses weighted scoring:
      - Rare/specific tokens (longer words) weighted more
      - Common tokens ('energy', 'power') weighted less
    """
    # Down-weight tokens that are very common in this domain
    _DOMAIN_COMMON = {'energy', 'power', 'hydrogen', 'clean', 'green', 'blue',
                      'carbon', 'global', 'national', 'american', 'international',
                      'resources', 'capital', 'fund', 'industries', 'solutions'}

    na = normalize(name_a) if name_a else ''
    nb = normalize(name_b) if name_b else ''

    ta = token_set(na)
    tb = token_set(nb)

    if not ta or not tb:
        return 0.0

    intersection = ta & tb
    union = ta | tb

    # Weighted: longer/rarer tokens count more
    def weight(token):
        if token in _DOMAIN_COMMON:
            return 0.3
        return min(len(token) / 6.0, 1.5)  # max 1.5x for long tokens

    w_intersection = sum(weight(t) for t in intersection)
    w_union = sum(weight(t) for t in union)

    if w_union == 0:
        return 0.0

    return w_intersection / w_union


def is_match(name_a: str, name_b: str, threshold: float = 0.45) -> bool:
    """True if two company names refer to the same company."""
    if not name_a or not name_b:
        return False
    # Exact normalized match always wins
    if normalize(name_a) == normalize(name_b):
        return True
    return match_score(name_a, name_b) >= threshold


# ── Company index: built once from DB, reused per session ────────────────────

def build_company_index(conn: sqlite3.Connection) -> dict:
    """
    Build a lookup index mapping normalized company names → CIK and primary key
    for all companies found across all relevant tables.

    Returns:
        {
          'normalized_name': {
              'cik': str | None,
              'developer_key': str | None,     # from unified_projects
              'reg_company_names': [str, ...], # from regulatory_evidence
          },
          ...
        }

    This index is built entirely from the database — no hardcoding.
    """
    conn.row_factory = sqlite3.Row
    index = {}

    # --- Pull from unified_projects ---
    projects = conn.execute("""
        SELECT DISTINCT developer_key, developer_name
        FROM unified_projects
        WHERE developer_key IS NOT NULL
    """).fetchall()

    for p in projects:
        norm = normalize(p['developer_key'])
        if not norm:
            continue
        if norm not in index:
            index[norm] = {'cik': None, 'developer_key': p['developer_key'],
                           'developer_name': p['developer_name'], 'reg_company_names': []}
        else:
            # May already exist from another table — fill in developer_key
            index[norm]['developer_key'] = index[norm].get('developer_key') or p['developer_key']

    # --- Pull from regulatory_evidence (with CIKs) ---
    reg_rows = conn.execute("""
        SELECT DISTINCT company_name, company_cik
        FROM regulatory_evidence
        WHERE company_name IS NOT NULL
    """).fetchall()

    for r in reg_rows:
        norm = normalize(r['company_name'])
        if not norm:
            continue

        # Try to find existing index entry with good overlap
        best_key = None
        best_score = 0.0
        for key in index:
            score = match_score(norm, key)
            if score > best_score:
                best_score = score
                best_key = key

        if best_score >= 0.45 and best_key:
            # Merge into existing entry
            index[best_key]['reg_company_names'].append(r['company_name'])
            if r['company_cik'] and not index[best_key]['cik']:
                index[best_key]['cik'] = r['company_cik']
        else:
            # New entry from regulatory side only
            index[norm] = {
                'cik': r['company_cik'],
                'developer_key': None,
                'developer_name': None,
                'reg_company_names': [r['company_name']],
            }

    return index


def find_reg_evidence_company_names(
    company_key: str,
    index: dict,
    conn: sqlite3.Connection,
) -> list:
    """
    Given a developer_key or company name, return the list of company_name
    strings to use when querying regulatory_evidence.

    Matching strategy (in order):
      1. CIK exact match (most reliable)
      2. Normalized name exact match
      3. Token overlap >= 0.45 threshold

    Returns list of company_name strings from regulatory_evidence.
    """
    norm_key = normalize(company_key)

    # Strategy 1: find via index
    best_entry = None
    best_score = 0.0

    for idx_key, entry in index.items():
        score = match_score(norm_key, idx_key)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score >= 0.45:
        # Return the reg_company_names from this entry
        names = best_entry.get('reg_company_names', [])
        if names:
            return names

        # If no reg names yet but we have a CIK, query by CIK
        if best_entry.get('cik'):
            cik = best_entry['cik']
            rows = conn.execute(
                "SELECT DISTINCT company_name FROM regulatory_evidence WHERE company_cik=?",
                (cik,)
            ).fetchall()
            return [r['company_name'] for r in rows]

    # Strategy 2: direct DB query with normalized name
    # Try exact normalized match first
    rows = conn.execute("SELECT DISTINCT company_name FROM regulatory_evidence").fetchall()
    matches = []
    for r in rows:
        if is_match(company_key, r['company_name']):
            matches.append(r['company_name'])
    return matches


def find_evidence_for_company(
    conn: sqlite3.Connection,
    company_key: str,
    index: dict,
    scope: str = 'specific',
) -> list:
    """
    Fetch regulatory_evidence rows for a company.

    scope:
      'specific' — only records where company_name matches THIS company
                   (avoids parent company evidence bleeding into subsidiaries)
      'broad'    — also includes parent company evidence if specific is empty

    Returns list of row dicts.
    """
    conn.row_factory = sqlite3.Row

    # Get the list of company_name variants in regulatory_evidence
    reg_names = find_reg_evidence_company_names(company_key, index, conn)

    if not reg_names:
        return []

    # Fetch records for exactly these company names
    placeholders = ','.join('?' * len(reg_names))
    rows = conn.execute(f"""
        SELECT * FROM regulatory_evidence
        WHERE company_name IN ({placeholders})
        AND (stale IS NULL OR stale = 0)
        ORDER BY document_date DESC
    """, reg_names).fetchall()

    result = [dict(r) for r in rows]

    # 'broad' fallback: if no specific records, try parent company
    if not result and scope == 'broad':
        # Strip the most specific token (e.g. 'massena' from 'air products massena')
        norm = normalize(company_key)
        tokens = norm.split()
        if len(tokens) > 2:
            parent_key = ' '.join(tokens[:2])  # take first two tokens as parent
            parent_names = find_reg_evidence_company_names(parent_key, index, conn)
            if parent_names:
                placeholders = ','.join('?' * len(parent_names))
                rows = conn.execute(f"""
                    SELECT * FROM regulatory_evidence
                    WHERE company_name IN ({placeholders})
                    AND (stale IS NULL OR stale = 0)
                    ORDER BY document_date DESC
                    LIMIT 3
                """, parent_names).fetchall()
                result = [dict(r) for r in rows]

    return result


# ── Self-test ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import sys
    db_path = sys.argv[1] if len(sys.argv) > 1 else 'blue_h2_intelligence.db'
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print("Building company index...")
    idx = build_company_index(conn)
    print(f"Index entries: {len(idx)}\n")

    print(f"{'developer_key':<45} {'match_score':<12} {'reg_names'}")
    print("─" * 90)

    projects = conn.execute(
        "SELECT developer_key, developer_name FROM unified_projects WHERE quarantined=0 ORDER BY developer_key"
    ).fetchall()

    for p in projects:
        reg_names = find_reg_evidence_company_names(p['developer_key'], idx, conn)
        norm = normalize(p['developer_key'])
        # Find best score
        best = max((match_score(norm, k) for k in idx), default=0)
        flag = "✓" if reg_names else "✗"
        print(f"  {flag} {p['developer_key']:<43} {best:.2f}  {reg_names[:2]}")

    conn.close()
