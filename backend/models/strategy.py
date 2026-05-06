"""
BAR-45: Strategy v2 의 입출력 Pydantic v2 모델.

자금흐름 영역 — 가격·수량·잔고는 모두 Decimal.
EntrySignal.price 는 float 유지 (BAR-41 어댑터 호환). 본 모델은 *내부* Decimal.

Reference:
- Plan: docs/01-plan/features/bar-45-strategy-v2.plan.md
- Design: docs/02-design/features/bar-45-strategy-v2.design.md §2.1
"""
from __future__ import annotations

from datetime import datetime, time as dtime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models.market import MarketType, OHLCV


# === AnalysisContext ===

class AnalysisContext(BaseModel):
    """진입 의사결정 컨텍스트. KRX/NXT 통합 시세, 호가, 테마, 뉴스, 시간대.

    Phase 0 단계에서는 candles + symbol + market_type 만 필수. 후속 BAR 가
    placeholder 5건을 채움 (BAR-52/54/57/58 등).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    symbol: str = Field(..., min_length=1)
    name: str | None = None
    candles: list[OHLCV] = Field(..., min_length=1)
    market_type: MarketType
    timestamp: datetime = Field(default_factory=datetime.now)

    # 선택 — Phase 0 이후 BAR 가 채움
    orderbook: Any = None              # backend.models.market.OrderBook (forward ref)
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
        """Backward compat — 4-arg legacy 호출 변환 (Strategy.analyze dispatch)."""
        return cls(
            symbol=symbol,
            name=name,
            candles=candles,
            market_type=market_type,
        )


# === ExitPlan ===

class TakeProfitTier(BaseModel):
    """분할 익절 단계 — (price, qty_pct, condition)."""

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

    @field_validator("fixed_pct", mode="before")
    @classmethod
    def _fixed_to_decimal(cls, v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))

    @field_validator("trailing_pct", mode="before")
    @classmethod
    def _trailing_to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))


class ExitPlan(BaseModel):
    """청산 계획 — 분할 익절 + 손절 + 시간 청산 + 브레이크이븐."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    take_profits: list[TakeProfitTier] = Field(default_factory=list)
    stop_loss: StopLoss
    time_exit: Optional[dtime] = None            # 예: 14:50 강제 청산
    breakeven_trigger: Optional[Decimal] = None  # 예: +0.015 도달 시 SL 을 +0.005 로

    @field_validator("breakeven_trigger", mode="before")
    @classmethod
    def _be_to_decimal(cls, v: Any) -> Optional[Decimal]:
        if v is None:
            return None
        return v if isinstance(v, Decimal) else Decimal(str(v))

    @field_validator("take_profits")
    @classmethod
    def _qty_sum_le_one(cls, v: list[TakeProfitTier]) -> list[TakeProfitTier]:
        total = sum((t.qty_pct for t in v), Decimal(0))
        if total > Decimal("1.0001"):
            raise ValueError(f"take_profits qty_pct 합계 {total} > 1.0")
        return v


# === Account ===

class Account(BaseModel):
    """포지션 사이징 입력 — 잔고·일일 PnL·현재 보유 수.

    daily_pnl_pct 는 Kill Switch (-3%) 발동 임계값과 비교 (BAR-64 도입 예정).
    """

    model_config = ConfigDict(frozen=True)

    balance: Decimal = Field(..., ge=0)
    available: Decimal = Field(..., ge=0)
    position_count: int = Field(..., ge=0)
    daily_pnl_pct: Decimal = Decimal(0)

    @field_validator("balance", "available", "daily_pnl_pct", mode="before")
    @classmethod
    def _to_decimal(cls, v: Any) -> Decimal:
        return v if isinstance(v, Decimal) else Decimal(str(v))


__all__ = [
    "AnalysisContext",
    "TakeProfitTier",
    "StopLoss",
    "ExitPlan",
    "Account",
]
