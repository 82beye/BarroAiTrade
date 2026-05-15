"""P2 (갭4) — PortfolioSimulator 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from backend.core.backtester import (
    PortfolioResult,
    PortfolioSimulator,
    compute_metrics,
)
from backend.core.backtester.portfolio_simulator import _realize_pnl
from backend.models.market import MarketType, OHLCV


def _candles(symbol: str, n: int = 80, base: float = 70000, start_min: int = 0) -> list[OHLCV]:
    """단순 상승 추세 합성 데이터 (test_intraday_simulator 패턴)."""
    out = []
    t0 = datetime(2026, 5, 8, 9, 0)
    for i in range(n):
        delta = (i % 10 - 4) * 50 + i * 30
        c = base + delta
        out.append(OHLCV(
            symbol=symbol,
            timestamp=t0 + timedelta(minutes=i + start_min),
            open=c, high=c + 100, low=c - 80, close=c + 20,
            volume=10000 + i * 100, market_type=MarketType.STOCK,
        ))
    return out


class _SigStub:
    """_allocate 단위 테스트용 — EntrySignal 의 price/score 만 모킹."""

    def __init__(self, price: float, score: float) -> None:
        self.price = price
        self.score = score


def _assert_invariants(result: PortfolioResult) -> None:
    """모든 run() 결과가 만족해야 하는 공통 불변식."""
    assert all(c >= 0 for _, c in result.cash_curve), "cash < 0 발생"
    assert len(result.equity_curve) == result.timeline_length
    assert len(result.cash_curve) == result.timeline_length
    sym_sum = sum(result.pnl_by_symbol.values(), Decimal("0"))
    strat_sum = sum(result.pnl_by_strategy.values(), Decimal("0"))
    assert sym_sum == strat_sum == result.metrics.total_pnl
    # cash 흐름 불변식 — 미청산 0 이면 final == initial + total_pnl
    if result.open_positions_count == 0:
        assert (
            result.final_capital
            == result.initial_capital + result.metrics.total_pnl
        )


# ─── __init__ / 입력 검증 ──────────────────────────────────────


def test_init_invalid_capital():
    with pytest.raises(ValueError, match="initial_capital"):
        PortfolioSimulator(Decimal("0"))


def test_empty_input_raises():
    sim = PortfolioSimulator(Decimal("10000000"))
    with pytest.raises(ValueError, match="empty"):
        sim.run({}, strategies=["f_zone"])


# ─── _build_timeline ──────────────────────────────────────────


def test_build_timeline_union():
    """두 종목 타임스탬프가 어긋나도 합집합 정렬."""
    a = _candles("A", n=10, start_min=0)
    b = _candles("B", n=10, start_min=5)  # 5분 어긋남
    timeline = PortfolioSimulator._build_timeline({"A": a, "B": b})
    # union: 0~9 ∪ 5~14 = 0~14 → 15개
    assert len(timeline) == 15
    assert timeline == sorted(timeline)


def test_build_timeline_identical():
    """동일 타임스탬프 종목 → 중복 제거."""
    a = _candles("A", n=10)
    b = _candles("B", n=10)
    timeline = PortfolioSimulator._build_timeline({"A": a, "B": b})
    assert len(timeline) == 10


# ─── run() 기본 동작 ──────────────────────────────────────────


def test_run_returns_portfolio_result():
    sim = PortfolioSimulator(Decimal("10000000"), warmup_candles=15)
    result = sim.run({"A": _candles("A", 80)}, strategies=["f_zone"])
    assert isinstance(result, PortfolioResult)
    assert result.initial_capital == Decimal("10000000")
    assert result.symbols == ["A"]
    assert result.timeline_length == 80
    _assert_invariants(result)


def test_run_multi_symbol():
    sim = PortfolioSimulator(Decimal("10000000"), warmup_candles=15)
    result = sim.run(
        {"A": _candles("A", 80), "B": _candles("B", 80, base=50000)},
        strategies=["f_zone", "gold_zone"],
    )
    assert set(result.symbols) == {"A", "B"}
    _assert_invariants(result)


def test_equity_curve_matches_timeline():
    sim = PortfolioSimulator(Decimal("10000000"), warmup_candles=15)
    result = sim.run({"A": _candles("A", 60)}, strategies=["f_zone"])
    assert len(result.equity_curve) == 60
    # equity_curve timestamp 는 오름차순
    ts = [t for t, _ in result.equity_curve]
    assert ts == sorted(ts)


def test_cash_never_negative_multi():
    sim = PortfolioSimulator(Decimal("5000000"), warmup_candles=15)
    result = sim.run(
        {f"S{i}": _candles(f"S{i}", 80, base=40000 + i * 5000) for i in range(5)},
        strategies=["f_zone", "gold_zone", "swing_38"],
    )
    _assert_invariants(result)


def test_warmup_excludes_short_symbol():
    """warmup+1 미만 캔들 종목은 진입 신호 평가 안 됨 (예외 없이 완료)."""
    sim = PortfolioSimulator(Decimal("10000000"), warmup_candles=31)
    # 32봉 — warmup 31, idx 최대 31 → idx >= warmup 인 t 가 1개뿐
    result = sim.run({"A": _candles("A", 32)}, strategies=["f_zone"])
    assert result.timeline_length == 32
    _assert_invariants(result)


def test_metrics_integration():
    sim = PortfolioSimulator(Decimal("10000000"), warmup_candles=15)
    result = sim.run({"A": _candles("A", 80)}, strategies=["gold_zone"])
    recomputed = compute_metrics(result.trades)
    assert result.metrics.total_pnl == recomputed.total_pnl
    assert result.metrics.total_trades == recomputed.total_trades


def test_pnl_consistency_multi():
    sim = PortfolioSimulator(Decimal("20000000"), warmup_candles=15)
    result = sim.run(
        {"A": _candles("A", 100), "B": _candles("B", 100, base=30000)},
        strategies=["f_zone", "sf_zone", "gold_zone", "swing_38"],
    )
    _assert_invariants(result)


# ─── _allocate 단위 테스트 ────────────────────────────────────


def test_allocate_equal_split():
    """후보 3개 → 진입가능액 균등 분배 (per_slot < max_per)."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.5"),   # max_per = 5M
        max_total_position=Decimal("0.9"),  # available = 9M
    )
    ranked = [
        ("A", ("f_zone", _SigStub(1000, 0.9))),
        ("B", ("f_zone", _SigStub(1000, 0.8))),
        ("C", ("f_zone", _SigStub(1000, 0.7))),
    ]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    assert len(alloc) == 3
    # per_slot = 9M / 3 = 3M < max_per 5M
    assert all(v == Decimal("3000000") for v in alloc.values())


def test_allocate_max_per_cap():
    """후보 1개여도 per_slot 이 max_per 초과하면 max_per 로 캡."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.1"),    # max_per = 1M
        max_total_position=Decimal("0.9"),  # available = 9M
    )
    ranked = [("A", ("f_zone", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    assert alloc["A"] == Decimal("1000000")  # min(1M, 9M) = 1M


def test_allocate_max_concurrent():
    """free_slots = max_concurrent - occupied 만큼만 배분."""
    sim = PortfolioSimulator(Decimal("10000000"), max_concurrent=2)
    ranked = [
        ("A", ("f_zone", _SigStub(1000, 0.9))),
        ("B", ("f_zone", _SigStub(1000, 0.8))),
        ("C", ("f_zone", _SigStub(1000, 0.7))),
    ]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    assert len(alloc) <= 2
    # score 상위 A, B 가 우선 (ranked 순서대로 eligible)
    assert set(alloc.keys()) <= {"A", "B"}


def test_allocate_concurrent_full():
    """이미 max_concurrent 만큼 보유 중 → 신규 배분 0."""
    sim = PortfolioSimulator(Decimal("10000000"), max_concurrent=2)
    ranked = [("A", ("f_zone", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 2)
    assert alloc == {}


def test_allocate_cash_guard():
    """cash 가 available 보다 작으면 cash 한도로 배분 제한."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.5"),
    )
    ranked = [("A", ("f_zone", _SigStub(1000, 0.9)))]
    # cash 만 500k — available(9M)·max_per(5M) 보다 작음
    alloc = sim._allocate(ranked, Decimal("500000"), Decimal("0"), 0)
    assert alloc["A"] == Decimal("500000")


def test_allocate_position_value_reduces_available():
    """현재 보유 평가액이 available 을 줄임."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.5"),
        max_total_position=Decimal("0.9"),  # max_total = 9M
    )
    ranked = [("A", ("f_zone", _SigStub(1000, 0.9)))]
    # 이미 8.5M 보유 → available = 9M - 8.5M = 0.5M
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("8500000"), 0)
    assert alloc["A"] == Decimal("500000")


def test_allocate_no_room():
    """보유 평가액이 max_total 초과 → available 0 → 배분 없음."""
    sim = PortfolioSimulator(Decimal("10000000"), max_total_position=Decimal("0.9"))
    ranked = [("A", ("f_zone", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("9500000"), 0)
    assert alloc == {}


# ─── _realize_pnl ─────────────────────────────────────────────


def test_realize_pnl_no_cost():
    """수수료·세금 0 → pnl = gross."""
    pnl, comm, tax = _realize_pnl(
        Decimal("100"), Decimal("110"), Decimal("10"),
        Decimal("0"), Decimal("0"),
    )
    assert pnl == Decimal("100")  # (110-100)*10
    assert comm == Decimal("0")
    assert tax == Decimal("0")


def test_realize_pnl_with_cost():
    """수수료·세금 차감 — IntradaySimulator 공식과 동일."""
    # entry 100, exit 110, qty 10, comm 0.0015, tax 0.0018
    pnl, comm, tax = _realize_pnl(
        Decimal("100"), Decimal("110"), Decimal("10"),
        Decimal("0.0015"), Decimal("0.0018"),
    )
    # gross = 100, comm = (110+100)*10*0.0015 = 3.15, tax = 110*10*0.0018 = 1.98
    assert comm == Decimal("3.15")
    assert tax == Decimal("1.98")
    assert pnl == Decimal("100") - Decimal("3.15") - Decimal("1.98")


def test_realize_pnl_loss():
    """손실 거래도 정상 — 음수 pnl."""
    pnl, _, _ = _realize_pnl(
        Decimal("100"), Decimal("95"), Decimal("10"),
        Decimal("0"), Decimal("0"),
    )
    assert pnl == Decimal("-50")


# ─── P3 갭8 — 양방향 슬리피지 ──────────────────────────────────


def test_exit_slippage_invariant():
    """청산 슬리피지 — slippage>0 시뮬도 cash 흐름 불변식 유지.

    청산가 슬리피지가 cash/pnl/TradeRecord 중 한 곳이라도 누락되면
    _assert_invariants 의 final == initial + total_pnl 이 깨진다.
    """
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15, slippage_pct=0.5,
    )
    result = sim.run({"A": _candles("A", 100)}, strategies=["gold_zone"])
    _assert_invariants(result)


def test_exit_slippage_lowers_sell_price():
    """slippage>0 → sell 체결가가 tp1 raw(entry*1.03) 보다 낮음."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15, slippage_pct=1.0,
    )
    result = sim.run({"A": _candles("A", 120)}, strategies=["gold_zone"])
    for i, t in enumerate(result.trades):
        if t.side == "sell" and t.reason == "tp1":
            buy = next(b for b in reversed(result.trades[:i]) if b.side == "buy")
            assert t.price == buy.price * Decimal("1.03") * Decimal("0.99")
            break


# ─── strategy_weights — 전략별 자금 비중 ──────────────────────


def test_strategy_weight_halves_slot():
    """weight 0.5 → 그 전략 slot 이 다른 전략 대비 절반."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.5"),    # max_per = 5M
        max_total_position=Decimal("0.9"),  # available = 9M
        strategy_weights={"swing_38": 0.5},
    )
    ranked = [
        ("A", ("swing_38", _SigStub(1000, 0.9))),
        ("B", ("f_zone", _SigStub(1000, 0.8))),
    ]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    # per_slot = 9M / 2 = 4.5M, max_per 5M → base_slot = 4.5M
    # A (swing_38 weight 0.5) → 2.25M, B (f_zone weight 1.0) → 4.5M
    assert alloc["A"] == Decimal("2250000")
    assert alloc["B"] == Decimal("4500000")


def test_strategy_weight_zero_excludes():
    """weight 0 → 진입 제외 (alloc 에 없음)."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        strategy_weights={"swing_38": 0.0},
    )
    ranked = [("A", ("swing_38", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    assert "A" not in alloc


def test_strategy_weight_default_one():
    """미지정 전략은 weight 1.0 — 기본 동작 보존."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.5"),
        strategy_weights={"swing_38": 0.5},  # f_zone 미지정 → 1.0
    )
    ranked = [("A", ("f_zone", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    # per_slot = 9M, max_per 5M → 5M * 1.0 = 5M
    assert alloc["A"] == Decimal("5000000")


def test_strategy_weight_invariant():
    """weight 적용 시뮬도 cash 흐름 불변식 유지."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15,
        strategy_weights={"gold_zone": 0.3},
    )
    result = sim.run({"A": _candles("A", 100)}, strategies=["gold_zone"])
    _assert_invariants(result)


def test_strategy_weight_above_one_capped_by_max_per():
    """weight > 1.0 — slot 은 max_per 로 cap (종목당 한도 보존)."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.1"),    # max_per = 1M
        max_total_position=Decimal("0.9"),  # available = 9M
        strategy_weights={"gold_zone": 1.5},
    )
    ranked = [("A", ("gold_zone", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    # base_slot = min(1M, 9M, ...) = 1M. slot = min(1M*1.5, 1M) = 1M.
    # weight 1.5 가 max_per 1M 에서 cap — 종목당 한도 깨짐 방지.
    assert alloc["A"] == Decimal("1000000")


def test_strategy_universe_empty_excludes_all():
    """모든 전략 universe 가 빈 set → 거래 0 (analyze() 호출 안 함)."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15,
        strategy_universe={"f_zone": set(), "gold_zone": set(), "sf_zone": set(),
                           "swing_38": set(), "scalping_consensus": set()},
    )
    result = sim.run({"A": _candles("A", 80)}, strategies=["f_zone", "gold_zone"])
    assert len(result.trades) == 0


def test_dynamic_universe_default_disabled():
    """dynamic_universe=False (default) — 시점별 필터 미적용 (회귀 안전)."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15, dynamic_universe=False,
    )
    result = sim.run({"A": _candles("A", 80)}, strategies=["gold_zone"])
    _assert_invariants(result)


def test_dynamic_universe_enabled_invariant():
    """dynamic_universe=True — 시뮬 정상 완료 + cash 불변식."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15,
        dynamic_universe=True, universe_lookback=15,
    )
    result = sim.run({"A": _candles("A", 100)}, strategies=["gold_zone"])
    _assert_invariants(result)


def test_strategy_universe_none_unrestricted():
    """strategy_universe=None → 모든 종목 모든 전략 (기존 동작 보존)."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15, strategy_universe=None,
    )
    result = sim.run({"A": _candles("A", 80)}, strategies=["gold_zone"])
    _assert_invariants(result)


def test_f_zone_atr_exit_flag_invariant():
    """PortfolioSimulator(f_zone_atr_exit=True) — 정상 완료 + cash 흐름 불변식."""
    sim = PortfolioSimulator(
        Decimal("10000000"), warmup_candles=15, f_zone_atr_exit=True,
    )
    result = sim.run({"A": _candles("A", 100)}, strategies=["f_zone"])
    _assert_invariants(result)


def test_strategy_weight_below_one_unaffected_by_cap():
    """weight < 1.0 — per_slot 이 max_per 보다 커도 cap 에 안 닿음 (정상 축소)."""
    sim = PortfolioSimulator(
        Decimal("10000000"),
        max_per_position=Decimal("0.5"),    # max_per = 5M
        max_total_position=Decimal("0.9"),  # available = 9M
        strategy_weights={"swing_38": 0.3},
    )
    # 단일 후보 → per_slot = 9M (max_per 5M 보다 큼)
    ranked = [("A", ("swing_38", _SigStub(1000, 0.9)))]
    alloc = sim._allocate(ranked, Decimal("10000000"), Decimal("0"), 0)
    # base_slot = min(5M, 9M, ...) = 5M. slot = min(5M * 0.3, 5M) = 1.5M.
    # weight 0.3 정상 축소 — per_slot 이 커도 cap 에 무력화 안 됨.
    assert alloc["A"] == Decimal("1500000")
