"""슈퍼트렌드 주문 배선 (진입+청산) 테스트 — 2026-06-01.

signal-only → 실주문 승격 배선 검증:
  1) 진입: BUY 신호 → 사이징(예수금 8%/종목) → executor.submit 매수 송출,
     strategy_id 가 _pending_strategy_ids 에 기록되고 체결 시 포지션에 부여.
  2) 진입 가드: 미보유만, 동시 상한 10, 중복 송출 방지, 배분금<1주 스킵.
  3) 청산: SELL 신호 → 보유분 _execute_exit 전량 청산 호출.
  4) strategy_id 전파: _on_order_filled 가 BUY 체결 포지션에 strategy_id 부여
     (SupertrendExitWatcher 가 청산 대상 식별 가능).
  5) 토글: _SUPERTREND_AUTO_TRADE=False 면 주문 없이 알림만(기존 동작).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from backend.core import orchestrator as orch_mod
from backend.core.orchestrator import TradingOrchestrator
from backend.models.market import MarketType
from backend.models.position import (
    Balance, Order, OrderResult, OrderSide, OrderStatus, Position,
)
from backend.models.signal import EntrySignal, ExitSignal


# ── 가짜 협력자 ──────────────────────────────────────────────────────────────
class _FakeExecutor:
    """submit 호출을 기록하고, 즉시 체결 OrderResult 를 반환."""
    def __init__(self, fail: bool = False):
        self.submitted: List[Order] = []
        self.fail = fail
        self.on_filled = None

    async def submit(self, order, gateway, risk_engine, positions, balance):
        self.submitted.append(order)
        if self.fail:
            return None
        result = OrderResult(
            order_id=f"OID_{order.symbol}",
            symbol=order.symbol,
            side=order.side,
            status=OrderStatus.FILLED,
            filled_quantity=order.quantity,
            avg_price=order.price or 0.0,
            market_type=order.market_type,
            timestamp=datetime.now(),
        )
        return result


class _FakePositionMgr:
    def __init__(self, balance: Balance, positions: Optional[Dict[str, Position]] = None):
        self._balance = balance
        self._positions = positions or {}
        self.filled_calls: List[tuple] = []

    def get_balance(self): return self._balance
    def get_positions(self): return dict(self._positions)
    def has_position(self, sym): return sym in self._positions
    def on_order_filled(self, result, name="", strategy_id=""):
        self.filled_calls.append((result.symbol, result.side, strategy_id))
        if result.side == OrderSide.BUY:
            self._positions[result.symbol] = Position(
                symbol=result.symbol, name=name or result.symbol,
                quantity=result.filled_quantity, avg_price=result.avg_price,
                current_price=result.avg_price, realized_pnl=0.0,
                unrealized_pnl=0.0, pnl_pct=0.0, market_type=result.market_type,
                entry_time=datetime.now(), strategy_id=strategy_id or result.order_id,
            )


def _balance(cash=10_000_000.0):
    return Balance(total_value=cash, available_cash=cash, invested_value=0.0,
                   total_pnl=0.0, total_pnl_pct=0.0, market_type=MarketType.STOCK,
                   updated_at=datetime.now())


def _entry(symbol, score=8.0, price=70000.0):
    return EntrySignal(symbol=symbol, name=symbol, price=price, signal_type="supertrend",
                       score=score, reason="t", market_type=MarketType.STOCK,
                       strategy_id="supertrend_v1", timestamp=datetime.now())


def _pos(symbol, strategy_id="supertrend_v1", qty=10.0, avg=70000.0):
    return Position(symbol=symbol, name=symbol, quantity=qty, avg_price=avg,
                    current_price=avg, realized_pnl=0.0, unrealized_pnl=0.0,
                    pnl_pct=0.0, market_type=MarketType.STOCK,
                    entry_time=datetime.now(), strategy_id=strategy_id)


# ── 1) 진입 사이징 + strategy_id 기록 ────────────────────────────────────────
@pytest.mark.asyncio
async def test_enter_sizes_and_submits():
    orch = TradingOrchestrator()
    ex = _FakeExecutor()
    orch._executor = ex
    orch._position_mgr = _FakePositionMgr(_balance(10_000_000.0))
    orch._alert = None
    await orch._supertrend_enter(object(), [_entry("005930", price=70000.0)])
    assert len(ex.submitted) == 1
    o = ex.submitted[0]
    # 예수금 1천만 × 8% = 80만 / 7만원 = 11주
    assert o.quantity == 11
    assert o.side == OrderSide.BUY
    assert o.strategy_id == "supertrend_v1"
    # strategy_id 사전 기록(체결 콜백 전파용) — 체결 성공 시 정리되지 않고 콜백에서 pop.
    assert orch._pending_strategy_ids.get("005930") == "supertrend_v1"


# ── 2-a) 이미 보유한 종목은 진입 스킵 ────────────────────────────────────────
@pytest.mark.asyncio
async def test_enter_skips_held():
    orch = TradingOrchestrator()
    ex = _FakeExecutor()
    orch._executor = ex
    orch._position_mgr = _FakePositionMgr(_balance(), {"005930": _pos("005930")})
    await orch._supertrend_enter(object(), [_entry("005930")])
    assert ex.submitted == []


# ── 2-b) 동시 보유 상한 도달 시 신규 진입 중단 ───────────────────────────────
@pytest.mark.asyncio
async def test_enter_respects_max_positions(monkeypatch):
    monkeypatch.setattr(orch_mod, "_SUPERTREND_MAX_POSITIONS", 2)
    orch = TradingOrchestrator()
    ex = _FakeExecutor()
    orch._executor = ex
    held = {f"00{i}": _pos(f"00{i}") for i in range(2)}  # 이미 2종목 보유
    orch._position_mgr = _FakePositionMgr(_balance(), held)
    await orch._supertrend_enter(object(), [_entry("111111"), _entry("222222")])
    assert ex.submitted == []  # 상한 도달 → 신규 0건


# ── 2-c) 배분금 < 1주가 → 스킵 ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_enter_skips_when_alloc_below_one_share():
    orch = TradingOrchestrator()
    ex = _FakeExecutor()
    orch._executor = ex
    # 예수금 100만 × 8% = 8만 < 1주(고가 50만)
    orch._position_mgr = _FakePositionMgr(_balance(1_000_000.0))
    await orch._supertrend_enter(object(), [_entry("005930", price=500000.0)])
    assert ex.submitted == []


# ── 2-d) 리스크 거부 시 pending strategy_id 회수 ─────────────────────────────
@pytest.mark.asyncio
async def test_enter_rejected_cleans_pending():
    orch = TradingOrchestrator()
    ex = _FakeExecutor(fail=True)  # submit → None (거부)
    orch._executor = ex
    orch._position_mgr = _FakePositionMgr(_balance())
    await orch._supertrend_enter(object(), [_entry("005930")])
    assert len(ex.submitted) == 1
    assert "005930" not in orch._pending_strategy_ids  # 회수됨


# ── 3) strategy_id 전파: _on_order_filled → 포지션에 부여 ─────────────────────
@pytest.mark.asyncio
async def test_on_order_filled_propagates_strategy_id():
    orch = TradingOrchestrator()
    pm = _FakePositionMgr(_balance())
    orch._position_mgr = pm
    orch._pending_strategy_ids["005930"] = "supertrend_v1"
    result = OrderResult(order_id="OID", symbol="005930", side=OrderSide.BUY,
                         status=OrderStatus.FILLED, filled_quantity=11.0,
                         avg_price=70000.0, market_type=MarketType.STOCK,
                         timestamp=datetime.now())
    await orch._on_order_filled(result)
    # PositionManager 가 strategy_id 를 받아 포지션에 부여
    assert pm.filled_calls[0] == ("005930", OrderSide.BUY, "supertrend_v1")
    assert pm.get_positions()["005930"].strategy_id == "supertrend_v1"
    # 전파 후 pending 정리
    assert "005930" not in orch._pending_strategy_ids


# ── 4) 청산 배선: SELL 신호 → _execute_exit 호출 ─────────────────────────────
@pytest.mark.asyncio
async def test_cycle_exit_calls_execute_exit(monkeypatch):
    import backend.core.scanner as scn
    from backend.core.state import app_state
    # 2026-06-01: orchestrator 자동주문은 LEGACY 비활성(_SUPERTREND_AUTO_TRADE=False).
    #   본 테스트는 '활성 시 청산이 _execute_exit 를 호출하는가'를 검증하므로 True 강제.
    monkeypatch.setattr(orch_mod, "_SUPERTREND_AUTO_TRADE", True)
    app_state.watchlist = ["005930"]  # oauth=None 시 watchlist fallback 유니버스
    orch = TradingOrchestrator()
    orch._executor = _FakeExecutor()
    held = {"005930": _pos("005930")}
    orch._position_mgr = _FakePositionMgr(_balance(), held)

    # 진입 스캔은 빈 결과, 청산 watcher 는 SELL 1건
    class _Scn:
        def __init__(self, gw): pass
        async def scan(self, syms): return []
    class _Wat:
        def __init__(self, gw): pass
        async def check(self, positions):
            return [ExitSignal(symbol="005930", name="005930", exit_type="reverse_signal",
                               price=71000.0, pnl_pct=1.4, reason="SELL",
                               market_type=MarketType.STOCK, timestamp=datetime.now())]
    monkeypatch.setattr(scn, "SupertrendScanner", _Scn)
    monkeypatch.setattr(scn, "SupertrendExitWatcher", _Wat)
    class _Prov:
        def __init__(self, o): pass
        async def fetch_universe(self, max_symbols): return ["005930"]
    monkeypatch.setattr(scn, "RankUniverseProvider", _Prov)

    calls = []
    async def fake_exit(symbol, pos, qty_pct, reason):
        calls.append((symbol, qty_pct, reason))
    monkeypatch.setattr(orch, "_execute_exit", fake_exit)

    await orch._supertrend_cycle(object(), oauth=None)
    assert len(calls) == 1
    assert calls[0][0] == "005930"
    assert calls[0][1] == 1.0  # 전량 청산
    assert "reverse_signal" in calls[0][2]


# ── 5) 토글 OFF → 주문 없이 알림만 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_auto_trade_off_no_orders(monkeypatch):
    monkeypatch.setattr(orch_mod, "_SUPERTREND_AUTO_TRADE", False)
    import backend.core.scanner as scn
    from backend.core.state import app_state
    app_state.watchlist = ["000660"]  # oauth=None 시 watchlist fallback 유니버스
    orch = TradingOrchestrator()
    ex = _FakeExecutor()
    orch._executor = ex
    orch._position_mgr = _FakePositionMgr(_balance(), {"005930": _pos("005930")})

    class _Scn:
        def __init__(self, gw): pass
        async def scan(self, syms): return [_entry("000660")]
    class _Wat:
        def __init__(self, gw): pass
        async def check(self, positions):
            return [ExitSignal(symbol="005930", name="005930", exit_type="reverse_signal",
                               price=71000.0, pnl_pct=1.4, reason="SELL",
                               market_type=MarketType.STOCK, timestamp=datetime.now())]
    monkeypatch.setattr(scn, "SupertrendScanner", _Scn)
    monkeypatch.setattr(scn, "SupertrendExitWatcher", _Wat)
    class _Prov:
        def __init__(self, o): pass
        async def fetch_universe(self, max_symbols): return ["000660"]
    monkeypatch.setattr(scn, "RankUniverseProvider", _Prov)

    exit_calls = []
    async def fake_exit(*a, **k): exit_calls.append(a)
    monkeypatch.setattr(orch, "_execute_exit", fake_exit)

    await orch._supertrend_cycle(object(), oauth=None)
    assert ex.submitted == []      # 진입 주문 없음
    assert exit_calls == []          # 청산 주문 없음 (signal-only)
