"""BAR-65 — TradeNote + JournalRepository (12 cases)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.journal_repo import JournalRepository
from backend.models.journal import Emotion, TradeNote


def _note(trade_id="t1", symbol="005930", side="buy", **kw) -> TradeNote:
    base = dict(
        trade_id=trade_id,
        symbol=symbol,
        side=side,
        qty=Decimal("10"),
        entry_price=Decimal("70000"),
        entry_time=datetime.now(timezone.utc),
    )
    base.update(kw)
    return TradeNote(**base)


@pytest.fixture
async def isolated_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "journal.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS trade_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    pnl REAL,
                    entry_time TEXT NOT NULL,
                    exit_time TEXT,
                    emotion TEXT NOT NULL DEFAULT 'neutral',
                    note TEXT NOT NULL DEFAULT '',
                    tags TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
        )
    yield db_file
    reset_engine_for_test()


class TestModel:
    def test_frozen(self):
        n = _note()
        with pytest.raises(Exception):
            n.note = "x"  # type: ignore[misc]

    def test_emotion_default(self):
        n = _note()
        assert n.emotion == Emotion.NEUTRAL

    def test_side_validation(self):
        with pytest.raises(Exception):
            TradeNote(
                trade_id="x", symbol="005930", side="invalid",
                qty=Decimal("1"), entry_price=Decimal("100"),
                entry_time=datetime.now(timezone.utc),
            )

    def test_qty_positive(self):
        with pytest.raises(Exception):
            TradeNote(
                trade_id="x", symbol="005930", side="buy",
                qty=Decimal("0"), entry_price=Decimal("100"),
                entry_time=datetime.now(timezone.utc),
            )

    def test_tags_tuple(self):
        n = _note(tags=("scalp", "momentum"))
        assert isinstance(n.tags, tuple)


class TestRepo:
    @pytest.mark.asyncio
    async def test_insert_returns_id(self, isolated_db):
        repo = JournalRepository()
        new_id = await repo.insert(_note())
        assert new_id is not None and new_id > 0

    @pytest.mark.asyncio
    async def test_insert_duplicate_returns_none(self, isolated_db):
        repo = JournalRepository()
        await repo.insert(_note(trade_id="dup"))
        again = await repo.insert(_note(trade_id="dup"))
        assert again is None

    @pytest.mark.asyncio
    async def test_find_by_symbol(self, isolated_db):
        repo = JournalRepository()
        await repo.insert(_note(trade_id="t1", symbol="005930"))
        await repo.insert(_note(trade_id="t2", symbol="000660"))
        rows = await repo.find_by_symbol("005930")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_update_emotion(self, isolated_db):
        repo = JournalRepository()
        new_id = await repo.insert(_note(trade_id="emo1"))
        ok = await repo.update_emotion(new_id, Emotion.PROUD)
        assert ok is True
        rows = await repo.find_by_symbol("005930")
        assert rows[0]["emotion"] == "proud"

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_false(self, isolated_db):
        repo = JournalRepository()
        ok = await repo.update_emotion(99999, Emotion.PROUD)
        assert ok is False

    @pytest.mark.asyncio
    async def test_with_pnl_and_exit(self, isolated_db):
        repo = JournalRepository()
        n = _note(
            trade_id="exit1",
            exit_price=Decimal("72000"),
            exit_time=datetime.now(timezone.utc),
            pnl=Decimal("20000"),
            note="좋은 매매",
        )
        new_id = await repo.insert(n)
        assert new_id is not None

    @pytest.mark.asyncio
    async def test_with_tags(self, isolated_db):
        repo = JournalRepository()
        n = _note(trade_id="t_tag", tags=("scalp", "BAR-65"))
        new_id = await repo.insert(n)
        assert new_id is not None
