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

-- BAR-62/104(테마·마켓일정·뉴스) — 기존엔 라우트·레포만 있고 스키마 부트스트랩이
-- 빠져 있어 실 DB(themes 테이블 부재)에서 /api/themes 가 500 으로 크래시했다.
-- (사전 존재 결함, 이번에 수정) 라우트/레포의 실제 쿼리 컬럼과 정합된 DDL.
CREATE TABLE IF NOT EXISTS themes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS theme_keywords (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id  INTEGER NOT NULL REFERENCES themes(id) ON DELETE CASCADE,
    keyword   TEXT    NOT NULL,
    UNIQUE(theme_id, keyword)
);

CREATE TABLE IF NOT EXISTS theme_stocks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    theme_id  INTEGER NOT NULL,
    symbol    TEXT    NOT NULL,
    score     REAL    NOT NULL,
    UNIQUE(theme_id, symbol)
);

CREATE TABLE IF NOT EXISTS market_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type  TEXT    NOT NULL,
    symbol      TEXT,
    event_date  TEXT    NOT NULL,
    title       TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'manual',
    metadata    TEXT    NOT NULL DEFAULT '{}',
    UNIQUE(symbol, event_date, event_type)
);

CREATE TABLE IF NOT EXISTS news_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source       TEXT    NOT NULL,
    source_id    TEXT    NOT NULL,
    title        TEXT    NOT NULL,
    body         TEXT    NOT NULL DEFAULT '',
    url          TEXT    NOT NULL,
    published_at TEXT    NOT NULL,
    fetched_at   TEXT    NOT NULL,
    tags         TEXT    NOT NULL DEFAULT '[]',
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_theme_stocks_theme_id ON theme_stocks(theme_id);
CREATE INDEX IF NOT EXISTS idx_theme_stocks_symbol ON theme_stocks(symbol);
CREATE INDEX IF NOT EXISTS idx_market_events_date ON market_events(event_date);
CREATE INDEX IF NOT EXISTS idx_news_items_published ON news_items(published_at);
"""

__all__ = ["CREATE_TABLES_SQL"]
