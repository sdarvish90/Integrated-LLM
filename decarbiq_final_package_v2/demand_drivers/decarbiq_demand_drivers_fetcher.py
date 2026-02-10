#!/usr/bin/env python3
"""
DecarbIQ Natural Gas Demand Drivers Fetcher
============================================

Tracks the major demand-side factors affecting US natural gas prices and consumption:

1. CONSUMPTION BY SECTOR (monthly, Bcf/d):
   - Power Sector: Largest and fastest growing (~40% of consumption)
   - Industrial: Manufacturing, chemical feedstock (~32%)
   - Residential: Heating demand, weather-driven (~14%)
   - Commercial: Heating, cooling (~10%)
   - Transportation/Other: Vehicle fuel, pipeline fuel

2. EXPORTS (monthly, Bcf/d):
   - LNG Exports: To Europe, Asia, LatAm (largest growth driver)
   - Pipeline to Mexico: Steady growth for power generation
   - Pipeline to Canada: Small volumes

3. WEATHER INDICATORS:
   - Heating Degree Days (HDD): Drives winter residential/commercial demand
   - Cooling Degree Days (CDD): Drives summer power sector demand

4. SUPPLY-DEMAND BALANCE:
   - Production vs Consumption
   - Net Exports
   - Storage levels

Data sources:
- EIA Natural Gas Monthly (API v2)
- EIA Short-Term Energy Outlook (STEO)
- NOAA Climate Data (HDD/CDD)

Author: DecarbIQ
Version: 1.0
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

# Check for required libraries
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("Warning: pandas not available. Run: pip install pandas")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("Warning: requests not available. Run: pip install requests")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def load_eia_api_key(provided_key: Optional[str] = None) -> Optional[str]:
    """Load EIA API key from various sources."""
    if provided_key:
        return provided_key
    
    # Try environment variable
    env_key = os.environ.get('EIA_API_KEY')
    if env_key:
        return env_key
    
    # Try eia_token.txt in current directory or script directory
    for path in ['./eia_token.txt', os.path.join(os.path.dirname(__file__), 'eia_token.txt')]:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    key = f.read().strip()
                    if key:
                        return key
            except Exception:
                pass
    
    return None


def get_existing_periods(filepath: str, period_col: str = 'period') -> set:
    """Get set of periods already in a CSV file."""
    if not os.path.exists(filepath):
        return set()
    
    try:
        df = pd.read_csv(filepath)
        if period_col in df.columns:
            return set(df[period_col].astype(str).tolist())
    except Exception:
        pass
    
    return set()


def get_missing_periods(filepath: str, start_year: int, end_year: int) -> List[Tuple[int, int]]:
    """Calculate which (year, month) periods are missing from a file."""
    existing = get_existing_periods(filepath)
    
    missing = []
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Don't request future months
            if year == current_year and month > current_month:
                continue
            if year > current_year:
                continue
            
            period = f"{year}-{month:02d}"
            if period not in existing:
                missing.append((year, month))
    
    return missing


def merge_and_save(existing_filepath: str, new_df: pd.DataFrame, 
                   period_col: str = 'period') -> pd.DataFrame:
    """Merge new data with existing CSV and save."""
    if os.path.exists(existing_filepath):
        try:
            existing_df = pd.read_csv(existing_filepath)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=[period_col], keep='last')
        except Exception:
            combined = new_df
    else:
        combined = new_df
    
    # Sort by year, month if those columns exist
    if 'year' in combined.columns and 'month' in combined.columns:
        combined = combined.sort_values(['year', 'month']).reset_index(drop=True)
    
    combined.to_csv(existing_filepath, index=False)
    return combined


# =============================================================================
# EIA NATURAL GAS CONSUMPTION BY SECTOR
# =============================================================================

class EIAGasConsumptionFetcher:
    """
    Fetches natural gas consumption by sector from EIA API.
    
    Uses the total-energy endpoint which has reliable monthly consumption data.
    Series codes (from Monthly Energy Review Table 4.3):
    - NGRCPUS: Residential
    - NGCCPUS: Commercial  
    - NGICPUS: Industrial
    - NGEIPUS: Electric Power
    - NGTCPUS: Total consumption
    
    Units: Trillion Btu -> converted to Bcf (1 Bcf ≈ 1.028 Trillion Btu)
    """
    
    BASE_URL = "https://api.eia.gov/v2/total-energy/data/"
    
    # Series codes for consumption by sector
    SECTOR_SERIES = {
        'residential': 'NGRCPUS',
        'commercial': 'NGCCPUS', 
        'industrial': 'NGICPUS',
        'electric_power': 'NGEIPUS',
        'total': 'NGTCPUS',
    }
    
    # Conversion: 1 Bcf natural gas ≈ 1.028 Trillion Btu
    TBTU_TO_BCF = 1.0 / 1.028
    
    def __init__(self, api_key: Optional[str] = None):
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library required")
        
        self.api_key = load_eia_api_key(api_key)
        if not self.api_key:
            raise ValueError("EIA API key required")
    
    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """Make API request."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  API request error: {e}")
            return None
    
    def get_monthly_consumption_by_sector(self,
                                          start_year: int = 1973,
                                          end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly natural gas consumption by sector.
        
        Returns:
            DataFrame with columns: year, month, period,
            residential_bcf, commercial_bcf, industrial_bcf, 
            electric_power_bcf, total_bcf, and _bcfd versions
        """
        print(f"  Fetching consumption by sector ({start_year}-{end_year})...")
        
        all_data = {}
        
        for sector, series_id in self.SECTOR_SERIES.items():
            params = {
                'frequency': 'monthly',
                'data[0]': 'value',
                'facets[msn][]': series_id,
                'start': f'{start_year}-01',
                'end': f'{end_year}-12',
                'sort[0][column]': 'period',
                'sort[0][direction]': 'asc',
                'length': 5000
            }
            
            result = self._make_request(self.BASE_URL, params)
            
            if result and 'response' in result and 'data' in result['response']:
                for item in result['response']['data']:
                    period = item.get('period', '')
                    value = item.get('value')
                    
                    if period and value is not None:
                        try:
                            if period not in all_data:
                                all_data[period] = {}
                            # Data is in Trillion Btu, convert to Bcf
                            all_data[period][sector] = float(value) * self.TBTU_TO_BCF
                        except (ValueError, TypeError):
                            pass
            else:
                print(f"    Warning: No data returned for {sector}")
        
        if not all_data:
            print("  No consumption data returned")
            return None
        
        # Build DataFrame
        records = []
        for period in sorted(all_data.keys()):
            year = int(period[:4])
            month = int(period[5:7])
            
            # Days in month for Bcf/d calculation
            if month in [1, 3, 5, 7, 8, 10, 12]:
                days = 31
            elif month in [4, 6, 9, 11]:
                days = 30
            else:
                days = 28 if year % 4 != 0 else 29
            
            data = all_data[period]
            
            record = {
                'year': year,
                'month': month,
                'period': period,
                # Bcf (monthly total)
                'residential_bcf': data.get('residential', 0),
                'commercial_bcf': data.get('commercial', 0),
                'industrial_bcf': data.get('industrial', 0),
                'electric_power_bcf': data.get('electric_power', 0),
                'total_bcf': data.get('total', 0),
            }
            
            # Calculate Bcf/d (daily average)
            record['residential_bcfd'] = record['residential_bcf'] / days
            record['commercial_bcfd'] = record['commercial_bcf'] / days
            record['industrial_bcfd'] = record['industrial_bcf'] / days
            record['electric_power_bcfd'] = record['electric_power_bcf'] / days
            record['total_bcfd'] = record['total_bcf'] / days
            
            # Calculate sector shares (%)
            total = record['total_bcf'] if record['total_bcf'] > 0 else 1
            record['electric_power_share_pct'] = (record['electric_power_bcf'] / total) * 100
            record['industrial_share_pct'] = (record['industrial_bcf'] / total) * 100
            record['residential_share_pct'] = (record['residential_bcf'] / total) * 100
            record['commercial_share_pct'] = (record['commercial_bcf'] / total) * 100
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of consumption data")
        
        return df


# =============================================================================
# EIA NATURAL GAS EXPORTS (LNG + PIPELINE)
# =============================================================================

class EIAGasExportsFetcher:
    """
    Fetches natural gas export data from EIA API.
    
    Export types:
    - LNG exports (waterborne, vessel)
    - Pipeline exports to Mexico
    - Pipeline exports to Canada
    
    Units: MMcf -> Bcf, Bcf/d
    """
    
    def __init__(self, api_key: Optional[str] = None):
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library required")
        
        self.api_key = load_eia_api_key(api_key)
        if not self.api_key:
            raise ValueError("EIA API key required")
    
    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """Make API request."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  API request error: {e}")
            return None
    
    def get_monthly_exports(self,
                            start_year: int = 2010,
                            end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly natural gas exports by type.
        
        Returns:
            DataFrame with columns: year, month, period,
            lng_exports_bcf, pipeline_mexico_bcf, pipeline_canada_bcf,
            total_exports_bcf, and _bcfd versions
        """
        print(f"  Fetching exports ({start_year}-{end_year})...")
        
        all_data = {}
        
        # LNG exports
        url_lng = "https://api.eia.gov/v2/natural-gas/move/expc/data/"
        params_lng = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'facets[process][]': 'EXL',  # LNG exports
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        result = self._make_request(url_lng, params_lng)
        if result and 'response' in result and 'data' in result['response']:
            for item in result['response']['data']:
                period = item.get('period', '')
                value = item.get('value')
                if period and value is not None:
                    if period not in all_data:
                        all_data[period] = {}
                    all_data[period]['lng'] = float(value) / 1000  # MMcf to Bcf
        
        # Pipeline exports to Mexico
        url_pipe = "https://api.eia.gov/v2/natural-gas/move/poe2/data/"
        params_mexico = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'facets[duession][]': 'NMX',  # To Mexico
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        result = self._make_request(url_pipe, params_mexico)
        if result and 'response' in result and 'data' in result['response']:
            for item in result['response']['data']:
                period = item.get('period', '')
                value = item.get('value')
                if period and value is not None:
                    if period not in all_data:
                        all_data[period] = {}
                    all_data[period]['mexico'] = float(value) / 1000
        
        # Pipeline exports to Canada
        params_canada = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'facets[duession][]': 'NCA',  # To Canada
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        result = self._make_request(url_pipe, params_canada)
        if result and 'response' in result and 'data' in result['response']:
            for item in result['response']['data']:
                period = item.get('period', '')
                value = item.get('value')
                if period and value is not None:
                    if period not in all_data:
                        all_data[period] = {}
                    all_data[period]['canada'] = float(value) / 1000
        
        if not all_data:
            print("  No export data returned")
            return None
        
        # Build DataFrame
        records = []
        for period in sorted(all_data.keys()):
            year = int(period[:4])
            month = int(period[5:7])
            
            # Days in month
            if month in [1, 3, 5, 7, 8, 10, 12]:
                days = 31
            elif month in [4, 6, 9, 11]:
                days = 30
            else:
                days = 28 if year % 4 != 0 else 29
            
            data = all_data[period]
            
            lng = data.get('lng', 0)
            mexico = data.get('mexico', 0)
            canada = data.get('canada', 0)
            total = lng + mexico + canada
            
            record = {
                'year': year,
                'month': month,
                'period': period,
                # Bcf (monthly)
                'lng_exports_bcf': lng,
                'pipeline_mexico_bcf': mexico,
                'pipeline_canada_bcf': canada,
                'total_exports_bcf': total,
                # Bcf/d
                'lng_exports_bcfd': lng / days,
                'pipeline_mexico_bcfd': mexico / days,
                'pipeline_canada_bcfd': canada / days,
                'total_exports_bcfd': total / days,
                # Shares
                'lng_share_pct': (lng / total * 100) if total > 0 else 0,
                'mexico_share_pct': (mexico / total * 100) if total > 0 else 0,
            }
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of export data")
        
        return df


# =============================================================================
# EIA NATURAL GAS IMPORTS
# =============================================================================

class EIAGasImportsFetcher:
    """
    Fetches natural gas import data from EIA API.
    
    Import types:
    - Pipeline imports from Canada (primary source)
    - LNG imports (small, mainly to New England)
    """
    
    def __init__(self, api_key: Optional[str] = None):
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library required")
        
        self.api_key = load_eia_api_key(api_key)
        if not self.api_key:
            raise ValueError("EIA API key required")
    
    def _make_request(self, url: str, params: Dict) -> Optional[Dict]:
        """Make API request."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"  API request error: {e}")
            return None
    
    def get_monthly_imports(self,
                            start_year: int = 1990,
                            end_year: int = 2024) -> Optional[pd.DataFrame]:
        """
        Get monthly natural gas imports.
        
        Returns:
            DataFrame with columns: year, month, period,
            pipeline_canada_bcf, lng_imports_bcf, total_imports_bcf
        """
        print(f"  Fetching imports ({start_year}-{end_year})...")
        
        all_data = {}
        
        # Pipeline imports (total)
        url = "https://api.eia.gov/v2/natural-gas/move/impc/data/"
        params = {
            'frequency': 'monthly',
            'data[0]': 'value',
            'start': f'{start_year}-01',
            'end': f'{end_year}-12',
            'sort[0][column]': 'period',
            'sort[0][direction]': 'asc',
            'length': 5000
        }
        
        # Try pipeline imports
        params_pipe = params.copy()
        params_pipe['facets[process][]'] = 'IRP'  # Pipeline imports
        
        result = self._make_request(url, params_pipe)
        if result and 'response' in result and 'data' in result['response']:
            for item in result['response']['data']:
                period = item.get('period', '')
                value = item.get('value')
                if period and value is not None:
                    if period not in all_data:
                        all_data[period] = {}
                    all_data[period]['pipeline'] = float(value) / 1000
        
        # LNG imports
        params_lng = params.copy()
        params_lng['facets[process][]'] = 'IML'  # LNG imports
        
        result = self._make_request(url, params_lng)
        if result and 'response' in result and 'data' in result['response']:
            for item in result['response']['data']:
                period = item.get('period', '')
                value = item.get('value')
                if period and value is not None:
                    if period not in all_data:
                        all_data[period] = {}
                    all_data[period]['lng'] = float(value) / 1000
        
        if not all_data:
            print("  No import data returned")
            return None
        
        # Build DataFrame
        records = []
        for period in sorted(all_data.keys()):
            year = int(period[:4])
            month = int(period[5:7])
            
            if month in [1, 3, 5, 7, 8, 10, 12]:
                days = 31
            elif month in [4, 6, 9, 11]:
                days = 30
            else:
                days = 28 if year % 4 != 0 else 29
            
            data = all_data[period]
            pipeline = data.get('pipeline', 0)
            lng = data.get('lng', 0)
            total = pipeline + lng
            
            record = {
                'year': year,
                'month': month,
                'period': period,
                'pipeline_imports_bcf': pipeline,
                'lng_imports_bcf': lng,
                'total_imports_bcf': total,
                'pipeline_imports_bcfd': pipeline / days,
                'lng_imports_bcfd': lng / days,
                'total_imports_bcfd': total / days,
            }
            
            records.append(record)
        
        df = pd.DataFrame(records)
        df = df.sort_values(['year', 'month']).reset_index(drop=True)
        
        print(f"    Retrieved {len(df)} months of import data")
        
        return df


# =============================================================================
# EMBEDDED HISTORICAL CONSUMPTION DATA
# =============================================================================

class EmbeddedConsumptionData:
    """
    Embedded historical natural gas consumption by sector (1990-2000).
    
    Pre-API data from EIA Natural Gas Annual archives.
    """
    
    # Annual consumption by sector (Bcf)
    # Source: EIA Natural Gas Annual Historical
    ANNUAL_CONSUMPTION = {
        # Year: {sector: Bcf}
        1990: {'residential': 4391, 'commercial': 2623, 'industrial': 7182, 'electric_power': 2882, 'total': 18716},
        1991: {'residential': 4556, 'commercial': 2729, 'industrial': 7232, 'electric_power': 2939, 'total': 19035},
        1992: {'residential': 4690, 'commercial': 2803, 'industrial': 7527, 'electric_power': 2972, 'total': 19544},
        1993: {'residential': 4956, 'commercial': 2862, 'industrial': 7712, 'electric_power': 2948, 'total': 20279},
        1994: {'residential': 4848, 'commercial': 2895, 'industrial': 7923, 'electric_power': 3073, 'total': 20708},
        1995: {'residential': 4850, 'commercial': 3031, 'industrial': 8203, 'electric_power': 3344, 'total': 21620},
        1996: {'residential': 5241, 'commercial': 3159, 'industrial': 8325, 'electric_power': 3152, 'total': 22017},
        1997: {'residential': 4984, 'commercial': 3219, 'industrial': 8499, 'electric_power': 3448, 'total': 22254},
        1998: {'residential': 4520, 'commercial': 3076, 'industrial': 8521, 'electric_power': 3807, 'total': 21965},
        1999: {'residential': 4725, 'commercial': 3090, 'industrial': 8341, 'electric_power': 4043, 'total': 21403},
        2000: {'residential': 5007, 'commercial': 3265, 'industrial': 8476, 'electric_power': 5076, 'total': 23333},
    }
    
    # Annual exports (Bcf)
    ANNUAL_EXPORTS = {
        1990: {'pipeline_mexico': 16, 'pipeline_canada': 2, 'lng': 0, 'total': 18},
        1991: {'pipeline_mexico': 61, 'pipeline_canada': 3, 'lng': 0, 'total': 64},
        1992: {'pipeline_mexico': 96, 'pipeline_canada': 5, 'lng': 0, 'total': 101},
        1993: {'pipeline_mexico': 40, 'pipeline_canada': 16, 'lng': 0, 'total': 56},
        1994: {'pipeline_mexico': 47, 'pipeline_canada': 15, 'lng': 0, 'total': 62},
        1995: {'pipeline_mexico': 61, 'pipeline_canada': 29, 'lng': 0, 'total': 90},
        1996: {'pipeline_mexico': 34, 'pipeline_canada': 21, 'lng': 0, 'total': 55},
        1997: {'pipeline_mexico': 39, 'pipeline_canada': 17, 'lng': 0, 'total': 56},
        1998: {'pipeline_mexico': 53, 'pipeline_canada': 10, 'lng': 0, 'total': 63},
        1999: {'pipeline_mexico': 61, 'pipeline_canada': 17, 'lng': 0, 'total': 78},
        2000: {'pipeline_mexico': 106, 'pipeline_canada': 17, 'lng': 0, 'total': 123},
        2001: {'pipeline_mexico': 294, 'pipeline_canada': 92, 'lng': 0, 'total': 386},
        2002: {'pipeline_mexico': 332, 'pipeline_canada': 172, 'lng': 0, 'total': 504},
        2003: {'pipeline_mexico': 333, 'pipeline_canada': 242, 'lng': 0, 'total': 575},
        2004: {'pipeline_mexico': 407, 'pipeline_canada': 342, 'lng': 0, 'total': 749},
        2005: {'pipeline_mexico': 389, 'pipeline_canada': 340, 'lng': 0, 'total': 729},
        2006: {'pipeline_mexico': 393, 'pipeline_canada': 371, 'lng': 0, 'total': 764},
        2007: {'pipeline_mexico': 396, 'pipeline_canada': 445, 'lng': 0, 'total': 841},
        2008: {'pipeline_mexico': 360, 'pipeline_canada': 677, 'lng': 0, 'total': 1037},
        2009: {'pipeline_mexico': 361, 'pipeline_canada': 740, 'lng': 0, 'total': 1101},
        2010: {'pipeline_mexico': 378, 'pipeline_canada': 747, 'lng': 0, 'total': 1125},
        2011: {'pipeline_mexico': 490, 'pipeline_canada': 910, 'lng': 0, 'total': 1400},
        2012: {'pipeline_mexico': 634, 'pipeline_canada': 885, 'lng': 0, 'total': 1519},
        2013: {'pipeline_mexico': 690, 'pipeline_canada': 850, 'lng': 0, 'total': 1540},
        2014: {'pipeline_mexico': 755, 'pipeline_canada': 858, 'lng': 0, 'total': 1613},
        2015: {'pipeline_mexico': 912, 'pipeline_canada': 857, 'lng': 28, 'total': 1797},
        2016: {'pipeline_mexico': 1366, 'pipeline_canada': 823, 'lng': 187, 'total': 2376},
        2017: {'pipeline_mexico': 1545, 'pipeline_canada': 845, 'lng': 708, 'total': 3098},
        2018: {'pipeline_mexico': 1726, 'pipeline_canada': 869, 'lng': 1083, 'total': 3678},
        2019: {'pipeline_mexico': 1812, 'pipeline_canada': 952, 'lng': 1819, 'total': 4583},
        2020: {'pipeline_mexico': 1903, 'pipeline_canada': 899, 'lng': 2389, 'total': 5191},
        2021: {'pipeline_mexico': 2086, 'pipeline_canada': 893, 'lng': 3562, 'total': 6541},
        2022: {'pipeline_mexico': 2107, 'pipeline_canada': 978, 'lng': 3860, 'total': 6945},
        2023: {'pipeline_mexico': 2190, 'pipeline_canada': 1010, 'lng': 4325, 'total': 7525},
        2024: {'pipeline_mexico': 2280, 'pipeline_canada': 1050, 'lng': 4500, 'total': 7830},
    }
    
    # Annual imports (Bcf) - mainly from Canada
    ANNUAL_IMPORTS = {
        1990: {'pipeline': 1448, 'lng': 84, 'total': 1532},
        1991: {'pipeline': 1690, 'lng': 18, 'total': 1708},
        1992: {'pipeline': 2020, 'lng': 43, 'total': 2063},
        1993: {'pipeline': 2221, 'lng': 81, 'total': 2302},
        1994: {'pipeline': 2520, 'lng': 51, 'total': 2571},
        1995: {'pipeline': 2797, 'lng': 18, 'total': 2815},
        1996: {'pipeline': 2883, 'lng': 40, 'total': 2923},
        1997: {'pipeline': 2854, 'lng': 77, 'total': 2931},
        1998: {'pipeline': 2959, 'lng': 70, 'total': 3029},
        1999: {'pipeline': 3368, 'lng': 163, 'total': 3531},
        2000: {'pipeline': 3471, 'lng': 226, 'total': 3697},
        2001: {'pipeline': 3688, 'lng': 238, 'total': 3926},
        2002: {'pipeline': 3701, 'lng': 229, 'total': 3930},
        2003: {'pipeline': 3494, 'lng': 507, 'total': 4001},
        2004: {'pipeline': 3558, 'lng': 652, 'total': 4210},
        2005: {'pipeline': 3593, 'lng': 631, 'total': 4224},
        2006: {'pipeline': 3587, 'lng': 584, 'total': 4171},
        2007: {'pipeline': 3779, 'lng': 771, 'total': 4550},
        2008: {'pipeline': 3550, 'lng': 352, 'total': 3902},
        2009: {'pipeline': 3290, 'lng': 453, 'total': 3743},
        2010: {'pipeline': 3098, 'lng': 431, 'total': 3529},
        2011: {'pipeline': 3132, 'lng': 349, 'total': 3481},
        2012: {'pipeline': 2958, 'lng': 128, 'total': 3086},
        2013: {'pipeline': 2835, 'lng': 91, 'total': 2926},
        2014: {'pipeline': 2730, 'lng': 57, 'total': 2787},
        2015: {'pipeline': 2878, 'lng': 37, 'total': 2915},
        2016: {'pipeline': 3040, 'lng': 25, 'total': 3065},
        2017: {'pipeline': 2977, 'lng': 25, 'total': 3002},
        2018: {'pipeline': 2894, 'lng': 25, 'total': 2919},
        2019: {'pipeline': 2813, 'lng': 23, 'total': 2836},
        2020: {'pipeline': 2755, 'lng': 15, 'total': 2770},
        2021: {'pipeline': 2910, 'lng': 18, 'total': 2928},
        2022: {'pipeline': 2920, 'lng': 20, 'total': 2940},
        2023: {'pipeline': 2850, 'lng': 18, 'total': 2868},
        2024: {'pipeline': 2900, 'lng': 15, 'total': 2915},
    }
    
    def __init__(self):
        pass
    
    def get_annual_consumption(self, start_year: int = 1990, 
                               end_year: int = 2000) -> pd.DataFrame:
        """Get annual consumption by sector."""
        records = []
        
        for year, data in self.ANNUAL_CONSUMPTION.items():
            if start_year <= year <= end_year:
                total = data['total']
                records.append({
                    'year': year,
                    'period': f"{year}-12",
                    'residential_bcf': data['residential'],
                    'commercial_bcf': data['commercial'],
                    'industrial_bcf': data['industrial'],
                    'electric_power_bcf': data['electric_power'],
                    'total_bcf': total,
                    # Calculate daily averages (365 days)
                    'residential_bcfd': data['residential'] / 365,
                    'commercial_bcfd': data['commercial'] / 365,
                    'industrial_bcfd': data['industrial'] / 365,
                    'electric_power_bcfd': data['electric_power'] / 365,
                    'total_bcfd': total / 365,
                    # Shares
                    'electric_power_share_pct': data['electric_power'] / total * 100,
                    'industrial_share_pct': data['industrial'] / total * 100,
                    'residential_share_pct': data['residential'] / total * 100,
                    'commercial_share_pct': data['commercial'] / total * 100,
                    'source': 'EIA_Historical'
                })
        
        df = pd.DataFrame(records)
        return df.sort_values('year').reset_index(drop=True)
    
    def get_annual_exports(self, start_year: int = 1990, 
                           end_year: int = 2024) -> pd.DataFrame:
        """Get annual exports by destination."""
        records = []
        
        for year, data in self.ANNUAL_EXPORTS.items():
            if start_year <= year <= end_year:
                total = data['total']
                records.append({
                    'year': year,
                    'period': f"{year}-12",
                    'pipeline_mexico_bcf': data['pipeline_mexico'],
                    'pipeline_canada_bcf': data['pipeline_canada'],
                    'lng_exports_bcf': data['lng'],
                    'total_exports_bcf': total,
                    'pipeline_mexico_bcfd': data['pipeline_mexico'] / 365,
                    'pipeline_canada_bcfd': data['pipeline_canada'] / 365,
                    'lng_exports_bcfd': data['lng'] / 365,
                    'total_exports_bcfd': total / 365,
                    'lng_share_pct': data['lng'] / total * 100 if total > 0 else 0,
                    'source': 'EIA_Historical'
                })
        
        df = pd.DataFrame(records)
        return df.sort_values('year').reset_index(drop=True)
    
    def get_annual_imports(self, start_year: int = 1990, 
                           end_year: int = 2024) -> pd.DataFrame:
        """Get annual imports."""
        records = []
        
        for year, data in self.ANNUAL_IMPORTS.items():
            if start_year <= year <= end_year:
                records.append({
                    'year': year,
                    'period': f"{year}-12",
                    'pipeline_imports_bcf': data['pipeline'],
                    'lng_imports_bcf': data['lng'],
                    'total_imports_bcf': data['total'],
                    'pipeline_imports_bcfd': data['pipeline'] / 365,
                    'total_imports_bcfd': data['total'] / 365,
                    'source': 'EIA_Historical'
                })
        
        df = pd.DataFrame(records)
        return df.sort_values('year').reset_index(drop=True)
    
    def get_annual_net_exports(self, start_year: int = 1990, 
                               end_year: int = 2024) -> pd.DataFrame:
        """Get annual net exports (exports - imports)."""
        exports_df = self.get_annual_exports(start_year, end_year)
        imports_df = self.get_annual_imports(start_year, end_year)
        
        merged = exports_df.merge(imports_df[['year', 'total_imports_bcf', 'total_imports_bcfd']], 
                                  on='year', how='outer')
        
        merged['net_exports_bcf'] = merged['total_exports_bcf'] - merged['total_imports_bcf'].fillna(0)
        merged['net_exports_bcfd'] = merged['total_exports_bcfd'] - merged['total_imports_bcfd'].fillna(0)
        merged['is_net_exporter'] = merged['net_exports_bcf'] > 0
        
        return merged.sort_values('year').reset_index(drop=True)


# =============================================================================
# MAIN FETCH FUNCTION
# =============================================================================

def fetch_demand_drivers(start_year: int = 1990,
                         end_year: int = 2024,
                         output_dir: str = "./",
                         eia_api_key: Optional[str] = None,
                         incremental: bool = True) -> Dict[str, pd.DataFrame]:
    """
    Fetch all natural gas demand driver data.
    
    Args:
        start_year: Start year for data
        end_year: End year for data
        output_dir: Directory to save CSV files
        eia_api_key: Optional EIA API key
        incremental: If True, only fetch missing data
    
    Returns:
        Dictionary of DataFrames by data type
    """
    print("=" * 70)
    print("DECARBIQ NATURAL GAS DEMAND DRIVERS FETCHER")
    print("=" * 70)
    print(f"Date range: {start_year} to {end_year}")
    print(f"Output directory: {os.path.abspath(output_dir)}")
    
    if incremental:
        print("📊 INCREMENTAL MODE: Only fetching missing data")
    
    os.makedirs(output_dir, exist_ok=True)
    
    results = {}
    key = load_eia_api_key(eia_api_key)
    
    if key:
        print(f"✓ EIA API key loaded")
    else:
        print("⚠️  No EIA API key - will use embedded historical data only")
    
    # =========================================================================
    # CONSUMPTION BY SECTOR (API + Embedded)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING CONSUMPTION BY SECTOR")
    print("=" * 70)
    
    consumption_filepath = f"{output_dir}/gas_consumption_by_sector_monthly.csv"
    
    if key:
        try:
            # Total-energy API data starts from 1973
            api_start = max(start_year, 1973)
            
            if incremental:
                missing = get_missing_periods(consumption_filepath, api_start, end_year)
                if not missing:
                    print("  ✓ All consumption data already present, skipping...")
                    if os.path.exists(consumption_filepath):
                        results['consumption'] = pd.read_csv(consumption_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    fetcher = EIAGasConsumptionFetcher(api_key=key)
                    df = fetcher.get_monthly_consumption_by_sector(api_start, end_year)
                    if df is not None and len(df) > 0:
                        results['consumption'] = merge_and_save(consumption_filepath, df)
                        print(f"  ✅ Updated consumption data: {len(results['consumption'])} months")
            else:
                fetcher = EIAGasConsumptionFetcher(api_key=key)
                df = fetcher.get_monthly_consumption_by_sector(api_start, end_year)
                if df is not None and len(df) > 0:
                    df.to_csv(consumption_filepath, index=False)
                    results['consumption'] = df
                    print(f"✅ Saved {len(df)} months of consumption data")
        except Exception as e:
            print(f"❌ Error fetching consumption: {e}")
    
    # =========================================================================
    # EXPORTS (LNG + Pipeline)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING EXPORTS (LNG + Pipeline)")
    print("=" * 70)
    
    exports_filepath = f"{output_dir}/gas_exports_monthly.csv"
    
    if key:
        try:
            # LNG exports significant from 2016
            api_start = max(start_year, 2010)
            
            if incremental:
                missing = get_missing_periods(exports_filepath, api_start, end_year)
                if not missing:
                    print("  ✓ All export data already present, skipping...")
                    if os.path.exists(exports_filepath):
                        results['exports'] = pd.read_csv(exports_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    fetcher = EIAGasExportsFetcher(api_key=key)
                    df = fetcher.get_monthly_exports(api_start, end_year)
                    if df is not None and len(df) > 0:
                        results['exports'] = merge_and_save(exports_filepath, df)
                        print(f"  ✅ Updated export data: {len(results['exports'])} months")
            else:
                fetcher = EIAGasExportsFetcher(api_key=key)
                df = fetcher.get_monthly_exports(api_start, end_year)
                if df is not None and len(df) > 0:
                    df.to_csv(exports_filepath, index=False)
                    results['exports'] = df
                    print(f"✅ Saved {len(df)} months of export data")
        except Exception as e:
            print(f"❌ Error fetching exports: {e}")
    
    # =========================================================================
    # IMPORTS (Pipeline from Canada + LNG)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING IMPORTS")
    print("=" * 70)
    
    imports_filepath = f"{output_dir}/gas_imports_monthly.csv"
    
    if key:
        try:
            api_start = max(start_year, 1997)
            
            if incremental:
                missing = get_missing_periods(imports_filepath, api_start, end_year)
                if not missing:
                    print("  ✓ All import data already present, skipping...")
                    if os.path.exists(imports_filepath):
                        results['imports'] = pd.read_csv(imports_filepath)
                else:
                    print(f"  Missing {len(missing)} months, fetching...")
                    fetcher = EIAGasImportsFetcher(api_key=key)
                    df = fetcher.get_monthly_imports(api_start, end_year)
                    if df is not None and len(df) > 0:
                        results['imports'] = merge_and_save(imports_filepath, df)
                        print(f"  ✅ Updated import data: {len(results['imports'])} months")
            else:
                fetcher = EIAGasImportsFetcher(api_key=key)
                df = fetcher.get_monthly_imports(api_start, end_year)
                if df is not None and len(df) > 0:
                    df.to_csv(imports_filepath, index=False)
                    results['imports'] = df
                    print(f"✅ Saved {len(df)} months of import data")
        except Exception as e:
            print(f"❌ Error fetching imports: {e}")
    
    # =========================================================================
    # EMBEDDED HISTORICAL DATA (Annual, 1990-2024)
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCHING EMBEDDED HISTORICAL DATA")
    print("=" * 70)
    
    embedded = EmbeddedConsumptionData()
    
    # Annual consumption (pre-API)
    if start_year < 2001:
        try:
            cons_hist_filepath = f"{output_dir}/gas_consumption_annual_historical.csv"
            cons_hist_df = embedded.get_annual_consumption(start_year, min(2000, end_year))
            if len(cons_hist_df) > 0:
                cons_hist_df.to_csv(cons_hist_filepath, index=False)
                results['consumption_historical'] = cons_hist_df
                print(f"✅ Historical consumption (1990-2000): {len(cons_hist_df)} years")
                
                # Show power sector growth
                first = cons_hist_df.iloc[0]
                last = cons_hist_df.iloc[-1]
                print(f"    Power sector: {first['electric_power_share_pct']:.1f}% ({int(first['year'])}) → {last['electric_power_share_pct']:.1f}% ({int(last['year'])})")
        except Exception as e:
            print(f"❌ Error with historical consumption: {e}")
    
    # Annual exports (complete history including LNG era)
    try:
        exports_hist_filepath = f"{output_dir}/gas_exports_annual.csv"
        exports_hist_df = embedded.get_annual_exports(start_year, end_year)
        if len(exports_hist_df) > 0:
            exports_hist_df.to_csv(exports_hist_filepath, index=False)
            results['exports_annual'] = exports_hist_df
            print(f"✅ Annual exports: {len(exports_hist_df)} years")
            
            # Show LNG growth
            lng_start = exports_hist_df[exports_hist_df['year'] == 2016]
            lng_end = exports_hist_df[exports_hist_df['year'] == end_year]
            if len(lng_start) > 0 and len(lng_end) > 0:
                print(f"    LNG exports: {lng_start['lng_exports_bcfd'].values[0]:.1f} Bcf/d (2016) → {lng_end['lng_exports_bcfd'].values[0]:.1f} Bcf/d ({end_year})")
    except Exception as e:
        print(f"❌ Error with annual exports: {e}")
    
    # Annual imports
    try:
        imports_hist_filepath = f"{output_dir}/gas_imports_annual.csv"
        imports_hist_df = embedded.get_annual_imports(start_year, end_year)
        if len(imports_hist_df) > 0:
            imports_hist_df.to_csv(imports_hist_filepath, index=False)
            results['imports_annual'] = imports_hist_df
            print(f"✅ Annual imports: {len(imports_hist_df)} years")
    except Exception as e:
        print(f"❌ Error with annual imports: {e}")
    
    # Net exports (when US became net exporter)
    try:
        net_exports_filepath = f"{output_dir}/gas_net_exports_annual.csv"
        net_exports_df = embedded.get_annual_net_exports(start_year, end_year)
        if len(net_exports_df) > 0:
            net_exports_df.to_csv(net_exports_filepath, index=False)
            results['net_exports'] = net_exports_df
            print(f"✅ Net exports: {len(net_exports_df)} years")
            
            # Find when US became net exporter
            net_exporter_years = net_exports_df[net_exports_df['is_net_exporter'] == True]['year'].tolist()
            if net_exporter_years:
                first_net_exporter = min(net_exporter_years)
                print(f"    🇺🇸 US became net gas exporter in {first_net_exporter}")
    except Exception as e:
        print(f"❌ Error with net exports: {e}")
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCH SUMMARY")
    print("=" * 70)
    
    for name, df in results.items():
        print(f"  {name}: {len(df)} records")
    
    print(f"\nFiles saved to: {os.path.abspath(output_dir)}")
    
    # Print key metrics
    print("\n" + "=" * 70)
    print("KEY DEMAND METRICS (2024)")
    print("=" * 70)
    
    if 'exports_annual' in results:
        latest = results['exports_annual'][results['exports_annual']['year'] == end_year]
        if len(latest) > 0:
            row = latest.iloc[0]
            print(f"  LNG Exports: {row['lng_exports_bcfd']:.1f} Bcf/d ({row['lng_share_pct']:.0f}% of total exports)")
            print(f"  Pipeline to Mexico: {row['pipeline_mexico_bcfd']:.1f} Bcf/d")
            print(f"  Total Exports: {row['total_exports_bcfd']:.1f} Bcf/d")
    
    if 'consumption' in results:
        latest = results['consumption'][results['consumption']['year'] == end_year]
        if len(latest) > 0:
            avg = latest.mean(numeric_only=True)
            print(f"  Power Sector: {avg['electric_power_bcfd']:.1f} Bcf/d ({avg['electric_power_share_pct']:.0f}% of total)")
            print(f"  Industrial: {avg['industrial_bcfd']:.1f} Bcf/d ({avg['industrial_share_pct']:.0f}%)")
            print(f"  Residential: {avg['residential_bcfd']:.1f} Bcf/d ({avg['residential_share_pct']:.0f}%)")
    
    return results


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("DecarbIQ Natural Gas Demand Drivers Fetcher")
    print("=" * 70)
    
    # Check for API key
    key = load_eia_api_key()
    if not key:
        print("\n⚠️  No EIA API key found!")
        print("   To fetch monthly data, create eia_token.txt with your API key")
        print("   Get a free key at: https://www.eia.gov/opendata/register.php")
        print("\n   Proceeding with embedded historical data only...\n")
    
    # Run the fetcher
    results = fetch_demand_drivers(
        start_year=1990,
        end_year=2024,
        output_dir="./",
        incremental=True
    )
    
    print("\n✅ Done!")
