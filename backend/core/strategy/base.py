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
from backend.models.signal import EntrySignal
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

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """포지션 사이징 — 기본은 available × 0.3 / price (KRX 1주 quantize).

        BAR-66 (RiskEngine 비중 관리) 가 동시 보유 한도·동일 테마 합산 등 정책으로 override.
        """
        if account.available <= 0:
            return Decimal(0)
        max_invest = account.available * Decimal("0.3")
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        # KRX 1주 단위 quantize (코인은 후속 BAR 에서 0.000001 단위 override)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        """전략 상태 점검 — 데이터 충분성·파라미터 sanity. 후속 BAR override 가능."""
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": bool(self.STRATEGY_ID),
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy_id={self.STRATEGY_ID!r})"


__all__ = ["Strategy"]
