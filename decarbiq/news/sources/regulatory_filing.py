"""
Regulatory Filing Adapter — wraps ``FIDProbabilityEngine`` regulations.gov methods.
"""
from __future__ import annotations

from typing import List

from nlp.schema import DocumentType
from .base import _RegulatoryEvidenceAdapter


class RegulatoryFilingAdapter(_RegulatoryEvidenceAdapter):
    """Regulations.gov filing adapter.

    Wraps ``engine.collect_regulatory_filings()`` for broad discovery.
    Does not support targeted company search.
    """

    priority = 20
    rate_limit_seconds = 1.0

    @property
    def name(self) -> str:
        return "regulatory_filing"

    @property
    def _source_filter(self) -> str:
        # FID engine uses source LIKE 'regsgov_%' — match those variants
        return "regsgov_comment,regsgov_notice,regsgov_rule,regsgov_proposed"

    @property
    def _doc_type(self) -> DocumentType:
        return DocumentType.REGULATORY_FILING

    @property
    def document_types(self) -> List[DocumentType]:
        return [DocumentType.REGULATORY_FILING]

    def _run_collection(self, **kwargs):
        return self._engine.collect_regulatory_filings()
