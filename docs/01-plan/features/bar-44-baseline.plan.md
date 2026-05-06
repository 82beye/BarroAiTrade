---
tags: [plan, feature/bar-44, status/in_progress, phase/0, area/strategy]
template: plan
version: 1.0
---

# BAR-44 회귀 베이스라인 측정 + 마스터 플랜 v2 Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-44
> **Phase**: 0 — **종료 게이트** (마지막 티켓)
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v1#Phase 0 — 기반 정비 (Week 1–2, 5 티켓: BAR-40~44)]]
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: Phase 0 → Phase 1 진입 게이트

---

## 1. Overview

### 1.1 Purpose

Phase 0 종료 게이트로서:
1. **4 전략 (F존/블루라인/수박/암호화폐) 베이스라인** 측정 → `docs/04-report/PHASE-0-baseline-2026-05.md`
2. **마스터 플랜 v2 발행** — BAR-51 번호 충돌 정정 + 누적된 명세 갱신 (BAR-40 §A1~A5, BAR-41 L1~L3, BAR-42 L1~L2, BAR-43 L1~L3) 일괄 통합
3. **Phase 0 종합 통계 보고** — 5 BAR (BAR-40~44) × PDCA 5 PR + 거버넌스 1 PR = **총 26 PR** 규모 회고

### 1.2 옵션 결정 (옵션 2 채택)

| 옵션 | 평가 |
|---|---|
| 옵션 1: 5년 OHLCV 캐시 복원 + 백테스트 | 시간 비용 매우 큼 (수 시간), 캐시 144MB worktree 외부 |
| **옵션 2: 합성 데이터 베이스라인 + 후속 정식 측정** | ⭐ 채택 — `backtester.py` 의 *합성 데이터 생성기* 활용. Phase 0 종료 *게이트* 의 정신("후속 회귀 비교의 *기준점*") 충족. 정식 5년 측정은 BAR-44b 또는 maintenance |
| 옵션 3: 마스터 플랜 v2 만 발행 후 BAR-44 정식 진행 | Phase 0 종료 지연, BAR-45 진입 블로킹 |

**옵션 2** 의 핵심 가치:
- Phase 0 종료 게이트의 *정의* 는 "후속 회귀 비교의 기준점 확립" — *합성* 데이터로도 *비교 기준* 자체는 정의 가능
- 후속 BAR-44b (정식 5년 백테스트) 는 OHLCV 캐시 통합·Postgres 마이그(BAR-56) 시점에 자연스럽게 진행
- BAR-45~ 진입 블로킹 회피, Phase 1 *동일자* 진입 가능

### 1.3 Background

- 기존 `backend/core/strategy/backtester.py` (852 LOC) 가 합성 데이터 생성기 포함
- OHLCV 캐시 144MB 는 `/Users/beye/workspace/ai-trade/data/ohlcv_cache/` 에 존재 (worktree 외부)
- 4 전략: `f_zone.py`, `blue_line.py`, `stock_strategy.py` (수박), `crypto_breakout.py`

### 1.4 Related Documents

- 마스터 플랜 v1: [[../MASTER-EXECUTION-PLAN-v1]]
- BAR-40~43 ✅ 완료
- 기존 백테스트 분석: `docs/01-plan/analysis/Individual-Stock-Analysis.md`, `KR-Market-Analysis-2026Q2.md` (참고)

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/core/strategy/backtester.py` 의 합성 데이터 생성기로 4 전략 베이스라인 측정 (250 거래일 = 약 1년 합성 데이터)
- [ ] `docs/04-report/PHASE-0-baseline-2026-05.md` 베이스라인 리포트 작성
  - 4 전략별 승률, 수익률, MDD, Sharpe, 거래 횟수
  - 데이터 형식: 표 + 회귀 비교용 *기준 임계값* (±5% 정의)
- [ ] **마스터 플랜 v2 발행** — `docs/01-plan/MASTER-EXECUTION-PLAN-v2.md`
  - BAR-51 번호 충돌 정정 → BAR-79 (백테스터 v2 확장) 로 재할당
  - L1~L9 누적 명세 갱신 통합 (zero-modification 정의·LOC 한도·extra="ignore"·fixture Singleton·PROM_FORCE_NOOP 등)
- [ ] **Phase 0 종합 회고** — `docs/04-report/PHASE-0-summary.md`
  - 26 PR 통계, 5 BAR Match Rate 합산, 후속 BAR 의존 해소 효과, Lessons 통합
- [ ] `tests/strategy/test_baseline.py` 신규 — 베이스라인 합성 측정 결과의 *재현성* 검증 (3+ 케이스, fixed seed)

### 2.2 Out of Scope

- ❌ **실제 5년 OHLCV 백테스트** — 후속 BAR-44b (Postgres 마이그·OHLCV 통합 후)
- ❌ NXT 야간 시뮬레이션 — Phase 2 BAR-53 후 백테스터 v2 (BAR-79)
- ❌ workforward 분석·슬리피지 모델 — BAR-79
- ❌ 라이브 모의투자 N주 검증 — Phase 1 BAR-45 do 후

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `backtester.run_backtest(strategy, ohlcv_data)` 4 전략 합성 데이터 베이스라인 측정 | High |
| FR-02 | 측정 결과 표: 승률·수익률·MDD·Sharpe·거래수 (4 전략 × 5 지표 = 20 셀) | High |
| FR-03 | 회귀 비교 *기준 임계값* (±5%) 정의 + 후속 PR 비교 매크로 명시 | High |
| FR-04 | Fixed seed (`random.seed(42)`) 로 재현성 보장 | High |
| FR-05 | **마스터 플랜 v2** 발행 — BAR-51 → BAR-79 재할당, L1~L9 통합 | High |
| FR-06 | **Phase 0 종합 회고** 보고서 — 26 PR / 5 BAR 통계 | Medium |
| FR-07 | `tests/strategy/test_baseline.py` 재현성 검증 (3+ 케이스) | Medium |

### 3.2 Non-Functional Requirements

| Category | 기준 |
|---|---|
| 성능 | 4 전략 × 250 거래일 합성 베이스라인 ≤ 30초 |
| 호환성 | BAR-40/41/42/43 회귀 무영향 |
| 재현성 | Fixed seed 시 동일 결과 |
| 커버리지 | `test_baseline.py` ≥ 80% |

---

## 4. Success Criteria

### 4.1 DoD

- [ ] `PHASE-0-baseline-2026-05.md` 발행 — 4 전략 5 지표 표
- [ ] 마스터 플랜 v2 머지
- [ ] Phase 0 종합 회고 보고서 머지
- [ ] BAR-40~43 회귀 무영향
- [ ] PR 셀프 리뷰 + 머지

### 4.2 베이스라인 표 형식

```
| Strategy        | 승률    | 수익률    | MDD     | Sharpe | 거래수 |
|-----------------|--------|----------|--------|--------|-------|
| FZoneStrategy   | XX.X%  | YY.Y%    | -ZZ.Z% | A.AA   | NNN   |
| BlueLineStrategy| ...    | ...      | ...    | ...    | ...   |
| StockStrategy   | ...    | ...      | ...    | ...    | ...   |
| CryptoBreakout  | ...    | ...      | ...    | ...    | ...   |
```

회귀 임계값: 후속 PR 의 4 전략 결과가 위 베이스라인의 ±5% 이내일 것.

---

## 5. Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| 합성 데이터 통계가 실측과 큰 차이 | *베이스라인 자체는 비교 기준점* 으로만 활용 (절대값 아님). 정식 5년은 BAR-44b |
| 4 전략 중 일부가 0 거래 (조건 불만족) | 합성 데이터 generator 의 변동성 파라미터 조정으로 거래 발생 보장 |
| 마스터 플랜 v2 발행 후 v1 wikilink 깨짐 | v1 보존 + v2 가 v1 supersede 명시 |
| BAR-51 재할당 시 master plan v1 기존 wikilink (`[[BAR-51]]`) 모호 | v2 의 BAR-51 = 서비스 복구 모니터링(기존 main), BAR-79 = 백테스터 v2 (마스터 v1 의 BAR-51) 명시 |
| 합성 데이터 백테스트 시간 ≤ 30초 미달성 | 거래일 수를 250 → 100 으로 축소, 재현성 보장 우선 |

---

## 6. Architecture Considerations

### 6.1 옵션 2 구조

```
[합성 데이터 generator (backtester.py 내부)]
   ↓ (Fixed seed 42)
   250 거래일 OHLCV synthetic
   ↓
   ├─ FZoneStrategy.run_backtest()
   ├─ BlueLineStrategy.run_backtest()
   ├─ StockStrategy.run_backtest()  (수박)
   └─ CryptoBreakoutStrategy.run_backtest()
       ↓
   각 전략 결과 (BacktestResult)
       ↓
   docs/04-report/PHASE-0-baseline-2026-05.md
```

### 6.2 마스터 플랜 v2 변경 항목

| 항목 | v1 | v2 |
|---|---|---|
| BAR-51 | 백테스터 v2 확장 | 🔁 BAR-79 로 재할당 |
| Plan §3.3 zero-modification 정의 | "코드 무수정" | "외부 동작 보존, 진입점 격리만" |
| `_adapter.py` LOC 한도 | ≤ 200 | ≤ 250 |
| Schema `extra` 정책 | `forbid` | `ignore` |
| metrics fixture | `importlib.reload` | Singleton (reload 제거) |
| fallback 검증 | 환경 종속 | `PROM_FORCE_NOOP=1` 권고 |
| BAR-44b (선택, 정식 5년) | (부재) | 신규 후순위 ticket |

---

## 7. Convention Prerequisites

- ✅ pytest 인프라 (BAR-41~43)
- ✅ `setup_logging()` (BAR-43)
- ❌ `tests/strategy/` 디렉터리 부재 → 본 티켓 시동
- ❌ 마스터 플랜 v2 부재 → 본 티켓 발행

---

## 8. Implementation Outline (D1~D10)

1. **D1 OHLCV 가용성 확인** — `/Users/beye/workspace/ai-trade/data/ohlcv_cache/` 존재 확인 (옵션 1 가능성 평가, 시간 비용으로 옵션 2 채택)
2. **D2 backtester 합성 데이터 모드 검증** — `python -c "from backend.core.strategy.backtester import ..."` 으로 import 무에러
3. **D3 베이스라인 측정 스크립트** — `scripts/run_baseline.py` (또는 inline) 4 전략 × 합성 250일
4. **D4 결과 기록** — `docs/04-report/PHASE-0-baseline-2026-05.md` (4 전략 × 5 지표 표)
5. **D5 `tests/strategy/test_baseline.py`** — Fixed seed 재현성 3+ 케이스
6. **D6 마스터 플랜 v2 발행** — `docs/01-plan/MASTER-EXECUTION-PLAN-v2.md` (v1 supersede 표기 + v1 보존)
7. **D7 Phase 0 종합 회고** — `docs/04-report/PHASE-0-summary.md` (26 PR, 5 BAR Match 평균, Lessons 통합)
8. **D8 V1~V6 검증** (design 에서 정의)
9. **D9 BAR-40~43 회귀 무영향 확인**
10. **D10 PR 생성** (라벨: `area:strategy` `phase:0` `priority:p0`)

---

## 9. Next Steps

1. [ ] Design (BAR-44 specifics)
2. [ ] Do (베이스라인 측정 + v2 + Phase 0 회고)
3. [ ] Analyze (재현성 + 회귀)
4. [ ] Report
5. [ ] **Phase 1 진입** — BAR-45 plan (Strategy v2 추상)

---

## 10. 비고

- 합성 데이터 베이스라인은 *비교 기준점* 이지 *절대 성과 지표* 가 아니다. 후속 BAR-44b (정식 5년) 가 절대 지표 책임.
- Phase 1 BAR-45 (Strategy v2 추상) 진입은 본 BAR-44 의 *베이스라인 표 머지* 후 즉시 가능.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — Phase 0 종료 게이트 (옵션 2 + v2 + 회고 통합) | beye (CTO-lead) |
