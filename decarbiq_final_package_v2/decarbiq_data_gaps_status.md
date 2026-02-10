# DecarbIQ Data Gaps Status Report
## Updated: 2026-02-09

---

## Summary: What's Done vs What's Still Needed

### Regional Analysis STATUS: ✅ COMPLETED (with limitations)

The regional analysis WAS run separately for each region and era:
- **Texas (Waha → ERCOT)**: Pre-Shale, Shale Boom, Post-Pandemic
- **California (SoCal Citygate → CAISO)**: Pre-Shale, Shale Boom, Post-Pandemic  
- **US Average (Henry Hub → US Average)**: Pre-Shale, Shale Boom, Post-Pandemic

**BUT** it used NATIONAL upstream factors (production, storage, rigs) for all regions because regional factors weren't available.

---

## Data Gap Status by Category

### 1. NATURAL GAS PRICES

| Data | Status | Notes |
|------|--------|-------|
| Henry Hub Monthly | ✅ **COLLECTED** | 1997-2025, 347 records |
| Texas Citygate Monthly | ✅ **COLLECTED** | 1997-2025, via EIA web fetch |
| California Citygate Monthly | ✅ **COLLECTED** | 1997-2025, via EIA web fetch |
| Waha Hub Daily | ❌ **NOT COLLECTED** | Need to verify EIA series code, may need Natural Gas Intelligence subscription |
| SoCal Border Daily | ❌ **NOT COLLECTED** | Need to verify EIA series code |
| Basis Differentials | ✅ **CALCULATED** | TX-HH and CA-HH from collected data |

### 2. REGIONAL PRODUCTION

| Data | Status | Notes |
|------|--------|-------|
| US Total Production | ✅ **IN DATABASE** | Annual, used in regression |
| Permian Monthly | ⚠️ **PARTIAL** | Found annual data (16.7→25.4 Bcf/d 2021-2024), need monthly via EIA STEO |
| Haynesville Monthly | ⚠️ **PARTIAL** | Found annual data only |
| Appalachian Monthly | ⚠️ **PARTIAL** | Found annual data only |
| Texas State Production | ❌ **NOT COLLECTED** | Available via EIA API |

### 3. STORAGE

| Data | Status | Notes |
|------|--------|-------|
| US Total Storage | ✅ **IN DATABASE** | Weekly, used in regression |
| Pacific Region Storage | ❌ **NOT COLLECTED** | EIA duoarea "SAP" - not fetched |
| Gulf Coast Storage | ❌ **NOT COLLECTED** | EIA duoarea "SAC" - not fetched |
| Aliso Canyon Status | ❌ **NOT COLLECTED** | California Energy Commission, operational restrictions since 2015 |

### 4. PIPELINE/INFRASTRUCTURE

| Data | Status | Notes |
|------|--------|-------|
| US Pipeline Capacity | ✅ **IN DATABASE** | Annual, used in regression |
| Permian Pipeline Capacity | ❌ **NOT COLLECTED** | Matterhorn (2.5 Bcf/d, Oct 2024), Whistler, Gulf Coast Express timing |
| Southwest Pipelines to CA | ❌ **NOT COLLECTED** | El Paso, Transwestern, Kern River flows |
| LNG Feed Gas by Terminal | ❌ **NOT COLLECTED** | Freeport, Sabine Pass, Corpus Christi utilization |

### 5. WHOLESALE ELECTRICITY PRICES

| Data | Status | Notes |
|------|--------|-------|
| US Retail Prices | ✅ **IN DATABASE** | Monthly, by state |
| Texas Retail | ✅ **IN DATABASE** | Monthly |
| California Retail | ✅ **IN DATABASE** | Monthly |
| ERCOT Wholesale LMP | ❌ **NOT COLLECTED** | Requires gridstatus library (pip install gridstatus) |
| CAISO Wholesale LMP | ❌ **NOT COLLECTED** | Requires gridstatus library |
| ERCOT Zonal Prices | ❌ **NOT COLLECTED** | HB_NORTH, HB_WEST, HB_HOUSTON, etc. |

### 6. RIG COUNTS

| Data | Status | Notes |
|------|--------|-------|
| US Gas Rig Count | ✅ **IN DATABASE** | Annual, used in regression |
| Permian Rig Count | ❌ **NOT COLLECTED** | Baker Hughes weekly, needs manual download |
| Haynesville Rig Count | ❌ **NOT COLLECTED** | Baker Hughes weekly |

### 7. CALIFORNIA NON-FUEL COSTS

| Data | Status | Notes |
|------|--------|-------|
| CPUC Rate Cases | ❌ **NOT COLLECTED** | Required for CA electricity model |
| Wildfire Liability Costs | ❌ **NOT COLLECTED** | PG&E, SCE, SDG&E filings |
| Grid Hardening Costs | ❌ **NOT COLLECTED** | CPUC filings |
| RPS Compliance Costs | ❌ **NOT COLLECTED** | California Public Utilities Commission |
| Renewable Curtailment | ❌ **NOT COLLECTED** | CAISO Daily Renewables Watch |

### 8. TEMPORAL RESOLUTION

| Requirement | Status | Notes |
|-------------|--------|-------|
| Monthly gas prices | ✅ **COLLECTED** | 347 monthly records |
| Monthly electricity | ❌ **PARTIAL** | Have annual only in regression database |
| Monthly production | ❌ **NOT COLLECTED** | Would give 60 obs for Post-Pandemic vs 5 |
| Weekly storage | ❌ **NOT USED** | EIA has weekly, but used annual average in regression |

---

## Priority Actions to Fill Gaps

### HIGH PRIORITY (Most Impact on Model Accuracy)

1. **Get ERCOT wholesale prices** (vs retail)
   - Action: `pip install gridstatus` then run wholesale fetcher
   - Impact: Texas gas→electricity elasticity could change significantly

2. **Get monthly data for regression**
   - Action: Convert all series to monthly, re-run regression
   - Impact: 60 observations vs 5 for Post-Pandemic era

3. **Get Permian-specific production monthly**
   - Action: EIA STEO API, verify series ID "NGPRPPERM" or similar
   - Impact: Better explain Waha basis dynamics

### MEDIUM PRIORITY (Improves Specific Regions)

4. **Get CAISO wholesale LMP**
   - Action: gridstatus.CAISO().get_lmp()
   - Impact: Unlikely to help (CA gas-elec relationship is broken anyway)

5. **Get Pacific Region storage**
   - Action: EIA API duoarea "SAP"
   - Impact: May explain SoCal Citygate premium

6. **Get Permian pipeline capacity timeline**
   - Action: Manual research on Matterhorn, Whistler, PHP, Gulf Coast Express in-service dates
   - Impact: Explain Waha basis volatility 2019-2024

### LOWER PRIORITY (For California Non-Fuel Model)

7. **Build California non-fuel cost database**
   - Action: Scrape CPUC rate case decisions
   - Impact: Required for CA electricity model (gas price doesn't work)

8. **Get renewable curtailment data**
   - Action: CAISO Daily Renewables Watch scraper
   - Impact: Understand when gas sets marginal price in CA

---

## Summary Table

| Category | Total Items | Collected | Partial | Missing |
|----------|-------------|-----------|---------|---------|
| Gas Prices | 6 | 3 | 0 | 3 |
| Production | 5 | 1 | 3 | 1 |
| Storage | 4 | 1 | 0 | 3 |
| Infrastructure | 4 | 1 | 0 | 3 |
| Wholesale Elec | 5 | 0 | 0 | 5 |
| Rig Counts | 3 | 1 | 0 | 2 |
| CA Non-Fuel | 5 | 0 | 0 | 5 |
| **TOTAL** | **32** | **7 (22%)** | **3 (9%)** | **22 (69%)** |

---

## What the Current Monthly Database Enables

With the 347 monthly gas price records collected today, you CAN now:

1. ✅ Re-run gas→electricity regression with **monthly** data (more observations)
2. ✅ Calculate basis differentials (TX-HH, CA-HH) monthly
3. ✅ Identify seasonal patterns in gas prices
4. ✅ Detect price spikes (Uri Feb 2021, CA Jan 2023)

You CANNOT yet:

1. ❌ Run upstream factors → regional gas prices with regional factors
2. ❌ Compare wholesale vs retail electricity elasticity
3. ❌ Model California electricity prices (need non-fuel cost data)
4. ❌ Attribute Waha basis to Permian pipeline constraints

---

## Recommended Next Session

1. **Install gridstatus** and fetch ERCOT/CAISO wholesale prices
2. **Run monthly regression** with 347 monthly gas prices vs monthly electricity
3. **Search for Permian monthly production** via EIA STEO
4. **Verify Waha Hub spot price series** in EIA API

This will get you to ~50% gap coverage and significantly improve the Texas model.
