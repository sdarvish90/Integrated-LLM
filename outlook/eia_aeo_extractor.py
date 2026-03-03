"""
DecarbIQ EIA AEO Projection Extractor
=======================================

Extracts Annual Energy Outlook projection data from the EIA API v2 and
populates DecarbIQ canonical outlook structures for scenario comparison
and Monte Carlo simulation.

Supports:
  - All 9 AEO2025 scenarios (Reference + 8 side cases)
  - 77 canonical variables mapped to AEO tables (exact series ID matching)
  - AEO 2025 already reports in 2024$ — no dollar-year deflation needed
  - Table-batched API queries for efficiency
  - Discovery mode for series ID validation
  - Priority-tiered validation reporting
  - Cross-scenario correlation extraction

Usage:
  python3 eia_aeo_extractor.py --discover                   # Discovery first
  python3 eia_aeo_extractor.py --scenarios reference         # Single scenario
  python3 eia_aeo_extractor.py                               # All 9 scenarios
  python3 eia_aeo_extractor.py --verbose                     # Debug logging

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-03-02
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

# Local import — canonical schema
sys.path.insert(0, str(Path(__file__).parent))
from energy_outlook_canonical_schema import (
    VARIABLE_CATALOG,
    SOURCE_REGISTRY,
    UNIT_CONVERSIONS,
    build_empty_outlook,
    get_variables_for_source,
    get_variables_by_priority,
)

logger = logging.getLogger("eia_aeo_extractor")


# =============================================================================
# SCENARIO MAP: canonical name → EIA API scenario ID
# =============================================================================
SCENARIO_MAP = {
    "reference":            "ref2025",
    "high_oil_price":       "highprice",
    "low_oil_price":        "lowprice",
    "high_oil_gas_supply":  "highogs",
    "low_oil_gas_supply":   "lowogs",
    "high_ztc":             "highZTC",
    "low_ztc":              "lowZTC",
    "high_economic_growth": "hm2025",
    "low_economic_growth":  "lm2025",
}


# =============================================================================
# UNIT CONVERSION DISPATCH TABLE
# =============================================================================
CONVERSION_TABLE = {
    "cents_kwh_to_usd_mwh":       lambda v: v * 10.0,
    "mills_kwh_to_usd_mwh":       lambda v: v * 1.0,
    "quads_to_ej":                 lambda v: v * 1.05506,
    "billion_kwh_to_twh":          lambda v: v * 1.0,        # identity
    "btu_kwh_to_mmbtu_mwh":       lambda v: v / 1000.0,
    "percent_to_fraction":         lambda v: v / 100.0,
    "billion_usd_to_trillion_usd": lambda v: v / 1000.0,
    "tcf_yr_to_bcf_d":            lambda v: v * 1000.0 / 365.0,
    "million_short_tons_to_mt":    lambda v: v * 0.9072,
    "mcf_to_mmbtu":               lambda v: v / 1.037,
    "thousand_btu_usd_to_mj_usd": lambda v: v * 1.05506,
    "none":                        lambda v: v,              # no conversion
}


# =============================================================================
# VARIABLE MAP — imported from variable_map_v2 (exact series ID matching)
# =============================================================================
from variable_map_v2 import VARIABLE_MAP_V2, EXTRA_CONVERSIONS, get_tables_used, get_wsc_tables

VARIABLE_MAP = VARIABLE_MAP_V2  # alias for backward compat
CONVERSION_TABLE.update(EXTRA_CONVERSIONS)


# =============================================================================
# MAIN EXTRACTOR CLASS
# =============================================================================

class EIAAEOExtractor:
    """
    Extracts AEO projection data from EIA API v2 into DecarbIQ canonical
    outlook structures.
    """

    BASE_URL_TEMPLATE = "https://api.eia.gov/v2/aeo/{year}/data/"
    MAX_REQUESTS_PER_SEC = 4
    PAGE_SIZE = 5000
    MAX_RETRIES = 5

    def __init__(
        self,
        api_key: Optional[str] = None,
        aeo_year: int = 2025,
        start_year: int = 2024,
        end_year: int = 2050,
        deflator_override: Optional[float] = None,
    ):
        self.api_key = api_key or os.environ.get("EIA_API_KEY")
        if not self.api_key:
            raise ValueError(
                "EIA API key required. Set EIA_API_KEY env var or pass --api-key. "
                "Get a free key: https://www.eia.gov/opendata/register.php"
            )
        self.aeo_year = aeo_year
        self.start_year = start_year
        self.end_year = end_year
        self.base_url = self.BASE_URL_TEMPLATE.format(year=aeo_year)
        self._request_timestamps: list[float] = []

        # Dollar-year deflator — AEO 2025 already reports in 2024$, so 1.0
        self.deflator = deflator_override if deflator_override is not None else 1.0
        if self.deflator != 1.0:
            logger.info(f"Using custom deflator: {self.deflator}")
        else:
            logger.info("AEO 2025 reports in 2024$ — no deflation needed (deflator=1.0)")

    def _adjust_dollar_year(self, value: Optional[float]) -> Optional[float]:
        """Apply deflator if needed. AEO 2025 is already in 2024$, so this is a no-op by default."""
        if value is None:
            return None
        return value * self.deflator

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------
    def _rate_limit(self):
        """Enforce max requests per second."""
        now = time.monotonic()
        # Clean old timestamps
        self._request_timestamps = [
            t for t in self._request_timestamps
            if now - t < 1.0
        ]
        if len(self._request_timestamps) >= self.MAX_REQUESTS_PER_SEC:
            sleep_time = 1.0 - (now - self._request_timestamps[0])
            if sleep_time > 0:
                time.sleep(sleep_time)
        self._request_timestamps.append(time.monotonic())

    # -------------------------------------------------------------------------
    # API requests
    # -------------------------------------------------------------------------
    def _make_request(self, params: dict, max_retries: int = None) -> Optional[dict]:
        """Make a single API request with retry and backoff."""
        max_retries = max_retries or self.MAX_RETRIES
        params["api_key"] = self.api_key

        for attempt in range(max_retries):
            self._rate_limit()
            try:
                resp = requests.get(self.base_url, params=params, timeout=90)

                if resp.status_code == 429:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"Rate limited (429). Waiting {wait}s...")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                return resp.json()

            except requests.exceptions.RequestException as e:
                wait = 3 ** attempt  # 1, 3, 9, 27, 81s backoff
                logger.warning(f"Request failed (attempt {attempt+1}/{max_retries}): {e}. Retrying in {wait}s...")
                time.sleep(wait)

        logger.error(f"Request failed after {max_retries} attempts")
        return None

    def _fetch_paginated(self, params: dict) -> list[dict]:
        """Fetch all records with pagination."""
        all_records = []
        offset = 0
        params["length"] = self.PAGE_SIZE

        while True:
            params["offset"] = offset
            result = self._make_request(params)

            if result is None:
                break

            response = result.get("response", {})
            data = response.get("data", [])

            if not data:
                break

            all_records.extend(data)
            total = int(response.get("total", 0))

            if len(data) < self.PAGE_SIZE or len(all_records) >= total:
                break

            offset += self.PAGE_SIZE

        return all_records

    # -------------------------------------------------------------------------
    # Discovery mode
    # -------------------------------------------------------------------------
    def run_discovery(self, output_dir: str):
        """Fetch all series names per table for reference scenario. Save to JSON."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario_id = SCENARIO_MAP["reference"]
        tables_to_discover = sorted(set(
            spec["aeo_table"] for spec in VARIABLE_MAP.values()
            if not spec["aeo_table"].startswith("_")
        ))

        discovery = {}

        for table_id in tables_to_discover:
            logger.info(f"Discovering table {table_id}...")
            params = {
                "frequency": "annual",
                "data[0]": "value",
                "facets[scenario][]": scenario_id,
                "facets[tableId][]": table_id,
                "start": str(self.start_year),
                "end": str(self.start_year),  # just one year for discovery
                "sort[0][column]": "seriesId",
                "sort[0][direction]": "asc",
            }
            records = self._fetch_paginated(params)

            # Extract unique series
            series_set = {}
            for rec in records:
                sid = rec.get("seriesId", rec.get("series", "unknown"))
                sname = rec.get("seriesName", rec.get("seriesDescription", sid))
                unit = rec.get("unit", rec.get("units", ""))
                geo = rec.get("geography", rec.get("region", ""))
                key = f"{sid}|{geo}"
                if key not in series_set:
                    series_set[key] = {
                        "seriesId": sid,
                        "seriesName": sname,
                        "unit": unit,
                        "geography": geo,
                        "sample_value": rec.get("value"),
                    }

            discovery[table_id] = {
                "total_records": len(records),
                "unique_series": len(series_set),
                "series": list(series_set.values()),
            }

            logger.info(f"  Table {table_id}: {len(records)} records, {len(series_set)} unique series")

        # Save discovery output
        disc_file = output_path / f"eia_aeo_{self.aeo_year}_discovery.json"
        with open(disc_file, "w") as f:
            json.dump(discovery, f, indent=2, default=str)

        logger.info(f"Discovery saved to {disc_file}")
        return discovery

    # -------------------------------------------------------------------------
    # Core extraction
    # -------------------------------------------------------------------------
    def _match_series(self, record: dict, spec: dict) -> bool:
        """Check if an API record matches a variable's exact series ID."""
        target_sid = spec.get("series_id")
        if not target_sid:
            return False
        return str(record.get("seriesId", "")) == target_sid

    def _convert_value(self, value: Any, spec: dict) -> Optional[float]:
        """Convert a raw API value through unit conversion and dollar-year adjustment."""
        if value is None or value == "--" or value == "":
            return None
        try:
            val = float(value)
        except (ValueError, TypeError):
            return None

        # Unit conversion
        conv_name = spec.get("unit_conversion", "none")
        if conv_name in CONVERSION_TABLE:
            val = CONVERSION_TABLE[conv_name](val)

        # Dollar-year adjustment
        if spec.get("dollar_year_adjust", False):
            val = self._adjust_dollar_year(val)

        return round(val, 6)

    def _match_and_populate(
        self,
        outlook: dict,
        var_key: str,
        spec: dict,
        records: list[dict],
    ):
        """Match records to a variable spec and populate the outlook."""
        if var_key not in outlook["variables"]:
            return

        # Handle _sum_series: sum multiple specific series IDs
        sum_ids = spec.get("_sum_series")
        if sum_ids:
            by_year: dict[str, float] = defaultdict(float)
            found_any = False
            for sid in sum_ids:
                for rec in records:
                    if str(rec.get("seriesId", "")) == sid:
                        found_any = True
                        yr = str(rec.get("period", ""))
                        val = self._convert_value(rec.get("value"), spec)
                        if val is not None and yr:
                            by_year[yr] += val
            if found_any:
                outlook["variables"][var_key]["annual"] = dict(by_year)
                logger.info(f"  {var_key}: {len(by_year)} years populated (summed {len(sum_ids)} series)")
            else:
                logger.warning(f"  {var_key}: no matching series found for _sum_series")
            return

        # Standard exact series_id matching
        matching = [r for r in records if self._match_series(r, spec)]

        if not matching:
            # Try static fallback for policy variables
            if "_static_fallback" in spec:
                outlook["variables"][var_key]["annual"] = {
                    str(yr): val for yr, val in spec["_static_fallback"].items()
                    if self.start_year <= yr <= self.end_year
                }
                logger.info(f"  {var_key}: populated from static fallback ({len(outlook['variables'][var_key]['annual'])} years)")
                return
            logger.warning(f"  {var_key}: no matching series found")
            return

        annual: dict[str, float] = {}
        for rec in matching:
            yr = str(rec.get("period", ""))
            val = self._convert_value(rec.get("value"), spec)
            if val is not None and yr:
                annual[yr] = val

        outlook["variables"][var_key]["annual"] = annual
        logger.info(f"  {var_key}: {len(annual)} years populated")

    def _compute_derived(self, outlook: dict):
        """Compute derived variables after all direct variables are populated."""
        vars_ = outlook["variables"]

        # grid_emissions_intensity = emissions_co2_power / generation_total * 1e3
        if "grid_emissions_intensity" in vars_:
            power_em = vars_.get("emissions_co2_power", {}).get("annual", {})
            gen_total = vars_.get("generation_total", {}).get("annual", {})
            intensity = {}
            for yr in power_em:
                if yr in gen_total and gen_total[yr] and gen_total[yr] > 0:
                    intensity[yr] = round(power_em[yr] / gen_total[yr] * 1e3, 2)
            vars_["grid_emissions_intensity"]["annual"] = intensity
            logger.info(f"  grid_emissions_intensity: derived for {len(intensity)} years")

        # renewable_share_generation = (solar + wind + hydro + biomass + geo) / total
        if "renewable_share_generation" in vars_:
            gen_total_data = vars_.get("generation_total", {}).get("annual", {})
            ren_keys = ["generation_solar", "generation_wind", "generation_hydro",
                        "generation_biomass", "generation_geothermal"]
            share = {}
            for yr in gen_total_data:
                if gen_total_data[yr] and gen_total_data[yr] > 0:
                    ren_sum = sum(
                        vars_.get(k, {}).get("annual", {}).get(yr, 0) or 0
                        for k in ren_keys
                    )
                    share[yr] = round(ren_sum / gen_total_data[yr], 4)
            vars_["renewable_share_generation"]["annual"] = share
            logger.info(f"  renewable_share_generation: derived for {len(share)} years")

        # solar_pv_utility_cf = generation_solar (TWh) / (capacity_solar_pv (GW) * 8.760)
        if "solar_pv_utility_cf" in vars_:
            gen_solar = vars_.get("generation_solar", {}).get("annual", {})
            cap_solar = vars_.get("capacity_solar_pv", {}).get("annual", {})
            cf = {}
            for yr in gen_solar:
                if yr in cap_solar and cap_solar[yr] and cap_solar[yr] > 0:
                    # TWh / (GW * 8760 h) = TWh / (GW * 8.760 TWh/GW-yr)
                    cf[yr] = round(gen_solar[yr] / (cap_solar[yr] * 8.760), 4)
            vars_["solar_pv_utility_cf"]["annual"] = cf
            logger.info(f"  solar_pv_utility_cf: derived for {len(cf)} years")

        # gdp_growth_rate = (GDP[yr] / GDP[yr-1]) - 1
        if "gdp_growth_rate" in vars_:
            gdp_data = vars_.get("gdp_real", {}).get("annual", {})
            growth = {}
            sorted_yrs = sorted(gdp_data.keys())
            for i in range(1, len(sorted_yrs)):
                yr = sorted_yrs[i]
                prev_yr = sorted_yrs[i - 1]
                if gdp_data.get(prev_yr) and gdp_data[prev_yr] > 0:
                    growth[yr] = round((gdp_data[yr] / gdp_data[prev_yr]) - 1, 6)
            vars_["gdp_growth_rate"]["annual"] = growth
            logger.info(f"  gdp_growth_rate: derived for {len(growth)} years")

        # emissions_co2_per_gdp = emissions_co2_total (Mt) / gdp_real (T$) → Mt/$T = kg/$
        if "emissions_co2_per_gdp" in vars_:
            co2 = vars_.get("emissions_co2_total", {}).get("annual", {})
            gdp = vars_.get("gdp_real", {}).get("annual", {})
            ratio = {}
            for yr in co2:
                if yr in gdp and gdp[yr] and gdp[yr] > 0:
                    ratio[yr] = round(co2[yr] / gdp[yr], 4)
            vars_["emissions_co2_per_gdp"]["annual"] = ratio
            logger.info(f"  emissions_co2_per_gdp: derived for {len(ratio)} years")

    def _compute_ev_sales_share(self, outlook: dict, scenario_id: str):
        """Derive EV sales share from Table 48 (BEV + PHEV) / Total."""
        spec = VARIABLE_MAP.get("ev_sales_share_ldv", {})
        source = spec.get("_source_series", {})
        source_table = spec.get("_source_table", "48")
        if not source:
            return

        logger.info(f"Fetching table {source_table} for EV sales share")
        params = {
            "frequency": "annual",
            "data[0]": "value",
            "facets[scenario][]": scenario_id,
            "facets[tableId][]": source_table,
            "start": str(self.start_year),
            "end": str(self.end_year),
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        }
        records = self._fetch_paginated(params)
        if not records:
            logger.warning("  ev_sales_share_ldv: no Table 48 data")
            return

        # Collect by series and year
        series_data: dict[str, dict[str, float]] = defaultdict(dict)
        for rec in records:
            sid = str(rec.get("seriesId", ""))
            yr = str(rec.get("period", ""))
            val = rec.get("value")
            if val and yr:
                try:
                    series_data[sid][yr] = float(val)
                except (ValueError, TypeError):
                    pass

        bev = series_data.get(source["bev"], {})
        phev = series_data.get(source["phev"], {})
        total = series_data.get(source["total"], {})

        share = {}
        for yr in total:
            if total[yr] and total[yr] > 0:
                ev_total = (bev.get(yr, 0) or 0) + (phev.get(yr, 0) or 0)
                share[yr] = round(ev_total / total[yr], 4)

        outlook["variables"]["ev_sales_share_ldv"]["annual"] = share
        logger.info(f"  ev_sales_share_ldv: derived for {len(share)} years")

    def extract_scenario(self, scenario_name: str) -> dict:
        """Extract all variables for a single scenario."""
        scenario_id = SCENARIO_MAP.get(scenario_name)
        if not scenario_id:
            raise ValueError(f"Unknown scenario: {scenario_name}. Available: {list(SCENARIO_MAP.keys())}")

        logger.info(f"\n{'='*60}")
        logger.info(f"Extracting scenario: {scenario_name} ({scenario_id})")
        logger.info(f"{'='*60}")

        outlook = build_empty_outlook("eia_aeo", scenario_name, self.start_year, self.end_year)

        # Set extraction metadata
        outlook["extraction_metadata"] = {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": "api",
            "confidence_score": 0.99,
            "source_document": f"https://api.eia.gov/v2/aeo/{self.aeo_year}/data/",
            "notes": f"AEO{self.aeo_year} extracted via EIA API v2. All values in real 2024$.",
        }

        # Group variables by table for batch queries
        table_groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)
        derived_vars = []
        static_vars = []

        for var_key, spec in VARIABLE_MAP.items():
            if var_key not in outlook["variables"]:
                continue
            aeo_table = spec["aeo_table"]
            if aeo_table == "_derived":
                derived_vars.append((var_key, spec))
            elif aeo_table == "_static":
                static_vars.append((var_key, spec))
            else:
                table_groups[aeo_table].append((var_key, spec))

        # Populate static fallback variables (IRA credits, etc.)
        for var_key, spec in static_vars:
            self._match_and_populate(outlook, var_key, spec, [])

        # Fetch and populate table-batched variables
        for table_id, var_specs in sorted(table_groups.items()):
            logger.info(f"Fetching table {table_id} — {len(var_specs)} variables")
            params = {
                "frequency": "annual",
                "data[0]": "value",
                "facets[scenario][]": scenario_id,
                "facets[tableId][]": table_id,
                "start": str(self.start_year),
                "end": str(self.end_year),
                "sort[0][column]": "period",
                "sort[0][direction]": "asc",
            }

            records = self._fetch_paginated(params)

            if not records:
                logger.warning(f"  No data returned for table {table_id}")
                continue

            for var_key, spec in var_specs:
                self._match_and_populate(outlook, var_key, spec, records)

        # Compute derived variables (from already-populated data)
        self._compute_derived(outlook)

        # EV sales share — requires separate Table 48 fetch
        if "ev_sales_share_ldv" in outlook["variables"]:
            self._compute_ev_sales_share(outlook, scenario_id)

        return outlook

    def extract_all_scenarios(self) -> dict[str, dict]:
        """Extract all scenarios."""
        results = {}
        for scenario_name in SCENARIO_MAP:
            results[scenario_name] = self.extract_scenario(scenario_name)
        return results

    # -------------------------------------------------------------------------
    # Output
    # -------------------------------------------------------------------------
    def save_json(self, outlook: dict, output_dir: str):
        """Save a single scenario outlook as canonical JSON."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario = outlook["scenario_name"]
        filename = f"eia_aeo_{self.aeo_year}_{scenario}.json"
        filepath = output_path / filename

        with open(filepath, "w") as f:
            json.dump(outlook, f, indent=2, default=str)

        logger.info(f"Saved JSON: {filepath}")

    def save_csv(self, outlook: dict, output_dir: str):
        """Save a single scenario as flat CSV."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario = outlook["scenario_name"]
        filename = f"eia_aeo_{self.aeo_year}_{scenario}.csv"
        filepath = output_path / filename

        rows = []
        years = [str(y) for y in range(self.start_year, self.end_year + 1)]

        for var_key, var_data in outlook["variables"].items():
            row = {
                "variable": var_key,
                "unit": var_data.get("unit", ""),
                "category": var_data.get("category", ""),
                "description": var_data.get("description", ""),
            }
            annual = var_data.get("annual", {})
            for yr in years:
                row[yr] = annual.get(yr, annual.get(int(yr), ""))
            rows.append(row)

        # Write CSV manually to avoid pandas dependency for saving
        headers = ["variable", "unit", "category", "description"] + years
        with open(filepath, "w") as f:
            f.write(",".join(headers) + "\n")
            for row in rows:
                values = [str(row.get(h, "")).replace(",", ";") for h in headers]
                f.write(",".join(values) + "\n")

        logger.info(f"Saved CSV: {filepath}")

    def save_all(self, all_outlooks: dict[str, dict], output_dir: str):
        """Save all scenarios as individual JSON/CSV + combined CSV."""
        for scenario_name, outlook in all_outlooks.items():
            self.save_json(outlook, output_dir)
            self.save_csv(outlook, output_dir)

        # Combined CSV
        output_path = Path(output_dir)
        combined_file = output_path / f"eia_aeo_{self.aeo_year}_all_scenarios.csv"
        years = [str(y) for y in range(self.start_year, self.end_year + 1)]
        headers = ["scenario", "variable", "unit", "category"] + years

        with open(combined_file, "w") as f:
            f.write(",".join(headers) + "\n")
            for scenario_name, outlook in all_outlooks.items():
                for var_key, var_data in outlook["variables"].items():
                    annual = var_data.get("annual", {})
                    row_vals = [
                        scenario_name,
                        var_key,
                        var_data.get("unit", "").replace(",", ";"),
                        var_data.get("category", ""),
                    ]
                    for yr in years:
                        row_vals.append(str(annual.get(yr, annual.get(int(yr), ""))))
                    f.write(",".join(row_vals) + "\n")

        logger.info(f"Saved combined CSV: {combined_file}")

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------
    def validate_outlook(self, outlook: dict) -> dict:
        """Validate completeness and quality by priority tier."""
        scenario = outlook["scenario_name"]
        total_vars = len(outlook["variables"])
        populated = 0
        empty = 0
        partial = 0

        p1_total = 0
        p1_populated = 0
        p2_total = 0
        p2_populated = 0
        p3_total = 0
        p3_populated = 0

        issues = []
        expected_years = self.end_year - self.start_year + 1

        for var_key, var_data in outlook["variables"].items():
            annual = var_data.get("annual", {})
            priority = VARIABLE_CATALOG.get(var_key, {}).get("priority", 3)

            # Count by priority
            if priority == 1:
                p1_total += 1
            elif priority == 2:
                p2_total += 1
            else:
                p3_total += 1

            if not annual:
                empty += 1
                if priority == 1:
                    issues.append({"level": "ERROR", "var": var_key, "msg": "P1 variable empty"})
                elif priority == 2:
                    issues.append({"level": "WARNING", "var": var_key, "msg": "P2 variable empty"})
            elif len(annual) < expected_years * 0.8:
                partial += 1
                issues.append({
                    "level": "WARNING",
                    "var": var_key,
                    "msg": f"Only {len(annual)}/{expected_years} years populated",
                })
            else:
                populated += 1
                if priority == 1:
                    p1_populated += 1
                elif priority == 2:
                    p2_populated += 1
                else:
                    p3_populated += 1

        report = {
            "scenario": scenario,
            "total_variables": total_vars,
            "populated": populated,
            "partial": partial,
            "empty": empty,
            "completeness_pct": round(populated / total_vars * 100, 1) if total_vars else 0,
            "priority_1": {
                "total": p1_total,
                "populated": p1_populated,
                "pct": round(p1_populated / p1_total * 100, 1) if p1_total else 0,
                "target": "95%",
            },
            "priority_2": {
                "total": p2_total,
                "populated": p2_populated,
                "pct": round(p2_populated / p2_total * 100, 1) if p2_total else 0,
                "target": "80%",
            },
            "priority_3": {
                "total": p3_total,
                "populated": p3_populated,
                "pct": round(p3_populated / p3_total * 100, 1) if p3_total else 0,
                "target": "N/A",
            },
            "issues": issues,
        }

        # Log summary
        logger.info(f"\nValidation — {scenario}:")
        logger.info(f"  Total: {populated}/{total_vars} fully populated ({report['completeness_pct']}%)")
        logger.info(f"  P1: {p1_populated}/{p1_total} ({report['priority_1']['pct']}%) — target 95%")
        logger.info(f"  P2: {p2_populated}/{p2_total} ({report['priority_2']['pct']}%) — target 80%")
        logger.info(f"  P3: {p3_populated}/{p3_total} ({report['priority_3']['pct']}%)")
        if issues:
            for issue in issues[:10]:
                logger.info(f"  [{issue['level']}] {issue['var']}: {issue['msg']}")
            if len(issues) > 10:
                logger.info(f"  ... and {len(issues) - 10} more issues")

        return report


# =============================================================================
# CROSS-SCENARIO CORRELATION EXTRACTION
# =============================================================================

def compute_cross_scenario_correlations(
    all_outlooks: dict[str, dict],
    var_pairs: Optional[list[tuple[str, str]]] = None,
) -> dict:
    """
    Compute implied correlations across scenarios for key variable pairs.

    For each year, correlates how variables co-move across the 9 AEO scenarios.
    Output format: {year: {(var1, var2): correlation_value}}
    Directly ingestible by Monte Carlo copula calibration.
    """
    if var_pairs is None:
        var_pairs = [
            ("henry_hub_ng_price", "electricity_price_wholesale"),
            ("henry_hub_ng_price", "electricity_price_industrial"),
            ("henry_hub_ng_price", "generation_natural_gas"),
            ("brent_crude_price", "henry_hub_ng_price"),
            ("henry_hub_ng_price", "emissions_co2_power"),
            ("henry_hub_ng_price", "lcoh_gray_smr"),
            ("electricity_price_industrial", "lcoh_green_electrolysis"),
            ("generation_solar", "electricity_price_wholesale"),
            ("gdp_real", "demand_total_primary"),
            ("gdp_real", "emissions_co2_total"),
        ]

    scenarios = list(all_outlooks.keys())
    if len(scenarios) < 3:
        logger.warning("Need at least 3 scenarios for meaningful correlations")
        return {}

    correlations: dict[str, dict] = {}

    # Get all years from reference
    ref = all_outlooks.get("reference", next(iter(all_outlooks.values())))
    sample_var = next(iter(ref["variables"].values()), {})
    years = sorted(sample_var.get("annual", {}).keys())

    for yr in years:
        yr_corrs = {}
        for var1, var2 in var_pairs:
            vals1 = []
            vals2 = []
            for scenario_name, outlook in all_outlooks.items():
                v1 = outlook.get("variables", {}).get(var1, {}).get("annual", {}).get(yr)
                v2 = outlook.get("variables", {}).get(var2, {}).get("annual", {}).get(yr)
                if v1 is not None and v2 is not None:
                    vals1.append(v1)
                    vals2.append(v2)

            if len(vals1) >= 3:
                # Pearson correlation (manual to avoid numpy dependency)
                n = len(vals1)
                mean1 = sum(vals1) / n
                mean2 = sum(vals2) / n
                cov = sum((a - mean1) * (b - mean2) for a, b in zip(vals1, vals2)) / n
                std1 = (sum((a - mean1) ** 2 for a in vals1) / n) ** 0.5
                std2 = (sum((b - mean2) ** 2 for b in vals2) / n) ** 0.5
                if std1 > 0 and std2 > 0:
                    corr = cov / (std1 * std2)
                    yr_corrs[f"{var1}|{var2}"] = round(corr, 4)

        if yr_corrs:
            correlations[yr] = yr_corrs

    return correlations


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="DecarbIQ EIA AEO Projection Extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api-key", default=None, help="EIA API key (or set EIA_API_KEY env var)")
    parser.add_argument("--aeo-year", type=int, default=2025, help="AEO edition year (default: 2025)")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="Specific scenarios to extract (default: all)")
    parser.add_argument("--output-dir", default="./output", help="Output directory (default: ./output)")
    parser.add_argument("--discover", action="store_true", help="Run discovery mode (list series per table)")
    parser.add_argument("--deflator", type=float, default=None,
                        help="Override GDP deflator for dollar-year conversion (default: auto from FRED)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("DecarbIQ EIA AEO Projection Extractor")
    logger.info(f"AEO Year: {args.aeo_year}")

    extractor = EIAAEOExtractor(
        api_key=args.api_key,
        aeo_year=args.aeo_year,
        deflator_override=args.deflator,
    )

    if args.discover:
        extractor.run_discovery(args.output_dir)
        return

    # Extract scenarios
    scenarios = args.scenarios or list(SCENARIO_MAP.keys())
    logger.info(f"Extracting {len(scenarios)} scenarios: {scenarios}")

    all_outlooks = {}
    all_validations = {}

    for scenario in scenarios:
        outlook = extractor.extract_scenario(scenario)
        validation = extractor.validate_outlook(outlook)
        all_outlooks[scenario] = outlook
        all_validations[scenario] = validation

    # Save outputs
    extractor.save_all(all_outlooks, args.output_dir)

    # Save validation report
    val_file = Path(args.output_dir) / f"eia_aeo_{args.aeo_year}_validation.json"
    with open(val_file, "w") as f:
        json.dump(all_validations, f, indent=2)
    logger.info(f"Saved validation report: {val_file}")

    # Cross-scenario correlations (only if multiple scenarios)
    if len(all_outlooks) >= 3:
        logger.info("\nComputing cross-scenario correlations...")
        corrs = compute_cross_scenario_correlations(all_outlooks)
        corr_file = Path(args.output_dir) / f"eia_aeo_{args.aeo_year}_implied_correlations.json"
        with open(corr_file, "w") as f:
            json.dump(corrs, f, indent=2)
        logger.info(f"Saved correlations: {corr_file}")

    logger.info("\nExtraction complete.")


if __name__ == "__main__":
    main()
