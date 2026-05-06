"""
ScalpingConsensusStrategy — 12 legacy_scalping 에이전트 가중합 (Strategy v2 wrapper).

옵션 B (provider injection): 외부 (legacy ScalpingCoordinator wrapper, 모킹 등) 가
ctx 받아 ScalpingAnalysis 또는 dict 반환. 본 Strategy 는 BAR-41 to_entry_signal
어댑터로 EntrySignal 변환 + threshold 0.65 적용.

BAR-50: Phase 1 마지막 티켓.

Reference:
- Plan: docs/01-plan/features/bar-50-scalping-consensus.plan.md
- Design: docs/02-design/features/bar-50-scalping-consensus.design.md
- BAR-41 어댑터: backend/legacy_scalping/_adapter.py
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Callable, Optional

from backend.core.strategy.base import Strategy
from backend.legacy_scalping import to_entry_signal
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


@dataclass
class ScalpingConsensusParams:
    """ScalpingConsensus 파라미터."""

    threshold: float = 0.65   # score 정규화(0~1) ≥ threshold 만 통과


# 분석 결과 provider 시그니처 (외부 주입용)
AnalysisProvider = Callable[[AnalysisContext], Optional[Any]]


class ScalpingConsensusStrategy(Strategy):
    """12 legacy_scalping 에이전트 가중합 — provider injection 패턴."""

    STRATEGY_ID = "scalping_consensus_v1"

    def __init__(self, params: Optional[ScalpingConsensusParams] = None) -> None:
        self.params = params or ScalpingConsensusParams()
        self._provider: Optional[AnalysisProvider] = None

    def set_analysis_provider(self, provider: AnalysisProvider) -> None:
        """ScalpingAnalysis 또는 dict 를 반환하는 콜러블 등록.

        외부 (legacy ScalpingCoordinator wrapper, 모킹) 가 ctx 받아 분석 결과 반환.
        후속 BAR-78 회귀 자동화 시점에 정식 coordinator wrapper 와 연결.
        """
        self._provider = provider

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """provider 결과 → EntrySignal (BAR-41 어댑터) → threshold 0.65 적용."""
        if self._provider is None:
            return None  # provider 미등록 시 silent None

        legacy_data = self._provider(ctx)
        if legacy_data is None:
            return None

        try:
            signal = to_entry_signal(legacy_data, fallback_market_type=ctx.market_type)
        except (TypeError, ValueError):
            return None  # adapter 실패 시 silent None

        if signal.score < self.params.threshold:
            return None  # threshold 미달

        # ScalpingConsensus strategy_id 로 재라벨
        return signal.model_copy(update={"strategy_id": self.STRATEGY_ID})

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """단타 정책: TP1=+1.5% (50%), TP2=+3% (50%), SL=-1%, breakeven=+0.5%."""
        avg = Decimal(str(position.avg_price))
        return ExitPlan(
            take_profits=[
                TakeProfitTier(
                    price=avg * Decimal("1.015"),
                    qty_pct=Decimal("0.5"),
                    condition="단타 TP1 +1.5%",
                ),
                TakeProfitTier(
                    price=avg * Decimal("1.03"),
                    qty_pct=Decimal("0.5"),
                    condition="단타 TP2 +3%",
                ),
            ],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.01")),
            time_exit=dtime(14, 50) if ctx.market_type == MarketType.STOCK else None,
            breakeven_trigger=Decimal("0.005"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """단타 보수적: ≥0.7 → 25%, 0.5~0.7 → 15%, <0.5 → 8%.

        threshold 0.65 가 진입 자체 차단하므로 실질 진입은 ≥0.65 (대부분 25% 분기).
        """
        if account.available <= 0:
            return Decimal(0)

        score = Decimal(str(signal.score))
        if score >= Decimal("0.7"):
            ratio = Decimal("0.25")
        elif score >= Decimal("0.5"):
            ratio = Decimal("0.15")
        else:
            ratio = Decimal("0.08")

        max_invest = account.available * ratio
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": self._provider is not None,
            "provider_registered": self._provider is not None,
            "threshold": self.params.threshold,
        }


__all__ = ["ScalpingConsensusStrategy", "ScalpingConsensusParams"]
