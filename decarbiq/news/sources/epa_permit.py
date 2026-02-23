"""
EPA Permit Adapter — wraps ``FIDProbabilityEngine`` EPA methods.
"""
from __future__ import annotations

from typing import List

from nlp.schema import DocumentType
from .base import _RegulatoryEvidenceAdapter


class EPAPermitAdapter(_RegulatoryEvidenceAdapter):
    """EPA ECHO facility and permit adapter.

    Wraps:
    - ``engine.collect_permits()`` — broad SIC-based discovery
    - ``engine.search_epa_permits(company, state)`` — targeted search
    """

    priority = 15
    rate_limit_seconds = 0.5

    @property
    def name(self) -> str:
        return "epa_permit"

    @property
    def _source_filter(self) -> str:
        return "epa_echo,epa_echo_broad"

    @property
    def _doc_type(self) -> DocumentType:
        return DocumentType.EPA_PERMIT

    @property
    def document_types(self) -> List[DocumentType]:
        return [DocumentType.EPA_PERMIT]

    @property
    def supports_targeted_fetch(self) -> bool:
        return True

    def _run_collection(self, **kwargs):
        company = kwargs.get("company_name")
        if company:
            return self._engine.search_epa_permits(
                company, kwargs.get("state", ""))
        return self._engine.collect_permits()
