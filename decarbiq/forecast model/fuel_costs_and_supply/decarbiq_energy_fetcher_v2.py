"""
DecarbIQ Energy Price Fetcher v2.0
===================================
Comprehensive data fetcher for electricity prices (wholesale AND retail),
natural gas prices, and grid data for regression analysis.

Data sources:
    - ERCOT: Historical DAM Settlement Point Prices (bulk yearly files, 2010+)
    - CAISO: Day-Ahead LMPs via OASIS API (~3 years of history)
    - EIA:   
        * Hourly grid data (load, generation, fuel mix)
        * Henry Hub natural gas spot prices
        * Monthly retail electricity prices by sector (Residential, Commercial, Industrial)
        * Monthly retail electricity prices by state
      Requires a free API key from https://www.eia.gov/opendata/

Installation:
    pip install gridstatus pandas requests

Usage:
    python decarbiq_energy_fetcher.py

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-02-09
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
import json

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
    
    # State codes (FIPS)
    STATE_TEXAS = "TX"
    STATE_CALIFORNIA = "CA"
    STATE_US_TOTAL = "US"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize the EIA retail price fetcher.
        
        Args:
            api_key: EIA API key. If not provided, reads from EIA_API_KEY env var.
        """
        self.api_key = api_key or os.environ.get("EIA_API_KEY")
        if not self.api_key:
            raise ValueError(
                "EIA API key required. Get a free key at "
                "https://www.eia.gov/opendata/register.php "
                "then set EIA_API_KEY env var or pass api_key parameter."
            )
    
    def _make_request(self, params: Dict) -> Optional[Dict]:
        """Make API request to EIA."""
        params['api_key'] = self.api_key
        
        try:
            response = requests.get(self.BASE_URL, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error making EIA API request: {e}")
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
        
        print(f"  Retrieved {len(df)} months of {sector} prices for {state}")
        
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
        Get monthly average wholesale prices for multiple years using DAM bulk files.
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
            print(f"  {row['period']}: ${row['avg_price_mwh']:.2f}/MWh")

        return monthly


# =============================================================================
# CAISO WHOLESALE PRICE FETCHER
# =============================================================================

class CAISOWholesaleFetcher:
    """
    Fetches CAISO wholesale electricity prices using gridstatus library.
    Uses OASIS API for Day-Ahead LMPs (~3 years of history).
    """

    def __init__(self):
        if not GRIDSTATUS_AVAILABLE:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        self.caiso = gridstatus.CAISO()

    def get_daily_lmp(self,
                      date: str,
                      location: str = "TH_SP15_GEN-APND",
                      market: str = "DAY_AHEAD_HOURLY") -> Optional[pd.DataFrame]:
        """Get Locational Marginal Prices for a single day."""
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
        """Get LMPs for a date range."""
        try:
            df = self.caiso.get_lmp(start, end=end, market=market)
            if location:
                df = df[df['Location'].str.contains(location, case=False, na=False)]
            return df
        except Exception as e:
            print(f"Error fetching CAISO LMP: {e}")
            return None

    def get_monthly_average(self,
                            year: int,
                            month: int,
                            location: str = "TH_SP15_GEN-APND") -> Optional[float]:
        """Get monthly average wholesale price for CAISO."""
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

    def get_monthly_series(self,
                           start_year: int,
                           end_year: int,
                           location: str = "TH_SP15_GEN-APND") -> pd.DataFrame:
        """Get monthly average wholesale prices for multiple years."""
        data = []

        for year in range(start_year, end_year + 1):
            for month in range(1, 13):
                if year == datetime.now().year and month > datetime.now().month:
                    continue

                avg_price = self.get_monthly_average(year, month, location)

                if avg_price is not None:
                    data.append({
                        'year': year,
                        'month': month,
                        'period': f"{year}-{month:02d}",
                        'avg_price_mwh': avg_price,
                        'avg_price_kwh': avg_price / 10
                    })
                    print(f"  {year}-{month:02d}: ${avg_price:.2f}/MWh")

        return pd.DataFrame(data)


# =============================================================================
# EIA GRID DATA FETCHER (for load, generation, gas prices)
# =============================================================================

class EIAGridDataFetcher:
    """
    Fetches supplementary grid data from EIA for regression analysis.
    Requires a free API key from https://www.eia.gov/opendata/register.php
    """

    BA_ERCOT = "ERCO"
    BA_CAISO = "CISO"
    BA_PJM = "PJM"
    BA_MISO = "MISO"

    def __init__(self, api_key: Optional[str] = None):
        if not GRIDSTATUS_AVAILABLE:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        key = api_key or os.environ.get("EIA_API_KEY")
        if not key:
            raise ValueError(
                "EIA API key required. Get a free key at "
                "https://www.eia.gov/opendata/register.php"
            )
        self.eia = gridstatus.EIA(api_key=key)

    def get_region_data(self,
                        start: str,
                        end: str,
                        ba_id: str = "ERCO",
                        verbose: bool = False) -> Optional[pd.DataFrame]:
        """Get hourly load, generation, and interchange for a region."""
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
        """Get hourly generation by fuel type for a region."""
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
        """Get Henry Hub natural gas spot prices (daily)."""
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
        """Get monthly average load (MW) for a region."""
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
            print(f"  {row['period']}: Load={row['avg_load_mw']:.0f} MW, "
                  f"Gen={row['avg_generation_mw']:.0f} MW")

        return monthly


# =============================================================================
# COMBINED FETCHER - ALL DATA SOURCES
# =============================================================================

def fetch_all_data(start_year: int = 2020,
                   end_year: int = 2024,
                   output_dir: str = "./",
                   eia_api_key: Optional[str] = None,
                   states: List[str] = ["TX", "CA"]) -> Dict[str, pd.DataFrame]:
    """
    Fetch ALL electricity price data:
    - Wholesale: ERCOT (DAM), CAISO (LMP)
    - Retail: Industrial, Commercial, Residential by state
    - Supplementary: Load, generation, natural gas prices
    
    Args:
        start_year: First year to fetch
        end_year: Last year to fetch
        output_dir: Directory to save CSV files
        eia_api_key: EIA API key (or set EIA_API_KEY env var)
        states: List of state codes for retail prices
    
    Returns:
        Dict with all fetched DataFrames
    """
    results = {}
    key = eia_api_key or os.environ.get("EIA_API_KEY")
    
    # =========================================================================
    # WHOLESALE PRICES
    # =========================================================================
    
    # --- ERCOT Wholesale ---
    print("\n" + "=" * 70)
    print("FETCHING ERCOT WHOLESALE PRICES (DAM)")
    print("=" * 70)

    try:
        ercot_fetcher = ERCOTWholesaleFetcher()
        ercot_df = ercot_fetcher.get_monthly_series(start_year, end_year)
        if len(ercot_df) > 0:
            ercot_df.to_csv(f"{output_dir}/ercot_wholesale_monthly.csv", index=False)
            results['ercot_wholesale'] = ercot_df
            print(f"\n✅ Saved {len(ercot_df)} months of ERCOT wholesale data")
    except Exception as e:
        print(f"❌ Error fetching ERCOT wholesale: {e}")

    # --- CAISO Wholesale ---
    print("\n" + "=" * 70)
    print("FETCHING CAISO WHOLESALE PRICES (DAM)")
    print("=" * 70)

    try:
        caiso_fetcher = CAISOWholesaleFetcher()
        caiso_df = caiso_fetcher.get_monthly_series(start_year, end_year)
        if len(caiso_df) > 0:
            caiso_df.to_csv(f"{output_dir}/caiso_wholesale_monthly.csv", index=False)
            results['caiso_wholesale'] = caiso_df
            print(f"\n✅ Saved {len(caiso_df)} months of CAISO wholesale data")
    except Exception as e:
        print(f"❌ Error fetching CAISO wholesale: {e}")

    # =========================================================================
    # RETAIL PRICES (requires EIA API key)
    # =========================================================================
    
    if key:
        print("\n" + "=" * 70)
        print("FETCHING RETAIL ELECTRICITY PRICES (EIA)")
        print("=" * 70)
        
        try:
            retail_fetcher = EIARetailElectricityFetcher(api_key=key)
            
            # --- Industrial prices by state ---
            print("\n--- Industrial Prices by State ---")
            industrial_df = retail_fetcher.get_industrial_prices_multi_state(
                states=states, start_year=start_year, end_year=end_year)
            if len(industrial_df) > 0:
                industrial_df.to_csv(f"{output_dir}/retail_industrial_monthly.csv", index=False)
                results['retail_industrial'] = industrial_df
                print(f"\n✅ Saved {len(industrial_df)} months of industrial retail data")
            
            # --- All sectors for Texas ---
            print("\n--- All Sectors for Texas ---")
            tx_all_df = retail_fetcher.get_all_sectors_for_state(
                state="TX", start_year=start_year, end_year=end_year)
            if len(tx_all_df) > 0:
                tx_all_df.to_csv(f"{output_dir}/retail_texas_all_sectors.csv", index=False)
                results['retail_texas_all'] = tx_all_df
                print(f"\n✅ Saved {len(tx_all_df)} months of Texas all-sector data")
            
            # --- All sectors for California ---
            print("\n--- All Sectors for California ---")
            ca_all_df = retail_fetcher.get_all_sectors_for_state(
                state="CA", start_year=start_year, end_year=end_year)
            if len(ca_all_df) > 0:
                ca_all_df.to_csv(f"{output_dir}/retail_california_all_sectors.csv", index=False)
                results['retail_california_all'] = ca_all_df
                print(f"\n✅ Saved {len(ca_all_df)} months of California all-sector data")
            
            # --- US Average (all sectors) ---
            print("\n--- US Average (All Sectors) ---")
            us_all_df = retail_fetcher.get_all_sectors_for_state(
                state="US", start_year=start_year, end_year=end_year)
            if len(us_all_df) > 0:
                us_all_df.to_csv(f"{output_dir}/retail_us_all_sectors.csv", index=False)
                results['retail_us_all'] = us_all_df
                print(f"\n✅ Saved {len(us_all_df)} months of US average data")
                
        except Exception as e:
            print(f"❌ Error fetching retail prices: {e}")
            import traceback
            traceback.print_exc()
    
    # =========================================================================
    # SUPPLEMENTARY DATA (EIA grid data, gas prices)
    # =========================================================================
    
    if key:
        print("\n" + "=" * 70)
        print("FETCHING SUPPLEMENTARY DATA (EIA)")
        print("=" * 70)

        try:
            eia_fetcher = EIAGridDataFetcher(api_key=key)

            # ERCOT load/generation
            print("\n--- ERCOT Load & Generation ---")
            ercot_load_df = eia_fetcher.get_monthly_load_series(start_year, end_year, "ERCO")
            if len(ercot_load_df) > 0:
                ercot_load_df.to_csv(f"{output_dir}/eia_ercot_load_monthly.csv", index=False)
                results['eia_ercot_load'] = ercot_load_df
                print(f"✅ Saved {len(ercot_load_df)} months of ERCOT load data")

            # CAISO load/generation
            print("\n--- CAISO Load & Generation ---")
            caiso_load_df = eia_fetcher.get_monthly_load_series(start_year, end_year, "CISO")
            if len(caiso_load_df) > 0:
                caiso_load_df.to_csv(f"{output_dir}/eia_caiso_load_monthly.csv", index=False)
                results['eia_caiso_load'] = caiso_load_df
                print(f"✅ Saved {len(caiso_load_df)} months of CAISO load data")

            # Natural gas prices
            print("\n--- Henry Hub Natural Gas Prices ---")
            gas_df = eia_fetcher.get_natural_gas_prices(
                f"{start_year}-01-01", f"{end_year + 1}-01-01")
            if gas_df is not None and len(gas_df) > 0:
                gas_df['year'] = gas_df['Interval Start'].dt.year
                gas_df['month'] = gas_df['Interval Start'].dt.month
                gas_monthly = gas_df.groupby(['year', 'month'])['price'].mean().reset_index()
                gas_monthly.columns = ['year', 'month', 'avg_gas_price']
                gas_monthly['period'] = gas_monthly.apply(
                    lambda r: f"{int(r['year'])}-{int(r['month']):02d}", axis=1)
                gas_monthly.to_csv(f"{output_dir}/eia_natgas_monthly.csv", index=False)
                results['eia_natgas'] = gas_monthly
                print(f"✅ Saved {len(gas_monthly)} months of natural gas price data")

        except Exception as e:
            print(f"❌ Error fetching EIA supplementary data: {e}")
    else:
        print("\n" + "-" * 70)
        print("⚠️  Skipping EIA data (no API key)")
        print("   To enable retail prices and grid data, set EIA_API_KEY")
        print("   Get a free key: https://www.eia.gov/opendata/register.php")
        print("-" * 70)

    # =========================================================================
    # SUMMARY
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCH SUMMARY")
    print("=" * 70)
    
    for name, df in results.items():
        print(f"  {name}: {len(df)} records")
    
    print(f"\nFiles saved to: {output_dir}")
    
    return results


# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    # Check dependencies
    if not PANDAS_AVAILABLE:
        print("\n❌ Missing pandas. Run: pip install pandas")
        sys.exit(1)
    
    if not GRIDSTATUS_AVAILABLE:
        print("\n❌ Missing gridstatus. Run: pip install gridstatus")
        sys.exit(1)
    
    if not REQUESTS_AVAILABLE:
        print("\n❌ Missing requests. Run: pip install requests")
        sys.exit(1)
    
    print("=" * 70)
    print("DecarbIQ Energy Price Fetcher v2.0")
    print("=" * 70)
    
    # Check for EIA API key
    if not os.environ.get("EIA_API_KEY"):
        print("\n⚠️  EIA_API_KEY not set")
        print("   Wholesale data will be fetched, but retail prices require an API key.")
        print("   Get a free key: https://www.eia.gov/opendata/register.php")
        print("   Then run: export EIA_API_KEY='your_key_here'")
    
    # Fetch all data
    results = fetch_all_data(
        start_year=2020,
        end_year=2024,
        output_dir="./",
        states=["TX", "CA"]
    )
    
    print("\n✅ Data fetch complete!")
