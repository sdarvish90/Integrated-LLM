#!/usr/bin/env python3
"""
Un-quarantine projects with strong regulatory signals.

Criteria:
  1. EPA UIC Class VI permit holders (CO2 sequestration — real CCS projects)
  2. Active DOE awards (federally funded, confirmed via USAspending)
  3. TX RRC Class VI permit holders (Oxy, Denbury, Orchard Storage, etc.)

Dedup rules:
  - If same developer has both a claims-based and auto_create entry,
    un-quarantine the claims-based one only.
  - Skip A2 duplicates (correctly flagged as entity duplicates).
  - Skip entries with 'non_project_entity' quarantine reason.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from config import LEARNING_DB

NOW = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def run():
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    # ── Category 1: EPA UIC CCS projects ──────────────────────────────────
    uic_projects = db.execute("""
        SELECT DISTINCT up.project_id, up.project_name, up.developer_name,
               up.state, up.stage, up.technology,
               up.quarantine_reason, up.bootstrap_source
        FROM unified_projects up
        JOIN project_claims pc ON up.project_id = pc.project_id
        JOIN claims c ON pc.claim_id = c.claim_id
        JOIN cleaned_documents cd ON c.source_id = cd.source_id
        WHERE up.quarantined = 1
          AND cd.source_system = 'epa_uic'
          AND up.technology IN ('point_source_ccs', 'direct_air_capture')
          AND up.project_name NOT LIKE 'RRC Operator%'
          AND up.quarantine_reason NOT LIKE 'non_project_entity%'
          AND up.quarantine_reason NOT LIKE 'A2:%'
    """).fetchall()

    # ── Category 2: TX RRC Class VI permit holders ────────────────────────
    rrc_projects = db.execute("""
        SELECT DISTINCT up.project_id, up.project_name, up.developer_name,
               up.state, up.stage, up.technology,
               up.quarantine_reason, up.bootstrap_source
        FROM unified_projects up
        JOIN project_claims pc ON up.project_id = pc.project_id
        JOIN claims c ON pc.claim_id = c.claim_id
        JOIN cleaned_documents cd ON c.source_id = cd.source_id
        WHERE up.quarantined = 1
          AND cd.source_system = 'tx_rrc'
          AND up.technology IN ('point_source_ccs', 'direct_air_capture', 'co2_transport_storage')
          AND up.quarantine_reason NOT LIKE 'non_project_entity%'
          AND up.quarantine_reason NOT LIKE 'A2:%'
    """).fetchall()

    # ── Category 3: Active DOE awards ─────────────────────────────────────
    doe_projects = db.execute("""
        SELECT DISTINCT up.project_id, up.project_name, up.developer_name,
               up.state, up.stage, up.technology,
               up.quarantine_reason, up.bootstrap_source,
               ds.doe_award_status
        FROM unified_projects up
        JOIN project_claims pc ON up.project_id = pc.project_id
        JOIN claims c ON pc.claim_id = c.claim_id
        JOIN doe_status ds ON c.source_id = ds.source_id
        WHERE up.quarantined = 1
          AND ds.doe_award_status = 'active'
          AND up.quarantine_reason NOT LIKE 'A2:%'
          AND up.quarantine_reason NOT LIKE 'non_project_entity%'
    """).fetchall()

    # ── Dedup: prefer claims-based over auto_create ───────────────────────
    # For each developer, if both exist, keep only the claims-based entry
    all_candidates = {}

    for row in list(uic_projects) + list(rrc_projects) + list(doe_projects):
        pid = row['project_id']
        if pid in all_candidates:
            continue
        all_candidates[pid] = dict(row)

    # Group by developer name to detect duplicates
    by_developer: dict[str, list[dict]] = {}
    for pid, proj in all_candidates.items():
        dev = (proj['developer_name'] or '').strip().upper()
        if dev:
            by_developer.setdefault(dev, []).append(proj)

    skip_ids = set()
    for dev, entries in by_developer.items():
        if len(entries) <= 1:
            continue
        # Prefer claims-based over auto_create
        claims_entries = [e for e in entries if e['bootstrap_source'] == 'claims']
        auto_entries = [e for e in entries if e['bootstrap_source'] == 'auto_create']
        if claims_entries and auto_entries:
            for ae in auto_entries:
                skip_ids.add(ae['project_id'])
                print(f"  SKIP (auto_create dup): {ae['project_name'][:60]} → keeping claims-based")

    # ── Execute un-quarantine ─────────────────────────────────────────────
    to_unquarantine = {pid: proj for pid, proj in all_candidates.items() if pid not in skip_ids}

    print(f"\n{'='*70}")
    print(f"UN-QUARANTINE SUMMARY")
    print(f"{'='*70}")
    print(f"  EPA UIC CCS candidates:  {len(uic_projects)}")
    print(f"  TX RRC candidates:       {len(rrc_projects)}")
    print(f"  Active DOE candidates:   {len(doe_projects)}")
    print(f"  After dedup/skip:        {len(to_unquarantine)}")
    print(f"{'='*70}\n")

    for pid, proj in sorted(to_unquarantine.items(), key=lambda x: x[1]['project_name']):
        old_reason = proj['quarantine_reason']
        name = proj['project_name'][:55]
        tech = proj['technology'] or '?'
        state = proj['state'] or '?'
        print(f"  UN-Q: {name:<55} {state:<5} {tech:<25} was: {old_reason[:40]}")

    print(f"\nApplying {len(to_unquarantine)} updates...")

    for pid in to_unquarantine:
        db.execute("""
            UPDATE unified_projects
            SET quarantined = 0, quarantine_reason = NULL, updated_at = ?
            WHERE project_id = ?
        """, (NOW, pid))

    db.commit()

    # ── Final counts ──────────────────────────────────────────────────────
    counts = db.execute("SELECT quarantined, COUNT(*) FROM unified_projects GROUP BY quarantined").fetchall()
    print(f"\nFinal counts:")
    for row in counts:
        label = "Active" if row[0] == 0 else "Quarantined"
        print(f"  {label}: {row[1]}")

    db.close()
    print("\nDone.")


if __name__ == '__main__':
    run()
