"""BAR-57 — Dedup 검증 (5 cases)."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from backend.core.news.dedup import InMemoryDeduplicator, RedisDeduplicator


class TestInMemory:
    @pytest.mark.asyncio
    async def test_mark_and_seen(self):
        d = InMemoryDeduplicator(ttl_hours=24, max_size=100)
        assert await d.seen("k1") is False
        await d.mark("k1")
        assert await d.seen("k1") is True

    @pytest.mark.asyncio
    async def test_ttl_expiry_returns_false(self):
        d = InMemoryDeduplicator(ttl_hours=24)
        # 직접 cache 에 만료된 timestamp 주입
        d._cache["k"] = time.time() - (25 * 3600)
        assert await d.seen("k") is False

    @pytest.mark.asyncio
    async def test_lru_evicts_oldest_when_full(self):
        d = InMemoryDeduplicator(ttl_hours=24, max_size=3)
        for k in ["a", "b", "c", "d"]:
            await d.mark(k)
        # a 가 evict 되었어야
        assert await d.seen("a") is False
        assert await d.seen("d") is True


class TestRedis:
    def test_secretstr_required(self):
        with pytest.raises(TypeError, match="SecretStr"):
            RedisDeduplicator("plain_url", ttl_hours=72)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_seen_and_mark_via_mock(self):
        """redis.asyncio.from_url mock 으로 동작 검증."""
        fake_client = AsyncMock()
        fake_client.exists = AsyncMock(return_value=0)
        fake_client.set = AsyncMock()

        with patch(
            "redis.asyncio.from_url", return_value=fake_client
        ):
            d = RedisDeduplicator(SecretStr("redis://x:6379"), ttl_hours=72)
            assert await d.seen("k") is False
            await d.mark("k")
            fake_client.set.assert_awaited_once()
            args, kwargs = fake_client.set.await_args
            assert args[0] == "k"
            assert kwargs.get("ex") == 72 * 3600
