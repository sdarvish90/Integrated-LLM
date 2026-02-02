# Station Model - Python Implementation

A comprehensive Python implementation of the multi-energy station mass and energy balance calculator, converted from `Calculator-Final_V1_4.xlsx`.

## Overview

This package models an integrated energy station that includes:

- **TriGen (MCFC)**: Molten Carbonate Fuel Cell producing electricity, hydrogen, and heat from natural gas
- **SMR**: Steam Methane Reformer for hydrogen production
- **Electrolyzer**: PEM Electrolyzer for hydrogen production from electricity
- **LH2 Delivery**: Liquid hydrogen pump and vaporizer pathway
- **H2 Station**: Hydrogen compression, chilling, and dispensing
- **Fuel Cell**: PEM fuel cell converting excess hydrogen to electricity
- **Battery (BESS)**: Battery energy storage for load leveling
- **CNG Station**: Compressed natural gas fueling station
- **EV Charger**: DC fast charging for electric vehicles

## Installation

```bash
# Clone or download the package
cd station_model

# Install dependencies
pip install pint  # For unit handling (optional)
```

## Quick Start

```python
from core.integration import Station

# Create station with default configuration
station = Station()

# Run calculation
outputs = station.calculate(
    trigen_NG_scf_h=10700,      # TriGen natural gas input (scf/h)
    smr_H2_tpd=0,               # SMR production (ton/day H2)
    electrolyzer_H2_tpd=0,      # Electrolyzer production (ton/day H2)
    lh2_throughput_tpd=0.5,     # LH2 delivery (ton/day)
    gh2_trailer_kg_day=0,       # GH2 trailer delivery (kg/day)
    cng_trucks_per_day=25,      # CNG trucks to fuel
    ev_demand_kWh_day=20000,    # EV charging demand (kWh/day)
    fuel_cell_enabled=False     # Whether fuel cell is active
)

# Print summary
print(station.summary())

# Access specific outputs
print(f"Total H2 dispensed: {outputs.H2_dispensed_kg_day:.2f} kg/day")
print(f"Grid import: {outputs.elec_from_grid:.2f} kWh/day")
print(f"LEC: {outputs.levelized_energy_consumption:.3f}")
```

## Using Individual Systems

Each system can be used independently:

```python
from systems.trigen import TriGen
from systems.smr import SMR
from systems.electrolyzer import Electrolyzer

# TriGen
trigen = TriGen()
trigen_out = trigen.calculate(NG_input_scf_h=10700)
print(trigen.summary())

# SMR with heat from TriGen
smr = SMR()
smr_out = smr.calculate(
    H2_rate_tpd=0.5,
    Q_from_trigen_kW=trigen_out.Q_available_for_export_kW
)
print(smr.summary())

# Electrolyzer
elec = Electrolyzer()
elec_out = elec.calculate(H2_rate_tpd=0.5)
print(elec.summary())
```

## Configuration

All system parameters can be customized via config classes:

```python
from config import StationConfig, TriGenConfig

# Customize TriGen
trigen_config = TriGenConfig(
    NG_input_scf_per_h=15000,  # Higher NG input
    H2_coprod_share=0.30,      # Higher H2 co-production
)

# Create station with custom config
config = StationConfig(trigen=trigen_config)
station = Station(config)
```

## Package Structure

```
station_model/
├── __init__.py           # Package initialization
├── config.py             # All configuration dataclasses
├── units.py              # Unit handling with Pint
├── main.py               # Example usage and tests
│
├── systems/              # Individual system modules
│   ├── __init__.py
│   ├── trigen.py         # TriGen MCFC system
│   ├── smr.py            # Steam Methane Reformer
│   ├── electrolyzer.py   # PEM Electrolyzer
│   ├── lh2_delivery.py   # LH2 pump and vaporizer
│   ├── h2_station.py     # H2 compression and dispensing
│   ├── fuel_cell.py      # PEM Fuel Cell
│   ├── battery.py        # Battery storage (BESS)
│   ├── cng_station.py    # CNG fueling station
│   └── ev_charger.py     # EV DC fast charger
│
└── core/                 # Integration layer
    ├── __init__.py
    └── integration.py    # Station-level orchestration
```

## Issues Identified in Excel Model

During conversion, several issues were identified in the original Excel model:

1. **LH2 Delivery (B48)**: Formula `=B3*1000*(1+B19)` ADDS losses instead of subtracting. Should be `*(1-B19)`.

2. **SMR Steam Calculation (C16)**: The formula uses `MW_H2` instead of `MW_CH4` in the denominator, resulting in ~8x overestimate of steam requirements. The Python model uses the physically correct calculation.

These issues are flagged when running calculations via the `issues` property of each system.

## Validation

The model has been validated against Excel for key outputs:

| Parameter | Excel | Python | Match |
|-----------|-------|--------|-------|
| TriGen H2 (kg/day) | 522.20 | 522.20 | ✓ |
| TriGen Electricity (kWh/day) | 30,636.48 | 30,636.48 | ✓ |
| TriGen Heat Export (kWh_th/day) | 9,941.62 | 9,941.65 | ✓ |
| CNG NG Consumption (scf/day) | 234,156 | 234,156 | ✓ |
| CNG Electricity (kWh/day) | 1,301.80 | 1,301.80 | ✓ |

## Key Equations

### TriGen System
- Fuel Energy Input: `NG_scf_h × LHV_BTU/scf / 3412 = kW`
- H2 Production: `Fuel_Energy × H2_coprod_share / LHV_H2`
- Electrical Output: `Fuel_to_stack × electrical_efficiency`
- Heat Available: `Useful_heat + Latent_recovery - Preheat_duties`

### Compressor Energy
- From Buffer (20 bar): `H2_kg × 8 kWh/kg`
- From GH2 Trailer (250 bar): `H2_kg × 1.5 kWh/kg`

### Levelized Energy Consumption (LEC)
```
LEC = Total_Energy_Input / Total_Energy_Output
    = (NG_HHV + Grid_Import + Delivered_H2_HHV) / (Dispensed_H2_LHV + CNG + EV)
```
LEC < 1.5 indicates good overall system efficiency.

## Future Enhancements

- [ ] Time-series simulation (hourly dispatch)
- [ ] Demand charge optimization
- [ ] Equipment degradation models
- [ ] Economic analysis (CAPEX, OPEX, LCOH)
- [ ] Carbon intensity tracking
- [ ] Uncertainty/sensitivity analysis

## Authors

- Original Excel Model: Shadi Darvish (Eco Decarb Forward), Ian Monk (Powertech USA)
- Python Conversion: Claude (Anthropic)

## License

Proprietary - Southern Company / Powertech USA

---

# Hydrogen Intelligence Monitoring System

Automated news collection and analysis for hydrogen project intelligence.

## Quick Start

### 1. Install Dependencies

```bash
# Install Python packages
pip install -r requirements.txt
```

### 2. Run the System

```bash
python hydrogen_intelligence_monitor.py
```

### 3. Choose Mode

When you run the script, you'll see:

```
CHOOSE MODE:
1. Run once (test mode)          ← Start here to test
2. Run scheduled monitoring (continuous)
3. View recent articles
4. Generate daily digest
5. Generate weekly summary
6. View statistics
```

**Recommended first run:** Choose option `1` to test
