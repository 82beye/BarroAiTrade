"""
BAR-56 — SQLite → Postgres 데이터 마이그레이션.

본 BAR-56a 단계에서는 코드/단위 테스트만 정식. 실 실행은 BAR-56b.

플로우:
    1. SQLite source 연결 → audit_log / trades row count 측정
    2. Postgres target 연결 (DATABASE_URL) → row count 측정 (사전)
    3. (--dry-run 이 아닐 때) 트랜잭션 단위로 COPY (asyncpg.copy_records_to_table)
       audit_log.metadata: TEXT(JSON) → dict (JSONB 입력)
    4. 사후 row count 검증 — source 행 수가 target 증분과 일치
    5. 실패 시 트랜잭션 롤백
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from typing import Iterable

logger = logging.getLogger(__name__)

TABLES = ("audit_log", "trades")


def _count_sqlite(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _rows_from_sqlite(con: sqlite3.Connection, table: str) -> Iterable[dict]:
    cur = con.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    for row in cur:
        d = dict(zip(cols, row))
        if table == "audit_log" and isinstance(d.get("metadata"), str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except Exception:
                d["metadata"] = {}
        yield d


async def _count_pg(conn, table: str) -> int:
    return await conn.fetchval(f"SELECT count(*) FROM {table}")


async def migrate(sqlite_path: str, pg_dsn: str, dry_run: bool) -> int:
    """동작 핵심 — 단위 테스트는 mock 으로 sqlite3/asyncpg.connect 가짜 주입."""
    import asyncpg

    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    pg = await asyncpg.connect(pg_dsn)
    report: dict = {}

    try:
        for t in TABLES:
            src_n = _count_sqlite(src, t)
            tgt_n_before = await _count_pg(pg, t)
            report[t] = {"src": src_n, "tgt_before": tgt_n_before}

            if dry_run:
                continue

            rows = list(_rows_from_sqlite(src, t))
            if rows:
                cols = list(rows[0].keys())
                async with pg.transaction():
                    await pg.copy_records_to_table(
                        t,
                        records=[tuple(r[c] for c in cols) for r in rows],
                        columns=cols,
                    )

            report[t]["tgt_after"] = await _count_pg(pg, t)

        if not dry_run:
            for t in TABLES:
                inc = report[t]["tgt_after"] - report[t]["tgt_before"]
                if report[t]["src"] != inc:
                    raise RuntimeError(
                        f"row count mismatch on {t}: src={report[t]['src']} inc={inc}"
                    )

        logger.info("migrate report: %s", report)
        return 0
    finally:
        src.close()
        await pg.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="BAR-56 SQLite → Postgres migrate")
    ap.add_argument("--sqlite", default="data/barro_trade.db")
    ap.add_argument(
        "--pg-dsn",
        required=True,
        help="postgresql://barro:barro@localhost:5432/barro",
    )
    ap.add_argument("--dry-run", action="store_true", help="count only, no apply")
    args = ap.parse_args()
    rc = asyncio.run(migrate(args.sqlite, args.pg_dsn, args.dry_run))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
