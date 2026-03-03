#!/usr/bin/env python3
"""
IEA Global EV Data Explorer Extractor
======================================

Extracts electric vehicle market data from the IEA Global EV Outlook 2025
data file and produces structured JSON + CSV outputs.

Data source:
    IEA Global EV Data Explorer 2025 (based on EV Volumes, Marklines, ACEA, etc.)
    Sheet: GEVO_EV_2025 (16,436 rows, flat/long format)

Coverage:
    - 63 countries and regions (including World, continent aggregates)
    - 9 parameters: EV sales, stock, shares, battery demand, charging, oil displacement
    - 6 vehicle modes: Cars, Vans, Trucks, Buses, 2/3 wheelers, EV (aggregate)
    - 6 powertrains: BEV, PHEV, FCEV, EV (aggregate), charging point types
    - Years: 2010-2024 (historical) + 2030 (STEPS projection)

Output:
    - Per-country JSON files (US first)
    - Combined global JSON + flat CSV

Usage:
    python3 iea_ev_extractor.py --file "data/EV Data Explorer 2025.xlsx" --discover
    python3 iea_ev_extractor.py --file "data/EV Data Explorer 2025.xlsx" --country USA
    python3 iea_ev_extractor.py --file "data/EV Data Explorer 2025.xlsx"
    python3 iea_ev_extractor.py --file "data/EV Data Explorer 2025.xlsx" --verbose
"""

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import openpyxl

logger = logging.getLogger("iea_ev_extractor")

# =============================================================================
# Constants
# =============================================================================

SHEET_NAME = "GEVO_EV_2025"

# Column indices in the main sheet
COL_COUNTRY = 0
COL_CATEGORY = 1
COL_PARAMETER = 2
COL_MODE = 3
COL_POWERTRAIN = 4
COL_YEAR = 5
COL_UNIT = 6
COL_VALUE = 7
COL_AGG_GROUP = 8

# Canonical parameter keys
PARAMETER_KEY_MAP = {
    "EV sales": "ev_sales",
    "EV stock": "ev_stock",
    "EV sales share": "ev_sales_share",
    "EV stock share": "ev_stock_share",
    "Battery demand": "battery_demand",
    "Electricity demand": "electricity_demand",
    "EV charging points": "ev_charging_points",
    "Oil displacement Mbd": "oil_displacement_mbd",
    "Oil displacement, million lge": "oil_displacement_lge",
}

# Canonical mode keys
MODE_KEY_MAP = {
    "Cars": "cars",
    "Vans": "vans",
    "Trucks": "trucks",
    "Buses": "buses",
    "2 and 3 wheelers": "2_3_wheelers",
    "EV": "ev_total",
}

# Canonical powertrain keys
POWERTRAIN_KEY_MAP = {
    "BEV": "BEV",
    "PHEV": "PHEV",
    "FCEV": "FCEV",
    "EV": "EV_total",
    "Publicly available fast": "charging_fast",
    "Publicly available slow": "charging_slow",
}

# Country output priority order
COUNTRY_PRIORITY = [
    "USA", "Canada", "Mexico",
    "China", "Japan", "Korea", "India", "Indonesia", "Thailand",
    "Germany", "France", "United Kingdom", "Norway", "Netherlands",
    "Sweden", "Italy", "Spain", "Poland", "Belgium", "Austria",
    "Australia", "Brazil", "Chile", "South Africa",
]

# Regional aggregates (not individual countries)
REGIONAL_AGGREGATES = {
    "World", "Africa", "Asia Pacific", "Central and South America",
    "EU27", "Europe", "Middle East", "North America", "Other",
    "Rest of the world",
}


# =============================================================================
# Extractor class
# =============================================================================

class IEAEVExtractor:
    """Extract IEA Global EV Data Explorer data."""

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"EV data file not found: {self.file_path}")
        self._raw_data = None

    def _load_data(self):
        """Load all rows from the GEVO_EV_2025 sheet."""
        if self._raw_data is not None:
            return

        wb = openpyxl.load_workbook(str(self.file_path), read_only=True, data_only=True)
        ws = wb[SHEET_NAME]

        self._raw_data = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                continue  # skip header
            vals = list(row)
            if len(vals) < 8 or vals[COL_COUNTRY] is None:
                continue
            self._raw_data.append({
                "country": str(vals[COL_COUNTRY]).strip(),
                "category": str(vals[COL_CATEGORY]).strip(),
                "parameter": str(vals[COL_PARAMETER]).strip(),
                "mode": str(vals[COL_MODE]).strip(),
                "powertrain": str(vals[COL_POWERTRAIN]).strip(),
                "year": int(vals[COL_YEAR]) if vals[COL_YEAR] else None,
                "unit": str(vals[COL_UNIT]).strip() if vals[COL_UNIT] else "",
                "value": self._to_float(vals[COL_VALUE]),
                "agg_group": str(vals[COL_AGG_GROUP]).strip() if len(vals) > COL_AGG_GROUP and vals[COL_AGG_GROUP] else "",
            })

        wb.close()
        logger.info(f"Loaded {len(self._raw_data)} rows from {SHEET_NAME}")

    @staticmethod
    def _to_float(v) -> Optional[float]:
        """Convert a value to float, returning None if not possible."""
        if v is None:
            return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------

    def discover(self):
        """Print inventory of available dimensions."""
        self._load_data()

        countries = set()
        params = set()
        modes = set()
        powertrains = set()
        years = set()
        units = set()
        categories = set()

        for row in self._raw_data:
            countries.add(row["country"])
            params.add(row["parameter"])
            modes.add(row["mode"])
            powertrains.add(row["powertrain"])
            if row["year"]:
                years.add(row["year"])
            units.add(row["unit"])
            categories.add(row["category"])

        actual_countries = sorted(countries - REGIONAL_AGGREGATES)
        regions = sorted(countries & REGIONAL_AGGREGATES)

        print(f"\n{'='*70}")
        print(f"IEA Global EV Data Explorer 2025")
        print(f"File: {self.file_path.name}")
        print(f"Total rows: {len(self._raw_data)}")
        print(f"{'='*70}")

        print(f"\nCountries ({len(actual_countries)}):")
        for c in actual_countries:
            count = sum(1 for r in self._raw_data if r["country"] == c)
            print(f"  {c:30s} ({count} rows)")

        print(f"\nRegional Aggregates ({len(regions)}):")
        for r in regions:
            count = sum(1 for row in self._raw_data if row["country"] == r)
            print(f"  {r:30s} ({count} rows)")

        print(f"\nCategories: {sorted(categories)}")
        print(f"Parameters ({len(params)}): {sorted(params)}")
        print(f"Modes ({len(modes)}): {sorted(modes)}")
        print(f"Powertrains ({len(powertrains)}): {sorted(powertrains)}")
        print(f"Years ({len(years)}): {sorted(years)}")
        print(f"Units ({len(units)}): {sorted(units)}")

        # US summary
        us_rows = [r for r in self._raw_data if r["country"] == "USA"]
        if us_rows:
            print(f"\n--- USA Data Summary ---")
            print(f"Total rows: {len(us_rows)}")
            us_params = set(r["parameter"] for r in us_rows)
            for p in sorted(us_params):
                p_rows = [r for r in us_rows if r["parameter"] == p]
                years_avail = sorted(set(r["year"] for r in p_rows if r["year"]))
                print(f"  {p:40s} years={years_avail[0]}-{years_avail[-1]} ({len(p_rows)} rows)")

    # -----------------------------------------------------------------
    # Extraction
    # -----------------------------------------------------------------

    def extract_country(self, country_name: str) -> dict:
        """
        Extract structured data for one country.

        Returns:
            {
                parameter_key: {
                    mode_key: {
                        powertrain_key: {
                            "unit": str,
                            "category": str,
                            "values": {year_int: value}
                        }
                    }
                }
            }
        """
        self._load_data()

        rows = [r for r in self._raw_data if r["country"] == country_name]
        if not rows:
            logger.warning(f"No data found for country: {country_name}")
            return {}

        result = {}
        for row in rows:
            if row["value"] is None or row["year"] is None:
                continue

            param_key = PARAMETER_KEY_MAP.get(row["parameter"], row["parameter"])
            mode_key = MODE_KEY_MAP.get(row["mode"], row["mode"])
            pt_key = POWERTRAIN_KEY_MAP.get(row["powertrain"], row["powertrain"])

            if param_key not in result:
                result[param_key] = {}
            if mode_key not in result[param_key]:
                result[param_key][mode_key] = {}
            if pt_key not in result[param_key][mode_key]:
                result[param_key][mode_key][pt_key] = {
                    "unit": row["unit"],
                    "category": row["category"],
                    "values": {},
                }

            entry = result[param_key][mode_key][pt_key]
            entry["values"][row["year"]] = row["value"]
            # Keep most recent category label
            if row["category"] == "Historical":
                pass  # historical is default
            else:
                entry["category"] = row["category"]

        # Sort values by year
        for param in result.values():
            for mode in param.values():
                for pt in mode.values():
                    pt["values"] = dict(sorted(pt["values"].items()))

        return result

    def extract_all(self) -> dict[str, dict]:
        """Extract data for all countries and regions."""
        self._load_data()

        all_entities = sorted(set(r["country"] for r in self._raw_data))

        result = {}
        for entity in all_entities:
            data = self.extract_country(entity)
            if data:
                result[entity] = data
                logger.info(f"  Extracted {entity}: {sum(len(m) for p in data.values() for m in p.values())} series")

        return result

    # -----------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------

    def save_json(
        self,
        output_dir: str,
        data: dict[str, dict],
        countries: Optional[list[str]] = None,
    ) -> list[Path]:
        """Save JSON output files."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        metadata = {
            "source": "IEA Global EV Data Explorer 2025",
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "file": self.file_path.name,
            "total_countries": len(data),
        }

        saved = []

        # Per-country files (if specific countries requested)
        if countries:
            for country in countries:
                if country not in data:
                    logger.warning(f"Country {country} not in extracted data")
                    continue
                payload = {
                    **metadata,
                    "country": country,
                    "data": data[country],
                }
                fpath = out / f"iea_ev_2025_{country.lower().replace(' ', '_')}.json"
                with open(fpath, "w") as f:
                    json.dump(payload, f, indent=2)
                saved.append(fpath)
                logger.info(f"Saved: {fpath}")

        # Global combined file
        payload = {
            **metadata,
            "countries": data,
        }
        fpath = out / "iea_ev_2025_global.json"
        with open(fpath, "w") as f:
            json.dump(payload, f, indent=2)
        saved.append(fpath)
        logger.info(f"Saved: {fpath}")

        return saved

    def save_csv(self, output_dir: str, data: dict[str, dict]) -> Path:
        """Save flat CSV with all data."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Collect all years
        all_years = set()
        for country_data in data.values():
            for param_data in country_data.values():
                for mode_data in param_data.values():
                    for pt_data in mode_data.values():
                        all_years.update(pt_data["values"].keys())
        all_years = sorted(all_years)

        fpath = out / "iea_ev_2025_all.csv"
        with open(fpath, "w", newline="") as f:
            writer = csv.writer(f)
            header = ["country", "parameter", "mode", "powertrain", "unit"] + [str(y) for y in all_years]
            writer.writerow(header)

            # Sort countries: priority list first, then alphabetical
            priority_set = set(COUNTRY_PRIORITY)
            countries_sorted = []
            for c in COUNTRY_PRIORITY:
                if c in data:
                    countries_sorted.append(c)
            for c in sorted(data.keys()):
                if c not in priority_set:
                    countries_sorted.append(c)

            for country in countries_sorted:
                country_data = data[country]
                for param_key in sorted(country_data.keys()):
                    for mode_key in sorted(country_data[param_key].keys()):
                        for pt_key in sorted(country_data[param_key][mode_key].keys()):
                            entry = country_data[param_key][mode_key][pt_key]
                            row = [country, param_key, mode_key, pt_key, entry["unit"]]
                            for yr in all_years:
                                val = entry["values"].get(yr)
                                row.append(str(val) if val is not None else "")
                            writer.writerow(row)

        logger.info(f"Saved: {fpath}")
        return fpath


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="IEA Global EV Data Explorer Extractor"
    )
    parser.add_argument(
        "--file", required=True,
        help="Path to EV Data Explorer 2025 Excel file",
    )
    parser.add_argument(
        "--output-dir", default="output/",
        help="Output directory (default: output/)",
    )
    parser.add_argument(
        "--country", nargs="*", default=None,
        help="Extract specific countries (default: all). E.g. --country USA China",
    )
    parser.add_argument(
        "--discover", action="store_true",
        help="Discovery mode: print data inventory",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s  %(message)s",
    )

    extractor = IEAEVExtractor(file_path=args.file)

    if args.discover:
        extractor.discover()
        return

    # Extract
    if args.country:
        # Extract specific countries only
        all_data = {}
        for c in args.country:
            data = extractor.extract_country(c)
            if data:
                all_data[c] = data
                logger.info(f"Extracted {c}: {sum(len(m) for p in data.values() for m in p.values())} series")
    else:
        # Extract all
        all_data = extractor.extract_all()

    # Save
    country_list = args.country if args.country else ["USA"]  # Always save US separately
    json_files = extractor.save_json(args.output_dir, all_data, countries=country_list)
    csv_path = extractor.save_csv(args.output_dir, all_data)

    # Print summary
    actual_countries = [c for c in all_data.keys() if c not in REGIONAL_AGGREGATES]
    regions = [c for c in all_data.keys() if c in REGIONAL_AGGREGATES]

    print(f"\n{'='*70}")
    print(f"IEA Global EV Data Explorer — Extraction Complete")
    print(f"{'='*70}")
    print(f"Countries extracted: {len(actual_countries)}")
    print(f"Regional aggregates: {len(regions)}")

    total_series = sum(
        len(m)
        for country_data in all_data.values()
        for p in country_data.values()
        for m in p.values()
    )
    print(f"Total data series: {total_series}")

    # US highlight
    if "USA" in all_data:
        us = all_data["USA"]
        print(f"\n--- USA Highlights ---")
        # EV sales BEV Cars
        if "ev_sales" in us and "cars" in us["ev_sales"]:
            bev = us["ev_sales"]["cars"].get("BEV", {}).get("values", {})
            if bev:
                hist_years = [yr for yr in bev.keys() if yr <= 2024]
                if hist_years:
                    latest_yr = max(hist_years)
                    print(f"  BEV car sales {latest_yr}: {bev[latest_yr]:,.0f}")
                if 2030 in bev:
                    print(f"  BEV car sales 2030 (STEPS projection): {bev[2030]:,.0f}")
        # EV stock
        if "ev_stock" in us and "cars" in us["ev_stock"]:
            stock = us["ev_stock"]["cars"].get("BEV", {}).get("values", {})
            if stock:
                hist_years = [yr for yr in stock.keys() if yr <= 2024]
                if hist_years:
                    latest_yr = max(hist_years)
                    print(f"  BEV car stock {latest_yr}: {stock[latest_yr]:,.0f}")
        # Sales share
        if "ev_sales_share" in us and "cars" in us["ev_sales_share"]:
            share = us["ev_sales_share"]["cars"].get("EV_total", {}).get("values", {})
            if share:
                hist_years = [yr for yr in share.keys() if yr <= 2024]
                if hist_years:
                    latest_yr = max(hist_years)
                    print(f"  EV sales share {latest_yr}: {share[latest_yr]:.1f}%")

    print(f"\nOutput files:")
    for fp in json_files:
        print(f"  {fp}")
    print(f"  {csv_path}")


if __name__ == "__main__":
    main()
