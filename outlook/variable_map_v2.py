"""
VARIABLE_MAP V2 — Exact Series ID Mapping for EIA AEO 2025
============================================================

Built from discovery output (eia_aeo_2025_discovery.json).
Uses exact seriesId matching instead of regex patterns.
AEO 2025 reports all real prices in 2024$ — no deflator needed.

Key conventions:
  - series_id: exact EIA seriesId string
  - series_id=None means no API series found; may use _static_fallback
  - dollar_year_adjust is False for all (AEO2025 already in 2024$)
  - unit_conversion handles unit differences between API and canonical schema
"""

from typing import Any

# Additional conversion entries beyond the base CONVERSION_TABLE
EXTRA_CONVERSIONS = {
    "mmbtu_to_mwh_electricity": lambda v: v * 3.41214,  # $/MMBtu → $/MWh for electricity
    "tbtu_to_ej": lambda v: v * 0.00105506,  # TBtu → EJ
    "tbtu_to_mt_h2": lambda v: v * 0.00105506 / 0.12,  # TBtu → Mt H2 (LHV: 120 MJ/kg = 0.12 EJ/Mt)
    "blnkwh_to_twh": lambda v: v * 1.0,  # identity (billion kWh = TWh)
    "billion_2012_to_trillion_2024": lambda v: v / 1000.0 * 1.35,  # rough 2012→2024 deflator ~1.35
    "mmmt_co2_to_mt": lambda v: v * 1.0,  # MMmt CO2 is already Mt CO2
    "per_capita_to_t": lambda v: v * 1.0,  # already t CO2/person (from MMmt CO2/capita × 1e6 / 1e6)
    "gal_to_galyr": lambda v: v * 1.0,  # placeholder for biofuel units
    "mmbpd_to_mbpd": lambda v: v * 1.0,  # MMb/d = Mb/d in EIA convention
    "mcf_to_mmbtu": lambda v: v / 1.037,  # $/Mcf → $/MMBtu
    "mill_tons_to_mt": lambda v: v * 0.9072,  # million short tons → million metric tonnes
    "mlspgln_to_mpge": lambda v: v * 1.0,  # mpg ≈ mpge for conventional
}


VARIABLE_MAP_V2: dict[str, dict[str, Any]] = {

    # =========================================================================
    # COMMODITY PRICES — OIL (Table 12)
    # =========================================================================
    "brent_crude_price": {
        "aeo_table": "12",
        "series_id": "prce_NA_NA_NA_cr_brntsppr_usa_y13dlrpbbl",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "wti_crude_price": {
        "aeo_table": "12",
        "series_id": "prce_NA_NA_NA_cr_wti_usa_y13dlrpbbl",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "motor_gasoline_price": {
        "aeo_table": "12",
        "series_id": "prce_NA_alls_NA_mgs_NA_usa_y13dlrpgln",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "diesel_price": {
        "aeo_table": "12",
        "series_id": "prce_NA_trn_NA_dfu_NA_usa_y13dlrpgln",  # Transportation diesel
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "jet_fuel_price": {
        "aeo_table": "12",
        "series_id": "prce_NA_trn_NA_jfl_NA_usa_y13dlrpgln",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # COMMODITY PRICES — NATURAL GAS (Tables 3, 13, 72)
    # =========================================================================
    "henry_hub_ng_price": {
        "aeo_table": "72",
        "series_id": "prce_sup_NA_NA_ng_NA_hhub_y13dlrpmmbtu",
        "unit_conversion": "none",  # already $/MMBtu
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "ng_price_residential": {
        "aeo_table": "3",
        "series_id": "prce_real_resd_NA_ng_NA_NA_y13dlrpmmbtu",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "ng_price_commercial": {
        "aeo_table": "3",
        "series_id": "prce_real_comm_NA_ng_NA_NA_y13dlrpmmbtu",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "ng_price_industrial": {
        "aeo_table": "3",
        "series_id": "prce_real_idal_NA_ng_NA_NA_y13dlrpmmbtu",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "ng_price_electric_power": {
        "aeo_table": "3",
        "series_id": "prce_real_elep_NA_ng_NA_NA_y13dlrpmmbtu",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "lng_export_price": {
        "aeo_table": "13",
        # No specific LNG export price series found — use delivered NG transportation
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # COMMODITY PRICES — COAL (Tables 3, 15)
    # =========================================================================
    "coal_minemouth_price": {
        "aeo_table": "15",
        "series_id": "prce_NA_NA_NA_cl_mnmth_NA_y13dlrptn",  # $/short ton
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "coal_delivered_power": {
        "aeo_table": "15",
        "series_id": "prce_NA_elep_NA_cl_NA_NA_y13dlrptn",  # $/short ton delivered to power
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # COMMODITY PRICES — ELECTRICITY (Table 8)
    # =========================================================================
    "electricity_price_residential": {
        "aeo_table": "8",
        "series_id": "prce_NA_resd_NA_edu_NA_usa_y13cntpkwh",
        "unit_conversion": "cents_kwh_to_usd_mwh",  # cents/kWh → $/MWh
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "electricity_price_commercial": {
        "aeo_table": "8",
        "series_id": "prce_NA_comm_NA_edu_NA_usa_y13cntpkwh",
        "unit_conversion": "cents_kwh_to_usd_mwh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "electricity_price_industrial": {
        "aeo_table": "8",
        "series_id": "prce_NA_idal_NA_edu_NA_usa_y13cntpkwh",
        "unit_conversion": "cents_kwh_to_usd_mwh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "electricity_price_wholesale": {
        "aeo_table": "8",
        "series_id": "prce_NA_elep_NA_gen_NA_usa_y13cntpkwh",  # Generation component ≈ wholesale
        "unit_conversion": "cents_kwh_to_usd_mwh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # COMMODITY PRICES — HYDROGEN (Tables 7, 73)
    # =========================================================================
    "hydrogen_price_delivered": {
        "aeo_table": "7",
        "series_id": "prce_NA_trn_NA_hdg_NA_NA_NA",  # 2024 $/kg delivered
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # ELECTRICITY — GENERATION (Table 8)
    # =========================================================================
    "generation_total": {
        "aeo_table": "8",
        "series_id": "gen_NA_elep_NA_teg_NA_usa_blnkwh",  # Total generation
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "generation_natural_gas": {
        "aeo_table": "8",
        "series_id": "gen_NA_elep_tge_ng_NA_usa_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "generation_coal": {
        "aeo_table": "8",
        "series_id": "gen_NA_elep_tge_cl_NA_usa_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "generation_nuclear": {
        "aeo_table": "8",
        "series_id": "gen_NA_elep_tge_nup_NA_usa_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "generation_wind": {
        "aeo_table": "16",
        "series_id": "gen_NA_alls_NA_wnd_NA_NA_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "generation_solar": {
        "aeo_table": "16",
        "series_id": "gen_NA_alls_NA_slr_NA_NA_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "generation_hydro": {
        "aeo_table": "16",
        "series_id": "gen_NA_alls_NA_hyd_cnv_NA_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "generation_biomass": {
        "aeo_table": "16",
        "series_id": "gen_NA_enus_NA_bms_NA_NA_blnkwh",  # End-use generators biomass
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 3,
    },
    "generation_geothermal": {
        "aeo_table": "16",
        "series_id": "gen_NA_alls_NA_geothm_NA_NA_blnkwh",
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 3,
    },
    "renewable_share_generation": {
        "aeo_table": "16",
        "series_id": None,  # Derived: total renewable gen / total gen
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_derived": "renewable_share",
    },

    # =========================================================================
    # ELECTRICITY — CAPACITY (Tables 9, 16)
    # =========================================================================
    "capacity_total": {
        "aeo_table": "9",
        "series_id": "cap_NA_elep_NA_tep_NA_usa_gw",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "capacity_solar_pv": {
        "aeo_table": "16",
        "series_id": "cap_NA_alls_NA_slr_NA_NA_gw",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "capacity_wind_onshore": {
        "aeo_table": "16",
        "series_id": "cap_nts_elep_NA_wnd_NA_NA_gw",  # Onshore wind (electric power sector)
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "capacity_wind_offshore": {
        "aeo_table": "16",
        "series_id": "cap_nts_elep_NA_wnd_ofs_NA_gw",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "capacity_battery_storage": {
        "aeo_table": "9",
        "series_id": "cap_NA_elep_pow_diurn_NA_usa_gw",  # Diurnal storage = battery
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "capacity_natural_gas": {
        "aeo_table": "9",
        # Combined: CC + CT + CHP gas. Use power-only CC + CT as proxy
        "series_id": "cap_NA_elep_pow_cmc_NA_usa_gw",  # Combined Cycle only for now
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        # TODO: sum CC + CT for total gas
    },
    "capacity_coal": {
        "aeo_table": "9",
        "series_id": "cap_NA_elep_pow_cl_NA_usa_gw",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "capacity_nuclear": {
        "aeo_table": "9",
        "series_id": "cap_NA_elep_pow_nup_NA_usa_gw",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "capacity_additions_annual": {
        "aeo_table": "9",
        "series_id": "cap_NA_elep_cuna_cep_NA_usa_gw",  # Cumulative unplanned additions total
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
        # Note: this is cumulative; annual additions = year-over-year delta
    },
    "capacity_retirements_annual": {
        "aeo_table": "9",
        "series_id": "cap_NA_elep_cre_tot_NA_usa_gw",  # Cumulative retirements total
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
        # Note: this is cumulative; annual retirements = year-over-year delta
    },

    # =========================================================================
    # ELECTRICITY — SYSTEM (Table 8)
    # =========================================================================
    "electricity_demand_total": {
        "aeo_table": "8",
        "series_id": "cnsm_NA_elep_NA_tel_NA_usa_blnkwh",  # Total electricity use
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "grid_emissions_intensity": {
        "aeo_table": "_derived",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_derived": "grid_emissions_intensity",
    },

    # =========================================================================
    # ENERGY DEMAND (Table 2)
    # =========================================================================
    "demand_total_primary": {
        "aeo_table": "2",
        "series_id": "cnsm_enu_ten_NA_tot_NA_NA_qbtu",  # Total energy use (quads)
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "demand_residential": {
        "aeo_table": "2",
        "series_id": "cnsm_enu_resd_NA_tot_NA_NA_qbtu",
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "demand_commercial": {
        "aeo_table": "2",
        "series_id": "cnsm_enu_comm_NA_tot_NA_NA_qbtu",
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "demand_industrial": {
        "aeo_table": "2",
        "series_id": "cnsm_enu_idal_NA_tot_NA_NA_qbtu",
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "demand_transport": {
        "aeo_table": "2",
        "series_id": "cnsm_enu_trn_NA_tot_NA_NA_qbtu",
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "demand_natural_gas": {
        "aeo_table": "1",
        "series_id": "cnsm_use_ten_NA_ng_NA_usa_qbtu",
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "demand_coal": {
        "aeo_table": "1",
        "series_id": "cnsm_use_ten_NA_cl_NA_usa_qbtu",
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "demand_electricity_total": {
        "aeo_table": "8",
        "series_id": "cnsm_NA_elep_NA_els_NA_usa_blnkwh",  # Total electricity sales
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "demand_hydrogen": {
        "aeo_table": "73",
        "series_id": "sup_NA_NA_NA_hdg_tot_NA_tbtu",  # Total H2 supply (≈demand) in TBtu
        "unit_conversion": "tbtu_to_mt_h2",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # FOSSIL FUEL SUPPLY / PRODUCTION (Tables 13, 14, 15)
    # =========================================================================
    "production_crude_oil_us": {
        "aeo_table": "14",
        "series_id": "sup_prd_NA_NA_cr_NA_usa_millbrlpdy",  # US crude production
        "unit_conversion": "none",  # already Mb/d
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "production_tight_oil_us": {
        "aeo_table": "14",
        "series_id": "sup_prd_ons_NA_cr_toil_l48on_millbrlpdy",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "production_dry_gas_us": {
        "aeo_table": "13",
        "series_id": "sup_prd_NA_NA_ng_dng_usa_trlcf",  # Dry gas production (Tcf)
        "unit_conversion": "tcf_yr_to_bcf_d",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "production_shale_gas_us": {
        "aeo_table": "14",
        "series_id": "sup_dpr_ons_NA_ng_tg_l48on_trlcf",  # Tight gas (proxy for shale) from Table 14
        "unit_conversion": "tcf_yr_to_bcf_d",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },
    "lng_exports_us": {
        "aeo_table": "13",
        "series_id": "cnsm_NA_lqfct_NA_ng_lqfct_usa_trlcf",  # Liquefaction for LNG export
        "unit_conversion": "tcf_yr_to_bcf_d",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "production_coal_us": {
        "aeo_table": "15",
        "series_id": "sup_prd_NA_NA_cl_prd_usa_millton",
        "unit_conversion": "none",  # million short tons → keep as is (Mt/yr ≈ Mst/yr)
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # HYDROGEN ECONOMY (Table 73)
    # =========================================================================
    "h2_production_total": {
        "aeo_table": "73",
        "series_id": "sup_NA_NA_NA_hdg_tot_NA_tbtu",
        "unit_conversion": "tbtu_to_mt_h2",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "h2_production_electrolysis": {
        "aeo_table": "73",
        "series_id": "sup_prd_NA_NA_hdg_grdelec_NA_tbtu",
        "unit_conversion": "tbtu_to_mt_h2",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "h2_production_smr": {
        "aeo_table": "73",
        "series_id": "sup_prd_NA_NA_hdg_smr_NA_tbtu",
        "unit_conversion": "tbtu_to_mt_h2",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "h2_production_smr_ccs": {
        "aeo_table": "73",
        "series_id": "sup_prd_NA_NA_hdg_smrccs_NA_tbtu",
        "unit_conversion": "tbtu_to_mt_h2",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "h2_carbon_intensity_by_pathway": {
        "aeo_table": "73",
        "series_id": None,  # Not directly available as a series
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # EMISSIONS (Tables 2, 17)
    # =========================================================================
    "emissions_co2_total": {
        "aeo_table": "17",
        "series_id": "emi_co2_ten_NA_tot_NA_NA_millmetnco2",  # Total by Fuel: Total CO2
        "unit_conversion": "none",  # already MMmt CO2 = Mt CO2
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "emissions_co2_power": {
        "aeo_table": "17",
        "series_id": "emi_co2_elep_NA_tot_NA_NA_millmetnco2",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "emissions_co2_industry": {
        "aeo_table": "17",
        "series_id": "emi_co2_idal_NA_tot_NA_NA_millmetnco2",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "emissions_co2_transport": {
        "aeo_table": "17",
        "series_id": "emi_co2_trn_NA_tot_NA_NA_millmetnco2",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "emissions_co2_buildings": {
        "aeo_table": "17",
        # Residential + commercial combined
        "series_id": None,  # Need to sum resd + comm
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
        "_sum_series": [
            "emi_co2_resd_NA_tot_NA_NA_millmetnco2",
            "emi_co2_comm_NA_tot_NA_NA_millmetnco2",
        ],
    },
    "emissions_co2_per_capita": {
        "aeo_table": "17",
        "series_id": "emi_co2_NA_NA_NA_NA_NA_millmtco2pp",  # Per-Capita CO2 (national)
        "unit_conversion": "none",  # units appear as MMmt CO2/capita but value is t CO2/person
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # TRANSPORT ELECTRIFICATION (Table 7)
    # =========================================================================
    "ev_battery_pack_cost": {
        "aeo_table": "7",
        "series_id": "prce_NA_trn_NA_bat_ldv_NA_13ydkwh",  # Light-duty BEV battery $/kWh
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "new_vehicle_fuel_economy": {
        "aeo_table": "7",
        "series_id": "efi_cafeo_trn_hwy_ldty_new_NA_mlspgln",  # On-road new LDV mpg
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # MACROECONOMIC (Tables 2, 18, 20)
    # =========================================================================
    "gdp_real": {
        "aeo_table": "2",
        "series_id": "kei_gdp_NA_NA_NA_NA_NA_blny09dlr",  # GDP in billion 2012$
        "unit_conversion": "billion_2012_to_trillion_2024",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "population": {
        "aeo_table": "2",
        "series_id": "dmg_pop_NA_NA_NA_NA_NA_mill",
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # GULF COAST / WSC REGIONAL (Table 3)
    # =========================================================================
    "gulf_coast_industrial_ng_price": {
        "aeo_table": "3",
        "series_id": "prce_real_idal_NA_ng_NA_wsc_y13dlrpmmbtu",
        "unit_conversion": "none",  # already $/MMBtu
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },
    "gulf_coast_industrial_electricity_price": {
        "aeo_table": "3",
        "series_id": "prce_real_idal_NA_elc_NA_wsc_y13dlrpmmbtu",
        "unit_conversion": "mmbtu_to_mwh_electricity",  # $/MMBtu → $/MWh
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # POLICY — STATIC FALLBACKS (no API series)
    # =========================================================================
    "ira_itc_rate": {
        "aeo_table": "_static",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_static_fallback": {yr: 0.30 for yr in range(2024, 2033)} | {yr: 0.0 for yr in range(2033, 2051)},
    },
    "ira_ptc_value": {
        "aeo_table": "_static",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_static_fallback": {yr: 27.50 for yr in range(2024, 2033)} | {yr: 0.0 for yr in range(2033, 2051)},
    },
    "ira_45v_h2_ptc": {
        "aeo_table": "_static",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_static_fallback": {yr: 3.00 for yr in range(2024, 2033)} | {yr: 0.0 for yr in range(2033, 2051)},
    },
    "ira_45q_ccs_credit": {
        "aeo_table": "_static",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_static_fallback": {yr: 85.0 for yr in range(2024, 2051)},
    },
    "rps_target": {
        "aeo_table": "_static",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
        "_static_fallback": {},
    },

    # =========================================================================
    # NEW — OIL DEMAND (Table 1)
    # =========================================================================
    "demand_oil": {
        "aeo_table": "1",
        "series_id": "cnsm_use_ten_NA_lfl_NA_usa_qbtu",  # Total Liquid Fuels use
        "unit_conversion": "quads_to_ej",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # NEW — TRANSPORT ELECTRICITY (Table 8)
    # =========================================================================
    "transport_electricity_demand": {
        "aeo_table": "8",
        "series_id": "cnsm_NA_trn_NA_els_NA_usa_blnkwh",  # Electricity Sales to Transport
        "unit_conversion": "blnkwh_to_twh",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # NEW — ENERGY INTENSITY (Table 18)
    # =========================================================================
    "energy_intensity_gdp": {
        "aeo_table": "18",
        "series_id": "iny_NA_NA_NA_ten_NA_NA_thbtupdlrgdp",  # thousand Btu per $ GDP
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
    },

    # =========================================================================
    # NEW — SOLAR CAPACITY FACTOR (derived from Table 16)
    # =========================================================================
    "solar_pv_utility_cf": {
        "aeo_table": "_derived",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_derived": "solar_pv_utility_cf",
    },

    # =========================================================================
    # NEW — GDP GROWTH RATE (derived from GDP level)
    # =========================================================================
    "gdp_growth_rate": {
        "aeo_table": "_derived",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_derived": "gdp_growth_rate",
    },

    # =========================================================================
    # NEW — CO2 PER GDP (derived)
    # =========================================================================
    "emissions_co2_per_gdp": {
        "aeo_table": "_derived",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 2,
        "_derived": "emissions_co2_per_gdp",
    },

    # =========================================================================
    # NEW — EV SALES (Table 48) — derived share from BEV+PHEV/Total
    # =========================================================================
    "ev_sales_share_ldv": {
        "aeo_table": "_derived",
        "series_id": None,
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
        "_derived": "ev_sales_share_ldv",
        "_source_series": {
            "bev": "eci_sal_trn_ldty_ele_NA_NA_th",
            "phev": "eci_sal_trn_ldty_pie_NA_NA_th",
            "total": "eci_sal_trn_ldty_NA_NA_NA_th",
        },
        "_source_table": "48",
    },

    # =========================================================================
    # NEW — EV STOCK (Table 49)
    # =========================================================================
    "ev_stock_ldv": {
        "aeo_table": "49",
        "series_id": "eci_stk_trn_ldv_ele_NA_NA_mill",  # BEV stock in millions
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 1,
    },

    # =========================================================================
    # NEW — FCEV STOCK (Table 49)
    # =========================================================================
    "fcev_stock": {
        "aeo_table": "49",
        "series_id": "eci_stk_trn_ldv_fuc_NA_NA_mill",  # Fuel cell stock in millions
        "unit_conversion": "none",
        "dollar_year_adjust": False,
        "aggregate": None,
        "priority": 3,
    },
}


def get_tables_used() -> set:
    """Return the set of AEO table IDs needed for extraction."""
    return set(
        spec["aeo_table"] for spec in VARIABLE_MAP_V2.values()
        if spec["aeo_table"] and not spec["aeo_table"].startswith("_")
    )


def get_wsc_tables() -> set:
    """Return tables that need WSC-specific queries."""
    wsc = set()
    for spec in VARIABLE_MAP_V2.values():
        sid = spec.get("series_id") or ""
        if "_wsc_" in sid:
            wsc.add(spec["aeo_table"])
    return wsc


if __name__ == "__main__":
    tables = get_tables_used()
    wsc = get_wsc_tables()
    total = len(VARIABLE_MAP_V2)
    mapped = sum(1 for s in VARIABLE_MAP_V2.values() if s.get("series_id"))
    static = sum(1 for s in VARIABLE_MAP_V2.values() if "_static_fallback" in s)
    derived = sum(1 for s in VARIABLE_MAP_V2.values() if "_derived" in s or "_sum_series" in s)

    print(f"VARIABLE_MAP_V2: {total} variables")
    print(f"  Direct API match: {mapped}")
    print(f"  Static fallback:  {static}")
    print(f"  Derived/sum:      {derived}")
    print(f"  Unmapped:         {total - mapped - static - derived}")
    print(f"\nTables needed: {sorted(tables)}")
    print(f"WSC tables:    {sorted(wsc)}")
