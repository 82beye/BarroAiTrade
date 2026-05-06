"""BAR-57 — NewsCollector 4단 시퀀스 + retry/timeout 검증 (6 cases)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from backend.core.news.collector import NewsCollector
from backend.core.news.dedup import InMemoryDeduplicator
from backend.core.news.publisher import InMemoryStreamPublisher
from backend.models.news import NewsItem, NewsSource


def _item(sid: str) -> NewsItem:
    return NewsItem(
        source=NewsSource.DART,
        source_id=sid,
        title="t",
        url="https://dart.fss.or.kr/x",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )


class _FakeRepo:
    def __init__(self, behavior: str = "insert_ok") -> None:
        # insert_ok | insert_dup | insert_fail
        self.behavior = behavior
        self.calls = 0

    async def insert(self, item: NewsItem) -> bool:
        self.calls += 1
        if self.behavior == "insert_ok":
            return True
        if self.behavior == "insert_dup":
            return False
        raise RuntimeError("repo down")


class _FakeSource:
    def __init__(self, name: NewsSource, items=None, raise_first=0):
        self.name = name
        self._items = items or []
        self._calls = 0
        self._raise_first = raise_first

    async def fetch(self):
        self._calls += 1
        if self._calls <= self._raise_first:
            raise RuntimeError("flaky")
        return self._items


@pytest.fixture
def http_client():
    return MagicMock(spec=httpx.AsyncClient)


class TestCollector:
    @pytest.mark.asyncio
    async def test_isolated_sources_one_fails(self, http_client):
        ok = _FakeSource(NewsSource.DART, items=[_item("a")])
        bad = _FakeSource(NewsSource.RSS_HANKYUNG, raise_first=10)
        c = NewsCollector(
            sources=[ok, bad],
            repo=_FakeRepo("insert_ok"),
            publisher=InMemoryStreamPublisher(maxsize=10),
            dedup=InMemoryDeduplicator(),
            http_client=http_client,
            retry_backoff=0.01,
        )
        await c.tick()
        # 한 source 가 실패해도 다른 source 진행
        assert c.published == 1
        assert c.errors >= 1

    @pytest.mark.asyncio
    async def test_retry_then_success(self, http_client):
        src = _FakeSource(NewsSource.DART, items=[_item("a")], raise_first=1)
        c = NewsCollector(
            sources=[src],
            repo=_FakeRepo("insert_ok"),
            publisher=InMemoryStreamPublisher(maxsize=10),
            dedup=InMemoryDeduplicator(),
            http_client=http_client,
            retry_backoff=0.01,
        )
        await c.tick()
        assert c.published == 1
        assert src._calls == 2

    @pytest.mark.asyncio
    async def test_4step_sequence_publish_then_mark(self, http_client):
        item = _item("seq-1")
        repo = _FakeRepo("insert_ok")
        pub = InMemoryStreamPublisher(maxsize=10)
        dedup = InMemoryDeduplicator()
        c = NewsCollector(
            sources=[_FakeSource(NewsSource.DART, items=[item])],
            repo=repo,
            publisher=pub,
            dedup=dedup,
            http_client=http_client,
            retry_backoff=0.01,
        )
        await c.tick()
        # publish 후 dedup.mark 되어 다음 tick 에 seen → True
        assert pub.queue.qsize() == 1
        assert await dedup.seen(f"news:dedup:dart:seq-1") is True

    @pytest.mark.asyncio
    async def test_insert_dup_skips_publish(self, http_client):
        pub = InMemoryStreamPublisher(maxsize=10)
        dedup = InMemoryDeduplicator()
        c = NewsCollector(
            sources=[_FakeSource(NewsSource.DART, items=[_item("dup-1")])],
            repo=_FakeRepo("insert_dup"),
            publisher=pub,
            dedup=dedup,
            http_client=http_client,
            retry_backoff=0.01,
        )
        await c.tick()
        # 0 row → publish skip → mark skip
        assert pub.queue.qsize() == 0
        assert await dedup.seen("news:dedup:dart:dup-1") is False

    @pytest.mark.asyncio
    async def test_publish_failure_skips_mark(self, http_client):
        pub_fail = MagicMock()
        pub_fail.publish = AsyncMock(side_effect=RuntimeError("redis down"))
        dedup = InMemoryDeduplicator()
        c = NewsCollector(
            sources=[_FakeSource(NewsSource.DART, items=[_item("pub-fail")])],
            repo=_FakeRepo("insert_ok"),
            publisher=pub_fail,
            dedup=dedup,
            http_client=http_client,
            retry_backoff=0.01,
        )
        await c.tick()
        # publish 실패 → mark 안 함 → 다음 tick 재시도 가능
        assert await dedup.seen("news:dedup:dart:pub-fail") is False

    @pytest.mark.asyncio
    async def test_timeout_records_error(self, http_client):
        async def slow_fetch():
            await asyncio.sleep(2)
            return []

        slow_src = MagicMock()
        slow_src.name = NewsSource.DART
        slow_src.fetch = slow_fetch
        c = NewsCollector(
            sources=[slow_src],
            repo=_FakeRepo("insert_ok"),
            publisher=InMemoryStreamPublisher(maxsize=10),
            dedup=InMemoryDeduplicator(),
            http_client=http_client,
            fetch_timeout=0.1,
            retry_backoff=0.01,
        )
        await c.tick()
        assert c.errors >= 1
        assert c.published == 0
