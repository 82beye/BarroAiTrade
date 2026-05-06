"""BAR-63 — ExitEngine 15 케이스."""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

import pytest

from backend.core.execution.exit_engine import ExitEngine
from backend.models.exit_order import ExitOrder, ExitReason, PositionState
from backend.models.strategy import ExitPlan, StopLoss, TakeProfitTier


def _plan(
    tps: list[tuple[str, str]] | None = None,
    sl: str = "-0.02",
    breakeven: str | None = None,
    time_exit: time | None = None,
) -> ExitPlan:
    take_profits = [
        TakeProfitTier(price=Decimal(p), qty_pct=Decimal(q)) for p, q in (tps or [])
    ]
    return ExitPlan(
        take_profits=take_profits,
        stop_loss=StopLoss(fixed_pct=Decimal(sl)),
        time_exit=time_exit,
        breakeven_trigger=Decimal(breakeven) if breakeven else None,
    )


def _pos(qty: str = "100", entry: str = "100", **overrides) -> PositionState:
    base = dict(
        symbol="005930",
        entry_price=Decimal(entry),
        qty=Decimal(qty),
        initial_qty=Decimal(qty),
        entry_time=datetime(2026, 5, 7, 9, 30),
    )
    base.update(overrides)
    return PositionState(**base)


class TestModel:
    def test_exit_order_frozen(self):
        o = ExitOrder(symbol="x", qty=Decimal("1"), target_price=Decimal("100"), reason=ExitReason.TP1)
        with pytest.raises(Exception):
            o.qty = Decimal("2")  # type: ignore[misc]

    def test_position_frozen(self):
        p = _pos()
        with pytest.raises(Exception):
            p.qty = Decimal("0")  # type: ignore[misc]

    def test_exit_order_qty_positive(self):
        with pytest.raises(Exception):
            ExitOrder(symbol="x", qty=Decimal("0"), target_price=Decimal("100"), reason=ExitReason.TP1)


class TestTakeProfit:
    def test_tp1_triggered(self):
        e = ExitEngine()
        plan = _plan(tps=[("103", "0.5")], sl="-0.02")
        pos = _pos(entry="100", qty="100")
        new_pos, orders = e.evaluate(pos, plan, Decimal("103.5"), datetime(2026, 5, 7, 10, 0))
        assert len(orders) == 1
        assert orders[0].reason == ExitReason.TP1
        assert orders[0].qty == Decimal("50")
        assert new_pos.qty == Decimal("50")
        assert new_pos.tp_filled == 1

    def test_tp1_and_tp2_sequential(self):
        e = ExitEngine()
        plan = _plan(tps=[("103", "0.3"), ("105", "0.4")], sl="-0.02")
        pos = _pos(entry="100", qty="100")
        # 103 도달 → TP1 만
        new_pos, orders1 = e.evaluate(pos, plan, Decimal("103.5"), datetime(2026, 5, 7, 10, 0))
        assert len(orders1) == 1
        # 105 도달 → TP2
        new_pos2, orders2 = e.evaluate(new_pos, plan, Decimal("105.1"), datetime(2026, 5, 7, 10, 5))
        assert len(orders2) == 1
        assert orders2[0].reason == ExitReason.TP2
        assert orders2[0].qty == Decimal("40")
        assert new_pos2.qty == Decimal("30")
        assert new_pos2.tp_filled == 2

    def test_all_tp_filled(self):
        e = ExitEngine()
        plan = _plan(tps=[("103", "0.3"), ("105", "0.4"), ("107", "0.3")], sl="-0.02")
        pos = _pos(entry="100", qty="100")
        # 한 번에 107 도달
        _, orders = e.evaluate(pos, plan, Decimal("108"), datetime(2026, 5, 7, 10, 0))
        assert len(orders) == 3
        total_qty = sum((o.qty for o in orders), Decimal(0))
        assert total_qty == Decimal("100")

    def test_tp_below_threshold_no_trigger(self):
        e = ExitEngine()
        plan = _plan(tps=[("105", "0.5")], sl="-0.02")
        pos = _pos(entry="100", qty="100")
        new_pos, orders = e.evaluate(pos, plan, Decimal("103"), datetime(2026, 5, 7, 10, 0))
        assert orders == []
        assert new_pos.qty == Decimal("100")


class TestStopLoss:
    def test_sl_full_exit(self):
        e = ExitEngine()
        plan = _plan(sl="-0.02")
        pos = _pos(entry="100", qty="100")
        new_pos, orders = e.evaluate(pos, plan, Decimal("97"), datetime(2026, 5, 7, 10, 0))
        assert len(orders) == 1
        assert orders[0].reason == ExitReason.STOP_LOSS
        assert orders[0].qty == Decimal("100")
        assert new_pos.qty == Decimal("0")

    def test_sl_at_explicit_used(self):
        e = ExitEngine()
        plan = _plan(sl="-0.05")  # fixed_pct 무시되고 sl_at 사용
        pos = _pos(entry="100", qty="100", sl_at=Decimal("99.5"))
        new_pos, orders = e.evaluate(pos, plan, Decimal("99.4"), datetime(2026, 5, 7, 10, 0))
        assert len(orders) == 1
        assert orders[0].reason == ExitReason.STOP_LOSS

    def test_sl_not_triggered(self):
        e = ExitEngine()
        plan = _plan(sl="-0.02")
        pos = _pos(entry="100", qty="100")
        _, orders = e.evaluate(pos, plan, Decimal("99"), datetime(2026, 5, 7, 10, 0))
        assert orders == []


class TestBreakeven:
    def test_breakeven_updates_sl_after_tp1(self):
        e = ExitEngine()
        plan = _plan(
            tps=[("103", "0.5")], sl="-0.02", breakeven="0.005"
        )  # SL → +0.5%
        pos = _pos(entry="100", qty="100")
        new_pos, _ = e.evaluate(pos, plan, Decimal("103"), datetime(2026, 5, 7, 10, 0))
        # breakeven 적용 — sl_at = 100 * 1.005 = 100.5
        assert new_pos.sl_at == Decimal("100.500")

    def test_breakeven_no_update_without_tp(self):
        e = ExitEngine()
        plan = _plan(tps=[("110", "0.5")], sl="-0.02", breakeven="0.005")
        pos = _pos(entry="100", qty="100")
        new_pos, _ = e.evaluate(pos, plan, Decimal("105"), datetime(2026, 5, 7, 10, 0))
        # TP 미발동 → sl_at 유지
        assert new_pos.sl_at is None


class TestTimeExit:
    def test_time_exit_full(self):
        e = ExitEngine()
        plan = _plan(sl="-0.02", time_exit=time(14, 50))
        pos = _pos(qty="100")
        new_pos, orders = e.evaluate(pos, plan, Decimal("100"), datetime(2026, 5, 7, 14, 50))
        assert len(orders) == 1
        assert orders[0].reason == ExitReason.TIME_EXIT
        assert new_pos.qty == Decimal("0")


class TestEdge:
    def test_empty_plan(self):
        e = ExitEngine()
        plan = _plan(sl="-0.02")
        pos = _pos(entry="100", qty="100")
        _, orders = e.evaluate(pos, plan, Decimal("100.5"), datetime(2026, 5, 7, 10, 0))
        assert orders == []

    def test_zero_qty_returns_empty(self):
        e = ExitEngine()
        plan = _plan(tps=[("103", "0.5")], sl="-0.02")
        # initial_qty=100 으로 진입 후 전량 청산된 상태 → qty=0
        pos = PositionState(
            symbol="005930", entry_price=Decimal("100"),
            qty=Decimal("0"), initial_qty=Decimal("100"),
            entry_time=datetime(2026, 5, 7, 9, 30),
        )
        _, orders = e.evaluate(pos, plan, Decimal("110"), datetime(2026, 5, 7, 10, 0))
        assert orders == []
