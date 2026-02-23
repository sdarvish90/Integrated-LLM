# Blue H2 / Ammonia Intelligence Monitor — System Audit

**Audit Date:** February 18, 2026
**Codebase Location:** `decarbiq/news/`
**Database:** `blue_h2_intelligence.db` (SQLite)
**Vector Store:** `chroma_store/` (ChromaDB)

---

## 1. What This System Does

This is an **automated market intelligence platform** for the blue hydrogen and ammonia industry. It continuously collects news articles, SEC filings, EPA permits, DOE awards, and regulatory filings, then extracts structured project data (developer, capacity, technology, status, location) and assesses the probability of each project reaching Final Investment Decision (FID).

The system connects extracted intelligence to a trained **DecarbIQ energy model** to quantify market impacts: incremental gas demand (bcf/d), Henry Hub price sensitivity ($/MMBtu), ERCOT/CAISO power price impacts, and levelized cost of hydrogen (LCOH).

**Key outputs:**
- Deduplicated project pipeline with FID probabilities
- Gas market impact quantification (model-backed, not guesswork)
- HTML intelligence reports with executive summaries
- Competitive landscape dashboards
- Pluggable client extension reports (demo: refractory materials market sizing)

---

## 2. System Architecture

```
                        +-----------------------+
                        |  blue_h2_intelligence |  (Interactive Menu — 16 options)
                        |     .py (4,215 lines) |  + Background collection daemon
                        +----------+------------+
                                   |
            +----------------------+----------------------+
            |                      |                      |
  +---------v----------+  +--------v---------+  +---------v---------+
  | blue_h2_news_      |  | news_market_     |  | fid_probability   |
  | collector.py       |  | signal_parser.py |  | .py (3,360 lines) |
  | (2,942 lines)      |  | (2,554 lines)    |  |                   |
  | News collection +  |  | Signal extract + |  | SEC/EPA/DOE/Regs  |
  | 5-layer NLP pipe   |  | project dedup    |  | evidence + LLM    |
  +--------+-----------+  +--------+---------+  +--------+----------+
           |                       |                      |
           |              +--------v---------+            |
           |              | decarbiq_market_  |            |
           |              | connector.py      |            |
           |              | (458 lines)       |            |
           |              | Model bridge      |            |
           |              +--------+----------+            |
           |                       |                      |
  +--------v---------+    +--------v----------+           |
  | nlp/ pipeline    |    | report_generator  |           |
  | semantic_        |    | .py (548 lines)   |           |
  | classifier.py    |    | HTML reports      |           |
  | entity_          |    +-------------------+           |
  | extractor.py     |                                    |
  | article_store.py |    +-------------------+           |
  +---------+--------+    | llm_client.py     +-----------+
            |              | (238 lines)       |
            v              | Groq + Ollama     |
      +----------+        +-------------------+
      | ChromaDB |
      | chroma_  |
      | store/   |
      +----------+
```

---

## 3. File Inventory

| File | Lines | Purpose |
|------|-------|---------|
| `blue_h2_intelligence.py` | 4,215 | Interactive CLI menu, background daemon, LLM assessments |
| `blue_h2_news_collector.py` | 2,942 | News collection engine + 5-layer NLP pipeline |
| `news_market_signal_parser.py` | 2,554 | Article-to-signal extraction + project deduplication |
| `fid_probability.py` | 3,360 | FID probability engine (multi-source regulatory evidence + LLM) |
| `decarbiq_market_connector.py` | 458 | Bridges news signals to trained DecarbIQ energy model |
| `report_generator.py` | 548 | HTML intelligence report generation |
| `llm_client.py` | 238 | Dual-backend LLM client (Groq cloud + Ollama local) |
| `nlp/semantic_classifier.py` | 220 | Layer 1: Embedding-based relevance classifier |
| `nlp/entity_extractor.py` | ~600 | Layers 2+3: GLiNER/GLiREL entity & relation extraction |
| `nlp/article_store.py` | 332 | Layer 4: ChromaDB vector store wrapper |
| `sites.yaml` | ~50KB | RSS feed definitions + keyword taxonomy |
| `config/domains/hydrogen_ammonia.yaml` | ~400 | NLP domain config (examples, entity types, regions) |
| `config/refractory_client.yaml` | ~100 | Demo client extension config |

---

## 4. Development History — What Changed

### Phase 1: Foundation (Original)

The system started with a straightforward architecture:

- **Collection:** RSS feeds + web search + basic scrapers
- **Scoring:** Keyword-based categorization (CRITICAL/HIGH/MEDIUM/LOW) using a 350+ keyword taxonomy organized into tiers
- **Extraction:** Ollama LLM (llama3.1:8b) called on **every article** (~2 seconds per article)
- **Storage:** SQLite only
- **Deduplication:** URL hash + content hash (exact match only)

**Performance:** ~200 seconds for 100 articles (dominated by LLM calls)

### Phase 2: Signal Parser + FID Engine

Added structured intelligence extraction:

- **Signal parser** (`news_market_signal_parser.py`): Converts raw articles into structured `ProjectSignal` objects with 20+ fields. Includes 13 specific fixes for edge cases (nuclear SMR disambiguation, capacity unit context, word-boundary matching, etc.)
- **FID Probability Engine** (`fid_probability.py`): Multi-source regulatory evidence collection (SEC EDGAR, EPA ECHO, DOE awards, regulations.gov) with LLM-synthesized probability assessments
- **DecarbIQ connector** (`decarbiq_market_connector.py`): Bridges signals to the trained energy model for quantified market impact
- **Report generator** (`report_generator.py`): 7-section HTML reports
- **Client extension system**: Pluggable architecture with YAML-driven configuration (demo: refractory materials)

### Phase 3: Source Improvements

Several targeted fixes to data quality:

- **SEC filing text fetching**: Changed from reading index pages to fetching actual filing documents (10-K, 10-Q, 8-K full text)
- **SEC EDGAR expansion**: Added 10-K and 10-Q support (not just 8-K), with 90-day lookback for periodic filings and form-type-aware scoring
- **Source article excerpts**: Options 4, 5, 7 now show relevant text excerpts from source articles
- **Full text for all sources**: Every data source now attempts full text extraction before LLM processing
- **Smart truncation**: 30K character limit preserving beginning (25K) + end (5K) of long articles

### Phase 4: 5-Layer Semantic NLP Pipeline (v3)

**This is the major architectural change.** Replaced the keyword scoring + LLM-on-every-article approach with a 5-layer pipeline:

| Layer | Technology | Speed | Purpose |
|-------|-----------|-------|---------|
| 1 | sentence-transformers (all-MiniLM-L6-v2) | ~5ms/article | Embedding-based relevance classification |
| 2 | GLiNER (zero-shot NER) | ~30ms/article | Entity extraction without training |
| 3 | GLiREL (zero-shot RE) | ~30ms/article | Relationship extraction |
| 4 | ChromaDB (cosine similarity) | ~1ms/article | Semantic deduplication + persistent storage |
| 5 | Ollama LLM | ~2s/article | Refinement — **only for HIGH-impact articles (~5-10%)** |

**Performance improvement:** ~200s → ~6s for 100 articles (33x faster)

**Key design decisions:**
- **Graceful fallback**: If NLP dependencies are missing (`_NLP_AVAILABLE=False`), the old keyword+LLM pipeline still works
- **Lazy model loading**: Heavy models (sentence-transformers: 80MB, GLiNER: ~500MB) only load on first use
- **Consistent embeddings**: All ChromaDB vectors use title+snippet embeddings (never full_text) so similarity comparisons are valid
- **Borderline rescoring**: Articles scoring 0.45-0.65 get rescored with full_text[:1000] for better accuracy
- **Batch operations**: Batch embedding, batch ChromaDB dedup instead of N+1 individual queries

### Phase 5: Audit + 33 Bug Fixes

A comprehensive code audit identified 33 issues (10 Critical, 15 Important, 8 Minor). All critical and most important issues were fixed:

**Critical fixes:**
| ID | Issue | Fix |
|----|-------|-----|
| C1 | Borderline rescoring replaced the embedding vector, creating inconsistent ChromaDB entries | Score-only rescoring; embedding stays as title+snippet |
| C2 | `classify_batch()` skipped borderline rescoring entirely | Added second-pass batch rescoring |
| C3 | `'saf' in text` matched "safe"/"safety" (false positives) | Word-boundary regex `\bsaf\b` |
| C4 | GW→MTPA conversion: 0.16 in code but 0.15 in LLM prompt | Aligned to 0.16 everywhere |
| C5 | GLiNER truncated to 5K chars (83% of text unsearched) | Chunked processing: 4 chunks x 4500 chars = 18K |
| C6 | N+1 ChromaDB queries per article during dedup | New `batch_dedup()` method — single batch query |
| C7 | URL hash mismatch: SQLite used SHA256, ChromaDB used MD5 | Both now use SHA256 |
| C8 | Full-text fetch happened BEFORE ChromaDB dedup (wasted bandwidth) | Dedup moved before fetch |
| C10 | Articles scored by keywords then re-scored by pipeline (wasted work) | Source collectors skip keyword scoring when NLP available |

**Important fixes:**
- Singleton now checks domain parameter (I1)
- Entity extraction finds ALL occurrences, not just first (I3)
- Consistent casing normalization (I4)
- Region detection uses word boundaries (I5)
- Date extraction checks all occurrences (I6)
- `add_batch` logs skipped articles (I7)
- Search uses pipeline's embedding model, not ChromaDB's default (I8)
- Consolidated duplicate logic (I9)
- SEC keyword dedup with set (I10)
- Market impact requires relevance >= 0.50 (I11)
- dateutil import moved outside loop (I13)

### Phase 6: Incremental Saves + Background Collection

Two reliability improvements:

1. **Incremental persistence**: Each article is saved to SQLite + ChromaDB immediately after entity extraction and scoring — not in one batch at the end. If the process crashes at article #47 of 100, articles #1-46 are already safely persisted.

2. **Background collection daemon**: A daemon thread auto-starts with the monitor and runs:
   - RSS collection every 15 minutes (lightweight, no LLM)
   - Full collection every 2 hours (all sources + LLM for HIGH articles)
   - Thread-safe: mutex lock prevents concurrent collections
   - Graceful shutdown on exit or Ctrl+C

---

## 5. Data Flow — How Option 1 Works

```
User selects Option 1: Full Collection
│
├─ 1. COLLECT from all sources
│  ├─ RSS feeds (sites.yaml driven, tag-activated)
│  ├─ SEC EDGAR 8-K/10-K/10-Q (per-company monitoring)
│  ├─ Deep web search (31 queries, 5 pages each)
│  └─ Web scrapers (known aggregator sites)
│
├─ 2. DEDUPLICATE against database
│  ├─ URL hash (SHA256) — exact match
│  └─ Content hash (title+snippet[:200]) — cross-source dedup
│
├─ 3. 5-LAYER SEMANTIC PIPELINE (if NLP available)
│  │
│  ├─ Layer 1: Embedding Classification
│  │  ├─ Encode all articles with all-MiniLM-L6-v2 (~5ms each)
│  │  ├─ Compare to domain centroid (built from 61 positive + 24 negative examples)
│  │  └─ Filter: keep articles with cosine similarity >= 0.55
│  │
│  ├─ Layer 4: Semantic Dedup (before fetch — saves bandwidth)
│  │  ├─ Batch query ChromaDB for near-duplicates (cosine > 0.95)
│  │  └─ Remove articles already semantically present
│  │
│  ├─ Fetch Full Text (only for surviving articles)
│  │  ├─ newspaper3k extraction
│  │  ├─ Paywall detection
│  │  └─ Smart truncation (25K beginning + 5K end)
│  │
│  ├─ Layers 2+3: Entity Extraction (per-article, saved immediately)
│  │  ├─ GLiNER zero-shot NER (chunked, up to 18K chars)
│  │  │  → developer, project_name, capacity, technology, location, status, EPC, dates, money
│  │  ├─ GLiREL relation extraction
│  │  │  → develops, located_in, capacity_of, uses_technology, contracted_by
│  │  ├─ Regex fallbacks for technology/status/capacity normalization
│  │  ├─ Priority scoring: base(relevance*50) + developer(+10) + FID/EPC(+15) + capacity(+10) + deal>$100M(+10)
│  │  ├─ Market impact classification: HIGH / MEDIUM / LOW
│  │  └─ ** SAVE immediately to SQLite + ChromaDB ** (incremental persistence)
│  │
│  └─ Layer 5: LLM Refinement (HIGH-impact only, ~5-10% of articles)
│     ├─ Ollama llama3.1:8b structured extraction
│     ├─ Re-score priority after LLM enrichment
│     └─ UPDATE already-saved SQLite rows + ChromaDB metadata
│
├─ 4. STORE
│  ├─ SQLite: articles table (32 columns including all llm_* fields)
│  ├─ ChromaDB: embeddings + metadata (semantic search ready)
│  └─ source_stats: per-source performance tracking
│
└─ 5. SUMMARY
   ├─ Category breakdown (CRITICAL / HIGH / MEDIUM / LOW)
   ├─ Total articles stored
   ├─ Entities extracted count
   └─ ChromaDB total count
```

---

## 6. Data Sources

### News Collection (blue_h2_news_collector.py)

| Source | Method | Frequency | Articles/Run |
|--------|--------|-----------|--------------|
| RSS Feeds | sites.yaml tag-driven, feedparser | Every 15 min (bg) | 50-200 |
| Google News | RSS + base64/HTTP URL resolver | Every 15 min (bg) | 20-50 |
| Web Search | 31 curated queries, 5 pages each | Every 2 hrs (bg) | 50-150 |
| Web Scrapers | Known aggregator sites | Every 2 hrs (bg) | 10-30 |
| SEC EDGAR | Per-company 8-K/10-K/10-Q monitoring | Every 2 hrs (bg) | 5-20 |

### Regulatory Evidence (fid_probability.py)

| Source | Method | Lookback | Purpose |
|--------|--------|----------|---------|
| SEC EDGAR EFTS | 4-strategy keyword discovery (33 queries) | 180 days initial, 7 days refresh | Corporate filings, project announcements |
| EPA ECHO | SIC/NAICS code + keyword search | Ongoing | Facility permits, environmental compliance |
| DOE USASpending | Award keyword search | 365 days initial, 30 days refresh | Federal funding, grants |
| DOE LPO | Portfolio page scraping | Manual | Loan Programs Office projects |
| DOE H2Hub | Project list scraping | Manual | Hydrogen hub consortium data |
| Regulations.gov | FERC/EPA/PHMSA/DOE queries | Ongoing | Regulatory filings, permit applications |

---

## 7. Database Schema

### articles (main table — 32+ columns)
```
id, title, url, url_hash, content_hash,
source, snippet, full_text,
category, priority_score, market_impact,
published_date, fetched_date,
alerted, keywords, region, subregion,
investigated, paywall_blocked,
llm_extracted, llm_project_name, llm_developer, llm_co_developers,
llm_status, llm_status_confirmed,
llm_capacity_raw, llm_capacity_mtpa_h2,
llm_technology, llm_product, llm_epc_contractor,
llm_location_state, llm_location_subregion, llm_region,
llm_fid_date, llm_cod_date, llm_deal_value_usd
```

### projects (deduplicated pipeline)
```
id, project_name, developer, co_developers,
capacity_mtpa_h2, capacity_raw, capacity_unit_uncertain,
technology, product,
location_state, location_subregion, region,
status, fid_date, cod_date, epc_contractor,
fid_probability, confidence,
last_updated, source_article_ids, source_article_titles, source_article_urls
UNIQUE(project_name, developer, region, technology)
```

### regulatory_evidence (FID engine)
```
id, company_name, company_cik,
source, document_type, document_id, document_url, document_date,
fetched_date, raw_text_excerpt, full_text_hash, content_hash, excerpt_char_count,
state, stale, stale_reason,
discovery_method, stage, stage_confidence, stage_reasoning,
fid_date, construction_start, operations_start, milestones, project_name
UNIQUE(company_name, source, document_id)
```

### fid_assessments (cached LLM assessments)
```
id, company_name, project_name,
assessed_date, evidence_ids, evidence_summary,
llm_backend, llm_raw_response,
stage, fid_probability, confidence,
key_evidence_points, reasoning,
stale, stale_reason, prompt_version, llm_tokens_used
```

### Supporting tables
- **competitors**: project_name, company, region, capacity_mw, status, fid_date, cod_date
- **offtakers**: company_name, type, region, total_demand_kt, secured_kt, available_kt
- **risks**: risk_category, risk_description, status, trend, impact_level, probability
- **deals**: deal_date, deal_type, parties, value_usd, region, description, signal
- **source_stats**: source_name, total_articles, high_priority_articles, avg_score, last_fetched
- **collection_runs**: run_type, started_at, completed_at, items_found, items_new

---

## 8. Interactive Menu (16 Options)

```
--- NEWS COLLECTION ---
 1. Run full collection          (RSS + search + scrapers + SEC + 5-layer pipeline)
 2. RSS-only collection          (lightweight, with semantic pipeline)
 3. View recent articles         (generates HTML audit report)

--- MARKET SIGNALS ---
 4. Parse recent articles        (extract ProjectSignals + deduplicate + FID assess)
 5. Analyze existing DB          (parse + FID engine, no new collection)
 6. View competitive landscape   (developer / capacity / technology breakdown)

--- DECARBIQ MARKET ASSESSMENT ---
 7. Assess recent news impact    (quantified via DecarbIQ model)
 8. Gas price outlook            (P10/P50/P90 Henry Hub, 2025-2035)
 9. Cumulative pipeline impact   (aggregate gas demand from all projects)
10. Sensitivity explorer         (what-if analysis, LLM-selected variables)

--- DASHBOARDS ---
11. Competitive intelligence dashboard
12. Deal flow tracker
13. Risk register

--- REPORTS ---
14. Generate HTML intelligence report
15. Generate client extension report

--- CLIENT EXTENSIONS ---
16. Configure client extension module

 0. Exit
```

---

## 9. LLM Architecture

### Dual-Backend Design (llm_client.py)

| Backend | Model | Context | Speed | Cost |
|---------|-------|---------|-------|------|
| **Groq** (primary) | llama-3.3-70b-versatile | 128K tokens | ~1s/call | Free tier (30 req/min) |
| **Ollama** (fallback) | llama3.1:8b | 128K tokens | ~2s/call | Free (local CPU) |

- **Rate limiter**: 28 req/min to stay under Groq's 30 limit
- **Daily limit handling**: Auto-switches to Ollama when Groq daily limit hit
- **Automatic failover**: If Groq fails, falls back to Ollama transparently

### Where LLM Is Used

| Feature | Backend | Purpose |
|---------|---------|---------|
| Layer 5 extraction (HIGH articles only) | Ollama | Structured entity extraction from article text |
| FID probability assessment | Groq → Ollama | Synthesize regulatory evidence into stage + probability |
| News impact assessment (Option 7) | Groq → Ollama | Per-signal significance, credibility, risk analysis |
| Batch market outlook | Groq → Ollama | Aggregate market summary from all signals |
| Sensitivity explorer | Groq → Ollama | Variable selection based on news context |

---

## 10. NLP Domain Configuration

**File:** `config/domains/hydrogen_ammonia.yaml`

### Embedding Classifier
- **Model:** all-MiniLM-L6-v2 (384-dim, ~80MB)
- **Positive examples:** 61 representative article titles/snippets
- **Negative examples:** 24 off-topic examples (generic energy, unrelated chemicals)
- **Relevance threshold:** 0.55
- **Borderline range:** 0.45-0.65 (rescored with full_text[:1000])

### Entity Types (GLiNER)
company, project_name, capacity, technology, product, location, status, epc_contractor, date, money

### Relation Types (GLiREL)
develops, located_in, capacity_of, uses_technology, contracted_by, produces, invested_in

### Normalization Rules
- **Technology:** SMR, ATR, Electrolysis, CCUS (regex pattern matching)
- **Status:** 9 levels (Announced → Pre-FEED → FEED → FID → EPC Award → Construction → Commissioning → Operational) + Cancelled/Delayed
- **Capacity conversions:**
  - 1 TPD H2 = 0.000365 MTPA
  - 1 MTPA NH3 = 0.178 MTPA H2 equivalent
  - 1 GW electrolyzer = 0.16 MTPA H2 (50% capacity factor)
- **Region classification:** 9 US regions (Gulf Coast TX, Gulf Coast LA, Appalachia, Midwest, Mountain West, Pacific NW, California, Northeast, Southeast) + International

---

## 11. Market Quantification (DecarbIQ Connector)

All market impact numbers come from the trained DecarbIQ model, not hardcoded estimates.

### Literature-Sourced Constants
| Parameter | Value | Source |
|-----------|-------|--------|
| Blue H2 LCOH | $1.80/kg | IEA, NETL (SMR+CCS, Gulf Coast) |
| Gas feedstock cost | $0.80/kg | At $4/MMBtu Henry Hub |
| 45Q credit impact | $0.79/kg | IRC §45Q, 85% capture rate |
| 45V max credit | $3.00/kg | IRC §45V, lifecycle CI < 0.45 |
| Gas intensity (SMR) | 0.155 bcf/d per MTPA H2 | NETL 2022 |
| Gas intensity (ATR) | 0.135 bcf/d per MTPA H2 | IEAGHG 2017 |
| Gas price sensitivity | $0.14/MMBtu per bcf/d | DecarbIQ model coefficient |

### Model Integration
- Reads projection maps (baseline + scenarios)
- Reads Monte Carlo results (10,000 paths, regime-switching GARCH)
- Reads sensitivity analysis matrix (50+ variables)
- Multi-shock scenario analysis

---

## 12. Client Extension System

### Architecture
Abstract base class `ClientExtension` with four required methods:
- `analyze_signal(signal, assessment)` → per-project analysis
- `analyze_pipeline(projects, assessments)` → full pipeline analysis
- `generate_report_section(pipeline_analysis)` → HTML for report
- `get_questions()` → list of business questions answered

### Demo: Refractory Materials Extension
**Config:** `config/refractory_client.yaml`

Answers 5 questions for refractory materials suppliers:
1. Which projects are likely to proceed? (FID probability >= 25%)
2. When to expect equipment orders? (status-based lead times)
3. Which regions to prioritize? (capacity x FID probability ranking)
4. Who are the EPC contractors? (partnership targets)
5. What is the addressable market size? (P10/P50/P90 via FID weighting)

**Cost basis:** Harbison-Walker (2019) + CEPCI 2024 escalation
- SMR primary reformer: $45K/MTPA
- SMR secondary reformer: $20K/MTPA
- ATR reformer: $55K/MTPA
- Ammonia converter: $15K/MTPA NH3

---

## 13. Signal Parser — 13 Fixes Applied

| # | Fix | Problem Solved |
|---|-----|----------------|
| 1 | Real project name extraction | Was inferring generic names like "Hydrogen Project" |
| 2 | Capacity-mismatch dedup safeguard | Projects with different capacities incorrectly merged |
| 3 | Full-text extraction for parsing | Only parsing title+snippet missed project details |
| 4 | Context-aware status detection | Hedge words ("planned", "proposed") misread as confirmed |
| 5 | Multi-developer/JV handling | Joint ventures treated as single developer |
| 6 | Three-layer confidence model | Confidence = completeness x article quality x corroboration |
| 7 | Capacity unit disambiguation | H2 vs NH3 resolved by context proximity (±200 chars) |
| 8 | Known-project database | Canonical naming for ~20 tracked projects |
| 9 | Nuclear SMR disambiguation | "SMR" in nuclear context not tagged as hydrogen |
| 10 | Capacity validation | Unrealistic values (>10 MTPA) rejected or flagged |
| 11 | Word-boundary matching | "fid" no longer matches "confidence", "feed" no longer matches "feeding" |
| 12 | Relevance filter | Skip signals without developer + project specifics |
| 13 | Known-project technology override | If known project has ATR, don't override to SMR from article text |

---

## 14. Performance Characteristics

### Before v3 Pipeline
| Metric | Value |
|--------|-------|
| 100 articles total time | ~200 seconds |
| Per article | ~2,000 ms |
| LLM calls | Every article |
| Deduplication | Exact hash only |
| Persistence | Batch at end (all-or-nothing) |

### After v3 Pipeline
| Metric | Value |
|--------|-------|
| 100 articles total time | ~6 seconds |
| Per article | ~60 ms |
| LLM calls | HIGH-impact only (~5-10%) |
| Deduplication | Exact hash + semantic (cosine 0.95) |
| Persistence | Incremental (per-article, crash-safe) |

### Background Collection
| Task | Frequency | Duration | LLM |
|------|-----------|----------|-----|
| RSS collection | Every 15 min | ~30s | No |
| Full collection | Every 2 hrs | ~5 min | HIGH only |

---

## 15. Dependencies

### Core
```
feedparser>=6.0          # RSS parsing
requests>=2.28           # HTTP
PyYAML>=6.0              # Config files
beautifulsoup4>=4.12     # HTML parsing
newspaper3k>=0.2.8       # Article full-text extraction
python-dateutil>=2.8     # Date parsing
certifi                  # SSL certificates
```

### 5-Layer NLP Pipeline
```
sentence-transformers>=2.2.0   # Layer 1: Embeddings (80MB model)
gliner>=0.2.5                  # Layer 2: Zero-shot NER (~500MB model)
glirel>=0.1.0                  # Layer 3: Zero-shot RE
chromadb>=0.4.0                # Layer 4: Vector store
spacy>=3.7.0                   # Supporting NLP
numpy>=1.24                    # Array operations
```

### LLM Backends
```
groq (API)                     # Cloud LLM (free tier, 30 req/min)
ollama (local)                 # Local LLM (llama3.1:8b, free)
```

---

## 16. Known Limitations & Unaddressed Issues

These were identified during the audit but deprioritized:

| ID | Issue | Impact |
|----|-------|--------|
| I2 | Negative centroid weight (0.3) not calibrated | Minor accuracy impact |
| I12 | `collect_all` / `collect_rss_only` code duplication | Maintainability |
| I15 | ChromaDB/SQLite not transactionally consistent | Edge case: crash between the two stores |
| M4 | content_hash uses only first 200 chars of title | Theoretically possible collision |
| M5 | warm_up_ollama called multiple times | Minor startup overhead |
| M7 | URL cache unbounded in memory | Only matters for very long runs |

---

## 17. Security & API Keys

- **Groq API key**: Stored in `groq_api_token.txt` (local file, not committed)
- **No other API keys required**: SEC EDGAR, EPA ECHO, regulations.gov are public APIs
- **SQLite**: Local file, no network exposure
- **Ollama**: Runs on localhost only
- **No credentials in code**: All sensitive values loaded from files

---

## 18. How to Run

```bash
cd decarbiq/news/

# Install dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# Optional: install GLiNER/GLiREL for full NLP pipeline
pip install gliner glirel

# Run the interactive monitor
python blue_h2_intelligence.py
```

The system will:
1. Show the interactive menu immediately
2. Start background RSS collection (first run after 15 minutes)
3. Wait for your selection

**Option 1** runs the full 5-layer semantic pipeline with incremental saves.
**Option 7** is the most comprehensive analysis (collection + parsing + FID + model assessment + LLM).
