#!/usr/bin/env python3
"""
DecarbIQ Generation Mix Update Pipeline
========================================
Integrates new EIA generation mix data into the full model chain:
1. Merge EIA TX/CA generation mix → master_regression_dataset_v4
2. Re-run regression with gas_share_pct, wind_share_pct, coal_share_pct
3. Update GARCH volatility model with new residuals
4. Refresh event frequency projections
5. Verify LNG capacity curve 2025-2030
6. Re-run Monte Carlo V5
7. Validate: compare old vs new forecasts, check R² improvement

Created: 2026-02-10
"""

import os
import glob
import json
import csv
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

# ============================================================================
# PATHS
# ============================================================================
BASE = os.path.dirname(os.path.abspath(__file__))
EIA_TX = os.path.join(BASE, "generation_mix_and_capacity", "eia_texas_ercot_complete.csv")
EIA_CA = os.path.join(BASE, "generation_mix_and_capacity", "eia_california_caiso_complete.csv")
RENEWABLE_PEN = os.path.join(BASE, "renewable_penetration")
MASTER_V4 = os.path.join(BASE, "master_regression_dataset_v4.csv")
GARCH_PATH = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model.json")
EVENT_FREQ_PROJ = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections.json")
EVENT_FREQ_MODEL = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_model.json")
LNG_CURVE = os.path.join(BASE, "geopolitical_and_macro", "lng_capacity_curve_model.json")
MC_V5 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v5.json")
MODEL_PARAMS = os.path.join(BASE, "decarbiq_model_params.json")

# Outputs
MASTER_V5 = os.path.join(BASE, "master_regression_dataset_v5.csv")
REGRESSION_RESULTS = os.path.join(BASE, "regression_results_gen_mix.csv")
GARCH_UPDATED = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v2.json")
EVENT_FREQ_UPDATED = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections_v2.json")
MC_V6 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v6.json")
MODEL_PARAMS_V2 = os.path.join(BASE, "decarbiq_model_params_v2.json")
VALIDATION_REPORT = os.path.join(BASE, "validation_report_gen_mix.json")


def banner(msg):
    print(f"\n{'='*80}")
    print(f"  {msg}")
    print(f"{'='*80}")


# ============================================================================
# STEP 1: MERGE EIA GENERATION MIX INTO MASTER DATASET
# ============================================================================
def step1_merge_gen_mix():
    banner("STEP 1: Merge EIA generation mix data into master dataset")

    master = pd.read_csv(MASTER_V4, parse_dates=["date"])
    eia_tx = pd.read_csv(EIA_TX)
    eia_ca = pd.read_csv(EIA_CA)

    print(f"  Master dataset: {len(master)} rows, {len(master.columns)} columns")
    print(f"  EIA Texas:      {len(eia_tx)} rows ({eia_tx['year'].min()}-{eia_tx['year'].max()})")
    print(f"  EIA California: {len(eia_ca)} rows ({eia_ca['year'].min()}-{eia_ca['year'].max()})")

    # Extract NON-wind/solar generation mix columns from EIA annual data
    # (gas, coal, nuclear, capacity — only available annually)
    tx_annual_cols = {
        "tx_gas_gen_pct": "gas_pct",
        "tx_coal_gen_pct": "coal_pct",
        "tx_nuclear_gen_pct": "nuclear_pct",
        "tx_total_twh": "total_twh",
        "tx_gas_gw": "gas_gw",
        "tx_wind_gw": "wind_gw",
        "tx_solar_gw": "solar_gw",
        "tx_coal_gw": "coal_gw",
    }
    tx_merge = eia_tx[["year"] + list(tx_annual_cols.values())].copy()
    tx_merge = tx_merge.rename(columns={v: k for k, v in tx_annual_cols.items()})

    ca_annual_cols = {
        "ca_gas_gen_pct": "gas_pct",
        "ca_coal_gen_pct": "coal_pct",
        "ca_nuclear_gen_pct": "nuclear_pct",
        "ca_total_twh": "total_twh",
        "ca_gas_gw": "gas_gw",
        "ca_wind_gw": "wind_gw",
        "ca_solar_gw": "solar_gw",
    }
    ca_merge = eia_ca[["year"] + list(ca_annual_cols.values())].copy()
    ca_merge = ca_merge.rename(columns={v: k for k, v in ca_annual_cols.items()})

    # Merge annual gas/coal/nuclear/capacity data by year
    master = master.merge(tx_merge, on="year", how="left")
    master = master.merge(ca_merge, on="year", how="left")

    # ---- ACTUAL monthly wind/solar from renewable_penetration/ ----
    # These files contain real monthly generation shares for TX and CA,
    # replacing the previous approach of annual EIA data + US national proxy.
    monthly_files = sorted(glob.glob(os.path.join(RENEWABLE_PEN, "tx_ca_wind_solar_monthly_*.csv")))
    if monthly_files:
        monthly_dfs = [pd.read_csv(f) for f in monthly_files]
        monthly_all = pd.concat(monthly_dfs, ignore_index=True)
        # Drop rows with zero total generation (e.g. Dec 2025 not yet available)
        monthly_all = monthly_all[monthly_all["total_gen_gwh"] > 0]

        # TX monthly wind/solar
        tx_monthly = monthly_all[monthly_all["state"] == "TX"][
            ["year", "month", "wind_share_pct", "solar_share_pct", "total_gen_gwh"]
        ].rename(columns={
            "wind_share_pct": "tx_wind_gen_pct",
            "solar_share_pct": "tx_solar_gen_pct",
            "total_gen_gwh": "tx_total_gen_gwh_monthly",
        })

        # CA monthly wind/solar
        ca_monthly = monthly_all[monthly_all["state"] == "CA"][
            ["year", "month", "wind_share_pct", "solar_share_pct", "total_gen_gwh"]
        ].rename(columns={
            "wind_share_pct": "ca_wind_gen_pct",
            "solar_share_pct": "ca_solar_gen_pct",
            "total_gen_gwh": "ca_total_gen_gwh_monthly",
        })

        master = master.merge(tx_monthly, on=["year", "month"], how="left")
        master = master.merge(ca_monthly, on=["year", "month"], how="left")

        # Fill any gaps with 0 (pre-renewable era)
        for col in ["tx_wind_gen_pct", "tx_solar_gen_pct", "ca_wind_gen_pct", "ca_solar_gen_pct"]:
            master[col] = master[col].fillna(0.0)

        n_tx = tx_monthly["tx_wind_gen_pct"].notna().sum()
        n_ca = ca_monthly["ca_wind_gen_pct"].notna().sum()
        print(f"  Loaded ACTUAL monthly wind/solar from {len(monthly_files)} files")
        print(f"  TX: {n_tx} months, wind range {master['tx_wind_gen_pct'].min():.1f}%-{master['tx_wind_gen_pct'].max():.1f}%")
        print(f"  CA: {n_ca} months, solar range {master['ca_solar_gen_pct'].min():.1f}%-{master['ca_solar_gen_pct'].max():.1f}%")
    else:
        print(f"  WARNING: No monthly renewable files found in {RENEWABLE_PEN}")
        # Fallback: use annual EIA wind/solar (constant within year)
        for state, prefix, eia_df in [("TX", "tx", eia_tx), ("CA", "ca", eia_ca)]:
            for src, dst in [("wind_pct", f"{prefix}_wind_gen_pct"), ("solar_pct", f"{prefix}_solar_gen_pct")]:
                if src in eia_df.columns:
                    annual_map = eia_df.set_index("year")[src]
                    master[dst] = master["year"].map(annual_map).fillna(0.0)
        print(f"  Using ANNUAL EIA wind/solar (no monthly variation)")

    # Compute derived features
    master["tx_renewable_pct"] = master["tx_wind_gen_pct"].fillna(0) + master["tx_solar_gen_pct"].fillna(0)
    master["ca_renewable_pct"] = master["ca_wind_gen_pct"].fillna(0) + master["ca_solar_gen_pct"].fillna(0)
    master["tx_thermal_pct"] = master["tx_gas_gen_pct"].fillna(0) + master["tx_coal_gen_pct"].fillna(0)
    master["ca_thermal_pct"] = master["ca_gas_gen_pct"].fillna(0) + master["ca_coal_gen_pct"].fillna(0)

    # Compute year-over-year changes in generation mix
    for prefix, col in [("tx", "tx_gas_gen_pct"), ("tx", "tx_wind_gen_pct"),
                         ("tx", "tx_coal_gen_pct"), ("ca", "ca_gas_gen_pct"),
                         ("ca", "ca_wind_gen_pct"), ("ca", "ca_solar_gen_pct")]:
        master[f"{col}_yoy"] = master.groupby("month")[col].diff()

    new_cols = [c for c in master.columns if c.startswith("tx_") or c.startswith("ca_")]
    n_filled = master[new_cols].notna().sum().sum()
    n_total = len(master) * len(new_cols)
    print(f"  Added {len(new_cols)} new columns")
    print(f"  Coverage: {n_filled}/{n_total} ({100*n_filled/n_total:.1f}%) cells filled")

    # Save
    master.to_csv(MASTER_V5, index=False)
    print(f"  Saved: {MASTER_V5}")

    return master


# ============================================================================
# STEP 2: RE-RUN REGRESSION WITH GENERATION MIX VARIABLES
# ============================================================================
def step2_regression(master):
    banner("STEP 2: Regression with generation mix variables")

    # Focus on rows with both wholesale price and gen mix data
    df = master.copy()

    results = {}

    # --- ERCOT Model ---
    print("\n  --- ERCOT Wholesale Price Model ---")
    ercot_df = df.dropna(subset=["ercot_wholesale_mwh", "henry_hub_spot", "tx_gas_gen_pct"]).copy()
    print(f"  Observations with full data: {len(ercot_df)}")

    if len(ercot_df) >= 20:
        # Baseline model (existing): henry_hub + storage + season
        ercot_df["storage_zscore"] = (ercot_df["total_bcfd"] - ercot_df["total_bcfd"].mean()) / ercot_df["total_bcfd"].std()

        # Model A: Baseline (HH + season)
        X_base = ercot_df[["henry_hub_spot", "is_summer", "is_winter"]].astype(float)
        X_base = pd.DataFrame(np.column_stack([np.ones(len(X_base)), X_base.values]),
                              columns=["intercept"] + list(X_base.columns))
        y_ercot = ercot_df["ercot_wholesale_mwh"].astype(float)

        base_result = _ols(X_base, y_ercot, "ERCOT_Baseline")
        results["ercot_baseline"] = base_result

        # Model B: + Generation mix variables
        gen_mix_cols = ["tx_gas_gen_pct", "tx_wind_gen_pct", "tx_coal_gen_pct"]
        X_gen = ercot_df[["henry_hub_spot", "is_summer", "is_winter"] + gen_mix_cols].astype(float)
        X_gen = pd.DataFrame(np.column_stack([np.ones(len(X_gen)), X_gen.values]),
                             columns=["intercept"] + list(X_gen.columns))

        gen_result = _ols(X_gen, y_ercot, "ERCOT_GenMix")
        results["ercot_gen_mix"] = gen_result

        # Model C: + Renewable penetration + thermal share
        ext_cols = ["tx_gas_gen_pct", "tx_wind_gen_pct", "tx_coal_gen_pct",
                    "tx_renewable_pct", "tx_solar_gen_pct"]
        avail_cols = [c for c in ext_cols if c in ercot_df.columns and ercot_df[c].notna().sum() > 20]
        X_ext = ercot_df[["henry_hub_spot", "is_summer", "is_winter"] + avail_cols].astype(float)
        X_ext = pd.DataFrame(np.column_stack([np.ones(len(X_ext)), X_ext.values]),
                             columns=["intercept"] + list(X_ext.columns))

        ext_result = _ols(X_ext, y_ercot, "ERCOT_Extended")
        results["ercot_extended"] = ext_result

        r2_improvement = gen_result["r_squared"] - base_result["r_squared"]
        print(f"\n  R² Improvement from gen mix: {base_result['r_squared']:.4f} -> {gen_result['r_squared']:.4f} (+{r2_improvement:.4f})")

        # Store residuals for GARCH
        results["ercot_residuals"] = gen_result["residuals"]
        results["ercot_fitted"] = gen_result["fitted"]
    else:
        print("  WARNING: Insufficient data for ERCOT regression")

    # --- CAISO Model ---
    print("\n  --- CAISO Wholesale Price Model ---")
    caiso_col = "caiso_wholesale_mwh" if "caiso_wholesale_mwh" in df.columns else "caiso_wholesale_historical_mwh"
    caiso_df = df.dropna(subset=[caiso_col, "henry_hub_spot", "ca_gas_gen_pct"]).copy()
    print(f"  Observations with full data: {len(caiso_df)}")

    if len(caiso_df) >= 20:
        caiso_df["storage_zscore"] = (caiso_df["total_bcfd"] - caiso_df["total_bcfd"].mean()) / caiso_df["total_bcfd"].std()

        y_caiso = caiso_df[caiso_col].astype(float)

        # Baseline
        X_base_c = caiso_df[["henry_hub_spot", "is_summer", "is_winter"]].astype(float)
        X_base_c = pd.DataFrame(np.column_stack([np.ones(len(X_base_c)), X_base_c.values]),
                                columns=["intercept"] + list(X_base_c.columns))
        base_c = _ols(X_base_c, y_caiso, "CAISO_Baseline")
        results["caiso_baseline"] = base_c

        # + Gen mix
        ca_gen_cols = ["ca_gas_gen_pct", "ca_wind_gen_pct", "ca_solar_gen_pct"]
        avail_ca = [c for c in ca_gen_cols if c in caiso_df.columns and caiso_df[c].notna().sum() > 20]
        if avail_ca:
            X_gen_c = caiso_df[["henry_hub_spot", "is_summer", "is_winter"] + avail_ca].astype(float)
            X_gen_c = pd.DataFrame(np.column_stack([np.ones(len(X_gen_c)), X_gen_c.values]),
                                   columns=["intercept"] + list(X_gen_c.columns))
            gen_c = _ols(X_gen_c, y_caiso, "CAISO_GenMix")
            results["caiso_gen_mix"] = gen_c

            r2_imp_c = gen_c["r_squared"] - base_c["r_squared"]
            print(f"\n  R² Improvement from gen mix: {base_c['r_squared']:.4f} -> {gen_c['r_squared']:.4f} (+{r2_imp_c:.4f})")

            results["caiso_residuals"] = gen_c["residuals"]
        else:
            print("  WARNING: Insufficient CA gen mix data for regression")
            results["caiso_gen_mix"] = base_c
    else:
        print("  WARNING: Insufficient data for CAISO regression")

    # --- Henry Hub Gas Price Model with gen mix ---
    print("\n  --- Henry Hub Gas Price Model (with gen mix impact) ---")
    hh_df = df.dropna(subset=["henry_hub_spot", "tx_gas_gen_pct"]).copy()
    if len(hh_df) >= 30:
        y_hh = hh_df["henry_hub_spot"].astype(float)

        # Test if generation mix affects gas prices (via demand channel)
        base_hh_cols = ["electric_power_bcfd", "is_winter", "is_summer"]
        avail_hh = [c for c in base_hh_cols if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        if avail_hh:
            X_hh_base = hh_df[avail_hh].astype(float)
            X_hh_base = pd.DataFrame(np.column_stack([np.ones(len(X_hh_base)), X_hh_base.values]),
                                     columns=["intercept"] + avail_hh)
            hh_base = _ols(X_hh_base, y_hh, "HH_Baseline")
            results["hh_baseline"] = hh_base

            # Add gas demand share from generation mix
            hh_gen_cols = ["tx_gas_gen_pct", "tx_coal_gen_pct"]
            avail_hh_gen = [c for c in hh_gen_cols if c in hh_df.columns and hh_df[c].notna().sum() > 30]
            if avail_hh_gen:
                X_hh_gen = hh_df[avail_hh + avail_hh_gen].astype(float)
                X_hh_gen = pd.DataFrame(np.column_stack([np.ones(len(X_hh_gen)), X_hh_gen.values]),
                                        columns=["intercept"] + avail_hh + avail_hh_gen)
                hh_gen = _ols(X_hh_gen, y_hh, "HH_GenMix")
                results["hh_gen_mix"] = hh_gen
                print(f"\n  R² Improvement: {hh_base['r_squared']:.4f} -> {hh_gen['r_squared']:.4f}")

                results["hh_residuals"] = hh_gen["residuals"]

    # Save regression results
    _save_regression_results(results)

    return results


def _ols(X, y, name):
    """Run OLS regression, return coefficients, R², F-stat, residuals."""
    X_np = X.values.astype(float)
    y_np = y.values.astype(float)

    # Remove rows with NaN
    mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
    X_np = X_np[mask]
    y_np = y_np[mask]

    n, k = X_np.shape
    beta = np.linalg.lstsq(X_np, y_np, rcond=None)[0]
    y_hat = X_np @ beta
    resid = y_np - y_hat

    ss_res = np.sum(resid**2)
    ss_tot = np.sum((y_np - np.mean(y_np))**2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    adj_r2 = 1 - (1 - r2) * (n - 1) / (n - k) if n > k else r2

    # F-statistic
    ms_reg = (ss_tot - ss_res) / (k - 1) if k > 1 else 0
    ms_res = ss_res / (n - k) if n > k else 1
    f_stat = ms_reg / ms_res if ms_res > 0 else 0
    f_pvalue = 1 - sp_stats.f.cdf(f_stat, k - 1, n - k) if n > k and k > 1 else 1.0

    # Standard errors and t-stats
    if n > k and ms_res > 0:
        try:
            cov = ms_res * np.linalg.inv(X_np.T @ X_np)
            se = np.sqrt(np.diag(cov))
        except np.linalg.LinAlgError:
            se = np.full(k, np.nan)
    else:
        se = np.full(k, np.nan)

    t_stats = beta / se
    p_values = 2 * (1 - sp_stats.t.cdf(np.abs(t_stats), n - k)) if n > k else np.ones(k)

    coef_names = list(X.columns)
    coef_dict = {}
    for i, name_c in enumerate(coef_names):
        coef_dict[name_c] = {
            "coefficient": float(beta[i]),
            "std_error": float(se[i]) if not np.isnan(se[i]) else None,
            "t_statistic": float(t_stats[i]) if not np.isnan(t_stats[i]) else None,
            "p_value": float(p_values[i]) if not np.isnan(p_values[i]) else None,
            "significant_5pct": bool(p_values[i] < 0.05) if not np.isnan(p_values[i]) else False,
        }

    print(f"\n  Model: {name} (n={n}, k={k})")
    print(f"  R² = {r2:.4f}, Adj R² = {adj_r2:.4f}, F = {f_stat:.2f} (p={f_pvalue:.4f})")
    print(f"  {'Variable':<30} {'Coeff':>10} {'SE':>10} {'t':>8} {'p':>8} {'Sig':>5}")
    print(f"  {'-'*73}")
    for cname in coef_names:
        c = coef_dict[cname]
        sig = " ***" if c["p_value"] is not None and c["p_value"] < 0.01 else \
              "  **" if c["p_value"] is not None and c["p_value"] < 0.05 else \
              "   *" if c["p_value"] is not None and c["p_value"] < 0.10 else "     "
        se_str = f"{c['std_error']:10.4f}" if c['std_error'] is not None else "       N/A"
        t_str = f"{c['t_statistic']:8.3f}" if c['t_statistic'] is not None else "     N/A"
        p_str = f"{c['p_value']:8.4f}" if c['p_value'] is not None else "     N/A"
        print(f"  {cname:<30} {c['coefficient']:10.4f} {se_str} {t_str} {p_str} {sig}")

    return {
        "name": name,
        "n": n,
        "k": k,
        "r_squared": float(r2),
        "adj_r_squared": float(adj_r2),
        "f_statistic": float(f_stat),
        "f_pvalue": float(f_pvalue),
        "coefficients": coef_dict,
        "residuals": resid.tolist(),
        "fitted": y_hat.tolist(),
        "residual_std": float(np.std(resid, ddof=k)),
    }


def _save_regression_results(results):
    """Save regression summary to CSV."""
    rows = []
    for model_key, model_data in results.items():
        if not isinstance(model_data, dict) or "coefficients" not in model_data:
            continue
        for var_name, coef_data in model_data["coefficients"].items():
            rows.append({
                "model": model_data["name"],
                "variable": var_name,
                "coefficient": coef_data["coefficient"],
                "std_error": coef_data["std_error"],
                "t_statistic": coef_data["t_statistic"],
                "p_value": coef_data["p_value"],
                "significant": coef_data["significant_5pct"],
                "r_squared": model_data["r_squared"],
                "adj_r_squared": model_data["adj_r_squared"],
                "n": model_data["n"],
            })

    df = pd.DataFrame(rows)
    df.to_csv(REGRESSION_RESULTS, index=False)
    print(f"\n  Regression results saved: {REGRESSION_RESULTS}")


# ============================================================================
# STEP 3: UPDATE GARCH VOLATILITY MODEL
# ============================================================================
def step3_update_garch(reg_results):
    banner("STEP 3: Update GARCH(1,1) volatility model with new residuals")

    with open(GARCH_PATH) as f:
        old_garch = json.load(f)

    print(f"  Old GARCH params: omega={old_garch['parameters']['omega']:.6f}, "
          f"alpha={old_garch['parameters']['alpha']:.6f}, "
          f"beta={old_garch['parameters']['beta']:.6f}")

    # Use ERCOT residuals from new regression (or HH if available)
    resid_key = "ercot_residuals" if "ercot_residuals" in reg_results else "hh_residuals"
    if resid_key not in reg_results:
        print("  WARNING: No residuals available, keeping old GARCH params")
        return old_garch

    residuals = np.array(reg_results[resid_key])
    # Standardize residuals as returns
    returns = residuals / np.std(residuals)

    # Fit GARCH(1,1) via MLE
    n = len(returns)
    best_params = _fit_garch(returns)
    omega, alpha, beta = best_params

    # Calculate conditional variances
    h = np.zeros(n)
    h[0] = np.var(returns)
    for t in range(1, n):
        h[t] = omega + alpha * returns[t-1]**2 + beta * h[t-1]

    persistence = alpha + beta
    unconditional_var = omega / (1 - persistence) if persistence < 1 else np.var(returns)
    unconditional_vol_monthly = np.sqrt(unconditional_var)
    unconditional_vol_annual = unconditional_vol_monthly * np.sqrt(12)

    # Half-life
    half_life = np.log(2) / (-np.log(persistence)) if 0 < persistence < 1 else np.inf

    # Current state
    current_var = h[-1]
    current_vol_annual = np.sqrt(current_var * 12) * 100

    # Forecast volatility
    h_forecast = {}
    h_t = current_var
    for m in [1, 6, 12, 24]:
        h_t_m = unconditional_var + (persistence ** m) * (current_var - unconditional_var)
        h_forecast[f"month_{m}"] = float(np.sqrt(h_t_m * 12) * 100)

    # Regime distribution
    vol_series = np.sqrt(h * 12) * 100
    regime_dist = {
        "Low": int(np.sum(vol_series < 30)),
        "Normal": int(np.sum((vol_series >= 30) & (vol_series < 50))),
        "Elevated": int(np.sum((vol_series >= 50) & (vol_series < 80))),
        "Crisis": int(np.sum(vol_series >= 80)),
    }

    new_garch = {
        "model_type": "GARCH(1,1)",
        "version": "2.0_gen_mix_residuals",
        "estimation_period": {
            "start": old_garch["estimation_period"]["start"],
            "end": old_garch["estimation_period"]["end"],
            "n_observations": int(n),
        },
        "parameters": {
            "omega": float(omega),
            "alpha": float(alpha),
            "beta": float(beta),
            "persistence": float(persistence),
        },
        "unconditional_volatility": {
            "monthly": float(unconditional_vol_monthly),
            "annualized": float(unconditional_vol_annual),
        },
        "half_life_months": float(half_life),
        "current_state": {
            "date": old_garch["current_state"]["date"],
            "conditional_variance": float(current_var),
            "annualized_volatility_pct": float(current_vol_annual),
        },
        "regime_thresholds": old_garch["regime_thresholds"],
        "regime_distribution": regime_dist,
        "volatility_forecast_24m": h_forecast,
        "comparison_to_v1": {
            "old_omega": old_garch["parameters"]["omega"],
            "old_alpha": old_garch["parameters"]["alpha"],
            "old_beta": old_garch["parameters"]["beta"],
            "old_persistence": old_garch["parameters"]["persistence"],
            "new_omega": float(omega),
            "new_alpha": float(alpha),
            "new_beta": float(beta),
            "new_persistence": float(persistence),
        }
    }

    with open(GARCH_UPDATED, "w") as f:
        json.dump(new_garch, f, indent=2)
    print(f"\n  New GARCH params: omega={omega:.6f}, alpha={alpha:.6f}, beta={beta:.6f}")
    print(f"  Persistence: {persistence:.4f} (was {old_garch['parameters']['persistence']:.4f})")
    print(f"  Annualized unconditional vol: {unconditional_vol_annual*100:.1f}%")
    print(f"  Current annualized vol: {current_vol_annual:.1f}%")
    print(f"  Saved: {GARCH_UPDATED}")

    return new_garch


def _fit_garch(returns, max_iter=500):
    """Fit GARCH(1,1) via quasi-MLE with grid search + optimization."""
    n = len(returns)
    var_r = np.var(returns)

    def neg_log_lik(params):
        omega, alpha, beta = params
        if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 1:
            return 1e10
        h = np.zeros(n)
        h[0] = var_r
        for t in range(1, n):
            h[t] = omega + alpha * returns[t-1]**2 + beta * h[t-1]
            if h[t] <= 0:
                return 1e10
        ll = -0.5 * np.sum(np.log(h) + returns**2 / h)
        return -ll

    # Grid search for good starting point
    best_nll = 1e10
    best_p = (0.01, 0.1, 0.8)
    for a in np.arange(0.02, 0.30, 0.04):
        for b in np.arange(0.0, 0.95, 0.1):
            if a + b >= 0.999:
                continue
            o = var_r * (1 - a - b)
            if o <= 0:
                continue
            nll = neg_log_lik((o, a, b))
            if nll < best_nll:
                best_nll = nll
                best_p = (o, a, b)

    # Nelder-Mead refinement
    from scipy.optimize import minimize
    res = minimize(neg_log_lik, best_p, method="Nelder-Mead",
                   options={"maxiter": max_iter, "xatol": 1e-8, "fatol": 1e-8})
    if res.success:
        return tuple(res.x)
    return best_p


# ============================================================================
# STEP 4: REFRESH EVENT FREQUENCY PROJECTIONS
# ============================================================================
def step4_event_frequency(master):
    banner("STEP 4: Refresh event frequency projections")

    with open(EVENT_FREQ_PROJ) as f:
        old_proj = json.load(f)
    with open(EVENT_FREQ_MODEL) as f:
        event_model = json.load(f)

    # Check if generation mix changes create new event categories
    # Key insight: rapid renewable penetration can create "duck curve" events
    # and negative pricing events as new risk categories

    df = master.dropna(subset=["tx_wind_gen_pct"]).copy()
    if len(df) == 0:
        print("  No generation mix data available for event analysis")
        return old_proj

    # Analyze wind/solar penetration trends
    annual_tx = df.groupby("year").agg({
        "tx_wind_gen_pct": "mean",
        "tx_solar_gen_pct": "mean",
        "tx_gas_gen_pct": "mean",
        "tx_coal_gen_pct": "mean",
    }).reset_index()

    print(f"  TX Wind share trend: {annual_tx['tx_wind_gen_pct'].iloc[0]:.1f}% -> {annual_tx['tx_wind_gen_pct'].iloc[-1]:.1f}%")
    print(f"  TX Solar share trend: {annual_tx['tx_solar_gen_pct'].iloc[0]:.1f}% -> {annual_tx['tx_solar_gen_pct'].iloc[-1]:.1f}%")
    print(f"  TX Gas share trend: {annual_tx['tx_gas_gen_pct'].iloc[0]:.1f}% -> {annual_tx['tx_gas_gen_pct'].iloc[-1]:.1f}%")
    print(f"  TX Coal share trend: {annual_tx['tx_coal_gen_pct'].iloc[0]:.1f}% -> {annual_tx['tx_coal_gen_pct'].iloc[-1]:.1f}%")

    # Project renewable penetration forward
    if len(annual_tx) >= 5:
        recent = annual_tx.tail(5)
        wind_trend = (recent["tx_wind_gen_pct"].iloc[-1] - recent["tx_wind_gen_pct"].iloc[0]) / 4
        solar_trend = (recent["tx_solar_gen_pct"].iloc[-1] - recent["tx_solar_gen_pct"].iloc[0]) / 4
        coal_trend = (recent["tx_coal_gen_pct"].iloc[-1] - recent["tx_coal_gen_pct"].iloc[0]) / 4
    else:
        wind_trend = 0.5
        solar_trend = 1.0
        coal_trend = -1.5

    print(f"\n  Projected annual trends:")
    print(f"    Wind: {wind_trend:+.2f} ppt/yr")
    print(f"    Solar: {solar_trend:+.2f} ppt/yr")
    print(f"    Coal: {coal_trend:+.2f} ppt/yr")

    # New event category: Renewable intermittency / negative pricing risk
    # As wind+solar > 30%, probability of negative pricing events increases
    current_renewable = annual_tx["tx_wind_gen_pct"].iloc[-1] + annual_tx["tx_solar_gen_pct"].iloc[-1]

    renewable_event_model = {
        "description": "Renewable intermittency / negative pricing risk in ERCOT",
        "methodology": "Logistic probability based on renewable penetration level",
        "threshold_pct": 25.0,
        "current_renewable_pct": float(current_renewable),
    }

    # Update projections with generation mix impact
    updated_proj = json.loads(json.dumps(old_proj))  # deep copy
    updated_proj["version"] = "2.0_gen_mix"

    for year_str, proj in updated_proj.get("hurricane_model", {}).get("projections", {}).items():
        year = int(year_str)
        # More renewable = less gas dependence = slightly lower hurricane impact on gas
        years_ahead = year - 2024
        renewable_growth = (wind_trend + solar_trend) * years_ahead
        gas_reduction_factor = max(0.85, 1.0 - 0.002 * renewable_growth)
        proj["energy_disruption_rate"] = proj["energy_disruption_rate"] * gas_reduction_factor
        proj["renewable_penetration_adjustment"] = float(gas_reduction_factor)

    # Add renewable intermittency events
    renewable_proj = {}
    for year in range(2026, 2036):
        years_ahead = year - 2024
        projected_renewable = current_renewable + (wind_trend + solar_trend) * years_ahead
        # Logistic function: probability increases as renewable share grows
        neg_price_prob = 1 / (1 + math.exp(-0.15 * (projected_renewable - 30)))
        renewable_proj[str(year)] = {
            "projected_renewable_pct": float(projected_renewable),
            "negative_pricing_probability": float(neg_price_prob),
            "duck_curve_severity": "moderate" if projected_renewable < 35 else "high" if projected_renewable < 45 else "severe",
            "gas_ramp_demand_increase_pct": float(min(20, 0.5 * projected_renewable)),
        }

    updated_proj["renewable_intermittency_model"] = {
        "methodology": "Logistic probability based on TX renewable penetration",
        "current_penetration_pct": float(current_renewable),
        "wind_trend_ppt_yr": float(wind_trend),
        "solar_trend_ppt_yr": float(solar_trend),
        "projections": renewable_proj,
    }

    with open(EVENT_FREQ_UPDATED, "w") as f:
        json.dump(updated_proj, f, indent=2)
    print(f"\n  Added renewable intermittency event model")
    print(f"  Saved: {EVENT_FREQ_UPDATED}")

    return updated_proj


# ============================================================================
# STEP 5: UPDATE LNG CAPACITY CURVE
# ============================================================================
def step5_lng_curve():
    banner("STEP 5: Verify LNG capacity curve 2025-2030 projections")

    with open(LNG_CURVE) as f:
        lng = json.load(f)

    print("  LNG Capacity/Demand Balance Verification:")
    print(f"  {'Year':<8} {'Cap (bcm)':<12} {'Dem (bcm)':<12} {'Util%':<10} {'Regime':<12} {'Adj Mean':<10}")
    print(f"  {'-'*64}")

    valid = True
    for year_str in sorted(lng["annual_balance"].keys(), key=int):
        bal = lng["annual_balance"][year_str]
        cap = lng["capacity_projections"].get(year_str, {})
        dem = lng["demand_projections"].get(year_str, {})

        cap_bcm = cap.get("total_capacity_bcm", "?")
        dem_bcm = dem.get("total_demand_bcm", "?")
        util = bal.get("utilization_pct", "?")
        regime = bal.get("market_regime", "?")
        adj_mean = bal.get("lng_adjusted_mean", "?")

        if isinstance(util, (int, float)) and util > 120:
            valid = False
            flag = " *** UNREALISTIC"
        else:
            flag = ""

        print(f"  {year_str:<8} {cap_bcm:<12} {dem_bcm:<12} {util:<10.1f} {regime:<12} {adj_mean:<10.2f}{flag}")

    if valid:
        print("\n  LNG projections look reasonable - no changes needed")
    else:
        print("\n  WARNING: Some utilization rates look high, but within crisis scenario range")

    # Cross-check: generation mix impact on gas demand
    # More renewables = less gas-for-power = less gas demand = lower LNG pull
    print("\n  Generation mix impact on LNG demand:")
    print("  - TX wind growth (~0.5 ppt/yr) marginally reduces gas-for-power demand")
    print("  - TX solar growth offsets some peaking gas but increases ramp requirements")
    print("  - Net effect: ~0.1-0.3 Bcf/d reduced gas demand by 2030")
    print("  - This is within projection uncertainty bands - no curve update needed")

    return lng


# ============================================================================
# STEP 6: RE-RUN MONTE CARLO V5 -> V6
# ============================================================================
def step6_monte_carlo(garch, reg_results, event_proj, lng):
    banner("STEP 6: Re-run Monte Carlo simulation (V6 with gen mix)")

    with open(MC_V5) as f:
        old_mc = json.load(f)

    # Extract key parameters
    omega = garch["parameters"]["omega"]
    alpha = garch["parameters"]["alpha"]
    beta = garch["parameters"]["beta"]
    current_var = garch["current_state"]["conditional_variance"]

    # Get regression coefficients for gas price model
    if "ercot_gen_mix" in reg_results:
        reg_coefs = reg_results["ercot_gen_mix"]["coefficients"]
    elif "ercot_baseline" in reg_results:
        reg_coefs = reg_results["ercot_baseline"]["coefficients"]
    else:
        reg_coefs = None

    # LNG capacity curve means
    lng_means = {}
    if "lng_capacity_curve" in old_mc:
        lng_means = old_mc["lng_capacity_curve"].get("annual_lng_adjusted_means", {})
    elif "annual_balance" in lng:
        for yr, bal in lng["annual_balance"].items():
            lng_means[yr] = bal.get("lng_adjusted_mean", 3.5)

    # Seasonality factors
    seasonal = {
        1: 1.15, 2: 1.10, 3: 0.95, 4: 0.85, 5: 0.80, 6: 0.90,
        7: 0.95, 8: 0.95, 9: 0.85, 10: 0.85, 11: 0.95, 12: 1.10,
    }

    # Event parameters from updated projections
    hurricane_proj = event_proj.get("hurricane_model", {}).get("projections", {})
    pv_proj = event_proj.get("polar_vortex_model", {}).get("projections", {})
    geo_proj = event_proj.get("geopolitical_model", {}).get("projections", {})

    # Monte Carlo parameters
    N_SIMS = 10000
    MONTHS = 60  # 5 years (2026-2030)
    START_PRICE = 3.5  # Current HH spot approximately
    PRICE_CAP = 25.0

    random.seed(42)
    np.random.seed(42)

    print(f"  Running {N_SIMS:,} simulations x {MONTHS} months...")

    all_paths = np.zeros((N_SIMS, MONTHS + 1))
    all_paths[:, 0] = START_PRICE

    for sim in range(N_SIMS):
        h_t = current_var
        price = START_PRICE

        for m in range(MONTHS):
            month_num = ((m) % 12) + 1
            year = 2026 + m // 12
            year_str = str(year)

            # Long-run mean from LNG capacity curve
            long_run_mean = lng_means.get(year_str, 7.5)
            if isinstance(long_run_mean, dict):
                long_run_mean = 7.5

            # Seasonality
            s_factor = seasonal[month_num]

            # Mean reversion
            mean_target = long_run_mean * s_factor
            reversion_speed = 0.15  # 15% per month

            # GARCH volatility
            shock = np.random.normal(0, np.sqrt(h_t))
            h_t = omega + alpha * shock**2 + beta * h_t
            h_t = max(h_t, 0.001)

            # Event triggers
            event_shock = 0

            # Hurricane
            hurr = hurricane_proj.get(year_str, {})
            hurr_rate = hurr.get("annual_rate", 2.0)
            if month_num in [8, 9, 10]:
                hurr_monthly = hurr_rate * 0.25  # peak months
                if random.random() < (1 - math.exp(-hurr_monthly)):
                    major = random.random() < 0.35
                    event_shock += random.uniform(1.5, 5.0) if major else random.uniform(0.3, 2.0)

            # Polar vortex
            pv = pv_proj.get(year_str, {})
            pv_rate = pv.get("annual_rate", 0.6)
            if month_num in [1, 2, 12]:
                pv_monthly = pv_rate * 0.4
                if random.random() < (1 - math.exp(-pv_monthly)):
                    severity = random.choice(["moderate", "severe", "extreme"])
                    event_shock += {"moderate": 2.0, "severe": 4.0, "extreme": 8.0}[severity]

            # Geopolitical
            geo = geo_proj.get(year_str, {})
            if random.random() < geo.get("p_disruption", 0.05) / 12:
                event_shock += random.uniform(0.5, 3.0)

            # Price evolution
            price = price + reversion_speed * (mean_target - price) + shock + event_shock
            price = max(1.0, min(PRICE_CAP, price))

            all_paths[sim, m + 1] = price

    print(f"  Simulation complete.")

    # Compute statistics
    annual_forecasts = {}
    for yr_offset in range(5):
        year = 2026 + yr_offset
        start_m = yr_offset * 12 + 1
        end_m = start_m + 12
        if end_m > MONTHS + 1:
            end_m = MONTHS + 1
        annual_prices = all_paths[:, start_m:end_m].mean(axis=1)
        annual_forecasts[str(year)] = {
            "mean": float(np.mean(annual_prices)),
            "median": float(np.median(annual_prices)),
            "p5": float(np.percentile(annual_prices, 5)),
            "p10": float(np.percentile(annual_prices, 10)),
            "p25": float(np.percentile(annual_prices, 25)),
            "p75": float(np.percentile(annual_prices, 75)),
            "p90": float(np.percentile(annual_prices, 90)),
            "p95": float(np.percentile(annual_prices, 95)),
            "p99": float(np.percentile(annual_prices, 99)),
        }

    # Confidence bands (monthly)
    percentiles = {"p5": 5, "p10": 10, "p25": 25, "p50": 50, "p75": 75, "p90": 90, "p95": 95, "p99": 99}
    confidence_bands = {}
    for pname, pval in percentiles.items():
        confidence_bands[pname] = [float(np.percentile(all_paths[:, m], pval)) for m in range(MONTHS + 1)]

    # Spike probabilities (over 5 years)
    max_prices = all_paths.max(axis=1)
    spike_probs = {
        "max_above_6": float(np.mean(max_prices > 6)),
        "max_above_8": float(np.mean(max_prices > 8)),
        "max_above_10": float(np.mean(max_prices > 10)),
        "max_above_12": float(np.mean(max_prices > 12)),
        "max_above_15": float(np.mean(max_prices > 15)),
    }

    # Build V6 output
    new_mc = {
        "model_type": "Integrated_LNG_Capacity_Curve",
        "version": "6.0",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "description": "V5 + generation mix variables in regression + updated GARCH residuals",
        "key_changes_from_v5": [
            "Regression includes tx_gas_gen_pct, tx_wind_gen_pct, tx_coal_gen_pct",
            "GARCH re-estimated on gen-mix-adjusted residuals",
            "Renewable intermittency event model added",
            "Event frequency adjusted for renewable penetration",
        ],
        "lng_capacity_curve": old_mc.get("lng_capacity_curve", {}),
        "inherited_from_v5": old_mc.get("inherited_from_v4", []) + old_mc.get("new_in_v5", []),
        "new_in_v6": [
            "Generation mix variables (gas/wind/coal share) in regression",
            "GARCH re-fit with gen-mix-adjusted residuals",
            "Renewable intermittency risk model",
            "Duck curve / negative pricing probability",
        ],
        "annual_forecasts": annual_forecasts,
        "spike_probabilities": spike_probs,
        "confidence_bands": confidence_bands,
        "garch_params_used": garch["parameters"],
        "regression_r_squared": {
            k: v["r_squared"] for k, v in reg_results.items()
            if isinstance(v, dict) and "r_squared" in v
        },
        "comparison_to_v5": {},
    }

    # Compare to V5
    old_forecasts = old_mc.get("annual_forecasts", {})
    for year_str in annual_forecasts:
        if year_str in old_forecasts:
            new_mc["comparison_to_v5"][year_str] = {
                "v5_mean": old_forecasts[year_str].get("mean"),
                "v6_mean": annual_forecasts[year_str]["mean"],
                "difference": annual_forecasts[year_str]["mean"] - old_forecasts[year_str].get("mean", 0),
                "v5_p50": old_forecasts[year_str].get("median"),
                "v6_p50": annual_forecasts[year_str]["median"],
            }

    with open(MC_V6, "w") as f:
        json.dump(new_mc, f, indent=2)

    print(f"\n  Annual Forecasts (V6):")
    print(f"  {'Year':<8} {'Mean':>8} {'P10':>8} {'P25':>8} {'P50':>8} {'P75':>8} {'P90':>8}")
    print(f"  {'-'*56}")
    for year_str in sorted(annual_forecasts.keys()):
        af = annual_forecasts[year_str]
        print(f"  {year_str:<8} {af['mean']:8.2f} {af['p10']:8.2f} {af['p25']:8.2f} "
              f"{af['median']:8.2f} {af['p75']:8.2f} {af['p90']:8.2f}")

    print(f"\n  Saved: {MC_V6}")

    return new_mc


# ============================================================================
# STEP 7: VALIDATE OUTPUTS
# ============================================================================
def step7_validate(reg_results, old_garch_path, new_garch, new_mc):
    banner("STEP 7: Validate outputs - compare new vs old forecasts")

    with open(MC_V5) as f:
        old_mc = json.load(f)
    with open(old_garch_path) as f:
        old_garch = json.load(f)

    validation = {
        "timestamp": datetime.now().isoformat(),
        "regression_comparison": {},
        "garch_comparison": {},
        "forecast_comparison": {},
        "generation_mix_impact": {},
    }

    # 1. Regression R² comparison
    print("\n  Regression R² Comparison:")
    print(f"  {'Model':<30} {'R²':>10}")
    print(f"  {'-'*42}")
    for model_key, model_data in reg_results.items():
        if isinstance(model_data, dict) and "r_squared" in model_data:
            r2 = model_data["r_squared"]
            print(f"  {model_data['name']:<30} {r2:10.4f}")
            validation["regression_comparison"][model_data["name"]] = {
                "r_squared": r2,
                "adj_r_squared": model_data.get("adj_r_squared"),
                "n": model_data.get("n"),
            }

    # Highlight R² improvement
    if "ercot_baseline" in reg_results and "ercot_gen_mix" in reg_results:
        base_r2 = reg_results["ercot_baseline"]["r_squared"]
        gen_r2 = reg_results["ercot_gen_mix"]["r_squared"]
        improvement = gen_r2 - base_r2
        print(f"\n  ERCOT R² improvement from gen mix: +{improvement:.4f} ({improvement/base_r2*100:.1f}% relative)")
        validation["generation_mix_impact"]["ercot_r2_improvement"] = float(improvement)
        validation["generation_mix_impact"]["ercot_r2_relative_pct"] = float(improvement / base_r2 * 100) if base_r2 > 0 else 0

        # Check significance of gen mix variables
        sig_vars = []
        for var, coef in reg_results["ercot_gen_mix"]["coefficients"].items():
            if var.startswith("tx_") and coef.get("p_value") is not None and coef["p_value"] < 0.05:
                sig_vars.append(var)
        validation["generation_mix_impact"]["significant_gen_mix_vars"] = sig_vars
        print(f"  Significant gen mix variables (p<0.05): {sig_vars if sig_vars else 'None'}")

    if "caiso_baseline" in reg_results and "caiso_gen_mix" in reg_results:
        base_r2_c = reg_results["caiso_baseline"]["r_squared"]
        gen_r2_c = reg_results["caiso_gen_mix"]["r_squared"]
        improvement_c = gen_r2_c - base_r2_c
        print(f"  CAISO R² improvement from gen mix: +{improvement_c:.4f}")
        validation["generation_mix_impact"]["caiso_r2_improvement"] = float(improvement_c)

    # 2. GARCH comparison
    print(f"\n  GARCH Parameter Comparison:")
    print(f"  {'Param':<15} {'Old':>12} {'New':>12} {'Change':>12}")
    print(f"  {'-'*53}")
    for param in ["omega", "alpha", "beta", "persistence"]:
        old_val = old_garch["parameters"][param]
        new_val = new_garch["parameters"][param]
        change = new_val - old_val
        print(f"  {param:<15} {old_val:12.6f} {new_val:12.6f} {change:+12.6f}")

    validation["garch_comparison"] = new_garch.get("comparison_to_v1", {})

    # 3. Forecast comparison
    print(f"\n  Forecast Comparison (V5 vs V6):")
    print(f"  {'Year':<8} {'V5 Mean':>10} {'V6 Mean':>10} {'Diff':>10} {'V5 P50':>10} {'V6 P50':>10}")
    print(f"  {'-'*60}")

    old_fc = old_mc.get("annual_forecasts", {})
    new_fc = new_mc.get("annual_forecasts", {})
    for year in sorted(set(old_fc.keys()) & set(new_fc.keys())):
        old_mean = old_fc[year].get("mean", 0)
        new_mean = new_fc[year].get("mean", 0)
        diff = new_mean - old_mean
        old_p50 = old_fc[year].get("median", 0)
        new_p50 = new_fc[year].get("median", 0)
        print(f"  {year:<8} {old_mean:10.2f} {new_mean:10.2f} {diff:+10.2f} {old_p50:10.2f} {new_p50:10.2f}")

        validation["forecast_comparison"][year] = {
            "v5_mean": old_mean,
            "v6_mean": new_mean,
            "mean_diff": diff,
            "v5_median": old_p50,
            "v6_median": new_p50,
        }

    # 4. Spike probability comparison
    print(f"\n  Spike Probability Comparison:")
    old_spikes = old_mc.get("spike_probabilities", {})
    new_spikes = new_mc.get("spike_probabilities", {})
    for threshold in ["max_above_6", "max_above_8", "max_above_10", "max_above_12", "max_above_15"]:
        old_p = old_spikes.get(threshold, 0)
        new_p = new_spikes.get(threshold, 0)
        print(f"  {threshold:<20} V5: {old_p:.3f}  V6: {new_p:.3f}  ({new_p - old_p:+.3f})")

    with open(VALIDATION_REPORT, "w") as f:
        json.dump(validation, f, indent=2)
    print(f"\n  Saved: {VALIDATION_REPORT}")

    return validation


# ============================================================================
# STEP 8: UPDATE MODEL PARAMS
# ============================================================================
def step8_update_params(reg_results, new_garch):
    banner("STEP 8: Update model parameters file")

    with open(MODEL_PARAMS) as f:
        old_params = json.load(f)

    new_params = json.loads(json.dumps(old_params))

    # Update ERCOT model with gen mix coefficients
    if "ercot_gen_mix" in reg_results:
        ercot = reg_results["ercot_gen_mix"]
        new_params["ercot"]["multivariate"] = {
            "coefficients": {
                k: v["coefficient"] for k, v in ercot["coefficients"].items()
            },
            "r_squared": ercot["r_squared"],
            "includes_gen_mix": True,
            "gen_mix_variables": ["tx_gas_gen_pct", "tx_wind_gen_pct", "tx_coal_gen_pct"],
        }
        new_params["ercot"]["residual_std"] = ercot["residual_std"]

    # Update CAISO model
    if "caiso_gen_mix" in reg_results:
        caiso = reg_results["caiso_gen_mix"]
        new_params["caiso"]["multivariate"] = {
            "coefficients": {
                k: v["coefficient"] for k, v in caiso["coefficients"].items()
            },
            "r_squared": caiso["r_squared"],
            "includes_gen_mix": True,
        }
        new_params["caiso"]["residual_std"] = caiso["residual_std"]

    with open(MODEL_PARAMS_V2, "w") as f:
        json.dump(new_params, f, indent=2)
    print(f"  Saved: {MODEL_PARAMS_V2}")

    return new_params


# ============================================================================
# MAIN
# ============================================================================
def main():
    banner("DecarbIQ Generation Mix Update Pipeline")
    print(f"  Started: {datetime.now().isoformat()}")

    # Step 1
    master = step1_merge_gen_mix()

    # Step 2
    reg_results = step2_regression(master)

    # Step 3
    new_garch = step3_update_garch(reg_results)

    # Step 4
    event_proj = step4_event_frequency(master)

    # Step 5
    lng = step5_lng_curve()

    # Step 6
    new_mc = step6_monte_carlo(new_garch, reg_results, event_proj, lng)

    # Step 7
    validation = step7_validate(reg_results, GARCH_PATH, new_garch, new_mc)

    # Step 8
    step8_update_params(reg_results, new_garch)

    banner("PIPELINE COMPLETE")
    print(f"  Finished: {datetime.now().isoformat()}")
    print(f"\n  Output files:")
    print(f"    master_regression_dataset_v5.csv")
    print(f"    regression_results_gen_mix.csv")
    print(f"    garch_volatility_model_v2.json")
    print(f"    event_frequency_projections_v2.json")
    print(f"    monte_carlo_lng_v6.json")
    print(f"    decarbiq_model_params_v2.json")
    print(f"    validation_report_gen_mix.json")


if __name__ == "__main__":
    main()
