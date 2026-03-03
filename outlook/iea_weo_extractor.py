#!/usr/bin/env python3
"""
IEA World Energy Outlook (WEO) — Canonical Schema Extractor
============================================================

Extracts projection data from the IEA WEO free dataset and populates the
DecarbIQ canonical energy outlook schema.

Data sources:
    WEO 2024: https://www.iea.org/data-and-statistics/data-product/world-energy-outlook-2024-free-dataset
    WEO 2025: https://www.iea.org/data-and-statistics/data-product/world-energy-outlook-2025-free-dataset

Supports:
    - Excel data annex format (wide: scenarios as column groups)
    - Flat CSV export from IEA .Stat Data Explorer (long: one value per row)
    - Multiple CSV files (e.g. Regions.csv + Global_Data.csv from IEA download)

Editions:
    WEO 2024: Scenarios STEPS, APS, NZE | Snapshots 2023, 2030, 2035, 2040, 2050
    WEO 2025: Scenarios CPS, STEPS, NZE | Snapshots 2024, 2035, 2040, 2050

Annual interpolation: linear between snapshots

Usage:
    python3 iea_weo_extractor.py --file data/WEO2024_AnnexA.xlsx
    python3 iea_weo_extractor.py --file data/Regions.csv data/Global_Data.csv --format csv
    python3 iea_weo_extractor.py --file data/WEO2025_AnnexA.xlsx --weo-edition 2025
    python3 iea_weo_extractor.py --file data/WEO2024_AnnexA.xlsx --discover
    python3 iea_weo_extractor.py --file data/WEO2024_AnnexA.xlsx --scenarios steps
    python3 iea_weo_extractor.py --file data/WEO2024_AnnexA.xlsx --deflator 1.027 --verbose
"""

import argparse
import csv
import json
import logging
import math
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import openpyxl

# Add parent to path for schema import
sys.path.insert(0, str(Path(__file__).resolve().parent))
from energy_outlook_canonical_schema import (
    VARIABLE_CATALOG,
    UNIT_CONVERSIONS,
    build_empty_outlook,
    get_variables_for_source,
)

logger = logging.getLogger("iea_weo_extractor")

# =============================================================================
# CONSTANTS
# =============================================================================

WEO_EDITION = "2024"
CANONICAL_DOLLAR_YEAR = 2024

# Edition-specific configuration
EDITION_CONFIG = {
    "2024": {
        "dollar_year": 2023,  # WEO 2024 reports in real 2023 USD
        "default_deflator": 1.025,  # approximate CPI/GDP deflator 2023→2024
        "scenarios": ["steps", "aps", "nze"],
        "snapshot_years": {
            "steps": [2010, 2022, 2023, 2030, 2035, 2040, 2050],
            "aps": [2023, 2030, 2035, 2040, 2050],
            "nze": [2023, 2030, 2035, 2040, 2050],
        },
        "projection_snapshots": [2023, 2030, 2035, 2040, 2050],
        "base_year": 2023,
    },
    "2025": {
        "dollar_year": 2024,  # WEO 2025 reports in real 2024 USD
        "default_deflator": 1.0,  # no adjustment needed (already in 2024$)
        "scenarios": ["cps", "steps", "nze"],
        "snapshot_years": {
            "cps": [2010, 2023, 2024, 2035, 2040, 2050],
            "steps": [2010, 2023, 2024, 2035, 2040, 2050],
            "nze": [2024, 2035, 2040, 2050],
        },
        "projection_snapshots": [2024, 2035, 2040, 2050],
        "base_year": 2024,
    },
}

DEFAULT_DEFLATOR = EDITION_CONFIG[WEO_EDITION]["default_deflator"]

# =============================================================================
# SCENARIO MAP
# =============================================================================

SCENARIO_MAP = {
    "steps": {
        "weo_name": "Stated Policies Scenario",
        "col_header_pattern": r"Stated Policies",
        "short": "STEPS",
        "csv_codes": ["1", "STEPS", "Stated Policies", "Stated Policies Scenario"],
        "description": "Stated Policies — existing laws and implemented measures (~2.4°C)",
    },
    "aps": {
        "weo_name": "Announced Pledges Scenario",
        "col_header_pattern": r"Announced Pledges",
        "short": "APS",
        "csv_codes": ["2", "APS", "Announced Pledges", "Announced Pledges Scenario"],
        "description": "Announced Pledges — all NDCs and net-zero pledges met (~1.7°C)",
        "editions": ["2024"],  # APS only in WEO 2024
    },
    "cps": {
        "weo_name": "Current Policies Scenario",
        "col_header_pattern": r"Current Policies",
        "short": "CPS",
        "csv_codes": ["CPS", "Current Policies", "Current Policies Scenario"],
        "description": "Current Policies — only existing, implemented policies (~3°C)",
        "editions": ["2025"],  # CPS only in WEO 2025
    },
    "nze": {
        "weo_name": "Net Zero Emissions by 2050 Scenario",
        "col_header_pattern": r"Net Zero",
        "short": "NZE",
        "csv_codes": ["3", "NZE", "Net Zero", "Net Zero Emissions by 2050",
                      "Net Zero Emissions by 2050 Scenario"],
        "description": "Net Zero Emissions by 2050 — 1.5°C pathway",
    },
}

# =============================================================================
# UNIT CONVERSION TABLE
# =============================================================================

CONVERSION_TABLE = {
    "none":              lambda v: v,
    "pj_to_ej":          lambda v: v / 1000.0,
    "ej_identity":       lambda v: v,
    "twh_identity":      lambda v: v,
    "gw_identity":        lambda v: v,
    "mt_co2_identity":   lambda v: v,
    "mt_h2_identity":    lambda v: v,
    "mbd_identity":      lambda v: v,  # million barrels/day
    "bcm_to_ej":         lambda v: v * 0.03531 * 1.05506,  # bcm → Tcf → EJ
    "bcm_identity":      lambda v: v,
    "mtce_to_ej":        lambda v: v * 0.02931,  # Mtce → EJ (1 Mtce ≈ 29.31 PJ)
    "pj_to_twh":         lambda v: v / 3.6,
    "billion_usd_to_billion": lambda v: v,  # identity, stays in billions
    "billion_usd_to_trillion": lambda v: v / 1000.0,
    "gj_per_kusd_to_mj_per_usd": lambda v: v,  # GJ/1000USD ≈ MJ/USD
    "g_co2_per_kwh_identity": lambda v: v,
    "million_people_identity": lambda v: v,
    "pct_to_fraction":   lambda v: v / 100.0,
    "usd_per_barrel_identity": lambda v: v,
    "usd_per_mbtu_to_mmbtu": lambda v: v,  # $/MBtu ≈ $/MMBtu (IEA uses MBtu = MMBtu)
    "usd_per_tonne_identity": lambda v: v,
    "mt_identity":       lambda v: v,
    "ej_to_mt_h2":       lambda v: v / 0.12,  # 1 Mt H2 = 0.12 EJ (LHV 120 MJ/kg)
}

# Dynamic unit-to-conversion mapping for CSV mode
# Maps (csv_unit, target_canonical_unit) → conversion function name
# When CSV data has a different unit than what the spec expects, use this table.
CSV_UNIT_CONVERSIONS = {
    # Energy: EJ is the canonical unit
    "EJ": "ej_identity",
    "PJ": "pj_to_ej",
    "TWh": "twh_identity",
    "GW": "gw_identity",
    "Mt CO2": "mt_co2_identity",
    "Million barrels per day": "mbd_identity",
    "Billion cubic metres": "bcm_to_ej",
    "Million tonnes of coal equivalent": "mtce_to_ej",
    "gCO2 per kWh": "g_co2_per_kwh_identity",
    "Million people": "million_people_identity",
    "Billion USD (2024, PPP)": "billion_usd_to_trillion",
    "GJ per thousand USD (2024, PPP)": "gj_per_kusd_to_mj_per_usd",
    "Million tonnes": "mt_h2_identity",
    "USD per barrel (2023, MER)": "usd_per_barrel_identity",
    "USD per MBtu (2023, MER)": "usd_per_mbtu_to_mmbtu",
    "USD per tonne (2023, MER)": "usd_per_tonne_identity",
    "Billion USD (2023, MER)": "billion_usd_to_billion",
    "USD per barrel (2024, MER)": "usd_per_barrel_identity",
    "USD per MBtu (2024, MER)": "usd_per_mbtu_to_mmbtu",
    "USD per tonne (2024, MER)": "usd_per_tonne_identity",
    "Billion USD (2024, MER)": "billion_usd_to_billion",
    "USD per capita (2024, PPP)": "none",
    "Billion passenger-km": "none",
    "Billion tonne-km": "none",
    "Million units": "none",
    "Million square metres": "none",
}

# =============================================================================
# VARIABLE MAP: Canonical variable → WEO coordinates
# =============================================================================
# Each entry specifies how to find the data in the WEO Excel/CSV structure.
#
# Fields:
#   sheet:           Excel sheet name (for wide format)
#   category:        WEO metadata column C value
#   product:         WEO metadata column D value
#   flow:            WEO metadata column E value
#   region:          WEO metadata column G value (default: None = match any)
#   unit_source:     Expected unit in WEO data
#   unit_conversion: Key into CONVERSION_TABLE
#   dollar_year_adjust: Whether to apply 2023$→2024$ deflation
#   priority:        1/2/3 from canonical schema
#   geography:       "us" or "world" — which geographic data we're pulling
#   notes:           Human-readable notes
#   _derived:        If present, indicates this is computed from other variables

VARIABLE_MAP_WEO = {
    # =========================================================================
    # COMMODITY PRICES (from Prices sheet)
    # =========================================================================
    "brent_crude_price": {
        "sheet": "Prices",
        "category": "Energy",
        "product": "Oil",
        "flow": "Price",
        "region": "International Energy Agency",
        "unit_source": "USD per barrel (2023, MER)",
        "unit_conversion": "usd_per_barrel_identity",
        "dollar_year_adjust": True,
        "priority": 1,
        "geography": "global",
        "notes": "IEA crude oil import price (proxy for Brent)",
    },
    "henry_hub_ng_price": {
        "sheet": "Prices",
        "category": "Energy",
        "product": "Natural gas",
        "flow": "Price",
        "region": "United States",
        "unit_source": "USD per MBtu (2023, MER)",
        "unit_conversion": "usd_per_mbtu_to_mmbtu",
        "dollar_year_adjust": True,
        "priority": 1,
        "geography": "us",
        "notes": "US natural gas price (Henry Hub proxy)",
    },
    "ng_price_europe": {
        "sheet": "Prices",
        "category": "Energy",
        "product": "Natural gas",
        "flow": "Price",
        "region": "European Union",
        "unit_source": "USD per MBtu (2023, MER)",
        "unit_conversion": "usd_per_mbtu_to_mmbtu",
        "dollar_year_adjust": True,
        "priority": 2,
        "geography": "europe",
        "notes": "EU natural gas import price",
    },
    "ng_price_asia_lng": {
        "sheet": "Prices",
        "category": "Energy",
        "product": "Natural gas",
        "flow": "Price",
        "region": "Japan",
        "unit_source": "USD per MBtu (2023, MER)",
        "unit_conversion": "usd_per_mbtu_to_mmbtu",
        "dollar_year_adjust": True,
        "priority": 2,
        "geography": "asia",
        "notes": "Japan LNG import price (proxy for Asia LNG)",
    },
    "steam_coal_price_intl": {
        "sheet": "Prices",
        "category": "Energy",
        "product": "Steam coal",
        "flow": "Price",
        "region": "Japan",
        "unit_source": "USD per tonne (2023, MER)",
        "unit_conversion": "usd_per_tonne_identity",
        "dollar_year_adjust": True,
        "priority": 3,
        "geography": "asia",
        "notes": "Japan coal import price (international benchmark)",
    },

    # =========================================================================
    # ENERGY DEMAND — from REGION_Balance (United States) or demand sheets
    # =========================================================================
    "demand_total_primary": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Total",
        "flow": "Total energy supply",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US total primary energy supply",
    },
    "demand_industrial": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Total",
        "flow": "Industry",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US industrial energy demand",
    },
    "demand_transport": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Total",
        "flow": "Transport",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US transport energy demand",
    },
    # WEO combines residential+commercial into "Buildings"
    # Map to demand_residential as the closest match; note this is buildings total
    "demand_residential": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Total",
        "flow": "Buildings",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "us",
        "notes": "WEO 'Buildings' = residential + commercial combined",
    },
    "demand_natural_gas": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Natural gas",
        "flow": "Total energy supply",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US natural gas total energy supply",
    },
    "demand_oil": {
        "sheet": "Oil demand",
        "category": "Energy",
        "product": "Oil",
        "flow": "Total energy supply",
        "region": "United States",
        "unit_source": "Million barrels per day",
        "unit_conversion": "mbd_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US oil demand in Mb/d",
    },
    "demand_coal": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Coal",
        "flow": "Total energy supply",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "us",
        "notes": "US coal total energy supply",
    },
    "demand_electricity_total": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Electricity",
        "flow": "Final demand",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US electricity final demand (TWh)",
    },
    "demand_hydrogen": {
        "sheet": "Hydrogen demand",
        "category": "Energy",
        "product": "Hydrogen (incl onsite)",
        "flow": "Final demand",
        "region": "United States",
        "unit_source": "PJ",
        "unit_conversion": "pj_to_ej",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US hydrogen demand (from Hydrogen demand sheet)",
    },

    # =========================================================================
    # ELECTRICITY SYSTEM — from REGION_Balance (United States)
    # =========================================================================
    "generation_total": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Total",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
    },
    "generation_natural_gas": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Natural gas",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US CSV uses 'Natural gas' (not ':unabated')",
    },
    "generation_coal": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Coal",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
        "notes": "US CSV uses 'Coal' (not ':unabated')",
    },
    "generation_nuclear": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Nuclear",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
    },
    "generation_wind": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Wind",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
    },
    "generation_solar": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Solar PV",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
    },
    "generation_hydro": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Hydro",
        "flow": "Electricity generation",
        "region": "World",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
        "notes": "World level (US hydro not in WEO 2025 free regional data)",
    },
    "generation_biomass": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Modern bioenergy and renewable waste",
        "flow": "Electricity generation",
        "region": "World",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 3,
        "geography": "world",
        "notes": "Bioenergy and renewable waste combined, World level",
    },
    "generation_hydrogen_ammonia": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Hydrogen and H2-based fuels",
        "flow": "Electricity generation",
        "region": "World",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 3,
        "geography": "world",
    },

    # =========================================================================
    # CAPACITY — from REGION_Balance (US if available) or World CSV
    # Note: WEO 2025 free dataset has capacity at World level only
    # =========================================================================
    "capacity_total": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Total",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "World total capacity (US not available in WEO 2025 free dataset)",
    },
    "capacity_solar_pv": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Solar PV",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
    },
    "capacity_wind_onshore": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Wind",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "WEO free dataset has combined wind (onshore+offshore), World level",
    },
    "capacity_battery_storage": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Battery storage",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
    },
    "capacity_natural_gas": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Natural gas: unabated",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
    },
    "capacity_coal": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Coal: unabated",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
    },
    "capacity_nuclear": {
        "sheet": "REGION_Balance",
        "category": "Capacity: installed",
        "product": "Nuclear",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
    },

    # =========================================================================
    # EMISSIONS — from REGION_Balance (United States)
    # =========================================================================
    "emissions_co2_total": {
        "sheet": "REGION_Balance",
        "category": "CO2 total",
        "product": "Total",
        "flow": "Total energy supply",
        "region": "United States",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
    },
    "emissions_co2_power": {
        "sheet": "REGION_Balance",
        "category": "CO2 total",
        "product": "Total",
        "flow": "Power sector inputs",
        "region": "United States",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "us",
    },
    "emissions_co2_industry": {
        "sheet": "REGION_Balance",
        "category": "CO2 total",
        "product": "Total",
        "flow": "Industry",
        "region": "World",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "World level (US industry CO2 not in WEO 2025 free regional data)",
    },
    "emissions_co2_transport": {
        "sheet": "REGION_Balance",
        "category": "CO2 total",
        "product": "Total",
        "flow": "Transport",
        "region": "World",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
    },
    "emissions_co2_buildings": {
        "sheet": "REGION_Balance",
        "category": "CO2 total",
        "product": "Total",
        "flow": "Buildings",
        "region": "World",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
    },
    "grid_emissions_intensity": {
        "sheet": "REGION_Balance",
        "category": "CO2 total intensity",
        "product": "Electricity",
        "flow": "Electricity generation",
        "region": "World",
        "unit_source": "gCO2 per kWh",
        "unit_conversion": "g_co2_per_kwh_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
    },
    "ccus_capture_total": {
        "sheet": "REGION_Balance",
        "category": "CO2 total captured",
        "product": "Total",
        "flow": "Total energy supply",
        "region": "World",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
    },

    # =========================================================================
    # MACROECONOMIC — from REGION_Balance (United States)
    # =========================================================================
    "population": {
        "sheet": "REGION_Balance",
        "category": "Population indicators",
        "product": "Total",
        "flow": "Population",
        "region": "World",
        "unit_source": "Million people",
        "unit_conversion": "million_people_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "World population (US-specific not in WEO 2025 free dataset)",
    },
    "gdp_real": {
        "sheet": "REGION_Balance",
        "category": "Economic indicators",
        "product": "Total",
        "flow": "Gross domestic product",
        "region": "World",
        "unit_source": "Billion USD (2024, PPP)",
        "unit_conversion": "billion_usd_to_trillion",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "World GDP in PPP terms, converted to $ trillion. Already 2024$.",
    },
    "energy_intensity_gdp": {
        "sheet": "REGION_Balance",
        "category": "Energy intensity",
        "product": "Total",
        "flow": "TES per GDP",
        "region": "World",
        "unit_source": "GJ per thousand USD (2024, PPP)",
        "unit_conversion": "gj_per_kusd_to_mj_per_usd",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
        "notes": "GJ per thousand USD ≈ MJ per USD",
    },

    # =========================================================================
    # HYDROGEN — from Hydrogen balance (World) and Hydrogen demand (US)
    # =========================================================================
    "h2_production_total": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen: low-emissions",
        "flow": "Hydrogen production: offsite",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "Low-emissions H2 production (offsite), in EJ",
        # Excel fallback
        "label_match": "Low-emissions hydrogen production",
    },
    "h2_production_electrolysis": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen: low-emissions",
        "flow": "Hydrogen production inputs: offsite",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "Low-emissions H2 production inputs (proxy for electrolysis)",
        "label_match": "Water electrolysis",
    },
    "h2_production_smr_ccs": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen: low-emissions",
        "flow": "Hydrogen-based fuels production inputs",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "H2-based fuels production inputs (proxy for SMR+CCS)",
        "label_match": "Fossil fuels with CCUS",
    },
    "h2_demand_power": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen",
        "flow": "Power sector inputs",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
        "notes": "H2 for power generation (World level)",
        "label_match": "To power generation",
    },
    "h2_demand_industrial": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen",
        "flow": "Industry",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_to_mt_h2",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "H2 demand in industry (World, EJ→Mt H2 via LHV 120 MJ/kg)",
    },
    "h2_demand_transport": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen",
        "flow": "Transport",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_to_mt_h2",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "H2 demand in transport (World, EJ→Mt H2 via LHV 120 MJ/kg)",
    },
    "h2_demand_buildings": {
        "sheet": "Hydrogen balance",
        "category": "Energy",
        "product": "Hydrogen",
        "flow": "Buildings",
        "region": "World",
        "unit_source": "EJ",
        "unit_conversion": "ej_to_mt_h2",
        "dollar_year_adjust": False,
        "priority": 3,
        "geography": "world",
        "notes": "H2 demand in buildings (World, EJ→Mt H2 via LHV 120 MJ/kg)",
    },
    "electrolyzer_capacity_installed": {
        "sheet": "Hydrogen balance",
        "category": "Capacity: installed",
        "product": "Hydrogen and H2-based fuels",
        "flow": "Electrical capacity",
        "region": "World",
        "unit_source": "GW",
        "unit_conversion": "gw_identity",
        "dollar_year_adjust": False,
        "priority": 1,
        "geography": "world",
        "notes": "Electrolyzer + fuel cell capacity (World, GW). Proxy for electrolyzer capacity.",
    },

    # =========================================================================
    # CCUS — additional variables from World CSV
    # =========================================================================
    "ccus_capture_dac": {
        "sheet": None,
        "category": "CO2 total stored",
        "product": "Total",
        "flow": "Direct air capture",
        "region": "World",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
        "notes": "CO2 stored from DAC (World, Mt CO2/yr)",
    },
    "ccus_stored_geological": {
        "sheet": None,
        "category": "CO2 total removed",
        "product": "Total",
        "flow": "Total energy supply",
        "region": "World",
        "unit_source": "Mt CO2",
        "unit_conversion": "mt_co2_identity",
        "dollar_year_adjust": False,
        "priority": 2,
        "geography": "world",
        "notes": "Total CO2 removed incl. DAC + BECCS (World, Mt CO2/yr). Proxy for geological storage.",
    },

    # =========================================================================
    # INVESTMENT — from Investment sheet (World)
    # =========================================================================
    "investment_renewables": {
        "sheet": "Investment",
        "_investment_sheet": True,
        "_csv_skip": True,  # Investment data not in WEO free CSV
        "label_match": "Renewables",
        "label_match_parent": "Generation",
        "unit_conversion": "billion_usd_to_billion",
        "dollar_year_adjust": True,
        "priority": 1,
        "geography": "world",
        "notes": "Power generation renewables investment (annual avg by period). Excel only.",
    },
    "investment_grids": {
        "sheet": "Investment",
        "_investment_sheet": True,
        "_csv_skip": True,
        "label_match": "Electricity networks",
        "unit_conversion": "billion_usd_to_billion",
        "dollar_year_adjust": True,
        "priority": 2,
        "geography": "world",
    },
    "investment_battery_storage": {
        "sheet": "Investment",
        "_investment_sheet": True,
        "_csv_skip": True,
        "label_match": "Battery storage",
        "unit_conversion": "billion_usd_to_billion",
        "dollar_year_adjust": True,
        "priority": 2,
        "geography": "world",
    },
    "investment_hydrogen": {
        "sheet": "Investment",
        "_investment_sheet": True,
        "_csv_skip": True,
        "label_match": "Hydrogen and H\u2082-based fuels",
        "unit_conversion": "billion_usd_to_billion",
        "dollar_year_adjust": True,
        "priority": 2,
        "geography": "world",
    },
    "investment_fossil_fuel_supply": {
        "sheet": "Investment",
        "_investment_sheet": True,
        "_csv_skip": True,
        "label_match": "Oil and gas",
        "unit_conversion": "billion_usd_to_billion",
        "dollar_year_adjust": True,
        "priority": 2,
        "geography": "world",
        "notes": "Oil and gas upstream only; does not include coal mining. Excel only.",
    },


    # =========================================================================
    # DERIVED VARIABLES (computed post-extraction)
    # =========================================================================
    "renewable_share_generation": {
        "_derived": "ratio",
        "numerator": "generation_renewables_total",
        "denominator": "generation_total",
        "unit_conversion": "none",
        "priority": 1,
        "notes": "renewables generation / total generation",
        # We need a helper variable for total renewables generation
    },
    "emissions_co2_per_capita": {
        "_derived": "ratio",
        "numerator": "emissions_co2_total",
        "denominator": "population",
        "unit_conversion": "none",
        "priority": 2,
        "notes": "Mt CO2 / million people = t CO2/person",
    },
    "emissions_co2_per_gdp": {
        "_derived": "ratio_special",
        "numerator": "emissions_co2_total",
        "denominator": "gdp_real",
        "unit_conversion": "none",
        "priority": 2,
        "notes": "Mt CO2 / $ trillion → kg CO2/$ GDP (×1e6/1e12 = ×1e-6... needs scaling)",
    },
    "gdp_growth_rate": {
        "_derived": "yoy_growth",
        "source_var": "gdp_real",
        "unit_conversion": "none",
        "priority": 1,
        "notes": "Year-over-year GDP growth as fraction",
    },
}

# Helper variable (not in canonical schema but needed for derived calcs)
_HELPER_VARIABLES = {
    "generation_renewables_total": {
        "sheet": "REGION_Balance",
        "category": "Energy",
        "product": "Renewables",
        "flow": "Electricity generation",
        "region": "United States",
        "unit_source": "TWh",
        "unit_conversion": "twh_identity",
        "dollar_year_adjust": False,
        "priority": 0,
        "geography": "us",
    },
}


# =============================================================================
# MAIN EXTRACTOR CLASS
# =============================================================================

class IEAWEOExtractor:
    """
    Extracts IEA WEO free dataset and populates canonical energy outlook schema.

    Supports multiple input files (e.g. Regions.csv + Global_Data.csv from IEA download).
    """

    def __init__(
        self,
        file_path: str,
        additional_files: Optional[list[str]] = None,
        weo_edition: str = WEO_EDITION,
        start_year: int = 2024,
        end_year: int = 2050,
        deflator: Optional[float] = None,
        file_format: str = "auto",
    ):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"Data file not found: {self.file_path}")
        self.additional_files = [Path(f) for f in (additional_files or [])]
        for f in self.additional_files:
            if not f.exists():
                raise FileNotFoundError(f"Additional data file not found: {f}")

        self.weo_edition = weo_edition
        self.edition_cfg = EDITION_CONFIG.get(weo_edition, EDITION_CONFIG["2024"])
        self.start_year = start_year
        self.end_year = end_year
        self.deflator = deflator or self.edition_cfg["default_deflator"]
        self.file_format = file_format  # "auto", "excel", "csv"

        self._wb = None
        self._csv_data = None
        self._sheet_cache: dict[str, list] = {}

        # Auto-detect format
        if self.file_format == "auto":
            suffix = self.file_path.suffix.lower()
            if suffix in (".xlsx", ".xls", ".xlsb"):
                self.file_format = "excel"
            elif suffix in (".csv", ".tsv"):
                self.file_format = "csv"
            else:
                raise ValueError(f"Cannot auto-detect format for {suffix}; use --format")

        logger.info(f"IEA WEO Extractor initialized: {self.file_path.name} "
                     f"(edition={weo_edition}, format={self.file_format}, "
                     f"deflator={self.deflator:.4f})")
        if self.additional_files:
            logger.info(f"  Additional files: {[f.name for f in self.additional_files]}")

    # =========================================================================
    # DATA LOADING
    # =========================================================================

    def _load_workbook(self):
        """Load Excel workbook lazily."""
        if self._wb is None:
            logger.info(f"Loading workbook: {self.file_path.name}")
            self._wb = openpyxl.load_workbook(
                str(self.file_path), read_only=False, data_only=True
            )
            logger.info(f"Sheets: {self._wb.sheetnames}")

    def _load_csv(self):
        """Load flat CSV(s) into structured list of dicts.

        Merges primary file + any additional_files. Handles both
        .Stat Data Explorer exports and custom CSV formats by
        normalizing column names.
        """
        if self._csv_data is not None:
            return

        all_files = [self.file_path] + self.additional_files
        self._csv_data = []

        for csv_path in all_files:
            logger.info(f"Loading CSV: {csv_path.name}")
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                # Normalize column names for .Stat Data Explorer compatibility
                normalized = []
                for row in rows:
                    norm = {}
                    for k, v in row.items():
                        key = k.strip()
                        # Map .Stat Explorer column names to our expected names
                        key_map = {
                            "SCENARIO": "SCENARIO", "Scenario": "SCENARIO",
                            "SCENARIO_EN": "SCENARIO",
                            "REGION": "REGION", "Region": "REGION",
                            "COUNTRY": "REGION", "Country": "REGION",
                            "REF_AREA": "REGION",
                            "CATEGORY": "CATEGORY", "Category": "CATEGORY",
                            "PRODUCT": "PRODUCT", "Product": "PRODUCT",
                            "FLOW": "FLOW", "Flow": "FLOW",
                            "YEAR": "YEAR", "Year": "YEAR",
                            "TIME_PERIOD": "YEAR", "Time": "YEAR",
                            "VALUE": "VALUE", "Value": "VALUE",
                            "OBS_VALUE": "VALUE",
                            "UNIT": "UNIT", "Unit": "UNIT",
                            "UNIT_MEASURE": "UNIT",
                        }
                        mapped_key = key_map.get(key, key)
                        norm[mapped_key] = v.strip() if isinstance(v, str) else v
                    normalized.append(norm)
                self._csv_data.extend(normalized)
                logger.info(f"  Loaded {len(rows)} rows from {csv_path.name}")

        logger.info(f"Total CSV rows loaded: {len(self._csv_data)}")

    def _get_sheet_rows(self, sheet_name: str) -> list:
        """Get all rows from an Excel sheet, with caching."""
        if sheet_name in self._sheet_cache:
            return self._sheet_cache[sheet_name]

        self._load_workbook()
        if sheet_name not in self._wb.sheetnames:
            logger.warning(f"Sheet '{sheet_name}' not found in workbook")
            return []

        ws = self._wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        self._sheet_cache[sheet_name] = rows
        return rows

    # =========================================================================
    # EXCEL COLUMN DETECTION
    # =========================================================================

    def _detect_scenario_columns(self, sheet_name: str) -> dict[str, dict[int, int]]:
        """
        Detect which columns correspond to which scenario and year.

        Returns: {scenario_key: {year: col_index, ...}, ...}
        """
        rows = self._get_sheet_rows(sheet_name)
        if len(rows) < 2:
            return {}

        row1 = rows[0]  # Scenario headers
        row2 = rows[1]  # Year numbers

        result = {}
        for scenario_key, sinfo in SCENARIO_MAP.items():
            pattern = re.compile(sinfo["col_header_pattern"], re.IGNORECASE)
            year_cols = {}

            for col_idx in range(len(row1)):
                header = str(row1[col_idx]) if row1[col_idx] is not None else ""
                if pattern.search(header):
                    # Check if row2 has a year
                    year_val = row2[col_idx] if col_idx < len(row2) else None
                    if year_val is not None:
                        try:
                            year = int(float(str(year_val)))
                            if 2000 <= year <= 2060:
                                year_cols[year] = col_idx
                        except (ValueError, TypeError):
                            pass

            if year_cols:
                result[scenario_key] = year_cols
                logger.debug(f"  {scenario_key}: {sorted(year_cols.keys())} "
                             f"(cols {list(year_cols.values())})")

        return result

    def _find_data_rows(self, sheet_name: str) -> list[tuple[int, tuple]]:
        """Find rows that contain actual data (have GEC in col 0)."""
        rows = self._get_sheet_rows(sheet_name)
        data_rows = []
        for idx, row in enumerate(rows):
            if row[0] and "GEC" in str(row[0]):
                data_rows.append((idx, row))
        return data_rows

    # =========================================================================
    # DATA MATCHING
    # =========================================================================

    def _match_row_excel(
        self,
        spec: dict,
        data_rows: list[tuple[int, tuple]],
    ) -> Optional[tuple]:
        """
        Find the row in data_rows that matches the variable spec.
        Uses metadata columns (category, product, flow, region) and/or label_match.
        """
        for row_idx, row in data_rows:
            # Metadata matching
            cat = str(row[2]).strip() if row[2] else ""
            prod = str(row[3]).strip() if row[3] else ""
            flow = str(row[4]).strip() if row[4] else ""
            region = str(row[6]).strip() if row[6] else ""
            label = str(row[9]).strip() if len(row) > 9 and row[9] else ""

            # If spec has label_match, use that as primary matcher
            if "label_match" in spec and spec["label_match"]:
                target_label = spec["label_match"]
                # Normalize unicode characters for matching
                label_norm = label.replace("\xa0", " ").replace("\u2082", "2")
                target_norm = target_label.replace("\xa0", " ").replace("\u2082", "2")
                if label_norm.strip() == target_norm.strip():
                    # Also check region if specified
                    if spec.get("region") and region != spec["region"]:
                        continue
                    return row

            # Standard metadata matching
            if spec.get("category") and cat != spec["category"]:
                continue
            if spec.get("product") and prod != spec["product"]:
                continue
            if spec.get("flow") and flow != spec["flow"]:
                continue
            if spec.get("region") and region != spec["region"]:
                continue

            # All specified fields match
            if spec.get("category") or spec.get("product") or spec.get("flow"):
                return row

        return None

    def _match_row_csv(
        self,
        spec: dict,
        scenario_key: str,
    ) -> list[dict]:
        """
        Find matching rows in flat CSV data.
        Returns list of matching row dicts.
        Uses normalized column names (set by _load_csv).
        """
        self._load_csv()
        matches = []
        scenario_codes = SCENARIO_MAP[scenario_key]["csv_codes"]
        # Also include Historical data (provides base year values)
        scenario_codes_with_hist = scenario_codes + ["Historical"]

        for row in self._csv_data:
            # Check scenario
            scenario_val = row.get("SCENARIO", "")
            if scenario_val not in scenario_codes_with_hist:
                continue

            # Check region (flexible matching — strip whitespace, case-insensitive)
            region_val = row.get("REGION", "")
            if spec.get("region"):
                expected_region = spec["region"]
                if region_val != expected_region:
                    # Try case-insensitive and partial match
                    if expected_region.lower() not in region_val.lower():
                        continue

            # Label match (for variables with label_match instead of metadata)
            # Only use label matching if the CSV has a LABEL column
            if spec.get("label_match") and not spec.get("category"):
                label_val = row.get("LABEL", row.get("Label",
                            row.get("VARIABLE", row.get("Variable", ""))))
                if label_val:
                    target = spec["label_match"]
                    label_norm = label_val.replace("\xa0", " ").replace("\u2082", "2").strip()
                    target_norm = target.replace("\xa0", " ").replace("\u2082", "2").strip()
                    if label_norm != target_norm:
                        continue
                    matches.append(row)
                    continue
                # No LABEL column — fall through to category/product/flow matching

            # Check category, product, flow
            cat_val = row.get("CATEGORY", "")
            prod_val = row.get("PRODUCT", "")
            flow_val = row.get("FLOW", "")

            if spec.get("category") and cat_val != spec["category"]:
                continue
            if spec.get("product") and prod_val != spec["product"]:
                continue
            if spec.get("flow") and flow_val != spec["flow"]:
                continue

            matches.append(row)

        return matches

    # =========================================================================
    # VALUE EXTRACTION
    # =========================================================================

    def _extract_snapshots_excel(
        self,
        spec: dict,
        matched_row: tuple,
        scenario_cols: dict[int, int],
    ) -> dict[int, float]:
        """
        Extract snapshot year values from a matched Excel row.
        Returns {year: value, ...} for available snapshots.
        """
        snapshots = {}
        conv = CONVERSION_TABLE[spec["unit_conversion"]]

        for year, col_idx in scenario_cols.items():
            if col_idx < len(matched_row):
                raw_val = matched_row[col_idx]
                if raw_val is not None:
                    try:
                        val = float(raw_val)
                        val = conv(val)
                        if spec.get("dollar_year_adjust"):
                            val *= self.deflator
                        snapshots[year] = round(val, 6)
                    except (ValueError, TypeError):
                        logger.debug(f"Non-numeric value at year {year}: {raw_val}")

        return snapshots

    def _extract_snapshots_csv(
        self,
        spec: dict,
        matched_rows: list[dict],
    ) -> dict[int, float]:
        """
        Extract snapshot year values from matched CSV rows.
        CSV format has one row per observation with YEAR and VALUE columns.
        Uses normalized column names (set by _load_csv).

        Dynamically selects unit conversion based on CSV UNIT column when
        available, falling back to the spec's hardcoded conversion.
        """
        snapshots = {}

        # Detect unit from first matched row with a UNIT column
        csv_unit = None
        for row in matched_rows:
            u = row.get("UNIT", "")
            if u:
                csv_unit = u
                break

        # Use spec conversion when CSV unit matches expected source unit
        # (spec already encodes the correct transformation, e.g. EJ → Mt H2).
        # Only override with CSV_UNIT_CONVERSIONS when CSV unit differs from
        # what the spec expects (unit normalization, e.g. PJ→EJ when spec expected PJ).
        spec_unit_source = spec.get("unit_source", "")
        if csv_unit and csv_unit == spec_unit_source:
            # CSV unit matches spec expectation — use spec's conversion
            conv = CONVERSION_TABLE[spec["unit_conversion"]]
            logger.debug(f"    CSV unit '{csv_unit}' matches spec, "
                        f"using spec conversion '{spec['unit_conversion']}'")
        elif csv_unit and csv_unit in CSV_UNIT_CONVERSIONS:
            conv_key = CSV_UNIT_CONVERSIONS[csv_unit]
            conv = CONVERSION_TABLE[conv_key]
            logger.debug(f"    CSV unit '{csv_unit}' → conversion '{conv_key}'")
        else:
            conv = CONVERSION_TABLE[spec["unit_conversion"]]
            if csv_unit:
                logger.debug(f"    CSV unit '{csv_unit}' not in mapping, "
                            f"using spec conversion '{spec['unit_conversion']}'")

        for row in matched_rows:
            year_val = row.get("YEAR", "")
            value_val = row.get("VALUE", "")
            try:
                year = int(float(str(year_val)))
                val = float(value_val)
                val = conv(val)
                if spec.get("dollar_year_adjust"):
                    val *= self.deflator
                snapshots[year] = round(val, 6)
            except (ValueError, TypeError):
                continue

        return snapshots

    # =========================================================================
    # INVESTMENT SHEET SPECIAL HANDLING
    # =========================================================================

    # Investment sheet layout:
    #   - Scenarios stacked vertically (col B = scenario name)
    #   - Period averages in cols 13-15: 2024-2030, 2031-2040, 2041-2050
    #   - Labels in col J (index 9)
    # We map period averages to midpoint years: 2027, 2035, 2045

    INVESTMENT_PERIOD_COLS = {
        2027: 13,  # Annual average 2024-2030
        2035: 14,  # Annual average 2031-2040
        2045: 15,  # Annual average 2041-2050
    }

    INVESTMENT_SCENARIO_NAMES = {
        "steps": "Stated Policies Scenario",
        "aps": "Announced Pledges Scenario",
        "cps": "Current Policies Scenario",
        "nze": "Net Zero Emissions by 2050 Scenario",
    }

    def _extract_investment_excel(
        self,
        scenario_key: str,
        var_key: str,
        spec: dict,
    ) -> dict[str, float]:
        """
        Extract investment data from the special Investment sheet layout.
        Returns annual interpolated data.
        """
        rows = self._get_sheet_rows("Investment")
        if not rows:
            return {}

        target_scenario = self.INVESTMENT_SCENARIO_NAMES.get(scenario_key, "")
        target_label = spec.get("label_match", "")
        if not target_label:
            return {}

        conv = CONVERSION_TABLE[spec["unit_conversion"]]

        # Find matching row: scenario in col 1, label in col 9
        for row in rows:
            if not row[0] or "GEC" not in str(row[0]):
                continue
            row_scenario = str(row[1]).strip() if row[1] else ""
            row_label = str(row[9]).strip() if len(row) > 9 and row[9] else ""

            if row_scenario != target_scenario:
                continue

            # Normalize label (strip leading whitespace used for indentation)
            label_clean = row_label.strip()
            target_clean = target_label.strip()

            # Normalize unicode
            label_norm = label_clean.replace("\xa0", " ").replace("\u2082", "2")
            target_norm = target_clean.replace("\xa0", " ").replace("\u2082", "2")

            if label_norm != target_norm:
                continue

            # Extract period values
            snapshots = {}
            for midpoint_year, col_idx in self.INVESTMENT_PERIOD_COLS.items():
                if col_idx < len(row) and row[col_idx] is not None:
                    try:
                        val = float(row[col_idx])
                        val = conv(val)
                        if spec.get("dollar_year_adjust"):
                            val *= self.deflator
                        snapshots[midpoint_year] = round(val, 6)
                    except (ValueError, TypeError):
                        pass

            if snapshots:
                return self._interpolate_annual(
                    snapshots, self.start_year, self.end_year
                )

        return {}

    # =========================================================================
    # INTERPOLATION
    # =========================================================================

    @staticmethod
    def _interpolate_annual(
        snapshots: dict[int, float],
        start_year: int,
        end_year: int,
    ) -> dict[str, float]:
        """
        Linear interpolation between WEO snapshot years to produce annual values.

        WEO snapshots: {2023: v0, 2030: v1, 2035: v2, 2040: v3, 2045: v4, 2050: v5}
        Output: {"2024": v, "2025": v, ..., "2050": v}
        """
        if not snapshots:
            return {}

        sorted_years = sorted(snapshots.keys())
        annual = {}

        for year in range(start_year, end_year + 1):
            if year in snapshots:
                annual[str(year)] = snapshots[year]
                continue

            # Find bounding snapshot years
            lower_year = None
            upper_year = None
            for sy in sorted_years:
                if sy <= year:
                    lower_year = sy
                if sy >= year and upper_year is None:
                    upper_year = sy

            if lower_year is not None and upper_year is not None and lower_year != upper_year:
                # Linear interpolation
                frac = (year - lower_year) / (upper_year - lower_year)
                val = snapshots[lower_year] + frac * (snapshots[upper_year] - snapshots[lower_year])
                annual[str(year)] = round(val, 6)
            elif lower_year is not None:
                # Beyond last snapshot — flat extrapolation
                annual[str(year)] = snapshots[lower_year]
            elif upper_year is not None:
                # Before first snapshot — flat backward
                annual[str(year)] = snapshots[upper_year]

        return annual

    # =========================================================================
    # DERIVED VARIABLES
    # =========================================================================

    def _compute_derived(
        self,
        outlook: dict,
        helper_data: dict[str, dict[str, float]],
    ):
        """Compute derived variables from extracted primary data."""
        variables = outlook["variables"]

        for var_key, spec in VARIABLE_MAP_WEO.items():
            if "_derived" not in spec:
                continue

            deriv_type = spec["_derived"]

            if deriv_type == "ratio":
                num_key = spec["numerator"]
                den_key = spec["denominator"]
                # Numerator may be a helper variable
                num_data = helper_data.get(num_key, {})
                if not num_data and num_key in variables:
                    num_data = variables[num_key].get("annual", {})
                den_data = variables.get(den_key, {}).get("annual", {})

                if num_data and den_data:
                    annual = {}
                    for year_str in num_data:
                        if year_str in den_data and den_data[year_str] != 0:
                            annual[year_str] = round(
                                num_data[year_str] / den_data[year_str], 6
                            )
                    if annual and var_key in variables:
                        variables[var_key]["annual"] = annual
                        logger.info(f"  Derived {var_key}: {len(annual)} years")

            elif deriv_type == "ratio_special":
                # emissions_co2_per_gdp: Mt CO2 / $ trillion
                # → (Mt × 1e6 tonnes) / (trillion × 1e12 $) = 1e-6 kg/$
                # Actually: Mt CO2 / $ trillion = (1e6 t) / (1e12 $) = 1e-6 t/$ = 1e-3 kg/$
                # Canonical unit is kg CO2/$ GDP
                num_key = spec["numerator"]
                den_key = spec["denominator"]
                num_data = variables.get(num_key, {}).get("annual", {})
                den_data = variables.get(den_key, {}).get("annual", {})

                if num_data and den_data:
                    annual = {}
                    for year_str in num_data:
                        if year_str in den_data and den_data[year_str] != 0:
                            # Mt CO2 / $ trillion → kg CO2 / $ GDP
                            # = (Mt × 1e9 kg) / (trillion × 1e12 $) = 1e-3 kg/$
                            ratio = num_data[year_str] / den_data[year_str]
                            annual[year_str] = round(ratio * 1e-3, 6)
                    if annual and var_key in variables:
                        variables[var_key]["annual"] = annual
                        logger.info(f"  Derived {var_key}: {len(annual)} years")

            elif deriv_type == "yoy_growth":
                src_key = spec["source_var"]
                src_data = variables.get(src_key, {}).get("annual", {})
                if src_data:
                    years_sorted = sorted(src_data.keys())
                    annual = {}
                    for i in range(1, len(years_sorted)):
                        prev_yr = years_sorted[i - 1]
                        curr_yr = years_sorted[i]
                        if src_data[prev_yr] != 0:
                            growth = (src_data[curr_yr] - src_data[prev_yr]) / src_data[prev_yr]
                            annual[curr_yr] = round(growth, 6)
                    if annual and var_key in variables:
                        variables[var_key]["annual"] = annual
                        logger.info(f"  Derived {var_key}: {len(annual)} years")

    # =========================================================================
    # MAIN EXTRACTION
    # =========================================================================

    def extract_scenario(self, scenario_key: str) -> dict:
        """
        Extract a single WEO scenario and return a canonical outlook dict.
        """
        if scenario_key not in SCENARIO_MAP:
            raise ValueError(f"Unknown scenario: {scenario_key}. "
                             f"Valid: {list(SCENARIO_MAP.keys())}")
        # Check edition compatibility
        sinfo_editions = SCENARIO_MAP[scenario_key].get("editions")
        if sinfo_editions and self.weo_edition not in sinfo_editions:
            logger.warning(f"Scenario '{scenario_key}' is for editions "
                          f"{sinfo_editions}, not {self.weo_edition}. Skipping.")
            return None

        sinfo = SCENARIO_MAP[scenario_key]
        logger.info(f"Extracting scenario: {scenario_key} ({sinfo['short']})")

        # Build empty outlook
        outlook = build_empty_outlook(
            "iea_weo", scenario_key, self.start_year, self.end_year
        )
        source_dollar_year = self.edition_cfg["dollar_year"]
        proj_snapshots = self.edition_cfg["projection_snapshots"]
        outlook["extraction_metadata"] = {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": "excel_parse" if self.file_format == "excel" else "csv_parse",
            "confidence_score": 0.90,
            "source_document": str(self.file_path.name),
            "notes": (
                f"WEO {self.weo_edition} free dataset. "
                f"Dollar-year adjusted from {source_dollar_year}$ to "
                f"{CANONICAL_DOLLAR_YEAR}$ (deflator={self.deflator:.4f}). "
                f"Snapshot years interpolated linearly to annual."
            ),
            "weo_scenario": sinfo["short"],
            "interpolation_method": "linear",
            "snapshot_years": proj_snapshots,
        }

        # Track helper variables for derived calcs
        helper_data = {}

        if self.file_format == "excel":
            self._extract_excel(outlook, scenario_key, helper_data)
        elif self.file_format == "csv":
            self._extract_csv(outlook, scenario_key, helper_data)

        # Compute derived variables
        logger.info("Computing derived variables...")
        self._compute_derived(outlook, helper_data)

        return outlook

    def _extract_excel(
        self,
        outlook: dict,
        scenario_key: str,
        helper_data: dict,
    ):
        """Extract all variables from Excel format."""
        self._load_workbook()
        variables = outlook["variables"]

        # Group variables by sheet for efficiency
        by_sheet: dict[str, list[tuple[str, dict]]] = {}
        all_vars = list(VARIABLE_MAP_WEO.items()) + [
            (k, v) for k, v in _HELPER_VARIABLES.items()
        ]

        for var_key, spec in all_vars:
            if "_derived" in spec:
                continue
            sheet = spec.get("sheet", "")
            by_sheet.setdefault(sheet, []).append((var_key, spec))

        populated = 0
        total = 0

        for sheet_name, var_specs in by_sheet.items():
            if sheet_name not in self._wb.sheetnames:
                logger.warning(f"Sheet '{sheet_name}' not found — skipping "
                               f"{len(var_specs)} variables")
                continue

            # Investment sheet has special layout (period averages, vertical scenarios)
            if sheet_name == "Investment":
                for var_key, spec in var_specs:
                    total += 1
                    annual = self._extract_investment_excel(scenario_key, var_key, spec)
                    if annual and var_key in variables:
                        variables[var_key]["annual"] = annual
                        populated += 1
                        logger.info(f"  {var_key} (investment): {len(annual)} annual values")
                    elif not annual:
                        logger.debug(f"  {var_key}: no investment data for {scenario_key}")
                continue

            # Standard sheets: detect scenario columns
            scen_cols = self._detect_scenario_columns(sheet_name)
            if scenario_key not in scen_cols:
                logger.warning(f"Scenario '{scenario_key}' not found in "
                               f"'{sheet_name}' columns")
                continue

            year_cols = scen_cols[scenario_key]
            data_rows = self._find_data_rows(sheet_name)
            logger.info(f"Sheet '{sheet_name}': {len(data_rows)} data rows, "
                        f"years {sorted(year_cols.keys())}")

            for var_key, spec in var_specs:
                total += 1
                matched = self._match_row_excel(spec, data_rows)
                if matched is None:
                    logger.debug(f"  {var_key}: no match in {sheet_name}")
                    continue

                snapshots = self._extract_snapshots_excel(spec, matched, year_cols)
                if not snapshots:
                    logger.debug(f"  {var_key}: matched but no numeric values")
                    continue

                annual = self._interpolate_annual(
                    snapshots, self.start_year, self.end_year
                )

                if var_key in _HELPER_VARIABLES:
                    helper_data[var_key] = annual
                    logger.debug(f"  {var_key} (helper): {len(annual)} annual values")
                elif var_key in variables:
                    variables[var_key]["annual"] = annual
                    populated += 1
                    logger.info(f"  {var_key}: {len(snapshots)} snapshots → "
                                f"{len(annual)} annual values")

        logger.info(f"Excel extraction: {populated}/{total} variables populated")

    def _extract_csv(
        self,
        outlook: dict,
        scenario_key: str,
        helper_data: dict,
    ):
        """Extract all variables from flat CSV format."""
        self._load_csv()
        variables = outlook["variables"]

        populated = 0
        total = 0

        all_vars = list(VARIABLE_MAP_WEO.items()) + [
            (k, v) for k, v in _HELPER_VARIABLES.items()
        ]

        for var_key, spec in all_vars:
            if "_derived" in spec:
                continue
            if spec.get("_csv_skip"):
                logger.debug(f"  {var_key}: skipped (Excel-only variable)")
                continue
            total += 1

            matched = self._match_row_csv(spec, scenario_key)
            if not matched:
                logger.debug(f"  {var_key}: no CSV match")
                continue

            snapshots = self._extract_snapshots_csv(spec, matched)
            if not snapshots:
                continue

            annual = self._interpolate_annual(
                snapshots, self.start_year, self.end_year
            )

            if var_key in _HELPER_VARIABLES:
                helper_data[var_key] = annual
            elif var_key in variables:
                variables[var_key]["annual"] = annual
                populated += 1
                logger.info(f"  {var_key}: {len(snapshots)} snapshots → "
                            f"{len(annual)} annual values")

        logger.info(f"CSV extraction: {populated}/{total} variables populated")

    # =========================================================================
    # DISCOVERY MODE
    # =========================================================================

    def run_discovery(self, output_dir: str) -> dict:
        """
        Scan the data file and dump its structure for inspection.
        Helps verify variable mappings before full extraction.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        discovery = {
            "file": str(self.file_path),
            "format": self.file_format,
            "weo_edition": self.weo_edition,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

        if self.file_format == "excel":
            self._load_workbook()
            discovery["sheets"] = {}

            for sheet_name in self._wb.sheetnames:
                if sheet_name in ("Contents", "Notes", "Understanding WEO balances"):
                    continue

                rows = self._get_sheet_rows(sheet_name)
                data_rows = [r for r in rows if r[0] and "GEC" in str(r[0])]

                # Detect scenario columns
                scen_cols = self._detect_scenario_columns(sheet_name)

                # Collect unique metadata values
                categories = set()
                products = set()
                flows = set()
                units = set()
                regions = set()
                labels = []

                for row in data_rows:
                    if row[2]: categories.add(str(row[2]))
                    if row[3]: products.add(str(row[3]))
                    if row[4]: flows.add(str(row[4]))
                    if row[5]: units.add(str(row[5]))
                    if row[6]: regions.add(str(row[6]))
                    if len(row) > 9 and row[9]:
                        lbl = str(row[9]).strip()
                        if lbl not in ("0", "None", "Back to contents page"):
                            labels.append(lbl)

                sheet_info = {
                    "total_rows": len(rows),
                    "data_rows": len(data_rows),
                    "scenarios_detected": {
                        k: sorted(v.keys()) for k, v in scen_cols.items()
                    },
                    "categories": sorted(categories),
                    "products": sorted(products),
                    "flows": sorted(flows),
                    "units": sorted(units),
                    "regions": sorted(regions),
                    "labels": labels,
                }
                discovery["sheets"][sheet_name] = sheet_info

            # Check variable mapping coverage
            discovery["variable_mapping"] = self._check_variable_coverage()

        elif self.file_format == "csv":
            self._load_csv()
            # Summarize CSV structure
            if self._csv_data:
                discovery["columns"] = list(self._csv_data[0].keys())
                discovery["row_count"] = len(self._csv_data)
                discovery["sample_rows"] = self._csv_data[:5]

        # Save discovery output
        out_file = output_path / f"iea_weo_{self.weo_edition}_discovery.json"
        with open(out_file, "w") as f:
            json.dump(discovery, f, indent=2, default=str)
        logger.info(f"Discovery saved to {out_file}")

        return discovery

    def _check_variable_coverage(self) -> dict:
        """Check which variables can be matched in the data."""
        coverage = {"matched": [], "unmatched": [], "derived": []}

        for var_key, spec in VARIABLE_MAP_WEO.items():
            if "_derived" in spec:
                coverage["derived"].append(var_key)
                continue

            sheet = spec.get("sheet", "")
            if sheet not in self._wb.sheetnames:
                coverage["unmatched"].append(
                    {"variable": var_key, "reason": f"sheet '{sheet}' not found"}
                )
                continue

            # Investment sheet uses label matching with scenario in col B
            if spec.get("_investment_sheet"):
                data_rows = self._find_data_rows(sheet)
                target_label = spec.get("label_match", "")
                found = False
                for _, row in data_rows:
                    row_label = str(row[9]).strip() if len(row) > 9 and row[9] else ""
                    label_norm = row_label.replace("\xa0", " ").replace("\u2082", "2")
                    target_norm = target_label.replace("\xa0", " ").replace("\u2082", "2")
                    if label_norm.strip() == target_norm.strip():
                        coverage["matched"].append({
                            "variable": var_key,
                            "sheet": sheet,
                            "label": row_label,
                            "priority": spec.get("priority", 0),
                        })
                        found = True
                        break
                if not found:
                    coverage["unmatched"].append({
                        "variable": var_key,
                        "sheet": sheet,
                        "reason": f"no row matching label '{target_label}'",
                    })
                continue

            data_rows = self._find_data_rows(sheet)
            matched = self._match_row_excel(spec, data_rows)
            if matched:
                label = str(matched[9]).strip() if len(matched) > 9 and matched[9] else ""
                coverage["matched"].append({
                    "variable": var_key,
                    "sheet": sheet,
                    "label": label,
                    "priority": spec.get("priority", 0),
                })
            else:
                coverage["unmatched"].append({
                    "variable": var_key,
                    "sheet": sheet,
                    "reason": "no matching row",
                    "spec_category": spec.get("category"),
                    "spec_product": spec.get("product"),
                    "spec_flow": spec.get("flow"),
                })

        logger.info(f"Variable coverage: {len(coverage['matched'])} matched, "
                     f"{len(coverage['unmatched'])} unmatched, "
                     f"{len(coverage['derived'])} derived")
        return coverage

    # =========================================================================
    # OUTPUT
    # =========================================================================

    def save_json(self, outlook: dict, output_dir: str) -> str:
        """Save outlook as canonical JSON."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario = outlook["scenario_name"]
        filename = f"iea_weo_{self.weo_edition}_{scenario}.json"
        filepath = output_path / filename

        with open(filepath, "w") as f:
            json.dump(outlook, f, indent=2, default=str)
        logger.info(f"Saved JSON: {filepath}")
        return str(filepath)

    def save_csv(self, outlook: dict, output_dir: str) -> str:
        """Save outlook as flat CSV (one row per variable)."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario = outlook["scenario_name"]
        filename = f"iea_weo_{self.weo_edition}_{scenario}.csv"
        filepath = output_path / filename

        variables = outlook["variables"]
        years = [str(y) for y in range(self.start_year, self.end_year + 1)]

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["variable", "unit", "category", "geography"] + years
            writer.writerow(header)

            for var_key in sorted(variables.keys()):
                vdata = variables[var_key]
                annual = vdata.get("annual", {})
                if not annual:
                    continue

                cat_info = VARIABLE_CATALOG.get(var_key, {})
                unit = cat_info.get("unit", "")
                category = cat_info.get("category", "")
                spec = VARIABLE_MAP_WEO.get(var_key, {})
                geo = spec.get("geography", "")

                row = [var_key, unit.replace(",", ";"), category, geo]
                for yr in years:
                    row.append(annual.get(yr, ""))
                writer.writerow(row)

        logger.info(f"Saved CSV: {filepath}")
        return str(filepath)

    def save_all_scenarios_csv(
        self,
        outlooks: dict[str, dict],
        output_dir: str,
    ) -> str:
        """Save combined CSV with all scenarios."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        filename = f"iea_weo_{self.weo_edition}_all_scenarios.csv"
        filepath = output_path / filename

        years = [str(y) for y in range(self.start_year, self.end_year + 1)]

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["scenario", "variable", "unit", "category", "geography"] + years
            writer.writerow(header)

            for scenario_key in sorted(outlooks.keys()):
                outlook = outlooks[scenario_key]
                variables = outlook["variables"]

                for var_key in sorted(variables.keys()):
                    vdata = variables[var_key]
                    annual = vdata.get("annual", {})
                    if not annual:
                        continue

                    cat_info = VARIABLE_CATALOG.get(var_key, {})
                    unit = cat_info.get("unit", "")
                    category = cat_info.get("category", "")
                    spec = VARIABLE_MAP_WEO.get(var_key, {})
                    geo = spec.get("geography", "")

                    row = [scenario_key, var_key, unit.replace(",", ";"), category, geo]
                    for yr in years:
                        row.append(annual.get(yr, ""))
                    writer.writerow(row)

        logger.info(f"Saved combined CSV: {filepath}")
        return str(filepath)

    # =========================================================================
    # VALIDATION
    # =========================================================================

    def validate_outlook(self, outlook: dict) -> dict:
        """
        Validate extraction completeness and data quality.
        Reports by priority tier.
        """
        variables = outlook["variables"]
        scenario = outlook["scenario_name"]

        weo_vars = get_variables_for_source("iea_weo")
        mapped_vars = set(VARIABLE_MAP_WEO.keys())

        report = {
            "scenario": scenario,
            "weo_edition": self.weo_edition,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {},
            "by_priority": {},
            "issues": [],
        }

        for priority in [1, 2, 3]:
            p_vars = [v for v in weo_vars
                       if VARIABLE_CATALOG.get(v, {}).get("priority") == priority
                       and v in mapped_vars]
            populated = 0
            partial = 0
            empty = 0

            for var_key in p_vars:
                if var_key not in variables:
                    empty += 1
                    continue
                annual = variables[var_key].get("annual", {})
                expected_years = self.end_year - self.start_year + 1
                if len(annual) >= expected_years * 0.8:
                    populated += 1
                elif len(annual) > 0:
                    partial += 1
                else:
                    empty += 1

            total = len(p_vars)
            pct = (populated / total * 100) if total > 0 else 0
            tier_label = {1: "P1 (core)", 2: "P2 (important)", 3: "P3 (nice-to-have)"}
            report["by_priority"][f"P{priority}"] = {
                "total": total,
                "populated": populated,
                "partial": partial,
                "empty": empty,
                "pct": round(pct, 1),
            }
            logger.info(f"  {tier_label.get(priority, f'P{priority}')}: "
                        f"{populated}/{total} ({pct:.1f}%)")

            # Flag issues
            if priority == 1 and pct < 80:
                report["issues"].append({
                    "level": "ERROR",
                    "message": f"P1 coverage below 80%: {pct:.1f}%",
                })
            if priority == 2 and pct < 50:
                report["issues"].append({
                    "level": "WARNING",
                    "message": f"P2 coverage below 50%: {pct:.1f}%",
                })

        # Data quality checks
        for var_key, vdata in variables.items():
            annual = vdata.get("annual", {})
            if not annual:
                continue

            values = list(annual.values())

            # Check for suspicious values
            if any(v < -1000 for v in values):
                report["issues"].append({
                    "level": "WARNING",
                    "variable": var_key,
                    "message": f"Large negative value: {min(values):.2f}",
                })

            # Check for constant values (no change over 27 years)
            if len(values) > 5 and len(set(round(v, 2) for v in values)) == 1:
                report["issues"].append({
                    "level": "WARNING",
                    "variable": var_key,
                    "message": "All values identical (suspiciously constant)",
                })

        # Overall summary
        total_mapped = len(mapped_vars)
        total_populated = sum(
            1 for v in mapped_vars
            if v in variables and variables[v].get("annual")
        )
        report["summary"] = {
            "total_canonical_weo_vars": len(weo_vars),
            "total_mapped": total_mapped,
            "total_populated": total_populated,
            "coverage_pct": round(total_populated / total_mapped * 100, 1)
            if total_mapped > 0 else 0,
        }

        return report


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="IEA WEO → DecarbIQ Canonical Schema Extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # WEO 2024 (STEPS, APS, NZE):
  python3 iea_weo_extractor.py --file data/WEO2024_AnnexA.xlsx --discover
  python3 iea_weo_extractor.py --file data/WEO2024_AnnexA.xlsx --scenarios steps

  # WEO 2025 (CPS, STEPS, NZE) with multiple CSVs:
  python3 iea_weo_extractor.py --file data/Regions.csv data/Global_Data.csv --weo-edition 2025
  python3 iea_weo_extractor.py --file data/WEO2025_AnnexA.xlsx --weo-edition 2025

  # CSV from .Stat Data Explorer:
  python3 iea_weo_extractor.py --file data/WEO2024_export.csv --format csv

Data download (free IEA account required):
  WEO 2024: https://www.iea.org/data-and-statistics/data-product/world-energy-outlook-2024-free-dataset
  WEO 2025: https://www.iea.org/data-and-statistics/data-product/world-energy-outlook-2025-free-dataset

  Download these files and place in data/ directory:
    1. "Regions" CSV (374 KB) — contains US-specific data (most important)
    2. "Global Data" CSV (297 KB) — world aggregates (hydrogen, investment)
    3. "Tables for scenario projections (Annex A)" XLSX (327 KB) — summary tables
    4. "Power generation technology costs" XLSB (81 KB) — tech cost assumptions
        """,
    )
    parser.add_argument("--file", nargs="+", required=True,
                        help="Path to WEO data file(s) (Excel or CSV). "
                             "Multiple CSVs are merged automatically.")
    parser.add_argument("--format", choices=["auto", "excel", "csv"], default="auto",
                        help="File format (default: auto-detect)")
    parser.add_argument("--weo-edition", default=WEO_EDITION,
                        help=f"WEO edition year (default: {WEO_EDITION}). "
                             "Determines available scenarios and snapshot years.")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="Scenarios to extract (default: all for edition). "
                             "2024: steps, aps, nze | 2025: cps, steps, nze")
    parser.add_argument("--output-dir", default="output", help="Output directory (default: output)")
    parser.add_argument("--discover", action="store_true", help="Run discovery mode only")
    parser.add_argument("--deflator", type=float, default=None,
                        help="Dollar-year deflator to canonical 2024$ "
                             "(default: auto from edition config)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve paths
    script_dir = Path(__file__).resolve().parent
    file_paths = []
    for fp in args.file:
        p = Path(fp)
        if not p.is_absolute():
            p = script_dir / p
        file_paths.append(p)

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = script_dir / output_dir

    # Create extractor
    primary_file = str(file_paths[0])
    additional = [str(f) for f in file_paths[1:]]

    extractor = IEAWEOExtractor(
        file_path=primary_file,
        additional_files=additional if additional else None,
        weo_edition=args.weo_edition,
        deflator=args.deflator,
        file_format=args.format,
    )

    # Discovery mode
    if args.discover:
        discovery = extractor.run_discovery(str(output_dir))
        print(f"\nDiscovery complete. Results saved to {output_dir}")
        if "variable_mapping" in discovery:
            vm = discovery["variable_mapping"]
            print(f"  Matched: {len(vm['matched'])} variables")
            print(f"  Unmatched: {len(vm['unmatched'])} variables")
            print(f"  Derived: {len(vm['derived'])} variables")
            if vm["unmatched"]:
                print("\nUnmatched variables:")
                for u in vm["unmatched"]:
                    print(f"  - {u['variable']}: {u.get('reason', 'unknown')}")
        return

    # Determine scenarios based on edition
    edition_cfg = EDITION_CONFIG.get(args.weo_edition, EDITION_CONFIG["2024"])
    if args.scenarios:
        scenarios = args.scenarios
    else:
        scenarios = edition_cfg["scenarios"]

    for s in scenarios:
        if s not in SCENARIO_MAP:
            print(f"ERROR: Unknown scenario '{s}'. "
                  f"Valid for WEO {args.weo_edition}: {edition_cfg['scenarios']}")
            sys.exit(1)

    # Extract
    outlooks = {}
    for scenario_key in scenarios:
        print(f"\n{'='*60}")
        print(f"Extracting: {scenario_key} ({SCENARIO_MAP[scenario_key]['short']})")
        print(f"{'='*60}")

        outlook = extractor.extract_scenario(scenario_key)
        if outlook is None:
            print(f"  Skipped (not available for WEO {args.weo_edition})")
            continue

        outlooks[scenario_key] = outlook

        # Save individual files
        extractor.save_json(outlook, str(output_dir))
        extractor.save_csv(outlook, str(output_dir))

        # Validate
        print(f"\nValidation Report ({scenario_key}):")
        report = extractor.validate_outlook(outlook)
        print(f"  Coverage: {report['summary']['total_populated']}/"
              f"{report['summary']['total_mapped']} mapped variables "
              f"({report['summary']['coverage_pct']}%)")
        for tier, info in report["by_priority"].items():
            print(f"  {tier}: {info['populated']}/{info['total']} ({info['pct']}%)")
        if report["issues"]:
            print(f"  Issues: {len(report['issues'])}")
            for issue in report["issues"][:5]:
                print(f"    [{issue['level']}] {issue.get('variable', '')}: {issue['message']}")

    # Save combined CSV
    if len(outlooks) > 1:
        extractor.save_all_scenarios_csv(outlooks, str(output_dir))

    # Save validation report
    if outlooks:
        val_report = {s: extractor.validate_outlook(o) for s, o in outlooks.items()}
        val_path = output_dir / f"iea_weo_{extractor.weo_edition}_validation.json"
        with open(val_path, "w") as f:
            json.dump(val_report, f, indent=2, default=str)
        print(f"\nValidation report saved to {val_path}")

    print(f"\nDone. Output files in {output_dir}/")


if __name__ == "__main__":
    main()
