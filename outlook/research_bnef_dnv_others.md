# BNEF, DNV, S&P, Lazard, McKinsey, DOE — Data Structure Reference

## 1. BloombergNEF (BNEF) New Energy Outlook

### Scenarios
| Scenario | Description |
|---|---|
| **Green** | Max renewables + electrification; limited fossil/nuclear |
| **Gray** | Slower transition; more gas bridging; moderate CCS |
| **Red** | Nuclear-heavy pathway |
| **Net Zero** | 1.5°C aligned |

### Key Variable Categories
- **Power**: generation mix, capacity by tech, annual additions, retirement schedules
- **Investment**: clean energy investment flows by sector/region (BNEF's flagship metric)
- **EVs/Batteries**: EV sales/stock by type, battery pack cost ($/kWh) — strongest coverage of any source
- **Battery value chain**: cell chemistry mix, manufacturing capacity by region, raw material prices
- **Hydrogen**: production by method, electrolyzer deployment, cost curves
- **Technology costs**: solar, wind, battery LCOE/LCOS with proprietary learning curves
- **Carbon markets**: EU ETS, voluntary carbon market pricing
- **Commodity prices**: oil, gas, coal, power (with forward curves)

### Unique Strengths
- Best-in-class EV/battery data and forecasts
- Real-time clean energy investment tracking
- Granular technology cost curves with quarterly updates
- Battery value chain analysis (lithium, cobalt, nickel pricing)

### Access: Subscription required (BNEF Terminal). No public API.
### Geography: Global, ~30+ countries, major regions
### Time: Annual through 2050
### Dollar year: ~2023$

---

## 2. DNV Energy Transition Outlook (ETO)

### Scenario
| Scenario | Description |
|---|---|
| **Pathway** | Single "most likely" probabilistic best estimate (~2.2°C) |

Unique: DNV publishes ONE pathway (not multiple scenarios), representing their probability-weighted most-likely outcome.

### Key Variable Categories
- **Energy demand** by source: oil, gas, coal, nuclear, hydro, solar PV, wind, biomass, geothermal, hydrogen
- **Electricity mix**: generation by source, capacity
- **Hydrogen**: production by method, demand by sector
- **CCUS deployment**: capture capacity, storage
- **Transport**: electrification rates (road, maritime, aviation) — **strongest maritime coverage**
- **Regional energy mix**: 10 world regions
- **Emissions**: CO2 by sector

### 10 DNV World Regions
1. North America
2. Latin America
3. Europe
4. Sub-Saharan Africa
5. Middle East & North Africa
6. North East Eurasia
7. Greater China
8. Indian Subcontinent
9. South East Asia
10. OECD Pacific

### Unique Strengths
- Probabilistic single-pathway framing (not conditional scenarios)
- Maritime and shipping decarbonization (DNV's core domain)
- Annual interactive data tool + downloadable dataset
- Free access to most data

### Access: Free interactive tool + PDF report. Downloadable dataset.
### Time: Annual through 2050
### Dollar year: ~2022$

---

## 3. S&P Global / Platts Energy Scenarios

### Scenarios
| Scenario | Description |
|---|---|
| **Base Case** | Current trajectory |
| **Accelerated Energy Transition** | Faster clean deployment |
| **Delayed Transition** | Policy/investment shortfall |

### Key Variable Categories
- **Commodity prices**: Oil (Brent, WTI, Dubai), gas (HH, TTF, JKM), coal, power — **most granular pricing**
- **Power markets**: sub-national wholesale prices, generation by fuel, capacity additions
- **Refining**: throughput, margins, capacity utilization
- **LNG**: trade flows, contract pricing, liquefaction capacity
- **Petrochemicals**: feedstock demand, naphtha vs ethane
- **Upstream**: production by basin, breakeven costs

### Unique Strengths
- **Sub-national granularity** for commodity prices (hub-level, basin-level)
- Forward curves and market pricing (not just model projections)
- Most detailed refining and petrochemical coverage
- Quarterly updates with market-driven revisions

### Access: Subscription (Platts Analytics, S&P Global Commodity Insights)
### Geography: Global, sub-national for US/EU markets
### Time: Annual/monthly through 2050

---

## 4. Lazard LCOE / LCOS

### Scenarios (per technology)
| Case | Description |
|---|---|
| **Unsubsidized Low** | Best-case unsubsidized LCOE |
| **Unsubsidized High** | Worst-case unsubsidized LCOE |
| **Subsidized Low** | With ITC/PTC, best case |
| **Subsidized High** | With ITC/PTC, worst case |

### Technologies Covered (LCOE)
- Solar PV (utility rooftop, community, residential)
- Wind (onshore, offshore)
- Gas peaking, gas CC
- Coal
- Nuclear
- Geothermal
- Battery + solar hybrid

### Technologies Covered (LCOS)
- Li-ion (utility, C&I, residential)
- Flow batteries (vanadium redox)
- Pumped hydro
- Compressed air
- Green hydrogen (as storage medium)

### Key Metrics
- LCOE ($/MWh) — low/mid/high range
- LCOS ($/MWh) — low/mid/high range
- Key assumptions: CAPEX, capacity factor, fuel cost, economic life, tax equity
- Historical LCOE trends (10-year time series showing cost declines)

### Unique Strengths
- **Industry-standard** LCOE/LCOS reference widely cited in investment decisions
- Clear low/mid/high range presentation
- Year-over-year cost decline tracking
- Simple, standardized methodology for cross-technology comparison

### Access: Free PDF download (annual, ~October-November)
### Geography: Primarily U.S.
### Time: Current year snapshot (no projections, but historical series)

---

## 5. McKinsey Global Energy Perspective

### Scenarios
| Scenario | Description |
|---|---|
| **Reference** | Current trajectory / business as usual |
| **Achieved Commitments** | All NDCs met |
| **Accelerated Transition** | 1.5°C aligned |
| **Further Acceleration** | Beyond current 1.5°C ambition |

### Key Variable Categories
- **Energy demand** by source and sector
- **Industrial decarbonization**: steel, cement, chemicals, aluminum — **most detailed industrial pathways**
- **Hydrogen**: production costs by pathway and region, demand by sector
- **Marginal abatement cost curves** (MACCs) — unique to McKinsey
- **Technology cost trajectories**: with McKinsey's technology adoption S-curves
- **Power sector**: generation mix, investment
- **Transport**: fleet turnover, EV adoption curves

### Unique Strengths
- **Industrial decarbonization pathways** (hard-to-abate sectors)
- Marginal abatement cost analysis ($/tonne CO2 avoided by measure)
- Technology adoption S-curve modeling
- Sector-specific deep dives (hydrogen, steel, cement)

### Access: Reports (some free, some gated behind McKinsey.com registration)
### Geography: Global, major regions
### Time: Snapshots through 2050

---

## 6. DOE Hydrogen Program / H2A / Hydrogen Shot

### Frameworks
| Program | Description |
|---|---|
| **Hydrogen Shot** | $1/kg clean H2 by 2031 target |
| **H2A Model** | Bottom-up production cost analysis tool |
| **Pathways Studies** | Multi-pathway deployment scenarios |
| **Clean Hydrogen Strategy** | National roadmap |

### Production Pathways Modeled
| Pathway | Key Cost Components |
|---|---|
| **PEM Electrolysis** | Electrolyzer CAPEX, stack replacement, electricity, water, BOP |
| **Alkaline Electrolysis** | Similar to PEM, lower CAPEX but larger footprint |
| **SOEC** | High-temp electrolyzer, heat integration, stack degradation |
| **SMR (gray)** | NG feedstock (~75%), CAPEX, O&M, water |
| **SMR + CCS (blue)** | NG + capture equipment + CO2 transport/storage |
| **ATR + CCS** | Alternative reforming, higher capture rates |
| **Biomass gasification** | Feedstock cost, gasifier CAPEX |
| **Nuclear thermochemical** | Nuclear heat + electrochemical splitting |

### H2A Model Cost Components
For each pathway:
- CAPEX ($/kW or $/kg-day capacity)
- Fixed O&M ($/yr)
- Variable O&M ($/kg)
- Feedstock/fuel cost ($/MMBtu or $/kWh)
- Electricity cost ($/kWh)
- Water cost ($/gal)
- CO2 T&S cost ($/tonne) — for CCS pathways
- Capacity factor (%)
- Plant lifetime (years)
- IRR assumption (%)
- Results in **LCOH ($/kg H2)**

### DOE Hydrogen Shot Targets
| Metric | 2021 | 2026 Target | 2031 Target |
|---|---|---|---|
| Clean H2 cost | ~$5/kg (green) | $2/kg | **$1/kg** |
| Electrolyzer CAPEX | ~$1,400/kW | $700/kW | $300/kW |
| Stack efficiency | ~65% LHV | 68% | 73% |

### Section 45V Clean Hydrogen PTC
| CI Threshold (kg CO2e/kg H2) | Credit ($/kg H2) |
|---|---|
| 0 – 0.45 | $3.00 |
| 0.45 – 1.5 | $1.00 |
| 1.5 – 2.5 | $0.75 |
| 2.5 – 4.0 | $0.60 |

### Unique Strengths
- **Most detailed bottom-up H2 production cost engineering**
- H2A model is publicly available and customizable
- Explicit policy incentive modeling (45V, 45Q)
- Infrastructure requirements (pipeline, storage, fueling stations)
- Lifecycle emissions analysis by pathway

### Access: Free reports + H2A model download (DOE EERE website)
### Geography: U.S. national + regional
### Time: Current + targets through 2030-2050

---

## Summary: Unique Coverage by Source

| Source | Unique Strength |
|---|---|
| **BNEF** | Clean energy investment flows, EV/battery value chains |
| **DNV** | Single probabilistic pathway, maritime decarbonization |
| **S&P/Platts** | Sub-national commodity pricing, forward curves |
| **Lazard** | Standardized LCOE/LCOS benchmarks, industry-standard reference |
| **McKinsey** | Industrial decarbonization pathways, marginal abatement cost curves |
| **DOE** | Bottom-up hydrogen production cost engineering, US policy incentives |
