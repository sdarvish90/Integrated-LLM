"""
DecarbIQ Futures Benchmark Validation Module
=============================================

Benchmarks Monte Carlo output against NYMEX Henry Hub futures strip,
EIA AEO reference case, analyst consensus ranges, and historical
electricity price benchmarks for ERCOT and CAISO.

Flags any model P50 that diverges >20% from liquid market references.

Pipeline integration
--------------------
This module is designed to slot into the policy-regulation update pipeline
as **step 15** (after step14_expanded_validation).  It follows the same
conventions:

    * function signature:  step15_futures_benchmark_validation(mc_results,
          projection_map, literature_path=None, output_dir=None)
    * returns a dict that is also persisted as JSON
    * status strings: "PASS", "HIGH", "LOW", "DIVERGENT"
    * uses banner() / sub-step print patterns

It can also be run standalone:

    python decarbiq_futures_benchmark_validation.py

Dependencies: only stdlib + json/os/datetime (no numpy/pandas required).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Path constants – mirror the pipeline's BASE / file-path pattern
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Default file locations (overridable via function params)
_DEFAULT_MC_PATH = os.path.join(
    _THIS_DIR, "geopolitical_and_macro", "monte_carlo_lng_v8.json"
)
_DEFAULT_PROJECTION_PATH = os.path.join(
    _THIS_DIR, "decarbiq_projection_map.json"
)
_DEFAULT_LITERATURE_PATH = os.path.join(
    _THIS_DIR, "literature_benchmarks.json"
)
_DEFAULT_OUTPUT_PATH = os.path.join(
    _THIS_DIR, "futures_benchmark_validation.json"
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DIVERGENCE_THRESHOLD_PCT = 20.0  # flag if |model − reference| / reference > 20%

# Forecast years to validate (intersection of MC output and benchmarks)
FORECAST_YEARS = ["2026", "2027", "2028", "2029", "2030"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def banner(text: str) -> None:
    """Pipeline-style step banner."""
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  {text}")
    print(sep)


def _pct_divergence(model_val: float, ref_val: float) -> float | None:
    """Signed percentage divergence: positive means model is ABOVE reference."""
    if ref_val == 0:
        return None
    return ((model_val - ref_val) / abs(ref_val)) * 100.0


def _status(pct: float | None, threshold: float = DIVERGENCE_THRESHOLD_PCT) -> str:
    """Return status string based on divergence magnitude and direction."""
    if pct is None:
        return "NO_REF"
    abs_pct = abs(pct)
    if abs_pct <= threshold:
        return "PASS"
    return "HIGH" if pct > 0 else "LOW"


def _get_mc_p50(annual_forecast: dict) -> float | None:
    """Extract P50 from a single-year MC forecast dict.

    Handles the naming asymmetry: annual_forecasts stores the 50th
    percentile as ``"median"`` while confidence_bands uses ``"p50"``.
    """
    return annual_forecast.get(
        "median",
        annual_forecast.get("p50", None),
    )


def _load_json(path: str) -> dict:
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 15-A  Gas price: MC P50 vs NYMEX futures strip
# ---------------------------------------------------------------------------
def _validate_gas_vs_nymex(
    mc_forecasts: dict,
    nymex: dict,
) -> dict:
    """Compare MC gas-price P50/mean against NYMEX Henry Hub futures strip.

    Parameters
    ----------
    mc_forecasts : dict
        ``monte_carlo_lng_v8["annual_forecasts"]`` keyed by year string.
    nymex : dict
        ``literature_benchmarks["gas_price_forecasts"]["nymex_futures_strip"]``
        with ``"values"`` sub-dict keyed by year string and ``"as_of"`` date.

    Returns
    -------
    dict with per-year comparison and aggregate flag.
    """
    results: dict[str, Any] = {
        "reference": "NYMEX Henry Hub Futures Strip",
        "as_of": nymex.get("as_of", "unknown"),
        "unit": nymex.get("unit", "$/MMBtu"),
        "years": {},
        "any_divergent": False,
        "divergent_years": [],
    }

    nymex_vals = nymex.get("values", {})

    for yr in FORECAST_YEARS:
        mc_yr = mc_forecasts.get(yr)
        ref_price = nymex_vals.get(yr)
        if mc_yr is None or ref_price is None:
            continue

        p50 = _get_mc_p50(mc_yr)
        mean = mc_yr.get("mean")

        p50_div = _pct_divergence(p50, ref_price) if p50 is not None else None
        mean_div = _pct_divergence(mean, ref_price) if mean is not None else None

        p50_status = _status(p50_div)
        mean_status = _status(mean_div)

        entry = {
            "nymex_futures": ref_price,
            "model_p50": round(p50, 4) if p50 is not None else None,
            "model_mean": round(mean, 4) if mean is not None else None,
            "p50_divergence_pct": round(p50_div, 2) if p50_div is not None else None,
            "mean_divergence_pct": round(mean_div, 2) if mean_div is not None else None,
            "p50_status": p50_status,
            "mean_status": mean_status,
        }
        results["years"][yr] = entry

        if p50_status in ("HIGH", "LOW"):
            results["any_divergent"] = True
            results["divergent_years"].append(yr)

    return results


# ---------------------------------------------------------------------------
# 15-B  Gas price: MC P50 vs EIA AEO reference case
# ---------------------------------------------------------------------------
def _validate_gas_vs_eia_aeo(
    mc_forecasts: dict,
    eia_aeo: dict,
) -> dict:
    """Compare MC gas-price P50/mean against EIA AEO Reference Case."""
    results: dict[str, Any] = {
        "reference": eia_aeo.get("source", "EIA AEO Reference Case"),
        "unit": eia_aeo.get("unit", "$/MMBtu"),
        "years": {},
        "any_divergent": False,
        "divergent_years": [],
    }

    eia_vals = eia_aeo.get("values", {})

    for yr in FORECAST_YEARS:
        mc_yr = mc_forecasts.get(yr)
        ref_price = eia_vals.get(yr)
        if mc_yr is None or ref_price is None:
            continue

        p50 = _get_mc_p50(mc_yr)
        mean = mc_yr.get("mean")

        p50_div = _pct_divergence(p50, ref_price) if p50 is not None else None
        mean_div = _pct_divergence(mean, ref_price) if mean is not None else None

        entry = {
            "eia_aeo_ref": ref_price,
            "model_p50": round(p50, 4) if p50 is not None else None,
            "model_mean": round(mean, 4) if mean is not None else None,
            "p50_divergence_pct": round(p50_div, 2) if p50_div is not None else None,
            "mean_divergence_pct": round(mean_div, 2) if mean_div is not None else None,
            "p50_status": _status(p50_div),
            "mean_status": _status(mean_div),
        }
        results["years"][yr] = entry

        if entry["p50_status"] in ("HIGH", "LOW"):
            results["any_divergent"] = True
            results["divergent_years"].append(yr)

    return results


# ---------------------------------------------------------------------------
# 15-C  Gas price: MC P50 vs analyst consensus range
# ---------------------------------------------------------------------------
def _validate_gas_vs_consensus(
    mc_forecasts: dict,
    consensus: dict,
) -> dict:
    """Compare MC P50 against analyst consensus low/mid/high band.

    A P50 that falls *within* [low, high] is PASS.
    Outside the band is flagged with divergence computed against the
    nearest bound (low or high).
    """
    results: dict[str, Any] = {
        "reference": "Analyst Consensus (EIA, NYMEX, IHS Markit, Wood Mackenzie)",
        "years": {},
        "any_divergent": False,
        "divergent_years": [],
    }

    for yr in FORECAST_YEARS:
        mc_yr = mc_forecasts.get(yr)
        cons_yr = consensus.get(yr)
        if mc_yr is None or cons_yr is None:
            continue

        p50 = _get_mc_p50(mc_yr)
        mean = mc_yr.get("mean")
        c_low = cons_yr.get("low")
        c_mid = cons_yr.get("mid")
        c_high = cons_yr.get("high")

        if p50 is None or c_low is None or c_high is None or c_mid is None:
            continue

        # Divergence vs mid-point
        p50_div_mid = _pct_divergence(p50, c_mid)
        mean_div_mid = _pct_divergence(mean, c_mid) if mean is not None else None

        # Band check: inside [low, high] is PASS regardless of mid divergence
        if c_low <= p50 <= c_high:
            band_status = "PASS"
            band_divergence = 0.0
        elif p50 > c_high:
            band_status = "HIGH"
            band_divergence = _pct_divergence(p50, c_high)
        else:
            band_status = "LOW"
            band_divergence = _pct_divergence(p50, c_low)

        # For the >20% flag, use divergence from the *nearest bound*
        flagged = band_status != "PASS" and abs(band_divergence or 0) > DIVERGENCE_THRESHOLD_PCT

        entry = {
            "consensus_low": c_low,
            "consensus_mid": c_mid,
            "consensus_high": c_high,
            "model_p50": round(p50, 4),
            "model_mean": round(mean, 4) if mean is not None else None,
            "p50_vs_mid_pct": round(p50_div_mid, 2) if p50_div_mid is not None else None,
            "mean_vs_mid_pct": round(mean_div_mid, 2) if mean_div_mid is not None else None,
            "band_status": band_status,
            "band_divergence_pct": round(band_divergence, 2) if band_divergence is not None else None,
            "flagged": flagged,
        }
        results["years"][yr] = entry

        if flagged:
            results["any_divergent"] = True
            results["divergent_years"].append(yr)

    return results


# ---------------------------------------------------------------------------
# 15-D  Electricity: ERCOT projections vs historical benchmarks
# ---------------------------------------------------------------------------
def _validate_electricity(
    elec_projections: dict,
    elec_benchmarks: dict,
    market: str,
) -> dict:
    """Compare electricity price projections against historical typical range.

    Parameters
    ----------
    elec_projections : dict
        ``projection_map["electricity_price_projections"][market]`` keyed by
        year string with ``mean``, ``low``, ``high`` sub-keys.
    elec_benchmarks : dict
        ``literature_benchmarks["electricity_price_benchmarks"][market.lower()]``
        with ``typical_range`` and ``historical_avg_2019_2024``.
    market : str
        ``"ERCOT"`` or ``"CAISO"``.
    """
    typical = elec_benchmarks.get("typical_range", [None, None])
    typical_low = typical[0] if typical else None
    typical_high = typical[1] if len(typical) > 1 else None

    # Compute a forward reference: use the midpoint of the typical range
    # as the stable benchmark, and historical average as a sanity anchor.
    hist_vals = elec_benchmarks.get("historical_avg_2019_2024", {}).get("values", {})
    hist_prices = [v for v in hist_vals.values() if v is not None]
    hist_avg = sum(hist_prices) / len(hist_prices) if hist_prices else None

    results: dict[str, Any] = {
        "market": market,
        "typical_range": typical,
        "historical_avg_2019_2024": round(hist_avg, 2) if hist_avg is not None else None,
        "years": {},
        "any_divergent": False,
        "divergent_years": [],
    }

    for yr in FORECAST_YEARS:
        proj = elec_projections.get(yr)
        if proj is None:
            continue

        proj_mean = proj.get("mean")
        if proj_mean is None:
            continue

        # Primary check: is the projected mean within 20% of the typical
        # range high-end?  Energy prices trend upward, so we benchmark
        # against the TOP of the typical range, not the midpoint.
        ref_val = typical_high
        div_pct = _pct_divergence(proj_mean, ref_val) if ref_val else None

        # Band check against typical range
        if typical_low is not None and typical_high is not None:
            if typical_low <= proj_mean <= typical_high:
                band_status = "PASS"
            elif proj_mean > typical_high:
                band_status = "HIGH"
            else:
                band_status = "LOW"
        else:
            band_status = "NO_REF"

        flagged = (
            band_status in ("HIGH", "LOW")
            and div_pct is not None
            and abs(div_pct) > DIVERGENCE_THRESHOLD_PCT
        )

        entry = {
            "projected_mean": round(proj_mean, 2),
            "projected_low": round(proj.get("low", 0), 2),
            "projected_high": round(proj.get("high", 0), 2),
            "gas_component": round(proj.get("gas_component", 0), 2),
            "typical_range_high": typical_high,
            "divergence_vs_typical_high_pct": round(div_pct, 2) if div_pct is not None else None,
            "band_status": band_status,
            "flagged": flagged,
        }
        results["years"][yr] = entry

        if flagged:
            results["any_divergent"] = True
            results["divergent_years"].append(yr)

    return results


# ---------------------------------------------------------------------------
# 15-E  Monthly confidence band sanity check
# ---------------------------------------------------------------------------
def _validate_confidence_bands(
    bands: dict,
    production_floor: dict,
) -> dict:
    """Check monthly MC confidence bands for economic sanity.

    Verifies:
    1. P5 stays above the production economics floor (marginal shut-in cost)
    2. P95 stays below an extreme-but-plausible ceiling
    3. Band widths are monotonically increasing with horizon
    """
    floor_mid = production_floor.get("breakeven_estimates", {}).get(
        "overall_us_marginal", {}
    ).get("mid", 2.0)

    p5_band = bands.get("p5", [])
    p50_band = bands.get("p50", [])
    p95_band = bands.get("p95", [])

    violations: list[str] = []
    floor_breaches = 0
    ceiling_breaches = 0
    # Reasonable ceiling: $25/MMBtu sustained monthly average is extreme
    # (Uri was ~$24 for ONE month).  Flag sustained above $20.
    ceiling = 20.0

    for month_idx in range(1, min(len(p5_band), len(p95_band))):
        p5_val = p5_band[month_idx]
        p95_val = p95_band[month_idx]

        if p5_val < floor_mid * 0.5:  # P5 below half the shut-in cost
            floor_breaches += 1
        if p95_val > ceiling:
            ceiling_breaches += 1

    # Band width monotonicity (should widen with horizon)
    widths = []
    for i in range(1, min(len(p5_band), len(p95_band))):
        widths.append(p95_band[i] - p5_band[i])

    non_monotonic_count = 0
    # Check in 12-month rolling windows (seasonal contractions are OK)
    for i in range(12, len(widths)):
        if widths[i] < widths[i - 12] * 0.8:  # >20% narrower than a year ago
            non_monotonic_count += 1

    if floor_breaches > 6:
        violations.append(
            f"P5 band breaches production floor ({floor_mid * 0.5:.2f}) "
            f"in {floor_breaches}/{len(p5_band) - 1} months"
        )
    if ceiling_breaches > 6:
        violations.append(
            f"P95 band exceeds ${ceiling}/MMBtu ceiling "
            f"in {ceiling_breaches}/{len(p95_band) - 1} months"
        )
    if non_monotonic_count > 6:
        violations.append(
            f"Band width contracts year-over-year in {non_monotonic_count} months"
        )

    return {
        "production_floor_mid": floor_mid,
        "extreme_ceiling": ceiling,
        "total_months": len(p5_band) - 1,
        "p5_floor_breaches": floor_breaches,
        "p95_ceiling_breaches": ceiling_breaches,
        "band_width_issues": non_monotonic_count,
        "violations": violations,
        "status": "PASS" if not violations else "FLAGGED",
    }


# ---------------------------------------------------------------------------
# 15-F  LNG-adjusted mean sanity check
# ---------------------------------------------------------------------------
def _validate_lng_adjusted_means(
    lng_curve: dict,
    nymex_vals: dict,
    consensus: dict,
) -> dict:
    """Check whether the LNG capacity curve produces realistic base prices.

    The LNG-adjusted annual means are the starting point for MC simulation.
    If these are already 2-3x above market references, the entire MC
    distribution will be shifted upward.
    """
    adjusted_means = lng_curve.get("annual_lng_adjusted_means", {})
    utilization = lng_curve.get("utilization_by_year", {})

    results: dict[str, Any] = {
        "description": "LNG capacity-curve adjusted mean vs market references",
        "years": {},
        "any_divergent": False,
        "divergent_years": [],
        "regime_distribution": {},
    }

    # Summarize regime distribution
    regime_counts: dict[str, int] = {}
    for yr, util_data in utilization.items():
        regime = util_data.get("market_regime", "unknown")
        regime_counts[regime] = regime_counts.get(regime, 0) + 1
    results["regime_distribution"] = regime_counts

    for yr in FORECAST_YEARS:
        adj_mean = adjusted_means.get(yr)
        nymex_ref = nymex_vals.get(yr)
        cons_yr = consensus.get(yr, {})
        cons_mid = cons_yr.get("mid")

        if adj_mean is None:
            continue

        # Compare against NYMEX as primary reference
        ref = nymex_ref or cons_mid
        if ref is None:
            continue

        div_pct = _pct_divergence(adj_mean, ref)
        status = _status(div_pct)

        util_yr = utilization.get(yr, {})
        entry = {
            "lng_adjusted_mean": round(adj_mean, 4),
            "nymex_ref": nymex_ref,
            "consensus_mid": cons_mid,
            "primary_ref_used": round(ref, 4),
            "divergence_pct": round(div_pct, 2) if div_pct is not None else None,
            "status": status,
            "utilization_pct": util_yr.get("utilization_pct"),
            "market_regime": util_yr.get("market_regime"),
        }
        results["years"][yr] = entry

        if status in ("HIGH", "LOW"):
            results["any_divergent"] = True
            results["divergent_years"].append(yr)

    return results


# ---------------------------------------------------------------------------
# Aggregate summary + recommendation engine
# ---------------------------------------------------------------------------
def _build_summary(sections: dict) -> dict:
    """Build an aggregate summary with overall status and actionable flags."""
    all_flags: list[str] = []
    section_statuses: dict[str, str] = {}

    for name, section in sections.items():
        if isinstance(section, dict):
            divergent = section.get("any_divergent", False)
            violations = section.get("violations", [])
            status = section.get("status")

            if divergent or (isinstance(violations, list) and len(violations) > 0):
                section_statuses[name] = "FLAGGED"
            elif status == "FLAGGED":
                section_statuses[name] = "FLAGGED"
            else:
                section_statuses[name] = "PASS"

            # Collect specific divergent years
            for yr in section.get("divergent_years", []):
                all_flags.append(f"{name}/{yr}")

    n_flagged = sum(1 for s in section_statuses.values() if s == "FLAGGED")
    n_total = len(section_statuses)

    if n_flagged == 0:
        overall = "PASS"
        recommendation = (
            "All Monte Carlo outputs are within 20% of liquid market references. "
            "No calibration adjustment needed."
        )
    elif n_flagged <= 2:
        overall = "WARNING"
        recommendation = (
            f"{n_flagged}/{n_total} validation sections flagged. "
            "Review the flagged sections and consider whether the divergence "
            "is driven by a known structural assumption (e.g., LNG capacity curve "
            "regime classification). Targeted recalibration recommended."
        )
    else:
        overall = "CRITICAL"
        recommendation = (
            f"{n_flagged}/{n_total} validation sections flagged. "
            "Model outputs diverge materially from market references across "
            "multiple dimensions. Systematic recalibration required before "
            "using these projections for project economics. Priority: "
            "1) Fix LNG capacity curve regime thresholds, "
            "2) Re-estimate GARCH on post-shale data, "
            "3) Anchor MC starting distribution to current futures strip."
        )

    return {
        "overall_status": overall,
        "sections_flagged": n_flagged,
        "sections_total": n_total,
        "section_statuses": section_statuses,
        "flagged_items": all_flags,
        "recommendation": recommendation,
        "divergence_threshold_pct": DIVERGENCE_THRESHOLD_PCT,
    }


# ---------------------------------------------------------------------------
# Main pipeline step
# ---------------------------------------------------------------------------
def step15_futures_benchmark_validation(
    mc_results: dict | None = None,
    projection_map: dict | None = None,
    literature: dict | None = None,
    mc_path: str = _DEFAULT_MC_PATH,
    projection_path: str = _DEFAULT_PROJECTION_PATH,
    literature_path: str = _DEFAULT_LITERATURE_PATH,
    output_path: str = _DEFAULT_OUTPUT_PATH,
) -> dict:
    """Run futures-benchmark validation on Monte Carlo output.

    Can be called two ways:

    1. **From the pipeline** (preferred): pass pre-loaded dicts directly::

           result = step15_futures_benchmark_validation(
               mc_results=mc, projection_map=proj, literature=lit
           )

    2. **Standalone**: omit dict args and supply file paths (or use defaults)::

           result = step15_futures_benchmark_validation()

    Parameters
    ----------
    mc_results : dict, optional
        Monte Carlo output (``monte_carlo_lng_v8.json`` structure).
    projection_map : dict, optional
        Projection map (``decarbiq_projection_map.json`` structure).
    literature : dict, optional
        Literature benchmarks (``literature_benchmarks.json`` structure).
    mc_path, projection_path, literature_path : str
        File paths used when the corresponding dict arg is ``None``.
    output_path : str
        Where to save the JSON validation report.

    Returns
    -------
    dict
        The full validation report (also saved to ``output_path``).
    """
    banner("STEP 15: Futures Benchmark Validation")

    # --- Load data -----------------------------------------------------------
    if mc_results is None:
        print(f"  Loading MC results from: {mc_path}")
        mc_results = _load_json(mc_path)

    if projection_map is None:
        print(f"  Loading projection map from: {projection_path}")
        projection_map = _load_json(projection_path)

    if literature is None:
        print(f"  Loading literature benchmarks from: {literature_path}")
        literature = _load_json(literature_path)

    mc_forecasts = mc_results.get("annual_forecasts", {})
    mc_bands = mc_results.get("confidence_bands", {})
    lng_curve = mc_results.get("lng_capacity_curve", {})

    gas_lit = literature.get("gas_price_forecasts", {})
    nymex = gas_lit.get("nymex_futures_strip", {})
    eia_aeo = gas_lit.get("eia_aeo_2025_reference", {})
    consensus = gas_lit.get("consensus_range", {})
    elec_bench = literature.get("electricity_price_benchmarks", {})
    prod_floor = literature.get("production_economics_floor", {})

    elec_proj = projection_map.get("electricity_price_projections", {})

    # --- 15A: Gas vs NYMEX futures -------------------------------------------
    print("\n  --- 15A. Gas Price: MC P50 vs NYMEX Futures Strip ---")
    gas_nymex = _validate_gas_vs_nymex(mc_forecasts, nymex)
    for yr, data in gas_nymex["years"].items():
        status_marker = "!!" if data["p50_status"] in ("HIGH", "LOW") else "OK"
        print(
            f"    {yr}: NYMEX=${data['nymex_futures']:.2f}  "
            f"Model P50=${data['model_p50']:.2f}  "
            f"Div={data['p50_divergence_pct']:+.1f}%  [{status_marker}]"
        )

    # --- 15B: Gas vs EIA AEO -------------------------------------------------
    print("\n  --- 15B. Gas Price: MC P50 vs EIA AEO Reference Case ---")
    gas_eia = _validate_gas_vs_eia_aeo(mc_forecasts, eia_aeo)
    for yr, data in gas_eia["years"].items():
        status_marker = "!!" if data["p50_status"] in ("HIGH", "LOW") else "OK"
        print(
            f"    {yr}: EIA=${data['eia_aeo_ref']:.2f}  "
            f"Model P50=${data['model_p50']:.2f}  "
            f"Div={data['p50_divergence_pct']:+.1f}%  [{status_marker}]"
        )

    # --- 15C: Gas vs consensus band ------------------------------------------
    print("\n  --- 15C. Gas Price: MC P50 vs Analyst Consensus Range ---")
    gas_consensus = _validate_gas_vs_consensus(mc_forecasts, consensus)
    for yr, data in gas_consensus["years"].items():
        flag = " ** FLAGGED **" if data["flagged"] else ""
        print(
            f"    {yr}: Consensus=[${data['consensus_low']:.2f}-${data['consensus_high']:.2f}]  "
            f"Model P50=${data['model_p50']:.2f}  "
            f"Band={data['band_status']}{flag}"
        )

    # --- 15D: ERCOT electricity projections -----------------------------------
    print("\n  --- 15D. Electricity: ERCOT Projections vs Benchmarks ---")
    ercot_val = _validate_electricity(
        elec_proj.get("ERCOT", {}),
        elec_bench.get("ercot", {}),
        "ERCOT",
    )
    for yr, data in ercot_val["years"].items():
        flag = " ** FLAGGED **" if data["flagged"] else ""
        print(
            f"    {yr}: Projected=${data['projected_mean']:.1f}  "
            f"Typical high=${data['typical_range_high']}  "
            f"Div={data['divergence_vs_typical_high_pct']:+.1f}%  "
            f"[{data['band_status']}]{flag}"
        )

    # --- 15D (cont): CAISO electricity projections ----------------------------
    print("\n  --- 15D. Electricity: CAISO Projections vs Benchmarks ---")
    caiso_val = _validate_electricity(
        elec_proj.get("CAISO", {}),
        elec_bench.get("caiso", {}),
        "CAISO",
    )
    for yr, data in caiso_val["years"].items():
        flag = " ** FLAGGED **" if data["flagged"] else ""
        print(
            f"    {yr}: Projected=${data['projected_mean']:.1f}  "
            f"Typical high=${data['typical_range_high']}  "
            f"Div={data['divergence_vs_typical_high_pct']:+.1f}%  "
            f"[{data['band_status']}]{flag}"
        )

    # --- 15E: Confidence band sanity -----------------------------------------
    print("\n  --- 15E. Monthly Confidence Band Sanity Check ---")
    band_check = _validate_confidence_bands(mc_bands, prod_floor)
    print(f"    Total months: {band_check['total_months']}")
    print(f"    P5 floor breaches: {band_check['p5_floor_breaches']}")
    print(f"    P95 ceiling breaches: {band_check['p95_ceiling_breaches']}")
    print(f"    Band width issues: {band_check['band_width_issues']}")
    if band_check["violations"]:
        for v in band_check["violations"]:
            print(f"    !! {v}")
    else:
        print("    All checks passed.")

    # --- 15F: LNG-adjusted mean sanity ---------------------------------------
    print("\n  --- 15F. LNG Capacity Curve Adjusted Mean Sanity ---")
    lng_check = _validate_lng_adjusted_means(
        lng_curve,
        nymex.get("values", {}),
        consensus,
    )
    print(f"    Regime distribution: {lng_check['regime_distribution']}")
    for yr, data in lng_check["years"].items():
        status_marker = "!!" if data["status"] in ("HIGH", "LOW") else "OK"
        ref_used = data["primary_ref_used"]
        print(
            f"    {yr}: LNG-adj=${data['lng_adjusted_mean']:.2f}  "
            f"Ref=${ref_used:.2f}  "
            f"Div={data['divergence_pct']:+.1f}%  "
            f"Regime={data['market_regime']}  [{status_marker}]"
        )

    # --- Summary -------------------------------------------------------------
    sections = {
        "gas_vs_nymex": gas_nymex,
        "gas_vs_eia_aeo": gas_eia,
        "gas_vs_consensus": gas_consensus,
        "ercot_electricity": ercot_val,
        "caiso_electricity": caiso_val,
        "confidence_bands": band_check,
        "lng_adjusted_means": lng_check,
    }
    summary = _build_summary(sections)

    print(f"\n  === OVERALL STATUS: {summary['overall_status']} ===")
    print(f"  Sections flagged: {summary['sections_flagged']}/{summary['sections_total']}")
    if summary["flagged_items"]:
        print(f"  Flagged items: {', '.join(summary['flagged_items'])}")
    print(f"\n  Recommendation: {summary['recommendation']}")

    # --- Assemble full report ------------------------------------------------
    validation = {
        "metadata": {
            "step": "step15_futures_benchmark_validation",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "divergence_threshold_pct": DIVERGENCE_THRESHOLD_PCT,
            "forecast_years": FORECAST_YEARS,
            "sources": {
                "monte_carlo": mc_results.get("version", "unknown"),
                "literature_benchmarks": literature.get("_metadata", {}).get(
                    "last_updated", "unknown"
                ),
                "nymex_as_of": nymex.get("as_of", "unknown"),
            },
        },
        "summary": summary,
        "gas_vs_nymex_futures": gas_nymex,
        "gas_vs_eia_aeo": gas_eia,
        "gas_vs_consensus_range": gas_consensus,
        "ercot_electricity": ercot_val,
        "caiso_electricity": caiso_val,
        "confidence_band_sanity": band_check,
        "lng_adjusted_mean_sanity": lng_check,
    }

    # --- Save ----------------------------------------------------------------
    with open(output_path, "w") as f:
        json.dump(validation, f, indent=2, default=str)
    print(f"\n  Saved: {output_path}")

    return validation


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Allow override of output dir via CLI arg
    out_path = _DEFAULT_OUTPUT_PATH
    if len(sys.argv) > 1:
        out_dir = sys.argv[1]
        out_path = os.path.join(out_dir, "futures_benchmark_validation.json")

    result = step15_futures_benchmark_validation(output_path=out_path)

    # Exit with non-zero if CRITICAL
    if result.get("summary", {}).get("overall_status") == "CRITICAL":
        sys.exit(1)
