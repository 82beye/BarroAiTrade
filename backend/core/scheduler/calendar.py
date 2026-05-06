"""BAR-61 — EventCalendar / EventCollector / EventLinker.

stub 어댑터 (worktree). 실 IR/인포맥스/FnGuide API 는 BAR-61b.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Protocol, runtime_checkable

from backend.models.event import EventType, MarketEvent

logger = logging.getLogger(__name__)


@runtime_checkable
class EventCollector(Protocol):
    async def fetch(self, start: date, end: date) -> list[MarketEvent]: ...


class StubEventCollector:
    """fixture 기반 collector — BAR-61b 에서 실 API 어댑터로 교체."""

    def __init__(self, fixtures: list[MarketEvent] | None = None) -> None:
        self._fixtures = fixtures or []

    async def fetch(self, start: date, end: date) -> list[MarketEvent]:
        return [e for e in self._fixtures if start <= e.event_date <= end]


class EventCalendar:
    """저장된 events 조회 + collector 결과 적재 orchestrator."""

    def __init__(self, repo, collector: EventCollector | None = None) -> None:
        self._repo = repo
        self._collector = collector

    async def refresh(self, start: date, end: date) -> int:
        """collector 에서 fetch 후 repo 적재. 적재된 신규 건수 반환."""
        if self._collector is None:
            return 0
        events = await self._collector.fetch(start, end)
        count = 0
        for ev in events:
            inserted = await self._repo.insert(ev)
            if inserted:
                count += 1
        return count


class EventLinker:
    """event → 관련 종목 매핑.

    1) symbol 직접 지정 → [symbol]
    2) symbol None + title 키워드 → theme_repo.find_themes_by_keyword (BAR-61b 확장)
       worktree 단계는 title 매칭 fixture 만 처리
    """

    def __init__(self, theme_repo=None) -> None:
        self._theme_repo = theme_repo

    async def link_event_to_stocks(self, event: MarketEvent) -> list[str]:
        if event.symbol:
            return [event.symbol]
        # symbol 없는 경우 — theme_repo 기반 매칭은 BAR-61b 정식
        # worktree 단계: metadata 의 'related_stocks' fallback
        related = event.metadata.get("related_stocks") if event.metadata else None
        if isinstance(related, list):
            return [str(s) for s in related]
        return []


__all__ = ["EventCollector", "StubEventCollector", "EventCalendar", "EventLinker"]
