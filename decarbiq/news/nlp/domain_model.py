"""
Domain Model — Persistent semantic understanding of blue H2/ammonia domain.
===========================================================================

Responsibilities:
1. Load/save domain embeddings to disk
2. Manage positive/negative example centroids
3. Support continuous learning (add new examples)
4. Classify any document type against learned domain

Works alongside SemanticClassifier (Layer 1) but adds:
- Persistence: centroids and examples survive restarts
- Continuous learning: add positive/negative examples at runtime
- Multi-domain: load seed training YAML files for any domain
- Cross-domain triggers: detect when content spans multiple domains
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_SEED_TRAINING_DIR = _HERE.parent.parent.parent / "seed training" / "domains"


class DomainModel:
    """Persistent domain understanding for blue H2/ammonia intelligence.

    Learns from examples, not keywords. Can recognize new terminology
    that is semantically similar to known relevant content.
    """

    def __init__(
        self,
        model_dir: str = "models/domain",
        embedding_model: str = _DEFAULT_MODEL,
    ):
        """Initialize domain model.

        Args:
            model_dir: Directory to persist learned embeddings.
            embedding_model: Sentence transformer model name.
        """
        self._model_dir = Path(model_dir)
        self._embedding_model_name = embedding_model
        self._model = None  # lazy-load

        # Per-domain storage:  domain_name -> DomainState
        self._domains: Dict[str, _DomainState] = {}

        # Cross-domain connection rules (loaded once)
        self._cross_domain_triggers: Dict[str, List[dict]] = {}

        # Entity resolution data from core/entities.yaml
        self._core_entities: Dict = {}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> bool:
        """Load persisted domain model from disk. Returns True if loaded."""
        meta_path = self._model_dir / "meta.json"
        if not meta_path.exists():
            logger.info("No persisted domain model found at %s", self._model_dir)
            return False

        with open(meta_path, "r") as f:
            meta = json.load(f)

        loaded_any = False
        for domain_name in meta.get("domains", []):
            domain_dir = self._model_dir / domain_name
            if not domain_dir.exists():
                continue
            state = _DomainState(domain_name)
            state.load(domain_dir)
            self._domains[domain_name] = state
            loaded_any = True
            logger.info(
                "Loaded domain '%s': %d pos, %d neg examples",
                domain_name,
                len(state.positive_texts),
                len(state.negative_texts),
            )

        if meta.get("cross_domain_triggers"):
            self._cross_domain_triggers = meta["cross_domain_triggers"]

        core_ent_path = self._model_dir / "core_entities.json"
        if core_ent_path.exists():
            with open(core_ent_path, "r") as f:
                self._core_entities = json.load(f)

        return loaded_any

    def save(self) -> None:
        """Persist current domain model state to disk."""
        self._model_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "embedding_model": self._embedding_model_name,
            "domains": list(self._domains.keys()),
            "cross_domain_triggers": self._cross_domain_triggers,
        }
        with open(self._model_dir / "meta.json", "w") as f:
            json.dump(meta, f, indent=2)

        for domain_name, state in self._domains.items():
            domain_dir = self._model_dir / domain_name
            domain_dir.mkdir(parents=True, exist_ok=True)
            state.save(domain_dir)

        if self._core_entities:
            with open(self._model_dir / "core_entities.json", "w") as f:
                json.dump(self._core_entities, f, indent=2)

        logger.info("Saved domain model with %d domains", len(self._domains))

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _load_model(self):
        """Lazy-load sentence-transformers model on first use."""
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer

        logger.info("Loading embedding model: %s", self._embedding_model_name)
        self._model = SentenceTransformer(self._embedding_model_name)

    def _encode(self, text: str) -> np.ndarray:
        """Encode a single text to a normalized embedding."""
        self._load_model()
        return self._model.encode(text, normalize_embeddings=True, show_progress_bar=False)

    def _encode_batch(self, texts: List[str]) -> np.ndarray:
        """Encode a batch of texts. Returns (N, dim) array."""
        self._load_model()
        return self._model.encode(
            texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False
        )

    # ------------------------------------------------------------------
    # Domain ingestion (from seed training YAML)
    # ------------------------------------------------------------------

    def load_domain(self, domain_path: str, seed_dir: Optional[Path] = None) -> None:
        """Load a domain from a seed training YAML file.

        Args:
            domain_path: Slash-separated path, e.g. ``"hydrogen/blue"`` or
                ``"ammonia/green"``.  Resolved under *seed_dir*.
            seed_dir: Root of the seed training domains directory.
                Defaults to ``<project>/seed training/domains``.
        """
        seed_dir = seed_dir or _SEED_TRAINING_DIR
        yaml_path = seed_dir / domain_path / "domain.yaml"

        # Try shared.yaml fallback (e.g. hydrogen/shared.yaml)
        if not yaml_path.exists():
            yaml_path = seed_dir / f"{domain_path}.yaml"
        if not yaml_path.exists():
            raise FileNotFoundError(f"Domain YAML not found: {yaml_path}")

        with open(yaml_path, "r") as f:
            config = yaml.safe_load(f)

        domain_name = config.get("name", domain_path.replace("/", "_"))
        state = self._domains.get(domain_name, _DomainState(domain_name))
        state.definition = config.get("definition", "")

        # Ingest seed examples
        seeds = config.get("seed_examples", {})
        for text in seeds.get("positive", []):
            text = text.strip()
            if text and text not in state.positive_texts:
                state.positive_texts.append(text)
        for text in seeds.get("negative", []):
            text = text.strip()
            if text and text not in state.negative_texts:
                state.negative_texts.append(text)

        # Ingest cross-domain triggers
        triggers = config.get("cross_domain_triggers", {})
        if triggers:
            self._cross_domain_triggers[domain_name] = [
                {"target": target, **info} for target, info in triggers.items()
            ]

        # Rebuild centroids
        self._rebuild_centroids(state)
        self._domains[domain_name] = state
        logger.info(
            "Loaded domain '%s': %d pos, %d neg examples",
            domain_name,
            len(state.positive_texts),
            len(state.negative_texts),
        )

    def load_existing_config(self, config_name: str = "hydrogen_ammonia") -> None:
        """Import examples from an existing ``config/domains/*.yaml`` file.

        This bridges the legacy SemanticClassifier configuration into the
        new persistent DomainModel.
        """
        config_dir = _HERE.parent / "config" / "domains"
        config_path = config_dir / f"{config_name}.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Config not found: {config_path}")

        with open(config_path, "r") as f:
            config = yaml.safe_load(f)

        state = self._domains.get(config_name, _DomainState(config_name))
        for text in config.get("positive_examples", []):
            if text not in state.positive_texts:
                state.positive_texts.append(text)
        for text in config.get("negative_examples", []):
            if text not in state.negative_texts:
                state.negative_texts.append(text)

        state.threshold = config.get("relevance_threshold", 0.55)
        state.borderline_low = config.get("borderline_low", 0.45)
        state.borderline_high = config.get("borderline_high", 0.65)

        self._rebuild_centroids(state)
        self._domains[config_name] = state
        logger.info(
            "Imported existing config '%s': %d pos, %d neg",
            config_name,
            len(state.positive_texts),
            len(state.negative_texts),
        )

    # ------------------------------------------------------------------
    # Full seed training ingestion
    # ------------------------------------------------------------------

    def load_all(self, seed_dir: Optional[Path] = None) -> Dict[str, int]:
        """Auto-discover and load ALL seed training data.

        Scans the seed training directory and loads:

        1. Core concepts and entities (``core/``)
        2. All ``domain.yaml`` and ``shared.yaml`` files
        3. All ``*_seeds.yaml`` seed extract files from docs
        4. Cross-domain connections (``cross_domain/connections.yaml``)
        5. Document manifests with cross-domain triggers

        Encoding and centroid computation happen in a single batch
        pass at the end for efficiency.

        Args:
            seed_dir: Root of ``seed training/domains``.
                Defaults to ``<project>/seed training/domains``.

        Returns:
            Dict mapping domain names to total example counts.
        """
        seed_dir = Path(seed_dir) if seed_dir else _SEED_TRAINING_DIR
        if not seed_dir.exists():
            logger.warning("Seed training dir not found: %s", seed_dir)
            return {}

        # Phase 1: Collect all texts into domain states (no encoding yet)
        self._load_core(seed_dir)
        self._load_all_domain_yamls(seed_dir)
        self._load_all_shared_yamls(seed_dir)
        self._load_all_seed_extracts(seed_dir)
        self._load_cross_domain_connections(seed_dir)
        self._load_all_manifests(seed_dir)

        # Phase 2: Encode and build centroids for all domains
        stats: Dict[str, int] = {}
        for name, state in self._domains.items():
            if state.positive_texts or state.negative_texts:
                self._rebuild_centroids(state)
            total = len(state.positive_texts) + len(state.negative_texts)
            stats[name] = total
            logger.info(
                "Domain '%s': %d pos, %d neg examples",
                name,
                len(state.positive_texts),
                len(state.negative_texts),
            )

        logger.info(
            "load_all() complete: %d domains, %d total examples",
            len(self._domains),
            sum(stats.values()),
        )
        return stats

    # ------------------------------------------------------------------
    # load_all helpers — text collection (no encoding)
    # ------------------------------------------------------------------

    def _add_texts_to_domain(
        self,
        domain_name: str,
        positives: List[str],
        negatives: List[str],
        definition: str = "",
    ) -> None:
        """Add text examples to a domain state without encoding."""
        state = self._domains.get(domain_name, _DomainState(domain_name))
        if definition and not state.definition:
            state.definition = definition

        for text in positives:
            text = str(text).strip()
            if text and text not in state.positive_texts:
                state.positive_texts.append(text)

        for text in negatives:
            text = str(text).strip()
            if text and text not in state.negative_texts:
                state.negative_texts.append(text)

        self._domains[domain_name] = state

    def _load_core(self, seed_dir: Path) -> None:
        """Load ``core/concepts.yaml`` and ``core/entities.yaml``."""
        # --- concepts.yaml: has seed examples (including nested sections) ---
        concepts_path = seed_dir / "core" / "concepts.yaml"
        if concepts_path.exists():
            with open(concepts_path, "r") as f:
                config = yaml.safe_load(f)
            domain_name = config.get("name", "core_concepts")
            seeds = config.get("seed_examples", {})
            positives, negatives = self._extract_seed_texts(seeds)
            self._add_texts_to_domain(
                domain_name, positives, negatives,
                definition=config.get("description", ""),
            )
            logger.info(
                "Loaded core concepts: %d pos, %d neg",
                len(positives), len(negatives),
            )

        # --- entities.yaml: reference data for entity resolution ---
        entities_path = seed_dir / "core" / "entities.yaml"
        if entities_path.exists():
            with open(entities_path, "r") as f:
                try:
                    self._core_entities = yaml.safe_load(f) or {}
                except yaml.YAMLError as e:
                    logger.warning("Failed to parse entities.yaml: %s", e)
                    self._core_entities = {}
            logger.info(
                "Loaded core entities: %d known companies",
                len(self._core_entities.get("known_companies", {})),
            )

    def _load_all_domain_yamls(self, seed_dir: Path) -> None:
        """Discover and load all ``domain.yaml`` files."""
        for yaml_path in sorted(seed_dir.rglob("domain.yaml")):
            rel = yaml_path.parent.relative_to(seed_dir)
            domain_path_str = str(rel)
            try:
                with open(yaml_path, "r") as f:
                    config = yaml.safe_load(f)
                if not config:
                    continue

                domain_name = config.get(
                    "name", domain_path_str.replace("/", "_"),
                )
                seeds = config.get("seed_examples", {})
                positives, negatives = self._extract_seed_texts(seeds)
                self._add_texts_to_domain(
                    domain_name, positives, negatives,
                    definition=config.get("definition", ""),
                )

                # Cross-domain triggers from domain.yaml
                triggers = config.get("cross_domain_triggers", {})
                if triggers:
                    self._cross_domain_triggers[domain_name] = [
                        {"target": target, **info}
                        for target, info in triggers.items()
                        if isinstance(info, dict)
                    ]
            except Exception as e:
                logger.warning("Failed to load %s: %s", yaml_path, e)

    def _load_all_shared_yamls(self, seed_dir: Path) -> None:
        """Discover and load all ``shared.yaml`` files.

        Shared files are merged into every child domain under the same
        parent directory.  E.g. ``hydrogen/shared.yaml`` feeds into
        ``hydrogen_blue``, ``hydrogen_green``, etc.
        """
        for yaml_path in sorted(seed_dir.rglob("shared.yaml")):
            try:
                with open(yaml_path, "r") as f:
                    config = yaml.safe_load(f)
                if not config:
                    continue

                seeds = config.get("seed_examples", {})
                positives, negatives = self._extract_seed_texts(seeds)
                if not positives and not negatives:
                    continue

                parent_dir = yaml_path.parent.name  # e.g. "hydrogen"

                # Find child domains to merge into
                child_domains = [
                    name for name in self._domains
                    if name.startswith(parent_dir + "_")
                    or name.startswith(parent_dir)
                ]

                if not child_domains:
                    # No child domains yet — create one from the shared file
                    domain_name = config.get(
                        "name", parent_dir + "_shared",
                    )
                    child_domains = [domain_name]

                for domain_name in child_domains:
                    self._add_texts_to_domain(
                        domain_name, positives, negatives,
                    )
                logger.info(
                    "Merged shared.yaml into %d domains: %s",
                    len(child_domains), child_domains,
                )
            except Exception as e:
                logger.warning("Failed to load %s: %s", yaml_path, e)

    def _load_all_seed_extracts(self, seed_dir: Path) -> None:
        """Discover and load all ``*_seeds.yaml`` files from docs."""
        for seeds_path in sorted(seed_dir.rglob("*_seeds.yaml")):
            try:
                with open(seeds_path, "r") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue

                positives, negatives = self._extract_seed_file_texts(data)
                if not positives and not negatives:
                    continue

                domain_name = self._resolve_domain_for_path(
                    seeds_path, seed_dir,
                )
                self._add_texts_to_domain(domain_name, positives, negatives)
                logger.debug(
                    "Loaded seeds %s → domain '%s': %d pos, %d neg",
                    seeds_path.name, domain_name,
                    len(positives), len(negatives),
                )
            except Exception as e:
                logger.warning("Failed to load %s: %s", seeds_path, e)

    def _load_cross_domain_connections(self, seed_dir: Path) -> None:
        """Load ``cross_domain/connections.yaml``."""
        conn_path = seed_dir / "cross_domain" / "connections.yaml"
        if not conn_path.exists():
            return

        with open(conn_path, "r") as f:
            data = yaml.safe_load(f)
        if not data:
            return

        for rel_name, rel_data in data.get("relationships", {}).items():
            if not isinstance(rel_data, dict):
                continue
            to_domain = rel_data.get("to_domain", "")
            patterns = rel_data.get("trigger_patterns", [])
            if to_domain and patterns:
                # Normalize domain name (e.g. "hydrogen/blue" → "hydrogen_blue")
                from_domain = rel_data.get("from_domain", "").replace("/", "_")
                to_norm = to_domain.replace("/", "_")
                trigger = {
                    "target": to_norm,
                    "patterns": patterns,
                    "relationship": rel_data.get("relationship_type", ""),
                    "action": rel_data.get("description", ""),
                }
                self._cross_domain_triggers.setdefault(
                    from_domain, [],
                ).append(trigger)

        # Propagation rules
        for rule_name, rule_data in data.get("propagation_rules", {}).items():
            if not isinstance(rule_data, dict):
                continue
            for impact in rule_data.get("impacts", []):
                domain = impact.get("domain", "").replace("/", "_")
                if domain:
                    trigger = {
                        "target": domain,
                        "patterns": [rule_data.get("trigger", "")],
                        "relationship": "propagation",
                        "action": impact.get("effect", ""),
                    }
                    self._cross_domain_triggers.setdefault(
                        rule_name, [],
                    ).append(trigger)

        logger.info(
            "Loaded cross-domain connections: %d relationship groups",
            len(data.get("relationships", {})),
        )

    def _load_all_manifests(self, seed_dir: Path) -> None:
        """Load ``manifest.yaml`` files for cross-domain trigger enrichment."""
        for manifest_path in sorted(seed_dir.rglob("manifest.yaml")):
            try:
                with open(manifest_path, "r") as f:
                    data = yaml.safe_load(f)
                if not data:
                    continue

                for doc in data.get("documents", []):
                    if not isinstance(doc, dict):
                        continue
                    triggers = doc.get("cross_domain_triggers", {})
                    doc_id = doc.get("id", "")
                    for target_domain, patterns in triggers.items():
                        if not isinstance(patterns, list):
                            continue
                        target_norm = target_domain.replace("/", "_")
                        trigger = {
                            "target": target_norm,
                            "patterns": patterns,
                            "relationship": "document_reference",
                            "action": f"From document: {doc_id}",
                        }
                        self._cross_domain_triggers.setdefault(
                            doc_id, [],
                        ).append(trigger)
            except Exception as e:
                logger.warning("Failed to load %s: %s", manifest_path, e)

    def _resolve_domain_for_path(self, file_path: Path, seed_dir: Path) -> str:
        """Determine which domain a seed file belongs to based on path."""
        try:
            rel = file_path.relative_to(seed_dir)
        except ValueError:
            return "unknown"

        parts = list(rel.parts)
        if not parts:
            return "unknown"

        top_level = parts[0]  # e.g. "ammonia", "hydrogen", "carbon"

        # Find loaded domains matching this top-level directory
        candidates = [
            name for name in self._domains
            if name.startswith(top_level + "_") or name == top_level
        ]

        if candidates:
            # Prefer "blue" variant (primary production domain)
            for c in candidates:
                if "blue" in c:
                    return c
            return candidates[0]

        # No matching domain loaded — use top_level as domain name
        return top_level

    @staticmethod
    def _extract_seed_texts(seeds_data: dict) -> tuple:
        """Extract positive/negative texts from ``seed_examples`` structure.

        Handles both flat format (``positive: [...]``) and nested sections
        (e.g. ``commercial_finance: {positive: [...], negative: [...]}``)
        as found in ``core/concepts.yaml``.
        """
        positives: List[str] = []
        negatives: List[str] = []

        for key, value in seeds_data.items():
            key_lower = str(key).lower()
            if key_lower == "positive" and isinstance(value, list):
                positives.extend(str(t).strip() for t in value if t)
            elif key_lower == "negative" and isinstance(value, list):
                negatives.extend(str(t).strip() for t in value if t)
            elif isinstance(value, dict):
                # Nested section (e.g. commercial_finance, regulatory_safety)
                for sub_key, sub_val in value.items():
                    sub_lower = str(sub_key).lower()
                    if "positive" in sub_lower and isinstance(sub_val, list):
                        positives.extend(
                            str(t).strip() for t in sub_val if t
                        )
                    elif "negative" in sub_lower and isinstance(sub_val, list):
                        negatives.extend(
                            str(t).strip() for t in sub_val if t
                        )

        return positives, negatives

    @staticmethod
    def _extract_seed_file_texts(data: dict) -> tuple:
        """Extract texts from ``*_seeds.yaml`` format.

        These files use ``positive_examples: [{text: ...}]`` format
        (list of dicts with ``text`` key) rather than flat string lists.
        """
        positives: List[str] = []
        negatives: List[str] = []

        for ex in data.get("positive_examples", []):
            if isinstance(ex, dict):
                text = str(ex.get("text", "")).strip()
            else:
                text = str(ex).strip()
            if text:
                positives.append(text)

        for ex in data.get("negative_examples", []):
            if isinstance(ex, dict):
                text = str(ex.get("text", "")).strip()
            else:
                text = str(ex).strip()
            if text:
                negatives.append(text)

        return positives, negatives

    # ------------------------------------------------------------------
    # Continuous learning
    # ------------------------------------------------------------------

    def train_from_examples(
        self,
        positive_examples: List[str],
        negative_examples: Optional[List[str]] = None,
        domain_name: str = "hydrogen_ammonia",
    ) -> None:
        """Train (or extend) a domain from explicit example lists.

        This is the quickest way to bootstrap a domain without a YAML file.

        Args:
            positive_examples: Texts that ARE relevant.
            negative_examples: Texts that are NOT relevant.
            domain_name: Domain to store examples under.
        """
        state = self._domains.get(domain_name, _DomainState(domain_name))
        for text in positive_examples:
            if text not in state.positive_texts:
                state.positive_texts.append(text)
        for text in (negative_examples or []):
            if text not in state.negative_texts:
                state.negative_texts.append(text)

        self._rebuild_centroids(state)
        self._domains[domain_name] = state
        logger.info(
            "Trained domain '%s': %d pos, %d neg examples",
            domain_name,
            len(state.positive_texts),
            len(state.negative_texts),
        )

    def add_feedback(
        self, text: str, is_relevant: bool, document_type: str = "news"
    ) -> None:
        """Forward user feedback for continuous learning.

        Convenience wrapper used by :class:`SemanticClassifier`.  Routes the
        feedback to the first loaded domain (or ``"hydrogen_ammonia"``).
        """
        domain_name = self.domain_names[0] if self.domain_names else "hydrogen_ammonia"
        self.add_example(domain_name, text, positive=is_relevant)
        logger.info(
            "Feedback recorded: %s example for '%s' (doc_type=%s)",
            "positive" if is_relevant else "negative",
            domain_name,
            document_type,
        )

    def add_example(
        self, domain_name: str, text: str, positive: bool = True
    ) -> None:
        """Add a positive or negative example to a domain at runtime.

        Recomputes the corresponding centroid incrementally.
        """
        if domain_name not in self._domains:
            self._domains[domain_name] = _DomainState(domain_name)
        state = self._domains[domain_name]

        target = state.positive_texts if positive else state.negative_texts
        if text in target:
            return  # duplicate

        target.append(text)
        emb = self._encode(text)

        if positive:
            state.positive_embeddings.append(emb)
            state.positive_centroid = _recompute_centroid(state.positive_embeddings)
        else:
            state.negative_embeddings.append(emb)
            state.negative_centroid = _recompute_centroid(state.negative_embeddings)

        logger.debug(
            "Added %s example to '%s' (%d total)",
            "positive" if positive else "negative",
            domain_name,
            len(target),
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(
        self,
        text: str,
        domain_name: Optional[str] = None,
    ) -> Dict:
        """Classify *text* against one or all loaded domains.

        Args:
            text: Document content (title, snippet, full text, filing excerpt, etc.)
            domain_name: If given, score against this domain only.
                Otherwise score against every loaded domain and return the best match.

        Returns:
            dict with keys ``relevant``, ``score``, ``domain``, ``embedding``,
            and optionally ``cross_domain_triggers``.
        """
        emb = self._encode(text)

        if domain_name:
            domains_to_check = {domain_name: self._domains[domain_name]}
        else:
            domains_to_check = self._domains

        best: Optional[Dict] = None
        for name, state in domains_to_check.items():
            score = self._score(emb, state)
            relevant = score >= state.threshold
            result = {
                "relevant": relevant,
                "score": round(score, 4),
                "domain": name,
                "embedding": emb,
            }
            if best is None or score > best["score"]:
                best = result

        if best is None:
            return {"relevant": False, "score": 0.0, "domain": None, "embedding": emb}

        # Check cross-domain triggers
        triggers_fired = self._check_cross_domain(text, best["domain"])
        if triggers_fired:
            best["cross_domain_triggers"] = triggers_fired

        return best

    def classify_batch(
        self,
        texts: List[str],
        domain_name: Optional[str] = None,
    ) -> List[Dict]:
        """Classify a batch of texts efficiently.

        Same semantics as :meth:`classify` but encodes all texts in a single
        batch pass.
        """
        embeddings = self._encode_batch(texts)

        if domain_name:
            domains_to_check = {domain_name: self._domains[domain_name]}
        else:
            domains_to_check = self._domains

        results: List[Dict] = []
        for emb, text in zip(embeddings, texts):
            best: Optional[Dict] = None
            for name, state in domains_to_check.items():
                score = self._score(emb, state)
                relevant = score >= state.threshold
                result = {
                    "relevant": relevant,
                    "score": round(score, 4),
                    "domain": name,
                    "embedding": emb,
                }
                if best is None or score > best["score"]:
                    best = result
            if best is None:
                best = {"relevant": False, "score": 0.0, "domain": None, "embedding": emb}

            triggers_fired = self._check_cross_domain(text, best["domain"])
            if triggers_fired:
                best["cross_domain_triggers"] = triggers_fired

            results.append(best)

        return results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _score(self, emb: np.ndarray, state: "_DomainState") -> float:
        """Score an embedding against a domain's centroids."""
        if state.positive_centroid is None:
            return 0.5

        pos_score = float(np.dot(emb, state.positive_centroid))
        score = pos_score

        if state.negative_centroid is not None:
            neg_score = float(np.dot(emb, state.negative_centroid))
            score = pos_score - 0.3 * neg_score
            score = max(0.0, min(1.0, score))

        return score

    def _rebuild_centroids(self, state: "_DomainState") -> None:
        """(Re-)compute centroids from the full text lists."""
        if state.positive_texts:
            embeddings = self._encode_batch(state.positive_texts)
            state.positive_embeddings = list(embeddings)
            state.positive_centroid = _recompute_centroid(state.positive_embeddings)
        if state.negative_texts:
            embeddings = self._encode_batch(state.negative_texts)
            state.negative_embeddings = list(embeddings)
            state.negative_centroid = _recompute_centroid(state.negative_embeddings)

    def _check_cross_domain(
        self, text: str, source_domain: Optional[str]
    ) -> List[dict]:
        """Return any cross-domain triggers that fire for *text*."""
        if not source_domain or source_domain not in self._cross_domain_triggers:
            return []

        text_lower = text.lower()
        fired: List[dict] = []
        for trigger in self._cross_domain_triggers[source_domain]:
            patterns = trigger.get("patterns", [])
            for pattern in patterns:
                # Patterns use "+" as AND (e.g. "hydrogen + ammonia")
                parts = [p.strip().lower() for p in pattern.split("+")]
                if all(part in text_lower for part in parts):
                    fired.append(
                        {
                            "target_domain": trigger.get("target"),
                            "pattern": pattern,
                            "action": trigger.get("action", ""),
                        }
                    )
                    break  # one match per trigger is enough

        return fired

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def domain_names(self) -> List[str]:
        """List of loaded domain names."""
        return list(self._domains.keys())

    def domain_stats(self, domain_name: str) -> Dict:
        """Return stats for a specific domain."""
        state = self._domains[domain_name]
        return {
            "name": domain_name,
            "positive_examples": len(state.positive_texts),
            "negative_examples": len(state.negative_texts),
            "has_positive_centroid": state.positive_centroid is not None,
            "has_negative_centroid": state.negative_centroid is not None,
            "threshold": state.threshold,
        }

    @property
    def core_entities(self) -> Dict:
        """Known entities loaded from ``core/entities.yaml``.

        Returns raw dict with ``known_companies``, ``locations``,
        ``agencies``, etc.  Useful for entity resolution.
        """
        return self._core_entities


# ======================================================================
# Internal helpers
# ======================================================================


class _DomainState:
    """Mutable state for a single domain."""

    def __init__(self, name: str):
        self.name = name
        self.definition: str = ""
        self.positive_texts: List[str] = []
        self.negative_texts: List[str] = []
        self.positive_embeddings: List[np.ndarray] = []
        self.negative_embeddings: List[np.ndarray] = []
        self.positive_centroid: Optional[np.ndarray] = None
        self.negative_centroid: Optional[np.ndarray] = None
        self.threshold: float = 0.55
        self.borderline_low: float = 0.45
        self.borderline_high: float = 0.65

    def save(self, directory: Path) -> None:
        """Persist state to *directory*."""
        directory.mkdir(parents=True, exist_ok=True)

        meta = {
            "name": self.name,
            "definition": self.definition,
            "threshold": self.threshold,
            "borderline_low": self.borderline_low,
            "borderline_high": self.borderline_high,
            "positive_count": len(self.positive_texts),
            "negative_count": len(self.negative_texts),
        }
        with open(directory / "state.json", "w") as f:
            json.dump(meta, f, indent=2)

        with open(directory / "positive_texts.json", "w") as f:
            json.dump(self.positive_texts, f, indent=2)
        with open(directory / "negative_texts.json", "w") as f:
            json.dump(self.negative_texts, f, indent=2)

        if self.positive_centroid is not None:
            np.save(directory / "positive_centroid.npy", self.positive_centroid)
        if self.negative_centroid is not None:
            np.save(directory / "negative_centroid.npy", self.negative_centroid)
        if self.positive_embeddings:
            np.save(directory / "positive_embeddings.npy", np.array(self.positive_embeddings))
        if self.negative_embeddings:
            np.save(directory / "negative_embeddings.npy", np.array(self.negative_embeddings))

    def load(self, directory: Path) -> None:
        """Restore state from *directory*."""
        state_path = directory / "state.json"
        if state_path.exists():
            with open(state_path, "r") as f:
                meta = json.load(f)
            self.definition = meta.get("definition", "")
            self.threshold = meta.get("threshold", 0.55)
            self.borderline_low = meta.get("borderline_low", 0.45)
            self.borderline_high = meta.get("borderline_high", 0.65)

        pos_path = directory / "positive_texts.json"
        if pos_path.exists():
            with open(pos_path, "r") as f:
                self.positive_texts = json.load(f)

        neg_path = directory / "negative_texts.json"
        if neg_path.exists():
            with open(neg_path, "r") as f:
                self.negative_texts = json.load(f)

        pos_centroid_path = directory / "positive_centroid.npy"
        if pos_centroid_path.exists():
            self.positive_centroid = np.load(pos_centroid_path)

        neg_centroid_path = directory / "negative_centroid.npy"
        if neg_centroid_path.exists():
            self.negative_centroid = np.load(neg_centroid_path)

        pos_emb_path = directory / "positive_embeddings.npy"
        if pos_emb_path.exists():
            self.positive_embeddings = list(np.load(pos_emb_path))

        neg_emb_path = directory / "negative_embeddings.npy"
        if neg_emb_path.exists():
            self.negative_embeddings = list(np.load(neg_emb_path))


def _recompute_centroid(embeddings: List[np.ndarray]) -> Optional[np.ndarray]:
    """Compute a unit-norm centroid from a list of embeddings."""
    if not embeddings:
        return None
    centroid = np.mean(embeddings, axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid /= norm
    return centroid


# ======================================================================
# Module-level singleton
# ======================================================================

_instance: Optional[DomainModel] = None


def get_domain_model(
    model_dir: str = "models/domain",
    auto_load: bool = True,
) -> DomainModel:
    """Get or create the module-level DomainModel singleton.

    On first call, attempts to load persisted state from *model_dir*.
    """
    global _instance
    if _instance is None:
        _instance = DomainModel(model_dir=model_dir)
        if auto_load:
            _instance.load()
    return _instance
