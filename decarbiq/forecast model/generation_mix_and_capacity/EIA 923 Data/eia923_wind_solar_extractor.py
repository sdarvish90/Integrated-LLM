#!/usr/bin/env python3
"""
EIA-923 Wind & Solar Monthly Generation Extractor

This script extracts monthly wind and solar generation data by state from 
EIA Form 923 Excel files (Schedules 2,3,4,5 monthly data).

Download historical files from:
https://www.eia.gov/electricity/data/eia923/

Usage:
    python eia923_wind_solar_extractor.py <path_to_eia923_file.xlsx> [--states TX,CA] [--output output.csv]
"""

import pandas as pd
import argparse
import sys
from pathlib import Path


# EIA Fuel Type Codes for renewables
FUEL_CODES = {
    'WND': 'Wind',
    'SUN': 'Solar (PV + Thermal)',
    'WAT': 'Hydro (Conventional)',
    'GEO': 'Geothermal',
    'WDS': 'Wood/Wood Waste Solids',
    'LFG': 'Landfill Gas',
    'OBG': 'Other Biomass Gas',
    'NUC': 'Nuclear',
    'NG': 'Natural Gas',
    'BIT': 'Bituminous Coal',
    'SUB': 'Subbituminous Coal'
}

MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December']


def load_eia923(filepath: str) -> pd.DataFrame:
    """Load EIA-923 Generation and Fuel Data sheet."""
    print(f"Loading {filepath}...")
    
    # Try different possible sheet names
    xl = pd.ExcelFile(filepath)
    sheet_name = None
    for name in xl.sheet_names:
        if 'generation' in name.lower() and 'fuel' in name.lower():
            sheet_name = name
            break
    
    if not sheet_name:
        # Default to first sheet that looks like generation data
        sheet_name = "Page 1 Generation and Fuel Data"
    
    print(f"  Using sheet: {sheet_name}")
    
    df = pd.read_excel(filepath, sheet_name=sheet_name, header=5)
    
    # Clean column names (remove newlines)
    df.columns = [str(c).replace('\n', '_') for c in df.columns]
    
    return df


def extract_year(df: pd.DataFrame) -> int:
    """Extract year from the data."""
    if 'YEAR' in df.columns:
        return int(df['YEAR'].iloc[0])
    return None


def extract_monthly_generation(df: pd.DataFrame, states: list = None, 
                                fuel_types: list = None) -> pd.DataFrame:
    """
    Extract monthly generation data aggregated by state and fuel type.
    
    Args:
        df: EIA-923 dataframe
        states: List of state codes to filter (e.g., ['TX', 'CA']). None = all states.
        fuel_types: List of fuel codes to extract (e.g., ['WND', 'SUN']). None = all.
    
    Returns:
        DataFrame with monthly generation by state and fuel type
    """
    year = extract_year(df)
    
    # Identify netgen columns
    netgen_cols = [c for c in df.columns if c.startswith('Netgen_')]
    
    if not netgen_cols:
        raise ValueError("No Netgen columns found in data")
    
    # Filter by states if specified
    if states:
        df = df[df['Plant State'].isin(states)]
    
    # Get unique states and fuel types
    all_states = df['Plant State'].unique()
    all_fuels = df['Reported_Fuel Type Code'].unique()
    
    if fuel_types:
        target_fuels = fuel_types
    else:
        target_fuels = all_fuels
    
    results = []
    
    for state in all_states:
        state_df = df[df['Plant State'] == state]
        
        for i, month in enumerate(MONTHS):
            col = f'Netgen_{month}'
            if col not in df.columns:
                continue
            
            # Total generation for state
            total_mwh = pd.to_numeric(state_df[col], errors='coerce').sum()
            
            row = {
                'year': year,
                'month': i + 1,
                'month_name': month,
                'state': state,
                'total_gen_mwh': total_mwh,
                'total_gen_gwh': total_mwh / 1000,
            }
            
            # Extract each fuel type
            for fuel in target_fuels:
                fuel_df = state_df[state_df['Reported_Fuel Type Code'] == fuel]
                fuel_mwh = pd.to_numeric(fuel_df[col], errors='coerce').sum()
                
                fuel_name = FUEL_CODES.get(fuel, fuel).lower().replace(' ', '_').replace('/', '_')
                row[f'{fuel_name}_mwh'] = fuel_mwh
                row[f'{fuel_name}_gwh'] = fuel_mwh / 1000
                row[f'{fuel_name}_share_pct'] = (fuel_mwh / total_mwh * 100) if total_mwh > 0 else 0
            
            results.append(row)
    
    return pd.DataFrame(results)


def main():
    parser = argparse.ArgumentParser(description='Extract wind/solar data from EIA-923')
    parser.add_argument('filepath', help='Path to EIA-923 Excel file')
    parser.add_argument('--states', default='TX,CA', help='Comma-separated state codes (default: TX,CA)')
    parser.add_argument('--fuels', default='WND,SUN', help='Comma-separated fuel codes (default: WND,SUN for wind/solar)')
    parser.add_argument('--output', '-o', help='Output CSV path (default: auto-generated)')
    parser.add_argument('--all-states', action='store_true', help='Extract all states')
    
    args = parser.parse_args()
    
    # Parse states and fuels
    states = None if args.all_states else [s.strip() for s in args.states.split(',')]
    fuels = [f.strip() for f in args.fuels.split(',')]
    
    # Load data
    df = load_eia923(args.filepath)
    
    # Extract year
    year = extract_year(df)
    print(f"  Data year: {year}")
    print(f"  Total records: {len(df)}")
    
    # Extract monthly generation
    result = extract_monthly_generation(df, states=states, fuel_types=fuels)
    
    # Generate output path
    if args.output:
        output_path = args.output
    else:
        state_str = 'all_states' if args.all_states else '_'.join(states)
        output_path = f"eia923_monthly_{state_str}_{year}.csv"
    
    # Save
    result.to_csv(output_path, index=False)
    print(f"\nSaved to: {output_path}")
    
    # Display summary
    print(f"\nSummary for {year}:")
    print("=" * 70)
    
    for state in result['state'].unique():
        state_data = result[result['state'] == state]
        print(f"\n{state}:")
        print(f"  Total Generation: {state_data['total_gen_gwh'].sum():,.1f} GWh")
        
        for fuel in fuels:
            fuel_name = FUEL_CODES.get(fuel, fuel).lower().replace(' ', '_').replace('/', '_')
            col = f'{fuel_name}_gwh'
            if col in state_data.columns:
                annual_gwh = state_data[col].sum()
                share = annual_gwh / state_data['total_gen_gwh'].sum() * 100
                print(f"  {FUEL_CODES.get(fuel, fuel)}: {annual_gwh:,.1f} GWh ({share:.1f}%)")


if __name__ == '__main__':
    main()
