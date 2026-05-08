"""BAR-OPS-03 — LiveTradingOrchestrator 통합 (10 cases)."""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.core.execution.exit_engine import ExitEngine
from backend.core.execution.live_trading import (
    LiveTradingOrchestrator,
    TradeOutcome,
)
from backend.core.execution.router import SmartOrderRouter
from backend.core.gateway.composite_orderbook import CompositeOrderBookService
from backend.core.market_session.service import KST, MarketSessionService
from backend.core.risk.kill_switch import KillSwitch, KillSwitchReason
from backend.core.risk.theme_guard import ThemeAwareRiskGuard, ThemeExposurePolicy
from backend.models.exit_order import PositionState
from backend.models.market import Exchange, OrderBookL2, TradingSession
from backend.models.order import OrderRequest, OrderSide
from backend.models.strategy import ExitPlan, StopLoss, TakeProfitTier


def _ob(venue, bids, asks):
    return OrderBookL2(
        symbol="005930", venue=venue,
        ts=datetime.now(KST), bids=bids, asks=asks,
    )


@pytest.fixture
def session_regular(monkeypatch):
    s = MarketSessionService()
    monkeypatch.setattr(s, "get_session", lambda now=None: TradingSession.REGULAR)
    return s


@pytest.fixture
def book():
    svc = CompositeOrderBookService()
    krx = _ob(Exchange.KRX, [(Decimal("69900"), 100)], [(Decimal("70100"), 100)])
    return svc.merge(krx, None, "005930")


@pytest.fixture
def orch(session_regular):
    return LiveTradingOrchestrator(
        router=SmartOrderRouter(session_regular),
        exit_engine=ExitEngine(),
        kill_switch=KillSwitch(),
        risk_guard=ThemeAwareRiskGuard(ThemeExposurePolicy(
            max_theme_exposure_pct=0.40,
            max_concurrent_positions=3,
            max_position_pct=0.30,
        )),
    )


def _order(qty=10):
    return OrderRequest(symbol="005930", side=OrderSide.BUY, qty=qty)


class TestEntry:
    @pytest.mark.asyncio
    async def test_approved_entry(self, orch, book):
        attempt = await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={},
        )
        assert attempt.outcome == TradeOutcome.APPROVED
        assert attempt.routing is not None
        assert attempt.routing.venue == Exchange.KRX

    @pytest.mark.asyncio
    async def test_blocked_by_kill_switch(self, orch, book):
        orch._ks.trip(KillSwitchReason.MANUAL, datetime(2026, 5, 7, 10, 0))
        attempt = await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={},
        )
        assert attempt.outcome == TradeOutcome.BLOCKED_KILL_SWITCH

    @pytest.mark.asyncio
    async def test_blocked_by_concurrent_positions(self, orch, book):
        attempt = await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=3,   # 한도 도달
            current_theme_exposure={},
        )
        assert attempt.outcome == TradeOutcome.BLOCKED_RISK_GUARD
        assert "동시" in attempt.reason or "concurrent" in attempt.reason.lower()

    @pytest.mark.asyncio
    async def test_blocked_by_position_size(self, orch, book):
        attempt = await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("40000"),     # 40%
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={},
        )
        assert attempt.outcome == TradeOutcome.BLOCKED_RISK_GUARD

    @pytest.mark.asyncio
    async def test_blocked_by_theme_exposure(self, orch, book):
        attempt = await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("10000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={1: Decimal("35000")},   # 합산 45%
        )
        assert attempt.outcome == TradeOutcome.BLOCKED_RISK_GUARD

    @pytest.mark.asyncio
    async def test_blocked_by_routing_no_liquidity(self, orch):
        svc = CompositeOrderBookService()
        empty_book = svc.merge(None, None, "005930")
        attempt = await orch.attempt_entry(
            _order(), empty_book,
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={},
        )
        assert attempt.outcome == TradeOutcome.BLOCKED_ROUTING

    @pytest.mark.asyncio
    async def test_executor_called_on_approval(self, orch, book):
        executor = AsyncMock()
        executor.submit = AsyncMock(return_value={"status": "filled"})
        orch._executor = executor
        await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={},
        )
        executor.submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_executor_failure_logged_not_raised(self, orch, book):
        executor = AsyncMock()
        executor.submit = AsyncMock(side_effect=RuntimeError("network"))
        orch._executor = executor
        attempt = await orch.attempt_entry(
            _order(), book,
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("100000"),
            current_concurrent_positions=0,
            current_theme_exposure={},
        )
        # executor 실패해도 APPROVED 반환 (라우팅까지는 통과)
        assert attempt.outcome == TradeOutcome.APPROVED


class TestExit:
    def test_evaluate_position_delegates_to_exit_engine(self, orch):
        plan = ExitPlan(
            take_profits=[TakeProfitTier(price=Decimal("103"), qty_pct=Decimal("0.5"))],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
        )
        pos = PositionState(
            symbol="005930",
            entry_price=Decimal("100"),
            qty=Decimal("100"),
            initial_qty=Decimal("100"),
            entry_time=datetime(2026, 5, 7, 9, 30),
        )
        new_pos, orders = orch.evaluate_position(
            pos, plan, Decimal("103.5"), datetime(2026, 5, 7, 10, 0)
        )
        assert len(orders) == 1
        assert new_pos.tp_filled == 1

    def test_exit_works_even_with_active_kill_switch(self, orch):
        """KillSwitch 가 발동된 상태에서도 보유 포지션 청산은 정상 작동."""
        orch._ks.trip(KillSwitchReason.DAILY_LOSS, datetime(2026, 5, 7, 10, 0))
        plan = ExitPlan(
            take_profits=[],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
        )
        pos = PositionState(
            symbol="005930",
            entry_price=Decimal("100"),
            qty=Decimal("100"),
            initial_qty=Decimal("100"),
            entry_time=datetime(2026, 5, 7, 9, 30),
        )
        # SL 발동
        new_pos, orders = orch.evaluate_position(
            pos, plan, Decimal("97"), datetime(2026, 5, 7, 10, 5)
        )
        assert len(orders) == 1
        assert new_pos.qty == Decimal("0")
