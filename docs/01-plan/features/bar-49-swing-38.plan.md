---
tags: [plan, feature/bar-49, status/in_progress, phase/1, area/strategy]
template: plan
version: 1.0
---

# BAR-49 38스윙 신규 포팅 Plan

> **Project**: BarroAiTrade / **Feature**: BAR-49 / **Phase**: 1 — 다섯 번째 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 1]]
> **Date**: 2026-05-06 / **Status**: In Progress

---

## 1. Overview

### 1.1 Purpose

38스윙 전략 신규 — *임펄스 후 Fib 0.382 되돌림 매수*.

진입 조건 (3 단계 순차):
1. **임펄스 탐지**: 최근 N봉 내 +5% 이상 단일 양봉 + 거래량 평균 2배 이상
2. **0.382 되돌림**: 임펄스 고점 대비 0.30~0.45 되돌림 zone 안 (Fib 0.382 ± 7.5%)
3. **반등 확인**: 되돌림 후 양봉 1개 이상 + 마감가 > 시가

### 1.2 Background

- 마스터 플랜 v2 §2 Phase 1 다섯 번째
- 마스터 플랜 v1 BAR-49 명세: `Swing38Strategy` 의 Fib 0.382 + 임펄스 탐지 포팅
- F존(눌림목 -2~-5%) 보다 깊은 되돌림 (-30~45%) 을 노리는 *스윙* 매매

---

## 2. Scope

### 2.1 In

- [ ] backend/core/strategy/swing_38.py — Swing38Strategy + Swing38Params
- [ ] _analyze_v2 — 3 단계 순차 검증
- [ ] exit_plan: TP1=+2.5% (50%), TP2=+5% (50%), SL=-1.5%, time_exit=14:50, breakeven=+1.2%
- [ ] position_size: 28%/18%/8%
- [ ] health_check: impulse_min_gain ≥ 5%
- [ ] tests/strategy/test_swing_38.py 6+
- [ ] BAR-44 베이스라인 ±5%

### 2.2 Out

- ❌ ScalpingConsensus — BAR-50

---

## 3. Requirements

### 3.1 FR

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | Swing38Strategy + Params | High |
| FR-02 | 임펄스 탐지 (gain ≥ 5% + volume ≥ 2x avg) | High |
| FR-03 | Fib 0.382 ± 7.5% zone 검증 | High |
| FR-04 | 반등 캔들 확인 (마감 > 시가) | High |
| FR-05 | EntrySignal signal_type="blue_line" + metadata | Medium |
| FR-06 | exit_plan / position_size / health_check override | High |
| FR-07 | BAR-44 베이스라인 회귀 | High |

### 3.2 NFR

| Category | 기준 |
|---|---|
| 회귀 | BAR-44 베이스라인 ±5% |
| 커버리지 | swing_38.py ≥ 80% |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] 6+ 테스트
- [ ] BAR-44 회귀
- [ ] BAR-40~48 회귀 무영향

### 4.2 6+ 테스트

| # | 케이스 |
|---|---|
| C1 | Strategy 상속 |
| C2 | min_candles 미달 None |
| C3 | 합성 임펄스 시나리오 → EntrySignal 또는 None |
| C4 | exit_plan TP1=+2.5%, TP2=+5%, SL=-1.5% |
| C5 | position_size 28%/18%/8% |
| C6 | health_check ready |
| C7 | BAR-44 베이스라인 보존 |

---

## 5. Risks & Mitigation

| Risk | Mitigation |
|------|------------|
| 임펄스 탐지 임계값 부적절 | 초기 5% / 2x — Phase 1 종료 후 조정 (BAR-79) |
| Fib 0.382 ± 7.5% 너무 넓음 | metadata.fib_score 보존 |
| 합성 데이터 0 거래 | F존 6 / BlueLine 12 보존만 검증 |

---

## 6. Architecture

### 6.1 ExitPlan 매트릭스

| 항목 | 38스윙 |
|---|---|
| TP1 | avg×1.025 (50%) |
| TP2 | avg×1.05 (50%) |
| SL | -1.5% |
| time_exit | 14:50 (KRX) / None (crypto) |
| breakeven | +1.2% |

### 6.2 position_size

| score | 비중 |
|---|---|
| ≥0.7 | 28% |
| 0.5~0.7 | 18% |
| <0.5 | 8% |

### 6.3 score 산출

`score = impulse_score*0.4 + fib_score*0.4 + bounce_score*0.2`

- impulse_score: gain/0.05 비율, max 1.0
- fib_score: |retrace - 0.382| / 0.075 → 1.0 - distance
- bounce_score: 양봉 마감 강도 (0~1)

---

## 7. Convention Prerequisites

- ✅ Strategy v2 + 패턴 (BAR-46/47/48)

---

## 8. Implementation Outline (D1~D5)

1. D1 — swing_38.py (3 단계 helper + _analyze_v2 + 3 override)
2. D2 — test_swing_38.py 6+
3. D3 — V1~V6
4. D4 — PR

---

## 9. Next

- BAR-50 ScalpingConsensus (Phase 1 마지막)

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 plan — 38스윙, 임펄스+Fib 0.382+반등 |
