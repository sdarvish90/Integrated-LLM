# DecarbIQ Complete Data Package V2
## Generated: February 10, 2026

---

## 📦 PACKAGE CONTENTS SUMMARY

| Category | Files | Description |
|----------|-------|-------------|
| Python Scripts | 16 | Analysis & data processing code |
| CSV Data Files | 59 | All datasets |
| Excel Workbooks | 5 | Consolidated databases |
| Visualizations | 14 | Heatmaps & charts |
| Documentation | 11 | Methodology & summaries |
| JSON Config | 3 | Model parameters |

**Total: 108 files**

---

## 🗂️ KEY FILES BY CATEGORY

### 📊 MASTER DATASETS (Use These for Analysis)

| File | Rows | Columns | Description |
|------|------|---------|-------------|
| `master_regression_dataset_v3.csv` | 336 | 75 | **LATEST** - Full dataset with ERCOT/CAISO |
| `master_regression_dataset_v2.csv` | 336 | 75 | Previous version |
| `full_regression_dataset.csv` | 336 | 67 | Base regression dataset |

### ⚡ ELECTRICITY PRICE DATA

| File | Description | Coverage |
|------|-------------|----------|
| `ercot_wholesale_historical_full.csv` | **ERCOT wholesale monthly** | Dec 2010 - Dec 2019 (109 months) |
| `ercot_wholesale_merged.csv` | ERCOT recent | Jan 2020 - Dec 2024 (60 months) |
| `caiso_wholesale_historical.csv` | **CAISO wholesale monthly** | Jan 2001 - Dec 2013 (156 months) |
| `caiso_wholesale_merged.csv` | CAISO recent | Oct 2022 - Dec 2024 (27 months) |
| `regional_retail_electricity_wide.csv` | TX/CA industrial retail | Jan 2001 - Nov 2025 (288 months) |
| `wholesale_hubs_monthly_long.csv` | All ICE hubs (PJM, ISO-NE, etc.) | 2001-2013 |
| `wholesale_hubs_monthly_wide.csv` | Same, pivoted format | 2001-2013 |

### 🔥 NATURAL GAS DATA

| File | Description | Coverage |
|------|-------------|----------|
| `decarbiq_monthly_gas_prices.csv` | Henry Hub monthly prices | 1997-2025 |
| `decarbiq_gas_electricity_database.csv` | Gas-electricity nexus data | 1997-2024 |

### 📋 POLICY & REGULATORY DATABASES

| File | Description | Records |
|------|-------------|---------|
| `decarbiq_policy_database.csv` | All energy policies | 40 policies (1919-2026) |
| `decarbiq_permitting_bottlenecks.csv` | Current permitting issues | 17 bottlenecks |
| `decarbiq_regulatory_milestones.csv` | Regulatory events | 31 milestones |
| `policy_regulation_database.csv` | Combined policy database | Full timeline |
| `decarbiq_permitting_master.xlsx` | **Excel workbook** | 8 sheets |
| `decarbiq_policy_database.xlsx` | **Excel workbook** | 5 sheets |

### 📈 VOLATILITY & EVENTS

| File | Description | Records |
|------|-------------|---------|
| `volatility_events_database.csv` | Major price events | 12 events |
| `volatility_parameter_events.csv` | Parameter-level events | 718 events |
| `decarbiq_volatility_database.xlsx` | **Excel workbook** | 9 tabs |

### 📉 REGRESSION RESULTS

| File | Description |
|------|-------------|
| `ercot_caiso_correlations.csv` | **ERCOT/CAISO correlation matrix** |
| `full_model_coefficients.csv` | All 45 regression coefficients |
| `model_comparison_by_group.csv` | R² by variable group |
| `incremental_model_results.csv` | Sequential model building |

### 🖼️ VISUALIZATIONS

| File | Description |
|------|-------------|
| `ercot_caiso_heatmap_full.png` | **KEY** - ERCOT/CAISO focused heatmap |
| `comprehensive_correlation_heatmap.png` | Full 17x17 correlation matrix |
| `grouped_correlation_heatmap.png` | Prices vs all variable groups |
| `regional_electricity_heatmap_v2.png` | Regional electricity analysis |

---

## 🐍 PYTHON SCRIPTS

### Core Analysis Scripts
| Script | Purpose |
|--------|---------|
| `decarbiq_comprehensive_regression.py` | **MAIN** - Full regression with 45 variables |
| `decarbiq_regression_analysis.py` | Base regression analysis |
| `wholesale_regression_analysis.py` | Wholesale electricity regression |

### Data Collection Scripts
| Script | Purpose |
|--------|---------|
| `decarbiq_wholesale_fetcher.py` | Wholesale electricity data collection |
| `decarbiq_demand_drivers_fetcher.py` | EIA demand drivers |
| `decarbiq_energy_fetcher_v2.py` | Energy market data |
| `decarbiq_data_agents.py` | Automated data agents |

### Specialized Analysis
| Script | Purpose |
|--------|---------|
| `decarbiq_regional_analysis.py` | Regional market analysis |
| `decarbiq_fuel_supply_analysis.py` | Fuel supply dynamics |
| `crude_oil_analysis.py` | Crude oil market analysis |
| `oil_vs_gas_impact_comparison.py` | Oil vs gas comparison |

---

## 📊 KEY FINDINGS SUMMARY

### Model Performance
- **R² = 0.76** (explains 76% of gas price variance)
- **MAE = $0.72/MMBtu**
- **45 variables across 5 groups**

### ERCOT vs CAISO Electricity Prices

| Variable | TX Industrial | CA Industrial | Insight |
|----------|--------------|---------------|---------|
| Henry Hub Correlation | **+0.68** | **-0.35** | Opposite responses! |
| Mean Price | 6.2 ¢/kWh | 12.0 ¢/kWh | CA 96% higher |
| Shale Era Effect | ↓ prices | ↑ prices | Divergent |

### Data Coverage (Final)
- ERCOT Wholesale: **169 months** (2010-2024)
- CAISO Wholesale: **183 months** (2001-2024)
- TX/CA Retail: **288 months** (2001-2025)
- Policy Database: **40 policies** (1919-2026)

---

## 📝 DOCUMENTATION

| File | Description |
|------|-------------|
| `decarbiq_model_methodology.md` | Model methodology |
| `decarbiq_regression_vs_benchmarks.md` | Benchmark comparison |
| `ng_electricity_regression_summary.md` | Main findings summary |
| `decarbiq_confidence_assessment.md` | Confidence levels |
| `decarbiq_data_gaps_status.md` | Data gap analysis |

---

## 🚀 QUICK START

1. **For regression analysis**: Use `master_regression_dataset_v3.csv`
2. **For ERCOT/CAISO comparison**: Use `ercot_caiso_correlations.csv` + `ercot_caiso_heatmap_full.png`
3. **For policy analysis**: Use `decarbiq_permitting_master.xlsx`
4. **To run analysis**: Start with `decarbiq_comprehensive_regression.py`

