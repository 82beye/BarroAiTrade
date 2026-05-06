# Team Agent Combined Output

- BAR: BAR-57
- Stage: plan
- Generated: 2026-05-07 01:04:21 KST
- Roles: architect developer qa reviewer security


---

## architect (exit=0)

## Architect 검토 — BAR BAR-57 plan

### 결과
**PASS**

### 핵심 발견 (5개)
1. **모듈 경계 깔끔**: `backend/core/news/{sources,collector,dedup,publisher}` + `backend/db/repositories/news_repo.py` + `backend/models/news.py` 분할은 기존 `core/{gateway,market_session,execution,risk,scanner}` + `db/repositories/audit_repo.py` 의 동일 패턴. INxtGateway·MarketSessionService·CompositeOrderBookService·SmartOrderRouter 와 의존성 충돌 없음 (수집 파이프라인은 시장 데이터 라인과 직교).
2. **Pydantic v2 frozen 정책 준수**: `NewsItem` (frozen=True, TZ-aware datetime, `tuple[str,...]` tags) 은 `OrderRequest` (`backend/models/order.py:42`) 와 동일한 ConfigDict 패턴. 자금흐름 필드는 없으므로 Decimal 정책 N/A — 적절.
3. **repo 패턴 정합**: `text() + named param + ON CONFLICT DO NOTHING` 는 `AuditRepository` (BAR-56 dialect-무관 패턴) 와 일치. SQLite/Postgres 양립도 명시되어 회귀 게이트(≥292 passed) 와 정렬됨.
4. **a/b 분리 정책 정합**: BAR-54a/54b · BAR-56a/56b 와 동일 트랙 분리. Redis daemon·실 DART 호출·24h 운용 부재라는 worktree 제약 하에서 인터페이스 + InMemory fallback 으로 충분히 BAR-58 진입 입력면을 봉쇄.
5. **다음 단계 입력 충분성**: design 단계 council 5인 위임 표(architect/infra/bkend/qa/security)가 명시되어 있고 산출 위치(`docs/02-design/features/bar-57-news-collection.design.md`)도 확정 — design 진입 가능.

### 권고
1. **호출 시퀀스 4단을 design 단계에서 못 박기** — `dedup.seen → repo.insert(ON CONFLICT) → publisher.publish → dedup.mark`. 의사코드(L185~201)에는 함의되어 있으나, Redis 단절 fallback 시 dedup.mark 누락 가능성이 있어 시퀀스 다이어그램 + 실패 분기를 design.md §시퀀스에 명시 필요.
2. **Redis Streams 후행 계약 명시** — FR-14 의 `MAXLEN ~10000` 은 분당 4건 × 4h ≈ 1.7일 보관. BAR-58 임베딩 consumer backlog 시 손실 위험. design 단계에서 **consumer group 이름 + ACK/PEL 정책 + MAXLEN 재산정 근거(SLA 기반)** 를 BAR-58 진입 전에 계약화.
3. **InMemory 한계 파라미터를 config 화** — InMemory dedup TTL 24h vs Redis TTL 72h 비대칭, InMemoryStreamPublisher Queue maxsize 미명시. 두 항목을 `settings.py` 의 `NEWS_DEDUP_TTL_HOURS` / `NEWS_INMEMORY_QUEUE_MAX` 로 노출하고 두 백엔드 모두 동일 값 사용을 강제 → 환경 간 동작 일관성 확보.

### blocker
없음. design 단계 council 진입 가능.

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Grep (architect 역할 검토 — 산출물·모델·repo 패턴 정합성 확인)
⏭️ Not Used: /pdca skill·gap-detector·report-generator (본 요청은 plan 단계 council 의 architect 단일 역할 검토라 PDCA 진행 명령 부적합); TodoWrite (단일 검토 산출 — 다단계 추적 불필요)
💡 Recommended: 5인 council(enterprise/infra/bkend/qa/security) 결과 종합 후 `/pdca design BAR-57` — design.md 작성 단계 진입
─────────────────────────────────────────────────

---

## developer (exit=0)

## Developer 작업 — BAR BAR-57 plan

### 산출 파일
- (plan 단계: 코드 변경 없음 — 의견·체크리스트만)

### 검증 항목 (구현 가능성 점검)
- ✅ `httpx>=0.27.0`, `pydantic>=2.7.0`, `aiosqlite>=0.20.0` 이미 `backend/requirements.txt` 에 존재.
- ⚠️ **신규 의존성 3종 부재**: `apscheduler`, `feedparser`(또는 `lxml`), `redis-py`(또는 `coredis`). do 단계 첫 커밋에서 추가 필요.
- ✅ `backend/db/repositories/audit_repo.py` 가 SQLAlchemy `text()` + named param + dialect 분기 패턴을 이미 구현 — FR-18 의 `news_repo` 가 그대로 이식 가능.
- ✅ `alembic/versions/0001_init.py` 정상. 0002 추가 충돌 없음 (`down_revision="0001"`).
- ✅ `backend/core/` 하위에 `news/` 신규 패키지 — 기존 `execution/gateway/scanner/strategy/risk/...` 와 경계 충돌 없음.
- ✅ `backend/tests/news/` 신규 디렉터리 — 회귀 262 → 292 산술 일치.
- ✅ Makefile `test-*` 패턴 (`test-monitoring`, `test-strategy` 등) 충실 — `test-news` 동일 양식 추가 자명.

### 결정: **PASS** (조건 3개 충족 시 do 진입 가능)

### 위험·노트 (3개 권고)
1. **apscheduler 의존성 회피 검토** — `asyncio.create_task` + `asyncio.sleep(60)` + `asyncio.shield` 패턴만으로도 1분 polling + 격리 + cancel 가능. 의존성 1개 줄고 trigger drift 보정 (NFR-04) 도 직접 제어 가능. design council 의 architect/infra 와 1라운드 합의 후 결정 권고. 변경 시 plan §6 의사코드 첫 줄만 수정.
2. **`INSERT ... ON CONFLICT (source, source_id) DO NOTHING` 단일 syntax** — SQLite 3.24+ 와 Postgres 모두 동일 지원. FR-18 의 `(Postgres) / INSERT OR IGNORE (SQLite)` 양립 표현은 dialect 분기 불필요. audit_repo 의 metadata JSON 분기와 달리 단일화 권고 — 구현 코드/테스트 양쪽이 단순해짐.
3. **`NewsItem.tags: tuple[str, ...]` ↔ JSONB list 변환층 명시** — Pydantic v2 frozen tuple 은 JSONB 적재 시 list 로 풀려, fetch 후 `tuple(sorted(set(...)))` 복원이 필요. `news_repo._row_to_item()` 헬퍼를 design 단계에서 명시 권고. 단위 테스트 1건 (round-trip equality + frozen 보장) 추가 — 30건 한도 내 흡수 가능.

### 변경 범위 보고 (충돌 회피)
- 본 pane 은 plan 단계라 직접 코드 수정 없음. do 진입 시:
  - `backend/requirements.txt` 추가 3종 → architect/infra pane 과 1회 sync 필요
  - `alembic/versions/0002_news_items.py` 신규 → 단독 커밋 가능
  - `backend/core/news/` + `backend/tests/news/` 신규 → 충돌 없음

---
─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Grep (간접) — plan 문서·기존 audit_repo·alembic·requirements 검증
⏭️ Not Used: TodoWrite (단일 응답 점검이라 트래킹 불요), AskUserQuestion (auto mode + 역할 한정 보고이므로 질문 X), pdca skill (다른 pane 의 CTO Lead 가 종합 담당)
💡 Recommended: design 단계 5인 council 진입 시 본 권고 3건을 design 매트릭스에 반영 → 이후 `/pdca do BAR-57` 첫 커밋으로 의존성 추가 + alembic 0002 + `news_repo` 골격 시작
─────────────────────────────────────────────────

---

## qa (exit=0)

baseline 262 passed + 1 skipped 일치 확인. plan 검증 완료.

## QA 검증 — BAR BAR-57 plan

### 결과
**WARN**

### 테스트 메트릭
- 신규 (계획): 30 cases — 모델 5 / RSSSource 5 / DARTSource 5 / Deduplicator 5 / Publisher 4 / Collector 6 (합 30, do 단계에서 alembic 왕복 테스트 별도 가산 가능)
- 회귀 베이스라인: 262 passed / 1 skipped / 0 failed (직접 재실행 확인 — 일치)
- DoD 회귀 목표: ≥ 292 passed (262 + 30) — 산술 적정
- coverage 목표: **명시 누락** ⚠️

### 누락·위험
1. **coverage 임계 미명시** — `backend/core/news/*` 패키지 신규 도입인데 DoD 5-1 에 coverage 라인 부재. QA 표준 ≥ 70% (자금흐름 관련 아니므로 80% 불요) 가 plan 에 박혀야 do 게이트가 단단해짐.
2. **NFR-01 (RSS P95 ≤ 500ms) 자동 게이트 부재** — `pytest --durations=20 + benchmark fixture` 만 언급, CI fail 기준 (예: pytest-benchmark `--benchmark-max-time` 또는 회귀 ≥ 1.5x 시 fail) 없음. 측정만 하고 통과 게이트화 X 면 NFR 으로 약함.
3. **dedup TTL 매트릭스 부족** — FR-12 에서 "TTL 만료 시 재게재 가능, fetched_at 갱신 후 publish" 정책이 명시됐으나 5-1 의 Deduplicator 테스트 5건 분류 (hit/miss/TTL 만료/Redis hit/miss) 에서 **재게재 시 publish 발생** 케이스가 NFR-02 (중복률 0%) 와 충돌하지 않음을 검증하는 분리 케이스가 불명. "24h 내 재방문=hit / TTL+1s 후 재방문=miss + publish 1회" 둘 다 명시 권고.
4. **publisher backpressure** — `InMemoryStreamPublisher` 가 `asyncio.Queue` 무제한 grow 시 BAR-58 consumer 지연 시나리오 단위 테스트 없음. 운영 영향은 BAR-57b 라 BLOCK 까진 아니지만 인터페이스 시점에 maxsize 시그니처는 결정 필요.

### 권고 (3개 이내)
- **(1) DoD 5-1 에 coverage ≥ 70% 라인 추가** — `pytest --cov=backend.core.news --cov=backend.db.repositories.news_repo --cov-fail-under=70` 게이트 명시.
- **(2) NFR-01 자동 fail 기준 명시** — pytest-benchmark 또는 `assert duration < 0.5` 마이크로벤치 케이스 1건 30+ 매트릭스에 포함.
- **(3) dedup TTL 매트릭스를 5건→6건으로 확장** — "TTL 만료 후 재방문 → publish 1회" 케이스 분리, NFR-02 의 "중복률 0%" 정의가 (source, source_id, fetched_at 윈도우) 인지 (전 구간) 인지 plan 본문에 한 줄 정의.

design 단계 진입 가능 (WARN — coverage·benchmark 게이트 두 항목은 design 의 qa-strategist 매트릭스에서 흡수 필요).

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Glob (회귀 베이스라인 검증), QA 역할 — plan DoD/NFR/테스트 매트릭스 검수
⏭️ Not Used: gap-detector (plan 단계라 구현 없음), pdca-iterator (개선 사이클 아님), code-analyzer (코드 변경 없음) — 모두 plan 단계 비대상
💡 Recommended: design 단계 진입 시 design-validator + qa-strategist 매트릭스 (`/pdca design BAR-57`) 로 본 권고 3건 흡수
─────────────────────────────────────────────────

---

## reviewer (exit=0)

## Reviewer 리뷰 — BAR BAR-57 plan

### 종합
**APPROVE with minor changes** (PASS, 일부 명세 모순 해결 권고)

### 발견 (5개)
- **[중요] FR-12 ↔ FR-19 모순**: FR-12 는 "TTL 만료 후 재게재 시 fetched_at 만 갱신 (publish 한다)" 로 명시되나, FR-19 는 "repo 가 0 row 영향 (이미 존재) 면 publish 도 skip". UNIQUE 충돌 시 ON CONFLICT DO NOTHING 이면 fetched_at 갱신 자체가 일어나지 않음. 두 정책 중 하나로 정리 필요 — 권장: **재게재 0건 정책 (NFR-07)** 와 정합되도록 FR-12 의 "재발행 가능" 절을 *재발행 안 함* 으로 단순화하거나, ON CONFLICT DO UPDATE SET fetched_at = EXCLUDED.fetched_at 으로 명시.
- **[중요] FR-08 'in-process counter'**: prometheus client 도입 여부가 불분명. 단순 `dict[str,int]` counter 인지, prometheus_client 의 in-memory Registry 인지 design 단계에서 확정 권장 (BAR-57b 와의 코드 재사용성 결정).
- **[경미] PR 분할 정책 누락**: BAR-56 은 단일 PR 로 머지되었음 (#85 #86 #87). 본 BAR-57a 도 단일 PR 인지, 5 PR 분할 (모델/sources/dedup/publisher/collector+repo) 인지 명시 권장. 표준 일관성 측면에서 BAR-56 패턴 (단일 PR) 추정.
- **[경미] NFR-01 200KB 페이로드 fixture**: 측정 기준이 mock httpx 라면 latency 의 의미가 약함 (실 네트워크가 아니라 parse 시간만 측정됨). 'parse latency P95 ≤ 500ms' 로 표현 정정 권장.
- **[경미] 30+ 테스트 = 정확히 30**: 5+5+5+5+4+6 합산이 30. "≥ 30" 표현 또는 여유분 (시각 파싱 edge / DART corp_name 결합 등) 2-3건 추가 권장.

### 표준 일관성
- **커밋 메시지 prefix**: 일치 (`docs(BAR-57):` — BAR-56 패턴과 동형).
- **a/b 분리 정책**: 일치 (BAR-54a/54b, BAR-56a/56b 와 동일 골격, deferred 항목 명시 일관).
- **5 PR pattern**: **명시 누락** — plan 본문에 PR 분할 정책 절 추가 권장 (단일 PR 추정).
- **DoD 측정 가능성**: 우수 — 회귀 ≥ 292 / gap ≥ 90% / 30+ 테스트 / alembic 왕복 PASS 모두 정량 검증 가능.
- **plan 헤더 형식**: BAR-56 plan 과 일관.

권고 사항 3건은 design 단계 council 에 그대로 인계 가능 (특히 FR-12↔FR-19 정합은 bkend-expert 가 ON CONFLICT 정책 결정 시 우선 처리).

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Glob (plan 문서 발견 + 검토)
⏭️ Not Used: /pdca, gap-detector, design-validator (reviewer 역할은 의견 제시만 — 5 역할 결과는 CTO Lead 가 종합)
💡 Recommended: 본 plan 확정 후 `/pdca design BAR-57` (5인 council — enterprise/infra/bkend/qa/security)
─────────────────────────────────────────────────

---

## security (exit=0)

## Security 검토 — BAR BAR-57 plan

### 종합
**PASS** (with WARN — design council 단계에서 반드시 흡수해야 할 권고 3건)

### 발견 (CWE / OWASP 매핑)

- **[MEDIUM] CWE-918 SSRF / CWE-319 Cleartext Transport**
  `RSS_FEEDS: list[str]` 가 env 로 주입되는 free-form URL 리스트. plan §8 에 "RSS URL allowlist" 가 security-architect 책임으로 명시는 되어 있으나, plan 본문 (FR-04, settings.py 갱신, DoD 5-1) 어디에도 **scheme=https 강제 / 도메인 allowlist 검증 지점** 이 명문화되어 있지 않음. design 단계에서 누락 위험.

- **[MEDIUM] CWE-532 Insertion of Sensitive Info into Log**
  DART API 는 `crtfc_key` 를 query string 에 실어 보내는 구조. FR-08 `news_collector_errors_total{source}` 카운터 + retry 로깅 명시되어 있으나, 에러 로그·exception trace·httpx Request repr 에 `crtfc_key` 가 평문 포함될 수 있음. plan 에 redaction 정책 부재.

- **[LOW] CWE-117 Improper Output Neutralization for Logs**
  RSS entry.title/body 외부 문자열이 그대로 metric label / log message 로 흘러갈 가능성. label cardinality 폭주 + log injection.

- **[LOW] 자금흐름 / audit log 영향 없음** — area:money 비해당. NewsItem 은 공개 RSS/DART 정보로 PII 미포함 (plan §8 security-architect 책임으로 명시됨). area:security 부분 해당 (외부 호출 + DART secret).

### 권고 (3건, design 단계 위임)

1. **DART_API_KEY 흐름 명시** — design 단계 security-architect 산출물 "secret 흐름도" 에 ① `pydantic-settings` 의 `SecretStr` 타입 강제, ② httpx Request 직렬화·exception 메시지의 `crtfc_key` redaction 헬퍼, ③ logger filter (regex `crtfc_key=[^&]+` → `crtfc_key=***`) 를 설계 항목으로 못박을 것. CWE-532.

2. **RSS_FEEDS 검증 게이트** — `settings.py` 의 `RSS_FEEDS` 에 Pydantic validator 추가: scheme ∈ {`https`} / 도메인 ∈ allowlist (한경/매경/연합/이데일리). 임의 URL 주입 차단 (config tampering 시 SSRF/MITM). DoD 5-1 에 "validator 단위 테스트" 1건 추가 권고. CWE-918/319.

3. **BAR-57b 운영 강등 audit** — Redis 단절 시 `RedisStreamPublisher → InMemoryStreamPublisher` 자동 강등 (위험 §7) 은 운영상 **데이터 휘발 + 무공지** 위험. BAR-57b DoD 에 "강등 발생 시 audit_log 1행 + alert page" 명문화 권고 (본 BAR-57a plan 에서 사전 합의만).

위 3건 모두 **plan 자체의 BLOCK 사유는 아님** — design council 의 security-architect 책임으로 이미 위임되어 있고, plan §8 에서 산출물 형태 (위협 모델 + secret 흐름도) 가 약속되어 있음. design 단계 진입 시 본 권고 3건을 산출물 입력으로 전달.

---

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan 파일 검토), Glob/Bash (산출물 확인)
⏭️ Not Used: /pdca, gap-detector, code-analyzer (본 작업은 plan 단계 보안 의견 1건 산출 — PDCA cycle 진행은 CTO Lead 가 5역할 종합 후 수행)
💡 Recommended: design 단계 진입 시 `/pdca design BAR-57` + security-architect agent 호출 (위협 모델 1쪽 + secret 흐름도 산출 — 본 의견 3건을 입력으로)
─────────────────────────────────────────────────
