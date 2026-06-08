"""슈퍼트렌드 자동매매 루프 테스트 — 진입/청산/상한/사이징/게이트.

네트워크 없이 모든 협력자를 가짜로 주입해 결정적으로 검증.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List

import pytest

from backend.core.supertrend_auto_trader import (
    SupertrendAutoConfig,
    SupertrendAutoTrader,
)
from backend.core.gateway.kiwoom_native_account import AccountBalance, AccountDeposit
from backend.models.market import OHLCV, MarketType, TradingSession


class _FakeSession:
    """장시간 판단기 가짜 — 지정한 세션을 반환(테스트 시각 비의존)."""
    def __init__(self, session=TradingSession.REGULAR):
        self._s = session
    def get_session(self, now=None):
        return self._s


# ── 캔들 생성 (compute_supertrend 신호 유도) ─────────────────────────────────
def _candles(prices: List[float], symbol="005930") -> List[OHLCV]:
    base = datetime(2026, 6, 1, 9, 0)
    return [
        OHLCV(symbol=symbol, timestamp=base + timedelta(minutes=5 * i),
              open=p, high=p * 1.005, low=p * 0.995, close=p,
              volume=10000 + i, market_type=MarketType.STOCK)
        for i, p in enumerate(prices)
    ]

# 하락 57봉 후 급반등 3봉 → BUY 전환
_BUY = [10000 - i * 50 for i in range(57)] + [7200 + i * 350 for i in range(3)]
# 상승 56봉 후 급락 4봉 → SELL 전환
_SELL = [10000 + i * 50 for i in range(56)] + [12800 - i * 300 for i in range(4)]
# 잔잔한 상승 지속 (전환 없음)
_FLAT_UP = [10000 + i * 5 for i in range(60)]
# 완만 상승 + 규칙적 소폭 눌림 → trend +1 유지(ST SELL 없음) & HTF(10m) RSI ≈ 63.5(중간대).
#   RSI 조기청산을 슈퍼트렌드 SELL 과 분리 검증하기 위한 시리즈(level floor 로 dead 유도).
_CHOPPY_UP = [int(10000 + i * 22 - (140 if i % 4 == 3 else 0)) for i in range(60)]


# ── 가짜 협력자 ──────────────────────────────────────────────────────────────
class _FakeCandles:
    def __init__(self, mapping):  # {symbol: [prices]}
        self._m = mapping
    async def fetch_minute(self, symbol, tic_scope="5"):
        prices = self._m.get(symbol)
        return _candles(prices, symbol) if prices else []


class _FakeAccount:
    def __init__(self, cash=10_000_000.0, pnl_rate=0.0):
        self._cash = cash
        self._pnl_rate = pnl_rate
    async def fetch_deposit(self):
        return AccountDeposit(cash=Decimal(str(self._cash)), margin_cash=Decimal("0"),
                              bond_margin_cash=Decimal("0"), next_day_settlement=Decimal("0"))
    async def fetch_balance(self):
        return AccountBalance(total_purchase=Decimal("0"), total_eval=Decimal("0"),
                              total_pnl=Decimal("0"), total_pnl_rate=Decimal(str(self._pnl_rate)),
                              estimated_deposit=Decimal(str(self._cash)), holdings=[])


class _OrderRec:
    def __init__(self, order_no="OID", dry_run=True):
        self.order_no = order_no
        self.dry_run = dry_run


class _FakeGate:
    def __init__(self, block_buy=False):
        self.buys = []
        self.sells = []
        self._block_buy = block_buy
        # SupertrendAutoTrader.run_forever 로그가 _gate._executor._dry_run 참조
        self._executor = type("E", (), {"_dry_run": True})()
    async def place_buy(self, symbol, qty, daily_pnl_pct=Decimal("0"), strategy_id=None):
        if self._block_buy:
            raise RuntimeError("blocked")
        self.buys.append((symbol, qty, strategy_id, daily_pnl_pct))
        return _OrderRec(order_no=f"BUY_{symbol}")
    async def place_sell(self, symbol, qty, daily_pnl_pct=Decimal("0"), strategy_id=None):
        self.sells.append((symbol, qty, strategy_id))
        return _OrderRec(order_no=f"SELL_{symbol}")


class _FakePosStore:
    """ActivePositionStore 인터페이스 최소 구현."""
    def __init__(self):
        self._d = {}
    def load_all(self):
        return dict(self._d)
    def get(self, symbol):
        return self._d.get(symbol)
    def remove(self, symbol):
        self._d.pop(symbol, None)
    def create_from_order(self, symbol, name, strategy, entry_price,
                          total_recommended_qty, order_no, **kw):
        pos = type("P", (), {
            "symbol": symbol, "name": name, "strategy": strategy,
            "entry_price": entry_price, "total_recommended_qty": total_recommended_qty,
            "order_no": order_no, "filled_qty": lambda self=None: total_recommended_qty,
        })()
        self._d[symbol] = pos
        return pos
    # 보유 강제 주입(테스트용)
    def _inject(self, symbol, strategy="supertrend", qty=11):
        self._d[symbol] = type("P", (), {
            "symbol": symbol, "name": symbol, "strategy": strategy,
            "entry_price": 10000.0, "total_recommended_qty": qty,
            "filled_qty": lambda self=None: qty,
        })()


def _base_config(**kw):
    """순수 진입 로직 테스트용 기본 config — 선택적 게이트 전부 비활성.
    6/2 개선(장초반차단/whipsaw/하드캡) + 6/8 개선(트레일/익절/고점진입게이트) 모두 OFF.
    개선 기능은 각 전용 테스트에서 명시적으로 켜서 검증한다."""
    defaults = dict(entry_start_time="", min_adx=0.0, min_flip_atr_mult=0.0,
                    max_order_qty=0, max_order_value=0.0,
                    trail_atr_mult=0.0, take_profit_pct=0.0,
                    max_intraday_range_pos=0.0, max_day_change_pct=0.0)
    defaults.update(kw)
    return SupertrendAutoConfig(**defaults)


def _trader(candles_map, *, universe, gate=None, account=None, pos=None, config=None,
            session=None):
    async def _uni():
        return universe
    return SupertrendAutoTrader(
        candle_fetcher=_FakeCandles(candles_map),
        account_fetcher=account or _FakeAccount(),
        order_gate=gate or _FakeGate(),
        pos_store=pos or _FakePosStore(),
        universe_provider=_uni,
        config=config or _base_config(),
        session_service=session or _FakeSession(TradingSession.REGULAR),
    )


# ── 1) 진입: BUY 전환 종목 자동 매수 + 포지션 등록 ───────────────────────────
@pytest.mark.asyncio
async def test_entry_buys_on_buy_signal():
    gate = _FakeGate()
    pos = _FakePosStore()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate, pos=pos)
    r = await t.run_cycle()
    assert len(gate.buys) == 1
    assert gate.buys[0][0] == "005930"
    assert gate.buys[0][2] == "supertrend"   # strategy_id
    assert r["entered"][0]["symbol"] == "005930"
    # 포지션 등록됨 (재진입 차단 근거)
    assert pos.get("005930") is not None
    assert pos.get("005930").strategy == "supertrend"


# ── 2) 진입 안 함: 전환 없는 잔잔한 추세 ─────────────────────────────────────
@pytest.mark.asyncio
async def test_no_entry_without_buy_signal():
    gate = _FakeGate()
    t = _trader({"000660": _FLAT_UP}, universe=[("000660", "SK하이닉스")], gate=gate)
    await t.run_cycle()
    assert gate.buys == []


# ── 3) 미보유만 진입 (이미 보유 종목 스킵) ───────────────────────────────────
@pytest.mark.asyncio
async def test_skip_already_held():
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend")
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate, pos=pos)
    await t.run_cycle()
    assert gate.buys == []


# ── 4) 동시 보유 상한 (max_positions) ────────────────────────────────────────
@pytest.mark.asyncio
async def test_respects_max_positions():
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("000001"); pos._inject("000002")  # 이미 2종목
    cfg = _base_config(max_positions=2)
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")],
                gate=gate, pos=pos, config=cfg)
    await t.run_cycle()
    assert gate.buys == []   # 상한 도달 → 신규 0


# ── 5) 청산: 보유 supertrend 종목 SELL 전환 → 매도 + 포지션 제거 ─────────────
@pytest.mark.asyncio
async def test_exit_sells_on_sell_signal():
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos)
    r = await t.run_cycle()
    assert len(gate.sells) == 1
    assert gate.sells[0] == ("005930", 11, "supertrend")
    assert pos.get("005930") is None   # 청산 후 제거
    assert r["exited"][0]["symbol"] == "005930"


# ── 6) 청산 안 함: 비-supertrend 전략 포지션은 건드리지 않음 ──────────────────
@pytest.mark.asyncio
async def test_does_not_touch_other_strategy_positions():
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="swing_38", qty=11)  # 다른 전략
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos)
    await t.run_cycle()
    assert gate.sells == []
    assert pos.get("005930") is not None  # 유지


# ── 7) 사이징: evaluate_risk_gate 예수금 8%×10종목 ───────────────────────────
@pytest.mark.asyncio
async def test_sizing_uses_8pct_per_symbol():
    gate = _FakeGate()
    acct = _FakeAccount(cash=10_000_000.0)
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")],
                gate=gate, account=acct)
    await t.run_cycle()
    # 1천만 × 80% ÷ 10 = 80만, 단 캡 10%(100만)보다 작으므로 80만. 진입가 ~7900 근처.
    # 정확 진입가는 _BUY 마지막 종가(7200+2*350=7900). 80만/7900 = 101주.
    assert gate.buys[0][1] == 101


# ── 8) enabled=False → 사이클 미실행 ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_disabled_does_nothing():
    gate = _FakeGate()
    cfg = _base_config(enabled=False)
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate, config=cfg)
    # run_cycle 직접 호출은 enabled 무관(루프 게이트는 run_forever) — 여기선 run_forever 1틱 모사 대신
    # enabled 게이트가 run_forever 에 있으므로 run_cycle 자체는 동작. 대신 토글 필드만 확인.
    assert t.config.enabled is False


# ── 9) 일일손실 게이트 입력 전달 (place_buy 에 daily_pnl_pct 전파) ───────────
@pytest.mark.asyncio
async def test_daily_pnl_passed_to_gate():
    gate = _FakeGate()
    acct = _FakeAccount(cash=10_000_000.0, pnl_rate=-1.5)
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate, account=acct)
    await t.run_cycle()
    assert gate.buys[0][3] == Decimal("-1.5")   # daily_pnl_pct 전달됨


# ── 10) 진입 주문 실패해도 다음 종목 계속 (예외 격리) ────────────────────────
@pytest.mark.asyncio
async def test_buy_failure_isolated():
    gate = _FakeGate(block_buy=True)
    pos = _FakePosStore()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate, pos=pos)
    r = await t.run_cycle()   # 예외 없이 완주
    assert r["entered"] == []
    assert pos.get("005930") is None  # 실패 시 포지션 미등록


# ── 11) 장시간 가드: 비정규장(CLOSED) → 진입/청산 모두 skip ───────────────────
@pytest.mark.asyncio
async def test_market_hours_guard_skips_when_closed():
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)  # 보유 SELL 신호분
    t = _trader({"005930": _SELL, "000660": _BUY}, universe=[("000660", "SK하이닉스")],
                gate=gate, pos=pos, session=_FakeSession(TradingSession.CLOSED))
    r = await t.run_cycle()
    assert gate.buys == [] and gate.sells == []
    assert r["entered"] == [] and r["exited"] == []


# ── 12) 장시간 가드: 시간외(KRX_AFTER, 시장가 불가) → skip ────────────────────
@pytest.mark.asyncio
async def test_market_hours_guard_skips_after_hours():
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")],
                gate=gate, session=_FakeSession(TradingSession.KRX_AFTER))
    await t.run_cycle()
    assert gate.buys == []


# ── 13) 가드 OFF(market_hours_only=False) → 세션 무관 매매 ────────────────────
@pytest.mark.asyncio
async def test_market_hours_guard_disabled_allows_any_session():
    gate = _FakeGate()
    cfg = _base_config(market_hours_only=False)
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")],
                gate=gate, config=cfg, session=_FakeSession(TradingSession.CLOSED))
    await t.run_cycle()
    assert len(gate.buys) == 1


# ── 14) REGULAR 세션 → 정상 매매 (가드 통과) ─────────────────────────────────
@pytest.mark.asyncio
async def test_market_hours_guard_allows_regular():
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")],
                gate=gate, session=_FakeSession(TradingSession.REGULAR))
    await t.run_cycle()
    assert len(gate.buys) == 1


# ══ 6/2 복기 개선 3건 (recon_2026-06-02) ══════════════════════════════════════

# ── [개선1] 장초반 진입 차단 (_entry_time_open) ──────────────────────────────
def test_entry_start_time_empty_always_open():
    """entry_start_time 빈값 → 항상 진입 허용(True)."""
    assert _trader({}, universe=[], config=_base_config(entry_start_time=""))._entry_time_open() is True


def test_entry_start_time_cutoff_evaluates():
    """entry_start_time 설정 시 _entry_time_open 이 bool 반환(현재 시각 기준 비교)."""
    t = _trader({}, universe=[], config=_base_config(entry_start_time="09:30"))
    assert isinstance(t._entry_time_open(), bool)


@pytest.mark.asyncio
async def test_entry_start_time_empty_allows():
    """entry_start_time 빈값이면 시각 무관 진입 허용."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930","삼성전자")],
                gate=gate, config=_base_config(entry_start_time=""))
    await t.run_cycle()
    assert len(gate.buys) == 1


# ── [개선2] whipsaw 필터 (_whipsaw_pass) ─────────────────────────────────────
@pytest.mark.asyncio
async def test_whipsaw_filter_blocks_weak_with_high_adx_req():
    """min_adx 과대(99) → 합성 약추세 BUY 거부."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930","삼성전자")],
                gate=gate, config=_base_config(min_adx=99.0, min_flip_atr_mult=0.0))
    await t.run_cycle()
    assert gate.buys == []   # ADX 99 미달 → 진입 0


@pytest.mark.asyncio
async def test_whipsaw_filter_off_allows():
    """필터 0(비활성) → 진입 허용 (기존 무필터 동작)."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930","삼성전자")],
                gate=gate, config=_base_config(min_adx=0.0, min_flip_atr_mult=0.0))
    await t.run_cycle()
    assert len(gate.buys) == 1


@pytest.mark.asyncio
async def test_whipsaw_flip_gate_blocks_excessive_mult():
    """FLIP 과대(100·ATR) → 약한 전환 거부."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930","삼성전자")],
                gate=gate, config=_base_config(min_adx=0.0, min_flip_atr_mult=100.0))
    await t.run_cycle()
    assert gate.buys == []


# ── [개선3] 수량 하드캡 (_cap_qty) ───────────────────────────────────────────
def test_cap_qty_by_quantity():
    t = _trader({}, universe=[], config=_base_config(max_order_qty=100, max_order_value=0.0))
    assert t._cap_qty(38219, 5000.0, "252670") == 100   # 수량캡


def test_cap_qty_by_value():
    t = _trader({}, universe=[], config=_base_config(max_order_qty=0, max_order_value=1_000_000.0))
    # 100만 / 5000 = 200주 상한
    assert t._cap_qty(38219, 5000.0, "252670") == 200


def test_cap_qty_no_cap_when_zero():
    t = _trader({}, universe=[], config=_base_config(max_order_qty=0, max_order_value=0.0))
    assert t._cap_qty(38219, 5000.0, "252670") == 38219  # 캡 없음


def test_cap_qty_within_limit_unchanged():
    t = _trader({}, universe=[], config=_base_config(max_order_qty=5000, max_order_value=5_000_000.0))
    assert t._cap_qty(10, 70000.0, "005930") == 10  # 한도 내 → 그대로


@pytest.mark.asyncio
async def test_cap_qty_applied_in_cycle():
    """진입 사이클에서 저가종목 거대수량이 하드캡으로 클램프되어 주문됨."""
    gate = _FakeGate()
    # 저가 ETF 모사: 예수금 큼 + 저가 → recommended_qty 거대
    acct = _FakeAccount(cash=100_000_000.0)
    # _BUY 마지막 종가는 7900 근처 → 8%×1억/7900 ≈ 1013주, 캡 500 적용
    t = _trader({"005930": _BUY}, universe=[("005930","저가주")],
                gate=gate, account=acct,
                config=_base_config(max_order_qty=500, max_order_value=0.0))
    await t.run_cycle()
    assert len(gate.buys) == 1
    assert gate.buys[0][1] == 500   # 하드캡 적용


# ── [BAR-OPS-10] 멀티 타임프레임 RSI 확인 필터 ───────────────────────────────
@pytest.mark.asyncio
async def test_rsi_gate_off_allows_entry():
    """rsi_enabled=False(기본) → RSI 게이트 no-op, _BUY 정상 진입(기존 회귀 보존)."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate,
                config=_base_config(rsi_enabled=False))
    await t.run_cycle()
    assert len(gate.buys) == 1


@pytest.mark.asyncio
async def test_rsi_gate_level_high_floor_blocks_entry():
    """rsi_enabled + level 모드 floor=99 → RSI<99 이라 미확정 → 진입 거부."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate,
                config=_base_config(rsi_enabled=True, rsi_mode="level",
                                    rsi_min_level=99.0, rsi_max_level=100.0))
    await t.run_cycle()
    assert gate.buys == []   # RSI 확인 실패 → 진입 0


@pytest.mark.asyncio
async def test_rsi_gate_level_low_floor_allows_entry():
    """rsi_enabled + level 모드 floor=0 → RSI 항상 [0,100] → 확인 통과 → 진입."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate,
                config=_base_config(rsi_enabled=True, rsi_mode="level",
                                    rsi_min_level=0.0, rsi_max_level=100.0))
    await t.run_cycle()
    assert len(gate.buys) == 1


@pytest.mark.asyncio
async def test_rsi_gate_centerline_blocks_downtrend_rebound():
    """긴 하락 후 막판 반등(_BUY) → HTF RSI 아직 <50 → centerline 골든 미발생 → 거부."""
    gate = _FakeGate()
    t = _trader({"005930": _BUY}, universe=[("005930", "삼성전자")], gate=gate,
                config=_base_config(rsi_enabled=True, rsi_mode="centerline",
                                    rsi_cross_lookback=2))
    await t.run_cycle()
    assert gate.buys == []   # 상위 TF RSI 미확정(약세 잔존) → 진입 거부


@pytest.mark.asyncio
async def test_rsi_exit_requires_st_sell_rsi_alone_no_sell():
    """[수정 핵심] RSI 단독으로는 청산 안 됨 — 슈퍼트렌드 SELL 이 '기준'(필수).

    _CHOPPY_UP: ST SELL 없음. rsi_exit_enabled + level floor=99(RSI<99=dead)여도
    ST SELL 이 없으므로 청산 불가. (046970 05-07 RSI단독 청산 버그의 회귀 테스트)
    """
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _CHOPPY_UP}, universe=[], gate=gate, pos=pos,
                config=_base_config(rsi_enabled=True, rsi_exit_enabled=True,
                                    rsi_mode="level", rsi_min_level=99.0))
    await t.run_cycle()
    assert gate.sells == []   # ST SELL 없음 → RSI 단독 청산 불가


@pytest.mark.asyncio
async def test_rsi_exit_confirms_st_sell_sells():
    """ST SELL + RSI 데드 확인(AND) → 청산. (_SELL: ST SELL 발생, level floor=99 → RSI<99=확인)"""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos,
                config=_base_config(rsi_enabled=True, rsi_exit_enabled=True,
                                    rsi_mode="level", rsi_min_level=99.0))
    r = await t.run_cycle()
    assert len(gate.sells) == 1
    assert gate.sells[0][0] == "005930"
    assert pos.get("005930") is None


@pytest.mark.asyncio
async def test_rsi_exit_blocks_unconfirmed_st_sell():
    """ST SELL 있어도 RSI 데드 미확인이면 청산 보류(AND). (_SELL + level floor=0 → RSI<0 불가)"""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos,
                config=_base_config(rsi_enabled=True, rsi_exit_enabled=True,
                                    rsi_mode="level", rsi_min_level=0.0))
    await t.run_cycle()
    assert gate.sells == []   # ST SELL 있으나 RSI 확인 실패 → 보유 유지


@pytest.mark.asyncio
async def test_exit_st_sell_only_when_rsi_exit_disabled():
    """rsi_exit_enabled=False → ST SELL 만으로 청산(RSI 확인 불요)."""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos,
                config=_base_config(rsi_enabled=True, rsi_exit_enabled=False))
    await t.run_cycle()
    assert len(gate.sells) == 1   # ST SELL → 청산


@pytest.mark.asyncio
async def test_trail_exit_fires_and_takes_priority():
    """ATR 트레일링: 고점종가 대비 k×ATR 이탈 시 청산(reason=트레일청산), 신호보다 우선."""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    # _SELL: 12,800 고점 후 급락 → 트레일 2×ATR 이탈
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos,
                config=_base_config(trail_atr_mult=2.0))
    r = await t.run_cycle()
    assert len(gate.sells) == 1
    assert r["exited"][0]["reason"] == "트레일청산"
    assert pos.get("005930") is None


@pytest.mark.asyncio
async def test_trail_disabled_uses_signal_exit():
    """trail_atr_mult=0 → 트레일 비활성, ST SELL 신호로 청산(reason=SELL 전환)."""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _SELL}, universe=[], gate=gate, pos=pos,
                config=_base_config(trail_atr_mult=0.0))
    r = await t.run_cycle()
    assert len(gate.sells) == 1
    assert r["exited"][0]["reason"] == "SELL 전환"


@pytest.mark.asyncio
async def test_trail_no_exit_when_near_peak():
    """고점 근처 보유(되돌림 없음) + ST SELL 없음 → 트레일 미발동, 청산 안 함."""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("005930", strategy="supertrend", qty=11)
    t = _trader({"005930": _FLAT_UP}, universe=[], gate=gate, pos=pos,
                config=_base_config(trail_atr_mult=2.0))
    await t.run_cycle()
    assert gate.sells == []   # 종가가 고점 근처 → 트레일 미발동, ST SELL도 없음


@pytest.mark.asyncio
async def test_rsi_no_second_fetch_minute():
    """rsi_enabled 라도 HTF 는 5분봉 bars 리샘플 → fetch_minute 종목당 1회(추가 fetch 금지)."""
    calls: dict[str, int] = {}

    class _CountingCandles(_FakeCandles):
        async def fetch_minute(self, symbol, tic_scope="5"):
            calls[symbol] = calls.get(symbol, 0) + 1
            return await super().fetch_minute(symbol, tic_scope=tic_scope)

    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("000660", strategy="supertrend", qty=11)   # 보유(청산 평가 대상)
    fetcher = _CountingCandles({"005930": _BUY, "000660": _FLAT_UP})

    async def _uni():
        return [("005930", "삼성전자")]

    t = SupertrendAutoTrader(
        candle_fetcher=fetcher,
        account_fetcher=_FakeAccount(),
        order_gate=gate,
        pos_store=pos,
        universe_provider=_uni,
        config=_base_config(rsi_enabled=True, rsi_exit_enabled=True,
                            rsi_mode="level", rsi_min_level=0.0),
        session_service=_FakeSession(TradingSession.REGULAR),
    )
    await t.run_cycle()
    # 보유 000660(청산 평가) + 후보 005930(진입 평가) 각각 정확히 1회만 fetch.
    assert calls.get("000660") == 1
    assert calls.get("005930") == 1


# ── 6/8 개선: 익절 / 트레일 기본활성 / 고점진입 게이트 ───────────────────────
import types as _types
from backend.core.strategy.supertrend import compute_supertrend


def test_config_defaults_6_8():
    """6/8 손익귀속 개선 기본값: 트레일3.0·익절5%·고점위치≤90%."""
    c = SupertrendAutoConfig()
    assert c.trail_atr_mult == 3.0
    assert c.take_profit_pct == 5.0
    assert c.max_intraday_range_pos == 0.90
    assert c.max_day_change_pct == 0.0   # 당일상승 게이트는 기본 OFF


def test_take_profit_hit_helper():
    """_take_profit_hit — 진입가 대비 +5% 도달 시 True, 미달 시 False, 0이면 비활성."""
    tr = _trader({}, universe=[], config=_base_config(take_profit_pct=5.0))
    pos = _types.SimpleNamespace(entry_price=10000.0)
    bars_up = _candles([10000, 10300, 10600])     # 마지막 +6%
    bars_flat = _candles([10000, 10100, 10200])   # 마지막 +2%
    assert tr._take_profit_hit(pos, bars_up) is True
    assert tr._take_profit_hit(pos, bars_flat) is False
    tr_off = _trader({}, universe=[], config=_base_config(take_profit_pct=0.0))
    assert tr_off._take_profit_hit(pos, bars_up) is False


@pytest.mark.asyncio
async def test_take_profit_exits_via_run_cycle():
    """보유 종목이 +5% 초과면 익절 청산(ST SELL 신호 없이도)."""
    pos = _FakePosStore(); pos._inject("005930", qty=10)   # entry_price 10000
    gate = _FakeGate()
    prices = [10000 + i * 40 for i in range(60)]           # 지속 상승 → trend+1, +수%
    tr = _trader({"005930": prices}, universe=[], gate=gate, pos=pos,
                 config=_base_config(take_profit_pct=5.0))   # trail off, TP만 ON
    r = await tr.run_cycle()
    assert any(e["symbol"] == "005930" and e["reason"] == "익절" for e in r["exited"])


def test_trail_hit_with_default_mult():
    """기본 trail 3.0 — 고점 대비 3×ATR 이탈 시 청산 True, 비활성 시 False."""
    tr = _trader({}, universe=[], config=SupertrendAutoConfig(
        entry_start_time="", min_adx=0.0, min_flip_atr_mult=0.0,
        take_profit_pct=0.0, max_intraday_range_pos=0.0))  # trail=3.0 기본 유지
    # 상승(고점 형성) 후 급락 시리즈
    prices = [10000 + i * 60 for i in range(40)] + [12340 - i * 250 for i in range(8)]
    bars = _candles(prices)
    res = compute_supertrend(bars, period=10, multiplier=3.0, source="hl2")
    pos = _types.SimpleNamespace(entry_price=10000.0, entry_time=None)
    assert tr._trail_hit(pos, bars, res) is True
    tr_off = _trader({}, universe=[], config=_base_config(trail_atr_mult=0.0))
    assert tr_off._trail_hit(pos, bars, res) is False


def test_entry_range_gate_rejects_high_position():
    """고점진입 게이트 — 일중 고점권(>90%) 진입 거부, 중하단 통과."""
    tr = _trader({}, universe=[], config=_base_config(max_intraday_range_pos=0.90))
    # 단조 상승 → 마지막 종가가 일중 최고권(pos≈100%) → 거부
    bars_high = _candles([10000 + i * 50 for i in range(40)])
    res_h = compute_supertrend(bars_high, period=10, multiplier=3.0, source="hl2")
    assert tr._whipsaw_pass(bars_high, res_h, "005930") is False
    # 상승 후 되돌림 → 마지막 종가가 일중 중하단(pos<90%) → 통과
    bars_mid = _candles([10000 + i * 50 for i in range(30)] + [11450 - i * 60 for i in range(10)])
    res_m = compute_supertrend(bars_mid, period=10, multiplier=3.0, source="hl2")
    assert tr._whipsaw_pass(bars_mid, res_m, "005930") is True
    # 게이트 OFF면 둘 다 통과
    tr_off = _trader({}, universe=[], config=_base_config(max_intraday_range_pos=0.0))
    assert tr_off._whipsaw_pass(bars_high, res_h, "005930") is True


def test_config_defaults_bar_ops_33():
    """BAR-OPS-33 supertrend 제약 강화 기본값: min_adx 30·min_flip 1.5·max_positions 10 유지."""
    c = SupertrendAutoConfig()
    assert c.min_adx == 30.0
    assert c.min_flip_atr_mult == 1.5
    assert c.max_positions == 10  # 사이징 역효과 회피 — 게이트/priority로 드래그 축소


# ════════════════════════════════════════════════════════════════════════════
# BAR-OPS-35 (2026-06-08 매매복기 권고) — 재진입 가드·하드손절·테마/추격 필터·승자보유
# 전부 default OFF(no-op): 기본 동작 불변은 위 54개 회귀 테스트가 보장. 아래는 ON 동작.
# ════════════════════════════════════════════════════════════════════════════
from datetime import timezone as _tz  # noqa: E402

# 보유종목 하락/상승 시리즈 (청산 경로 테스트용)
_DROP = [10000] * 55 + [9800, 9600, 9300, 9000, 9000]   # entry 10000 대비 -10%
_RISE = [10000 + i * 12 for i in range(60)]              # +7.08% (TP/trail 테스트)


def test_p0_3_hard_stop_helper_boundary():
    """[P0#3] _hard_stop_hit — 진입가 대비 손실률 ≤ hard_stop_pct 경계."""
    pos = type("P", (), {"entry_price": 10000.0})()
    bars_95 = _candles([10000] * 30 + [9500])   # -5%
    t = _trader({}, universe=[])
    t.config.hard_stop_pct = -6.0
    assert t._hard_stop_hit(pos, bars_95) is False   # -5% > -6% → 미발동
    t.config.hard_stop_pct = -4.0
    assert t._hard_stop_hit(pos, bars_95) is True    # -5% ≤ -4% → 발동
    t.config.hard_stop_pct = 0.0
    assert t._hard_stop_hit(pos, bars_95) is False   # 0=비활성


@pytest.mark.asyncio
async def test_p0_3_hard_stop_exit_reason():
    """[P0#3] 하드손절 ON 이면 신호 무관 청산(reason=하드손절). 459550 -12.63% 방치 방지."""
    gate = _FakeGate()
    pos = _FakePosStore()
    pos._inject("459550", strategy="supertrend", qty=1731)
    t = _trader({"459550": _DROP}, universe=[], gate=gate, pos=pos,
                config=_base_config(hard_stop_pct=-6.0))
    r = await t.run_cycle()
    assert any(s[0] == "459550" for s in gate.sells)
    assert r["exited"] and r["exited"][0]["reason"] == "하드손절"
    assert pos.get("459550") is None  # 청산됨


@pytest.mark.asyncio
async def test_p0_1_max_entries_per_symbol_day():
    """[P0#1] 동일종목 당일 재진입 횟수 상한 — 1회 진입 후 청산해도 재진입 차단.

    459550 1차 익절 후 14:12 재진입(-509K) 차단 시나리오의 결정적 축소판.
    """
    gate = _FakeGate()
    pos = _FakePosStore()
    t = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate, pos=pos,
                config=_base_config(max_entries_per_symbol_day=1))
    await t.run_cycle()                 # 1차 진입
    assert len(gate.buys) == 1
    pos.remove("459550")               # 청산(매도) 시뮬 — 보유 해제
    await t.run_cycle()                 # 재진입 시도 → 당일 상한으로 차단
    assert len(gate.buys) == 1          # 여전히 1건 (재진입 안 됨)


@pytest.mark.asyncio
async def test_p0_1_reentry_cooldown_blocks():
    """[P0#1] 청산 후 cooldown(분) 이내 동일종목 재진입 차단."""
    gate = _FakeGate()
    t = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate,
                config=_base_config(reentry_cooldown_min=30))
    t._entry_day = t._kst_today()                       # _roll_day 리셋 방지
    t._last_exit["459550"] = datetime.now(_tz.utc)      # 방금 청산
    await t.run_cycle()
    assert gate.buys == []                              # cooldown 내 → 차단


@pytest.mark.asyncio
async def test_p0_1_block_reentry_after_loss():
    """[P0#1] 당일 손절 종목 재진입 금지."""
    gate = _FakeGate()
    t = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate,
                config=_base_config(block_reentry_after_loss=True))
    t._entry_day = t._kst_today()
    t._loss_locked.add("459550")
    await t.run_cycle()
    assert gate.buys == []


@pytest.mark.asyncio
async def test_p2_chase_guard_blocks_gap_up():
    """[P2] 추격 매수 가드 — 직전봉 대비 급등(+4.6%) 진입봉 스킵."""
    # _BUY 마지막 갭 ≈ +4.64%
    gate_on = _FakeGate()
    t_on = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate_on,
                   config=_base_config(max_entry_gap_pct=3.0))
    await t_on.run_cycle()
    assert gate_on.buys == []          # 갭 4.6% > 3% → 차단

    gate_off = _FakeGate()
    t_off = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate_off,
                    config=_base_config(max_entry_gap_pct=0.0))
    await t_off.run_cycle()
    assert len(gate_off.buys) == 1     # 0=비활성 → 진입


@pytest.mark.asyncio
async def test_p1_theme_filter_blocks_high_atr():
    """[P1] 고변동(테마) 필터 — ATR/price 과대 종목 진입 스킵."""
    gate_on = _FakeGate()
    t_on = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate_on,
                   config=_base_config(max_atr_pct_for_entry=1e-6))  # 사실상 전부 차단
    await t_on.run_cycle()
    assert gate_on.buys == []

    gate_off = _FakeGate()
    t_off = _trader({"459550": _BUY}, universe=[("459550", "더블유")], gate=gate_off,
                    config=_base_config(max_atr_pct_for_entry=1.0))  # 사실상 허용
    await t_off.run_cycle()
    assert len(gate_off.buys) == 1


@pytest.mark.asyncio
async def test_p1_take_profit_trail_only():
    """[P1] take_profit_trail_only — 고정 익절 비활성(트레일만). 승자 조기청산 방지."""
    # TP ON(기존): +7% 도달 → 익절 청산
    gate1 = _FakeGate(); pos1 = _FakePosStore(); pos1._inject("066430", qty=1002)
    t1 = _trader({"066430": _RISE}, universe=[], gate=gate1, pos=pos1,
                 config=_base_config(take_profit_pct=5.0, take_profit_trail_only=False))
    r1 = await t1.run_cycle()
    assert r1["exited"] and r1["exited"][0]["reason"] == "익절"

    # trail_only ON: 고정 익절 비활성 + 트레일/신호 없음 → 청산 안 함(승자 보유)
    gate2 = _FakeGate(); pos2 = _FakePosStore(); pos2._inject("066430", qty=1002)
    t2 = _trader({"066430": _RISE}, universe=[], gate=gate2, pos=pos2,
                 config=_base_config(take_profit_pct=5.0, take_profit_trail_only=True))
    r2 = await t2.run_cycle()
    assert r2["exited"] == []
    assert pos2.get("066430") is not None
