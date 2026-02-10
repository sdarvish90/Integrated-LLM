# DecarbIQ Node Inventory
## Complete registry of all nodes considered for the computational graph
### Last updated: 2026-02-10
### Policy current as of: OBBBA (July 4, 2025)

---

## CURRENT NODES (in decarbiq_graph.py)

### Tier 1: Upstream -> Henry Hub (45-variable Ridge model, R²=0.76)

#### COST_SUPPLY (group R²=0.49, +0.33 incremental R²)

| node_id | label | unit | type | full_model_coeff | status |
|---------|-------|------|------|-----------------|--------|
| gas_rigs | Gas Rig Count | count | upstream_supply | -0.008 | ACTIVE |
| oil_rigs | Oil Rig Count | count | upstream_supply | +0.008 | ACTIVE |
| lng_exports_bcfd | LNG Exports | Bcf/d | upstream_demand | +0.119 | ACTIVE |
| net_exports_bcfd | Net Gas Exports | Bcf/d | upstream_supply | +0.324 | ACTIVE |
| shale_era | Shale Production Surge (2009+) | binary | structural_shifter | -1.304 | ACTIVE |
| us_net_exporter | US Net Gas Exporter (2017+) | binary | structural_shifter | -0.036 | ACTIVE |
| post_lng_exports | LNG Exports Begin (2016+) | binary | structural_shifter | +0.773 | ACTIVE |
| post_lng_pause | DOE LNG Pause (2024+) | binary | structural_shifter | -0.503 | ACTIVE |

#### DEMAND_DRIVERS (group R²=0.32, first in incremental chain)

| node_id | label | unit | type | full_model_coeff | status |
|---------|-------|------|------|-----------------|--------|
| electric_power_bcfd | Power Sector Gas Demand | Bcf/d | upstream_demand | +0.075 | ACTIVE |
| residential_bcfd | Residential Gas Demand | Bcf/d | upstream_demand | +0.088 | ACTIVE |
| commercial_bcfd | Commercial Gas Demand | Bcf/d | upstream_demand | -0.058 | ACTIVE |
| industrial_bcfd | Industrial Gas Demand | Bcf/d | upstream_demand | -0.235 | ACTIVE |
| power_share_of_gas | Power Sector Share of Gas | % | derived | +0.084 | ACTIVE |
| heating_demand | Heating Demand (Res+Comm) | Bcf/d | derived | +0.030 | ACTIVE |
| base_demand | Base Demand (Ind+Power) | Bcf/d | derived | -0.160 | ACTIVE |
| is_summer | Summer Indicator (Jun-Aug) | binary | seasonal | -0.248 | ACTIVE |
| is_winter | Winter Indicator (Dec-Feb) | binary | seasonal | -0.139 | ACTIVE |

#### GENERATION_MIX (group R²=0.46, +0.06 incremental R²)

| node_id | label | unit | type | full_model_coeff | status |
|---------|-------|------|------|-----------------|--------|
| gas_share_pct | Gas Generation Share | % | moderator | -0.112 | ACTIVE |
| coal_share_pct | Coal Generation Share | % | moderator | +0.124 | ACTIVE |
| nuclear_share_pct | Nuclear Generation Share | % | moderator | -0.178 | ACTIVE |
| renewable_share_pct | Renewable Generation Share | % | moderator | +0.080 | ACTIVE |
| gas_coal_ratio | Gas-to-Coal Ratio | ratio | derived | -0.450 | ACTIVE |
| clean_share | Clean Energy Share (Nuc+Ren) | % | derived | -0.098 | ACTIVE |
| post_coal_decline | Post-Coal Decline (2010+) | binary | structural_shifter | -0.028 | ACTIVE |
| post_major_coal_ret | Post-MATS Coal Retirements (2015+) | binary | structural_shifter | +0.630 | ACTIVE |

#### PERMITTING (group R²=0.46, +0.05 incremental R²)

| node_id | label | unit | type | full_model_coeff | start_date | status |
|---------|-------|------|------|-----------------|------------|--------|
| post_epact2005_permitting | EPAct 2005 FERC Lead Agency | binary | structural_shifter | +0.495 | 2005-08-08 | ACTIVE |
| post_fra2023 | Fiscal Responsibility Act 2yr EIS | binary | structural_shifter | -0.429 | 2023-06-03 | ACTIVE |
| post_certificate_policy | FERC Certificate Policy Statement | binary | structural_shifter | +1.520 | 1999-09-15 | ACTIVE |
| post_mvp_approval | MVP Congressional Approval | binary | structural_shifter | -0.429 | 2023-06-03 | ACTIVE |
| post_order2003 | FERC Order 2003 Interconnection | binary | structural_shifter | +1.130 | 2003-07-24 | ACTIVE |
| post_order2023 | FERC Order 2023 Queue Reform | binary | structural_shifter | +0.176 | 2023-07-28 | ACTIVE |
| post_order1000 | FERC Order 1000 Transmission Planning | binary | structural_shifter | -0.296 | 2011-07-21 | ACTIVE |
| post_order1920 | FERC Order 1920 Transmission Reform | binary | structural_shifter | +0.213 | 2024-05-13 | ACTIVE |
| post_401_battles | NY 401 Constitution Pipeline Denial | binary | structural_shifter | -0.554 | 2016-04-22 | ACTIVE |

#### POLICY_REGULATORY (group R²=0.47, +0.005 incremental R²)

| node_id | label | unit | type | full_model_coeff | start_date | status |
|---------|-------|------|------|-----------------|------------|--------|
| post_epact2005 | EPAct 2005 Fracking Exemption | binary | structural_shifter | +0.495 | 2005-08-08 | ACTIVE |
| post_mats | MATS Mercury Rule | binary | structural_shifter | +0.630 | 2015-04-16 | ACTIVE |
| post_rggi | RGGI Cap-and-Trade | binary | structural_shifter | -1.304 | 2009-01-01 | ACTIVE |
| post_ca_cap | CA Cap-and-Trade | binary | structural_shifter | +0.726 | 2013-01-01 | ACTIVE |
| post_ira | Inflation Reduction Act | binary | structural_shifter | -0.951 | 2022-08-16 | ACTIVE (SUPERSEDED by OBBBA) |
| post_obbba | One Big Beautiful Bill Act | binary | structural_shifter | +1.200 | 2025-07-04 | ACTIVE |

**OBBBA Note**: Signed July 4, 2025. Repeals/modifies most IRA clean energy provisions. Maintains nuclear credits (+10% bonus), 45Q (CCS), and 45Z (clean fuel to 2029). Creates Energy Dominance Financing Program ($1B). Postpones methane fees 10 years. Net effect with IRA: -0.951 + 1.200 = +0.249 $/MMBtu (pro-fossil net shift).

#### GENERATION CAPACITY DYNAMICS (qualitative — no regression coefficients yet)

| node_id | label | unit | type | direction | data_source | status |
|---------|-------|------|------|-----------|-------------|--------|
| gas_capacity_additions_gw | Gas Plant Capacity Additions | GW | upstream_demand | positive on gas price | EIA Form 860 | ACTIVE (no coeff) |
| coal_retirements_gw | Coal Plant Retirements | GW | upstream_demand | positive on gas price | EIA Form 860 | ACTIVE (no coeff) |
| gas_capacity_factor_pct | Gas Capacity Factor | % | upstream_demand | positive on gas price | EIA Form 860 | ACTIVE (no coeff) |

#### OIL MARKET

| node_id | label | unit | type | coeff | R² | status |
|---------|-------|------|------|-------|-----|--------|
| oil_price_wti | WTI Crude Oil Price | $/bbl | upstream_supply | +0.059 | 0.376 | ACTIVE |

#### STORAGE

| node_id | label | unit | type | status |
|---------|-------|------|------|--------|
| storage_zscore | US Storage Z-Score | z-score | upstream_storage | ACTIVE |

#### DISRUPTION: Weather Events (from geopolitical_regression_results.json, R²=0.315)

| node_id | label | unit | type | coeff ($/MMBtu) | events | status |
|---------|-------|------|------|----------------|--------|--------|
| hurricane_active | Hurricane Active (Gulf Coast) | binary | disruption_weather | -0.276 | 15 (1998-2021) | ACTIVE |
| hurricane_major | Major Hurricane (Cat 3+) | binary | disruption_weather | **+5.652** | 8 (2005-2008) | ACTIVE (data gap: missing Laura 2020, Ida 2021) |
| polar_vortex_event | Polar Vortex Event | binary | disruption_weather | 0.0 | 0 | ACTIVE (DATA GAP: no events coded — needs Uri 2021, Vortex 2014, Elliott 2022, Heather 2024) |

#### DISRUPTION: Geopolitical Events

| node_id | label | unit | type | coeff ($/MMBtu) | events | status |
|---------|-------|------|------|----------------|--------|--------|
| war_active | Active War (Major Oil Region) | binary | disruption_geopolitical | -0.224 | 121 mo (2003-2024) | ACTIVE |
| russia_gas_dispute | Russia Gas Dispute | binary | disruption_geopolitical | -0.277 | 2 (2006, 2009) | ACTIVE |
| iran_sanctions_active | Iran Sanctions Active | binary | disruption_geopolitical | -0.200 | 128 mo (2012-2024) | ACTIVE |
| venezuela_sanctions_active | Venezuela Sanctions Active | binary | disruption_geopolitical | -0.076 | 88 mo (2017-2024) | ACTIVE |
| russia_sanctions_active | Russia Sanctions Active | binary | disruption_geopolitical | **-1.517** | 129 mo (2014-2024) | ACTIVE |

#### DISRUPTION: Market Conditions

| node_id | label | unit | type | coeff ($/MMBtu) | events | status |
|---------|-------|------|------|----------------|--------|--------|
| lng_tight_market | LNG Tight Market | binary | disruption_market | **+1.452** | 48 mo (2017-2022) | ACTIVE |

#### POLICY: Temporal Window

| node_id | label | unit | type | status |
|---------|-------|------|------|--------|
| ira_era | IRA Active Era (2022-09 to OBBBA) | binary | structural_shifter | ACTIVE (no coeff — temporal window only) |

**Disruption Note**: Coefficients from a standalone geopolitical regression (R²=0.315, n=336, intercept=$4.55/MMBtu). These are additive to the 45-variable Ridge model. Key impacts: hurricane_major (+$5.65) is the single largest event shock; russia_sanctions (-$1.52) reflects post-2022 US oversupply response; lng_tight_market (+$1.45) captures global LNG scarcity premium.

### Tier 2: Central + Downstream

| node_id | label | unit | type | base_value | R² | status |
|---------|-------|------|------|-----------|-----|--------|
| henry_hub | Henry Hub Gas Price | $/MMBtu | gas_price | 0.0 (computed) | 0.761 (full model) | ACTIVE |
| ercot_wholesale | ERCOT Wholesale Electricity | $/MWh | elec_wholesale | 8.004 | 0.653 | ACTIVE (ERCOT graph) |
| tx_retail | TX Industrial Retail Electricity | cents/kWh | elec_retail | 8.386 | 0.298 | ACTIVE (ERCOT graph) |
| caiso_wholesale | CAISO Wholesale Electricity | $/MWh | elec_wholesale | -32.874 | 0.441 | ACTIVE (CAISO graph) |

### Additional (CAISO only)

| node_id | label | unit | type | status |
|---------|-------|------|------|--------|
| total_rigs | Total Rig Count | count | upstream_supply | ACTIVE (CAISO only) |

**TOTAL ACTIVE NODES: 58 per graph** (ERCOT: 58 nodes / 71 edges, CAISO: 58 nodes / 61 edges)

---

## VOLATILITY NODES (candidates from volatility database)

### Volatility Thresholds (from volatility_thresholds_config.csv)

These define when "normal" transitions to "stressed" market conditions. Could be implemented as threshold-based state nodes.

| node_id (proposed) | label | market | normal_range | elevated_threshold | extreme_threshold | unit |
|---------------------|-------|--------|-------------|-------------------|-------------------|------|
| ercot_vol_state | ERCOT Volatility State | ERCOT wholesale | $15-60 | $100 | $200 | $/MWh |
| caiso_vol_state | CAISO Volatility State | CAISO wholesale | $20-80 | $100 | $150 | $/MWh |
| hh_vol_state | Henry Hub Volatility State | Henry Hub | $2-5 | $6 | $8 | $/MMBtu |
| retail_vol_state | Industrial Retail Vol State | Industrial retail | 5-8 | 8.5 | 9 | cents/kWh |

**How they fit**: State nodes that trigger when computed prices cross thresholds. They don't feed back into price computation but classify the output regime (NORMAL / ELEVATED / EXTREME).

### Event Cause Nodes (from volatility_event_narratives.csv)

Root-cause categories that drive volatility events. Binary triggers that can activate scarcity pricing.

| node_id (proposed) | label | type | historical_events | avg_price_impact |
|---------------------|-------|------|-------------------|-----------------|
| weather_cold_snap | Cold Snap / Polar Vortex | event_trigger | Winter Storm Uri (2021-02), Elliott (2022-12) | ERCOT +6636%, CAISO +268% |
| weather_heat_dome | Extreme Heat Dome | event_trigger | TX Heat (2023-08) | ERCOT +410% |
| geopolitical_supply_shock | Geopolitical Supply Disruption | event_trigger | Russia-Ukraine (2022-04 to 2022-09) | HH +51% sustained 6 months |

**How they fit**: Binary nodes (0/1) that, when active, add scarcity premium to electricity prices. The premium magnitude comes from the scarcity event database.

### Parameter Volatility Nodes (from volatility_parameter_summary.csv)

Rolling volatility metrics for key upstream parameters. These measure how volatile an input is, not its level.

| node_id (proposed) | label | unit | total_events | spike_events | crash_events | avg_recovery_months |
|---------------------|-------|------|-------------|-------------|-------------|-------------------|
| vol_electric_power | Power Sector Demand Volatility | events/year | 145 | 72 spikes | 73 crashes | 2.86 |
| vol_residential | Residential Demand Volatility | events/year | 328 | 119 spikes | 209 crashes | 3.92 |
| vol_total_gas | Total Gas Consumption Volatility | events/year | 90 | 65 spikes | 25 crashes | 2.61 |
| vol_gas_share | Gas Gen Share Volatility | events/year | 35 | 19 spikes | 16 crashes | 2.83 |
| vol_coal_share | Coal Gen Share Volatility | events/year | 11 | 2 spikes | 9 crashes | 2.36 |
| vol_gas_rigs | Gas Rig Count Volatility | events/year | 19 | 0 spikes | 19 crashes | 5.79 |
| vol_total_rigs | Total Rig Count Volatility | events/year | 19 | 0 spikes | 19 crashes | 6.37 |
| vol_net_exports | Net Exports Volatility | events/year | 34 | 21 spikes | 13 crashes | 6.18 |
| vol_lng_exports | LNG Exports Volatility | events/year | 37 | 37 spikes | 0 crashes | 5.95 |

**Key asymmetries**:
- LNG exports: ONLY spikes (never crashes) — unidirectional supply response
- Rig counts: ONLY crashes (never spikes) — supply destruction pattern
- Residential demand: Most volatile (328 events), seasonal extremes dominate

**How they fit**: These become metadata or conditional modifiers on existing causal edges. When a parameter is in a "spike" state, the edge coefficient may amplify. When in "crash" state, the coefficient may dampen. This is the mechanism for regime-dependent sensitivity.

### Cross-Market Transmission Nodes (from volatility_cross_market_impact.csv)

Quantified gas-to-electricity price transmission during volatility events.

| node_id (proposed) | label | type | observation |
|---------------------|-------|------|-------------|
| hh_to_ercot_vol_transmission | Gas->ERCOT Volatility Transmission | transmission_multiplier | HH $8.45 -> ERCOT $80.51 (ratio 9.5x), HH $9.14 -> ERCOT $100.77 (ratio 11x) |
| hh_to_caiso_vol_transmission | Gas->CAISO Volatility Transmission | transmission_multiplier | Weaker: CAISO not impacted during 2022 gas crisis |

**How they fit**: During normal conditions, the gas->electricity coefficient is 7.01 (ERCOT). During geopolitical crises, the effective ratio rises to 9.5-11x. These transmission nodes modulate the henry_hub->ercot_wholesale edge coefficient during stress events.

### Scarcity Premium Nodes (from volatility_events_database.csv)

Direct price additions during extreme events, separate from the normal regression model.

| node_id (proposed) | label | unit | type | events | mechanism |
|---------------------|-------|------|------|--------|-----------|
| ercot_scarcity_premium | ERCOT Scarcity Price Premium | $/MWh | event_adder | Uri: +$1,455, Heat: +$233 | Operating reserve demand curve (ORDC) pricing |
| caiso_scarcity_premium | CAISO Scarcity Price Premium | $/MWh | event_adder | Elliott: +$184 | Pipeline constraints, gas price spikes |
| hh_scarcity_premium | Henry Hub Scarcity Premium | $/MMBtu | event_adder | Russia-Ukraine: +$3-5 | Geopolitical supply disruption |

**How they fit**: Additive nodes. When active (binary trigger from event_cause nodes), they add a scarcity premium on top of the normal regression-computed price. Magnitude sampled from historical event database.

### Recovery Dynamics (from volatility_regional_summary.csv)

Time-to-recovery after events, by market.

| market | avg_recovery_months | max_recovery_months | implication |
|--------|-------------------|-------------------|-------------|
| ERCOT wholesale | 1.25 | 2 | Fast market clearing |
| CAISO wholesale | 1.5 | 2 | Fast market clearing |
| Henry Hub gas | 3.5 | 6 | Slower demand rebalancing |
| Parameter-level | 2-6 | varies | Supply recovery slowest (rigs: 6 mo) |

**How they fit**: Recovery dynamics are temporal — they define how long a scarcity premium persists after the trigger deactivates. Not a static node but a decay function on the scarcity premium.

### Concurrent Parameter Events (from volatility_price_parameter_crossref.csv)

Which parameters spike/crash simultaneously during price events.

| price_event | concurrent_parameter_events |
|-------------|---------------------------|
| ERCOT 2021-02 (Uri) | Coal +33.6%, LNG +37.8%, Net Exports +37.9%, Residential +132.1%, Total Consumption +31.5% |
| ERCOT 2023-08 (Heat) | Power Sector +36.5%, Residential -73.7% |
| CAISO 2022-12 (Elliott) | Residential +96.9% |
| HH 2022-05 (Russia) | Residential -50.3% |
| HH 2022-07 (Russia) | Power Sector +41.8%, Residential -72.7% |

**How they fit**: These define the correlation structure during extreme events. During a weather event, multiple upstream parameters move simultaneously. This is critical for Monte Carlo simulation — you can't sample parameters independently during crises.

---

## SUMMARY: NODE COUNTS

| Category | Current (Active) | Volatility (Candidate) | Total Possible |
|----------|-----------------|----------------------|----------------|
| Upstream Supply | 4 | 0 | 4 |
| Upstream Demand | 5 + 3 qualitative = 8 | 0 | 8 |
| Upstream Storage | 1 | 0 | 1 |
| Seasonal | 2 | 0 | 2 |
| Moderator | 4 | 0 | 4 |
| Structural Shifter | 22 (+ira_era) | 0 | 22 |
| Derived | 5 | 0 | 5 |
| Oil Market | 1 | 0 | 1 |
| **Disruption: Weather** | **3** | 0 | 3 |
| **Disruption: Geopolitical** | **5** | 0 | 5 |
| **Disruption: Market** | **1** | 0 | 1 |
| Gas Price | 1 | 1 (hh_scarcity_premium) | 2 |
| Elec Wholesale | 1-2 | 2 (scarcity premiums) | 3-4 |
| Elec Retail | 0-1 | 0 | 0-1 |
| **Volatility State** | 0 | **4** (threshold classifiers) | 4 |
| **Event Triggers** | 0 | **3** (cold, heat, geopolitical) | 3 |
| **Parameter Volatility** | 0 | **9** (rolling vol per parameter) | 9 |
| **Transmission Multipliers** | 0 | **2** (gas->elec during stress) | 2 |
| **TOTAL** | **58** | **~21** | **~79** |
