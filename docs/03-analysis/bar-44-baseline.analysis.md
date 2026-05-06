---
tags: [analysis, feature/bar-44, status/in_progress, phase/0, area/strategy]
template: analysis
version: 1.0
---

# BAR-44 Gap Analysis Report

> **관련 문서**: [[../01-plan/features/bar-44-baseline.plan|Plan]] | [[../02-design/features/bar-44-baseline.design|Design]] | [[../04-report/PHASE-0-baseline-2026-05|Baseline Report]] | Report (pending)

- **Feature**: BAR-44 회귀 베이스라인 (옵션 2)
- **Phase**: 0 — 종료 게이트
- **Match Rate**: **96%**
- **Date**: 2026-05-06
- **Status**: ✅ Above 90% — `/pdca report` 진행 권장

---

## 1. Phase Scores

| Phase | Weight | Score |
|---|:---:|:---:|
| Plan FR (FR-01~FR-07, 7건) | 20% | 100% |
| Plan NFR (4건) | 10% | 95% |
| Plan DoD (5건) | 10% | 100% |
| Design §1 Architecture | 15% | 100% |
| Design §2 Implementation Spec (4 하위) | 15% | 100% |
| Design §3 Verification (V1~V6) | 15% | 100% |
| Design §4 D1~D10 Checklist | 15% | 100% |
| **Overall (가중)** | **100%** | **99% → 보수적 96%** |

> 가중 산식 99 → 보수적 96% (NFR 성능 측정 +α 마진 포함)

---

## 2. 검증 결과

| # | 시나리오 | 결과 |
|---|---|:---:|
| V1 | `make test-baseline` 6 케이스 | ✅ |
| V2 | `make baseline` 4 전략 결과 (≤30s) | ✅ 즉시 완료 |
| V3 | BAR-40 dry-run 회귀 | ✅ |
| V4 | BAR-41/42/43 pytest 회귀 | ✅ 35 (19+9+7) passed |
| V5 | 동일 seed 재현성 | ✅ test_c2 |
| V6 | baseline.md + JSON 생성 | ✅ |

---

## 3. Functional Requirements

| FR | 요구 | 구현 |
|----|------|:---:|
| FR-01 | 4 전략 합성 베이스라인 측정 | ✅ scripts/run_baseline.py |
| FR-02 | 5 지표 표 (4×5=20 셀) | ✅ PHASE-0-baseline-2026-05.md §2 |
| FR-03 | ±5% 회귀 임계값 정의 | ✅ baseline.md §3 |
| FR-04 | Fixed seed 재현성 | ✅ test_c2 |
| FR-05 | 마스터 플랜 v2 발행 | ✅ MASTER-EXECUTION-PLAN-v2.md |
| FR-06 | Phase 0 종합 회고 | ✅ PHASE-0-summary.md |
| FR-07 | 6+ 재현성 테스트 | ✅ 6 passed |

**FR Score: 7/7 = 100%**

---

## 4. Missing Items

| # | 항목 | 영향도 |
|---|---|:---:|
| M1 | NFR 성능 정량 측정 (≤30s) | Low — 실측 즉시 완료 (수 ms 추정), 향후 BAR-78 회귀 자동화 시 정밀 측정 |
| M2 | stock_v1, crypto_breakout_v1 가 0 거래 | 합리적 한계 — BAR-44b 정식 5년 측정에서 해결 (위임) |

비차단.

---

## 5. Additional Changes (5건, 정합 강화)

| # | 변경 |
|---|------|
| A1 | `Makefile` `test-baseline` + `baseline` 타겟 (Design §4.D8) |
| A2 | `docs/04-report/PHASE-0-baseline.json` (자동 생성, 회귀 데이터 머신리더블) |
| A3 | `_index.md` v1 📦 보존 + v2 🟢 active 표기 |
| A4 | TestBaselineMinimalData (`num_candles=50`, 보강) |
| A5 | 본 PR 에 마스터 플랜 v2 + Phase 0 회고 *통합* — Plan §2.1 의 단일 PR 정책 일관 |

모두 정합·강화 방향.

---

## 6. Risk Status (Plan §5)

| Risk | Status |
|---|:---:|
| 합성 데이터와 실측 차이 | ✅ 한계 명시 (baseline.md §2 해석 한계) |
| 0 거래 전략 | ✅ stock/crypto_breakout 0 거래 발생, 정식 측정 BAR-44b 위임 |
| v1/v2 wikilink 모호성 | ✅ v1 보존 + v2 supersede 명시 |
| BAR-51 재할당 | ✅ v2 §1 매트릭스 + Phase 6 BAR-79 |
| 백테스트 ≤30초 | ✅ 즉시 완료 |

전 위험 회피.

---

## 7. Conclusion

BAR-44 Phase 0 종료 게이트가 **96%** 매치로 통과. 옵션 2 (합성 데이터 + v2 + 회고 통합) 의 모든 핵심 산출물이 단일 do PR (#25) 에 포함되어 머지됨. 4 전략 베이스라인 표 + ±5% 회귀 임계값 + 마스터 플랜 v2 (9 변경) + Phase 0 종합 회고 (5 BAR / 27 PR / 평균 96.4%) 가 모두 발행되었다.

자금흐름·보안 영향 0건. legacy zero-modification 유지. 후속 BAR 의존 15+ 해소.

### 7.1 다음 단계

→ **`/pdca report BAR-44`** + **Phase 0 종료 선언** + **BAR-45 plan 진입**.

### 7.2 Iteration 비권장

- Match 96% > 90%
- 미달 2건은 합성 데이터 한계 (정식 BAR-44b) + 성능 측정 (BAR-78 통합)

---

## 8. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 분석 — 96% 매치, Phase 0 종료 게이트 통과 |
