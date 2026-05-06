"""BAR-56 — backend/db/database.py 어댑터 단위 검증."""
from __future__ import annotations

import inspect
import os

import pytest


def test_imports_get_db_init_db():
    from backend.db.database import get_db, init_db
    assert callable(get_db)
    assert inspect.iscoroutinefunction(init_db)


def test_get_db_is_asynccontextmanager():
    from backend.db.database import get_db
    # get_db 자체는 함수 (decorator 적용 결과는 _AsyncGeneratorContextManager 반환)
    cm = get_db()
    # async context manager 프로토콜
    assert hasattr(cm, "__aenter__")
    assert hasattr(cm, "__aexit__")


def test_init_db_legacy_db_path_kwarg(monkeypatch, tmp_path):
    """legacy 호출 시그니처 init_db(db_path=...) 호환."""
    from backend.db import database
    from backend.db.database import init_db, reset_engine_for_test

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    reset_engine_for_test()

    db_file = tmp_path / "legacy.db"
    # await 후 파일 생성 검증
    import asyncio
    asyncio.run(init_db(str(db_file)))
    assert db_file.exists()
    reset_engine_for_test()


def test_resolve_database_url_sqlite_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    from backend.db.database import _resolve_database_url
    url = _resolve_database_url()
    assert url.startswith("sqlite+aiosqlite:///")


def test_resolve_database_url_postgres_when_env_set(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:5432/db")
    from backend.db.database import _resolve_database_url
    url = _resolve_database_url()
    assert url == "postgresql+asyncpg://u:p@h:5432/db"
    monkeypatch.delenv("DATABASE_URL")
