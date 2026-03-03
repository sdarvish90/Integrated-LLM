#!/usr/bin/env python3
"""
DecarbIQ — enrich_technology_capacity.py  [DEPRECATED]

NOTE: This logic is now integrated into bootstrap_projects.py as
phase_enrich_technology_capacity() (Phase 5d). This standalone script
is retained for debugging and diagnostic use only.

Populate technology and capacity on unified_projects using verbatim source data.

Six passes (no normalization, no inference — exact text from source):
  Pass 1: regulatory_evidence.derived_technology → technology
  Pass 2: claims.extracted_technology → technology  (filtered, no boilerplate)
  Pass 3: claims.extracted_capacity → capacity_raw
  Pass 4: GHGRP Subpart P reporting → technology (the boilerplate IS the signal)
  Pass 5: DOE award project_description → technology (verbatim from USA Spending)
  Pass 6: Facility name keywords → technology (the name IS the declaration)

Usage:
    python enrich_technology_capacity.py              # full run
    python enrich_technology_capacity.py --dry-run    # show changes without writing
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT   = Path(__file__).resolve().parent.parent
CORE_DB = _ROOT / 'data' / 'core_database.db'
DRY_RUN = '--dry-run' in sys.argv


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pass_1_derived_technology(db: sqlite3.Connection) -> int:
    """Set technology from regulatory_evidence.derived_technology.

    Source priority: doe_oced_detail > tx_rrc > epa_uic > epa_echo > other.
    Only touches projects where technology is currently NULL/empty.
    """
    print('\n── Pass 1: Technology from regulatory_evidence.derived_technology ──')

    projects = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE technology IS NULL OR technology = ''
    """).fetchall()

    updated = 0
    now = now_iso()

    for p in projects:
        pid = p['project_id']
        row = db.execute("""
            SELECT re.derived_technology, re.source_system
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            JOIN cleaned_documents cd ON c.source_id = cd.source_id
            JOIN regulatory_evidence re ON re.id = cd.source_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND re.derived_technology IS NOT NULL AND re.derived_technology != ''
            ORDER BY
                CASE re.source_system
                    WHEN 'doe_oced_detail' THEN 1
                    WHEN 'tx_rrc' THEN 2
                    WHEN 'epa_uic' THEN 3
                    WHEN 'epa_echo' THEN 4
                    ELSE 5
                END,
                re.id
            LIMIT 1
        """, (pid,)).fetchone()

        if row:
            tech = row['derived_technology']
            if DRY_RUN:
                print(f'  [dry-run] {pid[:50]:<52} ← {tech} ({row["source_system"]})')
            else:
                db.execute(
                    "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                    (tech, now, pid)
                )
            updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Updated: {updated} projects')
    return updated


def pass_2_extracted_technology(db: sqlite3.Connection) -> int:
    """Set technology from claims.extracted_technology.

    Only for projects still without technology after Pass 1.
    Filters: confidence >= 0.7, excludes GHGRP boilerplate.
    Takes most frequent value from filtered set.
    """
    print('\n── Pass 2: Technology from claims.extracted_technology ────────────')

    projects = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE technology IS NULL OR technology = ''
    """).fetchall()

    updated = 0
    now = now_iso()

    for p in projects:
        pid = p['project_id']
        row = db.execute("""
            SELECT c.extracted_technology, COUNT(*) as cnt
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.extracted_technology IS NOT NULL AND c.extracted_technology != ''
              AND c.confidence >= 0.7
              AND c.claim_text NOT LIKE 'This facility reports under GHGRP%'
            GROUP BY c.extracted_technology
            ORDER BY cnt DESC
            LIMIT 1
        """, (pid,)).fetchone()

        if row:
            tech = row['extracted_technology']
            if DRY_RUN:
                print(f'  [dry-run] {pid[:50]:<52} ← {tech} (n={row["cnt"]})')
            else:
                db.execute(
                    "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                    (tech, now, pid)
                )
            updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Updated: {updated} projects')
    return updated


def pass_3_capacity(db: sqlite3.Connection) -> int:
    """Set capacity_raw from claims.extracted_capacity.

    For each project, takes the highest-confidence resolved claim's value.
    Only touches projects where capacity_raw is currently NULL/empty.
    """
    print('\n── Pass 3: Capacity from claims.extracted_capacity ────────────────')

    projects = db.execute("""
        SELECT project_id FROM unified_projects
        WHERE capacity_raw IS NULL OR capacity_raw = ''
    """).fetchall()

    updated = 0
    now = now_iso()

    for p in projects:
        pid = p['project_id']
        row = db.execute("""
            SELECT c.extracted_capacity, c.confidence, c.claim_type
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.extracted_capacity IS NOT NULL AND c.extracted_capacity != ''
            ORDER BY c.confidence DESC, c.claim_id
            LIMIT 1
        """, (pid,)).fetchone()

        if row:
            cap = row['extracted_capacity']
            if DRY_RUN:
                print(f'  [dry-run] {pid[:50]:<52} ← {cap} (conf={row["confidence"]}, type={row["claim_type"]})')
            else:
                db.execute(
                    "UPDATE unified_projects SET capacity_raw=?, updated_at=? WHERE project_id=?",
                    (cap, now, pid)
                )
            updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Updated: {updated} projects')
    return updated


def pass_4_ghgrp_subpart_p(db: sqlite3.Connection) -> int:
    """Set technology for facilities that report GHGRP Subpart P.

    If a facility reports under Subpart P, it produces hydrogen.
    The claim text literally says "Subpart P (Hydrogen Production)".
    Uses the verbatim claim text as technology value.
    """
    print('\n── Pass 4: Technology from GHGRP Subpart P reporting ─────────────')

    # Find tech-less facility projects with Subpart P claims
    rows = db.execute("""
        SELECT DISTINCT up.project_id, up.project_name, c.claim_text
        FROM unified_projects up
        JOIN project_claims pc ON up.project_id = pc.project_id
        JOIN claims c ON pc.claim_id = c.claim_id
        WHERE (up.technology IS NULL OR up.technology = '')
          AND pc.resolution_method != 'unresolved'
          AND c.claim_type = 'hydrogen_production_method'
          AND c.claim_text LIKE '%Subpart P%Hydrogen Production%'
        ORDER BY up.project_name
    """).fetchall()

    updated = 0
    now = now_iso()

    for r in rows:
        pid = r['project_id']
        # Use the specific text from the claim
        tech = 'Hydrogen Production (GHGRP Subpart P)'
        if DRY_RUN:
            print(f'  [dry-run] {r["project_name"][:50]:<52} ← {tech}')
        else:
            db.execute(
                "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                (tech, now, pid)
            )
        updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Updated: {updated} projects')
    return updated


def pass_5_doe_award_description(db: sqlite3.Connection) -> int:
    """Set technology for DOE award projects from project_description claims.

    DOE project descriptions are verbatim from USA Spending / DOE award records.
    Takes the first project_description claim text (truncated to 200 chars).
    """
    print('\n── Pass 5: Technology from DOE award project_description ──────────')

    projects = db.execute("""
        SELECT project_id, project_name FROM unified_projects
        WHERE (technology IS NULL OR technology = '')
          AND project_type = 'doe_award'
    """).fetchall()

    updated = 0
    now = now_iso()

    for p in projects:
        pid = p['project_id']
        row = db.execute("""
            SELECT c.claim_text
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            WHERE pc.project_id = ?
              AND pc.resolution_method != 'unresolved'
              AND c.claim_type = 'project_description'
              AND LENGTH(c.claim_text) > 20
            ORDER BY c.confidence DESC, c.claim_id
            LIMIT 1
        """, (pid,)).fetchone()

        if row:
            tech = row['claim_text'][:200]
            if DRY_RUN:
                print(f'  [dry-run] {p["project_name"][:45]:<47} ← {tech[:60]}...')
            else:
                db.execute(
                    "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                    (tech, now, pid)
                )
            updated += 1

    if not DRY_RUN:
        db.commit()
    print(f'  Updated: {updated} projects')
    return updated


def pass_6_facility_name(db: sqlite3.Connection) -> int:
    """Set technology for projects whose facility name declares the technology.

    Facility names like "SWEENY HYDROGEN PRODUCTION PLANT" or
    "OCI CLEAN AMMONIA PRODUCTION FACILITY" are verbatim from GHGRP/EPA.
    Uses the source_identity (facility name) as-is.
    """
    print('\n── Pass 6: Technology from facility name keywords ─────────────────')

    # Keyword → technology label mapping (keywords that appear in facility names)
    # Each keyword must be an exact substring found in GHGRP/EPA facility names
    FACILITY_KEYWORDS = [
        ('HYDROGEN', 'hydrogen_production'),
        ('AMMONIA', 'ammonia_production'),
        ('CCS', 'ccs'),
        ('CARBON CAPTURE', 'carbon_capture'),
        ('CO2', 'co2_processing'),
    ]

    projects = db.execute("""
        SELECT project_id, project_name, source_identity FROM unified_projects
        WHERE (technology IS NULL OR technology = '')
          AND source_identity IS NOT NULL AND source_identity != ''
    """).fetchall()

    updated = 0
    now = now_iso()

    for p in projects:
        name_upper = (p['source_identity'] or '').upper()
        for keyword, tech in FACILITY_KEYWORDS:
            if keyword in name_upper:
                if DRY_RUN:
                    print(f'  [dry-run] {p["source_identity"][:50]:<52} ← {tech} (keyword: {keyword})')
                else:
                    db.execute(
                        "UPDATE unified_projects SET technology=?, updated_at=? WHERE project_id=?",
                        (tech, now, p['project_id'])
                    )
                updated += 1
                break  # first match wins

    if not DRY_RUN:
        db.commit()
    print(f'  Updated: {updated} projects')
    return updated


def print_summary(db: sqlite3.Connection):
    """Print coverage after enrichment."""
    print('\n══ ENRICHMENT SUMMARY ══════════════════════════════════════════════')

    total = db.execute("SELECT COUNT(*) FROM unified_projects").fetchone()[0]

    tech_count = db.execute(
        "SELECT COUNT(*) FROM unified_projects WHERE technology IS NOT NULL AND technology != ''"
    ).fetchone()[0]
    print(f'  Technology: {tech_count}/{total} ({tech_count/total*100:.1f}%)')

    cap_count = db.execute(
        "SELECT COUNT(*) FROM unified_projects WHERE capacity_raw IS NOT NULL AND capacity_raw != ''"
    ).fetchone()[0]
    print(f'  Capacity:   {cap_count}/{total} ({cap_count/total*100:.1f}%)')

    print('\n  Technology values:')
    rows = db.execute("""
        SELECT technology, COUNT(*) as cnt FROM unified_projects
        WHERE technology IS NOT NULL AND technology != ''
        GROUP BY technology ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        print(f'    {r["technology"]:<50} {r["cnt"]:>4}')

    print('\n  Capacity sample (first 20):')
    rows = db.execute("""
        SELECT project_name, capacity_raw FROM unified_projects
        WHERE capacity_raw IS NOT NULL AND capacity_raw != ''
        LIMIT 20
    """).fetchall()
    for r in rows:
        print(f'    {r["project_name"][:45]:<47} {r["capacity_raw"]}')


def run():
    if not CORE_DB.exists():
        print(f'ERROR: {CORE_DB} not found'); sys.exit(1)

    db = sqlite3.connect(str(CORE_DB))
    db.row_factory = sqlite3.Row

    print(f'{"DRY RUN" if DRY_RUN else "ENRICHING"} — Technology & Capacity')

    t1 = pass_1_derived_technology(db)
    t2 = pass_2_extracted_technology(db)
    t3 = pass_3_capacity(db)
    t4 = pass_4_ghgrp_subpart_p(db)
    t5 = pass_5_doe_award_description(db)
    t6 = pass_6_facility_name(db)

    print(f'\n  TOTAL: Pass1={t1}  Pass2={t2}  Pass3={t3}  Pass4={t4}  Pass5={t5}  Pass6={t6}')
    print_summary(db)
    db.close()


if __name__ == '__main__':
    run()
