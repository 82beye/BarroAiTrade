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
        """단일 종목 스캔 시 1m + 1d 둘 다 fetch 호출 (swing_38 명시 활성).

        Phase D2.1 (2026-05-28) default 가 swing_38=False 로 변경되어 1d fetch 가
        skip 되도록 동작이 바뀜. 본 테스트는 swing_38 활성 케이스를 명시 검증.
        """
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        gw.get_ticker = AsyncMock(return_value=_ticker())
        # intraday 4 strategy 시그널 없음 → swing_38 분기 도달 (daily fetch 호출)
        gw.get_ohlcv = AsyncMock(return_value=_candles(120))

        # Phase D2.1: 단타 default 에서 swing_38 명시 활성으로 본 테스트 의도 보존
        scanner = SignalScanner(gw, enabled_strategies={"swing_38": True})
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


class TestSignalScannerDaytradingOnly:
    """BAR-OPS-09 Phase D2.1 (2026-05-28) — 단타 전용 모드 default.

    활성 (default): sf_zone, f_zone, gold_zone (1·2·6번)
    비활성 (default): blue_line, crypto_breakout, swing_38 (3·4·5번)
    재활성화: enabled_strategies={"swing_38": True} 등 override.
    """

    @pytest.mark.asyncio
    async def test_default_enabled_matrix(self):
        """default — sf/f/gold 활성, blue/crypto/swing_38 비활성."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        scanner = SignalScanner(gw)
        assert scanner.is_enabled("sf_zone") is True
        assert scanner.is_enabled("f_zone") is True
        assert scanner.is_enabled("gold_zone") is True
        assert scanner.is_enabled("blue_line") is False
        assert scanner.is_enabled("crypto_breakout") is False
        assert scanner.is_enabled("swing_38") is False

    @pytest.mark.asyncio
    async def test_gold_zone_registered(self):
        """Phase D2.1 — gold_zone 인스턴스 SignalScanner 에 등록."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        scanner = SignalScanner(gw)
        assert hasattr(scanner, "gold_zone"), "gold_zone 등록 누락"
        assert scanner.gold_zone.STRATEGY_ID == "gold_zone_v1"

    @pytest.mark.asyncio
    async def test_default_swing_38_inactive_skips_daily_fetch(self):
        """default swing_38=False → 1d fetch skip (1m 만 1회 호출)."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        gw.get_ticker = AsyncMock(return_value=_ticker())
        gw.get_ohlcv = AsyncMock(return_value=_candles(120))

        scanner = SignalScanner(gw)
        await scanner.scan(["TEST"])

        # 호출된 timeframe 추출 — 1m 만 있고 1d 없어야 함
        calls = gw.get_ohlcv.call_args_list
        timeframes = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("timeframe"))
            for c in calls
        ]
        assert "1m" in timeframes, f"1m fetch 누락: {timeframes}"
        assert "1d" not in timeframes, f"swing_38 비활성인데 1d fetch 발생: {timeframes}"

    @pytest.mark.asyncio
    async def test_override_swing_38_active_triggers_daily_fetch(self):
        """enabled_strategies={'swing_38': True} 시 1d fetch 발생."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        gw.get_ticker = AsyncMock(return_value=_ticker())
        gw.get_ohlcv = AsyncMock(return_value=_candles(120))

        scanner = SignalScanner(gw, enabled_strategies={"swing_38": True})
        await scanner.scan(["TEST"])

        calls = gw.get_ohlcv.call_args_list
        timeframes = [
            (c.args[1] if len(c.args) > 1 else c.kwargs.get("timeframe"))
            for c in calls
        ]
        assert "1d" in timeframes, f"swing_38 활성인데 1d fetch 없음: {timeframes}"

    @pytest.mark.asyncio
    async def test_all_intraday_disabled_skips_min_fetch(self):
        """모든 intraday 비활성 + swing_38 비활성 시 ohlcv fetch 0회."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        gw.get_ticker = AsyncMock(return_value=_ticker())
        gw.get_ohlcv = AsyncMock(return_value=_candles(120))

        scanner = SignalScanner(
            gw,
            enabled_strategies={
                "sf_zone": False, "f_zone": False, "gold_zone": False,
                "blue_line": False, "crypto_breakout": False, "swing_38": False,
            },
        )
        await scanner.scan(["TEST"])

        assert gw.get_ohlcv.call_count == 0, (
            f"모든 전략 비활성인데 get_ohlcv {gw.get_ohlcv.call_count}회 호출"
        )

    @pytest.mark.asyncio
    async def test_override_merges_with_default(self):
        """override 는 default 와 병합 (지정 안 한 키는 default 유지)."""
        gw = MagicMock()
        gw.market_type = MarketType.STOCK
        # blue_line 만 명시 활성 → 다른 키는 default 유지
        scanner = SignalScanner(gw, enabled_strategies={"blue_line": True})
        assert scanner.is_enabled("sf_zone") is True       # default 유지
        assert scanner.is_enabled("f_zone") is True        # default 유지
        assert scanner.is_enabled("gold_zone") is True     # default 유지
        assert scanner.is_enabled("blue_line") is True     # override
        assert scanner.is_enabled("crypto_breakout") is False
        assert scanner.is_enabled("swing_38") is False
