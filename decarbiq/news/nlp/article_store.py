"""
Layer 4 — ChromaDB Vector Store
================================
Provides semantic deduplication, vector search, and persistent storage
for article embeddings + metadata.

Uses ChromaDB PersistentClient for disk-based storage.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_DEFAULT_DB_PATH = str(_HERE.parent / 'chroma_store')


def _url_hash_sha256(url: str) -> str:
    """SHA256 hash of URL — matches the collector's _url_hash function (C7 fix)."""
    return hashlib.sha256(url.encode()).hexdigest()


class ArticleStore:
    """ChromaDB wrapper for article storage, dedup, and semantic search."""

    def __init__(self, persist_dir: str = _DEFAULT_DB_PATH,
                 collection_name: str = 'hydrogen_articles'):
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._client = None
        self._collection = None

    def _init_client(self):
        """Lazy-initialize ChromaDB client and collection."""
        if self._client is not None:
            return
        import chromadb
        logger.info(f"Initializing ChromaDB at: {self._persist_dir}")
        self._client = chromadb.PersistentClient(path=self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"ChromaDB collection '{self._collection_name}': "
                     f"{self._collection.count()} documents")

    def is_url_duplicate(self, url: str) -> bool:
        """Check if URL already exists in the store (exact match)."""
        self._init_client()
        url_hash = _url_hash_sha256(url)
        try:
            results = self._collection.get(ids=[url_hash])
            return len(results['ids']) > 0
        except Exception:
            return False

    def find_near_duplicate(self, embedding: np.ndarray,
                            threshold: float = 0.95) -> Optional[dict]:
        """Check if a near-duplicate exists and return its metadata.

        Consolidates is_duplicate + find_similar into one method (I9 fix).

        Args:
            embedding: Article embedding vector
            threshold: Cosine similarity threshold (0.95 = very similar)

        Returns:
            Metadata dict with 'similarity' if duplicate found, else None.
        """
        self._init_client()
        if self._collection.count() == 0:
            return None

        try:
            results = self._collection.query(
                query_embeddings=[embedding.tolist()],
                n_results=1,
                include=['metadatas', 'distances']
            )
            if results['distances'] and results['distances'][0]:
                distance = results['distances'][0][0]
                similarity = 1.0 - distance
                if similarity >= threshold:
                    meta = (results['metadatas'][0][0]
                            if results.get('metadatas') and results['metadatas'][0]
                            else {})
                    meta['similarity'] = similarity
                    return meta
        except Exception as e:
            logger.warning(f"Dedup query error: {e}")
        return None

    def is_duplicate(self, embedding: np.ndarray,
                     threshold: float = 0.95) -> bool:
        """Check if an article with similar embedding already exists."""
        return self.find_near_duplicate(embedding, threshold) is not None

    def batch_dedup(self, articles: List[dict],
                    threshold: float = 0.95) -> List[dict]:
        """Batch semantic dedup: filter out articles already in the store.

        C6 fix: uses a single batch query instead of N+1 individual queries.

        Each article dict must have 'url' and 'embedding'.
        Returns list of non-duplicate articles.
        """
        self._init_client()
        if not articles or self._collection.count() == 0:
            return list(articles)

        # 1. URL dedup (batch get)
        url_hashes = [_url_hash_sha256(a.get('url', '')) for a in articles]
        try:
            existing = self._collection.get(ids=url_hashes)
            existing_ids = set(existing['ids']) if existing['ids'] else set()
        except Exception:
            existing_ids = set()

        # 2. Semantic dedup (batch query)
        candidates = []
        candidate_indices = []
        for i, art in enumerate(articles):
            url_hash = url_hashes[i]
            if url_hash in existing_ids:
                continue  # URL already exists
            emb = art.get('embedding')
            if emb is not None:
                candidates.append(emb.tolist() if isinstance(emb, np.ndarray)
                                  else emb)
                candidate_indices.append(i)

        # No candidates survive URL dedup
        if not candidates:
            url_dups = len(articles) - len(candidate_indices)
            if url_dups:
                logger.info(f"Batch dedup: all {url_dups} articles were URL duplicates")
            return []

        # Batch cosine query
        deduped_indices = set(candidate_indices)
        try:
            results = self._collection.query(
                query_embeddings=candidates,
                n_results=1,
                include=['distances']
            )
            for j, idx in enumerate(candidate_indices):
                if (results['distances'] and results['distances'][j]
                        and results['distances'][j]):
                    distance = results['distances'][j][0]
                    similarity = 1.0 - distance
                    if similarity >= threshold:
                        deduped_indices.discard(idx)
        except Exception as e:
            logger.warning(f"Batch dedup query error: {e}")

        return [articles[i] for i in sorted(deduped_indices)]

    def add_article(self, url: str, embedding: np.ndarray,
                    metadata: Optional[dict] = None):
        """Add a single article to the store."""
        self._init_client()
        url_hash = _url_hash_sha256(url)
        meta = metadata or {}
        clean_meta = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean_meta[k] = v
            elif v is not None:
                clean_meta[k] = str(v)

        try:
            self._collection.upsert(
                ids=[url_hash],
                embeddings=[embedding.tolist()],
                metadatas=[clean_meta],
                documents=[meta.get('title', '')]
            )
        except Exception as e:
            logger.warning(f"ChromaDB add error: {e}")

    def add_batch(self, articles: List[dict]):
        """Bulk insert articles into ChromaDB.

        Each article dict should have:
            - 'url': str
            - 'embedding': np.ndarray
            - 'title': str (optional)
            - any other metadata fields

        I7 fix: logs warnings for skipped articles.
        """
        self._init_client()
        if not articles:
            return

        ids = []
        embeddings = []
        metadatas = []
        documents = []
        skipped = 0

        for art in articles:
            url = art.get('url', '')
            emb = art.get('embedding')
            if not url or emb is None:
                skipped += 1
                continue

            url_hash = _url_hash_sha256(url)
            ids.append(url_hash)
            embeddings.append(emb.tolist() if isinstance(emb, np.ndarray)
                              else emb)

            meta = {}
            for k in ['title', 'source', 'priority_score', 'category',
                       'region', 'published', 'relevance_score']:
                v = art.get(k)
                if v is not None:
                    meta[k] = v if isinstance(v, (str, int, float, bool)) \
                        else str(v)
            metadatas.append(meta)
            documents.append(art.get('title', ''))

        if skipped:
            logger.warning(f"add_batch: skipped {skipped} articles "
                           f"(missing url or embedding)")

        if ids:
            try:
                self._collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    documents=documents
                )
                logger.info(f"Added {len(ids)} articles to ChromaDB")
            except Exception as e:
                logger.warning(f"ChromaDB batch add error: {e}")

    def search(self, query_embedding: Optional[np.ndarray] = None,
               query_text: Optional[str] = None,
               filters: Optional[dict] = None,
               n_results: int = 10) -> List[dict]:
        """Semantic search across stored articles.

        I8 fix: query_text path uses the same sentence-transformers model
        that populated the collection instead of ChromaDB's default embedder.

        Args:
            query_embedding: Pre-computed embedding vector
            query_text: Text query (will be embedded with the pipeline model)
            filters: ChromaDB where clause for metadata filtering
            n_results: Number of results to return

        Returns:
            List of dicts with 'id', 'metadata', 'distance', 'similarity'
        """
        self._init_client()
        if self._collection.count() == 0:
            return []

        # I8 fix: if query_text given, embed it with our model, not ChromaDB's
        if query_embedding is None and query_text:
            from nlp.semantic_classifier import get_classifier
            classifier = get_classifier()
            query_embedding = classifier.embed(query_text)

        if query_embedding is None:
            return []

        kwargs = {
            'query_embeddings': [query_embedding.tolist()
                                 if isinstance(query_embedding, np.ndarray)
                                 else query_embedding],
            'n_results': min(n_results, self._collection.count()),
            'include': ['metadatas', 'distances', 'documents']
        }

        if filters:
            kwargs['where'] = filters

        try:
            results = self._collection.query(**kwargs)
        except Exception as e:
            logger.warning(f"ChromaDB search error: {e}")
            return []

        output = []
        for i, doc_id in enumerate(results.get('ids', [[]])[0]):
            distance = (results['distances'][0][i]
                        if results.get('distances') else 0)
            output.append({
                'id': doc_id,
                'metadata': results['metadatas'][0][i]
                if results.get('metadatas') else {},
                'document': results['documents'][0][i]
                if results.get('documents') else '',
                'distance': distance,
                'similarity': 1.0 - distance,
            })

        return output

    def count(self) -> int:
        """Return number of articles in the store."""
        self._init_client()
        return self._collection.count()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_instance: Optional[ArticleStore] = None


def get_store(persist_dir: Optional[str] = None) -> ArticleStore:
    """Get or create the module-level ArticleStore singleton."""
    global _instance
    if _instance is None:
        kwargs = {}
        if persist_dir:
            kwargs['persist_dir'] = persist_dir
        _instance = ArticleStore(**kwargs)
    return _instance
