#!/usr/bin/env python3
"""
DecarbIQ — fix_medium_severity.py
Addresses all medium-severity findings from the 2026-03-03 audit:

1. Merge 15 duplicate entity pairs (same name, different IDs)
2. Merge 8 duplicate company pairs
3. Delete 64 "Parent Company" literal-string aliases
4. Clean up 27 orphaned accumulator entries
5. Fix capacity misclassification (CF Industries, NextDecade, Carbon Solutions)
6. Remove ~25 false-positive spatial crossrefs (>4mi, 0 name overlap)

Usage:
    python fix_medium_severity.py --dry-run    # preview
    python fix_medium_severity.py              # execute
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import LEARNING_DB

DRY_RUN = '--dry-run' in sys.argv


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _merge_entities(db: sqlite3.Connection) -> int:
    """Merge duplicate entity pairs (case-insensitive name match)."""
    print("\n── Fix 1: Merge Duplicate Entities ──────────────────────────────")

    # Find duplicates: same LOWER(canonical_name), keep the one with most references
    dups = db.execute("""
        SELECT LOWER(canonical_name) AS norm, COUNT(*) AS cnt
        FROM entities
        GROUP BY LOWER(canonical_name)
        HAVING cnt > 1
    """).fetchall()

    if not dups:
        print("  No duplicate entities found.")
        return 0

    merged = 0
    for dup in dups:
        norm = dup['norm']
        # Get all entity IDs for this name, ordered by reference count (winner first)
        entities = db.execute("""
            SELECT e.entity_id, e.canonical_name,
                   (SELECT COUNT(*) FROM project_claims pc WHERE pc.entity_id = e.entity_id) +
                   (SELECT COUNT(*) FROM entity_aliases ea WHERE ea.entity_id = e.entity_id) AS refs
            FROM entities e
            WHERE LOWER(e.canonical_name) = ?
            ORDER BY refs DESC
        """, (norm,)).fetchall()

        winner = entities[0]
        losers = entities[1:]

        for loser in losers:
            if DRY_RUN:
                print(f"  [dry] MERGE entity '{loser['canonical_name']}' "
                      f"({loser['entity_id'][:12]}) → "
                      f"'{winner['canonical_name']}' ({winner['entity_id'][:12]})")
            else:
                # Move aliases (skip conflicts via INSERT OR IGNORE + DELETE)
                db.execute("""
                    UPDATE OR IGNORE entity_aliases
                    SET entity_id = ? WHERE entity_id = ?
                """, (winner['entity_id'], loser['entity_id']))
                db.execute("""
                    DELETE FROM entity_aliases WHERE entity_id = ?
                """, (loser['entity_id'],))

                # Move project_claims
                db.execute("""
                    UPDATE project_claims SET entity_id = ? WHERE entity_id = ?
                """, (winner['entity_id'], loser['entity_id']))

                # Move companies
                db.execute("""
                    UPDATE companies SET entity_id = ? WHERE entity_id = ?
                """, (winner['entity_id'], loser['entity_id']))

                # Delete loser entity
                db.execute("DELETE FROM entities WHERE entity_id = ?",
                           (loser['entity_id'],))

            merged += 1

    if not DRY_RUN:
        db.commit()

    # Also merge "linde gas north america, llc" (with comma) into
    # "linde gas north america llc" (without comma) — same company
    comma_variant = db.execute("""
        SELECT entity_id FROM entities
        WHERE LOWER(canonical_name) = 'linde gas north america, llc'
    """).fetchall()
    no_comma = db.execute("""
        SELECT entity_id FROM entities
        WHERE LOWER(canonical_name) = 'linde gas north america llc'
    """).fetchone()

    if comma_variant and no_comma:
        for cv in comma_variant:
            if DRY_RUN:
                print(f"  [dry] MERGE 'Linde Gas North America, LLC' → 'LINDE GAS NORTH AMERICA LLC'")
            else:
                db.execute("UPDATE OR IGNORE entity_aliases SET entity_id = ? WHERE entity_id = ?",
                           (no_comma['entity_id'], cv['entity_id']))
                db.execute("DELETE FROM entity_aliases WHERE entity_id = ?", (cv['entity_id'],))
                db.execute("UPDATE project_claims SET entity_id = ? WHERE entity_id = ?",
                           (no_comma['entity_id'], cv['entity_id']))
                db.execute("UPDATE companies SET entity_id = ? WHERE entity_id = ?",
                           (no_comma['entity_id'], cv['entity_id']))
                db.execute("DELETE FROM entities WHERE entity_id = ?", (cv['entity_id'],))
            merged += 1
        if not DRY_RUN:
            db.commit()

    print(f"  Entities merged: {merged}")
    return merged


def _merge_companies(db: sqlite3.Connection) -> int:
    """Merge duplicate company pairs (case-insensitive name match)."""
    print("\n── Fix 2: Merge Duplicate Companies ─────────────────────────────")

    dups = db.execute("""
        SELECT LOWER(company_name) AS norm, COUNT(*) AS cnt
        FROM companies
        GROUP BY LOWER(company_name)
        HAVING cnt > 1
    """).fetchall()

    if not dups:
        print("  No duplicate companies found.")
        return 0

    merged = 0
    for dup in dups:
        norm = dup['norm']
        # Winner: most projects, has entity_id
        companies = db.execute("""
            SELECT c.company_id, c.company_name, c.entity_id, c.company_key,
                   (SELECT COUNT(*) FROM unified_projects up WHERE up.company_id = c.company_id) AS proj_cnt
            FROM companies c
            WHERE LOWER(c.company_name) = ?
            ORDER BY proj_cnt DESC, c.entity_id IS NOT NULL DESC
        """, (norm,)).fetchall()

        winner = companies[0]
        losers = companies[1:]

        for loser in losers:
            if DRY_RUN:
                print(f"  [dry] MERGE company '{loser['company_name']}' "
                      f"({loser['company_id'][:12]}) → "
                      f"'{winner['company_name']}' ({winner['company_id'][:12]})")
            else:
                # Move projects
                db.execute("""
                    UPDATE unified_projects SET company_id = ? WHERE company_id = ?
                """, (winner['company_id'], loser['company_id']))

                # Move corporate_events
                db.execute("""
                    UPDATE corporate_events SET parent_company_id = ?
                    WHERE parent_company_id = ?
                """, (winner['company_id'], loser['company_id']))

                # If winner has no entity_id but loser does, take it
                if not winner['entity_id'] and loser['entity_id']:
                    db.execute("""
                        UPDATE companies SET entity_id = ? WHERE company_id = ?
                    """, (loser['entity_id'], winner['company_id']))

                # Delete loser
                db.execute("DELETE FROM companies WHERE company_id = ?",
                           (loser['company_id'],))

            merged += 1

    if not DRY_RUN:
        db.commit()
    print(f"  Companies merged: {merged}")
    return merged


def _fix_parent_company_aliases(db: sqlite3.Connection) -> int:
    """Delete 'Parent Company' literal-string aliases (extraction bug)."""
    print("\n── Fix 3: Remove 'Parent Company' Literal Aliases ───────────────")

    count = db.execute(
        "SELECT COUNT(*) FROM entity_aliases WHERE alias = 'Parent Company'"
    ).fetchone()[0]

    print(f"  Found: {count} aliases with alias = 'Parent Company'")

    if DRY_RUN:
        print(f"  [dry] Would delete {count} rows")
    else:
        db.execute("DELETE FROM entity_aliases WHERE alias = 'Parent Company'")
        db.commit()
        print(f"  Deleted: {count}")

    return count


def _clean_orphaned_accumulators(db: sqlite3.Connection) -> int:
    """Delete accumulator entries for non-existent projects."""
    print("\n── Fix 4: Clean Orphaned Accumulator Entries ─────────────────────")

    count = db.execute("""
        SELECT COUNT(*) FROM project_signal_accumulator
        WHERE project_id NOT IN (SELECT project_id FROM unified_projects)
    """).fetchone()[0]

    print(f"  Found: {count} orphaned accumulator entries")

    if DRY_RUN:
        print(f"  [dry] Would delete {count} rows")
    else:
        db.execute("""
            DELETE FROM project_signal_accumulator
            WHERE project_id NOT IN (SELECT project_id FROM unified_projects)
        """)
        db.commit()
        print(f"  Deleted: {count}")

    return count


def _fix_capacity_misclassification(db: sqlite3.Connection) -> int:
    """Fix capacity_mtpa_h2 for non-H2 projects."""
    print("\n── Fix 5: Fix Capacity Misclassification ────────────────────────")

    fixes = [
        # (project_name_pattern, reason, new_capacity_mtpa_h2)
        ('NextDecade Corp', 'LNG capacity (6 MTPA LNG, not H2)', None),
        ('CARBON SOLUTIONS%', 'CO2 transport capacity (25 MT CO2, not H2)', None),
        ('CF Industries', 'Ammonia capacity (10 MT ammonia, not H2)', None),
    ]

    fixed = 0
    for pattern, reason, new_val in fixes:
        row = db.execute("""
            SELECT project_id, project_name, capacity_mtpa_h2, capacity_raw
            FROM unified_projects
            WHERE project_name LIKE ? AND capacity_mtpa_h2 IS NOT NULL
        """, (pattern,)).fetchone()

        if row:
            if DRY_RUN:
                print(f"  [dry] {row['project_name']}: capacity_mtpa_h2={row['capacity_mtpa_h2']} → NULL "
                      f"({reason})")
            else:
                db.execute("""
                    UPDATE unified_projects
                    SET capacity_mtpa_h2 = NULL, updated_at = ?
                    WHERE project_id = ?
                """, (now_iso(), row['project_id']))
                print(f"  Fixed: {row['project_name']} — capacity_mtpa_h2 → NULL ({reason})")
            fixed += 1

    if not DRY_RUN:
        db.commit()
    print(f"  Capacity fixes: {fixed}")
    return fixed


def _remove_false_positive_crossrefs(db: sqlite3.Connection) -> int:
    """Remove spatial crossrefs with >4 miles distance and 0 name overlap."""
    print("\n── Fix 6: Remove False-Positive Spatial Crossrefs ───────────────")

    # Find them
    fps = db.execute("""
        SELECT id, source_a_system, source_a_id, source_b_system, source_b_id,
               confidence, match_detail
        FROM facility_crossref
        WHERE match_method = 'spatial'
          AND json_extract(match_detail, '$.distance_miles') > 4.0
          AND json_extract(match_detail, '$.name_overlap') = 0.0
    """).fetchall()

    print(f"  Found: {len(fps)} false-positive spatial crossrefs (>4mi, 0 overlap)")

    if DRY_RUN:
        for fp in fps[:5]:
            print(f"    [dry] {fp['source_a_system']}:{fp['source_a_id']} ↔ "
                  f"{fp['source_b_system']}:{fp['source_b_id']}  conf={fp['confidence']:.3f}")
        if len(fps) > 5:
            print(f"    ... and {len(fps) - 5} more")
    else:
        ids = [fp['id'] for fp in fps]
        if ids:
            placeholders = ','.join('?' for _ in ids)
            db.execute(f"DELETE FROM facility_crossref WHERE id IN ({placeholders})", ids)
            db.commit()
        print(f"  Deleted: {len(fps)}")

    return len(fps)


def _clean_orphaned_events(db: sqlite3.Connection) -> int:
    """Delete project_events for non-existent projects."""
    print("\n── Fix 7: Clean Orphaned Project Events ─────────────────────────")

    count = db.execute("""
        SELECT COUNT(*) FROM project_events
        WHERE project_id NOT IN (SELECT project_id FROM unified_projects)
    """).fetchone()[0]

    print(f"  Found: {count} orphaned project events")

    if DRY_RUN:
        print(f"  [dry] Would delete {count} rows")
    else:
        db.execute("""
            DELETE FROM project_events
            WHERE project_id NOT IN (SELECT project_id FROM unified_projects)
        """)
        db.commit()
        print(f"  Deleted: {count}")

    return count


def run():
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    prefix = "DRY RUN — " if DRY_RUN else ""
    print(f"{prefix}Medium-Severity Audit Fixes")
    print(f"  Database: {LEARNING_DB}")

    totals = {}
    totals['entities_merged'] = _merge_entities(db)
    totals['companies_merged'] = _merge_companies(db)
    totals['parent_aliases_removed'] = _fix_parent_company_aliases(db)
    totals['orphaned_accumulators'] = _clean_orphaned_accumulators(db)
    totals['capacity_fixes'] = _fix_capacity_misclassification(db)
    totals['false_positive_crossrefs'] = _remove_false_positive_crossrefs(db)
    totals['orphaned_events'] = _clean_orphaned_events(db)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for key, val in totals.items():
        print(f"  {key:<30} {val}")

    db.close()


if __name__ == '__main__':
    run()
