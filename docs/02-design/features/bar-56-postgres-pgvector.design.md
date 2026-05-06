# BAR-56 — Postgres 17 + pgvector 0.8 Design

**Plan**: `docs/01-plan/features/bar-56-postgres-pgvector.plan.md`
**Phase**: 3 (테마 인텔리전스) — 첫 BAR / 인프라 게이트
**Status**: Draft (council: architect + infra-architect)
**Date**: 2026-05-07
**Worktree**: `/Users/beye/workspace/BarroAiTrade/.claude/worktrees/strange-jackson-3c740a`

> **요약**: 본 문서는 BAR-56 의 설계를 **BAR-56a (worktree 검증 가능)** 와 **BAR-56b (운영 정식)** 로 분리하여, worktree 환경의 docker daemon 부재 제약 하에서도 코드 / 스키마 / Alembic 골격 + 단위 테스트를 정식 do 로 진행할 수 있는 경계를 정의한다.

---

## §0. BAR-56a / BAR-56b 분리 정책 (Scope Split)

본 BAR 의 do 는 worktree 환경 (docker daemon 부재, 실 DB 부재) 에서 검증 가능한 범위로 한정하고, 실 컨테이너 기동·데이터 마이그레이션·통합 테스트는 후속 BAR-56b 로 분리한다.

### 0.1 BAR-56a 스코프 (worktree 정식 do — 본 BAR)

| 카테고리 | 산출물 | 검증 방법 (worktree) |
|----------|--------|----------------------|
| 인프라 정의 | `docker-compose.yml` postgres 서비스 스니펫, `infra/postgres/init.sql` | YAML / SQL 구문 lint (`docker compose config`, `psql --dry-run` 不요) |
| Alembic 골격 | `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_init.py` | Python import + `alembic check` 정적 검증, MetaData inspect 단위 테스트 |
| DB 어댑터 | `backend/db/database.py` 교체 (AsyncEngine + asyncpg) | import + `get_db()` / `init_db()` 시그니처 검증 단위 테스트 |
| 타입 매핑 | 매핑 dict 모듈 (`backend/db/_type_map.py`) | 단위 테스트 (입력 SQLite 타입 → 기대 Postgres 타입) |
| 마이그레이션 스크립트 | `scripts/migrate_sqlite_to_postgres.py` (의사코드 → 실 코드) | `--dry-run` 모드 only, `--help` smoke + 함수 단위 테스트 (모킹) |
| 환경변수 | `.env.example` 갱신 (`DATABASE_URL`) + `backend/config/settings.py` | settings 로드 단위 테스트 |
| 의존성 | `pyproject.toml` 에 `sqlalchemy[asyncio]>=2.0`, `asyncpg`, `alembic>=1.13`, `pgvector` (Python 클라이언트는 BAR-58) | `pip install -e .` smoke (CI) |

**BAR-56a DoD**:
- 단위 테스트 신규 ≥ 12 개 모두 PASS
- 기존 회귀 240 passed 유지 (어댑터 교체 후에도 SQLite fallback 유지 — §5.4 참고)
- gap-detector 매치율 ≥ 90%

### 0.2 BAR-56b 스코프 (운영 환경 정식 do — 후속)

| 카테고리 | 산출물 | 검증 방법 (운영) |
|----------|--------|------------------|
| 컨테이너 기동 | `docker compose up -d postgres` healthcheck PASS | `pg_isready` |
| Extension 활성화 | `init.sql` 자동 실행 검증 | `SELECT extversion FROM pg_extension WHERE extname='vector';` ≥ 0.8 |
| Alembic 실 적용 | `alembic upgrade head` / `downgrade -1` 왕복 | psql `\dt`, `\di` |
| 통합 테스트 | `backend/tests/db/test_audit_repo_postgres.py` (실 DB) | pytest with `DATABASE_URL=postgresql+asyncpg://...` |
| 데이터 마이그레이션 | `migrate_sqlite_to_postgres.py` 실 실행 (dry-run + apply) | row count 일치, audit_repo.find_recent 정합성 |
| 회귀 240 passed | Postgres 위에서 동일하게 통과 | `pytest backend/tests/` |

**BAR-56b 트리거**: BAR-56a 머지 + 운영 환경에서 docker daemon 가용 시점.

### 0.3 결정 근거 (council)

- **architect**: 운영 환경 의존 검증을 worktree 에 강제하면 do 가 영구 블록됨. 코드/스키마 분리 정식화 후 운영 정식화로 2단 분리하면 PDCA 사이클 끊김 없음.
- **infra-architect**: docker-compose / init.sql 은 정적 산출물 (실 daemon 없이도 합의 가능). Alembic revision 도 SQLAlchemy MetaData inspect 만으로 1:1 검증 가능.

---

## §1. docker-compose.yml 스니펫

```yaml
# docker-compose.yml — postgres 서비스 추가 (기존 backend/frontend 와 같은 networks 공유)

services:
  postgres:
    image: pgvector/pgvector:pg17
    container_name: barro_postgres
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-barro}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-barro}
      POSTGRES_DB: ${POSTGRES_DB:-barro}
      # asyncpg / SQLAlchemy 가 표준 사용하는 인증 옵션
      POSTGRES_HOST_AUTH_METHOD: scram-sha-256
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
      - ./infra/postgres/init.sql:/docker-entrypoint-initdb.d/00-init.sql:ro
    networks:
      - barro_net
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-barro} -d ${POSTGRES_DB:-barro}"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 20s

  # backend 서비스에 depends_on 추가
  backend:
    # ... 기존 설정 유지 ...
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      # 기존 환경변수 + DATABASE_URL 병기
      - DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER:-barro}:${POSTGRES_PASSWORD:-barro}@postgres:5432/${POSTGRES_DB:-barro}

volumes:
  pg_data:           # 기존 prometheus_data, grafana_data, app_logs 와 같은 위치에 추가
```

**검증 (BAR-56a)**: `docker compose config` 가 syntax error 없이 parse 됨.

---

## §2. infra/postgres/init.sql

```sql
-- infra/postgres/init.sql
-- 컨테이너 첫 기동 시 자동 실행 (docker-entrypoint-initdb.d/)
-- pgvector extension 활성화 + 사용자 권한 점검.
-- 주의: extension 만 활성화. 벡터 컬럼·인덱스는 BAR-58 책임.

\c barro

-- 1. pgvector 확장 활성화 (idempotent)
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. 버전 / 설치 검증 (≥ 0.8). 미만이면 즉시 실패.
DO $$
DECLARE
    v_extversion TEXT;
BEGIN
    SELECT extversion INTO v_extversion FROM pg_extension WHERE extname = 'vector';
    IF v_extversion IS NULL THEN
        RAISE EXCEPTION 'pgvector extension not installed';
    END IF;
    IF v_extversion < '0.8' THEN
        RAISE EXCEPTION 'pgvector version % < 0.8', v_extversion;
    END IF;
    RAISE NOTICE 'pgvector % installed', v_extversion;
END $$;

-- 3. 애플리케이션 사용자 권한 (POSTGRES_USER 가 owner 라 별도 GRANT 불필요).
--    추후 BAR-69 (RLS) 시 readonly / app 분리 사용자 도입.

-- 4. 기본 timezone
ALTER DATABASE barro SET TIMEZONE TO 'UTC';
```

---

## §3. Alembic 구조

### 3.1 alembic.ini (핵심)

```ini
# alembic.ini
[alembic]
script_location = alembic
prepend_sys_path = .
# DATABASE_URL 은 env.py 에서 동적으로 주입 (settings.DATABASE_URL).
sqlalchemy.url =

# revision 파일명: 0001_init.py 처럼 zero-padded
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

### 3.2 alembic/env.py (asyncio + AsyncEngine)

```python
# alembic/env.py
"""Alembic env — asyncio 엔진 + sqlalchemy.ext.asyncio.run_sync 패턴."""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.config.settings import get_settings
from backend.db.models import metadata  # SQLAlchemy MetaData (§3.4)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# settings.DATABASE_URL 을 alembic 에 주입 (alembic.ini 에는 비워둠)
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = metadata


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,           # TIMESTAMPTZ vs TEXT 차이 감지
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """async 엔진 → run_sync 로 동기 마이그레이션 실행."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


def run_migrations_offline() -> None:
    """offline (SQL 출력) 모드 — CI dry-run 용."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

### 3.3 alembic/versions/0001_init.py

현 SQLite 스키마 (`backend/db/database.py` 의 `CREATE_TABLES_SQL`) 의 Postgres 1:1 매핑.

```python
# alembic/versions/0001_init.py
"""init — audit_log + trades tables (1:1 from SQLite, BAR-56).

Revision ID: 0001
Revises:
Create Date: 2026-05-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # audit_log
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("symbol", sa.Text),
        sa.Column("market_type", sa.Text),
        sa.Column("quantity", sa.Float(precision=53)),    # DOUBLE PRECISION
        sa.Column("price", sa.Float(precision=53)),
        sa.Column("pnl", sa.Float(precision=53)),
        sa.Column("strategy_id", sa.Text),
        sa.Column(
            "metadata",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_audit_log_event_type", "audit_log", ["event_type"])
    op.create_index("idx_audit_log_created_at", "audit_log", ["created_at"])

    # trades
    op.create_table(
        "trades",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.Text, nullable=False),
        sa.Column("side", sa.Text, nullable=False),     # buy | sell (CHECK 는 BAR-69 보안)
        sa.Column("order_type", sa.Text, nullable=False),
        sa.Column("quantity", sa.Float(precision=53), nullable=False),
        sa.Column("price", sa.Float(precision=53), nullable=False),
        sa.Column("strategy_id", sa.Text),
        sa.Column("order_id", sa.Text),
        sa.Column("status", sa.Text),
        sa.Column(
            "created_at",
            sa.dialects.postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_trades_symbol", "trades", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_trades_symbol", table_name="trades")
    op.drop_table("trades")
    op.drop_index("idx_audit_log_created_at", table_name="audit_log")
    op.drop_index("idx_audit_log_event_type", table_name="audit_log")
    op.drop_table("audit_log")
```

**유지 보수 노트**:
- 인덱스는 SQLite 의 3개 (`idx_audit_log_event_type`, `idx_audit_log_created_at`, `idx_trades_symbol`) 와 1:1.
- pgvector 인덱스 (ivfflat / hnsw) 는 BAR-58 의 별도 revision 에서 추가.
- `metadata` 가 SQLite 에선 TEXT (JSON 문자열) 였으나 Postgres 에선 JSONB. audit_repo 의 `json.dumps(...)` 호출은 어댑터에서 dict 로 변경 (§5).

### 3.4 backend/db/models.py (Declarative MetaData)

```python
# backend/db/models.py — alembic env.py 의 target_metadata 소스
"""SQLAlchemy MetaData. 본 BAR-56 에선 audit_log + trades 만 정의.

ORM 모델 도입은 굳이 강제하지 않음 — repository 는 text() / Core API 사용.
"""
from sqlalchemy import MetaData

metadata = MetaData()

# 0001_init.py 가 op.create_table 로 직접 생성하므로
# 본 파일은 alembic autogenerate 비교 대상 metadata 만 export.
# (autogenerate 사용 시점에 ORM 모델 추가)
```

---

## §4. 타입 매핑 표 (SQLite → Postgres 17)

| SQLite 컬럼 / 타입 | Postgres 타입 | 비고 |
|--------------------|---------------|------|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL` (= `BIGINT GENERATED BY DEFAULT AS IDENTITY`) | id 폭발 대비 64-bit |
| `TEXT` (일반) | `TEXT` | length 제한 없음 — 1:1 |
| `TEXT NOT NULL` | `TEXT NOT NULL` | 동상 |
| `REAL` | `DOUBLE PRECISION` (`Float(precision=53)`) | 가격/수량 — Decimal 변환은 §6 노트 |
| `TEXT (JSON)` (예: `metadata`) | `JSONB` | GIN 인덱스 가능 (BAR-59 에서 추가). 어댑터에서 dict ↔ JSONB 자동 |
| `TEXT` (`created_at`, ISO 8601) | `TIMESTAMPTZ` | UTC 강제. ISO 직렬화는 응답 레이어에서 |
| `INDEX (col)` | `INDEX (col)` | btree default, 1:1 |

**변환 모듈** (`backend/db/_type_map.py`):

```python
# backend/db/_type_map.py — 단위 테스트 가능한 매핑 dict
SQLITE_TO_PG: dict[str, str] = {
    "INTEGER PRIMARY KEY AUTOINCREMENT": "BIGSERIAL PRIMARY KEY",
    "TEXT": "TEXT",
    "REAL": "DOUBLE PRECISION",
    "INTEGER": "BIGINT",
    "JSON": "JSONB",
    "TIMESTAMP": "TIMESTAMPTZ",
}
```

---

## §5. backend/db/database.py 어댑터 (외부 시그니처 보존)

### 5.1 목표

- `from backend.db.database import get_db, init_db` import 위치 보존.
- `async with get_db() as db: await db.execute(SQL, params)` 시그니처 보존.
- 호출자 (`audit_repo.py`) 코드 변경 0건 목표 — 단, SQL 플레이스홀더 `?` → `:name` 만 변경 (§6).

### 5.2 신규 구현 골격

```python
# backend/db/database.py
"""Database — SQLAlchemy AsyncEngine + asyncpg (Postgres 17).

BAR-56a: 외부 시그니처 (`get_db()`, `init_db()`) 보존.
SQLite fallback 은 DATABASE_URL 미설정 시에만 동작 (legacy / starter 호환).
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


def _get_database_url() -> str:
    """DATABASE_URL 우선, 미설정 시 SQLite fallback (legacy)."""
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    # legacy: 회귀 240 passed 가 SQLite 위에서 통과 중인 상황 호환.
    db_path = os.getenv("DB_PATH", "data/barro_trade.db")
    return f"sqlite+aiosqlite:///{db_path}"


def _ensure_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _get_database_url(),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


async def init_db(db_path: Optional[str] = None) -> None:
    """초기화 — Postgres 인 경우 alembic upgrade 가 책임.
    SQLite fallback 일 때만 CREATE TABLE 수행 (legacy 호환).
    """
    if db_path is not None:
        # legacy 인자 호환 — DB_PATH override
        os.environ.setdefault("DB_PATH", db_path)

    engine = _ensure_engine()
    if engine.dialect.name == "sqlite":
        # legacy fallback: 기존 CREATE_TABLES_SQL 실행
        from backend.db._legacy_sqlite import CREATE_TABLES_SQL
        async with engine.begin() as conn:
            for stmt in CREATE_TABLES_SQL.strip().split(";"):
                if stmt.strip():
                    await conn.exec_driver_sql(stmt)
        logger.info("DB(sqlite) 초기화 완료")
    else:
        logger.info("DB(postgres) — alembic upgrade head 가 스키마 책임")


@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncConnection, None]:
    """DB 연결 컨텍스트 매니저 — AsyncConnection 반환.

    호환성:
    - `await db.execute(text("SQL"), {"k": v})` SQLAlchemy text() 권장.
    - 기존 `?` 플레이스홀더 호출자는 §6 의 _? 호환 어댑터 적용 시 동작.
    """
    engine = _ensure_engine()
    async with engine.connect() as conn:
        async with conn.begin():
            yield conn
```

### 5.3 `db.execute()` 호환성 — Row 접근

기존 `aiosqlite.Row` 는 dict-like 접근 (`row["col"]`) 가능. SQLAlchemy `Result` 의 `.mappings()` 도 동일 패턴.

→ audit_repo 의 `dict(row)` 패턴은 다음으로 1:1 호환:

```python
# 기존
cursor = await db.execute("SELECT * FROM audit_log ...", params)
rows = await cursor.fetchall()
return [dict(row) for row in rows]

# 신규 (변경 최소)
result = await db.execute(text("SELECT * FROM audit_log ..."), params)
return [dict(row) for row in result.mappings().all()]
```

### 5.4 SQLite fallback 보존 근거 (council)

- **architect**: BAR-56a 단계에선 회귀 240 passed 가 SQLite 위에서 통과 중. AsyncEngine 으로 교체하면서 SQLite 백엔드를 즉시 끊으면 240 회귀가 worktree 에서 재현 불가 → check 단계 매치율 산정 곤란. fallback 보존 시 `DATABASE_URL` 만으로 dialect 스위칭.
- **infra-architect**: BAR-56b 머지 시점에 fallback 제거 PR (`DATABASE_URL` 강제 + SQLite 코드 삭제) 별도 분리. 본 BAR-56a 의 do 산출물에 영향 없음.

---

## §6. audit_repo 호출자 영향 분석

### 6.1 호출자 전수 inspect

```bash
$ grep -rn "from backend.db.database\|get_db()\|init_db" backend/ --include="*.py" | grep -v __pycache__ | grep -v "test_"
backend/main.py:33:                                       from backend.db.database import init_db
backend/db/database.py:70:async def get_db(db_path: ...
backend/db/repositories/audit_repo.py:13: from backend.db.database import get_db
backend/db/repositories/audit_repo.py:35:           async with get_db() as db:
backend/db/repositories/audit_repo.py:38:               await db.execute(...)
backend/db/repositories/audit_repo.py:70:           async with get_db() as db:
backend/db/repositories/audit_repo.py:74:               cursor = await db.execute(...)
backend/db/repositories/audit_repo.py:79:               cursor = await db.execute(...)
backend/core/orchestrator.py:399:           from backend.db.database import init_db
```

→ **DB 호출 지점 = 3 곳 (audit_repo.py 만)**, init_db 호출 = 2 곳 (main.py, orchestrator.py).

### 6.2 변경 영향

| 호출자 | 변경 필요 여부 | 변경 내용 |
|--------|----------------|-----------|
| `backend/main.py:33` (`init_db`) | ✗ | 시그니처 동일 |
| `backend/core/orchestrator.py:399` (`init_db`) | ✗ | 시그니처 동일 |
| `audit_repo.insert` (line 38) | ✓ (SQL 만) | `?` 플레이스홀더 → `:name`, `text()` wrap, `metadata` 는 dict 그대로 (JSONB 자동) |
| `audit_repo.find_recent` (line 70-79) | ✓ (SQL 만) | 동상, `cursor.fetchall()` → `result.mappings().all()` |

→ **함수 시그니처 변경 0건** (목표 달성). SQL 텍스트만 SQLAlchemy `text()` 변환.

### 6.3 audit_repo 변경 diff (스케치)

```python
# backend/db/repositories/audit_repo.py — BAR-56a 변경
import json
from sqlalchemy import text
from backend.db.database import get_db


async def insert(self, ..., metadata=None, created_at=None) -> bool:
    try:
        async with get_db() as db:
            await db.execute(
                text("""
                    INSERT INTO audit_log
                        (event_type, symbol, market_type, quantity, price, pnl,
                         strategy_id, metadata, created_at)
                    VALUES
                        (:event_type, :symbol, :market_type, :quantity, :price, :pnl,
                         :strategy_id, :metadata, :created_at)
                """),
                {
                    "event_type": event_type,
                    "symbol": symbol,
                    "market_type": market_type,
                    "quantity": quantity,
                    "price": price,
                    "pnl": pnl,
                    "strategy_id": strategy_id,
                    # JSONB 는 dict 그대로. SQLite fallback 은 dialect 가 직렬화.
                    "metadata": metadata or {},
                    "created_at": created_at,    # TIMESTAMPTZ 캐스팅은 dialect 처리
                },
            )
        return True
    except Exception as e:
        logger.error("감사 로그 DB 저장 실패: %s", e)
        return False
```

**SQLite fallback 호환성**: `text()` + named param 은 dialect 무관. JSONB 는 SQLite 에서 TEXT 로 자동 직렬화 (legacy 240 회귀 통과 보존).

---

## §7. 마이그레이션 스크립트 의사코드 (`scripts/migrate_sqlite_to_postgres.py`)

```python
# scripts/migrate_sqlite_to_postgres.py
"""SQLite → Postgres 데이터 마이그레이션 (BAR-56b 운영 정식 do 에서 실행).

플로우:
    1. SQLite source 연결 → audit_log / trades row count 측정
    2. Postgres target 연결 (DATABASE_URL) → row count 측정 (사전)
    3. (a) audit_log → COPY FROM STDIN (CSV) — JSON 직렬화 변환
       (b) trades   → COPY FROM STDIN (CSV)
       단, --dry-run 이면 (a)(b) 생략, count 만 보고
    4. row count 사후 검증 — 모든 테이블에서 source == target 일치
    5. 검증 실패 또는 --dry-run 외 모든 단계 실패 → 트랜잭션 롤백
"""
from __future__ import annotations
import argparse
import asyncio
import json
import logging
import sqlite3
from typing import Iterable

import asyncpg

logger = logging.getLogger(__name__)

TABLES = ("audit_log", "trades")


async def _count_pg(conn: asyncpg.Connection, table: str) -> int:
    return await conn.fetchval(f"SELECT count(*) FROM {table}")


def _count_sqlite(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _rows_from_sqlite(con: sqlite3.Connection, table: str) -> Iterable[dict]:
    cur = con.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    for row in cur:
        d = dict(zip(cols, row))
        # audit_log.metadata: TEXT(JSON) → dict (Postgres JSONB 입력)
        if table == "audit_log" and isinstance(d.get("metadata"), str):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except Exception:
                d["metadata"] = {}
        return d  # 본 의사코드는 yield 로 generator 권장


async def migrate(sqlite_path: str, pg_dsn: str, dry_run: bool) -> int:
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    pg = await asyncpg.connect(pg_dsn)

    try:
        report = {}
        for t in TABLES:
            src_n = _count_sqlite(src, t)
            tgt_n_before = await _count_pg(pg, t)
            report[t] = {"src": src_n, "tgt_before": tgt_n_before}

            if dry_run:
                continue

            # 트랜잭션 단위 — 실패 시 전체 롤백
            async with pg.transaction():
                # COPY FROM STDIN binary or csv (asyncpg.copy_records_to_table)
                rows = list(_rows_from_sqlite(src, t))
                if rows:
                    cols = list(rows[0].keys())
                    await pg.copy_records_to_table(
                        t, records=[tuple(r[c] for c in cols) for r in rows], columns=cols
                    )

            report[t]["tgt_after"] = await _count_pg(pg, t)

        # 사후 검증 (dry_run 이 아닐 때만)
        if not dry_run:
            for t in TABLES:
                if report[t]["src"] != report[t]["tgt_after"] - report[t]["tgt_before"]:
                    raise RuntimeError(f"row count mismatch on {t}: {report[t]}")

        logger.info("migrate report: %s", report)
        return 0
    finally:
        src.close()
        await pg.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default="data/barro_trade.db")
    ap.add_argument("--pg-dsn", required=True, help="postgresql://barro:barro@localhost:5432/barro")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(migrate(args.sqlite, args.pg_dsn, args.dry_run)))
```

**BAR-56a 검증**: `--help` smoke + 함수 단위 테스트 (모킹 sqlite + 모킹 asyncpg). 실 실행은 BAR-56b.

---

## §8. 테스트 전략

### 8.1 BAR-56a 단위 테스트 (worktree, ≥ 12개)

| ID | 위치 | 검증 |
|----|------|------|
| T-01 | `backend/tests/db/test_alembic_env.py` | `alembic.env` import 가능 (구문 검증) |
| T-02 | `backend/tests/db/test_alembic_env.py` | `target_metadata` 가 `MetaData` 인스턴스 |
| T-03 | `backend/tests/db/test_alembic_init_revision.py` | `0001_init.upgrade` 가 callable + 호출 시 op.create_table 4회 (audit_log + 2 idx + trades + 1 idx — 정확히는 mock op 로 카운트) |
| T-04 | `backend/tests/db/test_alembic_init_revision.py` | `0001_init.downgrade` 가 callable + reverse 순서 검증 |
| T-05 | `backend/tests/db/test_alembic_init_revision.py` | revision id == "0001", down_revision is None |
| T-06 | `backend/tests/db/test_database_adapter.py` | `from backend.db.database import get_db, init_db` import OK |
| T-07 | `backend/tests/db/test_database_adapter.py` | `get_db()` 시그니처 = `AsyncContextManager` (inspect.signature 검증) |
| T-08 | `backend/tests/db/test_database_adapter.py` | `init_db()` legacy `db_path` kwarg 호환 |
| T-09 | `backend/tests/db/test_database_adapter.py` | `DATABASE_URL` 미설정 → SQLite dialect, 설정 → postgresql |
| T-10 | `backend/tests/db/test_type_map.py` | `SQLITE_TO_PG["TEXT"] == "TEXT"`, `["REAL"] == "DOUBLE PRECISION"`, `["JSON"] == "JSONB"`, `["INTEGER PRIMARY KEY AUTOINCREMENT"] == "BIGSERIAL PRIMARY KEY"` |
| T-11 | `backend/tests/db/test_migrate_script.py` | `migrate.--help` zero-exit (subprocess) |
| T-12 | `backend/tests/db/test_migrate_script.py` | `migrate.migrate()` dry-run 시 `pg.copy_records_to_table` 호출 0회 (mock 검증) |
| T-13 | `backend/tests/db/test_audit_repo_signature.py` | `audit_repo.insert` / `find_recent` async 시그니처 보존 (호출자 컨트랙트) |
| T-14 | `backend/tests/db/test_audit_repo_sqlite_fallback.py` | DATABASE_URL 미설정 시 audit_repo.insert 가 SQLite 위에서 정상 동작 (회귀 보장) |

### 8.2 BAR-56b 통합 테스트 (운영 환경)

| ID | 위치 | 검증 |
|----|------|------|
| I-01 | manual | `docker compose up -d postgres` → healthcheck PASS |
| I-02 | manual | `psql -c "SELECT extversion FROM pg_extension WHERE extname='vector'"` ≥ 0.8 |
| I-03 | manual | `alembic upgrade head` → `\dt` 에서 audit_log / trades 노출 |
| I-04 | manual | `alembic downgrade -1 && alembic upgrade head` 왕복 PASS |
| I-05 | `backend/tests/db/test_audit_repo_postgres.py` | DATABASE_URL=postgres 로 insert / find_recent / event_type filter 0 fail |
| I-06 | manual | `python scripts/migrate_sqlite_to_postgres.py --dry-run` row count 일치 |
| I-07 | CI | 회귀 240 passed (Postgres 위에서) |

---

## §9. 후속 BAR

| BAR | 트리거 | 산출물 |
|-----|--------|--------|
| **BAR-56b** | BAR-56a 머지 + docker daemon 가용 | I-01 ~ I-07 통합 검증 + SQLite fallback 제거 PR |
| **BAR-58** | BAR-56b 완료 | 뉴스 임베딩 인프라 — `embeddings (id BIGSERIAL, vec vector(1536), …)` 테이블 + ivfflat / hnsw 인덱스 + 임베딩 적재 파이프라인 |
| **BAR-69** | Phase 5 보안 | RLS 정책, pgcrypto 컬럼 암호화, app/readonly 사용자 분리 |
| **BAR-72** | Phase 6 운영 | PgBouncer, read replica, PITR 백업, audit_log 월별 파티셔닝 |

---

## §10. council 권고 요약 (200단어 이내)

**architect (180 words)**: SQL 호출 통일은 SQLAlchemy `text()` + named param 으로 가야 한다. `?` 플레이스홀더는 dialect-specific 이며, `:name` 으로 통일하면 SQLite fallback 과 Postgres 양쪽에서 동작 → BAR-56a 검증 가능성 확보. AsyncEngine 어댑터는 `get_db()` / `init_db()` 의 외부 시그니처를 보존해야 audit_repo 외 호출자 (main.py / orchestrator.py) 의 변경이 0건이 된다 — 본 BAR 의 회귀 240 passed 게이트와 직결. SQLite fallback 은 BAR-56a 머지 시점까진 보존, BAR-56b 머지 PR 에서 제거하라. ORM 모델은 본 BAR 에서 도입하지 말고 alembic op.create_table 직접 호출 + MetaData export 만으로 끝낸다 — Repository 인터페이스 보존 + 테스트 가능성 우선.

**infra-architect (170 words)**: pgvector extension 활성화는 본 BAR-56a 에서 정식 산출물 (`infra/postgres/init.sql`) 로 포함하되, **벡터 컬럼·인덱스는 BAR-58** 로 분리하라 — 본 BAR 의 회귀 240 passed 게이트와 충돌 회피. docker-compose `pgvector/pgvector:pg17` 이미지는 multi-arch (M1/x86) 모두 공식 지원. healthcheck 는 `pg_isready` (10s interval, 5 retries) 로 backend 의 `depends_on: condition: service_healthy` 와 결합. init.sql 은 idempotent (`IF NOT EXISTS`) + 버전 게이트 (`< 0.8` 시 `RAISE EXCEPTION`) 로 운영 안전성 확보. BAR-56b 운영 정식 do 단계에서 alembic 왕복 (upgrade ↔ downgrade) 검증을 PR 게이트화하라 — Phase 3~5 의 빈번한 스키마 진화에 대비.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-07 | Initial draft (council: architect + infra-architect) | bkit-cto-lead |
