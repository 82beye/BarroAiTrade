"""
Legacy SQLite CREATE TABLES SQL — BAR-56a fallback.

DATABASE_URL 미설정 시 기존 회귀 240 passed 테스트가 SQLite 위에서 통과해야 하므로 본 SQL 을 init_db 에서 사용.
BAR-56b 머지 후 (운영 정식 do) 본 모듈은 삭제.
"""

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
    metadata    TEXT,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,
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

__all__ = ["CREATE_TABLES_SQL"]
