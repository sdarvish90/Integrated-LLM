"""
Source Adapter base classes and registry.
=========================================

All source adapters share a common interface via :class:`SourceAdapter`.
Each adapter wraps existing fetch logic — it does NOT rewrite it.

Three output types:
- **RAW** — produces :class:`RawDocument`, must go through DocumentProcessor
- **PROCESSED** — produces :class:`ProcessedDocument` directly
- **DOMAIN_DATA** — feeds DomainModel (seed training), not DocumentProcessor
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from nlp.schema import DocumentType, ProcessedDocument, RawDocument

logger = logging.getLogger(__name__)


# ======================================================================
# Enums and dataclasses
# ======================================================================


class AdapterOutputType(Enum):
    """What kind of output an adapter produces."""
    RAW = "raw"
    PROCESSED = "processed"
    DOMAIN_DATA = "domain_data"


@dataclass
class FetchError:
    """Structured error from a fetch operation."""
    error_type: str         # "rate_limit", "auth_failed", "timeout", "parse_error", "network"
    message: str
    document_id: Optional[str] = None
    retryable: bool = True
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class FetchResult:
    """Standardized result from any adapter's ``fetch()`` call."""
    adapter_name: str
    output_type: AdapterOutputType

    # Documents (populated depending on output_type)
    raw_documents: List[RawDocument] = field(default_factory=list)
    processed_documents: List[ProcessedDocument] = field(default_factory=list)
    domain_data: Dict[str, Any] = field(default_factory=dict)

    # Stats
    fetched_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    fetch_time_ms: int = 0

    # Structured errors
    errors: List[FetchError] = field(default_factory=list)

    # Provenance
    fetch_params: Dict[str, Any] = field(default_factory=dict)
    fetch_timestamp: datetime = field(default_factory=datetime.now)

    # Caching
    cache_key: Optional[str] = None
    cache_ttl_seconds: int = 0      # 0 = don't cache
    from_cache: bool = False

    @property
    def success(self) -> bool:
        return self.error_count == 0 or self.fetched_count > 0


# ======================================================================
# Abstract base: SourceAdapter
# ======================================================================


class SourceAdapter(ABC):
    """Abstract base for all source adapters.

    Subclasses MUST implement:
        - name (property)
        - output_type (property)
        - fetch(**kwargs) -> FetchResult

    Subclasses MAY override:
        - document_types, supports_targeted_fetch
        - validate_config(), health_check()
        - priority, rate_limit_seconds
    """

    priority: int = 50                  # 0 = highest, 100 = lowest
    rate_limit_seconds: float = 0.0     # per-adapter rate limit
    _last_fetch_time: float = 0.0

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique adapter name (e.g. ``'sec_filing'``, ``'news_rss'``)."""
        ...

    @property
    @abstractmethod
    def output_type(self) -> AdapterOutputType:
        """What this adapter produces."""
        ...

    @property
    def document_types(self) -> List[DocumentType]:
        """Document types this adapter can produce."""
        return [DocumentType.UNKNOWN]

    @property
    def supports_targeted_fetch(self) -> bool:
        """Whether this adapter supports ``company_name=`` filtering."""
        return False

    @abstractmethod
    def fetch(self, **kwargs) -> FetchResult:
        """Fetch documents from the source (synchronous)."""
        ...

    async def fetch_async(self, **kwargs) -> FetchResult:
        """Async fetch. Default wraps sync ``fetch()`` in a thread."""
        return await asyncio.to_thread(self.fetch, **kwargs)

    def validate_config(self) -> bool:
        """Check that the adapter is properly configured."""
        return True

    def health_check(self) -> bool:
        """Quick connectivity / availability check."""
        return True

    def _respect_rate_limit(self) -> None:
        """Sleep if called too soon after the previous fetch."""
        if self.rate_limit_seconds > 0:
            elapsed = time.time() - self._last_fetch_time
            if elapsed < self.rate_limit_seconds:
                time.sleep(self.rate_limit_seconds - elapsed)
        self._last_fetch_time = time.time()


# ======================================================================
# Shared base for regulatory evidence adapters
# ======================================================================


class _RegulatoryEvidenceAdapter(SourceAdapter):
    """Internal base for adapters that wrap ``FIDProbabilityEngine`` methods.

    Subclasses provide:
        - ``_source_filter`` — SQL value(s) for the ``source`` column
        - ``_doc_type`` — :class:`DocumentType` for produced ``RawDocument`` objects
        - ``_run_collection(**kwargs)`` — calls the engine's collect/search method
    """

    def __init__(self, fid_engine=None):
        self._engine = fid_engine

    @property
    def output_type(self) -> AdapterOutputType:
        return AdapterOutputType.RAW

    # --- subclass hooks ---

    @property
    def _source_filter(self) -> str:
        """Comma-separated source values for the ``regulatory_evidence`` table."""
        raise NotImplementedError

    @property
    def _doc_type(self) -> DocumentType:
        raise NotImplementedError

    def _run_collection(self, **kwargs) -> Any:
        """Call the specific engine method. Returns whatever the engine returns."""
        raise NotImplementedError

    # --- main fetch ---

    def fetch(self, refresh: bool = True, **kwargs) -> FetchResult:
        """Fetch regulatory evidence.

        Args:
            refresh: If ``True``, call the API first (via ``_run_collection``),
                then read fresh results from SQLite.  If ``False``, read
                existing cached data only.
        """
        start = time.time()
        self._ensure_engine()
        self._respect_rate_limit()
        errors: List[FetchError] = []

        if refresh:
            try:
                self._run_collection(**kwargs)
            except Exception as exc:
                err_type = self._classify_error(exc)
                errors.append(FetchError(
                    error_type=err_type,
                    message=str(exc)[:200],
                    retryable=err_type in ("rate_limit", "timeout", "network"),
                ))

        raw_docs = self._read_evidence(
            self._source_filter,
            company=kwargs.get("company_name"),
            limit=kwargs.get("limit", 100),
            since=kwargs.get("since"),
        )

        elapsed = int((time.time() - start) * 1000)
        return FetchResult(
            adapter_name=self.name,
            output_type=self.output_type,
            raw_documents=raw_docs,
            fetched_count=len(raw_docs),
            error_count=len(errors),
            errors=errors,
            fetch_time_ms=elapsed,
            fetch_params=kwargs,
            cache_key=f"{self.name}:{kwargs.get('company_name', 'all')}",
            cache_ttl_seconds=86400,
        )

    # --- helpers ---

    def _ensure_engine(self):
        if self._engine is None:
            from fid_probability import FIDProbabilityEngine
            self._engine = FIDProbabilityEngine()

    def _read_evidence(
        self,
        source_filter: str,
        company: Optional[str] = None,
        limit: int = 100,
        since: Optional[str] = None,
    ) -> List[RawDocument]:
        """Read from ``regulatory_evidence`` table and convert."""
        conn = sqlite3.connect(self._engine.db_path)
        conn.row_factory = sqlite3.Row

        sources = [s.strip() for s in source_filter.split(",")]
        placeholders = ",".join("?" * len(sources))
        conditions = [f"source IN ({placeholders})", "stale=0"]
        params: list = list(sources)

        if company:
            conditions.append("LOWER(company_name)=LOWER(?)")
            params.append(company)
        if since:
            conditions.append("fetched_date>=?")
            params.append(since)

        where = " AND ".join(conditions)
        query = (
            f"SELECT * FROM regulatory_evidence "
            f"WHERE {where} ORDER BY fetched_date DESC LIMIT ?"
        )
        params.append(limit)

        try:
            rows = conn.execute(query, params).fetchall()
        except Exception:
            rows = []
        finally:
            conn.close()

        return [self._row_to_raw_doc(row) for row in rows]

    def _row_to_raw_doc(self, row: sqlite3.Row) -> RawDocument:
        raw_text = row["raw_text_excerpt"] or ""
        return RawDocument(
            document_id=row["document_id"],
            document_type=self._doc_type,
            source_name=row["source"],
            raw_text=raw_text,
            text_snippet=raw_text[:1000],
            source_url=row["document_url"],
            published_date=row["document_date"],
            title=f"{row['company_name']} — {row['document_type']}",
            source_metadata={
                "company_name": row["company_name"],
                "company_cik": row["company_cik"],
                "document_type": row["document_type"],
                "discovery_method": row["discovery_method"],
                "state": row["state"],
                "project_name": row["project_name"],
            },
            fetch_method=row["discovery_method"] or row["source"],
        )

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        msg = str(exc).lower()
        if "rate" in msg or "429" in msg or "quota" in msg:
            return "rate_limit"
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        if "auth" in msg or "401" in msg or "403" in msg:
            return "auth_failed"
        if "connection" in msg or "network" in msg or "dns" in msg:
            return "network"
        return "parse_error"


# ======================================================================
# News Article Cache — shared persistence for all news adapters
# ======================================================================

import hashlib as _hashlib

_NEWS_DB_PATH = "blue_h2_intelligence.db"


def _news_save(articles: list, adapter_name: str) -> int:
    """Persist raw news article dicts to the articles table. Returns rows inserted."""
    conn = sqlite3.connect(_NEWS_DB_PATH)
    saved = 0
    for art in articles:
        url = art.get("url", "")
        if not url:
            continue
        url_hash = _hashlib.md5(url.encode()).hexdigest()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO articles
                   (title, url, url_hash, source, snippet, full_text, category,
                    priority_score, published_date, fetched_date, region)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    art.get("title", "")[:500],
                    url,
                    url_hash,
                    art.get("source", adapter_name),
                    (art.get("snippet", "") or "")[:500],
                    art.get("full_text", "") or "",
                    art.get("category", ""),
                    art.get("priority_score", 0),
                    art.get("published", "") or art.get("published_date", ""),
                    datetime.now().isoformat(),
                    art.get("region", ""),
                ),
            )
            saved += conn.execute("SELECT changes()").fetchone()[0]
        except Exception:
            pass
    conn.commit()
    conn.close()
    return saved


def _news_load(adapter_name: str) -> list:
    """Load cached article dicts from the articles table for a given adapter."""
    conn = sqlite3.connect(_NEWS_DB_PATH)
    conn.row_factory = sqlite3.Row
    # Map adapter name → source column patterns
    source_map = {
        "news_rss": (
            "Ammonia Energy RSS", "EIA Today in Energy RSS", "EIA Press Releases",
            "DOE News RSS", "Hydrogen Central RSS", "FuelCellsWorks RSS",
            "Utility Dive RSS", "Power Engineering RSS", "decarbonfuse",
        ),
        "news_search": ("Google News",),
        "news_scraper": ("H2 Insight", "DecarbonFuse", "recharge", "Hydrogen Insight"),
    }
    sources = source_map.get(adapter_name, ())
    if not sources:
        # Fallback: load everything not from regulatory sources
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY fetched_date DESC LIMIT 2000"
        ).fetchall()
    else:
        placeholders = ",".join("?" * len(sources))
        rows = conn.execute(
            f"SELECT * FROM articles WHERE source IN ({placeholders})"
            f" ORDER BY fetched_date DESC LIMIT 2000",
            sources,
        ).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        d["published"] = d.get("published_date", "")  # normalise field name
        result.append(d)
    return result


# ======================================================================
# Source Registry
# ======================================================================


class SourceRegistry:
    """Registry of available source adapters.

    Usage::

        registry = SourceRegistry()
        registry.register(NewsRSSAdapter())
        registry.register(SECFilingAdapter(fid_engine))

        # Fetch from one source
        result = registry.fetch('news_rss')

        # Fetch from all sources (in priority order)
        results = registry.fetch_all()

        # Gather documents
        raw = registry.collect_raw_documents(results)
        processed = registry.collect_processed_documents(results)
    """

    def __init__(self):
        self._adapters: Dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        self._adapters[adapter.name] = adapter
        logger.info("Registered source adapter: %s (priority=%d)",
                     adapter.name, adapter.priority)

    def get(self, name: str) -> Optional[SourceAdapter]:
        return self._adapters.get(name)

    @property
    def adapter_names(self) -> List[str]:
        return list(self._adapters.keys())

    def get_targeted_adapters(self) -> List[SourceAdapter]:
        """Return adapters that support ``company_name=`` filtering."""
        return [a for a in self._adapters.values() if a.supports_targeted_fetch]

    # --- fetch ---

    def fetch(self, name: str, **kwargs) -> FetchResult:
        """Call a single adapter by name."""
        adapter = self._adapters.get(name)
        if not adapter:
            raise KeyError(f"Unknown adapter: {name}")
        return adapter.fetch(**kwargs)

    def fetch_all(self, **kwargs) -> Dict[str, FetchResult]:
        """Call all adapters sequentially in priority order."""
        ordered = sorted(self._adapters.values(), key=lambda a: a.priority)
        results: Dict[str, FetchResult] = {}
        for adapter in ordered:
            try:
                results[adapter.name] = adapter.fetch(**kwargs)
            except Exception as exc:
                logger.error("Adapter %s failed: %s", adapter.name, exc)
                results[adapter.name] = FetchResult(
                    adapter_name=adapter.name,
                    output_type=adapter.output_type,
                    error_count=1,
                    errors=[FetchError(
                        error_type="network",
                        message=str(exc)[:200],
                    )],
                )
        return results

    async def fetch_all_async(self, **kwargs) -> Dict[str, FetchResult]:
        """Call all adapters in parallel via ``asyncio.gather``."""
        names = list(self._adapters.keys())
        tasks = [self._adapters[n].fetch_async(**kwargs) for n in names]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: Dict[str, FetchResult] = {}
        for name, result in zip(names, raw_results):
            if isinstance(result, Exception):
                results[name] = FetchResult(
                    adapter_name=name,
                    output_type=self._adapters[name].output_type,
                    error_count=1,
                    errors=[FetchError(
                        error_type="network",
                        message=str(result)[:200],
                    )],
                )
            else:
                results[name] = result
        return results

    # --- collectors ---

    @staticmethod
    def collect_raw_documents(
        results: Dict[str, FetchResult],
    ) -> List[RawDocument]:
        """Gather all raw documents from multiple fetch results."""
        raw: List[RawDocument] = []
        for result in results.values():
            if result.output_type == AdapterOutputType.RAW:
                raw.extend(result.raw_documents)
        return raw

    @staticmethod
    def collect_processed_documents(
        results: Dict[str, FetchResult],
    ) -> List[ProcessedDocument]:
        """Gather all already-processed documents."""
        processed: List[ProcessedDocument] = []
        for result in results.values():
            if result.output_type == AdapterOutputType.PROCESSED:
                processed.extend(result.processed_documents)
        return processed
