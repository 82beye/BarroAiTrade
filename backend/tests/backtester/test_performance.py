"""performance.py — 성과 지표 + equity curve (P1 갭2/갭6)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from backend.core.backtester import PerformanceMetrics, compute_metrics
from backend.core.backtester.intraday_simulator import TradeRecord


def _sell(pnl: str, day: int = 1, hour: int = 9) -> TradeRecord:
    return TradeRecord(
        strategy_id="x", symbol="y", side="sell",
        qty=Decimal("10"), price=Decimal("100"),
        timestamp=datetime(2026, 5, day, hour, 0),
        reason="tp1", pnl=Decimal(pnl),
    )


def _buy(day: int = 1) -> TradeRecord:
    return TradeRecord(
        strategy_id="x", symbol="y", side="buy",
        qty=Decimal("10"), price=Decimal("100"),
        timestamp=datetime(2026, 5, day, 9, 0), reason="entry",
    )


def test_empty_trades():
    m = compute_metrics([])
    assert isinstance(m, PerformanceMetrics)
    assert m.total_trades == 0
    assert m.total_pnl == Decimal("0")
    assert m.equity_curve == []


def test_only_buys_excluded():
    """buy 거래는 집계 제외 (side == 'sell' 만)."""
    m = compute_metrics([_buy(), _buy()])
    assert m.total_trades == 0


def test_win_loss_mix():
    # +100, -40, +60, -20 → total +100, win 2 / loss 2
    trades = [_sell("100", 1, 9), _sell("-40", 1, 10),
              _sell("60", 1, 11), _sell("-20", 1, 12)]
    m = compute_metrics(trades)
    assert m.total_trades == 4
    assert m.win_trades == 2
    assert m.lose_trades == 2
    assert m.win_rate == 0.5
    assert m.total_pnl == Decimal("100")
    assert m.avg_pnl == Decimal("25")
    assert m.avg_win == Decimal("80")     # (100+60)/2
    assert m.avg_loss == Decimal("-30")   # (-40-20)/2
    assert abs(m.profit_factor - 160 / 60) < 1e-6


def test_equity_curve_and_mdd():
    # +100, -40, +60, -20 → curve [100, 60, 120, 100]
    # peak: 100, 100, 120, 120 → dd: 0, 40, 0, 20 → MDD 40, pct 0.4
    trades = [_sell("100", 1, 9), _sell("-40", 1, 10),
              _sell("60", 1, 11), _sell("-20", 1, 12)]
    m = compute_metrics(trades)
    assert m.equity_curve == [
        Decimal("100"), Decimal("60"), Decimal("120"), Decimal("100"),
    ]
    assert m.max_drawdown == Decimal("40")
    assert abs(m.max_drawdown_pct - 0.4) < 1e-6


def test_profit_factor_no_loss():
    m = compute_metrics([_sell("100"), _sell("50")])
    assert m.profit_factor == float("inf")


def test_profit_factor_all_loss():
    m = compute_metrics([_sell("-100"), _sell("-50")])
    assert m.profit_factor == 0.0


def test_period_filter():
    """갭6 — period 로 대상 기간 거래만 집계."""
    trades = [
        _sell("100", 1), _sell("-30", 1),
        _sell("200", 2), _sell("-50", 2),
    ]
    m_all = compute_metrics(trades)
    assert m_all.total_trades == 4
    assert m_all.total_pnl == Decimal("220")

    m_d1 = compute_metrics(trades, period=(date(2026, 5, 1), date(2026, 5, 1)))
    assert m_d1.total_trades == 2
    assert m_d1.total_pnl == Decimal("70")

    m_d2 = compute_metrics(trades, period=(date(2026, 5, 2), date(2026, 5, 2)))
    assert m_d2.total_trades == 2
    assert m_d2.total_pnl == Decimal("150")


def test_sharpe_single_trade_zero():
    """거래 2건 미만 → Sharpe 0."""
    m = compute_metrics([_sell("100")])
    assert m.sharpe_ratio == 0.0


def test_sharpe_nonzero_for_varied_returns():
    """변동 있는 다거래 → Sharpe 계산됨 (0 아님)."""
    trades = [_sell(p, 1, 9 + i) for i, p in enumerate(["50", "-20", "80", "-10", "40"])]
    m = compute_metrics(trades)
    assert m.sharpe_ratio != 0.0
