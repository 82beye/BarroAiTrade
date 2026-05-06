"""BAR-59 — TfidfLogRegClassifier (4 cases)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.themes.classifier import TfidfLogRegClassifier
from backend.models.news import NewsItem, NewsSource


FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "theme_labels.json"


def _samples():
    with open(FIXTURE, encoding="utf-8") as f:
        data = json.load(f)
    return [(t, theme) for theme, texts in data.items() for t in texts]


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


class TestTfidfLR:
    @pytest.mark.asyncio
    async def test_fit_and_classify_basic(self):
        cls = TfidfLogRegClassifier(threshold=0.0)  # 모든 라벨 포함
        cls.fit(_samples())
        r = await cls.classify(_news("전기차 배터리"))
        assert r.backend == "tfidf_lr_v1"
        assert len(r.scores) == 5  # 테마 5종
        assert r.confidence > 0

    @pytest.mark.asyncio
    async def test_unfit_returns_empty(self):
        cls = TfidfLogRegClassifier()
        r = await cls.classify(_news("아무거나"))
        assert r.tags == ()
        assert r.confidence == 0.0
        assert r.attempted == ("tfidf_lr_v1",)

    @pytest.mark.asyncio
    async def test_threshold_filter(self):
        cls = TfidfLogRegClassifier(threshold=0.99)  # 매우 보수적
        cls.fit(_samples())
        r = await cls.classify(_news("전기차"))
        # threshold 0.99 → 거의 빈 tags
        assert all(s < 0.99 for c, s in r.scores.items() if c not in r.tags)

    @pytest.mark.asyncio
    async def test_attempted_records_backend_id(self):
        cls = TfidfLogRegClassifier(threshold=0.0)
        cls.fit(_samples())
        r = await cls.classify(_news("AI 반도체"))
        assert r.attempted == ("tfidf_lr_v1",)
