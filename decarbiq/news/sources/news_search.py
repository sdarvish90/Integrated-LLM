"""
News Search Adapter — wraps ``BlueH2NewsCollector.collect_search()``.
"""
from __future__ import annotations

import time
from typing import List, Optional

from nlp.schema import DocumentType, ProcessedDocument, RawDocument
from .base import AdapterOutputType, FetchError, FetchResult, SourceAdapter, _news_save, _news_load


class NewsSearchAdapter(SourceAdapter):
    """Adapter for web search collection (DuckDuckGo + Google News RSS).

    Delegates to ``BlueH2NewsCollector.collect_search()``.
    """

    priority = 40
    rate_limit_seconds = 0.0

    def __init__(self, collector=None):
        self._collector = collector

    @property
    def name(self) -> str:
        return "news_search"

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

    def fetch(self, queries: Optional[List[str]] = None, refresh: bool = True, **kwargs) -> FetchResult:
        start = time.time()

        if not refresh:
            articles = _news_load("news_search")
            raw_docs = self._to_raw_docs(articles)
            elapsed = int((time.time() - start) * 1000)
            return FetchResult(
                adapter_name=self.name,
                output_type=self.output_type,
                raw_documents=raw_docs,
                fetched_count=len(raw_docs),
                fetch_time_ms=elapsed,
                fetch_params={**kwargs, "queries": queries},
                from_cache=True,
            )

        self._ensure_collector()
        try:
            articles = self._collector.collect_search(queries=queries)
        except Exception as exc:
            return FetchResult(
                adapter_name=self.name,
                output_type=self.output_type,
                error_count=1,
                errors=[FetchError(error_type="network", message=str(exc)[:200])],
                fetch_params=kwargs,
            )

        _news_save(articles, "news_search")
        raw_docs = self._to_raw_docs(articles)
        elapsed = int((time.time() - start) * 1000)
        return FetchResult(
            adapter_name=self.name,
            output_type=self.output_type,
            raw_documents=raw_docs,
            fetched_count=len(raw_docs),
            fetch_time_ms=elapsed,
            fetch_params={**kwargs, "queries": queries},
            cache_key="news_search:all",
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
                source_name="news_search",
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
                fetch_method="web_search",
            ))
        return raw_docs
