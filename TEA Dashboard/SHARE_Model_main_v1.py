# By Shadi Darvish, PhD

import pandas as pd
import json
from datetime import datetime
import inspect

from SHARE_model_v1 import simulator
from inputs import PV_capacity_ref_AC, wind_capacity_ref, wind_interpolation
from cases import get_cases

"""
This is the main script where the simulations are run. It reads the casses sheet from the input_sheet and uses the simualtor function from SHARE_model_v1 to simulate all the cases
"""


cases_sheet = get_cases()   # this already returns the DataFrame you built in cases.py

print(cases_sheet.columns)


result_rows = []
cases_rows = []
for index, case in cases_sheet.iterrows():
    print("running case " + str(index + 1))

    PV_capacity = case.PV_capacity_MW
    wind_capacity = case.Wind_MW
    PV_ppa = case.Solar_PPA
    wind_ppa = case.Wind_PPA
    # wake_losses = case.Wake_loss_factor
    BESS_power = case.BESS_Power_MW
    BESS_energy = case.BESS_energy_MWh
    H2_storage_capacity_tonnes = case.H2_Storage_capacity_tonne
    PV_scaling_factor = case.PV_scaling_factor
    wind_scaling_factor = case.wind_scaling_factor
    electrolyser_capacity = case.Electrolyzer_capacity_MW
    NH3_plant_capacity_ratio = case.Ratio_NH3_Plant_capacity
    HB_capacity = case.HB_capacity_MW
    HB_capacity_tonne = case.HB_capacity_tonne
    plant_capacity = case.Total_plant_capacity_MW
    grid_import_capacity = case.Grid_import_capacity_MW
    grid_export_capacity = case.Grid_export_capacity_MW
    grid_capex = case.Grid_CAPEX_kUSD * 1000
    grid_opex = case.Grid_OPEX_kUSD_per_year * 1000
    wind_capex = case.Wind_CAPEX_kUSD * 1000
    wind_opex = case.Wind_OPEX_kUSD_per_year * 1000
    PV_capex = case.PV_CAPEX_kUSD * 1000
    PV_opex = case.PV_OPEX_kUSD_per_year * 1000
    BESS_capex = case.BESS_CAPEX_kUSD * 1000
    BESS_opex = case.BESS_OPEX_kUSD_per_year * 1000
    electrolyser_capex = case.Electrolyser_CAPEX_kUSD * 1000
    electrolyser_opex = case.Electrolyser_OPEX_kUSD_per_year * 1000
    H2_BOP_capex = case.H2_BOP_CAPEX_kUSD * 1000
    H2_BOP_opex = case.H2_BOP_OPEX_kUSD_per_year * 1000
    H2_storage_capex = case.H2_Storage_CAPEX_kUSD * 1000
    H2_storage_opex = case.H2_Storage_OPEX_kUSD_per_year * 1000
    HB_capex = case.HB_CAPEX_kUSD * 1000
    HB_opex = case.HB_OPEX_kUSD_per_year * 1000
    NH3_storage_capex = case.NH3_Storage_CAPEX_kUSD * 1000
    NH3_storage_opex = case.NH3_Storage_OPEX_kUSD * 1000
    NH3_Storage_fixed_opex = case.NH3_Storage_fixed_OPEX_kUSD * 1000
    process_plant_fixed_opex = case.Process_plant_fixed_OPEX_kUSD * 1000
    NH3_storage_capacity = case.NH3_storage_capacity_m3
    H2_storage_compressor_capex = case.H2_compressor_CAPEX_kUSD * 1000
    H2_storage_compressor_opex = case.H2_compressor_OPEX_kUSD * 1000

    result = simulator(
        PV_capacity,
        wind_capacity,
        PV_ppa,
        wind_ppa,
        BESS_power,
        BESS_energy,
        PV_scaling_factor,
        wind_scaling_factor,
        electrolyser_capacity,
        HB_capacity,
        HB_capacity_tonne,
        H2_storage_capacity_tonnes,
        NH3_plant_capacity_ratio,
        plant_capacity,
        grid_import_capacity,
        grid_export_capacity,
        grid_capex,
        grid_opex,
        wind_capex,
        wind_opex,
        PV_capex,
        PV_opex,
        BESS_capex,
        BESS_opex,
        electrolyser_capex,
        electrolyser_opex,
        H2_BOP_capex,
        H2_BOP_opex,
        H2_storage_capex,
        H2_storage_opex,
        HB_capex,
        HB_opex,
        NH3_storage_capex,
        NH3_storage_opex,
        NH3_Storage_fixed_opex,
        process_plant_fixed_opex,
        NH3_storage_capacity,
        H2_storage_compressor_capex,
        H2_storage_compressor_opex
    )
    result_rows.extend(result)

print(cases_sheet)

# -------------------------
# Read LCOH values from the Project_Summary CSV (where simulator saves them)
# -------------------------
import glob
import os

# Find the most recent Project_Summary CSV
summary_dir = "Summary_output"
summary_pattern = os.path.join(summary_dir, "*Project_Summary.csv")
summary_files = glob.glob(summary_pattern)

if summary_files:
    # Get the most recently modified file
    latest_summary = max(summary_files, key=os.path.getmtime)
    summary_df = pd.read_csv(latest_summary)
    rec = summary_df.iloc[0].to_dict()

    outputs = {
        "Total_LCOH_USD_per_tonne": float(rec.get("Total_LCOH [$/tonnes]")) if rec.get("Total_LCOH [$/tonnes]") is not None else None,
        "Total_LCOA_USD_per_tonne": float(rec.get("Total_LCOA [$/tonnes]")) if rec.get("Total_LCOA [$/tonnes]") is not None else None,
        "Total_LCOE_USD_per_MWh": float(rec.get("Total_LCOE [$/MWh]")) if rec.get("Total_LCOE [$/MWh]") is not None else None,

        "Total_LCOH_USD_per_kg": (float(rec.get("Total_LCOH [$/tonnes]")) / 1000.0) if rec.get("Total_LCOH [$/tonnes]") is not None else None,
        "Total_LCOA_USD_per_kg": (float(rec.get("Total_LCOA [$/tonnes]")) / 1000.0) if rec.get("Total_LCOA [$/tonnes]") is not None else None,

        "NH3_prod_TPA_tonnes_per_year": float(rec.get("NH3_prod_[TPA]")) if rec.get("NH3_prod_[TPA]") is not None else None,
        
        "H2_prod_TPA_tonnes_per_year": float(rec.get("H2_prod_[TPA]")) if rec.get("H2_prod_[TPA]") is not None else None,

        "IRR_percent": float(rec.get("IRR [%]")) if rec.get("IRR [%]") is not None else None,
        "NPV_USD_M": float(rec.get("NPV [$M]")) if rec.get("NPV [$M]") is not None else None,
        "Total_CAPEX_USD_M": float(rec.get("Total_CAPEX [$M]")) if rec.get("Total_CAPEX [$M]") is not None else None,

        "final_product": str(rec.get("final_product", "NH3")),
        "green_H2_selling_price_USD_per_t": float(rec.get("green_H2_selling_price [$/t]")) if rec.get("green_H2_selling_price [$/t]") is not None else None,
        "grey_H2_selling_price_USD_per_t": float(rec.get("grey_H2_selling_price [$/t]")) if rec.get("grey_H2_selling_price [$/t]") is not None else None,

        "run_timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "model": "SHARE",
        "entrypoint": "SHARE_Model_main_v1.py",
    }
else:
    # Fallback if no summary file found
    outputs = {
        "error": "No Project_Summary.csv found",
        "run_timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "model": "SHARE",
    }

with open("outputs.json", "w", encoding="utf-8") as f:
    json.dump(outputs, f, indent=2)

print("outputs.json written successfully")
