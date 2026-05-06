"""BAR-62 — REST 엔드포인트 (10 cases)."""
from __future__ import annotations

import json
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.routes.themes_calendar_news import router
from backend.db.database import get_db, init_db, reset_engine_for_test


@pytest.fixture
async def client(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "api_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        # 필요 테이블 fixture 생성
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
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    UNIQUE(source, source_id)
                )
                """
            )
        )
        # seed
        await db.execute(text("INSERT INTO themes (name) VALUES ('AI')"))
        await db.execute(
            text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (1, '005930', 0.9)")
        )
        await db.execute(
            text(
                "INSERT INTO market_events (event_type, symbol, event_date, title, source) "
                "VALUES ('earnings', '005930', '2026-05-10', '실적 발표', 'manual')"
            )
        )
        await db.execute(
            text(
                "INSERT INTO news_items (source, source_id, title, url, "
                "published_at, fetched_at, tags) "
                "VALUES ('dart', 'x1', '뉴스1', 'https://x', '2026-05-10', '2026-05-10', '[]')"
            )
        )

    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    reset_engine_for_test()


class TestThemes:
    def test_list_themes(self, client):
        r = client.get("/api/themes")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "AI"

    def test_get_theme_stocks_ok(self, client):
        r = client.get("/api/themes/1/stocks")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["symbol"] == "005930"
        assert data[0]["theme_name"] == "AI"

    def test_get_theme_stocks_404(self, client):
        r = client.get("/api/themes/999/stocks")
        assert r.status_code == 404


class TestCalendar:
    def test_list_events(self, client):
        r = client.get("/api/calendar?start=2026-05-01&end=2026-05-31")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["event_type"] == "earnings"

    def test_start_after_end_422(self, client):
        r = client.get("/api/calendar?start=2026-05-31&end=2026-05-01")
        assert r.status_code == 422

    def test_events_by_symbol(self, client):
        r = client.get("/api/calendar/symbol/005930")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1


class TestNews:
    def test_recent_news(self, client):
        r = client.get("/api/news/recent")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["source"] == "dart"

    def test_recent_news_filter_source(self, client):
        r = client.get("/api/news/recent?source=dart")
        assert r.status_code == 200
        assert all(n["source"] == "dart" for n in r.json())

    def test_recent_news_limit_validation(self, client):
        r = client.get("/api/news/recent?limit=1000")
        assert r.status_code == 422  # le=500


class TestSchemaValidation:
    def test_invalid_date_format(self, client):
        r = client.get("/api/calendar?start=invalid&end=2026-05-31")
        assert r.status_code == 422
