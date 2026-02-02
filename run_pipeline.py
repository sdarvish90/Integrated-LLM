#!/usr/bin/env python3
"""
Full Pipeline Runner
====================
Runs all 6 steps of the hydrogen intelligence pipeline in order:

  1. website_dl.py           - Download PDFs, data, articles from configured sites
  2. llm_training_system.py  - Ingest downloaded files into chunks
  3. llm_training_system.py  - Build ChromaDB knowledge base from chunks
  4. setup_database.py       - Create SQLite tables (skipped if DB already exists)
  5. migrate_database.py     - Add unique constraints (skipped if already migrated)
  6. hydrogen_intelligence_monitor_v3.py - Run the monitor interactively

Usage:
    python run_pipeline.py                  # Run all steps
    python run_pipeline.py --from 3         # Start from step 3 (build_kb)
    python run_pipeline.py --only 1         # Run only step 1
    python run_pipeline.py --skip 1         # Skip step 1 (downloads)
    python run_pipeline.py --skip 1,4,5     # Skip multiple steps
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# All paths relative to this script's directory
SCRIPT_DIR = Path(__file__).parent.resolve()
DOWNLOADS_DIR = SCRIPT_DIR / "downloads"
INGESTED_FILE = SCRIPT_DIR / "ingested_documents.json"
KB_DIR = SCRIPT_DIR / "knowledge_base_db"
SQLITE_DB = SCRIPT_DIR / "hydrogen_intelligence_v3.db"


def run_step(step_num: int, description: str, cmd: list[str], interactive: bool = False) -> bool:
    """Run a pipeline step. Returns True if successful."""
    print(f"\n{'='*70}")
    print(f"STEP {step_num}: {description}")
    print(f"{'='*70}")
    print(f"Command: {' '.join(cmd)}\n")

    start = time.time()
    try:
        if interactive:
            # For interactive steps, don't capture output
            result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
        else:
            result = subprocess.run(
                cmd, cwd=str(SCRIPT_DIR),
                stdout=sys.stdout, stderr=sys.stderr
            )
        elapsed = time.time() - start

        if result.returncode == 0:
            print(f"\n--- Step {step_num} completed in {elapsed:.1f}s ---")
            return True
        else:
            print(f"\n--- Step {step_num} FAILED (exit code {result.returncode}) after {elapsed:.1f}s ---")
            return False
    except KeyboardInterrupt:
        elapsed = time.time() - start
        print(f"\n--- Step {step_num} interrupted by user after {elapsed:.1f}s ---")
        return False
    except Exception as e:
        print(f"\n--- Step {step_num} ERROR: {e} ---")
        return False


def step1_download():
    """Download PDFs, data, articles from configured sites."""
    return run_step(1, "DOWNLOAD DOCUMENTS (website_dl.py)",
                    [sys.executable, str(SCRIPT_DIR / "website_dl.py")])


def step2_ingest():
    """Ingest downloaded files into chunks."""
    if not DOWNLOADS_DIR.exists():
        print(f"Downloads folder not found: {DOWNLOADS_DIR}")
        print("Run step 1 first, or create the folder manually.")
        return False

    file_count = sum(1 for _ in DOWNLOADS_DIR.rglob("*") if _.is_file())
    print(f"Found {file_count} files in {DOWNLOADS_DIR}")

    return run_step(2, "INGEST DOCUMENTS (llm_training_system.py --mode ingest)",
                    [sys.executable, str(SCRIPT_DIR / "llm_training_system.py"),
                     "--mode", "ingest", "--folder", str(DOWNLOADS_DIR)])


def step3_build_kb():
    """Build ChromaDB knowledge base from ingested chunks."""
    if not INGESTED_FILE.exists():
        print(f"Ingested documents not found: {INGESTED_FILE}")
        print("Run step 2 first.")
        return False

    size_mb = INGESTED_FILE.stat().st_size / (1024 * 1024)
    print(f"Ingested file size: {size_mb:.1f} MB")

    return run_step(3, "BUILD KNOWLEDGE BASE (llm_training_system.py --mode build_kb)",
                    [sys.executable, str(SCRIPT_DIR / "llm_training_system.py"),
                     "--mode", "build_kb"])


def step4_setup_db():
    """Create SQLite tables."""
    if SQLITE_DB.exists():
        size_kb = SQLITE_DB.stat().st_size / 1024
        print(f"Database already exists: {SQLITE_DB} ({size_kb:.0f} KB)")
        print("Running setup anyway (uses CREATE IF NOT EXISTS, safe to re-run).")

    return run_step(4, "SETUP DATABASE (setup_database.py)",
                    [sys.executable, str(SCRIPT_DIR / "setup_database.py")])


def step5_migrate_db():
    """Add unique constraints to database."""
    if not SQLITE_DB.exists():
        print(f"Database not found: {SQLITE_DB}")
        print("Run step 4 first.")
        return False

    # Check if migration is needed by looking for unique constraints
    import sqlite3
    conn = sqlite3.connect(str(SQLITE_DB))
    cursor = conn.cursor()
    cursor.execute("SELECT sql FROM sqlite_master WHERE name='competitors'")
    row = cursor.fetchone()
    conn.close()

    if row and "UNIQUE" in (row[0] or ""):
        print("Database already has unique constraints. Skipping migration.")
        return True

    # Run migration non-interactively by piping 'y'
    print("Running migration...")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "migrate_database.py")],
            cwd=str(SCRIPT_DIR),
            input="y\n", text=True,
            stdout=sys.stdout, stderr=sys.stderr
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Migration error: {e}")
        return False


def step6_monitor():
    """Run the hydrogen intelligence monitor interactively."""
    return run_step(6, "RUN MONITOR (hydrogen_intelligence_monitor_v3.py)",
                    [sys.executable, str(SCRIPT_DIR / "hydrogen_intelligence_monitor_v3.py")],
                    interactive=True)


STEPS = {
    1: ("Download documents", step1_download),
    2: ("Ingest documents", step2_ingest),
    3: ("Build knowledge base", step3_build_kb),
    4: ("Setup database", step4_setup_db),
    5: ("Migrate database", step5_migrate_db),
    6: ("Run monitor", step6_monitor),
}


def main():
    parser = argparse.ArgumentParser(
        description="Run the hydrogen intelligence pipeline (all 6 steps)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Steps:
  1. Download documents      (website_dl.py)
  2. Ingest documents        (llm_training_system.py --mode ingest)
  3. Build knowledge base    (llm_training_system.py --mode build_kb)
  4. Setup database           (setup_database.py)
  5. Migrate database         (migrate_database.py)
  6. Run monitor              (hydrogen_intelligence_monitor_v3.py)

Examples:
  python run_pipeline.py                  # Run all steps
  python run_pipeline.py --from 3         # Start from step 3
  python run_pipeline.py --only 1         # Run only step 1
  python run_pipeline.py --skip 1         # Skip step 1
  python run_pipeline.py --skip 1,4,5     # Skip steps 1, 4, and 5
  python run_pipeline.py --from 2 --to 3  # Run steps 2 and 3 only
        """
    )

    parser.add_argument("--from", dest="start", type=int, default=1,
                        help="Start from this step (1-6)")
    parser.add_argument("--to", dest="end", type=int, default=6,
                        help="Stop after this step (1-6)")
    parser.add_argument("--only", type=int,
                        help="Run only this step (1-6)")
    parser.add_argument("--skip", type=str, default="",
                        help="Comma-separated steps to skip (e.g., 1,4,5)")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="Stop pipeline if a step fails (default: continue)")

    args = parser.parse_args()

    # Determine which steps to run
    if args.only:
        steps_to_run = [args.only]
    else:
        steps_to_run = list(range(args.start, args.end + 1))

    skip_set = set()
    if args.skip:
        skip_set = {int(s.strip()) for s in args.skip.split(",")}

    steps_to_run = [s for s in steps_to_run if s not in skip_set]

    # Validate
    for s in steps_to_run:
        if s not in STEPS:
            print(f"Error: Invalid step {s}. Valid steps are 1-6.")
            sys.exit(1)

    # Show plan
    print("="*70)
    print("HYDROGEN INTELLIGENCE PIPELINE")
    print("="*70)
    print(f"\nSteps to run:")
    for s in steps_to_run:
        desc, _ = STEPS[s]
        print(f"  {s}. {desc}")
    print()

    # Execute
    results = {}
    for s in steps_to_run:
        desc, func = STEPS[s]
        success = func()
        results[s] = success

        if not success and args.stop_on_error:
            print(f"\nPipeline stopped at step {s} due to --stop-on-error")
            break

    # Summary
    print(f"\n{'='*70}")
    print("PIPELINE SUMMARY")
    print(f"{'='*70}\n")
    for s in steps_to_run:
        desc, _ = STEPS[s]
        status = "PASS" if results.get(s) else "FAIL"
        icon = "+" if results.get(s) else "x"
        print(f"  [{icon}] Step {s}: {desc} - {status}")

    failed = [s for s, ok in results.items() if not ok]
    if failed:
        print(f"\n{len(failed)} step(s) failed: {failed}")
        sys.exit(1)
    else:
        print(f"\nAll {len(results)} steps completed successfully.")


if __name__ == "__main__":
    main()
