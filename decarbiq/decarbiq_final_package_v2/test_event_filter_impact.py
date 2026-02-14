"""
Standalone test: Event-filtering impact on HH regression and downstream outputs.
Compares current (unfiltered) vs event-filtered regression without modifying the pipeline.
"""
import pandas as pd
import numpy as np
from scipy import stats as sp_stats
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))

# ─── Load data ────────────────────────────────────────────────────────────
master = pd.read_csv(os.path.join(BASE, "master_regression_dataset_v8.csv"))
master["date"] = pd.to_datetime(master["date"])

with open(os.path.join(BASE, "geopolitical_and_macro", "geopolitical_macro_disruptions.csv")) as f:
    events_raw = pd.read_csv(f)

with open(os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v8.json")) as f:
    mc = json.load(f)

with open(os.path.join(BASE, "validation_expanded.json")) as f:
    val = json.load(f)

# ─── Identify event months ────────────────────────────────────────────────
# Use disruptions catalog: any month where gas_price_impact >= $1/MMBtu
event_months = set()
for _, row in events_raw.iterrows():
    impact = abs(float(row.get("gas_price_impact", 0)))
    if impact < 1.0:
        continue  # only filter major events
    start = pd.to_datetime(row["date"])
    end = pd.to_datetime(row.get("end_date", row["date"])) if pd.notna(row.get("end_date")) else start
    # Include the event month and surrounding months (impact persists)
    for dt in pd.date_range(start.replace(day=1), end.replace(day=1) + pd.DateOffset(months=1), freq="MS"):
        event_months.add((dt.year, dt.month))

print(f"Event months identified (|impact| >= $1/MMBtu): {len(event_months)}")
for ym in sorted(event_months):
    # Find event name
    names = []
    for _, row in events_raw.iterrows():
        if abs(float(row.get("gas_price_impact", 0))) < 1.0:
            continue
        start = pd.to_datetime(row["date"])
        end = pd.to_datetime(row.get("end_date", row["date"])) if pd.notna(row.get("end_date")) else start
        if start.year <= ym[0] <= end.year and start.month <= ym[1] <= end.month + 1:
            names.append(row["event_name"])
    print(f"  {ym[0]}-{ym[1]:02d}: {', '.join(names) if names else '?'}")

# ─── Create event filter mask ────────────────────────────────────────────
master["_event_month"] = master.apply(
    lambda r: (int(r["year"]), int(r["month"])) in event_months, axis=1)
n_event = master["_event_month"].sum()
n_normal = (~master["_event_month"]).sum()
print(f"\nMaster: {len(master)} total, {n_event} event months, {n_normal} normal months")

# ─── OLS helper ──────────────────────────────────────────────────────────
def ols(X, y, name="model"):
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
    try:
        ms_res = ss_res / (n - k) if n > k else 1
        XtX = X_np.T @ X_np
        cov = ms_res * np.linalg.pinv(XtX)
        se = np.sqrt(np.abs(np.diag(cov)))
    except:
        se = np.full(k, np.nan)
    t_stats = beta / se
    p_values = 2 * (1 - sp_stats.t.cdf(np.abs(t_stats), max(n - k, 1)))
    coefs = {}
    for i, c in enumerate(X.columns):
        coefs[c] = {"coef": float(beta[i]), "se": float(se[i]),
                     "t": float(t_stats[i]), "p": float(p_values[i])}
    return {"name": name, "n": n, "k": k, "r2": r2, "adj_r2": adj_r2,
            "coefs": coefs, "beta": beta, "X_cols": list(X.columns),
            "resid": resid, "fitted": y_hat, "X_np": X_np, "y_np": y_np}

# ─── Expanding window OOS ──────────────────────────────────────────────
def expanding_oos(df, y_col, x_cols, n_windows=5, event_filter_test=False):
    """Expanding window OOS R². If event_filter_test, exclude event months from test set."""
    sub = df[[y_col] + x_cols + ["_event_month"]].dropna().copy()
    sub["intercept"] = 1.0
    x_all = ["intercept"] + x_cols
    n = len(sub)
    test_size = 12
    train_start = n - n_windows * test_size - test_size
    if train_start < 30:
        train_start = 30

    windows = []
    for w in range(n_windows):
        train_end = train_start + (w + 1) * test_size
        test_start = train_end
        test_end = test_start + test_size
        if test_end > n:
            break

        train = sub.iloc[:train_end]
        test = sub.iloc[test_start:test_end]

        if event_filter_test:
            # Filter event months from BOTH train and test
            train = train[~train["_event_month"]]
            test = test[~test["_event_month"]]
            if len(test) < 3:
                continue

        X_train = train[x_all].values.astype(float)
        y_train = train[y_col].values.astype(float)
        X_test = test[x_all].values.astype(float)
        y_test = test[y_col].values.astype(float)

        beta = np.linalg.lstsq(X_train, y_train, rcond=None)[0]
        y_pred = X_test @ beta
        resid = y_test - y_pred
        ss_res = np.sum(resid**2)
        ss_tot = np.sum((y_test - np.mean(y_test))**2)
        r2_oos = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        rmse = np.sqrt(np.mean(resid**2))
        windows.append({"train_n": len(train), "test_n": len(test),
                        "r2_oos": round(r2_oos, 4), "rmse": round(rmse, 2)})

    avg_r2 = np.mean([w["r2_oos"] for w in windows]) if windows else None
    avg_rmse = np.mean([w["rmse"] for w in windows]) if windows else None
    return windows, avg_r2, avg_rmse

# ─── HH Regression Variables (matching step2b) ──────────────────────────
hh_dv = "henry_hub_spot"
hh_vars = ["electric_power_bcfd", "us_hdd", "us_cdd",
           "us_gdp_growth_pct", "us_industrial_prod_index", "us_data_center_twh",
           "lng_utilization_pct", "us_ng_storage_vs_5yr_pct",
           "itc_rate_pct", "ptc_rate_cents_kwh",
           "queue_backlog_gw", "cumulative_ferc_reforms"]

# Filter to available columns with enough data
available_vars = []
for v in hh_vars:
    if v in master.columns and master[v].notna().sum() > 30:
        available_vars.append(v)
print(f"\nHH variables available: {len(available_vars)}")
for v in available_vars:
    print(f"  {v}: {master[v].notna().sum()} obs")

# ─── Run comparison ─────────────────────────────────────────────────────
print("\n" + "="*80)
print("  COMPARISON: Unfiltered vs Event-Filtered HH Regression")
print("="*80)

# Prepare data
hh_sub = master[[hh_dv] + available_vars + ["_event_month", "year", "month"]].dropna().copy()
hh_sub["intercept"] = 1.0
x_cols = ["intercept"] + available_vars

# 1. Unfiltered regression (current)
print("\n--- UNFILTERED (current model) ---")
res_unfilt = ols(hh_sub[x_cols], hh_sub[hh_dv], "HH_unfiltered")
print(f"  n={res_unfilt['n']}, R²={res_unfilt['r2']:.4f}, Adj R²={res_unfilt['adj_r2']:.4f}")
for v, c in res_unfilt["coefs"].items():
    sig = "***" if c["p"] < 0.01 else "**" if c["p"] < 0.05 else "*" if c["p"] < 0.1 else ""
    print(f"    {v:<35} {c['coef']:>10.4f}  (p={c['p']:.4f}) {sig}")

# 2. Event-filtered regression
hh_filtered = hh_sub[~hh_sub["_event_month"]]
print(f"\n--- EVENT-FILTERED (removing {hh_sub['_event_month'].sum()} event months) ---")
res_filt = ols(hh_filtered[x_cols], hh_filtered[hh_dv], "HH_filtered")
print(f"  n={res_filt['n']}, R²={res_filt['r2']:.4f}, Adj R²={res_filt['adj_r2']:.4f}")
for v, c in res_filt["coefs"].items():
    sig = "***" if c["p"] < 0.01 else "**" if c["p"] < 0.05 else "*" if c["p"] < 0.1 else ""
    print(f"    {v:<35} {c['coef']:>10.4f}  (p={c['p']:.4f}) {sig}")

# 3. Coefficient comparison
print("\n--- COEFFICIENT COMPARISON ---")
print(f"  {'Variable':<35} {'Unfiltered':>12} {'Filtered':>12} {'Change':>10} {'%Change':>10}")
print(f"  {'-'*80}")
for v in x_cols:
    u = res_unfilt["coefs"][v]["coef"]
    f = res_filt["coefs"][v]["coef"]
    change = f - u
    pct = (change / abs(u) * 100) if abs(u) > 1e-10 else float("inf")
    print(f"  {v:<35} {u:>12.4f} {f:>12.4f} {change:>+10.4f} {pct:>+9.1f}%")

# 4. OOS R² comparison
print("\n--- OOS R² COMPARISON ---")

print("\n  Unfiltered train, unfiltered test (current):")
wins_uf, avg_r2_uf, avg_rmse_uf = expanding_oos(
    master, hh_dv, available_vars, n_windows=5, event_filter_test=False)
for w in wins_uf:
    print(f"    train={w['train_n']}, test={w['test_n']}, R²={w['r2_oos']:.4f}, RMSE=${w['rmse']:.2f}")
print(f"  Average OOS R² = {avg_r2_uf:.4f}, RMSE = ${avg_rmse_uf:.2f}")

print("\n  Event-filtered train, event-filtered test:")
wins_ef, avg_r2_ef, avg_rmse_ef = expanding_oos(
    master, hh_dv, available_vars, n_windows=5, event_filter_test=True)
for w in wins_ef:
    print(f"    train={w['train_n']}, test={w['test_n']}, R²={w['r2_oos']:.4f}, RMSE=${w['rmse']:.2f}")
print(f"  Average OOS R² = {avg_r2_ef:.4f}, RMSE = ${avg_rmse_ef:.2f}")

# 5. Gas forecast impact
# The MC uses regression coefficients to set mean-reversion target
# Simulate: what's the regression-predicted HH for 2026 under each model?
print("\n--- GAS FORECAST IMPACT ---")
# Use 2025 latest values as base
latest = master.dropna(subset=[hh_dv]).iloc[-1]
proj_2026 = {
    "electric_power_bcfd": 35.9,
    "us_hdd": float(master["us_hdd"].dropna().mean()),
    "us_cdd": float(master["us_cdd"].dropna().mean()),
    "us_gdp_growth_pct": 2.74,
    "us_industrial_prod_index": 104.3,
    "us_data_center_twh": 217.0,
    "lng_utilization_pct": 68.0,
    "us_ng_storage_vs_5yr_pct": 0.0,
    "itc_rate_pct": 30.0,
    "ptc_rate_cents_kwh": 2.75,
    "queue_backlog_gw": 2800,
    "cumulative_ferc_reforms": 13,
}

x_2026 = np.array([1.0] + [proj_2026.get(v, float(latest.get(v, 0))) for v in available_vars])
pred_unfilt = float(x_2026 @ res_unfilt["beta"])
pred_filt = float(x_2026 @ res_filt["beta"])
print(f"  Regression-predicted HH 2026 (unfiltered): ${pred_unfilt:.2f}")
print(f"  Regression-predicted HH 2026 (filtered):   ${pred_filt:.2f}")
print(f"  Difference: ${pred_filt - pred_unfilt:+.2f}")

# Current MC P50 for comparison
mc_p50_2026 = mc.get("annual_forecasts", {}).get("2026", {}).get("median", 0)
print(f"  Current MC P50 2026: ${mc_p50_2026:.2f}")
print(f"  Consensus range: $2.80 - $3.30 - $4.00")

# 6. ERCOT regression impact (separate test)
print("\n--- ERCOT REGRESSION (for reference) ---")
ercot_dv = "ercot_wholesale_mwh"
ercot_vars = ["henry_hub_spot", "tx_hdd", "tx_cdd",
              "tx_gas_gen_pct", "tx_wind_gen_pct", "tx_solar_gen_pct",
              "ercot_scarcity_intensity"]
ercot_avail = [v for v in ercot_vars if v in master.columns and master[v].notna().sum() > 30]

if ercot_avail:
    ercot_sub = master[[ercot_dv] + ercot_avail + ["_event_month"]].dropna().copy()
    ercot_sub["intercept"] = 1.0
    ex_cols = ["intercept"] + ercot_avail

    res_e_uf = ols(ercot_sub[ex_cols], ercot_sub[ercot_dv], "ERCOT_unfiltered")
    ercot_filtered = ercot_sub[~ercot_sub["_event_month"]]
    res_e_f = ols(ercot_filtered[ex_cols], ercot_filtered[ercot_dv], "ERCOT_filtered")

    print(f"  Unfiltered: n={res_e_uf['n']}, R²={res_e_uf['r2']:.4f}, gas passthrough=${res_e_uf['coefs']['henry_hub_spot']['coef']:.2f}")
    print(f"  Filtered:   n={res_e_f['n']}, R²={res_e_f['r2']:.4f}, gas passthrough=${res_e_f['coefs']['henry_hub_spot']['coef']:.2f}")
    print(f"  Passthrough change: ${res_e_f['coefs']['henry_hub_spot']['coef'] - res_e_uf['coefs']['henry_hub_spot']['coef']:+.2f}")

# 7. Summary
print("\n" + "="*80)
print("  SUMMARY: Impact of Event Filtering")
print("="*80)
print(f"\n  Events removed: {n_event} months ({n_event/len(master)*100:.1f}% of sample)")
print(f"\n  HH Regression:")
print(f"    R² change:      {res_unfilt['r2']:.4f} → {res_filt['r2']:.4f} ({res_filt['r2'] - res_unfilt['r2']:+.4f})")
print(f"    Adj R² change:  {res_unfilt['adj_r2']:.4f} → {res_filt['adj_r2']:.4f}")
print(f"    OOS R² change:  {avg_r2_uf:.4f} → {avg_r2_ef:.4f}")
print(f"    OOS RMSE:       ${avg_rmse_uf:.2f} → ${avg_rmse_ef:.2f}")
print(f"\n  Gas Forecast:")
print(f"    Predicted HH 2026: ${pred_unfilt:.2f} → ${pred_filt:.2f} ({pred_filt - pred_unfilt:+.2f})")
print(f"    MC P50 2026: ${mc_p50_2026:.2f} (consensus $2.80-$4.00)")
print(f"\n  Validation Checks:")
gas_checks = val.get("literature_comparison", {}).get("gas_forecasts", {})
for yr, chk in sorted(gas_checks.items()):
    print(f"    {yr}: P50=${chk['p50']:.2f}, status={chk['status']}")

if ercot_avail:
    pt_uf = res_e_uf["coefs"]["henry_hub_spot"]["coef"]
    pt_f = res_e_f["coefs"]["henry_hub_spot"]["coef"]
    print(f"\n  ERCOT Passthrough: ${pt_uf:.2f} → ${pt_f:.2f} (literature $7-9)")
