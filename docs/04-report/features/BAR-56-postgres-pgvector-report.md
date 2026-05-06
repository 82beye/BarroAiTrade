# BAR-56a Postgres + pgvector 인프라 — Completion Report

**Phase**: 3 (테마 인텔리전스) — **첫 BAR / 인프라 게이트**
**Ticket**: BAR-56 (56a worktree 트랙) — Postgres 17 + pgvector 0.8 마이그레이션
**Status**: ✅ COMPLETED (worktree backend)
**Date**: 2026-05-07

---

## 1. Outcomes

Phase 3 의 모든 후속 BAR (BAR-58 임베딩, BAR-59 테마 분류기, BAR-61 일정 캘린더) 가 의존하는 **Postgres + pgvector 인프라 골격** 이 도입되었다. SQLite fallback 보존으로 회귀 240 passed 위에서 동일하게 동작.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| AsyncEngine 어댑터 | `backend/db/database.py` | SQLAlchemy + asyncpg / aiosqlite, get_db/init_db 시그니처 보존 |
| Alembic | `alembic/` (env.py + 0001_init.py + script.py.mako) | asyncio 마이그레이션 (audit_log + trades 1:1) |
| MetaData export | `backend/db/models.py` | alembic env.py target_metadata |
| 타입 매핑 | `backend/db/_type_map.py` | SQLITE_TO_PG dict |
| Legacy SQL | `backend/db/_legacy_sqlite.py` | CREATE_TABLES_SQL 분리 (BAR-56b 머지 시 삭제 예정) |
| 컨테이너 | `docker-compose.yml` postgres 서비스 | pgvector/pgvector:pg17, healthcheck |
| Init script | `infra/postgres/init.sql` | pgvector ≥0.8 게이트 + UTC + idempotent |
| 마이그레이션 | `scripts/migrate_sqlite_to_postgres.py` | --dry-run + COPY + row count |
| audit_repo | `backend/db/repositories/audit_repo.py` | text() + named param (dialect 무관) |

### 1.2 분리 정책 (BAR-56a / BAR-56b)

| BAR | 트랙 | 산출물 | 본 사이클 |
|-----|------|--------|:---:|
| **BAR-56a** | worktree (코드/스키마/단위 테스트) | 모든 정적 산출물 + 22 단위 테스트 | ✅ 정식 do |
| **BAR-56b** | 운영 (docker daemon + 실 DB) | docker compose up + alembic 왕복 + 통합 테스트 + SQLite fallback 제거 | deferred |

이유: worktree 환경은 docker daemon 부재. 정적 산출물 + mock 단위 테스트만으로도 회귀 게이트 (240 → 262 passed) 충족 가능 → PDCA 사이클 지속.

---

## 2. Validation

### 2.1 Tests

```
make test-db
─────────────────────────────────────────────
22 passed in 1.88s
```

| 파일 | 케이스 |
|------|:------:|
| `test_alembic_env.py` | 3 |
| `test_alembic_init_revision.py` | 3 |
| `test_database_adapter.py` | 5 |
| `test_type_map.py` | 2 |
| `test_audit_repo_signature.py` | 4 |
| `test_audit_repo_sqlite_fallback.py` | 2 |
| `test_migrate_script.py` | 3 |
| **합계** | **22 PASSED** |

### 2.2 회귀

- 시작: 240 passed (Phase 2 종료 직후)
- BAR-56a do 머지 후: **262 passed, 1 skipped, 0 failed** (+22)
- DATABASE_URL 미설정 = SQLite fallback → 240 회귀 모두 그대로 통과
- audit_repo 코드 변경 (`text()` + named param) 후에도 SQLite 위에서 정상 동작 (test_audit_repo_sqlite_fallback 으로 검증)

### 2.3 Gap Analysis (PR #86 머지)

- 매치율 **100%** (12/12) — PASS
- iterator 트리거 불필요

상세: `docs/04-report/analyze/BAR-56-gap-analysis.md`

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #82 | ✅ MERGED |
| design | #83 | ✅ MERGED |
| do | #85 | ✅ MERGED (22 tests, 262 회귀) |
| analyze | #86 | ✅ MERGED (100%) |
| report | (this) | 진행 중 |

---

## 4. Phase 3 Progress

| BAR | Title | Status |
|-----|-------|:------:|
| **BAR-56 (56a)** | Postgres + pgvector 인프라 골격 | ✅ DONE |
| BAR-56b | 운영 정식 (docker 기동 + alembic 왕복 + SQLite fallback 제거) | deferred |
| BAR-57 | 뉴스/공시 수집 파이프라인 | NEXT |
| BAR-58 | 형태소·임베딩 인프라 + pgvector 컬럼 | pending |
| BAR-59 | 테마 분류기 v1 | pending |
| BAR-60 | 대장주 점수 알고리즘 | pending |
| BAR-61 | 일정 캘린더 + 이벤트→종목 연동 | pending |
| BAR-62 | 프론트 테마 박스 + 캘린더 + 뉴스 티커 | pending |

---

## 5. Lessons & Decisions

1. **시그니처 보존 정책**: `get_db()` / `init_db()` 외부 시그니처를 보존해 audit_repo 외 호출자 (main.py / orchestrator.py) 변경 0건. 회귀 240 passed 그대로 보존.
2. **dialect 무관 SQL**: SQLAlchemy `text()` + named param `:name` → SQLite/Postgres 양립. `?` 플레이스홀더는 dialect-specific 이라 통일 곤란.
3. **fallback 보존 → 분리 정책**: BAR-56a 단계에선 SQLite fallback 보존. BAR-56b PR 에서 제거. 단계별 회귀 게이트 흐트러짐 회피.
4. **BAR 분할 (a/b)** 반복 패턴: Phase 2 의 BAR-54a/54b 와 동일 — worktree 환경 제약 시 정적 산출물 + 단위 테스트만 do, 실 환경 검증은 후속.
5. **dialect 분기 1곳만 도입**: audit_repo 의 metadata 컬럼은 SQLite (TEXT) vs Postgres (JSONB) 가 데이터 타입 자체가 다름 → `dialect.name == "sqlite"` 분기 1곳만 보존. BAR-56b 에서 제거.
6. **CTO Lead leader/council 패턴**: plan = leader (단일 산출물), design = architect+infra-architect council. Phase 3 의 빈번한 인프라 결정에 적합.

---

## 6. Next Action

`/pdca plan BAR-57` — 뉴스/공시 수집 파이프라인. RSS + DART 1분 polling + Redis Streams + 24h 누락률 ≤ 1%. BAR-58 (임베딩) 의 입력 소스.

본 BAR-56a 의 SQLAlchemy AsyncEngine + audit_repo 패턴이 후속 BAR 의 repository 추가 시 표준 템플릿.
