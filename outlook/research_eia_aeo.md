# EIA Annual Energy Outlook (AEO) — Data Structure Reference

## 1. API Architecture

- **API v2 Base**: `https://api.eia.gov/v2/`
- **AEO Root**: `https://api.eia.gov/v2/aeo/`
- **Auth**: Free API key from `https://www.eia.gov/opendata/register.php`
- **Response**: JSON with `response.data[]`

### Query Pattern
```
GET https://api.eia.gov/v2/aeo/{year}/data/
    ?api_key={KEY}
    &frequency=annual
    &data[0]=value
    &facets[scenario][]={scenarioId}
    &facets[seriesId][]={seriesId}
    &start={startYear}
    &end={endYear}
```

## 2. AEO2025 Scenarios (11 Cases)

| # | Scenario | API ID (typical) | Description |
|---|----------|-------------------|-------------|
| 1 | Reference Case | `ref2025` | Laws/regulations as of Dec 2024 |
| 2 | High Oil Price | `highoilprice` | Brent ~$157/b by 2050 |
| 3 | Low Oil Price | `lowoilprice` | Brent ~$48/b by 2050 |
| 4 | High Oil and Gas Supply | `highoilgassupply` | 50% higher recovery |
| 5 | Low Oil and Gas Supply | `lowoilgassupply` | Constrained resources |
| 6 | High Zero-Carbon Tech Cost | `highztc` | Higher clean tech costs |
| 7 | Low Zero-Carbon Tech Cost | `lowztc` | 40% lower by 2050 |
| 8 | High Economic Growth | `highmacro` | 2.1% GDP CAGR |
| 9 | Low Economic Growth | `lowmacro` | 1.2% GDP CAGR |
| 10 | Alt Electricity | `altelec` | No EPA CAA 111 rule |
| 11 | Alt Transportation | `alttrans` | No CAFE/ZEV mandates |

## 3. Commodity Price Variables

### Petroleum
| Variable | Units | Geography |
|----------|-------|-----------|
| Brent spot | $/barrel (real & nominal) | Global |
| WTI spot | $/barrel | National |
| Motor gasoline retail | $/gallon | National, Census Division |
| Diesel retail | $/gallon | National, Census Division |
| Jet fuel | $/gallon | National |
| Heating oil | $/gallon | National, Census Division |

### Natural Gas
| Variable | Units | Geography |
|----------|-------|-----------|
| Henry Hub spot | $/MMBtu | National |
| Wellhead price | $/Mcf | National |
| Residential delivered | $/MMBtu | National, Census Division |
| Commercial delivered | $/MMBtu | National, Census Division |
| Industrial delivered | $/MMBtu | National, Census Division |
| Electric power delivered | $/MMBtu | National, Census Division |
| LNG export price | $/MMBtu | National |

### Coal
| Variable | Units | Geography |
|----------|-------|-----------|
| Minemouth avg | $/short ton, $/MMBtu | National, Supply Region |
| Delivered to power | $/short ton, $/MMBtu | National, Census Division |
| Delivered to industrial | $/short ton | National |

### Electricity
| Variable | Units | Geography |
|----------|-------|-----------|
| Residential avg | cents/kWh | National, Census Division |
| Commercial avg | cents/kWh | National, Census Division |
| Industrial avg | cents/kWh | National, Census Division |
| All sectors avg | cents/kWh | National, Census Division |
| Wholesale | cents/kWh | National, Region |

### Hydrogen (NEW AEO2025)
| Variable | Units | Geography |
|----------|-------|-----------|
| Production cost by tech | $/kg | National, Census Division |
| Delivered price | $/kg | National |

## 4. Demand Variables

### By Sector
- Total primary energy: quads
- Residential: quads (by end use: space heating, cooling, water heating, lighting, appliances)
- Commercial: quads (by end use + floorspace)
- Industrial: quads (fuel + feedstock, by subsector)
- Transportation: quads (by fuel and mode)
- Electric power: billion kWh

### By Fuel (all sectors)
- Natural gas: Tcf
- Petroleum: Mb/d
- Coal: million short tons
- Electricity: billion kWh
- Hydrogen (NEW): million metric tons
- Renewables/biomass: quads

### Transportation Detail
- LDV sales by tech (ICE, HEV, PHEV, BEV, FCEV): millions
- LDV stock by tech: millions
- VMT: billions of miles
- New vehicle fuel economy: mpge
- Heavy-duty truck sales/stock by fuel

## 5. Supply Variables

### Oil
- U.S. crude production: Mb/d (total, tight oil, offshore, Alaska)
- NGPL production: Mb/d
- Refinery production/capacity: Mb/d
- Biofuels: billion gallons
- Imports/exports: Mb/d

### Natural Gas
- Dry gas production: Tcf (shale, tight, CBM, offshore, Alaska)
- LNG exports/imports: Tcf
- Pipeline exports: Tcf
- Storage: Bcf

### Coal
- Production by region and rank: million short tons
- Exports/imports: million short tons

### Hydrogen (NEW AEO2025)
- Production by tech: million metric tons
- Capacity: GW
- Carbon intensity: kg CO2e/kg H2

## 6. Electricity Generation & Capacity

### Generation (billion kWh)
Coal, natural gas (CC/CT/steam), nuclear, hydro, wind, solar (utility/distributed/CSP), geothermal, biomass, petroleum, CHP

### Capacity (GW)
Same breakdown + battery storage (GW/GWh), planned additions/retirements by tech

### Carbon Capture (NEW AEO2025)
- CO2 captured: Mt (power + industrial)
- CO2 stored/used for EOR: Mt
- CO2 pipeline capacity

## 7. Technology Cost Projections
- Overnight capital cost: $/kW
- Fixed/Variable O&M: $/kW-yr, $/MWh
- LCOE: $/MWh
- Capacity factor: %
- Heat rate: Btu/kWh

Technologies: coal ±CCS, NGCC ±CCS, NGCT, nuclear, onshore/offshore wind, solar PV, CSP, geothermal, biomass, battery storage

## 8. Emissions
- CO2 by sector and fuel: MMmt CO2
- CO2 intensity: per capita, per GDP
- SO2, NOx, mercury: thousand short tons / tons
- Methane: MMmt CO2e

## 9. Macro
- Real GDP: billions 2024$
- Population: millions
- Employment: millions
- CPI/deflator, interest rates

## 10. Units
| Category | Primary Unit |
|----------|-------------|
| Energy | quads (1 quad = 1.055 EJ) |
| Oil | Mb/d |
| Gas | Tcf, Bcf/d |
| Coal | million short tons |
| Electricity gen | billion kWh |
| Capacity | GW |
| Prices | real 2024$ |
| CO2 | MMmt CO2 |
| Hydrogen | $/kg, MMmt |

## 11. Geography
- National (US)
- 9 Census Divisions
- ~25 Electricity Market Module (EMM) regions
- ~6-12 oil/gas supply regions
- ~14 coal supply regions

### Census Divisions
1. New England (CT, MA, ME, NH, RI, VT)
2. Middle Atlantic (NJ, NY, PA)
3. East North Central (IL, IN, MI, OH, WI)
4. West North Central (IA, KS, MN, MO, ND, NE, SD)
5. South Atlantic (DC, DE, FL, GA, MD, NC, SC, VA, WV)
6. East South Central (AL, KY, MS, TN)
7. **West South Central (AR, LA, OK, TX)** ← Gulf Coast / ERCOT
8. Mountain (AZ, CO, ID, MT, NM, NV, UT, WY)
9. Pacific (AK, CA, HI, OR, WA)

## 12. Time: Annual, 2024-2050

## 13. AEO Reference Table Numbers
| Table | Content |
|-------|---------|
| 1 | Energy supply/disposition/prices summary |
| 2 | Consumption by sector and source |
| 3 | Prices by sector and source |
| 4-7 | Residential, commercial, industrial, transport detail |
| 8 | Electricity supply/prices/emissions |
| 9 | Generating capacity |
| 11-15 | Oil, gas, coal supply and prices |
| 16-17 | Renewable capacity/generation/consumption |
| 18-19 | CO2 emissions |
| 20 | Macro indicators |

## 14. New in AEO2025
- **Hydrogen Market Module (HMM)**: production, transport, consumption, 45V/45Q credits
- **CCATS Module**: CO2 capture, pipeline, storage, 45Q impacts
- **Hydrocarbon Supply Module**: replaces legacy oil/gas module
- **Captured energy methodology** for non-combustible renewables (3,412 Btu/kWh)
- NEMS source code now on GitHub
