"""BAR-57 — Publisher 검증 (4 cases)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from backend.core.news.publisher import (
    STREAM_KEY,
    InMemoryStreamPublisher,
    RedisStreamPublisher,
)
from backend.models.news import NewsItem, NewsSource


def _item(source_id: str = "x") -> NewsItem:
    return NewsItem(
        source=NewsSource.DART,
        source_id=source_id,
        title="t",
        url="https://dart.fss.or.kr/x",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
    )


class TestInMemory:
    @pytest.mark.asyncio
    async def test_publish_enqueues(self):
        pub = InMemoryStreamPublisher(maxsize=10)
        await pub.publish(_item("a"))
        assert pub.queue.qsize() == 1
        assert pub.dropped == 0

    @pytest.mark.asyncio
    async def test_queue_full_drops_and_counts(self):
        pub = InMemoryStreamPublisher(maxsize=2)
        await pub.publish(_item("a"))
        await pub.publish(_item("b"))
        await pub.publish(_item("c"))  # full → drop
        assert pub.dropped == 1
        assert pub.queue.qsize() == 2


class TestRedis:
    def test_secretstr_required(self):
        with pytest.raises(TypeError, match="SecretStr"):
            RedisStreamPublisher("plain_url")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_xadd_invoked_with_maxlen(self):
        fake_client = AsyncMock()
        fake_client.xadd = AsyncMock()
        with patch(
            "redis.asyncio.from_url", return_value=fake_client
        ):
            pub = RedisStreamPublisher(SecretStr("redis://x:6379"), maxlen=10000)
            await pub.publish(_item("a"))
            args, kwargs = fake_client.xadd.await_args
            assert args[0] == STREAM_KEY
            assert kwargs.get("maxlen") == 10000
            assert kwargs.get("approximate") is True
            payload_dict = args[1]
            assert "payload" in payload_dict
