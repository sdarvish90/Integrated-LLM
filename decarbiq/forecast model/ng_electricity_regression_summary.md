# DECARBIQ Regression Analysis: Policy Impact on NG-Electricity Nexus

## Executive Summary

This analysis examines how major energy policies have affected natural gas prices and the gas-electricity relationship from 1997-2024.

## Model Performance

| Model | R² | MAE ($/MMBtu) | Features |
|-------|-----|---------------|----------|
| Demand Only | 0.32 | $1.28 | 4 demand variables |
| Demand + Supply | 0.51 | $1.11 | 7 fundamental variables |
| Full with Policy | 0.67 | $0.87 | 14 variables incl. policy regimes |

**Adding policy regime variables improved R² by 31%** (0.51 → 0.67)

## Policy Regime Impacts on Gas Prices

### Price-Decreasing Policies:
- **Inflation Reduction Act (2022)**: -$2.40/MMBtu
- **Shale Production Boom (2009)**: -$1.37/MMBtu  
- **RGGI Carbon Markets (2009)**: -$1.37/MMBtu

### Price-Increasing Policies:
- **MATS Mercury Rule (2015)**: +$0.86/MMBtu
- **EPAct 2005 Fracking Exemption**: +$0.66/MMBtu
- **LNG Exports (2016)**: +$0.12/MMBtu

## Structural Break Analysis

| Event | Date | Price Change | Significance |
|-------|------|--------------|--------------|
| Shale Boom | 2009-01 | -33.7% | *** (p<0.001) |
| LNG Exports | 2016-02 | -31.0% | *** (p<0.001) |
| IRA | 2022-08 | -31.4% | ** (p<0.01) |

## Gas-Electricity Nexus Evolution

| Era | Avg Gas Price | Power Demand | Correlation |
|-----|---------------|--------------|-------------|
| Early Deregulation (1997-99) | $2.29 | 11.5 Bcf/d | -0.20 |
| Mature Market (2000-05) | $4.51 | 14.2 Bcf/d | +0.09 |
| Early Shale (2005-09) | $7.99 | 17.2 Bcf/d | -0.17 |
| Shale Dominance (2009-16) | $3.67 | 21.7 Bcf/d | -0.42 |
| LNG Era (2016-20) | $2.78 | 27.5 Bcf/d | -0.20 |
| COVID Era (2020-22) | $4.00 | 31.0 Bcf/d | +0.27 |
| IRA Era (2022-24) | $2.90 | 34.6 Bcf/d | -0.12 |

## Key Insights

1. **Shale Revolution was the dominant price driver** - Reduced gas prices by $1.37/MMBtu controlling for other factors

2. **Power sector demand tripled** - From 11.5 to 34.6 Bcf/d (1997-2024), now 40% of total consumption

3. **Correlation structure changed** - Negative in most eras (more demand → lower prices due to scale), positive only during supply shocks (COVID)

4. **IRA shows largest policy coefficient** - Though short sample period, associated with $2.40/MMBtu price reduction

5. **Environmental rules had mixed effects** - MATS increased prices (coal retirement → gas demand), RGGI decreased prices (efficiency, renewables)
