# DecarbIQ Learning Layer — Corpus Frequency

Corpus-frequency claim extraction running in isolation from the production DecarbIQ pipeline.
Reads `blue_h2_intelligence.db` (read-only). All output goes to `data/learning.db`.

## Setup

```bash
pip install -r requirements.txt
```

Groq API key: set `GROQ_API_KEY` env var, or place it in `groq_api_token.txt`.

## Running

Run each step in order. Steps 1 and 2 have no LLM cost.

```bash
# Step 1 — Strip boilerplate, refetch truncated docs, qualify text
python step1_preprocess.py

# Step 2 — Create schema (safe to re-run)
python step2_schema.py

# Step 3 — Groq extraction (~$0.30, ~30-45 min)
python step3_read_pass.py
# To resume from a specific source_id:
python step3_read_pass.py 100

# Step 4 — Print frequency reports (no writes, run as many times as you want)
python step4_review.py

# Step 5 — Detect contradictions, auto-resolve terminations
python step5_contradiction.py
# Interactive review mode — prompts for your decision on each flagged pair:
python step5_contradiction.py --review
```

## What does not change

- `blue_h2_intelligence.db` — opened read-only, never written to
- No imports from the existing DecarbIQ codebase
- No files outside `corpus_frequency/` are touched
