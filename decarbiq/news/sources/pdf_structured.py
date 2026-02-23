"""
PDF Structured Source Adapter — wraps existing ``PDFStructuredAdapter``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import List, Optional

from nlp.schema import (
    DocumentType,
    ProcessedDocument,
)
from .base import (
    AdapterOutputType,
    FetchError,
    FetchResult,
    SourceAdapter,
)


class PDFStructuredSourceAdapter(SourceAdapter):
    """Adapter for pre-extracted PDF ``*_structured.yaml`` files.

    Delegates to :class:`nlp.pdf_structured_adapter.PDFStructuredAdapter`.
    Returns :class:`ProcessedDocument` directly — no further extraction needed.
    """

    priority = 25
    rate_limit_seconds = 0.0

    def __init__(self):
        from nlp.pdf_structured_adapter import PDFStructuredAdapter
        self._inner = PDFStructuredAdapter()

    @property
    def name(self) -> str:
        return "pdf_structured"

    @property
    def output_type(self) -> AdapterOutputType:
        return AdapterOutputType.PROCESSED

    @property
    def document_types(self) -> List[DocumentType]:
        return [
            DocumentType.PDF_STRUCTURED,
            DocumentType.REGULATION,
            DocumentType.EPA_GUIDANCE,
            DocumentType.INDUSTRY_STANDARD,
        ]

    def fetch(
        self,
        yaml_paths: Optional[List[str]] = None,
        scan_dir: Optional[str] = None,
        **kwargs,
    ) -> FetchResult:
        """Load structured YAML files.

        Args:
            yaml_paths: Explicit list of ``*_structured.yaml`` file paths.
            scan_dir: Directory to scan for ``*_structured.yaml`` files.
        """
        start = time.time()
        paths: List[str] = list(yaml_paths or [])

        if scan_dir:
            paths.extend(
                str(p) for p in Path(scan_dir).rglob("*_structured.yaml")
            )

        docs: List[ProcessedDocument] = []
        errors: List[FetchError] = []

        for path in paths:
            try:
                doc = self._inner.load_structured_yaml(path)
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
            fetch_params={"yaml_paths": yaml_paths, "scan_dir": scan_dir},
        )
