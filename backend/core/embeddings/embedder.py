"""
BAR-58 — Embedder Protocol + 3 구현체.

- FakeDeterministicEmbedder: sha256 기반 결정성 (테스트/dev)
- LocalKoSbertEmbedder: sentence-transformers + ko-sbert (lazy import + revision pin CWE-494)
- create_embedder: settings 분기 팩토리
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Optional, Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Embedder(Protocol):
    """텍스트 → 벡터 변환 추상."""

    name: str
    dim: int

    async def encode(self, texts: list[str]) -> list[np.ndarray]: ...


# ─────────────────────────────────────────────
# FakeDeterministicEmbedder — 테스트/dev
# ─────────────────────────────────────────────


class FakeDeterministicEmbedder:
    """sha256(text) → 768-dim float32 L2-normalized.

    결정성 보장 (같은 text → 같은 vector).
    entropy 한정 명시: 96 unique value × 8 repeat (reviewer 권고).
    """

    name = "fake-deterministic-768"
    dim = 768

    async def encode(self, texts: list[str]) -> list[np.ndarray]:
        out: list[np.ndarray] = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            base = np.concatenate(
                [
                    np.frombuffer(
                        hashlib.sha256(h + bytes([i])).digest(),
                        dtype=np.uint8,
                    ).astype(np.float32)
                    for i in range(3)
                ]
            )
            arr = base.repeat(8) / 255.0
            n = float(np.linalg.norm(arr))
            arr = arr / n if n > 0 else arr
            out.append(arr.astype(np.float32))
        return out


# ─────────────────────────────────────────────
# LocalKoSbertEmbedder — sentence-transformers (lazy)
# ─────────────────────────────────────────────


class LocalKoSbertEmbedder:
    """sentence-transformers + ko-sbert (HuggingFace revision pin).

    security 권고 (CWE-494): revision 미지정 시 ValueError.
    developer 권고: encode 는 asyncio.to_thread (CPU-bound, event loop 보호).
    """

    name = "ko-sbert-768"

    def __init__(
        self,
        model_name: str = "jhgan/ko-sroberta-multitask",
        revision: str = "",
        cache_folder: Optional[str] = None,
        expected_dim: int = 768,
    ) -> None:
        if not revision:
            raise ValueError(
                "revision must be pinned (CWE-494 supply chain). "
                "Use HuggingFace commit SHA."
            )
        self._model_name = model_name
        self._revision = revision
        self._cache_folder = cache_folder
        self.dim = expected_dim
        self._model: Optional[object] = None  # lazy

    async def encode(self, texts: list[str]) -> list[np.ndarray]:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self._model_name,
                revision=self._revision,
                cache_folder=self._cache_folder,
            )
        if not texts:
            return []
        arrs = await asyncio.to_thread(
            self._model.encode,
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [np.asarray(a, dtype=np.float32) for a in arrs]


# ─────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────


def create_embedder(settings) -> Embedder:
    """settings.news_embedding_backend → Embedder 인스턴스."""
    backend = settings.news_embedding_backend
    if backend == "fake":
        return FakeDeterministicEmbedder()
    if backend == "ko_sbert":
        return LocalKoSbertEmbedder(
            model_name=settings.news_embedding_model,
            revision=settings.news_embedding_revision or "",
            expected_dim=settings.news_embedding_dim,
        )
    if backend == "openai":
        raise NotImplementedError("openai backend deferred to BAR-58b")
    raise ValueError(f"unknown backend: {backend}")


__all__ = [
    "Embedder",
    "FakeDeterministicEmbedder",
    "LocalKoSbertEmbedder",
    "create_embedder",
]
