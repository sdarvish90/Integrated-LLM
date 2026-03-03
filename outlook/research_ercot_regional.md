# ERCOT, EPA, Hydrogen, Carbon, Regional — Data Structure Reference

## 1. ERCOT Planning Reports

### Capacity, Demand, and Reserves (CDR) Report
Published semi-annually. 10-year forward look.

| Variable | Units | Granularity |
|---|---|---|
| Peak demand forecast | MW | System, weather zone |
| Energy demand forecast | MWh | System |
| Installed generation capacity | MW | By fuel type, resource category |
| Planned capacity additions | MW | By fuel, in-service date |
| Planned retirements | MW | By fuel, retirement date |
| Reserve margin | % | System |
| Effective load-carrying capability | MW | By resource type (esp. wind, solar) |

### Load Forecast
- Peak demand: summer and winter peaks
- Energy: annual TWh
- By weather zone (Coast, East, Far West, North, North Central, South, South Central, West)
- By load zone (Houston, North, South, West)
- Demand growth drivers: population, economic activity, data center load, electrification

### Generation Interconnection Queue
- Projects by fuel type: solar, wind, battery, gas, hybrid
- Capacity (MW) by status: active, under study, approved, withdrawn
- Expected in-service dates
- Location (county, weather zone)

### Wholesale Prices
| Variable | Units |
|---|---|
| System-wide average price (SPP) | $/MWh |
| Zonal prices | $/MWh (by load zone) |
| Hub prices (Houston, North, South, West) | $/MWh |
| Real-time market prices | $/MWh (5-min, 15-min, hourly) |
| Day-ahead market prices | $/MWh |
| Ancillary services prices | $/MW (Reg Up, Reg Down, RRS, ECRS) |

### Access
- CDR reports: Excel downloads from ERCOT planning portal
- Load forecasts: Excel from ERCOT
- Price data: ERCOT Market Information System (MIS), some via API
- Queue: Excel download, updated monthly

---

## 2. EPA Integrated Planning Model (IPM)

### Overview
EPA uses ICF's IPM model for power sector regulatory analysis. Published with major rulemakings.

### Key Variables
| Variable | Units | Granularity |
|---|---|---|
| Generation by fuel | GWh | IPM region (~64 regions) |
| Capacity by fuel | MW | IPM region |
| Capacity additions/retirements | MW | By tech, region |
| Wholesale electricity prices | $/MWh | IPM region |
| CO2 emissions | tons | Region, plant-level |
| SO2 emissions | tons | Region |
| NOx emissions | tons | Region |
| Mercury emissions | lbs | Region |
| Compliance costs | $ billion | National |
| Coal plant economics | $/MWh (going-forward cost) | Plant-level |

### Scenarios
- **Baseline**: current regulations in effect
- **Policy case**: proposed regulation impact (e.g., Clean Power Plan 2.0, Good Neighbor Rule)
- **Sensitivity runs**: varying gas prices, demand, renewable costs

### Access: EPA publications + IPM documentation at epa.gov/power-sector-modeling
### Geography: ~64 IPM model regions covering contiguous US

---

## 3. Hydrogen Data Sources (beyond DOE)

### Hydrogen Council
- Global hydrogen demand projections by sector
- Investment needed estimates
- Regional deployment pathways
- Cost trajectories by production method

### IRENA Hydrogen
| Variable | Units |
|---|---|
| Green H2 production cost | $/kg (by region, renewable source) |
| Electrolyzer CAPEX learning curves | $/kW through 2050 |
| H2 trade potential | Mt H2 by route |
| Infrastructure investment needs | $ billion |

### Key Hydrogen Cost Components (for Monte Carlo)
| Component | Variable | Typical Range |
|---|---|---|
| Electrolyzer CAPEX | $/kW | $300-$1,400 (declining) |
| Electricity cost | $/MWh | $20-$80 (dominant for green) |
| NG feedstock | $/MMBtu | $2-$8 (dominant for blue/gray) |
| Capacity factor | % | 30-90% (grid vs. dedicated RE) |
| Water cost | $/m³ | $0.5-$3 |
| CO2 T&S cost | $/tonne | $10-$30 (for CCS pathways) |
| Stack replacement | $/kW | 30-50% of initial CAPEX at mid-life |

---

## 4. Carbon Pricing Data

### Compliance Markets
| Market | Current Range | Projections Available From |
|---|---|---|
| EU ETS | €50-100/tonne | S&P, BNEF, IEA |
| California Cap-and-Trade | $30-40/tonne | ARB, S&P |
| RGGI | $13-15/tonne | RGGI Inc. |
| China ETS | ~$10/tonne | IEA, BNEF |

### Social Cost of Carbon (US)
| Source | 2025 Estimate | 2050 Estimate |
|---|---|---|
| EPA (2023 update) | ~$190/tonne (2020$) | ~$340/tonne |
| OMB (Biden admin) | ~$51/tonne (2020$) | Higher |

### IEA Carbon Price Assumptions
| Scenario | Advanced Economies 2030 | Advanced Economies 2050 |
|---|---|---|
| STEPS | $25-75 | $25-100 |
| APS | $100-140 | $140-200 |
| NZE | $130-175 | $200-250+ |

### Voluntary Carbon Markets
- Offset prices: $5-$50/tonne (high variance by quality)
- Sources: BNEF, S&P, Ecosystem Marketplace

---

## 5. Gulf Coast / Texas Regional Variables

### Critical for hydrogen/decarbonization project economics:

| Variable | Source | Units |
|---|---|---|
| Industrial NG price (Gulf Coast) | EIA AEO (Census Div 7: WSC) | $/MMBtu |
| Industrial electricity price | EIA AEO (Census Div 7) | cents/kWh |
| ERCOT wholesale price | ERCOT MIS | $/MWh |
| Industrial water cost | TWDB, local utilities | $/1000 gal ($1-4 typical) |
| Labor cost index | BLS, ENR Construction Cost Index | index |
| Pipeline tariffs (NG) | FERC filings | $/MMBtu |
| CO2 pipeline tariffs | Limited data; DOE estimates | $/tonne/100mi |
| Port/terminal capacity | Port of Houston, Corpus Christi | Mt/yr, Bcf/d |
| Land costs (industrial) | CoStar, local data | $/acre |
| Property tax rates | County appraisal districts | % of assessed value |
| State/local incentives | TX Comptroller, TX Enterprise Fund | Various |

### Gulf Coast Hydrogen Hub (HyVelocity)
- DOE-selected Regional Clean H2 Hub
- $1.2B federal funding
- Production targets: 800,000+ tonnes/yr clean H2
- Key anchor demand: refining, ammonia, petrochemicals
- Infrastructure: pipelines, storage (salt caverns)

### Key Texas Energy Context
- ERCOT is ~90% of Texas load, isolated grid (limited interconnections)
- Largest wind capacity in US (~40 GW)
- Fastest-growing solar market
- Massive battery deployment underway
- ~150 GW interconnection queue (mostly solar + battery)
- Wholesale prices: volatile, negative pricing during high wind/solar, scarcity pricing during extremes
- No capacity market — energy-only market design
- Industrial electricity: among lowest in US (~5-7 cents/kWh)
- Industrial NG: Henry Hub + small basis differential
- Salt dome geology ideal for H2 storage (Clemens Dome, etc.)

---

## 6. Summary: Data Access Priority for DecarbIQ

### Start Here (API/structured, free):
1. **EIA AEO** — api.eia.gov (101 variables, all scenarios, annual)
2. **NREL ATB** — developer.nrel.gov (40 tech variables, 3 scenarios, annual)
3. **ERCOT** — CDR + load forecasts (Excel downloads)

### Next (structured but may need subscription):
4. **IEA WEO** — Free data annex Excel (102 variables, snapshots)
5. **Lazard** — Free PDF, clean tables (16 tech cost variables)
6. **DNV ETO** — Free interactive tool + download

### Requires subscription:
7. **BNEF** — Terminal subscription
8. **S&P Global** — Platts Analytics subscription
9. **McKinsey** — Reports (some free)

### Manual/PDF extraction:
10. **DOE Hydrogen** — Reports + H2A model
11. **EPA IPM** — Published with rulemakings
