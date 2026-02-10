"""
DecarbIQ Gas-Electricity Price Analysis Tool
=============================================
Fetches historical data, builds database, and analyzes trends
for US Average, California (CAISO), and Texas (ERCOT)
"""

import csv
import json
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
import statistics

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class AnnualDataPoint:
    """Single year of gas and electricity data for a region"""
    year: int
    region: str  # 'US', 'California', 'Texas'
    gas_price_usd_mmbtu: float
    elec_price_cents_kwh: float
    gas_share_pct: float
    coal_share_pct: float
    renewables_share_pct: float
    nuclear_share_pct: float = 0.0
    notes: str = ""

@dataclass 
class EraAnalysis:
    """Analysis results for a specific era"""
    era_name: str
    start_year: int
    end_year: int
    region: str
    avg_gas_price: float
    avg_elec_price: float
    gas_price_volatility: float  # std dev
    elec_price_volatility: float
    avg_gas_share: float
    gas_elec_correlation: float
    price_trend_gas: float  # annualized % change
    price_trend_elec: float

# =============================================================================
# RAW DATA - From EIA and other sources
# =============================================================================

# Henry Hub Natural Gas Prices ($/MMBtu) - Monthly averages annualized
HENRY_HUB_ANNUAL = {
    1997: 2.52, 1998: 2.08, 1999: 2.27, 2000: 4.31, 2001: 4.07,
    2002: 3.37, 2003: 5.49, 2004: 5.90, 2005: 8.81, 2006: 6.74,
    2007: 6.97, 2008: 8.86, 2009: 3.95, 2010: 4.39, 2011: 4.00,
    2012: 2.75, 2013: 3.73, 2014: 4.39, 2015: 2.63, 2016: 2.52,
    2017: 2.99, 2018: 3.18, 2019: 2.57, 2020: 2.03, 2021: 3.91,
    2022: 6.45, 2023: 2.54, 2024: 2.19, 2025: 3.50
}

# US Average Retail Electricity Prices (cents/kWh) - All sectors
US_ELEC_ANNUAL = {
    1997: 6.85, 1998: 6.74, 1999: 6.64, 2000: 6.81, 2001: 7.29,
    2002: 7.20, 2003: 7.44, 2004: 7.61, 2005: 8.14, 2006: 8.90,
    2007: 9.13, 2008: 9.74, 2009: 9.82, 2010: 9.83, 2011: 9.90,
    2012: 9.84, 2013: 10.07, 2014: 10.44, 2015: 10.41, 2016: 10.27,
    2017: 10.48, 2018: 10.53, 2019: 10.54, 2020: 10.59, 2021: 10.98,
    2022: 12.55, 2023: 12.68, 2024: 12.94, 2025: 13.50
}

# US Generation Mix - Gas share %
US_GAS_SHARE = {
    1997: 15, 1998: 15, 1999: 16, 2000: 16, 2001: 17,
    2002: 18, 2003: 17, 2004: 18, 2005: 19, 2006: 20,
    2007: 22, 2008: 21, 2009: 23, 2010: 24, 2011: 25,
    2012: 30, 2013: 27, 2014: 27, 2015: 33, 2016: 34,
    2017: 32, 2018: 35, 2019: 38, 2020: 40, 2021: 37,
    2022: 39, 2023: 43, 2024: 43, 2025: 43
}

# US Coal share %
US_COAL_SHARE = {
    1997: 52, 1998: 52, 1999: 51, 2000: 52, 2001: 51,
    2002: 50, 2003: 51, 2004: 50, 2005: 50, 2006: 49,
    2007: 49, 2008: 48, 2009: 45, 2010: 45, 2011: 42,
    2012: 37, 2013: 39, 2014: 39, 2015: 33, 2016: 30,
    2017: 30, 2018: 27, 2019: 24, 2020: 20, 2021: 22,
    2022: 20, 2023: 16, 2024: 16, 2025: 15
}

# US Renewables share % (including hydro)
US_RENEWABLES_SHARE = {
    1997: 10, 1998: 10, 1999: 10, 2000: 9, 2001: 9,
    2002: 9, 2003: 9, 2004: 9, 2005: 9, 2006: 9,
    2007: 9, 2008: 9, 2009: 10, 2010: 10, 2011: 12,
    2012: 12, 2013: 13, 2014: 13, 2015: 13, 2016: 15,
    2017: 17, 2018: 17, 2019: 18, 2020: 20, 2021: 20,
    2022: 22, 2023: 21, 2024: 22, 2025: 23
}

# US Nuclear share %
US_NUCLEAR_SHARE = {
    1997: 18, 1998: 19, 1999: 20, 2000: 20, 2001: 20,
    2002: 20, 2003: 20, 2004: 20, 2005: 19, 2006: 19,
    2007: 19, 2008: 19, 2009: 20, 2010: 20, 2011: 19,
    2012: 19, 2013: 19, 2014: 19, 2015: 20, 2016: 20,
    2017: 20, 2018: 19, 2019: 20, 2020: 20, 2021: 19,
    2022: 18, 2023: 19, 2024: 19, 2025: 18
}

# -----------------------------------------------------------------------------
# CALIFORNIA / CAISO DATA
# -----------------------------------------------------------------------------

# SoCal Citygate premium over Henry Hub (estimated annual average)
# Typically $1-5 above Henry Hub, with extreme spikes
SOCAL_PREMIUM = {
    1997: 0.70, 1998: 0.70, 1999: 0.75, 2000: 3.20, 2001: 4.00,  # Crisis
    2002: 1.10, 2003: 1.00, 2004: 1.10, 2005: 1.70, 2006: 1.30,
    2007: 1.50, 2008: 1.60, 2009: 1.00, 2010: 1.10, 2011: 1.20,
    2012: 0.75, 2013: 1.10, 2014: 1.10, 2015: 0.85, 2016: 0.70,
    2017: 0.80, 2018: 1.30, 2019: 0.90, 2020: 0.95, 2021: 1.60,
    2022: 5.50, 2023: 4.00, 2024: 2.35, 2025: 1.50  # Dec 2022 spike averaged in
}

# California electricity prices (cents/kWh) - All sectors
CA_ELEC_ANNUAL = {
    1997: 9.50, 1998: 9.20, 1999: 9.10, 2000: 9.50, 2001: 12.00,  # Crisis
    2002: 11.50, 2003: 11.80, 2004: 12.20, 2005: 12.80, 2006: 13.50,
    2007: 13.80, 2008: 14.50, 2009: 15.00, 2010: 14.80, 2011: 15.20,
    2012: 15.00, 2013: 15.80, 2014: 16.50, 2015: 16.00, 2016: 16.50,
    2017: 17.50, 2018: 18.00, 2019: 19.00, 2020: 19.90, 2021: 22.00,
    2022: 26.50, 2023: 24.87, 2024: 27.04, 2025: 28.50
}

# California gas share % (historically high, now declining)
CA_GAS_SHARE = {
    1997: 35, 1998: 36, 1999: 37, 2000: 38, 2001: 35,
    2002: 36, 2003: 37, 2004: 40, 2005: 42, 2006: 43,
    2007: 46, 2008: 47, 2009: 48, 2010: 48, 2011: 46,
    2012: 45, 2013: 44, 2014: 44, 2015: 45, 2016: 43,
    2017: 42, 2018: 40, 2019: 38, 2020: 37, 2021: 37,
    2022: 38, 2023: 42, 2024: 40, 2025: 38
}

# California coal share % (effectively 0 since 2011)
CA_COAL_SHARE = {
    1997: 1, 1998: 1, 1999: 1, 2000: 1, 2001: 1,
    2002: 1, 2003: 1, 2004: 1, 2005: 1, 2006: 1,
    2007: 1, 2008: 1, 2009: 1, 2010: 1, 2011: 0,
    2012: 0, 2013: 0, 2014: 0, 2015: 0, 2016: 0,
    2017: 0, 2018: 0, 2019: 0, 2020: 0, 2021: 0,
    2022: 0, 2023: 0, 2024: 0, 2025: 0
}

# California renewables % (including large hydro and imports)
CA_RENEWABLES_SHARE = {
    1997: 30, 1998: 30, 1999: 29, 2000: 28, 2001: 27,
    2002: 28, 2003: 27, 2004: 26, 2005: 25, 2006: 24,
    2007: 23, 2008: 22, 2009: 24, 2010: 25, 2011: 27,
    2012: 28, 2013: 30, 2014: 32, 2015: 33, 2016: 35,
    2017: 38, 2018: 40, 2019: 44, 2020: 46, 2021: 47,
    2022: 48, 2023: 48, 2024: 50, 2025: 52
}

# California nuclear % (Diablo Canyon + San Onofre until 2012)
CA_NUCLEAR_SHARE = {
    1997: 15, 1998: 15, 1999: 15, 2000: 15, 2001: 15,
    2002: 15, 2003: 16, 2004: 15, 2005: 15, 2006: 15,
    2007: 15, 2008: 15, 2009: 15, 2010: 15, 2011: 15,
    2012: 9, 2013: 9, 2014: 9, 2015: 9, 2016: 9,  # San Onofre closed 2012
    2017: 9, 2018: 9, 2019: 8, 2020: 8, 2021: 8,
    2022: 8, 2023: 8, 2024: 8, 2025: 8
}

# -----------------------------------------------------------------------------
# TEXAS / ERCOT DATA
# -----------------------------------------------------------------------------

# Waha Hub discount vs Henry Hub (often negative in Permian)
# Negative means Waha is BELOW Henry Hub
WAHA_SPREAD = {
    1997: -0.10, 1998: -0.10, 1999: -0.05, 2000: -0.10, 2001: -0.15,
    2002: -0.15, 2003: -0.20, 2004: -0.20, 2005: -0.30, 2006: -0.25,
    2007: -0.20, 2008: -0.35, 2009: -0.15, 2010: -0.20, 2011: -0.20,
    2012: -0.15, 2013: -0.25, 2014: -0.20, 2015: -0.15, 2016: -0.10,
    2017: -0.20, 2018: -0.20, 2019: -0.15, 2020: -0.15, 2021: -0.10,  # Uri spike
    2022: -0.25, 2023: -0.15, 2024: -0.20, 2025: -0.30  # Often negative intraday
}

# Texas electricity prices (cents/kWh) - All sectors
TX_ELEC_ANNUAL = {
    1997: 6.20, 1998: 6.00, 1999: 5.90, 2000: 7.50, 2001: 7.80,
    2002: 7.20, 2003: 7.80, 2004: 8.50, 2005: 9.80, 2006: 10.50,
    2007: 10.80, 2008: 11.20, 2009: 10.00, 2010: 10.20, 2011: 10.50,
    2012: 9.50, 2013: 10.00, 2014: 10.50, 2015: 9.80, 2016: 9.50,
    2017: 10.00, 2018: 10.20, 2019: 10.10, 2020: 10.00, 2021: 11.70,  # Uri
    2022: 14.46, 2023: 10.04, 2024: 9.79, 2025: 10.50
}

# Texas gas share %
TX_GAS_SHARE = {
    1997: 38, 1998: 40, 1999: 42, 2000: 45, 2001: 44,
    2002: 45, 2003: 46, 2004: 47, 2005: 48, 2006: 49,
    2007: 50, 2008: 49, 2009: 48, 2010: 47, 2011: 46,
    2012: 48, 2013: 47, 2014: 46, 2015: 47, 2016: 45,
    2017: 43, 2018: 44, 2019: 45, 2020: 47, 2021: 42,
    2022: 42, 2023: 42, 2024: 42, 2025: 42
}

# Texas coal share %
TX_COAL_SHARE = {
    1997: 37, 1998: 35, 1999: 33, 2000: 30, 2001: 32,
    2002: 31, 2003: 30, 2004: 29, 2005: 28, 2006: 27,
    2007: 26, 2008: 27, 2009: 28, 2010: 28, 2011: 29,
    2012: 27, 2013: 28, 2014: 28, 2015: 26, 2016: 26,
    2017: 26, 2018: 25, 2019: 22, 2020: 18, 2021: 18,
    2022: 16, 2023: 14, 2024: 13, 2025: 12
}

# Texas renewables % (wind dominant)
TX_RENEWABLES_SHARE = {
    1997: 1, 1998: 1, 1999: 2, 2000: 2, 2001: 2,
    2002: 3, 2003: 4, 2004: 5, 2005: 6, 2006: 7,
    2007: 8, 2008: 8, 2009: 8, 2010: 9, 2011: 10,
    2012: 11, 2013: 12, 2014: 13, 2015: 14, 2016: 16,
    2017: 18, 2018: 19, 2019: 21, 2020: 24, 2021: 26,
    2022: 28, 2023: 32, 2024: 36, 2025: 38
}

# Texas nuclear %
TX_NUCLEAR_SHARE = {
    1997: 12, 1998: 12, 1999: 12, 2000: 12, 2001: 11,
    2002: 11, 2003: 11, 2004: 11, 2005: 10, 2006: 10,
    2007: 10, 2008: 10, 2009: 10, 2010: 10, 2011: 10,
    2012: 10, 2013: 10, 2014: 10, 2015: 10, 2016: 10,
    2017: 10, 2018: 9, 2019: 9, 2020: 9, 2021: 10,
    2022: 10, 2023: 9, 2024: 9, 2025: 9
}

# =============================================================================
# ERA DEFINITIONS
# =============================================================================

ERAS = {
    "Pre-Shale": (1997, 2008),
    "Shale Boom": (2009, 2020),
    "Post-Pandemic": (2021, 2025)
}

# =============================================================================
# DATA PROCESSING FUNCTIONS
# =============================================================================

def build_database() -> List[AnnualDataPoint]:
    """Build complete database from raw data"""
    database = []
    
    years = range(1997, 2026)
    
    for year in years:
        # US Data
        database.append(AnnualDataPoint(
            year=year,
            region="US",
            gas_price_usd_mmbtu=HENRY_HUB_ANNUAL.get(year, 0),
            elec_price_cents_kwh=US_ELEC_ANNUAL.get(year, 0),
            gas_share_pct=US_GAS_SHARE.get(year, 0),
            coal_share_pct=US_COAL_SHARE.get(year, 0),
            renewables_share_pct=US_RENEWABLES_SHARE.get(year, 0),
            nuclear_share_pct=US_NUCLEAR_SHARE.get(year, 0),
            notes=""
        ))
        
        # California Data
        ca_gas = HENRY_HUB_ANNUAL.get(year, 0) + SOCAL_PREMIUM.get(year, 0)
        database.append(AnnualDataPoint(
            year=year,
            region="California",
            gas_price_usd_mmbtu=round(ca_gas, 2),
            elec_price_cents_kwh=CA_ELEC_ANNUAL.get(year, 0),
            gas_share_pct=CA_GAS_SHARE.get(year, 0),
            coal_share_pct=CA_COAL_SHARE.get(year, 0),
            renewables_share_pct=CA_RENEWABLES_SHARE.get(year, 0),
            nuclear_share_pct=CA_NUCLEAR_SHARE.get(year, 0),
            notes="SoCal Citygate hub"
        ))
        
        # Texas Data
        tx_gas = HENRY_HUB_ANNUAL.get(year, 0) + WAHA_SPREAD.get(year, 0)
        database.append(AnnualDataPoint(
            year=year,
            region="Texas",
            gas_price_usd_mmbtu=round(tx_gas, 2),
            elec_price_cents_kwh=TX_ELEC_ANNUAL.get(year, 0),
            gas_share_pct=TX_GAS_SHARE.get(year, 0),
            coal_share_pct=TX_COAL_SHARE.get(year, 0),
            renewables_share_pct=TX_RENEWABLES_SHARE.get(year, 0),
            nuclear_share_pct=TX_NUCLEAR_SHARE.get(year, 0),
            notes="Waha/HSC hub - ERCOT"
        ))
    
    return database


def calculate_correlation(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient"""
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    
    numerator = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    denom_x = sum((x[i] - mean_x) ** 2 for i in range(n)) ** 0.5
    denom_y = sum((y[i] - mean_y) ** 2 for i in range(n)) ** 0.5
    
    if denom_x == 0 or denom_y == 0:
        return 0.0
    
    return round(numerator / (denom_x * denom_y), 3)


def calculate_annualized_trend(values: List[float]) -> float:
    """Calculate annualized percentage change"""
    if len(values) < 2 or values[0] == 0:
        return 0.0
    
    n_years = len(values) - 1
    total_change = (values[-1] / values[0]) - 1
    annualized = ((1 + total_change) ** (1 / n_years)) - 1
    return round(annualized * 100, 2)


def analyze_era(database: List[AnnualDataPoint], era_name: str, 
                start_year: int, end_year: int, region: str) -> EraAnalysis:
    """Analyze a specific era for a region"""
    
    # Filter data
    data = [d for d in database 
            if d.region == region and start_year <= d.year <= end_year]
    
    if not data:
        return None
    
    gas_prices = [d.gas_price_usd_mmbtu for d in data]
    elec_prices = [d.elec_price_cents_kwh for d in data]
    gas_shares = [d.gas_share_pct for d in data]
    
    return EraAnalysis(
        era_name=era_name,
        start_year=start_year,
        end_year=end_year,
        region=region,
        avg_gas_price=round(statistics.mean(gas_prices), 2),
        avg_elec_price=round(statistics.mean(elec_prices), 2),
        gas_price_volatility=round(statistics.stdev(gas_prices), 2) if len(gas_prices) > 1 else 0,
        elec_price_volatility=round(statistics.stdev(elec_prices), 2) if len(elec_prices) > 1 else 0,
        avg_gas_share=round(statistics.mean(gas_shares), 1),
        gas_elec_correlation=calculate_correlation(gas_prices, elec_prices),
        price_trend_gas=calculate_annualized_trend(gas_prices),
        price_trend_elec=calculate_annualized_trend(elec_prices)
    )


def export_to_csv(database: List[AnnualDataPoint], filename: str):
    """Export database to CSV"""
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Year', 'Region', 'Gas_Price_USD_MMBtu', 'Elec_Price_cents_kWh',
            'Gas_Share_Pct', 'Coal_Share_Pct', 'Renewables_Share_Pct', 
            'Nuclear_Share_Pct', 'Notes'
        ])
        for d in database:
            writer.writerow([
                d.year, d.region, d.gas_price_usd_mmbtu, d.elec_price_cents_kwh,
                d.gas_share_pct, d.coal_share_pct, d.renewables_share_pct,
                d.nuclear_share_pct, d.notes
            ])


def print_era_comparison(analyses: List[EraAnalysis]):
    """Print formatted era comparison"""
    print("\n" + "="*80)
    print("ERA ANALYSIS COMPARISON")
    print("="*80)
    
    for era_name in ERAS.keys():
        print(f"\n{'─'*80}")
        print(f"ERA: {era_name} ({ERAS[era_name][0]}-{ERAS[era_name][1]})")
        print(f"{'─'*80}")
        
        era_data = [a for a in analyses if a.era_name == era_name]
        
        print(f"\n{'Region':<12} {'Avg Gas':>10} {'Avg Elec':>10} {'Gas Vol':>10} {'Elec Vol':>10} {'Gas Share':>10} {'Correlation':>12}")
        print(f"{'':12} {'($/MMBtu)':>10} {'(¢/kWh)':>10} {'(std)':>10} {'(std)':>10} {'(%)':>10} {'(gas→elec)':>12}")
        print("-"*76)
        
        for a in era_data:
            print(f"{a.region:<12} {a.avg_gas_price:>10.2f} {a.avg_elec_price:>10.2f} "
                  f"{a.gas_price_volatility:>10.2f} {a.elec_price_volatility:>10.2f} "
                  f"{a.avg_gas_share:>10.1f} {a.gas_elec_correlation:>12.3f}")


def print_trend_analysis(analyses: List[EraAnalysis]):
    """Print trend evolution across eras"""
    print("\n" + "="*80)
    print("TREND EVOLUTION ACROSS ERAS")
    print("="*80)
    
    for region in ["US", "California", "Texas"]:
        print(f"\n{'─'*60}")
        print(f"REGION: {region}")
        print(f"{'─'*60}")
        
        region_data = [a for a in analyses if a.region == region]
        region_data.sort(key=lambda x: x.start_year)
        
        print(f"\n{'Era':<15} {'Gas Trend':>12} {'Elec Trend':>12} {'Correlation':>12}")
        print(f"{'':15} {'(%/year)':>12} {'(%/year)':>12} {'Change':>12}")
        print("-"*55)
        
        prev_corr = None
        for a in region_data:
            corr_change = ""
            if prev_corr is not None:
                diff = a.gas_elec_correlation - prev_corr
                corr_change = f"{diff:+.3f}"
            prev_corr = a.gas_elec_correlation
            
            print(f"{a.era_name:<15} {a.price_trend_gas:>+12.1f} {a.price_trend_elec:>+12.1f} {corr_change:>12}")


def print_key_insights(database: List[AnnualDataPoint], analyses: List[EraAnalysis]):
    """Print key insights from the analysis"""
    print("\n" + "="*80)
    print("KEY INSIGHTS")
    print("="*80)
    
    # 1. Current state comparison
    print("\n📊 CURRENT STATE (2024)")
    print("-"*40)
    current = [d for d in database if d.year == 2024]
    for d in current:
        print(f"  {d.region:12}: Gas ${d.gas_price_usd_mmbtu:.2f}/MMBtu | "
              f"Elec {d.elec_price_cents_kwh:.1f}¢/kWh | "
              f"Gas {d.gas_share_pct}% | Renew {d.renewables_share_pct}%")
    
    # 2. Correlation evolution
    print("\n📈 GAS-ELECTRICITY CORRELATION EVOLUTION")
    print("-"*40)
    for region in ["US", "California", "Texas"]:
        region_analyses = sorted([a for a in analyses if a.region == region], 
                                  key=lambda x: x.start_year)
        corrs = [f"{a.era_name}: {a.gas_elec_correlation:.3f}" for a in region_analyses]
        print(f"  {region:12}: {' → '.join(corrs)}")
    
    # 3. Price divergence
    print("\n💰 REGIONAL PRICE DIVERGENCE (vs US Average)")
    print("-"*40)
    us_2024 = next(d for d in database if d.year == 2024 and d.region == "US")
    for region in ["California", "Texas"]:
        r_2024 = next(d for d in database if d.year == 2024 and d.region == region)
        gas_diff = r_2024.gas_price_usd_mmbtu - us_2024.gas_price_usd_mmbtu
        elec_diff = r_2024.elec_price_cents_kwh - us_2024.elec_price_cents_kwh
        print(f"  {region:12}: Gas {gas_diff:+.2f} $/MMBtu | Elec {elec_diff:+.1f} ¢/kWh")
    
    # 4. Generation mix shift
    print("\n🔄 GENERATION MIX SHIFT (1997 → 2024)")
    print("-"*40)
    for region in ["US", "California", "Texas"]:
        d_1997 = next(d for d in database if d.year == 1997 and d.region == region)
        d_2024 = next(d for d in database if d.year == 2024 and d.region == region)
        print(f"  {region:12}: Coal {d_1997.coal_share_pct}%→{d_2024.coal_share_pct}% | "
              f"Gas {d_1997.gas_share_pct}%→{d_2024.gas_share_pct}% | "
              f"Renew {d_1997.renewables_share_pct}%→{d_2024.renewables_share_pct}%")


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    print("DecarbIQ Gas-Electricity Analysis Tool")
    print("="*80)
    print(f"Analysis Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("Regions: US Average, California (CAISO), Texas (ERCOT)")
    print("Period: 1997-2025")
    print("="*80)
    
    # Build database
    print("\n[1/4] Building database...")
    database = build_database()
    print(f"      Created {len(database)} data points")
    
    # Export to CSV
    csv_path = "/mnt/user-data/outputs/decarbiq_full_database.csv"
    print(f"\n[2/4] Exporting to {csv_path}...")
    export_to_csv(database, csv_path)
    print("      Done")
    
    # Run era analysis
    print("\n[3/4] Running era analysis...")
    analyses = []
    for era_name, (start, end) in ERAS.items():
        for region in ["US", "California", "Texas"]:
            analysis = analyze_era(database, era_name, start, end, region)
            if analysis:
                analyses.append(analysis)
    print(f"      Completed {len(analyses)} era-region analyses")
    
    # Print results
    print("\n[4/4] Generating reports...")
    print_era_comparison(analyses)
    print_trend_analysis(analyses)
    print_key_insights(database, analyses)
    
    print("\n" + "="*80)
    print("Analysis complete!")
    print("="*80)
    
    return database, analyses


if __name__ == "__main__":
    database, analyses = main()
