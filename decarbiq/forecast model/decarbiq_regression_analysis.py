#!/usr/bin/env python3
"""
DecarbIQ Regression Analysis: Natural Gas Price & Demand Impact on Electricity Prices
======================================================================================

This analysis examines how natural gas prices and demand drivers correlate with
electricity prices at both wholesale (ERCOT, CAISO) and retail levels.

Key Variables:
- DEPENDENT: Electricity prices (wholesale $/MWh, retail ¢/kWh)
- INDEPENDENT: 
  * Gas prices (Henry Hub $/MMBtu)
  * Gas consumption by sector (power, industrial, residential, commercial)
  * Power generation mix (gas share, coal share)
  * Capacity factors
  * Rig counts (supply indicator)
  * Net exports (demand indicator)

Author: DecarbIQ
"""

import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Try to import visualization and stats libraries
try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("Note: matplotlib not available for charts")

try:
    from scipy import stats
    from scipy.stats import pearsonr, spearmanr
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("Note: scipy not available, using numpy for correlations")

try:
    import statsmodels.api as sm
    from statsmodels.stats.outliers_influence import variance_inflation_factor
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False
    print("Note: statsmodels not available, using basic OLS")


# =============================================================================
# DATA LOADING FUNCTIONS
# =============================================================================

def load_gas_consumption(filepath):
    """Load gas consumption by sector data."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    return df

def load_retail_electricity(filepath):
    """Load US retail electricity prices."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    return df

def load_generation_mix(filepath):
    """Load generation by fuel type."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    return df

def load_rig_counts(filepath):
    """Load Baker Hughes rig counts."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    return df

def load_wholesale_ercot(filepath):
    """Load ERCOT wholesale prices."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    df = df.rename(columns={'avg_price_mwh': 'ercot_wholesale_mwh'})
    return df

def load_wholesale_caiso(filepath):
    """Load CAISO wholesale prices."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    df = df.rename(columns={'avg_price_mwh': 'caiso_wholesale_mwh'})
    return df

def load_net_exports(filepath):
    """Load annual net exports (will interpolate to monthly)."""
    df = pd.read_csv(filepath)
    return df

def load_historical_gas_prices(filepath):
    """Load historical gas prices (1990-1996)."""
    df = pd.read_csv(filepath)
    df['date'] = pd.to_datetime(df['period'] + '-01')
    return df

def parse_steo_gas_prices(filepath):
    """Parse STEO format to extract Henry Hub prices."""
    try:
        with open(filepath, 'r') as f:
            lines = f.readlines()
        
        header_line = lines[4].strip()
        headers = header_line.split(',')
        
        records = []
        for line in lines[5:]:
            if 'Henry Hub' in line or 'NGHHUUS' in line:
                parts = line.strip().split(',')
                for i, header in enumerate(headers[6:], start=6):
                    if header and 'remove' not in header.lower():
                        try:
                            date_str = header.strip().replace('"', '')
                            date = pd.to_datetime(date_str, format='%b %Y')
                            value = float(parts[i].replace('"', '')) if i < len(parts) and parts[i].strip() else None
                            if value is not None:
                                records.append({
                                    'date': date,
                                    'year': date.year,
                                    'month': date.month,
                                    'period': date.strftime('%Y-%m'),
                                    'henry_hub_mmbtu': value
                                })
                        except:
                            pass
        
        if records:
            return pd.DataFrame(records)
    except Exception as e:
        print(f"Could not parse STEO file: {e}")
    
    return pd.DataFrame()


# =============================================================================
# DATA MERGING
# =============================================================================

def build_master_dataset(data_dir="./"):
    """Build a master dataset merging all available data sources."""
    print("=" * 70)
    print("BUILDING MASTER DATASET")
    print("=" * 70)
    
    print("\nLoading datasets...")
    
    # Gas consumption (primary)
    try:
        consumption = load_gas_consumption(f"{data_dir}/gas_consumption_by_sector_monthly.csv")
        print(f"  ✓ Gas consumption: {len(consumption)} months ({consumption['year'].min()}-{consumption['year'].max()})")
    except Exception as e:
        print(f"  ✗ Gas consumption: {e}")
        consumption = pd.DataFrame()
    
    # Retail electricity
    try:
        retail = load_retail_electricity(f"{data_dir}/retail_us_all_sectors.csv")
        print(f"  ✓ Retail electricity: {len(retail)} months")
    except Exception as e:
        print(f"  ✗ Retail electricity: {e}")
        retail = pd.DataFrame()
    
    # Generation mix
    try:
        generation = load_generation_mix(f"{data_dir}/eia_generation_by_fuel_monthly.csv")
        print(f"  ✓ Generation mix: {len(generation)} months")
    except Exception as e:
        print(f"  ✗ Generation mix: {e}")
        generation = pd.DataFrame()
    
    # Rig counts
    try:
        rigs = load_rig_counts(f"{data_dir}/baker_hughes_rig_counts_monthly.csv")
        print(f"  ✓ Rig counts: {len(rigs)} months")
    except Exception as e:
        print(f"  ✗ Rig counts: {e}")
        rigs = pd.DataFrame()
    
    # ERCOT wholesale
    try:
        ercot = load_wholesale_ercot(f"{data_dir}/ercot_wholesale_monthly.csv")
        print(f"  ✓ ERCOT wholesale: {len(ercot)} months")
    except Exception as e:
        print(f"  ✗ ERCOT wholesale: {e}")
        ercot = pd.DataFrame()
    
    # CAISO wholesale
    try:
        caiso = load_wholesale_caiso(f"{data_dir}/caiso_wholesale_monthly.csv")
        print(f"  ✓ CAISO wholesale: {len(caiso)} months")
    except Exception as e:
        print(f"  ✗ CAISO wholesale: {e}")
        caiso = pd.DataFrame()
    
    # Historical gas prices
    try:
        gas_hist = load_historical_gas_prices(f"{data_dir}/gas_prices_historical_1990_1996.csv")
        print(f"  ✓ Historical gas prices: {len(gas_hist)} months")
    except Exception as e:
        print(f"  ✗ Historical gas prices: {e}")
        gas_hist = pd.DataFrame()
    
    # STEO gas prices (recent)
    try:
        gas_steo = parse_steo_gas_prices(f"{data_dir}/5b__U_S__Regional_Natural_Gas_Prices.csv")
        if len(gas_steo) > 0:
            print(f"  ✓ STEO gas prices: {len(gas_steo)} months")
        else:
            print(f"  ⚠ STEO gas prices: Could not parse")
    except Exception as e:
        print(f"  ✗ STEO gas prices: {e}")
        gas_steo = pd.DataFrame()
    
    # Net exports (annual)
    try:
        net_exports = load_net_exports(f"{data_dir}/gas_net_exports_annual.csv")
        print(f"  ✓ Net exports: {len(net_exports)} years")
    except Exception as e:
        print(f"  ✗ Net exports: {e}")
        net_exports = pd.DataFrame()
    
    # Start with consumption as base
    if len(consumption) == 0:
        print("\n❌ Cannot build master dataset without consumption data")
        return pd.DataFrame()
    
    master = consumption[['date', 'year', 'month', 'period', 
                          'electric_power_bcfd', 'industrial_bcfd', 
                          'residential_bcfd', 'commercial_bcfd', 'total_bcfd',
                          'electric_power_share_pct', 'industrial_share_pct']].copy()
    
    # Merge retail electricity
    if len(retail) > 0:
        retail_cols = ['date', 'residential_cents_kwh', 'commercial_cents_kwh', 'industrial_cents_kwh']
        master = master.merge(retail[retail_cols], on='date', how='left')
        print(f"\n  Merged retail electricity: {master['residential_cents_kwh'].notna().sum()} matches")
    
    # Merge generation mix
    if len(generation) > 0:
        gen_cols = ['date', 'gas_share_pct', 'coal_share_pct', 'nuclear_share_pct', 'renewable_share_pct']
        available_cols = ['date'] + [c for c in gen_cols[1:] if c in generation.columns]
        master = master.merge(generation[available_cols], on='date', how='left')
        print(f"  Merged generation mix: {master['gas_share_pct'].notna().sum() if 'gas_share_pct' in master.columns else 0} matches")
    
    # Merge rig counts
    if len(rigs) > 0:
        rigs_cols = ['date', 'gas_rigs', 'oil_rigs', 'total_rigs']
        master = master.merge(rigs[rigs_cols], on='date', how='left')
        print(f"  Merged rig counts: {master['gas_rigs'].notna().sum()} matches")
    
    # Merge ERCOT wholesale
    if len(ercot) > 0:
        master = master.merge(ercot[['date', 'ercot_wholesale_mwh']], on='date', how='left')
        print(f"  Merged ERCOT wholesale: {master['ercot_wholesale_mwh'].notna().sum()} matches")
    
    # Merge CAISO wholesale
    if len(caiso) > 0:
        master = master.merge(caiso[['date', 'caiso_wholesale_mwh']], on='date', how='left')
        print(f"  Merged CAISO wholesale: {master['caiso_wholesale_mwh'].notna().sum()} matches")
    
    # Merge gas prices (combine historical + STEO)
    if len(gas_hist) > 0 or len(gas_steo) > 0:
        gas_prices = pd.DataFrame()
        if len(gas_hist) > 0:
            gas_prices = gas_hist[['date', 'gas_price_mmbtu']].rename(columns={'gas_price_mmbtu': 'henry_hub_mmbtu'})
        if len(gas_steo) > 0:
            if len(gas_prices) > 0:
                gas_prices = pd.concat([gas_prices, gas_steo[['date', 'henry_hub_mmbtu']]])
                gas_prices = gas_prices.drop_duplicates(subset='date', keep='last')
            else:
                gas_prices = gas_steo[['date', 'henry_hub_mmbtu']]
        
        master = master.merge(gas_prices, on='date', how='left')
        print(f"  Merged gas prices: {master['henry_hub_mmbtu'].notna().sum()} matches")
    
    # Merge net exports (annual -> monthly by year)
    if len(net_exports) > 0:
        exports_cols = ['year', 'net_exports_bcfd', 'lng_exports_bcfd', 'is_net_exporter']
        available_cols = ['year'] + [c for c in exports_cols[1:] if c in net_exports.columns]
        master = master.merge(net_exports[available_cols], on='year', how='left')
        print(f"  Merged net exports: {master['net_exports_bcfd'].notna().sum() if 'net_exports_bcfd' in master.columns else 0} matches")
    
    master = master.sort_values('date').reset_index(drop=True)
    
    print(f"\n✅ Master dataset: {len(master)} rows, {len(master.columns)} columns")
    print(f"   Date range: {master['date'].min().strftime('%Y-%m')} to {master['date'].max().strftime('%Y-%m')}")
    
    return master


# =============================================================================
# CORRELATION ANALYSIS
# =============================================================================

def calculate_correlations(df, target_cols, feature_cols):
    """Calculate correlations between target and feature variables."""
    results = []
    
    for target in target_cols:
        if target not in df.columns:
            continue
            
        for feature in feature_cols:
            if feature not in df.columns:
                continue
            
            aligned = df[[target, feature]].dropna()
            
            if len(aligned) < 10:
                continue
            
            if SCIPY_AVAILABLE:
                r, p_value = pearsonr(aligned[target], aligned[feature])
            else:
                r = np.corrcoef(aligned[target], aligned[feature])[0, 1]
                p_value = None
            
            results.append({
                'target': target,
                'feature': feature,
                'correlation': r,
                'r_squared': r**2,
                'p_value': p_value,
                'n_observations': len(aligned),
                'significance': '***' if p_value and p_value < 0.001 else ('**' if p_value and p_value < 0.01 else ('*' if p_value and p_value < 0.05 else ''))
            })
    
    return pd.DataFrame(results)


# =============================================================================
# REGRESSION ANALYSIS
# =============================================================================

def run_ols_regression(df, target, features, add_constant=True):
    """Run OLS regression and return results."""
    available_features = [f for f in features if f in df.columns]
    cols = [target] + available_features
    
    data = df[cols].dropna()
    
    if len(data) < len(available_features) + 10:
        return {'error': f'Insufficient data: {len(data)} rows for {len(available_features)} features'}
    
    y = data[target]
    X = data[available_features]
    
    if add_constant:
        X = sm.add_constant(X) if STATSMODELS_AVAILABLE else np.column_stack([np.ones(len(X)), X])
    
    if STATSMODELS_AVAILABLE:
        model = sm.OLS(y, X).fit()
        
        results = {
            'target': target,
            'features': available_features,
            'n_observations': len(data),
            'r_squared': model.rsquared,
            'adj_r_squared': model.rsquared_adj,
            'f_statistic': model.fvalue,
            'f_pvalue': model.f_pvalue,
            'coefficients': dict(zip(model.params.index, model.params.values)),
            'std_errors': dict(zip(model.bse.index, model.bse.values)),
            't_values': dict(zip(model.tvalues.index, model.tvalues.values)),
            'p_values': dict(zip(model.pvalues.index, model.pvalues.values)),
            'model': model
        }
    else:
        X_arr = np.array(X)
        y_arr = np.array(y)
        beta = np.linalg.lstsq(X_arr, y_arr, rcond=None)[0]
        y_pred = X_arr @ beta
        ss_res = np.sum((y_arr - y_pred)**2)
        ss_tot = np.sum((y_arr - np.mean(y_arr))**2)
        r_squared = 1 - ss_res / ss_tot
        
        feature_names = ['const'] + available_features if add_constant else available_features
        
        results = {
            'target': target,
            'features': available_features,
            'n_observations': len(data),
            'r_squared': r_squared,
            'coefficients': dict(zip(feature_names, beta)),
            'model': None
        }
    
    return results


# =============================================================================
# LAG ANALYSIS
# =============================================================================

def calculate_lagged_correlations(df, target, feature, max_lag=12):
    """Calculate correlations at different lag periods."""
    results = []
    
    target_data = df[target].dropna()
    feature_data = df[feature].dropna()
    
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            aligned_target = target_data.iloc[-lag:]
            aligned_feature = feature_data.iloc[:lag]
        elif lag > 0:
            aligned_target = target_data.iloc[:-lag]
            aligned_feature = feature_data.iloc[lag:]
        else:
            aligned_target = target_data
            aligned_feature = feature_data
        
        min_len = min(len(aligned_target), len(aligned_feature))
        if min_len < 10:
            continue
        
        aligned_target = aligned_target.iloc[:min_len].reset_index(drop=True)
        aligned_feature = aligned_feature.iloc[:min_len].reset_index(drop=True)
        
        r = np.corrcoef(aligned_target, aligned_feature)[0, 1]
        
        results.append({
            'lag_months': lag,
            'correlation': r,
            'interpretation': f"Feature leads by {abs(lag)} months" if lag < 0 else (f"Feature lags by {lag} months" if lag > 0 else "Contemporaneous")
        })
    
    return pd.DataFrame(results)


# =============================================================================
# VISUALIZATION
# =============================================================================

def plot_correlation_heatmap(corr_df, output_path="correlation_heatmap.png"):
    """Plot correlation heatmap."""
    if not MATPLOTLIB_AVAILABLE:
        print("Matplotlib not available for plotting")
        return
    
    matrix = corr_df.pivot(index='target', columns='feature', values='correlation')
    
    fig, ax = plt.subplots(figsize=(14, 8))
    im = ax.imshow(matrix.values, cmap='RdBu_r', aspect='auto', vmin=-1, vmax=1)
    
    ax.set_xticks(range(len(matrix.columns)))
    ax.set_yticks(range(len(matrix.index)))
    ax.set_xticklabels(matrix.columns, rotation=45, ha='right', fontsize=9)
    ax.set_yticklabels(matrix.index, fontsize=9)
    
    cbar = plt.colorbar(im, ax=ax)
    cbar.set_label('Correlation Coefficient')
    
    for i in range(len(matrix.index)):
        for j in range(len(matrix.columns)):
            val = matrix.iloc[i, j]
            if not np.isnan(val):
                color = 'white' if abs(val) > 0.5 else 'black'
                ax.text(j, i, f'{val:.2f}', ha='center', va='center', color=color, fontsize=8)
    
    ax.set_title('Correlation: NG Demand Drivers vs Electricity Prices', fontsize=12, fontweight='bold')
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_path}")


def plot_time_series(df, output_path="time_series_analysis.png"):
    """Plot key time series."""
    if not MATPLOTLIB_AVAILABLE:
        return
    
    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    
    # 1. Electricity prices
    ax1 = axes[0]
    if 'residential_cents_kwh' in df.columns:
        ax1.plot(df['date'], df['residential_cents_kwh'], label='Residential', alpha=0.8)
    if 'industrial_cents_kwh' in df.columns:
        ax1.plot(df['date'], df['industrial_cents_kwh'], label='Industrial', alpha=0.8)
    if 'ercot_wholesale_mwh' in df.columns:
        ax1_twin = ax1.twinx()
        ax1_twin.plot(df['date'], df['ercot_wholesale_mwh'], label='ERCOT Wholesale', color='red', alpha=0.6)
        ax1_twin.set_ylabel('Wholesale ($/MWh)', color='red')
    ax1.set_ylabel('Retail (¢/kWh)')
    ax1.set_title('Electricity Prices')
    ax1.legend(loc='upper left')
    ax1.grid(True, alpha=0.3)
    
    # 2. Gas prices and consumption
    ax2 = axes[1]
    if 'henry_hub_mmbtu' in df.columns:
        ax2.plot(df['date'], df['henry_hub_mmbtu'], label='Henry Hub', color='orange')
        ax2.set_ylabel('Gas Price ($/MMBtu)', color='orange')
    ax2_twin = ax2.twinx()
    if 'electric_power_bcfd' in df.columns:
        ax2_twin.plot(df['date'], df['electric_power_bcfd'], label='Power Sector Demand', color='blue', alpha=0.7)
        ax2_twin.set_ylabel('Power Sector Gas (Bcf/d)', color='blue')
    ax2.set_title('Natural Gas: Price vs Power Sector Demand')
    ax2.legend(loc='upper left')
    ax2.grid(True, alpha=0.3)
    
    # 3. Generation mix
    ax3 = axes[2]
    if 'gas_share_pct' in df.columns:
        ax3.fill_between(df['date'], 0, df['gas_share_pct'], label='Gas', alpha=0.7)
    if 'coal_share_pct' in df.columns:
        ax3.fill_between(df['date'], df.get('gas_share_pct', 0), 
                        df.get('gas_share_pct', 0) + df['coal_share_pct'], label='Coal', alpha=0.7)
    ax3.set_ylabel('Generation Share (%)')
    ax3.set_title('Power Generation Mix')
    ax3.legend(loc='upper right')
    ax3.grid(True, alpha=0.3)
    
    # 4. Supply indicators
    ax4 = axes[3]
    if 'gas_rigs' in df.columns:
        ax4.plot(df['date'], df['gas_rigs'], label='Gas Rigs', color='green')
    if 'net_exports_bcfd' in df.columns:
        ax4_twin = ax4.twinx()
        ax4_twin.plot(df['date'], df['net_exports_bcfd'], label='Net Exports', color='purple', alpha=0.7)
        ax4_twin.set_ylabel('Net Exports (Bcf/d)', color='purple')
        ax4_twin.axhline(y=0, color='purple', linestyle='--', alpha=0.3)
    ax4.set_ylabel('Gas Rig Count', color='green')
    ax4.set_title('Supply/Demand Indicators')
    ax4.legend(loc='upper left')
    ax4.grid(True, alpha=0.3)
    
    ax4.set_xlabel('Date')
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_path}")


def plot_scatter_matrix(df, output_path="scatter_analysis.png"):
    """Plot scatter plots of key relationships."""
    if not MATPLOTLIB_AVAILABLE:
        return
    
    pairs = [
        ('henry_hub_mmbtu', 'industrial_cents_kwh', 'Gas Price vs Industrial Retail'),
        ('henry_hub_mmbtu', 'ercot_wholesale_mwh', 'Gas Price vs ERCOT Wholesale'),
        ('henry_hub_mmbtu', 'caiso_wholesale_mwh', 'Gas Price vs CAISO Wholesale'),
        ('electric_power_bcfd', 'industrial_cents_kwh', 'Power Sector Demand vs Industrial'),
        ('gas_share_pct', 'ercot_wholesale_mwh', 'Gas Gen Share vs ERCOT'),
        ('gas_share_pct', 'caiso_wholesale_mwh', 'Gas Gen Share vs CAISO'),
        ('net_exports_bcfd', 'industrial_cents_kwh', 'Net Exports vs Industrial'),
        ('coal_share_pct', 'ercot_wholesale_mwh', 'Coal Share vs ERCOT'),
    ]
    
    available_pairs = [(x, y, t) for x, y, t in pairs if x in df.columns and y in df.columns]
    
    if not available_pairs:
        print("  No valid pairs for scatter plot")
        return
    
    n_plots = len(available_pairs)
    n_cols = min(3, n_plots)
    n_rows = (n_plots + n_cols - 1) // n_cols
    
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(5*n_cols, 4*n_rows))
    if n_plots == 1:
        axes = [axes]
    else:
        axes = axes.flatten()
    
    for i, (x_col, y_col, title) in enumerate(available_pairs):
        ax = axes[i]
        data = df[[x_col, y_col]].dropna()
        
        if len(data) < 5:
            ax.text(0.5, 0.5, 'Insufficient data', ha='center', va='center', transform=ax.transAxes)
            ax.set_title(title)
            continue
        
        ax.scatter(data[x_col], data[y_col], alpha=0.5, s=20)
        
        z = np.polyfit(data[x_col], data[y_col], 1)
        p = np.poly1d(z)
        x_line = np.linspace(data[x_col].min(), data[x_col].max(), 100)
        ax.plot(x_line, p(x_line), 'r--', alpha=0.8, linewidth=2)
        
        r = np.corrcoef(data[x_col], data[y_col])[0, 1]
        ax.text(0.05, 0.95, f'r = {r:.3f}', transform=ax.transAxes, 
                fontsize=10, verticalalignment='top', fontweight='bold',
                bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
        
        ax.set_xlabel(x_col.replace('_', ' ').title())
        ax.set_ylabel(y_col.replace('_', ' ').title())
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
    
    for j in range(len(available_pairs), len(axes)):
        axes[j].set_visible(False)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    print(f"  Saved: {output_path}")


# =============================================================================
# MAIN ANALYSIS
# =============================================================================

def run_full_analysis(data_dir="./", output_dir="./"):
    """Run complete regression analysis."""
    print("\n" + "=" * 70)
    print("DECARBIQ REGRESSION ANALYSIS")
    print("Natural Gas Price & Demand Impact on Electricity Prices")
    print("=" * 70)
    
    master = build_master_dataset(data_dir)
    
    if len(master) == 0:
        print("\n❌ Cannot proceed without data")
        return None, None
    
    master.to_csv(f"{output_dir}/master_regression_dataset.csv", index=False)
    print(f"\n✅ Saved master dataset: master_regression_dataset.csv")
    
    target_vars = [
        'industrial_cents_kwh',
        'ercot_wholesale_mwh',
        'caiso_wholesale_mwh'
    ]
    
    feature_vars = [
        'henry_hub_mmbtu',
        'electric_power_bcfd',
        'industrial_bcfd',
        'residential_bcfd',
        'total_bcfd',
        'electric_power_share_pct',
        'gas_share_pct',
        'coal_share_pct',
        'gas_rigs',
        'total_rigs',
        'net_exports_bcfd',
        'lng_exports_bcfd'
    ]
    
    # CORRELATION ANALYSIS
    print("\n" + "=" * 70)
    print("CORRELATION ANALYSIS")
    print("=" * 70)
    
    corr_df = calculate_correlations(master, target_vars, feature_vars)
    
    if len(corr_df) > 0:
        corr_df.to_csv(f"{output_dir}/correlation_results.csv", index=False)
        print(f"\n✅ Saved: correlation_results.csv")
        
        print("\n📊 TOP CORRELATIONS (by absolute value):")
        print("-" * 70)
        
        for target in target_vars:
            target_corrs = corr_df[corr_df['target'] == target].copy()
            if len(target_corrs) == 0:
                continue
            
            target_corrs['abs_corr'] = target_corrs['correlation'].abs()
            top = target_corrs.nlargest(3, 'abs_corr')
            
            print(f"\n{target}:")
            for _, row in top.iterrows():
                print(f"  {row['feature']:30} r={row['correlation']:+.3f} (R²={row['r_squared']:.3f}) {row['significance']}")
    
    # REGRESSION ANALYSIS
    print("\n" + "=" * 70)
    print("REGRESSION ANALYSIS")
    print("=" * 70)
    
    if STATSMODELS_AVAILABLE:
        regression_results = []
        
        for target in target_vars:
            if target not in master.columns:
                continue
            
            available_features = [f for f in feature_vars if f in master.columns]
            
            if len(available_features) == 0:
                continue
            
            result = run_ols_regression(master, target, available_features)
            
            if 'error' not in result:
                regression_results.append(result)
                
                print(f"\n📈 {target}:")
                print(f"   R² = {result['r_squared']:.4f}, Adj R² = {result.get('adj_r_squared', 'N/A'):.4f}")
                print(f"   N = {result['n_observations']}")
                print(f"   F-statistic = {result.get('f_statistic', 'N/A'):.2f} (p = {result.get('f_pvalue', 'N/A'):.4e})")
                
                print("\n   Coefficients:")
                for var, coef in result['coefficients'].items():
                    if var == 'const':
                        continue
                    pval = result.get('p_values', {}).get(var, None)
                    sig = '***' if pval and pval < 0.001 else ('**' if pval and pval < 0.01 else ('*' if pval and pval < 0.05 else ''))
                    print(f"     {var:30} β = {coef:+.4f} {sig}")
        
        if regression_results:
            summary_rows = []
            for r in regression_results:
                row = {
                    'target': r['target'],
                    'r_squared': r['r_squared'],
                    'adj_r_squared': r.get('adj_r_squared'),
                    'n_observations': r['n_observations'],
                    'f_statistic': r.get('f_statistic'),
                    'f_pvalue': r.get('f_pvalue')
                }
                for var, coef in r['coefficients'].items():
                    row[f'coef_{var}'] = coef
                    if 'p_values' in r:
                        row[f'pval_{var}'] = r['p_values'].get(var)
                summary_rows.append(row)
            
            summary_df = pd.DataFrame(summary_rows)
            summary_df.to_csv(f"{output_dir}/regression_results.csv", index=False)
            print(f"\n✅ Saved: regression_results.csv")
    
    # LAG ANALYSIS
    print("\n" + "=" * 70)
    print("LAG ANALYSIS: Do Gas Prices Lead Electricity Prices?")
    print("=" * 70)
    
    lag_pairs = [
        ('henry_hub_mmbtu', 'industrial_cents_kwh'),
        ('henry_hub_mmbtu', 'ercot_wholesale_mwh'),
        ('henry_hub_mmbtu', 'caiso_wholesale_mwh'),
        ('electric_power_bcfd', 'ercot_wholesale_mwh'),
    ]
    
    for feature, target in lag_pairs:
        if feature in master.columns and target in master.columns:
            lag_df = calculate_lagged_correlations(master, target, feature, max_lag=6)
            
            if len(lag_df) > 0:
                best_lag = lag_df.loc[lag_df['correlation'].abs().idxmax()]
                print(f"\n{feature} → {target}:")
                print(f"  Best correlation: r = {best_lag['correlation']:.3f} at lag = {best_lag['lag_months']} months")
                print(f"  ({best_lag['interpretation']})")
    
    # VISUALIZATIONS
    if MATPLOTLIB_AVAILABLE:
        print("\n" + "=" * 70)
        print("GENERATING VISUALIZATIONS")
        print("=" * 70)
        
        if len(corr_df) > 0:
            plot_correlation_heatmap(corr_df, f"{output_dir}/correlation_heatmap.png")
        
        plot_time_series(master, f"{output_dir}/time_series_analysis.png")
        plot_scatter_matrix(master, f"{output_dir}/scatter_analysis.png")
    
    # KEY FINDINGS SUMMARY
    print("\n" + "=" * 70)
    print("KEY FINDINGS SUMMARY")
    print("=" * 70)
    
    if len(corr_df) > 0:
        print("\n🔑 STRONGEST RELATIONSHIPS:")
        
        top_overall = corr_df.nlargest(5, 'r_squared')
        for _, row in top_overall.iterrows():
            direction = "↑" if row['correlation'] > 0 else "↓"
            print(f"  {direction} {row['feature']} → {row['target']}: r={row['correlation']:.3f} (explains {row['r_squared']*100:.1f}% of variance)")
    
    gas_corrs = corr_df[corr_df['feature'] == 'henry_hub_mmbtu'] if len(corr_df) > 0 else pd.DataFrame()
    if len(gas_corrs) > 0:
        print("\n⛽ GAS PRICE IMPACT ON ELECTRICITY:")
        for _, row in gas_corrs.iterrows():
            if row['r_squared'] > 0.1:
                print(f"  • {row['target']}: correlation of {row['correlation']:.3f}")
    
    print("\n" + "=" * 70)
    print("ANALYSIS COMPLETE")
    print("=" * 70)
    
    return master, corr_df


if __name__ == "__main__":
    master, correlations = run_full_analysis(data_dir="./", output_dir="./")
