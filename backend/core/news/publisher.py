"""
BAR-57 — Stream publisher.

- InMemoryStreamPublisher: asyncio.Queue (maxsize 강제, full 시 drop + 메트릭).
- RedisStreamPublisher: Redis Streams XADD news_items MAXLEN ~10000.

design §4.1 5 항목 계약:
    stream key:       news_items
    consumer group:   embedder_v1 (BAR-58 등록)
    payload:          단일 필드 'payload' = NewsItem.model_dump_json()
    ACK / PEL:        BAR-58 consumer 책임
    MAXLEN:           ~10000 (분당 4건 × 41h retention)
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol

from pydantic import SecretStr

from backend.models.news import NewsItem

logger = logging.getLogger(__name__)

STREAM_KEY = "news_items"
STREAM_MAXLEN = 10_000


class StreamPublisher(Protocol):
    async def publish(self, item: NewsItem) -> None: ...


class InMemoryStreamPublisher:
    """asyncio.Queue, maxsize 초과 시 drop + 카운터 +1."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._queue: asyncio.Queue[NewsItem] = asyncio.Queue(maxsize=maxsize)
        self.dropped: int = 0

    async def publish(self, item: NewsItem) -> None:
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            logger.warning("InMemoryStreamPublisher queue full — dropped (%d)", self.dropped)

    @property
    def queue(self) -> asyncio.Queue:
        return self._queue


class RedisStreamPublisher:
    """Redis Streams XADD MAXLEN ~10000. lazy connect."""

    def __init__(self, redis_url: SecretStr, maxlen: int = STREAM_MAXLEN) -> None:
        if not isinstance(redis_url, SecretStr):
            raise TypeError("redis_url must be SecretStr (CWE-522)")
        self._redis_url = redis_url
        self._maxlen = maxlen
        self._client: Optional[object] = None

    async def _connect(self):
        if self._client is None:
            import redis.asyncio as redis_async

            self._client = redis_async.from_url(
                self._redis_url.get_secret_value(),
                decode_responses=True,
            )
        return self._client

    async def publish(self, item: NewsItem) -> None:
        client = await self._connect()
        await client.xadd(
            STREAM_KEY,
            {"payload": item.model_dump_json()},
            maxlen=self._maxlen,
            approximate=True,
        )


__all__ = [
    "STREAM_KEY",
    "STREAM_MAXLEN",
    "StreamPublisher",
    "InMemoryStreamPublisher",
    "RedisStreamPublisher",
]
