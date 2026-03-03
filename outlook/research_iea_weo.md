# IEA World Energy Outlook (WEO) — Data Structure Reference

## 1. Scenarios

| Scenario | Description | Temp Outcome |
|----------|-------------|-------------|
| **STEPS** | Stated Policies — existing laws + implemented measures | ~2.4°C |
| **APS** | Announced Pledges — all NDCs and net-zero pledges met | ~1.7°C |
| **NZE** | Net Zero Emissions by 2050 — 1.5°C backcasting pathway | 1.5°C |

Historical note: STEPS replaced New Policies Scenario (NPS); NZE replaced Sustainable Development Scenario (SDS) circa WEO-2020.

## 2. Data Annex Structure (Excel download, free)

### A. Energy Demand
- **Total Primary Energy (TES)** by fuel: coal, oil, gas, nuclear, hydro, bioenergy, wind, solar PV, CSP, geothermal, hydrogen — Unit: Mtoe or EJ
- **Total Final Consumption (TFC)** by fuel and sector:
  - Sectors: Industry (iron/steel, cement, chemicals, aluminium, pulp/paper), Transport (road, aviation, shipping, rail), Buildings (residential, commercial), Other
  - Fuels: coal, oil, gas, electricity, heat, bioenergy, hydrogen
- Energy intensity: toe/thousand USD PPP

### B. Electricity
- **Generation** by source (TWh): coal ±CCS, gas ±CCS, nuclear, hydro, wind onshore/offshore, solar PV/CSP, bioenergy, geothermal, marine, H2/ammonia, battery dispatch
- **Capacity** by source (GW): same breakdown
- Renewable share (%), capacity factors, T&D losses, electricity trade, prices

### C. CO2 Emissions
- **By fuel**: coal, oil, gas — Mt CO2
- **By sector**: power, industry, transport, buildings, other energy
- **Process emissions**: cement, other industrial
- **Intensity**: per capita, per GDP, per kWh
- **CCUS captured**: by sector (power, industry, fuel transformation, DAC)

### D. Fossil Fuel Supply/Production
- Oil: by type and region (Mb/d)
- Gas: by region (bcm)
- Coal: by region (Mtce)

### E. Fuel Prices
- **Oil**: IEA avg import $/barrel (real)
- **Gas**: Henry Hub $/MBtu, Europe TTF $/MBtu, Japan/Asia LNG $/MBtu
- **Coal**: Japan/Asia import $/tonne, Europe import $/tonne
- **Carbon prices**: Advanced economies, EMDEs ($/t CO2, real) — NZE reaches $200+/t CO2 by 2040-50
- **Hydrogen**: LCOH by method and region ($/kg) — in recent editions

### F. Investment ($ billion, real)
- **Supply**: oil/gas upstream, coal mining, biofuels, hydrogen
- **Power**: renewables (by tech), nuclear, fossil ±CCS, battery storage, grids (T&D)
- **Demand-side**: energy efficiency, EVs, heat pumps, industrial electrification, H2 end-use
- Clean vs fossil total + ratio

### G. Technology Deployment
- **Renewables**: solar/wind annual additions (GW/yr), cumulative (GW), generation (TWh), biofuels (Mb/d)
- **EVs**: stock by type (BEV/PHEV), sales share (%), by region, trucks/buses
- **Heat pumps**: millions of units, sales by region, heating share
- **Hydrogen/electrolyzers**: capacity (GW), production by method (Mt H2), demand by sector
- **CCUS**: capture capacity by sector (Mt CO2/yr)
- **Nuclear**: capacity (GW), generation (TWh)
- **Battery storage**: grid-scale + behind-meter (GW/GWh)

### H. Energy Access
- Population without electricity, without clean cooking (millions, by region)

### Additional (recent editions)
- **Critical minerals**: lithium, cobalt, nickel, copper, rare earths — demand by technology (kt)
- **Methane emissions**: energy-sector by source (Mt CH4)
- **Trade flows**: oil, gas (pipeline + LNG), coal, hydrogen, electricity

## 3. Units

| Quantity | Primary Unit | Alt |
|----------|-------------|-----|
| Primary energy | Mtoe | EJ (×0.04187) |
| Electricity gen | TWh | |
| Capacity | GW | |
| CO2 | Mt CO2 / Gt CO2 | |
| Oil | Mb/d | |
| Gas | bcm | Tcf (×0.03531) |
| Coal | Mtce | |
| Investment | $ billion (real, ~2022$) | |
| Oil price | $/barrel (real) | |
| Gas price | $/MBtu (real) | |
| Carbon price | $/t CO2 (real) | |
| Hydrogen | Mt H2, $/kg | |
| Minerals | kt | |

## 4. Geographic Breakdowns

**Major groupings**: World, Advanced economies, EMDEs

**Regions**: North America, Central/South America, Europe, EU, Africa, Sub-Saharan Africa, Middle East, Eurasia, Asia Pacific, Southeast Asia (ASEAN)

**Key countries**: US, Brazil, Russia, China, India, Japan, Korea, Indonesia, South Africa

**Special**: OPEC, Non-OPEC, IEA members, G7/G20

## 5. Time Horizon

- **Snapshot years**: 2023 (base), 2030, 2035, 2040, 2050
- **NOT annual** — must interpolate for annual series
- Historical benchmarks: 2010, sometimes 2000/2015

## 6. Data Access

- **WEO Data Annex**: Free Excel download from iea.org
- **Extended Dataset**: Paid (more granular)
- **IEA Data Explorer**: iea.org/data-and-statistics (some free, full requires subscription)
- **API**: api.iea.org (registration required) — mainly historical stats, limited WEO projections

## 7. GEC Model Technology Cost Assumptions

### Power Generation
- CAPEX ($/kW) by region, with learning rates:
  - Solar PV: ~20-24% per doubling
  - Wind onshore: ~15-18%
  - Wind offshore: ~10-15%
  - Batteries: ~15-20%
- O&M, efficiency, capacity factors, lifetime, construction time

### End-Use Technologies
- EV battery cost ($/kWh), vehicle premium
- Heat pump COP and cost by type
- Electrolyzer CAPEX ($/kW), efficiency (kWh/kg H2) by type (alkaline, PEM, SOEC)
- Industrial equipment: EAF, H2-DRI, electric kilns

### Fuel Supply
- Upstream breakeven costs by resource type
- LCOH by method and region
- DAC cost per tonne CO2

### Macro Inputs (exogenous)
- GDP: IMF short-term, OECD/WB long-term
- Population: UN medium variant
- Discount rates: vary by region/sector

## 8. Key Differences from EIA AEO

| Dimension | IEA WEO | EIA AEO |
|-----------|---------|---------|
| Geography | Global, 26+ regions | US only |
| Scenarios | STEPS, APS, NZE | Reference + side cases |
| Time resolution | Snapshots (2030/35/40/50) | Annual |
| Normative scenario | Yes (NZE) | No |
| Model | GEC (proprietary) | NEMS (public) |
| Data annex | Free Excel | Free API |
| Publication | Annual (October) | Annual (varies) |
