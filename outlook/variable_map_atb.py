"""
VARIABLE_MAP_ATB — ATB Technology/Metric Mapping for NREL ATB 2024
===================================================================

Maps canonical VARIABLE_CATALOG keys to NREL ATB CSV column filters.
Built from actual ATB 2024 v3.0.0 CSV discovery output.

ATB 2024 CSV structure (LONG format — one row per data point):
  technology            — "UtilityPV", "NaturalGas_FE", etc.
  techdetail            — "Class5", "NG 2-on-1 Combined Cycle (F-Frame)", etc.
  core_metric_parameter — "CAPEX", "Fixed O&M", "LCOE", "CF", "Heat Rate", etc.
  core_metric_case      — "Market" or "R&D" (financial case)
  scenario              — "Conservative", "Moderate", "Advanced", or "*"
  core_metric_variable  — projection year (2022-2050)
  value                 — the data point
  units                 — unit string

Key findings from discovery:
  - Hydrogen/electrolyzer/SMR are NOT in the electricity ATB CSV.
    These come from Transportation ATB (LCOH) or DOE targets (CAPEX/efficiency).
  - Battery storage has no LCOE/LCOS metric — only CAPEX, Fixed O&M, OCC, GCC.
    LCOS is derived from components; RTE uses a static assumption.
  - NaturalGas has no LCOE — only CAPEX, Fixed O&M, Variable O&M, Heat Rate.
    LCOE is derived from components + assumed CF and fuel price.
  - Financial cases are "Market" (with IRA) and "R&D" (unsubsidized)

Variable flags:
  _helper: True      — internal variable extracted from CSV, stored in _helper_data
  _derived: str      — variable computed in _compute_derived() after direct extraction
  _not_in_csv: True  — not in ATB electricity CSV (hydrogen, missing metrics)
  _static_fallback   — dict of {year: value} for variables with no CSV source
"""

from typing import Any

# =============================================================================
# CONVERSION TABLE — ATB-specific unit conversions
# =============================================================================
ATB_CONVERSIONS = {
    "percent_to_fraction": lambda v: v / 100.0,
    "btu_kwh_to_mmbtu_mwh": lambda v: v / 1000.0,
    "none": lambda v: v,
}


# =============================================================================
# VARIABLE MAP — canonical key → ATB filter specification
#
# Each entry specifies how to find data in the ATB CSV (long format):
#   technology + techdetail + core_metric_parameter + scenario + core_metric_case
#   → filter rows, then pivot core_metric_variable (year) and value
#
# _technology_aliases / _techdetail_aliases / _metric_aliases:
#   Fallback values for resilience across ATB editions.
#
# _derived: variable computed from other populated variables
# _not_in_csv: True if this variable is known absent from electricity CSV
# =============================================================================
VARIABLE_MAP_ATB: dict[str, dict[str, Any]] = {

    # =========================================================================
    # SOLAR PV (6 variables) — all confirmed in CSV
    # =========================================================================
    "solar_pv_utility_capex": {
        "technology": "UtilityPV",
        "techdetail": "Class5",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "solar_pv_utility_fom": {
        "technology": "UtilityPV",
        "techdetail": "Class5",
        "metric": "Fixed O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "solar_pv_utility_lcoe": {
        "technology": "UtilityPV",
        "techdetail": "Class5",
        "metric": "LCOE",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "solar_pv_utility_cf": {
        "technology": "UtilityPV",
        "techdetail": "Class5",
        "metric": "CF",
        "unit_conversion": "none",  # CSV already in fraction (0-1)
        "dollar_year_adjust": False,
        "priority": 1,
    },
    "solar_pv_commercial_capex": {
        "technology": "CommPV",
        "techdetail": "Class5",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },
    "solar_pv_residential_capex": {
        "technology": "ResPV",
        "techdetail": "Class5",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 3,
    },

    # =========================================================================
    # WIND (7 variables) — all confirmed in CSV
    # =========================================================================
    "wind_onshore_capex": {
        "technology": "LandbasedWind",
        "techdetail": "Class4",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "wind_onshore_fom": {
        "technology": "LandbasedWind",
        "techdetail": "Class4",
        "metric": "Fixed O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "wind_onshore_lcoe": {
        "technology": "LandbasedWind",
        "techdetail": "Class4",
        "metric": "LCOE",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "wind_onshore_cf": {
        "technology": "LandbasedWind",
        "techdetail": "Class4",
        "metric": "CF",
        "unit_conversion": "none",  # CSV already in fraction (0-1)
        "dollar_year_adjust": False,
        "priority": 1,
    },
    "wind_offshore_fixed_capex": {
        "technology": "OffShoreWind",
        "techdetail": "Class3",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },
    "wind_offshore_fixed_lcoe": {
        "technology": "OffShoreWind",
        "techdetail": "Class3",
        "metric": "LCOE",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },
    "wind_offshore_floating_capex": {
        "technology": "OffShoreWind",
        "techdetail": "Class11",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 3,
    },

    # =========================================================================
    # BATTERY STORAGE (4 canonical + 1 helper)
    # ATB electricity CSV has CAPEX and Fixed O&M for battery.
    # LCOS is derived from components; RTE is a static assumption.
    # =========================================================================
    "battery_utility_capex_power": {
        "technology": "Utility-Scale Battery Storage",
        "techdetail": "4Hr Battery Storage",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "battery_utility_capex_energy": {
        "technology": "Utility-Scale Battery Storage",
        "techdetail": "4Hr Battery Storage",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
        "_derived": "capex_energy_from_power",  # capex_power / 4 hours
    },
    "battery_utility_lcos": {
        "technology": "Utility-Scale Battery Storage",
        "techdetail": "4Hr Battery Storage",
        "metric": "LCOS",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # inputs already adjusted
        "priority": 1,
        "_not_in_csv": True,
        "_derived": "battery_lcos_from_components",
    },
    "battery_utility_rte": {
        "technology": "Utility-Scale Battery Storage",
        "techdetail": "4Hr Battery Storage",
        "metric": "Round Trip Efficiency",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 2,
        "_not_in_csv": True,
        "_derived": "battery_rte_static_curve",
    },
    # --- helper: battery FOM (needed for LCOS derivation) ---
    "_battery_utility_fom": {
        "technology": "Utility-Scale Battery Storage",
        "techdetail": "4Hr Battery Storage",
        "metric": "Fixed O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
        "_helper": True,
    },

    # =========================================================================
    # NATURAL GAS POWER (6 canonical + 6 helpers)
    # Technology = "NaturalGas_FE" (not "NaturalGas")
    # LCOE is derived from CAPEX + FOM + VOM + HR + assumed CF + fuel price
    # =========================================================================
    "ngcc_capex": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame)",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "ngcc_heat_rate": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame)",
        "metric": "Heat Rate",
        "unit_conversion": "none",  # CSV already in MMBtu/MWh
        "dollar_year_adjust": False,
        "priority": 1,
    },
    "ngcc_lcoe": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame)",
        "metric": "LCOE",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # inputs already adjusted
        "priority": 1,
        "_not_in_csv": True,
        "_derived": "ngcc_lcoe_from_components",
    },
    "ngct_capex": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG Combustion Turbine (F-Frame)",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },
    "ngcc_ccs_capex": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame) 95% CCS",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
    },
    "ngcc_ccs_lcoe": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame) 95% CCS",
        "metric": "LCOE",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # inputs already adjusted
        "priority": 1,
        "_not_in_csv": True,
        "_derived": "ngcc_ccs_lcoe_from_components",
    },
    # --- helpers: NGCC FOM, VOM, WACC (needed for LCOE derivation) ---
    "_ngcc_fom": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame)",
        "metric": "Fixed O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
        "_helper": True,
    },
    "_ngcc_vom": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame)",
        "metric": "Variable O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
        "_helper": True,
    },
    "_ngcc_wacc_real": {
        "technology": "NaturalGas_FE",
        "techdetail": "*",
        "metric": "WACC Real",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 1,
        "_helper": True,
    },
    "_ngcc_ccs_fom": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame) 95% CCS",
        "metric": "Fixed O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
        "_helper": True,
    },
    "_ngcc_ccs_vom": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame) 95% CCS",
        "metric": "Variable O&M",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 1,
        "_helper": True,
    },
    "_ngcc_ccs_heat_rate": {
        "technology": "NaturalGas_FE",
        "techdetail": "NG 2-on-1 Combined Cycle (F-Frame) 95% CCS",
        "metric": "Heat Rate",
        "unit_conversion": "none",  # CSV already in MMBtu/MWh
        "dollar_year_adjust": False,
        "priority": 1,
        "_helper": True,
    },

    # =========================================================================
    # NUCLEAR (3 variables)
    # techdetail = "Nuclear - Large" and "Nuclear - Small" (not "Nuclear")
    # =========================================================================
    "nuclear_conventional_capex": {
        "technology": "Nuclear",
        "techdetail": "Nuclear - Large",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },
    "nuclear_smr_capex": {
        "technology": "Nuclear",
        "techdetail": "Nuclear - Small",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },
    "nuclear_lcoe": {
        "technology": "Nuclear",
        "techdetail": "Nuclear - Large",
        "metric": "LCOE",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 2,
    },

    # =========================================================================
    # HYDROGEN / ELECTROLYZER (8 variables)
    # Not in electricity CSV. Electrolyzer CAPEX/efficiency from DOE targets.
    # LCOH from Transportation ATB H2 production cost data.
    # =========================================================================
    "electrolyzer_pem_capex": {
        "technology": "Electrolysis",
        "techdetail": "PEM Electrolyzer",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # fallback already in 2024$
        "priority": 1,
        "_not_in_csv": True,
        # DOE Hydrogen Shot + industry consensus: PEM $/kW
        # ~$1100/kW (2024) declining to ~$250/kW (2030), $200 long-term
        "_static_fallback": {
            **{yr: round(1100 - (1100 - 250) * (yr - 2024) / 6)
               for yr in range(2024, 2031)},
            **{yr: round(250 - (250 - 200) * (yr - 2030) / 6)
               for yr in range(2031, 2037)},
            **{yr: 200 for yr in range(2037, 2051)},
        },
    },
    "electrolyzer_alkaline_capex": {
        "technology": "Electrolysis",
        "techdetail": "Alkaline Electrolyzer",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 1,
        "_not_in_csv": True,
        # Alkaline: cheaper than PEM today, convergent long-term
        "_static_fallback": {
            **{yr: round(500 - (500 - 150) * (yr - 2024) / 6)
               for yr in range(2024, 2031)},
            **{yr: 150 for yr in range(2031, 2051)},
        },
    },
    "electrolyzer_soec_capex": {
        "technology": "Electrolysis",
        "techdetail": "SOEC Electrolyzer",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 2,
        "_not_in_csv": True,
        # SOEC: higher starting cost, steeper learning curve
        "_static_fallback": {
            **{yr: round(2500 - (2500 - 500) * (yr - 2024) / 11)
               for yr in range(2024, 2036)},
            **{yr: 500 for yr in range(2036, 2051)},
        },
    },
    "electrolyzer_efficiency": {
        "technology": "Electrolysis",
        "techdetail": "PEM Electrolyzer",
        "metric": "Efficiency",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 1,
        "_not_in_csv": True,
        # PEM system efficiency kWh/kg H2: 54.5 (2024, ~61% LHV) → 47.6 (2050, ~70% LHV)
        "_static_fallback": {
            yr: round(54.5 - (54.5 - 47.6) * (yr - 2024) / 26, 1)
            for yr in range(2024, 2051)
        },
    },
    "lcoh_green_electrolysis": {
        "technology": "Electrolysis",
        "techdetail": "PEM Electrolyzer",
        "metric": "LCOH",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # deflator applied during H2 data load
        "priority": 1,
        "_not_in_csv": True,
        "_derived": "hydrogen_transport_atb",
    },
    "lcoh_blue_smr_ccs": {
        "technology": "SMR",
        "techdetail": "SMR-CCS",
        "metric": "LCOH",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 1,
        "_not_in_csv": True,
        "_derived": "hydrogen_transport_atb",
    },
    "lcoh_gray_smr": {
        "technology": "SMR",
        "techdetail": "SMR",
        "metric": "LCOH",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 1,
        "_not_in_csv": True,
        "_derived": "hydrogen_transport_atb",
    },
    "lcoh_atr_ccs": {
        "technology": "ATR",
        "techdetail": "ATR-CCS",
        "metric": "LCOH",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 2,
        "_not_in_csv": True,
        # No ATR data in Transportation ATB — left empty
    },

    # =========================================================================
    # OTHER TECHNOLOGIES (4 variables)
    # =========================================================================
    "csp_capex": {
        "technology": "CSP",
        "techdetail": "Class2",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 3,
    },
    "geothermal_capex": {
        "technology": "Geothermal",
        "techdetail": "HydroFlash",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 3,
    },
    "coal_ccs_capex": {
        "technology": "Coal_FE",
        "techdetail": "Coal-95%-CCS",
        "metric": "CAPEX",
        "unit_conversion": "none",
        "dollar_year_adjust": True,
        "priority": 3,
    },

    # =========================================================================
    # CCUS (1 variable) — not in electricity CSV
    # =========================================================================
    "ccus_cost_transport_storage": {
        "technology": "CCS",
        "techdetail": "Transport+Storage",
        "metric": "Cost",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # fallback already in 2024$
        "priority": 2,
        "_not_in_csv": True,
        # DOE/NETL estimates: pipeline transport ~$5-15/tCO2, saline storage ~$5-15/tCO2
        # Combined ~$15-20/tCO2 today, declining with scale to ~$10/tCO2 long-term
        "_static_fallback": {
            **{yr: round(18 - (18 - 12) * (yr - 2024) / 11, 1)
               for yr in range(2024, 2036)},
            **{yr: round(12 - (12 - 10) * (yr - 2035) / 15, 1)
               for yr in range(2036, 2051)},
        },
    },

    # =========================================================================
    # POLICY — IRA ITC/PTC (2 variables) — not in electricity CSV
    # IRA Section 48E (ITC) and Section 45Y (PTC) for clean electricity.
    # Base rates apply with prevailing wage + apprenticeship requirements.
    # Phase-down: begins when US power sector emissions ≤ 25% of 2022 levels,
    # or after 2032, whichever is later. Most analysts project ~2035 trigger.
    # Schedule: 100% → 75% (yr+1) → 50% (yr+2) → 0% (yr+3)
    # =========================================================================
    "ira_itc_rate": {
        "technology": "Policy",
        "techdetail": "IRA_ITC",
        "metric": "Rate",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "priority": 1,
        "_not_in_csv": True,
        # 30% base rate through 2035, then phasedown 75%→50%→0%
        "_static_fallback": {
            **{yr: 0.30 for yr in range(2024, 2036)},
            2036: 0.225,   # 75% of 30%
            2037: 0.15,    # 50% of 30%
            **{yr: 0.0 for yr in range(2038, 2051)},
        },
    },
    "ira_ptc_value": {
        "technology": "Policy",
        "techdetail": "IRA_PTC",
        "metric": "Value",
        "unit_conversion": "none",
        "dollar_year_adjust": False,  # already in 2024$
        "priority": 1,
        "_not_in_csv": True,
        # $27.50/MWh in 2024$ (inflation-indexed from $26/MWh 2022 base)
        # Phasedown same schedule as ITC
        "_static_fallback": {
            **{yr: 27.5 for yr in range(2024, 2036)},
            2036: 20.6,    # 75%
            2037: 13.8,    # 50%
            **{yr: 0.0 for yr in range(2038, 2051)},
        },
    },
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_technologies_used() -> set:
    """Return the set of ATB technology names needed for extraction."""
    techs = set()
    for spec in VARIABLE_MAP_ATB.values():
        if not spec.get("_not_in_csv") and not spec.get("_helper"):
            techs.add(spec["technology"])
    return techs


def get_canonical_variables() -> list[str]:
    """Return variable keys that are canonical (not helpers)."""
    return [k for k in VARIABLE_MAP_ATB if not k.startswith("_")]


def get_helper_variables() -> list[str]:
    """Return variable keys that are internal helpers."""
    return [k for k, v in VARIABLE_MAP_ATB.items() if v.get("_helper")]


def get_variables_by_priority_atb(priority: int) -> list[str]:
    """Return canonical variable keys with the given priority level."""
    return [k for k, v in VARIABLE_MAP_ATB.items()
            if v.get("priority") == priority and not v.get("_helper")]


def get_csv_variables() -> list[str]:
    """Return variable keys expected to be found in the electricity CSV."""
    return [k for k, v in VARIABLE_MAP_ATB.items()
            if not v.get("_not_in_csv") and not v.get("_derived")
            and not v.get("_helper")]


def get_hydrogen_variables() -> list[str]:
    """Return variable keys that require the hydrogen ATB dataset."""
    return [k for k, v in VARIABLE_MAP_ATB.items()
            if v.get("_not_in_csv") and v["technology"] in
            ("Electrolysis", "SMR", "ATR")]


if __name__ == "__main__":
    canonical = get_canonical_variables()
    helpers = get_helper_variables()
    total = len(canonical)
    p1 = len(get_variables_by_priority_atb(1))
    p2 = len(get_variables_by_priority_atb(2))
    p3 = len(get_variables_by_priority_atb(3))
    csv_vars = get_csv_variables()
    h2_vars = get_hydrogen_variables()
    derived = sum(1 for k, s in VARIABLE_MAP_ATB.items()
                  if s.get("_derived") and not s.get("_helper"))
    static_fb = sum(1 for s in VARIABLE_MAP_ATB.values() if s.get("_static_fallback"))

    print(f"VARIABLE_MAP_ATB: {total} canonical + {len(helpers)} helpers")
    print(f"  P1 (core):         {p1}")
    print(f"  P2 (important):    {p2}")
    print(f"  P3 (nice-to-have): {p3}")
    print(f"\n  Direct from CSV:   {len(csv_vars)}")
    print(f"  Derived:           {derived}")
    print(f"  Static fallback:   {static_fb}")
    print(f"  Hydrogen:          {len(h2_vars)}")
    print(f"  Helpers:           {len(helpers)}")

    techs = sorted(set(s["technology"] for s in VARIABLE_MAP_ATB.values()
                       if not s.get("_not_in_csv") and not s.get("_helper")))
    print(f"\nCSV Technologies ({len(techs)}):")
    for t in techs:
        count = sum(1 for s in VARIABLE_MAP_ATB.values()
                    if s["technology"] == t and not s.get("_not_in_csv")
                    and not s.get("_helper"))
        print(f"  {t:45s} {count} variables")
