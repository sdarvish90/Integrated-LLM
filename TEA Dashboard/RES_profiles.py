# RES_profiles.py
import pandas as pd
from pathlib import Path

"""
RES_profiles.py

Handles loading 1-year (8760 h) wind & solar profiles and
building multi-year (e.g. 10-year) time series.

Expected files (in the same folder as this script):
  - wind_8760.csv  : 8760 rows, first column = wind profile (per-unit or MW)
  - pv_8760.csv    : 8760 rows, first column = solar profile

You can scale them later in the model by installed capacity.
"""

BASE_DIR = Path(__file__).resolve().parent

# -----------------------------
# Internal helpers
# -----------------------------

def _load_8760_series(filename: str) -> pd.Series:
    """Load a single-column 8760-hour profile from CSV."""
    path = BASE_DIR / filename
    df = pd.read_csv(path, header=None)
    # Take the first column as the profile
    series = df.iloc[:, 0]

    if len(series) != 8761:
        raise ValueError(f"{filename} must have 8761 rows (1 year label + 8760 data); got {len(series)}")

    # Drop the first row (year label) and keep only the 8760 hourly values
    series = series.iloc[1:].reset_index(drop=True)
    series = series.astype(float)
    series.name = filename.replace(".csv", "")
    return series


def _build_multi_year(series: pd.Series,
                      n_years: int = 10,
                      start_date: str = "2023-01-01",
                      freq: str = "1h") -> pd.Series:
    """
    Repeat a 1-year 8760-hour series over n_years and attach a datetime index.
    """
    if n_years <= 0:
        raise ValueError("n_years must be >= 1")

    repeated = pd.concat([series] * n_years, ignore_index=True)
    total_hours = len(repeated)

    index = pd.date_range(start=start_date, periods=total_hours, freq=freq)
    if len(index) != total_hours:
        raise ValueError("Date index length mismatch with repeated series.")

    return pd.Series(repeated.to_numpy(), index=index, name=series.name)


# -----------------------------
# Public API
# -----------------------------

# 1-year base profiles (8760 h each)
wind_1y = _load_8760_series("wind_8760.csv")
pv_1y   = _load_8760_series("pv_8760.csv")


def get_wind_profile(n_years: int = 10,
                     start_date: str = "2023-01-01",
                     freq: str = "1h") -> pd.Series:
    """
    Return a multi-year wind profile (default 10 years).
    """
    return _build_multi_year(wind_1y, n_years=n_years,
                             start_date=start_date, freq=freq)


def get_pv_profile(n_years: int = 10,
                   start_date: str = "2023-01-01",
                   freq: str = "1h") -> pd.Series:
    """
    Return a multi-year solar/PV profile (default 10 years).
    """
    return _build_multi_year(pv_1y, n_years=n_years,
                             start_date=start_date, freq=freq)
