"""
BAR-45 Strategy v2 모델 테스트 (Plan §4.2 / Design §3.2).

C6~C11 — AnalysisContext / ExitPlan / Account 검증.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.models.market import MarketType
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)


class TestAnalysisContext:
    """C6~C8."""

    def test_c6_construction_minimal(self, sample_candles):
        """C6: 최소 필드로 생성."""
        ctx = AnalysisContext(
            symbol="005930", candles=sample_candles, market_type=MarketType.STOCK
        )
        assert ctx.symbol == "005930"
        assert ctx.trading_session is None  # placeholder

    def test_c7_empty_candles_rejected(self):
        """C7: candles 빈 리스트 → ValidationError."""
        with pytest.raises(ValidationError):
            AnalysisContext(symbol="005930", candles=[], market_type=MarketType.STOCK)

    def test_c8_from_legacy(self, sample_candles):
        """C8: from_legacy 변환."""
        ctx = AnalysisContext.from_legacy(
            "005930", "삼성전자", sample_candles, MarketType.STOCK
        )
        assert ctx.symbol == "005930"
        assert ctx.name == "삼성전자"

    def test_empty_symbol_rejected(self, sample_candles):
        with pytest.raises(ValidationError):
            AnalysisContext(symbol="", candles=sample_candles, market_type=MarketType.STOCK)


class TestExitPlan:
    """C9~C10."""

    def test_c9_qty_sum_validation(self):
        """C9: take_profits qty_pct 합계 > 1.0 → ValidationError."""
        with pytest.raises(ValidationError):
            ExitPlan(
                take_profits=[
                    TakeProfitTier(price=Decimal(100), qty_pct=Decimal("0.6")),
                    TakeProfitTier(price=Decimal(110), qty_pct=Decimal("0.5")),
                ],
                stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
            )

    def test_c10_stop_loss_positive_rejected(self):
        """C10: SL fixed_pct ≥ 0 → ValidationError."""
        with pytest.raises(ValidationError):
            StopLoss(fixed_pct=Decimal("0.02"))

    def test_qty_sum_within_limit(self):
        """qty_pct 합계 ≤ 1.0 정상."""
        plan = ExitPlan(
            take_profits=[
                TakeProfitTier(price=Decimal(100), qty_pct=Decimal("0.4")),
                TakeProfitTier(price=Decimal(110), qty_pct=Decimal("0.6")),
            ],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
        )
        total = sum(t.qty_pct for t in plan.take_profits)
        assert total == Decimal("1.0")

    def test_breakeven_trigger_decimal_conversion(self):
        """breakeven_trigger float 입력 → Decimal 변환."""
        plan = ExitPlan(
            stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
            breakeven_trigger=0.015,  # float
        )
        assert isinstance(plan.breakeven_trigger, Decimal)
        assert plan.breakeven_trigger == Decimal("0.015")


class TestAccount:
    """C11."""

    def test_c11_negative_balance_rejected(self):
        """C11: balance 음수 → ValidationError."""
        with pytest.raises(ValidationError):
            Account(
                balance=Decimal("-1"),
                available=Decimal(0),
                position_count=0,
            )

    def test_account_decimal_conversion(self):
        """float 입력 → Decimal 변환."""
        a = Account(
            balance=10000000.0,
            available=8000000.0,
            position_count=2,
        )
        assert isinstance(a.balance, Decimal)
        assert a.balance == Decimal("10000000.0")

    def test_position_count_negative_rejected(self):
        with pytest.raises(ValidationError):
            Account(
                balance=Decimal(100),
                available=Decimal(100),
                position_count=-1,
            )


class TestTakeProfitTier:
    """단계 검증."""

    def test_qty_pct_zero_rejected(self):
        with pytest.raises(ValidationError):
            TakeProfitTier(price=Decimal(100), qty_pct=Decimal(0))

    def test_qty_pct_over_one_rejected(self):
        with pytest.raises(ValidationError):
            TakeProfitTier(price=Decimal(100), qty_pct=Decimal("1.1"))

    def test_price_decimal_conversion(self):
        tier = TakeProfitTier(price=72500.0, qty_pct=0.5)  # float 입력
        assert isinstance(tier.price, Decimal)
        assert tier.price == Decimal("72500.0")
