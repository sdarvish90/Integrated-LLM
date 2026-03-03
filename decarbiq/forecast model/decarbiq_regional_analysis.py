"""
DecarbIQ Regional Regression Analysis
======================================
Analyzes upstream factors against REGIONAL gas prices (not just Henry Hub)
- Waha (Texas/Permian)
- SoCal Citygate (California)
- Henry Hub (National benchmark)

Then traces through to regional electricity prices.

Author: DecarbIQ Analysis Tool
Date: 2026-02-09
"""

import csv
import math
from typing import List, Dict, Tuple, Optional

# =============================================================================
# STATISTICAL FUNCTIONS (same as before)
# =============================================================================

def mean(x: List[float]) -> float:
    return sum(x) / len(x) if x else 0.0

def variance(x: List[float]) -> float:
    if len(x) < 2:
        return 0.0
    m = mean(x)
    return sum((xi - m) ** 2 for xi in x) / (len(x) - 1)

def std_dev(x: List[float]) -> float:
    return math.sqrt(variance(x))

def covariance(x: List[float], y: List[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx, my = mean(x), mean(y)
    return sum((x[i] - mx) * (y[i] - my) for i in range(len(x))) / (len(x) - 1)

def correlation(x: List[float], y: List[float]) -> float:
    if len(x) != len(y) or len(x) < 3:
        return 0.0
    sx, sy = std_dev(x), std_dev(y)
    if sx == 0 or sy == 0:
        return 0.0
    return covariance(x, y) / (sx * sy)

def simple_ols(x: List[float], y: List[float]) -> Dict:
    """Simple OLS regression: y = alpha + beta * x"""
    n = len(x)
    if n < 3 or len(y) != n:
        return {"error": "Insufficient data", "n": n}
    
    mx, my = mean(x), mean(y)
    
    numerator = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    denominator = sum((x[i] - mx) ** 2 for i in range(n))
    
    if denominator == 0:
        return {"error": "No variance in X", "n": n}
    
    beta = numerator / denominator
    alpha = my - beta * mx
    
    y_pred = [alpha + beta * x[i] for i in range(n)]
    residuals = [y[i] - y_pred[i] for i in range(n)]
    
    ss_res = sum(r ** 2 for r in residuals)
    ss_tot = sum((y[i] - my) ** 2 for i in range(n))
    
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    adj_r_squared = 1 - (1 - r_squared) * (n - 1) / (n - 2) if n > 2 else r_squared
    
    mse = ss_res / (n - 2) if n > 2 else 0
    se_regression = math.sqrt(mse)
    se_beta = se_regression / math.sqrt(denominator) if denominator > 0 else 0
    
    t_stat = beta / se_beta if se_beta > 0 else 0
    df = n - 2
    p_value = approximate_p_value(t_stat, df)
    
    return {
        "alpha": round(alpha, 6),
        "beta": round(beta, 6),
        "r_squared": round(r_squared, 4),
        "adj_r_squared": round(adj_r_squared, 4),
        "std_err_beta": round(se_beta, 6),
        "t_stat": round(t_stat, 3),
        "p_value": round(p_value, 4),
        "n": n,
        "df": df,
        "se_regression": round(se_regression, 4),
        "significant_05": p_value < 0.05,
        "significant_01": p_value < 0.01
    }

def approximate_p_value(t: float, df: int) -> float:
    if df < 1:
        return 1.0
    t = abs(t)
    if df <= 30:
        if t < 1.0: return 0.5
        elif t < 1.5: return 0.2
        elif t < 2.0: return 0.1
        elif t < 2.5: return 0.05
        elif t < 3.0: return 0.02
        elif t < 3.5: return 0.01
        elif t < 4.0: return 0.005
        else: return 0.001
    else:
        if t < 1.64: return 0.1
        elif t < 1.96: return 0.05
        elif t < 2.58: return 0.01
        else: return 0.001

# =============================================================================
# DATA
# =============================================================================

YEARS = list(range(2000, 2026))

# NATIONAL UPSTREAM FACTORS
PRODUCTION = [52.0, 53.5, 52.5, 52.9, 52.3, 51.2, 52.0, 53.6, 55.1, 56.6,
              58.5, 63.0, 65.7, 66.6, 70.4, 74.1, 72.3, 74.0, 83.3, 92.0,
              91.4, 93.6, 99.6, 103.2, 103.1, 109.0]

RIG_COUNT = [623, 788, 550, 741, 832, 943, 1108, 1337, 1473, 707,
             936, 811, 422, 369, 328, 195, 87, 167, 186, 162,
             76, 98, 156, 128, 101, 115]

STORAGE = [2941, 3196, 2914, 3092, 3295, 3284, 3454, 3545, 3490, 3837,
           3843, 3834, 3929, 3834, 3571, 3993, 4047, 3816, 3234, 3762,
           3929, 3644, 3541, 3836, 3916, 3900]

LNG_EXPORTS = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
               0.1, 0.1, 0.1, 0.0, 0.0, 0.0, 0.5, 1.9, 3.0, 5.0,
               6.5, 9.4, 10.6, 11.9, 11.9, 14.5]

MEXICO_EXPORTS = [0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.9, 0.9, 0.9,
                  0.9, 1.3, 1.8, 1.8, 2.0, 2.9, 3.8, 4.2, 4.8, 5.2,
                  5.3, 5.9, 5.8, 6.1, 6.3, 6.8]

PIPELINE_CAP = [3.5, 4.0, 3.0, 3.5, 4.0, 5.0, 4.5, 5.5, 8.0, 7.5,
                6.5, 7.0, 5.5, 4.5, 5.0, 5.5, 6.0, 8.2, 12.5, 9.8,
                6.5, 7.4, 2.5, 6.1, 17.8, 8.0]

# REGIONAL GAS PRICES
HENRY_HUB = [4.31, 4.07, 3.37, 5.49, 5.90, 8.81, 6.74, 6.97, 8.86, 3.95,
             4.39, 4.00, 2.75, 3.73, 4.39, 2.63, 2.52, 2.99, 3.18, 2.57,
             2.03, 3.91, 6.45, 2.54, 2.19, 3.50]

# Waha (Texas/Permian) - typically discount to Henry Hub
# Based on EIA data and spread estimates
WAHA = [4.21, 3.92, 3.22, 5.29, 5.70, 8.51, 6.49, 6.77, 8.51, 3.80,
        4.19, 3.80, 2.60, 3.48, 4.19, 2.48, 2.42, 2.79, 2.98, 2.42,
        1.88, 3.81, 6.20, 2.39, 1.99, 3.20]

# SoCal Citygate - typically premium to Henry Hub
# Based on EIA data and spread estimates  
SOCAL_CITYGATE = [7.51, 8.07, 4.47, 6.49, 7.00, 10.51, 8.04, 8.47, 10.46, 4.95,
                  5.49, 5.20, 3.50, 4.83, 5.49, 3.48, 3.22, 3.79, 4.48, 3.47,
                  2.98, 5.51, 11.95, 6.54, 4.54, 5.00]

# REGIONAL ELECTRICITY PRICES
US_ELEC = [6.81, 7.29, 7.20, 7.44, 7.61, 8.14, 8.90, 9.13, 9.74, 9.82,
           9.83, 9.90, 9.84, 10.07, 10.44, 10.41, 10.27, 10.48, 10.53, 10.54,
           10.59, 10.98, 12.55, 12.68, 12.94, 13.50]

CA_ELEC = [9.50, 12.00, 11.50, 11.80, 12.20, 12.80, 13.50, 13.80, 14.50, 15.00,
           14.80, 15.20, 15.00, 15.80, 16.50, 16.00, 16.50, 17.50, 18.00, 19.00,
           19.90, 22.00, 26.50, 24.87, 27.04, 28.50]

TX_ELEC = [7.50, 7.80, 7.20, 7.80, 8.50, 9.80, 10.50, 10.80, 11.20, 10.00,
           10.20, 10.50, 9.50, 10.00, 10.50, 9.80, 9.50, 10.00, 10.20, 10.10,
           10.00, 11.70, 14.46, 10.04, 9.79, 10.50]

# Era definitions
ERA_INDICES = {
    "Pre-Shale": (0, 9),      # 2000-2008
    "Shale Boom": (9, 21),    # 2009-2020
    "Post-Pandemic": (21, 26) # 2021-2025
}

# Derived variables
def calc_waha_basis():
    """Waha discount to Henry Hub"""
    return [WAHA[i] - HENRY_HUB[i] for i in range(len(YEARS))]

def calc_socal_basis():
    """SoCal premium to Henry Hub"""
    return [SOCAL_CITYGATE[i] - HENRY_HUB[i] for i in range(len(YEARS))]

def calc_total_exports():
    """LNG + Mexico exports"""
    return [LNG_EXPORTS[i] + MEXICO_EXPORTS[i] for i in range(len(YEARS))]

WAHA_BASIS = calc_waha_basis()
SOCAL_BASIS = calc_socal_basis()
TOTAL_EXPORTS = calc_total_exports()

# =============================================================================
# ANALYSIS
# =============================================================================

def run_regional_analysis():
    """Run full regional analysis"""
    
    print("="*100)
    print("DecarbIQ REGIONAL REGRESSION ANALYSIS")
    print("="*100)
    print("\nAnalyzing upstream factors against REGIONAL gas prices, then to electricity prices")
    print("All results include: coefficient (β), standard error, t-stat, p-value, R², n")
    print("Significance: * p<0.05, ** p<0.01")
    
    # Define factors
    factors = {
        "Production (Bcf/d)": PRODUCTION,
        "Rig Count": RIG_COUNT,
        "Storage (Bcf)": STORAGE,
        "LNG Exports (Bcf/d)": LNG_EXPORTS,
        "Mexico Exports (Bcf/d)": MEXICO_EXPORTS,
        "Total Exports (Bcf/d)": TOTAL_EXPORTS,
        "Pipeline Cap Added (Bcf/d)": PIPELINE_CAP,
    }
    
    # Define regional gas prices
    gas_prices = {
        "Henry Hub (National)": HENRY_HUB,
        "Waha (Texas/Permian)": WAHA,
        "SoCal Citygate (California)": SOCAL_CITYGATE,
        "Waha Basis (Waha - HH)": WAHA_BASIS,
        "SoCal Basis (SoCal - HH)": SOCAL_BASIS,
    }
    
    # Define electricity prices
    elec_prices = {
        "US Average": US_ELEC,
        "Texas": TX_ELEC,
        "California": CA_ELEC,
    }
    
    all_results = {}
    
    # ==========================================================================
    # PART 1: UPSTREAM FACTORS → REGIONAL GAS PRICES
    # ==========================================================================
    
    print("\n" + "="*100)
    print("PART 1: UPSTREAM FACTORS → REGIONAL GAS PRICES (by Era)")
    print("="*100)
    
    for gas_name, gas_data in gas_prices.items():
        print(f"\n{'─'*100}")
        print(f"DEPENDENT VARIABLE: {gas_name}")
        print(f"{'─'*100}")
        
        gas_results = {}
        
        for era_name, (start, end) in ERA_INDICES.items():
            print(f"\n  ERA: {era_name} ({YEARS[start]}-{YEARS[end-1]}), n={end-start}")
            print(f"  {'Factor':<30} {'β':>12} {'Std Err':>10} {'t-stat':>8} {'p-val':>7} {'R²':>7} {'Sig':>5}")
            print(f"  {'-'*85}")
            
            y = gas_data[start:end]
            era_results = {}
            
            for factor_name, factor_data in factors.items():
                x = factor_data[start:end]
                
                # Check for zero variance (e.g., LNG exports pre-2016)
                if std_dev(x) == 0:
                    print(f"  {factor_name:<30} {'N/A - No variance in X':>50}")
                    era_results[factor_name] = {"error": "No variance", "n": len(x)}
                    continue
                
                result = simple_ols(x, y)
                
                if "error" in result:
                    print(f"  {factor_name:<30} {'ERROR: ' + result['error']:>50}")
                else:
                    sig = "**" if result["significant_01"] else ("*" if result["significant_05"] else "")
                    print(f"  {factor_name:<30} {result['beta']:>12.6f} {result['std_err_beta']:>10.6f} "
                          f"{result['t_stat']:>8.2f} {result['p_value']:>7.3f} {result['r_squared']:>7.3f} {sig:>5}")
                
                era_results[factor_name] = result
            
            gas_results[era_name] = era_results
        
        all_results[gas_name] = gas_results
    
    # ==========================================================================
    # PART 2: REGIONAL GAS PRICES → REGIONAL ELECTRICITY PRICES
    # ==========================================================================
    
    print("\n" + "="*100)
    print("PART 2: REGIONAL GAS PRICES → REGIONAL ELECTRICITY PRICES (by Era)")
    print("="*100)
    
    # Define which gas price to use for which region
    gas_elec_pairs = [
        ("Henry Hub (National)", "US Average", HENRY_HUB, US_ELEC),
        ("Waha (Texas/Permian)", "Texas", WAHA, TX_ELEC),
        ("SoCal Citygate (California)", "California", SOCAL_CITYGATE, CA_ELEC),
    ]
    
    gas_elec_results = {}
    
    for gas_name, elec_name, gas_data, elec_data in gas_elec_pairs:
        print(f"\n{'─'*100}")
        print(f"{gas_name} → {elec_name} Electricity")
        print(f"{'─'*100}")
        
        pair_results = {}
        
        for era_name, (start, end) in ERA_INDICES.items():
            x = gas_data[start:end]
            y = elec_data[start:end]
            
            result = simple_ols(x, y)
            
            if "error" in result:
                print(f"\n  {era_name}: ERROR - {result['error']}")
            else:
                sig = "**" if result["significant_01"] else ("*" if result["significant_05"] else "")
                print(f"\n  {era_name} ({YEARS[start]}-{YEARS[end-1]}): n={result['n']}")
                print(f"    Elasticity: {result['beta']:.4f} ¢/kWh per $/MMBtu gas {sig}")
                print(f"    Intercept:  {result['alpha']:.4f} ¢/kWh (base cost when gas = $0)")
                print(f"    R-squared:  {result['r_squared']:.4f}")
                print(f"    t-stat: {result['t_stat']:.2f}, p-value: {result['p_value']:.4f}")
            
            pair_results[era_name] = result
        
        gas_elec_results[f"{gas_name} → {elec_name}"] = pair_results
    
    # ==========================================================================
    # PART 3: FULL CHAIN - UPSTREAM → GAS → ELECTRICITY (by Region)
    # ==========================================================================
    
    print("\n" + "="*100)
    print("PART 3: FULL TRANSMISSION CHAIN BY REGION")
    print("="*100)
    
    chains = [
        ("TEXAS", "Waha (Texas/Permian)", "Texas", WAHA, TX_ELEC),
        ("CALIFORNIA", "SoCal Citygate (California)", "California", SOCAL_CITYGATE, CA_ELEC),
        ("US NATIONAL", "Henry Hub (National)", "US Average", HENRY_HUB, US_ELEC),
    ]
    
    for region_label, gas_name, elec_name, gas_data, elec_data in chains:
        print(f"\n{'─'*100}")
        print(f"REGION: {region_label}")
        print(f"Chain: Upstream Factors → {gas_name} → {elec_name} Electricity")
        print(f"{'─'*100}")
        
        for era_name, (start, end) in ERA_INDICES.items():
            print(f"\n  ERA: {era_name} ({YEARS[start]}-{YEARS[end-1]})")
            
            # Get gas-elec elasticity for this era
            gas_slice = gas_data[start:end]
            elec_slice = elec_data[start:end]
            gas_elec = simple_ols(gas_slice, elec_slice)
            
            if "error" in gas_elec:
                print(f"    Gas→Elec: ERROR")
                continue
            
            gas_elec_beta = gas_elec["beta"]
            gas_elec_sig = gas_elec["significant_05"]
            
            print(f"    Gas→Elec β: {gas_elec_beta:.4f} ¢/kWh per $/MMBtu (R²={gas_elec['r_squared']:.3f}, {'SIG' if gas_elec_sig else 'NOT SIG'})")
            print(f"    ")
            print(f"    {'Factor':<28} {'Factor→Gas β':>14} {'Gas→Elec β':>12} {'Implied Elec β':>16} {'Both Sig?':>10}")
            print(f"    {'-'*82}")
            
            for factor_name, factor_data in factors.items():
                x = factor_data[start:end]
                
                if std_dev(x) == 0:
                    continue
                
                # Factor → Gas regression
                factor_gas = simple_ols(x, gas_slice)
                
                if "error" in factor_gas:
                    continue
                
                factor_gas_beta = factor_gas["beta"]
                factor_gas_sig = factor_gas["significant_05"]
                
                # Implied effect: Factor → Gas → Electricity
                implied_elec_beta = factor_gas_beta * gas_elec_beta
                
                both_sig = "YES" if (factor_gas_sig and gas_elec_sig) else "NO"
                
                print(f"    {factor_name:<28} {factor_gas_beta:>14.6f} {gas_elec_beta:>12.4f} {implied_elec_beta:>16.6f} {both_sig:>10}")
    
    # ==========================================================================
    # PART 4: REGIONAL BASIS ANALYSIS
    # ==========================================================================
    
    print("\n" + "="*100)
    print("PART 4: WHAT DRIVES REGIONAL GAS PRICE DIFFERENTIALS (BASIS)?")
    print("="*100)
    
    print("\nWaha Basis = Waha - Henry Hub (negative = discount)")
    print("SoCal Basis = SoCal Citygate - Henry Hub (positive = premium)")
    print("\nQuestion: Which factors explain why regional prices deviate from Henry Hub?")
    
    basis_targets = [
        ("Waha Basis", WAHA_BASIS),
        ("SoCal Basis", SOCAL_BASIS),
    ]
    
    for basis_name, basis_data in basis_targets:
        print(f"\n{'─'*100}")
        print(f"DEPENDENT: {basis_name}")
        print(f"{'─'*100}")
        
        for era_name, (start, end) in ERA_INDICES.items():
            print(f"\n  ERA: {era_name} ({YEARS[start]}-{YEARS[end-1]}), n={end-start}")
            print(f"  {'Factor':<30} {'β':>12} {'R²':>8} {'p-val':>8} {'Sig':>5}")
            print(f"  {'-'*70}")
            
            y = basis_data[start:end]
            
            for factor_name, factor_data in factors.items():
                x = factor_data[start:end]
                
                if std_dev(x) == 0:
                    continue
                
                result = simple_ols(x, y)
                
                if "error" not in result:
                    sig = "**" if result["significant_01"] else ("*" if result["significant_05"] else "")
                    print(f"  {factor_name:<30} {result['beta']:>12.6f} {result['r_squared']:>8.3f} {result['p_value']:>8.3f} {sig:>5}")
    
    return all_results


def summarize_confidence():
    """Print confidence assessment by region"""
    
    print("\n" + "="*100)
    print("PART 5: CONFIDENCE ASSESSMENT BY REGION")
    print("="*100)
    
    print("""
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│ TEXAS (Waha → ERCOT)                                                                       │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                            │
│ GAS → ELECTRICITY:                                                                         │
│   Pre-Shale:     Need to check results above                                               │
│   Shale Boom:    Need to check results above                                               │
│   Post-Pandemic: Need to check results above                                               │
│                                                                                            │
│ UPSTREAM → GAS:                                                                            │
│   Need to check which factors are significant for WAHA specifically                        │
│                                                                                            │
│ DATA GAPS:                                                                                 │
│   ✗ Permian-specific production (currently using national total)                          │
│   ✗ Permian pipeline capacity/constraints (Waha basis driver)                             │
│   ✗ Gulf Coast LNG terminal capacity utilization                                          │
│   ✗ ERCOT wholesale LMP data (using retail prices)                                        │
│                                                                                            │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ CALIFORNIA (SoCal Citygate → CAISO)                                                        │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                            │
│ GAS → ELECTRICITY:                                                                         │
│   Pre-Shale:     Need to check results above                                               │
│   Shale Boom:    Need to check results above                                               │
│   Post-Pandemic: Need to check results above                                               │
│                                                                                            │
│ UPSTREAM → GAS:                                                                            │
│   Need to check which factors are significant for SOCAL CITYGATE specifically             │
│                                                                                            │
│ DATA GAPS:                                                                                 │
│   ✗ Southwest pipeline capacity (El Paso, Transwestern, Kern River)                       │
│   ✗ California storage levels (Aliso Canyon restrictions post-2015)                       │
│   ✗ Pacific region storage separately from Gulf Coast                                     │
│   ✗ California renewable curtailment data (affects when gas sets marginal)                │
│   ✗ CAISO wholesale LMP data (using retail prices)                                        │
│   ✗ Wildfire/grid hardening cost allocation                                               │
│                                                                                            │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ US NATIONAL (Henry Hub → US Average)                                                       │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                            │
│ GAS → ELECTRICITY:                                                                         │
│   Pre-Shale:     Need to check results above                                               │
│   Shale Boom:    Need to check results above                                               │
│   Post-Pandemic: Need to check results above                                               │
│                                                                                            │
│ UPSTREAM → GAS:                                                                            │
│   Using national factors - this is appropriate for Henry Hub                              │
│                                                                                            │
│ DATA GAPS:                                                                                 │
│   ✗ Regional production breakdown (Appalachian, Haynesville, Permian, etc.)              │
│   ✗ Monthly data for higher-frequency analysis                                            │
│   ✗ Wholesale prices by ISO/RTO                                                           │
│                                                                                            │
└────────────────────────────────────────────────────────────────────────────────────────────┘
""")


def export_regional_results(results: Dict, filename: str):
    """Export regional results to CSV"""
    
    with open(filename, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['Region', 'Gas_Price', 'Era', 'Factor', 'Beta', 'Std_Err', 
                        't_stat', 'p_value', 'R_squared', 'n', 'Significant_05'])
        
        # ... export logic here
    
    print(f"\nResults exported to {filename}")


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    results = run_regional_analysis()
    summarize_confidence()
    
    print("\n" + "="*100)
    print("NEXT STEP: Search for regional upstream factor data")
    print("="*100)
    print("""
Required data to build proper regional models:

TEXAS:
  1. Permian Basin production (Bcf/d) - EIA reports this separately
  2. Haynesville Shale production (Bcf/d) - relevant for Gulf Coast
  3. Waha hub historical prices - EIA Natural Gas Weekly Update
  4. Permian pipeline capacity additions (Matterhorn, Whistler, etc.)
  5. Gulf Coast LNG feed gas volumes
  
CALIFORNIA:
  1. Southwest pipeline deliveries to CA
  2. California storage working gas (separately from national)
  3. Aliso Canyon operational status/restrictions
  4. SoCal Citygate historical prices - EIA
  5. CAISO renewable penetration % (affects marginal generator)
  
BOTH:
  1. Monthly data instead of annual
  2. Wholesale LMP data from ERCOT and CAISO
""")
