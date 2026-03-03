#!/usr/bin/env python3
"""
Pipeline 2: Distribution-Level Interconnections Tracker
=========================================================
Covers Section 4 of the ERCOT/Texas Data Collection Guide.

Modules:
  4A - TSP quarterly earnings (Oncor, CenterPoint, AEP queue data)
  4B - EIA Form 860/860M (planned generators ≥1 MW in Texas)
  4C - Interconnection.fyi (aggregated ERCOT queue data)
  4D - Cross-TSP synthesis, delta detection, alert generation

Usage:
  python run_pipeline.py                    # Run all modules
  python run_pipeline.py --module 4a        # Run specific module
  python run_pipeline.py --module 4b        # EIA data only
"""

import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUTS_DIR, ScrapeResult


def run_pipeline(modules=None):
    if modules is None:
        modules = ["4a", "4b", "4c", "4d"]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    print("=" * 70)
    print(f"Pipeline 2: Distribution Interconnections — {timestamp}")
    print("=" * 70)

    all_results: list[ScrapeResult] = []

    # ── 4A: TSP Quarterly Earnings / Queue Data ──────────────────────────
    if "4a" in modules:
        print(f"\n{'─' * 50}")
        print("MODULE 4A: TSP Quarterly Queue Data")
        print("─" * 50)
        try:
            from scrapers.scraper_4a_tsp_data import run_all_4a
            results_4a = run_all_4a()
            all_results.extend(results_4a)
            print(f"  → {len(results_4a)} items from 4A")
        except Exception as e:
            print(f"  [ERROR] Module 4A failed: {e}")
            import traceback; traceback.print_exc()

    # ── 4B: EIA Form 860/860M ────────────────────────────────────────────
    if "4b" in modules:
        print(f"\n{'─' * 50}")
        print("MODULE 4B: EIA Form 860/860M Planned Generators")
        print("─" * 50)
        try:
            from scrapers.scraper_4b_eia860 import run_all_4b
            results_4b = run_all_4b()
            all_results.extend(results_4b)
            print(f"  → {len(results_4b)} items from 4B")
        except Exception as e:
            print(f"  [ERROR] Module 4B failed: {e}")
            import traceback; traceback.print_exc()

    # ── 4C: Interconnection.fyi ──────────────────────────────────────────
    if "4c" in modules:
        print(f"\n{'─' * 50}")
        print("MODULE 4C: Interconnection.fyi Queue Data")
        print("─" * 50)
        try:
            from scrapers.scraper_4c_interconnection_fyi import run_all_4c
            results_4c = run_all_4c()
            all_results.extend(results_4c)
            print(f"  → {len(results_4c)} items from 4C")
        except Exception as e:
            print(f"  [ERROR] Module 4C failed: {e}")
            import traceback; traceback.print_exc()

    # ── 4D: Synthesis & Alerting ─────────────────────────────────────────
    if "4d" in modules:
        print(f"\n{'─' * 50}")
        print("MODULE 4D: Cross-TSP Synthesis & Alerting")
        print("─" * 50)
        try:
            from scrapers.scraper_4d_synthesis import run_all_4d
            timeseries, deltas, alerts = run_all_4d(all_results)
            print(f"  Time series: {sum(len(v) for v in timeseries.values())} data points")
            print(f"  EIA deltas: {len(deltas)}")
            print(f"  Alerts: {len(alerts)}")
        except Exception as e:
            print(f"  [ERROR] Module 4D failed: {e}")
            import traceback; traceback.print_exc()

    # ── OUTPUT ────────────────────────────────────────────────────────────
    print(f"\n{'─' * 50}")
    print("GENERATING OUTPUTS")
    print("─" * 50)

    # Full results JSON
    results_data = []
    for r in all_results:
        results_data.append({
            "source": r.source, "doc_type": r.doc_type, "title": r.title,
            "url": r.url, "date": r.date, "relevance_score": r.relevance_score,
            "matched_keywords": r.matched_keywords,
            "metadata": r.metadata, "is_new": r.is_new,
            "text_excerpt": r.text_excerpt[:500],
        })

    results_file = OUTPUTS_DIR / f"dist_interconnections_{timestamp}.json"
    results_file.write_text(json.dumps(results_data, indent=2, default=str))
    print(f"  [OK] {len(results_data)} results → {results_file.name}")

    # Latest results (overwritten each run)
    latest = OUTPUTS_DIR / "latest_results.json"
    latest.write_text(json.dumps(results_data, indent=2, default=str))

    # Summary report
    summary = OUTPUTS_DIR / f"summary_{timestamp}.txt"
    with open(summary, "w") as f:
        f.write(f"Pipeline 2: Distribution Interconnections — {timestamp}\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"Total items: {len(all_results)}\n")

        by_source = {}
        for r in all_results:
            by_source.setdefault(r.source, []).append(r)
        f.write(f"\nBy source:\n")
        for src, items in sorted(by_source.items()):
            f.write(f"  {src}: {len(items)} items\n")

        # TSP metrics summary
        f.write(f"\n{'─' * 40}\nTSP Queue Metrics\n{'─' * 40}\n")
        for r in all_results:
            if r.metadata.get("extracted_metrics"):
                m = r.metadata["extracted_metrics"]
                f.write(f"\n{r.metadata.get('tsp', 'unknown').upper()} ({r.metadata.get('quarter', 'N/A')}):\n")
                for k, v in m.items():
                    f.write(f"  {k}: {v}\n")

        # EIA summary
        for r in all_results:
            if r.source == "eia_860m" and r.metadata.get("total_texas_planned"):
                f.write(f"\n{'─' * 40}\nEIA 860M Summary\n{'─' * 40}\n")
                for k, v in r.metadata.items():
                    if k != "columns":
                        f.write(f"  {k}: {v}\n")

        # Alerts
        alerts_file = OUTPUTS_DIR / "queue_alerts.json"
        if alerts_file.exists():
            alerts = json.loads(alerts_file.read_text())
            if alerts:
                f.write(f"\n{'─' * 40}\nAlerts ({len(alerts)})\n{'─' * 40}\n")
                for a in alerts:
                    f.write(f"  [{a['severity']}] {a['type']}: {a['title']}\n")
                    if a.get('signal'):
                        f.write(f"         → {a['signal']}\n")

    print(f"  [OK] Summary → {summary.name}")

    print(f"\n{'=' * 70}")
    print(f"PIPELINE COMPLETE — {len(all_results)} total items")
    print(f"  Outputs: {OUTPUTS_DIR}")
    print("=" * 70)

    return all_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline 2: Distribution Interconnections")
    parser.add_argument("-m", "--module", default="all",
                        choices=["4a", "4b", "4c", "4d", "all"],
                        help="Module to run (default: all)")
    args = parser.parse_args()
    modules = ["4a", "4b", "4c", "4d"] if args.module == "all" else [args.module]
    run_pipeline(modules)
