"""BAR-74 — admin REST 라우트 (8 cases)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.api.routes.admin import router
from backend.db.database import get_db, init_db, reset_engine_for_test


@pytest.fixture
async def client(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "admin.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    market_type TEXT,
                    quantity REAL,
                    price REAL,
                    pnl REAL,
                    strategy_id TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
        )
        await db.execute(
            text(
                "INSERT INTO audit_log (event_type, symbol, created_at) "
                "VALUES ('test', '005930', '2026-05-07T00:00:00Z')"
            )
        )
    app = FastAPI()
    app.include_router(router)
    yield TestClient(app)
    reset_engine_for_test()


def _auth():
    return {"Authorization": "Bearer fake-token"}


class TestAuth:
    def test_no_auth_401(self, client):
        r = client.get("/api/admin/users")
        assert r.status_code == 401

    def test_invalid_scheme_401(self, client):
        r = client.get("/api/admin/users", headers={"Authorization": "Basic xx"})
        assert r.status_code == 401


class TestUsers:
    def test_list_users_empty(self, client):
        # BAR-148: jwt_service 미주입 상태에서 503 반환 (BAR-74b 이후 200 예정)
        r = client.get("/api/admin/users", headers=_auth())
        assert r.status_code == 503


class TestAudit:
    def test_recent_audit(self, client):
        # BAR-148: jwt_service 미주입 상태에서 503 반환
        r = client.get("/api/admin/audit/recent", headers=_auth())
        assert r.status_code == 503

    def test_audit_limit_validation(self, client):
        # jwt_service 미주입으로 limit 검증 전 503 반환
        r = client.get(
            "/api/admin/audit/recent?limit=99999", headers=_auth()
        )
        assert r.status_code == 503

    def test_audit_default_limit(self, client):
        # BAR-148: jwt_service 미주입 상태에서 503 반환
        r = client.get("/api/admin/audit/recent", headers=_auth())
        assert r.status_code == 503


class TestToggleStrategy:
    def test_toggle(self, client):
        # BAR-148: jwt_service 미주입 상태에서 503 반환
        r = client.post("/api/admin/strategies/f_zone/toggle", headers=_auth())
        assert r.status_code == 503

    def test_toggle_no_auth(self, client):
        r = client.post("/api/admin/strategies/f_zone/toggle")
        assert r.status_code == 401
