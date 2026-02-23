"""
Project Store — Unified project records with SQLite persistence.
================================================================

Aggregates evidence from multiple sources (SEC filings, EPA permits,
DOE awards, news) into canonical project records. Each project links
to all its evidence via a many-to-many relationship.

Part of Tier 5: Evidence Aggregator.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB = _HERE.parent / 'blue_h2_intelligence.db'


# ======================================================================
# Enums & Data classes
# ======================================================================


class ProjectStage(Enum):
    ANNOUNCED = "announced"
    PRE_FEED = "pre_feed"
    FEED = "feed"
    FID = "fid"
    EPC_AWARD = "epc_award"
    CONSTRUCTION = "construction"
    COMMISSIONING = "commissioning"
    OPERATIONAL = "operational"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ValueChain(Enum):
    """Product / supply-chain focus — independent grouping dimension."""
    HYDROGEN = "hydrogen"
    AMMONIA = "ammonia"
    CCS_CCUS = "ccs_ccus"
    LNG_GAS = "lng_gas"
    POWER = "power"
    METHANOL = "methanol"
    UNKNOWN = "unknown"


class EndUseSector(Enum):
    """End-use sector — independent grouping dimension."""
    INDUSTRIAL_FEEDSTOCK = "industrial_feedstock"
    POWER_GENERATION = "power_generation"
    TRANSPORT_FUEL = "transport_fuel"
    EXPORT_TRADE = "export_trade"
    STORAGE_HUB = "storage_hub"
    UNKNOWN = "unknown"


SOURCE_AUTHORITY = {
    'sec_filing': 0.95,
    'sec_edgar': 0.95,
    'epa_permit': 0.92,
    'epa_echo': 0.92,
    'epa_echo_broad': 0.92,
    'doe_award': 0.92,
    'regulatory_filing': 0.85,
    'pdf_structured': 0.80,
    'pdf_chunk': 0.75,
    'news': 0.60,
    'unknown': 0.50,
}


@dataclass
class EvidenceItem:
    """A link from a project to a piece of evidence."""
    document_id: str
    document_type: str              # "sec_filing", "epa_permit", "news", etc.
    source_url: Optional[str] = None
    source_authority: float = 0.50
    document_date: Optional[str] = None
    added_at: datetime = field(default_factory=datetime.now)
    # What this evidence contributes
    contributes_stage: Optional[str] = None
    contributes_capacity: Optional[str] = None
    contributes_investment: Optional[str] = None
    contributes_timeline: Optional[str] = None
    confidence: float = 0.0
    snippet: str = ""
    # Staleness tracking
    stale: bool = False
    stale_reason: Optional[str] = None


@dataclass
class StageAssessment:
    """Aggregated stage determination from multiple evidence sources."""
    stage: ProjectStage = ProjectStage.UNKNOWN
    confidence: float = 0.0
    evidence_count: int = 0
    latest_evidence_date: Optional[str] = None
    reasoning: str = ""


@dataclass
class Project:
    """Unified project record aggregating all evidence."""
    project_id: str = ""
    project_name: str = ""
    developer_key: Optional[str] = None
    developer_name: str = ""

    # Location
    state: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None

    # Technical
    technology: Optional[str] = None
    product: Optional[str] = None
    capacity_raw: Optional[str] = None
    capacity_mtpa_h2: Optional[float] = None

    # Independent grouping dimensions
    value_chain: ValueChain = ValueChain.UNKNOWN
    end_use_sector: EndUseSector = EndUseSector.UNKNOWN

    # Stage & probability
    stage: StageAssessment = field(default_factory=StageAssessment)
    fid_probability: Optional[float] = None

    # Key players
    epc_contractor: Optional[str] = None
    co_developers: List[str] = field(default_factory=list)

    # Dates
    fid_date: Optional[str] = None
    cod_date: Optional[str] = None
    construction_start: Optional[str] = None

    # Evidence linkage
    evidence: List[EvidenceItem] = field(default_factory=list)
    evidence_gap_flags: List[str] = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    last_evidence_date: Optional[str] = None
    source_count: int = 0


# ======================================================================
# SQL DDL
# ======================================================================

_DDL = """
CREATE TABLE IF NOT EXISTS unified_projects (
    project_id TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    developer_key TEXT,
    developer_name TEXT,
    state TEXT,
    city TEXT,
    region TEXT,
    technology TEXT,
    product TEXT,
    capacity_raw TEXT,
    capacity_mtpa_h2 REAL,
    value_chain TEXT DEFAULT 'unknown',
    end_use_sector TEXT DEFAULT 'unknown',
    stage TEXT DEFAULT 'unknown',
    stage_confidence REAL DEFAULT 0.0,
    stage_evidence_count INTEGER DEFAULT 0,
    stage_reasoning TEXT,
    stage_latest_evidence_date TEXT,
    fid_probability REAL,
    epc_contractor TEXT,
    co_developers_json TEXT DEFAULT '[]',
    fid_date TEXT,
    cod_date TEXT,
    construction_start TEXT,
    evidence_gap_flags_json TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT,
    last_evidence_date TEXT,
    source_count INTEGER DEFAULT 0,
    UNIQUE(project_name, developer_key)
);

CREATE TABLE IF NOT EXISTS project_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES unified_projects(project_id),
    document_id TEXT NOT NULL,
    document_type TEXT,
    source_url TEXT,
    source_authority REAL,
    document_date TEXT,
    added_at TEXT,
    contributes_stage TEXT,
    contributes_capacity TEXT,
    contributes_investment TEXT,
    contributes_timeline TEXT,
    confidence REAL,
    snippet TEXT,
    stale INTEGER DEFAULT 0,
    stale_reason TEXT,
    UNIQUE(project_id, document_id)
);

CREATE TABLE IF NOT EXISTS project_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    change_type TEXT,
    old_value TEXT,
    new_value TEXT,
    triggered_by_document_id TEXT,
    timestamp TEXT
);

CREATE INDEX IF NOT EXISTS idx_pe_project ON project_evidence(project_id);
CREATE INDEX IF NOT EXISTS idx_pe_document ON project_evidence(document_id);
CREATE INDEX IF NOT EXISTS idx_up_developer ON unified_projects(developer_key);
CREATE INDEX IF NOT EXISTS idx_up_stage ON unified_projects(stage);
CREATE INDEX IF NOT EXISTS idx_pal_project ON project_audit_log(project_id);
"""

# Migration: add columns to existing tables that lack them
_MIGRATIONS = [
    "ALTER TABLE unified_projects ADD COLUMN value_chain TEXT DEFAULT 'unknown'",
    "ALTER TABLE unified_projects ADD COLUMN end_use_sector TEXT DEFAULT 'unknown'",
]

# Post-migration indexes (run after migrations ensure columns exist)
_POST_MIGRATION_DDL = """
CREATE INDEX IF NOT EXISTS idx_up_value_chain ON unified_projects(value_chain);
CREATE INDEX IF NOT EXISTS idx_up_end_use ON unified_projects(end_use_sector);
"""


# ======================================================================
# ProjectStore
# ======================================================================


class ProjectStore:
    """Unified project records backed by SQLite.

    Uses the existing ``blue_h2_intelligence.db`` by default, adding
    three new tables: ``unified_projects``, ``project_evidence``,
    and ``project_audit_log``.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or str(_DEFAULT_DB)
        self._ensure_tables()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self):
        conn = self._conn()
        try:
            conn.executescript(_DDL)
            # Run migrations for existing DBs (ignore if columns already exist)
            for sql in _MIGRATIONS:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.executescript(_POST_MIGRATION_DDL)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Project ID generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_project_id(developer_key: str, project_name: str) -> str:
        """Deterministic project ID from developer + project name."""
        normalized = ProjectStore._normalize_project_name(project_name)
        key = f"{developer_key or 'unknown'}:{normalized}"
        return hashlib.md5(key.encode()).hexdigest()[:12]

    @staticmethod
    def _normalize_project_name(name: str) -> str:
        """Normalize a project name for matching."""
        if not name:
            return ""
        s = name.strip().lower()
        # Strip common prefixes/suffixes
        for prefix in ('the ', ):
            if s.startswith(prefix):
                s = s[len(prefix):]
        for suffix in (' project', ' facility', ' hub', ' plant', ' complex', ' site'):
            if s.endswith(suffix):
                s = s[:-len(suffix)]
        # Normalize whitespace
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_project(self, project_id: str) -> Optional[Project]:
        """Get a project by ID, including its evidence links."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM unified_projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if not row:
                return None
            record = self._row_to_record(row)
            record.evidence = self.get_evidence(project_id)
            return record
        finally:
            conn.close()

    def get_projects_by_developer(self, developer_key: str) -> List[Project]:
        """Get all projects for a developer."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM unified_projects WHERE developer_key = ?",
                (developer_key,),
            ).fetchall()
            records = []
            for row in rows:
                record = self._row_to_record(row)
                record.evidence = self.get_evidence(record.project_id)
                records.append(record)
            return records
        finally:
            conn.close()

    def get_all_projects(self, stage: Optional[str] = None) -> List[Project]:
        """Get all projects, optionally filtered by stage."""
        conn = self._conn()
        try:
            if stage:
                rows = conn.execute(
                    "SELECT * FROM unified_projects WHERE stage = ? ORDER BY updated_at DESC",
                    (stage,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM unified_projects ORDER BY updated_at DESC",
                ).fetchall()
            records = []
            for row in rows:
                record = self._row_to_record(row)
                record.evidence = self.get_evidence(record.project_id)
                records.append(record)
            return records
        finally:
            conn.close()

    def upsert_project(self, record: Project) -> str:
        """Insert or update a project record. Returns project_id."""
        if not record.project_id:
            record.project_id = self.generate_project_id(
                record.developer_key, record.project_name,
            )
        record.updated_at = datetime.now()

        conn = self._conn()
        try:
            conn.execute("""
                INSERT INTO unified_projects (
                    project_id, project_name, developer_key, developer_name,
                    state, city, region, technology, product,
                    capacity_raw, capacity_mtpa_h2,
                    value_chain, end_use_sector,
                    stage, stage_confidence, stage_evidence_count, stage_reasoning,
                    stage_latest_evidence_date,
                    fid_probability, epc_contractor, co_developers_json,
                    fid_date, cod_date, construction_start,
                    evidence_gap_flags_json,
                    created_at, updated_at, last_evidence_date, source_count
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(project_id) DO UPDATE SET
                    project_name = excluded.project_name,
                    developer_name = excluded.developer_name,
                    state = COALESCE(excluded.state, unified_projects.state),
                    city = COALESCE(excluded.city, unified_projects.city),
                    region = COALESCE(excluded.region, unified_projects.region),
                    technology = COALESCE(excluded.technology, unified_projects.technology),
                    product = COALESCE(excluded.product, unified_projects.product),
                    capacity_raw = COALESCE(excluded.capacity_raw, unified_projects.capacity_raw),
                    capacity_mtpa_h2 = COALESCE(excluded.capacity_mtpa_h2, unified_projects.capacity_mtpa_h2),
                    value_chain = CASE WHEN excluded.value_chain != 'unknown' THEN excluded.value_chain ELSE unified_projects.value_chain END,
                    end_use_sector = CASE WHEN excluded.end_use_sector != 'unknown' THEN excluded.end_use_sector ELSE unified_projects.end_use_sector END,
                    stage = excluded.stage,
                    stage_confidence = excluded.stage_confidence,
                    stage_evidence_count = excluded.stage_evidence_count,
                    stage_reasoning = excluded.stage_reasoning,
                    stage_latest_evidence_date = excluded.stage_latest_evidence_date,
                    fid_probability = COALESCE(excluded.fid_probability, unified_projects.fid_probability),
                    epc_contractor = COALESCE(excluded.epc_contractor, unified_projects.epc_contractor),
                    co_developers_json = excluded.co_developers_json,
                    fid_date = COALESCE(excluded.fid_date, unified_projects.fid_date),
                    cod_date = COALESCE(excluded.cod_date, unified_projects.cod_date),
                    construction_start = COALESCE(excluded.construction_start, unified_projects.construction_start),
                    evidence_gap_flags_json = excluded.evidence_gap_flags_json,
                    updated_at = excluded.updated_at,
                    last_evidence_date = excluded.last_evidence_date,
                    source_count = excluded.source_count
            """, (
                record.project_id, record.project_name,
                record.developer_key, record.developer_name,
                record.state, record.city, record.region,
                record.technology, record.product,
                record.capacity_raw, record.capacity_mtpa_h2,
                record.value_chain.value, record.end_use_sector.value,
                record.stage.stage.value, record.stage.confidence,
                record.stage.evidence_count, record.stage.reasoning,
                record.stage.latest_evidence_date,
                record.fid_probability, record.epc_contractor,
                json.dumps(record.co_developers),
                record.fid_date, record.cod_date, record.construction_start,
                json.dumps(record.evidence_gap_flags),
                record.created_at.isoformat(), record.updated_at.isoformat(),
                record.last_evidence_date, record.source_count,
            ))
            conn.commit()
        finally:
            conn.close()

        return record.project_id

    # ------------------------------------------------------------------
    # Evidence linking
    # ------------------------------------------------------------------

    def add_evidence(self, project_id: str, link: EvidenceItem):
        """Add an evidence link. Updates on duplicate (project_id, document_id)."""
        conn = self._conn()
        try:
            conn.execute("""
                INSERT INTO project_evidence (
                    project_id, document_id, document_type,
                    source_url, source_authority, document_date, added_at,
                    contributes_stage, contributes_capacity,
                    contributes_investment, contributes_timeline,
                    confidence, snippet, stale, stale_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, document_id) DO UPDATE SET
                    document_type = excluded.document_type,
                    source_url = COALESCE(excluded.source_url, project_evidence.source_url),
                    source_authority = excluded.source_authority,
                    document_date = COALESCE(excluded.document_date, project_evidence.document_date),
                    contributes_stage = COALESCE(excluded.contributes_stage, project_evidence.contributes_stage),
                    contributes_capacity = COALESCE(excluded.contributes_capacity, project_evidence.contributes_capacity),
                    contributes_investment = COALESCE(excluded.contributes_investment, project_evidence.contributes_investment),
                    contributes_timeline = COALESCE(excluded.contributes_timeline, project_evidence.contributes_timeline),
                    confidence = excluded.confidence,
                    snippet = excluded.snippet,
                    stale = excluded.stale,
                    stale_reason = excluded.stale_reason
            """, (
                project_id, link.document_id, link.document_type,
                link.source_url, link.source_authority, link.document_date,
                link.added_at.isoformat(),
                link.contributes_stage, link.contributes_capacity,
                link.contributes_investment, link.contributes_timeline,
                link.confidence, link.snippet,
                1 if link.stale else 0, link.stale_reason,
            ))
            conn.commit()

            # Audit log
            self._log_audit(
                project_id, 'evidence_added',
                new_value=f"{link.document_type}:{link.document_id}",
                triggered_by=link.document_id,
            )
        finally:
            conn.close()

    def get_evidence(self, project_id: str) -> List[EvidenceItem]:
        """Get all evidence links for a project."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM project_evidence WHERE project_id = ? ORDER BY added_at DESC",
                (project_id,),
            ).fetchall()
            return [self._row_to_evidence(row) for row in rows]
        finally:
            conn.close()

    def get_projects_for_document(self, document_id: str) -> List[str]:
        """Get project IDs linked to a given document."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT project_id FROM project_evidence WHERE document_id = ?",
                (document_id,),
            ).fetchall()
            return [row['project_id'] for row in rows]
        finally:
            conn.close()

    def mark_evidence_stale(self, project_id: str, document_id: str, reason: str):
        """Mark an evidence link as stale."""
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE project_evidence SET stale = 1, stale_reason = ? "
                "WHERE project_id = ? AND document_id = ?",
                (reason, project_id, document_id),
            )
            conn.commit()
            self._log_audit(
                project_id, 'evidence_staled',
                old_value=document_id, new_value=reason,
                triggered_by=document_id,
            )
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Search / matching
    # ------------------------------------------------------------------

    def find_project(
        self,
        project_name: str,
        developer_key: Optional[str] = None,
    ) -> Optional[Project]:
        """Fuzzy project matching.

        1. If developer_key provided, narrow to that developer's projects.
        2. Normalize names and compare with SequenceMatcher (threshold >= 0.75).
        3. Returns best match or None.
        """
        conn = self._conn()
        try:
            if developer_key:
                rows = conn.execute(
                    "SELECT * FROM unified_projects WHERE developer_key = ?",
                    (developer_key,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM unified_projects").fetchall()

            if not rows:
                return None

            query_norm = self._normalize_project_name(project_name)
            if not query_norm:
                return None

            best_ratio = 0.0
            best_row = None

            for row in rows:
                candidate_norm = self._normalize_project_name(row['project_name'])
                if not candidate_norm:
                    continue

                ratio = SequenceMatcher(None, query_norm, candidate_norm).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_row = row

            if best_row and best_ratio >= 0.75:
                record = self._row_to_record(best_row)
                record.evidence = self.get_evidence(record.project_id)
                return record

            return None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Project merge
    # ------------------------------------------------------------------

    def merge_projects(self, keep_id: str, merge_id: str) -> Optional[Project]:
        """Merge two projects. Evidence from merge_id transfers to keep_id.

        merge_id is deleted. Audit log entry created.
        Returns the updated keep record.
        """
        keep = self.get_project(keep_id)
        merge = self.get_project(merge_id)
        if not keep or not merge:
            logger.warning("Cannot merge: %s or %s not found", keep_id, merge_id)
            return None

        conn = self._conn()
        try:
            # Transfer evidence links
            conn.execute(
                "UPDATE OR IGNORE project_evidence SET project_id = ? WHERE project_id = ?",
                (keep_id, merge_id),
            )
            # Delete any remaining duplicates that couldn't be transferred
            conn.execute(
                "DELETE FROM project_evidence WHERE project_id = ?",
                (merge_id,),
            )

            # Delete merged project
            conn.execute(
                "DELETE FROM unified_projects WHERE project_id = ?",
                (merge_id,),
            )
            conn.commit()

            # Audit log
            self._log_audit(
                keep_id, 'merged',
                old_value=f"merged_from:{merge_id}:{merge.project_name}",
                new_value=keep.project_name,
            )
        finally:
            conn.close()

        # Return refreshed record
        return self.get_project(keep_id)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def get_project_history(self, project_id: str) -> List[Dict]:
        """Get audit trail for a project."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM project_audit_log WHERE project_id = ? ORDER BY timestamp DESC",
                (project_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _log_audit(
        self,
        project_id: str,
        change_type: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        triggered_by: Optional[str] = None,
    ):
        """Write an audit log entry."""
        try:
            conn = self._conn()
            try:
                conn.execute(
                    "INSERT INTO project_audit_log "
                    "(project_id, change_type, old_value, new_value, triggered_by_document_id, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (project_id, change_type, old_value, new_value,
                     triggered_by, datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Audit log write failed: %s", e)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Overall project store statistics."""
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM unified_projects").fetchone()[0]
            by_stage = {}
            for row in conn.execute(
                "SELECT stage, COUNT(*) as cnt FROM unified_projects GROUP BY stage"
            ).fetchall():
                by_stage[row['stage']] = row['cnt']

            by_region = {}
            for row in conn.execute(
                "SELECT region, COUNT(*) as cnt FROM unified_projects "
                "WHERE region IS NOT NULL GROUP BY region"
            ).fetchall():
                by_region[row['region']] = row['cnt']

            by_value_chain = {}
            for row in conn.execute(
                "SELECT value_chain, COUNT(*) as cnt FROM unified_projects GROUP BY value_chain"
            ).fetchall():
                by_value_chain[row['value_chain']] = row['cnt']

            by_end_use = {}
            for row in conn.execute(
                "SELECT end_use_sector, COUNT(*) as cnt FROM unified_projects GROUP BY end_use_sector"
            ).fetchall():
                by_end_use[row['end_use_sector']] = row['cnt']

            evidence_count = conn.execute(
                "SELECT COUNT(*) FROM project_evidence"
            ).fetchone()[0]

            avg_evidence = evidence_count / total if total > 0 else 0.0

            # Projects with gaps
            gap_rows = conn.execute(
                "SELECT evidence_gap_flags_json FROM unified_projects "
                "WHERE evidence_gap_flags_json != '[]'"
            ).fetchall()
            projects_with_gaps = len(gap_rows)

            # Aggregate top gaps
            gap_counts: Dict[str, int] = {}
            for row in gap_rows:
                flags = json.loads(row['evidence_gap_flags_json'])
                for flag in flags:
                    # Strip stage_conflict details for aggregation
                    key = flag.split(':')[0] if flag.startswith('stage_conflict') else flag
                    gap_counts[key] = gap_counts.get(key, 0) + 1
            top_gaps = sorted(gap_counts.items(), key=lambda x: x[1], reverse=True)[:10]

            return {
                'total_projects': total,
                'by_stage': by_stage,
                'by_region': by_region,
                'by_value_chain': by_value_chain,
                'by_end_use_sector': by_end_use,
                'total_evidence_links': evidence_count,
                'avg_evidence_per_project': round(avg_evidence, 1),
                'projects_with_gaps': projects_with_gaps,
                'top_gaps': [{'gap': g, 'count': c} for g, c in top_gaps],
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> Project:
        """Convert a SQLite row to a Project."""
        # Safe enum parsing for grouping dimensions
        try:
            vc = ValueChain(row['value_chain']) if row['value_chain'] else ValueChain.UNKNOWN
        except (ValueError, KeyError):
            vc = ValueChain.UNKNOWN
        try:
            eus = EndUseSector(row['end_use_sector']) if row['end_use_sector'] else EndUseSector.UNKNOWN
        except (ValueError, KeyError):
            eus = EndUseSector.UNKNOWN

        return Project(
            project_id=row['project_id'],
            project_name=row['project_name'],
            developer_key=row['developer_key'],
            developer_name=row['developer_name'] or '',
            state=row['state'],
            city=row['city'],
            region=row['region'],
            technology=row['technology'],
            product=row['product'],
            capacity_raw=row['capacity_raw'],
            capacity_mtpa_h2=row['capacity_mtpa_h2'],
            value_chain=vc,
            end_use_sector=eus,
            stage=StageAssessment(
                stage=ProjectStage(row['stage']) if row['stage'] else ProjectStage.UNKNOWN,
                confidence=row['stage_confidence'] or 0.0,
                evidence_count=row['stage_evidence_count'] or 0,
                latest_evidence_date=row['stage_latest_evidence_date'],
                reasoning=row['stage_reasoning'] or '',
            ),
            fid_probability=row['fid_probability'],
            epc_contractor=row['epc_contractor'],
            co_developers=json.loads(row['co_developers_json'] or '[]'),
            fid_date=row['fid_date'],
            cod_date=row['cod_date'],
            construction_start=row['construction_start'],
            evidence_gap_flags=json.loads(row['evidence_gap_flags_json'] or '[]'),
            created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.now(),
            updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else datetime.now(),
            last_evidence_date=row['last_evidence_date'],
            source_count=row['source_count'] or 0,
        )

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> EvidenceItem:
        """Convert a SQLite row to an EvidenceItem."""
        return EvidenceItem(
            document_id=row['document_id'],
            document_type=row['document_type'] or 'unknown',
            source_url=row['source_url'],
            source_authority=row['source_authority'] or 0.50,
            document_date=row['document_date'],
            added_at=datetime.fromisoformat(row['added_at']) if row['added_at'] else datetime.now(),
            contributes_stage=row['contributes_stage'],
            contributes_capacity=row['contributes_capacity'],
            contributes_investment=row['contributes_investment'],
            contributes_timeline=row['contributes_timeline'],
            confidence=row['confidence'] or 0.0,
            snippet=row['snippet'] or '',
            stale=bool(row['stale']),
            stale_reason=row['stale_reason'],
        )
