import pandas as pd
import warnings
import os
import numpy as np
from RES_profiles import get_wind_profile, get_pv_profile
from datetime import datetime



### Read the inputs needed for the simulation ###

#Open the Excel file and catch the warning "DataValidation feature not supported"
# with warnings.catch_warnings():
#     warnings.filterwarnings("ignore", category=UserWarning)
#     technical_sheet = pd.read_excel("SHARE_model_input_sheet_v1.xlsx", sheet_name=0, usecols = ["General parameters", "Value"], index_col = 0)


debug_save_profiles = True


## General inputs ##
project_lifetime = 30 
hours_per_year = 8760

# Start & End date for date range
start_date = "01/01/2028" #start of operation
end_date = "01/01/2058"
freq = "1h"

forecast_window = 72 #hrs
NH3_window_user = 3 #hrs
dispatch_options = "Proportional"

ramp_rates_buffer = 0.7
end_of_year_SOC_target = 12

RES_years = project_lifetime   # or min(project_lifetime, something)

# Wind inputs
wind_profile = get_wind_profile(RES_years) #MW
wind_capacity_ref = 0 
wind_ava_f = 1
wind_ac_losses = 0.01
wind_upscaling_factor = 1.00
wind_interpolation = "cubic scaling"
wind_degradation_rate= 0.0055
wind_iav_sigma= 0.08
wind_seasonal_sigma = 0.05      # optional
wind_iav_seed = 42
wind_use_seasonal_iav = True
wind_write_debug_files = True    # writes DEBUG CSVs when importing / running

# Solar inputs
pv_profile = get_pv_profile(RES_years)  #MW #8760 × project_lifetime, no timestamps
PV_capacity_ref_AC =400 
PV_DC_AC = 1.15  # already defined above
PV_capacity_ref_DC = PV_capacity_ref_AC*PV_DC_AC
PV_ann_degr = 0.0055
PV_ava_f = 0.99
PV_ac_losses = 0.01
shading_losses = 0


# BESS inputs
BESS_tech = "Li_ion"
BESS_capacity_MW = 0 
BESS_duration = 0 
BESS_energy_MWh= BESS_capacity_MW*BESS_duration
BESS_min_cap = 0.9
BESS_cal_degr = 0.01
BESS_RTE = 0.85
init_SOC = 0
SOC_max = 1
BESS_DoD = 0.95
BESS_ac_losses = 0.02
BESS_cycles = 3000
BESS_degr = (SOC_max-BESS_min_cap)/BESS_cycles
BESS_aug_years = 2
BESS_avail = 0.99

# H2 Production
elec_tech = "Alkaline" #PEM
Electrolyzer_capacity_MW = 400 
elec_lifetime = 80000
init_kWh_to_kg_H2 = 52 #55
elect_degradation = 0.01
elect_min_SOH = 0.9
elect_aug_years = 2
electrolyser_avail = 0.875 #0.98
waste_water_cons= 2.2 #kWh/m3
waste_water_flowrate= 42 #m3/t
elec_feed_water= 8.82 #m3/t of H2
elec_cooling_water = 0 #m3/t of H2
HB_cooling_water = 5.88 #m3/t of NH3


# H2 Storage
H2_storage_consum = 0
H2_storage_capacity = 22 #tonne
H2_SOC_min = 0
H2_SOC_max = 1
H2_init_SOC = 0


# NH3 production
min_NH3 = 0.3
HB_lifetime = 80000
NH3_ramp_up_rate = 0.2
NH3_ramp_down_rate = 0.2
kWh_to_kg_NH3 = 0.7
H2_t_to_NH3_t = 0.182
start_range_of_no_NH3_prod = "01/01/2028"
HB_avail_factor = 0.96
NH3_density= 0.68 #t/m3
Ratio_NH3_Plant_capacity= 1.0 
HB_capacity_MW= kWh_to_kg_NH3/(init_kWh_to_kg_H2*H2_t_to_NH3_t)*Ratio_NH3_Plant_capacity*Electrolyzer_capacity_MW
print("HB_capacity_MW=", HB_capacity_MW)
HB_capacity_tonne= HB_capacity_MW/kWh_to_kg_NH3 #per hour
print("HB_capacity_tonne=", HB_capacity_tonne)
# Grid Losses
transmission_losses = 0.035
grid_profile = "Hourly"
grid_balancing_options = "Yearly"
balancing_period = 12
grid_capacity = 10**100
number_of_hours = 336
grid_import_usage = "None" #other option "HB", "All"

"####################################################################################################"

"####################################################################################################"


# Finacial inputs
# with warnings.catch_warnings():
#     warnings.filterwarnings("ignore", category=UserWarning)
#     financial_sheet = pd.read_excel("SHARE_model_input_sheet_v1.xlsx", sheet_name=1, usecols = ["General parameters", "Value"], index_col = 0)

# general





discount_rate = 0.085 #WACC for green H2 projects (Oman)
start_operation = datetime.strptime("01/01/2028", "%d/%m/%Y")
construction_start = datetime.strptime("01/07/2025", "%d/%m/%Y")
construction_period = (start_operation - construction_start).days
construction_period_years = construction_period/ 365.25
construction_period_months = construction_period/30.437
max_month= int(construction_period_months)
# print(max_month)
r_month = (1 + discount_rate)**(1/12) - 1

def generate_capex_months(step_months, max_month):
    months = list(range(0, max_month + 1, step_months))
    if months[-1] != max_month:
        months.append(max_month)
    return months
capex_months = generate_capex_months(step_months=9, max_month=max_month)

discount_factors = [
    (1 + r_month)**(max_month - m)
    for m in capex_months
]
print (capex_months)

project_overhead=0.092 
inflation=0.015 #CPI forecast 2026 (Oman)

# Grid
grid_capex = 0 #USD/KW #upfront CAPEX for the grid connection
grid_opex = 0.02 #annual OPEX for the grid connection
grid_indexation = 0.01 #how those grid OPEX costs escalate over time
grid_import_tariff = 0   # USD/MWh (added)
export_revenue = 0.0 # USD/MWh (added)


# PV and wind variable CAPEX & OPEX
wind_variable_capex = 1000 #USD/kW
# wind_fixed_opex_frac_start = 0.025   # 2.5% of CAPEX
# wind_fixed_opex_frac_mid= 0.032   # 3.2%
# wind_fixed_opex_frac_end= 0.040   # 4.0%
wind_warranty_years= 2
wind_midlife_year= 10
wind_variable_opex = 0.00057
wind_opex_real_escalation= 0.00    # e.g. 0.01 if you want +1%/yr above inflation

PV_variable_capex = 590 #USD/kWp #data sum of the table 4 paper
pv_opex= 27 #USD/kW/yr
# pv_fixed_opex_frac_start= 0.020
# pv_fixed_opex_frac_mid= 0.025
# pv_fixed_opex_frac_end= 0.030
pv_warranty_years= 2
pv_midlife_year= 12
PV_variable_opex = 0.0004 #usd/kWp
PV_augmentation_cost= 550 #USD/kWp
pv_opex_real_escalation= 0.00

# BESS
BESS_energy_capex = 200 #USD/kWh
BESS_power_capex = 325 #USD/kW
BESS_opex = 11 #usd/kW/yr
BESS_aug_cost = 0.2

# Electrolyser
elec_capex_epc = 663 #USD/kW
elec_opex_epc = 0.04
elec_aug_cost = 0.5

#H2 Compression
H2_compression_capex= 0 #1500000 #USD/h #30 to 60 bar
H2_compression_opex= 0.02

#Hydrogen_station_BOP
H2_BOP_capex= 0 #usd/kW
H2_BOP_opex= 0.0 #USD/MW/yr. #OPEX AS A PERCENTAGE OF CAPEX

#Hydrogen_storage
H2_storage_capex= 0 #500 #USD/kg
H2_storage_opex= 0.03 #USD/kg

# HB system
HB_capex_epc = 925 #USD/tonnes/yr
HB_opex_epc= 0.02
HB_aug_cost = 0.02*HB_capex_epc

#NH3 storage 
NH3_storage_capacity_m3 =  1.2* 24*26* (HB_capacity_tonne/NH3_density) 
print(NH3_storage_capacity_m3)
NH3_storage_capex = 0 #1000 #USD/m3
NH3_storage_opex= 0.03

# Water Costs
H2_water_variable_opex = 0 #5.29 #USD/year
NH3_water_variable_opex = 0 #3.53 #USD/year
water_costs = 7.06 #USD/tH2 (= 0.8 x 8.82 m3/tH2)
water_unit_cost_per_m3 = 0.8 #USD/m3 (desal, Oman)

# IRR related inputs paramerters 
green_NH3_selling_price = 650 #USD/t green NH3 (Middle East - Gulf (SA, AE, OM, QA) Export-Oriented)
grey_NH3_selling_price = 280 #USD/t grey NH3 (Middle East - Gulf (SA, AE, OM, QA) Export-Oriented)

green_H2_selling_price = 0 #USD/t green H2 (Middle East - Gulf (SA, AE, OM, QA) Export-Oriented)
grey_H2_selling_price = 0 #USD/t grey H2 (Middle East - Gulf (SA, AE, OM, QA) Export-Oriented)

# Final product: "NH3" (default), "H2", or "H2+NH3"
final_product = "NH3" 

taxes =  0.15 #combined corporate tax (Oman)
depreciation = 15 #years straight-line (Oman)

# Pipelines costs
H2_pipeline_capex = 0 * 10**6
H2_pipeline_opex = 0.02
H2O_pipeline_capex =0 * 10**6
H2O_pipeline_opex = 0.02

# HB consumption profile

HB_consumption_profile = pd.DataFrame({
    "Loading_[%]": [0,10,20,30,40,50,60,70,80,90,100],
    "HB_Consumption_[MWh/t]": [0.7,0.7,0.7,0.7,0.7,0.7,0.7,0.7,0.7,0.7,0.7],
})



#Capex deployment schedule


capex_shares = [0.04, 0.04, 0.30, 0.30, 0.32] #manual now but it has to change as the COD months change
from capex_deployments import CAPEX_DEPLOYMENT

# expose a helper instead of individual variables
def get_capex_deployment(year: int) -> float:
    """
    Returns the fraction of total CAPEX deployed in a given year.
    Defaults to 0.0 if the year is not defined.
    """
    return float(CAPEX_DEPLOYMENT.get(year, 0.0))
    # print("CAPEX_DEPLOYMENT loaded in inputs:", CAPEX_DEPLOYMENT, flush=True)

#land cost
NH3_storage_area= 0.19 #m2/tonnes/yr
process_plant_area= 478 #m2/MW
land_cost= 2.0 #USD/acre/yr (OM)
NH3_storage_fixed_opex= 0 #8 #USD/yr
H2_NH3_Production_fixed_opex=0 #2.8 #USD/yr

init_H2_ratio =(init_kWh_to_kg_H2*H2_t_to_NH3_t)/(init_kWh_to_kg_H2*H2_t_to_NH3_t+kWh_to_kg_NH3)

#NPV
npv_factor = sum(df * w for df, w in zip(discount_factors, capex_shares))
# print(npv_factor)

#line to connect pv to substation
line_capex = 0 #0.2 #MUSD/km
line_opex = 0 #0.002 #MUSD/km/yr
capex_stepup= 0 #0.05 #MUSD/MW
opex_stepup= 0 #0.0005 #MUSD/MW/yr


# Economies of scale parameters
SCALING_FACTORS = {
    'electrolyzer': 0.75,      # Typical for electrolyzers
    'HB_reactor': 0.70,        # Haber-Bosch reactor
    'air_separation': 0.65,    # N2 air separation unit
    'compressors': 0.67,       # Gas compressors
    'heat_exchangers': 0.68,   # Heat exchangers
    'power_electronics': 0.82, # Power conversion equipment
    'pv_modules': 0.90,        # Solar PV (less economies of scale)
    'balance_of_plant': 0.85   # BOP components
}

# Reference sizes and costs (from paper/literature)
REFERENCE_DATA = {
    'electrolyzer': {
        'ref_capacity_MW': 100,  # Reference: 100 MW electrolyzer
        'ref_cost_M$': 66.3,     # $663/kW × 100,000 kW
    },
    'HB_reactor': {
        'ref_capacity_tpd_NH3': 300,  # 300 tonnes/day NH3
        'ref_cost_M$': 50,            # Typical cost
    },
    'air_separation': {
        'ref_capacity_tpd_N2': 285,   # For 300 t/d NH3
        'ref_cost_M$': 25,            # Typical ASU cost
    }
}