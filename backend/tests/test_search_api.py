"""통합검색(P2) + NXT 시세 스텁 테스트 (티마 앱 벤치마킹 §3.4/§5)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.core.state import app_state
from backend.db.database import get_db, init_db, reset_engine_for_test


@pytest.fixture
async def seeded_db(monkeypatch, tmp_path):
    """themes/theme_stocks 테이블 + 샘플 데이터 (SQLite 격리)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "search_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setattr(app_state, "market_gateway", None, raising=False)
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        await db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS themes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, "
                "description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT '')"
            )
        )
        await db.execute(
            text(
                "CREATE TABLE IF NOT EXISTS theme_stocks ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, theme_id INTEGER NOT NULL, "
                "symbol TEXT NOT NULL, score REAL NOT NULL, UNIQUE(theme_id, symbol))"
            )
        )
        await db.execute(
            text("INSERT INTO themes (id, name, description) VALUES (1, 'K뷰티', '화장품株')")
        )
        await db.execute(
            text("INSERT INTO themes (id, name, description) VALUES (2, '로봇', '피지컬AI')")
        )
        await db.execute(
            text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (1, '005930', 0.9)")
        )
        await db.execute(
            text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (2, '000660', 0.5)")
        )
    yield db_file
    reset_engine_for_test()


@pytest.fixture
def refined_file(monkeypatch, tmp_path):
    """refined_signals.json 경로를 tmp 로 격리 (symbol+한글명 제공)."""
    path = tmp_path / "refined_signals.json"
    path.write_text(
        json.dumps(
            {
                "signals": [
                    {"symbol": "005930", "name": "삼성전자", "signal_type": "f_zone"},
                    {"symbol": "000660", "name": "SK하이닉스", "signal_type": "sf_zone"},
                ]
            }
        ),
        encoding="utf-8",
    )
    from backend.api.routes import search as search_module

    monkeypatch.setattr(search_module, "_refined_path", lambda: path)
    return path


@pytest.fixture
def client(seeded_db, refined_file):
    from backend.api.routes.market import router as market_router
    from backend.api.routes.search import router as search_router

    app = FastAPI()
    app.include_router(market_router, prefix="/api")
    app.include_router(search_router, prefix="/api")
    return TestClient(app)


# ── 통합검색 ────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_by_symbol_code(self, client):
        r = client.get("/api/search?q=005930")
        assert r.status_code == 200
        body = r.json()
        assert body["query"] == "005930"
        stocks = [x for x in body["results"] if x["type"] == "stock"]
        assert any(s["symbol"] == "005930" for s in stocks)

    def test_search_by_partial_code(self, client):
        r = client.get("/api/search?q=0066")
        assert r.status_code == 200
        stocks = [x for x in r.json()["results"] if x["type"] == "stock"]
        assert any(s["symbol"] == "000660" for s in stocks)

    def test_search_by_korean_name(self, client):
        r = client.get("/api/search?q=삼성")
        assert r.status_code == 200
        stocks = [x for x in r.json()["results"] if x["type"] == "stock"]
        assert any(s["name"] == "삼성전자" for s in stocks)

    def test_search_by_theme_name(self, client):
        r = client.get("/api/search?q=로봇")
        assert r.status_code == 200
        themes = [x for x in r.json()["results"] if x["type"] == "theme"]
        assert any(t["name"] == "로봇" and t["id"] == 2 for t in themes)

    def test_search_case_insensitive_theme(self, client):
        # 영문 대소문자 무시 (K뷰티)
        r = client.get("/api/search?q=k뷰티")
        assert r.status_code == 200
        themes = [x for x in r.json()["results"] if x["type"] == "theme"]
        assert any(t["name"] == "K뷰티" for t in themes)

    def test_empty_query_422(self, client):
        r = client.get("/api/search?q=")
        assert r.status_code == 422

    def test_no_match_empty_results(self, client):
        r = client.get("/api/search?q=존재하지않는종목명ZZZ")
        assert r.status_code == 200
        assert r.json()["results"] == []

    def test_limit_respected(self, client):
        r = client.get("/api/search?q=0&limit=1")
        assert r.status_code == 200
        stocks = [x for x in r.json()["results"] if x["type"] == "stock"]
        assert len(stocks) <= 1


# ── NXT 시세 스텁 ────────────────────────────────────────────────────────────


class TestNxtStub:
    def test_nxt_default_unsupported(self, client):
        r = client.get("/api/market/nxt")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("unsupported", "not_ready")
        assert body["items"] == []
        assert body["filter"] == "value"

    @pytest.mark.parametrize("f", ["value", "gainers", "losers"])
    def test_nxt_valid_filters(self, client, f):
        r = client.get(f"/api/market/nxt?filter={f}")
        assert r.status_code == 200
        assert r.json()["filter"] == f
        assert r.json()["items"] == []

    def test_nxt_invalid_filter_422(self, client):
        r = client.get("/api/market/nxt?filter=bogus")
        assert r.status_code == 422
