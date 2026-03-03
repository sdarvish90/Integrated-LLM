"""
DecarbIQ Model — Central Assumptions Registry
==============================================

All financial and technical assumptions used by PySAM and ProFAST
computations, documented with source references.

Sources:
  - NREL ATB 2024 (https://atb.nrel.gov/electricity/2024)
  - ProFAST reference configs (https://github.com/NREL/ProFAST)
  - IRS MACRS depreciation schedules
  - IRA Section 45V hydrogen PTC tiers
  - GREET model lifecycle emission factors
"""

# =============================================================================
# MACRS Depreciation Schedules (IRS Publication 946)
# Fraction of capital depreciated each year (half-year convention)
# =============================================================================
MACRS_SCHEDULES = {
    5: [0.2000, 0.3200, 0.1920, 0.1152, 0.1152, 0.0576],
    7: [0.1429, 0.2449, 0.1749, 0.1249, 0.0893, 0.0892, 0.0893, 0.0446],
    15: [0.0500, 0.0950, 0.0855, 0.0770, 0.0693, 0.0623, 0.0590, 0.0590,
         0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0590, 0.0591, 0.0295],
    20: [0.0375, 0.0722, 0.0668, 0.0618, 0.0571, 0.0528, 0.0489, 0.0452,
         0.0447, 0.0447, 0.0446, 0.0446, 0.0446, 0.0446, 0.0446, 0.0446,
         0.0446, 0.0446, 0.0446, 0.0446, 0.0223],
}

# =============================================================================
# NGCC Financial Assumptions (Source: ATB 2024, EIA AEO assumptions)
# =============================================================================
NGCC_ASSUMPTIONS = {
    "capacity_factor": 0.55,            # Dispatchable baseload proxy
    "economic_life_years": 30,          # ATB default for gas CC
    "construction_duration_months": 36, # ATB construction schedule
    "federal_tax_rate": 0.21,           # US corporate rate
    "state_tax_rate": 0.06,             # Blended state average
    "macrs_schedule_years": 20,         # MACRS for gas power plants
    "use_atb_wacc": True,               # Pull WACC from ATB helper data
    "default_wacc_real": 0.0536,        # Fallback if not in helper data
}

NGCC_CCS_ASSUMPTIONS = {
    **NGCC_ASSUMPTIONS,
    "ccs_capture_rate": 0.95,           # 95% capture per ATB F-Frame 95% CCS
}

# =============================================================================
# Battery Storage Financial Assumptions (Source: ATB 2024)
# =============================================================================
BATTERY_ASSUMPTIONS = {
    "duration_hours": 4,                # 4-hr utility-scale Li-ion
    "cycles_per_year": 365,             # Daily full cycling
    "economic_life_years": 20,          # Battery project life
    "default_wacc_real": 0.0393,        # PV+Battery proxy from ATB
    "macrs_schedule_years": 7,          # Battery MACRS (7-yr per IRS)
    "federal_tax_rate": 0.21,
    "state_tax_rate": 0.06,
    "construction_duration_months": 12, # Shorter for battery
    "rte_start": 0.86,                 # 2024 RTE (Li-ion 4hr AC-coupled)
    "rte_end": 0.88,                   # 2050 RTE projection
    "rte_start_year": 2024,
    "rte_end_year": 2050,
}

# =============================================================================
# Hydrogen Financial Assumptions (Source: ProFAST reference configs, DOE)
# =============================================================================
H2_COMMON_FINANCIAL = {
    "analysis_start_year": 2024,
    "operating_life_years": 40,
    "installation_months": 36,
    "long_term_utilization": 0.97,      # 97% uptime
    "total_income_tax_rate": 0.2574,    # Federal 21% + state ~6%
    "debt_equity_ratio": 1.5,           # 60% debt / 40% equity
    "debt_interest_rate": 0.037,        # 3.7% nominal
    "leverage_after_tax_discount_rate": 0.08,  # 8% after-tax nominal
    "general_inflation_rate": 0.0,      # Real-dollar analysis (2024$)
}

H2_GREEN_PEM = {
    **H2_COMMON_FINANCIAL,
    "water_usage_gal_per_kg": 3.78,     # ProFAST PEM template
    "water_cost_per_gal": 0.005,        # US average industrial
    "macrs_depreciation_years": 20,
    "capacity_kg_per_day": 50000,       # Reference plant size
    # Electrolyzer CAPEX and efficiency come from extractor output
}

H2_GRAY_SMR = {
    **H2_COMMON_FINANCIAL,
    "ng_usage_mmbtu_per_kg": 0.156,     # ProFAST SMR template
    "electricity_usage_kwh_per_kg": 0.131,
    "water_usage_gal_per_kg": 4.344,
    "water_cost_per_gal": 0.005,
    "macrs_depreciation_years": 20,
    "capacity_kg_per_day": 50000,
    # SMR is mature technology — static CAPEX
    "capex_per_kg_day": 639.0,          # $/kg-day capacity (ProFAST ref)
}

H2_BLUE_SMR_CCS = {
    **H2_COMMON_FINANCIAL,
    "ng_usage_mmbtu_per_kg": 0.158,     # Slightly higher than gray (CCS parasitic)
    "electricity_usage_kwh_per_kg": 3.495,  # Much higher (CCS compression)
    "water_usage_gal_per_kg": 8.116,
    "water_cost_per_gal": 0.005,
    "macrs_depreciation_years": 20,
    "capacity_kg_per_day": 50000,
    "capex_per_kg_day": 1044.0,         # $/kg-day capacity (ProFAST SMR+CCS ref)
    "co2_transport_storage_per_tonne": 15.0,  # $/tonne CO2 T&S default
    "co2_captured_kg_per_kg_h2": 9.3 * 0.95,  # 95% capture of 9.3 kg CO2/kg H2
}

# =============================================================================
# Carbon Intensity by H2 Production Pathway (Source: GREET, DOE)
# Units: kg CO2e per kg H2 (lifecycle, well-to-gate)
# =============================================================================
H2_CARBON_INTENSITY = {
    # Green: depends on grid carbon intensity — computed dynamically
    "green_pem_grid_dependent": True,
    # Gray: unabated natural gas reforming
    "gray_smr": 9.3,                    # kg CO2/kg H2 (GREET)
    # Blue: SMR with CCS at different capture rates
    "blue_smr_ccs_90": 1.5,             # 90% capture
    "blue_smr_ccs_95": 0.9,             # 95% capture (ATB F-Frame 95% CCS)
    # Default capture rate for blue
    "blue_capture_rate": 0.95,
}

# =============================================================================
# IRA Section 45V Clean Hydrogen PTC Tiers
# Source: Inflation Reduction Act of 2022, as amended
# Effective for facilities placed in service after 2022, available for 10 years
# =============================================================================
IRA_45V_TIERS = [
    # (max_ci_kg_co2_per_kg_h2, ptc_per_kg_h2)
    (0.45,  3.00),   # Tier 1: cleanest, max credit
    (1.50,  1.00),   # Tier 2
    (2.50,  0.75),   # Tier 3
    (4.00,  0.60),   # Tier 4: least clean qualifying
]

IRA_45V_DURATION_YEARS = 10
IRA_45V_START_YEAR = 2023   # Facilities placed in service after 2022


def get_45v_ptc(carbon_intensity: float) -> float:
    """
    Return the 45V PTC $/kg H2 for a given carbon intensity.
    Returns 0.0 if CI exceeds all tier thresholds (>4.0 kg CO2/kg H2).
    """
    for max_ci, ptc in IRA_45V_TIERS:
        if carbon_intensity <= max_ci:
            return ptc
    return 0.0


def compute_green_h2_ci(
    efficiency_kwh_per_kg: float,
    grid_ci_g_per_kwh: float,
) -> float:
    """
    Compute green H2 carbon intensity from grid emissions intensity.

    Args:
        efficiency_kwh_per_kg: Electrolyzer electricity consumption (kWh/kg H2)
        grid_ci_g_per_kwh: Grid carbon intensity (g CO2/kWh)

    Returns:
        kg CO2 per kg H2
    """
    return efficiency_kwh_per_kg * grid_ci_g_per_kwh / 1000.0


def compute_blue_h2_ci(capture_rate: float = 0.95) -> float:
    """
    Compute blue H2 carbon intensity from capture rate.

    Args:
        capture_rate: Fraction of CO2 captured (0-1)

    Returns:
        kg CO2 per kg H2
    """
    unabated = H2_CARBON_INTENSITY["gray_smr"]
    return unabated * (1 - capture_rate)
