---
tags: [plan, feature/bar-45, status/in_progress, phase/1, area/strategy]
template: plan
version: 1.0
---

# BAR-45 Strategy v2 추상 + AnalysisContext Plan

> **Project**: BarroAiTrade
> **Feature**: BAR-45
> **Phase**: 1 (전략 엔진 통합) — 첫 티켓
> **Master Plan**: [[../MASTER-EXECUTION-PLAN-v2#Phase 1 (BAR-45~50, 6 티켓)]]
> **Author**: beye (CTO-lead)
> **Date**: 2026-05-06
> **Status**: In Progress
> **Gate**: BAR-46~50 (4대 전략 + ScalpingConsensus) 의 *모든* 의존

---

## 1. Overview

### 1.1 Purpose

현재 `backend/core/strategy/base.py` 의 `Strategy` ABC 는 `analyze() -> Optional[EntrySignal]` 단일 메서드만 가짐. 이를 **Strategy v2** 로 확장:

```python
class Strategy(ABC):
    STRATEGY_ID: str
    PARAMS_SCHEMA: type[BaseModel]      # Pydantic 파라미터 스키마

    @abstractmethod
    def analyze(self, ctx: AnalysisContext) -> Optional[EntrySignal]: ...

    @abstractmethod
    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan: ...

    @abstractmethod
    def position_size(self, signal: EntrySignal, account: Account) -> Decimal: ...

    def health_check(self) -> dict: ...   # 데이터 충분성, 파라미터 sanity
```

`AnalysisContext` 는 KRX/NXT 통합 시세, 호가, 거래원, 테마, 뉴스, 시간대를 보유한 *진입 의사결정 컨텍스트*.

### 1.2 Background

- 마스터 플랜 v2 §2 의 Phase 1 첫 티켓
- 마스터 플랜 v1 의 BAR-45 명세 그대로 (Phase 1 진입 시점에 v2 §4 명세 갱신 일관 적용 — LOC ≤ 250, fixture Singleton)
- 후속 BAR-46 (F존 v2 리팩터) ~ BAR-50 (ScalpingConsensus) 모두 본 v2 인터페이스 의존
- 현 4 전략 (FZoneStrategy / BlueLineStrategy / StockStrategy / CryptoBreakoutStrategy) 의 **backward compatibility** 유지 (BAR-44 베이스라인 ±5% 회귀 임계값 충족 의무)

### 1.3 Related Documents

- [[../MASTER-EXECUTION-PLAN-v2]]
- BAR-44 (선결, 완료): [[../../04-report/bar-44-baseline.report]]
- BAR-44 베이스라인: [[../../04-report/PHASE-0-baseline-2026-05]]
- 기존 Strategy ABC: `backend/core/strategy/base.py`

---

## 2. Scope

### 2.1 In Scope

- [ ] `backend/models/strategy.py` 신규 — `AnalysisContext`, `ExitPlan`, `Account`, `TakeProfitTier`, `StopLoss` Pydantic v2 모델
- [ ] `backend/core/strategy/base.py` 확장 — Strategy v2 ABC (`analyze` v2 시그니처 + `exit_plan` + `position_size` + `health_check`)
- [ ] **Backward compatibility** — 기존 4 전략의 `analyze(symbol, name, candles, market_type)` 호출이 *deprecation warning 후* 동작 유지
- [ ] 4 전략의 v2 `analyze(ctx)` 호환 어댑터 (default 구현 제공)
- [ ] `tests/strategy/test_base.py` 신규 — Strategy v2 ABC 인터페이스 검증 (5+ 케이스)
- [ ] `tests/strategy/test_strategy_models.py` 신규 — AnalysisContext/ExitPlan/Account 모델 검증 (5+ 케이스)
- [ ] `Makefile` `test-strategy` 타겟 (또는 `test` 통합)
- [ ] BAR-44 베이스라인 회귀 ±5% 검증

### 2.2 Out of Scope

- ❌ 기존 4 전략의 *내부 로직* 리팩터 — BAR-46/47/48/49 분담
- ❌ ScalpingConsensus 메타전략 — BAR-50
- ❌ NxtGateway 시세 컨텍스트 통합 — BAR-53 (AnalysisContext 의 NXT 필드는 placeholder)
- ❌ 테마 컨텍스트 — BAR-58/59 (placeholder)
- ❌ 백테스터 v2 (workforward·NXT 야간) — BAR-79 (재할당)
- ❌ ExitPlan 의 *분할 익절 엔진* — BAR-63

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | `AnalysisContext` 모델 — symbol/name/candles/market_type/orderbook/trading_session/theme_context/news_context | High |
| FR-02 | `ExitPlan` 모델 — `take_profits: list[TakeProfitTier]`, `stop_loss: StopLoss`, `time_exit: Optional[time]`, `breakeven_trigger: Optional[Decimal]` | High |
| FR-03 | `Account` 모델 — `balance: Decimal`, `available: Decimal`, `position_count: int`, `daily_pnl_pct: Decimal` | High |
| FR-04 | Strategy v2 ABC — `analyze(ctx)` / `exit_plan(position, ctx)` / `position_size(signal, account)` / `health_check()` | High |
| FR-05 | Backward compat — 기존 `analyze(symbol, name, candles, market_type)` 시그니처도 deprecation warning 후 동작 | High |
| FR-06 | 4 전략 default v2 호환 — 기존 analyze() 를 새 시그니처로 wrap | High |
| FR-07 | `health_check()` default 구현 — 캔들 길이·필수 파라미터 검사 | Medium |

### 3.2 Non-Functional Requirements

| Category | 기준 |
|---|---|
| 호환성 | BAR-44 베이스라인 회귀 ±5% 이내 (4 전략 모두) |
| 성능 | `Strategy.analyze(ctx)` 호출 ≤ 50ms (250 거래일 데이터) |
| Decimal 안전 | ExitPlan / Account / position_size 자금흐름 영역 — Decimal 의무 |
| 테스트 커버리지 | `models/strategy.py` + `core/strategy/base.py` ≥ 80% |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] `backend/models/strategy.py` 신규 — 5 Pydantic 모델
- [ ] `backend/core/strategy/base.py` 확장 — Strategy v2 ABC
- [ ] 4 전략 (f_zone/blue_line/stock/crypto_breakout) 모두 v2 호환 (deprecated 시그니처 + 새 시그니처 동시 지원)
- [ ] 10+ 테스트 통과 (test_base 5+ + test_strategy_models 5+)
- [ ] BAR-44 베이스라인 회귀 ±5% 이내
- [ ] BAR-40~43 회귀 무영향
- [ ] 라인 커버리지 ≥ 80%

### 4.2 핵심 시나리오

| # | 케이스 |
|---|--------|
| C1 | `Strategy()` 인스턴스화 (구체 클래스) — analyze 만 구현 → backward compat 동작 |
| C2 | `Strategy.analyze(ctx)` 신규 시그니처 — AnalysisContext 받아 EntrySignal 반환 |
| C3 | `Strategy.exit_plan(position, ctx)` — ExitPlan(분할 TP+SL) 반환 |
| C4 | `Strategy.position_size(signal, account)` — Decimal 반환, 자금흐름 안전 |
| C5 | `Strategy.health_check()` — 캔들 길이·파라미터 sanity 검사 |
| C6 | AnalysisContext 모델 검증 — 누락 필드 None 허용, candles 길이 ≥ 1 |
| C7 | ExitPlan 모델 — TakeProfitTier list 정렬·합계 검증 |
| C8 | Account 모델 — balance/available Decimal 음수 거부 |
| C9 | 4 전략 모두 v2 호환 — 동일 OHLCV 입력 → 동일 EntrySignal (deprecated 경로) |
| C10 | BAR-44 베이스라인 회귀 — 동일 seed → 동일 결과 (±0.001%) |

### 4.3 Quality Criteria

- [ ] `base.py` ≤ 250 LOC (v2 §4.2 LOC 한도)
- [ ] `models/strategy.py` ≤ 250 LOC
- [ ] 모든 신규 모델 Pydantic v2 (`model_config`, `model_validate`)
- [ ] 자금흐름 필드 (balance/available/position_size return/take_profit price 등) **Decimal**
- [ ] Test fixture Singleton (v2 §4.4 — `importlib.reload` 회피)

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| 기존 4 전략 회귀 (BAR-44 베이스라인 -5% 초과) | High | Medium | backward compat layer + 회귀 테스트 매 PR. 초과 시 PR 차단 |
| `analyze()` deprecation warning 노이즈 | Low | High | `warnings.warn(DeprecationWarning)` + `@deprecated` 데코레이터, 후속 BAR-46~50 에서 단계적 제거 |
| AnalysisContext 의 NXT/테마 필드가 BAR-52/58 미구현 상태 | Medium | High | placeholder `Optional[...] = None` (BAR-42 settings 패턴 일관) |
| `EntrySignal.price: float` vs ExitPlan price `Decimal` 불일치 | Medium | High | ExitPlan 내부 Decimal, 출력 시 EntrySignal 호환 위해 float 캐스팅 (BAR-41 어댑터 패턴 일관) |
| `position_size` Decimal 반환 회귀 (기존 호출처 float 가정) | Medium | Medium | 기존 호출처 grep + 명시적 type 체크 |
| 4 전략 v2 어댑터 default 구현이 부정확 | Medium | Medium | 각 전략별 단위 테스트 (BAR-46~49 도) — 본 BAR-45 는 *시그니처* 만 |

---

## 6. Architecture Considerations

### 6.1 Project Level — Enterprise

### 6.2 Backward Compatibility 전략

| 옵션 | 장점 | 단점 | 채택 |
|---|---|---|:---:|
| A. 새 메서드 `analyze_v2(ctx)` 추가, 기존 `analyze()` 보존 | 명시적 분리, 회귀 위험 0 | API 비대화, BAR-46~50 모두 `analyze_v2` 호출 변경 필요 | — |
| B. **`analyze(ctx)` 단일 시그니처, 기존 4-arg 호출은 *동적 dispatch* (DeprecationWarning)** | API 단일, 점진 마이그 | 동적 dispatch 복잡 | ⭐ |
| C. 기존 시그니처 강제 변경, 모든 호출처 즉시 수정 | 단순 | 회귀 위험 높음, BAR-44 베이스라인 위반 가능 | — |

→ **B 채택**. `Strategy.analyze` 가 `(ctx_or_symbol, ...)` 첫 인자를 검사해 *dispatch*:

```python
def analyze(self, *args, **kwargs) -> Optional[EntrySignal]:
    if args and isinstance(args[0], AnalysisContext):
        return self._analyze_v2(args[0])
    # Legacy 4-arg
    warnings.warn("Strategy.analyze(symbol, name, candles, market_type) is deprecated; use AnalysisContext", DeprecationWarning, stacklevel=2)
    ctx = AnalysisContext.from_legacy(*args, **kwargs)
    return self._analyze_v2(ctx)
```

각 구체 전략은 `_analyze_v2(ctx)` 만 override. 기존 `analyze()` 본문이 있으면 도구로 자동 변환 (수동).

### 6.3 모델 의존

```
backend/models/
├── market.py       (기존: OHLCV, MarketType, Ticker, OrderBook)
├── signal.py       (기존: EntrySignal, ExitSignal)
├── position.py     (기존: Position)
├── risk.py         (기존: RiskLimits)
└── strategy.py     ← 🆕 신규
    ├── AnalysisContext     — 진입 의사결정 컨텍스트
    ├── ExitPlan            — 분할 TP + SL + time_exit + breakeven
    ├── TakeProfitTier      — (price, qty_pct, condition)
    ├── StopLoss            — fixed + trailing
    └── Account             — balance/available/position_count
```

### 6.4 AnalysisContext 필드 매트릭스

| 필드 | 타입 | 출처 | 본 BAR-45 |
|---|---|---|:---:|
| `symbol` | `str` | 호출자 | ✅ 필수 |
| `name` | `str \| None` | 호출자 | ✅ |
| `candles` | `list[OHLCV]` | 시세 수집 | ✅ 필수 |
| `market_type` | `MarketType` | 호출자 | ✅ |
| `orderbook` | `Optional[OrderBook]` | KiwoomGateway | ✅ Optional |
| `trading_session` | `Optional[TradingSession]` | BAR-52 | ⏳ placeholder |
| `theme_context` | `Optional[ThemeContext]` | BAR-58/59 | ⏳ placeholder |
| `news_context` | `Optional[NewsContext]` | BAR-57 | ⏳ placeholder |
| `composite_orderbook` | `Optional[CompositeOrderBook]` | BAR-54 | ⏳ placeholder |

→ placeholder 5건은 *type forward reference* 또는 `Any | None` 으로 표시. BAR-52/53/57/58 에서 정식 타입 주입.

---

## 7. Convention Prerequisites

### 7.1 기존 컨벤션

- ✅ Pydantic v2 (`model_config`, `Field`, `SecretStr`)
- ✅ `from __future__ import annotations`
- ✅ pytest 인프라 (BAR-41~44)
- ✅ Decimal 자금흐름 의무 (마스터 플랜 §0)

### 7.2 본 티켓에서 정의할 컨벤션

| 항목 | 결정 |
|---|---|
| Strategy v2 인터페이스 docstring | 한국어, 각 메서드 *언제 호출되는지* 명시 |
| `analyze` 시그니처 dispatch | §6.2 옵션 B (동적, DeprecationWarning) |
| ExitPlan 단위 | take_profit_pct 는 *수익률* (0.03 = +3%), stop_loss_pct 는 *손실률* (-0.02 = -2%) |
| position_size Decimal 정밀도 | 6 자리 (KRX 주식: 1주 단위, 코인: 0.000001 BTC) |
| forward reference 표기 | placeholder 5건 모두 `# TODO(BAR-XX): ...` 주석 + `Any \| None` |

---

## 8. Implementation Outline (D1~D10)

> Design 에서 상세화.

1. **D1** — 기존 `analyze()` 호출처 grep (`from backend.core.strategy.base import Strategy`) 으로 회귀 위험도 측정
2. **D2** — `backend/models/strategy.py` 신규 — 5 Pydantic 모델 (AnalysisContext / ExitPlan / TakeProfitTier / StopLoss / Account)
3. **D3** — `backend/core/strategy/base.py` 확장 — Strategy v2 ABC + dispatch + DeprecationWarning
4. **D4** — `Position` 모델에 ExitPlan 호환 필드 확인 (이미 있다면 활용, 없으면 forward ref)
5. **D5** — 4 전략 default v2 어댑터 — 각 전략의 `_analyze_v2` 메서드 추가 (기존 본문 호출)
6. **D6** — `tests/strategy/test_base.py` (5+ 케이스, C1~C5)
7. **D7** — `tests/strategy/test_strategy_models.py` (5+ 케이스, C6~C8)
8. **D8** — `Makefile` `test-strategy` 또는 `test` 통합
9. **D9** — V1~V6 검증 + BAR-44 베이스라인 회귀 (`make baseline` 결과 비교)
10. **D10** — PR 생성 (라벨: `area:strategy` `phase:1` `priority:p0`)

---

## 9. Next Steps

1. [ ] Design 문서 작성
2. [ ] Do 구현
3. [ ] Analyze (gap-detector)
4. [ ] Report
5. [ ] **BAR-46 진입** — F존 v2 리팩터 (본 BAR-45 인터페이스 활용)

---

## 10. 비고

- 자금흐름 영역 진입 (Decimal 의무) — 라벨 `area:strategy` (`area:money` 는 OrderExecutor 통합 시점부터)
- 본 PR 은 *시그니처 + 모델* 만, 4 전략 *내부 로직* 변경은 BAR-46~49
- 마스터 플랜 v2 §4 명세 일관 적용 (LOC ≤ 250, fixture Singleton)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-06 | 초기 plan — Phase 1 첫 티켓, 옵션 B (동적 dispatch + DeprecationWarning) | beye (CTO-lead) |
