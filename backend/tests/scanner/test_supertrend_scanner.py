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


# 하락 57봉 후 급반등 3봉 → **최근 봉(1봉 전)에 BUY 시그널** 발생 → 진입 대상.
_BUY_RECENT = [10000 - i * 50 for i in range(57)] + [7200 + i * 350 for i in range(3)]
# 하락 40봉 후 반등 20봉 → BUY 는 16봉 전(오래됨), 상승추세 "지속" 중.
#   trend==1 이지만 BUY 시그널은 한참 전 → entry_lookback 기준 **미진입**.
#   "상승추세 동안 매봉 매수"가 아니라 "BUY 전환 이벤트 1회"만 트리거함을 검증.
_UPTREND_STALE = [10000 - i * 50 for i in range(40)] + [8100 + i * 80 for i in range(20)]
# 지속 하락 → buySignal 없음
_DOWNTREND = [10000 - i * 30 for i in range(60)]


def test_scanner_emits_signal_on_buy_signal():
    """최근 봉에 BUY 시그널(trend -1→1 전환) 발생 → 진입."""
    gw = _FakeGateway(_candles(_BUY_RECENT))
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930"]))
    assert len(signals) == 1
    assert signals[0].signal_type == "supertrend"
    assert signals[0].strategy_id == "supertrend_v1"


def test_scanner_no_signal_when_buy_is_stale():
    """상승추세 지속이지만 BUY 시그널은 오래 전(16봉 전) → 미진입.

    '상승추세 동안 매봉 매수'가 아니라 'BUY 전환 이벤트 1회'만 트리거함을 검증
    (청산이 SELL 전환 봉에서만 발동하는 것과 대칭).
    """
    gw = _FakeGateway(_candles(_UPTREND_STALE))
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930"]))
    assert signals == []


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
    gw = _FakeGateway(_candles(_BUY_RECENT))
    scanner = SupertrendScanner(gw)
    signals = asyncio.run(scanner.scan(["005930", "000660", "035720"]))
    scores = [s.score for s in signals]
    assert scores == sorted(scores, reverse=True)
