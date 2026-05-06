# Team Agent Combined Output

- BAR: BAR-58
- Stage: design
- Generated: 2026-05-07 01:44:01 KST
- Roles: architect developer qa reviewer security


---

## architect (exit=0)

## Architect 검토 — BAR BAR-58 design

### 결과
**WARN**

### 핵심 발견 (최대 5개)

1. **`news_id` 회수 경로 부재 (FR-10 모순)** — plan 알고리즘은 `news_item.id` 를 `repo.insert(news_id, ...)` 인자로 사용하지만, BAR-57a `NewsItem` (Pydantic frozen) 에는 `id` 필드가 없음. Publisher 의 `model_dump_json()` payload 에는 DB BIGSERIAL id 가 동봉되지 않음. 또한 `NewsRepository.insert` 는 bool 반환이라 id 회수 불가. Worker 가 `news_id` 를 얻을 경로가 닫혀 있음.

2. **XGROUP CREATE 첫 기동 누락 위험** — plan FR-09 는 `$` (latest only) 옵션을 명시하지 않았으나 일반적 디폴트. BAR-57a publisher 가 XADD 한 뒤 BAR-58 consumer 가 처음 기동하면 기존 PEL 이 누락된다. 첫 기동 정책 (`$` vs `0`) design 에 미명시.

3. **graceful shutdown — `XREADGROUP BLOCK=5000ms` 중 인터럽트 race** — `asyncio.Event` set 만으로는 redis-py 의 BLOCK 호출을 즉시 깨우지 못함. SIGTERM 후 최대 5초 대기 + 진행 중 batch 의 partial state 이슈. cancel() / short BLOCK / `redis.aclose()` 중 정책 명시 필요.

4. **batch encode 부분 실패 정책 미정** — `embedder.encode(bodies)` 가 1건 실패로 전체 raise 시 batch 16건 전체 ACK 차단. plan §6 의사코드는 try/except 가 individual insert 만 감싸고 encode 자체는 보호 안 됨. 1건 poison body → batch 전체 멈춤.

5. **모델/차원 교체 정책 부재** — `vector(768)` 컬럼 고정. 향후 openai (1536) / large 모델 교체 시 alembic 신규 revision 필요하나 plan §7 위험표에 명시 X. `UNIQUE(news_id, model)` 만으론 차원 mismatch 흡수 불가.

### 권고
1. **NewsItem 모델 + Publisher payload 보강** — `NewsItem.id: int | None = None` 추가 (frozen 호환, default None). `NewsRepository.insert` 시그니처를 `-> int | None` (id) 로 승격, collector 가 `model_copy(update={"id": new_id})` 후 publisher 에 전달. 또는 publisher payload 에 `db_id` 별도 필드 추가. design §1·§3·§4 에 명세.
2. **Stream consumer 첫 기동 정책 + shutdown 절차 명시** — `XGROUP CREATE news_items embedder_v1 0 MKSTREAM` (운영) / `$` (테스트) 옵션화. `BLOCK=500ms` (혹은 `1000ms`) 로 단축 + stop_event check loop. design 의 `EmbeddingWorker.start()` pseudo 에 lifecycle 다이어그램 첨부.
3. **batch encode failure boundary + dim 변경 운영 노트** — `_process` 에서 `encode()` 자체를 try/except 로 감싸 batch 전체 실패 시 entire batch NACK (PEL 잔존, claim 으로 회복) 또는 individual fallback 정책 결정. design §7 에 "모델 차원 변경 시 alembic 0004+ 신규 + UNIQUE 키 (news_id, model) 자연 분리" 운영 노트 추가.

### blocker (있으면)
- 없음. 본 3건을 design.md 에 명세 흡수하면 do 진입 가능. **재실행 조건**: design.md §3·§4·§6·§7 에 (1)(2)(3) 흡수 후 architect 재검토 1회.

---

```
─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read, Bash, Grep (architect 검토 — plan + 인접 design 패턴 + 코드 구조 확인)
⏭️ Not Used: TodoWrite (단일 검토 작업, 진행 추적 불필요), Agent (review-only, sub-delegation 부적절), /pdca skill (외부 supervisor 가 design 단계 dispatch 중 — 본 pane 은 architect role 한정)
💡 Recommended: 본 검토 흡수 후 design.md 갱신 → 다른 4 pane (developer/qa/reviewer/security) 산출과 종합 → /pdca do BAR-58 진입
─────────────────────────────────────────────────
```

---

## developer (exit=0)

## Developer 작업 — BAR BAR-58 design

### 검토 범위
- plan 292줄 + 기존 BAR-57a 패턴 (`backend/core/news/publisher.py`, `backend/db/repositories/news_repo.py`, `alembic/0002_news_items.py`) 대조
- 본 pane 코드 수정 없음 (검토만)

### 결정: **PASS (WITH 3 WARN)**

인터페이스 시그니처·dialect 분기·a/b 분리·25+ 테스트 매트릭스 모두 BAR-57a/56a 답습 패턴이며 구현 가능. 단 design 명세 확정 시 다음 3개 보강 필요.

### 권고 (3개)

**1. WARN — async/sync 경계 명시 누락**
`Embedder.encode` 를 `async def` 로 강제하지만 실 호출은 CPU-bound (numpy hash, sentence-transformers `model.encode`). event loop blocking 위험.
- design 에서 `LocalKoSbertEmbedder.encode` 내부 `asyncio.to_thread(self._model.encode, ...)` 명시
- `FakeDeterministicEmbedder` 는 단순 hash 라 sync OK, 단 Protocol 일관성 위해 동일 패턴 또는 명시적 fallback 코멘트
- 누락 시: do 단계 worker loop 가 첫 batch encode 동안 다른 코루틴 starvation, 단위 테스트는 통과해도 운영 진입(58b) 시 lag 누적

**2. WARN — pgvector driver / 등록 시점 미확정**
plan FR-16 "asyncpg + pgvector adapter" 만 언급, register 시점 미명시. BAR-56a 는 extension 활성화만, bind 검증은 본 BAR.
- design 명세: `pgvector.asyncpg.register_vector(conn)` 호출 위치 (engine pool init / per-connection setup) 확정
- alembic 0003 upgrade 의 SQLite 분기: `vector` 자료형 import 가 SQLite 에서 실패하지 않도록 dialect 분기 (BAR-57a `0002_news_items.py` 의 `is_pg = bind.dialect.name == "postgresql"` 패턴 답습 명시)
- 누락 시: do 단계 `INSERT INTO embeddings ... :vector` 에서 `unsupported type` 디버깅 0.5~1일 추가

**3. WARN — `search_similar` 반환 metric 통일**
Postgres `<=>` 는 **cosine distance** (0~2, 낮을수록 유사), SQLite 직접 계산은 보통 **cosine similarity** (-1~1, 높을수록 유사). plan FR-15 `list[tuple[int, float]]` 만 정의, 의미 미확정.
- design 명세에서 "distance (낮을수록 유사)" 또는 "similarity (높을수록 유사)" 한쪽으로 통일 + repository docstring 강제
- 누락 시: BAR-59 테마 분류기가 임계치 (`< 0.3` vs `> 0.7`) 잘못 사용 → 분류 inversion 발생

### 위험·노트
- `pyproject.toml` 위치 확인 필요 (worktree root `.toml` 단일 파일 가정).
- `NEWS_EMBEDDING_BATCH_SIZE` 기본값 plan 본문은 16, 컨텍스트 요약은 10 — design 에서 16 으로 확정 권고 (XREADGROUP COUNT 도 동일).
- FakeEmbedder `arr.repeat(8)` 는 96차원 entropy 만 사용 (768 위치는 8개씩 동일 값) → 결정성은 OK, 단위 테스트는 norm/dtype 만 검증하므로 통과. 코멘트로 한정 명시 권장.
- 본 pane 코드 수정 없음 → architect/qa/reviewer/security pane 충돌 없음.

---
─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan + 기존 BAR-57a 코드), Bash (구조 탐색), Grep (settings/pgvector 검색)
⏭️ Not Used: TodoWrite (단발 검토 1회 — 작업 트래킹 불필요), pdca-iterator (design 단계 검토만), gap-detector (do 후 단계)
💡 Recommended: design council 5명 종합 후 `/pdca do BAR-58` (위 3 WARN 반영 design 확정 시점)
─────────────────────────────────────────────────

---

## qa (exit=0)

## QA 검증 — BAR BAR-58 design

### 결과
**PASS (with WARN — design 보강 권고 3건)**

### 테스트 메트릭 (plan 기준 — design 단계 검토)
- 신규 명세: **28 cases** (≥ 20 권장 충족) — Protocol contract 9 / Fake 4 / KoSbert mock 3 / Factory 3 / Repo 4 / alembic 1 / Worker 4
- 회귀 목표: **≥ 324 passed** (BAR-57a 누적 299 + 25 신규). 베이스라인 표기 불일치 (prompt: 240 / plan: 299) 는 Phase 2 vs Phase 3 누적 기준 차이 — design 단계에서 명시 필요
- coverage 목표: **≥ 70%** (`backend/core/embeddings/` + `embedding_repo.py`) — 자금흐름·세션 영역 아님, 70% 적정

### 누락·위험 (design 단계 보강 권고)

1. **dim mismatch assert 단위 테스트 누락** — `EmbeddingWorker.__init__` 의 `assert embedder.dim == settings.NEWS_EMBEDDING_DIM` (의사코드 line 188) 이 False 시 AssertionError 검증 케이스가 5-1 DoD 의 worker 4건 (batch / poison pill / consumer group / shutdown) 에 빠짐. design 시 5번째 케이스로 추가 — settings.DIM=512, embedder.dim=768 fixture 로 기동 실패 검증.

2. **search_similar dialect 분기 fixture 명세 부재** — FR-16 의 Postgres `<=>` cosine 와 SQLite 직접 코사인 계산 양쪽 fixture 구성 전략 (testcontainers vs in-memory pgvector dialect mock) 이 plan 에 없음. BAR-56a 에서 pgvector adapter 검증되었으나 본 BAR 의 worktree 단위 테스트가 어떤 경로로 vector 자료형 bind 를 mock 또는 실측할지 design 에서 결정 필요. **수치 오차 허용 (≤ 1e-5)** + Postgres / SQLite 결과 동등성 검증 케이스 추가 권고.

3. **stream_adapter 별도 단위 테스트 + SQLite TEXT 직렬화 round-trip** — `RedisStreamConsumerAdapter` (fakeredis) / `InMemoryStreamConsumerAdapter` 의 ensure_group · read_group · ack 라이프사이클을 worker 테스트와 분리해 별도 2~3건 신설 권고. 동시에 SQLite fallback 의 `np.float32 → json.dumps → np.float32` 정밀도 손실 (NFR-03 의 norm ≈ 1.0 ± 1e-5 위배 가능) round-trip 검증 1건 추가. `kiwipiepy` 의존성은 본 BAR 미사용으로 OOS 적정.

### 결론
시나리오 매트릭스(28 cases) · 회귀 목표 · coverage gate · 결정성/idempotent/graceful shutdown critical points 모두 충족 → **PASS**. dim assert / dialect fixture / adapter 단위 분리 3건은 design 산출물에 반영 권고.

---
─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan 검토), Bash (worktree 구조 확인), QA design-stage review
⏭️ Not Used: gap-detector (do 단계 산출물 부재) / pdca-iterator (design 단계 한정 검토) / pytest 실행 (design 단계 회귀 검증 불필요)
💡 Recommended: 5인 council (architect/developer/reviewer/security 병렬) 종합 후 `/pdca design BAR-58` → bar-58-embedding-infra.design.md 산출 → `/pdca do BAR-58` 진입
─────────────────────────────────────────────────

---

## reviewer (exit=0)

## Reviewer 리뷰 — BAR BAR-58 design

### 종합
COMMENT (design 문서 본문 미생성 — plan 의사코드 + FR/NFR 기준 review)

### 발견 (최대 7개)
- [중요] **NewsItem.id 채움 시점 계약 누락** — worker 의사코드(L218) `repo.insert(news_item.id, ...)` 사용. 그러나 BAR-57a publisher 는 `{"payload": NewsItem.model_dump_json()}` 만 보내며, NewsRepository.insert 후 부여된 id 를 NewsItem 에 재주입해 publish 한다는 계약이 plan 에 비-명시. design 에 "publisher 가 RETURNING id 후 id 채워 publish" 1줄 추가 필요.
- [중요] **search_similar 반환의 cosine 정의 모호** — `list[tuple[int, float]]` 의 float 가 distance(`<=>`) 인지 similarity 인지 미고정. BAR-59 분류기 임계치 방향이 정반대로 갈 위험. design 에 distance 명시 + 정렬 ASC.
- [중요] **EmbeddingWorker.consumer_name 미정의** — 의사코드 L195 `self.consumer_name` 참조하나 `__init__` 에 설정 부재. design 에 `f"embedder-{hostname}-{pid}"` 초기화 + 다중 워커 격리 정책 명시.
- [중요] **shutdown race** — `XREADGROUP block_ms=5000` 동안 stop_event set 시 최대 5s 지연. design 에 `asyncio.wait([read, stop.wait()], FIRST_COMPLETED)` 또는 task.cancel 패턴 명시 (NFR-04 의 batch encode 횟수 = 1 와도 무관해야 함).
- [경미] **BATCH_SIZE 불일치** — plan FR-21 = 16, 본 review prompt 컨텍스트 = 10. design 에서 16 으로 통일 + 사유 1줄.
- [경미] **assert 의존** — FR-02 의 dim 체크가 `assert` (python -O 시 strip). 명시적 `raise ValueError("dim mismatch")` 권고.
- [경미] **FakeEmbedder entropy** — sha256×3 = 96B `.repeat(8)` 시 768-dim 중 unique value 96. 결정성/L2 norm 은 OK 이나 cosine 검색 fixture 가 cluster 편향 가능 — design 의 테스트 fixture 분포 노트 권고.

### 표준 일관성
- 커밋 메시지 prefix: `docs(BAR-58):` — 일치 (PR #93 와 동일 패턴)
- 5 PR pattern (plan/design/do/analyze/report): 일치 — design PR 진입 단계

---

## security (exit=0)

## Security 검토 — BAR BAR-58 design

### 종합
**WARN** — 자격증명 직접 노출/하드코딩은 없으나, design 산출물 작성 시 강제할 보안 명세 4건이 plan 단계에서 누락. design.md 에 아래 권고를 통합하면 PASS 전환 가능.

### 발견 (CWE / OWASP 매핑)

- **[HIGH] 모델 공급망 검증 부재 (CWE-494, OWASP A08:2021)** — FR-05 / FR-06 / BAR-58b 의 ko-sbert (`jhgan/ko-sroberta-multitask`) 다운로드 시 model hash pinning · HuggingFace revision 고정 절차가 plan 본문에 미명세. `SentenceTransformer(model_name, revision=..., cache_folder=...)` 의 revision pin 과 SHA256 검증이 design 의 `LocalKoSbertEmbedder` 시그니처에 강제되지 않으면 typosquatting / mirror 변조 위험. council 8절 위임 문구만 있고 design 내 enforcement 미정.

- **[MEDIUM] 외부 API 키 처리 명세 누락 (CWE-798, OWASP A07:2021)** — `NEWS_EMBEDDING_BACKEND="openai"` 분기 (FR-07) 와 BAR-58b 의 claude-haiku 어댑터 키 보관 정책이 Settings 4 신규 (FR-18~21) 에 부재. design 시그니처에 `api_key: SecretStr` (pydantic) 강제, `__repr__` / 로그 출력 시 마스킹 보장 명시 필요.

- **[MEDIUM] Worker 로그 누설 위험 (CWE-532)** — FR-10 의 poison pill 처리 (`parse 실패 → ACK + error counter`) 와 알고리즘 의사코드의 `except Exception: ERR.inc()` 분기에서 payload/body 가 logger.exception() 으로 흘러갈 수 있음. 뉴스 본문은 공개 데이터이나 RSS source/auth header (BAR-57a 가 fetch 시 attach 한 경우) 가 함께 흐르면 누설. design 에 "에러 로그에 payload dict 직렬화 금지, news_id + stream_id 만 기록" 강제.

- **[LOW] DoS / 자원 입력 검증 부재 (CWE-1284)** — FR-11 batch encode 시 NewsItem.body 길이 상한 미정. 수 MB 본문이 batch 16건 들어오면 ko-sbert encode 메모리 폭증. design 에 `body[:MAX_EMBED_CHARS]` 트렁케이션 (예: 8192 chars) + `NEWS_EMBEDDING_BATCH_SIZE` 상한 검증 (`Field(le=64)`) 명시.

### 권고
1. **design.md `Embedder Protocol` 섹션에 model identity 가드 추가** — `LocalKoSbertEmbedder.__init__(model_name, revision: str, expected_dim: int = 768)` 시그니처 강제, revision 미지정 시 ValueError. BAR-58b 운영 다운로드 절차에 SHA256 manifest 검증 단계 명시.
2. **Settings 보강** — 외부 API 키는 `pydantic.SecretStr` 로 선언, `model_config = SettingsConfigDict(secrets_dir=...)` 패턴 (BAR-42 와 동일). plan FR-18~21 외에 `OPENAI_API_KEY: SecretStr | None`, `ANTHROPIC_API_KEY: SecretStr | None` 을 design 에서 추가하되 본 BAR 에서는 사용 X (NotImplementedError 유지).
3. **Worker error path 명세** — design 의 `_process` 의사코드에 `logger.error("embedding_failed", news_id=..., stream_id=..., err_type=type(e).__name__)` (payload/body 제외) 패턴 강제. body 트렁케이션 (`MAX_EMBED_CHARS`) + batch_size 상한 (`Field(ge=1, le=64)`) 을 FR-21 에 부착.

PII 역산 공격 위험은 뉴스/공시 공개 데이터 특성상 LOW 로 유지. audit log 무결성은 본 BAR 가 자금흐름 미접촉이므로 area:money 게이트 해당 없음.

---
─────────────────────────────────────────────────
📊 bkit Feature Usage
─────────────────────────────────────────────────
✅ Used: Read (plan doc 검토), security council 역할 (BarroAiTrade 자체 PDCA design 단계 검토)
⏭️ Not Used: bkit:pdca / gap-detector / code-analyzer (design.md 미작성 단계, 검토 대상은 plan 본문) / TodoWrite (단발 검토 산출물)
💡 Recommended: design.md 작성 후 `/pdca design BAR-58` 종합 → bkit:design-validator 로 5인 council 산출물 일관성 검증
─────────────────────────────────────────────────
