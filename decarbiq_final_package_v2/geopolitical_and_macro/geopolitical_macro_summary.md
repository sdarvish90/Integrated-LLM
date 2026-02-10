# Geopolitical & Macro Disruption Database
## DecarbIQ Intelligence Layer

**Created:** February 10, 2026  
**Data Coverage:** 1995 - 2026

---

## Event Categories

### 1. Hurricane Events (11 tracked)

Major Gulf Coast hurricanes affecting natural gas/oil production.

| Event | Date | Shut-in % | Price Impact |
|-------|------|-----------|--------------|
| Katrina | Aug 2005 | 95% | +$4.50/MMBtu |
| Rita | Sep 2005 | 100% | +$3.00/MMBtu |
| Ike | Sep 2008 | 95% | +$1.50/MMBtu |
| Ida | Aug 2021 | 96% | +$0.80/MMBtu |

**Key Insight:** Hurricane impact has declined over time. GOM share of US gas fell from 13% (2005) to 2% (2024). Post-hurricane price response now often bearish due to demand destruction exceeding supply loss.

### 2. Polar Vortex / Extreme Cold (6 tracked)

Extreme cold events causing demand spikes and supply freeze-offs.

| Event | Date | Price Spike | Deaths | Key Impact |
|-------|------|-------------|--------|------------|
| Polar Vortex 2014 | Jan 2014 | +114% | 21 | NY spot $20.77/MMBtu |
| Winter Storm Uri | Feb 2021 | +535% | 246 | TX spot $224/MMBtu, $195B damage |
| Winter Storm Elliott | Dec 2022 | +80% | 87 | Grid stress across Eastern US |

**Key Insight:** Polar vortex events have the highest price impact coefficient (+2.50) due to simultaneous demand surge and supply freeze-offs.

### 3. Geopolitical Events (8 tracked)

Wars, conflicts, and political disruptions affecting energy markets.

| Event | Date | Gas Disruption | Price Impact |
|-------|------|----------------|--------------|
| Russia-Ukraine 2009 | Jan 2009 | 6 bcfd | +$1.20/MMBtu |
| Fukushima 2011 | Mar 2011 | LNG demand surge | +$1.50/MMBtu |
| Russia-Ukraine War 2022 | Feb 2022 | 22 bcfd | +$5.00/MMBtu |
| Nord Stream Sabotage | Sep 2022 | 15 bcfd | +$2.00/MMBtu |

**Key Insight:** Russia-Ukraine War created the largest price impact in database. EU TTF peaked at €340/MWh vs typical €20-30/MWh.

### 4. LNG Market Events (7 tracked)

Supply additions, disruptions, and policy changes affecting LNG trade.

| Event | Date | Capacity | Impact |
|-------|------|----------|--------|
| Sabine Pass Start | Feb 2016 | +3.0 bcfd | US export era begins |
| Freeport Fire | Jun 2022 | -2.1 bcfd | 8-month shutdown, lowered HH |
| Qatar Expansion FID | Oct 2023 | +64 MTPA | 2025-2030 supply wave |
| Plaquemines Start | Dec 2024 | +2.6 bcfd | 8th US export facility |

**Key Insight:** US LNG export capacity to double by 2028 (11.9 → 25+ bcfd). Creates structural upward pressure on Henry Hub.

### 5. Sanctions (5 tracked)

Economic sanctions affecting energy trade.

| Sanction | Date | Target | Impact |
|----------|------|--------|--------|
| Crimea Sanctions | Mar 2014 | Russia | +$0.30/MMBtu |
| Ukraine Invasion Sanctions | Feb 2022 | Russia | +$4.00/MMBtu |
| G7 Oil Price Cap | Dec 2022 | Russia | +$0.50/MMBtu |

---

## Regression Variables Added

| Variable | Description | Months=1 |
|----------|-------------|----------|
| `hurricane_season` | June-November | 168 |
| `winter_season` | November-March | 140 |
| `major_hurricane` | Major GOM disruption months | 10 |
| `polar_vortex` | Extreme cold event months | 4 |
| `russia_ukraine_war` | Post Feb 2022 | 47 |
| `europe_energy_crisis` | Feb 2022 - Mar 2023 | 14 |
| `high_lng_competition` | Post Jan 2022 | 48 |
| `us_lng_exports` | Post Feb 2016 | 119 |

---

## Coefficient Estimates

| Factor | Coefficient | Interpretation |
|--------|-------------|----------------|
| Polar Vortex | +2.50 | Highest impact - demand+supply shock |
| Russia-Ukraine War | +0.80 | Persistent LNG demand pull |
| Major Hurricane | +0.40 | Declining impact, supply diversified |
| LNG Export Growth | +0.12/bcfd | Each 1 bcfd exports adds $0.12/MMBtu |
| Europe Crisis | +1.20 | Acute phase TTF arbitrage |

---

## Files Created

| File | Contents |
|------|----------|
| `geopolitical_macro_disruptions.csv` | All events combined (37 total) |
| `hurricane_events.csv` | Hurricane detail (11 events) |
| `polar_vortex_events.csv` | Cold events (6 events) |
| `geopolitical_events.csv` | Wars/conflicts (8 events) |
| `lng_market_events.csv` | LNG supply/demand (7 events) |
| `sanctions_events.csv` | Sanctions (5 events) |
| `master_regression_dataset_v4.csv` | Updated with disruption flags |

---

## Key Findings

1. **Polar Vortex > Hurricane** for US gas prices in modern era
2. **Russia-Ukraine War** created structural shift in global LNG markets
3. **US LNG exports** are the new demand driver (was domestic only pre-2016)
4. **Hurricane impact declining** as GOM share of production shrinks
5. **Europe-Asia competition** for US LNG creates winter price volatility

*Last Updated: February 10, 2026*
