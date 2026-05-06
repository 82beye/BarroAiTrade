"""BAR-59 — EmbeddingCosineClassifier (4 cases)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from backend.core.embeddings.embedder import FakeDeterministicEmbedder
from backend.core.themes.classifier import EmbeddingCosineClassifier
from backend.models.news import NewsItem, NewsSource


def _news(title: str, body: str = "") -> NewsItem:
    return NewsItem(
        source=NewsSource.DART,
        source_id="x-1",
        title=title,
        body=body,
        url="https://x",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )


PROTOTYPES = {
    "전기차": "전기차 배터리 시장 성장",
    "반도체": "메모리 반도체 가격 회복",
    "AI": "GPT 거대언어모델 학습",
}


class _BadDimEmbedder:
    name = "bad-dim"
    dim = 768

    def __init__(self):
        self._call = 0

    async def encode(self, texts):
        self._call += 1
        # 첫 호출 (prototype) → 768-dim, 두 번째 호출 (news) → 100-dim 으로 mismatch 유발
        if self._call == 1:
            return [np.ones(768, dtype=np.float32) / np.sqrt(768) for _ in texts]
        return [np.ones(100, dtype=np.float32) / np.sqrt(100) for _ in texts]


class TestEmbeddingCosine:
    @pytest.mark.asyncio
    async def test_prototype_cached(self):
        embedder = FakeDeterministicEmbedder()
        cls = EmbeddingCosineClassifier(embedder, PROTOTYPES, threshold=2.0)
        await cls._ensure_prototypes()
        first = cls._proto_vecs
        await cls._ensure_prototypes()
        # 동일 객체 (재계산 X)
        assert cls._proto_vecs is first

    @pytest.mark.asyncio
    async def test_threshold_filter(self):
        embedder = FakeDeterministicEmbedder()
        cls = EmbeddingCosineClassifier(embedder, PROTOTYPES, threshold=0.0)
        # threshold 0.0 → 빈 tags (자기 자신 외 모두 제외)
        r = await cls.classify(_news("이슈 없음", body="random text"))
        assert isinstance(r.tags, tuple)
        # 모두 distance > 0 일 가능성 → 빈 tags
        assert r.backend == "embedding_cosine_v1"

    @pytest.mark.asyncio
    async def test_dim_mismatch_raises(self):
        cls = EmbeddingCosineClassifier(_BadDimEmbedder(), PROTOTYPES, threshold=2.0)
        with pytest.raises(ValueError, match="dim mismatch"):
            await cls.classify(_news("test"))

    @pytest.mark.asyncio
    async def test_l2_normalized_returns_distance_in_range(self):
        embedder = FakeDeterministicEmbedder()
        cls = EmbeddingCosineClassifier(embedder, PROTOTYPES, threshold=2.0)
        r = await cls.classify(_news("전기차"))
        for theme, dist in r.scores.items():
            # cosine distance: [0, 2]
            assert 0.0 <= dist <= 2.0
