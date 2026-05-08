"""BAR-OPS-29 — pnl_diff 테스트."""
from __future__ import annotations

from decimal import Decimal

from backend.core.gateway.kiwoom_native_account import RealizedPnLEntry
from backend.core.journal.pnl_diff import (
    SymbolDiff,
    compare,
    summarize,
)
from backend.core.journal.simulation_log import SimulationLogEntry


def _sim(symbol: str, pnl: float, trades: int = 1, name: str = "") -> SimulationLogEntry:
    return SimulationLogEntry(
        run_at="2026-05-08T13:00:00", mode="daily",
        symbol=symbol, name=name or symbol, strategy="swing_38",
        candle_count=600, trades=trades, pnl=pnl, win_rate=1.0 if pnl > 0 else 0.0,
        score=0.7, flu_rate=2.0,
    )


def _real(symbol: str, pnl: float, name: str = "") -> RealizedPnLEntry:
    return RealizedPnLEntry(
        date="20260507", symbol=symbol, name=name or symbol,
        qty=10, buy_price=Decimal("100"), sell_price=Decimal("110"),
        pnl=Decimal(str(pnl)), pnl_rate=Decimal("10"),
        commission=Decimal("70"), tax=Decimal("22"),
    )


def test_compare_matches_by_symbol():
    diffs = compare(
        sim_entries=[_sim("005930", 100), _sim("000660", -50)],
        real_entries=[_real("005930", 80), _real("000660", -100)],
    )
    by = {d.symbol: d for d in diffs}
    assert by["005930"].sim_pnl == 100
    assert by["005930"].real_pnl == 80
    assert by["005930"].diff == -20
    assert by["000660"].diff == -50


def test_compare_handles_sim_only_or_real_only():
    diffs = compare(
        sim_entries=[_sim("AAA", 100)],
        real_entries=[_real("BBB", 50)],
    )
    by = {d.symbol: d for d in diffs}
    # sim 만 있는 종목
    assert by["AAA"].sim_pnl == 100
    assert by["AAA"].real_pnl == 0
    # real 만 있는 종목
    assert by["BBB"].sim_pnl == 0
    assert by["BBB"].real_pnl == 50


def test_compare_aggregates_multiple_strategies_per_symbol():
    diffs = compare(
        sim_entries=[
            _sim("005930", 100),
            _sim("005930", 50),     # 전략 다른 같은 종목
        ],
        real_entries=[_real("005930", 120)],
    )
    assert len(diffs) == 1
    d = diffs[0]
    assert d.sim_pnl == 150           # 누적
    assert d.real_pnl == 120
    assert d.diff == -30


def test_bias_detection():
    """양호 / 과대 시뮬 / 과소 시뮬 분류."""
    diffs = compare(
        sim_entries=[
            _sim("GOOD", 1000),         # 양호 (real ≥ 800)
            _sim("OVER", 1000),         # 과대 시뮬 (real < 800)
            _sim("UNDER", -100),        # 과소 시뮬 (real < -120)
            _sim("SAFE_LOSS", -1000),   # 양호 (real ≥ -1200)
        ],
        real_entries=[
            _real("GOOD", 900),                    # 90% ≥ 80% → 양호
            _real("OVER", 100),                    # 10% < 80% → 과대
            _real("UNDER", -1000),                 # < -120 → 과소
            _real("SAFE_LOSS", -1100),             # ≥ -1200 → 양호
        ],
    )
    by = {d.symbol: d for d in diffs}
    assert by["GOOD"].bias == "양호"
    assert by["OVER"].bias == "과대 시뮬"
    assert by["UNDER"].bias == "과소 시뮬"
    assert by["SAFE_LOSS"].bias == "양호"


def test_bias_zero_sim_returns_no_signal():
    diffs = compare(
        sim_entries=[],
        real_entries=[_real("UNEXPECTED", 500)],
    )
    assert diffs[0].bias == "신호 없음"


def test_diff_pct_with_zero_sim_is_none():
    diffs = compare(
        sim_entries=[],
        real_entries=[_real("X", 100)],
    )
    assert diffs[0].diff_pct is None


def test_compare_sorted_by_abs_diff_descending():
    diffs = compare(
        sim_entries=[_sim("A", 0), _sim("B", 0), _sim("C", 0)],
        real_entries=[_real("A", 100), _real("B", -500), _real("C", 50)],
    )
    syms = [d.symbol for d in diffs]
    assert syms == ["B", "A", "C"]


def test_summarize_aggregates():
    diffs = compare(
        sim_entries=[_sim("A", 100), _sim("B", -50)],
        real_entries=[_real("A", 80), _real("B", -100)],
    )
    s = summarize(diffs)
    assert s["n_symbols"] == 2
    assert s["total_sim"] == 50
    assert s["total_real"] == -20
    assert s["total_diff"] == -70
    # A: sim=100, real=80 → 80%=threshold, real ≥ 80 → 양호
    # B: sim=-50, real=-100 → -50*1.2=-60, real(-100) < -60 → 과소 시뮬
    assert s["bias_counts"].get("양호", 0) == 1
    assert s["bias_counts"].get("과소 시뮬", 0) == 1
