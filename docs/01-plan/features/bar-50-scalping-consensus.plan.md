---
tags: [plan, feature/bar-50, status/in_progress, phase/1, area/strategy]
template: plan
version: 1.0
---

# BAR-50 ScalpingConsensusStrategy Plan — Phase 1 마지막 🎯

> **Project**: BarroAiTrade / **Feature**: BAR-50 / **Phase**: 1 — **마지막 티켓**
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 1]]
> **Date**: 2026-05-06 / **Status**: In Progress

---

## 1. Overview

### 1.1 Purpose

ai-trade 의 12 legacy_scalping 에이전트 (vwap, momentum_burst, breakout_confirm, spread_tape, golden_time, pullback, relative_strength, candle_pattern, volume_profile, risk_reward 등) 가중합을 표준 Strategy v2 인터페이스 위에서 노출.

핵심:
- **Delegate**: legacy `ScalpingCoordinator` 가 12 에이전트 관리 + 가중합. 본 BAR-50 은 결과 (`ScalpingAnalysis`) 를 받아 `EntrySignal` 로 변환 (BAR-41 어댑터 활용).
- **threshold 0.65**: total_score (0~100) → score(0~1) 정규화 후 ≥0.65 만 통과.
- 본 BAR-50 = *통합 인터페이스 layer*. 12 에이전트 본문 변경 0.

### 1.2 Background

- 마스터 플랜 v2 §2 Phase 1 마지막 티켓
- 마스터 플랜 v1 BAR-50: ScalpingConsensusStrategy — 12 에이전트 가중합, threshold 0.65
- BAR-41 (어댑터): `to_entry_signal(legacy_data)` 사용 가능
- legacy ScalpingCoordinator 는 `analyze(snapshots, cache_data, intraday_data)` 시그니처라 *AnalysisContext 와 시그니처가 다름* — adapter layer 필요

### 1.3 Related

- BAR-41 (어댑터): [[../../04-report/bar-41-model-adapter.report]]
- BAR-45/46/47/48/49 모두 ✅
- legacy ScalpingCoordinator: `backend/legacy_scalping/strategy/scalping_team/coordinator.py`

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/core/strategy/scalping_consensus.py` 신규 — ScalpingConsensusStrategy
- [ ] **옵션 B (외부 ScalpingAnalysis 입력)** — `_analyze_v2(ctx)` 가 ctx.metadata 또는 별도 channel 로 *이미 분석된* ScalpingAnalysis 를 받음 (legacy coordinator 호출은 본 BAR 범위 외, 후속 통합 시점)
- [ ] 모킹 가능한 인터페이스 — `set_analysis_provider(callable)` 또는 ctx 활용
- [ ] threshold 0.65 적용
- [ ] BAR-41 `to_entry_signal` 활용
- [ ] ExitPlan 단타: TP1=+1.5% (50%), TP2=+3% (50%), SL=-1%, time_exit=14:50, breakeven=+0.5%
- [ ] position_size 보수적: 25%/15%/8%
- [ ] tests/strategy/test_scalping_consensus.py 6+
- [ ] BAR-44 베이스라인 ±5%

### 2.2 Out of Scope

- ❌ legacy ScalpingCoordinator 직접 호출 (외부 OHLCV 수집·snapshot 생성 포함, 후속 BAR-78 통합 자동화 시점)
- ❌ 12 에이전트 본문 변경 (zero-modification)
- ❌ 가중치 그리드 서치 (BAR-79 백테스터 v2 시점)

---

## 3. Requirements

### 3.1 FR

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | ScalpingConsensusStrategy + Strategy v2 상속 | High |
| FR-02 | ScalpingAnalysis 또는 dict 입력 → BAR-41 to_entry_signal 위임 | High |
| FR-03 | total_score / 100 ≥ 0.65 만 통과 (threshold) | High |
| FR-04 | analysis_provider injection 지원 (테스트 모킹·후속 coordinator 통합) | Medium |
| FR-05 | 단타 ExitPlan (짧은 hold) | High |
| FR-06 | position_size 25%/15%/8% (보수적, 단타이므로) | High |
| FR-07 | health_check ready (provider 등록 여부 등) | Medium |
| FR-08 | BAR-44 베이스라인 회귀 | High |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] scalping_consensus.py 신규
- [ ] 6+ 테스트
- [ ] BAR-44 회귀
- [ ] BAR-40~49 회귀 무영향

### 4.2 6+ 테스트

| # | 케이스 |
|---|---|
| C1 | Strategy 상속 |
| C2 | provider 미등록 시 None |
| C3 | ScalpingAnalysis (total_score=85) → EntrySignal |
| C4 | total_score=50 (≥65 미달) → None |
| C5 | exit_plan TP1=+1.5%, TP2=+3%, SL=-1% |
| C6 | position_size 25%/15%/8% |
| C7 | health_check ready (provider 등록 후) |
| C8 | BAR-44 베이스라인 보존 |

---

## 5. Architecture

### 5.1 옵션

| 옵션 | 평가 |
|---|---|
| A. legacy ScalpingCoordinator 직접 호출 | OHLCV·snapshot·intraday 데이터 수집 필요. 본 BAR 범위 너무 큼 |
| B. **provider injection** (analysis 외부 주입) | 본 BAR 단순. legacy 통합은 후속 BAR (BAR-78 회귀 자동화 시점) |
| C. ctx.metadata 활용 | provider injection 은 더 명시적 |

→ **B 채택**. `set_analysis_provider(callable)` 메서드로 외부 주입.

### 5.2 ExitPlan 매트릭스 (단타)

| 항목 | ScalpingConsensus |
|---|---|
| TP1 | avg×1.015 (50%) |
| TP2 | avg×1.03 (50%) |
| SL | -1% |
| time_exit | 14:50 (KRX) / None (crypto) |
| breakeven | +0.5% (조기) |

### 5.3 position_size 매트릭스

| score | 비중 |
|---|---|
| ≥0.7 (0.65 임계 위) | 25% |
| 0.5~0.7 | 15% (실질 진입 안 됨, threshold 0.65) |
| <0.5 | 8% (실질 진입 안 됨) |

threshold 0.65 가 진입 자체를 차단하므로 score 분기는 *형식상* 만 정의.

---

## 6. Convention Prerequisites

- ✅ Strategy v2 (BAR-45)
- ✅ BAR-41 to_entry_signal
- ✅ legacy ScalpingAnalysis 흡수 (BAR-40)

---

## 7. Implementation Outline (D1~D5)

1. D1 — scalping_consensus.py (provider injection + threshold)
2. D2 — test_scalping_consensus.py 8+
3. D3 — V1~V6
4. D4 — PR + Phase 1 종합 회고

---

## 8. Next

- Phase 2 진입 (BAR-52~55 NXT 통합)

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 plan — 옵션 B provider injection, threshold 0.65, 단타 ExitPlan |
