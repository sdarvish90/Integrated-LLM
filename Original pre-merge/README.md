# Hydrogen Intelligence Integrated System

## Overview

This system integrates four components into a unified pipeline for hydrogen project intelligence:

```
┌─────────────────┐     ┌────────────────────┐     ┌─────────────────────┐
│  website_dl.py  │────▶│ llm_training_      │────▶│ hydrogen_           │
│                 │     │ system.py          │     │ intelligence_       │
│ Downloads PDFs, │     │                    │     │ monitor.py          │
│ reports, data   │     │ Ingests docs,      │     │                     │
│                 │     │ builds knowledge   │     │ Collects news,      │
└─────────────────┘     │ base (vectors)     │     │ analyzes impact     │
                        │                    │     │                     │
                        │ Extracts           │     │ Uses TEA baseline   │
                        │ sensitivity data   │     │ from tea_baseline_  │
                        └────────────────────┘     │ adapter.py          │
                                                   └─────────────────────┘
                                                            │
                                                            ▼
                                                   ┌─────────────────────┐
                                                   │ GROUNDED ANALYSIS   │
                                                   │ • No AI guessing    │
                                                   │ • Full citations    │
                                                   │ • Audit trail       │
                                                   └─────────────────────┘
```

## Key Feature: Grounded Analysis (No AI Guessing)

The critical differentiator of this system is that **all impact conclusions are derived ONLY from data in the knowledge database**, with explicit citations to source documents.

When the system says "NPV increases by 4%", it provides:

1. **The sensitivity factor used** (e.g., "$10/MWh electricity price change → $0.05/kg LCOH")
2. **Source document** (e.g., "IEA_Green_Hydrogen_Cost_Report_2024.pdf, page 47")
3. **The calculation applied** (e.g., "News mentions $5/MWh reduction → 0.5 × $0.05 = $0.025/kg LCOH improvement")
4. **Confidence score** (based on how well the source data matches the news context)

If the knowledge base doesn't have relevant sensitivity data for a news item, the system explicitly flags this as a **data gap** rather than making up numbers.

## Components

### 1. `website_dl.py` - Document Downloader

Downloads documents from configured sources:
- IEA, IRENA, World Bank reports
- Government publications (Oman, Chile, etc.)
- Industry association reports
- Research papers

**Output**: PDFs, Excel files, and data files in `./downloads/`

### 2. `llm_training_system.py` - Knowledge Base Builder

Processes downloaded documents:
- Extracts text from PDFs, Word, Excel
- Chunks text for semantic search
- Builds vector embeddings (ChromaDB + SentenceTransformers)
- Extracts sensitivity analysis data

**Output**: 
- Vector database in `./knowledge_base_db/`
- Sensitivity factors in `./sensitivity_analysis.db`

### 3. `tea_baseline_adapter.py` - TEA Baseline Loader

Reads SHARE model outputs:
- LCOH, LCOA, LCOE
- NPV, IRR, CAPEX
- Production capacity

**Output**: Baseline dict with project economics

### 4. `hydrogen_intelligence_monitor_v3.py` - News Collector

Collects hydrogen news from:
- 30+ RSS feeds
- Deep web search (DuckDuckGo, Google News)
- News aggregators (Hydrogen Insight, Recharge News, etc.)
- Arabic sources (Oman-specific)

**Output**: Articles in `./hydrogen_monitor.db`

### 5. `hydrogen_integrated_system.py` - Orchestrator (NEW)

Ties everything together:
- Runs pipeline in correct order
- Provides grounded impact analysis
- Maintains audit trail
- Generates reports with citations

## Installation

```bash
# Core dependencies
pip install requests beautifulsoup4 feedparser pyyaml
pip install PyPDF2 pandas python-docx openpyxl
pip install sentence-transformers chromadb
pip install anthropic  # For LLM analysis (optional)

# Optional: Translation for Arabic
pip install googletrans==4.0.0rc1
```

## Usage

### Full Pipeline

```bash
# Run everything: download → ingest → analyze
python hydrogen_integrated_system.py --mode full --region Oman --days 7
```

### Individual Steps

```bash
# Step 1: Download documents
python hydrogen_integrated_system.py --mode download

# Step 2: Ingest documents and build knowledge base
python hydrogen_integrated_system.py --mode ingest

# Step 3: Analyze news (uses existing KB)
python hydrogen_integrated_system.py --mode analyze --region Oman --days 14
```

### Interactive Mode

```bash
python hydrogen_integrated_system.py --mode interactive
```

### Original Tools (Still Available)

```bash
# Download documents directly
python website_dl.py --discover --regions "Oman,Chile"

# Build knowledge base directly
python llm_training_system.py --mode ingest --folder ./downloads
python llm_training_system.py --mode build_kb

# Run news monitor directly
python hydrogen_intelligence_monitor_v3.py
```

## Configuration

### sites.yaml (for website_dl.py)

```yaml
keywords:
  - hydrogen
  - electrolyzer
  - LCOH
  - green ammonia

output_dir: ./downloads

sites:
  - name: IEA Hydrogen Reports
    type: generic_scraper
    start_urls:
      - https://www.iea.org/reports?topic=hydrogen
```

### Environment Variables

```bash
# For LLM-powered analysis (optional)
export ANTHROPIC_API_KEY=your_key_here
```

### IntegratedConfig Class

Modify `hydrogen_integrated_system.py` for custom paths:

```python
@dataclass
class IntegratedConfig:
    downloads_dir: Path = Path("./downloads")
    knowledge_base_dir: Path = Path("./knowledge_base_db")
    news_db_path: Path = Path("./hydrogen_monitor.db")
    tea_locations_root: Path = Path("/path/to/SHARE_Model")
```

## Output Structure

### Impact Report (JSON)

```json
{
  "summary": {
    "region": "Oman",
    "articles_analyzed": 25,
    "articles_with_quantified_impact": 8,
    "total_npv_change_usd_million": 15.3,
    "total_lcoh_change_usd_per_kg": -0.045,
    "baseline_npv": 580,
    "baseline_lcoh": 1.90,
    "net_npv_after_news": 595.3,
    "net_lcoh_after_news": 1.855
  },
  "citation_summary": {
    "total_citations": 12,
    "unique_source_documents": [
      "IEA_Global_Hydrogen_Review_2024.pdf",
      "IRENA_Green_Hydrogen_Cost_2023.pdf"
    ],
    "citation_details": [...]
  },
  "detailed_results": [
    {
      "article": {
        "title": "IRA 45V Tax Credit Final Rules Released",
        "url": "...",
        "category": "CRITICAL"
      },
      "impact": {
        "has_quantifiable_impact": true,
        "npv_change": 23.5,
        "lcoh_change": -0.12,
        "confidence": 0.85,
        "citations": [
          {
            "source": "IEA_Global_Hydrogen_Review_2024.pdf",
            "parameter_used": "subsidy_rate",
            "sensitivity_applied": "$1/kg subsidy → $0.8/kg LCOH reduction",
            "news_value_used": 3.0,
            "calculated_impact": "$0.12/kg LCOH reduction"
          }
        ],
        "reasoning": "Based on IEA_Global_Hydrogen_Review_2024.pdf: subsidy_rate change of $3.0/kg → LCOH change of -$0.12/kg"
      }
    }
  ]
}
```

### Audit Trail (SQLite)

```sql
-- Every analysis is logged for accountability
SELECT * FROM analysis_audit;

-- Shows:
-- - analysis_date
-- - article_title
-- - article_url
-- - npv_change_claimed
-- - lcoh_change_claimed
-- - supporting_documents (list)
-- - confidence_score
-- - reasoning
```

## Sensitivity Data Structure

The system extracts and stores sensitivity relationships from your training documents:

```sql
-- Example sensitivity factors
SELECT * FROM sensitivity_factors;

-- Results:
-- | parameter_name     | change_amount | change_unit | lcoh_impact | source_document                    |
-- |--------------------|---------------|-------------|-------------|-------------------------------------|
-- | electricity_price  | 10            | $/MWh       | 0.05        | IEA_Green_Hydrogen_Cost_2024.pdf   |
-- | electrolyzer_capex | 10            | %           | 0.08        | IRENA_Electrolyser_Analysis.pdf    |
-- | capacity_factor    | 5             | % points    | 0.15        | Lazard_LCOE_Report_2024.pdf        |
```

## How Grounded Analysis Works

### Step 1: News Classification
```
Article: "Government announces $2/kg production subsidy for green hydrogen"
Category: POLICY
Affected Parameters: [subsidy_rate, tax_credit]
```

### Step 2: Value Extraction from News
```
Extracted: subsidy_rate = $2.0/kg
```

### Step 3: Sensitivity Lookup
```
Knowledge Base Query: sensitivity_factors WHERE parameter_name = 'subsidy_rate'
Found: "$1/kg subsidy → $0.8/kg LCOH reduction" (source: IEA_2024.pdf, confidence: 0.85)
```

### Step 4: Impact Calculation
```
News Value: $2.0/kg subsidy
Sensitivity: $1/kg → -$0.8/kg LCOH
Calculation: 2.0 × (-0.8) = -$1.6/kg LCOH impact
```

### Step 5: Citation Generation
```json
{
  "source": "IEA_Global_Hydrogen_Review_2024.pdf",
  "parameter_used": "subsidy_rate",
  "sensitivity_applied": "$1/kg subsidy → -$0.8/kg LCOH",
  "news_value_used": 2.0,
  "calculated_impact": "-$1.6/kg LCOH change"
}
```

### Step 6: If No Data Available
```json
{
  "has_quantifiable_impact": false,
  "data_gaps": [
    "Could not extract subsidy_rate value from news. Have sensitivity data from IEA_2024.pdf but news text was ambiguous."
  ]
}
```

## Troubleshooting

### "No sensitivity data found"

1. Run the ingest step: `python hydrogen_integrated_system.py --mode ingest`
2. Check that downloads folder has relevant TEA/cost reports
3. View available data: `python hydrogen_integrated_system.py --mode interactive` → Option 5

### "TEA baseline not loaded"

1. Check `tea_locations_root` path in config
2. Ensure SHARE model outputs exist in expected folders
3. System falls back to defaults if TEA unavailable

### "No articles found"

1. Run news collection first: `python hydrogen_intelligence_monitor_v3.py` → Option 1
2. Check date range (try `--days 30` for more data)

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    hydrogen_integrated_system.py                  │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────┐   ┌─────────────────┐                     │
│  │ HydrogenIntel-   │   │ IntegratedNews- │                     │
│  │ ligencePipeline  │──▶│ Analyzer        │                     │
│  └──────────────────┘   └────────┬────────┘                     │
│           │                      │                              │
│           │              ┌───────▼───────┐                      │
│           │              │ GroundedImpact│                      │
│           │              │ Analyzer      │                      │
│           │              └───────┬───────┘                      │
│           │                      │                              │
│  ┌────────▼────────┐    ┌───────▼───────┐   ┌─────────────────┐│
│  │ KnowledgeBase-  │    │sensitivity_   │   │ tea_baseline_   ││
│  │ Manager         │───▶│analysis.db    │   │ adapter.py      ││
│  └─────────────────┘    └───────────────┘   └─────────────────┘│
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│                        External Components                        │
├────────────┬─────────────────┬─────────────────┬─────────────────┤
│website_dl  │llm_training_    │hydrogen_intel-  │tea_baseline_    │
│.py         │system.py        │ligence_monitor  │adapter.py       │
│            │                 │_v3.py           │                 │
│Downloads   │Ingests &        │Collects news    │Loads SHARE      │
│documents   │builds KB        │                 │model outputs    │
└────────────┴─────────────────┴─────────────────┴─────────────────┘
```

## License

This system integrates multiple components. Refer to individual file headers for specific licensing.
