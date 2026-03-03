#!/usr/bin/env python3
"""
crossref.py — Facility Cross-Reference Pipeline Step
=====================================================
Links projects that represent the same physical facility across
different regulatory databases (EPA ECHO, GHGRP, TCEQ, RRC UIC, PHMSA).

Creates soft links in facility_crossref — never merges projects.
Injects cross-referenced evidence into the signal accumulator to
promote single-source projects to multi-source confidence.

Pipeline position: collect → step1 → step3 → connect.py → crossref → bootstrap

Usage:
    python crossref.py                   # full run (all phases)
    python crossref.py --dry-run         # show what would be linked
    python crossref.py --stats           # current cross-reference state
    python crossref.py --phase=1         # FRS ID matching only
    python crossref.py --phase=2         # TCEQ-EPA name matching only
    python crossref.py --phase=3         # spatial proximity only
    python crossref.py --phase=4         # PHMSA operator matching only
    python crossref.py --phase=5         # signal accumulator update only
    python crossref.py --phase=6         # update counts + print stats
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ── Imports from connect.py (same directory) ─────────────────────────────────
from connect import (
    normalize,
    word_overlap,
    now_iso,
    accumulate_signal,
    get_config,
    DB_PATH,
)

# ── CLI flags ────────────────────────────────────────────────────────────────
DRY_RUN    = '--dry-run' in sys.argv
STATS_ONLY = '--stats'   in sys.argv
_PHASE = 0
for _a in sys.argv:
    if _a.startswith('--phase='):
        _PHASE = int(_a.split('=', 1)[1])


# ── Schema migration ────────────────────────────────────────────────────────
def migrate(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS facility_crossref (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            source_a_system       TEXT NOT NULL,
            source_a_id           TEXT NOT NULL,
            source_b_system       TEXT NOT NULL,
            source_b_id           TEXT NOT NULL,
            match_method          TEXT NOT NULL,
            match_detail          TEXT,
            confidence            REAL NOT NULL,
            project_a_id          TEXT,
            project_b_id          TEXT,
            created_at            TEXT NOT NULL,
            applied_to_accumulator INTEGER DEFAULT 0,
            UNIQUE(source_a_system, source_a_id, source_b_system, source_b_id)
        );
        CREATE INDEX IF NOT EXISTS idx_fc_project_a ON facility_crossref(project_a_id);
        CREATE INDEX IF NOT EXISTS idx_fc_project_b ON facility_crossref(project_b_id);
        CREATE INDEX IF NOT EXISTS idx_fc_method    ON facility_crossref(match_method);
    """)
    try:
        db.execute("ALTER TABLE unified_projects ADD COLUMN crossref_source_count INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    db.commit()


# ── Helpers ──────────────────────────────────────────────────────────────────
def _find_project_for_source(db, source_system: str, id_field: str,
                             id_value: str) -> str | None:
    """Find project_id linked to a regulatory_evidence record via claims."""
    row = db.execute("""
        SELECT pc.project_id
        FROM regulatory_evidence re
        JOIN claims c ON c.source_id = re.id
        JOIN project_claims pc ON pc.claim_id = c.claim_id
        WHERE re.source_system = ? AND re.{} = ?
          AND pc.resolution_method <> 'unresolved'
        LIMIT 1
    """.format(id_field), (source_system, id_value)).fetchone()
    return row[0] if row else None


def _find_project_for_epa_registry(db, registry_id: str) -> str | None:
    """Find project linked to an EPA FRS registry_id."""
    # Via regulatory_evidence (epa_echo stores registry_id in company_cik)
    row = db.execute("""
        SELECT pc.project_id
        FROM regulatory_evidence re
        JOIN claims c ON c.source_id = re.id
        JOIN project_claims pc ON pc.claim_id = c.claim_id
        WHERE re.source_system = 'epa_echo' AND re.company_cik = ?
          AND pc.resolution_method <> 'unresolved'
        LIMIT 1
    """, (registry_id,)).fetchone()
    if row:
        return row[0]
    # Fallback: unified_projects.source_registry_ids JSON (include quarantined
    # so E5 single_source projects can still get crossref links for recovery)
    row = db.execute("""
        SELECT up.project_id
        FROM unified_projects up, json_each(COALESCE(up.source_registry_ids, '[]')) je
        WHERE je.value = ?
        LIMIT 1
    """, (registry_id,)).fetchone()
    return row[0] if row else None


def _find_project_for_tceq(db, rn_number: str) -> str | None:
    """Find project linked to a TCEQ RN number."""
    row = db.execute("""
        SELECT pc.project_id
        FROM regulatory_evidence re
        JOIN claims c ON c.source_id = re.id
        JOIN project_claims pc ON pc.claim_id = c.claim_id
        WHERE re.source_system = 'tceq' AND re.facility_id = ?
          AND pc.resolution_method <> 'unresolved'
        LIMIT 1
    """, (rn_number,)).fetchone()
    return row[0] if row else None


def _find_project_for_rrc_uic(db, uic_number: str) -> str | None:
    """Find project linked to an RRC UIC well."""
    row = db.execute("""
        SELECT pc.project_id
        FROM regulatory_evidence re
        JOIN claims c ON c.source_id = re.id
        JOIN project_claims pc ON pc.claim_id = c.claim_id
        WHERE re.source_system = 'rrc_uic'
          AND re.document_url = ?
          AND pc.resolution_method <> 'unresolved'
        LIMIT 1
    """, (f'rrc_uic://well_{uic_number}',)).fetchone()
    return row[0] if row else None


def _insert_crossref(db, source_a_system, source_a_id, source_b_system,
                     source_b_id, match_method, match_detail, confidence,
                     project_a_id, project_b_id) -> bool:
    """Insert a cross-reference. Returns True if a new row was created."""
    if DRY_RUN:
        pa = project_a_id or '?'
        pb = project_b_id or '?'
        print(f"    [dry] {source_a_system}:{source_a_id} ↔ "
              f"{source_b_system}:{source_b_id}  "
              f"conf={confidence:.2f}  projects={pa}↔{pb}")
        return True
    db.execute("""
        INSERT OR IGNORE INTO facility_crossref
        (source_a_system, source_a_id, source_b_system, source_b_id,
         match_method, match_detail, confidence,
         project_a_id, project_b_id, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (source_a_system, source_a_id, source_b_system, source_b_id,
          match_method, json.dumps(match_detail) if match_detail else None,
          confidence, project_a_id, project_b_id, now_iso()))
    return db.execute("SELECT changes()").fetchone()[0] > 0


def _parse_epa_county(facility_city: str) -> str | None:
    """Extract county from EPA facility_city: 'PASADENA, HARRIS County, TX' → 'HARRIS'.
    Also handles 'HARRIS COUNTY County, TX' → 'HARRIS'."""
    if not facility_city or ',' not in facility_city:
        return None
    parts = facility_city.split(',')
    for part in parts[1:]:
        p = part.strip()
        m = re.match(r'^(.+?)\s+County', p, re.IGNORECASE)
        if m:
            result = m.group(1).strip().upper()
            # Strip trailing "COUNTY" from cases like "HARRIS COUNTY County"
            if result.endswith(' COUNTY'):
                result = result[:-7].strip()
            return result
    return None


def _parse_epa_city(facility_city: str) -> str | None:
    """Extract city from EPA facility_city: 'PASADENA, HARRIS County, TX' → 'PASADENA'."""
    if not facility_city or ',' not in facility_city:
        return None
    return facility_city.split(',', 1)[0].strip().upper()


def _haversine_miles(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in miles."""
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(min(1.0, a)))


# ── Phase 1: FRS ID matching (GHGRP ↔ ECHO) ─────────────────────────────────
def phase_1_frs_id(db) -> int:
    """Link GHGRP and ECHO records by shared EPA FRS Registry ID."""
    # GHGRP stores FRS ID in permit_id, ECHO stores it in company_cik
    ghgrp_frs = {}
    for row in db.execute("""
        SELECT DISTINCT permit_id, facility_id, company_name
        FROM regulatory_evidence
        WHERE source_system = 'epa_ghgrp'
          AND permit_id IS NOT NULL AND permit_id <> ''
    """).fetchall():
        ghgrp_frs[row[0]] = {'ghgrp_fid': row[1], 'name': row[2]}

    echo_frs = {}
    for row in db.execute("""
        SELECT DISTINCT company_cik, company_name
        FROM regulatory_evidence
        WHERE source_system = 'epa_echo'
          AND company_cik IS NOT NULL AND company_cik <> ''
    """).fetchall():
        echo_frs[row[0]] = {'name': row[1]}

    inserted = 0
    for frs_id, g in ghgrp_frs.items():
        if frs_id not in echo_frs:
            continue
        e = echo_frs[frs_id]

        project_a = _find_project_for_source(db, 'epa_ghgrp', 'permit_id', frs_id)
        project_b = _find_project_for_epa_registry(db, frs_id)

        detail = {'frs_id': frs_id, 'ghgrp_name': g['name'], 'echo_name': e['name']}
        if _insert_crossref(db, 'epa_ghgrp', frs_id, 'epa_echo', frs_id,
                            'frs_id', detail, 0.99, project_a, project_b):
            inserted += 1

    if not DRY_RUN:
        db.commit()
    return inserted


# ── Phase 1.5: FRS crosswalk (TCEQ → EPA deterministic) ──────────────────────
def phase_1_5_frs_tceq_epa(db) -> int:
    """Link TCEQ and EPA records via FRS crosswalk (deterministic, conf 0.99)."""
    # Check if frs_crosswalk table exists
    has_frs = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='frs_crosswalk'"
    ).fetchone()
    if not has_frs:
        print("    frs_crosswalk table not found — run collect_frs_crosswalk.py first")
        return 0

    # Find TCEQ RNs that map to a registry_id present in our EPA data
    matches = db.execute("""
        SELECT fc.pgm_sys_id AS rn_number,
               fc.registry_id,
               fc.primary_name
        FROM frs_crosswalk fc
        WHERE fc.pgm_sys_acrnm = 'TX-TCEQ ACR'
          AND fc.registry_id IN (
              SELECT DISTINCT company_cik FROM regulatory_evidence
              WHERE source_system = 'epa_echo'
                AND company_cik IS NOT NULL AND company_cik <> ''
              UNION
              SELECT DISTINCT registry_id FROM epa_permits
              WHERE registry_id IS NOT NULL AND registry_id <> ''
          )
    """).fetchall()

    inserted = 0
    for m in matches:
        rn = m[0]       # e.g., 'RN102056108'
        reg_id = m[1]   # e.g., '110034478740'
        frs_name = m[2]

        project_a = _find_project_for_tceq(db, rn)
        project_b = _find_project_for_epa_registry(db, reg_id)

        detail = json.dumps({
            'rn_number': rn, 'frs_registry_id': reg_id, 'frs_name': frs_name,
        })
        if _insert_crossref(db, 'tceq', rn, 'epa_echo', reg_id,
                            'frs_crosswalk', detail, 0.99, project_a, project_b):
            inserted += 1

    if not DRY_RUN:
        db.commit()
    return inserted


# ── Phase 2: TCEQ → EPA name+county matching ─────────────────────────────────
def phase_2_tceq_epa(db) -> int:
    """Match TCEQ entities to EPA facilities by normalized name + county."""
    # Build EPA lookup by county (keep all rows — pair dedup handled by `seen` set)
    epa_by_county: dict[str, list] = defaultdict(list)
    seen_epa_entry = set()
    for row in db.execute("""
        SELECT registry_id, facility_name, facility_city
        FROM epa_permits WHERE facility_name IS NOT NULL
    """).fetchall():
        entry_key = (row[0], row[1])  # dedup by (registry_id, facility_name)
        if entry_key in seen_epa_entry:
            continue
        county = _parse_epa_county(row[2])
        if county:
            seen_epa_entry.add(entry_key)
            epa_by_county[county].append({
                'registry_id': row[0],
                'name': row[1],
                'name_norm': normalize(row[1]),
                'city': _parse_epa_city(row[2]),
            })

    # Load distinct TCEQ entities (one per RN)
    tceq_entities = db.execute("""
        SELECT rn_number, entity_name, city, county
        FROM tceq_permits
        WHERE entity_name IS NOT NULL AND county IS NOT NULL
        GROUP BY rn_number
    """).fetchall()

    inserted = 0
    seen = set()  # avoid duplicate rn+registry pairs
    for te in tceq_entities:
        county = te[3].upper()
        if county not in epa_by_county:
            continue

        te_norm = normalize(te[1])
        if len(te_norm) < 4:
            continue

        for epa in epa_by_county[county]:
            pair = (te[0], epa['registry_id'])
            if pair in seen:
                continue

            overlap = word_overlap(te_norm, epa['name_norm'])
            if overlap < 0.65:
                continue
            seen.add(pair)

            city_match = False
            if te[2] and epa['city']:
                city_match = te[2].upper() == epa['city']

            confidence = min(0.95, 0.60 + overlap * 0.35 + (0.10 if city_match else 0))

            project_a = _find_project_for_tceq(db, te[0])
            project_b = _find_project_for_epa_registry(db, epa['registry_id'])

            detail = {
                'tceq_name': te[1], 'epa_name': epa['name'],
                'county': county, 'word_overlap': round(overlap, 3),
                'city_match': city_match,
            }
            if _insert_crossref(db, 'tceq', te[0], 'epa_echo', epa['registry_id'],
                                'name_county', detail, confidence, project_a, project_b):
                inserted += 1

    if not DRY_RUN:
        db.commit()
    return inserted


# ── Phase 3: RRC UIC spatial proximity ────────────────────────────────────────

# Approximate city center coordinates for Texas cities in EPA permits
TX_CITY_COORDS: dict[str, tuple[float, float]] = {
    'ABILENE': (32.4487, -99.7331), 'ALVIN': (29.4238, -95.2441),
    'AMARILLO': (35.2220, -101.8313), 'BAY CITY': (28.9828, -95.9694),
    'BAYTOWN': (29.7355, -94.9774), 'BEAUMONT': (30.0802, -94.1266),
    'BLOOMINGTON': (28.6475, -96.8941), 'BORGER': (35.6670, -101.3971),
    'BRIDGEPORT': (33.2101, -97.7547), 'CHANNELVIEW': (29.7757, -95.1147),
    'CORPUS CHRISTI': (27.8006, -97.3964), 'DALLAS': (32.7767, -96.7970),
    'FREEPORT': (28.9541, -95.3597), 'GRAND PRAIRIE': (32.7459, -96.9978),
    'GREGORY': (27.9214, -97.2889), 'GRUVER': (36.2653, -101.4065),
    'HAWKINS': (32.5885, -95.2041), 'HOUSTON': (29.7604, -95.3698),
    'INGLESIDE': (27.8775, -97.2114), 'JEWETT': (31.3616, -96.1469),
    'LA PORTE': (29.6658, -95.0194), 'LONGVIEW': (32.5007, -94.7405),
    'MAGNOLIA': (30.2091, -95.7508), 'MERKEL': (32.4709, -100.0126),
    'MIDLOTHIAN': (32.4824, -96.9945), 'NEDERLAND': (29.9744, -94.0002),
    'NOTREES': (31.9270, -102.7579), 'ODESSA': (31.8457, -102.3676),
    'PASADENA': (29.6911, -95.2091), 'PORT ARTHUR': (29.8850, -93.9400),
    'PORT NECHES': (29.9913, -93.9588), 'RHOME': (33.0537, -97.4717),
    'SAN ANTONIO': (29.4241, -98.4936), 'TEXARKANA': (33.4418, -94.0477),
    'TEXAS CITY': (29.3838, -94.9027),
}

def phase_3_spatial(db) -> int:
    """Match RRC UIC wells to nearby EPA facilities by lat/lon proximity."""
    MAX_DIST = 5.0  # miles

    # Build EPA facility list — one entry per registry_id (dedup permits)
    epa_facilities = []
    seen_registry = set()
    for row in db.execute("""
        SELECT registry_id, facility_name, facility_city
        FROM epa_permits WHERE facility_city IS NOT NULL
        ORDER BY registry_id
    """).fetchall():
        if row[0] in seen_registry:
            continue
        city = _parse_epa_city(row[2])
        if city and city in TX_CITY_COORDS:
            seen_registry.add(row[0])
            lat, lon = TX_CITY_COORDS[city]
            epa_facilities.append({
                'registry_id': row[0], 'name': row[1],
                'lat': lat, 'lon': lon, 'city': city,
            })

    if not epa_facilities:
        print("    No EPA facilities with known coordinates — skipping spatial")
        return 0

    # Load all wells with coordinates
    wells = db.execute("""
        SELECT uic_number, operator_name, latitude, longitude
        FROM rrc_uic_wells
        WHERE latitude IS NOT NULL AND longitude IS NOT NULL
    """).fetchall()

    inserted = 0
    for well in wells:
        wlat, wlon = well[2], well[3]
        for epa in epa_facilities:
            dist = _haversine_miles(wlat, wlon, epa['lat'], epa['lon'])
            if dist > MAX_DIST:
                continue

            op_norm = normalize(well[1] or '')
            epa_norm = normalize(epa['name'])
            name_overlap = word_overlap(op_norm, epa_norm) if op_norm and epa_norm else 0

            dist_conf = max(0.50, 0.85 - (dist / MAX_DIST) * 0.35)
            name_boost = min(0.15, name_overlap * 0.20)
            confidence = min(0.95, dist_conf + name_boost)

            project_a = _find_project_for_rrc_uic(db, well[0])
            project_b = _find_project_for_epa_registry(db, epa['registry_id'])

            detail = {
                'distance_miles': round(dist, 2),
                'well_operator': well[1],
                'epa_facility': epa['name'],
                'name_overlap': round(name_overlap, 3),
            }
            if _insert_crossref(db, 'rrc_uic', well[0], 'epa_echo', epa['registry_id'],
                                'spatial', detail, confidence, project_a, project_b):
                inserted += 1

    if not DRY_RUN:
        db.commit()
    return inserted


# ── Phase 4: PHMSA operator → company matching ───────────────────────────────
def phase_4_phmsa(db) -> int:
    """Match PHMSA pipeline operators to unified_projects by normalized name."""
    operators = db.execute("""
        SELECT DISTINCT operator_id, operator_name
        FROM pipeline_infrastructure
        WHERE commodity IN ('CO2', 'CARBON DIOXIDE', 'co2')
    """).fetchall()

    # Build normalized project lookup
    projects = db.execute("""
        SELECT project_id, developer_name
        FROM unified_projects WHERE quarantined = 0 AND developer_name IS NOT NULL
    """).fetchall()

    proj_by_norm: dict[str, list] = defaultdict(list)
    for p in projects:
        pn = normalize(p[1])
        if pn and len(pn) > 3:
            proj_by_norm[pn].append({'project_id': p[0], 'name': p[1]})

    inserted = 0
    for op in operators:
        op_norm = normalize(op[1] or '')
        if not op_norm or len(op_norm) < 4:
            continue

        matched = proj_by_norm.get(op_norm, [])

        # Try substring containment
        if not matched:
            for pn, plist in proj_by_norm.items():
                if op_norm in pn or pn in op_norm:
                    matched = plist
                    break

        # Try word overlap
        if not matched:
            best_score, best = 0.0, []
            for pn, plist in proj_by_norm.items():
                score = word_overlap(op_norm, pn)
                if score > best_score and score >= 0.60:
                    best_score, best = score, plist
            matched = best

        for mp in matched:
            detail = {'phmsa_operator': op[1], 'project_developer': mp['name']}
            if _insert_crossref(db, 'phmsa', op[0], 'unified_projects',
                                mp['project_id'], 'operator_name', detail,
                                0.85, None, mp['project_id']):
                inserted += 1

    if not DRY_RUN:
        db.commit()
    return inserted


# ── Phase 5: Signal accumulator update ────────────────────────────────────────
def phase_5_accumulator(db) -> int:
    """Inject cross-referenced claims into signal accumulator for both sides."""
    crossrefs = db.execute("""
        SELECT id, source_a_system, source_a_id, source_b_system, source_b_id,
               match_method, confidence, project_a_id, project_b_id
        FROM facility_crossref
        WHERE applied_to_accumulator = 0
          AND project_a_id IS NOT NULL
          AND project_b_id IS NOT NULL
          AND project_a_id <> project_b_id
    """).fetchall()

    if not crossrefs:
        print("    No unapplied cross-references with distinct project pairs")
        return 0

    updated = 0
    for xref in crossrefs:
        xref_id = xref[0]
        project_a, project_b = xref[7], xref[8]
        method = xref[5]

        # Get claims from project_b → inject into project_a's accumulator
        b_claims = db.execute("""
            SELECT c.claim_id, c.claim_type, c.claim_text, c.document_type,
                   c.document_date, c.document_url, c.confidence
            FROM project_claims pc
            JOIN claims c ON c.claim_id = pc.claim_id
            WHERE pc.project_id = ? AND pc.resolution_method <> 'unresolved'
        """, (project_b,)).fetchall()

        resolution_cite = {
            'method': 'crossref', 'crossref_id': xref_id,
            'match_method': method, 'source_project': project_b,
        }
        for claim in b_claims:
            accumulate_signal(project_a, claim, '', resolution_cite, db, None)

        # Reverse: project_a claims → project_b accumulator
        a_claims = db.execute("""
            SELECT c.claim_id, c.claim_type, c.claim_text, c.document_type,
                   c.document_date, c.document_url, c.confidence
            FROM project_claims pc
            JOIN claims c ON c.claim_id = pc.claim_id
            WHERE pc.project_id = ? AND pc.resolution_method <> 'unresolved'
        """, (project_a,)).fetchall()

        rev_cite = {
            'method': 'crossref', 'crossref_id': xref_id,
            'match_method': method, 'source_project': project_a,
        }
        for claim in a_claims:
            accumulate_signal(project_b, claim, '', rev_cite, db, None)

        # Mark applied
        db.execute("UPDATE facility_crossref SET applied_to_accumulator = 1 WHERE id = ?",
                   (xref_id,))

        # Audit log
        db.execute("""
            INSERT INTO citation_audit_log
            (event_type, project_id, assertion, new_value,
             citation_json, confidence, triggered_by, created_at)
            VALUES ('crossref_applied', ?, ?, ?, ?, ?, 'crossref', ?)
        """, (project_a, f'crossref:{method}', project_b,
              json.dumps({'a→b_claims': len(a_claims), 'b→a_claims': len(b_claims),
                          'match_method': method}),
              xref[6], now_iso()))

        updated += 1
        if updated % 10 == 0:
            db.commit()

    db.commit()
    return updated


# ── Phase 6: Update source counts ────────────────────────────────────────────
def phase_6_counts(db) -> None:
    """Update unified_projects.crossref_source_count."""
    db.execute("""
        UPDATE unified_projects
        SET crossref_source_count = (
            SELECT COUNT(DISTINCT
                CASE WHEN project_a_id = unified_projects.project_id
                     THEN source_b_system ELSE source_a_system END
            )
            FROM facility_crossref
            WHERE (project_a_id = unified_projects.project_id
                   OR project_b_id = unified_projects.project_id)
              AND applied_to_accumulator = 1
        )
    """)
    db.commit()


# ── Phase 7: Un-quarantine recovery ───────────────────────────────────────────
def phase_7_unquarantine(db) -> int:
    """Un-quarantine E5 (single_source) projects that now have multi-source evidence.

    Checks quarantined projects whose quarantine_reason starts with 'E5:'.
    If the project now has claims from 2+ source systems OR has crossref links
    (crossref_source_count > 0), it gets un-quarantined.
    """
    candidates = db.execute("""
        SELECT up.project_id, up.project_name, up.quarantine_reason,
               COALESCE(up.crossref_source_count, 0) AS xref_count,
               COALESCE(src.sys_count, 0) AS direct_systems
        FROM unified_projects up
        LEFT JOIN (
            SELECT pc.project_id, COUNT(DISTINCT cd.source_system) AS sys_count
            FROM project_claims pc
            JOIN claims c ON pc.claim_id = c.claim_id
            JOIN cleaned_documents cd ON c.source_id = cd.source_id
            GROUP BY pc.project_id
        ) src ON src.project_id = up.project_id
        WHERE up.quarantined = 1
          AND up.quarantine_reason LIKE 'E5:%'
          AND (
              COALESCE(src.sys_count, 0) >= 2
              OR COALESCE(up.crossref_source_count, 0) > 0
          )
    """).fetchall()

    promoted = 0
    for r in candidates:
        if DRY_RUN:
            print(f"    [dry] UN-QUARANTINE: {r['project_name'][:55]}  "
                  f"(direct={r['direct_systems']}, xref={r['xref_count']})")
        else:
            db.execute("""
                UPDATE unified_projects
                SET quarantined = 0, quarantine_reason = NULL, updated_at = ?
                WHERE project_id = ?
            """, (now_iso(), r['project_id']))
            print(f"    UN-QUARANTINE: {r['project_name'][:55]}  "
                  f"(direct={r['direct_systems']}, xref={r['xref_count']})")
        promoted += 1

    if not DRY_RUN:
        db.commit()
    return promoted


# ── Stats ────────────────────────────────────────────────────────────────────
def print_stats(db) -> None:
    print("\n" + "=" * 60)
    print("CROSSREF — STATISTICS")
    print("=" * 60)

    total = db.execute("SELECT COUNT(*) FROM facility_crossref").fetchone()[0]
    print(f"\n  Total cross-references: {total}")

    if total == 0:
        return

    print("\n  By match method:")
    for r in db.execute("""
        SELECT match_method, COUNT(*) n, ROUND(AVG(confidence), 3) avg_conf,
               SUM(CASE WHEN project_a_id IS NOT NULL AND project_b_id IS NOT NULL
                        AND project_a_id <> project_b_id THEN 1 ELSE 0 END) as diff_projects
        FROM facility_crossref GROUP BY match_method ORDER BY n DESC
    """).fetchall():
        print(f"    {r[0]:<20} {r[1]:>5}  avg_conf={r[2]}  "
              f"diff_project_pairs={r[3]}")

    applied = db.execute(
        "SELECT COUNT(*) FROM facility_crossref WHERE applied_to_accumulator = 1"
    ).fetchone()[0]
    pending = total - applied
    print(f"\n  Applied to accumulator: {applied}")
    print(f"  Pending:                {pending}")

    # Source diversity before/after
    single_before = db.execute("""
        SELECT COUNT(*) FROM unified_projects
        WHERE quarantined = 0 AND crossref_source_count = 0
    """).fetchone()[0]
    promoted = db.execute("""
        SELECT COUNT(*) FROM unified_projects
        WHERE quarantined = 0 AND crossref_source_count > 0
    """).fetchone()[0]
    print(f"\n  PROMOTION IMPACT:")
    print(f"    Single-source (no crossref): {single_before}")
    print(f"    Promoted (has crossref):     {promoted}")

    if promoted:
        print(f"\n  Top promoted projects:")
        for r in db.execute("""
            SELECT project_name, crossref_source_count
            FROM unified_projects
            WHERE quarantined = 0 AND crossref_source_count > 0
            ORDER BY crossref_source_count DESC LIMIT 10
        """).fetchall():
            print(f"    +{r[1]} sources  {r[0][:60]}")


# ── Main ─────────────────────────────────────────────────────────────────────
def run():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    prefix = "DRY RUN — " if DRY_RUN else ""
    print(f"{prefix}Cross-Reference Pipeline")
    print(f"  Database: {DB_PATH}")

    migrate(db)

    if STATS_ONLY:
        print_stats(db)
        db.close()
        return

    results = {}

    if _PHASE in (0, 1):
        print("\n── Phase 1: FRS ID Matching (GHGRP ↔ ECHO) ──────────────")
        results['frs_id'] = phase_1_frs_id(db)
        print(f"  Cross-references created: {results['frs_id']}")

    if _PHASE in (0, 15):
        print("\n── Phase 1.5: FRS Crosswalk (TCEQ → EPA) ────────────────")
        results['frs_crosswalk'] = phase_1_5_frs_tceq_epa(db)
        print(f"  Cross-references created: {results['frs_crosswalk']}")

    if _PHASE in (0, 2):
        print("\n── Phase 2: TCEQ → EPA Name+County ──────────────────────")
        results['name_county'] = phase_2_tceq_epa(db)
        print(f"  Cross-references created: {results['name_county']}")

    if _PHASE in (0, 3):
        print("\n── Phase 3: RRC UIC Spatial Proximity ───────────────────")
        results['spatial'] = phase_3_spatial(db)
        print(f"  Cross-references created: {results['spatial']}")

    if _PHASE in (0, 4):
        print("\n── Phase 4: PHMSA Operator Matching ─────────────────────")
        results['phmsa'] = phase_4_phmsa(db)
        print(f"  Cross-references created: {results['phmsa']}")

    if _PHASE in (0, 5) and not DRY_RUN:
        print("\n── Phase 5: Signal Accumulator Update ───────────────────")
        results['accumulator'] = phase_5_accumulator(db)
        print(f"  Crossrefs applied: {results['accumulator']}")

    if _PHASE in (0, 6) and not DRY_RUN:
        print("\n── Phase 6: Source Count Update ─────────────────────────")
        phase_6_counts(db)

    if _PHASE in (0, 7):
        print("\n── Phase 7: Un-quarantine Check ─────────────────────────")
        unq = phase_7_unquarantine(db)
        print(f"  Projects un-quarantined: {unq}")

    print_stats(db)
    db.close()


if __name__ == '__main__':
    run()
