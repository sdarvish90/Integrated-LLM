"""
DecarbIQ Model — Hydrogen LCOH Computation via ProFAST
=======================================================

Computes Levelized Cost of Hydrogen using ProFAST's GAAP-based
discounted cash flow model for three production pathways:

  1. Green H2 (PEM electrolysis) — CAPEX + electricity feedstock
  2. Gray H2 (SMR, no CCS) — CAPEX + natural gas feedstock
  3. Blue H2 (SMR + 95% CCS) — CAPEX + NG + electricity + CO2 T&S

Each year-point runs a separate ProFAST solve with that year's
technology costs and fuel prices (vintage cost methodology).

Also computes carbon intensity (kg CO2/kg H2) per pathway and
determines IRA 45V PTC eligibility.
"""

import logging
from typing import Optional

from ProFAST import ProFAST

from assumptions import (
    H2_BLUE_SMR_CCS,
    H2_CARBON_INTENSITY,
    H2_GRAY_SMR,
    H2_GREEN_PEM,
    IRA_45V_DURATION_YEARS,
    compute_blue_h2_ci,
    compute_green_h2_ci,
    get_45v_ptc,
)

logger = logging.getLogger("decarbiq.model.lcoh")


# ---------------------------------------------------------------------------
# Single-year ProFAST solvers
# ---------------------------------------------------------------------------

def _configure_base_profast(assumptions: dict, capacity_kg_per_day: float) -> ProFAST:
    """Create and configure a ProFAST instance with common financial params."""
    pf = ProFAST()
    pf.set_params("commodity", {
        "name": "Hydrogen",
        "unit": "kg",
        "initial price": 10.0,
        "escalation": 0,
    })
    pf.set_params("operating life", assumptions["operating_life_years"])
    pf.set_params("analysis start year", assumptions["analysis_start_year"])
    pf.set_params("installation months", assumptions["installation_months"])
    pf.set_params("long term utilization", assumptions["long_term_utilization"])
    pf.set_params("capacity", capacity_kg_per_day * 365)
    pf.set_params("demand rampup", 0)
    pf.set_params("total income tax rate", assumptions["total_income_tax_rate"])
    pf.set_params("debt equity ratio of initial financing", assumptions["debt_equity_ratio"])
    pf.set_params("debt interest rate", assumptions["debt_interest_rate"])
    pf.set_params("leverage after tax nominal discount rate",
                  assumptions["leverage_after_tax_discount_rate"])
    pf.set_params("general inflation rate", assumptions["general_inflation_rate"])
    return pf


def compute_lcoh_green_pem(
    electrolyzer_capex_per_kw: float,
    electrolyzer_efficiency: float,
    electricity_price_per_mwh: float,
    assumptions: dict,
    ptc_per_kg: float = 0.0,
) -> float:
    """
    Compute green H2 LCOH for a single year-point using ProFAST.

    Args:
        electrolyzer_capex_per_kw: Electrolyzer CAPEX ($/kW)
        electrolyzer_efficiency: Electricity consumption (kWh/kg H2)
        electricity_price_per_mwh: Electricity price ($/MWh)
        assumptions: H2_GREEN_PEM assumptions dict
        ptc_per_kg: 45V PTC ($/kg H2), 0 if not applicable

    Returns:
        LCOH in $/kg H2
    """
    capacity = assumptions["capacity_kg_per_day"]
    pf = _configure_base_profast(assumptions, capacity)

    # Electrolyzer sizing: kW = (kg/day * kWh/kg) / 24 hours
    kw_rating = capacity * electrolyzer_efficiency / 24.0
    total_capex = electrolyzer_capex_per_kw * kw_rating

    pf.add_capital_item(
        name="Electrolyzer System",
        cost=total_capex,
        depr_type="MACRS",
        depr_period=assumptions["macrs_depreciation_years"],
        refurb=[0],
    )

    # Feedstocks
    elec_price_per_kwh = electricity_price_per_mwh / 1000.0
    pf.add_feedstock(
        name="Electricity",
        usage=electrolyzer_efficiency,
        unit="kWh",
        cost=elec_price_per_kwh,
        escalation=0,
    )
    pf.add_feedstock(
        name="Water",
        usage=assumptions["water_usage_gal_per_kg"],
        unit="gallon",
        cost=assumptions["water_cost_per_gal"],
        escalation=0,
    )

    # 45V PTC (if applicable) — modeled as annual operating incentive
    if ptc_per_kg > 0:
        annual_capacity = capacity * 365 * assumptions["long_term_utilization"]
        pf.set_params("annual operating incentive", {
            "value": ptc_per_kg * annual_capacity,
            "decay": 0,
            "sunset years": IRA_45V_DURATION_YEARS,
            "taxable": False,
        })

    sol = pf.solve_price()
    return sol["lco"]


def compute_lcoh_gray_smr(
    ng_price_per_mmbtu: float,
    electricity_price_per_mwh: float,
    assumptions: dict,
) -> float:
    """
    Compute gray H2 LCOH (SMR, no CCS) for a single year-point.

    Args:
        ng_price_per_mmbtu: Natural gas price ($/MMBtu)
        electricity_price_per_mwh: Electricity price ($/MWh)
        assumptions: H2_GRAY_SMR assumptions dict

    Returns:
        LCOH in $/kg H2
    """
    capacity = assumptions["capacity_kg_per_day"]
    pf = _configure_base_profast(assumptions, capacity)

    # SMR CAPEX (mature technology, static)
    total_capex = assumptions["capex_per_kg_day"] * capacity
    pf.add_capital_item(
        name="SMR Plant",
        cost=total_capex,
        depr_type="MACRS",
        depr_period=assumptions["macrs_depreciation_years"],
        refurb=[0],
    )

    # Feedstocks
    pf.add_feedstock(
        name="Natural Gas",
        usage=assumptions["ng_usage_mmbtu_per_kg"],
        unit="MMBtu",
        cost=ng_price_per_mmbtu,
        escalation=0,
    )
    pf.add_feedstock(
        name="Electricity",
        usage=assumptions["electricity_usage_kwh_per_kg"],
        unit="kWh",
        cost=electricity_price_per_mwh / 1000.0,
        escalation=0,
    )
    pf.add_feedstock(
        name="Water",
        usage=assumptions["water_usage_gal_per_kg"],
        unit="gallon",
        cost=assumptions["water_cost_per_gal"],
        escalation=0,
    )

    sol = pf.solve_price()
    return sol["lco"]


def compute_lcoh_blue_smr_ccs(
    ng_price_per_mmbtu: float,
    electricity_price_per_mwh: float,
    assumptions: dict,
    ptc_per_kg: float = 0.0,
) -> float:
    """
    Compute blue H2 LCOH (SMR + 95% CCS) for a single year-point.

    Args:
        ng_price_per_mmbtu: Natural gas price ($/MMBtu)
        electricity_price_per_mwh: Electricity price ($/MWh)
        assumptions: H2_BLUE_SMR_CCS assumptions dict
        ptc_per_kg: 45V PTC ($/kg H2), 0 if not applicable

    Returns:
        LCOH in $/kg H2
    """
    capacity = assumptions["capacity_kg_per_day"]
    pf = _configure_base_profast(assumptions, capacity)

    # SMR+CCS CAPEX
    total_capex = assumptions["capex_per_kg_day"] * capacity
    pf.add_capital_item(
        name="SMR+CCS Plant",
        cost=total_capex,
        depr_type="MACRS",
        depr_period=assumptions["macrs_depreciation_years"],
        refurb=[0],
    )

    # Feedstocks
    pf.add_feedstock(
        name="Natural Gas",
        usage=assumptions["ng_usage_mmbtu_per_kg"],
        unit="MMBtu",
        cost=ng_price_per_mmbtu,
        escalation=0,
    )
    pf.add_feedstock(
        name="Electricity",
        usage=assumptions["electricity_usage_kwh_per_kg"],
        unit="kWh",
        cost=electricity_price_per_mwh / 1000.0,
        escalation=0,
    )
    pf.add_feedstock(
        name="Water",
        usage=assumptions["water_usage_gal_per_kg"],
        unit="gallon",
        cost=assumptions["water_cost_per_gal"],
        escalation=0,
    )

    # CO2 transport and storage cost as a feedstock
    co2_ts_per_kg_h2 = (
        assumptions["co2_captured_kg_per_kg_h2"]
        * assumptions["co2_transport_storage_per_tonne"]
        / 1000.0
    )
    pf.add_feedstock(
        name="CO2 Transport & Storage",
        usage=1.0,
        unit="kg H2",
        cost=co2_ts_per_kg_h2,
        escalation=0,
    )

    # 45V PTC (if applicable) — modeled as annual operating incentive
    if ptc_per_kg > 0:
        annual_capacity = capacity * 365 * assumptions["long_term_utilization"]
        pf.set_params("annual operating incentive", {
            "value": ptc_per_kg * annual_capacity,
            "decay": 0,
            "sunset years": IRA_45V_DURATION_YEARS,
            "taxable": False,
        })

    sol = pf.solve_price()
    return sol["lco"]


# ---------------------------------------------------------------------------
# Year-by-year series with carbon intensity
# ---------------------------------------------------------------------------

def compute_lcoh_series(
    outlook: dict,
    ng_price_series: dict,
    electricity_price_series: dict,
    grid_ci_series: Optional[dict] = None,
    policy_45v_series: Optional[dict] = None,
    enable_45v: bool = False,
    green_assumptions: Optional[dict] = None,
    gray_assumptions: Optional[dict] = None,
    blue_assumptions: Optional[dict] = None,
) -> dict:
    """
    Compute year-by-year LCOH for all three H2 pathways plus carbon intensity.

    Args:
        outlook: Full outlook dict (with "variables" for electrolyzer data)
        ng_price_series: {year_str: $/MMBtu} from EIA AEO
        electricity_price_series: {year_str: $/MWh} from EIA AEO
        grid_ci_series: {year_str: g CO2/kWh} from EIA AEO grid_emissions_intensity
        policy_45v_series: {year_str: $/kg} from EIA AEO ira_45v_h2_ptc (overrides tiers)
        enable_45v: Whether to apply 45V PTC (True if --with-45v or policy data present)
        green_assumptions: Override H2_GREEN_PEM
        gray_assumptions: Override H2_GRAY_SMR
        blue_assumptions: Override H2_BLUE_SMR_CCS

    Returns:
        {
            "lcoh_green_electrolysis": {year: $/kg},
            "lcoh_gray_smr": {year: $/kg},
            "lcoh_blue_smr_ccs": {year: $/kg},
            "ci_green": {year: kg CO2/kg H2},
            "ci_gray": {year: kg CO2/kg H2},
            "ci_blue": {year: kg CO2/kg H2},
            "lcoh_green_post_subsidy": {year: $/kg},  (only if 45V enabled)
            "lcoh_blue_post_subsidy": {year: $/kg},   (only if 45V enabled)
        }
    """
    if green_assumptions is None:
        green_assumptions = H2_GREEN_PEM
    if gray_assumptions is None:
        gray_assumptions = H2_GRAY_SMR
    if blue_assumptions is None:
        blue_assumptions = H2_BLUE_SMR_CCS

    vars_ = outlook.get("variables", {})

    # Electrolyzer inputs from extractor
    pem_capex = vars_.get("electrolyzer_pem_capex", {}).get("annual", {})
    elec_eff = vars_.get("electrolyzer_efficiency", {}).get("annual", {})

    # Blue H2 carbon intensity (constant)
    blue_ci_val = compute_blue_h2_ci(
        H2_CARBON_INTENSITY.get("blue_capture_rate", 0.95)
    )
    gray_ci_val = H2_CARBON_INTENSITY["gray_smr"]

    # Default grid CI if not provided
    default_grid_ci = 400.0  # g CO2/kWh, US average 2024

    # Determine year range from available data
    all_years = sorted(set(ng_price_series.keys()) & set(electricity_price_series.keys()))

    result = {
        "lcoh_green_electrolysis": {},
        "lcoh_gray_smr": {},
        "lcoh_blue_smr_ccs": {},
        "ci_green": {},
        "ci_gray": {},
        "ci_blue": {},
    }
    if enable_45v or policy_45v_series:
        result["lcoh_green_post_subsidy"] = {}
        result["lcoh_blue_post_subsidy"] = {}

    for yr in all_years:
        ng_price = ng_price_series[yr]
        elec_price = electricity_price_series[yr]

        # --- Green H2 ---
        if yr in pem_capex and yr in elec_eff:
            capex = pem_capex[yr]
            eff = elec_eff[yr]

            # Carbon intensity
            grid_ci = (grid_ci_series or {}).get(yr, default_grid_ci)
            ci_green = compute_green_h2_ci(eff, grid_ci)
            result["ci_green"][yr] = round(ci_green, 2)

            # Determine 45V PTC for green
            green_ptc = 0.0
            if enable_45v or policy_45v_series:
                if policy_45v_series and yr in policy_45v_series:
                    green_ptc = policy_45v_series[yr]
                elif enable_45v:
                    green_ptc = get_45v_ptc(ci_green)

            # Pre-subsidy LCOH
            lcoh_green = compute_lcoh_green_pem(
                capex, eff, elec_price, green_assumptions, ptc_per_kg=0.0
            )
            result["lcoh_green_electrolysis"][yr] = round(lcoh_green, 4)

            # Post-subsidy LCOH (if 45V enabled)
            if "lcoh_green_post_subsidy" in result and green_ptc > 0:
                lcoh_green_sub = compute_lcoh_green_pem(
                    capex, eff, elec_price, green_assumptions, ptc_per_kg=green_ptc
                )
                result["lcoh_green_post_subsidy"][yr] = round(lcoh_green_sub, 4)

        # --- Gray H2 ---
        lcoh_gray = compute_lcoh_gray_smr(ng_price, elec_price, gray_assumptions)
        result["lcoh_gray_smr"][yr] = round(lcoh_gray, 4)
        result["ci_gray"][yr] = gray_ci_val

        # --- Blue H2 ---
        blue_ptc = 0.0
        if enable_45v or policy_45v_series:
            if policy_45v_series and yr in policy_45v_series:
                blue_ptc = policy_45v_series[yr]
            elif enable_45v:
                blue_ptc = get_45v_ptc(blue_ci_val)

        lcoh_blue = compute_lcoh_blue_smr_ccs(
            ng_price, elec_price, blue_assumptions, ptc_per_kg=0.0
        )
        result["lcoh_blue_smr_ccs"][yr] = round(lcoh_blue, 4)
        result["ci_blue"][yr] = blue_ci_val

        if "lcoh_blue_post_subsidy" in result and blue_ptc > 0:
            lcoh_blue_sub = compute_lcoh_blue_smr_ccs(
                ng_price, elec_price, blue_assumptions, ptc_per_kg=blue_ptc
            )
            result["lcoh_blue_post_subsidy"][yr] = round(lcoh_blue_sub, 4)

    # Log summary
    for key in ["lcoh_green_electrolysis", "lcoh_gray_smr", "lcoh_blue_smr_ccs"]:
        series = result[key]
        if series:
            first_yr = min(series.keys())
            last_yr = max(series.keys())
            logger.info(
                f"  {key}: ProFAST computed {len(series)} years "
                f"(${series[first_yr]:.2f}→${series[last_yr]:.2f}/kg)"
            )

    return result
