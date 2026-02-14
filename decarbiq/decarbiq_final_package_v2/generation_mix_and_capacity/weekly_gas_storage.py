import requests
import pandas as pd
from datetime import datetime

def fetch_weekly_gas_storage(start_date='2010-01-01', end_date='2024-12-31'):
    """
    Fetch weekly natural gas storage levels from EIA.
    
    Series: NG.NW2_EPG0_SWO_R48_BCF.W
    - Working gas in underground storage, Lower 48 states
    - Unit: Billion cubic feet (Bcf)
    - Frequency: Weekly (released Thursdays at 10:30 AM ET)
    
    Returns:
        DataFrame with columns: date, storage_bcf, net_change_bcf, yoy_change_bcf
    """
    
    url = "https://api.eia.gov/v2/natural-gas/stor/wkly/data/"
    
    params = {
        'api_key': 'o5nQ7H9b6Bf9Kh3D0cCeC8ydP24drGTiFGaCQrKN',
        'frequency': 'weekly',
        'data[0]': 'value',
        'facets[series][]': 'NW2_EPG0_SWO_R48_BCF',  # Working gas, Lower 48
        'start': start_date,
        'end': end_date,
        'sort[0][column]': 'period',
        'sort[0][direction]': 'asc',
        'offset': 0,
        'length': 5000
    }
    
    all_data = []
    offset = 0
    
    while True:
        params['offset'] = offset
        response = requests.get(url, params=params)
        data = response.json()
        
        if 'response' not in data or 'data' not in data['response']:
            break
            
        batch = data['response']['data']
        if not batch:
            break
            
        all_data.extend(batch)
        offset += len(batch)
        
        if len(batch) < 5000:
            break
    
    df = pd.DataFrame(all_data)
    df['date'] = pd.to_datetime(df['period'])
    df['storage_bcf'] = df['value'].astype(float)
    
    # Calculate week-over-week change
    df = df.sort_values('date')
    df['net_change_bcf'] = df['storage_bcf'].diff()
    
    # Calculate year-over-year change (52 weeks)
    df['yoy_change_bcf'] = df['storage_bcf'].diff(52)
    
    # Add 5-year average for context
    df['week_of_year'] = df['date'].dt.isocalendar().week
    five_year_avg = df.groupby('week_of_year')['storage_bcf'].transform(
        lambda x: x.rolling(window=5, min_periods=1).mean()
    )
    df['storage_vs_5yr_avg_bcf'] = df['storage_bcf'] - five_year_avg
    
    return df[['date', 'storage_bcf', 'net_change_bcf', 'yoy_change_bcf', 
               'storage_vs_5yr_avg_bcf']].set_index('date')


# Fetch data
storage = fetch_weekly_gas_storage(start_date='2010-01-01')
storage.to_csv('gas_storage_weekly.csv')

print(storage.tail(10))
print(f"\nTotal records: {len(storage)}")
print(f"Date range: {storage.index.min()} to {storage.index.max()}")