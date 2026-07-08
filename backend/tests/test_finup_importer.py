"""Finup 테마 스냅숏 임포터 — 출처 범위 한정 삭제(source-scoped wipe) 검증.

핵심 회귀 방지: Finup 재수입(replace=True)이 큐레이션/뉴스발굴 등 다른 출처의
테마까지 지우면 안 된다(docs/03-analysis/2026-07-08-theme-implementation-
issues-and-fix-design.md §2-B).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from backend.core.themes.finup_importer import import_finup_theme_snapshot
from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.theme_repo import ThemeRepository


@pytest.fixture
async def theme_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "finup_importer_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
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
                CREATE TABLE IF NOT EXISTS theme_stocks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    score REAL NOT NULL,
                    UNIQUE(theme_id, symbol)
                )
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS theme_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    theme_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    UNIQUE(theme_id, keyword)
                )
                """
            )
        )
    yield db_file
    reset_engine_for_test()


def _write_snapshot(tmp_path, themes: list[dict]) -> "type[str]":
    path = tmp_path / "finup_snapshot.json"
    path.write_text(
        json.dumps({"metadata": {"source": "finup"}, "themes": themes}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _theme_item(name: str, stocks: list[tuple[str, float]]) -> dict:
    return {
        "theme": {"theme_name": name},
        "relation_stocks": [
            {"stockCode": sym, "keyword": f"{sym}-name", "diff": diff} for sym, diff in stocks
        ],
    }


class TestFinupImporterSourceScoping:
    @pytest.mark.asyncio
    async def test_first_import_creates_themes(self, theme_db, tmp_path):
        snap = _write_snapshot(tmp_path, [_theme_item("AI(인공지능)", [("005930", 1.2)])])
        result = await import_finup_theme_snapshot(snap, update_stock_names=False)
        assert result["status"] == "ok"
        assert result["theme_count"] == 1
        assert result["stock_count"] == 1

    @pytest.mark.asyncio
    async def test_replace_preserves_non_finup_themes(self, theme_db, tmp_path):
        repo = ThemeRepository()
        curated_id = await repo.upsert_theme("반도체", description="큐레이션 시드")
        await repo.link_stock(curated_id, "000660", 3.0)
        news_id = await repo.upsert_theme("전력망 증설", description="뉴스기반 자동발굴")
        await repo.link_stock(news_id, "010120", 1.0)

        snap = _write_snapshot(tmp_path, [_theme_item("AI(인공지능)", [("005930", 1.2)])])
        result = await import_finup_theme_snapshot(snap, update_stock_names=False)
        assert result["theme_count"] == 1

        async with get_db() as db:
            names_res = await db.execute(text("SELECT name FROM themes ORDER BY name"))
            names = {r["name"] for r in names_res.mappings().all()}
            curated_stock_res = await db.execute(
                text("SELECT symbol FROM theme_stocks WHERE theme_id = :tid"), {"tid": curated_id}
            )
            curated_symbols = {r["symbol"] for r in curated_stock_res.mappings().all()}

        assert "반도체" in names
        assert "전력망 증설" in names
        assert "AI(인공지능)" in names
        assert curated_symbols == {"000660"}  # 큐레이션 테마 종목 그대로

    @pytest.mark.asyncio
    async def test_second_finup_import_clears_first_finup_themes_only(self, theme_db, tmp_path):
        repo = ThemeRepository()
        curated_id = await repo.upsert_theme("반도체", description="큐레이션 시드")
        await repo.link_stock(curated_id, "000660", 3.0)

        snap1 = _write_snapshot(tmp_path, [_theme_item("DDR5", [("005930", 1.0)])])
        await import_finup_theme_snapshot(snap1, update_stock_names=False)

        snap2 = _write_snapshot(tmp_path, [_theme_item("3D 낸드", [("042700", 2.0)])])
        result2 = await import_finup_theme_snapshot(snap2, update_stock_names=False)
        assert result2["theme_count"] == 1

        async with get_db() as db:
            names_res = await db.execute(text("SELECT name FROM themes ORDER BY name"))
            names = {r["name"] for r in names_res.mappings().all()}

        assert names == {"반도체", "3D 낸드"}  # DDR5(1차 finup) 는 삭제, 큐레이션은 유지

    @pytest.mark.asyncio
    async def test_name_collision_with_non_finup_theme_upserts_not_crashes(
        self, theme_db, tmp_path
    ):
        repo = ThemeRepository()
        existing_id = await repo.upsert_theme("방산", description="큐레이션 시드")
        await repo.link_stock(existing_id, "079550", 5.0)

        snap = _write_snapshot(tmp_path, [_theme_item("방산", [("012450", 9.0)])])
        result = await import_finup_theme_snapshot(snap, update_stock_names=False)

        assert result["status"] == "ok"
        assert result["skipped_themes"] == 0

        async with get_db() as db:
            theme_res = await db.execute(text("SELECT id, description FROM themes WHERE name = '방산'"))
            rows = theme_res.mappings().all()
            symbols_res = await db.execute(
                text("SELECT symbol FROM theme_stocks WHERE theme_id = :tid"), {"tid": existing_id}
            )
            symbols = {r["symbol"] for r in symbols_res.mappings().all()}

        assert len(rows) == 1  # UNIQUE(name) 위반 없이 동일 row 로 합쳐짐
        assert rows[0]["id"] == existing_id
        assert "Finup" in rows[0]["description"]
        assert symbols == {"079550", "012450"}  # 기존+신규 종목 공존

    @pytest.mark.asyncio
    async def test_reimport_same_snapshot_no_duplicate_rows(self, theme_db, tmp_path):
        snap = _write_snapshot(tmp_path, [_theme_item("로봇", [("005930", 1.0)])])
        await import_finup_theme_snapshot(snap, update_stock_names=False)
        await import_finup_theme_snapshot(snap, update_stock_names=False)

        async with get_db() as db:
            count_res = await db.execute(text("SELECT COUNT(*) AS c FROM themes WHERE name = '로봇'"))
            count = count_res.mappings().first()["c"]

        assert count == 1
