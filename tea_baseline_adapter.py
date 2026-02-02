# tea_baseline_adapter.py
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ShareRunConfig:
    """
    Configure how we run SHARE.
    - locations_root: parent folder containing location subfolders (e.g., ./locations/Oman)
    - share_entrypoint: filename expected inside each location folder
    - baseline_output_name: filename we create/update in the location folder
    - share_output_hint: optional filename SHARE already produces (csv/json) that we can map into a baseline
    """
    locations_root: Path
    share_entrypoint: str = "SHARE_Model_main_v1.py"
    baseline_output_name: str = "baseline.json"
    share_output_hint: Optional[str] = None  # e.g., "outputs.json" if SHARE already writes it


class ShareBaselineAdapter:
    """
    Runs SHARE for a given location folder and returns a normalized baseline dict.
    Also writes baseline.json into the location folder.
    """

    def __init__(self, config: ShareRunConfig):
        self.cfg = config

    def location_path(self, location: str) -> Path:
        return (self.cfg.locations_root / location).resolve()

    def baseline_path(self, location: str) -> Path:
        return self.location_path(location) / self.cfg.baseline_output_name

    def get_baseline(self, location: str) -> Dict[str, Any]:
        """
        Read baseline from Summary_output folder (no SHARE execution).

        Priority:
        1. Summary_output/*.csv files
        2. Cached baseline.json
        """
        loc_dir = self.location_path(location)
        if not loc_dir.exists():
            raise FileNotFoundError(f"Location folder not found: {loc_dir}")

        # Try to build baseline from Summary_output
        baseline = self._build_baseline_from_outputs(loc_dir)

        # Cache it to baseline.json
        baseline_file = self.baseline_path(location)
        self._save_json(baseline_file, baseline)

        return baseline

    def run_share_and_build_baseline(
        self,
        location: str,
        *,
        force_rerun: bool = False,
        extra_env: Optional[Dict[str, str]] = None,
        timeout_sec: int = 900,
    ) -> Dict[str, Any]:
        """
        Run SHARE model and build baseline (only use if you need to re-run SHARE).
        For just reading existing outputs, use get_baseline() instead.
        """
        loc_dir = self.location_path(location)
        if not loc_dir.exists():
            raise FileNotFoundError(f"Location folder not found: {loc_dir}")

        baseline_file = self.baseline_path(location)
        if baseline_file.exists() and not force_rerun:
            return self._load_json(baseline_file)

        # Run SHARE
        share_script = loc_dir / self.cfg.share_entrypoint
        if not share_script.exists():
            raise FileNotFoundError(
                f"SHARE entrypoint not found at: {share_script}\n"
                f"Expected {self.cfg.share_entrypoint} inside {loc_dir}"
            )

        self._run_python_script(
            script_path=share_script,
            cwd=loc_dir,
            extra_env=extra_env,
            timeout_sec=timeout_sec,
        )

        # Convert SHARE outputs -> baseline dict
        baseline = self._build_baseline_from_outputs(loc_dir)

        # Save baseline.json
        self._save_json(baseline_file, baseline)

        return baseline

    # -------------------------
    # Internals
    # -------------------------
    def _run_python_script(
        self,
        *,
        script_path: Path,
        cwd: Path,
        extra_env: Optional[Dict[str, str]],
        timeout_sec: int,
    ) -> None:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)

        # Using sys.executable ensures we run in the same venv/conda env as the monitor.
        cmd = [sys.executable, str(script_path)]

        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )

        if proc.returncode != 0:
            raise RuntimeError(
                "SHARE run failed.\n"
                f"Command: {' '.join(cmd)}\n"
                f"CWD: {cwd}\n\n"
                f"STDOUT:\n{proc.stdout}\n\n"
                f"STDERR:\n{proc.stderr}\n"
            )

    def _build_baseline_from_outputs(self, loc_dir: Path) -> Dict[str, Any]:
        """
        Build baseline from SHARE outputs.

        Priority:
        1. Summary_output folder CSV files (Project_Summary.csv)
        2. JSON files (outputs.json, results.json, etc.)
        """
        # 1) Check Summary_output folder for CSV files
        summary_dir = loc_dir / "Summary_output"
        if summary_dir.exists():
            csv_files = list(summary_dir.glob("*Project_Summary.csv"))
            if csv_files:
                # Use the most recent CSV file
                latest_csv = max(csv_files, key=lambda p: p.stat().st_mtime)
                raw = self._load_csv_as_dict(latest_csv)
                return self._normalize_share_outputs(raw, loc_dir=loc_dir)

        # 2) Fallback to JSON candidates
        candidates = []
        if self.cfg.share_output_hint:
            candidates.append(loc_dir / self.cfg.share_output_hint)

        candidates += [
            loc_dir / "outputs.json",
            loc_dir / "results.json",
            loc_dir / "tea_outputs.json",
        ]

        found = next((p for p in candidates if p.exists()), None)

        if found and found.suffix.lower() == ".json":
            raw = self._load_json(found)
            return self._normalize_share_outputs(raw, loc_dir=loc_dir)

        raise FileNotFoundError(
            "Could not find SHARE output to build baseline.\n"
            f"Checked Summary_output folder: {summary_dir}\n"
            f"Checked JSON candidates: {[str(p) for p in candidates]}\n\n"
            "Fix options:\n"
            "1) Ensure SHARE writes Project_Summary.csv to Summary_output/\n"
            "2) Set ShareRunConfig(share_output_hint='YOUR_FILE.json')\n"
        )

    def _load_csv_as_dict(self, path: Path) -> Dict[str, Any]:
        """Load a CSV file and return the first data row as a dict."""
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            if not rows:
                raise ValueError(f"CSV file is empty: {path}")
            # Return the first (and typically only) data row
            return rows[0]

    def _normalize_share_outputs(self, raw: Dict[str, Any], *, loc_dir: Path) -> Dict[str, Any]:
        """
        Normalize raw SHARE outputs to what your monitor expects.

        Handles both JSON outputs and CSV Summary_output files.
        CSV columns use formats like "Total_LCOH [$/tonnes]" while JSON uses "Total_LCOH_USD_per_kg".
        """
        def pick(*keys: str, default: Any = None) -> Any:
            for k in keys:
                if k in raw and raw[k] is not None and raw[k] != "":
                    try:
                        return float(raw[k])
                    except (ValueError, TypeError):
                        return raw[k]
            return default

        # LCOH: CSV uses $/tonnes, convert to $/kg (divide by 1000)
        lcoh_tonnes = pick("Total_LCOH [$/tonnes]", "Total LCOH [$/tonnes]")
        lcoh_kg = pick("Total_LCOH_USD_per_kg", "lcoh_usd_per_kg", "LCOH", "lcoh")
        if lcoh_tonnes is not None:
            lcoh_kg = lcoh_tonnes / 1000.0

        # LCOA: CSV uses $/tonnes, convert to $/kg (divide by 1000)
        lcoa_tonnes = pick("Total_LCOA [$/tonnes]", "Total LCOA [$/tonnes]")
        lcoa_kg = pick("Total_LCOA_USD_per_kg", "lcoa_usd_per_kg", "LCOA", "lcoa")
        if lcoa_tonnes is not None:
            lcoa_kg = lcoa_tonnes / 1000.0

        baseline = {
            "lcoh_usd_per_kg": lcoh_kg,
            "lcoa_usd_per_kg": lcoa_kg,
            "lcoe_usd_per_mwh": pick("Total_LCOE [$/MWh]", "Total LCOE [$/MWh]",
                                     "Total_LCOE_USD_per_MWh", "lcoe_usd_per_mwh", "LCOE"),

            # NPV/IRR/CAPEX
            "npv": pick("NPV [$M]", "NPV_USD_M", "cashflows_NPV", "npv", "NPV"),
            "irr_pct": pick("IRR [%]", "IRR_percent", "irr_pct", "IRR_pct"),
            "capex": pick("Total_CAPEX [$M]", "Total_CAPEX_USD_M", "capex_usd_m", "CAPEX"),

            # Additional useful fields from CSV
            "nh3_prod_tpa": pick("NH3_prod_[TPA]", "NH3_prod_TPA"),
            "h2_prod_tpa": pick("H2_prod_[TPA]", "H2_prod_TPA"),
            "cf_nh3_pct": pick("CF_NH3 [%]", "CF_NH3"),
            "cf_h2_pct": pick("CF_H2 [%]", "CF_H2"),
            "solar_capacity_mw": pick("Solar_capacity [MW]"),
            "wind_capacity_mw": pick("Wind_capacity [MW]"),
            "electrolyser_capacity_mw": pick("Electrolyser_Capacity[MW]"),

            "assumptions": pick("assumptions", default={}),
            "source": {"model": "SHARE", "location_dir": str(loc_dir)},
        }

        # Guardrail: fail loudly if the minimum baseline fields are missing
        missing = [k for k in ("lcoh_usd_per_kg",) if baseline.get(k) is None]

        if missing:
            raise ValueError(
                f"Baseline missing required fields: {missing}\n"
                f"Raw keys available: {sorted(raw.keys())}"
            )

        return baseline

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _save_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
