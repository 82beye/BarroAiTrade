---
tags: [plan, feature/bar-48, status/in_progress, phase/1, area/strategy]
template: plan
version: 1.0
---

# BAR-48 골드존 신규 포팅 Plan

> **Project**: BarroAiTrade / **Feature**: BAR-48 / **Phase**: 1 — 네 번째 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 1]]
> **Date**: 2026-05-06 / **Status**: In Progress

---

## 1. Overview

### 1.1 Purpose

골드존 전략 신규 구현 — 보수적 *되돌림 매수* 전략.

진입 조건 (3 조건 동시 충족):
- **BB 하단 터치**: 종가가 BB(20, 2σ) 하단 1% 이내 진입
- **Fib 0.382~0.618 zone**: 최근 30봉 고점-저점 기준 Fib 되돌림 zone 안
- **RSI 회복**: RSI 30 이하에서 40 돌파 (oversold → neutral 회복)

### 1.2 Background

- 마스터 플랜 v2 §2 Phase 1 네 번째 티켓
- 마스터 플랜 v1 의 BAR-48 명세: `seoheefather_strategy.py:GoldZoneStrategy` 의 BB+Fib+RSI 회복 로직 포팅
- F존 (급등 후 눌림목) 과 *반대 성향* — 골드존은 *과매도 회복* 매수

### 1.3 Related

- BAR-46 F존 v2 (선결): [[../../04-report/bar-46-f-zone-v2.report]]
- BAR-47 SF존 (선결): [[../../04-report/bar-47-sf-zone-split.report]]

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/core/strategy/gold_zone.py` 신규 — GoldZoneStrategy 클래스
- [ ] `GoldZoneParams` dataclass — BB 기간/배수, Fib 임계값, RSI oversold/recovery
- [ ] BB / Fib / RSI 계산 helper 메서드
- [ ] `_analyze_v2(ctx)` — 3 조건 동시 충족 시 EntrySignal 발행
- [ ] EntrySignal `signal_type` — `blue_line` (5 enum 제약 — 골드존은 추세 회복 성격)
  - 또는 metadata 에 `gold_zone_subtype` 보존
- [ ] `exit_plan` override — 보수적 (TP1=+2% 50% / TP2=+4% 50% / SL=-1.5% / time_exit=14:50 / breakeven=+1.0%)
- [ ] `position_size` override — 25% / 15% / 8% (보수적)
- [ ] `health_check` override — BB period ≥20, RSI period ≥14
- [ ] `tests/strategy/test_gold_zone.py` 6+
- [ ] BAR-44 베이스라인 회귀 (4 전략 모두 ±5%)

### 2.2 Out of Scope

- ❌ 38스윙 — BAR-49
- ❌ ScalpingConsensus — BAR-50
- ❌ EntrySignal Literal 확장 (gold_zone enum 추가) — 마스터 플랜 v2 후속 또는 BAR-79

---

## 3. Requirements

### 3.1 FR

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | GoldZoneStrategy (Strategy v2) + GoldZoneParams | High |
| FR-02 | BB(20, 2σ) 계산 — 종가 기반 SMA + std | High |
| FR-03 | Fib 0.382/0.5/0.618 계산 — 최근 30봉 고점-저점 | High |
| FR-04 | RSI(14) 계산 — Wilder smoothing | High |
| FR-05 | 진입: BB 하단 1% + Fib zone + RSI 30→40 회복 *동시* | High |
| FR-06 | EntrySignal.signal_type="blue_line", metadata 에 gold_zone 정보 | Medium |
| FR-07 | exit_plan: TP1+TP2+SL=-1.5%+breakeven+1.0% | High |
| FR-08 | position_size: 25%/15%/8% | High |
| FR-09 | BAR-44 베이스라인 회귀 ±5% | High |

### 3.2 NFR

| Category | 기준 |
|---|---|
| 회귀 | BAR-44 베이스라인 4 전략 ±5% |
| 성능 | _analyze_v2 ≤ 50ms |
| 커버리지 | gold_zone.py ≥ 80% |
| Decimal | exit_plan / position_size 자금흐름 |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] gold_zone.py + 6+ 테스트
- [ ] BAR-44 베이스라인 회귀 ±5%
- [ ] BAR-40~47 회귀 무영향
- [ ] cov ≥ 80%

### 4.2 6+ 테스트

| # | 케이스 |
|---|--------|
| C1 | GoldZoneStrategy import + Strategy 상속 |
| C2 | _analyze_v2 — 캔들 부족 시 None |
| C3 | _analyze_v2 — 합성 oversold 시나리오 → EntrySignal |
| C4 | exit_plan: TP1=+2%, TP2=+4%, SL=-1.5%, time_exit=14:50 |
| C5 | position_size 25%/15%/8% 분기 |
| C6 | health_check ready |
| C7 | BAR-44 베이스라인 회귀 (run_baseline) |

---

## 5. Risks

| Risk | Mitigation |
|------|------------|
| signal_type Literal 5 enum 제약 (gold_zone 부재) | "blue_line" 사용 + metadata.gold_zone_subtype |
| BB/Fib/RSI 계산 정확성 | pandas 표준 계산 (rolling mean/std), 단위 테스트로 검증 |
| BAR-44 합성 데이터에서 0 거래 가능 | 베이스라인 영향 0 (다른 4 전략 보존) |

---

## 6. Architecture

### 6.1 ExitPlan 매트릭스 (보수적)

| 항목 | 골드존 |
|---|---|
| TP1 | avg×1.02 (50%) |
| TP2 | avg×1.04 (50%) |
| SL | -1.5% |
| time_exit | 14:50 (KRX) / None (crypto) |
| breakeven_trigger | +1.0% |

### 6.2 position_size 매트릭스

| score | 비중 |
|---|---|
| ≥ 0.7 | 25% |
| 0.5~0.7 | 15% |
| < 0.5 | 8% |

### 6.3 score 산출

각 조건 충족도 가중합:
- BB 하단 거리 가까울수록 0~1 (1% 이내 → 1.0)
- Fib zone 중심(0.5) 가까울수록 0~1
- RSI 회복 강도 (30→40 가까이 도달) 0~1

`score = (bb*0.4 + fib*0.3 + rsi*0.3)` 가중합.

---

## 7. Convention Prerequisites

- ✅ Strategy v2 (BAR-45) + F존/SF존 패턴 (BAR-46/47)

---

## 8. Implementation Outline (D1~D7)

1. D1 — gold_zone.py 신규 (계산 helper + _analyze_v2)
2. D2 — exit_plan/position_size/health_check override
3. D3 — score 산출 로직
4. D4 — test_gold_zone.py 6+
5. D5 — V1~V6 검증
6. D6 — PR

---

## 9. Next

- BAR-49 38스윙 신규

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 plan — 보수적 되돌림 매수, BB+Fib+RSI 동시 충족 |
