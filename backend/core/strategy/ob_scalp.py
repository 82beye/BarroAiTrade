"""호가창(L2 orderbook) 초단타 스캘핑 전략 — 2026-05-30 신규.

키움 REST 호가(ka10004 주식호가요청) 기반 마이크로구조 신호로 진입을 판정한다.
AnalysisContext.orderbook(OrderBook: bids/asks=(price,qty) 단계별) 의 첫 소비 전략.

신호 (전문 트레이더 마이크로구조):
  - 호가 잔량 불균형 OFI = (Σ매수잔량 - Σ매도잔량)/(합), 상위 N단계. +면 매수우위.
  - 스프레드(틱): (best_ask - best_bid)/tick. 좁을 때만 진입(스캘핑 R:R 확보).
  - 마이크로프라이스 = (best_bid·ask_qty + best_ask·bid_qty)/(bid_qty+ask_qty).
    큰 잔량 반대쪽으로 끌림 → 단기 방향 예측. > mid 면 상방.
  - 깊이(depth): 진입·청산 유동성 하한.
진입(롱): 스프레드≤max + OFI≥imb + 마이크로프라이스 상방 + 깊이 충분 → best_ask 진입.
청산: 틱 기반 타이트 TP/SL + 시간청산(exit_plan).

⚠️ 한계(반드시 인지):
  1) L2 호가 이력이 없어 **백테스트 불가** — 페이퍼/shadow 검증 전용.
  2) REST 폴링 지연(수백 ms)으로 마이크로구조 엣지 대부분 소실 — 보조 시그널/페이퍼 용도.
     진정한 호가 스캘핑은 WebSocket 실시간 + 저지연 체결이 전제(본 구현은 그 토대).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from backend.core.strategy.base import Strategy
from backend.models.market import MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext, ExitPlan, StopLoss, TakeProfitTier


# ─── KRX 호가단위 (2023 개편) ─────────────────────────────────────────────
def krx_tick_size(price: float) -> int:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


# ─── 비용 모델 (수수료 + 제세금) — 스캘핑 생존의 핵심 ─────────────────────
# [BAR-OPS-39] 브로커 실측 비용(trading_costs)으로 교체 — 종전 가정(왕복 ≈0.21%)의
#   2.6배(≈0.55%). breakeven_ticks 가 그만큼 늘어 진입 게이트·TP 가 보수화된다(의도).
from backend.core.trading_costs import (
    COMMISSION_RATE as _CR, TAX_RATE_SELL as _TR, ROUND_TRIP_COST_RATE as _RT,
)
COMMISSION_RATE = float(_CR)   # 매수·매도 각 (실측 0.175%)
TAX_RATE = float(_TR)          # 매도 (실측 0.20%)
ROUND_TRIP_COST_PCT = float(_RT)   # 왕복 ≈ 0.55% (동가 근사)


def net_return_pct(buy_price: float, sell_price: float) -> float:
    """수수료(양방)+제세금(매도) 차감 순수익률% — paper 평가/검증용 정밀 계산."""
    if buy_price <= 0:
        return 0.0
    gross = sell_price - buy_price
    commission = COMMISSION_RATE * (buy_price + sell_price)
    tax = TAX_RATE * sell_price
    return (gross - commission - tax) / buy_price * 100


def breakeven_ticks(price: float, tick: int, slippage_ticks: float = 0.0) -> float:
    """왕복 비용(수수료+제세금)을 커버하는 데 필요한 틱 수 (+ 슬리피지 가정).

    예: 10,000원/틱10 → 0.21%×10000/10 = 2.1틱. 즉 +2틱 익절은 순손실, +3틱이 최소 본전.
    """
    if price <= 0 or tick <= 0:
        return 0.0
    return ROUND_TRIP_COST_PCT * price / tick + slippage_ticks


# ─── 호가 마이크로구조 신호 (순수 함수, 테스트 대상) ──────────────────────
def _f(x) -> float:
    return float(x)


def best_bid_ask(bids: Sequence, asks: Sequence) -> tuple[Optional[float], Optional[float]]:
    """최우선 매수/매도가 — 정렬 가정 없이 산출."""
    bb = max((_f(p) for p, q in bids if _f(q) > 0), default=None)
    ba = min((_f(p) for p, q in asks if _f(q) > 0), default=None)
    return bb, ba


def _qty_at(levels: Sequence, price: float) -> float:
    for p, q in levels:
        if abs(_f(p) - price) < 1e-9:
            return _f(q)
    return 0.0


def order_flow_imbalance(bids: Sequence, asks: Sequence, levels: int = 3) -> float:
    """상위 levels 단계 호가잔량 불균형 [-1,+1]. +면 매수우위."""
    top_bids = sorted((( _f(p), _f(q)) for p, q in bids if _f(q) > 0), key=lambda x: -x[0])[:levels]
    top_asks = sorted((( _f(p), _f(q)) for p, q in asks if _f(q) > 0), key=lambda x: x[0])[:levels]
    bq = sum(q for _, q in top_bids)
    aq = sum(q for _, q in top_asks)
    tot = bq + aq
    return (bq - aq) / tot if tot > 0 else 0.0


def spread_ticks(bb: Optional[float], ba: Optional[float], tick: int) -> Optional[float]:
    if bb is None or ba is None or tick <= 0:
        return None
    return (ba - bb) / tick


def microprice(bb: float, ba: float, bid_qty: float, ask_qty: float) -> float:
    """최우선 잔량 가중 mid. 큰 잔량 반대쪽으로 끌림 → 단기 방향."""
    tot = bid_qty + ask_qty
    if tot <= 0:
        return (bb + ba) / 2
    return (bb * ask_qty + ba * bid_qty) / tot


def top_depth(bids: Sequence, asks: Sequence, levels: int = 3) -> float:
    """상위 levels 양측 잔량 합의 최소값(병목 유동성)."""
    bq = sum(_f(q) for p, q in sorted(bids, key=lambda x: -_f(x[0]))[:levels])
    aq = sum(_f(q) for p, q in sorted(asks, key=lambda x: _f(x[0]))[:levels])
    return min(bq, aq)


@dataclass
class OBScalpParams:
    imb_threshold: float = 0.55       # OFI 임계 (≥ 면 매수우위 진입)
    max_spread_ticks: float = 2.0     # 스프레드 상한(틱) — 좁을 때만
    levels: int = 3                   # 불균형/깊이 평가 단계
    min_depth: float = 100.0          # 상위 단계 최소 잔량(주)
    profit_ticks: int = 2             # 비용 차감 후 순이익 목표 틱 (TP = 비용커버틱 + profit_ticks)
    sl_ticks: int = 3                 # 손절 틱 (순손실 = sl_ticks틱 + 비용)
    max_breakeven_ticks: float = 4.0  # 비용 커버에 이보다 많은 틱 필요 시 진입 skip(비용 과중)
    slippage_ticks: float = 0.0       # 보수적 슬리피지 가정(틱)
    time_exit: Optional[dtime] = dtime(15, 10)  # 스캘핑 강제청산(장 마감 전)
    min_price: float = 1_000.0        # 최소 가격(동전주 제외)


class OBScalpStrategy(Strategy):
    """호가 마이크로구조 초단타 스캘핑."""

    STRATEGY_ID = "ob_scalp_v1"

    def __init__(self, params: Optional[OBScalpParams] = None) -> None:
        self.params = params or OBScalpParams()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        p = self.params
        book = getattr(ctx, "orderbook", None)
        if book is None:
            return None
        bids = getattr(book, "bids", None)
        asks = getattr(book, "asks", None)
        if not bids or not asks:
            return None

        bb, ba = best_bid_ask(bids, asks)
        if bb is None or ba is None or bb <= 0 or ba <= 0 or ba <= bb:
            return None
        if bb < p.min_price:
            return None

        tick = krx_tick_size(ba)
        sp = spread_ticks(bb, ba, tick)
        if sp is None or sp > p.max_spread_ticks:
            return None

        # 비용(수수료+제세금) 게이트: 비용 커버에 너무 많은 틱이 필요한 저가·소틱주는 진입 차단.
        be = breakeven_ticks(ba, tick, p.slippage_ticks)
        if be > p.max_breakeven_ticks:
            return None

        depth = top_depth(bids, asks, p.levels)
        if depth < p.min_depth:
            return None

        ofi = order_flow_imbalance(bids, asks, p.levels)
        if ofi < p.imb_threshold:
            return None

        bbq = _qty_at(bids, bb)
        baq = _qty_at(asks, ba)
        mp = microprice(bb, ba, bbq, baq)
        mid = (bb + ba) / 2
        if mp <= mid:  # 마이크로프라이스가 상방이 아니면 진입 안 함
            return None

        # 점수: OFI [imb..1] → [5..10], 마이크로프라이스 상방 강도 보너스
        score = 5.0 + (ofi - p.imb_threshold) / max(1e-9, 1 - p.imb_threshold) * 5.0
        mp_tilt = (mp - mid) / (ba - bb) if (ba - bb) > 0 else 0.0  # 0~1
        score = min(10.0, score + mp_tilt)

        # 비용 내재화 익절 목표: 비용커버틱 + 순이익틱 → TP 도달 시 반드시 순(+).
        tp_ticks_eff = math.ceil(be) + p.profit_ticks
        tp_target = ba + tp_ticks_eff * tick
        net_tp = net_return_pct(ba, tp_target)

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=float(ba),  # 즉시 체결 가정 → 최우선 매도호가 매수
            signal_type="ob_scalp",
            score=round(score, 2),
            reason=(f"호가스캘핑: OFI {ofi:+.2f} · 스프레드 {sp:.0f}틱 · 마이크로 {mp:.0f}>mid {mid:.0f} · "
                    f"깊이 {depth:.0f} · TP {tp_ticks_eff}틱(비용커버 {be:.1f}+순익 {p.profit_ticks}) 순+{net_tp:.2f}%"),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={"ofi": round(ofi, 3), "spread_ticks": round(sp, 2), "microprice": round(mp, 1),
                      "depth": round(depth, 0), "tick": tick, "breakeven_ticks": round(be, 2),
                      "tp_ticks": tp_ticks_eff, "tp_target": float(tp_target), "net_tp_pct": round(net_tp, 3)},
        )

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """비용 내재화 청산 — TP = 비용커버틱 + 순이익틱 (도달 시 수수료+제세금 차감 후 순(+) 보장).
        SL -sl_ticks틱, 비용커버틱 도달 시 본전 잠금, 시간청산."""
        p = self.params
        avg_f = float(position.avg_price)
        avg = Decimal(str(position.avg_price))
        tick_i = krx_tick_size(avg_f)
        tick = Decimal(str(tick_i))
        be_ticks = math.ceil(breakeven_ticks(avg_f, tick_i, p.slippage_ticks))
        tp_ticks = be_ticks + p.profit_ticks
        tp_price = avg + tick * Decimal(tp_ticks)
        sl_pct = -(tick * Decimal(p.sl_ticks)) / avg  # 음수 비율
        time_exit = p.time_exit if ctx.market_type == MarketType.STOCK else None
        return ExitPlan(
            take_profits=[TakeProfitTier(
                price=tp_price, qty_pct=Decimal("1.0"),
                condition=f"스캘핑 TP +{tp_ticks}틱(비용커버 {be_ticks}+순익 {p.profit_ticks})")],
            stop_loss=StopLoss(fixed_pct=sl_pct),
            time_exit=time_exit,
            breakeven_trigger=tick * Decimal(be_ticks) / avg,  # 비용커버 도달 시 본전 잠금
        )

    def health_check(self) -> dict[str, Any]:
        return {"strategy_id": self.STRATEGY_ID, "ready": True,
                "needs_orderbook": True, "backtestable": False}


__all__ = ["OBScalpStrategy", "OBScalpParams", "krx_tick_size",
           "order_flow_imbalance", "spread_ticks", "microprice", "best_bid_ask", "top_depth",
           "net_return_pct", "breakeven_ticks", "ROUND_TRIP_COST_PCT",
           "COMMISSION_RATE", "TAX_RATE"]
