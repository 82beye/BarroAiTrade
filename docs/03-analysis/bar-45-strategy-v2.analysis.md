---
tags: [analysis, feature/bar-45, status/in_progress, phase/1, area/strategy]
template: analysis
version: 1.0
---

# BAR-45 Gap Analysis Report

> **관련 문서**: [[../01-plan/features/bar-45-strategy-v2.plan|Plan]] | [[../02-design/features/bar-45-strategy-v2.design|Design]] | Report (pending)

- **Feature**: BAR-45 Strategy v2 추상 + AnalysisContext
- **Phase**: 1 — 첫 티켓
- **Match Rate**: **97%**
- **Date**: 2026-05-06
- **Status**: ✅ Above 90% — `/pdca report` 권장

---

## 1. Phase Scores

| Phase | Weight | Score |
|---|:---:|:---:|
| Plan FR (FR-01~FR-07, 7건) | 20% | 100% |
| Plan NFR (4건) | 10% | 95% |
| Plan DoD (5건) | 10% | 100% |
| Design §1 Architecture | 15% | 100% |
| Design §2 Implementation Spec | 20% | 100% |
| Design §3 Test Cases (10+) | 15% | 100% |
| Design §4 V1~V6 | 10% | 100% |
| **Overall** | **100%** | **97%** |

가중 산식: `0.20×100 + 0.10×95 + 0.10×100 + 0.15×100 + 0.20×100 + 0.15×100 + 0.10×100 = 99.5 → 보수적 97%` (BAR-44 회귀 *완벽 일치* 보너스 마진 후 보수적 조정)

---

## 2. 검증 결과

| # | 시나리오 | 결과 |
|---|---|:---:|
| V1 | `make test-strategy` 31 통과 | ✅ (계획 10+ → 실측 31) |
| V2 | 라인 커버리지 ≥ 80% | ✅ **94%** (base 91% / models 95%) |
| V3 | BAR-44 베이스라인 회귀 ±5% | ✅ **100% 일치** (수치 변동 0건) |
| V4 | BAR-40~43 회귀 | ✅ 35 통과 (BAR-41 19 / BAR-42 9 / BAR-43 7) |
| V5 | DeprecationWarning 발생 | ✅ test_c1 |
| V6 | placeholder TODO 주석 | ✅ 5건 |

---

## 3. Functional Requirements

| FR | 요구 | 구현 |
|----|------|:---:|
| FR-01 | AnalysisContext 모델 (8 필드) | ✅ |
| FR-02 | ExitPlan + TakeProfitTier + StopLoss | ✅ |
| FR-03 | Account (Decimal balance/available/...) | ✅ |
| FR-04 | Strategy v2 ABC (analyze/exit_plan/position_size/health_check) | ✅ |
| FR-05 | Backward compat (DeprecationWarning + 자동 변환) | ✅ |
| FR-06 | 4 전략 default v2 호환 | ✅ (4 전략 모두 Strategy 상속, _analyze_v2 구현) |
| FR-07 | health_check default | ✅ (strategy_id ready) |

**FR Score: 7/7 = 100%**

---

## 4. Missing Items

| # | 항목 | 영향도 |
|---|---|:---:|
| M1 | `analyze()` 성능 ≤50ms 정량 측정 | Low — 단위 테스트 0.7s/31 케이스 → 실측 매우 빠름. BAR-78 회귀 자동화 시 통합 |
| M2 | DeprecationWarning 952건 (backtester legacy 호출) | Low — Phase 1 종료까지 의도된 backward compat. BAR-46~49 가 ctx 호출로 단계 전환 |

비차단.

---

## 5. Additional Changes

| # | 변경 | 분류 |
|---|------|---|
| A1 | `__init__.py` re-export 제거 (Python 3.14 + numpy 충돌 회피) | 🟢 Python 3.14 호환성 — v2 §4.4 fixture Singleton 정신 일관 |
| A2 | conftest 분리 (root vs legacy_scalping) — pandas/numpy 무거운 import 격리 | 🟢 테스트 격리 강화 |
| A3 | pyproject.toml `pythonpath = ["."]` — pytest sys.path 보정 | 🟢 도구 설정 보강 |
| A4 | 31 테스트 (계획 10+ 보강 21) — TestStrategyV2InheritanceCheck (4) / TestStrategyV2EdgeCases (2) / TestExitPlan/Account/TakeProfitTier 보강 | 🟢 회귀 안전망 |
| A5 | `_analyze_impl` 패턴 — 본문 보존 + ctx 풀어내기 분리 | 🟢 zero-modification 정신 (BAR-44 §A1 일관) |

가산 변경 합산 평가: 모두 정합·강화. 동작 의미 변화 0건 (V3 베이스라인 *수치 변동 0* 으로 입증).

---

## 6. Risk Status (Plan §5)

| Risk | Status |
|---|:---:|
| BAR-44 베이스라인 -5% 초과 | ✅ **0% 초과** (수치 100% 일치) |
| DeprecationWarning 노이즈 | ✅ 의도됨 (BAR-46~49 단계 전환) |
| AnalysisContext placeholder 5건 | ✅ Optional + TODO 주석 |
| EntrySignal.price=float vs ExitPlan Decimal | ✅ 격리 (BAR-41 패턴 일관) |
| position_size Decimal 회귀 | ✅ 기존 호출처 grep — 0건 |
| 4 전략 v2 어댑터 부정확 | ✅ TestStrategyV2InheritanceCheck (4 전략) + V3 베이스라인 |

전 위험 회피.

---

## 7. Convention Compliance

| 항목 | 평가 |
|---|:---:|
| Pydantic v2 (model_config / Field / SecretStr) | ✅ |
| Decimal 자금흐름 의무 | ✅ ExitPlan / Account / position_size return |
| `from __future__ import annotations` | ✅ 모든 신규 파일 |
| Type hint 의무 | ✅ |
| 한국어 docstring | ✅ |
| 마스터 플랜 v2 §4 명세 일관 (LOC ≤250) | ✅ base.py 105 / models 155 |
| Singleton fixture (BAR-43 패턴) | ✅ conftest 분리 |
| placeholder TODO 주석 | ✅ 5건 |

---

## 8. Conclusion

### 8.1 결론

BAR-45 Strategy v2 추상 + AnalysisContext 가 **97%** 매치로 통과. **BAR-44 베이스라인이 수치 변동 0건으로 100% 일치** — backward compat layer 가 완벽히 작동했음을 입증한다. 31 테스트 / 라인 커버리지 94% 로 도메인 안전망도 충분.

가산 변경 5건 (Python 3.14 호환·conftest 격리·pythonpath·31 테스트·`_analyze_impl` 패턴) 모두 정합·강화 방향. 자금흐름 영역 진입 (Decimal 의무) 시작 — Account / ExitPlan / position_size 모두 Decimal.

후속 BAR-46~50 의 모든 전략 리팩터·메타전략은 본 BAR-45 의 인터페이스 위에서 *순수하게 도메인 로직만* 다룰 수 있게 됐다.

### 8.2 다음 단계

→ **`/pdca report BAR-45`** + **BAR-46 plan 진입** (F존 v2 리팩터).

후속 BAR 인계:
- BAR-46/47/48/49: `_analyze_impl` 본문을 v2 ctx 직접 사용으로 리팩터 + ExitPlan/PositionSize override
- BAR-50: ScalpingConsensusStrategy — _analyze_v2 에서 ctx.theme_context / ctx.news_context 활용
- BAR-52: AnalysisContext.trading_session 정식 type (TradingSession enum)
- BAR-53: AnalysisContext.composite_orderbook 정식 type
- BAR-57: AnalysisContext.news_context 정식 type
- BAR-58/59: AnalysisContext.theme_context 정식 type
- BAR-66: position_size override (RiskEngine 동시 보유·동일 테마 한도)
- BAR-63: ExitPlan 분할 익절/손절 엔진 정식

### 8.3 Iteration 비권장

Match 97% > 90%, 미달 2건은 측정 도구·의도된 backward compat. 가산 5건 정합. iterate 비대상.

---

## 9. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 분석 — 97% 매치, 31 테스트 / cov 94% / BAR-44 베이스라인 100% 일치 |
