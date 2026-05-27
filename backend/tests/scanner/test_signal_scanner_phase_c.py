"""BAR-OPS-09 Phase C (2026-05-27) — SignalScanner 전략별 timeframe 매트릭스."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.scanner import SignalScanner
from backend.core.strategy.swing_38 import Swing38Params
from backend.models.market import MarketType, OHLCV


def _ticker():
    t = MagicMock()
    t.name = "테스트"
    return t


def _candles(n: int, interval_minutes: int = 1):
    """interval_minutes: 1=1분봉, 1440=1일봉."""
    t0 = datetime(2026, 5, 1, 9, 0)
    return [
        OHLCV(
            symbol="TEST", timestamp=t0 + timedelta(minutes=i * interval_minutes),
            open=1000, high=1010, low=990, close=1000,
            volume=10000, market_type=MarketType.STOCK,
        )
        for i in range(n)
    ]


class TestSignalScannerTimeframeMatrix:
    """SignalScanner 가 전략별로 다른 timeframe 호출 검증."""

    @pytest.mark.asyncio
    async def test_default_timeframes_1m_and_1d(self):
        """default — intraday timeframe=1m, daily timeframe=1d."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        gw.get_ticker = AsyncMock(return_value=_ticker())
        gw.get_ohlcv = AsyncMock(return_value=_candles(120))

        scanner = SignalScanner(gw)
        assert scanner.timeframe == "1m"
        assert scanner.daily_timeframe == "1d"
        assert hasattr(scanner, "swing_38"), "swing_38 등록 누락"

    @pytest.mark.asyncio
    async def test_scan_calls_get_ohlcv_for_both_timeframes(self):
        """단일 종목 스캔 시 1m + 1d 둘 다 fetch 호출."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        gw.get_ticker = AsyncMock(return_value=_ticker())
        # intraday 4 strategy 시그널 없음 → swing_38 분기 도달 (daily fetch 호출)
        gw.get_ohlcv = AsyncMock(return_value=_candles(120))

        scanner = SignalScanner(gw)
        await scanner.scan(["TEST"])

        # get_ohlcv 가 2번 호출되어야 (1m, 1d)
        calls = gw.get_ohlcv.call_args_list
        timeframes = [c.args[1] if len(c.args) > 1 else c.kwargs.get("timeframe") for c in calls]
        assert "1m" in str(timeframes) or "1m" in [c[0][1] for c in calls if len(c[0]) > 1]
        assert "1d" in str(timeframes) or "1d" in [c[0][1] for c in calls if len(c[0]) > 1]

    @pytest.mark.asyncio
    async def test_swing_38_requires_daily_candles_via_params(self):
        """swing_38 params 가 require_daily_candles=True default (Phase D2 max_hold=20)."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        scanner = SignalScanner(gw)
        assert scanner.swing_38.params.require_daily_candles is True
        assert scanner.swing_38.params.min_hold_days == 3
        assert scanner.swing_38.params.max_hold_days == 20

    @pytest.mark.asyncio
    async def test_custom_swing_38_params_override(self):
        """swing_38_params override 가능."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        custom = Swing38Params(min_hold_days=5, max_hold_days=10)
        scanner = SignalScanner(gw, swing_38_params=custom)
        assert scanner.swing_38.params.min_hold_days == 5
        assert scanner.swing_38.params.max_hold_days == 10
