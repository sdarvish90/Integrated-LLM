# DecarbIQ Pipeline — Change Log

All changes are in `decarbiq_policy_regulation_update_pipeline.py` unless otherwise noted.

---

## Change 1: Data Integration — Regime Indicators, Hydro, and Basis (step1b)

**Status:** Complete
**Lines:** ~2840–2910 in step1b_enhance_master()

**What was added:**
- `ercot_regime` column: `"normal"` / `"ng_crisis"` / `"elec_crisis"`
- `caiso_regime` column: `"normal"` / `"heat_crisis"` / `"drought_stress"`
- Binary indicators: `is_ng_crisis`, `is_elec_crisis`, `is_caiso_heat_crisis`, `is_caiso_drought`
- `ca_hydro_vs_normal_pct` from drought data (annual, forward-filled monthly)
- `ca_drought_severity_score` (Moderate=1 to Exceptional=4)
- `ca_reservoir_pct` from Shasta levels (annual)
- `california_basis` = citygate − HH (monthly)
- `scarcity_pct` = ercot_scarcity_intensity / ercot_price_cap_mwh (normalizes for 2022 ORDC reform)

**Data sources (all existing CSV/JSON):**
- `geopolitical_and_macro/geopolitical_macro_disruptions.csv`
- `weather_climate/major_grid_stress_events.csv`
- `weather_climate/drought_wildfire_grid_impacts.csv`
- `generation_mix_and_capacity/reservoir_levels_database.csv`
- `fuel_costs_and_supply/decarbiq_monthly_gas_prices.csv`

**Regime classification logic:**
- ERCOT NG-crisis: disruption events with `gas_price_impact >= 1.5`
- ERCOT Elec-crisis: `scarcity_pct > 0.05` (captures ~25 months)
- Priority: elec_crisis > ng_crisis when both active (e.g., Uri)
- CAISO heat-crisis: grid stress events where region=CAISO
- CAISO drought: `hydro_vs_normal_pct < 70%`

---

## Change 2: CAISO Gas Variable Switch (step2b)

**Status:** Complete
**Lines:** ~2909 (caiso_base definition)

**What changed:**
- Replaced `henry_hub_spot` with `california_citygate` in CAISO regression specs
- Added `ca_hydro_vs_normal_pct` to CAISO base variables
- All downstream code (step11, step12) updated to use citygate

---

## Change 3: Variant H — Regime-Switching Regressions (step2b)

**Status:** Complete
**Lines:** ~3366–3600 (after Variant G, before selection scoring)

**What was added:**
- **H1 Normal-regime OLS:** Same vars as Variant A, filtered to normal months
- **H2 NG-crisis interaction model:** Full sample + `is_ng_crisis` + `henry_hub_spot × is_ng_crisis`
- **H3 Elec-crisis scarcity scaling:** `log(price) = α + β × log(scarcity_pct)` on elec-crisis months
  - Type-gated empirical pools (cold_weather_pool, summer_heat_pool)
  - Current VOLL cap stored for denormalization
- Equivalent CAISO models (heat-crisis interaction, drought/hydro)
- HH gas models (normal-regime, NG-crisis interaction)

**Design decision:** H doesn't compete with A–F variants. H results always stored and used for regime-aware projections.

**Output:** `reg_v2["regime_models"]` dict with all regime model results

---

## Change 4: MC Regime-Switching (step6)

**Status:** Complete
**Lines:** ~6420–6790 in step6_monte_carlo()

**What was added:**
- Events trigger regimes (no separate Markov chain)
- Gas MC has two regimes: normal and ng_crisis
- Regime-specific mean-reversion (AR(1) estimated speeds)
- Regime-specific GARCH volatility (normal from step3, crisis empirical variance)
- Crisis level shift from H2 interaction coefficient
- Regime tracker matrix (10,000 × 60) for fraction computation

**Key parameters (data-estimated):**
- `speed_normal`: 0.0576/month (AR(1) on log-prices, normal months)
- `speed_crisis`: 0.0864/month (AR(1) on log-prices, crisis-adjacent months)
- `uncond_var_normal`: 0.0217
- `uncond_var_crisis`: 0.0335 (1.24× normal)

---

## Change 5: Citygate Gas Projection (step6)

**Status:** Complete
**Lines:** ~6517–6570 (basis model), ~6792–6815 (citygate paths)

**What was added:**
- OLS basis model: `california_basis = f(ca_gas_gen_pct, ca_cdd, ca_hydro_vs_normal_pct, trend)`
- Bootstrapped residuals (non-normal tail risk preserved)
- Citygate paths = HH paths + basis model prediction + noise
- Annual citygate forecasts (mean, median, P5, P95)

---

## Change 6: Step 11 Projection Map Updates

**Status:** Complete
**Lines:** ~7773–8235 in step11_projection_map()

**What changed:**
- ERCOT uses regime-weighted projections (unconditional expected value)
- CAISO uses citygate gas (not HH)
- Delta method preserved (anchored to observed 2025 values)

---

## Change 7: Step 12 — Data-Estimated Regimes

**Status:** Complete
**Lines:** ~7312–7461 (REGIMES dict, RegimeDetector, _regime_passthrough)

**What changed:**
- Replaced hardcoded REGIMES multipliers with regression-estimated values
- RegimeDetector uses event-based classification
- Passthrough coefficients from Variant H results

---

## Change 8: Extended Projections & Literature Validation (step14)

**Status:** Complete
**Lines:** ~4629–5400 in step14_expanded_validation()

**What was added:**
- 14E-8: Mean-reversion speed vs literature (Schwartz/Pilipovic)
- 14E-9: LNG capacity vs literature
- 14E-10: Battery storage vs literature
- 14E-11: Electricity projections vs historical benchmarks (CAGR comparison)
- 14F: Decarbonization milestones (TX coal→0, TX gas<35%, etc.)
- 14G: Carbon intensity trajectory (lbs CO₂/MWh from gen mix × EPA eGRID factors)
- 14H: Price volatility term structure (implied vol from MC percentile bands)

**Files modified:**
- `literature_benchmarks.json`: Added `carbon_emission_factors`, `generation_mix_benchmarks`, `grid_reliability_benchmarks`

---

## Change 9: Data-Driven Variable Projections

**Status:** Complete
**Lines:** ~1020–1444 (`_fit_variable_trend()`, `_build_all_projections()`)

**What was added:**
- `_fit_variable_trend()`: Fits linear/quadratic/exponential to historical annual data
  - Auto-onset detection for variables that started near zero
  - Short-window guard (≤5yr → linear unless quadratic improves adj R² by >0.05)
  - Physical bounds only (no market caps)
  - Extrapolation warnings, structural break flags
- `_build_all_projections()`: Computes all 53 variable projections once
  - Category A: 19 trend-fitted + 3 residual-derived
  - Category B: 15 policy/legislation assumptions
  - Category C: 8 weather normals
  - Category D: 4 exogenous macro inputs
- Gen mix residual approach: TX/CA gas gen % = 100 − sum(fitted movers) − constants
- Generation mix consistency check: forces TX and CA shares to sum to 100%

**What changed in existing code:**
- step6 reads from `all_projections` (computed once)
- step11 reads from `all_projections`
- step14 reads from model output (no hardcoded formula copies)
- `_render_projection_chart` uses `all_projections`
- `main()` calls `_build_all_projections()` once, passes to all steps

**Literature benchmarks reverted:**
- TX battery in `literature_benchmarks.json`: reverted from Modo Energy to EIA/BNEF values

---

## Change 10: Supply-Response Soft Floor (step6 MC)

**Status:** Complete
**Date:** 2026-02-14
**Lines:** ~6422 (supply_speed fallback), ~6518–6540 (AR(1) estimation), ~6575–6578 (BREAKEVEN/ABS_FLOOR), ~6817–6822 (simulation loop)

**What was added:**
- Replaced hard reflecting floor at $1.50 with supply-response soft floor
- `BREAKEVEN = $1.75` (marginal Marcellus breakeven)
- `ABS_FLOOR = $1.25` (absolute physical minimum, below historical min $1.49)
- `supply_speed` estimated from AR(1) on log-prices conditional on HH < $2.50
  - Result: φ=0.6145, speed=0.4869/month, half-life=1.4 months (n=65 months)
  - Enforced minimum: supply_speed ≥ speed_normal

**Simulation loop change:**
```python
# Before (hard floor):
ln_price = max(ln_price, np.log(FLOOR))  # reflecting floor at $1.50

# After (soft floor):
if ln_price < ln_breakeven:
    supply_pull = supply_speed * (ln_breakeven - ln_price)
    ln_price += supply_pull
ln_price = max(ln_price, np.log(ABS_FLOOR))  # absolute physical minimum
```

**MC output additions:**
- `supply_floor` dict: breakeven, supply_speed, abs_floor, pct_below_breakeven
- `conditional_means` dict: normal and ng_crisis conditional gas prices per year
- Annual forecast now includes `normal_conditional_mean` and `crisis_conditional_mean`

**Diagnostics:**
- Prints below-breakeven path-month percentage
- Prints gas conditional means table

**Results:**
- Supply-response floor activated in 20.6% of path-months
- P10 2026: $1.97 (up from $1.86 with old hard floor)
- Gas conditional means: normal $3.53–$5.41, crisis $10.05–$12.23

---

## Change 11: Decomposed Risk-Adjusted Output (step11)

**Status:** Complete
**Date:** 2026-02-14
**Lines:** ~7879–7900 (elec-crisis model reading), ~8184–8235 (ERCOT projection), ~8410–8430 (diagnostics), ~8527–8540 (risk_decomposition in JSON)

**What was added:**
- Reads H3 scarcity scaling model to compute expected elec-crisis electricity price
- `p_elec_monthly` = fraction of historical months with scarcity_pct > 0.05
- ERCOT projection now uses full 3-regime decomposition:
  - `e_mean = p_normal × e_normal + p_ng × e_ng_crisis + p_elec × e_elec_crisis`

**New output fields per year in ERCOT projections:**
| Field | Description |
|-------|-------------|
| `mean_normal` | Expected price given normal regime |
| `mean_ng_crisis` | Expected price given NG crisis (= normal + crisis_shift) |
| `mean_elec_crisis` | Expected price given elec crisis (from H3 scarcity model) |
| `p_normal` | Probability of normal regime (= 1 − p_ng − p_elec) |
| `p_ng_crisis` | Probability of NG crisis (from MC regime fractions) |
| `p_elec_crisis` | Probability of elec crisis (from historical scarcity frequency) |
| `crisis_premium_ng` | p_ng × (mean_ng − mean_normal) |
| `crisis_premium_elec` | p_elec × (mean_elec − mean_normal) |
| `crisis_premium_total` | Sum of NG and elec premiums |

**`risk_decomposition` section added to projection map JSON:**
- Methodology description
- Use case guidance (hedger, infrastructure investor, risk manager)

**Verification identity:**
- `mean ≈ p_normal × mean_normal + p_ng × mean_ng + p_elec × mean_elec`
- Checked for all years → all PASS (diff < $0.1)

**Diagnostics:**
- ERCOT Price Decomposition table printed during pipeline run
- Step14 risk decomposition check validates identity from saved JSON

**Results (2028 example):**
- mean=$53.2, mean_normal=$48.8, mean_ng=$50.8, mean_elec=$103.9
- p_ng=15.2%, p_elec=7.4%, p_normal=77.4%
- crisis_premium_ng=$0.32, crisis_premium_elec=$4.10, total=$4.42

---

## Known Issues / Open Items

1. **P10 still below $2.00 production floor** (2027–2030: $1.51–$1.57). The soft floor improves P10 for 2026 ($1.97) but multi-month negative events (recession, supply surplus) can still push annual averages below breakeven.

2. **p_elec_monthly = 7.4%** is higher than the 1.8% originally planned. The scarcity_pct > 0.05 threshold captures ~25 months (not just the 6 named extreme events). This includes moderate scarcity months. The decomposition identity is correct but the elec-crisis premium may be overstated relative to "extreme crisis only" interpretation. Consider tightening threshold if desired.

3. **Gas price means still HIGH for 2029–2030** (mean $6.22–$6.48 vs consensus $3.70–$3.85). This is driven by the regime-switching MC producing fat right tails (ng_crisis conditional mean ~$12). The P50 ($4.85–$5.13) is closer to consensus.

4. **ERCOT projected prices HIGH for 2028+** ($53–$73/MWh vs typical range $20–$50). Driven by gas price assumptions and data center demand growth. The normal-regime price ($48–$70) is the dominant component.

5. **Expanding volatility term structure** (implied vol 49%→77%). Expected behavior with regime-switching (fat tails widen with projection horizon). GARCH unconditional vol is 66%.

---

## Files Modified

| File | Changes |
|------|---------|
| `decarbiq_policy_regulation_update_pipeline.py` | All 11 changes |
| `literature_benchmarks.json` | Added 3 benchmark categories; reverted TX battery to EIA/BNEF |

## Files Read (existing, no new files created)

| File | Used By |
|------|---------|
| `geopolitical_and_macro/geopolitical_macro_disruptions.csv` | Change 1 |
| `weather_climate/major_grid_stress_events.csv` | Change 1 |
| `weather_climate/drought_wildfire_grid_impacts.csv` | Change 1 |
| `generation_mix_and_capacity/reservoir_levels_database.csv` | Change 1 |
| `fuel_costs_and_supply/decarbiq_monthly_gas_prices.csv` | Changes 1, 5 |
| `master_regression_dataset_v8.csv` | All changes |
| `fuel_costs_and_supply/hh_shock_catalog.json` | Change 4 (event frequencies) |

## Output Files

| File | Contents |
|------|----------|
| `monte_carlo_lng_v8.json` | MC results with supply_floor, conditional_means, regime_params |
| `decarbiq_projection_map.json` | Projections with risk_decomposition, decomposed ERCOT output |
| `validation_expanded.json` | All validation checks including production floor and risk decomposition |
| `decarbiq_sensitivity_analysis.json` | Sensitivity analysis with data-estimated regimes |
