# BAR-57a 뉴스/공시 수집 파이프라인 — Completion Report

**Phase**: 3 (테마 인텔리전스) — **두 번째 BAR / Phase 3 입력 게이트**
**Ticket**: BAR-57 (57a worktree 트랙) — RSS + DART 수집 파이프라인
**Status**: ✅ COMPLETED (worktree backend)
**Date**: 2026-05-07

---

## 1. Outcomes

Phase 3 의 BAR-58 (임베딩) / BAR-59 (테마 분류기) / BAR-61 (일정 캘린더) 가 의존하는 **NewsItem stream 의 안정 발행** 파이프라인이 도입되었다. mock 단위 테스트 위에서 4단 시퀀스 (dedup→insert→publish→mark) 가 검증되어 BAR-58 진입 인터페이스가 동결됐다.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| NewsItem / NewsSource | `backend/models/news.py` | Pydantic v2 frozen + SourceIdStr (max_length=256) |
| NewsSourceAdapter Protocol | `backend/core/news/sources.py` | mock 친화 단일 메서드 |
| RSSSource | 동상 | HOST_ALLOWLIST 4 도메인 + https-only (SSRF CWE-918) |
| DARTSource | 동상 | SecretStr params dict + query 마스킹 (CWE-532) |
| InMemoryDeduplicator / RedisDeduplicator | `backend/core/news/dedup.py` | LRU+TTL / Redis SET+EXPIRE |
| InMemoryStreamPublisher / RedisStreamPublisher | `backend/core/news/publisher.py` | Queue drop / XADD MAXLEN ~10000 |
| NewsCollector | `backend/core/news/collector.py` | 4단 시퀀스 + retry/timeout + asyncio.gather 격리 |
| NewsRepository | `backend/db/repositories/news_repo.py` | text() + named param + dialect 분기 |
| alembic 0002 | `alembic/versions/0002_news_items.py` | UNIQUE(source,source_id) + JSONB tags + 인덱스 2 |

### 1.2 정책

| 정책 | 값 |
|------|------|
| 4단 시퀀스 | dedup.seen → repo.insert (ON CONFLICT) → publisher.publish → dedup.mark |
| 실패 분기 (publisher) | publish 실패 시 dedup.mark skip → 다음 사이클 재시도 |
| 0 row 분기 (race) | publish skip (NFR-07 재게재 0건) |
| retry/timeout 예산 | httpx 10s + wait_for 30s + retry 1회 (백오프 1s) |
| Redis Streams 계약 | key=`news_items` / consumer-group=`embedder_v1` (BAR-58) / payload 단일 필드 / MAXLEN ~10000 (≈41h retention) |
| 보안 | SSRF allowlist 4 도메인 / DART query 마스킹 / redis_url SecretStr 승격 / source_id max_length=256 |

### 1.3 분리 정책

| BAR | 트랙 | 본 사이클 |
|-----|------|:---:|
| **BAR-57a** | worktree (코드 + mock 단위 테스트) | ✅ 정식 do |
| **BAR-57b** | 운영 (실 daemon + 24h 운용) | deferred |

---

## 2. Validation

### 2.1 Tests

```
make test-news
─────────────────────────────────────────────
37 passed in 0.55s
coverage 82.2% (≥ 70% gate)
```

| 파일 | 케이스 |
|------|:------:|
| `test_news_models.py` | 5 |
| `test_rss_source.py` | 5 |
| `test_dart_source.py` | 6 |
| `test_dedup.py` | 5 |
| `test_publisher.py` | 4 |
| `test_collector.py` | 6 |
| `test_news_repo.py` | 3 |
| `test_alembic_0002.py` | 3 |
| **합계** | **37 PASSED** |

### 2.2 회귀

- 시작: 262 passed (BAR-56a 머지 후)
- BAR-57a 머지 후: **299 passed, 1 skipped, 0 failed** (+37)
- DoD ≥ 298 충족

### 2.3 Gap Analysis (PR #91 머지)

- 매치율 **100%** (12/12) — PASS
- iterator 트리거 불필요

상세: `docs/04-report/analyze/BAR-57-gap-analysis.md`

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #88 | ✅ MERGED (CTO Lead leader) |
| design | #89 | ✅ MERGED (5 pane council 종합) |
| do | #90 | ✅ MERGED (37 tests, 299 회귀, coverage 82.2%) |
| analyze | #91 | ✅ MERGED (100%) |
| report | (this) | 진행 중 |

---

## 4. Phase 3 Progress

| BAR | Title | Status |
|-----|-------|:------:|
| BAR-56 (56a) | Postgres + pgvector 인프라 | ✅ DONE |
| **BAR-57 (57a)** | 뉴스/공시 수집 파이프라인 | ✅ DONE |
| BAR-57b | 운영 정식 (docker compose redis + 실 DART/RSS + 24h) | deferred |
| BAR-58 | 임베딩 인프라 + pgvector 컬럼 + embedder_v1 consumer | NEXT |
| BAR-59 | 테마 분류기 v1 | pending |
| BAR-60 | 대장주 점수 알고리즘 | pending |
| BAR-61 | 일정 캘린더 + 이벤트→종목 연동 | pending |
| BAR-62 | 프론트 테마 박스 + 캘린더 + 뉴스 티커 | pending |

---

## 5. Lessons & Decisions

1. **5 pane tmux council 정상 가동 (BAR-META-001 patched)**: line-wrap 버그 수정 후 design 단계 5 역할 (architect / developer / qa / reviewer / security) 병렬 검토 → 18.6KB COMBINED.md 자동 종합. 단일 Agent 검토 대비 권고 다양성·품질 향상.
2. **a/b 분리 정책 답습**: BAR-54a/b · BAR-56a/b 와 동일. worktree 환경 제약 (Redis daemon 부재) 이지만 mock + Protocol 기반 인터페이스 동결로 BAR-58 진입 막힘 없음.
3. **dialect 무관 SQL 패턴 확장**: audit_repo (BAR-56) 의 `text()` + named param + dialect 분기 패턴이 news_repo 에도 그대로 적용. 후속 repository 추가 시 표준 템플릿.
4. **Redis Streams 5 항목 계약 동결**: stream key / consumer group / payload schema / ACK·PEL / MAXLEN 근거 — BAR-58 진입 시 재논의 0건.
5. **보안 4 항목 사전 봉인**: SSRF allowlist (CWE-918) / DART query 마스킹 (CWE-532) / redis_url SecretStr 승격 (CWE-522) / source_id max_length (CWE-1284) — Phase 5 보안 정식화 (BAR-67~70) 전이라도 본 BAR design 단계에서 동결.
6. **mock-first 테스트 전략**: redis.asyncio.from_url / httpx.AsyncClient 모두 patch — 실 daemon 없이도 4단 시퀀스 / retry / timeout / publisher 실패 분기 검증.

---

## 6. Next Action

`/pdca plan BAR-58` — 임베딩 인프라.

핵심:
- `embedder_v1` Redis Streams consumer group 등록 (BAR-57 의 `news_items` stream 소비)
- NewsItem.body → 1536-dim 벡터 (OpenAI / 768-dim ko-sbert local)
- pgvector 컬럼 추가 (alembic 0003)
- ivfflat / hnsw 인덱스 (벡터 검색)
- BAR-59 (테마 분류기) 의 입력 면 봉인

본 BAR-57a 의 NewsCollector + NewsRepository + Settings 패턴이 후속 BAR-58/59 의 표준 템플릿.
