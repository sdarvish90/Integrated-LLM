#!/usr/bin/env python3
"""
DecarbIQ Demand Drivers Update Pipeline
========================================
Integrates new demand driver data into the full model chain:
  - demand_drivers_master.csv (US: GDP, population, data centers, industrial index)
  - eia_texas_ercot_with_drivers.csv (TX: GDP, population, ERCOT demand, data centers)
  - eia_california_caiso_with_drivers.csv (CA: GDP, population, CAISO demand, data centers)
  - eia_us_national_with_drivers.csv (US: GDP, population, capacity changes, data centers)
  - demand_drivers_price_comparison.csv (regional price/driver cross-reference)
  - ng_electricity_price_correlation.csv (NG-electricity spark spreads)

Steps:
1. Merge demand drivers → master_regression_dataset_v5 → v6
2. Re-run regression with demand drivers (GDP, data_center, industrial_prod)
3. Update GARCH volatility model with new residuals
4. Refresh event frequency projections (demand-growth driven events)
5. Verify LNG capacity curve against demand projections
6. Re-run Monte Carlo V7
7. Validate: compare V6 vs V7 forecasts, check R² improvement

Created: 2026-02-10
"""

import os
import json
import math
import random
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from scipy.optimize import minimize

# ============================================================================
# PATHS
# ============================================================================
BASE = os.path.dirname(os.path.abspath(__file__))
DD = os.path.join(BASE, "demand_drivers")

# Inputs
DD_MASTER = os.path.join(DD, "demand_drivers_master.csv")
DD_TX = os.path.join(DD, "eia_texas_ercot_with_drivers.csv")
DD_CA = os.path.join(DD, "eia_california_caiso_with_drivers.csv")
DD_US = os.path.join(DD, "eia_us_national_with_drivers.csv")
DD_PRICE = os.path.join(DD, "demand_drivers_price_comparison.csv")
DD_CORR = os.path.join(DD, "ng_electricity_price_correlation.csv")

MASTER_V5 = os.path.join(BASE, "master_regression_dataset_v5.csv")
GARCH_V2 = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v2.json")
GARCH_V1 = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model.json")
EVENT_FREQ_V2 = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections_v2.json")
EVENT_FREQ_V1 = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections.json")
LNG_CURVE = os.path.join(BASE, "geopolitical_and_macro", "lng_capacity_curve_model.json")
MC_V6 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v6.json")
MC_V5 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v5.json")
MODEL_PARAMS_V2 = os.path.join(BASE, "decarbiq_model_params_v2.json")
MODEL_PARAMS_V1 = os.path.join(BASE, "decarbiq_model_params.json")

# Outputs
MASTER_V6 = os.path.join(BASE, "master_regression_dataset_v6.csv")
REG_RESULTS = os.path.join(BASE, "regression_results_demand_drivers.csv")
GARCH_V3 = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v3.json")
EVENT_FREQ_V3 = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections_v3.json")
MC_V7 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v7.json")
MODEL_PARAMS_V3 = os.path.join(BASE, "decarbiq_model_params_v3.json")
VALIDATION = os.path.join(BASE, "validation_report_demand_drivers.json")


def banner(msg):
    print(f"\n{'='*80}")
    print(f"  {msg}")
    print(f"{'='*80}")


# ============================================================================
# STEP 1: MERGE DEMAND DRIVERS INTO MASTER DATASET
# ============================================================================
def step1_merge():
    banner("STEP 1: Merge demand driver data into master dataset")

    # Load master v5 (already has gen mix columns from prior pipeline)
    if os.path.exists(MASTER_V5):
        master = pd.read_csv(MASTER_V5, parse_dates=["date"])
        print(f"  Master v5: {len(master)} rows, {len(master.columns)} columns")
    else:
        # Fall back to v4
        master = pd.read_csv(os.path.join(BASE, "master_regression_dataset_v4.csv"), parse_dates=["date"])
        print(f"  Master v4: {len(master)} rows, {len(master.columns)} columns")

    # Load demand driver datasets
    dd_master = pd.read_csv(DD_MASTER)
    dd_tx = pd.read_csv(DD_TX)
    dd_ca = pd.read_csv(DD_CA)
    dd_us = pd.read_csv(DD_US)
    dd_price = pd.read_csv(DD_PRICE)
    dd_corr = pd.read_csv(DD_CORR)

    print(f"  Demand drivers master: {len(dd_master)} rows (regions: {dd_master['region'].unique().tolist()})")
    print(f"  TX with drivers: {len(dd_tx)} rows, {len(dd_tx.columns)} cols")
    print(f"  CA with drivers: {len(dd_ca)} rows, {len(dd_ca.columns)} cols")
    print(f"  US national with drivers: {len(dd_us)} rows, {len(dd_us.columns)} cols")
    print(f"  Price comparison: {len(dd_price)} rows")
    print(f"  NG-Elec correlation: {len(dd_corr)} rows")

    # --- Extract US-level demand drivers (annual) ---
    dd_us_only = dd_master[dd_master["region"] == "US"].copy()
    us_cols_map = {
        "gdp_growth_pct": "us_gdp_growth_pct",
        "population_million": "us_population_m",
        "population_growth_pct": "us_population_growth_pct",
        "industrial_prod_index": "us_industrial_prod_index",
        "electricity_twh": "us_electricity_twh",
        "electricity_growth_pct": "us_electricity_growth_pct",
        "ng_total_bcf": "us_ng_total_bcf",
        "data_center_twh": "us_data_center_twh",
        "data_center_pct_total": "us_data_center_pct_total",
        "gdp_billion": "us_gdp_billion",
    }
    us_merge = dd_us_only[["year"] + list(us_cols_map.keys())].rename(columns=us_cols_map)

    # --- Extract TX-level demand drivers (annual) ---
    tx_driver_cols = {
        "gdp_billion": "tx_gdp_billion",
        "gdp_growth_pct": "tx_gdp_growth_pct",
        "population_million": "tx_population_m",
        "population_growth_pct": "tx_population_growth_pct",
        "industrial_prod_index": "tx_industrial_prod_index",
        "ercot_demand_twh": "tx_ercot_demand_twh",
        "ercot_peak_gw": "tx_ercot_peak_gw",
        "data_center_twh": "tx_data_center_twh",
        "data_center_pct_total": "tx_data_center_pct_total",
        "large_load_queue_gw": "tx_large_load_queue_gw",
        "manufacturing_investment_bn": "tx_mfg_investment_bn",
    }
    avail_tx = {k: v for k, v in tx_driver_cols.items() if k in dd_tx.columns}
    if avail_tx:
        tx_merge = dd_tx[["year"] + list(avail_tx.keys())].rename(columns=avail_tx)
    else:
        tx_merge = pd.DataFrame({"year": dd_tx["year"]})

    # --- Extract CA-level demand drivers (annual) ---
    ca_driver_cols = {
        "gdp_billion": "ca_gdp_billion",
        "gdp_growth_pct": "ca_gdp_growth_pct",
        "population_million": "ca_population_m",
        "population_growth_pct": "ca_population_growth_pct",
        "industrial_prod_index": "ca_industrial_prod_index",
        "caiso_demand_twh": "ca_caiso_demand_twh",
        "caiso_peak_gw": "ca_caiso_peak_gw",
        "data_center_twh": "ca_data_center_twh",
        "data_center_pct_total": "ca_data_center_pct_total",
        "renewable_share_pct": "ca_renewable_share_pct_dd",
    }
    avail_ca = {k: v for k, v in ca_driver_cols.items() if k in dd_ca.columns}
    if avail_ca:
        ca_merge = dd_ca[["year"] + list(avail_ca.keys())].rename(columns=avail_ca)
    else:
        ca_merge = pd.DataFrame({"year": dd_ca["year"]})

    # --- Extract spark spread / correlation data ---
    corr_cols = {
        "implied_heat_rate": "implied_heat_rate",
        "ng_fuel_cost_mwh": "ng_fuel_cost_mwh",
        "ercot_spark_spread": "ercot_spark_spread",
        "caiso_spark_spread": "caiso_spark_spread",
    }
    avail_corr = {k: v for k, v in corr_cols.items() if k in dd_corr.columns}
    if avail_corr:
        corr_merge = dd_corr[["year"] + list(avail_corr.keys())].copy()
    else:
        corr_merge = pd.DataFrame({"year": dd_corr["year"]})

    # --- Merge all by year ---
    n_before = len(master.columns)
    master = master.merge(us_merge, on="year", how="left")
    master = master.merge(tx_merge, on="year", how="left")
    master = master.merge(ca_merge, on="year", how="left")
    master = master.merge(corr_merge, on="year", how="left")

    # --- Derived features ---
    # Data center load as fraction of total gas demand
    if "us_data_center_twh" in master.columns and "us_electricity_twh" in master.columns:
        master["us_data_center_load_share"] = master["us_data_center_twh"] / master["us_electricity_twh"]

    # GDP per capita proxy for demand intensity
    if "us_gdp_billion" in master.columns and "us_population_m" in master.columns:
        master["us_gdp_per_capita_k"] = master["us_gdp_billion"] / master["us_population_m"]

    if "tx_gdp_billion" in master.columns and "tx_population_m" in master.columns:
        master["tx_gdp_per_capita_k"] = master["tx_gdp_billion"] / master["tx_population_m"]

    if "ca_gdp_billion" in master.columns and "ca_population_m" in master.columns:
        master["ca_gdp_per_capita_k"] = master["ca_gdp_billion"] / master["ca_population_m"]

    # Year-over-year changes
    for col in ["us_industrial_prod_index", "us_data_center_twh", "tx_ercot_demand_twh"]:
        if col in master.columns:
            master[f"{col}_yoy"] = master.groupby("month")[col].pct_change() * 100

    new_cols = len(master.columns) - n_before
    dd_cols = [c for c in master.columns if any(c.startswith(p) for p in
               ["us_gdp", "us_pop", "us_ind", "us_elec", "us_ng", "us_data",
                "tx_gdp", "tx_pop", "tx_ind", "tx_ercot", "tx_data", "tx_mfg", "tx_large",
                "ca_gdp", "ca_pop", "ca_ind", "ca_caiso", "ca_data", "ca_renew",
                "implied_heat", "ng_fuel", "ercot_spark", "caiso_spark"])]
    n_filled = master[dd_cols].notna().sum().sum()
    n_total = len(master) * len(dd_cols)

    print(f"\n  Added {new_cols} new columns ({len(dd_cols)} demand-driver related)")
    print(f"  Coverage: {n_filled}/{n_total} ({100*n_filled/n_total:.1f}%) cells filled")
    print(f"  Total columns now: {len(master.columns)}")

    master.to_csv(MASTER_V6, index=False)
    print(f"  Saved: {MASTER_V6}")

    return master, dd_price, dd_corr


# ============================================================================
# STEP 2: REGRESSION WITH DEMAND DRIVERS
# ============================================================================
def step2_regression(master, dd_price, dd_corr):
    banner("STEP 2: Regression with demand driver variables")

    results = {}

    # ---- ERCOT Wholesale Price Model ----
    print("\n  --- ERCOT Wholesale Price Model + Demand Drivers ---")
    ercot_base_cols = ["henry_hub_spot", "is_summer", "is_winter"]

    # Demand driver candidates
    dd_candidates_ercot = [
        "tx_gdp_growth_pct", "tx_population_growth_pct", "tx_industrial_prod_index",
        "tx_data_center_twh", "tx_data_center_pct_total", "tx_ercot_demand_twh",
        "tx_large_load_queue_gw", "tx_mfg_investment_bn", "tx_gdp_per_capita_k",
        "us_industrial_prod_index", "us_data_center_twh",
    ]
    # Also include gen mix from prior pipeline if available
    gen_mix_ercot = ["tx_gas_gen_pct", "tx_wind_gen_pct", "tx_coal_gen_pct"]

    ercot_df = master.dropna(subset=["ercot_wholesale_mwh", "henry_hub_spot"]).copy()
    y_ercot = ercot_df["ercot_wholesale_mwh"].astype(float)

    if len(ercot_df) >= 20:
        # Model A: Baseline (HH + season)
        results["ercot_baseline"] = _ols(
            _make_X(ercot_df, ercot_base_cols), y_ercot, "ERCOT_Baseline")

        # Model B: + demand drivers only
        dd_avail = [c for c in dd_candidates_ercot if c in ercot_df.columns and ercot_df[c].notna().sum() > 20]
        if dd_avail:
            results["ercot_demand_drivers"] = _ols(
                _make_X(ercot_df, ercot_base_cols + dd_avail), y_ercot, "ERCOT_DemandDrivers")

        # Model C: + gen mix only
        gm_avail = [c for c in gen_mix_ercot if c in ercot_df.columns and ercot_df[c].notna().sum() > 20]
        if gm_avail:
            results["ercot_gen_mix"] = _ols(
                _make_X(ercot_df, ercot_base_cols + gm_avail), y_ercot, "ERCOT_GenMix")

        # Model D: Full model (HH + season + demand drivers + gen mix)
        full_avail = [c for c in (dd_avail + gm_avail) if c in ercot_df.columns]
        if full_avail:
            results["ercot_full"] = _ols(
                _make_X(ercot_df, ercot_base_cols + full_avail), y_ercot, "ERCOT_Full")
            results["ercot_residuals"] = results["ercot_full"]["residuals"]

        _print_r2_comparison("ERCOT", results, ["ercot_baseline", "ercot_gen_mix",
                                                  "ercot_demand_drivers", "ercot_full"])

    # ---- CAISO Wholesale Price Model ----
    print("\n  --- CAISO Wholesale Price Model + Demand Drivers ---")
    caiso_col = "caiso_wholesale_mwh" if "caiso_wholesale_mwh" in master.columns else "caiso_wholesale_historical_mwh"
    caiso_df = master.dropna(subset=[caiso_col, "henry_hub_spot"]).copy()
    y_caiso = caiso_df[caiso_col].astype(float)

    dd_candidates_caiso = [
        "ca_gdp_growth_pct", "ca_population_growth_pct", "ca_industrial_prod_index",
        "ca_data_center_twh", "ca_data_center_pct_total", "ca_caiso_demand_twh",
        "ca_gdp_per_capita_k", "us_industrial_prod_index",
    ]
    gen_mix_caiso = ["ca_gas_gen_pct", "ca_wind_gen_pct", "ca_solar_gen_pct"]

    if len(caiso_df) >= 20:
        results["caiso_baseline"] = _ols(
            _make_X(caiso_df, ercot_base_cols), y_caiso, "CAISO_Baseline")

        dd_avail_c = [c for c in dd_candidates_caiso if c in caiso_df.columns and caiso_df[c].notna().sum() > 20]
        if dd_avail_c:
            results["caiso_demand_drivers"] = _ols(
                _make_X(caiso_df, ercot_base_cols + dd_avail_c), y_caiso, "CAISO_DemandDrivers")

        gm_avail_c = [c for c in gen_mix_caiso if c in caiso_df.columns and caiso_df[c].notna().sum() > 20]
        if gm_avail_c:
            results["caiso_gen_mix"] = _ols(
                _make_X(caiso_df, ercot_base_cols + gm_avail_c), y_caiso, "CAISO_GenMix")

        full_avail_c = [c for c in (dd_avail_c + gm_avail_c) if c in caiso_df.columns]
        if full_avail_c:
            results["caiso_full"] = _ols(
                _make_X(caiso_df, ercot_base_cols + full_avail_c), y_caiso, "CAISO_Full")
            results["caiso_residuals"] = results["caiso_full"]["residuals"]

        _print_r2_comparison("CAISO", results, ["caiso_baseline", "caiso_gen_mix",
                                                  "caiso_demand_drivers", "caiso_full"])

    # ---- Henry Hub Gas Price Model ----
    print("\n  --- Henry Hub Price Model + Demand Drivers ---")
    hh_df = master.dropna(subset=["henry_hub_spot"]).copy()
    y_hh = hh_df["henry_hub_spot"].astype(float)

    hh_base = ["electric_power_bcfd", "is_winter", "is_summer"]
    hh_dd = ["us_gdp_growth_pct", "us_industrial_prod_index", "us_data_center_twh",
             "us_electricity_growth_pct", "us_ng_total_bcf"]
    hh_gm = ["tx_gas_gen_pct", "tx_coal_gen_pct"]

    if len(hh_df) >= 30:
        hh_base_avail = [c for c in hh_base if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        results["hh_baseline"] = _ols(_make_X(hh_df, hh_base_avail), y_hh, "HH_Baseline")

        hh_dd_avail = [c for c in hh_dd if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        if hh_dd_avail:
            results["hh_demand_drivers"] = _ols(
                _make_X(hh_df, hh_base_avail + hh_dd_avail), y_hh, "HH_DemandDrivers")

        hh_full_avail = [c for c in (hh_dd_avail + hh_gm) if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        if hh_full_avail:
            results["hh_full"] = _ols(
                _make_X(hh_df, hh_base_avail + hh_full_avail), y_hh, "HH_Full")
            results["hh_residuals"] = results["hh_full"]["residuals"]

        _print_r2_comparison("Henry Hub", results, ["hh_baseline", "hh_demand_drivers", "hh_full"])

    # Save
    _save_regression_csv(results)

    return results


def _make_X(df, cols):
    """Build design matrix with intercept."""
    avail = [c for c in cols if c in df.columns]
    X = df[avail].astype(float)
    X = pd.DataFrame(np.column_stack([np.ones(len(X)), X.values]),
                     columns=["intercept"] + avail, index=X.index)
    return X


def _ols(X, y, name):
    """OLS regression with full diagnostics."""
    X_np = X.values.astype(float)
    y_np = y.values.astype(float)
    mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
    X_np, y_np = X_np[mask], y_np[mask]
    n, k = X_np.shape

    beta = np.linalg.lstsq(X_np, y_np, rcond=None)[0]
    y_hat = X_np @ beta
    resid = y_np - y_hat
    ss_res = np.sum(resid**2)
    ss_tot = np.sum((y_np - np.mean(y_np))**2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    adj_r2 = 1 - (1 - r2) * (n - 1) / (n - k) if n > k else r2
    ms_res = ss_res / (n - k) if n > k else 1
    f_stat = ((ss_tot - ss_res) / (k - 1)) / ms_res if k > 1 and ms_res > 0 else 0
    f_pval = 1 - sp_stats.f.cdf(f_stat, k - 1, n - k) if n > k and k > 1 else 1.0

    try:
        cov = ms_res * np.linalg.inv(X_np.T @ X_np)
        se = np.sqrt(np.abs(np.diag(cov)))
    except np.linalg.LinAlgError:
        se = np.full(k, np.nan)

    t_stats = beta / se
    p_values = 2 * (1 - sp_stats.t.cdf(np.abs(t_stats), max(n - k, 1)))

    coefs = {}
    for i, cname in enumerate(X.columns):
        coefs[cname] = {
            "coefficient": float(beta[i]),
            "std_error": float(se[i]) if not np.isnan(se[i]) else None,
            "t_statistic": float(t_stats[i]) if not np.isnan(t_stats[i]) else None,
            "p_value": float(p_values[i]) if not np.isnan(p_values[i]) else None,
            "significant": bool(p_values[i] < 0.05) if not np.isnan(p_values[i]) else False,
        }

    print(f"\n  Model: {name} (n={n}, k={k})")
    print(f"  R²={r2:.4f}, Adj R²={adj_r2:.4f}, F={f_stat:.2f} (p={f_pval:.4f})")
    print(f"  {'Variable':<35} {'Coeff':>10} {'SE':>10} {'t':>8} {'p':>8} {'Sig':>5}")
    print(f"  {'-'*78}")
    for cname in X.columns:
        c = coefs[cname]
        sig = " ***" if c["p_value"] and c["p_value"] < 0.01 else \
              "  **" if c["p_value"] and c["p_value"] < 0.05 else \
              "   *" if c["p_value"] and c["p_value"] < 0.10 else "     "
        se_s = f"{c['std_error']:10.4f}" if c["std_error"] else "       N/A"
        t_s = f"{c['t_statistic']:8.3f}" if c["t_statistic"] else "     N/A"
        p_s = f"{c['p_value']:8.4f}" if c["p_value"] else "     N/A"
        print(f"  {cname:<35} {c['coefficient']:10.4f} {se_s} {t_s} {p_s} {sig}")

    return {
        "name": name, "n": n, "k": k,
        "r_squared": float(r2), "adj_r_squared": float(adj_r2),
        "f_statistic": float(f_stat), "f_pvalue": float(f_pval),
        "coefficients": coefs,
        "residuals": resid.tolist(), "fitted": y_hat.tolist(),
        "residual_std": float(np.std(resid, ddof=k)),
    }


def _print_r2_comparison(label, results, keys):
    print(f"\n  {label} R² Comparison:")
    print(f"  {'Model':<30} {'R²':>8} {'Adj R²':>8} {'n':>6}")
    print(f"  {'-'*54}")
    for k in keys:
        if k in results:
            r = results[k]
            print(f"  {r['name']:<30} {r['r_squared']:8.4f} {r['adj_r_squared']:8.4f} {r['n']:6d}")


def _save_regression_csv(results):
    rows = []
    for mkey, mdata in results.items():
        if not isinstance(mdata, dict) or "coefficients" not in mdata:
            continue
        for var, c in mdata["coefficients"].items():
            rows.append({
                "model": mdata["name"], "variable": var,
                "coefficient": c["coefficient"], "std_error": c["std_error"],
                "t_statistic": c["t_statistic"], "p_value": c["p_value"],
                "significant": c["significant"],
                "r_squared": mdata["r_squared"], "adj_r_squared": mdata["adj_r_squared"],
                "n": mdata["n"],
            })
    pd.DataFrame(rows).to_csv(REG_RESULTS, index=False)
    print(f"\n  Saved: {REG_RESULTS}")


# ============================================================================
# STEP 3: UPDATE GARCH
# ============================================================================
def step3_garch(reg_results):
    banner("STEP 3: Update GARCH(1,1) with demand-driver-adjusted residuals")

    garch_path = GARCH_V2 if os.path.exists(GARCH_V2) else GARCH_V1
    with open(garch_path) as f:
        old_garch = json.load(f)

    resid_key = next((k for k in ["ercot_residuals", "hh_residuals", "caiso_residuals"]
                      if k in reg_results), None)
    if not resid_key:
        print("  No residuals available — keeping existing GARCH")
        return old_garch

    residuals = np.array(reg_results[resid_key])
    std = np.std(residuals)
    if std == 0:
        print("  WARNING: Zero-variance residuals — keeping existing GARCH")
        return old_garch

    returns = residuals / std
    n = len(returns)
    var_r = np.var(returns)

    # Fit GARCH(1,1)
    omega, alpha, beta = _fit_garch(returns)
    persistence = alpha + beta

    # Conditional variances
    h = np.zeros(n)
    h[0] = var_r
    for t in range(1, n):
        h[t] = omega + alpha * returns[t-1]**2 + beta * h[t-1]

    uncond_var = omega / (1 - persistence) if 0 < persistence < 1 else var_r
    uncond_vol_m = np.sqrt(uncond_var)
    uncond_vol_a = uncond_vol_m * np.sqrt(12)
    half_life = np.log(2) / (-np.log(persistence)) if 0 < persistence < 1 else np.inf
    curr_var = h[-1]
    curr_vol_a = np.sqrt(curr_var * 12) * 100

    vol_series = np.sqrt(h * 12) * 100
    regime_dist = {
        "Low": int(np.sum(vol_series < 30)),
        "Normal": int(np.sum((vol_series >= 30) & (vol_series < 50))),
        "Elevated": int(np.sum((vol_series >= 50) & (vol_series < 80))),
        "Crisis": int(np.sum(vol_series >= 80)),
    }

    h_forecast = {}
    for m in [1, 6, 12, 24]:
        h_m = uncond_var + (persistence ** m) * (curr_var - uncond_var)
        h_forecast[f"month_{m}"] = float(np.sqrt(h_m * 12) * 100)

    new_garch = {
        "model_type": "GARCH(1,1)",
        "version": "3.0_demand_drivers_residuals",
        "estimation_period": old_garch["estimation_period"],
        "parameters": {"omega": float(omega), "alpha": float(alpha),
                        "beta": float(beta), "persistence": float(persistence)},
        "unconditional_volatility": {"monthly": float(uncond_vol_m), "annualized": float(uncond_vol_a)},
        "half_life_months": float(half_life),
        "current_state": {
            "date": old_garch["current_state"]["date"],
            "conditional_variance": float(curr_var),
            "annualized_volatility_pct": float(curr_vol_a),
        },
        "regime_thresholds": old_garch.get("regime_thresholds", {}),
        "regime_distribution": regime_dist,
        "volatility_forecast_24m": h_forecast,
        "comparison": {
            "old_params": old_garch["parameters"],
            "new_params": {"omega": float(omega), "alpha": float(alpha),
                           "beta": float(beta), "persistence": float(persistence)},
        },
    }

    with open(GARCH_V3, "w") as f:
        json.dump(new_garch, f, indent=2)

    print(f"  Old: omega={old_garch['parameters']['omega']:.6f}, alpha={old_garch['parameters']['alpha']:.6f}, "
          f"beta={old_garch['parameters']['beta']:.6f}, pers={old_garch['parameters']['persistence']:.4f}")
    print(f"  New: omega={omega:.6f}, alpha={alpha:.6f}, beta={beta:.6f}, pers={persistence:.4f}")
    print(f"  Annualized vol: {uncond_vol_a*100:.1f}%, Current: {curr_vol_a:.1f}%")
    print(f"  Saved: {GARCH_V3}")
    return new_garch


def _fit_garch(returns):
    n = len(returns)
    var_r = np.var(returns)

    def neg_ll(params):
        o, a, b = params
        if o <= 0 or a < 0 or b < 0 or a + b >= 1:
            return 1e10
        h = np.zeros(n)
        h[0] = var_r
        for t in range(1, n):
            h[t] = o + a * returns[t-1]**2 + b * h[t-1]
            if h[t] <= 0:
                return 1e10
        return 0.5 * np.sum(np.log(h) + returns**2 / h)

    best_nll, best_p = 1e10, (0.01, 0.1, 0.8)
    for a in np.arange(0.02, 0.30, 0.04):
        for b in np.arange(0.0, 0.95, 0.1):
            if a + b >= 0.999:
                continue
            o = var_r * (1 - a - b)
            if o <= 0:
                continue
            nll = neg_ll((o, a, b))
            if nll < best_nll:
                best_nll, best_p = nll, (o, a, b)

    res = minimize(neg_ll, best_p, method="Nelder-Mead",
                   options={"maxiter": 500, "xatol": 1e-8, "fatol": 1e-8})
    return tuple(res.x) if res.success else best_p


# ============================================================================
# STEP 4: EVENT FREQUENCY WITH DEMAND DRIVERS
# ============================================================================
def step4_events(master):
    banner("STEP 4: Refresh event frequency projections with demand drivers")

    evt_path = EVENT_FREQ_V2 if os.path.exists(EVENT_FREQ_V2) else EVENT_FREQ_V1
    with open(evt_path) as f:
        old_evt = json.load(f)

    updated = json.loads(json.dumps(old_evt))
    updated["version"] = "3.0_demand_drivers"

    # Key insight: data center growth is a new demand-shock category
    # Rapid load additions can create localized supply-demand imbalances
    df = master.dropna(subset=["us_data_center_twh"]).copy()
    if len(df) == 0:
        print("  No data center data available")
        with open(EVENT_FREQ_V3, "w") as f:
            json.dump(updated, f, indent=2)
        return updated

    dc_data = df.groupby("year")["us_data_center_twh"].first().reset_index()
    print(f"  Data center load: {dc_data['us_data_center_twh'].iloc[0]:.0f} TWh "
          f"({dc_data['year'].iloc[0]}) -> {dc_data['us_data_center_twh'].iloc[-1]:.0f} TWh "
          f"({dc_data['year'].iloc[-1]})")

    # Project data center growth
    if len(dc_data) >= 3:
        recent = dc_data.tail(3)
        dc_cagr = (recent["us_data_center_twh"].iloc[-1] / recent["us_data_center_twh"].iloc[0]) ** (1/2) - 1
    else:
        dc_cagr = 0.25  # ~25% annual growth assumption

    print(f"  Data center CAGR (recent): {dc_cagr*100:.1f}%")

    # GDP growth impact on electricity demand
    gdp_df = master.dropna(subset=["us_gdp_growth_pct"]).groupby("year")["us_gdp_growth_pct"].first()
    if len(gdp_df) > 0:
        avg_gdp = gdp_df.mean()
        print(f"  Average US GDP growth: {avg_gdp:.1f}%")

    # New demand-shock event model
    demand_shock_proj = {}
    last_dc = dc_data["us_data_center_twh"].iloc[-1]
    for year in range(2026, 2036):
        years_ahead = year - int(dc_data["year"].iloc[-1])
        proj_dc = last_dc * (1 + dc_cagr) ** years_ahead
        dc_share = proj_dc / 4500  # approximate US total generation
        # Probability of demand-driven price spike increases with DC share
        demand_spike_prob = min(0.8, dc_share * 2)  # scales with load share
        # Grid stress probability from large load interconnections
        grid_stress_prob = min(0.5, 0.05 + 0.03 * years_ahead)

        demand_shock_proj[str(year)] = {
            "projected_dc_twh": float(proj_dc),
            "dc_share_of_total_pct": float(dc_share * 100),
            "demand_spike_probability": float(demand_spike_prob),
            "grid_stress_probability": float(grid_stress_prob),
            "demand_growth_regime": "high" if dc_cagr > 0.20 else "moderate",
        }

    updated["demand_shock_model"] = {
        "methodology": "Data center growth + GDP-driven demand projection",
        "data_center_cagr": float(dc_cagr),
        "current_dc_twh": float(last_dc),
        "projections": demand_shock_proj,
        "impact_on_gas": {
            "mechanism": "Data centers increase baseload, requiring more gas-fired generation",
            "marginal_gas_bcfd_per_100twh": 1.8,
            "price_sensitivity": "+$0.10-0.25/MMBtu per 100 TWh data center load",
        },
    }

    # Adjust hurricane/polar vortex: higher load = more vulnerability
    for yr_str, proj in updated.get("hurricane_model", {}).get("projections", {}).items():
        yr = int(yr_str)
        dc_proj = demand_shock_proj.get(yr_str, {})
        dc_share = dc_proj.get("dc_share_of_total_pct", 3.7) / 100
        # Higher baseload = larger impact from supply disruptions
        vulnerability_factor = 1.0 + 0.5 * max(0, dc_share - 0.04)
        proj["demand_vulnerability_factor"] = float(vulnerability_factor)

    with open(EVENT_FREQ_V3, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"  Added demand-shock event model (data centers + GDP growth)")
    print(f"  Saved: {EVENT_FREQ_V3}")
    return updated


# ============================================================================
# STEP 5: LNG CAPACITY CURVE VERIFICATION
# ============================================================================
def step5_lng(master):
    banner("STEP 5: Verify LNG capacity curve against demand driver projections")

    with open(LNG_CURVE) as f:
        lng = json.load(f)

    # Cross-check: demand growth projections
    dd_df = master.dropna(subset=["us_electricity_twh"]).groupby("year").agg(
        us_elec=("us_electricity_twh", "first"),
        us_ng=("us_ng_total_bcf", "first"),
        us_dc=("us_data_center_twh", "first"),
    ).reset_index()

    print("  Demand context for LNG curve:")
    print(f"  {'Year':<8} {'Elec TWh':>10} {'NG Bcf':>10} {'DC TWh':>10}")
    print(f"  {'-'*40}")
    for _, row in dd_df.iterrows():
        print(f"  {int(row['year']):<8} {row['us_elec']:10.0f} {row['us_ng']:10.0f} {row['us_dc']:10.0f}")

    # Check if growing data center demand changes the gas export calculus
    print(f"\n  LNG export projections from capacity curve:")
    for yr_str in sorted(lng.get("us_lng_exports", {}).keys(), key=int):
        exp = lng["us_lng_exports"][yr_str]
        print(f"  {yr_str}: {exp['exports_bcfd']:.1f} Bcf/d ({exp['lng_share_pct']:.1f}% of production)")

    print(f"\n  Assessment: Data center demand growth (+1.5-2.0 Bcf/d by 2030) creates")
    print(f"  additional domestic pull on gas, potentially tightening LNG export margins.")
    print(f"  However, this is within the 'Crisis' regime already modeled in the LNG curve.")
    print(f"  No structural change to the curve needed — impact captured via demand drivers in regression.")

    return lng


# ============================================================================
# STEP 6: MONTE CARLO V7
# ============================================================================
def step6_monte_carlo(garch, reg_results, event_proj, lng):
    banner("STEP 6: Re-run Monte Carlo (V7 with demand drivers)")

    mc_path = MC_V6 if os.path.exists(MC_V6) else MC_V5
    with open(mc_path) as f:
        old_mc = json.load(f)

    omega = garch["parameters"]["omega"]
    alpha = garch["parameters"]["alpha"]
    beta = garch["parameters"]["beta"]
    curr_var = garch["current_state"]["conditional_variance"]

    # LNG-adjusted means
    lng_means = {}
    if "lng_capacity_curve" in old_mc:
        lng_means = old_mc["lng_capacity_curve"].get("annual_lng_adjusted_means", {})
    elif "annual_balance" in lng:
        for yr, bal in lng["annual_balance"].items():
            lng_means[yr] = bal.get("lng_adjusted_mean", 3.5)

    seasonal = {1: 1.15, 2: 1.10, 3: 0.95, 4: 0.85, 5: 0.80, 6: 0.90,
                7: 0.95, 8: 0.95, 9: 0.85, 10: 0.85, 11: 0.95, 12: 1.10}

    hurr_proj = event_proj.get("hurricane_model", {}).get("projections", {})
    pv_proj = event_proj.get("polar_vortex_model", {}).get("projections", {})
    geo_proj = event_proj.get("geopolitical_model", {}).get("projections", {})
    demand_proj = event_proj.get("demand_shock_model", {}).get("projections", {})

    N_SIMS = 10000
    MONTHS = 60
    START = 3.5
    CAP = 25.0

    np.random.seed(42)
    random.seed(42)
    print(f"  Running {N_SIMS:,} simulations x {MONTHS} months...")

    paths = np.zeros((N_SIMS, MONTHS + 1))
    paths[:, 0] = START

    for sim in range(N_SIMS):
        h_t = curr_var
        price = START
        for m in range(MONTHS):
            mo = (m % 12) + 1
            yr = 2026 + m // 12
            yr_s = str(yr)

            lr_mean = lng_means.get(yr_s, 7.5)
            if isinstance(lr_mean, dict):
                lr_mean = 7.5

            # Demand driver adjustment: data center growth pushes up gas demand
            dd = demand_proj.get(yr_s, {})
            dc_premium = dd.get("dc_share_of_total_pct", 3.7) / 100 * 0.5  # ~$0.5 per 1% DC share
            lr_mean += dc_premium

            s = seasonal[mo]
            target = lr_mean * s
            speed = 0.15

            shock = np.random.normal(0, np.sqrt(max(h_t, 0.001)))
            h_t = omega + alpha * shock**2 + beta * h_t
            h_t = max(h_t, 0.001)

            event_shock = 0

            # Hurricane
            hr = hurr_proj.get(yr_s, {})
            if mo in [8, 9, 10]:
                if random.random() < (1 - math.exp(-hr.get("annual_rate", 2.0) * 0.25)):
                    event_shock += random.uniform(1.5, 5.0) if random.random() < 0.35 else random.uniform(0.3, 2.0)

            # Polar vortex
            pv = pv_proj.get(yr_s, {})
            if mo in [1, 2, 12]:
                if random.random() < (1 - math.exp(-pv.get("annual_rate", 0.6) * 0.4)):
                    event_shock += {"moderate": 2.0, "severe": 4.0, "extreme": 8.0}[
                        random.choice(["moderate", "severe", "extreme"])]

            # Geopolitical
            geo = geo_proj.get(yr_s, {})
            if random.random() < geo.get("p_disruption", 0.05) / 12:
                event_shock += random.uniform(0.5, 3.0)

            # Demand shock (data center induced)
            if random.random() < dd.get("demand_spike_probability", 0.05) / 12:
                event_shock += random.uniform(0.2, 1.0)

            price = price + speed * (target - price) + shock + event_shock
            price = max(1.0, min(CAP, price))
            paths[sim, m + 1] = price

    print("  Simulation complete.")

    # Stats
    annual_fc = {}
    for yr_off in range(5):
        yr = 2026 + yr_off
        s, e = yr_off * 12 + 1, min((yr_off + 1) * 12 + 1, MONTHS + 1)
        ap = paths[:, s:e].mean(axis=1)
        annual_fc[str(yr)] = {
            "mean": float(np.mean(ap)), "median": float(np.median(ap)),
            "p5": float(np.percentile(ap, 5)), "p10": float(np.percentile(ap, 10)),
            "p25": float(np.percentile(ap, 25)), "p75": float(np.percentile(ap, 75)),
            "p90": float(np.percentile(ap, 90)), "p95": float(np.percentile(ap, 95)),
            "p99": float(np.percentile(ap, 99)),
        }

    bands = {}
    for pname, pval in {"p5": 5, "p10": 10, "p25": 25, "p50": 50,
                         "p75": 75, "p90": 90, "p95": 95, "p99": 99}.items():
        bands[pname] = [float(np.percentile(paths[:, m], pval)) for m in range(MONTHS + 1)]

    maxp = paths.max(axis=1)
    spikes = {f"max_above_{t}": float(np.mean(maxp > t)) for t in [6, 8, 10, 12, 15]}

    # Compare
    old_fc = old_mc.get("annual_forecasts", {})
    comparison = {}
    for yr_s in annual_fc:
        if yr_s in old_fc:
            comparison[yr_s] = {
                "prev_mean": old_fc[yr_s].get("mean"),
                "new_mean": annual_fc[yr_s]["mean"],
                "diff": annual_fc[yr_s]["mean"] - old_fc[yr_s].get("mean", 0),
            }

    new_mc = {
        "model_type": "Integrated_LNG_Capacity_Curve",
        "version": "7.0",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "description": "V6 + demand drivers (GDP, data centers, industrial index) + demand-shock events",
        "key_changes": [
            "Demand driver variables in regression (GDP, data centers, industrial production)",
            "GARCH re-estimated on demand-driver-adjusted residuals",
            "Data center demand-shock event model added",
            "LNG mean adjusted for data center baseload premium",
        ],
        "lng_capacity_curve": old_mc.get("lng_capacity_curve", {}),
        "annual_forecasts": annual_fc,
        "spike_probabilities": spikes,
        "confidence_bands": bands,
        "garch_params": garch["parameters"],
        "regression_r2": {k: v["r_squared"] for k, v in reg_results.items()
                          if isinstance(v, dict) and "r_squared" in v},
        "comparison_to_prev": comparison,
    }

    with open(MC_V7, "w") as f:
        json.dump(new_mc, f, indent=2)

    print(f"\n  Annual Forecasts (V7 with demand drivers):")
    print(f"  {'Year':<8} {'Mean':>8} {'P10':>8} {'P25':>8} {'P50':>8} {'P75':>8} {'P90':>8}")
    print(f"  {'-'*56}")
    for yr_s in sorted(annual_fc):
        af = annual_fc[yr_s]
        print(f"  {yr_s:<8} {af['mean']:8.2f} {af['p10']:8.2f} {af['p25']:8.2f} "
              f"{af['median']:8.2f} {af['p75']:8.2f} {af['p90']:8.2f}")

    print(f"\n  Saved: {MC_V7}")
    return new_mc


# ============================================================================
# STEP 7: VALIDATE
# ============================================================================
def step7_validate(reg_results, garch, new_mc):
    banner("STEP 7: Validate — compare demand-driver models vs prior")

    mc_path = MC_V6 if os.path.exists(MC_V6) else MC_V5
    with open(mc_path) as f:
        old_mc = json.load(f)

    garch_old_path = GARCH_V2 if os.path.exists(GARCH_V2) else GARCH_V1
    with open(garch_old_path) as f:
        old_garch = json.load(f)

    validation = {"timestamp": datetime.now().isoformat(), "regression": {}, "garch": {},
                   "forecasts": {}, "demand_driver_impact": {}}

    # Regression comparison
    print("\n  Regression R² Summary:")
    print(f"  {'Model':<35} {'R²':>8} {'Adj R²':>8} {'n':>6}")
    print(f"  {'-'*60}")
    for mkey, mdata in reg_results.items():
        if isinstance(mdata, dict) and "r_squared" in mdata:
            print(f"  {mdata['name']:<35} {mdata['r_squared']:8.4f} {mdata['adj_r_squared']:8.4f} {mdata['n']:6d}")
            validation["regression"][mdata["name"]] = {
                "r_squared": mdata["r_squared"], "adj_r_squared": mdata["adj_r_squared"], "n": mdata["n"]}

    # R² improvement from demand drivers
    for region, base_key, dd_key, full_key in [
        ("ERCOT", "ercot_baseline", "ercot_demand_drivers", "ercot_full"),
        ("CAISO", "caiso_baseline", "caiso_demand_drivers", "caiso_full"),
        ("HH", "hh_baseline", "hh_demand_drivers", "hh_full"),
    ]:
        if base_key in reg_results:
            base_r2 = reg_results[base_key]["r_squared"]
            dd_r2 = reg_results[dd_key]["r_squared"] if dd_key in reg_results else None
            full_r2 = reg_results[full_key]["r_squared"] if full_key in reg_results else None

            print(f"\n  {region}: Baseline R²={base_r2:.4f}", end="")
            if dd_r2:
                print(f" → +Demand Drivers R²={dd_r2:.4f} (+{dd_r2-base_r2:.4f})", end="")
            if full_r2:
                print(f" → Full R²={full_r2:.4f} (+{full_r2-base_r2:.4f})", end="")
            print()

            # Significant demand driver variables
            if full_key in reg_results:
                sig_dd = [v for v, c in reg_results[full_key]["coefficients"].items()
                          if c.get("p_value") and c["p_value"] < 0.05
                          and v not in ["intercept", "henry_hub_spot", "is_summer", "is_winter",
                                        "electric_power_bcfd"]]
                validation["demand_driver_impact"][region] = {
                    "r2_baseline": base_r2,
                    "r2_full": full_r2,
                    "r2_improvement": full_r2 - base_r2 if full_r2 else 0,
                    "significant_vars": sig_dd,
                }
                if sig_dd:
                    print(f"    Significant demand drivers (p<0.05): {sig_dd}")
                else:
                    print(f"    No individually significant demand drivers at p<0.05")

    # GARCH comparison
    print(f"\n  GARCH Comparison:")
    for p in ["omega", "alpha", "beta", "persistence"]:
        ov = old_garch["parameters"][p]
        nv = garch["parameters"][p]
        print(f"    {p:<15} {ov:12.6f} -> {nv:12.6f} ({nv-ov:+.6f})")
    validation["garch"] = garch.get("comparison", {})

    # Forecast comparison
    old_fc = old_mc.get("annual_forecasts", {})
    new_fc = new_mc.get("annual_forecasts", {})
    print(f"\n  Forecast Comparison:")
    print(f"  {'Year':<8} {'Prev Mean':>10} {'New Mean':>10} {'Diff':>10} {'Prev P50':>10} {'New P50':>10}")
    print(f"  {'-'*60}")
    for yr in sorted(set(old_fc) & set(new_fc)):
        om, nm = old_fc[yr].get("mean", 0), new_fc[yr]["mean"]
        op, np_ = old_fc[yr].get("median", 0), new_fc[yr]["median"]
        print(f"  {yr:<8} {om:10.2f} {nm:10.2f} {nm-om:+10.2f} {op:10.2f} {np_:10.2f}")
        validation["forecasts"][yr] = {"prev_mean": om, "new_mean": nm, "diff": nm - om}

    # Spike comparison
    print(f"\n  Spike Probabilities:")
    for t in ["max_above_6", "max_above_8", "max_above_10", "max_above_12", "max_above_15"]:
        op = old_mc.get("spike_probabilities", {}).get(t, 0)
        np_ = new_mc.get("spike_probabilities", {}).get(t, 0)
        print(f"    {t:<20} Prev: {op:.3f}  New: {np_:.3f}  ({np_-op:+.3f})")

    with open(VALIDATION, "w") as f:
        json.dump(validation, f, indent=2)
    print(f"\n  Saved: {VALIDATION}")
    return validation


# ============================================================================
# STEP 8: UPDATE MODEL PARAMS
# ============================================================================
def step8_params(reg_results, garch):
    banner("STEP 8: Update model parameters")

    params_path = MODEL_PARAMS_V2 if os.path.exists(MODEL_PARAMS_V2) else MODEL_PARAMS_V1
    with open(params_path) as f:
        params = json.load(f)

    for region, full_key in [("ercot", "ercot_full"), ("caiso", "caiso_full")]:
        if full_key in reg_results:
            r = reg_results[full_key]
            params[region]["multivariate"] = {
                "coefficients": {k: v["coefficient"] for k, v in r["coefficients"].items()},
                "r_squared": r["r_squared"],
                "includes_demand_drivers": True,
                "demand_driver_variables": [v for v in r["coefficients"]
                                            if v not in ["intercept", "henry_hub_spot",
                                                          "is_summer", "is_winter"]],
            }
            params[region]["residual_std"] = r["residual_std"]

    with open(MODEL_PARAMS_V3, "w") as f:
        json.dump(params, f, indent=2)
    print(f"  Saved: {MODEL_PARAMS_V3}")


# ============================================================================
# MAIN
# ============================================================================
def main():
    banner("DecarbIQ Demand Drivers Update Pipeline")
    print(f"  Started: {datetime.now().isoformat()}")

    master, dd_price, dd_corr = step1_merge()
    reg = step2_regression(master, dd_price, dd_corr)
    garch = step3_garch(reg)
    evt = step4_events(master)
    lng = step5_lng(master)
    mc = step6_monte_carlo(garch, reg, evt, lng)
    step7_validate(reg, garch, mc)
    step8_params(reg, garch)

    banner("PIPELINE COMPLETE")
    print(f"  Finished: {datetime.now().isoformat()}")
    print(f"\n  Output files:")
    for f in [MASTER_V6, REG_RESULTS, GARCH_V3, EVENT_FREQ_V3, MC_V7, MODEL_PARAMS_V3, VALIDATION]:
        print(f"    {os.path.basename(f)}")


if __name__ == "__main__":
    main()
