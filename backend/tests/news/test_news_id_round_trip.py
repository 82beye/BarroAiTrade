"""BAR-58 — NewsItem.id round-trip + collector model_copy + publisher payload (4 cases)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.core.news.collector import NewsCollector
from backend.core.news.dedup import InMemoryDeduplicator
from backend.core.news.publisher import InMemoryStreamPublisher
from backend.models.news import NewsItem, NewsSource


def _item(sid="x") -> NewsItem:
    return NewsItem(
        source=NewsSource.DART,
        source_id=sid,
        title="t",
        url="https://dart.fss.or.kr/x",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )


class _FakeSource:
    def __init__(self, items):
        self.name = NewsSource.DART
        self._items = items

    async def fetch(self):
        return self._items


class _FakeRepoWithId:
    """insert 반환 = Optional[int] (BIGSERIAL id)."""

    def __init__(self, next_id=42):
        self._next = next_id

    async def insert(self, item):
        id_ = self._next
        self._next += 1
        return id_


class TestNewsItemId:
    def test_id_default_is_none(self):
        item = _item()
        assert item.id is None

    def test_id_can_be_set_via_model_copy(self):
        item = _item()
        item2 = item.model_copy(update={"id": 100})
        assert item2.id == 100
        assert item.id is None  # frozen — 원본 보존

    @pytest.mark.asyncio
    async def test_collector_model_copy_after_insert(self):
        item = _item("seq-1")
        repo = _FakeRepoWithId(next_id=42)
        pub = InMemoryStreamPublisher(maxsize=10)
        c = NewsCollector(
            sources=[_FakeSource([item])],
            repo=repo,
            publisher=pub,
            dedup=InMemoryDeduplicator(),
            http_client=MagicMock(spec=httpx.AsyncClient),
            retry_backoff=0.01,
        )
        await c.tick()
        assert pub.queue.qsize() == 1
        published_item = await pub.queue.get()
        assert published_item.id == 42

    @pytest.mark.asyncio
    async def test_publisher_payload_contains_id(self):
        """InMemoryStreamPublisher 는 NewsItem 그대로. RedisStreamPublisher
        는 model_dump_json 직렬화 — id 필드 포함되어야."""
        item = _item("payload").model_copy(update={"id": 7})
        # model_dump_json 직접 검증
        payload = item.model_dump_json()
        data = json.loads(payload)
        assert data["id"] == 7
