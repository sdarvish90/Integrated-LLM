"""
Source Adapters (Tier 3) — Unified interface for all data sources.
=================================================================

Every source in the decarbiq intelligence system is wrapped by an
adapter that shares a common :class:`SourceAdapter` interface.
Adapters wrap existing fetch logic — they do NOT rewrite it.

Three output types:

- **RAW** — :class:`RawDocument` objects that need ``DocumentProcessor``
- **PROCESSED** — :class:`ProcessedDocument` objects (already extracted)
- **DOMAIN_DATA** — feeds ``DomainModel`` for seed training

Usage::

    from decarbiq.news.sources import build_default_registry

    registry = build_default_registry()

    # Fetch from one source
    result = registry.fetch('news_rss')
    for doc in result.raw_documents:
        processed = processor.process(**doc.to_processor_kwargs())

    # Fetch from all sources (in priority order)
    results = registry.fetch_all()
    raw_docs = registry.collect_raw_documents(results)
    processed_docs = registry.collect_processed_documents(results)

    # Only targeted adapters (for company-specific queries)
    for adapter in registry.get_targeted_adapters():
        result = adapter.fetch(company_name="Air Products")
"""

from .base import (
    AdapterOutputType,
    FetchError,
    FetchResult,
    SourceAdapter,
    SourceRegistry,
)
from .news_rss import NewsRSSAdapter
from .news_search import NewsSearchAdapter
from .news_scraper import NewsScraperAdapter
from .sec_filing import SECFilingAdapter
from .epa_permit import EPAPermitAdapter
from .doe_award import DOEAwardAdapter
from .regulatory_filing import RegulatoryFilingAdapter
from .pdf_structured import PDFStructuredSourceAdapter
from .pdf_chunk import PDFChunkSourceAdapter
from .seed_training import SeedTrainingAdapter


def build_default_registry(
    collector=None,
    fid_engine=None,
    domain_model=None,
) -> SourceRegistry:
    """Build a registry with all default adapters.

    Args:
        collector: Optional ``BlueH2NewsCollector`` instance (shared).
        fid_engine: Optional ``FIDProbabilityEngine`` instance (shared).
        domain_model: Optional ``DomainModel`` instance (shared).

    Returns:
        Fully configured :class:`SourceRegistry` with 10 adapters.
    """
    registry = SourceRegistry()

    # News adapters (share collector)
    registry.register(NewsRSSAdapter(collector))
    registry.register(NewsSearchAdapter(collector))
    registry.register(NewsScraperAdapter(collector))

    # Regulatory evidence adapters (share FID engine)
    registry.register(SECFilingAdapter(fid_engine))
    registry.register(EPAPermitAdapter(fid_engine))
    registry.register(DOEAwardAdapter(fid_engine))
    registry.register(RegulatoryFilingAdapter(fid_engine))

    # File-based adapters
    registry.register(PDFStructuredSourceAdapter())
    registry.register(PDFChunkSourceAdapter())

    # Domain config adapter
    registry.register(SeedTrainingAdapter(domain_model))

    return registry


__all__ = [
    "AdapterOutputType",
    "FetchError",
    "FetchResult",
    "SourceAdapter",
    "SourceRegistry",
    "NewsRSSAdapter",
    "NewsSearchAdapter",
    "NewsScraperAdapter",
    "SECFilingAdapter",
    "EPAPermitAdapter",
    "DOEAwardAdapter",
    "RegulatoryFilingAdapter",
    "PDFStructuredSourceAdapter",
    "PDFChunkSourceAdapter",
    "SeedTrainingAdapter",
    "build_default_registry",
]
