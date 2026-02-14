"""
Refined event-filter test v2: Only filter DISCRETE spike events (duration <= 2 months).
Keep sustained market shifts (Fukushima, Europe LNG surge, Freeport) in the regression
because these represent structural conditions the model should learn from.
"""
import pandas as pd
import numpy as np
from scipy import stats as sp_stats
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))

# ─── Load data ────────────────────────────────────────────────────────────
master = pd.read_csv(os.path.join(BASE, "master_regression_dataset_v8.csv"))
master["date"] = pd.to_datetime(master["date"])

events_raw = pd.read_csv(os.path.join(BASE, "geopolitical_and_macro",
                                       "geopolitical_macro_disruptions.csv"))

with open(os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v8.json")) as f:
    mc = json.load(f)

with open(os.path.join(BASE, "validation_expanded.json")) as f:
    val = json.load(f)

# ─── Classify events ────────────────────────────────────────────────────
print("=" * 80)
print("  EVENT CLASSIFICATION: Discrete Spikes vs Sustained Shifts")
print("=" * 80)

spike_months = set()
sustained_months = set()

for _, row in events_raw.iterrows():
    impact = abs(float(row.get("gas_price_impact", 0)))
    if impact < 1.0:
        continue

    start = pd.to_datetime(row["date"])
    end_raw = row.get("end_date")
    if pd.notna(end_raw) and str(end_raw).strip():
        end = pd.to_datetime(end_raw)
    else:
        end = start

    duration_days = (end - start).days
    duration_months = max(1, round(duration_days / 30))

    event_ym = set()
    for dt in pd.date_range(start.replace(day=1),
                            end.replace(day=1) + pd.DateOffset(months=0),
                            freq="MS"):
        event_ym.add((dt.year, dt.month))

    if duration_months <= 2:
        category = "SPIKE"
        spike_months.update(event_ym)
    else:
        category = "SUSTAINED"
        sustained_months.update(event_ym)

    print(f"  {category:>9} | {row['event_name']:<40} | {duration_months:>3} mo | "
          f"impact=${impact:.1f} | {len(event_ym)} months")

# Remove any overlap (spike classification takes precedence)
sustained_only = sustained_months - spike_months

print(f"\nDiscrete spike months: {len(spike_months)}")
print(f"Sustained shift months: {len(sustained_only)}")
print(f"Total event months (v1 approach): {len(spike_months | sustained_only)}")

# ─── Create filter masks ────────────────────────────────────────────────
master["_spike_month"] = master.apply(
    lambda r: (int(r["year"]), int(r["month"])) in spike_months, axis=1)
master["_any_event_month"] = master.apply(
    lambda r: (int(r["year"]), int(r["month"])) in (spike_months | sustained_only), axis=1)

n_spike = master["_spike_month"].sum()
n_any = master["_any_event_month"].sum()
print(f"\nMaster: {len(master)} total rows")
print(f"  Spike-only filter: removes {n_spike} months ({n_spike/len(master)*100:.1f}%)")
print(f"  All-event filter (v1): removes {n_any} months ({n_any/len(master)*100:.1f}%)")

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
        cov = ms_res * np.linalg.pinv(X_np.T @ X_np)
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
            "coefs": coefs, "beta": beta, "X_cols": list(X.columns)}

# ─── Expanding window OOS ──────────────────────────────────────────────
def expanding_oos(df, y_col, x_cols, n_windows=5, filter_col=None):
    sub = df[[y_col] + x_cols].copy()
    if filter_col:
        sub["_filter"] = df[filter_col].values
    else:
        sub["_filter"] = False
    sub = sub.dropna(subset=[y_col] + x_cols)
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

        if filter_col:
            train = train[~train["_filter"]]
            test = test[~test["_filter"]]
            if len(test) < 3 or len(train) < 20:
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

# ─── HH Variables (matching step2b) ──────────────────────────────────────
hh_dv = "henry_hub_spot"
hh_vars = ["electric_power_bcfd", "us_hdd", "us_cdd",
           "us_gdp_growth_pct", "us_industrial_prod_index", "us_data_center_twh",
           "lng_utilization_pct", "us_ng_storage_vs_5yr_pct",
           "itc_rate_pct", "ptc_rate_cents_kwh",
           "queue_backlog_gw", "cumulative_ferc_reforms"]

available_vars = [v for v in hh_vars if v in master.columns and master[v].notna().sum() > 30]

# ─── THREE-WAY COMPARISON ───────────────────────────────────────────────
print("\n" + "=" * 80)
print("  THREE-WAY COMPARISON: Unfiltered vs Spike-Only vs All-Event Filter")
print("=" * 80)

hh_sub = master[[hh_dv] + available_vars + ["_spike_month", "_any_event_month",
                                              "year", "month"]].dropna().copy()
hh_sub["intercept"] = 1.0
x_cols = ["intercept"] + available_vars

# 1. Unfiltered (current)
print("\n--- A. UNFILTERED (current model) ---")
res_A = ols(hh_sub[x_cols], hh_sub[hh_dv], "Unfiltered")
print(f"  n={res_A['n']}, R²={res_A['r2']:.4f}, Adj R²={res_A['adj_r2']:.4f}")
for v, c in res_A["coefs"].items():
    sig = "***" if c["p"] < 0.01 else "**" if c["p"] < 0.05 else "*" if c["p"] < 0.1 else ""
    print(f"    {v:<35} {c['coef']:>10.4f}  (p={c['p']:.4f}) {sig}")

# 2. Spike-only filter (REFINED)
hh_spike = hh_sub[~hh_sub["_spike_month"]]
n_removed_spike = hh_sub["_spike_month"].sum()
print(f"\n--- B. SPIKE-ONLY FILTER (removing {int(n_removed_spike)} discrete event months) ---")
res_B = ols(hh_spike[x_cols], hh_spike[hh_dv], "Spike-filtered")
print(f"  n={res_B['n']}, R²={res_B['r2']:.4f}, Adj R²={res_B['adj_r2']:.4f}")
for v, c in res_B["coefs"].items():
    sig = "***" if c["p"] < 0.01 else "**" if c["p"] < 0.05 else "*" if c["p"] < 0.1 else ""
    print(f"    {v:<35} {c['coef']:>10.4f}  (p={c['p']:.4f}) {sig}")

# 3. All-event filter (v1 - for comparison)
hh_all_filt = hh_sub[~hh_sub["_any_event_month"]]
n_removed_all = hh_sub["_any_event_month"].sum()
print(f"\n--- C. ALL-EVENT FILTER (v1, removing {int(n_removed_all)} months) ---")
res_C = ols(hh_all_filt[x_cols], hh_all_filt[hh_dv], "All-event-filtered")
print(f"  n={res_C['n']}, R²={res_C['r2']:.4f}, Adj R²={res_C['adj_r2']:.4f}")
for v, c in res_C["coefs"].items():
    sig = "***" if c["p"] < 0.01 else "**" if c["p"] < 0.05 else "*" if c["p"] < 0.1 else ""
    print(f"    {v:<35} {c['coef']:>10.4f}  (p={c['p']:.4f}) {sig}")

# ─── Coefficient stability check ─────────────────────────────────────────
print("\n--- COEFFICIENT COMPARISON (A vs B vs C) ---")
print(f"  {'Variable':<35} {'A:Unfilt':>10} {'B:Spike':>10} {'C:AllEvt':>10} {'A→B %':>8} {'A→C %':>8} {'B sign?':>8}")
print(f"  {'-'*95}")
for v in x_cols:
    a = res_A["coefs"][v]["coef"]
    b = res_B["coefs"][v]["coef"]
    c = res_C["coefs"][v]["coef"]
    pct_ab = ((b - a) / abs(a) * 100) if abs(a) > 1e-10 else float("inf")
    pct_ac = ((c - a) / abs(a) * 100) if abs(a) > 1e-10 else float("inf")
    # Check sign flip between A and B
    sign_ok = "OK" if (a * b > 0 or abs(a) < 1e-10 or abs(b) < 1e-10) else "FLIP!"
    print(f"  {v:<35} {a:>10.4f} {b:>10.4f} {c:>10.4f} {pct_ab:>+7.0f}% {pct_ac:>+7.0f}% {sign_ok:>8}")

# ─── OOS R² comparison ───────────────────────────────────────────────────
print("\n--- OOS R² COMPARISON ---")

print("\n  A. Unfiltered (current):")
wins_A, avg_r2_A, avg_rmse_A = expanding_oos(master, hh_dv, available_vars)
for w in wins_A:
    print(f"    train={w['train_n']}, test={w['test_n']}, R²={w['r2_oos']:.4f}, RMSE=${w['rmse']:.2f}")
print(f"  Average OOS R² = {avg_r2_A:.4f}, RMSE = ${avg_rmse_A:.2f}")

print("\n  B. Spike-only filter:")
wins_B, avg_r2_B, avg_rmse_B = expanding_oos(
    master, hh_dv, available_vars, filter_col="_spike_month")
for w in wins_B:
    print(f"    train={w['train_n']}, test={w['test_n']}, R²={w['r2_oos']:.4f}, RMSE=${w['rmse']:.2f}")
if avg_r2_B is not None:
    print(f"  Average OOS R² = {avg_r2_B:.4f}, RMSE = ${avg_rmse_B:.2f}")
else:
    print(f"  No valid windows (insufficient non-event observations)")

print("\n  C. All-event filter (v1):")
wins_C, avg_r2_C, avg_rmse_C = expanding_oos(
    master, hh_dv, available_vars, filter_col="_any_event_month")
for w in wins_C:
    print(f"    train={w['train_n']}, test={w['test_n']}, R²={w['r2_oos']:.4f}, RMSE=${w['rmse']:.2f}")
if avg_r2_C is not None:
    print(f"  Average OOS R² = {avg_r2_C:.4f}, RMSE = ${avg_rmse_C:.2f}")
else:
    print(f"  No valid windows (insufficient non-event observations)")

# ─── Gas forecast impact ─────────────────────────────────────────────────
print("\n--- GAS FORECAST IMPACT ---")
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

x_2026 = np.array([1.0] + [proj_2026.get(v, 0) for v in available_vars])
pred_A = float(x_2026 @ res_A["beta"])
pred_B = float(x_2026 @ res_B["beta"])
pred_C = float(x_2026 @ res_C["beta"])

mc_p50_2026 = mc.get("annual_forecasts", {}).get("2026", {}).get("median", 0)
print(f"  Predicted HH 2026 (A: unfiltered):   ${pred_A:.2f}")
print(f"  Predicted HH 2026 (B: spike-only):   ${pred_B:.2f}")
print(f"  Predicted HH 2026 (C: all-event):    ${pred_C:.2f}")
print(f"  Current MC P50 2026:                 ${mc_p50_2026:.2f}")
print(f"  Consensus range:                     $2.80 - $3.30 - $4.00")

# ─── ERCOT regression impact ─────────────────────────────────────────────
print("\n--- ERCOT REGRESSION (gas passthrough comparison) ---")
ercot_dv = "ercot_wholesale_mwh"
ercot_vars = ["henry_hub_spot", "tx_hdd", "tx_cdd",
              "tx_gas_gen_pct", "tx_wind_gen_pct", "tx_solar_gen_pct",
              "ercot_scarcity_intensity"]
ercot_avail = [v for v in ercot_vars if v in master.columns and master[v].notna().sum() > 30]

if ercot_avail:
    ercot_sub = master[[ercot_dv] + ercot_avail + ["_spike_month", "_any_event_month"]].dropna().copy()
    ercot_sub["intercept"] = 1.0
    ex_cols = ["intercept"] + ercot_avail

    res_eA = ols(ercot_sub[ex_cols], ercot_sub[ercot_dv], "ERCOT_unfilt")
    res_eB = ols(ercot_sub[~ercot_sub["_spike_month"]][ex_cols],
                 ercot_sub[~ercot_sub["_spike_month"]][ercot_dv], "ERCOT_spike")
    res_eC = ols(ercot_sub[~ercot_sub["_any_event_month"]][ex_cols],
                 ercot_sub[~ercot_sub["_any_event_month"]][ercot_dv], "ERCOT_all_evt")

    pt_A = res_eA["coefs"]["henry_hub_spot"]["coef"]
    pt_B = res_eB["coefs"]["henry_hub_spot"]["coef"]
    pt_C = res_eC["coefs"]["henry_hub_spot"]["coef"]

    print(f"  A (unfiltered): n={res_eA['n']}, R²={res_eA['r2']:.4f}, passthrough=${pt_A:.2f}")
    print(f"  B (spike-only): n={res_eB['n']}, R²={res_eB['r2']:.4f}, passthrough=${pt_B:.2f}")
    print(f"  C (all-event):  n={res_eC['n']}, R²={res_eC['r2']:.4f}, passthrough=${pt_C:.2f}")
    print(f"  Literature range: $7-9/MWh per $/MMBtu")

# ─── CAISO regression impact ────────────────────────────────────────────
print("\n--- CAISO REGRESSION (gas passthrough comparison) ---")
caiso_dv = "caiso_wholesale_mwh"
caiso_vars = ["henry_hub_spot", "ca_hdd", "ca_cdd",
              "ca_gas_gen_pct", "ca_solar_gen_pct", "ca_wind_gen_pct"]
caiso_avail = [v for v in caiso_vars if v in master.columns and master[v].notna().sum() > 30]

if caiso_avail:
    caiso_sub = master[[caiso_dv] + caiso_avail + ["_spike_month", "_any_event_month"]].dropna().copy()
    caiso_sub["intercept"] = 1.0
    cx_cols = ["intercept"] + caiso_avail

    res_cA = ols(caiso_sub[cx_cols], caiso_sub[caiso_dv], "CAISO_unfilt")
    res_cB = ols(caiso_sub[~caiso_sub["_spike_month"]][cx_cols],
                 caiso_sub[~caiso_sub["_spike_month"]][caiso_dv], "CAISO_spike")
    res_cC = ols(caiso_sub[~caiso_sub["_any_event_month"]][cx_cols],
                 caiso_sub[~caiso_sub["_any_event_month"]][caiso_dv], "CAISO_all_evt")

    cpt_A = res_cA["coefs"]["henry_hub_spot"]["coef"]
    cpt_B = res_cB["coefs"]["henry_hub_spot"]["coef"]
    cpt_C = res_cC["coefs"]["henry_hub_spot"]["coef"]

    print(f"  A (unfiltered): n={res_cA['n']}, R²={res_cA['r2']:.4f}, passthrough=${cpt_A:.2f}")
    print(f"  B (spike-only): n={res_cB['n']}, R²={res_cB['r2']:.4f}, passthrough=${cpt_B:.2f}")
    print(f"  C (all-event):  n={res_cC['n']}, R²={res_cC['r2']:.4f}, passthrough=${cpt_C:.2f}")
    print(f"  Literature range: $8-12/MWh per $/MMBtu")

# ─── SUMMARY ─────────────────────────────────────────────────────────────
print("\n" + "=" * 80)
print("  SUMMARY: Three-Way Comparison")
print("=" * 80)
print(f"\n  {'Metric':<35} {'A:Unfilt':>12} {'B:Spike':>12} {'C:AllEvt':>12}")
print(f"  {'-'*72}")
print(f"  {'HH sample size':<35} {res_A['n']:>12} {res_B['n']:>12} {res_C['n']:>12}")
print(f"  {'Months removed':<35} {'0':>12} {int(n_removed_spike):>12} {int(n_removed_all):>12}")
print(f"  {'HH In-sample R²':<35} {res_A['r2']:>12.4f} {res_B['r2']:>12.4f} {res_C['r2']:>12.4f}")
print(f"  {'HH Adj R²':<35} {res_A['adj_r2']:>12.4f} {res_B['adj_r2']:>12.4f} {res_C['adj_r2']:>12.4f}")
r2_B_str = f"{avg_r2_B:.4f}" if avg_r2_B is not None else "N/A"
r2_C_str = f"{avg_r2_C:.4f}" if avg_r2_C is not None else "N/A"
rmse_B_str = f"${avg_rmse_B:.2f}" if avg_rmse_B is not None else "N/A"
rmse_C_str = f"${avg_rmse_C:.2f}" if avg_rmse_C is not None else "N/A"
print(f"  {'HH OOS R²':<35} {avg_r2_A:>12.4f} {r2_B_str:>12} {r2_C_str:>12}")
print(f"  {'HH OOS RMSE':<35} {'${:.2f}'.format(avg_rmse_A):>12} {rmse_B_str:>12} {rmse_C_str:>12}")
print(f"  {'Predicted HH 2026':<35} {'${:.2f}'.format(pred_A):>12} {'${:.2f}'.format(pred_B):>12} {'${:.2f}'.format(pred_C):>12}")
if ercot_avail:
    print(f"  {'ERCOT passthrough (lit: $7-9)':<35} {'${:.2f}'.format(pt_A):>12} {'${:.2f}'.format(pt_B):>12} {'${:.2f}'.format(pt_C):>12}")
    print(f"  {'ERCOT R²':<35} {res_eA['r2']:>12.4f} {res_eB['r2']:>12.4f} {res_eC['r2']:>12.4f}")
    print(f"  {'ERCOT n':<35} {res_eA['n']:>12} {res_eB['n']:>12} {res_eC['n']:>12}")
if caiso_avail:
    print(f"  {'CAISO passthrough (lit: $8-12)':<35} {'${:.2f}'.format(cpt_A):>12} {'${:.2f}'.format(cpt_B):>12} {'${:.2f}'.format(cpt_C):>12}")
    print(f"  {'CAISO R²':<35} {res_cA['r2']:>12.4f} {res_cB['r2']:>12.4f} {res_cC['r2']:>12.4f}")
    print(f"  {'CAISO n':<35} {res_cA['n']:>12} {res_cB['n']:>12} {res_cC['n']:>12}")

# Count sign flips A→B
n_flips_B = sum(1 for v in available_vars
                if res_A["coefs"][v]["coef"] * res_B["coefs"][v]["coef"] < 0
                and abs(res_A["coefs"][v]["coef"]) > 1e-10)
n_flips_C = sum(1 for v in available_vars
                if res_A["coefs"][v]["coef"] * res_C["coefs"][v]["coef"] < 0
                and abs(res_A["coefs"][v]["coef"]) > 1e-10)
print(f"\n  Coefficient sign flips (A→B): {n_flips_B}")
print(f"  Coefficient sign flips (A→C): {n_flips_C}")

print(f"\n  RECOMMENDATION:")
if n_flips_B <= 1 and res_B['n'] >= 45:
    print(f"  → B (spike-only) is STABLE: preserves sample, minimal sign flips")
    print(f"    Implement spike-only filter in pipeline")
elif n_flips_B <= 2:
    print(f"  → B (spike-only) is MARGINAL: {n_flips_B} sign flips, review individual coefficients")
else:
    print(f"  → B (spike-only) has {n_flips_B} sign flips — may still remove too many observations")
    print(f"    Consider raising impact threshold or reducing regressors")
