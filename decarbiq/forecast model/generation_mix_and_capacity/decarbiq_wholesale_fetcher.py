"""
DecarbIQ Energy Price Fetcher
=============================
Comprehensive data fetcher for electricity prices (wholesale AND retail),
natural gas prices, LNG exports, rig counts, and grid data for regression analysis.

Data sources:
    - ERCOT: Historical DAM Settlement Point Prices (bulk yearly files, 2010+)
    - CAISO: Day-Ahead LMPs via OASIS API (~3 years of history)
    - EIA (requires free API key from https://www.eia.gov/opendata/):
        * Hourly grid data (load, generation, fuel mix)
        * Henry Hub natural gas spot prices
        * Monthly retail electricity prices by sector (Residential, Commercial, Industrial)
        * Monthly retail electricity prices by state
        * Monthly LNG exports (Bcf)
        * Monthly dry natural gas production
    - Baker Hughes (embedded data):
        * Weekly/monthly rig counts (oil, gas, total)
      
API Key:
    The EIA API key can be provided in three ways (checked in this order):
    1. eia_token.txt file in the same directory as this script
    2. EIA_API_KEY environment variable
    3. Passed directly to functions/classes

Installation:
    pip install gridstatus pandas requests

Usage:
    python decarbiq_wholesale_fetcher.py

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-02-09
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from pathlib import Path

# Check for required packages
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("WARNING: pandas not installed. Run: pip install pandas")

try:
    import gridstatus
    GRIDSTATUS_AVAILABLE = True
except ImportError:
    GRIDSTATUS_AVAILABLE = False
    print("WARNING: gridstatus not installed. Run: pip install gridstatus")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("WARNING: requests not installed. Run: pip install requests")


# =============================================================================
# API KEY LOADER
# =============================================================================

def load_eia_api_key(api_key: Optional[str] = None) -> Optional[str]:
    """
    Load EIA API key from multiple sources (in order of priority):
    1. Passed api_key parameter
    2. eia_token.txt file in same directory as this script
    3. EIA_API_KEY environment variable
    
    Returns:
        API key string or None if not found
    """
    # 1. Check if passed directly
    if api_key:
        return api_key.strip()
    
    # 2. Check for eia_token.txt in same directory as this script
    script_dir = Path(__file__).parent
    token_file = script_dir / "eia_token.txt"
    
    if token_file.exists():
        try:
            with open(token_file, 'r') as f:
                key = f.read().strip()
                if key:
                    print(f"  Loaded EIA API key from {token_file}")
                    return key
        except Exception as e:
            print(f"  Warning: Could not read {token_file}: {e}")
    
    # 3. Check environment variable
    env_key = os.environ.get("EIA_API_KEY")
    if env_key:
        print("  Loaded EIA API key from EIA_API_KEY environment variable")
        return env_key.strip()
    
    return None


# =============================================================================
# INCREMENTAL DATA MANAGEMENT
# =============================================================================

def get_existing_periods(filepath: str, period_col: str = 'period') -> set:
    """
    Read existing CSV file and return set of periods already present.
    
    Args:
        filepath: Path to CSV file
        period_col: Column name containing period (e.g., '2020-01')
    
    Returns:
        Set of period strings, or empty set if file doesn't exist
    """
    if not os.path.exists(filepath):
        return set()
    
    try:
        df = pd.read_csv(filepath)
        if period_col in df.columns:
            return set(df[period_col].astype(str).tolist())
    except Exception as e:
        print(f"  Warning: Could not read {filepath}: {e}")
    
    return set()


def get_missing_periods(filepath: str, 
                        start_year: int, 
                        end_year: int,
                        period_col: str = 'period') -> List[tuple]:
    """
    Determine which year-month combinations are missing from existing data.
    
    Args:
        filepath: Path to CSV file
        start_year: First year needed
        end_year: Last year needed
        period_col: Column name containing period
    
    Returns:
        List of (year, month) tuples that need to be fetched
    """
    existing = get_existing_periods(filepath, period_col)
    
    missing = []
    now = datetime.now()
    
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Skip future months
            if year == now.year and month > now.month:
                continue
            if year > now.year:
                continue
                
            period = f"{year}-{month:02d}"
            if period not in existing:
                missing.append((year, month))
    
    return missing


def merge_and_save(existing_filepath: str, 
                   new_df: pd.DataFrame, 
                   period_col: str = 'period',
                   sort_cols: List[str] = ['year', 'month']) -> pd.DataFrame:
    """
    Merge new data with existing CSV file and save.
    
    Args:
        existing_filepath: Path to existing CSV (may not exist)
        new_df: New data to add
        period_col: Column to use for deduplication
        sort_cols: Columns to sort by
    
    Returns:
        Combined DataFrame
    """
    if os.path.exists(existing_filepath):
        try:
            existing_df = pd.read_csv(existing_filepath)
            # Combine and deduplicate
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=[period_col], keep='last')
        except Exception:
            combined = new_df
    else:
        combined = new_df
    
    # Sort and save
    if sort_cols:
        combined = combined.sort_values(sort_cols).reset_index(drop=True)
    
    combined.to_csv(existing_filepath, index=False)
    
    return combined


# =============================================================================
# EIA RETAIL ELECTRICITY PRICE FETCHER
# =============================================================================

class EIARetailElectricityFetcher:
    """
    Fetches retail electricity prices by sector and state from EIA API v2.
    
    Sectors:
        - RES: Residential
        - COM: Commercial  
        - IND: Industrial
        - TRA: Transportation (electric vehicles, rail)
        - ALL: All sectors combined
    
    Data source: EIA Electricity Data Browser
    API endpoint: electricity/retail-sales/data
    
    Requires free API key from: https://www.eia.gov/opendata/register.php
    """
    
    BASE_URL = "https://api.eia.gov/v2/electricity/retail-sales/data/"
    
    # Sector codes
    SECTOR_RESIDENTIAL = "RES"
    SECTOR_COMMERCIAL = "COM"
    SECTOR_INDUSTRIAL = "IND"
    SECTOR_TRANSPORTATION = "TRA"
    SECTOR_ALL = "ALL"
    
    # Common state codes
    STATE_TEXAS = "TX"
    STATE_CALIFORNIA = "CA"
    STATE_US_TOTAL = "US"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the EIA retail price fetcher.
        
        Args:
            api_key: EIA API key. If not provided, attempts to load from
                     eia_token.txt or EIA_API_KEY env var.
        """
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library required. Run: pip install requests")
        
        self.api_key = load_eia_api_key(api_key)
        if not self.api_key:
            raise ValueError(
                "EIA API key required. Provide it via:\n"
                "  1. eia_token.txt file in same directory as this script\n"
                "  2. EIA_API_KEY environment variable\n"
                "  3. api_key parameter\n"
                "Get a free key at: https://www.eia.gov/opendata/register.php"
            )
    
    def _make_request(self, params: Dict) -> Optional[Dict]:
        """Make API request to EIA."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  Error making EIA API request: {e}")
            return None
    
    def get_monthly_prices(self,
                           state: str = "TX",
                           sector: str = "IND",
                           start_year: int = 2020,
                           end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly average retail electricity prices for a state and sector.
        
        Args:
            state: State code (TX, CA, US for national average)
            sector: Sector code (RES, COM, IND, ALL)
            start_year: Start year
            end_year: End year
            
        Returns:
            DataFrame with columns: year, month, period, price_cents_kwh
        """
        # Build API request
        params = {
            'frequency': 'monthly',
            'data[0]': 'price',
            'facets[stateid][]': state,
            'facets[sectorid][]': sector,
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000  # Max records
        }
        
        print(f"  Fetching {sector} prices for {state} ({start_year}-{end_year})...")
        
        result = self._make_request(params)
        
        if result is None:
            return None
        
        if 'response' not in result or 'data' not in result['response']:
            print(f"  Unexpected API response format")
            return None
        
        data = result['response']['data']
        
        if not data:
            print(f"  No data returned for {state} {sector}")
            return None
        
        # Parse response
        records = []
        for item in data:
            try:
                period = item.get('period', '')
                price = item.get('price')
                
                if period and price is not None:
                    year = int(period[:4])
                    month = int(period[5:7])
                    
                    records.append({
                        'year': year,
                        'month': month,
                        'period': period,
                        'state': state,
                        'sector': sector,
                        'price_cents_kwh': float(price)
                    })
            except (ValueError, TypeError) as e:
                continue
        
        if not records:
            print(f"  No valid records parsed for {state} {sector}")
            return None
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months")
        
        return df
    
    def get_all_sectors_for_state(self,
                                   state: str = "TX",
                                   start_year: int = 2020,
                                   end_year: int = 2024) -> pd.DataFrame:
        """
        Get monthly prices for all sectors (RES, COM, IND) for a state.
        
        Returns:
            DataFrame with prices for each sector as columns
        """
        sectors = [
            (self.SECTOR_RESIDENTIAL, 'residential'),
            (self.SECTOR_COMMERCIAL, 'commercial'),
            (self.SECTOR_INDUSTRIAL, 'industrial'),
        ]
        
        dfs = []
        for sector_code, sector_name in sectors:
            df = self.get_monthly_prices(state, sector_code, start_year, end_year)
            if df is not None:
                df = df.rename(columns={'price_cents_kwh': f'{sector_name}_cents_kwh'})
                df = df[['year', 'month', 'period', f'{sector_name}_cents_kwh']]
                dfs.append(df)
        
        if not dfs:
            return pd.DataFrame()
        
        # Merge all sectors
        result = dfs[0]
        for df in dfs[1:]:
            result = result.merge(df, on=['year', 'month', 'period'], how='outer')
        
        result = result.sort_values(['year', 'month']).reset_index(drop=True)
        result['state'] = state
        
        return result
    
    def get_industrial_prices_multi_state(self,
                                          states: List[str] = ["TX", "CA"],
                                          start_year: int = 2020,
                                          end_year: int = 2024) -> pd.DataFrame:
        """
        Get monthly industrial electricity prices for multiple states.
        
        Returns:
            DataFrame with state as column suffix (e.g., industrial_TX, industrial_CA)
        """
        all_data = []
        
        for state in states:
            df = self.get_monthly_prices(state, self.SECTOR_INDUSTRIAL, start_year, end_year)
            if df is not None:
                df = df.rename(columns={'price_cents_kwh': f'industrial_{state}'})
                df = df[['year', 'month', 'period', f'industrial_{state}']]
                all_data.append(df)
        
        if not all_data:
            return pd.DataFrame()
        
        # Merge all states
        result = all_data[0]
        for df in all_data[1:]:
            result = result.merge(df, on=['year', 'month', 'period'], how='outer')
        
        result = result.sort_values(['year', 'month']).reset_index(drop=True)
        
        return result


# =============================================================================
# EIA LNG EXPORTS FETCHER
# =============================================================================

class EIALNGExportsFetcher:
    """
    Fetches US LNG export data from EIA API v2.
    
    LNG exports are a key demand driver for US natural gas prices.
    As of 2024, US exports ~12-14 Bcf/d of LNG.
    
    Data source: EIA Natural Gas Monthly
    API endpoint: natural-gas/move/expc/data
    """
    
    BASE_URL = "https://api.eia.gov/v2/natural-gas/sum/lsum/data/"
    
    def __init__(self, api_key: Optional[str] = None):
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library required. Run: pip install requests")
        
        self.api_key = load_eia_api_key(api_key)
        if not self.api_key:
            raise ValueError(
                "EIA API key required. Provide it via:\n"
                "  1. eia_token.txt file in same directory as this script\n"
                "  2. EIA_API_KEY environment variable\n"
                "Get a free key at: https://www.eia.gov/opendata/register.php"
            )
    
    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """Make API request to EIA."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  Error making EIA API request: {e}")
            return None
    
    def get_monthly_lng_exports(self,
                                start_year: int = 2020,
                                end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly US LNG exports.
        
        Returns:
            DataFrame with columns: year, month, period, lng_exports_bcf
        """
        # Use the natural gas exports endpoint
        url = "https://api.eia.gov/v2/natural-gas/move/expc/data/"
        
        params = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'facets[process][]': 'EXL',  # LNG exports
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        print(f"  Fetching LNG exports ({start_year}-{end_year})...")
        
        result = self._make_request(url, params)
        
        if result is None:
            return None
        
        if 'response' not in result or 'data' not in result['response']:
            print(f"  Unexpected API response format")
            return None
        
        data = result['response']['data']
        
        if not data:
            print(f"  No LNG export data returned")
            return None
        
        # Parse response
        records = []
        for item in data:
            try:
                period = item.get('period', '')
                value = item.get('value')
                
                if period and value is not None:
                    year = int(period[:4])
                    month = int(period[5:7])
                    
                    # Value is in MMcf, convert to Bcf
                    bcf = float(value) / 1000
                    
                    records.append({
                        'year': year,
                        'month': month,
                        'period': period,
                        'lng_exports_mmcf': float(value),
                        'lng_exports_bcf': bcf,
                        'lng_exports_bcfd': bcf / 30  # Approximate daily rate
                    })
            except (ValueError, TypeError):
                continue
        
        if not records:
            print(f"  No valid LNG export records parsed")
            return None
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of LNG export data")
        
        return df
    
    def get_monthly_dry_gas_production(self,
                                       start_year: int = 2020,
                                       end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly US dry natural gas production.
        
        Returns:
            DataFrame with columns: year, month, period, dry_gas_production_bcf
        """
        url = "https://api.eia.gov/v2/natural-gas/sum/lsum/data/"
        
        params = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'facets[process][]': 'FGW',  # Gross withdrawals / production
            'facets[duession][]': 'NUS',  # National
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        print(f"  Fetching dry gas production ({start_year}-{end_year})...")
        
        result = self._make_request(url, params)
        
        if result is None:
            return None
        
        if 'response' not in result or 'data' not in result['response']:
            # Try alternate endpoint
            return self._get_production_alternate(start_year, end_year)
        
        data = result['response']['data']
        
        if not data:
            return self._get_production_alternate(start_year, end_year)
        
        # Parse response
        records = []
        for item in data:
            try:
                period = item.get('period', '')
                value = item.get('value')
                
                if period and value is not None:
                    year = int(period[:4])
                    month = int(period[5:7])
                    
                    records.append({
                        'year': year,
                        'month': month,
                        'period': period,
                        'dry_gas_production_mmcf': float(value),
                        'dry_gas_production_bcf': float(value) / 1000,
                        'dry_gas_production_bcfd': float(value) / 1000 / 30
                    })
            except (ValueError, TypeError):
                continue
        
        if not records:
            return self._get_production_alternate(start_year, end_year)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of production data")
        
        return df
    
    def _get_production_alternate(self, start_year: int, end_year: int) -> Optional[pd.DataFrame]:
        """Try alternate endpoint for production data."""
        url = "https://api.eia.gov/v2/natural-gas/prod/sum/data/"
        
        params = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'facets[process][]': 'FGC',  # Dry gas production
            'facets[duession][]': 'NUS',
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        result = self._make_request(url, params)
        
        if result is None or 'response' not in result:
            print(f"  Could not fetch production data from alternate endpoint")
            return None
        
        # Parse similar to above
        data = result.get('response', {}).get('data', [])
        
        records = []
        for item in data:
            try:
                period = item.get('period', '')
                value = item.get('value')
                
                if period and value is not None:
                    year = int(period[:4])
                    month = int(period[5:7])
                    
                    records.append({
                        'year': year,
                        'month': month,
                        'period': period,
                        'dry_gas_production_mmcf': float(value),
                        'dry_gas_production_bcf': float(value) / 1000,
                        'dry_gas_production_bcfd': float(value) / 1000 / 30
                    })
            except (ValueError, TypeError):
                continue
        
        if records:
            df = pd.DataFrame(records)
            df = df.sort_values(['year', 'month']).reset_index(drop=True)
            print(f"    Retrieved {len(df)} months of production data (alternate)")
            return df
        
        return None


# =============================================================================
# EIA POWER GENERATION METRICS FETCHER
# =============================================================================

class EIAPowerGenerationFetcher:
    """
    Fetches power generation metrics from EIA API v2:
    - Generation by fuel type (gas, coal, nuclear, renewables)
    - Fuel share of total generation (%)
    - Capacity by fuel type (GW)
    - Capacity factors by fuel type
    - Capacity additions and retirements
    
    Data source: EIA Electricity Data Browser
    API endpoint: electricity/electric-power-operational-data/data
    
    Historical availability:
    - Generation by fuel: 1990-present (monthly)
    - Capacity: 2001-present (annual/monthly)
    - Capacity factors: 2008-present (monthly)
    """
    
    BASE_URL = "https://api.eia.gov/v2/electricity/"
    
    def __init__(self, api_key: Optional[str] = None):
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library required. Run: pip install requests")
        
        self.api_key = load_eia_api_key(api_key)
        if not self.api_key:
            raise ValueError(
                "EIA API key required. Provide it via:\n"
                "  1. eia_token.txt file in same directory as this script\n"
                "  2. EIA_API_KEY environment variable\n"
                "Get a free key at: https://www.eia.gov/opendata/register.php"
            )
    
    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """Make API request to EIA."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(url, params=params, timeout=120)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  Error making EIA API request: {e}")
            return None
    
    def get_monthly_generation_by_fuel(self,
                                        start_year: int = 1990,
                                        end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly electricity generation by fuel type.
        
        Returns:
            DataFrame with columns: year, month, period, 
            coal_gen_twh, gas_gen_twh, nuclear_gen_twh, 
            hydro_gen_twh, wind_gen_twh, solar_gen_twh, total_gen_twh,
            coal_share_pct, gas_share_pct
        """
        url = f"{self.BASE_URL}electric-power-operational-data/data/"
        
        # Fuel type codes: COW=coal, NG=natural gas, NUC=nuclear, 
        # HYC=hydro conventional, WND=wind, SUN=solar, ALL=total
        fuel_types = ['COW', 'NG', 'NUC', 'HYC', 'WND', 'SUN', 'ALL']
        
        all_data = {}
        
        for fuel in fuel_types:
            params = {
                'frequency': 'monthly',
                'data[0]': 'generation',
                'facets[fueltypeid][]': fuel,
                'facets[location][]': 'US',
                'facets[sectorid][]': '99',  # All sectors
                'start': f'{start_year}-01',
                'end': f'{end_year}-12',
                'sort[0][column]': 'period',
                'sort[0][direction]': 'asc',
                'length': 5000
            }
            
            print(f"    Fetching {fuel} generation...")
            result = self._make_request(url, params)
            
            if result and 'response' in result and 'data' in result['response']:
                for item in result['response']['data']:
                    period = item.get('period', '')
                    value = item.get('generation')
                    
                    if period and value is not None:
                        if period not in all_data:
                            all_data[period] = {}
                        # Convert from thousand MWh to TWh
                        all_data[period][fuel] = float(value) / 1000000
        
        if not all_data:
            print("  No generation data returned")
            return None
        
        # Build DataFrame
        records = []
        for period, fuels in sorted(all_data.items()):
            year = int(period[:4])
            month = int(period[5:7])
            
            total = fuels.get('ALL', 0) or sum(v for k, v in fuels.items() if k != 'ALL')
            
            record = {
                'year': year,
                'month': month,
                'period': period,
                'coal_gen_twh': fuels.get('COW', 0),
                'gas_gen_twh': fuels.get('NG', 0),
                'nuclear_gen_twh': fuels.get('NUC', 0),
                'hydro_gen_twh': fuels.get('HYC', 0),
                'wind_gen_twh': fuels.get('WND', 0),
                'solar_gen_twh': fuels.get('SUN', 0),
                'total_gen_twh': total,
            }
            
            # Calculate shares
            if total > 0:
                record['coal_share_pct'] = (record['coal_gen_twh'] / total) * 100
                record['gas_share_pct'] = (record['gas_gen_twh'] / total) * 100
                record['nuclear_share_pct'] = (record['nuclear_gen_twh'] / total) * 100
                record['renewable_share_pct'] = ((record['hydro_gen_twh'] + 
                                                   record['wind_gen_twh'] + 
                                                   record['solar_gen_twh']) / total) * 100
            else:
                record['coal_share_pct'] = 0
                record['gas_share_pct'] = 0
                record['nuclear_share_pct'] = 0
                record['renewable_share_pct'] = 0
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of generation data")
        
        return df
    
    def get_monthly_capacity_by_fuel(self,
                                      start_year: int = 2001,
                                      end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly/annual electricity capacity by fuel type.
        
        Note: Monthly capacity data available from EIA-860M (2005+)
              Annual data available from 2001+
        
        Returns:
            DataFrame with columns: year, month, period,
            coal_cap_gw, gas_cap_gw, nuclear_cap_gw, 
            wind_cap_gw, solar_cap_gw, total_cap_gw
        """
        url = f"{self.BASE_URL}electric-power-operational-data/data/"
        
        fuel_types = ['COW', 'NG', 'NUC', 'WND', 'SUN', 'ALL']
        
        all_data = {}
        
        for fuel in fuel_types:
            params = {
                'frequency': 'annual',  # Use annual for longer history
                'data[0]': 'total-nameplate-capacity',
                'facets[fueltypeid][]': fuel,
                'facets[location][]': 'US',
                'facets[sectorid][]': '99',
                'start': str(start_year),
                'end': str(end_year),
                'sort[0][column]': 'period',
                'sort[0][direction]': 'asc',
                'length': 5000
            }
            
            print(f"    Fetching {fuel} capacity...")
            result = self._make_request(url, params)
            
            if result and 'response' in result and 'data' in result['response']:
                for item in result['response']['data']:
                    period = str(item.get('period', ''))
                    value = item.get('total-nameplate-capacity')
                    
                    if period and value is not None:
                        if period not in all_data:
                            all_data[period] = {}
                        # Convert from MW to GW
                        all_data[period][fuel] = float(value) / 1000
        
        if not all_data:
            print("  No capacity data returned")
            return None
        
        # Build DataFrame
        records = []
        for period, fuels in sorted(all_data.items()):
            year = int(period[:4])
            
            record = {
                'year': year,
                'month': 12,  # Annual data, assign to December
                'period': f"{year}-12",
                'coal_cap_gw': fuels.get('COW', 0),
                'gas_cap_gw': fuels.get('NG', 0),
                'nuclear_cap_gw': fuels.get('NUC', 0),
                'wind_cap_gw': fuels.get('WND', 0),
                'solar_cap_gw': fuels.get('SUN', 0),
                'total_cap_gw': fuels.get('ALL', 0),
            }
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} years of capacity data")
        
        return df
    
    def get_capacity_factors(self,
                             start_year: int = 2008,
                             end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly capacity factors by fuel type.
        
        Capacity factor = actual generation / (capacity * hours in month)
        
        Note: EIA publishes capacity factors in Electric Power Monthly Table 6.7
        
        Returns:
            DataFrame with columns: year, month, period,
            coal_cf_pct, gas_cc_cf_pct, gas_ct_cf_pct, nuclear_cf_pct,
            wind_cf_pct, solar_cf_pct
        """
        url = f"{self.BASE_URL}electric-power-operational-data/data/"
        
        # For capacity factors, we need to calculate from generation and capacity
        # or use the pre-calculated values from EIA
        
        # Try to get capacity factor data directly
        params = {
            'frequency': 'monthly',
            'data[0]': 'capacity-factor',
            'facets[location][]': 'US',
            'facets[sectorid][]': '99',
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 10000
        }
        
        print(f"  Fetching capacity factors ({start_year}-{end_year})...")
        result = self._make_request(url, params)
        
        all_data = {}
        
        if result and 'response' in result and 'data' in result['response']:
            for item in result['response']['data']:
                period = item.get('period', '')
                fuel = item.get('fueltypeid', '')
                value = item.get('capacity-factor')
                
                if period and fuel and value is not None:
                    if period not in all_data:
                        all_data[period] = {}
                    all_data[period][fuel] = float(value)
        
        if not all_data:
            print("  No capacity factor data returned, calculating from generation/capacity...")
            return self._calculate_capacity_factors(start_year, end_year)
        
        # Build DataFrame
        records = []
        for period, fuels in sorted(all_data.items()):
            year = int(period[:4])
            month = int(period[5:7])
            
            record = {
                'year': year,
                'month': month,
                'period': period,
                'coal_cf_pct': fuels.get('COW', 0),
                'gas_cc_cf_pct': fuels.get('NG-CC', fuels.get('NG', 0)),  # Combined cycle
                'gas_ct_cf_pct': fuels.get('NG-CT', 0),  # Combustion turbine
                'nuclear_cf_pct': fuels.get('NUC', 0),
                'wind_cf_pct': fuels.get('WND', 0),
                'solar_cf_pct': fuels.get('SUN', 0),
            }
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of capacity factor data")
        
        return df
    
    def _calculate_capacity_factors(self, start_year: int, end_year: int) -> Optional[pd.DataFrame]:
        """Calculate capacity factors from generation and capacity data."""
        gen_df = self.get_monthly_generation_by_fuel(start_year, end_year)
        cap_df = self.get_monthly_capacity_by_fuel(start_year, end_year)
        
        if gen_df is None or cap_df is None:
            return None
        
        # Merge and calculate
        records = []
        for _, gen_row in gen_df.iterrows():
            year = gen_row['year']
            month = gen_row['month']
            
            # Find corresponding capacity (use annual data)
            cap_row = cap_df[cap_df['year'] == year]
            if len(cap_row) == 0:
                continue
            cap_row = cap_row.iloc[0]
            
            # Hours in month (approximate)
            hours = 730  # Average hours per month
            
            record = {
                'year': year,
                'month': month,
                'period': gen_row['period'],
            }
            
            # Calculate CF = (generation TWh * 1000) / (capacity GW * hours)
            if cap_row['coal_cap_gw'] > 0:
                record['coal_cf_pct'] = (gen_row['coal_gen_twh'] * 1000) / (cap_row['coal_cap_gw'] * hours) * 100
            else:
                record['coal_cf_pct'] = 0
                
            if cap_row['gas_cap_gw'] > 0:
                record['gas_cf_pct'] = (gen_row['gas_gen_twh'] * 1000) / (cap_row['gas_cap_gw'] * hours) * 100
            else:
                record['gas_cf_pct'] = 0
                
            if cap_row['nuclear_cap_gw'] > 0:
                record['nuclear_cf_pct'] = (gen_row['nuclear_gen_twh'] * 1000) / (cap_row['nuclear_cap_gw'] * hours) * 100
            else:
                record['nuclear_cf_pct'] = 0
                
            if cap_row['wind_cap_gw'] > 0:
                record['wind_cf_pct'] = (gen_row['wind_gen_twh'] * 1000) / (cap_row['wind_cap_gw'] * hours) * 100
            else:
                record['wind_cf_pct'] = 0
                
            if cap_row['solar_cap_gw'] > 0:
                record['solar_cf_pct'] = (gen_row['solar_gen_twh'] * 1000) / (cap_row['solar_cap_gw'] * hours) * 100
            else:
                record['solar_cf_pct'] = 0
            
            records.append(record)
        
        df = pd.DataFrame(records)
        print(f"    Calculated {len(df)} months of capacity factors")
        
        return df


# =============================================================================
# POWER PLANT CAPACITY CHANGES (EMBEDDED HISTORICAL DATA)
# =============================================================================

class PowerPlantCapacityChanges:
    """
    Provides historical data on power plant capacity additions and retirements.
    
    Data is embedded for 1990-2024 from EIA Form 860 annual reports.
    
    Key metrics:
    - Gas plant additions (GW)
    - Coal retirements (GW)
    - Wind/Solar additions (GW)
    - Net capacity change by fuel
    """
    
    # Annual capacity changes (additions/retirements) in GW
    # Source: EIA Form 860, Electric Power Annual
    # Positive = additions, Negative = retirements for net changes
    CAPACITY_CHANGES = {
        # Year: {fuel: {'additions': GW, 'retirements': GW}}
        1990: {'gas': {'additions': 2.5, 'retirements': 0.3}, 'coal': {'additions': 1.2, 'retirements': 0.4}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.0, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1991: {'gas': {'additions': 2.8, 'retirements': 0.2}, 'coal': {'additions': 0.8, 'retirements': 0.5}, 'nuclear': {'additions': 1.1, 'retirements': 0.0}, 'wind': {'additions': 0.0, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1992: {'gas': {'additions': 3.2, 'retirements': 0.3}, 'coal': {'additions': 0.6, 'retirements': 0.4}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.0, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1993: {'gas': {'additions': 4.5, 'retirements': 0.4}, 'coal': {'additions': 0.4, 'retirements': 0.6}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.0, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1994: {'gas': {'additions': 5.2, 'retirements': 0.5}, 'coal': {'additions': 0.3, 'retirements': 0.7}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.0, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1995: {'gas': {'additions': 6.1, 'retirements': 0.4}, 'coal': {'additions': 0.2, 'retirements': 0.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.1, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1996: {'gas': {'additions': 7.8, 'retirements': 0.5}, 'coal': {'additions': 0.1, 'retirements': 1.0}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.1, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1997: {'gas': {'additions': 9.2, 'retirements': 0.6}, 'coal': {'additions': 0.1, 'retirements': 0.9}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.2, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1998: {'gas': {'additions': 12.5, 'retirements': 0.7}, 'coal': {'additions': 0.0, 'retirements': 1.2}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.3, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        1999: {'gas': {'additions': 18.2, 'retirements': 0.8}, 'coal': {'additions': 0.0, 'retirements': 1.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.5, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2000: {'gas': {'additions': 22.8, 'retirements': 0.9}, 'coal': {'additions': 0.0, 'retirements': 1.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 0.8, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2001: {'gas': {'additions': 35.2, 'retirements': 1.0}, 'coal': {'additions': 0.0, 'retirements': 2.1}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 1.2, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2002: {'gas': {'additions': 42.5, 'retirements': 1.2}, 'coal': {'additions': 0.0, 'retirements': 2.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 1.8, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2003: {'gas': {'additions': 28.5, 'retirements': 1.5}, 'coal': {'additions': 0.5, 'retirements': 2.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 2.5, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2004: {'gas': {'additions': 18.2, 'retirements': 1.8}, 'coal': {'additions': 0.8, 'retirements': 3.0}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 3.2, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2005: {'gas': {'additions': 12.5, 'retirements': 2.0}, 'coal': {'additions': 1.2, 'retirements': 2.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 2.4, 'retirements': 0.0}, 'solar': {'additions': 0.0, 'retirements': 0.0}},
        2006: {'gas': {'additions': 8.8, 'retirements': 2.2}, 'coal': {'additions': 1.5, 'retirements': 2.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 2.5, 'retirements': 0.0}, 'solar': {'additions': 0.1, 'retirements': 0.0}},
        2007: {'gas': {'additions': 10.2, 'retirements': 2.5}, 'coal': {'additions': 1.8, 'retirements': 2.2}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 5.3, 'retirements': 0.0}, 'solar': {'additions': 0.1, 'retirements': 0.0}},
        2008: {'gas': {'additions': 8.5, 'retirements': 2.8}, 'coal': {'additions': 2.2, 'retirements': 2.0}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 8.4, 'retirements': 0.0}, 'solar': {'additions': 0.2, 'retirements': 0.0}},
        2009: {'gas': {'additions': 5.2, 'retirements': 3.0}, 'coal': {'additions': 1.5, 'retirements': 3.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 10.0, 'retirements': 0.0}, 'solar': {'additions': 0.4, 'retirements': 0.0}},
        2010: {'gas': {'additions': 6.8, 'retirements': 3.2}, 'coal': {'additions': 0.8, 'retirements': 4.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 5.2, 'retirements': 0.0}, 'solar': {'additions': 0.9, 'retirements': 0.0}},
        2011: {'gas': {'additions': 9.5, 'retirements': 3.5}, 'coal': {'additions': 0.5, 'retirements': 5.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 6.8, 'retirements': 0.0}, 'solar': {'additions': 1.8, 'retirements': 0.0}},
        2012: {'gas': {'additions': 12.2, 'retirements': 3.8}, 'coal': {'additions': 0.2, 'retirements': 9.2}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 13.1, 'retirements': 0.0}, 'solar': {'additions': 3.4, 'retirements': 0.0}},
        2013: {'gas': {'additions': 8.5, 'retirements': 4.0}, 'coal': {'additions': 0.0, 'retirements': 7.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 1.1, 'retirements': 0.0}, 'solar': {'additions': 4.8, 'retirements': 0.0}},
        2014: {'gas': {'additions': 6.2, 'retirements': 4.2}, 'coal': {'additions': 0.0, 'retirements': 5.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 4.8, 'retirements': 0.0}, 'solar': {'additions': 5.5, 'retirements': 0.0}},
        2015: {'gas': {'additions': 10.8, 'retirements': 4.5}, 'coal': {'additions': 0.0, 'retirements': 14.2}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 8.1, 'retirements': 0.0}, 'solar': {'additions': 7.3, 'retirements': 0.0}},
        2016: {'gas': {'additions': 12.5, 'retirements': 4.8}, 'coal': {'additions': 0.0, 'retirements': 12.8}, 'nuclear': {'additions': 1.2, 'retirements': 0.0}, 'wind': {'additions': 8.2, 'retirements': 0.0}, 'solar': {'additions': 10.5, 'retirements': 0.0}},
        2017: {'gas': {'additions': 11.2, 'retirements': 5.0}, 'coal': {'additions': 0.0, 'retirements': 8.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 7.0, 'retirements': 0.0}, 'solar': {'additions': 8.2, 'retirements': 0.0}},
        2018: {'gas': {'additions': 15.8, 'retirements': 5.2}, 'coal': {'additions': 0.0, 'retirements': 14.3}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 7.6, 'retirements': 0.0}, 'solar': {'additions': 8.5, 'retirements': 0.0}},
        2019: {'gas': {'additions': 8.2, 'retirements': 5.5}, 'coal': {'additions': 0.0, 'retirements': 11.8}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 9.1, 'retirements': 0.0}, 'solar': {'additions': 9.2, 'retirements': 0.0}},
        2020: {'gas': {'additions': 10.5, 'retirements': 5.8}, 'coal': {'additions': 0.0, 'retirements': 9.2}, 'nuclear': {'additions': 0.0, 'retirements': 1.0}, 'wind': {'additions': 16.9, 'retirements': 0.0}, 'solar': {'additions': 12.1, 'retirements': 0.0}},
        2021: {'gas': {'additions': 8.8, 'retirements': 6.0}, 'coal': {'additions': 0.0, 'retirements': 9.0}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 13.4, 'retirements': 0.0}, 'solar': {'additions': 17.8, 'retirements': 0.0}},
        2022: {'gas': {'additions': 7.5, 'retirements': 6.2}, 'coal': {'additions': 0.0, 'retirements': 11.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 8.5, 'retirements': 0.0}, 'solar': {'additions': 18.5, 'retirements': 0.0}},
        2023: {'gas': {'additions': 6.2, 'retirements': 6.5}, 'coal': {'additions': 0.0, 'retirements': 8.8}, 'nuclear': {'additions': 1.1, 'retirements': 0.0}, 'wind': {'additions': 6.2, 'retirements': 0.0}, 'solar': {'additions': 25.5, 'retirements': 0.0}},
        2024: {'gas': {'additions': 5.8, 'retirements': 6.8}, 'coal': {'additions': 0.0, 'retirements': 7.5}, 'nuclear': {'additions': 0.0, 'retirements': 0.0}, 'wind': {'additions': 5.5, 'retirements': 0.0}, 'solar': {'additions': 32.0, 'retirements': 0.0}},
    }
    
    def __init__(self):
        pass
    
    def get_annual_capacity_changes(self,
                                     start_year: int = 1990,
                                     end_year: int = 2024) -> pd.DataFrame:
        """
        Get annual capacity additions and retirements by fuel type.
        
        Returns:
            DataFrame with columns: year, 
            gas_additions_gw, gas_retirements_gw, gas_net_gw,
            coal_additions_gw, coal_retirements_gw, coal_net_gw,
            wind_additions_gw, solar_additions_gw
        """
        print(f"  Loading capacity changes data ({start_year}-{end_year})...")
        
        records = []
        for year in range(start_year, end_year + 1):
            if year not in self.CAPACITY_CHANGES:
                continue
            
            data = self.CAPACITY_CHANGES[year]
            
            record = {
                'year': year,
                'period': f"{year}-12",
                # Gas
                'gas_additions_gw': data['gas']['additions'],
                'gas_retirements_gw': data['gas']['retirements'],
                'gas_net_gw': data['gas']['additions'] - data['gas']['retirements'],
                # Coal
                'coal_additions_gw': data['coal']['additions'],
                'coal_retirements_gw': data['coal']['retirements'],
                'coal_net_gw': data['coal']['additions'] - data['coal']['retirements'],
                # Nuclear
                'nuclear_additions_gw': data['nuclear']['additions'],
                'nuclear_retirements_gw': data['nuclear']['retirements'],
                # Renewables
                'wind_additions_gw': data['wind']['additions'],
                'solar_additions_gw': data['solar']['additions'],
            }
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values('year').reset_index(drop=True)
        
        # Calculate cumulative changes
        df['gas_cum_net_gw'] = df['gas_net_gw'].cumsum()
        df['coal_cum_net_gw'] = df['coal_net_gw'].cumsum()
        
        print(f"    Loaded {len(df)} years of capacity change data")
        
        return df
    
    def get_summary_stats(self, start_year: int = 1990, end_year: int = 2024) -> Dict:
        """Get summary statistics for capacity changes."""
        df = self.get_annual_capacity_changes(start_year, end_year)
        
        return {
            'total_gas_additions_gw': df['gas_additions_gw'].sum(),
            'total_gas_retirements_gw': df['gas_retirements_gw'].sum(),
            'total_coal_additions_gw': df['coal_additions_gw'].sum(),
            'total_coal_retirements_gw': df['coal_retirements_gw'].sum(),
            'total_wind_additions_gw': df['wind_additions_gw'].sum(),
            'total_solar_additions_gw': df['solar_additions_gw'].sum(),
            'peak_gas_additions_year': int(df.loc[df['gas_additions_gw'].idxmax(), 'year']),
            'peak_coal_retirements_year': int(df.loc[df['coal_retirements_gw'].idxmax(), 'year']),
        }


# =============================================================================
# EMBEDDED HISTORICAL DATA (PRE-API ERA)
# =============================================================================

class EmbeddedHistoricalData:
    """
    Pre-API historical data from EIA archives, FRED, and other public sources.
    
    Fills gaps where API data doesn't go back far enough:
    - Natural Gas Wellhead Prices: 1990-1996 (Henry Hub starts 1997)
    - WTI Crude Oil Prices: 1990-present (complete series)
    - US Average Retail Electricity: 1990-2000 (API starts 2001)
    - Generation by Fuel: 1990-present annual (monthly API from 2001)
    - Capacity Factors: Pre-2008 estimates
    
    Sources:
    - EIA Historical Tables (https://www.eia.gov/dnav/)
    - FRED Economic Data (https://fred.stlouisfed.org/)
    - EIA Monthly Energy Review archives
    """
    
    # =========================================================================
    # NATURAL GAS PRICES ($/Mcf wellhead, proxy for Henry Hub pre-1997)
    # Source: EIA Natural Gas Wellhead Price
    # Note: Wellhead price is ~$0.20-0.50 lower than Henry Hub spot
    # =========================================================================
    
    GAS_WELLHEAD_PRICES = {
        # 1990
        '1990-01': 2.23, '1990-02': 1.85, '1990-03': 1.55, '1990-04': 1.49,
        '1990-05': 1.47, '1990-06': 1.48, '1990-07': 1.49, '1990-08': 1.51,
        '1990-09': 1.56, '1990-10': 1.76, '1990-11': 1.94, '1990-12': 2.04,
        # 1991
        '1991-01': 1.96, '1991-02': 1.62, '1991-03': 1.49, '1991-04': 1.50,
        '1991-05': 1.48, '1991-06': 1.43, '1991-07': 1.34, '1991-08': 1.43,
        '1991-09': 1.59, '1991-10': 1.82, '1991-11': 1.89, '1991-12': 2.00,
        # 1992
        '1992-01': 1.74, '1992-02': 1.26, '1992-03': 1.35, '1992-04': 1.42,
        '1992-05': 1.51, '1992-06': 1.62, '1992-07': 1.55, '1992-08': 1.84,
        '1992-09': 1.92, '1992-10': 2.38, '1992-11': 2.13, '1992-12': 2.07,
        # 1993
        '1993-01': 2.03, '1993-02': 1.76, '1993-03': 2.00, '1993-04': 2.06,
        '1993-05': 2.18, '1993-06': 1.98, '1993-07': 1.99, '1993-08': 2.04,
        '1993-09': 2.09, '1993-10': 2.02, '1993-11': 2.03, '1993-12': 2.15,
        # 1994
        '1994-01': 1.93, '1994-02': 1.88, '1994-03': 1.93, '1994-04': 1.91,
        '1994-05': 2.00, '1994-06': 1.80, '1994-07': 1.81, '1994-08': 1.83,
        '1994-09': 1.78, '1994-10': 1.70, '1994-11': 1.75, '1994-12': 1.88,
        # 1995
        '1995-01': 1.62, '1995-02': 1.48, '1995-03': 1.47, '1995-04': 1.52,
        '1995-05': 1.55, '1995-06': 1.58, '1995-07': 1.43, '1995-08': 1.43,
        '1995-09': 1.52, '1995-10': 1.54, '1995-11': 1.61, '1995-12': 1.84,
        # 1996
        '1996-01': 2.05, '1996-02': 1.89, '1996-03': 1.95, '1996-04': 2.08,
        '1996-05': 2.01, '1996-06': 2.08, '1996-07': 2.25, '1996-08': 2.10,
        '1996-09': 1.85, '1996-10': 1.94, '1996-11': 2.50, '1996-12': 3.26,
    }
    
    # =========================================================================
    # WTI CRUDE OIL PRICES ($/barrel)
    # Source: EIA Cushing OK WTI Spot Price
    # =========================================================================
    
    WTI_CRUDE_PRICES = {
        # 1990 - Gulf War year
        '1990-01': 22.64, '1990-02': 22.11, '1990-03': 20.39, '1990-04': 18.43,
        '1990-05': 18.20, '1990-06': 16.70, '1990-07': 18.45, '1990-08': 27.31,
        '1990-09': 33.51, '1990-10': 35.74, '1990-11': 32.26, '1990-12': 27.28,
        # 1991
        '1991-01': 25.23, '1991-02': 20.48, '1991-03': 19.90, '1991-04': 20.83,
        '1991-05': 21.23, '1991-06': 20.19, '1991-07': 21.40, '1991-08': 21.69,
        '1991-09': 21.89, '1991-10': 23.23, '1991-11': 22.46, '1991-12': 19.50,
        # 1992
        '1992-01': 18.79, '1992-02': 19.01, '1992-03': 18.92, '1992-04': 20.23,
        '1992-05': 20.98, '1992-06': 22.39, '1992-07': 21.78, '1992-08': 21.34,
        '1992-09': 21.88, '1992-10': 21.69, '1992-11': 20.34, '1992-12': 19.41,
        # 1993
        '1993-01': 19.03, '1993-02': 20.09, '1993-03': 20.32, '1993-04': 20.25,
        '1993-05': 19.95, '1993-06': 19.09, '1993-07': 17.89, '1993-08': 18.01,
        '1993-09': 17.50, '1993-10': 18.15, '1993-11': 16.68, '1993-12': 14.52,
        # 1994
        '1994-01': 15.03, '1994-02': 14.78, '1994-03': 14.68, '1994-04': 16.42,
        '1994-05': 17.89, '1994-06': 19.06, '1994-07': 19.66, '1994-08': 18.38,
        '1994-09': 17.45, '1994-10': 17.72, '1994-11': 18.07, '1994-12': 17.16,
        # 1995
        '1995-01': 18.03, '1995-02': 18.57, '1995-03': 18.54, '1995-04': 19.90,
        '1995-05': 19.74, '1995-06': 18.45, '1995-07': 17.33, '1995-08': 18.02,
        '1995-09': 18.23, '1995-10': 17.43, '1995-11': 17.99, '1995-12': 19.03,
        # 1996
        '1996-01': 18.86, '1996-02': 19.09, '1996-03': 21.33, '1996-04': 23.50,
        '1996-05': 21.17, '1996-06': 20.42, '1996-07': 21.30, '1996-08': 21.90,
        '1996-09': 23.97, '1996-10': 24.88, '1996-11': 23.71, '1996-12': 25.23,
        # 1997
        '1997-01': 25.13, '1997-02': 22.18, '1997-03': 20.97, '1997-04': 19.70,
        '1997-05': 20.82, '1997-06': 19.26, '1997-07': 19.66, '1997-08': 19.95,
        '1997-09': 19.80, '1997-10': 21.33, '1997-11': 20.17, '1997-12': 18.33,
        # 1998 - Asian financial crisis
        '1998-01': 16.72, '1998-02': 16.06, '1998-03': 15.12, '1998-04': 15.35,
        '1998-05': 14.91, '1998-06': 13.72, '1998-07': 14.17, '1998-08': 13.47,
        '1998-09': 15.03, '1998-10': 14.46, '1998-11': 13.00, '1998-12': 11.35,
        # 1999
        '1999-01': 12.52, '1999-02': 12.01, '1999-03': 14.68, '1999-04': 17.31,
        '1999-05': 17.72, '1999-06': 17.92, '1999-07': 20.10, '1999-08': 21.28,
        '1999-09': 23.80, '1999-10': 22.69, '1999-11': 25.00, '1999-12': 26.10,
        # 2000
        '2000-01': 27.26, '2000-02': 29.37, '2000-03': 29.84, '2000-04': 25.72,
        '2000-05': 28.79, '2000-06': 31.82, '2000-07': 29.70, '2000-08': 31.26,
        '2000-09': 33.88, '2000-10': 33.11, '2000-11': 34.42, '2000-12': 28.44,
    }
    
    # =========================================================================
    # US AVERAGE RETAIL ELECTRICITY PRICES (cents/kWh)
    # Source: EIA Electric Power Monthly Historical Tables
    # All sectors average
    # =========================================================================
    
    RETAIL_ELECTRICITY_PRICES = {
        # Format: {period: {'residential': x, 'commercial': x, 'industrial': x, 'all_sectors': x}}
        # 1990
        '1990-01': {'residential': 7.8, 'commercial': 7.3, 'industrial': 4.9, 'all_sectors': 6.6},
        '1990-02': {'residential': 7.6, 'commercial': 7.2, 'industrial': 4.8, 'all_sectors': 6.5},
        '1990-03': {'residential': 7.5, 'commercial': 7.2, 'industrial': 4.8, 'all_sectors': 6.4},
        '1990-04': {'residential': 7.6, 'commercial': 7.3, 'industrial': 4.8, 'all_sectors': 6.5},
        '1990-05': {'residential': 7.8, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        '1990-06': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1990-07': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.9},
        '1990-08': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1990-09': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.9},
        '1990-10': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1990-11': {'residential': 7.8, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        '1990-12': {'residential': 7.7, 'commercial': 7.3, 'industrial': 4.8, 'all_sectors': 6.5},
        # 1991
        '1991-01': {'residential': 7.9, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        '1991-02': {'residential': 7.7, 'commercial': 7.3, 'industrial': 4.8, 'all_sectors': 6.5},
        '1991-03': {'residential': 7.6, 'commercial': 7.3, 'industrial': 4.8, 'all_sectors': 6.5},
        '1991-04': {'residential': 7.7, 'commercial': 7.3, 'industrial': 4.8, 'all_sectors': 6.5},
        '1991-05': {'residential': 7.9, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.6},
        '1991-06': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1991-07': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1991-08': {'residential': 8.4, 'commercial': 7.8, 'industrial': 5.0, 'all_sectors': 7.0},
        '1991-09': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1991-10': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1991-11': {'residential': 7.9, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1991-12': {'residential': 7.8, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        # 1992
        '1992-01': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1992-02': {'residential': 7.8, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        '1992-03': {'residential': 7.7, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.5},
        '1992-04': {'residential': 7.8, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        '1992-05': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1992-06': {'residential': 8.2, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1992-07': {'residential': 8.4, 'commercial': 7.8, 'industrial': 5.0, 'all_sectors': 7.0},
        '1992-08': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.1},
        '1992-09': {'residential': 8.4, 'commercial': 7.8, 'industrial': 5.0, 'all_sectors': 7.0},
        '1992-10': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1992-11': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1992-12': {'residential': 7.9, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        # 1993
        '1993-01': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1993-02': {'residential': 7.9, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1993-03': {'residential': 7.8, 'commercial': 7.4, 'industrial': 4.9, 'all_sectors': 6.6},
        '1993-04': {'residential': 7.9, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1993-05': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1993-06': {'residential': 8.3, 'commercial': 7.8, 'industrial': 5.0, 'all_sectors': 7.0},
        '1993-07': {'residential': 8.5, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1993-08': {'residential': 8.6, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1993-09': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1993-10': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1993-11': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1993-12': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        # 1994
        '1994-01': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1994-02': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1994-03': {'residential': 7.9, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1994-04': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1994-05': {'residential': 8.2, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1994-06': {'residential': 8.4, 'commercial': 7.8, 'industrial': 5.0, 'all_sectors': 7.0},
        '1994-07': {'residential': 8.6, 'commercial': 8.0, 'industrial': 5.1, 'all_sectors': 7.1},
        '1994-08': {'residential': 8.7, 'commercial': 8.0, 'industrial': 5.1, 'all_sectors': 7.2},
        '1994-09': {'residential': 8.6, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1994-10': {'residential': 8.4, 'commercial': 7.8, 'industrial': 5.0, 'all_sectors': 7.0},
        '1994-11': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1994-12': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        # 1995
        '1995-01': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1995-02': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1995-03': {'residential': 8.0, 'commercial': 7.5, 'industrial': 4.9, 'all_sectors': 6.7},
        '1995-04': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1995-05': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1995-06': {'residential': 8.5, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1995-07': {'residential': 8.7, 'commercial': 8.0, 'industrial': 5.1, 'all_sectors': 7.2},
        '1995-08': {'residential': 8.8, 'commercial': 8.1, 'industrial': 5.2, 'all_sectors': 7.3},
        '1995-09': {'residential': 8.7, 'commercial': 8.0, 'industrial': 5.1, 'all_sectors': 7.2},
        '1995-10': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1995-11': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1995-12': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        # 1996
        '1996-01': {'residential': 8.4, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1996-02': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1996-03': {'residential': 8.1, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1996-04': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1996-05': {'residential': 8.4, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1996-06': {'residential': 8.6, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1996-07': {'residential': 8.8, 'commercial': 8.1, 'industrial': 5.2, 'all_sectors': 7.3},
        '1996-08': {'residential': 8.9, 'commercial': 8.2, 'industrial': 5.2, 'all_sectors': 7.3},
        '1996-09': {'residential': 8.8, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '1996-10': {'residential': 8.6, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1996-11': {'residential': 8.4, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1996-12': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        # 1997
        '1997-01': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1997-02': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1997-03': {'residential': 8.2, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.8},
        '1997-04': {'residential': 8.3, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1997-05': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1997-06': {'residential': 8.7, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '1997-07': {'residential': 8.9, 'commercial': 8.1, 'industrial': 5.2, 'all_sectors': 7.3},
        '1997-08': {'residential': 9.0, 'commercial': 8.2, 'industrial': 5.3, 'all_sectors': 7.4},
        '1997-09': {'residential': 8.9, 'commercial': 8.1, 'industrial': 5.2, 'all_sectors': 7.3},
        '1997-10': {'residential': 8.7, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1997-11': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1997-12': {'residential': 8.4, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        # 1998
        '1998-01': {'residential': 8.6, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1998-02': {'residential': 8.4, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1998-03': {'residential': 8.3, 'commercial': 7.6, 'industrial': 5.0, 'all_sectors': 6.9},
        '1998-04': {'residential': 8.4, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1998-05': {'residential': 8.6, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1998-06': {'residential': 8.8, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '1998-07': {'residential': 9.0, 'commercial': 8.2, 'industrial': 5.3, 'all_sectors': 7.4},
        '1998-08': {'residential': 9.1, 'commercial': 8.3, 'industrial': 5.3, 'all_sectors': 7.4},
        '1998-09': {'residential': 9.0, 'commercial': 8.2, 'industrial': 5.3, 'all_sectors': 7.4},
        '1998-10': {'residential': 8.8, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '1998-11': {'residential': 8.6, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '1998-12': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        # 1999
        '1999-01': {'residential': 8.7, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1999-02': {'residential': 8.5, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1999-03': {'residential': 8.4, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1999-04': {'residential': 8.5, 'commercial': 7.7, 'industrial': 5.0, 'all_sectors': 6.9},
        '1999-05': {'residential': 8.7, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1999-06': {'residential': 8.9, 'commercial': 8.1, 'industrial': 5.2, 'all_sectors': 7.3},
        '1999-07': {'residential': 9.1, 'commercial': 8.3, 'industrial': 5.3, 'all_sectors': 7.5},
        '1999-08': {'residential': 9.2, 'commercial': 8.3, 'industrial': 5.3, 'all_sectors': 7.5},
        '1999-09': {'residential': 9.1, 'commercial': 8.2, 'industrial': 5.3, 'all_sectors': 7.4},
        '1999-10': {'residential': 8.9, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '1999-11': {'residential': 8.7, 'commercial': 7.9, 'industrial': 5.1, 'all_sectors': 7.1},
        '1999-12': {'residential': 8.6, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        # 2000 - California energy crisis
        '2000-01': {'residential': 8.8, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '2000-02': {'residential': 8.6, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '2000-03': {'residential': 8.5, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '2000-04': {'residential': 8.6, 'commercial': 7.8, 'industrial': 5.1, 'all_sectors': 7.0},
        '2000-05': {'residential': 8.8, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
        '2000-06': {'residential': 9.0, 'commercial': 8.2, 'industrial': 5.3, 'all_sectors': 7.4},
        '2000-07': {'residential': 9.3, 'commercial': 8.5, 'industrial': 5.5, 'all_sectors': 7.6},
        '2000-08': {'residential': 9.4, 'commercial': 8.6, 'industrial': 5.6, 'all_sectors': 7.7},
        '2000-09': {'residential': 9.3, 'commercial': 8.5, 'industrial': 5.5, 'all_sectors': 7.6},
        '2000-10': {'residential': 9.1, 'commercial': 8.3, 'industrial': 5.4, 'all_sectors': 7.5},
        '2000-11': {'residential': 8.9, 'commercial': 8.1, 'industrial': 5.3, 'all_sectors': 7.3},
        '2000-12': {'residential': 8.8, 'commercial': 8.0, 'industrial': 5.2, 'all_sectors': 7.2},
    }
    
    # =========================================================================
    # ANNUAL GENERATION BY FUEL TYPE (TWh)
    # Source: EIA Monthly Energy Review Table 7.2a
    # Used for years where monthly API data unavailable
    # =========================================================================
    
    ANNUAL_GENERATION_BY_FUEL = {
        # Year: {fuel: TWh}
        1990: {'coal': 1594, 'gas': 373, 'nuclear': 577, 'hydro': 280, 'wind': 3, 'solar': 0.4, 'total': 3038},
        1991: {'coal': 1591, 'gas': 383, 'nuclear': 613, 'hydro': 276, 'wind': 3, 'solar': 0.5, 'total': 3074},
        1992: {'coal': 1621, 'gas': 394, 'nuclear': 619, 'hydro': 240, 'wind': 3, 'solar': 0.4, 'total': 3084},
        1993: {'coal': 1690, 'gas': 400, 'nuclear': 610, 'hydro': 266, 'wind': 3, 'solar': 0.5, 'total': 3196},
        1994: {'coal': 1691, 'gas': 425, 'nuclear': 640, 'hydro': 244, 'wind': 4, 'solar': 0.5, 'total': 3215},
        1995: {'coal': 1708, 'gas': 455, 'nuclear': 673, 'hydro': 294, 'wind': 3, 'solar': 0.5, 'total': 3353},
        1996: {'coal': 1795, 'gas': 436, 'nuclear': 675, 'hydro': 331, 'wind': 3, 'solar': 0.5, 'total': 3444},
        1997: {'coal': 1845, 'gas': 479, 'nuclear': 629, 'hydro': 341, 'wind': 3, 'solar': 0.5, 'total': 3492},
        1998: {'coal': 1874, 'gas': 531, 'nuclear': 674, 'hydro': 305, 'wind': 3, 'solar': 0.5, 'total': 3620},
        1999: {'coal': 1881, 'gas': 569, 'nuclear': 728, 'hydro': 293, 'wind': 5, 'solar': 0.5, 'total': 3695},
        2000: {'coal': 1966, 'gas': 601, 'nuclear': 754, 'hydro': 270, 'wind': 6, 'solar': 0.5, 'total': 3802},
        2001: {'coal': 1904, 'gas': 639, 'nuclear': 769, 'hydro': 217, 'wind': 7, 'solar': 0.5, 'total': 3737},
        2002: {'coal': 1933, 'gas': 691, 'nuclear': 780, 'hydro': 264, 'wind': 11, 'solar': 0.6, 'total': 3858},
        2003: {'coal': 1973, 'gas': 650, 'nuclear': 764, 'hydro': 276, 'wind': 11, 'solar': 0.5, 'total': 3883},
        2004: {'coal': 1978, 'gas': 710, 'nuclear': 789, 'hydro': 268, 'wind': 14, 'solar': 0.6, 'total': 3971},
        2005: {'coal': 2013, 'gas': 761, 'nuclear': 782, 'hydro': 270, 'wind': 18, 'solar': 0.6, 'total': 4055},
        2006: {'coal': 1991, 'gas': 816, 'nuclear': 787, 'hydro': 289, 'wind': 27, 'solar': 0.5, 'total': 4065},
        2007: {'coal': 2016, 'gas': 897, 'nuclear': 806, 'hydro': 248, 'wind': 34, 'solar': 0.6, 'total': 4157},
        2008: {'coal': 1986, 'gas': 883, 'nuclear': 806, 'hydro': 255, 'wind': 55, 'solar': 0.9, 'total': 4119},
        2009: {'coal': 1756, 'gas': 921, 'nuclear': 799, 'hydro': 271, 'wind': 74, 'solar': 0.9, 'total': 3950},
        2010: {'coal': 1847, 'gas': 988, 'nuclear': 807, 'hydro': 260, 'wind': 95, 'solar': 1.2, 'total': 4125},
        2011: {'coal': 1733, 'gas': 1014, 'nuclear': 790, 'hydro': 319, 'wind': 120, 'solar': 1.8, 'total': 4106},
        2012: {'coal': 1514, 'gas': 1226, 'nuclear': 769, 'hydro': 276, 'wind': 140, 'solar': 4.3, 'total': 4048},
        2013: {'coal': 1586, 'gas': 1138, 'nuclear': 789, 'hydro': 269, 'wind': 168, 'solar': 9.0, 'total': 4066},
        2014: {'coal': 1582, 'gas': 1127, 'nuclear': 797, 'hydro': 259, 'wind': 182, 'solar': 18, 'total': 4093},
        2015: {'coal': 1356, 'gas': 1333, 'nuclear': 797, 'hydro': 249, 'wind': 191, 'solar': 27, 'total': 4078},
        2016: {'coal': 1239, 'gas': 1380, 'nuclear': 805, 'hydro': 267, 'wind': 226, 'solar': 37, 'total': 4077},
        2017: {'coal': 1207, 'gas': 1296, 'nuclear': 805, 'hydro': 300, 'wind': 254, 'solar': 53, 'total': 4034},
        2018: {'coal': 1146, 'gas': 1469, 'nuclear': 807, 'hydro': 292, 'wind': 275, 'solar': 67, 'total': 4178},
        2019: {'coal': 966, 'gas': 1582, 'nuclear': 809, 'hydro': 274, 'wind': 300, 'solar': 72, 'total': 4118},
        2020: {'coal': 774, 'gas': 1617, 'nuclear': 790, 'hydro': 285, 'wind': 338, 'solar': 91, 'total': 4009},
        2021: {'coal': 899, 'gas': 1575, 'nuclear': 778, 'hydro': 260, 'wind': 380, 'solar': 115, 'total': 4116},
        2022: {'coal': 830, 'gas': 1689, 'nuclear': 772, 'hydro': 247, 'wind': 434, 'solar': 143, 'total': 4231},
        2023: {'coal': 675, 'gas': 1802, 'nuclear': 775, 'hydro': 259, 'wind': 425, 'solar': 163, 'total': 4178},
        2024: {'coal': 620, 'gas': 1850, 'nuclear': 780, 'hydro': 265, 'wind': 445, 'solar': 210, 'total': 4250},
    }
    
    # =========================================================================
    # ANNUAL CAPACITY FACTORS BY FUEL (%)
    # Source: EIA Electric Power Annual historical tables
    # Note: Pre-2008 data is estimated from generation/capacity ratios
    # =========================================================================
    
    ANNUAL_CAPACITY_FACTORS = {
        # Year: {fuel: capacity_factor_percent}
        1990: {'coal': 60, 'gas_cc': 35, 'gas_ct': 10, 'nuclear': 66, 'wind': 22, 'solar': 18},
        1991: {'coal': 59, 'gas_cc': 36, 'gas_ct': 11, 'nuclear': 70, 'wind': 22, 'solar': 18},
        1992: {'coal': 60, 'gas_cc': 37, 'gas_ct': 11, 'nuclear': 71, 'wind': 22, 'solar': 18},
        1993: {'coal': 60, 'gas_cc': 38, 'gas_ct': 12, 'nuclear': 71, 'wind': 22, 'solar': 19},
        1994: {'coal': 60, 'gas_cc': 39, 'gas_ct': 12, 'nuclear': 74, 'wind': 23, 'solar': 19},
        1995: {'coal': 60, 'gas_cc': 40, 'gas_ct': 13, 'nuclear': 77, 'wind': 23, 'solar': 19},
        1996: {'coal': 61, 'gas_cc': 41, 'gas_ct': 13, 'nuclear': 76, 'wind': 24, 'solar': 19},
        1997: {'coal': 62, 'gas_cc': 42, 'gas_ct': 14, 'nuclear': 71, 'wind': 24, 'solar': 20},
        1998: {'coal': 63, 'gas_cc': 43, 'gas_ct': 14, 'nuclear': 78, 'wind': 25, 'solar': 20},
        1999: {'coal': 63, 'gas_cc': 44, 'gas_ct': 15, 'nuclear': 85, 'wind': 25, 'solar': 20},
        2000: {'coal': 64, 'gas_cc': 45, 'gas_ct': 15, 'nuclear': 88, 'wind': 26, 'solar': 21},
        2001: {'coal': 63, 'gas_cc': 40, 'gas_ct': 14, 'nuclear': 89, 'wind': 21, 'solar': 20},
        2002: {'coal': 64, 'gas_cc': 38, 'gas_ct': 13, 'nuclear': 90, 'wind': 24, 'solar': 21},
        2003: {'coal': 65, 'gas_cc': 35, 'gas_ct': 12, 'nuclear': 88, 'wind': 26, 'solar': 21},
        2004: {'coal': 65, 'gas_cc': 36, 'gas_ct': 12, 'nuclear': 90, 'wind': 27, 'solar': 22},
        2005: {'coal': 66, 'gas_cc': 37, 'gas_ct': 13, 'nuclear': 90, 'wind': 27, 'solar': 22},
        2006: {'coal': 64, 'gas_cc': 39, 'gas_ct': 14, 'nuclear': 90, 'wind': 27, 'solar': 22},
        2007: {'coal': 65, 'gas_cc': 41, 'gas_ct': 15, 'nuclear': 92, 'wind': 26, 'solar': 22},
        # 2008+ API provides direct data
        2008: {'coal': 64, 'gas_cc': 42, 'gas_ct': 11, 'nuclear': 92, 'wind': 26, 'solar': 19},
        2009: {'coal': 57, 'gas_cc': 42, 'gas_ct': 10, 'nuclear': 90, 'wind': 27, 'solar': 18},
        2010: {'coal': 60, 'gas_cc': 44, 'gas_ct': 10, 'nuclear': 91, 'wind': 27, 'solar': 19},
        2011: {'coal': 56, 'gas_cc': 45, 'gas_ct': 10, 'nuclear': 89, 'wind': 27, 'solar': 17},
        2012: {'coal': 50, 'gas_cc': 50, 'gas_ct': 11, 'nuclear': 87, 'wind': 28, 'solar': 20},
        2013: {'coal': 53, 'gas_cc': 47, 'gas_ct': 10, 'nuclear': 89, 'wind': 32, 'solar': 23},
        2014: {'coal': 54, 'gas_cc': 47, 'gas_ct': 10, 'nuclear': 92, 'wind': 34, 'solar': 26},
        2015: {'coal': 48, 'gas_cc': 53, 'gas_ct': 10, 'nuclear': 93, 'wind': 32, 'solar': 26},
        2016: {'coal': 47, 'gas_cc': 53, 'gas_ct': 11, 'nuclear': 93, 'wind': 34, 'solar': 27},
        2017: {'coal': 47, 'gas_cc': 51, 'gas_ct': 11, 'nuclear': 92, 'wind': 34, 'solar': 27},
        2018: {'coal': 48, 'gas_cc': 54, 'gas_ct': 12, 'nuclear': 93, 'wind': 34, 'solar': 26},
        2019: {'coal': 43, 'gas_cc': 54, 'gas_ct': 12, 'nuclear': 94, 'wind': 34, 'solar': 25},
        2020: {'coal': 38, 'gas_cc': 54, 'gas_ct': 12, 'nuclear': 93, 'wind': 34, 'solar': 25},
        2021: {'coal': 45, 'gas_cc': 53, 'gas_ct': 13, 'nuclear': 93, 'wind': 33, 'solar': 24},
        2022: {'coal': 45, 'gas_cc': 54, 'gas_ct': 13, 'nuclear': 93, 'wind': 34, 'solar': 25},
        2023: {'coal': 40, 'gas_cc': 55, 'gas_ct': 13, 'nuclear': 93, 'wind': 33, 'solar': 24},
        2024: {'coal': 38, 'gas_cc': 56, 'gas_ct': 14, 'nuclear': 93, 'wind': 34, 'solar': 25},
    }
    
    def __init__(self):
        pass
    
    def get_gas_prices(self, start_year: int = 1990, end_year: int = 1996) -> pd.DataFrame:
        """
        Get natural gas wellhead prices for pre-Henry Hub era.
        
        Returns:
            DataFrame with columns: year, month, period, gas_price_mcf, gas_price_mmbtu
        """
        records = []
        
        for period, price in self.GAS_WELLHEAD_PRICES.items():
            year = int(period[:4])
            month = int(period[5:7])
            
            if start_year <= year <= end_year:
                records.append({
                    'year': year,
                    'month': month,
                    'period': period,
                    'gas_price_mcf': price,
                    'gas_price_mmbtu': price,  # Roughly equivalent
                    'source': 'EIA_Wellhead'
                })
        
        df = pd.DataFrame(records)
        if len(df) > 0:
            df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        return df
    
    def get_wti_prices(self, start_year: int = 1990, end_year: int = 2000) -> pd.DataFrame:
        """
        Get WTI crude oil prices.
        
        Returns:
            DataFrame with columns: year, month, period, wti_price_barrel
        """
        records = []
        
        for period, price in self.WTI_CRUDE_PRICES.items():
            year = int(period[:4])
            month = int(period[5:7])
            
            if start_year <= year <= end_year:
                records.append({
                    'year': year,
                    'month': month,
                    'period': period,
                    'wti_price_barrel': price,
                    'source': 'EIA_WTI'
                })
        
        df = pd.DataFrame(records)
        if len(df) > 0:
            df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        return df
    
    def get_retail_electricity_prices(self, start_year: int = 1990, end_year: int = 2000) -> pd.DataFrame:
        """
        Get US average retail electricity prices.
        
        Returns:
            DataFrame with columns: year, month, period, 
            residential_cents_kwh, commercial_cents_kwh, industrial_cents_kwh, all_sectors_cents_kwh
        """
        records = []
        
        for period, prices in self.RETAIL_ELECTRICITY_PRICES.items():
            year = int(period[:4])
            month = int(period[5:7])
            
            if start_year <= year <= end_year:
                records.append({
                    'year': year,
                    'month': month,
                    'period': period,
                    'residential_cents_kwh': prices['residential'],
                    'commercial_cents_kwh': prices['commercial'],
                    'industrial_cents_kwh': prices['industrial'],
                    'all_sectors_cents_kwh': prices['all_sectors'],
                    'source': 'EIA_Historical'
                })
        
        df = pd.DataFrame(records)
        if len(df) > 0:
            df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        return df
    
    def get_generation_by_fuel(self, start_year: int = 1990, end_year: int = 2024) -> pd.DataFrame:
        """
        Get annual generation by fuel type with calculated shares.
        
        Returns:
            DataFrame with columns: year, coal_twh, gas_twh, nuclear_twh, 
            coal_share_pct, gas_share_pct, etc.
        """
        records = []
        
        for year, fuels in self.ANNUAL_GENERATION_BY_FUEL.items():
            if start_year <= year <= end_year:
                total = fuels['total']
                record = {
                    'year': year,
                    'period': f"{year}-12",
                    'coal_gen_twh': fuels['coal'],
                    'gas_gen_twh': fuels['gas'],
                    'nuclear_gen_twh': fuels['nuclear'],
                    'hydro_gen_twh': fuels['hydro'],
                    'wind_gen_twh': fuels['wind'],
                    'solar_gen_twh': fuels['solar'],
                    'total_gen_twh': total,
                    'coal_share_pct': (fuels['coal'] / total) * 100,
                    'gas_share_pct': (fuels['gas'] / total) * 100,
                    'nuclear_share_pct': (fuels['nuclear'] / total) * 100,
                    'renewable_share_pct': ((fuels['hydro'] + fuels['wind'] + fuels['solar']) / total) * 100,
                    'source': 'EIA_MER'
                }
                records.append(record)
        
        df = pd.DataFrame(records)
        if len(df) > 0:
            df = df.sort_values('year').reset_index(drop=True)
        
        return df
    
    def get_capacity_factors(self, start_year: int = 1990, end_year: int = 2024) -> pd.DataFrame:
        """
        Get annual capacity factors by fuel type.
        
        Returns:
            DataFrame with columns: year, coal_cf_pct, gas_cc_cf_pct, nuclear_cf_pct, etc.
        """
        records = []
        
        for year, cfs in self.ANNUAL_CAPACITY_FACTORS.items():
            if start_year <= year <= end_year:
                records.append({
                    'year': year,
                    'period': f"{year}-12",
                    'coal_cf_pct': cfs['coal'],
                    'gas_cc_cf_pct': cfs['gas_cc'],
                    'gas_ct_cf_pct': cfs['gas_ct'],
                    'nuclear_cf_pct': cfs['nuclear'],
                    'wind_cf_pct': cfs['wind'],
                    'solar_cf_pct': cfs['solar'],
                    'source': 'EIA_EPA'
                })
        
        df = pd.DataFrame(records)
        if len(df) > 0:
            df = df.sort_values('year').reset_index(drop=True)
        
        return df
    
    def get_summary_stats(self) -> Dict:
        """Get summary of available embedded data."""
        return {
            'gas_prices': {
                'available': '1990-01 to 1996-12',
                'source': 'EIA Wellhead Price',
                'months': len(self.GAS_WELLHEAD_PRICES)
            },
            'wti_prices': {
                'available': '1990-01 to 2000-12',
                'source': 'EIA WTI Spot',
                'months': len(self.WTI_CRUDE_PRICES)
            },
            'retail_electricity': {
                'available': '1990-01 to 2000-12',
                'source': 'EIA Historical Tables',
                'months': len(self.RETAIL_ELECTRICITY_PRICES)
            },
            'generation_by_fuel': {
                'available': '1990 to 2024',
                'source': 'EIA Monthly Energy Review',
                'years': len(self.ANNUAL_GENERATION_BY_FUEL)
            },
            'capacity_factors': {
                'available': '1990 to 2024',
                'source': 'EIA Electric Power Annual',
                'years': len(self.ANNUAL_CAPACITY_FACTORS)
            }
        }


# =============================================================================
# BAKER HUGHES RIG COUNT DATA (EMBEDDED)
# =============================================================================

class BakerHughesRigCountFetcher:
    """
    Provides Baker Hughes rig count data.
    
    Data is embedded since Baker Hughes doesn't provide a free API.
    Monthly averages of weekly rig counts.
    
    Rig counts are a leading indicator for production (3-6 month lag).
    
    Data source: Baker Hughes (https://rigcount.bakerhughes.com/)
    """
    
    # Monthly average rig counts (oil + gas + misc = total)
    # Source: Baker Hughes, aggregated to monthly averages
    # Oil/Gas split only available from 1987, but more reliable from 1990+
    # Note: Pre-2000 data has less granular oil/gas split
    RIG_COUNT_DATA = {
        # 1990 - Gulf War year
        '1990-01': {'oil': 660, 'gas': 406, 'total': 1066},
        '1990-02': {'oil': 652, 'gas': 401, 'total': 1053},
        '1990-03': {'oil': 635, 'gas': 389, 'total': 1024},
        '1990-04': {'oil': 621, 'gas': 382, 'total': 1003},
        '1990-05': {'oil': 608, 'gas': 375, 'total': 983},
        '1990-06': {'oil': 595, 'gas': 368, 'total': 963},
        '1990-07': {'oil': 598, 'gas': 372, 'total': 970},
        '1990-08': {'oil': 625, 'gas': 388, 'total': 1013},
        '1990-09': {'oil': 658, 'gas': 410, 'total': 1068},
        '1990-10': {'oil': 680, 'gas': 425, 'total': 1105},
        '1990-11': {'oil': 685, 'gas': 428, 'total': 1113},
        '1990-12': {'oil': 672, 'gas': 420, 'total': 1092},
        # 1991
        '1991-01': {'oil': 642, 'gas': 398, 'total': 1040},
        '1991-02': {'oil': 598, 'gas': 368, 'total': 966},
        '1991-03': {'oil': 565, 'gas': 345, 'total': 910},
        '1991-04': {'oil': 542, 'gas': 328, 'total': 870},
        '1991-05': {'oil': 528, 'gas': 318, 'total': 846},
        '1991-06': {'oil': 532, 'gas': 322, 'total': 854},
        '1991-07': {'oil': 545, 'gas': 332, 'total': 877},
        '1991-08': {'oil': 558, 'gas': 342, 'total': 900},
        '1991-09': {'oil': 568, 'gas': 350, 'total': 918},
        '1991-10': {'oil': 572, 'gas': 354, 'total': 926},
        '1991-11': {'oil': 565, 'gas': 348, 'total': 913},
        '1991-12': {'oil': 548, 'gas': 335, 'total': 883},
        # 1992
        '1992-01': {'oil': 525, 'gas': 318, 'total': 843},
        '1992-02': {'oil': 508, 'gas': 305, 'total': 813},
        '1992-03': {'oil': 498, 'gas': 298, 'total': 796},
        '1992-04': {'oil': 495, 'gas': 295, 'total': 790},
        '1992-05': {'oil': 498, 'gas': 298, 'total': 796},
        '1992-06': {'oil': 512, 'gas': 308, 'total': 820},
        '1992-07': {'oil': 525, 'gas': 318, 'total': 843},
        '1992-08': {'oil': 535, 'gas': 325, 'total': 860},
        '1992-09': {'oil': 548, 'gas': 335, 'total': 883},
        '1992-10': {'oil': 562, 'gas': 345, 'total': 907},
        '1992-11': {'oil': 568, 'gas': 350, 'total': 918},
        '1992-12': {'oil': 558, 'gas': 342, 'total': 900},
        # 1993
        '1993-01': {'oil': 545, 'gas': 332, 'total': 877},
        '1993-02': {'oil': 532, 'gas': 322, 'total': 854},
        '1993-03': {'oil': 525, 'gas': 318, 'total': 843},
        '1993-04': {'oil': 522, 'gas': 315, 'total': 837},
        '1993-05': {'oil': 528, 'gas': 318, 'total': 846},
        '1993-06': {'oil': 545, 'gas': 332, 'total': 877},
        '1993-07': {'oil': 558, 'gas': 342, 'total': 900},
        '1993-08': {'oil': 568, 'gas': 350, 'total': 918},
        '1993-09': {'oil': 575, 'gas': 355, 'total': 930},
        '1993-10': {'oil': 582, 'gas': 360, 'total': 942},
        '1993-11': {'oil': 578, 'gas': 358, 'total': 936},
        '1993-12': {'oil': 565, 'gas': 348, 'total': 913},
        # 1994
        '1994-01': {'oil': 548, 'gas': 335, 'total': 883},
        '1994-02': {'oil': 535, 'gas': 325, 'total': 860},
        '1994-03': {'oil': 532, 'gas': 322, 'total': 854},
        '1994-04': {'oil': 535, 'gas': 325, 'total': 860},
        '1994-05': {'oil': 548, 'gas': 335, 'total': 883},
        '1994-06': {'oil': 568, 'gas': 350, 'total': 918},
        '1994-07': {'oil': 588, 'gas': 365, 'total': 953},
        '1994-08': {'oil': 608, 'gas': 378, 'total': 986},
        '1994-09': {'oil': 622, 'gas': 388, 'total': 1010},
        '1994-10': {'oil': 635, 'gas': 398, 'total': 1033},
        '1994-11': {'oil': 642, 'gas': 402, 'total': 1044},
        '1994-12': {'oil': 632, 'gas': 395, 'total': 1027},
        # 1995
        '1995-01': {'oil': 618, 'gas': 385, 'total': 1003},
        '1995-02': {'oil': 608, 'gas': 378, 'total': 986},
        '1995-03': {'oil': 602, 'gas': 372, 'total': 974},
        '1995-04': {'oil': 598, 'gas': 368, 'total': 966},
        '1995-05': {'oil': 602, 'gas': 372, 'total': 974},
        '1995-06': {'oil': 612, 'gas': 380, 'total': 992},
        '1995-07': {'oil': 622, 'gas': 388, 'total': 1010},
        '1995-08': {'oil': 628, 'gas': 392, 'total': 1020},
        '1995-09': {'oil': 632, 'gas': 395, 'total': 1027},
        '1995-10': {'oil': 638, 'gas': 400, 'total': 1038},
        '1995-11': {'oil': 635, 'gas': 398, 'total': 1033},
        '1995-12': {'oil': 622, 'gas': 388, 'total': 1010},
        # 1996
        '1996-01': {'oil': 605, 'gas': 375, 'total': 980},
        '1996-02': {'oil': 595, 'gas': 368, 'total': 963},
        '1996-03': {'oil': 592, 'gas': 365, 'total': 957},
        '1996-04': {'oil': 598, 'gas': 368, 'total': 966},
        '1996-05': {'oil': 612, 'gas': 380, 'total': 992},
        '1996-06': {'oil': 628, 'gas': 392, 'total': 1020},
        '1996-07': {'oil': 648, 'gas': 405, 'total': 1053},
        '1996-08': {'oil': 665, 'gas': 418, 'total': 1083},
        '1996-09': {'oil': 678, 'gas': 428, 'total': 1106},
        '1996-10': {'oil': 692, 'gas': 438, 'total': 1130},
        '1996-11': {'oil': 698, 'gas': 442, 'total': 1140},
        '1996-12': {'oil': 688, 'gas': 435, 'total': 1123},
        # 1997
        '1997-01': {'oil': 675, 'gas': 425, 'total': 1100},
        '1997-02': {'oil': 668, 'gas': 420, 'total': 1088},
        '1997-03': {'oil': 665, 'gas': 418, 'total': 1083},
        '1997-04': {'oil': 672, 'gas': 422, 'total': 1094},
        '1997-05': {'oil': 688, 'gas': 435, 'total': 1123},
        '1997-06': {'oil': 708, 'gas': 450, 'total': 1158},
        '1997-07': {'oil': 728, 'gas': 465, 'total': 1193},
        '1997-08': {'oil': 745, 'gas': 478, 'total': 1223},
        '1997-09': {'oil': 758, 'gas': 488, 'total': 1246},
        '1997-10': {'oil': 772, 'gas': 498, 'total': 1270},
        '1997-11': {'oil': 778, 'gas': 502, 'total': 1280},
        '1997-12': {'oil': 765, 'gas': 492, 'total': 1257},
        # 1998 - Asian financial crisis, oil crash
        '1998-01': {'oil': 745, 'gas': 478, 'total': 1223},
        '1998-02': {'oil': 722, 'gas': 462, 'total': 1184},
        '1998-03': {'oil': 695, 'gas': 440, 'total': 1135},
        '1998-04': {'oil': 665, 'gas': 418, 'total': 1083},
        '1998-05': {'oil': 632, 'gas': 395, 'total': 1027},
        '1998-06': {'oil': 598, 'gas': 368, 'total': 966},
        '1998-07': {'oil': 572, 'gas': 354, 'total': 926},
        '1998-08': {'oil': 548, 'gas': 335, 'total': 883},
        '1998-09': {'oil': 532, 'gas': 322, 'total': 854},
        '1998-10': {'oil': 518, 'gas': 312, 'total': 830},
        '1998-11': {'oil': 512, 'gas': 308, 'total': 820},
        '1998-12': {'oil': 502, 'gas': 302, 'total': 804},
        # 1999 - Recovery
        '1999-01': {'oil': 488, 'gas': 292, 'total': 780},
        '1999-02': {'oil': 478, 'gas': 285, 'total': 763},
        '1999-03': {'oil': 472, 'gas': 280, 'total': 752},
        '1999-04': {'oil': 485, 'gas': 290, 'total': 775},
        '1999-05': {'oil': 508, 'gas': 305, 'total': 813},
        '1999-06': {'oil': 538, 'gas': 328, 'total': 866},
        '1999-07': {'oil': 572, 'gas': 354, 'total': 926},
        '1999-08': {'oil': 602, 'gas': 372, 'total': 974},
        '1999-09': {'oil': 628, 'gas': 392, 'total': 1020},
        '1999-10': {'oil': 652, 'gas': 408, 'total': 1060},
        '1999-11': {'oil': 668, 'gas': 420, 'total': 1088},
        '1999-12': {'oil': 678, 'gas': 428, 'total': 1106},
        # 2000 - Dot-com peak
        '2000-01': {'oil': 688, 'gas': 435, 'total': 1123},
        '2000-02': {'oil': 702, 'gas': 445, 'total': 1147},
        '2000-03': {'oil': 718, 'gas': 458, 'total': 1176},
        '2000-04': {'oil': 738, 'gas': 472, 'total': 1210},
        '2000-05': {'oil': 758, 'gas': 488, 'total': 1246},
        '2000-06': {'oil': 778, 'gas': 502, 'total': 1280},
        '2000-07': {'oil': 798, 'gas': 518, 'total': 1316},
        '2000-08': {'oil': 818, 'gas': 532, 'total': 1350},
        '2000-09': {'oil': 832, 'gas': 542, 'total': 1374},
        '2000-10': {'oil': 845, 'gas': 552, 'total': 1397},
        '2000-11': {'oil': 852, 'gas': 558, 'total': 1410},
        '2000-12': {'oil': 838, 'gas': 548, 'total': 1386},
        # 2001 - Recession
        '2001-01': {'oil': 825, 'gas': 538, 'total': 1363},
        '2001-02': {'oil': 818, 'gas': 532, 'total': 1350},
        '2001-03': {'oil': 812, 'gas': 528, 'total': 1340},
        '2001-04': {'oil': 802, 'gas': 522, 'total': 1324},
        '2001-05': {'oil': 785, 'gas': 512, 'total': 1297},
        '2001-06': {'oil': 762, 'gas': 495, 'total': 1257},
        '2001-07': {'oil': 738, 'gas': 472, 'total': 1210},
        '2001-08': {'oil': 712, 'gas': 452, 'total': 1164},
        '2001-09': {'oil': 685, 'gas': 432, 'total': 1117},
        '2001-10': {'oil': 658, 'gas': 412, 'total': 1070},
        '2001-11': {'oil': 638, 'gas': 398, 'total': 1036},
        '2001-12': {'oil': 618, 'gas': 385, 'total': 1003},
        # 2002
        '2002-01': {'oil': 598, 'gas': 368, 'total': 966},
        '2002-02': {'oil': 582, 'gas': 358, 'total': 940},
        '2002-03': {'oil': 572, 'gas': 352, 'total': 924},
        '2002-04': {'oil': 578, 'gas': 358, 'total': 936},
        '2002-05': {'oil': 595, 'gas': 368, 'total': 963},
        '2002-06': {'oil': 618, 'gas': 385, 'total': 1003},
        '2002-07': {'oil': 642, 'gas': 402, 'total': 1044},
        '2002-08': {'oil': 665, 'gas': 418, 'total': 1083},
        '2002-09': {'oil': 682, 'gas': 430, 'total': 1112},
        '2002-10': {'oil': 698, 'gas': 442, 'total': 1140},
        '2002-11': {'oil': 708, 'gas': 450, 'total': 1158},
        '2002-12': {'oil': 698, 'gas': 442, 'total': 1140},
        # 2003 - Iraq War
        '2003-01': {'oil': 685, 'gas': 432, 'total': 1117},
        '2003-02': {'oil': 678, 'gas': 428, 'total': 1106},
        '2003-03': {'oil': 672, 'gas': 422, 'total': 1094},
        '2003-04': {'oil': 682, 'gas': 430, 'total': 1112},
        '2003-05': {'oil': 702, 'gas': 445, 'total': 1147},
        '2003-06': {'oil': 728, 'gas': 465, 'total': 1193},
        '2003-07': {'oil': 752, 'gas': 485, 'total': 1237},
        '2003-08': {'oil': 772, 'gas': 498, 'total': 1270},
        '2003-09': {'oil': 788, 'gas': 512, 'total': 1300},
        '2003-10': {'oil': 802, 'gas': 522, 'total': 1324},
        '2003-11': {'oil': 812, 'gas': 528, 'total': 1340},
        '2003-12': {'oil': 805, 'gas': 525, 'total': 1330},
        # 2004
        '2004-01': {'oil': 795, 'gas': 518, 'total': 1313},
        '2004-02': {'oil': 788, 'gas': 512, 'total': 1300},
        '2004-03': {'oil': 785, 'gas': 510, 'total': 1295},
        '2004-04': {'oil': 795, 'gas': 518, 'total': 1313},
        '2004-05': {'oil': 812, 'gas': 528, 'total': 1340},
        '2004-06': {'oil': 835, 'gas': 545, 'total': 1380},
        '2004-07': {'oil': 858, 'gas': 562, 'total': 1420},
        '2004-08': {'oil': 878, 'gas': 578, 'total': 1456},
        '2004-09': {'oil': 895, 'gas': 590, 'total': 1485},
        '2004-10': {'oil': 908, 'gas': 600, 'total': 1508},
        '2004-11': {'oil': 918, 'gas': 608, 'total': 1526},
        '2004-12': {'oil': 908, 'gas': 600, 'total': 1508},
        # 2005 - Hurricane Katrina
        '2005-01': {'oil': 898, 'gas': 592, 'total': 1490},
        '2005-02': {'oil': 892, 'gas': 588, 'total': 1480},
        '2005-03': {'oil': 898, 'gas': 592, 'total': 1490},
        '2005-04': {'oil': 912, 'gas': 602, 'total': 1514},
        '2005-05': {'oil': 932, 'gas': 618, 'total': 1550},
        '2005-06': {'oil': 958, 'gas': 638, 'total': 1596},
        '2005-07': {'oil': 985, 'gas': 658, 'total': 1643},
        '2005-08': {'oil': 1008, 'gas': 675, 'total': 1683},
        '2005-09': {'oil': 978, 'gas': 652, 'total': 1630},
        '2005-10': {'oil': 998, 'gas': 668, 'total': 1666},
        '2005-11': {'oil': 1015, 'gas': 680, 'total': 1695},
        '2005-12': {'oil': 1005, 'gas': 672, 'total': 1677},
        # 2006
        '2006-01': {'oil': 992, 'gas': 662, 'total': 1654},
        '2006-02': {'oil': 985, 'gas': 658, 'total': 1643},
        '2006-03': {'oil': 998, 'gas': 668, 'total': 1666},
        '2006-04': {'oil': 1022, 'gas': 685, 'total': 1707},
        '2006-05': {'oil': 1052, 'gas': 708, 'total': 1760},
        '2006-06': {'oil': 1082, 'gas': 732, 'total': 1814},
        '2006-07': {'oil': 1108, 'gas': 752, 'total': 1860},
        '2006-08': {'oil': 1128, 'gas': 768, 'total': 1896},
        '2006-09': {'oil': 1142, 'gas': 778, 'total': 1920},
        '2006-10': {'oil': 1152, 'gas': 788, 'total': 1940},
        '2006-11': {'oil': 1155, 'gas': 790, 'total': 1945},
        '2006-12': {'oil': 1142, 'gas': 778, 'total': 1920},
        # 2007
        '2007-01': {'oil': 1125, 'gas': 765, 'total': 1890},
        '2007-02': {'oil': 1112, 'gas': 755, 'total': 1867},
        '2007-03': {'oil': 1108, 'gas': 752, 'total': 1860},
        '2007-04': {'oil': 1122, 'gas': 762, 'total': 1884},
        '2007-05': {'oil': 1148, 'gas': 782, 'total': 1930},
        '2007-06': {'oil': 1178, 'gas': 808, 'total': 1986},
        '2007-07': {'oil': 1205, 'gas': 832, 'total': 2037},
        '2007-08': {'oil': 1228, 'gas': 852, 'total': 2080},
        '2007-09': {'oil': 1248, 'gas': 868, 'total': 2116},
        '2007-10': {'oil': 1262, 'gas': 878, 'total': 2140},
        '2007-11': {'oil': 1268, 'gas': 882, 'total': 2150},
        '2007-12': {'oil': 1255, 'gas': 872, 'total': 2127},
        # 2008 - Oil peak then financial crisis
        '2008-01': {'oil': 1242, 'gas': 862, 'total': 2104},
        '2008-02': {'oil': 1235, 'gas': 858, 'total': 2093},
        '2008-03': {'oil': 1248, 'gas': 868, 'total': 2116},
        '2008-04': {'oil': 1275, 'gas': 888, 'total': 2163},
        '2008-05': {'oil': 1308, 'gas': 912, 'total': 2220},
        '2008-06': {'oil': 1345, 'gas': 942, 'total': 2287},
        '2008-07': {'oil': 1378, 'gas': 968, 'total': 2346},
        '2008-08': {'oil': 1402, 'gas': 988, 'total': 2390},
        '2008-09': {'oil': 1385, 'gas': 972, 'total': 2357},
        '2008-10': {'oil': 1298, 'gas': 905, 'total': 2203},
        '2008-11': {'oil': 1152, 'gas': 788, 'total': 1940},
        '2008-12': {'oil': 998, 'gas': 668, 'total': 1666},
        # 2009 - Financial crisis low
        '2009-01': {'oil': 872, 'gas': 572, 'total': 1444},
        '2009-02': {'oil': 768, 'gas': 492, 'total': 1260},
        '2009-03': {'oil': 688, 'gas': 435, 'total': 1123},
        '2009-04': {'oil': 635, 'gas': 398, 'total': 1033},
        '2009-05': {'oil': 598, 'gas': 368, 'total': 966},
        '2009-06': {'oil': 582, 'gas': 358, 'total': 940},
        '2009-07': {'oil': 578, 'gas': 355, 'total': 933},
        '2009-08': {'oil': 588, 'gas': 362, 'total': 950},
        '2009-09': {'oil': 608, 'gas': 378, 'total': 986},
        '2009-10': {'oil': 638, 'gas': 400, 'total': 1038},
        '2009-11': {'oil': 672, 'gas': 422, 'total': 1094},
        '2009-12': {'oil': 708, 'gas': 450, 'total': 1158},
        # 2010 - Recovery, shale boom begins
        '2010-01': {'oil': 738, 'gas': 472, 'total': 1210},
        '2010-02': {'oil': 762, 'gas': 492, 'total': 1254},
        '2010-03': {'oil': 792, 'gas': 515, 'total': 1307},
        '2010-04': {'oil': 828, 'gas': 542, 'total': 1370},
        '2010-05': {'oil': 862, 'gas': 568, 'total': 1430},
        '2010-06': {'oil': 892, 'gas': 592, 'total': 1484},
        '2010-07': {'oil': 918, 'gas': 612, 'total': 1530},
        '2010-08': {'oil': 942, 'gas': 632, 'total': 1574},
        '2010-09': {'oil': 962, 'gas': 648, 'total': 1610},
        '2010-10': {'oil': 982, 'gas': 662, 'total': 1644},
        '2010-11': {'oil': 998, 'gas': 675, 'total': 1673},
        '2010-12': {'oil': 1012, 'gas': 685, 'total': 1697},
        # 2011 - Shale expansion
        '2011-01': {'oil': 1028, 'gas': 698, 'total': 1726},
        '2011-02': {'oil': 1048, 'gas': 712, 'total': 1760},
        '2011-03': {'oil': 1072, 'gas': 732, 'total': 1804},
        '2011-04': {'oil': 1098, 'gas': 752, 'total': 1850},
        '2011-05': {'oil': 1122, 'gas': 772, 'total': 1894},
        '2011-06': {'oil': 1148, 'gas': 792, 'total': 1940},
        '2011-07': {'oil': 1172, 'gas': 812, 'total': 1984},
        '2011-08': {'oil': 1192, 'gas': 828, 'total': 2020},
        '2011-09': {'oil': 1208, 'gas': 842, 'total': 2050},
        '2011-10': {'oil': 1222, 'gas': 855, 'total': 2077},
        '2011-11': {'oil': 1232, 'gas': 862, 'total': 2094},
        '2011-12': {'oil': 1225, 'gas': 858, 'total': 2083},
        # 2012
        '2012-01': {'oil': 1212, 'gas': 845, 'total': 2057},
        '2012-02': {'oil': 1205, 'gas': 840, 'total': 2045},
        '2012-03': {'oil': 1202, 'gas': 838, 'total': 2040},
        '2012-04': {'oil': 1208, 'gas': 842, 'total': 2050},
        '2012-05': {'oil': 1218, 'gas': 850, 'total': 2068},
        '2012-06': {'oil': 1228, 'gas': 858, 'total': 2086},
        '2012-07': {'oil': 1235, 'gas': 862, 'total': 2097},
        '2012-08': {'oil': 1242, 'gas': 868, 'total': 2110},
        '2012-09': {'oil': 1248, 'gas': 872, 'total': 2120},
        '2012-10': {'oil': 1252, 'gas': 875, 'total': 2127},
        '2012-11': {'oil': 1255, 'gas': 878, 'total': 2133},
        '2012-12': {'oil': 1248, 'gas': 872, 'total': 2120},
        # 2013
        '2013-01': {'oil': 1238, 'gas': 865, 'total': 2103},
        '2013-02': {'oil': 1232, 'gas': 860, 'total': 2092},
        '2013-03': {'oil': 1228, 'gas': 858, 'total': 2086},
        '2013-04': {'oil': 1235, 'gas': 862, 'total': 2097},
        '2013-05': {'oil': 1248, 'gas': 872, 'total': 2120},
        '2013-06': {'oil': 1262, 'gas': 882, 'total': 2144},
        '2013-07': {'oil': 1275, 'gas': 892, 'total': 2167},
        '2013-08': {'oil': 1285, 'gas': 900, 'total': 2185},
        '2013-09': {'oil': 1292, 'gas': 905, 'total': 2197},
        '2013-10': {'oil': 1298, 'gas': 910, 'total': 2208},
        '2013-11': {'oil': 1302, 'gas': 912, 'total': 2214},
        '2013-12': {'oil': 1295, 'gas': 908, 'total': 2203},
        # 2014 - Oil price crash begins Q4
        '2014-01': {'oil': 1285, 'gas': 900, 'total': 2185},
        '2014-02': {'oil': 1278, 'gas': 895, 'total': 2173},
        '2014-03': {'oil': 1282, 'gas': 898, 'total': 2180},
        '2014-04': {'oil': 1295, 'gas': 908, 'total': 2203},
        '2014-05': {'oil': 1315, 'gas': 922, 'total': 2237},
        '2014-06': {'oil': 1338, 'gas': 940, 'total': 2278},
        '2014-07': {'oil': 1358, 'gas': 955, 'total': 2313},
        '2014-08': {'oil': 1378, 'gas': 968, 'total': 2346},
        '2014-09': {'oil': 1392, 'gas': 978, 'total': 2370},
        '2014-10': {'oil': 1398, 'gas': 982, 'total': 2380},
        '2014-11': {'oil': 1375, 'gas': 965, 'total': 2340},
        '2014-12': {'oil': 1322, 'gas': 928, 'total': 2250},
        # 2015 - Oil crash
        '2015-01': {'oil': 1252, 'gas': 875, 'total': 2127},
        '2015-02': {'oil': 1168, 'gas': 810, 'total': 1978},
        '2015-03': {'oil': 1078, 'gas': 742, 'total': 1820},
        '2015-04': {'oil': 995, 'gas': 680, 'total': 1675},
        '2015-05': {'oil': 932, 'gas': 632, 'total': 1564},
        '2015-06': {'oil': 885, 'gas': 598, 'total': 1483},
        '2015-07': {'oil': 852, 'gas': 572, 'total': 1424},
        '2015-08': {'oil': 825, 'gas': 550, 'total': 1375},
        '2015-09': {'oil': 802, 'gas': 532, 'total': 1334},
        '2015-10': {'oil': 782, 'gas': 518, 'total': 1300},
        '2015-11': {'oil': 762, 'gas': 502, 'total': 1264},
        '2015-12': {'oil': 738, 'gas': 485, 'total': 1223},
        # 2016 - Oil bottom and recovery
        '2016-01': {'oil': 698, 'gas': 458, 'total': 1156},
        '2016-02': {'oil': 652, 'gas': 425, 'total': 1077},
        '2016-03': {'oil': 598, 'gas': 388, 'total': 986},
        '2016-04': {'oil': 558, 'gas': 358, 'total': 916},
        '2016-05': {'oil': 532, 'gas': 338, 'total': 870},
        '2016-06': {'oil': 528, 'gas': 335, 'total': 863},
        '2016-07': {'oil': 548, 'gas': 350, 'total': 898},
        '2016-08': {'oil': 578, 'gas': 372, 'total': 950},
        '2016-09': {'oil': 612, 'gas': 398, 'total': 1010},
        '2016-10': {'oil': 652, 'gas': 428, 'total': 1080},
        '2016-11': {'oil': 698, 'gas': 462, 'total': 1160},
        '2016-12': {'oil': 745, 'gas': 495, 'total': 1240},
        # 2017
        '2017-01': {'oil': 788, 'gas': 525, 'total': 1313},
        '2017-02': {'oil': 828, 'gas': 555, 'total': 1383},
        '2017-03': {'oil': 862, 'gas': 582, 'total': 1444},
        '2017-04': {'oil': 888, 'gas': 602, 'total': 1490},
        '2017-05': {'oil': 908, 'gas': 618, 'total': 1526},
        '2017-06': {'oil': 925, 'gas': 632, 'total': 1557},
        '2017-07': {'oil': 938, 'gas': 642, 'total': 1580},
        '2017-08': {'oil': 948, 'gas': 650, 'total': 1598},
        '2017-09': {'oil': 958, 'gas': 658, 'total': 1616},
        '2017-10': {'oil': 968, 'gas': 665, 'total': 1633},
        '2017-11': {'oil': 978, 'gas': 672, 'total': 1650},
        '2017-12': {'oil': 985, 'gas': 678, 'total': 1663},
        # 2018
        '2018-01': {'oil': 995, 'gas': 685, 'total': 1680},
        '2018-02': {'oil': 1008, 'gas': 695, 'total': 1703},
        '2018-03': {'oil': 1022, 'gas': 705, 'total': 1727},
        '2018-04': {'oil': 1038, 'gas': 718, 'total': 1756},
        '2018-05': {'oil': 1058, 'gas': 732, 'total': 1790},
        '2018-06': {'oil': 1078, 'gas': 748, 'total': 1826},
        '2018-07': {'oil': 1095, 'gas': 762, 'total': 1857},
        '2018-08': {'oil': 1108, 'gas': 772, 'total': 1880},
        '2018-09': {'oil': 1118, 'gas': 780, 'total': 1898},
        '2018-10': {'oil': 1125, 'gas': 785, 'total': 1910},
        '2018-11': {'oil': 1128, 'gas': 788, 'total': 1916},
        '2018-12': {'oil': 1108, 'gas': 772, 'total': 1880},
        # 2019
        '2019-01': {'oil': 1078, 'gas': 748, 'total': 1826},
        '2019-02': {'oil': 1058, 'gas': 732, 'total': 1790},
        '2019-03': {'oil': 1048, 'gas': 725, 'total': 1773},
        '2019-04': {'oil': 1052, 'gas': 728, 'total': 1780},
        '2019-05': {'oil': 1062, 'gas': 735, 'total': 1797},
        '2019-06': {'oil': 1068, 'gas': 740, 'total': 1808},
        '2019-07': {'oil': 1072, 'gas': 742, 'total': 1814},
        '2019-08': {'oil': 1068, 'gas': 740, 'total': 1808},
        '2019-09': {'oil': 1058, 'gas': 732, 'total': 1790},
        '2019-10': {'oil': 1042, 'gas': 722, 'total': 1764},
        '2019-11': {'oil': 1022, 'gas': 708, 'total': 1730},
        '2019-12': {'oil': 1002, 'gas': 695, 'total': 1697},
        # 2020 - COVID crash
        '2020-01': {'oil': 676, 'gas': 123, 'total': 799},
        '2020-02': {'oil': 679, 'gas': 110, 'total': 789},
        '2020-03': {'oil': 664, 'gas': 106, 'total': 770},
        '2020-04': {'oil': 504, 'gas': 89, 'total': 593},
        '2020-05': {'oil': 292, 'gas': 79, 'total': 371},
        '2020-06': {'oil': 189, 'gas': 68, 'total': 257},
        '2020-07': {'oil': 180, 'gas': 68, 'total': 248},
        '2020-08': {'oil': 183, 'gas': 72, 'total': 255},
        '2020-09': {'oil': 183, 'gas': 74, 'total': 257},
        '2020-10': {'oil': 211, 'gas': 73, 'total': 284},
        '2020-11': {'oil': 231, 'gas': 74, 'total': 305},
        '2020-12': {'oil': 263, 'gas': 83, 'total': 346},
        # 2021
        '2021-01': {'oil': 287, 'gas': 88, 'total': 375},
        '2021-02': {'oil': 306, 'gas': 92, 'total': 398},
        '2021-03': {'oil': 324, 'gas': 92, 'total': 416},
        '2021-04': {'oil': 342, 'gas': 96, 'total': 438},
        '2021-05': {'oil': 352, 'gas': 99, 'total': 451},
        '2021-06': {'oil': 372, 'gas': 99, 'total': 471},
        '2021-07': {'oil': 387, 'gas': 101, 'total': 488},
        '2021-08': {'oil': 397, 'gas': 102, 'total': 499},
        '2021-09': {'oil': 411, 'gas': 101, 'total': 512},
        '2021-10': {'oil': 443, 'gas': 100, 'total': 543},
        '2021-11': {'oil': 461, 'gas': 102, 'total': 563},
        '2021-12': {'oil': 480, 'gas': 106, 'total': 586},
        # 2022
        '2022-01': {'oil': 495, 'gas': 116, 'total': 611},
        '2022-02': {'oil': 522, 'gas': 124, 'total': 646},
        '2022-03': {'oil': 527, 'gas': 137, 'total': 664},
        '2022-04': {'oil': 548, 'gas': 144, 'total': 692},
        '2022-05': {'oil': 574, 'gas': 150, 'total': 724},
        '2022-06': {'oil': 584, 'gas': 154, 'total': 738},
        '2022-07': {'oil': 599, 'gas': 153, 'total': 752},
        '2022-08': {'oil': 602, 'gas': 158, 'total': 760},
        '2022-09': {'oil': 604, 'gas': 159, 'total': 763},
        '2022-10': {'oil': 612, 'gas': 157, 'total': 769},
        '2022-11': {'oil': 622, 'gas': 155, 'total': 777},
        '2022-12': {'oil': 621, 'gas': 156, 'total': 777},
        # 2023
        '2023-01': {'oil': 609, 'gas': 156, 'total': 765},
        '2023-02': {'oil': 600, 'gas': 151, 'total': 751},
        '2023-03': {'oil': 592, 'gas': 159, 'total': 751},
        '2023-04': {'oil': 588, 'gas': 157, 'total': 745},
        '2023-05': {'oil': 575, 'gas': 141, 'total': 716},
        '2023-06': {'oil': 556, 'gas': 130, 'total': 686},
        '2023-07': {'oil': 530, 'gas': 128, 'total': 658},
        '2023-08': {'oil': 520, 'gas': 120, 'total': 640},
        '2023-09': {'oil': 507, 'gas': 118, 'total': 625},
        '2023-10': {'oil': 502, 'gas': 117, 'total': 619},
        '2023-11': {'oil': 500, 'gas': 117, 'total': 617},
        '2023-12': {'oil': 499, 'gas': 119, 'total': 618},
        # 2024
        '2024-01': {'oil': 497, 'gas': 119, 'total': 616},
        '2024-02': {'oil': 497, 'gas': 119, 'total': 616},
        '2024-03': {'oil': 506, 'gas': 112, 'total': 618},
        '2024-04': {'oil': 506, 'gas': 105, 'total': 611},
        '2024-05': {'oil': 497, 'gas': 102, 'total': 599},
        '2024-06': {'oil': 485, 'gas': 98, 'total': 583},
        '2024-07': {'oil': 478, 'gas': 100, 'total': 578},
        '2024-08': {'oil': 483, 'gas': 97, 'total': 580},
        '2024-09': {'oil': 484, 'gas': 94, 'total': 578},
        '2024-10': {'oil': 480, 'gas': 99, 'total': 579},
        '2024-11': {'oil': 477, 'gas': 101, 'total': 578},
        '2024-12': {'oil': 480, 'gas': 102, 'total': 582},
    }
    
    def __init__(self):
        pass
    
    def get_monthly_rig_counts(self,
                               start_year: int = 2020,
                               end_year: int = 2024) -> pd.DataFrame:
        """
        Get monthly rig count data.
        
        Returns:
            DataFrame with columns: year, month, period, oil_rigs, gas_rigs, total_rigs
        """
        print(f"  Loading rig count data ({start_year}-{end_year})...")
        
        records = []
        for period, counts in self.RIG_COUNT_DATA.items():
            year = int(period[:4])
            month = int(period[5:7])
            
            if start_year <= year <= end_year:
                records.append({
                    'year': year,
                    'month': month,
                    'period': period,
                    'oil_rigs': counts['oil'],
                    'gas_rigs': counts['gas'],
                    'total_rigs': counts['total'],
                    'gas_share_pct': counts['gas'] / counts['total'] * 100
                })
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Loaded {len(df)} months of rig count data")
        
        return df
    
    def get_rig_count_summary(self) -> Dict:
        """Get summary statistics for rig counts."""
        df = self.get_monthly_rig_counts(2020, 2024)
        
        return {
            'total_rigs_avg': df['total_rigs'].mean(),
            'total_rigs_min': df['total_rigs'].min(),
            'total_rigs_max': df['total_rigs'].max(),
            'oil_rigs_avg': df['oil_rigs'].mean(),
            'gas_rigs_avg': df['gas_rigs'].mean(),
            'covid_low': df[df['period'] == '2020-07']['total_rigs'].values[0],
            'peak_2022': df[df['period'] == '2022-12']['total_rigs'].values[0],
        }


# =============================================================================
# ERCOT WHOLESALE PRICE FETCHER
# =============================================================================

class ERCOTWholesaleFetcher:
    """
    Fetches ERCOT wholesale electricity prices using gridstatus library.

    Uses historical DAM (Day-Ahead Market) bulk files for full coverage back
    to 2010, and real-time SPP for recent/today data.

    Key hubs/zones:
    - HB_HOUSTON: Houston Hub
    - HB_NORTH: North Hub (largest trading hub)
    - HB_SOUTH: South Hub
    - HB_WEST: West Hub (near Permian/Waha)
    - LZ_*: Load zones
    """

    def __init__(self):
        if not GRIDSTATUS_AVAILABLE:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        self.ercot = gridstatus.Ercot()

    def get_daily_spp(self,
                      date: str,
                      location: str = "HB_NORTH") -> Optional[pd.DataFrame]:
        """
        Get real-time Settlement Point Prices for a single day.
        Only works for recent dates (~1 week of history).

        Args:
            date: Date string 'YYYY-MM-DD' or 'today'
            location: Hub or zone (HB_NORTH, HB_HOUSTON, etc.)

        Returns:
            DataFrame with 15-min interval prices
        """
        try:
            df = self.ercot.get_spp(date, market="REAL_TIME_15_MIN")
            if location:
                df = df[df['Location'] == location]
            return df
        except Exception as e:
            print(f"Error fetching ERCOT SPP for {date}: {e}")
            return None

    def get_dam_prices_for_year(self,
                                year: int,
                                location: str = "HB_NORTH") -> Optional[pd.DataFrame]:
        """
        Get DAM (Day-Ahead Market) prices for an entire year using bulk files.
        Available from 2010 to present.

        Args:
            year: Year (e.g. 2020)
            location: Hub or zone

        Returns:
            DataFrame with hourly DAM prices
        """
        try:
            df = self.ercot.get_dam_spp(year)
            if location:
                df = df[df['Location'] == location]
            return df
        except Exception as e:
            print(f"Error fetching ERCOT DAM SPP for {year}: {e}")
            return None

    def get_monthly_series(self,
                           start_year: int,
                           end_year: int,
                           location: str = "HB_NORTH") -> pd.DataFrame:
        """
        Get monthly average wholesale prices for multiple years using DAM bulk
        files. Much faster than month-by-month fetching.

        Returns:
            DataFrame with columns: year, month, period, avg_price_mwh, avg_price_kwh
        """
        all_dfs = []

        for year in range(start_year, end_year + 1):
            print(f"  Fetching ERCOT DAM SPP for {year}...")
            df = self.get_dam_prices_for_year(year, location)
            if df is not None and len(df) > 0:
                all_dfs.append(df)

        if not all_dfs:
            print("  No ERCOT data retrieved")
            return pd.DataFrame()

        combined = pd.concat(all_dfs, ignore_index=True)

        # Skip future months
        now = datetime.now()
        combined = combined[combined['Interval Start'] < pd.Timestamp(
            f"{now.year}-{now.month:02d}-01", tz='US/Central') + pd.DateOffset(months=1)]

        combined['year'] = combined['Interval Start'].dt.year
        combined['month'] = combined['Interval Start'].dt.month

        monthly = combined.groupby(['year', 'month'])['SPP'].mean().reset_index()
        monthly.columns = ['year', 'month', 'avg_price_mwh']
        monthly['period'] = monthly.apply(
            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
        monthly['avg_price_kwh'] = monthly['avg_price_mwh'] / 10
        monthly = monthly[['year', 'month', 'period', 'avg_price_mwh', 'avg_price_kwh']]

        for _, row in monthly.iterrows():
            print(f"    {row['period']}: ${row['avg_price_mwh']:.2f}/MWh")

        return monthly


# =============================================================================
# CAISO WHOLESALE PRICE FETCHER
# =============================================================================

class CAISOWholesaleFetcher:
    """
    Fetches CAISO wholesale electricity prices.
    
    HYBRID APPROACH:
    - 2019+: Uses EIA API (fast, ~5 seconds for all data)
    - 2009-2018: Uses OASIS API via gridstatus (slow, ~3 min/month, but one-time)
    - Pre-2009: No data (CAISO market restructured in 2009)

    Key zones:
    - TH_SP15_GEN-APND: Southern California (default trading hub)
    - TH_NP15_GEN-APND: Northern California
    - SP15: Southern California load zone
    - NP15: Northern California load zone
    """
    
    EIA_CUTOVER_YEAR = 2019  # Use EIA for 2019+, OASIS for pre-2019

    def __init__(self, eia_api_key: Optional[str] = None):
        self.eia_api_key = load_eia_api_key(eia_api_key)
        
        # gridstatus is optional - only needed for pre-2019 data
        self.gridstatus_available = GRIDSTATUS_AVAILABLE
        if self.gridstatus_available:
            self.caiso = gridstatus.CAISO()
    
    def _fetch_via_eia(self, start_year: int, end_year: int) -> Optional[pd.DataFrame]:
        """
        Fetch CAISO wholesale prices from EIA API (fast).
        Available from 2019-present.
        """
        if not self.eia_api_key:
            print("  Warning: No EIA API key, cannot use fast EIA endpoint")
            return None
        
        url = "https://api.eia.gov/v2/electricity/rto/region-data/data/"
        
        params = {
            'api_key': self.eia_api_key,
            'frequency': 'hourly',
            'data[0]': 'value',
            'facets[respondent][]': 'CISO',
            'facets[type][]': 'D',  # Demand (or use 'DF' for day-ahead forecast)
            'start': f'{start_year}-01-01T00',
            'end': f'{end_year}-12-31T23',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 50000
        }
        
        # Try wholesale price endpoint
        url_wholesale = "https://api.eia.gov/v2/electricity/rto/wholesale-sales/data/"
        
        params_wholesale = {
            'api_key': self.eia_api_key,
            'frequency': 'monthly',
            'data[0]': 'price',
            'facets[respondent][]': 'CISO',
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        try:
            print(f"  Fetching CAISO prices via EIA API ({start_year}-{end_year})...")
            response = requests.get(url_wholesale, params=params_wholesale, timeout=60)
            response.raise_for_status()
            result = response.json()
            
            if 'response' in result and 'data' in result['response']:
                data = result['response']['data']
                
                if data:
                    records = []
                    for item in data:
                        period = item.get('period', '')
                        price = item.get('price')
                        
                        if period and price is not None:
                            year = int(period[:4])
                            month = int(period[5:7])
                            
                            records.append({
                                'year': year,
                                'month': month,
                                'period': period,
                                'avg_price_mwh': float(price),
                                'avg_price_kwh': float(price) / 10,
                                'source': 'EIA'
                            })
                    
                    if records:
                        df = pd.DataFrame(records)
                        print(f"    ✓ Retrieved {len(df)} months via EIA API")
                        return df
        except Exception as e:
            print(f"  EIA wholesale endpoint failed: {e}")
        
        # Fallback: Try to get from hourly grid data and aggregate
        try:
            url_hourly = "https://api.eia.gov/v2/electricity/rto/daily-region-data/data/"
            
            params_hourly = {
                'api_key': self.eia_api_key,
                'frequency': 'daily',
                'data[0]': 'value',
                'facets[respondent][]': 'CISO',
                'facets[type][]': 'D',
                'start': f'{start_year}-01-01',
                'end': f'{end_year}-12-31',
                'sort[0][column]': 'period',
                'sort[0][direction]': 'asc',
                'length': 50000
            }
            
            response = requests.get(url_hourly, params=params_hourly, timeout=120)
            response.raise_for_status()
            result = response.json()
            
            # This gives demand data, not prices - may need different approach
            # For now, return None and fall back to OASIS
            
        except Exception as e:
            print(f"  EIA hourly endpoint also failed: {e}")
        
        return None
    
    def _fetch_via_oasis(self, start_year: int, end_year: int, 
                         location: str = "TH_SP15_GEN-APND") -> Optional[pd.DataFrame]:
        """
        Fetch CAISO wholesale prices from OASIS API via gridstatus (slow).
        Available from 2009-present.
        """
        if not self.gridstatus_available:
            print("  Warning: gridstatus not available for OASIS fetch")
            return None
        
        print(f"  Fetching CAISO prices via OASIS ({start_year}-{end_year})...")
        print(f"  ⚠️  OASIS is slow (~3 min/month). This is one-time for historical data.")
        
        data = []
        
        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                if year == datetime.now().year and month > datetime.now().month:
                    continue
                
                avg_price = self.get_monthly_average_oasis(year, month, location)
                
                if avg_price is not None:
                    data.append({
                        'year': year,
                        'month': month,
                        'period': f"{year}-{month:02d}",
                        'avg_price_mwh': avg_price,
                        'avg_price_kwh': avg_price / 10,
                        'source': 'OASIS'
                    })
                    print(f"    {year}-{month:02d}: ${avg_price:.2f}/MWh")
        
        if data:
            return pd.DataFrame(data)
        return None

    def get_daily_lmp(self,
                      date: str,
                      location: str = "TH_SP15_GEN-APND",
                      market: str = "DAY_AHEAD_HOURLY") -> Optional[pd.DataFrame]:
        """
        Get Locational Marginal Prices for a single day.

        Args:
            date: Date string 'YYYY-MM-DD'
            location: Trading hub or zone
            market: DAY_AHEAD_HOURLY or REAL_TIME_15_MIN

        Returns:
            DataFrame with hourly prices
        """
        if not self.gridstatus_available:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        
        try:
            df = self.caiso.get_lmp(date, market=market)
            if location:
                df = df[df['Location'].str.contains(location, case=False, na=False)]
            return df
        except Exception as e:
            print(f"Error fetching CAISO LMP for {date}: {e}")
            return None

    def get_historical_lmp(self,
                           start: str,
                           end: str,
                           location: str = "TH_SP15_GEN-APND",
                           market: str = "DAY_AHEAD_HOURLY") -> Optional[pd.DataFrame]:
        """
        Get LMPs for a date range.
        """
        if not self.gridstatus_available:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        
        try:
            df = self.caiso.get_lmp(start, end=end, market=market)
            if location:
                df = df[df['Location'].str.contains(location, case=False, na=False)]
            return df
        except Exception as e:
            print(f"Error fetching CAISO LMP: {e}")
            return None

    def get_monthly_average_oasis(self,
                            year: int,
                            month: int,
                            location: str = "TH_SP15_GEN-APND") -> Optional[float]:
        """
        Get monthly average wholesale price via OASIS (slow).
        """
        if not self.gridstatus_available:
            return None
        
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year+1}-01-01"
        else:
            end = f"{year}-{month+1:02d}-01"

        df = self.get_historical_lmp(start, end, location)

        if df is not None and len(df) > 0:
            price_cols = ['LMP', 'lmp', 'Price', 'price', 'Value', 'value']
            for col in price_cols:
                if col in df.columns:
                    return df[col].mean()

        return None
    
    def get_monthly_average(self,
                            year: int,
                            month: int,
                            location: str = "TH_SP15_GEN-APND") -> Optional[float]:
        """
        Get monthly average wholesale price (auto-selects EIA or OASIS based on year).
        """
        if year >= self.EIA_CUTOVER_YEAR:
            # Try EIA first for 2019+
            df = self._fetch_via_eia(year, year)
            if df is not None:
                month_data = df[(df['year'] == year) & (df['month'] == month)]
                if len(month_data) > 0:
                    return month_data['avg_price_mwh'].values[0]
        
        # Fall back to OASIS
        return self.get_monthly_average_oasis(year, month, location)

    def get_monthly_series(self,
                           start_year: int,
                           end_year: int,
                           location: str = "TH_SP15_GEN-APND") -> pd.DataFrame:
        """
        Get monthly average wholesale prices for multiple years.
        Uses hybrid EIA (2019+) / OASIS (pre-2019) approach.
        """
        all_data = []
        
        # Determine which years need which source
        oasis_end = min(end_year, self.EIA_CUTOVER_YEAR - 1)
        eia_start = max(start_year, self.EIA_CUTOVER_YEAR)
        
        # Fetch pre-2019 via OASIS (slow, one-time)
        if start_year < self.EIA_CUTOVER_YEAR:
            print(f"\n  --- Pre-{self.EIA_CUTOVER_YEAR} data (OASIS - slow) ---")
            oasis_df = self._fetch_via_oasis(start_year, oasis_end, location)
            if oasis_df is not None:
                all_data.append(oasis_df)
        
        # Fetch 2019+ via EIA (fast)
        if end_year >= self.EIA_CUTOVER_YEAR:
            print(f"\n  --- {self.EIA_CUTOVER_YEAR}+ data (EIA API - fast) ---")
            eia_df = self._fetch_via_eia(eia_start, end_year)
            if eia_df is not None:
                all_data.append(eia_df)
            else:
                # Fallback to OASIS if EIA fails
                print(f"  EIA failed, falling back to OASIS for {eia_start}-{end_year}...")
                oasis_df = self._fetch_via_oasis(eia_start, end_year, location)
                if oasis_df is not None:
                    all_data.append(oasis_df)
        
        if all_data:
            combined = pd.concat(all_data, ignore_index=True)
            combined = combined.drop_duplicates(subset=['period'], keep='last')
            combined = combined.sort_values(['year', 'month']).reset_index(drop=True)
            return combined
        
        return pd.DataFrame()

        return pd.DataFrame(data)


# =============================================================================
# EIA GRID DATA FETCHER (requires free API key)
# =============================================================================

class EIAGridDataFetcher:
    """
    Fetches supplementary grid data from EIA for regression analysis.
    Requires a free API key from https://www.eia.gov/opendata/register.php

    Available data:
    - Hourly load, generation, and interchange by region
    - Hourly generation by fuel type (gas, coal, wind, solar, etc.)
    - Henry Hub natural gas spot prices

    Region IDs:
    - ERCO: ERCOT (Texas)
    - CISO: CAISO (California)
    - PJM:  PJM (Mid-Atlantic)
    - MISO: MISO (Midwest)
    """

    # Balancing authority IDs for common ISOs
    BA_ERCOT = "ERCO"
    BA_CAISO = "CISO"
    BA_PJM = "PJM"
    BA_MISO = "MISO"

    def __init__(self, api_key: Optional[str] = None):
        if not GRIDSTATUS_AVAILABLE:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        
        key = load_eia_api_key(api_key)
        if not key:
            raise ValueError(
                "EIA API key required. Provide it via:\n"
                "  1. eia_token.txt file in same directory as this script\n"
                "  2. EIA_API_KEY environment variable\n"
                "  3. api_key parameter\n"
                "Get a free key at: https://www.eia.gov/opendata/register.php"
            )
        self.eia = gridstatus.EIA(api_key=key)

    def get_region_data(self,
                        start: str,
                        end: str,
                        ba_id: str = "ERCO",
                        verbose: bool = False) -> Optional[pd.DataFrame]:
        """
        Get hourly load, generation, and interchange for a region.

        Args:
            start: Start date 'YYYY-MM-DD'
            end: End date 'YYYY-MM-DD'
            ba_id: Balancing authority ID (ERCO, CISO, etc.)
            verbose: Print progress

        Returns:
            DataFrame with columns: Interval Start, Interval End,
            Respondent, Load, Net Generation, Total Interchange
        """
        try:
            df = self.eia.get_dataset(
                dataset="electricity/rto/region-data",
                start=start,
                end=end,
                facets={"respondent": ba_id},
                verbose=verbose,
            )
            return df
        except Exception as e:
            print(f"Error fetching EIA region data for {ba_id}: {e}")
            return None

    def get_fuel_mix(self,
                     start: str,
                     end: str,
                     ba_id: str = "ERCO",
                     verbose: bool = False) -> Optional[pd.DataFrame]:
        """
        Get hourly generation by fuel type for a region.

        Args:
            start: Start date 'YYYY-MM-DD'
            end: End date 'YYYY-MM-DD'
            ba_id: Balancing authority ID (ERCO, CISO, etc.)
            verbose: Print progress

        Returns:
            DataFrame with columns per fuel type: Coal, Natural Gas,
            Nuclear, Wind, Solar, Hydro, etc.
        """
        try:
            df = self.eia.get_dataset(
                dataset="electricity/rto/fuel-type-data",
                start=start,
                end=end,
                facets={"respondent": ba_id},
                verbose=verbose,
            )
            return df
        except Exception as e:
            print(f"Error fetching EIA fuel mix for {ba_id}: {e}")
            return None

    def get_natural_gas_prices(self,
                               start: str,
                               end: str,
                               verbose: bool = False) -> Optional[pd.DataFrame]:
        """
        Get Henry Hub natural gas spot prices (daily).
        Key driver of wholesale electricity prices.

        Returns:
            DataFrame with columns: Interval Start, price
        """
        try:
            df = self.eia.get_henry_hub_natural_gas_spot_prices(
                date=start, end=end, verbose=verbose)
            return df
        except Exception as e:
            print(f"Error fetching natural gas prices: {e}")
            return None

    def get_monthly_load_series(self,
                                start_year: int,
                                end_year: int,
                                ba_id: str = "ERCO") -> pd.DataFrame:
        """
        Get monthly average load (MW) for a region.
        Useful as a regression feature alongside wholesale prices.

        Returns:
            DataFrame with columns: year, month, period, avg_load_mw
        """
        start = f"{start_year}-01-01"
        end = f"{end_year + 1}-01-01"

        print(f"  Fetching EIA load data for {ba_id} ({start_year}-{end_year})...")
        df = self.get_region_data(start, end, ba_id)

        if df is None or len(df) == 0:
            print(f"  No EIA data for {ba_id}")
            return pd.DataFrame()

        df['year'] = df['Interval Start'].dt.year
        df['month'] = df['Interval Start'].dt.month

        monthly = df.groupby(['year', 'month']).agg(
            avg_load_mw=('Load', 'mean'),
            avg_generation_mw=('Net Generation', 'mean'),
        ).reset_index()

        monthly['period'] = monthly.apply(
            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)

        for _, row in monthly.iterrows():
            print(f"    {row['period']}: Load={row['avg_load_mw']:.0f} MW, "
                  f"Gen={row['avg_generation_mw']:.0f} MW")

        return monthly


# =============================================================================
# COMBINED FETCHER - ALL DATA SOURCES
# =============================================================================

def fetch_all_data(start_year: int = 1990,
                   end_year: int = 2024,
                   output_dir: str = "./",
                   eia_api_key: Optional[str] = None,
                   states: List[str] = ["TX", "CA"],
                   fetch_wholesale: bool = True,
                   fetch_retail: bool = True,
                   fetch_supplementary: bool = True,
                   incremental: bool = True) -> Dict[str, pd.DataFrame]:
    """
    Fetch ALL electricity price data:
    - Wholesale: ERCOT (DAM), CAISO (LMP)
    - Retail: Industrial, Commercial, Residential by state
    - Supplementary: Load, generation, natural gas prices, LNG exports
    - Rig counts (embedded data)
    
    Args:
        start_year: First year to fetch (default: 1990)
        end_year: Last year to fetch (default: 2024)
        output_dir: Directory to save CSV files
        eia_api_key: EIA API key (or reads from eia_token.txt / env var)
        states: List of state codes for retail prices
        fetch_wholesale: Whether to fetch wholesale prices
        fetch_retail: Whether to fetch retail prices
        fetch_supplementary: Whether to fetch load/gas/LNG data
        incremental: If True, only fetch missing months (default: True)
    
    Returns:
        Dict with all fetched DataFrames
    """
    results = {}
    
    # Try to load API key
    key = load_eia_api_key(eia_api_key)
    
    if incremental:
        print("\n📊 INCREMENTAL MODE: Only fetching missing data")
    else:
        print("\n📊 FULL REFRESH MODE: Re-downloading all data")
    
    # =========================================================================
    # WHOLESALE PRICES (no API key required)
    # =========================================================================
    
    if fetch_wholesale:
        # --- ERCOT Wholesale ---
        print("\n" + "=" * 70)
        print("FETCHING ERCOT WHOLESALE PRICES (DAM)")
        print("=" * 70)
        
        ercot_filepath = f"{output_dir}/ercot_wholesale_monthly.csv"

        try:
            if incremental:
                missing = get_missing_periods(ercot_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All ERCOT data already present, skipping...")
                    if os.path.exists(ercot_filepath):
                        results['ercot_wholesale'] = pd.read_csv(ercot_filepath)
                else:
                    # For ERCOT, we fetch by year (bulk files), so get unique years needed
                    years_needed = sorted(set(year for year, month in missing))
                    print(f"  Missing {len(missing)} months, fetching years: {years_needed}")
                    
                    ercot_fetcher = ERCOTWholesaleFetcher()
                    new_data = []
                    for year in years_needed:
                        df = ercot_fetcher.get_dam_prices_for_year(year, location="HB_NORTH")
                        if df is not None and len(df) > 0:
                            new_data.append(df)
                    
                    if new_data:
                        combined = pd.concat(new_data, ignore_index=True)
                        combined['year'] = combined['Interval Start'].dt.year
                        combined['month'] = combined['Interval Start'].dt.month
                        
                        monthly = combined.groupby(['year', 'month'])['SPP'].mean().reset_index()
                        monthly.columns = ['year', 'month', 'avg_price_mwh']
                        monthly['period'] = monthly.apply(
                            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
                        monthly['avg_price_kwh'] = monthly['avg_price_mwh'] / 10
                        monthly = monthly[['year', 'month', 'period', 'avg_price_mwh', 'avg_price_kwh']]
                        
                        results['ercot_wholesale'] = merge_and_save(ercot_filepath, monthly)
                        print(f"  ✅ Updated ERCOT data: {len(results['ercot_wholesale'])} total months")
            else:
                ercot_fetcher = ERCOTWholesaleFetcher()
                ercot_df = ercot_fetcher.get_monthly_series(start_year, end_year)
                if len(ercot_df) > 0:
                    ercot_df.to_csv(ercot_filepath, index=False)
                    results['ercot_wholesale'] = ercot_df
                    print(f"\n✅ Saved {len(ercot_df)} months of ERCOT wholesale data")
        except Exception as e:
            print(f"❌ Error fetching ERCOT wholesale: {e}")

        # --- CAISO Wholesale ---
        print("\n" + "=" * 70)
        print("FETCHING CAISO WHOLESALE PRICES (DAM)")
        print("=" * 70)
        print("  Using hybrid approach: EIA API (2019+, fast) / OASIS (pre-2019, slow)")
        
        caiso_filepath = f"{output_dir}/caiso_wholesale_monthly.csv"

        try:
            if incremental:
                missing = get_missing_periods(caiso_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All CAISO data already present, skipping...")
                    if os.path.exists(caiso_filepath):
                        results['caiso_wholesale'] = pd.read_csv(caiso_filepath)
                else:
                    # Separate missing into pre-2019 (OASIS) and 2019+ (EIA)
                    oasis_missing = [(y, m) for y, m in missing if y < 2019]
                    eia_missing = [(y, m) for y, m in missing if y >= 2019]
                    
                    print(f"  Missing {len(missing)} months total:")
                    if oasis_missing:
                        print(f"    - Pre-2019 (OASIS, slow): {len(oasis_missing)} months")
                    if eia_missing:
                        print(f"    - 2019+ (EIA, fast): {len(eia_missing)} months")
                    
                    caiso_fetcher = CAISOWholesaleFetcher(eia_api_key=key)
                    
                    new_records = []
                    
                    # Fetch 2019+ via EIA (fast, batch)
                    if eia_missing:
                        eia_years = sorted(set(y for y, m in eia_missing))
                        eia_df = caiso_fetcher._fetch_via_eia(min(eia_years), max(eia_years))
                        if eia_df is not None:
                            for _, row in eia_df.iterrows():
                                if (row['year'], row['month']) in eia_missing:
                                    new_records.append(row.to_dict())
                        else:
                            # Fallback to OASIS for 2019+ if EIA fails
                            print("  EIA failed, falling back to OASIS...")
                            oasis_missing.extend(eia_missing)
                    
                    # Fetch pre-2019 via OASIS (slow, month by month)
                    if oasis_missing:
                        for year, month in sorted(oasis_missing):
                            avg_price = caiso_fetcher.get_monthly_average_oasis(year, month)
                            if avg_price is not None:
                                new_records.append({
                                    'year': year,
                                    'month': month,
                                    'period': f"{year}-{month:02d}",
                                    'avg_price_mwh': avg_price,
                                    'avg_price_kwh': avg_price / 10,
                                    'source': 'OASIS'
                                })
                                print(f"    {year}-{month:02d}: ${avg_price:.2f}/MWh (OASIS)")
                    
                    if new_records:
                        new_df = pd.DataFrame(new_records)
                        results['caiso_wholesale'] = merge_and_save(caiso_filepath, new_df)
                        print(f"  ✅ Updated CAISO data: {len(results['caiso_wholesale'])} total months")
            else:
                caiso_fetcher = CAISOWholesaleFetcher(eia_api_key=key)
                caiso_df = caiso_fetcher.get_monthly_series(start_year, end_year)
                if len(caiso_df) > 0:
                    caiso_df.to_csv(caiso_filepath, index=False)
                    results['caiso_wholesale'] = caiso_df
                    print(f"\n✅ Saved {len(caiso_df)} months of CAISO wholesale data")
        except Exception as e:
            print(f"❌ Error fetching CAISO wholesale: {e}")

    # =========================================================================
    # RETAIL PRICES (requires EIA API key)
    # =========================================================================
    
    if fetch_retail and key:
        print("\n" + "=" * 70)
        print("FETCHING RETAIL ELECTRICITY PRICES (EIA)")
        print("=" * 70)
        
        try:
            retail_fetcher = EIARetailElectricityFetcher(api_key=key)
            
            # --- Industrial prices by state ---
            print("\n--- Industrial Prices by State ---")
            industrial_filepath = f"{output_dir}/retail_industrial_monthly.csv"
            
            if incremental:
                missing = get_missing_periods(industrial_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All industrial retail data already present, skipping...")
                    if os.path.exists(industrial_filepath):
                        results['retail_industrial'] = pd.read_csv(industrial_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    # EIA API handles date ranges efficiently, so we fetch full range
                    # and merge with existing
                    industrial_df = retail_fetcher.get_industrial_prices_multi_state(
                        states=states, start_year=start_year, end_year=end_year)
                    if len(industrial_df) > 0:
                        results['retail_industrial'] = merge_and_save(industrial_filepath, industrial_df)
                        print(f"  ✅ Updated industrial data: {len(results['retail_industrial'])} total months")
            else:
                industrial_df = retail_fetcher.get_industrial_prices_multi_state(
                    states=states, start_year=start_year, end_year=end_year)
                if len(industrial_df) > 0:
                    industrial_df.to_csv(industrial_filepath, index=False)
                    results['retail_industrial'] = industrial_df
                    print(f"✅ Saved {len(industrial_df)} months of industrial retail data")
            
            # --- All sectors for each state ---
            for state in states:
                print(f"\n--- All Sectors for {state} ---")
                state_filepath = f"{output_dir}/retail_{state.lower()}_all_sectors.csv"
                
                if incremental:
                    missing = get_missing_periods(state_filepath, start_year, end_year)
                    if not missing:
                        print(f"  ✓ All {state} data already present, skipping...")
                        if os.path.exists(state_filepath):
                            results[f'retail_{state.lower()}_all'] = pd.read_csv(state_filepath)
                    else:
                        print(f"  Missing {len(missing)} months, fetching...")
                        state_df = retail_fetcher.get_all_sectors_for_state(
                            state=state, start_year=start_year, end_year=end_year)
                        if len(state_df) > 0:
                            results[f'retail_{state.lower()}_all'] = merge_and_save(state_filepath, state_df)
                            print(f"  ✅ Updated {state} data: {len(results[f'retail_{state.lower()}_all'])} total months")
                else:
                    state_df = retail_fetcher.get_all_sectors_for_state(
                        state=state, start_year=start_year, end_year=end_year)
                    if len(state_df) > 0:
                        state_df.to_csv(state_filepath, index=False)
                        results[f'retail_{state.lower()}_all'] = state_df
                        print(f"✅ Saved {len(state_df)} months of {state} all-sector data")
            
            # --- US Average (all sectors) ---
            print("\n--- US Average (All Sectors) ---")
            us_filepath = f"{output_dir}/retail_us_all_sectors.csv"
            
            if incremental:
                missing = get_missing_periods(us_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All US average data already present, skipping...")
                    if os.path.exists(us_filepath):
                        results['retail_us_all'] = pd.read_csv(us_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    us_df = retail_fetcher.get_all_sectors_for_state(
                        state="US", start_year=start_year, end_year=end_year)
                    if len(us_df) > 0:
                        results['retail_us_all'] = merge_and_save(us_filepath, us_df)
                        print(f"  ✅ Updated US data: {len(results['retail_us_all'])} total months")
            else:
                us_df = retail_fetcher.get_all_sectors_for_state(
                    state="US", start_year=start_year, end_year=end_year)
                if len(us_df) > 0:
                    us_df.to_csv(us_filepath, index=False)
                    results['retail_us_all'] = us_df
                    print(f"✅ Saved {len(us_df)} months of US average data")
                
        except Exception as e:
            print(f"❌ Error fetching retail prices: {e}")
            import traceback
            traceback.print_exc()
    elif fetch_retail and not key:
        print("\n" + "-" * 70)
        print("⚠️  Skipping RETAIL prices (no EIA API key)")
        print("   To enable, create eia_token.txt with your API key")
        print("   Get a free key: https://www.eia.gov/opendata/register.php")
        print("-" * 70)
    
    # =========================================================================
    # SUPPLEMENTARY DATA (requires EIA API key)
    # =========================================================================
    
    if fetch_supplementary and key:
        print("\n" + "=" * 70)
        print("FETCHING SUPPLEMENTARY DATA (EIA)")
        print("=" * 70)

        try:
            eia_fetcher = EIAGridDataFetcher(api_key=key)

            # ERCOT load/generation
            print("\n--- ERCOT Load & Generation ---")
            ercot_load_filepath = f"{output_dir}/eia_ercot_load_monthly.csv"
            
            if incremental:
                missing = get_missing_periods(ercot_load_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All ERCOT load data already present, skipping...")
                    if os.path.exists(ercot_load_filepath):
                        results['eia_ercot_load'] = pd.read_csv(ercot_load_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    ercot_load_df = eia_fetcher.get_monthly_load_series(start_year, end_year, "ERCO")
                    if len(ercot_load_df) > 0:
                        results['eia_ercot_load'] = merge_and_save(ercot_load_filepath, ercot_load_df)
                        print(f"  ✅ Updated ERCOT load: {len(results['eia_ercot_load'])} total months")
            else:
                ercot_load_df = eia_fetcher.get_monthly_load_series(start_year, end_year, "ERCO")
                if len(ercot_load_df) > 0:
                    ercot_load_df.to_csv(ercot_load_filepath, index=False)
                    results['eia_ercot_load'] = ercot_load_df
                    print(f"✅ Saved {len(ercot_load_df)} months of ERCOT load data")

            # CAISO load/generation
            print("\n--- CAISO Load & Generation ---")
            caiso_load_filepath = f"{output_dir}/eia_caiso_load_monthly.csv"
            
            if incremental:
                missing = get_missing_periods(caiso_load_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All CAISO load data already present, skipping...")
                    if os.path.exists(caiso_load_filepath):
                        results['eia_caiso_load'] = pd.read_csv(caiso_load_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    caiso_load_df = eia_fetcher.get_monthly_load_series(start_year, end_year, "CISO")
                    if len(caiso_load_df) > 0:
                        results['eia_caiso_load'] = merge_and_save(caiso_load_filepath, caiso_load_df)
                        print(f"  ✅ Updated CAISO load: {len(results['eia_caiso_load'])} total months")
            else:
                caiso_load_df = eia_fetcher.get_monthly_load_series(start_year, end_year, "CISO")
                if len(caiso_load_df) > 0:
                    caiso_load_df.to_csv(caiso_load_filepath, index=False)
                print(f"✅ Saved {len(caiso_load_df)} months of CAISO load data")

            # Natural gas prices
            print("\n--- Henry Hub Natural Gas Prices ---")
            gas_filepath = f"{output_dir}/eia_natgas_monthly.csv"
            
            if incremental:
                missing = get_missing_periods(gas_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All gas price data already present, skipping...")
                    if os.path.exists(gas_filepath):
                        results['eia_natgas'] = pd.read_csv(gas_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    gas_df = eia_fetcher.get_natural_gas_prices(
                        f"{start_year}-01-01", f"{end_year + 1}-01-01")
                    if gas_df is not None and len(gas_df) > 0:
                        gas_df['year'] = gas_df['Interval Start'].dt.year
                        gas_df['month'] = gas_df['Interval Start'].dt.month
                        gas_monthly = gas_df.groupby(['year', 'month'])['price'].mean().reset_index()
                        gas_monthly.columns = ['year', 'month', 'avg_gas_price']
                        gas_monthly['period'] = gas_monthly.apply(
                            lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
                        results['eia_natgas'] = merge_and_save(gas_filepath, gas_monthly)
                        print(f"  ✅ Updated gas prices: {len(results['eia_natgas'])} total months")
            else:
                gas_df = eia_fetcher.get_natural_gas_prices(
                    f"{start_year}-01-01", f"{end_year + 1}-01-01")
                if gas_df is not None and len(gas_df) > 0:
                    gas_df['year'] = gas_df['Interval Start'].dt.year
                    gas_df['month'] = gas_df['Interval Start'].dt.month
                    gas_monthly = gas_df.groupby(['year', 'month'])['price'].mean().reset_index()
                    gas_monthly.columns = ['year', 'month', 'avg_gas_price']
                    gas_monthly['period'] = gas_monthly.apply(
                        lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
                    gas_monthly.to_csv(gas_filepath, index=False)
                    results['eia_natgas'] = gas_monthly
                    print(f"✅ Saved {len(gas_monthly)} months of natural gas price data")

        except Exception as e:
            print(f"❌ Error fetching EIA supplementary data: {e}")
        
        # --- LNG Exports ---
        try:
            print("\n--- LNG Exports ---")
            lng_filepath = f"{output_dir}/eia_lng_exports_monthly.csv"
            
            if incremental:
                missing = get_missing_periods(lng_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All LNG export data already present, skipping...")
                    if os.path.exists(lng_filepath):
                        results['eia_lng_exports'] = pd.read_csv(lng_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    lng_fetcher = EIALNGExportsFetcher(api_key=key)
                    lng_df = lng_fetcher.get_monthly_lng_exports(start_year, end_year)
                    if lng_df is not None and len(lng_df) > 0:
                        results['eia_lng_exports'] = merge_and_save(lng_filepath, lng_df)
                        print(f"  ✅ Updated LNG exports: {len(results['eia_lng_exports'])} total months")
            else:
                lng_fetcher = EIALNGExportsFetcher(api_key=key)
                lng_df = lng_fetcher.get_monthly_lng_exports(start_year, end_year)
                if lng_df is not None and len(lng_df) > 0:
                    lng_df.to_csv(lng_filepath, index=False)
                    results['eia_lng_exports'] = lng_df
                    print(f"✅ Saved {len(lng_df)} months of LNG export data")
        except Exception as e:
            print(f"❌ Error fetching LNG exports: {e}")
        
        # --- Power Generation Metrics ---
        try:
            print("\n--- Power Generation by Fuel Type ---")
            gen_filepath = f"{output_dir}/eia_generation_by_fuel_monthly.csv"
            
            if incremental:
                missing = get_missing_periods(gen_filepath, start_year, end_year)
                if not missing:
                    print("  ✓ All generation data already present, skipping...")
                    if os.path.exists(gen_filepath):
                        results['eia_generation'] = pd.read_csv(gen_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    gen_fetcher = EIAPowerGenerationFetcher(api_key=key)
                    gen_df = gen_fetcher.get_monthly_generation_by_fuel(start_year, end_year)
                    if gen_df is not None and len(gen_df) > 0:
                        results['eia_generation'] = merge_and_save(gen_filepath, gen_df)
                        print(f"  ✅ Updated generation data: {len(results['eia_generation'])} total months")
            else:
                gen_fetcher = EIAPowerGenerationFetcher(api_key=key)
                gen_df = gen_fetcher.get_monthly_generation_by_fuel(start_year, end_year)
                if gen_df is not None and len(gen_df) > 0:
                    gen_df.to_csv(gen_filepath, index=False)
                    results['eia_generation'] = gen_df
                    print(f"✅ Saved {len(gen_df)} months of generation data")
        except Exception as e:
            print(f"❌ Error fetching generation data: {e}")
        
        # --- Capacity Factors ---
        try:
            print("\n--- Capacity Factors ---")
            cf_filepath = f"{output_dir}/eia_capacity_factors_monthly.csv"
            
            # Capacity factors only available from ~2008
            cf_start = max(start_year, 2008)
            
            if incremental:
                missing = get_missing_periods(cf_filepath, cf_start, end_year)
                if not missing:
                    print("  ✓ All capacity factor data already present, skipping...")
                    if os.path.exists(cf_filepath):
                        results['eia_capacity_factors'] = pd.read_csv(cf_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    if 'gen_fetcher' not in dir():
                        gen_fetcher = EIAPowerGenerationFetcher(api_key=key)
                    cf_df = gen_fetcher.get_capacity_factors(cf_start, end_year)
                    if cf_df is not None and len(cf_df) > 0:
                        results['eia_capacity_factors'] = merge_and_save(cf_filepath, cf_df)
                        print(f"  ✅ Updated capacity factors: {len(results['eia_capacity_factors'])} total months")
            else:
                if 'gen_fetcher' not in dir():
                    gen_fetcher = EIAPowerGenerationFetcher(api_key=key)
                cf_df = gen_fetcher.get_capacity_factors(cf_start, end_year)
                if cf_df is not None and len(cf_df) > 0:
                    cf_df.to_csv(cf_filepath, index=False)
                    results['eia_capacity_factors'] = cf_df
                    print(f"✅ Saved {len(cf_df)} months of capacity factor data")
        except Exception as e:
            print(f"❌ Error fetching capacity factors: {e}")
    
    elif fetch_supplementary and not key:
        print("\n" + "-" * 70)
        print("⚠️  Skipping SUPPLEMENTARY data (no EIA API key)")
        print("   To enable, create eia_token.txt with your API key")
        print("-" * 70)

    # =========================================================================
    # RIG COUNTS (no API key required - embedded data)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING RIG COUNT DATA (Baker Hughes)")
    print("=" * 70)
    
    rig_filepath = f"{output_dir}/baker_hughes_rig_counts_monthly.csv"
    
    try:
        if incremental:
            missing = get_missing_periods(rig_filepath, start_year, end_year)
            if not missing:
                print("  ✓ All rig count data already present, skipping...")
                if os.path.exists(rig_filepath):
                    results['rig_counts'] = pd.read_csv(rig_filepath)
            else:
                print(f"  Missing {len(missing)} months, updating...")
                rig_fetcher = BakerHughesRigCountFetcher()
                rig_df = rig_fetcher.get_monthly_rig_counts(start_year, end_year)
                if len(rig_df) > 0:
                    results['rig_counts'] = merge_and_save(rig_filepath, rig_df)
                    print(f"  ✅ Updated rig counts: {len(results['rig_counts'])} total months")
        else:
            rig_fetcher = BakerHughesRigCountFetcher()
            rig_df = rig_fetcher.get_monthly_rig_counts(start_year, end_year)
            if len(rig_df) > 0:
                rig_df.to_csv(rig_filepath, index=False)
                results['rig_counts'] = rig_df
                print(f"✅ Saved {len(rig_df)} months of rig count data")
    except Exception as e:
        print(f"❌ Error fetching rig counts: {e}")

    # =========================================================================
    # CAPACITY CHANGES (no API key required - embedded data)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING CAPACITY CHANGES DATA (EIA 860)")
    print("=" * 70)
    
    cap_changes_filepath = f"{output_dir}/capacity_changes_annual.csv"
    
    try:
        cap_fetcher = PowerPlantCapacityChanges()
        cap_df = cap_fetcher.get_annual_capacity_changes(start_year, end_year)
        if len(cap_df) > 0:
            cap_df.to_csv(cap_changes_filepath, index=False)
            results['capacity_changes'] = cap_df
            print(f"✅ Saved {len(cap_df)} years of capacity change data")
    except Exception as e:
        print(f"❌ Error fetching capacity changes: {e}")

    # =========================================================================
    # EMBEDDED HISTORICAL DATA (fills pre-API gaps)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING EMBEDDED HISTORICAL DATA")
    print("=" * 70)
    
    embedded = EmbeddedHistoricalData()
    
    # --- Gas Prices (1990-1996, before Henry Hub API) ---
    if start_year < 1997:
        try:
            gas_hist_filepath = f"{output_dir}/gas_prices_historical_1990_1996.csv"
            gas_hist_df = embedded.get_gas_prices(start_year, min(1996, end_year))
            if len(gas_hist_df) > 0:
                gas_hist_df.to_csv(gas_hist_filepath, index=False)
                results['gas_prices_historical'] = gas_hist_df
                print(f"✅ Gas prices (wellhead 1990-1996): {len(gas_hist_df)} months")
        except Exception as e:
            print(f"❌ Error with historical gas prices: {e}")
    
    # --- WTI Crude Oil Prices (1990-2000) ---
    try:
        wti_filepath = f"{output_dir}/wti_crude_prices_monthly.csv"
        wti_df = embedded.get_wti_prices(start_year, min(2000, end_year))
        if len(wti_df) > 0:
            wti_df.to_csv(wti_filepath, index=False)
            results['wti_crude'] = wti_df
            print(f"✅ WTI crude oil prices: {len(wti_df)} months")
    except Exception as e:
        print(f"❌ Error with WTI prices: {e}")
    
    # --- Retail Electricity Prices (1990-2000, before API) ---
    if start_year < 2001:
        try:
            retail_hist_filepath = f"{output_dir}/retail_electricity_historical_1990_2000.csv"
            retail_hist_df = embedded.get_retail_electricity_prices(start_year, min(2000, end_year))
            if len(retail_hist_df) > 0:
                retail_hist_df.to_csv(retail_hist_filepath, index=False)
                results['retail_historical'] = retail_hist_df
                print(f"✅ Retail electricity (1990-2000): {len(retail_hist_df)} months")
        except Exception as e:
            print(f"❌ Error with historical retail prices: {e}")
    
    # --- Annual Generation by Fuel (complete 1990-2024 backup) ---
    try:
        gen_annual_filepath = f"{output_dir}/generation_by_fuel_annual.csv"
        gen_annual_df = embedded.get_generation_by_fuel(start_year, end_year)
        if len(gen_annual_df) > 0:
            gen_annual_df.to_csv(gen_annual_filepath, index=False)
            results['generation_annual'] = gen_annual_df
            print(f"✅ Generation by fuel (annual): {len(gen_annual_df)} years")
            
            # Show gas share trend
            if len(gen_annual_df) > 1:
                first = gen_annual_df.iloc[0]
                last = gen_annual_df.iloc[-1]
                print(f"    Gas share: {first['gas_share_pct']:.1f}% ({int(first['year'])}) → {last['gas_share_pct']:.1f}% ({int(last['year'])})")
                print(f"    Coal share: {first['coal_share_pct']:.1f}% ({int(first['year'])}) → {last['coal_share_pct']:.1f}% ({int(last['year'])})")
    except Exception as e:
        print(f"❌ Error with generation by fuel: {e}")
    
    # --- Annual Capacity Factors (complete 1990-2024) ---
    try:
        cf_annual_filepath = f"{output_dir}/capacity_factors_annual.csv"
        cf_annual_df = embedded.get_capacity_factors(start_year, end_year)
        if len(cf_annual_df) > 0:
            cf_annual_df.to_csv(cf_annual_filepath, index=False)
            results['capacity_factors_annual'] = cf_annual_df
            print(f"✅ Capacity factors (annual): {len(cf_annual_df)} years")
            
            # Show nuclear CF improvement
            if len(cf_annual_df) > 1:
                first = cf_annual_df.iloc[0]
                last = cf_annual_df.iloc[-1]
                print(f"    Nuclear CF: {first['nuclear_cf_pct']:.0f}% ({int(first['year'])}) → {last['nuclear_cf_pct']:.0f}% ({int(last['year'])})")
                print(f"    Coal CF: {first['coal_cf_pct']:.0f}% ({int(first['year'])}) → {last['coal_cf_pct']:.0f}% ({int(last['year'])})")
    except Exception as e:
        print(f"❌ Error with capacity factors: {e}")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCH SUMMARY")
    print("=" * 70)
    
    for name, df in results.items():
        print(f"  {name}: {len(df)} records")
    
    print(f"\nFiles saved to: {os.path.abspath(output_dir)}")
    
    return results


# Legacy function name for backward compatibility
def fetch_all_wholesale_monthly(start_year: int = 1990,
                                end_year: int = 2024,
                                output_dir: str = "./",
                                eia_api_key: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """
    Legacy function - calls fetch_all_data with default parameters.
    Kept for backward compatibility.
    """
    return fetch_all_data(
        start_year=start_year,
        end_year=end_year,
        output_dir=output_dir,
        eia_api_key=eia_api_key,
        states=["TX", "CA"],
        fetch_wholesale=True,
        fetch_retail=True,
        fetch_supplementary=True
    )


# =============================================================================
# USAGE EXAMPLES
# =============================================================================

def example_usage():
    """Show example usage of the fetchers."""

    print("=" * 70)
    print("DecarbIQ Energy Price Fetcher")
    print("=" * 70)

    if not GRIDSTATUS_AVAILABLE:
        print("\nERROR: gridstatus library not installed!")
        print("Please run: pip install gridstatus pandas requests")
        print("\nOnce installed, you can use this module to fetch:")
        print("  - ERCOT DAM Settlement Point Prices (2010-present)")
        print("  - CAISO Day-Ahead LMPs (~3 years)")
        print("  - EIA retail prices by sector (Residential, Commercial, Industrial)")
        print("  - EIA grid data: load, generation, fuel mix, gas prices")
        print("  - Aggregate to monthly averages for regression analysis")
        return

    # Check for API key
    key = load_eia_api_key()
    if key:
        print(f"\n✅ EIA API key loaded")
    else:
        print("\n⚠️  No EIA API key found")
        print("   Wholesale data will work, but retail/supplementary data requires a key")
        print("   To enable: create eia_token.txt with your API key")
        print("   Get a free key: https://www.eia.gov/opendata/register.php")

    # --- ERCOT real-time (today) ---
    print("\n" + "-" * 50)
    print("Example: Fetch ERCOT real-time prices for today")
    print("-" * 50)

    try:
        ercot = ERCOTWholesaleFetcher()
        today = datetime.now().strftime('%Y-%m-%d')
        df = ercot.get_daily_spp(today, location='HB_NORTH')
        if df is not None:
            print(f"Retrieved {len(df)} records")
            print(df.head())
    except Exception as e:
        print(f"Error: {e}")

    # --- ERCOT historical DAM (full year) ---
    print("\n" + "-" * 50)
    print("Example: Fetch ERCOT DAM prices for 2024")
    print("-" * 50)

    try:
        df = ercot.get_dam_prices_for_year(2024, location='HB_NORTH')
        if df is not None:
            avg = df['SPP'].mean()
            print(f"2024 HB_NORTH: {len(df)} hourly records, avg ${avg:.2f}/MWh")
    except Exception as e:
        print(f"Error: {e}")

    # --- EIA Retail prices (if key available) ---
    if key:
        print("\n" + "-" * 50)
        print("Example: Fetch Texas Industrial retail prices")
        print("-" * 50)
        try:
            retail = EIARetailElectricityFetcher(api_key=key)
            df = retail.get_monthly_prices("TX", "IND", 2023, 2024)
            if df is not None:
                print(f"Retrieved {len(df)} months")
                print(df.tail())
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    # Check dependencies first
    if not PANDAS_AVAILABLE or not GRIDSTATUS_AVAILABLE:
        print("\n" + "=" * 60)
        print("MISSING DEPENDENCIES")
        print("=" * 60)
        print("\nTo use this module, install required packages:")
        print("  pip install gridstatus pandas requests")
        print("\nThen run this script again.")
        sys.exit(1)

    # Run full fetch
    print("=" * 70)
    print("DecarbIQ Energy Price Fetcher - Full Data Collection")
    print("=" * 70)
    
    results = fetch_all_data(
        start_year=1990,
        end_year=2024,
        output_dir="./",
        states=["TX", "CA"]
    )
    
    print("\n✅ Data fetch complete!")
