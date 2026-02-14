# DecarbIQ Regression Analysis: Our Findings vs Industry Benchmarks

## Executive Summary

This document compares our statistically-derived regression results against academic studies and industry sources. We flag areas of agreement, disagreement, and gaps requiring attention.

---

## Part 1: Gas Price → Electricity Price Relationship

### Our Findings (Statistically Significant)

| Region | Era | Elasticity (¢/kWh per $/MMBtu) | R² | p-value | Significant? |
|--------|-----|-------------------------------|-----|---------|--------------|
| **Texas** | Pre-Shale (2000-08) | +0.72 | 0.76 | <0.01 | **Yes** |
| **Texas** | Shale Boom (2009-20) | +0.26 | 0.40 | <0.05 | **Yes** |
| **Texas** | Post-Pandemic (2021-25) | +1.14 | 0.98 | <0.01 | **Yes** |
| US Avg | Pre-Shale | +0.41 | 0.65 | <0.01 | **Yes** |
| US Avg | Shale Boom | -0.21 | 0.30 | 0.05 | Borderline |
| US Avg | Post-Pandemic | -0.11 | 0.04 | >0.05 | No |
| California | Pre-Shale | +0.44 | 0.32 | 0.10 | No |
| California | Shale Boom | -1.02 | 0.32 | 0.05 | Borderline |
| California | Post-Pandemic | +0.03 | 0.001 | >0.05 | No |

### Industry Benchmark: Theoretical Heat Rate Calculation

From EIA and industry sources:
- Combined cycle gas plant heat rate: ~7.0 MMBtu/MWh
- This implies: **$1/MMBtu gas → $7/MWh = 0.70 ¢/kWh electricity**

**If gas sets marginal price 100% of the time, elasticity should be ~0.70 ¢/kWh per $/MMBtu**

### Comparison

| Region | Our Finding | Theoretical | Match? | Explanation |
|--------|-------------|-------------|--------|-------------|
| **Texas Pre-Shale** | 0.72 | 0.70 | ✅ **Excellent** | Gas dominated, heat rate passthrough |
| **Texas Post-Pandemic** | 1.14 | 0.70 | ⚠️ Higher | ORDC scarcity pricing adds premium beyond fuel cost |
| **Texas Shale Boom** | 0.26 | 0.70 | ⚠️ Lower | Low gas prices → other costs became larger share |
| **US Average** | 0.41 (Pre-Shale) | 0.70 | ⚠️ Lower | Blended with regions where gas doesn't set marginal |
| **California** | Not significant | 0.70 | ❌ Decoupled | Non-fuel costs dominate (wildfires, grid, RPS) |

### Industry Sources Validating Our Findings

1. **EIA ERCOT Study (2022)**: "ERCOT's wholesale electricity prices are highly correlated with the price of natural gas, which is the fuel most often used by the most expensive (marginal) generator that sets the prices during most hours."

2. **Potomac Economics ERCOT State of Market Report (2023)**: "Electricity prices will be correlated with natural gas prices in a well-functioning market because fuel costs represent the majority of most suppliers' marginal production costs, and natural gas units are generally on the margin in ERCOT."

3. **PJM State of Market (2023)**: "Natural gas set marginal prices 84.3% of the time in the real-time market during the first nine months of 2023."

4. **FRED Blog (2025)**: "The divergence of electricity and natural gas prices... reflects structural pressures in the power sector that go beyond fuel costs."

### ⚠️ Issues Requiring Attention

1. **California Decoupling**: Our regressions show NO statistically significant relationship between gas and electricity prices in CA for Shale Boom and Post-Pandemic eras. This is **confirmed by industry observation** but means gas price is NOT a useful predictor for CA electricity.

2. **Texas Post-Pandemic Elasticity >1.0**: This exceeds theoretical heat rate. Likely explained by:
   - ORDC scarcity adders during tight reserve margins
   - Battery storage setting high offer prices
   - Small sample (n=5)

3. **US Average Negative Correlation in Recent Eras**: Statistically non-significant, but directionally suggests electricity prices rising while gas prices fell. Confirmed by FRED analysis as "structural pressures beyond fuel costs."

---

## Part 2: Upstream Factors → Gas Price

### Our Findings (Statistically Significant by Era)

| Factor | Pre-Shale β | Shale Boom β | Post-Pandemic β | Notes |
|--------|-------------|--------------|-----------------|-------|
| **Rig Count** | +0.0050** | +0.0021** | +0.052 (NS) | Consistent positive |
| **Production** | +0.29 (NS) | -0.050* | -0.10 (NS) | Flipped sign |
| **Storage** | +0.0066* | -0.0012 (NS) | -0.0088* | Flipped sign |
| **LNG Exports** | N/A | -0.22* | -0.32 (NS) | Counterintuitive negative |
| **Mexico Exports** | +5.44* | -0.37** | -2.10 (NS) | Flipped sign |

### Industry Benchmarks

1. **EIA Short-Term Energy Outlook**: Uses storage levels relative to 5-year average as primary price driver. Our Post-Pandemic finding (β = -0.0088, R² = 0.77) confirms: **+100 Bcf storage → -$0.88/MMBtu price**

2. **Potomac Economics**: Notes gas prices drive wholesale electricity, but production/storage fundamentals drive gas prices.

3. **Academic Studies on Price Elasticity**:
   - RAND study: Short-run price elasticity of demand = -0.21
   - German study (2024): Price elasticity = -0.01 to -0.04
   - US industrial (2021): Static own-price elasticity = -0.027 to -0.062

### ⚠️ Counterintuitive Results Requiring Investigation

1. **LNG Exports Negatively Correlated with Price (Shale Boom)**
   - Expected: Positive (exports reduce domestic supply → higher prices)
   - Found: Negative (β = -0.22, p < 0.05)
   - **Likely Explanation**: Spurious correlation. Both LNG exports and production grew simultaneously during shale boom. Production growth outpaced export growth, keeping prices low. This is a **multicollinearity problem** (VIF > 10 in multivariate regression).

2. **Storage Positively Correlated with Price (Pre-Shale)**
   - Expected: Negative (high storage → oversupply → lower prices)
   - Found: Positive (β = +0.0066, p < 0.05)
   - **Likely Explanation**: Reverse causality. High prices incentivized storage builds. Or: High demand drove both prices AND storage injections. This requires **instrumental variable approach** to resolve.

3. **Mexico Exports Sign Flip**
   - Pre-Shale: Positive (exports growing as prices rose)
   - Shale Boom: Negative (exports grew while prices fell)
   - **Explanation**: Time trend confound. Both are trending up over time but for different reasons.

---

## Part 3: Multicollinearity Issues

Our multivariate regression detected severe multicollinearity:

| Variable | VIF | Concern Level |
|----------|-----|---------------|
| Production | 51.6 | **SEVERE** |
| Mexico Exports | 27.1 | **SEVERE** |
| LNG Exports | 12.4 | **SEVERE** |
| Rig Count | 4.1 | Moderate |
| Storage | 1.9 | OK |

**Implication**: We cannot reliably estimate independent effects of Production, Mexico Exports, and LNG Exports in a single regression. They are all trending together over time.

### Recommended Solutions

1. **Use first differences** (year-over-year changes) instead of levels
2. **Use derived variables** like "Net Domestic Supply" or "Export Share"
3. **Focus on era-specific simple regressions** rather than multivariate
4. **Acquire monthly data** for more observations and less multicollinearity

---

## Part 4: Statistical Limitations

| Issue | Impact | Recommendation |
|-------|--------|----------------|
| **Small sample (Post-Pandemic n=5)** | Cannot reach significance for most factors | Pool with Shale Boom or wait for more years |
| **Annual data frequency** | Misses intra-year dynamics | Acquire monthly or quarterly data |
| **Retail vs wholesale prices** | Retail includes non-fuel costs that obscure relationship | Use ISO/RTO wholesale LMP data |
| **Structural breaks** | Cannot use single regression across eras | Era-specific models required |
| **Endogeneity** | Storage, production may be driven BY prices | Instrumental variables or lagged models |

---

## Part 5: Validated Relationships for DecarbIQ Model

Based on our analysis and industry validation, these relationships are **robust enough to use**:

### ✅ HIGH CONFIDENCE

| Relationship | Coefficient | Conditions | Source Validation |
|--------------|-------------|------------|-------------------|
| Gas → TX Electricity | +0.7 to +1.1 ¢/kWh per $/MMBtu | ERCOT market | Heat rate + EIA + Potomac |
| Storage → Gas Price | -$0.009/MMBtu per Bcf | Post-2020 | EIA STEO methodology |
| Rig Count → Gas Price | +$0.002-0.005/MMBtu per rig | All eras | Consistent across periods |

### ⚠️ MEDIUM CONFIDENCE

| Relationship | Coefficient | Conditions | Issue |
|--------------|-------------|------------|-------|
| Production → Gas Price | -$0.04-0.05/MMBtu per Bcf/d | Shale Boom only | Sign flipped between eras |
| Gas → US Avg Electricity | +0.4 ¢/kWh per $/MMBtu | Pre-Shale only | Not significant post-2008 |

### ❌ NOT USABLE

| Relationship | Reason |
|--------------|--------|
| Gas → CA Electricity | Statistically not significant in 2 of 3 eras |
| LNG Exports → Gas Price | Multicollinearity with production |
| Mexico Exports → Gas Price | Multicollinearity and sign instability |

---

## Part 6: Next Steps

1. **Acquire monthly data** from EIA for higher-frequency analysis
2. **Get wholesale LMP data** from ERCOT/CAISO for electricity prices
3. **Run first-difference regressions** to address multicollinearity
4. **Test lagged relationships** (rigs → production with 6-12 month lag)
5. **Build California non-fuel cost model** separately (wildfires, RPS, transmission)
6. **Validate with out-of-sample testing** (fit on 2000-2020, test on 2021-2025)

---

## Part 7: New Literature References

The following references were identified through literature review (February 2026) and are **new** additions beyond the original EIA, Potomac Economics, PJM, FRED, and RAND sources cited in Parts 1-5.

### 7.1 Gas-Electricity Price Passthrough & Heat Rates

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 1 | Borenstein & Bushnell, "The U.S. Electricity Industry After 20 Years of Restructuring" (Annual Review of Economics) | 2015 | Gas marginal cost passthrough in competitive wholesale markets is typically **$7-$12/MWh per $1/MMBtu** | https://doi.org/10.1146/annurev-economics-080614-115630 |
| 2 | Linn, Muehlenbachs & Wang, "How do Natural Gas Prices Affect Electricity Consumers?" (Energy Economics) | 2014 | Gas-to-electricity passthrough of ~**$6-$9/MWh per $1/MMBtu** in restructured markets | https://doi.org/10.1016/j.eneco.2014.03.015 |
| 3 | FERC Staff Report on Fuel-Electric Coordination | 2023 | Typical implied marginal heat rates: **7-10 MMBtu/MWh**; electricity price ~ Heat Rate × Gas Price + VOM | https://www.ferc.gov |
| 4 | Bushnell & Novan, "Setting with the Sun" (JAERE) | 2021 | Each additional GW of solar in CAISO reduced wholesale prices by $1.5-$3.5/MWh; peaker-driven pricing raises effective heat rates to **9-12 MMBtu/MWh** during peak hours | https://doi.org/10.1086/714088 |
| 5 | Joskow, "Challenges for Wholesale Electricity Markets with Intermittent Renewable Generation at Scale" | 2019 | High renewable penetration bifurcates price formation into zero-price hours and scarcity/peaker-priced hours | https://doi.org/10.1093/oxrep/grz001 |

### 7.2 Henry Hub Gas Price Forecasts & Supply Fundamentals

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 6 | EIA Annual Energy Outlook 2025 (Reference Case) | 2025 | Henry Hub spot: **$3.10 (2025), ~$3.40-$3.90 (2026-2030), ~$4.30 (2035)** | https://www.eia.gov/outlooks/aeo/ |
| 7 | EIA Short-Term Energy Outlook (STEO) | 2025 | Projects HH at **$3.00-$3.20 (2025), $3.80-$4.20 (2026)** | https://www.eia.gov/outlooks/steo/ |
| 8 | Goldman Sachs Commodities Research | 2024 | Long-term HH forecast: **$3.50-$4.50/MMBtu** through 2030; LNG exports add ~$0.50-$1.00 premium vs pre-2020 | Subscription |
| 9 | Deloitte "2025 Oil and Gas Industry Outlook" | 2024 | Projects HH in **$3.00-$4.50/MMBtu** range through end of decade | https://www2.deloitte.com/us/en/pages/energy-and-resources/articles/oil-and-gas-industry-outlook.html |
| 10 | Wood Mackenzie Long-Term Gas Outlook | 2024 | Projects HH at **$3.50-$4.50/MMBtu** (real) through 2035 | Subscription |
| 11 | S&P Global / IHS Markit Commodity Insights | 2024 | Base case HH at **$3.50-$4.00/MMBtu** through 2030. High case ~$5.50 only under simultaneous LNG surge + production curtailment | https://www.spglobal.com/commodityinsights/ |

### 7.3 GARCH Volatility Modeling for Natural Gas

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 12 | Bollerslev, "Generalized Autoregressive Conditional Heteroskedasticity" (J. Econometrics) | 1986 | Foundational paper: for well-specified GARCH(1,1), persistence (alpha+beta) must be **<1** for stationarity. Persistence=1 implies IGARCH. | https://doi.org/10.1016/0304-4076(86)90063-1 |
| 13 | Pindyck, "Volatility in Natural Resource Markets" (J. Energy & Development) | 2004 | GARCH(1,1) for natural gas: alpha ~**0.05-0.15**, beta ~**0.80-0.90**, persistence **0.85-0.95** | Journal of Energy and Development, 30(1) |
| 14 | Sadorsky, "Modeling and Forecasting Petroleum Futures Volatility" (Energy Economics) | 2006 | Energy GARCH: alpha **0.04-0.12**, beta **0.85-0.94**, persistence **0.93-0.99** | https://doi.org/10.1016/j.eneco.2006.04.005 |
| 15 | Kang et al., "Modeling Long Memory in Natural Gas Markets" (Energy Economics) | 2009 | NG GARCH(1,1): alpha ~**0.10**, beta ~**0.88**, persistence ~**0.98**. Recommends FIGARCH for long-memory. | https://doi.org/10.1016/j.eneco.2009.04.009 |
| 16 | Efimova & Serletis, "Energy Markets Volatility Modelling using GARCH" (Energy Economics) | 2014 | Henry Hub GARCH: alpha ~**0.08-0.12**, beta ~**0.85-0.90**. Near-unit-root persistence signals structural breaks. | https://doi.org/10.1016/j.eneco.2014.02.018 |
| 17 | Nick & Thoenes, "What Drives Natural Gas Prices?" (Energy Economics) | 2014 | Regime-switching GARCH decomposes near-unit-root persistence into distinct states with lower within-regime persistence | https://doi.org/10.1016/j.eneco.2014.05.003 |

### 7.4 Data Center Electricity Demand

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 18 | IEA, "Electricity 2024" | 2024 | Global DC electricity ~460 TWh (2022); US share ~40% (~184-210 TWh) | https://www.iea.org/reports/electricity-2024 |
| 19 | Goldman Sachs, "AI, Data Centers and the Coming US Power Demand Surge" | 2024 | US DC power demand to increase **160%** by 2030, reaching **~300+ TWh** | https://www.goldmansachs.com/insights/articles/AI-poised-to-drive-160-increase-in-power-demand |
| 20 | EPRI, "Powering Intelligence: Analyzing AI and Data Center Energy Consumption" | 2024 | US DC could reach **4.6-9.1%** of total US electricity by 2030 (~190-390 TWh) | https://www.epri.com/research/products/000000003002028905 |
| 21 | McKinsey, "How data centers and the energy sector can sate AI's hunger for power" | 2024 | High-adoption scenario: US DC demand **606 TWh** by 2030; base case ~350-400 TWh | https://www.mckinsey.com/industries/technology-media-and-telecommunications/our-insights/how-data-centers-and-the-energy-sector-can-sate-ais-hunger-for-power |
| 22 | Grid Strategies, "The Era of Flat Power Demand is Over" | 2023 | US utilities doubled 5-year load growth forecasts, driven by data centers, reshoring, electrification | https://gridstrategies.com/the-era-of-flat-power-demand-is-over/ |

### 7.5 Interconnection Queue & FERC Reform

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 23 | LBNL, "Queued Up" Report | 2024 | US interconnection queue reached **~2,600 GW** (end 2023); only **~21%** of projects reach COD; average time >5 years | https://emp.lbl.gov/queues |
| 24 | FERC Order 2023 (Final Rule RM22-14) | 2023 | "First-ready, first-served" cluster study process; expected to reduce queue times from 4-5 to ~2.5 years | https://www.ferc.gov/media/e-1-rm22-14-000 |

### 7.6 LNG Exports & Capacity

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 25 | EIA AEO 2024 LNG Projections | 2024 | US LNG capacity: **~14 Bcf/d (2024)** rising to **~24-25 Bcf/d by 2030**, potentially **28-30 Bcf/d by 2035** | https://www.eia.gov/outlooks/aeo/ |
| 26 | Oxford Institute for Energy Studies, "LNG Supply Wave" | 2023 | US reaching **25-28 Bcf/d** by 2030, full potential of **30+ Bcf/d** contingent on permitting | https://www.oxfordenergy.org/ |

### 7.7 Hurricane Frequency & Climate

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 27 | Knutson et al., "Tropical Cyclones and Climate Change Assessment" (BAMS) | 2020 | Projects **decreased** global TC frequency (-10% to -30%) but **increased** intensity (Cat 4-5 up 10-40%) | https://doi.org/10.1175/BAMS-D-18-0189.1 |
| 28 | IPCC AR6 WG1 Chapter 11 | 2021 | Medium confidence: total global TC frequency will **decrease or remain unchanged**; proportion of intense storms will increase | https://www.ipcc.ch/report/ar6/wg1/ |
| 29 | Vecchi et al., "Changes in Atlantic Major Hurricane Frequency" (Nature Communications) | 2021 | After correcting observational biases, **no significant long-term trend** in Atlantic major hurricane frequency 1851-2019 | https://doi.org/10.1038/s41467-021-24268-5 |

### 7.8 Policy & Macro

| # | Source | Year | Key Finding | URL |
|---|--------|------|-------------|-----|
| 30 | Rhodium Group, "A Turning Point for US Climate Progress" | 2022 | IRA projected to reduce emissions 32-42% by 2030; clean energy credits expected to **reduce** gas demand for power over time | https://rhg.com/research/climate-clean-energy-inflation-reduction-act/ |
| 31 | Resources for the Future (RFF) | 2023 | Net IRA effect on gas prices: **small and ambiguous** (~-$0.10 to +$0.50/MMBtu) | https://www.rff.org/publications/ |
| 32 | CBO, "Budget and Economic Outlook: 2024-2034" | 2024 | Long-run US GDP growth: **~1.8%** (vs model's 2.2-2.8%) | https://www.cbo.gov/publication/59710 |
| 33 | CARB, Annual Report on AB 32 | 2023 | CA carbon allowance prices $13-$38/ton CO2; implied wholesale price adder **+$5-$15/MWh** (not negative) | https://ww2.arb.ca.gov/our-work/programs/cap-and-trade-program |
| 34 | Wiser, Barbose et al. (LBNL), "Impact of RPS on US Electricity Market" | 2016 | RPS merit-order effect: **$1-$5/MWh per 10 pp** of renewable penetration | https://emp.lbl.gov/publications/impact-renewable-portfolio-standards |
| 35 | CAISO Dept. of Market Monitoring, Annual Report | 2023 | Negative pricing exceeded **1,000 hours** in 2023 (up from ~200 in 2018); evening ramps exceed 15 GW over 3 hours | http://www.caiso.com/market/Pages/MarketMonitoring/ |
| 36 | NREL (Denholm et al.), "Overgeneration from Solar Energy in California: A Field Guide to the Duck Chart" | 2015 | As solar >15%, midday overgeneration becomes routine; evening ramp requires CT/peaker capacity (heat rates 9-12 MMBtu/MWh) | https://www.nrel.gov/docs/fy16osti/65023.pdf |
| 37 | Lazard LCOE Analysis v17.0 | 2024 | Marginal cost existing NGCC: **$30-$60/MWh** at gas $3-5/MMBtu. Even at $10/MMBtu gas, NGCC marginal ~$70-100/MWh | https://www.lazard.com/research-insights/levelized-cost-of-energy/ |

---

## Part 8: Current Model vs Literature Comparison (Updated Model, Feb 2026)

This section compares the **current** (updated) model parameters and projections against the literature references catalogued in Part 7. The model was updated to address several issues identified in the initial review (IRA coefficient zeroed out, passthrough coefficients reduced, model complexity reduced).

### 8.1 Gas-to-Electricity Passthrough (Updated)

| Parameter | Current Model | Literature Range | Assessment |
|-----------|--------------|-----------------|------------|
| ERCOT passthrough | **$18.67/MWh** per $1/MMBtu | $7-$12/MWh (Refs 1-3) | Still **~1.9x too high**. Implied heat rate of 18.67 MMBtu/MWh vs CCGT 7.0 and fleet average 8-10. Improvement from prior $41.33 but remains above literature ceiling. Likely driven by scarcity events (Winter Storm Uri $1,485/MWh month) inflating the regression coefficient. |
| CAISO passthrough | **$10.74/MWh** per $1/MMBtu | $7-$12/MWh (Refs 1-4) | **Within range**. Consistent with CAISO's peaker-driven marginal pricing (Ref 4, 36) where effective heat rates reach 9-12 MMBtu/MWh during evening ramps. This is now well-calibrated. |
| ERCOT R² | **0.12** (n=169, k=11) | N/A | **Low explanatory power**. Only 12% of ERCOT price variance is explained. Using this for forward projections is risky; the model is capturing noise more than signal. |
| CAISO R² | **0.34** (n=183, k=8) | N/A | **Moderate**. Adequate for directional guidance but not precision forecasting. |

### 8.2 Henry Hub Gas Price Forecasts (Unchanged from Prior Version)

| Year | Model Mean | EIA AEO 2025 (Ref 6) | NYMEX Futures | Goldman Sachs (Ref 8) | Gap |
|------|-----------|----------------------|--------------|----------------------|-----|
| 2026 | **$9.71** | ~$3.40 | ~$3.80 | ~$3.50-$4.00 | **2.5-2.9x too high** |
| 2027 | **$12.63** | ~$3.60 | ~$4.00 | ~$3.50-$4.50 | **2.8-3.5x too high** |
| 2028 | **$12.26** | ~$3.70 | ~$4.00 | ~$3.50-$4.50 | **2.7-3.3x too high** |
| 2030 | **$12.39** | ~$3.90 | ~$4.20 | ~$3.50-$4.50 | **2.8-3.2x too high** |
| 2035 | **$7.79** | ~$4.30 | ~$4.50 | ~$4.00-$5.00 | **1.6-1.8x too high** |
| Latest actual (2024) | N/A | N/A | N/A | N/A | HH averaged ~$2.10-$2.50 |

**Root cause**: The LNG capacity curve model (monte_carlo_lng_v8.json) shows 2024 utilization at 107.4% ("Crisis" regime), setting a base mean of $11.19/MMBtu. Actual 2024 HH was ~$2.10-$2.50. The capacity-utilization-to-price mapping is miscalibrated. Gas prices have not sustained $9+/MMBtu since the pre-shale era (2005-2008), when US production was ~52 Bcf/d vs today's ~105 Bcf/d.

### 8.3 Wholesale Electricity Price Projections (Updated)

| Year | ERCOT Model | ERCOT Actual/Consensus | CAISO Model | CAISO Actual/Consensus |
|------|------------|----------------------|------------|----------------------|
| 2025 | **$55.89** | ~$25-$40 (Ref 37) | **$113.10** | ~$40-$55 |
| 2026 | **$181.00** | ~$25-$45 | **$185.39** | ~$40-$60 |
| 2027 | **$235.55** | ~$30-$50 | **$217.06** | ~$40-$60 |
| 2030 | **$230.94** | ~$30-$55 (EIA AEO) | **$215.25** | ~$40-$60 |
| 2035 | **$145.11** | ~$30-$55 | **$167.25** | ~$40-$65 |
| Latest actual (2024) | N/A | **$25.06/MWh** | N/A | **$40.00/MWh** |

**Assessment**: Even after reducing passthrough coefficients, projections remain **4-6x too high** for ERCOT and **3-4x too high** for CAISO. The primary driver is the inflated gas price forecasts: $12.63 gas × $18.67 passthrough = $236/MWh for ERCOT. Correcting the gas forecast alone (e.g., $3.80 × $18.67 = $71/MWh) would bring projections much closer to reality, though the passthrough is still high for ERCOT.

**Corrected estimate**: At consensus gas prices ($3.50-$4.50/MMBtu):
- ERCOT: $3.80 × 18.67 = ~$71/MWh (still high due to elevated passthrough)
- ERCOT at literature passthrough: $3.80 × 8.5 = ~$32/MWh (matches actuals)
- CAISO: $3.80 × 10.74 = ~$41/MWh (matches actuals well)

### 8.4 GARCH Volatility Model (Updated)

| Parameter | Current Model | Literature Typical (Refs 12-17) | Assessment |
|-----------|--------------|-------------------------------|------------|
| Alpha | **0.979** | 0.05-0.15 | **Still ~7-20x too high**. Improved from 1.0 but still far from typical. |
| Beta | **~0 (9.7e-16)** | 0.80-0.92 | **Effectively zero vs should dominate**. Literature finds beta is the largest parameter. |
| Persistence | **0.979** | 0.85-0.99 | **Within range** but achieved via wrong channel (all alpha, no beta). |
| Regime distribution | 169/169 "Crisis" | Should distribute across regimes | **All observations in one regime** = regime classification is non-functional. |
| Half-life | **32.3 months** | 2-12 months typical | **Far too long** for natural gas markets. |

**Interpretation**: The GARCH parameters improved (persistence dropped from 1.0 to 0.979, no longer IGARCH), but the fundamental issue remains: alpha captures essentially all persistence while beta is zero. In the literature (Refs 13-16), beta (autoregressive variance persistence) is the dominant parameter (~0.85-0.92) while alpha (shock impact) is small (~0.05-0.15). The current model reverses this, meaning every shock has near-permanent impact on volatility but there is no "normal" baseline variance that the model reverts to. This likely results from estimating GARCH on price levels rather than log-returns.

### 8.5 Data Center Demand Projections (Well-Calibrated)

| Variable | Model 2025 | Model 2030 | Model 2035 | Literature Range | Assessment |
|----------|-----------|-----------|-----------|-----------------|------------|
| US DC TWh | **217** | **326** | **489** | 160-390 (2030, Refs 18-21) | **Middle of range** for 2030; 2035 extrapolation plausible |
| TX DC TWh | **7.3** | **16.7** | **38.3** | Consistent with ERCOT queue data | **High-end plausible**; 18% CAGR is aggressive |
| DC coefficient on gas | **0.0122** $/MMBtu per TWh | $0.003-$0.02 implied from supply elasticity | **Now within range** (improved from 0.0591) |

**Assessment**: The data center demand trajectory is one of the model's strongest components. The updated DC-to-gas coefficient (0.0122, down from 0.0591) is now consistent with supply-elasticity-implied estimates. At 100 TWh incremental demand, the model now implies ~$1.22/MMBtu gas price impact, which falls within the $0.30-$2.00 range estimated by Goldman Sachs and NBER supply elasticity studies.

### 8.6 Other Model Components

| Component | Model Value | Literature | Assessment |
|-----------|------------|-----------|------------|
| Interconnection queue (2025) | **2,600 GW** | 2,600 GW (Ref 23, LBNL end-2023) | **Directly matches** |
| LNG capacity trajectory | 14→30 Bcf/d | 14→24-30 Bcf/d (Refs 25-26) | **Well-calibrated** |
| IRA rollback probability | **15-25%** | 15-30% analyst consensus | **Well-calibrated** |
| IRA coefficient on gas | **0.0** (zeroed out) | -$0.10 to +$0.50 (Refs 30-31) | **Correctly zeroed** -- prior $8.57 was confounded with 2022 gas crisis |
| Hurricane frequency | **+2%/year** | Flat or declining (Refs 27-29, IPCC) | **Contradicts literature** -- should model intensity increase, not frequency |
| GDP growth 2025-2035 | **2.8% → 2.2%** | CBO: 2.0% → 1.8% (Ref 32) | **0.4-0.8pp too high** throughout |
| TX gen mix: coal to 0% by 2034 | Declining from 6.8% | Supported by retirement announcements | **Well-supported** |
| TX renewable to 50.6% by 2035 | Rising from 29.1% | 45-55% range (BNEF, ERCOT CDR) | **Within range** |

### 8.7 Remaining Critical Issues — Detailed Literature Evidence

After the model updates (reduced passthrough, zeroed IRA/FERC/queue coefficients, reduced DC coefficient, improved GARCH persistence), the following issues remain. Each is documented with specific literature data points for verification.

| Priority | Issue | Current Gap | Root Cause |
|----------|-------|------------|------------|
| **1 (Critical)** | Gas price forecasts 2.5-3.5x above consensus | $9.71-$12.63 vs $3.40-$4.50 | LNG capacity curve calibration; "Crisis" regime for 100% of observations |
| **2 (Critical)** | ERCOT electricity projections 4-6x too high | $181-$236 vs $25-$50 | Compounding of inflated gas × elevated passthrough |
| **3 (High)** | CAISO electricity projections 3-4x too high | $185-$217 vs $40-$60 | Driven primarily by inflated gas prices (passthrough now reasonable) |
| **4 (High)** | GARCH alpha/beta inverted | alpha=0.979/beta~0 vs 0.10/0.88 | Likely estimating on levels not log-returns |
| **5 (Medium)** | Hurricane frequency assumption | +2%/year vs flat/declining | Contradicts IPCC AR6 consensus |
| **6 (Low)** | GDP growth assumptions | 0.4-0.8pp above CBO/Fed | Minor effect on projections |

---

#### Issue 1 (Critical): Henry Hub Gas Price Forecasts — $9.71-$12.63 vs Consensus $3.40-$4.50

**What the model produces:**
- 2026 mean: $9.71/MMBtu (P10: $7.73, P90: $12.44)
- 2027 mean: $12.63/MMBtu (P10: $9.99, P90: $15.83)
- 2030 mean: $12.39/MMBtu (P10: $9.49, P90: $15.68)
- 2035 mean: $7.79/MMBtu (P10: $3.29, P90: $12.29)
- Latest actual (2024 observed): **$3.01/MMBtu** (from `decarbiq_projection_map.json` metadata)

**What the literature says — specific data points:**

| Source | Year Published | 2025 Forecast | 2026 Forecast | 2030 Forecast | 2035 Forecast |
|--------|--------------|--------------|--------------|--------------|--------------|
| **EIA AEO 2025 Reference Case** (Ref 6) | 2025 | $3.10 | $3.40 | $3.90 | $4.30 |
| **EIA AEO 2025 High Oil & Gas Supply Price** | 2025 | ~$3.50 | ~$4.00 | ~$5.50 | ~$6.00 |
| **EIA STEO** (Ref 7) | Jan 2025 | $3.00-$3.20 | $3.80-$4.20 | — | — |
| **NYMEX Henry Hub Futures** (Q1 2025 strip) | 2025 | ~$3.50 | ~$3.80 | ~$4.00-$4.30 | ~$4.50 |
| **Goldman Sachs** (Ref 8) | 2024 | — | ~$3.50-$4.00 | ~$3.50-$4.50 | — |
| **Deloitte** (Ref 9) | 2024 | — | — | $3.00-$4.50 | — |
| **Wood Mackenzie** (Ref 10) | 2024 | — | — | $3.50-$4.50 | $3.50-$4.50 |
| **S&P Global / IHS Markit** (Ref 11) | 2024 | — | — | $3.50-$4.00 (base) | — |

**Why the model diverges — root cause chain:**
1. The Monte Carlo model (`monte_carlo_lng_v8.json`) uses an LNG capacity utilization curve to set the gas price base mean
2. The model calculates 2024 LNG utilization at **107.4%** (>100% = "Crisis" regime)
3. This maps to a 2024 adjusted base mean of **$11.19/MMBtu** — but actual 2024 HH was **$2.10-$2.50/MMBtu**
4. The GARCH volatility overlay then adds further upward skew from "Crisis" regime classification
5. **The last time HH sustained $9-12/MMBtu was 2005-2008**, when US dry gas production was ~52 Bcf/d. Current production is ~105 Bcf/d — roughly 2x higher, making those price levels structurally implausible absent unprecedented supply disruption

**Specific remediation paths from literature:**
- EIA AEO uses NEMS (National Energy Modeling System), which models supply curves, demand sectors, and trade flows simultaneously. The EIA's supply elasticity assumptions result in modest LNG export-driven price increases of $0.50-$1.00/MMBtu above pre-export-era levels (EIA LNG Export Study, 2018)
- Goldman Sachs estimates incremental LNG demand adds 3-5 Bcf/d by 2030, which at short-run supply elasticity of ~0.2 implies ~$0.50-$2.00/MMBtu above today's $3/MMBtu — nowhere near $12
- The model's LNG utilization-to-price mapping needs recalibration against actual observed utilization vs price pairs (e.g., 2023-2024 utilization was ~85-90% at actual LNG facilities, yet prices were $2-3/MMBtu, not $11)

---

#### Issue 2 (Critical): ERCOT Wholesale Electricity Projections — $181-$236 vs Actual $25-$50

**What the model produces:**
- 2025: $55.89/MWh (low: $18.55, high: $93.22)
- 2026: $181.00/MWh (low: $143.95, high: $231.92)
- 2027: $235.55/MWh (low: $186.25, high: $295.19)
- 2030: $230.94/MWh (low: $176.79, high: $292.30)
- Gas component dominates: 2027 gas_component = $235.85 out of $235.55 total
- Latest actual (2024 observed): **$25.06/MWh** (from metadata)

**What the literature says — specific data points:**

| Source | Year | ERCOT Wholesale Price | Notes |
|--------|------|----------------------|-------|
| **Potomac Economics, 2023 ERCOT State of Market Report** | 2024 | 2023 load-weighted avg: **~$28-30/MWh** | Down from 2022 due to lower gas and record renewables |
| **Potomac Economics, 2022 ERCOT State of Market Report** | 2023 | 2022 load-weighted avg: **~$75-80/MWh** | High gas prices ($6-9/MMBtu) drove elevated levels |
| **ERCOT actual prices** (from model's own data) | 2024 | 2019: $38, 2020: $22.50, 2021: $80.30 (incl. Uri), 2022: $75.80, 2023: $28.40, 2024: $25.06 | 6-year average excl. Uri: ~$40 |
| **EIA AEO 2025** (Ref 6) | 2025 | TX wholesale 2025-2035: **$30-$50/MWh** range | Even High Oil & Gas case stays below $100/MWh annual avg |
| **Lazard LCOE v17.0** (Ref 37) | 2024 | Existing NGCC marginal cost: **$30-$60/MWh** at gas $3-5/MMBtu | At $10/MMBtu gas (extreme), NGCC marginal ~$70-100/MWh |
| **ERCOT CDR (Capacity, Demand & Reserves)** | 2024 | Reserve margins >20% through 2028+ | No structural basis for sustained scarcity pricing |
| **ICF International / consultant consensus** | 2024 | ERCOT wholesale 2025-2030: **$30-$55/MWh** | Data center load may push toward higher end |

**Why the model diverges — arithmetic decomposition:**
- The gas_component for 2027 = $235.85/MWh
- This equals: gas price ($12.63/MMBtu) × passthrough ($18.67/MWh per $/MMBtu) = $235.88 ✓
- The error compounds two separate over-estimates:
  - **Gas price over-estimate**: $12.63 vs consensus $3.80 → 3.3x multiplier
  - **Passthrough over-estimate**: $18.67 vs literature $7-10 → 1.9-2.7x multiplier
  - **Combined**: 3.3 × 2.0 = 6.6x → $235 vs corrected ~$32-$38

**Cross-check with actual implied heat rates:**
- Potomac Economics reports ERCOT system-wide marginal heat rates of **7.0-8.5 MMBtu/MWh** during non-scarcity hours (load-weighted average ~8.0)
- The model's regression diagnostics (`regression_diagnostics_v2.json`) report an ERCOT implied heat rate of **8.2 MMBtu/MWh** — which is consistent with Potomac
- But the regression coefficient of 18.67 implies 18.67 MMBtu/MWh equivalent, which is **2.3x the model's own diagnostic heat rate**
- This discrepancy arises because the regression coefficient is inflated by scarcity events (Feb 2021 at $1,485/MWh, Aug 2023 at $263/MWh) which create leverage points in the OLS regression
- **Recommendation**: Use robust regression (e.g., Huber M-estimator) or exclude scarcity months, then add a separate scarcity overlay based on the model's existing scarcity event detection

---

#### Issue 3 (High): CAISO Wholesale Electricity Projections — $185-$217 vs Actual $40-$60

**What the model produces:**
- 2025: $113.10/MWh (gas_component: $32.34, policy_component: $0.00)
- 2026: $185.39/MWh (gas_component: $104.35, policy_component: $0.00)
- 2027: $217.06/MWh (gas_component: $135.75, policy_component: $0.00)
- 2030: $215.25/MWh (gas_component: $133.10, policy_component: $0.00)
- Latest actual (2024 observed): **$40.00/MWh** (from metadata)

**What the literature says — specific data points:**

| Source | Year | CAISO Wholesale Price | Notes |
|--------|------|----------------------|-------|
| **CAISO Dept. of Market Monitoring Annual Report** (Ref 35) | 2023 | 2023 avg day-ahead: **~$45-55/MWh** | Down from 2022; negative pricing >1,000 hours |
| **CAISO actual** (from model's own metadata) | 2024 | 2024: **$40.00/MWh** | |
| **EIA California Electricity Profile** | 2024 | CA wholesale (hub avg): **~$40-55/MWh** (2022-2024 range) | Retail much higher at $0.27-$0.32/kWh due to non-fuel costs |
| **EIA AEO 2025** | 2025 | CA wholesale 2025-2035: **$40-$65/MWh** projected | Higher than ERCOT due to carbon costs and duck curve dynamics |

**Why the model diverges — arithmetic decomposition:**
- For CAISO, the passthrough ($10.74) is now reasonable (within $7-$12 literature range)
- The 2027 gas_component = $135.75 = gas price ($12.63) × passthrough ($10.74) = $135.65 ✓
- The CAISO intercept in the regression is $79.17 (from `decarbiq_model_params_v4.json`)
- So CAISO total ≈ $79.17 (intercept) + $135.75 (gas) + other terms = $217
- **The primary issue is the gas price forecast, not the passthrough**
- At consensus gas prices: $3.80 × $10.74 = ~$40.81 gas component + $79.17 intercept = **~$120/MWh**
- Even correcting gas, the $79.17 intercept seems high — it may be absorbing effects of variables not included in the reduced model (previously zeroed-out policy variables like cap-and-trade, RPS targets, etc.)
- **The CAISO intercept needs investigation**: with policy_component now at $0.00 (zeroed IRA/FERC/queue), the intercept may be compensating; the old model had large negative policy components (e.g., -$1,015 in 2034) that offset inflated coefficients. The current model removed those but the intercept may still embed structural assumptions from the full estimation

**CAISO-specific literature context:**
- Borenstein & Bushnell (2015, Ref 1): CA wholesale increasingly driven by non-fuel factors — CPUC cost allocation for wildfire liability adds **$5-$10 billion/year** across IOUs, but this affects **retail**, not wholesale
- Bushnell & Novan (2021, Ref 4): Duck curve dynamics mean CAISO wholesale is bifurcated — near-zero midday, peaker-priced evenings. Annual average wholesale should remain in the $40-$65/MWh range absent gas price shocks
- CAISO DMM reports that SoCal Citygate gas basis (premium over Henry Hub) adds ~$1-$3/MMBtu, which would imply CAISO gas passthrough using local gas prices should be slightly higher than using HH directly — the model's $10.74 on HH may partially capture this basis

---

#### Issue 4 (High): GARCH(1,1) Alpha/Beta Inversion

**What the model produces** (from `garch_volatility_model_v4.json`):
- Alpha: **0.9788** (shock impact on variance)
- Beta: **9.67e-16 ≈ 0** (autoregressive variance persistence)
- Omega: **0.0847** (constant/baseline variance)
- Persistence (alpha + beta): **0.9788**
- Half-life: **32.3 months**
- Unconditional monthly volatility: **199.8%** (annualized: 692.2%)
- Regime distribution: **169/169 observations in "Crisis"** (Low: 0, Normal: 0, Elevated: 0)
- Volatility forecast: month_1 = 151%, month_6 = 263%, month_12 = 345%, month_24 = 448%

**What the literature says — specific parameter estimates for natural gas:**

| Study | Alpha (ARCH) | Beta (GARCH) | Persistence | Data | Notes |
|-------|-------------|-------------|-------------|------|-------|
| **Pindyck (2004)** (Ref 13) | 0.05-0.15 | 0.80-0.90 | 0.85-0.95 | NG futures, 1990s-2000s | "Volatility in Natural Resource Markets" |
| **Sadorsky (2006)** (Ref 14) | 0.04-0.12 | 0.85-0.94 | 0.93-0.99 | Energy futures | Standard energy GARCH estimates |
| **Kang et al. (2009)** (Ref 15) | ~0.10 | ~0.88 | ~0.98 | NG daily prices | Recommends FIGARCH for long memory |
| **Efimova & Serletis (2014)** (Ref 16) | 0.08-0.12 | 0.85-0.90 | 0.93-0.98 | Henry Hub daily | Near-unit-root = structural breaks |
| **Nick & Thoenes (2014)** (Ref 17) | Varies by regime | Varies by regime | <0.95 within regimes | EU/US NG | Regime-switching decomposes persistence |
| **Mohammadi & Su (2010)** | 0.05-0.15 | 0.80-0.92 | 0.90-0.99 | Intl. energy | Survey of energy GARCH studies |

**Key discrepancies:**

1. **Alpha vs Beta roles are reversed**: In all published studies, **beta dominates** (0.80-0.94) while **alpha is small** (0.04-0.15). The model has alpha=0.979 and beta≈0 — the exact opposite. This means:
   - Literature: yesterday's variance is the best predictor of today's variance (beta carries memory)
   - Model: yesterday's *shock* determines today's variance (no memory of past variance levels)
   - Practically: the model says a single large price move permanently resets the volatility regime, with no mean-reversion in variance — every spike has near-permanent effect

2. **Beta ≈ 0 is diagnostic of misspecification**: Engle & Bollerslev (1986, foundational GARCH paper, Ref 12) note that beta captures the autoregressive component of conditional variance. Beta near zero means the GARCH reduces to an ARCH model where only lagged squared residuals matter. In energy markets, this is universally rejected in favor of GARCH with significant beta.

3. **All 169 observations in "Crisis" regime**: The model defines regimes based on annualized volatility thresholds (Low: <30%, Normal: 30-50%, Elevated: 50-80%, Crisis: >80%). With unconditional volatility at 692% annualized, every observation exceeds the Crisis threshold. This means:
   - The regime classification provides zero information (no discrimination)
   - The "Crisis" label is meaningless when it applies to every month from 1997-2024, including low-volatility periods (e.g., 2015-2019 when HH traded in a narrow $2-3/MMBtu range)

4. **Half-life of 32.3 months is too long**: Literature finds natural gas volatility half-lives of **2-12 months** — shocks dissipate within a quarter to a year. A 32-month half-life means a shock in Jan 2025 still has 50% impact on volatility in Sep 2027.

5. **Volatility forecasts are explosive**: Month_24 forecast of 448% annualized volatility implies the model expects monthly price swings of ~130% (±$4/MMBtu at current levels) as *normal* within 2 years. Actual HH monthly volatility is typically 15-40% annualized in non-crisis periods.

**Likely root cause**: The model appears to estimate GARCH on **monthly price levels** (or monthly residuals from a linear regression in levels) rather than on **log-returns** or **percentage changes**. Monthly NG price levels are non-stationary (unit root), so the GARCH optimizer pushes alpha toward 1.0 to capture the level-persistence through the variance equation. The fix per Bollerslev (1986) and all subsequent literature is to estimate GARCH on stationary series: log(P_t / P_{t-1}) or regression residuals from a properly specified mean equation with stationarity confirmed via ADF/KPSS tests.

---

#### Issue 5 (Medium): Hurricane Frequency — +2%/Year vs IPCC Flat/Declining

**What the model produces** (from `event_frequency_projections_v4.json`):
- Climate-adjusted Poisson model with **+2% annual increase** in hurricane frequency
- Base rate: ~2.0 storms/year (2025), rising to ~2.35 storms/year (2035)
- Climate factor: 1.0 (2025) → 1.175 (2035), i.e., +17.5% more storms by 2035
- Trend stated as: "+1.51 storms per decade"
- Monthly seasonality peaks in September at weight 0.32

**What the literature says — specific findings:**

| Study | Year | Frequency Finding | Intensity Finding | Key Conclusion |
|-------|------|-------------------|-------------------|----------------|
| **Knutson et al. (BAMS)** (Ref 27) | 2020 | Global TC frequency projected to **decrease 10-30%** | Cat 4-5 storms increase **10-40%** | "It is premature to conclude with high confidence that increases in TC activity have emerged from the background natural variability" |
| **IPCC AR6 WG1 Ch.11** (Ref 28) | 2021 | "Total global number of TCs will **not increase** or will **decrease**" (medium confidence) | "Proportion of intense TCs will **increase**" (medium-high confidence) | Frequency decrease + intensity increase is the consensus |
| **Vecchi et al. (Nature Comms)** (Ref 29) | 2021 | After bias correction: "**no significant trend**" in Atlantic major hurricane frequency 1851-2019 | Rapid intensification rates increasing | 170 years of data show no frequency trend |
| **NOAA GFDL Hurricane Research Division** | 2024 | "A **decrease** in the overall frequency of TCs globally" under warming | "Increase in the average intensity of TCs" | Primary government research lab |
| **Emanuel (JAMES)** | 2021 | Ambiguous frequency change (some models up, some down) | **+5-10% intensity per degree C** warming | Intensity, not frequency, is the robust signal |
| **Bhatia et al. (Nature Comms)** | 2019 | No robust frequency trend | **Rapid intensification rates** (24-hour strengthening) increasing significantly | The energy infrastructure risk is from faster-intensifying storms, not more storms |

**Specific problematic claims in the model:**
1. **"+1.51 storms per decade"** — This would mean ~15 additional storms by 2035 compared to today, an extraordinary increase. The observed Atlantic hurricane rate is ~12-14 named storms/year with ~6-7 hurricanes. Adding 1.5/decade would imply ~14-16 named storms by 2035 — above recent active period averages but the attribution to climate change is not supported.
2. **"+2%/year compounding"** — Over a 30-year horizon (to 2055), this implies a 1.02^30 = 1.81x increase (81% more hurricanes). No published climate model projects anything close to this for tropical cyclone frequency. Even the most aggressive high-sensitivity projections (RCP8.5/SSP5-8.5) show flat or declining total TC frequency.
3. The **seasonality weights** (Sep peak at 0.32, Oct at 0.23, Aug at 0.21) are accurate and match the observed Atlantic hurricane season climatology from NOAA's Historical Hurricane Tracks database.

**What the model should capture instead (per literature):**
- **Intensity increase**: Cat 4-5 proportion rises ~10-40% (Knutson 2020) — this affects peak wind damage and infrastructure disruption differently than frequency
- **Rapid intensification**: storms strengthen faster (Bhatia 2019), reducing warning time for grid operators and increasing the probability of unprepared-for disruption events
- **Rainfall rate increase**: ~10-15% per degree C warming (Knutson 2020), increasing flood risk to substations and gas infrastructure
- **Storm surge amplification**: from sea level rise (~3-4mm/year), increasing coastal infrastructure flooding independent of storm frequency
- The **energy price impact** of hurricanes comes primarily from **Gulf of Mexico gas production shut-ins** and **refinery outages**, which are driven by storm *proximity and intensity*, not raw frequency. A better model would estimate shut-in volumes as a function of storm intensity and track rather than simply counting storms.

### 8.8 What's Working Well

- CAISO gas passthrough ($10.74) is now within literature range
- Data center demand trajectories match IEA/EPRI/Goldman Sachs consensus
- DC coefficient on gas (0.0122) is now reasonable
- Interconnection queue data directly matches LBNL
- LNG capacity projections match EIA/Oxford consensus
- IRA rollback probability aligns with analyst consensus
- IRA coefficient correctly zeroed out (no longer confounded with 2022 crisis)
- TX generation mix projections are plausible
- Monte Carlo + GARCH + regime-switching methodology is well-established in literature
