"""BAR-59 — ThreeTierClassifier orchestrator (5 cases)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core.themes.classifier import ThreeTierClassifier
from backend.models.news import NewsItem, NewsSource
from backend.models.theme import ClassificationResult


def _news() -> NewsItem:
    return NewsItem(
        source=NewsSource.DART,
        source_id="x-1",
        title="t",
        url="https://x",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )


class _FakeClassifier:
    def __init__(self, backend_id, tags=(), confidence=0.0, raises=None):
        self.backend_id = backend_id
        self._tags = tags
        self._confidence = confidence
        self._raises = raises

    async def classify(self, news_item):
        if self._raises is not None:
            raise self._raises
        return ClassificationResult(
            tags=self._tags,
            backend=self.backend_id,
            confidence=self._confidence,
            attempted=(self.backend_id,),
        )


class TestThreeTier:
    @pytest.mark.asyncio
    async def test_tier1_hit_returns_immediately(self):
        t1 = _FakeClassifier("tfidf_lr_v1", tags=("전기차",), confidence=0.85)
        t2 = _FakeClassifier("embedding_cosine_v1")
        t3 = _FakeClassifier("claude_haiku_v1", raises=NotImplementedError("x"))
        orch = ThreeTierClassifier(t1, t2, t3)
        r = await orch.classify(_news())
        assert r.backend == "tfidf_lr_v1"
        assert r.attempted == ("tfidf_lr_v1",)

    @pytest.mark.asyncio
    async def test_tier2_fallback_on_tier1_low_confidence(self):
        t1 = _FakeClassifier("tfidf_lr_v1", tags=(), confidence=0.3)
        t2 = _FakeClassifier("embedding_cosine_v1", tags=("AI",), confidence=0.6)
        t3 = _FakeClassifier("claude_haiku_v1", raises=NotImplementedError("x"))
        orch = ThreeTierClassifier(t1, t2, t3)
        r = await orch.classify(_news())
        assert r.backend == "embedding_cosine_v1"
        assert r.attempted == ("tfidf_lr_v1", "embedding_cosine_v1")

    @pytest.mark.asyncio
    async def test_tier3_not_implemented_falls_back_to_best(self):
        t1 = _FakeClassifier("tfidf_lr_v1", tags=(), confidence=0.4)
        t2 = _FakeClassifier("embedding_cosine_v1", tags=(), confidence=0.2)
        t3 = _FakeClassifier("claude_haiku_v1", raises=NotImplementedError("x"))
        orch = ThreeTierClassifier(t1, t2, t3)
        r = await orch.classify(_news())
        # tier1 confidence 0.4 > tier2 0.2 → tier1 best
        assert r.backend.startswith("three_tier_v1:fallback_no_tier3:from_tfidf_lr_v1")
        assert r.attempted == ("tfidf_lr_v1", "embedding_cosine_v1", "claude_haiku_v1")

    @pytest.mark.asyncio
    async def test_tier3_active_normal_path(self):
        t1 = _FakeClassifier("tfidf_lr_v1", tags=(), confidence=0.4)
        t2 = _FakeClassifier("embedding_cosine_v1", tags=(), confidence=0.2)
        t3 = _FakeClassifier("claude_haiku_v1", tags=("AI",), confidence=0.9)
        orch = ThreeTierClassifier(t1, t2, t3)
        r = await orch.classify(_news())
        assert r.backend == "claude_haiku_v1"
        assert r.attempted == ("tfidf_lr_v1", "embedding_cosine_v1", "claude_haiku_v1")

    @pytest.mark.asyncio
    async def test_attempted_accumulated_across_tiers(self):
        t1 = _FakeClassifier("tfidf_lr_v1", tags=(), confidence=0.1)
        t2 = _FakeClassifier("embedding_cosine_v1", tags=(), confidence=0.1)
        t3 = _FakeClassifier("claude_haiku_v1", raises=NotImplementedError("x"))
        orch = ThreeTierClassifier(t1, t2, t3)
        r = await orch.classify(_news())
        # 3 tier 모두 시도되었음
        assert len(r.attempted) == 3
