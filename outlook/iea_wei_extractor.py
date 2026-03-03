#!/usr/bin/env python3
"""
IEA World Energy Investment (WEI) Extractor
============================================

Extracts global energy investment data from IEA WEI annual data files
(2022-2025 editions) and produces a unified, dollar-year-normalized dataset.

Data source:
    IEA World Energy Investment reports (published annually).
    Each edition covers ~10 years of historical data across 10 world regions
    with 25-35 investment categories.

Key features:
    - Parses all 4 editions (2022-2025) with edition-adaptive row detection
    - Normalizes all values to a common dollar year (default: 2024 USD)
    - Combines overlapping years using most-recent-edition preference
    - Outputs per-edition JSON + combined JSON + flat CSV

Regions (no country-level data available):
    World, Advanced Economies, EMDE, China, North America,
    Central and South America, Europe, Africa, Middle East,
    Eurasia, Asia Pacific

Usage:
    python3 iea_wei_extractor.py --data-dir data/ --discover
    python3 iea_wei_extractor.py --data-dir data/ --editions 2025
    python3 iea_wei_extractor.py --data-dir data/
    python3 iea_wei_extractor.py --data-dir data/ --region north_america
    python3 iea_wei_extractor.py --data-dir data/ --verbose
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import openpyxl

logger = logging.getLogger("iea_wei_extractor")

# =============================================================================
# Edition configuration
# =============================================================================

EDITION_FILES = {
    "2022": "WorldEnergyInvestment2022_DataFile.xlsx",
    "2023": "WorldEnergyInvestment2023_DataFile.xlsx",
    "2024": "WorldEnergyInvestment2024_DataFile.xlsx",
    "2025": "WorldEnergyInvestment2025_DataFile.xlsx",
}

EDITION_DOLLAR_YEAR = {
    "2022": 2021,
    "2023": 2022,
    "2024": 2023,
    "2025": 2024,
}

# US GDP implicit price deflator (BEA) — deflate to 2024 USD
DEFLATORS_TO_2024 = {
    2021: 1.131,
    2022: 1.077,
    2023: 1.029,
    2024: 1.000,
}

# Sheets to skip (non-data)
SKIP_SHEETS = {"Cover", "Notes_Web", "Notes"}

# Region canonical key mapping
REGION_KEY_MAP = {
    "World": "world",
    "Advanced economies": "advanced_economies",
    "Advanced Economies": "advanced_economies",
    "EMDE": "emde",
    "China": "china",
    "North America": "north_america",
    "Central and South America": "central_south_america",
    "Europe": "europe",
    "Africa": "africa",
    "Middle East": "middle_east",
    "Eurasia": "eurasia",
    "Asia Pacific": "asia_pacific",
}

# =============================================================================
# Category label → canonical key mapping
# =============================================================================
# Context-aware: same label (e.g. "Coal", "Renewables") maps differently
# depending on which block (Fuels, Power/Generation, End-use) we're in.

# Block markers — when we see these labels, we switch context
BLOCK_MARKERS = {
    "fuels": {"Fuels", "Supply (by type)"},
    "power": {"Power"},
    "generation": {"Generation"},
    "end_use": {"End-use", "End-use "},
    "efficiency": {"Energy efficiency"},
    "other_end_use": {"Other end-use", "Other end-use "},
    "memo": {"Memo: Oil & gas upstream"},
}

# Label → canonical key mapping, context-dependent
# Format: {(label_lower_stripped, block_context): canonical_key}
# block_context = None means context-independent
LABEL_MAP = {
    # Top-level (before any block)
    ("total", None): "total",
    ("of which: clean energy", None): "clean_energy",
    # Supply decomposition (2022-2023 only — Supply/End-use is the first decomposition)
    ("supply (by type)", None): "supply_by_type",
    ("supply (by type)", "supply"): "supply_by_type",
    ("fossil fuels without ccus", "supply"): "supply_fossil_no_ccus",
    ("renewables", "supply"): "supply_renewables",
    ("electricity networks", "supply"): "supply_electricity_networks",
    ("other supply", "supply"): "supply_other",
    # Fuels block
    ("fuels", None): "fuels",
    ("fossil fuels", "fuels"): "fossil_fuels",
    ("oil", "fuels"): "fossil_oil",
    ("gas", "fuels"): "fossil_gas",
    ("coal", "fuels"): "fossil_coal",
    ("clean fuels", "fuels"): "clean_fuels",
    ("direct air capture", "fuels"): "direct_air_capture",
    # Power block
    ("power", None): "power",
    ("generation", "power"): "generation",
    ("coal", "generation"): "gen_coal_unabated",
    ("coal (unabated)", "generation"): "gen_coal_unabated",
    ("oil and natural gas", "generation"): "gen_oil_gas_unabated",
    ("oil and natural gas (unabated)", "generation"): "gen_oil_gas_unabated",
    ("waste: non-renewable", "generation"): "gen_waste_nonrenewable",
    ("nuclear", "generation"): "gen_nuclear",
    ("renewables", "generation"): "gen_renewables",
    ("o/w solar", "generation"): "gen_renewables_solar",
    ("o/w wind", "generation"): "gen_renewables_wind",
    ("o/w others", "generation"): "gen_renewables_other",
    ("fossil fuels: with ccus", "generation"): "gen_fossil_ccus",
    ("other clean power", "generation"): "gen_other_clean",
    ("o/w hydrogen", "generation"): "gen_other_clean_hydrogen",
    ("o/w ammonia", "generation"): "gen_other_clean_ammonia",
    ("o/w large scale heat pumps", "generation"): "gen_other_clean_heat_pumps",
    ("storage", "power"): "battery_storage",
    ("storage", "generation"): "battery_storage",
    ("battery storage", "power"): "battery_storage",
    ("battery storage", "generation"): "battery_storage",
    ("electricity networks", "power"): "electricity_networks",
    # End-use block
    ("end-use", None): "end_use",
    ("energy efficiency", "end_use"): "energy_efficiency",
    ("buildings", "efficiency"): "efficiency_buildings",
    ("transport", "efficiency"): "efficiency_transport",
    ("industry", "efficiency"): "efficiency_industry",
    ("other end-use", "end_use"): "other_end_use",
    ("other end-use", "efficiency"): "other_end_use",
    ("other end-use renewables", "end_use"): "other_end_use_renewables",
    ("other end-use renewables", "efficiency"): "other_end_use_renewables",
    ("other end-use renewables", "other_end_use"): "other_end_use_renewables",
    ("buildings", "other_end_use"): "other_end_use_buildings",
    ("transport", "other_end_use"): "other_end_use_transport",
    ("industry", "other_end_use"): "other_end_use_industry",
    # Memo items
    ("memo: oil & gas upstream", None): "memo_oil_gas_upstream",
    ("memo: transitional fossil fuels", None): "memo_transitional_fossil",
}

# Category metadata for output
CATEGORY_META = {
    "total": {"level": 0, "parent": None, "display": "Total"},
    "clean_energy": {"level": 1, "parent": "total", "display": "of which: Clean energy"},
    "supply_by_type": {"level": 1, "parent": None, "display": "Supply (by type)"},
    "supply_fossil_no_ccus": {"level": 2, "parent": "supply_by_type", "display": "Supply: Fossil fuels (no CCUS)"},
    "supply_renewables": {"level": 1, "parent": None, "display": "Supply: Renewables"},
    "supply_electricity_networks": {"level": 1, "parent": None, "display": "Supply: Electricity networks"},
    "supply_other": {"level": 1, "parent": None, "display": "Supply: Other"},
    "fuels": {"level": 1, "parent": None, "display": "Fuels"},
    "fossil_fuels": {"level": 2, "parent": "fuels", "display": "Fossil fuels"},
    "fossil_oil": {"level": 3, "parent": "fossil_fuels", "display": "Oil"},
    "fossil_gas": {"level": 3, "parent": "fossil_fuels", "display": "Gas"},
    "fossil_coal": {"level": 3, "parent": "fossil_fuels", "display": "Coal"},
    "clean_fuels": {"level": 2, "parent": "fuels", "display": "Clean Fuels"},
    "direct_air_capture": {"level": 2, "parent": "fuels", "display": "Direct Air Capture"},
    "power": {"level": 1, "parent": None, "display": "Power"},
    "generation": {"level": 2, "parent": "power", "display": "Generation"},
    "gen_coal_unabated": {"level": 3, "parent": "generation", "display": "Coal (unabated)"},
    "gen_oil_gas_unabated": {"level": 3, "parent": "generation", "display": "Oil & natural gas (unabated)"},
    "gen_waste_nonrenewable": {"level": 3, "parent": "generation", "display": "Waste: non-renewable"},
    "gen_nuclear": {"level": 3, "parent": "generation", "display": "Nuclear"},
    "gen_renewables": {"level": 3, "parent": "generation", "display": "Renewables"},
    "gen_renewables_solar": {"level": 4, "parent": "gen_renewables", "display": "Solar"},
    "gen_renewables_wind": {"level": 4, "parent": "gen_renewables", "display": "Wind"},
    "gen_renewables_other": {"level": 4, "parent": "gen_renewables", "display": "Other renewables"},
    "gen_fossil_ccus": {"level": 3, "parent": "generation", "display": "Fossil fuels with CCUS"},
    "gen_other_clean": {"level": 3, "parent": "generation", "display": "Other clean power"},
    "gen_other_clean_hydrogen": {"level": 4, "parent": "gen_other_clean", "display": "Hydrogen"},
    "gen_other_clean_ammonia": {"level": 4, "parent": "gen_other_clean", "display": "Ammonia"},
    "gen_other_clean_heat_pumps": {"level": 4, "parent": "gen_other_clean", "display": "Large scale heat pumps"},
    "battery_storage": {"level": 2, "parent": "power", "display": "Battery storage"},
    "electricity_networks": {"level": 2, "parent": "power", "display": "Electricity networks"},
    "end_use": {"level": 1, "parent": None, "display": "End-use"},
    "energy_efficiency": {"level": 2, "parent": "end_use", "display": "Energy efficiency"},
    "efficiency_buildings": {"level": 3, "parent": "energy_efficiency", "display": "Efficiency: Buildings"},
    "efficiency_transport": {"level": 3, "parent": "energy_efficiency", "display": "Efficiency: Transport"},
    "efficiency_industry": {"level": 3, "parent": "energy_efficiency", "display": "Efficiency: Industry"},
    "other_end_use": {"level": 2, "parent": "end_use", "display": "Other end-use"},
    "other_end_use_renewables": {"level": 2, "parent": "end_use", "display": "Other end-use renewables"},
    "other_end_use_buildings": {"level": 3, "parent": "other_end_use", "display": "Other end-use: Buildings"},
    "other_end_use_transport": {"level": 3, "parent": "other_end_use", "display": "Other end-use: Transport"},
    "other_end_use_industry": {"level": 3, "parent": "other_end_use", "display": "Other end-use: Industry"},
    "memo_oil_gas_upstream": {"level": 0, "parent": None, "display": "Memo: Oil & gas upstream"},
    "memo_transitional_fossil": {"level": 0, "parent": None, "display": "Memo: Transitional fossil fuels"},
}


# =============================================================================
# Extractor class
# =============================================================================

class IEAWEIExtractor:
    """Extract and normalize IEA World Energy Investment data."""

    def __init__(
        self,
        data_dir: str,
        editions: Optional[list[str]] = None,
        target_dollar_year: int = 2024,
    ):
        self.data_dir = Path(data_dir)
        self.editions = editions or sorted(EDITION_FILES.keys())
        self.target_dollar_year = target_dollar_year
        self._validate_files()

    def _validate_files(self):
        """Check that requested edition files exist."""
        for ed in self.editions:
            fpath = self.data_dir / EDITION_FILES[ed]
            if not fpath.exists():
                logger.warning(f"Edition {ed} file not found: {fpath}")
                self.editions = [e for e in self.editions if e != ed]

    # -----------------------------------------------------------------
    # Discovery
    # -----------------------------------------------------------------

    def discover(self, edition: str = "2025"):
        """Print inventory of sheets and rows for one edition."""
        fpath = self.data_dir / EDITION_FILES[edition]
        wb = openpyxl.load_workbook(str(fpath), read_only=True, data_only=True)

        print(f"\n{'='*70}")
        print(f"IEA World Energy Investment {edition}")
        print(f"File: {fpath.name}")
        print(f"Dollar year: {EDITION_DOLLAR_YEAR[edition]}")
        print(f"Deflator to 2024$: {DEFLATORS_TO_2024[EDITION_DOLLAR_YEAR[edition]]}")
        print(f"{'='*70}")

        print(f"\nSheets ({len(wb.sheetnames)}):")
        for name in wb.sheetnames:
            skip = "(skip)" if name in SKIP_SHEETS else ""
            print(f"  {name} {skip}")

        # Show World sheet structure
        ws = wb["World"]
        years, data_start_row = self._find_header(ws)
        print(f"\nYear columns: {years}")
        print(f"Data starts at row: {data_start_row}")

        print(f"\nCategories (World sheet):")
        block = None
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < data_start_row:
                continue
            label = row[1] if len(row) > 1 else None
            if not label or not isinstance(label, str):
                continue
            label_clean = label.strip()
            if label_clean.startswith("Note:"):
                continue
            canonical = self._resolve_label(label_clean, block)
            block = self._update_block(label_clean, block)
            vals = [row[j] for j in range(2, 2 + len(years)) if j < len(row)]
            last_val = vals[-1] if vals and vals[-1] is not None else "?"
            print(f"  {canonical or '???':40s} ← {label_clean:45s} last={last_val}")

        wb.close()

    # -----------------------------------------------------------------
    # Parsing helpers
    # -----------------------------------------------------------------

    def _find_header(self, ws) -> tuple[list[int], int]:
        """Find the year-header row and return (years_list, data_start_row)."""
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            # Look for a row that has integer years (2015+) starting in column C
            vals = [c for c in row[2:] if c is not None]
            if vals and all(isinstance(v, (int, float)) and 2010 <= v <= 2030 for v in vals[:3]):
                years = [int(v) for v in vals if isinstance(v, (int, float)) and 2010 <= v <= 2030]
                return years, i + 1
        # Fallback: try row 3 (2024 and earlier editions)
        return list(range(2015, 2026)), 4

    def _update_block(self, label: str, current_block: str) -> str:
        """Update the current parsing block based on label."""
        label_lower = label.lower().strip()
        if label_lower == "supply (by type)":
            return "supply"
        if label_lower == "fuels":
            return "fuels"
        if label_lower == "power":
            return "power"
        if label_lower == "generation":
            return "generation"
        if label_lower.startswith("end-use"):
            return "end_use"
        if label_lower == "energy efficiency":
            return "efficiency"
        if label_lower.startswith("other end-use") and label_lower != "other end-use renewables":
            return "other_end_use"
        if label_lower.startswith("memo:"):
            return "memo"
        # Storage/Networks after generation → back to power
        if current_block == "generation" and label_lower in (
            "storage", "battery storage", "electricity networks"
        ):
            return "power"
        return current_block

    def _resolve_label(self, label: str, block: str) -> Optional[str]:
        """Resolve a row label to a canonical category key using context."""
        label_lower = label.lower().strip()
        # Remove leading "Total" prefix with dollar year info
        if label_lower.startswith("total"):
            label_lower = "total"

        # Try context-specific match first
        key = LABEL_MAP.get((label_lower, block))
        if key and not key.startswith("_block"):
            return key
        # Try context-independent match
        key = LABEL_MAP.get((label_lower, None))
        if key and not key.startswith("_block"):
            return key
        return None

    # -----------------------------------------------------------------
    # Extraction
    # -----------------------------------------------------------------

    def extract_edition(self, edition: str) -> dict[str, dict[str, dict[str, float]]]:
        """
        Extract one edition's data.

        Returns:
            {region_key: {category_key: {year_str: value_in_original_dollars}}}
        """
        fpath = self.data_dir / EDITION_FILES[edition]
        wb = openpyxl.load_workbook(str(fpath), read_only=True, data_only=True)

        result = {}
        for sheet_name in wb.sheetnames:
            if sheet_name in SKIP_SHEETS:
                continue
            region_key = REGION_KEY_MAP.get(sheet_name)
            if not region_key:
                logger.debug(f"  Skipping unrecognized sheet: {sheet_name}")
                continue

            ws = wb[sheet_name]
            years, data_start = self._find_header(ws)
            region_data = {}
            block = None

            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i < data_start:
                    continue
                label = row[1] if len(row) > 1 else None
                if not label or not isinstance(label, str):
                    continue
                label_clean = label.strip()
                if label_clean.startswith("Note:"):
                    continue

                canonical = self._resolve_label(label_clean, block)
                block = self._update_block(label_clean, block)

                if not canonical:
                    logger.debug(f"  [{edition}] {region_key}: unmatched label '{label_clean}' (block={block})")
                    continue

                # Extract year values
                year_data = {}
                for j, yr in enumerate(years):
                    col_idx = 2 + j
                    if col_idx < len(row) and row[col_idx] is not None:
                        try:
                            year_data[str(yr)] = float(row[col_idx])
                        except (ValueError, TypeError):
                            pass

                if year_data:
                    region_data[canonical] = year_data

            result[region_key] = region_data
            logger.info(f"  [{edition}] {region_key}: extracted {len(region_data)} categories")

        wb.close()
        return result

    def extract_all(self) -> dict:
        """
        Extract all editions and combine into a unified dataset.

        Returns combined dict with dollar-year-normalized values.
        Most recent edition preferred for overlapping years.
        """
        all_editions = {}
        for ed in self.editions:
            logger.info(f"Extracting WEI {ed}...")
            all_editions[ed] = self.extract_edition(ed)

        # Combine: iterate editions from oldest to newest
        # so newer editions overwrite older for overlapping years
        combined = {}
        source_map = {}  # {region: {category: {year: edition}}}

        for ed in sorted(self.editions):
            deflator = DEFLATORS_TO_2024[EDITION_DOLLAR_YEAR[ed]]
            ed_data = all_editions[ed]

            for region, categories in ed_data.items():
                if region not in combined:
                    combined[region] = {}
                    source_map[region] = {}

                for cat, year_vals in categories.items():
                    if cat not in combined[region]:
                        combined[region][cat] = {}
                        source_map[region][cat] = {}

                    for yr_str, val in year_vals.items():
                        normalized = round(val * deflator, 2)
                        combined[region][cat][yr_str] = normalized
                        source_map[region][cat][yr_str] = ed

        # Sort years within each category
        for region in combined:
            for cat in combined[region]:
                combined[region][cat] = dict(
                    sorted(combined[region][cat].items(), key=lambda x: int(x[0]))
                )

        return combined, all_editions, source_map

    # -----------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------

    def save_json(self, output_dir: str, combined: dict, all_editions: dict, source_map: dict):
        """Save per-edition raw + combined normalized JSON files."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Per-edition raw files
        for ed, ed_data in all_editions.items():
            dollar_yr = EDITION_DOLLAR_YEAR[ed]
            payload = {
                "source": "IEA World Energy Investment",
                "edition": ed,
                "dollar_year": dollar_yr,
                "units": f"Billion USD ({dollar_yr}, MER)",
                "extraction_metadata": {
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "file": EDITION_FILES[ed],
                },
                "regions": ed_data,
            }
            fpath = out / f"iea_wei_{ed}_raw.json"
            with open(fpath, "w") as f:
                json.dump(payload, f, indent=2)
            logger.info(f"Saved: {fpath}")

        # Combined normalized file
        # Build category index
        all_categories = set()
        for region_data in combined.values():
            all_categories.update(region_data.keys())

        category_index = {}
        for cat in sorted(all_categories):
            meta = CATEGORY_META.get(cat, {"level": 0, "parent": None, "display": cat})
            category_index[cat] = meta

        payload = {
            "source": "IEA World Energy Investment",
            "editions_used": self.editions,
            "dollar_year": self.target_dollar_year,
            "units": f"Billion USD ({self.target_dollar_year}, MER)",
            "combination_rule": "most_recent_edition_preferred",
            "extraction_metadata": {
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "deflators_applied": {
                    str(k): v for k, v in DEFLATORS_TO_2024.items()
                    if str(k) != str(self.target_dollar_year)
                },
            },
            "category_index": category_index,
            "regions": combined,
        }
        fpath = out / f"iea_wei_combined_{self.target_dollar_year}usd.json"
        with open(fpath, "w") as f:
            json.dump(payload, f, indent=2)
        logger.info(f"Saved: {fpath} ({len(combined)} regions)")

        return fpath

    def save_csv(self, output_dir: str, combined: dict) -> Path:
        """Save flat CSV with region × category × years."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Collect all years
        all_years = set()
        for region_data in combined.values():
            for cat_data in region_data.values():
                all_years.update(cat_data.keys())
        all_years = sorted(all_years, key=int)

        fpath = out / f"iea_wei_combined_{self.target_dollar_year}usd.csv"
        with open(fpath, "w") as f:
            # Header
            header = ["region", "category", "level", "parent", "display_name"] + all_years
            f.write(",".join(header) + "\n")

            # Data rows
            for region in sorted(combined.keys()):
                for cat in sorted(combined[region].keys()):
                    meta = CATEGORY_META.get(cat, {"level": 0, "parent": None, "display": cat})
                    row = [
                        region,
                        cat,
                        str(meta["level"]),
                        meta["parent"] or "",
                        meta["display"],
                    ]
                    for yr in all_years:
                        val = combined[region].get(cat, {}).get(yr)
                        row.append(f"{val:.2f}" if val is not None else "")
                    f.write(",".join(row) + "\n")

        logger.info(f"Saved: {fpath}")
        return fpath


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="IEA World Energy Investment Extractor"
    )
    parser.add_argument(
        "--data-dir", default="data/",
        help="Directory containing WEI Excel files (default: data/)",
    )
    parser.add_argument(
        "--output-dir", default="output/",
        help="Output directory (default: output/)",
    )
    parser.add_argument(
        "--editions", nargs="*", default=None,
        help="Editions to process (default: all available). E.g. --editions 2024 2025",
    )
    parser.add_argument(
        "--region", default=None,
        help="Filter output to specific region key (e.g. north_america, world)",
    )
    parser.add_argument(
        "--discover", action="store_true",
        help="Discovery mode: print structure inventory without extracting",
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

    extractor = IEAWEIExtractor(
        data_dir=args.data_dir,
        editions=args.editions,
    )

    if args.discover:
        ed = args.editions[0] if args.editions else "2025"
        extractor.discover(ed)
        return

    # Full extraction
    combined, all_editions, source_map = extractor.extract_all()

    # Filter by region if requested
    if args.region:
        combined = {k: v for k, v in combined.items() if k == args.region}
        if not combined:
            logger.error(f"Region '{args.region}' not found. Available: {list(source_map.keys())}")
            sys.exit(1)

    # Save outputs
    json_path = extractor.save_json(args.output_dir, combined, all_editions, source_map)
    csv_path = extractor.save_csv(args.output_dir, combined)

    # Print summary
    print(f"\n{'='*70}")
    print(f"IEA World Energy Investment — Extraction Complete")
    print(f"{'='*70}")
    print(f"Editions processed: {extractor.editions}")
    print(f"Dollar year: {extractor.target_dollar_year} USD")
    print(f"Regions: {len(combined)}")

    total_categories = sum(len(cats) for cats in combined.values())
    print(f"Total data points: {total_categories} (region × category)")

    # Print World summary table
    if "world" in combined:
        world = combined["world"]
        years = sorted(world.get("total", {}).keys(), key=int)
        if years:
            print(f"\nWorld Investment Summary (Billion {extractor.target_dollar_year} USD):")
            print(f"  {'Category':40s} {years[0]:>8s}  {years[-1]:>8s}  Change")
            print(f"  {'-'*40} {'-'*8}  {'-'*8}  {'-'*8}")
            for cat in ["total", "clean_energy", "fossil_fuels", "gen_renewables",
                        "battery_storage", "gen_fossil_ccus", "electricity_networks"]:
                if cat in world:
                    first = world[cat].get(years[0])
                    last = world[cat].get(years[-1])
                    if first and last:
                        change = (last - first) / first * 100
                        display = CATEGORY_META.get(cat, {}).get("display", cat)
                        print(f"  {display:40s} {first:>8.1f}  {last:>8.1f}  {change:>+7.1f}%")

    print(f"\nOutput files:")
    print(f"  {json_path}")
    print(f"  {csv_path}")
    for ed in extractor.editions:
        print(f"  {Path(args.output_dir) / f'iea_wei_{ed}_raw.json'}")


if __name__ == "__main__":
    main()
