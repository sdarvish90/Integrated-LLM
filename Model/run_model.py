"""
DecarbIQ Model Runner — PySAM + ProFAST
=========================================

CLI entry point that reads NREL ATB extractor JSON and EIA AEO JSON,
runs PySAM and ProFAST financial models, and produces enhanced outlook
files with properly modeled derived variables (LCOE, LCOS, LCOH).

Requires either EIA AEO JSON for fuel/electricity price projections
or explicit --ng-fuel-price and --electricity-price overrides.

Usage:
  # Single ATB×EIA pairing
  python3 run_model.py --atb-scenario moderate \\
      --eia-json ../outlook/output/eia_aeo_2025_reference.json

  # Matrix mode — all ATB × EIA combinations
  python3 run_model.py --matrix --eia-dir ../outlook/output/

  # Override fuel prices (flat, no EIA needed)
  python3 run_model.py --atb-scenario moderate \\
      --ng-fuel-price 4.50 --electricity-price 50

  # With IRA 45V PTC
  python3 run_model.py --atb-scenario advanced \\
      --eia-json ../outlook/output/eia_aeo_2025_high_ztc.json --with-45v

  # Compare model vs extractor derived values
  python3 run_model.py --atb-scenario moderate \\
      --eia-json ../outlook/output/eia_aeo_2025_reference.json --compare

Author: DecarbIQ / Eco Decarb Forward
"""

import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("decarbiq.model")

ATB_SCENARIOS = ["conservative", "moderate", "advanced"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(filepath: Path) -> dict:
    """Load and return a JSON file."""
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def find_atb_json(input_dir: Path, scenario: str, atb_year: int = 2024) -> Path:
    """Locate ATB extractor JSON for a given scenario."""
    filepath = input_dir / f"nrel_atb_{atb_year}_{scenario}.json"
    if not filepath.exists():
        raise FileNotFoundError(
            f"ATB extractor output not found: {filepath}\n"
            f"Run the extractor first: python3 nrel_atb_extractor.py --scenarios {scenario}"
        )
    return filepath


def find_eia_jsons(eia_dir: Path) -> list[Path]:
    """Find all EIA AEO scenario JSONs in a directory."""
    candidates = sorted(eia_dir.glob("eia_aeo_*_*.json"))
    # Exclude non-scenario files
    excluded = {"discovery", "implied_correlations", "validation", "all_scenarios"}
    result = []
    for p in candidates:
        stem = p.stem
        parts = stem.split("_")
        # eia_aeo_2025_reference → scenario = "reference"
        if len(parts) >= 4:
            scenario_part = "_".join(parts[3:])
            if scenario_part not in excluded:
                result.append(p)
    return result


def extract_series(outlook: dict, variable_key: str) -> dict[str, float]:
    """Extract year→value series from an outlook JSON variable."""
    var_data = outlook.get("variables", {}).get(variable_key, {})
    return var_data.get("annual", {})


def make_flat_price_series(
    price: float, start_year: int = 2024, end_year: int = 2050,
) -> dict[str, float]:
    """Create a flat price series from a single override value."""
    return {str(yr): price for yr in range(start_year, end_year + 1)}


def get_eia_scenario_name(eia_path: Path) -> str:
    """Extract EIA scenario name from filename."""
    stem = eia_path.stem
    parts = stem.split("_")
    if len(parts) >= 4:
        return "_".join(parts[3:])
    return stem


# ---------------------------------------------------------------------------
# Comparison report
# ---------------------------------------------------------------------------

def compare_derived_values(
    model_vars: dict, extractor_vars: dict, variable_keys: list[str],
) -> dict:
    """
    Compute deltas between model-computed and extractor-computed values.

    Returns dict with per-variable, per-year comparisons.
    """
    report = {}
    for key in variable_keys:
        model_ann = model_vars.get(key, {}).get("annual", {})
        ext_ann = extractor_vars.get(key, {}).get("annual", {})
        if not model_ann or not ext_ann:
            continue

        deltas = {}
        for yr in sorted(set(model_ann.keys()) & set(ext_ann.keys())):
            m = model_ann[yr]
            e = ext_ann[yr]
            if e and e != 0:
                pct = (m - e) / abs(e) * 100
            else:
                pct = None
            deltas[yr] = {
                "model": round(m, 4),
                "extractor": round(e, 4),
                "delta": round(m - e, 4),
                "delta_pct": round(pct, 2) if pct is not None else None,
            }

        if deltas:
            # Summary stats
            pcts = [d["delta_pct"] for d in deltas.values() if d["delta_pct"] is not None]
            report[key] = {
                "years": deltas,
                "avg_delta_pct": round(sum(pcts) / len(pcts), 2) if pcts else None,
                "max_delta_pct": round(max(pcts), 2) if pcts else None,
                "min_delta_pct": round(min(pcts), 2) if pcts else None,
            }

    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_model_json(outlook: dict, output_path: Path):
    """Save model-enhanced outlook as JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(outlook, f, indent=2, default=str)
    logger.info(f"  Saved: {output_path}")


def save_model_csv(outlook: dict, output_path: Path):
    """Save model-enhanced outlook as wide-format CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    vars_ = outlook.get("variables", {})

    # Collect all years
    all_years = set()
    for var_data in vars_.values():
        all_years.update(var_data.get("annual", {}).keys())
    years_sorted = sorted(all_years)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["variable", "unit", "category", "description"] + years_sorted
        writer.writerow(header)

        for var_key in sorted(vars_.keys()):
            var_data = vars_[var_key]
            annual = var_data.get("annual", {})
            row = [
                var_key,
                var_data.get("unit", ""),
                var_data.get("category", ""),
                var_data.get("description", ""),
            ]
            for yr in years_sorted:
                row.append(annual.get(yr, ""))
            writer.writerow(row)

    logger.info(f"  Saved: {output_path}")


def save_scenario_matrix(
    all_results: list[dict], output_path: Path,
):
    """
    Save all ATB×EIA scenario combinations as a wide-format matrix CSV.

    Indexed by (atb_scenario, eia_scenario, variable, year).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Collect all years
    all_years = set()
    for r in all_results:
        for var_data in r["outlook"].get("variables", {}).values():
            all_years.update(var_data.get("annual", {}).keys())
    years_sorted = sorted(all_years)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["atb_scenario", "eia_scenario", "variable", "unit"] + years_sorted
        writer.writerow(header)

        for r in all_results:
            atb_s = r["atb_scenario"]
            eia_s = r["eia_scenario"]
            vars_ = r["outlook"].get("variables", {})
            for var_key in sorted(vars_.keys()):
                var_data = vars_[var_key]
                annual = var_data.get("annual", {})
                if not annual:
                    continue
                row = [atb_s, eia_s, var_key, var_data.get("unit", "")]
                for yr in years_sorted:
                    row.append(annual.get(yr, ""))
                writer.writerow(row)

    logger.info(f"  Saved scenario matrix: {output_path} ({len(all_results)} combinations)")


# ---------------------------------------------------------------------------
# Core model runner
# ---------------------------------------------------------------------------

def run_model_for_pair(
    atb_outlook: dict,
    eia_outlook: dict | None,
    atb_scenario: str,
    eia_scenario: str,
    ng_price_override: float | None,
    elec_price_override: float | None,
    enable_45v: bool,
) -> dict:
    """
    Run all model computations for one (ATB, EIA) scenario pair.

    Returns the enhanced outlook dict.
    """
    # Import here to allow running from this directory
    from compute_lcoe import compute_all_lcoe
    from compute_lcoh import compute_lcoh_series
    from compute_lcos import compute_battery_lcos_series

    vars_ = atb_outlook["variables"]

    # --- Extract fuel/electricity price series ---
    if ng_price_override is not None:
        ng_prices = make_flat_price_series(ng_price_override)
        ng_source = f"override: ${ng_price_override}/MMBtu (flat)"
    elif eia_outlook:
        ng_prices = extract_series(eia_outlook, "ng_price_electric_power")
        ng_source = f"EIA AEO {eia_scenario}"
    else:
        ng_prices = {}
        ng_source = "MISSING"

    if elec_price_override is not None:
        elec_prices = make_flat_price_series(elec_price_override)
        elec_source = f"override: ${elec_price_override}/MWh (flat)"
    elif eia_outlook:
        elec_prices = extract_series(eia_outlook, "electricity_price_wholesale")
        # Fallback to industrial if wholesale not available
        if not elec_prices:
            elec_prices = extract_series(eia_outlook, "electricity_price_industrial")
        elec_source = f"EIA AEO {eia_scenario}"
    else:
        elec_prices = {}
        elec_source = "MISSING"

    # Validate
    if not ng_prices:
        raise ValueError(
            "No natural gas price data available. Provide --eia-json or --ng-fuel-price."
        )
    if not elec_prices:
        raise ValueError(
            "No electricity price data available. Provide --eia-json or --electricity-price."
        )

    # Warn about flat overrides
    if ng_price_override is not None or elec_price_override is not None:
        logger.warning(
            "Using flat fuel/electricity price overrides. This creates an inconsistency "
            "with declining ATB CAPEX projections — consider using EIA AEO projections."
        )

    # Save original extractor values for comparison
    extractor_snapshot = {
        key: {"annual": dict(vars_.get(key, {}).get("annual", {}))}
        for key in ["ngcc_lcoe", "ngcc_ccs_lcoe", "battery_utility_lcos",
                     "battery_utility_rte", "lcoh_green_electrolysis",
                     "lcoh_gray_smr", "lcoh_blue_smr_ccs"]
    }

    t_start = time.perf_counter()

    # --- 1. NGCC LCOE ---
    logger.info("Computing NGCC LCOE via PySAM...")
    lcoe_results = compute_all_lcoe(atb_outlook, ng_prices)
    for key, series in lcoe_results.items():
        if key in vars_:
            vars_[key]["annual"] = series

    # --- 2. Battery LCOS + RTE ---
    logger.info("Computing battery LCOS via PySAM...")
    lcos_series, rte_series = compute_battery_lcos_series(atb_outlook, elec_prices)
    if "battery_utility_lcos" in vars_:
        vars_["battery_utility_lcos"]["annual"] = lcos_series
    if "battery_utility_rte" in vars_:
        vars_["battery_utility_rte"]["annual"] = rte_series

    # --- 3. LCOH via ProFAST ---
    logger.info("Computing hydrogen LCOH via ProFAST...")

    # Grid carbon intensity for green H2 CI
    grid_ci = extract_series(eia_outlook, "grid_emissions_intensity") if eia_outlook else None

    # 45V policy data
    policy_45v = None
    if eia_outlook:
        policy_45v = extract_series(eia_outlook, "ira_45v_h2_ptc")
        if policy_45v:
            enable_45v = True  # Auto-enable if policy data available

    lcoh_results = compute_lcoh_series(
        outlook=atb_outlook,
        ng_price_series=ng_prices,
        electricity_price_series=elec_prices,
        grid_ci_series=grid_ci,
        policy_45v_series=policy_45v if enable_45v else None,
        enable_45v=enable_45v,
    )

    # Write LCOH results back into outlook variables
    lcoh_var_keys = ["lcoh_green_electrolysis", "lcoh_gray_smr", "lcoh_blue_smr_ccs"]
    for key in lcoh_var_keys:
        if key in vars_ and key in lcoh_results:
            vars_[key]["annual"] = lcoh_results[key]

    # Write carbon intensity into outlook
    ci_mapping = {
        "ci_green": "green_pem",
        "ci_gray": "gray_smr",
        "ci_blue": "blue_smr_ccs",
    }
    h2_ci_data = {}
    for ci_key, pathway in ci_mapping.items():
        if ci_key in lcoh_results:
            h2_ci_data[pathway] = lcoh_results[ci_key]
    if h2_ci_data:
        if "h2_carbon_intensity_by_pathway" not in vars_:
            vars_["h2_carbon_intensity_by_pathway"] = {
                "unit": "kg CO2e/kg H2",
                "description": "H2 production carbon intensity by pathway",
                "category": "emissions",
                "annual": {},
            }
        vars_["h2_carbon_intensity_by_pathway"]["annual"] = h2_ci_data

    # Write post-subsidy LCOH if computed
    for sub_key in ["lcoh_green_post_subsidy", "lcoh_blue_post_subsidy"]:
        if sub_key in lcoh_results and lcoh_results[sub_key]:
            vars_[sub_key] = {
                "unit": "$/kg H2 (real 2024 USD)",
                "description": f"Post-45V-PTC {sub_key.replace('_post_subsidy', '')}",
                "category": "costs",
                "annual": lcoh_results[sub_key],
            }

    t_end = time.perf_counter()

    # --- 4. Model metadata ---
    atb_outlook["model_metadata"] = {
        "model_version": "0.1.0",
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "compute_time_seconds": round(t_end - t_start, 2),
        "scenario_pairing": {
            "atb_scenario": atb_scenario,
            "eia_scenario": eia_scenario,
        },
        "fuel_price_source": {
            "natural_gas": ng_source,
            "electricity": elec_source,
        },
        "computation_methods": {
            "ngcc_lcoe": "PySAM.Lcoefcr (FCR = CRF * PFF * CFF)",
            "ngcc_ccs_lcoe": "PySAM.Lcoefcr (FCR = CRF * PFF * CFF)",
            "battery_utility_lcos": "PySAM.Lcoefcr (FCR method, MACRS-7)",
            "battery_utility_rte": "Linear interpolation (0.86→0.88, 2024→2050)",
            "lcoh_green_electrolysis": "ProFAST GAAP DCF (PEM electrolysis)",
            "lcoh_gray_smr": "ProFAST GAAP DCF (SMR, unabated)",
            "lcoh_blue_smr_ccs": "ProFAST GAAP DCF (SMR + 95% CCS)",
        },
        "45v_ptc_applied": enable_45v,
    }

    # Store extractor snapshot for comparison
    atb_outlook["_extractor_snapshot"] = extractor_snapshot

    logger.info(f"  Model computation complete in {t_end - t_start:.2f}s")
    return atb_outlook


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="DecarbIQ Model Runner — PySAM + ProFAST",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Single pairing
  python3 run_model.py --atb-scenario moderate \\
      --eia-json ../outlook/output/eia_aeo_2025_reference.json

  # Matrix mode
  python3 run_model.py --matrix --eia-dir ../outlook/output/

  # With fuel price overrides
  python3 run_model.py --atb-scenario moderate \\
      --ng-fuel-price 4.50 --electricity-price 50
""",
    )

    # Input sources
    parser.add_argument(
        "--atb-dir", default="../outlook/output",
        help="Directory containing ATB extractor JSON output (default: ../outlook/output)",
    )
    parser.add_argument(
        "--atb-scenario", default=None,
        help="ATB scenario: conservative, moderate, or advanced",
    )
    parser.add_argument(
        "--atb-year", type=int, default=2024,
        help="ATB year (default: 2024)",
    )
    parser.add_argument(
        "--eia-json", default=None,
        help="Path to a single EIA AEO scenario JSON file",
    )
    parser.add_argument(
        "--eia-dir", default=None,
        help="Directory containing EIA AEO scenario JSONs (for --matrix mode)",
    )

    # Fuel price overrides
    parser.add_argument(
        "--ng-fuel-price", type=float, default=None,
        help="Override NG price $/MMBtu (flat across all years)",
    )
    parser.add_argument(
        "--electricity-price", type=float, default=None,
        help="Override electricity price $/MWh (flat across all years)",
    )

    # Policy
    parser.add_argument(
        "--with-45v", action="store_true",
        help="Apply IRA 45V hydrogen PTC based on carbon intensity tiers",
    )

    # Matrix mode
    parser.add_argument(
        "--matrix", action="store_true",
        help="Run all ATB × EIA scenario combinations",
    )

    # Output
    parser.add_argument(
        "--output-dir", default="./output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--compare", action="store_true",
        help="Show delta between model and extractor derived values",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    atb_dir = Path(args.atb_dir)
    output_dir = Path(args.output_dir)

    # Validate inputs
    has_eia = args.eia_json is not None or args.eia_dir is not None
    has_overrides = args.ng_fuel_price is not None and args.electricity_price is not None

    if not has_eia and not has_overrides:
        parser.error(
            "Fuel price source required. Provide either:\n"
            "  --eia-json <path>  (EIA AEO scenario JSON)\n"
            "  --eia-dir <path>   (directory with EIA JSONs, for --matrix)\n"
            "  --ng-fuel-price <$> --electricity-price <$>  (flat overrides)\n"
        )

    # --- Matrix mode ---
    if args.matrix:
        eia_dir = Path(args.eia_dir or args.atb_dir)
        eia_files = find_eia_jsons(eia_dir)
        if not eia_files:
            parser.error(f"No EIA AEO scenario JSONs found in {eia_dir}")

        logger.info(
            f"Matrix mode: {len(ATB_SCENARIOS)} ATB × {len(eia_files)} EIA "
            f"= {len(ATB_SCENARIOS) * len(eia_files)} combinations"
        )

        all_results = []
        t_total_start = time.perf_counter()

        for atb_scenario in ATB_SCENARIOS:
            atb_path = find_atb_json(atb_dir, atb_scenario, args.atb_year)
            atb_data = load_json(atb_path)

            for eia_path in eia_files:
                eia_scenario = get_eia_scenario_name(eia_path)
                eia_data = load_json(eia_path)

                logger.info(f"\n{'='*60}")
                logger.info(f"Pairing: ATB {atb_scenario} × EIA {eia_scenario}")
                logger.info(f"{'='*60}")

                # Deep copy ATB data to avoid mutation across pairings
                import copy
                atb_copy = copy.deepcopy(atb_data)

                outlook = run_model_for_pair(
                    atb_outlook=atb_copy,
                    eia_outlook=eia_data,
                    atb_scenario=atb_scenario,
                    eia_scenario=eia_scenario,
                    ng_price_override=args.ng_fuel_price,
                    elec_price_override=args.electricity_price,
                    enable_45v=args.with_45v,
                )

                # Save individual files
                base = f"nrel_atb_{args.atb_year}_{atb_scenario}_{eia_scenario}_modeled"
                save_model_json(outlook, output_dir / f"{base}.json")
                save_model_csv(outlook, output_dir / f"{base}.csv")

                all_results.append({
                    "atb_scenario": atb_scenario,
                    "eia_scenario": eia_scenario,
                    "outlook": outlook,
                })

        # Save matrix CSV
        save_scenario_matrix(
            all_results,
            output_dir / f"scenario_matrix_{args.atb_year}.csv",
        )

        t_total = time.perf_counter() - t_total_start
        logger.info(f"\nMatrix complete: {len(all_results)} combinations in {t_total:.1f}s")
        return

    # --- Single pairing mode ---
    atb_scenarios = [args.atb_scenario] if args.atb_scenario else ATB_SCENARIOS

    eia_data = None
    eia_scenario = "override"
    if args.eia_json:
        eia_path = Path(args.eia_json)
        if not eia_path.exists():
            parser.error(f"EIA JSON not found: {eia_path}")
        eia_data = load_json(eia_path)
        eia_scenario = get_eia_scenario_name(eia_path)

    for atb_scenario in atb_scenarios:
        logger.info(f"\n{'='*60}")
        logger.info(f"ATB scenario: {atb_scenario} | EIA scenario: {eia_scenario}")
        logger.info(f"{'='*60}")

        atb_path = find_atb_json(atb_dir, atb_scenario, args.atb_year)
        atb_data = load_json(atb_path)

        # Store original for comparison
        import copy
        extractor_vars = copy.deepcopy(atb_data.get("variables", {}))

        outlook = run_model_for_pair(
            atb_outlook=atb_data,
            eia_outlook=eia_data,
            atb_scenario=atb_scenario,
            eia_scenario=eia_scenario,
            ng_price_override=args.ng_fuel_price,
            elec_price_override=args.electricity_price,
            enable_45v=args.with_45v,
        )

        # Save output
        base = f"nrel_atb_{args.atb_year}_{atb_scenario}_{eia_scenario}_modeled"
        save_model_json(outlook, output_dir / f"{base}.json")
        save_model_csv(outlook, output_dir / f"{base}.csv")

        # Comparison report
        if args.compare:
            derived_keys = [
                "ngcc_lcoe", "ngcc_ccs_lcoe",
                "battery_utility_lcos", "battery_utility_rte",
                "lcoh_green_electrolysis", "lcoh_gray_smr", "lcoh_blue_smr_ccs",
            ]
            report = compare_derived_values(
                outlook["variables"], extractor_vars, derived_keys,
            )

            print(f"\n{'='*70}")
            print(f"COMPARISON: Model (PySAM/ProFAST) vs Extractor (simplified)")
            print(f"Scenario: ATB {atb_scenario} × EIA {eia_scenario}")
            print(f"{'='*70}")

            for key, data in report.items():
                avg = data.get("avg_delta_pct")
                sample_years = ["2024", "2030", "2040", "2050"]
                samples = []
                for yr in sample_years:
                    if yr in data["years"]:
                        d = data["years"][yr]
                        samples.append(
                            f"{yr}: ${d['model']:.1f} vs ${d['extractor']:.1f} "
                            f"({d['delta_pct']:+.1f}%)"
                        )
                print(f"\n  {key} (avg delta: {avg:+.1f}%):")
                for s in samples:
                    print(f"    {s}")

            # Save comparison JSON
            comp_path = output_dir / f"comparison_{atb_scenario}_{eia_scenario}.json"
            with open(comp_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
            print(f"\n  Full comparison saved: {comp_path}")

    logger.info("\nDone.")


if __name__ == "__main__":
    main()
