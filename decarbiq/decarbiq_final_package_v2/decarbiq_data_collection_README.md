# DecarbIQ Data Collection System

## Overview

This package provides automated data collection agents for building and maintaining the DecarbIQ natural gas and electricity price database. The system is designed to:

1. **Fetch historical data** for initial database construction
2. **Update incrementally** with new monthly/weekly data
3. **Handle multiple data sources** with unified interfaces
4. **Export to CSV** for use in regression analysis

## Files Included

| File | Purpose |
|------|---------|
| `decarbiq_data_agents.py` | Main data collection agents for EIA, ERCOT, CAISO, Baker Hughes |
| `decarbiq_wholesale_fetcher.py` | Gridstatus wrapper for ERCOT/CAISO wholesale prices |
| `decarbiq_regression_analysis.py` | Statistical regression analysis (from previous session) |
| `decarbiq_regional_analysis.py` | Region-specific factor analysis |
| `decarbiq_regional_confidence_assessment.md` | Confidence assessment and data gaps |

## Quick Start

### 1. Install Dependencies

```bash
pip install gridstatus pandas requests
```

### 2. Get an EIA API Key (Free)

1. Go to: https://www.eia.gov/opendata/register.php
2. Register with your email
3. Save the API key

### 3. Initialize the Collector

```python
from decarbiq_data_agents import DecarbIQDataCollector

# Initialize with your API key
collector = DecarbIQDataCollector(eia_api_key="YOUR_KEY_HERE")

# Or set environment variable first:
# export EIA_API_KEY=your_key_here
collector = DecarbIQDataCollector()
```

### 4. Fetch Data

```python
# Fetch all EIA series from 2010 onwards
collector.fetch_all_eia(start="2010-01")

# Fetch a specific series
data = collector.fetch_series("henry_hub_spot", start="2020-01", end="2024-12")

# Fetch and save to CSV
collector.fetch_and_save("us_storage", start="2020-01")
```

### 5. Fetch Wholesale Electricity Prices

```python
from decarbiq_wholesale_fetcher import ERCOTWholesaleFetcher, CAISOWholesaleFetcher

# ERCOT (Texas)
ercot = ERCOTWholesaleFetcher()
df = ercot.get_monthly_series(2020, 2024, location="HB_NORTH")
df.to_csv("ercot_monthly.csv")

# CAISO (California)
caiso = CAISOWholesaleFetcher()
df = caiso.get_monthly_series(2020, 2024, location="TH_SP15_GEN-APND")
df.to_csv("caiso_monthly.csv")
```

---

## Data Sources

### EIA API (Automated)

| Series | Endpoint | Frequency | Status |
|--------|----------|-----------|--------|
| Henry Hub Spot | `natural-gas/pri/sum` | Monthly/Daily | ✅ Ready |
| US Production | `natural-gas/prod/sum` | Monthly | ✅ Ready |
| US Storage | `natural-gas/stor/sum` | Weekly | ✅ Ready |
| Retail Electricity | `electricity/retail-sales` | Monthly | ✅ Ready |
| LNG Exports | `natural-gas/move/expc/sum` | Monthly | ✅ Ready |
| Mexico Exports | `natural-gas/move/expn/sum` | Monthly | ✅ Ready |

### EIA API (Need Verification)

| Series | Issue | Resolution |
|--------|-------|------------|
| Waha Hub Spot | Need correct duoarea code | Check EIA API browser for `STXPWAHA` |
| SoCal Citygate | Need correct duoarea code | Check EIA API browser for `SCA` |
| Permian Production | Need STEO series ID | Use `NGPRPPERM` or similar |
| Haynesville Production | Need STEO series ID | Check STEO route |
| Pacific Storage | Need duoarea code | Use `SAP` |

### Gridstatus (Wholesale Prices)

| Series | Source | Frequency | Location |
|--------|--------|-----------|----------|
| ERCOT SPP | gridstatus.ERCOT() | Hourly → Monthly | HB_NORTH, HB_HOUSTON, HB_WEST |
| CAISO LMP | gridstatus.CAISO() | Hourly → Monthly | SP15, NP15 |

### Manual Download Required

| Data | Source | Format | URL |
|------|--------|--------|-----|
| Rig Count by Basin | Baker Hughes | Excel | https://rigcount.bakerhughes.com/na-rig-count |
| Permian Pipeline Capacity | Operator announcements | Various | See EIA articles |
| Aliso Canyon Status | California Energy Commission | Reports | https://www.energy.ca.gov |

---

## Registered Data Series

The system includes 18 pre-defined data series:

### Natural Gas Prices
- `henry_hub_spot` - US benchmark (monthly)
- `waha_spot` - Permian/Texas (daily)
- `socal_citygate` - California (monthly)

### Production
- `us_production` - US total (monthly)
- `permian_production` - Permian Basin (monthly)
- `haynesville_production` - Haynesville Shale (monthly)
- `appalachian_production` - Marcellus/Utica (monthly)

### Storage
- `us_storage` - Total Lower 48 (weekly)
- `pacific_storage` - Pacific region (weekly)

### Electricity Prices
- `us_retail_elec` - US average residential (monthly)
- `tx_retail_elec` - Texas residential (monthly)
- `ca_retail_elec` - California residential (monthly)
- `ercot_spp` - ERCOT wholesale (hourly)
- `caiso_lmp` - CAISO wholesale (hourly)

### Exports
- `lng_exports` - US LNG exports (monthly)
- `mexico_exports` - Pipeline to Mexico (monthly)

### Rig Counts
- `us_gas_rigs` - US gas rigs (weekly)
- `permian_rigs` - Permian basin (weekly)

---

## Data Gaps for DecarbIQ Model

### High Priority (Critical for Regression)

| Gap | Impact | Solution |
|-----|--------|----------|
| Waha daily prices | Texas gas-elec model | Verify EIA series code or use Natural Gas Intelligence |
| SoCal Citygate | California gas-elec model | Verify EIA series code |
| ERCOT wholesale monthly | Texas elasticity calculation | Use gridstatus aggregation |
| Permian production | Regional factor analysis | Use EIA STEO or Drilling Productivity Report |

### Medium Priority (Improves Model)

| Gap | Impact | Solution |
|-----|--------|----------|
| Pacific storage levels | CA gas price model | Verify EIA duoarea code |
| Haynesville production | Gulf Coast supply | Use EIA STEO |
| LNG terminal utilization | Export impact on price | EIA LNG Monthly |

### Low Priority (Nice to Have)

| Gap | Impact | Solution |
|-----|--------|----------|
| CAISO renewable curtailment | When gas sets marginal | CAISO daily renewables report |
| California wildfire costs | Non-fuel elec price driver | CPUC rate cases |
| Permian pipeline capacity | Waha basis driver | Operator announcements |

---

## Automation Schedule

For production use, set up cron jobs:

```bash
# Weekly updates (Fridays) - Storage and rig counts
0 12 * * 5 python3 decarbiq_data_agents.py --update weekly

# Monthly updates (1st of month) - Prices and production
0 12 1 * * python3 decarbiq_data_agents.py --update monthly

# Daily ERCOT prices (for real-time monitoring)
0 8 * * * python3 decarbiq_wholesale_fetcher.py --ercot --yesterday
```

---

## API Reference

### EIAAgent

```python
agent = EIAAgent(api_key="YOUR_KEY")

# Natural gas prices
data = agent.get_natural_gas_prices(price_type="rngwhhd", frequency="monthly")

# Regional prices
data = agent.get_regional_gas_prices(region="SCA", frequency="monthly")

# Production
data = agent.get_production_by_region(region="TX", frequency="monthly")

# Storage
data = agent.get_storage(region="NUS", frequency="weekly")

# Electricity prices
data = agent.get_electricity_prices(state="TX", sector="RES")
```

### ERCOTWholesaleFetcher

```python
fetcher = ERCOTWholesaleFetcher()

# Single day
df = fetcher.get_daily_spp(date="2024-01-15", location="HB_NORTH")

# Date range
df = fetcher.get_historical_spp(start="2024-01-01", end="2024-12-31")

# Monthly average
avg = fetcher.get_monthly_average(year=2024, month=1, location="HB_NORTH")

# Monthly series
df = fetcher.get_monthly_series(start_year=2020, end_year=2024)
```

### CAISOWholesaleFetcher

```python
fetcher = CAISOWholesaleFetcher()

# Single day
df = fetcher.get_daily_lmp(date="2024-01-15", location="TH_SP15_GEN-APND")

# Monthly series
df = fetcher.get_monthly_series(start_year=2020, end_year=2024)
```

---

## Troubleshooting

### EIA API Errors

| Error | Cause | Solution |
|-------|-------|----------|
| 403 Forbidden | Invalid API key | Check key at https://www.eia.gov/opendata/ |
| 400 Bad Request | Invalid parameters | Check facet names in API browser |
| 5000 row limit | Too much data | Add date range filters |

### Gridstatus Errors

| Error | Cause | Solution |
|-------|-------|----------|
| No data returned | Date range too old | ERCOT data starts ~2010, CAISO ~2012 |
| Location not found | Invalid zone name | Use HB_NORTH, SP15, etc. |
| Timeout | Large date range | Fetch in smaller chunks (1 year at a time) |

---

## Next Steps

1. **Get EIA API key** and test basic fetches
2. **Install gridstatus** and verify ERCOT/CAISO access
3. **Run initial historical fetch** for 2010-2024
4. **Validate against existing annual data** in regression scripts
5. **Set up automated updates** for ongoing data collection
6. **Fill remaining gaps** (Waha, SoCal Citygate series codes)
