# Pipeline 2: Distribution-Level Interconnections Tracker

Tracks TSP-specific queue data, EIA planned generators, and aggregated interconnection
queue statistics to identify blue H2/ammonia/CCS signals at the distribution level.

## Architecture

```
Section 4 of ERCOT/Texas Data Collection Guide
├── 4A: TSP Quarterly Earnings (Oncor, CenterPoint, AEP)
│   ├── LC&I queue request counts and GW breakdown
│   ├── Generation POI queue (storage/solar/wind/gas %)
│   ├── Circuit miles, premises growth, collateral deposits
│   └── DG interconnection page monitoring
├── 4B: EIA Form 860/860M
│   ├── Monthly planned generator inventory (TX filter)
│   ├── Gulf Coast county filter
│   ├── Known developer matching
│   └── Gas generator identification
├── 4C: Interconnection.fyi
│   ├── ERCOT generation queue aggregates
│   └── Data center project tracker
└── 4D: Cross-TSP Synthesis
    ├── Unified queue time series
    ├── EIA 860M delta detection (new/removed generators)
    └── Alert generation for DecarbIQ
```

## Usage

```bash
# Install dependencies
pip install requests beautifulsoup4 pandas lxml pdfplumber openpyxl

# Run all modules
python run_pipeline.py

# Run specific module
python run_pipeline.py -m 4a    # TSP earnings only
python run_pipeline.py -m 4b    # EIA data only
python run_pipeline.py -m 4c    # Interconnection.fyi
python run_pipeline.py -m 4d    # Synthesis only (needs 4A-4C data)
```

## Scheduling

```bash
# Monthly full run (1st of month, after EIA 860M published ~25th)
0 7 1 * * cd /path/to/pipeline_2 && python run_pipeline.py

# Weekly TSP check (Monday 8 AM, catches earnings releases)
0 8 * * 1 cd /path/to/pipeline_2 && python run_pipeline.py -m 4a

# Quarterly deep run (after earnings season)
# Feb, May, Aug, Nov — 2nd week
0 7 8-14 2,5,8,11 1 cd /path/to/pipeline_2 && python run_pipeline.py
```

## Key Outputs

| File | Contents |
|------|----------|
| `oncor_queue_timeseries.json` | Quarterly Oncor metrics (queue requests, GW by type, gen mix) |
| `eia860m_texas_planned.csv` | All planned generators in Texas |
| `eia860m_gulf_coast_planned.csv` | Planned generators in Gulf Coast counties |
| `eia860m_developer_matches.csv` | Generators owned by known H2/ammonia developers |
| `unified_queue_timeseries.json` | Cross-TSP time series |
| `eia860m_deltas.json` | New/removed generators vs previous run |
| `queue_alerts.json` | DecarbIQ alerts |

## Alert Types

| Alert | Severity | Trigger |
|-------|----------|---------|
| `NEW_GAS_GENERATOR` | HIGH | Gas turbine planned in Gulf Coast county |
| `DEVELOPER_MATCH` | HIGH | Known H2/ammonia developer in EIA filings |
| `INDUSTRIAL_GROWTH` | MEDIUM | Industrial GW > 15 in TSP queue |
| `COLLATERAL_SPIKE` | MEDIUM | Customer collateral > $2B |
| `QUEUE_MILESTONE` | LOW | Queue crosses round-number threshold |

## Cross-Reference with Pipeline 1

Pipeline 2 data enriches Pipeline 1 (large load queue) signals:

- **4A Oncor industrial GW → 2B PUC SB6**: When Oncor reports industrial GW growth,
  check if corresponding SB6 filings appear for new large load interconnection standards
- **4B EIA gas generators → 2D permit cross-ref**: New gas generator in Gulf Coast →
  check TCEQ air permits for same entity → potential blue H2 power supply
- **4D alerts → 2A ERCOT Board**: HIGH alerts should trigger deep monitoring of next
  ERCOT Board/RPG meeting materials for related transmission project endorsements
