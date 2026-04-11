"""
Database — aiosqlite 기반 비동기 SQLite 연결 관리

감사 로그, 매매 내역, 포지션 스냅샷 저장에 사용.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

try:
    import aiosqlite
    _AIOSQLITE_AVAILABLE = True
except ImportError:
    _AIOSQLITE_AVAILABLE = False

logger = logging.getLogger(__name__)

_DB_PATH = os.getenv("DB_PATH", "data/barro_trade.db")

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    symbol      TEXT,
    market_type TEXT,
    quantity    REAL,
    price       REAL,
    pnl         REAL,
    strategy_id TEXT,
    metadata    TEXT,   -- JSON
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,   -- buy | sell
    order_type  TEXT    NOT NULL,
    quantity    REAL    NOT NULL,
    price       REAL    NOT NULL,
    strategy_id TEXT,
    order_id    TEXT,
    status      TEXT,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
"""


async def init_db(db_path: str = _DB_PATH) -> None:
    """DB 파일 생성 및 테이블 초기화"""
    if not _AIOSQLITE_AVAILABLE:
        logger.warning("aiosqlite 미설치 — DB 영속성 비활성화 (인메모리 모드로 동작)")
        return

    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(CREATE_TABLES_SQL)
        await db.commit()
    logger.info("DB 초기화 완료: %s", db_path)


@asynccontextmanager
async def get_db(db_path: str = _DB_PATH) -> AsyncGenerator:
    """DB 연결 컨텍스트 매니저"""
    if not _AIOSQLITE_AVAILABLE:
        yield None
        return

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db
