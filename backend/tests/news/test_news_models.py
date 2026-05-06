"""BAR-57 — NewsItem / NewsSource / SourceIdStr 검증 (5 cases)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.models.news import NewsItem, NewsSource


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TestNewsItem:
    def test_frozen_blocks_mutation(self):
        item = NewsItem(
            source=NewsSource.DART,
            source_id="20260507001",
            title="t",
            url="https://x.com/y",
            published_at=_now(),
            fetched_at=_now(),
        )
        with pytest.raises(Exception):
            item.title = "modified"  # type: ignore[misc]

    def test_source_id_max_length_256(self):
        with pytest.raises(ValidationError):
            NewsItem(
                source=NewsSource.DART,
                source_id="a" * 257,
                title="t",
                url="https://x.com/y",
                published_at=_now(),
                fetched_at=_now(),
            )

    def test_source_id_pattern(self):
        # 패턴 외 문자 (공백) → 검증 실패
        with pytest.raises(ValidationError):
            NewsItem(
                source=NewsSource.DART,
                source_id="bad id with spaces",
                title="t",
                url="https://x.com/y",
                published_at=_now(),
                fetched_at=_now(),
            )

    def test_tags_tuple_round_trip(self):
        item = NewsItem(
            source=NewsSource.RSS_HANKYUNG,
            source_id="abc-123",
            title="t",
            url="https://rss.hankyung.com/x",
            published_at=_now(),
            fetched_at=_now(),
            tags=("tag1", "tag2"),
        )
        assert isinstance(item.tags, tuple)
        assert item.tags == ("tag1", "tag2")

    def test_tz_aware_datetime_accepted(self):
        item = NewsItem(
            source=NewsSource.DART,
            source_id="20260507001",
            title="t",
            url="https://dart.fss.or.kr/x",
            published_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
            fetched_at=_now(),
        )
        assert item.published_at.tzinfo is not None
