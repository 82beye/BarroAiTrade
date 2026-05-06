# BAR-58 — 임베딩 인프라 Design

**Plan**: `docs/01-plan/features/bar-58-embedding-infra.plan.md` (PR #93 머지)
**Phase**: 3 — 세 번째 BAR / Phase 3 변환 게이트
**Status**: Draft (council: architect + developer + qa + reviewer + security)
**Date**: 2026-05-07

> **요약**: BAR-57a `news_items` Redis Streams → 768-dim 벡터 → pgvector 적재. 5인 council 권고 모두 흡수. BAR-58a (worktree mock) / BAR-58b (운영 실모델) 분리.

---

## §0. 분리 정책

| BAR | 트랙 | 산출물 |
|-----|------|--------|
| **BAR-58a** | worktree | Embedder Protocol + 3 구현체 + Worker + Repo + alembic 0003 + 27+ tests |
| **BAR-58b** | 운영 | 실 ko-sbert (HF revision pin) + 24h + 100건 P95 ≤ 500ms + claude-haiku 백업 |

### 0.1 5 council 합의

| 역할 | 결정 |
|------|------|
| architect | NewsItem.id 추가 + Publisher payload db_id / XGROUP 첫 기동 옵션 / shutdown 단축 / batch 부분 실패 분리 |
| developer | `asyncio.to_thread(model.encode)` / `pgvector.asyncpg.register_vector` per-connection / search_similar = **cosine distance** |
| qa | dim mismatch ValueError / SQLite TEXT round-trip / stream_adapter 분리 |
| reviewer | consumer_name `embedder-{host}-{pid}` / `assert` → `raise ValueError` / BATCH_SIZE=16 |
| security | model revision pin / API 키 SecretStr / log payload 제외 / body 트렁케이션 8192 + BATCH ≤ 64 |

### 0.2 BAR-57a 보강

`NewsItem.id: Optional[int] = None` 추가 + `NewsRepository.insert` → `Optional[int]` 반환 (BIGSERIAL id) + `NewsCollector._handle_item` 에서 `model_copy(update={"id": new_id})` 후 publisher 에 전달.

---

## §1. 데이터 모델 (`backend/models/embedding.py`)

```python
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field

MAX_EMBED_CHARS: int = 8192       # security CWE-1284 DoS 방지

class EmbeddingJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    news_db_id: int = Field(gt=0)
    body: str = Field(max_length=MAX_EMBED_CHARS)
    stream_id: str = Field(min_length=1)

class EmbeddingResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    news_db_id: int
    model: str
    vector: tuple[float, ...]      # frozen 호환
    created_at: Optional[datetime] = None
```

---

## §2. Embedder (`backend/core/embeddings/embedder.py`)

```python
from typing import Protocol, runtime_checkable
import numpy as np

@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int
    async def encode(self, texts: list[str]) -> list[np.ndarray]: ...
```

### 2.1 FakeDeterministicEmbedder

```python
class FakeDeterministicEmbedder:
    """sha256(text) → 768-dim float32 L2-normalized. 결정성 보장."""
    name = "fake-deterministic-768"
    dim = 768

    async def encode(self, texts):
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode()).digest()
            base = np.concatenate([
                np.frombuffer(
                    hashlib.sha256(h + bytes([i])).digest(),
                    dtype=np.uint8,
                ).astype(np.float32)
                for i in range(3)
            ])
            arr = base.repeat(8) / 255.0
            n = np.linalg.norm(arr)
            arr = (arr / n) if n > 0 else arr
            out.append(arr.astype(np.float32))
        return out
```

### 2.2 LocalKoSbertEmbedder (lazy import + revision pin)

```python
class LocalKoSbertEmbedder:
    name = "ko-sbert-768"
    dim = 768

    def __init__(self, model_name="jhgan/ko-sroberta-multitask",
                 revision="", cache_folder=None, expected_dim=768):
        if not revision:
            raise ValueError("revision must be pinned (CWE-494)")
        self._model_name = model_name
        self._revision = revision
        self._cache_folder = cache_folder
        self.dim = expected_dim
        self._model = None  # lazy

    async def encode(self, texts):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(
                self._model_name,
                revision=self._revision,
                cache_folder=self._cache_folder,
            )
        if not texts:
            return []
        # CPU-bound → to_thread
        arrs = await asyncio.to_thread(
            self._model.encode, texts,
            normalize_embeddings=True, convert_to_numpy=True,
        )
        return [np.asarray(a, dtype=np.float32) for a in arrs]
```

### 2.3 팩토리

```python
def create_embedder(settings) -> Embedder:
    backend = settings.news_embedding_backend
    if backend == "fake":
        return FakeDeterministicEmbedder()
    if backend == "ko_sbert":
        return LocalKoSbertEmbedder(
            model_name=settings.news_embedding_model,
            revision=settings.news_embedding_revision or "",
            expected_dim=settings.news_embedding_dim,
        )
    if backend == "openai":
        raise NotImplementedError("openai → BAR-58b")
    raise ValueError(f"unknown backend: {backend}")
```

---

## §3. EmbeddingWorker (`backend/core/embeddings/worker.py`)

```python
class EmbeddingWorker:
    """Redis Streams news_items consumer group embedder_v1."""

    STREAM_KEY = "news_items"
    GROUP_NAME = "embedder_v1"
    BATCH_SIZE = 16
    BLOCK_MS = 1000     # shutdown race 단축

    def __init__(self, embedder, repo, redis_url: SecretStr,
                 stream_start: Literal["$", "0"] = "$",
                 batch_size: int = BATCH_SIZE):
        if not isinstance(redis_url, SecretStr):
            raise ValueError("redis_url must be SecretStr")
        if embedder.dim != getattr(repo, "expected_dim", embedder.dim):
            raise ValueError(f"dim mismatch: {embedder.dim} vs {repo.expected_dim}")
        if not (1 <= batch_size <= 64):
            raise ValueError("batch_size out of range [1, 64]")
        self.consumer_name = f"embedder-{socket.gethostname()}-{os.getpid()}"
        self._embedder = embedder
        self._repo = repo
        self._redis_url = redis_url
        self._stream_start = stream_start
        self._batch_size = batch_size
        self._stop = asyncio.Event()
        self._client = None
        self.processed = 0
        self.errors = 0

    async def _connect(self):
        if self._client is None:
            import redis.asyncio as redis_async
            self._client = redis_async.from_url(
                self._redis_url.get_secret_value(),
                decode_responses=True,
            )
            try:
                await self._client.xgroup_create(
                    self.STREAM_KEY, self.GROUP_NAME,
                    id=self._stream_start, mkstream=True,
                )
            except Exception:
                pass  # 이미 존재
        return self._client

    async def run(self):
        client = await self._connect()
        while not self._stop.is_set():
            resp = await client.xreadgroup(
                self.GROUP_NAME, self.consumer_name,
                streams={self.STREAM_KEY: ">"},
                count=self._batch_size, block=self.BLOCK_MS,
            )
            if not resp:
                continue
            await self._process_batch(client, resp[0][1])

    async def _process_batch(self, client, entries):
        jobs = []
        for stream_id, fields in entries:
            try:
                data = json.loads(fields["payload"])
                body = (data.get("body") or "")[:MAX_EMBED_CHARS]
                jobs.append(EmbeddingJob(
                    news_db_id=int(data["id"]),
                    body=body, stream_id=stream_id,
                ))
            except Exception:
                self.errors += 1
                await client.xack(self.STREAM_KEY, self.GROUP_NAME, stream_id)
        if not jobs:
            return
        try:
            vectors = await self._embedder.encode([j.body for j in jobs])
        except Exception:
            self.errors += 1
            return  # NACK — PEL 잔존
        for job, vec in zip(jobs, vectors):
            try:
                await self._repo.insert(EmbeddingResult(
                    news_db_id=job.news_db_id,
                    model=self._embedder.name,
                    vector=tuple(float(x) for x in vec),
                ))
                await client.xack(self.STREAM_KEY, self.GROUP_NAME, job.stream_id)
                self.processed += 1
            except Exception as exc:
                self.errors += 1
                logger.error(
                    "insert failed news_id=%s stream_id=%s err=%s",
                    job.news_db_id, job.stream_id, type(exc).__name__,
                )

    async def stop(self):
        self._stop.set()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
```

---

## §4. EmbeddingRepository (`backend/db/repositories/embedding_repo.py`)

```python
class EmbeddingRepository:
    expected_dim: int = 768

    async def insert(self, result: EmbeddingResult) -> bool:
        async with get_db() as db:
            if db is None:
                return False
            is_pg = db.engine.dialect.name == "postgresql"
            if is_pg:
                vec_payload = list(result.vector)
                sql = text("""
                    INSERT INTO embeddings (news_id, model, vector, created_at)
                    VALUES (:news_id, :model, :vector, NOW())
                    ON CONFLICT (news_id, model) DO NOTHING
                """)
            else:
                vec_payload = json.dumps(list(result.vector))
                sql = text("""
                    INSERT OR IGNORE INTO embeddings
                        (news_id, model, vector, created_at)
                    VALUES (:news_id, :model, :vector, :now)
                """)
            params = {
                "news_id": result.news_db_id,
                "model": result.model,
                "vector": vec_payload,
            }
            if not is_pg:
                params["now"] = datetime.now(timezone.utc).isoformat()
            res = await db.execute(sql, params)
            return (res.rowcount or 0) == 1

    async def search_similar(self, query_vec, model: str, top_k: int = 10):
        """반환: list[(news_id, distance)] — cosine distance, ASC (낮을수록 유사)."""
        async with get_db() as db:
            if db is None:
                return []
            is_pg = db.engine.dialect.name == "postgresql"
            if is_pg:
                sql = text("""
                    SELECT news_id, (vector <=> :q) AS distance
                    FROM embeddings WHERE model = :model
                    ORDER BY vector <=> :q ASC LIMIT :k
                """)
                res = await db.execute(sql, {
                    "q": list(query_vec), "model": model, "k": top_k,
                })
                return [(int(r["news_id"]), float(r["distance"]))
                        for r in res.mappings().all()]
            # SQLite fallback — Python 측 cosine
            res = await db.execute(text(
                "SELECT news_id, vector FROM embeddings WHERE model = :model"
            ), {"model": model})
            q = np.asarray(query_vec, dtype=np.float32)
            qn = np.linalg.norm(q) or 1.0
            pairs = []
            for r in res.mappings().all():
                v = np.asarray(json.loads(r["vector"]), dtype=np.float32)
                vn = np.linalg.norm(v) or 1.0
                sim = float(np.dot(q, v) / (qn * vn))
                pairs.append((int(r["news_id"]), 1.0 - sim))
            pairs.sort(key=lambda kv: kv[1])
            return pairs[:top_k]
```

---

## §5. Alembic 0003 (`alembic/versions/0003_embeddings.py`)

```python
def upgrade():
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    ts_type = postgresql.TIMESTAMP(timezone=True) if is_pg else sa.Text

    op.create_table(
        "embeddings",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("news_id", sa.BigInteger, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("vector", sa.Text, nullable=False),  # placeholder
        sa.Column(
            "created_at", ts_type, nullable=False,
            server_default=sa.text("NOW()") if is_pg else sa.text("''"),
        ),
        sa.UniqueConstraint("news_id", "model", name="uq_embeddings_news_model"),
    )
    op.create_index("idx_embeddings_news_id", "embeddings", ["news_id"])
    op.create_index("idx_embeddings_model", "embeddings", ["model"])

    if is_pg:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE embeddings ALTER COLUMN vector "
                   "TYPE vector(768) USING vector::vector")
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_vector_cos "
            "ON embeddings USING ivfflat (vector vector_cosine_ops) "
            "WITH (lists=100)"
        )


def downgrade():
    op.drop_index("idx_embeddings_model", table_name="embeddings")
    op.drop_index("idx_embeddings_news_id", table_name="embeddings")
    op.drop_table("embeddings")
```

---

## §6. Settings (`backend/config/settings.py`)

```python
# === 임베딩 (BAR-58) ===
news_embedding_backend: Literal["fake", "ko_sbert", "openai"] = "fake"
news_embedding_model: str = "jhgan/ko-sroberta-multitask"
news_embedding_dim: int = 768
news_embedding_batch_size: int = Field(default=16, ge=1, le=64)
news_embedding_revision: Optional[str] = None
openai_api_key: Optional[SecretStr] = None       # SecretStr (CWE-798)
anthropic_api_key: Optional[SecretStr] = None    # SecretStr (BAR-58b)
```

---

## §7. NewsItem.id 보강 (BAR-57a)

- `NewsItem` 에 `id: Optional[int] = None` 필드 추가 (frozen 호환).
- `NewsRepository.insert` 반환 `Optional[int]` (BIGSERIAL id) — Postgres `RETURNING id` / SQLite `lastrowid`.
- `NewsCollector._handle_item`: insert 후 `item = item.model_copy(update={"id": new_id})` 다음 `publisher.publish(item)`.
- 회귀 9건은 `id=None` 기본값으로 보존.

---

## §8. 테스트 매트릭스 (≥ 27 cases)

| 그룹 | 케이스 |
|------|:------:|
| `test_embedder_protocol.py` | 4 |
| `test_local_kosbert.py` | 3 (revision 미지정 ValueError / dim attr / lazy import) |
| `test_factory.py` | 3 |
| `test_worker.py` | 6 (XGROUP CREATE / batch encode / poison pill ACK / encode 실패 NACK / dim mismatch ValueError / shutdown FIRST_COMPLETED) |
| `test_embedding_repo.py` | 4 (insert 신규 / 중복 skip / search_similar SQLite cosine / TEXT round-trip 정밀도) |
| `test_alembic_0003.py` | 3 |
| `test_news_id_round_trip.py` | 4 (id None 기본 / insert 후 id 채움 / collector model_copy / publisher payload 에 id) |
| **합계** | **27** |

`--cov-fail-under=70` 게이트.

---

## §9. 회귀 게이트

- baseline: 299 passed (BAR-57a 후)
- 신규: 27 → **≥ 326 passed**
- DATABASE_URL 미설정 = SQLite fallback → 299 회귀 보존

---

## §10. 보안 요약

| CWE | 시그니처 |
|-----|----------|
| CWE-494 모델 공급망 | LocalKoSbertEmbedder revision 빈 문자열 ValueError |
| CWE-798 API 키 | openai_api_key / anthropic_api_key SecretStr |
| CWE-532 로그 누설 | Worker error log: news_id + stream_id + err_type 만 (body 제외) |
| CWE-1284 DoS | NewsItem.body 트렁케이션 8192 + BATCH Field(le=64) |

---

## §11. 후속 BAR

- **BAR-58b** : 실 ko-sbert (revision SHA pin) + 24h + claude-haiku 백업
- **BAR-59** : 테마 분류기 (search_similar cosine distance ASC 활용)
- **BAR-60** : 대장주 알고리즘

---

## §12. 다음 단계

`/pdca do BAR-58` — §1~§7 1:1 구현. DoD:
- [ ] backend/models/embedding.py + 4 tests
- [ ] backend/core/embeddings/{embedder,worker,__init__}.py + 13 tests (Protocol 4 + LocalKoSbert 3 + Factory 3 + Worker 6에서 일부 제외하면 적정)
- [ ] backend/db/repositories/embedding_repo.py + 4 tests
- [ ] alembic/versions/0003_embeddings.py + 3 tests
- [ ] BAR-57a 보강 (NewsItem.id + insert 반환 + collector model_copy) + 4 tests
- [ ] backend/config/settings.py 7 필드
- [ ] Makefile `test-embeddings`
- [ ] 회귀 ≥ 326 passed (0 fail), coverage ≥ 70%

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-07 | Initial draft (5 council 종합) | bkit-cto-lead |
