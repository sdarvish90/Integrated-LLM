"""
SEC Filing Adapter — wraps ``FIDProbabilityEngine`` SEC methods.
"""
from __future__ import annotations

from typing import List

from nlp.schema import DocumentType
from .base import _RegulatoryEvidenceAdapter


class SECFilingAdapter(_RegulatoryEvidenceAdapter):
    """SEC EDGAR filing adapter.

    Wraps:
    - ``engine.collect_filings()`` — broad EFTS 6-strategy discovery
    - ``engine.search_sec_filings(company)`` — company-specific CIK lookup
    """

    priority = 10
    rate_limit_seconds = 0.12   # SEC requires <= 10 req/s

    @property
    def name(self) -> str:
        return "sec_filing"

    @property
    def _source_filter(self) -> str:
        return "sec_edgar,sec_edgar_efts"

    @property
    def _doc_type(self) -> DocumentType:
        return DocumentType.SEC_FILING

    @property
    def document_types(self) -> List[DocumentType]:
        return [DocumentType.SEC_FILING]

    @property
    def supports_targeted_fetch(self) -> bool:
        return True

    def _run_collection(self, **kwargs):
        company = kwargs.get("company_name")
        if company:
            return self._engine.search_sec_filings(
                company, cik=kwargs.get("cik"))
        return self._engine.collect_filings()
