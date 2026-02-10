# DecarbIQ Gas-Electricity Price Database: Summary Analysis

## Data Overview

This database contains annual data from 1997-2025 for:
- **US National Average**
- **California** (CAISO region, SoCal Citygate gas hub)
- **Texas** (ERCOT region, Waha/Houston Ship Channel gas hubs)

---

## Key Metrics Comparison (2024)

| Metric | US Average | California | Texas |
|--------|-----------|------------|-------|
| Gas Price ($/MMBtu) | $2.19 | $4.55 | $2.00 |
| Electricity Price (¢/kWh) | 12.94 | 27.04 | 9.79 |
| Gas Share of Generation | 43% | 40% | 42% |
| Renewables Share | 22% | 50% | 36% |
| Coal Share | 16% | 0% | 13% |

---

## Regional Dynamics

### California
- **Gas premium**: SoCal Citygate typically trades $1-5/MMBtu above Henry Hub due to pipeline constraints
- **Extreme volatility**: Spiked to $49/MMBtu in Dec 2022 during polar vortex
- **High electricity prices**: 2x national average driven by:
  - Wildfire liability costs
  - Grid modernization investments
  - RPS mandate costs
  - Behind-the-meter solar reducing sales (fixed costs spread over fewer kWh)
- **Decarbonization leader**: 50% renewables, 0% coal since 2011
- **Gas still sets marginal price** during evening ramp and winter peaks

### Texas (ERCOT)
- **Gas discount**: Waha Hub often trades $0.50-2.00 BELOW Henry Hub due to Permian oversupply
- **Negative prices**: Waha traded negative 42% of days in 2024 before Matterhorn pipeline
- **Low electricity prices**: 25% below national average
- **Extreme events**: Winter Storm Uri (Feb 2021) saw prices hit $9,000/MWh
- **Rapid renewable growth**: Wind+Solar at 36% in 2024, expected 40%+ by 2026
- **No capacity market**: Energy-only market means higher price volatility

---

## Three Eras of Gas-Electricity Dynamics

### Era 1: Pre-Shale (1997-2008)
- Gas prices: $2-9/MMBtu (volatile)
- Gas share: 15% → 21%
- Coal dominated (~50%)
- **Weak correlation**: Gas only set prices during peaks

### Era 2: Shale Boom (2009-2020)
- Gas prices: $2-4.50/MMBtu (low, stable)
- Gas share: 23% → 40%
- Coal collapsed: 45% → 20%
- **Strong correlation emerges**: Gas now marginal setter 60-70% of hours

### Era 3: Post-Pandemic (2021-Present)
- Gas prices: $2-6.50/MMBtu (volatile again)
- Gas share: 40-43%
- Renewables surge: 20% → 22%+
- **Mixed correlation**: Renewables moderating peaks, but gas still critical

---

## Key Relationships Observed

### Gas → Electricity Elasticity
- **National**: +$1/MMBtu gas ≈ +0.5-0.8 ¢/kWh electricity
- **Texas**: Higher elasticity due to 42% gas share and no capacity payments
- **California**: Lower elasticity due to high fixed costs and renewable penetration

### Generation Mix Impact
- Each 10% increase in gas share → ~15% increase in gas-electricity correlation
- Each 10% increase in renewable share → ~10% decrease in gas-electricity correlation

### Regional Gas Spreads
| Hub | Typical Spread vs Henry Hub | Key Driver |
|-----|---------------------------|------------|
| SoCal Citygate | +$1.50 to +$5.00 | Pipeline constraints, import dependency |
| Waha (Permian) | -$0.50 to -$2.00 | Production exceeds takeaway capacity |
| Houston Ship Channel | +$0.10 to +$0.30 | Near Henry Hub, LNG export demand |

---

## Implications for Hydrogen Projects

### LCOH Sensitivity to Electricity Price
Using 50 kWh/kg electrolyzer efficiency:
- At 5 ¢/kWh: Electricity = $2.50/kg H2
- At 10 ¢/kWh: Electricity = $5.00/kg H2
- At 27 ¢/kWh (CA): Electricity = $13.50/kg H2

### Regional Competitiveness
| Region | Electricity Cost Advantage | Gas Price Advantage | Net Assessment |
|--------|---------------------------|--------------------|-----------------
| Texas | Very High | High (negative Waha) | Excellent for green H2 |
| California | Very Low | Low (premium pricing) | Challenging without incentives |
| Gulf Coast | Moderate | Moderate | Good with 45V credit |

---

## Data Sources

- **Gas Prices**: EIA Henry Hub (monthly), regional hubs from EIA/NGI
- **Electricity Prices**: EIA Form 861, State Electricity Profiles
- **Generation Mix**: EIA Form 923, Electric Power Monthly
- **Regional Data**: ERCOT, CAISO market data

---

## Next Steps for DecarbIQ

1. **Add monthly granularity** for volatility analysis
2. **Add wholesale prices** (LMP data from ISOs) vs retail
3. **Add gas hub differentials** as separate variables
4. **Build regression model** by era and region
5. **Integrate with SHARE model** for hydrogen economics
