# BAR-59a 테마 분류기 v1 — Design ↔ Implementation Gap Analysis

**Analyzed**: 2026-05-07
**Design**: `docs/02-design/features/bar-59-theme-classifier.design.md`
**Implementation**: backend/models/theme + core/themes + db/repositories + alembic 0004 + tests

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
| 1 | ClassificationResult (frozen + tuple tags + attempted) | ✅ |
| 2 | ThemeClassifier Protocol + _redact hook | ✅ |
| 3 | TfidfLogRegClassifier (solver='liblinear', random_state=42, kiwipiepy NN/VV/VA) | ✅ |
| 4 | EmbeddingCosineClassifier (prototype 캐시, search_similar 미사용, dim mismatch ValueError) | ✅ |
| 5 | ClaudeHaikuClassifier (lazy stub: __init__ 정상 + classify() raise) | ✅ |
| 6 | ThreeTierClassifier (1→2→3 + best-effort fallback + attempted 누적) | ✅ |
| 7 | ClassifierFactory (settings 기반 + auto-fit + labels_path 부재 ValueError) | ✅ |
| 8 | ThemeRepository (upsert/add_keyword/link_stock/find + dialect 분기 + FK CASCADE) | ✅ |
| 9 | alembic 0004 (themes/theme_keywords/theme_stocks + UNIQUE + 인덱스) | ✅ |
| 10 | Settings 4 신규 + fixture 5×5 | ✅ |
| 11 | Makefile test-themes + cov-fail-under=70 | ✅ |
| 12 | 30 tests + 회귀 357 (≥355 DoD) + coverage 88.89% | ✅ |

## 누락 / 불일치

**없음.** 12/12 1:1 일치. 5 council 권고 모두 흡수.

## 미세 메모 (gap 아님)

- TfidfLogReg `LabelBinarizer` 사용 — design 의사코드는 단일 라벨 fit, 실제 multilabel 호환성 위해 binarizer 추가. 결과 동일.
- EmbeddingCosine `_BadDimEmbedder` 단위 테스트 — prototype init 시 정상 → news encode 시 차원 변경으로 mismatch 유발. dim mismatch 검증 의도 충족.

## 권장 후속

1. ✅ pdca-iterator 트리거 **불필요** (100% PASS).
2. `/pdca report BAR-59` 진행.
3. **BAR-59b** (운영): 라벨링 1주 (≥ 1000건) + 실 LR 학습 + claude-haiku 활성화 + SHA256/HMAC 모델 무결성 + 정확도 ≥ 85% 게이트
4. **BAR-60** (대장주): theme_stocks + embeddings 결합 점수 (월 1회 그리드 서치)

**판정**: PASS — Phase 3 분류 게이트 통과.
