"""
price_databases.py

Lookup functions for location-dependent cost databases:
  - Industrial electricity tariffs (industrial_tariffs.json)
  - PPA prices (ppa_prices.json)
  - Water costs (water_costs.json)
  - Land costs (land_costs.json)
  - Tax rates, depreciation & inflation (tax_rates.json)
  - H2/NH3 selling prices (selling_prices.json)

Also provides a staleness checker that warns when databases are outdated.
"""

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent

STALE_MONTHS = 6  # warn if databases are older than this

# ---------------------------------------------------------------------------
# Database staleness check
# ---------------------------------------------------------------------------

_ALL_DATABASES = [
    ("industrial_tariffs.json", "Industrial tariff"),
    ("ppa_prices.json", "PPA price"),
    ("water_costs.json", "Water cost"),
    ("land_costs.json", "Land cost"),
    ("tax_rates.json", "Tax rate / depreciation / inflation"),
    ("selling_prices.json", "H2/NH3 selling price"),
]


def check_db_staleness():
    """Check if price databases are older than STALE_MONTHS and print warnings."""
    now = datetime.now()
    for fname, label in _ALL_DATABASES:
        path = BASE_DIR / fname
        if not path.exists():
            continue
        try:
            meta = json.loads(path.read_text()).get("_metadata", {})
            last_updated = meta.get("last_updated", "")
            if not last_updated:
                continue
            db_date = datetime.strptime(last_updated, "%Y-%m")
            age_months = (now.year - db_date.year) * 12 + (now.month - db_date.month)
            if age_months >= STALE_MONTHS:
                print(
                    f"  WARNING: {label} database ({fname}) was last updated "
                    f"{last_updated} ({age_months} months ago). "
                    f"Consider updating the prices."
                )
        except (json.JSONDecodeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Industrial electricity tariffs
# ---------------------------------------------------------------------------

def _load_tariff_db() -> dict:
    path = BASE_DIR / "industrial_tariffs.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("countries", {})


def lookup_tariff(country_code: str) -> tuple:
    """
    Look up industrial electricity tariff for a country.
    Returns (price_usd_mwh, country_name, note) or (None, None, None).
    """
    db = _load_tariff_db()
    entry = db.get(country_code)
    if entry:
        return entry["price_usd_mwh"], entry["name"], entry.get("currency_note", "")
    return None, None, None


def list_tariffs() -> pd.DataFrame:
    db = _load_tariff_db()
    rows = []
    for code, entry in db.items():
        rows.append({
            "code": code,
            "country": entry["name"],
            "price_usd_mwh": entry["price_usd_mwh"],
            "year": entry.get("year", ""),
            "note": entry.get("currency_note", ""),
        })
    return pd.DataFrame(rows).sort_values("country").reset_index(drop=True)


# ---------------------------------------------------------------------------
# PPA prices
# ---------------------------------------------------------------------------

def _load_ppa_db() -> dict:
    path = BASE_DIR / "ppa_prices.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("countries", {})


def lookup_ppa(country_code: str, state_code: str = None) -> tuple:
    """
    Look up PPA prices for a country (or US region).
    Returns (solar_ppa, wind_ppa, name, note) or (None, None, None, None).
    """
    db = _load_ppa_db()

    # US: try region-specific first
    if country_code == "US" and state_code:
        rto_map = {
            "CA": "US_CAISO", "TX": "US_ERCOT", "NY": "US_NYISO",
            "MA": "US_ISNE", "CT": "US_ISNE", "NH": "US_ISNE",
            "IL": "US_MISO", "IN": "US_MISO", "MI": "US_MISO",
            "OH": "US_PJM", "PA": "US_PJM", "NJ": "US_PJM",
            "VA": "US_PJM", "MD": "US_PJM",
            "OK": "US_SPP", "KS": "US_SPP",
        }
        rto_key = rto_map.get(state_code.upper())
        if rto_key and rto_key in db:
            e = db[rto_key]
            return e.get("solar_ppa_usd_mwh"), e.get("wind_ppa_usd_mwh"), e["name"], e.get("note", "")
        if "US_NATIONAL" in db:
            e = db["US_NATIONAL"]
            return e.get("solar_ppa_usd_mwh"), e.get("wind_ppa_usd_mwh"), e["name"], e.get("note", "")

    # EU: try EU_<CC> first
    eu_key = f"EU_{country_code}"
    if eu_key in db:
        e = db[eu_key]
        return e.get("solar_ppa_usd_mwh"), e.get("wind_ppa_usd_mwh"), e["name"], e.get("note", "")

    # Direct country lookup
    if country_code in db:
        e = db[country_code]
        return e.get("solar_ppa_usd_mwh"), e.get("wind_ppa_usd_mwh"), e["name"], e.get("note", "")

    return None, None, None, None


def list_ppa_prices() -> pd.DataFrame:
    db = _load_ppa_db()
    rows = []
    for code, entry in db.items():
        rows.append({
            "code": code,
            "country": entry["name"],
            "solar_usd_mwh": entry.get("solar_ppa_usd_mwh"),
            "wind_usd_mwh": entry.get("wind_ppa_usd_mwh"),
            "year": entry.get("year", ""),
            "note": entry.get("note", ""),
        })
    return pd.DataFrame(rows).sort_values("country").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Water costs
# ---------------------------------------------------------------------------

def _load_water_db() -> dict:
    path = BASE_DIR / "water_costs.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("countries", {})


def lookup_water_cost(country_code: str, source: str = "desal") -> tuple:
    """
    Look up water cost for a country.

    Parameters
    ----------
    source : str
        "desal", "municipal", or "cheapest".

    Returns (price_usd_m3, country_name, note) or (None, None, None).
    """
    db = _load_water_db()
    entry = db.get(country_code)
    if not entry:
        return None, None, None

    desal = entry.get("desal_usd_m3")
    municipal = entry.get("municipal_usd_m3")
    name = entry["name"]
    note = entry.get("note", "")

    if source == "desal":
        price = desal
        if price is None:
            price = municipal
            note = f"(no desal data, using municipal) {note}"
    elif source == "municipal":
        price = municipal
        if price is None:
            price = desal
            note = f"(no municipal data, using desal) {note}"
    else:  # cheapest
        available = [p for p in (desal, municipal) if p is not None]
        price = min(available) if available else None

    return price, name, note


def list_water_costs() -> pd.DataFrame:
    db = _load_water_db()
    rows = []
    for code, entry in db.items():
        rows.append({
            "code": code,
            "country": entry["name"],
            "desal_usd_m3": entry.get("desal_usd_m3"),
            "municipal_usd_m3": entry.get("municipal_usd_m3"),
            "note": entry.get("note", ""),
        })
    return pd.DataFrame(rows).sort_values("country").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Land costs
# ---------------------------------------------------------------------------

def _load_land_db() -> dict:
    path = BASE_DIR / "land_costs.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("countries", {})


def lookup_land_cost(country_code: str, state_code: str = None) -> tuple:
    """
    Look up industrial land lease cost for a country or sub-region.

    For US locations, tries US_<state> first (e.g., US_TX), then US national.
    For EU locations, tries EU_<CC>_<region> entries if they exist, else EU country.

    Returns (price_usd_acre_yr, country_name, note) or (None, None, None).
    """
    db = _load_land_db()

    # US: try state-specific first
    if country_code == "US" and state_code:
        state_key = f"US_{state_code.upper()}"
        if state_key in db:
            e = db[state_key]
            return e["land_cost_usd_acre_yr"], e["name"], e.get("note", "")

    # Direct country lookup (works for US fallback, EU national, and all others)
    entry = db.get(country_code)
    if entry:
        return entry["land_cost_usd_acre_yr"], entry["name"], entry.get("note", "")

    return None, None, None


def lookup_land_cost_eu_region(country_code: str, region: str) -> tuple:
    """
    Look up EU sub-regional land cost.

    Example: lookup_land_cost_eu_region("ES", "ANDALUSIA") -> tries EU_ES_ANDALUSIA
    Returns (price_usd_acre_yr, name, note) or falls back to country-level.
    """
    db = _load_land_db()
    region_key = f"EU_{country_code}_{region.upper()}"
    if region_key in db:
        e = db[region_key]
        return e["land_cost_usd_acre_yr"], e["name"], e.get("note", "")
    # Fallback to country
    return lookup_land_cost(country_code)


def list_land_regions(country_code: str = None) -> pd.DataFrame:
    """List all sub-regional land costs. Optionally filter by country prefix."""
    db = _load_land_db()
    rows = []
    for code, entry in db.items():
        if country_code:
            if country_code == "US":
                if not code.startswith("US_"):
                    continue
            else:
                if not code.startswith(f"EU_{country_code}"):
                    continue
        if "_" in code:  # sub-regional entries
            rows.append({
                "code": code,
                "region": entry["name"],
                "usd_acre_yr": entry["land_cost_usd_acre_yr"],
                "note": entry.get("note", ""),
            })
    return pd.DataFrame(rows).sort_values("usd_acre_yr").reset_index(drop=True)


def list_land_costs() -> pd.DataFrame:
    db = _load_land_db()
    rows = []
    for code, entry in db.items():
        rows.append({
            "code": code,
            "country": entry["name"],
            "usd_acre_yr": entry["land_cost_usd_acre_yr"],
            "note": entry.get("note", ""),
        })
    return pd.DataFrame(rows).sort_values("country").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Tax rates, depreciation & inflation
# ---------------------------------------------------------------------------

def _load_tax_db() -> dict:
    path = BASE_DIR / "tax_rates.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text()).get("countries", {})


def lookup_tax(country_code: str, state_code: str = None) -> dict | None:
    """
    Look up tax rates, depreciation, and inflation for a country or US state.

    For US locations, tries US_<state> first, then US national.

    Returns a dict with keys:
        name, corporate_federal_tax, corporate_state_tax, combined_corporate_tax,
        franchise_tax, sales_tax, property_tax_rate, vat_rate,
        depreciation_years, depreciation_method, inflation_rate,
        notes, tax_incentives
    or None if not found.
    """
    db = _load_tax_db()

    # US: try state-specific first
    if country_code == "US" and state_code:
        state_key = f"US_{state_code.upper()}"
        if state_key in db:
            return db[state_key]

    entry = db.get(country_code)
    return entry if entry else None


def lookup_inflation(country_code: str, state_code: str = None) -> tuple:
    """
    Look up inflation rate for a country or US state.
    Returns (inflation_rate, country_name) or (None, None).
    """
    entry = lookup_tax(country_code, state_code)
    if entry:
        return entry.get("inflation_rate"), entry["name"]
    return None, None


def lookup_depreciation(country_code: str, state_code: str = None) -> tuple:
    """
    Look up depreciation period and method.
    Returns (years, method, country_name) or (None, None, None).
    """
    entry = lookup_tax(country_code, state_code)
    if entry:
        return entry.get("depreciation_years"), entry.get("depreciation_method", ""), entry["name"]
    return None, None, None


def lookup_discount_rate(country_code: str, state_code: str = None) -> tuple:
    """
    Look up project discount rate (WACC) for a country or US state.
    Returns (discount_rate, country_name, note) or (None, None, None).
    """
    entry = lookup_tax(country_code, state_code)
    if entry:
        return (
            entry.get("discount_rate"),
            entry["name"],
            entry.get("discount_rate_note", ""),
        )
    return None, None, None


def list_tax_rates() -> pd.DataFrame:
    """List all tax rates as a DataFrame."""
    db = _load_tax_db()
    rows = []
    for code, entry in db.items():
        rows.append({
            "code": code,
            "country": entry["name"],
            "federal_tax": entry.get("corporate_federal_tax"),
            "state_tax": entry.get("corporate_state_tax"),
            "combined_tax": entry.get("combined_corporate_tax"),
            "vat_rate": entry.get("vat_rate"),
            "sales_tax": entry.get("sales_tax"),
            "property_tax": entry.get("property_tax_rate"),
            "franchise_tax": entry.get("franchise_tax"),
            "depreciation_yr": entry.get("depreciation_years"),
            "inflation": entry.get("inflation_rate"),
            "discount_rate": entry.get("discount_rate"),
        })
    return pd.DataFrame(rows).sort_values("country").reset_index(drop=True)


# ---------------------------------------------------------------------------
# H2 / NH3 selling prices
# ---------------------------------------------------------------------------

def _load_selling_db() -> dict:
    path = BASE_DIR / "selling_prices.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def lookup_selling_prices(country_code: str, state_code: str = None,
                          market: str = "export") -> dict | None:
    """
    Look up regional H2 and NH3 selling prices.

    Parameters
    ----------
    country_code : str
        2-letter country code.
    state_code : str, optional
        US state code (maps to US sub-region).
    market : str
        "export" or "domestic" — for Middle East, selects export vs domestic pricing.

    Returns a dict with keys:
        name, green_h2_usd_t, grey_h2_usd_t, green_nh3_usd_t, grey_nh3_usd_t, note
    or None if not found.
    """
    db = _load_selling_db()
    regions = db.get("regions", {})
    c2r = db.get("country_to_region", {})
    us2r = db.get("us_state_to_region", {})

    region_key = None

    # US: try state-specific region first
    if country_code == "US" and state_code:
        region_key = us2r.get(state_code.upper())
    if not region_key and country_code == "US":
        region_key = "US_GULF"  # US default

    # Middle East domestic vs export
    if not region_key and country_code in ("SA", "AE", "OM", "QA"):
        if market == "domestic":
            region_key = "ME_DOMESTIC"
        else:
            region_key = "ME_GULF_EXPORT"

    # China interior vs coastal
    if not region_key and country_code == "CN":
        region_key = "CHINA_EAST"  # default; CHINA_WEST available via direct region

    # General country → region mapping
    if not region_key:
        region_key = c2r.get(country_code)

    # Fallback to GLOBAL
    if not region_key:
        region_key = "GLOBAL"

    entry = regions.get(region_key)
    return entry if entry else regions.get("GLOBAL")


def lookup_selling_prices_by_region(region_key: str) -> dict | None:
    """Direct lookup by region key (e.g., 'US_GULF', 'JAPAN', 'ME_DOMESTIC')."""
    db = _load_selling_db()
    return db.get("regions", {}).get(region_key)


def list_selling_prices() -> pd.DataFrame:
    """List all regional selling prices as a DataFrame."""
    db = _load_selling_db()
    rows = []
    for code, entry in db.get("regions", {}).items():
        rows.append({
            "region": code,
            "name": entry["name"],
            "green_h2_usd_t": entry.get("green_h2_usd_t"),
            "grey_h2_usd_t": entry.get("grey_h2_usd_t"),
            "green_nh3_usd_t": entry.get("green_nh3_usd_t"),
            "grey_nh3_usd_t": entry.get("grey_nh3_usd_t"),
            "note": entry.get("note", ""),
        })
    return pd.DataFrame(rows)
