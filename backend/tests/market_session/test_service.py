"""
BAR-52 MarketSessionService 테스트 — 24+ 시나리오.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from backend.core.market_session import KST, MarketSessionService
from backend.models.market import Exchange, TradingSession


@pytest.fixture
def svc() -> MarketSessionService:
    return MarketSessionService()


def _kst(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=KST)


# 평일 = 2026-05-06 (수요일)
WEEKDAY = (2026, 5, 6)
SAT = (2026, 5, 9)   # 토
SUN = (2026, 5, 10)  # 일


class TestGetSession:
    """24+ 시간대 매트릭스."""

    def test_closed_before_8am(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 7, 30)) == TradingSession.CLOSED

    def test_nxt_pre_8_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 0)) == TradingSession.NXT_PRE

    def test_nxt_pre_8_29(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 29)) == TradingSession.NXT_PRE

    def test_krx_pre_8_30(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 30)) == TradingSession.KRX_PRE

    def test_krx_pre_8_59(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 8, 59)) == TradingSession.KRX_PRE

    def test_regular_9_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 9, 0)) == TradingSession.REGULAR

    def test_regular_12_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 12, 0)) == TradingSession.REGULAR

    def test_regular_15_19(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 19)) == TradingSession.REGULAR

    def test_closing_auction_15_20(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 20)) == TradingSession.KRX_CLOSING_AUCTION

    def test_closing_auction_15_29(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 29)) == TradingSession.KRX_CLOSING_AUCTION

    def test_interlude_15_30(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 30)) == TradingSession.INTERLUDE

    def test_interlude_15_39(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 39)) == TradingSession.INTERLUDE

    def test_krx_after_15_40(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 15, 40)) == TradingSession.KRX_AFTER

    def test_krx_after_17_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 17, 0)) == TradingSession.KRX_AFTER

    def test_krx_after_17_59(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 17, 59)) == TradingSession.KRX_AFTER

    def test_nxt_after_18_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 18, 0)) == TradingSession.NXT_AFTER

    def test_nxt_after_19_30(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 19, 30)) == TradingSession.NXT_AFTER

    def test_closed_20_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 20, 0)) == TradingSession.CLOSED

    def test_closed_22_00(self, svc):
        assert svc.get_session(_kst(*WEEKDAY, 22, 0)) == TradingSession.CLOSED

    def test_saturday_closed(self, svc):
        assert svc.get_session(_kst(*SAT, 12, 0)) == TradingSession.CLOSED

    def test_sunday_closed(self, svc):
        assert svc.get_session(_kst(*SUN, 12, 0)) == TradingSession.CLOSED

    def test_holiday_closed(self, svc):
        svc.add_holiday(date(2026, 5, 6))
        assert svc.get_session(_kst(*WEEKDAY, 12, 0)) == TradingSession.CLOSED

    def test_naive_datetime_handled(self, svc):
        """tzinfo 없는 datetime → KST 가정."""
        naive = datetime(2026, 5, 6, 10, 0)
        assert svc.get_session(naive) == TradingSession.REGULAR

    def test_default_now_works(self, svc):
        """now=None → datetime.now(KST) 사용."""
        result = svc.get_session()
        assert isinstance(result, TradingSession)

    def test_utc_datetime_converted(self, svc):
        """UTC datetime → KST 자동 변환 (UTC 03:00 = KST 12:00)."""
        from datetime import timezone

        utc_noon_kst = datetime(2026, 5, 6, 3, 0, tzinfo=timezone.utc)  # KST 12:00
        assert svc.get_session(utc_noon_kst) == TradingSession.REGULAR


class TestAvailableExchanges:
    @pytest.mark.parametrize(
        "session,expected",
        [
            (TradingSession.CLOSED, []),
            (TradingSession.NXT_PRE, [Exchange.NXT]),
            (TradingSession.KRX_PRE, [Exchange.KRX, Exchange.NXT]),
            (TradingSession.REGULAR, [Exchange.KRX, Exchange.NXT]),
            (TradingSession.KRX_CLOSING_AUCTION, [Exchange.KRX]),
            (TradingSession.INTERLUDE, []),
            (TradingSession.KRX_AFTER, [Exchange.KRX, Exchange.NXT]),
            (TradingSession.NXT_AFTER, [Exchange.NXT]),
        ],
    )
    def test_available_exchanges(self, svc, session, expected):
        assert svc.available_exchanges(session) == expected


class TestAvailableOrders:
    def test_closed_blocks_all(self, svc):
        orders = svc.available_orders(TradingSession.CLOSED)
        assert orders == {"market": False, "limit": False, "after_hours": False}

    def test_interlude_blocks_all(self, svc):
        orders = svc.available_orders(TradingSession.INTERLUDE)
        assert orders["market"] is False
        assert orders["limit"] is False

    def test_regular_market_and_limit(self, svc):
        orders = svc.available_orders(TradingSession.REGULAR)
        assert orders["market"] is True
        assert orders["limit"] is True
        assert orders["after_hours"] is False

    def test_after_only_limit(self, svc):
        orders = svc.available_orders(TradingSession.KRX_AFTER)
        assert orders["market"] is False
        assert orders["limit"] is True
        assert orders["after_hours"] is True

    def test_closing_auction_only_limit(self, svc):
        orders = svc.available_orders(TradingSession.KRX_CLOSING_AUCTION)
        assert orders["market"] is False
        assert orders["limit"] is True


class TestHoliday:
    def test_add_and_check(self, svc):
        d = date(2026, 12, 25)
        assert svc.is_holiday(d) is False
        svc.add_holiday(d)
        assert svc.is_holiday(d) is True

    def test_remove(self, svc):
        d = date(2026, 12, 25)
        svc.add_holiday(d)
        svc.remove_holiday(d)
        assert svc.is_holiday(d) is False

    def test_initial_holidays(self):
        s = MarketSessionService(holidays={date(2026, 1, 1)})
        assert s.is_holiday(date(2026, 1, 1)) is True


class TestAnalysisContextIntegration:
    """BAR-45 placeholder forward ref 해소 검증."""

    def test_analysis_context_with_session(self, sample_candles):
        from backend.models.market import MarketType
        from backend.models.strategy import AnalysisContext

        ctx = AnalysisContext(
            symbol="005930",
            candles=sample_candles,
            market_type=MarketType.STOCK,
            trading_session=TradingSession.REGULAR,
        )
        assert ctx.trading_session == TradingSession.REGULAR

    def test_analysis_context_without_session(self, sample_candles):
        """trading_session 미지정 → None (기본값)."""
        from backend.models.market import MarketType
        from backend.models.strategy import AnalysisContext

        ctx = AnalysisContext(
            symbol="005930",
            candles=sample_candles,
            market_type=MarketType.STOCK,
        )
        assert ctx.trading_session is None
