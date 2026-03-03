"""
Pipeline 1: Large Load Interconnection Queue Monitor
=====================================================
Main orchestrator — runs all scrapers (2A, 2B, 2D), deduplicates,
scores relevance, and produces output reports.

Usage:
    python run_pipeline.py                 # Run all scrapers
    python run_pipeline.py --module 2a     # Run only ERCOT public docs
    python run_pipeline.py --module 2b     # Run only PUC SB6 dockets
    python run_pipeline.py --module 2d     # Run only indirect sources
    python run_pipeline.py --report-only   # Just regenerate reports from stored data
"""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import OUTPUTS_DIR, STORAGE_DIR, ScrapeResult
from scrapers.utils import results_to_json, results_to_dataframe


def run_pipeline(modules: list[str] = None) -> list[ScrapeResult]:
    """
    Run specified scraper modules and return combined results.
    
    Args:
        modules: List of module codes to run. None = all.
                 Options: "2a", "2b", "2d"
    """
    if modules is None:
        modules = ["2a", "2b", "2c", "2d"]

    all_results: list[ScrapeResult] = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    print("=" * 70)
    print(f"Pipeline 1: Large Load Queue Monitor — {timestamp}")
    print("=" * 70)

    # ── 2A: ERCOT Public Documents ───────────────────────────────────────
    if "2a" in modules:
        print("\n" + "─" * 50)
        print("MODULE 2A: ERCOT Public Documents")
        print("─" * 50)
        try:
            from scrapers.scraper_2a_ercot_public import run_all_2a
            results_2a = run_all_2a()
            all_results.extend(results_2a)
            print(f"  → {len(results_2a)} items from 2A")
        except Exception as e:
            print(f"  [ERROR] Module 2A failed: {e}")
            import traceback
            traceback.print_exc()

    # ── 2B: PUC SB6 Docket Monitor ──────────────────────────────────────
    if "2b" in modules:
        print("\n" + "─" * 50)
        print("MODULE 2B: PUC SB6 Docket Monitor")
        print("─" * 50)
        try:
            from scrapers.scraper_2b_puc_sb6 import run_all_2b
            results_2b = run_all_2b()
            all_results.extend(results_2b)
            print(f"  → {len(results_2b)} items from 2B")
        except Exception as e:
            print(f"  [ERROR] Module 2B failed: {e}")
            import traceback
            traceback.print_exc()

    # ── 2C: ERCOT Large Load Integration Deep Monitor ──────────────────
    if "2c" in modules:
        print("\n" + "─" * 50)
        print("MODULE 2C: ERCOT Large Load Integration Deep Monitor")
        print("─" * 50)
        try:
            from scrapers.scraper_2c_large_load_page import run_all_2c
            results_2c = run_all_2c()
            all_results.extend(results_2c)
            print(f"  → {len(results_2c)} items from 2C")
        except Exception as e:
            print(f"  [ERROR] Module 2C failed: {e}")
            import traceback
            traceback.print_exc()

    # ── 2D: Indirect Sources ─────────────────────────────────────────────
    if "2d" in modules:
        print("\n" + "─" * 50)
        print("MODULE 2D: Indirect Triangulation Sources")
        print("─" * 50)
        try:
            from scrapers.scraper_2d_indirect import run_all_2d
            results_2d = run_all_2d()
            all_results.extend(results_2d)
            print(f"  → {len(results_2d)} items from 2D")
        except Exception as e:
            print(f"  [ERROR] Module 2D failed: {e}")
            import traceback
            traceback.print_exc()

    # ── Post-processing ──────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("POST-PROCESSING")
    print("─" * 50)

    # Deduplicate by content_hash
    seen_hashes = set()
    deduped = []
    for r in all_results:
        if r.content_hash not in seen_hashes:
            seen_hashes.add(r.content_hash)
            deduped.append(r)
    print(f"  Deduplicated: {len(all_results)} → {len(deduped)} items")

    # Sort by relevance (highest first), then by date
    deduped.sort(key=lambda r: (-r.relevance_score, r.date), reverse=False)

    # ── Generate Outputs ─────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("GENERATING OUTPUTS")
    print("─" * 50)

    # Full results JSON
    full_output = OUTPUTS_DIR / f"large_load_queue_results_{timestamp}.json"
    results_to_json(deduped, full_output)

    # High-relevance items (score > 0.2)
    high_relevance = [r for r in deduped if r.relevance_score >= 0.2]
    if high_relevance:
        hr_output = OUTPUTS_DIR / f"high_relevance_{timestamp}.json"
        results_to_json(high_relevance, hr_output)

    # New items only
    new_items = [r for r in deduped if r.is_new]
    if new_items:
        new_output = OUTPUTS_DIR / f"new_items_{timestamp}.json"
        results_to_json(new_items, new_output)

    # Summary report
    summary = _generate_summary(deduped, timestamp, modules)
    summary_path = OUTPUTS_DIR / f"summary_{timestamp}.txt"
    summary_path.write_text(summary)
    print(f"  [OK] Summary written to {summary_path}")

    # Also save latest results for easy access
    latest_output = OUTPUTS_DIR / "latest_results.json"
    results_to_json(deduped, latest_output)

    print("\n" + "=" * 70)
    print(f"PIPELINE COMPLETE — {len(deduped)} total items")
    print(f"  High relevance (≥0.2): {len(high_relevance)}")
    print(f"  New items: {len(new_items)}")
    print(f"  Outputs: {OUTPUTS_DIR}")
    print("=" * 70)

    return deduped


def _generate_summary(results: list[ScrapeResult], timestamp: str,
                       modules: list[str]) -> str:
    """Generate a human-readable summary report."""
    lines = [
        "=" * 70,
        f"LARGE LOAD QUEUE MONITOR — Summary Report",
        f"Generated: {timestamp}",
        f"Modules run: {', '.join(modules)}",
        "=" * 70,
        "",
        f"Total items collected: {len(results)}",
        f"New items: {sum(1 for r in results if r.is_new)}",
        f"High relevance (≥0.2): {sum(1 for r in results if r.relevance_score >= 0.2)}",
        "",
    ]

    # Breakdown by source
    sources = {}
    for r in results:
        sources.setdefault(r.source, []).append(r)

    lines.append("── BY SOURCE ──────────────────────────────────────────")
    for source, items in sorted(sources.items()):
        lines.append(f"  {source}: {len(items)} items")

    # Top keywords across all results
    keyword_counts: dict[str, int] = {}
    for r in results:
        for kw in r.matched_keywords:
            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

    if keyword_counts:
        lines.append("")
        lines.append("── TOP MATCHED KEYWORDS ───────────────────────────────")
        for kw, count in sorted(keyword_counts.items(), key=lambda x: -x[1])[:20]:
            lines.append(f"  {kw}: {count} mentions")

    # High-relevance items detail
    high_rel = [r for r in results if r.relevance_score >= 0.2]
    if high_rel:
        lines.append("")
        lines.append("── HIGH-RELEVANCE ITEMS (≥0.2) ────────────────────────")
        for r in high_rel[:30]:
            lines.append(f"  [{r.source}] {r.title[:65]}")
            lines.append(f"    Score: {r.relevance_score:.2f} | Date: {r.date} | Keywords: {', '.join(r.matched_keywords[:5])}")
            if r.metadata.get("queue_data"):
                lines.append(f"    Queue data: {r.metadata['queue_data']}")
            if r.metadata.get("load_breakdown"):
                lines.append(f"    Load breakdown: {r.metadata['load_breakdown']}")
            lines.append("")

    # Extracted queue data (if any operational overviews found)
    queue_items = [r for r in results if r.metadata.get("queue_data")]
    if queue_items:
        lines.append("")
        lines.append("── EXTRACTED LARGE LOAD QUEUE DATA ─────────────────────")
        for r in queue_items:
            lines.append(f"  Source: {r.title[:60]} ({r.date})")
            for k, v in r.metadata["queue_data"].items():
                lines.append(f"    {k}: {v}")
            lines.append("")

    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pipeline 1: Large Load Interconnection Queue Monitor"
    )
    parser.add_argument(
        "--module", "-m",
        choices=["2a", "2b", "2c", "2d", "all"],
        default="all",
        help="Which module(s) to run (default: all)"
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Only regenerate reports from latest_results.json"
    )
    args = parser.parse_args()

    if args.report_only:
        latest = OUTPUTS_DIR / "latest_results.json"
        if not latest.exists():
            print("No latest_results.json found. Run pipeline first.")
            sys.exit(1)
        data = json.loads(latest.read_text())
        results = [ScrapeResult(**d) for d in data]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        summary = _generate_summary(results, timestamp, ["report-only"])
        print(summary)
        return

    modules = ["2a", "2b", "2c", "2d"] if args.module == "all" else [args.module]
    run_pipeline(modules)


if __name__ == "__main__":
    main()
