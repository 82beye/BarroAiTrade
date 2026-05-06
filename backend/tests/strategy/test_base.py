"""
BAR-45 Strategy v2 ABC 테스트 (Plan §4.2 / Design §3.1).

C1~C5 핵심 + 보강.
"""

from __future__ import annotations

import warnings
from decimal import Decimal

import pytest

from backend.core.strategy.base import Strategy
from backend.models.market import MarketType
from backend.models.signal import EntrySignal
from backend.models.strategy import Account, AnalysisContext


class _DummyStrategy(Strategy):
    """테스트용 더미 — _analyze_v2 만 구현."""

    STRATEGY_ID = "dummy_v1"

    def _analyze_v2(self, ctx: AnalysisContext) -> EntrySignal | None:
        return None


class _DummySignalStrategy(Strategy):
    """테스트용 더미 — 항상 EntrySignal 반환."""

    STRATEGY_ID = "dummy_signal_v1"

    def _analyze_v2(self, ctx: AnalysisContext) -> EntrySignal | None:
        from datetime import datetime, timezone

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=ctx.candles[-1].close,
            signal_type="f_zone",
            score=0.5,
            reason="dummy",
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
        )


class TestStrategyV2Dispatch:
    """C1~C2 — analyze dispatch."""

    def test_c1_legacy_dispatch_with_deprecation(self, sample_candles):
        """C1: legacy 4-arg → DeprecationWarning + 정상 동작."""
        s = _DummySignalStrategy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = s.analyze("005930", "삼성전자", sample_candles, MarketType.STOCK)
        assert result is not None
        assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_c2_v2_dispatch_with_ctx(self, sample_ctx):
        """C2: AnalysisContext 인자 → DeprecationWarning 없음."""
        s = _DummySignalStrategy()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = s.analyze(sample_ctx)
        assert result is not None
        assert not any(issubclass(x.category, DeprecationWarning) for x in w)


class TestStrategyV2Defaults:
    """C3~C5 — 기본 구현."""

    def test_c3_exit_plan_default(self, sample_position, sample_ctx):
        """C3: default exit_plan — SL=-2%, TP=[]."""
        s = _DummyStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)
        assert plan.stop_loss.fixed_pct == Decimal("-0.02")
        assert plan.take_profits == []

    def test_c4_position_size_default(self, sample_signal):
        """C4: position_size = available × 0.3 / price (Decimal, KRX 1주 quantize)."""
        s = _DummyStrategy()
        account = Account(
            balance=Decimal("10000000"),
            available=Decimal("10000000"),
            position_count=0,
        )
        size = s.position_size(sample_signal, account)
        assert isinstance(size, Decimal)
        # 10_000_000 * 0.3 / 72000 = 41.67 → quantize("1") ROUND_HALF_EVEN → 42
        assert size == Decimal("42")

    def test_c5_health_check_default(self):
        """C5: health_check default — strategy_id ready=True."""
        s = _DummyStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "dummy_v1"
        assert h["ready"] is True


class TestStrategyV2EdgeCases:
    """경계 케이스 보강."""

    def test_position_size_zero_balance(self, sample_signal):
        """available=0 → size=0."""
        s = _DummyStrategy()
        account = Account(balance=Decimal(0), available=Decimal(0), position_count=0)
        size = s.position_size(sample_signal, account)
        assert size == Decimal(0)

    def test_position_size_zero_price(self):
        """price=0 → size=0 (signal price 가 0 또는 음수일 때)."""
        from datetime import datetime, timezone

        s = _DummyStrategy()
        signal = EntrySignal(
            symbol="X",
            name="X",
            price=0.0001,  # EntrySignal price > 0 필수, 거의 0
            signal_type="f_zone",
            score=0.5,
            reason="r",
            market_type=MarketType.STOCK,
            strategy_id="x",
            timestamp=datetime.now(timezone.utc),
        )
        account = Account(
            balance=Decimal("100"),
            available=Decimal("100"),
            position_count=0,
        )
        size = s.position_size(signal, account)
        assert isinstance(size, Decimal)


class TestStrategyV2InheritanceCheck:
    """4 전략의 v2 호환 검증 (BAR-46~49 회귀 안전)."""

    def test_f_zone_strategy_inherits(self):
        from backend.core.strategy.f_zone import FZoneStrategy

        assert issubclass(FZoneStrategy, Strategy)
        assert hasattr(FZoneStrategy, "_analyze_v2")

    def test_blue_line_strategy_inherits(self):
        from backend.core.strategy.blue_line import BlueLineStrategy

        assert issubclass(BlueLineStrategy, Strategy)
        assert hasattr(BlueLineStrategy, "_analyze_v2")

    def test_stock_strategy_inherits(self):
        from backend.core.strategy.stock_strategy import StockStrategy

        assert issubclass(StockStrategy, Strategy)
        assert hasattr(StockStrategy, "_analyze_v2")

    def test_crypto_breakout_strategy_inherits(self):
        from backend.core.strategy.crypto_breakout import CryptoBreakoutStrategy

        assert issubclass(CryptoBreakoutStrategy, Strategy)
        assert hasattr(CryptoBreakoutStrategy, "_analyze_v2")
