"""BAR-72 — CacheLayer (InMemory / Redis 어댑터).

P95 latency 50% 감소 목표. dialect 무관.
"""
from __future__ import annotations

import time
from typing import Any, Optional, Protocol

from pydantic import SecretStr


class CacheLayer(Protocol):
    async def get(self, key: str) -> Optional[Any]: ...
    async def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None: ...
    async def delete(self, key: str) -> None: ...


class InMemoryCache:
    """단일 프로세스 캐시. TTL 만료 evict."""

    def __init__(self, max_size: int = 10_000) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._max = max_size

    async def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    async def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if len(self._store) >= self._max:
            # 가장 오래된 키 evict (단순 FIFO)
            oldest = next(iter(self._store))
            self._store.pop(oldest, None)
        self._store[key] = (time.time() + ttl_seconds, value)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class RedisCache:
    """Redis 어댑터 (lazy connect)."""

    def __init__(self, redis_url: SecretStr) -> None:
        if not isinstance(redis_url, SecretStr):
            raise TypeError("redis_url must be SecretStr (CWE-522)")
        self._url = redis_url
        self._client: Optional[object] = None

    async def _connect(self):
        if self._client is None:
            import redis.asyncio as redis_async

            self._client = redis_async.from_url(
                self._url.get_secret_value(),
                decode_responses=True,
            )
        return self._client

    async def get(self, key: str) -> Optional[Any]:
        client = await self._connect()
        return await client.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int = 60) -> None:
        client = await self._connect()
        await client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        client = await self._connect()
        await client.delete(key)


__all__ = ["CacheLayer", "InMemoryCache", "RedisCache"]
