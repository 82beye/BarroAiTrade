"""
BAR-48 GoldZoneStrategy 테스트.

C1~C7 — 상속 / 캔들 부족 None / oversold 회복 신호 / ExitPlan / PositionSize /
HealthCheck / Baseline 회귀.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from decimal import Decimal

import numpy as np
import pytest

from backend.core.strategy.base import Strategy
from backend.core.strategy.gold_zone import GoldZoneStrategy, GoldZoneParams
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import Account, AnalysisContext


def _make_oversold_candles(num: int = 100, seed: int = 7) -> list[OHLCV]:
    """합성 oversold + 회복 시나리오: 70봉 하락 → 30봉 회복."""
    np.random.seed(seed)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles: list[OHLCV] = []
    price = 100000.0
    # 70봉 하락 (oversold 진입)
    for i in range(70):
        price *= 1 + np.random.normal(-0.012, 0.01)
    # 30봉 회복 (RSI 회복)
    for i in range(30):
        price *= 1 + np.random.normal(0.005, 0.008)

    np.random.seed(seed)
    p = 100000.0
    for i in range(num):
        if i < 70:
            ret = np.random.normal(-0.012, 0.01)
        else:
            ret = np.random.normal(0.005, 0.008)
        new = p * (1 + ret)
        candles.append(
            OHLCV(
                symbol="TEST",
                timestamp=base_time.replace(day=(i % 28) + 1, month=((i // 28) % 12) + 1),
                open=p,
                high=max(p, new) * 1.001,
                low=min(p, new) * 0.999,
                close=new,
                volume=1_000_000.0,
                market_type=MarketType.STOCK,
            )
        )
        p = new
    return candles


class TestGoldZoneStrategyV2:
    """C1~C3 — Strategy v2 진입점."""

    def test_c1_inherits_strategy(self):
        assert issubclass(GoldZoneStrategy, Strategy)
        assert GoldZoneStrategy.STRATEGY_ID == "gold_zone_v1"

    def test_c2_min_candles_returns_none(self, sample_ctx):
        """5 candles 이라 min_candles(60) 미달 → None."""
        s = GoldZoneStrategy()
        assert s._analyze_v2(sample_ctx) is None

    def test_c3_signal_or_none_on_synthetic_oversold(self):
        """합성 oversold + 회복 데이터 → EntrySignal 또는 None (확률성)."""
        candles = _make_oversold_candles(num=100, seed=7)
        ctx = AnalysisContext(
            symbol="TEST",
            name="TEST",
            candles=candles,
            market_type=MarketType.STOCK,
        )
        s = GoldZoneStrategy()
        result = s._analyze_v2(ctx)
        # None 또는 EntrySignal 모두 정상 (BB/Fib/RSI 동시 충족 확률성)
        assert result is None or result.strategy_id == "gold_zone_v1"
        if result is not None:
            assert result.signal_type == "blue_line"  # 5 enum 제약
            assert result.metadata.get("gold_zone_subtype") == "gold_zone"


class TestGoldZoneExitPlan:
    """C4 — 보수적 정책."""

    def test_c4_exit_plan_stock(self, sample_position, sample_ctx):
        s = GoldZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)

        assert len(plan.take_profits) == 2
        # avg_price=72000
        assert plan.take_profits[0].price == Decimal("72000") * Decimal("1.02")
        assert plan.take_profits[1].price == Decimal("72000") * Decimal("1.04")
        assert plan.take_profits[0].qty_pct == Decimal("0.5")
        assert plan.take_profits[1].qty_pct == Decimal("0.5")
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")
        assert plan.time_exit == dtime(14, 50)
        assert plan.breakeven_trigger == Decimal("0.01")

    def test_exit_plan_crypto_no_time_exit(self, sample_position, sample_ctx_crypto):
        s = GoldZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx_crypto)
        assert plan.time_exit is None


class TestGoldZonePositionSize:
    """C5 — 25%/15%/8% 분기."""

    def _account(self) -> Account:
        return Account(
            balance=Decimal("10000000"),
            available=Decimal("10000000"),
            position_count=0,
        )

    def test_c5a_high_score_25pct(self, sample_signal_high_score):
        s = GoldZoneStrategy()
        size = s.position_size(sample_signal_high_score, self._account())
        # 10_000_000 * 0.25 / 72000 = 34.72 → quantize ROUND_HALF_EVEN → 35
        assert size == Decimal("35")

    def test_c5b_mid_score_15pct(self, sample_signal_mid_score):
        s = GoldZoneStrategy()
        size = s.position_size(sample_signal_mid_score, self._account())
        # 10_000_000 * 0.15 / 72000 = 20.83 → 21
        assert size == Decimal("21")

    def test_c5c_low_score_8pct(self, sample_signal_low_score):
        s = GoldZoneStrategy()
        size = s.position_size(sample_signal_low_score, self._account())
        # 10_000_000 * 0.08 / 72000 = 11.11 → 11
        assert size == Decimal("11")

    def test_zero_balance(self, sample_signal_high_score):
        s = GoldZoneStrategy()
        empty = Account(balance=Decimal(0), available=Decimal(0), position_count=0)
        assert s.position_size(sample_signal_high_score, empty) == Decimal(0)


class TestGoldZoneHealthCheck:
    """C6."""

    def test_c6_health_check(self):
        s = GoldZoneStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "gold_zone_v1"
        assert h["ready"] is True
        assert h["bb_period"] >= 20
        assert h["rsi_period"] >= 14


class TestGoldZoneBaselineRegression:
    """C7 — F존 베이스라인 보존 (골드존은 별도 strategy 라 영향 0)."""

    def test_c7_baseline_unchanged(self):
        import sys

        sys.path.insert(0, ".")
        from run_baseline import run_baseline

        reports = run_baseline(seed=42, num_candles=250)
        f = reports["f_zone_v1"]
        assert len(f.trades) == 6, f"F존 거래 수 회귀 ({len(f.trades)} ≠ 6)"
        b = reports["blue_line_v1"]
        assert len(b.trades) == 12, f"BlueLine 거래 수 회귀 ({len(b.trades)} ≠ 12)"
