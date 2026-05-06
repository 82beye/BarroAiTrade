# BAR-58a 임베딩 인프라 — Design ↔ Implementation Gap Analysis

**Analyzed**: 2026-05-07
**Design**: `docs/02-design/features/bar-58-embedding-infra.design.md`
**Implementation**: backend/models/embedding + core/embeddings + db/repositories + alembic 0003 + tests

## Summary

| Metric | Value |
|---|---|
| 총 항목 수 | 12 |
| 매치 항목 수 | 12 |
| **매치율** | **100 %** |
| 상태 | **PASS** (≥ 90 %) |

## Verification Matrix

| # | 항목 | 결과 |
|---|------|:---:|
| 1 | EmbeddingJob/Result (frozen + MAX_EMBED_CHARS=8192) | ✅ |
| 2 | Embedder Protocol (runtime_checkable + name + dim + async encode) | ✅ |
| 3 | FakeDeterministicEmbedder (sha256 → 768-dim L2 normalized 결정성) | ✅ |
| 4 | LocalKoSbertEmbedder (lazy import + revision="" → ValueError + asyncio.to_thread) | ✅ |
| 5 | create_embedder 팩토리 (fake/ko_sbert/openai NotImplementedError) | ✅ |
| 6 | EmbeddingWorker (consumer_name + BATCH 16 + BLOCK 1000 + dim mismatch + SecretStr + XGROUP CREATE + poison ACK + encode fail NACK) | ✅ |
| 7 | EmbeddingRepository (text() + dialect 분기 + cosine distance ASC) | ✅ |
| 8 | alembic 0003 (embeddings + UNIQUE + ivfflat) | ✅ |
| 9 | NewsItem.id Optional + insert → Optional[int] + collector model_copy | ✅ |
| 10 | Settings 7 신규 (news_embedding_* + openai/anthropic SecretStr) | ✅ |
| 11 | Makefile test-embeddings + cov-fail-under=70 | ✅ |
| 12 | 28 tests + 회귀 327 (≥326 DoD) + coverage 77.64% | ✅ |

## 누락 / 불일치

**없음.** 12/12 1:1 일치. 5 council 권고 모두 흡수.

## 미세 메모 (gap 아님)

- `EmbeddingWorker.run_once()` — 설계 외 테스트 헬퍼 (run() 무한 loop 대신 결정적 1 batch 처리). 무해, 단위 테스트 결정성 확보.
- BAR-57a 회귀 9건 — NewsItem.id Optional 변경 후에도 모두 PASS (id=None 기본값 보존 확인).

## 권장 후속

1. ✅ pdca-iterator 트리거 **불필요** (100% PASS)
2. `/pdca report BAR-58` 진행
3. **BAR-58b** (운영 정식): 실 ko-sbert (HuggingFace revision SHA pin) + Redis daemon + 24h + 100건 P95 ≤ 500ms + claude-haiku 백업
4. **BAR-59** (테마 분류기): `EmbeddingRepository.search_similar` (cosine distance ASC) 활용

**판정**: PASS — Phase 3 변환 게이트 통과.
