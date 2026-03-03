"""
Scraper 4D: Cross-TSP Synthesis & Alerting
=============================================
Combines data from all 4A/4B/4C sources into:

1. Unified TSP Queue Time Series — quarterly snapshots of queue size,
   load mix, and growth rates across Oncor/CenterPoint/AEP
2. EIA 860M Delta Detection — new planned generators vs previous month
3. Industrial Load Tracker — filters out data center noise to isolate
   the industrial/H2/ammonia signal from queue growth
4. Alert Generation — flags for DecarbIQ when:
   - New gas generator planned in Gulf Coast county (potential H2 power supply)
   - Industrial GW growth rate exceeds threshold
   - New developer match detected in EIA filings
   - TSP collateral deposits spike (leading indicator of committed load)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import (
    STORAGE_DIR, OUTPUTS_DIR, ScrapeResult,
    GULF_COAST_COUNTIES, KNOWN_DEVELOPERS,
)

TIMESERIES_FILE = OUTPUTS_DIR / "unified_queue_timeseries.json"
ALERTS_FILE = OUTPUTS_DIR / "queue_alerts.json"
DELTA_FILE = OUTPUTS_DIR / "eia860m_deltas.json"


def build_unified_timeseries(all_results: list[ScrapeResult]) -> dict:
    """
    Build a unified time series from all TSP earnings extractions.
    Groups by TSP and quarter, tracks key metrics over time.
    """
    print("[4D.1] Building unified queue time series...")

    # Load existing time series
    existing = {}
    if TIMESERIES_FILE.exists():
        try:
            existing = json.loads(TIMESERIES_FILE.read_text())
        except json.JSONDecodeError:
            pass

    # Extract TSP earnings data
    for r in all_results:
        if r.doc_type != "quarterly_earnings":
            continue
        tsp = r.metadata.get("tsp", "unknown")
        quarter = r.metadata.get("quarter", "")
        metrics = r.metadata.get("extracted_metrics", {})
        if not quarter or not metrics:
            continue

        if tsp not in existing:
            existing[tsp] = {}
        existing[tsp][quarter] = {
            "metrics": metrics,
            "date": r.date,
            "url": r.url,
        }

    # Save
    TIMESERIES_FILE.write_text(json.dumps(existing, indent=2, default=str))
    total_points = sum(len(v) for v in existing.values())
    print(f"  [OK] Time series: {len(existing)} TSPs, {total_points} data points")
    return existing


def compute_eia860m_deltas() -> list[dict]:
    """
    Compare current EIA-860M Texas planned generators against
    previous snapshot to detect new entries and changes.
    """
    print("[4D.2] Computing EIA-860M deltas...")
    deltas = []

    current_file = OUTPUTS_DIR / "eia860m_texas_planned.csv"
    previous_file = STORAGE_DIR / "snapshots" / "eia860m_texas_planned_prev.csv"

    if not current_file.exists():
        print("  [SKIP] No current EIA-860M data")
        return deltas

    current = pd.read_csv(current_file)

    if previous_file.exists():
        previous = pd.read_csv(previous_file)

        # Find plant ID column
        id_col = None
        for col in current.columns:
            if "plant id" in col.lower() or "generator id" in col.lower():
                id_col = col
                break

        if id_col:
            current_ids = set(current[id_col].astype(str))
            previous_ids = set(previous[id_col].astype(str))

            new_ids = current_ids - previous_ids
            removed_ids = previous_ids - current_ids

            if new_ids:
                new_generators = current[current[id_col].astype(str).isin(new_ids)]
                deltas.append({
                    "type": "new_planned_generators",
                    "count": len(new_ids),
                    "generators": new_generators.to_dict(orient="records"),
                    "date": datetime.now(timezone.utc).isoformat(),
                })
                print(f"  NEW planned generators: {len(new_ids)}")

            if removed_ids:
                deltas.append({
                    "type": "removed_from_planned",
                    "count": len(removed_ids),
                    "ids": list(removed_ids)[:50],
                    "date": datetime.now(timezone.utc).isoformat(),
                })
                print(f"  REMOVED from planned: {len(removed_ids)}")
        else:
            # Fall back to row count comparison
            delta_count = len(current) - len(previous)
            deltas.append({
                "type": "row_count_change",
                "current": len(current),
                "previous": len(previous),
                "delta": delta_count,
                "date": datetime.now(timezone.utc).isoformat(),
            })
            print(f"  Row count delta: {delta_count:+d}")
    else:
        print("  [INFO] No previous snapshot — establishing baseline")

    # Save current as next run's previous
    snapshot_dir = STORAGE_DIR / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    current.to_csv(previous_file, index=False)

    if deltas:
        DELTA_FILE.write_text(json.dumps(deltas, indent=2, default=str))

    return deltas


def generate_alerts(all_results: list[ScrapeResult], deltas: list[dict]) -> list[dict]:
    """
    Generate DecarbIQ alerts based on pipeline findings.
    
    Alert types:
    - NEW_GAS_GENERATOR: Gas turbine planned in Gulf Coast county
    - DEVELOPER_MATCH: Known H2/ammonia developer in EIA filings
    - INDUSTRIAL_GROWTH: Industrial GW increase in TSP queue
    - COLLATERAL_SPIKE: Customer collateral deposits increased
    - QUEUE_MILESTONE: Queue crossed round-number threshold
    """
    print("[4D.3] Generating alerts...")
    alerts = []

    # Alert 1: New gas generators in Gulf Coast from EIA deltas
    for delta in deltas:
        if delta.get("type") == "new_planned_generators":
            for gen in delta.get("generators", []):
                # Check if gas in Gulf Coast
                county = str(gen.get("County", gen.get("Plant County", ""))).strip()
                tech = str(gen.get("Technology", gen.get("Energy Source Code", ""))).lower()

                is_gas = any(kw in tech for kw in ["gas", "combustion", "combined cycle", "ng"])
                is_gulf = county in GULF_COAST_COUNTIES

                if is_gas and is_gulf:
                    alerts.append({
                        "type": "NEW_GAS_GENERATOR",
                        "severity": "HIGH",
                        "title": f"New gas generator planned in {county} County",
                        "detail": gen,
                        "signal": "Potential power supply for H2/ammonia facility",
                        "date": datetime.now(timezone.utc).isoformat(),
                    })

                # Check developer match
                entity = str(gen.get("Entity Name", gen.get("Plant Name", ""))).strip()
                for dev in KNOWN_DEVELOPERS:
                    if dev.lower() in entity.lower():
                        alerts.append({
                            "type": "DEVELOPER_MATCH",
                            "severity": "HIGH",
                            "title": f"Known developer {dev} filed new generator",
                            "detail": gen,
                            "signal": "Direct H2/ammonia developer activity in EIA filings",
                            "date": datetime.now(timezone.utc).isoformat(),
                        })

    # Alert 2: Industrial GW growth from TSP earnings
    for r in all_results:
        if r.doc_type != "quarterly_earnings":
            continue
        metrics = r.metadata.get("extracted_metrics", {})
        tsp = r.metadata.get("tsp", "")

        # Industrial GW
        ind_gw = metrics.get("industrial_gw", 0)
        if ind_gw and ind_gw > 15:  # Significant industrial queue
            alerts.append({
                "type": "INDUSTRIAL_GROWTH",
                "severity": "MEDIUM",
                "title": f"{tsp.title()}: {ind_gw} GW industrial load in queue",
                "detail": metrics,
                "signal": "Industrial load includes H2/ammonia/CCS facilities",
                "date": datetime.now(timezone.utc).isoformat(),
            })

        # Collateral
        collateral = metrics.get("customer_collateral_billions", 0)
        if collateral and collateral > 2.0:
            alerts.append({
                "type": "COLLATERAL_SPIKE",
                "severity": "MEDIUM",
                "title": f"{tsp.title()}: ${collateral}B in customer collateral",
                "detail": metrics,
                "signal": "High collateral = committed projects moving forward",
                "date": datetime.now(timezone.utc).isoformat(),
            })

        # Queue milestone
        total = metrics.get("lci_queue_requests", 0) or metrics.get("total_queue_gw", 0)
        if total and total > 200:
            alerts.append({
                "type": "QUEUE_MILESTONE",
                "severity": "LOW",
                "title": f"{tsp.title()}: Queue at {total} GW / requests",
                "signal": "Queue growth tracking",
                "date": datetime.now(timezone.utc).isoformat(),
            })

    # Save alerts
    if alerts:
        ALERTS_FILE.write_text(json.dumps(alerts, indent=2, default=str))

    print(f"  [OK] Generated {len(alerts)} alerts ({sum(1 for a in alerts if a['severity']=='HIGH')} HIGH)")
    return alerts


def run_all_4d(all_results: list[ScrapeResult]) -> tuple[dict, list[dict], list[dict]]:
    """Run all synthesis and alerting."""
    timeseries = build_unified_timeseries(all_results)
    deltas = compute_eia860m_deltas()
    alerts = generate_alerts(all_results, deltas)
    return timeseries, deltas, alerts
