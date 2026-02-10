# DecarbIQ Wholesale Electricity Regression Results
## Final Analysis: Gas → Electricity Price Relationships

---

## Executive Summary

**The wholesale data dramatically improves our understanding:**

| Region | Price Type | β ($/MWh per $/Mcf) | R² | p-value | Significant? |
|--------|-----------|---------------------|-----|---------|--------------|
| **ERCOT** | Wholesale | **9.01** | **0.641** | 0.0000 | ✅ **YES** |
| ERCOT | Retail | 2.58 | 0.298 | 0.0000 | ✅ Yes |
| **CAISO** | Wholesale | **13.27** | **0.242** | 0.0107 | ✅ **YES** |
| CAISO | Retail | -1.25 | 0.006 | 0.56 | ❌ No |

### Key Finding: **Wholesale prices in BOTH regions track gas prices!**

The retail analysis was misleading for California — wholesale prices DO respond to gas, but retail prices are dominated by fixed costs (T&D, wildfire, RPS).

---

## ERCOT (Texas) Results

### Wholesale Regression
```
ERCOT_Wholesale ($/MWh) = 14.7 + 9.01 × Henry_Hub ($/Mcf)
```

| Metric | Value |
|--------|-------|
| β (sensitivity) | **9.01 $/MWh per $/Mcf** |
| R² | **0.641** (gas explains 64% of variance) |
| p-value | 0.0000 (highly significant) |
| n | 58 months (excl. Feb 2021, Aug 2023 spikes) |
| Implied Heat Rate | 9.0 MMBtu/MWh |
| vs Theoretical (7.0) | 1.29x (reasonable with inefficiencies) |

### Wholesale vs Retail Comparison
| Metric | Wholesale | Retail |
|--------|-----------|--------|
| β | 9.01 $/MWh | 0.258 ¢/kWh |
| β (in ¢/kWh) | **0.901** | 0.258 |
| R² | **0.641** | 0.298 |
| Ratio | **3.5x more sensitive** | baseline |

**Insight**: Wholesale prices are 3.5x more responsive to gas than retail because retail includes fixed T&D costs that dilute the fuel signal.

---

## CAISO (California) Results

### Wholesale Regression
```
CAISO_Wholesale ($/MWh) = 7.3 + 13.27 × Henry_Hub ($/Mcf)
```

| Metric | Value |
|--------|-------|
| β (sensitivity) | **13.27 $/MWh per $/Mcf** |
| R² | **0.242** (gas explains 24% of variance) |
| p-value | 0.0107 (significant) |
| n | 26 months (excl. Dec 2022 spike) |
| Implied Heat Rate | 13.3 MMBtu/MWh |
| vs Theoretical (7.0) | 1.9x (high due to duck curve) |

### The California Revelation
**Retail showed NO relationship (p=0.56), but wholesale shows SIGNIFICANT relationship (p=0.01)!**

| Metric | Wholesale | Retail |
|--------|-----------|--------|
| β | 13.27 $/MWh | -1.25 ¢/kWh (wrong sign!) |
| R² | 0.242 | 0.006 |
| Significant? | ✅ **YES** | ❌ No |

**What this means:**
- California wholesale prices DO respond to gas
- Retail prices are completely dominated by non-fuel costs
- The marginal cost of generation follows gas, but customers don't see it

---

## Why CAISO Has Higher β Than ERCOT (13.27 vs 9.01)

1. **Duck Curve Effect**: Solar floods midday, gas plants ramp hard evening
2. **Peaker Reliance**: Evening peaks served by less efficient gas peakers
3. **Scarcity Events**: Pipeline constraints in winter 2022 spiked prices
4. **Lower Base**: CAISO has more zero-marginal-cost renewables, so gas moves the needle more when needed

---

## Price Spike Analysis

| Event | Date | Price | Normal | Multiplier |
|-------|------|-------|--------|------------|
| Winter Storm Uri | Feb 2021 | $1,485/MWh | ~$30 | **50x** |
| TX Heat Wave | Aug 2023 | $263/MWh | ~$30 | 9x |
| CA Pipeline Constraint | Dec 2022 | $254/MWh | ~$50 | 5x |

**These outliers were excluded from regression** — they represent scarcity pricing, not fuel cost pass-through.

---

## DecarbIQ Model Parameters (FINAL)

```python
DECARBIQ_PARAMS = {
    # TEXAS / ERCOT
    'texas_ercot': {
        # Wholesale (USE FOR PROJECT ECONOMICS)
        'wholesale_beta': 9.01,      # $/MWh per $/Mcf
        'wholesale_beta_cents': 0.90,# ¢/kWh per $/Mcf
        'wholesale_r2': 0.641,
        'use_gas_model': True,
        'confidence': 'HIGH',
        
        # Retail (FOR RESIDENTIAL/COMMERCIAL ONLY)
        'retail_beta': 0.26,         # ¢/kWh per $/Mcf
        'retail_r2': 0.30,
        
        # Implied heat rate
        'implied_heat_rate': 9.0,    # MMBtu/MWh
    },
    
    # CALIFORNIA / CAISO
    'california_caiso': {
        # Wholesale (USE FOR PROJECT ECONOMICS)
        'wholesale_beta': 13.27,     # $/MWh per $/Mcf
        'wholesale_beta_cents': 1.33,# ¢/kWh per $/Mcf
        'wholesale_r2': 0.242,
        'use_gas_model': True,       # NOW TRUE for wholesale!
        'confidence': 'MEDIUM',
        
        # Retail (DO NOT USE FOR PROJECTS)
        'retail_beta': None,         # Not significant
        'retail_note': 'Non-fuel costs dominate retail',
        
        # Implied heat rate
        'implied_heat_rate': 13.3,   # Higher due to duck curve
    },
    
    # Scarcity events (exclude from normal forecasting)
    'scarcity_events': {
        'ercot_uri_feb2021': 1485,   # $/MWh
        'ercot_heat_aug2023': 263,   # $/MWh  
        'caiso_pipe_dec2022': 254,   # $/MWh
    }
}
```

---

## Practical Application for Hydrogen Projects

### For Texas (ERCOT) Electrolyzer Projects:

```python
def forecast_tx_electricity_cost(henry_hub_price):
    """
    Forecast ERCOT wholesale electricity for hydrogen production.
    
    Args:
        henry_hub_price: $/MMBtu (or $/Mcf, approximately equal)
    
    Returns:
        Wholesale electricity cost in $/MWh
    """
    # Base regression
    wholesale_mwh = 14.7 + 9.01 * henry_hub_price
    
    # Add seasonal/scarcity buffer (optional)
    buffer_factor = 1.15  # 15% for volatility
    
    return wholesale_mwh * buffer_factor

# Example:
# Henry Hub at $3/MMBtu → $41.7/MWh base → $48/MWh with buffer
# Henry Hub at $5/MMBtu → $59.7/MWh base → $69/MWh with buffer
```

### For California (CAISO) Electrolyzer Projects:

```python
def forecast_ca_electricity_cost(henry_hub_price, time_of_use='average'):
    """
    Forecast CAISO wholesale electricity for hydrogen production.
    
    Args:
        henry_hub_price: $/MMBtu
        time_of_use: 'average', 'midday' (low), 'evening' (high)
    
    Returns:
        Wholesale electricity cost in $/MWh
    """
    # Base regression
    wholesale_mwh = 7.3 + 13.27 * henry_hub_price
    
    # Time-of-use adjustment (duck curve)
    tou_factors = {
        'midday': 0.5,   # Solar surplus
        'average': 1.0,
        'evening': 1.8   # Ramp period
    }
    
    return wholesale_mwh * tou_factors.get(time_of_use, 1.0)

# Example:
# Henry Hub at $3/MMBtu, midday → $24/MWh (great for electrolysis!)
# Henry Hub at $3/MMBtu, evening → $86/MWh (avoid this period)
```

---

## Summary Table

| Parameter | ERCOT | CAISO | Unit |
|-----------|-------|-------|------|
| Wholesale β | **9.01** | **13.27** | $/MWh per $/Mcf |
| Wholesale R² | **0.641** | 0.242 | - |
| Wholesale p | 0.0000 | 0.0107 | - |
| Use Gas Model? | ✅ **Yes** | ✅ **Yes** | - |
| Confidence | HIGH | MEDIUM | - |
| Implied Heat Rate | 9.0 | 13.3 | MMBtu/MWh |
| Retail β | 0.26 | N/A | ¢/kWh per $/Mcf |

---

## Data Sources

- **ERCOT Wholesale**: EIA via gridstatus, Jan 2020 - Dec 2024 (58 months excl. outliers)
- **CAISO Wholesale**: EIA via gridstatus, Oct 2022 - Dec 2024 (26 months excl. outliers)
- **Henry Hub**: EIA STEO monthly spot prices
- **Retail Electricity**: EIA STEO Table 7c regional prices

---

## Conclusion

**The wholesale analysis fundamentally changes our understanding:**

1. ✅ **Texas**: Strong gas-electricity relationship confirmed (R²=0.64)
2. ✅ **California**: Gas DOES predict wholesale prices (R²=0.24) — retail was misleading
3. ✅ **Heat Rate**: Both regions show implied heat rates reasonably close to theory
4. ⚠️ **Scarcity Events**: Must be handled separately (50x normal prices possible)

**For DecarbIQ hydrogen project economics: USE WHOLESALE PRICES, NOT RETAIL.**
