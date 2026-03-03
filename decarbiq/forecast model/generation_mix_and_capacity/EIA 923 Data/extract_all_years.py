#!/usr/bin/env python3
"""
Extract monthly wind and solar generation for TX and CA from EIA data files
across all available years (1989-2025). Handles different file formats per era.

Output format matches: tx_ca_wind_solar_monthly_YYYY.csv
"""

import pandas as pd
import numpy as np
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATES = ['TX', 'CA']
MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
          'July', 'August', 'September', 'October', 'November', 'December']


def make_empty_csv(year):
    """Create a CSV with 0 values for all months when no monthly data is available."""
    rows = []
    for state in STATES:
        for i, month in enumerate(MONTHS, 1):
            rows.append({
                'year': year,
                'month': i,
                'month_name': month,
                'state': state,
                'total_gen_gwh': 0,
                'wind_gen_gwh': 0,
                'solar_gen_gwh': 0,
                'wind_share_pct': 0,
                'solar_share_pct': 0,
                'renewable_share_pct': 0,
            })
    return pd.DataFrame(rows)


def normalize_columns(df):
    """Normalize column names: strip whitespace, replace newlines with underscores."""
    df.columns = [str(c).replace('\n', ' ').strip() for c in df.columns]
    return df


def find_state_col(df):
    """Find the state column name."""
    for c in df.columns:
        cl = c.lower().strip()
        if cl == 'plant state' or cl == 'state' or cl == 'facilstate':
            return c
    return None


def find_fuel_col(df):
    """Find the fuel type code column name."""
    for c in df.columns:
        cl = c.lower().strip()
        if 'reported' in cl and 'fuel type' in cl:
            return c
        if cl == 'fueltype':
            return c
    return None


def find_netgen_cols(df):
    """Find net generation monthly columns. Returns dict: month_index (0-11) -> col_name."""
    month_abbrevs = {
        'jan': 0, 'feb': 1, 'mar': 2, 'apr': 3, 'may': 4, 'jun': 5,
        'jul': 6, 'aug': 7, 'sep': 8, 'oct': 9, 'nov': 10, 'dec': 11,
        'january': 0, 'february': 1, 'march': 2, 'april': 3,
        'june': 5, 'july': 6, 'august': 7, 'september': 8,
        'october': 9, 'november': 10, 'december': 11
    }
    result = {}
    for c in df.columns:
        cl = c.lower().replace('\n', ' ').strip()
        # Match patterns like NETGEN_JAN, Netgen_Jan, Netgen January, etc.
        if 'netgen' in cl or 'net gen' in cl:
            # Skip annual total columns
            if 'megawatthour' in cl or 'mwh' in cl:
                continue
            for abbr, idx in month_abbrevs.items():
                if abbr in cl:
                    result[idx] = c
                    break
    return result


def extract_monthly_data(df, year, state_col, fuel_col, netgen_cols, wind_codes, solar_codes):
    """Extract monthly wind/solar generation for TX and CA."""
    rows = []
    for state in STATES:
        state_df = df[df[state_col] == state]
        if len(state_df) == 0:
            # No data for this state - output zeros
            for i, month in enumerate(MONTHS):
                rows.append({
                    'year': year,
                    'month': i + 1,
                    'month_name': month,
                    'state': state,
                    'total_gen_gwh': 0,
                    'wind_gen_gwh': 0,
                    'solar_gen_gwh': 0,
                    'wind_share_pct': 0,
                    'solar_share_pct': 0,
                    'renewable_share_pct': 0,
                })
            continue

        for i, month in enumerate(MONTHS):
            if i not in netgen_cols:
                rows.append({
                    'year': year,
                    'month': i + 1,
                    'month_name': month,
                    'state': state,
                    'total_gen_gwh': 0,
                    'wind_gen_gwh': 0,
                    'solar_gen_gwh': 0,
                    'wind_share_pct': 0,
                    'solar_share_pct': 0,
                    'renewable_share_pct': 0,
                })
                continue

            col = netgen_cols[i]
            total_mwh = pd.to_numeric(state_df[col], errors='coerce').sum()

            wind_df = state_df[state_df[fuel_col].isin(wind_codes)]
            wind_mwh = pd.to_numeric(wind_df[col], errors='coerce').sum()

            solar_df = state_df[state_df[fuel_col].isin(solar_codes)]
            solar_mwh = pd.to_numeric(solar_df[col], errors='coerce').sum()

            total_gwh = total_mwh / 1000
            wind_gwh = wind_mwh / 1000
            solar_gwh = solar_mwh / 1000

            wind_pct = (wind_mwh / total_mwh * 100) if total_mwh > 0 else 0
            solar_pct = (solar_mwh / total_mwh * 100) if total_mwh > 0 else 0
            renew_pct = wind_pct + solar_pct

            rows.append({
                'year': year,
                'month': i + 1,
                'month_name': month,
                'state': state,
                'total_gen_gwh': total_gwh,
                'wind_gen_gwh': wind_gwh,
                'solar_gen_gwh': solar_gwh,
                'wind_share_pct': wind_pct,
                'solar_share_pct': solar_pct,
                'renewable_share_pct': renew_pct,
            })
    return pd.DataFrame(rows)


def save_csv(df, year):
    """Save DataFrame to CSV."""
    outpath = os.path.join(BASE_DIR, f'tx_ca_wind_solar_monthly_{year}.csv')
    df.to_csv(outpath, index=False)
    print(f"  -> Saved {outpath}")


# =============================================================================
# Year-specific handlers
# =============================================================================

def process_1989_1998():
    """1989-1998: Annual-only data, no monthly breakdown available."""
    filepath = os.path.join(BASE_DIR, '1989 to 1998 Nonutility Power Producer Data.xls')
    print(f"Loading {filepath}...")
    df = pd.read_excel(filepath, sheet_name='Nonutilty Data 1989-1998')

    for year in range(1989, 1999):
        print(f"Processing {year}...")
        # Only annual data available - create CSV with 0 for monthly values
        result = make_empty_csv(year)
        save_csv(result, year)


def process_1999_2000():
    """1999-2000: Non-utility format with coded fuel types, generation in KWh."""
    files = {
        1999: os.path.join(BASE_DIR, 'f906nonutil1999', 'F9061999nu.xls'),
        2000: os.path.join(BASE_DIR, 'f906nonutil2000', 'F9062000nu.xls'),
    }
    # Fuel codes: D=Wind, E=Solar PV, F=Solar Thermal
    wind_codes = ['D']
    solar_codes = ['E', 'F']

    month_cols = {
        0: 'JANGENERAT', 1: 'FEBGENERAT', 2: 'MARGENERAT', 3: 'APRGENERAT',
        4: 'MAYGENERAT', 5: 'JUNGENERAT', 6: 'JULGENERAT', 7: 'AUGGENERAT',
        8: 'SEPGENERAT', 9: 'OCTGENERAT', 10: 'NOVGENERAT', 11: 'DECGENERAT',
    }

    for year, filepath in files.items():
        print(f"Processing {year}...")
        df = pd.read_excel(filepath)

        rows = []
        for state in STATES:
            state_df = df[df['FACILSTATE'] == state]
            for i, month in enumerate(MONTHS):
                col = month_cols[i]
                # Values are in KWh, convert to MWh
                total_mwh = pd.to_numeric(state_df[col], errors='coerce').sum() / 1000
                wind_df = state_df[state_df['FUELTYPE'].isin(wind_codes)]
                wind_mwh = pd.to_numeric(wind_df[col], errors='coerce').sum() / 1000
                solar_df = state_df[state_df['FUELTYPE'].isin(solar_codes)]
                solar_mwh = pd.to_numeric(solar_df[col], errors='coerce').sum() / 1000

                total_gwh = total_mwh / 1000
                wind_gwh = wind_mwh / 1000
                solar_gwh = solar_mwh / 1000
                wind_pct = (wind_mwh / total_mwh * 100) if total_mwh > 0 else 0
                solar_pct = (solar_mwh / total_mwh * 100) if total_mwh > 0 else 0

                rows.append({
                    'year': year, 'month': i + 1, 'month_name': month,
                    'state': state, 'total_gen_gwh': total_gwh,
                    'wind_gen_gwh': wind_gwh, 'solar_gen_gwh': solar_gwh,
                    'wind_share_pct': wind_pct, 'solar_share_pct': solar_pct,
                    'renewable_share_pct': wind_pct + solar_pct,
                })
        save_csv(pd.DataFrame(rows), year)


def process_2001_2010():
    """2001-2010: EIA-906/920/923 format with NETGEN_JAN columns, header row 7."""
    file_map = {
        2001: os.path.join(BASE_DIR, 'f906920y2001.xls'),
        2002: os.path.join(BASE_DIR, 'f906920y2002.xls'),
        2003: os.path.join(BASE_DIR, 'f906920_2003.xls'),
        2004: os.path.join(BASE_DIR, 'f906920_2004', 'f906920_2004.xls'),
        2005: os.path.join(BASE_DIR, 'f906920_2005', 'f906920_2005.xls'),
        2006: os.path.join(BASE_DIR, 'f906920_2006', 'f906920_2006.xls'),
        2007: os.path.join(BASE_DIR, 'f906920_2007', 'f906920_2007.xls'),
        2008: os.path.join(BASE_DIR, 'f923_2008', 'eia923December2008.xls'),
    }

    # 2009 and 2010 - find the Schedules 2_3_4_5 file
    for year in [2009, 2010]:
        folder = os.path.join(BASE_DIR, f'f923_{year}')
        for f in os.listdir(folder):
            fl = f.lower()
            if ('schedule' in fl or 'eia923' in fl) and ('2_3_4_5' in fl or '2_3_4_5' in f):
                file_map[year] = os.path.join(folder, f)
                break

    wind_codes = ['WND']
    solar_codes = ['SUN']

    for year in range(2001, 2011):
        filepath = file_map.get(year)
        if not filepath or not os.path.exists(filepath):
            print(f"  WARNING: No file found for {year}, creating empty CSV")
            save_csv(make_empty_csv(year), year)
            continue

        print(f"Processing {year}...")
        df = pd.read_excel(filepath, sheet_name='Page 1 Generation and Fuel Data', header=7)
        df = normalize_columns(df)

        state_col = find_state_col(df)
        fuel_col = find_fuel_col(df)
        netgen = find_netgen_cols(df)

        if not state_col or not fuel_col or not netgen:
            print(f"  WARNING: Could not find required columns for {year}")
            print(f"    state_col={state_col}, fuel_col={fuel_col}, netgen_count={len(netgen)}")
            save_csv(make_empty_csv(year), year)
            continue

        result = extract_monthly_data(df, year, state_col, fuel_col, netgen, wind_codes, solar_codes)
        save_csv(result, year)


def process_2011_2025():
    """2011-2025: EIA-923 XLSX format with header row 5, varying column name styles."""
    wind_codes = ['WND']
    solar_codes = ['SUN']

    for year in range(2011, 2026):
        # Find the Schedules 2_3_4_5 file
        filepath = None
        if year == 2025:
            filepath = os.path.join(BASE_DIR, 'EIA923_Schedules_2_3_4_5_M_11_2025_21JAN2026.xlsx')
        else:
            folder = os.path.join(BASE_DIR, f'f923_{year}')
            if os.path.isdir(folder):
                for f in os.listdir(folder):
                    fl = f.lower()
                    if 'schedule' in fl and ('2_3_4_5' in fl or '2_3_4_5' in f):
                        filepath = os.path.join(folder, f)
                        break

        if not filepath or not os.path.exists(filepath):
            print(f"  WARNING: No file found for {year}, creating empty CSV")
            save_csv(make_empty_csv(year), year)
            continue

        print(f"Processing {year}...")
        xl = pd.ExcelFile(filepath)
        # Find the generation and fuel data sheet
        sheet_name = xl.sheet_names[0]
        for s in xl.sheet_names:
            if 'generation' in s.lower() and 'fuel' in s.lower():
                sheet_name = s
                break

        df = pd.read_excel(filepath, sheet_name=sheet_name, header=5)
        df = normalize_columns(df)

        state_col = find_state_col(df)
        fuel_col = find_fuel_col(df)
        netgen = find_netgen_cols(df)

        if not state_col or not fuel_col or not netgen:
            print(f"  WARNING: Could not find required columns for {year}")
            print(f"    state_col={state_col}, fuel_col={fuel_col}, netgen_count={len(netgen)}")
            # Debug: print columns
            print(f"    Columns: {list(df.columns[:20])}")
            save_csv(make_empty_csv(year), year)
            continue

        print(f"  state_col='{state_col}', fuel_col='{fuel_col}', months_found={len(netgen)}")
        result = extract_monthly_data(df, year, state_col, fuel_col, netgen, wind_codes, solar_codes)
        save_csv(result, year)


def main():
    print("=" * 70)
    print("EIA Wind & Solar Monthly Generation Extractor - All Years")
    print("States: TX, CA")
    print("=" * 70)

    print("\n--- 1989-1998 (annual-only data, no monthly breakdown) ---")
    process_1989_1998()

    print("\n--- 1999-2000 (non-utility format, KWh generation) ---")
    process_1999_2000()

    print("\n--- 2001-2010 (EIA-906/920/923 XLS format) ---")
    process_2001_2010()

    print("\n--- 2011-2025 (EIA-923 XLSX format) ---")
    process_2011_2025()

    print("\n" + "=" * 70)
    print("Done! All CSV files created.")
    print("=" * 70)


if __name__ == '__main__':
    main()
