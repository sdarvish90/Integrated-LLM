# DecarbIQ Electricity Price Model v2.0
## Advanced Methodology Documentation

---

## Overview

This model predicts wholesale electricity prices for ERCOT (Texas) and CAISO (California) based on natural gas prices and other fundamentals. It goes beyond simple regression to provide:

- **Multivariate control** for storage, seasonality
- **Quantile forecasts** (P10/P50/P90 scenarios)
- **Scarcity event detection** and simulation
- **Seasonal decomposition**
- **Monte Carlo uncertainty quantification**

---

## Model Components

### 1. Scarcity Event Detection

**Purpose**: Identify and separate extreme price events that cannot be predicted by fundamentals.

**Method**: Robust threshold using Median Absolute Deviation (MAD)
```
threshold = median + 2.5 × 1.4826 × MAD
```

**Results**:
| Grid | Threshold | Events Detected |
|------|-----------|-----------------|
| ERCOT | $65/MWh | 9 events (Feb 2021 Uri, Summer 2022, Aug 2023) |
| CAISO | $91/MWh | 2 events (Dec 2022, Jan 2023 pipeline constraints) |

**Use**: These events are excluded from normal regression, modeled separately for risk analysis.

---

### 2. Multivariate Regression

**Purpose**: Control for multiple factors beyond just gas price.

**Model**:
```
Price = β₀ + β₁(gas_price) + β₂(storage_zscore) + β₃(is_summer) + β₄(is_winter) + ε
```

**ERCOT Results** (excluding scarcity events):
| Variable | Coefficient | p-value | Interpretation |
|----------|-------------|---------|----------------|
| Intercept | 8.00 | 0.008** | Base price when gas=$0 |
| **Henry Hub** | **7.01** | **0.000***| **+$7/MWh per $1/Mcf gas** |
| Storage Z-score | 0.14 | 0.89 | Not significant |
| Summer (Jun-Sep) | 5.57 | 0.04* | +$5.6/MWh in summer |
| Winter (Dec-Feb) | -2.16 | 0.41 | Not significant |

- **R² = 0.653** (gas + season explain 65% of variance)
- **Implied heat rate = 7.0 MMBtu/MWh** (matches theoretical perfectly!)

**CAISO Results** (excluding scarcity events):
| Variable | Coefficient | p-value | Interpretation |
|----------|-------------|---------|----------------|
| Intercept | -32.87 | 0.14 | Not significant |
| **Henry Hub** | **29.69** | **0.004**| **+$30/MWh per $1/Mcf gas** |
| Storage Z-score | -6.29 | 0.12 | Borderline (high storage → lower price) |
| Summer | 6.79 | 0.40 | Not significant |
| Winter | 1.79 | 0.86 | Not significant |

- **R² = 0.441** (gas explains 44% of variance)
- Higher coefficient reflects duck curve dynamics

---

### 3. Quantile Regression

**Purpose**: Forecast not just the mean, but the range of outcomes (P10/P50/P90).

**Why it matters**: For hydrogen project FID, you need:
- **P50 (base case)**: Most likely price
- **P10 (downside)**: Conservative low-price scenario
- **P90 (upside)**: High-price stress test

**Results** (at $3.50/Mcf gas, summer month):

| Grid | P10 | P50 | P90 | Range |
|------|-----|-----|-----|-------|
| ERCOT | $35/MWh | $40/MWh | $55/MWh | ±$10-15 |
| CAISO | $63/MWh | $84/MWh | $128/MWh | ±$20-44 |

CAISO has much wider uncertainty range (duck curve volatility).

---

### 4. Seasonal Decomposition

**Purpose**: Capture predictable monthly patterns.

**ERCOT Seasonal Factors** ($/MWh adjustment):
| Month | Adjustment | Reason |
|-------|------------|--------|
| Jan | -3.4 | Mild winter, low demand |
| Feb | -12.3 | Typically mild (Uri was outlier) |
| Jul | **+9.1** | Peak AC demand |
| Aug | +6.1 | High demand continues |
| Oct | +7.3 | Shoulder month volatility |

**CAISO Seasonal Factors** ($/MWh adjustment):
| Month | Adjustment | Reason |
|-------|------------|--------|
| Jan | **+24.5** | Low solar, gas peakers needed |
| Apr | -26.0 | Spring solar surplus |
| May | -24.5 | Peak solar, low demand |
| Jul | +14.1 | Evening ramp, AC load |
| Aug | +22.2 | Heat wave risk |

---

### 5. Monte Carlo Simulation

**Purpose**: Propagate uncertainty through forecast horizon.

**Components**:
1. Base prediction from multivariate model
2. Residual noise (normal distribution, σ = $7 ERCOT, $14 CAISO)
3. Scarcity event probability (15% annual)
4. Scarcity severity (sampled from historical)

**Output**: Distribution of prices at each forecast period, enabling:
- Expected value (mean)
- VaR-style risk metrics (P5, P95)
- Probability of exceeding threshold
- Scarcity event probability

---

## Final Model Parameters

### ERCOT (Texas)

```python
ERCOT_MODEL = {
    # Base equation (normal conditions)
    'intercept': 8.00,
    'gas_coefficient': 7.01,  # $/MWh per $/Mcf
    'summer_adder': 5.57,     # Jun-Sep
    'winter_adder': -2.16,    # Dec-Feb
    
    # Implied heat rate
    'heat_rate': 7.0,  # MMBtu/MWh (matches theory!)
    
    # Uncertainty
    'residual_std': 6.9,  # $/MWh
    'r_squared': 0.653,
    
    # Scarcity threshold
    'scarcity_threshold': 65,  # $/MWh
    'scarcity_events': ['2021-02', '2022-05:09', '2023-06', '2023-08:09'],
    
    # Historical scarcity prices
    'uri_feb2021': 1485,  # $/MWh
    'aug2023_heat': 263,  # $/MWh
    'summer2022_avg': 90, # $/MWh
}
```

### CAISO (California)

```python
CAISO_MODEL = {
    # Base equation (normal conditions)
    'intercept': -32.87,
    'gas_coefficient': 29.69,  # $/MWh per $/Mcf (high due to duck curve)
    'summer_adder': 6.79,
    'winter_adder': 1.79,
    
    # Implied heat rate
    'heat_rate': 29.7,  # Much higher than theory (peaker reliance)
    
    # Uncertainty
    'residual_std': 14.1,  # $/MWh (higher volatility)
    'r_squared': 0.441,
    
    # Scarcity threshold
    'scarcity_threshold': 91,  # $/MWh
    'scarcity_events': ['2022-12', '2023-01'],
    
    # Historical scarcity prices
    'dec2022_pipeline': 254,  # $/MWh
    'jan2023_cold': 139,      # $/MWh
}
```

---

## Usage Example

```python
from decarbiq_price_model_v2 import DecarbIQPriceModel

# Initialize and fit
model = DecarbIQPriceModel()
model.fit()

# Predict ERCOT price
# Gas at $3.50/Mcf, July, normal storage
price_p50 = model.predict_ercot(gas_price=3.50, month=7, scenario='P50')
# → $40/MWh

# Get P10/P90 range for risk analysis
price_p10 = model.predict_ercot(gas_price=3.50, month=7, scenario='P10')
price_p90 = model.predict_ercot(gas_price=3.50, month=7, scenario='P90')
# → $35-55/MWh range

# For California
price_caiso = model.predict_caiso(gas_price=3.50, month=7, scenario='P50')
# → $84/MWh (much higher, duck curve effect)
```

---

## Key Insights for Hydrogen Projects

### ERCOT (Texas) - Favorable for Electrolysis

1. **Strong gas-price linkage** (R²=0.65, β=7.0)
2. **Predictable heat rate** matches theory
3. **Summer premium** ($5-9/MWh) - consider load flexibility
4. **Scarcity risk**: 15% annual probability, avg severity $90-260/MWh

**Recommendation**: Base electrolyzer economics on P50 prices, stress test at P90, add scarcity insurance for extreme events.

### CAISO (California) - Complex for Electrolysis

1. **High gas sensitivity** (β=30) but lower R² (0.44)
2. **Wide uncertainty band** (±$20-40/MWh)
3. **Strong seasonality**: Avoid Jan (expensive), target Apr-May (cheap solar surplus)
4. **Duck curve opportunity**: Midday prices can go negative → electrolyzer sweet spot

**Recommendation**: Time-of-use strategy essential. Target 10am-3pm for lowest costs. Avoid evening ramp (4-9pm).

---

## Limitations and Future Work

### Current Limitations
- Monthly data smooths intra-day volatility
- CAISO data only from Oct 2022 (limited history)
- No explicit renewable curtailment variable
- Storage assumes linear relationship

### Recommended Enhancements
1. **Hourly data**: Capture intra-day patterns, negative pricing
2. **Renewable penetration**: Add solar/wind generation as variable
3. **VAR model**: Vector autoregression for lagged effects
4. **GARCH**: Model volatility clustering
5. **Regime switching**: Explicit normal/scarcity state model

---

## Files Delivered

| File | Description |
|------|-------------|
| `decarbiq_price_model_v2.py` | Main model code with all components |
| `decarbiq_model_params.json` | Exported parameters for production use |
| `wholesale_regression_summary.md` | Regression analysis documentation |
| `ercot_wholesale_merged.csv` | ERCOT data with fundamentals |
| `caiso_wholesale_merged.csv` | CAISO data with fundamentals |
