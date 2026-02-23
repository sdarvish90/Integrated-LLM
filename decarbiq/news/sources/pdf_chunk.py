"""
PDF Chunk Source Adapter — wraps existing ``PDFStructuredAdapter.load_chunk()``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from nlp.schema import DocumentType, ProcessedDocument
from .base import (
    AdapterOutputType,
    FetchError,
    FetchResult,
    SourceAdapter,
)


class PDFChunkSourceAdapter(SourceAdapter):
    """Adapter for PDF chunk markdown files (``*_NNN_*.md``).

    Delegates to :class:`nlp.pdf_structured_adapter.PDFStructuredAdapter`.
    Returns :class:`ProcessedDocument` with ``needs_extraction`` flag
    for chunks that may benefit from further LLM processing.
    """

    priority = 25
    rate_limit_seconds = 0.0

    def __init__(self):
        from nlp.pdf_structured_adapter import PDFStructuredAdapter
        self._inner = PDFStructuredAdapter()

    @property
    def name(self) -> str:
        return "pdf_chunk"

    @property
    def output_type(self) -> AdapterOutputType:
        return AdapterOutputType.PROCESSED

    @property
    def document_types(self) -> List[DocumentType]:
        return [DocumentType.PDF_CHUNK]

    def fetch(
        self,
        chunk_paths: Optional[List[str]] = None,
        scan_dir: Optional[str] = None,
        **kwargs,
    ) -> FetchResult:
        """Load chunk markdown files.

        Args:
            chunk_paths: Explicit list of chunk file paths.
            scan_dir: Directory to scan for ``*_NNN_*.md`` chunk files.
        """
        start = time.time()
        paths: List[str] = list(chunk_paths or [])

        if scan_dir:
            paths.extend(
                str(p) for p in Path(scan_dir).rglob("*_[0-9][0-9][0-9]_*.md")
            )

        docs: List[ProcessedDocument] = []
        errors: List[FetchError] = []

        for path in paths:
            try:
                doc = self._inner.load_chunk(path)
                docs.append(doc)
            except Exception as exc:
                errors.append(FetchError(
                    error_type="parse_error",
                    message=f"{path}: {exc}"[:200],
                    retryable=False,
                ))

        elapsed = int((time.time() - start) * 1000)
        return FetchResult(
            adapter_name=self.name,
            output_type=self.output_type,
            processed_documents=docs,
            fetched_count=len(docs),
            error_count=len(errors),
            errors=errors,
            fetch_time_ms=elapsed,
            fetch_params={"chunk_paths": chunk_paths, "scan_dir": scan_dir},
        )
