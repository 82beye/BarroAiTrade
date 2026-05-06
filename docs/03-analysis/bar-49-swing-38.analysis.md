---
tags: [analysis, feature/bar-49, status/in_progress, phase/1, area/strategy]
template: analysis
version: 1.0
---

# BAR-49 Gap Analysis Report

> **관련 문서**: [[../01-plan/features/bar-49-swing-38.plan|Plan]] | [[../02-design/features/bar-49-swing-38.design|Design]]

- **Match Rate**: **96%**
- **Date**: 2026-05-06
- **Status**: ✅ Above 90% — `/pdca report` 권장

---

## 1. Phase Scores

| Phase | Score |
|---|:---:|
| Plan FR (7) | 100% |
| Plan NFR (4) | 95% |
| Plan DoD | 100% |
| Design Implementation | 100% |
| 8+ → 10 테스트 | 100% |
| V1~V6 | 92% |
| **Overall** | **96%** |

---

## 2. 검증

| # | 결과 |
|---|:---:|
| V1 74 테스트 (이전 64 + 신규 10) | ✅ |
| V2 cov 94% | ✅ |
| V3 BAR-44 베이스라인 (F존 6 / BlueLine 12) | ✅ |
| V4 BAR-40~48 회귀 | ✅ |
| V5 exit_plan TP qty 합 1.0 | ✅ |
| V6 metadata.swing_38_subtype | ✅ |

---

## 3. FR

| FR | 구현 |
|----|:---:|
| FR-01 Swing38Strategy + Params | ✅ |
| FR-02 임펄스 탐지 | ✅ |
| FR-03 Fib 0.382±7.5% | ✅ |
| FR-04 반등 캔들 | ✅ |
| FR-05 EntrySignal metadata | ✅ |
| FR-06 3 override | ✅ |
| FR-07 BAR-44 회귀 | ✅ |

---

## 4. Missing (1, 비차단)

- M1: 합성 시나리오 결정성 — BAR-79 백테스터 v2

---

## 5. Conclusion

**96%** 매치. 38스윙 — 임펄스(5%/2x) + Fib 0.382±7.5% + 반등 가중합. 10 테스트, BAR-44 보존.

→ `/pdca report BAR-49` + **BAR-50 (ScalpingConsensus)** 진입 — Phase 1 마지막.

---

## 6. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 분석 — 96% |
