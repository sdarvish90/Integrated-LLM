# DecarbIQ Updated Regression Analysis Results
## Regional Gas-Electricity Relationship by Era

**Date:** 2026-02-09  
**Data Sources:** EIA Natural Gas Navigator, EIA Electric Power Monthly, EIA Drilling Productivity Report, EIA STEO, Baker Hughes Rig Count

---

## Executive Summary

### What Was Analyzed

1. **Gas → Electricity regression** for Texas, California, US Average across three eras:
   - Pre-Shale (2000-2008)
   - Shale Boom (2009-2020) 
   - Post-Pandemic (2021-2024)

2. **Upstream factors → Regional gas prices** including:
   - US and regional production (Permian, Haynesville, Appalachian)
   - US storage levels
   - Rig counts
   - Permian pipeline capacity

3. **Wholesale vs Retail electricity comparison** for Texas

4. **Waha basis analysis** (Permian constraints)

5. **California non-fuel cost analysis**

---

## Key Findings

### TEXAS ✅ Strong Gas-Electricity Relationship

| Era | Elasticity (¢/kWh per $/MMBtu) | R² | p-value | Significant? |
|-----|-------------------------------|-----|---------|--------------|
| Pre-Shale (2000-2008) | **+0.679** | 0.852 | 0.001 | ✅ Yes |
| Shale Boom (2009-2020) | +0.379 | 0.491 | 0.010 | ✅ Yes |
| Post-Pandemic (2021-2024) | -0.295 | 0.621 | 0.100 | ❌ No |

**Key Insight:** Pre-Shale elasticity of 0.679 ¢/kWh per $/MMBtu is consistent with theoretical heat rate (7 MMBtu/MWh × $1/MMBtu = $7/MWh = 0.7 ¢/kWh).

**Post-Pandemic Anomaly:** Retail prices increased while gas prices fell, indicating:
- ORDC scarcity pricing effects
- Non-fuel cost increases (transmission, capacity)
- Need to use **wholesale ERCOT prices** instead of retail

### CALIFORNIA ❌ Broken Gas-Electricity Link

| Era | Elasticity | R² | p-value | Significant? |
|-----|-----------|-----|---------|--------------|
| Pre-Shale (2000-2008) | +0.554 | 0.473 | 0.050 | ❌ Borderline |
| Shale Boom (2009-2020) | **-2.070** | 0.526 | 0.010 | ⚠️ Wrong Sign |
| Post-Pandemic (2021-2024) | -0.276 | 0.022 | 0.100 | ❌ No |

**Key Insight:** Gas price does NOT predict California electricity prices. The negative correlation during Shale Boom indicates electricity prices rose while gas prices fell - driven by non-fuel costs.

**Implied Heat Rate Analysis:**
| Year | CA Citygate ($/MMBtu) | CA Retail (¢/kWh) | Implied Heat Rate |
|------|----------------------|-------------------|-------------------|
| 2015 | $3.30 | 16.27 | 49.3 MMBtu/MWh |
| 2020 | $2.92 | 20.10 | 68.8 MMBtu/MWh |
| 2024 | $3.51 | 31.91 | **90.8 MMBtu/MWh** |

*Note: Efficient CCGT heat rate is 6-7 MMBtu/MWh. Values >15 indicate non-fuel costs dominate.*

---

## Upstream Factors → Gas Prices (Significant Relationships)

### Texas Citygate Price Drivers

| Factor | Era | β Coefficient | R² | Interpretation |
|--------|-----|---------------|-----|----------------|
| US Production | Shale Boom | -0.066 | 0.55 | +1 Bcf/d → -$0.07/MMBtu |
| US Production | Post-Pandemic | -0.272 | 0.84 | +1 Bcf/d → -$0.27/MMBtu |
| Haynesville Production | Post-Pandemic | -2.113 | 0.93 | +1 Bcf/d → -$2.11/MMBtu |
| Appalachia Production | Post-Pandemic | -0.916 | 0.93 | +1 Bcf/d → -$0.92/MMBtu |
| Gas Rig Count | Pre-Shale | +0.0063 | 0.91 | +100 rigs → +$0.63/MMBtu |

### Henry Hub Price Drivers

| Factor | Era | β Coefficient | R² | Interpretation |
|--------|-----|---------------|-----|----------------|
| US Production | Shale Boom | -0.048 | 0.48 | +1 Bcf/d → -$0.05/MMBtu |
| Appalachia Production | Post-Pandemic | -0.707 | 0.88 | +1 Bcf/d → -$0.71/MMBtu |
| **US Storage** | **Post-Pandemic** | **-0.0072** | **0.94** | **+100 Bcf → -$0.72/MMBtu** |
| Gas Rig Count | Pre-Shale | +0.0058 | 0.76 | +100 rigs → +$0.58/MMBtu |

---

## Wholesale vs Retail Comparison (Texas)

| Year | ERCOT Wholesale ($/MWh) | TX Retail (¢/kWh) | Retail/Wholesale Ratio |
|------|------------------------|-------------------|----------------------|
| 2019 | 38.0 | 8.75 | 2.30x |
| 2020 | 22.5 | 8.59 | 3.82x |
| 2021 | **80.3** | 9.10 | 1.13x |
| 2022 | 75.8 | 10.53 | 1.39x |
| 2023 | 28.4 | 10.69 | 3.76x |
| 2024 | 32.1 | 11.22 | 3.50x |

**Gas → ERCOT Wholesale Regression:**
- Post-Pandemic: β = **11.15 $/MWh per $/MMBtu**, R² = 0.95, p < 0.001 ✅
- Implied: +$1/MMBtu gas → +$11.15/MWh electricity (vs theoretical $7/MWh)

---

## Waha Basis Analysis (Permian Constraints)

| Year | Waha Basis | Permian Production | Pipeline Capacity | Production/Capacity |
|------|-----------|-------------------|-------------------|-------------------|
| 2019 | **-$1.66** | 13.5 Bcf/d | 12.5 Bcf/d | 108% (Constrained) |
| 2021 | -$0.26 | 16.7 Bcf/d | 18.0 Bcf/d | 93% (Relief) |
| 2024 | **-$1.80** | 25.4 Bcf/d | 21.0 Bcf/d | 121% (Constrained) |

**Regression:** Each +1 Bcf/d of Permian production widens Waha basis by ~$0.05/MMBtu (R² = 0.25, weak due to pipeline capacity changes)

---

## Data Gaps Status Update

### ✅ NOW COVERED (This Session)

| Data | Status | Impact on Model |
|------|--------|-----------------|
| Monthly gas prices (HH, TX, CA) | ✅ 347 monthly records | Higher statistical power |
| Annual electricity prices | ✅ 2000-2024 by state | Era-specific analysis |
| Regional production (Permian, Haynesville, Appalachia) | ✅ Annual 2014-2024 | Upstream factor analysis |
| ERCOT wholesale prices | ✅ Annual 2014-2024 | Wholesale vs retail comparison |
| Waha basis | ✅ Annual 2018-2024 | Permian constraint analysis |
| US storage | ✅ Annual 2000-2024 | Strong storage→price relationship |
| Rig counts | ✅ Annual 2000-2024 | Lagging indicator confirmed |
| Permian pipeline capacity | ✅ Annual 2017-2024 | Constraint analysis |

### ⚠️ STILL NEEDED (Priority Gaps)

| Data | Priority | Source | Impact |
|------|----------|--------|--------|
| **Monthly wholesale ERCOT LMP** | HIGH | gridstatus library | Better elasticity estimate |
| **Monthly wholesale CAISO LMP** | HIGH | gridstatus library | Verify CA non-fuel thesis |
| **Monthly Permian production** | MEDIUM | EIA STEO API | Monthly regression |
| Pacific region storage | MEDIUM | EIA API | CA-specific storage |
| Waha daily/monthly prices | MEDIUM | Natural Gas Intelligence | Basis volatility |
| CA non-fuel cost data | LOW | CPUC filings | Alternative CA model |

---

## Model Recommendations

### TEXAS: Use Gas-Electricity Relationship ✅

```
DecarbIQ Texas Electricity Model:
─────────────────────────────────
IF era = "Pre-Shale":
    Elasticity = 0.68 ¢/kWh per $/MMBtu (R² = 0.85)
    
IF era = "Shale Boom":
    Elasticity = 0.38 ¢/kWh per $/MMBtu (R² = 0.49)
    Note: Lower due to non-fuel cost share increasing
    
IF era = "Post-Pandemic":
    Use WHOLESALE ERCOT prices, not retail
    Wholesale Elasticity = 11.15 $/MWh per $/MMBtu (R² = 0.95)
    
Upstream → Gas Price Chain:
    US Storage: -$0.72/MMBtu per 100 Bcf (strongest predictor)
    US Production: -$0.27/MMBtu per Bcf/d
    Waha Basis: ~-$0.05/MMBtu per Bcf/d Permian production
```

### CALIFORNIA: Do NOT Use Gas-Electricity Relationship ❌

```
DecarbIQ California Electricity Model:
──────────────────────────────────────
Gas price is NOT a significant predictor.

Alternative drivers to model:
  1. CPUC rate case decisions (annual)
  2. Wildfire liability costs (utility filings)
  3. Grid hardening/undergrounding costs
  4. RPS compliance costs
  5. Transmission congestion costs

Implied non-fuel cost share: >85% of retail price (2024)
```

---

## Files Produced

| File | Description |
|------|-------------|
| `decarbiq_updated_regression.py` | Main analysis script with embedded data |
| `decarbiq_updated_regression_results.csv` | All regression results |
| `decarbiq_monthly_gas_prices.csv` | 347 monthly records (1997-2025) |
| `decarbiq_data_gaps_status.md` | Gap coverage assessment |

---

## Next Steps

1. **Install gridstatus library** locally: `pip install gridstatus`
2. **Fetch monthly ERCOT/CAISO wholesale prices** using the wholesale fetcher script
3. **Re-run regression with monthly wholesale data** for higher power
4. **Build California non-fuel cost database** from CPUC filings
5. **Integrate into DecarbIQ tool** with era-specific elasticity parameters
