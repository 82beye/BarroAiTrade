"""호가 스캘핑 전략 (ob_scalp) — 마이크로구조 신호 + 진입판정 + ka10004 파서 테스트."""
from __future__ import annotations

import pytest

from datetime import datetime, timezone

from backend.core.gateway.kiwoom_native_orderbook import parse_orderbook, _abs_int
from backend.core.strategy.ob_scalp import (
    OBScalpStrategy, OBScalpParams,
    krx_tick_size, order_flow_imbalance, spread_ticks, microprice, best_bid_ask, top_depth,
    net_return_pct, breakeven_ticks, ROUND_TRIP_COST_PCT,
)
from backend.models.market import OHLCV, OrderBook, MarketType
from backend.models.strategy import AnalysisContext


def _ctx(orderbook):
    candle = OHLCV(symbol="005930", timestamp=datetime.now(timezone.utc),
                   open=10000, high=10010, low=9990, close=10000, volume=1000,
                   market_type=MarketType.STOCK)
    return AnalysisContext(symbol="005930", name="삼성전자", candles=[candle],
                           market_type=MarketType.STOCK, orderbook=orderbook)


def _book(bids, asks):
    return OrderBook(symbol="005930", bids=bids, asks=asks,
                     timestamp=datetime.now(timezone.utc), market_type=MarketType.STOCK)


# 강한 매수우위 + 좁은 스프레드 + 충분 깊이 → 진입
STRONG = _book(bids=[(10000, 500), (9990, 400), (9980, 300)],
               asks=[(10010, 50), (10020, 40), (10030, 30)])
# 균형 → 미진입
BALANCED = _book(bids=[(10000, 100), (9990, 100), (9980, 100)],
                 asks=[(10010, 100), (10020, 100), (10030, 100)])
# 넓은 스프레드 → 미진입
WIDE = _book(bids=[(10000, 500), (9990, 400)], asks=[(10100, 50), (10110, 40)])



@pytest.fixture(autouse=True)
def _legacy_costs(monkeypatch):
    """[BAR-OPS-39] 비용 상수가 브로커 실측(왕복 0.55%)으로 교체됨 — 본 파일의 메커니즘
    테스트들은 설계 당시 요율(0.015%/0.18%) 기준 시나리오(2.1틱 본전 등)라, 요율을 고정해
    '비용 게이트/TP 내재화 메커니즘'만 검증한다. 실측 요율 검증은 test_bar_ops_39.py.
    (함수들은 모듈 전역을 호출 시점에 읽으므로 monkeypatch 가 적용된다.)"""
    import backend.core.strategy.ob_scalp as ob
    monkeypatch.setattr(ob, "COMMISSION_RATE", 0.00015)
    monkeypatch.setattr(ob, "TAX_RATE", 0.0018)
    monkeypatch.setattr(ob, "ROUND_TRIP_COST_PCT", 2 * 0.00015 + 0.0018)


class TestTickSize:
    def test_bands(self):
        assert krx_tick_size(1500) == 1
        assert krx_tick_size(3000) == 5
        assert krx_tick_size(10000) == 10
        assert krx_tick_size(30000) == 50
        assert krx_tick_size(100000) == 100
        assert krx_tick_size(300000) == 500
        assert krx_tick_size(700000) == 1000


class TestSignals:
    def test_imbalance_bid_heavy_positive(self):
        ofi = order_flow_imbalance(STRONG.bids, STRONG.asks, levels=3)
        assert ofi > 0.5  # 매수우위

    def test_imbalance_balanced_zero(self):
        assert abs(order_flow_imbalance(BALANCED.bids, BALANCED.asks, levels=3)) < 1e-6

    def test_spread_ticks(self):
        bb, ba = best_bid_ask(STRONG.bids, STRONG.asks)
        assert spread_ticks(bb, ba, krx_tick_size(ba)) == 1.0  # (10010-10000)/10

    def test_microprice_tilts_to_ask_when_bid_heavy(self):
        bb, ba = best_bid_ask(STRONG.bids, STRONG.asks)
        mp = microprice(bb, ba, 500, 50)  # bid_qty>>ask_qty
        assert mp > (bb + ba) / 2  # 상방

    def test_best_bid_ask_unsorted(self):
        bb, ba = best_bid_ask([(9990, 100), (10000, 200)], [(10030, 10), (10010, 20)])
        assert bb == 10000 and ba == 10010

    def test_top_depth_bottleneck(self):
        assert top_depth(STRONG.bids, STRONG.asks, 3) == 120  # min(1200, 120)


class TestStrategy:
    def test_no_orderbook_none(self):
        assert OBScalpStrategy()._analyze_v2(_ctx(None)) is None

    def test_strong_imbalance_signals(self):
        sig = OBScalpStrategy()._analyze_v2(_ctx(STRONG))
        assert sig is not None
        assert sig.signal_type == "ob_scalp"
        assert sig.price == 10010  # best_ask 진입
        assert sig.metadata["ofi"] > 0.5
        assert 5.0 <= sig.score <= 10.0

    def test_balanced_no_signal(self):
        assert OBScalpStrategy()._analyze_v2(_ctx(BALANCED)) is None

    def test_wide_spread_no_signal(self):
        assert OBScalpStrategy()._analyze_v2(_ctx(WIDE)) is None

    def test_low_depth_no_signal(self):
        thin = _book(bids=[(10000, 10), (9990, 5)], asks=[(10010, 8), (10020, 5)])
        assert OBScalpStrategy(OBScalpParams(min_depth=100))._analyze_v2(_ctx(thin)) is None

    def test_exit_plan_cost_aware_tp(self):
        from backend.models.position import Position
        pos = Position(symbol="005930", name="삼성전자", quantity=10, avg_price=10000,
                       current_price=10000, realized_pnl=0, unrealized_pnl=0, pnl_pct=0,
                       market_type=MarketType.STOCK, entry_time=datetime.now(timezone.utc),
                       strategy_id="ob_scalp_v1", total_value=100000)
        plan = OBScalpStrategy(OBScalpParams(profit_ticks=2, sl_ticks=3)).exit_plan(pos, _ctx(STRONG))
        # tick=10, breakeven=ceil(2.1)=3틱, TP=3+2=5틱 → 10050. SL=-3*10/10000=-0.003
        assert float(plan.take_profits[0].price) == 10050
        assert abs(float(plan.stop_loss.fixed_pct) - (-0.003)) < 1e-9
        # ★ TP 도달 시 수수료+제세금 차감 후 순수익 > 0 보장
        assert net_return_pct(10000, 10050) > 0


class TestCostModel:
    """수수료+제세금 내재화 — 스캘핑 생존의 핵심."""

    def test_round_trip_cost(self):
        # [BAR-OPS-39] 브로커 실측: 수수료 0.175%×2 + 거래세 0.20% = 0.55%
        #   (import-time 바인딩 상수 — legacy fixture 와 무관하게 실측값이어야 함)
        assert abs(ROUND_TRIP_COST_PCT - 0.0055) < 1e-9

    def test_breakeven_ticks(self):
        assert abs(breakeven_ticks(10000, 10) - 2.1) < 0.01   # 0.21%×10000/10
        assert breakeven_ticks(1500, 1) > 3.0                  # 저가주 = 비용 과중(3.15틱)

    def test_net_return_includes_costs(self):
        # +1틱(10000→10010): gross +0.1%, 비용 ~0.21% → 순 음수 (스캘핑 함정)
        assert net_return_pct(10000, 10010) < 0
        # +3틱(10030): gross +0.3% - 0.21% → 겨우 양수
        assert net_return_pct(10000, 10030) > 0
        # +5틱(10050): 분명한 순이익
        assert net_return_pct(10000, 10050) > net_return_pct(10000, 10030)

    def test_cost_gate_rejects_costly(self):
        # 저가주(틱 대비 비용 과중): breakeven 3.15틱 > max_breakeven 3 → 진입 차단
        cheap = OrderBook(symbol="X", bids=[(1500, 800), (1499, 500), (1498, 400)],
                          asks=[(1501, 60), (1502, 40), (1503, 30)],
                          timestamp=datetime.now(timezone.utc), market_type=MarketType.STOCK)
        ctx = _ctx(cheap)
        # 강매수우위·좁은스프레드여도 비용 과중이면 차단
        assert OBScalpStrategy(OBScalpParams(max_breakeven_ticks=3.0))._analyze_v2(ctx) is None
        # 허용 임계 높이면 통과(신호 발생)
        assert OBScalpStrategy(OBScalpParams(max_breakeven_ticks=5.0))._analyze_v2(ctx) is not None

    def test_signal_carries_net_tp(self):
        sig = OBScalpStrategy()._analyze_v2(_ctx(STRONG))
        assert sig is not None
        assert sig.metadata["net_tp_pct"] > 0  # TP 목표는 비용 차감 후 순(+)
        assert sig.metadata["breakeven_ticks"] > 0


class TestKa10004Parser:
    def test_abs_int(self):
        assert _abs_int("+1,234") == 1234
        assert _abs_int("-5678") == 5678
        assert _abs_int("") == 0
        assert _abs_int(None) == 0

    def test_parse_orderbook(self):
        data = {
            "sel_1th_pre_bid": "+10010", "sel_1th_pre_req": "50",
            "sel_2th_pre_bid": "10020", "sel_2th_pre_req": "40",
            "buy_1th_pre_bid": "10000", "buy_1th_pre_req": "500",
            "buy_2th_pre_bid": "9990", "buy_2th_pre_req": "400",
            "buy_3th_pre_bid": "0", "buy_3th_pre_req": "0",  # 빈 단계 제외
        }
        ob = parse_orderbook(data, "005930")
        assert ob.asks[0] == (10010.0, 50.0)   # best ask 최저
        assert ob.bids[0] == (10000.0, 500.0)  # best bid 최고
        assert len(ob.bids) == 2  # qty>0 단계만
