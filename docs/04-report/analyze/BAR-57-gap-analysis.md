# BAR-57a 뉴스/공시 수집 — Design ↔ Implementation Gap Analysis

**Analyzed**: 2026-05-07
**Design**: `docs/02-design/features/bar-57-news-collection.design.md`
**Plan**: `docs/01-plan/features/bar-57-news-collection.plan.md`
**Implementation**: backend/models/news + core/news + db/repositories + alembic 0002 + tests

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
| 1 | NewsItem (frozen + SourceIdStr max_length=256/regex + 9 필드) | ✅ |
| 2 | NewsSource enum (DART + 4 RSS) | ✅ |
| 3 | RSSSource HOST_ALLOWLIST 4 도메인 + https-only enforce | ✅ |
| 4 | DARTSource SecretStr 강제 + query 마스킹 + 401/429 fallback | ✅ |
| 5 | InMemoryDeduplicator (LRU + TTL) + RedisDeduplicator (SecretStr) | ✅ |
| 6 | InMemoryStreamPublisher (maxsize+drop) + RedisStreamPublisher (XADD MAXLEN ~10000) | ✅ |
| 7 | NewsCollector 4단 시퀀스 (dedup.seen→insert→publish→mark) + 실패 분기 + retry/timeout | ✅ |
| 8 | news_repo (text() + named param + dialect 분기 INSERT OR IGNORE / ON CONFLICT) | ✅ |
| 9 | alembic 0002 (news_items + UNIQUE(source,source_id) + JSONB tags + 인덱스 2개) | ✅ |
| 10 | Settings 6 신규 필드 + redis_url SecretStr 승격 (CWE-522) | ✅ |
| 11 | Makefile test-news 타겟 + `--cov-fail-under=70` | ✅ |
| 12 | 37 tests PASSED, 회귀 299 passed (≥298 DoD), coverage 82.2% (≥70%) | ✅ |

## 누락 / 불일치

**없음.** 12/12 1:1 일치.

## 미세 메모 (gap 아님)

- 5 council 권고 모두 흡수 — 4단 시퀀스 / Redis Streams 5 항목 계약 / 9 필드 타입 매핑 / 보안 4 항목 / coverage 게이트.
- BAR-57a 단계 검증 — 운영 daemon 부재 (BAR-57b 분리).
- design §0 PR 분할 정책 명시 (5단 PDCA) 준수.

## 권장 후속

1. ✅ pdca-iterator 트리거 **불필요** (100% PASS).
2. `/pdca report BAR-57` 진행.
3. **BAR-57b** (운영 정식): docker compose redis 추가, 실 DART/RSS 가동, 24h 누락률 측정, SQLite fallback 제거.
4. **BAR-58** (임베딩 인프라): `embedder_v1` consumer group 등록, NewsItem.body → 1536-dim 벡터 → pgvector 적재.

**판정**: PASS — Phase 3 입력 게이트 통과.
