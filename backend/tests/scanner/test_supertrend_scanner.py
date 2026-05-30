"""SupertrendScanner 테스트 — 5분봉 추세전환 스캔.

외부 선별 종목 리스트 → 5분봉 fetch → Supertrend 신호 산출 검증.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List

import pytest

from backend.core.scanner.supertrend_scanner import SupertrendScanner
from backend.models.market import OHLCV, MarketType, Ticker


class _FakeGateway:
    """candles 를 주입식으로 반환하는 덕타이핑 테스트 게이트웨이.

    SupertrendScanner 가 실제로 쓰는 멤버(market_type·get_ticker·get_ohlcv)만 구현.
    MarketGateway ABC 를 상속하지 않아 미사용 추상 메서드 구현 부담 없음.
    """

    def __init__(self, candles: List[OHLCV], market: MarketType = MarketType.STOCK):
        self._candles = candles
        self._market = market

    @property
    def market_type(self) -> MarketType:
        return self._market

    async def get_ticker(self, symbol: str) -> Ticker:
        return Ticker(
            symbol=symbol, name=f"종목{symbol}", price=10000.0,
            volume=100000.0, change_pct=1.0,
            timestamp=datetime.now(), market_type=self._market,
        )

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 300) -> List[OHLCV]:
        return self._candles


def _candles(prices: List[float]) -> List[OHLCV]:
    base = datetime(2026, 5, 31, 9, 0)
    out = []
    for i, px in enumerate(prices):
        out.append(OHLCV(
            symbol="005930",
            timestamp=base + timedelta(minutes=5 * i),
            open=px, high=px * 1.005, low=px * 0.995, close=px,
            volume=10000 + i * 100, market_type=MarketType.STOCK,
        ))
    return out


# 하락 40봉 후 급반등 20봉 → 상승 추세전환 시나리오
_UPTREND_FLIP = [10000 - i * 50 for i in range(40)] + [8100 + i * 80 for i in range(20)]
# 지속 하락 → buySignal 없음
_DOWNTREND = [10000 - i * 30 for i in range(60)]


def test_scanner_emits_signal_on_uptrend_flip():
    gw = _FakeGateway(_candles(_UPTREND_FLIP))
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930"]))
    assert len(signals) == 1
    assert signals[0].signal_type == "supertrend"
    assert signals[0].strategy_id == "supertrend_v1"


def test_scanner_no_signal_on_downtrend():
    gw = _FakeGateway(_candles(_DOWNTREND))
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930"]))
    assert signals == []


def test_scanner_skips_empty_candles():
    gw = _FakeGateway([])
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930"]))
    assert signals == []


def test_scanner_sorts_by_score_desc():
    gw = _FakeGateway(_candles(_UPTREND_FLIP))
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930", "000660", "035720"]))
    scores = [s.score for s in signals]
    assert scores == sorted(scores, reverse=True)
