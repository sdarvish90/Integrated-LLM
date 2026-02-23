"""
Evidence Aggregator — Combines evidence from multiple sources.
==============================================================

Responsibilities:
1. Match processed documents to projects
2. Merge new evidence with existing project data
3. Resolve conflicts using authority and recency
4. Detect gaps and trigger targeted fetches

Part of Tier 5: Evidence Aggregator.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .entity_resolver import EntityResolver, CanonicalEntity
from .project_store import (
    EndUseSector,
    EvidenceItem,
    Project,
    ProjectStage,
    ProjectStore,
    SOURCE_AUTHORITY,
    StageAssessment,
    ValueChain,
)
from .schema import (
    DocumentType,
    Entity,
    EntityType,
    Fact,
    FactType,
    ProcessedDocument,
)

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB = _HERE.parent / 'blue_h2_intelligence.db'

# Stage progression order (for conflict resolution)
STAGE_ORDER = {
    ProjectStage.ANNOUNCED: 1,
    ProjectStage.PRE_FEED: 2,
    ProjectStage.FEED: 3,
    ProjectStage.FID: 4,
    ProjectStage.EPC_AWARD: 5,
    ProjectStage.CONSTRUCTION: 6,
    ProjectStage.COMMISSIONING: 7,
    ProjectStage.OPERATIONAL: 8,
    ProjectStage.CANCELLED: 0,   # Special case
    ProjectStage.UNKNOWN: 0,
}

# Reverse lookup: stage string → enum for internal use
_STAGE_STR_ORDER = {stage.value: order for stage, order in STAGE_ORDER.items()}

# Map source column values from existing tables to document_type
_SOURCE_TO_DOCTYPE = {
    'sec_edgar': 'sec_filing',
    'sec_edgar_efts': 'sec_filing',
    'epa_echo': 'epa_permit',
    'epa_echo_broad': 'epa_permit',
    'doe': 'doe_award',
}


# ======================================================================
# EvidenceAggregator
# ======================================================================


class EvidenceAggregator:
    """Aggregates evidence from processed documents into unified projects.

    Usage::

        aggregator = EvidenceAggregator()

        # Process a document
        doc = document_processor.process(text, DocumentType.SEC_FILING)

        # Aggregate into project(s)
        projects = aggregator.aggregate(doc)

        # Check for gaps
        gaps = aggregator.detect_gaps(projects[0])
    """

    def __init__(
        self,
        project_store: Optional[ProjectStore] = None,
        entity_resolver: Optional[Any] = None,
        db_path: Optional[str] = None,
    ):
        self._db_path = db_path or str(_DEFAULT_DB)
        self._store = project_store or ProjectStore(db_path=self._db_path)
        self._entity_resolver = entity_resolver
        self._resolver_loaded = False

    def _ensure_resolver(self):
        """Lazy-load EntityResolver if not provided."""
        if self._resolver_loaded:
            return
        self._resolver_loaded = True
        if self._entity_resolver is None:
            try:
                self._entity_resolver = EntityResolver()
            except Exception as e:
                logger.debug("EntityResolver not available: %s", e)

    # ==================================================================
    # Core ingestion — Spec API
    # ==================================================================

    def aggregate(self, doc: ProcessedDocument) -> List[Project]:
        """Aggregate a ProcessedDocument into project(s).

        Returns a list of Project objects linked to this document.
        Typically returns one project, but may return empty list if
        the document cannot be linked (no developer found).
        """
        signal = self._extract_signal(doc)

        if not signal.get('developer_key'):
            logger.debug(
                "No developer found in doc %s, skipping", doc.document_id,
            )
            return []

        # Find or create project
        project_name = signal.get('project_name', '')
        developer_key = signal['developer_key']

        existing = self._store.find_project(project_name, developer_key)

        if existing:
            record = existing
            is_new = False
        else:
            record = self._build_new_project(signal)
            is_new = True

        # Build evidence item
        item = self._build_evidence_item(doc, signal)

        # Persist project first (so evidence FK can reference it)
        project_id = self._store.upsert_project(record)

        # Add evidence
        self._store.add_evidence(project_id, item)

        # Refresh evidence and re-aggregate
        record.evidence = self._store.get_evidence(project_id)

        # Update fields from signal if we have new info
        self._merge_signal_into_record(record, signal)

        # Aggregate stage
        old_stage = record.stage.stage.value if record.stage else 'unknown'
        record.stage = self._aggregate_stage(record.evidence, record.stage)

        # Classify grouping dimensions
        self._classify_project(record)

        # Detect gaps
        record.evidence_gap_flags = self._detect_gaps(record)

        # Update metadata
        record.source_count = len({e.document_type for e in record.evidence if not e.stale})
        evidence_dates = [
            e.document_date for e in record.evidence
            if e.document_date and not e.stale
        ]
        if evidence_dates:
            record.last_evidence_date = max(evidence_dates)

        # Persist updated record
        self._store.upsert_project(record)

        # Audit stage change
        new_stage = record.stage.stage.value
        if not is_new and old_stage != new_stage:
            self._store._log_audit(
                project_id, 'stage_updated',
                old_value=old_stage, new_value=new_stage,
                triggered_by=doc.document_id,
            )
        elif is_new:
            self._store._log_audit(
                project_id, 'created',
                new_value=record.project_name,
                triggered_by=doc.document_id,
            )

        return [record]

    def ingest(self, doc: ProcessedDocument) -> Optional[str]:
        """Legacy API. Returns project_id string."""
        projects = self.aggregate(doc)
        return projects[0].project_id if projects else None

    def aggregate_batch(self, docs: List[ProcessedDocument]) -> Dict[str, Any]:
        """Aggregate multiple documents. Returns summary."""
        new_projects: Set[str] = set()
        updated_projects: Set[str] = set()
        unlinked = 0

        # Track existing project IDs before ingestion
        existing_ids = {
            p.project_id for p in self._store.get_all_projects()
        }

        for doc in docs:
            projects = self.aggregate(doc)
            if not projects:
                unlinked += 1
            else:
                pid = projects[0].project_id
                if pid in existing_ids:
                    updated_projects.add(pid)
                else:
                    new_projects.add(pid)
                    existing_ids.add(pid)

        return {
            'ingested': len(docs) - unlinked,
            'new_projects': len(new_projects),
            'updated_projects': len(updated_projects),
            'unlinked': unlinked,
        }

    def ingest_batch(self, docs: List[ProcessedDocument]) -> Dict[str, Any]:
        """Legacy API. Delegates to aggregate_batch."""
        return self.aggregate_batch(docs)

    # ==================================================================
    # Bootstrap from existing tables
    # ==================================================================

    def ingest_from_regulatory_evidence(self, db_path: Optional[str] = None) -> Dict[str, Any]:
        """Import existing regulatory_evidence rows into the unified project store.

        Reads the regulatory_evidence table, resolves company names via
        EntityResolver, and creates project records with evidence items.
        """
        self._ensure_resolver()
        path = db_path or self._db_path
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        results = {'ingested': 0, 'skipped': 0, 'new_projects': 0, 'errors': 0}

        try:
            rows = conn.execute(
                "SELECT * FROM regulatory_evidence ORDER BY document_date DESC"
            ).fetchall()
        finally:
            conn.close()

        existing_before = {p.project_id for p in self._store.get_all_projects()}

        for row in rows:
            try:
                company_name = row['company_name']
                if not company_name:
                    results['skipped'] += 1
                    continue

                # Resolve company
                developer_key = self._resolve_company_key(company_name, row['company_cik'])
                if not developer_key:
                    developer_key = company_name.lower().replace(' ', '_')

                developer_name = self._resolve_company_name(company_name) or company_name

                # Build signal
                source = row['source'] or 'unknown'
                doc_type = _SOURCE_TO_DOCTYPE.get(source, source)
                doc_id = row['document_id'] or f"reg_{row['id']}"

                # Parse stage confidence (may be text like 'LOW')
                stage_conf = self._parse_confidence(row['stage_confidence'])

                project_name = row['project_name']
                if not project_name:
                    state = row['state']
                    if state:
                        project_name = f"{developer_name} {state} Project"
                    else:
                        project_name = f"{developer_name} Unnamed Project"

                # Find or create project
                existing = self._store.find_project(project_name, developer_key)
                is_new = existing is None
                if existing:
                    record = existing
                else:
                    record = Project(
                        project_name=project_name,
                        developer_key=developer_key,
                        developer_name=developer_name,
                        state=row['state'],
                        stage=StageAssessment(
                            stage=self._parse_stage(row['stage']),
                            confidence=stage_conf,
                            reasoning=row['stage_reasoning'] or '',
                        ),
                        fid_date=row['fid_date'],
                        construction_start=row['construction_start'],
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )

                project_id = self._store.upsert_project(record)

                # Build evidence item
                item = EvidenceItem(
                    document_id=doc_id,
                    document_type=doc_type,
                    source_url=row['document_url'],
                    source_authority=SOURCE_AUTHORITY.get(doc_type, 0.50),
                    document_date=row['document_date'],
                    contributes_stage=row['stage'],
                    confidence=stage_conf,
                    snippet=(row['raw_text_excerpt'] or '')[:500],
                    stale=bool(row['stale']),
                    stale_reason=row['stale_reason'],
                )
                self._store.add_evidence(project_id, item)

                # Re-aggregate
                record = self._store.get_project(project_id)
                if record:
                    old_stage = record.stage.stage.value
                    record.stage = self._aggregate_stage(record.evidence, record.stage)
                    self._classify_project(record)
                    record.evidence_gap_flags = self._detect_gaps(record)
                    record.source_count = len({e.document_type for e in record.evidence if not e.stale})
                    evidence_dates = [e.document_date for e in record.evidence if e.document_date and not e.stale]
                    if evidence_dates:
                        record.last_evidence_date = max(evidence_dates)
                    self._store.upsert_project(record)

                    # Audit logging
                    new_stage = record.stage.stage.value
                    if is_new:
                        self._store._log_audit(
                            project_id, 'created',
                            new_value=record.project_name,
                            triggered_by=doc_id,
                        )
                    elif old_stage != new_stage:
                        self._store._log_audit(
                            project_id, 'stage_updated',
                            old_value=old_stage, new_value=new_stage,
                            triggered_by=doc_id,
                        )

                if project_id not in existing_before:
                    results['new_projects'] += 1
                    existing_before.add(project_id)

                results['ingested'] += 1

            except Exception as e:
                logger.warning("Error ingesting regulatory row %s: %s", row['id'], e)
                results['errors'] += 1

        return results

    def ingest_from_articles(self, db_path: Optional[str] = None) -> Dict[str, Any]:
        """Import existing articles with llm_extracted=1 into the project store."""
        self._ensure_resolver()
        path = db_path or self._db_path
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        results = {'ingested': 0, 'skipped': 0, 'new_projects': 0, 'errors': 0}

        try:
            rows = conn.execute(
                "SELECT * FROM articles WHERE llm_extracted = 1 ORDER BY published_date DESC"
            ).fetchall()
        finally:
            conn.close()

        existing_before = {p.project_id for p in self._store.get_all_projects()}

        for row in rows:
            try:
                developer = row['llm_developer']
                if not developer:
                    results['skipped'] += 1
                    continue

                # Resolve company
                developer_key = self._resolve_company_key(developer)
                if not developer_key:
                    developer_key = developer.lower().replace(' ', '_')

                developer_name = self._resolve_company_name(developer) or developer

                # Build project name
                project_name = row['llm_project_name']
                if not project_name:
                    state = row['llm_location_state']
                    tech = row['llm_technology']
                    if state:
                        project_name = f"{developer_name} {state} Project"
                    elif tech:
                        project_name = f"{developer_name} {tech} Project"
                    else:
                        project_name = f"{developer_name} Unnamed Project"

                # Find or create project
                existing = self._store.find_project(project_name, developer_key)
                is_new = existing is None
                if existing:
                    record = existing
                else:
                    co_devs = []
                    if row['llm_co_developers']:
                        co_devs = [d.strip() for d in row['llm_co_developers'].split(',') if d.strip()]

                    record = Project(
                        project_name=project_name,
                        developer_key=developer_key,
                        developer_name=developer_name,
                        state=row['llm_location_state'],
                        region=row['llm_region'],
                        technology=row['llm_technology'],
                        product=row['llm_product'],
                        capacity_raw=row['llm_capacity_raw'],
                        capacity_mtpa_h2=row['llm_capacity_mtpa_h2'],
                        stage=StageAssessment(
                            stage=self._parse_stage(row['llm_status']),
                        ),
                        epc_contractor=row['llm_epc_contractor'],
                        co_developers=co_devs,
                        fid_date=row['llm_fid_date'],
                        cod_date=row['llm_cod_date'],
                        created_at=datetime.now(),
                        updated_at=datetime.now(),
                    )

                project_id = self._store.upsert_project(record)

                # Build evidence item
                doc_id = row['content_hash'] or row['url_hash'] or f"article_{row['id']}"
                item = EvidenceItem(
                    document_id=doc_id,
                    document_type='news',
                    source_url=row['url'],
                    source_authority=SOURCE_AUTHORITY.get('news', 0.60),
                    document_date=row['published_date'],
                    contributes_stage=row['llm_status'],
                    contributes_capacity=row['llm_capacity_raw'],
                    contributes_investment=str(row['llm_deal_value_usd']) if row['llm_deal_value_usd'] else None,
                    confidence=0.70,
                    snippet=(row['snippet'] or row['title'] or '')[:500],
                )
                self._store.add_evidence(project_id, item)

                # Re-aggregate
                record = self._store.get_project(project_id)
                if record:
                    old_stage = record.stage.stage.value
                    record.stage = self._aggregate_stage(record.evidence, record.stage)
                    self._classify_project(record)
                    record.evidence_gap_flags = self._detect_gaps(record)
                    record.source_count = len({e.document_type for e in record.evidence if not e.stale})
                    evidence_dates = [e.document_date for e in record.evidence if e.document_date and not e.stale]
                    if evidence_dates:
                        record.last_evidence_date = max(evidence_dates)
                    self._store.upsert_project(record)

                    # Audit logging
                    new_stage = record.stage.stage.value
                    if is_new:
                        self._store._log_audit(
                            project_id, 'created',
                            new_value=record.project_name,
                            triggered_by=doc_id,
                        )
                    elif old_stage != new_stage:
                        self._store._log_audit(
                            project_id, 'stage_updated',
                            old_value=old_stage, new_value=new_stage,
                            triggered_by=doc_id,
                        )

                if project_id not in existing_before:
                    results['new_projects'] += 1
                    existing_before.add(project_id)

                results['ingested'] += 1

            except Exception as e:
                logger.warning("Error ingesting article %s: %s", row['id'], e)
                results['errors'] += 1

        return results

    # ==================================================================
    # EPA ID auto-discovery
    # ==================================================================

    def auto_register_epa_ids(self, db_path: Optional[str] = None) -> Dict[str, Any]:
        """Scan regulatory_evidence for EPA source rows and register new EPA IDs.

        For each EPA-sourced row, resolves the company name and registers
        the document_id as an EPA facility ID in the EntityResolver's registry.
        This enables future EPA ID lookups for companies beyond Air Products.

        Returns summary: {discovered, already_known, unresolved}.
        """
        self._ensure_resolver()
        path = db_path or self._db_path
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        results = {'discovered': 0, 'already_known': 0, 'unresolved': 0}

        try:
            rows = conn.execute(
                "SELECT document_id, company_name, company_cik, source "
                "FROM regulatory_evidence WHERE source LIKE '%epa%'"
            ).fetchall()
        finally:
            conn.close()

        if not self._entity_resolver or not hasattr(self._entity_resolver, '_registry'):
            logger.warning("EntityResolver or registry not available for EPA ID registration")
            return results

        registry = self._entity_resolver._registry

        for row in rows:
            epa_id = row['document_id']
            if not epa_id or not epa_id.strip().isdigit():
                continue

            # Check if already known
            if registry.lookup_epa_id(epa_id):
                results['already_known'] += 1
                continue

            # Resolve company
            company_key = self._resolve_company_key(row['company_name'], row['company_cik'])
            if company_key:
                # Add to runtime registry
                existing_data = registry._companies.get(company_key, {})
                existing_ids = existing_data.get('identifiers', {})
                if not isinstance(existing_ids, dict):
                    existing_ids = {}
                epa_list = existing_ids.get('epa_ids', [])
                if epa_id not in [str(x) for x in epa_list]:
                    epa_list.append(epa_id)
                    existing_ids['epa_ids'] = epa_list
                    existing_data['identifiers'] = existing_ids
                    registry._companies[company_key] = existing_data
                    registry._build_indexes()
                    results['discovered'] += 1
                    logger.info("Registered EPA ID %s for %s", epa_id, company_key)
                else:
                    results['already_known'] += 1
            else:
                results['unresolved'] += 1

        return results

    # ==================================================================
    # Query delegates
    # ==================================================================

    def get_project(self, project_id: str) -> Optional[Project]:
        return self._store.get_project(project_id)

    def get_projects_by_developer(self, developer_key: str) -> List[Project]:
        return self._store.get_projects_by_developer(developer_key)

    def get_all_projects(self, stage: Optional[str] = None) -> List[Project]:
        return self._store.get_all_projects(stage)

    # ==================================================================
    # Analysis — Spec API
    # ==================================================================

    def detect_gaps(self, project: Project) -> List[str]:
        """Get gap flags for a single project."""
        return self._detect_gaps(project)

    def find_all_gaps(self) -> List[Dict]:
        """Get all projects with evidence gaps, sorted by gap count."""
        projects = self._store.get_all_projects()
        results = []
        for p in projects:
            if p.evidence_gap_flags:
                results.append({
                    'project_id': p.project_id,
                    'project_name': p.project_name,
                    'developer': p.developer_name,
                    'stage': p.stage.stage.value,
                    'gaps': p.evidence_gap_flags,
                    'gap_count': len(p.evidence_gap_flags),
                    'evidence_count': len(p.evidence),
                })
        return sorted(results, key=lambda x: x['gap_count'], reverse=True)

    def find_gaps(self) -> List[Dict]:
        """Legacy API. Delegates to find_all_gaps."""
        return self.find_all_gaps()

    def project_summary(self) -> Dict[str, Any]:
        """Overall pipeline stats."""
        return self._store.summary()

    # ==================================================================
    # Signal extraction
    # ==================================================================

    def _extract_signal(self, doc: ProcessedDocument) -> Dict[str, Any]:
        """Extract project-relevant signals from a ProcessedDocument."""
        signal: Dict[str, Any] = {
            'developer_key': None,
            'developer_name': None,
            'project_name': None,
            'stage': None,
            'capacity': None,
            'investment': None,
            'timeline': None,
            'location_state': None,
            'technology': None,
            'epc_contractor': None,
            'product': None,
            'document_type': doc.document_type.value,
            'source_url': doc.source_url,
            'source_authority': doc.source_authority,
            'document_id': doc.document_id,
        }

        # From entities
        for entity in doc.entities:
            if entity.type == EntityType.COMPANY:
                if entity.role in (None, 'developer', 'owner'):
                    if not signal['developer_key']:
                        signal['developer_key'] = entity.canonical_key or (
                            entity.value.lower().replace(' ', '_')
                        )
                        signal['developer_name'] = entity.canonical_name or entity.value
                elif entity.role == 'epc_contractor':
                    signal['epc_contractor'] = entity.canonical_name or entity.value
            elif entity.type == EntityType.PROJECT:
                signal['project_name'] = entity.canonical_name or entity.value
            elif entity.type == EntityType.LOCATION:
                signal['location_state'] = entity.normalized or entity.value
            elif entity.type == EntityType.CONTRACTOR:
                signal['epc_contractor'] = entity.canonical_name or entity.value
            elif entity.type == EntityType.TECHNOLOGY:
                signal['technology'] = entity.value
            elif entity.type == EntityType.PRODUCT:
                signal['product'] = entity.value

        # From facts
        for fact in doc.facts:
            if fact.type == FactType.STATUS_CHANGE:
                signal['stage'] = fact.claim
            elif fact.type == FactType.CAPACITY:
                signal['capacity'] = fact.numerical_value or fact.claim
            elif fact.type == FactType.INVESTMENT:
                signal['investment'] = fact.numerical_value or fact.claim
            elif fact.type == FactType.TIMELINE:
                signal['timeline'] = fact.claim

        # Infer project name if missing
        if not signal['project_name'] and signal['developer_name']:
            if signal['location_state']:
                signal['project_name'] = f"{signal['developer_name']} {signal['location_state']} Project"
            elif signal['technology']:
                signal['project_name'] = f"{signal['developer_name']} {signal['technology']} Project"
            else:
                signal['project_name'] = f"{signal['developer_name']} Unnamed Project"
            signal['project_name_inferred'] = True

        return signal

    # ==================================================================
    # Project building
    # ==================================================================

    def _build_new_project(self, signal: Dict) -> Project:
        """Build a new Project from extracted signal."""
        return Project(
            project_name=signal.get('project_name', 'Unknown'),
            developer_key=signal.get('developer_key'),
            developer_name=signal.get('developer_name', ''),
            state=signal.get('location_state'),
            technology=signal.get('technology'),
            product=signal.get('product'),
            capacity_raw=signal.get('capacity'),
            epc_contractor=signal.get('epc_contractor'),
            stage=StageAssessment(
                stage=self._parse_stage(signal.get('stage')),
            ),
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    def _merge_signal_into_record(self, record: Project, signal: Dict):
        """Update record fields from signal if we have new info."""
        if signal.get('location_state') and not record.state:
            record.state = signal['location_state']
        if signal.get('technology') and not record.technology:
            record.technology = signal['technology']
        if signal.get('product') and not record.product:
            record.product = signal['product']
        if signal.get('capacity') and not record.capacity_raw:
            record.capacity_raw = signal['capacity']
        if signal.get('epc_contractor') and not record.epc_contractor:
            record.epc_contractor = signal['epc_contractor']

    def _build_evidence_item(self, doc: ProcessedDocument, signal: Dict) -> EvidenceItem:
        """Build an EvidenceItem from a ProcessedDocument and its signal."""
        doc_type = doc.document_type.value
        # Normalize document type for authority lookup
        authority_key = doc_type
        if doc_type in ('sec_filing',):
            authority_key = 'sec_filing'
        elif doc_type in ('epa_permit',):
            authority_key = 'epa_permit'

        return EvidenceItem(
            document_id=doc.document_id,
            document_type=doc_type,
            source_url=doc.source_url,
            source_authority=SOURCE_AUTHORITY.get(authority_key, doc.source_authority),
            document_date=doc.processed_at.strftime('%Y-%m-%d') if doc.processed_at else None,
            contributes_stage=self._to_str(signal.get('stage')),
            contributes_capacity=self._to_str(signal.get('capacity')),
            contributes_investment=self._to_str(signal.get('investment')),
            contributes_timeline=self._to_str(signal.get('timeline')),
            confidence=doc.extraction_confidence,
            snippet=doc.text_snippet[:500] if doc.text_snippet else '',
        )

    # ==================================================================
    # Stage aggregation
    # ==================================================================

    def _aggregate_stage(
        self,
        evidence: List[EvidenceItem],
        current: StageAssessment,
    ) -> StageAssessment:
        """Determine project stage from multiple evidence sources.

        Rules:
        1. Filter out stale evidence
        2. Weight by source_authority x recency_factor (90-day half-life)
        3. Confidence floor: if current confidence > 0.8 and new evidence
           confidence < 0.6, require >=2 new sources OR higher authority
        4. Multiple sources agreeing = corroboration boost (up to 1.25x)
        5. 'cancelled' always wins if any high-authority source (>=0.85) says it
        6. Otherwise pick the most advanced confirmed stage
        """
        active = [e for e in evidence if not e.stale and e.contributes_stage]
        if not active:
            return current

        now = datetime.now()

        # Collect weighted votes per stage
        stage_votes: Dict[str, List[Dict]] = {}
        for e in active:
            stage_str = self._normalize_stage_string(e.contributes_stage)
            if stage_str not in _STAGE_STR_ORDER:
                continue

            # Recency factor: exponential decay with 90-day half-life
            recency = 1.0
            if e.document_date:
                try:
                    doc_date = datetime.strptime(e.document_date[:10], '%Y-%m-%d')
                    days_old = (now - doc_date).days
                    recency = math.exp(-0.693 * days_old / 90)  # ln(2) ~ 0.693
                except (ValueError, TypeError):
                    pass

            weight = e.source_authority * recency * e.confidence if e.confidence else e.source_authority * recency

            stage_votes.setdefault(stage_str, []).append({
                'weight': weight,
                'authority': e.source_authority,
                'date': e.document_date,
                'doc_type': e.document_type,
            })

        if not stage_votes:
            return current

        # Rule 5: cancelled always wins if high-authority source says it
        if 'cancelled' in stage_votes:
            for vote in stage_votes['cancelled']:
                if vote['authority'] >= 0.85:
                    cancel_evidence = stage_votes['cancelled']
                    dates = [v['date'] for v in cancel_evidence if v['date']]
                    return StageAssessment(
                        stage=ProjectStage.CANCELLED,
                        confidence=0.95,
                        evidence_count=len(cancel_evidence),
                        latest_evidence_date=max(dates) if dates else None,
                        reasoning=f"{len(cancel_evidence)} source(s) confirm cancelled",
                    )

        # Score each stage
        best_stage = 'unknown'
        best_score = 0.0
        best_votes: List[Dict] = []

        for stage_str, votes in stage_votes.items():
            total_weight = sum(v['weight'] for v in votes)
            # Corroboration boost: 1.0 for 1 source, up to 1.25 for 3+
            corroboration = min(1.0 + 0.125 * (len(votes) - 1), 1.25)
            score = total_weight * corroboration

            if score > best_score:
                best_score = score
                best_stage = stage_str
                best_votes = votes

        # Confidence calculation
        max_possible = max(v['authority'] for v in best_votes) * 1.25
        raw_confidence = min(best_score / max_possible, 1.0) if max_possible > 0 else 0.0
        confidence = min(raw_confidence, 0.99)

        # Rule 3: Confidence floor
        if (current.confidence > 0.8 and confidence < 0.6
                and len(best_votes) < 2
                and max(v['authority'] for v in best_votes) <= current.confidence):
            # Don't downgrade — keep current stage
            return current

        dates = [v['date'] for v in best_votes if v['date']]
        doc_types = [v['doc_type'] for v in best_votes]
        reasoning_parts = [f"{len(best_votes)} source(s) confirm {best_stage}"]
        if doc_types:
            reasoning_parts.append(f"from: {', '.join(set(doc_types))}")
        if dates:
            reasoning_parts.append(f"latest: {max(dates)}")

        return StageAssessment(
            stage=self._parse_stage(best_stage),
            confidence=confidence,
            evidence_count=len(best_votes),
            latest_evidence_date=max(dates) if dates else None,
            reasoning='; '.join(reasoning_parts),
        )

    # ==================================================================
    # Gap detection
    # ==================================================================

    def _detect_gaps(self, record: Project) -> List[str]:
        """Detect missing evidence or inconsistencies."""
        flags = []
        active_evidence = [e for e in record.evidence if not e.stale]
        source_types = {e.document_type for e in active_evidence}

        # Source coverage gaps
        if 'sec_filing' not in source_types:
            flags.append('no_sec_filing')
        if 'epa_permit' not in source_types:
            flags.append('no_epa_permit')
        if 'news' not in source_types:
            flags.append('no_news_coverage')

        # Stage-specific gaps
        stage = record.stage.stage
        if stage in (ProjectStage.CONSTRUCTION, ProjectStage.COMMISSIONING, ProjectStage.OPERATIONAL):
            if 'epa_permit' not in source_types:
                flags.append('advanced_stage_no_permit')
        if stage == ProjectStage.FID:
            if 'sec_filing' not in source_types:
                flags.append('fid_no_sec_filing')

        # Data completeness
        if not record.capacity_raw:
            flags.append('missing_capacity')
        if not record.fid_date and not record.cod_date:
            flags.append('missing_timeline')
        if not record.technology:
            flags.append('missing_technology')

        # Conflict detection
        stage_claims: Dict[str, List[EvidenceItem]] = {}
        for e in active_evidence:
            if e.contributes_stage:
                normalized = self._normalize_stage_string(e.contributes_stage)
                stage_claims.setdefault(normalized, []).append(e)
        if len(stage_claims) > 1:
            flags.append(f"stage_conflict:{'|'.join(stage_claims.keys())}")

        return flags

    # ==================================================================
    # Helpers
    # ==================================================================

    def _resolve_company_key(self, name: str, cik: Optional[str] = None) -> Optional[str]:
        """Resolve a company name to its canonical key."""
        if not self._entity_resolver:
            return None
        try:
            result = self._entity_resolver.resolve(name, EntityType.COMPANY, cik=cik)
            if result.resolved:
                return result.canonical_key
        except Exception:
            pass
        return None

    def _resolve_company_name(self, name: str) -> Optional[str]:
        """Resolve a company name to its canonical display name."""
        if not self._entity_resolver:
            return None
        try:
            result = self._entity_resolver.resolve(name, EntityType.COMPANY)
            if result.resolved:
                return result.canonical_name
        except Exception:
            pass
        return None

    @staticmethod
    def _normalize_stage_string(stage: Optional[str]) -> str:
        """Normalize a stage string to match ProjectStage values."""
        if not stage:
            return 'unknown'
        s = stage.strip().lower()

        # Direct match against ProjectStage values
        if s in _STAGE_STR_ORDER:
            return s

        # Common variations
        stage_map = {
            'announced': 'announced',
            'planning': 'announced',
            'planned': 'announced',
            'proposed': 'announced',
            'pre-feed': 'pre_feed',
            'pre feed': 'pre_feed',
            'prefeed': 'pre_feed',
            'feed': 'feed',
            'front-end': 'feed',
            'fid': 'fid',
            'final investment': 'fid',
            'epc award': 'epc_award',
            'epc_award': 'epc_award',
            'construction': 'construction',
            'under construction': 'construction',
            'broke ground': 'construction',
            'building': 'construction',
            'commissioning': 'commissioning',
            'startup': 'commissioning',
            'start-up': 'commissioning',
            'operational': 'operational',
            'operating': 'operational',
            'online': 'operational',
            'producing': 'operational',
            'cancelled': 'cancelled',
            'canceled': 'cancelled',
            'suspended': 'cancelled',
            'shelved': 'cancelled',
        }

        if s in stage_map:
            return stage_map[s]

        # Partial match
        for key, val in stage_map.items():
            if key in s:
                return val

        return 'unknown'

    @staticmethod
    def _parse_confidence(value: Any) -> float:
        """Parse a confidence value that may be numeric or text (LOW/MEDIUM/HIGH)."""
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        s = str(value).strip().upper()
        text_map = {'LOW': 0.3, 'MEDIUM': 0.6, 'MED': 0.6, 'HIGH': 0.85}
        if s in text_map:
            return text_map[s]
        try:
            return float(s)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _to_str(value: Any) -> Optional[str]:
        """Coerce a value to str for SQLite binding (handles lists/dicts from LLM)."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, (list, dict)):
            import json
            return json.dumps(value)
        return str(value)

    @staticmethod
    def _parse_stage(stage_str: Optional[str]) -> ProjectStage:
        """Parse a stage string to a ProjectStage enum."""
        if not stage_str:
            return ProjectStage.UNKNOWN
        normalized = EvidenceAggregator._normalize_stage_string(stage_str)
        try:
            return ProjectStage(normalized)
        except ValueError:
            return ProjectStage.UNKNOWN

    # ==================================================================
    # Grouping dimension classifiers
    # ==================================================================

    @staticmethod
    def classify_value_chain(text: str) -> ValueChain:
        """Classify a project's value-chain / product focus from combined text.

        Uses keyword scoring — highest-scoring category wins.
        """
        t = text.lower()
        scores: Dict[ValueChain, float] = {vc: 0.0 for vc in ValueChain if vc != ValueChain.UNKNOWN}

        # Hydrogen — broad, but weighted lower per-keyword since it's often a modifier
        for kw in ('hydrogen', 'h2 ', 'h2,', 'h2.', 'blue hydrogen', 'green hydrogen',
                    'grey hydrogen', 'gray hydrogen', 'clean hydrogen', 'low-carbon hydrogen',
                    'syngas', 'electrolyz', 'electrolis', 'fuel cell', 'fuelcell',
                    'smr ', 'steam methane', 'autothermal', ' atr '):
            if kw in t:
                scores[ValueChain.HYDROGEN] += 1.5 if 'hydrogen' in kw else 1.0

        # Ammonia — strong signal
        for kw in ('ammonia', 'nh3', 'fertilizer', 'fertiliser', 'urea'):
            if kw in t:
                scores[ValueChain.AMMONIA] += 2.0

        # CCS/CCUS
        for kw in ('ccs', 'ccus', 'carbon capture', 'carbon sequestration', 'co2 ',
                    'co2,', 'co2.', 'carbon storage', 'direct air capture', 'dac ',
                    'sequestration'):
            if kw in t:
                scores[ValueChain.CCS_CCUS] += 2.0

        # LNG / natural gas
        for kw in ('lng', 'natural gas', 'gas processing', 'liquefied natural',
                    'gas pipeline', 'methane reform', 'gasification', 'petcoke',
                    'synfuel'):
            if kw in t:
                scores[ValueChain.LNG_GAS] += 2.0

        # Power
        for kw in ('power plant', 'power generation', 'electricity generation',
                    'gas turbine', 'combined cycle', 'peaker', 'power station',
                    'gigawatt', ' gw ', 'megawatt', ' mw ', 'net power',
                    'nuclear power', 'nuclear energy'):
            if kw in t:
                scores[ValueChain.POWER] += 2.0

        # Methanol
        for kw in ('methanol', 'e-methanol', 'green methanol'):
            if kw in t:
                scores[ValueChain.METHANOL] += 2.0

        # Pick highest, require minimum score
        best = max(scores, key=scores.get)
        if scores[best] < 1.0:
            return ValueChain.UNKNOWN
        return best

    @staticmethod
    def classify_end_use_sector(text: str) -> EndUseSector:
        """Classify a project's end-use sector from combined text.

        Uses keyword scoring — highest-scoring category wins.
        """
        t = text.lower()
        scores: Dict[EndUseSector, float] = {eu: 0.0 for eu in EndUseSector if eu != EndUseSector.UNKNOWN}

        # Industrial feedstock
        for kw in ('refinery', 'refining', 'feedstock', 'industrial', 'cement',
                    'steel', 'petrochemical', 'chemical plant', 'desulfurization',
                    'hydrocrack', 'hydrotreat', 'fertilizer', 'fertiliser',
                    'syngas', 'sic: 28', 'chemical manufactur', 'pulp',
                    'paper mill', 'glass', 'smelting'):
            if kw in t:
                scores[EndUseSector.INDUSTRIAL_FEEDSTOCK] += 2.0

        # Power generation
        for kw in ('power plant', 'power generation', 'electricity generation',
                    'gas turbine', 'combined cycle', 'peaker', 'grid',
                    'dispatch', 'power station', 'electric utility',
                    'co-firing', 'cofiring', 'nuclear power', 'nuclear energy',
                    'net power', 'cms energy', 'clean fuel'):
            if kw in t:
                scores[EndUseSector.POWER_GENERATION] += 2.0

        # Transport / fuel
        for kw in ('transport', 'fuel cell vehicle', 'fcev', 'trucking',
                    'aviation', 'saf', 'sustainable aviation', 'marine fuel',
                    'bunker fuel', 'shipping fuel', 'fueling station',
                    'hydrogen fuel', 'mobility'):
            if kw in t:
                scores[EndUseSector.TRANSPORT_FUEL] += 2.0

        # Export / trade
        for kw in ('export', 'import terminal', 'trade', 'offtake agreement',
                    'shipping terminal', 'port ', 'overseas', 'international market'):
            if kw in t:
                scores[EndUseSector.EXPORT_TRADE] += 2.0

        # Storage / hub
        for kw in ('storage', 'cavern', 'salt dome', 'underground storage',
                    'hydrogen hub', 'energy hub', 'h2hub', 'regional hub',
                    'clean energy hub', 'salt cavern'):
            if kw in t:
                scores[EndUseSector.STORAGE_HUB] += 2.0

        best = max(scores, key=scores.get)
        if scores[best] < 1.0:
            return EndUseSector.UNKNOWN
        return best

    def _classify_project(self, record: Project) -> None:
        """Classify a project's value_chain and end_use_sector from all its text.

        Combines project name, evidence snippets, technology, and product fields
        into one text block and runs the classifiers.
        """
        parts = [
            record.project_name or '',
            record.developer_name or '',
            record.technology or '',
            record.product or '',
        ]
        for ev in record.evidence:
            if ev.snippet and not ev.stale:
                parts.append(ev.snippet)

        combined = ' '.join(parts)

        vc = self.classify_value_chain(combined)
        eu = self.classify_end_use_sector(combined)

        # Only overwrite if we found something (don't downgrade to unknown)
        if vc != ValueChain.UNKNOWN:
            record.value_chain = vc
        if eu != EndUseSector.UNKNOWN:
            record.end_use_sector = eu

    def reclassify_all(self) -> Dict[str, Any]:
        """Re-classify value_chain and end_use_sector for all existing projects.

        Returns summary of changes.
        """
        projects = self._store.get_all_projects()
        results = {'total': len(projects), 'value_chain_set': 0, 'end_use_set': 0, 'unchanged': 0}

        for record in projects:
            old_vc = record.value_chain
            old_eu = record.end_use_sector

            self._classify_project(record)

            changed = False
            if record.value_chain != old_vc:
                results['value_chain_set'] += 1
                changed = True
            if record.end_use_sector != old_eu:
                results['end_use_set'] += 1
                changed = True

            if changed:
                self._store.upsert_project(record)
            else:
                results['unchanged'] += 1

        return results
