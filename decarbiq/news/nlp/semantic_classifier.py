"""
Layer 1 — Semantic Embedding Classifier
========================================
Uses sentence-transformers (all-MiniLM-L6-v2, ~80MB) to classify articles
by cosine similarity to a pre-built domain centroid.

Performance: ~5ms per article on CPU.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import yaml

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_CONFIG_DIR = _HERE.parent / 'config' / 'domains'
_DEFAULT_MODEL = 'all-MiniLM-L6-v2'


class SemanticClassifier:
    """Embedding-based article relevance classifier."""

    def __init__(self, domain_config: str = 'hydrogen_ammonia',
                 model_name: str = _DEFAULT_MODEL):
        self._model_name = model_name
        self._domain_config = domain_config
        self._model = None  # lazy load
        self._positive_centroid: Optional[np.ndarray] = None
        self._negative_centroid: Optional[np.ndarray] = None
        self._threshold: float = 0.55
        self._borderline_low: float = 0.45
        self._borderline_high: float = 0.65

        # Load config
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

    def _load_model(self):
        """Lazy-load sentence-transformers model on first use."""
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {self._model_name}")
        self._model = SentenceTransformer(self._model_name)
        self._build_centroids()

    def _build_centroids(self):
        """Compute domain centroids from config examples."""
        pos_examples = self._config.get('positive_examples', [])
        neg_examples = self._config.get('negative_examples', [])

        if pos_examples:
            pos_embeddings = self._model.encode(pos_examples,
                                                 normalize_embeddings=True,
                                                 show_progress_bar=False)
            self._positive_centroid = np.mean(pos_embeddings, axis=0)
            norm = np.linalg.norm(self._positive_centroid)
            if norm > 0:
                self._positive_centroid /= norm
            logger.info(f"Built positive centroid from {len(pos_examples)} examples")

        if neg_examples:
            neg_embeddings = self._model.encode(neg_examples,
                                                 normalize_embeddings=True,
                                                 show_progress_bar=False)
            self._negative_centroid = np.mean(neg_embeddings, axis=0)
            norm = np.linalg.norm(self._negative_centroid)
            if norm > 0:
                self._negative_centroid /= norm
            logger.info(f"Built negative centroid from {len(neg_examples)} examples")

    def _score_embedding(self, emb: np.ndarray) -> float:
        """Compute relevance score from embedding vs centroids."""
        if self._positive_centroid is not None:
            pos_score = float(np.dot(emb, self._positive_centroid))
        else:
            return 0.5
        score = pos_score
        if self._negative_centroid is not None:
            neg_score = float(np.dot(emb, self._negative_centroid))
            score = pos_score - 0.3 * neg_score
            score = max(0.0, min(1.0, score))
        return score

    def embed(self, text: str) -> np.ndarray:
        """Encode text to normalized embedding vector."""
        self._load_model()
        return self._model.encode(text, normalize_embeddings=True,
                                   show_progress_bar=False)

    def embed_batch(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        """Encode a batch of texts. Returns (N, dim) array."""
        self._load_model()
        return self._model.encode(texts, normalize_embeddings=True,
                                   batch_size=batch_size,
                                   show_progress_bar=False)

    def classify_article(self, title: str, snippet: str = '',
                         full_text: str = '') -> Dict:
        """Classify a single article for domain relevance.

        The returned embedding is always from title+snippet (never full_text)
        so all embeddings in ChromaDB are comparable.

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

    def classify_batch(self, articles: List[dict]) -> List[Dict]:
        """Classify a batch of articles efficiently.

        Each article dict should have 'title' and optionally 'snippet'.
        Performs borderline rescoring for articles in the 0.45-0.65 range
        that have 'full_text' or 'snippet' available.

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
