# Pipeline 1: Large Load Interconnection Queue Monitor

Monitors ERCOT and Texas PUC for electricity data signals relevant to blue H2, blue ammonia, and CCS projects in the ERCOT region.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      run_pipeline.py                            │
│  Orchestrator: runs modules, deduplicates, scores, reports      │
├─────────────┬──────────────┬────────────────────────────────────┤
│  Module 2A  │  Module 2B   │  Module 2D                        │
│  ERCOT      │  PUC SB6     │  Indirect                         │
│  Public     │  Dockets     │  Triangulation                    │
├─────────────┼──────────────┼────────────────────────────────────┤
│ • Monthly   │ • 58317 SB6  │ • SEC EDGAR                       │
│   Oper.     │   umbrella   │   (Sempra, CNP, AEP)              │
│   Overview  │ • 58481 Intx │ • ERCOT Constraints               │
│ • Board     │   standards  │   Report (annual)                 │
│   present.  │ • 58479 Net  │                                   │
│ • RPG mtg   │   metering   │                                   │
│   materials │ • 58482 Reli │                                   │
│ • Large     │   ability    │                                   │
│   Load page │ • 58484 4CP  │                                   │
│   docs      │ • CCN search │                                   │
└─────────────┴──────────────┴────────────────────────────────────┘
         │              │                │
         ▼              ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Shared Utilities                            │
│  Rate-limited HTTP │ PDF extraction │ Relevance scoring         │
│  Content hashing   │ Deduplication  │ Link extraction           │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Outputs: JSON results, summary reports, downloaded PDFs        │
│  Storage: seen hashes (dedup), downloaded docs                  │
└─────────────────────────────────────────────────────────────────┘
```

## Relevance Scoring

Every document is scored for relevance to blue H2/ammonia/CCS:

- **Primary keywords** (0.15 each, max 0.75): hydrogen, ammonia, carbon capture, CCS, SMR, ATR, ASU, etc.
- **Secondary keywords** (0.05 each, max 0.25): large load, Gulf Coast counties, MW thresholds, LLIS, etc.
- Score range: 0.0 (irrelevant) to 1.0 (highly relevant)

## Installation

```bash
pip install requests beautifulsoup4 pandas lxml pdfplumber openpyxl
```

## Usage

```bash
# Run all modules
python run_pipeline.py

# Run specific module
python run_pipeline.py --module 2a   # ERCOT public docs only
python run_pipeline.py --module 2b   # PUC SB6 dockets only
python run_pipeline.py --module 2d   # Indirect sources only

# Regenerate reports from cached results
python run_pipeline.py --report-only
```

## Outputs

All outputs go to `outputs/`:

| File | Description |
|------|-------------|
| `large_load_queue_results_{timestamp}.json` | All collected items |
| `high_relevance_{timestamp}.json` | Items with relevance ≥ 0.2 |
| `new_items_{timestamp}.json` | Items not seen in previous runs |
| `summary_{timestamp}.txt` | Human-readable summary report |
| `latest_results.json` | Latest run (overwritten each run) |

## Scheduling

For automated monitoring, run via cron:

```bash
# Weekly full run (Sunday 6 AM)
0 6 * * 0 cd /path/to/pipeline_1_large_load_queue && python run_pipeline.py >> logs/pipeline.log 2>&1

# Daily PUC docket check (Mon-Fri 8 AM)
0 8 * * 1-5 cd /path/to/pipeline_1_large_load_queue && python run_pipeline.py -m 2b >> logs/puc.log 2>&1

# Monthly ERCOT overview check (1st of month)
0 7 1 * * cd /path/to/pipeline_1_large_load_queue && python run_pipeline.py -m 2a >> logs/ercot.log 2>&1
```

## Data Sources Reference

| Source | URL | Frequency | Module |
|--------|-----|-----------|--------|
| ERCOT Resource Adequacy | ercot.com/gridinfo/resource | Monthly | 2A |
| ERCOT Calendar | ercot.com/calendar | Bimonthly/Quarterly | 2A |
| ERCOT Large Load Integration | ercot.com/services/rq/large-load-integration | As updated | 2A |
| PUCT Interchange | interchange.puc.texas.gov | Daily/Weekly | 2B |
| SEC EDGAR | efts.sec.gov | Quarterly | 2D |
| ERCOT Planning | ercot.com/gridinfo/planning | Annual | 2D |

## Key Dockets Monitored

| PUCT Project | Topic | Target Rule Date |
|---|---|---|
| 58317 | SB 6 Implementation (umbrella) | Ongoing |
| 58481 | Large-load interconnection standards | Mid-2026 |
| 58479 | Net-metering / co-location | Early-mid 2026 |
| 58482 | Demand reduction programs | Late 2026 |
| 58484 | Transmission cost allocation (4CP) | Dec 2026 |

## Extending

To add a new data source:

1. Create `scrapers/scraper_XX_name.py` with a `run_all_XX()` function
2. Return `list[ScrapeResult]` using the standard dataclass from `config/settings.py`
3. Register in `run_pipeline.py` under a new module code
4. Use `utils.py` for HTTP fetching, PDF extraction, and relevance scoring
