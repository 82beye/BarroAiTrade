"""BAR-56 — SQLite → Postgres 타입 매핑 검증."""
from __future__ import annotations


def test_sqlite_to_pg_basic_mapping():
    from backend.db._type_map import SQLITE_TO_PG
    assert SQLITE_TO_PG["TEXT"] == "TEXT"
    assert SQLITE_TO_PG["REAL"] == "DOUBLE PRECISION"
    assert SQLITE_TO_PG["INTEGER"] == "BIGINT"
    assert SQLITE_TO_PG["JSON"] == "JSONB"
    assert SQLITE_TO_PG["TIMESTAMP"] == "TIMESTAMPTZ"
    assert (
        SQLITE_TO_PG["INTEGER PRIMARY KEY AUTOINCREMENT"]
        == "BIGSERIAL PRIMARY KEY"
    )


def test_sqlite_to_pg_is_dict_str_str():
    from backend.db._type_map import SQLITE_TO_PG
    assert isinstance(SQLITE_TO_PG, dict)
    for k, v in SQLITE_TO_PG.items():
        assert isinstance(k, str)
        assert isinstance(v, str)
