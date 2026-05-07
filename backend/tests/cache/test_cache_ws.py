"""BAR-72 — Cache + WS Shard (10 cases)."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from backend.core.cache.cache_layer import InMemoryCache, RedisCache
from backend.core.cache.ws_shard import WebSocketChannelShard


class TestInMemoryCache:
    @pytest.mark.asyncio
    async def test_set_get(self):
        c = InMemoryCache()
        await c.set("k1", "v1", ttl_seconds=60)
        assert await c.get("k1") == "v1"

    @pytest.mark.asyncio
    async def test_get_missing(self):
        c = InMemoryCache()
        assert await c.get("missing") is None

    @pytest.mark.asyncio
    async def test_ttl_expiry(self):
        c = InMemoryCache()
        # 직접 expires_at 만료시간 주입 (테스트 시간 단축)
        await c.set("k", "v", ttl_seconds=1)
        c._store["k"] = (time.time() - 1, "v")  # 1초 전 만료
        assert await c.get("k") is None

    @pytest.mark.asyncio
    async def test_invalid_ttl(self):
        c = InMemoryCache()
        with pytest.raises(ValueError):
            await c.set("k", "v", ttl_seconds=0)

    @pytest.mark.asyncio
    async def test_delete(self):
        c = InMemoryCache()
        await c.set("k", "v")
        await c.delete("k")
        assert await c.get("k") is None

    @pytest.mark.asyncio
    async def test_max_size_evict(self):
        c = InMemoryCache(max_size=2)
        await c.set("a", "1")
        await c.set("b", "2")
        await c.set("c", "3")
        # 'a' evicted
        assert await c.get("a") is None
        assert await c.get("c") == "3"


class TestRedisCache:
    def test_secretstr_required(self):
        with pytest.raises(TypeError):
            RedisCache("plain")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_set_get_via_mock(self):
        fake = AsyncMock()
        fake.set = AsyncMock()
        fake.get = AsyncMock(return_value="v")
        with patch("redis.asyncio.from_url", return_value=fake):
            c = RedisCache(SecretStr("redis://x:6379"))
            await c.set("k", "v", ttl_seconds=30)
            assert await c.get("k") == "v"
            fake.set.assert_awaited_once_with("k", "v", ex=30)


class TestWebSocketShard:
    def test_invalid_shards(self):
        with pytest.raises(ValueError):
            WebSocketChannelShard(num_shards=0)

    def test_shard_deterministic(self):
        s = WebSocketChannelShard(num_shards=8)
        assert s.shard_for("user-1") == s.shard_for("user-1")

    def test_shard_in_range(self):
        s = WebSocketChannelShard(num_shards=8)
        for uid in ["alice", "bob", "carol", "dan"]:
            sid = s.shard_for(uid)
            assert 0 <= sid < 8

    def test_channel_format(self):
        s = WebSocketChannelShard()
        ch = s.channel_for("alice")
        assert ch.startswith("ws:shard:")
        assert ":user:alice" in ch

    def test_empty_user_raises(self):
        s = WebSocketChannelShard()
        with pytest.raises(ValueError):
            s.shard_for("")
