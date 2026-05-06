# BAR-56a Postgres + pgvector — Design ↔ Implementation Gap Analysis

**Analyzed**: 2026-05-07
**Design**: `docs/02-design/features/bar-56-postgres-pgvector.design.md`
**Plan**: `docs/01-plan/features/bar-56-postgres-pgvector.plan.md`
**Implementation**: docker-compose / init.sql / alembic / backend/db / scripts / tests

## Summary

| Metric | Value |
|---|---|
| 총 항목 수 | 12 |
| 매치 항목 수 | 12 |
| **매치율** | **100 %** |
| 상태 | **PASS** (≥ 90 %) |

## Verification Matrix

| # | Item | 결과 |
|---|------|:---:|
| 1 | docker-compose postgres 서비스 (pgvector/pgvector:pg17, pg_isready healthcheck, init.sql 마운트, depends_on) | ✅ |
| 2 | infra/postgres/init.sql (CREATE EXTENSION vector + 버전 게이트 ≥0.8 + UTC) | ✅ |
| 3 | alembic.ini (script_location, prepend_sys_path) | ✅ |
| 4 | alembic/env.py (asyncio + AsyncEngine + run_sync + offline 모드) | ✅ |
| 5 | 0001_init.py (audit_log + trades + 인덱스 3개, JSONB metadata, TIMESTAMPTZ created_at, BIGSERIAL id) | ✅ |
| 6 | backend/db/models.py (MetaData export) | ✅ |
| 7 | backend/db/database.py (AsyncEngine + asyncpg / aiosqlite fallback, get_db/init_db 시그니처 보존) | ✅ |
| 8 | backend/db/_type_map.py (SQLITE_TO_PG dict) | ✅ |
| 9 | backend/db/_legacy_sqlite.py (CREATE_TABLES_SQL 분리) | ✅ |
| 10 | audit_repo (`?` → `:name`, text() wrap, .mappings()) | ✅ |
| 11 | scripts/migrate_sqlite_to_postgres.py (--dry-run + row count + COPY) | ✅ |
| 12 | 단위 테스트 ≥ 12 (실측 22 PASSED), 회귀 262 passed 0 fail | ✅ |

## 누락 / 불일치

**없음.** 12/12 1:1 일치.

## 미세 메모 (gap 아님)

- audit_repo: SQLite 의 metadata 컬럼은 TEXT 라서 dialect 분기로 `json.dumps` 보존. Postgres JSONB 는 dict 그대로 bind. 회귀 보존 + 후속 BAR-56b 에서 분기 제거.
- Settings 에 `postgres_user/password/db` 필드 추가 — env.example 동기성 회귀 테스트와 일관.
- design §0.2 BAR-56b 항목은 본 BAR 의 검증 범위 외 (운영 환경 정식 do).

## 권장 후속

1. ✅ pdca-iterator 트리거 **불필요** (100% PASS).
2. `/pdca report BAR-56` 진행.
3. BAR-56b 후속 (운영 환경 docker daemon 가용 시) — alembic 왕복 + 통합 테스트 + SQLite fallback 제거.
4. BAR-58 (벡터 컬럼 + 인덱스) — embeddings 테이블 + ivfflat/hnsw.
5. BAR-69 (RLS, pgcrypto 컬럼 암호화) — Phase 5.

**판정**: PASS — Phase 3 진입 인프라 게이트 통과.
