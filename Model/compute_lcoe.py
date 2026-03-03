"""
DecarbIQ Model — NGCC & NGCC+CCS LCOE Computation
===================================================

Computes Levelized Cost of Energy using PySAM.Lcoefcr with the proper
Fixed Charge Rate (FCR) method from NREL SAM:

    FCR = CRF * PFF * CFF
    LCOE = (CAPEX * FCR + FOM) / (CF * 8760) * 1000 + VOM + (HR * FuelPrice)

Where:
    CRF = Capital Recovery Factor (from WACC and project life)
    PFF = Project Financing Factor (accounts for MACRS tax depreciation benefit)
    CFF = Construction Financing Factor (interest during construction)

This replaces the extractor's simplified CRF-only formula.
"""

import logging
from typing import Optional

import PySAM.Lcoefcr as lcoefcr

from assumptions import MACRS_SCHEDULES, NGCC_ASSUMPTIONS, NGCC_CCS_ASSUMPTIONS

logger = logging.getLogger("decarbiq.model.lcoe")


# ---------------------------------------------------------------------------
# FCR components
# ---------------------------------------------------------------------------

def compute_crf(wacc: float, n: int) -> float:
    """
    Capital Recovery Factor.

    CRF = WACC / (1 - (1+WACC)^(-n))

    Args:
        wacc: Real weighted average cost of capital (fraction)
        n: Economic life in years

    Returns:
        CRF as fraction
    """
    if wacc <= 0:
        return 1.0 / n
    factor = (1 + wacc) ** n
    return wacc * factor / (factor - 1)


def compute_pff(
    wacc: float,
    federal_tax: float,
    state_tax: float,
    macrs_years: int,
) -> float:
    """
    Project Financing Factor — accounts for MACRS tax depreciation benefit.

    PFF = (1 - T * PV_depreciation) / (1 - T)

    Where:
        T = combined tax rate = federal + state * (1 - federal)
        PV_depreciation = SUM_t(MACRS_fraction[t] / (1+WACC)^(t+1))

    Args:
        wacc: Real WACC (fraction)
        federal_tax: Federal corporate tax rate (fraction)
        state_tax: State corporate tax rate (fraction)
        macrs_years: MACRS depreciation schedule length (5, 7, 15, or 20)

    Returns:
        PFF as fraction (typically 1.0-1.05)
    """
    combined_tax = federal_tax + state_tax * (1 - federal_tax)
    schedule = MACRS_SCHEDULES.get(macrs_years)
    if schedule is None or combined_tax <= 0:
        return 1.0

    pv_depr = sum(
        frac / (1 + wacc) ** (t + 1)
        for t, frac in enumerate(schedule)
    )
    return (1 - combined_tax * pv_depr) / (1 - combined_tax)


def compute_cff(
    wacc: float,
    construction_months: int,
) -> float:
    """
    Construction Financing Factor — interest during construction.

    Assumes equal monthly spending over the construction period.
    Each monthly tranche earns interest from its spending date to COD.

    CFF = SUM over months of (1/n * (1+WACC)^(remaining_time_in_years))

    Args:
        wacc: Real WACC (fraction)
        construction_months: Construction duration in months

    Returns:
        CFF as fraction (typically 1.02-1.08)
    """
    if construction_months <= 0:
        return 1.0

    n = construction_months
    monthly_fraction = 1.0 / n
    total = 0.0
    for month in range(n):
        remaining_years = (n - month - 0.5) / 12.0
        total += monthly_fraction * (1 + wacc) ** remaining_years
    return total


def compute_fcr(
    wacc: float,
    economic_life: int,
    federal_tax: float,
    state_tax: float,
    macrs_years: int,
    construction_months: int,
) -> float:
    """
    Fixed Charge Rate — the complete NREL methodology.

    FCR = CRF * PFF * CFF

    Args:
        wacc: Real WACC (fraction)
        economic_life: Project life in years
        federal_tax: Federal corporate tax rate (fraction)
        state_tax: State corporate tax rate (fraction)
        macrs_years: MACRS depreciation schedule (5, 7, 15, or 20)
        construction_months: Construction duration in months

    Returns:
        FCR as fraction
    """
    crf = compute_crf(wacc, economic_life)
    pff = compute_pff(wacc, federal_tax, state_tax, macrs_years)
    cff = compute_cff(wacc, construction_months)
    fcr = crf * pff * cff
    logger.debug(
        f"FCR={fcr:.5f} (CRF={crf:.5f} * PFF={pff:.4f} * CFF={cff:.4f}) "
        f"WACC={wacc:.4f}, life={economic_life}yr, MACRS-{macrs_years}, "
        f"construction={construction_months}mo"
    )
    return fcr


# ---------------------------------------------------------------------------
# PySAM LCOE computation
# ---------------------------------------------------------------------------

def compute_lcoe_pysam(
    capex: float,
    fom: float,
    vom: float,
    heat_rate: float,
    fuel_price: float,
    capacity_factor: float,
    fcr: float,
    plant_size_kw: float = 1000.0,
) -> float:
    """
    Compute LCOE using PySAM.Lcoefcr.

    Maps our per-kW inputs to PySAM's absolute-dollar inputs,
    runs the solver, and converts back to $/MWh.

    Args:
        capex: Overnight capital cost ($/kW)
        fom: Fixed O&M ($/kW-yr)
        vom: Variable O&M ($/MWh)
        heat_rate: Heat rate (MMBtu/MWh)
        fuel_price: Fuel price ($/MMBtu)
        capacity_factor: Annual CF (fraction)
        fcr: Fixed charge rate (fraction, from compute_fcr)
        plant_size_kw: Reference plant size (kW) — cancels out

    Returns:
        LCOE in $/MWh
    """
    model = lcoefcr.new()
    model.SimpleLCOE.annual_energy = plant_size_kw * capacity_factor * 8760
    model.SimpleLCOE.capital_cost = capex * plant_size_kw
    model.SimpleLCOE.fixed_charge_rate = fcr
    model.SimpleLCOE.fixed_operating_cost = fom * plant_size_kw
    # VOM + fuel cost combined into variable operating cost
    model.SimpleLCOE.variable_operating_cost = (vom + heat_rate * fuel_price) / 1000.0
    model.execute()
    return model.Outputs.lcoe_fcr * 1000.0  # $/kWh → $/MWh


# ---------------------------------------------------------------------------
# Year-by-year series computation
# ---------------------------------------------------------------------------

def compute_ngcc_lcoe_series(
    outlook_vars: dict,
    helper_data: dict,
    assumptions: dict,
    fuel_price_series: dict,
) -> dict[str, float]:
    """
    Compute year-by-year NGCC LCOE from ATB components + EIA fuel prices.

    Args:
        outlook_vars: outlook["variables"] dict with ngcc_capex, ngcc_heat_rate
        helper_data: outlook["helper_data"] with _ngcc_fom, _ngcc_vom, _ngcc_wacc_real
        assumptions: NGCC_ASSUMPTIONS dict
        fuel_price_series: {year_str: $/MMBtu} from EIA AEO ng_price_electric_power

    Returns:
        {year_str: LCOE in $/MWh}
    """
    capex = outlook_vars.get("ngcc_capex", {}).get("annual", {})
    heat_rate = outlook_vars.get("ngcc_heat_rate", {}).get("annual", {})
    fom = helper_data.get("_ngcc_fom", {})
    vom = helper_data.get("_ngcc_vom", {})
    wacc_series = helper_data.get("_ngcc_wacc_real", {})

    cf = assumptions["capacity_factor"]
    life = assumptions["economic_life_years"]
    fed_tax = assumptions["federal_tax_rate"]
    state_tax = assumptions["state_tax_rate"]
    macrs = assumptions["macrs_schedule_years"]
    constr = assumptions["construction_duration_months"]
    default_wacc = assumptions["default_wacc_real"]

    result = {}
    for yr in sorted(capex.keys()):
        if yr not in heat_rate or yr not in fom or yr not in vom:
            continue
        if yr not in fuel_price_series:
            logger.warning(f"  ngcc_lcoe: no fuel price for {yr}, skipping")
            continue

        wacc = wacc_series.get(yr, default_wacc)
        fcr = compute_fcr(wacc, life, fed_tax, state_tax, macrs, constr)

        lcoe = compute_lcoe_pysam(
            capex=capex[yr],
            fom=fom[yr],
            vom=vom[yr],
            heat_rate=heat_rate[yr],
            fuel_price=fuel_price_series[yr],
            capacity_factor=cf,
            fcr=fcr,
        )
        result[yr] = round(lcoe, 2)

    logger.info(
        f"  ngcc_lcoe: computed {len(result)} years via PySAM "
        f"(CF={cf}, MACRS-{macrs}, construction={constr}mo)"
    )
    return result


def compute_ngcc_ccs_lcoe_series(
    outlook_vars: dict,
    helper_data: dict,
    assumptions: dict,
    fuel_price_series: dict,
) -> dict[str, float]:
    """
    Compute year-by-year NGCC+CCS LCOE from ATB components + EIA fuel prices.

    Args:
        outlook_vars: outlook["variables"] dict with ngcc_ccs_capex
        helper_data: outlook["helper_data"] with _ngcc_ccs_fom, _ngcc_ccs_vom,
                     _ngcc_ccs_heat_rate, _ngcc_wacc_real
        assumptions: NGCC_CCS_ASSUMPTIONS dict
        fuel_price_series: {year_str: $/MMBtu} from EIA AEO

    Returns:
        {year_str: LCOE in $/MWh}
    """
    capex = outlook_vars.get("ngcc_ccs_capex", {}).get("annual", {})
    heat_rate = helper_data.get("_ngcc_ccs_heat_rate", {})
    fom = helper_data.get("_ngcc_ccs_fom", {})
    vom = helper_data.get("_ngcc_ccs_vom", {})
    wacc_series = helper_data.get("_ngcc_wacc_real", {})

    cf = assumptions["capacity_factor"]
    life = assumptions["economic_life_years"]
    fed_tax = assumptions["federal_tax_rate"]
    state_tax = assumptions["state_tax_rate"]
    macrs = assumptions["macrs_schedule_years"]
    constr = assumptions["construction_duration_months"]
    default_wacc = assumptions["default_wacc_real"]

    result = {}
    for yr in sorted(capex.keys()):
        if yr not in heat_rate or yr not in fom or yr not in vom:
            continue
        if yr not in fuel_price_series:
            logger.warning(f"  ngcc_ccs_lcoe: no fuel price for {yr}, skipping")
            continue

        wacc = wacc_series.get(yr, default_wacc)
        fcr = compute_fcr(wacc, life, fed_tax, state_tax, macrs, constr)

        lcoe = compute_lcoe_pysam(
            capex=capex[yr],
            fom=fom[yr],
            vom=vom[yr],
            heat_rate=heat_rate[yr],
            fuel_price=fuel_price_series[yr],
            capacity_factor=cf,
            fcr=fcr,
        )
        result[yr] = round(lcoe, 2)

    logger.info(
        f"  ngcc_ccs_lcoe: computed {len(result)} years via PySAM "
        f"(CF={cf}, MACRS-{macrs}, CCS capture={assumptions.get('ccs_capture_rate', 'N/A')})"
    )
    return result


def compute_all_lcoe(
    outlook: dict,
    fuel_price_series: dict,
    ngcc_assumptions: Optional[dict] = None,
    ngcc_ccs_assumptions: Optional[dict] = None,
) -> dict[str, dict[str, float]]:
    """
    Compute both NGCC and NGCC+CCS LCOE series.

    Args:
        outlook: Full outlook dict (with "variables" and "helper_data")
        fuel_price_series: {year_str: $/MMBtu} from EIA AEO
        ngcc_assumptions: Override NGCC assumptions (default: NGCC_ASSUMPTIONS)
        ngcc_ccs_assumptions: Override CCS assumptions (default: NGCC_CCS_ASSUMPTIONS)

    Returns:
        {"ngcc_lcoe": {year: $/MWh}, "ngcc_ccs_lcoe": {year: $/MWh}}
    """
    if ngcc_assumptions is None:
        ngcc_assumptions = NGCC_ASSUMPTIONS
    if ngcc_ccs_assumptions is None:
        ngcc_ccs_assumptions = NGCC_CCS_ASSUMPTIONS

    vars_ = outlook.get("variables", {})
    helper = outlook.get("helper_data", {})

    result = {}

    if "ngcc_lcoe" in vars_:
        result["ngcc_lcoe"] = compute_ngcc_lcoe_series(
            vars_, helper, ngcc_assumptions, fuel_price_series
        )

    if "ngcc_ccs_lcoe" in vars_:
        result["ngcc_ccs_lcoe"] = compute_ngcc_ccs_lcoe_series(
            vars_, helper, ngcc_ccs_assumptions, fuel_price_series
        )

    return result
