"""BAR-56 — DATABASE_URL 미설정 시 audit_repo 가 SQLite fallback 으로 정상 동작.

회귀 240 passed 가 SQLite 위에서 통과 중인 상황과 호환되어야 함.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest


@pytest.fixture
def isolated_sqlite(monkeypatch, tmp_path):
    """Postgres URL 제거 + 임시 SQLite 사용."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "audit_test.db"
    monkeypatch.setenv("DB_PATH", str(db_file))

    from backend.db.database import reset_engine_for_test
    reset_engine_for_test()
    yield db_file
    reset_engine_for_test()


@pytest.mark.asyncio
async def test_insert_and_find_recent_on_sqlite(isolated_sqlite):
    from backend.db.database import init_db
    from backend.db.repositories.audit_repo import audit_repo

    await init_db(str(isolated_sqlite))
    ok = await audit_repo.insert(
        event_type="test_event",
        symbol="005930",
        market_type="stock",
        quantity=10.0,
        price=70000.0,
        metadata={"foo": "bar"},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert ok is True

    rows = await audit_repo.find_recent(limit=10)
    assert len(rows) >= 1
    assert any(r["event_type"] == "test_event" for r in rows)


@pytest.mark.asyncio
async def test_find_recent_filter_by_event_type(isolated_sqlite):
    from backend.db.database import init_db
    from backend.db.repositories.audit_repo import audit_repo

    await init_db(str(isolated_sqlite))
    await audit_repo.insert(
        event_type="alpha",
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    await audit_repo.insert(
        event_type="beta",
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    rows = await audit_repo.find_recent(limit=100, event_type="alpha")
    assert len(rows) >= 1
    assert all(r["event_type"] == "alpha" for r in rows)
