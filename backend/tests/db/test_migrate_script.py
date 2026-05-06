"""BAR-56 — scripts/migrate_sqlite_to_postgres.py 단위 검증."""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_migrate_script_help_exits_zero():
    """--help 가 정상 종료."""
    result = subprocess.run(
        [sys.executable, "scripts/migrate_sqlite_to_postgres.py", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[3],
        timeout=15,
    )
    assert result.returncode == 0
    assert "BAR-56" in result.stdout or "migrate" in result.stdout.lower()


@pytest.mark.asyncio
async def test_migrate_dry_run_does_not_copy(tmp_path, monkeypatch):
    """--dry-run 에서는 copy_records_to_table 호출 0회."""
    import sqlite3

    # 임시 SQLite — audit_log/trades 테이블 + 1행씩
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT,
            symbol TEXT, market_type TEXT, quantity REAL, price REAL, pnl REAL,
            strategy_id TEXT, metadata TEXT, created_at TEXT);
        CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
            side TEXT, order_type TEXT, quantity REAL, price REAL, strategy_id TEXT,
            order_id TEXT, status TEXT, created_at TEXT);
        INSERT INTO audit_log (event_type, metadata, created_at) VALUES ('e', '{}', '2026-05-07T00:00:00Z');
        INSERT INTO trades (symbol, side, order_type, quantity, price, created_at)
            VALUES ('005930', 'buy', 'market', 10, 70000, '2026-05-07T00:00:00Z');
        """
    )
    con.commit()
    con.close()

    # asyncpg.connect mock
    fake_pg = MagicMock()
    fake_pg.fetchval = AsyncMock(return_value=0)
    fake_pg.copy_records_to_table = AsyncMock()
    fake_pg.close = AsyncMock()
    fake_pg.transaction = MagicMock()

    fake_module = MagicMock()
    fake_module.connect = AsyncMock(return_value=fake_pg)

    with patch.dict(sys.modules, {"asyncpg": fake_module}):
        from scripts import migrate_sqlite_to_postgres as m
        rc = await m.migrate(str(db), "postgresql://x", dry_run=True)

    assert rc == 0
    fake_pg.copy_records_to_table.assert_not_awaited()
    fake_pg.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_rows_from_sqlite_parses_metadata(tmp_path):
    import sqlite3
    db = tmp_path / "src.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute(
        "CREATE TABLE audit_log (metadata TEXT, event_type TEXT, created_at TEXT)"
    )
    con.execute(
        "INSERT INTO audit_log (metadata, event_type, created_at) VALUES (?, ?, ?)",
        ('{"k":"v"}', "e", "2026-05-07"),
    )
    con.commit()

    from scripts.migrate_sqlite_to_postgres import _rows_from_sqlite
    rows = list(_rows_from_sqlite(con, "audit_log"))
    con.close()

    assert len(rows) == 1
    assert rows[0]["metadata"] == {"k": "v"}
