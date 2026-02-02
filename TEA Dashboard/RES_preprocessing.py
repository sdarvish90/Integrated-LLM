"""
RES_preprocessing_wind_only_optionA_B.py (wind only)

Build wind profiles for the full project lifetime using:
Option A) Annual inter-annual variability (IAV) multiplier + turbine degradation
Option B) Optional seasonal (quarterly) multipliers within each year

Outputs (module-level):
- wind_prod: DataFrame (8760 x project_lifetime) with DateTimeIndex and columns 1..project_lifetime
- wind_8760_lifetime: DataFrame (8761 x project_lifetime) with row 0 = calendar years, rows 1..8760 = hourly MW

Assumptions about wind_8760.csv:
- Row 0 contains year labels per column (may be numeric years, or any labels)
- Rows 1..8760 contain hourly values (may include commas as thousands separators)

This module reads scenario parameters from inputs.py.

New inputs expected in inputs.py (with defaults if missing):
- wind_iav_sigma: float (e.g., 0.08)  # annual energy variability sigma (lognormal), 0 disables
- wind_iav_seed: int (e.g., 42)
- wind_degradation_rate: float (e.g., 0.005)  # 0.5%/yr, 0 disables
- wind_use_seasonal_iav: bool (e.g., True)
- wind_seasonal_sigma: float (e.g., 0.05)  # seasonal variability sigma (lognormal), 0 disables
- wind_write_debug_files: bool (e.g., True)  # write debug CSVs when importing
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path

import inputs  # keep naming consistent across files


# -----------------------------
# Helpers
# -----------------------------
def _clean_numeric_df(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce a DataFrame to numeric, stripping commas/spaces. Raises on non-numeric."""
    def _clean_col(col: pd.Series) -> pd.Series:
        s = col.astype(str).str.replace(",", "", regex=False).str.strip()
        return pd.to_numeric(s, errors="raise")
    return df.apply(_clean_col)


def _pick_start_year_column(wind_raw: pd.DataFrame, start_year: int) -> int:
    """
    Try to select a column whose row-0 label matches start_year (e.g., 2025).
    If not found, fall back to column 0.
    """
    labels = wind_raw.iloc[0, :].tolist()
    for i, lab in enumerate(labels):
        try:
            if int(str(lab).strip()) == int(start_year):
                return i
        except Exception:
            continue
    return 0


def _season_index(dt_index: pd.DatetimeIndex) -> np.ndarray:
    """
    Map each timestamp to a season bucket 0..3:
      0: DJF (Dec/Jan/Feb)
      1: MAM (Mar/Apr/May)
      2: JJA (Jun/Jul/Aug)
      3: SON (Sep/Oct/Nov)
    """
    m = dt_index.month.values
    season = np.empty_like(m, dtype=int)
    season[(m == 12) | (m == 1) | (m == 2)] = 0
    season[(m >= 3) & (m <= 5)] = 1
    season[(m >= 6) & (m <= 8)] = 2
    season[(m >= 9) & (m <= 11)] = 3
    return season


def _get_input(name: str, default):
    """Fetch input from inputs.py with a safe default."""
    return getattr(inputs, name, default)


# -----------------------------
# Core builder
# -----------------------------
def build_wind_profiles_lifetime(
    wind_csv_path: str | Path = "wind_8760.csv",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns:
      wind_prod: 8760 x project_lifetime with DateTimeIndex, columns 1..project_lifetime
      wind_8760_lifetime: 8761 x project_lifetime with row0 year labels, no DateTimeIndex
    """
    wind_csv_path = Path(wind_csv_path)

    # --- Inputs ---
    start_date_str: str = _get_input("start_date", "01/01/2025")
    freq: str = _get_input("freq", "H")
    project_lifetime: int = int(_get_input("project_lifetime", 25))

    wind_capacity_ref: float = float(_get_input("wind_capacity_ref", 0.0))

    # Option A
    wind_iav_sigma: float = float(_get_input("wind_iav_sigma", 0.0))  # 0 disables
    wind_iav_seed: int = int(_get_input("wind_iav_seed", 42))
    wind_degradation_rate: float = float(_get_input("wind_degradation_rate", 0.0))  # 0 disables

    # Option B
    wind_use_seasonal_iav: bool = bool(_get_input("wind_use_seasonal_iav", False))
    wind_seasonal_sigma: float = float(_get_input("wind_seasonal_sigma", 0.0))  # 0 disables

    # Debug output toggle
    write_debug: bool = bool(_get_input("wind_write_debug_files", False))

    # --- Parse start year & build timestamp index for a single year ---
    start_dt = datetime.strptime(start_date_str, "%m/%d/%Y")
    start_year = start_dt.year
    ts = pd.date_range(start=start_dt, periods=8760, freq=freq)

    # --- Read wind CSV ---
    wind_raw = pd.read_csv(wind_csv_path, header=None)

    # Hourly rows should be 8760 after dropping row 0
    wind_hourly = wind_raw.iloc[1:, :].reset_index(drop=True)
    if len(wind_hourly) != 8760:
        raise ValueError(
            f"{wind_csv_path.name}: expected 8760 hourly rows after dropping row 0, got {len(wind_hourly)}"
        )

    wind_hourly = _clean_numeric_df(wind_hourly)

    # Select base column
    col_idx = _pick_start_year_column(wind_raw, start_year)
    base_profile = wind_hourly.iloc[:, col_idx].copy()
    base_profile.index = ts

    # --- Scale base to wind_capacity_ref using max() as "current nameplate" ---
    nameplate_current = float(base_profile.max())
    if wind_capacity_ref <= 0 or nameplate_current <= 0:
        # Solar-only scenario: return zero DataFrames matching normal format
        wind_prod = pd.DataFrame(
            np.zeros((8760, project_lifetime)),
            index=ts,
            columns=range(1, project_lifetime + 1),
        )
        years_row = [start_year + i for i in range(project_lifetime)]
        wind_8760_lifetime = pd.DataFrame(
            np.vstack([years_row, np.zeros((8760, project_lifetime))]),
            columns=range(1, project_lifetime + 1),
        )
        return wind_prod, wind_8760_lifetime

    y1_profile = base_profile * (wind_capacity_ref / nameplate_current)

    # --- Option A: annual IAV multipliers (lognormal around 1.0) ---
    rng = np.random.default_rng(wind_iav_seed)
    if wind_iav_sigma and wind_iav_sigma > 0:
        annual_mult = rng.lognormal(mean=0.0, sigma=wind_iav_sigma, size=project_lifetime)
        # normalize to mean 1.0 (so you don't bias long-term energy)
        annual_mult = annual_mult / annual_mult.mean()
    else:
        annual_mult = np.ones(project_lifetime, dtype=float)

    # --- Degradation (monotonic) ---
    if wind_degradation_rate and wind_degradation_rate > 0:
        degr = (1.0 - wind_degradation_rate) ** np.arange(project_lifetime, dtype=float)
    else:
        degr = np.ones(project_lifetime, dtype=float)

    # --- Option B: seasonal multipliers (4 seasons) per year, normalized per-year mean = 1 ---
    season_bucket = _season_index(ts)  # length 8760, values 0..3

    if wind_use_seasonal_iav and wind_seasonal_sigma and wind_seasonal_sigma > 0:
        seasonal_mult_by_year = rng.lognormal(mean=0.0, sigma=wind_seasonal_sigma, size=(project_lifetime, 4))

        # normalize each year's seasonal multipliers so the weighted hourly mean is 1
        counts = np.bincount(season_bucket, minlength=4).astype(float)
        weights = counts / counts.sum()
        weighted_mean = (seasonal_mult_by_year * weights).sum(axis=1)  # (project_lifetime,)
        seasonal_mult_by_year = seasonal_mult_by_year / weighted_mean[:, None]
    else:
        seasonal_mult_by_year = np.ones((project_lifetime, 4), dtype=float)

    # --- Build wind_prod ---
    wind_prod = pd.DataFrame(index=ts)

    for y in range(1, project_lifetime + 1):
        idx = y - 1

        prof = y1_profile.copy()

        # Option A: annual multiplier + degradation
        prof = prof * annual_mult[idx] * degr[idx]

        # Option B: seasonal reshaping
        sm = seasonal_mult_by_year[idx]  # length 4
        prof = prof * sm[season_bucket]

        # Physical cap: wind output cannot exceed nameplate capacity (MW)
        # After applying variability/degradation multipliers, clip to wind_capacity_ref.
        prof = prof.clip(upper=wind_capacity_ref)

        wind_prod[y] = prof.values

    wind_prod.columns = range(1, project_lifetime + 1)

    # --- Build 8761-row lifetime table ---
    years_row = [start_year + i for i in range(project_lifetime)]
    wind_8760_lifetime = pd.DataFrame(
        np.vstack([years_row, wind_prod.to_numpy()]),
        columns=wind_prod.columns,
    )

    # # --- Optional debug outputs ---
    # if write_debug:
    #     wind_prod.to_csv("DEBUG_wind_prod_8760xN.csv")
    #     wind_8760_lifetime.to_csv("DEBUG_wind_8760_lifetime.csv", index=False, header=False)

    #     factors_df = pd.DataFrame(
    #         {
    #             "project_year": np.arange(1, project_lifetime + 1),
    #             "calendar_year": years_row,
    #             "annual_IAV_multiplier": annual_mult,
    #             "degradation_multiplier": degr,
    #             "combined_annual_multiplier": annual_mult * degr,
    #             "DJF_mult": seasonal_mult_by_year[:, 0],
    #             "MAM_mult": seasonal_mult_by_year[:, 1],
    #             "JJA_mult": seasonal_mult_by_year[:, 2],
    #             "SON_mult": seasonal_mult_by_year[:, 3],
    #         }
    #     )
    #     factors_df.to_csv("DEBUG_wind_year_multipliers.csv", index=False)

    return wind_prod, wind_8760_lifetime


# -----------------------------
# Module-level outputs (used by SHARE_model_v1)
# -----------------------------
wind_prod, wind_8760_lifetime = build_wind_profiles_lifetime("wind_8760.csv")


# -----------------------------
# Run standalone to write debug outputs
# -----------------------------
# if __name__ == "__main__":
#     # Force debug outputs when running directly
#     setattr(inputs, "wind_write_debug_files", True)
#     wind_prod, wind_8760_lifetime = build_wind_profiles_lifetime("wind_8760.csv")
#     print("Wrote DEBUG_wind_prod_8760xN.csv, DEBUG_wind_8760_lifetime.csv, DEBUG_wind_year_multipliers.csv")
#     print("wind_prod shape:", wind_prod.shape)
#     print("wind_8760_lifetime shape:", wind_8760_lifetime.shape)


# =========================
# PV (Level 1: degradation only)
# =========================

# --- Load one 8760 PV profile (assumes single column CSV with 8760 rows) ---
pv_raw = pd.read_csv("pv_8760.csv", header=None)

# If your pv_8760.csv has a year-label row like wind, drop it.
# We detect this safely: if length is 8761, assume row 0 is metadata.
if len(pv_raw) == 8761:
    pv_series = pv_raw.iloc[1:, 0]
else:
    pv_series = pv_raw.iloc[:, 0]

# Ensure exactly 8760 rows
pv_series = pv_series.reset_index(drop=True)
if len(pv_series) != 8760:
    raise ValueError(f"pv_8760.csv must contain 8760 hourly values (got {len(pv_series)})")

# Clean numeric (handles "1,234" strings)
pv_series = pd.to_numeric(
    pv_series.astype(str).str.replace(",", "", regex=False).str.strip(),
    errors="raise",
)

# --- Get PV ref capacity and degradation from inputs ---
PV_capacity_ref_AC = float(getattr(inputs, "PV_capacity_ref_AC"))
PV_ann_degr = float(getattr(inputs, "PV_ann_degr", 0.0))  # default 0 if missing

# --- Scale base PV profile to PV_capacity_ref_AC using max(8760) as base nameplate ---
pv_nameplate_current = float(pv_series.max())
if pv_nameplate_current <= 0:
    raise ValueError("PV base profile max is non-positive; cannot scale to nameplate.")

pv_y1 = pv_series * (PV_capacity_ref_AC / pv_nameplate_current)

# Cap at nameplate (physical AC cap)
pv_y1 = pv_y1.clip(lower=0.0, upper=PV_capacity_ref_AC)

# --- Build lifetime PV: 8760 × project_lifetime with annual degradation ---
# Year y multiplier = (1 - PV_ann_degr)^(y-1)
pv_cols = {}
for y in range(1, int(inputs.project_lifetime) + 1):
    degr_factor = (1.0 - PV_ann_degr) ** (y - 1)
    prof = (pv_y1 * degr_factor).clip(lower=0.0, upper=PV_capacity_ref_AC)
    pv_cols[y] = prof.values

PV_prod = pd.DataFrame(pv_cols)

# Add timestamp index (matches your wind timestamp logic)
PV_prod["timestamp"] = pd.date_range(
    start=inputs.start_date,
    periods=8760,
    freq=inputs.freq,
)
PV_prod = PV_prod.set_index("timestamp")

# Optional: Excel-friendly lifetime format (8761 rows: first row is years)
start_year = datetime.strptime(inputs.start_date, "%m/%d/%Y").year
years_row = [start_year + (y - 1) for y in range(1, int(inputs.project_lifetime) + 1)]
pv_8760_lifetime = pd.DataFrame(
    np.vstack([years_row, PV_prod.reset_index(drop=True).to_numpy()]),
    columns=range(1, int(inputs.project_lifetime) + 1),
)

# Optional: write debug CSVs when running this file directly
# if __name__ == "__main__":
#     PV_prod.to_csv("DEBUG_PV_prod_8760xN.csv")
#     pv_8760_lifetime.to_csv("DEBUG_PV_8760_lifetime.csv", index=False, header=False)
#     # print("Saved: DEBUG_PV_prod_8760xN.csv")
#     # print("Saved: DEBUG_PV_8760_lifetime.csv")
