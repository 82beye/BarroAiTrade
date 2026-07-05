"""테마 스냅숏(타임라인) + 종목→테마 역조회 테스트 (티마 앱 벤치마킹 P1)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.core.state import app_state
from backend.core.themes import snapshot as snapshot_module
from backend.db.database import get_db, init_db, reset_engine_for_test


@pytest.fixture
async def seeded_db(monkeypatch, tmp_path):
    """themes/theme_stocks 3테이블 + 샘플 데이터 (SQLite 격리)."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "snap_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    # gateway 미초기화 → enrich no-op (시세 null)
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
            text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (1, '161890', 0.7)")
        )
        await db.execute(
            text("INSERT INTO theme_stocks (theme_id, symbol, score) VALUES (2, '005930', 0.5)")
        )
    yield db_file
    reset_engine_for_test()


@pytest.fixture
def client(seeded_db, monkeypatch, tmp_path):
    # 스냅숏 저장 디렉터리 격리
    snap_dir = tmp_path / "theme_snapshots"
    monkeypatch.setattr(snapshot_module, "_snapshots_dir", lambda: snap_dir)

    from backend.api.routes.themes_calendar_news import router as tcn_router

    app = FastAPI()
    app.include_router(tcn_router)
    return TestClient(app)


# ── 스냅숏 capture → 조회 라운드트립 ──────────────────────────────────────────


class TestSnapshotCaptureAndLoad:
    def test_capture_then_get_slot(self, client):
        r = client.post("/api/themes/snapshots/capture?slot=10:00")
        assert r.status_code == 200
        cap = r.json()
        assert cap["status"] == "ok"
        assert cap["slot"] == "10:00"
        assert cap["theme_count"] == 2

        date_str = cap["date"]
        r2 = client.get(f"/api/themes/snapshots?date={date_str}&slot=10:00")
        assert r2.status_code == 200
        snap = r2.json()
        assert snap["slot"] == "10:00"
        assert snap["date"] == date_str
        assert len(snap["themes"]) == 2
        # 동결된 종목 (score desc), gateway 없어 price null
        kbeauty = next(t for t in snap["themes"] if t["name"] == "K뷰티")
        assert [s["symbol"] for s in kbeauty["stocks"]] == ["005930", "161890"]
        assert kbeauty["stocks"][0]["price"] is None

    def test_slot_list_when_no_slot_param(self, client):
        client.post("/api/themes/snapshots/capture?slot=10:00")
        client.post("/api/themes/snapshots/capture?slot=15:35")
        # 오늘 날짜 slots 조회 (VALID_SLOTS 순서 유지)
        r = client.get("/api/themes/snapshots")
        data = r.json()
        assert data["slots"] == ["10:00", "15:35"]

    def test_invalid_slot_422_on_capture(self, client):
        r = client.post("/api/themes/snapshots/capture?slot=09:00")
        assert r.status_code == 422

    def test_invalid_slot_422_on_get(self, client):
        r = client.get("/api/themes/snapshots?slot=09:00")
        assert r.status_code == 422

    def test_no_data_for_missing_snapshot(self, client):
        r = client.get("/api/themes/snapshots?date=2020-01-01&slot=12:30")
        assert r.status_code == 200
        assert r.json()["status"] == "no_data"


# ── 종목 → 테마 역조회 ────────────────────────────────────────────────────────


class TestStockThemes:
    def test_reverse_lookup_score_desc(self, client):
        r = client.get("/api/stocks/005930/themes")
        assert r.status_code == 200
        data = r.json()
        assert data["symbol"] == "005930"
        # 005930 은 K뷰티(0.9) + 로봇(0.5) → score desc
        assert [t["name"] for t in data["themes"]] == ["K뷰티", "로봇"]
        assert data["themes"][0]["description"] == "화장품株"
        assert data["themes"][0]["score"] == 0.9

    def test_reverse_lookup_no_themes(self, client):
        r = client.get("/api/stocks/999999/themes")
        assert r.status_code == 200
        assert r.json()["themes"] == []
