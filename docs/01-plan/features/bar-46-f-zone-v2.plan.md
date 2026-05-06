---
tags: [plan, feature/bar-46, status/in_progress, phase/1, area/strategy]
template: plan
version: 1.0
---

# BAR-46 F존 v2 리팩터 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-46
> **Phase**: 1 (전략 엔진 통합) — 두 번째 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 1]]
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: BAR-47 (SF존 분리) 의 선결

---

## 1. Overview

### 1.1 Purpose

BAR-45 (Strategy v2) 의 인터페이스 위에서 **FZoneStrategy 를 v2 직접 구현으로 리팩터**하고, **ExitPlan / position_size override** 를 추가한다.

- 현재 `_analyze_v2(ctx)` 가 `_analyze_impl(symbol, name, candles, market_type)` 으로 위임 (BAR-45 backward compat shim)
- 본 BAR-46 에서 `_analyze_impl` 제거 + `_analyze_v2(ctx)` 가 *직접* ctx 사용
- `exit_plan(position, ctx)` override — F존 정책 (분할 익절 +3%/+5%, 손절 -2%, time_exit 14:50)
- `position_size(signal, account)` override — F존 강도 기반 비중 조정

### 1.2 Background

- 마스터 플랜 v2 §2 의 Phase 1 두 번째 티켓
- BAR-45 design §2.3 의 "후속 BAR-46~49 가 *내부 로직* 리팩터"
- BAR-44 베이스라인 회귀 ±5% 의무 (FZoneStrategy 거래 6, 승률 33.3%, 수익 -0.42%)
- F존 정책 (서희파더 매매기법): 분할 익절 +3%/+5%, 손절 -2%, 14:50 강제청산

### 1.3 Related Documents

- [[../MASTER-EXECUTION-PLAN-v2]]
- BAR-45 (선결, 완료): [[../../04-report/bar-45-strategy-v2.report]]
- 기존 F존: `backend/core/strategy/f_zone.py` (418 LOC)
- 베이스라인: [[../../04-report/PHASE-0-baseline-2026-05]]

---

## 2. Scope

### 2.1 In Scope

- [ ] `_analyze_impl` 제거 → `_analyze_v2(ctx)` 가 직접 ctx.symbol/candles 사용
- [ ] `exit_plan(position, ctx)` override — F존 정책
  - take_profits: [(price=avg×1.03, qty_pct=0.5), (price=avg×1.05, qty_pct=0.5)]
  - stop_loss: fixed_pct=-0.02
  - time_exit: 14:50 (KRX 정규장 종료 30분 전, 강제 청산)
  - breakeven_trigger: +0.015 (TP1 도달 직전)
- [ ] `position_size(signal, account)` override — F존 강도 (signal.score) 기반
  - score 0.7~1.0 → 자산의 30%
  - score 0.5~0.7 → 자산의 20%
  - score < 0.5 → 자산의 10%
- [ ] `health_check()` override — 캔들 ≥60, FZoneParams sanity
- [ ] `tests/strategy/test_f_zone.py` 신규 — 6+ 테스트
- [ ] BAR-44 베이스라인 ±5% 회귀

### 2.2 Out of Scope

- ❌ SF존 분리 — BAR-47
- ❌ FZoneAnalysis dataclass 변경 — 본문 보존
- ❌ ScalpingConsensus — BAR-50
- ❌ 백테스터 v2 — BAR-79

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `_analyze_v2(ctx)` 직접 구현 — `_analyze_impl` 호출 제거 | High |
| FR-02 | `exit_plan` override — F존 정책 (TP1+TP2+SL+time_exit+breakeven) | High |
| FR-03 | `position_size` override — score 기반 분기 (30%/20%/10%) | High |
| FR-04 | `health_check` override — 캔들 길이·param sanity | Medium |
| FR-05 | BAR-44 베이스라인 회귀 ±5% (6 거래 / 33.3% 승률 / -0.42% / MDD 0.81% / Sharpe -4.54) | High |
| FR-06 | `_analyze_impl` 제거 후에도 backward compat 동작 (BAR-45 dispatch 활용) | High |

### 3.2 Non-Functional

| Category | 기준 |
|---|---|
| 회귀 | BAR-44 베이스라인 4 전략 모두 ±5% |
| 성능 | `_analyze_v2` ≤ 50ms |
| 커버리지 | f_zone.py ≥ 80% |
| Decimal 안전 | exit_plan / position_size 자금흐름 Decimal |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] `_analyze_impl` 제거 + v2 직접 구현
- [ ] exit_plan/position_size/health_check override
- [ ] 6+ 테스트 통과
- [ ] BAR-44 베이스라인 회귀 ±5%
- [ ] BAR-40~45 회귀 무영향
- [ ] 라인 커버리지 ≥ 80%

### 4.2 6+ 테스트 케이스

| # | 케이스 |
|---|--------|
| C1 | `_analyze_v2(ctx)` 직접 호출 — EntrySignal 또는 None |
| C2 | `_analyze_impl` 부재 확인 (제거됨) |
| C3 | `exit_plan` — TP1=avg×1.03 / TP2=avg×1.05 / SL=-2% / time_exit=14:50 |
| C4 | `position_size` score=0.85 → 30% 기준 |
| C5 | `position_size` score=0.6 → 20% |
| C6 | `position_size` score=0.4 → 10% |
| C7 | `health_check` ready=True 조건 (params.min_candles 충족 시) |
| C8 | BAR-44 베이스라인 회귀 (run_baseline 결과 ±5%) |

### 4.3 Quality

- f_zone.py LOC ≤ 500 (현 418 + ExitPlan/position_size override 약 +50)
- Decimal 의무 (가격·수량)

---

## 5. Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| BAR-44 베이스라인 회귀 -5% 초과 | 본문 변경 최소화 — `_analyze_impl` 본문 그대로 `_analyze_v2` 안으로 inline |
| ExitPlan time_exit=14:50 이 KRX 외 시장에서 부적절 | market_type=STOCK 만 적용. crypto 면 None |
| score 분기 임계값 부적절 | 임계 0.7 / 0.5 는 *초기값* — Phase 1 종료 후 backtester 결과로 조정 (BAR-79) |
| `_analyze_impl` 제거 시 외부 호출처 회귀 | grep 으로 호출처 확인 — 0건 예상 (private) |
| Pydantic v2 ExitPlan 의 Decimal 변환 누락 | TakeProfitTier `_to_decimal` field_validator 활용 |

---

## 6. Architecture Considerations

### 6.1 ExitPlan 정책 매트릭스

| 필드 | F존 값 | 사유 |
|---|---|---|
| `take_profits[0]` | price=avg×1.03, qty_pct=0.5 | 1차 익절 +3% (절반 청산) |
| `take_profits[1]` | price=avg×1.05, qty_pct=0.5 | 2차 익절 +5% (나머지 청산) |
| `stop_loss.fixed_pct` | -0.02 | 손절 -2% |
| `time_exit` | 14:50 (KRX) / None (crypto) | KRX 정규장 종료 30분 전 강제 청산 |
| `breakeven_trigger` | +0.015 | +1.5% 도달 시 SL 을 +0.005 로 이동 (BAR-63 ExitPlan 엔진) |

### 6.2 position_size 정책 매트릭스

| score | 비중 |
|---|---|
| ≥ 0.7 | 자산의 30% (강한 F존, 풀 사이즈) |
| 0.5 ≤ score < 0.7 | 자산의 20% (중간 강도) |
| < 0.5 | 자산의 10% (약한 신호) |

수식: `available × ratio / price` quantize KRX 1주.

### 6.3 v2 직접 구현 vs `_analyze_impl` 보존

| 옵션 | 평가 |
|---|---|
| A. `_analyze_impl` 제거 + `_analyze_v2` 안에 inline | 단순, 의존 1 단계 감소 |
| B. `_analyze_impl` 보존, `_analyze_v2` 가 호출 (BAR-45 패턴 그대로) | 변경 최소, 회귀 위험 0 |

→ **A 채택** (Phase 1 의 *내부 로직 리팩터* 목적). 본문 변경 0, 호출 한 단계 제거.

---

## 7. Convention Prerequisites

- ✅ Strategy v2 ABC (BAR-45)
- ✅ Decimal 자금흐름 (BAR-45)
- ✅ pytest 인프라
- ✅ Pydantic v2

---

## 8. Implementation Outline (D1~D8)

1. D1 — `_analyze_impl` 호출처 grep (외부 호출 0 확인)
2. D2 — `_analyze_v2(ctx)` 안에 본문 inline + `_analyze_impl` 제거
3. D3 — `exit_plan(position, ctx)` override (F존 정책 §6.1)
4. D4 — `position_size(signal, account)` override (§6.2)
5. D5 — `health_check()` override
6. D6 — `tests/strategy/test_f_zone.py` 6+ 케이스
7. D7 — V1~V6 검증 (특히 BAR-44 베이스라인 회귀)
8. D8 — PR 생성 (`area:strategy` `phase:1` `priority:p1`)

---

## 9. Next Steps

1. [ ] Design
2. [ ] Do
3. [ ] Analyze
4. [ ] Report
5. [ ] **BAR-47 SF존 분리** 진입

---

## 10. 비고

- 본 BAR-46 은 *F존 한 전략* 만. BAR-47/48/49 는 동일 패턴 반복.
- 마스터 플랜 v2 §4 명세 일관 (LOC ≤ 500)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — F존 v2 직접 + ExitPlan/PositionSize override | beye |
