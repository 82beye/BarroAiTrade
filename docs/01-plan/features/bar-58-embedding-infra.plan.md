# BAR-58 — 뉴스 임베딩 인프라 (kiwipiepy + ko-sbert + pgvector, Phase 3 변환 게이트)

**Phase**: 3 (테마 인텔리전스) — **세 번째 BAR**
**선행**: BAR-56a (Postgres + pgvector 인프라) ✅ — pgvector extension 활성화 / 287 passed
        BAR-57a (뉴스/공시 수집 + Redis Streams `news_items`) ✅ — 299 passed
**후속 블로킹**: BAR-59 (테마 분류기, 임베딩 코사인 검색 사용), BAR-60 (검색·랭킹 API)

---

## 0. 분리 정책 — BAR-58a (worktree 정식 do) / BAR-58b (운영 정식)

worktree 환경 제약 (ko-sbert 모델 ~700MB 다운로드 시간 / 실 sentence-transformers 추론 메모리 / Redis daemon 부재) 을 고려하여 BAR-54a/54b · BAR-56a/56b · BAR-57a/57b 와 동일한 a/b 분리 패턴을 적용한다. 본 plan 의 5단 PDCA 는 **BAR-58a 트랙** 만 다룬다. BAR-58b 는 별도 plan 없이 **운영 환경 진입 시 BAR-58a 산출물에 대한 운영 검증 사이클**로 수행한다.

| BAR | 트랙 | 산출물 | 본 사이클 |
|-----|------|--------|:---:|
| **BAR-58a** | worktree (Embedder Protocol + 3 어댑터 코드 + alembic 0003 + repo + worker + mock 단위 테스트) | `Embedder` Protocol, `LocalKoSbertEmbedder` (코드만, 다운로드 X), `FakeDeterministicEmbedder` (768-dim hash 기반), `EmbeddingRepository` (text() + named param + dialect 분기), `EmbeddingWorker` (`news_items` consumer group), Settings 4 신규, alembic 0003 (embeddings 테이블 + ivfflat 인덱스), 25+ mock 단위 테스트 | ✅ 정식 do |
| **BAR-58b** | 운영 (실 ko-sbert 다운로드 + 실 Redis daemon + 실 P95 측정 + 백업 어댑터 + ivfflat REINDEX) | HuggingFace/S3 모델 페칭 절차, 실 Redis XREADGROUP 가동, 100건 P95 ≤ 500ms 측정, claude-haiku zero-shot 백업 어댑터, ivfflat 인덱스 build/REINDEX 운영 절차, prometheus metric, embedder warmup | deferred — 운영 진입 시 |

**왜 분리하는가**
- ko-sbert (`jhgan/ko-sroberta-multitask` 또는 `BM-K/KoSimCSE-roberta-multitask`) 모델 다운로드는 ~700MB. worktree 환경에서 다운로드 시간 + 디스크 부담 + CI 회귀 시간 모두 부담.
- 실 Redis daemon 위 XREADGROUP 검증은 BAR-57b 와 동일 운영 사이클에서 통합. BAR-58a 는 **`fakeredis` 또는 in-process queue mock** 으로 consumer 로직만 검증.
- claude-haiku zero-shot 백업 어댑터는 비용 절감용 2차 — 1차 (ko-sbert) 대비 정확도 / latency / 비용 trade-off 가 BAR-59 분류기 결과를 봐야 결정 가능. BAR-58a 에서 인터페이스만 두고 본 어댑터는 BAR-58b.
- 후속 BAR-59 가 요구하는 것은 "임베딩 768-dim 벡터 + 코사인 검색" 뿐. `FakeDeterministicEmbedder` 만으로도 BAR-59 의 분류기 알고리즘 / 임계치 튜닝 / 단위 테스트는 모두 진행 가능. 실 모델은 BAR-58b 머지 후 BAR-59 의 통합 검증 시점에 합류.

---

## 1. 목적 (Why)

Phase 3 의 핵심 변환 — **NewsItem.body 를 768-dim 벡터로 변환하여 pgvector 에 적재 + 코사인 유사도 검색 가능 상태로 둔다**.

- BAR-59 (테마 분류기): NewsItem 임베딩 ↔ 사전 정의 테마 임베딩 코사인 유사도 → top-k 테마 분류.
- BAR-60 (검색·랭킹 API): "유사 뉴스" / "테마 군집" 검색은 본 BAR 의 ivfflat 인덱스 위에서 수행.
- BAR-61 (일정 캘린더): 공시 임베딩으로 유사 과거 공시 검색 (실적 시즌 패턴 매칭).

따라서 본 BAR 의 출력 (`embeddings` 테이블 + 768-dim 벡터 + ivfflat 인덱스) 이 Phase 3 후속 BAR 3건의 **공통 검색 면** 이다. 본 BAR 가 후행 3건의 벡터 차원 / 모델 식별자 / 검색 인터페이스를 결정한다.

**왜 ko-sbert 1차 / claude-haiku 2차 인가**
- ko-sbert: 한국어 sentence transformer 의 사실상 표준. 768-dim, 무료, 로컬 추론. RSS/공시 한국어 짧은 문장에 적합.
- claude-haiku zero-shot: 별도 임베딩 모델 다운로드 불필요. 비용은 토큰당 발생하나 본문이 짧아 절감 가능. 단, 외부 API latency 변동성 / 결정성 부족 → 백업 위치.
- 본 BAR 는 **ko-sbert 를 "코드상 1차"** 로 두고 (실 다운로드는 BAR-58b), `Embedder` Protocol 뒤에서 backend 교체로 claude-haiku / openai 도 합류 가능하도록 추상화한다.

---

## 2. 기능 요구사항 (FR)

### 2-1. Embedder Protocol

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-01 | `Embedder` (Protocol, async) — `async def encode(self, texts: list[str]) -> list[np.ndarray]`. 반환 ndarray dtype=float32, shape=(dim,). 빈 입력 → 빈 리스트. | `backend/core/embeddings/base.py` |
| FR-02 | `Embedder.dim: int` (property) — 차원 expose. settings 의 `NEWS_EMBEDDING_DIM` 과 일치 검증 (worker 기동 시 assert). | 동상 |
| FR-03 | `Embedder.model_id: str` (property) — DB `embeddings.model` 컬럼에 기록되는 식별자 (예: `"fake-deterministic-v1"`, `"jhgan/ko-sroberta-multitask"`, `"claude-haiku-zero-shot-v1"`). | 동상 |

### 2-2. 구현체 3종

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-04 | `FakeDeterministicEmbedder` — 입력 텍스트의 sha256 → 768-dim float32 벡터 (uniform [-1,1] / L2 normalize). 동일 입력 → 동일 출력 (테스트 결정성). dim=768 고정. model_id=`"fake-deterministic-v1"`. seed 파라미터 (기본 0). | `backend/core/embeddings/fake.py` |
| FR-05 | `LocalKoSbertEmbedder` — `sentence_transformers.SentenceTransformer(model_name)` 을 **lazy import + lazy load** (encode 첫 호출 시점). model_name 기본 `"jhgan/ko-sroberta-multitask"` (settings override 가능). batch encode (`encode(texts, batch_size=32, convert_to_numpy=True, normalize_embeddings=True)`). dim=768. model_id=model_name. | `backend/core/embeddings/ko_sbert.py` |
| FR-06 | `LocalKoSbertEmbedder` 의 실 모델 다운로드는 **본 BAR 의 do 단계에서 수행하지 않는다**. 단위 테스트는 `sentence_transformers` 를 mock 하여 (`unittest.mock.patch`) 호출 시그니처·차원·정규화만 검증. 실 추론은 BAR-58b. | 동상 |
| FR-07 | `EmbedderFactory.from_settings(settings) -> Embedder` — `NEWS_EMBEDDING_BACKEND` (`fake|ko_sbert|openai`) 분기. `openai` 는 BAR-58b 에서 합류 (현재 stub raise NotImplementedError). | `backend/core/embeddings/factory.py` |

### 2-3. EmbeddingWorker (Redis Streams consumer)

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-08 | `EmbeddingWorker` — Redis Streams `news_items` (BAR-57a 와 동일 stream 이름) 의 consumer group `embedder_v1` 으로 `XREADGROUP` 폴링. consumer name = `f"embedder-{hostname}-{pid}"`. block=5000ms, count=BATCH_SIZE. | `backend/core/embeddings/worker.py` |
| FR-09 | 첫 기동 시 consumer group 미존재면 `XGROUP CREATE news_items embedder_v1 $ MKSTREAM` (idempotent). 이미 존재 (BUSYGROUP) 는 무시. | 동상 |
| FR-10 | message payload 는 BAR-57a 의 `NewsItem.model_dump_json()` (단일 필드 `payload`). worker 는 parse → `news_id` 추출 → body 임베딩 → repo.insert → `XACK news_items embedder_v1 <id>`. parse 실패 / news_id null → ACK + error counter (poison pill 흡수). | 동상 |
| FR-11 | batch processing — `XREADGROUP COUNT BATCH_SIZE` 로 한 번에 N건 수령 → `embedder.encode([body1, body2, ...])` 단일 호출 → 각각 repo.insert + XACK. batch 단위 트랜잭션 X (개별 commit, 한 건 실패가 batch 전체 ACK 막지 않음). | 동상 |
| FR-12 | worker 에 `BACKEND=memory` 옵션 — `news_items` Redis 대신 `asyncio.Queue` 의 in-memory adapter 로 BAR-58a 단위 테스트가 외부 Redis 없이 worker loop 검증 가능. (BAR-57a 의 `InMemoryStreamPublisher` 와 짝을 이룸.) | `backend/core/embeddings/stream_adapter.py` |
| FR-13 | graceful shutdown — SIGTERM 수신 시 진행 중 batch 의 ACK 까지 처리 후 종료. asyncio.Event 로 stop signal 전파. | `backend/core/embeddings/worker.py` |

### 2-4. EmbeddingRepository (audit_repo / news_repo 와 동일 패턴)

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-14 | `embeddings` 테이블 — Alembic revision 0003. 컬럼: `id BIGSERIAL`, `news_id BIGINT NOT NULL` (FK → news_items.id ON DELETE CASCADE), `model TEXT NOT NULL`, `vector vector(768)` (Postgres) / `vector TEXT` (SQLite fallback, JSON 직렬화), `created_at TIMESTAMPTZ DEFAULT NOW()`. UNIQUE(news_id, model). 인덱스: `ivfflat (vector vector_cosine_ops) WITH (lists=100)` (Postgres only, SQLite skip). | `alembic/versions/0003_embeddings.py` |
| FR-15 | `EmbeddingRepository` — `async def insert(news_id: int, model: str, vector: np.ndarray) -> int`, `async def find_by_news_id(news_id: int, model: str) -> np.ndarray | None`, `async def search_similar(query: np.ndarray, model: str, k: int = 10) -> list[tuple[int, float]]` (news_id + cosine distance). | `backend/db/repositories/embedding_repo.py` (신규) |
| FR-16 | dialect 분기 — Postgres: `vector` 자료형으로 직접 bind (psycopg / asyncpg + pgvector adapter). SQLite: `np.ndarray.tolist()` → `json.dumps()` → TEXT 컬럼. 검색은 Postgres `<=>` (cosine distance) operator / SQLite 는 코사인 직접 계산 (테스트용, 운영은 Postgres). | 동상 |
| FR-17 | UNIQUE(news_id, model) 충돌 시 `ON CONFLICT (news_id, model) DO NOTHING` (Postgres) / `INSERT OR IGNORE` (SQLite) — idempotent insert. | 동상 |

### 2-5. Settings 신규 4종

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-18 | `NEWS_EMBEDDING_BACKEND: Literal["fake", "ko_sbert", "openai"] = "fake"` (worktree 기본 fake / 운영 ko_sbert) | `backend/config/settings.py` |
| FR-19 | `NEWS_EMBEDDING_MODEL: str = "jhgan/ko-sroberta-multitask"` (ko_sbert backend 일 때만 사용) | 동상 |
| FR-20 | `NEWS_EMBEDDING_DIM: int = 768` (모든 backend 공통, worker 기동 시 embedder.dim 과 assert) | 동상 |
| FR-21 | `NEWS_EMBEDDING_BATCH_SIZE: int = 16` (XREADGROUP COUNT + encoder batch_size 동시 적용) | 동상 |

### 2-6. 의존성

| ID | 요구 | 산출물 |
|----|------|--------|
| FR-22 | `pyproject.toml` 또는 `requirements.txt` 에 `sentence-transformers>=2.7` (lazy import, install 시 torch 동반 설치). 본 BAR 의 worktree do 에서는 dependency 선언만 추가, 실 import 는 단위 테스트에서 mock. | `pyproject.toml` |
| FR-23 | `kiwipiepy>=0.17` 선택 의존 — 형태소 토크나이저 (BAR-59 또는 BAR-58c 에서 통합). 본 BAR 에서는 dependency 선언만, 실 사용 X. | 동상 |
| FR-24 | `pgvector` 파이썬 패키지 (이미 BAR-56a 에서 도입됨, 재확인) — Postgres dialect 의 `vector` 자료형 bind 지원. | 동상 |

---

## 3. 비기능 요구사항 (NFR)

| ID | 요구 | 측정 |
|----|------|------|
| NFR-01 | `FakeDeterministicEmbedder` 결정성 — 동일 입력 → 동일 출력 (bit-exact). seed 파라미터 변경 시만 출력 변경. | 단위 테스트 5회 반복, np.array_equal |
| NFR-02 | `Embedder.encode([])` → `[]` 빈 입력 안전 처리 (어댑터 3종 공통) | 단위 테스트 |
| NFR-03 | 모든 어댑터의 출력 ndarray 는 dtype=float32, L2-normalized (norm ≈ 1.0 ± 1e-5) | 단위 테스트, np.linalg.norm |
| NFR-04 | EmbeddingWorker 의 batch 처리 — 16건 batch 한 번 encode → 16번 repo.insert + 16번 XACK. encode 호출 횟수 = 1 (batch) | 단위 테스트, mock spy |
| NFR-05 | 회귀 299 passed 유지 (BAR-57a 누적) — BAR-58a do 머지 후 **회귀 ≥ 324 passed (299 + 25 신규)** | `pytest backend/tests/` exit 0 |
| NFR-06 | coverage ≥ 70% (`backend/core/embeddings/` + `backend/db/repositories/embedding_repo.py`) | `pytest --cov=backend/core/embeddings --cov=backend/db/repositories/embedding_repo` |
| NFR-07 | alembic 0003 의 up/down 양방향 PASS (Postgres / SQLite 양쪽) | 단위 테스트 |
| NFR-08 (BAR-58b) | 100건 입력 시 P95 latency ≤ 500ms (입력→repo.insert 까지, batch_size=16, 실 ko-sbert) | 운영 환경, prometheus dashboard |
| NFR-09 (BAR-58b) | ivfflat 인덱스 build 후 1만건 적재 시 검색 P95 ≤ 50ms (k=10) | 운영 환경 측정 |

---

## 4. 비고려 (Out of Scope)

| 영역 | 이관 대상 | 사유 |
|------|----------|------|
| 실 ko-sbert 모델 다운로드 (HuggingFace fetch) | **BAR-58b** | 700MB, worktree 디스크/시간 부담. lazy import + mock 으로 코드만 검증. |
| claude-haiku zero-shot 백업 어댑터 (실 API 호출) | **BAR-58b** | 외부 API 비용 + 결정성 검증 필요. ko-sbert 실측치를 본 후 결정. |
| 100건 P95 ≤ 500ms 측정 | **BAR-58b** | 실 모델 + 실 Redis 위에서만 의미. mock 위에선 유의미한 수치 X. |
| kiwipiepy 형태소 토크나이저 통합 (전처리 파이프) | **BAR-58c 또는 BAR-59** | 임베딩 모델 (ko-sbert) 이 자체 토크나이저 보유. 형태소 분리는 분류기 (BAR-59) 의 키워드 추출 단계가 더 자연스러움. |
| 테마 분류기 (코사인 유사도 → 사전 정의 테마 매핑) | **BAR-59** | 본 BAR 는 임베딩 적재 + 검색 인터페이스까지. 테마 라벨은 BAR-59. |
| 검색·랭킹 REST API | **BAR-60** | 본 BAR 는 repo 메서드 (`search_similar`) 까지. HTTP endpoint 는 BAR-60. |
| ivfflat 인덱스 build / REINDEX 운영 절차 | **BAR-58b** | 적재량이 일정 수준 (1k+) 넘어야 ivfflat 의 lists 튜닝 의미. worktree 는 인덱스 정의만. |
| OpenAI text-embedding-3-large 등 외부 API 어댑터 (실 호출) | **BAR-58b** | 비용 + 키 관리 운영 사이클. 본 BAR 는 factory 분기 stub 만. |
| 실 prometheus metric 노출 | BAR-72 (Phase 6 운영) | worktree: in-process counter 만. |
| 임베딩 캐시 (입력 sha256 → vector LRU) | BAR-58c (성능 최적화 후속 BAR) | 1차 정책: insert 단계의 UNIQUE(news_id, model) 가 사실상 idempotent. 본격 캐시는 동일 본문 재발행 빈도 측정 후. |
| 다중 모델 ensemble | 추후 BAR | 1 모델 (ko-sbert 또는 fake) 로 단순화. |

---

## 5. DoD

### 5-1. BAR-58a (worktree 정식 do — 본 사이클)

- [ ] `backend/core/embeddings/__init__.py`
- [ ] `backend/core/embeddings/base.py` — `Embedder` Protocol + property 명세
- [ ] `backend/core/embeddings/fake.py` — `FakeDeterministicEmbedder` (sha256 → 768-dim float32 / L2 norm)
- [ ] `backend/core/embeddings/ko_sbert.py` — `LocalKoSbertEmbedder` (lazy import + lazy load, encode 시그니처)
- [ ] `backend/core/embeddings/factory.py` — `EmbedderFactory.from_settings()` (3-way 분기, openai 는 NotImplementedError)
- [ ] `backend/core/embeddings/stream_adapter.py` — `RedisStreamConsumerAdapter` + `InMemoryStreamConsumerAdapter` (BAR-57a publisher 와 짝)
- [ ] `backend/core/embeddings/worker.py` — `EmbeddingWorker` (XREADGROUP loop / consumer group create / batch encode / ACK / graceful shutdown)
- [ ] `backend/db/repositories/embedding_repo.py` — `EmbeddingRepository` (insert / find_by_news_id / search_similar, dialect 분기)
- [ ] `alembic/versions/0003_embeddings.py` — embeddings 테이블 + UNIQUE(news_id, model) + ivfflat 인덱스 (Postgres only) + FK CASCADE. up/down 왕복 PASS
- [ ] `backend/config/settings.py` — 4 신규 설정 (`NEWS_EMBEDDING_BACKEND`, `NEWS_EMBEDDING_MODEL`, `NEWS_EMBEDDING_DIM`, `NEWS_EMBEDDING_BATCH_SIZE`)
- [ ] `pyproject.toml` — `sentence-transformers`, `kiwipiepy` 의존성 추가 (lazy / 선택)
- [ ] **25+ 단위 테스트 (mock sentence-transformers + fakeredis 또는 in-memory queue)** — `backend/tests/embeddings/`
  - Embedder Protocol contract — dim/model_id property + encode([]) 빈 입력 (3개 어댑터 × 3 = 9)
  - FakeDeterministicEmbedder — 결정성 (동일 입력 → 동일 출력) / seed 변경 / L2 norm / dtype float32 (4)
  - LocalKoSbertEmbedder — mock SentenceTransformer.encode 시그니처 / dim 검증 / lazy import 검증 (3)
  - EmbedderFactory — 3-way 분기 (fake / ko_sbert / openai NotImplementedError) (3)
  - EmbeddingRepository — insert / find_by_news_id round-trip / UNIQUE 충돌 / search_similar 모킹 (4)
  - alembic 0003 up/down (1)
  - EmbeddingWorker — batch 처리 / poison pill ACK / consumer group create idempotent / shutdown signal (4)
- [ ] `Makefile` `test-embeddings` 타겟
- [ ] **회귀 ≥ 324 passed** (299 누적 + 25 신규)
- [ ] **coverage ≥ 70%** (`backend/core/embeddings/` + `embedding_repo.py`)
- [ ] gap-detector 매치율 ≥ 90%

### 5-2. BAR-58b (운영 정식 — deferred)

- [ ] 실 ko-sbert (`jhgan/ko-sroberta-multitask`) HuggingFace 다운로드 + 캐시 디렉토리 (S3 mirror 검토)
- [ ] docker-compose embedder 워커 컨테이너 + 모델 볼륨 마운트
- [ ] 실 Redis daemon 위 XREADGROUP 24h 가동 — consumer lag ≤ 5s
- [ ] 100건 입력 P95 latency ≤ 500ms (batch_size=16) 측정
- [ ] claude-haiku zero-shot 백업 어댑터 (`HaikuZeroShotEmbedder`) — 실 Anthropic API 호출 + 비용/latency 측정
- [ ] ivfflat 인덱스 build (1만건 적재 후) + REINDEX 운영 절차
- [ ] prometheus metric: `news_embeddings_total`, `news_embedding_latency_seconds` (P50/P95), `news_embedding_consumer_lag`
- [ ] alert: 1분간 embedded==0 (워커 무응답) → page

---

## 6. 알고리즘 의사코드

```python
# worker.py (요지)
class EmbeddingWorker:
    def __init__(self, embedder, repo, stream, settings, stop_event=None):
        self.embedder = embedder
        self.repo = repo
        self.stream = stream  # RedisStreamConsumerAdapter or InMemory
        self.batch_size = settings.NEWS_EMBEDDING_BATCH_SIZE
        self.stop = stop_event or asyncio.Event()
        assert embedder.dim == settings.NEWS_EMBEDDING_DIM, "dim mismatch"

    async def start(self):
        await self.stream.ensure_group("news_items", "embedder_v1")  # idempotent
        while not self.stop.is_set():
            batch = await self.stream.read_group(
                group="embedder_v1",
                consumer=self.consumer_name,
                count=self.batch_size,
                block_ms=5000,
            )
            if not batch:
                continue
            await self._process(batch)

    async def _process(self, batch):
        # batch = list[(stream_id, payload_json)]
        items = []
        for stream_id, payload in batch:
            try:
                news_item = NewsItem.model_validate_json(payload["payload"])
                items.append((stream_id, news_item))
            except Exception:
                ERR.inc(); await self.stream.ack(stream_id)  # poison pill
        if not items:
            return
        bodies = [it.body for _, it in items]
        vectors = await self.embedder.encode(bodies)  # batch encode (1 call)
        for (stream_id, news_item), vec in zip(items, vectors):
            try:
                await self.repo.insert(news_item.id, self.embedder.model_id, vec)
                EMB.inc()
            except Exception:
                ERR.inc()
            finally:
                await self.stream.ack(stream_id)


# fake.py (요지)
class FakeDeterministicEmbedder:
    dim = 768
    model_id = "fake-deterministic-v1"

    def __init__(self, seed: int = 0):
        self.seed = seed

    async def encode(self, texts: list[str]) -> list[np.ndarray]:
        out = []
        for t in texts:
            h = hashlib.sha256(f"{self.seed}:{t}".encode()).digest()  # 32 bytes
            # 32 bytes → 768 floats: extend to 96 bytes via PRF, then unpack
            extended = h + hashlib.sha256(h).digest() + hashlib.sha256(h+h).digest()
            arr = np.frombuffer(extended, dtype=np.uint8).astype(np.float32)
            arr = (arr / 127.5 - 1.0).repeat(8)[:768]  # uniform [-1, 1]
            arr /= np.linalg.norm(arr) + 1e-8  # L2 norm
            out.append(arr)
        return out
```

---

## 7. 위험 / 완화

| 위험 | 트리거 | 완화 | 일정 영향 |
|------|--------|------|----------|
| 모델 메모리 (ko-sbert ~500MB resident) | 운영 워커 컨테이너 OOM | k8s requests=1Gi/limits=2Gi + warmup 시 1회 dummy encode. BAR-58b 검증 항목. | BAR-58b 만 영향 |
| 차원 mismatch (settings DIM ≠ embedder.dim) | 신 모델 교체 후 settings 업데이트 누락 | worker 기동 시 assert + 단위 테스트 fixture. 실 운영은 Helm chart 의 ENV + ConfigMap 동기화. | +0 일 |
| consumer lag 누적 | 입력 폭증 / encoder 처리 지연 | batch_size 동적 튜닝 (16→32→64) + horizontal pod autoscaling. BAR-58b 검증. | BAR-58b 만 영향 |
| ivfflat 인덱스 부재 시 검색 seq scan | 1만건 적재 후 search_similar 가 느려짐 | alembic 0003 에 인덱스 정의 (lists=100). 운영 적재량 도달 시 REINDEX 절차 (BAR-58b). | +0 일 |
| FakeEmbedder vs 실 ko-sbert 의 의미적 차이 | BAR-59 가 fake 위에서 통과해도 실 모델에선 분류 정확도 저하 | BAR-59 do 단계는 fake / BAR-59 의 통합 검증은 BAR-58b 머지 후 ko-sbert 위에서 재실행. matchRate 별도 측정. | BAR-59 일정 +1 일 |
| sentence-transformers torch 의존성 install 실패 | CI / worktree pip install torch 시간 (~5분) | lazy import + 선택 의존 (`extras_require={"embeddings-real": [...]}`) — 본 BAR 의 회귀는 fake 만으로 통과 가능. 실 install 은 BAR-58b. | +0 일 |
| pgvector 자료형 bind 실패 (asyncpg dialect) | Postgres insert 시 type cast 에러 | repo dialect 분기 + 단위 테스트는 SQLite (TEXT) + Postgres (vector) 양쪽 fixture. BAR-56a 에서 이미 pgvector adapter 검증됨. | +0 일 |
| Redis consumer group 재기동 시 pending entries (PEL) 누락 | 워커 크래시 후 재기동 | 본 BAR 는 graceful shutdown 으로 ACK 보장 + claim 미구현 (BAR-58b 의 운영 확장 항목). 회복 가능 손실 ≤ batch_size 건. | BAR-58b 만 영향 |
| **트리거: 1주 추가 일정** | 위 위험 중 2개 이상 동시 발생 | BAR-58a 만 1주 연장 → BAR-59 시작 1주 지연 보고 | +5 일 |

---

## 8. 다음 단계 — design 단계 council 위임

design 단계는 **5인 council** 패턴으로 위임한다 (Enterprise level, Phase 3 의 두 번째 데이터 파이프라인 BAR — pgvector 자료형 bind / Redis consumer 패턴 / 모델 추상 / 운영 게이트 분리 등 다각 시각 필요).

| 역할 | 시각 | 주요 산출물 |
|------|------|-----------|
| **enterprise-expert (architect)** | 전체 책임 분할 — Embedder Protocol / Worker / Repo / Factory 의 모듈 경계 + DI 컨테이너 | 모듈 다이어그램 + 의존성 흐름 |
| **infra-architect** | Redis Streams consumer group · pgvector ivfflat 인덱스 lists 튜닝 · alembic 0003 · k8s 워커 리소스 (CPU/MEM) · 모델 볼륨 | 인프라 토폴로지 + 인덱스 튜닝 표 + 운영 환경 추가분 |
| **bkend-expert** | Embedder Protocol 시그니처 / Factory 분기 / Repo dialect 처리 / Worker batch loop / graceful shutdown | `backend/core/embeddings/` 패키지 구조 + 테스트 fixture 명세 |
| **qa-strategist** | 25+ 테스트 시나리오 매트릭스 (결정성 / 빈 입력 / dim mismatch / poison pill / shutdown / batch 한 번 encode / UNIQUE 충돌 / dialect 분기) — mock SentenceTransformer fixture 설계 | 시나리오 표 + fixture 명세 + coverage gate (70%) |
| **security-architect** | HuggingFace 모델 출처 검증 (모델 hash pinning) · 외부 API 어댑터 (claude-haiku / openai) 의 키 처리 · embedding 의 PII 노출 가능성 (역산 공격) | 위협 모델 1쪽 + 모델 공급망 (supply chain) 검증 절차 |

design 산출물: `docs/02-design/features/bar-58-embedding-infra.design.md`

PDCA 5단:
1. `/pdca design BAR-58` — council 5인 종합 → Embedder Protocol + Worker + Repo + alembic 0003 + 시나리오 매트릭스 확정
2. `/pdca do BAR-58` — 25+ 단위 테스트 + alembic 0003 + 회귀 ≥ 324 passed + coverage ≥ 70%
3. `/pdca analyze BAR-58` — gap-detector
4. `/pdca iterate BAR-58` (필요 시)
5. `/pdca report BAR-58` — BAR-58a 완료 보고 (BAR-58b 항목 deferred 명시)

본 BAR-58a 완료 → **BAR-59 (테마 분류기) 진입 가능** (FakeDeterministicEmbedder 위에서 분류기 알고리즘 검증, 실 ko-sbert 합류는 BAR-58b 머지 후).

---

## 요약 (200단어)

BAR-58 은 Phase 3 의 세 번째 BAR 이자 BAR-59/60/61 가 공통으로 의존하는 **뉴스 임베딩 인프라** 다. NewsItem.body 를 768-dim 벡터로 변환하여 pgvector `embeddings` 테이블에 적재하고 ivfflat 인덱스 위에서 코사인 유사도 검색을 가능케 한다. 1차 모델은 ko-sbert (`jhgan/ko-sroberta-multitask`), 2차 백업은 claude-haiku zero-shot. worktree 환경 제약 (모델 ~700MB 다운로드 / 실 Redis daemon 부재 / 실 P95 측정 불가) 을 고려해 BAR-54a/54b · BAR-56a/56b · BAR-57a/57b 와 동일한 a/b 분리 정책을 채택한다. **BAR-58a (worktree 정식 do) — 본 사이클**: `Embedder` Protocol + `FakeDeterministicEmbedder` (sha256 기반 결정성, 768-dim, L2 norm) + `LocalKoSbertEmbedder` (코드만, 실 다운로드 X, sentence-transformers mock) + `EmbedderFactory` 3-way 분기 + `EmbeddingWorker` (XREADGROUP consumer group `embedder_v1` + batch encode + ACK + graceful shutdown) + `EmbeddingRepository` (text() + named param + Postgres vector / SQLite TEXT 분기 + UNIQUE(news_id, model) + search_similar) + alembic 0003 (embeddings 테이블 + ivfflat 인덱스) + Settings 4 신규 + 25+ mock 단위 테스트 + 회귀 ≥ 324 passed + coverage ≥ 70%. **BAR-58b (운영 정식)**: 실 ko-sbert 다운로드 + 실 Redis 가동 + 100건 P95 ≤ 500ms 측정 + claude-haiku 백업 어댑터 + ivfflat REINDEX 운영 절차 + prometheus metric, deferred. 비고려: claude-haiku 실 호출 (58b) / kiwipiepy 토크나이저 통합 (58c 또는 BAR-59) / 검색 REST API (BAR-60) / 테마 라벨 (BAR-59) / 임베딩 캐시 (BAR-58c). 위험은 모델 메모리 / 차원 mismatch / consumer lag / FakeEmbedder ↔ 실 모델 의미 차이 — 모두 인터페이스 격리 + assert + a/b 분리 + BAR-59 통합 재검증으로 흡수. 다음은 enterprise-expert + infra-architect + bkend-expert + qa-strategist + security-architect 5인 council 로 design 단계 위임.
