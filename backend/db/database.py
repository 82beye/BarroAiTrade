"""
Database — SQLAlchemy AsyncEngine + asyncpg (Postgres 17) / aiosqlite fallback.

BAR-56a 정책:
- 외부 시그니처 (`get_db()`, `init_db()`) 보존 → audit_repo 외 호출자 (main.py / orchestrator.py)
  변경 0건.
- DATABASE_URL 미설정 시 SQLite 자동 fallback (회귀 240 passed 보존).
- DATABASE_URL 설정 시 Postgres + asyncpg.
- SQLite fallback 제거는 BAR-56b PR 에서 별도 진행.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    create_async_engine,
)

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None
_DEFAULT_SQLITE_PATH = "data/barro_trade.db"


def _resolve_database_url(db_path: Optional[str] = None) -> str:
    """DATABASE_URL > POSTGRES_URL 우선, 없으면 SQLite fallback."""
    explicit = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if explicit:
        return explicit
    sqlite_path = db_path or os.getenv("DB_PATH") or _DEFAULT_SQLITE_PATH
    return f"sqlite+aiosqlite:///{sqlite_path}"


def _ensure_engine(db_path: Optional[str] = None) -> AsyncEngine:
    """전역 AsyncEngine — lazy init."""
    global _engine
    if _engine is None:
        url = _resolve_database_url(db_path)
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        # SQLite 는 pool_size 옵션 미지원 — 기본 dialect 풀 사용
        if not url.startswith("sqlite"):
            kwargs.update({"pool_size": 5, "max_overflow": 10})
        _engine = create_async_engine(url, **kwargs)
        logger.info("DB engine 초기화: dialect=%s", _engine.dialect.name)
    return _engine


def reset_engine_for_test() -> None:
    """테스트 격리용 — engine 재초기화 강제."""
    global _engine
    _engine = None


async def init_db(db_path: Optional[str] = None) -> None:
    """DB 초기화.

    - SQLite fallback: 기존 CREATE_TABLES_SQL 실행 (legacy 호환).
    - Postgres: alembic upgrade 가 책임 (본 함수는 no-op + log).
    """
    if db_path is not None:
        os.environ.setdefault("DB_PATH", db_path)

    engine = _ensure_engine(db_path)

    if engine.dialect.name == "sqlite":
        # SQLite 파일 디렉터리 보장
        url_path = engine.url.database or ""
        if url_path and url_path not in (":memory:", ""):
            d = os.path.dirname(url_path)
            if d:
                os.makedirs(d, exist_ok=True)

        from backend.db._legacy_sqlite import CREATE_TABLES_SQL

        async with engine.begin() as conn:
            for stmt in CREATE_TABLES_SQL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    await conn.exec_driver_sql(stmt)
        logger.info("DB(sqlite) 초기화 완료: %s", url_path)
    else:
        logger.info(
            "DB(%s) — alembic upgrade head 가 스키마 책임 (init_db no-op)",
            engine.dialect.name,
        )


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncConnection, None]:
    """DB 연결 컨텍스트.

    호출자:
      async with get_db() as db:
          await db.execute(text("SQL :name"), {"name": value})
    트랜잭션 자동 begin/commit (예외 시 rollback).
    """
    engine = _ensure_engine()
    async with engine.connect() as conn:
        async with conn.begin():
            yield conn


__all__ = ["get_db", "init_db", "reset_engine_for_test"]
