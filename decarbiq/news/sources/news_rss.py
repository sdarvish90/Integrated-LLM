"""
News RSS Adapter — wraps ``BlueH2NewsCollector.collect_rss()``.
"""
from __future__ import annotations

import time
from typing import List, Optional

from nlp.schema import DocumentType, ProcessedDocument, RawDocument
from .base import AdapterOutputType, FetchError, FetchResult, SourceAdapter, _news_save, _news_load


class NewsRSSAdapter(SourceAdapter):
    """Adapter for RSS feed collection.

    Delegates to ``BlueH2NewsCollector.collect_rss()`` — does NOT
    re-implement RSS fetching.
    """

    priority = 30
    rate_limit_seconds = 0.0  # feedparser handles internally

    def __init__(self, collector=None):
        self._collector = collector

    @property
    def name(self) -> str:
        return "news_rss"

    @property
    def output_type(self) -> AdapterOutputType:
        return AdapterOutputType.RAW

    @property
    def document_types(self) -> List[DocumentType]:
        return [DocumentType.NEWS]

    def _ensure_collector(self):
        if self._collector is None:
            from blue_h2_news_collector import BlueH2NewsCollector
            self._collector = BlueH2NewsCollector()

    def fetch(self, refresh: bool = True, **kwargs) -> FetchResult:
        start = time.time()
        errors: List[FetchError] = []

        if not refresh:
            articles = _news_load("news_rss")
            raw_docs = self._to_raw_docs(articles)
            elapsed = int((time.time() - start) * 1000)
            return FetchResult(
                adapter_name=self.name,
                output_type=self.output_type,
                raw_documents=raw_docs,
                fetched_count=len(raw_docs),
                fetch_time_ms=elapsed,
                fetch_params=kwargs,
                from_cache=True,
            )

        self._ensure_collector()
        try:
            articles = self._collector.collect_rss()
        except Exception as exc:
            return FetchResult(
                adapter_name=self.name,
                output_type=self.output_type,
                error_count=1,
                errors=[FetchError(error_type="network", message=str(exc)[:200])],
                fetch_params=kwargs,
            )

        _news_save(articles, "news_rss")
        raw_docs = self._to_raw_docs(articles)
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
            cache_key="news_rss:all",
            cache_ttl_seconds=900,
        )

    def _to_raw_docs(self, articles: list) -> list:
        raw_docs = []
        for art in articles:
            text = art.get("full_text") or art.get("snippet", "")
            raw_docs.append(RawDocument(
                document_id=ProcessedDocument.compute_text_hash(
                    art.get("url", "") + art.get("title", "")),
                document_type=DocumentType.NEWS,
                source_name="news_rss",
                raw_text=text,
                text_snippet=text[:1000],
                source_url=art.get("url"),
                published_date=art.get("published"),
                title=art.get("title"),
                source_metadata={
                    "source_feed": art.get("source", ""),
                    "category": art.get("category", ""),
                    "priority_score": art.get("priority_score", 0),
                    "region": art.get("region", ""),
                },
                fetch_method="rss_feed",
            ))
        return raw_docs
