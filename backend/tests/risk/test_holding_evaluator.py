"""BAR-OPS-20 — holding_evaluator 테스트 (적응형 매도 포함)."""
from __future__ import annotations

from decimal import Decimal

from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.risk.holding_evaluator import (
    ExitPolicy,
    HoldingDecision,
    PositionContext,
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


# ── 기본(레거시) 평가 ──────────────────────────────────────────


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


def test_sell_qty_matches_qty_for_basic():
    """기본 평가: sell_qty = qty (전량)."""
    d = evaluate_holding(_h(pnl_rate="6.0"))
    assert d.sell_qty == d.qty


# ── 트레일링 스톱 ──────────────────────────────────────────────


def test_trailing_stop_triggered():
    """고점 4.5% → 현재 2.5% → 하락폭 2% > 허용 1.5% → TRAILING_STOP."""
    ctx = PositionContext(peak_pnl_rate=4.5, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="2.5"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.TRAILING_STOP
    assert "트레일링" in d.reason


def test_trailing_stop_not_triggered_below_start():
    """고점 2.0% < trailing_start 3% → 트레일링 비활성."""
    ctx = PositionContext(peak_pnl_rate=2.0, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="1.0"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.HOLD


def test_trailing_stop_not_triggered_within_offset():
    """고점 4.0% → 현재 3.0% → 하락폭 1.0% < 허용 1.5% → HOLD."""
    ctx = PositionContext(peak_pnl_rate=4.0, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="3.0"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.HOLD


# ── 브레이크이븐 보호 ─────────────────────────────────────────


def test_breakeven_stop_triggered():
    """고점 2.6% >= BE trigger 2.5% (trailing 3% 미만) → 현재 -0.5% ≤ 0% → BREAKEVEN_STOP."""
    ctx = PositionContext(peak_pnl_rate=2.6, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="-0.5"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.BREAKEVEN_STOP
    assert "브레이크이븐" in d.reason


def test_breakeven_not_triggered_still_positive():
    """고점 2.6% 경험 but 현재 +0.5% > 0% → HOLD."""
    ctx = PositionContext(peak_pnl_rate=2.6, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="0.5"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.HOLD


def test_breakeven_not_triggered_peak_below_trigger():
    """고점 1.5% < BE trigger 2.5% → 브레이크이븐 비활성."""
    ctx = PositionContext(peak_pnl_rate=1.5, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="-0.5"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.HOLD  # 일반 SL까지도 안 닿음


# ── 분할 익절 ──────────────────────────────────────────────────


def test_partial_tp_triggered():
    """수익률 4.0% >= partial_tp 3.5% & < TP 5.0% & 미실행 → PARTIAL_TP."""
    ctx = PositionContext(peak_pnl_rate=4.0, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="4.0"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.PARTIAL_TP
    assert d.sell_qty == 5  # 10 * 0.5 = 5


def test_partial_tp_skipped_if_done():
    """partial_tp_done=True → 분할 익절 스킵, HOLD."""
    ctx = PositionContext(peak_pnl_rate=4.0, partial_tp_done=True, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="4.0"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.HOLD


def test_partial_tp_becomes_full_tp_at_threshold():
    """수익률 5.0% >= TP → 분할이 아닌 전량 TP."""
    ctx = PositionContext(peak_pnl_rate=5.0, entry_time="2026-05-10T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="5.0"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.TAKE_PROFIT
    assert d.sell_qty == d.qty


# ── 시간 기반 SL 강화 ─────────────────────────────────────────


def test_time_tightened_sl():
    """5일 이상 보유 + 수익률 -2.5% → tightened SL -2% 적용 → TIME_TIGHTENED_SL."""
    ctx = PositionContext(peak_pnl_rate=0.5, entry_time="2026-05-01T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="-2.5"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.TIME_TIGHTENED_SL
    assert "보유" in d.reason


def test_time_tightened_sl_not_triggered_within_days():
    """2일 보유 + 수익률 -2.5% → 기본 SL -4% 적용 → HOLD."""
    ctx = PositionContext(peak_pnl_rate=0.5, entry_time="2026-05-12T00:00:00+00:00")
    d = evaluate_holding(_h(pnl_rate="-2.5"), ExitPolicy(), ctx)
    assert d.signal == SellSignal.HOLD


# ── 렌더링 ─────────────────────────────────────────────────────


def test_render_decisions_table():
    decisions = [
        HoldingDecision(
            symbol="005930", name="삼성전자", qty=10, sell_qty=10,
            avg_buy_price=Decimal("260000"), cur_price=Decimal("276500"),
            pnl=Decimal("165000"), pnl_rate=Decimal("6.35"),
            signal=SellSignal.TAKE_PROFIT, reason="...",
        ),
        HoldingDecision(
            symbol="000660", name="SK하이닉스", qty=5, sell_qty=5,
            avg_buy_price=Decimal("210000"), cur_price=Decimal("203000"),
            pnl=Decimal("-35000"), pnl_rate=Decimal("-3.33"),
            signal=SellSignal.STOP_LOSS, reason="...",
        ),
    ]
    md = render_decisions_table(decisions)
    assert "TP" in md
    assert "SL" in md
    assert "삼성전자" in md
    assert "+6.35%" in md
    assert "-3.33%" in md


def test_render_includes_new_signals():
    decisions = [
        HoldingDecision(
            symbol="005930", name="삼성전자", qty=10, sell_qty=5,
            avg_buy_price=Decimal("260000"), cur_price=Decimal("276500"),
            pnl=Decimal("100000"), pnl_rate=Decimal("3.5"),
            signal=SellSignal.PARTIAL_TP, reason="분할 익절",
        ),
        HoldingDecision(
            symbol="000660", name="SK하이닉스", qty=10, sell_qty=10,
            avg_buy_price=Decimal("210000"), cur_price=Decimal("203000"),
            pnl=Decimal("-10000"), pnl_rate=Decimal("2.0"),
            signal=SellSignal.TRAILING_STOP, reason="트레일링",
        ),
    ]
    md = render_decisions_table(decisions)
    assert "P-TP" in md
    assert "TRAIL" in md
