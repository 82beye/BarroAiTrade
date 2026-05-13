"""BAR-OPS-20 — holding_evaluator 테스트."""
from __future__ import annotations

from decimal import Decimal

from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.risk.holding_evaluator import (
    ExitPolicy,
    HoldingDecision,
    SellSignal,
    evaluate_all,
    evaluate_holding,
    render_decisions_table,
)


def _h(pnl_rate: str = "0.0", **kw) -> HoldingPosition:
    base = dict(
        symbol="005930", name="삼성전자", qty=10,
        avg_buy_price=Decimal("260000"),
        cur_price=Decimal("276500"),
        eval_amount=Decimal("2765000"),
        pnl=Decimal("165000"),
        pnl_rate=Decimal(pnl_rate),
    )
    base.update(kw)
    return HoldingPosition(**base)


def test_take_profit_signal_at_threshold():
    d = evaluate_holding(_h(pnl_rate="5.0"), ExitPolicy(take_profit_pct=Decimal("5.0")))
    assert d.signal == SellSignal.TAKE_PROFIT
    assert "익절" in d.reason


def test_take_profit_signal_above_threshold():
    d = evaluate_holding(_h(pnl_rate="6.35"))
    assert d.signal == SellSignal.TAKE_PROFIT


def test_stop_loss_signal_at_threshold():
    d = evaluate_holding(_h(pnl_rate="-2.0"), ExitPolicy(stop_loss_pct=Decimal("-2.0")))
    assert d.signal == SellSignal.STOP_LOSS
    assert "손절" in d.reason


def test_stop_loss_signal_below_threshold():
    # main 9c4ed24: default SL -4% (이전 -2%) — pnl_rate -4.5 로 SL 트리거
    d = evaluate_holding(_h(pnl_rate="-4.5"))
    assert d.signal == SellSignal.STOP_LOSS


def test_hold_signal_in_range():
    d = evaluate_holding(_h(pnl_rate="1.5"))
    assert d.signal == SellSignal.HOLD
    assert "보유 유지" in d.reason


def test_hold_at_exactly_zero():
    d = evaluate_holding(_h(pnl_rate="0.0"))
    assert d.signal == SellSignal.HOLD


def test_evaluate_all_returns_list():
    # main 9c4ed24: SL -4% — B pnl_rate -4.5 로 SL 트리거
    holdings = [_h(symbol="A", pnl_rate="5.5"), _h(symbol="B", pnl_rate="-4.5"), _h(symbol="C", pnl_rate="1.0")]
    decisions = evaluate_all(holdings)
    assert [d.signal for d in decisions] == [
        SellSignal.TAKE_PROFIT, SellSignal.STOP_LOSS, SellSignal.HOLD,
    ]


def test_custom_policy_overrides():
    """더 보수적인 정책 (TP +3%, SL -1%)."""
    policy = ExitPolicy(take_profit_pct=Decimal("3.0"), stop_loss_pct=Decimal("-1.0"))
    assert evaluate_holding(_h(pnl_rate="3.5"), policy).signal == SellSignal.TAKE_PROFIT
    assert evaluate_holding(_h(pnl_rate="-1.5"), policy).signal == SellSignal.STOP_LOSS
    assert evaluate_holding(_h(pnl_rate="2.0"), policy).signal == SellSignal.HOLD


def test_render_decisions_table():
    decisions = [
        HoldingDecision(
            symbol="005930", name="삼성전자", qty=10,
            avg_buy_price=Decimal("260000"), cur_price=Decimal("276500"),
            pnl=Decimal("165000"), pnl_rate=Decimal("6.35"),
            signal=SellSignal.TAKE_PROFIT, reason="...",
        ),
        HoldingDecision(
            symbol="000660", name="SK하이닉스", qty=5,
            avg_buy_price=Decimal("210000"), cur_price=Decimal("203000"),
            pnl=Decimal("-35000"), pnl_rate=Decimal("-3.33"),
            signal=SellSignal.STOP_LOSS, reason="...",
        ),
    ]
    md = render_decisions_table(decisions)
    assert "✅ TP" in md
    assert "🛑 SL" in md
    assert "삼성전자" in md
    assert "+6.35%" in md
    assert "-3.33%" in md
