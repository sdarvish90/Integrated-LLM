"""
Layers 2+3 — GLiNER Entity Extraction + GLiREL Relation Extraction
====================================================================
Zero-shot NER and relation extraction without training data.

Performance: ~30ms per article on CPU for each layer.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_CONFIG_DIR = _HERE.parent / 'config' / 'domains'

# GLiNER chunk size: process multiple chunks for longer documents (C5 fix)
_GLINER_CHUNK_SIZE = 4500
_GLINER_MAX_CHUNKS = 4  # up to 18K chars searched for entities


@dataclass
class ArticleExtraction:
    """Structured extraction result from an article."""
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
            'llm_co_developers': ','.join(self.co_developers) if self.co_developers else None,
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


class EntityExtractor:
    """Zero-shot entity and relation extraction using GLiNER + GLiREL."""

    def __init__(self, domain_config: str = 'hydrogen_ammonia'):
        self._gliner_model = None
        self._glirel_model = None
        self._config: dict = {}
        self._regions: dict = {}
        self._conversions: dict = {}

        # Load config
        config_path = _CONFIG_DIR / f'{domain_config}.yaml'
        if config_path.exists():
            with open(config_path, 'r') as f:
                self._config = yaml.safe_load(f)
            self._regions = self._config.get('regions', {})
            self._conversions = self._config.get('capacity_conversions', {})
        else:
            logger.warning(f"Domain config not found: {config_path}")

    def _load_gliner(self):
        """Lazy-load GLiNER model on first use."""
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
            logger.warning(f"GLiNER not available: {e}")
            self._gliner_model = False  # sentinel: tried but failed

    def _load_glirel(self):
        """Lazy-load GLiREL model on first use."""
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
            logger.warning(f"GLiREL not available: {e}")
            self._glirel_model = False  # sentinel

    def extract(self, text: str, title: str = '') -> ArticleExtraction:
        """Extract structured entities and relations from article text.

        Args:
            text: Article full text (or snippet if full text unavailable)
            title: Article title (prepended for context)

        Returns:
            ArticleExtraction dataclass with all extracted fields
        """
        result = ArticleExtraction()

        # Combine title + text, smart truncation at 30K chars
        combined = f"{title}\n\n{text}" if title else text
        if len(combined) > 30000:
            combined = combined[:25000] + "\n...\n" + combined[-5000:]

        # --- Layer 2: GLiNER Entity Extraction (chunked — C5 fix) ---
        entities = self._extract_entities_gliner(combined)
        result.entities_raw = entities

        # --- Layer 3: GLiREL Relation Extraction (on first chunk) ---
        relations = self._extract_relations_glirel(combined, entities)
        result.relations_raw = relations

        # --- Post-processing: map raw entities to structured fields ---
        self._map_entities_to_fields(result, entities, combined)
        self._map_relations_to_fields(result, relations)

        # --- Regex-based fallbacks for fields not found by NER ---
        self._regex_fallbacks(result, combined)

        return result

    def _extract_entities_gliner(self, text: str) -> List[Dict]:
        """Run GLiNER zero-shot NER on text using chunked processing.

        Processes multiple non-overlapping chunks to cover more of the document.
        Deduplicates entities by (text, label) to avoid redundant results.
        """
        self._load_gliner()
        if self._gliner_model is False:
            return []

        entity_types = self._config.get('entity_types', [
            'company', 'project_name', 'capacity', 'technology',
            'product', 'location', 'status', 'epc_contractor', 'date', 'money'
        ])

        all_entities = []
        seen = set()  # (text_lower, label) for dedup

        try:
            # Process multiple chunks (C5 fix: was 5K, now up to 18K)
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
            logger.warning(f"GLiNER extraction error: {e}")

        return all_entities

    def _extract_relations_glirel(self, text: str,
                                   entities: List[Dict]) -> List[Dict]:
        """Run GLiREL relation extraction between entities."""
        self._load_glirel()
        if self._glirel_model is False or not entities:
            return []

        relation_types = self._config.get('relation_types', [
            'develops', 'located_in', 'capacity_of', 'uses_technology',
            'contracted_by', 'produces', 'invested_in'
        ])

        try:
            chunk = text[:_GLINER_CHUNK_SIZE]
            # Format entities for GLiREL — find ALL occurrences (I3 fix)
            ner_spans = []
            seen_spans = set()  # (start, end) dedup
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
                            'start': start,
                            'end': end,
                            'text': e['text'],
                            'label': e['label']
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
            logger.warning(f"GLiREL extraction error: {e}")
            return []

    def _map_entities_to_fields(self, result: ArticleExtraction,
                                entities: List[Dict], text: str):
        """Map GLiNER entities to structured ArticleExtraction fields."""
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

        # Assign developer / co-developers (I4 fix: consistent casing)
        if companies:
            result.developer = companies[0].lower()
            result.co_developers = [c.lower() for c in companies[1:4]]

        # Assign location fields
        if locations:
            region, state, subregion = self._classify_region(locations, text)
            result.region = region
            result.location_state = state
            result.location_subregion = subregion

    def _map_relations_to_fields(self, result: ArticleExtraction,
                                 relations: List[Dict]):
        """Use GLiREL relations to refine field assignments."""
        for rel in relations:
            label = rel['label']
            if label == 'develops' and not result.developer:
                result.developer = rel['head'].lower()
            elif label == 'contracted_by' and not result.epc_contractor:
                # I4 fix: normalize casing for epc_contractor too
                result.epc_contractor = rel['tail'].strip()
            elif label == 'uses_technology' and not result.technology:
                result.technology = self._normalize_technology(rel['tail'])
            elif label == 'located_in':
                if not result.location_subregion:
                    result.location_subregion = rel['tail']

    def _regex_fallbacks(self, result: ArticleExtraction, text: str):
        """Use regex patterns for fields not found by GLiNER."""
        text_lower = text.lower()

        # Technology fallback
        if not result.technology:
            result.technology = self._normalize_technology(text_lower)

        # Status fallback
        if not result.status:
            result.status = self._normalize_status(text_lower)

        # Product fallback
        if not result.product:
            result.product = self._normalize_product(text_lower)

        # Capacity fallback (M3 fix: added kg/day, Nm3/h, kt/year)
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

        # Money fallback
        if result.deal_value_usd is None:
            money_match = re.search(
                r'\$\s*([\d,.]+)\s*(billion|million|B|M)\b', text, re.IGNORECASE
            )
            if money_match:
                result.deal_value_usd = self._parse_money(money_match.group(0))

        # Region fallback from text
        if not result.region:
            region, state, sub = self._classify_region([], text)
            result.region = region
            if state and not result.location_state:
                result.location_state = state
            if sub and not result.location_subregion:
                result.location_subregion = sub

        # Status confirmation: past tense / confirmed language
        if result.status:
            confirmed_patterns = [
                r'has reached FID', r'reached final investment',
                r'awarded.*EPC', r'broke ground', r'began construction',
                r'is now operational', r'achieved.*COD', r'commenced production',
                r'signed.*contract', r'completed.*FEED',
            ]
            for pat in confirmed_patterns:
                if re.search(pat, text, re.IGNORECASE):
                    result.status_confirmed = True
                    break

    # -- Normalization helpers -----------------------------------------------

    def _normalize_technology(self, text: str) -> Optional[str]:
        """Map text to normalized technology label."""
        patterns = self._config.get('technology_patterns', {})
        text_lower = text.lower()
        for tech, keywords in patterns.items():
            for kw in keywords:
                if kw.lower() in text_lower:
                    return tech
        return None

    def _normalize_status(self, text: str) -> Optional[str]:
        """Map text to normalized status label."""
        patterns = self._config.get('status_patterns', {})
        text_lower = text.lower()
        # Check in priority order (FID > EPC Award > Construction > ... > Announced)
        priority_order = ['FID', 'EPC Award', 'Construction', 'Operational',
                          'FEED', 'Pre-FEED', 'Cancelled', 'Delayed', 'Announced']
        for status in priority_order:
            keywords = patterns.get(status, [])
            for kw in keywords:
                if kw.lower() in text_lower:
                    return status
        return None

    def _normalize_product(self, text: str) -> Optional[str]:
        """Detect primary product from text.

        M2 fix: Hydrogen checked before ammonia (blue H2 is the primary product).
        C3 fix: SAF uses word boundary regex to avoid "safe"/"safety" false positives.
        """
        text_lower = text.lower()
        # Hydrogen first — primary product for blue H2 projects
        if 'hydrogen' in text_lower:
            return 'Hydrogen'
        if 'ammonia' in text_lower:
            return 'Ammonia'
        if 'methanol' in text_lower:
            return 'Methanol'
        # C3 fix: word-boundary match for SAF to avoid "safe", "safety", etc.
        if re.search(r'\bsaf\b', text_lower) or 'sustainable aviation fuel' in text_lower:
            return 'SAF'
        return None

    def _convert_capacity(self, raw: str) -> Optional[float]:
        """Convert capacity string to MTPA H2 equivalent.

        M3 fix: handles kg/day, Nm3/h, kt/year in addition to existing units.
        """
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
                return round(num * conv.get('MTPA_NH3_to_MTPA_H2', 0.178), 4)
            return round(num, 4)
        elif 'GW' in raw_upper:
            return round(num * conv.get('GW_electrolyzer_to_MTPA_H2', 0.16), 4)
        elif 'MW' in raw_upper:
            return round(num * conv.get('MW_electrolyzer_to_MTPA_H2', 0.00016), 4)
        # M3: kt/year — kilotonnes per year
        elif 'KT' in raw_upper:
            return round(num * 0.001, 4)  # 1 kt = 0.001 MTPA
        # M3: kg/day — convert via TPD (1000 kg = 1 tonne)
        elif 'KG' in raw_upper:
            tpd = num / 1000.0
            return round(tpd * conv.get('TPD_H2_to_MTPA', 0.000365), 4)
        # M3: Nm3/h — normal cubic meters per hour (1 Nm3 H2 ≈ 0.0899 kg)
        elif 'NM3' in raw_upper:
            tpd = num * 0.0899 * 24 / 1000.0  # Nm3/h → kg/h → kg/day → t/day
            return round(tpd * conv.get('TPD_H2_to_MTPA', 0.000365), 4)

        return None

    def _parse_money(self, text: str) -> Optional[float]:
        """Parse money expression to USD float."""
        if not text:
            return None
        match = re.search(r'\$?\s*([\d,.]+)\s*(billion|million|B|M)\b',
                          text, re.IGNORECASE)
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
        """Assign date to FID or COD based on context.

        I6 fix: checks ALL occurrences of date_text, not just the first.
        """
        context_window = 200
        fid_kws = ['fid', 'final investment', 'financial close', 'sanction']
        cod_kws = ['cod', 'commercial operation', 'online', 'first production',
                   'start-up', 'startup']

        search_start = 0
        while True:
            idx = full_text.find(date_text, search_start)
            if idx < 0:
                break
            surrounding = full_text[max(0, idx - context_window):
                                    idx + len(date_text) + context_window].lower()

            if not result.fid_date and any(kw in surrounding for kw in fid_kws):
                result.fid_date = date_text
            elif not result.cod_date and any(kw in surrounding for kw in cod_kws):
                result.cod_date = date_text

            # Stop early if both dates found
            if result.fid_date and result.cod_date:
                break
            search_start = idx + len(date_text)

    def _classify_region(self, location_entities: List[str],
                         text: str) -> Tuple[Optional[str], Optional[str],
                                             Optional[str]]:
        """Classify region from location entities and text.

        I5 fix: uses word-boundary regex instead of bare substring matching
        to avoid false positives like 'Houston' in 'Sam Houston'.

        Returns: (region, state, subregion)
        """
        # Prioritize location entities (high confidence from NER)
        entity_text = ' '.join(location_entities)
        # Check entities first (more trustworthy), then article text
        for search_text in [entity_text, text[:3000]]:
            if not search_text.strip():
                continue
            search_lower = search_text.lower()

            for region_name, region_data in self._regions.items():
                # Check cities first (more specific)
                for city in region_data.get('cities', []):
                    # I5 fix: word-boundary match
                    if re.search(r'\b' + re.escape(city.lower()) + r'\b',
                                 search_lower):
                        states = region_data.get('states', [])
                        state = states[0] if states else None
                        return region_name, state, city

                # Check states
                for state in region_data.get('states', []):
                    if re.search(r'\b' + re.escape(state.lower()) + r'\b',
                                 search_lower):
                        return region_name, state, None

        return 'Unknown', None, None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: Optional[EntityExtractor] = None


def get_extractor(domain: str = 'hydrogen_ammonia') -> EntityExtractor:
    """Get or create the module-level EntityExtractor singleton."""
    global _instance
    if _instance is None:
        _instance = EntityExtractor(domain_config=domain)
    return _instance
