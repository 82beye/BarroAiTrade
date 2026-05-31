"""SupertrendExitWatcher + SupertrendStrategy.exit_on_signal 테스트.

진입(상승전환)의 거울상인 청산(하락전환) 동작 검증.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List

import pytest

from backend.core.scanner.supertrend_exit_watcher import SupertrendExitWatcher
from backend.core.strategy.supertrend import SupertrendStrategy
from backend.models.market import OHLCV, MarketType, Ticker
from backend.models.position import Position
from backend.models.strategy import AnalysisContext


# ─── fixtures ────────────────────────────────────────────────────────────────
def _candles(prices: List[float]) -> List[OHLCV]:
    base = datetime(2026, 5, 31, 9, 0)
    return [
        OHLCV(
            symbol="005930", timestamp=base + timedelta(minutes=5 * i),
            open=p, high=p * 1.005, low=p * 0.995, close=p,
            volume=10000 + i, market_type=MarketType.STOCK,
        )
        for i, p in enumerate(prices)
    ]


# 상승 56봉 후 급락 4봉 → **최근 봉(1봉 전)에 SELL 시그널** 발생 → 청산 대상.
_SELL_RECENT = [10000 + i * 50 for i in range(56)] + [12800 - i * 300 for i in range(4)]
# 상승 40봉 후 급락 20봉 → SELL 은 15봉 전(오래됨), 현재는 하락추세 "지속".
#   trend==-1 이지만 SELL 시그널은 한참 전 → exit_lookback 기준 **미청산** (보유 유지).
#   "하락추세 동안 매봉 청산"이 아니라 "SELL 전환 이벤트 1회"만 트리거함을 검증.
_DOWNTREND_STALE = [10000 + i * 50 for i in range(40)] + [11900 - i * 90 for i in range(20)]
# 지속 상승 → 매도 시그널 없음 (보유 유지)
_UPTREND = [10000 + i * 40 for i in range(60)]


def _position(symbol="005930", strategy_id="supertrend_v1", avg=10000.0, cur=10500.0) -> Position:
    return Position(
        symbol=symbol, name=f"종목{symbol}", quantity=10,
        avg_price=avg, current_price=cur,
        realized_pnl=0.0, unrealized_pnl=0.0, pnl_pct=0.0,
        market_type=MarketType.STOCK, entry_time=datetime(2026, 5, 31, 9, 0),
        strategy_id=strategy_id,
    )


class _FakeGateway:
    """5분봉 candles 주입 덕타이핑 게이트웨이 (scanner 와 동일 패턴)."""

    def __init__(self, candles, market=MarketType.STOCK):
        self._candles = candles
        self.market_type = market
        self.calls = 0

    async def get_ticker(self, symbol: str) -> Ticker:
        return Ticker(symbol=symbol, name="t", price=1.0, volume=1.0,
                      change_pct=0.0, timestamp=datetime.now(), market_type=self.market_type)

    async def get_ohlcv(self, symbol, timeframe, limit=300):
        self.calls += 1
        return self._candles


# ─── exit_on_signal (전략 단위) ─────────────────────────────────────────────
def test_exit_on_signal_fires_on_sell_signal():
    """최근 봉에 SELL 시그널(trend 1→-1 전환) 발생 → 청산."""
    strat = SupertrendStrategy()
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles(_SELL_RECENT), market_type=MarketType.STOCK)
    sig = strat.exit_on_signal(_position(), ctx, Decimal("11000"))
    assert sig is not None
    assert sig.exit_type == "reverse_signal"
    assert sig.symbol == "005930"
    assert "SELL" in sig.reason


def test_exit_on_signal_holds_when_sell_is_stale():
    """하락추세 지속이지만 SELL 시그널은 오래 전(15봉 전) → 미청산.

    '하락추세 동안 매봉 청산'이 아니라 'SELL 전환 이벤트 1회'만 트리거함을 검증.
    """
    strat = SupertrendStrategy()
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles(_DOWNTREND_STALE), market_type=MarketType.STOCK)
    assert strat.exit_on_signal(_position(), ctx, Decimal("9000")) is None


def test_exit_on_signal_holds_on_uptrend():
    strat = SupertrendStrategy()
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles(_UPTREND), market_type=MarketType.STOCK)
    assert strat.exit_on_signal(_position(), ctx, Decimal("12000")) is None


def test_exit_on_signal_respects_exit_lookback():
    """exit_lookback 확대 시 오래된 SELL 도 포착 → 청산 (게이트 동작 확인)."""
    from backend.core.strategy.supertrend import SupertrendParams
    strat = SupertrendParams and SupertrendStrategy(SupertrendParams(exit_lookback=30))
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles(_DOWNTREND_STALE), market_type=MarketType.STOCK)
    sig = strat.exit_on_signal(_position(), ctx, Decimal("9000"))
    assert sig is not None  # since=15 ≤ lookback 30 → 청산


def test_exit_on_signal_pnl_pct_computed():
    strat = SupertrendStrategy()
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles(_SELL_RECENT), market_type=MarketType.STOCK)
    # avg 10000, 현재가 9000 → -10%
    sig = strat.exit_on_signal(_position(avg=10000.0), ctx, Decimal("9000"))
    assert sig is not None
    assert abs(sig.pnl_pct - (-10.0)) < 1e-6


def test_exit_on_signal_insufficient_candles_none():
    strat = SupertrendStrategy()
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles([10000, 9900, 9800]), market_type=MarketType.STOCK)
    assert strat.exit_on_signal(_position(), ctx, Decimal("9800")) is None


def test_base_strategy_exit_on_signal_default_none():
    """다른 전략(default 구현)은 항상 None — 지표 청산 미참여."""
    from backend.core.strategy.blue_line import BlueLineStrategy
    strat = BlueLineStrategy()
    ctx = AnalysisContext(symbol="005930", name="t",
                          candles=_candles(_SELL_RECENT), market_type=MarketType.STOCK)
    assert strat.exit_on_signal(_position(strategy_id="blue_line_v1"), ctx, Decimal("9000")) is None


# ─── watcher (배선 단위) ─────────────────────────────────────────────────────
def test_watcher_emits_exit_on_sell_signal():
    gw = _FakeGateway(_candles(_SELL_RECENT))
    watcher = SupertrendExitWatcher(gw)
    exits = asyncio.run(watcher.check([_position()]))
    assert len(exits) == 1
    assert exits[0].exit_type == "reverse_signal"


def test_watcher_holds_when_sell_stale():
    """SELL 이 오래된 하락추세 지속 → 청산 안 함."""
    gw = _FakeGateway(_candles(_DOWNTREND_STALE))
    watcher = SupertrendExitWatcher(gw)
    assert asyncio.run(watcher.check([_position()])) == []


def test_watcher_holds_on_uptrend():
    gw = _FakeGateway(_candles(_UPTREND))
    watcher = SupertrendExitWatcher(gw)
    assert asyncio.run(watcher.check([_position()])) == []


def test_watcher_ignores_other_strategy_positions():
    """다른 전략 진입분은 평가 대상 제외 — get_ohlcv 호출조차 안 함."""
    gw = _FakeGateway(_candles(_SELL_RECENT))
    watcher = SupertrendExitWatcher(gw)
    exits = asyncio.run(watcher.check([
        _position(symbol="000660", strategy_id="f_zone_v1"),
        _position(symbol="035720", strategy_id="swing_38_v1"),
    ]))
    assert exits == []
    assert gw.calls == 0


def test_watcher_mixed_positions_only_supertrend():
    gw = _FakeGateway(_candles(_SELL_RECENT))
    watcher = SupertrendExitWatcher(gw)
    exits = asyncio.run(watcher.check([
        _position(symbol="005930", strategy_id="supertrend_v1"),
        _position(symbol="000660", strategy_id="gold_zone_v1"),
    ]))
    assert [e.symbol for e in exits] == ["005930"]


def test_watcher_empty_positions():
    gw = _FakeGateway(_candles(_SELL_RECENT))
    watcher = SupertrendExitWatcher(gw)
    assert asyncio.run(watcher.check([])) == []
