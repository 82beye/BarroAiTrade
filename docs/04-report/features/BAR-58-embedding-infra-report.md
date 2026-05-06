# BAR-58a 임베딩 인프라 — Completion Report

**Phase**: 3 (테마 인텔리전스) — **세 번째 BAR / Phase 3 변환 게이트**
**Ticket**: BAR-58 (58a worktree 트랙) — Embedder + EmbeddingWorker + pgvector 컬럼
**Status**: ✅ COMPLETED (worktree backend)
**Date**: 2026-05-07

---

## 1. Outcomes

BAR-57a `news_items` Redis Streams → 768-dim 벡터 → `embeddings` 테이블 적재 + cosine 검색까지 인프라가 동결되었다. BAR-59 (테마 분류기) 의 입력 면 (`EmbeddingRepository.search_similar`) 봉인.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| EmbeddingJob/Result | `backend/models/embedding.py` | frozen + MAX_EMBED_CHARS=8192 (CWE-1284) |
| Embedder Protocol | `backend/core/embeddings/embedder.py` | runtime_checkable, async encode |
| FakeDeterministicEmbedder | 동상 | sha256 → 768-dim L2 norm (테스트/dev 결정성) |
| LocalKoSbertEmbedder | 동상 | sentence-transformers + revision pin (CWE-494) + asyncio.to_thread |
| create_embedder | 동상 | fake / ko_sbert / openai (NotImplementedError) |
| EmbeddingWorker | `backend/core/embeddings/worker.py` | XREADGROUP + batch + ACK + poison + shutdown |
| EmbeddingRepository | `backend/db/repositories/embedding_repo.py` | cosine **distance** ASC, dialect 분기 |
| alembic 0003 | `alembic/versions/0003_embeddings.py` | UNIQUE + ivfflat (Postgres) / TEXT (SQLite) |

### 1.2 정책

| 정책 | 값 |
|------|------|
| consumer group | `embedder_v1` (BAR-57 publisher 와 짝) |
| BATCH_SIZE | 16 |
| BLOCK_MS | 1000 (shutdown race 단축) |
| 부분 실패 (encode) | entire batch NACK (PEL 잔존) — BAR-58b 에서 claim 회복 |
| 부분 실패 (insert) | individual error counter, 다른 entry 진행 |
| poison pill | ACK + counter (재시도 무의미) |
| consumer_name | `embedder-{hostname}-{pid}` |
| search_similar | cosine **distance** ASC (Postgres `<=>` / SQLite Python) |

### 1.3 BAR-57a 보강 (architect 권고)

- `NewsItem.id: Optional[int] = None` 추가 (frozen 호환)
- `NewsRepository.insert` → `Optional[int]` (BIGSERIAL id) 반환 (Postgres `RETURNING id` / SQLite `last_insert_rowid()`)
- `NewsCollector._handle_item` → `model_copy(update={"id": new_id})` → publisher

회귀 9건 모두 PASS.

---

## 2. Validation

### 2.1 Tests

```
make test-embeddings
─────────────────────────────────────────────
28 passed in 0.52s
coverage 77.64% (≥ 70% gate)
```

| 파일 | 케이스 |
|------|:------:|
| test_embedder_protocol.py | 4 |
| test_local_kosbert.py | 3 |
| test_factory.py | 3 |
| test_worker.py | 7 |
| test_embedding_repo.py | 4 |
| test_alembic_0003.py | 3 |
| test_news_id_round_trip.py | 4 |
| **합계** | **28** |

### 2.2 회귀

- 시작: 299 passed (BAR-57a 후)
- BAR-58a 머지 후: **327 passed, 1 skipped, 0 failed** (+28)
- DoD ≥ 326 충족
- BAR-57a 회귀 9건 — NewsItem.id Optional 변경 후에도 모두 PASS

### 2.3 Gap Analysis (PR #96 머지)

- 매치율 **100%** (12/12) — PASS
- iterator 트리거 불필요

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #93 | ✅ MERGED (CTO Lead leader) |
| design | #94 | ✅ MERGED (5 pane council) |
| do | #95 | ✅ MERGED (28 tests, 327 회귀, coverage 77.64%) |
| analyze | #96 | ✅ MERGED (100%) |
| report | (this) | 진행 중 |

---

## 4. Phase 3 Progress

| BAR | Title | Status |
|-----|-------|:------:|
| BAR-56 (56a) | Postgres + pgvector 인프라 | ✅ DONE |
| BAR-57 (57a) | 뉴스/공시 수집 파이프라인 | ✅ DONE |
| **BAR-58 (58a)** | 임베딩 인프라 (Embedder + Worker + pgvector 컬럼) | ✅ DONE |
| BAR-58b | 운영 (실 ko-sbert + 24h + claude-haiku 백업) | deferred |
| BAR-59 | 테마 분류기 v1 | NEXT |
| BAR-60 | 대장주 점수 알고리즘 | pending |
| BAR-61 | 일정 캘린더 + 이벤트→종목 연동 | pending |
| BAR-62 | 프론트 (테마 박스 + 캘린더 + 뉴스 티커) | pending |

---

## 5. Lessons & Decisions

1. **a/b 분리 정책 4번째 답습**: BAR-54a/b · BAR-56a/b · BAR-57a/b · BAR-58a/b. worktree 환경 제약 (모델 다운로드 / Redis daemon / 24h 운용) 을 backend 인터페이스 동결 + mock 단위 테스트로 우회하는 패턴 표준화.
2. **`search_similar` 메트릭 통일**: cosine **distance** ASC 으로 통일 (낮을수록 유사). BAR-59 진입 시 임계치 방향 inversion 위험 사전 차단.
3. **`asyncio.to_thread` 명시**: CPU-bound encode (ko-sbert) 가 event loop blocking 회피. 후속 BAR (테마 분류 + 대장주 점수) 도 동일 패턴.
4. **Supply chain 보안 (CWE-494)**: `LocalKoSbertEmbedder.__init__` 의 `revision=""` 시 ValueError. HuggingFace mirror 변조 / typosquatting 사전 차단.
5. **5 pane council 정상 가동 (2회차)**: BAR-57 design / BAR-58 design 모두 18~19KB COMBINED.md 자동 종합. 단일 Agent 대비 권고 다양성 + design 흡수 무결성 향상.
6. **BAR-57a 보강 = council 1번째 권고 (architect)**: NewsItem.id 추가 → news_repo insert 반환 → collector model_copy 의 3-step 작은 변경으로 BAR-58 진입 게이트 봉인. 회귀 0 fail 보존.
7. **dialect 분기 패턴 확장**: BAR-56 audit_repo / BAR-57 news_repo / BAR-58 embedding_repo 모두 `text() + named param + dialect 분기` 표준 답습. 후속 repository (theme_repo / journal_repo) 도 동일 템플릿.

---

## 6. Next Action

`/pdca plan BAR-59` — 테마 분류기 v1.

핵심:
- TF-IDF + LR (1차) → 임베딩 코사인 (2차) → claude-haiku zero-shot (3차) 3-tier
- `EmbeddingRepository.search_similar` (cosine distance ASC) 활용
- 운영자 라벨링 1주 검증 정확도 ≥ 85% 게이트 (BAR-59b)
- BAR-60 (대장주) 입력 면 = 분류기 결과 + theme tags

본 BAR-58a 의 Embedder + Worker 패턴이 후속 BAR-59 (분류기) 의 표준 템플릿.
