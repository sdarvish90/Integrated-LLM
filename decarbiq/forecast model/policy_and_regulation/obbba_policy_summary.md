# OBBBA - One Big Beautiful Bill Act
## Policy Summary for DecarbIQ

**Signed:** July 4, 2025 by President Trump  
**Effective:** July 4, 2025  
**Replaces:** Inflation Reduction Act (IRA) of 2022

---

## Key Energy Provisions

### Credits TERMINATED or Accelerated Phaseout

| Credit | Section | Termination Date | Notes |
|--------|---------|------------------|-------|
| New Clean Vehicle | 30D | Sept 30, 2025 | Immediate termination |
| Used Clean Vehicle | 25E | Sept 30, 2025 | Immediate termination |
| Commercial Clean Vehicle | 45W | Sept 30, 2025 | Immediate termination |
| Residential Clean Energy | 25D | Dec 31, 2025 | Solar, geothermal for homes |
| Home Electrification | 25C | Dec 31, 2025 | Efficiency improvements |
| EV Charging Infrastructure | 30C | June 30, 2026 | Alternative fuel property |
| Wind/Solar ITC/PTC | 48E/45Y | 2028 | Must begin construction within 12 months |
| Clean Hydrogen | 45V | Dec 31, 2027 | Shortened from 2033 |
| Wind Components (45X) | 45X | Dec 31, 2027 | Manufacturing credit |

### Credits MAINTAINED or ENHANCED

| Credit | Section | Status | Notes |
|--------|---------|--------|-------|
| Nuclear (existing) | 45U | Maintained through 2031 | Plus 10% bonus |
| Nuclear (new advanced) | 45Y/48E | Maintained | Construction before 2029 |
| Carbon Capture | 45Q | Maintained | Parity for all uses at $17-36/ton |
| Clean Fuel Production | 45Z | Extended to 2029 | SAF reduced to $1/gallon |
| Advanced Manufacturing | 45X | Maintained (non-wind) | Solar, batteries, minerals |

### New Programs Created

| Program | Funding | Purpose |
|---------|---------|---------|
| Energy Dominance Financing | $1 billion | Loan guarantees for energy infrastructure |
| Strategic Petroleum Reserve | $389 million | Maintenance and acquisition |

### Key Restrictions

1. **Foreign Entity of Concern (FEOC)**: Projects with Chinese/Russian involvement barred from credits
2. **Domestic Content**: Escalating requirements (40% → 55% by 2027)
3. **60-Day Rule**: Projects must begin construction within 60 days for certain credits
4. **Methane Fees**: Postponed for 10 years

---

## Impact on DecarbIQ Model

### Variable Changes

| Variable | Description | Values |
|----------|-------------|--------|
| `post_ira` | IRA period flag | 1 for Aug 2022 - July 2025 |
| `ira_era` | Same as post_ira | Historical IRA period |
| `post_obbba` | OBBBA period flag | 1 for dates >= July 4, 2025 |

### Coefficient Estimates

| Policy | Coefficient | Effect on Gas Price |
|--------|-------------|---------------------|
| IRA (2022-2025) | -2.40 | Price reducing (clean energy support) |
| OBBBA (2025+) | +1.20 | Price increasing (reverses IRA trajectory) |

### Expected Market Effects

1. **Natural Gas**: Positive pressure
   - Reduced competition from subsidized renewables
   - Delayed methane regulations
   - Supported by Energy Dominance program

2. **Electricity (by region)**:
   - **TX/ERCOT**: Minimal change (already gas-driven)
   - **CA/CAISO**: Negative for renewables pipeline
   - **PJM/ISO-NE**: Nuclear support maintains some clean capacity

3. **Generation Mix**:
   - Wind/solar deployment slows significantly
   - Nuclear maintains support
   - Coal retirements may slow (executive orders)
   - Gas generation share likely increases

---

## Timeline

```
Aug 2022          July 2025         Sept 2025        Dec 2025         2028
    |                 |                 |                |              |
    IRA Signed        OBBBA Signed      EV Credits       Residential    Wind/Solar
                                        Terminated       Credits End    Phaseout
    |<--- IRA ERA --->|<-------------- OBBBA ERA ---------------------->|
```

---

## Sources

- IRS: One Big Beautiful Bill Provisions (irs.gov)
- Bipartisan Policy Center: Energy Provisions Summary
- Jones Day: Impact on Clean Energy Tax Credits
- Arnold & Porter: From IRA to OBBBA Analysis
- Wikipedia: One Big Beautiful Bill Act

*Last Updated: February 10, 2026*
