"""
Strategy v2 ABC — 매매 전략 추상 기반 클래스.

v2 (BAR-45):
- analyze(ctx: AnalysisContext) -> Optional[EntrySignal]   ← 신규 진입점
- exit_plan(position, ctx) -> ExitPlan                       ← 신규
- position_size(signal, account) -> Decimal                  ← 신규 (자금흐름, Decimal)
- health_check() -> dict                                     ← 신규

Backward compat (Phase 1 종료까지 지원):
- analyze(symbol, name, candles, market_type) → DeprecationWarning + 자동 변환

Reference:
- Plan: docs/01-plan/features/bar-45-strategy-v2.plan.md
- Design: docs/02-design/features/bar-45-strategy-v2.design.md
- BAR-44 회귀 임계값: 베이스라인 ±5%
"""
from __future__ import annotations

import warnings
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Optional

from backend.models.position import Position
from backend.models.signal import EntrySignal, ExitSignal
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
)


class Strategy(ABC):
    """매매 전략 추상 기반 클래스 (v2)."""

    STRATEGY_ID: str = ""

    # === v2 진입점 ===

    def analyze(self, *args: Any, **kwargs: Any) -> Optional[EntrySignal]:
        """진입 신호 분석 — v2 시그니처 + backward compat dispatch.

        v2 사용:
            strategy.analyze(ctx: AnalysisContext) -> EntrySignal | None

        Legacy (deprecated, Phase 1 종료까지 지원):
            strategy.analyze(symbol, name, candles, market_type) -> EntrySignal | None
        """
        if args and isinstance(args[0], AnalysisContext):
            return self._analyze_v2(args[0])

        # Legacy 4-arg 또는 키워드 dispatch
        warnings.warn(
            "Strategy.analyze(symbol, name, candles, market_type) is deprecated "
            "(BAR-45). Use AnalysisContext. Will be removed at Phase 1 종료.",
            DeprecationWarning,
            stacklevel=2,
        )
        ctx = AnalysisContext.from_legacy(*args, **kwargs)
        return self._analyze_v2(ctx)

    @abstractmethod
    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """v2 진입 신호 분석 — 구체 클래스가 override."""
        ...

    # === v2 청산 / 사이징 / 헬스체크 (default 구현) ===

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """청산 계획 — 기본은 SL=-2%, TP 없음. 후속 BAR-46~49/63 가 override."""
        return ExitPlan(
            take_profits=[],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
        )

    def exit_on_signal(
        self,
        position: Position,
        ctx: AnalysisContext,
        current_price: Decimal,
    ) -> Optional[ExitSignal]:
        """지표 기반 반대 시그널 청산 — 기본 None (가격 기반 ExitPlan 만 사용).

        추세추종 전략(supertrend 등)이 자신의 지표로 **추세전환(매도/숏) 시그널**을
        감지했을 때, 가격 SL 도달 전이라도 보유 포지션을 즉시 청산하도록 override.
        SupertrendExitWatcher 가 position.strategy_id 로 라우팅하므로, 해당 전략이
        진입한 포지션에 대해서만 호출된다 (다른 전략 포지션엔 영향 없음).

        Returns:
            청산해야 하면 ExitSignal(exit_type="reverse_signal"), 아니면 None.
        """
        return None

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """포지션 사이징 — BAR-OPS-09 Phase 9 (2026-05-23) 균등 진입.

        모든 strategy 종목별 동일 금액 진입 (default 8% = 1/10 슬롯).
        score 정보는 진입 게이트(strategy._analyze_v2)에서만 사용. 운영 매수 qty
        는 balance_gate.evaluate_risk_gate() 가 최종 결정.
        """
        from backend.core.strategy.position_sizing import even_position_size
        return even_position_size(signal, account)

    def health_check(self) -> dict[str, Any]:
        """전략 상태 점검 — 데이터 충분성·파라미터 sanity. 후속 BAR override 가능."""
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": bool(self.STRATEGY_ID),
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy_id={self.STRATEGY_ID!r})"


__all__ = ["Strategy"]
