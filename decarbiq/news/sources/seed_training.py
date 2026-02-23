"""
Seed Training Adapter — wraps ``DomainModel.load_all()``.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from nlp.schema import DocumentType
from .base import AdapterOutputType, FetchError, FetchResult, SourceAdapter


class SeedTrainingAdapter(SourceAdapter):
    """Adapter for seed training domain configuration.

    Delegates to :meth:`DomainModel.load_all` for auto-discovery of:

    - ``core/concepts.yaml``, ``core/entities.yaml``
    - ``domain.yaml`` files (per-domain seed examples)
    - ``shared.yaml`` files (cross-domain shared examples)
    - ``*_seeds.yaml`` files (document-extracted seeds)
    - ``cross_domain/connections.yaml``
    - ``manifest.yaml`` files (with cross-domain triggers)

    Returns ``DOMAIN_DATA`` output type — feeds the domain model,
    not the document processor.
    """

    priority = 50
    rate_limit_seconds = 0.0

    def __init__(self, domain_model=None):
        self._model = domain_model

    @property
    def name(self) -> str:
        return "seed_training"

    @property
    def output_type(self) -> AdapterOutputType:
        return AdapterOutputType.DOMAIN_DATA

    def _ensure_model(self):
        if self._model is None:
            from nlp.domain_model import DomainModel
            self._model = DomainModel()

    def fetch(self, seed_dir: Optional[str] = None, **kwargs) -> FetchResult:
        """Load all seed training data.

        Args:
            seed_dir: Override seed training directory path.
                Defaults to ``<project>/seed training/domains``.
        """
        start = time.time()
        self._ensure_model()
        errors: List[FetchError] = []

        try:
            seed_path = Path(seed_dir) if seed_dir else None
            stats: Dict[str, int] = self._model.load_all(seed_dir=seed_path)
        except Exception as exc:
            return FetchResult(
                adapter_name=self.name,
                output_type=self.output_type,
                error_count=1,
                errors=[FetchError(
                    error_type="parse_error",
                    message=str(exc)[:200],
                    retryable=False,
                )],
                fetch_params={"seed_dir": seed_dir},
            )

        elapsed = int((time.time() - start) * 1000)
        return FetchResult(
            adapter_name=self.name,
            output_type=self.output_type,
            domain_data={
                "domain_stats": stats,
                "domain_names": self._model.domain_names,
                "core_entities": self._model.core_entities,
                "cross_domain_triggers": self._model._cross_domain_triggers,
            },
            fetched_count=sum(stats.values()),
            fetch_time_ms=elapsed,
            fetch_params={"seed_dir": seed_dir},
        )

    @property
    def model(self):
        """Access the underlying DomainModel (after fetch)."""
        return self._model
