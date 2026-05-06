---
tags: [analysis, feature/bar-46, status/in_progress, phase/1, area/strategy]
template: analysis
version: 1.0
---

# BAR-46 Gap Analysis Report

> **관련 문서**: [[../01-plan/features/bar-46-f-zone-v2.plan|Plan]] | [[../02-design/features/bar-46-f-zone-v2.design|Design]] | Report (pending)

- **Match Rate**: **97%**
- **Date**: 2026-05-06
- **Status**: ✅ Above 90% — `/pdca report` 권장

---

## 1. Phase Scores

| Phase | Score |
|---|:---:|
| Plan FR (6) | 100% |
| Plan NFR (4) | 95% |
| Plan DoD (5) | 100% |
| Design §1 Implementation Spec | 100% |
| Design §2 6+ 테스트 → 실측 10 | 100% |
| Design §3 V1~V6 | 100% |
| **Overall** | **97%** |

---

## 2. 검증 결과

| # | 결과 |
|---|:---:|
| V1 41 테스트 (이전 31 + 신규 10) | ✅ |
| V2 라인 커버리지 94% | ✅ |
| V3 **BAR-44 베이스라인 수치 변동 0건 (100% 일치)** | ✅ |
| V4 BAR-40~45 회귀 | ✅ 73 테스트 무영향 |
| V5 `_analyze_impl` 부재 | ✅ test_c2 |
| V6 exit_plan Decimal 정확 | ✅ TP qty_pct 합 1.0 |

---

## 3. Functional Requirements

| FR | 구현 |
|----|:---:|
| FR-01 _analyze_v2 직접 | ✅ |
| FR-02 exit_plan override | ✅ |
| FR-03 position_size override (30%/20%/10%) | ✅ |
| FR-04 health_check override | ✅ |
| FR-05 BAR-44 베이스라인 ±5% | ✅ 0% 변동 |
| FR-06 backward compat 동작 | ✅ |

---

## 4. Missing (1, 비차단)

- M1: `_analyze_v2` ≤50ms 정량 측정 → BAR-78 통합

---

## 5. Additional Changes

- A1: 보강 테스트 4 (crypto time_exit None / zero balance / health_check params)
- A2: conftest 에 score 분기 fixture 3종 + crypto ctx

---

## 6. Conclusion

BAR-46 F존 v2 리팩터가 **97%** 매치. BAR-44 베이스라인 수치 변동 0건 — 옵션 A (`_analyze_impl` 제거 + inline) 안전 입증.

ExitPlan / PositionSize / HealthCheck 정책이 서희파더 매매기법 그대로 코드화 — TP1=+3% (절반) / TP2=+5% (나머지) / SL=-2% / 14:50 강제 / breakeven +1.5%.

### 6.1 다음

→ `/pdca report BAR-46` + **BAR-47 SF존 분리** 진입.

### 6.2 후속 인계

- BAR-47: F존 클래스에서 SF존 분기를 별도 클래스로 분리
- BAR-48/49: 골드존/38스윙 신규 (BAR-46 패턴 그대로)
- BAR-63: ExitPlan 분할 익절 엔진 정식화

---

## 7. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 분석 — 97% 매치, 베이스라인 수치 변동 0 |
