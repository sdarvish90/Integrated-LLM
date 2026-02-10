# DecarbIQ Regression Confidence Assessment

## Summary: What Can We Actually Use?

Based on rigorous OLS regression with p-values, R², and comparison to industry benchmarks.

---

## TEXAS

### Gas → Electricity Relationship

| Era | Elasticity | R² | p-value | n | Confidence | Notes |
|-----|------------|-----|---------|---|------------|-------|
| Pre-Shale (2000-08) | **+0.72** ¢/kWh per $/MMBtu | 0.76 | <0.01 | 9 | **HIGH** | Matches heat rate theory (0.70) |
| Shale Boom (2009-20) | +0.20 ¢/kWh per $/MMBtu | 0.31 | 0.05 | 12 | **NOT USABLE** | Below theory, borderline significance |
| Post-Pandemic (2021-25) | **+1.02** ¢/kWh per $/MMBtu | 0.95 | <0.01 | 5 | **MEDIUM-HIGH** | Exceeds theory (ORDC premium?), small n |

### Upstream Factors → Waha Gas Price

| Factor | Pre-Shale | Shale Boom | Post-Pandemic |
|--------|-----------|------------|---------------|
| **Production** | NS | β=-0.06** (R²=0.65) | NS |
| **Rig Count** | β=+0.005** (R²=0.65) | β=+0.002** (R²=0.63) | NS (n=5) |
| **Storage** | β=+0.006* (counterintuitive) | NS | β=-0.010** (R²=0.81) |
| **LNG Exports** | N/A | β=-0.28* (counterintuitive) | NS |
| **Mexico Exports** | β=+5.2* | β=-0.44** (counterintuitive) | NS |

### Texas Data Gaps Identified

- ❌ **Permian production** separate from national (needed - associated gas from oil wells is key driver)
- ❌ **Permian pipeline takeaway capacity** (Waha constraints caused negative prices in 2024)
- ❌ **Gulf Coast LNG terminal utilization** (affects Waha-Henry Hub spread)
- ❌ **Texas rig count** separate from national

### Texas Confidence Summary

| What | Confidence | Can Use? |
|------|------------|----------|
| Gas→Elec Pre-Shale | HIGH | ✅ Yes |
| Gas→Elec Post-Pandemic | MEDIUM-HIGH | ⚠️ With caution (small n) |
| Storage→Gas (Post-Pandemic) | MEDIUM-HIGH | ✅ Yes |
| Rig Count→Gas | MEDIUM | ⚠️ Correlation, not causation |
| Production→Gas (Shale Boom) | MEDIUM | ⚠️ Era-specific only |
| LNG/Mexico→Gas | LOW | ❌ Counterintuitive signs |

---

## CALIFORNIA

### Gas → Electricity Relationship

| Era | Elasticity | R² | p-value | n | Confidence | Notes |
|-----|------------|-----|---------|---|------------|-------|
| Pre-Shale (2000-08) | +0.50 ¢/kWh per $/MMBtu | 0.58 | 0.01 | 9 | **MEDIUM** | Below heat rate theory |
| Shale Boom (2009-20) | -1.02 ¢/kWh per $/MMBtu | 0.32 | 0.05 | 12 | **NOT USABLE** | Negative coefficient is wrong |
| Post-Pandemic (2021-25) | +0.07 ¢/kWh per $/MMBtu | 0.007 | >0.5 | 5 | **NOT USABLE** | No relationship |

### Upstream Factors → SoCal Citygate Gas Price

| Factor | Pre-Shale | Shale Boom | Post-Pandemic |
|--------|-----------|------------|---------------|
| **Production** | NS | β=-0.05* | NS |
| **Rig Count** | β=+0.006** | β=+0.002** | β=+0.12** |
| **Storage** | β=+0.007* (counterintuitive) | NS | NS |
| **LNG Exports** | N/A | NS | NS |
| **Mexico Exports** | β=+6.2* | β=-0.40* | NS |

### California Data Gaps Identified

- ❌ **SoCal Gas storage levels** (Aliso Canyon constraints critical - reduced from 86 Bcf to 34-68 Bcf)
- ❌ **Southwest pipeline flows** (El Paso, Kern River, Transwestern)
- ❌ **California in-state production** (declining, but still exists)
- ❌ **Pacific NW hydro conditions** (affects gas demand for peakers)
- ❌ **Wildfire-related grid costs** (MAJOR driver of CA electricity prices)
- ❌ **RPS compliance costs** (renewables mandates)
- ❌ **Transmission/distribution investment** (PG&E undergrounding, etc.)

### California Confidence Summary

| What | Confidence | Can Use? |
|------|------------|----------|
| Gas→Elec (any era) | LOW to NONE | ❌ Gas is NOT a predictor for CA |
| Rig Count→SoCal Gas | MEDIUM | ⚠️ National proxy only |
| All other factors | LOW | ❌ Need CA-specific data |

### California Key Finding

**Gas price does NOT drive California electricity prices post-2008.** This is validated by:
1. Our regressions (not statistically significant, or negative coefficient)
2. Industry observation (FRED: "structural pressures beyond fuel costs")
3. Known factors: Aliso Canyon restrictions, wildfires, RPS, grid investments

**California needs a separate model** with non-fuel cost drivers.

---

## US AVERAGE

### Gas → Electricity Relationship

| Era | Elasticity | R² | p-value | n | Confidence | Notes |
|-----|------------|-----|---------|---|------------|-------|
| Pre-Shale (2000-08) | **+0.41** ¢/kWh per $/MMBtu | 0.65 | <0.01 | 9 | **HIGH** | Lower than TX (blended regions) |
| Shale Boom (2009-20) | -0.21 ¢/kWh per $/MMBtu | 0.30 | 0.05 | 12 | **NOT USABLE** | Negative is counterintuitive |
| Post-Pandemic (2021-25) | -0.11 ¢/kWh per $/MMBtu | 0.04 | >0.5 | 5 | **NOT USABLE** | No relationship |

### Upstream Factors → Henry Hub Gas Price

| Factor | Pre-Shale | Shale Boom | Post-Pandemic |
|--------|-----------|------------|---------------|
| **Production** | NS | β=-0.05* (R²=0.53) | NS |
| **Rig Count** | β=+0.005** (R²=0.65) | β=+0.002** (R²=0.61) | NS |
| **Storage** | β=+0.007* (counterintuitive) | NS | β=-0.009* (R²=0.77) |
| **LNG Exports** | N/A | β=-0.22* (counterintuitive) | NS |
| **Mexico Exports** | β=+5.4* | β=-0.37** (counterintuitive) | NS |

### US Average Data Gaps

- ❌ **Regional production breakdown** (Appalachia, Haynesville, Permian behave differently)
- ❌ **Regional storage** (Gulf Coast, Midwest, East, Pacific have different dynamics)
- ❌ **Inter-regional pipeline flows**

### US Average Confidence Summary

| What | Confidence | Can Use? |
|------|------------|----------|
| Gas→Elec Pre-Shale | HIGH | ✅ Yes (historical reference) |
| Gas→Elec Post-2008 | NONE | ❌ National average masks regional divergence |
| Storage→Gas (Post-Pandemic) | MEDIUM-HIGH | ✅ Yes |
| Production→Gas (Shale Boom) | MEDIUM | ⚠️ Era-specific |
| Rig Count→Gas | MEDIUM | ⚠️ Correlation not causation |

---

## CROSS-CUTTING ISSUES

### Multicollinearity (All Regions)

These factors are too correlated with each other to isolate effects:

| Variable | VIF | Issue |
|----------|-----|-------|
| Production | 51.6 | All trending up together |
| Mexico Exports | 27.1 | All trending up together |
| LNG Exports | 12.4 | All trending up together |

**Recommendation**: Use first-differences (YoY changes) or derived variables (export share, net supply)

### Small Sample Size (Post-Pandemic)

- n = 5 for all Post-Pandemic regressions
- Most factors will NOT reach statistical significance
- Need to wait for more years OR use monthly data

### Structural Breaks

- Correlations flip sign between eras (Production, Mexico Exports)
- Cannot use single regression across full time period
- Must use era-specific models

### Counterintuitive Results Flagged

| Finding | Expected | Got | Likely Cause |
|---------|----------|-----|--------------|
| Storage + price (Pre-Shale) | Negative | Positive | Reverse causality (high prices → storage builds) |
| LNG exports - price (Shale Boom) | Positive | Negative | Spurious (production grew faster than exports) |
| Mexico exports sign flip | Consistent | Flipped | Time trend confound |

---

## RECOMMENDED ACTIONS

### For DecarbIQ Model

1. **Texas**: Use Gas→Elec relationship (0.72-1.02 ¢/kWh per $/MMBtu) with appropriate era
2. **Texas**: Use Storage→Gas relationship (Post-Pandemic) for price forecasting
3. **California**: Build separate non-fuel cost model (do NOT use gas price as predictor)
4. **US Average**: Do not use post-2008; use regional models instead

### Data Acquisition Priorities

1. **High Priority**: Permian production, Permian pipeline capacity, Waha price history
2. **High Priority**: SoCal Gas storage levels, Aliso Canyon capacity limits
3. **Medium Priority**: Monthly data for all variables (increase n)
4. **Medium Priority**: ERCOT/CAISO wholesale LMP data (not retail)

### Analysis Improvements

1. Run first-difference regressions to address multicollinearity
2. Test lagged relationships (rigs → production with 6-12 month lag)
3. Add instrumental variables for endogenous factors (storage, production)
4. Validate with out-of-sample testing
