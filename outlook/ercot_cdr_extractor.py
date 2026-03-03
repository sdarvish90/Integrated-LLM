"""
DecarbIQ ERCOT CDR Projection Extractor
========================================

Extracts capacity, demand, reserve margin, and generation capacity mix
projections from the ERCOT Capacity, Demand and Reserves (CDR) report
(Excel workbook) and populates DecarbIQ canonical outlook structures.

Supports:
  - 3 CDR scenarios (Base, High Demand, Low Demand)
  - 10 canonical ERCOT variables directly extractable from CDR
  - Header-based column detection (resilient to tab/column shifts)
  - Discovery mode for workbook structure inventory
  - Horizon extrapolation (5-year CDR → 2050) with metadata flags
  - Priority-tiered validation reporting

Data Source:
  ERCOT CDR Excel workbook (published semi-annually).
  Must be downloaded manually or provided via --file flag.
  Portal: https://www.ercot.com/gridinfo/resource

Usage:
  python3 ercot_cdr_extractor.py --file CDR_May2025.xlsx --discover
  python3 ercot_cdr_extractor.py --file CDR_May2025.xlsx --scenarios base
  python3 ercot_cdr_extractor.py --file CDR_May2025.xlsx
  python3 ercot_cdr_extractor.py --file CDR_May2025.xlsx --no-extrapolate
  python3 ercot_cdr_extractor.py --verbose

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-03-02
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    import openpyxl
except ImportError:
    openpyxl = None  # handled at runtime with clear error

sys.path.insert(0, str(Path(__file__).parent))
from energy_outlook_canonical_schema import (
    VARIABLE_CATALOG,
    build_empty_outlook,
    get_variables_for_source,
    get_variables_by_priority,
)

logger = logging.getLogger("ercot_cdr_extractor")


# =============================================================================
# SCENARIO MAP: canonical name → CDR scenario tab/section identifier
# =============================================================================
SCENARIO_MAP = {
    "base": {
        # Demand row label(s) in "Load-Resource Scenarios"
        "demand_labels": [
            "Firm Peak Load -- ERCOT Adjusted",
            "Firm Peak Load",
            "ERCOT Adjusted",
        ],
        # Capacity row label(s) in "Load-Resource Scenarios"
        "capacity_labels": [
            "Total Capacity -- Protocol Prescribed",
            "Protocol Prescribed",
        ],
        # Reserve margin row label(s)
        "rm_labels": [
            "Protocol-prescribed",
        ],
        "canonical_type": "reference",
        "description": (
            "ERCOT base case — protocol-prescribed resources with "
            "ERCOT adjusted load forecast"
        ),
    },
    "high": {
        "demand_labels": [
            "TSP Officer Letter Load Scenario 1",
            "50% of TSP",
            "50% Officer Letter",
        ],
        "capacity_labels": [
            "TEF Capacity Scenario 3",
            "TEF Scenario 3",
        ],
        "rm_labels": [
            "TSP Officer Letter Load Scenario 1",
        ],
        "canonical_type": "high_demand",
        "description": (
            "ERCOT high demand — 50% of TSP officer letter large loads "
            "materialize (data centers, LNG, crypto) + TEF capacity"
        ),
    },
    "low": {
        "demand_labels": [
            "Firm Peak Load -- ERCOT Adjusted",
            "ERCOT Adjusted",
        ],
        "capacity_labels": [
            "TEF Capacity Scenario 4",
            "TEF Scenario 4",
        ],
        "rm_labels": [
            "TEF Capacity Scenario 4",
        ],
        "canonical_type": "low_capacity",
        "description": (
            "ERCOT base demand with TEF Scenario 4 capacity — "
            "only Texas Energy Fund Phase 1 projects included"
        ),
    },
}


# =============================================================================
# FUEL TYPE MAPPING: CDR fuel labels → canonical categories
# =============================================================================
FUEL_TYPE_MAPPING = {
    # Natural gas variants
    "gas-cc": "natural_gas",
    "gas-ct": "natural_gas",
    "gas-st": "natural_gas",
    "gas-ice": "natural_gas",
    "gas": "natural_gas",
    "natural gas": "natural_gas",
    "ng": "natural_gas",
    "ccgt": "natural_gas",
    "ct": "natural_gas",
    "combined cycle": "natural_gas",
    "combustion turbine": "natural_gas",
    # Coal
    "coal": "coal",
    "lignite": "coal",
    # Nuclear
    "nuclear": "nuclear",
    # Wind
    "wind": "wind",
    "wind-onshore": "wind",
    "wind-offshore": "wind",
    # Solar
    "solar": "solar",
    "solar-pv": "solar",
    "solar pv": "solar",
    "pvgr": "solar",
    # Battery / Storage
    "battery": "battery",
    "bess": "battery",
    "esr": "battery",
    "energy storage": "battery",
    "energy storage resource": "battery",
    # Other
    "biomass": "other",
    "hydro": "other",
    "other": "other",
    "switchable": "other",
}


# =============================================================================
# VARIABLE MAP: canonical key → extraction specification
# =============================================================================
VARIABLE_MAP_ERCOT = {
    "ercot_peak_demand": {
        "source_tab": "load_resource_scenarios",
        "data_type": "demand",
        "season": "summer",
        "unit_source": "MW",
        "unit_conversion": "mw_to_gw",
        "priority": 1,
    },
    "ercot_energy_demand": {
        "source_tab": "_not_in_cdr",
        "priority": 1,
        "notes": (
            "CDR reports peak demand (MW) not energy (GWh). "
            "Energy forecasts are in separate ERCOT load forecast publication."
        ),
    },
    "ercot_installed_capacity": {
        "source_tab": "load_resource_scenarios",
        "data_type": "capacity",
        "season": "summer",
        "unit_source": "MW",
        "unit_conversion": "mw_to_gw",
        "priority": 1,
    },
    "ercot_reserve_margin": {
        "source_tab": "load_resource_scenarios",
        "data_type": "reserve_margin",
        "season": "summer",
        "unit_source": "fraction",
        "unit_conversion": "none",  # CDR already reports as fraction (0.17 = 17%)
        "priority": 1,
    },
    "ercot_solar_capacity": {
        "source_tab": "capacity_by_resource",
        "fuel_category": "solar",
        "fuel_labels": ["Solar"],
        "unit_source": "MW",
        "unit_conversion": "mw_to_gw",
        "priority": 1,
    },
    "ercot_wind_capacity": {
        "source_tab": "capacity_by_resource",
        "fuel_category": "wind",
        "fuel_labels": ["Wind"],
        "unit_source": "MW",
        "unit_conversion": "mw_to_gw",
        "priority": 1,
    },
    "ercot_battery_capacity": {
        "source_tab": "capacity_by_resource",
        "fuel_category": "battery",
        "fuel_labels": ["Batteries"],  # NOT "Energy Storage" (parent row, same values)
        "unit_source": "MW",
        "unit_conversion": "mw_to_gw",
        "priority": 1,
    },
    "ercot_ng_capacity": {
        "source_tab": "capacity_by_resource",
        "fuel_category": "natural_gas",
        "fuel_labels": ["Natural Gas"],
        "unit_source": "MW",
        "unit_conversion": "mw_to_gw",
        "priority": 1,
    },
    "ercot_generation_mix": {
        "source_tab": "_derived",
        "_derived": "capacity_share",
        "priority": 1,
    },
    "ercot_wholesale_price": {
        "source_tab": "_not_in_cdr",
        "priority": 1,
        "notes": "Not in CDR. Requires ERCOT MIS historical data or S&P Global.",
    },
}


# =============================================================================
# UNIT CONVERSION TABLE
# =============================================================================
CONVERSION_TABLE = {
    "mw_to_gw": lambda v: v / 1000.0,
    "gwh_to_twh": lambda v: v / 1000.0,
    "pct_to_fraction": lambda v: v / 100.0,
    "none": lambda v: v,
}


# =============================================================================
# MAIN EXTRACTOR CLASS
# =============================================================================

class ERCOTCDRExtractor:
    """
    Extracts ERCOT CDR projections from Excel workbook into DecarbIQ
    canonical outlook structures.
    """

    def __init__(
        self,
        file_path: str,
        cdr_year: int = 2025,
        cdr_edition: str = "May",
        start_year: int = 2026,
        end_year: int = 2050,
        extrapolate: bool = True,
        cdr_horizon_end: Optional[int] = None,
    ):
        if openpyxl is None:
            raise ImportError(
                "openpyxl is required for ERCOT CDR extraction. "
                "Install with: pip install openpyxl"
            )

        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"CDR workbook not found: {self.file_path}")

        self.cdr_year = cdr_year
        self.cdr_edition = cdr_edition
        self.start_year = start_year
        self.end_year = end_year
        self.extrapolate = extrapolate
        self.cdr_horizon_end = cdr_horizon_end  # auto-detected if None

        # Loaded workbook state
        self._wb = None
        self._tab_map: dict[str, str] = {}  # canonical tab type → actual sheet name
        self._discovered_years: list[int] = []

    # -------------------------------------------------------------------------
    # Workbook loading and tab detection
    # -------------------------------------------------------------------------
    def _load_workbook(self):
        """Load Excel workbook with openpyxl."""
        if self._wb is not None:
            return

        logger.info(f"Loading workbook: {self.file_path}")
        self._wb = openpyxl.load_workbook(
            str(self.file_path), read_only=True, data_only=True,
        )
        logger.info(f"Sheets found: {self._wb.sheetnames}")
        self._detect_tabs()

    def _detect_tabs(self):
        """Map canonical tab types to actual sheet names using keyword matching."""
        sheets = self._wb.sheetnames

        # Keywords for each canonical tab type (order matters — first match wins)
        tab_keywords = {
            "load_resource_scenarios": [
                "load-resource scenario", "load resource scenario",
                "load-resource", "load resource",
            ],
            "capacity_by_resource": [
                "capacity by resource type", "capacity by resource",
                "resource type",
            ],
            "seasonal_summary": [
                "seasonal summary", "seasonal",
            ],
            "unit_details": [
                "unit detail",
            ],
            "elcc": [
                "elcc",
            ],
            "new_cdr_eligible": [
                "new cdr-eligible", "new cdr eligible", "new planned",
            ],
            "findings": [
                "findings", "discussion",
            ],
        }

        for canonical_type, keywords in tab_keywords.items():
            for sheet_name in sheets:
                sn_lower = sheet_name.lower().strip()
                for kw in keywords:
                    if kw in sn_lower:
                        self._tab_map[canonical_type] = sheet_name
                        logger.info(f"  Tab '{canonical_type}' → '{sheet_name}'")
                        break
                if canonical_type in self._tab_map:
                    break

        # Log unmapped tabs
        mapped_sheets = set(self._tab_map.values())
        unmapped = [s for s in sheets if s not in mapped_sheets
                    and not s.endswith("---->") and not s.endswith(">")]
        if unmapped:
            logger.debug(f"  Unmapped tabs: {unmapped}")

    def _get_sheet(self, tab_type: str) -> Optional[Any]:
        """Get a worksheet by canonical tab type."""
        sheet_name = self._tab_map.get(tab_type)
        if sheet_name and sheet_name in self._wb.sheetnames:
            return self._wb[sheet_name]
        return None

    # -------------------------------------------------------------------------
    # Header detection helpers
    # -------------------------------------------------------------------------
    def _find_header_row(
        self, ws, keywords: list[str], max_rows: int = 30,
    ) -> Optional[int]:
        """Find the row number containing header keywords."""
        for row_idx in range(1, max_rows + 1):
            row_values = []
            for col in range(1, ws.max_column + 1 if ws.max_column else 50):
                cell = ws.cell(row=row_idx, column=col)
                if cell.value is not None:
                    row_values.append(str(cell.value).lower().strip())

            row_text = " ".join(row_values)
            for kw in keywords:
                if kw.lower() in row_text:
                    return row_idx

        return None

    def _get_column_map(
        self, ws, header_row: int,
    ) -> dict[str, int]:
        """Build a mapping of column header text → column index."""
        col_map = {}
        for col in range(1, (ws.max_column or 50) + 1):
            cell = ws.cell(row=header_row, column=col)
            if cell.value is not None:
                header_text = str(cell.value).strip()
                col_map[header_text] = col
                col_map[header_text.lower()] = col
        return col_map

    def _detect_year_columns(
        self, col_map: dict[str, int], first_only: bool = True,
    ) -> dict[int, int]:
        """Find columns that represent years (2024-2040 range).

        Args:
            col_map: header text → column index
            first_only: if True, keep only the first (leftmost) column for
                each year. This avoids picking up duplicate year columns
                from side-by-side tables (e.g., Peak Load Hour vs Peak
                Net Load Hour in the CDR).
        """
        year_cols = {}
        for header, col_idx in col_map.items():
            try:
                yr = int(header)
                if 2020 <= yr <= 2045:
                    if first_only:
                        # Keep only the leftmost occurrence
                        if yr not in year_cols or col_idx < year_cols[yr]:
                            year_cols[yr] = col_idx
                    else:
                        year_cols[yr] = col_idx
            except (ValueError, TypeError):
                pass
        return year_cols

    def _find_row_by_label(
        self,
        ws,
        labels: list[str],
        label_col: int = 1,
        start_row: int = 1,
        end_row: int = 200,
    ) -> Optional[int]:
        """Find a row whose label column matches one of the given labels."""
        for row_idx in range(start_row, end_row + 1):
            cell = ws.cell(row=row_idx, column=label_col)
            if cell.value is not None:
                cell_text = str(cell.value).strip().lower()
                for label in labels:
                    if label.lower() in cell_text:
                        return row_idx
        return None

    # -------------------------------------------------------------------------
    # Discovery mode
    # -------------------------------------------------------------------------
    def run_discovery(self, output_dir: str) -> dict:
        """Dump workbook structure: tabs, headers, year ranges, data samples."""
        self._load_workbook()
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        discovery = {
            "cdr_year": self.cdr_year,
            "cdr_edition": self.cdr_edition,
            "file": str(self.file_path),
            "sheets": [],
            "tab_mapping": dict(self._tab_map),
        }

        for sheet_name in self._wb.sheetnames:
            ws = self._wb[sheet_name]
            sheet_info = {
                "name": sheet_name,
                "max_row": ws.max_row,
                "max_column": ws.max_column,
                "sample_headers": [],
                "year_columns": {},
            }

            # Sample first 10 rows for headers
            for row_idx in range(1, min(11, (ws.max_row or 1) + 1)):
                row_vals = []
                for col in range(1, min(20, (ws.max_column or 1) + 1)):
                    cell = ws.cell(row=row_idx, column=col)
                    if cell.value is not None:
                        row_vals.append(str(cell.value).strip())
                if row_vals:
                    sheet_info["sample_headers"].append({
                        "row": row_idx,
                        "values": row_vals,
                    })

            # Detect year columns in first 10 rows
            for row_idx in range(1, min(11, (ws.max_row or 1) + 1)):
                col_map = self._get_column_map(ws, row_idx)
                year_cols = self._detect_year_columns(col_map)
                if year_cols:
                    sheet_info["year_columns"] = {
                        "header_row": row_idx,
                        "years": sorted(year_cols.keys()),
                    }
                    break

            discovery["sheets"].append(sheet_info)

        # Save
        disc_file = output_path / f"ercot_cdr_{self.cdr_year}_discovery.json"
        with open(disc_file, "w") as f:
            json.dump(discovery, f, indent=2, default=str)

        # Print summary
        logger.info(f"\n{'='*60}")
        logger.info(f"ERCOT CDR {self.cdr_edition} {self.cdr_year} Discovery")
        logger.info(f"{'='*60}")
        logger.info(f"File: {self.file_path}")
        logger.info(f"Sheets: {len(self._wb.sheetnames)}")
        for si in discovery["sheets"]:
            yr_info = si.get("year_columns", {})
            yr_str = f" — years: {yr_info.get('years', [])}" if yr_info else ""
            logger.info(f"  {si['name']:40s} ({si['max_row']} rows, {si['max_column']} cols){yr_str}")
        logger.info(f"\nTab mapping:")
        for canonical, actual in self._tab_map.items():
            logger.info(f"  {canonical:25s} → {actual}")
        logger.info(f"\nDiscovery saved to {disc_file}")

        return discovery

    # -------------------------------------------------------------------------
    # Load-Resource Scenarios parsing (primary data: demand, capacity, RM)
    # -------------------------------------------------------------------------
    def _parse_load_resource_scenarios(
        self, scenario_name: str,
    ) -> dict[str, dict[str, float]]:
        """
        Parse the "Load-Resource Scenarios" tab for peak demand, total
        capacity, and reserve margin — the main CDR scenario data.

        The sheet has:
          - Table 1: Summer (rows ~3-16)
          - Table 2: Winter (rows ~19-32)
          - Year columns: 2026-2030 in row 3/19
          - Different scenarios as different ROWS (not tabs)
          - Labels in column B (col 2)

        Returns: {variable_key: {year_str: value}}
        """
        results: dict[str, dict[str, float]] = {}

        ws = self._get_sheet("load_resource_scenarios")
        if ws is None:
            logger.warning("No Load-Resource Scenarios tab found")
            return results

        scenario_info = SCENARIO_MAP[scenario_name]

        # Find summer table header row (has year columns)
        # IMPORTANT: The sheet has side-by-side tables:
        #   Peak Load Hour (cols C-G) | Peak Net Load Hour (cols J-N)
        # Both have identical year headers. We want Peak Load Hour only.
        # Strategy: scan left-to-right, collect years, stop at first gap
        # after finding consecutive years.
        year_cols = {}
        header_row = None
        for row_idx in range(1, 25):
            found_years = {}
            found_any_year = False
            for col in range(2, min((ws.max_column or 20) + 1, 30)):
                cell = ws.cell(row=row_idx, column=col)
                if cell.value is not None:
                    try:
                        yr = int(str(cell.value).strip())
                        if 2020 <= yr <= 2045:
                            found_years[yr] = col
                            found_any_year = True
                            continue
                    except (ValueError, TypeError):
                        pass
                # Non-year cell — if we already found years, stop
                # (this prevents picking up the duplicate Peak Net Load columns)
                if found_any_year and cell.value is not None:
                    # Hit a non-year cell after finding years — check if it's
                    # a section label (indicating a new side-by-side table)
                    break
                elif found_any_year and cell.value is None:
                    # Blank separator column — stop here
                    break
            if found_years:
                year_cols = found_years
                header_row = row_idx
                break

        if not year_cols:
            logger.warning("No year columns found in Load-Resource Scenarios")
            return results

        years_found = sorted(year_cols.keys())
        if self.cdr_horizon_end is None:
            self.cdr_horizon_end = max(years_found)
        self._discovered_years = years_found
        logger.info(f"  CDR years: {years_found[0]}–{years_found[-1]} "
                     f"(header row {header_row})")

        # Helper: find a row by matching label text in col B
        def find_row(labels: list[str], start: int, end: int) -> Optional[int]:
            for row_idx in range(start, end + 1):
                cell = ws.cell(row=row_idx, column=2)
                if cell.value is None:
                    continue
                cell_text = str(cell.value).strip()
                for label in labels:
                    if label.lower() in cell_text.lower():
                        return row_idx
            return None

        # Helper: read year values from a row
        def read_row_values(
            row_idx: int, converter,
        ) -> dict[str, float]:
            annual = {}
            for yr, col_idx in year_cols.items():
                if yr < self.start_year:
                    continue
                cell = ws.cell(row=row_idx, column=col_idx)
                if cell.value is not None:
                    try:
                        raw = float(cell.value)
                        annual[str(yr)] = round(converter(raw), 6)
                    except (ValueError, TypeError):
                        pass
            return annual

        search_end = min((ws.max_row or 50) + 1, 60)

        # --- Peak Demand ---
        demand_row = find_row(
            scenario_info["demand_labels"], header_row + 1, search_end,
        )
        if demand_row:
            conv = CONVERSION_TABLE["mw_to_gw"]
            annual = read_row_values(demand_row, conv)
            if annual:
                results["ercot_peak_demand"] = annual
                label = str(ws.cell(row=demand_row, column=2).value).strip()[:50]
                logger.info(f"  ercot_peak_demand: {len(annual)} yrs from "
                            f"row {demand_row} '{label}'")

        # --- Total Capacity ---
        cap_row = find_row(
            scenario_info["capacity_labels"], header_row + 1, search_end,
        )
        if cap_row:
            conv = CONVERSION_TABLE["mw_to_gw"]
            annual = read_row_values(cap_row, conv)
            if annual:
                results["ercot_installed_capacity"] = annual
                label = str(ws.cell(row=cap_row, column=2).value).strip()[:50]
                logger.info(f"  ercot_installed_capacity: {len(annual)} yrs from "
                            f"row {cap_row} '{label}'")

        # --- Reserve Margin ---
        # Find the reserve margins section first
        rm_section = find_row(["Reserve Margin"], header_row + 1, search_end)
        if rm_section:
            rm_row = find_row(
                scenario_info["rm_labels"], rm_section + 1, search_end,
            )
            if rm_row:
                # CDR reports RM as fraction already (0.17 = 17%)
                conv = CONVERSION_TABLE["none"]
                annual = read_row_values(rm_row, conv)
                if annual:
                    results["ercot_reserve_margin"] = annual
                    label = str(ws.cell(row=rm_row, column=2).value).strip()[:50]
                    logger.info(f"  ercot_reserve_margin: {len(annual)} yrs from "
                                f"row {rm_row} '{label}'")

        return results

    # -------------------------------------------------------------------------
    # Capacity by Resource Type parsing (fuel-type breakdown)
    # -------------------------------------------------------------------------
    def _parse_capacity_by_resource(
        self, scenario_name: str,
    ) -> dict[str, dict[str, float]]:
        """
        Parse the "Capacity by Resource Type" tab for fuel-type capacity.

        Structure:
          - Operational Resources (rows ~4-31): existing capacity
          - Planned Resources (rows ~32-55): committed additions
          - Total Resources (row ~57)
          - Column headers are seasonal: "Summer 2026", "Summer 2030", etc.
          - Labels in column B (col 2)
          - Only 2 year snapshots per season (start/end of CDR horizon)

        Strategy: sum Operational + Planned for each fuel category.
        Use summer ratings. Interpolate intermediate years linearly.

        Returns: {variable_key: {year_str: value}}
        """
        results: dict[str, dict[str, float]] = {}

        ws = self._get_sheet("capacity_by_resource")
        if ws is None:
            logger.warning("No Capacity by Resource Type tab found")
            return results

        # Find header rows and parse seasonal year columns
        # Headers look like: "Summer 2026", "Summer 2030", "Winter 2026/27"
        # We want summer columns for nameplate capacity
        summer_year_cols: dict[int, int] = {}

        for row_idx in range(1, 10):
            for col in range(2, min((ws.max_column or 20) + 1, 50)):
                cell = ws.cell(row=row_idx, column=col)
                if cell.value is None:
                    continue
                header = str(cell.value).strip()
                # Match "Summer YYYY" pattern
                if header.lower().startswith("summer"):
                    parts = header.split()
                    if len(parts) == 2:
                        try:
                            yr = int(parts[1])
                            if 2020 <= yr <= 2045:
                                summer_year_cols[yr] = col
                        except ValueError:
                            pass

        if not summer_year_cols:
            logger.warning("No summer year columns found in Capacity by Resource Type")
            return results

        years_found = sorted(summer_year_cols.keys())
        logger.info(f"  Capacity tab summer years: {years_found}")

        # Parse both Operational and Planned sections
        # We need to find the top-level fuel rows (not sub-categories)
        # Operational: "Natural Gas" (row ~5), "Coal" (~11), "Nuclear" (~12),
        #              "Solar" (~14), "Wind" (~18), "Batteries" (~26)
        # Planned: same labels starting at row ~32

        # Build fuel totals (operational + planned) per variable
        for var_key, spec in VARIABLE_MAP_ERCOT.items():
            if spec.get("source_tab") != "capacity_by_resource":
                continue

            fuel_labels = spec.get("fuel_labels", [])
            conv = CONVERSION_TABLE[spec.get("unit_conversion", "none")]

            # Find matching rows across entire sheet, sum operational + planned
            year_totals: dict[int, float] = defaultdict(float)
            matched_rows = []

            # We want the top-level aggregation row for each fuel,
            # not the sub-categories (e.g., "Natural Gas" not "Combined-cycle")
            for row_idx in range(3, min((ws.max_row or 60) + 1, 70)):
                cell = ws.cell(row=row_idx, column=2)
                if cell.value is None:
                    continue
                cell_text = str(cell.value).strip()

                for label in fuel_labels:
                    if cell_text == label or cell_text.lower() == label.lower():
                        # Exact match — read summer values
                        for yr, col_idx in summer_year_cols.items():
                            val_cell = ws.cell(row=row_idx, column=col_idx)
                            if val_cell.value is not None:
                                try:
                                    year_totals[yr] += float(val_cell.value)
                                    matched_rows.append(row_idx)
                                except (ValueError, TypeError):
                                    pass
                        break

            if not year_totals:
                logger.warning(f"  {var_key}: no data (tried labels: {fuel_labels})")
                continue

            # Interpolate intermediate years linearly
            annual = {}
            yr_list = sorted(year_totals.keys())
            for yr in yr_list:
                annual[str(yr)] = round(conv(year_totals[yr]), 4)

            # Fill gaps between snapshots
            if len(yr_list) >= 2:
                for i in range(len(yr_list) - 1):
                    y1, y2 = yr_list[i], yr_list[i + 1]
                    v1, v2 = year_totals[y1], year_totals[y2]
                    for yr in range(y1 + 1, y2):
                        if yr >= self.start_year:
                            frac = (yr - y1) / (y2 - y1)
                            interp = v1 + frac * (v2 - v1)
                            annual[str(yr)] = round(conv(interp), 4)

            if annual:
                results[var_key] = annual
                mw_vals = list(year_totals.values())
                logger.info(
                    f"  {var_key}: {len(annual)} yrs "
                    f"({min(mw_vals):.0f}–{max(mw_vals):.0f} MW, "
                    f"{len(yr_list)} snapshots + {len(annual) - len(yr_list)} interpolated)"
                )

        return results

    # -------------------------------------------------------------------------
    # Load forecast parsing (not available in CDR; placeholder)
    # -------------------------------------------------------------------------
    def _parse_load_forecast(
        self, scenario_name: str,
    ) -> dict[str, dict[str, float]]:
        """
        Parse load forecast for annual energy demand.

        Note: the CDR does not contain energy demand (GWh/TWh) — only peak
        demand (MW). Energy forecasts come from separate ERCOT publications.
        This method is kept as a placeholder for future integration.
        """
        results = {}

        # CDR doesn't have a load forecast tab — energy demand is not in scope
        logger.info("  ercot_energy_demand: not in CDR (peak demand only)")
        return results

    def _parse_load_forecast_UNUSED(
        self, scenario_name: str,
    ) -> dict[str, dict[str, float]]:
        """Original load forecast parser — kept for reference."""
        results = {}

        ws = self._get_sheet("load_forecast")
        if ws is None:
            return results

        year_cols = {}
        header_row = None
        for row_idx in range(1, 30):
            col_map = self._get_column_map(ws, row_idx)
            yc = self._detect_year_columns(col_map)
            if yc:
                year_cols = yc
                header_row = row_idx
                break

        if not year_cols:
            logger.warning("No year columns in load forecast tab")
            return results

        spec = VARIABLE_MAP_ERCOT["ercot_energy_demand"]
        field_labels = spec.get("field_labels", [])
        conv = CONVERSION_TABLE[spec["unit_conversion"]]

        found_row = self._find_row_by_label(
            ws, field_labels,
            start_row=header_row + 1,
        )

        if found_row:
            annual = {}
            for yr, col_idx in year_cols.items():
                if yr < self.start_year:
                    continue
                cell = ws.cell(row=found_row, column=col_idx)
                if cell.value is not None:
                    try:
                        annual[str(yr)] = round(conv(float(cell.value)), 4)
                    except (ValueError, TypeError):
                        pass
            if annual:
                results["ercot_energy_demand"] = annual
                logger.info(f"  ercot_energy_demand: {len(annual)} years from load forecast")

        return results

    # -------------------------------------------------------------------------
    # Derived variables
    # -------------------------------------------------------------------------
    def _compute_derived(self, outlook: dict):
        """Compute derived variables from populated data."""
        vars_ = outlook["variables"]

        # Generation mix = capacity share by fuel (as dict of fuel→fraction)
        # For the canonical variable we store total renewable fraction
        if "ercot_generation_mix" in vars_:
            total_cap = vars_.get("ercot_installed_capacity", {}).get("annual", {})
            solar_cap = vars_.get("ercot_solar_capacity", {}).get("annual", {})
            wind_cap = vars_.get("ercot_wind_capacity", {}).get("annual", {})

            mix = {}
            for yr in total_cap:
                total = total_cap.get(yr, 0)
                if total > 0:
                    solar = solar_cap.get(yr, 0)
                    wind = wind_cap.get(yr, 0)
                    renewable_share = (solar + wind) / total
                    mix[yr] = round(renewable_share, 4)

            if mix:
                vars_["ercot_generation_mix"]["annual"] = mix
                logger.info(f"  ercot_generation_mix: derived renewable capacity "
                            f"share for {len(mix)} years")

    # -------------------------------------------------------------------------
    # Horizon extrapolation
    # -------------------------------------------------------------------------
    def _extrapolate_to_horizon(
        self, annual: dict[str, float], var_key: str,
    ) -> tuple[dict[str, float], dict[str, bool]]:
        """
        Extend CDR 5-year data to self.end_year using trend fitting.

        Returns:
            (extended_annual, extrapolation_flags)
            extrapolation_flags: {year_str: True} for extrapolated years
        """
        if not annual:
            return annual, {}

        years = sorted(int(y) for y in annual.keys())
        max_cdr_year = max(years)
        flags = {}

        if max_cdr_year >= self.end_year:
            return annual, flags

        if not self.extrapolate:
            return annual, flags

        # Determine extrapolation method based on variable type
        values = [annual[str(y)] for y in years]

        extended = dict(annual)

        # Reserve margin: hold flat at terminal value
        if "reserve_margin" in var_key:
            terminal = values[-1]
            for yr in range(max_cdr_year + 1, self.end_year + 1):
                extended[str(yr)] = terminal
                flags[str(yr)] = True

        # Capacity variables: fit CAGR from available data
        elif any(kw in var_key for kw in ["capacity", "installed"]):
            if len(values) >= 2 and values[0] > 0 and values[-1] > 0:
                n_years = years[-1] - years[0]
                if n_years > 0:
                    cagr = (values[-1] / values[0]) ** (1.0 / n_years) - 1
                    # Cap growth rate to prevent absurd extrapolation
                    cagr = min(cagr, 0.25)  # max 25% annual growth
                    cagr = max(cagr, -0.10)  # max 10% annual decline

                    last_val = values[-1]
                    for yr in range(max_cdr_year + 1, self.end_year + 1):
                        last_val = last_val * (1 + cagr)
                        extended[str(yr)] = round(last_val, 4)
                        flags[str(yr)] = True

        # Demand variables: linear extrapolation
        elif any(kw in var_key for kw in ["demand", "energy", "peak"]):
            if len(values) >= 2:
                n_years = years[-1] - years[0]
                if n_years > 0:
                    annual_growth = (values[-1] - values[0]) / n_years
                    last_val = values[-1]
                    for yr in range(max_cdr_year + 1, self.end_year + 1):
                        last_val += annual_growth
                        extended[str(yr)] = round(last_val, 4)
                        flags[str(yr)] = True

        # Generation mix: logistic growth capped at 1.0
        elif "mix" in var_key or "share" in var_key:
            if len(values) >= 2:
                annual_growth = (values[-1] - values[0]) / (years[-1] - years[0])
                last_val = values[-1]
                for yr in range(max_cdr_year + 1, self.end_year + 1):
                    last_val = min(last_val + annual_growth * 0.9, 1.0)  # decelerating
                    extended[str(yr)] = round(last_val, 4)
                    flags[str(yr)] = True

        # Default: linear
        else:
            if len(values) >= 2:
                n_years = years[-1] - years[0]
                if n_years > 0:
                    slope = (values[-1] - values[0]) / n_years
                    last_val = values[-1]
                    for yr in range(max_cdr_year + 1, self.end_year + 1):
                        last_val += slope
                        extended[str(yr)] = round(last_val, 4)
                        flags[str(yr)] = True

        if flags:
            logger.info(f"  {var_key}: extrapolated {len(flags)} years "
                        f"({max_cdr_year+1}–{self.end_year})")

        return extended, flags

    # -------------------------------------------------------------------------
    # Core extraction
    # -------------------------------------------------------------------------
    def extract_scenario(self, scenario_name: str) -> dict:
        """Extract all ERCOT variables for a single scenario."""
        if scenario_name not in SCENARIO_MAP:
            raise ValueError(f"Unknown scenario: {scenario_name}. "
                             f"Available: {list(SCENARIO_MAP.keys())}")

        logger.info(f"\n{'='*60}")
        logger.info(f"Extracting ERCOT CDR scenario: {scenario_name}")
        logger.info(f"{'='*60}")

        self._load_workbook()
        outlook = build_empty_outlook("ercot", scenario_name, self.start_year, self.end_year)
        outlook["scenario_description"] = SCENARIO_MAP[scenario_name]["description"]
        outlook["extraction_metadata"] = {
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_method": "excel_parse",
            "confidence_score": 0.90,
            "source_document": str(self.file_path.name),
            "notes": (
                f"CDR {self.cdr_edition} {self.cdr_year}. "
                f"CDR horizon: {self.start_year}–{self.cdr_horizon_end or '?'}. "
                f"Values beyond CDR horizon are extrapolated (confidence=0.50). "
                f"Capacity values are nameplate MW. "
            ),
        }

        # Parse all data sources
        seasonal_data = self._parse_load_resource_scenarios(scenario_name)
        resource_data = self._parse_capacity_by_resource(scenario_name)
        load_data = self._parse_load_forecast(scenario_name)

        # Merge all parsed data into outlook
        all_extrapolation_flags: dict[str, dict[str, bool]] = {}

        for data_dict in [seasonal_data, resource_data, load_data]:
            for var_key, annual in data_dict.items():
                if var_key in outlook["variables"]:
                    # Extrapolate if needed
                    extended, flags = self._extrapolate_to_horizon(annual, var_key)
                    outlook["variables"][var_key]["annual"] = extended
                    if flags:
                        all_extrapolation_flags[var_key] = flags

        # Compute derived variables
        self._compute_derived(outlook)

        # Extrapolate derived variables too
        for var_key in ["ercot_generation_mix"]:
            if var_key in outlook["variables"]:
                annual = outlook["variables"][var_key].get("annual", {})
                if annual:
                    extended, flags = self._extrapolate_to_horizon(annual, var_key)
                    outlook["variables"][var_key]["annual"] = extended
                    if flags:
                        all_extrapolation_flags[var_key] = flags

        # Store extrapolation metadata
        if all_extrapolation_flags:
            outlook["extraction_metadata"]["extrapolation_flags"] = all_extrapolation_flags
            n_extrap = sum(len(f) for f in all_extrapolation_flags.values())
            outlook["extraction_metadata"]["notes"] += (
                f"Extrapolated {n_extrap} data points beyond CDR horizon."
            )

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
        filename = f"ercot_cdr_{self.cdr_year}_{scenario}.json"
        filepath = output_path / filename

        with open(filepath, "w") as f:
            json.dump(outlook, f, indent=2, default=str)

        logger.info(f"Saved JSON: {filepath}")

    def save_csv(self, outlook: dict, output_dir: str):
        """Save a single scenario as flat CSV."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        scenario = outlook["scenario_name"]
        filename = f"ercot_cdr_{self.cdr_year}_{scenario}.csv"
        filepath = output_path / filename

        years = [str(y) for y in range(self.start_year, self.end_year + 1)]
        headers = ["variable", "unit", "category"] + years

        with open(filepath, "w") as f:
            f.write(",".join(headers) + "\n")
            for var_key, var_data in outlook["variables"].items():
                annual = var_data.get("annual", {})
                row_vals = [
                    var_key,
                    var_data.get("unit", "").replace(",", ";"),
                    var_data.get("category", ""),
                ]
                for yr in years:
                    row_vals.append(str(annual.get(yr, "")))
                f.write(",".join(row_vals) + "\n")

        logger.info(f"Saved CSV: {filepath}")

    def save_all(self, all_outlooks: dict[str, dict], output_dir: str):
        """Save all scenarios as individual JSON/CSV + combined CSV."""
        for scenario_name, outlook in all_outlooks.items():
            self.save_json(outlook, output_dir)
            self.save_csv(outlook, output_dir)

        # Combined CSV
        output_path = Path(output_dir)
        combined_file = output_path / f"ercot_cdr_{self.cdr_year}_all_scenarios.csv"
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
                        row_vals.append(str(annual.get(yr, "")))
                    f.write(",".join(row_vals) + "\n")

        logger.info(f"Saved combined CSV: {combined_file}")

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------
    def validate_outlook(self, outlook: dict) -> dict:
        """Validate completeness and quality by priority tier."""
        scenario = outlook["scenario_name"]

        # Count only ERCOT variables (not all 176)
        ercot_vars = get_variables_for_source("ercot")
        total_vars = 0
        populated = 0
        empty = 0
        partial = 0
        p1_total = 0
        p1_pop = 0
        issues = []

        cdr_years_expected = len(self._discovered_years) if self._discovered_years else 5

        for var_key in ercot_vars:
            if var_key not in outlook["variables"]:
                continue
            total_vars += 1
            var_data = outlook["variables"][var_key]
            annual = var_data.get("annual", {})
            priority = VARIABLE_CATALOG.get(var_key, {}).get("priority", 3)

            if priority == 1:
                p1_total += 1

            if not annual:
                empty += 1
                level = "ERROR" if priority == 1 else "WARNING"
                issues.append({"level": level, "var": var_key, "msg": f"P{priority} variable empty"})
            else:
                # Count CDR-horizon years only for primary validation
                cdr_year_count = sum(
                    1 for y in annual
                    if int(y) <= (self.cdr_horizon_end or 2030)
                )
                if cdr_year_count >= max(1, cdr_years_expected * 0.8):
                    populated += 1
                    if priority == 1:
                        p1_pop += 1
                else:
                    partial += 1
                    issues.append({
                        "level": "WARNING",
                        "var": var_key,
                        "msg": f"Only {cdr_year_count}/{cdr_years_expected} CDR-horizon years",
                    })

                # Sanity checks
                vals = [v for v in annual.values() if isinstance(v, (int, float))]
                if vals and min(vals) < -0.5:
                    issues.append({
                        "level": "WARNING",
                        "var": var_key,
                        "msg": f"Negative values (min={min(vals):.2f})",
                    })

        report = {
            "scenario": scenario,
            "total_ercot_variables": total_vars,
            "populated": populated,
            "partial": partial,
            "empty": empty,
            "completeness_pct": round(populated / total_vars * 100, 1) if total_vars else 0,
            "priority_1": {
                "total": p1_total,
                "populated": p1_pop,
                "pct": round(p1_pop / p1_total * 100, 1) if p1_total else 0,
            },
            "cdr_horizon": f"{self.start_year}–{self.cdr_horizon_end or '?'}",
            "extrapolated_to": self.end_year if self.extrapolate else None,
            "issues": issues,
        }

        logger.info(f"\nValidation — {scenario}:")
        logger.info(f"  ERCOT vars: {populated}/{total_vars} populated ({report['completeness_pct']}%)")
        logger.info(f"  P1: {p1_pop}/{p1_total} ({report['priority_1']['pct']}%)")
        logger.info(f"  CDR horizon: {report['cdr_horizon']}")
        if self.extrapolate:
            logger.info(f"  Extrapolated to: {self.end_year}")
        for issue in issues[:10]:
            logger.info(f"  [{issue['level']}] {issue['var']}: {issue['msg']}")
        if len(issues) > 10:
            logger.info(f"  ... and {len(issues) - 10} more issues")

        return report


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="DecarbIQ ERCOT CDR Projection Extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 ercot_cdr_extractor.py --file CDR.xlsx --discover
  python3 ercot_cdr_extractor.py --file CDR.xlsx --scenarios base
  python3 ercot_cdr_extractor.py --file CDR.xlsx
  python3 ercot_cdr_extractor.py --file CDR.xlsx --no-extrapolate
  python3 ercot_cdr_extractor.py --verbose
        """,
    )
    parser.add_argument("--file", required=True,
                        help="Path to CDR Excel workbook (.xlsx)")
    parser.add_argument("--cdr-year", type=int, default=2025,
                        help="CDR report year (default: 2025)")
    parser.add_argument("--cdr-edition", default="May",
                        help="CDR edition (e.g., May, December)")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="Specific scenarios (default: all three)")
    parser.add_argument("--output-dir", default="./output",
                        help="Output directory (default: ./output)")
    parser.add_argument("--discover", action="store_true",
                        help="Run discovery mode (dump workbook structure)")
    parser.add_argument("--no-extrapolate", action="store_true",
                        help="Don't extrapolate beyond CDR horizon")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("DecarbIQ ERCOT CDR Projection Extractor")
    logger.info(f"CDR: {args.cdr_edition} {args.cdr_year}")

    extractor = ERCOTCDRExtractor(
        file_path=args.file,
        cdr_year=args.cdr_year,
        cdr_edition=args.cdr_edition,
        extrapolate=not args.no_extrapolate,
    )

    if args.discover:
        extractor.run_discovery(args.output_dir)
        return

    scenarios = args.scenarios or list(SCENARIO_MAP.keys())
    logger.info(f"Extracting {len(scenarios)} scenarios: {scenarios}")

    all_outlooks = {}
    all_validations = {}

    for scenario in scenarios:
        outlook = extractor.extract_scenario(scenario)
        validation = extractor.validate_outlook(outlook)
        all_outlooks[scenario] = outlook
        all_validations[scenario] = validation

    extractor.save_all(all_outlooks, args.output_dir)

    # Validation report
    val_file = Path(args.output_dir) / f"ercot_cdr_{args.cdr_year}_validation.json"
    with open(val_file, "w") as f:
        json.dump(all_validations, f, indent=2)
    logger.info(f"Saved validation report: {val_file}")

    logger.info("\nExtraction complete.")


if __name__ == "__main__":
    main()
