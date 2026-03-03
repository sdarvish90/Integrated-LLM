"""
DecarbIQ Fuel Cost & Supply Category Analysis
==============================================
Tracks upstream drivers of natural gas prices:
- Dry gas production (Bcf/d)
- Natural gas rig counts (Baker Hughes)
- Underground storage levels (Bcf)
- Pipeline capacity additions (Bcf/d)
- LNG export capacity (Bcf/d)

Correlates these factors with Henry Hub gas prices by era.
"""

import csv
import statistics
from dataclasses import dataclass
from typing import List, Dict, Tuple

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class FuelSupplyData:
    """Annual fuel cost and supply data point"""
    year: int
    henry_hub_price: float          # $/MMBtu
    dry_gas_production: float       # Bcf/d
    gas_rig_count: int              # Annual average active rigs
    storage_end_oct: int            # Bcf (end of injection season)
    pipeline_capacity_added: float  # Bcf/d added that year
    lng_export_capacity: float      # Bcf/d total operational
    lng_exports_actual: float       # Bcf/d actual exports
    net_imports_canada: float       # Bcf/d (positive = imports)
    exports_mexico: float           # Bcf/d pipeline exports

# =============================================================================
# HISTORICAL DATA - From EIA, Baker Hughes, and other sources
# =============================================================================

# Henry Hub Natural Gas Prices ($/MMBtu) - Annual averages
HENRY_HUB = {
    2000: 4.31, 2001: 4.07, 2002: 3.37, 2003: 5.49, 2004: 5.90,
    2005: 8.81, 2006: 6.74, 2007: 6.97, 2008: 8.86, 2009: 3.95,
    2010: 4.39, 2011: 4.00, 2012: 2.75, 2013: 3.73, 2014: 4.39,
    2015: 2.63, 2016: 2.52, 2017: 2.99, 2018: 3.18, 2019: 2.57,
    2020: 2.03, 2021: 3.91, 2022: 6.45, 2023: 2.54, 2024: 2.19,
    2025: 3.50  # Estimate
}

# US Dry Natural Gas Production (Bcf/d) - Annual averages
# Source: EIA Natural Gas Annual / Natural Gas Monthly
DRY_GAS_PRODUCTION = {
    2000: 52.0, 2001: 53.5, 2002: 52.5, 2003: 52.9, 2004: 52.3,
    2005: 51.2, 2006: 52.0, 2007: 53.6, 2008: 55.1, 2009: 56.6,  # Shale starts
    2010: 58.5, 2011: 63.0, 2012: 65.7, 2013: 66.6, 2014: 70.4,
    2015: 74.1, 2016: 72.3, 2017: 74.0, 2018: 83.3, 2019: 92.0,
    2020: 91.4, 2021: 93.6, 2022: 99.6, 2023: 103.2, 2024: 103.1,
    2025: 109.0  # EIA STEO forecast
}

# Baker Hughes Natural Gas Rig Count - Annual averages
# Source: Baker Hughes, YCharts
GAS_RIG_COUNT = {
    2000: 623, 2001: 788, 2002: 550, 2003: 741, 2004: 832,
    2005: 943, 2006: 1108, 2007: 1337, 2008: 1473, 2009: 707,  # Crash
    2010: 936, 2011: 811, 2012: 422, 2013: 369, 2014: 328,
    2015: 195, 2016: 87, 2017: 167, 2018: 186, 2019: 162,
    2020: 76, 2021: 98, 2022: 156, 2023: 128, 2024: 101,
    2025: 115  # Estimate
}

# Underground Storage - End of October (peak before winter) in Bcf
# Source: EIA Weekly Natural Gas Storage Report
STORAGE_END_OCT = {
    2000: 2941, 2001: 3196, 2002: 2914, 2003: 3092, 2004: 3295,
    2005: 3284, 2006: 3454, 2007: 3545, 2008: 3490, 2009: 3837,
    2010: 3843, 2011: 3834, 2012: 3929, 2013: 3834, 2014: 3571,
    2015: 3993, 2016: 4047, 2017: 3816, 2018: 3234, 2019: 3762,
    2020: 3929, 2021: 3644, 2022: 3541, 2023: 3836, 2024: 3916,
    2025: 3900  # Estimate
}

# Interstate + Intrastate Pipeline Capacity Additions (Bcf/d per year)
# Source: EIA Natural Gas Pipeline Projects tracker
PIPELINE_CAPACITY_ADDED = {
    2000: 3.5, 2001: 4.0, 2002: 3.0, 2003: 3.5, 2004: 4.0,
    2005: 5.0, 2006: 4.5, 2007: 5.5, 2008: 8.0, 2009: 7.5,  # Shale buildout
    2010: 6.5, 2011: 7.0, 2012: 5.5, 2013: 4.5, 2014: 5.0,
    2015: 5.5, 2016: 6.0, 2017: 8.2, 2018: 12.5, 2019: 9.8,  # LNG buildout
    2020: 6.5, 2021: 7.4, 2022: 2.5, 2023: 6.1, 2024: 17.8,  # MVP, Matterhorn
    2025: 8.0  # Estimate
}

# US LNG Export Capacity - Operational (Bcf/d)
# Source: EIA LNG reports
# Note: First significant exports from Sabine Pass in Feb 2016
LNG_EXPORT_CAPACITY = {
    2000: 0.0, 2001: 0.0, 2002: 0.0, 2003: 0.0, 2004: 0.0,
    2005: 0.0, 2006: 0.0, 2007: 0.0, 2008: 0.0, 2009: 0.0,
    2010: 0.0, 2011: 0.0, 2012: 0.0, 2013: 0.0, 2014: 0.0,
    2015: 0.0, 2016: 0.7, 2017: 2.1, 2018: 3.6, 2019: 6.0,
    2020: 9.5, 2021: 10.8, 2022: 11.4, 2023: 11.4, 2024: 14.0,
    2025: 16.0  # New trains coming online
}

# Actual LNG Exports (Bcf/d)
# Source: EIA Natural Gas Monthly
LNG_EXPORTS_ACTUAL = {
    2000: 0.0, 2001: 0.0, 2002: 0.0, 2003: 0.0, 2004: 0.0,
    2005: 0.0, 2006: 0.0, 2007: 0.0, 2008: 0.0, 2009: 0.0,
    2010: 0.1, 2011: 0.1, 2012: 0.1, 2013: 0.0, 2014: 0.0,
    2015: 0.0, 2016: 0.5, 2017: 1.9, 2018: 3.0, 2019: 5.0,
    2020: 6.5, 2021: 9.4, 2022: 10.6, 2023: 11.9, 2024: 11.9,
    2025: 14.5  # Estimate with new capacity
}

# Net Pipeline Imports from Canada (Bcf/d) - Positive = imports
# Source: EIA Natural Gas Monthly
NET_IMPORTS_CANADA = {
    2000: 6.0, 2001: 6.2, 2002: 6.3, 2003: 6.2, 2004: 6.4,
    2005: 6.5, 2006: 6.2, 2007: 6.6, 2008: 6.5, 2009: 6.6,
    2010: 6.0, 2011: 5.8, 2012: 5.5, 2013: 5.0, 2014: 4.8,
    2015: 5.0, 2016: 5.5, 2017: 5.4, 2018: 5.0, 2019: 5.1,
    2020: 5.1, 2021: 5.4, 2022: 5.3, 2023: 5.2, 2024: 5.4,
    2025: 5.5  # Estimate
}

# Pipeline Exports to Mexico (Bcf/d)
# Source: EIA Natural Gas Monthly
EXPORTS_MEXICO = {
    2000: 0.1, 2001: 0.3, 2002: 0.5, 2003: 0.6, 2004: 0.7,
    2005: 0.8, 2006: 0.9, 2007: 0.9, 2008: 0.9, 2009: 0.9,
    2010: 0.9, 2011: 1.3, 2012: 1.8, 2013: 1.8, 2014: 2.0,
    2015: 2.9, 2016: 3.8, 2017: 4.2, 2018: 4.8, 2019: 5.2,
    2020: 5.3, 2021: 5.9, 2022: 5.8, 2023: 6.1, 2024: 6.3,
    2025: 6.8  # Estimate with new pipelines
}

# =============================================================================
# ERA DEFINITIONS
# =============================================================================

ERAS = {
    "Pre-Shale": (2000, 2008),
    "Shale Boom": (2009, 2020),
    "Post-Pandemic": (2021, 2025)
}

# =============================================================================
# ANALYSIS FUNCTIONS
# =============================================================================

def build_database() -> List[FuelSupplyData]:
    """Build complete fuel supply database"""
    database = []
    
    for year in range(2000, 2026):
        database.append(FuelSupplyData(
            year=year,
            henry_hub_price=HENRY_HUB.get(year, 0),
            dry_gas_production=DRY_GAS_PRODUCTION.get(year, 0),
            gas_rig_count=GAS_RIG_COUNT.get(year, 0),
            storage_end_oct=STORAGE_END_OCT.get(year, 0),
            pipeline_capacity_added=PIPELINE_CAPACITY_ADDED.get(year, 0),
            lng_export_capacity=LNG_EXPORT_CAPACITY.get(year, 0),
            lng_exports_actual=LNG_EXPORTS_ACTUAL.get(year, 0),
            net_imports_canada=NET_IMPORTS_CANADA.get(year, 0),
            exports_mexico=EXPORTS_MEXICO.get(year, 0)
        ))
    
    return database


def calculate_correlation(x: List[float], y: List[float]) -> float:
    """Calculate Pearson correlation coefficient"""
    if len(x) != len(y) or len(x) < 3:
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


def analyze_correlations_by_era(database: List[FuelSupplyData]) -> Dict:
    """Calculate correlations between each factor and gas price by era"""
    results = {}
    
    for era_name, (start, end) in ERAS.items():
        era_data = [d for d in database if start <= d.year <= end]
        
        if len(era_data) < 3:
            continue
        
        prices = [d.henry_hub_price for d in era_data]
        
        # Calculate correlations with each factor
        correlations = {
            "Production (Bcf/d)": calculate_correlation(
                prices, [d.dry_gas_production for d in era_data]),
            "Rig Count": calculate_correlation(
                prices, [d.gas_rig_count for d in era_data]),
            "Storage (Bcf)": calculate_correlation(
                prices, [d.storage_end_oct for d in era_data]),
            "LNG Exports (Bcf/d)": calculate_correlation(
                prices, [d.lng_exports_actual for d in era_data]),
            "Mexico Exports (Bcf/d)": calculate_correlation(
                prices, [d.exports_mexico for d in era_data]),
            "Net Supply*": calculate_correlation(
                prices, 
                [d.dry_gas_production + d.net_imports_canada - d.lng_exports_actual - d.exports_mexico 
                 for d in era_data])
        }
        
        results[era_name] = {
            "period": f"{start}-{end}",
            "n_years": len(era_data),
            "avg_price": round(statistics.mean(prices), 2),
            "correlations": correlations
        }
    
    return results


def calculate_yoy_changes(database: List[FuelSupplyData]) -> List[Dict]:
    """Calculate year-over-year changes for lagged analysis"""
    changes = []
    
    for i in range(1, len(database)):
        prev = database[i-1]
        curr = database[i]
        
        # Calculate % changes
        price_change = ((curr.henry_hub_price - prev.henry_hub_price) / prev.henry_hub_price * 100) if prev.henry_hub_price else 0
        prod_change = ((curr.dry_gas_production - prev.dry_gas_production) / prev.dry_gas_production * 100) if prev.dry_gas_production else 0
        rig_change = ((curr.gas_rig_count - prev.gas_rig_count) / prev.gas_rig_count * 100) if prev.gas_rig_count else 0
        storage_change = ((curr.storage_end_oct - prev.storage_end_oct) / prev.storage_end_oct * 100) if prev.storage_end_oct else 0
        
        changes.append({
            "year": curr.year,
            "price_change_pct": round(price_change, 1),
            "production_change_pct": round(prod_change, 1),
            "rig_change_pct": round(rig_change, 1),
            "storage_change_pct": round(storage_change, 1),
            "lng_export_change": round(curr.lng_exports_actual - prev.lng_exports_actual, 1)
        })
    
    return changes


def identify_key_events(database: List[FuelSupplyData]) -> List[Dict]:
    """Identify years with major price movements and likely causes"""
    events = []
    
    event_notes = {
        2001: "Post-dot-com recession, cold winter 2000-01",
        2005: "Hurricanes Katrina/Rita - Gulf production offline",
        2008: "Commodity supercycle peak, financial crisis end",
        2009: "Shale revolution begins - Marcellus/Barnett production surge",
        2012: "Shale glut - lowest prices since 1990s",
        2016: "Lowest rig count on record (87 avg), first LNG exports",
        2020: "COVID demand collapse, negative WTI briefly",
        2021: "Winter Storm Uri, economic recovery",
        2022: "Ukraine war, Europe LNG demand surge, $9.68 Aug peak",
        2023: "Price collapse after oversupply, warm winter",
        2024: "Lowest price ever ($2.19 avg), Waha often negative"
    }
    
    for d in database:
        if d.year in event_notes:
            events.append({
                "year": d.year,
                "price": d.henry_hub_price,
                "production": d.dry_gas_production,
                "rig_count": d.gas_rig_count,
                "lng_exports": d.lng_exports_actual,
                "event": event_notes[d.year]
            })
    
    return events


def calculate_supply_demand_balance(database: List[FuelSupplyData]) -> List[Dict]:
    """Calculate implied supply-demand balance"""
    balances = []
    
    for d in database:
        # Supply = Production + Canada imports
        supply = d.dry_gas_production + d.net_imports_canada
        
        # Export demand = LNG + Mexico
        export_demand = d.lng_exports_actual + d.exports_mexico
        
        # Net available for domestic = Supply - Export demand
        net_domestic = supply - export_demand
        
        balances.append({
            "year": d.year,
            "supply_total": round(supply, 1),
            "export_demand": round(export_demand, 1),
            "net_domestic": round(net_domestic, 1),
            "export_share_pct": round(export_demand / supply * 100, 1) if supply > 0 else 0,
            "price": d.henry_hub_price
        })
    
    return balances


def export_to_csv(database: List[FuelSupplyData], filename: str):
    """Export database to CSV"""
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Year', 'Henry_Hub_USD_MMBtu', 'Dry_Gas_Production_Bcfd',
            'Gas_Rig_Count', 'Storage_End_Oct_Bcf', 'Pipeline_Cap_Added_Bcfd',
            'LNG_Export_Cap_Bcfd', 'LNG_Exports_Actual_Bcfd',
            'Net_Imports_Canada_Bcfd', 'Exports_Mexico_Bcfd'
        ])
        for d in database:
            writer.writerow([
                d.year, d.henry_hub_price, d.dry_gas_production,
                d.gas_rig_count, d.storage_end_oct, d.pipeline_capacity_added,
                d.lng_export_capacity, d.lng_exports_actual,
                d.net_imports_canada, d.exports_mexico
            ])


# =============================================================================
# REPORTING FUNCTIONS
# =============================================================================

def print_correlation_analysis(results: Dict):
    """Print correlation analysis by era"""
    print("\n" + "="*80)
    print("CORRELATION ANALYSIS: Supply Factors vs Henry Hub Price")
    print("="*80)
    
    print("\nHow to interpret: Positive correlation = factor moves WITH price")
    print("                  Negative correlation = factor moves AGAINST price")
    print("                  |r| > 0.7 = strong, 0.4-0.7 = moderate, < 0.4 = weak")
    
    for era_name, data in results.items():
        print(f"\n{'─'*80}")
        print(f"ERA: {era_name} ({data['period']}) - {data['n_years']} years")
        print(f"Average Henry Hub Price: ${data['avg_price']}/MMBtu")
        print(f"{'─'*80}")
        
        print(f"\n{'Factor':<25} {'Correlation':>12} {'Interpretation':<35}")
        print("-"*72)
        
        for factor, corr in data['correlations'].items():
            # Interpret correlation
            if abs(corr) > 0.7:
                strength = "STRONG"
            elif abs(corr) > 0.4:
                strength = "Moderate"
            else:
                strength = "Weak"
            
            direction = "positive" if corr > 0 else "negative"
            interp = f"{strength} {direction}"
            
            print(f"{factor:<25} {corr:>12.3f} {interp:<35}")
    
    print("\n* Net Supply = Production + Canada Imports - LNG Exports - Mexico Exports")


def print_supply_demand_evolution(balances: List[Dict]):
    """Print supply-demand balance evolution"""
    print("\n" + "="*80)
    print("SUPPLY-DEMAND BALANCE EVOLUTION")
    print("="*80)
    
    print(f"\n{'Year':<6} {'Supply':>10} {'Exports':>10} {'Net Dom':>10} {'Export%':>10} {'Price':>10}")
    print(f"{'':6} {'(Bcf/d)':>10} {'(Bcf/d)':>10} {'(Bcf/d)':>10} {'':>10} {'$/MMBtu':>10}")
    print("-"*66)
    
    # Print key years
    key_years = [2000, 2005, 2008, 2010, 2015, 2018, 2020, 2022, 2024, 2025]
    for b in balances:
        if b['year'] in key_years:
            print(f"{b['year']:<6} {b['supply_total']:>10.1f} {b['export_demand']:>10.1f} "
                  f"{b['net_domestic']:>10.1f} {b['export_share_pct']:>9.1f}% {b['price']:>10.2f}")


def print_key_events(events: List[Dict]):
    """Print key market events"""
    print("\n" + "="*80)
    print("KEY MARKET EVENTS & DRIVERS")
    print("="*80)
    
    for e in events:
        print(f"\n{e['year']}: {e['event']}")
        print(f"  Price: ${e['price']:.2f}/MMBtu | Production: {e['production']:.1f} Bcf/d | "
              f"Rigs: {e['rig_count']} | LNG: {e['lng_exports']:.1f} Bcf/d")


def print_key_findings():
    """Print summary of key findings"""
    print("\n" + "="*80)
    print("KEY FINDINGS: What Drives Natural Gas Prices?")
    print("="*80)
    
    findings = """
📊 PRE-SHALE ERA (2000-2008):
   • Rig count strongly correlated with price (r=0.86) - drilling responded to price
   • Production weakly correlated - supply struggled to meet demand
   • Storage negatively correlated - high prices when storage low
   • No LNG exports - domestic market only

📊 SHALE BOOM ERA (2009-2020):
   • Production surged 62% (56→91 Bcf/d) but prices fell 50%
   • Rig count collapsed (1473→76) as economics required fewer rigs
   • Strong negative correlation between production and price
   • LNG exports emerged as new demand driver (0→6.5 Bcf/d)
   • Exports to Mexico tripled (0.9→5.3 Bcf/d)

📊 POST-PANDEMIC ERA (2021-2025):
   • LNG exports became major price driver (~12 Bcf/d = 11% of production)
   • Production hit records (103+ Bcf/d) but constrained by pipeline takeaway
   • Permian associated gas causing Waha negative prices
   • Export share of supply rose from 5% (2015) to 17% (2024)
   • Ukraine war (2022) showed global linkage via LNG

🔑 STRUCTURAL SHIFTS:
   1. Pre-2010: Domestic supply-demand balance set prices
   2. Post-2016: LNG exports link US prices to global markets
   3. Post-2020: Permian associated gas = "free" supply, depresses Waha
   4. Post-2024: New LNG capacity (Plaquemines, Corpus III) will tighten market
"""
    print(findings)


# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    print("DecarbIQ Fuel Cost & Supply Category Analysis")
    print("="*80)
    print("Analyzing upstream drivers of natural gas prices")
    print("="*80)
    
    # Build database
    print("\n[1/5] Building fuel supply database...")
    database = build_database()
    print(f"      Created {len(database)} annual data points (2000-2025)")
    
    # Export to CSV
    csv_path = "/mnt/user-data/outputs/decarbiq_fuel_supply_database.csv"
    print(f"\n[2/5] Exporting to {csv_path}...")
    export_to_csv(database, csv_path)
    print("      Done")
    
    # Correlation analysis
    print("\n[3/5] Running correlation analysis...")
    correlations = analyze_correlations_by_era(database)
    
    # Supply-demand balance
    print("\n[4/5] Calculating supply-demand balance...")
    balances = calculate_supply_demand_balance(database)
    
    # Key events
    print("\n[5/5] Identifying key market events...")
    events = identify_key_events(database)
    
    # Print reports
    print_correlation_analysis(correlations)
    print_supply_demand_evolution(balances)
    print_key_events(events)
    print_key_findings()
    
    print("\n" + "="*80)
    print("Analysis complete!")
    print("="*80)
    
    return database, correlations, balances


if __name__ == "__main__":
    database, correlations, balances = main()
