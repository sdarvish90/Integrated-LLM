"""
DecarbIQ Model — Battery LCOS & RTE Computation
=================================================

Computes Levelized Cost of Storage using PySAM.Lcoefcr with the proper
Fixed Charge Rate (FCR) method:

    LCOS = (CAPEX * FCR + FOM) / annual_energy_discharged + charging_cost / RTE

Where:
    annual_energy_discharged = cycles_per_year * duration_hours (kWh per kW nameplate)
    charging_cost = electricity_price ($/MWh, from EIA AEO)
    RTE = round-trip efficiency (linear interpolation 0.86→0.88)

This replaces the extractor's simplified CRF-only formula with the proper
FCR (CRF * PFF * CFF) accounting for MACRS-7 depreciation.
"""

import logging
from typing import Optional

import PySAM.Lcoefcr as lcoefcr

from assumptions import BATTERY_ASSUMPTIONS
from compute_lcoe import compute_fcr

logger = logging.getLogger("decarbiq.model.lcos")


# ---------------------------------------------------------------------------
# Battery RTE
# ---------------------------------------------------------------------------

def compute_battery_rte_series(
    start_year: int = 2024,
    end_year: int = 2050,
    rte_start: float = 0.86,
    rte_end: float = 0.88,
) -> dict[str, float]:
    """
    Compute year-by-year battery round-trip efficiency.

    Linear interpolation from rte_start (2024) to rte_end (2050).
    Clamped at endpoints.

    Returns:
        {year_str: RTE fraction}
    """
    span = end_year - start_year
    if span <= 0:
        span = 1
    result = {}
    for yr in range(start_year, end_year + 1):
        t = max(0, min(1, (yr - start_year) / span))
        rte = rte_start + t * (rte_end - rte_start)
        result[str(yr)] = round(rte, 4)
    return result


# ---------------------------------------------------------------------------
# PySAM LCOS computation
# ---------------------------------------------------------------------------

def compute_lcos_pysam(
    capex_power: float,
    fom: float,
    rte: float,
    electricity_price: float,
    fcr: float,
    cycles_per_year: int,
    duration_hours: int,
) -> float:
    """
    Compute battery LCOS for a single year-point using PySAM.Lcoefcr.

    Maps storage inputs to PySAM's LCOE framework:
        annual_energy = cycles * duration  (kWh per kW nameplate)
        capital_cost = capex_power (per kW, 1 kW reference)
        fixed_charge_rate = fcr
        fixed_operating_cost = fom (per kW-yr)
        variable_operating_cost = charging_cost / RTE ($/kWh discharged)

    Args:
        capex_power: Battery power CAPEX ($/kW)
        fom: Fixed O&M ($/kW-yr)
        rte: Round-trip efficiency (fraction)
        electricity_price: Charging electricity price ($/MWh)
        fcr: Fixed charge rate (fraction)
        cycles_per_year: Annual full discharge cycles
        duration_hours: Storage duration (hours)

    Returns:
        LCOS in $/MWh
    """
    annual_energy_kwh = cycles_per_year * duration_hours  # kWh per kW nameplate
    charging_cost_per_kwh = (electricity_price / rte) / 1000.0  # $/MWh → $/kWh

    model = lcoefcr.new()
    model.SimpleLCOE.annual_energy = annual_energy_kwh
    model.SimpleLCOE.capital_cost = capex_power  # 1 kW reference
    model.SimpleLCOE.fixed_charge_rate = fcr
    model.SimpleLCOE.fixed_operating_cost = fom
    model.SimpleLCOE.variable_operating_cost = charging_cost_per_kwh
    model.execute()
    return model.Outputs.lcoe_fcr * 1000.0  # $/kWh → $/MWh


# ---------------------------------------------------------------------------
# Year-by-year series
# ---------------------------------------------------------------------------

def compute_battery_lcos_series(
    outlook: dict,
    electricity_price_series: dict,
    assumptions: Optional[dict] = None,
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Compute year-by-year battery LCOS and RTE.

    Args:
        outlook: Full outlook dict (with "variables" and "helper_data")
        electricity_price_series: {year_str: $/MWh} from EIA AEO
        assumptions: Override BATTERY_ASSUMPTIONS (default: BATTERY_ASSUMPTIONS)

    Returns:
        (lcos_series, rte_series) both as {year_str: value}
    """
    if assumptions is None:
        assumptions = BATTERY_ASSUMPTIONS

    vars_ = outlook.get("variables", {})
    helper = outlook.get("helper_data", {})

    capex_power = vars_.get("battery_utility_capex_power", {}).get("annual", {})
    fom = helper.get("_battery_utility_fom", {})

    wacc = assumptions["default_wacc_real"]
    life = assumptions["economic_life_years"]
    fed_tax = assumptions["federal_tax_rate"]
    state_tax = assumptions["state_tax_rate"]
    macrs = assumptions["macrs_schedule_years"]
    constr = assumptions["construction_duration_months"]
    cycles = assumptions["cycles_per_year"]
    duration = assumptions["duration_hours"]

    # Compute FCR once (WACC is constant for battery)
    fcr = compute_fcr(wacc, life, fed_tax, state_tax, macrs, constr)

    # Compute RTE series
    rte_series = compute_battery_rte_series(
        start_year=assumptions["rte_start_year"],
        end_year=assumptions["rte_end_year"],
        rte_start=assumptions["rte_start"],
        rte_end=assumptions["rte_end"],
    )

    # Compute LCOS series
    lcos_series = {}
    for yr in sorted(capex_power.keys()):
        if yr not in fom:
            continue
        if yr not in electricity_price_series:
            logger.warning(f"  battery_lcos: no electricity price for {yr}, skipping")
            continue

        rte = rte_series.get(yr, assumptions["rte_start"])
        elec_price = electricity_price_series[yr]

        lcos = compute_lcos_pysam(
            capex_power=capex_power[yr],
            fom=fom[yr],
            rte=rte,
            electricity_price=elec_price,
            fcr=fcr,
            cycles_per_year=cycles,
            duration_hours=duration,
        )
        lcos_series[yr] = round(lcos, 2)

    logger.info(
        f"  battery_lcos: computed {len(lcos_series)} years via PySAM "
        f"(MACRS-{macrs}, life={life}yr, cycles={cycles}, dur={duration}h, "
        f"FCR={fcr:.4f})"
    )

    return lcos_series, rte_series
