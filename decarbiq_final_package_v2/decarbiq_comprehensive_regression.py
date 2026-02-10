"""
DECARBIQ COMPREHENSIVE REGRESSION MODEL
=======================================
Merges all variable groups:
- Permitting Regimes
- Demand Drivers  
- Generation Mix & Capacity
- Cost & Supply
- Policy & Regulatory

Author: DecarbIQ Analytics
Date: 2024
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from sklearn.linear_model import LinearRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import r2_score, mean_absolute_error, mean_squared_error
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================

# File paths (adjust as needed)
MASTER_DATA_PATH = 'regression_dataset_complete.csv'
POLICY_DB_PATH = 'decarbiq_policy_database.csv'
BOTTLENECKS_PATH = 'decarbiq_permitting_bottlenecks.csv'
MILESTONES_PATH = 'decarbiq_regulatory_milestones.csv'

# Analysis period
START_DATE = '1997-01-01'

# =============================================================================
# VARIABLE GROUP DEFINITIONS
# =============================================================================

# Group 1: Permitting Regime Variables
PERMITTING_REGIMES = {
    'post_nepa': ('1970-01-01', 'NEPA Enacted'),
    'post_epact2005_permitting': ('2005-08-08', 'EPAct 2005 - FERC Lead Agency'),
    'post_fra2023': ('2023-06-03', 'Fiscal Responsibility Act - 2yr EIS Limit'),
    'post_certificate_policy': ('1999-09-15', 'FERC Certificate Policy Statement'),
    'post_mvp_approval': ('2023-06-03', 'MVP Congressional Approval'),
    'post_order2003': ('2003-07-24', 'FERC Order 2003 - Interconnection'),
    'post_order2023': ('2023-07-28', 'FERC Order 2023 - Queue Reform'),
    'post_order1000': ('2011-07-21', 'FERC Order 1000 - Transmission Planning'),
    'post_order1920': ('2024-05-13', 'FERC Order 1920 - Transmission Reform'),
    'post_401_battles': ('2016-04-22', 'NY Denies Constitution Pipeline 401'),
}

# Group 2: Policy & Regulatory Regimes
POLICY_REGIMES = {
    'post_order636': ('1992-11-01', 'FERC Order 636 - Gas Restructuring'),
    'post_order888': ('1996-04-24', 'FERC Order 888 - Electric Open Access'),
    'post_epact2005': ('2005-08-08', 'EPAct 2005 - Fracking Exemption'),
    'post_caa1990': ('1990-11-15', 'Clean Air Act Amendments'),
    'post_mats': ('2015-04-16', 'MATS Mercury Rule'),
    'post_rggi': ('2009-01-01', 'RGGI Cap-and-Trade'),
    'post_ca_cap': ('2013-01-01', 'CA Cap-and-Trade'),
    'post_ira': ('2022-08-16', 'Inflation Reduction Act'),
}

# Group 3: Supply Regimes
SUPPLY_REGIMES = {
    'shale_era': ('2009-01-01', 'Shale Production Surge'),
    'us_net_exporter': ('2017-09-01', 'US Net Gas Exporter'),
    'post_lng_exports': ('2016-02-01', 'LNG Exports Begin'),
    'post_lng_pause': ('2024-01-26', 'DOE LNG Pause'),
}

# =============================================================================
# MAIN FUNCTIONS
# =============================================================================

def load_data(master_path):
    """Load and prepare master dataset."""
    df = pd.read_csv(master_path)
    df['date'] = pd.to_datetime(df['date'])
    return df

def create_regime_variables(df, regimes_dict):
    """Create binary regime variables from date thresholds."""
    for var, (date_str, desc) in regimes_dict.items():
        df[var] = (df['date'] >= pd.to_datetime(date_str)).astype(int)
    return df

def create_derived_variables(df):
    """Create derived demand, generation, and supply variables."""
    # Demand derivatives
    df['power_share_of_gas'] = df['electric_power_bcfd'] / df['total_bcfd'] * 100
    df['heating_demand'] = df['residential_bcfd'] + df['commercial_bcfd']
    df['base_demand'] = df['industrial_bcfd'] + df['electric_power_bcfd']
    
    # Seasonal indicators
    df['month'] = df['date'].dt.month
    df['is_winter'] = df['month'].isin([12, 1, 2]).astype(int)
    df['is_summer'] = df['month'].isin([6, 7, 8]).astype(int)
    
    # Generation mix derivatives
    if 'gas_share_pct' in df.columns and 'coal_share_pct' in df.columns:
        df['gas_coal_ratio'] = df['gas_share_pct'] / df['coal_share_pct'].replace(0, np.nan)
        df['thermal_share'] = df['gas_share_pct'] + df['coal_share_pct']
        df['clean_share'] = df['nuclear_share_pct'].fillna(0) + df['renewable_share_pct'].fillna(0)
    
    # Coal retirement regimes
    df['post_coal_decline'] = (df['date'] >= '2010-01-01').astype(int)
    df['post_major_coal_ret'] = (df['date'] >= '2015-04-16').astype(int)
    
    return df

def define_variable_groups(df):
    """Define comprehensive variable groups."""
    GROUPS = {
        'PERMITTING': list(PERMITTING_REGIMES.keys()),
        'DEMAND_DRIVERS': [
            'electric_power_bcfd', 'residential_bcfd', 'commercial_bcfd', 'industrial_bcfd',
            'power_share_of_gas', 'heating_demand', 'base_demand',
            'is_winter', 'is_summer'
        ],
        'GENERATION_MIX': [
            'gas_share_pct', 'coal_share_pct', 'nuclear_share_pct', 'renewable_share_pct',
            'gas_coal_ratio', 'thermal_share', 'clean_share',
            'post_coal_decline', 'post_major_coal_ret'
        ],
        'COST_SUPPLY': [
            'gas_rigs', 'oil_rigs', 'total_rigs',
            'lng_exports_bcfd', 'net_exports_bcfd'
        ] + list(SUPPLY_REGIMES.keys()),
        'POLICY_REGULATORY': list(POLICY_REGIMES.keys())
    }
    
    # Filter to available variables
    for group, vars_list in GROUPS.items():
        GROUPS[group] = [v for v in vars_list if v in df.columns and df[v].notna().sum() > 20]
    
    return GROUPS

def run_group_regressions(df, y_col, GROUPS):
    """Run individual regressions for each variable group."""
    y = df[y_col].values
    results = []
    
    for group_name, vars_list in GROUPS.items():
        available_vars = [v for v in vars_list if v in df.columns and df[v].notna().sum() > 50]
        
        if len(available_vars) >= 2:
            X = df[available_vars].fillna(0).values
            
            model = LinearRegression()
            model.fit(X, y)
            y_pred = model.predict(X)
            
            results.append({
                'group': group_name,
                'n_vars': len(available_vars),
                'r2': r2_score(y, y_pred),
                'mae': mean_absolute_error(y, y_pred),
                'rmse': np.sqrt(mean_squared_error(y, y_pred)),
                'variables': available_vars,
                'coefficients': dict(zip(available_vars, model.coef_))
            })
    
    return results

def run_incremental_regression(df, y_col, GROUPS, group_order):
    """Run incremental regression adding groups sequentially."""
    y = df[y_col].values
    combined_vars = []
    results = []
    
    for group_name in group_order:
        new_vars = [v for v in GROUPS[group_name] if v in df.columns and df[v].notna().sum() > 50]
        combined_vars.extend(new_vars)
        combined_vars = list(set(combined_vars))
        
        if len(combined_vars) >= 2:
            X = df[combined_vars].fillna(0).values
            
            model = LinearRegression()
            model.fit(X, y)
            y_pred = model.predict(X)
            
            prev_r2 = results[-1]['r2'] if results else 0
            
            results.append({
                'step': group_name,
                'cumulative_vars': len(combined_vars),
                'r2': r2_score(y, y_pred),
                'mae': mean_absolute_error(y, y_pred),
                'r2_gain': r2_score(y, y_pred) - prev_r2
            })
    
    return results

def run_full_model(df, y_col, GROUPS):
    """Run full comprehensive model with all variables."""
    y = df[y_col].values
    
    all_vars = []
    for group_vars in GROUPS.values():
        all_vars.extend(group_vars)
    all_vars = list(set(all_vars))
    all_vars = [v for v in all_vars if v in df.columns and df[v].notna().sum() > 50]
    
    X = df[all_vars].fillna(0).values
    
    # Use Ridge to handle multicollinearity
    model = Ridge(alpha=1.0)
    model.fit(X, y)
    y_pred = model.predict(X)
    
    return {
        'n_vars': len(all_vars),
        'r2': r2_score(y, y_pred),
        'mae': mean_absolute_error(y, y_pred),
        'rmse': np.sqrt(mean_squared_error(y, y_pred)),
        'variables': all_vars,
        'coefficients': dict(zip(all_vars, model.coef_)),
        'intercept': model.intercept_
    }

def create_heatmap(df, price_vars, x_vars_grouped, group_boundaries, group_names, output_path):
    """Create grouped correlation heatmap."""
    fig, ax = plt.subplots(figsize=(20, 8))
    
    # Calculate correlations
    corr_matrix = pd.DataFrame(index=[v[1] for v in price_vars], 
                                columns=[v.replace('_', ' ').title() for v in x_vars_grouped])
    
    for pv, pl in price_vars:
        if pv in df.columns:
            for j, xv in enumerate(x_vars_grouped):
                mask = df[pv].notna() & df[xv].notna()
                if mask.sum() > 20:
                    corr_matrix.iloc[price_vars.index((pv, pl)), j] = df.loc[mask, pv].corr(df.loc[mask, xv])
    
    corr_matrix = corr_matrix.astype(float)
    
    sns.heatmap(corr_matrix, annot=True, fmt='.2f', cmap='RdBu_r', 
                center=0, vmin=-1, vmax=1, linewidths=0.5,
                annot_kws={'size': 9, 'fontweight': 'bold'}, ax=ax)
    
    # Add group separators
    for boundary in group_boundaries[1:-1]:
        ax.axvline(x=boundary, color='black', linewidth=2)
    
    ax.set_title('Energy Prices vs All Variable Groups\n(Correlation Coefficients)', 
                 fontsize=14, fontweight='bold', pad=20)
    
    plt.xticks(rotation=45, ha='right')
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    print("=" * 80)
    print("DECARBIQ COMPREHENSIVE REGRESSION MODEL")
    print("=" * 80)
    
    # Load data
    df = load_data(MASTER_DATA_PATH)
    print(f"Loaded {len(df)} observations")
    
    # Create all regime variables
    df = create_regime_variables(df, PERMITTING_REGIMES)
    df = create_regime_variables(df, POLICY_REGIMES)
    df = create_regime_variables(df, SUPPLY_REGIMES)
    
    # Create derived variables
    df = create_derived_variables(df)
    
    # Define variable groups
    GROUPS = define_variable_groups(df)
    
    # Filter to analysis period
    df_reg = df[(df['date'] >= START_DATE) & (df['gas_price'].notna())].copy()
    print(f"Analysis sample: {len(df_reg)} observations")
    
    # Run group regressions
    print("\n--- Individual Group Models ---")
    group_results = run_group_regressions(df_reg, 'gas_price', GROUPS)
    for res in group_results:
        print(f"{res['group']}: R² = {res['r2']:.4f}, MAE = ${res['mae']:.2f}/MMBtu")
    
    # Run incremental regression
    print("\n--- Incremental Model Building ---")
    group_order = ['DEMAND_DRIVERS', 'COST_SUPPLY', 'GENERATION_MIX', 'PERMITTING', 'POLICY_REGULATORY']
    incremental_results = run_incremental_regression(df_reg, 'gas_price', GROUPS, group_order)
    for res in incremental_results:
        print(f"+ {res['step']}: R² = {res['r2']:.4f} (Δ = {res['r2_gain']:+.4f})")
    
    # Run full model
    print("\n--- Full Model ---")
    full_results = run_full_model(df_reg, 'gas_price', GROUPS)
    print(f"R² = {full_results['r2']:.4f}, MAE = ${full_results['mae']:.2f}/MMBtu")
    
    # Save results
    pd.DataFrame(group_results).to_csv('model_comparison_by_group.csv', index=False)
    pd.DataFrame(incremental_results).to_csv('incremental_model_results.csv', index=False)
    
    coef_df = pd.DataFrame({
        'variable': full_results['variables'],
        'coefficient': [full_results['coefficients'][v] for v in full_results['variables']]
    })
    coef_df['abs_coef'] = coef_df['coefficient'].abs()
    coef_df.sort_values('abs_coef', ascending=False).to_csv('full_model_coefficients.csv', index=False)
    
    df_reg.to_csv('full_regression_dataset.csv', index=False)
    
    print("\n✅ Analysis complete. Results saved.")
    
    return df_reg, GROUPS, full_results

if __name__ == "__main__":
    df_reg, GROUPS, full_results = main()
