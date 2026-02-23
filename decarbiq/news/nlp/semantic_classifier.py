"""
Layer 1 — Semantic Embedding Classifier
========================================
Uses sentence-transformers (all-MiniLM-L6-v2, ~80MB) to classify articles
by cosine similarity to a pre-built domain centroid.

Now delegates embedding and scoring to :class:`DomainModel` for persistent,
continuously-learning domain understanding while keeping the original API
intact for all existing callers.

Performance: ~5ms per article on CPU.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import yaml

from .domain_model import DomainModel

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_CONFIG_DIR = _HERE.parent / 'config' / 'domains'
_DEFAULT_MODEL = 'all-MiniLM-L6-v2'


class SemanticClassifier:
    """Embedding-based article relevance classifier.

    Under the hood, classification and embedding are delegated to a
    :class:`DomainModel` instance.  All original public methods remain
    unchanged so existing callers (``blue_h2_news_collector``,
    ``article_store``, etc.) continue to work unmodified.
    """

    def __init__(self, domain_config: str = 'hydrogen_ammonia',
                 model_name: str = _DEFAULT_MODEL,
                 domain_model: Optional[DomainModel] = None):
        self._model_name = model_name
        self._domain_config = domain_config

        # Thresholds (read from config, may be overridden by DomainModel)
        self._threshold: float = 0.55
        self._borderline_low: float = 0.45
        self._borderline_high: float = 0.65

        # Load YAML config (still needed for entity/relation types used by
        # other pipeline layers)
        config_path = _CONFIG_DIR / f'{domain_config}.yaml'
        if config_path.exists():
            with open(config_path, 'r') as f:
                self._config = yaml.safe_load(f)
            self._threshold = self._config.get('relevance_threshold', 0.55)
            self._borderline_low = self._config.get('borderline_low', 0.45)
            self._borderline_high = self._config.get('borderline_high', 0.65)
        else:
            logger.warning(f"Domain config not found: {config_path}")
            self._config = {}

        # --- DomainModel integration ---
        if domain_model is not None:
            self.domain_model = domain_model
        else:
            self.domain_model = DomainModel(
                model_dir='models/domain',
                embedding_model=model_name,
            )
            # Try to load persisted model first
            if not self.domain_model.load():
                # No persisted model — bootstrap from existing YAML config
                if config_path.exists():
                    self.domain_model.load_existing_config(domain_config)

    # ------------------------------------------------------------------
    # Lazy model access (delegates to DomainModel)
    # ------------------------------------------------------------------

    def _load_model(self):
        """Ensure the underlying embedding model is loaded."""
        # DomainModel lazy-loads internally; this is a no-op compatibility shim
        # that triggers the load so _score_embedding can work.
        self.domain_model._load_model()

    # ------------------------------------------------------------------
    # Embedding (public API — unchanged)
    # ------------------------------------------------------------------

    def embed(self, text: str) -> np.ndarray:
        """Encode text to normalized embedding vector."""
        return self.domain_model._encode(text)

    def embed_batch(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        """Encode a batch of texts. Returns (N, dim) array."""
        return self.domain_model._encode_batch(texts)

    # ------------------------------------------------------------------
    # Scoring (delegates to DomainModel centroids)
    # ------------------------------------------------------------------

    def _score_embedding(self, emb: np.ndarray) -> float:
        """Compute relevance score from embedding vs domain centroids."""
        domain_name = self._domain_config
        if domain_name in self.domain_model._domains:
            state = self.domain_model._domains[domain_name]
            return self.domain_model._score(emb, state)
        # Fallback: no domain loaded yet
        return 0.5

    # ------------------------------------------------------------------
    # Article classification (public API — unchanged)
    # ------------------------------------------------------------------

    def classify_article(self, title: str, snippet: str = '',
                         full_text: str = '',
                         document_type: str = 'news') -> Dict:
        """Classify a single article for domain relevance.

        The returned embedding is always from title+snippet (never full_text)
        so all embeddings in ChromaDB are comparable.

        Args:
            title: Article title.
            snippet: Article snippet/description.
            full_text: Full article body (used only for borderline rescoring).
            document_type: Document type hint (news, sec_filing, epa_permit,
                doe_award).  Currently used for logging; may drive
                type-specific adjustments in the future.

        Returns:
            {
                'relevant': bool,
                'score': float (0-1),
                'embedding': np.ndarray,
            }
        """
        self._load_model()

        # Build text: title weighted by repetition
        text = f"{title}. {title}. {snippet}"
        embedding = self.embed(text)
        score = self._score_embedding(embedding)
        relevant = score >= self._threshold

        # Borderline rescoring: use full_text to refine SCORE only,
        # but keep the original embedding for ChromaDB consistency (C1 fix)
        if (self._borderline_low <= score <= self._borderline_high
                and full_text):
            extended = f"{title}. {snippet}. {full_text[:1000]}"
            ext_emb = self.embed(extended)
            score = self._score_embedding(ext_emb)
            relevant = score >= self._threshold
            # embedding stays as the title+snippet version

        return {
            'relevant': relevant,
            'score': score,
            'embedding': embedding,
        }

    def classify_batch(self, articles: List[dict],
                       document_type: str = 'news') -> List[Dict]:
        """Classify a batch of articles efficiently.

        Each article dict should have 'title' and optionally 'snippet'.
        Performs borderline rescoring for articles in the 0.45-0.65 range
        that have 'full_text' or 'snippet' available.

        Args:
            articles: List of article dicts with 'title', 'snippet', 'full_text'.
            document_type: Document type hint for all articles in the batch.

        Returns list of classification results (same order).
        """
        self._load_model()

        # Build texts for batch encoding
        texts = []
        for art in articles:
            title = art.get('title', '')
            snippet = art.get('snippet', '')
            texts.append(f"{title}. {title}. {snippet}")

        embeddings = self.embed_batch(texts)

        # First pass: score all
        scores = []
        for emb in embeddings:
            scores.append(self._score_embedding(emb))

        # Borderline rescoring pass (C2 fix): collect borderline indices
        borderline_indices = []
        borderline_texts = []
        for i, (score, art) in enumerate(zip(scores, articles)):
            if self._borderline_low <= score <= self._borderline_high:
                full_text = art.get('full_text', '') or art.get('snippet', '')
                if full_text:
                    title = art.get('title', '')
                    snippet = art.get('snippet', '')
                    borderline_texts.append(
                        f"{title}. {snippet}. {full_text[:1000]}")
                    borderline_indices.append(i)

        if borderline_texts:
            rescore_embeddings = self.embed_batch(borderline_texts)
            for j, idx in enumerate(borderline_indices):
                scores[idx] = self._score_embedding(rescore_embeddings[j])

        # Build results — embeddings are always from title+snippet (consistent)
        results = []
        for i, (emb, score) in enumerate(zip(embeddings, scores)):
            results.append({
                'relevant': score >= self._threshold,
                'score': score,
                'embedding': emb,
            })

        return results

    # ------------------------------------------------------------------
    # Continuous learning (NEW)
    # ------------------------------------------------------------------

    def add_feedback(self, text: str, is_relevant: bool,
                     document_type: str = 'news') -> None:
        """Forward feedback to domain model for continuous learning."""
        self.domain_model.add_feedback(text, is_relevant, document_type)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: Optional[SemanticClassifier] = None


def get_classifier(domain: str = 'hydrogen_ammonia') -> SemanticClassifier:
    """Get or create the module-level SemanticClassifier singleton.

    If called with a different domain than the existing instance,
    creates a new instance for that domain.
    """
    global _instance
    if _instance is None or _instance._domain_config != domain:
        _instance = SemanticClassifier(domain_config=domain)
    return _instance
