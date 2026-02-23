"""
Entity Extractor — Multi-method entity extraction pipeline.
============================================================

Zero-shot NER (GLiNER) + regex pattern matching for domain-specific
entity extraction.  Returns :class:`Entity` objects from the unified schema.

Supports:
- Raw text extraction  → ``List[Entity]``
- PDF chunk files      → ``(List[Entity], ChunkMetadata)``
- Seed YAML files      → ``List[Entity]``

Legacy API (``ArticleExtraction``, ``get_extractor()``) is preserved
at the bottom of this module for backward compatibility with
``blue_h2_news_collector.py``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from .schema import ChunkMetadata, Entity, EntityType

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_CONFIG_DIR = _HERE.parent / 'config' / 'domains'

# GLiNER chunk size: process multiple chunks for longer documents
_GLINER_CHUNK_SIZE = 4500
_GLINER_MAX_CHUNKS = 4  # up to 18K chars searched for entities


# ======================================================================
# New EntityExtractor — primary API used by DocumentProcessor
# ======================================================================


class EntityExtractor:
    """Multi-method entity extraction using GLiNER + regex patterns.

    Usage::

        extractor = EntityExtractor()

        # From raw text
        entities = extractor.extract("Air Products announced...")

        # From a PDF chunk file
        entities, meta = extractor.extract_from_chunk("doc_001_section.md")

        # From a seeds YAML
        entities = extractor.extract_from_seeds_yaml("doc_seeds.yaml")
    """

    # GLiNER label → EntityType mapping
    TYPE_MAPPING: Dict[str, EntityType] = {
        'company': EntityType.COMPANY,
        'organization': EntityType.COMPANY,
        'project': EntityType.PROJECT,
        'project_name': EntityType.PROJECT,
        'location': EntityType.LOCATION,
        'person': EntityType.PERSON,
        'technology': EntityType.TECHNOLOGY,
        'product': EntityType.PRODUCT,
        'contractor': EntityType.CONTRACTOR,
        'epc_contractor': EntityType.CONTRACTOR,
        'agency': EntityType.AGENCY,
        'facility': EntityType.FACILITY,
        'standard': EntityType.STANDARD,
        'regulation': EntityType.REGULATION,
        'equipment': EntityType.EQUIPMENT,
        'capacity': EntityType.FINANCIAL,
        'money': EntityType.FINANCIAL,
        'date': EntityType.PROJECT,
        'status': EntityType.PROJECT,
    }

    # Regex patterns for domain-specific entities
    PATTERNS: Dict[str, List[Tuple[str, EntityType]]] = {
        'standards': [
            (r'\b(ANSI[/\s]CGA\s+[A-Z]-[\d.]+-\d{4})\b', EntityType.STANDARD),
            (r'\b(NFPA\s+\d+[A-Z]?)\b', EntityType.STANDARD),
            (r'\b(ASME\s+[A-Z]+[\d.]*)\b', EntityType.STANDARD),
            (r'\b(API\s+\d+[A-Z]?)\b', EntityType.STANDARD),
            (r'\b(ISO\s+\d+(?:[-:]\d+)?)\b', EntityType.STANDARD),
            (r'\b(49\s*C\.?F\.?R\.?\s*(?:Part\s*)?\d+)\b', EntityType.REGULATION),
            (r'\b(40\s*C\.?F\.?R\.?\s*(?:Part\s*)?\d+)\b', EntityType.REGULATION),
            (r'\b(29\s*C\.?F\.?R\.?\s*(?:Part\s*)?\d+)\b', EntityType.REGULATION),
        ],
        'equipment': [
            (r'\b((?:pressure relief|safety relief|rupture disc)\s*(?:valve|device)s?)\b',
             EntityType.EQUIPMENT),
            (r'\b((?:storage|pressure|cryogenic)\s*(?:tank|vessel|container)s?)\b',
             EntityType.EQUIPMENT),
            (r'\b((?:transfer|loading|unloading)\s*(?:hose|line|arm)s?)\b',
             EntityType.EQUIPMENT),
            (r'\b(compressor|heat exchanger|reformer|electrolyzer|vaporizer)s?\b',
             EntityType.EQUIPMENT),
        ],
        'technology': [
            (r'\b(SMR|ATR|POx|steam methane reform(?:ing|er))\b',
             EntityType.TECHNOLOGY),
            (r'\b((?:carbon|CO2)\s*capture(?:\s+and\s+storage)?)\b',
             EntityType.TECHNOLOGY),
            (r'\b(CCS|CCUS)\b', EntityType.TECHNOLOGY),
            (r'\b((?:PEM|alkaline|SOEC)\s*electrolysis)\b', EntityType.TECHNOLOGY),
            (r'\b(blue hydrogen|green hydrogen|grey hydrogen)\b',
             EntityType.TECHNOLOGY),
        ],
    }

    def __init__(self, use_gliner: bool = True):
        """Initialize entity extractor.

        Args:
            use_gliner: Whether to use GLiNER model (lazy-loaded on first use).
        """
        self._use_gliner = use_gliner
        self._gliner_model = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str) -> List[Entity]:
        """Extract entities from text using GLiNER + regex.

        Args:
            text: Document text to extract entities from.

        Returns:
            Deduplicated list of :class:`Entity` objects.
        """
        entities: List[Entity] = []
        seen: set = set()  # (type, value_lower) for dedup

        # GLiNER extraction
        if self._use_gliner:
            for ent in self._extract_gliner(text):
                key = (ent.type, ent.value.lower())
                if key not in seen:
                    seen.add(key)
                    entities.append(ent)

        # Regex extraction
        for ent in self._extract_regex(text):
            key = (ent.type, ent.value.lower())
            if key not in seen:
                seen.add(key)
                entities.append(ent)

        return entities

    def extract_to_dict(self, text: str) -> List[Dict]:
        """Extract entities and return as dicts."""
        return [e.to_dict() for e in self.extract(text)]

    def extract_from_chunk(
        self, chunk_path: str,
    ) -> Tuple[List[Entity], Optional[ChunkMetadata]]:
        """Extract entities from a PDF chunk file (``*_NNN_section.md``).

        Args:
            chunk_path: Path to a chunk markdown file.

        Returns:
            Tuple of ``(entities, chunk_metadata)``.
        """
        path = Path(chunk_path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        metadata = self._parse_chunk_metadata(content)
        text = self._extract_content_section(content)

        entities = self.extract(text)
        return entities, metadata

    def extract_from_seeds_yaml(self, seeds_path: str) -> List[Entity]:
        """Extract entities from a ``*_seeds.yaml`` file.

        Args:
            seeds_path: Path to seeds YAML file.

        Returns:
            Deduplicated list of entities found across all positive examples.
        """
        path = Path(seeds_path)
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        entities: List[Entity] = []
        seen: set = set()

        for example in data.get('positive_examples', []):
            text = example.get('text', '')
            for ent in self.extract(text):
                key = (ent.type, ent.value.lower())
                if key not in seen:
                    seen.add(key)
                    entities.append(ent)

        return entities

    # ------------------------------------------------------------------
    # GLiNER extraction
    # ------------------------------------------------------------------

    def _load_gliner(self):
        """Lazy-load GLiNER model on first use."""
        if self._gliner_model is not None:
            return
        try:
            from gliner import GLiNER
            logger.info("Loading GLiNER model...")
            self._gliner_model = GLiNER.from_pretrained(
                "urchade/gliner_medium-v2.1"
            )
            logger.info("GLiNER model loaded")
        except Exception as e:
            logger.warning("GLiNER not available: %s", e)
            self._gliner_model = False  # sentinel: tried but failed

    def _extract_gliner(self, text: str) -> List[Entity]:
        """Run GLiNER zero-shot NER on text (chunked processing)."""
        self._load_gliner()
        if self._gliner_model is False:
            return []

        labels = [
            'company', 'project', 'location', 'person',
            'technology', 'product', 'contractor', 'agency',
            'facility', 'standard', 'equipment',
        ]

        entities: List[Entity] = []
        seen: set = set()

        try:
            for chunk_idx in range(_GLINER_MAX_CHUNKS):
                start = chunk_idx * _GLINER_CHUNK_SIZE
                chunk = text[start:start + _GLINER_CHUNK_SIZE]
                if not chunk.strip():
                    break

                raw = self._gliner_model.predict_entities(
                    chunk, labels, threshold=0.3,
                )
                for e in raw:
                    val = e['text'].strip()
                    label = e['label']
                    key = (val.lower(), label)
                    if key not in seen:
                        seen.add(key)
                        entity_type = self._infer_entity_type(val, label)
                        entities.append(Entity(
                            type=entity_type,
                            value=val,
                            normalized=(
                                self._normalize_company(val)
                                if entity_type == EntityType.COMPANY
                                else None
                            ),
                            confidence=float(e['score']),
                            evidence=self._get_evidence_span(text, val),
                            attributes={'gliner_label': label},
                        ))
        except Exception as e:
            logger.warning("GLiNER extraction error: %s", e)

        return entities

    # ------------------------------------------------------------------
    # Regex extraction
    # ------------------------------------------------------------------

    def _extract_regex(self, text: str) -> List[Entity]:
        """Extract entities using regex patterns."""
        entities: List[Entity] = []

        for category, patterns in self.PATTERNS.items():
            for pattern, entity_type in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    val = (match.group(1) if match.lastindex
                           else match.group(0))
                    entities.append(Entity(
                        type=entity_type,
                        value=val.strip(),
                        confidence=0.85,
                        evidence=self._get_evidence_span(text, val),
                        attributes={
                            'extraction_method': 'regex',
                            'pattern_category': category,
                        },
                    ))

        return entities

    # ------------------------------------------------------------------
    # Chunk helpers
    # ------------------------------------------------------------------

    def _parse_chunk_metadata(self, content: str) -> Optional[ChunkMetadata]:
        """Parse ``## chunk_metadata`` section from a chunk markdown file."""
        meta_match = re.search(
            r'## chunk_metadata\s*\n(.*?)(?=\n## |\Z)',
            content, re.DOTALL,
        )
        if not meta_match:
            return None

        meta_text = meta_match.group(1)
        chunk_id = self._extract_field(meta_text, 'chunk_id')
        section = self._extract_field(meta_text, 'section')
        token_est = self._extract_field(meta_text, 'token_estimate')

        return ChunkMetadata(
            chunk_id=chunk_id,
            section=section,
            token_estimate=int(token_est) if token_est.isdigit() else 0,
            key_entities=self._extract_list_field(meta_text, 'key_entities'),
            key_concepts=self._extract_list_field(meta_text, 'key_concepts'),
        )

    def _extract_content_section(self, content: str) -> str:
        """Extract ``## content`` section from chunk markdown."""
        content_match = re.search(
            r'## content\s*\n(.*?)(?=\n## |\Z)',
            content, re.DOTALL,
        )
        if content_match:
            return content_match.group(1).strip()

        # Fallback: everything after chunk_metadata
        meta_end = re.search(
            r'## chunk_metadata.*?(?=\n## )', content, re.DOTALL,
        )
        if meta_end:
            return content[meta_end.end():].strip()

        return content.strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _infer_entity_type(self, text: str, label: str) -> EntityType:
        """Infer EntityType from GLiNER label, with fallback heuristics."""
        mapped = self.TYPE_MAPPING.get(label.lower())
        if mapped:
            return mapped

        # Heuristic fallbacks
        text_lower = text.lower()
        if any(kw in text_lower for kw in ['inc', 'corp', 'llc', 'ltd', 'plc']):
            return EntityType.COMPANY
        if any(kw in text_lower for kw in ['epa', 'doe', 'ferc', 'sec', 'osha']):
            return EntityType.AGENCY

        return EntityType.COMPANY  # safe default

    def _get_evidence_span(
        self, text: str, entity_text: str, window: int = 100,
    ) -> str:
        """Get surrounding text as evidence for an extracted entity."""
        idx = text.find(entity_text)
        if idx < 0:
            return entity_text
        start = max(0, idx - window)
        end = min(len(text), idx + len(entity_text) + window)
        return text[start:end].strip()

    @staticmethod
    def _normalize_company(name: str) -> str:
        """Normalize company name by removing common suffixes."""
        suffixes = [
            r',?\s*Inc\.?', r',?\s*LLC', r',?\s*Ltd\.?', r',?\s*Corp\.?',
            r',?\s*PLC', r',?\s*L\.?P\.?', r',?\s*Co\.?',
            r',?\s*Corporation', r',?\s*Limited', r',?\s*Incorporated',
        ]
        normalized = name.strip()
        for suffix in suffixes:
            normalized = re.sub(
                suffix + r'$', '', normalized, flags=re.IGNORECASE,
            )
        return normalized.strip()

    @staticmethod
    def _extract_field(text: str, field_name: str) -> str:
        """Extract a single field value from metadata text."""
        match = re.search(rf'-\s*{field_name}:\s*(.+)', text)
        return match.group(1).strip() if match else ''

    @staticmethod
    def _extract_list_field(text: str, field_name: str) -> List[str]:
        """Extract a list field from metadata text."""
        match = re.search(rf'-\s*{field_name}:\s*\[([^\]]*)\]', text)
        if not match:
            return []
        items = match.group(1).split(',')
        return [item.strip().strip('"\'') for item in items if item.strip()]


# ======================================================================
# Legacy backward compatibility
# ======================================================================
# The classes below preserve the old API used by blue_h2_news_collector.py:
#   extractor = get_extractor()
#   result = extractor.extract(text, title)  → ArticleExtraction
#   article.update(result.to_dict())         → llm_* prefixed keys
# ======================================================================


@dataclass
class ArticleExtraction:
    """Structured extraction result from an article (legacy API)."""
    project_name: Optional[str] = None
    developer: Optional[str] = None
    co_developers: List[str] = field(default_factory=list)
    status: Optional[str] = None
    status_confirmed: bool = False
    capacity_raw: Optional[str] = None
    capacity_mtpa_h2: Optional[float] = None
    technology: Optional[str] = None
    product: Optional[str] = None
    epc_contractor: Optional[str] = None
    location_state: Optional[str] = None
    location_subregion: Optional[str] = None
    region: Optional[str] = None
    fid_date: Optional[str] = None
    cod_date: Optional[str] = None
    deal_value_usd: Optional[float] = None
    entities_raw: List[Dict] = field(default_factory=list)
    relations_raw: List[Dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to flat dict compatible with existing article dict keys."""
        return {
            'llm_project_name': self.project_name,
            'llm_developer': self.developer,
            'llm_co_developers': (
                ','.join(self.co_developers) if self.co_developers else None
            ),
            'llm_status': self.status,
            'llm_status_confirmed': self.status_confirmed,
            'llm_capacity_raw': self.capacity_raw,
            'llm_capacity_mtpa_h2': self.capacity_mtpa_h2,
            'llm_technology': self.technology,
            'llm_product': self.product,
            'llm_epc_contractor': self.epc_contractor,
            'llm_location_state': self.location_state,
            'llm_location_subregion': self.location_subregion,
            'llm_region': self.region,
            'llm_fid_date': self.fid_date,
            'llm_cod_date': self.cod_date,
            'llm_deal_value_usd': self.deal_value_usd,
            'llm_extracted': True,
        }


class _LegacyEntityExtractor:
    """Legacy extractor preserving ``extract(text, title) → ArticleExtraction``.

    Used by ``blue_h2_news_collector.py`` via :func:`get_extractor`.
    Delegates GLiNER model loading to the shared module-level state.
    """

    def __init__(self, domain_config: str = 'hydrogen_ammonia'):
        self._gliner_model = None
        self._glirel_model = None
        self._config: dict = {}
        self._regions: dict = {}
        self._conversions: dict = {}

        config_path = _CONFIG_DIR / f'{domain_config}.yaml'
        if config_path.exists():
            with open(config_path, 'r') as f:
                self._config = yaml.safe_load(f)
            self._regions = self._config.get('regions', {})
            self._conversions = self._config.get('capacity_conversions', {})
        else:
            logger.warning("Domain config not found: %s", config_path)

    def _load_gliner(self):
        if self._gliner_model is not None:
            return
        try:
            from gliner import GLiNER
            logger.info("Loading GLiNER model...")
            self._gliner_model = GLiNER.from_pretrained(
                "urchade/gliner_multi_pii-v1"
            )
            logger.info("GLiNER model loaded")
        except Exception as e:
            logger.warning("GLiNER not available: %s", e)
            self._gliner_model = False

    def _load_glirel(self):
        if self._glirel_model is not None:
            return
        try:
            from glirel import GLiREL
            logger.info("Loading GLiREL model...")
            self._glirel_model = GLiREL.from_pretrained(
                "jackboyla/glirel-large-v0"
            )
            logger.info("GLiREL model loaded")
        except Exception as e:
            logger.warning("GLiREL not available: %s", e)
            self._glirel_model = False

    def extract(self, text: str, title: str = '') -> ArticleExtraction:
        """Extract structured entities and relations from article text."""
        result = ArticleExtraction()

        combined = f"{title}\n\n{text}" if title else text
        if len(combined) > 30000:
            combined = combined[:25000] + "\n...\n" + combined[-5000:]

        entities = self._extract_entities_gliner(combined)
        result.entities_raw = entities

        relations = self._extract_relations_glirel(combined, entities)
        result.relations_raw = relations

        self._map_entities_to_fields(result, entities, combined)
        self._map_relations_to_fields(result, relations)
        self._regex_fallbacks(result, combined)

        return result

    def _extract_entities_gliner(self, text: str) -> List[Dict]:
        self._load_gliner()
        if self._gliner_model is False:
            return []

        entity_types = self._config.get('entity_types', [
            'company', 'project_name', 'capacity', 'technology',
            'product', 'location', 'status', 'epc_contractor', 'date', 'money'
        ])

        all_entities = []
        seen = set()

        try:
            for chunk_idx in range(_GLINER_MAX_CHUNKS):
                start = chunk_idx * _GLINER_CHUNK_SIZE
                chunk = text[start:start + _GLINER_CHUNK_SIZE]
                if not chunk.strip():
                    break

                raw = self._gliner_model.predict_entities(
                    chunk, entity_types, threshold=0.3
                )
                for e in raw:
                    key = (e['text'].strip().lower(), e['label'])
                    if key not in seen:
                        seen.add(key)
                        all_entities.append({
                            'text': e['text'], 'label': e['label'],
                            'score': float(e['score'])
                        })
        except Exception as e:
            logger.warning("GLiNER extraction error: %s", e)

        return all_entities

    def _extract_relations_glirel(self, text: str,
                                   entities: List[Dict]) -> List[Dict]:
        self._load_glirel()
        if self._glirel_model is False or not entities:
            return []

        relation_types = self._config.get('relation_types', [
            'develops', 'located_in', 'capacity_of', 'uses_technology',
            'contracted_by', 'produces', 'invested_in'
        ])

        try:
            chunk = text[:_GLINER_CHUNK_SIZE]
            ner_spans = []
            seen_spans = set()
            for e in entities:
                search_start = 0
                while True:
                    start = chunk.find(e['text'], search_start)
                    if start < 0:
                        break
                    end = start + len(e['text'])
                    if (start, end) not in seen_spans:
                        seen_spans.add((start, end))
                        ner_spans.append({
                            'start': start, 'end': end,
                            'text': e['text'], 'label': e['label']
                        })
                    search_start = end

            if len(ner_spans) < 2:
                return []

            raw = self._glirel_model.predict_relations(
                chunk, relation_types, ner=ner_spans, threshold=0.3
            )
            return [{'head': r['head']['text'], 'tail': r['tail']['text'],
                      'label': r['label'], 'score': float(r['score'])}
                     for r in raw]
        except Exception as e:
            logger.warning("GLiREL extraction error: %s", e)
            return []

    def _map_entities_to_fields(self, result: ArticleExtraction,
                                entities: List[Dict], text: str):
        companies = []
        locations = []

        for ent in sorted(entities, key=lambda x: x.get('score', 0),
                          reverse=True):
            label = ent['label']
            val = ent['text'].strip()

            if label == 'company':
                companies.append(val)
            elif label == 'project_name' and not result.project_name:
                result.project_name = val
            elif label == 'capacity' and not result.capacity_raw:
                result.capacity_raw = val
                result.capacity_mtpa_h2 = self._convert_capacity(val)
            elif label == 'technology' and not result.technology:
                result.technology = self._normalize_technology(val)
            elif label == 'product' and not result.product:
                result.product = self._normalize_product(val)
            elif label == 'location':
                locations.append(val)
            elif label == 'status' and not result.status:
                result.status = self._normalize_status(val)
            elif label == 'epc_contractor' and not result.epc_contractor:
                result.epc_contractor = val
            elif label == 'money' and result.deal_value_usd is None:
                result.deal_value_usd = self._parse_money(val)
            elif label == 'date':
                self._assign_date(result, val, text)

        if companies:
            result.developer = companies[0].lower()
            result.co_developers = [c.lower() for c in companies[1:4]]

        if locations:
            region, state, subregion = self._classify_region(locations, text)
            result.region = region
            result.location_state = state
            result.location_subregion = subregion

    def _map_relations_to_fields(self, result: ArticleExtraction,
                                 relations: List[Dict]):
        for rel in relations:
            label = rel['label']
            if label == 'develops' and not result.developer:
                result.developer = rel['head'].lower()
            elif label == 'contracted_by' and not result.epc_contractor:
                result.epc_contractor = rel['tail'].strip()
            elif label == 'uses_technology' and not result.technology:
                result.technology = self._normalize_technology(rel['tail'])
            elif label == 'located_in':
                if not result.location_subregion:
                    result.location_subregion = rel['tail']

    def _regex_fallbacks(self, result: ArticleExtraction, text: str):
        text_lower = text.lower()

        if not result.technology:
            result.technology = self._normalize_technology(text_lower)
        if not result.status:
            result.status = self._normalize_status(text_lower)
        if not result.product:
            result.product = self._normalize_product(text_lower)

        if not result.capacity_raw:
            cap_match = re.search(
                r'(\d[\d,.]*)\s*(MTPA|TPD|GW|MW|'
                r'tonnes?\s*per\s*day|'
                r'million\s*tonnes?\s*per\s*(?:annum|year)|'
                r'kt/?(?:year|yr|a)|'
                r'kg/?(?:day|d)|'
                r'Nm3/?h)',
                text, re.IGNORECASE
            )
            if cap_match:
                result.capacity_raw = cap_match.group(0)
                result.capacity_mtpa_h2 = self._convert_capacity(
                    cap_match.group(0))

        if result.deal_value_usd is None:
            money_match = re.search(
                r'\$\s*([\d,.]+)\s*(billion|million|B|M)\b',
                text, re.IGNORECASE,
            )
            if money_match:
                result.deal_value_usd = self._parse_money(money_match.group(0))

        if not result.region:
            region, state, sub = self._classify_region([], text)
            result.region = region
            if state and not result.location_state:
                result.location_state = state
            if sub and not result.location_subregion:
                result.location_subregion = sub

        if result.status:
            confirmed_patterns = [
                r'has reached FID', r'reached final investment',
                r'awarded.*EPC', r'broke ground', r'began construction',
                r'is now operational', r'achieved.*COD',
                r'commenced production',
                r'signed.*contract', r'completed.*FEED',
            ]
            for pat in confirmed_patterns:
                if re.search(pat, text, re.IGNORECASE):
                    result.status_confirmed = True
                    break

    # -- Normalization helpers -----------------------------------------------

    def _normalize_technology(self, text: str) -> Optional[str]:
        patterns = self._config.get('technology_patterns', {})
        text_lower = text.lower()
        for tech, keywords in patterns.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    return tech
        return None

    def _normalize_status(self, text: str) -> Optional[str]:
        patterns = self._config.get('status_patterns', {})
        text_lower = text.lower()
        priority_order = ['FID', 'EPC Award', 'Construction', 'Operational',
                          'FEED', 'Pre-FEED', 'Cancelled', 'Delayed',
                          'Announced']
        for status in priority_order:
            keywords = patterns.get(status, [])
            for kw in keywords:
                if kw.lower() in text_lower:
                    return status
        return None

    def _normalize_product(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        if 'hydrogen' in text_lower:
            return 'Hydrogen'
        if 'ammonia' in text_lower:
            return 'Ammonia'
        if 'methanol' in text_lower:
            return 'Methanol'
        if (re.search(r'\bsaf\b', text_lower)
                or 'sustainable aviation fuel' in text_lower):
            return 'SAF'
        return None

    def _convert_capacity(self, raw: str) -> Optional[float]:
        if not raw:
            return None
        num_match = re.search(r'([\d,.]+)', raw)
        if not num_match:
            return None
        num = float(num_match.group(1).replace(',', ''))

        raw_upper = raw.upper()
        conv = self._conversions or {
            'TPD_H2_to_MTPA': 0.000365,
            'MTPA_NH3_to_MTPA_H2': 0.178,
            'GW_electrolyzer_to_MTPA_H2': 0.16,
            'MW_electrolyzer_to_MTPA_H2': 0.00016,
        }

        if 'TPD' in raw_upper or 'TONNES PER DAY' in raw_upper:
            return round(num * conv.get('TPD_H2_to_MTPA', 0.000365), 4)
        elif 'MTPA' in raw_upper or 'MILLION TONNES PER' in raw_upper:
            if 'NH3' in raw_upper or 'AMMONIA' in raw_upper:
                return round(
                    num * conv.get('MTPA_NH3_to_MTPA_H2', 0.178), 4)
            return round(num, 4)
        elif 'GW' in raw_upper:
            return round(
                num * conv.get('GW_electrolyzer_to_MTPA_H2', 0.16), 4)
        elif 'MW' in raw_upper:
            return round(
                num * conv.get('MW_electrolyzer_to_MTPA_H2', 0.00016), 4)
        elif 'KT' in raw_upper:
            return round(num * 0.001, 4)
        elif 'KG' in raw_upper:
            tpd = num / 1000.0
            return round(tpd * conv.get('TPD_H2_to_MTPA', 0.000365), 4)
        elif 'NM3' in raw_upper:
            tpd = num * 0.0899 * 24 / 1000.0
            return round(tpd * conv.get('TPD_H2_to_MTPA', 0.000365), 4)
        return None

    def _parse_money(self, text: str) -> Optional[float]:
        if not text:
            return None
        match = re.search(
            r'\$?\s*([\d,.]+)\s*(billion|million|B|M)\b',
            text, re.IGNORECASE,
        )
        if not match:
            return None
        num = float(match.group(1).replace(',', ''))
        unit = match.group(2).lower()
        if unit in ('billion', 'b'):
            return num * 1_000_000_000
        elif unit in ('million', 'm'):
            return num * 1_000_000
        return num

    def _assign_date(self, result: ArticleExtraction, date_text: str,
                     full_text: str):
        context_window = 200
        fid_kws = ['fid', 'final investment', 'financial close', 'sanction']
        cod_kws = ['cod', 'commercial operation', 'online',
                   'first production', 'start-up', 'startup']

        search_start = 0
        while True:
            idx = full_text.find(date_text, search_start)
            if idx < 0:
                break
            surrounding = full_text[
                max(0, idx - context_window):
                idx + len(date_text) + context_window
            ].lower()

            if (not result.fid_date
                    and any(kw in surrounding for kw in fid_kws)):
                result.fid_date = date_text
            elif (not result.cod_date
                    and any(kw in surrounding for kw in cod_kws)):
                result.cod_date = date_text

            if result.fid_date and result.cod_date:
                break
            search_start = idx + len(date_text)

    def _classify_region(self, location_entities: List[str],
                         text: str) -> Tuple[Optional[str], Optional[str],
                                             Optional[str]]:
        entity_text = ' '.join(location_entities)
        for search_text in [entity_text, text[:3000]]:
            if not search_text.strip():
                continue
            search_lower = search_text.lower()

            for region_name, region_data in self._regions.items():
                for city in region_data.get('cities', []):
                    if re.search(r'\b' + re.escape(city.lower()) + r'\b',
                                 search_lower):
                        states = region_data.get('states', [])
                        state = states[0] if states else None
                        return region_name, state, city

                for state in region_data.get('states', []):
                    if re.search(r'\b' + re.escape(state.lower()) + r'\b',
                                 search_lower):
                        return region_name, state, None

        return 'Unknown', None, None


# ---------------------------------------------------------------------------
# Module-level singleton (legacy — returns _LegacyEntityExtractor)
# ---------------------------------------------------------------------------
_instance: Optional[_LegacyEntityExtractor] = None


def get_extractor(domain: str = 'hydrogen_ammonia') -> _LegacyEntityExtractor:
    """Get or create the module-level legacy EntityExtractor singleton.

    Used by ``blue_h2_news_collector.py``.  Returns the legacy extractor
    that supports ``extract(text, title) → ArticleExtraction``.

    For new code, use :class:`EntityExtractor` directly.
    """
    global _instance
    if _instance is None:
        _instance = _LegacyEntityExtractor(domain_config=domain)
    return _instance
