---
tags: [design, feature/bar-45, status/in_progress, phase/1, area/strategy]
template: design
version: 1.0
---

# BAR-45 Strategy v2 추상 + AnalysisContext Design Document

> **관련 문서**: [[../../01-plan/features/bar-45-strategy-v2.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Summary**: Strategy v2 ABC + 5 Pydantic 모델. Backward compat 옵션 B (동적 dispatch + DeprecationWarning). placeholder 5건 forward ref. 10+ 테스트 + V1~V6
>
> **Project**: BarroAiTrade
> **Feature**: BAR-45
> **Phase**: 1
> **Date**: 2026-05-06
> **Status**: Draft

---

## 1. Architecture

### 1.1 흐름

```
호출자
   ↓
ctx = AnalysisContext(symbol, name, candles, market_type, ...)
   ↓
strategy.analyze(ctx)  ← Strategy v2 진입
   ↓
EntrySignal | None

[진입 후 포지션 보유 시]
   ↓
exit_plan = strategy.exit_plan(position, ctx)
   ↓
ExitPlan(take_profits=[(price, qty_pct), ...], stop_loss=..., time_exit=14:50, breakeven_trigger=+0.015)

[진입 직전]
   ↓
size = strategy.position_size(signal, account)
   ↓
Decimal (KRX 1주 단위 quantize)
```

### 1.2 Module Layout

```
backend/models/strategy.py        ← 🆕 신규 (~180 LOC)
├── AnalysisContext               (Pydantic v2)
├── ExitPlan, TakeProfitTier, StopLoss
├── Account
└── (placeholder forward refs)

backend/core/strategy/base.py     ← 확장 (60 → ~150 LOC)
└── Strategy ABC v2 (analyze + exit_plan + position_size + health_check + dispatch)

backend/core/strategy/{f_zone,blue_line,stock_strategy,crypto_breakout}.py
                                  ← _analyze_v2 default 어댑터 추가 (각 +20 LOC)

backend/tests/strategy/
├── test_base.py                  ← 🆕 5+ 케이스 (Strategy v2 ABC)
├── test_strategy_models.py       ← 🆕 5+ 케이스 (모델)
└── test_baseline.py              (BAR-44, 회귀 검증)
```

---

## 2. Implementation Spec

### 2.1 `backend/models/strategy.py` 신규

```python
"""
BAR-45: Strategy v2 의 입출력 Pydantic v2 모델.

자금흐름 영역 — 가격·수량·잔고는 모두 Decimal.
EntrySignal.price 는 float 유지 (BAR-41 어댑터 호환). 본 모델은 *내부* Decimal.

Reference:
- Plan: docs/01-plan/features/bar-45-strategy-v2.plan.md
- Design: 본 문서 §2.1
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.market import MarketType, OHLCV, OrderBook
from backend.models.position import Position


# === AnalysisContext ===

class AnalysisContext(BaseModel):
    """진입 의사결정 컨텍스트. KRX/NXT 통합 시세, 호가, 테마, 뉴스, 시간대."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    symbol: str = Field(..., min_length=1)
    name: str | None = None
    candles: list[OHLCV] = Field(..., min_length=1)
    market_type: MarketType
    timestamp: datetime = Field(default_factory=lambda: datetime.now())

    # 선택 필드 (Phase 0 이후 BAR 들이 채움)
    orderbook: Optional[OrderBook] = None
    trading_session: Any = None        # TODO(BAR-52): TradingSession enum
    composite_orderbook: Any = None    # TODO(BAR-54): CompositeOrderBook
    theme_context: Any = None          # TODO(BAR-58/59): ThemeContext
    news_context: Any = None           # TODO(BAR-57): NewsContext

    @classmethod
    def from_legacy(
        cls,
        symbol: str,
        name: str,
        candles: list[OHLCV],
        market_type: MarketType,
    ) -> "AnalysisContext":
        """Backward compat — Strategy.analyze(symbol, name, candles, market_type) 호출 변환."""
        return cls(symbol=symbol, name=name, candles=candles, market_type=market_type)


# === ExitPlan ===

class TakeProfitTier(BaseModel):
    """분할 익절 단계 (price, qty_pct, condition)."""
    model_config = ConfigDict(frozen=True)

    price: Decimal = Field(..., gt=0)
    qty_pct: Decimal = Field(..., gt=0, le=1)   # 0.5 = 50%
    condition: str = ""                          # 예: "ATR x 1.5"

    @field_validator("price", "qty_pct", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))


class StopLoss(BaseModel):
    """고정 + 트레일링 손절."""
    model_config = ConfigDict(frozen=True)

    fixed_pct: Decimal = Field(..., lt=0)        # -0.02 = -2%
    trailing_pct: Optional[Decimal] = None       # 동적 손절 (선택)

    @field_validator("fixed_pct", "trailing_pct", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class ExitPlan(BaseModel):
    """청산 계획 — 분할 익절 + 손절 + 시간 청산 + 브레이크이븐."""
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    take_profits: list[TakeProfitTier] = Field(default_factory=list)
    stop_loss: StopLoss
    time_exit: Optional[dtime] = None            # 예: 14:50 강제 청산
    breakeven_trigger: Optional[Decimal] = None  # 예: +0.015 도달 시 SL 을 +0.005 로 이동

    @field_validator("take_profits")
    @classmethod
    def _qty_sum_le_one(cls, v: list[TakeProfitTier]) -> list[TakeProfitTier]:
        total = sum((t.qty_pct for t in v), Decimal(0))
        if total > Decimal("1.0001"):  # 부동소수점 오차 허용
            raise ValueError(f"take_profits qty_pct 합계 {total} > 1.0")
        return v


# === Account ===

class Account(BaseModel):
    """포지션 사이징 입력 — 잔고·일일 PnL·현재 보유 수."""
    model_config = ConfigDict(frozen=True)

    balance: Decimal = Field(..., ge=0)
    available: Decimal = Field(..., ge=0)
    position_count: int = Field(..., ge=0)
    daily_pnl_pct: Decimal = Decimal(0)           # -0.03 = -3% (Kill Switch 발동 임계)

    @field_validator("balance", "available", "daily_pnl_pct", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))
```

### 2.2 `backend/core/strategy/base.py` 확장

```python
"""
Strategy v2 ABC — analyze + exit_plan + position_size + health_check.

Reference:
- Plan: docs/01-plan/features/bar-45-strategy-v2.plan.md
- Design: docs/02-design/features/bar-45-strategy-v2.design.md
- BAR-44 회귀 임계값: 베이스라인 ±5%
"""
from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import List, Optional

from backend.models.market import OHLCV, MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext, ExitPlan, Account, StopLoss


class Strategy(ABC):
    """매매 전략 추상 기반 클래스 (v2)."""

    STRATEGY_ID: str = ""

    # === v2 필수 인터페이스 ===

    def analyze(self, *args, **kwargs) -> Optional[EntrySignal]:
        """진입 신호 분석 — v2 시그니처 + backward compat dispatch.

        v2 사용:
            strategy.analyze(ctx: AnalysisContext) -> EntrySignal | None

        Legacy (deprecated):
            strategy.analyze(symbol, name, candles, market_type) -> EntrySignal | None
        """
        if args and isinstance(args[0], AnalysisContext):
            return self._analyze_v2(args[0])

        # Legacy 4-arg dispatch
        warnings.warn(
            "Strategy.analyze(symbol, name, candles, market_type) is deprecated; "
            "use AnalysisContext (BAR-45). Will be removed in Phase 1 종료.",
            DeprecationWarning,
            stacklevel=2,
        )
        ctx = AnalysisContext.from_legacy(*args, **kwargs)
        return self._analyze_v2(ctx)

    @abstractmethod
    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """v2 진입 신호 분석 — 구체 클래스가 override."""
        ...

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """청산 계획 — 기본은 -2% SL, BAR-46~49 가 override."""
        return ExitPlan(
            take_profits=[],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """포지션 사이징 — 기본은 자산의 30% / 가격, BAR-66 RiskEngine 정책 통합 시 override."""
        if account.available <= 0:
            return Decimal(0)
        max_invest = account.available * Decimal("0.3")
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        # KRX 1주 단위 quantize
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict:
        """전략 상태 점검 — 데이터 충분성·파라미터 sanity. 후속 BAR override 가능."""
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": bool(self.STRATEGY_ID),
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy_id={self.STRATEGY_ID!r})"
```

### 2.3 4 전략 backward compat 어댑터

각 전략의 기존 `analyze(symbol, name, candles, market_type)` 본문을 `_analyze_v2(ctx)` 로 *이름만* 변경 + ctx 인자에서 추출:

```python
# backend/core/strategy/f_zone.py (예)
class FZoneStrategy(Strategy):
    STRATEGY_ID = "f_zone_v1"

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        # 기존 analyze 본문 그대로 — symbol/name/candles/market_type 만 ctx 에서 가져옴
        symbol = ctx.symbol
        name = ctx.name or symbol
        candles = ctx.candles
        market_type = ctx.market_type
        # ... (기존 본문) ...
```

각 전략 +20 LOC (이름 변경 + ctx 풀어내기).

### 2.4 Forward Reference 주석 패턴

placeholder 5건 (`trading_session` / `composite_orderbook` / `theme_context` / `news_context` / etc.):

```python
trading_session: Any = None  # TODO(BAR-52): TradingSession enum
```

후속 BAR 가 *type 만* 갱신 — 본 BAR-45 는 *형태만* 정의.

---

## 3. Test Cases (10+ 케이스)

### 3.1 `tests/strategy/test_base.py` (5+)

```python
import warnings
from decimal import Decimal

import pytest
from backend.core.strategy.base import Strategy
from backend.models.market import MarketType
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext, Account


class _DummyStrategy(Strategy):
    """테스트용 더미 — _analyze_v2 만 구현."""
    STRATEGY_ID = "dummy_v1"

    def _analyze_v2(self, ctx):
        return None  # 항상 None


class TestStrategyV2:
    def test_c1_legacy_dispatch_with_deprecation(self, sample_candles):
        """C1: legacy 4-arg 호출 → DeprecationWarning + 정상 동작"""
        s = _DummyStrategy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = s.analyze("005930", "삼성전자", sample_candles, MarketType.STOCK)
        assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_c2_v2_dispatch_with_ctx(self, sample_candles):
        """C2: AnalysisContext 인자 → DeprecationWarning 없음"""
        s = _DummyStrategy()
        ctx = AnalysisContext(symbol="005930", name="삼성전자",
                              candles=sample_candles, market_type=MarketType.STOCK)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = s.analyze(ctx)
        assert not any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_c3_exit_plan_default(self, sample_position, sample_ctx):
        """C3: default exit_plan — SL=-2%, take_profits=[]"""
        s = _DummyStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)
        assert plan.stop_loss.fixed_pct == Decimal("-0.02")
        assert plan.take_profits == []

    def test_c4_position_size_default(self, sample_signal):
        """C4: position_size = available * 0.3 / price (KRX 1주 quantize)"""
        s = _DummyStrategy()
        account = Account(balance=Decimal("10_000_000"), available=Decimal("10_000_000"),
                          position_count=0)
        size = s.position_size(sample_signal, account)
        assert isinstance(size, Decimal)
        assert size > 0

    def test_c5_health_check(self):
        """C5: health_check default — strategy_id ready=True"""
        s = _DummyStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "dummy_v1"
        assert h["ready"] is True
```

### 3.2 `tests/strategy/test_strategy_models.py` (5+)

```python
class TestAnalysisContext:
    def test_c6_construction_minimal(self, sample_candles):
        ctx = AnalysisContext(symbol="X", candles=sample_candles, market_type=MarketType.STOCK)
        assert ctx.symbol == "X"
        assert ctx.trading_session is None  # placeholder

    def test_c7_empty_candles_rejected(self):
        with pytest.raises(ValidationError):
            AnalysisContext(symbol="X", candles=[], market_type=MarketType.STOCK)

    def test_c8_from_legacy(self, sample_candles):
        ctx = AnalysisContext.from_legacy("X", "name", sample_candles, MarketType.STOCK)
        assert ctx.symbol == "X" and ctx.name == "name"


class TestExitPlan:
    def test_c9_qty_sum_validation(self):
        """take_profits qty_pct 합계 > 1.0 → ValidationError"""
        with pytest.raises(ValidationError):
            ExitPlan(
                take_profits=[
                    TakeProfitTier(price=Decimal(100), qty_pct=Decimal("0.6")),
                    TakeProfitTier(price=Decimal(110), qty_pct=Decimal("0.5")),
                ],
                stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
            )

    def test_c10_stop_loss_negative(self):
        """SL fixed_pct 양수 → ValidationError"""
        with pytest.raises(ValidationError):
            StopLoss(fixed_pct=Decimal("0.02"))


class TestAccount:
    def test_c11_negative_balance_rejected(self):
        with pytest.raises(ValidationError):
            Account(balance=Decimal("-1"), available=Decimal(0), position_count=0)
```

---

## 4. Verification Scenarios (V1~V6)

| # | 시나리오 | 명령 | 기대 |
|---|---|---|---|
| V1 | `make test-strategy` 통과 | `make test-strategy` | 10+ passed |
| V2 | 라인 커버리지 ≥ 80% | `pytest --cov=backend.core.strategy.base --cov=backend.models.strategy` | ≥ 80% |
| V3 | BAR-44 베이스라인 회귀 ±5% | `make baseline` 결과 vs PHASE-0-baseline-2026-05.md | 4 전략 모두 ±5% 이내 |
| V4 | BAR-40~43 회귀 무영향 | 4 전략 모두 import + dry-run | 무에러 |
| V5 | DeprecationWarning 발생 | C1 테스트 | warn 1회 |
| V6 | placeholder forward ref | mypy 또는 grep `# TODO(BAR-` | 5건 모두 주석 |

---

## 5. Implementation Checklist (D1~D10)

- [ ] D1 — 기존 `analyze()` 호출처 grep
- [ ] D2 — `backend/models/strategy.py` 작성 (5 모델)
- [ ] D3 — `backend/core/strategy/base.py` Strategy v2 ABC 확장 (dispatch)
- [ ] D4 — Position 모델 ExitPlan 호환 확인
- [ ] D5 — 4 전략 `_analyze_v2` 어댑터 (이름 변경 + ctx 풀어내기)
- [ ] D6 — `tests/strategy/test_base.py` 5+ 케이스 + conftest fixture
- [ ] D7 — `tests/strategy/test_strategy_models.py` 5+ 케이스
- [ ] D8 — `Makefile` `test-strategy` 또는 `test` 통합
- [ ] D9 — V1~V6 검증 (특히 V3 베이스라인 ±5%)
- [ ] D10 — PR 생성 (라벨: `area:strategy` `phase:1` `priority:p0`)

---

## 6. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — 5 모델 + Strategy v2 ABC + 10+ 테스트 + V1~V6 |
