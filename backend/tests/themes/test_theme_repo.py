"""BAR-59 — ThemeRepository (4 cases). SQLite fallback 위에서 검증."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.theme_repo import ThemeRepository


@pytest.fixture
async def isolated_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "theme_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    # SQLite 에 themes 3 테이블 직접 생성
    async with get_db() as db:
        await db.execute(text("PRAGMA foreign_keys=ON"))
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS themes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS theme_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
                    keyword TEXT NOT NULL,
                    UNIQUE(theme_id, keyword)
                )
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS theme_stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme_id INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
                    symbol TEXT NOT NULL,
                    score REAL NOT NULL,
                    UNIQUE(theme_id, symbol)
                )
                """
            )
        )
    yield db_file
    reset_engine_for_test()


class TestThemeRepo:
    @pytest.mark.asyncio
    async def test_upsert_theme_returns_id(self, isolated_db):
        repo = ThemeRepository()
        id1 = await repo.upsert_theme("전기차", "EV")
        assert id1 is not None and id1 > 0
        # 재호출 시 동일 id
        id2 = await repo.upsert_theme("전기차", "modified")
        assert id1 == id2

    @pytest.mark.asyncio
    async def test_link_stock_and_find(self, isolated_db):
        repo = ThemeRepository()
        tid = await repo.upsert_theme("AI")
        ok = await repo.link_stock(tid, "005930", 0.85)
        assert ok is True
        rows = await repo.find_themes_by_stock("005930")
        assert len(rows) == 1
        assert rows[0]["name"] == "AI"
        assert abs(rows[0]["score"] - 0.85) < 1e-6

    @pytest.mark.asyncio
    async def test_add_keyword(self, isolated_db):
        repo = ThemeRepository()
        tid = await repo.upsert_theme("반도체")
        ok = await repo.add_keyword(tid, "메모리")
        assert ok is True
        # 중복 → False
        ok2 = await repo.add_keyword(tid, "메모리")
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_fk_cascade_on_theme_delete(self, isolated_db):
        repo = ThemeRepository()
        tid = await repo.upsert_theme("바이오")
        await repo.link_stock(tid, "207940", 0.9)
        # theme 삭제 → theme_stocks 도 CASCADE 삭제
        async with get_db() as db:
            await db.execute(
                text("DELETE FROM themes WHERE id = :id"), {"id": tid}
            )
        rows = await repo.find_themes_by_stock("207940")
        assert rows == []
