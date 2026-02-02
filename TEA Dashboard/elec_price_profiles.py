"""
elec_price_profiles.py

Generate 30-year hourly electricity price profiles (elec_prices_30y.csv)
for the SHARE model.

Three modes:
  1. EU:   Fetch 1 year of hourly day-ahead prices from ENTSO-E, repeat + escalate for 30 years
  2. US:   Fetch 1 year of hourly wholesale prices from EIA, repeat + escalate for 30 years
  3. Flat: User-specified tariff (USD/MWh) with annual escalation for 30 years

Tokens/keys:
  - ENTSO-E: env var ENTSOE_TOKEN or entsoe_token.txt
  - EIA:     env var EIA_API_KEY or eia_key.txt
"""

import json
import os
import ssl
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import certifi
import numpy as np
import pandas as pd
import requests

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"

# Price database lookups (tariffs, PPA, water, land, tax) live in price_databases.py
from price_databases import (
    check_db_staleness,
    lookup_tariff, list_tariffs,
    lookup_ppa, list_ppa_prices,
    lookup_water_cost, list_water_costs,
    lookup_land_cost, list_land_costs,
    lookup_tax, lookup_inflation, lookup_depreciation, list_tax_rates,
    lookup_selling_prices, list_selling_prices,
)

# ---------------------------------------------------------------------------
# ENTSO-E bidding zone codes
# ---------------------------------------------------------------------------
ENTSOE_ZONES = {
    # country code -> (EIC code, display name)
    "DE": ("10Y1001A1001A82H", "Germany/Luxembourg"),
    "FR": ("10YFR-RTE------C", "France"),
    "ES": ("10YES-REE------0", "Spain"),
    "IT": ("10Y1001A1001A73I", "Italy (North)"),
    "NL": ("10YNL----------L", "Netherlands"),
    "BE": ("10YBE----------2", "Belgium"),
    "AT": ("10YAT-APG------L", "Austria"),
    "PL": ("10YPL-AREA-----S", "Poland"),
    "PT": ("10YPT-REN------W", "Portugal"),
    "GR": ("10YGR-HTSO-----Y", "Greece"),
    "DK": ("10Y1001A1001A65H", "Denmark (DK1)"),
    "SE": ("10Y1001A1001A44P", "Sweden (SE1)"),
    "NO": ("10YNO-1--------2", "Norway (NO1)"),
    "FI": ("10YFI-1--------U", "Finland"),
    "CZ": ("10YCZ-CEPS-----N", "Czech Republic"),
    "RO": ("10YRO-TEL------P", "Romania"),
    "HU": ("10YHU-MAVIR----U", "Hungary"),
    "BG": ("10YCA-BULGARIA-R", "Bulgaria"),
    "IE": ("10Y1001A1001A59C", "Ireland"),
    "CH": ("10YCH-SWISSGRIDZ", "Switzerland"),
    "GB": ("10YGB----------A", "Great Britain"),
}

# Countries that are in the ENTSO-E area (for auto-detection)
EU_COUNTRIES = set(ENTSOE_ZONES.keys())

# ---------------------------------------------------------------------------
# EIA RTO codes
# ---------------------------------------------------------------------------
EIA_RTOS = {
    # state code -> (RTO code, display name)
    "CA": ("CISO", "California ISO"),
    "TX": ("ERCO", "ERCOT (Texas)"),
    "NY": ("NYIS", "New York ISO"),
    "IL": ("MISO", "MISO"),
    "IN": ("MISO", "MISO"),
    "MI": ("MISO", "MISO"),
    "MN": ("MISO", "MISO"),
    "WI": ("MISO", "MISO"),
    "OH": ("PJM", "PJM"),
    "PA": ("PJM", "PJM"),
    "NJ": ("PJM", "PJM"),
    "VA": ("PJM", "PJM"),
    "MD": ("PJM", "PJM"),
    "DC": ("PJM", "PJM"),
    "MA": ("ISNE", "ISO New England"),
    "CT": ("ISNE", "ISO New England"),
    "NH": ("ISNE", "ISO New England"),
    "VT": ("ISNE", "ISO New England"),
    "ME": ("ISNE", "ISO New England"),
    "RI": ("ISNE", "ISO New England"),
    "WA": ("BPAT", "Bonneville Power"),
    "OR": ("BPAT", "Bonneville Power"),
    "CO": ("PSCO", "Public Service Colorado"),
    "AZ": ("SRP", "Salt River Project"),
    "FL": ("FPC", "Duke Energy Florida"),
}


# ---------------------------------------------------------------------------
# Token/key loading
# ---------------------------------------------------------------------------

def _load_key(name: str, env_var: str, filename: str) -> str:
    """Load an API key from env var or file."""
    val = os.environ.get(env_var, "").strip()
    if val:
        return val
    path = BASE_DIR / filename
    if path.exists():
        text = path.read_text().strip()
        if text:
            return text
    raise RuntimeError(
        f"No {name} found.\n"
        f"Provide via {env_var} env var or {filename} in project directory."
    )


def load_entsoe_token() -> str:
    return _load_key("ENTSO-E token", "ENTSOE_TOKEN", "entsoe_token.txt")


def load_eia_key() -> str:
    # Check both possible filenames
    for fname in ("eia_key.txt", "eia_token.txt"):
        path = BASE_DIR / fname
        if path.exists():
            text = path.read_text().strip()
            if text:
                return text
    val = os.environ.get("EIA_API_KEY", "").strip()
    if val:
        return val
    raise RuntimeError(
        "No EIA API key found.\n"
        "Provide via EIA_API_KEY env var or eia_key.txt / eia_token.txt in project directory."
    )


# ---------------------------------------------------------------------------
# Caching (shared with location_profiles.py)
# ---------------------------------------------------------------------------

def _cache_path(prefix: str, params: dict) -> Path:
    import hashlib
    raw = f"{prefix}|{json.dumps(params, sort_keys=True)}"
    h = hashlib.md5(raw.encode()).hexdigest()
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"{prefix}_{h}.json"


def _get_cached(prefix: str, params: dict):
    p = _cache_path(prefix, params)
    if p.exists():
        return json.loads(p.read_text())
    return None


def _set_cache(prefix: str, params: dict, data):
    p = _cache_path(prefix, params)
    p.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# Country detection from geocoded address
# ---------------------------------------------------------------------------

def detect_country_code(address: str, lat: float, lon: float) -> str:
    """
    Try to extract a 2-letter country code from a geocoded address.
    Falls back to reverse geocoding if needed.
    """
    # Try reverse geocoding to get country code
    try:
        from geopy.geocoders import Nominatim
        ctx = ssl.create_default_context(cafile=certifi.where())
        geolocator = Nominatim(user_agent="share_model_tea", ssl_context=ctx)
        loc = geolocator.reverse(f"{lat}, {lon}", language="en")
        if loc and loc.raw.get("address", {}).get("country_code"):
            return loc.raw["address"]["country_code"].upper()
    except Exception:
        pass

    # Fallback: check if address contains known country names
    addr_lower = address.lower()
    country_map = {
        "united states": "US", "usa": "US", "america": "US",
        "germany": "DE", "deutschland": "DE",
        "france": "FR", "spain": "ES", "españa": "ES",
        "italy": "IT", "italia": "IT",
        "netherlands": "NL", "belgium": "BE",
        "austria": "AT", "poland": "PL",
        "portugal": "PT", "greece": "GR",
        "denmark": "DK", "sweden": "SE",
        "norway": "NO", "finland": "FI",
        "czech": "CZ", "romania": "RO",
        "hungary": "HU", "bulgaria": "BG",
        "ireland": "IE", "switzerland": "CH",
        "united kingdom": "GB", "great britain": "GB",
        "oman": "OM", "عمان": "OM",
        "saudi": "SA", "السعودية": "SA",
        "chile": "CL", "australia": "AU",
        "morocco": "MA", "egypt": "EG",
        "brazil": "BR", "argentina": "AR",
        "india": "IN_COUNTRY", "china": "CN",
        "japan": "JP", "south korea": "KR",
        "south africa": "ZA", "namibia": "NA_COUNTRY",
    }
    for name, code in country_map.items():
        if name in addr_lower:
            return code
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Mode 1: ENTSO-E (EU hourly day-ahead prices)
# ---------------------------------------------------------------------------

def fetch_entsoe_prices(
    country_code: str,
    year: int,
    token: str = None,
) -> pd.Series:
    """
    Fetch 1 year of hourly day-ahead prices from ENTSO-E.
    Returns pd.Series of length 8760 in EUR/MWh.
    """
    if token is None:
        token = load_entsoe_token()

    if country_code not in ENTSOE_ZONES:
        raise ValueError(
            f"No ENTSO-E bidding zone for '{country_code}'. "
            f"Available: {', '.join(sorted(ENTSOE_ZONES.keys()))}"
        )

    eic_code, zone_name = ENTSOE_ZONES[country_code]
    print(f"  ENTSO-E zone: {zone_name} ({eic_code})")

    cache_params = {"zone": eic_code, "year": year}
    cached = _get_cached("entsoe", cache_params)
    if cached is not None:
        print(f"  [cache hit] ENTSO-E {zone_name} {year}")
        return pd.Series(cached, name="price_eur_mwh")

    # ENTSO-E expects YYYYMMDD0000 format
    start = f"{year}01010000"
    end = f"{year + 1}01010000"

    url = "https://web-api.tp.entsoe.eu/api"
    params = {
        "securityToken": token,
        "documentType": "A44",
        "in_Domain": eic_code,
        "out_Domain": eic_code,
        "periodStart": start,
        "periodEnd": end,
    }

    print(f"  [fetching] ENTSO-E day-ahead prices for {zone_name} ({year})...")
    resp = requests.get(url, params=params, timeout=120)
    resp.raise_for_status()

    # Parse XML
    ns = {"ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3"}
    root = ET.fromstring(resp.content)

    prices = []
    for ts in root.findall(".//ns:TimeSeries", ns):
        for period in ts.findall("ns:Period", ns):
            resolution = period.find("ns:resolution", ns).text
            # PT60M = hourly, PT15M = 15-min (we want hourly)
            for point in period.findall("ns:Point", ns):
                price = float(point.find("ns:price.amount", ns).text)
                if resolution == "PT60M":
                    prices.append(price)
                elif resolution == "PT15M":
                    # Average 4 x 15-min into 1 hour (handled by collecting all, then resampling)
                    prices.append(price)

    # If 15-min resolution, average to hourly
    if len(prices) > 8760:
        # Likely 15-min data (35040 points) - resample to hourly
        arr = np.array(prices[:35040])
        prices = arr.reshape(-1, 4).mean(axis=1).tolist()

    if len(prices) < 8760:
        # Pad with mean if slightly short (some zones have gaps)
        mean_price = np.mean(prices) if prices else 50.0
        prices.extend([mean_price] * (8760 - len(prices)))

    prices = prices[:8760]
    _set_cache("entsoe", cache_params, prices)

    series = pd.Series(prices, name="price_eur_mwh")
    print(f"  Mean: {series.mean():.1f} EUR/MWh  |  Min: {series.min():.1f}  |  Max: {series.max():.1f}")
    return series


# ---------------------------------------------------------------------------
# Mode 2: EIA (US hourly wholesale prices)
# ---------------------------------------------------------------------------

def fetch_eia_prices(
    state_code: str,
    year: int,
    api_key: str = None,
) -> pd.Series:
    """
    Fetch 1 year of hourly wholesale prices from EIA for a US state/RTO.
    Returns pd.Series of length 8760 in USD/MWh.
    """
    if api_key is None:
        api_key = load_eia_key()

    state_upper = state_code.upper()
    if state_upper not in EIA_RTOS:
        raise ValueError(
            f"No EIA RTO mapping for state '{state_upper}'. "
            f"Available: {', '.join(sorted(set(EIA_RTOS.keys())))}"
        )

    rto_code, rto_name = EIA_RTOS[state_upper]
    print(f"  EIA RTO: {rto_name} ({rto_code})")

    cache_params = {"rto": rto_code, "year": year}
    cached = _get_cached("eia", cache_params)
    if cached is not None:
        print(f"  [cache hit] EIA {rto_name} {year}")
        return pd.Series(cached, name="price_usd_mwh")

    # EIA API v2: fetch hourly data in chunks (max 5000 per request)
    all_prices = {}
    offset = 0
    chunk_size = 5000

    print(f"  [fetching] EIA hourly prices for {rto_name} ({year})...")

    while True:
        params = {
            "frequency": "hourly",
            "data[0]": "value",
            "facets[respondent][]": rto_code,
            "facets[type][]": "D",  # Demand (day-ahead clearing price)
            "start": f"{year}-01",
            "end": f"{year}-12",
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "offset": offset,
            "length": chunk_size,
            "api_key": api_key,
        }

        resp = requests.get(
            "https://api.eia.gov/v2/electricity/rto/region-data/data",
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        rows = data.get("response", {}).get("data", [])
        if not rows:
            break

        for row in rows:
            period = row.get("period", "")
            value = row.get("value")
            if value is not None:
                all_prices[period] = float(value)

        if len(rows) < chunk_size:
            break
        offset += chunk_size

    if not all_prices:
        raise ValueError(
            f"No EIA data returned for {rto_name} ({year}). "
            f"Try a different year or check your API key."
        )

    # Sort by period and extract values
    sorted_prices = [v for _, v in sorted(all_prices.items())]

    # Ensure exactly 8760
    if len(sorted_prices) > 8760:
        sorted_prices = sorted_prices[:8760]
    elif len(sorted_prices) < 8760:
        mean_p = np.mean(sorted_prices) if sorted_prices else 50.0
        sorted_prices.extend([mean_p] * (8760 - len(sorted_prices)))

    _set_cache("eia", cache_params, sorted_prices)

    series = pd.Series(sorted_prices, name="price_usd_mwh")
    print(f"  Mean: {series.mean():.1f} USD/MWh  |  Min: {series.min():.1f}  |  Max: {series.max():.1f}")
    return series




# ---------------------------------------------------------------------------
# Mode 3: Flat tariff
# ---------------------------------------------------------------------------

def generate_flat_tariff(
    base_price_usd_mwh: float,
    export_price_usd_mwh: float = 0.0,
) -> pd.Series:
    """
    Generate a flat 8760-hour price series (constant every hour).
    Returns pd.Series of length 8760 in USD/MWh.
    """
    series = pd.Series(
        [base_price_usd_mwh] * 8760,
        name="price_usd_mwh",
    )
    print(f"  Flat tariff: {base_price_usd_mwh:.1f} USD/MWh")
    return series


# ---------------------------------------------------------------------------
# Build 30-year CSV from a 1-year profile
# ---------------------------------------------------------------------------

def build_30y_prices(
    hourly_1y: pd.Series,
    export_price_usd_mwh: float = 0.0,
    escalation_rate: float = 0.02,
    export_escalation_rate: float = 0.02,
    project_lifetime: int = 30,
    eur_to_usd: float = None,
) -> pd.DataFrame:
    """
    Repeat a 1-year hourly price profile over project_lifetime years,
    applying annual escalation.

    Parameters
    ----------
    hourly_1y : pd.Series
        8760 hourly prices (USD/MWh or EUR/MWh).
    export_price_usd_mwh : float
        Base export revenue per MWh.
    escalation_rate : float
        Annual real price escalation (e.g., 0.02 = 2%/year).
    export_escalation_rate : float
        Annual escalation for export price.
    project_lifetime : int
        Number of years (default 30).
    eur_to_usd : float, optional
        Conversion rate if prices are in EUR. None = already USD.

    Returns
    -------
    pd.DataFrame with columns: year, Grid electricity final price, Export revenue
    """
    base_prices = hourly_1y.values[:8760]

    if eur_to_usd is not None:
        base_prices = base_prices * eur_to_usd

    rows = []
    for yr in range(1, project_lifetime + 1):
        esc = (1.0 + escalation_rate) ** (yr - 1)
        exp_esc = (1.0 + export_escalation_rate) ** (yr - 1)
        for h in range(8760):
            rows.append({
                "year": yr,
                "Grid electricity final price": base_prices[h] * esc,
                "Export revenue": export_price_usd_mwh * exp_esc,
            })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def update_elec_prices(
    country_code: str = None,
    state_code: str = None,
    address: str = None,
    lat: float = None,
    lon: float = None,
    year: int = 2023,
    mode: str = "auto",
    flat_price_usd_mwh: float = 50.0,
    export_price_usd_mwh: float = 0.0,
    escalation_rate: float = 0.02,
    export_escalation_rate: float = 0.02,
    eur_to_usd: float = 1.08,
    project_lifetime: int = 30,
    entsoe_token: str = None,
    eia_key: str = None,
    output_dir: str = None,
    ppa_technology: str = "blend",
) -> str:
    """
    Generate elec_prices_30y.csv for the SHARE model.

    Parameters
    ----------
    country_code : str
        2-letter country code (e.g., "DE", "US", "OM").
    state_code : str
        US state code (e.g., "CA", "TX"). Only for US.
    address : str
        Geocoded address string (for auto-detection).
    lat, lon : float
        Coordinates (for reverse-geocoding country if needed).
    year : int
        Year to fetch historical prices for (EU/US modes).
    mode : str
        "eu", "us", "flat", or "auto" (detect from country).
    flat_price_usd_mwh : float
        Flat industrial tariff for non-EU/US locations.
    export_price_usd_mwh : float
        Export revenue per MWh.
    escalation_rate : float
        Annual price escalation.
    export_escalation_rate : float
        Annual export price escalation.
    eur_to_usd : float
        EUR to USD conversion (for ENTSO-E prices).
    project_lifetime : int
        Number of years.
    output_dir : str
        Where to write the CSV.
    ppa_technology : str
        For PPA mode: "solar", "wind", or "blend" (average of both).
    """
    out = Path(output_dir) if output_dir else BASE_DIR

    # --- Check database freshness ---
    check_db_staleness()

    # --- Auto-detect mode from country ---
    if mode == "auto":
        if country_code is None and address and lat is not None:
            country_code = detect_country_code(address, lat, lon)
            print(f"  Detected country: {country_code}")

        if country_code == "US" or (state_code and len(state_code) == 2):
            mode = "us"
        elif country_code in EU_COUNTRIES:
            mode = "eu"
        else:
            mode = "flat"

    print(f"\nElectricity price mode: {mode.upper()}")

    # --- Fetch 1-year profile ---
    hourly_1y = None
    currency_note = ""

    if mode == "eu":
        if not country_code or country_code not in ENTSOE_ZONES:
            raise ValueError(f"EU mode requires a valid EU country code. Got: {country_code}")
        hourly_1y = fetch_entsoe_prices(country_code, year, token=entsoe_token)
        currency_note = f" (converted EUR->USD at {eur_to_usd})"

    elif mode == "us":
        if not state_code:
            raise ValueError("US mode requires --state-code (e.g., CA, TX, NY)")
        hourly_1y = fetch_eia_prices(state_code, year, api_key=eia_key)
        eur_to_usd = None  # already USD

    elif mode == "flat":
        # Auto-lookup tariff from database if user didn't override
        if country_code and flat_price_usd_mwh == 50.0:
            db_price, db_name, db_note = lookup_tariff(country_code)
            if db_price is not None:
                flat_price_usd_mwh = db_price
                print(f"  Tariff database: {db_name} = {db_price} USD/MWh ({db_note})")
            else:
                print(f"  No tariff found for '{country_code}', using default {flat_price_usd_mwh} USD/MWh")
        hourly_1y = generate_flat_tariff(flat_price_usd_mwh)
        eur_to_usd = None  # already USD

    elif mode == "ppa":
        # Look up PPA prices from database
        solar_ppa, wind_ppa, ppa_name, ppa_note = lookup_ppa(country_code, state_code)
        if solar_ppa is None and wind_ppa is None:
            raise ValueError(
                f"No PPA data found for country='{country_code}', state='{state_code}'.\n"
                f"Use list_ppa_prices() to see available entries, or use --flat-price instead."
            )
        print(f"  PPA database: {ppa_name}")
        if solar_ppa is not None:
            print(f"    Solar PPA: {solar_ppa} USD/MWh")
        if wind_ppa is not None:
            print(f"    Wind PPA:  {wind_ppa} USD/MWh")
        if ppa_note:
            print(f"    Note: {ppa_note}")

        # Determine price based on technology choice
        if ppa_technology == "solar":
            if solar_ppa is None:
                raise ValueError(f"No solar PPA data for '{ppa_name}'. Try 'wind' or 'blend'.")
            ppa_price = solar_ppa
        elif ppa_technology == "wind":
            if wind_ppa is None:
                raise ValueError(f"No wind PPA data for '{ppa_name}'. Try 'solar' or 'blend'.")
            ppa_price = wind_ppa
        else:  # blend
            available = [p for p in (solar_ppa, wind_ppa) if p is not None]
            ppa_price = sum(available) / len(available)

        print(f"  Using PPA price ({ppa_technology}): {ppa_price:.1f} USD/MWh")
        hourly_1y = generate_flat_tariff(ppa_price)
        eur_to_usd = None  # already USD

    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'eu', 'us', 'flat', 'ppa', or 'auto'.")

    # --- Build 30-year profile ---
    print(f"\nBuilding {project_lifetime}-year price profile (escalation={escalation_rate:.1%}/yr){currency_note}...")
    df = build_30y_prices(
        hourly_1y,
        export_price_usd_mwh=export_price_usd_mwh,
        escalation_rate=escalation_rate,
        export_escalation_rate=export_escalation_rate,
        project_lifetime=project_lifetime,
        eur_to_usd=eur_to_usd,
    )

    # --- Write CSV ---
    csv_path = out / "elec_prices_30y.csv"
    df.to_csv(csv_path, index=False)
    print(f"  Written: {csv_path}")
    print(f"  Rows: {len(df)} ({project_lifetime} years x 8760 hours)")
    print(f"  Year 1 avg: {df[df['year']==1]['Grid electricity final price'].mean():.1f} USD/MWh")
    print(f"  Year {project_lifetime} avg: {df[df['year']==project_lifetime]['Grid electricity final price'].mean():.1f} USD/MWh")

    return str(csv_path)
