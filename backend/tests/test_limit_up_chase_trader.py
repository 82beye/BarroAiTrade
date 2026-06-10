"""상따(상한가 따라잡기) 트레이더 테스트 — 진입(모멘텀+호가벽)/청산(RUNNER)/오버나잇/격리.

네트워크 없이 모든 협력자를 가짜로 주입해 결정적으로 검증.
부모 SupertrendAutoTrader 의 RUNNER/_is_limit_up 재사용이 상따에서도 동작함을 확인.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import List

import pytest

from backend.core.limit_up_chase_trader import (
    LimitUpChaseConfig,
    LimitUpChaseTrader,
)
from backend.core.gateway.kiwoom_native_account import AccountBalance, AccountDeposit
from backend.models.market import OHLCV, MarketType, OrderBook, TradingSession


# ── 캔들 ─────────────────────────────────────────────────────────────────────
def _candles(prices: List[float], symbol="005930", day=(2026, 6, 1)) -> List[OHLCV]:
    base = datetime(day[0], day[1], day[2], 9, 0)
    return [
        OHLCV(symbol=symbol, timestamp=base + timedelta(minutes=5 * i),
              open=p, high=p * 1.005, low=p * 0.995, close=p,
              volume=10000 + i, market_type=MarketType.STOCK)
        for i, p in enumerate(prices)
    ]


def _candles_2d(day1: List[float], day2: List[float], symbol="005930") -> List[OHLCV]:
    """2거래일 캔들 — _prev_close(전일 마지막 종가)·상한가 판정용."""
    return _candles(day1, symbol, (2026, 6, 1)) + _candles(day2, symbol, (2026, 6, 2))


# 완만 상승 60봉(단일일) — 진입 분석용(전환 무관, 가격만)
_FLAT = [5000 + i * 2 for i in range(60)]


# ── 가짜 협력자 ──────────────────────────────────────────────────────────────
class _FakeCandles:
    def __init__(self, mapping):
        self._m = mapping
    async def fetch_minute(self, symbol, tic_scope="5"):
        v = self._m.get(symbol)
        return list(v) if v else []


class _FakeAccount:
    def __init__(self, cash=10_000_000.0):
        self._cash = cash
    async def fetch_deposit(self):
        return AccountDeposit(cash=Decimal(str(self._cash)), margin_cash=Decimal("0"),
                              bond_margin_cash=Decimal("0"), next_day_settlement=Decimal("0"))
    async def fetch_balance(self):
        return AccountBalance(total_purchase=Decimal("0"), total_eval=Decimal("0"),
                              total_pnl=Decimal("0"), total_pnl_rate=Decimal("0"),
                              estimated_deposit=Decimal(str(self._cash)), holdings=[])


class _OrderRec:
    def __init__(self, order_no="OID", dry_run=True):
        self.order_no = order_no
        self.dry_run = dry_run


class _FakeGate:
    def __init__(self):
        self.buys = []
        self.sells = []
        self._executor = type("E", (), {"_dry_run": True})()
    async def place_buy(self, symbol, qty, daily_pnl_pct=Decimal("0"), strategy_id=None):
        self.buys.append((symbol, qty, strategy_id))
        return _OrderRec(order_no=f"BUY_{symbol}")
    async def place_sell(self, symbol, qty, daily_pnl_pct=Decimal("0"), strategy_id=None):
        self.sells.append((symbol, qty, strategy_id))
        return _OrderRec(order_no=f"SELL_{symbol}")


def _pos(symbol, strategy, entry_price, qty, entry_time="", partial_tp_done=False):
    return SimpleNamespace(
        symbol=symbol, name=symbol, strategy=strategy, entry_price=entry_price,
        total_recommended_qty=qty, order_no="", entry_time=entry_time,
        partial_tp_done=partial_tp_done, filled_qty=lambda: qty,
    )


class _FakePos:
    def __init__(self):
        self._d = {}
    def load_all(self):
        return dict(self._d)
    def get(self, s):
        return self._d.get(s)
    def remove(self, s):
        self._d.pop(s, None)
    def upsert(self, pos):
        self._d[pos.symbol] = pos
    def create_from_order(self, symbol, name, strategy, entry_price,
                          total_recommended_qty, order_no, **kw):
        p = _pos(symbol, strategy, entry_price, total_recommended_qty)
        p.name = name
        self._d[symbol] = p
        return p
    def inject(self, pos):
        self._d[pos.symbol] = pos


class _FakeOrderbook:
    def __init__(self, mapping):
        self._m = mapping   # {symbol: (bids, asks)}
    async def fetch_orderbook(self, symbol):
        bids, asks = self._m.get(symbol, ([], []))
        return OrderBook(symbol=symbol, asks=list(asks), bids=list(bids),
                         timestamp=datetime.now(), market_type=MarketType.STOCK)


class _FakeSession:
    def __init__(self, session=TradingSession.REGULAR):
        self._s = session
    def get_session(self, now=None):
        return self._s


def _cfg(**kw):
    """상따 테스트 기본 config — 진입 시간창 항상 열림(시각 비의존)."""
    d = dict(entry_start_time="", entry_end_time="", min_adx=0.0, min_flip_atr_mult=0.0,
             max_order_qty=0, max_order_value=0.0, max_intraday_range_pos=0.0,
             min_price=Decimal("1000"), max_per_position_ratio=Decimal("0.10"),
             overnight_mode="overnight",  # _eod_force_now 비활성(시각 비의존)
             wall_min_top_qty=50_000, wall_bid_ask_ratio=3.0, wall_near_pct=1.0,
             entry_flu_min=20.0, entry_flu_max=27.0)
    d.update(kw)
    return LimitUpChaseConfig(**d)


def _trader(candles_map, *, universe=None, orderbook=None, gate=None, account=None,
            pos=None, config=None):
    async def _uni():
        return universe or []
    return LimitUpChaseTrader(
        candle_fetcher=_FakeCandles(candles_map),
        account_fetcher=account or _FakeAccount(),
        order_gate=gate or _FakeGate(),
        pos_store=pos or _FakePos(),
        universe_provider=_uni,
        notifier=None,
        config=config or _cfg(),
        session_service=_FakeSession(),
        orderbook_fetcher=orderbook or _FakeOrderbook({}),
    )


def _lead(symbol, flu, price=5118.0, name=None):
    return SimpleNamespace(symbol=symbol, name=name or symbol, flu_rate=flu, cur_price=price)


# ════════════════════════════ 호가 매수벽 ════════════════════════════════════
@pytest.mark.asyncio
async def test_wall_pass_fat_bid_thin_ask():
    t = _trader({"A": _candles(_FLAT, "A")},
                orderbook=_FakeOrderbook({"A": ([(5120, 80_000)], [(5125, 1_000)])}))
    bars = await t._fetch_bars("A")
    assert await t._passes_orderbook_wall("A", bars) is True


@pytest.mark.asyncio
async def test_wall_reject_thin_top_qty():
    t = _trader({"A": _candles(_FLAT, "A")},
                orderbook=_FakeOrderbook({"A": ([(5120, 10_000)], [(5125, 1_000)])}))
    bars = await t._fetch_bars("A")
    assert await t._passes_orderbook_wall("A", bars) is False  # 잔량 10k < 50k


@pytest.mark.asyncio
async def test_wall_reject_low_ratio():
    # 매수1잔량은 충분하나 top-3 매수/매도 비율이 낮음(매도벽 두꺼움)
    t = _trader({"A": _candles(_FLAT, "A")},
                orderbook=_FakeOrderbook({"A": ([(5120, 60_000)], [(5125, 60_000)])}))
    bars = await t._fetch_bars("A")
    assert await t._passes_orderbook_wall("A", bars) is False  # 60k/60k=1.0 < 3.0


@pytest.mark.asyncio
async def test_wall_pass_no_asks_locked():
    # 매도 잔량 전무 = 상한가 락 임박 → 통과
    t = _trader({"A": _candles(_FLAT, "A")},
                orderbook=_FakeOrderbook({"A": ([(5120, 60_000)], [])}))
    bars = await t._fetch_bars("A")
    assert await t._passes_orderbook_wall("A", bars) is True


@pytest.mark.asyncio
async def test_wall_reject_not_near_limit_price():
    # 2일 캔들: 전일종가 5000 → 상한가가격 6450. 매수1호가 5120 → 근접 아님 → 탈락.
    day2 = [5118 for _ in range(5)]
    t = _trader({"A": _candles_2d([5000] * 30, day2, "A")},
                orderbook=_FakeOrderbook({"A": ([(5120, 80_000)], [])}))
    bars = await t._fetch_bars("A")
    assert await t._passes_orderbook_wall("A", bars) is False


@pytest.mark.asyncio
async def test_wall_no_fetcher_is_conservative_false():
    t = _trader({"A": _candles(_FLAT, "A")}, orderbook=None)
    t._ob = None
    bars = await t._fetch_bars("A")
    assert await t._passes_orderbook_wall("A", bars) is False


# ════════════════════════════ 모멘텀 밴드 ════════════════════════════════════
def test_momentum_band_in_and_out():
    t = _trader({})
    assert t._momentum_band_pass(_lead("A", 23.0)) is True
    assert t._momentum_band_pass(_lead("A", 19.0)) is False   # 밴드 하한 미만
    assert t._momentum_band_pass(_lead("A", 29.0)) is False   # 상한 초과(락 추격 차단)


# ════════════════════════════ 진입 통합 ══════════════════════════════════════
@pytest.mark.asyncio
async def test_entry_full_path_tags_limit_up_chase():
    gate = _FakeGate(); ps = _FakePos()
    t = _trader({"A": _candles(_FLAT, "A")},
                universe=[_lead("A", 24.0)],
                orderbook=_FakeOrderbook({"A": ([(5120, 80_000)], [(5125, 1_000)])}),
                gate=gate, pos=ps)
    res = await t.run_cycle()
    assert len(gate.buys) == 1
    assert gate.buys[0][0] == "A" and gate.buys[0][2] == "limit_up_chase"
    assert ps.get("A").strategy == "limit_up_chase"
    assert res["entered"] and res["entered"][0]["symbol"] == "A"


@pytest.mark.asyncio
async def test_entry_rejected_by_wall():
    gate = _FakeGate()
    t = _trader({"A": _candles(_FLAT, "A")},
                universe=[_lead("A", 24.0)],
                orderbook=_FakeOrderbook({"A": ([(5120, 1_000)], [(5125, 1_000)])}),  # thin
                gate=gate)
    await t.run_cycle()
    assert gate.buys == []


@pytest.mark.asyncio
async def test_entry_rejected_by_momentum():
    gate = _FakeGate()
    t = _trader({"A": _candles(_FLAT, "A")},
                universe=[_lead("A", 12.0)],   # 밴드 밖
                orderbook=_FakeOrderbook({"A": ([(5120, 80_000)], [])}),
                gate=gate)
    await t.run_cycle()
    assert gate.buys == []


# ════════════════════════════ 상한가 판정 재사용 ═════════════════════════════
@pytest.mark.asyncio
async def test_is_limit_up_inherited():
    # 전일종가 10000 → ×1.29=12900. day2 종가 13000 → 상한가권.
    t = _trader({"A": _candles_2d([10000] * 30, [13000] * 3, "A")}, config=_cfg())
    bars = await t._fetch_bars("A")
    assert t._is_limit_up(bars) is True


# ════════════════════════════ 오버나잇 홀딩(상한가 락) ═══════════════════════
@pytest.mark.asyncio
async def test_overnight_hold_when_locked_no_sell():
    gate = _FakeGate(); ps = _FakePos()
    ps.inject(_pos("A", "limit_up_chase", entry_price=10000.0, qty=10,
                   entry_time="2026-06-02T09:00:00"))
    # day2 종가 13000 → 상한가권 → _runner_should_exit "상한가 홀딩" → 미청산.
    t = _trader({"A": _candles_2d([10000] * 30, [13000] * 5, "A")},
                gate=gate, pos=ps, config=_cfg(overnight_mode="overnight", take_profit_pct=5.0))
    await t.run_cycle()
    assert gate.sells == []           # 상한가 락 → 안 판다
    assert ps.get("A") is not None    # 보유 유지(오버나잇)


# ════════════════════════════ 리스크 청산(하드손절) ══════════════════════════
@pytest.mark.asyncio
async def test_exit_on_hard_stop():
    gate = _FakeGate(); ps = _FakePos()
    ps.inject(_pos("A", "limit_up_chase", entry_price=10000.0, qty=10,
                   entry_time="2026-06-01T09:00:00"))
    # 종가가 진입가 대비 -8% (8000~9000대 하락) → hard_stop_pct=-4% 발동.
    falling = [10000] * 30 + [9000 - i * 50 for i in range(10)]
    t = _trader({"A": _candles(falling, "A")},
                gate=gate, pos=ps,
                config=_cfg(hard_stop_pct=-4.0, trail_atr_mult=0.0,
                            take_profit_pct=0.0, overnight_mode="overnight"))
    await t.run_cycle()
    assert len(gate.sells) == 1
    assert gate.sells[0][0] == "A" and gate.sells[0][2] == "limit_up_chase"


# ════════════════════════════ 전략 격리(더블셀 방지) ═════════════════════════
@pytest.mark.asyncio
async def test_does_not_touch_supertrend_positions():
    gate = _FakeGate(); ps = _FakePos()
    # supertrend 포지션(급락) — 상따 트레이더는 절대 건드리면 안 됨.
    ps.inject(_pos("ST", "supertrend", entry_price=10000.0, qty=10,
                   entry_time="2026-06-01T09:00:00"))
    falling = [10000] * 30 + [7000 - i * 50 for i in range(10)]
    t = _trader({"ST": _candles(falling, "ST")},
                gate=gate, pos=ps, config=_cfg(hard_stop_pct=-4.0, overnight_mode="overnight"))
    await t.run_cycle()
    assert gate.sells == []          # supertrend 포지션 미청산
    assert ps.get("ST") is not None


# ════════════════════════════ 익일 시가갭 부분익절 ═══════════════════════════
@pytest.mark.asyncio
async def test_gap_partial_next_day_once():
    gate = _FakeGate(); ps = _FakePos()
    # 오버나잇 보유(전일 진입), 익일 시가 갭상승.
    ps.inject(_pos("A", "limit_up_chase", entry_price=10000.0, qty=100,
                   entry_time="2026-06-01T10:00:00", partial_tp_done=False))
    # day1 종가 10000, day2 시가 11000(갭 +10%), 현재가 11200(이익) — 개장초 3봉.
    bars = {"A": _candles_2d([10000] * 30, [11000, 11100, 11200], "A")}
    cfg = _cfg(overnight_mode="overnight", runner_gap_partial_ratio=0.5,
               runner_gap_partial_min_pct=3.0, runner_gap_partial_window_bars=6)
    t = _trader(bars, gate=gate, pos=ps, config=cfg)
    res = await t.run_cycle()
    assert len(gate.sells) == 1
    assert gate.sells[0][2] == "limit_up_chase" and gate.sells[0][1] == 50  # 절반
    assert ps.get("A").partial_tp_done is True
    assert ps.get("A").total_recommended_qty == 50
    # 2회차: 이미 partial_tp_done → 추가 매도 없음(멱등)
    await t.run_cycle()
    assert len(gate.sells) == 1


# ════════════════════════════ 시간 게이트 ════════════════════════════════════
def test_entry_window_gate():
    t = _trader({}, config=_cfg(entry_start_time="", entry_end_time="14:00"))
    assert t._entry_window_open(now=dtime(13, 0)) is True
    assert t._entry_window_open(now=dtime(14, 30)) is False   # 마감 후


def test_eod_force_modes():
    daily = _trader({}, config=_cfg(overnight_mode="daily", eod_close_time="15:15"))
    assert daily._eod_force_now(now=dtime(15, 20)) is True
    assert daily._eod_force_now(now=dtime(14, 0)) is False
    overnight = _trader({}, config=_cfg(overnight_mode="overnight", eod_close_time="15:15"))
    assert overnight._eod_force_now(now=dtime(15, 20)) is False  # 오버나잇은 강제청산 안 함


# ════════════════════════════ evaluate_holdings 제외 default ═════════════════
def test_evaluate_holdings_default_excludes_limit_up():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_holdings.py"
    spec = importlib.util.spec_from_file_location("evh", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ap = m._build_argparser() if hasattr(m, "_build_argparser") else None
    if ap is None:
        # argparser 가 main 내부 구성이면 소스에서 default 문자열 확인(방어적).
        src = p.read_text(encoding="utf-8")
        assert 'default="supertrend,limit_up_chase"' in src
    else:
        ns = ap.parse_args([])
        assert "limit_up_chase" in ns.exclude_strategy
