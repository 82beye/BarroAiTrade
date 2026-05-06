"""
BAR-57 — Deduplicator.

- InMemoryDeduplicator: TTL + LRU 만료. dev/test 기본.
- RedisDeduplicator: SET key + EXPIRE. SecretStr 로 url 보관 (CWE-522).
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Optional, Protocol

from pydantic import SecretStr


class Deduplicator(Protocol):
    async def seen(self, key: str) -> bool: ...
    async def mark(self, key: str) -> None: ...


class InMemoryDeduplicator:
    """LRU + TTL. queue 가득 시 가장 오래된 항목 evict."""

    def __init__(self, ttl_hours: int = 24, max_size: int = 10_000) -> None:
        self._ttl_seconds = ttl_hours * 3600
        self._max = max_size
        self._cache: OrderedDict[str, float] = OrderedDict()

    async def seen(self, key: str) -> bool:
        now = time.time()
        ts = self._cache.get(key)
        if ts is None:
            return False
        if now - ts > self._ttl_seconds:
            # 만료 — 제거 후 False
            self._cache.pop(key, None)
            return False
        # LRU touch
        self._cache.move_to_end(key)
        return True

    async def mark(self, key: str) -> None:
        self._cache[key] = time.time()
        self._cache.move_to_end(key)
        # 용량 초과 시 oldest evict
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)


class RedisDeduplicator:
    """Redis SET + EXPIRE. lazy connect — fixture 친화."""

    def __init__(self, redis_url: SecretStr, ttl_hours: int = 72) -> None:
        if not isinstance(redis_url, SecretStr):
            raise TypeError("redis_url must be SecretStr (CWE-522)")
        self._redis_url = redis_url
        self._ttl_seconds = ttl_hours * 3600
        self._client: Optional[object] = None  # redis.asyncio.Redis

    async def _connect(self):
        if self._client is None:
            import redis.asyncio as redis_async

            self._client = redis_async.from_url(
                self._redis_url.get_secret_value(),
                decode_responses=True,
            )
        return self._client

    async def seen(self, key: str) -> bool:
        client = await self._connect()
        return bool(await client.exists(key))

    async def mark(self, key: str) -> None:
        client = await self._connect()
        await client.set(key, "1", ex=self._ttl_seconds)


__all__ = ["Deduplicator", "InMemoryDeduplicator", "RedisDeduplicator"]
