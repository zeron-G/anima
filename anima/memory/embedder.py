"""Local embedding engine — semantic search fallback when ChromaDB is unavailable.

H-08 fix: Provides vector embeddings for memory search without requiring
ChromaDB. Uses sentence-transformers with a multilingual model that
supports both Chinese and English.

Three-tier fallback strategy (managed by MemoryStore):
  1. ChromaDB (if installed) — full vector DB
  2. Local embedder (this module) — in-memory cosine similarity
  3. SQLite LIKE — last resort keyword matching

The embedding model is loaded lazily on first use and cached for the
process lifetime. On a machine with a GPU, encoding is fast (~5ms per
sentence). On CPU-only, ~50ms per sentence.
"""

from __future__ import annotations

import struct
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("embedder")

# Model config — multilingual model that handles Chinese + English well
# paraphrase-multilingual-MiniLM-L12-v2: 118M params, 384-dim embeddings, ~50MB
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_EMBEDDING_DIM = 384

# Lazy-loaded model singleton
_model: Any = None
_model_load_attempted = False


def _get_model():
    """Lazy-load the sentence-transformer model.

    Returns the model or None if sentence-transformers is not installed.
    Only attempts loading once per process lifetime.
    """
    global _model, _model_load_attempted
    if _model is not None:
        return _model
    if _model_load_attempted:
        return None  # Already tried and failed
    _model_load_attempted = True

    try:
        from sentence_transformers import SentenceTransformer
        log.info("Loading embedding model: %s ...", _MODEL_NAME)
        _model = SentenceTransformer(_MODEL_NAME)
        log.info("Embedding model loaded: %s (%d dimensions)", _MODEL_NAME, _EMBEDDING_DIM)
        return _model
    except ImportError:
        log.info(
            "sentence-transformers not installed — local embeddings disabled. "
            "Install with: pip install sentence-transformers"
        )
        return None
    except Exception as e:
        log.warning("Failed to load embedding model: %s", e)
        return None


def is_available() -> bool:
    """Check if the local embedder is available (model loadable)."""
    return _get_model() is not None


def embed(text: str) -> list[float] | None:
    """Generate an embedding vector for the given text.

    Args:
        text: Input text to embed (Chinese, English, or mixed).

    Returns:
        A list of floats (384-dimensional vector), normalized to unit length.
        Returns None if the model is unavailable.
    """
    model = _get_model()
    if model is None:
        return None

    try:
        # Truncate very long text (model has ~512 token limit)
        if len(text) > 2000:
            text = text[:2000]

        vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return vec.tolist()
    except Exception as e:
        log.debug("Embedding failed for text[:%d]: %s", min(len(text), 50), e)
        return None


def embed_batch(texts: list[str]) -> list[list[float] | None]:
    """Embed multiple texts in a single batch (more efficient than individual calls).

    Returns a list of embedding vectors (or None for failed items).
    """
    model = _get_model()
    if model is None:
        return [None] * len(texts)

    try:
        # Truncate
        truncated = [t[:2000] for t in texts]
        vectors = model.encode(truncated, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vectors]
    except Exception as e:
        log.warning("Batch embedding failed: %s", e)
        return [None] * len(texts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors.

    Both vectors should be normalized (unit length) for this to return
    values in [-1, 1]. With normalized vectors, cosine similarity
    simplifies to the dot product.

    Returns:
        Similarity score in [-1, 1]. Higher = more similar.
    """
    if len(a) != len(b):
        return 0.0
    # Dot product of normalized vectors = cosine similarity
    dot = sum(x * y for x, y in zip(a, b))
    return dot


# ── Serialization helpers for SQLite storage ──

def vector_to_bytes(vec: list[float]) -> bytes:
    """Pack a float vector into compact bytes for SQLite BLOB storage.

    Uses struct.pack for 4-byte floats (384 dims * 4 bytes = 1536 bytes).
    """
    return struct.pack(f"{len(vec)}f", *vec)


def bytes_to_vector(data: bytes) -> list[float]:
    """Unpack bytes back into a float vector."""
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


# ── Embedding dimension info ──

def get_embedding_dim() -> int:
    """Return the dimensionality of the embedding model."""
    return _EMBEDDING_DIM


def get_model_name() -> str:
    """Return the name of the embedding model."""
    return _MODEL_NAME
