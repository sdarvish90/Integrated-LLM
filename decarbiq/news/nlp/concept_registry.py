"""
Concept Registry — Loads concepts.yaml, normalizes domain concepts.
====================================================================

Normalizes project stages, financial terms, filing types, and entity
roles to canonical forms using the seed training concept catalog.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONCEPTS = (
    _HERE.parent.parent.parent / 'seed training' / 'domains' / 'core' / 'concepts.yaml'
)


# ======================================================================
# Data classes
# ======================================================================


@dataclass
class ConceptMatch:
    """Result of a concept normalization."""
    canonical: str                       # "construction", "capex", "10-K"
    category: str                        # "project_stage", "financial", "filing_sec", "role"
    confidence: float = 0.0
    description: Optional[str] = None
    keywords_matched: List[str] = field(default_factory=list)


# ======================================================================
# ConceptRegistry
# ======================================================================


class ConceptRegistry:
    """In-memory concept catalog loaded from concepts.yaml.

    Provides normalization for four concept categories:
    - Project stages (announced → ... → operational → cancelled)
    - Financial terms (capex, opex, lcoh, etc.)
    - Filing types (8-K, 10-K, Title V, Certificate, etc.)
    - Entity roles (developer, epc_contractor, offtaker, etc.)
    """

    def __init__(self, yaml_path: Optional[str] = None):
        # Stage data: ordered list of (stage_name, description, keywords)
        self._stages: List[Dict[str, Any]] = []

        # Financial terms: term → description
        self._financial_terms: Dict[str, str] = {}
        self._funding_sources: List[str] = []

        # Filing types: filing_ref → {canonical, category}
        self._filings: Dict[str, Dict[str, str]] = {}

        # Entity roles: role_key → description
        self._roles: Dict[str, str] = {}

        if yaml_path:
            self._load_yaml(yaml_path)

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def default(cls) -> ConceptRegistry:
        """Load from the standard seed training concepts.yaml."""
        return cls(yaml_path=str(_DEFAULT_CONCEPTS))

    @classmethod
    def from_yaml(cls, path: str) -> ConceptRegistry:
        """Load from an explicit YAML path."""
        return cls(yaml_path=path)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ConceptRegistry:
        """Load from a pre-loaded dict."""
        reg = cls()
        reg._parse_data(data)
        return reg

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_yaml(self, path: str):
        """Load and parse concepts YAML file."""
        p = Path(path)
        if not p.exists():
            logger.warning("Concepts YAML not found: %s", path)
            return

        with open(p, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        if data:
            self._parse_data(data)

    def _parse_data(self, data: Dict[str, Any]):
        """Parse the concepts data dict."""
        concepts = data.get('concepts', {})

        # Project stages
        stages_data = concepts.get('project_stages', {})
        stages_dict = stages_data.get('stages', {})
        # Preserve lifecycle order
        stage_order = [
            'announced', 'pre_feed', 'feed', 'fid', 'epc_award',
            'construction', 'commissioning', 'operational', 'cancelled',
        ]
        for stage_name in stage_order:
            stage_info = stages_dict.get(stage_name, {})
            if stage_info:
                self._stages.append({
                    'name': stage_name,
                    'description': stage_info.get('description', ''),
                    'keywords': [kw.lower() for kw in stage_info.get('keywords', [])],
                })

        # Financial terms
        financial_data = concepts.get('financial', {})
        self._financial_terms = financial_data.get('terms', {})
        self._funding_sources = financial_data.get('funding_sources', [])

        # Filing types
        regulatory_data = concepts.get('regulatory', {})
        filing_types = regulatory_data.get('filing_types', {})

        for category, filings in filing_types.items():
            if isinstance(filings, list):
                for filing_str in filings:
                    # Parse "8-K: Material events" format
                    parts = filing_str.split(':', 1)
                    ref = parts[0].strip()
                    desc = parts[1].strip() if len(parts) > 1 else ''
                    self._filings[ref.lower()] = {
                        'canonical': ref,
                        'category': f'filing_{category}',
                        'description': desc,
                    }

        # Entity roles
        entities_data = concepts.get('entities', {})
        self._roles = entities_data.get('roles', {})

        logger.info(
            "ConceptRegistry loaded: %d stages, %d financial terms, %d filings, %d roles",
            len(self._stages), len(self._financial_terms),
            len(self._filings), len(self._roles),
        )

    # ------------------------------------------------------------------
    # Stage normalization
    # ------------------------------------------------------------------

    def normalize_stage(self, text: str) -> Optional[ConceptMatch]:
        """Normalize free text to a project stage.

        Iterates stages in lifecycle order. Later matches override earlier
        ones — if text has both "announced" and "broke ground", returns
        "construction".
        """
        if not text or not text.strip():
            return None

        text_lower = text.strip().lower()
        best_match: Optional[ConceptMatch] = None

        for stage in self._stages:
            matched_keywords = []
            for kw in stage['keywords']:
                if kw in text_lower:
                    matched_keywords.append(kw)

            if matched_keywords:
                best_match = ConceptMatch(
                    canonical=stage['name'],
                    category='project_stage',
                    confidence=0.85,
                    description=stage['description'],
                    keywords_matched=matched_keywords,
                )

        return best_match

    # ------------------------------------------------------------------
    # Financial term normalization
    # ------------------------------------------------------------------

    def normalize_financial(self, term: str) -> Optional[ConceptMatch]:
        """Normalize a financial term to its canonical form."""
        if not term or not term.strip():
            return None

        term_lower = term.strip().lower()

        # Direct match
        for key, description in self._financial_terms.items():
            if term_lower == key.lower():
                return ConceptMatch(
                    canonical=key,
                    category='financial',
                    confidence=0.95,
                    description=description,
                )

        # Partial match — check if term appears in description
        for key, description in self._financial_terms.items():
            if term_lower in description.lower() or key.lower() in term_lower:
                return ConceptMatch(
                    canonical=key,
                    category='financial',
                    confidence=0.80,
                    description=description,
                    keywords_matched=[key],
                )

        return None

    # ------------------------------------------------------------------
    # Filing classification
    # ------------------------------------------------------------------

    def classify_filing(self, text: str) -> Optional[ConceptMatch]:
        """Classify a filing reference to its canonical form."""
        if not text or not text.strip():
            return None

        text_clean = text.strip()

        # Direct match
        filing = self._filings.get(text_clean.lower())
        if filing:
            return ConceptMatch(
                canonical=filing['canonical'],
                category=filing['category'],
                confidence=0.95,
                description=filing.get('description', ''),
            )

        # Partial match — check if filing ref appears in text
        text_lower = text_clean.lower()
        for ref, filing in self._filings.items():
            if ref in text_lower:
                return ConceptMatch(
                    canonical=filing['canonical'],
                    category=filing['category'],
                    confidence=0.85,
                    description=filing.get('description', ''),
                    keywords_matched=[ref],
                )

        return None

    # ------------------------------------------------------------------
    # Role normalization
    # ------------------------------------------------------------------

    def normalize_role(self, text: str) -> Optional[ConceptMatch]:
        """Normalize an entity role to its canonical form."""
        if not text or not text.strip():
            return None

        text_lower = text.strip().lower()
        # Normalize common variations
        text_normalized = re.sub(r'[\s_-]+', '_', text_lower)

        for role_key, description in self._roles.items():
            if text_normalized == role_key or text_lower == role_key:
                return ConceptMatch(
                    canonical=role_key,
                    category='role',
                    confidence=0.95,
                    description=description,
                )

        # Partial match
        for role_key, description in self._roles.items():
            if role_key in text_normalized or text_normalized in role_key:
                return ConceptMatch(
                    canonical=role_key,
                    category='role',
                    confidence=0.80,
                    description=description,
                    keywords_matched=[role_key],
                )

        return None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def stage_names(self) -> List[str]:
        """List of stage names in lifecycle order."""
        return [s['name'] for s in self._stages]

    @property
    def financial_term_names(self) -> List[str]:
        """List of financial term names."""
        return list(self._financial_terms.keys())

    @property
    def filing_refs(self) -> List[str]:
        """List of known filing references."""
        return [f['canonical'] for f in self._filings.values()]

    @property
    def role_names(self) -> List[str]:
        """List of known role names."""
        return list(self._roles.keys())
