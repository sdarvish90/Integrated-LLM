"""
DecarbIQ Learning Layer — Step 2: Schema Creation
Creates all tables in core_database.db needed for Steps 3–5.
Run once. Safe to re-run (uses IF NOT EXISTS).
"""

import sqlite3

from config import LEARNING_DB, DATA_DIR


def migrate_extraction_schema(db):
    """Add structured extraction columns. Safe to re-run on existing DBs."""
    migrations = [
        "ALTER TABLE claims ADD COLUMN extracted_company TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_project TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_city TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_state TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_county TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_capacity TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_technology TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_stage TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_date TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_date_type TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_permit_id TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_parent_company TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_partners TEXT",
        "ALTER TABLE claims ADD COLUMN extracted_funding TEXT",
        "ALTER TABLE claims ADD COLUMN corroboration_count INTEGER DEFAULT 1",
        "ALTER TABLE claims ADD COLUMN corroborating_sources TEXT",
        "ALTER TABLE cleaned_documents ADD COLUMN source_system TEXT",
        # External ID columns for cross-source tracing
        "ALTER TABLE regulatory_evidence ADD COLUMN facility_id TEXT",
        "ALTER TABLE regulatory_evidence ADD COLUMN permit_id TEXT",
        "ALTER TABLE regulatory_evidence ADD COLUMN project_name TEXT",
        "ALTER TABLE unified_projects ADD COLUMN permit_ids_json TEXT",
        "ALTER TABLE unified_projects ADD COLUMN facility_ids_json TEXT",
        # Company/project separation columns
        "ALTER TABLE unified_projects ADD COLUMN company_id TEXT",
        "ALTER TABLE unified_projects ADD COLUMN project_type TEXT DEFAULT 'company_portfolio'",
        "ALTER TABLE unified_projects ADD COLUMN source_identity TEXT",
        "ALTER TABLE unified_projects ADD COLUMN source_registry_ids TEXT",
        "ALTER TABLE unified_projects ADD COLUMN doe_award_url TEXT",
        # Cross-reference source count (set by Connect/crossref.py)
        "ALTER TABLE unified_projects ADD COLUMN crossref_source_count INTEGER DEFAULT 0",
    ]
    for sql in migrations:
        try:
            db.execute(sql)
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                pass
            else:
                raise
    db.commit()


def run():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(LEARNING_DB))

    db.executescript("""
        -- Step 1 output (also created by step1, but included for completeness)
        CREATE TABLE IF NOT EXISTS cleaned_documents (
            source_id       INTEGER PRIMARY KEY,
            company_name    TEXT,
            document_type   TEXT,
            document_date   TEXT,
            document_url    TEXT,
            original_chars  INTEGER,
            clean_text      TEXT,
            clean_chars     INTEGER,
            quality_flag    TEXT,
            processed_at    TEXT,
            source_system   TEXT
        );

        -- Step 3: one row per extracted claim
        CREATE TABLE IF NOT EXISTS claims (
            claim_id            TEXT PRIMARY KEY,
            source_id           INTEGER,
            company_name        TEXT,
            document_type       TEXT,
            document_date       TEXT,
            document_url        TEXT,
            claim_type          TEXT,
            claim_text          TEXT,
            confidence          REAL,
            commitment_level    TEXT,
            time_sensitivity    TEXT,
            extraction_model    TEXT,
            extracted_at        TEXT,
            superseded_by       TEXT,
            flagged_for_review  INTEGER DEFAULT 0,
            flag_reason         TEXT,
            extracted_company       TEXT,
            extracted_project       TEXT,
            extracted_city          TEXT,
            extracted_state         TEXT,
            extracted_county        TEXT,
            extracted_capacity      TEXT,
            extracted_technology    TEXT,
            extracted_stage         TEXT,
            extracted_date          TEXT,
            extracted_date_type     TEXT,
            extracted_permit_id     TEXT,
            extracted_parent_company TEXT,
            extracted_partners      TEXT,
            extracted_funding       TEXT,
            corroboration_count     INTEGER DEFAULT 1,
            corroborating_sources   TEXT
        );

        -- Step 3: frequency of each claim_type per document_type
        CREATE TABLE IF NOT EXISTS scheme_frequency (
            document_type       TEXT,
            claim_type          TEXT,
            occurrence_count    INTEGER DEFAULT 0,
            first_seen          TEXT,
            last_seen           TEXT,
            PRIMARY KEY (document_type, claim_type)
        );

        -- Step 3: one row per document processed
        CREATE TABLE IF NOT EXISTS read_pass_log (
            source_id           INTEGER PRIMARY KEY,
            company_name        TEXT,
            document_type       TEXT,
            status              TEXT,
            claims_extracted    INTEGER,
            prompt_tokens       INTEGER,
            completion_tokens   INTEGER,
            cost_usd            REAL,
            error_message       TEXT,
            processed_at        TEXT
        );

        -- Step 5: flagged contradiction pairs
        CREATE TABLE IF NOT EXISTS contradiction_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id_a          TEXT,
            claim_id_b          TEXT,
            company_name        TEXT,
            claim_type          TEXT,
            contradiction_type  TEXT,
            auto_resolved       INTEGER,
            resolution          TEXT,
            resolution_rule     TEXT,
            flagged_at          TEXT,
            reviewed_at         TEXT,
            review_decision     TEXT
        );

        -- Document chunks for long documents + FTS search
        CREATE TABLE IF NOT EXISTS document_chunks (
            chunk_id        TEXT PRIMARY KEY,
            source_id       INTEGER,
            chunk_index     INTEGER,
            chunk_text      TEXT,
            chunk_chars     INTEGER,
            overlap_prev    INTEGER DEFAULT 0,
            created_at      TEXT,
            UNIQUE(source_id, chunk_index)
        );

        -- Indexes for common queries
        CREATE INDEX IF NOT EXISTS idx_claims_company ON claims(company_name);
        CREATE INDEX IF NOT EXISTS idx_claims_type ON claims(claim_type);
        CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_id);
        CREATE INDEX IF NOT EXISTS idx_claims_flagged ON claims(flagged_for_review);
        CREATE INDEX IF NOT EXISTS idx_claims_ext_company ON claims(extracted_company);
        CREATE INDEX IF NOT EXISTS idx_claims_ext_state ON claims(extracted_state);
        CREATE INDEX IF NOT EXISTS idx_contradiction_company ON contradiction_log(company_name);
        CREATE INDEX IF NOT EXISTS idx_chunks_source ON document_chunks(source_id);
    """)

    # FTS5 virtual tables (cannot be inside executescript with other DDL)
    fts_statements = [
        """CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
            claim_text,
            claim_type,
            company_name,
            content='claims',
            content_rowid='rowid'
        )""",
        """CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            chunk_text,
            content='document_chunks',
            content_rowid='rowid'
        )""",
    ]
    for sql in fts_statements:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # already exists

    # FTS auto-populate triggers
    trigger_statements = [
        """CREATE TRIGGER IF NOT EXISTS claims_fts_insert AFTER INSERT ON claims BEGIN
            INSERT INTO claims_fts(rowid, claim_text, claim_type, company_name)
            VALUES (new.rowid, new.claim_text, new.claim_type, new.company_name);
        END""",
        """CREATE TRIGGER IF NOT EXISTS chunks_fts_insert AFTER INSERT ON document_chunks BEGIN
            INSERT INTO chunks_fts(rowid, chunk_text)
            VALUES (new.rowid, new.chunk_text);
        END""",
        # DELETE / UPDATE triggers to keep FTS in sync
        """CREATE TRIGGER IF NOT EXISTS claims_fts_delete AFTER DELETE ON claims BEGIN
            INSERT INTO claims_fts(claims_fts, rowid, claim_text, claim_type, company_name)
            VALUES ('delete', old.rowid, old.claim_text, old.claim_type, old.company_name);
        END""",
        """CREATE TRIGGER IF NOT EXISTS claims_fts_update AFTER UPDATE ON claims BEGIN
            INSERT INTO claims_fts(claims_fts, rowid, claim_text, claim_type, company_name)
            VALUES ('delete', old.rowid, old.claim_text, old.claim_type, old.company_name);
            INSERT INTO claims_fts(rowid, claim_text, claim_type, company_name)
            VALUES (new.rowid, new.claim_text, new.claim_type, new.company_name);
        END""",
        """CREATE TRIGGER IF NOT EXISTS chunks_fts_delete AFTER DELETE ON document_chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text)
            VALUES ('delete', old.rowid, old.chunk_text);
        END""",
        """CREATE TRIGGER IF NOT EXISTS chunks_fts_update AFTER UPDATE ON document_chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, chunk_text)
            VALUES ('delete', old.rowid, old.chunk_text);
            INSERT INTO chunks_fts(rowid, chunk_text)
            VALUES (new.rowid, new.chunk_text);
        END""",
    ]
    for sql in trigger_statements:
        try:
            db.execute(sql)
        except sqlite3.OperationalError:
            pass  # already exists

    # Companies table — one row per corporate entity
    db.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            company_id   TEXT PRIMARY KEY,
            company_key  TEXT UNIQUE NOT NULL,
            company_name TEXT NOT NULL,
            entity_id    TEXT,
            cik          TEXT,
            company_type TEXT DEFAULT 'developer',
            country      TEXT DEFAULT 'US',
            created_at   TEXT,
            updated_at   TEXT
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_co_key ON companies(company_key);
    """)

    # Project events — timestamped events per project
    db.executescript("""
        CREATE TABLE IF NOT EXISTS project_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id   TEXT NOT NULL,
            company_id   TEXT,
            event_type   TEXT NOT NULL,
            event_date   TEXT NOT NULL,
            event_source TEXT,
            source_id    TEXT,
            source_url   TEXT,
            description  TEXT,
            confidence   REAL DEFAULT 1.0,
            created_at   TEXT,
            UNIQUE(project_id, event_type, event_date, event_source)
        );
        CREATE INDEX IF NOT EXISTS idx_pe_project ON project_events(project_id);
    """)

    # Corporate events — mergers, acquisitions, spin-offs with effective dates
    db.executescript("""
        CREATE TABLE IF NOT EXISTS corporate_events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_company_id   TEXT NOT NULL,
            child_company_id    TEXT,
            child_name          TEXT NOT NULL,
            child_name_norm     TEXT,
            event_type          TEXT NOT NULL,
            event_date          TEXT,
            event_source        TEXT,
            source_url          TEXT,
            description         TEXT,
            created_at          TEXT,
            UNIQUE(parent_company_id, child_name, event_type)
        );
        CREATE INDEX IF NOT EXISTS idx_ce_child_norm
            ON corporate_events(child_name_norm);
        CREATE INDEX IF NOT EXISTS idx_ce_parent
            ON corporate_events(parent_company_id);
    """)

    # Indexes for company/project separation
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_up_company ON unified_projects(company_id)")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("CREATE INDEX IF NOT EXISTS idx_up_source_identity ON unified_projects(source_identity)")
    except sqlite3.OperationalError:
        pass

    # Run migration for existing DBs (adds columns if missing)
    migrate_extraction_schema(db)

    db.commit()
    db.close()

    print("STEP 2 — SCHEMA CREATED")
    print(f"  Database: {LEARNING_DB}")
    print("  Tables: cleaned_documents, claims, scheme_frequency,")
    print("          read_pass_log, contradiction_log, document_chunks,")
    print("          companies, project_events, corporate_events")
    print("  FTS5:  claims_fts, chunks_fts")


if __name__ == "__main__":
    run()
