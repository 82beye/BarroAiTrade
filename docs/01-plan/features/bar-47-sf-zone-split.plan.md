---
tags: [plan, feature/bar-47, status/in_progress, phase/1, area/strategy]
template: plan
version: 1.0
---

# BAR-47 SF존 별도 클래스 분리 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-47
> **Phase**: 1 — 세 번째 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 1]]
> **Date**: 2026-05-06
> **Status**: In Progress

---

## 1. Overview

### 1.1 Purpose

현재 `FZoneStrategy._analyze_v2` 가 F존 + SF존 양쪽 신호를 *동일 클래스* 에서 분기 (signal_type 만 다름). BAR-47 에서 **SFZoneStrategy 를 별도 클래스로 분리**:

- 강도 가중치 명시화 (sf_impulse_min_gain_pct=5%, sf_volume_ratio=3.0)
- ExitPlan 더 공격적: TP3 (+7%) 추가, SL -1.5% 더 타이트
- position_size 더 큰 비중 (강한 신호이므로 score≥0.7 → 35%)
- BAR-44 베이스라인은 *F존 6 거래만* — SF존은 0 거래라 회귀 영향 0

### 1.2 Background

- 마스터 플랜 v2 §2 Phase 1 세 번째 티켓
- BAR-46 의 F존 v2 패턴 그대로 활용
- F존 본문에 이미 SF존 분기 코드 (`analysis.is_sf_zone`) 존재 → 추출 + 단독 클래스화

### 1.3 Related

- BAR-46 (선결, 완료): [[../../04-report/bar-46-f-zone-v2.report]]
- 기존 F존 본문: `backend/core/strategy/f_zone.py:_score_and_classify`

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/core/strategy/sf_zone.py` 신규 — SFZoneStrategy 클래스
- [ ] FZoneStrategy 의 `_score_and_classify` 안에서 SF존 분기 *유지* (F존 단독 발행은 이미 가능)
- [ ] SFZoneStrategy 는 *오직 SF존 신호만* 발행 (signal_type="sf_zone")
- [ ] SF존 ExitPlan: TP1=+3% (33%), TP2=+5% (33%), TP3=+7% (34%), SL=-1.5%, time_exit=14:50
- [ ] SF존 position_size: score≥0.7 → 35% / 0.5~0.7 → 25% / <0.5 → 10%
- [ ] `tests/strategy/test_sf_zone.py` 6+
- [ ] BAR-44 베이스라인 회귀 (F존 6 거래 그대로 유지, SF존 0 거래)

### 2.2 Out of Scope

- ❌ FZoneStrategy 의 SF존 분기 *제거* — 본 티켓에서 보존 (회귀 위험 회피). 후속 maintenance 시점에 정리
- ❌ 골드존/38스윙 — BAR-48/49

---

## 3. Requirements

### 3.1 Functional

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | SFZoneStrategy 클래스 (Strategy v2 상속) | High |
| FR-02 | F존과 SharedFZoneAnalyzer (helper) 공유 — 코드 중복 회피 | High |
| FR-03 | _analyze_v2: F존 분석 후 is_sf_zone=True 만 EntrySignal 반환 | High |
| FR-04 | exit_plan: TP3 추가 (+7%), SL -1.5% (더 타이트), TP qty_pct 합계 1.0 | High |
| FR-05 | position_size: 35%/25%/10% 분기 | High |
| FR-06 | health_check: SF존 강도 임계값 sanity (sf_impulse_min_gain_pct ≥ 0.05) | Medium |
| FR-07 | BAR-44 베이스라인 회귀 (F존 6 거래 보존) | High |

### 3.2 Non-Functional

| Category | 기준 |
|---|---|
| 회귀 | BAR-44 베이스라인 4 전략 모두 ±5% |
| 코드 중복 | F존 분석 코드 재사용 (helper 또는 import) |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] SFZoneStrategy 신규
- [ ] 6+ 테스트
- [ ] BAR-44 베이스라인 회귀
- [ ] BAR-40~46 회귀 무영향
- [ ] 라인 커버리지 ≥80%

### 4.2 6+ 테스트

| # | 케이스 |
|---|--------|
| C1 | SFZoneStrategy import + Strategy 상속 |
| C2 | _analyze_v2 — is_sf_zone=False 시 None |
| C3 | _analyze_v2 — is_sf_zone=True 시 EntrySignal (signal_type="sf_zone") |
| C4 | exit_plan: TP1/TP2/TP3 합계 1.0, SL=-1.5%, time_exit=14:50 |
| C5 | position_size: score=0.85 → 35%, score=0.6 → 25%, score=0.4 → 10% |
| C6 | health_check: ready=True (default params) |
| C7 | BAR-44 베이스라인 회귀 (F존 6 거래 변동 0) |

---

## 5. Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| F존과 SF존 코드 중복 | `_score_and_classify` 만 분기 다름 — helper 함수로 추출 또는 F존 본문 재사용 import |
| 베이스라인 SF존 0 거래 → 검증 부재 | 보강 단위 테스트로 forced fixture (is_sf_zone=True 모킹) |
| ExitPlan TP qty_pct 합계 ≠ 1.0 | 0.33 + 0.33 + 0.34 = 1.0 정확 |

---

## 6. Architecture Considerations

### 6.1 코드 공유 옵션

| 옵션 | 평가 |
|---|---|
| A. SFZone 이 FZone 인스턴스 보유 + delegate | 단순, 의존 명확 |
| B. SharedFZoneAnalyzer mixin/helper | 더 깔끔하나 리팩터 폭 큼 |
| C. SFZone 이 FZone 상속 + override _score_and_classify | OOP 자연 |

→ **A 채택** (변경 최소). SFZoneStrategy 가 내부에 FZoneStrategy 인스턴스 보유, `_analyze_v2(ctx)` 호출 후 *signal_type==sf_zone* 만 통과시킴.

### 6.2 ExitPlan 매트릭스 (SF존)

| 항목 | F존 | **SF존** |
|---|---|---|
| TP1 | avg×1.03 (50%) | avg×1.03 (33%) |
| TP2 | avg×1.05 (50%) | avg×1.05 (33%) |
| TP3 | — | avg×1.07 (34%) |
| SL | -2% | **-1.5%** (더 타이트) |
| time_exit | 14:50 | 14:50 |
| breakeven | +1.5% | **+1.0%** (조기) |

### 6.3 position_size 매트릭스 (SF존)

| score | F존 | **SF존** |
|---|---|---|
| ≥0.7 | 30% | **35%** |
| 0.5~0.7 | 20% | **25%** |
| <0.5 | 10% | 10% |

---

## 7. Convention Prerequisites

- ✅ Strategy v2 (BAR-45) + F존 v2 (BAR-46) 패턴
- ✅ Decimal 자금흐름

---

## 8. Implementation Outline (D1~D7)

1. D1 — sf_zone.py 신규 (SFZoneStrategy)
2. D2 — _analyze_v2: FZoneStrategy delegate, is_sf_zone 필터
3. D3 — exit_plan override (§6.2)
4. D4 — position_size override (§6.3)
5. D5 — health_check override
6. D6 — test_sf_zone.py 6+
7. D7 — V1~V6 + PR

---

## 9. Next

- BAR-48 골드존 신규 (BAR-46/47 패턴)

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 plan — SF존 별도 클래스, 옵션 A delegate |
