"""
DecarbIQ NREL ATB Projection Extractor
=======================================

Extracts Annual Technology Baseline cost and performance projections from
NREL's OEDI Data Lake (S3-hosted CSV) and populates DecarbIQ canonical
outlook structures for scenario comparison and Monte Carlo simulation.

Supports:
  - All 3 ATB scenarios (Conservative, Moderate, Advanced)
  - 38 canonical variables mapped to ATB technology/metric combinations
  - Dollar-year deflation from 2022$ (ATB 2024) to 2024$ (canonical)
  - Both financial cases: R&D+Markets (unsubsidized, default) and Market+Policies
  - Discovery mode for technology/metric inventory
  - Priority-tiered validation reporting
  - Cross-scenario correlation extraction

Data Source:
  OEDI Data Lake (no authentication required):
  https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/{year}/ATBe.csv

Usage:
  python3 nrel_atb_extractor.py --discover                    # Discovery first
  python3 nrel_atb_extractor.py --scenarios moderate           # Single scenario
  python3 nrel_atb_extractor.py                                # All 3 scenarios
  python3 nrel_atb_extractor.py --financial-case market        # With IRA subsidies
  python3 nrel_atb_extractor.py --resource-class 5             # Override class
  python3 nrel_atb_extractor.py --verbose                      # Debug logging

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-03-02
"""

import argparse
import csv
import io
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

logger = logging.getLogger("nrel_atb_extractor")


# =============================================================================
# SCENARIO MAP: canonical name → ATB scenario column value
# =============================================================================
SCENARIO_MAP = {
    "conservative": {
        "atb_name": "Conservative",
        "canonical_type": "reference",
        "description": "Floor of expected improvement — minimal R&D breakthroughs",
    },
    "moderate": {
        "atb_name": "Moderate",
        "canonical_type": "reference",
        "description": "Central estimate — current trends continue",
    },
    "advanced": {
        "atb_name": "Advanced",
        "canonical_type": "low_carbon",
        "description": "Aggressive but plausible — DOE targets met, high R&D success",
    },
}


# =============================================================================
# FINANCIAL CASE MAP
# ATB 2024 CSV uses "R&D" and "Market" (short names)
# =============================================================================
FINANCIAL_CASE_MAP = {
    "rd_only": {
        "atb_names": ["R&D", "R&D+Markets", "R&D Only"],
        "description": "Unsubsidized — no ITC/PTC policy effects on LCOE",
    },
    "market": {
        "atb_names": ["Market", "Market+Policies"],
        "description": "Includes IRA ITC/PTC effects — subsidized LCOE",
    },
}


# =============================================================================
# DOLLAR-YEAR DEFLATORS
# ATB 2024 reports in 2022$. Canonical schema standardizes to 2024$.
# =============================================================================
DEFLATOR_2022_TO_2023 = 1.037   # ~3.7% GDP implicit price deflator 2022→2023
DEFLATOR_2023_TO_2024 = 1.025   # ~2.5% GDP implicit price deflator 2023→2024
DEFLATOR_2022_TO_2024 = DEFLATOR_2022_TO_2023 * DEFLATOR_2023_TO_2024  # ≈ 1.063


# =============================================================================
# UNIT CONVERSION DISPATCH TABLE
# =============================================================================
CONVERSION_TABLE = {
    "percent_to_fraction": lambda v: v / 100.0,
    "btu_kwh_to_mmbtu_mwh": lambda v: v / 1000.0,
    "none": lambda v: v,
}


# =============================================================================
# VARIABLE MAP — imported from variable_map_atb
# =============================================================================
from variable_map_atb import VARIABLE_MAP_ATB, ATB_CONVERSIONS

VARIABLE_MAP = VARIABLE_MAP_ATB  # alias for consistency with EIA extractor
CONVERSION_TABLE.update(ATB_CONVERSIONS)


# =============================================================================
# CSV COLUMN NAME CANDIDATES
# ATB CSV column names vary slightly between editions. We try multiple names.
# =============================================================================
COL_CANDIDATES = {
    "technology":     ["technology", "Technology", "tech"],
    "techdetail":     ["techdetail", "TechDetail", "tech_detail", "technology_alias"],
    "scenario":       ["scenario", "Scenario", "core_metric_case_scenario"],
    "metric":         ["core_metric_parameter", "CoreMetricParameter",
                       "metric", "variable"],
    "financial_case": ["core_metric_case", "CoreMetricCase", "financial_case",
                       "financialcase"],
    "units":          ["units", "Units", "unit"],
    "year":           ["core_metric_variable", "CoreMetricVariable",
                       "projection_year", "year"],
    "value":          ["value", "Value"],
}

# ATB CSV is LONG format: each row = one (technology, metric, scenario, year, value) tuple


# =============================================================================
# MAIN EXTRACTOR CLASS
# =============================================================================

class NRELATBExtractor:
    """
    Extracts ATB technology cost/performance projections from NREL's OEDI
    Data Lake CSV into DecarbIQ canonical outlook structures.
    """

    # OEDI S3 bucket includes a version subdirectory. Known versions per year:
    CSV_VERSIONS = {
        2024: "v3.0.0",
        2023: "v2.0.0",
    }
    CSV_URL_TEMPLATE = "https://oedi-data-lake.s3.amazonaws.com/ATB/electricity/csv/{year}/{version}/ATBe.csv"
    API_URL_TEMPLATE = "https://developer.nrel.gov/api/atb/v1/"
    MAX_RETRIES = 3

    # Transportation ATB URL for hydrogen data
    H2_INPUT_URL_TEMPLATE = (
        "https://oedi-data-lake.s3.amazonaws.com/ATB/transportation/csv/{year}/v1/"
        "input/inputs_fuels_prices_h2.csv"
    )
    # Anchor years for H2 2-point interpolation
    H2_CURRENT_YEAR = 2024
    H2_FUTURE_YEAR = 2035

    def __init__(
        self,
        atb_year: int = 2024,
        start_year: int = 2024,
        end_year: int = 2050,
        financial_case: str = "rd_only",
        resource_class: Optional[int] = None,
        deflator_override: Optional[float] = None,
        cache_dir: Optional[str] = None,
        fuel_price_ng: float = 3.73,
        electricity_price_default: float = 40.0,
        ngcc_capacity_factor: float = 0.55,
        ngcc_economic_life: int = 30,
        battery_cycles_per_year: int = 365,
        battery_duration_hours: int = 4,
    ):
        self.atb_year = atb_year
        self.start_year = start_year
        self.end_year = end_year
        self.financial_case = financial_case
        self.resource_class = resource_class  # None = use per-variable defaults
        version = self.CSV_VERSIONS.get(atb_year, "v3.0.0")
        self.csv_url = self.CSV_URL_TEMPLATE.format(year=atb_year, version=version)
        self.cache_dir = Path(cache_dir) if cache_dir else Path(__file__).parent / "cache"

        # Derived-variable assumptions
        self.fuel_price_ng = fuel_price_ng            # $/MMBtu, 2024$
        self.electricity_price = electricity_price_default  # $/MWh for battery charging
        self.ngcc_cf = ngcc_capacity_factor
        self.ngcc_life = ngcc_economic_life
        self.battery_cycles = battery_cycles_per_year
        self.battery_duration = battery_duration_hours

        # Dollar-year deflator
        if deflator_override is not None:
            self.deflator = deflator_override
        else:
            # ATB 2024 = 2022$
            if atb_year == 2024:
                self.deflator = DEFLATOR_2022_TO_2024
            elif atb_year == 2023:
                self.deflator = DEFLATOR_2023_TO_2024
            else:
                self.deflator = DEFLATOR_2022_TO_2024  # default assumption
                logger.warning(f"Unknown ATB edition {atb_year} — assuming 2022$ base. "
                               f"Pass --deflator to override.")

        logger.info(f"Dollar-year deflator: {self.deflator:.4f} "
                     f"(ATB {atb_year} base → 2024$)")

        # Raw DataFrame (loaded on demand)
        self._df = None
        self._col_map: dict[str, str] = {}  # canonical name → actual CSV column name
        self._helper_data: dict[str, dict[str, float]] = {}  # helper var time series

    # -------------------------------------------------------------------------
    # Dollar-year adjustment
    # -------------------------------------------------------------------------
    def _adjust_dollar_year(self, value: Optional[float], apply: bool = True) -> Optional[float]:
        """Apply deflator to convert from ATB base dollars to 2024$."""
        if value is None or not apply:
            return value
        return value * self.deflator

    # -------------------------------------------------------------------------
    # CSV download and loading
    # -------------------------------------------------------------------------
    def _download_csv(self) -> str:
        """Download ATB CSV from OEDI S3. Cache locally."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / f"ATBe_{self.atb_year}.csv"

        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < 168:  # 7 days
                logger.info(f"Using cached CSV: {cache_file} (age: {age_hours:.1f}h)")
                return str(cache_file)
            else:
                logger.info(f"Cache expired ({age_hours:.0f}h old). Re-downloading...")

        logger.info(f"Downloading ATB CSV from: {self.csv_url}")

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(self.csv_url, timeout=120, stream=True)
                resp.raise_for_status()

                # Stream to file
                total_bytes = 0
                with open(cache_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)
                        total_bytes += len(chunk)

                logger.info(f"Downloaded {total_bytes / 1e6:.1f} MB → {cache_file}")
                return str(cache_file)

            except requests.exceptions.RequestException as e:
                wait = 3 ** attempt
                logger.warning(f"Download failed (attempt {attempt+1}/{self.MAX_RETRIES}): {e}. "
                               f"Retrying in {wait}s...")
                time.sleep(wait)

        raise RuntimeError(f"Failed to download ATB CSV after {self.MAX_RETRIES} attempts")

    def _resolve_column(self, canonical_name: str, header_row: list[str]) -> Optional[str]:
        """Find the actual CSV column name for a canonical column."""
        candidates = COL_CANDIDATES.get(canonical_name, [canonical_name])
        header_lower = {h.lower().strip(): h for h in header_row}

        for candidate in candidates:
            if candidate in header_row:
                return candidate
            if candidate.lower() in header_lower:
                return header_lower[candidate.lower()]

        return None

    def _load_dataframe(self) -> list[dict]:
        """Load ATB CSV into a list of dicts (avoid pandas dependency)."""
        if self._df is not None:
            return self._df

        csv_path = self._download_csv()
        logger.info(f"Loading CSV: {csv_path}")

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            rows = list(reader)

        logger.info(f"Loaded {len(rows)} rows, {len(header)} columns")

        # Resolve canonical column names
        for canonical in COL_CANDIDATES:
            actual = self._resolve_column(canonical, header)
            if actual:
                self._col_map[canonical] = actual
                logger.debug(f"  Column '{canonical}' → '{actual}'")
            else:
                logger.warning(f"  Column '{canonical}' not found in CSV headers")

        # Verify critical columns for long-format CSV
        for required in ("metric", "year", "value"):
            if required not in self._col_map:
                raise RuntimeError(
                    f"Required column '{required}' not found. "
                    f"Tried: {COL_CANDIDATES[required]}. "
                    f"Available headers: {header}"
                )

        # Discover year range from the year column
        year_col = self._col_map["year"]
        all_years = set()
        for row in rows:
            try:
                yr = int(row.get(year_col, ""))
                if 2000 <= yr <= 2060:
                    all_years.add(yr)
            except (ValueError, TypeError):
                pass
        self._available_years = sorted(all_years)
        logger.info(f"Long-format CSV: year range {self._available_years[0]}–"
                     f"{self._available_years[-1]} ({len(self._available_years)} years)")

        self._df = rows
        return rows

    # -------------------------------------------------------------------------
    # Field access helpers
    # -------------------------------------------------------------------------
    def _get_field(self, row: dict, canonical_name: str) -> str:
        """Get a field value from a row using resolved column names."""
        col = self._col_map.get(canonical_name, canonical_name)
        return str(row.get(col, "")).strip()

    # -------------------------------------------------------------------------
    # Matching logic
    # -------------------------------------------------------------------------
    def _matches_technology(self, row: dict, spec: dict) -> bool:
        """Check if a row matches the technology filter (with aliases)."""
        row_tech = self._get_field(row, "technology")
        candidates = [spec["technology"]] + spec.get("_technology_aliases", [])
        return row_tech in candidates

    def _matches_techdetail(self, row: dict, spec: dict) -> bool:
        """Check if a row matches the techdetail filter (with aliases)."""
        row_td = self._get_field(row, "techdetail")
        candidates = [spec["techdetail"]] + spec.get("_techdetail_aliases", [])

        for candidate in candidates:
            if candidate == "*":
                return True  # wildcard — accept any techdetail
            if candidate.startswith("*") and candidate.endswith("*"):
                if candidate[1:-1] in row_td:
                    return True
            elif row_td == candidate:
                return True
        return False

    def _matches_metric(self, row: dict, spec: dict) -> bool:
        """Check if a row matches the metric filter (with aliases)."""
        row_metric = self._get_field(row, "metric")
        candidates = [spec["metric"]] + spec.get("_metric_aliases", [])
        return row_metric in candidates

    def _matches_scenario(self, row: dict, scenario_name: str) -> bool:
        """Check if a row matches the target scenario. '*' means all scenarios."""
        row_scenario = self._get_field(row, "scenario")
        if row_scenario == "*":
            return True  # applies to all scenarios
        target = SCENARIO_MAP[scenario_name]["atb_name"]
        return row_scenario == target

    def _matches_financial_case(self, row: dict) -> bool:
        """Check if a row matches the selected financial case."""
        row_fc = self._get_field(row, "financial_case")
        if not row_fc:
            return True  # if column doesn't exist, accept all

        target_names = FINANCIAL_CASE_MAP[self.financial_case]["atb_names"]
        return row_fc in target_names

    def _find_matching_rows(
        self,
        rows: list[dict],
        spec: dict,
        scenario_name: str,
    ) -> list[dict]:
        """Find all rows matching a variable spec for a given scenario."""
        matching = []
        for row in rows:
            if (self._matches_technology(row, spec)
                    and self._matches_techdetail(row, spec)
                    and self._matches_metric(row, spec)
                    and self._matches_scenario(row, scenario_name)
                    and self._matches_financial_case(row)):
                matching.append(row)
        return matching

    # -------------------------------------------------------------------------
    # Unit conversion
    # -------------------------------------------------------------------------
    def _convert_value(self, value: Optional[float], spec: dict) -> Optional[float]:
        """Convert a raw CSV value through unit conversion and dollar-year adjustment."""
        if value is None:
            return None

        # Unit conversion
        conv_name = spec.get("unit_conversion", "none")
        if conv_name in CONVERSION_TABLE:
            value = CONVERSION_TABLE[conv_name](value)

        # Dollar-year adjustment
        if spec.get("dollar_year_adjust", False):
            value = self._adjust_dollar_year(value)

        return round(value, 6)

    # -------------------------------------------------------------------------
    # Discovery mode
    # -------------------------------------------------------------------------
    def run_discovery(self, output_dir: str):
        """
        Dump all unique technology/techdetail/metric/scenario combinations.
        Use this to validate and refine the variable map.
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        rows = self._load_dataframe()

        # Collect unique combinations
        combos: dict[str, set] = {
            "technologies": set(),
            "techdetails": set(),
            "metrics": set(),
            "scenarios": set(),
            "financial_cases": set(),
            "units": set(),
        }

        # Detailed: technology → {techdetail → {metrics}}
        tech_tree: dict[str, dict[str, dict[str, Any]]] = defaultdict(
            lambda: defaultdict(lambda: {"metrics": set(), "scenarios": set(), "units": set()})
        )

        for row in rows:
            tech = self._get_field(row, "technology")
            td = self._get_field(row, "techdetail")
            metric = self._get_field(row, "metric")
            scenario = self._get_field(row, "scenario")
            fc = self._get_field(row, "financial_case")
            unit = self._get_field(row, "units")

            combos["technologies"].add(tech)
            combos["techdetails"].add(td)
            combos["metrics"].add(metric)
            combos["scenarios"].add(scenario)
            combos["financial_cases"].add(fc)
            combos["units"].add(unit)

            tech_tree[tech][td]["metrics"].add(metric)
            tech_tree[tech][td]["scenarios"].add(scenario)
            tech_tree[tech][td]["units"].add(unit)

        # Convert sets to sorted lists for JSON
        discovery = {
            "atb_year": self.atb_year,
            "csv_url": self.csv_url,
            "total_rows": len(rows),
            "format": "long",
            "available_years": self._available_years,
            "column_map": self._col_map,
            "summary": {k: sorted(v) for k, v in combos.items()},
            "technology_tree": {},
        }

        for tech in sorted(tech_tree):
            discovery["technology_tree"][tech] = {}
            for td in sorted(tech_tree[tech]):
                info = tech_tree[tech][td]
                discovery["technology_tree"][tech][td] = {
                    "metrics": sorted(info["metrics"]),
                    "scenarios": sorted(info["scenarios"]),
                    "units": sorted(info["units"]),
                }

        # Check which canonical variables can be matched
        value_col = self._col_map.get("value", "value")
        coverage = {"matched": [], "unmatched": [], "partial": [], "not_in_csv": []}
        for var_key, spec in VARIABLE_MAP.items():
            if spec.get("_helper"):
                continue  # skip internal helpers in coverage report
            if spec.get("_not_in_csv"):
                if spec.get("_derived"):
                    note = f"Derived: {spec['_derived']}"
                elif spec.get("_static_fallback"):
                    note = "Static fallback (DOE targets)"
                else:
                    note = "Not in electricity CSV"
                coverage["not_in_csv"].append({
                    "variable": var_key,
                    "priority": spec["priority"],
                    "note": note,
                })
                continue
            if spec.get("_derived"):
                coverage["partial"].append({
                    "variable": var_key,
                    "priority": spec["priority"],
                    "note": f"Derived: {spec['_derived']}",
                })
                continue

            found = self._find_matching_rows(rows, spec, "moderate")
            if found:
                # Sample first row's value
                sample_val = None
                try:
                    sample_val = float(found[0].get(value_col, ""))
                except (ValueError, TypeError):
                    pass
                coverage["matched"].append({
                    "variable": var_key,
                    "priority": spec["priority"],
                    "matched_rows": len(found),
                    "sample_value": sample_val,
                    "technology": self._get_field(found[0], "technology"),
                    "techdetail": self._get_field(found[0], "techdetail"),
                    "metric": self._get_field(found[0], "metric"),
                })
            else:
                coverage["unmatched"].append({
                    "variable": var_key,
                    "priority": spec["priority"],
                    "tried": {
                        "technology": spec["technology"],
                        "techdetail": spec["techdetail"],
                        "metric": spec["metric"],
                    },
                })

        discovery["variable_coverage"] = coverage

        # Save
        disc_file = output_path / f"nrel_atb_{self.atb_year}_discovery.json"
        with open(disc_file, "w") as f:
            json.dump(discovery, f, indent=2, default=str)

        # Print summary
        logger.info(f"\n{'='*60}")
        logger.info(f"NREL ATB {self.atb_year} Discovery")
        logger.info(f"{'='*60}")
        logger.info(f"Total rows: {len(rows)}")
        logger.info(f"Technologies: {len(combos['technologies'])}")
        logger.info(f"Tech details: {len(combos['techdetails'])}")
        logger.info(f"Metrics: {len(combos['metrics'])}")
        logger.info(f"Scenarios: {sorted(combos['scenarios'])}")
        logger.info(f"Financial cases: {sorted(combos['financial_cases'])}")
        canonical_count = sum(1 for s in VARIABLE_MAP.values() if not s.get("_helper"))
        logger.info(f"\nVariable coverage ({canonical_count} canonical):")
        logger.info(f"  Matched:      {len(coverage['matched'])}")
        logger.info(f"  Derived:      {len(coverage['partial'])}")
        logger.info(f"  Not in CSV:   {len(coverage['not_in_csv'])}")
        logger.info(f"  Unmatched:    {len(coverage['unmatched'])}")

        if coverage["unmatched"]:
            logger.info(f"\nUnmatched variables:")
            for item in coverage["unmatched"]:
                logger.info(f"  [{item['priority']}] {item['variable']}: "
                            f"tried {item['tried']}")

        logger.info(f"\nDiscovery saved to {disc_file}")
        return discovery

    # -------------------------------------------------------------------------
    # Core extraction
    # -------------------------------------------------------------------------
    def _extract_annual_from_rows(
        self, matching: list[dict], spec: dict,
    ) -> dict[str, float]:
        """Extract annual time series from matching long-format rows."""
        year_col = self._col_map["year"]
        value_col = self._col_map["value"]
        annual: dict[str, float] = {}

        for row in matching:
            try:
                yr_int = int(row.get(year_col, ""))
            except (ValueError, TypeError):
                continue
            if yr_int < self.start_year or yr_int > self.end_year:
                continue
            raw_str = row.get(value_col, "")
            if raw_str is None or raw_str == "" or raw_str in ("--", "NA", "n/a"):
                continue
            try:
                raw_val = float(raw_str)
            except (ValueError, TypeError):
                continue
            converted = self._convert_value(raw_val, spec)
            if converted is not None:
                annual[str(yr_int)] = converted

        return annual

    def _populate_variable(
        self,
        outlook: dict,
        var_key: str,
        spec: dict,
        rows: list[dict],
        scenario_name: str,
    ):
        """Match rows to a variable spec and populate the outlook (long-format CSV)."""
        # --- Helper variables → store in _helper_data, not outlook ---
        if spec.get("_helper"):
            matching = self._find_matching_rows(rows, spec, scenario_name)
            if not matching:
                logger.warning(f"  {var_key} (helper): no matching rows")
                return
            annual = self._extract_annual_from_rows(matching, spec)
            self._helper_data[var_key] = annual
            logger.info(f"  {var_key} (helper): {len(annual)} years extracted")
            return

        # --- Derived variables → computed in _compute_derived() ---
        if spec.get("_derived"):
            return

        # --- Static fallback for variables not in electricity CSV ---
        if spec.get("_not_in_csv"):
            if "_static_fallback" in spec and var_key in outlook.get("variables", {}):
                fallback = spec["_static_fallback"]
                annual = {
                    str(yr): val for yr, val in fallback.items()
                    if self.start_year <= yr <= self.end_year
                }
                outlook["variables"][var_key]["annual"] = annual
                logger.info(f"  {var_key}: {len(annual)} years from static fallback")
            else:
                logger.debug(f"  {var_key}: skipped (_not_in_csv, no fallback)")
            return

        # --- Standard canonical variable from electricity CSV ---
        if var_key not in outlook["variables"]:
            return

        matching = self._find_matching_rows(rows, spec, scenario_name)

        if not matching:
            logger.warning(f"  {var_key}: no matching rows "
                           f"(tech={spec['technology']}, td={spec['techdetail']}, "
                           f"metric={spec['metric']})")
            return

        annual = self._extract_annual_from_rows(matching, spec)
        outlook["variables"][var_key]["annual"] = annual
        logger.info(f"  {var_key}: {len(annual)} years populated")

    # -------------------------------------------------------------------------
    # Derived variable computations
    # -------------------------------------------------------------------------
    def _compute_ngcc_lcoe(self, outlook: dict):
        """
        Derive NGCC and NGCC+CCS LCOE from ATB component variables.

        LCOE = (CAPEX * CRF + FOM) / (CF * 8760) * 1000 + VOM + (HR * FuelPrice)
        CRF  = WACC * (1+WACC)^n / ((1+WACC)^n - 1)

        Units: CAPEX $/kW, FOM $/kW-yr → ($/kW-yr)/(hrs) * 1000 kW/MW = $/MWh
        """
        vars_ = outlook["variables"]

        configs = [
            {
                "target": "ngcc_lcoe",
                "capex_key": "ngcc_capex",
                "fom_key": "_ngcc_fom",
                "vom_key": "_ngcc_vom",
                "hr_key": "ngcc_heat_rate",
            },
            {
                "target": "ngcc_ccs_lcoe",
                "capex_key": "ngcc_ccs_capex",
                "fom_key": "_ngcc_ccs_fom",
                "vom_key": "_ngcc_ccs_vom",
                "hr_key": "_ngcc_ccs_heat_rate",
            },
        ]

        wacc_series = self._helper_data.get("_ngcc_wacc_real", {})

        for cfg in configs:
            target = cfg["target"]
            if target not in vars_:
                continue

            def _get(key):
                if key.startswith("_"):
                    return self._helper_data.get(key, {})
                return vars_.get(key, {}).get("annual", {})

            capex = _get(cfg["capex_key"])
            fom = _get(cfg["fom_key"])
            vom = _get(cfg["vom_key"])
            hr = _get(cfg["hr_key"])

            lcoe = {}
            for yr in capex:
                if yr not in fom or yr not in vom or yr not in hr:
                    continue
                wacc = wacc_series.get(yr, 0.0536)  # ATB 2024 default ~5.36%
                n = self.ngcc_life
                if wacc <= 0:
                    continue
                factor = (1 + wacc) ** n
                crf = wacc * factor / (factor - 1)

                fixed = capex[yr] * crf + fom[yr]  # $/kW-yr
                lcoe_val = (fixed / (self.ngcc_cf * 8760)) * 1000 + vom[yr] + (hr[yr] * self.fuel_price_ng)
                lcoe[yr] = round(lcoe_val, 2)

            vars_[target]["annual"] = lcoe
            logger.info(f"  {target}: derived LCOE for {len(lcoe)} years "
                        f"(CF={self.ngcc_cf}, fuel=${self.fuel_price_ng}/MMBtu)")

    def _compute_battery_lcos_rte(self, outlook: dict):
        """
        Derive battery LCOS and RTE.

        RTE:  static curve 0.86 (2024) → 0.88 (2050) for Li-ion 4-hr.
        LCOS: (CAPEX*CRF + FOM) / annual_energy_discharged + charging_cost/RTE
              annual_energy = cycles * duration / 1000  (MWh/kW-yr)
        """
        vars_ = outlook["variables"]

        # --- Round-Trip Efficiency ---
        if "battery_utility_rte" in vars_:
            rte_annual = {}
            for yr in range(self.start_year, self.end_year + 1):
                rte = 0.86 + (0.88 - 0.86) * max(0, yr - 2024) / 26
                rte_annual[str(yr)] = round(rte, 4)
            vars_["battery_utility_rte"]["annual"] = rte_annual
            logger.info(f"  battery_utility_rte: static curve {len(rte_annual)} years (0.86→0.88)")

        # --- LCOS ---
        if "battery_utility_lcos" in vars_:
            capex_power = vars_.get("battery_utility_capex_power", {}).get("annual", {})
            fom = self._helper_data.get("_battery_utility_fom", {})
            rte_data = vars_.get("battery_utility_rte", {}).get("annual", {})

            wacc = 0.0393  # PV+Battery proxy, ~3.93% real
            n = 20  # battery project life
            factor = (1 + wacc) ** n
            crf = wacc * factor / (factor - 1)

            # Annual energy discharged per kW: cycles * duration / 1000 MWh/kW-yr
            annual_energy = self.battery_cycles * self.battery_duration / 1000

            lcos = {}
            for yr in capex_power:
                if yr not in fom:
                    continue
                rte = rte_data.get(yr, 0.86)
                fixed_cost = (capex_power[yr] * crf + fom[yr]) / annual_energy  # $/MWh
                charging = self.electricity_price / rte  # $/MWh
                lcos[yr] = round(fixed_cost + charging, 2)

            vars_["battery_utility_lcos"]["annual"] = lcos
            logger.info(f"  battery_utility_lcos: derived {len(lcos)} years "
                        f"(cycles={self.battery_cycles}, dur={self.battery_duration}h, "
                        f"elec=${self.electricity_price}/MWh)")

    def _interpolate_two_point(
        self, current_val: float, future_val: float,
        current_year: int, future_year: int,
    ) -> dict[str, float]:
        """Linear interpolation between two data points, then hold flat."""
        result = {}
        for yr in range(self.start_year, self.end_year + 1):
            if yr <= current_year:
                val = current_val
            elif yr >= future_year:
                val = future_val
            else:
                frac = (yr - current_year) / (future_year - current_year)
                val = current_val + frac * (future_val - current_val)
            result[str(yr)] = round(val, 4)
        return result

    def _download_h2_input_csv(self) -> str:
        """Download Transportation ATB H2 input prices CSV. Cache locally."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.cache_dir / f"ATBt_h2_prices_{self.atb_year}.csv"

        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < 168:
                logger.info(f"Using cached H2 prices CSV: {cache_file}")
                return str(cache_file)

        url = self.H2_INPUT_URL_TEMPLATE.format(year=self.atb_year)
        logger.info(f"Downloading H2 prices CSV: {url}")
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = requests.get(url, timeout=60)
                resp.raise_for_status()
                with open(cache_file, "wb") as f:
                    f.write(resp.content)
                logger.info(f"Downloaded {len(resp.content)} bytes → {cache_file}")
                return str(cache_file)
            except requests.exceptions.RequestException as e:
                wait = 3 ** attempt
                logger.warning(f"H2 CSV download failed (attempt {attempt+1}): {e}")
                time.sleep(wait)
        raise RuntimeError("Failed to download H2 prices CSV")

    def _compute_hydrogen_variables(self, outlook: dict, scenario_name: str):
        """
        Populate LCOH variables from Transportation ATB H2 production cost data.

        Scenario mapping (ATB tech scenario → H2 cost bound):
          conservative → High production cost
          moderate     → midpoint of High and Low
          advanced     → Low production cost (DOE targets)
        """
        vars_ = outlook["variables"]

        # Which LCOH variables are derived from transport ATB?
        lcoh_map = {
            "lcoh_green_electrolysis": "Low temperature electrolysis",
            "lcoh_gray_smr": "Steam Methane Reforming",
            "lcoh_blue_smr_ccs": "Steam Methane Reforming with CCS",
        }

        # Check if any of these variables exist in the outlook
        needed = [k for k in lcoh_map if k in vars_]
        if not needed:
            return

        try:
            csv_path = self._download_h2_input_csv()
        except Exception as e:
            logger.warning(f"  Could not load H2 prices: {e}")
            return

        # Parse H2 production cost data
        # Columns: fuel_pathway, metric, fuel_scenario, lcod_case, value, units
        h2_costs: dict[str, dict[str, dict[str, float]]] = {}  # pathway → scenario_label → case → cost

        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                metric = row.get("metric", "").strip()
                if metric != "H2 Production Cost":
                    continue
                pathway = row.get("fuel_pathway", "").strip()
                scenario_label = row.get("fuel_scenario", "").strip()
                case = row.get("lcod_case", "").strip()
                try:
                    cost = float(row.get("value", ""))
                except (ValueError, TypeError):
                    continue

                h2_costs.setdefault(pathway, {}).setdefault(scenario_label, {})[case] = cost

        # Map ATB scenario → case selection
        case_map = {
            "conservative": lambda h, l: h,       # High
            "moderate":     lambda h, l: (h + l) / 2,  # Midpoint
            "advanced":     lambda h, l: l,        # Low (DOE target)
        }
        case_fn = case_map.get(scenario_name, case_map["moderate"])

        for var_key, pathway in lcoh_map.items():
            if var_key not in vars_:
                continue
            pathway_data = h2_costs.get(pathway)
            if not pathway_data:
                logger.warning(f"  {var_key}: no H2 data for pathway '{pathway}'")
                continue

            current_data = pathway_data.get("Current Modeled, Current Volume", {})
            future_data = pathway_data.get("Future Modeled, High Volume", {})

            c_high = current_data.get("High")
            c_low = current_data.get("Low")
            f_high = future_data.get("High")
            f_low = future_data.get("Low")

            if c_high is None or c_low is None or f_high is None or f_low is None:
                logger.warning(f"  {var_key}: incomplete H2 cost data for '{pathway}'")
                continue

            current_val = case_fn(c_high, c_low) * self.deflator  # 2022$ → 2024$
            future_val = case_fn(f_high, f_low) * self.deflator

            annual = self._interpolate_two_point(
                current_val, future_val,
                self.H2_CURRENT_YEAR, self.H2_FUTURE_YEAR,
            )
            vars_[var_key]["annual"] = annual
            logger.info(f"  {var_key}: H2 production cost interpolated "
                        f"(${current_val:.2f}→${future_val:.2f}/kg, "
                        f"{self.H2_CURRENT_YEAR}→{self.H2_FUTURE_YEAR})")

    def _compute_derived(self, outlook: dict, scenario_name: str = "moderate"):
        """Compute all derived variables after direct variables are populated."""
        vars_ = outlook["variables"]

        # 1. Battery energy CAPEX (existing)
        if "battery_utility_capex_energy" in vars_:
            power_capex = vars_.get("battery_utility_capex_power", {}).get("annual", {})
            energy_capex = {}
            for yr, val in power_capex.items():
                if val is not None and val > 0:
                    energy_capex[yr] = round(val / 4.0, 6)
            vars_["battery_utility_capex_energy"]["annual"] = energy_capex
            logger.info(f"  battery_utility_capex_energy: derived for {len(energy_capex)} years")

        # 2. NGCC LCOE
        self._compute_ngcc_lcoe(outlook)

        # 3. Battery LCOS and RTE
        self._compute_battery_lcos_rte(outlook)

        # 4. Hydrogen LCOH
        self._compute_hydrogen_variables(outlook, scenario_name)

    def extract_scenario(self, scenario_name: str) -> dict:
        """Extract all variables for a single scenario."""
        if scenario_name not in SCENARIO_MAP:
            raise ValueError(f"Unknown scenario: {scenario_name}. "
                             f"Available: {list(SCENARIO_MAP.keys())}")

        logger.info(f"\n{'='*60}")
        logger.info(f"Extracting scenario: {scenario_name} "
                     f"({SCENARIO_MAP[scenario_name]['atb_name']})")
        logger.info(f"Financial case: {self.financial_case} "
                     f"({FINANCIAL_CASE_MAP[self.financial_case]['description']})")
        logger.info(f"{'='*60}")

        rows = self._load_dataframe()
        outlook = build_empty_outlook("nrel_atb", scenario_name, self.start_year, self.end_year)

        # Override scenario description with ATB-specific text
        outlook["scenario_description"] = SCENARIO_MAP[scenario_name]["description"]

        # Set extraction metadata
        outlook["extraction_metadata"] = {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": "csv_download",
            "confidence_score": 0.98,
            "source_document": self.csv_url,
            "notes": (f"ATB {self.atb_year}. Dollar-year adjusted from 2022$ to 2024$ "
                      f"(deflator={self.deflator:.4f}). "
                      f"Financial case: {self.financial_case}. "
                      f"Resource class: {self.resource_class or 'per-variable default'}."),
        }

        # Reset helper data for this scenario
        self._helper_data = {}

        # Populate all direct variables (including helpers → _helper_data)
        for var_key, spec in VARIABLE_MAP.items():
            self._populate_variable(outlook, var_key, spec, rows, scenario_name)

        # Compute derived variables (uses _helper_data + populated vars)
        self._compute_derived(outlook, scenario_name)

        # Export helper data so downstream Model module can consume it
        outlook["helper_data"] = dict(self._helper_data)

        return outlook

    def extract_all_scenarios(self) -> dict[str, dict]:
        """Extract all 3 scenarios."""
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
        filename = f"nrel_atb_{self.atb_year}_{scenario}.json"
        filepath = output_path / filename

        with open(filepath, "w") as f:
            json.dump(outlook, f, indent=2, default=str)

        logger.info(f"Saved JSON: {filepath}")

    def save_csv(self, outlook: dict, output_dir: str):
        """Save a single scenario as flat CSV."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario = outlook["scenario_name"]
        filename = f"nrel_atb_{self.atb_year}_{scenario}.csv"
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

        # Write CSV manually to avoid pandas dependency
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
        combined_file = output_path / f"nrel_atb_{self.atb_year}_all_scenarios.csv"
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

                # Sanity checks on populated variables
                vals = list(annual.values())
                if min(vals) < 0:
                    issues.append({
                        "level": "WARNING",
                        "var": var_key,
                        "msg": f"Negative values found (min={min(vals):.2f})",
                    })

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
                "target": "90%",
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
        logger.info(f"  P2: {p2_populated}/{p2_total} ({report['priority_2']['pct']}%) — target 90%")
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
    Compute implied correlations across ATB scenarios for key variable pairs.

    For each year, correlates how variables co-move across the 3 ATB scenarios.
    Output format: {year: {"{var1}|{var2}": correlation_value}}
    """
    if var_pairs is None:
        var_pairs = [
            ("solar_pv_utility_capex", "solar_pv_utility_lcoe"),
            ("wind_onshore_capex", "wind_onshore_lcoe"),
            ("battery_utility_capex_power", "battery_utility_lcos"),
            ("ngcc_capex", "ngcc_lcoe"),
            ("electrolyzer_pem_capex", "lcoh_green_electrolysis"),
            ("solar_pv_utility_capex", "battery_utility_capex_power"),
            ("solar_pv_utility_lcoe", "wind_onshore_lcoe"),
        ]

    scenarios = list(all_outlooks.keys())
    if len(scenarios) < 3:
        logger.warning("Need at least 3 scenarios for meaningful correlations")
        return {}

    correlations: dict[str, dict] = {}

    # Get all years from first scenario
    ref = next(iter(all_outlooks.values()))
    sample_var = next(
        (v for v in ref["variables"].values() if v.get("annual")),
        {},
    )
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
        description="DecarbIQ NREL ATB Projection Extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 nrel_atb_extractor.py --discover                    # Discovery first
  python3 nrel_atb_extractor.py --scenarios moderate           # Single scenario
  python3 nrel_atb_extractor.py                                # All 3 scenarios
  python3 nrel_atb_extractor.py --financial-case market        # With IRA subsidies
  python3 nrel_atb_extractor.py --resource-class 5             # Override class
  python3 nrel_atb_extractor.py --verbose                      # Debug logging
        """,
    )
    parser.add_argument("--atb-year", type=int, default=2024,
                        help="ATB edition year (default: 2024)")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="Specific scenarios to extract (default: all three)")
    parser.add_argument("--output-dir", default="./output",
                        help="Output directory (default: ./output)")
    parser.add_argument("--discover", action="store_true",
                        help="Run discovery mode (list all technology/metric combos)")
    parser.add_argument("--financial-case", choices=["rd_only", "market"], default="rd_only",
                        help="Financial case: rd_only (unsubsidized, default) or market (with IRA)")
    parser.add_argument("--resource-class", type=int, default=None,
                        help="Override resource class for all technologies (default: per-variable)")
    parser.add_argument("--deflator", type=float, default=None,
                        help="Override GDP deflator for dollar-year conversion")
    parser.add_argument("--cache-dir", default=None,
                        help="Directory to cache downloaded CSV (default: ./cache)")
    parser.add_argument("--ng-fuel-price", type=float, default=3.73,
                        help="Natural gas fuel price $/MMBtu in 2024$ (default: 3.73)")
    parser.add_argument("--electricity-price", type=float, default=40.0,
                        help="Electricity price $/MWh for battery LCOS charging (default: 40)")
    parser.add_argument("--ngcc-cf", type=float, default=0.55,
                        help="NGCC capacity factor assumption (default: 0.55)")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("DecarbIQ NREL ATB Projection Extractor")
    logger.info(f"ATB Year: {args.atb_year}")

    extractor = NRELATBExtractor(
        atb_year=args.atb_year,
        financial_case=args.financial_case,
        resource_class=args.resource_class,
        deflator_override=args.deflator,
        cache_dir=args.cache_dir,
        fuel_price_ng=args.ng_fuel_price,
        electricity_price_default=args.electricity_price,
        ngcc_capacity_factor=args.ngcc_cf,
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
    val_file = Path(args.output_dir) / f"nrel_atb_{args.atb_year}_validation.json"
    with open(val_file, "w") as f:
        json.dump(all_validations, f, indent=2)
    logger.info(f"Saved validation report: {val_file}")

    # Cross-scenario correlations (all 3 scenarios)
    if len(all_outlooks) >= 3:
        logger.info("\nComputing cross-scenario correlations...")
        corrs = compute_cross_scenario_correlations(all_outlooks)
        corr_file = Path(args.output_dir) / f"nrel_atb_{args.atb_year}_implied_correlations.json"
        with open(corr_file, "w") as f:
            json.dump(corrs, f, indent=2)
        logger.info(f"Saved correlations: {corr_file}")

    logger.info("\nExtraction complete.")


if __name__ == "__main__":
    main()
