# BAR-57 — 뉴스/공시 수집 파이프라인 (RSS + DART, Phase 3 입력 게이트)

**Phase**: 3 (테마 인텔리전스) — **두 번째 BAR**
**선행**: BAR-56a (Postgres + pgvector 인프라, worktree 트랙) ✅ — 262 passed
**후속 블로킹**: BAR-58 (뉴스 임베딩), BAR-59 (테마 분류기), BAR-61 (일정 캘린더)

---

## 0. 분리 정책 — BAR-57a (worktree 정식 do) / BAR-57b (운영 정식)

worktree 환경 제약 (Redis daemon 부재 / DART 실 API 호출 불가 / 24h 운용 검증 불가) 을 고려하여 BAR-54a/54b · BAR-56a/56b 와 동일한 a/b 분리 패턴을 적용한다. 본 plan 의 5단 PDCA 는 **BAR-57a 트랙** 만 다룬다. BAR-57b 는 별도 plan 없이 **운영 환경 진입 시 BAR-57a 산출물에 대한 운영 검증 사이클**로 수행한다.

| BAR | 트랙 | 산출물 | 본 사이클 |
|-----|------|--------|:---:|
| **BAR-57a** | worktree (코드 + mock 단위 테스트 + Redis Streams 어댑터 인터페이스 + 의존성 주입) | NewsItem 모델, Source 추상, Collector orchestrator, in-memory dedup, Redis Streams publisher 어댑터 (인터페이스 + InMemory fallback), `news_repo`, 30+ 단위 테스트 (mock httpx + mock Redis) | ✅ 정식 do |
| **BAR-57b** | 운영 (실 daemon + 실 polling + 24h 운용) | docker compose 에 redis 서비스 / 실 DART API 키 / 실 RSS 피드 / 1분 polling 실 가동 / 24h 누락률 측정 / 중복 0건 검증 / 운영 metric (수집량·실패율·latency) | deferred — 운영 진입 시 |

**왜 분리하는가**
- worktree 에서 Redis daemon 기동·실 API 호출은 환경상 불가. mock 만으로도 코드/스키마/테스트 게이트 (회귀 ≥ 262 passed) 는 충족.
- BAR-57b 의 24h 운용 검증·누락률 측정은 운영 환경 도달 시점 (Phase 5 보안 시동 ~ Phase 6 운영 게이트 전후) 까지 자연 지연. BAR-57a 단계에서 폴링 코드/스케줄 시그니처/Redis publisher 인터페이스를 확정해 두면 BAR-57b 는 **설정 + 실 키 주입 + 모니터링 셋업** 만 남는다.
- 후속 BAR-58/59 가 요구하는 것은 "NewsItem stream 의 안정 발행" 뿐 — mock fallback 위에서 BAR-58 임베딩 인프라 설계까지는 막힘 없이 진행 가능.

---

## 1. 목적 (Why)

Phase 3 의 핵심 입력 — **국내 시장 정보를 분 단위 latency 로 NewsItem stream 에 적재**.

- BAR-58 (뉴스 임베딩 인프라): NewsItem.body 를 입력으로 1536-dim 임베딩 생성 → pgvector 적재.
- BAR-59 (테마 분류기): NewsItem.title + body → Korean tag set 분류.
- BAR-61 (일정 캘린더): DART 공시 (실적 / 배당 / 주총) 를 캘린더 이벤트로 변환.

따라서 본 BAR 의 출력 (`NewsItem` + Redis Streams `news_items`) 이 Phase 3 후속 BAR 3건의 **공통 입력 면** 이다. 본 BAR 가 후행 3건의 인터페이스를 결정한다.

**왜 RSS + DART 두 소스만 v1 인가**
- RSS: 한경/매경/연합 등 무료·공개·구조 단순. 인증/쿼터 없음 → worktree mock 화 용이.
- DART (Open API): 국내 공시 단일 진실 원천 (legal SoR). API 키만 있으면 polling 가능.
- Naver/Daum 검색 API · 종목토론방·SNS 는 v2 (BAR-66 권역) 로 분리 — 인증/쿼터/저작권 이슈가 본 BAR DoD 를 흐림.

---

## 2. 기능 요구사항 (FR)

### 2-1. 모델

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-01 | `NewsItem` (Pydantic v2, `frozen=True`) — `source: NewsSource`, `source_id: str`, `title: str`, `body: str`, `url: HttpUrl`, `published_at: datetime` (TZ-aware), `fetched_at: datetime` (TZ-aware), `tags: tuple[str, ...]` (정렬·중복 제거 후 immutable) | `backend/models/news.py` (신규) |
| FR-02 | `NewsSource` enum — `RSS_HANKYUNG`, `RSS_MAEKYUNG`, `RSS_YONHAP`, `DART` (확장 여지 — 미정의 source 는 추가 시 enum 만 늘림) | 동상 |

### 2-2. Source 추상

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-03 | `NewsSourceAdapter` (Protocol or ABC, async) — `async def fetch(self, since: datetime) -> list[NewsItem]` 단일 메서드. since 이후만 반환 (서버측 since 미지원이면 클라이언트측 필터). | `backend/core/news/sources/base.py` |
| FR-04 | `RSSSource` 구현 — feed URL · `feedparser` (or `httpx + lxml`) · 발행시각 파싱 · source_id 는 entry.id (없으면 entry.link 의 sha1 8자리). | `backend/core/news/sources/rss.py` |
| FR-05 | `DARTSource` 구현 — `https://opendart.fss.or.kr/api/list.json` polling, `bgn_de`/`end_de` 파라미터로 since 적용, `rcept_no` → source_id, `report_nm` → title, 본문은 별도 호출 없이 1차에선 title + corp_name 결합 (실 본문 fetch 는 BAR-57b 에서 옵션화). | `backend/core/news/sources/dart.py` |

### 2-3. Collector orchestrator

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-06 | `NewsCollector` — 등록된 `NewsSourceAdapter` 리스트를 1분 주기로 `apscheduler.AsyncIOScheduler` 위에서 병렬 fetch. | `backend/core/news/collector.py` |
| FR-07 | `httpx.AsyncClient` 단일 인스턴스 의존성 주입 (timeout=10s, http2=on). 어댑터별 client 생성 X — connection 재사용. | 동상 |
| FR-08 | source 별 fetch 실패 시 retry 1회 (지수 백오프 1s) → 실패 시 `news_collector_errors_total{source}` 카운터 증가 (worktree: in-process counter / BAR-57b: prometheus). | 동상 |
| FR-09 | 한 사이클에서 한 source 가 30s 초과 시 cancel + 다음 사이클로. (긴 fetch 가 다음 1분 사이클을 막지 않음.) | 동상 |

### 2-4. 중복 제거 (dedup)

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-10 | dedup 키: `(source, source_id)`. 동일 키 재방문 시 publish 생략 + `news_dedup_hits_total{source}` 증가. | `backend/core/news/dedup.py` |
| FR-11 | dedup 백엔드 추상 — `Deduplicator` 인터페이스 (`async def seen(key) -> bool`, `async def mark(key) -> None`). 구현 2개: `InMemoryDeduplicator` (TTL=24h LRU, worktree 기본) / `RedisDeduplicator` (Redis SET / SETEX TTL=72h, 운영). | 동상 |
| FR-12 | dedup TTL 만료 시 같은 source_id 가 재게재 가능. 재게재 시 NewsItem.fetched_at 만 갱신 (publish 한다). 1차 정책: TTL 충분히 길게 (24~72h) → 사실상 재발행 0. | 동상 |

### 2-5. Redis Streams publisher 어댑터

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-13 | `NewsPublisher` 인터페이스 — `async def publish(item: NewsItem) -> None`. | `backend/core/news/publisher.py` |
| FR-14 | `RedisStreamPublisher` 구현 — stream 이름 `news_items` (config 가능), `XADD` (MAXLEN ~10000 approx) 로 적재. payload 직렬화는 NewsItem.model_dump_json() 단일 문자열 필드 `payload`. | 동상 |
| FR-15 | `InMemoryStreamPublisher` (worktree fallback) — `asyncio.Queue` 위에 적재, 테스트에서 drain. BAR-57a 의 모든 단위 테스트 default. | 동상 |
| FR-16 | publisher 선택은 `NEWS_STREAM_BACKEND=redis|memory` env 로 결정. 미설정 시 `memory`. | `backend/config/settings.py` |

### 2-6. 저장소 (audit_repo 와 동일 패턴)

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-17 | `news_items` 테이블 — Alembic revision 0002. 컬럼: `id BIGSERIAL`, `source TEXT`, `source_id TEXT`, `title TEXT`, `body TEXT`, `url TEXT`, `published_at TIMESTAMPTZ`, `fetched_at TIMESTAMPTZ`, `tags JSONB`. UNIQUE(source, source_id). 인덱스: (source, published_at DESC), (fetched_at DESC). | `alembic/versions/0002_news_items.py` |
| FR-18 | `news_repo.py` — `async def insert(item: NewsItem) -> int`, `async def find_recent_by_source(source, limit=100) -> list[NewsItem]`, `async def find_since(since: datetime, limit=500) -> list[NewsItem]`. SQLAlchemy `text()` + named param (audit_repo 와 동일 dialect-무관 패턴 유지). UNIQUE 충돌 시 ON CONFLICT DO NOTHING (Postgres) / INSERT OR IGNORE (SQLite). | `backend/db/repositories/news_repo.py` (신규) |
| FR-19 | publisher 와 repo 의 책임 분리 — collector 는 1) repo.insert (idempotent) 2) publisher.publish 순서로 호출. repo 가 0 row 영향 (이미 존재) 면 publish 도 skip. | `backend/core/news/collector.py` |

---

## 3. 비기능 요구사항 (NFR)

| ID | 요구 | 측정 |
|----|------|------|
| NFR-01 | RSS 1건 fetch + parse 의 P95 latency ≤ 500ms (mock httpx 응답 기준, 200KB 페이로드) | `pytest --durations=20` + benchmark fixture |
| NFR-02 | 중복률 0% — 동일 (source, source_id) 가 두 번 publish 되지 않음 | 100건 stress 테스트, 5회 재실행, dedup_hits == 4×|items| |
| NFR-03 | 회귀 262 passed 유지 (BAR-56a 누적) — BAR-57a do 머지 후 **회귀 ≥ 292 passed (262 + 30 신규)** | `pytest backend/tests/` exit 0 |
| NFR-04 | 1분 사이클 timing 정확도 — apscheduler trigger 1분 ±5s 이내 | scheduler.next_run_time 단위 테스트 |
| NFR-05 | source 1건 fetch 실패가 다른 source 를 막지 않음 (격리) | 의도적 raise 의 mock fixture, 다른 source 의 publish 횟수 확인 |
| NFR-06 (BAR-57b) | 24h 운용 시 수집 누락률 ≤ 1% — RSS 서버측 published count vs DB count | 운영 환경, prometheus dashboard |
| NFR-07 (BAR-57b) | 동일 source_id 재게재 0건 — 24h 윈도우 | 동상 |

---

## 4. 비고려 (Out of Scope)

| 영역 | 이관 대상 | 사유 |
|------|----------|------|
| 임베딩 생성 | **BAR-58** (뉴스 임베딩 인프라) | NewsItem.body → vector 는 별 BAR. 본 BAR 는 stream 발행까지. |
| 테마 분류 (Korean tag set) | **BAR-59** (테마 분류기) | NewsItem.tags 는 본 BAR 에서 빈 tuple 가능. 분류기가 별 stream consumer. |
| 일정 캘린더 (실적/배당/주총) | **BAR-61** | DART 공시 → 캘린더 이벤트 변환은 BAR-61 책임. 본 BAR 는 raw NewsItem 까지. |
| 실 DART API 인증·페이로드 검증·운영 키 주입 | **BAR-57b** | 운영 환경 진입 시. worktree 는 mock httpx 응답으로 fixture 화. |
| 검색 API (Naver, Daum), SNS, 종목토론방 | **BAR-66** (소셜 시그널) | 인증/쿼터/저작권. v1 스코프 흐림. |
| HTML 본문 풀-파싱 (boilerplate 제거) | BAR-57b 옵션 / BAR-58 의 input pipeline | 1차에선 title + summary 문자열로 충분 (BAR-58 임베딩 품질 검증 후 결정) |
| 실 prometheus / grafana 연동 | BAR-72 (Phase 6 운영) | worktree: in-process counter 만. |
| RSS 정규화·중복 클러스터링 (같은 사건 다른 매체) | BAR-58 의 임베딩 유사도 군집화 | 본 BAR 의 dedup 은 (source, source_id) 단순 키만 |
| Pub/Sub consumer 구현 (BAR-58/59 측 소비자) | BAR-58/59 | 본 BAR 는 publisher 만 |

---

## 5. DoD

### 5-1. BAR-57a (worktree 정식 do — 본 사이클)

- [ ] `backend/models/news.py` — NewsItem (frozen) + NewsSource enum
- [ ] `backend/core/news/sources/base.py` — NewsSourceAdapter Protocol
- [ ] `backend/core/news/sources/rss.py` — RSSSource (httpx + feedparser 또는 lxml)
- [ ] `backend/core/news/sources/dart.py` — DARTSource (httpx + DART list.json shape 매칭)
- [ ] `backend/core/news/dedup.py` — Deduplicator + InMemoryDeduplicator + RedisDeduplicator (인터페이스 충실, 실 Redis 통합은 BAR-57b)
- [ ] `backend/core/news/publisher.py` — NewsPublisher + InMemoryStreamPublisher + RedisStreamPublisher (인터페이스 충실)
- [ ] `backend/core/news/collector.py` — NewsCollector + apscheduler glue
- [ ] `backend/db/repositories/news_repo.py` — insert / find_recent_by_source / find_since (text() + named param)
- [ ] `alembic/versions/0002_news_items.py` — news_items 테이블 + UNIQUE(source, source_id) + 2 인덱스. up/down 왕복 PASS
- [ ] `backend/config/settings.py` — `NEWS_STREAM_BACKEND`, `NEWS_DEDUP_BACKEND`, `DART_API_KEY` (optional), `RSS_FEEDS` (list[str]) 추가
- [ ] **30+ 단위 테스트 (mock httpx + mock Redis)** — `backend/tests/news/`
  - NewsItem 모델 frozen·tag 정렬·TZ-aware 검증 (5)
  - RSSSource: 정상 / 빈 피드 / 잘못된 시각 / since 필터 (5)
  - DARTSource: 정상 / API 에러 / since→bgn_de 매핑 / source_id=rcept_no (5)
  - Deduplicator: in-memory hit/miss / TTL 만료 / Redis mock hit/miss (5)
  - Publisher: in-memory drain / Redis mock XADD payload 검증 (4)
  - Collector: 1분 주기 / 한 source 실패 격리 / 30s timeout cancel / repo→publisher 순서 (6)
- [ ] `Makefile` `test-news` 타겟
- [ ] **회귀 ≥ 292 passed** (262 누적 + 30 신규)
- [ ] gap-detector 매치율 ≥ 90%

### 5-2. BAR-57b (운영 정식 — deferred, 별도 plan 없이 진행 시 본 항목으로 검증)

- [ ] `docker-compose.yml` 에 `redis:7-alpine` 서비스 + healthcheck
- [ ] 실 DART API 키 (`.env.production`) + RSS 피드 4개 (한경/매경/연합/이데일리)
- [ ] **24h 운용** — collector 가동 후 24h 누락률 ≤ 1% 검증 (서버측 RSS count vs DB count)
- [ ] 동일 (source, source_id) 재게재 0건 (24h 윈도우)
- [ ] prometheus metric: `news_collected_total`, `news_dedup_hits_total`, `news_collector_errors_total`, `news_collector_latency_seconds` (P50/P95)
- [ ] alert rule: 1분간 collected==0 (모든 source 무응답) → page

---

## 6. 알고리즘 의사코드

```python
# collector.py (요지)
class NewsCollector:
    def __init__(self, sources, repo, publisher, dedup, client):
        self.sources, self.repo = sources, repo
        self.publisher, self.dedup, self.client = publisher, dedup, client
        self.scheduler = AsyncIOScheduler()

    async def start(self):
        self.scheduler.add_job(self.tick, IntervalTrigger(minutes=1), max_instances=1)
        self.scheduler.start()

    async def tick(self):
        since = utcnow() - timedelta(minutes=2)  # 약간 여유
        results = await asyncio.gather(
            *[self._fetch_one(src, since) for src in self.sources],
            return_exceptions=True,  # 격리
        )
        for r in results:
            if isinstance(r, Exception):
                ERRORS.labels(source="unknown").inc()

    async def _fetch_one(self, src, since):
        try:
            items = await asyncio.wait_for(src.fetch(since), timeout=30)
        except asyncio.TimeoutError:
            ERRORS.labels(source=src.name).inc(); return
        except Exception:
            ERRORS.labels(source=src.name).inc(); raise

        for item in items:
            key = (item.source, item.source_id)
            if await self.dedup.seen(key):
                DEDUP_HITS.labels(source=src.name).inc(); continue
            inserted = await self.repo.insert(item)  # ON CONFLICT DO NOTHING → 0 or 1
            if inserted:
                await self.publisher.publish(item)
                await self.dedup.mark(key)
                COLLECTED.labels(source=src.name).inc()
```

---

## 7. 위험 / 완화

| 위험 | 트리거 | 완화 | 일정 영향 |
|------|--------|------|----------|
| RSS 피드 구조 변경 (entry.id 누락 등) | feed parser 가 source_id 추출 실패 | source_id fallback 체인: entry.id → entry.link sha1 → (entry.published || fetched_at) sha1. 단위 테스트 fixture 3종 | +0 일 |
| DART API 인증/쿼터 변동 | 운영 가동 시 401 / 429 | DARTSource 401/429 시 errors_total 증가 + 백오프 5분 + alert. BAR-57b 시 검증 | BAR-57b 만 영향 |
| Redis 단절 (운영) | Redis 컨테이너 다운 | RedisDeduplicator·RedisStreamPublisher 모두 fallback 체인 — Redis 실패 시 in-memory 로 자동 강등 + degraded 알람. 본 BAR 는 인터페이스만 두고 실 강등 로직은 BAR-57b | BAR-57b |
| apscheduler 1분 trigger drift | 컨테이너 재시작 사이 사이클 누락 | trigger=IntervalTrigger 의 `next_run_time` 보정 + 시작 시 `since=last_fetched_at - 5min` 으로 재방문 (dedup 이 흡수) | +0 일 |
| 메모리 dedup 용량 (24h × 4 source × 분당 10건 ≈ 60k 키) | 메모리 압박 | InMemory 는 LRU max=200k + TTL=24h. 실 운영은 Redis 권장 | +0 일 |
| 회귀 깨짐 (alembic 0002 rev 누락 / SQLite UNIQUE 차이) | alembic upgrade head 실패 | 0002 의 up/down 양방향 단위 테스트. UNIQUE 는 SQLite/Postgres 양립 syntax | +0.5 일 |
| **트리거: 1주 추가 일정** | 위 위험 중 2개 이상 동시 발생 | BAR-57a 만 1주 연장 → BAR-58 시작 1주 지연 보고 | +5 일 |

---

## 8. 다음 단계 — design 단계 council 위임

design 단계는 **5인 council** 패턴으로 위임한다 (Enterprise level, Phase 3 의 첫 데이터 파이프라인 BAR — 다각 시각 필요).

| 역할 | 시각 | 주요 산출물 |
|------|------|-----------|
| **enterprise-expert (architect)** | 전체 책임 분할 — sources / collector / dedup / publisher / repo 의 모듈 경계 | 모듈 다이어그램 + 의존성 주입 컨테이너 윤곽 |
| **infra-architect** | Redis Streams · apscheduler · httpx connection pool · alembic 0002 | docker-compose 추가분 + Redis stream 설정 + indexing 전략 |
| **bkend-expert** | Pydantic v2 frozen 모델 / async retry / `text()` named param SQL / ON CONFLICT 처리 | `backend/core/news/` 패키지 구조 + 테스트 fixture 명세 |
| **qa-strategist** | 30+ 테스트의 시나리오 매트릭스 (정상 / 빈 / 에러 / timeout / dedup hit / 격리) — mock httpx 응답 fixture 설계 | 시나리오 표 + fixture 명세 |
| **security-architect** | DART API 키 secrets 처리 (env / Secrets Manager) · RSS URL allowlist · payload 직렬화 시 PII 노출 방지 | 위협 모델 1쪽 + secret 흐름도 |

design 산출물: `docs/02-design/features/bar-57-news-collection.design.md`

PDCA 5단:
1. `/pdca design BAR-57` — council 5인 종합 → 모듈 경계 + DI + 테스트 매트릭스 확정
2. `/pdca do BAR-57` — 30+ 단위 테스트 + alembic 0002 + 회귀 ≥ 292 passed
3. `/pdca analyze BAR-57` — gap-detector
4. `/pdca iterate BAR-57` (필요 시)
5. `/pdca report BAR-57` — BAR-57a 완료 보고 (BAR-57b 항목 deferred 명시)

본 BAR-57a 완료 → **BAR-58 (뉴스 임베딩 인프라) 진입 가능**.

---

## 요약 (200단어)

BAR-57 은 Phase 3 의 두 번째 BAR 이자 BAR-58/59/61 가 공통으로 의존하는 **뉴스/공시 수집 파이프라인** 이다. RSS 4개 + DART 1개를 1분 주기 polling 으로 fetch 하여 `NewsItem` (Pydantic v2 frozen, source/source_id/title/body/url/published_at/fetched_at/tags) 으로 정규화한 뒤 (source, source_id) 키로 중복 제거하고 Postgres `news_items` 테이블에 적재 + Redis Streams `news_items` 에 발행한다. worktree 환경 제약 (Redis daemon 부재 / 실 DART 호출 불가 / 24h 운용 검증 불가) 을 고려해 BAR-54a/54b · BAR-56a/56b 와 동일한 a/b 분리 정책을 채택한다. **BAR-57a (worktree 정식 do) — 본 사이클**: 모델·소스 추상·collector·dedup·publisher 의 인터페이스 + InMemory fallback + 30+ mock 단위 테스트 + alembic 0002 + 회귀 ≥ 292 passed. **BAR-57b (운영 정식)**: 실 Redis 기동 + 실 polling + 24h 누락률 ≤ 1% + 동일 source_id 재게재 0건 검증 + prometheus metric, deferred. 비고려: 임베딩 (BAR-58) / 테마 분류 (BAR-59) / 캘린더 (BAR-61) / SNS·검색 API (BAR-66). 위험은 RSS 구조 변경 / DART 인증 변동 / Redis 단절 — 모두 인터페이스 격리 + fallback 체인 + 격리 회복 전략으로 흡수. 다음은 enterprise-expert + infra-architect + bkend-expert + qa-strategist + security-architect 5인 council 로 design 단계 위임.
