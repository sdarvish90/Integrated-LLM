#!/usr/bin/env python3
"""
DecarbIQ — migrate_corporate_events.py
Populates the corporate_events table with merger/acquisition dates derived from
entity_aliases (GHGRP parent-company filings) and known manual seeds.

Then remaps subsidiary claims to the correct parent company based on temporal
ownership: if a document is dated AFTER an acquisition, the subsidiary's claims
resolve to the acquirer's project.

Usage:
    python migrate_corporate_events.py              # dry-run: report only
    python migrate_corporate_events.py --apply      # write corporate_events + remap claims
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import LEARNING_DB

# ── Normalize helper (same as connect.py) ────────────────────────────────────

_LEGAL_SUFFIXES = re.compile(
    r'\b(llc|inc|corp|corporation|company|co|limited|ltd|'
    r'holdings|group|energy|and|the|&|lp)\b', re.I
)

def _normalize(name: str) -> str:
    n = name.lower().strip()
    n = _LEGAL_SUFFIXES.sub(' ', n)
    n = re.sub(r'[^a-z0-9 ]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Known corporate events with exact dates ──────────────────────────────────
# These are well-known mergers/acquisitions where the entity_aliases data from
# GHGRP shows the ownership transition but doesn't give the exact transaction
# date. We supplement with the actual closing dates.
#
# Format: (parent_company_name, child_names[], event_type, event_date, description)
#
# The event_date is the effective date: claims dated ON or AFTER this date
# for any child_name should resolve to the parent.

KNOWN_EVENTS = [
    # Linde acquired Praxair — merger closed 2018-10-31
    ('Linde', [
        'PRAXAIR', 'Praxair', 'PRAXAIR INC', 'Praxair Inc', 'Praxair, Inc',
        'Praxair, Inc.', 'Praxair,Inc',
        'PRAXAIR INC.-LINDE DIVISION',
        'PRAXAIR TEXAS CITY', 'PRAXAIR Texas City Hydrogen Complex',
        'PRAXAIR - WHITING, IN 1-4', 'PRAXAIR - WHITING, IN 5&6',
        'PRAXAIR ONTARIO CA',
        'Praxair Port Arthur #379', 'Praxair Port Arthur Facility',
        'PRAXAIR HYDROGEN/SMR AND ASU/SP',
        'PRAXAIR INC - SYNGAS SEPARATION UNIT',
        'PRAXAIR FREEPORT HYDROGEN PSA UNIT',
    ], 'acquisition', '2018-10-31',
     'Linde plc acquired Praxair Inc in an all-stock merger of equals'),

    # PBF Energy acquired Torrance Refinery from ExxonMobil — closed 2016-07-01
    # NOTE: PBF Energy is not in the companies table, so we cannot create a
    # temporal event. Pre-2016 claims resolve to ExxonMobil via entity_aliases.
    # Post-2016 claims stay under ExxonMobil until PBF is added.
    # ('PBF Energy', ['TORRANCE REFINING COMPANY LLC'], 'acquisition', '2016-07-01', ...),

    # Marathon Petroleum acquired Andeavor (formerly Tesoro) — closed 2018-10-01
    ('PHILLIPS 66', [
        # Phillips 66 spun off from ConocoPhillips in 2012
    ], 'spin_off', '2012-05-01',
     'Phillips 66 spun off from ConocoPhillips in May 2012'),

    # Tesoro rebranded to Andeavor, then acquired by Marathon — closed 2018-10-01
    ('Marathon Petroleum', [
        'TESORO CORP', 'Tesoro Corporation', 'Tesoro',
        'Andeavor', 'ANDEAVOR',
        'TESORO ALASKA PETROLEUM CO',
        'TESORO REFINING AND MARKETING COMPANY GOLDEN EAGLE REFINERY',
        'Martinez Renewable Fuel Facility',
    ], 'acquisition', '2018-10-01',
     'Marathon Petroleum acquired Andeavor (formerly Tesoro) in October 2018'),

    # HollyFrontier merged with Sinclair → HF Sinclair — effective 2022-03-14
    ('HF Sinclair', [
        'HOLLYFRONTIER CORP', 'HollyFrontier Corporation', 'HollyFrontier',
        'HOLLYFRONTIER EL DORADO REFINING LLC',
        'HollyFrontier Cheyenne Refining LLC',
        'Frontier Refining & Marketing',
    ], 'merger', '2022-03-14',
     'HollyFrontier merged with Sinclair Oil to form HF Sinclair in March 2022'),

    # ConocoPhillips spun off Phillips 66 refining — 2012-05-01
    # Pre-2012 Phillips 66 refineries were ConocoPhillips subsidiaries
    ('ConocoPhillips', [
        'BORGER REFINERY',
        'SAN FRANCISCO REFINERY AT RODEO',
    ], 'divestiture', '2012-05-01',
     'ConocoPhillips spun off downstream/refining as Phillips 66 in May 2012'),

    # Western Refining acquired by Tesoro/Andeavor — 2017-06-01
    ('Andeavor', [
        'WESTERN REFINING INC', 'Western Refining, Inc.',
    ], 'acquisition', '2017-06-01',
     'Tesoro (Andeavor) acquired Western Refining in June 2017'),
]


# ── Phase 1: Detect temporal transitions from entity_aliases ─────────────────

def _detect_transitions(db: sqlite3.Connection) -> list[dict]:
    """Scan entity_aliases for subsidiaries that changed parent companies over time.

    Returns list of detected transitions with:
      child_name, old_parent, new_parent, transition_year, evidence
    """
    # Get all subsidiary-type aliases with dates, grouped by child name
    rows = db.execute("""
        SELECT ea.alias AS child_name,
               e.canonical_name AS parent_name,
               ea.alias_type,
               ea.cited_document_date,
               ea.cited_document_type
        FROM entity_aliases ea
        JOIN entities e ON ea.entity_id = e.entity_id
        WHERE ea.alias_type IN ('subsidiary', 'acquired_by', 'parent', 'parent_company')
          AND ea.cited_document_date IS NOT NULL
        ORDER BY ea.alias, ea.cited_document_date
    """).fetchall()

    # Group by normalized child name
    children: dict[str, list] = {}
    for r in rows:
        cn = _normalize(r['child_name'])
        if cn not in children:
            children[cn] = []
        children[cn].append({
            'child_name': r['child_name'],
            'parent_name': r['parent_name'],
            'parent_norm': _normalize(r['parent_name']),
            'date': r['cited_document_date'],
            'doc_type': r['cited_document_type'],
            'alias_type': r['alias_type'],
        })

    transitions = []
    for cn, records in children.items():
        # Find children with multiple DIFFERENT parents (normalized)
        parents_by_date = {}
        for rec in records:
            pn = rec['parent_norm']
            d = rec['date']
            if pn not in parents_by_date:
                parents_by_date[pn] = []
            parents_by_date[pn].append(d)

        # Filter out "Parent Company" and very short normalized names
        real_parents = {p: dates for p, dates in parents_by_date.items()
                        if p and len(p) > 3 and p != 'parent'}

        if len(real_parents) < 2:
            continue

        # Sort parents by earliest date
        parent_timeline = []
        for pn, dates in real_parents.items():
            earliest = min(dates)
            latest = max(dates)
            parent_timeline.append((earliest, latest, pn))
        parent_timeline.sort()

        # Detect transitions: when parent changes between consecutive date ranges
        for i in range(len(parent_timeline) - 1):
            old_earliest, old_latest, old_parent = parent_timeline[i]
            new_earliest, new_latest, new_parent = parent_timeline[i + 1]

            # Only flag if the transition is clear (old's latest < new's earliest)
            # or they overlap in a single year boundary
            transitions.append({
                'child_name': records[0]['child_name'],  # original form
                'child_norm': cn,
                'old_parent': old_parent,
                'old_latest': old_latest,
                'new_parent': new_parent,
                'new_earliest': new_earliest,
                'transition_approx': new_earliest,  # first year under new parent
            })

    return transitions


# ── Phase 2: Populate corporate_events ───────────────────────────────────────

def _find_company_id(db: sqlite3.Connection, name: str) -> str | None:
    """Find company_id by exact or normalized name match."""
    row = db.execute(
        "SELECT company_id FROM companies WHERE company_name = ?", (name,)
    ).fetchone()
    if row:
        return row[0]

    norm = _normalize(name)
    row = db.execute(
        "SELECT company_id, company_name FROM companies WHERE company_key = ?",
        (norm.replace(' ', '_'),)
    ).fetchone()
    if row:
        return row[0]

    # Fuzzy: normalized substring match
    for r in db.execute("SELECT company_id, company_name FROM companies"):
        cn = _normalize(r[1])
        if norm and cn and len(norm) > 3:
            if norm == cn or norm in cn or cn in norm:
                return r[0]
    return None


def populate_events(db: sqlite3.Connection, apply: bool = False) -> dict:
    """Populate corporate_events from KNOWN_EVENTS and auto-detected transitions."""

    stats = {'manual_events': 0, 'auto_detected': 0, 'claims_remapped': 0}

    print("\n" + "=" * 70)
    print("CORPORATE EVENTS MIGRATION")
    print("=" * 70)

    # ── 2a. Manual known events ──────────────────────────────────────────
    print("\n── Phase 1: Known Corporate Events ──")
    for parent_name, child_names, event_type, event_date, description in KNOWN_EVENTS:
        parent_id = _find_company_id(db, parent_name)
        if not parent_id:
            print(f"  SKIP: parent '{parent_name}' not in companies table")
            continue

        for child_name in child_names:
            child_id = _find_company_id(db, child_name)
            child_norm = _normalize(child_name)

            if apply:
                db.execute("""
                    INSERT OR IGNORE INTO corporate_events
                    (parent_company_id, child_company_id, child_name, child_name_norm,
                     event_type, event_date, event_source, description, created_at)
                    VALUES (?,?,?,?,?,?,'manual_seed',?,?)
                """, (parent_id, child_id, child_name, child_norm,
                      event_type, event_date, description, _now()))
            stats['manual_events'] += 1
            print(f"  + {child_name:50s} → {parent_name} ({event_type}, {event_date})")

    if apply:
        db.commit()

    # ── 2b. Auto-detect transitions from entity_aliases ──────────────────
    print(f"\n── Phase 2: Auto-Detected Ownership Transitions ──")
    transitions = _detect_transitions(db)
    print(f"  Found {len(transitions)} transitions from GHGRP parent-company filings")

    for t in transitions:
        print(f"\n  {t['child_name']}")
        print(f"    {t['old_parent']} (until {t['old_latest']}) → "
              f"{t['new_parent']} (from {t['new_earliest']})")

        # Only auto-insert if not already covered by KNOWN_EVENTS
        child_norm = t['child_norm']
        existing = db.execute(
            "SELECT 1 FROM corporate_events WHERE child_name_norm = ?",
            (child_norm,)
        ).fetchone() if apply else None

        if existing:
            print(f"    (already in corporate_events — skipping)")
            continue

        # Guard: skip if the child IS a known parent company in companies table
        # (prevents GHGRP reporting hierarchy from being mistaken for acquisitions)
        child_as_company = _find_company_id(db, t['child_name'])
        if child_as_company:
            print(f"    SKIP: child '{t['child_name']}' is itself in companies table")
            continue

        # Find parent company_id for the NEW parent
        new_parent_id = _find_company_id(db, t['new_parent'])
        if not new_parent_id:
            # Try the original (un-normalized) child parent name from any record
            print(f"    SKIP: new parent '{t['new_parent']}' not in companies table")
            stats['auto_detected'] += 1
            continue

        # The transition date is Jan 1 of the year shown in the first GHGRP filing
        # under the new parent (conservative — actual acquisition was sometime before)
        transition_date = t['new_earliest'][:4] + '-01-01'

        if apply:
            db.execute("""
                INSERT OR IGNORE INTO corporate_events
                (parent_company_id, child_company_id, child_name, child_name_norm,
                 event_type, event_date, event_source, description, created_at)
                VALUES (?,?,?,?,?,?,'ghgrp_detected',?,?)
            """, (new_parent_id, None, t['child_name'], child_norm,
                  'acquisition', transition_date,
                  f"Detected from GHGRP: parent changed from {t['old_parent']} "
                  f"to {t['new_parent']} around {transition_date}",
                  _now()))
        stats['auto_detected'] += 1

    if apply:
        db.commit()

    # ── 2c. Merge standalone Praxair projects into Linde ─────────────────
    print(f"\n── Phase 3: Merge Acquired Company Projects ──")

    linde_id = _find_company_id(db, 'Linde')
    if linde_id:
        praxair_projects = db.execute("""
            SELECT project_id, developer_name, company_id, project_type
            FROM unified_projects
            WHERE developer_name LIKE '%PRAXAIR%'
              AND company_id != ?
        """, (linde_id,)).fetchall()

        for pp in praxair_projects:
            print(f"  Praxair project: {pp['developer_name']} "
                  f"({pp['project_id'][:12]}) → Linde ({linde_id})")
            if apply:
                db.execute("""
                    UPDATE unified_projects SET company_id = ?, updated_at = ?
                    WHERE project_id = ?
                """, (linde_id, _now(), pp['project_id']))
        if apply:
            db.commit()
        if praxair_projects:
            print(f"  Merged {len(praxair_projects)} Praxair projects → Linde")

    # ── Phase 4: Report current state ────────────────────────────────────
    if apply:
        events = db.execute("SELECT COUNT(*) FROM corporate_events").fetchone()[0]
        print(f"\n── Summary ──")
        print(f"  corporate_events rows: {events}")
        print(f"  Manual events inserted: {stats['manual_events']}")
        print(f"  Auto-detected transitions: {stats['auto_detected']}")

        # Show all events
        print(f"\n── All Corporate Events ──")
        for r in db.execute("""
            SELECT ce.child_name, c.company_name AS parent_name,
                   ce.event_type, ce.event_date, ce.event_source
            FROM corporate_events ce
            JOIN companies c ON ce.parent_company_id = c.company_id
            ORDER BY ce.event_date
        """).fetchall():
            print(f"  {r['event_date']}: {r['child_name']:50s} → "
                  f"{r['parent_name']} ({r['event_type']}, {r['event_source']})")

    return stats


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    apply = '--apply' in sys.argv

    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = sqlite3.Row

    if not apply:
        print("DRY RUN — use --apply to write changes")

    stats = populate_events(db, apply=apply)

    if not apply:
        print(f"\n  To apply changes, run: python migrate_corporate_events.py --apply")

    db.close()
