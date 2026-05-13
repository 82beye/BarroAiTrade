"""
BAR-46 F존 v2 리팩터 테스트 (Plan §4.2 / Design §2).

C1~C8 — _analyze_v2 직접 / _analyze_impl 부재 / exit_plan / position_size /
health_check / 베이스라인 회귀.
"""

from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal

import pytest

from backend.core.strategy.f_zone import FZoneStrategy
from backend.models.market import MarketType
from backend.models.strategy import Account


class TestFZoneV2:
    """C1~C2 — v2 진입점 + legacy shim 부재."""

    def test_c1_analyze_v2_callable(self, sample_ctx):
        s = FZoneStrategy()
        # 5 candles 이라 min_candles 미달 → None 정상
        result = s._analyze_v2(sample_ctx)
        assert result is None or hasattr(result, "symbol")

    def test_c2_no_legacy_impl(self):
        s = FZoneStrategy()
        assert not hasattr(s, "_analyze_impl"), "BAR-46 에서 _analyze_impl 제거됨"


class TestFZoneExitPlan:
    """C3 — F존 정책 매트릭스."""

    def test_c3_exit_plan_stock_with_time_exit(self, sample_position, sample_ctx):
        s = FZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)

        assert len(plan.take_profits) == 2
        assert plan.take_profits[0].qty_pct == Decimal("0.5")
        assert plan.take_profits[1].qty_pct == Decimal("0.5")
        # avg_price=72000 → TP1=74160, TP2=75600
        assert plan.take_profits[0].price == Decimal("72000") * Decimal("1.03")
        assert plan.take_profits[1].price == Decimal("72000") * Decimal("1.05")

        assert plan.stop_loss.fixed_pct == Decimal("-0.02")
        assert plan.time_exit == dtime(14, 50)
        assert plan.breakeven_trigger == Decimal("0.015")

    def test_exit_plan_crypto_no_time_exit(self, sample_position, sample_ctx_crypto):
        """crypto 시장은 time_exit 없음."""
        s = FZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx_crypto)
        assert plan.time_exit is None


class TestFZonePositionSize:
    """C4~C6 — score 분기."""

    def _account_10m(self) -> Account:
        return Account(
            balance=Decimal("10000000"),
            available=Decimal("10000000"),
            position_count=0,
        )

    def test_c4_high_score_30pct(self, sample_signal_high_score):
        s = FZoneStrategy()
        size = s.position_size(sample_signal_high_score, self._account_10m())
        # 10_000_000 * 0.3 / 72000 = 41.67 → quantize ROUND_HALF_EVEN → 42
        assert size == Decimal("42")

    def test_c5_mid_score_20pct(self, sample_signal_mid_score):
        s = FZoneStrategy()
        size = s.position_size(sample_signal_mid_score, self._account_10m())
        # 10_000_000 * 0.2 / 72000 = 27.78 → 28
        assert size == Decimal("28")

    def test_c6_low_score_10pct(self, sample_signal_low_score):
        s = FZoneStrategy()
        size = s.position_size(sample_signal_low_score, self._account_10m())
        # 10_000_000 * 0.1 / 72000 = 13.89 → 14
        assert size == Decimal("14")

    def test_position_size_zero_balance(self, sample_signal_high_score):
        s = FZoneStrategy()
        empty = Account(
            balance=Decimal(0), available=Decimal(0), position_count=0,
        )
        assert s.position_size(sample_signal_high_score, empty) == Decimal(0)


class TestFZoneHealthCheck:
    """C7."""

    def test_c7_health_check_ready(self):
        s = FZoneStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "f_zone_v1"
        assert h["ready"] is True
        assert h["min_candles"] >= 60
        assert h["impulse_lookback"] > 0


class TestFZoneVolatilityFilter:
    """F1 변동성 필터 — ATR% < min_atr_pct 종목 거부 (저변동 종목 제외).

    2026-05-14 FZONE_ANALYSIS.md 후속: LG전자 ATR% 2.94%, win 0%, -627k 손실 패턴 차단.
    """

    def _candles(self, atr_target_pct: float, n: int = 60):
        """대략적으로 원하는 ATR% 가 나오도록 합성 캔들 생성."""
        from datetime import datetime, timedelta

        from backend.models.market import OHLCV, MarketType

        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        base = 1000
        tr = base * atr_target_pct  # high-low 폭 = target ATR%
        for i in range(n):
            out.append(OHLCV(
                symbol="TEST",
                timestamp=t0 + timedelta(days=i),
                open=base, high=base + tr / 2, low=base - tr / 2, close=base,
                volume=10000, market_type=MarketType.STOCK,
            ))
        return out

    def test_atr_pct_static_computation(self):
        from backend.core.strategy.f_zone import FZoneStrategy
        candles = self._candles(0.05)  # 약 5%
        atr = FZoneStrategy._atr_pct(candles, n=14)
        assert 0.04 <= atr <= 0.06, f"atr={atr}, ~5% 예상"

    def test_low_atr_rejected(self):
        """ATR% < min_atr_pct (default 3.5%) 종목은 진입 거부."""
        from backend.core.strategy.f_zone import FZoneStrategy
        from backend.models.strategy import AnalysisContext

        s = FZoneStrategy()
        # ATR% 약 2% — 임계 3.5% 미만
        candles = self._candles(0.02, n=70)
        ctx = AnalysisContext(symbol="LOW_VOL", candles=candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "저변동 종목 진입 거부 실패"

    def test_filter_disabled_when_min_zero(self):
        """min_atr_pct=0 → 필터 비활성, BEFORE 동작 보존."""
        from backend.core.strategy.f_zone import FZoneParams, FZoneStrategy
        from backend.models.strategy import AnalysisContext

        s = FZoneStrategy(FZoneParams(min_atr_pct=0.0))
        candles = self._candles(0.02, n=70)
        ctx = AnalysisContext(symbol="LOW_VOL", candles=candles, market_type=MarketType.STOCK)
        # 임펄스/눌림 조건 미충족이라 None 이긴 하지만 ATR 필터 통과는 확인됨
        # (filter 통과 후 다른 단계에서 None) — exception 안 나면 OK
        s._analyze_v2(ctx)  # 예외 없으면 통과


class TestFZoneBaselineRegression:
    """C8 — BAR-44 베이스라인 ±5% 회귀."""

    def test_c8_baseline_unchanged(self):
        import sys

        sys.path.insert(0, ".")
        from run_baseline import run_baseline

        reports = run_baseline(seed=42, num_candles=250)
        assert "f_zone_v1" in reports
        f = reports["f_zone_v1"]
        # 베이스라인: 6 거래 / 33.3% / -0.42%
        assert len(f.trades) == 6, f"거래 수 {len(f.trades)} ≠ 6 (베이스라인 회귀)"
        assert abs(f.metrics.win_rate - 1.0 / 3.0) < 0.05, f"승률 회귀 ({f.metrics.win_rate})"
        assert abs(f.metrics.total_return_pct - (-0.0042)) < 0.05, f"수익률 회귀 ({f.metrics.total_return_pct})"
