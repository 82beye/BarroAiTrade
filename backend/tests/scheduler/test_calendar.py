"""BAR-61 — EventCalendar + Collector + Linker (15 cases)."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from sqlalchemy import text

from backend.core.scheduler.calendar import (
    EventCalendar,
    EventLinker,
    StubEventCollector,
)
from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.event_repo import EventRepository
from backend.models.event import EventType, MarketEvent


def _ev(symbol="005930", d=date(2026, 5, 10), title="t", etype=EventType.EARNINGS, **kw) -> MarketEvent:
    return MarketEvent(
        event_type=etype, symbol=symbol, event_date=d, title=title, **kw
    )


@pytest.fixture
async def isolated_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "event_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS market_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    event_date TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    metadata TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(symbol, event_date, event_type)
                )
                """
            )
        )
    yield db_file
    reset_engine_for_test()


class TestModel:
    def test_event_type_enum(self):
        assert EventType.EARNINGS.value == "earnings"

    def test_market_event_frozen(self):
        e = _ev()
        with pytest.raises(Exception):
            e.title = "modified"  # type: ignore[misc]

    def test_required_fields(self):
        with pytest.raises(Exception):
            MarketEvent(event_type=EventType.OTHER, event_date=date(2026, 5, 10))  # title missing


class TestStubCollector:
    @pytest.mark.asyncio
    async def test_fetch_returns_in_range(self):
        fixtures = [
            _ev(d=date(2026, 5, 10)),
            _ev(d=date(2026, 6, 10), symbol="000660"),
        ]
        c = StubEventCollector(fixtures=fixtures)
        results = await c.fetch(date(2026, 5, 1), date(2026, 5, 31))
        assert len(results) == 1
        assert results[0].symbol == "005930"

    @pytest.mark.asyncio
    async def test_fetch_empty(self):
        c = StubEventCollector(fixtures=[])
        results = await c.fetch(date(2026, 5, 1), date(2026, 5, 31))
        assert results == []


class TestRepository:
    @pytest.mark.asyncio
    async def test_insert_returns_id(self, isolated_db):
        repo = EventRepository()
        new_id = await repo.insert(_ev())
        assert new_id is not None and new_id > 0

    @pytest.mark.asyncio
    async def test_insert_duplicate_returns_none(self, isolated_db):
        repo = EventRepository()
        await repo.insert(_ev())
        dup = await repo.insert(_ev())
        assert dup is None

    @pytest.mark.asyncio
    async def test_find_by_date_range(self, isolated_db):
        repo = EventRepository()
        await repo.insert(_ev(d=date(2026, 5, 10)))
        await repo.insert(_ev(symbol="000660", d=date(2026, 5, 20)))
        rows = await repo.find_by_date_range(date(2026, 5, 1), date(2026, 5, 31))
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_find_by_symbol(self, isolated_db):
        repo = EventRepository()
        await repo.insert(_ev(symbol="005930"))
        await repo.insert(_ev(symbol="000660", title="x"))
        rows = await repo.find_by_symbol("005930")
        assert len(rows) == 1


class TestCalendar:
    @pytest.mark.asyncio
    async def test_refresh_inserts_events(self, isolated_db):
        repo = EventRepository()
        collector = StubEventCollector(fixtures=[
            _ev(d=date(2026, 5, 10)),
            _ev(symbol="000660", d=date(2026, 5, 11)),
        ])
        cal = EventCalendar(repo=repo, collector=collector)
        n = await cal.refresh(date(2026, 5, 1), date(2026, 5, 31))
        assert n == 2

    @pytest.mark.asyncio
    async def test_refresh_no_collector_returns_zero(self, isolated_db):
        cal = EventCalendar(repo=EventRepository(), collector=None)
        n = await cal.refresh(date(2026, 5, 1), date(2026, 5, 31))
        assert n == 0


class TestLinker:
    @pytest.mark.asyncio
    async def test_symbol_direct(self):
        linker = EventLinker()
        result = await linker.link_event_to_stocks(_ev(symbol="005930"))
        assert result == ["005930"]

    @pytest.mark.asyncio
    async def test_no_symbol_falls_back_to_metadata(self):
        linker = EventLinker()
        e = MarketEvent(
            event_type=EventType.POLICY,
            event_date=date(2026, 5, 10),
            title="배터리 정책 발표",
            metadata={"related_stocks": ["006400", "373220"]},
        )
        result = await linker.link_event_to_stocks(e)
        assert result == ["006400", "373220"]

    @pytest.mark.asyncio
    async def test_no_symbol_no_metadata(self):
        linker = EventLinker()
        e = MarketEvent(
            event_type=EventType.POLICY,
            event_date=date(2026, 5, 10),
            title="x",
        )
        result = await linker.link_event_to_stocks(e)
        assert result == []
