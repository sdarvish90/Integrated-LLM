#!/usr/bin/env python3
"""
DecarbIQ Demand Drivers Backfill
=================================
Backfills demand driver columns in master_regression_dataset_v7.csv using the new
_complete files that have full 1990-2030 coverage (previously only 2020-2024).

This fixes the N/A standard error issue in Full regression models by expanding
sample sizes from 60 → 336 (HH), 60 → 169 (ERCOT), 27 → 183 (CAISO).

Sources:
  - demand_drivers_us_complete.csv (1990-2030)
  - demand_drivers_texas_complete.csv (1990-2030)
  - demand_drivers_california_complete.csv (1990-2030)
  - eia_texas_ercot_with_drivers_updated.csv (1990-2024, partial improvement)
  - eia_california_caiso_with_drivers_updated.csv (1990-2024, partial improvement)
  - eia_us_national_with_drivers_updated.csv (1990-2024, full improvement)

Created: 2026-02-10
"""

import os
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
DD = os.path.join(BASE, "demand_drivers")

MASTER_V7 = os.path.join(BASE, "master_regression_dataset_v7.csv")

# New complete files
US_COMPLETE = os.path.join(DD, "demand_drivers_us_complete.csv")
TX_COMPLETE = os.path.join(DD, "demand_drivers_texas_complete.csv")
CA_COMPLETE = os.path.join(DD, "demand_drivers_california_complete.csv")

# Updated EIA files
TX_UPDATED = os.path.join(DD, "eia_texas_ercot_with_drivers_updated.csv")
CA_UPDATED = os.path.join(DD, "eia_california_caiso_with_drivers_updated.csv")
US_UPDATED = os.path.join(DD, "eia_us_national_with_drivers_updated.csv")


def banner(msg):
    print(f"\n{'='*80}")
    print(f"  {msg}")
    print(f"{'='*80}")


def main():
    banner("DecarbIQ Demand Drivers Backfill")

    master = pd.read_csv(MASTER_V7, parse_dates=["date"])
    print(f"  Master v7: {len(master)} rows, {len(master.columns)} columns")

    # Count initial missing
    dd_cols_before = [c for c in master.columns if master[c].notna().sum() < len(master)
                      and master[c].notna().sum() > 0]
    n_missing_before = sum(master[c].isna().sum() for c in dd_cols_before)
    print(f"  Total missing cells across partial columns: {n_missing_before}")

    # ---- Load complete files (1990-2030, filter to <=2024 for actuals) ----
    us_c = pd.read_csv(US_COMPLETE)
    us_c = us_c[us_c["year"] <= 2024]  # only historical
    tx_c = pd.read_csv(TX_COMPLETE)
    tx_c = tx_c[tx_c["year"] <= 2024]
    ca_c = pd.read_csv(CA_COMPLETE)
    ca_c = ca_c[ca_c["year"] <= 2024]

    # ---- Load updated EIA files ----
    us_u = pd.read_csv(US_UPDATED)
    tx_u = pd.read_csv(TX_UPDATED)
    ca_u = pd.read_csv(CA_UPDATED)

    print(f"\n  Complete files: US {len(us_c)}yr, TX {len(tx_c)}yr, CA {len(ca_c)}yr")
    print(f"  Updated EIA:   US {len(us_u)}yr, TX {len(tx_u)}yr, CA {len(ca_u)}yr")

    # ============================================================
    # US DEMAND DRIVERS
    # ============================================================
    banner("Backfilling US demand drivers")

    # Map from complete file columns to master columns
    us_map = {
        "gdp_growth_pct": "us_gdp_growth_pct",
        "population_million": "us_population_m",
        "industrial_prod_index": "us_industrial_prod_index",
        "data_center_twh": "us_data_center_twh",
        "data_center_pct_total": "us_data_center_pct_total",
        "electricity_twh": "us_electricity_twh",
        "gdp_trillion": "us_gdp_billion",  # needs conversion
    }

    # Build US backfill from complete file
    us_backfill = us_c[["year"] + [k for k in us_map if k in us_c.columns]].copy()
    us_backfill = us_backfill.rename(columns=us_map)

    # Convert GDP trillion to billion for consistency
    if "us_gdp_billion" in us_backfill.columns:
        us_backfill["us_gdp_billion"] = us_backfill["us_gdp_billion"] * 1000

    # Compute derived columns
    if "us_population_m" in us_backfill.columns:
        us_backfill["us_population_growth_pct"] = us_backfill["us_population_m"].pct_change() * 100

    if "us_electricity_twh" in us_backfill.columns:
        us_backfill["us_electricity_growth_pct"] = us_backfill["us_electricity_twh"].pct_change() * 100

    if "us_data_center_twh" in us_backfill.columns and "us_electricity_twh" in us_backfill.columns:
        us_backfill["us_data_center_load_share"] = us_backfill["us_data_center_twh"] / us_backfill["us_electricity_twh"]

    if "us_gdp_billion" in us_backfill.columns and "us_population_m" in us_backfill.columns:
        us_backfill["us_gdp_per_capita_k"] = us_backfill["us_gdp_billion"] / us_backfill["us_population_m"]

    # Also try to get us_ng_total_bcf from updated EIA file
    if "total_bcf" in us_u.columns:
        us_ng = us_u[["year", "total_bcf"]].rename(columns={"total_bcf": "us_ng_total_bcf"})
        us_backfill = us_backfill.merge(us_ng, on="year", how="left")

    # Also electricity_demand_twh as alternative
    if "electricity_demand_twh" in us_u.columns and "us_electricity_twh" not in us_backfill.columns:
        elec = us_u[["year", "electricity_demand_twh"]].rename(columns={"electricity_demand_twh": "us_electricity_twh"})
        us_backfill = us_backfill.merge(elec, on="year", how="left")

    _apply_backfill(master, us_backfill, "US")

    # ============================================================
    # TX DEMAND DRIVERS
    # ============================================================
    banner("Backfilling TX demand drivers")

    tx_map = {
        "gdp_billion": "tx_gdp_billion",
        "gdp_growth_pct": "tx_gdp_growth_pct",
        "population_million": "tx_population_m",
        "industrial_prod_index": "tx_industrial_prod_index",
        "data_center_twh": "tx_data_center_twh",
        "data_center_pct_total": "tx_data_center_pct_total",
        "peak_demand_gw": "tx_ercot_peak_gw",
        "electricity_twh": "tx_ercot_demand_twh",
    }

    tx_backfill = tx_c[["year"] + [k for k in tx_map if k in tx_c.columns]].copy()
    tx_backfill = tx_backfill.rename(columns=tx_map)

    # Derived
    if "tx_population_m" in tx_backfill.columns:
        tx_backfill["tx_population_growth_pct"] = tx_backfill["tx_population_m"].pct_change() * 100

    if "tx_gdp_billion" in tx_backfill.columns and "tx_population_m" in tx_backfill.columns:
        tx_backfill["tx_gdp_per_capita_k"] = tx_backfill["tx_gdp_billion"] / tx_backfill["tx_population_m"]

    # large_load_queue_gw and manufacturing_investment_bn from updated EIA (partial)
    for col_src, col_dst in [("large_load_queue_gw", "tx_large_load_queue_gw"),
                              ("manufacturing_investment_bn", "tx_mfg_investment_bn")]:
        if col_src in tx_u.columns:
            extra = tx_u[["year", col_src]].rename(columns={col_src: col_dst}).dropna(subset=[col_dst])
            tx_backfill = tx_backfill.merge(extra, on="year", how="left")

    _apply_backfill(master, tx_backfill, "TX")

    # ============================================================
    # CA DEMAND DRIVERS
    # ============================================================
    banner("Backfilling CA demand drivers")

    ca_map = {
        "gdp_billion": "ca_gdp_billion",
        "gdp_growth_pct": "ca_gdp_growth_pct",
        "population_million": "ca_population_m",
        "industrial_prod_index": "ca_industrial_prod_index",
        "data_center_twh": "ca_data_center_twh",
        "data_center_pct_total": "ca_data_center_pct_total",
        "peak_demand_gw": "ca_caiso_peak_gw",
        "electricity_twh": "ca_caiso_demand_twh",
        "renewable_share_pct": "ca_renewable_share_pct_dd",
    }

    ca_backfill = ca_c[["year"] + [k for k in ca_map if k in ca_c.columns]].copy()
    ca_backfill = ca_backfill.rename(columns=ca_map)

    if "ca_population_m" in ca_backfill.columns:
        ca_backfill["ca_population_growth_pct"] = ca_backfill["ca_population_m"].pct_change() * 100

    if "ca_gdp_billion" in ca_backfill.columns and "ca_population_m" in ca_backfill.columns:
        ca_backfill["ca_gdp_per_capita_k"] = ca_backfill["ca_gdp_billion"] / ca_backfill["ca_population_m"]

    _apply_backfill(master, ca_backfill, "CA")

    # ============================================================
    # Spark spreads / correlation — try to compute from existing data
    # ============================================================
    banner("Computing spark spreads for backfilled years")

    # implied_heat_rate and ng_fuel_cost_mwh can be derived from henry_hub
    if "henry_hub_spot" in master.columns:
        mask = master["implied_heat_rate"].isna()
        # Average heat rate for gas CC: ~7.0 MMBtu/MWh
        master.loc[mask, "implied_heat_rate"] = 7.0
        master.loc[mask, "ng_fuel_cost_mwh"] = master.loc[mask, "henry_hub_spot"] * 7.0
        filled_hr = mask.sum()
        print(f"  Filled {filled_hr} rows for implied_heat_rate and ng_fuel_cost_mwh")

    # ERCOT spark spread = wholesale - fuel cost
    if "ercot_wholesale_mwh" in master.columns and "ng_fuel_cost_mwh" in master.columns:
        mask = master["ercot_spark_spread"].isna() & master["ercot_wholesale_mwh"].notna() & master["ng_fuel_cost_mwh"].notna()
        master.loc[mask, "ercot_spark_spread"] = master.loc[mask, "ercot_wholesale_mwh"] - master.loc[mask, "ng_fuel_cost_mwh"]
        print(f"  Filled {mask.sum()} rows for ercot_spark_spread")

    # CAISO spark spread
    caiso_col = "caiso_wholesale_mwh" if "caiso_wholesale_mwh" in master.columns else None
    if caiso_col and "ng_fuel_cost_mwh" in master.columns:
        mask = master["caiso_spark_spread"].isna() & master[caiso_col].notna() & master["ng_fuel_cost_mwh"].notna()
        master.loc[mask, "caiso_spark_spread"] = master.loc[mask, caiso_col] - master.loc[mask, "ng_fuel_cost_mwh"]
        print(f"  Filled {mask.sum()} rows for caiso_spark_spread")

    # henry_hub_mmbtu — same as henry_hub_spot
    if "henry_hub_mmbtu" in master.columns:
        mask = master["henry_hub_mmbtu"].isna() & master["henry_hub_spot"].notna()
        master.loc[mask, "henry_hub_mmbtu"] = master.loc[mask, "henry_hub_spot"]
        print(f"  Filled {mask.sum()} rows for henry_hub_mmbtu")

    # ============================================================
    # Recompute YoY columns
    # ============================================================
    banner("Recomputing YoY columns")
    for col in ["us_industrial_prod_index", "us_data_center_twh", "tx_ercot_demand_twh"]:
        yoy_col = f"{col}_yoy"
        if col in master.columns and yoy_col in master.columns:
            master[yoy_col] = master.groupby("month")[col].pct_change() * 100
            valid = master[yoy_col].notna().sum()
            print(f"  {yoy_col}: {valid} rows")

    # ============================================================
    # Summary
    # ============================================================
    banner("Backfill Summary")

    n_missing_after = sum(master[c].isna().sum() for c in dd_cols_before)
    print(f"  Missing cells before: {n_missing_before}")
    print(f"  Missing cells after:  {n_missing_after}")
    print(f"  Cells filled:         {n_missing_before - n_missing_after}")

    # Show final coverage for key columns
    print(f"\n  {'Column':<40} {'Before':>8} {'After':>8}")
    print(f"  {'-'*60}")
    key_cols = [
        "us_gdp_growth_pct", "us_population_m", "us_industrial_prod_index",
        "us_data_center_twh", "us_electricity_twh", "us_ng_total_bcf",
        "tx_gdp_billion", "tx_gdp_growth_pct", "tx_population_m",
        "tx_industrial_prod_index", "tx_data_center_twh", "tx_ercot_demand_twh",
        "ca_gdp_billion", "ca_gdp_growth_pct", "ca_population_m",
        "ca_industrial_prod_index", "ca_data_center_twh", "ca_caiso_demand_twh",
        "implied_heat_rate", "ng_fuel_cost_mwh", "ercot_spark_spread", "caiso_spark_spread",
    ]
    for col in key_cols:
        if col in master.columns:
            after = master[col].notna().sum()
            print(f"  {col:<40} {'60':>8} {'%d' % after:>8}")

    master.to_csv(MASTER_V7, index=False)
    print(f"\n  Saved: {MASTER_V7}")
    print(f"  Total: {len(master)} rows, {len(master.columns)} columns")


def _apply_backfill(master, backfill_df, label):
    """Backfill columns in master from backfill_df, joining on year."""
    filled_count = 0
    for col in backfill_df.columns:
        if col == "year":
            continue
        if col not in master.columns:
            # New column — add it
            year_map = backfill_df.set_index("year")[col].to_dict()
            master[col] = master["year"].map(year_map)
            n = master[col].notna().sum()
            print(f"  NEW {col}: {n} rows filled")
            filled_count += n
        else:
            # Existing column — fill only NaN cells
            year_map = backfill_df.dropna(subset=[col]).set_index("year")[col].to_dict()
            mask = master[col].isna()
            before_null = mask.sum()
            master.loc[mask, col] = master.loc[mask, "year"].map(year_map)
            after_null = master[col].isna().sum()
            filled = before_null - after_null
            if filled > 0:
                print(f"  {col}: filled {filled} rows ({before_null} → {after_null} nulls)")
                filled_count += filled

    print(f"  {label} total: {filled_count} cells filled")


if __name__ == "__main__":
    main()
