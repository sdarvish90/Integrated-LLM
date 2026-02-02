# cases.py
import pandas as pd

from inputs import kWh_to_kg_NH3,PV_capacity_ref_AC, pv_opex, wind_capacity_ref, wind_variable_opex, wind_interpolation,wind_variable_capex, PV_capacity_ref_AC, PV_DC_AC, PV_variable_capex,PV_variable_opex, BESS_capacity_MW, BESS_duration,BESS_energy_MWh, BESS_energy_capex, BESS_power_capex, BESS_opex, Electrolyzer_capacity_MW, elec_capex_epc, elec_opex_epc, init_kWh_to_kg_H2,H2_t_to_NH3_t,H2_BOP_capex,Ratio_NH3_Plant_capacity,HB_capacity_MW,NH3_density, grid_capex, grid_opex, npv_factor, project_overhead, H2_BOP_capex, H2_BOP_opex, H2_storage_capacity
from inputs import NH3_storage_capacity_m3, H2_storage_capex, H2_storage_opex, HB_capacity_tonne, HB_capex_epc, HB_opex_epc, NH3_storage_capex, NH3_storage_opex, NH3_storage_area, NH3_storage_fixed_opex,process_plant_area, H2_NH3_Production_fixed_opex, H2_compression_capex, H2_compression_opex, NH3_storage_opex


# ---- 1) Define your cases here ---------------------------------------------
def calculate_scaled_cost(required_capacity, ref_capacity, ref_cost, scaling_exponent):
    """
    Calculate equipment cost using economies of scale.
    
    Formula: Scaled_Cost = Reference_Cost × (Required_Size / Reference_Size) ^ Scaling_Exponent
    
    Parameters:
    -----------
    required_capacity : float
        Your plant's capacity (in same units as ref_capacity)
    ref_capacity : float
        Reference plant capacity with known cost
    ref_cost : float
        Known cost at reference capacity
    scaling_exponent : float
        Scaling factor (0.6-0.9):
        - 0.6 = strong economies of scale
        - 1.0 = linear (no economies of scale)
    
    Returns:
    --------
    scaled_cost : float
        Estimated cost for your capacity
    """
    if required_capacity <= 0 or ref_capacity <= 0:
        return ref_cost
    
    scaling_ratio = required_capacity / ref_capacity
    scaled_cost = ref_cost * (scaling_ratio ** scaling_exponent)
    
    return scaled_cost

# Scaling exponents from literature (paper values)
SCALING_EXPONENTS = {
    'electrolyzer': 0.75,       # AEL/PEMEL stacks
    'HB_reactor': 0.70,         # Haber-Bosch reactor
    'air_separation': 0.65,     # N2 production (ASU)
    'compressors': 0.67,        # H2 compressors
    'H2_BOP': 0.75,             # Balance of plant
    'pv_bop': 0.85,             # PV balance of plant (modular, less scaling)
    'wind': 0.90,               # Wind (modular)
    'storage': 0.80,            # Storage tanks
}

# Reference sizes and costs (adjust these to match your baseline)
# These should represent a "typical" or "literature reference" plant
REFERENCE_COSTS = {
    'electrolyzer': {
        'capacity_MW': 100,              # 100 MW reference
        'cost_per_MW_M$': 66.3 / 100,   # $663/kW = $0.663M/MW
    },
    'HB_reactor': {
        'capacity_tonne_per_day': 300,   # 300 t/d NH3 reference
        'cost_per_tpd_k$': 150,          # Adjust based on your data
    },
    'H2_compressor': {
        'capacity_t_per_h': 10,          # 10 tonnes/hour reference
        'cost_per_tph_k$': 500,          # Adjust based on your data
    }
}

CASES = [
    {
        "Case": 1,
        "PV_capacity_MW": 400,
        "PV_capacity_augmentation_year": 0.0, #WHEN additional PV capacity is added (the year trigger)
        "PV_capacity_augmenatation_per": 0.0, #HOW MUCH PV capacity increases at that year (percentage growth)
        "Solar_PPA": 0.0, #Doqm
        "Wind_PPA": 0.0, #Doqm
        "Wind_MW": 0,
        "Wake_loss_factor": 1,
        "BESS_Power_MW": 0,
        "BESS_Duration": 0,
        "BESS_energy_MWh": 0,
        "H2_Storage_capacity_tonne": 22,
        "Electrolyzer_capacity_MW": 400,
        "H2_compressor_flowrate_t_per_h": Electrolyzer_capacity_MW/init_kWh_to_kg_H2,
        "Ratio_NH3_Plant_capacity": 1.0,
        "HB_capacity_MW": HB_capacity_MW,
        "HB_capacity_tonne": HB_capacity_MW/kWh_to_kg_NH3,   # fill if you use it
        "Total_plant_capacity_MW": HB_capacity_MW+Electrolyzer_capacity_MW,
        "Grid_import_capacity_MW": 0, #HB_capacity_MW+Electrolyzer_capacity_MW,
        "Grid_export_capacity_MW": 0, #40.0
        "NH3_storage_capacity_m3": NH3_storage_capacity_m3,         #20% storage safety margin* 24 h/day/26 storage days  # set to your value

        # CAPEX/OPEX in kUSD from the excel row
        "Grid_CAPEX_kUSD": grid_capex/1000, #1000 for kUSD
        "Grid_OPEX_kUSD_per_year": grid_opex*grid_capex/1000, #1000 for kUSD
        "Wind_CAPEX_kUSD": wind_capacity_ref*wind_variable_capex*npv_factor*(1+project_overhead), #kW/kUSD
        "Wind_OPEX_kUSD_per_year": wind_variable_opex* wind_capacity_ref,
        "PV_CAPEX_kUSD": PV_capacity_ref_AC*PV_DC_AC*PV_variable_capex*npv_factor*(1+project_overhead), #kW/kUSD
        "PV_OPEX_kUSD_per_year": PV_capacity_ref_AC*pv_opex*PV_DC_AC ,
        "BESS_CAPEX_kUSD": ((BESS_capacity_MW * BESS_power_capex)+(BESS_capacity_MW * BESS_duration * BESS_energy_capex))*npv_factor* (1+project_overhead),
        "BESS_OPEX_kUSD_per_year": BESS_opex * BESS_capacity_MW,
        # "Electrolyser_CAPEX_kUSD": (Electrolyzer_capacity_MW * elec_capex_epc* npv_factor)* (1+project_overhead) ,
        # "Electrolyser_OPEX_kUSD_per_year": elec_opex_epc*(Electrolyzer_capacity_MW * elec_capex_epc),
        # "H2_BOP_CAPEX_kUSD": (Electrolyzer_capacity_MW* H2_BOP_capex * npv_factor) * (1+project_overhead),
        # "H2_BOP_OPEX_kUSD_per_year": H2_BOP_opex * (Electrolyzer_capacity_MW * H2_BOP_capex),
        # "H2_Storage_CAPEX_kUSD": (H2_storage_capacity* H2_storage_capex*npv_factor)*(1+project_overhead),
        # "H2_Storage_OPEX_kUSD_per_year": H2_storage_opex* H2_storage_capacity* H2_storage_capex,
        # "HB_CAPEX_kUSD": HB_capacity_tonne *8760* HB_capex_epc * npv_factor/1000 *(1+project_overhead),  #1000 for kUSD
        # "HB_OPEX_kUSD_per_year": HB_opex_epc* HB_capacity_tonne *8760* HB_capex_epc * npv_factor/1000 *(1+project_overhead),  #1000 for kUSD
        # "NH3_Storage_CAPEX_kUSD": NH3_storage_capacity_m3*NH3_storage_capex*npv_factor/1000,  #1000 for kUSD
        # "NH3_Storage_OPEX_kUSD": NH3_storage_opex* (NH3_storage_capacity_m3*NH3_storage_capex*npv_factor/1000),
        # "NH3_Storage_fixed_OPEX_kUSD": HB_capacity_tonne*8760* NH3_storage_area*NH3_storage_fixed_opex/1000,#need to check if it needs *8760
        # "Process_plant_fixed_OPEX_kUSD": (HB_capacity_MW+Electrolyzer_capacity_MW)*process_plant_area*H2_NH3_Production_fixed_opex/1000,
        # "H2_compressor_CAPEX_kUSD": (Electrolyzer_capacity_MW/init_kWh_to_kg_H2)*H2_compression_capex*npv_factor/1000,
        # "H2_compressor_OPEX_kUSD":((Electrolyzer_capacity_MW/init_kWh_to_kg_H2)*H2_compression_capex*npv_factor/1000)* H2_compression_opex,
                # ====================================================================
        # CAPEX CALCULATIONS WITH ECONOMIES OF SCALE
        # ====================================================================
        
        # --- Grid (no scaling - fixed infrastructure) ---
        "Grid_CAPEX_kUSD": grid_capex/1000,
        "Grid_OPEX_kUSD_per_year": grid_opex*grid_capex/1000,
        
        # --- Wind (minimal scaling - modular) ---
        "Wind_CAPEX_kUSD": wind_capacity_ref * wind_variable_capex * npv_factor * (1+project_overhead) * \
                           calculate_scaled_cost(wind_capacity_ref, 100, 1.0, SCALING_EXPONENTS['wind']),
        "Wind_OPEX_kUSD_per_year": wind_variable_opex * wind_capacity_ref,
        
        # --- PV Solar (minimal scaling - modular, but BOP has scaling) ---
        # Modules: nearly linear (buy in bulk)
        "PV_modules_CAPEX_kUSD": PV_capacity_ref_AC * PV_DC_AC * PV_variable_capex * 0.70 * npv_factor * (1+project_overhead),
        
        # BOP: has economies of scale
        "PV_BOP_CAPEX_kUSD": (PV_capacity_ref_AC * PV_DC_AC * PV_variable_capex * 0.30 * npv_factor * (1+project_overhead)) * \
                             calculate_scaled_cost(PV_capacity_ref_AC, 100, 1.0, SCALING_EXPONENTS['pv_bop']),
        
        "PV_CAPEX_kUSD": None,  # Will calculate after defining both
        "PV_OPEX_kUSD_per_year": PV_capacity_ref_AC * pv_opex * PV_DC_AC,
        
        # --- BESS (has scaling) ---
        "BESS_CAPEX_kUSD": ((BESS_capacity_MW * BESS_power_capex) + 
                           (BESS_capacity_MW * BESS_duration * BESS_energy_capex)) * \
                           npv_factor * (1+project_overhead) * \
                           calculate_scaled_cost(BESS_capacity_MW, 10, 1.0, SCALING_EXPONENTS['storage']),
        "BESS_OPEX_kUSD_per_year": BESS_opex * BESS_capacity_MW,
        
        # --- Electrolyzer (STRONG economies of scale) ---
        # Base cost at reference size
        "elec_base_cost_M$": REFERENCE_COSTS['electrolyzer']['cost_per_MW_M$'] * \
                             REFERENCE_COSTS['electrolyzer']['capacity_MW'],
        
        # Scaled cost for your size
        "elec_scaled_cost_M$": calculate_scaled_cost(
            required_capacity=Electrolyzer_capacity_MW,
            ref_capacity=REFERENCE_COSTS['electrolyzer']['capacity_MW'],
            ref_cost=REFERENCE_COSTS['electrolyzer']['cost_per_MW_M$'] * REFERENCE_COSTS['electrolyzer']['capacity_MW'],
            scaling_exponent=SCALING_EXPONENTS['electrolyzer']
        ),
        
        "Electrolyser_CAPEX_kUSD": None,  # Will calculate below
        "Electrolyser_OPEX_kUSD_per_year": elec_opex_epc * (Electrolyzer_capacity_MW * elec_capex_epc),
        
        # --- H2 BOP (Balance of Plant - has scaling) ---
        "H2_BOP_CAPEX_kUSD": (Electrolyzer_capacity_MW * H2_BOP_capex * npv_factor) * \
                             (1+project_overhead) * \
                             calculate_scaled_cost(Electrolyzer_capacity_MW, 100, 1.0, SCALING_EXPONENTS['H2_BOP']),
        "H2_BOP_OPEX_kUSD_per_year": H2_BOP_opex * (Electrolyzer_capacity_MW * H2_BOP_capex),
        
        # --- H2 Storage (has scaling) ---
        "H2_Storage_CAPEX_kUSD": (H2_storage_capacity * H2_storage_capex * npv_factor) * \
                                 (1+project_overhead) * \
                                 calculate_scaled_cost(H2_storage_capacity, 10, 1.0, SCALING_EXPONENTS['storage']),
        "H2_Storage_OPEX_kUSD_per_year": H2_storage_opex * H2_storage_capacity * H2_storage_capex,
        
        # --- Haber-Bosch Plant (STRONG economies of scale) ---
        "HB_CAPEX_kUSD": (HB_capacity_tonne * 8760 * HB_capex_epc * npv_factor / 1000 * (1+project_overhead)) * \
                         calculate_scaled_cost(
                             required_capacity=HB_capacity_tonne * 24,  # Convert to tonnes/day
                             ref_capacity=REFERENCE_COSTS['HB_reactor']['capacity_tonne_per_day'],
                             ref_cost=1.0,
                             scaling_exponent=SCALING_EXPONENTS['HB_reactor']
                         ),
        "HB_OPEX_kUSD_per_year": HB_opex_epc * HB_capacity_tonne * 8760 * HB_capex_epc * npv_factor / 1000 * (1+project_overhead),
        
        # --- NH3 Storage (has scaling) ---
        "NH3_Storage_CAPEX_kUSD": (NH3_storage_capacity_m3 * NH3_storage_capex * npv_factor / 1000) * \
                                  calculate_scaled_cost(NH3_storage_capacity_m3, 1000, 1.0, SCALING_EXPONENTS['storage']),
        "NH3_Storage_OPEX_kUSD": NH3_storage_opex * (NH3_storage_capacity_m3 * NH3_storage_capex * npv_factor / 1000),
        "NH3_Storage_fixed_OPEX_kUSD": HB_capacity_tonne * 8760 * NH3_storage_area * NH3_storage_fixed_opex / 1000,
        
        # --- Process Plant Fixed OPEX ---
        "Process_plant_fixed_OPEX_kUSD": (HB_capacity_MW + Electrolyzer_capacity_MW) * process_plant_area * H2_NH3_Production_fixed_opex / 1000,
        
        # --- H2 Compressor (has scaling) ---
        "H2_compressor_CAPEX_kUSD": ((Electrolyzer_capacity_MW / init_kWh_to_kg_H2) * H2_compression_capex * npv_factor / 1000) * \
                                    calculate_scaled_cost(
                                        required_capacity=Electrolyzer_capacity_MW / init_kWh_to_kg_H2,
                                        ref_capacity=REFERENCE_COSTS['H2_compressor']['capacity_t_per_h'],
                                        ref_cost=1.0,
                                        scaling_exponent=SCALING_EXPONENTS['compressors']
                                    ),
        "H2_compressor_OPEX_kUSD": ((Electrolyzer_capacity_MW / init_kWh_to_kg_H2) * H2_compression_capex * npv_factor / 1000) * H2_compression_opex,

        "Total capex": None,  # Will be calculated in post-processing
    }
    # 👉 In the future: add more dicts here:
    # {
    #   "Case": 2,
    #   "PV_capacity_MW": ...,
    #   ...
    # }
]  # ← CLOSE THE CASES LIST HERE

# ============================================================================
# POST-PROCESSING: Calculate combined fields
# ============================================================================
# Do this after defining the case to avoid forward references

for case in CASES:
    # Calculate PV total (modules + BOP)
    case["PV_CAPEX_kUSD"] = case["PV_modules_CAPEX_kUSD"] + case["PV_BOP_CAPEX_kUSD"]
    
    # Calculate Electrolyzer total (convert from M$ to kUSD)
    case["Electrolyser_CAPEX_kUSD"] = case["elec_scaled_cost_M$"] * 1000 * npv_factor * (1 + project_overhead)
    
    # Calculate Total CAPEX
    case["Total capex"] = (
        case["Grid_CAPEX_kUSD"] +
        case["Wind_CAPEX_kUSD"] +
        case["PV_CAPEX_kUSD"] +
        case["BESS_CAPEX_kUSD"] +
        case["Electrolyser_CAPEX_kUSD"] +
        case["H2_BOP_CAPEX_kUSD"] +
        case["H2_Storage_CAPEX_kUSD"] +
        case["HB_CAPEX_kUSD"] +
        case["NH3_Storage_CAPEX_kUSD"] +
        case["H2_compressor_CAPEX_kUSD"]
    )
    
    # Print scaling comparison for debugging
    print(f"\n=== CASE {case['Case']} - ECONOMIES OF SCALE ANALYSIS ===")
    print(f"Electrolyzer: {case['Electrolyzer_capacity_MW']} MW")
    print(f"  - Base cost (100 MW ref): ${REFERENCE_COSTS['electrolyzer']['cost_per_MW_M$'] * case['Electrolyzer_capacity_MW']:.1f}M (linear)")
    print(f"  - Scaled cost (exp=0.75): ${case['elec_scaled_cost_M$']:.1f}M")
    print(f"  - Savings: ${(REFERENCE_COSTS['electrolyzer']['cost_per_MW_M$'] * case['Electrolyzer_capacity_MW'] - case['elec_scaled_cost_M$']):.1f}M ({((1 - case['elec_scaled_cost_M$']/(REFERENCE_COSTS['electrolyzer']['cost_per_MW_M$'] * case['Electrolyzer_capacity_MW'])) * 100):.1f}%)")
    print(f"\nHB Plant: {case['HB_capacity_tonne'] * 24:.0f} t/day NH3")
    print(f"Total CAPEX: ${case['Total capex']/1000:.1f}M")

print(f"\nGrid Import Capacity: {CASES[0]['Grid_import_capacity_MW']} MW")
print(f"Total CAPEX (with scaling): ${CASES[0]['Total capex']/1000:.1f}M")
# ---- 2) Helpers to build the DataFrame used by main ------------------------


def _add_scaling_factors(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add PV_scaling_factor and wind_scaling_factor columns using
    reference capacities from inputs.py.
    """
    if "PV_capacity_MW" in df.columns:
        df["PV_scaling_factor"] = df["PV_capacity_MW"] / PV_capacity_ref_AC

    if "Wind_MW" in df.columns:
        if wind_interpolation == "linear scaling":
            df["wind_scaling_factor"] = df["Wind_MW"] / wind_capacity_ref
        else:
            df["wind_scaling_factor"] = 1.0

    return df


def get_cases() -> pd.DataFrame:
    """
    Public function used by SHARE_Model_main_v1.
    Returns a DataFrame similar to the old Excel 'Cases' sheet,
    but built purely from Python data.
    """
    df = pd.DataFrame(CASES)
    df = _add_scaling_factors(df)
    return df
