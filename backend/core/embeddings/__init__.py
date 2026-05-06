"""BAR-58 — 임베딩 인프라 (Embedder Protocol + Worker + Factory)."""

from backend.core.embeddings.embedder import (
    Embedder,
    FakeDeterministicEmbedder,
    LocalKoSbertEmbedder,
    create_embedder,
)
from backend.core.embeddings.worker import EmbeddingWorker

__all__ = [
    "Embedder",
    "FakeDeterministicEmbedder",
    "LocalKoSbertEmbedder",
    "create_embedder",
    "EmbeddingWorker",
]
