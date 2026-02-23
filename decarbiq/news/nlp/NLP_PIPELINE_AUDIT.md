# NLP Pipeline Audit Review — Tiers 1–6

**Audit Date:** February 20, 2026 (Tier 6 added — pipeline orchestration complete)
**Previous Audit:** February 19, 2026
**Codebase Location:** `decarbiq/news/nlp/` (Tiers 1–5), `decarbiq/news/intelligence_pipeline.py` (Tier 6)
**Database:** `blue_h2_intelligence.db` (SQLite)
**Total Code:** ~9,300 lines (7,447 across 13 NLP modules + 608 orchestrator + 1,283 across 12 source adapters)

---

## Table of Contents

1. [What Was Built](#1-what-was-built)
2. [Tier Architecture](#2-tier-architecture)
3. [File Inventory](#3-file-inventory)
4. [Tier-by-Tier Changes](#4-tier-by-tier-changes)
5. [Data Model & Schema](#5-data-model--schema)
6. [Resolution Methodology](#6-resolution-methodology)
7. [Evidence Aggregation Methodology](#7-evidence-aggregation-methodology)
8. [Current Data State](#8-current-data-state)
9. [Known Gaps & Limitations](#9-known-gaps--limitations)
10. [Expected Results & Next Steps](#10-expected-results--next-steps)

---

## 1. What Was Built

A **6-tier NLP pipeline** that takes raw text from any source (news, SEC filings, EPA permits, DOE awards, PDFs) and produces:

1. **Relevance classification** — is this document about blue H2/ammonia?
2. **Structured extraction** — entities (companies, projects, locations) and facts (status changes, capacity, investment, timelines)
3. **Canonical resolution** — "Air Products and Chemicals, Inc." → canonical key `air_products`
4. **Evidence aggregation** — link documents to unified project records, aggregate stage from multiple sources, detect evidence gaps
5. **End-to-end orchestration** — LEARN → RECOGNIZE → CONNECT pipeline with resume support, dry-run mode, gap-driven collection, and CLI/menu integration

The pipeline replaces the previous approach where each collector (news, SEC, EPA) stored extracted data in isolated tables with no cross-referencing.

---

## 2. Tier Architecture

```
Raw Text (any source)
    │
    ▼
┌──────────────────────────────────────────┐
│ Tier 1: Semantic Classifier              │  semantic_classifier.py
│   Embedding similarity → relevance score │  domain_model.py
│   Threshold: 0.55, ~5ms/article on CPU   │
└──────────────┬───────────────────────────┘
               │  relevant docs only
               ▼
┌──────────────────────────────────────────┐
│ Tier 2: Unified Processor                │  document_processor.py
│   GLiNER zero-shot NER + regex patterns  │  entity_extractor.py
│   LLM extraction with fallback           │  schema.py
│   → List[Entity], List[Fact]             │
└──────────────┬───────────────────────────┘
               │  ProcessedDocument
               ▼
┌──────────────────────────────────────────┐
│ Tier 3: Source Adapters (10 adapters)    │  pdf_structured_adapter.py
│   SEC, EPA, DOE, news, PDF structured,   │  (adapters live in parent modules)
│   PDF chunks, EPA guidance, etc.         │
│   → RawDocument → ProcessedDocument      │
└──────────────┬───────────────────────────┘
               │  entities with raw names
               ▼
┌──────────────────────────────────────────┐
│ Tier 4: Entity & Concept Resolution      │  entity_resolver.py
│   EntityRegistry (87 companies, 6 indexes│  entity_registry.py
│   ConceptRegistry (9 stages, 10 fin,     │  concept_registry.py
│   11 filings, 7 roles)                   │
│   Cascade: CIK → EPA → ticker → name →  │
│   alias → normalized → fuzzy → embedding │
└──────────────┬───────────────────────────┘
               │  resolved entities
               ▼
┌──────────────────────────────────────────┐
│ Tier 5: Evidence Aggregator              │  evidence_aggregator.py
│   Match documents → unified projects     │  project_store.py
│   Merge evidence, aggregate stage        │
│   Classify: value chain, end-use sector  │
│   Detect gaps, audit trail               │
│   SQLite: unified_projects,              │
│   project_evidence, project_audit_log    │
└──────────────┬───────────────────────────┘
               │  unified projects + gaps
               ▼
┌──────────────────────────────────────────┐
│ Tier 6: Pipeline Orchestration           │  intelligence_pipeline.py
│   LEARN → RECOGNIZE → CONNECT            │  (lives at news/ level)
│   3 pipeline variants: full, targeted,   │
│   gap-driven. Resume support, dry-run,   │
│   progress callback. CLI + menu (17–21)  │
└──────────────────────────────────────────┘
```

---

## 3. File Inventory

| File | Lines | Tier | Purpose |
|------|------:|------|---------|
| `semantic_classifier.py` | 248 | 1 | Embedding-based relevance filtering |
| `domain_model.py` | 1,023 | 1 | Persistent embedding centroids, continuous learning |
| `document_processor.py` | 486 | 2 | Unified processing pipeline for all doc types |
| `entity_extractor.py` | 890 | 2 | GLiNER + regex multi-method extraction |
| `schema.py` | 532 | 2 | Standardized data structures (ProcessedDocument, Entity, Fact) |
| `pdf_structured_adapter.py` | 363 | 3 | PDF processor output import |
| `entity_registry.py` | 737 | 4 | Company lookup with 6 indexes, YAML indentation bug handling |
| `concept_registry.py` | 324 | 4 | Stage/financial/filing/role normalization |
| `entity_resolver.py` | 464 | 4 | Orchestrator combining entity + concept registries |
| `project_store.py` | 829 | 5 | Unified project records, ValueChain/EndUseSector enums, SQLite persistence |
| `evidence_aggregator.py` | 1,195 | 5 | Document-to-project linking, stage aggregation, classification, gap detection |
| `article_store.py` | 331 | — | ChromaDB vector store for semantic dedup |
| `__init__.py` | 25 | — | Module exports (Tiers 1–5) |
| **NLP Subtotal** | **7,447** | | |

#### Orchestration (news/ level)

| File | Lines | Tier | Purpose |
|------|------:|------|---------|
| `intelligence_pipeline.py` | 608 | 6 | LEARN → RECOGNIZE → CONNECT orchestrator, CLI entry point |

| **Grand Total** | **9,338** | | *(NLP 7,447 + orchestrator 608 + sources 1,283)* |

### Configuration Files

| File | Location | Purpose |
|------|----------|---------|
| `entities.yaml` | `seed training/domains/core/` | 87 companies with canonical names, aliases, identifiers (CIK, ticker, EPA IDs), sectors |
| `concepts.yaml` | `seed training/domains/core/` | 9 project stages, 10 financial terms, 11 filing types, 7 entity roles |

---

## 4. Tier-by-Tier Changes

### Tier 1 — Semantic Classifier (pre-existing)

No changes in this session. Uses `DomainModel` with sentence-transformer embeddings (all-MiniLM-L6-v2) to classify document relevance against learned domain centroids. Threshold 0.55.

### Tier 2 — Unified Processor (pre-existing)

**Modified:** `document_processor.py` — added EntityResolver integration:
- New `entity_resolver` parameter on `__init__()`
- Lazy-loading via `_ensure_initialized()`
- Resolution call after `_extract()` in `process()`: resolved entities get `canonical_key`, `canonical_name`, `resolution_method`, `resolution_confidence`

**Modified:** `schema.py` — added resolution fields:
- `LocationMatch` dataclass (state, city, region, confidence)
- 4 new fields on `Entity`: `canonical_key`, `canonical_name`, `resolution_method`, `resolution_confidence`
- Updated `to_dict()` and `from_dict()` methods

### Tier 3 — Source Adapters (pre-existing)

No changes in this session. 10 adapters convert source-specific formats to `RawDocument`.

### Tier 4 — Entity & Concept Resolution (NEW)

Three new files created:

**`entity_registry.py`** (737 lines):
- Loads `entities.yaml` into 6 in-memory indexes (canonical, alias, normalized, CIK, ticker, EPA ID)
- Lookup cascade: exact canonical → alias → normalized → fuzzy (SequenceMatcher ≥ 0.80)
- Handles 3 YAML indentation bugs:
  1. Companies nested under other companies (yara/nutrien/plug_power/bloom_energy/nel_asa under air_liquide)
  2. `lsb_industries:` key maps to None, overwriting parent's canonical_name
  3. Gemini section companies under agencies.known_companies
- Location normalization (state abbreviation, region lookup)
- Project name normalization with optional fuzzy matching
- Runtime entity persistence via SQLite

**`concept_registry.py`** (324 lines):
- Loads `concepts.yaml` into 4 concept categories
- Stage normalization: iterates in lifecycle order, later matches override earlier
- Financial term normalization: direct → partial match
- Filing classification: direct → partial match
- Role normalization: exact → partial match

**`entity_resolver.py`** (464 lines):
- Orchestrator combining EntityRegistry + ConceptRegistry
- Resolution cascade: CIK → EPA ID → Ticker → Name-based → Embedding fallback → Unresolved
- `resolve()` method handles COMPANY, CONTRACTOR, LOCATION, PROJECT types differently
- `resolve_entities()` batch method enriches Entity objects in place
- Embedding fallback using DomainModel cosine similarity (threshold 0.75)
- Resolution audit log with `get_resolution_stats()` and `unresolved_entities` property
- Entity merging via `merge_entities()`
- `CanonicalEntity` alias for `ResolvedEntity` (spec compatibility)

### Tier 5 — Evidence Aggregator (NEW — in prior session)

Two files created:

**`project_store.py`** (829 lines):
- Data model: `ProjectStage` (enum), `ValueChain` (enum), `EndUseSector` (enum), `EvidenceItem`, `StageAssessment`, `Project`
- 3 SQLite tables: `unified_projects`, `project_evidence`, `project_audit_log`
- Independent grouping dimensions: `value_chain` (hydrogen/ammonia/ccs_ccus/lng_gas/power/methanol) and `end_use_sector` (industrial_feedstock/power_generation/transport_fuel/export_trade/storage_hub) — orthogonal to location
- SQLite migration system: `_MIGRATIONS` for ALTER TABLE + `_POST_MIGRATION_DDL` for dependent indexes
- CRUD: `get_project`, `upsert_project`, `get_all_projects`, `get_projects_by_developer`
- Evidence: `add_evidence` (ON CONFLICT dedup), `get_evidence`, `mark_evidence_stale`
- Fuzzy project matching: `find_project` via SequenceMatcher (threshold ≥ 0.75)
- Project merge: `merge_projects` — transfers evidence, deletes duplicate, audit logged
- `SOURCE_AUTHORITY` constants: SEC=0.95, EPA=0.92, DOE=0.92, regulatory=0.85, PDF=0.80, news=0.60

**`evidence_aggregator.py`** (1,195 lines):
- **Spec API**: `aggregate(doc) → List[Project]`, `detect_gaps(project) → List[str]`
- **Legacy API**: `ingest(doc) → Optional[str]`, `find_gaps() → List[Dict]`
- Signal extraction from ProcessedDocument entities/facts
- Project name inference when missing (developer + state/technology + " Project")
- Bootstrap from existing tables: `ingest_from_regulatory_evidence()`, `ingest_from_articles()` — both now log `created`/`stage_updated` audit events
- Stage aggregation with weighted voting (authority × recency × corroboration)
- Evidence gap detection (missing source types, stage-specific gaps, data completeness, conflicts)
- `STAGE_ORDER` uses enum keys: `ProjectStage.ANNOUNCED: 1` through `ProjectStage.OPERATIONAL: 8`, `CANCELLED: 0` (special case)
- **Classification**: `classify_value_chain(text)` and `classify_end_use_sector(text)` — keyword-scoring static methods with min threshold 1.0. `_classify_project()` combines all project text and runs both classifiers. `reclassify_all()` for batch reclassification.
- **EPA auto-discovery**: `auto_register_epa_ids()` — scans regulatory_evidence for EPA source rows, resolves company names, registers facility IDs in EntityResolver for future lookups
- Bug fix (Feb 20): `_to_str()` helper added to coerce LLM outputs (lists/dicts) to strings before SQLite binding — fixes `Error binding parameter 9` crash in `_build_evidence_item()`

### Tier 6 — Pipeline Orchestration (NEW)

One new file created at the `news/` level (not inside `nlp/`, since it imports from `nlp.*`, `sources.*`, and existing collectors):

**`intelligence_pipeline.py`** (608 lines):

**`PipelineResult` dataclass:**
- Accumulates stats across all stages: `stages_run`, `stage_timings` (per-stage ms), `learn_stats`, `fetch_results` (per-adapter), `raw_fetched`, `processed_created`, `filtered_irrelevant`, `skipped_duplicate`
- CONNECT stats: `projects_new`, `projects_updated`, `documents_unlinked`
- Error recovery: `last_completed_stage`, `resumable` (always True — stages are idempotent)
- `summary_text()` → human-readable CLI output with per-adapter breakdown

**`GAP_TO_ADAPTERS` constant:**
- Static mapping from 8 evidence gap flags → adapter names. Deterministic and debuggable — no LLM needed to interpret gap flags.
- Example: `no_sec_filing` → `['sec_filing']`, `missing_timeline` → `['sec_filing', 'doe_award']`

**`IntelligencePipeline` class (23 methods):**

*Constructor & initialization:*
- Dependency injection: accepts pre-built `collector`, `fid_engine`, `domain_model`, `entity_resolver` — shares instances with `BlueH2Intelligence`
- `progress_callback(stage, step, detail)` — default prints to stdout, custom callback for programmatic use
- `_ensure_initialized()` lazy-loads: DomainModel → EntityResolver → DocumentProcessor → ProjectStore → EvidenceAggregator → SourceRegistry (via `build_default_registry`)

*Stage methods (3 stages):*
- **LEARN** `learn(seed_dir=None)`: Load seed training into DomainModel + auto-register EPA IDs from `regulatory_evidence` via `auto_register_epa_ids()`
- **RECOGNIZE** — 3 variants:
  - `recognize_all(refresh)`: Fetch from all adapters (except seed_training) sorted by priority, sequential with non-fatal error handling
  - `recognize_targeted(company_name)`: Fetch from targeted adapters only (SEC, EPA, DOE)
  - `recognize_from_gaps(max_projects)`: Gap-driven — find top-N projects with most gaps, map gap flags → adapters via `GAP_TO_ADAPTERS`, deduplicate by `(adapter_name, company)` tuple, execute unique fetches
- **CONNECT** — 2 variants:
  - `connect(processed_docs, dry_run)`: Aggregate via `aggregate_batch()`
  - `connect_bootstrap(dry_run)`: Bootstrap from existing `regulatory_evidence` + `articles` tables + `reclassify_all()`

*Composite pipelines (3 entry points):*
- `run_full_pipeline(resume_from, dry_run)`: LEARN → RECOGNIZE(all) → CONNECT. `resume_from='recognize'|'connect'` skips completed stages.
- `run_targeted_pipeline(company_name, dry_run)`: LEARN → RECOGNIZE(targeted) → CONNECT for one company.
- `run_gap_pipeline(max_projects, dry_run)`: LEARN → RECOGNIZE(from_gaps) → CONNECT to fill evidence gaps.

*Core routing:*
- `_route_and_process(fetch_results, batch_size=50)`: Routes by `AdapterOutputType` — RAW → processor, PROCESSED → direct append, DOMAIN_DATA → skip (handled in LEARN). Processes raw docs in batches with progress reports.
- `_is_already_ingested(document_id)`: Checks `project_evidence` table — makes stages idempotent on re-runs.
- `_handle_fetch_error()`: Non-fatal — logs warning, detects rate limits (429), continues to next adapter.

*Query/status delegates:*
- `status()` → component availability + data state
- `project_summary()`, `find_gaps()`, `reclassify_all()` → delegate to EvidenceAggregator

*CLI entry point:*
- `python intelligence_pipeline.py [full|targeted|gaps|bootstrap|status]`
- Flags: `--resume-from [recognize|connect]`, `--dry-run`, `--max-projects N`, `--verbose`

**Menu integration (`blue_h2_intelligence.py` — ~100 lines added):**
- `_pipeline` attribute + lazy `pipeline` property sharing `self.collector` and `self.fid_engine`
- 5 new menu options (17–21) under "NLP INTELLIGENCE PIPELINE":
  - 17: Run full pipeline (LEARN → RECOGNIZE → CONNECT) — with resume prompt
  - 18: Targeted company deep-dive — prompts for company name
  - 19: Fill evidence gaps (auto-targeted) — shows preview of targeted projects
  - 20: Pipeline status & project summary — component health + data breakdown
  - 21: Bootstrap existing data into unified projects — confirmation prompt

**`nlp/__init__.py` updated (12 → 25 lines):**
- Added Tier 4 exports: `EntityRegistry`, `ConceptRegistry`, `EntityResolver`, `CanonicalEntity`
- Added Tier 5 exports: `EvidenceAggregator`, `STAGE_ORDER`, `ProjectStore`, `Project`, `ProjectStage`, `EvidenceItem`, `StageAssessment`, `ValueChain`, `EndUseSector`, `SOURCE_AUTHORITY`

**Key design decisions:**
1. File at `news/` level, not `news/nlp/` — imports from `nlp.*`, `sources.*`, `fid_probability`, `blue_h2_news_collector`
2. New menu section (17–21) parallel to existing options — preserves battle-tested Option 1 (`_collect_all`)
3. Shared instances via DI — no duplicate connections, no double rate-limit tracking
4. Static gap-to-adapter mapping — deterministic, no LLM dependency for gap interpretation
5. Idempotent stages + `resume_from` — dedup via `_is_already_ingested()`, safe to re-run after failures
6. Sequential adapter fetching with non-fatal errors — each adapter failure is a warning, pipeline continues
7. Dry-run mode — fetches and processes (to see filtering) but skips DB persistence
8. Batch processing in chunks of 50 with progress reports

---

## 5. Data Model & Schema

### ProcessedDocument (Tier 2 output)

Every document, regardless of source, produces a `ProcessedDocument` with:
- `document_id`, `document_type`, `source_url`, `source_authority`
- `entities: List[Entity]` — each with type, value, confidence, canonical resolution fields
- `facts: List[Fact]` — each with type (STATUS_CHANGE, CAPACITY, INVESTMENT, TIMELINE, etc.), claim, confidence
- `text_snippet`, `full_text_hash`, `extraction_confidence`, `flags`

### Entity Types

| Type | Examples |
|------|----------|
| COMPANY | "Air Products", "CF Industries" |
| PROJECT | "NEOM Green Hydrogen", "Louisiana Clean Energy Complex" |
| LOCATION | "Donaldsonville, LA", "Gulf Coast" |
| TECHNOLOGY | "SMR", "ATR", "Electrolysis" |
| CONTRACTOR | "Fluor", "KBR" |
| PRODUCT | "Hydrogen", "Ammonia" |
| FINANCIAL | "$2.5B investment" |
| FACILITY | "Geismar Plant" |
| AGENCY | "EPA", "DOE" |

### Fact Types

| Type | Example Claim |
|------|---------------|
| STATUS_CHANGE | "Construction underway" |
| CAPACITY | "1.2 MTPA green hydrogen" |
| INVESTMENT | "$4.5 billion committed" |
| TIMELINE | "COD expected 2027" |
| PARTNERSHIP | "JV with ACWA Power" |
| REGULATORY | "Title V permit approved" |

### Project Record (Tier 5 output)

Aggregated from multiple evidence sources:
- Identity: `project_id`, `project_name`, `developer_key`, `developer_name`
- Location: `state`, `city`, `region`
- Technical: `technology`, `product`, `capacity_raw`, `capacity_mtpa_h2`
- Grouping: `value_chain` (ValueChain enum), `end_use_sector` (EndUseSector enum) — independent of location
- Stage: `StageAssessment` (stage, confidence, evidence_count, reasoning)
- Players: `epc_contractor`, `co_developers`
- Dates: `fid_date`, `cod_date`, `construction_start`
- Evidence: `List[EvidenceItem]` — all linked documents
- Gaps: `evidence_gap_flags` — missing source types, conflicts, incomplete data

---

## 6. Resolution Methodology

### Entity Resolution Cascade (Tier 4)

```
Input: entity name + optional identifiers (CIK, ticker, EPA ID)
    │
    ├─ CIK lookup (confidence 0.99) ──────→ RegistryMatch
    ├─ EPA ID lookup (confidence 0.98) ───→ RegistryMatch
    ├─ Ticker lookup (confidence 0.97) ───→ RegistryMatch
    ├─ Exact canonical match (0.95) ──────→ RegistryMatch
    ├─ Alias match (0.90) ────────────────→ RegistryMatch
    ├─ Normalized match (0.85) ───────────→ RegistryMatch
    ├─ Fuzzy match ≥ 0.80 (scaled) ──────→ RegistryMatch
    ├─ Embedding fallback ≥ 0.75 ────────→ ResolvedEntity
    └─ Unresolved (flagged for review) ──→ ResolvedEntity(resolved=False)
```

### Registry Indexes (6 total)

| Index | Key | Example |
|-------|-----|---------|
| `_canonical_index` | lowercase canonical name | "air products" → air_products |
| `_alias_index` | lowercase alias | "apd" → air_products |
| `_normalized_index` | suffix-stripped, lowered | "air products chemicals" → air_products |
| `_cik_index` | SEC CIK number | "0000002969" → air_products |
| `_ticker_index` | stock ticker | "APD" → air_products |
| `_epa_index` | EPA facility ID | (populated from entities.yaml epa_ids) |

### YAML Indentation Bug Handling

The `entities.yaml` file has three structural bugs due to YAML indentation:

1. **Nested companies**: `yara`, `nutrien`, `plug_power`, `bloom_energy`, `nel_asa` appear indented under `air_liquide`, making them child keys instead of siblings. `_flatten_companies()` detects dicts-as-values and extracts them as separate companies.

2. **Null key overwrite**: `lsb_industries:` maps to `None`, and its `canonical_name: "LSB Industries"` overwrites `air_liquide`'s canonical_name at the parent level. Detected by checking for None-valued child keys; parent's canonical_name restored from aliases.

3. **Gemini section nesting**: Companies in the gemini section (Woodside, Wabash, etc.) end up under `agencies.known_companies` instead of the top-level companies dict. `_find_nested_companies()` recursively searches all sections for `known_companies` sub-dicts.

**Result**: 87 companies loaded correctly (up from ~70 without bug handling).

### Concept Normalization

| Category | Count | Example |
|----------|------:|---------|
| Project stages | 9 | "broke ground" → `construction`, "Final Investment Decision" → `fid` |
| Financial terms | 10 | "capital expenditure" → `capex`, "levelized cost of hydrogen" → `lcoh` |
| Filing types | 11 | "10-K" → SEC annual report, "Title V" → EPA permit |
| Entity roles | 7 | "general contractor" → `epc_contractor`, "buyer" → `offtaker` |

---

## 7. Evidence Aggregation Methodology

### Stage Aggregation Algorithm

When multiple sources report different stages for the same project:

1. **Filter**: Exclude stale evidence (`stale=True`)
2. **Weight**: Each evidence item's vote = `source_authority × recency_factor × confidence`
   - Recency: exponential decay with 90-day half-life: `e^(-0.693 × days_old / 90)`
3. **Corroboration boost**: Multiple sources agreeing amplifies confidence (1.0 → 1.25× for 3+ sources)
4. **Confidence floor**: If existing stage confidence > 0.8 and new evidence confidence < 0.6, require ≥2 new sources OR higher authority to override
5. **Cancelled override**: Any high-authority source (≥ 0.85) reporting "cancelled" wins immediately
6. **Scoring**: Highest weighted score wins

### Source Authority Weights

| Source Type | Authority | Rationale |
|------------|----------:|-----------|
| SEC filing | 0.95 | Legal obligation to accuracy |
| EPA permit | 0.92 | Government regulatory record |
| DOE award | 0.92 | Government funding decision |
| Regulatory filing | 0.85 | Official but may be preliminary |
| PDF structured | 0.80 | Formal document, verified extraction |
| PDF chunk | 0.75 | Partial document, context may be lost |
| News article | 0.60 | May contain errors, speculation |
| Unknown | 0.50 | No provenance |

### Evidence Gap Detection

Flags raised per project:

| Gap Flag | Condition |
|----------|-----------|
| `no_sec_filing` | No SEC filing evidence linked |
| `no_epa_permit` | No EPA permit evidence linked |
| `no_news_coverage` | No news article evidence linked |
| `advanced_stage_no_permit` | Construction/commissioning/operational without EPA permit |
| `fid_no_sec_filing` | FID stage without SEC filing confirmation |
| `missing_capacity` | No capacity data extracted |
| `missing_timeline` | No FID date or COD date |
| `missing_technology` | No technology type extracted |
| `stage_conflict:X\|Y` | Multiple sources claim different stages |

---

## 8. Current Data State

*Updated February 20, 2026 after re-processing.*

### Source Tables (pre-existing)

| Table | Rows | Description |
|-------|-----:|-------------|
| `articles` | 116 | News articles (25 with extraction — 4 LLM, 21 regex) |
| `regulatory_evidence` | 45 | SEC filings, EPA permits, DOE awards (10 with regex stage, 1 LLM stage) |
| `fid_assessments` | 2 | LLM-synthesized FID probability assessments |
| `projects` (old) | 2 | Consolidated projects from news only |

### Unified Tables (Tier 5)

| Table | Rows | Description |
|-------|-----:|-------------|
| `unified_projects` | 36 | Canonical project records (was 47 before cleanup) |
| `project_evidence` | 62 | Document-to-project links |
| `project_audit_log` | 84 | Change history (67 evidence_added, 6 renamed, 6 merged, 5 deleted) |

### Project Stage Distribution

| Stage | Count | % |
|-------|------:|--:|
| unknown | 29 | 81% |
| announced | 2 | 6% |
| construction | 2 | 6% |
| feed | 1 | 3% |
| operational | 2 | 6% |

### Value Chain Distribution (independent grouping dimension)

| Value Chain | Count | % |
|-------------|------:|--:|
| hydrogen | 10 | 28% |
| ammonia | 4 | 11% |
| lng_gas | 4 | 11% |
| power | 4 | 11% |
| ccs_ccus | 2 | 6% |
| **unknown** | **12** | **33%** |

### End-Use Sector Distribution (independent grouping dimension)

| End-Use Sector | Count | % |
|----------------|------:|--:|
| industrial_feedstock | 9 | 25% |
| power_generation | 5 | 14% |
| transport_fuel | 4 | 11% |
| **unknown** | **18** | **50%** |

### Evidence Type Distribution

| Document Type | Count | % |
|---------------|------:|--:|
| sec_filing | 37 | 60% |
| news | 18 | 29% |
| epa_permit | 7 | 11% |

### Entity Registry

| Index | Count | Change |
|-------|------:|--------|
| Companies loaded | 87 | — |
| Canonical index | 86 | — |
| Alias index | 64 | — |
| CIK index | 52 | — |
| Ticker index | 63 | — |
| EPA index | 7 | Was 0 — added 7 Air Products facility IDs |

### Concept Registry

| Category | Count |
|----------|------:|
| Project stages | 9 |
| Financial terms | 10 |
| Filing types | 11 |
| Entity roles | 7 |

---

## 9. Known Gaps & Limitations

*Updated February 20, 2026. Items marked RESOLVED were addressed in this session.*

### Critical

| # | Gap | Status | Impact | Notes |
|---|-----|--------|--------|-------|
| 1 | **Run full LLM extraction on remaining articles** | **TODO** | 91 articles still have no extraction (45 no text, 46 snippet-only with no regex match). 21 were regex-extracted but have messy project names. Groq daily limit (100k TPD) allows ~40 articles/day. | **Do this when Groq resets.** Re-run LLM on: (a) the 21 regex-extracted articles to get proper project names, (b) the 46 snippet-only articles that regex missed. This is the single highest-impact action remaining. |
| 2 | **29/36 projects still at stage=unknown** | Partial | After cleanup: 36 projects, 7 with known stages, 29 unknown. | Full LLM extraction on articles (gap #1) will help. Also need to fetch more source documents for these companies. |

### Resolved (previously Critical)

| # | Gap | Resolution | Date |
|---|-----|-----------|------|
| ~~1~~ | ~~29/31 projects at stage=unknown~~ | Re-processed 45 regulatory_evidence rows: regex extracted 10 stages, LLM extracted 1 more. Moved 6 projects out of unknown (3 announced, 1 construction, 1 FEED, 2 operational). | Feb 20 |
| ~~2~~ | ~~Only 2/116 articles LLM-extracted~~ | Partially resolved: 25/116 articles now extracted (4 LLM, 21 regex). 17 new projects created, 21 new evidence links. 91 articles remain unprocessed. | Feb 20 |
| ~~3~~ | ~~EPA index is empty (0 entries)~~ | Added 7 EPA ECHO facility IDs to `entities.yaml` under `air_products`. EPA index now resolves all 7 Air Products facilities (Catlettsburg, Convent, Geismar, Massena, Taft). | Feb 20 |
| ~~3b~~ | ~~Messy project names from regex extraction~~ | Cleaned up: deleted 5 false positives, merged 6 duplicates, renamed 5 headline fragments. 47 → 36 projects. | Feb 20 |

### High

| # | Gap | Impact |
|---|-----|--------|
| 4 | **No location data on most projects** | Can't group by region — but two new independent grouping dimensions (value_chain, end_use_sector) added as alternatives. Location remains sparse (15%) and will need EPA/LLM enrichment. |
| 5 | ~~**All audit log entries are `evidence_added`**~~ | **FIXED** — bootstrap methods now log `created` and `stage_updated`. Audit log breakdown: 67 evidence_added, 6 renamed, 6 merged, 5 deleted. |
| 6 | ~~**`_locations` attribute missing from EntityRegistry**~~ | **NOT A BUG** — verified: `normalize_location()` loads 50 states, 3 regions from entities.yaml and works correctly. "City, ST" and full state names both resolve. |
| 7 | ~~**Project name inference produces generic names**~~ | **PARTIALLY FIXED** — cleaned up worst offenders: 5 false positives deleted, 6 duplicates merged, 5 headline fragments renamed. Still 24 "Unnamed" projects remain — LLM extraction will help name them. |
| 8 | **EPA IDs only cover Air Products** | Other companies (CF Industries, Linde, Shell, etc.) have no EPA IDs. Added `auto_register_epa_ids()` method to auto-discover IDs from future EPA collections. Priority targets: CF Industries, Linde, Shell, ExxonMobil, Dow, Chevron, BP, Occidental, Phillips 66, Valero, Marathon. Run EPA collector with `search_epa_permits(company, state)` for each. |

### Medium

| # | Gap | Impact |
|---|-----|--------|
| ~~9~~ | ~~**No Tier 6 pipeline orchestration**~~ | **RESOLVED** — `intelligence_pipeline.py` (608 lines) created with full LEARN → RECOGNIZE → CONNECT flow, 3 pipeline variants, resume support, dry-run, CLI + menu options 17–21. |
| ~~10~~ | ~~**`__init__.py` doesn't export Tier 4/5 modules**~~ | **RESOLVED** — `nlp/__init__.py` updated (12 → 25 lines) with all Tier 4/5 exports. |
| 11 | **No embedding model fallback test coverage** | Embedding resolution path untested in bootstrap |
| 12 | **fid_assessments table (2 rows) not bootstrapped** | Existing FID probability data not linked to unified projects |

---

## 10. Expected Results & Next Steps

### What the Pipeline Enables

With all 6 tiers operational, the system can:

1. **Cross-reference evidence**: An SEC 8-K mentioning "construction underway" + an EPA permit for the same facility + a news article about the groundbreaking → unified project with `stage=construction`, `confidence=0.95`, 3 evidence sources

2. **Detect intelligence gaps**: Project at FID stage but no SEC filing → flag for targeted SEC EDGAR search

3. **Track stage progression**: As new evidence arrives, stage confidence updates automatically with audit trail

4. **Prioritize collection**: `find_all_gaps()` identifies projects needing more evidence, enabling targeted fetches

5. **Run end-to-end pipeline**: `python intelligence_pipeline.py full` runs LEARN → RECOGNIZE → CONNECT in one command. `--dry-run` for testing, `--resume-from recognize` to skip completed stages, `gaps` for gap-driven collection targeting the projects with the most missing evidence

### Immediate Next Steps

*Updated Feb 20. Completed items struck through.*

| Priority | Action | Expected Impact | Status |
|----------|--------|-----------------|--------|
| **P0** | **Run full LLM extraction on ~67 remaining articles** (when Groq resets) | Proper project names for 21 regex-extracted articles + new extractions from 46 snippet-only articles. Budget: ~2 days at 100k TPD. | **DO TOMORROW** |
| ~~P0~~ | ~~Re-process regulatory_evidence through Tier 2~~ | ~~Move projects from unknown to actual stages~~ | Done (Feb 20): regex+LLM moved 6 projects |
| ~~P1~~ | ~~Enrich entities.yaml with EPA IDs~~ | ~~Enable EPA resolution~~ | Done (Feb 20): 7 Air Products IDs added |
| ~~P0~~ | ~~Clean up false-positive projects and merge duplicates~~ | ~~Reduce noise~~ | Done (Feb 20): 5 deleted, 6 merged, 5 renamed. 47 → 36 projects |
| **P1** | Discover EPA IDs for other companies (CF Industries, Linde, Shell, etc.) | Expand EPA resolution beyond Air Products | |
| ~~P1~~ | ~~Implement Tier 6 pipeline orchestration~~ | ~~End-to-end automation~~ | Done (Feb 20): `intelligence_pipeline.py` (608 lines) + menu options 17–21 |
| **P2** | Bootstrap `fid_assessments` into unified projects | Link existing FID probabilities | |
| **P2** | Deduplicate projects (merge "Unnamed" with facility-named) | Consolidate duplicates | Partially blocked on LLM extraction |
| **P3** | Add location data enrichment from EPA permits | Enable regional grouping and analysis | |

### What Changed on Feb 20

1. **Regulatory evidence re-processed** — All 45 rows run through regex stage extraction (10 matched) + LLM pass on remaining 33 (1 more matched). 6 projects moved out of `unknown`: 2 announced (Eagle Nuclear), 1 construction (NextDecade), 1 FEED (Mercer), 2 operational (Air Products, Venture Global). Investment data enriched on 11 projects.

2. **Articles regex-extracted** — 21 new articles extracted via regex pattern matching (Groq TPD limit blocked LLM path). 17 new projects created, 21 evidence links added. 1 new construction (Yara ammonia "breaking ground"), 1 new operational (false positive). Quality: project names from regex are noisy (headline fragments).

3. **EPA IDs populated** — 7 EPA ECHO facility IDs added to `entities.yaml` for Air Products (Catlettsburg KY, Convent LA x2, Geismar LA x2, Massena NY, Taft LA). EPA index now functional.

4. **Bug fix** — `_to_str()` helper added to `evidence_aggregator.py` to handle LLM returning non-string types (lists/dicts) in numerical_value fields. Prevented `Error binding parameter 9` SQLite crash.

5. **Grouping dimensions added** — Two new independent classification columns on `unified_projects`: `value_chain` (hydrogen/ammonia/ccs_ccus/lng_gas/power/methanol) and `end_use_sector` (industrial_feedstock/power_generation/transport_fuel/export_trade/storage_hub). Keyword-based classifiers in `evidence_aggregator.py` populate these from evidence text. Auto-classified on all new ingestions. These are independent of location — all three (location, value chain, end-use) can be queried separately.

6. **Project cleanup** — Deleted 5 false positives (transmission line, Stepstone fund, headline fragments). Merged 6 duplicates (enbridge/Enbridge Inc, Net Power/Net Power Inc., Green Plains/Green Plains Inc., Eagle/Eagle Nuclear, Air Products sub-keys). Renamed 5 headline-fragment projects to proper names. 47 → 36 clean projects.

7. **Audit log fixed** — Bootstrap methods (`ingest_from_regulatory_evidence`, `ingest_from_articles`) now log `created` and `stage_updated` events, not just `evidence_added`.

8. **EntityRegistry location check** — Verified: `_locations` gap was a non-issue. `normalize_location()` correctly loads 50 states + 3 regions from entities.yaml and resolves "City, ST", full state names, and abbreviations.

9. **EPA auto-discovery** — Added `auto_register_epa_ids()` method to `EvidenceAggregator`. When EPA collector fetches data for new companies, their facility IDs auto-register in the EntityResolver for future lookups. Priority targets: CF Industries, Linde, Shell, ExxonMobil, Dow, Chevron, BP, Occidental, Phillips 66, Valero, Marathon.

10. **Tier 6 pipeline orchestration** — Created `intelligence_pipeline.py` (608 lines) at the `news/` level. `IntelligencePipeline` class with LEARN → RECOGNIZE → CONNECT 3-stage architecture. Three pipeline variants: `run_full_pipeline()` (all adapters), `run_targeted_pipeline(company)` (SEC/EPA/DOE for one company), `run_gap_pipeline(max_projects)` (gap-driven targeted collection). Features: `resume_from` for partial restarts after failures, `dry_run` for testing without DB writes, `_is_already_ingested()` dedup for idempotent re-runs, non-fatal adapter error handling with rate limit (429) detection, `progress_callback` for programmatic use, batch processing in chunks of 50. CLI entry point with argparse (`full|targeted|gaps|bootstrap|status`). Menu integration: 5 new options (17–21) in `blue_h2_intelligence.py` sharing collector + fid_engine via lazy `pipeline` property. `nlp/__init__.py` updated (12 → 25 lines) with all Tier 4/5 exports.

### Verification Commands

```bash
cd decarbiq/news

# Full import check (Tiers 1–6)
python3 -c "
from nlp.project_store import ProjectStore, Project, ProjectStage, EvidenceItem, StageAssessment
from nlp.entity_resolver import EntityResolver, CanonicalEntity
from nlp.evidence_aggregator import EvidenceAggregator, STAGE_ORDER
from nlp.entity_registry import EntityRegistry
from nlp.concept_registry import ConceptRegistry
from intelligence_pipeline import IntelligencePipeline, PipelineResult, GAP_TO_ADAPTERS
print('All Tier 1-6 imports OK')
"

# Pipeline status (reads DB, no collection)
python3 intelligence_pipeline.py status

# Pipeline summary
python3 -c "
from nlp.evidence_aggregator import EvidenceAggregator
agg = EvidenceAggregator()
s = agg.project_summary()
print(f'Projects: {s[\"total_projects\"]}')
print(f'Evidence: {s[\"total_evidence_links\"]}')
print(f'By stage: {s[\"by_stage\"]}')
print(f'Gaps: {s[\"projects_with_gaps\"]} projects with gaps')
"

# Entity resolution spot check
python3 -c "
from nlp.entity_resolver import EntityResolver
from nlp.schema import EntityType
r = EntityResolver()
for name in ['Air Products', 'CF Industries', 'apd', 'Plug Power Inc.']:
    result = r.resolve(name, EntityType.COMPANY)
    print(f'{name:30s} → {result.canonical_name} ({result.resolution_method}, {result.resolution_confidence:.2f})')
"

# Dry-run full pipeline (fetch + process, no DB writes)
python3 intelligence_pipeline.py full --dry-run

# Gap-driven pipeline (targets top 5 projects with most missing evidence)
python3 intelligence_pipeline.py gaps --max-projects 5

# Targeted company deep-dive
python3 intelligence_pipeline.py targeted "Air Products"

# Bootstrap existing data into unified projects
python3 intelligence_pipeline.py bootstrap

# Menu integration (select option 20 for status)
python3 blue_h2_intelligence.py
```
