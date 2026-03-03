"""
HOURLY/DAILY DATA ENHANCEMENT FOR DECARBIQ WHOLESALE FETCHER
=============================================================

This module adds hourly and daily data fetching capabilities to the existing
decarbiq_wholesale_fetcher.py. It provides:

1. ERCOTHourlyFetcher - Fetches hourly/15-min SPP data from ERCOT
2. CAISOHourlyFetcher - Fetches hourly LMP data from CAISO OASIS
3. HourlyDataAggregator - Aggregates hourly data to daily with statistics

The daily aggregates include:
- Mean, max, min prices
- 90th and 95th percentile prices (captures tail behavior)
- Standard deviation (volatility)
- Count of hours above price thresholds (scarcity indicators)

These metrics are critical for:
- Improving ERCOT regression R² from 0.15 to 0.35-0.50
- Quantile regression analysis
- Capturing data center load effects on peak prices

Usage:
    from decarbiq_wholesale_fetcher_hourly import fetch_hourly_data
    
    results = fetch_hourly_data(
        start_year=2020,
        end_year=2025,
        output_dir="./hourly_data"
    )

Author: DecarbIQ / Eco Decarb Forward
Date: 2026-02-11
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import time

# Check for required packages
try:
    import pandas as pd
    import numpy as np
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
    print("WARNING: pandas not installed. Run: pip install pandas numpy")

try:
    import gridstatus
    GRIDSTATUS_AVAILABLE = True
except ImportError:
    GRIDSTATUS_AVAILABLE = False
    print("WARNING: gridstatus not installed. Run: pip install gridstatus")


# =============================================================================
# ERCOT HOURLY DATA FETCHER
# =============================================================================

class ERCOTHourlyFetcher:
    """
    Fetches hourly/15-min ERCOT Settlement Point Prices.
    
    Data sources:
    - Historical RTM SPP (NP6-785-ER): 15-min intervals, 2011-present
    - Historical DAM SPP (NP4-180-ER): Hourly, 2010-present
    
    Key locations:
    - HB_HOUSTON: Houston Hub
    - HB_NORTH: North Hub (most liquid trading hub)
    - HB_SOUTH: South Hub  
    - HB_WEST: West Hub (near Permian/wind)
    - LZ_HOUSTON, LZ_NORTH, LZ_SOUTH, LZ_WEST: Load zones
    """
    
    MAIN_HUBS = ['HB_HOUSTON', 'HB_NORTH', 'HB_SOUTH', 'HB_WEST']
    LOAD_ZONES = ['LZ_HOUSTON', 'LZ_NORTH', 'LZ_SOUTH', 'LZ_WEST']
    
    def __init__(self):
        if not GRIDSTATUS_AVAILABLE:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        self.ercot = gridstatus.Ercot()
    
    def get_rtm_spp_year(self, year: int, verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get Real-Time Market Settlement Point Prices for an entire year.
        
        This downloads the annual bulk file from ERCOT MIS (NP6-785-ER).
        15-minute intervals for all hubs and load zones.
        
        Args:
            year: Year to fetch (2011-present)
            verbose: Print progress
            
        Returns:
            DataFrame with columns: Time, Location, SPP, etc.
        """
        try:
            if verbose:
                print(f"  Downloading ERCOT RTM SPP for {year}...")
                print(f"    (This downloads ~50MB annual file, may take 1-2 minutes)")
            
            df = self.ercot.get_rtm_spp(year=year, verbose=verbose)
            
            if verbose and df is not None:
                print(f"    ✓ Retrieved {len(df):,} records")
            
            return df
            
        except Exception as e:
            print(f"  Error fetching ERCOT RTM SPP for {year}: {e}")
            return None
    
    def get_dam_spp_year(self, year: int, verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get Day-Ahead Market Settlement Point Prices for an entire year.
        
        This downloads the annual bulk file from ERCOT MIS (NP4-180-ER).
        Hourly intervals for all hubs and load zones.
        
        Args:
            year: Year to fetch (2010-present)
            verbose: Print progress
            
        Returns:
            DataFrame with columns: Time, Location, SPP, etc.
        """
        try:
            if verbose:
                print(f"  Downloading ERCOT DAM SPP for {year}...")
            
            df = self.ercot.get_dam_spp(year=year, verbose=verbose)
            
            if verbose and df is not None:
                print(f"    ✓ Retrieved {len(df):,} records")
            
            return df
            
        except Exception as e:
            print(f"  Error fetching ERCOT DAM SPP for {year}: {e}")
            return None
    
    def get_multi_year_rtm(self, 
                           start_year: int, 
                           end_year: int,
                           locations: List[str] = None,
                           verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get RTM SPP for multiple years, filtered to specified locations.
        
        Args:
            start_year: First year
            end_year: Last year
            locations: List of locations to include (default: main hubs)
            verbose: Print progress
            
        Returns:
            Combined DataFrame
        """
        if locations is None:
            locations = self.MAIN_HUBS
        
        all_data = []
        
        for year in range(start_year, end_year + 1):
            df = self.get_rtm_spp_year(year, verbose=verbose)
            
            if df is not None and len(df) > 0:
                # Filter to requested locations
                if 'Location' in df.columns:
                    df = df[df['Location'].isin(locations)]
                elif 'SettlementPoint' in df.columns:
                    df = df[df['SettlementPoint'].isin(locations)]
                    df = df.rename(columns={'SettlementPoint': 'Location'})
                
                all_data.append(df)
        
        if not all_data:
            return None
        
        combined = pd.concat(all_data, ignore_index=True)
        
        if verbose:
            print(f"\n  Total: {len(combined):,} records across {len(all_data)} years")
        
        return combined
    
    def get_multi_year_dam(self,
                           start_year: int,
                           end_year: int,
                           locations: List[str] = None,
                           verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get DAM SPP for multiple years, filtered to specified locations.
        """
        if locations is None:
            locations = self.MAIN_HUBS
        
        all_data = []
        
        for year in range(start_year, end_year + 1):
            df = self.get_dam_spp_year(year, verbose=verbose)
            
            if df is not None and len(df) > 0:
                if 'Location' in df.columns:
                    df = df[df['Location'].isin(locations)]
                elif 'SettlementPoint' in df.columns:
                    df = df[df['SettlementPoint'].isin(locations)]
                    df = df.rename(columns={'SettlementPoint': 'Location'})
                
                all_data.append(df)
        
        if not all_data:
            return None
        
        combined = pd.concat(all_data, ignore_index=True)
        
        if verbose:
            print(f"\n  Total: {len(combined):,} records across {len(all_data)} years")
        
        return combined


# =============================================================================
# CAISO HOURLY DATA FETCHER
# =============================================================================

class CAISOHourlyFetcher:
    """
    Fetches hourly CAISO Locational Marginal Prices from OASIS API.
    
    Data source: CAISO OASIS (via gridstatus)
    Coverage: 2009-present (MRTU market start)
    
    Key locations:
    - TH_NP15_GEN-APND: Northern California trading hub
    - TH_SP15_GEN-APND: Southern California trading hub (most liquid)
    - TH_ZP26_GEN-APND: Zone P26 trading hub
    
    Note: OASIS API is rate-limited and slow (~30 seconds per day).
    Use sleep parameter to avoid rate limiting.
    """
    
    TRADING_HUBS = [
        'TH_NP15_GEN-APND',  # Northern California
        'TH_SP15_GEN-APND',  # Southern California
        'TH_ZP26_GEN-APND',  # Zone P26
    ]
    
    def __init__(self):
        if not GRIDSTATUS_AVAILABLE:
            raise ImportError("gridstatus library required. Run: pip install gridstatus")
        self.caiso = gridstatus.CAISO()
    
    def get_lmp_range(self,
                      start: str,
                      end: str,
                      locations: List[str] = None,
                      market: str = 'DAY_AHEAD_HOURLY',
                      sleep: int = 5,
                      verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get LMP data for a date range.
        
        Args:
            start: Start date 'YYYY-MM-DD'
            end: End date 'YYYY-MM-DD'
            locations: List of trading hubs (default: all three main hubs)
            market: 'DAY_AHEAD_HOURLY' or 'REAL_TIME_15_MIN'
            sleep: Seconds between API calls (for rate limiting)
            verbose: Print progress
            
        Returns:
            DataFrame with columns: Time, Location, LMP, Energy, Congestion, Loss
        """
        if locations is None:
            locations = self.TRADING_HUBS
        
        try:
            if verbose:
                print(f"  Fetching CAISO {market} LMP from {start} to {end}...")
                print(f"    Locations: {locations}")
                print(f"    (OASIS API is slow, ~30 sec/day. Total may take several minutes)")
            
            df = self.caiso.get_lmp(
                start=start,
                end=end,
                market=market,
                locations=locations,
                sleep=sleep
            )
            
            if verbose and df is not None:
                print(f"    ✓ Retrieved {len(df):,} records")
            
            return df
            
        except Exception as e:
            print(f"  Error fetching CAISO LMP: {e}")
            return None
    
    def get_year_by_month(self,
                          year: int,
                          locations: List[str] = None,
                          market: str = 'DAY_AHEAD_HOURLY',
                          verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get a full year of LMP data, fetched month by month.
        
        This is more reliable than fetching the entire year at once.
        
        Args:
            year: Year to fetch
            locations: List of trading hubs
            market: Market type
            verbose: Print progress
            
        Returns:
            Combined DataFrame for the year
        """
        if locations is None:
            locations = self.TRADING_HUBS
        
        all_data = []
        
        for month in range(1, 13):
            # Skip future months
            if year == datetime.now().year and month > datetime.now().month:
                break
            
            start = f"{year}-{month:02d}-01"
            
            if month == 12:
                end = f"{year+1}-01-01"
            else:
                end = f"{year}-{month+1:02d}-01"
            
            if verbose:
                print(f"  Fetching {year}-{month:02d}...")
            
            # Retry with exponential backoff (gridstatus only retries 3x with fixed sleep)
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    df = self.caiso.get_lmp(
                        start=start,
                        end=end,
                        market=market,
                        locations=locations,
                        sleep=15
                    )

                    if df is not None and len(df) > 0:
                        all_data.append(df)
                        if verbose:
                            avg_lmp = df['LMP'].mean() if 'LMP' in df.columns else 0
                            print(f"    ✓ {len(df):,} records, avg ${avg_lmp:.2f}/MWh")
                        break  # Success, move to next month
                    else:
                        # Empty response — likely rate limited, retry with backoff
                        backoff = 15 * (2 ** attempt)
                        if attempt < max_retries - 1:
                            if verbose:
                                print(f"    ⏳ Empty response, retrying in {backoff}s (attempt {attempt+1}/{max_retries})...")
                            time.sleep(backoff)
                        else:
                            if verbose:
                                print(f"    ✗ No data after {max_retries} attempts")

                except Exception as e:
                    backoff = 15 * (2 ** attempt)
                    if attempt < max_retries - 1:
                        if verbose:
                            print(f"    ✗ Error: {e}, retrying in {backoff}s...")
                        time.sleep(backoff)
                    else:
                        print(f"    ✗ Failed after {max_retries} attempts: {e}")

            # Rate limiting between months
            time.sleep(10)
        
        if not all_data:
            return None
        
        combined = pd.concat(all_data, ignore_index=True)
        
        if verbose:
            print(f"\n  Year {year} total: {len(combined):,} records")
        
        return combined
    
    def get_multi_year(self,
                       start_year: int,
                       end_year: int,
                       locations: List[str] = None,
                       market: str = 'DAY_AHEAD_HOURLY',
                       verbose: bool = True) -> Optional[pd.DataFrame]:
        """
        Get multiple years of LMP data.
        """
        all_data = []
        
        for year in range(start_year, end_year + 1):
            if verbose:
                print(f"\n{'='*50}")
                print(f"CAISO {year}")
                print('='*50)
            
            df = self.get_year_by_month(year, locations, market, verbose)
            
            if df is not None:
                all_data.append(df)
        
        if not all_data:
            return None
        
        return pd.concat(all_data, ignore_index=True)


# =============================================================================
# HOURLY DATA AGGREGATOR
# =============================================================================

class HourlyDataAggregator:
    """
    Aggregates hourly price data to daily statistics.
    
    Computes:
    - Mean, max, min prices
    - 90th and 95th percentile (tail behavior)
    - Standard deviation (volatility)
    - Hours above price thresholds (scarcity indicators)
    - Negative price hours (CAISO duck curve)
    """
    
    # Price thresholds for scarcity counting ($/MWh)
    PRICE_THRESHOLDS = [100, 200, 500, 1000, 5000]
    
    @staticmethod
    def aggregate_to_daily(df: pd.DataFrame,
                           time_col: str = 'Time',
                           location_col: str = 'Location',
                           price_col: str = 'SPP',
                           verbose: bool = True) -> pd.DataFrame:
        """
        Aggregate hourly/15-min data to daily statistics.
        
        Args:
            df: Hourly DataFrame
            time_col: Name of timestamp column
            location_col: Name of location column
            price_col: Name of price column
            verbose: Print progress
            
        Returns:
            Daily aggregated DataFrame
        """
        if df is None or len(df) == 0:
            return pd.DataFrame()
        
        # Standardize column names
        df = df.copy()
        
        # Find the time column
        time_cols = ['Time', 'Interval Start', 'IntervalStart', 'INTERVALSTARTTIME_GMT', 'OPR_DT']
        for tc in time_cols:
            if tc in df.columns:
                time_col = tc
                break
        
        # Find the price column
        price_cols = ['SPP', 'LMP', 'Price', 'price', 'Value', 'value']
        for pc in price_cols:
            if pc in df.columns:
                price_col = pc
                break
        
        # Ensure datetime
        df[time_col] = pd.to_datetime(df[time_col])
        df['date'] = df[time_col].dt.date
        
        # Find location column
        loc_cols = ['Location', 'SettlementPoint', 'NODE', 'node']
        for lc in loc_cols:
            if lc in df.columns:
                location_col = lc
                break
        
        if verbose:
            print(f"  Aggregating {len(df):,} records to daily...")
            print(f"    Time column: {time_col}")
            print(f"    Price column: {price_col}")
            print(f"    Location column: {location_col}")
        
        # Define aggregation functions
        def percentile_90(x):
            return np.percentile(x, 90)
        
        def percentile_95(x):
            return np.percentile(x, 95)
        
        def hours_gt_100(x):
            return (x > 100).sum()
        
        def hours_gt_200(x):
            return (x > 200).sum()
        
        def hours_gt_500(x):
            return (x > 500).sum()
        
        def hours_gt_1000(x):
            return (x > 1000).sum()
        
        def hours_gt_5000(x):
            return (x > 5000).sum()
        
        def hours_negative(x):
            return (x < 0).sum()
        
        # Group and aggregate
        agg_dict = {
            price_col: [
                ('price_mean', 'mean'),
                ('price_max', 'max'),
                ('price_min', 'min'),
                ('price_std', 'std'),
                ('price_p90', percentile_90),
                ('price_p95', percentile_95),
                ('hours_gt_100', hours_gt_100),
                ('hours_gt_200', hours_gt_200),
                ('hours_gt_500', hours_gt_500),
                ('hours_gt_1000', hours_gt_1000),
                ('hours_gt_5000', hours_gt_5000),
                ('hours_negative', hours_negative),
                ('n_intervals', 'count'),
            ]
        }
        
        # Group by date and location
        if location_col in df.columns:
            daily = df.groupby(['date', location_col]).agg(agg_dict)
        else:
            daily = df.groupby('date').agg(agg_dict)
        
        # Flatten column names
        daily.columns = [col[1] if isinstance(col, tuple) else col for col in daily.columns]
        daily = daily.reset_index()
        
        # Add derived columns
        daily['price_range'] = daily['price_max'] - daily['price_min']
        daily['price_cv'] = daily['price_std'] / daily['price_mean'].abs()  # Coefficient of variation
        
        if verbose:
            print(f"    ✓ Created {len(daily):,} daily records")
            if 'price_mean' in daily.columns:
                print(f"    Price range: ${daily['price_min'].min():.2f} to ${daily['price_max'].max():.2f}/MWh")
                print(f"    Avg daily mean: ${daily['price_mean'].mean():.2f}/MWh")
        
        return daily
    
    @staticmethod
    def aggregate_to_monthly(daily_df: pd.DataFrame,
                             date_col: str = 'date',
                             location_col: str = 'Location',
                             verbose: bool = True) -> pd.DataFrame:
        """
        Aggregate daily data to monthly statistics.
        
        Preserves tail statistics by taking monthly averages of daily percentiles.
        """
        if daily_df is None or len(daily_df) == 0:
            return pd.DataFrame()
        
        df = daily_df.copy()
        df['date'] = pd.to_datetime(df[date_col])
        df['year'] = df['date'].dt.year
        df['month'] = df['date'].dt.month
        df['period'] = df['date'].dt.to_period('M').astype(str)
        
        # Numeric columns to aggregate
        numeric_cols = df.select_dtypes(include=[np.number]).columns.tolist()
        numeric_cols = [c for c in numeric_cols if c not in ['year', 'month']]
        
        agg_dict = {}
        for col in numeric_cols:
            if col.startswith('hours_'):
                agg_dict[col] = 'sum'  # Sum up hours
            elif col == 'n_intervals':
                agg_dict[col] = 'sum'
            else:
                agg_dict[col] = 'mean'  # Average the statistics
        
        # Track max of max separately
        has_price_max = 'price_max' in numeric_cols
        
        # Group by year, month, and location
        group_cols = ['year', 'month', 'period']
        if location_col in df.columns:
            group_cols.append(location_col)
        
        monthly = df.groupby(group_cols).agg(agg_dict).reset_index()

        # Flatten any multi-level columns
        if isinstance(monthly.columns, pd.MultiIndex):
            monthly.columns = ['_'.join(col).strip('_') for col in monthly.columns]

        # Add max of max as a separate column
        if has_price_max:
            max_of_max = df.groupby(group_cols)['price_max'].max().reset_index()
            max_of_max = max_of_max.rename(columns={'price_max': 'price_max_of_max'})
            monthly = monthly.merge(max_of_max, on=group_cols)
        
        if verbose:
            print(f"  Aggregated to {len(monthly)} monthly records")
        
        return monthly


# =============================================================================
# MAIN FETCH FUNCTION
# =============================================================================

def fetch_hourly_data(start_year: int = 2020,
                      end_year: int = 2025,
                      output_dir: str = "./hourly_data",
                      fetch_ercot: bool = True,
                      fetch_caiso: bool = True,
                      ercot_market: str = 'RTM',  # 'RTM' or 'DAM'
                      caiso_market: str = 'DAY_AHEAD_HOURLY',
                      aggregate_daily: bool = True,
                      aggregate_monthly: bool = True) -> Dict[str, pd.DataFrame]:
    """
    Fetch hourly electricity price data from ERCOT and CAISO.
    
    Args:
        start_year: First year to fetch
        end_year: Last year to fetch
        output_dir: Directory to save CSV files
        fetch_ercot: Whether to fetch ERCOT data
        fetch_caiso: Whether to fetch CAISO data
        ercot_market: 'RTM' (15-min real-time) or 'DAM' (hourly day-ahead)
        caiso_market: 'DAY_AHEAD_HOURLY' or 'REAL_TIME_15_MIN'
        aggregate_daily: Whether to compute daily aggregates
        aggregate_monthly: Whether to compute monthly aggregates
        
    Returns:
        Dictionary of DataFrames with keys:
        - ercot_hourly: Raw hourly/15-min ERCOT data
        - ercot_daily: Daily aggregated ERCOT data
        - ercot_monthly: Monthly aggregated ERCOT data
        - caiso_hourly: Raw hourly CAISO data
        - caiso_daily: Daily aggregated CAISO data
        - caiso_monthly: Monthly aggregated CAISO data
    """
    os.makedirs(output_dir, exist_ok=True)
    
    results = {}
    aggregator = HourlyDataAggregator()
    
    print("=" * 70)
    print("DECARBIQ HOURLY DATA FETCHER")
    print("=" * 70)
    print(f"Period: {start_year} to {end_year}")
    print(f"Output directory: {os.path.abspath(output_dir)}")
    print()
    
    # =========================================================================
    # ERCOT
    # =========================================================================
    
    if fetch_ercot:
        print("=" * 70)
        print("FETCHING ERCOT HOURLY DATA")
        print("=" * 70)
        
        try:
            ercot_fetcher = ERCOTHourlyFetcher()
            
            if ercot_market == 'RTM':
                ercot_hourly = ercot_fetcher.get_multi_year_rtm(
                    start_year, end_year,
                    locations=ERCOTHourlyFetcher.MAIN_HUBS,
                    verbose=True
                )
                market_suffix = 'rtm'
            else:
                ercot_hourly = ercot_fetcher.get_multi_year_dam(
                    start_year, end_year,
                    locations=ERCOTHourlyFetcher.MAIN_HUBS,
                    verbose=True
                )
                market_suffix = 'dam'
            
            if ercot_hourly is not None and len(ercot_hourly) > 0:
                # Save hourly
                hourly_path = f"{output_dir}/ercot_{market_suffix}_hourly_{start_year}_{end_year}.csv"
                ercot_hourly.to_csv(hourly_path, index=False)
                results['ercot_hourly'] = ercot_hourly
                print(f"\n✅ Saved ERCOT hourly: {hourly_path}")
                print(f"   {len(ercot_hourly):,} records")
                
                # Aggregate to daily
                if aggregate_daily:
                    print("\nAggregating ERCOT to daily...")
                    ercot_daily = aggregator.aggregate_to_daily(ercot_hourly, verbose=True)
                    
                    if len(ercot_daily) > 0:
                        daily_path = f"{output_dir}/ercot_{market_suffix}_daily_{start_year}_{end_year}.csv"
                        ercot_daily.to_csv(daily_path, index=False)
                        results['ercot_daily'] = ercot_daily
                        print(f"✅ Saved ERCOT daily: {daily_path}")
                        
                        # Aggregate to monthly
                        if aggregate_monthly:
                            print("\nAggregating ERCOT to monthly...")
                            ercot_monthly = aggregator.aggregate_to_monthly(ercot_daily, verbose=True)
                            
                            if len(ercot_monthly) > 0:
                                monthly_path = f"{output_dir}/ercot_{market_suffix}_monthly_{start_year}_{end_year}.csv"
                                ercot_monthly.to_csv(monthly_path, index=False)
                                results['ercot_monthly'] = ercot_monthly
                                print(f"✅ Saved ERCOT monthly: {monthly_path}")
                                
        except Exception as e:
            print(f"❌ Error fetching ERCOT data: {e}")
            import traceback
            traceback.print_exc()
    
    # =========================================================================
    # CAISO
    # =========================================================================
    
    if fetch_caiso:
        print("\n" + "=" * 70)
        print("FETCHING CAISO HOURLY DATA")
        print("=" * 70)
        print("⚠️  CAISO OASIS API is slow. This may take 30-60 minutes for 5 years.")
        print()
        
        try:
            caiso_fetcher = CAISOHourlyFetcher()
            
            caiso_hourly = caiso_fetcher.get_multi_year(
                start_year, end_year,
                locations=CAISOHourlyFetcher.TRADING_HUBS,
                market=caiso_market,
                verbose=True
            )
            
            if caiso_hourly is not None and len(caiso_hourly) > 0:
                # Save hourly
                market_suffix = 'dam' if 'DAY_AHEAD' in caiso_market else 'rtm'
                hourly_path = f"{output_dir}/caiso_{market_suffix}_hourly_{start_year}_{end_year}.csv"
                caiso_hourly.to_csv(hourly_path, index=False)
                results['caiso_hourly'] = caiso_hourly
                print(f"\n✅ Saved CAISO hourly: {hourly_path}")
                print(f"   {len(caiso_hourly):,} records")
                
                # Aggregate to daily
                if aggregate_daily:
                    print("\nAggregating CAISO to daily...")
                    caiso_daily = aggregator.aggregate_to_daily(
                        caiso_hourly, 
                        price_col='LMP',
                        verbose=True
                    )
                    
                    if len(caiso_daily) > 0:
                        daily_path = f"{output_dir}/caiso_{market_suffix}_daily_{start_year}_{end_year}.csv"
                        caiso_daily.to_csv(daily_path, index=False)
                        results['caiso_daily'] = caiso_daily
                        print(f"✅ Saved CAISO daily: {daily_path}")
                        
                        # Aggregate to monthly
                        if aggregate_monthly:
                            print("\nAggregating CAISO to monthly...")
                            caiso_monthly = aggregator.aggregate_to_monthly(caiso_daily, verbose=True)
                            
                            if len(caiso_monthly) > 0:
                                monthly_path = f"{output_dir}/caiso_{market_suffix}_monthly_{start_year}_{end_year}.csv"
                                caiso_monthly.to_csv(monthly_path, index=False)
                                results['caiso_monthly'] = caiso_monthly
                                print(f"✅ Saved CAISO monthly: {monthly_path}")
                                
        except Exception as e:
            print(f"❌ Error fetching CAISO data: {e}")
            import traceback
            traceback.print_exc()
    
    # =========================================================================
    # SUMMARY
    # =========================================================================
    
    print("\n" + "=" * 70)
    print("FETCH SUMMARY")
    print("=" * 70)
    
    for name, df in results.items():
        print(f"  {name}: {len(df):,} records")
    
    print(f"\nFiles saved to: {os.path.abspath(output_dir)}")
    
    return results


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    if not PANDAS_AVAILABLE or not GRIDSTATUS_AVAILABLE:
        print("\n" + "=" * 60)
        print("MISSING DEPENDENCIES")
        print("=" * 60)
        print("\nTo use this module, install required packages:")
        print("  pip install gridstatus pandas numpy")
        print("\nThen run this script again.")
        sys.exit(1)
    
    # Run fetch
    # ERCOT RTM available from ~2011, CAISO OASIS LMP from ~2023 for TH_* nodes
    results = fetch_hourly_data(
        start_year=2023,
        end_year=2025,
        output_dir="./hourly_data",
        fetch_ercot=False,  # Already fetched
        fetch_caiso=True,
        ercot_market='RTM',  # or 'DAM'
        aggregate_daily=True,
        aggregate_monthly=True
    )
    
    print("\n✅ Hourly data fetch complete!")
