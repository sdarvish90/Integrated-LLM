"""
Intelligence Pipeline — Main orchestration for semantic data collection.
========================================================================

Implements the 3-stage architecture:
1. **LEARN**: Domain understanding (seed training, entity/concept registries)
2. **RECOGNIZE**: Document acquisition + processing (source adapters → processor)
3. **CONNECT**: Evidence aggregation (unified project records)

Usage::

    from intelligence_pipeline import IntelligencePipeline

    pipeline = IntelligencePipeline()

    # Full pipeline
    result = pipeline.run_full_pipeline()
    print(result.summary_text())

    # Targeted company deep-dive
    result = pipeline.run_targeted_pipeline("Air Products")

    # Fill evidence gaps
    result = pipeline.run_gap_pipeline(max_projects=10)

CLI::

    python intelligence_pipeline.py full
    python intelligence_pipeline.py targeted "Air Products"
    python intelligence_pipeline.py gaps --max-projects 5
    python intelligence_pipeline.py bootstrap
    python intelligence_pipeline.py status
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from nlp.schema import DocumentType, ProcessedDocument

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB = _HERE / 'blue_h2_intelligence.db'


# ======================================================================
# Pipeline result
# ======================================================================


@dataclass
class PipelineResult:
    """Result of a pipeline run (one or more stages)."""

    stages_run: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    elapsed_ms: int = 0

    # Per-stage timings (ms)
    stage_timings: Dict[str, int] = field(default_factory=dict)

    # LEARN
    learn_stats: Dict[str, Any] = field(default_factory=dict)

    # RECOGNIZE
    fetch_results: Dict[str, Any] = field(default_factory=dict)
    raw_fetched: int = 0
    processed_created: int = 0
    filtered_irrelevant: int = 0
    skipped_duplicate: int = 0

    # CONNECT
    projects_new: int = 0
    projects_updated: int = 0
    documents_unlinked: int = 0

    # Error recovery
    last_completed_stage: Optional[str] = None
    resumable: bool = True

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # Internal — processed docs for CONNECT stage
    _processed_docs: List[ProcessedDocument] = field(
        default_factory=list, repr=False)

    def summary_text(self) -> str:
        """Human-readable summary for CLI output."""
        lines = ["\n  ─── Pipeline Result ───"]
        lines.append(f"  Stages: {' → '.join(s.upper() for s in self.stages_run)}")

        if self.stage_timings:
            timing_parts = [f"{s}: {ms}ms" for s, ms in self.stage_timings.items()]
            lines.append(f"  Timings: {', '.join(timing_parts)}")

        lines.append(f"  Total: {self.elapsed_ms}ms")

        if self.learn_stats:
            epa = self.learn_stats.get('epa_registration', {})
            lines.append(f"  LEARN: EPA IDs discovered={epa.get('discovered', 0)}, "
                         f"already_known={epa.get('already_known', 0)}")

        if self.fetch_results:
            lines.append(f"  RECOGNIZE: {self.raw_fetched} fetched, "
                         f"{self.processed_created} processed, "
                         f"{self.filtered_irrelevant} filtered, "
                         f"{self.skipped_duplicate} deduped")
            for name, info in self.fetch_results.items():
                count = info.get('fetched', 0)
                ms = info.get('time_ms', 0)
                errs = info.get('errors', 0)
                status = f"{count} docs ({ms}ms)"
                if errs:
                    status += f" [{errs} errors]"
                lines.append(f"    {name}: {status}")

        if any([self.projects_new, self.projects_updated, self.documents_unlinked]):
            lines.append(f"  CONNECT: {self.projects_new} new, "
                         f"{self.projects_updated} updated, "
                         f"{self.documents_unlinked} unlinked")

        if self.warnings:
            lines.append(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings[:5]:
                lines.append(f"    ⚠ {w}")
            if len(self.warnings) > 5:
                lines.append(f"    ... and {len(self.warnings) - 5} more")

        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}):")
            for e in self.errors[:5]:
                lines.append(f"    ✗ {e}")

        lines.append("")
        return "\n".join(lines)


# ======================================================================
# Gap-to-adapter mapping
# ======================================================================


GAP_TO_ADAPTERS: Dict[str, List[str]] = {
    'no_sec_filing':            ['sec_filing'],
    'fid_no_sec_filing':        ['sec_filing'],
    'no_epa_permit':            ['epa_permit'],
    'advanced_stage_no_permit': ['epa_permit'],
    'no_news_coverage':         ['news_search'],
    'missing_capacity':         ['sec_filing'],
    'missing_timeline':         ['sec_filing', 'doe_award'],
    'missing_technology':       ['news_search'],
}


# ======================================================================
# Intelligence Pipeline
# ======================================================================


class IntelligencePipeline:
    """Orchestrates the LEARN → RECOGNIZE → CONNECT pipeline.

    Coordinates:
    - Source adapters (Tier 3) for data acquisition
    - DocumentProcessor (Tier 2) for extraction
    - EvidenceAggregator (Tier 5) for project unification

    All heavy resources are lazy-loaded. Pre-built instances can be
    injected to share with ``BlueH2Intelligence``.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        collector=None,
        fid_engine=None,
        domain_model=None,
        entity_resolver=None,
        progress_callback: Optional[Callable[[str, str, str], None]] = None,
    ):
        self._db_path = db_path or str(_DEFAULT_DB)
        self._collector = collector
        self._fid_engine = fid_engine
        self._domain_model = domain_model
        self._entity_resolver = entity_resolver
        self._progress = progress_callback or self._default_progress

        # Lazy-initialized components
        self._processor = None
        self._store = None
        self._aggregator = None
        self._registry = None
        self._initialized = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _ensure_initialized(self):
        """Lazy-load all pipeline components."""
        if self._initialized:
            return

        from nlp.domain_model import DomainModel
        from nlp.document_processor import DocumentProcessor
        from nlp.entity_resolver import EntityResolver
        from nlp.evidence_aggregator import EvidenceAggregator
        from nlp.project_store import ProjectStore
        from sources import build_default_registry

        # 1. Domain model
        if self._domain_model is None:
            self._domain_model = DomainModel()
            self._domain_model.load()

        # 2. Entity resolver (uses domain model for embedding fallback)
        if self._entity_resolver is None:
            self._entity_resolver = EntityResolver(
                domain_model=self._domain_model)

        # 3. Document processor (uses domain model + entity resolver)
        self._processor = DocumentProcessor(
            domain_model=self._domain_model,
            entity_resolver=self._entity_resolver,
        )

        # 4. Project store
        self._store = ProjectStore(db_path=self._db_path)

        # 5. Evidence aggregator
        self._aggregator = EvidenceAggregator(
            project_store=self._store,
            entity_resolver=self._entity_resolver,
            db_path=self._db_path,
        )

        # 6. Source registry (shares collector + fid_engine)
        self._registry = build_default_registry(
            collector=self._collector,
            fid_engine=self._fid_engine,
            domain_model=self._domain_model,
        )

        # 7. Staging table for crash-safe processed documents
        self._init_staging_table()

        self._initialized = True
        logger.info("IntelligencePipeline initialized (db=%s)", self._db_path)

    # ------------------------------------------------------------------
    # Progress reporting
    # ------------------------------------------------------------------

    @staticmethod
    def _default_progress(stage: str, step: str, detail: str = ''):
        detail_str = f": {detail}" if detail else ''
        print(f"  [{stage.upper()}] {step}{detail_str}")

    # ------------------------------------------------------------------
    # Stage 1: LEARN
    # ------------------------------------------------------------------

    def learn(self, seed_dir: Optional[str] = None) -> Dict[str, Any]:
        """Load domain knowledge and register known identifiers.

        1. Load seed training data into DomainModel.
        2. Auto-register EPA IDs from existing regulatory evidence.
        """
        self._ensure_initialized()
        stats: Dict[str, Any] = {}

        # Seed training
        self._progress('learn', 'Loading seed training data')
        try:
            seed_result = self._registry.fetch('seed_training',
                                               seed_dir=seed_dir)
            domain_data = seed_result.domain_data
            stats['seed_training'] = {
                'domains': domain_data.get('domain_names', []),
                'stats': domain_data.get('domain_stats', {}),
                'errors': [str(e.message) for e in seed_result.errors],
            }
            domain_count = len(domain_data.get('domain_names', []))
            self._progress('learn', 'Seed training loaded',
                           f"{domain_count} domains")
        except Exception as exc:
            stats['seed_training'] = {'error': str(exc)}
            logger.warning("Seed training failed: %s", exc)

        # Auto-register EPA IDs
        self._progress('learn', 'Registering EPA IDs from evidence')
        try:
            epa_stats = self._aggregator.auto_register_epa_ids(
                db_path=self._db_path)
            stats['epa_registration'] = epa_stats
            self._progress('learn', 'EPA IDs',
                           f"discovered={epa_stats.get('discovered', 0)}, "
                           f"already_known={epa_stats.get('already_known', 0)}")
        except Exception as exc:
            stats['epa_registration'] = {'error': str(exc)}
            logger.warning("EPA ID registration failed: %s", exc)

        return stats

    # ------------------------------------------------------------------
    # Stage 2: RECOGNIZE
    # ------------------------------------------------------------------

    def recognize_all(self, refresh: bool = True,
                      exclude_adapters: Optional[Set[str]] = None) -> PipelineResult:
        """Fetch from all adapters, process raw docs, route by output type."""
        self._ensure_initialized()
        result = PipelineResult(started_at=datetime.now())

        # Get adapter names sorted by priority (exclude seed_training + user exclusions)
        skip = {'seed_training'}
        if exclude_adapters:
            skip |= exclude_adapters
        adapter_order = self._get_adapter_order(exclude=skip)

        # Fetch from each adapter
        fetch_results = self._fetch_adapters(adapter_order, refresh=refresh,
                                             result=result)

        # Route and process
        processed, proc_stats = self._route_and_process(fetch_results)
        result._processed_docs = processed
        result.processed_created = proc_stats['processed']
        result.filtered_irrelevant = proc_stats['filtered']
        result.skipped_duplicate = proc_stats['skipped']
        result.raw_fetched = proc_stats['total_raw']

        self._progress('recognize', 'Result',
                       f"{result.processed_created} processed, "
                       f"{result.filtered_irrelevant} filtered, "
                       f"{result.skipped_duplicate} deduped")

        result.stages_run.append('recognize')
        return result

    def recognize_targeted(self, company_name: str,
                           **kwargs) -> PipelineResult:
        """Targeted fetch for a specific company (SEC, EPA, DOE)."""
        self._ensure_initialized()
        result = PipelineResult(started_at=datetime.now())

        self._progress('recognize', f'Targeted fetch for "{company_name}"')

        # Only use targeted adapters
        targeted = self._registry.get_targeted_adapters()
        fetch_results: Dict[str, Any] = {}
        for adapter in targeted:
            name = adapter.name
            try:
                fetch_kw = dict(kwargs)
                fetch_kw['company_name'] = company_name
                fetch_kw.setdefault('refresh', True)
                fr = adapter.fetch(**fetch_kw)
                fetch_results[name] = fr
                result.fetch_results[name] = {
                    'fetched': fr.fetched_count,
                    'time_ms': fr.fetch_time_ms,
                    'errors': fr.error_count,
                }
                self._progress('recognize', f'Fetched {name}',
                               f"{fr.fetched_count} docs ({fr.fetch_time_ms}ms)")
            except Exception as exc:
                self._handle_fetch_error(name, exc, result)

        # Route and process
        processed, proc_stats = self._route_and_process(fetch_results)
        result._processed_docs = processed
        result.processed_created = proc_stats['processed']
        result.filtered_irrelevant = proc_stats['filtered']
        result.skipped_duplicate = proc_stats['skipped']
        result.raw_fetched = proc_stats['total_raw']

        result.stages_run.append('recognize')
        return result

    def recognize_from_gaps(self, max_projects: int = 10) -> PipelineResult:
        """Gap-driven targeted collection.

        Finds projects with the most evidence gaps, maps gap flags to
        adapters, deduplicates by (adapter, company), and fetches.
        """
        self._ensure_initialized()
        result = PipelineResult(started_at=datetime.now())

        gaps = self._aggregator.find_all_gaps()
        if not gaps:
            self._progress('recognize', 'No gaps found')
            result.stages_run.append('recognize')
            return result

        target_projects = gaps[:max_projects]
        self._progress('recognize', f'Targeting {len(target_projects)} projects with gaps')

        # Build deduplicated fetch plan: (adapter_name, company) → {project_ids}
        fetch_plan: Dict[Tuple[str, str], Set[str]] = {}
        for gap_info in target_projects:
            project = self._store.get_project(gap_info['project_id'])
            if not project:
                continue
            dev_name = project.developer_name or ''
            state = project.state or ''

            for flag in gap_info.get('gaps', []):
                base_flag = flag.split(':')[0]
                for adapter_name in GAP_TO_ADAPTERS.get(base_flag, []):
                    key = (adapter_name, dev_name)
                    fetch_plan.setdefault(key, set()).add(gap_info['project_id'])

        self._progress('recognize', 'Fetch plan',
                       f"{len(fetch_plan)} unique (adapter, company) pairs")

        # Execute fetches
        fetch_results: Dict[str, Any] = {}
        for (adapter_name, company), project_ids in fetch_plan.items():
            label = f"{adapter_name}:{company}" if company else adapter_name
            try:
                kw: Dict[str, Any] = {'refresh': True}
                if company:
                    kw['company_name'] = company
                if adapter_name == 'news_search' and company:
                    kw['queries'] = [f"{company} hydrogen project"]

                fr = self._registry.fetch(adapter_name, **kw)
                fetch_results[label] = fr
                result.fetch_results[label] = {
                    'fetched': fr.fetched_count,
                    'time_ms': fr.fetch_time_ms,
                    'errors': fr.error_count,
                    'fills_projects': list(project_ids),
                }
                self._progress('recognize', f'Fetched {label}',
                               f"{fr.fetched_count} docs ({fr.fetch_time_ms}ms)")
            except Exception as exc:
                self._handle_fetch_error(label, exc, result)

        # Route and process
        processed, proc_stats = self._route_and_process(fetch_results)
        result._processed_docs = processed
        result.processed_created = proc_stats['processed']
        result.filtered_irrelevant = proc_stats['filtered']
        result.skipped_duplicate = proc_stats['skipped']
        result.raw_fetched = proc_stats['total_raw']

        result.stages_run.append('recognize')
        return result

    # ------------------------------------------------------------------
    # Stage 3: CONNECT
    # ------------------------------------------------------------------

    def connect(self, processed_docs: List[ProcessedDocument],
                dry_run: bool = False) -> Dict[str, Any]:
        """Aggregate processed documents into unified projects.

        If *processed_docs* is empty, automatically loads un-ingested
        documents from the ``pipeline_staging`` table so that interrupted
        RECOGNIZE runs can resume into CONNECT without data loss.
        """
        self._ensure_initialized()

        # Fall back to staged documents if none passed in memory
        if not processed_docs:
            staged = self._load_staged_documents(only_unprocessed=True)
            if staged:
                self._progress('connect', 'Loaded from staging',
                               f"{len(staged)} documents recovered")
                processed_docs = staged

        if not processed_docs:
            return {'ingested': 0, 'new_projects': 0,
                    'updated_projects': 0, 'unlinked': 0}

        if dry_run:
            self._progress('connect', 'Dry run',
                           f"would process {len(processed_docs)} documents")
            return {'would_process': len(processed_docs), 'dry_run': True}

        self._progress('connect', f'Aggregating {len(processed_docs)} documents')

        # Aggregate one at a time and mark each as ingested in staging
        new_projects: set = set()
        updated_projects: set = set()
        unlinked = 0
        existing_ids = {
            p.project_id for p in self._store.get_all_projects()
        }

        for doc in processed_docs:
            try:
                projects = self._aggregator.aggregate(doc)
                if not projects:
                    unlinked += 1
                else:
                    pid = projects[0].project_id
                    if pid in existing_ids:
                        updated_projects.add(pid)
                    else:
                        new_projects.add(pid)
                        existing_ids.add(pid)
                # Mark as ingested in staging (crash-safe checkpoint)
                self._mark_staged_ingested(doc.document_id)
            except Exception as e:
                logger.warning("Aggregation error for %s: %s",
                               doc.document_id, e)

        summary = {
            'ingested': len(processed_docs) - unlinked,
            'new_projects': len(new_projects),
            'updated_projects': len(updated_projects),
            'unlinked': unlinked,
        }

        # Clean up ingested staging entries
        self._clear_staging(only_ingested=True)

        self._progress('connect', 'Result',
                       f"{summary.get('new_projects', 0)} new, "
                       f"{summary.get('updated_projects', 0)} updated, "
                       f"{summary.get('unlinked', 0)} unlinked")
        return summary

    def connect_bootstrap(self, dry_run: bool = False) -> Dict[str, Any]:
        """Bootstrap from existing regulatory_evidence + articles tables."""
        self._ensure_initialized()

        if dry_run:
            self._progress('connect', 'Dry run — would bootstrap from existing tables')
            return {'dry_run': True}

        stats: Dict[str, Any] = {}

        self._progress('connect', 'Bootstrapping from regulatory_evidence')
        reg = self._aggregator.ingest_from_regulatory_evidence(
            db_path=self._db_path)
        stats['regulatory'] = reg
        self._progress('connect', 'Regulatory',
                       f"ingested={reg.get('ingested', 0)}, "
                       f"new_projects={reg.get('new_projects', 0)}")

        self._progress('connect', 'Bootstrapping from articles')
        art = self._aggregator.ingest_from_articles(db_path=self._db_path)
        stats['articles'] = art
        self._progress('connect', 'Articles',
                       f"ingested={art.get('ingested', 0)}, "
                       f"new_projects={art.get('new_projects', 0)}")

        self._progress('connect', 'Reclassifying all projects')
        reclass = self._aggregator.reclassify_all()
        stats['reclassify'] = reclass
        self._progress('connect', 'Reclassify',
                       f"value_chain_set={reclass.get('value_chain_set', 0)}, "
                       f"end_use_set={reclass.get('end_use_set', 0)}")

        return stats

    # ------------------------------------------------------------------
    # Composite pipeline methods
    # ------------------------------------------------------------------

    def run_full_pipeline(self, resume_from: Optional[str] = None,
                          dry_run: bool = False,
                          exclude_adapters: Optional[Set[str]] = None,
                          refresh: bool = True) -> PipelineResult:
        """Run LEARN → RECOGNIZE(all) → CONNECT.

        Args:
            resume_from: ``'recognize'`` to skip LEARN,
                         ``'connect'`` to skip LEARN + RECOGNIZE.
            dry_run: Fetch and process but don't persist to DB.
            exclude_adapters: Set of adapter names to skip in RECOGNIZE.
            refresh: If False, adapters read cached data only (no API calls).
        """
        self._ensure_initialized()
        result = PipelineResult(started_at=datetime.now())

        # LEARN
        if resume_from not in ('recognize', 'connect'):
            t0 = time.time()
            self._progress('learn', 'Starting LEARN stage')
            result.learn_stats = self.learn()
            result.stages_run.append('learn')
            result.stage_timings['learn'] = int((time.time() - t0) * 1000)
            result.last_completed_stage = 'learn'

        # RECOGNIZE
        if resume_from != 'connect':
            t0 = time.time()
            self._progress('recognize', 'Starting RECOGNIZE stage')
            rec = self.recognize_all(exclude_adapters=exclude_adapters,
                                     refresh=refresh)
            result.stages_run.append('recognize')
            result.stage_timings['recognize'] = int((time.time() - t0) * 1000)
            result.last_completed_stage = 'recognize'
            # Merge recognize stats into result
            result.fetch_results = rec.fetch_results
            result.raw_fetched = rec.raw_fetched
            result.processed_created = rec.processed_created
            result.filtered_irrelevant = rec.filtered_irrelevant
            result.skipped_duplicate = rec.skipped_duplicate
            result.warnings.extend(rec.warnings)
            result.errors.extend(rec.errors)
            result._processed_docs = rec._processed_docs

        # CONNECT
        t0 = time.time()
        self._progress('connect', 'Starting CONNECT stage')
        connect_stats = self.connect(result._processed_docs, dry_run=dry_run)
        result.stages_run.append('connect')
        result.stage_timings['connect'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'connect'
        result.projects_new = connect_stats.get('new_projects', 0)
        result.projects_updated = connect_stats.get('updated_projects', 0)
        result.documents_unlinked = connect_stats.get('unlinked', 0)

        result.finished_at = datetime.now()
        result.elapsed_ms = int(
            (result.finished_at - result.started_at).total_seconds() * 1000)
        return result

    def run_targeted_pipeline(self, company_name: str,
                              dry_run: bool = False,
                              **kwargs) -> PipelineResult:
        """LEARN → RECOGNIZE(targeted) → CONNECT for one company."""
        self._ensure_initialized()
        result = PipelineResult(started_at=datetime.now())

        # LEARN
        t0 = time.time()
        result.learn_stats = self.learn()
        result.stages_run.append('learn')
        result.stage_timings['learn'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'learn'

        # RECOGNIZE (targeted)
        t0 = time.time()
        rec = self.recognize_targeted(company_name, **kwargs)
        result.stages_run.append('recognize')
        result.stage_timings['recognize'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'recognize'
        result.fetch_results = rec.fetch_results
        result.raw_fetched = rec.raw_fetched
        result.processed_created = rec.processed_created
        result.filtered_irrelevant = rec.filtered_irrelevant
        result.skipped_duplicate = rec.skipped_duplicate
        result.warnings.extend(rec.warnings)
        result.errors.extend(rec.errors)
        result._processed_docs = rec._processed_docs

        # CONNECT
        t0 = time.time()
        connect_stats = self.connect(result._processed_docs, dry_run=dry_run)
        result.stages_run.append('connect')
        result.stage_timings['connect'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'connect'
        result.projects_new = connect_stats.get('new_projects', 0)
        result.projects_updated = connect_stats.get('updated_projects', 0)
        result.documents_unlinked = connect_stats.get('unlinked', 0)

        result.finished_at = datetime.now()
        result.elapsed_ms = int(
            (result.finished_at - result.started_at).total_seconds() * 1000)
        return result

    def run_gap_pipeline(self, max_projects: int = 10,
                         dry_run: bool = False) -> PipelineResult:
        """LEARN → RECOGNIZE(from_gaps) → CONNECT to fill evidence gaps."""
        self._ensure_initialized()
        result = PipelineResult(started_at=datetime.now())

        # LEARN
        t0 = time.time()
        result.learn_stats = self.learn()
        result.stages_run.append('learn')
        result.stage_timings['learn'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'learn'

        # RECOGNIZE (gap-driven)
        t0 = time.time()
        rec = self.recognize_from_gaps(max_projects=max_projects)
        result.stages_run.append('recognize')
        result.stage_timings['recognize'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'recognize'
        result.fetch_results = rec.fetch_results
        result.raw_fetched = rec.raw_fetched
        result.processed_created = rec.processed_created
        result.filtered_irrelevant = rec.filtered_irrelevant
        result.skipped_duplicate = rec.skipped_duplicate
        result.warnings.extend(rec.warnings)
        result.errors.extend(rec.errors)
        result._processed_docs = rec._processed_docs

        # CONNECT
        t0 = time.time()
        connect_stats = self.connect(result._processed_docs, dry_run=dry_run)
        result.stages_run.append('connect')
        result.stage_timings['connect'] = int((time.time() - t0) * 1000)
        result.last_completed_stage = 'connect'
        result.projects_new = connect_stats.get('new_projects', 0)
        result.projects_updated = connect_stats.get('updated_projects', 0)
        result.documents_unlinked = connect_stats.get('unlinked', 0)

        result.finished_at = datetime.now()
        result.elapsed_ms = int(
            (result.finished_at - result.started_at).total_seconds() * 1000)
        return result

    # ------------------------------------------------------------------
    # Query / status delegates
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Pipeline health, component availability, and data state."""
        self._ensure_initialized()
        info: Dict[str, Any] = {}

        # LLM availability
        try:
            from llm_client import llm_client as _llm
            info['llm_backend'] = _llm.check()
        except Exception:
            info['llm_backend'] = None

        # Domain model
        info['domain_model_loaded'] = self._domain_model is not None

        # Resolver stats
        try:
            info['resolver'] = self._entity_resolver.get_resolution_stats()
        except Exception:
            info['resolver'] = {}

        # Adapter names
        info['adapters'] = self._registry.adapter_names

        # Data summary
        info['data'] = self._aggregator.project_summary()

        return info

    def project_summary(self) -> Dict[str, Any]:
        """Delegate to EvidenceAggregator.project_summary()."""
        self._ensure_initialized()
        return self._aggregator.project_summary()

    def find_gaps(self) -> List[Dict]:
        """Delegate to EvidenceAggregator.find_all_gaps()."""
        self._ensure_initialized()
        return self._aggregator.find_all_gaps()

    def reclassify_all(self) -> Dict[str, Any]:
        """Delegate to EvidenceAggregator.reclassify_all()."""
        self._ensure_initialized()
        return self._aggregator.reclassify_all()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_adapter_order(self, exclude: Optional[Set[str]] = None) -> List[str]:
        """Get adapter names sorted by priority, optionally excluding some."""
        exclude = exclude or set()
        all_names = self._registry.adapter_names
        # Sort by adapter priority
        adapters = [(self._registry.get(n), n) for n in all_names
                     if n not in exclude]
        adapters.sort(key=lambda pair: pair[0].priority if pair[0] else 999)
        return [name for _, name in adapters]

    def _fetch_adapters(self, adapter_names: List[str],
                        refresh: bool = True,
                        result: Optional[PipelineResult] = None,
                        **extra_kwargs) -> Dict[str, Any]:
        """Fetch from a list of adapters sequentially.

        Non-fatal errors are logged as warnings; the pipeline continues.
        """
        from sources.base import FetchResult as _FR
        fetch_results: Dict[str, _FR] = {}
        for name in adapter_names:
            try:
                fr = self._registry.fetch(name, refresh=refresh, **extra_kwargs)
                fetch_results[name] = fr
                if result is not None:
                    result.fetch_results[name] = {
                        'fetched': fr.fetched_count,
                        'time_ms': fr.fetch_time_ms,
                        'errors': fr.error_count,
                    }
                self._progress('recognize', f'Fetched {name}',
                               f"{fr.fetched_count} docs ({fr.fetch_time_ms}ms)")
            except Exception as exc:
                self._handle_fetch_error(name, exc,
                                         result or PipelineResult())
        return fetch_results

    def _handle_fetch_error(self, adapter_name: str, exc: Exception,
                            result: PipelineResult):
        """Classify and log a fetch error without stopping the pipeline."""
        err_msg = str(exc)
        if 'rate' in err_msg.lower() or '429' in err_msg:
            msg = f"{adapter_name}: rate limited, skipping"
        else:
            msg = f"{adapter_name}: {err_msg[:200]}"
        result.warnings.append(msg)
        logger.warning("Adapter fetch failed: %s", msg)

    # ------------------------------------------------------------------
    # Staging table — crash-safe persistence of ProcessedDocuments
    # ------------------------------------------------------------------

    def _init_staging_table(self):
        """Create the staging table if it doesn't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_staging (
                    document_id TEXT PRIMARY KEY,
                    document_json TEXT NOT NULL,
                    staged_at TEXT NOT NULL,
                    run_id TEXT,
                    ingested INTEGER DEFAULT 0
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _stage_document(self, doc: ProcessedDocument, run_id: str):
        """Persist a ProcessedDocument to the staging table immediately."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """INSERT OR REPLACE INTO pipeline_staging
                   (document_id, document_json, staged_at, run_id, ingested)
                   VALUES (?, ?, ?, ?, 0)""",
                (doc.document_id,
                 json.dumps(doc.to_dict(), default=str),
                 datetime.now().isoformat(),
                 run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _load_staged_documents(self, only_unprocessed: bool = True
                               ) -> List[ProcessedDocument]:
        """Load ProcessedDocuments from the staging table."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            if only_unprocessed:
                rows = conn.execute(
                    "SELECT document_json FROM pipeline_staging WHERE ingested = 0"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT document_json FROM pipeline_staging"
                ).fetchall()
        finally:
            conn.close()

        docs = []
        for row in rows:
            try:
                data = json.loads(row['document_json'])
                docs.append(ProcessedDocument.from_dict(data))
            except Exception as e:
                logger.warning("Failed to deserialize staged doc: %s", e)
        return docs

    def _mark_staged_ingested(self, document_id: str):
        """Mark a staged document as successfully ingested."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "UPDATE pipeline_staging SET ingested = 1 WHERE document_id = ?",
                (document_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def _clear_staging(self, only_ingested: bool = True):
        """Remove staged documents (all, or only those already ingested)."""
        conn = sqlite3.connect(self._db_path)
        try:
            if only_ingested:
                conn.execute(
                    "DELETE FROM pipeline_staging WHERE ingested = 1")
            else:
                conn.execute("DELETE FROM pipeline_staging")
            conn.commit()
        finally:
            conn.close()

    def _is_already_staged(self, document_id: str) -> bool:
        """Check if a document is already in the staging table."""
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT 1 FROM pipeline_staging WHERE document_id = ? LIMIT 1",
                    (document_id,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception:
            return False

    def _route_and_process(
        self,
        fetch_results: Dict[str, Any],
        batch_size: int = 50,
    ) -> Tuple[List[ProcessedDocument], Dict[str, int]]:
        """Route FetchResults by output_type and process raw documents.

        Each successfully processed document is staged to the DB immediately,
        so progress is not lost if the pipeline is interrupted.

        Returns:
            (processed_docs, stats_dict)
        """
        from sources.base import AdapterOutputType

        run_id = uuid.uuid4().hex[:12]
        all_processed: List[ProcessedDocument] = []
        stats = {'processed': 0, 'filtered': 0, 'skipped': 0,
                 'errors': 0, 'total_raw': 0, 'already_processed': 0}

        # Collect all raw documents
        raw_docs = []
        for _name, fr in fetch_results.items():
            if fr.output_type == AdapterOutputType.RAW:
                raw_docs.extend(fr.raw_documents)
            elif fr.output_type == AdapterOutputType.PROCESSED:
                for pd in fr.processed_documents:
                    if not self._is_already_ingested(pd.document_id):
                        all_processed.append(pd)
                        self._stage_document(pd, run_id)
                        stats['already_processed'] += 1
                    else:
                        stats['skipped'] += 1
            # DOMAIN_DATA is handled in learn(), skip here

        stats['total_raw'] = len(raw_docs)
        self._progress('recognize', 'Documents collected',
                       f"{len(raw_docs)} raw docs to process via LLM")

        # Process raw documents in batches
        total_batches = (len(raw_docs) + batch_size - 1) // batch_size if raw_docs else 0
        for batch_idx in range(total_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(raw_docs))
            batch = raw_docs[start:end]

            if total_batches > 1:
                self._progress('recognize',
                               f'Processing batch {batch_idx + 1}/{total_batches}',
                               f"{len(batch)} docs")

            for doc_idx, raw_doc in enumerate(batch):
                doc_id = raw_doc.document_id
                if self._is_already_ingested(doc_id) or self._is_already_staged(doc_id):
                    stats['skipped'] += 1
                    self._progress('recognize', 'Skip (dedup)',
                                   f"doc {start + doc_idx + 1}/{stats['total_raw']}: {doc_id[:12]}...")
                    continue
                try:
                    title_hint = getattr(raw_doc, 'title', '') or ''
                    self._progress('recognize', 'Processing',
                                   f"doc {start + doc_idx + 1}/{stats['total_raw']}: "
                                   f"{title_hint[:60] or doc_id[:12]}...")
                    t0 = time.time()
                    processed = self._processor.process(
                        **raw_doc.to_processor_kwargs())
                    elapsed_s = time.time() - t0
                    if processed:
                        all_processed.append(processed)
                        self._stage_document(processed, run_id)
                        stats['processed'] += 1
                        self._progress('recognize', 'Staged',
                                       f"doc {start + doc_idx + 1}/{stats['total_raw']}: "
                                       f"relevance={processed.relevance_score:.2f} "
                                       f"({elapsed_s:.1f}s)")
                    else:
                        stats['filtered'] += 1
                        self._progress('recognize', 'Filtered',
                                       f"doc {start + doc_idx + 1}/{stats['total_raw']}: "
                                       f"irrelevant ({elapsed_s:.1f}s)")
                except Exception as exc:
                    stats['errors'] += 1
                    logger.warning("Processing error for %s: %s", doc_id, exc)
                    self._progress('recognize', 'Error',
                                   f"doc {start + doc_idx + 1}/{stats['total_raw']}: {exc}")

        return all_processed, stats

    def _is_already_ingested(self, document_id: str) -> bool:
        """Check if a document is already linked in project_evidence."""
        try:
            conn = sqlite3.connect(self._db_path)
            try:
                row = conn.execute(
                    "SELECT 1 FROM project_evidence WHERE document_id = ? LIMIT 1",
                    (document_id,),
                ).fetchone()
                return row is not None
            finally:
                conn.close()
        except Exception:
            return False


# ======================================================================
# CLI entry point
# ======================================================================


def _main():
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description='Intelligence Pipeline — LEARN → RECOGNIZE → CONNECT')
    parser.add_argument(
        'command',
        choices=['full', 'targeted', 'gaps', 'bootstrap', 'status'],
        nargs='?', default='status',
        help='Pipeline command to run')
    parser.add_argument(
        'company', nargs='?', default=None,
        help='Company name (for "targeted" command)')
    parser.add_argument(
        '--resume-from', choices=['recognize', 'connect'],
        help='Skip earlier stages (for "full" command)')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Fetch and process but do not persist to DB')
    parser.add_argument(
        '--max-projects', type=int, default=10,
        help='Max projects to target (for "gaps" command)')
    parser.add_argument(
        '--exclude-adapters', nargs='+', default=[],
        help='Adapter names to skip (e.g. sec_filing)')
    parser.add_argument(
        '--no-refresh', action='store_true',
        help='Read cached data only — skip API calls to external sources')
    parser.add_argument(
        '--verbose', '-v', action='store_true',
        help='Enable debug logging')
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(levelname)s %(name)s: %(message)s')
    else:
        logging.basicConfig(level=logging.WARNING)

    pipeline = IntelligencePipeline()

    exclude = set(args.exclude_adapters) if args.exclude_adapters else None

    if args.command == 'full':
        out = pipeline.run_full_pipeline(
            resume_from=args.resume_from, dry_run=args.dry_run,
            exclude_adapters=exclude,
            refresh=not args.no_refresh)
    elif args.command == 'targeted':
        company = args.company or input('Company name: ').strip()
        if not company:
            print("  No company specified.")
            return
        out = pipeline.run_targeted_pipeline(company, dry_run=args.dry_run)
    elif args.command == 'gaps':
        out = pipeline.run_gap_pipeline(
            max_projects=args.max_projects, dry_run=args.dry_run)
    elif args.command == 'bootstrap':
        out = pipeline.connect_bootstrap(dry_run=args.dry_run)
    elif args.command == 'status':
        out = pipeline.status()
    else:
        parser.print_help()
        return

    # Print result
    if isinstance(out, PipelineResult):
        print(out.summary_text())
    else:
        print(json.dumps(out, indent=2, default=str))


if __name__ == '__main__':
    _main()
