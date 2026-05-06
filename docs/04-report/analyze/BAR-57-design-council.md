# Team Agent Combined Output

- BAR: BAR-57
- Stage: design
- Generated: 2026-05-07 01:11:16 KST
- Roles: architect developer qa reviewer security


---

## architect (exit=0)

design.md 가 아직 없는 design-input 단계로 확인. plan(247줄) + plan-council 5역할 종합 + 기존 architecture(`backend/core/{gateway,market_session,...}`, `audit_repo.py`, `order.py` frozen, `0001_init.py` JSONB/TIMESTAMPTZ, `settings.py` redis_url 자리) 일치 여부 검토 완료. 산출:

## Architect 검토 — BAR BAR-57 design

### 결과
**PASS** (council 진입 가능, design.md 작성 시 명세 5건 반드시 포함)

### 핵심 발견 (5개)
1. **모듈 경계 일관성**: `backend/core/news/{sources,collector,dedup,publisher}` + `backend/db/repositories/news_repo.py` + `backend/models/news.py` 분할은 기존 7개 core 패키지(`execution/gateway/market_session/monitoring/risk/scanner/strategy`) 와 직교. INxtGateway·MarketSessionService·CompositeOrderBookService·SmartOrderRouter (시장 데이터 라인) 와 정보 데이터 라인이 분리되어 의존성 충돌 0.
2. **frozen 모델 정합**: `OrderRequest`(`backend/models/order.py:39-48`) 의 `ConfigDict(frozen=True)` 패턴이 그대로 `NewsItem` 에 이식 가능. NewsItem 은 자금흐름 X → Decimal 정책 N/A. `tags: tuple[str,...]` 은 frozen 호환 (list 는 hash 불가).
3. **dialect 분기 패턴 동형**: `audit_repo.py:44` 의 `if db.engine.dialect.name == "sqlite": json.dumps(...)` 분기가 `news_repo` 의 `tags JSONB` 적재에도 그대로 필요. plan FR-18 의 `ON CONFLICT DO NOTHING` 단일화(developer council 권고 #2) 와 결합하면 dialect 분기는 metadata 직렬화 1지점만 남음.
4. **DI 라이프사이클 결합 미정**: `backend/core/orchestrator.py` 가 존재하지만 plan 어디에도 NewsCollector 의 부팅·종료 hook(start/shutdown) 이 orchestrator 에 통합되는지 명시 없음 — design 단계에서 결정 필요.
5. **settings.py 선점 일치**: `redis_url`/`redis_streams_enabled` 슬롯이 이미 BAR-42 에서 placeholder 로 선점됨. `NEWS_STREAM_BACKEND`/`NEWS_DEDUP_BACKEND`/`DART_API_KEY: SecretStr | None`/`RSS_FEEDS: list[HttpUrl]`/`NEWS_DEDUP_TTL_HOURS`/`NEWS_INMEMORY_QUEUE_MAX` 6개 추가만 남음.

### 권고
1. **design.md 에 시퀀스 다이어그램 + DI 컨테이너 윤곽 명시** — `dedup.seen → repo.insert(ON CONFLICT) → return inserted? → publisher.publish → dedup.mark` 4단(reviewer 발견 FR-12↔FR-19 모순 해소 포함) 과 `NewsCollector(sources, repo, publisher, dedup, http_client)` 의 orchestrator 라이프사이클 결합 지점을 한 절로.
2. **alembic 0002 dialect 매트릭스** — `tags JSONB(Postgres)/JSON-as-TEXT(SQLite)`, `published_at TIMESTAMPTZ`, UNIQUE(source,source_id) 의 SQLite/Postgres 양립 syntax 표를 design.md 에 못 박아 do 진입 시 재논의 차단.
3. **Redis Streams 계약 1쪽** — stream `news_items`, `XADD MAXLEN ~10000`, payload `{"payload": NewsItem.model_dump_json()}`, consumer group 이름 `news_consumers_v1` 후보 + ACK/PEL 정책을 BAR-58 진입 전에 인터페이스 동결 (plan-council architect 권고 #2 흡수).

### blocker
없음. 위 권고 3건이 design.md 에 흡수되면 do 진입 가능.

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Glob — plan + plan-council + audit_repo + order.py + 0001_init.py + settings.py 정합 검증 (architect 단일 역할)
⏭️ Not Used: TodoWrite (단일 검토 산출물), AskUserQuestion (auto mode + 역할 한정 보고), gap-detector/code-analyzer (design-input 단계는 코드 변경 X), /pdca skill (CTO Lead 가 5역할 종합 후 design.md 작성 시 호출)
💡 Recommended: 5역할(architect/developer/qa/reviewer/security) 결과 종합 → CTO Lead 가 `docs/02-design/features/bar-57-news-collection.design.md` 작성 → `/pdca do BAR-57` 진입
─────────────────────────────────────────────────

---

## developer (exit=0)

# Developer 의견 — BAR-57 design

**결정: WARN** — plan 인터페이스는 구현 가능. 다만 design 문서에 **3개 항목** 명세 보강 필요.

## 산출 파일 (design 단계 — 코드 변경 없음)
- 본 pane 코드 수정 없음. design 검토 의견만 제출.

## 구현 가능성 재확인 (PASS 항목)

| 모듈 | 구현 가능성 | 근거 |
|------|:---:|------|
| `NewsItem` (Pydantic v2 frozen, tuple tags) | ✅ | `model_validator(mode="after")` 로 tags 정렬·중복 제거 후 tuple 캐스팅 → frozen 호환 |
| `NewsSourceAdapter` Protocol | ✅ | runtime_checkable Protocol 단일 메서드, mock 용이 |
| `news_repo` (text() + named) | ✅ | `audit_repo.py` 동일 패턴 — dialect 분기는 dict→json.dumps 만 추가 |
| `alembic 0002` UNIQUE(source, source_id) | ✅ | 0001 와 동일 SQLAlchemy idiom, postgresql.JSONB + sa.Text 양립 |
| Collector 호출 시퀀스 4단 (council 권고) | ✅ | plan §6 의사코드와 일치 |

## 위험·노트 (3개 권고)

1. **신규 의존성 4종 (apscheduler / feedparser / redis / lxml) 이 `backend/requirements.txt` 에 미반영.**
   현재 requirements 에 httpx 만 존재. design 문서에 **버전 핀 + extras** 명시 필요:
   `apscheduler>=3.10`, `feedparser>=6.0`, `redis>=5.0` (asyncio 포함), `lxml>=5.0` (RSS XML fallback).
   → BAR-57a do 단계 첫 commit 에 requirements 갱신 + `pip install -e .` 검증 게이트.

2. **retry + timeout 예산 충돌 — `asyncio.wait_for(src.fetch, timeout=30)` 안에 "retry 1회 + 백오프 1s" 가 수용되는지 모호.**
   단일 시도가 30s 까지 늘어나면 retry 자체가 timeout 에 의해 취소된다. design 에서 **예산 분할 명세**:
   - 단일 HTTP 시도 timeout = `httpx.Timeout(10s)` (httpx 레벨)
   - 어댑터 fetch 전체 wait_for = 30s (retry 1회 + 백오프 1s 포함, 즉 10 + 1 + 10 = 21s 여유)
   → 단위 테스트 시나리오에 "1회 실패→2회 성공", "1회 실패→2회 timeout→cancel" 분리.

3. **`news_repo.insert` 의 ON CONFLICT — SQLite/Postgres 분기 명시 필요.**
   plan FR-18 에 양 dialect 언급은 있으나 SQL 단일 문자열로는 양립 불가. design 에서 **dialect 분기 메서드** 패턴 (audit_repo 의 `db.engine.dialect.name == "sqlite"` 분기) 채택 + tags JSONB ↔ TEXT(JSON) 직렬화도 동일 분기로 통일. 회귀 테스트는 SQLite 위에서 돌므로 dialect 양립이 ≥ 292 passed 의 전제.

## 추가 노트 (info-only)
- `apscheduler.AsyncIOScheduler` 단위 테스트는 scheduler.start() 호출 금지 — `collector.tick()` 직접 호출로 결정성 확보 (pytest-asyncio mode=auto 호환).
- `httpx.AsyncClient` lifespan 은 FastAPI `app.router.lifespan_context` 에 위임 (BAR-57b 진입 시).
- Redis Streams `MAXLEN ~10000` + consumer group 명세는 BAR-58 의 진입 계약이므로 design 에 stream key, consumer group name (`news_consumers`), payload schema (`{"payload": "<NewsItem.model_dump_json()>"}`) 까지 고정 권장.

**최종: WARN** — 위 3개 항목이 design 문서에 명세되면 PASS. do 단계 진입 가능.

---

📊 bkit Feature Usage
- ✅ Used: Read, Bash, Grep, Write, /pdca design (council 위임 컨텍스트)
- ⏭️ Not Used: TodoWrite (단일 산출 task), gap-detector (do 이후), pdca-iterator (analyze 이후)
- 💡 Recommended: design 문서 확정 후 `/pdca do BAR-57` — 30+ 단위 테스트 + alembic 0002 + 회귀 ≥ 292 passed

---

## qa (exit=0)

## QA 검증 — BAR BAR-57 design

### 결과
**WARN** (design council 진입은 허용, 단 매트릭스 5개 항목 보강 필수)

### 테스트 메트릭
- 신규 (계획): **30 cases** — 모델 5 / RSS 5 / DART 5 / Dedup 5 / Publisher 4 / Collector 6
- 회귀 베이스라인: **262 passed / 1 skipped / 0 failed** (직접 재실행 확인 — plan 의 262 와 일치, role 프롬프트 240 은 BAR-56a 이전 수치)
- DoD 목표: ≥ **292 passed** (262 + 30) — 산술 적정
- coverage 목표: **명시 누락** ⚠️ (design.md 에 박혀야 함)

### 누락·위험
1. **coverage 게이트 미명시** — `backend/core/news/*` + `backend/db/repositories/news_repo` 신규 패키지인데 plan-council 1차 권고 미흡수. design.md 에 `pytest --cov=backend.core.news --cov=backend.db.repositories.news_repo --cov-fail-under=70` 박을 것 (자금흐름 비해당 → 80% 불요).
2. **NFR-01 P95 ≤ 500ms 자동 fail 기준 부재** — "측정만 하고 fail 안 함" 은 NFR 약화. parse-only 마이크로벤치 1건 (`assert duration < 0.5`) 매트릭스 명시.
3. **30 cases 정확히 30** — reviewer 권고 미흡수. design 단계에서 ≥ **34 cases** 로 확장 권장: ① TTL 만료 후 재방문 → publish 1회 (NFR-02 vs FR-12↔FR-19 모순 검증), ② Collector 4단 시퀀스 (`dedup.seen → repo.insert → publisher.publish → dedup.mark`) 명시 통합 케이스 1건, ③ RSS_FEEDS Pydantic validator (https + 도메인 allowlist, security 권고 2), ④ DART `crtfc_key` redaction 테스트 (security 권고 1, CWE-532).
4. **publisher backpressure** — `InMemoryStreamPublisher` `asyncio.Queue` maxsize 미명시. design 에서 `NEWS_INMEMORY_QUEUE_MAX` settings 노출 + queue full 시 drop/block 정책 1건 케이스화.
5. **Alembic 0002 왕복** — plan 에서 "별도 가산" 표현 모호. design 매트릭스에 up/down + UNIQUE(source, source_id) 충돌 + JSONB↔tuple round-trip 명시 (3건 추가 → 회귀 ≥ 295).

### 권고 (3건)
1. coverage `--cov-fail-under=70` + benchmark assert 게이트 design.md 박기.
2. 매트릭스 30 → **≥ 34** 확장 (TTL/시퀀스/SSRF validator/redaction).
3. FR-12 ↔ FR-19 재게재 정책 단일화 결과를 dedup TTL 테스트 fixture 의 expected 값으로 못박기 (NFR-02 정의 명문화).

design 진입 가능 — qa-strategist 가 매트릭스 흡수 시 do 게이트 단단해짐.

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan/plan-council/유사 design 확인), Bash (회귀 baseline 재측정 — 262 passed 확인), Grep — QA 역할 design 매트릭스 검수
⏭️ Not Used: gap-detector·pdca-iterator·code-analyzer (design 단계 의견 산출 — 코드 변경/구현 없음, PDCA 진행은 CTO Lead 종합), TodoWrite (단일 검토)
💡 Recommended: CTO Lead 가 5 역할 종합 후 `/pdca design BAR-57` 로 design.md 작성 → qa-strategist 가 본 권고 3건 흡수 → `/pdca do BAR-57` 진입
─────────────────────────────────────────────────

---

## reviewer (exit=0)

## Reviewer 리뷰 — BAR BAR-57 design

### 종합
**COMMENT (사전 가드레일)** — design 산출물 자체가 미작성 상태(architect/developer/qa/security output.md 모두 0 byte). 본 리뷰는 plan council 권고 3건 + 미해결 모순 1건이 design.md 에 어떻게 안착되어야 하는지 사전 체크리스트로 제시.

### 발견 (7개)

- **[차단] FR-12 ↔ FR-19 정책 모순 미해결** — plan reviewer 가 지적했던 "TTL 만료 후 재게재 시 publish (FR-12) vs 0 row 시 publish skip (FR-19)" 모순이 plan PR #88 머지 시점에 해결되지 않았음. design.md §시퀀스에서 **두 정책 중 하나를 명시 채택** 필요. 권장: NFR-07 (재게재 0건) 정합 → ON CONFLICT DO NOTHING 유지 + FR-12 의 "fetched_at 갱신 후 publish" 절 삭제. bkend-expert 산출물 1순위.

- **[중요] 4단 호출 시퀀스 다이어그램 부재 가드** — `dedup.seen → repo.insert → publisher.publish → dedup.mark` 가 의사코드(plan §6 L185~201)에는 있으나 design 단계에서 **각 단계 실패 분기** (publisher.publish 실패 → dedup.mark skip → 다음 사이클 재방문 가능) 를 mermaid sequence + 예외 표로 못박지 않으면 do 단계 회귀 위험.

- **[중요] Redis Streams 계약 명세 누락 위험** — FR-14 의 `MAXLEN ~10000` 만으로는 BAR-58 consumer 진입 불가. design.md 에 **stream key (`news_items`) / consumer group 이름 (`embedder_v1` 등) / XADD field 스키마 (`payload` 단일 vs 멀티 필드) / ACK·PEL 정책 / MAXLEN 재산정 근거 (분당 expected events × retention SLA)** 5개 항목 표 강제.

- **[중요] 타입 매핑 표 (Pydantic ↔ JSONB ↔ Postgres) 누락 위험** — developer 권고 #3 의 "tags: tuple[str,...] ↔ JSONB list round-trip + frozen 보장" 은 design 단계 매핑 표에서 명시 필요. NewsItem 9개 필드 × (Python type / Pydantic v2 declaration / Postgres column / SQLite fallback) 4열 표 권장. BAR-56 design §타입 매핑 패턴 차용.

- **[중요] InMemory 한계 파라미터 settings.py 노출 미반영** — plan council 권고가 design.md §config 표에 `NEWS_DEDUP_TTL_HOURS=24` (Redis 72h 와 비대칭 방지) / `NEWS_INMEMORY_QUEUE_MAX=10000` / `NEWS_FETCH_TIMEOUT_SECONDS=30` 3개 항목으로 명문화되어야 함.

- **[경미] 30+ 테스트 매트릭스가 정확히 30** — plan reviewer 권고대로 design 의 qa-strategist 매트릭스에서 32~33건으로 여유분 흡수 (시각 파싱 edge / DART corp_name 결합 / TTL 만료 분리 / publisher backpressure 1건씩).

- **[경미] design 문서 헤더 형식** — BAR-56 design (Plan 경로/Phase/Status/Date/Worktree 5줄 + §0 분리정책 표) 와 동일 양식 강제. Status 는 `Draft (council: enterprise-expert + infra-architect + bkend-expert + qa-strategist + security-architect)` 로 5인 모두 명기.

### 표준 일관성
- **커밋 메시지 prefix**: 일치 예상 — `docs(BAR-57): ... design (Phase 3 BAR-57)` (BAR-56 패턴 동형).
- **5 PR pattern**: **명시 누락** — BAR-56 은 단일 PR 머지(#85→86→87 분리는 do/analyze/report 단계 분리). BAR-57a 도 단일 design PR 추정이나 design.md §0 에 PR 분할 정책 1줄 명문화 권장.
- **a/b 분리 표**: BAR-56a/56b · BAR-54a/54b 헤더 양식 준수 필수.
- **plan council ↔ design council 역할 매핑**: plan §8 의 5인 표가 design.md §0.3 (결정 근거) 에 그대로 인용되어야 추적성 유지.

design.md 작성 시 위 7건 (특히 [차단] 1건) 을 우선 흡수하면 do 단계 진입 가능. CTO Lead 종합 시 본 의견 + architect/developer/qa/security 4건 종합 권장.

---

## security (exit=0)

## Security 검토 — BAR BAR-57 design

### 종합
**WARN** — 비밀 자격증명 평문 노출 없음 (DART API 키·jwt_secret·postgres_password 모두 SecretStr ✅). `text()` + named param 으로 SQL injection 차단 (audit_repo 패턴 답습 ✅). 단, design 단계에서 **외부 입력 면 (RSS URL · DART query) 시그니처와 SecretStr 흐름** 을 명시해야 안전 봉인.

### 발견 (CWE / OWASP 매핑)
- **[HIGH] CWE-918 / OWASP A10 SSRF — `settings.rss_feed_urls: list[str]`** 가 env 주입 list 로 열려 있고, plan FR-04 RSSSource 가 직접 httpx GET. 악성 URL (file://, internal IP, metadata endpoint) 차단 시그니처가 design 에 없음. → `NewsSourceAdapter.fetch` 호출 전 `https://` scheme + host allowlist (한경/매경/연합/이데일리 도메인 4개) 검증 메서드 명시 필수.
- **[MEDIUM] CWE-532 로그 노출 — DART `crtfc_key`** 가 URL 쿼리스트링 (`opendart.fss.or.kr/api/list.json?crtfc_key=...`) 으로 전달. httpx 예외 로그/`news_collector_errors_total` debug trace 에 평문 흘러갈 위험. → DARTSource 시그니처에서 `api_key: SecretStr` 받고, `httpx` 호출 시 `params` dict 로 분리 + 로그에서 query string 마스킹 (`*url.replace(crtfc_key=*, crtfc_key=***)*`) design 명시.
- **[MEDIUM] CWE-522 — `redis_url: Optional[str]`** (settings.py:58) 가 plain str. `redis://:password@host:6379` 형태 시 비밀 평문 보관. → BAR-67 일괄 SecretStr 전환 전에라도 본 BAR design 에서 SecretStr 로 승격 권고 (RedisDeduplicator·RedisStreamPublisher 시그니처에서 `redis_url: SecretStr` 수신).
- **[LOW] CWE-1284 — `source_id` (rcept_no / entry.id)** 외부 입력이 길이/문자 검증 없이 dedup key + DB 적재. named param 으로 SQLi 는 차단되나, 비정상 길이 (1MB) 페이로드로 메모리 LRU 폭주 가능. → NewsItem Pydantic field 에 `max_length=256` 검증 명시.

### 권고
1. **design 문서 §위협모델** 에 SSRF allowlist (host 4개 화이트리스트) + DART query 마스킹 + redis_url SecretStr 3개 항목 명시.
2. **NewsItem** Pydantic field `source_id: Annotated[str, StringConstraints(max_length=256, pattern=r"^[\w\-/.]+$")]` — DART rcept_no(14자리 숫자) / RSS guid(URL 형) 양립.
3. **후속 BAR 명시** — PII redaction (RSS body 내 기자 이메일·전화) 은 BAR-58 임베딩 전처리에서 처리 + 본 BAR-57b 운영 진입 시 DART 401/429 응답을 audit_log 에 기록 (BAR-67 보안 트리거).

비밀 평문 노출 없음 → BLOCK 아님. design 단계에서 위 3 시그니처 반영 시 PASS 전환.

─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan + settings.py + audit_repo), Bash (디렉터리 스캔), Grep (없음 — 직접 read 로 충분)
⏭️ Not Used: /pdca design (CTO Lead 가 5인 council 종합 단계에서 호출 예정 — 본 pane 은 security 의견만 산출), security-architect 에이전트 (본 역할이 이미 security 시각이므로 중복 호출 회피)
💡 Recommended: CTO Lead 가 5 council 결과 종합 후 `/pdca design BAR-57` → design.md 작성. 그 후 `/pdca do BAR-57` 진입 시 본 검토의 3개 권고가 NewsItem/DARTSource/Settings 시그니처에 반영되었는지 gap-detector 로 재검증.
─────────────────────────────────────────────────
