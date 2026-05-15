"""
SF존 (슈퍼존) 전략 — F존 + 추가 강도 (거래량 재증가, 강한 기준봉, 테마 연속성).

BAR-47: F존 클래스에서 SF존 분기를 별도 클래스로 분리. 옵션 A (delegate) 채택 —
내부에 FZoneStrategy 인스턴스 보유, signal_type="sf_zone" 만 통과시킴.

Reference:
- Plan: docs/01-plan/features/bar-47-sf-zone-split.plan.md
- Design: docs/02-design/features/bar-47-sf-zone-split.design.md
- F존 본문: backend/core/strategy/f_zone.py
"""
from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal
from typing import Any, Optional

from backend.core.strategy.base import Strategy
from backend.core.strategy.f_zone import FZoneParams, FZoneStrategy
from backend.models.market import MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)


class SFZoneStrategy(Strategy):
    """SF존 (슈퍼존) 전략 — F존 + 추가 강도."""

    STRATEGY_ID = "sf_zone_v1"

    def __init__(self, params: Optional[FZoneParams] = None) -> None:
        # FZoneStrategy 인스턴스 보유 (옵션 A delegate).
        # f_zone default 가 max=1.0(무제한)이라 별도 override 불요 — 시그널 분리는
        # exit_plan(IntradaySimulator._exit_plan_for_strategy) 레벨에서 처리.
        self._inner = FZoneStrategy(params=params)

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """F존 분석 후 sf_zone 신호만 통과 + strategy_id 재라벨."""
        signal = self._inner._analyze_v2(ctx)
        if signal is None or signal.signal_type != "sf_zone":
            return None
        # SF존 strategy_id 로 재라벨
        return signal.model_copy(update={"strategy_id": self.STRATEGY_ID})

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """SF존 정책 — F존 대비 강화: TP3 추가 (+7%), SL=-1.5% 더 타이트, breakeven=+1.0%."""
        avg = Decimal(str(position.avg_price))

        take_profits = [
            TakeProfitTier(
                price=avg * Decimal("1.03"),
                qty_pct=Decimal("0.33"),
                condition="SF존 TP1 +3%",
            ),
            TakeProfitTier(
                price=avg * Decimal("1.05"),
                qty_pct=Decimal("0.33"),
                condition="SF존 TP2 +5%",
            ),
            TakeProfitTier(
                price=avg * Decimal("1.07"),
                qty_pct=Decimal("0.34"),
                condition="SF존 TP3 +7%",
            ),
        ]

        time_exit = dtime(14, 50) if ctx.market_type == MarketType.STOCK else None

        return ExitPlan(
            take_profits=take_profits,
            stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
            time_exit=time_exit,
            breakeven_trigger=Decimal("0.01"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """SF존 강도(score) 기반 비중: ≥0.7 → 35%, 0.5~0.7 → 25%, <0.5 → 10%."""
        if account.available <= 0:
            return Decimal(0)

        score = Decimal(str(signal.score))
        if score >= Decimal("0.7"):
            ratio = Decimal("0.35")
        elif score >= Decimal("0.5"):
            ratio = Decimal("0.25")
        else:
            ratio = Decimal("0.10")

        max_invest = account.available * ratio
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        """SF존 health_check — F존 inner ready + sf_impulse_min_gain_pct ≥ 0.045
        (2026-05-16 튜닝: 0.05 → 0.045, 신호 희소 완화)."""
        inner_h = self._inner.health_check()
        p = self._inner.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": inner_h["ready"] and p.sf_impulse_min_gain_pct >= 0.045,
            "inner_ready": inner_h["ready"],
            "sf_impulse_min_gain_pct": p.sf_impulse_min_gain_pct,
        }


__all__ = ["SFZoneStrategy"]
