#!/usr/bin/env python3
"""
DecarbIQ Policy & Regulation Update Pipeline
=============================================
Integrates new policy/regulation data into the full model chain:
  - policy_federal_tax_credits.csv (ITC/PTC history 1978-2025, IRA credits)
  - policy_federal_legislation.csv (PURPA through IRA/HR1)
  - policy_state_rps_ces.csv (CA, TX, NY, etc. RPS/CES targets)
  - policy_interconnection_queue.csv (FERC orders, queue backlog data)
  - policy_behind_the_meter.csv (net metering, storage mandates, FERC 2222)
  - policy_master_database.json (consolidated metadata)

Steps:
1. Build annual policy indicator time-series → merge into master dataset v6 → v7
2. Re-run regression with policy variables (ITC/PTC rates, queue backlog, RPS count)
3. Update GARCH volatility model with policy-adjusted residuals
4. Refresh event frequency projections with policy-risk events
5. Verify LNG capacity curve against policy outlook
6. Re-run Monte Carlo V8 with policy variables
7. Validate: compare V7 vs V8 forecasts
8. Update model params

Created: 2026-02-10
"""

import os
import glob
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
POL = os.path.join(BASE, "policy_and_regulation")

# Inputs — policy files
POL_TAX = os.path.join(POL, "policy_federal_tax_credits.csv")
POL_LEG = os.path.join(POL, "policy_federal_legislation.csv")
POL_RPS = os.path.join(POL, "policy_state_rps_ces.csv")
POL_QUEUE = os.path.join(POL, "policy_interconnection_queue.csv")
POL_BTM = os.path.join(POL, "policy_behind_the_meter.csv")
POL_DB = os.path.join(POL, "policy_master_database.json")
CARB_PRICES = os.path.join(POL, "CARB_auction_prices.csv")
RENEWABLE_PEN = os.path.join(BASE, "renewable_penetration")

# Inputs — current model files (read from v7/v4/v8 if re-running with updated policy data)
MASTER_V7_EXISTING = os.path.join(BASE, "master_regression_dataset_v7.csv")
GARCH_V3 = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v3.json")
GARCH_V4_EXISTING = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v4.json")
EVENT_FREQ_V3 = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections_v3.json")
EVENT_FREQ_V4_EXISTING = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections_v4.json")
LNG_CURVE = os.path.join(BASE, "geopolitical_and_macro", "lng_capacity_curve_model.json")
MC_V7 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v7.json")
MC_V8_EXISTING = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v8.json")
MODEL_PARAMS_V3 = os.path.join(BASE, "decarbiq_model_params_v3.json")
MODEL_PARAMS_V4_EXISTING = os.path.join(BASE, "decarbiq_model_params_v4.json")

# Outputs
MASTER_V7 = os.path.join(BASE, "master_regression_dataset_v7.csv")
REG_RESULTS = os.path.join(BASE, "regression_results_policy.csv")
GARCH_V4 = os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v4.json")
EVENT_FREQ_V4 = os.path.join(BASE, "geopolitical_and_macro", "event_frequency_projections_v4.json")
MC_V8 = os.path.join(BASE, "geopolitical_and_macro", "monte_carlo_lng_v8.json")
MODEL_PARAMS_V4 = os.path.join(BASE, "decarbiq_model_params_v4.json")
VALIDATION = os.path.join(BASE, "validation_report_policy.json")
REL_GRAPH_JSON = os.path.join(BASE, "decarbiq_relationship_graph.json")
CHORD_PNG = os.path.join(BASE, "decarbiq_relationship_chord.png")
CHORD_SVG = os.path.join(BASE, "decarbiq_relationship_chord.svg")
PROJECTION_JSON = os.path.join(BASE, "decarbiq_projection_map.json")
PROJECTION_PNG = os.path.join(BASE, "decarbiq_projection_map.png")
SENSITIVITY_JSON = os.path.join(BASE, "decarbiq_sensitivity_analysis.json")
SENSITIVITY_PNG = os.path.join(BASE, "decarbiq_sensitivity_analysis.png")

# Step 13 — Enhanced regression inputs
HDD_CDD_CSV = os.path.join(BASE, "weather_climate", "heating_cooling_degree_days.csv")
HDD_MONTHLY_CSV = os.path.join(BASE, "weather_climate", "HDD data.csv")
CDD_MONTHLY_CSV = os.path.join(BASE, "weather_climate", "CDD data.csv")
RESERVE_MARGINS_CSV = os.path.join(BASE, "market_structure", "reserve_margins_by_region.csv")
CONGESTION_CSV = os.path.join(BASE, "grid_infrastructure", "transmission_congestion_costs.csv")
CURTAILMENT_CSV = os.path.join(BASE, "renewable_penetration", "curtailment_trends_drivers.csv")
ERCOT_RTM_DAILY = os.path.join(BASE, "fuel_costs_and_supply", "ercot_rtm_daily_1990_2025.csv")
SCARCITY_CSV = os.path.join(BASE, "market_structure", "scarcity_pricing_mechanisms.csv")
NEGATIVE_HOURS_CSV = os.path.join(BASE, "generation_mix_and_capacity", "negative_pricing_hours.csv")
BATTERY_COST_CSV = os.path.join(BASE, "tech_shift", "battery_cost_decline.csv")
EV_CHARGING_CSV = os.path.join(BASE, "tech_shift", "ev_charging_grid_impact.csv")
VARIABILITY_CSV = os.path.join(BASE, "renewable_penetration", "renewable_variability_monthly.csv")
ANCILLARY_CSV = os.path.join(BASE, "market_structure", "ancillary_services_pricing.csv")
CAISO_DAM_HOURLY = os.path.join(BASE, "generation_mix_and_capacity", "caiso_dam_hourly_2023_2025.csv")
CAISO_CURTAIL_DIR = os.path.join(BASE, "renewable_penetration")
CAISO_CURTAIL_CSV = os.path.join(BASE, "renewable_penetration", "caiso_monthly_curtailment_2014_2025.csv")
BATTERY_CAPACITY_CSV = os.path.join(BASE, "renewable_penetration", "battery_storage_capacity_timeline.csv")
GAS_STORAGE_CSV = os.path.join(BASE, "generation_mix_and_capacity", "gas_storage_weekly.csv")
CAISO_DAILY_DIR = os.path.join(BASE, "generation_mix_and_capacity", "caiso_daily_data")

# Step 13 — Enhanced regression outputs
MASTER_V8 = os.path.join(BASE, "master_regression_dataset_v8.csv")
REG_DIAGNOSTICS = os.path.join(BASE, "regression_diagnostics_v2.json")
DATA_QUALITY = os.path.join(BASE, "data_quality_report.json")
DIAG_PNG = os.path.join(BASE, "decarbiq_model_diagnostics.png")
VALIDATION_EXPANDED = os.path.join(BASE, "validation_expanded.json")
LITERATURE_BENCHMARKS = os.path.join(BASE, "literature_benchmarks.json")
URI_EVENT_DATA = os.path.join(BASE, "ercot_uri_event_feb2021.json")


def banner(msg):
    print(f"\n{'='*80}")
    print(f"  {msg}")
    print(f"{'='*80}")


# ============================================================================
# STEP 1: BUILD POLICY TIME-SERIES & MERGE INTO MASTER
# ============================================================================
def step1_merge():
    banner("STEP 1: Build policy indicator time-series & merge into master dataset")

    # Load master — if v7 exists (re-run), strip old policy columns first
    POLICY_COLS = [
        "itc_rate_pct", "ptc_rate_cents_kwh", "itc_active", "ptc_active",
        "ira_active", "n_ira_credits", "queue_backlog_gw", "ercot_large_load_queue_gw",
        "ferc_reform_year", "cumulative_ferc_reforms", "n_rps_states", "ca_rps_target_pct",
        "ca_cap_trade_active", "ca_allowance_price_per_ton", "ca_allowance_spread",
        "tx_crez_active", "tx_crez_transmission_gw",
        "ca_nem_compensation_level",
        "ca_storage_mandate", "der_market_access", "ca_solar_mandate",
        "major_energy_legislation", "cumulative_energy_laws", "ira_spending_bn",
        "arra_active", "policy_intensity_index",
    ]

    if os.path.exists(MASTER_V7_EXISTING):
        master = pd.read_csv(MASTER_V7_EXISTING, parse_dates=["date"])
        # Strip old policy columns for re-merge
        drop_cols = [c for c in POLICY_COLS if c in master.columns]
        if drop_cols:
            master = master.drop(columns=drop_cols)
            print(f"  Re-run: loaded v7 ({len(master)} rows), stripped {len(drop_cols)} old policy columns → {len(master.columns)} columns")
        else:
            print(f"  Master v7: {len(master)} rows, {len(master.columns)} columns")
    else:
        master = pd.read_csv(os.path.join(BASE, "master_regression_dataset_v6.csv"), parse_dates=["date"])
        print(f"  Master v6: {len(master)} rows, {len(master.columns)} columns")

    # --- Load ACTUAL monthly wind/solar from renewable_penetration/ ---
    # Replaces stale annual values in master with real monthly regional data
    monthly_files = sorted(glob.glob(os.path.join(RENEWABLE_PEN, "tx_ca_wind_solar_monthly_*.csv")))
    if monthly_files:
        monthly_dfs = [pd.read_csv(f) for f in monthly_files]
        monthly_all = pd.concat(monthly_dfs, ignore_index=True)
        monthly_all = monthly_all[monthly_all["total_gen_gwh"] > 0]

        tx_monthly = monthly_all[monthly_all["state"] == "TX"][
            ["year", "month", "wind_share_pct", "solar_share_pct"]
        ].rename(columns={"wind_share_pct": "tx_wind_gen_pct", "solar_share_pct": "tx_solar_gen_pct"})
        ca_monthly = monthly_all[monthly_all["state"] == "CA"][
            ["year", "month", "wind_share_pct", "solar_share_pct"]
        ].rename(columns={"wind_share_pct": "ca_wind_gen_pct", "solar_share_pct": "ca_solar_gen_pct"})

        # Drop old annual columns and merge actual monthly
        for col in ["tx_wind_gen_pct", "tx_solar_gen_pct", "ca_wind_gen_pct", "ca_solar_gen_pct"]:
            if col in master.columns:
                master = master.drop(columns=[col])
        master = master.merge(tx_monthly, on=["year", "month"], how="left")
        master = master.merge(ca_monthly, on=["year", "month"], how="left")
        for col in ["tx_wind_gen_pct", "tx_solar_gen_pct", "ca_wind_gen_pct", "ca_solar_gen_pct"]:
            master[col] = master[col].fillna(0.0)

        print(f"  Loaded ACTUAL monthly wind/solar: {len(monthly_files)} files, "
              f"TX wind range {master['tx_wind_gen_pct'].min():.1f}-{master['tx_wind_gen_pct'].max():.1f}%")

    # --- Build annual policy indicators for 1990-2025 ---
    years = list(range(1990, 2026))
    pol_df = pd.DataFrame({"year": years})

    # ---- 1a. ITC Rate by Year ----
    tax_credits = pd.read_csv(POL_TAX)
    itc_rows = tax_credits[tax_credits["policy"].str.contains("ITC", na=False) &
                           ~tax_credits["policy"].str.contains("PTC|45|48|30D|25D|Bonus|Community|Low", na=False)]
    ptc_rows = tax_credits[tax_credits["policy"].str.contains("PTC", na=False) &
                           ~tax_credits["policy"].str.contains("ITC", na=False)]

    # Build ITC rate time-series
    itc_by_year = {}
    for yr in years:
        rate = 0
        for _, row in itc_rows.iterrows():
            enacted = int(row["year"])
            exp_raw = str(row["expiration"]).strip()
            try:
                exp = int(exp_raw)
            except ValueError:
                exp = 2099 if exp_raw == "permanent" else 2032
            if enacted <= yr <= exp:
                val_str = str(row["value"]).replace("%", "").replace("+bonuses", "").strip()
                try:
                    rate = max(rate, float(val_str))
                except ValueError:
                    pass
        itc_by_year[yr] = rate

    pol_df["itc_rate_pct"] = pol_df["year"].map(itc_by_year)

    # Build PTC rate time-series (cents/kWh)
    ptc_by_year = {}
    for yr in years:
        rate = 0.0
        for _, row in ptc_rows.iterrows():
            enacted = int(row["year"])
            exp_raw = str(row["expiration"]).strip()
            try:
                exp = int(exp_raw)
            except ValueError:
                exp = 2032
            if enacted <= yr <= exp:
                val_str = str(row["value"]).replace("¢/kWh", "").replace("+bonuses", "").replace("%", "").strip()
                try:
                    rate = max(rate, float(val_str))
                except ValueError:
                    pass
        ptc_by_year[yr] = rate

    pol_df["ptc_rate_cents_kwh"] = pol_df["year"].map(ptc_by_year)

    # Binary: ITC/PTC active
    pol_df["itc_active"] = (pol_df["itc_rate_pct"] > 0).astype(int)
    pol_df["ptc_active"] = (pol_df["ptc_rate_cents_kwh"] > 0).astype(int)

    # IRA active (2022+)
    pol_df["ira_active"] = (pol_df["year"] >= 2022).astype(int)

    # Count active IRA credits by year (45V, 45Q, 45X, 48C, 30D, 25D, bonuses)
    ira_credits = tax_credits[tax_credits["year"] == 2022]
    n_ira_credits = len(ira_credits)
    pol_df["n_ira_credits"] = pol_df["year"].apply(lambda y: n_ira_credits if y >= 2022 else 0)

    print(f"  Tax credits: ITC rate range {min(itc_by_year.values())}-{max(itc_by_year.values())}%, "
          f"PTC range {min(ptc_by_year.values())}-{max(ptc_by_year.values())}¢/kWh")

    # ---- 1b. Interconnection Queue Backlog ----
    queue_df = pd.read_csv(POL_QUEUE)
    queue_data = queue_df[queue_df["status"] == "data"]

    # Extract numeric backlog values
    queue_backlog = {}
    for _, row in queue_data.iterrows():
        yr = int(row["year"])
        desc = str(row["description"])
        if "GW" in desc and "queue" in desc.lower():
            # Parse "~300 GW", "~2,600 GW", "230+ GW"
            for token in desc.replace(",", "").replace("~", "").replace("+", "").split():
                try:
                    val = float(token)
                    if val > 50:  # must be GW-scale
                        if "large load" in row["policy"].lower():
                            pass  # handled separately
                        else:
                            queue_backlog[yr] = val
                    break
                except ValueError:
                    continue

    # Interpolate queue backlog for missing years
    queue_series = {}
    sorted_yrs = sorted(queue_backlog.keys())
    for yr in years:
        if yr in queue_backlog:
            queue_series[yr] = queue_backlog[yr]
        elif yr < sorted_yrs[0]:
            queue_series[yr] = 0.0
        elif yr > sorted_yrs[-1]:
            queue_series[yr] = queue_backlog[sorted_yrs[-1]]
        else:
            # Linear interpolation
            lo = max(y for y in sorted_yrs if y <= yr)
            hi = min(y for y in sorted_yrs if y >= yr)
            if lo == hi:
                queue_series[yr] = queue_backlog[lo]
            else:
                frac = (yr - lo) / (hi - lo)
                queue_series[yr] = queue_backlog[lo] + frac * (queue_backlog[hi] - queue_backlog[lo])

    pol_df["queue_backlog_gw"] = pol_df["year"].map(queue_series)

    # ERCOT large load queue (only 2025)
    ercot_large = queue_data[queue_data["description"].str.contains("large load", case=False, na=False)]
    ercot_queue_gw = {}
    for _, row in ercot_large.iterrows():
        yr = int(row["year"])
        for token in str(row["description"]).replace(",", "").replace("+", "").split():
            try:
                val = float(token)
                if val > 50:
                    ercot_queue_gw[yr] = val
                break
            except ValueError:
                continue
    # Fill: 0 before 2024, then grows
    for yr in years:
        if yr not in ercot_queue_gw:
            ercot_queue_gw[yr] = 0.0 if yr < 2024 else ercot_queue_gw.get(2025, 230.0)
    pol_df["ercot_large_load_queue_gw"] = pol_df["year"].map(ercot_queue_gw)

    # FERC major reforms (binary by year)
    ferc_orders = queue_df[queue_df["status"] == "enacted"]
    ferc_reform_years = set(int(row["year"]) for _, row in ferc_orders.iterrows())
    pol_df["ferc_reform_year"] = pol_df["year"].apply(lambda y: 1 if y in ferc_reform_years else 0)
    # Cumulative FERC reforms
    pol_df["cumulative_ferc_reforms"] = pol_df["year"].apply(
        lambda y: sum(1 for fy in ferc_reform_years if fy <= y))

    print(f"  Queue backlog: {queue_series.get(2010, 0):.0f} GW (2010) → {queue_series.get(2024, 0):.0f} GW (2024)")
    print(f"  FERC reforms: {len(ferc_reform_years)} orders ({sorted(ferc_reform_years)})")

    # ---- 1c. State RPS Count & CA RPS Target ----
    rps_df = pd.read_csv(POL_RPS)

    # Count states with active RPS by year
    rps_count_by_year = {}
    ca_rps_target = {}
    for yr in years:
        active_states = set()
        for _, row in rps_df.iterrows():
            enacted = int(row["year"])
            if enacted <= yr and "RPS" in str(row["policy"]):
                active_states.add(row["state"])
        rps_count_by_year[yr] = len(active_states)

        # CA RPS target (progressive ratchet)
        ca_rows = rps_df[(rps_df["state"] == "CA") & (rps_df["policy"].str.contains("RPS", na=False))]
        ca_target = 0
        for _, row in ca_rows.iterrows():
            if int(row["year"]) <= yr:
                tgt = str(row["target"]).replace("%", "").strip()
                try:
                    ca_target = max(ca_target, float(tgt))
                except ValueError:
                    pass
        ca_rps_target[yr] = ca_target

    pol_df["n_rps_states"] = pol_df["year"].map(rps_count_by_year)
    pol_df["ca_rps_target_pct"] = pol_df["year"].map(ca_rps_target)

    # CA cap-and-trade: REPLACED binary dummy with continuous allowance price
    # Binary ca_cap_trade_active was absorbing the shale gas price collapse + solar ramp
    # (coefficient was -343 $/MWh, wrong sign). Literature shows cap-and-trade should
    # RAISE prices by ~$0.41-0.59/MWh per $1/ton CO2 (Woo et al. 2018).
    #
    # Source: CARB quarterly auction settlement prices (current vintage)
    # File: policy_and_regulation/CARB_auction_prices.csv
    # https://ww2.arb.ca.gov/our-work/programs/cap-and-trade-program/auction-information
    if os.path.exists(CARB_PRICES):
        carb_raw = pd.read_csv(CARB_PRICES, header=None).dropna(how="all")
        # Column 0: auction name (e.g. "November 2025 Joint Auction #45")
        # Column 3: current vintage settlement price ($/ton CO2)
        carb_raw.columns = [
            "auction_name", "curr_offered", "curr_sold", "curr_settlement_price",
            "adv_offered", "adv_sold", "adv_settlement_price",
        ]
        # Extract year from auction name
        carb_raw["year"] = carb_raw["auction_name"].str.extract(r"(\d{4})").astype(int)
        # Annual average of current vintage settlement price
        ca_allowance_by_year = (
            carb_raw.groupby("year")["curr_settlement_price"].mean().to_dict()
        )
        # Pre-program years: $0
        for yr in range(pol_df["year"].min(), 2012 + 1):
            ca_allowance_by_year.setdefault(yr, 0.0)
        print(f"  Loaded CARB auction prices from CSV: {len(carb_raw)} auctions, "
              f"years {carb_raw['year'].min()}-{carb_raw['year'].max()}")
    else:
        print(f"  WARNING: {CARB_PRICES} not found — ca_allowance_price_per_ton will be NaN")
        ca_allowance_by_year = {}
    pol_df["ca_allowance_price_per_ton"] = pol_df["year"].map(ca_allowance_by_year).fillna(0.0)

    # CARB floor-price spread: actual settlement minus CARB auction floor price
    # CARB floor price: $10/ton in 2012, increases 5%+CPI (~7.5%/yr) annually
    # Floor prices from CARB auction notices (public data):
    # 2012: $10.00, 2013: $10.71, 2014: $11.34, 2015: $12.10, 2016: $12.73,
    # 2017: $13.57, 2018: $14.53, 2019: $15.62, 2020: $16.68, 2021: $17.71,
    # 2022: $19.70, 2023: $22.21, 2024: $23.30, 2025: $24.85
    carb_floor_by_year = {
        2012: 10.00, 2013: 10.71, 2014: 11.34, 2015: 12.10, 2016: 12.73,
        2017: 13.57, 2018: 14.53, 2019: 15.62, 2020: 16.68, 2021: 17.71,
        2022: 19.70, 2023: 22.21, 2024: 23.30, 2025: 24.85,
    }
    ca_allowance_spread = {}
    for yr in years:
        actual = ca_allowance_by_year.get(yr, 0.0)
        floor = carb_floor_by_year.get(yr, 0.0)
        ca_allowance_spread[yr] = actual - floor if actual > 0 and floor > 0 else 0.0
    pol_df["ca_allowance_spread"] = pol_df["year"].map(ca_allowance_spread)
    print(f"  CARB allowance spread: ${ca_allowance_spread.get(2015, 0):.2f} (2015) → "
          f"${ca_allowance_spread.get(2024, 0):.2f} (2024)")

    # Keep binary dummy for backward compatibility but DO NOT use in regressions
    pol_df["ca_cap_trade_active"] = (pol_df["year"] >= 2013).astype(int)

    # TX CREZ: REPLACED binary dummy with continuous transmission capacity (GW online)
    # Binary tx_crez_active was absorbing concurrent time trends.
    #
    # DATA PROVENANCE: Approximate GW online by year from ERCOT Long-Term System
    # Assessment reports. Phase schedule is approximate — should be verified against
    # ERCOT transmission planning documents for production use.
    tx_crez_gw_by_year = {
        1990: 0.0, 1991: 0.0, 1992: 0.0, 1993: 0.0, 1994: 0.0,
        1995: 0.0, 1996: 0.0, 1997: 0.0, 1998: 0.0, 1999: 0.0,
        2000: 0.0, 2001: 0.0, 2002: 0.0, 2003: 0.0, 2004: 0.0,
        2005: 0.0, 2006: 0.5, 2007: 1.0, 2008: 2.5, 2009: 4.5,
        2010: 7.0, 2011: 11.0, 2012: 14.0, 2013: 17.0, 2014: 18.5,
        # Post-CREZ: capacity stays at final level
        2015: 18.5, 2016: 18.5, 2017: 18.5, 2018: 18.5, 2019: 18.5,
        2020: 18.5, 2021: 18.5, 2022: 18.5, 2023: 18.5, 2024: 18.5,
        2025: 18.5,
    }
    pol_df["tx_crez_transmission_gw"] = pol_df["year"].map(tx_crez_gw_by_year)

    # Keep binary dummy for backward compatibility but DO NOT use in regressions
    pol_df["tx_crez_active"] = pol_df["year"].apply(lambda y: 1 if 2005 <= y <= 2014 else 0)

    print(f"  RPS states: {rps_count_by_year.get(2000, 0)} (2000) → {rps_count_by_year.get(2024, 0)} (2024)")
    print(f"  CA RPS target: {ca_rps_target.get(2002, 0)}% (2002) → {ca_rps_target.get(2024, 0)}% (2024)")

    # ---- 1d. Behind-the-Meter / DER Indicators ----
    btm_df = pd.read_csv(POL_BTM)

    # CA NEM compensation level (ordinal: 0=none, 3=retail, 2=TOU, 1=reduced)
    ca_nem_level = {}
    for yr in years:
        level = 0
        if yr >= 1996:
            level = 3  # NEM 1.0 = full retail
        if yr >= 2016:
            level = 2  # NEM 2.0 = TOU retail
        if yr >= 2023:
            level = 1  # NEM 3.0 = reduced (~5-8¢)
        ca_nem_level[yr] = level
    pol_df["ca_nem_compensation_level"] = pol_df["year"].map(ca_nem_level)

    # Storage mandate active
    pol_df["ca_storage_mandate"] = pol_df["year"].apply(lambda y: 1 if y >= 2013 else 0)

    # DER market access (FERC 2222 enacted 2020)
    pol_df["der_market_access"] = pol_df["year"].apply(lambda y: 1 if y >= 2020 else 0)

    # Solar home mandate (CA Title 24, effective 2020)
    pol_df["ca_solar_mandate"] = pol_df["year"].apply(lambda y: 1 if y >= 2020 else 0)

    print(f"  CA NEM level: retail(1996) → TOU(2016) → reduced(2023)")
    print(f"  Storage mandate: 2013+, DER access: 2020+, Solar mandate: 2020+")

    # ---- 1e. Federal Legislation Indicators ----
    leg_df = pd.read_csv(POL_LEG)
    leg_years = set(int(row["year"]) for _, row in leg_df.iterrows())
    pol_df["major_energy_legislation"] = pol_df["year"].apply(lambda y: 1 if y in leg_years else 0)
    pol_df["cumulative_energy_laws"] = pol_df["year"].apply(
        lambda y: sum(1 for ly in leg_years if ly <= y))

    # IRA-specific impact (largest climate investment)
    pol_df["ira_spending_bn"] = pol_df["year"].apply(lambda y: 369.0 if y >= 2022 else 0.0)

    # ARRA impact (2009-2012)
    pol_df["arra_active"] = pol_df["year"].apply(lambda y: 1 if 2009 <= y <= 2012 else 0)

    print(f"  Federal legislation years: {sorted(leg_years)}")
    print(f"  Cumulative laws: {pol_df['cumulative_energy_laws'].iloc[-1]} by 2025")

    # ---- 1f. Composite Policy Intensity Index ----
    # Normalized composite: uses only continuous measures, no binary dummies.
    # Binary dummies (ira_active, ptc_active, ca_cap_trade_active) removed
    # because they absorb time trends rather than causal policy effects.
    pol_df["policy_intensity_index"] = (
        (pol_df["itc_rate_pct"] / 30.0) * 0.30 +            # ITC relative to max 30%
        (pol_df["ptc_rate_cents_kwh"] / 2.75) * 0.25 +      # PTC rate relative to max 2.75¢/kWh
        (pol_df["n_rps_states"] / pol_df["n_rps_states"].max()) * 0.25 +  # RPS adoption breadth
        (pol_df["cumulative_ferc_reforms"] / pol_df["cumulative_ferc_reforms"].max()) * 0.10 +  # Regulatory evolution
        (pol_df["ca_rps_target_pct"] / 100.0) * 0.10        # CA ambition level
    )

    print(f"\n  Policy intensity index: {pol_df['policy_intensity_index'].iloc[0]:.3f} (1990) → "
          f"{pol_df['policy_intensity_index'].iloc[-1]:.3f} (2025)")

    # ---- Merge into master dataset ----
    n_before = len(master.columns)
    pol_cols = [c for c in pol_df.columns if c != "year"]
    master = master.merge(pol_df, on="year", how="left")

    new_cols = len(master.columns) - n_before
    n_filled = master[pol_cols].notna().sum().sum()
    n_total = len(master) * len(pol_cols)

    print(f"\n  Added {new_cols} new policy columns")
    print(f"  Coverage: {n_filled}/{n_total} ({100*n_filled/n_total:.1f}%) cells filled")
    print(f"  Total columns now: {len(master.columns)}")

    master.to_csv(MASTER_V7, index=False)
    print(f"  Saved: {MASTER_V7}")

    return master


# ============================================================================
# STEP 2: REGRESSION WITH POLICY VARIABLES
# ============================================================================
def step2_regression(master):
    banner("STEP 2: Regression with policy variables")

    results = {}

    # ---- ERCOT Wholesale Price Model ----
    print("\n  --- ERCOT Wholesale Price Model + Policy Variables ---")
    ercot_base_cols = ["henry_hub_spot", "is_summer", "is_winter"]

    # Policy variable candidates for ERCOT
    # NOTE: Binary dummies (ira_active, ptc_active, tx_crez_active) removed —
    # they absorb time trends rather than causal effects (see literature validation).
    # Continuous measures (itc_rate_pct, ptc_rate_cents_kwh) retained instead.
    pol_candidates_ercot = [
        "itc_rate_pct", "ptc_rate_cents_kwh",
        "queue_backlog_gw", "ercot_large_load_queue_gw",
        "cumulative_ferc_reforms",
        "n_ira_credits",
    ]

    # Demand drivers: use MONTHLY scarcity metrics from RTM daily aggregation
    # (ercot_scarcity_intensity, ercot_spike_count are monthly from daily data)
    # These separate fuel-cost passthrough from scarcity/congestion premium
    dd_cols_ercot = [
        "tx_gdp_growth_pct", "tx_data_center_twh", "tx_ercot_demand_twh",
        "us_industrial_prod_index",
        "ercot_scarcity_intensity", "ercot_spike_count",
        "ercot_actual_reserve_margin_pct",
        "ercot_congestion_per_mwh",
    ]
    gm_cols_ercot = ["tx_gas_gen_pct", "tx_wind_gen_pct", "tx_coal_gen_pct",
                     "tx_solar_gen_pct"]

    ercot_df = master.dropna(subset=["ercot_wholesale_mwh", "henry_hub_spot"]).copy()
    # Exclude Feb 2021 (Uri) — structural grid failure, not price formation
    ercot_df, uri_n = _exclude_uri(ercot_df)
    if uri_n > 0:
        print(f"    Excluded {uri_n} Uri observation(s) (Feb 2021) from ERCOT sample")
    y_ercot = ercot_df["ercot_wholesale_mwh"].astype(float)

    if len(ercot_df) >= 20:
        # Model A: Baseline
        results["ercot_baseline"] = _ols(
            _make_X(ercot_df, ercot_base_cols), y_ercot, "ERCOT_Baseline")

        # Model B: + policy only
        pol_avail = [c for c in pol_candidates_ercot if c in ercot_df.columns and ercot_df[c].notna().sum() > 20]
        if pol_avail:
            X_pol = _make_X(ercot_df, ercot_base_cols + pol_avail)
            X_pol, dropped_pol = _prune_by_vif(X_pol, y_ercot, threshold=10)
            results["ercot_policy"] = _ols(X_pol, y_ercot, "ERCOT_Policy")

        # Model C: Full (base + demand drivers + gen mix + policy)
        dd_avail = [c for c in dd_cols_ercot if c in ercot_df.columns and ercot_df[c].notna().sum() > 20]
        gm_avail = [c for c in gm_cols_ercot if c in ercot_df.columns and ercot_df[c].notna().sum() > 20]
        full_avail = dd_avail + gm_avail + pol_avail
        if full_avail:
            X_full = _make_X(ercot_df, ercot_base_cols + full_avail)
            X_full, dropped_full = _prune_by_vif(X_full, y_ercot, threshold=10)
            results["ercot_full"] = _ols(X_full, y_ercot, "ERCOT_Full")
            results["ercot_residuals"] = results["ercot_full"]["residuals"]

        _print_r2_comparison("ERCOT", results, ["ercot_baseline", "ercot_policy", "ercot_full"])

    # ---- CAISO Wholesale Price Model ----
    print("\n  --- CAISO Wholesale Price Model + Policy Variables ---")
    caiso_col = "caiso_wholesale_mwh" if "caiso_wholesale_mwh" in master.columns else "caiso_wholesale_historical_mwh"
    caiso_df = master.dropna(subset=[caiso_col, "henry_hub_spot"]).copy()
    # Exclude Feb 2021 (Uri) — gas price spike distorts passthrough across all markets
    caiso_df, uri_n_c = _exclude_uri(caiso_df)
    if uri_n_c > 0:
        print(f"    Excluded {uri_n_c} Uri observation(s) (Feb 2021) from CAISO sample")
    y_caiso = caiso_df[caiso_col].astype(float)

    # NOTE: ca_cap_trade_active (binary dummy) removed — absorbs shale gas
    # price collapse + solar ramp, not carbon pricing. Replace with
    # ca_allowance_price_per_ton when available. ira_active also removed.
    pol_candidates_caiso = [
        "itc_rate_pct", "ptc_rate_cents_kwh",
        "ca_rps_target_pct", "ca_allowance_price_per_ton",
        "ca_allowance_spread",
        "ca_nem_compensation_level", "ca_storage_mandate",
        "ca_solar_mandate", "der_market_access",
        "queue_backlog_gw", "n_rps_states",
    ]
    dd_cols_caiso = [
        "ca_gdp_growth_pct", "ca_data_center_twh", "ca_caiso_demand_twh",
        "caiso_congestion_per_mwh",
    ]
    # Use monthly-frequency curtailment (from Excel) and negative hours (from DAM hourly)
    # These have actual within-month variation unlike annual forward-fills
    gm_cols_caiso = ["ca_gas_gen_pct", "ca_wind_gen_pct",
                     "caiso_total_curtail_gwh", "caiso_monthly_negative_hours"]

    if len(caiso_df) >= 20:
        results["caiso_baseline"] = _ols(
            _make_X(caiso_df, ercot_base_cols), y_caiso, "CAISO_Baseline")

        pol_avail_c = [c for c in pol_candidates_caiso if c in caiso_df.columns and caiso_df[c].notna().sum() > 20]
        if pol_avail_c:
            X_pol_c = _make_X(caiso_df, ercot_base_cols + pol_avail_c)
            X_pol_c, dropped_pol_c = _prune_by_vif(X_pol_c, y_caiso, threshold=10)
            results["caiso_policy"] = _ols(X_pol_c, y_caiso, "CAISO_Policy")

        dd_avail_c = [c for c in dd_cols_caiso if c in caiso_df.columns and caiso_df[c].notna().sum() > 20]
        gm_avail_c = [c for c in gm_cols_caiso if c in caiso_df.columns and caiso_df[c].notna().sum() > 20]
        full_avail_c = dd_avail_c + gm_avail_c + pol_avail_c
        if full_avail_c:
            X_full_c = _make_X(caiso_df, ercot_base_cols + full_avail_c)
            X_full_c, dropped_full_c = _prune_by_vif(X_full_c, y_caiso, threshold=10)
            results["caiso_full"] = _ols(X_full_c, y_caiso, "CAISO_Full")
            results["caiso_residuals"] = results["caiso_full"]["residuals"]

        _print_r2_comparison("CAISO", results, ["caiso_baseline", "caiso_policy", "caiso_full"])

    # ---- Henry Hub Gas Price Model ----
    print("\n  --- Henry Hub Price Model + Policy Variables ---")
    hh_df = master.dropna(subset=["henry_hub_spot"]).copy()
    # Exclude Feb 2021 (Uri) — gas prices spiked to $23+/MMBtu nationally
    hh_df, uri_n_h = _exclude_uri(hh_df)
    if uri_n_h > 0:
        print(f"    Excluded {uri_n_h} Uri observation(s) (Feb 2021) from HH sample")
    y_hh = hh_df["henry_hub_spot"].astype(float)

    hh_base = ["electric_power_bcfd", "is_winter", "is_summer"]
    # NOTE: ira_active removed — conflated with post-Ukraine gas price spike.
    # policy_intensity_index removed — contains binary dummies as components.
    hh_pol = [
        "itc_rate_pct", "ptc_rate_cents_kwh",
        "queue_backlog_gw",
        "cumulative_ferc_reforms",
    ]
    hh_dd = ["us_gdp_growth_pct", "us_industrial_prod_index", "us_data_center_twh"]

    if len(hh_df) >= 30:
        hh_base_avail = [c for c in hh_base if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        results["hh_baseline"] = _ols(_make_X(hh_df, hh_base_avail), y_hh, "HH_Baseline")

        hh_pol_avail = [c for c in hh_pol if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        if hh_pol_avail:
            X_hh_pol = _make_X(hh_df, hh_base_avail + hh_pol_avail)
            X_hh_pol, dropped_hh_pol = _prune_by_vif(X_hh_pol, y_hh, threshold=10)
            results["hh_policy"] = _ols(X_hh_pol, y_hh, "HH_Policy")

        hh_dd_avail = [c for c in hh_dd if c in hh_df.columns and hh_df[c].notna().sum() > 30]
        hh_full_avail = hh_dd_avail + hh_pol_avail
        if hh_full_avail:
            X_hh_full = _make_X(hh_df, hh_base_avail + hh_full_avail)
            X_hh_full, dropped_hh_full = _prune_by_vif(X_hh_full, y_hh, threshold=10)
            results["hh_full"] = _ols(X_hh_full, y_hh, "HH_Full")
            results["hh_residuals"] = results["hh_full"]["residuals"]

        _print_r2_comparison("Henry Hub", results, ["hh_baseline", "hh_policy", "hh_full"])

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


# EIA fleet-average heat rates (MMBtu/MWh) by region — DIAGNOSTIC REFERENCE ONLY
# These are used in Variant G to show what non-gas coefficients look like when
# gas passthrough is fixed. They are NOT used in the primary models (A-F).
# Source: EIA Heat Rate Table 2024, fleet-average CCGT = 7.55, CT peaker = 11.0
HEAT_RATES = {
    "ercot": 8.2,   # ERCOT weighted avg: ~60% CCGT (7.55) + ~40% CT/steam (10.5)
    "caiso": 7.8,   # CAISO weighted avg: higher CCGT share, less peaker reliance
    "national": 8.0, # National average
}


def _compute_gas_cost_component(df, region="ercot"):
    """Compute the gas-to-electricity cost component using physics-based heat rate.

    DIAGNOSTIC ONLY: This imposes a heat rate rather than estimating it.
    Used in Variant G as a sanity check, not in primary model selection.
    """
    heat_rate = HEAT_RATES.get(region, 8.0)
    if "henry_hub_spot" in df.columns:
        return df["henry_hub_spot"] * heat_rate
    return pd.Series(np.nan, index=df.index)


def _two_stage_regression(df, y_col, non_gas_vars, region="ercot"):
    """Two-stage approach — DIAGNOSTIC REFERENCE, not a primary model.

    Stage 1: Compute gas_cost_component = HH × heat_rate (imposed, not estimated)
    Stage 2: Regress (electricity_price - gas_cost_component) on non-gas fundamentals

    Purpose: Show what DC, renewable, and policy coefficients look like when
    gas passthrough is removed at the physics-based rate. Compare these against
    Variants A-F (where gas passthrough is freely estimated with VIF < 10 pruning)
    to check whether the freely-estimated gas coefficient is distorting other variables.
    """
    gas_cost = _compute_gas_cost_component(df, region)
    y_raw = df[y_col].astype(float)
    y_residual = y_raw - gas_cost

    # Filter to non-gas regressors (exclude henry_hub_spot)
    non_gas = [v for v in non_gas_vars if v != "henry_hub_spot" and v in df.columns]
    X = _make_X(df, non_gas)
    X, vif_dropped = _prune_by_vif(X, y_residual, threshold=10)

    # Run OLS on the residualized price
    ols_result = _ols(X, y_residual, f"{region.upper()}_TwoStage")

    # Store metadata about the two-stage approach
    heat_rate = HEAT_RATES.get(region, 8.0)
    ols_result["two_stage"] = {
        "heat_rate_used": heat_rate,
        "gas_cost_component_mean": float(gas_cost.mean()),
        "gas_cost_component_std": float(gas_cost.std()),
        "implied_gas_passthrough": heat_rate,  # $/MWh per $/MMBtu (by construction)
        "residualized_dv_mean": float(y_residual.mean()),
    }
    ols_result["vif_dropped"] = [d["var"] if isinstance(d, dict) else d for d in vif_dropped]

    print(f"    Two-stage: heat_rate={heat_rate} MMBtu/MWh, "
          f"gas_cost_mean=${gas_cost.mean():.1f}/MWh, "
          f"residual_mean=${y_residual.mean():.1f}/MWh")

    return ols_result, gas_cost, y_residual


def _prune_collinear(X, y, threshold=0.99):
    """Remove perfectly/nearly collinear columns to ensure X'X is invertible.

    Strategy:
    1. Compute pairwise correlations among non-intercept columns
    2. For pairs with |r| > threshold, drop the column with less variance
    3. Verify matrix is full rank via SVD; remove additional dependent columns

    Returns: (pruned X DataFrame, list of dropped column names)
    """
    cols = [c for c in X.columns if c != "intercept"]
    if len(cols) < 2:
        return X, []

    # Work on complete rows only
    X_np = X.values.astype(float)
    y_np = y.values.astype(float)
    mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
    X_clean = X_np[mask]

    if X_clean.shape[0] < 2:
        return X, []

    dropped = []
    keep = list(cols)

    # Step 1: Correlation-based pruning (non-intercept columns start at index 1)
    col_data = X_clean[:, 1:]  # skip intercept
    if col_data.shape[1] >= 2:
        corr = np.corrcoef(col_data, rowvar=False)
        to_drop = set()
        for i in range(len(keep)):
            if keep[i] in to_drop:
                continue
            for j in range(i + 1, len(keep)):
                if keep[j] in to_drop:
                    continue
                if abs(corr[i, j]) > threshold:
                    # Drop the column with less variance (less unique information)
                    var_i = np.var(col_data[:, i])
                    var_j = np.var(col_data[:, j])
                    drop_col = keep[j] if var_i >= var_j else keep[i]
                    to_drop.add(drop_col)
        if to_drop:
            keep = [c for c in keep if c not in to_drop]
            dropped.extend(to_drop)

    # Step 2: Rank check via SVD — remove columns causing rank deficiency
    X_pruned = X[["intercept"] + keep]
    X_np2 = X_pruned.values.astype(float)
    mask2 = ~np.isnan(X_np2).any(axis=1)
    X_check = X_np2[mask2]

    rank = np.linalg.matrix_rank(X_check)
    max_iters = 10
    while rank < X_check.shape[1] and len(keep) > 0 and max_iters > 0:
        max_iters -= 1
        U, S, Vt = np.linalg.svd(X_check, full_matrices=False)
        tol = S[0] * max(X_check.shape) * np.finfo(float).eps
        # Find the smallest singular value and its most contributing column
        for sv_idx in range(len(S) - 1, -1, -1):
            if S[sv_idx] < tol * 10:
                contrib = np.abs(Vt[sv_idx, 1:])  # skip intercept
                if len(contrib) > 0:
                    worst_idx = np.argmax(contrib)
                    worst_col = keep[worst_idx]
                    keep.remove(worst_col)
                    dropped.append(worst_col)
                break
        X_pruned = X[["intercept"] + keep]
        X_np2 = X_pruned.values.astype(float)
        X_check = X_np2[~np.isnan(X_np2).any(axis=1)]
        rank = np.linalg.matrix_rank(X_check)

    if dropped:
        print(f"    Collinearity pruning: dropped {dropped}")
        print(f"    Kept {len(keep)} regressors (+ intercept)")

    return X[["intercept"] + keep], list(set(dropped))


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
        XtX = X_np.T @ X_np
        cond = np.linalg.cond(XtX)
        if cond > 1e12:
            # Near-singular: use pseudoinverse for covariance
            cov = ms_res * np.linalg.pinv(XtX)
        else:
            cov = ms_res * np.linalg.inv(XtX)
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
# STEP 13 HELPERS: DATA QUALITY, ERCOT AGGREGATION, DIAGNOSTICS
# ============================================================================

def _detect_backfill(series):
    """Flag columns that appear to be forward-filled from sparse annual data."""
    if series.isna().all():
        return False
    if not np.issubdtype(series.dtype, np.number):
        return False
    valid = series.dropna()
    if len(valid) < 12:
        return False
    try:
        diffs = valid.diff().abs()
        change_rate = (diffs > 0).mean()
        return bool(change_rate < 0.10)
    except (TypeError, ValueError):
        return False


def _data_quality_audit(master):
    """Systematic tracking of coverage gaps per variable."""
    report = {}
    for col in master.columns:
        s = master[col]
        first_valid = s.first_valid_index()
        last_valid = s.last_valid_index()
        report[col] = {
            "coverage_start": str(master.loc[first_valid, "date"]) if first_valid is not None and "date" in master.columns else None,
            "coverage_end": str(master.loc[last_valid, "date"]) if last_valid is not None and "date" in master.columns else None,
            "n_valid": int(s.notna().sum()),
            "n_total": len(s),
            "missing_pct": round(float(s.isna().mean()) * 100, 1),
            "dtype": str(s.dtype),
            "is_constant": bool(s.nunique() <= 1),
            "suspected_backfill": _detect_backfill(s),
        }
    # Print warnings
    warnings = []
    for col, info in report.items():
        if info["missing_pct"] > 50:
            warnings.append(f"  WARNING: {col} — {info['missing_pct']}% missing")
        if info["suspected_backfill"]:
            warnings.append(f"  WARNING: {col} — suspected backfill (annual→monthly forward-fill)")
        if info["is_constant"] and info["n_valid"] > 0:
            warnings.append(f"  WARNING: {col} — constant value (zero variance)")
    if warnings:
        print(f"\n  Data Quality Warnings ({len(warnings)}):")
        for w in warnings:
            print(w)
    return report


def _interpolate_annual(year_val_dict, max_gap=2):
    """Interpolate missing years in annual data.

    For gaps <= max_gap years: linear interpolation.
    For gaps > max_gap years: leave NaN and log warning.
    Returns dict of year → value with interpolated values filled in.
    """
    if not year_val_dict:
        return {}, []
    sorted_years = sorted(year_val_dict.keys())
    min_yr, max_yr = sorted_years[0], sorted_years[-1]
    full = {}
    gap_warnings = []
    for yr in range(min_yr, max_yr + 1):
        if yr in year_val_dict:
            full[yr] = year_val_dict[yr]
        else:
            # Find nearest data points before and after
            before = [y for y in sorted_years if y < yr]
            after = [y for y in sorted_years if y > yr]
            if before and after:
                y0, y1 = before[-1], after[0]
                gap_size = y1 - y0 - 1
                if gap_size <= max_gap:
                    v0, v1 = year_val_dict[y0], year_val_dict[y1]
                    if v0 is not None and v1 is not None:
                        frac = (yr - y0) / (y1 - y0)
                        full[yr] = v0 + frac * (v1 - v0)
                    else:
                        full[yr] = None
                else:
                    full[yr] = None
                    gap_warnings.append(f"MISSING DATA: {gap_size}-year gap ({y0+1}-{y1-1})")
            else:
                full[yr] = None
    return full, gap_warnings


def _aggregate_ercot_daily_to_monthly(daily_path):
    """Aggregate pre-computed daily stats to monthly features.

    Uses HB_HOUSTON as representative hub (highest load center, closest to
    data center concentrations in Dallas/San Antonio corridor).
    """
    daily = pd.read_csv(daily_path)
    # Filter to HB_HOUSTON
    hub_col = "Location" if "Location" in daily.columns else "location"
    houston = daily[daily[hub_col] == "HB_HOUSTON"].copy()
    houston["date"] = pd.to_datetime(houston["date"])
    houston["year"] = houston["date"].dt.year
    houston["month"] = houston["date"].dt.month

    monthly = houston.groupby(["year", "month"]).agg(
        ercot_rtm_mean=("price_mean", "mean"),
        ercot_rtm_p90=("price_p90", "mean"),
        ercot_rtm_p95=("price_p95", "mean"),
        ercot_rtm_max=("price_max", "max"),
        ercot_rtm_std=("price_std", "mean"),
        ercot_rtm_cv=("price_cv", "mean"),
        ercot_spike_count=("hours_gt_100", "sum"),
        ercot_spikes_gt200=("hours_gt_200", "sum"),
        ercot_spikes_gt500=("hours_gt_500", "sum"),
        ercot_extreme_spikes=("hours_gt_1000", "sum"),
        ercot_spikes_gt5000=("hours_gt_5000", "sum"),
        ercot_negative_hours=("hours_negative", "sum"),
        ercot_price_range=("price_range", "mean"),
    ).reset_index()
    # Monthly scarcity intensity: weighted sum of spike thresholds
    monthly["ercot_scarcity_intensity"] = (
        monthly["ercot_spike_count"] * 1
        + monthly["ercot_spikes_gt200"] * 2
        + monthly["ercot_spikes_gt500"] * 5
        + monthly["ercot_extreme_spikes"] * 10
        + monthly["ercot_spikes_gt5000"] * 50
    )

    print(f"  ERCOT RTM daily → monthly: {len(monthly)} months "
          f"({houston['year'].min()}-{houston['year'].max()}), hub=HB_HOUSTON")
    return monthly


def _aggregate_caiso_dam_to_monthly(hourly_path):
    """Aggregate CAISO DAM hourly data to monthly features.

    Uses SP15 trading hub (Southern CA, largest load zone).
    Produces: monthly avg LMP, negative price hours, congestion cost,
    price volatility, and spike counts.
    """
    dam = pd.read_csv(hourly_path)
    # Filter to SP15 (largest CAISO hub)
    sp15 = dam[dam["Location"] == "TH_SP15_GEN-APND"].copy()
    sp15["dt"] = pd.to_datetime(sp15["Time"].str[:19])
    sp15["year"] = sp15["dt"].dt.year
    sp15["month"] = sp15["dt"].dt.month

    # Compute per-row flags
    sp15["is_negative"] = (sp15["LMP"] < 0).astype(int)
    sp15["is_gt100"] = (sp15["LMP"] > 100).astype(int)
    sp15["is_gt200"] = (sp15["LMP"] > 200).astype(int)
    sp15["congestion_abs"] = sp15["Congestion"].abs()

    monthly = sp15.groupby(["year", "month"]).agg(
        caiso_dam_mean=("LMP", "mean"),
        caiso_dam_median=("LMP", "median"),
        caiso_dam_max=("LMP", "max"),
        caiso_dam_min=("LMP", "min"),
        caiso_dam_std=("LMP", "std"),
        caiso_monthly_negative_hours=("is_negative", "sum"),
        caiso_monthly_spikes_gt100=("is_gt100", "sum"),
        caiso_monthly_spikes_gt200=("is_gt200", "sum"),
        caiso_monthly_congestion_avg=("congestion_abs", "mean"),
        caiso_monthly_energy_avg=("Energy", "mean"),
        caiso_monthly_loss_avg=("Loss", "mean"),
    ).reset_index()

    # Negative price percentage
    hours_per_month = sp15.groupby(["year", "month"]).size().reset_index(name="n_hours")
    monthly = monthly.merge(hours_per_month, on=["year", "month"])
    monthly["caiso_negative_pct"] = monthly["caiso_monthly_negative_hours"] / monthly["n_hours"] * 100
    monthly.drop(columns=["n_hours"], inplace=True)

    print(f"  CAISO DAM hourly → monthly: {len(monthly)} months "
          f"({sp15['year'].min()}-{sp15['year'].max()}), hub=SP15")
    return monthly


def _aggregate_caiso_daily_data_to_monthly(caiso_daily_dir):
    """Parse all CAISO daily data files and aggregate to monthly system-average.

    Uses system-average across NP15/SP15/ZP26 trading hubs (not SP15 alone)
    to maximize temporal coverage — NP15 has 2018 data, SP15 starts Feb 2019.

    Combines five data sources (DAM preferred, ICE bilateral fills gaps):
    1. caiso_lmp_multiple_year.csv (2018-2022, hourly DAM LMP)
    2. Regional DAM files (2023-2024, OASIS format)
    3. 2025 monthly OASIS files (1 day/month, all nodes)
    4. CAISO_YYYY.csv (2014-2020, ICE bilateral peak-hour VWAP, NP15/SP15)
    5. SP15/NP15 xlsx files (2001-2013, ICE bilateral peak-hour VWAP)

    ICE bilateral data is peak-hour only (volume-weighted avg across hubs).
    DAM data is preferred when both sources cover the same month.

    Returns DataFrame with year, month, and monthly CAISO features.
    """
    import glob as glob_mod
    all_hourly = []

    # --- Source 1: Multi-year file (simple format) ---
    # Use all three trading hubs (NP15, SP15, ZP26) for system-average
    multiyear_path = os.path.join(caiso_daily_dir, "caiso_lmp_multiple_year.csv")
    if os.path.exists(multiyear_path):
        df = pd.read_csv(multiyear_path)
        # Use all three major CAISO trading hubs for system-average
        hubs = df[df["Node"].isin(["NP15", "SP15", "ZP26"])].copy()
        hubs["dt"] = pd.to_datetime(hubs["Date"], format="mixed")
        hubs = hubs.rename(columns={"LMP": "lmp", "Energy": "energy",
                                     "Congestion": "congestion", "Losses": "losses"})
        hubs["lmp"] = pd.to_numeric(hubs["lmp"], errors="coerce")
        hubs["energy"] = pd.to_numeric(hubs["energy"], errors="coerce")
        hubs["congestion"] = pd.to_numeric(hubs["congestion"], errors="coerce")
        hubs["losses"] = pd.to_numeric(hubs["losses"], errors="coerce")
        hubs["year"] = hubs["dt"].dt.year
        hubs["month"] = hubs["dt"].dt.month
        hubs["source"] = "multiyear"
        all_hourly.append(hubs[["year", "month", "dt", "lmp", "energy",
                                "congestion", "losses", "source"]])
        nodes_str = "/".join(sorted(hubs["Node"].unique()))
        print(f"    Multi-year file: {len(hubs)} rows ({nodes_str}) "
              f"({hubs['dt'].min().date()} to {hubs['dt'].max().date()})")
    else:
        print(f"    Multi-year file not found")

    # --- Source 2: Regional DAM files (OASIS format) — all hubs ---
    _oasis_hubs = {"TH_NP15_GEN-APND", "TH_SP15_GEN-APND", "TH_ZP26_GEN-APND"}
    for pattern in ["caiso_dam_SP15_*.csv", "caiso_dam_NP15_*.csv",
                     "caiso_dam_ZP26_*.csv", "caiso_dam_all_hubs_*.csv"]:
        for fpath in sorted(glob_mod.glob(os.path.join(caiso_daily_dir, pattern))):
            if "_daily" in os.path.basename(fpath):
                continue  # skip daily summary files
            try:
                df = pd.read_csv(fpath)
                lmp_rows = df[(df["NODE_ID"].isin(_oasis_hubs)) &
                              (df["LMP_TYPE"] == "LMP")].copy()
                if len(lmp_rows) == 0:
                    continue
                lmp_rows["dt"] = pd.to_datetime(lmp_rows["OPR_DT"])
                lmp_rows["year"] = lmp_rows["dt"].dt.year
                lmp_rows["month"] = lmp_rows["dt"].dt.month
                lmp_rows["lmp"] = pd.to_numeric(lmp_rows["MW"], errors="coerce")
                lmp_rows["energy"] = np.nan
                lmp_rows["congestion"] = np.nan
                lmp_rows["losses"] = np.nan
                lmp_rows["source"] = "regional_dam"
                all_hourly.append(lmp_rows[["year", "month", "dt", "lmp", "energy",
                                            "congestion", "losses", "source"]])
                print(f"    {os.path.basename(fpath)}: {len(lmp_rows)} hub LMP rows")
            except Exception as e:
                print(f"    SKIP {os.path.basename(fpath)}: {e}")

    # --- Source 3: 2025 OASIS monthly files (1 day per month, all nodes) ---
    oasis_2025_patterns = ["caiso_daily_*.csv", "caiso-daily-*.csv", "caiso_daily-*.csv"]
    oasis_files_seen = set()
    for pattern in oasis_2025_patterns:
        for fpath in sorted(glob_mod.glob(os.path.join(caiso_daily_dir, pattern))):
            basename = os.path.basename(fpath)
            # Skip files already handled (regional DAM, daily summaries, multi-year)
            if "SP15" in basename or "NP15" in basename or "ZP26" in basename:
                continue
            if "all_hubs" in basename or "multiple_year" in basename:
                continue
            if "_daily." in basename and not any(m in basename for m in
                    ["jan", "feb", "mar", "apr", "may", "jun",
                     "jul", "aug", "sep", "oct", "nov", "dec"]):
                continue
            if basename in oasis_files_seen:
                continue
            oasis_files_seen.add(basename)
            try:
                df = pd.read_csv(fpath)
                if "NODE_ID" not in df.columns or "LMP_TYPE" not in df.columns:
                    continue
                hub_rows = df[(df["NODE_ID"].isin(_oasis_hubs)) &
                              (df["LMP_TYPE"] == "LMP")].copy()
                if len(hub_rows) == 0:
                    continue
                hub_rows["dt"] = pd.to_datetime(hub_rows["OPR_DT"])
                hub_rows["year"] = hub_rows["dt"].dt.year
                hub_rows["month"] = hub_rows["dt"].dt.month
                hub_rows["lmp"] = pd.to_numeric(hub_rows["MW"], errors="coerce")
                hub_rows["energy"] = np.nan
                hub_rows["congestion"] = np.nan
                hub_rows["losses"] = np.nan
                hub_rows["source"] = "oasis_2025"
                all_hourly.append(hub_rows[["year", "month", "dt", "lmp", "energy",
                                            "congestion", "losses", "source"]])
                print(f"    {basename}: {len(hub_rows)} hub LMP rows")
            except Exception as e:
                print(f"    SKIP {basename}: {e}")

    # --- Source 4: ICE Bilateral Peak-Hour Trade CSVs (CAISO_2014-2020) ---
    # These are daily volume-weighted average prices for NP15/SP15 peak hours.
    # Format: Price hub, Trade date, Wtd avg price $/MWh, Daily volume MWh
    ice_bilateral_daily = []
    for yr in range(2014, 2025):
        fpath = os.path.join(caiso_daily_dir, f"CAISO_{yr}.csv")
        if not os.path.exists(fpath):
            continue
        try:
            df = pd.read_csv(fpath)
            # Normalize column names (lowercase, strip whitespace)
            df.columns = [c.strip() for c in df.columns]
            col_map = {}
            for c in df.columns:
                cl = c.lower()
                if "price hub" in cl:
                    col_map["hub"] = c
                elif "trade date" in cl:
                    col_map["trade_date"] = c
                elif "wtd avg" in cl:
                    col_map["vwap"] = c
                elif "daily volume" in cl:
                    col_map["volume"] = c
            if not all(k in col_map for k in ["hub", "trade_date", "vwap"]):
                print(f"    SKIP CAISO_{yr}.csv: missing required columns")
                continue
            df["dt"] = pd.to_datetime(df[col_map["trade_date"]], format="mixed")
            df["vwap"] = pd.to_numeric(df[col_map["vwap"]], errors="coerce")
            vol_col = col_map.get("volume")
            if vol_col:
                df["volume"] = pd.to_numeric(df[col_map["volume"]], errors="coerce")
            else:
                df["volume"] = 1.0
            df["hub_norm"] = df[col_map["hub"]].str.upper().apply(
                lambda x: "NP15" if "NP15" in x or "NP 15" in x
                else ("SP15" if "SP15" in x or "SP 15" in x else "OTHER"))
            df = df[df["hub_norm"].isin(["NP15", "SP15"])].copy()
            df = df.dropna(subset=["vwap"])
            df["year"] = df["dt"].dt.year
            df["month"] = df["dt"].dt.month
            ice_bilateral_daily.append(df[["year", "month", "dt", "hub_norm",
                                           "vwap", "volume"]])
            hubs_str = "/".join(sorted(df["hub_norm"].unique()))
            print(f"    CAISO_{yr}.csv (ICE bilateral): {len(df)} rows ({hubs_str}) "
                  f"({df['dt'].min().date()} to {df['dt'].max().date()})")
        except Exception as e:
            print(f"    SKIP CAISO_{yr}.csv: {e}")

    # --- Source 5: ICE Bilateral xlsx files (SP15 2001-2013, NP15 2009-2013) ---
    xlsx_files = [
        ("SP 15_daily_2001_2009.xlsx", None),
        ("SP 15_daily_2009_2013.xlsx", None),
        ("NP15_daily_2000-2013.xlsx", None),
    ]
    for fname, _ in xlsx_files:
        fpath = os.path.join(caiso_daily_dir, fname)
        if not os.path.exists(fpath):
            continue
        try:
            df = pd.read_excel(fpath, sheet_name=0)
            df.columns = [c.strip() for c in df.columns]
            col_map = {}
            for c in df.columns:
                cl = c.lower()
                if "price hub" in cl:
                    col_map["hub"] = c
                elif "trade date" in cl:
                    col_map["trade_date"] = c
                elif "wtd avg" in cl:
                    col_map["vwap"] = c
                elif "daily volume" in cl:
                    col_map["volume"] = c
            if not all(k in col_map for k in ["hub", "trade_date", "vwap"]):
                print(f"    SKIP {fname}: missing required columns")
                continue
            df["dt"] = pd.to_datetime(df[col_map["trade_date"]], format="mixed")
            df["vwap"] = pd.to_numeric(df[col_map["vwap"]], errors="coerce")
            vol_col = col_map.get("volume")
            if vol_col:
                df["volume"] = pd.to_numeric(df[col_map["volume"]], errors="coerce")
            else:
                df["volume"] = 1.0
            # Normalize hub names (SP 15, SP-15 Peak, SP-15 Gen DA LMP Peak → SP15, etc.)
            df["hub_norm"] = df[col_map["hub"]].str.upper().apply(
                lambda x: "NP15" if "NP15" in x or "NP 15" in x
                else ("SP15" if "SP15" in x or "SP 15" in x or "SP-15" in x
                      else "OTHER"))
            df = df[df["hub_norm"].isin(["NP15", "SP15"])].copy()
            df = df.dropna(subset=["vwap"])
            df["year"] = df["dt"].dt.year
            df["month"] = df["dt"].dt.month
            ice_bilateral_daily.append(df[["year", "month", "dt", "hub_norm",
                                           "vwap", "volume"]])
            hubs_str = "/".join(sorted(df["hub_norm"].unique()))
            print(f"    {fname} (ICE bilateral): {len(df)} rows ({hubs_str}) "
                  f"({df['dt'].min().date()} to {df['dt'].max().date()})")
        except Exception as e:
            print(f"    SKIP {fname}: {e}")

    # Aggregate ICE bilateral daily → monthly (volume-weighted avg across hubs)
    ice_monthly = pd.DataFrame()
    if ice_bilateral_daily:
        ice_all = pd.concat(ice_bilateral_daily, ignore_index=True)
        # Volume-weighted average across hubs and days within each month
        ice_all["vw"] = ice_all["vwap"] * ice_all["volume"].fillna(1)
        ice_grp = ice_all.groupby(["year", "month"]).agg(
            sum_vw=("vw", "sum"),
            sum_vol=("volume", "sum"),
            n_trades=("vwap", "count"),
            ice_max=("vwap", "max"),
            ice_min=("vwap", "min"),
            ice_std=("vwap", "std"),
        ).reset_index()
        ice_grp["ice_vwap"] = ice_grp["sum_vw"] / ice_grp["sum_vol"]
        ice_monthly = ice_grp[["year", "month", "ice_vwap", "n_trades",
                                "ice_max", "ice_min", "ice_std"]].copy()
        print(f"    ICE bilateral combined: {len(ice_all)} daily obs → "
              f"{len(ice_monthly)} months ({ice_all['year'].min()}-{ice_all['year'].max()})")

    # --- Combine DAM sources ---
    if not all_hourly:
        dam_monthly = pd.DataFrame()
        print("    No DAM hourly data parsed")
    else:
        combined = pd.concat(all_hourly, ignore_index=True)
        combined = combined.dropna(subset=["lmp"])

        combined["is_negative"] = (combined["lmp"] < 0).astype(int)
        combined["is_gt100"] = (combined["lmp"] > 100).astype(int)
        combined["is_gt200"] = (combined["lmp"] > 200).astype(int)
        combined["congestion_abs"] = combined["congestion"].abs()

        dam_monthly = combined.groupby(["year", "month"]).agg(
            caiso_daily_mean=("lmp", "mean"),
            caiso_daily_median=("lmp", "median"),
            caiso_daily_max=("lmp", "max"),
            caiso_daily_min=("lmp", "min"),
            caiso_daily_std=("lmp", "std"),
            caiso_daily_negative_hours=("is_negative", "sum"),
            caiso_daily_spikes_gt100=("is_gt100", "sum"),
            caiso_daily_spikes_gt200=("is_gt200", "sum"),
            caiso_daily_congestion_avg=("congestion_abs", "mean"),
            caiso_daily_energy_avg=("energy", "mean"),
            caiso_daily_loss_avg=("losses", "mean"),
            n_obs=("lmp", "count"),
        ).reset_index()

        dam_monthly["caiso_daily_negative_pct"] = (
            dam_monthly["caiso_daily_negative_hours"] / dam_monthly["n_obs"] * 100
        )
        dam_monthly["source"] = "dam"

        print(f"    DAM hourly: {len(combined)} obs → {len(dam_monthly)} months "
              f"({combined['year'].min()}-{combined['year'].max()})")

    # --- Merge: DAM preferred, ICE bilateral fills gaps ---
    if len(dam_monthly) == 0 and len(ice_monthly) == 0:
        print("    No CAISO daily data parsed from any source")
        return pd.DataFrame()

    if len(dam_monthly) > 0 and len(ice_monthly) > 0:
        # Find months only in ICE (not covered by DAM)
        dam_ym = set(zip(dam_monthly["year"], dam_monthly["month"]))
        ice_only = ice_monthly[~ice_monthly.apply(
            lambda r: (r["year"], r["month"]) in dam_ym, axis=1)].copy()

        if len(ice_only) > 0:
            # Convert ICE bilateral to same schema as DAM monthly
            ice_as_dam = pd.DataFrame({
                "year": ice_only["year"],
                "month": ice_only["month"],
                "caiso_daily_mean": ice_only["ice_vwap"],
                "caiso_daily_median": ice_only["ice_vwap"],  # VWAP ≈ median for bilateral
                "caiso_daily_max": ice_only["ice_max"],
                "caiso_daily_min": ice_only["ice_min"],
                "caiso_daily_std": ice_only["ice_std"],
                "caiso_daily_negative_hours": 0,  # bilateral trades don't go negative
                "caiso_daily_spikes_gt100": (ice_only["ice_max"] > 100).astype(int),
                "caiso_daily_spikes_gt200": (ice_only["ice_max"] > 200).astype(int),
                "caiso_daily_congestion_avg": np.nan,
                "caiso_daily_energy_avg": np.nan,
                "caiso_daily_loss_avg": np.nan,
                "n_obs": ice_only["n_trades"],
                "caiso_daily_negative_pct": 0.0,
                "source": "ice_bilateral",
            })
            monthly = pd.concat([dam_monthly, ice_as_dam], ignore_index=True)
            print(f"    ICE bilateral fills {len(ice_only)} additional months "
                  f"not covered by DAM data")
        else:
            monthly = dam_monthly
            print(f"    ICE bilateral: all months already covered by DAM data")
    elif len(dam_monthly) > 0:
        monthly = dam_monthly
    else:
        # Only ICE bilateral available
        monthly = pd.DataFrame({
            "year": ice_monthly["year"],
            "month": ice_monthly["month"],
            "caiso_daily_mean": ice_monthly["ice_vwap"],
            "caiso_daily_median": ice_monthly["ice_vwap"],
            "caiso_daily_max": ice_monthly["ice_max"],
            "caiso_daily_min": ice_monthly["ice_min"],
            "caiso_daily_std": ice_monthly["ice_std"],
            "caiso_daily_negative_hours": 0,
            "caiso_daily_spikes_gt100": (ice_monthly["ice_max"] > 100).astype(int),
            "caiso_daily_spikes_gt200": (ice_monthly["ice_max"] > 200).astype(int),
            "caiso_daily_congestion_avg": np.nan,
            "caiso_daily_energy_avg": np.nan,
            "caiso_daily_loss_avg": np.nan,
            "n_obs": ice_monthly["n_trades"],
            "caiso_daily_negative_pct": 0.0,
            "source": "ice_bilateral",
        })

    monthly = monthly.sort_values(["year", "month"]).reset_index(drop=True)
    src_counts = monthly["source"].value_counts().to_dict() if "source" in monthly.columns else {}
    src_str = ", ".join(f"{k}: {v}" for k, v in src_counts.items())
    print(f"    Final: {len(monthly)} months ({src_str})")

    return monthly


def _parse_caiso_curtailment(curtail_dir):
    """Parse CAISO curtailment data to monthly GWh.

    Prefers the pre-processed CSV (caiso_monthly_curtailment_2014_2025.csv)
    which has columns: Year, Month, Wind Curtailment (MWh), Solar Curtailment (MWh),
    Total Curtailment (MWh). Falls back to parsing raw Excel files if CSV absent.
    """
    # Prefer pre-processed CSV
    if os.path.exists(CAISO_CURTAIL_CSV):
        df = pd.read_csv(CAISO_CURTAIL_CSV)
        result = pd.DataFrame({
            "year": df["Year"].astype(int),
            "month": df["Month"].astype(int),
            "caiso_wind_curtail_gwh": (df["Wind Curtailment (MWh)"] / 1000).round(2),
            "caiso_solar_curtail_gwh": (df["Solar Curtailment (MWh)"] / 1000).round(2),
            "caiso_total_curtail_gwh": (df["Total Curtailment (MWh)"] / 1000).round(2),
        })
        print(f"  CAISO curtailment CSV → monthly: {len(result)} months "
              f"({result['year'].min()}-{result['year'].max()})")
        return result

    # Fallback: parse Excel files
    import glob as glob_mod
    patterns = [
        os.path.join(curtail_dir, "caiso-productionandcurtailmentsdata*.xlsx"),
        os.path.join(curtail_dir, "caiso-production-and-curtailments-data*.xlsx"),
    ]
    files = []
    for pat in patterns:
        files.extend(glob_mod.glob(pat))
    files = sorted(set(files))

    if not files:
        print("    No CAISO curtailment data found (CSV or Excel)")
        return pd.DataFrame()

    all_months = []
    for fpath in files:
        try:
            df = pd.read_excel(fpath, sheet_name="Curtailments")
        except Exception as e:
            print(f"    SKIP {os.path.basename(fpath)}: {e}")
            continue

        if "Date" not in df.columns:
            continue

        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"])
        df["year"] = df["Date"].dt.year
        df["month"] = df["Date"].dt.month

        wind_col = "Wind Curtailment" if "Wind Curtailment" in df.columns else None
        solar_col = "Solar Curtailment" if "Solar Curtailment" in df.columns else None

        for _, row_data in df.groupby(["year", "month"]):
            yr = row_data["year"].iloc[0]
            mo = row_data["month"].iloc[0]
            wind_mwh = row_data[wind_col].sum() * (5.0 / 60.0) if wind_col else 0.0
            solar_mwh = row_data[solar_col].sum() * (5.0 / 60.0) if solar_col else 0.0
            total_mwh = wind_mwh + solar_mwh
            n_intervals = len(row_data)
            all_months.append({
                "year": int(yr), "month": int(mo),
                "caiso_wind_curtail_gwh": round(wind_mwh / 1000, 2),
                "caiso_solar_curtail_gwh": round(solar_mwh / 1000, 2),
                "caiso_total_curtail_gwh": round(total_mwh / 1000, 2),
                "caiso_curtail_intervals": n_intervals,
            })

    if not all_months:
        return pd.DataFrame()

    result = pd.DataFrame(all_months)
    result = result.groupby(["year", "month"]).agg(
        caiso_wind_curtail_gwh=("caiso_wind_curtail_gwh", "max"),
        caiso_solar_curtail_gwh=("caiso_solar_curtail_gwh", "max"),
        caiso_total_curtail_gwh=("caiso_total_curtail_gwh", "max"),
        caiso_curtail_intervals=("caiso_curtail_intervals", "max"),
    ).reset_index()

    print(f"  CAISO curtailment Excel → monthly: {len(result)} months "
          f"({result['year'].min()}-{result['year'].max()}), "
          f"{len(files)} files parsed")
    return result


def _extract_uri_event_data(master):
    """Extract and save all Feb 2021 (Winter Storm Uri) data across all markets.

    Uri is excluded from ALL regression samples because it distorted the entire
    energy complex: ERCOT electricity ($9,000+/MWh), Henry Hub gas ($3→$23+/MMBtu),
    and CAISO through gas price transmission. The extracted data is saved separately
    for volatility/stress-test analysis.
    """
    uri_mask = (master["year"] == 2021) & (master["month"] == 2)
    if uri_mask.sum() == 0:
        return {}

    uri_row = master[uri_mask].iloc[0]

    # Collect all market-related columns
    ercot_cols = [c for c in master.columns if any(
        c.startswith(p) for p in ["ercot_", "tx_"]
    )]
    caiso_cols = [c for c in master.columns if any(
        c.startswith(p) for p in ["caiso_", "ca_"]
    )]
    hh_cols = [c for c in master.columns if any(
        c.startswith(p) for p in ["henry_hub", "electric_power_bcfd",
                                   "us_gdp", "us_industrial", "us_data_center"]
    )]
    price_cols = ["ercot_wholesale_mwh", "ercot_rtm_mean", "ercot_rtm_max",
                  "ercot_rtm_p90", "ercot_rtm_p95", "ercot_rtm_std",
                  "ercot_rtm_cv", "ercot_price_range"]
    scarcity_cols = ["ercot_spike_count", "ercot_spikes_gt200", "ercot_spikes_gt500",
                     "ercot_extreme_spikes", "ercot_spikes_gt5000",
                     "ercot_scarcity_intensity", "ercot_negative_hours"]
    gen_cols = ["tx_gas_gen_pct", "tx_wind_gen_pct", "tx_coal_gen_pct",
                "tx_solar_gen_pct"]
    demand_cols = ["tx_ercot_demand_twh", "tx_data_center_twh", "henry_hub_spot"]
    gas_cols = ["henry_hub_spot", "electric_power_bcfd"]
    caiso_price_cols = ["caiso_wholesale_mwh", "caiso_dam_mean",
                        "caiso_monthly_negative_hours", "caiso_monthly_congestion_avg"]

    uri_data = {
        "event": "Winter Storm Uri",
        "period": "2021-02-01 to 2021-02-28",
        "description": (
            "Winter Storm Uri caused catastrophic grid failure in ERCOT and "
            "disrupted the entire US energy complex. ERCOT prices hit $9,000+/MWh "
            "cap for ~5 consecutive days. Henry Hub gas spiked from ~$3 to $23+/MMBtu. "
            "CAISO was affected through gas price transmission. This observation is "
            "excluded from ALL market regressions (ERCOT, CAISO, HH) because it "
            "represents a systemic event, not normal price formation. "
            "Saved here for volatility/stress-test use."
        ),
        "exclusion_reason": "Systemic event distorting all markets; Cook's D > 500 for ERCOT",
        "use_for": "Monte Carlo tail risk, GARCH extreme event, stress-test scenarios",
    }

    # Extract all available values by category
    for col_group_name, col_list in [
        ("ercot_prices", price_cols),
        ("ercot_scarcity_metrics", scarcity_cols),
        ("ercot_generation_mix", gen_cols),
        ("demand_drivers", demand_cols),
        ("gas_market", gas_cols),
        ("caiso_prices", caiso_price_cols),
    ]:
        group = {}
        for c in col_list:
            if c in master.columns and pd.notna(uri_row.get(c)):
                group[c] = round(float(uri_row[c]), 4)
        uri_data[col_group_name] = group

    # Extract all ERCOT, CAISO, and HH variables
    for label, col_set in [("all_ercot_variables", ercot_cols),
                            ("all_caiso_variables", caiso_cols),
                            ("all_hh_variables", hh_cols)]:
        all_vars = {}
        for c in sorted(col_set):
            if c in master.columns and pd.notna(uri_row.get(c)):
                try:
                    all_vars[c] = round(float(uri_row[c]), 4)
                except (ValueError, TypeError):
                    all_vars[c] = str(uri_row[c])
        uri_data[label] = all_vars

    # Context: compare to surrounding months
    context_mask = (master["year"] == 2021) & (master["month"].isin([1, 3]))
    context_rows = master[context_mask]
    context = {}
    for _, row in context_rows.iterrows():
        mo = int(row["month"])
        mo_data = {}
        for c in price_cols + scarcity_cols + gas_cols + caiso_price_cols:
            if c in master.columns and pd.notna(row.get(c)):
                mo_data[c] = round(float(row[c]), 4)
        context[f"2021-{mo:02d}"] = mo_data
    uri_data["surrounding_months"] = context

    # Daily breakdown from ERCOT RTM file
    if os.path.exists(ERCOT_RTM_DAILY):
        daily = pd.read_csv(ERCOT_RTM_DAILY)
        daily["date"] = pd.to_datetime(daily["date"])
        houston = daily[daily["Location"] == "HB_HOUSTON"]
        uri_daily = houston[(houston["date"] >= "2021-02-01") &
                            (houston["date"] <= "2021-02-28")]
        daily_records = []
        for _, drow in uri_daily.iterrows():
            rec = {"date": drow["date"].strftime("%Y-%m-%d")}
            for c in ["price_mean", "price_max", "price_min", "price_std",
                       "hours_gt_100", "hours_gt_200", "hours_gt_500",
                       "hours_gt_1000", "hours_gt_5000", "hours_negative"]:
                if c in drow.index and pd.notna(drow[c]):
                    rec[c] = round(float(drow[c]), 2)
            daily_records.append(rec)
        uri_data["daily_breakdown"] = daily_records

    with open(URI_EVENT_DATA, "w") as f:
        json.dump(uri_data, f, indent=2)
    print(f"  Saved Uri event data: {URI_EVENT_DATA}")
    return uri_data


def _exclude_uri(df):
    """Exclude Feb 2021 (Winter Storm Uri) from ERCOT regression samples.

    Returns filtered DataFrame and count of excluded rows.
    """
    if "year" in df.columns and "month" in df.columns:
        mask = ~((df["year"] == 2021) & (df["month"] == 2))
        n_excluded = (~mask).sum()
        return df[mask].copy(), n_excluded
    return df.copy(), 0


# ============================================================================
# STEP 1B: ENHANCE MASTER WITH NEW CONTROLS
# ============================================================================

def step1b_enhance_master(master):
    """Tier 1B-1I: Integrate new control variables, compute lags, diffs, interactions, dummies."""
    banner("STEP 1B: Enhance master dataset with new controls (Tier 1)")

    master = master.copy()
    if "month" not in master.columns and "date" in master.columns:
        master["month"] = pd.to_datetime(master["date"]).dt.month

    n_cols_start = len(master.columns)

    # ------------------------------------------------------------------
    # 1B. Integrate monthly HDD/CDD data (EIA SEDS format)
    # ------------------------------------------------------------------
    print("\n  --- 1B. Monthly Heating/Cooling Degree Days (EIA) ---")
    # MSN codes: ZWHDPUS=US HDD, ZWHDPC7=West South Central (TX), ZWHDPC9=Pacific (CA)
    #            ZWCDPUS=US CDD, ZWCDPC7=West South Central (TX), ZWCDPC9=Pacific (CA)
    hdd_cdd_msn_map = {
        "ZWHDPUS": "us_hdd", "ZWHDPC7": "tx_hdd", "ZWHDPC9": "ca_hdd",
        "ZWCDPUS": "us_cdd", "ZWCDPC7": "tx_cdd", "ZWCDPC9": "ca_cdd",
    }
    for fpath, dd_label in [(HDD_MONTHLY_CSV, "HDD"), (CDD_MONTHLY_CSV, "CDD")]:
        if not os.path.exists(fpath):
            print(f"    SKIPPED: {os.path.basename(fpath)} not found")
            continue
        raw = pd.read_csv(fpath)
        for msn_code, col_name in hdd_cdd_msn_map.items():
            # HDD file has ZWHD* codes, CDD file has ZWCD* codes
            if dd_label == "HDD" and not msn_code.startswith("ZWHD"):
                continue
            if dd_label == "CDD" and not msn_code.startswith("ZWCD"):
                continue
            subset = raw[raw["MSN"] == msn_code].copy()
            if len(subset) == 0:
                print(f"    {col_name}: no data for MSN={msn_code}")
                continue
            subset["YYYYMM"] = subset["YYYYMM"].astype(str)
            # Keep only monthly rows (MM=01-12, not 13 which is annual total)
            subset = subset[~subset["YYYYMM"].str.endswith("13")]
            subset["year"] = subset["YYYYMM"].str[:4].astype(int)
            subset["month"] = subset["YYYYMM"].str[4:6].astype(int)
            subset[col_name] = pd.to_numeric(subset["Value"], errors="coerce")
            merge_df = subset[["year", "month", col_name]].drop_duplicates(subset=["year", "month"])
            if col_name in master.columns:
                master = master.drop(columns=[col_name])
            master = master.merge(merge_df, on=["year", "month"], how="left")
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} monthly observations merged")

    # ------------------------------------------------------------------
    # 1B. HDD/CDD Departure from Normal (monthly)
    # ------------------------------------------------------------------
    print("\n  --- 1B. HDD/CDD Departure from Normal ---")
    # Compute long-term monthly mean from the data itself, then departure_pct
    # departure_pct = (actual - normal) / normal * 100
    # Positive = hotter/colder than normal → drives gas prices
    for col, dep_col in [("us_hdd", "us_hdd_departure_pct"), ("us_cdd", "us_cdd_departure_pct"),
                         ("tx_hdd", "tx_hdd_departure_pct"), ("tx_cdd", "tx_cdd_departure_pct"),
                         ("ca_hdd", "ca_hdd_departure_pct"), ("ca_cdd", "ca_cdd_departure_pct")]:
        if col in master.columns and master[col].notna().sum() > 60:
            month_normals = master.groupby("month")[col].mean()
            master[dep_col] = master.apply(
                lambda row: ((row[col] - month_normals.get(row["month"], 0)) /
                             month_normals.get(row["month"], 1) * 100)
                if pd.notna(row[col]) and month_normals.get(row["month"], 0) != 0
                else np.nan, axis=1)
            n_valid = master[dep_col].notna().sum()
            print(f"    {dep_col}: {n_valid} months computed (mean={master[dep_col].mean():.1f}%, "
                  f"std={master[dep_col].std():.1f}%)")
        else:
            print(f"    {dep_col}: SKIPPED ({col} not available or <60 obs)")

    # ------------------------------------------------------------------
    # 1B. Integrate Natural Gas Storage (weekly → monthly)
    # ------------------------------------------------------------------
    print("\n  --- 1B. Natural Gas Storage ---")
    if os.path.exists(GAS_STORAGE_CSV):
        stor_raw = pd.read_csv(GAS_STORAGE_CSV)
        stor_raw["date"] = pd.to_datetime(stor_raw["date"])
        stor_raw["year"] = stor_raw["date"].dt.year
        stor_raw["month"] = stor_raw["date"].dt.month
        # Use end-of-month (last observation per month)
        stor_monthly = stor_raw.groupby(["year", "month"]).last().reset_index()
        # storage_vs_5yr_avg_bcf: deviation from 5-year average (Bcf)
        # Compute percentage: vs_5yr_pct = deviation / (5yr_avg) * 100
        # 5yr_avg = storage_bcf - storage_vs_5yr_avg_bcf
        stor_monthly["_5yr_avg"] = stor_monthly["storage_bcf"] - stor_monthly["storage_vs_5yr_avg_bcf"]
        stor_monthly["us_ng_storage_vs_5yr_pct"] = np.where(
            stor_monthly["_5yr_avg"].abs() > 10,  # avoid div by zero
            stor_monthly["storage_vs_5yr_avg_bcf"] / stor_monthly["_5yr_avg"] * 100,
            np.nan)
        # Also keep absolute Bcf for diagnostics
        stor_monthly["us_ng_storage_bcf"] = stor_monthly["storage_bcf"]
        stor_monthly["us_ng_storage_vs_5yr_bcf"] = stor_monthly["storage_vs_5yr_avg_bcf"]
        for col in ["us_ng_storage_bcf", "us_ng_storage_vs_5yr_bcf", "us_ng_storage_vs_5yr_pct"]:
            merge_df = stor_monthly[["year", "month", col]].drop_duplicates(subset=["year", "month"])
            if col in master.columns:
                master = master.drop(columns=[col])
            master = master.merge(merge_df, on=["year", "month"], how="left")
            n_valid = master[col].notna().sum()
            print(f"    {col}: {n_valid} monthly observations merged")
    else:
        print("    SKIPPED: Gas storage file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Reserve Margins
    # ------------------------------------------------------------------
    print("\n  --- 1B. Reserve Margins ---")
    if os.path.exists(RESERVE_MARGINS_CSV):
        rm_raw = pd.read_csv(RESERVE_MARGINS_CSV)
        rm_raw["year"] = rm_raw["year"].astype(int)

        for region, prefix in [("US_National", "us"), ("ERCOT", "ercot"), ("CAISO", "caiso")]:
            reg_data = rm_raw[rm_raw["region"] == region]
            for var in ["planning_reserve_margin_pct", "actual_reserve_margin_pct", "peak_demand_gw"]:
                if var not in reg_data.columns:
                    continue
                yr_val = dict(zip(reg_data["year"], pd.to_numeric(reg_data[var], errors="coerce")))
                interpolated, warnings = _interpolate_annual(yr_val)
                col_name = f"{prefix}_{var}"
                master[col_name] = master["year"].map(interpolated)
                n_valid = master[col_name].notna().sum()
                print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
                for w in warnings:
                    print(f"      {w}")
    else:
        print("    SKIPPED: Reserve margins file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Congestion Costs
    # ------------------------------------------------------------------
    print("\n  --- 1B. Transmission Congestion ---")
    if os.path.exists(CONGESTION_CSV):
        cong_raw = pd.read_csv(CONGESTION_CSV)
        cong_raw["year"] = cong_raw["year"].astype(int)

        for region, prefix in [("ERCOT", "ercot"), ("CAISO", "caiso")]:
            reg_data = cong_raw[cong_raw["region"] == region]
            for var in ["congestion_cost_million", "congestion_per_mwh"]:
                if var not in reg_data.columns:
                    continue
                yr_val = dict(zip(reg_data["year"], pd.to_numeric(reg_data[var], errors="coerce")))
                yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
                interpolated, warnings = _interpolate_annual(yr_val)
                col_name = f"{prefix}_{var}"
                master[col_name] = master["year"].map(interpolated)
                n_valid = master[col_name].notna().sum()
                print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
                for w in warnings:
                    print(f"      {w}")
    else:
        print("    SKIPPED: Congestion file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Curtailment
    # ------------------------------------------------------------------
    print("\n  --- 1B. Curtailment ---")
    if os.path.exists(CURTAILMENT_CSV):
        curt_raw = pd.read_csv(CURTAILMENT_CSV)
        curt_raw["year"] = pd.to_numeric(curt_raw["year"], errors="coerce")
        curt_raw = curt_raw.dropna(subset=["year"])
        curt_raw["year"] = curt_raw["year"].astype(int)

        for region, prefix in [("ERCOT", "ercot"), ("CAISO", "caiso"), ("US", "us")]:
            reg_data = curt_raw[curt_raw["region"] == region]
            for var in ["total_curtailment_pct", "wind_curtailment_pct", "solar_curtailment_pct"]:
                if var not in reg_data.columns:
                    continue
                yr_val = dict(zip(reg_data["year"], pd.to_numeric(reg_data[var], errors="coerce")))
                yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
                interpolated, warnings = _interpolate_annual(yr_val)
                col_name = f"{prefix}_{var}"
                master[col_name] = master["year"].map(interpolated)
                n_valid = master[col_name].notna().sum()
                print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
                for w in warnings:
                    print(f"      {w}")
    else:
        print("    SKIPPED: Curtailment file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Scarcity Pricing (ERCOT ORDC data)
    # ------------------------------------------------------------------
    print("\n  --- 1B. Scarcity Pricing ---")
    if os.path.exists(SCARCITY_CSV):
        scar_raw = pd.read_csv(SCARCITY_CSV)
        scar_raw["year"] = scar_raw["year"].astype(int)
        ercot_scar = scar_raw[scar_raw["region"] == "ERCOT"]
        for var in ["scarcity_hours", "scarcity_revenue_million", "ordc_adder_max_mwh",
                     "price_cap_mwh", "eea_events"]:
            if var not in ercot_scar.columns:
                continue
            yr_val = dict(zip(ercot_scar["year"], pd.to_numeric(ercot_scar[var], errors="coerce")))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            interpolated, warnings = _interpolate_annual(yr_val)
            col_name = f"ercot_{var}"
            master[col_name] = master["year"].map(interpolated)
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
            for w in warnings:
                print(f"      {w}")
        # CAISO scarcity
        caiso_scar = scar_raw[scar_raw["region"] == "CAISO"]
        for var in ["scarcity_hours", "eea_events"]:
            if var not in caiso_scar.columns:
                continue
            yr_val = dict(zip(caiso_scar["year"], pd.to_numeric(caiso_scar[var], errors="coerce")))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            interpolated, warnings = _interpolate_annual(yr_val)
            col_name = f"caiso_{var}"
            master[col_name] = master["year"].map(interpolated)
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
    else:
        print("    SKIPPED: Scarcity pricing file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Negative Pricing Hours (CAISO — ERCOT already from RTM daily)
    # ------------------------------------------------------------------
    print("\n  --- 1B. Negative Pricing Hours ---")
    if os.path.exists(NEGATIVE_HOURS_CSV):
        neg_raw = pd.read_csv(NEGATIVE_HOURS_CSV)
        neg_raw["year"] = neg_raw["year"].astype(int)
        # CAISO system average negative hours (ERCOT already has ercot_negative_hours from RTM daily)
        caiso_neg = neg_raw[(neg_raw["region"] == "CAISO") & (neg_raw["hub_zone"] == "System Avg")]
        if len(caiso_neg) > 0:
            yr_val = dict(zip(caiso_neg["year"], pd.to_numeric(caiso_neg["negative_hours"], errors="coerce")))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            interpolated, warnings = _interpolate_annual(yr_val)
            master["caiso_negative_hours"] = master["year"].map(interpolated)
            n_valid = master["caiso_negative_hours"].notna().sum()
            print(f"    caiso_negative_hours: {n_valid} months populated ({len(yr_val)} annual points)")
        # ERCOT system-wide (annual granularity — supplement RTM daily if missing)
        ercot_neg = neg_raw[(neg_raw["region"] == "ERCOT") & (neg_raw["hub_zone"] == "System-wide")]
        if len(ercot_neg) > 0:
            yr_val = dict(zip(ercot_neg["year"], pd.to_numeric(ercot_neg["negative_hours"], errors="coerce")))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            interpolated, warnings = _interpolate_annual(yr_val)
            col_name = "ercot_negative_hours_annual"
            master[col_name] = master["year"].map(interpolated)
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
    else:
        print("    SKIPPED: Negative pricing hours file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Battery Cost Decline
    # ------------------------------------------------------------------
    print("\n  --- 1B. Battery Cost Decline ---")
    if os.path.exists(BATTERY_COST_CSV):
        batt_raw = pd.read_csv(BATTERY_COST_CSV)
        # Use Li-ion aggregate data (most years available)
        batt_lion = batt_raw[batt_raw["battery_type"] == "Li-ion"]
        for var in ["pack_price_kwh", "stationary_price_kwh"]:
            if var not in batt_lion.columns:
                continue
            yr_val = dict(zip(batt_lion["year"], pd.to_numeric(batt_lion[var], errors="coerce")))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            interpolated, warnings = _interpolate_annual(yr_val)
            col_name = f"battery_{var}"
            master[col_name] = master["year"].map(interpolated)
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
            for w in warnings:
                print(f"      {w}")
    else:
        print("    SKIPPED: Battery cost file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Battery Storage Capacity (GW by region)
    # ------------------------------------------------------------------
    print("\n  --- 1B. Battery Storage Capacity ---")
    if os.path.exists(BATTERY_CAPACITY_CSV):
        batt_cap_raw = pd.read_csv(BATTERY_CAPACITY_CSV)
        batt_cap_raw["year"] = batt_cap_raw["year"].astype(int)
        for region, col_name in [("US", "us_battery_capacity_gw"),
                                  ("Texas", "tx_battery_capacity_gw"),
                                  ("California", "ca_battery_capacity_gw")]:
            reg_data = batt_cap_raw[batt_cap_raw["region"] == region]
            if len(reg_data) == 0:
                print(f"    {col_name}: no data for region '{region}'")
                continue
            # Convert cumulative MW to GW
            yr_val = dict(zip(reg_data["year"],
                              pd.to_numeric(reg_data["cumulative_capacity_mw"], errors="coerce") / 1000.0))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            interpolated, warnings = _interpolate_annual(yr_val)
            master[col_name] = master["year"].map(interpolated)
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
            for w in warnings:
                print(f"      {w}")
    else:
        print("    SKIPPED: Battery capacity file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate LNG Utilization & US LNG Exports
    # ------------------------------------------------------------------
    print("\n  --- 1B. LNG Utilization & Exports ---")
    if os.path.exists(LNG_CURVE):
        with open(LNG_CURVE) as f:
            lng_data = json.load(f)
        # LNG utilization %
        annual_bal = lng_data.get("annual_balance", {})
        if annual_bal:
            yr_util = {}
            for yr_str, bal in annual_bal.items():
                yr = int(yr_str)
                if yr <= 2025:  # only observed years, not projections
                    util = bal.get("utilization_pct")
                    if util is not None:
                        yr_util[yr] = float(util)
            if yr_util:
                interpolated, warnings = _interpolate_annual(yr_util)
                master["lng_utilization_pct"] = master["year"].map(interpolated)
                n_valid = master["lng_utilization_pct"].notna().sum()
                print(f"    lng_utilization_pct: {n_valid} months populated ({len(yr_util)} annual points)")
                for w in warnings:
                    print(f"      {w}")
        # US LNG exports (Bcf/d)
        lng_exports = lng_data.get("us_lng_exports", {})
        if lng_exports:
            yr_exp = {}
            for yr_str, exp in lng_exports.items():
                yr = int(yr_str)
                if yr <= 2025:
                    bcfd = exp.get("exports_bcfd")
                    if bcfd is not None:
                        yr_exp[yr] = float(bcfd)
            if yr_exp:
                interpolated, warnings = _interpolate_annual(yr_exp)
                master["us_lng_exports_bcfd"] = master["year"].map(interpolated)
                n_valid = master["us_lng_exports_bcfd"].notna().sum()
                print(f"    us_lng_exports_bcfd: {n_valid} months populated ({len(yr_exp)} annual points)")
                for w in warnings:
                    print(f"      {w}")
    else:
        print("    SKIPPED: LNG capacity curve file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate EV Charging Grid Impact
    # ------------------------------------------------------------------
    print("\n  --- 1B. EV Charging Grid Impact ---")
    if os.path.exists(EV_CHARGING_CSV):
        ev_raw = pd.read_csv(EV_CHARGING_CSV)
        # US national data (most complete)
        ev_us = ev_raw[ev_raw["region"] == "US"]
        for var in ["annual_charging_twh", "peak_demand_increase_gw"]:
            if var not in ev_us.columns:
                continue
            yr_val = dict(zip(ev_us["year"], pd.to_numeric(ev_us[var], errors="coerce")))
            yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
            # Exclude projections beyond 2025
            yr_val = {k: v for k, v in yr_val.items() if k <= 2025}
            interpolated, warnings = _interpolate_annual(yr_val)
            col_name = f"us_ev_{var}"
            master[col_name] = master["year"].map(interpolated)
            n_valid = master[col_name].notna().sum()
            print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
        # TX and CA specific
        for state, prefix in [("Texas", "tx"), ("California", "ca")]:
            ev_st = ev_raw[ev_raw["region"] == state]
            if len(ev_st) > 0:
                for var in ["annual_charging_twh", "peak_demand_increase_gw"]:
                    if var not in ev_st.columns:
                        continue
                    yr_val = dict(zip(ev_st["year"], pd.to_numeric(ev_st[var], errors="coerce")))
                    yr_val = {k: v for k, v in yr_val.items() if pd.notna(v) and k <= 2025}
                    interpolated, warnings = _interpolate_annual(yr_val)
                    col_name = f"{prefix}_ev_{var}"
                    master[col_name] = master["year"].map(interpolated)
                    n_valid = master[col_name].notna().sum()
                    print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
    else:
        print("    SKIPPED: EV charging file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Renewable Variability (monthly capacity factors & CV)
    # ------------------------------------------------------------------
    print("\n  --- 1B. Renewable Variability ---")
    if os.path.exists(VARIABILITY_CSV):
        var_raw = pd.read_csv(VARIABILITY_CSV)
        # Wind variability CV by region and year
        for region, prefix in [("US", "us"), ("Texas", "tx"), ("California", "ca")]:
            for metric in ["wind_capacity_factor_pct", "solar_capacity_factor_pct"]:
                reg_data = var_raw[(var_raw["region"] == region) &
                                   (var_raw["metric_type"] == metric)]
                if len(reg_data) == 0:
                    continue
                # variability_cv: coefficient of variation across months within each year
                for var in ["variability_cv", "annual_avg"]:
                    if var not in reg_data.columns:
                        continue
                    yr_val = dict(zip(reg_data["year"], pd.to_numeric(reg_data[var], errors="coerce")))
                    yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
                    interpolated, warnings = _interpolate_annual(yr_val)
                    short_metric = "wind_cf" if "wind" in metric else "solar_cf"
                    col_name = f"{prefix}_{short_metric}_{var}"
                    master[col_name] = master["year"].map(interpolated)
                    n_valid = master[col_name].notna().sum()
                    print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
        # Curtailment GWh by ISO (monthly data if available)
        for iso in ["ERCOT", "CAISO"]:
            iso_data = var_raw[var_raw["region"] == iso]
            if len(iso_data) > 0 and "metric_type" in iso_data.columns:
                curt_data = iso_data[iso_data["metric_type"] == "curtailment_gwh"]
                if len(curt_data) > 0:
                    # Monthly curtailment columns: jan-dec; build monthly merge
                    month_cols = ["jan", "feb", "mar", "apr", "may", "jun",
                                  "jul", "aug", "sep", "oct", "nov", "dec"]
                    curt_monthly_rows = []
                    for _, row in curt_data.iterrows():
                        yr = int(row["year"])
                        for m_idx, m_col in enumerate(month_cols, 1):
                            if m_col in row.index and pd.notna(row[m_col]):
                                curt_monthly_rows.append({
                                    "year": yr, "month": m_idx,
                                    f"{iso.lower()}_curtailment_gwh": float(row[m_col])
                                })
                    if curt_monthly_rows:
                        curt_df = pd.DataFrame(curt_monthly_rows)
                        col_name = f"{iso.lower()}_curtailment_gwh"
                        if col_name in master.columns:
                            master = master.drop(columns=[col_name])
                        master = master.merge(curt_df, on=["year", "month"], how="left")
                        n_valid = master[col_name].notna().sum()
                        print(f"    {col_name}: {n_valid} monthly observations")
    else:
        print("    SKIPPED: Variability file not found")

    # ------------------------------------------------------------------
    # 1B. Integrate Ancillary Services Pricing
    # ------------------------------------------------------------------
    print("\n  --- 1B. Ancillary Services Pricing ---")
    if os.path.exists(ANCILLARY_CSV):
        anc_raw = pd.read_csv(ANCILLARY_CSV)
        anc_raw["year"] = anc_raw["year"].astype(int)
        # ERCOT regulation up and responsive reserve (key scarcity indicators)
        for region, prefix in [("ERCOT", "ercot"), ("CAISO", "caiso")]:
            for svc_type in ["Regulation_Up", "Responsive_Reserve", "Spinning_Reserve"]:
                svc_data = anc_raw[(anc_raw["region"] == region) &
                                   (anc_raw["service_type"] == svc_type)]
                if len(svc_data) == 0:
                    continue
                for var in ["avg_price_mwh"]:
                    yr_val = dict(zip(svc_data["year"], pd.to_numeric(svc_data[var], errors="coerce")))
                    yr_val = {k: v for k, v in yr_val.items() if pd.notna(v)}
                    interpolated, warnings = _interpolate_annual(yr_val)
                    svc_short = svc_type.lower().replace("_", "")
                    col_name = f"{prefix}_{svc_short}_price"
                    master[col_name] = master["year"].map(interpolated)
                    n_valid = master[col_name].notna().sum()
                    print(f"    {col_name}: {n_valid} months populated ({len(yr_val)} annual points)")
    else:
        print("    SKIPPED: Ancillary services file not found")

    # ------------------------------------------------------------------
    # 1I. ERCOT RTM daily data → monthly features
    # ------------------------------------------------------------------
    print("\n  --- 1I. ERCOT RTM Daily → Monthly ---")
    if os.path.exists(ERCOT_RTM_DAILY):
        ercot_monthly = _aggregate_ercot_daily_to_monthly(ERCOT_RTM_DAILY)
        # Merge on (year, month)
        n_before = len(master.columns)
        rtm_cols = [c for c in ercot_monthly.columns if c not in ("year", "month")]
        master = master.merge(ercot_monthly, on=["year", "month"], how="left")
        print(f"    Added {len(master.columns) - n_before} ERCOT RTM columns")

        # Cross-validate against existing ercot_wholesale_mwh
        if "ercot_wholesale_mwh" in master.columns and "ercot_rtm_mean" in master.columns:
            both = master[["ercot_wholesale_mwh", "ercot_rtm_mean"]].dropna()
            if len(both) > 0:
                corr = both["ercot_wholesale_mwh"].corr(both["ercot_rtm_mean"])
                pct_diff = ((both["ercot_rtm_mean"] - both["ercot_wholesale_mwh"]).abs() /
                            both["ercot_wholesale_mwh"].clip(lower=1)).mean() * 100
                print(f"    Cross-check: ercot_rtm_mean vs ercot_wholesale_mwh "
                      f"(n={len(both)}, corr={corr:.3f}, avg_pct_diff={pct_diff:.1f}%)")
                if pct_diff > 5:
                    print(f"    WARNING: >5% discrepancy between RTM mean and EIA wholesale")
    else:
        print("    SKIPPED: ERCOT RTM daily file not found")

    # ------------------------------------------------------------------
    # 1J. CAISO DAM hourly data → monthly features
    # ------------------------------------------------------------------
    print("\n  --- 1J. CAISO DAM Hourly → Monthly ---")
    if os.path.exists(CAISO_DAM_HOURLY):
        caiso_monthly = _aggregate_caiso_dam_to_monthly(CAISO_DAM_HOURLY)
        n_before = len(master.columns)
        dam_cols = [c for c in caiso_monthly.columns if c not in ("year", "month")]
        master = master.merge(caiso_monthly, on=["year", "month"], how="left")
        print(f"    Added {len(master.columns) - n_before} CAISO DAM columns")

        # Cross-validate against existing caiso_wholesale_mwh
        if "caiso_wholesale_mwh" in master.columns and "caiso_dam_mean" in master.columns:
            both = master[["caiso_wholesale_mwh", "caiso_dam_mean"]].dropna()
            if len(both) > 0:
                corr = both["caiso_wholesale_mwh"].corr(both["caiso_dam_mean"])
                pct_diff = ((both["caiso_dam_mean"] - both["caiso_wholesale_mwh"]).abs() /
                            both["caiso_wholesale_mwh"].clip(lower=1)).mean() * 100
                print(f"    Cross-check: caiso_dam_mean vs caiso_wholesale_mwh "
                      f"(n={len(both)}, corr={corr:.3f}, avg_pct_diff={pct_diff:.1f}%)")
    else:
        print("    SKIPPED: CAISO DAM hourly file not found")

    # ------------------------------------------------------------------
    # 1J2. CAISO Daily Data (multi-source) → fill caiso_wholesale_mwh gaps
    # ------------------------------------------------------------------
    print("\n  --- 1J2. CAISO Daily Data → Fill Wholesale Price Gaps ---")
    if os.path.exists(CAISO_DAILY_DIR):
        caiso_daily = _aggregate_caiso_daily_data_to_monthly(CAISO_DAILY_DIR)
        if len(caiso_daily) > 0:
            # Fill gaps in caiso_wholesale_mwh using daily DAM data
            n_filled = 0
            n_validated = 0
            if "caiso_wholesale_mwh" not in master.columns:
                master["caiso_wholesale_mwh"] = np.nan

            for _, row in caiso_daily.iterrows():
                yr, mo = int(row["year"]), int(row["month"])
                mask = (master["year"] == yr) & (master["month"] == mo)
                if mask.sum() == 0:
                    continue
                existing = master.loc[mask, "caiso_wholesale_mwh"].values[0]
                new_val = row["caiso_daily_mean"]
                if pd.isna(existing) and pd.notna(new_val):
                    master.loc[mask, "caiso_wholesale_mwh"] = new_val
                    n_filled += 1
                elif pd.notna(existing) and pd.notna(new_val):
                    n_validated += 1

            n_total = master["caiso_wholesale_mwh"].notna().sum()
            print(f"    Filled {n_filled} missing months, validated {n_validated} existing")
            print(f"    caiso_wholesale_mwh now has {n_total} monthly observations")

            # Also fill caiso_dam_mean and caiso_dam_* features for months
            # where original DAM hourly file didn't have data
            dam_feature_map = {
                "caiso_daily_mean": "caiso_dam_mean",
                "caiso_daily_median": "caiso_dam_median",
                "caiso_daily_max": "caiso_dam_max",
                "caiso_daily_min": "caiso_dam_min",
                "caiso_daily_std": "caiso_dam_std",
                "caiso_daily_negative_hours": "caiso_monthly_negative_hours",
                "caiso_daily_spikes_gt100": "caiso_monthly_spikes_gt100",
                "caiso_daily_spikes_gt200": "caiso_monthly_spikes_gt200",
                "caiso_daily_congestion_avg": "caiso_monthly_congestion_avg",
                "caiso_daily_energy_avg": "caiso_monthly_energy_avg",
                "caiso_daily_loss_avg": "caiso_monthly_loss_avg",
                "caiso_daily_negative_pct": "caiso_negative_pct",
            }
            for src_col, dst_col in dam_feature_map.items():
                if src_col not in caiso_daily.columns:
                    continue
                if dst_col not in master.columns:
                    master[dst_col] = np.nan
                n_feat_filled = 0
                for _, row in caiso_daily.iterrows():
                    yr, mo = int(row["year"]), int(row["month"])
                    mask = (master["year"] == yr) & (master["month"] == mo)
                    if mask.sum() == 0:
                        continue
                    existing = master.loc[mask, dst_col].values[0]
                    new_val = row[src_col]
                    if pd.isna(existing) and pd.notna(new_val):
                        master.loc[mask, dst_col] = new_val
                        n_feat_filled += 1
                if n_feat_filled > 0:
                    print(f"    {dst_col}: filled {n_feat_filled} months from daily data")
    else:
        print("    SKIPPED: CAISO daily data directory not found")

    # ------------------------------------------------------------------
    # 1K. CAISO Curtailment → Monthly GWh (CSV preferred, Excel fallback)
    # ------------------------------------------------------------------
    print("\n  --- 1K. CAISO Curtailment → Monthly ---")
    if os.path.exists(CAISO_CURTAIL_CSV) or os.path.exists(CAISO_CURTAIL_DIR):
        caiso_curtail = _parse_caiso_curtailment(CAISO_CURTAIL_DIR)
        if len(caiso_curtail) > 0:
            n_before = len(master.columns)
            curtail_cols = [c for c in caiso_curtail.columns if c not in ("year", "month")]
            # Drop existing columns if present to avoid _x/_y suffixes
            for cc in curtail_cols:
                if cc in master.columns:
                    master = master.drop(columns=[cc])
            master = master.merge(caiso_curtail, on=["year", "month"], how="left")
            n_new = len(master.columns) - n_before
            print(f"    Added {n_new} CAISO monthly curtailment columns")
            for cc in curtail_cols:
                n_valid = master[cc].notna().sum()
                print(f"    {cc}: {n_valid} monthly observations")
        else:
            print("    No curtailment data extracted from Excel files")
    else:
        print("    SKIPPED: CAISO curtailment directory not found")

    # ------------------------------------------------------------------
    # 1C. Pre-compute lagged controls at candidate lags {1, 3, 6, 12}
    # ------------------------------------------------------------------
    print("\n  --- 1C. Lagged Controls ---")
    lag_candidates = [1, 3, 6, 12]
    lag_vars = []
    # Endogenous market variables that must be lagged
    lag_base_vars = [
        "ercot_congestion_per_mwh", "ercot_congestion_cost_million",
        "caiso_congestion_per_mwh", "caiso_congestion_cost_million",
        "ercot_actual_reserve_margin_pct", "caiso_actual_reserve_margin_pct",
        "us_actual_reserve_margin_pct",
        "ercot_total_curtailment_pct", "caiso_total_curtailment_pct",
        "ercot_spike_count", "ercot_rtm_cv", "ercot_negative_hours",
        "ercot_scarcity_hours", "caiso_negative_hours",
        "ercot_regulationup_price", "ercot_responsivereserve_price",
        # New monthly-frequency variables
        "ercot_scarcity_intensity", "ercot_spikes_gt500",
        "caiso_monthly_negative_hours", "caiso_monthly_congestion_avg",
        "caiso_total_curtail_gwh", "caiso_solar_curtail_gwh",
    ]
    for base_var in lag_base_vars:
        if base_var not in master.columns:
            continue
        for lag in lag_candidates:
            col_name = f"{base_var}_lag{lag}"
            master[col_name] = master[base_var].shift(lag)
            lag_vars.append(col_name)
    print(f"    Created {len(lag_vars)} lagged control variables")

    # ------------------------------------------------------------------
    # 1D. Regional spillover lags
    # ------------------------------------------------------------------
    print("\n  --- 1D. Regional Spillover Lags ---")
    spillover_count = 0
    for price_col, prefix in [("caiso_wholesale_mwh", "caiso_wholesale"),
                               ("ercot_wholesale_mwh", "ercot_wholesale"),
                               ("ercot_rtm_mean", "ercot_rtm_mean")]:
        if price_col not in master.columns:
            continue
        for lag in lag_candidates:
            col_name = f"{prefix}_lag{lag}"
            master[col_name] = master[price_col].shift(lag)
            spillover_count += 1
    print(f"    Created {spillover_count} spillover lag variables")

    # ------------------------------------------------------------------
    # 1E. First-Difference Variables (de-trending)
    # ------------------------------------------------------------------
    print("\n  --- 1E. First-Difference Variables ---")
    diff_pairs = [
        ("us_data_center_twh", "dc_twh_yoy_change"),
        ("tx_data_center_twh", "tx_dc_twh_yoy_change"),
        ("ca_data_center_twh", "ca_dc_twh_yoy_change"),
        ("queue_backlog_gw", "queue_yoy_change"),
    ]
    diff_count = 0
    for src, dst in diff_pairs:
        if src in master.columns:
            master[dst] = master[src].diff()
            diff_count += 1
            n_valid = master[dst].notna().sum()
            print(f"    {dst}: {n_valid} values (lost 1 from differencing)")
    print(f"    Created {diff_count} first-difference variables")

    # ------------------------------------------------------------------
    # 1F. Centered Interaction Terms
    # ------------------------------------------------------------------
    print("\n  --- 1F. Centered Interaction Terms ---")
    interaction_count = 0
    # ERCOT: DC × gas generation share
    if "tx_data_center_twh" in master.columns and "tx_gas_gen_pct" in master.columns:
        dc_c = master["tx_data_center_twh"] - master["tx_data_center_twh"].mean()
        gas_c = master["tx_gas_gen_pct"] - master["tx_gas_gen_pct"].mean()
        master["dc_x_gas_gen_pct"] = dc_c * gas_c
        interaction_count += 1
    # ERCOT: DC × wind share
    if "tx_data_center_twh" in master.columns and "tx_wind_gen_pct" in master.columns:
        dc_c = master["tx_data_center_twh"] - master["tx_data_center_twh"].mean()
        wind_c = master["tx_wind_gen_pct"] - master["tx_wind_gen_pct"].mean()
        master["dc_x_renewable_share"] = dc_c * wind_c
        interaction_count += 1
    # CAISO: DC × solar share
    if "ca_data_center_twh" in master.columns and "ca_solar_gen_pct" in master.columns:
        ca_dc_c = master["ca_data_center_twh"] - master["ca_data_center_twh"].mean()
        ca_solar_c = master["ca_solar_gen_pct"] - master["ca_solar_gen_pct"].mean()
        master["ca_dc_x_solar"] = ca_dc_c * ca_solar_c
        interaction_count += 1
    # Gas price × wind share (captures varying merit-order displacement value)
    if "henry_hub_spot" in master.columns and "tx_wind_gen_pct" in master.columns:
        hh_c = master["henry_hub_spot"] - master["henry_hub_spot"].mean()
        tw_c = master["tx_wind_gen_pct"] - master["tx_wind_gen_pct"].mean()
        master["gas_x_tx_wind"] = hh_c * tw_c
        interaction_count += 1
    # Gas price × CA solar share
    if "henry_hub_spot" in master.columns and "ca_solar_gen_pct" in master.columns:
        hh_c = master["henry_hub_spot"] - master["henry_hub_spot"].mean()
        cs_c = master["ca_solar_gen_pct"] - master["ca_solar_gen_pct"].mean()
        master["gas_x_ca_solar"] = hh_c * cs_c
        interaction_count += 1
    # Gas price × monthly scarcity intensity (separates fuel cost from scarcity premium)
    if "henry_hub_spot" in master.columns and "ercot_scarcity_intensity" in master.columns:
        hh_c = master["henry_hub_spot"] - master["henry_hub_spot"].mean()
        sc_c = master["ercot_scarcity_intensity"].fillna(0) - master["ercot_scarcity_intensity"].fillna(0).mean()
        master["gas_x_ercot_scarcity"] = hh_c * sc_c
        interaction_count += 1
    print(f"    Created {interaction_count} centered interaction terms")

    # ------------------------------------------------------------------
    # 1G. Quadratic Terms (DC + Renewable penetration)
    # ------------------------------------------------------------------
    print("\n  --- 1G. Quadratic Terms ---")
    quad_count = 0
    for src, dst in [("tx_data_center_twh", "tx_dc_squared"),
                     ("ca_data_center_twh", "ca_dc_squared"),
                     ("us_data_center_twh", "us_dc_squared"),
                     ("tx_wind_gen_pct", "tx_wind_gen_pct_sq"),
                     ("ca_solar_gen_pct", "ca_solar_gen_pct_sq"),
                     ("tx_solar_gen_pct", "tx_solar_gen_pct_sq")]:
        if src in master.columns:
            master[dst] = master[src] ** 2
            quad_count += 1
    print(f"    Created {quad_count} quadratic terms")

    # ------------------------------------------------------------------
    # 1H. Structural Break Dummies + Interaction Terms
    # ------------------------------------------------------------------
    print("\n  --- 1H. Structural Break Dummies ---")
    if "date" in master.columns:
        dates = pd.to_datetime(master["date"])
        master["post_uri"] = (dates >= "2021-02-01").astype(int)
        master["post_ca_crisis"] = (dates >= "2001-01-01").astype(int)
        master["post_shale"] = (dates >= "2010-01-01").astype(int)
        master["post_covid"] = ((dates >= "2020-03-01") & (dates <= "2020-12-31")).astype(int)
        # ERCOT ORDC introduction (June 2014) — changes gas-to-price relationship
        master["post_ordc"] = (dates >= "2014-06-01").astype(int)
        print(f"    Created 5 structural break dummies: post_uri, post_ca_crisis, post_shale, post_covid, post_ordc")

        # Structural break interaction: gas passthrough × era
        # Allows the henry_hub coefficient to differ pre/post structural breaks
        break_interactions = 0
        if "henry_hub_spot" in master.columns:
            master["post_ordc_x_henry"] = master["post_ordc"] * master["henry_hub_spot"]
            master["post_shale_x_henry"] = master["post_shale"] * master["henry_hub_spot"]
            break_interactions += 2
        print(f"    Created {break_interactions} structural break interaction terms")

    # ------------------------------------------------------------------
    # Data quality audit
    # ------------------------------------------------------------------
    print("\n  --- Data Quality Audit ---")
    quality_report = _data_quality_audit(master)
    with open(DATA_QUALITY, "w") as f:
        json.dump(quality_report, f, indent=2, default=str)
    print(f"  Saved: {DATA_QUALITY}")

    # ------------------------------------------------------------------
    # Save enhanced master
    # ------------------------------------------------------------------
    master.to_csv(MASTER_V8, index=False)
    n_cols_end = len(master.columns)
    print(f"\n  Enhanced master: {len(master)} rows × {n_cols_end} columns "
          f"(+{n_cols_end - n_cols_start} new columns)")
    print(f"  Saved: {MASTER_V8}")

    # Print coverage summary
    print(f"\n  Coverage Summary:")
    low_coverage = []
    for col in master.columns:
        n_valid = master[col].notna().sum()
        if n_valid < 60 and col not in ("date", "year", "month"):
            low_coverage.append((col, n_valid))
    if low_coverage:
        print(f"  Variables with <60 usable monthly observations:")
        for col, n in sorted(low_coverage, key=lambda x: x[1]):
            print(f"    {col}: {n} months")

    return master


# ============================================================================
# TIER 2: SEASONAL DECOMPOSITION
# ============================================================================

def _seasonal_decompose_monthly(master, price_cols):
    """STL decomposition on monthly price series.

    Applied BEFORE first-differencing to separate seasonal from trend.
    Requires statsmodels; falls back gracefully if unavailable.
    """
    banner("TIER 2: Seasonal Decomposition (STL)")
    try:
        from statsmodels.tsa.seasonal import STL
    except ImportError:
        print("  WARNING: statsmodels not available — skipping STL decomposition")
        return master

    master = master.copy()
    decomposed = 0
    for col in price_cols:
        if col not in master.columns:
            continue
        series = master[col].dropna()
        if len(series) < 36:
            print(f"  {col}: SKIPPED (only {len(series)} obs, need 36+)")
            continue
        try:
            stl = STL(series, period=12, robust=True)
            result = stl.fit()
            master.loc[series.index, f"{col}_trend"] = result.trend
            master.loc[series.index, f"{col}_seasonal"] = result.seasonal
            master.loc[series.index, f"{col}_sa"] = result.trend + result.resid
            decomposed += 1
            print(f"  {col}: decomposed ({len(series)} obs) → _trend, _seasonal, _sa")
        except Exception as e:
            print(f"  {col}: STL failed — {e}")

    print(f"\n  Decomposed {decomposed}/{len(price_cols)} price series")
    return master


# ============================================================================
# TIER 3: PRE-REGRESSION DIAGNOSTICS
# ============================================================================

def _compute_vif(X):
    """Compute Variance Inflation Factor for each variable."""
    vif_data = {}
    cols = [c for c in X.columns if c != "intercept"]
    for col in cols:
        y_i = X[col].values
        X_others = X[[c for c in X.columns if c != col]].values
        mask = ~(np.isnan(y_i) | np.isnan(X_others).any(axis=1))
        y_i_clean, X_others_clean = y_i[mask], X_others[mask]
        if len(y_i_clean) < X_others_clean.shape[1] + 1:
            vif_data[col] = float('inf')
            continue
        coef = np.linalg.lstsq(X_others_clean, y_i_clean, rcond=None)[0]
        y_hat = X_others_clean @ coef
        ss_res = np.sum((y_i_clean - y_hat) ** 2)
        ss_tot = np.sum((y_i_clean - y_i_clean.mean()) ** 2)
        r2_i = 1 - ss_res / ss_tot if ss_tot > 0 else 0
        vif_data[col] = 1 / (1 - r2_i) if r2_i < 1 else float('inf')
    return vif_data


def _prune_by_vif(X, y, threshold=10):
    """Iterative VIF elimination: drop highest VIF (with lowest |t|) until all < threshold."""
    dropped = []
    X_work = X.copy()
    max_iters = 30
    for _ in range(max_iters):
        # Compute VIF
        vif = _compute_vif(X_work)
        max_vif_var = max(vif, key=vif.get) if vif else None
        if max_vif_var is None or vif[max_vif_var] <= threshold:
            break
        # Among variables with VIF > threshold, drop the one with lowest |t-stat|
        high_vif = {k: v for k, v in vif.items() if v > threshold}
        # Quick OLS to get t-stats
        X_np = X_work.values.astype(float)
        y_np = y.values.astype(float) if hasattr(y, 'values') else y
        mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
        X_c, y_c = X_np[mask], y_np[mask]
        if X_c.shape[0] <= X_c.shape[1]:
            # Can't compute t-stats, just drop highest VIF
            drop_var = max_vif_var
        else:
            beta = np.linalg.lstsq(X_c, y_c, rcond=None)[0]
            resid = y_c - X_c @ beta
            ms_res = np.sum(resid**2) / (len(y_c) - X_c.shape[1])
            try:
                se = np.sqrt(np.abs(np.diag(ms_res * np.linalg.inv(X_c.T @ X_c))))
                t_abs = np.abs(beta / se)
            except np.linalg.LinAlgError:
                t_abs = np.abs(beta)
            # Map to column names
            col_t = {col: t_abs[i] for i, col in enumerate(X_work.columns)}
            # Among high VIF vars, find lowest |t|
            drop_var = min(high_vif.keys(), key=lambda v: col_t.get(v, 0))
        dropped.append({"var": drop_var, "vif": float(vif[drop_var])})
        X_work = X_work.drop(columns=[drop_var])

    if dropped:
        print(f"    VIF pruning: dropped {[d['var'] for d in dropped]}")
    return X_work, dropped


def _chow_test(X, y, break_idx):
    """Chow test for structural break at given index."""
    X_np = X if isinstance(X, np.ndarray) else X.values.astype(float)
    y_np = y if isinstance(y, np.ndarray) else y.values.astype(float) if hasattr(y, 'values') else np.array(y)
    mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
    X_np, y_np = X_np[mask], y_np[mask]

    k = X_np.shape[1]
    if break_idx <= k + 1 or break_idx >= len(y_np) - k - 1:
        return {"stat": None, "p_value": None, "note": "insufficient observations"}

    X1, y1 = X_np[:break_idx], y_np[:break_idx]
    X2, y2 = X_np[break_idx:], y_np[break_idx:]

    if len(y1) < k + 1 or len(y2) < k + 1:
        return {"stat": None, "p_value": None, "note": "insufficient observations"}

    ssr_pooled = np.sum((y_np - X_np @ np.linalg.lstsq(X_np, y_np, rcond=None)[0])**2)
    ssr_1 = np.sum((y1 - X1 @ np.linalg.lstsq(X1, y1, rcond=None)[0])**2)
    ssr_2 = np.sum((y2 - X2 @ np.linalg.lstsq(X2, y2, rcond=None)[0])**2)

    n = len(y_np)
    denom = (ssr_1 + ssr_2) / (n - 2 * k)
    if denom <= 0:
        return {"stat": None, "p_value": None, "note": "degenerate"}
    chow_stat = ((ssr_pooled - (ssr_1 + ssr_2)) / k) / denom
    p_value = 1 - sp_stats.f.cdf(chow_stat, k, n - 2 * k)
    return {"stat": float(chow_stat), "p_value": float(p_value)}


def _granger_causality(master, cause_col, effect_col, max_lag=12):
    """Test if cause_col Granger-causes effect_col at lags 3, 6, 12."""
    if cause_col not in master.columns or effect_col not in master.columns:
        return {}
    dy = master[effect_col].diff().dropna()
    dx = master[cause_col].diff().dropna()
    data = pd.DataFrame({"y": dy, "x": dx}).dropna()
    y_arr = data["y"].values
    x_arr = data["x"].values
    n = len(y_arr)

    results = {}
    for lag in [3, 6, 12]:
        if n < 2 * lag + 10:
            continue
        Y = y_arr[lag:]
        # Restricted: AR(lag) on y only
        X_r = np.column_stack([
            np.ones(n - lag),
            *[np.array([y_arr[t - j - 1] for t in range(lag, n)]) for j in range(lag)]
        ])
        # Unrestricted: AR(lag) on y + x lags
        X_u = np.column_stack([
            X_r,
            *[np.array([x_arr[t - j - 1] for t in range(lag, n)]) for j in range(lag)]
        ])
        ssr_r = np.sum((Y - X_r @ np.linalg.lstsq(X_r, Y, rcond=None)[0])**2)
        ssr_u = np.sum((Y - X_u @ np.linalg.lstsq(X_u, Y, rcond=None)[0])**2)

        df1 = lag
        df2 = (n - lag) - X_u.shape[1]
        if df2 <= 0:
            continue
        f_stat = ((ssr_r - ssr_u) / df1) / (ssr_u / df2) if ssr_u > 0 else 0
        p_val = 1 - sp_stats.f.cdf(f_stat, df1, df2)
        results[f"lag_{lag}"] = {"f_stat": round(float(f_stat), 4), "p_value": round(float(p_val), 4)}
    return results


def _select_optimal_lag(master, y_col, x_base, control_var, lags=(1, 3, 6, 12)):
    """Select lag for control variable that minimizes AIC."""
    best_lag, best_aic = None, float('inf')
    aic_all = {}
    for lag in lags:
        col = f"{control_var}_lag{lag}"
        if col not in master.columns:
            continue
        subset = master[[y_col] + x_base + [col]].dropna()
        if len(subset) < len(x_base) + 5:
            continue
        X = _make_X(subset, x_base + [col])
        y_vals = subset[y_col].values
        X_np = X.values.astype(float)
        mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_vals))
        X_np, y_vals = X_np[mask], y_vals[mask]
        if len(y_vals) < X_np.shape[1] + 1:
            continue
        beta = np.linalg.lstsq(X_np, y_vals, rcond=None)[0]
        resid = y_vals - X_np @ beta
        n_obs, k = X_np.shape
        sse = np.sum(resid**2)
        aic = n_obs * np.log(sse / n_obs) + 2 * k if n_obs > 0 and sse > 0 else float('inf')
        aic_all[lag] = round(aic, 2)
        if aic < best_aic:
            best_aic, best_lag = aic, lag
    return best_lag, aic_all


# ============================================================================
# TIER 4: QUANTILE REGRESSION
# ============================================================================

def _quantile_regression(X, y, tau=0.5):
    """Quantile regression via linear programming (exact solution)."""
    from scipy.optimize import linprog
    n, k = X.shape
    c = np.concatenate([np.zeros(k), tau * np.ones(n), (1 - tau) * np.ones(n)])
    A_eq = np.hstack([X, np.eye(n), -np.eye(n)])
    b_eq = y
    bounds = [(None, None)] * k + [(0, None)] * (2 * n)
    result = linprog(c, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method='highs')
    if result.success:
        return result.x[:k]
    # Fallback: Nelder-Mead on pinball loss
    def pinball_loss(beta):
        resid = y - X @ beta
        return np.sum(np.where(resid >= 0, tau * resid, (tau - 1) * resid))
    beta_ols = np.linalg.lstsq(X, y, rcond=None)[0]
    return minimize(pinball_loss, beta_ols, method='Nelder-Mead',
                    options={'maxiter': 10000, 'xatol': 1e-8}).x


# ============================================================================
# TIER 5: POST-REGRESSION DIAGNOSTICS
# ============================================================================

def _durbin_watson(residuals):
    """Durbin-Watson test for autocorrelation."""
    r = np.array(residuals)
    diff = np.diff(r)
    denom = np.sum(r**2)
    return float(np.sum(diff**2) / denom) if denom > 0 else 2.0


def _newey_west_se(X, residuals, bandwidth=None):
    """HAC standard errors robust to autocorrelation and heteroskedasticity."""
    n, k = X.shape
    if bandwidth is None:
        bandwidth = int(np.ceil(n ** (1 / 3)))
    e = np.array(residuals)
    S = np.zeros((k, k))
    for lag in range(bandwidth + 1):
        weight = 1 - lag / (bandwidth + 1)  # Bartlett kernel
        for t in range(lag, n):
            outer = e[t] * e[t - lag] * np.outer(X[t], X[t - lag])
            S += weight * outer
            if lag > 0:
                S += weight * outer.T
    try:
        XtX_inv = np.linalg.inv(X.T @ X)
    except np.linalg.LinAlgError:
        XtX_inv = np.linalg.pinv(X.T @ X)
    V_HAC = XtX_inv @ S @ XtX_inv
    return np.sqrt(np.abs(np.diag(V_HAC)))


def _influence_analysis(X, y, fitted, residuals):
    """Cook's distance + outlier identification."""
    X_np = X if isinstance(X, np.ndarray) else X.values
    n, k = X_np.shape
    try:
        hat_matrix = X_np @ np.linalg.inv(X_np.T @ X_np) @ X_np.T
    except np.linalg.LinAlgError:
        hat_matrix = X_np @ np.linalg.pinv(X_np.T @ X_np) @ X_np.T
    h = np.diag(hat_matrix)
    r = np.array(residuals)
    mse = np.sum(r**2) / max(n - k, 1)
    denom = k * mse * (1 - h)**2
    denom = np.where(denom > 0, denom, 1e-10)
    cooks_d = (r**2 * h) / denom

    threshold = 4.0 / n
    influential_idx = np.where(cooks_d > threshold)[0]

    return {
        "cooks_d": cooks_d.tolist(),
        "max_cooks_d": float(np.max(cooks_d)),
        "n_influential": int(len(influential_idx)),
        "influential_indices": influential_idx.tolist(),
        "threshold": float(threshold),
    }


def _robust_regression(X, y, max_iter=50, tol=1e-6):
    """Iteratively Reweighted Least Squares with Huber weights."""
    c = 1.345  # Huber constant
    X_np = X if isinstance(X, np.ndarray) else X.values
    y_np = np.array(y)
    beta = np.linalg.lstsq(X_np, y_np, rcond=None)[0]

    for _ in range(max_iter):
        resid = y_np - X_np @ beta
        sigma = np.median(np.abs(resid)) / 0.6745
        if sigma < 1e-10:
            break
        u = resid / sigma
        w = np.where(np.abs(u) <= c, 1.0, c / np.abs(u))
        W = np.diag(w)
        try:
            beta_new = np.linalg.lstsq(X_np.T @ W @ X_np, X_np.T @ W @ y_np, rcond=None)[0]
        except np.linalg.LinAlgError:
            break
        if np.max(np.abs(beta_new - beta)) < tol:
            beta = beta_new
            break
        beta = beta_new

    return beta, w


def _rolling_regression(master, y_col, x_cols, window=60):
    """60-month rolling window to check coefficient stability."""
    valid_cols = [c for c in x_cols if c in master.columns]
    if not valid_cols:
        return []
    subset = master[[y_col] + valid_cols].dropna()
    if len(subset) < window + 10:
        return []

    results = []
    for i in range(window, len(subset)):
        chunk = subset.iloc[i - window:i]
        X = _make_X(chunk, valid_cols)
        y_vals = chunk[y_col].values
        X_np = X.values.astype(float)
        mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_vals))
        X_c, y_c = X_np[mask], y_vals[mask]
        if len(y_c) < X_c.shape[1] + 1:
            continue
        beta = np.linalg.lstsq(X_c, y_c, rcond=None)[0]
        coef_dict = {name: float(beta[j]) for j, name in enumerate(X.columns)}
        if "date" in master.columns:
            idx = subset.index[i - 1]
            coef_dict["end_date"] = str(master.loc[idx, "date"]) if idx in master.index else str(i)
        results.append(coef_dict)
    return results


def _reset_test(X, y, residuals, fitted):
    """Ramsey RESET test for functional form misspecification."""
    X_np = X if isinstance(X, np.ndarray) else X.values
    y_np = np.array(y)
    y_hat = np.array(fitted)
    n, k = X_np.shape

    # Add y_hat^2 and y_hat^3 as auxiliary regressors
    X_aug = np.column_stack([X_np, y_hat**2, y_hat**3])
    k_aug = X_aug.shape[1]

    beta_aug = np.linalg.lstsq(X_aug, y_np, rcond=None)[0]
    resid_aug = y_np - X_aug @ beta_aug
    ssr_r = np.sum(np.array(residuals)**2)
    ssr_u = np.sum(resid_aug**2)

    df1 = k_aug - k
    df2 = n - k_aug
    if df2 <= 0 or ssr_u <= 0:
        return {"stat": None, "p_value": None}
    f_stat = ((ssr_r - ssr_u) / df1) / (ssr_u / df2)
    p_value = 1 - sp_stats.f.cdf(f_stat, df1, df2)
    return {"stat": float(f_stat), "p_value": float(p_value)}


# ============================================================================
# TIER 6: OUT-OF-SAMPLE VALIDATION & MODEL SELECTION
# ============================================================================

def _out_of_sample_validation(master, y_col, x_cols, holdout_months=24):
    """Reserve last holdout_months for out-of-sample testing."""
    valid_cols = [c for c in x_cols if c in master.columns]
    subset = master[[y_col] + valid_cols].dropna()
    if len(subset) < holdout_months + 30:
        return {"note": "insufficient data for OOS validation"}

    train = subset.iloc[:-holdout_months]
    test = subset.iloc[-holdout_months:]

    X_train = _make_X(train, valid_cols)
    y_train = train[y_col].values
    # VIF prune on train set
    X_train_pruned, dropped = _prune_by_vif(X_train, y_train)
    kept_cols = [c for c in X_train_pruned.columns if c != "intercept"]

    X_np = X_train_pruned.values.astype(float)
    y_np = y_train.astype(float)
    mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
    X_np, y_np = X_np[mask], y_np[mask]
    if len(y_np) <= X_np.shape[1]:
        return {"note": "insufficient train data after pruning"}

    beta = np.linalg.lstsq(X_np, y_np, rcond=None)[0]

    # Predict on test
    X_test = _make_X(test, kept_cols)
    X_test_np = X_test.values.astype(float)
    y_test = test[y_col].values
    mask_t = ~(np.isnan(X_test_np).any(axis=1) | np.isnan(y_test))
    X_test_np, y_test = X_test_np[mask_t], y_test[mask_t]
    if len(y_test) == 0:
        return {"note": "no valid test observations"}

    y_pred = X_test_np @ beta
    rmse_oos = float(np.sqrt(np.mean((y_test - y_pred)**2)))
    ss_res = np.sum((y_test - y_pred)**2)
    ss_tot = np.sum((y_test - y_test.mean())**2)
    r2_oos = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0

    y_hat_train = X_np @ beta
    rmse_is = float(np.sqrt(np.mean((y_np - y_hat_train)**2)))

    return {
        "rmse_in_sample": rmse_is,
        "rmse_out_of_sample": rmse_oos,
        "r2_out_of_sample": r2_oos,
        "overfit_ratio": round(rmse_oos / rmse_is, 3) if rmse_is > 0 else None,
        "n_train": int(len(y_np)),
        "n_test": int(len(y_test)),
    }


def _selection_score(adj_r2, dc_pvalue, max_vif, dw_stat, r2_oos, overfit_ratio):
    """Composite score balancing fit, DC precision, diagnostics, and generalization."""
    r2_score = max(0, adj_r2) if adj_r2 is not None else 0
    dc_score = 1 - min(dc_pvalue, 1.0) if dc_pvalue is not None else 0
    oos_score = max(0, r2_oos) if r2_oos is not None else 0
    vif_score = max(0, 1 - max_vif / 10) if max_vif is not None else 0
    dw_score = max(0, 1 - abs(dw_stat - 2.0) / 2.0) if dw_stat else 0

    composite = (r2_score * 0.35 + dc_score * 0.25 + oos_score * 0.20
                 + vif_score * 0.10 + dw_score * 0.10)
    return round(composite, 4)


# ============================================================================
# STEP 2B: ENHANCED REGRESSION — 6 VARIANTS PER MARKET
# ============================================================================

def step2b_enhanced_regression(master_v8):
    """Tier 4: Run 6 model variants per market + P95 subvariant for ERCOT."""
    banner("STEP 2B: Enhanced Regression (6 variants × 3 markets)")

    results = {}
    diagnostics = {}

    # ------------------------------------------------------------------
    # Define base variable sets per market
    # ------------------------------------------------------------------

    # ERCOT base (Variant A — full spec with scarcity/congestion controls)
    ercot_dv = "ercot_rtm_mean" if "ercot_rtm_mean" in master_v8.columns else "ercot_wholesale_mwh"
    ercot_base = ["henry_hub_spot", "tx_hdd", "tx_cdd",
                  "tx_gdp_growth_pct", "tx_data_center_twh", "tx_ercot_demand_twh",
                  "us_industrial_prod_index", "tx_gas_gen_pct", "tx_wind_gen_pct",
                  "tx_coal_gen_pct", "tx_solar_gen_pct",
                  "ercot_scarcity_intensity", "ercot_spike_count",
                  "ercot_actual_reserve_margin_pct",
                  "ercot_congestion_per_mwh",
                  "tx_battery_capacity_gw"]
    # Binary dummies (ptc_active, tx_crez_active) removed per literature validation
    ercot_policy = ["ptc_rate_cents_kwh", "queue_backlog_gw",
                    "ercot_large_load_queue_gw", "cumulative_ferc_reforms",
                    "n_ira_credits"]

    # CAISO base — ca_solar_gen_pct replaced with curtailment/negative hours
    # (ca_solar_gen_pct had VIF=19 due to strong time trend; curtailment captures
    # the same solar oversupply → price depression with more within-year variation)
    caiso_dv = "caiso_wholesale_mwh"
    caiso_base = ["henry_hub_spot", "ca_hdd", "ca_cdd",
                  "ca_gdp_growth_pct", "ca_data_center_twh", "ca_caiso_demand_twh",
                  "ca_gas_gen_pct", "ca_wind_gen_pct",
                  "caiso_total_curtail_gwh", "caiso_monthly_negative_hours",
                  "caiso_monthly_congestion_avg",
                  "ca_battery_capacity_gw"]
    # ca_allowance_spread: premium above CARB floor price (better identified than
    # ca_allowance_price_per_ton which trends monotonically with other policy vars)
    caiso_policy = ["itc_rate_pct", "ptc_rate_cents_kwh", "ca_rps_target_pct",
                    "ca_allowance_price_per_ton", "ca_allowance_spread",
                    "ca_nem_compensation_level",
                    "queue_backlog_gw", "n_rps_states"]

    # HH base
    hh_dv = "henry_hub_spot"
    hh_base = ["electric_power_bcfd", "us_hdd", "us_cdd",
               "us_gdp_growth_pct", "us_industrial_prod_index", "us_data_center_twh",
               "lng_utilization_pct",
               "us_ng_storage_vs_5yr_pct"]
    # ira_active and policy_intensity_index removed per literature validation
    hh_policy = ["itc_rate_pct", "ptc_rate_cents_kwh",
                 "queue_backlog_gw",
                 "cumulative_ferc_reforms"]

    # DC variable per market (for diagnostics tracking)
    dc_vars = {"ercot": "tx_data_center_twh", "caiso": "ca_data_center_twh", "hh": "us_data_center_twh"}

    # ------------------------------------------------------------------
    # Pre-regression: Granger causality tests
    # ------------------------------------------------------------------
    print("\n  --- Granger Causality Tests ---")
    granger_results = {}
    for label, cause, effect in [
        ("tx_dc→ercot", "tx_data_center_twh", ercot_dv),
        ("ca_dc→caiso", "ca_data_center_twh", caiso_dv),
        ("us_dc→hh", "us_data_center_twh", hh_dv),
        ("hh→us_dc (reverse)", hh_dv, "us_data_center_twh"),
    ]:
        gc = _granger_causality(master_v8, cause, effect)
        granger_results[label] = gc
        if gc:
            for lag_key, vals in gc.items():
                sig = "***" if vals["p_value"] < 0.01 else "**" if vals["p_value"] < 0.05 else "*" if vals["p_value"] < 0.1 else ""
                print(f"    {label} ({lag_key}): F={vals['f_stat']:.3f}, p={vals['p_value']:.4f} {sig}")
        else:
            print(f"    {label}: insufficient data")
    diagnostics["granger_causality"] = granger_results

    # ------------------------------------------------------------------
    # Run each market
    # ------------------------------------------------------------------
    for market, dv, base_vars, pol_vars, dc_var in [
        ("ercot", ercot_dv, ercot_base, ercot_policy, "tx_data_center_twh"),
        ("caiso", caiso_dv, caiso_base, caiso_policy, "ca_data_center_twh"),
        ("hh", hh_dv, hh_base, hh_policy, "us_data_center_twh"),
    ]:
        print(f"\n{'='*60}")
        print(f"  MARKET: {market.upper()} (DV={dv})")
        print(f"{'='*60}")

        if dv not in master_v8.columns:
            print(f"  SKIPPED: {dv} not in master")
            continue

        # Exclude Feb 2021 (Uri) from all markets — event distorted gas prices
        # nationally ($3→$23+/MMBtu at HH) and electricity across all ISOs
        mkt_df, uri_n = _exclude_uri(master_v8)
        if uri_n > 0:
            print(f"    Excluded {uri_n} Uri observation(s) (Feb 2021) from {market.upper()} sample")

        all_base = [v for v in base_vars + pol_vars if v in mkt_df.columns and
                    mkt_df[v].notna().sum() > 30]

        # ------------------------------------------------------------------
        # Variant A: Current Full Model (baseline)
        # ------------------------------------------------------------------
        print(f"\n  --- Variant A: Baseline Full Model ---")
        df_a = mkt_df[[dv] + all_base].dropna()
        if len(df_a) > 20:
            X_a = _make_X(df_a, all_base)
            y_a = df_a[dv]
            # Use VIF < 10 pruning instead of correlation-only pruning
            # to properly address multicollinearity (VIFs were 17,530+ with old method)
            X_a, dropped_a = _prune_by_vif(X_a, y_a, threshold=10)
            ols_a = _ols(X_a, y_a, f"{market.upper()}_A_Baseline")
            results[f"{market}_A"] = ols_a

            # Post-diagnostics for A
            dw_a = _durbin_watson(ols_a["residuals"])
            reset_a = _reset_test(X_a.values, y_a.values, ols_a["residuals"], ols_a["fitted"])
            vif_a = _compute_vif(X_a)
            infl_a = _influence_analysis(X_a.values, y_a.values, ols_a["fitted"], ols_a["residuals"])
            max_vif_a = max(vif_a.values()) if vif_a else 0
            dc_p_a = ols_a["coefficients"].get(dc_var, {}).get("p_value")

            diagnostics[f"{market}_A"] = {
                "dw": dw_a, "reset": reset_a, "vif": vif_a,
                "max_vif": max_vif_a, "influence": infl_a,
                "n": ols_a["n"], "k": ols_a["k"],
                "nk_ratio": round(ols_a["n"] / ols_a["k"], 1),
            }
            print(f"    DW={dw_a:.3f}, RESET p={reset_a.get('p_value', 'N/A')}, "
                  f"Max VIF={max_vif_a:.1f}, Cook's D max={infl_a['max_cooks_d']:.4f}")

            # Newey-West if autocorrelation detected
            if dw_a < 1.5 or dw_a > 2.5:
                print(f"    Autocorrelation detected (DW={dw_a:.3f}) — computing Newey-West SEs")
                X_np = X_a.values.astype(float)
                y_np = y_a.values.astype(float)
                mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
                nw_se = _newey_west_se(X_np[mask], ols_a["residuals"])
                diagnostics[f"{market}_A"]["newey_west_se"] = {
                    col: float(nw_se[i]) for i, col in enumerate(X_a.columns)
                }
        else:
            print(f"    SKIPPED: insufficient data (n={len(df_a)})")

        # ------------------------------------------------------------------
        # AIC-based lag selection for control variables
        # ------------------------------------------------------------------
        print(f"\n  --- AIC Lag Selection ---")
        selected_lags = {}
        lag_control_map = {
            "ercot": ["ercot_congestion_per_mwh", "ercot_actual_reserve_margin_pct",
                      "ercot_total_curtailment_pct", "ercot_scarcity_intensity",
                      "ercot_spike_count",
                      "ercot_regulationup_price", "ercot_responsivereserve_price"],
            "caiso": ["caiso_monthly_congestion_avg", "caiso_actual_reserve_margin_pct",
                      "caiso_total_curtail_gwh", "caiso_monthly_negative_hours"],
            "hh": [],
        }
        # Spillover vars
        spillover_map = {
            "ercot": ["caiso_wholesale"],
            "caiso": ["ercot_wholesale"],
            "hh": [],
        }

        for ctrl_var in lag_control_map.get(market, []):
            best_lag, aic_vals = _select_optimal_lag(mkt_df, dv, all_base, ctrl_var)
            if best_lag is not None:
                selected_lags[ctrl_var] = best_lag
                print(f"    {ctrl_var}: best lag={best_lag} (AIC: {aic_vals})")
            else:
                print(f"    {ctrl_var}: no valid lag found")

        for spill_var in spillover_map.get(market, []):
            best_lag, aic_vals = _select_optimal_lag(mkt_df, dv, all_base, spill_var)
            if best_lag is not None:
                selected_lags[spill_var] = best_lag
                print(f"    {spill_var} (spillover): best lag={best_lag} (AIC: {aic_vals})")

        diagnostics[f"{market}_lag_selection"] = selected_lags

        # ------------------------------------------------------------------
        # Variant B: Enhanced Controls
        # ------------------------------------------------------------------
        print(f"\n  --- Variant B: Enhanced Controls ---")
        b_extra = []
        for ctrl_var, lag in selected_lags.items():
            lag_col = f"{ctrl_var}_lag{lag}"
            if lag_col in mkt_df.columns:
                b_extra.append(lag_col)

        # Weather controls
        weather_map = {
            "ercot": ["us_cdd_departure_pct"],
            "caiso": ["ca_cdd_departure_pct"],
            "hh": ["us_cdd_departure_pct", "us_hdd_departure_pct"],
        }
        for wc in weather_map.get(market, []):
            if wc in mkt_df.columns and mkt_df[wc].notna().sum() > 30:
                b_extra.append(wc)

        # Structural break dummies — only if Chow test significant
        break_map = {
            "ercot": [("post_uri", "Feb 2021 (Uri)")],
            "caiso": [("post_ca_crisis", "Jan 2001 (CA crisis)")],
            "hh": [("post_shale", "Jan 2010 (shale)")],
        }
        if f"{market}_A" in results:
            # Run Chow tests at relevant break points
            for dummy_col, break_label in break_map.get(market, []):
                if dummy_col in mkt_df.columns:
                    df_chow = mkt_df[[dv] + all_base].dropna()
                    if len(df_chow) > 40:
                        X_chow = _make_X(df_chow, all_base)
                        y_chow = df_chow[dv].values
                        # Find break index
                        if "date" in mkt_df.columns:
                            break_date_map = {
                                "post_uri": "2021-02-01", "post_ca_crisis": "2001-01-01",
                                "post_shale": "2010-01-01",
                            }
                            bd = break_date_map.get(dummy_col)
                            if bd:
                                dates_chow = pd.to_datetime(df_chow["date"]) if "date" in df_chow.columns else None
                                if dates_chow is not None:
                                    break_idx = (dates_chow >= bd).values.argmax()
                                else:
                                    break_idx = len(df_chow) // 2
                            else:
                                break_idx = len(df_chow) // 2
                        else:
                            break_idx = len(df_chow) // 2
                        chow = _chow_test(X_chow.values, y_chow, break_idx)
                        diagnostics[f"{market}_chow_{dummy_col}"] = chow
                        if chow["p_value"] is not None and chow["p_value"] < 0.05:
                            b_extra.append(dummy_col)
                            print(f"    Chow test for {break_label}: F={chow['stat']:.2f}, "
                                  f"p={chow['p_value']:.4f} *** → including {dummy_col}")
                        elif chow["p_value"] is not None:
                            print(f"    Chow test for {break_label}: F={chow['stat']:.2f}, "
                                  f"p={chow['p_value']:.4f} — not significant")
                        else:
                            print(f"    Chow test for {break_label}: {chow.get('note', 'N/A')}")

        b_vars = all_base + [v for v in b_extra if v in mkt_df.columns and
                             mkt_df[v].notna().sum() > 30]
        df_b = mkt_df[[dv] + b_vars].dropna()
        n_k_b = len(df_b) / max(len(b_vars) + 1, 1)
        if len(df_b) > 20 and n_k_b >= 10:
            X_b = _make_X(df_b, b_vars)
            y_b = df_b[dv]
            X_b, vif_dropped_b = _prune_by_vif(X_b, y_b)
            ols_b = _ols(X_b, y_b, f"{market.upper()}_B_Enhanced")
            results[f"{market}_B"] = ols_b
            dw_b = _durbin_watson(ols_b["residuals"])
            vif_b = _compute_vif(X_b)
            diagnostics[f"{market}_B"] = {
                "dw": dw_b, "vif": vif_b, "max_vif": max(vif_b.values()) if vif_b else 0,
                "n": ols_b["n"], "k": ols_b["k"],
                "nk_ratio": round(ols_b["n"] / ols_b["k"], 1),
                "extra_controls": b_extra, "vif_dropped": vif_dropped_b,
            }
            if dw_b < 1.5 or dw_b > 2.5:
                X_np = X_b.values.astype(float)
                y_np = y_b.values.astype(float)
                mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
                nw_se = _newey_west_se(X_np[mask], ols_b["residuals"])
                diagnostics[f"{market}_B"]["newey_west_se"] = {
                    col: float(nw_se[i]) for i, col in enumerate(X_b.columns)
                }
        elif n_k_b < 10:
            print(f"    SKIPPED Variant B: n/k={n_k_b:.1f} < 10 (n={len(df_b)}, k={len(b_vars)+1})")
        else:
            print(f"    SKIPPED Variant B: insufficient data (n={len(df_b)})")

        # ------------------------------------------------------------------
        # Variant C: First-Differences
        # ------------------------------------------------------------------
        print(f"\n  --- Variant C: First-Differences ---")
        diff_replace = {
            "tx_data_center_twh": "tx_dc_twh_yoy_change",
            "ca_data_center_twh": "ca_dc_twh_yoy_change",
            "us_data_center_twh": "dc_twh_yoy_change",
            "queue_backlog_gw": "queue_yoy_change",
        }
        c_vars = []
        for v in all_base:
            replacement = diff_replace.get(v)
            if replacement and replacement in mkt_df.columns:
                c_vars.append(replacement)
            else:
                c_vars.append(v)

        # Use seasonally adjusted DV if available
        sa_dv = f"{dv}_sa"
        c_dv = sa_dv if sa_dv in mkt_df.columns else dv
        c_vars_avail = [v for v in c_vars if v in mkt_df.columns and
                        mkt_df[v].notna().sum() > 30]
        df_c = mkt_df[[c_dv] + c_vars_avail].dropna()
        n_k_c = len(df_c) / max(len(c_vars_avail) + 1, 1)

        if len(df_c) > 20 and n_k_c >= 10:
            X_c = _make_X(df_c, c_vars_avail)
            y_c = df_c[c_dv]
            X_c, vif_dropped_c = _prune_by_vif(X_c, y_c)
            ols_c = _ols(X_c, y_c, f"{market.upper()}_C_FirstDiff")
            results[f"{market}_C"] = ols_c
            dw_c = _durbin_watson(ols_c["residuals"])
            vif_c = _compute_vif(X_c)
            diagnostics[f"{market}_C"] = {
                "dw": dw_c, "vif": vif_c, "max_vif": max(vif_c.values()) if vif_c else 0,
                "n": ols_c["n"], "k": ols_c["k"],
                "nk_ratio": round(ols_c["n"] / ols_c["k"], 1),
                "used_sa_dv": (c_dv == sa_dv),
            }
        elif n_k_c < 10:
            print(f"    SKIPPED Variant C: n/k={n_k_c:.1f} < 10")
        else:
            print(f"    SKIPPED Variant C: insufficient data (n={len(df_c)})")

        # ------------------------------------------------------------------
        # Variant D: Interaction + Quantile
        # ------------------------------------------------------------------
        print(f"\n  --- Variant D: Interaction + Quantile ---")
        d_extra = []
        interaction_map = {
            "ercot": ["dc_x_gas_gen_pct", "dc_x_renewable_share",
                      "gas_x_tx_wind", "gas_x_ercot_scarcity",
                      "tx_wind_gen_pct_sq", "tx_solar_gen_pct_sq",
                      "post_ordc_x_henry", "post_shale_x_henry"],
            "caiso": ["ca_dc_x_solar", "gas_x_ca_solar",
                      "ca_solar_gen_pct_sq"],
            "hh": [],
        }
        for ix_var in interaction_map.get(market, []):
            if ix_var in mkt_df.columns and mkt_df[ix_var].notna().sum() > 30:
                d_extra.append(ix_var)

        d_vars = all_base + d_extra
        d_vars_avail = [v for v in d_vars if v in mkt_df.columns and
                        mkt_df[v].notna().sum() > 30]
        df_d = mkt_df[[dv] + d_vars_avail].dropna()

        if len(df_d) > 20:
            X_d = _make_X(df_d, d_vars_avail)
            y_d = df_d[dv]
            X_d, _ = _prune_by_vif(X_d, y_d)
            ols_d = _ols(X_d, y_d, f"{market.upper()}_D_Interaction")
            results[f"{market}_D"] = ols_d
            diagnostics[f"{market}_D"] = {
                "n": ols_d["n"], "k": ols_d["k"],
                "interactions": d_extra,
            }

            # Quantile regression (ERCOT only, or any market with enough data)
            if market == "ercot":
                X_np = X_d.values.astype(float)
                y_np = y_d.values.astype(float)
                mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
                X_qr, y_qr = X_np[mask], y_np[mask]

                qr_results = {}
                for tau in [0.50, 0.90, 0.95, 0.99]:
                    try:
                        beta_qr = _quantile_regression(X_qr, y_qr, tau)
                        qr_coefs = {col: float(beta_qr[i]) for i, col in enumerate(X_d.columns)}
                        qr_results[f"tau_{tau}"] = qr_coefs
                        dc_coef = qr_coefs.get(dc_var, "N/A")
                        flag = " [exploratory only]" if tau == 0.99 else ""
                        print(f"    QR tau={tau}: DC coeff={dc_coef:.4f}{flag}" if isinstance(dc_coef, float) else f"    QR tau={tau}: DC not in model{flag}")
                    except Exception as e:
                        print(f"    QR tau={tau}: FAILED — {e}")
                        qr_results[f"tau_{tau}"] = {"error": str(e)}

                results[f"{market}_D_quantile"] = qr_results
                diagnostics[f"{market}_D"]["quantile_regression"] = qr_results

        # ------------------------------------------------------------------
        # Variant E: Regime-Dependent
        # ------------------------------------------------------------------
        print(f"\n  --- Variant E: Regime-Dependent ---")
        regime_config = {
            "ercot": ("ercot_actual_reserve_margin_pct_lag1", 13.75, "reserves < 13.75%"),
            "caiso": ("ca_solar_gen_pct", None, "high vs low solar"),  # None = median split
            "hh": ("is_summer", 0.5, "summer vs winter"),
        }
        regime_col, regime_thresh, regime_label = regime_config.get(market, (None, None, None))
        if regime_col and regime_col in mkt_df.columns:
            e_cols = list(dict.fromkeys([dv] + all_base + [regime_col]))  # deduplicate
            df_e = mkt_df[e_cols].dropna()
            if regime_thresh is None:
                regime_thresh = df_e[regime_col].median()

            mask_scarcity = df_e[regime_col] < regime_thresh
            n_scarcity = mask_scarcity.sum()
            n_normal = (~mask_scarcity).sum()
            min_n = (len(all_base) + 1) * 10

            print(f"    Regime: {regime_label} (threshold={regime_thresh})")
            print(f"    Scarcity: n={n_scarcity}, Normal: n={n_normal}, min_n={min_n}")

            e_results = {}
            for label, mask_val in [("scarcity", mask_scarcity), ("normal", ~mask_scarcity)]:
                sub = df_e[mask_val]
                if len(sub) >= min_n:
                    X_e = _make_X(sub, [v for v in all_base if v in sub.columns])
                    y_e = sub[dv]
                    X_e, _ = _prune_by_vif(X_e, y_e)
                    ols_e = _ols(X_e, y_e, f"{market.upper()}_E_{label}")
                    e_results[label] = ols_e
                else:
                    print(f"    {label}: SKIPPED (n={len(sub)} < min_n={min_n})")
                    e_results[label] = {"note": f"insufficient obs ({len(sub)} < {min_n})"}

            results[f"{market}_E"] = e_results
            diagnostics[f"{market}_E"] = {
                "regime_col": regime_col, "threshold": regime_thresh,
                "n_scarcity": int(n_scarcity), "n_normal": int(n_normal),
            }
        else:
            print(f"    SKIPPED: regime column {regime_col} not available")

        # ------------------------------------------------------------------
        # Variant F: Non-Linear (conditional on RESET failure)
        # ------------------------------------------------------------------
        print(f"\n  --- Variant F: Non-Linear (conditional on RESET) ---")
        reset_pval = diagnostics.get(f"{market}_A", {}).get("reset", {}).get("p_value")
        quad_map = {
            "ercot": ["tx_dc_squared", "tx_wind_gen_pct_sq"],
            "caiso": ["ca_dc_squared", "ca_solar_gen_pct_sq"],
            "hh": ["us_dc_squared"],
        }
        quad_vars = [q for q in quad_map.get(market, []) if q in mkt_df.columns]

        if reset_pval is not None and reset_pval < 0.05 and quad_vars:
            print(f"    RESET p={reset_pval:.4f} < 0.05 → estimating quadratic model")
            f_vars = all_base + quad_vars
            f_vars_avail = [v for v in f_vars if v in mkt_df.columns and
                            mkt_df[v].notna().sum() > 30]
            df_f = mkt_df[[dv] + f_vars_avail].dropna()
            if len(df_f) > 20:
                X_f = _make_X(df_f, f_vars_avail)
                y_f = df_f[dv]
                X_f, _ = _prune_by_vif(X_f, y_f)
                ols_f = _ols(X_f, y_f, f"{market.upper()}_F_NonLinear")
                results[f"{market}_F"] = ols_f
                diagnostics[f"{market}_F"] = {
                    "n": ols_f["n"], "k": ols_f["k"],
                    "quad_vars": quad_vars,
                }
        else:
            reason = f"RESET p={reset_pval}" if reset_pval is not None else "no RESET result"
            print(f"    SKIPPED: linearity not rejected ({reason})")

        # ------------------------------------------------------------------
        # Variant G: Two-Stage Gas Passthrough — DIAGNOSTIC REFERENCE ONLY
        # ------------------------------------------------------------------
        # This variant imposes a physics-based heat rate (not data-estimated).
        # It is NOT a competing model — it exists to show what non-gas coefficients
        # look like when gas passthrough is fixed at the EIA fleet-average heat rate.
        # Compare Variant G non-gas coefficients against Variants A-F to check
        # whether the freely-estimated gas coefficient is distorting other variables.
        if market in ("ercot", "caiso") and dv in mkt_df.columns:
            print(f"\n  --- Variant G: Two-Stage Gas Passthrough (DIAGNOSTIC ONLY) ---")
            print(f"    NOTE: Gas passthrough imposed at EIA heat rate, not estimated.")
            print(f"    Use for coefficient comparison, NOT model selection.")
            df_g = mkt_df[[dv] + all_base].dropna()
            if len(df_g) > 20 and "henry_hub_spot" in df_g.columns:
                two_stage_result, gas_cost, y_resid = _two_stage_regression(
                    df_g, dv, all_base, region=market)
                results[f"{market}_G"] = two_stage_result
                dw_g = _durbin_watson(two_stage_result["residuals"])
                diagnostics[f"{market}_G"] = {
                    "dw": dw_g,
                    "n": two_stage_result["n"], "k": two_stage_result["k"],
                    "heat_rate": two_stage_result["two_stage"]["heat_rate_used"],
                    "gas_cost_mean": two_stage_result["two_stage"]["gas_cost_component_mean"],
                    "is_two_stage": True,
                    "is_diagnostic_only": True,
                }
            else:
                print(f"    SKIPPED: insufficient data or no henry_hub_spot")

        # ------------------------------------------------------------------
        # Rolling regression (Variant A specification)
        # ------------------------------------------------------------------
        print(f"\n  --- Rolling Regression (60-month window) ---")
        rolling = _rolling_regression(mkt_df, dv, all_base)
        if rolling:
            dc_rolling = [r.get(dc_var, None) for r in rolling if dc_var in r]
            dc_rolling_valid = [v for v in dc_rolling if v is not None]
            if dc_rolling_valid:
                print(f"    DC coefficient rolling: min={min(dc_rolling_valid):.4f}, "
                      f"max={max(dc_rolling_valid):.4f}, mean={np.mean(dc_rolling_valid):.4f}")
                if min(dc_rolling_valid) * max(dc_rolling_valid) < 0:
                    print(f"    WARNING: DC coefficient changes sign across rolling windows")
            diagnostics[f"{market}_rolling"] = rolling
        else:
            print(f"    SKIPPED: insufficient data for rolling regression")

        # ------------------------------------------------------------------
        # Robust regression comparison
        # ------------------------------------------------------------------
        print(f"\n  --- Robust Regression (Huber M-estimator) ---")
        if f"{market}_A" in results:
            df_rob = mkt_df[[dv] + all_base].dropna()
            X_rob = _make_X(df_rob, [v for v in all_base if v in df_rob.columns])
            y_rob = df_rob[dv].values
            X_np = X_rob.values.astype(float)
            mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_rob))
            X_c, y_c = X_np[mask], y_rob[mask]
            if len(y_c) > X_c.shape[1]:
                beta_robust, weights = _robust_regression(X_c, y_c)
                robust_coefs = {col: float(beta_robust[i]) for i, col in enumerate(X_rob.columns)}
                ols_coefs = {col: results[f"{market}_A"]["coefficients"][col]["coefficient"]
                             for col in X_rob.columns if col in results[f"{market}_A"]["coefficients"]}
                # Check if DC coefficient changes sign
                ols_dc = ols_coefs.get(dc_var, 0)
                rob_dc = robust_coefs.get(dc_var, 0)
                sign_change = (ols_dc * rob_dc < 0) if (ols_dc != 0 and rob_dc != 0) else False
                diagnostics[f"{market}_robust"] = {
                    "robust_coefficients": robust_coefs,
                    "dc_sign_change": sign_change,
                    "n_downweighted": int((weights < 0.99).sum()),
                }
                print(f"    OLS DC={ols_dc:.4f}, Robust DC={rob_dc:.4f}"
                      + (" *** SIGN CHANGE" if sign_change else ""))
                print(f"    Downweighted observations: {int((weights < 0.99).sum())}")

    # ------------------------------------------------------------------
    # ERCOT P95 Subvariant
    # ------------------------------------------------------------------
    # Exclude Uri for P95 and OOS sections (all markets)
    uri_excl_df, _ = _exclude_uri(master_v8)

    if "ercot_rtm_p95" in master_v8.columns:
        print(f"\n{'='*60}")
        print(f"  ERCOT P95 Subvariant (DV=ercot_rtm_p95)")
        print(f"{'='*60}")

        # Find best ERCOT variant
        best_ercot = None
        best_score = -1
        # NOTE: ercot_G excluded — it's a diagnostic reference (imposed heat rate), not a competing model
        for var_key in ["ercot_A", "ercot_B", "ercot_C", "ercot_D", "ercot_F"]:
            if var_key not in results or not isinstance(results[var_key], dict) or "adj_r_squared" not in results[var_key]:
                continue
            r = results[var_key]
            dc_p = r["coefficients"].get("tx_data_center_twh", {}).get("p_value")
            diag = diagnostics.get(var_key, {})
            score = _selection_score(
                r["adj_r_squared"], dc_p,
                diag.get("max_vif", 10), diag.get("dw", 2.0),
                None, None  # No OOS yet
            )
            if score > best_score:
                best_score = score
                best_ercot = var_key

        if best_ercot and best_ercot in results:
            # Get the variables used in the best model
            best_vars = [c for c in results[best_ercot]["coefficients"].keys() if c != "intercept"]
            p95_avail = [v for v in best_vars if v in uri_excl_df.columns]
            df_p95 = uri_excl_df[["ercot_rtm_p95"] + p95_avail].dropna()
            if len(df_p95) > 20:
                X_p95 = _make_X(df_p95, p95_avail)
                y_p95 = df_p95["ercot_rtm_p95"]
                X_p95, _ = _prune_by_vif(X_p95, y_p95)
                ols_p95 = _ols(X_p95, y_p95, "ERCOT_P95_Subvariant")
                results["ercot_P95"] = ols_p95
                dc_p95 = ols_p95["coefficients"].get("tx_data_center_twh", {}).get("p_value")
                dc_mean = results.get(best_ercot, {}).get("coefficients", {}).get(
                    "tx_data_center_twh", {}).get("p_value")
                print(f"    Best base variant: {best_ercot}")
                print(f"    DC p-value (mean DV): {dc_mean}, DC p-value (P95 DV): {dc_p95}")

    # ------------------------------------------------------------------
    # OOS Validation for each variant
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  OUT-OF-SAMPLE VALIDATION")
    print(f"{'='*60}")
    oos_results = {}
    for var_key, var_data in results.items():
        if not isinstance(var_data, dict) or "coefficients" not in var_data:
            continue
        market_prefix = var_key.split("_")[0]
        dv_map = {"ercot": ercot_dv, "caiso": caiso_dv, "hh": hh_dv}
        var_dv = dv_map.get(market_prefix, None)
        # P95 subvariant uses a different DV
        if var_key == "ercot_P95":
            var_dv = "ercot_rtm_p95"
        # Two-stage variants: skip OOS (residualized DV not directly comparable)
        if var_key.endswith("_G") and var_data.get("two_stage"):
            oos_results[var_key] = {"note": "two-stage model — OOS on residualized DV not comparable"}
            print(f"  {var_key}: two-stage model — OOS skipped (residualized DV)")
            continue
        if var_dv is None:
            continue
        var_cols = [c for c in var_data["coefficients"].keys() if c != "intercept"]
        # Use Uri-excluded data for ERCOT variants
        # Use Uri-excluded data for all markets
        oos_df = uri_excl_df
        oos = _out_of_sample_validation(oos_df, var_dv, var_cols)
        oos_results[var_key] = oos
        if "rmse_out_of_sample" in oos:
            overfit = oos.get("overfit_ratio", "N/A")
            print(f"  {var_key}: OOS R²={oos['r2_out_of_sample']:.4f}, "
                  f"RMSE(IS/OOS)={oos['rmse_in_sample']:.2f}/{oos['rmse_out_of_sample']:.2f}, "
                  f"overfit={overfit}")
            if isinstance(overfit, (int, float)) and overfit > 2.0:
                print(f"    WARNING: overfit ratio {overfit:.1f} > 2.0")
        else:
            print(f"  {var_key}: {oos.get('note', 'N/A')}")
    diagnostics["oos_validation"] = oos_results

    # ------------------------------------------------------------------
    # Model Selection Table
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  MODEL SELECTION SCORES")
    print(f"{'='*60}")
    print(f"  {'Variant':<20} {'Adj R²':>8} {'DC p-val':>10} {'OOS R²':>8} "
          f"{'Max VIF':>8} {'DW':>6} {'Score':>8}")
    print(f"  {'-'*72}")

    selection_scores = {}
    p95_scores = {}  # Separate tracking for P95 subvariant (different DV)
    for var_key, var_data in results.items():
        if not isinstance(var_data, dict) or "adj_r_squared" not in var_data:
            continue
        market_prefix = var_key.split("_")[0]
        dc_name = dc_vars.get(market_prefix)
        dc_p = var_data["coefficients"].get(dc_name, {}).get("p_value") if dc_name else None
        diag = diagnostics.get(var_key, {})
        oos = oos_results.get(var_key, {})

        score = _selection_score(
            var_data["adj_r_squared"],
            dc_p,
            diag.get("max_vif", 10),
            diag.get("dw", 2.0),
            oos.get("r2_out_of_sample"),
            oos.get("overfit_ratio"),
        )

        # P95 uses a different DV — track separately, not in main competition
        # G variants use imposed heat rate — diagnostic only, not competing
        is_p95 = var_key.endswith("_P95")
        is_diagnostic = var_key.endswith("_G") and diagnostics.get(var_key, {}).get("is_diagnostic_only")
        if is_p95:
            p95_scores[var_key] = score
        elif is_diagnostic:
            p95_scores[var_key] = score  # track but don't compete
        else:
            selection_scores[var_key] = score

        dc_p_str = f"{dc_p:.4f}" if dc_p is not None else "N/A"
        oos_r2_str = f"{oos.get('r2_out_of_sample', 'N/A'):.4f}" if isinstance(oos.get('r2_out_of_sample'), (int, float)) else "N/A"
        suffix = "  (supplementary — different DV)" if is_p95 else \
                 "  (DIAGNOSTIC — imposed heat rate)" if is_diagnostic else ""
        print(f"  {var_key:<20} {var_data['adj_r_squared']:8.4f} {dc_p_str:>10} "
              f"{oos_r2_str:>8} {diag.get('max_vif', 'N/A'):>8} "
              f"{diag.get('dw', 'N/A'):>6} {score:8.4f}{suffix}")

    # Mark best per market (P95 excluded from competition)
    for mkt in ["ercot", "caiso", "hh"]:
        mkt_scores = {k: v for k, v in selection_scores.items() if k.startswith(mkt)}
        if mkt_scores:
            best = max(mkt_scores, key=mkt_scores.get)
            print(f"  → Best {mkt.upper()}: {best} (score={mkt_scores[best]:.4f})")
            diagnostics[f"{mkt}_best_variant"] = best

    # Report P95 separately
    for p95_key, p95_score in p95_scores.items():
        p95_data = results[p95_key]
        dc_name_p95 = dc_vars.get(p95_key.split("_")[0])
        dc_p95 = p95_data["coefficients"].get(dc_name_p95, {}).get("p_value") if dc_name_p95 else None
        dc_p95_str = f"{dc_p95:.4f}" if dc_p95 is not None else "N/A"
        print(f"  → P95 Subvariant ({p95_key}): DV=ercot_rtm_p95, "
              f"Adj R²={p95_data['adj_r_squared']:.4f}, DC p={dc_p95_str} "
              f"(NOT comparable to mean-DV variants)")

    diagnostics["selection_scores"] = selection_scores
    diagnostics["p95_scores"] = p95_scores

    # Save diagnostics
    # Convert non-serializable types
    def _clean_for_json(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {k: _clean_for_json(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_clean_for_json(v) for v in obj]
        return obj

    with open(REG_DIAGNOSTICS, "w") as f:
        json.dump(_clean_for_json(diagnostics), f, indent=2, default=str)
    print(f"\n  Saved diagnostics: {REG_DIAGNOSTICS}")

    return results, diagnostics


# ============================================================================
# STEP 14: EXPANDED VALIDATION
# ============================================================================

def step14_expanded_validation(reg_v2, diagnostics, master_v8):
    """Comprehensive validation: residual analysis, coefficient stability, rolling OOS."""
    banner("STEP 14: Expanded Model Validation")

    validation = {}

    # ------------------------------------------------------------------
    # 14A. Residual Correlation Against Unused Variables
    # ------------------------------------------------------------------
    print("\n  --- 14A. Residual Correlation Against Unused Variables ---")
    # Variables available in master but NOT in any regression spec
    unused_candidates = [
        "battery_pack_price_kwh", "battery_stationary_price_kwh",
        "us_ev_annual_charging_twh", "us_ev_peak_demand_increase_gw",
        "tx_wind_cf_variability_cv", "ca_solar_cf_variability_cv",
        "tx_wind_cf_annual_avg", "ca_solar_cf_annual_avg",
        "ercot_curtailment_gwh", "caiso_curtailment_gwh",
        "ercot_regulationup_price", "ercot_responsivereserve_price",
        "caiso_regulationup_price", "caiso_spinningreserve_price",
        "ercot_negative_hours_annual", "ercot_scarcity_revenue_million",
        "ercot_ordc_adder_max_mwh", "ercot_eea_events",
        "caiso_scarcity_hours", "caiso_eea_events",
        "ercot_price_cap_mwh",
    ]
    resid_corr = {}
    for market, resid_key in [("ercot", "ercot_full"), ("caiso", "caiso_full"), ("hh", "hh_full")]:
        # Try best variant first, then fall back to full
        best_key = diagnostics.get(f"{market}_best_variant")
        model = reg_v2.get(best_key) if best_key else None
        if model is None or "residuals" not in model:
            model = reg_v2.get(resid_key)
        if model is None or "residuals" not in model:
            continue

        residuals = np.array(model["residuals"])
        # Get the index from the model's data
        dv_map = {"ercot": "ercot_rtm_mean" if "ercot_rtm_mean" in master_v8.columns else "ercot_wholesale_mwh",
                   "caiso": "caiso_wholesale_mwh", "hh": "henry_hub_spot"}
        dv = dv_map.get(market)
        if dv is None or dv not in master_v8.columns:
            continue

        # Align residuals with master index
        used_vars = [c for c in model.get("coefficients", {}).keys() if c != "intercept"]
        all_cols = [dv] + [v for v in used_vars if v in master_v8.columns]
        df_aligned = master_v8[all_cols].dropna()
        if len(df_aligned) != len(residuals):
            # Try best effort alignment
            df_aligned = df_aligned.iloc[-len(residuals):]

        mkt_corr = {}
        for uvar in unused_candidates:
            if uvar not in master_v8.columns:
                continue
            # Align unused variable with residual index
            uvar_vals = master_v8.loc[df_aligned.index, uvar] if len(df_aligned) == len(residuals) else None
            if uvar_vals is None or uvar_vals.notna().sum() < 20:
                continue
            # Compute correlation where both are non-NaN
            valid = uvar_vals.notna()
            if valid.sum() < 20:
                continue
            r_vals = residuals[:valid.sum()] if len(residuals) >= valid.sum() else residuals
            u_vals = uvar_vals[valid].values[:len(r_vals)]
            if len(r_vals) != len(u_vals):
                min_len = min(len(r_vals), len(u_vals))
                r_vals, u_vals = r_vals[:min_len], u_vals[:min_len]
            if len(r_vals) < 20:
                continue
            try:
                corr = np.corrcoef(r_vals, u_vals)[0, 1]
                if np.isnan(corr):
                    continue
                mkt_corr[uvar] = round(float(corr), 4)
            except Exception:
                continue

        if mkt_corr:
            # Sort by absolute correlation
            sorted_corr = sorted(mkt_corr.items(), key=lambda x: abs(x[1]), reverse=True)
            resid_corr[market] = dict(sorted_corr)
            print(f"\n    {market.upper()} residual correlations with unused variables:")
            for var, corr in sorted_corr[:10]:
                sig = "***" if abs(corr) > 0.3 else "**" if abs(corr) > 0.2 else "*" if abs(corr) > 0.1 else ""
                print(f"      {var}: r={corr:.4f} {sig}")
                if abs(corr) > 0.3:
                    print(f"        → RECOMMEND adding to model: strong residual signal")

    validation["residual_correlations"] = resid_corr

    # ------------------------------------------------------------------
    # 14B. Coefficient Stability Across Sub-Periods
    # ------------------------------------------------------------------
    print("\n  --- 14B. Coefficient Stability Sub-Period Test ---")
    stability = {}
    for market in ["ercot", "caiso", "hh"]:
        best_key = diagnostics.get(f"{market}_best_variant")
        model = reg_v2.get(best_key) if best_key else None
        if model is None or "coefficients" not in model:
            continue

        dv_map = {"ercot": "ercot_rtm_mean" if "ercot_rtm_mean" in master_v8.columns else "ercot_wholesale_mwh",
                   "caiso": "caiso_wholesale_mwh", "hh": "henry_hub_spot"}
        dv = dv_map.get(market)
        if dv is None:
            continue

        used_vars = [c for c in model["coefficients"].keys() if c != "intercept"]
        valid_vars = [v for v in used_vars if v in master_v8.columns]
        df_sub = master_v8[[dv] + valid_vars].dropna()

        if len(df_sub) < 60:
            print(f"    {market.upper()}: insufficient data for sub-period test")
            continue

        # Split at midpoint
        mid = len(df_sub) // 2
        periods = {"first_half": df_sub.iloc[:mid], "second_half": df_sub.iloc[mid:]}

        period_results = {}
        for period_name, period_data in periods.items():
            if len(period_data) < len(valid_vars) + 5:
                period_results[period_name] = {"note": "insufficient observations"}
                continue
            X_p = _make_X(period_data, valid_vars)
            y_p = period_data[dv]
            X_p, _ = _prune_by_vif(X_p, y_p, threshold=10)
            ols_p = _ols(X_p, y_p, f"{market.upper()}_{period_name}")
            period_results[period_name] = {
                "coefficients": {k: v["coefficient"] for k, v in ols_p["coefficients"].items()},
                "r_squared": ols_p["r_squared"],
                "n": ols_p["n"],
            }

        stability[market] = period_results

        # Compare key coefficients
        print(f"\n    {market.upper()} coefficient stability (first half vs second half):")
        full_coefs = {k: v["coefficient"] for k, v in model["coefficients"].items() if k != "intercept"}
        h1 = period_results.get("first_half", {}).get("coefficients", {})
        h2 = period_results.get("second_half", {}).get("coefficients", {})
        for var in ["henry_hub_spot", "tx_wind_gen_pct", "ca_wind_gen_pct",
                     "ercot_scarcity_intensity", "ercot_spike_count",
                     "caiso_total_curtail_gwh", "caiso_monthly_negative_hours",
                     "tx_gas_gen_pct", "ca_gas_gen_pct"]:
            if var in full_coefs:
                v_full = full_coefs[var]
                v_h1 = h1.get(var, None)
                v_h2 = h2.get(var, None)
                h1_str = f"{v_h1:.4f}" if v_h1 is not None else "N/A"
                h2_str = f"{v_h2:.4f}" if v_h2 is not None else "N/A"
                sign_change = ""
                if v_h1 is not None and v_h2 is not None and v_h1 * v_h2 < 0:
                    sign_change = " *** SIGN CHANGE"
                print(f"      {var}: full={v_full:.4f}, H1={h1_str}, H2={h2_str}{sign_change}")

    validation["coefficient_stability"] = stability

    # ------------------------------------------------------------------
    # 14C. Expanding Window Out-of-Sample Validation
    # ------------------------------------------------------------------
    print("\n  --- 14C. Expanding Window OOS Validation ---")
    expanding_oos = {}
    for market in ["ercot", "caiso", "hh"]:
        best_key = diagnostics.get(f"{market}_best_variant")
        model = reg_v2.get(best_key) if best_key else None
        if model is None or "coefficients" not in model:
            continue

        dv_map = {"ercot": "ercot_rtm_mean" if "ercot_rtm_mean" in master_v8.columns else "ercot_wholesale_mwh",
                   "caiso": "caiso_wholesale_mwh", "hh": "henry_hub_spot"}
        dv = dv_map.get(market)
        if dv is None:
            continue

        used_vars = [c for c in model["coefficients"].keys() if c != "intercept"]
        valid_vars = [v for v in used_vars if v in master_v8.columns]
        df_oos = master_v8[[dv] + valid_vars].dropna()

        if len(df_oos) < 80:
            print(f"    {market.upper()}: insufficient data for expanding OOS")
            continue

        # Expanding window: start with 60% of data, expand by 12 months
        min_train = int(len(df_oos) * 0.6)
        window_results = []
        for train_end in range(min_train, len(df_oos) - 12, 12):
            train = df_oos.iloc[:train_end]
            test = df_oos.iloc[train_end:train_end + 12]
            if len(test) < 6:
                continue

            X_train = _make_X(train, valid_vars)
            y_train = train[dv].values
            X_np = X_train.values.astype(float)
            y_np = y_train.astype(float)
            mask = ~(np.isnan(X_np).any(axis=1) | np.isnan(y_np))
            X_np, y_np = X_np[mask], y_np[mask]
            if len(y_np) <= X_np.shape[1]:
                continue

            beta = np.linalg.lstsq(X_np, y_np, rcond=None)[0]

            # Predict
            X_test = _make_X(test, [c for c in X_train.columns if c != "intercept"])
            X_test_np = X_test.values.astype(float)
            y_test = test[dv].values
            mask_t = ~(np.isnan(X_test_np).any(axis=1) | np.isnan(y_test))
            X_test_np, y_test = X_test_np[mask_t], y_test[mask_t]
            if len(y_test) < 3:
                continue

            y_pred = X_test_np @ beta
            rmse = float(np.sqrt(np.mean((y_test - y_pred) ** 2)))
            mae = float(np.mean(np.abs(y_test - y_pred)))
            ss_res = np.sum((y_test - y_pred) ** 2)
            ss_tot = np.sum((y_test - y_test.mean()) ** 2)
            r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else 0

            window_results.append({
                "train_n": int(len(y_np)),
                "test_n": int(len(y_test)),
                "rmse": round(rmse, 2),
                "mae": round(mae, 2),
                "r2_oos": round(r2, 4),
            })

        if window_results:
            avg_rmse = np.mean([w["rmse"] for w in window_results])
            avg_r2 = np.mean([w["r2_oos"] for w in window_results])
            expanding_oos[market] = {
                "windows": window_results,
                "avg_rmse": round(float(avg_rmse), 2),
                "avg_r2_oos": round(float(avg_r2), 4),
                "n_windows": len(window_results),
            }
            print(f"    {market.upper()}: {len(window_results)} expanding windows, "
                  f"avg OOS R²={avg_r2:.4f}, avg RMSE=${avg_rmse:.2f}")
            for i, w in enumerate(window_results):
                print(f"      Window {i+1}: train={w['train_n']}, test={w['test_n']}, "
                      f"R²={w['r2_oos']:.4f}, RMSE=${w['rmse']:.2f}")

    validation["expanding_oos"] = expanding_oos

    # ------------------------------------------------------------------
    # 14D. Cross-Market Gas Passthrough Consistency
    # ------------------------------------------------------------------
    print("\n  --- 14D. Cross-Market Gas Passthrough Consistency ---")
    gas_passthroughs = {}
    for market in ["ercot", "caiso"]:
        best_key = diagnostics.get(f"{market}_best_variant")
        model = reg_v2.get(best_key) if best_key else None
        if model is None:
            continue
        hh_coef = model.get("coefficients", {}).get("henry_hub_spot", {})
        if hh_coef:
            gas_passthroughs[market] = {
                "coefficient": hh_coef.get("coefficient"),
                "p_value": hh_coef.get("p_value"),
                "variant": best_key,
            }
    for mkt, data in gas_passthroughs.items():
        coef = data["coefficient"]
        pval = data.get("p_value", "N/A")
        variant = data.get("variant", "?")
        print(f"    {mkt.upper()} gas passthrough: ${coef:.2f}/MWh per $/MMBtu "
              f"(p={pval}, variant={variant})")
    if len(gas_passthroughs) == 2:
        e_coef = gas_passthroughs["ercot"]["coefficient"]
        c_coef = gas_passthroughs["caiso"]["coefficient"]
        ratio = abs(e_coef / c_coef) if c_coef != 0 else float('inf')
        print(f"    Ratio: {ratio:.2f}x")
        gas_passthroughs["ratio"] = round(float(ratio), 2)
    elif len(gas_passthroughs) == 1:
        mkt = list(gas_passthroughs.keys())[0]
        coef = gas_passthroughs[mkt]["coefficient"]
        missing = "CAISO" if mkt == "ercot" else "ERCOT"
        print(f"    {missing}: henry_hub_spot dropped by VIF pruning in best variant")
    else:
        print(f"    No gas passthrough coefficients available")
    print(f"    Literature range: $7-9/MWh per $/MMBtu (heat rate based)")
    if gas_passthroughs:
        for mkt, data in gas_passthroughs.items():
            if not isinstance(data, dict):
                continue
            coef = data["coefficient"]
            if 5 <= coef <= 12:
                print(f"    {mkt.upper()}: PASS — within plausible range")
            elif coef > 15:
                print(f"    {mkt.upper()}: WARNING — passthrough elevated, "
                      f"scarcity premium may not be fully controlled")
    gas_passthroughs["literature_range"] = [7, 9]

    validation["gas_passthrough_consistency"] = gas_passthroughs

    # ------------------------------------------------------------------
    # 14E. Literature Benchmark Comparison
    # ------------------------------------------------------------------
    print("\n  --- 14E. Literature Benchmark Comparison ---")
    lit_comparison = {}
    if os.path.exists(LITERATURE_BENCHMARKS):
        with open(LITERATURE_BENCHMARKS) as f:
            lit = json.load(f)
        print(f"    Loaded: {LITERATURE_BENCHMARKS}")

        # 14E-1: GARCH parameters vs published studies
        garch_lit = lit.get("garch_parameters", {})
        garch_consensus = garch_lit.get("literature_consensus", {})
        garch_path = GARCH_V4 if os.path.exists(GARCH_V4) else os.path.join(BASE, "fuel_costs_and_supply", "garch_volatility_model_v4.json")
        if os.path.exists(garch_path):
            with open(garch_path) as f:
                garch_current = json.load(f)
            params = garch_current.get("parameters", {})
            ann_vol = garch_current.get("unconditional_volatility", {}).get("annualized_pct", 0)
            garch_checks = {}
            print(f"\n    GARCH Parameter Comparison:")
            for p_name, p_key in [("alpha", "alpha"), ("beta", "beta"), ("persistence", "persistence")]:
                val = params.get(p_key, 0)
                lit_range = garch_consensus.get(p_name, [0, 1])
                in_range = lit_range[0] <= val <= lit_range[1] if isinstance(lit_range, list) else False
                status = "PASS" if in_range else "OUTSIDE RANGE"
                garch_checks[p_name] = {"value": val, "literature_range": lit_range, "status": status}
                print(f"      {p_name}: {val:.3f} (literature {lit_range[0]:.2f}-{lit_range[1]:.2f}) — {status}")
            # Annualized vol
            vol_range = garch_consensus.get("annualized_vol_pct", [30, 55])
            vol_status = "PASS" if vol_range[0] <= ann_vol <= vol_range[1] else "HIGH" if ann_vol > vol_range[1] else "LOW"
            garch_checks["annualized_vol_pct"] = {"value": round(ann_vol, 1), "literature_range": vol_range, "status": vol_status}
            print(f"      annualized_vol: {ann_vol:.1f}% (literature {vol_range[0]}-{vol_range[1]}%) — {vol_status}")
            # Show per-era results if multi-era GARCH was used
            sel_era = garch_current.get("selected_era", "")
            era_data = garch_current.get("eras", {})
            if era_data:
                print(f"      Selected era: {sel_era}")
                garch_checks["selected_era"] = sel_era
                era_summary = {}
                for ek, ev in era_data.items():
                    if not ev.get("skipped"):
                        ea = ev.get("annualized_vol_pct", 0)
                        e_status = "PASS" if vol_range[0] <= ea <= vol_range[1] else ("HIGH" if ea > vol_range[1] else "LOW")
                        era_summary[ek] = {"ann_vol": round(ea, 1), "status": e_status}
                        marker = " <<<" if ek == sel_era else ""
                        print(f"        {ev.get('label', ek)}: vol={ea:.1f}% — {e_status}{marker}")
                garch_checks["eras"] = era_summary
            lit_comparison["garch"] = garch_checks

        # 14E-2: Gas price forecasts vs EIA/consensus
        gas_consensus = lit.get("gas_price_forecasts", {}).get("consensus_range", {})
        mc_path = MC_V8 if os.path.exists(MC_V8) else MC_V8_EXISTING
        if os.path.exists(mc_path) and gas_consensus:
            with open(mc_path) as f:
                mc_data = json.load(f)
            forecasts = mc_data.get("annual_forecasts", {})
            gas_checks = {}
            print(f"\n    Gas Forecast vs Consensus:")
            print(f"      {'Year':<6} {'P50':>8} {'Mean':>8} {'Consensus':>16} {'Status':>8}")
            for yr_s, consensus in gas_consensus.items():
                if not isinstance(consensus, dict):
                    continue
                fc = forecasts.get(yr_s, {})
                p50 = fc.get("median", 0)
                mean = fc.get("mean", 0)
                c_low, c_mid, c_high = consensus.get("low", 0), consensus.get("mid", 0), consensus.get("high", 0)
                in_range = c_low <= p50 <= c_high
                status = "PASS" if in_range else "HIGH" if p50 > c_high else "LOW"
                gas_checks[yr_s] = {"p50": round(p50, 2), "mean": round(mean, 2),
                                     "consensus_low": c_low, "consensus_mid": c_mid, "consensus_high": c_high,
                                     "status": status}
                print(f"      {yr_s:<6} ${p50:>7.2f} ${mean:>7.2f}  ${c_low:.2f}-${c_mid:.2f}-${c_high:.2f}  {status}")
            lit_comparison["gas_forecasts"] = gas_checks

        # 14E-3: Gas passthrough vs literature
        pt_lit = lit.get("gas_passthrough", {})
        pt_range = pt_lit.get("theoretical_range", {})
        if pt_range and gas_passthroughs:
            pt_low, pt_high = pt_range.get("low", 7), pt_range.get("high", 9)
            pt_checks = {}
            print(f"\n    Gas Passthrough vs Literature (${pt_low}-${pt_high}/MWh per $/MMBtu):")
            for mkt, data in gas_passthroughs.items():
                if not isinstance(data, dict) or "coefficient" not in data:
                    continue
                coef = data["coefficient"]
                status = "PASS" if pt_low * 0.7 <= coef <= pt_high * 1.3 else "OUTSIDE"
                pt_checks[mkt] = {"coefficient": coef, "range": [pt_low, pt_high], "status": status}
                print(f"      {mkt.upper()}: ${coef:.2f} — {status}")
            lit_comparison["gas_passthrough"] = pt_checks

        # 14E-4: Event probabilities vs empirical
        event_lit = lit.get("event_probabilities", {})
        if event_lit:
            event_checks = {}
            print(f"\n    Event Probabilities vs Empirical Catalog:")
            for cat in ["geopolitical", "demand_shock", "policy_shock"]:
                cat_data = event_lit.get(cat, {})
                emp_rate = cat_data.get("empirical_annual_rate", 0)
                if emp_rate:
                    event_checks[cat] = {"empirical_annual_rate": emp_rate,
                                          "events_qualifying": cat_data.get("events_with_us_gas_impact_gt_1pct", 0),
                                          "total_events": cat_data.get("total_events_in_catalog", 0)}
                    print(f"      {cat}: {emp_rate:.3f}/yr ({cat_data.get('events_with_us_gas_impact_gt_1pct', '?')}"
                          f" qualifying events in {cat_data.get('total_events_in_catalog', '?')} total)")
            lit_comparison["event_probabilities"] = event_checks

        # 14E-5: Hurricane frequency vs IPCC consensus
        hurricane_lit = lit.get("hurricane_frequency", {}).get("consensus", {})
        if hurricane_lit:
            trend = hurricane_lit.get("frequency_trend_per_decade", 0)
            print(f"\n    Hurricane Frequency: literature consensus = {trend:+.1f}/decade (flat)")
            lit_comparison["hurricane_frequency"] = {"consensus_trend_per_decade": trend}

        # 14E-6: Electricity price benchmarks
        elec_lit = lit.get("electricity_price_benchmarks", {})
        if elec_lit:
            print(f"\n    Electricity Price Benchmarks:")
            elec_checks = {}
            for mkt_key, mkt_label in [("ercot", "ERCOT"), ("caiso", "CAISO")]:
                mkt_lit = elec_lit.get(mkt_key, {})
                typical = mkt_lit.get("typical_range", [])
                hist = mkt_lit.get("historical_avg_2019_2024", {}).get("values", {})
                if typical:
                    print(f"      {mkt_label} typical range: ${typical[0]}-${typical[1]}/MWh")
                    elec_checks[mkt_key] = {"typical_range": typical, "historical": hist}
            lit_comparison["electricity_prices"] = elec_checks

        # 14E-7: Production economics floor check
        floor_lit = lit.get("production_economics_floor", {}).get("breakeven_estimates", {}).get("overall_us_marginal", {})
        if floor_lit:
            floor_mid = floor_lit.get("mid", 2.0)
            # Check P10 values
            if os.path.exists(mc_path):
                p10_issues = []
                for yr_s, fc in forecasts.items():
                    p10 = fc.get("p10", 0)
                    if isinstance(p10, (int, float)) and p10 < floor_mid:
                        p10_issues.append(f"{yr_s}: P10=${p10:.2f}")
                if p10_issues:
                    print(f"\n    Production Floor Check (marginal cost ~${floor_mid:.2f}/MMBtu):")
                    print(f"      WARNING: P10 below production floor in: {', '.join(p10_issues)}")
                    lit_comparison["production_floor"] = {"floor_mid": floor_mid, "violations": p10_issues}
                else:
                    print(f"\n    Production Floor Check: PASS — all P10 >= ${floor_mid:.2f}")
                    lit_comparison["production_floor"] = {"floor_mid": floor_mid, "violations": []}

    else:
        print(f"    WARNING: {LITERATURE_BENCHMARKS} not found — skipping literature comparison")
        print(f"    Create this file to enable automated benchmark validation")

    validation["literature_comparison"] = lit_comparison

    # Save expanded validation results
    with open(VALIDATION_EXPANDED, "w") as f:
        json.dump(validation, f, indent=2, default=str)
    print(f"\n  Saved: {VALIDATION_EXPANDED}")
    print(f"\n  Validation complete.")
    return validation


# ============================================================================
# STEP 13: MODEL DIAGNOSTICS REPORT
# ============================================================================

def step13_model_diagnostics(reg_v2, diagnostics, master_v8):
    """Comprehensive diagnostic report and visualization."""
    banner("STEP 13: Model Diagnostics Report")

    # Print summary
    print("\n  === DIAGNOSTIC SUMMARY ===")

    # Granger causality summary
    gc = diagnostics.get("granger_causality", {})
    if gc:
        print("\n  Granger Causality:")
        for pair, lags in gc.items():
            if isinstance(lags, dict):
                sig_lags = [k for k, v in lags.items() if isinstance(v, dict) and v.get("p_value", 1) < 0.05]
                status = f"significant at {sig_lags}" if sig_lags else "NOT significant"
                print(f"    {pair}: {status}")

    # Best variants
    print("\n  Best Model Variants:")
    for mkt in ["ercot", "caiso", "hh"]:
        best = diagnostics.get(f"{mkt}_best_variant", "N/A")
        score = diagnostics.get("selection_scores", {}).get(best, "N/A")
        print(f"    {mkt.upper()}: {best} (score={score})")

    # Data quality issues
    if os.path.exists(DATA_QUALITY):
        with open(DATA_QUALITY) as f:
            dq = json.load(f)
        n_warnings = sum(1 for v in dq.values()
                         if isinstance(v, dict) and (v.get("missing_pct", 0) > 50 or
                                                      v.get("suspected_backfill", False)))
        print(f"\n  Data Quality: {n_warnings} variables with warnings")

    # Render diagnostic charts
    _render_diagnostic_charts(reg_v2, diagnostics, master_v8)

    print("\n  Step 13 complete.")
    return diagnostics


def _render_diagnostic_charts(reg_v2, diagnostics, master_v8):
    """6-panel diagnostic visualization."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  WARNING: matplotlib not available — skipping diagnostic charts")
        return

    BG = "#1a1a2e"
    AX_BG = "#16213e"
    ACCENT = ["#00d2ff", "#ff6b6b", "#ffd93d", "#6bcb77", "#c084fc", "#ff922b"]

    fig, axes = plt.subplots(2, 3, figsize=(20, 12), facecolor=BG)
    for ax in axes.flat:
        ax.set_facecolor(AX_BG)
        ax.tick_params(colors="#ccc", labelsize=7)
        for spine in ax.spines.values():
            spine.set_color("#333")

    # Panel 1: Rolling DC Coefficient
    ax1 = axes[0, 0]
    for i, mkt in enumerate(["ercot", "caiso", "hh"]):
        rolling = diagnostics.get(f"{mkt}_rolling", [])
        dc_var = {"ercot": "tx_data_center_twh", "caiso": "ca_data_center_twh",
                  "hh": "us_data_center_twh"}.get(mkt)
        if rolling and dc_var:
            dc_vals = [r.get(dc_var) for r in rolling if dc_var in r]
            if dc_vals:
                ax1.plot(range(len(dc_vals)), dc_vals, color=ACCENT[i], label=mkt.upper(), linewidth=1.5)
    ax1.axhline(0, color="#555", linewidth=0.5)
    ax1.set_title("Rolling DC Coefficient (60-month)", color="white", fontsize=10, fontweight="bold")
    ax1.set_ylabel("Coefficient", color="#aaa", fontsize=8)
    ax1.legend(fontsize=7, facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc")
    ax1.grid(True, alpha=0.15, color="#555")

    # Panel 2: Regime Split Comparison
    ax2 = axes[0, 1]
    regime_data = []
    for mkt in ["ercot", "caiso", "hh"]:
        dc_var = {"ercot": "tx_data_center_twh", "caiso": "ca_data_center_twh",
                  "hh": "us_data_center_twh"}.get(mkt)
        e_result = reg_v2.get(f"{mkt}_E", {})
        if isinstance(e_result, dict):
            for regime in ["scarcity", "normal"]:
                if regime in e_result and isinstance(e_result[regime], dict) and "coefficients" in e_result[regime]:
                    dc_coef = e_result[regime]["coefficients"].get(dc_var, {}).get("coefficient", 0)
                    regime_data.append((mkt.upper(), regime, dc_coef))

    if regime_data:
        x_pos = 0
        for mkt_label in ["ERCOT", "CAISO", "HH"]:
            mkt_regimes = [(r, c) for m, r, c in regime_data if m == mkt_label]
            for j, (regime, coef) in enumerate(mkt_regimes):
                color = ACCENT[0] if regime == "scarcity" else ACCENT[1]
                ax2.bar(x_pos, coef, width=0.4, color=color,
                        label=regime if x_pos < 2 else "", alpha=0.8)
                x_pos += 0.5
            x_pos += 0.5
    ax2.set_title("Regime Split: DC Coefficient", color="white", fontsize=10, fontweight="bold")
    ax2.axhline(0, color="#555", linewidth=0.5)
    ax2.legend(fontsize=7, facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc")
    ax2.grid(True, axis="y", alpha=0.15, color="#555")

    # Panel 3: OOS Validation scatter
    ax3 = axes[0, 2]
    oos = diagnostics.get("oos_validation", {})
    for i, (var_key, oos_data) in enumerate(oos.items()):
        if isinstance(oos_data, dict) and "r2_out_of_sample" in oos_data:
            r2 = oos_data["r2_out_of_sample"]
            ax3.bar(i, r2, color=ACCENT[i % len(ACCENT)], alpha=0.8)
            ax3.text(i, r2 + 0.01, var_key.replace("_", "\n"), fontsize=5,
                     color="#ccc", ha="center", va="bottom")
    ax3.set_title("Out-of-Sample R²", color="white", fontsize=10, fontweight="bold")
    ax3.set_ylabel("R² (OOS)", color="#aaa", fontsize=8)
    ax3.axhline(0, color="#555", linewidth=0.5)
    ax3.grid(True, axis="y", alpha=0.15, color="#555")

    # Panel 4: Quantile Regression Coefficients (ERCOT)
    ax4 = axes[1, 0]
    qr = diagnostics.get("ercot_D", {}).get("quantile_regression", {})
    if qr:
        taus = []
        coefs = []
        colors = []
        for tau_key in ["tau_0.5", "tau_0.9", "tau_0.95", "tau_0.99"]:
            if tau_key in qr and isinstance(qr[tau_key], dict) and "tx_data_center_twh" in qr[tau_key]:
                tau_val = float(tau_key.split("_")[1])
                taus.append(f"τ={tau_val}")
                coefs.append(qr[tau_key]["tx_data_center_twh"])
                colors.append("#666" if tau_val == 0.99 else ACCENT[0])
        if taus:
            bars = ax4.bar(range(len(taus)), coefs, color=colors, alpha=0.8)
            ax4.set_xticks(range(len(taus)))
            labels = [f"{t}" + ("\n[exploratory]" if "0.99" in t else "") for t in taus]
            ax4.set_xticklabels(labels, fontsize=7, color="#ccc")
    ax4.set_title("ERCOT Quantile Regression: DC Coeff", color="white", fontsize=10, fontweight="bold")
    ax4.axhline(0, color="#555", linewidth=0.5)
    ax4.grid(True, axis="y", alpha=0.15, color="#555")

    # Panel 5: VIF Heatmap
    ax5 = axes[1, 1]
    vif_data = {}
    for var_key in sorted(diagnostics.keys()):
        if isinstance(diagnostics[var_key], dict) and "vif" in diagnostics[var_key]:
            vif_data[var_key] = diagnostics[var_key]["vif"]
    if vif_data:
        all_vars_set = set()
        for v in vif_data.values():
            all_vars_set.update(v.keys())
        all_vars_list = sorted(all_vars_set)[:15]  # Limit for readability
        model_names = list(vif_data.keys())[:8]
        matrix = np.full((len(model_names), len(all_vars_list)), np.nan)
        for i, mn in enumerate(model_names):
            for j, vn in enumerate(all_vars_list):
                matrix[i, j] = vif_data[mn].get(vn, np.nan)
        im = ax5.imshow(matrix, aspect="auto", cmap="RdYlGn_r", vmin=1, vmax=15)
        ax5.set_xticks(range(len(all_vars_list)))
        ax5.set_xticklabels([v[:15] for v in all_vars_list], fontsize=5, rotation=45, ha="right", color="#ccc")
        ax5.set_yticks(range(len(model_names)))
        ax5.set_yticklabels([m[:12] for m in model_names], fontsize=6, color="#ccc")
        plt.colorbar(im, ax=ax5, shrink=0.6)
    ax5.set_title("VIF by Model × Variable", color="white", fontsize=10, fontweight="bold")

    # Panel 6: Cook's Distance
    ax6 = axes[1, 2]
    for i, mkt in enumerate(["ercot", "caiso", "hh"]):
        infl = diagnostics.get(f"{mkt}_A", {}).get("influence", {})
        if "cooks_d" in infl:
            cd = infl["cooks_d"]
            ax6.plot(range(len(cd)), cd, color=ACCENT[i], label=mkt.upper(), alpha=0.7, linewidth=0.8)
            ax6.axhline(infl.get("threshold", 0.02), color=ACCENT[i], linestyle="--", alpha=0.3)
    ax6.set_title("Cook's Distance", color="white", fontsize=10, fontweight="bold")
    ax6.set_ylabel("Cook's D", color="#aaa", fontsize=8)
    ax6.legend(fontsize=7, facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc")
    ax6.grid(True, alpha=0.15, color="#555")

    fig.suptitle("DecarbIQ Model Diagnostics", color="white", fontsize=16, fontweight="bold", y=0.98)
    plt.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(DIAG_PNG, dpi=200, bbox_inches="tight", facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"  Saved: {DIAG_PNG}")


# ============================================================================
# STEP 3: UPDATE GARCH
# ============================================================================
def step3_garch(reg_results, master=None):
    banner("STEP 3: Update GARCH(1,1) on HH daily log-returns")

    garch_path = GARCH_V4_EXISTING if os.path.exists(GARCH_V4_EXISTING) else GARCH_V3
    with open(garch_path) as f:
        old_garch = json.load(f)
    print(f"  Reading from: {os.path.basename(garch_path)}")

    # Use daily HH spot prices (EIA data) for GARCH estimation
    # Daily data captures volatility clustering that monthly data smooths away
    # Literature GARCH studies use daily frequency
    HH_DAILY_XLS = os.path.join(BASE, "fuel_costs_and_supply", "henry_hub_daily_gas.xls")
    prices = None
    freq = "daily"
    trading_days_per_month = 21

    if os.path.exists(HH_DAILY_XLS):
        try:
            df_daily = pd.read_excel(HH_DAILY_XLS, sheet_name="Data 1", skiprows=2)
            df_daily.columns = ["date", "price"]
            df_daily["date"] = pd.to_datetime(df_daily["date"], errors="coerce")
            df_daily["price"] = pd.to_numeric(df_daily["price"], errors="coerce")
            df_daily = df_daily.dropna(subset=["date", "price"])
            # Exclude Feb 2021 (Uri) — extreme spike distorts GARCH estimation
            uri_mask = (df_daily["date"] >= "2021-02-01") & (df_daily["date"] <= "2021-02-28")
            n_uri = uri_mask.sum()
            if n_uri > 0:
                df_daily = df_daily[~uri_mask]
                print(f"  Excluded {n_uri} Uri days (Feb 2021) from GARCH estimation")
            prices = df_daily["price"].values
            price_dates = df_daily["date"].values
            start_date = str(df_daily["date"].min().date())
            end_date = str(df_daily["date"].max().date())
            print(f"  Using daily HH data: {len(prices)} observations ({start_date} to {end_date})")
        except Exception as e:
            print(f"  Warning: could not read daily HH data: {e}")

    # Fallback to monthly if daily not available
    if prices is None or len(prices) < 100:
        freq = "monthly"
        trading_days_per_month = 1  # already monthly
        if master is not None and "henry_hub_spot" in master.columns:
            price_series = master[["henry_hub_spot", "year", "month"]].dropna(subset=["henry_hub_spot"]).copy()
            uri_mask = (price_series["year"] == 2021) & (price_series["month"] == 2)
            n_uri = uri_mask.sum()
            if n_uri > 0:
                price_series = price_series[~uri_mask]
                print(f"  Excluded {n_uri} Uri observation(s) (Feb 2021) from GARCH estimation")
            prices = price_series["henry_hub_spot"].values
            print(f"  Fallback to monthly HH data: {len(prices)} observations")

    if prices is None or len(prices) < 24:
        print("  No HH price data available — keeping existing GARCH")
        return old_garch

    # Log-returns: standard input for GARCH on commodity prices
    raw_log_returns = np.diff(np.log(prices))
    raw_n = len(raw_log_returns)
    raw_return_dates = pd.Series(price_dates[1:]) if freq == "daily" else None
    print(f"  {freq.capitalize()} log-returns (raw): {raw_n} observations, mean={np.mean(raw_log_returns):.6f}, "
          f"std={np.std(raw_log_returns):.4f}, var={np.var(raw_log_returns):.6f}")

    # ---- Jump/spike filtering ----
    # Literature: Lee & Mykland (2008), Andersen et al. (2007)
    # Extreme price jumps inflate GARCH alpha (fat-tail absorption into diffusion).
    # Separate: diffusion volatility (GARCH) vs jump risk (shock catalog).
    robust_sigma = 1.4826 * float(np.median(np.abs(raw_log_returns - np.median(raw_log_returns))))
    JUMP_K = 3.0  # standard threshold in jump-diffusion literature
    jump_threshold = JUMP_K * robust_sigma
    jump_mask_stat = np.abs(raw_log_returns) > jump_threshold

    # Event-based detection: flag dates near known disruptive events
    disrupt_path = os.path.join(BASE, "geopolitical_and_macro", "geopolitical_macro_disruptions.csv")
    event_dates_set = set()
    event_lookup = {}
    if os.path.exists(disrupt_path):
        try:
            df_events = pd.read_csv(disrupt_path)
            for _, row in df_events.iterrows():
                ev_start = pd.to_datetime(row["date"], errors="coerce")
                ev_end_raw = row.get("end_date")
                ev_end = pd.to_datetime(ev_end_raw, errors="coerce") if pd.notna(ev_end_raw) else ev_start
                if pd.isna(ev_start):
                    continue
                if pd.isna(ev_end):
                    ev_end = ev_start
                # Buffer: event day ± 2 trading days to catch adjacent reactions
                for d in pd.date_range(ev_start - pd.Timedelta(days=3), ev_end + pd.Timedelta(days=3)):
                    event_dates_set.add(d)
                    event_lookup[d] = str(row.get("event_name", "unknown"))
            print(f"  Loaded {len(df_events)} events from disruptions database")
        except Exception as e:
            print(f"  Warning: could not load disruptions database: {e}")

    if freq == "daily" and raw_return_dates is not None:
        rd_dt = pd.to_datetime(raw_return_dates)
        jump_mask_event = np.array([d in event_dates_set for d in rd_dt])
    else:
        jump_mask_event = np.zeros(raw_n, dtype=bool)

    jump_mask = jump_mask_stat | jump_mask_event
    n_jumps = int(jump_mask.sum())
    n_stat_only = int((jump_mask_stat & ~jump_mask_event).sum())
    n_event_only = int((jump_mask_event & ~jump_mask_stat).sum())
    n_both = int((jump_mask_stat & jump_mask_event).sum())

    # Build and save shock catalog for volatility studies
    shock_catalog = []
    for idx in np.where(jump_mask)[0]:
        entry = {
            "log_return": round(float(raw_log_returns[idx]), 6),
            "pct_change": round(float((np.exp(raw_log_returns[idx]) - 1) * 100), 2),
            "z_score": round(float(raw_log_returns[idx] / robust_sigma), 2),
            "detection": [],
        }
        if freq == "daily" and raw_return_dates is not None:
            entry["date"] = str(pd.to_datetime(raw_return_dates.iloc[idx]).date())
            entry["price_before"] = round(float(prices[idx]), 3)
            entry["price_after"] = round(float(prices[idx + 1]), 3)
        if jump_mask_stat[idx]:
            entry["detection"].append("statistical")
        if jump_mask_event[idx]:
            entry["detection"].append("event_matched")
            d = pd.to_datetime(raw_return_dates.iloc[idx]) if raw_return_dates is not None else None
            if d is not None and d in event_lookup:
                entry["event_name"] = event_lookup[d]
        shock_catalog.append(entry)

    shock_output = {
        "description": "Price jumps and volatility spikes filtered from HH daily data before GARCH estimation",
        "purpose": "Separated for volatility studies - real-case shock/event data",
        "method": f"Statistical (|log_return| > {JUMP_K}x MAD-sigma) + event date matching from disruptions DB",
        "robust_sigma_daily": round(float(robust_sigma), 6),
        "threshold_log_return": round(float(jump_threshold), 6),
        "threshold_pct_approx": round(float((np.exp(jump_threshold) - 1) * 100), 1),
        "n_total_returns": raw_n,
        "n_jumps_removed": n_jumps,
        "n_statistical_only": n_stat_only,
        "n_event_matched_only": n_event_only,
        "n_both": n_both,
        "pct_removed": round(float(n_jumps / raw_n * 100), 2),
        "shocks": sorted(shock_catalog, key=lambda x: abs(x["log_return"]), reverse=True),
    }
    shock_path = os.path.join(BASE, "fuel_costs_and_supply", "hh_shock_catalog.json")
    with open(shock_path, "w") as f:
        json.dump(shock_output, f, indent=2)

    print(f"\n  --- Jump Filtering (Lee & Mykland 2008) ---")
    print(f"  Robust sigma (MAD): {robust_sigma:.6f}")
    print(f"  Threshold: {JUMP_K:.0f}x sigma = {jump_threshold:.4f} ({(np.exp(jump_threshold)-1)*100:.1f}% price move)")
    print(f"  Removed: {n_jumps} of {raw_n} returns ({n_jumps/raw_n*100:.1f}%)")
    print(f"    Statistical only: {n_stat_only}, Event only: {n_event_only}, Both: {n_both}")
    print(f"  Saved shock catalog: {os.path.basename(shock_path)} ({n_jumps} events)")

    # Use filtered returns for GARCH estimation
    log_returns = raw_log_returns[~jump_mask]
    n = len(log_returns)
    var_r = np.var(log_returns)
    print(f"  Filtered returns: {n} obs, std={np.std(log_returns):.4f} (was {np.std(raw_log_returns):.4f})")

    # Filtered return dates (for era splitting and seasonal)
    if freq == "daily" and raw_return_dates is not None:
        return_dates = raw_return_dates[~jump_mask].reset_index(drop=True)
    else:
        return_dates = None

    # Compute seasonal volatility ratios from filtered daily data
    seasonal_vol_ratio = {}
    seasonal_mean_premium = {}
    if freq == "daily" and return_dates is not None:
        import calendar
        return_months = return_dates.dt.month.values
        overall_std = np.std(log_returns)
        overall_mean_price = np.mean(prices)  # unfiltered price levels for seasonal premium
        price_months = pd.Series(price_dates).dt.month.values
        for mo in range(1, 13):
            r_mask = (return_months == mo)
            if r_mask.sum() > 30:
                seasonal_vol_ratio[str(mo)] = round(float(np.std(log_returns[r_mask]) / overall_std), 3)
            else:
                seasonal_vol_ratio[str(mo)] = 1.0
            p_mask = (price_months == mo)
            if p_mask.sum() > 30:
                seasonal_mean_premium[str(mo)] = round(float(np.mean(prices[p_mask]) - overall_mean_price), 2)
            else:
                seasonal_mean_premium[str(mo)] = 0.0
        print(f"\n  --- Seasonal Volatility Ratios (from {n} filtered daily log-returns) ---")
        print(f"  {'Month':<6} {'Vol Ratio':>10} {'Mean Premium':>14}")
        for mo in range(1, 13):
            mname = calendar.month_abbr[mo]
            vr = seasonal_vol_ratio[str(mo)]
            mp = seasonal_mean_premium[str(mo)]
            flag = " ***" if vr > 1.5 else (" **" if vr > 1.1 else "")
            print(f"  {mname:<6} {vr:>10.3f}x {mp:>+13.2f}{flag}")
    else:
        for mo in range(1, 13):
            seasonal_vol_ratio[str(mo)] = 1.0
            seasonal_mean_premium[str(mo)] = 0.0

    # ---- Multi-era GARCH estimation ----
    # Natural gas volatility structure differs across market regimes:
    #   Pre-shale (before 2009): tight supply, weather-driven spikes, high vol
    #   Post-shale (2009-2019): abundant shale gas supply, lower vol
    #   Post-COVID (2020+): LNG exports, geopolitical shocks, intermediate vol
    ann_factor = 252 if freq == "daily" else 12

    ERA_DEFS = [
        ("pre_shale",  None,         "2009-01-01", "Pre-shale (<2009)"),
        ("post_shale", "2009-01-01", "2020-01-01", "Post-shale (2009-19)"),
        ("post_covid", "2020-01-01", None,         "Post-COVID (2020+)"),
    ]
    # return_dates already defined above (jump-filtered)
    eras = {}

    print(f"\n  --- Multi-Era GARCH Estimation ---")
    for era_name, era_start, era_end, era_label in ERA_DEFS:
        if freq == "daily" and return_dates is not None:
            mask = np.ones(len(log_returns), dtype=bool)
            if era_start:
                mask &= (return_dates >= era_start).values
            if era_end:
                mask &= (return_dates < era_end).values
            era_returns = log_returns[mask]
        else:
            # Monthly fallback — split by index proportionally
            n3 = len(log_returns) // 3
            if era_name == "pre_shale":
                era_returns = log_returns[:n3]
            elif era_name == "post_shale":
                era_returns = log_returns[n3:2*n3]
            else:
                era_returns = log_returns[2*n3:]

        if len(era_returns) < 100:
            print(f"  {era_label}: {len(era_returns)} returns — too few, skipping")
            eras[era_name] = {"n": int(len(era_returns)), "skipped": True, "label": era_label}
            continue

        o_e, a_e, b_e = _fit_garch(era_returns)
        p_e = a_e + b_e
        v_e = float(np.var(era_returns))
        uv_e = o_e / (1 - p_e) if 0 < p_e < 1 else v_e
        ann_e = float(np.sqrt(uv_e * ann_factor) * 100)

        eras[era_name] = {
            "n": int(len(era_returns)),
            "label": era_label,
            "skipped": False,
            "omega": float(o_e),
            "alpha": float(a_e),
            "beta": float(b_e),
            "persistence": float(p_e),
            "sample_variance": v_e,
            "unconditional_variance": float(uv_e),
            "annualized_vol_pct": ann_e,
        }
        print(f"  {era_label}: n={len(era_returns):,}, alpha={a_e:.4f}, beta={b_e:.4f}, "
              f"pers={p_e:.4f}, ann_vol={ann_e:.1f}%")

    # Full-sample fit (reference and conditional variance series)
    omega_full, alpha_full, beta_full = _fit_garch(log_returns)
    pers_full = alpha_full + beta_full
    uv_full = omega_full / (1 - pers_full) if 0 < pers_full < 1 else var_r
    ann_full = float(np.sqrt(uv_full * ann_factor) * 100)
    print(f"  Full sample: n={n:,}, alpha={alpha_full:.4f}, beta={beta_full:.4f}, "
          f"pers={pers_full:.4f}, ann_vol={ann_full:.1f}%")

    # Select era for MC simulation
    ps = eras.get("post_shale", {})
    pc = eras.get("post_covid", {})

    if pc and not pc.get("skipped") and ps and not ps.get("skipped"):
        pers_diff = abs(pc["persistence"] - ps["persistence"])
        vol_diff = abs(pc["annualized_vol_pct"] - ps["annualized_vol_pct"])
        print(f"\n  Post-shale vs Post-COVID comparison:")
        print(f"    Persistence: {ps['persistence']:.4f} vs {pc['persistence']:.4f} (diff={pers_diff:.4f})")
        print(f"    Ann. vol:    {ps['annualized_vol_pct']:.1f}% vs {pc['annualized_vol_pct']:.1f}% (diff={vol_diff:.1f}%)")
        if pers_diff < 0.03 and vol_diff < 15:
            # Not materially different — combine into 'modern' era (2009+)
            print(f"    -> SIMILAR — combining into modern era (2009+)")
            if freq == "daily" and return_dates is not None:
                modern_mask = (return_dates >= "2009-01-01").values
                modern_returns = log_returns[modern_mask]
            else:
                n3 = len(log_returns) // 3
                modern_returns = log_returns[n3:]
            o_m, a_m, b_m = _fit_garch(modern_returns)
            p_m = a_m + b_m
            uv_m = o_m / (1 - p_m) if 0 < p_m < 1 else float(np.var(modern_returns))
            ann_m = float(np.sqrt(uv_m * ann_factor) * 100)
            eras["modern_2009plus"] = {
                "n": int(len(modern_returns)),
                "label": "Modern era (2009+)",
                "skipped": False,
                "omega": float(o_m), "alpha": float(a_m), "beta": float(b_m),
                "persistence": float(p_m),
                "unconditional_variance": float(uv_m),
                "annualized_vol_pct": ann_m,
            }
            selected_era = "modern_2009plus"
            omega, alpha, beta = o_m, a_m, b_m
            print(f"    Modern (2009+): n={len(modern_returns):,}, alpha={a_m:.4f}, beta={b_m:.4f}, "
                  f"pers={p_m:.4f}, ann_vol={ann_m:.1f}%")
        else:
            # Materially different — use post-COVID as most recent regime
            print(f"    -> DIFFERENT — using post_covid era for MC simulation")
            selected_era = "post_covid"
            omega, alpha, beta = pc["omega"], pc["alpha"], pc["beta"]
    elif pc and not pc.get("skipped"):
        selected_era = "post_covid"
        omega, alpha, beta = pc["omega"], pc["alpha"], pc["beta"]
    elif ps and not ps.get("skipped"):
        selected_era = "post_shale"
        omega, alpha, beta = ps["omega"], ps["alpha"], ps["beta"]
    else:
        selected_era = "full_sample"
        omega, alpha, beta = omega_full, alpha_full, beta_full

    persistence = alpha + beta
    print(f"\n  *** Selected for MC: {selected_era} -> alpha={alpha:.4f}, beta={beta:.4f}, pers={persistence:.4f}")

    # Conditional variances using selected-era parameters on full return series
    h = np.zeros(n)
    h[0] = var_r
    for t in range(1, n):
        h[t] = omega + alpha * log_returns[t-1]**2 + beta * h[t-1]

    uncond_var = omega / (1 - persistence) if 0 < persistence < 1 else var_r
    uncond_vol_period = np.sqrt(uncond_var)
    uncond_vol_a = uncond_vol_period * np.sqrt(ann_factor)
    half_life_periods = np.log(2) / (-np.log(persistence)) if 0 < persistence < 1 else np.inf
    half_life_months = half_life_periods / trading_days_per_month if freq == "daily" else half_life_periods
    curr_var = h[-1]
    curr_vol_a = np.sqrt(curr_var * ann_factor) * 100

    # Regime thresholds for annualized vol (% terms)
    vol_series = np.sqrt(h * ann_factor) * 100
    regime_dist = {
        "Low": int(np.sum(vol_series < 40)),
        "Normal": int(np.sum((vol_series >= 40) & (vol_series < 80))),
        "Elevated": int(np.sum((vol_series >= 80) & (vol_series < 120))),
        "Crisis": int(np.sum(vol_series >= 120)),
    }

    # Forecast at monthly horizons
    h_forecast = {}
    for m_months in [1, 6, 12, 24]:
        periods_ahead = m_months * trading_days_per_month
        h_m = uncond_var + (persistence ** periods_ahead) * (curr_var - uncond_var)
        h_forecast[f"month_{m_months}"] = float(np.sqrt(h_m * ann_factor) * 100)

    # Monthly-equivalent for MC simulation
    monthly_uncond_var = uncond_var * trading_days_per_month
    monthly_curr_var = curr_var * trading_days_per_month

    new_garch = {
        "model_type": "GARCH(1,1)",
        "version": "7.0_jump_filtered_multi_era",
        "input_data": f"HH {freq} log-returns (jump-filtered, excl. Feb 2021 Uri)",
        "frequency": freq,
        "n_observations": n,
        "n_raw_observations": raw_n,
        "estimation_period": {
            "start": start_date if freq == "daily" else old_garch["estimation_period"]["start"],
            "end": end_date if freq == "daily" else old_garch["estimation_period"]["end"],
            "n_observations": n,
        },
        "selected_era": selected_era,
        "eras": {k: v for k, v in eras.items()},
        "parameters": {"omega": float(omega), "alpha": float(alpha),
                        "beta": float(beta), "persistence": float(persistence)},
        "full_sample_parameters": {"omega": float(omega_full), "alpha": float(alpha_full),
                                    "beta": float(beta_full), "persistence": float(pers_full),
                                    "annualized_vol_pct": ann_full},
        "unconditional_volatility": {
            "period": float(uncond_vol_period),
            "annualized_pct": float(uncond_vol_a * 100),
        },
        "half_life_months": float(half_life_months),
        "current_state": {
            "date": end_date if freq == "daily" else old_garch["current_state"]["date"],
            "conditional_variance": float(curr_var),
            "annualized_volatility_pct": float(curr_vol_a),
        },
        "monthly_equivalent": {
            "description": f"GARCH from {selected_era} era, aggregated to monthly for MC",
            "unconditional_variance": float(monthly_uncond_var),
            "current_variance": float(monthly_curr_var),
            "persistence": float(persistence ** trading_days_per_month),
            "trading_days_per_month": trading_days_per_month,
        },
        "seasonal_factors": {
            "description": "Monthly volatility ratios and mean premiums from jump-filtered daily sample",
            "volatility_ratio": seasonal_vol_ratio,
            "mean_premium_usd": seasonal_mean_premium,
        },
        "regime_thresholds": {
            "Low": "<40% annualized",
            "Normal": "40-80% annualized",
            "Elevated": "80-120% annualized",
            "Crisis": ">120% annualized",
        },
        "regime_distribution": regime_dist,
        "volatility_forecast_24m": h_forecast,
        "comparison": {
            "old_params": old_garch["parameters"],
            "new_params": {"omega": float(omega), "alpha": float(alpha),
                           "beta": float(beta), "persistence": float(persistence)},
        },
        "jump_filtering": {
            "method": f"Statistical ({JUMP_K}x MAD-sigma) + event date matching",
            "n_raw": raw_n,
            "n_filtered": n,
            "n_jumps_removed": n_jumps,
            "robust_sigma": round(float(robust_sigma), 6),
            "threshold": round(float(jump_threshold), 4),
            "shock_catalog_path": "fuel_costs_and_supply/hh_shock_catalog.json",
        },
    }

    with open(GARCH_V4, "w") as f:
        json.dump(new_garch, f, indent=2)

    print(f"\n  Old: omega={old_garch['parameters']['omega']:.6f}, alpha={old_garch['parameters']['alpha']:.6f}, "
          f"beta={old_garch['parameters']['beta']:.6f}, pers={old_garch['parameters']['persistence']:.4f}")
    print(f"  New ({selected_era}): omega={omega:.6f}, alpha={alpha:.6f}, beta={beta:.6f}, pers={persistence:.4f}")
    print(f"  Annualized vol: {uncond_vol_a*100:.1f}%, Current: {curr_vol_a:.1f}%")
    print(f"  Half-life: {half_life_months:.1f} months ({half_life_periods:.0f} {freq} periods)")
    print(f"  Regime distribution: {regime_dist}")
    print(f"  Monthly equivalent: uncond_var={monthly_uncond_var:.6f}, curr_var={monthly_curr_var:.6f}, "
          f"pers_monthly={persistence**trading_days_per_month:.4f}")
    # Literature comparison — all eras side by side
    print(f"\n  --- Literature Comparison (Natural Gas GARCH studies, daily frequency) ---")
    print(f"  {'Study':<22} {'Alpha':>8} {'Beta':>8} {'Pers':>8} {'Ann Vol':>10}")
    print(f"  {'-'*58}")
    for study, a_range, b_range, v_range in [
        ("Pindyck 2004",      "0.05-0.10", "0.85-0.92", "35-55%"),
        ("Sadorsky 2006",     "0.08-0.12", "0.82-0.90", "30-50%"),
        ("Kang 2009",         "0.10-0.15", "0.80-0.88", "35-55%"),
        ("Efimova 2014",      "0.05-0.08", "0.88-0.92", "30-45%"),
        ("Nick 2014",         "0.08-0.12", "0.82-0.88", "35-50%"),
        ("Mohammadi 2015",    "0.06-0.10", "0.85-0.90", "30-50%"),
    ]:
        print(f"  {study:<22} {a_range:>8} {b_range:>8} {'0.90-0.99':>8} {v_range:>10}")
    print(f"  {'-'*58}")
    for era_name, era_data in eras.items():
        if not era_data.get("skipped"):
            lbl = era_data.get("label", era_name)
            marker = " <<<" if era_name == selected_era else ""
            print(f"  {lbl:<22} {era_data['alpha']:>8.4f} {era_data['beta']:>8.4f} "
                  f"{era_data['persistence']:>8.4f} {era_data['annualized_vol_pct']:>9.1f}%{marker}")
    print(f"  {'Full sample':<22} {alpha_full:>8.4f} {beta_full:>8.4f} {pers_full:>8.4f} {ann_full:>9.1f}%")
    print(f"  Saved: {GARCH_V4}")
    return new_garch


def _fit_garch(returns):
    n = len(returns)
    var_r = np.var(returns)
    MAX_PERS = 0.9999  # cap persistence < 1 to ensure finite unconditional variance

    def neg_ll(params):
        o, a, b = params
        if o <= 1e-12 or a < 1e-8 or b < 1e-8 or a + b >= MAX_PERS:
            return 1e10
        h = np.zeros(n)
        h[0] = var_r
        for t in range(1, n):
            h[t] = o + a * returns[t-1]**2 + b * h[t-1]
            if h[t] <= 1e-12:
                return 1e10
        return 0.5 * np.sum(np.log(h) + returns**2 / h)

    best_nll, best_p = 1e10, (var_r * 0.01, 0.08, 0.88)
    for a in np.arange(0.02, 0.25, 0.02):
        for b in np.arange(0.50, 0.97, 0.02):
            if a + b >= MAX_PERS:
                continue
            o = var_r * (1 - a - b)
            if o <= 0:
                continue
            nll = neg_ll((o, a, b))
            if nll < best_nll:
                best_nll, best_p = nll, (o, a, b)

    # Use L-BFGS-B for bounded optimization to prevent persistence >= 1
    from scipy.optimize import minimize as sp_minimize
    bounds = [(1e-12, var_r * 2), (1e-8, 0.30), (1e-8, 0.98)]

    def neg_ll_bounded(params):
        o, a, b = params
        if a + b >= MAX_PERS:
            return 1e10
        h = np.zeros(n)
        h[0] = var_r
        for t in range(1, n):
            h[t] = o + a * returns[t-1]**2 + b * h[t-1]
            if h[t] <= 1e-12:
                return 1e10
        return 0.5 * np.sum(np.log(h) + returns**2 / h)

    res = sp_minimize(neg_ll_bounded, best_p, method="L-BFGS-B", bounds=bounds,
                      options={"maxiter": 1000, "ftol": 1e-12})
    if res.success:
        o, a, b = res.x
        if a + b >= MAX_PERS:
            # Rescale to respect boundary
            scale = (MAX_PERS - 0.0001) / (a + b)
            a, b = a * scale, b * scale
            o = var_r * (1 - a - b)
        return (o, a, b)
    return best_p


# ============================================================================
# STEP 4: EVENT FREQUENCY WITH POLICY RISK
# ============================================================================
def step4_events(master):
    banner("STEP 4: Refresh event frequency projections with policy risk")

    evt_path = EVENT_FREQ_V4_EXISTING if os.path.exists(EVENT_FREQ_V4_EXISTING) else EVENT_FREQ_V3
    with open(evt_path) as f:
        old_evt = json.load(f)
    print(f"  Reading from: {os.path.basename(evt_path)}")

    updated = json.loads(json.dumps(old_evt))
    updated["version"] = "4.1_policy_risk_flat_hurricane"

    # Fix hurricane frequency: literature consensus (Knutson, IPCC AR6, Vecchi,
    # NOAA GFDL, Emanuel, Bhatia) is flat/declining frequency. Remove 2% annual
    # increase. Intensity adjustment deferred to future data.
    hurr = updated.get("hurricane_model", {})
    hurr_projs = hurr.get("projections", {})
    if hurr_projs:
        # Derive base rate by removing climate inflation from first year
        first_yr = min(hurr_projs.keys(), key=int)
        first = hurr_projs[first_yr]
        base_annual = first["annual_rate"] / first.get("climate_factor", 1.0)
        base_major = first["major_rate"] / first.get("climate_factor", 1.0)
        base_disruption = first["energy_disruption_rate"] / first.get("climate_factor", 1.0)
        for yr_str, proj in hurr_projs.items():
            old_cf = proj.get("climate_factor", 1.0)
            proj["climate_factor"] = 1.0
            proj["annual_rate"] = round(base_annual, 4)
            proj["major_rate"] = round(base_major, 4)
            proj["energy_disruption_rate"] = round(base_disruption, 6)
        hurr["climate_adjustment"] = "Flat frequency (literature consensus: Knutson, IPCC AR6). Intensity adjustment pending."
        hurr["trend"] = "0.0 storms per decade (flat)"
        print(f"  Hurricane frequency set flat: base_rate={base_annual:.3f}, "
              f"climate_factor=1.0 for all years")

    # Also fix combined_probability_matrix hurricane entries
    cpm = updated.get("combined_probability_matrix", {})
    if cpm and hurr_projs:
        for yr_str, months in cpm.items():
            if yr_str not in hurr_projs:
                continue
            old_proj = old_evt.get("hurricane_model", {}).get("projections", {}).get(yr_str, {})
            old_cf = old_proj.get("climate_factor", 1.0)
            if old_cf > 1.0:
                scale = 1.0 / old_cf  # deflate by old climate factor
                for mo_str, probs in months.items():
                    if "p_hurricane" in probs and probs["p_hurricane"] > 0:
                        probs["p_hurricane"] *= scale
                        probs["p_major_hurricane"] *= scale
                        # Recalculate p_any_event
                        p_any = 1.0 - (1.0 - probs.get("p_hurricane", 0)) * \
                                       (1.0 - probs.get("p_polar_vortex", 0)) * \
                                       (1.0 - probs.get("p_war_event", 0)) * \
                                       (1.0 - probs.get("p_supply_disruption", 0))
                        probs["p_any_event"] = p_any

    # Key policy-risk events:
    # 1. IRA rollback risk (2025+ if Congress acts)
    # 2. Interconnection queue bottleneck (constrains new capacity)
    # 3. RPS compliance shortfall (drives procurement mandates)
    # 4. NEM 3.0 adoption spread (reduces rooftop value, shifts to storage)

    policy_risk_proj = {}
    for year in range(2026, 2036):
        years_from_ira = year - 2022

        # IRA rollback probability: rises with political cycles
        # Higher in odd Congress years (new Congress sworn in Jan after election)
        election_proximity = 1 if (year % 2 == 0) else 0.5
        ira_rollback_prob = min(0.6, 0.05 + 0.03 * years_from_ira * election_proximity)

        # If IRA rolls back, ITC/PTC go to 0 → reduces renewable buildout → more gas dependence
        ira_rollback_gas_impact = 0.5 + 0.1 * years_from_ira  # $/MMBtu upward pressure

        # Queue bottleneck: delays new capacity → tighter market
        # Queue has grown from 300 GW (2010) to 2600 GW (2024)
        queue_delay_prob = min(0.9, 0.3 + 0.05 * (year - 2024))
        queue_capacity_shortfall_gw = max(0, 5 + 3 * (year - 2025))  # annual shortfall

        # RPS compliance pressure: states falling behind targets
        rps_shortfall_prob = min(0.5, 0.1 + 0.03 * (year - 2025))

        # NEM reform spread: other states follow CA NEM 3.0 model
        nem_reform_states = min(20, 3 + 2 * (year - 2024))

        policy_risk_proj[str(year)] = {
            "ira_rollback_probability": float(ira_rollback_prob),
            "ira_rollback_gas_impact_usd": float(ira_rollback_gas_impact),
            "queue_delay_probability": float(queue_delay_prob),
            "queue_capacity_shortfall_gw": float(queue_capacity_shortfall_gw),
            "rps_shortfall_probability": float(rps_shortfall_prob),
            "nem_reform_states": int(nem_reform_states),
            "policy_uncertainty_index": float(
                ira_rollback_prob * 0.4 + queue_delay_prob * 0.3 + rps_shortfall_prob * 0.3
            ),
        }

    updated["policy_risk_model"] = {
        "methodology": "Policy event probability modeling based on legislative cycles and queue data",
        "key_risks": [
            "IRA rollback/modification reduces renewable tax credits",
            "Interconnection queue bottleneck delays new generation capacity",
            "State RPS compliance shortfall increases procurement urgency",
            "NEM reform reduces rooftop solar economics, shifts to utility-scale",
        ],
        "projections": policy_risk_proj,
        "impact_on_gas": {
            "ira_rollback": "Reduced renewable buildout → higher gas-fired generation → +$0.50-2.00/MMBtu",
            "queue_delays": "Less new capacity online → tighter supply-demand → +$0.25-0.75/MMBtu",
            "rps_pressure": "Accelerated RE procurement → lower gas demand long-term → -$0.25-0.50/MMBtu",
        },
    }

    # Adjust existing hurricane/polar vortex models for policy context
    # Higher queue delays = less reserve margin = more vulnerability to weather events
    for yr_str, proj in updated.get("hurricane_model", {}).get("projections", {}).items():
        yr = int(yr_str)
        pol = policy_risk_proj.get(yr_str, {})
        queue_factor = 1.0 + 0.2 * pol.get("queue_delay_probability", 0.3)
        proj["policy_queue_vulnerability_factor"] = float(queue_factor)

    with open(EVENT_FREQ_V4, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"  Added policy-risk event model (IRA rollback, queue delays, RPS shortfall)")
    print(f"  Policy uncertainty index 2026: {policy_risk_proj['2026']['policy_uncertainty_index']:.3f}")
    print(f"  Policy uncertainty index 2035: {policy_risk_proj['2035']['policy_uncertainty_index']:.3f}")
    print(f"  Saved: {EVENT_FREQ_V4}")
    return updated


# ============================================================================
# STEP 5: LNG CAPACITY CURVE VERIFICATION
# ============================================================================
def step5_lng(master):
    banner("STEP 5: Verify LNG capacity curve against policy outlook")

    with open(LNG_CURVE) as f:
        lng = json.load(f)

    # Policy context for LNG exports
    print("  Policy context for LNG capacity curve:")
    print(f"  - IRA (2022): Accelerates renewable deployment → displaces some gas generation")
    print(f"    but increases electrification → net gas demand may still rise")
    print(f"  - Queue backlog (2,600 GW): Delays new renewable capacity → sustained gas reliance")
    print(f"  - FERC Order 2023: Reforms may accelerate queue processing → more RE online 2027+")
    print(f"  - State RPS targets: CA 100% by 2045, NY 100% by 2040 → long-term gas demand decline")

    # Check policy-adjusted demand outlook
    pol_df = master.dropna(subset=["policy_intensity_index"]).groupby("year").agg(
        policy_idx=("policy_intensity_index", "first"),
        queue_gw=("queue_backlog_gw", "first"),
        n_rps=("n_rps_states", "first"),
    ).reset_index()

    print(f"\n  Policy trajectory:")
    print(f"  {'Year':<8} {'Policy Idx':>12} {'Queue GW':>10} {'RPS States':>12}")
    print(f"  {'-'*45}")
    for _, row in pol_df.tail(10).iterrows():
        print(f"  {int(row['year']):<8} {row['policy_idx']:12.3f} {row['queue_gw']:10.0f} {row['n_rps']:12.0f}")

    print(f"\n  LNG export projections from capacity curve:")
    for yr_str in sorted(lng.get("us_lng_exports", {}).keys(), key=int):
        exp = lng["us_lng_exports"][yr_str]
        print(f"  {yr_str}: {exp['exports_bcfd']:.1f} Bcf/d ({exp['lng_share_pct']:.1f}% of production)")

    print(f"\n  Assessment: Queue backlog delays (~5yr avg wait) keep gas-fired generation elevated")
    print(f"  through 2028-2030 even with IRA incentives. IRA rollback risk adds upside to gas demand.")
    print(f"  FERC 2023 reforms may reduce queue times by 2027, potentially accelerating the")
    print(f"  renewable transition. Net effect: LNG curve assumptions remain valid; policy risk")
    print(f"  is captured via Monte Carlo event shocks rather than structural curve changes.")

    return lng


# ============================================================================
# STEP 6: MONTE CARLO V8
# ============================================================================
def step6_monte_carlo(garch, reg_results, event_proj, lng, master=None, reg_v2=None):
    banner("STEP 6: Re-run Monte Carlo (V8 — regression-estimated gas mean)")

    mc_path = MC_V8_EXISTING if os.path.exists(MC_V8_EXISTING) else MC_V7
    with open(mc_path) as f:
        old_mc = json.load(f)
    print(f"  Reading from: {os.path.basename(mc_path)}")

    # GARCH parameters — use monthly-equivalent if estimated on daily data
    # Daily GARCH needs aggregation: monthly variance ≈ daily variance × trading_days
    # Monthly persistence ≈ daily_persistence^21
    monthly_eq = garch.get("monthly_equivalent")
    if monthly_eq:
        # Daily estimation → use aggregated monthly parameters for MC
        omega_daily = garch["parameters"]["omega"]
        alpha_daily = garch["parameters"]["alpha"]
        beta_daily = garch["parameters"]["beta"]
        pers_daily = alpha_daily + beta_daily
        td = monthly_eq["trading_days_per_month"]
        # For monthly MC steps, use effective monthly parameters
        # Unconditional monthly variance = daily uncond var × trading days
        # Persistence per month = daily persistence ^ trading days
        omega = float(monthly_eq["unconditional_variance"] * (1 - monthly_eq["persistence"]))
        alpha = float(alpha_daily * td)  # scale shock sensitivity
        beta = float(monthly_eq["persistence"] - alpha_daily * td)
        # Ensure valid: if alpha scaling makes beta negative, use simpler aggregation
        if beta < 0:
            pers_monthly = monthly_eq["persistence"]
            alpha = float(min(0.15, 1 - pers_monthly) if pers_monthly < 1 else 0.05)
            beta = float(max(0, pers_monthly - alpha))
            omega = float(monthly_eq["unconditional_variance"] * (1 - alpha - beta))
        # For multi-year projections, start from unconditional (long-run) variance.
        # The current daily h_t reflects recent trading noise and decays to unconditional
        # with half-life ~1.6 months — irrelevant for 5-year forecasts.
        # Starting at elevated variance inflates P50 through Jensen's inequality / convexity premium.
        uncond_monthly = float(monthly_eq["unconditional_variance"])
        curr_monthly = float(monthly_eq["current_variance"])
        curr_var = uncond_monthly  # start at long-run average, not crisis level
        print(f"  GARCH: daily params (alpha={alpha_daily:.4f}, beta={beta_daily:.4f}, pers={pers_daily:.4f})")
        print(f"  GARCH: monthly equivalent (omega={omega:.6f}, alpha={alpha:.4f}, beta={beta:.4f}, "
              f"pers={alpha+beta:.4f})")
        print(f"  GARCH: monthly var: unconditional={uncond_monthly:.6f}, current={curr_monthly:.6f}, "
              f"starting={curr_var:.6f} (unconditional — long-run average)")
    else:
        # Monthly estimation — use directly
        omega = garch["parameters"]["omega"]
        alpha = garch["parameters"]["alpha"]
        beta = garch["parameters"]["beta"]
        curr_var = garch["current_state"]["conditional_variance"]
        uncond_monthly = garch.get("unconditional_volatility", {}).get("period", curr_var) ** 2

    # --- Build regression-estimated gas price target ---
    # Use best HH variant coefficients from step2b to project gas prices
    # This replaces the LNG-adjusted mean ($7-11) which was 3-5x too high
    hh_coefs = {}  # var_name → float coefficient
    hh_intercept = 3.0  # fallback
    if reg_v2:
        # Find best HH variant
        best_hh_key = None
        best_r2 = -1
        for key in reg_v2:
            if key.startswith("hh_") and isinstance(reg_v2[key], dict) and "adj_r_squared" in reg_v2[key]:
                if reg_v2[key]["adj_r_squared"] > best_r2:
                    best_r2 = reg_v2[key]["adj_r_squared"]
                    best_hh_key = key
        if best_hh_key:
            model = reg_v2[best_hh_key]
            raw_coefs = model.get("coefficients", {})
            # Extract float coefficients from nested dicts
            for var, vdict in raw_coefs.items():
                if var in ("const", "Intercept", "intercept"):
                    if isinstance(vdict, dict):
                        hh_intercept = float(vdict.get("coefficient", 3.0) or 3.0)
                    else:
                        hh_intercept = float(vdict)
                else:
                    if isinstance(vdict, dict):
                        hh_coefs[var] = float(vdict.get("coefficient", 0) or 0)
                    else:
                        hh_coefs[var] = float(vdict)
            print(f"  Using HH regression: {best_hh_key} (adj R²={best_r2:.4f})")
            print(f"    Intercept: ${hh_intercept:.2f}")
            for var, coef in sorted(hh_coefs.items()):
                print(f"    {var}: {coef:+.4f}")

    # Get latest observed HH price for START
    START = 3.0
    if master is not None and "henry_hub_spot" in master.columns:
        latest_hh = master["henry_hub_spot"].dropna()
        if len(latest_hh) > 0:
            START = float(latest_hh.iloc[-1])
    print(f"  START price: ${START:.2f} (latest observed HH)")

    # Build projected fundamentals for each year using data-driven trajectories
    # These must match the step11 variable projections to ensure consistency
    projected_vars = {}
    if master is not None and hh_coefs:
        latest = master.dropna(subset=["henry_hub_spot"]).iloc[-1].to_dict() if len(master) > 0 else {}
        for yr in range(2026, 2036):
            yr_vars = {}
            # Data-driven projections for each HH regression variable
            var_projections = {
                # GDP growth: CBO-like trajectory 2.8% → 2.2%
                "us_gdp_growth_pct": round(2.8 - 0.06 * (yr - 2025), 2),
                # Industrial production index: slow growth from 103.5
                "us_industrial_prod_index": round(103.5 + 0.8 * (yr - 2025), 1),
                # Data center TWh (US): CAGR 8.47% from 200 TWh (2024 base)
                "us_data_center_twh": round(200 * (1.0847 ** (yr - 2024)), 1),
                # Electric power demand (Bcf/d): slow growth with electrification
                "electric_power_bcfd": round(35.5 + 0.4 * (yr - 2025), 1),
                # ITC rate: 30% through 2032, phasedown after
                "itc_rate_pct": 30.0 if yr <= 2032 else max(10, 30 - 4 * (yr - 2032)),
                # PTC rate (cents/kWh): 2.75 through 2032, phasedown after
                "ptc_rate_cents_kwh": 2.75 if yr <= 2032 else max(0.5, 2.75 - 0.5 * (yr - 2032)),
                # Queue backlog (GW): peaks ~3200 by 2027 then declines with FERC reforms
                "queue_backlog_gw": round(2600 + 200 * (yr - 2025), 0) if yr <= 2027 else round(3000 - 100 * (yr - 2027), 0),
                # Cumulative FERC reforms: 12 now, +1 per year
                "cumulative_ferc_reforms": min(12 + (yr - 2025), 22),
                # LNG utilization: grows from ~65% toward 85-90% as capacity fills
                "lng_utilization_pct": round(min(92, 65 + 3.0 * (yr - 2025)), 1),
                # Storage deviation: project normal (0% vs 5yr avg)
                "us_ng_storage_vs_5yr_pct": 0.0,
                # Weather departures: project normal (0%)
                "us_hdd_departure_pct": 0.0,
                "us_cdd_departure_pct": 0.0,
            }
            # Seasonal variables handled monthly in simulation loop — skip from annual delta
            _seasonal_vars = {"is_winter", "is_summer", "us_hdd", "us_cdd", "tx_hdd", "tx_cdd", "ca_hdd", "ca_cdd"}
            for var in hh_coefs:
                if var in _seasonal_vars:
                    continue
                yr_vars[var] = var_projections.get(var, float(latest.get(var, 0)))
            projected_vars[str(yr)] = yr_vars

    # Compute regression-estimated annual gas means
    # Use latest observed price as base + regression deltas from projected changes
    # (direct regression prediction misses by residual; anchoring to observed avoids this)
    # Cap each variable's delta to ±2σ of in-sample range to prevent extrapolation
    # (regression coefficients are only valid within the sample's observed variation)
    _seasonal_vars = {"is_winter", "is_summer", "us_hdd", "us_cdd", "tx_hdd", "tx_cdd", "ca_hdd", "ca_cdd"}
    var_caps = {}
    if master is not None:
        for var in hh_coefs:
            if var in _seasonal_vars:
                continue
            if var in master.columns:
                var_std = float(master[var].dropna().std())
                var_caps[var] = 2.0 * var_std if var_std > 0 else float("inf")
    reg_gas_means = {}
    for yr_s, yr_vars in projected_vars.items():
        delta = 0.0
        for var, coef in hh_coefs.items():
            if var in _seasonal_vars:
                continue
            latest_val = float(latest.get(var, 0)) if latest else 0
            projected_val = yr_vars.get(var, latest_val)
            raw_delta = projected_val - latest_val
            cap = var_caps.get(var, float("inf"))
            capped_delta = max(-cap, min(cap, raw_delta))
            delta += coef * capped_delta
        reg_gas_means[yr_s] = max(1.5, START + delta)
    # Plausibility check: if regression target for first year is outside $2-12 range,
    # the model is extrapolating beyond its valid domain (OOS R²=-90 confirms this).
    # Fall back to latest observed price as MC target — let GARCH + events drive evolution.
    first_yr = sorted(reg_gas_means.keys())[0] if reg_gas_means else None
    first_target = reg_gas_means.get(first_yr, START) if first_yr else START
    if reg_gas_means and 2.0 <= first_target <= 12.0:
        print(f"  Regression-estimated annual gas means (base=${START:.2f} + delta):")
        for yr_s in sorted(reg_gas_means.keys())[:5]:
            delta = reg_gas_means[yr_s] - START
            print(f"    {yr_s}: ${reg_gas_means[yr_s]:.2f} (delta={delta:+.2f})")
    else:
        # Regression target implausible (${first_target:.2f}) — model extrapolating
        # beyond valid range. Use latest observed HH as long-run target.
        if reg_gas_means:
            print(f"  WARNING: Regression target ${first_target:.2f} outside plausible $2-12 range")
            print(f"    → Model extrapolating beyond in-sample domain (OOS R²<0)")
            print(f"    → Falling back to observed HH (${START:.2f}) as MC target")
        for yr in range(2026, 2036):
            reg_gas_means[str(yr)] = START
        if not reg_gas_means:
            print(f"  No regression coefficients — using START (${START:.2f}) as mean")

    # Monthly HDD/CDD normals for seasonal price adjustment in MC simulation
    # Replaces binary is_winter/is_summer with actual degree-day variation
    hdd_cdd_normals = {}  # col_name → {month: mean_value}
    hdd_cdd_annual_avg = {}  # col_name → annual monthly average
    if master is not None:
        for col in ["us_hdd", "us_cdd"]:
            if col in master.columns and col in hh_coefs:
                month_means = master.groupby("month")[col].mean().to_dict()
                hdd_cdd_normals[col] = month_means
                hdd_cdd_annual_avg[col] = sum(month_means.values()) / len(month_means) if month_means else 0
                print(f"  HDD/CDD seasonal: {col} annual avg={hdd_cdd_annual_avg[col]:.0f}, "
                      f"Jan={month_means.get(1, 0):.0f}, Jul={month_means.get(7, 0):.0f}")
    use_hdd_cdd_seasonal = len(hdd_cdd_normals) > 0

    # Seasonal volatility ratios from daily HH data (computed in step3_garch)
    # January=2.15x, winter avg=1.55x, summer avg=0.59x — data-driven, no assumptions
    seasonal_vol_raw = garch.get("seasonal_factors", {}).get("volatility_ratio", {})
    seasonal_vol = {int(k): v for k, v in seasonal_vol_raw.items()} if seasonal_vol_raw else {}
    if seasonal_vol:
        print(f"  Seasonal vol ratios: " +
              ", ".join(f"M{m}={seasonal_vol.get(m, 1.0):.2f}x" for m in range(1, 13)))
    else:
        print(f"  No seasonal volatility data — using uniform volatility")

    hurr_proj = event_proj.get("hurricane_model", {}).get("projections", {})
    pv_proj = event_proj.get("polar_vortex_model", {}).get("projections", {})
    geo_proj = event_proj.get("geopolitical_model", {}).get("projections", {})
    demand_proj = event_proj.get("demand_shock_model", {}).get("projections", {})
    policy_proj = event_proj.get("policy_risk_model", {}).get("projections", {})

    N_SIMS = 10000
    MONTHS = 60
    CAP = 25.0
    FLOOR = 1.50  # lowest monthly avg HH in 29 years = $1.49 (Mar 2024)

    np.random.seed(42)
    random.seed(42)
    print(f"  Running {N_SIMS:,} simulations x {MONTHS} months...")

    paths = np.zeros((N_SIMS, MONTHS + 1))
    paths[:, 0] = START

    for sim in range(N_SIMS):
        h_t = curr_var
        price = START
        ln_price = np.log(START)
        active_events = []  # Multi-month event tracking: [[signed_peak, remaining_months, total_duration], ...]
        # Determine if IRA rollback occurs in this simulation path
        ira_rollback_year = None
        for yr in range(2026, 2031):
            pol = policy_proj.get(str(yr), {})
            if random.random() < pol.get("ira_rollback_probability", 0.05):
                ira_rollback_year = yr
                break

        for m in range(MONTHS):
            mo = (m % 12) + 1
            yr = 2026 + m // 12
            yr_s = str(yr)

            # Regression-estimated gas price target (replaces LNG-adjusted mean)
            lr_mean = reg_gas_means.get(yr_s, START)

            # Seasonal adjustment via monthly HDD/CDD normals (or is_winter/is_summer fallback)
            if use_hdd_cdd_seasonal:
                for col in hdd_cdd_normals:
                    month_val = hdd_cdd_normals[col].get(mo, 0)
                    annual_avg = hdd_cdd_annual_avg[col]
                    lr_mean += hh_coefs[col] * (month_val - annual_avg)
            elif hh_coefs:
                if mo in [1, 2, 12] and "is_winter" in hh_coefs:
                    lr_mean += hh_coefs["is_winter"]
                elif mo in [6, 7, 8] and "is_summer" in hh_coefs:
                    lr_mean += hh_coefs["is_summer"]

            # Policy adjustment: IRA rollback shifts mean upward (more gas reliance)
            pol = policy_proj.get(yr_s, {})
            if ira_rollback_year and yr >= ira_rollback_year:
                years_since = yr - ira_rollback_year
                rollback_impact = min(2.0, pol.get("ira_rollback_gas_impact_usd", 0.5) * (1 + 0.2 * years_since))
                lr_mean += rollback_impact

            # Queue bottleneck: tighter markets when new capacity is delayed
            if random.random() < pol.get("queue_delay_probability", 0.3):
                queue_premium = pol.get("queue_capacity_shortfall_gw", 5) * 0.01
                lr_mean += min(0.5, queue_premium)

            target = lr_mean
            speed = 0.15

            # GARCH shock — additive in log-space (Schwartz 1997 one-factor model)
            # Simulating d(ln P) = κ(ln θ − ln P)dt + σ dW
            # Seasonal vol ratio applied to shock, GARCH tracks deseasonalized variance
            z = np.random.normal(0, 1)
            vol_ratio = seasonal_vol.get(mo, 1.0)
            shock = z * np.sqrt(max(h_t, 1e-6)) * vol_ratio  # additive in log-space
            h_t = omega + alpha * (z * np.sqrt(max(h_t, 1e-6)))**2 + beta * h_t
            h_t = max(h_t, 1e-6)
            h_t = min(h_t, 4.0 * uncond_monthly)  # variance cap: observed monthly std never exceeds ~2x unconditional

            # === MULTI-MONTH EVENT MODEL ===
            # Category-specific persistence from 29-year empirical severity-duration analysis
            # (henry_hub_historical_price_incidents.json, 12 positive + 11 negative named events)
            #
            # Positive mo/$ (duration per dollar of severity):
            #   hurricane=1.86, cold_snap/polar_vortex=1.33, geopolitical=0.99, structural=1.27
            # Negative mo/$ :
            #   mild_winter=4.94, supply_surplus=9.52, macro_recession=10.86, structural=2.46
            #
            # Frequencies from 29-year named event catalog:
            #   Positive: hurricane=0.172/yr, cold=0.172/yr, geopolitical=0.034/yr, structural=0.103/yr
            #   Negative: mild_winter=0.172/yr, supply_surplus=0.138/yr, recession=0.207/yr, structural=0.069/yr
            #
            # Expected monthly impulse (no calibration factors — pure empirical data):
            #   Positive: +$0.30/month, Negative: -$0.27/month, Net: +$0.03/month

            # Step 1: Accumulate contributions from all active events (linear decay)
            event_shock = 0.0
            surviving = []
            for evt_peak, evt_rem, evt_tot in active_events:
                event_shock += evt_peak * (evt_rem / evt_tot)
                if evt_rem > 1:
                    surviving.append([evt_peak, evt_rem - 1, evt_tot])
            active_events = surviving

            # Step 2: Check for new events this month

            # --- POSITIVE EVENTS ---

            # Hurricane (Aug-Oct): 5 gas-impacting events in 29 years = 0.172/yr
            # Scale by projection ratio for climate trend
            hr = hurr_proj.get(yr_s, {})
            if mo in [8, 9, 10]:
                hurr_p = (0.172 * hr.get("annual_rate", 2.0) / 2.0) / 3
                if random.random() < hurr_p:
                    mag = random.uniform(1.50, 4.60)
                    dur = max(1, round(1.86 * mag))
                    active_events.append([mag, dur, dur])

            # Polar vortex / cold snap (Dec-Feb): 5 events in 29 years = 0.172/yr
            # Scale by projection ratio; Uri-type extreme handled separately
            pv = pv_proj.get(yr_s, {})
            if mo in [1, 2, 12]:
                pv_p = (0.172 * pv.get("annual_rate", 0.6) / 0.6) / 3
                if random.random() < pv_p:
                    mag = random.uniform(1.30, 4.80)
                    dur = max(1, round(1.33 * mag))
                    active_events.append([mag, dur, dur])
                # Uri-type extreme tail event (~1 in 100 years)
                if random.random() < 0.01 / 3:
                    mag = random.uniform(8.0, 14.0)
                    dur = max(1, round(1.33 * mag))
                    active_events.append([mag, dur, dur])

            # Geopolitical (any month): 1 gas-impacting event in 29 years = 0.034/yr
            # (p_disruption from projections = 0.64 = ANY disruption worldwide,
            #  not ones that move US HH gas. Only Russia-Ukraine 2022 qualified.)
            # Scale by projection ratio for trend (rising geopolitical risk)
            geo = geo_proj.get(yr_s, {})
            geo_base = 0.034  # empirical gas-impact rate
            geo_ratio = geo.get("p_disruption", 0.05) / 0.50  # normalize to 2024 baseline ~0.50
            if random.random() < (geo_base * max(0.5, min(2.0, geo_ratio))) / 12:
                mag = random.uniform(2.00, 5.00)
                dur = max(1, round(0.99 * mag))
                active_events.append([mag, dur, dur])

            # Demand shock / structural surge (any month): 2 events in 29 years = 0.069/yr
            # (demand_spike_probability from projections = total grid stress probability,
            #  not specifically gas-price-impacting events)
            dd = demand_proj.get(yr_s, {})
            dd_base = 0.069  # empirical gas-impact rate
            dd_ratio = dd.get("demand_spike_probability", 0.05) / 0.05  # normalize
            if random.random() < (dd_base * max(0.5, min(2.0, dd_ratio))) / 12:
                mag = random.uniform(0.80, 1.50)
                dur = max(1, round(1.27 * mag))
                active_events.append([mag, dur, dur])

            # Policy shock (any month): 1 event in 29 years = 0.034/yr
            policy_shock_base = 0.034  # empirical gas-impact rate
            if random.random() < policy_shock_base / 12:
                mag = random.uniform(0.30, 1.50)
                dur = max(1, round(1.27 * mag))
                active_events.append([mag, dur, dur])

            # --- NEGATIVE EVENTS ---

            # Mild winter (Dec-Feb): 5 events in 29 years = 0.172/yr
            # Persistence: 4.94 months per dollar (weather category)
            if mo in [1, 2, 12]:
                if random.random() < 0.057:  # 0.172/3
                    mag = random.uniform(0.66, 1.30)
                    dur = max(1, round(4.94 * mag))
                    active_events.append([-mag, dur, dur])

            # Storage surplus (Sep-Nov): 4 events in 29 years = 0.138/yr
            # Persistence: 9.52 months per dollar (supply surplus category)
            if mo in [9, 10, 11]:
                if random.random() < 0.046:  # 0.138/3
                    mag = random.uniform(0.30, 1.50)
                    dur = max(1, round(9.52 * mag))
                    active_events.append([-mag, dur, dur])

            # Macro recession / demand destruction (any month): 6 events in 29 years = 0.207/yr
            # Persistence: 10.86 months per dollar (macro category — longest lasting)
            if random.random() < 0.017:  # 0.207/12
                mag = random.uniform(0.54, 2.25)
                dur = max(1, round(10.86 * mag))
                active_events.append([-mag, dur, dur])

            # Renewable overbuild / structural correction (any month, growing probability)
            # 2 events in 29 years = 0.069/yr base, increasing with renewable penetration
            # Persistence: 2.46 months per dollar (structural category)
            overbuild_p = 0.006 + 0.001 * max(0, yr - 2025)  # 0.069/12 base, growing
            if random.random() < overbuild_p:
                mag = random.uniform(0.51, 1.03)
                dur = max(1, round(2.46 * mag))
                active_events.append([-mag, dur, dur])

            # Log-space mean-reversion (Schwartz 1997 one-factor)
            # P50 tracks regression target; mean > P50 by convexity premium exp(σ²/2κ)
            ln_target = np.log(max(target, 0.5))
            # Convert dollar-denominated event shock to log-space
            ln_event = np.log(max(1.0 + event_shock / price, 0.05))  # floor at -95%
            ln_price = ln_price + speed * (ln_target - ln_price) + shock + ln_event
            ln_price = min(ln_price, np.log(CAP))  # cap at $25
            ln_price = max(ln_price, np.log(FLOOR))  # reflecting floor at $1.50 (production cost floor)
            price = np.exp(ln_price)
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
        "version": "8.0",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "description": "V7 + policy variables (ITC/PTC rates, queue backlog, RPS, IRA rollback risk)",
        "key_changes": [
            "Policy variables in regression (ITC rate, PTC rate, queue backlog, RPS count, policy intensity)",
            "GARCH re-estimated on policy-adjusted residuals",
            "Policy-risk event model added (IRA rollback, queue delays, RPS shortfall)",
            "IRA rollback scenario paths in Monte Carlo (shifts long-run mean upward)",
            "Queue bottleneck premium on gas prices",
            "Policy uncertainty shock events",
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

    with open(MC_V8, "w") as f:
        json.dump(new_mc, f, indent=2)

    print(f"\n  Annual Forecasts (V8 with policy variables):")
    print(f"  {'Year':<8} {'Mean':>8} {'P10':>8} {'P25':>8} {'P50':>8} {'P75':>8} {'P90':>8}")
    print(f"  {'-'*56}")
    for yr_s in sorted(annual_fc):
        af = annual_fc[yr_s]
        print(f"  {yr_s:<8} {af['mean']:8.2f} {af['p10']:8.2f} {af['p25']:8.2f} "
              f"{af['median']:8.2f} {af['p75']:8.2f} {af['p90']:8.2f}")

    print(f"\n  Saved: {MC_V8}")
    return new_mc


# ============================================================================
# STEP 7: VALIDATE
# ============================================================================
def step7_validate(reg_results, garch, new_mc):
    banner("STEP 7: Validate — compare policy models vs prior")

    mc_prev_path = MC_V8_EXISTING if os.path.exists(MC_V8_EXISTING) else MC_V7
    with open(mc_prev_path) as f:
        old_mc = json.load(f)

    garch_prev_path = GARCH_V4_EXISTING if os.path.exists(GARCH_V4_EXISTING) else GARCH_V3
    with open(garch_prev_path) as f:
        old_garch = json.load(f)

    validation = {"timestamp": datetime.now().isoformat(), "regression": {}, "garch": {},
                   "forecasts": {}, "policy_impact": {}}

    # Regression comparison
    print("\n  Regression R² Summary:")
    print(f"  {'Model':<35} {'R²':>8} {'Adj R²':>8} {'n':>6}")
    print(f"  {'-'*60}")
    for mkey, mdata in reg_results.items():
        if isinstance(mdata, dict) and "r_squared" in mdata:
            print(f"  {mdata['name']:<35} {mdata['r_squared']:8.4f} {mdata['adj_r_squared']:8.4f} {mdata['n']:6d}")
            validation["regression"][mdata["name"]] = {
                "r_squared": mdata["r_squared"], "adj_r_squared": mdata["adj_r_squared"], "n": mdata["n"]}

    # R² improvement from policy variables
    for region, base_key, pol_key, full_key in [
        ("ERCOT", "ercot_baseline", "ercot_policy", "ercot_full"),
        ("CAISO", "caiso_baseline", "caiso_policy", "caiso_full"),
        ("HH", "hh_baseline", "hh_policy", "hh_full"),
    ]:
        if base_key in reg_results:
            base_r2 = reg_results[base_key]["r_squared"]
            pol_r2 = reg_results[pol_key]["r_squared"] if pol_key in reg_results else None
            full_r2 = reg_results[full_key]["r_squared"] if full_key in reg_results else None

            print(f"\n  {region}: Baseline R²={base_r2:.4f}", end="")
            if pol_r2:
                print(f" → +Policy R²={pol_r2:.4f} (+{pol_r2-base_r2:.4f})", end="")
            if full_r2:
                print(f" → Full R²={full_r2:.4f} (+{full_r2-base_r2:.4f})", end="")
            print()

            # Significant policy variables
            if full_key in reg_results:
                sig_pol = [v for v, c in reg_results[full_key]["coefficients"].items()
                           if c.get("p_value") and c["p_value"] < 0.05
                           and v not in ["intercept", "henry_hub_spot", "is_summer", "is_winter",
                                         "us_hdd", "us_cdd", "tx_hdd", "tx_cdd", "ca_hdd", "ca_cdd",
                                         "electric_power_bcfd"]]
                validation["policy_impact"][region] = {
                    "r2_baseline": base_r2,
                    "r2_full": full_r2,
                    "r2_improvement": full_r2 - base_r2 if full_r2 else 0,
                    "significant_vars": sig_pol,
                }
                if sig_pol:
                    print(f"    Significant variables (p<0.05): {sig_pol}")
                else:
                    print(f"    No individually significant policy variables at p<0.05")

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
    print(f"\n  Forecast Comparison (V7 → V8):")
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

    # Quick literature cross-check (reads from local benchmarks file first)
    if os.path.exists(LITERATURE_BENCHMARKS):
        with open(LITERATURE_BENCHMARKS) as f:
            lit = json.load(f)
        consensus = lit.get("gas_price_forecasts", {}).get("consensus_range", {})
        if consensus:
            print(f"\n  Literature Benchmark Check (from {os.path.basename(LITERATURE_BENCHMARKS)}):")
            new_fc_data = new_mc.get("annual_forecasts", {})
            lit_check = {}
            for yr_s, c in consensus.items():
                if not isinstance(c, dict):
                    continue
                fc = new_fc_data.get(yr_s, {})
                p50 = fc.get("median", 0)
                c_low, c_high = c.get("low", 0), c.get("high", 0)
                status = "PASS" if c_low <= p50 <= c_high else "HIGH" if p50 > c_high else "LOW"
                print(f"    {yr_s}: P50=${p50:.2f} vs consensus ${c_low:.2f}-${c_high:.2f} — {status}")
                lit_check[yr_s] = {"p50": round(p50, 2), "status": status}
            validation["literature_check"] = lit_check

    with open(VALIDATION, "w") as f:
        json.dump(validation, f, indent=2)
    print(f"\n  Saved: {VALIDATION}")
    return validation


# ============================================================================
# STEP 8: UPDATE MODEL PARAMS
# ============================================================================
def step8_params(reg_results, garch):
    banner("STEP 8: Update model parameters")

    params_path = MODEL_PARAMS_V4_EXISTING if os.path.exists(MODEL_PARAMS_V4_EXISTING) else MODEL_PARAMS_V3
    with open(params_path) as f:
        params = json.load(f)

    for region, full_key in [("ercot", "ercot_full"), ("caiso", "caiso_full")]:
        if full_key in reg_results:
            r = reg_results[full_key]
            params[region]["multivariate"] = {
                "coefficients": {k: v["coefficient"] for k, v in r["coefficients"].items()},
                "r_squared": r["r_squared"],
                "includes_demand_drivers": True,
                "includes_policy_variables": True,
                "policy_variables": [v for v in r["coefficients"]
                                     if v in ["itc_rate_pct", "ptc_rate_cents_kwh",
                                              "queue_backlog_gw", "ercot_large_load_queue_gw",
                                              "cumulative_ferc_reforms",
                                              "tx_crez_transmission_gw",
                                              "n_ira_credits",
                                              "ca_rps_target_pct", "ca_allowance_price_per_ton",
                                              "ca_nem_compensation_level", "ca_storage_mandate",
                                              "ca_solar_mandate", "der_market_access", "n_rps_states"]],
                "demand_driver_variables": [v for v in r["coefficients"]
                                            if v not in ["intercept", "henry_hub_spot",
                                                          "is_summer", "is_winter",
                                                          "us_hdd", "us_cdd", "tx_hdd", "tx_cdd", "ca_hdd", "ca_cdd",
                                                          "itc_rate_pct", "ptc_rate_cents_kwh",
                                                          "queue_backlog_gw", "ercot_large_load_queue_gw",
                                                          "cumulative_ferc_reforms",
                                                          "tx_crez_transmission_gw",
                                                          "n_ira_credits",
                                                          "ca_rps_target_pct", "ca_allowance_price_per_ton",
                                                          "ca_nem_compensation_level", "ca_storage_mandate",
                                                          "ca_solar_mandate", "der_market_access", "n_rps_states"]],
            }
            params[region]["residual_std"] = r["residual_std"]

    with open(MODEL_PARAMS_V4, "w") as f:
        json.dump(params, f, indent=2)
    print(f"  Saved: {MODEL_PARAMS_V4}")


# ============================================================================
# STEP 9: GENERATE RELATIONSHIP GRAPH JSON
# ============================================================================
def step9_relationship_graph(reg_results):
    banner("STEP 9: Generate relationship graph JSON from regression results")

    def _coef(model_key, var):
        """Extract coefficient, p-value, significance from regression results."""
        if model_key not in reg_results:
            return None, None, False
        coefs = reg_results[model_key].get("coefficients", {})
        if var not in coefs:
            return None, None, False
        c = coefs[var]
        return c["coefficient"], c["p_value"], c["significant"]

    def _sig_str(p):
        if p is None:
            return ""
        if p < 0.001:
            return "***"
        if p < 0.01:
            return "**"
        if p < 0.05:
            return "*"
        return ""

    # Pull model-level stats
    def _model_stats(key):
        if key not in reg_results:
            return {}
        r = reg_results[key]
        return {
            "r2": round(r["r_squared"], 4),
            "adj_r2": round(r["adj_r_squared"], 4),
            "n": r["n"],
            "k": r["k"],
        }

    # Identify significant variables per model
    def _significant_vars(key):
        if key not in reg_results:
            return []
        coefs = reg_results[key].get("coefficients", {})
        return [v for v, c in coefs.items()
                if c.get("significant") and v != "intercept"]

    # Build high-impact factors from HH and ERCOT regressions
    hh_sig = _significant_vars("hh_full")
    ercot_sig = _significant_vars("ercot_full")
    caiso_sig = _significant_vars("caiso_full")

    # Build the graph structure
    gas_coef_ercot, gas_p_ercot, _ = _coef("ercot_full", "henry_hub_spot")
    gas_coef_caiso, gas_p_caiso, _ = _coef("caiso_full", "henry_hub_spot")

    # HH Full model coefficients
    hh_coefs = {}
    if "hh_full" in reg_results:
        for var, c in reg_results["hh_full"]["coefficients"].items():
            if var != "intercept":
                hh_coefs[var] = {
                    "coefficient": round(c["coefficient"], 4),
                    "p_value": round(c["p_value"], 6) if c["p_value"] else None,
                    "significant": c["significant"],
                    "sig": _sig_str(c["p_value"]),
                }

    # Build high-impact ranked list
    high_impact = []
    if gas_coef_ercot is not None:
        high_impact.append({
            "rank": 1, "factor": "Gas Price Passthrough",
            "model": "ERCOT_Full", "variable": "henry_hub_spot",
            "coefficient": round(gas_coef_ercot, 2),
            "p_value": round(gas_p_ercot, 4) if gas_p_ercot else None,
            "tier": "direct", "unit": "$/MWh per $/MMBtu",
        })

    # Add HH significant vars ranked by |coefficient|
    hh_ranked = sorted(
        [(v, d) for v, d in hh_coefs.items() if d["significant"]],
        key=lambda x: abs(x[1]["coefficient"]), reverse=True
    )
    for i, (var, d) in enumerate(hh_ranked):
        high_impact.append({
            "rank": len(high_impact) + 1,
            "factor": var, "model": "HH_Full", "variable": var,
            "coefficient": d["coefficient"],
            "p_value": d["p_value"],
            "tier": "indirect_via_gas",
        })

    graph = {
        "metadata": {
            "name": "DecarbIQ Energy Price Relationship Model",
            "version": "auto",
            "created": datetime.now().strftime("%Y-%m-%d"),
            "description": "Auto-generated from pipeline regression results",
            "regression_models": {
                "ercot_full": _model_stats("ercot_full"),
                "hh_full": _model_stats("hh_full"),
                "caiso_full": _model_stats("caiso_full"),
            },
            "pipeline_generated": True,
        },
        "nodes": {
            "natural_gas_price": {
                "category": "CENTRAL_HUB",
                "hh_full_model": {k: v for k, v in hh_coefs.items()},
                "hh_stats": _model_stats("hh_full"),
                "significant_drivers": hh_sig,
            },
            "electricity_price": {
                "TX_ERCOT": {
                    "gas_passthrough": round(gas_coef_ercot, 2) if gas_coef_ercot else None,
                    "gas_p_value": round(gas_p_ercot, 6) if gas_p_ercot else None,
                    "stats": _model_stats("ercot_full"),
                    "significant_vars": ercot_sig,
                    "all_coefficients": {
                        v: round(c["coefficient"], 4)
                        for v, c in reg_results.get("ercot_full", {}).get("coefficients", {}).items()
                        if v != "intercept"
                    },
                },
                "CA_CAISO": {
                    "gas_passthrough": round(gas_coef_caiso, 2) if gas_coef_caiso else None,
                    "gas_p_value": round(gas_p_caiso, 6) if gas_p_caiso else None,
                    "stats": _model_stats("caiso_full"),
                    "significant_vars": caiso_sig,
                    "all_coefficients": {
                        v: round(c["coefficient"], 4)
                        for v, c in reg_results.get("caiso_full", {}).get("coefficients", {}).items()
                        if v != "intercept"
                    },
                },
            },
        },
        "high_impact_factors": high_impact,
        "edges": [],
    }

    # Build edges from significant coefficients
    # HH model: factor → gas price
    for var, d in hh_coefs.items():
        if d["significant"]:
            graph["edges"].append({
                "from": var, "to": "natural_gas_price",
                "coefficient": d["coefficient"],
                "p_value": d["p_value"],
                "model": "HH_Full",
            })

    # ERCOT model: factor → ERCOT price
    if "ercot_full" in reg_results:
        for var, c in reg_results["ercot_full"]["coefficients"].items():
            if var != "intercept" and c["significant"]:
                graph["edges"].append({
                    "from": var, "to": "TX_ERCOT",
                    "coefficient": round(c["coefficient"], 4),
                    "p_value": round(c["p_value"], 6) if c["p_value"] else None,
                    "model": "ERCOT_Full",
                })

    # CAISO model: factor → CAISO price
    if "caiso_full" in reg_results:
        for var, c in reg_results["caiso_full"]["coefficients"].items():
            if var != "intercept" and c["significant"]:
                graph["edges"].append({
                    "from": var, "to": "CA_CAISO",
                    "coefficient": round(c["coefficient"], 4),
                    "p_value": round(c["p_value"], 6) if c["p_value"] else None,
                    "model": "CAISO_Full",
                })

    # Gas → ERCOT and Gas → CAISO structural edges
    if gas_coef_ercot is not None:
        graph["edges"].append({
            "from": "natural_gas_price", "to": "TX_ERCOT",
            "coefficient": round(gas_coef_ercot, 2),
            "p_value": round(gas_p_ercot, 6) if gas_p_ercot else None,
            "model": "ERCOT_Full", "dominant": True,
        })
    if gas_coef_caiso is not None:
        graph["edges"].append({
            "from": "natural_gas_price", "to": "CA_CAISO",
            "coefficient": round(gas_coef_caiso, 2),
            "p_value": round(gas_p_caiso, 6) if gas_p_caiso else None,
            "model": "CAISO_Full",
        })

    with open(REL_GRAPH_JSON, "w") as f:
        json.dump(graph, f, indent=2)
    print(f"  Saved: {REL_GRAPH_JSON}")
    print(f"  Nodes: {len(graph['nodes'])}")
    print(f"  Edges: {len(graph['edges'])}")
    print(f"  High-impact factors: {len(high_impact)}")

    return graph


# ============================================================================
# STEP 10: GENERATE CHORD DIAGRAM
# ============================================================================
def step10_chord_diagram(reg_results):
    banner("STEP 10: Generate chord diagram from regression results")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as patches
        from matplotlib.path import Path as MplPath
        from matplotlib.colors import to_rgba
        import matplotlib.patheffects as pe
    except ImportError:
        print("  WARNING: matplotlib not available, skipping chord diagram")
        return

    def _coef_safe(model_key, var):
        if model_key not in reg_results:
            return 0.0, 1.0, False
        coefs = reg_results[model_key].get("coefficients", {})
        if var not in coefs:
            return 0.0, 1.0, False
        c = coefs[var]
        return (c["coefficient"] or 0.0, c["p_value"] or 1.0, c["significant"] or False)

    # Extract key coefficients from regressions
    gas_ercot, gas_ercot_p, _ = _coef_safe("ercot_full", "henry_hub_spot")
    gas_caiso, gas_caiso_p, _ = _coef_safe("caiso_full", "henry_hub_spot")

    # HH Full significant vars
    hh_vars = {}
    if "hh_full" in reg_results:
        for v, c in reg_results["hh_full"]["coefficients"].items():
            if v != "intercept":
                hh_vars[v] = (c["coefficient"] or 0.0, c["p_value"] or 1.0, c["significant"] or False)

    # ── Build nodes dynamically from regression variables ──────────────
    # Fixed structure nodes
    nodes = [
        ("ERCOT\nWholesale",     "#d35400", 14),   # 0
        ("CAISO\nWholesale",     "#a04000", 10),   # 1
        ("Gen Mix\n(TX)",        "#b7950b",  8),    # 2
        ("Henry Hub\nGas Price", "#e74c3c", 18),   # 3
    ]
    node_idx = {"ercot": 0, "caiso": 1, "genmix": 2, "gas": 3}

    # Category color map
    cat_colors = {
        "supply": ("#2980b9", "#3498db", "#1a5276"),
        "demand": ("#27ae60", "#2ecc71", "#1e8449", "#82e0aa"),
        "data_center": ("#e91e63", "#c2185b", "#880e4f"),
        "policy": ("#8e44ad", "#9b59b6", "#7d3c98", "#af7ac5"),
        "ca_policy": ("#5b2c6f", "#6c3483"),
        "queue": ("#ff9800", "#e65100"),
    }

    # Map variables to categories and display names
    var_display = {
        # Supply
        "shale_era": ("Shale\nProduction", "supply"),
        "lng_exports_bcfd": ("LNG\nExports", "supply"),
        "us_net_exporter": ("Net\nExporter", "supply"),
        "gas_rigs": ("Drilling\nActivity", "supply"),
        # Macro demand
        "us_gdp_growth_pct": ("GDP\nGrowth", "demand"),
        "us_industrial_prod_index": ("Industrial\nProduction", "demand"),
        "electric_power_bcfd": ("Power Sector\nDemand", "demand"),
        "is_summer": ("Summer\nSeasonal", "demand"),
        "us_hdd": ("US Heating\nDegree-Days", "demand"),
        "us_cdd": ("US Cooling\nDegree-Days", "demand"),
        "tx_hdd": ("TX Heating\nDegree-Days", "demand"),
        "tx_cdd": ("TX Cooling\nDegree-Days", "demand"),
        "ca_hdd": ("CA Heating\nDegree-Days", "demand"),
        "ca_cdd": ("CA Cooling\nDegree-Days", "demand"),
        # Data centers
        "us_data_center_twh": ("US Data\nCenter TWh", "data_center"),
        "tx_data_center_twh": ("TX DC Load", "data_center"),
        "ercot_large_load_queue_gw": ("ERCOT Large\nLoad Queue", "data_center"),
        # Policy
        "ira_active": ("IRA\nActive", "policy"),
        "n_ira_credits": ("IRA\nCredits", "policy"),
        "cumulative_ferc_reforms": ("FERC\nReforms", "policy"),
        "tx_crez_active": ("TX CREZ", "policy"),
        "ptc_active": ("PTC\nActive", "policy"),
        "ptc_rate_cents_kwh": ("PTC\nRate", "policy"),
        "itc_rate_pct": ("ITC\nRate", "policy"),
        "policy_intensity_index": ("Policy\nIntensity", "policy"),
        "mats_era": ("MATS\nRule", "policy"),
        # CA policy
        "ca_rps_target_pct": ("CA RPS\nTarget", "ca_policy"),
        "ca_cap_trade_active": ("CA Cap\n& Trade", "ca_policy"),
        "ca_nem_compensation_level": ("CA NEM\nLevel", "ca_policy"),
        "ca_storage_mandate": ("CA Storage\nMandate", "ca_policy"),
        "ca_solar_mandate": ("CA Solar\nMandate", "ca_policy"),
        "der_market_access": ("DER Market\nAccess", "ca_policy"),
        "n_rps_states": ("RPS\nStates", "ca_policy"),
        # Queue
        "queue_backlog_gw": ("Queue\nBacklog", "queue"),
        # Gen mix
        "tx_gas_gen_pct": ("TX Gas\n47%", "genmix"),
        "tx_wind_gen_pct": ("TX Wind\n25%", "genmix"),
        "tx_coal_gen_pct": ("TX Coal\n16%", "genmix"),
        "ca_gas_gen_pct": ("CA Gas\nGen %", "genmix"),
        "ca_wind_gen_pct": ("CA Wind\nGen %", "genmix"),
        "ca_solar_gen_pct": ("CA Solar\nGen %", "genmix"),
        # Other demand
        "tx_gdp_growth_pct": ("TX GDP\nGrowth", "demand"),
        "tx_ercot_demand_twh": ("ERCOT\nDemand TWh", "demand"),
    }

    # Collect all variables that appear in any model
    all_vars = set()
    for model_key in ["ercot_full", "caiso_full", "hh_full"]:
        if model_key in reg_results:
            for v in reg_results[model_key]["coefficients"]:
                if v not in ("intercept", "is_winter", "henry_hub_spot"):
                    all_vars.add(v)

    # Build dynamic nodes for each variable, grouped by category
    cat_counter = {}
    connections = []

    # Add Gas→ERCOT and Gas→CAISO (the big passthroughs)
    connections.append((3, 0, abs(gas_ercot), "#e74c3c", abs(gas_ercot) > 20))
    connections.append((3, 1, abs(gas_caiso), "#a04000", abs(gas_caiso) > 10))
    connections.append((3, 2, 6.0, "#b7950b", False))  # Gas → Gen Mix structural

    # Group vars by category for ordering
    cat_order = ["supply", "demand", "data_center", "policy", "ca_policy", "queue"]
    ordered_vars = []
    for cat in cat_order:
        cat_vars = [(v, var_display.get(v, (v, cat)))
                    for v in sorted(all_vars) if var_display.get(v, (v, "other"))[1] == cat]
        ordered_vars.extend(cat_vars)
    # Add any remaining
    seen = {v for v, _ in ordered_vars}
    for v in sorted(all_vars):
        if v not in seen:
            ordered_vars.append((v, var_display.get(v, (v.replace("_", "\n"), "other"))))

    for var, (display_name, cat) in ordered_vars:
        colors = cat_colors.get(cat, ("#666", "#777"))
        ci = cat_counter.get(cat, 0)
        color = colors[ci % len(colors)]
        cat_counter[cat] = ci + 1

        # Determine node weight from significance
        is_hh_sig = hh_vars.get(var, (0, 1, False))[2]
        ercot_coefs = reg_results.get("ercot_full", {}).get("coefficients", {})
        caiso_coefs = reg_results.get("caiso_full", {}).get("coefficients", {})
        is_ercot_sig = ercot_coefs.get(var, {}).get("significant", False)
        is_caiso_sig = caiso_coefs.get(var, {}).get("significant", False)

        weight = 4
        if is_hh_sig or is_ercot_sig or is_caiso_sig:
            weight = 7

        idx = len(nodes)
        nodes.append((display_name, color, weight))
        node_idx[var] = idx

        # Add connections: var → gas price (from HH model)
        if var in hh_vars:
            coef, pv, sig = hh_vars[var]
            mag = max(abs(coef) * 3.5, 1.5)
            if sig:
                mag = max(mag, 6.0)
            connections.append((idx, 3, min(mag, 18.0), color, sig))

        # Add connections: var → ERCOT (from ERCOT model)
        if var in ercot_coefs and var != "henry_hub_spot":
            coef = ercot_coefs[var].get("coefficient", 0)
            sig = ercot_coefs[var].get("significant", False)
            mag = max(abs(coef) * 0.15, 1.0)
            if sig:
                mag = max(mag, 5.0)
            connections.append((idx, 0, min(mag, 15.0), color, sig))

        # Add connections: var → CAISO (from CAISO model)
        if var in caiso_coefs and var != "henry_hub_spot":
            coef = caiso_coefs[var].get("coefficient", 0)
            sig = caiso_coefs[var].get("significant", False)
            mag = max(abs(coef) * 0.3, 1.0)
            if sig:
                mag = max(mag, 5.0)
            connections.append((idx, 1, min(mag, 15.0), color, sig))

    # Gen Mix → ERCOT structural
    connections.append((2, 0, 5.0, "#b7950b", False))

    # ── Now render the chord diagram ──────────────────────────────────
    GAP_DEG = 1.5
    OUTER_R = 1.0
    ARC_WIDTH = 0.11
    CHORD_SCALE = 0.004
    LABEL_R = 1.17

    def _chord_path(ts1, te1, ts2, te2, r):
        n_pts = 30
        t1 = np.linspace(ts1, te1, n_pts)
        a1x, a1y = r * np.cos(t1), r * np.sin(t1)
        t2 = np.linspace(ts2, te2, n_pts)
        a2x, a2y = r * np.cos(t2), r * np.sin(t2)
        verts, codes = [], []
        for i, (x, y) in enumerate(zip(a1x, a1y)):
            verts.append((x, y))
            codes.append(MplPath.MOVETO if i == 0 else MplPath.LINETO)
        verts.append((0, 0)); codes.append(MplPath.CURVE3)
        verts.append((a2x[0], a2y[0])); codes.append(MplPath.CURVE3)
        for x, y in zip(a2x, a2y):
            verts.append((x, y)); codes.append(MplPath.LINETO)
        verts.append((0, 0)); codes.append(MplPath.CURVE3)
        verts.append((a1x[0], a1y[0])); codes.append(MplPath.CURVE3)
        verts.append((a1x[0], a1y[0])); codes.append(MplPath.CLOSEPOLY)
        return MplPath(verts, codes)

    fig, ax = plt.subplots(figsize=(20, 20), facecolor='#0f0f1e')
    ax.set_facecolor('#0f0f1e')
    ax.set_xlim(-1.65, 1.65)
    ax.set_ylim(-1.65, 1.65)
    ax.set_aspect('equal')
    ax.axis('off')

    # Compute arc angles
    nn = len(nodes)
    total_w = sum(w for _, _, w in nodes)
    avail_deg = 360.0 - nn * GAP_DEG

    arcs_deg, cur = [], 90.0
    for _, _, w in nodes:
        span = avail_deg * (w / total_w)
        arcs_deg.append((cur, cur + span))
        cur += span + GAP_DEG
    arcs = [(np.radians(s), np.radians(e)) for s, e in arcs_deg]

    inner_r = OUTER_R - ARC_WIDTH

    # Draw outer arcs
    for i, (name, color, _) in enumerate(nodes):
        ts, te = arcs[i]
        t_o = np.linspace(ts, te, 60)
        t_i = np.linspace(te, ts, 60)
        vx = np.concatenate([OUTER_R * np.cos(t_o), inner_r * np.cos(t_i),
                             [OUTER_R * np.cos(t_o[0])]])
        vy = np.concatenate([OUTER_R * np.sin(t_o), inner_r * np.sin(t_i),
                             [OUTER_R * np.sin(t_o[0])]])
        ax.fill(vx, vy, color=color, alpha=0.90, zorder=3)
        ax.plot(OUTER_R * np.cos(t_o), OUTER_R * np.sin(t_o),
                color='white', linewidth=0.5, alpha=0.2, zorder=4)
        for t_cap in [ts, te]:
            ax.plot([inner_r * np.cos(t_cap), OUTER_R * np.cos(t_cap)],
                    [inner_r * np.sin(t_cap), OUTER_R * np.sin(t_cap)],
                    color='#0f0f1e', linewidth=1.2, zorder=4)

    # Draw chords
    arc_used = [0.0] * nn
    sorted_conns = sorted(connections, key=lambda c: c[2])

    for fi, ti, mag, color, dominant in sorted_conns:
        if fi >= nn or ti >= nn:
            continue
        fs, fe = arcs[fi]
        ts, te = arcs[ti]
        f_span = fe - fs
        t_span = te - ts
        if f_span <= 0 or t_span <= 0:
            continue

        cw = mag * CHORD_SCALE
        wf = min(cw, f_span * 0.40)
        wt = min(cw, t_span * 0.40)

        cs1 = fs + arc_used[fi]
        ce1 = cs1 + wf
        cs2 = ts + arc_used[ti]
        ce2 = cs2 + wt

        if ce1 > fe:
            ce1 = fe; cs1 = max(fs, ce1 - wf)
        if ce2 > te:
            ce2 = te; cs2 = max(ts, ce2 - wt)

        arc_used[fi] += wf + 0.003
        arc_used[ti] += wt + 0.003

        path = _chord_path(cs1, ce1, cs2, ce2, inner_r)
        alpha = 0.80 if dominant else (0.50 if mag > 5 else 0.30)

        patch = patches.PathPatch(
            path,
            facecolor=to_rgba(color, alpha=alpha),
            edgecolor=to_rgba(color, alpha=alpha * 0.4),
            linewidth=0.3, zorder=2 if not dominant else 2.5
        )
        ax.add_patch(patch)

    # Labels
    for i, (name, color, _) in enumerate(nodes):
        mid = (arcs[i][0] + arcs[i][1]) / 2
        lx = LABEL_R * np.cos(mid)
        ly = LABEL_R * np.sin(mid)
        angle = np.degrees(mid)
        if 90 < angle % 360 < 270:
            rot, ha = angle - 180, 'right'
        else:
            rot, ha = angle, 'left'
        norm_a = angle % 360
        if (75 < norm_a < 105) or (255 < norm_a < 285):
            ha = 'center'
        fs = 13 if i < 4 else 10
        ax.text(lx, ly, name, fontsize=fs, fontweight='bold',
                color=color, ha=ha, va='center',
                rotation=rot, rotation_mode='anchor',
                fontfamily='Arial', linespacing=0.85,
                path_effects=[pe.withStroke(linewidth=3, foreground='#0f0f1e')])

    # Center text
    circle_bg = plt.Circle((0, 0), 0.28, facecolor='#0f0f1e',
                           edgecolor='#333', linewidth=1, zorder=2.8)
    ax.add_patch(circle_bg)
    ax.text(0, 0.10, "DecarbIQ", fontsize=22, fontweight='bold',
            color='white', ha='center', va='center', fontfamily='Arial',
            path_effects=[pe.withStroke(linewidth=2, foreground='#0f0f1e')], zorder=3)
    ax.text(0, -0.04, "ERCOT Price", fontsize=12,
            color='#bbb', ha='center', va='center', fontfamily='Arial', zorder=3)
    ax.text(0, -0.15, "Model v2.0", fontsize=10,
            color='#777', ha='center', va='center', fontfamily='Arial', zorder=3)

    # Footer with model stats
    stats_parts = []
    for key, label in [("hh_full", "HH"), ("ercot_full", "ERCOT"), ("caiso_full", "CAISO")]:
        if key in reg_results:
            r = reg_results[key]
            stats_parts.append(f"{label} R\u00b2={r['r_squared']:.3f} (n={r['n']})")
    ax.text(0, -1.55, "  |  ".join(stats_parts),
            fontsize=9, color='#888', ha='center', fontfamily='Arial')
    ax.text(0, -1.50, "Auto-generated from pipeline regression results",
            fontsize=8, color='#666', ha='center', fontfamily='Arial')

    fig.savefig(CHORD_PNG, dpi=200, bbox_inches='tight',
                facecolor='#0f0f1e', edgecolor='none')
    fig.savefig(CHORD_SVG, bbox_inches='tight',
                facecolor='#0f0f1e', edgecolor='none')
    plt.close(fig)

    print(f"  Saved: {CHORD_PNG}")
    print(f"  Saved: {CHORD_SVG}")
    print(f"  Nodes: {nn}, Connections: {len(connections)}")


# ============================================================================
# STEP 11: FUTURE PROJECTION RELATIONSHIP MAP (2025-2035)
# ============================================================================
def step11_projection_map(reg_results, mc_results, evt_results, master, reg_v2=None):
    banner("STEP 11: Build future projection relationship map (2025-2035)")

    # ── Helper: extract coefficient from regression results ────────────
    def _coef(model_key, var):
        if model_key not in reg_results:
            return 0.0
        coefs = reg_results[model_key].get("coefficients", {})
        return coefs.get(var, {}).get("coefficient", 0.0) or 0.0

    def _sig(model_key, var):
        if model_key not in reg_results:
            return False
        coefs = reg_results[model_key].get("coefficients", {})
        return coefs.get(var, {}).get("significant", False)

    # Helper for step2b coefficients (same nested format as step2)
    def _coef_v2(model_key, var):
        if not reg_v2 or model_key not in reg_v2:
            return 0.0
        coefs = reg_v2[model_key].get("coefficients", {})
        return coefs.get(var, {}).get("coefficient", 0.0) or 0.0

    # ── Model coefficients ─────────────────────────────────────────────
    # Use step2b best HH variant if available (includes lng_utilization, itc, ptc)
    best_hh_key = None
    if reg_v2:
        best_r2 = -1
        for key in reg_v2:
            if (key.startswith("hh_") and not key.endswith("_quantile")
                    and isinstance(reg_v2[key], dict) and "adj_r_squared" in reg_v2[key]):
                if reg_v2[key]["adj_r_squared"] > best_r2:
                    best_r2 = reg_v2[key]["adj_r_squared"]
                    best_hh_key = key

    if best_hh_key:
        hh_v2_coefs = reg_v2[best_hh_key].get("coefficients", {})
        hh_coefs = {}
        for var, vdict in hh_v2_coefs.items():
            if var in ("const", "intercept"):
                continue
            hh_coefs[var] = vdict.get("coefficient", 0) or 0
    else:
        # Fallback to step2 hh_full
        hh_coefs = {
            "us_gdp_growth_pct":       _coef("hh_full", "us_gdp_growth_pct"),
            "us_industrial_prod_index": _coef("hh_full", "us_industrial_prod_index"),
            "us_data_center_twh":      _coef("hh_full", "us_data_center_twh"),
            "electric_power_bcfd":     _coef("hh_full", "electric_power_bcfd"),
            "is_summer":               _coef("hh_full", "is_summer"),
            "is_winter":               _coef("hh_full", "is_winter"),
            "itc_rate_pct":            _coef("hh_full", "itc_rate_pct"),
            "ptc_rate_cents_kwh":      _coef("hh_full", "ptc_rate_cents_kwh"),
            "cumulative_ferc_reforms": _coef("hh_full", "cumulative_ferc_reforms"),
            "queue_backlog_gw":        _coef("hh_full", "queue_backlog_gw"),
        }
    ercot_gas_passthrough = _coef("ercot_full", "henry_hub_spot")
    caiso_gas_passthrough = _coef("caiso_full", "henry_hub_spot")

    # ── Gas price forecasts from Monte Carlo ───────────────────────────
    gas_forecasts = {}
    if isinstance(mc_results, dict):
        annual = mc_results.get("annual_forecasts", mc_results.get("forecasts", {}))
        if isinstance(annual, dict):
            for yr_str, data in annual.items():
                yr = int(yr_str) if isinstance(yr_str, str) else yr_str
                if isinstance(data, dict):
                    gas_forecasts[yr] = {
                        "mean": data.get("mean", 0),
                        "p10": data.get("p10", data.get("percentiles", {}).get("p10", 0)),
                        "p50": data.get("median", data.get("p50", data.get("percentiles", {}).get("p50", 0))),
                        "p90": data.get("p90", data.get("percentiles", {}).get("p90", 0)),
                    }
    # Extend to 2035: hold last MC year's forecast flat (no assumed trend beyond model horizon)
    # The MC already encodes mean-reversion + events + GARCH volatility through 2030.
    # Extrapolating toward a hardcoded equilibrium adds unjustified directional bias.
    if gas_forecasts:
        last_yr = max(gas_forecasts.keys())
        last_data = gas_forecasts[last_yr]
        for yr in range(last_yr + 1, 2036):
            gas_forecasts[yr] = {
                "mean": last_data["mean"],
                "p10": last_data["p10"],
                "p50": last_data["p50"],
                "p90": last_data["p90"],
            }

    # ── Latest observed values from master ─────────────────────────────
    latest = master.iloc[-1].to_dict() if len(master) > 0 else {}
    latest_gas = float(latest.get("henry_hub_spot", 3.0))

    # Ensure 2025 uses observed gas price (not MC), for smooth transition
    if 2025 not in gas_forecasts:
        gas_forecasts[2025] = {
            "mean": latest_gas, "p10": latest_gas * 0.8,
            "p50": latest_gas, "p90": latest_gas * 1.2,
        }

    # ── Variable projections 2025-2035 ─────────────────────────────────
    years = list(range(2025, 2036))

    # GDP growth: trend from 2.8% → 2.2% (CBO-like trajectory)
    gdp_proj = {yr: round(2.8 - 0.06 * (yr - 2025), 2) for yr in years}

    # Industrial production index: slow growth from 103.5
    indprod_proj = {yr: round(103.5 + 0.8 * (yr - 2025), 1) for yr in years}

    # Data center TWh (US): CAGR 8.47% from 200 TWh
    dc_proj = {yr: round(200 * (1.0847 ** (yr - 2024)), 1) for yr in years}

    # TX data center TWh: 6.2 → ~20 TWh by 2030, ~35 TWh by 2035
    tx_dc_proj = {yr: round(6.2 * (1.18 ** (yr - 2024)), 1) for yr in years}

    # Electric power demand (Bcf/d): slow growth with electrification
    elec_proj = {yr: round(35.5 + 0.4 * (yr - 2025), 1) for yr in years}

    # Queue backlog (GW): peaks ~3200 by 2027 then slowly declines with FERC reforms
    queue_proj = {}
    for yr in years:
        if yr <= 2027:
            queue_proj[yr] = round(2600 + 200 * (yr - 2025), 0)
        else:
            queue_proj[yr] = round(3000 - 100 * (yr - 2027), 0)

    # FERC reforms: 12 now, +1-2 per year
    ferc_proj = {yr: min(12 + (yr - 2025), 22) for yr in years}

    # ITC/PTC rate projections (shared across markets)
    itc_proj = {yr: 30.0 if yr <= 2032 else max(10, 30 - 4 * (yr - 2032)) for yr in years}
    ptc_proj = {yr: 2.75 if yr <= 2032 else max(0.5, 2.75 - 0.5 * (yr - 2032)) for yr in years}

    # LNG utilization % projection: grows from ~65% toward 85-90% as capacity fills
    lng_util_proj = {yr: round(min(92, 65 + 3.0 * (yr - 2025)), 1) for yr in years}

    # IRA/OBBBA active: base case stays active, with rollback scenario
    ira_proj_base = {yr: 1 for yr in years}
    ira_proj_rollback = {yr: 0 if yr >= 2027 else 1 for yr in years}

    # CA Generation Mix projections
    ca_gas_proj = {yr: round(max(20, 40.5 - 1.8 * (yr - 2025)), 1) for yr in years}
    ca_wind_proj = {yr: round(min(12, 4.0 + 0.7 * (yr - 2025)), 1) for yr in years}
    ca_solar_proj = {yr: round(min(40, 17.0 + 2.0 * (yr - 2025)), 1) for yr in years}

    # CA-specific policy projections
    ca_rps_proj = {yr: round(min(100, 60 + 4 * (yr - 2025)), 1) for yr in years}
    ca_allowance_proj = {yr: round(min(60, 40 + 2.0 * (yr - 2025)), 1) for yr in years}
    n_rps_proj = {yr: min(12, 9 + max(0, yr - 2027)) for yr in years}

    # CA battery capacity (GW): ~14 GW in 2024, +5 GW/yr
    ca_batt_base = float(latest.get("ca_battery_capacity_gw", 14.1))
    ca_batt_proj = {yr: round(ca_batt_base + 5.0 * (yr - 2025), 1) for yr in years}

    # CA curtailment: grows with solar penetration
    ca_curtail_proj = {yr: round(min(3000, 800 + 200 * (yr - 2025)), 0) for yr in years}

    # CA demand (TWh): slow growth
    ca_demand_proj = {yr: round(270 + 3.0 * (yr - 2025), 1) for yr in years}

    # TX Generation Mix projections
    tx_gas_proj = {yr: round(max(35, 51.8 - 1.5 * (yr - 2025)), 1) for yr in years}
    tx_wind_proj = {yr: round(min(35, 21.9 + 1.2 * (yr - 2025)), 1) for yr in years}
    tx_solar_proj = {yr: round(min(20, 7.2 + 1.3 * (yr - 2025)), 1) for yr in years}
    tx_coal_proj = {yr: round(max(0, 6.8 - 0.8 * (yr - 2025)), 1) for yr in years}

    # Renewable intermittency (from event projections)
    renew_pct_proj = {yr: round(min(55, 29.1 + 2.15 * (yr - 2025)), 1) for yr in years}

    # TX battery capacity (GW): ~5 GW in 2024, growing ~3 GW/yr
    tx_batt_base = float(latest.get("tx_battery_capacity_gw", 5.0))
    tx_batt_proj = {yr: round(tx_batt_base + 3.0 * (yr - 2025), 1) for yr in years}

    # TX demand (TWh): growing with data centers and electrification
    tx_demand_proj = {yr: round(425 + 8 * (yr - 2025), 0) for yr in years}

    # ERCOT congestion ($/MWh): rises with renewable growth and load growth
    tx_congestion_proj = {yr: round(min(15, 5.0 + 0.5 * (yr - 2025)), 1) for yr in years}

    # ERCOT spike count: expected to moderate with battery storage
    tx_spike_proj = {yr: round(max(50, 150 - 10 * (yr - 2025)), 0) for yr in years}

    # HDD/CDD annual mean monthly values (for annual projection equations)
    # Use recent historical average as "normal weather" for all projection years
    _hdd_cdd_avg = {}
    for col in ["us_hdd", "us_cdd", "tx_hdd", "tx_cdd", "ca_hdd", "ca_cdd"]:
        if col in master.columns:
            _hdd_cdd_avg[col] = float(master[col].dropna().mean())
        else:
            _hdd_cdd_avg[col] = 0
    # Constant projections: normal weather every year
    us_hdd_proj = {yr: round(_hdd_cdd_avg.get("us_hdd", 0), 0) for yr in years}
    us_cdd_proj = {yr: round(_hdd_cdd_avg.get("us_cdd", 0), 0) for yr in years}
    tx_hdd_proj = {yr: round(_hdd_cdd_avg.get("tx_hdd", 0), 0) for yr in years}
    tx_cdd_proj = {yr: round(_hdd_cdd_avg.get("tx_cdd", 0), 0) for yr in years}
    ca_hdd_proj = {yr: round(_hdd_cdd_avg.get("ca_hdd", 0), 0) for yr in years}
    ca_cdd_proj = {yr: round(_hdd_cdd_avg.get("ca_cdd", 0), 0) for yr in years}

    # ── Compute projected gas price impacts from each driver ───────────
    # delta_gas = sum(coef * delta_variable) for each year
    # Use latest observed values as base for all HH regression variables
    _seasonal_vars_s11 = {"is_winter", "is_summer", "us_hdd", "us_cdd", "tx_hdd", "tx_cdd", "ca_hdd", "ca_cdd"}
    base_vals = {}
    for var in hh_coefs:
        if var in _seasonal_vars_s11:
            continue
        base_vals[var] = float(latest.get(var, 0))
    # Ensure key variables have reasonable base values even if latest is zero
    if base_vals.get("us_gdp_growth_pct", 0) == 0:
        base_vals["us_gdp_growth_pct"] = 2.8
    if base_vals.get("us_industrial_prod_index", 0) == 0:
        base_vals["us_industrial_prod_index"] = 103.5
    if base_vals.get("electric_power_bcfd", 0) == 0:
        base_vals["electric_power_bcfd"] = 35.5

    # Storage deviation: project normal levels (0% deviation from 5yr avg)
    storage_proj = {yr: 0.0 for yr in years}

    # HDD/CDD departure: project normal weather (0% departure)
    hdd_dep_proj = {yr: 0.0 for yr in years}
    cdd_dep_proj = {yr: 0.0 for yr in years}

    # All HH regression variable projections (must match step6 MC projections)
    all_hh_projections = {
        "us_gdp_growth_pct": gdp_proj,
        "us_industrial_prod_index": indprod_proj,
        "us_data_center_twh": dc_proj,
        "electric_power_bcfd": elec_proj,
        "cumulative_ferc_reforms": ferc_proj,
        "queue_backlog_gw": queue_proj,
        "itc_rate_pct": itc_proj,
        "ptc_rate_cents_kwh": ptc_proj,
        "lng_utilization_pct": lng_util_proj,
        "us_ng_storage_vs_5yr_pct": storage_proj,
        "us_hdd_departure_pct": hdd_dep_proj,
        "us_cdd_departure_pct": cdd_dep_proj,
    }

    # Cap variable deltas to ±2σ of in-sample range (same logic as step6 MC)
    hh_var_caps = {}
    for var in hh_coefs:
        if var in _seasonal_vars_s11:
            continue
        if var in master.columns:
            var_std = float(master[var].dropna().std())
            hh_var_caps[var] = 2.0 * var_std if var_std > 0 else float("inf")

    driver_impacts = {}  # year → {variable: delta_gas_price}
    for yr in years:
        impacts = {}
        proj_vals = {}
        for var in hh_coefs:
            if var in _seasonal_vars_s11:
                continue
            if var in all_hh_projections:
                proj_vals[var] = all_hh_projections[var].get(yr, base_vals.get(var, 0))
            else:
                proj_vals[var] = base_vals.get(var, 0)
        for var, proj_v in proj_vals.items():
            raw_delta = proj_v - base_vals[var]
            cap = hh_var_caps.get(var, float("inf"))
            capped_delta = max(-cap, min(cap, raw_delta))
            coef = hh_coefs.get(var, 0)
            impacts[var] = round(coef * capped_delta, 4)
        driver_impacts[yr] = impacts

    # ── ERCOT variable projections for full regression equation ─────────
    # Scarcity intensity: from ERCOT daily RTM data. Post-2020 mean (ex-Uri) ~300.
    # Increases with renewable penetration (intermittency → more scarcity events)
    scarcity_base = 200.0  # 2024 observed ~131, long-run post-shale mean ~200
    scarcity_proj = {}
    for yr in years:
        # Scarcity grows with renewable penetration: more intermittent capacity
        # → more hours with tight supply/demand margins
        renew_share = renew_pct_proj.get(yr, 30)
        # Each 1% renewable increase adds ~5 to scarcity intensity
        scarcity_proj[yr] = round(scarcity_base + max(0, renew_share - 29.1) * 5.0, 1)

    # ERCOT interconnection queue (GW): large load queue separate from gen queue
    ercot_queue_proj = {}
    for yr in years:
        # Queue was ~230 GW in 2024, growing with data center/industrial demand
        ercot_queue_proj[yr] = round(230 + 15 * (yr - 2024), 0)

    # IRA tax credit count
    ira_credits_proj = {yr: min(15, 11 + (yr - 2024)) for yr in years}

    # Historical means for interaction term centering (from master data)
    hh_hist_mean = float(latest.get("henry_hub_spot", 3.5))
    wind_hist_mean = float(latest.get("tx_wind_gen_pct", 20.0))
    scarcity_hist_mean = 430.0  # from full dataset

    # ── Find best ERCOT model from step2b, fall back to step2 ────────
    best_ercot_key = None
    ercot_model_coefs = {}
    ercot_model_intercept = 0.0
    if reg_v2:
        best_r2 = -1
        for key in reg_v2:
            if (key.startswith("ercot_") and not key.endswith("_quantile")
                    and isinstance(reg_v2[key], dict) and "adj_r_squared" in reg_v2[key]):
                if reg_v2[key]["adj_r_squared"] > best_r2:
                    best_r2 = reg_v2[key]["adj_r_squared"]
                    best_ercot_key = key

    if best_ercot_key:
        model = reg_v2[best_ercot_key]
        raw_coefs = model.get("coefficients", {})
        for var, vdict in raw_coefs.items():
            if var in ("const", "Intercept", "intercept"):
                ercot_model_intercept = float(vdict.get("coefficient", 0) if isinstance(vdict, dict) else vdict)
            else:
                ercot_model_coefs[var] = float(vdict.get("coefficient", 0) if isinstance(vdict, dict) else vdict)
        print(f"  ERCOT projection using: {best_ercot_key} (adj R²={best_r2:.4f}, {len(ercot_model_coefs)} vars)")
    else:
        # Fall back to step2 ercot_full: just gas passthrough
        ercot_model_intercept = float(
            reg_results.get("ercot_full", {}).get("coefficients", {})
            .get("intercept", {}).get("coefficient", -53.6) or -53.6
        )
        ercot_model_coefs = {"henry_hub_spot": ercot_gas_passthrough}
        print(f"  ERCOT projection: fallback to step2 gas passthrough (${ercot_gas_passthrough:.2f}/MWh per $/MMBtu)")

    def _ercot_predict(gas_price, yr):
        """Full ERCOT regression equation with all projected variables."""
        # Build projected values for every variable in the model
        proj_vals = {
            "henry_hub_spot": gas_price,
            "is_summer": 3.0 / 12.0,  # fallback if model still has is_summer
            "is_winter": 3.0 / 12.0,  # fallback if model still has is_winter
            "tx_hdd": tx_hdd_proj.get(yr, _hdd_cdd_avg.get("tx_hdd", 0)),
            "tx_cdd": tx_cdd_proj.get(yr, _hdd_cdd_avg.get("tx_cdd", 0)),
            "tx_gdp_growth_pct": gdp_proj.get(yr, 2.5),
            "us_industrial_prod_index": indprod_proj.get(yr, 105),
            "tx_gas_gen_pct": tx_gas_proj.get(yr, 45),
            "tx_wind_gen_pct": tx_wind_proj.get(yr, 25),
            "tx_solar_gen_pct": tx_solar_proj.get(yr, 10),
            "tx_coal_gen_pct": tx_coal_proj.get(yr, 3),
            "ercot_scarcity_intensity": scarcity_proj.get(yr, 200),
            "ercot_large_load_queue_gw": ercot_queue_proj.get(yr, 250),
            "n_ira_credits": ira_credits_proj.get(yr, 12),
            "itc_rate_pct": itc_proj.get(yr, 30),
            "ptc_rate_cents_kwh": ptc_proj.get(yr, 2.75),
            "ercot_actual_reserve_margin_pct": max(10, 18 - 0.5 * (yr - 2024)),
            # Previously missing step2b variables — now with projections
            "tx_battery_capacity_gw": tx_batt_proj.get(yr, 8),
            "tx_ercot_demand_twh": tx_demand_proj.get(yr, 450),
            "ercot_congestion_per_mwh": tx_congestion_proj.get(yr, 7),
            "ercot_spike_count": tx_spike_proj.get(yr, 100),
            "tx_data_center_twh": tx_dc_proj.get(yr, 10),
            "cumulative_ferc_reforms": ferc_proj.get(yr, 14),
            "queue_backlog_gw": queue_proj.get(yr, 2800),
        }
        # Interaction and squared terms (mean-centered, matching step1b)
        proj_vals["gas_x_tx_wind"] = (gas_price - hh_hist_mean) * (proj_vals["tx_wind_gen_pct"] - wind_hist_mean)
        proj_vals["gas_x_ercot_scarcity"] = (gas_price - hh_hist_mean) * (proj_vals["ercot_scarcity_intensity"] - scarcity_hist_mean)
        proj_vals["tx_wind_gen_pct_sq"] = proj_vals["tx_wind_gen_pct"] ** 2
        proj_vals["tx_solar_gen_pct_sq"] = proj_vals["tx_solar_gen_pct"] ** 2
        # Additional interaction terms for variant D
        dc_hist_mean = float(latest.get("tx_data_center_twh", 6.2))
        gas_gen_hist_mean = float(latest.get("tx_gas_gen_pct", 51.8))
        renew_hist_mean = float(latest.get("tx_renewable_pct", 29.1))
        proj_vals["dc_x_gas_gen_pct"] = (proj_vals.get("tx_data_center_twh", 10) - dc_hist_mean) * (proj_vals["tx_gas_gen_pct"] - gas_gen_hist_mean)
        proj_vals["dc_x_renewable_share"] = (proj_vals.get("tx_data_center_twh", 10) - dc_hist_mean) * (renew_pct_proj.get(yr, 35) - renew_hist_mean)
        # Structural break interactions (post_ordc=1 since 2014, post_shale=1 since 2010)
        proj_vals["post_ordc_x_henry"] = 1 * gas_price
        proj_vals["post_shale_x_henry"] = 1 * gas_price

        pred = ercot_model_intercept
        for var, coef in ercot_model_coefs.items():
            if var in proj_vals:
                pred += coef * proj_vals[var]
            elif var in latest:
                pred += coef * float(latest.get(var, 0))
        return pred

    # ── Compute projected electricity prices ───────────────────────────
    # For the base year (2025), use latest observed values — consistent with
    # how gas is handled (2025 gas = observed $3.01, not model prediction).
    # Model predictions start from 2026 onwards.
    latest_ercot_obs = float(latest.get("ercot_wholesale_mwh", 25))
    latest_caiso_obs = float(latest.get("caiso_wholesale_mwh",
                             latest.get("caiso_wholesale_historical_mwh", 40)))
    # ── Delta method for smooth projections ──────────────────────────
    # P_yr = P_obs + [regression(X_yr, gas_yr) - regression(X_2025, gas_2025)]
    # Projects only the CHANGE from variable movements, anchored to observed.
    # Zero tuning parameters; consistent with gas MC (starts from observed).
    ercot_base_pred = _ercot_predict(latest_gas, 2025)
    print(f"  Delta method: ERCOT base pred (2025) = ${ercot_base_pred:.2f}, observed = ${latest_ercot_obs:.2f}, gap absorbed = ${ercot_base_pred - latest_ercot_obs:.2f}")
    # CAISO base prediction computed inside loop (predict function depends on yr)
    caiso_base_pred = None

    ercot_proj = {}
    caiso_proj = {}
    for yr in years:
        gas_mean = gas_forecasts.get(yr, {}).get("mean", latest_gas)
        gas_p10 = gas_forecasts.get(yr, {}).get("p10", gas_mean - 2)
        gas_p90 = gas_forecasts.get(yr, {}).get("p90", gas_mean + 2)

        # ERCOT: Delta method — project changes from observed
        # P_yr = P_obs + [f(X_yr, gas_yr) - f(X_2025, gas_2025)]
        if yr == 2025:
            e_mean = latest_ercot_obs
            e_low = latest_ercot_obs * 0.85
            e_high = latest_ercot_obs * 1.15
        else:
            e_mean = latest_ercot_obs + (_ercot_predict(gas_mean, yr) - ercot_base_pred)
            e_low = latest_ercot_obs + (_ercot_predict(gas_p10, yr) - ercot_base_pred)
            e_high = latest_ercot_obs + (_ercot_predict(gas_p90, yr) - ercot_base_pred)

        ercot_proj[yr] = {
            "mean": round(max(0, e_mean), 2),
            "low": round(max(0, e_low), 2),
            "high": round(max(0, e_high), 2),
            "gas_component": round(ercot_model_coefs.get("henry_hub_spot", ercot_gas_passthrough) * gas_mean, 2),
        }

        # CAISO: use step2b variant A (VIF-pruned baseline — most stable)
        # Variant A preferred over D/F which overfit with only 27 observations
        best_caiso_key = None
        if reg_v2:
            # Prefer variant A (VIF-pruned baseline) for stability
            if "caiso_A" in reg_v2 and isinstance(reg_v2["caiso_A"], dict) and "adj_r_squared" in reg_v2["caiso_A"]:
                best_caiso_key = "caiso_A"
            else:
                for key in reg_v2:
                    if key.startswith("caiso_") and isinstance(reg_v2[key], dict) and "adj_r_squared" in reg_v2[key]:
                        best_caiso_key = key
                        break

        if best_caiso_key:
            # Use step2b coefficients (with battery storage, curtailment, etc.)
            caiso_v2_coefs = reg_v2[best_caiso_key].get("coefficients", {})
            # Validate: model MUST include henry_hub_spot for projection
            # (VIF pruning may drop it with n=27, making model useless for forecasting)
            if "henry_hub_spot" not in caiso_v2_coefs:
                print(f"  WARNING: {best_caiso_key} dropped henry_hub_spot (VIF pruning) — falling back to step2 caiso_full")
                best_caiso_key = None
        if best_caiso_key:
            caiso_v2_coefs = reg_v2[best_caiso_key].get("coefficients", {})
            caiso_intercept = float(
                caiso_v2_coefs.get("const", {}).get("coefficient",
                caiso_v2_coefs.get("intercept", {}).get("coefficient", 0)) or 0
            )
            # Get latest observed CAISO values as base
            latest_caiso = master.iloc[-1].to_dict() if len(master) > 0 else {}

            # CAISO variable projections for each year
            caiso_var_proj = {
                "henry_hub_spot": None,  # passed as argument
                "is_summer": 0.33,  # fallback if model still has is_summer
                "is_winter": 0.25,  # fallback if model still has is_winter
                "ca_hdd": ca_hdd_proj.get(yr, _hdd_cdd_avg.get("ca_hdd", 0)),
                "ca_cdd": ca_cdd_proj.get(yr, _hdd_cdd_avg.get("ca_cdd", 0)),
                "ca_battery_capacity_gw": ca_batt_proj.get(yr, ca_batt_base),
                "ca_gdp_growth_pct": gdp_proj.get(yr, 2.5),
                "ca_gas_gen_pct": ca_gas_proj.get(yr, 35),
                "ca_wind_gen_pct": ca_wind_proj.get(yr, 8),
                "ca_solar_gen_pct": ca_solar_proj.get(yr, 25),
                "ca_data_center_twh": round(3.0 * (1.15 ** (yr - 2024)), 1),
                "ca_caiso_demand_twh": ca_demand_proj.get(yr, 280),
                "caiso_total_curtail_gwh": ca_curtail_proj.get(yr, 1000),
                "caiso_monthly_negative_hours": round(min(200, 50 + 15 * (yr - 2025)), 0),
                "caiso_monthly_congestion_avg": round(min(10, 3 + 0.5 * (yr - 2025)), 1),
                "itc_rate_pct": itc_proj.get(yr, 30),
                "ptc_rate_cents_kwh": ptc_proj.get(yr, 2.75),
                "ca_rps_target_pct": ca_rps_proj.get(yr, 80),
                "ca_allowance_price_per_ton": ca_allowance_proj.get(yr, 45),
                "ca_allowance_spread": round(min(20, 12 + 0.8 * (yr - 2025)), 1),
                "ca_nem_compensation_level": 3,  # reduced NEM 3.0
                "queue_backlog_gw": queue_proj.get(yr, 2600),
                "n_rps_states": n_rps_proj.get(yr, 9),
                "ca_storage_mandate": 1,
                "der_market_access": 1,
                "ca_solar_mandate": 1,
            }

            # Compute projection using all regression variables with projections
            def _caiso_predict(gas_price):
                pred = caiso_intercept
                for var, vdict in caiso_v2_coefs.items():
                    if var in ("const", "intercept"):
                        continue
                    coef = vdict.get("coefficient", 0) or 0
                    if var == "henry_hub_spot":
                        pred += coef * gas_price
                    elif var in caiso_var_proj and caiso_var_proj[var] is not None:
                        pred += coef * caiso_var_proj[var]
                    else:
                        # Fallback to latest observed
                        val = float(latest_caiso.get(var, 0))
                        pred += coef * val
                return pred

            c_mean = _caiso_predict(gas_mean)
            c_low = _caiso_predict(gas_p10)
            c_high = _caiso_predict(gas_p90)
        else:
            # Fallback to step2 caiso_full — use ALL coefficients dynamically
            caiso_full_coefs = reg_results.get("caiso_full", {}).get("coefficients", {})
            caiso_intercept = float(
                caiso_full_coefs.get("intercept", {}).get("coefficient", 0) or 0
            )
            # Use same projection dicts as step2b path for variable values
            caiso_s2_proj = {
                "henry_hub_spot": None,  # passed as argument
                "is_summer": 0.33, "is_winter": 0.25,  # fallback for step2 model
                "ca_hdd": ca_hdd_proj.get(yr, _hdd_cdd_avg.get("ca_hdd", 0)),
                "ca_cdd": ca_cdd_proj.get(yr, _hdd_cdd_avg.get("ca_cdd", 0)),
                "ca_gdp_growth_pct": gdp_proj.get(yr, 2.5),
                "ca_wind_gen_pct": ca_wind_proj.get(yr, 8),
                "ca_gas_gen_pct": ca_gas_proj.get(yr, 35),
                "queue_backlog_gw": queue_proj.get(yr, 2600),
                "n_rps_states": n_rps_proj.get(yr, 9),
                "ca_rps_target_pct": ca_rps_proj.get(yr, 80),
                "ca_allowance_price_per_ton": ca_allowance_proj.get(yr, 45),
                "ca_battery_capacity_gw": ca_batt_proj.get(yr, ca_batt_base),
                "ca_data_center_twh": round(3.0 * (1.15 ** (yr - 2024)), 1),
                "ca_caiso_demand_twh": ca_demand_proj.get(yr, 280),
                "caiso_total_curtail_gwh": ca_curtail_proj.get(yr, 1000),
                "itc_rate_pct": itc_proj.get(yr, 30),
                "ptc_rate_cents_kwh": ptc_proj.get(yr, 2.75),
            }
            latest_caiso_s2 = master.iloc[-1].to_dict() if len(master) > 0 else {}

            def _caiso_s2_predict(gas_price):
                pred = caiso_intercept
                for var, vdict in caiso_full_coefs.items():
                    if var in ("const", "intercept"):
                        continue
                    coef = vdict.get("coefficient", 0) or 0
                    if var == "henry_hub_spot":
                        pred += coef * gas_price
                    elif var in caiso_s2_proj and caiso_s2_proj[var] is not None:
                        pred += coef * caiso_s2_proj[var]
                    else:
                        pred += coef * float(latest_caiso_s2.get(var, 0))
                return pred

            c_mean = _caiso_s2_predict(gas_mean)
            c_low = _caiso_s2_predict(gas_p10)
            c_high = _caiso_s2_predict(gas_p90)

        # CAISO: Delta method — project changes from observed
        # P_yr = P_obs + [f(X_yr, gas_yr) - f(X_2025, gas_2025)]
        if yr == 2025:
            caiso_base_pred = c_mean  # Store regression prediction at 2025 variables
            print(f"  Delta method: CAISO base pred (2025) = ${caiso_base_pred:.2f}, observed = ${latest_caiso_obs:.2f}, gap absorbed = ${caiso_base_pred - latest_caiso_obs:.2f}")
            c_mean = latest_caiso_obs
            c_low = latest_caiso_obs * 0.85
            c_high = latest_caiso_obs * 1.15
        else:
            eq_c_mean, eq_c_low, eq_c_high = c_mean, c_low, c_high
            c_mean = latest_caiso_obs + (eq_c_mean - caiso_base_pred)
            c_low = latest_caiso_obs + (eq_c_low - caiso_base_pred)
            c_high = latest_caiso_obs + (eq_c_high - caiso_base_pred)

        # Gas component: use the coefficient from whichever model produced the projection
        if best_caiso_key and "henry_hub_spot" in caiso_v2_coefs:
            _caiso_gas_coef = float(caiso_v2_coefs["henry_hub_spot"].get("coefficient", caiso_gas_passthrough) or caiso_gas_passthrough)
        else:
            _caiso_gas_coef = caiso_gas_passthrough
        caiso_proj[yr] = {
            "mean": round(max(0, c_mean), 2),
            "low": round(max(0, c_low), 2),
            "high": round(max(0, c_high), 2),
            "gas_component": round(_caiso_gas_coef * gas_mean, 2),
            "source": best_caiso_key or "caiso_full",
        }

    # ── Scenario analysis: IRA rollback impact ─────────────────────────
    # Use itc_rate_pct coefficient (IRA rollback → ITC drops from 30% to 10%)
    ira_rollback_impact = {}
    itc_coef = hh_coefs.get("itc_rate_pct", 0)
    ptc_coef = hh_coefs.get("ptc_rate_cents_kwh", 0)
    for yr in years:
        if ira_proj_rollback[yr] == 0 and ira_proj_base[yr] == 1:
            # IRA rollback: ITC drops from 30% to 10%, PTC drops from 2.75 to 0
            delta_gas = itc_coef * (10 - 30) + ptc_coef * (0 - 2.75)
            ira_rollback_impact[yr] = {
                "gas_delta": round(delta_gas, 2),
                "ercot_delta": round(ercot_gas_passthrough * delta_gas, 2),
                "caiso_delta": round(caiso_gas_passthrough * delta_gas, 2),
            }
        else:
            ira_rollback_impact[yr] = {"gas_delta": 0, "ercot_delta": 0, "caiso_delta": 0}

    # ── Build the projection map JSON ──────────────────────────────────
    proj_map = {
        "metadata": {
            "name": "DecarbIQ Future Projection Relationship Map",
            "version": "1.0",
            "created": datetime.now().strftime("%Y-%m-%d"),
            "horizon": "2025-2035",
            "description": "Forward-looking projection combining regression coefficients with variable trajectories",
            "models_used": {
                "hh_full": {"r2": reg_results.get("hh_full", {}).get("r_squared", 0),
                            "n": reg_results.get("hh_full", {}).get("n", 0)},
                "ercot_full": {"r2": reg_results.get("ercot_full", {}).get("r_squared", 0),
                               "n": reg_results.get("ercot_full", {}).get("n", 0)},
                "caiso_full": {"r2": reg_results.get("caiso_full", {}).get("r_squared", 0),
                               "n": reg_results.get("caiso_full", {}).get("n", 0)},
            },
            "base_year": 2024,
            "latest_observed": {
                "henry_hub": latest_gas,
                "ercot_wholesale": float(latest.get("ercot_wholesale_mwh", latest.get("ercot_wholesale_historical_mwh", 40))),
                "caiso_wholesale": float(latest.get("caiso_wholesale_mwh", latest.get("caiso_wholesale_historical_mwh", 40))),
            },
        },
        "coefficients": {
            "hh_full": {k: round(v, 4) for k, v in hh_coefs.items()},
            "ercot_gas_passthrough": round(ercot_gas_passthrough, 2),
            "caiso_gas_passthrough": round(caiso_gas_passthrough, 2),
        },
        "variable_projections": {
            str(yr): {
                "gas_price": gas_forecasts.get(yr, {}),
                "us_gdp_growth_pct": gdp_proj[yr],
                "us_industrial_prod_index": indprod_proj[yr],
                "us_data_center_twh": dc_proj[yr],
                "tx_data_center_twh": tx_dc_proj[yr],
                "electric_power_bcfd": elec_proj[yr],
                "queue_backlog_gw": queue_proj[yr],
                "cumulative_ferc_reforms": ferc_proj[yr],
                "ira_active": ira_proj_base[yr],
                "tx_gas_gen_pct": tx_gas_proj[yr],
                "tx_wind_gen_pct": tx_wind_proj[yr],
                "tx_solar_gen_pct": tx_solar_proj[yr],
                "tx_coal_gen_pct": tx_coal_proj[yr],
                "tx_renewable_pct": renew_pct_proj[yr],
            } for yr in years
        },
        "driver_impacts_on_gas": {
            str(yr): impacts for yr, impacts in driver_impacts.items()
        },
        "electricity_price_projections": {
            "ERCOT": {str(yr): v for yr, v in ercot_proj.items()},
            "CAISO": {str(yr): v for yr, v in caiso_proj.items()},
        },
        "scenarios": {
            "ira_rollback": {
                "description": "IRA repealed/defunded starting 2027",
                "probability": "15-25% per Congressional discussions",
                "impact": {str(yr): v for yr, v in ira_rollback_impact.items()},
            },
            "data_center_boom": {
                "description": "DC load doubles vs base case (AI demand surge)",
                "gas_impact_2030": round(hh_coefs.get("us_data_center_twh", 0)
                                         * (dc_proj[2030] - 200) * 2, 2),
                "ercot_impact_2030": round(ercot_gas_passthrough
                                           * hh_coefs.get("us_data_center_twh", 0)
                                           * (dc_proj[2030] - 200) * 2, 2),
            },
            "queue_breakthrough": {
                "description": "FERC reforms clear 50% of backlog by 2030",
                "gas_impact": round(hh_coefs.get("queue_backlog_gw", 0) * (-1300), 2),
                "ercot_impact": round(ercot_gas_passthrough
                                      * hh_coefs.get("queue_backlog_gw", 0) * (-1300), 2),
            },
        },
        "key_risks": [
            {"risk": "IRA rollback", "probability": "15-25%",
             "gas_impact": f"+${abs(itc_coef * (-20) + ptc_coef * (-2.75)):.2f}/MMBtu",
             "ercot_impact": f"+${abs(ercot_gas_passthrough * (itc_coef * (-20) + ptc_coef * (-2.75))):.0f}/MWh"},
            {"risk": "Queue delays persist", "probability": "40-60%",
             "mechanism": "Delays renewable capacity, sustains gas dependence"},
            {"risk": "Data center demand spike", "probability": "30-50%",
             "mechanism": f"Each 100 TWh adds +${abs(hh_coefs.get('us_data_center_twh', 0)) * 100:.1f}/MMBtu to gas"},
            {"risk": "Extreme weather (hurricane/polar vortex)", "probability": "45-65% annually",
             "mechanism": "+$0.30 to +$5.00/MMBtu seasonal spikes"},
            {"risk": "LNG export surge", "probability": "High",
             "mechanism": "14→30 Bcf/d by 2035, tightens domestic supply"},
        ],
    }

    with open(PROJECTION_JSON, "w") as f:
        json.dump(proj_map, f, indent=2)
    print(f"  Saved: {PROJECTION_JSON}")

    # ── Generate projection visualization ──────────────────────────────
    _render_projection_chart(years, gas_forecasts, ercot_proj, caiso_proj,
                             driver_impacts, dc_proj, queue_proj,
                             renew_pct_proj, hh_coefs, ercot_gas_passthrough,
                             reg_results, gdp_proj, ferc_proj)

    return proj_map


def _render_projection_chart(years, gas_fc, ercot_p, caiso_p,
                             driver_imp, dc_proj, queue_proj,
                             renew_proj, hh_coefs, ercot_pt, reg,
                             gdp_proj, ferc_proj):
    """Render a multi-panel projection chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mticker
    except ImportError:
        print("  WARNING: matplotlib not available, skipping projection chart")
        return

    fig, axes = plt.subplots(3, 2, figsize=(22, 18), facecolor='#0f0f1e')
    fig.suptitle("DecarbIQ Forward Projection Map 2025-2035",
                 fontsize=22, fontweight='bold', color='white', y=0.98)
    fig.text(0.5, 0.955,
             f"HH R\u00b2={reg.get('hh_full', {}).get('r_squared', 0):.3f} | "
             f"ERCOT R\u00b2={reg.get('ercot_full', {}).get('r_squared', 0):.3f} | "
             f"CAISO R\u00b2={reg.get('caiso_full', {}).get('r_squared', 0):.3f}",
             ha='center', fontsize=11, color='#888')

    for ax in axes.flat:
        ax.set_facecolor('#1a1a2e')
        ax.tick_params(colors='#aaa', labelsize=9)
        ax.spines['bottom'].set_color('#444')
        ax.spines['left'].set_color('#444')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(True, alpha=0.15, color='#666')

    # ── Panel 1: Gas Price Forecast ────────────────────────────────────
    ax1 = axes[0, 0]
    yrs = sorted(gas_fc.keys())
    means = [gas_fc[y]["mean"] for y in yrs]
    p10s = [gas_fc[y]["p10"] for y in yrs]
    p90s = [gas_fc[y]["p90"] for y in yrs]
    ax1.fill_between(yrs, p10s, p90s, alpha=0.25, color='#e74c3c', label='P10-P90')
    ax1.plot(yrs, means, color='#e74c3c', linewidth=2.5, marker='o', markersize=4, label='Mean')
    ax1.plot(yrs, [gas_fc[y]["p50"] for y in yrs], color='#f1c40f',
             linewidth=1.5, linestyle='--', label='P50')
    ax1.axhline(y=7.79, color='#888', linestyle=':', alpha=0.5, label='Long-run eq.')
    ax1.set_title("Henry Hub Gas Price Forecast", color='white', fontsize=13, fontweight='bold')
    ax1.set_ylabel("$/MMBtu", color='#aaa')
    ax1.legend(fontsize=8, facecolor='#1a1a2e', edgecolor='#444', labelcolor='#ccc')

    # ── Panel 2: ERCOT Electricity Price ───────────────────────────────
    ax2 = axes[0, 1]
    e_yrs = sorted(ercot_p.keys())
    e_means = [ercot_p[y]["mean"] for y in e_yrs]
    e_lows = [ercot_p[y]["low"] for y in e_yrs]
    e_highs = [ercot_p[y]["high"] for y in e_yrs]
    ax2.fill_between(e_yrs, e_lows, e_highs, alpha=0.25, color='#d35400')
    ax2.plot(e_yrs, e_means, color='#d35400', linewidth=2.5, marker='s', markersize=4, label='ERCOT Mean')
    c_yrs = sorted(caiso_p.keys())
    c_means = [caiso_p[y]["mean"] for y in c_yrs]
    c_lows = [caiso_p[y]["low"] for y in c_yrs]
    c_highs = [caiso_p[y]["high"] for y in c_yrs]
    ax2.fill_between(c_yrs, c_lows, c_highs, alpha=0.15, color='#795548')
    ax2.plot(c_yrs, c_means, color='#795548', linewidth=2, marker='^', markersize=4, label='CAISO Mean')
    ax2.set_title("Electricity Price Projections", color='white', fontsize=13, fontweight='bold')
    ax2.set_ylabel("$/MWh", color='#aaa')
    ax2.legend(fontsize=8, facecolor='#1a1a2e', edgecolor='#444', labelcolor='#ccc')

    # ── Panel 3: Driver Impacts on Gas ─────────────────────────────────
    ax3 = axes[1, 0]
    drivers = ["us_gdp_growth_pct", "us_data_center_twh", "cumulative_ferc_reforms",
               "queue_backlog_gw", "electric_power_bcfd"]
    colors_d = ["#27ae60", "#e91e63", "#9b59b6", "#ff9800", "#1e8449"]
    labels_d = ["GDP Growth", "Data Centers", "FERC Reforms", "Queue Backlog", "Power Demand"]
    for drv, clr, lbl in zip(drivers, colors_d, labels_d):
        vals = [driver_imp[yr].get(drv, 0) for yr in years]
        ax3.plot(years, vals, color=clr, linewidth=2, marker='o', markersize=3, label=lbl)
    ax3.axhline(y=0, color='#666', linewidth=0.8, linestyle='-')
    ax3.set_title("Driver Impacts on Gas Price (vs 2024 baseline)",
                  color='white', fontsize=13, fontweight='bold')
    ax3.set_ylabel("\u0394 $/MMBtu", color='#aaa')
    ax3.legend(fontsize=8, facecolor='#1a1a2e', edgecolor='#444', labelcolor='#ccc', ncol=2)

    # ── Panel 4: Data Center & Queue Trajectories ──────────────────────
    ax4 = axes[1, 1]
    ax4_twin = ax4.twinx()
    ax4.bar([y - 0.15 for y in years], [dc_proj[y] for y in years],
            width=0.3, color='#e91e63', alpha=0.7, label='US DC Load (TWh)')
    ax4_twin.plot(years, [queue_proj[y] for y in years],
                  color='#ff9800', linewidth=2.5, marker='D', markersize=4, label='Queue (GW)')
    ax4.set_title("Data Center Load & Queue Backlog",
                  color='white', fontsize=13, fontweight='bold')
    ax4.set_ylabel("TWh", color='#e91e63')
    ax4_twin.set_ylabel("GW", color='#ff9800')
    ax4_twin.tick_params(colors='#ff9800', labelsize=9)
    ax4_twin.spines['right'].set_color('#ff9800')
    ax4_twin.spines['top'].set_visible(False)
    lines1, labels1 = ax4.get_legend_handles_labels()
    lines2, labels2 = ax4_twin.get_legend_handles_labels()
    ax4.legend(lines1 + lines2, labels1 + labels2, fontsize=8,
               facecolor='#1a1a2e', edgecolor='#444', labelcolor='#ccc')

    # ── Panel 5: TX Generation Mix Transition ──────────────────────────
    ax5 = axes[2, 0]
    gas_pct = [max(35, 51.8 - 1.5 * (y - 2025)) for y in years]
    wind_pct = [min(35, 21.9 + 1.2 * (y - 2025)) for y in years]
    solar_pct = [min(20, 7.2 + 1.3 * (y - 2025)) for y in years]
    coal_pct = [max(0, 6.8 - 0.8 * (y - 2025)) for y in years]
    other_pct = [100 - g - w - s - c for g, w, s, c in zip(gas_pct, wind_pct, solar_pct, coal_pct)]
    ax5.stackplot(years, gas_pct, wind_pct, solar_pct, coal_pct, other_pct,
                  labels=['Gas', 'Wind', 'Solar', 'Coal', 'Other'],
                  colors=['#c0392b', '#1abc9c', '#f39c12', '#7f8c8d', '#3498db'], alpha=0.8)
    ax5.set_title("TX (ERCOT) Generation Mix Projection",
                  color='white', fontsize=13, fontweight='bold')
    ax5.set_ylabel("% of Generation", color='#aaa')
    ax5.set_ylim(0, 100)
    ax5.legend(fontsize=8, facecolor='#1a1a2e', edgecolor='#444', labelcolor='#ccc',
               loc='center right')

    # ── Panel 6: Scenario Waterfall — 2030 ERCOT Price ─────────────────
    ax6 = axes[2, 1]
    gas_2030 = gas_fc.get(2030, {}).get("mean", 12.0)
    base_ercot_2030 = ercot_p.get(2030, {}).get("mean", 400)

    # Waterfall components
    wf_labels = ["Base\n2030", "+DC\nDemand", "+Queue\nDelay", "+GDP\nGrowth",
                 "+FERC\nReforms", "IRA\nRollback?", "Net\n2030"]
    dc_impact = round(ercot_pt * hh_coefs.get("us_data_center_twh", 0) * (dc_proj[2030] - 200), 1)
    queue_impact = round(ercot_pt * hh_coefs.get("queue_backlog_gw", 0) * (queue_proj[2030] - 2600), 1)
    gdp_impact = round(ercot_pt * hh_coefs.get("us_gdp_growth_pct", 0) * (gdp_proj[2030] - 2.8), 1)
    ferc_impact = round(ercot_pt * hh_coefs.get("cumulative_ferc_reforms", 0) * (ferc_proj[2030] - 12), 1)
    ira_impact = round(ercot_pt * (-hh_coefs.get("ira_active", 0)), 1)  # rollback scenario

    wf_values = [base_ercot_2030, dc_impact, queue_impact, gdp_impact,
                 ferc_impact, ira_impact,
                 base_ercot_2030 + dc_impact + queue_impact + gdp_impact + ferc_impact]
    wf_colors = ['#d35400', '#e91e63', '#ff9800', '#27ae60', '#9b59b6', '#e74c3c', '#f1c40f']

    # Draw waterfall
    bottoms = [0]
    cumulative = base_ercot_2030
    for i in range(1, len(wf_values) - 1):
        bottoms.append(cumulative)
        cumulative += wf_values[i]
    bottoms.append(0)

    for i, (lbl, val, bot, clr) in enumerate(zip(wf_labels, wf_values, bottoms, wf_colors)):
        if i == 0 or i == len(wf_values) - 1:
            ax6.bar(i, val, color=clr, alpha=0.85, width=0.6)
        else:
            ax6.bar(i, val, bottom=bot, color=clr, alpha=0.75, width=0.6,
                    edgecolor='white', linewidth=0.5)
            sign = "+" if val >= 0 else ""
            ax6.text(i, bot + val + (3 if val >= 0 else -8),
                     f"{sign}${val:.0f}", ha='center', fontsize=8, color=clr, fontweight='bold')

    ax6.set_xticks(range(len(wf_labels)))
    ax6.set_xticklabels(wf_labels, fontsize=8, color='#aaa')
    ax6.set_title("ERCOT 2030 Price Waterfall ($/MWh)",
                  color='white', fontsize=13, fontweight='bold')
    ax6.set_ylabel("$/MWh", color='#aaa')

    plt.tight_layout(rect=[0, 0.02, 1, 0.94])
    fig.savefig(PROJECTION_PNG, dpi=200, bbox_inches='tight',
                facecolor='#0f0f1e', edgecolor='none')
    plt.close(fig)
    print(f"  Saved: {PROJECTION_PNG}")


# ============================================================================
# STEP 12: ADVANCED SENSITIVITY SIMULATOR — NONLINEAR SHOCK PROPAGATION ENGINE
# ============================================================================

# --- Configuration constants ---

REGIMES = {
    "normal":   {"hh_range": (0, 5.0),  "ercot_mult": 1.0,  "caiso_mult": 1.0,  "label": "Normal"},
    "elevated": {"hh_range": (5.0, 8.0), "ercot_mult": 1.35, "caiso_mult": 1.15, "label": "Elevated"},
    "crisis":   {"hh_range": (8.0, 999), "ercot_mult": 1.80, "caiso_mult": 1.30, "label": "Crisis"},
}

SATURATION_CONFIG = {
    "tx_gas_gen_pct":      {"bounds": (0, 65),   "exponent": 1.0,  "type": "bounded"},
    "tx_wind_gen_pct":     {"bounds": (0, 50),   "exponent": 0.85, "type": "bounded"},
    "tx_coal_gen_pct":     {"bounds": (0, 20),   "exponent": 1.0,  "type": "bounded"},
    "tx_solar_gen_pct":    {"bounds": (0, 40),   "exponent": 0.85, "type": "bounded"},
    "ca_gas_gen_pct":      {"bounds": (0, 65),   "exponent": 1.0,  "type": "bounded"},
    "ca_solar_gen_pct":    {"bounds": (0, 40),   "exponent": 0.8,  "type": "bounded"},
    "ca_wind_gen_pct":     {"bounds": (0, 15),   "exponent": 1.0,  "type": "bounded"},
    "henry_hub_spot":      {"bounds": (0.5, 30), "exponent": 0.7,  "type": "bounded"},
    "electric_power_bcfd": {"bounds": (5, 50),   "exponent": 0.9,  "type": "bounded"},
    "queue_backlog_gw":    {"bounds": (0, 5000), "exponent": 0.65, "type": "concave"},
    "us_gdp_growth_pct":   {"bounds": (-8, 8),   "exponent": 1.0,  "type": "bounded"},
    "us_data_center_twh":  {"bounds": (20, 800), "exponent": 1.15, "type": "convex"},
    "ira_active":          {"bounds": (0, 1),    "exponent": 1.0,  "type": "binary"},
    "is_summer":           {"bounds": (0, 1),    "exponent": 1.0,  "type": "binary"},
    "is_winter":           {"bounds": (0, 1),    "exponent": 1.0,  "type": "binary"},
    "ca_cap_trade_active": {"bounds": (0, 1),    "exponent": 1.0,  "type": "binary"},
    "ptc_active":          {"bounds": (0, 1),    "exponent": 1.0,  "type": "binary"},
    # Step2b variables
    "tx_battery_capacity_gw":  {"bounds": (0, 50),    "exponent": 0.9, "type": "bounded"},
    "ca_battery_capacity_gw":  {"bounds": (0, 80),    "exponent": 0.9, "type": "bounded"},
    "lng_utilization_pct":     {"bounds": (0, 100),   "exponent": 1.0, "type": "bounded"},
    "us_ng_storage_vs_5yr_pct": {"bounds": (-50, 50), "exponent": 1.0, "type": "bounded"},
    "us_hdd_departure_pct":    {"bounds": (-40, 40),  "exponent": 1.0, "type": "bounded"},
    "us_cdd_departure_pct":    {"bounds": (-40, 40),  "exponent": 1.0, "type": "bounded"},
    "ercot_scarcity_intensity": {"bounds": (0, 50000), "exponent": 0.6, "type": "concave"},
    "ercot_spike_count":       {"bounds": (0, 500),   "exponent": 0.7, "type": "concave"},
    "ercot_actual_reserve_margin_pct": {"bounds": (5, 30), "exponent": 1.0, "type": "bounded"},
    "ercot_congestion_per_mwh": {"bounds": (0, 30),   "exponent": 0.8, "type": "bounded"},
    "caiso_total_curtail_gwh": {"bounds": (0, 5000),  "exponent": 0.75, "type": "concave"},
    "caiso_monthly_negative_hours": {"bounds": (0, 300), "exponent": 0.8, "type": "bounded"},
    "caiso_monthly_congestion_avg": {"bounds": (0, 20),  "exponent": 1.0, "type": "bounded"},
    "ca_allowance_price_per_ton": {"bounds": (10, 100), "exponent": 1.0, "type": "bounded"},
    "ca_allowance_spread":     {"bounds": (0, 30),    "exponent": 1.0, "type": "bounded"},
    "itc_rate_pct":            {"bounds": (0, 50),    "exponent": 1.0, "type": "bounded"},
    "ptc_rate_cents_kwh":      {"bounds": (0, 5),     "exponent": 1.0, "type": "bounded"},
    "ca_rps_target_pct":       {"bounds": (0, 100),   "exponent": 1.0, "type": "bounded"},
    # Interaction/squared terms: bounded by constituent ranges
    "gas_x_tx_wind":           {"bounds": (-200, 200),  "exponent": 1.0, "type": "bounded"},
    "gas_x_ercot_scarcity":    {"bounds": (-5000, 5000), "exponent": 0.7, "type": "bounded"},
    "tx_wind_gen_pct_sq":      {"bounds": (0, 2500),   "exponent": 0.85, "type": "bounded"},
    "tx_solar_gen_pct_sq":     {"bounds": (0, 1600),   "exponent": 0.85, "type": "bounded"},
    "ca_solar_gen_pct_sq":     {"bounds": (0, 1600),   "exponent": 0.8,  "type": "bounded"},
    "dc_x_gas_gen_pct":        {"bounds": (-500, 500), "exponent": 1.0, "type": "bounded"},
    "dc_x_renewable_share":    {"bounds": (-500, 500), "exponent": 1.0, "type": "bounded"},
    "ca_dc_x_solar":           {"bounds": (-500, 500), "exponent": 1.0, "type": "bounded"},
    "gas_x_ca_solar":          {"bounds": (-200, 200), "exponent": 1.0, "type": "bounded"},
    "post_ordc_x_henry":       {"bounds": (0, 30),    "exponent": 0.7, "type": "bounded"},
    "post_shale_x_henry":      {"bounds": (0, 30),    "exponent": 0.7, "type": "bounded"},
}

LAG_PROFILES = {
    "instant":    {"1m": 1.0,  "3m": 1.0,  "6m": 1.0,  "12m": 1.0,  "24m": 1.0},
    "fast":       {"1m": 0.7,  "3m": 0.9,  "6m": 1.0,  "12m": 1.0,  "24m": 1.0},
    "medium":     {"1m": 0.3,  "3m": 0.6,  "6m": 0.85, "12m": 1.0,  "24m": 1.0},
    "slow":       {"1m": 0.05, "3m": 0.15, "6m": 0.35, "12m": 0.65, "24m": 0.9},
    "structural": {"1m": 0.0,  "3m": 0.05, "6m": 0.15, "12m": 0.40, "24m": 0.75},
}

VARIABLE_LAG_MAP = {
    "henry_hub_spot": "instant", "is_summer": "instant", "is_winter": "instant",
    "electric_power_bcfd": "fast", "us_gdp_growth_pct": "fast",
    "us_industrial_prod_index": "fast",
    "tx_ercot_demand_twh": "fast", "ca_caiso_demand_twh": "fast",
    "ercot_scarcity_intensity": "instant", "ercot_spike_count": "instant",
    "ercot_congestion_per_mwh": "fast",
    "ercot_actual_reserve_margin_pct": "medium",
    "caiso_monthly_negative_hours": "fast", "caiso_monthly_congestion_avg": "fast",
    "tx_gas_gen_pct": "medium", "ca_gas_gen_pct": "medium",
    "tx_wind_gen_pct": "medium", "ca_wind_gen_pct": "medium",
    "tx_solar_gen_pct": "medium", "ca_solar_gen_pct": "medium",
    "itc_rate_pct": "medium", "ptc_rate_cents_kwh": "medium",
    "ptc_active": "medium", "ira_active": "medium",
    "lng_utilization_pct": "medium",
    "us_ng_storage_vs_5yr_pct": "instant",
    "us_hdd_departure_pct": "instant",
    "us_cdd_departure_pct": "instant",
    "ca_rps_target_pct": "slow", "ca_cap_trade_active": "slow",
    "ca_allowance_price_per_ton": "slow", "ca_allowance_spread": "slow",
    "n_rps_states": "slow", "cumulative_ferc_reforms": "slow",
    "queue_backlog_gw": "slow", "ercot_large_load_queue_gw": "slow",
    "policy_intensity_index": "slow", "n_ira_credits": "slow",
    "caiso_total_curtail_gwh": "slow",
    "tx_battery_capacity_gw": "structural", "ca_battery_capacity_gw": "structural",
    "tx_crez_active": "structural",
    "us_data_center_twh": "structural", "tx_data_center_twh": "structural",
    "ca_data_center_twh": "structural",
    "tx_coal_gen_pct": "structural",
    "ca_nem_compensation_level": "structural",
    # Interaction/squared terms inherit fastest component lag
    "gas_x_tx_wind": "instant", "gas_x_ercot_scarcity": "instant",
    "tx_wind_gen_pct_sq": "medium", "tx_solar_gen_pct_sq": "medium",
    "ca_solar_gen_pct_sq": "medium",
    "dc_x_gas_gen_pct": "medium", "dc_x_renewable_share": "medium",
    "ca_dc_x_solar": "medium", "gas_x_ca_solar": "instant",
    "post_ordc_x_henry": "instant", "post_shale_x_henry": "instant",
}

SEASONAL_AMPLIFICATION = {
    "summer": {
        "electric_power_bcfd": 1.40, "henry_hub_spot": 1.20,
        "tx_gas_gen_pct": 1.20, "tx_wind_gen_pct": 0.65,
        "tx_ercot_demand_twh": 1.30,
        "ca_solar_gen_pct": 0.75, "ca_gas_gen_pct": 1.25,
        "ca_caiso_demand_twh": 1.20,
        "us_data_center_twh": 1.0,
    },
    "winter": {
        "electric_power_bcfd": 1.15, "henry_hub_spot": 1.30,
        "tx_wind_gen_pct": 1.35, "tx_gas_gen_pct": 0.85,
        "tx_ercot_demand_twh": 1.10,
        "ca_solar_gen_pct": 0.50, "ca_gas_gen_pct": 1.30,
        "ca_caiso_demand_twh": 1.05,
    },
    "spring": {
        "ca_gas_gen_pct": 0.70, "ca_solar_gen_pct": 1.15,
        "ca_caiso_demand_twh": 0.85, "henry_hub_spot": 0.90,
        "tx_wind_gen_pct": 1.10, "tx_gas_gen_pct": 0.95,
    },
    "fall": {
        "tx_wind_gen_pct": 1.15, "tx_gas_gen_pct": 0.90,
        "electric_power_bcfd": 0.95,
        "ca_solar_gen_pct": 0.85, "ca_gas_gen_pct": 1.10,
    },
}

FEEDBACK_RULES = [
    {
        "name": "gas_renewable_stabilizer",
        "trigger_vars": ["henry_hub_spot", "electric_power_bcfd",
                         "us_gdp_growth_pct", "us_industrial_prod_index"],
        "target": "delta_gas", "strength": -0.13,
        "calibration_note": "wind response (~1pp/10$/MWh) x wind->gas displacement (-0.064/pp)",
        "time_constant_months": 30, "data_quality": "moderate",
    },
    {
        "name": "dc_cost_pressure",
        "trigger_vars": ["us_data_center_twh", "tx_data_center_twh", "ca_data_center_twh"],
        "target": "delta_gas", "strength": -0.07,
        "calibration_note": "DC coef(0.059) x passthrough(41.33) x elec cost elasticity(-0.15)",
        "time_constant_months": 18, "data_quality": "low",
    },
    {
        "name": "queue_amplifier",
        "trigger_vars": ["queue_backlog_gw", "ercot_large_load_queue_gw"],
        "target": "delta_gas", "strength": 0.09,
        "calibration_note": "queue app rate(~50GW/yr) x queue coef(-0.00985) x price sensitivity",
        "time_constant_months": 15, "data_quality": "moderate",
    },
    {
        "name": "policy_stabilizer",
        "trigger_vars": ["ira_active", "itc_rate_pct", "ptc_rate_cents_kwh",
                         "ca_rps_target_pct", "cumulative_ferc_reforms"],
        "target": "delta_ercot_total", "strength": -0.10,
        "calibration_note": "Historical policy expansion after price spikes (2005/2008/2015/2022)",
        "time_constant_months": 36, "data_quality": "low",
    },
]

VARIABLE_UNITS = {
    "henry_hub_spot": "$/MMBtu", "electric_power_bcfd": "Bcf/d",
    "us_gdp_growth_pct": "%", "us_industrial_prod_index": "index",
    "us_data_center_twh": "TWh", "tx_data_center_twh": "TWh",
    "ca_data_center_twh": "TWh",
    "queue_backlog_gw": "GW", "ercot_large_load_queue_gw": "GW",
    "ira_active": "binary", "is_summer": "binary", "is_winter": "binary",
    "itc_rate_pct": "%", "ptc_rate_cents_kwh": "¢/kWh",
    "ptc_active": "binary", "ca_cap_trade_active": "binary",
    "tx_gas_gen_pct": "%", "tx_wind_gen_pct": "%", "tx_coal_gen_pct": "%",
    "tx_solar_gen_pct": "%",
    "ca_gas_gen_pct": "%", "ca_solar_gen_pct": "%", "ca_wind_gen_pct": "%",
    "ca_rps_target_pct": "%", "n_rps_states": "count",
    "cumulative_ferc_reforms": "count", "n_ira_credits": "count",
    "policy_intensity_index": "index",
    "tx_ercot_demand_twh": "TWh", "ca_caiso_demand_twh": "TWh",
    "tx_crez_active": "binary", "ca_nem_compensation_level": "index",
    # Step2b variables
    "tx_battery_capacity_gw": "GW", "ca_battery_capacity_gw": "GW",
    "lng_utilization_pct": "%",
    "us_ng_storage_vs_5yr_pct": "%",
    "us_hdd_departure_pct": "%",
    "us_cdd_departure_pct": "%",
    "ercot_scarcity_intensity": "$/MWh×hrs", "ercot_spike_count": "count",
    "ercot_actual_reserve_margin_pct": "%", "ercot_congestion_per_mwh": "$/MWh",
    "caiso_total_curtail_gwh": "GWh", "caiso_monthly_negative_hours": "hours",
    "caiso_monthly_congestion_avg": "$/MWh",
    "ca_allowance_price_per_ton": "$/ton", "ca_allowance_spread": "$/ton",
    # Interaction/squared terms
    "gas_x_tx_wind": "interaction", "gas_x_ercot_scarcity": "interaction",
    "tx_wind_gen_pct_sq": "%²", "tx_solar_gen_pct_sq": "%²",
    "ca_solar_gen_pct_sq": "%²",
    "dc_x_gas_gen_pct": "interaction", "dc_x_renewable_share": "interaction",
    "ca_dc_x_solar": "interaction", "gas_x_ca_solar": "interaction",
    "post_ordc_x_henry": "interaction", "post_shale_x_henry": "interaction",
}


# --- Helper functions ---

def _get_season(system_state):
    month = system_state.get("month", 6)
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "fall"


def _calibrate_cross_market(reg_results, master):
    ercot_resids = np.array(reg_results.get("ercot_full", {}).get("residuals", []))
    caiso_resids = np.array(reg_results.get("caiso_full", {}).get("residuals", []))
    n_common = min(len(ercot_resids), len(caiso_resids))
    if n_common > 10 and np.std(ercot_resids[-n_common:]) > 0 and np.std(caiso_resids[-n_common:]) > 0:
        e_r = ercot_resids[-n_common:]
        c_r = caiso_resids[-n_common:]
        residual_correlation = float(np.corrcoef(e_r, c_r)[0, 1])
    else:
        residual_correlation = 0.35
        print("  WARNING: Insufficient data for cross-market correlation, using default 0.35")

    raw_corr = None
    if hasattr(master, "columns"):
        if "ercot_wholesale_mwh" in master.columns and "caiso_wholesale_mwh" in master.columns:
            overlap = master[["ercot_wholesale_mwh", "caiso_wholesale_mwh"]].dropna()
            if len(overlap) > 10:
                raw_corr = float(overlap.corr().iloc[0, 1])

    return {
        "residual_correlation": round(residual_correlation, 4),
        "raw_price_correlation": round(raw_corr, 4) if raw_corr is not None else None,
        "n_common_observations": n_common,
        "gas_competition_factor": 0.05,
        "calibration_method": "pearson_residual_correlation",
    }


def _saturate(delta_x, variable, catalog):
    cfg = SATURATION_CONFIG.get(variable, {"bounds": (None, None), "exponent": 1.0})
    info = catalog.get(variable, {})
    baseline = info.get("baseline_mean", 0)
    std = info.get("baseline_std", abs(delta_x) if delta_x != 0 else 1.0)

    new_val = baseline + delta_x
    lo, hi = cfg["bounds"]
    if lo is not None:
        new_val = max(lo, min(hi, new_val))
    bounded_delta = new_val - baseline

    exp = cfg["exponent"]
    if exp != 1.0 and std > 0 and abs(bounded_delta) > std:
        within_1std = std * (1 if bounded_delta > 0 else -1)
        excess = abs(bounded_delta) - std
        damped_excess = std * (excess / std) ** exp
        effective_delta = within_1std + damped_excess * (1 if bounded_delta > 0 else -1)
    else:
        effective_delta = bounded_delta

    return effective_delta, {"saturated": bounded_delta != delta_x, "exponent": exp}


# --- Stage classes ---

class SaturationFilter:
    def __init__(self, catalog):
        self.catalog = catalog

    def process(self, result):
        variable = result["variable"]
        raw_delta = result["raw_delta"]
        eff, meta = _saturate(raw_delta, variable, self.catalog)
        result["effective_delta"] = eff
        result["saturation_meta"] = meta
        return result


class LagFilter:
    def __init__(self, lag_map, profiles):
        self.lag_map = lag_map
        self.profiles = profiles

    def process(self, result):
        variable = result["variable"]
        horizon = result.get("time_horizon", "12m")
        lag_type = self.lag_map.get(variable, "fast")
        fraction = self.profiles[lag_type].get(horizon, 1.0)
        result["effective_delta"] = result["effective_delta"] * fraction
        result["lag_profile"] = lag_type
        result["lag_fraction"] = fraction
        result["lag_all_horizons"] = self.profiles[lag_type]
        return result


class SeasonalFilter:
    def __init__(self, amplification):
        self.amplification = amplification

    def process(self, result):
        season = _get_season(result.get("system_state", {}))
        variable = result["variable"]
        mult = self.amplification.get(season, {}).get(variable, 1.0)
        result["effective_delta"] = result["effective_delta"] * mult
        result["season"] = season
        result["seasonal_multiplier"] = mult
        return result


class RegimeDetector:
    def __init__(self, regimes):
        self.regimes = regimes

    def _detect(self, state):
        hh = state.get("henry_hub_spot", 4.12)
        for name, cfg in self.regimes.items():
            lo, hi = cfg["hh_range"]
            if lo <= hh < hi:
                return name
        return "normal"

    def process(self, result):
        state = result.get("system_state", {})
        result["regime_before"] = self._detect(state)
        post_state = dict(state)
        dg_est = result.get("effective_delta", 0)
        if result["variable"] == "henry_hub_spot":
            post_state["henry_hub_spot"] = state.get("henry_hub_spot", 4.12) + dg_est
        result["regime_after"] = self._detect(post_state)
        result["regime_transition"] = result["regime_before"] != result["regime_after"]
        return result


class GasTransmission:
    def __init__(self, hh_model):
        self.hh_model = hh_model

    def _get_coef(self, variable):
        c = self.hh_model.get("coefficients", {}).get(variable, {})
        return c.get("coefficient", 0)

    def _get_se(self, variable):
        # Prefer Newey-West HAC SE if available
        nw = self.hh_model.get("newey_west_se", {})
        if variable in nw and nw[variable] > 0:
            return nw[variable]
        c = self.hh_model.get("coefficients", {}).get(variable, {})
        return c.get("std_error", 0) or 0

    def _gas_supply_multiplier(self, state):
        gas_pct = state.get("tx_gas_gen_pct", 45)
        if gas_pct > 55:
            return 1.3
        if gas_pct > 45:
            return 1.1
        if gas_pct < 25:
            return 0.7
        return 1.0

    def process(self, result):
        variable = result["variable"]
        eff_delta = result["effective_delta"]
        state = result.get("system_state", {})

        if variable == "henry_hub_spot":
            result["delta_gas"] = eff_delta
            result["delta_gas_se"] = 0
            result["gas_state_multiplier"] = 1.0
        else:
            hh_coef = self._get_coef(variable)
            hh_se = self._get_se(variable)
            state_mult = self._gas_supply_multiplier(state)
            result["delta_gas"] = hh_coef * eff_delta * state_mult
            result["delta_gas_se"] = abs(hh_se * eff_delta * state_mult) if hh_se else 0
            result["gas_state_multiplier"] = state_mult
        return result


class CrossMarketTransmission:
    def __init__(self, reg_results, cross_market_params):
        self.shared_policy_vars = [
            "ira_active", "queue_backlog_gw", "cumulative_ferc_reforms",
            "itc_rate_pct", "ptc_rate_cents_kwh", "n_rps_states",
            "policy_intensity_index", "us_industrial_prod_index",
            "us_gdp_growth_pct",
        ]
        self.residual_correlation = cross_market_params["residual_correlation"]
        self.gas_competition_factor = cross_market_params["gas_competition_factor"]

    def process(self, result):
        variable = result["variable"]
        delta_gas = result.get("delta_gas", 0)
        result["cross_market_shared"] = variable in self.shared_policy_vars
        result["cross_market_residual_correlation"] = self.residual_correlation

        if variable.startswith("tx_") or variable.startswith("ercot"):
            result["caiso_gas_competition_premium"] = delta_gas * self.gas_competition_factor
            result["ercot_gas_competition_premium"] = 0
        elif variable.startswith("ca_") or variable.startswith("caiso"):
            result["ercot_gas_competition_premium"] = delta_gas * self.gas_competition_factor
            result["caiso_gas_competition_premium"] = 0
        else:
            result["ercot_gas_competition_premium"] = 0
            result["caiso_gas_competition_premium"] = 0
        return result


class ElectricityTransmission:
    def __init__(self, ercot_model, caiso_model):
        self.ercot = ercot_model
        self.caiso = caiso_model

    def _get_coef(self, market, variable):
        model = self.ercot if market == "ercot" else self.caiso
        c = model.get("coefficients", {}).get(variable, {})
        return c.get("coefficient", 0)

    def _get_se(self, market, variable):
        model = self.ercot if market == "ercot" else self.caiso
        # Prefer Newey-West HAC SE if available
        nw = model.get("newey_west_se", {})
        if variable in nw and nw[variable] > 0:
            return nw[variable]
        c = model.get("coefficients", {}).get(variable, {})
        return c.get("std_error", 0) or 0

    def _regime_passthrough(self, base_gas, delta_gas, market):
        gas_levels = sorted(REGIMES.values(), key=lambda r: r["hh_range"][0])
        total_impact = 0
        remaining = delta_gas
        current = base_gas
        mult_key = "ercot_mult" if market == "ercot" else "caiso_mult"
        base_pt = 41.33 if market == "ercot" else 14.19

        if delta_gas >= 0:
            for regime in gas_levels:
                lo, hi = regime["hh_range"]
                if current >= hi or remaining <= 0:
                    continue
                room = hi - max(current, lo)
                chunk = min(remaining, room)
                total_impact += chunk * base_pt * regime[mult_key]
                remaining -= chunk
                current += chunk
            if remaining > 0:
                total_impact += remaining * base_pt * gas_levels[-1][mult_key]
        else:
            for regime in reversed(gas_levels):
                lo, hi = regime["hh_range"]
                if current <= lo or remaining >= 0:
                    continue
                room = min(current, hi) - lo
                chunk = max(remaining, -room)
                total_impact += chunk * base_pt * regime[mult_key]
                remaining -= chunk
                current += chunk
            if remaining < 0:
                total_impact += remaining * base_pt * gas_levels[0][mult_key]
        return total_impact

    def process(self, result):
        delta_gas = result.get("delta_gas", 0)
        variable = result["variable"]
        eff_delta = result["effective_delta"]
        state = result.get("system_state", {})
        base_gas = state.get("henry_hub_spot", 4.12)

        ercot_indirect = self._regime_passthrough(base_gas, delta_gas, "ercot")
        caiso_indirect = self._regime_passthrough(base_gas, delta_gas, "caiso")

        if variable == "henry_hub_spot":
            ercot_direct = 0
            caiso_direct = 0
        else:
            ercot_direct = self._get_coef("ercot", variable) * eff_delta
            caiso_direct = self._get_coef("caiso", variable) * eff_delta

        caiso_indirect += result.get("caiso_gas_competition_premium", 0) * 14.19
        ercot_indirect += result.get("ercot_gas_competition_premium", 0) * 41.33

        ercot_direct_se = abs(self._get_se("ercot", variable) * eff_delta)
        caiso_direct_se = abs(self._get_se("caiso", variable) * eff_delta)

        result.update({
            "delta_ercot_indirect": round(ercot_indirect, 4),
            "delta_ercot_direct": round(ercot_direct, 4),
            "delta_ercot_total": round(ercot_indirect + ercot_direct, 4),
            "delta_caiso_indirect": round(caiso_indirect, 4),
            "delta_caiso_direct": round(caiso_direct, 4),
            "delta_caiso_total": round(caiso_indirect + caiso_direct, 4),
            "delta_ercot_se": ercot_direct_se,
            "delta_caiso_se": caiso_direct_se,
        })
        return result


class FeedbackAdjuster:
    def __init__(self, rules):
        self.rules = rules

    def process(self, result):
        variable = result["variable"]
        horizon = result.get("time_horizon", "12m")
        horizon_months = {"1m": 1, "3m": 3, "6m": 6, "12m": 12, "24m": 24}.get(horizon, 12)

        for rule in self.rules:
            if variable in rule["trigger_vars"]:
                tc = rule["time_constant_months"]
                realized = 1 - math.exp(-horizon_months / tc)
                effective_strength = rule["strength"] * realized
                multiplier = 1.0 / (1.0 - effective_strength) if abs(effective_strength) < 0.99 else 1.0

                target = rule["target"]
                if target in result:
                    result[target] *= multiplier

                result.setdefault("feedback_applied", []).append({
                    "loop": rule["name"],
                    "raw_strength": rule["strength"],
                    "realized_fraction": round(realized, 3),
                    "effective_strength": round(effective_strength, 4),
                    "multiplier": round(multiplier, 4),
                    "data_quality": rule["data_quality"],
                })
        return result


class UncertaintyQuantifier:
    DEFAULT_SE_MULTIPLIER = 0.5

    def __init__(self, reg_results, mc_params, master):
        self.reg_results = reg_results
        self.ercot_resid_std = reg_results.get("ercot_full", {}).get("residual_std", 111.58)
        self.caiso_resid_std = reg_results.get("caiso_full", {}).get("residual_std", 27.03)
        self.hh_resid_std = reg_results.get("hh_full", {}).get("residual_std", 1.0)
        self.mc_annual = mc_params.get("annual_forecasts", {}) if mc_params else {}

    def _get_se_or_default(self, model_name, variable):
        model = self.reg_results.get(model_name, {})
        # Prefer Newey-West HAC SE if available
        nw = model.get("newey_west_se", {})
        if variable in nw and nw[variable] > 0:
            return nw[variable], False
        coefs = model.get("coefficients", {})
        if variable in coefs:
            se = coefs[variable].get("std_error")
            coef = coefs[variable].get("coefficient", 0)
            if se is not None and se > 0:
                return se, False
            return abs(coef) * self.DEFAULT_SE_MULTIPLIER, True
        return 0.0, True

    def process(self, result):
        variable = result["variable"]
        eff_delta = result.get("effective_delta", 0)

        gas_se = result.get("delta_gas_se", 0)
        ercot_se = result.get("delta_ercot_se", 0)
        caiso_se = result.get("delta_caiso_se", 0)

        se_estimated_flags = []
        if gas_se == 0 and variable != "henry_hub_spot":
            hh_se, is_est = self._get_se_or_default("hh_full", variable)
            gas_se = abs(hh_se * eff_delta) if eff_delta != 0 else 0
            if is_est and variable in self.reg_results.get("hh_full", {}).get("coefficients", {}):
                se_estimated_flags.append(
                    f"HH model SE for {variable} estimated (0.5x|coef|={hh_se:.4f})")
        if ercot_se == 0:
            e_se, is_est = self._get_se_or_default("ercot_full", variable)
            ercot_se = abs(e_se * eff_delta) if eff_delta != 0 else 0
            if is_est and variable in self.reg_results.get("ercot_full", {}).get("coefficients", {}):
                se_estimated_flags.append(f"ERCOT model SE for {variable} estimated")
        if caiso_se == 0:
            c_se, is_est = self._get_se_or_default("caiso_full", variable)
            caiso_se = abs(c_se * eff_delta) if eff_delta != 0 else 0
            if is_est and variable in self.reg_results.get("caiso_full", {}).get("coefficients", {}):
                se_estimated_flags.append(f"CAISO model SE for {variable} estimated")

        ercot_pt_se = 41.33 * gas_se if gas_se > 0 else 0
        caiso_pt_se = 14.19 * gas_se if gas_se > 0 else 0

        ercot_total_se = math.sqrt(ercot_se**2 + ercot_pt_se**2 + self.ercot_resid_std**2)
        caiso_total_se = math.sqrt(caiso_se**2 + caiso_pt_se**2 + self.caiso_resid_std**2)
        gas_total_se = math.sqrt(gas_se**2 + self.hh_resid_std**2)

        resid_corr = result.get("cross_market_residual_correlation", 0.35)
        joint_se = math.sqrt(ercot_total_se**2 + caiso_total_se**2
                             + 2 * resid_corr * ercot_total_se * caiso_total_se)

        delta_gas = result.get("delta_gas", 0)
        delta_ercot = result.get("delta_ercot_total", 0)
        delta_caiso = result.get("delta_caiso_total", 0)

        result["confidence_intervals"] = {
            "gas_price": {
                "point_estimate": round(delta_gas, 4),
                "se": round(gas_total_se, 4),
                "ci_90": [round(delta_gas - 1.645 * gas_total_se, 2),
                          round(delta_gas + 1.645 * gas_total_se, 2)],
                "ci_95": [round(delta_gas - 1.96 * gas_total_se, 2),
                          round(delta_gas + 1.96 * gas_total_se, 2)],
            },
            "ercot_wholesale": {
                "point_estimate": round(delta_ercot, 2),
                "se": round(ercot_total_se, 2),
                "ci_90": [round(delta_ercot - 1.645 * ercot_total_se, 2),
                          round(delta_ercot + 1.645 * ercot_total_se, 2)],
                "ci_95": [round(delta_ercot - 1.96 * ercot_total_se, 2),
                          round(delta_ercot + 1.96 * ercot_total_se, 2)],
            },
            "caiso_wholesale": {
                "point_estimate": round(delta_caiso, 2),
                "se": round(caiso_total_se, 2),
                "ci_90": [round(delta_caiso - 1.645 * caiso_total_se, 2),
                          round(delta_caiso + 1.645 * caiso_total_se, 2)],
                "ci_95": [round(delta_caiso - 1.96 * caiso_total_se, 2),
                          round(delta_caiso + 1.96 * caiso_total_se, 2)],
            },
            "joint_system_se": round(joint_se, 2),
        }

        data_flags = list(se_estimated_flags)
        if result.get("feedback_applied"):
            for fb in result["feedback_applied"]:
                if fb["data_quality"] == "low":
                    data_flags.append(
                        f"Feedback '{fb['loop']}' uses estimated strength (data_quality=low)")
        for model_name in ["hh_full", "ercot_full", "caiso_full"]:
            coefs = self.reg_results.get(model_name, {}).get("coefficients", {})
            if variable in coefs and not coefs[variable].get("significant", False):
                pv = coefs[variable].get("p_value", "N/A")
                data_flags.append(
                    f"{variable} NOT significant in {model_name} (p={pv})")

        result["data_quality_flags"] = data_flags
        result["total_system_impact"] = round(
            abs(delta_gas) + abs(delta_ercot) + abs(delta_caiso), 4)
        return result


# --- ShockPropagator ---

class ShockPropagator:
    def __init__(self, catalog, reg_results, mc_params, master):
        self.catalog = catalog
        self.reg_results = reg_results
        cross_market_params = _calibrate_cross_market(reg_results, master)
        print(f"  Cross-market residual correlation: {cross_market_params['residual_correlation']:.4f} "
              f"(n={cross_market_params['n_common_observations']})")
        self.cross_market_params = cross_market_params
        self.stages = [
            SaturationFilter(catalog),
            LagFilter(VARIABLE_LAG_MAP, LAG_PROFILES),
            SeasonalFilter(SEASONAL_AMPLIFICATION),
            RegimeDetector(REGIMES),
            GasTransmission(reg_results.get("hh_full", {})),
            CrossMarketTransmission(reg_results, cross_market_params),
            ElectricityTransmission(reg_results.get("ercot_full", {}),
                                    reg_results.get("caiso_full", {})),
            FeedbackAdjuster(FEEDBACK_RULES),
            UncertaintyQuantifier(reg_results, mc_params, master),
        ]

    def propagate(self, variable, delta, system_state, time_horizon="12m"):
        result = {
            "variable": variable, "raw_delta": delta,
            "system_state": system_state, "time_horizon": time_horizon,
        }
        for stage in self.stages:
            result = stage.process(result)
        return result

    def propagate_linear(self, variable, delta):
        hh_coefs = self.reg_results.get("hh_full", {}).get("coefficients", {})
        ercot_coefs = self.reg_results.get("ercot_full", {}).get("coefficients", {})
        caiso_coefs = self.reg_results.get("caiso_full", {}).get("coefficients", {})

        if variable == "henry_hub_spot":
            dg = delta
        else:
            hh_c = hh_coefs.get(variable, {}).get("coefficient", 0)
            dg = hh_c * delta

        ercot_gas_pt = 41.33
        caiso_gas_pt = 14.19
        ercot_indirect = dg * ercot_gas_pt
        caiso_indirect = dg * caiso_gas_pt

        if variable == "henry_hub_spot":
            ercot_direct = 0
            caiso_direct = 0
        else:
            ercot_direct = ercot_coefs.get(variable, {}).get("coefficient", 0) * delta
            caiso_direct = caiso_coefs.get(variable, {}).get("coefficient", 0) * delta

        return {
            "variable": variable, "delta": delta,
            "delta_gas": round(dg, 4),
            "delta_ercot": round(ercot_indirect + ercot_direct, 2),
            "delta_caiso": round(caiso_indirect + caiso_direct, 2),
        }


# --- Catalog, Matrix, Scenario builders ---

def _build_variable_catalog(reg_results, master):
    catalog = {}
    category_map = {
        "henry_hub_spot": "commodity", "electric_power_bcfd": "demand",
        "us_gdp_growth_pct": "macro", "us_industrial_prod_index": "macro",
        "us_data_center_twh": "demand", "tx_data_center_twh": "demand",
        "ca_data_center_twh": "demand",
        "is_summer": "seasonal", "is_winter": "seasonal",
        "queue_backlog_gw": "infrastructure", "ercot_large_load_queue_gw": "infrastructure",
        "ira_active": "policy", "itc_rate_pct": "policy", "ptc_rate_cents_kwh": "policy",
        "ptc_active": "policy", "n_ira_credits": "policy",
        "ca_cap_trade_active": "policy", "ca_rps_target_pct": "policy",
        "n_rps_states": "policy", "policy_intensity_index": "policy",
        "cumulative_ferc_reforms": "policy",
        "tx_gas_gen_pct": "generation_mix", "tx_wind_gen_pct": "generation_mix",
        "tx_coal_gen_pct": "generation_mix",
        "ca_gas_gen_pct": "generation_mix", "ca_solar_gen_pct": "generation_mix",
        "ca_wind_gen_pct": "generation_mix",
        "tx_ercot_demand_twh": "demand", "ca_caiso_demand_twh": "demand",
        "tx_crez_active": "infrastructure", "ca_nem_compensation_level": "policy",
    }

    all_vars = set()
    for model_key in ["hh_full", "ercot_full", "caiso_full"]:
        coefs = reg_results.get(model_key, {}).get("coefficients", {})
        all_vars.update(coefs.keys())

    for var in sorted(all_vars):
        baseline_mean = 0.0
        baseline_std = 1.0
        if hasattr(master, "columns") and var in master.columns:
            col = master[var].dropna()
            if len(col) > 0:
                baseline_mean = float(col.mean())
                baseline_std = float(col.std()) if col.std() > 0 else 1.0

        models_present = []
        for mk in ["hh_full", "ercot_full", "caiso_full"]:
            if var in reg_results.get(mk, {}).get("coefficients", {}):
                models_present.append(mk)

        catalog[var] = {
            "baseline_mean": round(baseline_mean, 4),
            "baseline_std": round(baseline_std, 4),
            "sigma_1": round(baseline_std, 4),
            "category": category_map.get(var, "other"),
            "unit": VARIABLE_UNITS.get(var, ""),
            "models": models_present,
            "lag_class": VARIABLE_LAG_MAP.get(var, "fast"),
        }
    return catalog


def _compute_sensitivity_matrix(propagator, catalog):
    system_state = {
        "henry_hub_spot": 4.12, "tx_gas_gen_pct": 43.0,
        "queue_backlog_gw": 2600, "month": 6,
    }
    matrix = {}
    for var, info in catalog.items():
        sigma = info["sigma_1"]
        if sigma == 0:
            continue
        for direction, mult in [("plus_1sigma", 1.0), ("minus_1sigma", -1.0)]:
            delta = sigma * mult
            dynamic = propagator.propagate(var, delta, system_state, "12m")
            linear = propagator.propagate_linear(var, delta)
            key = f"{var}__{direction}"
            matrix[key] = {
                "variable": var, "direction": direction,
                "delta_input": round(delta, 4),
                "category": info["category"],
                "linear": {
                    "delta_gas": linear["delta_gas"],
                    "delta_ercot": linear["delta_ercot"],
                    "delta_caiso": linear["delta_caiso"],
                },
                "dynamic": {
                    "delta_gas": round(dynamic.get("delta_gas", 0), 4),
                    "delta_ercot": round(dynamic.get("delta_ercot_total", 0), 2),
                    "delta_caiso": round(dynamic.get("delta_caiso_total", 0), 2),
                    "regime_before": dynamic.get("regime_before"),
                    "regime_after": dynamic.get("regime_after"),
                    "lag_fraction": dynamic.get("lag_fraction"),
                    "seasonal_multiplier": dynamic.get("seasonal_multiplier"),
                    "feedback_applied": dynamic.get("feedback_applied", []),
                    "confidence_intervals": dynamic.get("confidence_intervals", {}),
                    "data_quality_flags": dynamic.get("data_quality_flags", []),
                },
            }
    return matrix


def _build_multi_shock_scenarios(propagator, catalog):
    scenarios = [
        {
            "name": "IRA Full Rollback",
            "description": "Complete repeal of IRA clean energy provisions",
            "shocks": {"ira_active": -1, "itc_rate_pct": -20, "ptc_rate_cents_kwh": -1.5},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 43,
                             "queue_backlog_gw": 2600, "month": 6},
            "horizon": "24m",
        },
        {
            "name": "Summer Heat Wave + Gas Spike",
            "description": "Extreme summer: gas +$3, high cooling demand",
            "shocks": {"henry_hub_spot": 3.0, "tx_ercot_demand_twh": 5.0,
                       "electric_power_bcfd": 4.0},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 50,
                             "queue_backlog_gw": 2600, "month": 7},
            "horizon": "3m",
        },
        {
            "name": "Winter Polar Vortex",
            "description": "Extreme winter: gas surge, heating demand spike",
            "shocks": {"henry_hub_spot": 5.0, "electric_power_bcfd": 6.0},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 40,
                             "queue_backlog_gw": 2600, "month": 1},
            "horizon": "1m",
        },
        {
            "name": "Data Center Boom",
            "description": "+50 TWh US data center load over 2 years",
            "shocks": {"us_data_center_twh": 50, "tx_data_center_twh": 15,
                       "ca_data_center_twh": 8},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 43,
                             "queue_backlog_gw": 2600, "month": 6},
            "horizon": "24m",
        },
        {
            "name": "Renewable Surge (TX Wind)",
            "description": "Texas wind generation share jumps +10pp",
            "shocks": {"tx_wind_gen_pct": 10, "tx_gas_gen_pct": -8},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 43,
                             "queue_backlog_gw": 2600, "month": 3},
            "horizon": "12m",
        },
        {
            "name": "Queue Crisis",
            "description": "Interconnection backlog doubles to 5000+ GW",
            "shocks": {"queue_backlog_gw": 2400, "ercot_large_load_queue_gw": 100},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 43,
                             "queue_backlog_gw": 2600, "month": 6},
            "horizon": "24m",
        },
        {
            "name": "CA Cap-and-Trade Repeal",
            "description": "California eliminates cap-and-trade program",
            "shocks": {"ca_cap_trade_active": -1},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 43,
                             "queue_backlog_gw": 2600, "month": 6},
            "horizon": "12m",
        },
        {
            "name": "GDP Recession",
            "description": "US GDP growth drops to -2% (recession)",
            "shocks": {"us_gdp_growth_pct": -4.5, "us_industrial_prod_index": -5},
            "system_state": {"henry_hub_spot": 4.12, "tx_gas_gen_pct": 43,
                             "queue_backlog_gw": 2600, "month": 6},
            "horizon": "12m",
        },
    ]

    results = []
    for sc in scenarios:
        totals = {"delta_gas": 0, "delta_ercot": 0, "delta_caiso": 0,
                  "delta_gas_linear": 0, "delta_ercot_linear": 0, "delta_caiso_linear": 0}
        details = []
        all_flags = []
        all_cis_gas = []
        all_cis_ercot = []
        all_cis_caiso = []
        for var, delta in sc["shocks"].items():
            dyn = propagator.propagate(var, delta, sc["system_state"], sc["horizon"])
            lin = propagator.propagate_linear(var, delta)
            totals["delta_gas"] += dyn.get("delta_gas", 0)
            totals["delta_ercot"] += dyn.get("delta_ercot_total", 0)
            totals["delta_caiso"] += dyn.get("delta_caiso_total", 0)
            totals["delta_gas_linear"] += lin["delta_gas"]
            totals["delta_ercot_linear"] += lin["delta_ercot"]
            totals["delta_caiso_linear"] += lin["delta_caiso"]
            ci = dyn.get("confidence_intervals", {})
            if ci.get("gas_price"):
                all_cis_gas.append(ci["gas_price"]["se"])
            if ci.get("ercot_wholesale"):
                all_cis_ercot.append(ci["ercot_wholesale"]["se"])
            if ci.get("caiso_wholesale"):
                all_cis_caiso.append(ci["caiso_wholesale"]["se"])
            all_flags.extend(dyn.get("data_quality_flags", []))
            details.append({
                "variable": var, "delta": delta,
                "dynamic_gas": round(dyn.get("delta_gas", 0), 4),
                "dynamic_ercot": round(dyn.get("delta_ercot_total", 0), 2),
                "dynamic_caiso": round(dyn.get("delta_caiso_total", 0), 2),
                "linear_gas": lin["delta_gas"],
                "linear_ercot": lin["delta_ercot"],
                "linear_caiso": lin["delta_caiso"],
            })

        combined_gas_se = math.sqrt(sum(s**2 for s in all_cis_gas)) if all_cis_gas else 0
        combined_ercot_se = math.sqrt(sum(s**2 for s in all_cis_ercot)) if all_cis_ercot else 0
        combined_caiso_se = math.sqrt(sum(s**2 for s in all_cis_caiso)) if all_cis_caiso else 0

        results.append({
            "name": sc["name"], "description": sc["description"],
            "horizon": sc["horizon"],
            "dynamic": {k: round(v, 4) for k, v in totals.items() if "linear" not in k},
            "linear": {k.replace("_linear", ""): round(v, 4)
                       for k, v in totals.items() if "linear" in k},
            "confidence_intervals_95": {
                "gas": [round(totals["delta_gas"] - 1.96 * combined_gas_se, 2),
                        round(totals["delta_gas"] + 1.96 * combined_gas_se, 2)],
                "ercot": [round(totals["delta_ercot"] - 1.96 * combined_ercot_se, 2),
                          round(totals["delta_ercot"] + 1.96 * combined_ercot_se, 2)],
                "caiso": [round(totals["delta_caiso"] - 1.96 * combined_caiso_se, 2),
                          round(totals["delta_caiso"] + 1.96 * combined_caiso_se, 2)],
            },
            "details": details,
            "data_quality_flags": list(set(all_flags)),
        })
    return results


# --- Step 12 orchestrator ---

def step12_sensitivity_simulator(reg_results, master, mc_results):
    banner("STEP 12: Advanced Sensitivity Simulator — Nonlinear Shock Propagation")

    print("  Building variable catalog...")
    catalog = _build_variable_catalog(reg_results, master)
    print(f"  Catalog: {len(catalog)} variables across {len(set(v['category'] for v in catalog.values()))} categories")

    print("  Initializing ShockPropagator (9-stage pipeline)...")
    propagator = ShockPropagator(catalog, reg_results, mc_results, master)

    print("  Computing sensitivity matrix (linear + dynamic, ±1σ per variable)...")
    matrix = _compute_sensitivity_matrix(propagator, catalog)
    print(f"  Matrix: {len(matrix)} entries ({len(catalog)} vars × 2 directions)")

    print("  Running 8 multi-shock scenarios...")
    scenarios = _build_multi_shock_scenarios(propagator, catalog)
    for sc in scenarios:
        dyn = sc["dynamic"]
        print(f"    {sc['name']:<30} Gas: {dyn['delta_gas']:+.2f} $/MMBtu  "
              f"ERCOT: {dyn['delta_ercot']:+.1f} $/MWh  "
              f"CAISO: {dyn['delta_caiso']:+.1f} $/MWh")

    output = {
        "metadata": {
            "name": "DecarbIQ Sensitivity Analysis",
            "version": "1.0",
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "pipeline_stages": [
                "SaturationFilter", "LagFilter", "SeasonalFilter",
                "RegimeDetector", "GasTransmission", "CrossMarketTransmission",
                "ElectricityTransmission", "FeedbackAdjuster", "UncertaintyQuantifier",
            ],
            "regimes": {k: v["label"] for k, v in REGIMES.items()},
            "cross_market": propagator.cross_market_params,
            "model_stats": {
                "hh_full": {"r2": reg_results.get("hh_full", {}).get("r_squared"),
                            "n": reg_results.get("hh_full", {}).get("n")},
                "ercot_full": {"r2": reg_results.get("ercot_full", {}).get("r_squared"),
                               "n": reg_results.get("ercot_full", {}).get("n")},
                "caiso_full": {"r2": reg_results.get("caiso_full", {}).get("r_squared"),
                               "n": reg_results.get("caiso_full", {}).get("n")},
            },
        },
        "variable_catalog": catalog,
        "sensitivity_matrix": matrix,
        "multi_shock_scenarios": scenarios,
        "feedback_rules": [
            {"name": r["name"], "strength": r["strength"],
             "calibration_note": r["calibration_note"],
             "data_quality": r["data_quality"],
             "time_constant_months": r["time_constant_months"]}
            for r in FEEDBACK_RULES
        ],
    }

    with open(SENSITIVITY_JSON, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Saved: {SENSITIVITY_JSON}")

    print("  Rendering 6-panel sensitivity visualization...")
    _render_sensitivity_charts(catalog, matrix, scenarios, propagator)

    return output


# --- 6-panel visualization ---

def _render_sensitivity_charts(catalog, matrix, scenarios, propagator):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    from matplotlib.lines import Line2D

    BG = "#0f0f1e"
    AX_BG = "#1a1a2e"
    fig, axes = plt.subplots(3, 2, figsize=(22, 26), facecolor=BG)
    fig.suptitle("DecarbIQ Sensitivity Analysis — Nonlinear Shock Propagation",
                 color="white", fontsize=18, fontweight="bold", y=0.98)

    for ax_row in axes:
        for ax in ax_row:
            ax.set_facecolor(AX_BG)
            ax.tick_params(colors="#aaa")
            for spine in ax.spines.values():
                spine.set_color("#333")

    # --- Panel 0,0: Tornado chart ---
    ax0 = axes[0, 0]
    tornado_data = []
    for var in catalog:
        pk = f"{var}__plus_1sigma"
        mk = f"{var}__minus_1sigma"
        if pk in matrix and mk in matrix:
            p_ercot = matrix[pk]["dynamic"]["delta_ercot"]
            m_ercot = matrix[mk]["dynamic"]["delta_ercot"]
            p_lin = matrix[pk]["linear"]["delta_ercot"]
            m_lin = matrix[mk]["linear"]["delta_ercot"]
            span = abs(p_ercot - m_ercot)
            ci = matrix[pk]["dynamic"].get("confidence_intervals", {})
            ercot_ci = ci.get("ercot_wholesale", {})
            se = ercot_ci.get("se", 0)
            tornado_data.append((var, p_ercot, m_ercot, p_lin, m_lin, span, se))

    tornado_data.sort(key=lambda x: x[5], reverse=True)
    top_n = min(15, len(tornado_data))
    tornado_data = tornado_data[:top_n]

    y_pos = list(range(top_n))
    for i, (var, p_dyn, m_dyn, p_lin, m_lin, _, se) in enumerate(reversed(tornado_data)):
        ax0.barh(i, p_lin, height=0.4, color="#4a90d9", alpha=0.3, left=0)
        ax0.barh(i, m_lin, height=0.4, color="#d94a4a", alpha=0.3, left=0)
        ax0.barh(i, p_dyn, height=0.4, color="#4a90d9", alpha=0.8, left=0)
        ax0.barh(i, m_dyn, height=0.4, color="#d94a4a", alpha=0.8, left=0)
        if se > 0:
            ax0.errorbar(p_dyn, i, xerr=1.96 * se, fmt='none', color='white',
                        capsize=2, linewidth=0.8, alpha=0.6)
            ax0.errorbar(m_dyn, i, xerr=1.96 * se, fmt='none', color='white',
                        capsize=2, linewidth=0.8, alpha=0.6)

    labels = [t[0].replace("_", " ")[:25] for t in reversed(tornado_data)]
    ax0.set_yticks(y_pos)
    ax0.set_yticklabels(labels, fontsize=8, color="#ccc")
    ax0.set_xlabel("ERCOT Impact ($/MWh)", color="#aaa", fontsize=9)
    ax0.set_title("Tornado: ERCOT ±1σ Sensitivity", color="white", fontsize=12, fontweight="bold")
    ax0.axvline(0, color="#555", linewidth=0.5)
    ax0.legend([mpatches.Patch(color="#4a90d9", alpha=0.3), mpatches.Patch(color="#4a90d9", alpha=0.8)],
               ["Linear", "Dynamic"], loc="lower right", fontsize=8,
               facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc")

    # --- Panel 0,1: Regime map ---
    ax1 = axes[0, 1]
    for name, cfg in REGIMES.items():
        lo, hi = cfg["hh_range"]
        hi_plot = min(hi, 15)
        colors_map = {"normal": "#2d5a27", "elevated": "#8a6d2b", "crisis": "#8a2b2b"}
        ax1.axvspan(lo, hi_plot, alpha=0.25, color=colors_map.get(name, "#333"),
                    label=cfg["label"])
    ax1.plot(4.12, 2600, 'o', color='#00ff88', markersize=12, zorder=5, label="Current State")
    sc_colors = ["#ff6b6b", "#ffd93d", "#6bcb77", "#4d96ff", "#ff922b",
                 "#cc5de8", "#20c997", "#868e96"]
    for idx, sc in enumerate(scenarios):
        dg = sc["dynamic"]["delta_gas"]
        dq = 0
        for d in sc["details"]:
            if d["variable"] == "queue_backlog_gw":
                dq = d["delta"]
        end_gas = 4.12 + dg
        end_queue = 2600 + dq
        clr = sc_colors[idx % len(sc_colors)]
        ax1.annotate("", xy=(end_gas, end_queue), xytext=(4.12, 2600),
                     arrowprops=dict(arrowstyle="->", color=clr, lw=1.5))
        ax1.text(end_gas + 0.1, end_queue, sc["name"][:20], fontsize=7, color=clr)

    ax1.set_xlabel("HH Gas Price ($/MMBtu)", color="#aaa", fontsize=9)
    ax1.set_ylabel("Queue Backlog (GW)", color="#aaa", fontsize=9)
    ax1.set_title("Regime Phase Diagram", color="white", fontsize=12, fontweight="bold")
    ax1.set_xlim(0, 15)
    ax1.set_ylim(0, 5500)
    ax1.legend(fontsize=7, facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc", loc="upper left")

    # --- Panel 1,0: Heatmap ---
    ax2 = axes[1, 0]
    hm_vars = sorted(tornado_data[:15], key=lambda x: x[5], reverse=True)
    hm_labels = [t[0] for t in hm_vars]
    hm_data = np.zeros((len(hm_labels), 3))
    for i, (var, *_) in enumerate(hm_vars):
        pk = f"{var}__plus_1sigma"
        if pk in matrix:
            hm_data[i, 0] = matrix[pk]["dynamic"]["delta_gas"]
            hm_data[i, 1] = matrix[pk]["dynamic"]["delta_ercot"]
            hm_data[i, 2] = matrix[pk]["dynamic"]["delta_caiso"]

    vmax = max(abs(hm_data.max()), abs(hm_data.min()), 1)
    im = ax2.imshow(hm_data, cmap="RdBu_r", aspect="auto", vmin=-vmax, vmax=vmax)
    ax2.set_yticks(range(len(hm_labels)))
    ax2.set_yticklabels([v.replace("_", " ")[:25] for v in hm_labels], fontsize=8, color="#ccc")
    ax2.set_xticks([0, 1, 2])
    ax2.set_xticklabels(["Gas ($/MMBtu)", "ERCOT ($/MWh)", "CAISO ($/MWh)"],
                        fontsize=9, color="#ccc")
    for i in range(len(hm_labels)):
        for j in range(3):
            val = hm_data[i, j]
            var = hm_labels[i]
            sig_mark = ""
            pk = f"{var}__plus_1sigma"
            if pk in matrix:
                flags = matrix[pk]["dynamic"].get("data_quality_flags", [])
                if any("NOT significant" in f for f in flags):
                    sig_mark = " ns"
                else:
                    sig_mark = ""
            txt_color = "white" if abs(val) > vmax * 0.5 else "#ccc"
            fmt_val = f"${val:.1f}" if j > 0 else f"${val:.2f}"
            ax2.text(j, i, f"{fmt_val}{sig_mark}", ha="center", va="center",
                    fontsize=7, color=txt_color)
    ax2.set_title("Impact Heatmap (+1σ Shock)", color="white", fontsize=12, fontweight="bold")
    cbar = fig.colorbar(im, ax=ax2, fraction=0.046, pad=0.04)
    cbar.ax.yaxis.set_tick_params(color="#aaa")
    cbar.ax.tick_params(labelcolor="#aaa")

    # --- Panel 1,1: Lag profiles ---
    ax3 = axes[1, 1]
    horizons = ["1m", "3m", "6m", "12m", "24m"]
    h_x = [1, 3, 6, 12, 24]
    lag_colors = {"instant": "#00ff88", "fast": "#4a90d9", "medium": "#ffd93d",
                  "slow": "#ff6b6b", "structural": "#cc5de8"}
    for lag_name, profile in LAG_PROFILES.items():
        vals = [profile[h] * 100 for h in horizons]
        ax3.plot(h_x, vals, 'o-', color=lag_colors.get(lag_name, "#aaa"),
                label=lag_name, linewidth=2, markersize=5)
    ax3.set_xlabel("Time Horizon (months)", color="#aaa", fontsize=9)
    ax3.set_ylabel("% of Full Impact Realized", color="#aaa", fontsize=9)
    ax3.set_title("Temporal Lag Profiles", color="white", fontsize=12, fontweight="bold")
    ax3.set_ylim(-5, 105)
    ax3.set_xticks(h_x)
    ax3.legend(fontsize=8, facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc")
    ax3.grid(True, alpha=0.15, color="#555")

    # --- Panel 2,0: Feedback network ---
    ax4 = axes[2, 0]
    ax4.set_xlim(-1.5, 1.5)
    ax4.set_ylim(-1.5, 1.5)
    ax4.set_aspect("equal")
    fb_nodes = {
        "Gas Price": (0, 0.8),
        "Renewables": (-1.0, 0),
        "Data Centers": (1.0, 0),
        "Queue": (-0.5, -0.8),
        "Policy": (0.5, -0.8),
        "ERCOT": (0, -0.2),
    }
    for name, (x, y) in fb_nodes.items():
        circle = plt.Circle((x, y), 0.18, color="#1e3a5f", ec="#4a90d9", linewidth=1.5, zorder=3)
        ax4.add_patch(circle)
        ax4.text(x, y, name, ha="center", va="center", fontsize=7, color="white",
                fontweight="bold", zorder=4)

    fb_edges = [
        ("Gas Price", "Renewables", -0.13, "moderate"),
        ("Data Centers", "Gas Price", -0.07, "low"),
        ("Queue", "Gas Price", 0.09, "moderate"),
        ("Policy", "ERCOT", -0.10, "low"),
    ]
    for src, dst, strength, quality in fb_edges:
        sx, sy = fb_nodes[src]
        dx, dy = fb_nodes[dst]
        color = "#4a90d9" if strength < 0 else "#ff6b6b"
        width = abs(strength) * 15
        ax4.annotate("", xy=(dx, dy), xytext=(sx, sy),
                     arrowprops=dict(arrowstyle="->", color=color,
                                     lw=max(1, width), alpha=0.7))
        mx, my = (sx + dx) / 2, (sy + dy) / 2
        badge = "M" if quality == "moderate" else "L"
        ax4.text(mx + 0.08, my + 0.08, f"{strength:+.2f} [{badge}]",
                fontsize=7, color=color, fontweight="bold")

    ax4.set_title("Feedback Network", color="white", fontsize=12, fontweight="bold")
    ax4.axis("off")
    legend_elements = [
        Line2D([0], [0], color="#4a90d9", lw=2, label="Stabilizing (-)"),
        Line2D([0], [0], color="#ff6b6b", lw=2, label="Amplifying (+)"),
    ]
    ax4.legend(handles=legend_elements, fontsize=8, facecolor=AX_BG,
              edgecolor="#555", labelcolor="#ccc", loc="lower left")

    # --- Panel 2,1: Scenario bars ---
    ax5 = axes[2, 1]
    sc_names = [sc["name"][:18] for sc in scenarios]
    x = np.arange(len(scenarios))
    width = 0.13

    gas_dyn = [sc["dynamic"]["delta_gas"] for sc in scenarios]
    gas_lin = [sc["linear"]["delta_gas"] for sc in scenarios]
    ercot_dyn = [sc["dynamic"]["delta_ercot"] for sc in scenarios]
    ercot_lin = [sc["linear"]["delta_ercot"] for sc in scenarios]
    caiso_dyn = [sc["dynamic"]["delta_caiso"] for sc in scenarios]
    caiso_lin = [sc["linear"]["delta_caiso"] for sc in scenarios]

    ax5.bar(x - 2.5 * width, gas_lin, width, color="#2d5a27", alpha=0.4, label="Gas (linear)")
    ax5.bar(x - 1.5 * width, gas_dyn, width, color="#2d5a27", alpha=0.9, label="Gas (dynamic)")
    ax5.bar(x - 0.5 * width, ercot_lin, width, color="#4a90d9", alpha=0.4, label="ERCOT (linear)")
    ax5.bar(x + 0.5 * width, ercot_dyn, width, color="#4a90d9", alpha=0.9, label="ERCOT (dynamic)")
    ax5.bar(x + 1.5 * width, caiso_lin, width, color="#ff922b", alpha=0.4, label="CAISO (linear)")
    ax5.bar(x + 2.5 * width, caiso_dyn, width, color="#ff922b", alpha=0.9, label="CAISO (dynamic)")

    for idx, sc in enumerate(scenarios):
        ci = sc.get("confidence_intervals_95", {})
        for offset, key, dyn_val in [(-1.5 * width, "gas", gas_dyn[idx]),
                                      (0.5 * width, "ercot", ercot_dyn[idx]),
                                      (2.5 * width, "caiso", caiso_dyn[idx])]:
            bounds = ci.get(key, [0, 0])
            lo_err = dyn_val - bounds[0]
            hi_err = bounds[1] - dyn_val
            if lo_err > 0 or hi_err > 0:
                ax5.errorbar(idx + offset, dyn_val,
                            yerr=[[max(0, lo_err)], [max(0, hi_err)]],
                            fmt='none', color='white', capsize=2, linewidth=0.8, alpha=0.5)

    ax5.set_xticks(x)
    ax5.set_xticklabels(sc_names, fontsize=7, color="#ccc", rotation=30, ha="right")
    ax5.set_ylabel("Price Impact", color="#aaa", fontsize=9)
    ax5.set_title("Multi-Shock Scenarios (Linear vs Dynamic)",
                  color="white", fontsize=12, fontweight="bold")
    ax5.axhline(0, color="#555", linewidth=0.5)
    ax5.legend(fontsize=6, facecolor=AX_BG, edgecolor="#555", labelcolor="#ccc",
              ncol=3, loc="upper right")
    ax5.grid(True, axis="y", alpha=0.15, color="#555")

    plt.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(SENSITIVITY_PNG, dpi=200, bbox_inches="tight",
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"  Saved: {SENSITIVITY_PNG}")


def _merge_enhanced_into_reg(reg, reg_v2, diag):
    """Bridge enhanced models into Step 12's expected format.

    Step 12 reads reg["ercot_full"], reg["caiso_full"], reg["hh_full"].
    Replace these with the best variant from reg_v2 if available, while
    preserving the expected dict structure (coefficients, residuals, etc.).
    Also propagate Newey-West SEs where computed.
    """
    merged = dict(reg)
    mapping = {
        "ercot": "ercot_full",
        "caiso": "caiso_full",
        "hh": "hh_full",
    }
    for mkt, orig_key in mapping.items():
        best_key = diag.get(f"{mkt}_best_variant")
        if best_key and best_key in reg_v2 and isinstance(reg_v2[best_key], dict):
            best_model = reg_v2[best_key]
            if "coefficients" in best_model:
                merged[orig_key] = best_model
                # Propagate Newey-West SEs if available
                nw = diag.get(best_key, {}).get("newey_west_se")
                if nw:
                    merged[orig_key]["newey_west_se"] = nw
                print(f"  Step 12 bridge: {orig_key} ← {best_key} "
                      f"(Adj R²={best_model.get('adj_r_squared', 'N/A')})")
    # Preserve residuals keys
    for rk in ["ercot_residuals", "hh_residuals", "caiso_residuals"]:
        if rk not in merged and rk in reg:
            merged[rk] = reg[rk]
    return merged


# ============================================================================
# MAIN
# ============================================================================
def main():
    banner("DecarbIQ Policy & Regulation Update Pipeline")
    print(f"  Started: {datetime.now().isoformat()}")

    # Steps 1-2: Original pipeline
    master = step1_merge()
    reg = step2_regression(master)

    # Step 1B: Enhance master with new controls (Tier 1)
    master_v8 = step1b_enhance_master(master)

    # Tier 2: Seasonal decomposition
    price_cols = ["ercot_wholesale_mwh", "caiso_wholesale_mwh", "henry_hub_spot", "ercot_rtm_mean"]
    master_v8 = _seasonal_decompose_monthly(master_v8, price_cols)

    # Extract Uri event data before exclusion (for volatility/stress-test use)
    _extract_uri_event_data(master_v8)

    # Step 2B: Enhanced regression — 6 variants per market
    reg_v2, diag = step2b_enhanced_regression(master_v8)

    # Steps 3-12: Original pipeline (using original reg for backward compat)
    garch = step3_garch(reg, master=master_v8)
    evt = step4_events(master)
    lng = step5_lng(master)
    mc = step6_monte_carlo(garch, reg, evt, lng, master=master_v8, reg_v2=reg_v2)
    step7_validate(reg, garch, mc)
    step8_params(reg, garch)
    step9_relationship_graph(reg)
    step10_chord_diagram(reg)
    step11_projection_map(reg, mc, evt, master, reg_v2=reg_v2)

    # Step 12: Use enhanced models if available
    # Merge best variant results into reg for Step 12 consumption
    reg_for_12 = _merge_enhanced_into_reg(reg, reg_v2, diag)
    step12_sensitivity_simulator(reg_for_12, master_v8, mc)

    # Step 13: Model diagnostics report
    step13_model_diagnostics(reg_v2, diag, master_v8)

    # Step 14: Expanded validation (residual analysis, coefficient stability, OOS, gas passthrough)
    step14_expanded_validation(reg_v2, diag, master_v8)

    banner("PIPELINE COMPLETE")
    print(f"  Finished: {datetime.now().isoformat()}")
    print(f"\n  Output files:")
    for f in [MASTER_V7, MASTER_V8, REG_RESULTS, REG_DIAGNOSTICS, DATA_QUALITY,
              GARCH_V4, EVENT_FREQ_V4, MC_V8,
              MODEL_PARAMS_V4, VALIDATION, VALIDATION_EXPANDED, URI_EVENT_DATA,
              REL_GRAPH_JSON, CHORD_PNG, CHORD_SVG, PROJECTION_JSON, PROJECTION_PNG,
              SENSITIVITY_JSON, SENSITIVITY_PNG, DIAG_PNG]:
        print(f"    {os.path.basename(f)}")


if __name__ == "__main__":
    main()
