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
    tp_ticks: int = 3                 # 익절 틱
    sl_ticks: int = 2                 # 손절 틱
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

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=float(ba),  # 즉시 체결 가정 → 최우선 매도호가 매수
            signal_type="ob_scalp",
            score=round(score, 2),
            reason=(f"호가스캘핑: OFI {ofi:+.2f}(≥{p.imb_threshold}) · 스프레드 {sp:.0f}틱 · "
                    f"마이크로 {mp:.0f}>mid {mid:.0f} · 깊이 {depth:.0f}"),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={"ofi": round(ofi, 3), "spread_ticks": round(sp, 2),
                      "microprice": round(mp, 1), "depth": round(depth, 0), "tick": tick},
        )

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """틱 기반 타이트 청산 — 스캘핑 (TP +tp_ticks / SL -sl_ticks / 시간청산)."""
        p = self.params
        avg = Decimal(str(position.avg_price))
        tick = Decimal(str(krx_tick_size(float(avg))))
        tp_price = avg + tick * Decimal(p.tp_ticks)
        sl_pct = -(tick * Decimal(p.sl_ticks)) / avg  # 음수 비율
        time_exit = p.time_exit if ctx.market_type == MarketType.STOCK else None
        return ExitPlan(
            take_profits=[TakeProfitTier(price=tp_price, qty_pct=Decimal("1.0"),
                                         condition=f"스캘핑 TP +{p.tp_ticks}틱")],
            stop_loss=StopLoss(fixed_pct=sl_pct),
            time_exit=time_exit,
            breakeven_trigger=tick / avg,  # +1틱 도달 시 본전 잠금
        )

    def health_check(self) -> dict[str, Any]:
        return {"strategy_id": self.STRATEGY_ID, "ready": True,
                "needs_orderbook": True, "backtestable": False}


__all__ = ["OBScalpStrategy", "OBScalpParams", "krx_tick_size",
           "order_flow_imbalance", "spread_ticks", "microprice", "best_bid_ask", "top_depth"]
