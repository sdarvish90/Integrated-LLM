# DecarbIQ Learning Layer — Full Build Plan
**Date:** 2026-02-23  
**Scope:** Corpus-frequency claim extraction running in isolation from the production DecarbIQ pipeline.  
**Principle:** Nothing in the current `blue_h2_intelligence.db` or any existing DecarbIQ folder changes. Everything new lives in a separate folder.

---

## Folder Structure

```
corpus_frequency/
│
├── data/
│   ├── learning.db                  # New database — all learning layer tables
│   └── refetched/                   # Raw full text for the 24 truncated docs
│
├── step1_preprocess.py              # Clean boilerplate, refetch truncated docs
├── step2_schema.py                  # Create learning.db schema (run once)
├── step3_read_pass.py               # Groq extraction → claims table
├── step4_review.py                  # Print frequency distribution for your review
├── step5_contradiction.py           # Flag contradicting claims for your attention
│
├── config.py                        # Paths, API keys, thresholds — one place
├── requirements.txt                 # Dependencies
└── README.md                        # How to run each step
```

No imports from the existing DecarbIQ folder. The only external dependency
is **read access to `blue_h2_intelligence.db`** (opened read-only).

---

## Files You Need to Provide

| File | Where it is now | Why needed |
|------|----------------|------------|
| `blue_h2_intelligence.db` (v12) | Your local machine | Source of all 411 regulatory_evidence records and document URLs |
| `GROQ_API_KEY` | Environment variable | Step 3 read pass |

That is the complete list. No files from the reasoning pack are needed.  
No files from the existing DecarbIQ codebase are imported.  
The three new output files (`company_normalizer.py`, `sec_evidence_fixes_v2.py`,  
`rescore_stale_assessments_v2.py`) are not used here — they remain in the  
production pipeline unchanged.

---

## Step 1 — Pre-Processing (No LLM Cost)

**Goal:** Produce clean, complete text for every document before any LLM call runs.  
**Input:** `blue_h2_intelligence.db` (read-only)  
**Output:** `learning.db` table `cleaned_documents` with one row per source record.

### What this step does

**Task A — Boilerplate strip (25 documents)**

25 SEC filings in `regulatory_evidence` begin with `---` followed by
forward-looking disclaimer language before any real content. The existing
`_SEC_HEADER_RE` pattern in `sec_evidence_fixes_v2.py` handles this stripping,
but we reimplement it here independently — no import from production code.

Affected records (by id):
```
10-K:    3, 35
10-K/A:  9
10-Q:    1, 6, 29
8-K:     2, 4, 5
EX-99:   32
EX-99.1: 8, 10, 17, 25, 26, 27, 31, 46, 47
S-4:     13, 14
S-4/A:   15, 16, 18, 19
```

**Task B — Refetch truncated documents (24 documents)**

24 documents hit the 4000-char ceiling mid-sentence. All have valid URLs
in `document_url`. We fetch each URL, extract the first 12,000 characters
of meaningful text (after boilerplate strip), and store in `refetched/`.

Affected records (id, type, company):
```
id=3   10-K      Air Products
id=35  10-K      Green Plains Inc.
id=9   10-K/A    HNO International
id=1   10-Q      Air Products
id=6   10-Q      Plug Power
id=29  10-Q      Net Power Inc.
id=20  EX-10.1   Stepstone Private Credit Fund
id=43  EX-21.1   Enbridge Inc
id=32  EX-99     REX American Resources Corp
id=8   EX-99.1   CF Industries Holdings
id=10  EX-99.1   CF Industries Holdings
id=17  EX-99.1   Air Products & Chemicals
id=22  EX-99.1   NextDecade Corp
id=28  EX-99.1   NextDecade Corp
id=31  EX-99.1   Mercer International
id=46  EX-99.1   CF Industries Holdings
id=47  EX-99.1   CF Industries Holdings
id=12  EX-99.2   FuelCell Energy
id=13  S-4       Eagle
id=14  S-4       Eagle Nuclear Energy Corp.
id=15  S-4/A     Eagle Nuclear Energy Corp.
id=16  S-4/A     Eagle Nuclear Energy Corp.
id=18  S-4/A     Eagle Nuclear Energy Corp.
id=19  S-4/A     Eagle Nuclear Energy Corp.
```

**Task C — Qualify text**

After stripping and refetching, each document is classified:
- `CLEAN` — has ≥200 chars of content after stripping, not truncated
- `REFETCHED` — was truncated, now has full text from URL
- `SHORT` — awards and facilities (under 200 chars by design, still valid)
- `FAILED` — refetch failed or URL missing (logged, skipped in Step 3)

**Output table: `cleaned_documents`**
```sql
CREATE TABLE cleaned_documents (
    source_id       INTEGER,      -- regulatory_evidence.id
    company_name    TEXT,
    document_type   TEXT,
    document_date   TEXT,
    document_url    TEXT,
    original_chars  INTEGER,
    clean_text      TEXT,         -- boilerplate-stripped, full text if refetched
    clean_chars     INTEGER,
    quality_flag    TEXT,         -- CLEAN | REFETCHED | SHORT | FAILED
    processed_at    TEXT
);
```

**Nothing is written back to `blue_h2_intelligence.db`.**

### Acceptance criteria before moving to Step 2

- All 341 DOE awards: `SHORT` or `CLEAN`, no failures
- All 30 EPA facilities: `SHORT`, no failures
- All 24 truncated docs: `REFETCHED` with clean_chars > 4000
- All 25 boilerplate docs: boilerplate stripped, content starts after first
  real sentence
- Zero `FAILED` records (or a documented reason for each failure)

---

## Step 2 — Schema (No LLM Cost)

**Goal:** Create `learning.db` with all tables needed for Steps 3–5.  
**Input:** Nothing. Schema creation only.  
**Output:** `learning.db` with empty tables, ready for Step 3.

### Tables

**`claims`** — one row per extracted claim, from any document

```sql
CREATE TABLE claims (
    claim_id            TEXT PRIMARY KEY,   -- uuid
    source_id           INTEGER,            -- cleaned_documents.source_id
    company_name        TEXT,
    document_type       TEXT,
    document_date       TEXT,
    document_url        TEXT,
    claim_type          TEXT,               -- LLM-named, free text, no constraints
    claim_text          TEXT,               -- the actual claim verbatim from LLM
    confidence          REAL,               -- 0.0–1.0, LLM-assigned
    commitment_level    TEXT,               -- factual | indicative | speculative
    time_sensitivity    TEXT,               -- current | historical | forward_looking
    extraction_model    TEXT,               -- e.g. groq/llama-3.3-70b-versatile
    extracted_at        TEXT,
    superseded_by       TEXT,               -- claim_id of newer contradicting claim
    flagged_for_review  INTEGER DEFAULT 0,  -- 1 = needs your attention
    flag_reason         TEXT
);
```

**`scheme_frequency`** — frequency of each claim_type per document_type

```sql
CREATE TABLE scheme_frequency (
    document_type       TEXT,
    claim_type          TEXT,
    occurrence_count    INTEGER DEFAULT 0,
    first_seen          TEXT,
    last_seen           TEXT,
    PRIMARY KEY (document_type, claim_type)
);
```

**`read_pass_log`** — one row per document processed in Step 3

```sql
CREATE TABLE read_pass_log (
    source_id           INTEGER PRIMARY KEY,
    company_name        TEXT,
    document_type       TEXT,
    status              TEXT,       -- SUCCESS | SKIPPED | FAILED
    claims_extracted    INTEGER,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    cost_usd            REAL,
    error_message       TEXT,
    processed_at        TEXT
);
```

**`contradiction_log`** — flagged pairs for your review (Step 5)

```sql
CREATE TABLE contradiction_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id_a          TEXT,       -- older claim
    claim_id_b          TEXT,       -- newer claim
    company_name        TEXT,
    claim_type          TEXT,
    contradiction_type  TEXT,       -- TERMINATION | VALUE_CHANGE | STATUS_CHANGE | UNKNOWN
    auto_resolved       INTEGER,    -- 1 = system applied a rule, 0 = needs your review
    resolution          TEXT,       -- description of what was done or why flagged
    resolution_rule     TEXT,       -- rule that fired, if auto_resolved
    flagged_at          TEXT,
    reviewed_at         TEXT,       -- NULL until you act on it
    review_decision     TEXT        -- SUPERSEDE | KEEP_BOTH | IGNORE
);
```

---

## Step 3 — Read Pass (Groq, ~$0.26–$0.30)

**Goal:** Run one open-ended Groq extraction call per document.  
The LLM names claim types from content — no predefined vocabulary.  
**Input:** `cleaned_documents` (from Step 1)  
**Output:** `claims` table and `scheme_frequency` table populated.  
**`blue_h2_intelligence.db` is not touched.**

### Groq model
`llama-3.3-70b-versatile`  
Input: $0.59/1M tokens | Output: $0.79/1M tokens

### Per-document cost estimate

| Document type | Count | Avg chars | Est. cost |
|---|---|---|---|
| award | 341 | 449 | $0.20 |
| facility | 30 | 138 | $0.02 |
| EX-99.1 | 16 | 2,866 | $0.02 |
| S-4/A | 4 | 4,000+ (refetched) | $0.01 |
| 10-Q | 3 | 4,000+ (refetched) | $0.01 |
| Other SEC | 15 | varies | $0.01 |
| **TOTAL A** | **409** | — | **$0.26** |
| +24 refetched (full text) | 24 | ~8,000 | $0.04 |
| **TOTAL A+B** | — | — | **$0.30** |

### Read pass prompt (open-ended — no predefined claim types)

```
SYSTEM:
You are a document analyst. Your job is to read a regulatory filing or document
and extract every claim that carries intelligence about a company's projects,
commitments, financial position, or regulatory status.

You must NOT use any knowledge outside this document.
You name the claim type yourself from the content — do not use a predefined list.
Be specific. "project_milestone" is too vague. "construction_commenced" or
"offtake_agreement_signed" is correct.

For each claim:
- claim_type: your name for what kind of claim this is (snake_case)
- claim_text: the specific claim, in plain language, as a single sentence
- confidence: 0.0–1.0 based on the strength of language used
  - 0.9–1.0: definitive past-tense facts ("broke ground", "signed agreement")
  - 0.7–0.89: strong present-tense ("is under construction", "has committed")
  - 0.5–0.69: announced intentions ("plans to", "expects to", "is negotiating")
  - 0.3–0.49: speculative or forward-looking ("may", "could", "if completed")
  - 0.1–0.29: hedged or uncertain ("subject to", "no assurance can be given")
- commitment_level: factual | indicative | speculative
- time_sensitivity: current | historical | forward_looking

Return a JSON array. Every notable claim gets its own entry.
A single document may produce 1 claim or 20 claims — extract all of them.
Nothing should be left out, including claims with frequency=1.

USER:
Company: {company_name}
Document type: {document_type}
Document date: {document_date}

{clean_text}

Extract all claims. Return JSON array only, no commentary.
```

### Output format (one element per claim)

```json
[
  {
    "claim_type": "offtake_negotiations_announced",
    "claim_text": "Air Products announced advanced negotiations with Yara International for low emission ammonia projects in the US and Saudi Arabia.",
    "confidence": 0.55,
    "commitment_level": "indicative",
    "time_sensitivity": "current"
  },
  {
    "claim_type": "capex_guidance_issued",
    "claim_text": "Air Products issued capital expenditure guidance of approximately $4.0 billion for the fiscal year.",
    "confidence": 0.90,
    "commitment_level": "factual",
    "time_sensitivity": "forward_looking"
  }
]
```

### Processing logic

1. Read each `cleaned_documents` row with `quality_flag IN ('CLEAN', 'REFETCHED', 'SHORT')`
2. Skip `FAILED` rows, log to `read_pass_log` as `SKIPPED`
3. Call Groq with the prompt above
4. Parse JSON response
5. For each claim: insert into `claims`, then upsert into `scheme_frequency`
6. Log to `read_pass_log` with token counts and cost
7. Sleep 0.5s between calls to respect rate limits
8. If Groq returns 429: back off with retry-after header, max 3 retries
9. If JSON parse fails: store raw response in `read_pass_log.error_message`, mark `FAILED`

### Running order (cheapest/simplest first)
1. DOE awards (341, fast, cheap)
2. EPA facilities (30, fast, cheap)
3. SEC filings — non-truncated (21 records)
4. SEC filings — refetched (24 records, larger text)

---

## Step 4 — Frequency Review (Your One Human Touchpoint)

**Goal:** Print the emergent scheme landscape so you can see what the LLM found.  
**Input:** `claims` and `scheme_frequency` tables  
**Output:** Terminal report — nothing written to any database.

### What you see

**Report A — Frequency distribution by document type**

For each document type, all claim types ranked by occurrence count.
Every claim type listed, including those with count=1.

Example output:
```
=== EX-99.1 (16 documents processed) ===

PRIMARY SCHEMES (appeared in 50%+ of documents):
  8/16  capex_guidance_issued          avg_confidence=0.88
  7/16  project_milestone_announced    avg_confidence=0.72
  6/16  dividend_increase              avg_confidence=0.95

SECONDARY SCHEMES (appeared in 20–49%):
  4/16  offtake_negotiations           avg_confidence=0.58
  3/16  construction_commenced         avg_confidence=0.93

SINGLE OCCURRENCE (appeared once — may be most important):
  1/16  doe_award_at_risk              avg_confidence=0.80  → Air Products 2026-01-30
  1/16  project_writeoff_signal        avg_confidence=0.85  → Plug Power 2025-11-10
  1/16  offtake_partner_named_yara     avg_confidence=0.55  → Air Products 2026-01-30
```

**Report B — Per-company claim summary**

For each company in `unified_projects`, all claims found, sorted by date descending.

```
=== Air Products ===
  2026-01-30  EX-99.1   offtake_negotiations_announced    conf=0.55  indicative
  2026-01-30  EX-99.1   capex_guidance_issued ($4.0B)     conf=0.90  factual
  2025-09-30  10-Q      project_advance_payment_writeoff  conf=0.85  factual  ← FLAGGED
  2025-09-30  10-Q      going_concern_signal              conf=0.70  factual  ← FLAGGED
```

**Report C — Read pass statistics**

```
Total documents processed:   409
Total claims extracted:       [n]
Claims flagged for review:    [n]
Total Groq cost:              $[x.xx]
Failed documents:             [n]  (listed by id and reason)
```

### No database writes in Step 4

This step is purely read-and-display. You can run it as many times as you want
without side effects. It is your instrument for understanding what emerged.

---

## Step 5 — Contradiction Detection

**Goal:** Find claims about the same company and same claim_type that appear
to conflict, apply the one rule you're confident about, and surface everything
else for your review.  
**Input:** `claims` table  
**Output:** `contradiction_log` populated. Claims superseded where a rule fired.

### The one rule you're confident about

```
IF:
  claim_type contains 'terminat' OR 'cancel' OR 'writeoff' OR 'no_longer'
  AND same company_name as an earlier claim of the same claim_type family
  AND document_date of new claim > document_date of old claim
THEN:
  auto_resolved = 1
  Set older claim superseded_by = newer claim_id
  contradiction_type = TERMINATION
  resolution = "Later termination/writeoff supersedes earlier announcement"
```

Everything else — value changes, status changes, unknown contradictions —
gets logged to `contradiction_log` with `auto_resolved=0` and
`flagged_for_review=1` on both claims.

### Contradiction detection logic (no rule hardcoded yet)

Three lightweight checks run without any LLM call:

**Check 1 — Termination rule (auto-resolve)**
Apply the rule above. Write to `contradiction_log` as `auto_resolved=1`.

**Check 2 — Same claim_type, different claim_text, same company (flag)**
If the same company has two claims of the same `claim_type` with meaningfully
different `claim_text` (Levenshtein distance > 40% of shorter string),
flag both as `UNKNOWN` contradiction for your review.

**Check 3 — Confidence inversion (flag)**
If a later document contains a claim of the same family with confidence
significantly lower than an earlier document (drop of >0.3), flag it.
A company that had `construction_commenced` at confidence=0.93 and now has
a new claim at confidence=0.30 in the same family is a signal something changed.

### What your review looks like

```
=== CONTRADICTIONS FLAGGED FOR YOUR REVIEW ===

[1] Air Products — capex_guidance_issued
    OLDER  2024-09-30  "capex guidance $3.0 billion"  conf=0.90  [claim_id: abc123]
    NEWER  2026-01-30  "capex guidance $4.0 billion"  conf=0.90  [claim_id: def456]
    Type: VALUE_CHANGE — no auto-resolution rule exists yet
    Options: SUPERSEDE older | KEEP_BOTH (track change over time) | IGNORE

[2] NextDecade — construction_status
    OLDER  2024-06-15  "Train 1 under construction"   conf=0.93  [claim_id: ghi789]
    NEWER  2025-03-20  "Train 1 construction paused"  conf=0.75  [claim_id: jkl012]
    Type: STATUS_CHANGE — no auto-resolution rule exists yet
    Options: SUPERSEDE older | KEEP_BOTH | IGNORE
```

Each contradiction you review produces a new resolution rule candidate.
After reviewing 10–20 real contradictions from your corpus, the rule set
becomes meaningful — grounded in actual patterns you observed, not anticipated.

---

## Sequence Summary

| Step | What runs | LLM cost | Time estimate | Your involvement |
|---|---|---|---|---|
| 1 — Pre-process | Python, HTTP fetches | $0 | 10–20 min | None until acceptance check |
| 2 — Schema | Python, SQLite | $0 | 1 min | None |
| 3 — Read pass | Groq, 409 calls | $0.26–$0.30 | 30–45 min | None |
| 4 — Review | Python, terminal output | $0 | 5 min to run | You review output |
| 5 — Contradiction | Python, no LLM | $0 | 2 min | You review flagged pairs |
| **Total** | | **~$0.30** | **~1 hour** | **2 review sessions** |

---

## What Does Not Change

- `blue_h2_intelligence.db` — opened read-only, never written to
- `blue_h2_v11.db`, `blue_h2_v12.db` — not accessed
- `company_normalizer.py` — not imported
- `sec_evidence_fixes_v2.py` — not imported
- `rescore_stale_assessments_v2.py` — not imported
- Existing DecarbIQ collection scripts — not touched
- Existing reasoning system files — not touched

---

## What Success Looks Like After Step 4

You open the frequency report and see claim types that you did not anticipate
but immediately recognize as correct — because the corpus surfaced them, not
because you predicted them. The single-occurrence claims in the report are the
ones you examine most carefully, because they either represent uniquely
important signals or extraction errors — and seeing the difference is the
first calibration of the system's intelligence.

After that review, you will know:
1. Whether the open-ended Groq prompt is producing sensible claim types
2. Which document types are information-rich vs. mostly structural
3. What the primary scheme landscape looks like for EX-99.1, 10-Q, and DOE awards
4. Which single-occurrence claims deserve immediate attention in DecarbIQ

That review drives every subsequent decision about what to build next.
