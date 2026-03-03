# DecarbIQ Regional Regression: Confidence Assessment & Data Gaps

## Results Summary by Region

---

## TEXAS (Waha → ERCOT)

### Gas → Electricity Relationship

| Era | Elasticity | R² | p-value | Confidence | Notes |
|-----|------------|-----|---------|------------|-------|
| Pre-Shale (2000-08) | +0.72 ¢/kWh per $/MMBtu | 0.76 | <0.01 | **HIGH** | Matches heat rate theory (0.70) |
| Shale Boom (2009-20) | +0.26 ¢/kWh per $/MMBtu | 0.40 | <0.05 | **MEDIUM** | Lower than expected - why? |
| Post-Pandemic (2021-25) | +1.14 ¢/kWh per $/MMBtu | 0.98 | <0.01 | **MEDIUM-LOW** | n=5, exceeds heat rate theory |

**Key Question**: Why did elasticity drop during Shale Boom then spike Post-Pandemic? Possible explanations:
- Shale Boom: Gas prices so low that other costs (transmission, capacity) became larger share of bill
- Post-Pandemic: ORDC scarcity pricing added premium beyond fuel cost; possible Winter Storm Uri outlier effect

### Upstream Factors → Waha Gas Price

**Statistically Significant (p < 0.05):**

| Factor | Era | β | R² | Interpretation |
|--------|-----|---|-----|---------------|
| Rig Count | Pre-Shale | +0.0049 | 0.65 | +100 rigs → +$0.49/MMBtu |
| Rig Count | Shale Boom | +0.0021 | 0.62 | +100 rigs → +$0.21/MMBtu |
| Storage | Pre-Shale | +0.0064 | 0.59 | ⚠️ Counterintuitive positive |
| Storage | Post-Pandemic | **-0.0089** | 0.80 | +100 Bcf → -$0.89/MMBtu ✓ Expected |
| Production | Shale Boom | -0.049 | 0.54 | +1 Bcf/d → -$0.049/MMBtu ✓ |
| LNG Exports | Shale Boom | -0.22 | 0.41 | ⚠️ Counterintuitive negative |
| Mexico Exports | Shale Boom | -0.36 | 0.62 | ⚠️ Counterintuitive negative |
| Pipeline Cap | Pre-Shale | +1.03 | 0.66 | ⚠️ Positive unexpected |

### Waha Basis (Waha - Henry Hub) Drivers

**Pre-Shale (significant):**
- Mexico Exports: β = -0.22 per Bcf/d (R² = 0.67, p < 0.01)
- Pipeline Cap Added: β = -0.042 per Bcf/d (R² = 0.67, p < 0.01)
- Rig Count: β = -0.00019 per rig (R² = 0.56, p < 0.05)

**Shale Boom & Post-Pandemic:** No factors significantly explain Waha basis with current data.

### Data Gaps - Texas

| Missing Data | Why It Matters | Source |
|--------------|----------------|--------|
| **Permian-specific production** | Using national total, but Permian is 22% of US and drives Waha | EIA Drilling Productivity Report |
| **Permian pipeline capacity** | Matterhorn, Whistler, Gulf Coast Express timing critical | EIA, Pipeline operator announcements |
| **Waha basis historical data** | Only have estimates, need actual daily/monthly prices | EIA Natural Gas Weekly Update |
| **ERCOT wholesale LMP** | Using retail prices which include T&D, taxes | ERCOT historical data |
| **Gulf Coast LNG feed gas** | Competes for Permian supply | EIA LNG Monthly |

**Regional Production Data Found (annual Bcf/d):**
- Permian 2021: 16.7 → 2022: 21.0 → 2023: 22.7 → 2024: 25.4
- Haynesville 2021: 12.4 → 2022: 13.1 → 2023: 14.6 → 2024: 14.6 (decline due to low prices)
- Appalachian 2021: 31.7 → 2022: 30.4 → 2023: 35.1 → 2024: 35.6

---

## CALIFORNIA (SoCal Citygate → CAISO)

### Gas → Electricity Relationship

| Era | Elasticity | R² | p-value | Confidence | Notes |
|-----|------------|-----|---------|------------|-------|
| Pre-Shale (2000-08) | +0.44 ¢/kWh per $/MMBtu | 0.32 | 0.10 | **LOW** | Not significant |
| Shale Boom (2009-20) | -1.02 ¢/kWh per $/MMBtu | 0.32 | 0.05 | **LOW** | Negative = counterintuitive |
| Post-Pandemic (2021-25) | +0.03 ¢/kWh per $/MMBtu | 0.001 | 0.50 | **VERY LOW** | Essentially zero |

**Conclusion**: Gas price is NOT a statistically significant predictor of California electricity prices in any era except Pre-Shale (and even that is borderline at p=0.10).

**Implications for DecarbIQ**:
- Cannot use upstream gas factors to predict CA electricity
- Need to model non-fuel cost drivers separately
- CA electricity model must be fundamentally different from Texas

### Upstream Factors → SoCal Citygate Gas Price

**Statistically Significant (p < 0.05):**

| Factor | Era | β | R² | Notes |
|--------|-----|---|-----|-------|
| Rig Count | Pre-Shale | +0.0043 | 0.52 | Similar to national |
| Rig Count | Shale Boom | +0.0024 | 0.58 | |
| **Rig Count** | **Post-Pandemic** | **+0.118** | **0.85** | Unusually strong |
| Pipeline Cap | Pre-Shale | +1.00 | 0.64 | |
| Production | Shale Boom | -0.052 | 0.42 | |
| Mexico Exports | Shale Boom | -0.40 | 0.51 | |

### SoCal Basis (SoCal - Henry Hub) Drivers

**Significant relationships found:**
- Pre-Shale: Mexico Exports (β = -2.56, R² = 0.49, p < 0.05)
- Shale Boom: Storage (β = -0.00062, R² = 0.52, p < 0.01)
- Post-Pandemic: Rig Count (β = +0.067, R² = 0.83, p < 0.01) ⚠️ Strange

**Key insight**: SoCal Citygate premium to Henry Hub is driven by:
- Storage levels (negative correlation during Shale Boom)
- But not by the factors we'd expect (pipeline capacity to CA)

### Data Gaps - California

| Missing Data | Why It Matters | Source |
|--------------|----------------|--------|
| **Southwest pipeline deliveries** | El Paso, Transwestern, Kern River capacity and flows | EIA, FERC Form 549 |
| **California storage levels** | Aliso Canyon restrictions post-2015 | California Energy Commission |
| **Aliso Canyon status** | Major CA storage field limited since 2015 leak | SoCalGas filings |
| **CAISO renewable penetration** | Affects when gas sets marginal price | CAISO OASIS |
| **CAISO wholesale LMP** | Retail prices include massive non-fuel costs | CAISO historical data |
| **Wildfire/grid hardening costs** | May be dominant driver of CA electricity prices | CPUC rate cases |

**California Non-Fuel Cost Drivers to Investigate:**
1. Wildfire liability and prevention investments
2. Renewable Portfolio Standard compliance costs
3. Grid hardening and undergrounding
4. Transmission congestion and upgrades
5. CPUC rate case decisions

---

## US NATIONAL (Henry Hub → US Average)

### Gas → Electricity Relationship

| Era | Elasticity | R² | p-value | Confidence | Notes |
|-----|------------|-----|---------|------------|-------|
| Pre-Shale (2000-08) | +0.41 ¢/kWh per $/MMBtu | 0.65 | <0.01 | **MEDIUM-HIGH** | Significant |
| Shale Boom (2009-20) | -0.21 ¢/kWh per $/MMBtu | 0.30 | 0.05 | **LOW** | Borderline, negative |
| Post-Pandemic (2021-25) | -0.11 ¢/kWh per $/MMBtu | 0.04 | 0.50 | **VERY LOW** | Not significant |

**Conclusion**: US Average is a blend of Texas (positive relationship) and California (no relationship) and other regions. The national average obscures regional dynamics.

### Upstream Factors → Henry Hub Gas Price

*Results identical to Waha analysis above since Henry Hub and Waha are highly correlated.*

### Full Chain Analysis (Both Links Significant)

For Texas Pre-Shale, these factor → electricity chains have BOTH links statistically significant:

| Factor | Factor→Gas β | Gas→Elec β | Implied Elec Effect |
|--------|--------------|------------|---------------------|
| Rig Count | +0.0049 | +0.72 | +100 rigs → +0.35 ¢/kWh |
| Storage | +0.0064 | +0.72 | +100 Bcf → +0.46 ¢/kWh |
| Mexico Exports | +5.22 | +0.72 | +1 Bcf/d → +3.74 ¢/kWh |
| Pipeline Cap | +1.03 | +0.72 | +1 Bcf/d → +0.74 ¢/kWh |

**For California:** No full chain is significant because Gas→Electricity link is never significant.

---

## Overall Confidence Assessment

### HIGH CONFIDENCE (Use in Model)

| Relationship | Region | Era | Evidence |
|--------------|--------|-----|----------|
| Gas → Electricity | Texas | Pre-Shale | R²=0.76, p<0.01, matches theory |
| Storage → Gas Price | All | Post-Pandemic | R²=0.77-0.80, p<0.05, makes sense |

### MEDIUM CONFIDENCE (Use with Caution)

| Relationship | Region | Era | Issue |
|--------------|--------|-----|-------|
| Gas → Electricity | Texas | Shale Boom | Lower than expected (0.26 vs 0.70) |
| Gas → Electricity | Texas | Post-Pandemic | n=5 only, higher than expected (1.14) |
| Rig Count → Gas Price | All | All | Consistent but may be reverse causality |
| Production → Gas Price | All | Shale Boom | Sign makes sense, but multicollinearity |

### LOW/NO CONFIDENCE (Do Not Use)

| Relationship | Region | Era | Reason |
|--------------|--------|-----|--------|
| Gas → Electricity | California | All eras | Not statistically significant |
| Gas → Electricity | US Average | Post-2008 | Negative/insignificant |
| LNG Exports → Gas Price | All | Shale Boom | Counterintuitive negative, VIF > 10 |
| Mexico Exports → Gas Price | All | Shale Boom | Sign instability, VIF > 27 |
| Any factor → CA Electricity | California | All | Gas link is broken |

---

## Recommendations

### For DecarbIQ Tool:

1. **Texas Model**: Use gas-electricity elasticity of +0.70 ¢/kWh per $/MMBtu (average of Pre-Shale and theory). Flag Post-Pandemic elasticity of 1.14 as needing validation with more data.

2. **California Model**: Do NOT use gas price as predictor. Build separate model based on:
   - CPUC rate case decisions
   - Wildfire costs
   - RPS compliance costs
   - Grid hardening investments

3. **Upstream → Gas**: Use only:
   - Storage levels (negative, Post-Pandemic)
   - Production (negative, Shale Boom)
   - Rig count (positive, but lagging indicator)

4. **Acquire Regional Data**:
   - Permian production (not national total)
   - Waha and SoCal Citygate actual prices
   - ERCOT and CAISO wholesale LMP

5. **Monthly Data**: Annual data limits statistical power (n=5 for recent era). Monthly data would provide 60 observations for Post-Pandemic era.
