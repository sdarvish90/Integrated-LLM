# from unittest import result
import datetime
import time
from timeit import default_timer as timer
import os
import math

import pandas as pd
import numpy as np
import numpy_financial as npf

import inputs  # This way it's easier to see that the variable is an input parameter
#from inputs import cost_sheet
#from inputs import capex_deployments
from RES_preprocessing import wind_prod
from RES_preprocessing import PV_prod
# from RES_preprocessing import interpolate_wind_profiles
#from RES_preprocessing import repeat_columns
from SOCTarget import SOCTarget
from NH3Prod import NH3Prod
from BatteryModel import BatteryModel
from H2StorageModel import H2StorageModel
from WaterModel import compute_hourly_water, compute_water_energy, compute_water_cost
import warnings
import matplotlib.pyplot as plt


# Ignore some warnings 
warnings.filterwarnings('ignore', 'DataFrame is highly fragmented')
warnings.filterwarnings('ignore', 'invalid value encountered in scalar divide')
warnings.filterwarnings('ignore', 'SettingWithCopyWarning')

start = timer()

start_time = time.time()


"""
This part of the model reads from the inputs, RES preprocessing files and uses different modules(BESS, H2 storage..etc) to output the hourly, yearly and project results. The main function used is the simulator function, 
which is used to run all the cases using methods of the classes. 
"""


# -----------------------------
# Fixed OPEX helpers (Excel-free)
# -----------------------------
def _fixed_opex_frac_piecewise(year: int,
                              frac_start: float,
                              frac_mid: float,
                              frac_end: float,
                              warranty_years: int,
                              midlife_year: int,
                              lifetime: int) -> float:
    """Return fixed OPEX as a fraction of CAPEX for a given project year (1-indexed)."""
    if lifetime <= 0:
        raise ValueError("lifetime must be > 0")
    if year <= warranty_years:
        return frac_start
    if year <= midlife_year:
        # ramp start -> mid
        denom = max(1, (midlife_year - warranty_years))
        return frac_start + (frac_mid - frac_start) * ((year - warranty_years) / denom)
    # ramp mid -> end
    denom = max(1, (lifetime - midlife_year))
    return frac_mid + (frac_end - frac_mid) * ((year - midlife_year) / denom)


def _annual_fixed_opex_kusd(capex_kusd: float,
                           years: pd.Series,
                           frac_start: float,
                           frac_mid: float,
                           frac_end: float,
                           warranty_years: int,
                           midlife_year: int,
                           lifetime: int,
                           inflation: float,
                           real_opex_escalation: float = 0.0) -> pd.Series:
    """Nominal annual fixed OPEX (kUSD/yr) as CAPEX * fraction(year), escalated by inflation + real escalation."""
    # years is a pandas Series of ints (1..lifetime)
    esc = (1 + inflation + real_opex_escalation) ** (years-1) #-1 adjusted for escalation
    fracs = years.apply(lambda y: _fixed_opex_frac_piecewise(int(y),
                                                             frac_start, frac_mid, frac_end,
                                                             warranty_years, midlife_year, lifetime))
    return capex_kusd * fracs * esc


def simulator(
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
):
    print("################################# running simulation #################################")

    """ This function simulates the entire energy system and takes a variety of parameters as input, all of which are floats. 
    The parameters include the capacity of different components of the system (e.g. PV panels, wind farm, battery storage, electorlyser capacity), 
    scaling factors for PV and wind power, losses associated with wake effects, and the capacity of an electrolyzer, haber-bosh plant, 
    and hydrogen storage. The function also takes in parameters related to the cost of the system, 
    including capital and operational expenses for different components. 
    The function returns a DataFrame object 
    
    Parameters:
    PV_capacity (float): capacity of the PV system in MWac 
    wind_capacity (float): capacity of wind farm in MW
    PV_ppa (float): solar plant PPA tariff in USD/MWh
    wind_ppa (float): wind plant PPA tariff in USD/MWh
    BESS_power (float): battery energy storage system power in MW 
    BESS_energy (float): battery energy storage system energy in MWh
    PV_scaling_factor (float): scaling factor for PV system
    wind_scaling_factor (float): scaling factor for wind farm 
    electrolyser_capacity (float): electrolyser capacity in MW 
    HB_capacity (float): Haber Bosh plant in MW 
    H2_storage_capacity_tonnes (float): hydrogen storage capacity in tonnes
    NH3_plant_capacity_ratio (float): ammonia plant capacity ratio
    plant_capacity (float): plant capacity in MW 
    grid_import_capacity (float): grid import capacity in MW 
    grid_export_capacity (float): grid export capacity in MW 
    grid_capex (float): grid capital expenses 
    grid_opex (float): grid operational expenses
    wind_capex (float): wind farm capital expenses
    wind_opex (float): wind farm operational expenses
    PV_capex (float): PV system capital expenses
    PV_opex (float): PV system operational expenses
    BESS_capex (float): battery energy storage system capital expenses
    BESS_opex (float): battery energy storage system operational expenses
    electrolyser_capex (float): electrolyser capital expenses
    electrolyser_opex (float): electrolyser operational expenses
    H2_BOP_capex (float): hydrogen balance of plant capital expenses
    H2_BOP_opex (float): hydrogen balance of plant operational expenses
    H2_storage_capex (float): hydrogen storage capital expenses
    H2_storage_opex (float): hydrogen storage operational expenses
    HB_capex (float): Haber Bosh plant capital expenses 
    HB_opex (float) : Haber Bosh plant operational expenses

    Returns: 
    pandas.DataFrame: The resulting simulated DataFrame.

 """


    # Calculated parameters
    H2_storage_capacity = H2_storage_capacity_tonnes * inputs.init_kWh_to_kg_H2 *1000 #MWh
    elec_SOH = electrolyser_capacity
    electrolyser_capacity_avail = electrolyser_capacity * inputs.electrolyser_avail  # including electrolyser availibility

    # BESS parameters
    BESS_usable_energy = BESS_energy * inputs.BESS_avail * inputs.BESS_DoD
    BESS_power_avail = BESS_power * inputs.BESS_avail

    # Setting up the main dataframe
    data = pd.DataFrame()
    data["timestamp"] = pd.date_range(start=inputs.start_date, end=inputs.end_date, freq=inputs.freq, inclusive="left")
    data["timestamp"] = pd.to_datetime(data["timestamp"])
    data = data.set_index("timestamp")

    # create grid import and export capacities columns
    data["grid_import_capacity_[MWh]"] = grid_import_capacity
    data["grid_export_capacity"] = grid_export_capacity

    # print(data['P_solar_aux'])

    # Saving to excel
    file_output_name = str(int(HB_capacity + electrolyser_capacity)) + "MW__" + str(NH3_plant_capacity_ratio * 100) + "_%_HB_ " + str(PV_capacity) + "_PV__" + (str(wind_capacity) + "_Wind__") + str(BESS_energy) + "BESS_MWh_" + str(PV_ppa) + 'PV_PPA_'+ str(round(H2_storage_capacity_tonnes)) + "_Tonnes " + "Simulation "
    # writer = pd.ExcelWriter(file_output_name + '.xlsx' , engine="openpyxl")
    # wb = Workbook()

    # Initiate main list for the final result dataframe
    result_rows = []

    # Initiate first year
    project_year = 1

    # Read the wind opex and wind degardation sheets
    # Read multi-year wind profiles from CSV:
    # - first row = year labels
    # - next 8760 rows = hourly data
    # raw_wind = pd.read_csv("wind_8760.csv", header=None)

    # # Row 0: years for each column
    # year_labels = raw_wind.iloc[0, :].tolist()

    # # Rows 1..8760: hourly profiles
    # wind_data = raw_wind.iloc[1:, :].reset_index(drop=True)

    # Sanity check: exactly 8760 rows of data
    # if len(wind_data) != 8760:
    #     raise ValueError(
    #         f"Expected 8760 data rows in wind_8760.csv (excluding first year row), "
    #         f"got {len(wind_data)}. Check the CSV."
    #     )

    # # 8760 rows × N columns (N = number of years / profiles in CSV)
    # wind_factor_df = pd.DataFrame(wind_data.values, columns=year_labels)
    
#scale up and interpolation is already taking place in RESpreprocessing

    # if inputs.wind_interpolation == "linear scaling":
    #     wind_data = wind_prod
    # else:
    #     wind_data = interpolate_wind_profiles(wind_prod, wind_capacity)
    # # Apply repeat_columns to wind data
    # wind_data = repeat_columns(wind_data, max(inputs.project_lifetime, len(wind_data.columns)))

    # NEW: take a snapshot of all FIXED columns that should persist across years
    # (exclude the ones that must be rewritten each year)
    _yearly_cols = {
        "P_wind", "P_solar",
        "wind_PPA", "solar_PPA",
        "grid_import_tariff", "export_revenues",
    }

    _fixed_cols = [c for c in data.columns if c not in _yearly_cols]
    _fixed_vals = data.iloc[0][_fixed_cols].to_dict()  # scalars / constants

    # Simulate the liftime of the project
    for project_year in range(1, inputs.project_lifetime + 1):
        year_start = pd.to_datetime(inputs.start_date) + pd.DateOffset(years=project_year-1)
        data = pd.DataFrame(index=pd.date_range(start=year_start, periods=8760, freq="h"))
        # NEW: re-apply fixed columns so they never "disappear" after resetting data
        for c, v in _fixed_vals.items():
            data[c] = v

        # Add wind and solar to the dataframe
        data["P_wind"] = wind_prod[project_year].values * wind_scaling_factor
        data["P_solar"] = PV_prod[project_year].values * PV_scaling_factor

    # `   # (optional safety)
    #     data["P_wind"]  = pd.to_numeric(data["P_wind"], errors="raise")
    #     data["P_solar"] = pd.to_numeric(data["P_solar"], errors="raise")`

        
        # Initiate each column of the cost sheet to each year of the simulation 
        data["wind_PPA"] = wind_ppa
        data["solar_PPA"] = PV_ppa

        #TODO: define these in inputs.py (recommended) or keep as constants here
        grid_import_tariff_y = getattr(inputs, "grid_import_tariff_usd_per_mwh", 0.0) * (1 + inputs.inflation) ** (project_year - 1)
        export_revenue_y     = getattr(inputs, "export_revenue_usd_per_mwh", 0.0)     * (1 + inputs.inflation) ** (project_year - 1)

        data["grid_import_tariff"] = grid_import_tariff_y
        data["export_revenues"]    = export_revenue_y
      
        # Wind degradation and availibilty
        # wind_factor = 1
        # wind_opex_yearly = wind_solar_opex_df[wind_solar_opex_df['year'] == project_year]['Wind_OPEX_kUSD/kW/year'].values[0]
        # PV_opex_yearly = wind_solar_opex_df[wind_solar_opex_df['year'] == project_year]['Wind OPEX_kUSD/kW/year'].values[0]
        # Ensure numeric wind series (handles "171,483" strings too)
        # data["P_wind"] = (
        #     pd.to_numeric(
        #         data["P_wind"].astype(str).str.replace(",", "", regex=False).str.strip(),
        #         errors="raise",
        #     )
        # )


        # # Scale wind solar and wind and account for any subsequent losses
        # data["P_wind"] = data["P_wind"] * (1 - inputs.wind_ac_losses) * wind_scaling_factor * wind_factor
        # data["P_solar"] = data["P_solar"] * (1 - inputs.PV_ac_losses) * inputs.PV_ava_f * PV_scaling_factor * (1 - inputs.shading_losses)

        # Check for solar aux loads
        # data["P_solar_aux"] = data["P_solar"].apply(lambda x: x if x < 0 else 0)
        # data["P_solar_aux"] = data["P_solar_aux"] * -1

        # # Remove the aux values from the P_solar
        # data["P_solar"] = data["P_solar"].apply(lambda x: 0 if x < 0 else x)
        # ==========================================


        # DEBUG: save final hourly profiles from SHARE
        # ==========================================
        print("debug_save_profiles =", getattr(inputs, "debug_save_profiles", False))


        
        if getattr(inputs, "debug_save_profiles", False):
            out_dir = "DEBUG_SHARE_profiles"
            os.makedirs(out_dir, exist_ok=True)

            df_debug = pd.DataFrame({
                "P_wind_MW": data["P_wind"].values,
                "P_solar_MW": data["P_solar"].values,
            }, index=data.index)
            df_debug.index.name = "timestamp"
            df_debug.to_csv(f"{out_dir}/profiles_year_{project_year}.csv")


        # Check first year for degradation
        if project_year == 1:

            # BESS Initial SOC
            BESS_init_SOC = inputs.init_SOC
            H2_Storage_SOC = inputs.H2_init_SOC

            # Electrolyser
            elec_SOH = electrolyser_capacity
            kWh_to_kg_H2 = inputs.init_kWh_to_kg_H2

        else:

            # BESS
            BESS_usable_energy = BESS_usable_energy * (1 - (inputs.BESS_degr + inputs.BESS_cal_degr))

            # if BESS_usable_energy <= BESS_energy * inputs.BESS_min_cap and inputs.BESS_aug_years < inputs.project_lifetime - project_year:
            #     BESS_usable_energy = BESS_energy
            # else:
            #     BESS_usable_energy = BESS_usable_energy * (1 - (inputs.BESS_degr + inputs.BESS_cal_degr))

        # Electrolyser
        if elec_SOH <= electrolyser_capacity * inputs.elect_min_SOH and inputs.elect_aug_years < inputs.project_lifetime - project_year:
            elec_SOH = electrolyser_capacity
        else:
            elec_SOH = elec_SOH * (1 - inputs.elect_degradation)

        if elec_SOH == electrolyser_capacity:
            kWh_to_kg_H2 = inputs.init_kWh_to_kg_H2
        else:
            kWh_to_kg_H2 = kWh_to_kg_H2 * (1 + inputs.elect_degradation)

        # Calculate wind and solar excess, min loads after wind and solar
        _final_product = getattr(inputs, 'final_product', 'NH3')
        if _final_product == "H2":
            # H2-only mode: max load = full electrolyser capacity
            data["max_loads"] = electrolyser_capacity
        else:
            data["max_loads"] = HB_capacity + electrolyser_capacity * NH3_plant_capacity_ratio
            if data["max_loads"].max() == 0:
                data["max_loads"] = electrolyser_capacity  # fallback
        data["min_loads"] = data["max_loads"] * inputs.min_NH3
        NH3_window = inputs.NH3_window_user

        # # Compute NH3 ramp-up and ramp-down MWh and tonnes
        NH3_ramp_down_rate_MWh = inputs.NH3_ramp_down_rate * data["max_loads"].max()
        NH3_ramp_up_rate_MWh = inputs.NH3_ramp_up_rate * data["max_loads"].max()

        # # calculate RES Contraint and NH3 potential 3h and 2h
        data["RES"] = data["P_wind"] + data["P_solar"] #- data["P_solar_aux"]. 
        data["RES_constraint"] = np.fmin(data["max_loads"].max(), data["RES"])
        # Add grid capacity to NH3 potential
        data["RES_grid_constraint"] = np.fmin(data["max_loads"].max(), data["RES"] + (data["grid_import_capacity_[MWh]"] * (1 - inputs.init_H2_ratio)))
        data["NH3_potential_3h"] = data["RES_grid_constraint"].iloc[::-1].rolling(NH3_window).mean().iloc[::-1].fillna(0).shift(-1)
        data["NH3_potential_2h"] = data["RES_grid_constraint"].iloc[::-1].rolling(NH3_window - 1).mean().iloc[::-1].fillna(0).shift(-1)
        data["NH3_potential"] = np.fmin(data["NH3_potential_3h"], data["NH3_potential_2h"])

        print("dispatch_options raw:", repr(inputs.dispatch_options))
        print("dispatch_options type:", type(inputs.dispatch_options))

        
        if inputs.dispatch_options == "Proportional":
            # Compute min loads after RES
            data["RES"] = data["P_wind"] + data["P_solar"] #- data["P_solar_aux"]
            data["excess_after_min"] = np.maximum(0, (data["RES"] - data["min_loads"]))
            data["NH3_supplied_after_RES"] = np.fmax(0, np.fmin(data["min_loads"] * (1 - inputs.init_H2_ratio), data["RES"]))
            data["H2_supplied_after_RES"] = np.fmax(0, data["RES"] - data["NH3_supplied_after_RES"])
            data["min_loads_after_RES"] = np.maximum(0, data["min_loads"] - data["NH3_supplied_after_RES"] - data["H2_supplied_after_RES"])

            # Compute max loads after RES
            data["excess_after_max"] = np.maximum(0, (data["RES"] - data["max_loads"]))
            data["max_loads_after_RES"] = np.maximum(0, data["max_loads"] - data["RES"])
            data["min_loads_after_RES_pot_curtailment"] = data["min_loads_after_RES"] - data["excess_after_max"]

            # Compute the potential excess of RE and H2 needed for min NH3 production
            data["H2_min_required"] = np.fmax(0, data["min_loads"] * (inputs.init_H2_ratio) - data["H2_supplied_after_RES"])
            data["H2_min_pot_curtailment"] = data["H2_min_required"] - data["excess_after_max"]
            data["H2_min_pot_curtailment"] = data["H2_min_pot_curtailment"]
            data["min_loads_supplied_after_RES"] = np.fmax(0, np.fmin(data["RES"], data["min_loads"])) #removed from the first argument - data["P_solar_aux"]

            # Compute propostions for wind and solar
        if wind_capacity == 0:
            data["wind_prod_ratio"] = 0
        else:
            data["wind_prod_ratio"] = data["P_wind"] / (data["P_wind"] + data["P_solar"])
        if PV_capacity == 0:
                data["solar_prod_ratio"] = 0
        else:
            data["solar_prod_ratio"] = data["P_solar"] / (data["P_wind"] + data["P_solar"])

            # Compute excess electrolyser capacity and H2 shortfall
        excess_elecrolyser_capacity = max(0, electrolyser_capacity_avail + HB_capacity - data["max_loads"].max())
        data["H2_shortfall"] = np.maximum(0,(data["min_loads"] * inputs.init_H2_ratio - data["RES"])- np.minimum(data["excess_after_max"], excess_elecrolyser_capacity))

        print("######################################### " + "Year " + str(project_year) + " #########################################")
        print("excess_electrolyser =", excess_elecrolyser_capacity)

        ############################    If Dispatch is proportional   ############################

        # General Paratemers
        print("dispatch_options =", inputs.dispatch_options)

        if inputs.dispatch_options == "Proportional":

            # Compute H2_SOC target
            # Create an H2 SOCtarget instance
            H2_SOC_target = SOCTarget(data, inputs.forecast_window, inputs.ramp_rates_buffer, "H2_shortfall", "H2_SOC_target")

            # Compute the SOC target
            H2_SOC_target.compute_SOC_target()

            # Charging the H2 storage
            current_H2_storage = H2_storage_capacity * H2_Storage_SOC
            H2_storage_rows = []
            H2_charging_rows = []
            RES_excess_after_H2_rows = []
            min_H2_SOC = inputs.H2_SOC_min
            next_H2_storage = None
            H2_discharge_rows = []

            for row in data.itertuples():

                # Charging

                H2_charging = max(0, min(row.excess_after_min, electrolyser_capacity_avail, (H2_storage_capacity - current_H2_storage), (row.H2_SOC_target - current_H2_storage)))
                next_H2_storage = current_H2_storage + H2_charging
                current_H2_storage = next_H2_storage

                # H2 Storage Discharging
                H2_discharge = max(0, min(row.H2_min_required, current_H2_storage - (H2_storage_capacity * min_H2_SOC)))
                next_H2_storage = current_H2_storage - H2_discharge
                current_H2_storage = next_H2_storage
                RES_excess_after_H2 = max(0, (row.excess_after_min - H2_charging))

                H2_charging_rows.append(H2_charging)
                H2_storage_rows.append(next_H2_storage)
                RES_excess_after_H2_rows.append(RES_excess_after_H2)
                H2_discharge_rows.append(H2_discharge)

            data["H2_charging"] = H2_charging_rows
            data["H2_wind_charging"] = data["H2_charging"] * data["wind_prod_ratio"]
            data["H2_solar_charging"] = data["H2_charging"] * data["solar_prod_ratio"]

            data["H2_storage"] = H2_storage_rows
            data["H2_SOC_[%]"] = data.H2_storage / H2_storage_capacity
            data["RES_excess_after_H2"] = RES_excess_after_H2_rows
            data["H2_discharge"] = H2_discharge_rows
            data["min_loads_after_H2"] = np.fmax(0, data["min_loads_after_RES"] - data["H2_discharge"])

            # compute max_after_excess after H2 charging to be used in BESS Charging
            data["excess_after_H2_charging"] = np.fmax(0, data["RES"] - data["H2_charging"])
            data["H2_SOC_target_remaining"] = np.fmax(0, (data["H2_SOC_target"] - np.fmax(data["H2_storage"], data["H2_charging"])))
            if BESS_power == 0:
                data["BESS_shortfall"] = 0
            else:
                data["BESS_shortfall"] = np.fmax(data["min_loads"] * (1 - inputs.init_H2_ratio) - data["excess_after_H2_charging"], 0) + np.fmax(np.fmin(data["max_loads"] - data["RES"], 0), -BESS_power)

            data["BESS_shortfall"] = data["BESS_shortfall"] / ((1 - (inputs.BESS_ac_losses)) * (math.sqrt(inputs.BESS_RTE)))

            # Compute cumulative sum for BESS SOC target
            # Create a BESS SOCtarget instance
            BESS_SOC_target = SOCTarget(data, inputs.forecast_window, 0, "BESS_shortfall", "SOC_target")

            # Compute BESS SOC target
            BESS_SOC_target.compute_SOC_target()

            # Compute SOCs  of H2 and BESS and other parameters
            # Load parameters
            max_loads_rows = []
            min_loads_rows = []
            current_max_loads = data["max_loads"].max()
            current_min_loads = data["min_loads"].max()
            next_max_loads = None
            next_min_loads = None
            max_loads = data["max_loads"].max()
            min_loads = data["min_loads"].max()

            # H2 parameters:
            min_H2_SOC = inputs.H2_SOC_min
            H2_charging_rows = []
            H2_discharge_rows = []
            RES_excess_after_H2_rows = []
            H2_storage_rows = []
            current_H2_storage = H2_storage_capacity * H2_Storage_SOC
            next_H2_storage = None
            H2_final_charging_rows = []
            H2_SOC_remainder_rows = []
            H2_SOC_remainder = current_H2_storage - data["H2_SOC_target"].iloc[0]
            H2_SOC_target_remaining_rows = []

            # Initiate a H2 storage object
            H2_storage_obj = H2StorageModel(H2_storage_capacity, current_H2_storage)

            # BESS parameters:
            SOC_target_rows = []
            BESS_discharge_rows = []
            BESS_rows = []
            next_bat_E = None
            BESS_final_charging_rows = []
            BESS_SOC_remainder_rows = []
            P_bat_max = BESS_power_avail
            E_bat_max = BESS_usable_energy
            current_bat_E = BESS_init_SOC * E_bat_max
            RES_excess_rows = []
            BESS_charging_rows = []
            P_discharge_rows = []
            if BESS_power == 0:
                BESS_SOC_target = 0
            else:
                H2_SOC_target_remaining_init = max(0, data["H2_SOC_target"].iloc[0] - current_H2_storage)
                BESS_SOC_target = (data["SOC_target"].iloc[0] + H2_SOC_target_remaining_init) + (inputs.ramp_rates_buffer * data["max_loads"].iloc[0] * (1 - inputs.init_H2_ratio) * NH3_window) #np.fmax(0, data["H2_SOC_target"].iloc[0] - current_H2_storage) #is it H2_target_deficit?
            print ("BESS_Soc_target=", BESS_SOC_target)
            BESS_SOC_remainder = np.maximum(0, np.fmin (P_bat_max * NH3_window, current_bat_E - BESS_SOC_target)) #originally P_bat_max / NH3_window
            print ("BESS_Soc_remainder=", BESS_SOC_remainder)
            BESS_to_H2_rows = []
            BESS_to_HB_rows = []
            BESS_losses = 1 - ((1 - inputs.BESS_ac_losses) * math.sqrt(inputs.BESS_RTE))
            # Initiate BESS instance
            BESS = BatteryModel(E_bat_max, P_bat_max, BESS_init_SOC, current_bat_E, inputs.BESS_RTE, inputs.BESS_ac_losses, inputs.BESS_degr)

            # RES parameters:
            RES_to_HB_rows = []
            RES_to_H2_rows = []
            RES_excess_after_grid_export_rows = []
            RES_curtailment_rows = []
            RES_excess_after_H2_rows = []
            RES_available_rows = []
            RES_excess_rows = []
            RES_to_plant_rows = []

            # Grid parameters
            grid_import_rows = []
            grid_export_rows = []
            grid_to_H2_rows = []
            grid_to_HB_rows = []

            # NH3_target and production parameters

            init_NH3_target = data["max_loads"].max() * 0.5  # Assumed 50% of max loads.
            current_NH3_target = init_NH3_target
            next_NH3_target = None
            NH3_target_rows = []
            NH3_target_final_remaining_rows = []
            NH3_potential_rows = []
            NH3_target_remainder_after_grid_rows = []
            NH3_prod = init_NH3_target
            next_NH3_prod = None
            NH3_prod_rows = []
            print("HIT 548")

            
            new_NH3_potential_rows = []
            tolerance = 1e-6  # This is just to make sure rounding errors will not occur while checking for trip events

            # Initiate NH3 production object
            NH3prod = NH3Prod(NH3_ramp_up_rate_MWh, NH3_ramp_down_rate_MWh, max_loads, min_loads, current_NH3_target)

            # Parameters for HB availibility
            # Parameters for HB availability
            raw_no_nh3 = str(inputs.start_range_of_no_NH3_prod).strip()

            # Accept either 'MM/DD/YYYY' or 'MM/DD/YYYY HH:MM:SS'
            try:
                hours_no_NH3_prod = datetime.datetime.strptime(raw_no_nh3, "%m/%d/%Y %H:%M:%S")
            except ValueError:
                hours_no_NH3_prod = datetime.datetime.strptime(raw_no_nh3, "%m/%d/%Y")

            range_of_no_NH3_prod = round(inputs.hours_per_year * (1 - inputs.HB_avail_factor))
            range_of_no_NH3_prod = datetime.timedelta(hours=range_of_no_NH3_prod)

            # Other
            trip_event_rows = []

            # intial allocation
            RES_to_H2 = 0
            RES_to_HB = 0
            BESS_to_H2 = 0
            BESS_to_HB = 0

            for row in data.itertuples():
                # Calculate new_NH3_potential for THIS hour
                new_NH3_potential = row.NH3_potential + ((BESS_SOC_remainder + H2_SOC_remainder) / NH3_window) #moved from line 588, it was out of the loop
                # Update NH3Prod object with the new potential value for this hour
                NH3prod.update(NH3_prod, new_NH3_potential)

                # Get the current max loads and min loads for this hour
                current_max_loads = NH3prod.current_max_loads
                current_min_loads = NH3prod.current_min_loads

                # Include NH3 availability
                if row.Index >= hours_no_NH3_prod and row.Index < hours_no_NH3_prod + range_of_no_NH3_prod:
                    # Set the NH3 target value within a certain range to 0
                    current_NH3_target = 0
                else:
                    # Compute NH3 target
                    current_NH3_target = NH3prod.current_NH3_target

                # Compute current NH3 loading
                if _final_product == "H2":
                    denom = electrolyser_capacity
                else:
                    denom = HB_capacity + electrolyser_capacity * NH3_plant_capacity_ratio
                current_NH3_target_loading = current_NH3_target / denom if denom > 0 else 0

                # Compute HB consumption based on HB loading
                HB_consumption = NH3Prod.calculate_HB_energy_consumption(current_NH3_target_loading, inputs.HB_consumption_profile)
                # HB_consumption = inputs.kWh_to_kg_NH3

                # Compute H2 ratio based on HB_consumption
                H2_ratio = NH3Prod.calculate_H2_ratio(kWh_to_kg_H2, inputs.H2_t_to_NH3_t, HB_consumption)

                # print(HB_consumption, H2_ratio)

                # Split current_NH3_target to H2 and HB
                current_NH3_target_H2 = current_NH3_target * H2_ratio
                current_NH3_target_HB = current_NH3_target * (1 - H2_ratio)

                # H2 first Charging
                # Charging
                H2_charging = H2_storage_obj.charge(max(0, min(row.RES - current_min_loads, electrolyser_capacity_avail - (current_min_loads * H2_ratio), (row.H2_SOC_target - current_H2_storage))))

                # Update H2 storage SOC
                current_H2_storage = H2_storage_obj.current_storage

                # RES excess after H2 charging
                RES_excess_after_H2 = max(0, (row.RES - H2_charging))

                # Compute remaining target after H2 charging
                current_NH3_target_H2 = min(current_NH3_target_H2, electrolyser_capacity_avail - H2_charging)
                current_NH3_target_HB = current_NH3_target_H2 / (H2_ratio / (1 - H2_ratio))
                current_NH3_target = current_NH3_target_H2 + current_NH3_target_HB

                # BESS first Charging
                # Charging
                BESS_charging = BESS.charge(max(0, min(row.RES - current_min_loads - H2_charging, BESS_SOC_target - current_bat_E)))

                # Update BESS SOC
                current_bat_E = BESS.current_energy

                # RES excess after BESS charging
                RES_excess = max(0, RES_excess_after_H2 - BESS_charging)

                # Compute RES to plant:
                RES_available = max(0, row.RES - BESS_charging - H2_charging)
                RES_to_plant = min(current_NH3_target, RES_available)

                # H2 discharge
                if row.RES > current_min_loads:
                    H2_to_be_discharged = max(0, min((current_bat_E + RES_to_plant + grid_import_capacity), ((current_NH3_target - RES_to_plant) * H2_ratio), current_H2_storage - row.H2_SOC_target))
                    H2_discharge = H2_storage_obj.discharge(H2_to_be_discharged)
                else:
                    H2_to_be_discharged = max(0, min((current_bat_E + RES_to_plant + grid_import_capacity), ((current_NH3_target - RES_to_plant) * H2_ratio), current_H2_storage))
                    H2_discharge = H2_storage_obj.discharge(H2_to_be_discharged)

                # Update H2 storage SOC
                current_H2_storage = H2_storage_obj.current_storage

                # Compute RES excess
                RES_excess = max(0, RES_available - RES_to_plant)

                # Allocation of RES to H2 and HB.
                RES_to_H2 = max(0, min(RES_to_plant, (current_NH3_target * H2_ratio) - H2_discharge, electrolyser_capacity_avail - H2_charging))
                RES_to_HB = max(0, min(RES_to_plant - RES_to_H2, (RES_to_H2 + H2_discharge) / (H2_ratio / (1 - H2_ratio)), current_NH3_target * (1 - H2_ratio)))

                # BESS discharge
                if row.RES > current_min_loads:
                    energy_to_discharged = max(0, min(current_bat_E - BESS_SOC_target, (current_NH3_target - RES_to_plant - H2_discharge)))
                    BESS_discharge = BESS.discharge(energy_to_discharged)
                else:
                    # BESS_discharge = max(0, min(current_bat_E, (current_NH3_target - RES_to_plant - H2_discharge), P_bat_max))
                    BESS_discharge = BESS.discharge((current_NH3_target - RES_to_plant - H2_discharge))

                # Update BESS SOC
                current_bat_E = BESS.current_energy

                # Allocation of BESS to H2 and to HB
                BESS_to_H2 = max(0, min(BESS_discharge, (current_NH3_target * H2_ratio) - H2_discharge - RES_to_H2, electrolyser_capacity_avail - RES_to_H2 - H2_charging))
                BESS_to_HB = max(0, min(BESS_discharge - BESS_to_H2, (RES_to_H2 + H2_discharge + BESS_to_H2) / (H2_ratio / (1 - H2_ratio)), current_NH3_target * (1 - H2_ratio) - RES_to_HB))

                # grid_import
                if inputs.grid_import_usage == "HB":
                    if RES_to_H2 + H2_discharge + BESS_discharge == 0:
                        grid_import = 0
                    else:
                        grid_import = max(0, min(grid_import_capacity, (RES_to_H2 + H2_discharge + BESS_discharge) / (H2_ratio / (1 - H2_ratio)), current_NH3_target * (1 - H2_ratio) - RES_to_HB - BESS_to_HB))
                else:
                    grid_import = max(0, min(grid_import_capacity, max(0, current_NH3_target - RES_to_plant - H2_discharge - BESS_discharge)))

                # Compute NH3 target remainder after grid import
                NH3_target_remainder_after_grid = max(0, current_NH3_target - RES_to_plant - grid_import - H2_discharge - BESS_discharge)

                # # Allocation of RES to H2 and HB.
                # RES_to_H2 = max(0, min(RES_to_plant, (current_NH3_target - NH3_target_remainder_after_grid) * (H2_ratio) - H2_discharge, electrolyser_capacity - H2_charging))
                # RES_to_HB = max(0, min(RES_to_plant - RES_to_H2, (RES_to_H2 + H2_discharge) / (H2_ratio / (1 - H2_ratio)), (current_NH3_target - NH3_target_remainder_after_grid) * (1 - H2_ratio)))

                # # Allocation of BESS to H2 and to HB
                # BESS_to_H2 = max(0, min(BESS_discharge, (current_NH3_target - NH3_target_remainder_after_grid) * (H2_ratio) - H2_discharge - RES_to_H2, electrolyser_capacity - RES_to_H2 - H2_charging))
                # BESS_to_HB = max(0, min(BESS_discharge - BESS_to_H2, (RES_to_H2 + H2_discharge + BESS_to_H2) / (H2_ratio / (1- H2_ratio)), (current_NH3_target - NH3_target_remainder_after_grid) * (1 - H2_ratio) - RES_to_HB))

                # Allocation of grid import to H2 and HB
                if inputs.grid_import_usage == "HB":
                    grid_to_HB = grid_import
                    grid_to_H2 = 0
                else:
                    grid_to_H2 = max(0, min(grid_import, (current_NH3_target - NH3_target_remainder_after_grid) * (H2_ratio) - H2_discharge - RES_to_H2 - BESS_to_H2, electrolyser_capacity - RES_to_H2 - H2_charging - BESS_to_H2))
                    grid_to_HB = min(grid_import - grid_to_H2, (current_NH3_target - NH3_target_remainder_after_grid) * (1 - H2_ratio) - RES_to_HB - BESS_to_HB, (RES_to_H2 + H2_discharge + BESS_discharge + grid_to_H2) / (H2_ratio / (1 - H2_ratio)))



                # # Compute grid export
                # grid_export = min(grid_export_capacity, RES_excess)
                # RES_excess_after_grid_export = max(0, RES_excess - grid_export)

                # BESS and H2 storage final charging
                NH3_ratio = 1 - H2_ratio
                BESS_charg_ratio = (NH3_ratio / (1 - BESS_losses)) / ((NH3_ratio / (1 - BESS_losses)) + H2_ratio)
                H2_charg_ratio = H2_ratio / ((NH3_ratio / (1 - BESS_losses)) + H2_ratio)

                # # H2 final_charging
                H2_final_charging_1 = H2_storage_obj.charge(max(0, min(RES_excess * H2_charg_ratio, electrolyser_capacity_avail - RES_to_H2 - BESS_to_H2 - H2_charging - grid_to_H2)))

                # Update H2 storage SOC
                current_H2_storage = H2_storage_obj.current_storage

                # BESS final_charging
                BESS_final_charging_1 = BESS.charge(max(0, min(RES_excess * BESS_charg_ratio, P_bat_max / (1 - inputs.BESS_ac_losses) - BESS_charging)))

                # Update BESS SOC
                # next_bat_E = current_bat_E + BESS_final_charging_1 * BESS_losses
                current_bat_E = BESS.current_energy

                # Check if SOCs are full:
                # H2
                if current_H2_storage < H2_storage_capacity:
                    H2_final_charging_2 = H2_storage_obj.charge(max(0, min(RES_excess - H2_final_charging_1 - BESS_final_charging_1, electrolyser_capacity_avail - RES_to_H2 - BESS_to_H2 - H2_charging - grid_to_H2 - H2_final_charging_1)))
                else:
                    H2_final_charging_2 = 0

                # Update H2 storage SOC
                current_H2_storage = H2_storage_obj.current_storage

                # Final H2 storage charging
                H2_final_charging = H2_final_charging_1 + H2_final_charging_2

                # BESS
                if current_bat_E < E_bat_max:
                    energy_to_be_charged = max(0, min(RES_excess - H2_final_charging_1 - BESS_final_charging_1 - H2_final_charging_2, P_bat_max / (1 - inputs.BESS_ac_losses) - BESS_charging - BESS_final_charging_1))
                    BESS_final_charging_2 = BESS.charge(energy_to_be_charged)
                else:
                    BESS_final_charging_2 = 0

                # Update BESS SOC
                current_bat_E = BESS.current_energy

                # BESS final charging
                BESS_final_charging = BESS_final_charging_1 + BESS_final_charging_2

                # RES_excess_after final BESS and H2 charging
                RES_excess_after_final_charging = max(0, RES_excess - H2_final_charging - BESS_final_charging)
                grid_export = min(grid_export_capacity, RES_excess_after_final_charging)
                RES_excess_after_grid_export = max(0, RES_excess_after_final_charging - grid_export)
                # Compute H2 SOC target remaining.
                H2_SOC_remainder = current_H2_storage - row.H2_SOC_target

                # Compte H2 SOC target remainder and add it to BESS SOC target
                H2_SOC_target_remaining = max(0, row.H2_SOC_target - current_H2_storage)

                if BESS_power == 0:
                    BESS_SOC_target = 0
                else:
                    BESS_SOC_target = (row.SOC_target + H2_SOC_target_remaining) + (inputs.ramp_rates_buffer * row.max_loads * (1 - H2_ratio) * NH3_window) #BESS_SOC_target = (row.SOC_target + H2_SOC_target_remaining) + (inputs.ramp_rates_buffer * row.max_loads * (1 - H2_ratio))

                # Compute BESS SOC remainder
                BESS_SOC_remainder = max(0, min(P_bat_max * NH3_window, current_bat_E - BESS_SOC_target)) #min(P_bat_max * NH3_window, current_bat_E - BESS_SOC_target)

                # Add excess SOCs to NH3 Potential
                new_NH3_potential = max(row.min_loads, row.NH3_potential) + (((BESS_SOC_remainder + max(0, H2_SOC_remainder))) / NH3_window)

                # # Compute grid export
                # grid_export = min(grid_export_capacity, RES_excess_after_final_charging)
                # RES_excess_after_grid_export = max(0, RES_excess_after_final_charging - grid_export)

                # final curtailment
                RES_curtailment = RES_excess_after_grid_export

                # final NH3 remainder
                NH3_target_final_remaining = NH3_target_remainder_after_grid

                # NH3 Production
                next_NH3_prod = max(0, current_NH3_target - NH3_target_final_remaining)
                NH3_prod = next_NH3_prod

                # Compute when NH3_prod is below min_NH3 load:
                if row.Index >= hours_no_NH3_prod and row.Index < hours_no_NH3_prod + range_of_no_NH3_prod:
                    trip_event = 0
                else:
                    if NH3_prod / data["max_loads"].max() < (inputs.min_NH3 - tolerance):
                        trip_event = 1
                    else:
                        trip_event = 0

                # appending rows
                # Loads
                max_loads_rows.append(current_max_loads)
                min_loads_rows.append(current_min_loads)

                # H2
                H2_charging_rows.append(H2_charging)
                RES_excess_after_H2_rows.append(RES_excess_after_H2)
                H2_discharge_rows.append(H2_discharge)
                H2_final_charging_rows.append(H2_final_charging)
                H2_storage_rows.append(current_H2_storage)
                H2_SOC_remainder_rows.append(H2_SOC_remainder)
                H2_SOC_target_remaining_rows.append(H2_SOC_target_remaining)

                # BESS
                BESS_charging_rows.append(BESS_charging)
                BESS_discharge_rows.append(BESS_discharge)
                BESS_final_charging_rows.append(BESS_final_charging)
                BESS_rows.append(current_bat_E)
                BESS_SOC_remainder_rows.append(BESS_SOC_remainder)
                SOC_target_rows.append(BESS_SOC_target)
                BESS_to_H2_rows.append(BESS_to_H2)
                BESS_to_HB_rows.append(BESS_to_HB)

                # RES
                RES_to_HB_rows.append(RES_to_HB)
                RES_to_H2_rows.append(RES_to_H2)
                RES_excess_after_grid_export_rows.append(RES_excess_after_grid_export)
                RES_curtailment_rows.append(RES_curtailment)
                RES_excess_rows.append(RES_excess)
                RES_available_rows.append(RES_available)
                RES_to_plant_rows.append(RES_to_plant)

                # NH3
                NH3_target_rows.append(current_NH3_target)
                NH3_target_remainder_after_grid_rows.append(NH3_target_remainder_after_grid)
                NH3_target_final_remaining_rows.append(NH3_target_final_remaining)
                NH3_prod_rows.append(next_NH3_prod)
                new_NH3_potential_rows.append(new_NH3_potential)

                # Grid
                grid_import_rows.append(grid_import)
                grid_export_rows.append(grid_export)
                grid_to_HB_rows.append(grid_to_HB)
                grid_to_H2_rows.append(grid_to_H2)

                # Others
                trip_event_rows.append(trip_event)

            # Create columns for the dataframe

            # loads
            data["max_loads"] = max_loads_rows
            data["min_loads"] = min_loads_rows

            # NH3
            data["NH3_potential_inclu_SOCs_excess"] = new_NH3_potential_rows
            data["NH3_target"] = NH3_target_rows
            # print('Sum of NH3 target =', data['NH3_target'].sum())

            # H2
            data["H2_charging"] = H2_charging_rows
            data["H2_wind_charging"] = data["H2_charging"] * data["wind_prod_ratio"]
            data["H2_solar_charging"] = data["H2_charging"] * data["solar_prod_ratio"]
            data["RES_excess_after_H2"] = RES_excess_after_H2_rows
            data["RES_available"] = RES_available_rows
            data["grid_import"] = grid_import_rows
            data["grid_to_HB"] = grid_to_HB_rows
            data["grid_to_H2"] = grid_to_H2_rows
            data["H2_SOC_target_remaining"] = H2_SOC_target_remaining_rows
            data["NH3_target_after_grid"] = NH3_target_remainder_after_grid_rows

            # BESS
            data["RES_BESS_charging"] = BESS_charging_rows
            data["P_wind_BESS_charging"] = data["RES_BESS_charging"] * data["wind_prod_ratio"]
            data["P_solar_BESS_charging"] = data["RES_BESS_charging"] - data["P_wind_BESS_charging"]
            data["SOC_target"] = SOC_target_rows

            # Set last 24 hours of SOC target
            if project_year < inputs.project_lifetime - 1:
                data.loc[data.index[8736:8760], "SOC_target"] = float(data["max_loads"].max()) * float(inputs.end_of_year_SOC_target)
                data.loc[data.index[8736:8760], "H2_SOC_target"] = float(data["max_loads"].max()) * float(inputs.end_of_year_SOC_target)


            data["E_bat"] = BESS_rows
            data["SOC_[%]"] = data["E_bat"] / E_bat_max
            data["RES_excess"] = RES_excess_rows
            data["H2_SOC_excess"] = H2_SOC_remainder_rows
            data["BESS_SOC_excess"] = BESS_SOC_remainder_rows
            data["SOCs_excess"] = np.fmax(0, data["H2_SOC_excess"]) + data["BESS_SOC_excess"]
            data["H2_discharge"] = H2_discharge_rows
            data["BESS_discharge"] = BESS_discharge_rows
            data["RES_to_plant"] = RES_to_plant_rows
            data["RES_to_H2"] = RES_to_H2_rows
            data["RES_to_HB"] = RES_to_HB_rows
            data["BESS_to_H2"] = BESS_to_H2_rows
            data["BESS_to_HB"] = BESS_to_HB_rows
            data["grid_export"] = grid_export_rows
            data["wind_grid_export"] = data["grid_export"] * data["wind_prod_ratio"]
            data["solar_grid_export"] = data["grid_export"] - data["wind_grid_export"] #+ data["RES"].apply(lambda x: x if x < 0 else 0)
            data["RES_excess_after_grid_export"] = RES_excess_after_grid_export_rows
            data["H2_charging_after_grid_export"] = H2_final_charging_rows
            data["H2_wind_charging_after_grid_export"] = data["H2_charging_after_grid_export"] * data["wind_prod_ratio"]
            data["H2_solar_charging_after_grid_export"] = data["H2_charging_after_grid_export"] - data["H2_wind_charging_after_grid_export"]
            data["BESS_charging_after_grid_export"] = BESS_final_charging_rows
            data["BESS_wind_charging_after_grid_export"] = data["BESS_charging_after_grid_export"] * data["wind_prod_ratio"]
            data["BESS_solar_charging_after_grid_export"] = data["BESS_charging_after_grid_export"] - data["BESS_wind_charging_after_grid_export"]
            data["H2_storage"] = H2_storage_rows
            data["H2_SOC_[%]"] = data["H2_storage"] / H2_storage_capacity

            # NH3 remainder
            data["NH3_target_remainder"] = NH3_target_final_remaining_rows

            # RES
            data["RES_final_curtailment"] = RES_curtailment_rows
            data["final_solar_curtailment"] = data["RES_final_curtailment"] * data["solar_prod_ratio"]
            data["final_wind_curtailment"] = data["RES_final_curtailment"] - data["final_solar_curtailment"]

            # Compute direct solar and direct wind
            data["direct_solar"] = data["P_solar"] - data["final_solar_curtailment"] - data["P_solar_BESS_charging"] - data["BESS_solar_charging_after_grid_export"] - data["solar_grid_export"]
            data["direct_wind"] = data["P_wind"] - data["final_wind_curtailment"] - data["P_wind_BESS_charging"] - data["BESS_wind_charging_after_grid_export"] - data["wind_grid_export"]

            # Compute final NH3 and H2 Prod and NH3 CF and H2 CF
            data["final_H2_prod_[MWh]"] = data["RES_to_H2"] + data["BESS_to_H2"] + data["H2_charging"] + data["H2_charging_after_grid_export"] + data["grid_to_H2"]
            data["final_NH3_prod_[MWh]"] = NH3_prod_rows
            data["final_H2_prod_[tonnes]"] = data["final_H2_prod_[MWh]"] / kWh_to_kg_H2
            data["final_NH3_prod_[tonnes]"] = (data["final_H2_prod_[tonnes]"] + (data["H2_discharge"] - data["H2_charging"] - data["H2_charging_after_grid_export"]) / kWh_to_kg_H2) / inputs.H2_t_to_NH3_t
            data['grey_H2_prod_[tonnes]'] = data['grid_to_H2'] / kWh_to_kg_H2
            data['green_H2_prod_[tonnes]'] = (data['RES_to_H2'] + data['BESS_to_H2'] + data['H2_discharge']) / kWh_to_kg_H2
            data['green_NH3_prod_[tonnes]'] = data['green_H2_prod_[tonnes]'] / inputs.H2_t_to_NH3_t
            data['grey_NH3_prod_[tonnes]'] = data['grey_H2_prod_[tonnes]'] / inputs.H2_t_to_NH3_t
            data["max_energy_deficit"] = np.fmax(0, data["NH3_target"] - data["final_NH3_prod_[MWh]"])
            data["min_energy_deficit"] = np.fmax(0, (data["min_loads"] - data["final_NH3_prod_[MWh]"]))
            # data["NH3_cf_[%]"] = data["final_NH3_prod_[MWh]"] /HB_capacity
            # data["H2_cf_[%]"] = data["final_H2_prod_[MWh]"] / electrolyser_capacity
            # FIX: Multiply by 100 to convert fraction to percentage (for hourly timesteps)
            data["NH3_cf_[%]"] = (data["final_NH3_prod_[MWh]"] / HB_capacity) * 100
            data["H2_cf_[%]"] = (data["final_H2_prod_[MWh]"] / electrolyser_capacity) * 100
            data["trip_event"] = trip_event_rows
            # print(data['final_H2_prod_[tonnes]'].sum() / data['final_NH3_prod_[tonnes]'].sum())
            # print(data['final_H2_prod_[MWh]'].max())
            # Test ramp rates fpr final NH3 Prod
            data["test_ramp_rates"] = data["NH3_cf_[%]"].diff().fillna(0)
            print("ramp_down rate =", data["test_ramp_rates"].min(), "ramp up rate =", data["test_ramp_rates"].max())
            print("Min NH3 Prod =", data["NH3_cf_[%]"].min())
            print("Total NH3 prod =", data["final_NH3_prod_[MWh]"].sum())
            print("Total NH3 prod_tonnes =", data["final_NH3_prod_[tonnes]"].sum())
            print("NH3 CF =", data["NH3_cf_[%]"].mean())

            # Count Shortfall trips
            shortfall_events = NH3prod.shortfall_event(data.trip_event)
            data["shortfall_events"] = shortfall_events
            print("Number of Shortfall trips = ", shortfall_events)
            data["BESS_usable_energy"] = BESS_usable_energy
            data["HB_loads"] = data["RES_to_HB"] + data["grid_to_HB"] + data["BESS_to_HB"]
            data["HB_checks"] = data["HB_loads"] / data["final_NH3_prod_[tonnes]"]
            print('max_check =', data['HB_checks'].max())
            print('NH3 from HB =', data['HB_loads'].sum() / 0.70)

            # compute total H2 charging
            data["total_H2_charging"] = data["H2_charging_after_grid_export"] + data["H2_charging"]

            

            # Solar and Wind transmission losses
            data["solar_transmission_losses"] = (data["solar_grid_export"] + data["direct_solar"]) / (1 - inputs.transmission_losses) - (data["solar_grid_export"] + data["direct_solar"])
            data["wind_transmission_losses"] = (data["wind_grid_export"] + data["direct_wind"]) / (1 - inputs.transmission_losses) - (data["wind_grid_export"] + data["direct_wind"])

            # Deduct solar and wind transmission losses to wind and solar curtailment
            #data["final_solar_curtailment"] = data["P_solar"] / (1 - inputs.transmission_losses) - data["direct_solar"] - data["solar_grid_export"] - data["solar_transmission_losses"]
            data["final_solar_curtailment"] = data["P_solar"] - data["direct_solar"] - data["solar_grid_export"] - data["solar_transmission_losses"]
            data["final_wind_curtailment"] = data["P_wind"] - data["direct_wind"] - data["wind_grid_export"] - data["wind_transmission_losses"]

            #data["final_wind_curtailment"] = data["P_wind"] / (1 - inputs.transmission_losses) - data["direct_wind"] - data["wind_grid_export"] - data["wind_transmission_losses"]
            data["RES_final_curtailment"] = data["final_solar_curtailment"] + data["final_wind_curtailment"]
            
            # Compute cost columns 
            data['wind_cost'] = data['P_wind'] * data['wind_PPA']
            data['solar_cost'] = data['P_solar'] * data['solar_PPA']
            data['grid_import_cost'] = data['grid_import'] * data['grid_import_tariff']
            # Water consumption volumes, energy and cost (via WaterModel)
            water_volumes = compute_hourly_water(
                H2_prod_tonnes=data["final_H2_prod_[tonnes]"],
                NH3_prod_tonnes=data["final_NH3_prod_[tonnes]"],
                elec_feed_water=inputs.elec_feed_water,
                elec_cooling_water=inputs.elec_cooling_water,
                HB_cooling_water=inputs.HB_cooling_water,
                waste_water_flowrate=inputs.waste_water_flowrate,
            )
            for col_name, series in water_volumes.items():
                data[col_name] = series

            data["waste_water_energy_MWh"] = compute_water_energy(
                data["waste_water_m3"], inputs.waste_water_cons
            )
            data['water_consumption_cost'] = compute_water_cost(
                data["total_water_m3"], inputs.water_unit_cost_per_m3
            )
            
            # Compute revenues 
            data['solar_export_revenues'] = data['solar_grid_export'] * data['export_revenues']
            data['wind_export_revenues'] = data['wind_grid_export'] * data['export_revenues']
            data['total_grid_export_revenues'] = data['solar_export_revenues'] + data['wind_export_revenues']
            
            
            # # Checking data
            # data_to_excel = data[
            #     [
            #         "P_solar",
            #         "P_wind",
            #         "P_solar_aux",
            #         "RES",
            #         'solar_PPA',
            #         'wind_PPA',
            #         'grid_import_tariff',
            #         "NH3_target",
            #         "grid_import",
            #         "grid_to_HB",
            #         "grid_to_H2",
            #         "RES_to_plant",
            #         "RES_to_HB",
            #         "RES_to_H2",
            #         "total_H2_charging",
            #         "H2_discharge",
            #         "H2_storage",
            #         "H2_SOC_[%]",
            #         "direct_solar",
            #         "direct_wind",
            #         "final_solar_curtailment",
            #         "final_wind_curtailment",
            #         "grid_export",
            #         "solar_grid_export",
            #         "wind_grid_export",
            #         'wind_cost',
            #         'solar_cost',
            #         'grid_import_cost',
            #         "final_H2_prod_[MWh]",
            #         "final_NH3_prod_[MWh]",
            #         "final_H2_prod_[tonnes]",
            #         "final_NH3_prod_[tonnes]",
            #         "NH3_cf_[%]",
            #         "H2_cf_[%]",
            #     ]
            # ].copy()
            # data_to_excel.to_excel("check_data.xlsx")

            "##############################################################################################"
            # Compute the sum and avg of some columns for each year
            data.loc["Total"] = data.sum()

            # Compute averages of some columns
            data.loc["Total", "H2_SOC_target"] = data["H2_SOC_target"].sum() / len(data)
            data.loc["Total", "H2_SOC_[%]"] = data["H2_SOC_[%]"].sum() / len(data)
            data.loc["Total", "H2_storage"] = data["H2_storage"].sum() / len(data)
            data.loc["Total", "H2_SOC_target_remaining"] = data["H2_SOC_target_remaining"].sum() / len(data)
            data.loc["Total", "SOC_[%]"] = data["SOC_[%]"].sum() / len(data)
            data.loc["Total", "E_bat"] = data["E_bat"].sum() / len(data)
            data.loc["Total", "NH3_cf_[%]"] = data["NH3_cf_[%]"].sum() / len(data)
            data.loc["Total", "H2_cf_[%]"] = data["H2_cf_[%]"].sum() / len(data)
            data.loc["Total", "BESS_usable_energy"] = BESS_usable_energy
            data.loc["Total", "Electrolyser_SOH"] = elec_SOH
            data.loc["Total", "shortfall_events"] = data["shortfall_events"].sum() / len(data)

            # create the annual summary dataframe
            required_columns = data.columns
            summary_data = data[required_columns].tail(1)
            result = summary_data.to_dict(orient="records")
            result_rows.extend(result)
            # print(data.columns)

            # Drop some columns
            # data = data.drop([ 'max_loads', 'min_loads', 'RES_constraint', 'NH3_potential_3h', 'NH3_potential_2h',
            #     'NH3_potential', 'excess_after_min', 'NH3_supplied_after_RES',
            #     'H2_supplied_after_RES', 'min_loads_after_RES', 'excess_after_max',
            #     'max_loads_after_RES', 'min_loads_after_RES_pot_curtailment',
            #     'H2_min_required', 'H2_min_pot_curtailment',
            #     'min_loads_supplied_after_RES', 'wind_prod_ratio', 'solar_prod_ratio',
            #     'H2_shortfall', 'H2_SOC_target',  'RES_excess_after_H2','min_loads_after_H2', 'excess_after_H2_charging',
            #     'H2_SOC_target_remaining', 'BESS_shortfall', 'SOC_target',
            #     'NH3_potential_inclu_SOCs_excess', 'NH3_target', 'RES_available', 'NH3_target_after_grid', 'H2_SOC_excess', 'BESS_SOC_excess', 'H2_discharge',
            #     'SOCs_excess', 'NH3_target_remainder', 'max_energy_deficit', 'min_energy_deficit', 'trip_event', 'test_ramp_rates',
            #     'BESS_usable_energy', 'Electrolyser_SOH'], axis=1)

        # fillna with 0
        data = data.fillna(0)
        if "SOC_[%]" not in data.columns:
            data["SOC_[%]"] = 0.0

        sheet = "year " + str(project_year)
        project_year += 1

        # Updating BESS and H2 storage SOC
        # Updating BESS and H2 storage SOC (robust to cases without BESS / H2 storage columns)
        if "SOC_[%]" in data.columns and len(data) >= 2:
            BESS_init_SOC = float(data["SOC_[%]"].iloc[-2])
        else:
            BESS_init_SOC = float(getattr(inputs, "init_SOC", 0.0))

        if "H2_SOC_[%]" in data.columns and len(data) >= 2:
            H2_Storage_SOC = float(data["H2_SOC_[%]"].iloc[-2])
        else:
            H2_Storage_SOC = float(getattr(inputs, "init_H2_SOC", 0.0))


        if project_year > inputs.project_lifetime + 1:
            break

        # Save to excel 25 years full data frame
        # ws = wb.create_sheet(sheet)
        # data.to_excel(writer, sheet, float_format='%.4f', index=True, header=True, startcol=0, startrow=0)

        # drop the total row
        data = data.drop(index="Total", errors="ignore")


        # save the summary dataframe
        yearly_result = pd.DataFrame(result_rows, columns=result_rows[0].keys())
        yearly_result.index += 1
        yearly_result = yearly_result.reset_index().rename(columns={"index": "year"})

        # data.to_csv("data.csv")

        "####################################################################################################"

        """ Financial modelling is considering NPVs for production, costs and output at the end the net LCOEs for each component """

        # financial modeling

        # NPV columns
        # solar NPV
        NPV_factor = (1 + inputs.discount_rate) ** (yearly_result["year"]-1)

        yearly_result["gross_solar_NPV"] = yearly_result["P_solar"] / NPV_factor
        yearly_result['solar_cost_NPV'] = yearly_result['solar_cost'] / NPV_factor 
                # PV fixed OPEX (Excel-free): model as a fraction of PV_capex (kUSD) + any additional PV_opex passed in (kUSD/yr)
        pv_frac_start = getattr(inputs, "pv_fixed_opex_frac_start", 0.020)
        pv_frac_mid   = getattr(inputs, "pv_fixed_opex_frac_mid",   0.025)
        pv_frac_end   = getattr(inputs, "pv_fixed_opex_frac_end",   0.030)
        pv_warranty   = getattr(inputs, "pv_warranty_years",        2)
        pv_midlife    = getattr(inputs, "pv_midlife_year",          min(12, inputs.project_lifetime))
        pv_real_esc   = getattr(inputs, "pv_opex_real_escalation",  0.0)
        pv_fixed_nom_kusd = _annual_fixed_opex_kusd(
            capex_kusd=PV_capex,
            years=yearly_result["year"],
            frac_start=pv_frac_start,
            frac_mid=pv_frac_mid,
            frac_end=pv_frac_end,
            warranty_years=pv_warranty,
            midlife_year=pv_midlife,
            lifetime=inputs.project_lifetime,
            inflation=inputs.inflation,
            real_opex_escalation=pv_real_esc,
        )
        pv_extra_nom_kusd = PV_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1)
        yearly_result["PV_opex"] = (pv_fixed_nom_kusd + pv_extra_nom_kusd) / NPV_factor 
        yearly_result["final_solar_curtailment_NPV"] = yearly_result["final_solar_curtailment"] / NPV_factor
        yearly_result["total_solar_BESS_charging_NPV"] = (yearly_result["P_solar_BESS_charging"] + yearly_result["BESS_solar_charging_after_grid_export"]) / NPV_factor #* PV_capacity * 1000
        yearly_result["total_solar_H2_charging_NPV"] = (yearly_result["H2_solar_charging"] + yearly_result["H2_solar_charging_after_grid_export"]) / NPV_factor
        yearly_result["PV_variable_opex_NPV"] = (yearly_result["P_solar"] - yearly_result["final_solar_curtailment"]) * inputs.PV_variable_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor
        yearly_result["direct_solar_NPV"] = yearly_result["direct_solar"] / NPV_factor

        # Wind NPV
        yearly_result["gross_wind_NPV"] = yearly_result["P_wind"] / NPV_factor
        yearly_result['wind_cost_NPV'] = yearly_result['wind_cost'] / NPV_factor
                # Wind fixed OPEX (Excel-free): model as a fraction of wind_capex (kUSD) + any additional wind_opex passed in (kUSD/yr)
        wind_frac_start = getattr(inputs, "wind_fixed_opex_frac_start", 0.025)
        wind_frac_mid   = getattr(inputs, "wind_fixed_opex_frac_mid",   0.032)
        wind_frac_end   = getattr(inputs, "wind_fixed_opex_frac_end",   0.040)
        wind_warranty   = getattr(inputs, "wind_warranty_years",        2)
        wind_midlife    = getattr(inputs, "wind_midlife_year",          min(10, inputs.project_lifetime))
        wind_real_esc   = getattr(inputs, "wind_opex_real_escalation",  0.0)
        wind_fixed_nom_kusd = _annual_fixed_opex_kusd(
            capex_kusd=wind_capex,
            years=yearly_result["year"],
            frac_start=wind_frac_start,
            frac_mid=wind_frac_mid,
            frac_end=wind_frac_end,
            warranty_years=wind_warranty,
            midlife_year=wind_midlife,
            lifetime=inputs.project_lifetime,
            inflation=inputs.inflation,
            real_opex_escalation=wind_real_esc,
        )
        wind_extra_nom_kusd = wind_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1)
        yearly_result["wind_opex"] = (wind_fixed_nom_kusd + wind_extra_nom_kusd) / NPV_factor

        yearly_result["final_wind_curtailment_NPV"] = yearly_result["final_wind_curtailment"] / NPV_factor
        yearly_result["total_wind_BESS_charging_NPV"] = (yearly_result["P_wind_BESS_charging"] + yearly_result["BESS_wind_charging_after_grid_export"]) / NPV_factor
        yearly_result["total_wind_H2_charging_NPV"] = (yearly_result["H2_wind_charging"] + yearly_result["H2_wind_charging_after_grid_export"]) / NPV_factor
        yearly_result["wind_variable_opex_NPV"] = (yearly_result["P_wind"] - yearly_result["final_wind_curtailment"]) * inputs.wind_variable_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor
        yearly_result["direct_wind_NPV"] = yearly_result["direct_wind"] / NPV_factor

        # BESS
        yearly_result["BESS_opex"] = inputs.BESS_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor
        yearly_result["BESS_NPV"] = (yearly_result["BESS_discharge"]) / NPV_factor

        # electrolyser
        yearly_result["electrolyser_opex_NPV"] = electrolyser_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor

        # H2 storage
        yearly_result["H2_storage_opex_NPV"] = H2_storage_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor
        yearly_result["H2_discharge_storage_NPV"] = (yearly_result["H2_discharge"]) / NPV_factor

        # HB
        yearly_result["HB_opex_NPV"] = HB_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor

        # NH3 storage
        yearly_result["NH3_storage_opex_NPV"] = NH3_storage_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1
        ) / NPV_factor

        # land costs
        yearly_result["NH3_fixed_opex_NPV"] = NH3_Storage_fixed_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor
        yearly_result["process_plant_fixed_opex_NPV"] = process_plant_fixed_opex * (1 + inputs.inflation) ** (yearly_result["year"]-1) / NPV_factor

        # Water costs (uses water_consumption_cost computed by WaterModel in dispatch)
        yearly_result["water_cost_NPV"] = yearly_result["water_consumption_cost"] / NPV_factor
        # Keep legacy columns for backward compatibility (now zero)
        yearly_result["H2_water_cost_NPV"] = 0.0
        yearly_result["NH3_water_cost_NPV"] = 0.0

        # Grid
        yearly_result["grid_import_NPV"] = yearly_result["grid_import"] / NPV_factor
        yearly_result['grid_import_cost_NPV'] = yearly_result['grid_import_cost'] / NPV_factor
        yearly_result["solar_grid_export_NPV"] = yearly_result["solar_grid_export"] / NPV_factor
        yearly_result["wind_grid_export_NPV"] = yearly_result["wind_grid_export"] / NPV_factor

        yearly_result["max_energy_deficit_NPV"] = yearly_result["max_energy_deficit"] / NPV_factor
        yearly_result["min_energy_deficit_NPV"] = yearly_result["min_energy_deficit"] / NPV_factor
        yearly_result["grid_OPEX_NPV"] = (inputs.grid_opex) * ((1 + inputs.inflation) ** (yearly_result["year"] - 1)) / NPV_factor

        # load supplied NPV
        yearly_result["final_NH3_prod_NPV"] = yearly_result["final_NH3_prod_[MWh]"] / NPV_factor
        yearly_result["max_loads_NPV"] = yearly_result["max_loads"] / NPV_factor
        yearly_result["min_loads_NPV"] = yearly_result["min_loads"] / NPV_factor

        # H2 Production
        yearly_result["final_H2_prod_[tonnes]_NPV"] = yearly_result["final_H2_prod_[tonnes]"] / NPV_factor
        yearly_result["final_NH3_prod_[tonnes]_NPV"] = yearly_result["final_NH3_prod_[tonnes]"] / NPV_factor

        # Pipeline OPEX
        yearly_result["H2_pipeline_opex_NPV"] = inputs.H2_pipeline_opex * ((1 + inputs.inflation) ** (yearly_result["year"] - 1)) / NPV_factor
        yearly_result["H2O_pipeline_opex_NPV"] = inputs.H2O_pipeline_opex * ((1 + inputs.inflation) ** (yearly_result["year"] - 1)) / NPV_factor

        # Check for BESS augmentation cost and Electrolyser augmentation costs
        BESS_aug_rows = []
        elect_aug_rows = []
        for row in yearly_result.itertuples():
            if row.BESS_usable_energy == BESS_energy and row.year > 1:
                BESS_augmentation = BESS_capex * inputs.BESS_aug_cost
            else:
                BESS_augmentation = 0

            if row.Electrolyser_SOH == electrolyser_capacity and row.year > 1:
                electrolyser_augmentation_cost = inputs.elec_capex_epc * electrolyser_capacity * inputs.elec_aug_cost * 1000
            else:
                electrolyser_augmentation_cost = 0

            BESS_aug_rows.append(BESS_augmentation)
            elect_aug_rows.append(electrolyser_augmentation_cost)

        # create the BESS augmentation and electrolyser augmentation columns and add it to summary result.
        yearly_result["BESS_aug_cost"] = ((BESS_aug_rows) * ((1 + inputs.inflation) ** (yearly_result["year"]-1))) / (1 + inputs.discount_rate) ** yearly_result["year"]
        yearly_result["elect_aug_cost"] = ((elect_aug_rows) * ((1 + inputs.inflation) ** (yearly_result["year"]-1))) / (1 + inputs.discount_rate) ** yearly_result["year"]

        # Augmentation cost of HB
        num_of_aug_elec = (inputs.project_lifetime * inputs.hours_per_year) / inputs.elec_lifetime
        number_of_aug_HB = round(inputs.project_lifetime * inputs.hours_per_year) / inputs.HB_lifetime
        year_of_aug_HB = int(inputs.project_lifetime / number_of_aug_HB)
        HB_augmentation_cost = NH3prod.compute_augmentation_cost(year_of_aug_HB, inputs.project_lifetime, inputs.HB_capex_epc, HB_capacity, inputs.HB_aug_cost, inputs.inflation, inputs.discount_rate)

        # Create the column for electrolyser stack replacement
        electrolyser_stack_replacment_rows = []
        for index, row in yearly_result.iterrows():
            if row.elect_aug_cost > 0:
                electrolyser_stack_replacment = row.elect_aug_cost
            else:
                electrolyser_stack_replacment = 0
            electrolyser_stack_replacment_rows.append(electrolyser_stack_replacment)
        yearly_result["electrolyser_stack_replacment"] = electrolyser_stack_replacment_rows

        # Create a column for HB referbishemnt
        HB_refurbishment_rows = []
        for index, row in yearly_result.iterrows():
            if row.year % year_of_aug_HB == 0:
                HB_refurbishment = inputs.HB_capex_epc * HB_capacity * inputs.HB_aug_cost * 1000

            else:
                HB_refurbishment = 0

            HB_refurbishment_rows.append(HB_refurbishment)
        yearly_result["HB_refurbishment"] = HB_refurbishment_rows
        yearly_result['grid_fixed_opex'] = grid_opex

        # calculate LCOE & other financial metrics

        # Gross LCOE
        # solar
        solar_capex = PV_capex
        solar_opex = yearly_result["PV_opex"].sum()
        solar_gross_prod = yearly_result["gross_solar_NPV"].sum()
        solar_variable_opex_total = yearly_result["PV_variable_opex_NPV"].sum()
        # print(PV_capacity)

        # wind
        wind_total_capex = wind_capex
        wind_total_opex = yearly_result["wind_opex"].sum()
        wind_gross_prod = yearly_result["gross_wind_NPV"].sum()
        wind_variable_opex_total = yearly_result["wind_variable_opex_NPV"].sum()

        # RES costs (PPA)
        solar_cost = yearly_result['solar_cost_NPV'].sum()
        wind_cost = yearly_result['wind_cost_NPV'].sum()
        RES_cost = solar_cost + wind_cost
        
        # BESS
        BESS_capex = BESS_capex
        BESS_opex_total = yearly_result["BESS_opex"].sum()
        BESS_aug = yearly_result["BESS_aug_cost"].sum()
        BESS_discharge = yearly_result["BESS_NPV"].sum()
        BESS_throughput = BESS_discharge

        # H2 and HB
        electrolyser_opex_total = yearly_result["electrolyser_opex_NPV"].sum()
        electrolyser_augmentation_cost = yearly_result["elect_aug_cost"].sum()

        # H2 storage
        H2_opex_total = yearly_result["H2_storage_opex_NPV"].sum()
        HB_opex_total = yearly_result["HB_opex_NPV"].sum()

        # NH3 storage
        NH3_storage_opex_total = yearly_result["NH3_storage_opex_NPV"].sum()
        NH3_Storage_fixed_opex_total = yearly_result["NH3_fixed_opex_NPV"].sum()

        # process plant
        process_plant_opex_total = yearly_result["process_plant_fixed_opex_NPV"].sum()

        # water_cost
        water_cost_total = yearly_result["water_cost_NPV"].sum()
        # Legacy (kept at 0 for backward compat)
        H2_water_cost_total = 0.0
        NH3_water_cost_total = 0.0

        # Pipeline Opex
        H2_pipeline_total_opex = yearly_result["H2_pipeline_opex_NPV"].sum()
        H2O_pipeline_total_opex = yearly_result["H2O_pipeline_opex_NPV"].sum()

        # Grid
        total_grid_opex =yearly_result['grid_OPEX_NPV'].sum()
        grid_cost = yearly_result["grid_import_cost_NPV"].sum()

        # Gross LCOE
        gross_solar_LCOE = (solar_capex + solar_opex) / solar_gross_prod
        #print (solar_opex, solar_capex)
        gross_wind_LCOE = (wind_total_capex + wind_total_opex) / wind_gross_prod
        BESS_LCOE = ((BESS_capex) + BESS_opex_total + BESS_aug) / BESS_throughput

        # Other metrics
        # Load
        total_demand = yearly_result["final_NH3_prod_NPV"].sum()
        max_loads = yearly_result["max_loads_NPV"].sum()
        min_loads = yearly_result["min_loads_NPV"].sum()

        # Compute NPV of H2 and NH3 production
        H2_production_tonnes = yearly_result["final_H2_prod_[tonnes]_NPV"].sum()
        NH3_production_tonnes = yearly_result["final_NH3_prod_[tonnes]_NPV"].sum()
        final_energy_deficit_per = yearly_result["max_energy_deficit_NPV"].sum() / max_loads
        min_energy_deficit = yearly_result["min_energy_deficit_NPV"].sum() / min_loads

        # Solar
        PV_curtailment = yearly_result["final_solar_curtailment_NPV"].sum()
        PV_curtailment_per = yearly_result["final_solar_curtailment_NPV"].sum() / yearly_result["gross_solar_NPV"].sum()

        # Wind
        wind_curtailment = yearly_result["final_wind_curtailment_NPV"].sum()
        wind_curtailment_per = yearly_result["final_wind_curtailment_NPV"].sum() / yearly_result["gross_wind_NPV"].sum()

        # Grid
        total_wind_grid_export = yearly_result["wind_grid_export_NPV"].sum()
        total_solar_grid_export = yearly_result["solar_grid_export_NPV"].sum()
        grid_import_total = yearly_result["grid_import_NPV"].sum()

        # BESS
        solar_BESS_charging = yearly_result["total_solar_BESS_charging_NPV"].sum()
        wind_BESS_charging = yearly_result["total_wind_BESS_charging_NPV"].sum()
        BESS_charging = solar_BESS_charging + wind_BESS_charging

        # H2 Storage
        solar_H2_charging = yearly_result["total_solar_H2_charging_NPV"].sum()
        wind_H2_charging = yearly_result["total_wind_H2_charging_NPV"].sum()
        H2_charging = solar_H2_charging + wind_H2_charging
        H2_discharge = yearly_result["H2_discharge_storage_NPV"].sum()
        H2_discharge_per = H2_discharge / total_demand

        # solar
        gross_solar = yearly_result["gross_solar_NPV"].sum()
        direct_solar_use = yearly_result["direct_solar_NPV"].sum()
        solar_share = (direct_solar_use - solar_H2_charging) / total_demand

        # wind
        gross_wind = yearly_result["gross_wind_NPV"].sum()
        direct_wind_use = yearly_result["direct_wind_NPV"].sum()
        wind_share = (direct_wind_use - wind_H2_charging) / total_demand

        # BESS

        BESS_throughput_share = BESS_throughput / total_demand
        BESS_throughput_solar = BESS_throughput * (solar_BESS_charging / BESS_charging)
        BESS_throughput_wind = BESS_throughput * (wind_BESS_charging / BESS_charging)

        # H2
        solar_H2_storage_discharge = H2_discharge * (solar_H2_charging / H2_charging)
        wind_H2_storage_discharge = H2_discharge * (wind_H2_charging / H2_charging)

        # Compute LCOE, LCOH & LCOA
        final_product = getattr(inputs, 'final_product', 'NH3')
        sum_of_cost_LCOE = solar_capex + solar_opex + wind_total_capex + wind_total_opex + RES_cost + grid_capex + BESS_capex + inputs.BESS_opex + BESS_aug + solar_variable_opex_total + wind_variable_opex_total + grid_cost + total_grid_opex

        sum_of_cost_LCOH = sum_of_cost_LCOE + electrolyser_capex + electrolyser_opex_total + electrolyser_augmentation_cost + H2_storage_capex + H2_opex_total + H2_BOP_capex + H2_BOP_opex + water_cost_total + process_plant_opex_total + inputs.H2_pipeline_capex + inputs.H2O_pipeline_capex + H2_pipeline_total_opex + H2O_pipeline_total_opex
        if final_product == "H2":
            sum_of_cost_LCOA = float('nan')  # No NH3 plant in H2-only mode
        else:
            sum_of_cost_LCOA = sum_of_cost_LCOH + HB_capex + HB_opex_total + HB_augmentation_cost + NH3_storage_capex + NH3_storage_opex_total + NH3_Storage_fixed_opex_total

        # Compute Sum of production
        sum_of_prod = total_demand

        Gross_wind_LCOE = (wind_total_capex + wind_total_opex + wind_variable_opex_total + wind_cost) / gross_wind
        Gross_solar_LCOE = (solar_capex + solar_opex + solar_variable_opex_total + solar_cost) / gross_solar
        Total_LCOE = sum_of_cost_LCOE / (direct_solar_use + direct_wind_use + BESS_throughput)
        Total_LCOH = (sum_of_cost_LCOH) / H2_production_tonnes if H2_production_tonnes > 0 else float('nan')
        if final_product == "H2":
            Total_LCOA = float('nan')
        else:
            Total_LCOA = (sum_of_cost_LCOA) / NH3_production_tonnes if NH3_production_tonnes > 0 else float('nan')
        print("LCOH=", Total_LCOH)
        print("LCOA=", Total_LCOA)
        # Caculate nominal values of some parameters to be reported in final summary result dataframe.
        H2_prod_TPA = yearly_result["final_H2_prod_[tonnes]"].mean()
        NH3_prod_TPA = yearly_result["final_NH3_prod_[tonnes]"].mean()
        max_NH3_per_hour = data["final_NH3_prod_[tonnes]"].max()
        max_H2_per_hour = data["final_H2_prod_[tonnes]"].max()
        # capacity_factor_NH3 = yearly_result["NH3_cf_[%]"].mean() #/ 2
        # capacity_factor_H2 = yearly_result["H2_cf_[%]"].mean() #/ 2
        # FIX: Calculate CF properly from annual totals: (Total MWh) / (Capacity MW × 8760 hours) × 100
        # capacity_factor_NH3 = (yearly_result["final_NH3_prod_[MWh]"].sum() / (HB_capacity * 8760)) * 100  # BUG: sums all 30 years
        # capacity_factor_H2 = (yearly_result["final_H2_prod_[MWh]"].sum() / (electrolyser_capacity * 8760)) * 100  # BUG: sums all 30 years
        # FIXED: Use .mean() to get average annual MWh instead of summing all years
        capacity_factor_NH3 = (yearly_result["final_NH3_prod_[tonnes]"].mean() / (HB_capacity_tonne * 8760)) * 100
        capacity_factor_H2 = (yearly_result["final_H2_prod_[MWh]"].mean() / (electrolyser_capacity * 8760)) * 100
        total_demand = yearly_result["final_NH3_prod_[MWh]"].mean()
        gross_solar = yearly_result["P_solar"].mean()
        gross_wind = yearly_result["P_wind"].mean()
        direct_solar_use = yearly_result["direct_solar"].mean()
        direct_wind_use = yearly_result["direct_wind"].mean()
        solar_share = yearly_result["direct_solar"].mean() / total_demand
        wind_share = yearly_result["direct_wind"].mean() / total_demand
        BESS_throughput = yearly_result["BESS_discharge"].mean()
        BESS_throughput_share = BESS_throughput / total_demand
        H2_discharge_per = yearly_result["H2_discharge"].mean() / total_demand
        PV_curtailment_per = yearly_result["final_solar_curtailment"].mean() / gross_solar
        wind_curtailment_per = yearly_result["final_wind_curtailment"].mean() / gross_wind
        grid_import_total = yearly_result["grid_import"].mean()
        grid_import_share = grid_import_total / total_demand
        total_solar_grid_export = yearly_result["solar_grid_export"].mean()
        total_wind_grid_export = yearly_result["wind_grid_export"].mean()
        shortfall_events = round(yearly_result["shortfall_events"].min())
        grey_H2_prod_per = yearly_result['grey_H2_prod_[tonnes]'].mean() /  H2_prod_TPA

       
        # Save to CSV — all outputs go into Summary_output
        outdir = "Summary_output"
        if not os.path.exists(outdir):
            os.mkdir(outdir)
        yearly_result.to_csv(f"{outdir}/{file_output_name} summary.csv", index=False)
        file_output = file_output_name + "Project_Summary" + ".csv"

        
        
        # Create data for IRR
      

        # --- Final product mode: exclude HB/NH3 costs for H2-only ---
        final_product = getattr(inputs, 'final_product', 'NH3')

        if final_product == "H2":
            # H2-only: exclude HB plant and NH3 storage from CAPEX
            effective_HB_capex = 0
            effective_NH3_storage_capex = 0
            effective_HB_opex = 0
            effective_NH3_storage_opex = 0
            effective_NH3_Storage_fixed_opex_total = 0
            effective_HB_opex_total = 0
            effective_HB_augmentation_cost = 0
        else:
            effective_HB_capex = HB_capex
            effective_NH3_storage_capex = NH3_storage_capex
            effective_HB_opex = HB_opex
            effective_NH3_storage_opex = NH3_storage_opex
            effective_NH3_Storage_fixed_opex_total = NH3_Storage_fixed_opex_total
            effective_HB_opex_total = HB_opex_total
            effective_HB_augmentation_cost = HB_augmentation_cost

        total_capex = electrolyser_capex + H2_storage_capex + effective_HB_capex + effective_NH3_storage_capex + H2_storage_compressor_capex + PV_capex + wind_capex + grid_capex
        data_for_IRR = yearly_result[['final_NH3_prod_[tonnes]','green_NH3_prod_[tonnes]', 'grey_NH3_prod_[tonnes]',  'total_grid_export_revenues', 'grid_import_cost', 'solar_cost', 'wind_cost', 'grid_fixed_opex', 'HB_refurbishment', 'water_consumption_cost','electrolyser_stack_replacment',
                                     "PV_opex", 'wind_opex' ]].copy()
        # Revenues from product selling
        data_for_IRR.index += 1
        data_for_IRR = data_for_IRR.reset_index().rename(columns={'index': 'year'})
        data_for_IRR['revenues_from_NH3_EUR'] = data_for_IRR['green_NH3_prod_[tonnes]'] * inputs.green_NH3_selling_price + data_for_IRR['grey_NH3_prod_[tonnes]'] * inputs.grey_NH3_selling_price

        # H2 revenue: based on H2 production (green vs grey split)
        green_H2_price = getattr(inputs, 'green_H2_selling_price', 0)
        grey_H2_price = getattr(inputs, 'grey_H2_selling_price', 0)
        data_for_IRR['revenues_from_H2_EUR'] = data_for_IRR['green_NH3_prod_[tonnes]'] * inputs.H2_t_to_NH3_t * green_H2_price + data_for_IRR['grey_NH3_prod_[tonnes]'] * inputs.H2_t_to_NH3_t * grey_H2_price

        if final_product == "H2":
            data_for_IRR['product_revenues'] = data_for_IRR['revenues_from_H2_EUR']
        elif final_product == "H2+NH3":
            data_for_IRR['product_revenues'] = data_for_IRR['revenues_from_NH3_EUR'] + data_for_IRR['revenues_from_H2_EUR']
        else:  # "NH3" (default)
            data_for_IRR['product_revenues'] = data_for_IRR['revenues_from_NH3_EUR']

        data_for_IRR['total_revenues_EUR'] = data_for_IRR['product_revenues'] + data_for_IRR['total_grid_export_revenues']

        # Compute total Opex (use effective values to exclude HB/NH3 costs in H2-only mode)
        data_for_IRR['total_OPEX_EUR'] = (electrolyser_opex * (1 + inputs.inflation) ** (yearly_result['year'])) + (H2_storage_opex * (1 + inputs.inflation) ** (yearly_result['year'])) + \
            data_for_IRR['electrolyser_stack_replacment'] + (0 if final_product == "H2" else data_for_IRR['HB_refurbishment']) + (effective_HB_opex  * (1 + inputs.inflation) ** (yearly_result['year'])) + \
                (effective_NH3_storage_opex * (1 + inputs.inflation) ** (yearly_result['year'])) + (H2_storage_compressor_opex * (1 + inputs.inflation) ** (yearly_result['year'])) + \
                    data_for_IRR['PV_opex'] + data_for_IRR['wind_opex']

        # Compute total costs
        data_for_IRR['total_costs_EUR'] = data_for_IRR ['solar_cost'] + data_for_IRR ['wind_cost'] + data_for_IRR ['grid_import_cost'] + data_for_IRR ['grid_fixed_opex'] + data_for_IRR ['water_consumption_cost'] + data_for_IRR['total_OPEX_EUR']


        # Compute D_&_A 
        data_for_IRR.loc[data_for_IRR['year'] <= inputs.depreciation, 'D_and_A_EUR'] = (total_capex) / inputs.depreciation
        data_for_IRR['D_and_A_EUR'] = data_for_IRR['D_and_A_EUR'].fillna(0)

        # Compute Taxes
        data_for_IRR['taxes'] = np.fmax(0, (data_for_IRR['total_revenues_EUR'] - data_for_IRR['total_costs_EUR'] - data_for_IRR['D_and_A_EUR']) * inputs.taxes)

        # Compute cash_flows
        add_year_zero = pd.DataFrame({'year': [0]})
        data_for_IRR = pd.concat([add_year_zero, data_for_IRR]).reset_index(drop=True)
        # Add 'year-1' and 'year-2' columns
        add_year_minus_one = pd.DataFrame({'year': [-1]})
        data_for_IRR = pd.concat([add_year_minus_one, data_for_IRR]).reset_index(drop=True)
        add_year_minus_two = pd.DataFrame({'year': [-2]})
        data_for_IRR = pd.concat([add_year_minus_two, data_for_IRR]).reset_index(drop=True)
        data_for_IRR['cashflows'] = data_for_IRR['total_revenues_EUR'] - data_for_IRR['total_costs_EUR'] - data_for_IRR['taxes']
        data_for_IRR['cashflows'] = data_for_IRR['cashflows'] + data_for_IRR["year"].apply(lambda y: -total_capex * inputs.get_capex_deployment(y))
        data_for_IRR = data_for_IRR.fillna(0) 
        cashflows_list = data_for_IRR['cashflows'].to_list()
        cashflows = data_for_IRR['cashflows'].sum()
        IRR = npf.irr(cashflows_list)

        # calculate NPV 
        data_for_IRR['cashflows_NPV'] = data_for_IRR['cashflows'] / (1 + inputs.discount_rate) ** data_for_IRR['year']
        cashflows_NPV = data_for_IRR['cashflows_NPV'].sum()
        data_for_IRR['RES_revenues_NPV'] = (data_for_IRR['total_grid_export_revenues']) / (1 + inputs.discount_rate) ** data_for_IRR['year']
        RES_export_revenues_NPV = data_for_IRR['RES_revenues_NPV'].sum()

        # Compute NH3 green and grey NPV rev
        data_for_IRR['green_NH3_rev_NPV'] =  (data_for_IRR['green_NH3_prod_[tonnes]'] * inputs.green_NH3_selling_price ) / (1 + inputs.discount_rate) ** data_for_IRR['year']
        data_for_IRR['grey_NH3_rev_NPV'] =  (data_for_IRR['grey_NH3_prod_[tonnes]'] * inputs.grey_NH3_selling_price) / (1 + inputs.discount_rate) ** data_for_IRR['year']
        green_NH3_rev_NPV = data_for_IRR['green_NH3_rev_NPV'].sum()
        grey_NH3_rev_NPV = data_for_IRR['grey_NH3_rev_NPV'].sum()

        # Compute H2 NPV rev
        data_for_IRR['green_H2_rev_NPV'] = (data_for_IRR['green_NH3_prod_[tonnes]'] * inputs.H2_t_to_NH3_t * green_H2_price) / (1 + inputs.discount_rate) ** data_for_IRR['year']
        data_for_IRR['grey_H2_rev_NPV'] = (data_for_IRR['grey_NH3_prod_[tonnes]'] * inputs.H2_t_to_NH3_t * grey_H2_price) / (1 + inputs.discount_rate) ** data_for_IRR['year']
        green_H2_rev_NPV = data_for_IRR['green_H2_rev_NPV'].sum()
        grey_H2_rev_NPV = data_for_IRR['grey_H2_rev_NPV'].sum()


        # --- DEBUG: check CAPEX deployment fractions by year ---
        print(data_for_IRR[["year"]].assign(capex_frac=lambda df: df["year"].apply(inputs.get_capex_deployment)).query("capex_frac != 0"),flush=True)


        # Split teh CAPEX for the first three years 
        #data_for_IRR['CAPEX_deployments_USD'] = 0 
        data_for_IRR["CAPEX_deployments_USD"] = data_for_IRR["year"].apply(lambda y: -total_capex * inputs.get_capex_deployment(y))
        print(data_for_IRR[["year", "CAPEX_deployments_USD"]])


        # data_for_IRR.loc[data_for_IRR['year'] == -1, 'CAPEX_deployments_EUR'] = - total_capex * inputs.capex_year_1
        # data_for_IRR.loc[data_for_IRR['year'] == 0, 'CAPEX_deployments_EUR'] = - total_capex * inputs.capex_year_0
        
         # Create final_summary dataframe with desirable metrics
        overall_data = {
            "Solar_capacity [MW]": [PV_capacity],
            "Solar_capacity [MWp]": [PV_capacity * inputs.PV_DC_AC],
            "Wind_capacity [MW]": [wind_capacity],
            "BESS_Energy [MWh]": [BESS_energy],
            "BESS_Power [MW]": [BESS_power],
            "H2_Storage [tonnes]": [H2_storage_capacity_tonnes],
            "Plant Capacity [MW]": [electrolyser_capacity + HB_capacity],
            "HB_Capacity[MW]": [HB_capacity],
            "PV_PPA": [PV_ppa],
            "HB_to_electrolyser_ratio_[%]": [NH3_plant_capacity_ratio],
            "Electrolyser_Capacity[MW]": [electrolyser_capacity],
            "Grid_import_capacity[MW]": [grid_import_capacity],
            "Grid_export_capacity[MW]": [grid_export_capacity],
            "Total_NH3_prod [MWh]": [total_demand],
            "Gross_solar [MWh]": [gross_solar],
            "Direct_solar [MWh]": [direct_solar_use],
            "Solar_share [%]": [solar_share],
            "Gross_wind [MWh]": gross_wind,
            "Direct_Wind [MWh]": [direct_wind_use],
            "Wind_share [%]": [wind_share],
            "BESS_throughput [MWh]": [BESS_throughput],
            "BESS_throughput_[%]": [BESS_throughput_share],
            "H2_discharge [%]": [H2_discharge_per],
            "solar_curtailment [%]": [PV_curtailment_per],
            "wind_curtailment [%]": [wind_curtailment_per],
            "grid_import [MWh]": grid_import_total,
            "grid_import [%]": [grid_import_share],
            "solar_export [MWh]": [total_solar_grid_export],
            "wind_export": [total_wind_grid_export],
            "Gross_wind_LCOE [$/MWh]": [Gross_wind_LCOE],
            "Gross_solar_LCOE": [Gross_solar_LCOE],
            "Number_of_shortfall_events_per_year": [shortfall_events],
            "Total_LCOE [$/MWh]": [Total_LCOE],
            "Total_LCOH [$/tonnes]": [Total_LCOH],
            "Total_LCOA [$/tonnes]": [Total_LCOA],
            "NH3_prod_[TPA]": [NH3_prod_TPA],
            "H2_prod_[TPA]": [H2_prod_TPA],
            "CF_NH3 [%]": [capacity_factor_NH3],
            "CF_H2 [%]": [capacity_factor_H2],
            'grey_H2_prod_per_ NH3[%]': [grey_H2_prod_per],
            'IRR [%]': [IRR * 100],
            'NPV [$M]': [cashflows_NPV / 1e6],
            'Total_CAPEX [$M]': [total_capex / 1e6],
            'final_product': [final_product],
            'green_H2_selling_price [$/t]': [green_H2_price],
            'grey_H2_selling_price [$/t]': [grey_H2_price],
        }

        # Assign overall data to a dataframe
        project_summary = pd.DataFrame(overall_data)
        project_summary.to_csv(f"{outdir}/{file_output}", index=False)
        
        # Save IRR data to excel 
        data_for_IRR.index += 1
        data_for_IRR = data_for_IRR.reset_index().rename(columns={'index': 'year'})
        data_for_IRR.to_csv(f"{outdir}/{file_output_name}IRR.csv", index=False)


        # Extract NH3 production in tonnes and electricity consumption and store it in a a new datafarme dataframe
        NH3_prod_tonnes_and_H2_production_tonnes =yearly_result['final_NH3_prod_[tonnes]']
        new_data = yearly_result[['P_solar', 'P_wind','final_solar_curtailment', 'final_wind_curtailment', 'direct_solar', 'direct_wind', 'BESS_discharge','final_H2_prod_[tonnes]', 'final_NH3_prod_[tonnes]', 'NH3_cf_[%]', 'H2_cf_[%]']].copy()
        new_data.to_excel(f"{outdir}/NH3_prod_{int(HB_capacity + electrolyser_capacity)}MW_Plant capacity.xlsx")


        data.index = pd.to_datetime(data.index)
        
        
        # Make timestamps unique per project year (otherwise every year bins into 2023)
        if "timestamp" in data.columns:
            data["timestamp"] = pd.to_datetime(data["timestamp"]) + pd.DateOffset(years=(project_year - 1))
            data = data.set_index("timestamp")
        else:# if index is already timestamp-like
            data.index = pd.to_datetime(data.index) + pd.DateOffset(years=(project_year - 1))
       
        # Resample to monthly result
        year_offset = int(project_year) - 1
        start_dt = pd.to_datetime(inputs.start_date, format="%m/%d/%Y") + pd.DateOffset(years=year_offset)
        data.index = pd.date_range(start=start_dt, periods=len(data), freq="H")
        print("Year", project_year, "timestamp range:", data.index.min(), "→", data.index.max(), flush=True)
        monthly_data = data.apply(pd.to_numeric).resample ('M').sum() #originally 1ME instead of M
        monthly_data_resampled = monthly_data[['P_solar', 'P_wind','final_solar_curtailment', 'final_wind_curtailment', 'direct_solar', 'direct_wind', 'BESS_discharge', 'grid_import','final_H2_prod_[tonnes]', 'final_NH3_prod_[tonnes]']].copy()

        # #create a monthly dataframe
        print(monthly_data_resampled)
        monthly_data_resampled.to_csv(f"{outdir}/monthly_results_{project_year}.csv")

        # PLOTTING DISABLED - Uncomment below to enable yearly plots
        # data_to_plot = data[['P_wind','P_solar', 'RES','final_H2_prod_[MWh]', 'final_NH3_prod_[MWh]', 'grid_import', 'H2_SOC_[%]', 'SOC_[%]']].copy()
        # fig, ax = plt.subplots()
        # ax.plot(data_to_plot['P_wind'], 'c', label='P_wind')
        # ax.plot(data_to_plot['P_solar'], 'orange', label='P_solar')
        # ax.plot(data_to_plot['final_H2_prod_[MWh]'], 'm',label='final H2 prod [MWh]')
        # ax.plot(data_to_plot['final_NH3_prod_[MWh]'], 'g',label='final NH3 prod [MWh]')
        # ax.plot(data_to_plot['grid_import'], 'g',label='grid import [MWh]')
        # #ax.plot(data_to_plot[ 'grid_import'], 'b' ,label= 'grid import')
        # ax.set_ylabel('MWh')
        # ax.set_xlabel('Timestamp')
        # ax.plot(data_to_plot['RES'], label='RES')
        # ax2 = ax.twinx()
        # ax2.plot(data_to_plot['H2_SOC_[%]'], 'r', label= 'H2_SOC_[%]')
        # ax2.plot(data_to_plot['SOC_[%]'], 'brown', label='BESS_SOC[%]')
        # ax2.set_ylabel('SOC[%]')
        # #ax2.plot(data_to_plot['SOC_[%]'], 'brown', label='BESS_SOC[%]')
        # ax2.set_ylabel('SOC[%]')

        # # # Add legend
        # fig.legend()

        # # # Add title
        # plt.title('Energy Dispatch and H2/NH3 Production')

        # # # add grid to the plot
        # ax.grid()
        # plt.show()

    # writer.save()
    print("The program took", timer() - start, "to run")
    return result


def calculate_scaled_cost(required_capacity, ref_capacity, ref_cost, scaling_exponent):
    """
    Calculate equipment cost using economies of scale.
    
    Parameters:
    -----------
    required_capacity : float
        The capacity you need (in same units as ref_capacity)
    ref_capacity : float
        Reference capacity with known cost
    ref_cost : float
        Known cost at reference capacity
    scaling_exponent : float
        Scaling factor (typically 0.6-0.9)
        - Lower values = stronger economies of scale
        - 1.0 = linear scaling (no economies of scale)
    
    Returns:
    --------
    scaled_cost : float
        Estimated cost for required capacity
    
    Example:
    --------
    >>> calculate_scaled_cost(400, 100, 66.3, 0.75)
    186.87  # Cost in same units as ref_cost (e.g., M$)
    """
    if required_capacity <= 0 or ref_capacity <= 0:
        raise ValueError("Capacities must be positive")
    
    scaling_ratio = required_capacity / ref_capacity
    scaled_cost = ref_cost * (scaling_ratio ** scaling_exponent)
    
    return scaled_cost
