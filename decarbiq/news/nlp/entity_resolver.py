"""
Entity Resolver — Orchestrator for entity and concept resolution.
==================================================================

Combines EntityRegistry (company lookup) and ConceptRegistry (stage/
financial/filing/role normalization) into a single resolve() API.

Supports:
- CIK / EPA ID / ticker-based deterministic resolution
- Name-based cascade: exact → alias → normalized → fuzzy
- Optional embedding fallback for unresolved entities
- Resolution audit log for tuning
- Entity merging for runtime corrections
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .concept_registry import ConceptMatch, ConceptRegistry
from .entity_registry import EntityRegistry, RegistryMatch
from .schema import Entity, EntityType

logger = logging.getLogger(__name__)


# ======================================================================
# Data classes
# ======================================================================


@dataclass
class ResolvedEntity:
    """Result of entity resolution."""
    canonical_name: Optional[str] = None
    canonical_key: Optional[str] = None
    resolution_method: Optional[str] = None   # "exact_canonical", "alias", "fuzzy", "cik", "epa_id", "ticker", "unresolved"
    resolution_confidence: float = 0.0
    resolved: bool = False
    flagged_for_review: bool = False
    sectors: List[str] = field(default_factory=list)
    role: Optional[str] = None
    identifiers: Dict[str, Any] = field(default_factory=dict)


# Alias for spec compatibility
CanonicalEntity = ResolvedEntity


# ======================================================================
# EntityResolver
# ======================================================================


class EntityResolver:
    """Top-level orchestrator combining entity and concept registries.

    Usage::

        resolver = EntityResolver()

        # Resolve a single entity
        result = resolver.resolve("Air Products and Chemicals, Inc.", EntityType.COMPANY)
        print(result.canonical_name)  # "Air Products"

        # Batch resolve after extraction
        entities = resolver.resolve_entities(extracted_entities)
    """

    def __init__(
        self,
        entity_registry: Optional[EntityRegistry] = None,
        concept_registry: Optional[ConceptRegistry] = None,
        enable_audit_log: bool = False,
        embedding_threshold: float = 0.75,
        domain_model: Optional[Any] = None,
    ):
        self._entity_registry = entity_registry
        self._concept_registry = concept_registry
        self._enable_audit = enable_audit_log
        self._embedding_threshold = embedding_threshold
        self._domain_model = domain_model

        self._audit_log: List[Dict] = []
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy-load registries if not provided."""
        if self._initialized:
            return

        if self._entity_registry is None:
            try:
                self._entity_registry = EntityRegistry.default()
            except Exception as e:
                logger.warning("EntityRegistry load failed: %s", e)
                self._entity_registry = EntityRegistry()

        if self._concept_registry is None:
            try:
                self._concept_registry = ConceptRegistry.default()
            except Exception as e:
                logger.warning("ConceptRegistry load failed: %s", e)
                self._concept_registry = ConceptRegistry()

        self._initialized = True

    # ==================================================================
    # Main resolution
    # ==================================================================

    def resolve(
        self,
        value: str,
        entity_type: EntityType,
        cik: Optional[str] = None,
        ticker: Optional[str] = None,
        epa_id: Optional[str] = None,
    ) -> ResolvedEntity:
        """Resolve a single entity value to its canonical form.

        Resolution cascade:
        1. CIK lookup (if provided) — 0.99
        2. EPA ID lookup (if provided) — 0.98
        3. Ticker lookup (if provided) — 0.97
        4. Name-based cascade: exact → alias → normalized → fuzzy
        5. Embedding fallback (if enabled and fuzzy fails)
        6. Unresolved — flagged for review
        """
        self._ensure_initialized()

        # Only resolve COMPANY, CONTRACTOR, LOCATION, PROJECT types
        if entity_type not in (
            EntityType.COMPANY, EntityType.CONTRACTOR,
            EntityType.LOCATION, EntityType.PROJECT,
        ):
            return ResolvedEntity(resolved=False)

        # Location entities use a different path
        if entity_type == EntityType.LOCATION:
            return self._resolve_location(value)

        # Project entities use project name normalization
        if entity_type == EntityType.PROJECT:
            return self._resolve_project(value)

        # Company / Contractor resolution
        result = self._resolve_company(value, cik=cik, ticker=ticker, epa_id=epa_id)

        if self._enable_audit:
            self._audit_log.append({
                'value': value,
                'type': entity_type.value,
                'method': result.resolution_method,
                'confidence': result.resolution_confidence,
                'canonical': result.canonical_name,
                'timestamp': datetime.now().isoformat(),
            })

        return result

    def _resolve_company(
        self,
        value: str,
        cik: Optional[str] = None,
        ticker: Optional[str] = None,
        epa_id: Optional[str] = None,
    ) -> ResolvedEntity:
        """Resolve a company/contractor name."""
        # Step 1: CIK lookup
        if cik:
            match = self._entity_registry.lookup_cik(cik)
            if match:
                return self._match_to_resolved(match)

        # Step 2: EPA ID lookup
        if epa_id:
            match = self._entity_registry.lookup_epa_id(epa_id)
            if match:
                return self._match_to_resolved(match)

        # Step 3: Ticker lookup
        if ticker:
            match = self._entity_registry.lookup_ticker(ticker)
            if match:
                return self._match_to_resolved(match)

        # Step 4: Name-based cascade (exact → alias → normalized → fuzzy)
        match = self._entity_registry.lookup(value)
        if match:
            return self._match_to_resolved(match)

        # Step 5: Embedding fallback
        embedding_result = self.resolve_with_embedding(value, EntityType.COMPANY)
        if embedding_result and embedding_result.resolved:
            return embedding_result

        # Step 6: Unresolved
        return ResolvedEntity(
            canonical_name=value,
            resolution_method='unresolved',
            resolution_confidence=0.0,
            resolved=False,
            flagged_for_review=True,
        )

    def _resolve_location(self, value: str) -> ResolvedEntity:
        """Resolve a location entity."""
        loc = self._entity_registry.normalize_location(value)
        if loc:
            canonical = loc.get('city', '')
            if canonical and loc.get('state'):
                canonical = f"{canonical}, {loc['state']}"
            elif loc.get('state'):
                canonical = loc['state']

            return ResolvedEntity(
                canonical_name=canonical,
                canonical_key=loc.get('state'),
                resolution_method='location',
                resolution_confidence=loc.get('confidence', 0.0),
                resolved=True,
            )
        return ResolvedEntity(
            canonical_name=value,
            resolution_method='unresolved',
            resolved=False,
        )

    def _resolve_project(self, value: str) -> ResolvedEntity:
        """Resolve a project entity."""
        normalized = self._entity_registry.normalize_project_name(value, fuzzy=True)
        if normalized and normalized != self._entity_registry._clean_project_name(value):
            return ResolvedEntity(
                canonical_name=normalized,
                canonical_key=normalized,
                resolution_method='fuzzy_project',
                resolution_confidence=0.80,
                resolved=True,
            )
        return ResolvedEntity(
            canonical_name=value,
            resolution_method='project_normalized',
            resolution_confidence=0.70,
            resolved=True,
        )

    @staticmethod
    def _match_to_resolved(match: RegistryMatch) -> ResolvedEntity:
        """Convert a RegistryMatch to a ResolvedEntity."""
        return ResolvedEntity(
            canonical_name=match.canonical_name,
            canonical_key=match.key,
            resolution_method=match.method,
            resolution_confidence=match.confidence,
            resolved=True,
            sectors=match.sectors,
            role=match.role,
            identifiers=match.identifiers,
        )

    # ==================================================================
    # Batch resolution
    # ==================================================================

    def resolve_entities(self, entities: List[Entity]) -> List[Entity]:
        """Resolve a batch of entities in place.

        Enriches each Entity with canonical_key, canonical_name,
        resolution_method, resolution_confidence. Also updates
        entity.normalized to the canonical_name if resolved.

        Returns the same list for convenience.
        """
        self._ensure_initialized()

        for entity in entities:
            # Extract identifiers from source metadata
            cik = entity.attributes.get('cik') or entity.attributes.get('company_cik')
            ticker = entity.attributes.get('ticker')
            epa_id = entity.attributes.get('epa_id') or entity.attributes.get('facility_id')

            resolved = self.resolve(
                entity.value, entity.type,
                cik=cik, ticker=ticker, epa_id=epa_id,
            )

            entity.canonical_key = resolved.canonical_key
            entity.canonical_name = resolved.canonical_name
            entity.resolution_method = resolved.resolution_method
            entity.resolution_confidence = resolved.resolution_confidence

            if resolved.resolved and resolved.canonical_name:
                entity.normalized = resolved.canonical_name

        return entities

    # ==================================================================
    # Concept delegates
    # ==================================================================

    def resolve_stage(self, text: str) -> Optional[ConceptMatch]:
        """Normalize free text to a project stage."""
        self._ensure_initialized()
        return self._concept_registry.normalize_stage(text)

    def resolve_financial(self, term: str) -> Optional[ConceptMatch]:
        """Normalize a financial term."""
        self._ensure_initialized()
        return self._concept_registry.normalize_financial(term)

    def classify_filing(self, text: str) -> Optional[ConceptMatch]:
        """Classify a filing reference."""
        self._ensure_initialized()
        return self._concept_registry.classify_filing(text)

    def resolve_role(self, text: str) -> Optional[ConceptMatch]:
        """Normalize an entity role."""
        self._ensure_initialized()
        return self._concept_registry.normalize_role(text)

    # ==================================================================
    # Entity merging
    # ==================================================================

    def merge_entities(self, keep_key: str, merge_key: str):
        """Merge two entities discovered to be the same.

        All aliases from merge_key are added to keep_key.
        merge_key is removed from the registry.
        """
        self._ensure_initialized()
        registry = self._entity_registry

        keep_data = registry._companies.get(keep_key)
        merge_data = registry._companies.get(merge_key)

        if not keep_data or not merge_data:
            logger.warning("Cannot merge: %s or %s not found", keep_key, merge_key)
            return

        # Transfer aliases
        keep_aliases = keep_data.get('aliases', [])
        merge_aliases = merge_data.get('aliases', [])
        merge_canonical = merge_data.get('canonical_name', '')

        new_aliases = list(set(keep_aliases + merge_aliases))
        if merge_canonical and merge_canonical not in new_aliases:
            new_aliases.append(merge_canonical)

        keep_data['aliases'] = new_aliases

        # Transfer sectors
        keep_sectors = keep_data.get('sectors', [])
        merge_sectors = merge_data.get('sectors', [])
        keep_data['sectors'] = list(set(keep_sectors + merge_sectors))

        # Remove merged entry
        del registry._companies[merge_key]

        # Rebuild indexes
        registry._build_indexes()

        logger.info("Merged %s into %s", merge_key, keep_key)

    # ==================================================================
    # Embedding fallback
    # ==================================================================

    def resolve_with_embedding(
        self, value: str, entity_type: EntityType,
    ) -> Optional[ResolvedEntity]:
        """Last-resort resolution using embedding similarity.

        Only works if DomainModel is available with _encode() method.
        Computes cosine similarity against pre-computed canonical name embeddings.
        """
        if not self._domain_model:
            return None

        try:
            import numpy as np

            # Encode the query value
            query_vec = self._domain_model._encode(value)

            best_sim = 0.0
            best_key = None

            for key, data in self._entity_registry._companies.items():
                canonical = data.get('canonical_name', '')
                if not canonical:
                    continue
                canon_vec = self._domain_model._encode(canonical)

                # Cosine similarity
                dot = np.dot(query_vec, canon_vec)
                norm = np.linalg.norm(query_vec) * np.linalg.norm(canon_vec)
                if norm > 0:
                    sim = float(dot / norm)
                    if sim > best_sim:
                        best_sim = sim
                        best_key = key

            if best_key and best_sim >= self._embedding_threshold:
                match = self._entity_registry._build_match(
                    best_key, 'embedding', best_sim * 0.90,
                )
                return self._match_to_resolved(match)

        except Exception as e:
            logger.debug("Embedding fallback unavailable: %s", e)

        return None

    # ==================================================================
    # Audit log
    # ==================================================================

    def get_resolution_stats(self) -> Dict[str, Any]:
        """Stats on resolution methods used — useful for tuning."""
        if not self._audit_log:
            return {'total': 0, 'by_method': {}, 'avg_confidence': 0.0, 'top_unresolved': []}

        by_method: Dict[str, int] = {}
        total_conf = 0.0
        unresolved: List[str] = []

        for entry in self._audit_log:
            method = entry.get('method', 'unknown')
            by_method[method] = by_method.get(method, 0) + 1
            total_conf += entry.get('confidence', 0.0)
            if method == 'unresolved':
                unresolved.append(entry.get('value', ''))

        # Count unique unresolved, sorted by frequency
        unresolved_counts: Dict[str, int] = {}
        for v in unresolved:
            unresolved_counts[v] = unresolved_counts.get(v, 0) + 1
        top_unresolved = sorted(
            unresolved_counts.items(), key=lambda x: x[1], reverse=True,
        )[:20]

        return {
            'total': len(self._audit_log),
            'by_method': by_method,
            'avg_confidence': total_conf / len(self._audit_log),
            'top_unresolved': [{'value': v, 'count': c} for v, c in top_unresolved],
        }

    @property
    def unresolved_entities(self) -> List[Dict]:
        """Entities that could not be resolved — candidates for entities.yaml additions."""
        seen = set()
        result = []
        for entry in self._audit_log:
            if entry.get('method') == 'unresolved':
                val = entry.get('value', '')
                if val and val not in seen:
                    seen.add(val)
                    result.append(entry)
        return result
