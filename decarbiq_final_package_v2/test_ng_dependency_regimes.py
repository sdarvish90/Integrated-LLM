"""
NG dependency of ERCOT electricity under three regimes:
1. Normal operations (no critical events)
2. NG crisis events (gas price spikes)
3. Electricity crisis events (grid stress/scarcity)
"""
import pandas as pd
import numpy as np
from scipy import stats as sp_stats
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
master = pd.read_csv(os.path.join(BASE, "master_regression_dataset_v8.csv"))
master["date"] = pd.to_datetime(master["date"])
events_raw = pd.read_csv(os.path.join(BASE, "geopolitical_and_macro",
                                       "geopolitical_macro_disruptions.csv"))

# ─── Classify event months by TYPE ────────────────────────────────────────
ng_crisis_months = set()   # Gas price spike events
elec_crisis_months = set() # Electricity grid stress events

for _, row in events_raw.iterrows():
    impact = abs(float(row.get("gas_price_impact", 0)))
    if impact < 1.0:
        continue
    start = pd.to_datetime(row["date"])
    end_raw = row.get("end_date")
    end = pd.to_datetime(end_raw) if pd.notna(end_raw) and str(end_raw).strip() else start

    event_ym = set()
    for dt in pd.date_range(start.replace(day=1),
                            end.replace(day=1) + pd.DateOffset(months=0), freq="MS"):
        event_ym.add((dt.year, dt.month))

    name = row["event_name"]
    etype = row.get("event_type", "")
    subtype = row.get("sub_type", "")

    # Classify: NG crisis = events that primarily spike GAS prices
    # Electricity crisis = events that primarily stress the GRID
    if subtype in ("POLAR_VORTEX", "HURRICANE") or "Storm" in name or "Cold" in name:
        # These stress the electricity grid directly
        elec_crisis_months.update(event_ym)
        category = "ELEC_CRISIS"
    elif "Russia" in name or "Ukraine" in name or "LNG" in name or "Freeport" in name \
         or "Nord Stream" in name or "Sanc" in name or "Fukushima" in name:
        # These spike gas prices through supply disruption
        ng_crisis_months.update(event_ym)
        category = "NG_CRISIS"
    else:
        ng_crisis_months.update(event_ym)
        category = "NG_CRISIS"

    print(f"  {category:>12} | {name:<42} | impact=${impact:.1f} | {len(event_ym)} mo")

# Handle overlap: Uri is BOTH (extreme gas AND extreme electricity)
overlap = ng_crisis_months & elec_crisis_months
print(f"\nNG crisis months: {len(ng_crisis_months)}")
print(f"Electricity crisis months: {len(elec_crisis_months)}")
print(f"Overlap months: {len(overlap)}")

# ─── Tag master ──────────────────────────────────────────────────────────
master["_ng_crisis"] = master.apply(
    lambda r: (int(r["year"]), int(r["month"])) in ng_crisis_months, axis=1)
master["_elec_crisis"] = master.apply(
    lambda r: (int(r["year"]), int(r["month"])) in elec_crisis_months, axis=1)
master["_any_crisis"] = master["_ng_crisis"] | master["_elec_crisis"]
master["_normal"] = ~master["_any_crisis"]

# Also use scarcity intensity as continuous electricity stress indicator
has_scarcity = "ercot_scarcity_intensity" in master.columns
if has_scarcity:
    scarcity_p75 = master["ercot_scarcity_intensity"].quantile(0.75)
    master["_high_scarcity"] = master["ercot_scarcity_intensity"] > scarcity_p75
    print(f"\nScarcity intensity P75 threshold: {scarcity_p75:.2f}")

# ─── OLS helper ──────────────────────────────────────────────────────────
def ols_detailed(X, y, name="model"):
    X_np = X.values.astype(float)
    y_np = y.values.astype(float)
    mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
    X_np, y_np = X_np[mask], y_np[mask]
    n, k = X_np.shape
    if n < k + 2:
        return None
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
    return {"name": name, "n": n, "k": k, "r2": r2, "adj_r2": adj_r2, "coefs": coefs,
            "beta": beta, "y_mean": float(np.mean(y_np)), "y_std": float(np.std(y_np))}

# ─── ERCOT regression variables ──────────────────────────────────────────
ercot_dv = "ercot_wholesale_mwh"
ercot_full_vars = ["henry_hub_spot", "tx_hdd", "tx_cdd",
                   "tx_gas_gen_pct", "tx_wind_gen_pct", "tx_solar_gen_pct",
                   "ercot_scarcity_intensity"]
ercot_avail = [v for v in ercot_full_vars if v in master.columns and master[v].notna().sum() > 30]

# Prepare data
ercot_sub = master[[ercot_dv] + ercot_avail + ["_normal", "_ng_crisis", "_elec_crisis",
                                                  "_any_crisis", "year", "month"]].dropna().copy()
ercot_sub["intercept"] = 1.0
x_cols = ["intercept"] + ercot_avail

print("\n" + "=" * 90)
print("  ERCOT NG DEPENDENCY BY REGIME")
print("=" * 90)

# Counts per regime
n_normal = ercot_sub["_normal"].sum()
n_ng = ercot_sub["_ng_crisis"].sum()
n_elec = ercot_sub["_elec_crisis"].sum()
print(f"\n  Observations: Normal={int(n_normal)}, NG-crisis={int(n_ng)}, Elec-crisis={int(n_elec)}, Total={len(ercot_sub)}")

# ─── METHOD 1: Separate regressions per regime ──────────────────────────
print("\n" + "-" * 90)
print("  METHOD 1: Separate Regressions by Regime")
print("-" * 90)

# 1. Normal operations only
normal_data = ercot_sub[ercot_sub["_normal"]]
res_normal = ols_detailed(normal_data[x_cols], normal_data[ercot_dv], "Normal")

# 2. NG crisis months only
ng_data = ercot_sub[ercot_sub["_ng_crisis"]]
# Fewer controls for small sample
ng_simple_vars = ["henry_hub_spot", "tx_hdd", "tx_cdd"]
ng_simple_avail = [v for v in ng_simple_vars if v in ercot_avail]
ng_x = ["intercept"] + ng_simple_avail
res_ng = ols_detailed(ng_data[ng_x], ng_data[ercot_dv], "NG_crisis")

# 3. Electricity crisis months only
elec_data = ercot_sub[ercot_sub["_elec_crisis"]]
elec_simple_avail = [v for v in ng_simple_vars if v in ercot_avail]
elec_x = ["intercept"] + elec_simple_avail
res_elec = ols_detailed(elec_data[elec_x], elec_data[ercot_dv], "Elec_crisis")

# 4. All data (baseline)
res_all = ols_detailed(ercot_sub[x_cols], ercot_sub[ercot_dv], "All")

print(f"\n  {'Regime':<25} {'n':>5} {'Gas coef':>10} {'p-value':>10} {'R²':>8} {'Avg ERCOT':>12} {'Avg HH':>10}")
print(f"  {'-'*82}")

for label, res, data in [("All data", res_all, ercot_sub),
                          ("Normal (no events)", res_normal, normal_data),
                          ("NG crisis", res_ng, ng_data),
                          ("Electricity crisis", res_elec, elec_data)]:
    if res is None:
        print(f"  {label:<25} {'Too few obs':>5}")
        continue
    gas_coef = res["coefs"]["henry_hub_spot"]["coef"]
    gas_p = res["coefs"]["henry_hub_spot"]["p"]
    sig = "***" if gas_p < 0.01 else "**" if gas_p < 0.05 else "*" if gas_p < 0.1 else "n.s."
    avg_ercot = data[ercot_dv].mean()
    avg_hh = data["henry_hub_spot"].mean()
    print(f"  {label:<25} {res['n']:>5} {gas_coef:>10.2f} {gas_p:>9.4f}{sig:>4} {res['r2']:>7.4f} "
          f"${avg_ercot:>10.2f} ${avg_hh:>9.2f}")

# ─── METHOD 2: Interaction model (full sample) ──────────────────────────
print("\n" + "-" * 90)
print("  METHOD 2: Interaction Model (HH × regime dummies)")
print("-" * 90)

# Create interaction terms
ercot_sub["hh_x_ng_crisis"] = ercot_sub["henry_hub_spot"] * ercot_sub["_ng_crisis"].astype(float)
ercot_sub["hh_x_elec_crisis"] = ercot_sub["henry_hub_spot"] * ercot_sub["_elec_crisis"].astype(float)
ercot_sub["ng_crisis_dummy"] = ercot_sub["_ng_crisis"].astype(float)
ercot_sub["elec_crisis_dummy"] = ercot_sub["_elec_crisis"].astype(float)

interact_vars = ercot_avail + ["ng_crisis_dummy", "elec_crisis_dummy",
                                "hh_x_ng_crisis", "hh_x_elec_crisis"]
interact_x = ["intercept"] + interact_vars
res_interact = ols_detailed(ercot_sub[interact_x], ercot_sub[ercot_dv], "Interaction")

if res_interact:
    base_gas = res_interact["coefs"]["henry_hub_spot"]["coef"]
    ng_incr = res_interact["coefs"]["hh_x_ng_crisis"]["coef"]
    elec_incr = res_interact["coefs"]["hh_x_elec_crisis"]["coef"]
    ng_dummy = res_interact["coefs"]["ng_crisis_dummy"]["coef"]
    elec_dummy = res_interact["coefs"]["elec_crisis_dummy"]["coef"]

    print(f"\n  Base gas passthrough (normal):              ${base_gas:.2f}/MWh per $/MMBtu")
    print(f"  NG crisis increment (interaction):           ${ng_incr:+.2f}/MWh per $/MMBtu")
    print(f"  Elec crisis increment (interaction):         ${elec_incr:+.2f}/MWh per $/MMBtu")
    print(f"")
    print(f"  → Normal operations gas passthrough:         ${base_gas:.2f}")
    print(f"  → NG crisis gas passthrough:                 ${base_gas + ng_incr:.2f}  (base + NG interaction)")
    print(f"  → Elec crisis gas passthrough:               ${base_gas + elec_incr:.2f}  (base + Elec interaction)")
    print(f"")
    print(f"  NG crisis level shift (intercept dummy):     ${ng_dummy:+.2f}/MWh")
    print(f"  Elec crisis level shift (intercept dummy):   ${elec_dummy:+.2f}/MWh")
    print(f"  Model R²: {res_interact['r2']:.4f}")

    for v in interact_vars:
        c = res_interact["coefs"][v]
        sig = "***" if c["p"] < 0.01 else "**" if c["p"] < 0.05 else "*" if c["p"] < 0.1 else ""
        print(f"    {v:<35} {c['coef']:>10.4f}  (p={c['p']:.4f}) {sig}")

# ─── METHOD 3: Simple bivariate (gas → electricity) per regime ──────────
print("\n" + "-" * 90)
print("  METHOD 3: Simple Bivariate (HH → ERCOT) by Regime")
print("-" * 90)
print("  (No controls — raw gas dependency)")

for label, data in [("All data", ercot_sub),
                    ("Normal (no events)", normal_data),
                    ("NG crisis", ng_data),
                    ("Electricity crisis", elec_data)]:
    if len(data) < 5:
        print(f"\n  {label}: Too few observations ({len(data)})")
        continue
    x = data["henry_hub_spot"].values
    y = data[ercot_dv].values
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    slope, intercept, r, p, se = sp_stats.linregress(x, y)
    sig = "***" if p < 0.01 else "**" if p < 0.05 else "*" if p < 0.1 else "n.s."
    print(f"\n  {label}:")
    print(f"    n={len(x)}, slope=${slope:.2f}/MWh per $/MMBtu, "
          f"R²={r**2:.4f}, p={p:.4f} {sig}")
    print(f"    Avg HH=${np.mean(x):.2f}, Avg ERCOT=${np.mean(y):.2f}")
    print(f"    Interpretation: $1 gas increase → ${slope:.2f} electricity increase")

# ─── METHOD 4: Gas share of ERCOT price (heat rate implied) ─────────────
print("\n" + "-" * 90)
print("  METHOD 4: Implied Heat Rate & Gas Cost Share by Regime")
print("-" * 90)

for label, data in [("All data", ercot_sub),
                    ("Normal (no events)", normal_data),
                    ("NG crisis", ng_data),
                    ("Electricity crisis", elec_data)]:
    if len(data) < 5:
        continue
    avg_hh = data["henry_hub_spot"].mean()
    avg_ercot = data[ercot_dv].mean()
    avg_gas_pct = data["tx_gas_gen_pct"].mean() if "tx_gas_gen_pct" in data.columns else np.nan

    # Implied gas cost component = heat_rate * gas_price
    # Typical CCGT heat rate: 7.0-7.5 MMBtu/MWh
    # Typical peaker heat rate: 9.5-11.0 MMBtu/MWh
    ccgt_hr = 7.2
    peaker_hr = 10.0
    gas_cost_ccgt = avg_hh * ccgt_hr
    gas_cost_peaker = avg_hh * peaker_hr
    gas_share_ccgt = gas_cost_ccgt / avg_ercot * 100 if avg_ercot > 0 else 0
    gas_share_peaker = gas_cost_peaker / avg_ercot * 100 if avg_ercot > 0 else 0

    print(f"\n  {label}:")
    print(f"    Avg HH gas: ${avg_hh:.2f}/MMBtu, Avg ERCOT: ${avg_ercot:.2f}/MWh")
    print(f"    Gas gen share: {avg_gas_pct:.1f}%")
    print(f"    CCGT fuel cost (HR=7.2): ${gas_cost_ccgt:.2f}/MWh = {gas_share_ccgt:.0f}% of elec price")
    print(f"    Peaker fuel cost (HR=10): ${gas_cost_peaker:.2f}/MWh = {gas_share_peaker:.0f}% of elec price")

# ─── SUMMARY ─────────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  SUMMARY: ERCOT Natural Gas Dependency by Regime")
print("=" * 90)

print(f"""
  ┌─────────────────────────┬────────────┬────────────┬────────────┐
  │ Metric                  │ Normal     │ NG Crisis  │ Elec Crisis│
  ├─────────────────────────┼────────────┼────────────┼────────────┤""")

if res_normal and res_ng and res_elec:
    pt_n = res_normal["coefs"]["henry_hub_spot"]["coef"]
    pt_ng = res_ng["coefs"]["henry_hub_spot"]["coef"]
    pt_el = res_elec["coefs"]["henry_hub_spot"]["coef"]
    print(f"  │ Gas passthrough ($/MWh)  │ ${pt_n:>8.2f} │ ${pt_ng:>8.2f} │ ${pt_el:>8.2f} │")

    avg_n_hh = normal_data["henry_hub_spot"].mean()
    avg_ng_hh = ng_data["henry_hub_spot"].mean()
    avg_el_hh = elec_data["henry_hub_spot"].mean()
    avg_n_er = normal_data[ercot_dv].mean()
    avg_ng_er = ng_data[ercot_dv].mean()
    avg_el_er = elec_data[ercot_dv].mean()

    share_n = (pt_n * avg_n_hh / avg_n_er * 100) if avg_n_er > 0 else 0
    share_ng = (pt_ng * avg_ng_hh / avg_ng_er * 100) if avg_ng_er > 0 else 0
    share_el = (pt_el * avg_el_hh / avg_el_er * 100) if avg_el_er > 0 else 0

    print(f"  │ Avg HH gas ($/MMBtu)     │ ${avg_n_hh:>8.2f} │ ${avg_ng_hh:>8.2f} │ ${avg_el_hh:>8.2f} │")
    print(f"  │ Avg ERCOT price ($/MWh)   │ ${avg_n_er:>8.2f} │ ${avg_ng_er:>8.2f} │ ${avg_el_er:>8.2f} │")
    print(f"  │ Gas cost share (%)        │ {share_n:>9.0f}% │ {share_ng:>9.0f}% │ {share_el:>9.0f}% │")
    print(f"  │ n observations            │ {int(n_normal):>10} │ {int(n_ng):>10} │ {int(n_elec):>10} │")

print(f"  └─────────────────────────┴────────────┴────────────┴────────────┘")

if res_interact:
    print(f"\n  Interaction model confirms:")
    print(f"    Base (normal) passthrough:    ${base_gas:.2f}/MWh per $/MMBtu")
    print(f"    NG crisis total passthrough:  ${base_gas + ng_incr:.2f}/MWh per $/MMBtu ({ng_incr:+.2f} increment)")
    print(f"    Elec crisis total passthrough: ${base_gas + elec_incr:.2f}/MWh per $/MMBtu ({elec_incr:+.2f} increment)")
    print(f"    Elec crisis level shift:       ${elec_dummy:+.2f}/MWh (scarcity premium)")
