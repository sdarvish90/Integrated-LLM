# NREL Annual Technology Baseline (ATB) — Data Structure Reference

## 1. Overview
Published annually (~June-July). Provides consistent cost/performance data for electricity generation and storage technologies. Used as input to NREL's ReEDS model and EIA's NEMS/AEO. Hosted at https://atb.nrel.gov/

## 2. Technologies Covered

### Solar
| Technology | ATB Name | Variants |
|---|---|---|
| Utility-Scale PV | `UtilityPV` | Class 1-10 (by GHI); tracking/fixed |
| Commercial PV | `CommPV` | Rooftop; Class 1-10 |
| Residential PV | `ResPV` | Rooftop; Class 1-10 |
| CSP | `CSP` | Tower + molten salt TES (10/14-hr); Class 1-5 (by DNI) |
| PV+Battery Hybrid | `UtilityPV+Battery` | 100 MW PV + 60 MW / 4-hr battery |

### Wind
| Technology | ATB Name | Variants |
|---|---|---|
| Land-Based | `LandbasedWind` | Class 1-10 (by wind speed/CF) |
| Offshore Fixed-Bottom | `OffShoreWind` | Class 1-7 |
| Offshore Floating | `OffShoreWind` | Class 8-15 |
| Distributed | `DistributedWind` | Residential/commercial/large |

### Storage
| Technology | ATB Name | Variants |
|---|---|---|
| Utility Battery | `UtilityBattery` | Li-ion; 2/4/6/8/10-hr duration |
| Commercial Battery | `CommBattery` | Behind-meter; 2/4-hr |
| Residential Battery | `ResBattery` | Coupled with PV |
| Pumped Storage Hydro | `PumpedStorageHydro` | New/existing |

### Natural Gas
| Technology | ATB Name | Variants |
|---|---|---|
| Combined Cycle | `NaturalGas_CCAvg` | F/H-class |
| Combustion Turbine | `NaturalGas_CTAvg` | Aeroderivative/frame |
| NG-CC + CCS (90%) | `NaturalGas_CC_CCS` | Amine capture |
| NG-CC + CCS (95%+) | `NaturalGas_CC_CCS95` | Higher capture |

### Coal
- Coal new (`Coal_newAvg`), existing (`Coal_Retrofits`)
- Coal + CCS 90%/95% (`Coal_CCS90`, `Coal_CCS95`)

### Nuclear
- Conventional Gen III+ LWR (`Nuclear`) ~1100 MW
- Small Modular Reactor (`NuclearSMR`) ~300 MW

### Geothermal
- Hydrothermal flash/binary (`Geothermal_HydroFlash`), Class 1-5
- Enhanced Geothermal (`Geothermal_EGS`)

### Hydropower
- Non-Powered Dams (`Hydropower_NPD`), Class 1-4
- New Stream-reach (`Hydropower_NSD`)

### Biopower
- Dedicated biomass (`Biopower`)

### Hydrogen
| Technology | ATB Name |
|---|---|
| PEM Electrolysis | `Electrolysis_PEM` |
| Alkaline Electrolysis | `Electrolysis_Alkaline` |
| SOEC (high-temp) | `Electrolysis_SOEC` |
| SMR | `SMR` |
| SMR + CCS | `SMR_CCS` |
| ATR + CCS | `ATR_CCS` |
| H2 Combustion Turbine | `H2_CT` |
| H2 Combined Cycle | `H2_CC` |

## 3. Cost Metrics

| Metric | Units | Applies To |
|---|---|---|
| **CAPEX** (overnight) | $/kW | All generation |
| **CAPEX** (with grid connection) | $/kW | All generation |
| **Fixed O&M** | $/kW-yr | All |
| **Variable O&M** | $/MWh | Dispatchable (0 for wind/solar) |
| **Fuel Cost** | $/MMBtu | Thermal |
| **Heat Rate** | MMBtu/MWh | Thermal |
| **Capacity Factor** | % (0-1) | All |
| **LCOE** | $/MWh (real, levelized) | All generation |
| **LCOS** | $/MWh | Storage |
| **LCOH** | $/kg H2 | Hydrogen |
| **Construction Finance Factor** | multiplier | All |
| **Economic Life** | years | 15-60 yrs by tech |
| **Construction Duration** | years | All |

### Technology-Specific
- Solar: degradation rate (%/yr), inverter loading ratio (DC:AC)
- Battery: round-trip efficiency (%), duration (hrs), augmentation cost ($/kWh)
- Wind: hub height (m), rotor diameter (m), specific power (W/m²)
- Offshore wind: water depth (m), distance to shore (km)
- CCS: CO2 capture rate (%), transport & storage cost ($/tonne)

### Financial Assumptions
- Federal tax: 21%, State tax: ~6%
- Inflation: 2.5%
- WACC: technology-specific (5-10% nominal)
- MACRS: 5-yr (solar/wind/battery), 7-yr (fuel cells), 20-yr (transmission)
- ITC/PTC: modeled with IRA values

## 4. Scenarios (3 technology trajectories)

| Scenario | Description |
|---|---|
| **Conservative** | Floor of improvement; minimal learning; slow adoption |
| **Moderate** | Central estimate; current trends; continuation of observed learning |
| **Advanced** | Aggressive but plausible; DOE targets met; rapid scale-up |

### Example ranges (2030):
- Solar PV CAPEX: Conservative ~$900/kW → Advanced ~$550/kW
- Wind onshore CAPEX: Conservative ~$1,300/kW → Advanced ~$900/kW
- Battery 4-hr CAPEX: Conservative ~$1,100/kW → Advanced ~$600/kW

## 5. Time Horizon
- **Annual** projections from base year through **2050**
- Base year: 1-2 years prior to publication (ATB 2024 base = 2022-2023)
- Historical data: typically from 2010
- Updated annually (June-July)

## 6. Data Access

| Format | Description |
|---|---|
| **Excel (.xlsx)** | Complete dataset, one tab per technology |
| **CSV** | Flat-file exports |
| **API** | `https://developer.nrel.gov/api/atb/v1/` (JSON, requires NREL API key) |
| **Tableau** | Interactive visualizations on ATB website |
| **Python** | `nrelpy` package |

### API Query Structure
```
GET https://developer.nrel.gov/api/atb/v1/
  ?api_key={KEY}
  &year={ATB edition}
  &technology={tech_id}
  &scenario=Conservative|Moderate|Advanced
  &projection_year=2024-2050
```

### Data Fields
```
technology, techdetail, scenario, core_metric_parameter,
core_metric_case (Market|R&D), core_metric_variable,
value, units, year
```

## 7. Learning Curves

### Methodology: hybrid
1. Experience curves (cost per doubling of cumulative capacity)
2. Bottom-up engineering (component-level, near-term)
3. Expert elicitation (long-term)
4. Literature/DOE roadmaps

### Learning Rates by Technology
| Technology | Learning Rate |
|---|---|
| Solar PV (module) | ~15-25% per doubling |
| Solar PV (BOS) | ~10% |
| Wind onshore | ~10-15% |
| Wind offshore | ~10-15% |
| Battery storage | ~18-25% |
| Nuclear | Uncertain (historical cost escalation in US) |
| NG CCS | ~10-15% |
| Electrolyzers | ~15-20% |

## 8. Complementary NREL Products

- **ReEDS**: Capacity expansion model (consumes ATB data). 134 balancing areas, 2050 horizon.
- **Standard Scenarios**: Annual publication running ReEDS under multiple ATB/AEO/policy assumptions. Outputs: capacity, generation, prices, emissions by region/year. Available at scenarioviewer.nrel.gov
- **SAM**: System Advisor Model (detailed performance behind ATB renewable data)
- **Cambium**: Marginal cost/emissions data from ReEDS runs
- **Electrification Futures Study (EFS)**: Demand-side electrification scenarios (Reference/Medium/High × Slow/Moderate/Rapid tech)

## 9. Key Notes
- All dollars in **real (constant) base year** (ATB 2024 = 2022$)
- CAPEX distinction: overnight vs. with construction finance factor (CFF adds 10-30% for nuclear/hydro)
- Renewable capacity factors are location-dependent (resource class system)
- Post-IRA editions include with/without ITC/PTC scenarios
- ATB is a primary input to EIA AEO (typically uses Moderate scenario)
