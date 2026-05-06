"""
BAR-57 — NewsCollector orchestrator.

design §5.2 4단 호출 시퀀스:
    dedup.seen → repo.insert (ON CONFLICT DO NOTHING) → publisher.publish → dedup.mark

실패 분기:
    - source.fetch 예외/timeout: 메트릭 +1, 다른 source 진행
    - repo.insert 0 row: publish skip (NFR-07)
    - publisher.publish 실패: dedup.mark skip → 다음 사이클 재시도
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from backend.core.news.dedup import Deduplicator
from backend.core.news.publisher import StreamPublisher
from backend.core.news.sources import NewsSourceAdapter
from backend.models.news import NewsItem

logger = logging.getLogger(__name__)


class NewsCollector:
    """1분 cron — 모든 source 격리 fetch + 4단 시퀀스로 적재 + 발행."""

    def __init__(
        self,
        sources: list[NewsSourceAdapter],
        repo,                                 # NewsRepository (duck-typed)
        publisher: StreamPublisher,
        dedup: Deduplicator,
        http_client: httpx.AsyncClient,
        scheduler: Optional[object] = None,   # AsyncIOScheduler — 의존성 주입
        fetch_timeout: float = 30.0,
        retry_backoff: float = 1.0,
    ) -> None:
        self._sources = sources
        self._repo = repo
        self._publisher = publisher
        self._dedup = dedup
        self._http = http_client
        self._scheduler = scheduler
        self._fetch_timeout = fetch_timeout
        self._retry_backoff = retry_backoff
        self.errors: int = 0
        self.published: int = 0

    def start(self) -> None:
        """1분 cron 등록. 외부 스케줄러 주입 시 add_job."""
        if self._scheduler is None:
            return
        self._scheduler.add_job(self.tick, "cron", second="0", id="news_collector_tick")

    async def stop(self) -> None:
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
        try:
            await self._http.aclose()
        except Exception:
            pass

    async def tick(self) -> None:
        """모든 source 격리 fetch — 한 source 실패가 다른 source 진행 차단 X."""
        await asyncio.gather(
            *[self._fetch_one(s) for s in self._sources],
            return_exceptions=True,
        )

    async def _fetch_one(self, src: NewsSourceAdapter) -> None:
        """단일 source — 30s 예산 + 1회 retry + 4단 시퀀스."""
        try:
            items = await asyncio.wait_for(
                self._fetch_with_retry(src), timeout=self._fetch_timeout
            )
        except asyncio.TimeoutError:
            self.errors += 1
            logger.warning("source %s timeout", src.name)
            return
        except Exception as exc:
            self.errors += 1
            logger.warning("source %s failed: %s", src.name, exc)
            return

        for item in items:
            await self._handle_item(item)

    async def _fetch_with_retry(
        self, src: NewsSourceAdapter
    ) -> list[NewsItem]:
        """1차 실패 → 백오프 → 2차 시도. 모두 실패 시 raise."""
        try:
            return await src.fetch()
        except Exception as first_exc:
            logger.info("source %s 1차 실패: %s — retry", src.name, first_exc)
            await asyncio.sleep(self._retry_backoff)
            return await src.fetch()

    async def _handle_item(self, item: NewsItem) -> None:
        key = f"news:dedup:{item.source.value}:{item.source_id}"
        try:
            if await self._dedup.seen(key):
                return
            inserted = await self._repo.insert(item)
            if not inserted:
                # 0 row = race / 이미 적재 → publish skip (NFR-07)
                return
            try:
                await self._publisher.publish(item)
            except Exception as exc:
                # publisher 실패 — dedup.mark skip → 다음 사이클 재시도
                logger.warning("publish failed for %s: %s", item.source_id, exc)
                return
            await self._dedup.mark(key)
            self.published += 1
        except Exception as exc:
            self.errors += 1
            logger.warning("handle_item %s exception: %s", item.source_id, exc)


__all__ = ["NewsCollector"]
