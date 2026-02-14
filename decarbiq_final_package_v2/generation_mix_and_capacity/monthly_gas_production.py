import requests
import pandas as pd

def fetch_monthly_gas_production(start_date='2010-01', end_date='2024-12'):
    """
    Fetch U.S. dry natural gas production from EIA.
    
    Series: NG.N9070US2.M
    - U.S. dry natural gas production
    - Unit: Million cubic feet per day (MMcf/d)
    - Frequency: Monthly
    
    Returns:
        DataFrame with: date, production_mmcf_day, production_bcf_month
    """
    
    url = "https://api.eia.gov/v2/natural-gas/prod/sum/data/"
    
    params = {
        'api_key': 'o5nQ7H9b6Bf9Kh3D0cCeC8ydP24drGTiFGaCQrKN',
        'frequency': 'monthly',
        'data[0]': 'value',
        'facets[series][]': 'N9070US2',  # U.S. Dry Natural Gas Production (MMcf)
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
    df['production_mmcf_day'] = df['value'].astype(float)
    
    # Calculate monthly total
    df['days_in_month'] = df['date'].dt.days_in_month
    df['production_bcf_month'] = (df['production_mmcf_day'] * df['days_in_month']) / 1000
    
    # Year-over-year growth
    df = df.sort_values('date')
    df['production_yoy_pct'] = df['production_mmcf_day'].pct_change(12) * 100
    
    return df[['date', 'production_mmcf_day', 'production_bcf_month', 
               'production_yoy_pct']].set_index('date')


# Fetch production
production_monthly = fetch_monthly_gas_production(start_date='2010-01')
production_monthly.to_csv('gas_production_monthly.csv')

print(production_monthly.tail(12))