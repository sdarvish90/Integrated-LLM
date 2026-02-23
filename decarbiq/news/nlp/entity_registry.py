"""
Entity Registry — Loads entities.yaml, builds lookup indexes.
================================================================

Resolves company names, CIK numbers, tickers, and EPA IDs to
canonical forms using the seed training entity catalog.

Handles the YAML indentation bug in entities.yaml where several
companies (yara, nutrien, plug_power, etc.) are accidentally
nested inside air_liquide.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_ENTITIES = (
    _HERE.parent.parent.parent / 'seed training' / 'domains' / 'core' / 'entities.yaml'
)

# Suffixes stripped during normalization
_COMPANY_SUFFIXES = re.compile(
    r',?\s*(?:Inc\.?|LLC|Ltd\.?|Corp\.?|PLC|L\.?P\.?|Co\.?|Corporation|'
    r'Limited|Incorporated|S\.?A\.?|S\.?p\.?A\.?|A/?S|SE|AG|N\.?V\.?)\s*$',
    re.IGNORECASE,
)


# ======================================================================
# Data classes
# ======================================================================


@dataclass
class RegistryMatch:
    """Result of an entity registry lookup."""
    key: str                              # "air_products"
    canonical_name: str                   # "Air Products"
    method: str                           # "exact_canonical", "alias", "normalized", "fuzzy", "cik", "ticker", "epa_id"
    confidence: float                     # 0.0–1.0
    sectors: List[str] = field(default_factory=list)
    role: Optional[str] = None
    identifiers: Dict[str, Any] = field(default_factory=dict)
    aliases: List[str] = field(default_factory=list)


# ======================================================================
# EntityRegistry
# ======================================================================


class EntityRegistry:
    """In-memory entity catalog loaded from entities.yaml.

    Builds six indexes for fast lookup:
    - canonical name → key
    - alias → key
    - normalized (suffix-stripped) → key
    - CIK → key
    - ticker → key
    - EPA facility ID → key

    Optionally persists runtime-discovered entities to SQLite.
    """

    def __init__(
        self,
        yaml_path: Optional[str] = None,
        db_path: Optional[str] = None,
        fuzzy_threshold: float = 0.80,
    ):
        self._fuzzy_threshold = fuzzy_threshold
        self._db_path = db_path

        # Raw company data: key → dict with canonical_name, aliases, identifiers, etc.
        self._companies: Dict[str, Dict[str, Any]] = {}

        # Location data
        self._states: Dict[str, str] = {}          # "Louisiana" → "LA"
        self._state_abbr_set: set = set()           # {"LA", "TX", ...}
        self._regions: Dict[str, Dict] = {}         # "gulf_coast" → {states, aliases}
        self._iso_regions: Dict[str, Dict] = {}

        # Project names seen (for fuzzy dedup)
        self._known_projects: Dict[str, str] = {}   # normalized → canonical

        # Indexes (all keys lowercased)
        self._canonical_index: Dict[str, str] = {}   # "air products" → "air_products"
        self._alias_index: Dict[str, str] = {}
        self._normalized_index: Dict[str, str] = {}
        self._cik_index: Dict[str, str] = {}
        self._ticker_index: Dict[str, str] = {}
        self._epa_index: Dict[str, str] = {}

        if yaml_path:
            self._load_yaml(yaml_path)
        if db_path:
            self._init_runtime_db()
            self._load_runtime_entities()

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def default(cls, db_path: Optional[str] = None) -> EntityRegistry:
        """Load from the standard seed training entities.yaml."""
        return cls(yaml_path=str(_DEFAULT_ENTITIES), db_path=db_path)

    @classmethod
    def from_yaml(cls, path: str, db_path: Optional[str] = None) -> EntityRegistry:
        """Load from an explicit YAML path."""
        return cls(yaml_path=path, db_path=db_path)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], db_path: Optional[str] = None) -> EntityRegistry:
        """Load from a pre-loaded dict (e.g., from DomainModel)."""
        reg = cls(db_path=db_path)
        reg._parse_data(data)
        return reg

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_yaml(self, path: str):
        """Load and parse entities YAML file."""
        p = Path(path)
        if not p.exists():
            logger.warning("Entities YAML not found: %s", path)
            return

        with open(p, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        if data:
            self._parse_data(data)

    def _parse_data(self, data: Dict[str, Any]):
        """Parse the entities data dict and build all indexes."""
        # Load companies (handles indentation bug)
        raw_companies = data.get('known_companies', {})
        if raw_companies:
            self._flatten_companies(raw_companies)

        # Also search for known_companies nested elsewhere due to YAML bugs
        # (e.g., gemini section's companies end up under agencies.known_companies)
        self._find_nested_companies(data, skip_key='known_companies')

        # Load locations
        locations = data.get('locations', {})
        if locations:
            self._states = locations.get('states', {})
            self._state_abbr_set = set(self._states.values())
            self._regions = locations.get('regions', {})
            self._iso_regions = locations.get('iso_regions', {})

        # Build indexes
        self._build_indexes()

        logger.info(
            "EntityRegistry loaded: %d companies, %d states, %d regions",
            len(self._companies), len(self._states), len(self._regions),
        )

    def _flatten_companies(self, raw: Dict[str, Any], parent_key: str = ''):
        """Walk the YAML tree and extract any dict with 'canonical_name'.

        Handles two YAML indentation bugs in entities.yaml:
        1. Companies nested inside other companies (yara inside air_liquide)
           → sub-key is a dict with 'canonical_name'
        2. Company key maps to None with attributes at parent level
           (lsb_industries: None, canonical_name: "LSB Industries" at parent)
           → sub-key is None, parent's canonical_name was overwritten
        """
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue

            if 'canonical_name' in value:
                # This is a valid company entry — but check for bugs
                # First, extract any properly nested sub-companies
                nested_keys = []
                for sub_key, sub_val in value.items():
                    if isinstance(sub_val, dict) and 'canonical_name' in sub_val:
                        self._companies[sub_key] = sub_val
                        nested_keys.append(sub_key)

                # Bug #2: Check for sub-keys that map to None (broken indent)
                # When YAML sees "lsb_industries:\n  canonical_name: X" but
                # canonical_name is at the PARENT level, lsb_industries → None
                # and canonical_name overwrites the parent's value.
                none_child_keys = [
                    k for k, v in value.items()
                    if v is None and k not in (
                        'canonical_name', 'aliases', 'identifiers', 'sectors',
                        'role', 'technologies', 'projects', 'partners',
                        'financing', 'customers', 'technology',
                    )
                ]

                if none_child_keys:
                    # The last None-valued key is likely the one whose attributes
                    # overwrote the parent. Reconstruct it.
                    orphan_key = none_child_keys[-1]
                    current_canonical = value.get('canonical_name', '')
                    # Heuristic: if canonical_name doesn't match the parent key,
                    # it was overwritten by the orphan's data
                    parent_name_guess = key.replace('_', ' ')
                    if current_canonical.lower().replace(' ', '') != parent_name_guess.replace(' ', ''):
                        # Extract orphan company from parent's overwritten fields
                        orphan_data = {
                            'canonical_name': current_canonical,
                        }
                        # Move identifiers/sectors if they look like they belong to orphan
                        if 'identifiers' in value:
                            orphan_data['identifiers'] = value['identifiers']
                        if 'sectors' in value:
                            orphan_data['sectors'] = value['sectors']
                        self._companies[orphan_key] = orphan_data

                        # Restore parent's canonical_name from its aliases or key
                        aliases = value.get('aliases', [])
                        if isinstance(aliases, str):
                            aliases = [aliases]
                        # Try to find a clean canonical from aliases
                        restored = None
                        for alias in aliases:
                            # First alias without suffixes is usually canonical
                            clean = alias.replace(' S.A.', '').replace(' plc', '').replace(' PLC', '')
                            if clean:
                                restored = clean
                                break
                        if not restored:
                            # Title-case from key as last resort
                            restored = key.replace('_', ' ').title()
                        value['canonical_name'] = restored

                # Store the (possibly restored) parent company
                self._companies[key] = value

            elif isinstance(value, dict):
                # Could be a nested section (like the gemini block)
                nested = value.get('known_companies', {})
                if nested:
                    self._flatten_companies(nested)
                else:
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, dict) and 'canonical_name' in sub_val:
                            self._companies[sub_key] = sub_val

    def _find_nested_companies(self, data: Dict[str, Any], skip_key: str = ''):
        """Recursively search for known_companies dicts nested anywhere in the YAML.

        The gemini section's companies end up under agencies.known_companies
        due to indentation issues. This catches those.
        """
        for key, value in data.items():
            if key == skip_key or not isinstance(value, dict):
                continue
            # Check if this dict has a known_companies sub-dict
            nested_kc = value.get('known_companies')
            if isinstance(nested_kc, dict):
                self._flatten_companies(nested_kc)
            # Recurse into sub-dicts
            self._find_nested_companies(value)

    def _build_indexes(self):
        """Build all lookup indexes from the loaded company data."""
        self._canonical_index.clear()
        self._alias_index.clear()
        self._normalized_index.clear()
        self._cik_index.clear()
        self._ticker_index.clear()
        self._epa_index.clear()

        for key, data in self._companies.items():
            canonical = data.get('canonical_name', '')
            if not canonical:
                continue

            # Canonical name index
            self._canonical_index[canonical.lower()] = key

            # Normalized form of canonical name
            norm = self._strip_suffixes(canonical).lower()
            if norm and norm != canonical.lower():
                self._normalized_index[norm] = key

            # Alias index
            aliases = data.get('aliases', [])
            if isinstance(aliases, str):
                aliases = [aliases]
            for alias in aliases:
                alias_lower = alias.lower()
                self._alias_index[alias_lower] = key
                # Also index normalized form of alias
                norm_alias = self._strip_suffixes(alias).lower()
                if norm_alias and norm_alias != alias_lower:
                    self._normalized_index[norm_alias] = key

            # Identifier indexes
            identifiers = data.get('identifiers', {})
            if isinstance(identifiers, dict):
                cik = identifiers.get('cik', '')
                if cik:
                    self._cik_index[cik.lower()] = key

                ticker = identifiers.get('ticker', '')
                if ticker:
                    self._ticker_index[ticker.lower()] = key

                epa_ids = identifiers.get('epa_ids', [])
                if isinstance(epa_ids, list):
                    for epa_id in epa_ids:
                        if epa_id:
                            self._epa_index[str(epa_id).lower()] = key

    # ------------------------------------------------------------------
    # Company lookup
    # ------------------------------------------------------------------

    def lookup(self, name: str) -> Optional[RegistryMatch]:
        """Look up a company name through the cascade.

        Cascade: exact canonical → alias → normalized → fuzzy.
        """
        if not name or not name.strip():
            return None

        name_lower = name.strip().lower()

        # Step 1: Exact canonical match
        key = self._canonical_index.get(name_lower)
        if key:
            return self._build_match(key, 'exact_canonical', 1.0)

        # Step 2: Exact alias match
        key = self._alias_index.get(name_lower)
        if key:
            return self._build_match(key, 'alias', 0.95)

        # Step 3: Normalized (suffix-stripped) match
        normalized = self._strip_suffixes(name).lower()
        key = self._canonical_index.get(normalized)
        if key:
            return self._build_match(key, 'normalized', 0.90)
        key = self._alias_index.get(normalized)
        if key:
            return self._build_match(key, 'normalized', 0.90)
        key = self._normalized_index.get(normalized)
        if key:
            return self._build_match(key, 'normalized', 0.90)

        # Step 4: Fuzzy match
        return self._fuzzy_lookup(name)

    def lookup_cik(self, cik: str) -> Optional[RegistryMatch]:
        """Look up by SEC CIK number."""
        if not cik:
            return None
        key = self._cik_index.get(cik.strip().lower())
        if key:
            return self._build_match(key, 'cik', 0.99)
        return None

    def lookup_ticker(self, ticker: str) -> Optional[RegistryMatch]:
        """Look up by stock ticker."""
        if not ticker:
            return None
        key = self._ticker_index.get(ticker.strip().lower())
        if key:
            return self._build_match(key, 'ticker', 0.97)
        return None

    def lookup_epa_id(self, epa_id: str) -> Optional[RegistryMatch]:
        """Look up by EPA facility registry ID."""
        if not epa_id:
            return None
        key = self._epa_index.get(str(epa_id).strip().lower())
        if key:
            return self._build_match(key, 'epa_id', 0.98)
        return None

    def _fuzzy_lookup(self, name: str) -> Optional[RegistryMatch]:
        """Fuzzy match against all canonical names and aliases."""
        name_lower = name.strip().lower()
        best_ratio = 0.0
        best_key = None

        # Check canonical names
        for canonical, key in self._canonical_index.items():
            ratio = SequenceMatcher(None, name_lower, canonical).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_key = key

        # Check aliases
        for alias, key in self._alias_index.items():
            ratio = SequenceMatcher(None, name_lower, alias).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_key = key

        if best_key and best_ratio >= self._fuzzy_threshold:
            return self._build_match(best_key, 'fuzzy', best_ratio * 0.85)

        return None

    def _build_match(self, key: str, method: str, confidence: float) -> RegistryMatch:
        """Build a RegistryMatch from a company key."""
        data = self._companies.get(key, {})
        identifiers = data.get('identifiers', {})
        if not isinstance(identifiers, dict):
            identifiers = {}
        aliases = data.get('aliases', [])
        if isinstance(aliases, str):
            aliases = [aliases]

        return RegistryMatch(
            key=key,
            canonical_name=data.get('canonical_name', key),
            method=method,
            confidence=confidence,
            sectors=data.get('sectors', []),
            role=data.get('role'),
            identifiers=identifiers,
            aliases=aliases,
        )

    # ------------------------------------------------------------------
    # Location normalization
    # ------------------------------------------------------------------

    def normalize_location(self, text: str) -> Optional[Dict[str, Any]]:
        """Normalize location text to state abbreviation + region.

        Returns dict with keys: state, city, region, confidence.
        Returns None if no match found.
        """
        if not text or not text.strip():
            return None

        text_clean = text.strip()
        state_abbr = None
        city = None
        confidence = 0.0

        # Check if text IS a state abbreviation
        if text_clean.upper() in self._state_abbr_set:
            state_abbr = text_clean.upper()
            confidence = 1.0

        # Check if text IS a full state name
        if not state_abbr:
            for full_name, abbr in self._states.items():
                if text_clean.lower() == full_name.lower():
                    state_abbr = abbr
                    confidence = 1.0
                    break

        # Check "City, ST" pattern
        if not state_abbr:
            match = re.match(r'^(.+?),\s*([A-Z]{2})$', text_clean)
            if match:
                potential_city = match.group(1).strip()
                potential_abbr = match.group(2)
                if potential_abbr in self._state_abbr_set:
                    city = potential_city
                    state_abbr = potential_abbr
                    confidence = 0.95

        # Check "City, State Name" pattern
        if not state_abbr:
            match = re.match(r'^(.+?),\s*(.+)$', text_clean)
            if match:
                potential_city = match.group(1).strip()
                potential_state = match.group(2).strip()
                for full_name, abbr in self._states.items():
                    if potential_state.lower() == full_name.lower():
                        city = potential_city
                        state_abbr = abbr
                        confidence = 0.90
                        break

        # Check if text contains a state name
        if not state_abbr:
            text_lower = text_clean.lower()
            for full_name, abbr in self._states.items():
                if full_name.lower() in text_lower:
                    state_abbr = abbr
                    confidence = 0.75
                    break

        if not state_abbr:
            return None

        region = self._lookup_region(state_abbr)

        return {
            'state': state_abbr,
            'city': city,
            'region': region,
            'confidence': confidence,
        }

    def _lookup_region(self, state_abbr: str) -> Optional[str]:
        """Find which region a state belongs to."""
        for region_name, region_data in self._regions.items():
            states = region_data.get('states', [])
            if state_abbr in states:
                return region_name
        return None

    def lookup_region(self, state_abbr: str) -> Optional[str]:
        """Public method: find which region a state belongs to."""
        return self._lookup_region(state_abbr)

    # ------------------------------------------------------------------
    # Project name resolution
    # ------------------------------------------------------------------

    def normalize_project_name(self, name: str, fuzzy: bool = False) -> str:
        """Normalize project name for deduplication.

        Strips common prefixes/suffixes, normalizes whitespace and case.

        If fuzzy=True, also checks against previously seen project names
        using SequenceMatcher. Returns the canonical form of the best match
        if found, otherwise the cleaned name.
        """
        cleaned = self._clean_project_name(name)

        if fuzzy and self._known_projects:
            best_ratio = 0.0
            best_canonical = None
            for known_norm, known_canonical in self._known_projects.items():
                ratio = SequenceMatcher(None, cleaned, known_norm).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_canonical = known_canonical
            if best_canonical and best_ratio >= self._fuzzy_threshold:
                return best_canonical

        # Register the cleaned name for future fuzzy matching
        if cleaned and cleaned not in self._known_projects:
            self._known_projects[cleaned] = name.strip()

        return cleaned

    def lookup_project(self, name: str) -> Optional[RegistryMatch]:
        """Fuzzy lookup against known project names."""
        cleaned = self._clean_project_name(name)
        if not cleaned:
            return None

        best_ratio = 0.0
        best_canonical = None
        for known_norm, known_canonical in self._known_projects.items():
            ratio = SequenceMatcher(None, cleaned, known_norm).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_canonical = known_canonical

        if best_canonical and best_ratio >= self._fuzzy_threshold:
            return RegistryMatch(
                key=cleaned,
                canonical_name=best_canonical,
                method='fuzzy_project',
                confidence=best_ratio * 0.85,
            )
        return None

    @staticmethod
    def _clean_project_name(name: str) -> str:
        """Clean a project name for comparison."""
        cleaned = name.strip().lower()
        # Strip common prefixes
        for prefix in ['the ', 'project ', 'proposed ']:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
        # Strip common suffixes
        for suffix in [' project', ' facility', ' plant', ' hub', ' complex']:
            if cleaned.endswith(suffix):
                cleaned = cleaned[:-len(suffix)]
        # Normalize whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned

    # ------------------------------------------------------------------
    # Runtime entity persistence
    # ------------------------------------------------------------------

    def add_runtime_entity(
        self,
        key: str,
        canonical_name: str,
        aliases: Optional[List[str]] = None,
        identifiers: Optional[Dict] = None,
        sectors: Optional[List[str]] = None,
        discovered_from_doc_id: Optional[str] = None,
        discovered_from_source: Optional[str] = None,
    ):
        """Add an entity discovered at runtime.

        Updates in-memory indexes and persists to SQLite if db_path configured.
        """
        data: Dict[str, Any] = {
            'canonical_name': canonical_name,
            'aliases': aliases or [],
            'identifiers': identifiers or {},
            'sectors': sectors or [],
        }
        self._companies[key] = data
        self._build_indexes()

        if self._db_path:
            self._persist_runtime_entity(
                key, canonical_name, aliases, identifiers, sectors,
                discovered_from_doc_id, discovered_from_source,
            )

    def _init_runtime_db(self):
        """Create runtime_entities table if it doesn't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runtime_entities (
                    key TEXT PRIMARY KEY,
                    canonical_name TEXT,
                    aliases_json TEXT,
                    identifiers_json TEXT,
                    sectors_json TEXT,
                    discovered_at TEXT,
                    discovered_from_doc_id TEXT,
                    discovered_from_source TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def _load_runtime_entities(self):
        """Load previously discovered runtime entities from SQLite."""
        if not self._db_path:
            return

        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute("SELECT key, canonical_name, aliases_json, identifiers_json, sectors_json FROM runtime_entities")
            count = 0
            for row in cursor:
                key, canonical_name, aliases_json, identifiers_json, sectors_json = row
                data: Dict[str, Any] = {
                    'canonical_name': canonical_name,
                    'aliases': json.loads(aliases_json) if aliases_json else [],
                    'identifiers': json.loads(identifiers_json) if identifiers_json else {},
                    'sectors': json.loads(sectors_json) if sectors_json else [],
                }
                if key not in self._companies:
                    self._companies[key] = data
                    count += 1

            if count > 0:
                self._build_indexes()
                logger.info("Loaded %d runtime entities from DB", count)
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet
        finally:
            conn.close()

    def _persist_runtime_entity(
        self,
        key: str,
        canonical_name: str,
        aliases: Optional[List[str]],
        identifiers: Optional[Dict],
        sectors: Optional[List[str]],
        discovered_from_doc_id: Optional[str],
        discovered_from_source: Optional[str],
    ):
        """Persist a runtime entity to SQLite."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO runtime_entities
                    (key, canonical_name, aliases_json, identifiers_json, sectors_json,
                     discovered_at, discovered_from_doc_id, discovered_from_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key,
                canonical_name,
                json.dumps(aliases or []),
                json.dumps(identifiers or {}),
                json.dumps(sectors or []),
                datetime.now().isoformat(),
                discovered_from_doc_id,
                discovered_from_source,
            ))
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_suffixes(name: str) -> str:
        """Strip common company name suffixes."""
        cleaned = name.strip()
        cleaned = _COMPANY_SUFFIXES.sub('', cleaned).strip()
        # Handle trailing comma from suffix removal
        cleaned = cleaned.rstrip(',').strip()
        return cleaned

    @property
    def company_count(self) -> int:
        """Number of companies in the registry."""
        return len(self._companies)

    @property
    def company_keys(self) -> List[str]:
        """All company keys."""
        return list(self._companies.keys())
