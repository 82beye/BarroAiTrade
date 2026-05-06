# BAR-56 — Postgres + pgvector 마이그레이션 (Phase 3 인프라 게이트)

**Phase**: 3 (테마 인텔리전스) — **첫 BAR / Phase 3 전체 블로킹**
**선행**: Phase 2 종료 (BAR-52~55) ✅ — NXT 통합 + SOR v1 완료
**후속 블로킹**: BAR-58 (뉴스 임베딩 인프라), BAR-59 (테마 분류기), BAR-61 (일정 캘린더)

---

## 1. 목적 (Why)

Phase 3 의 모든 BAR 가 **벡터 검색 + 관계형 트랜잭션 동시성** 을 요구한다. 현재 SQLite (aiosqlite) 는 다음 한계로 Phase 3 진입을 막는다.

| 한계 (SQLite) | Phase 3 의 요구 |
|---------------|-----------------|
| 벡터 타입 / ANN 인덱스 부재 | BAR-58 뉴스 임베딩 (1536-dim OpenAI / 768-dim local) → 유사 뉴스 검색 |
| 단일 writer (file lock) | BAR-58 비동기 임베딩 적재 + BAR-59 테마 분류 + 매매 audit 동시 write |
| 마이그레이션 추적 부재 | Phase 3~5 의 빈번한 스키마 진화 (테마/캘린더/시그널 테이블 추가) 추적 필요 |
| JSON 인덱싱 빈약 | BAR-59 테마 메타데이터 (Korean tag set) JSONB 검색 |

**결론**: Phase 3 진입 전 본 BAR 에서 **인프라만 교체**한다. 데이터·기능 변경 없음. 본 BAR 의 핵심 산출물은 "기존 회귀 240 passed 가 Postgres 위에서도 똑같이 통과하는 것".

**왜 pgvector 까지 본 BAR 에서 켜는가**: Postgres 만 깔고 pgvector 를 BAR-58 로 미루면 BAR-58 시작 시 docker-compose / Alembic / 의존성 재작업이 발생 → Phase 3 첫 주에 1주 손실. 확장(extension) 활성화는 5분 작업이므로 본 BAR 에 포함하되 **인덱스·임베딩 컬럼은 추가하지 않는다** (BAR-58 책임).

---

## 2. 스코프

### 2-1. 인프라
- **FR-01**: `docker-compose.yml` 에 `postgres` 서비스 추가 — `pgvector/pgvector:pg17` 이미지, 볼륨 `pg_data`, 포트 5432, healthcheck `pg_isready`
- **FR-02**: 첫 컨테이너 기동 시 `CREATE EXTENSION IF NOT EXISTS vector;` 자동 실행 (init script `infra/postgres/init.sql`)

### 2-2. 마이그레이션 도구
- **FR-03**: Alembic 도입 (`alembic`, `alembic[asyncio]`) — `alembic/` 디렉터리, `alembic.ini`, `env.py` (asyncio 엔진 + `sqlalchemy.ext.asyncio`)
- **FR-04**: 초기 revision `0001_init.py` — 현재 SQLite 스키마 (`audit_log`, `trades` + 인덱스) 를 Postgres 호환 타입으로 1:1 이전 (TEXT → TEXT, REAL → DOUBLE PRECISION, JSON metadata → JSONB, AUTOINCREMENT → BIGSERIAL, created_at TEXT → TIMESTAMPTZ)

### 2-3. 어댑터 (코드 변경 최소화)
- **FR-05**: `backend/db/database.py` 의 `get_db()` / `init_db()` 를 SQLAlchemy AsyncEngine + asyncpg 기반으로 교체. 외부 시그니처 (`async with get_db() as db:`) 보존 → `audit_repo.py` 등 호출자 수정 0건 목표
- **FR-06**: SQL 플레이스홀더 `?` → `$1, $2…` 자동 변환 어댑터 (또는 SQLAlchemy `text()` + bound params 로 전면 통일). 1차에선 후자 권장
- **FR-07**: `.env` / `.env.example` — `DATABASE_URL=postgresql+asyncpg://barro:barro@localhost:5432/barro` 추가, 기존 `DB_PATH` 는 deprecation 주석만 남김 (legacy export 시 사용)

### 2-4. 데이터 보존
- **FR-08**: `scripts/migrate_sqlite_to_postgres.py` — `data/barro_trade.db` 의 `audit_log` / `trades` 를 dump → COPY 로 Postgres 적재. **dry-run 모드 (`--dry-run`)** 와 row count 검증 (`SELECT count(*) WHERE …`) 포함. 실패 시 트랜잭션 롤백.

---

## 3. Out of Scope (다음 BAR 로 분리)

| 영역 | 이관 대상 | 사유 |
|------|----------|------|
| RLS (Row-Level Security) 정책 | **BAR-69** (Phase 5 보안) | 보안 모델 (사용자/조직 분리) 을 Phase 5 에서 정의한 후에 적용 |
| 컬럼 단위 암호화 (pgcrypto) | **BAR-69** | 동상 |
| 읽기 복제 (read replica) | **BAR-72** | Phase 6 운영. 1개 인스턴스로 Phase 3~5 충분 |
| pgvector 벡터 인덱스 (ivfflat / hnsw) + embedding 컬럼 추가 | **BAR-58** (뉴스 임베딩 인프라) | 임베딩 차원·모델 결정이 BAR-58 책임 |
| 본격 connection pool 튜닝 (PgBouncer) | **BAR-72** | Phase 6 운영 |
| 백업·PITR | **BAR-72** | 동상 |
| audit_log 파티셔닝 (월별) | **BAR-72** | row 수 < 1만 단계에선 불필요 |

---

## 4. 비기능 요구사항 (NFR)

| ID | 요구 | 측정 |
|----|------|------|
| NFR-01 | 마이그레이션 스크립트 실행 시간 ≤ 5분 (1만 row 기준) | `time python scripts/migrate_sqlite_to_postgres.py` |
| NFR-02 | 다운타임 0 (개발 환경) — `docker-compose up -d postgres` 후 `alembic upgrade head` 만으로 가용 | 메뉴얼 검증 |
| NFR-03 | 회귀 240 passed 유지 (Phase 2 까지 누적) — Postgres 위에서 동일하게 통과 | `pytest backend/tests/` |
| NFR-04 | 단건 INSERT P95 ≤ 5ms (로컬, 풀 5) | 마이크로 벤치 (audit_repo.insert × 1000) |
| NFR-05 | DATABASE_URL 미설정 시 명확한 에러 + Postgres 미기동 시 5초 내 fail-fast | 통합 테스트 |

---

## 5. Day 0 — 1일 스파이크 (Plan 직후 / Design 진입 전)

스파이크 산출물은 design 문서에 첨부. 본 plan 의 가정이 깨지면 plan 갱신.

| 항목 | 검증 방법 | 가정 |
|------|----------|------|
| 현재 SQLite 스키마 inspect | `sqlite3 data/barro_trade.db .schema` → `audit_log`, `trades` 두 테이블만 확인 | `database.py` `CREATE_TABLES_SQL` 와 일치 |
| pgvector 0.8 + Postgres 17 호환 | `docker run pgvector/pgvector:pg17` → `CREATE EXTENSION vector;` 성공 | 공식 이미지 존재 |
| Alembic asyncio 엔진 지원 | `alembic init -t async alembic` 템플릿 정상 동작 | Alembic 1.13+ |
| asyncpg vs psycopg3 선택 | asyncpg 성능 우선 → 채택. `sqlalchemy.ext.asyncio` 호환 OK | — |
| SQLite → Postgres 타입 매핑 표 | TEXT/REAL/INTEGER/JSON → TEXT/DOUBLE PRECISION/BIGINT/JSONB, `?` 플레이스홀더 → `:param` (SQLAlchemy text) | created_at 은 TIMESTAMPTZ 로 승격 (현재 TEXT ISO8601) |

스파이크 결과 ≥ 1개 가정 깨지면 plan 의 위험 섹션 (8) 트리거 발동.

---

## 6. DoD — Phase 3 진입 게이트

- [ ] `docker-compose up -d postgres` 기동, healthcheck PASS
- [ ] `infra/postgres/init.sql` 에서 `CREATE EXTENSION vector;` 자동 실행 확인 (`SELECT extversion FROM pg_extension WHERE extname='vector';` ≥ 0.8)
- [ ] `alembic upgrade head` 성공 → `audit_log`, `trades` 테이블·인덱스 생성 확인
- [ ] `alembic downgrade -1` → `alembic upgrade head` 왕복 PASS
- [ ] `audit_repo` 통합 테스트 (`backend/tests/db/test_audit_repo_postgres.py`, 신규) — insert / find_recent / event_type filter 0 fail
- [ ] `scripts/migrate_sqlite_to_postgres.py --dry-run` → row count 일치 보고
- [ ] **회귀 240 passed 유지** (Postgres 위에서 동일) — `pytest backend/tests/` exit 0
- [ ] gap-detector 매치율 ≥ 90%
- [ ] `.env.example` 갱신 + `docs/deployment.md` 에 Postgres 기동 절차 1 문단 추가

---

## 7. 기능 요구사항 요약 (FR 매트릭스)

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-01 | docker-compose postgres 서비스 | `docker-compose.yml` |
| FR-02 | pgvector extension 자동 활성화 | `infra/postgres/init.sql` |
| FR-03 | Alembic asyncio 도입 | `alembic/`, `alembic.ini` |
| FR-04 | 초기 revision (현 스키마 1:1) | `alembic/versions/0001_init.py` |
| FR-05 | get_db() AsyncEngine 어댑터 | `backend/db/database.py` |
| FR-06 | SQL 플레이스홀더 통일 | `backend/db/repositories/audit_repo.py` |
| FR-07 | DATABASE_URL 환경변수 | `.env.example`, `backend/config/settings.py` |
| FR-08 | SQLite → Postgres 마이그레이션 스크립트 | `scripts/migrate_sqlite_to_postgres.py` |

---

## 8. 위험 / 완화

| 위험 | 트리거 | 완화 | 일정 영향 |
|------|--------|------|----------|
| `?` 플레이스홀더 → `:param` 전환 누락 | audit_repo 외 미발견 호출자 존재 | Day 0 스파이크에서 `grep -rn "execute(" backend/` 전수, design 에서 어댑터 검증 테스트 추가 | +0.5 일 |
| asyncpg + asyncio 이벤트루프 충돌 (pytest) | 기존 회귀 일부가 sync fixture | `pytest-asyncio` 모드 통일 (asyncio_mode=auto) — BAR-44 베이스라인에서 이미 적용 가정. 깨지면 fixture 수정 | +1 일 |
| 마이그레이션 스크립트 데이터 누락 / 중복 | 키 충돌 (id 자동증가) | dry-run 의 row count 검증 + 트랜잭션 단위 롤백, 실패 시 staging schema 폐기 | +0.5 일 |
| pgvector 0.8 / pg17 빌드 이슈 (M1) | ARM64 이미지 호환 | 공식 `pgvector/pgvector:pg17` 다중 아키텍처 확인 (Day 0). 미지원 시 `pgvector/pgvector:pg16` fallback | +0 일 |
| TIMESTAMPTZ 승격으로 인한 created_at 비교 깨짐 | 기존 코드가 TEXT 비교 | audit_repo.find_recent ORDER BY 는 컬럼 정렬 — TIMESTAMPTZ 도 동일 동작. 단, 외부 직렬화 시 ISO 8601 강제 | +0 일 |
| **트리거: 1주 추가 일정** | 위 위험 중 2개 이상 동시 발생 | 본 BAR 만 1주 연장 → BAR-57 시작 1주 지연 보고 | +5 일 |

---

## 9. 다음 단계 (5단 PDCA)

1. `/pdca design BAR-56` — Day 0 스파이크 결과 + 어댑터 시그니처 + alembic env.py 윤곽 + 마이그레이션 스크립트 의사코드
2. `/pdca do BAR-56` — docker-compose / alembic 0001 / database.py 교체 / audit_repo 통합테스트 / migrate 스크립트
3. `/pdca analyze BAR-56` — gap-detector + 회귀 240 passed 재검증
4. `/pdca iterate BAR-56` (필요 시) — 매치율 < 90% 케이스
5. `/pdca report BAR-56` — Phase 3 인프라 게이트 통과 보고

본 BAR 의 5단 PDCA 완수 → **BAR-57 (뉴스 수집 v1) 진입 가능**.

---

## 10. council 단계 권고 (architect + infra-architect, 한 줄)

- **architect**: SQL 호출 통일은 SQLAlchemy `text()` + named param 으로 1:1 치환 권장 — Repository 인터페이스 보존 + 테스트 가능성 ↑.
- **infra-architect**: pgvector extension 은 본 BAR 에 포함하되 **벡터 컬럼·인덱스는 BAR-58** 로 분리 — 본 BAR 의 회귀 240 passed 게이트와 충돌 회피.
