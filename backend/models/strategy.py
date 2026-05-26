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

from backend.models.market import MarketType, OHLCV, TradingSession


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
    trading_session: Optional[TradingSession] = None  # BAR-52 정식 type ✅
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
    """고정 + 트레일링 + 시간별 단계 손절.

    time_stages (2026-05-17, ai-trade 패턴 도입):
      entry 후 경과초별 SL 단계. 예: [(120,-0.015),(300,-0.020),(99999,-0.025)]
      = 2분 -1.5% / 5분 -2% / 5분+ -2.5%
      → 개장 직후 노이즈 SL 회피 + 시간 지날수록 손실 허용 폭 확대.
      None 이면 fixed_pct 만 사용 (기존 동작 보존).
      breakeven 발동 후 갱신된 PositionState.sl_at 이 항상 우선.
    """

    model_config = ConfigDict(frozen=True)

    fixed_pct: Decimal = Field(..., lt=0)        # -0.02 = -2%
    trailing_pct: Optional[Decimal] = None       # 동적 손절 (선택)
    time_stages: Optional[list[tuple[int, Decimal]]] = None  # [(sec, sl_pct), ...]

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

    @field_validator("time_stages", mode="before")
    @classmethod
    def _stages_to_decimal(
        cls, v: Optional[list]
    ) -> Optional[list[tuple[int, Decimal]]]:
        if v is None:
            return None
        out: list[tuple[int, Decimal]] = []
        for sec, pct in v:
            d = pct if isinstance(pct, Decimal) else Decimal(str(pct))
            if d >= 0:
                raise ValueError(f"time_stages sl_pct must be < 0, got {d}")
            out.append((int(sec), d))
        out.sort(key=lambda kv: kv[0])
        return out

    def sl_pct_at_elapsed(self, elapsed_sec: float) -> Decimal:
        """elapsed_sec 시점의 SL 비율. time_stages 없으면 fixed_pct."""
        if not self.time_stages:
            return self.fixed_pct
        for limit_sec, pct in self.time_stages:
            if elapsed_sec <= limit_sec:
                return pct
        return self.time_stages[-1][1]


class ExitPlan(BaseModel):
    """청산 계획 — 분할 익절 + 손절 + 시간 청산 + 브레이크이븐 + 트레일링 + 보유 기간."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    take_profits: list[TakeProfitTier] = Field(default_factory=list)
    stop_loss: StopLoss
    time_exit: Optional[dtime] = None            # 예: 14:50 강제 청산
    breakeven_trigger: Optional[Decimal] = None  # 예: +0.015 도달 시 SL 을 +0.005 로
    # 5단계 변동성 트레일링 (2026-05-17, ai-trade 패턴):
    #   [(high_pnl_pct, trail_pct), ...] — peak 대비 high_pnl 도달 시 trail_pct 적용.
    #   예: [(0.05, -0.01), (0.04, -0.012), (0.03, -0.015), (0.02, -0.02), (0.015, -0.025)]
    #   high_pnl_pct DESC 정렬 (가장 큰 단계 우선). trail_sl = peak × (1 + trail_pct).
    #   None 이면 미적용 (기존 회귀 보존).
    trail_stages: Optional[list[tuple[Decimal, Decimal]]] = None

    # BAR-OPS-09 Phase C (2026-05-27) — 보유 기간 게이트 (swing 전략용):
    # - min_hold_days: 진입 후 N일 미만 시 TP/SL/breakeven 평가 차단 (강제 보유).
    #   단기 노이즈 청산 차단. None 이면 즉시 평가 가능 (기존 회귀 보존).
    # - max_hold_days: 진입 후 N일 도달 시 TIME_EXIT 강제 청산 (손익 무관).
    #   장기 보유 위험 차단. None 이면 무제한 보유.
    # 사용 사례: swing_38 (min=3, max=8) — 일봉 스윙 전략 보유 기간 강제.
    min_hold_days: Optional[int] = None
    max_hold_days: Optional[int] = None

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
        return sorted(v, key=lambda t: t.price)

    @field_validator("trail_stages", mode="before")
    @classmethod
    def _trail_to_decimal(
        cls, v: Optional[list]
    ) -> Optional[list[tuple[Decimal, Decimal]]]:
        if v is None:
            return None
        out: list[tuple[Decimal, Decimal]] = []
        for hp, tr in v:
            hp_d = hp if isinstance(hp, Decimal) else Decimal(str(hp))
            tr_d = tr if isinstance(tr, Decimal) else Decimal(str(tr))
            if hp_d <= 0:
                raise ValueError(f"trail_stages high_pnl must be > 0, got {hp_d}")
            if tr_d >= 0:
                raise ValueError(f"trail_stages trail_pct must be < 0, got {tr_d}")
            out.append((hp_d, tr_d))
        # DESC — 가장 큰 high_pnl 단계 우선 평가
        out.sort(key=lambda kv: kv[0], reverse=True)
        return out

    def trail_sl_for_peak(
        self, entry_price: Decimal, peak_price: Decimal
    ) -> Optional[Decimal]:
        """현재 peak 기준 trail SL 가격. trail_stages 없거나 단계 미달 시 None."""
        if not self.trail_stages or peak_price <= entry_price:
            return None
        high_pnl_pct = (peak_price - entry_price) / entry_price
        for thresh, trail_pct in self.trail_stages:  # DESC
            if high_pnl_pct >= thresh:
                return peak_price * (Decimal(1) + trail_pct)
        return None


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
