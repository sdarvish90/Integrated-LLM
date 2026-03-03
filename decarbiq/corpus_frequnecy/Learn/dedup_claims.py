#!/usr/bin/env python3
"""
DecarbIQ — dedup_claims.py
Deduplicate claims that have identical (project_id, claim_type, claim_text).

The TCEQ extraction generates per-permit claims for static facility attributes
(address, NAICS, status_date, etc.), resulting in hundreds of identical claims
per facility. This script keeps one representative claim per group and deletes
the rest from project_claims and claims.

Usage:
    python dedup_claims.py --dry-run    # preview what would be deleted
    python dedup_claims.py              # execute deduplication
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


def run():
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    prefix = "DRY RUN — " if DRY_RUN else ""
    print(f"{prefix}Claim Deduplication")
    print(f"  Database: {LEARNING_DB}")

    # Count before
    total_claims = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    total_pc = db.execute("SELECT COUNT(*) FROM project_claims").fetchone()[0]
    print(f"\n  Before: {total_claims:,} claims, {total_pc:,} project_claims")

    # Find duplicate groups: same (project_id, claim_type, claim_text) with count > 1
    # For each group, identify the "keeper" — newest document_date, then highest confidence
    print("\n  Identifying duplicates...")
    dup_groups = db.execute("""
        SELECT pc.project_id, c.claim_type, c.claim_text,
               COUNT(*) AS cnt,
               GROUP_CONCAT(c.claim_id) AS all_ids
        FROM project_claims pc
        JOIN claims c ON c.claim_id = pc.claim_id
        GROUP BY pc.project_id, c.claim_type, c.claim_text
        HAVING cnt > 1
    """).fetchall()

    print(f"  Duplicate groups found: {len(dup_groups):,}")

    if not dup_groups:
        print("  No duplicates to remove.")
        db.close()
        return

    # Stats by source system
    total_to_delete = 0
    source_stats: dict[str, int] = {}
    claims_to_delete: list[str] = []

    for group in dup_groups:
        ids = group['all_ids'].split(',')
        count = group['cnt']

        # Pick keeper: newest document_date, tiebreak highest confidence
        keeper_row = db.execute("""
            SELECT c.claim_id, c.document_date, c.confidence
            FROM claims c
            WHERE c.claim_id IN ({})
            ORDER BY c.document_date DESC NULLS LAST,
                     c.confidence DESC NULLS LAST
            LIMIT 1
        """.format(','.join('?' for _ in ids)), ids).fetchone()

        keeper_id = keeper_row['claim_id']
        delete_ids = [cid for cid in ids if cid != keeper_id]
        claims_to_delete.extend(delete_ids)
        total_to_delete += len(delete_ids)

        # Track by source system
        if delete_ids:
            sys_row = db.execute("""
                SELECT cd.source_system
                FROM claims c
                JOIN cleaned_documents cd ON c.source_id = cd.source_id
                WHERE c.claim_id = ?
            """, (delete_ids[0],)).fetchone()
            sys_name = sys_row['source_system'] if sys_row else 'unknown'
            source_stats[sys_name] = source_stats.get(sys_name, 0) + len(delete_ids)

    print(f"\n  Claims to delete: {total_to_delete:,}")
    print(f"  Claims to keep:   {total_claims - total_to_delete:,}")
    print(f"\n  Deletions by source system:")
    for sys_name, cnt in sorted(source_stats.items(), key=lambda x: -x[1]):
        print(f"    {sys_name:<20} {cnt:>7,}")

    if DRY_RUN:
        print(f"\n  Dry run — no changes. Re-run without --dry-run to execute.")
        db.close()
        return

    # Execute deletion in batches
    print(f"\n  Deleting {total_to_delete:,} duplicate claims...")
    batch_size = 500
    deleted_pc = 0
    deleted_c = 0

    for i in range(0, len(claims_to_delete), batch_size):
        batch = claims_to_delete[i:i + batch_size]
        placeholders = ','.join('?' for _ in batch)

        # Delete from project_claims first (references claims)
        db.execute(f"DELETE FROM project_claims WHERE claim_id IN ({placeholders})", batch)
        deleted_pc += db.execute("SELECT changes()").fetchone()[0]

        # Delete from claims (triggers FTS delete via trigger)
        db.execute(f"DELETE FROM claims WHERE claim_id IN ({placeholders})", batch)
        deleted_c += db.execute("SELECT changes()").fetchone()[0]

        if (i // batch_size + 1) % 20 == 0:
            db.commit()
            print(f"    ... {i + len(batch):,}/{total_to_delete:,}")

    db.commit()
    print(f"  Deleted: {deleted_pc:,} project_claims, {deleted_c:,} claims")

    # Rebuild FTS indexes
    print("  Rebuilding FTS indexes...")
    try:
        db.execute("INSERT INTO claims_fts(claims_fts) VALUES('rebuild')")
        db.commit()
        print("  claims_fts rebuilt.")
    except sqlite3.OperationalError as e:
        print(f"  FTS rebuild warning: {e}")

    # Final counts
    final_claims = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    final_pc = db.execute("SELECT COUNT(*) FROM project_claims").fetchone()[0]
    print(f"\n  After: {final_claims:,} claims, {final_pc:,} project_claims")
    print(f"  Reduction: {total_claims - final_claims:,} claims removed "
          f"({100 * (total_claims - final_claims) / max(1, total_claims):.1f}%)")

    db.close()


if __name__ == '__main__':
    run()
