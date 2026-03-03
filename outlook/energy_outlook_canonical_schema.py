"""
DecarbIQ Energy Outlook Canonical Schema
=========================================

Unified schema for normalizing energy outlook projections from multiple sources
(EIA AEO, IEA WEO, NREL ATB, BNEF, DNV, S&P, Lazard, DOE, ERCOT, EPA, etc.)
into a common structure for scenario comparison and Monte Carlo simulation.

Architecture:
  1. Each source+scenario is extracted into this canonical format
  2. Variables are normalized to common units and base year dollars
  3. Cross-source variance feeds structural uncertainty estimates
  4. Scenario "spines" feed the Monte Carlo engine with volatility overlays

Usage:
  from energy_outlook_canonical_schema import CANONICAL_SCHEMA, build_empty_outlook
  outlook = build_empty_outlook("EIA AEO 2025", "Reference Case")
"""

from typing import Any

# =============================================================================
# SCHEMA VERSION
# =============================================================================
SCHEMA_VERSION = "1.0.0"

# =============================================================================
# STANDARD UNITS - All outlooks get normalized to these
# =============================================================================
STANDARD_UNITS = {
    # Prices
    "commodity_price_oil":      "$/barrel (real 2024 USD)",
    "commodity_price_gas":      "$/MMBtu (real 2024 USD)",
    "commodity_price_coal":     "$/short ton (real 2024 USD)",
    "electricity_price":        "$/MWh (real 2024 USD)",
    "carbon_price":             "$/tonne CO2 (real 2024 USD)",
    "hydrogen_price":           "$/kg H2 (real 2024 USD)",

    # Energy quantities
    "energy_primary":           "EJ",          # exajoules
    "energy_final":             "EJ",
    "energy_quad":              "quads",       # kept for EIA compatibility (1 quad = 1.055 EJ)

    # Electricity
    "electricity_generation":   "TWh",
    "electricity_capacity":     "GW",
    "capacity_factor":          "fraction (0-1)",

    # Technology costs
    "capex":                    "$/kW (real 2024 USD)",
    "capex_storage_energy":     "$/kWh (real 2024 USD)",
    "fixed_om":                 "$/kW-yr (real 2024 USD)",
    "variable_om":              "$/MWh (real 2024 USD)",
    "lcoe":                     "$/MWh (real 2024 USD)",
    "lcos":                     "$/MWh (real 2024 USD)",
    "lcoh":                     "$/kg H2 (real 2024 USD)",
    "heat_rate":                "MMBtu/MWh",

    # Emissions
    "emissions_co2":            "Mt CO2",
    "emissions_methane":        "Mt CH4",
    "emissions_intensity":      "g CO2/kWh",

    # Production / supply
    "oil_production":           "Mb/d",        # million barrels per day
    "gas_production":           "Bcf/d",       # billion cubic feet per day
    "coal_production":          "Mt/yr",       # million short tons per year
    "hydrogen_production":      "Mt H2/yr",
    "biofuel_production":       "billion gallons/yr",

    # Capacity / deployment
    "electrolyzer_capacity":    "GW",
    "ev_stock":                 "million vehicles",
    "ev_sales_share":           "fraction (0-1)",
    "heat_pump_stock":          "million units",
    "ccus_capture":             "Mt CO2/yr",

    # Investment
    "investment":               "$ billion (real 2024 USD)",

    # Demand
    "demand_load":              "GW",          # peak demand
    "demand_energy":            "TWh",         # annual energy

    # Macro
    "gdp":                      "$ trillion (real 2024 USD)",
    "population":               "million persons",
    "energy_intensity":         "MJ/$ GDP (real 2024 USD)",

    # Minerals
    "mineral_demand":           "kt/yr",       # kilotonnes
}

# =============================================================================
# UNIT CONVERSION FACTORS
# =============================================================================
UNIT_CONVERSIONS = {
    # Energy
    "quad_to_ej":               1.05506,
    "mtoe_to_ej":               0.04187,
    "mtoe_to_twh":              11.63,
    "mmbtu_to_gj":              1.05506,
    "btu_per_kwh":              3412,

    # Gas
    "mcf_to_mmbtu":             1.037,    # approximate, varies by gas composition
    "bcm_to_tcf":               0.03531,
    "tcf_to_bcm":               28.317,

    # Price
    "cents_per_kwh_to_usd_per_mwh": 10.0,
    "mills_per_kwh_to_usd_per_mwh": 1.0,

    # Hydrogen
    "kg_h2_energy_content_mmbtu": 0.1197,  # 1 kg H2 ≈ 120 MJ ≈ 0.1197 MMBtu (LHV)
    "kg_h2_energy_content_kwh":   33.33,   # 1 kg H2 ≈ 33.33 kWh (LHV)

    # Oil
    "barrel_to_gj":             6.12,      # approximate for crude oil

    # Coal
    "short_ton_to_metric_ton":  0.9072,
}

# =============================================================================
# SOURCE REGISTRY - Known sources and their characteristics
# =============================================================================
SOURCE_REGISTRY = {
    "eia_aeo": {
        "full_name": "EIA Annual Energy Outlook",
        "publisher": "U.S. Energy Information Administration",
        "geography": "United States",
        "geo_granularity": ["national", "census_division", "emm_region"],
        "time_resolution": "annual",
        "horizon": 2050,
        "scenarios": {
            "reference":           "Reference Case — current laws and regulations",
            "high_oil_price":      "High Oil Price — Brent ~$157/b by 2050",
            "low_oil_price":       "Low Oil Price — Brent ~$48/b by 2050",
            "high_oil_gas_supply": "High Oil and Gas Supply — 50% higher recovery",
            "low_oil_gas_supply":  "Low Oil and Gas Supply — constrained resources",
            "high_ztc":            "High Zero-Carbon Technology Cost",
            "low_ztc":             "Low Zero-Carbon Technology Cost — 40% lower by 2050",
            "high_economic_growth": "High Economic Growth — 2.1% GDP CAGR",
            "low_economic_growth":  "Low Economic Growth — 1.2% GDP CAGR",
        },
        "data_access": "api",
        "api_url": "https://api.eia.gov/v2/aeo/",
        "dollar_year": 2024,
        "update_frequency": "annual",
    },
    "iea_weo": {
        "full_name": "IEA World Energy Outlook",
        "publisher": "International Energy Agency",
        "geography": "global",
        "geo_granularity": ["world", "region", "country"],
        "time_resolution": "snapshot",  # 2030, 2035, 2040, 2050
        "horizon": 2050,
        "scenarios": {
            "steps": "Stated Policies — existing laws and implemented measures (~2.4°C)",
            "aps":   "Announced Pledges — all NDCs and net-zero pledges met (~1.7°C)",
            "nze":   "Net Zero Emissions by 2050 — 1.5°C pathway",
        },
        "data_access": "excel_annex",
        "dollar_year": 2022,  # typically; check per edition
        "update_frequency": "annual (October)",
    },
    "nrel_atb": {
        "full_name": "NREL Annual Technology Baseline",
        "publisher": "National Renewable Energy Laboratory",
        "geography": "United States",
        "geo_granularity": ["national", "resource_class"],
        "time_resolution": "annual",
        "horizon": 2050,
        "scenarios": {
            "conservative": "Conservative — floor of expected improvement",
            "moderate":     "Moderate — central estimate, current trends",
            "advanced":     "Advanced — aggressive but plausible, DOE targets met",
        },
        "data_access": "api_and_excel",
        "api_url": "https://developer.nrel.gov/api/atb/v1/",
        "dollar_year": 2022,  # ATB 2024 base
        "update_frequency": "annual (June-July)",
    },
    "bnef_neo": {
        "full_name": "BloombergNEF New Energy Outlook",
        "publisher": "BloombergNEF",
        "geography": "global",
        "geo_granularity": ["world", "region", "country"],
        "time_resolution": "annual",
        "horizon": 2050,
        "scenarios": {
            "green":  "Green Scenario — max renewables and electrification",
            "gray":   "Gray Scenario — slower transition, more gas bridging",
            "red":    "Red Scenario — nuclear-heavy pathway",
            "net_zero": "Net Zero Scenario",
        },
        "data_access": "subscription",
        "dollar_year": 2023,
        "update_frequency": "annual",
    },
    "dnv_eto": {
        "full_name": "DNV Energy Transition Outlook",
        "publisher": "DNV",
        "geography": "global",
        "geo_granularity": ["world", "dnv_region"],  # 10 world regions
        "time_resolution": "annual",
        "horizon": 2050,
        "scenarios": {
            "pathway": "Most Likely Pathway — single probabilistic best estimate (~2.2°C)",
        },
        "data_access": "interactive_tool_and_download",
        "dollar_year": 2022,
        "update_frequency": "annual (October)",
    },
    "sp_global": {
        "full_name": "S&P Global Energy Scenarios",
        "publisher": "S&P Global / Platts",
        "geography": "global",
        "geo_granularity": ["world", "region", "country", "basin", "hub"],
        "time_resolution": "annual_or_monthly",
        "horizon": 2050,
        "scenarios": {
            "base":        "Base Case",
            "accelerated": "Accelerated Energy Transition",
            "delayed":     "Delayed Transition",
        },
        "data_access": "subscription",
        "update_frequency": "quarterly",
    },
    "lazard": {
        "full_name": "Lazard Levelized Cost of Energy / Storage",
        "publisher": "Lazard",
        "geography": "United States (primarily)",
        "geo_granularity": ["national"],
        "time_resolution": "point_in_time",  # annual snapshot, no projections
        "horizon": None,  # current year only
        "scenarios": {
            "unsubsidized_low":  "Unsubsidized LCOE — low end of range",
            "unsubsidized_high": "Unsubsidized LCOE — high end of range",
            "subsidized_low":    "Subsidized LCOE — low end with ITC/PTC",
            "subsidized_high":   "Subsidized LCOE — high end with ITC/PTC",
        },
        "data_access": "pdf",
        "update_frequency": "annual",
    },
    "doe_hydrogen": {
        "full_name": "DOE Hydrogen Program / H2A / Hydrogen Shot",
        "publisher": "U.S. Department of Energy",
        "geography": "United States",
        "geo_granularity": ["national", "regional"],
        "time_resolution": "snapshot",
        "horizon": 2050,
        "scenarios": {
            "current":    "Current technology costs",
            "future":     "Future target costs (Hydrogen Shot: $1/kg by 2031)",
        },
        "data_access": "reports_and_h2a_model",
        "update_frequency": "periodic",
    },
    "ercot": {
        "full_name": "ERCOT Planning Reports",
        "publisher": "Electric Reliability Council of Texas",
        "geography": "ERCOT region (Texas)",
        "geo_granularity": ["ercot_system", "weather_zone", "load_zone"],
        "time_resolution": "annual_and_hourly",
        "horizon": 2034,  # CDR typically 10-year horizon
        "scenarios": {
            "base":   "Base load forecast",
            "high":   "High demand scenario",
            "low":    "Low demand scenario",
        },
        "data_access": "excel_and_api",
        "update_frequency": "semi-annual (CDR), plus monthly updates",
    },
    "epa_ipm": {
        "full_name": "EPA Integrated Planning Model",
        "publisher": "U.S. Environmental Protection Agency",
        "geography": "United States",
        "geo_granularity": ["national", "ipm_region"],
        "time_resolution": "snapshot",
        "horizon": 2050,
        "scenarios": {
            "baseline":     "Baseline — current regulations",
            "policy":       "Policy case — proposed regulations",
        },
        "data_access": "reports_and_downloads",
        "update_frequency": "periodic (with major rulemakings)",
    },
    "mckinsey_gep": {
        "full_name": "McKinsey Global Energy Perspective",
        "publisher": "McKinsey & Company",
        "geography": "global",
        "geo_granularity": ["world", "region"],
        "time_resolution": "snapshot",
        "horizon": 2050,
        "scenarios": {
            "reference":    "Reference — current trajectory",
            "accelerated":  "Accelerated Transition — 1.5°C aligned",
            "achieved_commitments": "Achieved Commitments — NDCs met",
        },
        "data_access": "reports (gated)",
        "update_frequency": "annual",
    },
}

# =============================================================================
# CANONICAL VARIABLE DEFINITIONS
# Organized into categories. Each variable has:
#   - key: unique identifier used in the data structure
#   - unit: standard unit from STANDARD_UNITS
#   - description: what it represents
#   - sources: which outlook sources typically provide this variable
#   - geography: what geographic levels are typically available
#   - priority: 1 = core (must have), 2 = important, 3 = nice to have
# =============================================================================

VARIABLE_CATALOG: dict[str, dict[str, Any]] = {

    # =========================================================================
    # CATEGORY 1: COMMODITY PRICES
    # =========================================================================
    # --- Oil ---
    "brent_crude_price": {
        "unit": "$/barrel (real 2024 USD)",
        "description": "Brent crude oil international benchmark spot price",
        "category": "commodity_prices",
        "subcategory": "oil",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto", "sp_global"],
        "geography": ["global"],
        "priority": 1,
    },
    "wti_crude_price": {
        "unit": "$/barrel (real 2024 USD)",
        "description": "West Texas Intermediate crude oil spot price",
        "category": "commodity_prices",
        "subcategory": "oil",
        "sources": ["eia_aeo", "sp_global"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "motor_gasoline_price": {
        "unit": "$/gallon (real 2024 USD)",
        "description": "Retail motor gasoline price including taxes",
        "category": "commodity_prices",
        "subcategory": "oil",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 2,
    },
    "diesel_price": {
        "unit": "$/gallon (real 2024 USD)",
        "description": "On-highway diesel fuel retail price including taxes",
        "category": "commodity_prices",
        "subcategory": "oil",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 2,
    },
    "jet_fuel_price": {
        "unit": "$/gallon (real 2024 USD)",
        "description": "Kerosene-type jet fuel price",
        "category": "commodity_prices",
        "subcategory": "oil",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # --- Natural Gas ---
    "henry_hub_ng_price": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Henry Hub natural gas spot price benchmark",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto", "sp_global"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ng_price_europe": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "European natural gas benchmark (TTF / avg import)",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["iea_weo", "bnef_neo", "dnv_eto", "sp_global"],
        "geography": ["europe"],
        "priority": 2,
    },
    "ng_price_asia_lng": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Asian LNG import price (Japan/Korea marker)",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["iea_weo", "bnef_neo", "sp_global"],
        "geography": ["asia"],
        "priority": 2,
    },
    "ng_price_residential": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Delivered natural gas price to residential sector",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 2,
    },
    "ng_price_commercial": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Delivered natural gas price to commercial sector",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 2,
    },
    "ng_price_industrial": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Delivered natural gas price to industrial sector",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 1,
    },
    "ng_price_electric_power": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Delivered natural gas price to electric power sector",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 1,
    },
    "lng_export_price": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "U.S. LNG export price",
        "category": "commodity_prices",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo", "sp_global"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # --- Coal ---
    "coal_minemouth_price": {
        "unit": "$/short ton (real 2024 USD)",
        "description": "Average minemouth coal price",
        "category": "commodity_prices",
        "subcategory": "coal",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "supply_region"],
        "priority": 2,
    },
    "coal_delivered_power": {
        "unit": "$/short ton (real 2024 USD)",
        "description": "Delivered coal price to electric power sector",
        "category": "commodity_prices",
        "subcategory": "coal",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 2,
    },
    "steam_coal_price_intl": {
        "unit": "$/tonne (real 2024 USD)",
        "description": "International steam coal benchmark price",
        "category": "commodity_prices",
        "subcategory": "coal",
        "sources": ["iea_weo", "sp_global"],
        "geography": ["global", "asia", "europe"],
        "priority": 3,
    },

    # --- Electricity ---
    "electricity_price_residential": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Average retail electricity price — residential",
        "category": "commodity_prices",
        "subcategory": "electricity",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 1,
    },
    "electricity_price_commercial": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Average retail electricity price — commercial",
        "category": "commodity_prices",
        "subcategory": "electricity",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 2,
    },
    "electricity_price_industrial": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Average retail electricity price — industrial",
        "category": "commodity_prices",
        "subcategory": "electricity",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "census_division"],
        "priority": 1,
    },
    "electricity_price_wholesale": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Wholesale electricity price (competitive market)",
        "category": "commodity_prices",
        "subcategory": "electricity",
        "sources": ["eia_aeo", "sp_global", "ercot"],
        "geography": ["national_us", "emm_region", "ercot"],
        "priority": 1,
    },

    # --- Carbon ---
    "carbon_price_us": {
        "unit": "$/tonne CO2 (real 2024 USD)",
        "description": "U.S. carbon price / social cost of carbon",
        "category": "commodity_prices",
        "subcategory": "carbon",
        "sources": ["iea_weo", "bnef_neo", "epa_ipm"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "carbon_price_eu_ets": {
        "unit": "$/tonne CO2 (real 2024 USD)",
        "description": "EU Emissions Trading System carbon allowance price",
        "category": "commodity_prices",
        "subcategory": "carbon",
        "sources": ["iea_weo", "bnef_neo", "sp_global"],
        "geography": ["europe"],
        "priority": 2,
    },
    "carbon_price_advanced_economies": {
        "unit": "$/tonne CO2 (real 2024 USD)",
        "description": "Carbon price in advanced economies (IEA aggregate)",
        "category": "commodity_prices",
        "subcategory": "carbon",
        "sources": ["iea_weo"],
        "geography": ["advanced_economies"],
        "priority": 2,
    },

    # --- Hydrogen ---
    "hydrogen_price_delivered": {
        "unit": "$/kg H2 (real 2024 USD)",
        "description": "Delivered hydrogen price (including transport/storage)",
        "category": "commodity_prices",
        "subcategory": "hydrogen",
        "sources": ["eia_aeo", "iea_weo", "doe_hydrogen"],
        "geography": ["national_us", "regional"],
        "priority": 1,
    },

    # =========================================================================
    # CATEGORY 2: TECHNOLOGY COSTS (from NREL ATB, Lazard, IEA, BNEF)
    # =========================================================================

    # --- Solar PV ---
    "solar_pv_utility_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Utility-scale solar PV overnight capital cost (tracking)",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb", "lazard", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "resource_class", "global"],
        "priority": 1,
    },
    "solar_pv_utility_fom": {
        "unit": "$/kW-yr (real 2024 USD)",
        "description": "Utility-scale solar PV fixed O&M",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb", "lazard"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "solar_pv_utility_lcoe": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Utility-scale solar PV levelized cost of energy",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb", "lazard", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "resource_class", "global"],
        "priority": 1,
    },
    "solar_pv_utility_cf": {
        "unit": "fraction (0-1)",
        "description": "Utility-scale solar PV net capacity factor",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb", "eia_aeo"],
        "geography": ["national_us", "resource_class"],
        "priority": 1,
    },
    "solar_pv_commercial_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Commercial rooftop solar PV capital cost",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "solar_pv_residential_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Residential rooftop solar PV capital cost",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb"],
        "geography": ["national_us"],
        "priority": 3,
    },
    "csp_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Concentrating solar power (tower + TES) capital cost",
        "category": "technology_costs",
        "subcategory": "solar",
        "sources": ["nrel_atb", "lazard"],
        "geography": ["national_us"],
        "priority": 3,
    },

    # --- Wind ---
    "wind_onshore_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Land-based wind overnight capital cost",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb", "lazard", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "resource_class", "global"],
        "priority": 1,
    },
    "wind_onshore_fom": {
        "unit": "$/kW-yr (real 2024 USD)",
        "description": "Land-based wind fixed O&M",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb", "lazard"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "wind_onshore_lcoe": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Land-based wind levelized cost of energy",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb", "lazard", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "resource_class", "global"],
        "priority": 1,
    },
    "wind_onshore_cf": {
        "unit": "fraction (0-1)",
        "description": "Land-based wind net capacity factor",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb"],
        "geography": ["national_us", "resource_class"],
        "priority": 1,
    },
    "wind_offshore_fixed_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Offshore wind (fixed-bottom) capital cost",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb", "lazard", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "resource_class", "global"],
        "priority": 2,
    },
    "wind_offshore_fixed_lcoe": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Offshore wind (fixed-bottom) LCOE",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb", "lazard", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "resource_class", "global"],
        "priority": 2,
    },
    "wind_offshore_floating_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Offshore wind (floating) capital cost",
        "category": "technology_costs",
        "subcategory": "wind",
        "sources": ["nrel_atb", "bnef_neo"],
        "geography": ["national_us", "resource_class"],
        "priority": 3,
    },

    # --- Battery Storage ---
    "battery_utility_capex_power": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Utility-scale Li-ion battery power capacity cost (4-hr duration)",
        "category": "technology_costs",
        "subcategory": "storage",
        "sources": ["nrel_atb", "lazard", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "battery_utility_capex_energy": {
        "unit": "$/kWh (real 2024 USD)",
        "description": "Utility-scale Li-ion battery energy capacity cost",
        "category": "technology_costs",
        "subcategory": "storage",
        "sources": ["nrel_atb", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "battery_utility_lcos": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Utility-scale battery levelized cost of storage (4-hr)",
        "category": "technology_costs",
        "subcategory": "storage",
        "sources": ["nrel_atb", "lazard"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "battery_utility_rte": {
        "unit": "fraction (0-1)",
        "description": "Utility-scale battery round-trip efficiency",
        "category": "technology_costs",
        "subcategory": "storage",
        "sources": ["nrel_atb"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # --- Natural Gas Power ---
    "ngcc_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Natural gas combined cycle overnight capital cost",
        "category": "technology_costs",
        "subcategory": "natural_gas_power",
        "sources": ["nrel_atb", "eia_aeo", "lazard"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ngcc_heat_rate": {
        "unit": "MMBtu/MWh",
        "description": "Natural gas combined cycle net heat rate",
        "category": "technology_costs",
        "subcategory": "natural_gas_power",
        "sources": ["nrel_atb", "eia_aeo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ngcc_lcoe": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Natural gas combined cycle LCOE",
        "category": "technology_costs",
        "subcategory": "natural_gas_power",
        "sources": ["nrel_atb", "lazard", "eia_aeo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ngct_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Natural gas combustion turbine overnight capital cost",
        "category": "technology_costs",
        "subcategory": "natural_gas_power",
        "sources": ["nrel_atb", "eia_aeo"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "ngcc_ccs_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Natural gas CC with CCS (90% capture) capital cost",
        "category": "technology_costs",
        "subcategory": "natural_gas_power",
        "sources": ["nrel_atb", "eia_aeo", "iea_weo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ngcc_ccs_lcoe": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Natural gas CC with CCS (90% capture) LCOE",
        "category": "technology_costs",
        "subcategory": "natural_gas_power",
        "sources": ["nrel_atb", "lazard"],
        "geography": ["national_us"],
        "priority": 1,
    },

    # --- Nuclear ---
    "nuclear_conventional_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Conventional nuclear (Gen III+ LWR) overnight capital cost",
        "category": "technology_costs",
        "subcategory": "nuclear",
        "sources": ["nrel_atb", "eia_aeo", "iea_weo", "lazard"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "nuclear_smr_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Small modular reactor overnight capital cost",
        "category": "technology_costs",
        "subcategory": "nuclear",
        "sources": ["nrel_atb"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "nuclear_lcoe": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Nuclear LCOE (conventional large reactor)",
        "category": "technology_costs",
        "subcategory": "nuclear",
        "sources": ["nrel_atb", "lazard", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },

    # --- Hydrogen Production Technology Costs ---
    "electrolyzer_pem_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "PEM electrolyzer capital cost",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "electrolyzer_alkaline_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Alkaline electrolyzer capital cost",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "electrolyzer_soec_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Solid oxide electrolyzer (SOEC) capital cost",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "electrolyzer_efficiency": {
        "unit": "kWh/kg H2",
        "description": "Electrolyzer system efficiency (electricity per kg H2)",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen", "iea_weo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "lcoh_green_electrolysis": {
        "unit": "$/kg H2 (real 2024 USD)",
        "description": "Levelized cost of green hydrogen (electrolysis)",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen", "iea_weo", "bnef_neo", "mckinsey_gep"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "lcoh_blue_smr_ccs": {
        "unit": "$/kg H2 (real 2024 USD)",
        "description": "Levelized cost of blue hydrogen (SMR + CCS)",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "lcoh_gray_smr": {
        "unit": "$/kg H2 (real 2024 USD)",
        "description": "Levelized cost of gray hydrogen (unabated SMR)",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "lcoh_atr_ccs": {
        "unit": "$/kg H2 (real 2024 USD)",
        "description": "Levelized cost of hydrogen via autothermal reforming + CCS",
        "category": "technology_costs",
        "subcategory": "hydrogen",
        "sources": ["nrel_atb", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # --- Geothermal ---
    "geothermal_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Geothermal power plant capital cost (hydrothermal)",
        "category": "technology_costs",
        "subcategory": "geothermal",
        "sources": ["nrel_atb"],
        "geography": ["national_us", "resource_class"],
        "priority": 3,
    },

    # --- Coal Power ---
    "coal_ccs_capex": {
        "unit": "$/kW (real 2024 USD)",
        "description": "Coal with CCS (90% capture) capital cost",
        "category": "technology_costs",
        "subcategory": "coal_power",
        "sources": ["nrel_atb", "eia_aeo"],
        "geography": ["national_us"],
        "priority": 3,
    },

    # =========================================================================
    # CATEGORY 3: ELECTRICITY SYSTEM
    # =========================================================================

    # --- Generation ---
    "generation_total": {
        "unit": "TWh",
        "description": "Total electricity generation",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "generation_natural_gas": {
        "unit": "TWh",
        "description": "Electricity generation from natural gas",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "generation_coal": {
        "unit": "TWh",
        "description": "Electricity generation from coal",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "generation_nuclear": {
        "unit": "TWh",
        "description": "Electricity generation from nuclear",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "generation_wind": {
        "unit": "TWh",
        "description": "Electricity generation from wind (onshore + offshore)",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "generation_solar": {
        "unit": "TWh",
        "description": "Electricity generation from solar (utility + distributed)",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "generation_hydro": {
        "unit": "TWh",
        "description": "Electricity generation from hydroelectric",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 2,
    },
    "generation_biomass": {
        "unit": "TWh",
        "description": "Electricity generation from biomass/biopower",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 3,
    },
    "generation_geothermal": {
        "unit": "TWh",
        "description": "Electricity generation from geothermal",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 3,
    },
    "generation_hydrogen_ammonia": {
        "unit": "TWh",
        "description": "Electricity generation from hydrogen/ammonia combustion",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global"],
        "priority": 3,
    },
    "renewable_share_generation": {
        "unit": "fraction (0-1)",
        "description": "Share of renewables in total electricity generation",
        "category": "electricity",
        "subcategory": "generation",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },

    # --- Capacity ---
    "capacity_total": {
        "unit": "GW",
        "description": "Total installed electricity generating capacity",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "capacity_solar_pv": {
        "unit": "GW",
        "description": "Installed solar PV capacity (utility + distributed)",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "capacity_wind_onshore": {
        "unit": "GW",
        "description": "Installed onshore wind capacity",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "capacity_wind_offshore": {
        "unit": "GW",
        "description": "Installed offshore wind capacity",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global", "region"],
        "priority": 2,
    },
    "capacity_battery_storage": {
        "unit": "GW",
        "description": "Installed battery storage capacity (power)",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "capacity_battery_storage_energy": {
        "unit": "GWh",
        "description": "Installed battery storage capacity (energy)",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "capacity_natural_gas": {
        "unit": "GW",
        "description": "Installed natural gas generating capacity",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "capacity_coal": {
        "unit": "GW",
        "description": "Installed coal generating capacity",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "capacity_nuclear": {
        "unit": "GW",
        "description": "Installed nuclear generating capacity",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "capacity_additions_annual": {
        "unit": "GW",
        "description": "Annual new capacity additions by technology",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "capacity_retirements_annual": {
        "unit": "GW",
        "description": "Annual capacity retirements by technology",
        "category": "electricity",
        "subcategory": "capacity",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # --- Grid / System ---
    "electricity_demand_total": {
        "unit": "TWh",
        "description": "Total electricity demand (sales + T&D losses)",
        "category": "electricity",
        "subcategory": "system",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "ercot"],
        "geography": ["national_us", "global", "ercot"],
        "priority": 1,
    },
    "peak_demand": {
        "unit": "GW",
        "description": "Annual system peak demand",
        "category": "electricity",
        "subcategory": "system",
        "sources": ["eia_aeo", "ercot"],
        "geography": ["national_us", "ercot"],
        "priority": 1,
    },
    "grid_emissions_intensity": {
        "unit": "g CO2/kWh",
        "description": "Average CO2 intensity of electricity generation",
        "category": "electricity",
        "subcategory": "system",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "epa_ipm"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "td_losses": {
        "unit": "fraction (0-1)",
        "description": "Transmission and distribution loss factor",
        "category": "electricity",
        "subcategory": "system",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 3,
    },

    # =========================================================================
    # CATEGORY 4: ENERGY DEMAND BY SECTOR
    # =========================================================================
    "demand_total_primary": {
        "unit": "EJ",
        "description": "Total primary energy demand",
        "category": "energy_demand",
        "subcategory": "aggregate",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "demand_residential": {
        "unit": "EJ",
        "description": "Total residential sector energy consumption",
        "category": "energy_demand",
        "subcategory": "residential",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "demand_commercial": {
        "unit": "EJ",
        "description": "Total commercial sector energy consumption",
        "category": "energy_demand",
        "subcategory": "commercial",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "demand_industrial": {
        "unit": "EJ",
        "description": "Total industrial sector energy consumption",
        "category": "energy_demand",
        "subcategory": "industrial",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "demand_transport": {
        "unit": "EJ",
        "description": "Total transportation sector energy consumption",
        "category": "energy_demand",
        "subcategory": "transport",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },

    # --- Demand by Fuel ---
    "demand_natural_gas": {
        "unit": "EJ",
        "description": "Total natural gas consumption (all sectors)",
        "category": "energy_demand",
        "subcategory": "by_fuel",
        "sources": ["eia_aeo", "iea_weo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "demand_oil": {
        "unit": "Mb/d",
        "description": "Total oil consumption (all sectors)",
        "category": "energy_demand",
        "subcategory": "by_fuel",
        "sources": ["eia_aeo", "iea_weo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "demand_coal": {
        "unit": "EJ",
        "description": "Total coal consumption (all sectors)",
        "category": "energy_demand",
        "subcategory": "by_fuel",
        "sources": ["eia_aeo", "iea_weo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 2,
    },
    "demand_electricity_total": {
        "unit": "TWh",
        "description": "Total electricity consumption across all end-use sectors",
        "category": "energy_demand",
        "subcategory": "by_fuel",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "demand_hydrogen": {
        "unit": "Mt H2/yr",
        "description": "Total hydrogen demand across all sectors",
        "category": "energy_demand",
        "subcategory": "by_fuel",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "doe_hydrogen", "mckinsey_gep"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "demand_bioenergy": {
        "unit": "EJ",
        "description": "Total bioenergy consumption",
        "category": "energy_demand",
        "subcategory": "by_fuel",
        "sources": ["eia_aeo", "iea_weo", "dnv_eto"],
        "geography": ["national_us", "global"],
        "priority": 3,
    },

    # =========================================================================
    # CATEGORY 5: FOSSIL FUEL SUPPLY / PRODUCTION
    # =========================================================================
    "production_crude_oil_us": {
        "unit": "Mb/d",
        "description": "U.S. crude oil production (total domestic)",
        "category": "supply",
        "subcategory": "oil",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "production_tight_oil_us": {
        "unit": "Mb/d",
        "description": "U.S. tight oil (shale) production",
        "category": "supply",
        "subcategory": "oil",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "production_dry_gas_us": {
        "unit": "Bcf/d",
        "description": "U.S. dry natural gas production",
        "category": "supply",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "production_shale_gas_us": {
        "unit": "Bcf/d",
        "description": "U.S. shale gas production",
        "category": "supply",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "lng_exports_us": {
        "unit": "Bcf/d",
        "description": "U.S. LNG exports",
        "category": "supply",
        "subcategory": "natural_gas",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "production_coal_us": {
        "unit": "Mt/yr",
        "description": "U.S. coal production",
        "category": "supply",
        "subcategory": "coal",
        "sources": ["eia_aeo"],
        "geography": ["national_us", "supply_region"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 6: HYDROGEN ECONOMY
    # =========================================================================
    "h2_production_total": {
        "unit": "Mt H2/yr",
        "description": "Total hydrogen production",
        "category": "hydrogen",
        "subcategory": "production",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "doe_hydrogen"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_production_electrolysis": {
        "unit": "Mt H2/yr",
        "description": "Green hydrogen production via electrolysis",
        "category": "hydrogen",
        "subcategory": "production",
        "sources": ["iea_weo", "bnef_neo", "doe_hydrogen"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_production_smr": {
        "unit": "Mt H2/yr",
        "description": "Gray hydrogen production via unabated SMR",
        "category": "hydrogen",
        "subcategory": "production",
        "sources": ["iea_weo", "doe_hydrogen"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_production_smr_ccs": {
        "unit": "Mt H2/yr",
        "description": "Blue hydrogen production via SMR with CCS",
        "category": "hydrogen",
        "subcategory": "production",
        "sources": ["iea_weo", "bnef_neo", "doe_hydrogen"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_demand_industrial": {
        "unit": "Mt H2/yr",
        "description": "Hydrogen demand from industrial sector (refining, ammonia, steel, chemicals)",
        "category": "hydrogen",
        "subcategory": "demand",
        "sources": ["iea_weo", "doe_hydrogen", "mckinsey_gep"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_demand_transport": {
        "unit": "Mt H2/yr",
        "description": "Hydrogen demand from transport sector (FCEVs, trucks, shipping, aviation)",
        "category": "hydrogen",
        "subcategory": "demand",
        "sources": ["iea_weo", "bnef_neo", "mckinsey_gep"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_demand_power": {
        "unit": "Mt H2/yr",
        "description": "Hydrogen demand for power generation (gas turbines, fuel cells)",
        "category": "hydrogen",
        "subcategory": "demand",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "h2_demand_buildings": {
        "unit": "Mt H2/yr",
        "description": "Hydrogen demand from buildings sector (blending, heating)",
        "category": "hydrogen",
        "subcategory": "demand",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 3,
    },
    "electrolyzer_capacity_installed": {
        "unit": "GW",
        "description": "Cumulative installed electrolyzer capacity",
        "category": "hydrogen",
        "subcategory": "infrastructure",
        "sources": ["iea_weo", "bnef_neo", "doe_hydrogen"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "h2_carbon_intensity_by_pathway": {
        "unit": "kg CO2e/kg H2",
        "description": "Carbon intensity of hydrogen by production method",
        "category": "hydrogen",
        "subcategory": "emissions",
        "sources": ["eia_aeo", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 1,
    },

    # =========================================================================
    # CATEGORY 7: CCUS (Carbon Capture, Utilization and Storage)
    # =========================================================================
    "ccus_capture_total": {
        "unit": "Mt CO2/yr",
        "description": "Total CO2 captured annually",
        "category": "ccus",
        "subcategory": "capture",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "ccus_capture_power": {
        "unit": "Mt CO2/yr",
        "description": "CO2 captured from power sector",
        "category": "ccus",
        "subcategory": "capture",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "ccus_capture_industrial": {
        "unit": "Mt CO2/yr",
        "description": "CO2 captured from industrial processes (cement, steel, chemicals)",
        "category": "ccus",
        "subcategory": "capture",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "ccus_capture_dac": {
        "unit": "Mt CO2/yr",
        "description": "CO2 captured via direct air capture (DAC)",
        "category": "ccus",
        "subcategory": "capture",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 2,
    },
    "ccus_stored_geological": {
        "unit": "Mt CO2/yr",
        "description": "CO2 stored in geological formations (saline)",
        "category": "ccus",
        "subcategory": "storage",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "ccus_cost_capture": {
        "unit": "$/tonne CO2 (real 2024 USD)",
        "description": "Cost of CO2 capture per tonne",
        "category": "ccus",
        "subcategory": "costs",
        "sources": ["doe_hydrogen", "iea_weo"],
        "geography": ["national_us"],
        "priority": 2,
    },
    "ccus_cost_transport_storage": {
        "unit": "$/tonne CO2 (real 2024 USD)",
        "description": "Cost of CO2 transport and storage per tonne",
        "category": "ccus",
        "subcategory": "costs",
        "sources": ["nrel_atb", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 8: EMISSIONS
    # =========================================================================
    "emissions_co2_total": {
        "unit": "Mt CO2",
        "description": "Total energy-related CO2 emissions",
        "category": "emissions",
        "subcategory": "co2",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "emissions_co2_power": {
        "unit": "Mt CO2",
        "description": "CO2 emissions from electric power sector",
        "category": "emissions",
        "subcategory": "co2",
        "sources": ["eia_aeo", "iea_weo", "epa_ipm"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "emissions_co2_industry": {
        "unit": "Mt CO2",
        "description": "CO2 emissions from industrial sector",
        "category": "emissions",
        "subcategory": "co2",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "emissions_co2_transport": {
        "unit": "Mt CO2",
        "description": "CO2 emissions from transportation sector",
        "category": "emissions",
        "subcategory": "co2",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "emissions_co2_buildings": {
        "unit": "Mt CO2",
        "description": "CO2 emissions from buildings (residential + commercial)",
        "category": "emissions",
        "subcategory": "co2",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "emissions_co2_per_capita": {
        "unit": "t CO2/person",
        "description": "CO2 emissions per capita",
        "category": "emissions",
        "subcategory": "intensity",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "emissions_co2_per_gdp": {
        "unit": "kg CO2/$ GDP",
        "description": "CO2 emissions per unit GDP",
        "category": "emissions",
        "subcategory": "intensity",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "emissions_methane_energy": {
        "unit": "Mt CH4",
        "description": "Energy-sector methane emissions",
        "category": "emissions",
        "subcategory": "methane",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 9: TRANSPORT ELECTRIFICATION
    # =========================================================================
    "ev_sales_share_ldv": {
        "unit": "fraction (0-1)",
        "description": "Electric vehicle share of new light-duty vehicle sales",
        "category": "transport",
        "subcategory": "electrification",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo", "dnv_eto"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "ev_stock_ldv": {
        "unit": "million vehicles",
        "description": "Light-duty electric vehicle stock (BEV + PHEV)",
        "category": "transport",
        "subcategory": "electrification",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "ev_battery_pack_cost": {
        "unit": "$/kWh (real 2024 USD)",
        "description": "EV battery pack cost",
        "category": "transport",
        "subcategory": "electrification",
        "sources": ["bnef_neo"],
        "geography": ["global"],
        "priority": 1,
    },
    "ev_stock_trucks": {
        "unit": "million vehicles",
        "description": "Electric truck stock (medium + heavy duty)",
        "category": "transport",
        "subcategory": "electrification",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "fcev_stock": {
        "unit": "million vehicles",
        "description": "Fuel cell electric vehicle stock",
        "category": "transport",
        "subcategory": "hydrogen",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 3,
    },
    "transport_electricity_demand": {
        "unit": "TWh",
        "description": "Electricity demand from transportation sector (EV charging)",
        "category": "transport",
        "subcategory": "electrification",
        "sources": ["eia_aeo", "iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 1,
    },
    "new_vehicle_fuel_economy": {
        "unit": "mpge",
        "description": "Average new light-duty vehicle fuel economy (miles per gallon equivalent)",
        "category": "transport",
        "subcategory": "efficiency",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 10: BUILDINGS ELECTRIFICATION
    # =========================================================================
    "heat_pump_stock": {
        "unit": "million units",
        "description": "Heat pump installed stock",
        "category": "buildings",
        "subcategory": "electrification",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "buildings_electrification_rate": {
        "unit": "fraction (0-1)",
        "description": "Share of building heating demand met by electricity (heat pumps + resistance)",
        "category": "buildings",
        "subcategory": "electrification",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 11: INVESTMENT FLOWS
    # =========================================================================
    "investment_clean_energy_total": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Total clean energy investment",
        "category": "investment",
        "subcategory": "aggregate",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global"],
        "priority": 1,
    },
    "investment_fossil_fuel_supply": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Total fossil fuel supply investment (upstream oil, gas, coal)",
        "category": "investment",
        "subcategory": "aggregate",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 2,
    },
    "investment_renewables": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Investment in renewable energy (solar, wind, hydro, etc.)",
        "category": "investment",
        "subcategory": "power",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global", "region"],
        "priority": 1,
    },
    "investment_grids": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Investment in electricity grids (T&D)",
        "category": "investment",
        "subcategory": "power",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global"],
        "priority": 2,
    },
    "investment_battery_storage": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Investment in battery energy storage",
        "category": "investment",
        "subcategory": "power",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global"],
        "priority": 2,
    },
    "investment_hydrogen": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Investment in hydrogen production infrastructure",
        "category": "investment",
        "subcategory": "hydrogen",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global"],
        "priority": 2,
    },
    "investment_evs": {
        "unit": "$ billion (real 2024 USD)",
        "description": "Investment in electric vehicles and charging infrastructure",
        "category": "investment",
        "subcategory": "transport",
        "sources": ["iea_weo", "bnef_neo"],
        "geography": ["global"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 12: MACROECONOMIC
    # =========================================================================
    "gdp_real": {
        "unit": "$ trillion (real 2024 USD)",
        "description": "Real GDP",
        "category": "macro",
        "subcategory": "economic",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "gdp_growth_rate": {
        "unit": "fraction (annual)",
        "description": "Annual real GDP growth rate",
        "category": "macro",
        "subcategory": "economic",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "population": {
        "unit": "million persons",
        "description": "Total population",
        "category": "macro",
        "subcategory": "demographic",
        "sources": ["eia_aeo", "iea_weo", "dnv_eto"],
        "geography": ["national_us", "global", "region"],
        "priority": 1,
    },
    "energy_intensity_gdp": {
        "unit": "MJ/$ GDP (real 2024 USD)",
        "description": "Primary energy consumption per unit of GDP",
        "category": "macro",
        "subcategory": "intensity",
        "sources": ["eia_aeo", "iea_weo", "dnv_eto"],
        "geography": ["national_us", "global"],
        "priority": 2,
    },
    "industrial_output_index": {
        "unit": "index (base year = 100)",
        "description": "Industrial output index",
        "category": "macro",
        "subcategory": "economic",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 3,
    },

    # =========================================================================
    # CATEGORY 13: BIOFUELS AND RENEWABLE FUELS
    # =========================================================================
    "ethanol_production": {
        "unit": "billion gallons/yr",
        "description": "U.S. ethanol production",
        "category": "biofuels",
        "subcategory": "production",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 3,
    },
    "biodiesel_renewable_diesel_production": {
        "unit": "billion gallons/yr",
        "description": "Biodiesel and renewable diesel production",
        "category": "biofuels",
        "subcategory": "production",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 3,
    },
    "saf_production": {
        "unit": "billion gallons/yr",
        "description": "Sustainable aviation fuel production",
        "category": "biofuels",
        "subcategory": "production",
        "sources": ["eia_aeo", "iea_weo"],
        "geography": ["national_us", "global"],
        "priority": 3,
    },

    # =========================================================================
    # CATEGORY 14: CRITICAL MINERALS (IEA WEO addition)
    # =========================================================================
    "mineral_demand_lithium": {
        "unit": "kt/yr",
        "description": "Lithium demand from clean energy technologies",
        "category": "minerals",
        "subcategory": "demand",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 3,
    },
    "mineral_demand_cobalt": {
        "unit": "kt/yr",
        "description": "Cobalt demand from clean energy technologies",
        "category": "minerals",
        "subcategory": "demand",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 3,
    },
    "mineral_demand_nickel": {
        "unit": "kt/yr",
        "description": "Nickel demand from clean energy technologies",
        "category": "minerals",
        "subcategory": "demand",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 3,
    },
    "mineral_demand_copper": {
        "unit": "kt/yr",
        "description": "Copper demand from clean energy technologies",
        "category": "minerals",
        "subcategory": "demand",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 3,
    },
    "mineral_demand_rare_earths": {
        "unit": "kt/yr",
        "description": "Rare earth elements demand from clean energy technologies",
        "category": "minerals",
        "subcategory": "demand",
        "sources": ["iea_weo"],
        "geography": ["global"],
        "priority": 3,
    },

    # =========================================================================
    # CATEGORY 15: ERCOT / TEXAS REGIONAL
    # =========================================================================
    "ercot_peak_demand": {
        "unit": "GW",
        "description": "ERCOT system peak demand",
        "category": "regional_ercot",
        "subcategory": "demand",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_energy_demand": {
        "unit": "TWh",
        "description": "ERCOT total energy demand",
        "category": "regional_ercot",
        "subcategory": "demand",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_installed_capacity": {
        "unit": "GW",
        "description": "ERCOT total installed generating capacity",
        "category": "regional_ercot",
        "subcategory": "capacity",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_reserve_margin": {
        "unit": "fraction (0-1)",
        "description": "ERCOT planning reserve margin",
        "category": "regional_ercot",
        "subcategory": "reliability",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_wholesale_price": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "ERCOT average wholesale electricity price",
        "category": "regional_ercot",
        "subcategory": "prices",
        "sources": ["ercot", "sp_global"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_generation_mix": {
        "unit": "fraction (0-1)",
        "description": "ERCOT generation mix by fuel type",
        "category": "regional_ercot",
        "subcategory": "generation",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_solar_capacity": {
        "unit": "GW",
        "description": "ERCOT solar PV installed capacity",
        "category": "regional_ercot",
        "subcategory": "capacity",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_wind_capacity": {
        "unit": "GW",
        "description": "ERCOT wind installed capacity",
        "category": "regional_ercot",
        "subcategory": "capacity",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_battery_capacity": {
        "unit": "GW",
        "description": "ERCOT battery storage installed capacity",
        "category": "regional_ercot",
        "subcategory": "capacity",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "ercot_ng_capacity": {
        "unit": "GW",
        "description": "ERCOT natural gas generating capacity",
        "category": "regional_ercot",
        "subcategory": "capacity",
        "sources": ["ercot"],
        "geography": ["ercot"],
        "priority": 1,
    },
    "gulf_coast_industrial_ng_price": {
        "unit": "$/MMBtu (real 2024 USD)",
        "description": "Gulf Coast industrial natural gas price",
        "category": "regional_ercot",
        "subcategory": "prices",
        "sources": ["eia_aeo", "sp_global"],
        "geography": ["gulf_coast"],
        "priority": 1,
    },
    "gulf_coast_industrial_electricity_price": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Gulf Coast / West South Central industrial electricity price",
        "category": "regional_ercot",
        "subcategory": "prices",
        "sources": ["eia_aeo"],
        "geography": ["census_division_wsc"],
        "priority": 1,
    },
    "texas_water_cost": {
        "unit": "$/thousand gallons (real 2024 USD)",
        "description": "Industrial water cost in Texas",
        "category": "regional_ercot",
        "subcategory": "input_costs",
        "sources": [],  # manual / regional data
        "geography": ["texas"],
        "priority": 2,
    },

    # =========================================================================
    # CATEGORY 16: POLICY ASSUMPTIONS
    # =========================================================================
    "ira_itc_rate": {
        "unit": "fraction (0-1)",
        "description": "Investment Tax Credit rate (IRA Section 48/48E)",
        "category": "policy",
        "subcategory": "tax_credits",
        "sources": ["eia_aeo", "nrel_atb"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ira_ptc_value": {
        "unit": "$/MWh (real 2024 USD)",
        "description": "Production Tax Credit value (IRA Section 45/45Y)",
        "category": "policy",
        "subcategory": "tax_credits",
        "sources": ["eia_aeo", "nrel_atb"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ira_45v_h2_ptc": {
        "unit": "$/kg H2 (real 2024 USD)",
        "description": "Clean hydrogen production tax credit (Section 45V) value",
        "category": "policy",
        "subcategory": "tax_credits",
        "sources": ["eia_aeo", "doe_hydrogen"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "ira_45q_ccs_credit": {
        "unit": "$/tonne CO2 (real 2024 USD)",
        "description": "CCS tax credit (Section 45Q) value",
        "category": "policy",
        "subcategory": "tax_credits",
        "sources": ["eia_aeo"],
        "geography": ["national_us"],
        "priority": 1,
    },
    "rps_target": {
        "unit": "fraction (0-1)",
        "description": "Renewable Portfolio Standard target by state/region",
        "category": "policy",
        "subcategory": "mandates",
        "sources": ["eia_aeo", "epa_ipm"],
        "geography": ["state", "emm_region"],
        "priority": 2,
    },
}


# =============================================================================
# CROSS-CORRELATION DEFINITIONS
# Key structural relationships between variables for Monte Carlo
# =============================================================================
CROSS_CORRELATIONS = {
    "ng_price_to_electricity_price": {
        "driver": "henry_hub_ng_price",
        "target": "electricity_price_wholesale",
        "method": "heat_rate_implied",
        "description": "NG price drives marginal electricity cost via gas CC heat rate",
        "params": {
            "heat_rate_mmbtu_per_mwh": 6.4,  # typical NGCC
            "vom_adder_usd_per_mwh": 3.0,
        },
    },
    "ng_price_to_gray_h2_cost": {
        "driver": "henry_hub_ng_price",
        "target": "lcoh_gray_smr",
        "method": "smr_opex_fraction",
        "description": "NG feedstock is ~75% of gray H2 production cost",
        "params": {
            "ng_consumption_mmbtu_per_kg_h2": 0.156,  # ~156 kBtu/kg H2
            "non_fuel_cost_usd_per_kg": 0.35,
        },
    },
    "ng_price_to_blue_h2_cost": {
        "driver": "henry_hub_ng_price",
        "target": "lcoh_blue_smr_ccs",
        "method": "smr_ccs_opex_fraction",
        "description": "NG feedstock + CCS cost for blue H2",
        "params": {
            "ng_consumption_mmbtu_per_kg_h2": 0.167,  # slightly higher with CCS energy penalty
            "ccs_cost_usd_per_kg_h2": 0.30,
            "non_fuel_cost_usd_per_kg": 0.45,
        },
    },
    "electricity_price_to_green_h2_cost": {
        "driver": "electricity_price_industrial",
        "target": "lcoh_green_electrolysis",
        "method": "electrolyzer_opex_fraction",
        "description": "Electricity is 60-80% of green H2 production cost",
        "params": {
            "electrolyzer_efficiency_kwh_per_kg": 52.5,  # PEM at ~63% efficiency (LHV)
            "non_electricity_cost_usd_per_kg": 0.80,  # CAPEX + FOM amortized
        },
    },
    "oil_price_to_ng_price": {
        "driver": "brent_crude_price",
        "target": "henry_hub_ng_price",
        "method": "energy_equivalent_with_basis",
        "description": "Oil-gas price historically correlated (weakening in US since shale)",
        "params": {
            "energy_ratio_boe_to_mmbtu": 5.8,  # 1 barrel ≈ 5.8 MMBtu
            "us_basis_discount_fraction": 0.45,  # US gas significantly cheaper than oil equivalent
        },
    },
    "carbon_price_to_electricity_price": {
        "driver": "carbon_price_us",
        "target": "electricity_price_wholesale",
        "method": "emissions_adder",
        "description": "Carbon price adds to marginal cost of fossil generation",
        "params": {
            "marginal_emissions_rate_tco2_per_mwh": 0.40,  # NGCC marginal
        },
    },
    "solar_cost_to_wholesale_price": {
        "driver": "solar_pv_utility_lcoe",
        "target": "electricity_price_wholesale",
        "method": "merit_order_displacement",
        "description": "Falling solar LCOE suppresses wholesale prices via merit order",
        "params": {
            "solar_penetration_threshold": 0.15,  # effect accelerates above 15% share
        },
    },
    "battery_cost_to_solar_value": {
        "driver": "battery_utility_capex_energy",
        "target": "solar_pv_utility_lcoe",
        "method": "storage_pairing_value",
        "description": "Cheaper batteries increase dispatchable solar value",
    },
    "electrolyzer_capex_to_green_h2": {
        "driver": "electrolyzer_pem_capex",
        "target": "lcoh_green_electrolysis",
        "method": "capex_component",
        "description": "Electrolyzer CAPEX decline drives green H2 cost down",
        "params": {
            "capacity_factor": 0.50,
            "economic_life_years": 20,
        },
    },
}


# =============================================================================
# GEOGRAPHIC MAPPINGS
# Maps source-specific geographic labels to canonical geography codes
# =============================================================================
GEOGRAPHY_MAPPINGS = {
    "canonical_codes": {
        "global":               "World total",
        "national_us":          "United States total",
        "ercot":                "ERCOT region (most of Texas)",
        "gulf_coast":           "U.S. Gulf Coast (TX, LA industrial corridor)",
        "census_division_wsc":  "West South Central (AR, LA, OK, TX)",

        # IEA regions
        "advanced_economies":   "IEA advanced economies grouping",
        "north_america":        "North America",
        "europe":               "Europe",
        "asia":                 "Asia Pacific",
        "china":                "China",
        "india":                "India",

        # DNV regions
        "dnv_north_america":    "DNV North America region",
        "dnv_europe":           "DNV Europe region",

        # NREL
        "resource_class":       "NREL ATB resource quality class (1-10)",
    },

    # Source-specific → canonical mapping
    "eia_aeo": {
        "United States": "national_us",
        "West South Central": "census_division_wsc",
    },
    "iea_weo": {
        "World": "global",
        "United States": "national_us",
        "North America": "north_america",
        "European Union": "europe",
        "China": "china",
        "India": "india",
        "Japan": "asia",
        "Advanced economies": "advanced_economies",
    },
    "dnv_eto": {
        "World": "global",
        "North America": "dnv_north_america",
        "Europe": "dnv_europe",
    },
}


# =============================================================================
# TIME CONFIGURATION
# =============================================================================
TIME_CONFIG = {
    "base_year": 2024,
    "horizon": 2050,
    "annual_range": list(range(2024, 2051)),
    "iea_snapshot_years": [2023, 2030, 2035, 2040, 2050],
    "interpolation_method": "linear",  # for converting snapshots to annual
}


# =============================================================================
# OUTLOOK DATA STRUCTURE
# This is the actual data container that gets populated per source+scenario
# =============================================================================

def build_empty_outlook(
    source_id: str,
    scenario_name: str,
    base_year: int = 2024,
    horizon: int = 2050,
) -> dict[str, Any]:
    """
    Build an empty canonical outlook data structure ready to be populated.

    Args:
        source_id: Key from SOURCE_REGISTRY (e.g., "eia_aeo")
        scenario_name: Scenario identifier (e.g., "reference")
        base_year: First year of projections
        horizon: Last year of projections

    Returns:
        Dictionary matching the canonical schema, with empty annual dicts for
        each variable.
    """
    source_info = SOURCE_REGISTRY.get(source_id, {})

    outlook = {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "source_name": source_info.get("full_name", source_id),
        "publisher": source_info.get("publisher", "Unknown"),
        "scenario_name": scenario_name,
        "scenario_description": source_info.get("scenarios", {}).get(scenario_name, ""),
        "base_year": base_year,
        "horizon": horizon,
        "dollar_year": source_info.get("dollar_year", 2024),
        "extraction_metadata": {
            "extracted_at": None,       # ISO 8601 timestamp
            "extraction_method": None,  # "api", "llm_pdf", "excel_parse", "manual"
            "confidence_score": None,   # 0.0 - 1.0
            "source_document": None,    # filename or URL
            "notes": None,
        },

        # The actual data: variable_key → { year: value }
        "variables": {},

        # Cross-correlation overrides (source-specific if they publish them)
        "cross_correlations": {},
    }

    # Initialize all variables with empty annual dictionaries
    for var_key, var_def in VARIABLE_CATALOG.items():
        if source_id in var_def.get("sources", []) or source_id == "":
            outlook["variables"][var_key] = {
                "unit": var_def["unit"],
                "description": var_def["description"],
                "category": var_def["category"],
                "annual": {},  # { 2024: 3.10, 2025: 3.25, ... }
            }

    return outlook


def build_empty_outlook_all_variables(
    source_id: str,
    scenario_name: str,
    base_year: int = 2024,
    horizon: int = 2050,
) -> dict[str, Any]:
    """
    Build an outlook with ALL canonical variables (not just those from the source).
    Useful for creating a merged/composite outlook from multiple sources.
    """
    outlook = build_empty_outlook("", scenario_name, base_year, horizon)
    outlook["source_id"] = source_id
    outlook["source_name"] = f"Composite: {source_id}"
    return outlook


# =============================================================================
# SCHEMA SUMMARY FUNCTIONS
# =============================================================================

def get_variables_by_category() -> dict[str, list[str]]:
    """Return variable keys grouped by category."""
    by_cat: dict[str, list[str]] = {}
    for key, defn in VARIABLE_CATALOG.items():
        cat = defn["category"]
        by_cat.setdefault(cat, []).append(key)
    return by_cat


def get_variables_by_priority(priority: int) -> list[str]:
    """Return variable keys with the given priority level."""
    return [k for k, v in VARIABLE_CATALOG.items() if v.get("priority") == priority]


def get_variables_for_source(source_id: str) -> list[str]:
    """Return variable keys that a given source typically provides."""
    return [k for k, v in VARIABLE_CATALOG.items() if source_id in v.get("sources", [])]


def get_schema_stats() -> dict[str, Any]:
    """Return summary statistics about the schema."""
    categories = get_variables_by_category()
    return {
        "schema_version": SCHEMA_VERSION,
        "total_variables": len(VARIABLE_CATALOG),
        "total_categories": len(categories),
        "categories": {cat: len(vars_) for cat, vars_ in sorted(categories.items())},
        "priority_1_core": len(get_variables_by_priority(1)),
        "priority_2_important": len(get_variables_by_priority(2)),
        "priority_3_nice_to_have": len(get_variables_by_priority(3)),
        "total_sources": len(SOURCE_REGISTRY),
        "total_cross_correlations": len(CROSS_CORRELATIONS),
        "variables_per_source": {
            sid: len(get_variables_for_source(sid))
            for sid in SOURCE_REGISTRY
        },
    }


# =============================================================================
# CLI ENTRY POINT - print schema summary
# =============================================================================
if __name__ == "__main__":
    import json
    stats = get_schema_stats()
    print("=" * 70)
    print("DecarbIQ Energy Outlook Canonical Schema")
    print(f"Version: {stats['schema_version']}")
    print("=" * 70)
    print(f"\nTotal variables:        {stats['total_variables']}")
    print(f"  Priority 1 (core):    {stats['priority_1_core']}")
    print(f"  Priority 2 (important): {stats['priority_2_important']}")
    print(f"  Priority 3 (nice-to-have): {stats['priority_3_nice_to_have']}")
    print(f"\nTotal categories:       {stats['total_categories']}")
    for cat, count in stats["categories"].items():
        print(f"  {cat:30s} {count:3d} variables")
    print(f"\nTotal sources:          {stats['total_sources']}")
    for sid, count in stats["variables_per_source"].items():
        name = SOURCE_REGISTRY[sid]["full_name"]
        print(f"  {name:45s} {count:3d} variables")
    print(f"\nCross-correlations:     {stats['total_cross_correlations']}")
    print()

    # Example: build an empty EIA AEO outlook
    example = build_empty_outlook("eia_aeo", "reference")
    print(f"Example EIA AEO Reference outlook has {len(example['variables'])} variables")
    print("\nSample variable entry:")
    sample_key = "henry_hub_ng_price"
    print(f"  {sample_key}:")
    print(f"    {json.dumps(example['variables'][sample_key], indent=4)}")
