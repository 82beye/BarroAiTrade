"""BAR-OPS-03 — Live Trading Orchestrator.

자동매매 단위 사이클: 시그널 수신 → RiskGuard 승인 → SOR 라우팅 → KillSwitch 감시.
포지션 보유 중: 가격 tick → ExitEngine 평가 → ExitOrder 발행 → SOR 라우팅.

OrderExecutor 통합 (BAR-63b 흡수). 실 주문 송수신은 OrderExecutor 어댑터가 담당.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.core.execution.exit_engine import ExitEngine
from backend.core.execution.router import SmartOrderRouter
from backend.core.risk.kill_switch import KillSwitch
from backend.core.risk.theme_guard import ThemeAwareRiskGuard
from backend.models.exit_order import ExitOrder, PositionState
from backend.models.market import CompositeOrderBookL2
from backend.models.order import OrderRequest, RoutingDecision
from backend.models.strategy import ExitPlan

logger = logging.getLogger(__name__)


class TradeOutcome(str, Enum):
    APPROVED = "approved"
    BLOCKED_KILL_SWITCH = "blocked_kill_switch"
    BLOCKED_RISK_GUARD = "blocked_risk_guard"
    BLOCKED_ROUTING = "blocked_routing"


class TradeAttempt(BaseModel):
    """단일 매매 시도 결과 (frozen)."""

    model_config = ConfigDict(frozen=True)

    outcome: TradeOutcome
    routing: Optional[RoutingDecision] = None
    reason: str = ""


class OrderExecutor(Protocol):
    """실 주문 송수신 어댑터. BAR-63b 운영에서 키움/IBKR/Upbit 어댑터로 교체."""

    async def submit(self, decision: RoutingDecision) -> dict: ...


class LiveTradingOrchestrator:
    """진입 + 보유 + 청산을 한 단위 사이클로 묶음.

    의존:
    - SmartOrderRouter (BAR-55) — 라우팅 결정
    - ExitEngine (BAR-63) — 청산 평가
    - KillSwitch (BAR-64) — 자동 매매 중단
    - ThemeAwareRiskGuard (BAR-66) — 사전 비중 검증
    - OrderExecutor (BAR-63b) — 실 송수신
    """

    def __init__(
        self,
        router: SmartOrderRouter,
        exit_engine: ExitEngine,
        kill_switch: KillSwitch,
        risk_guard: ThemeAwareRiskGuard,
        executor: Optional[OrderExecutor] = None,
    ) -> None:
        self._router = router
        self._exit = exit_engine
        self._ks = kill_switch
        self._risk = risk_guard
        self._executor = executor

    # ─── 진입 ─────────────────────────────────────────────

    async def attempt_entry(
        self,
        order: OrderRequest,
        book: CompositeOrderBookL2,
        *,
        order_value: Decimal,
        order_theme_id: Optional[int],
        total_value: Decimal,
        current_concurrent_positions: int,
        current_theme_exposure: dict[int, Decimal],
        now: Optional[datetime] = None,
    ) -> TradeAttempt:
        """주문 1건 진입 시도. KillSwitch / RiskGuard / SOR 순서대로 게이트."""
        # 1. KillSwitch
        if self._ks.state.is_active:
            return TradeAttempt(
                outcome=TradeOutcome.BLOCKED_KILL_SWITCH,
                reason=str(self._ks.state.reason or "kill_switch active"),
            )

        # 2. RiskGuard (concurrent + position size + theme exposure)
        ok, reason = self._risk.check_concurrent_positions(current_concurrent_positions)
        if not ok:
            return TradeAttempt(outcome=TradeOutcome.BLOCKED_RISK_GUARD, reason=reason)
        ok, reason = self._risk.check_position_size(order_value, total_value)
        if not ok:
            return TradeAttempt(outcome=TradeOutcome.BLOCKED_RISK_GUARD, reason=reason)
        if order_theme_id is not None:
            ok, reason = self._risk.check_theme_exposure(
                order_value=order_value,
                order_theme_id=order_theme_id,
                total_value=total_value,
                current_theme_exposure=current_theme_exposure,
            )
            if not ok:
                return TradeAttempt(outcome=TradeOutcome.BLOCKED_RISK_GUARD, reason=reason)

        # 3. SOR 라우팅
        decision = self._router.route(order, book, now)
        if not decision.is_routed:
            return TradeAttempt(
                outcome=TradeOutcome.BLOCKED_ROUTING,
                routing=decision,
                reason=str(decision.reason.value),
            )

        # 4. 실 송수신 (운영)
        if self._executor is not None:
            try:
                await self._executor.submit(decision)
            except Exception as exc:
                logger.error("executor submit failed: %s", exc)

        return TradeAttempt(outcome=TradeOutcome.APPROVED, routing=decision)

    # ─── 보유 / 청산 ─────────────────────────────────────

    def evaluate_position(
        self,
        position: PositionState,
        plan: ExitPlan,
        current_price: Decimal,
        now: datetime,
    ) -> tuple[PositionState, list[ExitOrder]]:
        """가격 tick 마다 호출. ExitEngine 위임 + KillSwitch 영향 X
        (KillSwitch 는 신규 진입만 차단, 보유 청산은 항상 정상 발동)."""
        return self._exit.evaluate(position, plan, current_price, now)


__all__ = ["TradeOutcome", "TradeAttempt", "OrderExecutor", "LiveTradingOrchestrator"]
