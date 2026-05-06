"""
SQLite → Postgres 17 타입 매핑 — BAR-56 design §4.

단위 테스트 가능한 dict. 마이그레이션 스크립트 / 운영 문서에서 참조.
"""

SQLITE_TO_PG: dict[str, str] = {
    "INTEGER PRIMARY KEY AUTOINCREMENT": "BIGSERIAL PRIMARY KEY",
    "TEXT": "TEXT",
    "REAL": "DOUBLE PRECISION",
    "INTEGER": "BIGINT",
    "JSON": "JSONB",
    "TIMESTAMP": "TIMESTAMPTZ",
}

__all__ = ["SQLITE_TO_PG"]
