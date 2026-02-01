"""
location_profiles.py

Fetch hourly wind and solar PV profiles from Renewables.ninja API
and write them as wind_8760.csv / pv_8760.csv for the SHARE model.
Also generates elec_prices_30y.csv (EU hourly, US hourly, or flat tariff).

Supports location by city/country name (geocoded) or direct lat/lon.

Usage (CLI):
    python location_profiles.py --location "Duqm, Oman" --year 2019
    python location_profiles.py --location "Berlin, Germany" --year 2023 --elec-price-mode eu
    python location_profiles.py --location "Houston, US" --state-code TX --elec-price-mode us
    python location_profiles.py --location "Neom, Saudi Arabia" --flat-price 30

Token:
    Set RENEWABLES_NINJA_TOKEN env var, or place token in ninja_token.txt
    in this directory, or pass --token on the command line.
"""

import argparse
import hashlib
import json
import os
import ssl
import time
from pathlib import Path

import certifi
import pandas as pd
import requests
from geopy.geocoders import Nominatim

# Fix SSL certificates on macOS Python installs
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "cache"
API_BASE = "https://www.renewables.ninja/api"


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def load_token(cli_token: str = None) -> str:
    """
    Load Renewables.ninja API token from (in order):
      1. Explicit cli_token argument
      2. RENEWABLES_NINJA_TOKEN environment variable
      3. ninja_token.txt file in project directory
    """
    if cli_token:
        return cli_token.strip()

    env_token = os.environ.get("RENEWABLES_NINJA_TOKEN", "").strip()
    if env_token:
        return env_token

    token_file = BASE_DIR / "ninja_token.txt"
    if token_file.exists():
        text = token_file.read_text().strip()
        if text:
            return text

    raise RuntimeError(
        "No Renewables.ninja API token found.\n"
        "Provide one via:\n"
        "  1. --token <TOKEN> on the command line\n"
        "  2. RENEWABLES_NINJA_TOKEN environment variable\n"
        "  3. ninja_token.txt file in the project directory\n"
        "Register free at https://www.renewables.ninja/register"
    )


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------

def geocode_location(location_str: str) -> tuple:
    """
    Convert a place name to (lat, lon, local_display_name, english_display_name).

    Examples:
        geocode_location("Duqm, Oman")        -> (23.0, 57.0, "Duqm, Oman", "Duqm, Oman")
        geocode_location("東京, 日本")          -> (35.7, 139.7, "東京都, 日本", "Tokyo, Japan")
    """
    ctx = ssl.create_default_context(cafile=certifi.where())
    geolocator = Nominatim(user_agent="share_model_tea", ssl_context=ctx)
    location = geolocator.geocode(location_str)
    if location is None:
        raise ValueError(f"Could not geocode '{location_str}'. Try a more specific name or use --lat/--lon.")
    local_name = location.address
    # Reverse-geocode with English to get a transliterated name
    try:
        en_result = geolocator.reverse(
            (location.latitude, location.longitude),
            language="en",
        )
        english_name = en_result.address if en_result else local_name
    except Exception:
        english_name = local_name
    return location.latitude, location.longitude, local_name, english_name


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

def _cache_key(endpoint: str, params: dict) -> str:
    """Deterministic hash for an API request."""
    raw = f"{endpoint}|{json.dumps(params, sort_keys=True)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _get_cached(endpoint: str, params: dict):
    """Return cached JSON response or None."""
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(endpoint, params)}.json"
    if path.exists():
        return json.loads(path.read_text())
    return None


def _set_cache(endpoint: str, params: dict, data: dict):
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(endpoint, params)}.json"
    path.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def _api_request(endpoint: str, params: dict, token: str) -> dict:
    """Make a GET request to Renewables.ninja, with caching."""
    cached = _get_cached(endpoint, params)
    if cached is not None:
        print(f"  [cache hit] {endpoint}")
        return cached

    url = f"{API_BASE}/{endpoint}"
    headers = {"Authorization": f"Token {token}"}
    params["format"] = "json"

    print(f"  [fetching] {url}")
    resp = requests.get(url, params=params, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()

    _set_cache(endpoint, params, data)
    return data


def fetch_wind_profile(
    lat: float,
    lon: float,
    year: int,
    token: str,
    capacity: float = 1.0,
    turbine: str = "Vestas V80 2000",
    height: int = 100,
) -> pd.Series:
    """
    Fetch hourly wind capacity factors from Renewables.ninja.

    Returns pd.Series of length 8760 with values in [0, 1].
    """
    params = {
        "lat": lat,
        "lon": lon,
        "date_from": f"{year}-01-01",
        "date_to": f"{year}-12-31",
        "capacity": capacity,
        "height": height,
        "turbine": turbine,
        "dataset": "merra2",
    }
    data = _api_request("data/wind", params, token)

    df = pd.DataFrame.from_dict(data["data"], orient="index")
    df.index = pd.to_datetime(df.index.astype(int), unit="ms", utc=True)
    series = df["electricity"].astype(float).reset_index(drop=True)
    series.name = "wind_cf"

    if len(series) != 8760:
        raise ValueError(f"Expected 8760 hourly values, got {len(series)}. Check the year {year}.")

    return series


def fetch_pv_profile(
    lat: float,
    lon: float,
    year: int,
    token: str,
    capacity: float = 1.0,
    tilt: float = None,
    azim: float = None,
    tracking: int = 0,
    system_loss: float = 0.1,
) -> pd.Series:
    """
    Fetch hourly PV capacity factors from Renewables.ninja.

    If tilt is None, defaults to abs(lat).
    If azim is None, defaults to 180 (north hemisphere) or 0 (south hemisphere).

    Returns pd.Series of length 8760 with values in [0, 1].
    """
    if tilt is None:
        tilt = abs(lat)
    if azim is None:
        azim = 180.0 if lat >= 0 else 0.0

    params = {
        "lat": lat,
        "lon": lon,
        "date_from": f"{year}-01-01",
        "date_to": f"{year}-12-31",
        "capacity": capacity,
        "tilt": tilt,
        "azim": azim,
        "tracking": tracking,
        "system_loss": system_loss,
        "dataset": "merra2",
    }
    data = _api_request("data/pv", params, token)

    df = pd.DataFrame.from_dict(data["data"], orient="index")
    df.index = pd.to_datetime(df.index.astype(int), unit="ms", utc=True)
    series = df["electricity"].astype(float).reset_index(drop=True)
    series.name = "pv_cf"

    if len(series) != 8760:
        raise ValueError(f"Expected 8760 hourly values, got {len(series)}. Check the year {year}.")

    return series


# ---------------------------------------------------------------------------
# Update inputs.py helper
# ---------------------------------------------------------------------------

def _update_inputs_variable(var_name: str, new_value, comment: str = None):
    """
    Update a single variable assignment in inputs.py.
    Preserves the inline comment if present, or uses the provided one.
    """
    import re
    inputs_path = BASE_DIR / "inputs.py"
    text = inputs_path.read_text()

    # Match: var_name = <value> <optional comment>
    pattern = re.compile(
        rf'^({re.escape(var_name)}\s*=\s*)([^\n#]+)(#[^\n]*)?$',
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        print(f"  WARNING: Could not find '{var_name}' in inputs.py — skipping update")
        return False

    # Format the new value
    if isinstance(new_value, float):
        new_val_str = f"{new_value} "
    else:
        new_val_str = f"{new_value} "

    # Use existing comment or provided one
    cmt = f"#{comment}" if comment else (m.group(3) or "")
    replacement = f"{m.group(1)}{new_val_str}{cmt}"
    text = text[:m.start()] + replacement + text[m.end():]
    inputs_path.write_text(text)
    return True


# ---------------------------------------------------------------------------
# Write CSV in SHARE model format
# ---------------------------------------------------------------------------

def _write_8760_csv(series_mw: pd.Series, year: int, filepath: Path):
    """
    Write an 8760-hour MW profile to CSV in the format expected by the SHARE model:
      Row 0: year label
      Rows 1-8760: hourly MW values
    """
    lines = [str(year)]
    for val in series_mw:
        lines.append(str(val))
    filepath.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def update_profiles(
    location: str = None,
    lat: float = None,
    lon: float = None,
    year: int = 2019,
    token: str = None,
    wind_capacity_mw: float = 400.0,
    pv_capacity_mw: float = 400.0,
    turbine: str = "Vestas V80 2000",
    hub_height: int = 100,
    pv_tilt: float = None,
    pv_azim: float = None,
    pv_tracking: int = 0,
    pv_system_loss: float = 0.1,
    output_dir: str = None,
    # Electricity price params
    elec_price_mode: str = "auto",
    elec_price_year: int = None,
    flat_price_usd_mwh: float = 50.0,
    export_price_usd_mwh: float = 0.0,
    escalation_rate: float = 0.02,
    export_escalation_rate: float = 0.02,
    eur_to_usd: float = 1.08,
    state_code: str = None,
    project_lifetime: int = 30,
    ppa_technology: str = "blend",
    water_source: str = "desal",
    land_cost_override: float = None,
    land_region: str = None,
    selling_market: str = "export",
):
    """
    Fetch wind & PV profiles and write wind_8760.csv / pv_8760.csv.

    Parameters
    ----------
    location : str, optional
        City/country name, e.g. "Duqm, Oman". Geocoded to lat/lon.
    lat, lon : float, optional
        Direct coordinates. Used if location is not provided.
    year : int
        Weather year for the profiles (default 2019).
    token : str, optional
        API token. If None, loaded from env/file.
    wind_capacity_mw : float
        Reference wind capacity in MW (default 400).
    pv_capacity_mw : float
        Reference PV capacity in MW (default 400).
    turbine : str
        Wind turbine model name for Renewables.ninja.
    hub_height : int
        Turbine hub height in meters.
    pv_tilt : float, optional
        PV panel tilt in degrees. None = auto (abs(lat)).
    pv_azim : float, optional
        PV azimuth in degrees. None = auto (180 north hem, 0 south hem).
    pv_tracking : int
        0 = fixed, 1 = 1-axis, 2 = 2-axis.
    pv_system_loss : float
        PV system losses as fraction (default 0.1 = 10%).
    output_dir : str, optional
        Directory to write CSVs. Defaults to this script's directory.
    """
    token = load_token(token)
    out = Path(output_dir) if output_dir else BASE_DIR

    # --- Resolve location ---
    if location:
        print(f"Geocoding '{location}'...")
        lat, lon, resolved, resolved_en = geocode_location(location)
        print(f"  Resolved: {resolved}")
        if resolved_en != resolved:
            print(f"  English:  {resolved_en}")
        print(f"  Coordinates: {lat:.4f}, {lon:.4f}")
    elif lat is not None and lon is not None:
        resolved = f"({lat:.4f}, {lon:.4f})"
        resolved_en = resolved
        print(f"Using coordinates: {lat:.4f}, {lon:.4f}")
    else:
        raise ValueError("Provide either --location or both --lat and --lon.")

    # --- Fetch wind ---
    print(f"\nFetching wind profile (turbine={turbine}, height={hub_height}m)...")
    wind_cf = fetch_wind_profile(lat, lon, year, token, turbine=turbine, height=hub_height)
    wind_mw = wind_cf * wind_capacity_mw

    wind_path = out / "wind_8760.csv"
    _write_8760_csv(wind_mw, year, wind_path)
    print(f"  Written: {wind_path}")
    print(f"  Mean CF: {wind_cf.mean():.3f}  |  Peak MW: {wind_mw.max():.1f}  |  Capacity factor hours: {wind_cf.sum():.0f}")

    # --- Rate-limit pause (Renewables.ninja asks for 1s between requests) ---
    time.sleep(1)

    # --- Fetch PV ---
    print(f"\nFetching PV profile (tilt={pv_tilt or 'auto'}, azim={pv_azim or 'auto'}, tracking={pv_tracking})...")
    pv_cf = fetch_pv_profile(
        lat, lon, year, token,
        tilt=pv_tilt, azim=pv_azim, tracking=pv_tracking, system_loss=pv_system_loss,
    )
    pv_mw = pv_cf * pv_capacity_mw

    pv_path = out / "pv_8760.csv"
    _write_8760_csv(pv_mw, year, pv_path)
    print(f"  Written: {pv_path}")
    print(f"  Mean CF: {pv_cf.mean():.3f}  |  Peak MW: {pv_mw.max():.1f}  |  Capacity factor hours: {pv_cf.sum():.0f}")

    # --- Electricity prices ---
    from elec_price_profiles import update_elec_prices
    elec_yr = elec_price_year if elec_price_year else year
    elec_path = update_elec_prices(
        address=resolved if location else None,
        lat=lat,
        lon=lon,
        year=elec_yr,
        mode=elec_price_mode,
        flat_price_usd_mwh=flat_price_usd_mwh,
        export_price_usd_mwh=export_price_usd_mwh,
        escalation_rate=escalation_rate,
        export_escalation_rate=export_escalation_rate,
        eur_to_usd=eur_to_usd,
        state_code=state_code,
        project_lifetime=project_lifetime,
        output_dir=str(out),
        ppa_technology=ppa_technology,
    )

    # --- Water cost lookup ---
    from price_databases import lookup_water_cost
    from elec_price_profiles import detect_country_code
    country_code = None
    if location:
        country_code = detect_country_code(resolved, lat, lon)
    elif lat is not None and lon is not None:
        country_code = detect_country_code("", lat, lon)

    water_cost_usd_m3 = None
    if country_code and country_code != "UNKNOWN":
        price, wname, wnote = lookup_water_cost(country_code, source=water_source)
        if price is not None:
            water_cost_usd_m3 = price
            print(f"\nWater cost ({water_source}): {price:.2f} USD/m3 — {wname}")
            if wnote:
                print(f"  Note: {wnote}")

            # Update inputs.py
            elec_feed_water = 8.82  # m3/tH2 (from inputs.py)
            water_costs_per_tH2 = round(price * elec_feed_water, 2)

            ok1 = _update_inputs_variable(
                "water_unit_cost_per_m3", price,
                f"USD/m3 ({water_source}, {wname})"
            )
            ok2 = _update_inputs_variable(
                "water_costs", water_costs_per_tH2,
                f"USD/tH2 (= {price} x {elec_feed_water} m3/tH2)"
            )
            if ok1 and ok2:
                print(f"  Updated inputs.py: water_unit_cost_per_m3 = {price}")
                print(f"  Updated inputs.py: water_costs = {water_costs_per_tH2} USD/tH2")
        else:
            print(f"\nWater cost: no data for {country_code}")
    else:
        print(f"\nWater cost: could not detect country for water cost lookup")

    # --- Land cost lookup ---
    from price_databases import lookup_land_cost, lookup_land_cost_eu_region

    land_cost_usd_acre_yr = None
    if land_cost_override is not None:
        # User-specified override
        land_cost_usd_acre_yr = land_cost_override
        print(f"\nLand cost (user-specified): {land_cost_override:.2f} USD/acre/yr")
    elif country_code and country_code != "UNKNOWN":
        # EU sub-region lookup if --land-region provided
        if land_region:
            lprice, lname, lnote = lookup_land_cost_eu_region(country_code, land_region)
        else:
            lprice, lname, lnote = lookup_land_cost(country_code, state_code)
        if lprice is not None:
            land_cost_usd_acre_yr = lprice
            print(f"\nLand cost: {lprice:.1f} USD/acre/yr — {lname}")
            if lnote:
                print(f"  Note: {lnote}")
            print(f"  NOTE: Land costs are site-specific. Verify with local data.")
        else:
            print(f"\nLand cost: no data for {country_code}")
    else:
        print(f"\nLand cost: could not detect country for land cost lookup")

    if land_cost_usd_acre_yr is not None:
        ok = _update_inputs_variable(
            "land_cost", land_cost_usd_acre_yr,
            f"USD/acre/yr ({country_code or 'user'})"
        )
        if ok:
            print(f"  Updated inputs.py: land_cost = {land_cost_usd_acre_yr}")

    # --- Tax, depreciation & inflation lookup ---
    from price_databases import lookup_tax

    tax_info = None
    if country_code and country_code != "UNKNOWN":
        tax_info = lookup_tax(country_code, state_code)
        if tax_info:
            tname = tax_info["name"]
            combined = tax_info.get("combined_corporate_tax")
            fed = tax_info.get("corporate_federal_tax")
            st = tax_info.get("corporate_state_tax")
            franchise = tax_info.get("franchise_tax")
            sales = tax_info.get("sales_tax")
            prop = tax_info.get("property_tax_rate")
            vat = tax_info.get("vat_rate")
            dep_yr = tax_info.get("depreciation_years")
            dep_method = tax_info.get("depreciation_method", "")
            infl = tax_info.get("inflation_rate")
            incentives = tax_info.get("tax_incentives", "")

            print(f"\nTax & fiscal profile: {tname}")
            print(f"  Corporate tax (combined): {combined:.1%}" if combined else "")
            if fed is not None:
                print(f"    Federal/national:       {fed:.1%}")
            if st is not None:
                print(f"    State/regional:         {st:.1%}")
            if franchise is not None:
                print(f"    Franchise/margin tax:   {franchise:.2%}")
            if sales is not None:
                print(f"  Sales tax:                {sales:.2%}")
            if vat is not None:
                print(f"  VAT:                      {vat:.1%}")
            if prop is not None:
                print(f"  Property tax rate:        {prop:.2%}")
            if dep_yr is not None:
                print(f"  Depreciation:             {dep_yr} years ({dep_method})")
            if infl is not None:
                print(f"  Inflation (2026 forecast):{infl:.1%}")
            if incentives:
                print(f"  Incentives: {incentives}")

            # Update inputs.py: taxes, depreciation, inflation
            if combined is not None:
                _update_inputs_variable("taxes", combined, f"combined corporate tax ({tname})")
                print(f"  Updated inputs.py: taxes = {combined}")
            if dep_yr is not None:
                _update_inputs_variable("depreciation", dep_yr, f"years straight-line ({tname})")
                print(f"  Updated inputs.py: depreciation = {dep_yr}")
            if infl is not None:
                _update_inputs_variable("inflation", infl, f"CPI forecast 2026 ({tname})")
                print(f"  Updated inputs.py: inflation = {infl}")
            disc = tax_info.get("discount_rate")
            if disc is not None:
                _update_inputs_variable("discount_rate", disc,
                                        f"WACC for green H2 projects ({tname})")
                print(f"  Updated inputs.py: discount_rate = {disc}")
                disc_note = tax_info.get("discount_rate_note", "")
                if disc_note:
                    print(f"  Note: {disc_note}")
        else:
            print(f"\nTax rates: no data for {country_code}")
    else:
        print(f"\nTax rates: could not detect country for tax lookup")

    # --- H2/NH3 selling price lookup ---
    from price_databases import lookup_selling_prices

    selling_info = None
    if country_code and country_code != "UNKNOWN":
        selling_info = lookup_selling_prices(country_code, state_code, market=selling_market)
        if selling_info:
            sname = selling_info["name"]
            g_nh3 = selling_info.get("green_nh3_usd_t")
            gr_nh3 = selling_info.get("grey_nh3_usd_t")
            g_h2 = selling_info.get("green_h2_usd_t")
            gr_h2 = selling_info.get("grey_h2_usd_t")
            snote = selling_info.get("note", "")

            print(f"\nSelling prices ({selling_market}): {sname}")
            if g_h2 is not None:
                print(f"  Green H2:  {g_h2:,.0f} USD/t")
            if gr_h2 is not None:
                print(f"  Grey H2:   {gr_h2:,.0f} USD/t")
            if g_nh3 is not None:
                print(f"  Green NH3: {g_nh3:,.0f} USD/t")
            if gr_nh3 is not None:
                print(f"  Grey NH3:  {gr_nh3:,.0f} USD/t")
            if snote:
                print(f"  Note: {snote}")

            # Update inputs.py
            if g_nh3 is not None:
                _update_inputs_variable("green_NH3_selling_price", g_nh3,
                                        f"USD/t green NH3 ({sname})")
                print(f"  Updated inputs.py: green_NH3_selling_price = {g_nh3}")
            if gr_nh3 is not None:
                _update_inputs_variable("grey_NH3_selling_price", gr_nh3,
                                        f"USD/t grey NH3 ({sname})")
                print(f"  Updated inputs.py: grey_NH3_selling_price = {gr_nh3}")
            if g_h2 is not None:
                _update_inputs_variable("green_H2_selling_price", g_h2,
                                        f"USD/t green H2 ({sname})")
                print(f"  Updated inputs.py: green_H2_selling_price = {g_h2}")
            if gr_h2 is not None:
                _update_inputs_variable("grey_H2_selling_price", gr_h2,
                                        f"USD/t grey H2 ({sname})")
                print(f"  Updated inputs.py: grey_H2_selling_price = {gr_h2}")
        else:
            print(f"\nSelling prices: no data for {country_code}")
    else:
        print(f"\nSelling prices: could not detect country for price lookup")

    # --- Summary ---
    print(f"\nDone. All profiles for {resolved} (year {year}) saved to {out}/")
    return {
        "lat": lat,
        "lon": lon,
        "location": resolved if location else None,
        "location_en": resolved_en if location else None,
        "year": year,
        "wind_mean_cf": float(wind_cf.mean()),
        "pv_mean_cf": float(pv_cf.mean()),
        "wind_path": str(wind_path),
        "pv_path": str(pv_path),
        "elec_prices_path": elec_path,
        "water_cost_usd_m3": water_cost_usd_m3,
        "land_cost_usd_acre_yr": land_cost_usd_acre_yr,
        "tax_info": tax_info,
        "selling_prices": selling_info,
        "country_code": country_code,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Fetch wind & solar profiles from Renewables.ninja for the SHARE model."
    )
    parser.add_argument("--location", type=str, help='City/country, e.g. "Duqm, Oman"')
    parser.add_argument("--lat", type=float, help="Latitude")
    parser.add_argument("--lon", type=float, help="Longitude")
    parser.add_argument("--year", type=int, default=2019, help="Weather data year (default: 2019)")
    parser.add_argument("--token", type=str, help="Renewables.ninja API token (overrides env/file)")

    parser.add_argument("--wind-capacity", type=float, default=400.0, help="Wind reference capacity MW (default: 400)")
    parser.add_argument("--pv-capacity", type=float, default=400.0, help="PV reference capacity MW (default: 400)")
    parser.add_argument("--turbine", type=str, default="Vestas V80 2000", help="Wind turbine model")
    parser.add_argument("--hub-height", type=int, default=100, help="Turbine hub height in meters (default: 100)")
    parser.add_argument("--pv-tilt", type=float, default=None, help="PV tilt degrees (default: auto=abs(lat))")
    parser.add_argument("--pv-azim", type=float, default=None, help="PV azimuth degrees (default: auto)")
    parser.add_argument("--pv-tracking", type=int, default=0, choices=[0, 1, 2], help="PV tracking: 0=fixed, 1=1-axis, 2=2-axis")
    parser.add_argument("--pv-loss", type=float, default=0.1, help="PV system loss fraction (default: 0.1)")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory (default: script directory)")

    # Electricity price arguments
    parser.add_argument("--elec-price-mode", type=str, default="auto",
                        choices=["auto", "eu", "us", "flat", "ppa"],
                        help="Electricity price mode: auto (detect), eu (ENTSO-E), us (EIA), flat, ppa (default: auto)")
    parser.add_argument("--ppa-technology", type=str, default="blend",
                        choices=["solar", "wind", "blend"],
                        help="PPA technology for pricing: solar, wind, or blend (default: blend)")
    parser.add_argument("--elec-price-year", type=int, default=None,
                        help="Year for electricity prices (default: same as --year)")
    parser.add_argument("--flat-price", type=float, default=50.0,
                        help="Flat electricity tariff USD/MWh for non-EU/US locations (default: 50)")
    parser.add_argument("--export-price", type=float, default=0.0,
                        help="Export revenue USD/MWh (default: 0)")
    parser.add_argument("--escalation-rate", type=float, default=0.02,
                        help="Annual electricity price escalation (default: 0.02 = 2%%)")
    parser.add_argument("--export-escalation", type=float, default=0.02,
                        help="Annual export price escalation (default: 0.02 = 2%%)")
    parser.add_argument("--eur-to-usd", type=float, default=1.08,
                        help="EUR to USD conversion for EU prices (default: 1.08)")
    parser.add_argument("--state-code", type=str, default=None,
                        help="US state code for EIA prices (e.g., CA, TX, NY)")
    parser.add_argument("--project-lifetime", type=int, default=30,
                        help="Project lifetime in years (default: 30)")

    # Water cost arguments
    parser.add_argument("--water-source", type=str, default="desal",
                        choices=["desal", "municipal", "cheapest"],
                        help="Water source for cost lookup: desal, municipal, or cheapest (default: desal)")

    # Land cost arguments
    parser.add_argument("--land-cost", type=float, default=None,
                        help="Override land cost USD/acre/yr (default: auto-lookup from database)")
    parser.add_argument("--land-region", type=str, default=None,
                        help="EU sub-region for land cost (e.g., NORTH, ANDALUSIA, SINES). See land_costs.json for available regions.")

    # Selling price arguments
    parser.add_argument("--selling-market", type=str, default="export",
                        choices=["export", "domestic"],
                        help="Market orientation for H2/NH3 selling prices: export or domestic (default: export)")

    args = parser.parse_args()

    update_profiles(
        location=args.location,
        lat=args.lat,
        lon=args.lon,
        year=args.year,
        token=args.token,
        wind_capacity_mw=args.wind_capacity,
        pv_capacity_mw=args.pv_capacity,
        turbine=args.turbine,
        hub_height=args.hub_height,
        pv_tilt=args.pv_tilt,
        pv_azim=args.pv_azim,
        pv_tracking=args.pv_tracking,
        pv_system_loss=args.pv_loss,
        output_dir=args.output_dir,
        elec_price_mode=args.elec_price_mode,
        elec_price_year=args.elec_price_year,
        flat_price_usd_mwh=args.flat_price,
        export_price_usd_mwh=args.export_price,
        escalation_rate=args.escalation_rate,
        export_escalation_rate=args.export_escalation,
        eur_to_usd=args.eur_to_usd,
        state_code=args.state_code,
        project_lifetime=args.project_lifetime,
        ppa_technology=args.ppa_technology,
        water_source=args.water_source,
        land_cost_override=args.land_cost,
        land_region=args.land_region,
        selling_market=args.selling_market,
    )


if __name__ == "__main__":
    main()
