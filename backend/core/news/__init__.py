"""BAR-57 — 뉴스/공시 수집 파이프라인."""

from backend.core.news.collector import NewsCollector
from backend.core.news.dedup import (
    Deduplicator,
    InMemoryDeduplicator,
    RedisDeduplicator,
)
from backend.core.news.publisher import (
    InMemoryStreamPublisher,
    RedisStreamPublisher,
    StreamPublisher,
)
from backend.core.news.sources import (
    DARTSource,
    NewsSourceAdapter,
    RSSSource,
)

__all__ = [
    "NewsCollector",
    "Deduplicator",
    "InMemoryDeduplicator",
    "RedisDeduplicator",
    "StreamPublisher",
    "InMemoryStreamPublisher",
    "RedisStreamPublisher",
    "NewsSourceAdapter",
    "RSSSource",
    "DARTSource",
]
