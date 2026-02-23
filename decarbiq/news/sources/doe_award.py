"""
DOE Award Adapter — wraps ``FIDProbabilityEngine`` DOE methods.
"""
from __future__ import annotations

from typing import List

from nlp.schema import DocumentType
from .base import _RegulatoryEvidenceAdapter


class DOEAwardAdapter(_RegulatoryEvidenceAdapter):
    """DOE LPO / USASpending / OCED award adapter.

    Wraps:
    - ``engine.collect_doe_awards()`` — broad discovery
    - ``engine.search_doe_lpo(company)`` — targeted search
    """

    priority = 15
    rate_limit_seconds = 1.0

    @property
    def name(self) -> str:
        return "doe_award"

    @property
    def _source_filter(self) -> str:
        return "doe_lpo,doe_usaspending,doe_lpo_broad,doe_oced"

    @property
    def _doc_type(self) -> DocumentType:
        return DocumentType.DOE_AWARD

    @property
    def document_types(self) -> List[DocumentType]:
        return [DocumentType.DOE_AWARD]

    @property
    def supports_targeted_fetch(self) -> bool:
        return True

    def _run_collection(self, **kwargs):
        company = kwargs.get("company_name")
        if company:
            return self._engine.search_doe_lpo(company)
        return self._engine.collect_doe_awards()
