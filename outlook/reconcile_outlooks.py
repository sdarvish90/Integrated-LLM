#!/usr/bin/env python3
"""
Cross-Source Outlook Reconciliation Layer
==========================================

Merges canonical outlook JSONs from multiple extractors (EIA AEO, NREL ATB,
IEA WEO, ERCOT CDR) into a single composite outlook per scenario alignment.

Source priority rules:
    - Technology costs:  NREL ATB > IEA WEO > EIA AEO
    - US energy/demand:  EIA AEO > IEA WEO (annual vs snapshot, US-focused)
    - Commodity prices:  EIA AEO > IEA WEO (annual resolution)
    - Texas grid:        ERCOT > EIA AEO (regional granularity)
    - Global variables:  IEA WEO > EIA AEO (IEA is global authority)
    - Macro/GDP:         EIA AEO > IEA WEO (US primary)

Usage:
    python3 reconcile_outlooks.py --output-dir output/
    python3 reconcile_outlooks.py --eia output/eia_aeo_2025_reference.json \\
                                  --atb output/nrel_atb_2024_moderate.json \\
                                  --weo output/iea_weo_2024_steps.json \\
                                  --ercot output/ercot_cdr_2025_base.json
    python3 reconcile_outlooks.py --output-dir output/ --verbose
"""

import argparse
import csv
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from energy_outlook_canonical_schema import (
    VARIABLE_CATALOG,
    build_empty_outlook_all_variables,
    get_variables_for_source,
)

logger = logging.getLogger("reconcile_outlooks")

# =============================================================================
# SOURCE PRIORITY CONFIGURATION
# =============================================================================

# Default source priority (higher = preferred). Used when no category override applies.
DEFAULT_PRIORITY = {
    "eia_aeo": 80,
    "nrel_atb": 70,
    "iea_weo": 60,
    "ercot": 50,
}

# Category-specific priority overrides
CATEGORY_PRIORITY = {
    "technology_costs": {
        "nrel_atb": 95,   # NREL ATB is gold standard for tech costs
        "iea_weo": 60,
        "eia_aeo": 50,
        "ercot": 10,
    },
    "commodity_prices": {
        "eia_aeo": 90,    # Annual data, US-specific
        "iea_weo": 70,    # Snapshot years, global
        "nrel_atb": 10,
        "ercot": 10,
    },
    "electricity_system": {
        "eia_aeo": 85,    # NEMS model, annual
        "ercot": 80,      # ERCOT-specific variables
        "iea_weo": 60,
        "nrel_atb": 40,
    },
    "energy_demand": {
        "eia_aeo": 90,
        "iea_weo": 65,
        "nrel_atb": 10,
        "ercot": 30,
    },
    "fossil_fuel_supply": {
        "eia_aeo": 90,
        "iea_weo": 60,
        "nrel_atb": 10,
        "ercot": 10,
    },
    "hydrogen_economy": {
        "eia_aeo": 70,
        "nrel_atb": 85,    # ATB has detailed H2 cost data
        "iea_weo": 75,     # WEO has global H2 production/demand
        "ercot": 10,
    },
    "ccus": {
        "eia_aeo": 80,
        "iea_weo": 75,
        "nrel_atb": 40,
        "ercot": 10,
    },
    "emissions": {
        "eia_aeo": 85,
        "iea_weo": 70,
        "nrel_atb": 10,
        "ercot": 10,
    },
    "transport_electrification": {
        "eia_aeo": 85,
        "iea_weo": 70,
        "nrel_atb": 10,
        "ercot": 10,
    },
    "buildings_electrification": {
        "iea_weo": 80,   # WEO has heat pump data
        "eia_aeo": 70,
        "nrel_atb": 10,
        "ercot": 10,
    },
    "investment_flows": {
        "iea_weo": 90,    # IEA is the authority on global energy investment
        "eia_aeo": 50,
        "nrel_atb": 40,
        "ercot": 10,
    },
    "macroeconomic": {
        "eia_aeo": 85,    # US GDP from EIA/BLS
        "iea_weo": 65,    # Global/PPP-based
        "nrel_atb": 10,
        "ercot": 10,
    },
    "policy_regulatory": {
        "eia_aeo": 80,
        "nrel_atb": 75,    # ATB tracks IRA incentives closely
        "iea_weo": 40,
        "ercot": 30,
    },
    "biofuels_renewable_fuels": {
        "eia_aeo": 80,
        "iea_weo": 70,
        "nrel_atb": 10,
        "ercot": 10,
    },
    "critical_minerals": {
        "iea_weo": 90,    # IEA is primary source for critical minerals projections
        "eia_aeo": 30,
        "nrel_atb": 30,
        "ercot": 10,
    },
}

# Variable-specific overrides (highest priority)
VARIABLE_PRIORITY_OVERRIDE = {
    # ERCOT is authoritative for Texas-specific variables
    "ercot_peak_demand": {"ercot": 99},
    "ercot_reserve_margin": {"ercot": 99},
    "ercot_installed_capacity": {"ercot": 99},
    "ercot_energy_demand": {"ercot": 99},
    # NREL ATB is authoritative for specific LCOE/CAPEX
    "solar_pv_utility_capex": {"nrel_atb": 99},
    "solar_pv_utility_lcoe": {"nrel_atb": 99},
    "wind_onshore_capex": {"nrel_atb": 99},
    "wind_onshore_lcoe": {"nrel_atb": 99},
    "battery_4hr_capex": {"nrel_atb": 99},
    "battery_4hr_lcoe": {"nrel_atb": 99},
    "electrolyzer_pem_capex": {"nrel_atb": 99},
}

# =============================================================================
# SCENARIO ALIGNMENT
# =============================================================================

# Maps extractor scenarios to canonical alignment groups.
# Each group represents comparable scenarios across sources.
SCENARIO_ALIGNMENT = {
    "reference_central": {
        "label": "Reference / Central",
        "eia_aeo": "reference",
        "nrel_atb": "moderate",
        "iea_weo": "steps",
        "ercot": "base",
    },
    "optimistic_clean": {
        "label": "Optimistic Clean Energy",
        "eia_aeo": "low_ztc",
        "nrel_atb": "advanced",
        "iea_weo": "steps",       # WEO 2025: CPS replaced APS; STEPS is closest to old APS
        "ercot": "base",
    },
    "pessimistic_fossil": {
        "label": "Pessimistic / High Fossil",
        "eia_aeo": "high_oil_price",
        "nrel_atb": "conservative",
        "iea_weo": "cps",          # WEO 2025: CPS (current policies) is ideal for pessimistic
        "ercot": "high",
    },
    "net_zero": {
        "label": "Net Zero / Aggressive Decarbonization",
        "eia_aeo": "low_ztc",
        "nrel_atb": "advanced",
        "iea_weo": "nze",
        "ercot": "base",
    },
}


# =============================================================================
# RECONCILIATION ENGINE
# =============================================================================

class OutlookReconciler:
    """Merges multiple source outlooks into a composite canonical outlook."""

    def __init__(
        self,
        outlooks: dict[str, dict],
        start_year: int = 2024,
        end_year: int = 2050,
    ):
        """
        Args:
            outlooks: {source_id: outlook_dict, ...}
            start_year: First projection year
            end_year: Last projection year
        """
        self.outlooks = outlooks
        self.start_year = start_year
        self.end_year = end_year
        self.years = [str(y) for y in range(start_year, end_year + 1)]

        logger.info(f"Reconciler initialized with sources: {list(outlooks.keys())}")
        for src, ol in outlooks.items():
            var_count = sum(
                1 for v in ol.get("variables", {}).values()
                if v.get("annual")
            )
            logger.info(f"  {src}: {var_count} populated variables, "
                        f"scenario={ol.get('scenario_name', '?')}")

    def _get_priority(self, var_key: str, source_id: str) -> int:
        """
        Get the effective priority score for a variable from a source.
        Checks: variable override → category override → default.
        """
        # Variable-specific override
        if var_key in VARIABLE_PRIORITY_OVERRIDE:
            overrides = VARIABLE_PRIORITY_OVERRIDE[var_key]
            if source_id in overrides:
                return overrides[source_id]

        # Category-based priority
        cat_info = VARIABLE_CATALOG.get(var_key, {})
        category = cat_info.get("category", "")
        if category in CATEGORY_PRIORITY:
            return CATEGORY_PRIORITY[category].get(source_id, 0)

        # Default
        return DEFAULT_PRIORITY.get(source_id, 0)

    def reconcile(self, alignment_key: str = "reference_central") -> dict:
        """
        Build a composite outlook by merging sources according to priority rules.

        Returns a canonical outlook dict with:
            - Best-available value for each variable/year
            - Provenance tracking (which source was used per variable)
            - Conflict report (where sources disagree)
        """
        alignment = SCENARIO_ALIGNMENT.get(alignment_key, {})
        label = alignment.get("label", alignment_key)

        logger.info(f"Reconciling: {label}")
        logger.info(f"  Alignment: {alignment}")

        # Build composite outlook
        composite = build_empty_outlook_all_variables(
            source_id="composite",
            scenario_name=alignment_key,
            base_year=self.start_year,
            horizon=self.end_year,
        )
        composite["source_name"] = f"DecarbIQ Composite: {label}"
        composite["scenario_description"] = label
        composite["extraction_metadata"] = {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": "reconciliation",
            "confidence_score": None,  # Set per variable
            "source_document": "Multiple (see provenance)",
            "notes": f"Composite from: {', '.join(self.outlooks.keys())}",
            "alignment": alignment_key,
            "source_scenarios": {
                s: self.outlooks[s].get("scenario_name", "?")
                for s in self.outlooks
            },
        }

        variables = composite["variables"]
        provenance = {}
        conflicts = []

        for var_key in VARIABLE_CATALOG:
            if var_key not in variables:
                continue

            # Collect data from all sources that have this variable
            source_data = {}
            for src_id, outlook in self.outlooks.items():
                src_vars = outlook.get("variables", {})
                if var_key in src_vars:
                    annual = src_vars[var_key].get("annual", {})
                    if annual:
                        source_data[src_id] = annual

            if not source_data:
                continue

            # Rank sources by priority
            ranked = sorted(
                source_data.keys(),
                key=lambda s: self._get_priority(var_key, s),
                reverse=True,
            )

            primary_source = ranked[0]
            primary_data = source_data[primary_source]

            # Use primary source data
            variables[var_key]["annual"] = dict(primary_data)

            # Track provenance
            provenance[var_key] = {
                "primary_source": primary_source,
                "priority_score": self._get_priority(var_key, primary_source),
                "alternative_sources": ranked[1:] if len(ranked) > 1 else [],
                "source_count": len(source_data),
            }

            # Detect conflicts between sources
            if len(source_data) > 1:
                conflict = self._check_conflict(
                    var_key, source_data, primary_source
                )
                if conflict:
                    conflicts.append(conflict)

        # Compute composite confidence scores
        self._compute_confidence(variables, provenance)

        # Add reconciliation metadata
        composite["reconciliation"] = {
            "provenance": provenance,
            "conflicts": conflicts,
            "summary": self._summarize(variables, provenance, conflicts),
        }

        return composite

    def _check_conflict(
        self,
        var_key: str,
        source_data: dict[str, dict],
        primary_source: str,
    ) -> Optional[dict]:
        """
        Check if sources disagree significantly on a variable.
        Returns a conflict dict if divergence exceeds threshold.
        """
        sources = list(source_data.keys())
        if len(sources) < 2:
            return None

        # Compare at specific years (2030, 2040, 2050)
        check_years = ["2030", "2040", "2050"]
        max_divergence = 0
        worst_year = None
        details = {}

        for yr in check_years:
            values = {}
            for src in sources:
                val = source_data[src].get(yr)
                if val is not None:
                    values[src] = val

            if len(values) < 2:
                continue

            # Calculate divergence as max relative difference from primary
            primary_val = values.get(primary_source)
            if primary_val is None or primary_val == 0:
                continue

            for src, val in values.items():
                if src == primary_source:
                    continue
                rel_diff = abs(val - primary_val) / abs(primary_val)
                if rel_diff > max_divergence:
                    max_divergence = rel_diff
                    worst_year = yr

            details[yr] = values

        # Only flag if divergence > 20%
        if max_divergence > 0.20:
            cat_info = VARIABLE_CATALOG.get(var_key, {})
            return {
                "variable": var_key,
                "category": cat_info.get("category", ""),
                "priority": cat_info.get("priority", 0),
                "primary_source": primary_source,
                "max_divergence_pct": round(max_divergence * 100, 1),
                "worst_year": worst_year,
                "values_by_source": details,
                "level": "WARNING" if max_divergence < 0.5 else "ERROR",
            }

        return None

    def _compute_confidence(
        self,
        variables: dict,
        provenance: dict,
    ):
        """Compute per-variable confidence scores."""
        for var_key, prov in provenance.items():
            source = prov["primary_source"]
            n_sources = prov["source_count"]

            # Base confidence by source
            base_conf = {
                "eia_aeo": 0.95,     # API, annual, well-documented
                "nrel_atb": 0.93,    # CSV, annual, R&D-based
                "iea_weo": 0.85,     # Excel, snapshot years (interpolated)
                "ercot": 0.88,       # Excel, limited horizon (extrapolated)
            }.get(source, 0.70)

            # Boost if corroborated by multiple sources
            if n_sources >= 3:
                base_conf = min(base_conf + 0.05, 0.99)
            elif n_sources >= 2:
                base_conf = min(base_conf + 0.02, 0.99)

            # Store in provenance
            prov["confidence_score"] = round(base_conf, 3)

    def _summarize(
        self,
        variables: dict,
        provenance: dict,
        conflicts: list,
    ) -> dict:
        """Generate reconciliation summary statistics."""
        total_vars = len(VARIABLE_CATALOG)
        populated = len(provenance)

        # Count by primary source
        by_source = {}
        for prov in provenance.values():
            src = prov["primary_source"]
            by_source[src] = by_source.get(src, 0) + 1

        # Count by priority
        by_priority = {1: 0, 2: 0, 3: 0}
        for var_key in provenance:
            prio = VARIABLE_CATALOG.get(var_key, {}).get("priority", 0)
            if prio in by_priority:
                by_priority[prio] += 1

        total_p1 = sum(
            1 for v in VARIABLE_CATALOG.values() if v.get("priority") == 1
        )
        total_p2 = sum(
            1 for v in VARIABLE_CATALOG.values() if v.get("priority") == 2
        )

        return {
            "total_variables": total_vars,
            "populated": populated,
            "coverage_pct": round(populated / total_vars * 100, 1) if total_vars else 0,
            "by_primary_source": by_source,
            "by_priority": {
                f"P{k}": {"populated": v, "total": {"1": total_p1, "2": total_p2}.get(str(k), 0)}
                for k, v in by_priority.items()
            },
            "conflicts_total": len(conflicts),
            "conflicts_error": sum(1 for c in conflicts if c["level"] == "ERROR"),
            "conflicts_warning": sum(1 for c in conflicts if c["level"] == "WARNING"),
            "avg_confidence": round(
                sum(p["confidence_score"] for p in provenance.values()) / max(len(provenance), 1),
                3,
            ),
        }

    # =========================================================================
    # OUTPUT
    # =========================================================================

    def save_composite(
        self,
        composite: dict,
        output_dir: str,
    ) -> tuple[str, str, str]:
        """Save composite outlook as JSON and CSV."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        alignment = composite.get("scenario_name", "composite")

        # JSON
        json_path = output_path / f"composite_{alignment}.json"
        with open(json_path, "w") as f:
            json.dump(composite, f, indent=2, default=str)
        logger.info(f"Saved composite JSON: {json_path}")

        # CSV
        csv_path = output_path / f"composite_{alignment}.csv"
        years = self.years
        variables = composite["variables"]
        provenance = composite.get("reconciliation", {}).get("provenance", {})

        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["variable", "unit", "category", "primary_source",
                      "confidence", "source_count"] + years
            writer.writerow(header)

            for var_key in sorted(variables.keys()):
                vdata = variables[var_key]
                annual = vdata.get("annual", {})
                if not annual:
                    continue

                cat_info = VARIABLE_CATALOG.get(var_key, {})
                prov = provenance.get(var_key, {})
                row = [
                    var_key,
                    cat_info.get("unit", "").replace(",", ";"),
                    cat_info.get("category", ""),
                    prov.get("primary_source", ""),
                    prov.get("confidence_score", ""),
                    prov.get("source_count", ""),
                ]
                for yr in years:
                    row.append(annual.get(yr, ""))
                writer.writerow(row)

        logger.info(f"Saved composite CSV: {csv_path}")

        # Conflict report
        conflicts_path = output_path / f"composite_{alignment}_conflicts.json"
        recon = composite.get("reconciliation", {})
        with open(conflicts_path, "w") as f:
            json.dump({
                "summary": recon.get("summary", {}),
                "conflicts": recon.get("conflicts", []),
            }, f, indent=2, default=str)
        logger.info(f"Saved conflict report: {conflicts_path}")

        return str(json_path), str(csv_path), str(conflicts_path)


# =============================================================================
# AUTO-DISCOVERY OF OUTPUT FILES
# =============================================================================

def discover_outlooks(output_dir: str) -> dict[str, list[tuple[str, str]]]:
    """
    Scan output directory for extractor output JSONs.
    Returns: {source_id: [(scenario_name, file_path), ...], ...}
    """
    output_path = Path(output_dir)
    found = {}

    patterns = {
        "eia_aeo": "eia_aeo_*_*.json",
        "nrel_atb": "nrel_atb_*_*.json",
        "iea_weo": "iea_weo_*_*.json",
        "ercot": "ercot_cdr_*_*.json",
    }

    for source_id, pattern in patterns.items():
        matches = sorted(output_path.glob(pattern))
        # Exclude validation/discovery/correlation files
        matches = [
            m for m in matches
            if not any(x in m.name for x in ("validation", "discovery", "correlation", "all_scenarios"))
        ]

        # When multiple editions exist (e.g. iea_weo_2024 and iea_weo_2025),
        # prefer the newest edition by sorting descending so newer files come first.
        # Then deduplicate by scenario name, keeping only the first (newest) entry.
        matches = sorted(matches, reverse=True)

        entries = []
        seen_scenarios = set()
        for m in matches:
            try:
                with open(m) as f:
                    data = json.load(f)
                scenario = data.get("scenario_name", m.stem.split("_")[-1])
                if scenario not in seen_scenarios:
                    entries.append((scenario, str(m)))
                    seen_scenarios.add(scenario)
            except (json.JSONDecodeError, KeyError):
                continue
        if entries:
            found[source_id] = entries

    return found


def load_outlook(file_path: str) -> dict:
    """Load a canonical outlook JSON file."""
    with open(file_path) as f:
        return json.load(f)


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Cross-Source Outlook Reconciliation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-discover and reconcile all available outlooks
  python3 reconcile_outlooks.py --output-dir output/

  # Specify individual source files
  python3 reconcile_outlooks.py --eia output/eia_aeo_2025_reference.json \\
                                --atb output/nrel_atb_2024_moderate.json \\
                                --weo output/iea_weo_2024_steps.json \\
                                --ercot output/ercot_cdr_2025_base.json

  # Reconcile a specific alignment
  python3 reconcile_outlooks.py --output-dir output/ --alignment net_zero
        """,
    )
    parser.add_argument("--output-dir", default="output",
                        help="Directory with extractor outputs and for reconciled outputs")
    parser.add_argument("--eia", help="EIA AEO outlook JSON path")
    parser.add_argument("--atb", help="NREL ATB outlook JSON path")
    parser.add_argument("--weo", help="IEA WEO outlook JSON path")
    parser.add_argument("--ercot", help="ERCOT CDR outlook JSON path")
    parser.add_argument("--alignment", default=None,
                        help="Scenario alignment to use. Options: "
                        + ", ".join(SCENARIO_ALIGNMENT.keys())
                        + " (default: all)")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    script_dir = Path(__file__).resolve().parent
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = script_dir / output_dir

    # If explicit files provided, use those
    explicit_sources = {}
    if args.eia:
        explicit_sources["eia_aeo"] = [("explicit", args.eia)]
    if args.atb:
        explicit_sources["nrel_atb"] = [("explicit", args.atb)]
    if args.weo:
        explicit_sources["iea_weo"] = [("explicit", args.weo)]
    if args.ercot:
        explicit_sources["ercot"] = [("explicit", args.ercot)]

    # Auto-discover if no explicit files
    if not explicit_sources:
        discovered = discover_outlooks(str(output_dir))
        if not discovered:
            print(f"No outlook files found in {output_dir}/")
            print("Run extractors first, or specify files with --eia, --atb, --weo, --ercot")
            sys.exit(1)
        print(f"Discovered sources:")
        for src, entries in discovered.items():
            scenarios = [e[0] for e in entries]
            print(f"  {src}: {scenarios}")
    else:
        discovered = explicit_sources

    # Determine which alignments to reconcile
    if args.alignment:
        alignments = [args.alignment]
    else:
        alignments = list(SCENARIO_ALIGNMENT.keys())

    for alignment_key in alignments:
        alignment = SCENARIO_ALIGNMENT.get(alignment_key, {})
        print(f"\n{'='*60}")
        print(f"Alignment: {alignment.get('label', alignment_key)}")
        print(f"{'='*60}")

        # Load the appropriate scenario from each source
        outlooks = {}
        for src_id in ["eia_aeo", "nrel_atb", "iea_weo", "ercot"]:
            if src_id not in discovered:
                continue

            target_scenario = alignment.get(src_id)
            entries = discovered[src_id]

            # Find matching scenario
            matched_file = None
            for scenario, fpath in entries:
                if scenario == target_scenario or scenario == "explicit":
                    matched_file = fpath
                    break

            if matched_file is None:
                # Use first available scenario as fallback
                if entries:
                    _, matched_file = entries[0]
                    logger.warning(f"  {src_id}: scenario '{target_scenario}' not found, "
                                   f"using '{entries[0][0]}'")

            if matched_file:
                try:
                    outlooks[src_id] = load_outlook(matched_file)
                    print(f"  Loaded: {src_id} ({Path(matched_file).name})")
                except Exception as e:
                    logger.error(f"  Failed to load {src_id}: {e}")

        if not outlooks:
            print("  No outlooks loaded — skipping")
            continue

        # Reconcile
        reconciler = OutlookReconciler(outlooks)
        composite = reconciler.reconcile(alignment_key)

        # Save
        json_path, csv_path, conflicts_path = reconciler.save_composite(
            composite, str(output_dir)
        )

        # Print summary
        recon = composite.get("reconciliation", {})
        summary = recon.get("summary", {})
        print(f"\n  Results:")
        print(f"    Populated: {summary.get('populated', 0)}/{summary.get('total_variables', 0)} "
              f"({summary.get('coverage_pct', 0)}%)")
        print(f"    By source: {summary.get('by_primary_source', {})}")
        print(f"    Avg confidence: {summary.get('avg_confidence', 0):.3f}")
        print(f"    Conflicts: {summary.get('conflicts_total', 0)} "
              f"({summary.get('conflicts_error', 0)} errors, "
              f"{summary.get('conflicts_warning', 0)} warnings)")
        print(f"    Output: {Path(json_path).name}")

    print(f"\nDone. Output files in {output_dir}/")


if __name__ == "__main__":
    main()
