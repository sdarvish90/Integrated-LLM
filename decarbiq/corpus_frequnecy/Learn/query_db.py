#!/usr/bin/env python3
"""
DecarbIQ — query_db.py
Natural-language interface to core_database.db via LLM-generated SQL.

The LLM can ONLY answer from DB data. No guessing, no hallucination.
If the answer is not in the database, it says so.

Usage:
    python query_db.py                        # interactive mode
    python query_db.py "how many active projects?"  # single question
"""
from __future__ import annotations

import json
import readline
import sqlite3
import sys
from pathlib import Path

import yaml

from config import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL,
    GROQ_API_KEY, GROQ_MODEL,
    LEARNING_DB, REGULATORY_GLOSSARY,
)

# ── LLM backend selection ────────────────────────────────────────────────────
# Try Groq first (free/cheap), fall back to Anthropic.
BACKEND = None

if GROQ_API_KEY:
    try:
        import groq
        groq_client = groq.Groq(api_key=GROQ_API_KEY)
        BACKEND = 'groq'
    except ImportError:
        pass

if BACKEND is None and ANTHROPIC_API_KEY:
    import anthropic
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    BACKEND = 'anthropic'

if BACKEND is None:
    print('ERROR: No API key found. Set ANTHROPIC_API_KEY or GROQ_API_KEY.')
    sys.exit(1)


def _llm_call(system: str, messages: list[dict], max_tokens: int = 2000) -> str:
    """Unified LLM call — routes to Groq or Anthropic."""
    if BACKEND == 'groq':
        # Groq uses OpenAI-compatible chat format
        chat_msgs = [{'role': 'system', 'content': system}] + messages
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=chat_msgs,
            max_tokens=max_tokens,
            temperature=0,
        )
        return resp.choices[0].message.content
    else:
        resp = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return resp.content[0].text

MAX_ROWS_DISPLAY = 50
MAX_RESULT_CHARS = 8000


def _get_schema(db: sqlite3.Connection) -> str:
    """Extract full DB schema for the LLM system prompt."""
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()

    schema_parts = []
    for (tname,) in tables:
        if tname.startswith('sqlite_') or tname.endswith('_fts') or tname.endswith('_content') \
                or tname.endswith('_segments') or tname.endswith('_segdir') \
                or tname.endswith('_docsize') or tname.endswith('_stat') \
                or tname.endswith('_idx') or tname.endswith('_data'):
            continue

        cols = db.execute(f'PRAGMA table_info("{tname}")').fetchall()
        row_count = db.execute(f'SELECT COUNT(*) FROM "{tname}"').fetchone()[0]

        col_defs = []
        for col in cols:
            _, cname, ctype, notnull, default, pk = col
            parts = [f'  {cname} {ctype}']
            if pk:
                parts.append('PRIMARY KEY')
            if notnull:
                parts.append('NOT NULL')
            col_defs.append(' '.join(parts))

        schema_parts.append(
            f'-- {tname} ({row_count:,} rows)\n'
            f'CREATE TABLE {tname} (\n' +
            ',\n'.join(col_defs) +
            '\n);'
        )

    return '\n\n'.join(schema_parts)


def _get_sample_data(db: sqlite3.Connection) -> str:
    """Get sample values from key tables."""
    samples = []

    # Key enum-like columns
    queries = [
        ("Source systems", "SELECT DISTINCT source_system FROM regulatory_evidence ORDER BY source_system"),
        ("Technology types", "SELECT DISTINCT technology FROM unified_projects WHERE technology IS NOT NULL ORDER BY technology"),
        ("Stages", "SELECT DISTINCT stage FROM unified_projects WHERE stage IS NOT NULL ORDER BY stage"),
        ("Bootstrap sources", "SELECT DISTINCT bootstrap_source FROM unified_projects WHERE bootstrap_source IS NOT NULL"),
        ("Claim types (top 20)", "SELECT claim_type, COUNT(*) c FROM claims GROUP BY claim_type ORDER BY c DESC LIMIT 20"),
        ("Alias types", "SELECT DISTINCT alias_type FROM entity_aliases WHERE alias_type IS NOT NULL ORDER BY alias_type"),
        ("Crossref methods", "SELECT match_method, COUNT(*) c FROM facility_crossref GROUP BY match_method"),
        ("Active projects", "SELECT project_name, developer_name, state, stage, technology FROM unified_projects WHERE quarantined=0 ORDER BY project_name LIMIT 40"),
    ]

    for label, sql in queries:
        try:
            rows = db.execute(sql).fetchall()
            if rows:
                vals = [str(tuple(r)) for r in rows[:25]]
                samples.append(f'-- {label}:\n' + '\n'.join(vals))
        except Exception:
            pass

    return '\n\n'.join(samples)


def _load_glossary() -> str:
    """Load regulatory glossary YAML, format as plain text for the LLM."""
    if not REGULATORY_GLOSSARY.exists():
        return ''

    with open(REGULATORY_GLOSSARY, 'r') as f:
        kb = yaml.safe_load(f)

    if not kb:
        return ''

    category_titles = {
        'permits': 'PERMITS & REGULATORY FRAMEWORK',
        'source_systems': 'SOURCE SYSTEMS (NON-PERMIT)',
        'ghgrp_subparts': 'GHGRP REPORTING SUBPARTS',
        'claim_types': 'CLAIM TYPES — INTERPRETING DATABASE FIELDS',
        'technology': 'TECHNOLOGY CLASSIFICATIONS',
        'funding': 'DOE FUNDING PROGRAMS & TAX CREDITS',
        'stages': 'PROJECT STAGES & SIGNALS',
        'data_quality': 'ENTITY RESOLUTION & DATA QUALITY',
        'epa_programs': 'EPA FACILITY REGISTRY & PROGRAM ACRONYMS',
        'tceq': 'TCEQ-SPECIFIC IDENTIFIERS',
    }

    sections = []
    for key, title in category_titles.items():
        entries = kb.get(key, [])
        if not entries:
            continue

        lines = [f'--- {title} ---']
        for entry in entries:
            if entry.get('superseded_date'):
                continue
            topic = entry.get('topic', '')
            defn = entry.get('definition', '').strip()
            eff = entry.get('effective_date', '')
            lines.append(f'[{topic}] (effective: {eff})\n{defn}')

        sections.append('\n\n'.join(lines))

    return '\n\n'.join(sections)


SYSTEM_PROMPT_TEMPLATE = """You are a database analyst for DecarbIQ, a hydrogen/CCS/ammonia project tracking database.

You have ONE source of data: the SQLite database.
You also have a GLOSSARY of regulatory definitions to help you interpret database results. The glossary provides context for understanding what database fields and values mean — it is NOT a source of answers on its own.

RULES — FOLLOW STRICTLY:

DATABASE RULES:
1. ALL factual claims about projects, counts, and status MUST come from SQL queries.
2. Always show the SQL you ran and the raw results before your interpretation.
3. For numeric answers, include exact counts/values from the query.
4. If a query returns 0 rows, say that explicitly — don't infer or fill in.
5. Use the schema and sample data below to write correct SQL. Do not reference tables or columns that don't exist.

GLOSSARY RULES:
6. Use the glossary ONLY to explain/interpret database results.
7. If a question is purely definitional ("what is Class VI?"), answer from the glossary and label it: "From the glossary:"
8. NEVER combine glossary context with database results to infer facts not in the database.
9. For questions like "which projects have X, and what does X mean?" — run SQL for the data part, then use the glossary to explain what the results mean.

REASONING RULES:
10. When the user asks "WHY" a project is at a certain stage, or asks you to explain/justify
    a result, do NOT just re-run the same query. Instead, INVESTIGATE the evidence:
    a. Look at the project's CLAIMS: JOIN project_claims → claims to see what claim_types exist
    b. Look at the project's SOURCE SYSTEMS: JOIN claims → cleaned_documents to see which
       regulatory systems have data on this project
    c. Look at DOE status: JOIN claims → doe_status if relevant
    d. Then use the glossary to INTERPRET what those claims/sources mean for the project's stage
    e. Example: "Why is X in announced stage?" → query its claims and source_systems, then
       explain: "This project has TCEQ air permit claims and ERCOT GIS claims, which per the
       glossary indicate pre-construction activity consistent with 'announced' stage."
11. When asked to "list projects and explain why", include evidence columns in your SELECT:
    JOIN to get claim_type counts, source_system counts, or key claims alongside project fields.
12. When a project's stage seems inconsistent with its evidence (e.g., stage='unknown' but has
    Class VI permits), FLAG this: "Note: this project has Class VI claims suggesting at least
    'feasibility' stage, but is currently marked 'unknown'."

GENERAL RULES:
13. NEVER guess, speculate, or use knowledge beyond the database and glossary.
14. If neither source can answer: "This information is not available in the database or glossary."

COLUMN VALUE RULES:
15. ONLY use column values that ACTUALLY APPEAR in the SAMPLE DATA below. NEVER invent or guess values.
16. If the user asks about a concept (e.g. "blue hydrogen"), map it to actual DB values using the CONCEPT MAPPING below. If no mapping exists, use LIKE '%keyword%' to search broadly.
17. When unsure about an exact value, first run a discovery query: SELECT DISTINCT column FROM table WHERE column LIKE '%keyword%'

CONCEPT MAPPING (user terms → actual DB values):
- "blue hydrogen" / "blue H2" → technology IN ('hydrogen_production', 'steam_methane_reforming', 'autothermal_reforming') — H2 from natural gas with CCS
- "green hydrogen" / "green H2" → technology IN ('electrolysis', 'green_hydrogen')
- "pre-FID" / "pre-FEED" → stage IN ('announced', 'feasibility') — there is NO 'pre-FID' or 'pre-FEED' stage value
- "post-FID" → stage IN ('fid', 'permitted', 'construction')
- "early stage" → stage IN ('announced', 'feasibility', 'awarded')
- "late stage" / "mature" → stage IN ('fid', 'permitted', 'construction', 'operational')
- "CCS" / "carbon capture" → technology LIKE '%carbon_capture%' OR technology LIKE '%ccs%' OR technology = 'point_source_ccs' OR technology = 'direct_air_capture'
- "ammonia" → technology = 'ammonia_production' OR project_name LIKE '%ammonia%'
- "active" → quarantined = 0
- "quarantined" / "removed" → quarantined = 1

REGULATORY GLOSSARY:
{glossary}

DATABASE SCHEMA:
{schema}

SAMPLE DATA — these are the ONLY valid values (use ONLY these in WHERE clauses):
{samples}

KEY RELATIONSHIPS:
- unified_projects.project_id → project_claims.project_id → claims.claim_id
- claims.source_id → cleaned_documents.source_id (and regulatory_evidence.source_id)
- unified_projects.company_id → companies.company_id
- entities.entity_id → entity_aliases.entity_id
- facility_crossref links two source systems (source_a_system:source_a_id ↔ source_b_system:source_b_id)
- frs_crosswalk maps EPA registry_id to program IDs (pgm_sys_acrnm + pgm_sys_id)
- doe_status tracks DOE award status with USAspending URLs
- quarantined=0 means active project, quarantined=1 means quarantined

RESPONSE FORMAT:

For DATABASE answers:
```sql
YOUR SQL QUERY HERE
```
**Results:** (query results)
**Answer:** Your interpretation citing only what the data shows.

For GLOSSARY answers:
**From the glossary:** Your answer citing the specific definitions.

For COMBINED answers (data + context):
**From the database:** (SQL + results)
**From the glossary:** (interpretation of what the results mean)

SMART COLUMN SELECTION — what to show for different question types:

When listing projects, ALWAYS include these columns:
  - project_name (the project)
  - developer_name (the company — this is DIFFERENT from project_name)
  - state
  - technology
  - stage
  - capacity_raw (human-readable capacity, e.g., "500 MW", "1.2 MMTPA")

When asked about evidence/sources/reasoning, ALSO JOIN to show:
  - GROUP_CONCAT(DISTINCT cd.source_system) as sources — which regulatory systems
  - COUNT(DISTINCT c.claim_id) as claim_count — how much evidence

When asked for project descriptions, JOIN to get claim text:
  - SELECT c.claim_text FROM claims c JOIN project_claims pc ON c.claim_id = pc.claim_id
    WHERE pc.project_id = up.project_id AND c.claim_type = 'project_description' LIMIT 1

DO NOT select columns that are mostly NULL and unhelpful:
  - SKIP: capacity_mtpa_h2, fid_date, construction_start, cod_date (usually NULL)
  - SKIP: source_count alone without showing WHAT the sources are
  - Instead of source_count, show GROUP_CONCAT(DISTINCT source_system)

When explaining WHY a project is at a certain stage:
  - Show the source_system(s) — this IS the evidence
  - Use the glossary to explain what each source means (e.g., "ercot_gis = grid
    interconnection study, indicating pre-construction planning")
  - Summarize: "Classified as 'announced' because evidence comes only from ERCOT GIS
    (grid interconnection filing), with no permits or construction claims yet."
"""


def ask(db: sqlite3.Connection, question: str, schema: str, samples: str,
        glossary: str, history: list[dict]) -> str:
    """Send question to LLM, get SQL, execute, return answer."""

    system = SYSTEM_PROMPT_TEMPLATE.format(
        schema=schema, samples=samples, glossary=glossary
    )

    # Build messages with conversation history
    messages = list(history)
    messages.append({'role': 'user', 'content': question})

    # Step 1: Get SQL from LLM
    llm_text = _llm_call(system, messages)

    # Extract SQL blocks
    sql_blocks = []
    in_sql = False
    current_sql = []
    for line in llm_text.split('\n'):
        if line.strip().startswith('```sql'):
            in_sql = True
            current_sql = []
        elif line.strip() == '```' and in_sql:
            in_sql = False
            sql_blocks.append('\n'.join(current_sql))
        elif in_sql:
            current_sql.append(line)

    if not sql_blocks:
        # LLM might have answered directly without SQL
        return llm_text

    # Execute SQL queries
    all_results = []
    for sql in sql_blocks:
        sql = sql.strip()
        if not sql:
            continue

        # Safety: only allow SELECT and PRAGMA
        first_word = sql.split()[0].upper() if sql.split() else ''
        if first_word not in ('SELECT', 'PRAGMA', 'WITH', 'EXPLAIN'):
            all_results.append(f'[BLOCKED: Only SELECT queries allowed, got {first_word}]')
            continue

        try:
            rows = db.execute(sql).fetchall()
            if not rows:
                all_results.append(f'```sql\n{sql}\n```\n**Results:** 0 rows returned.')
            else:
                # Format results as table
                col_names = [desc[0] for desc in db.execute(sql).description]
                header = ' | '.join(col_names)
                separator = '-|-'.join('-' * max(len(c), 5) for c in col_names)

                display_rows = rows[:MAX_ROWS_DISPLAY]
                row_strs = []
                for r in display_rows:
                    vals = []
                    for v in r:
                        s = str(v) if v is not None else 'NULL'
                        if len(s) > 60:
                            s = s[:57] + '...'
                        vals.append(s)
                    row_strs.append(' | '.join(vals))

                table = f'{header}\n{separator}\n' + '\n'.join(row_strs)
                if len(rows) > MAX_ROWS_DISPLAY:
                    table += f'\n... ({len(rows) - MAX_ROWS_DISPLAY} more rows)'

                result_text = f'```sql\n{sql}\n```\n**Results** ({len(rows)} rows):\n```\n{table}\n```'

                # Truncate if too long
                if len(result_text) > MAX_RESULT_CHARS:
                    result_text = result_text[:MAX_RESULT_CHARS] + '\n... [truncated]'

                all_results.append(result_text)

        except sqlite3.Error as e:
            all_results.append(f'```sql\n{sql}\n```\n**SQL Error:** {e}')

    # Step 2: Send results back to LLM for interpretation
    results_text = '\n\n'.join(all_results)

    messages.append({'role': 'assistant', 'content': llm_text})
    messages.append({'role': 'user', 'content':
        f'Here are the query results. Interpret them to answer the original question. '
        f'Only state what the data shows — no speculation.\n\n{results_text}'
    })

    final_answer = _llm_call(system, messages)

    # Return formatted output
    output = f'{results_text}\n\n{final_answer}'
    return output


def run():
    db = sqlite3.connect(str(LEARNING_DB))
    db.row_factory = None  # Use tuples for cleaner display

    print('Loading database schema...')
    schema = _get_schema(db)
    samples = _get_sample_data(db)
    glossary = _load_glossary()
    model_name = GROQ_MODEL if BACKEND == 'groq' else CLAUDE_MODEL
    print(f'Schema: {len(schema):,} chars, {schema.count("CREATE TABLE")} tables')
    print(f'Glossary: {len(glossary):,} chars')
    print(f'Backend: {BACKEND} ({model_name})')
    print(f'Ready. Ask questions about the database. Type "quit" to exit.\n')

    # Single question mode
    if len(sys.argv) > 1 and not sys.argv[1].startswith('--'):
        question = ' '.join(sys.argv[1:])
        print(f'Q: {question}\n')
        answer = ask(db, question, schema, samples, glossary, [])
        print(answer)
        db.close()
        return

    # Interactive mode
    history: list[dict] = []
    while True:
        try:
            question = input('Q: ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\nBye.')
            break

        if not question:
            continue
        if question.lower() in ('quit', 'exit', 'q'):
            break

        answer = ask(db, question, schema, samples, glossary, history)
        print(f'\n{answer}\n')

        # Keep conversation history (last 6 turns)
        history.append({'role': 'user', 'content': question})
        history.append({'role': 'assistant', 'content': answer})
        if len(history) > 12:
            history = history[-12:]

    db.close()


if __name__ == '__main__':
    run()
