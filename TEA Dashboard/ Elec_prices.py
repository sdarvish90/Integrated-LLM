# Elec_prices.py
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def load_cost_sheet() -> pd.DataFrame:
    """
    Load 30-year × 8760 electricity prices and export prices.

    Expected CSV columns:
      - 'year'
      - 'Grid electricity final price'
      - 'Export revenue'
      - (any others you had in the Excel sheet)
    """
    path = BASE_DIR / "elec_prices_30y.csv"
    df = pd.read_csv(path)
    return df

# This is what SHARE_model_v1 will import
cost_sheet = load_cost_sheet()
