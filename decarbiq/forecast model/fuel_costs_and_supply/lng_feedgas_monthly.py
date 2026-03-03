import requests
import pandas as pd

def fetch_lng_feedgas_monthly(start_date='2016-01', end_date='2024-12'):
    """
    Fetch U.S. LNG feed gas consumption (exports).

    Series: NGM_EPG0_EVE_NUS-Z00_MMCF
    - Liquefied U.S. Natural Gas Exports by Vessel
    - Unit: Million cubic feet (MMcf)
    - Frequency: Monthly

    Coverage: 2016-present (when LNG exports began meaningfully)
    """

    url = "https://api.eia.gov/v2/natural-gas/move/expc/data/"

    params = {
        'api_key': 'o5nQ7H9b6Bf9Kh3D0cCeC8ydP24drGTiFGaCQrKN',
        'frequency': 'monthly',
        'data[0]': 'value',
        'facets[series][]': 'NGM_EPG0_EVE_NUS-Z00_MMCF',
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
    df['lng_feedgas_mmcf_day'] = df['value'].astype(float)

    # Month-over-month change
    df = df.sort_values('date')
    df['lng_mom_change_bcf'] = df['lng_feedgas_mmcf_day'].diff() / 1000

    return df[['date', 'lng_feedgas_mmcf_day', 'lng_mom_change_bcf']].set_index('date')


# Fetch LNG data
lng_monthly = fetch_lng_feedgas_monthly(start_date='2016-01')
lng_monthly.to_csv('lng_feedgas_monthly.csv')

print(lng_monthly.tail(12))
print(f"\nLatest LNG demand: {lng_monthly['lng_feedgas_mmcf_day'].iloc[-1]:.2f} MMcf/day")
