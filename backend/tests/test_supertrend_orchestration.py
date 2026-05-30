"""슈퍼트렌드 orchestrator 배선 테스트 — _supertrend_cycle 진입/청산 통합.

실주문 없음(signal-only) 검증 + watchlist fallback + 보유분 청산 평가.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import List

import pytest

from backend.core.orchestrator import TradingOrchestrator
from backend.core.state import app_state
from backend.models.market import OHLCV, MarketType, Ticker
from backend.models.position import Position


def _candles(prices: List[float]) -> List[OHLCV]:
    base = datetime(2026, 5, 31, 9, 0)
    return [
        OHLCV(symbol="005930", timestamp=base + timedelta(minutes=5 * i),
              open=p, high=p * 1.005, low=p * 0.995, close=p,
              volume=10000 + i, market_type=MarketType.STOCK)
        for i, p in enumerate(prices)
    ]


_UPTREND_FLIP = [10000 - i * 50 for i in range(40)] + [8100 + i * 80 for i in range(20)]
_DOWNTREND_FLIP = [10000 + i * 50 for i in range(40)] + [11900 - i * 90 for i in range(20)]


class _FakeGateway:
    def __init__(self, candles):
        self._candles = candles
        self.market_type = MarketType.STOCK

    async def get_ticker(self, symbol):
        return Ticker(symbol=symbol, name=f"종목{symbol}", price=1.0, volume=1.0,
                      change_pct=0.0, timestamp=datetime.now(), market_type=MarketType.STOCK)

    async def get_ohlcv(self, symbol, timeframe, limit=300):
        return self._candles


class _FakePositionMgr:
    def __init__(self, positions):
        self._p = {p.symbol: p for p in positions}

    def get_positions(self):
        return dict(self._p)


def _position(symbol="005930", strategy_id="supertrend_v1", avg=10000.0, cur=9000.0):
    return Position(
        symbol=symbol, name=f"종목{symbol}", quantity=10, avg_price=avg, current_price=cur,
        realized_pnl=0.0, unrealized_pnl=0.0, pnl_pct=0.0,
        market_type=MarketType.STOCK, entry_time=datetime(2026, 5, 31, 9, 0),
        strategy_id=strategy_id,
    )


def _run(coro):
    return asyncio.run(coro)


def test_cycle_entry_scan_populates_app_state():
    """유니버스(watchlist fallback) 상승전환 → app_state.supertrend_signals 채워짐."""
    app_state.watchlist = ["005930"]
    app_state.supertrend_signals = []
    orch = TradingOrchestrator()
    orch._position_mgr = _FakePositionMgr([])
    gw = _FakeGateway(_candles(_UPTREND_FLIP))
    _run(orch._supertrend_cycle(gw, oauth=None))
    assert len(app_state.supertrend_signals) == 1
    assert app_state.supertrend_signals[0]["symbol"] == "005930"


def test_cycle_exit_eval_does_not_place_orders():
    """보유 슈퍼트렌드 포지션 하락전환 → 청산 평가만, 실주문 호출 없음(signal-only)."""
    app_state.watchlist = ["005930"]
    app_state.supertrend_signals = []
    orch = TradingOrchestrator()
    # 실주문 경로가 호출되면 안 됨 → executor None 유지 + place_order 없는 gateway
    orch._position_mgr = _FakePositionMgr([_position(cur=9000.0)])
    gw = _FakeGateway(_candles(_DOWNTREND_FLIP))
    # 예외 없이 완주해야 함 (실행 경로 미호출)
    _run(orch._supertrend_cycle(gw, oauth=None))
    assert orch._executor is None  # 실행기 미사용


def test_cycle_empty_universe_noop():
    """watchlist 비어있고 oauth None → 아무것도 안 함 (예외 없음)."""
    app_state.watchlist = []
    app_state.supertrend_signals = []
    orch = TradingOrchestrator()
    orch._position_mgr = _FakePositionMgr([])
    gw = _FakeGateway(_candles(_UPTREND_FLIP))
    _run(orch._supertrend_cycle(gw, oauth=None))
    assert app_state.supertrend_signals == []


def test_supertrend_loop_registered_in_task_defs():
    """start() task_defs 에 supertrend 루프가 등록되어 있는지 (소스 수준 확인)."""
    import inspect
    src = inspect.getsource(TradingOrchestrator.start)
    assert '"supertrend"' in src or "'supertrend'" in src


def test_build_native_oauth_returns_none_without_keys(monkeypatch):
    """키 미설정 시 OAuth None (watchlist fallback 보장)."""
    class _S:
        kiwoom_app_key = ""
        kiwoom_app_secret = ""
    monkeypatch.setattr("backend.config.settings.get_settings", lambda: _S())
    assert TradingOrchestrator._build_native_oauth() is None
