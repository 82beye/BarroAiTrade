# BAR-59a 테마 분류기 v1 — Completion Report

**Phase**: 3 (테마 인텔리전스) — **네 번째 BAR / Phase 3 분류 게이트**
**Ticket**: BAR-59 (59a worktree 트랙) — 3-tier ThemeClassifier
**Status**: ✅ COMPLETED (worktree backend)
**Date**: 2026-05-07

---

## 1. Outcomes

BAR-58a Embedder + BAR-57a NewsItem.id 위에 **3-tier 테마 분류기** 가 도입되었다. BAR-60 (대장주 점수) 의 입력 면 (`theme_stocks`) 봉인.

### 1.1 신규 추상화

| 구성 | 위치 | 역할 |
|------|------|------|
| ClassificationResult | `backend/models/theme.py` | frozen + tuple tags + attempted (fallback 추적) |
| ThemeClassifier Protocol | `backend/core/themes/classifier.py` | runtime_checkable + `_redact()` hook (CWE-200) |
| TfidfLogRegClassifier | 동상 | sklearn TF-IDF + LR (solver='liblinear', random_state=42) + kiwipiepy NN/VV/VA |
| EmbeddingCosineClassifier | 동상 | prototype 5종 in-memory cosine (search_similar 미사용) |
| ClaudeHaikuClassifier | 동상 | lazy stub — `__init__` 정상 + `classify()` raise |
| ThreeTierClassifier | 동상 | 1→2→3 + best-effort fallback + attempted 누적 |
| ClassifierFactory | 동상 | settings 기반 + auto-fit + labels_path 부재 ValueError |
| ThemeRepository | `backend/db/repositories/theme_repo.py` | upsert/keyword/link/find + dialect 분기 |
| alembic 0004 | `alembic/versions/0004_themes.py` | 3 테이블 + FK CASCADE + UNIQUE |

### 1.2 정책

| 정책 | 값 |
|------|------|
| 1차 임계 (TF-IDF) | confidence ≥ 0.7 |
| 2차 임계 (Embedding cosine distance) | distance ≤ 0.5 |
| 3차 (Claude haiku) | NotImplementedError → best-effort fallback |
| Fallback marker | `three_tier_v1:fallback_no_tier3:from_<best.backend>` |
| attempted 추적 | tuple[str,...] tier 누적 |
| 결정성 | sklearn `solver='liblinear'` + `random_state=42` |
| Tokenizer | module-level singleton (joblib pickle 호환) |
| FK CASCADE | themes 삭제 시 theme_keywords/theme_stocks 자동 삭제 |

### 1.3 fixture (`backend/tests/fixtures/theme_labels.json`)

테마 5종 × 5건 = 25 샘플:
- 전기차 / 반도체 / 바이오 / 원전 / AI

---

## 2. Validation

### 2.1 Tests

```
make test-themes
─────────────────────────────────────────────
30 passed in 3.62s
coverage 88.89% (≥ 70% gate)
```

| 파일 | 케이스 |
|------|:------:|
| test_classification_result.py | 2 |
| test_tfidf_lr.py | 4 |
| test_embedding_cosine.py | 4 |
| test_claude_haiku.py | 2 |
| test_three_tier.py | 5 |
| test_factory.py | 6 |
| test_theme_repo.py | 4 |
| test_alembic_0004.py | 3 |
| **합계** | **30** |

### 2.2 회귀

- 시작: 327 passed (BAR-58a 후)
- BAR-59a 머지 후: **357 passed, 1 skipped, 0 failed** (+30)
- DoD ≥ 355 충족

### 2.3 Gap Analysis (PR #101 머지)

- 매치율 **100%** (12/12) — PASS
- iterator 트리거 불필요

---

## 3. PR Trail

| Stage | PR | 상태 |
|-------|----|:----:|
| plan | #98 | ✅ MERGED (CTO Lead leader) |
| design | #99 | ✅ MERGED (5 pane council) |
| do | #100 | ✅ MERGED (30 tests, 357 회귀, coverage 88.89%) |
| analyze | #101 | ✅ MERGED (100%) |
| report | (this) | 진행 중 |

---

## 4. Phase 3 Progress (4/7)

| BAR | Title | Status |
|-----|-------|:------:|
| BAR-56 (56a) | Postgres + pgvector 인프라 | ✅ DONE |
| BAR-57 (57a) | 뉴스/공시 수집 파이프라인 | ✅ DONE |
| BAR-58 (58a) | 임베딩 인프라 | ✅ DONE |
| **BAR-59 (59a)** | 테마 분류기 v1 (3-tier) | ✅ DONE |
| BAR-60 | 대장주 점수 알고리즘 | NEXT |
| BAR-61 | 일정 캘린더 + 이벤트→종목 연동 | pending |
| BAR-62 | 프론트 (테마 박스 + 캘린더 + 뉴스 티커) | pending |

---

## 5. Lessons & Decisions

1. **a/b 분리 정책 5번째 답습** (BAR-54/56/57/58/59). worktree 환경 제약 (라벨링 데이터 / API 키 / 정확도 측정) → mock + fixture + lazy stub 으로 인터페이스 동결.
2. **Lazy stub 패턴 표준화**: ClaudeHaikuClassifier `__init__` 정상 구성 + `classify()` 진입 시 NotImplementedError. 후속 외부 API 어댑터 (OpenAI / 외부 LLM) 모두 동일 패턴 답습 가능.
3. **search_similar 미사용 결정 (architect 권고)**: prototype 5종은 in-process 배열 — repo round-trip 불필요. 후속 BAR-60 (대장주) 가 실 임베딩 검색 시 `EmbeddingRepository.search_similar` 활용 분기.
4. **module-level tokenizer singleton**: kiwipiepy `Kiwi()` 인스턴스를 모듈 레벨 lazy 초기화 → joblib pickle 호환 (BAR-59b 모델 직렬화 시) + 매 호출 사전 reload 회피.
5. **`_redact()` hook 자리잡기 (security CWE-200)**: BAR-59a 단계는 no-op, BAR-59b 진입 시 정규식 + presidio 로 교체. 인터페이스 break 없이 점진적 보안 강화.
6. **5 council 정상 가동 3회차**: BAR-57 / BAR-58 / BAR-59 design 모두 5 pane 병렬 검토 → 18~19KB COMBINED.md 자동 종합 → design 1:1 흡수. CTO Lead leader 와의 분담 패턴 확립.

---

## 6. Next Action

`/pdca plan BAR-60` — 대장주 점수 알고리즘.

핵심:
- theme_stocks (BAR-59) + embeddings (BAR-58) + 거래량/시가총액 결합
- 그리드 서치 (월 1회 cron) — 가중치 학습
- 백테스트 환경 정확도 ≥ 60% 게이트 (BAR-60b)

본 BAR-59a 의 `theme_stocks` + `find_themes_by_stock` 가 BAR-60 의 핵심 입력.
