"""
DecarbIQ Learning Layer — Configuration
All paths, API keys, and thresholds in one place.
"""

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent          # Learn/
ROOT_DIR = BASE_DIR.parent                          # corpus_frequnecy/
DATA_DIR = ROOT_DIR / "data"
REFETCHED_DIR = DATA_DIR / "refetched"

LEARNING_DB = DATA_DIR / "core_database.db"          # all new tables

KNOWLEDGE_DIR = BASE_DIR / "knowledge"
REGULATORY_GLOSSARY = KNOWLEDGE_DIR / "regulatory_glossary.yaml"

# ── Anthropic API (primary) ──────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY") or (
    (ROOT_DIR / "anthropic_api_token.txt").read_text().strip()
    if (ROOT_DIR / "anthropic_api_token.txt").exists()
    else None
)
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
CLAUDE_INPUT_COST_PER_M = 0.80   # $/1M input tokens
CLAUDE_OUTPUT_COST_PER_M = 4.00  # $/1M output tokens

# ── Groq API (fallback) ─────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY") or (
    (ROOT_DIR / "groq_api_token.txt").read_text().strip()
    if (ROOT_DIR / "groq_api_token.txt").exists()
    else None
)
# Available Groq models (set GROQ_MODEL env var to override):
#   meta-llama/llama-4-maverick-17b-128e-instruct — Llama 4 Maverick, 128 experts, best reasoning (default)
#   qwen/qwen3-32b               — strong reasoning, good at analytical tasks
#   moonshotai/kimi-k2-instruct  — best reasoning (MoE 1T params), analytical
#   meta-llama/llama-4-scout-17b-16e-instruct — Llama 4 Scout, 16 experts, faster
#   llama-3.3-70b-versatile       — good general, literal instruction-following
GROQ_MODEL = os.environ.get("GROQ_MODEL", "meta-llama/llama-4-maverick-17b-128e-instruct")
GROQ_INPUT_COST_PER_M = 0.59   # $/1M input tokens (varies by model)
GROQ_OUTPUT_COST_PER_M = 0.79  # $/1M output tokens

# ── Step 1 thresholds ────────────────────────────────────────────────────────
TRUNCATION_CEILING = 4000       # excerpt_char_count == this means truncated
MIN_CONTENT_CHARS = 200         # below this → quality_flag = SHORT
REFETCH_MAX_CHARS = 12_000      # max chars to keep from a refetched document
SEC_HEADER_PATTERN = r"^---\s*\n[\s\S]*?(?=\n[A-Z])"  # boilerplate strip regex

# ── Step 3 rate limiting ─────────────────────────────────────────────────────
GROQ_SLEEP_BETWEEN_CALLS = 0.5  # seconds
GROQ_MAX_RETRIES = 3

# ── Step 5 contradiction thresholds ──────────────────────────────────────────
LEVENSHTEIN_THRESHOLD = 0.40    # flag if edit distance > 40% of shorter string
CONFIDENCE_DROP_THRESHOLD = 0.30  # flag if confidence drops by more than 0.3