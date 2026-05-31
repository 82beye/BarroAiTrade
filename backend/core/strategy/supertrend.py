"""Supertrend (ATR 밴드 추세전환) 전략 — 2026-05-31 신규.

TradingView Pine Script "Supertrend" 의 결정적 이식. **5분봉** 기준 추세전환 신호.

원리 (Pine 원본 충실 이식):
  - src = hl2 = (high+low)/2.
  - ATR(10) 은 RMA(Wilder) 평활 (Pine `changeATR=true` → `ta.atr`).
  - up = src - mult*atr ; close[1] > up1 면 up = max(up, up1)  (상승추세 밴드 lock-up).
  - dn = src + mult*atr ; close[1] < dn1 면 dn = min(dn, dn1)  (하락추세 밴드 lock-down).
  - trend 전환: (-1→1) close > dn1 = **buySignal** / (1→-1) close < up1 = **sellSignal**.
진입(롱): 상승 추세전환(buySignal) 발생 + 추세 유지 중 → EntrySignal 발행.
청산: 반대 전환(하락추세) 시 청산 — Supertrend 라인을 트레일링 스탑으로 근사(exit_plan).

종목 유니버스(스캔)는 **별도 모듈**(최근 7일 거래대금 선별)이 담당하며, 본 전략은
주어진 종목의 5분봉에 대해 신호만 산출한다 (signal-only, 실거래 송출 없음).

Pine 원본 기본값: ATR Period 10, Multiplier 3.0, src=hl2.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime
from decimal import Decimal
from typing import Any, List, Optional, Sequence

from backend.core.strategy.base import Strategy
from backend.models.market import MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal, ExitSignal
from backend.models.strategy import AnalysisContext, ExitPlan, StopLoss

logger = logging.getLogger(__name__)


# ─── 결과 컨테이너 ───────────────────────────────────────────────────────────
@dataclass
class SupertrendResult:
    """compute_supertrend 결과 — 입력 candles 와 동일 길이의 봉별 시계열."""

    trend: List[int]            # +1 상승추세 / -1 하락추세
    supertrend: List[float]     # 추세선 (trend==1 → up밴드, trend==-1 → dn밴드)
    up: List[float]             # 상승추세 추적 밴드(하단 = 지지)
    dn: List[float]             # 하락추세 추적 밴드(상단 = 저항)
    atr: List[float]            # RMA ATR (초기 구간 0.0)
    buy_signals: List[bool]     # trend -1→1 전환 봉
    sell_signals: List[bool]    # trend 1→-1 전환 봉


# ─── 결정적 지표 계산 (순수 함수, 테스트 대상) ──────────────────────────────
def _rma(values: Sequence[float], period: int) -> List[float]:
    """Wilder RMA (= Pine ta.rma / ta.atr 내부 평활). seed = 첫 period 개 SMA.

    out[i<seed_idx] = nan. period 보다 데이터가 적으면 가용 길이로 1회 SMA seed.
    """
    n = len(values)
    out: List[float] = [float("nan")] * n
    if n == 0:
        return out
    p = max(1, min(period, n))
    seed = sum(values[:p]) / p
    out[p - 1] = seed
    prev = seed
    for i in range(p, n):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def _true_ranges(candles) -> List[float]:
    """True Range 시계열. 첫 봉은 prev close 부재 → high-low."""
    trs: List[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(float(c.high) - float(c.low))
            continue
        pc = float(candles[i - 1].close)
        trs.append(max(
            float(c.high) - float(c.low),
            abs(float(c.high) - pc),
            abs(float(c.low) - pc),
        ))
    return trs


def _src_series(candles, source: str) -> List[float]:
    if source == "close":
        return [float(c.close) for c in candles]
    if source == "hlc3":
        return [(float(c.high) + float(c.low) + float(c.close)) / 3 for c in candles]
    # 기본: hl2 (Pine 원본 default)
    return [(float(c.high) + float(c.low)) / 2 for c in candles]


def compute_supertrend(
    candles,
    period: int = 10,
    multiplier: float = 3.0,
    source: str = "hl2",
) -> SupertrendResult:
    """Pine Script Supertrend 결정적 이식.

    Args:
        candles: OHLCV 리스트 (오래된 → 최신 순).
        period: ATR 기간 (Pine ATR Period, 기본 10).
        multiplier: ATR 배수 (Pine Multiplier, 기본 3.0).
        source: 밴드 중심선 ("hl2"|"close"|"hlc3", 기본 hl2).

    Returns:
        SupertrendResult — candles 와 동일 길이. 빈 입력 시 빈 결과(객체는 항상 반환).
    """
    n = len(candles)
    if n == 0:
        return SupertrendResult([], [], [], [], [], [], [])

    src = _src_series(candles, source)
    close = [float(c.close) for c in candles]
    atr_raw = _rma(_true_ranges(candles), period)
    # nan(초기 구간) → 0.0 (밴드 계산 안정화). a == a 는 nan 판별.
    atr = [a if a == a else 0.0 for a in atr_raw]

    up: List[float] = [0.0] * n
    dn: List[float] = [0.0] * n
    trend: List[int] = [1] * n

    for i in range(n):
        basic_up = src[i] - multiplier * atr[i]
        basic_dn = src[i] + multiplier * atr[i]
        if i == 0:
            up[i] = basic_up
            dn[i] = basic_dn
            trend[i] = 1
            continue
        up1 = up[i - 1]
        dn1 = dn[i - 1]
        # 밴드 캐리오버 (close[1] = 직전 종가 기준 lock)
        up[i] = max(basic_up, up1) if close[i - 1] > up1 else basic_up
        dn[i] = min(basic_dn, dn1) if close[i - 1] < dn1 else basic_dn
        # 추세 전환 (직전 밴드 dn1/up1 과 현재 종가 비교)
        prev = trend[i - 1]
        if prev == -1 and close[i] > dn1:
            trend[i] = 1
        elif prev == 1 and close[i] < up1:
            trend[i] = -1
        else:
            trend[i] = prev

    buy: List[bool] = [False] * n
    sell: List[bool] = [False] * n
    for i in range(1, n):
        if trend[i] == 1 and trend[i - 1] == -1:
            buy[i] = True
        elif trend[i] == -1 and trend[i - 1] == 1:
            sell[i] = True

    supertrend = [up[i] if trend[i] == 1 else dn[i] for i in range(n)]
    return SupertrendResult(trend, supertrend, up, dn, atr, buy, sell)


# ─── 전략 파라미터 ───────────────────────────────────────────────────────────
@dataclass
class SupertrendParams:
    atr_period: int = 10          # Pine ATR Period
    multiplier: float = 3.0       # Pine Multiplier
    source: str = "hl2"           # 밴드 중심선 (Pine src=hl2)
    min_candles: int = 30         # ATR(10) 안정화 최소 봉수

    # 진입 트리거 — 슈퍼트렌드 **buy 시그널**(trend -1→1 전환 봉) 발생 시에만 진입.
    #   마지막 N봉 내 buySignal 이 있어야 진입. 5분봉 폴링 타이밍 흔들림 흡수용 N=2 default.
    #   "상승추세 동안 매 사이클 매수"가 아니라 "BUY 전환 이벤트 1회"가 되도록 함 (청산과 대칭).
    #   None 으로 두면 현재 상승추세(trend==1) 지속 중 매봉 진입 (추세추종 스크리너 모드).
    entry_lookback: Optional[int] = 2

    # 청산 트리거 — 슈퍼트렌드 **sell 시그널**(trend 1→-1 전환 봉) 발생 시에만 청산.
    #   마지막 N봉 내 sellSignal 이 있어야 청산. 5분봉 폴링 타이밍 흔들림 흡수용 N=2 default.
    #   "하락추세 동안 매 사이클 청산"이 아니라 "전환 이벤트 1회"가 되도록 함 (진입과 대칭).
    exit_lookback: int = 2

    # 변동성 필터 (운영 override) — ATR% < min_atr_pct 면 진입 거부. 0 이면 비활성.
    min_atr_pct: float = 0.0
    atr_n: int = 14

    # 진입 시간 게이트 (운영 override) — last candle.time() >= cutoff 면 차단. None 비활성.
    entry_time_cutoff: Optional[dtime] = None

    # 청산 SL clamp (Supertrend 라인 트레일링 근사) — 음수 비율.
    sl_min_pct: float = -0.01     # 최소 손절 폭
    sl_max_pct: float = -0.08     # 최대 손절 폭
    time_exit: Optional[dtime] = dtime(15, 10)  # 장 마감 전 강제청산(주식)


class SupertrendStrategy(Strategy):
    """Supertrend ATR 밴드 추세전환 전략 (5분봉)."""

    STRATEGY_ID = "supertrend_v1"

    def __init__(self, params: Optional[SupertrendParams] = None) -> None:
        self.params = params or SupertrendParams()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        p = self.params
        candles = ctx.candles
        if len(candles) < p.min_candles:
            return None

        # 변동성 필터 — 저변동·고가주 가짜 시그널 차단 (운영 override)
        if p.min_atr_pct > 0:
            from backend.core.strategy.indicators import atr_pct
            if atr_pct(candles, n=p.atr_n) < p.min_atr_pct:
                logger.debug("%s: ATR%% 임계 미달 — supertrend 진입 거부", ctx.symbol)
                return None

        # 진입 시간 게이트 — 장 후반 진입 차단 (운영 override)
        if p.entry_time_cutoff is not None:
            if candles[-1].timestamp.time() >= p.entry_time_cutoff:
                logger.debug("%s: 진입 시간 cutoff 도달 — supertrend 진입 거부", ctx.symbol)
                return None

        res = compute_supertrend(
            candles, period=p.atr_period, multiplier=p.multiplier, source=p.source,
        )
        # 현재 상승추세여야 진입 (trend==1) — buy 직후 추세 유지 확인용 안전판.
        if not res.trend or res.trend[-1] != 1:
            return None
        # 진입 트리거: 최근 entry_lookback 봉 내 BUY 시그널(전환 봉)이 있어야 진입.
        #   None 이면 추세추종 스크리너 모드(상승추세 지속 중 매봉 진입).
        if p.entry_lookback is not None:
            lb = max(1, p.entry_lookback)
            if not res.buy_signals or not any(res.buy_signals[-lb:]):
                return None

        c = candles[-1]
        price = float(c.close)
        st_line = res.supertrend[-1]
        atr_v = res.atr[-1]

        # 점수(6~10): 추세선 대비 종가 여유 + 변동성(ATR%) 가산
        dist_pct = (price - st_line) / price if price > 0 else 0.0
        atr_pct_v = atr_v / price if price > 0 else 0.0
        score = 6.0 + min(max(dist_pct, 0.0) / 0.02, 1.0) * 2.0
        score += min(atr_pct_v / 0.02, 1.0) * 2.0

        if True in res.buy_signals:
            flip_bar = len(res.buy_signals) - 1 - res.buy_signals[::-1].index(True)
            bars_since = (len(candles) - 1) - flip_bar
        else:
            bars_since = None  # 시작부터 상승추세 (하락→상승 전환 봉 없음)

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=price,
            signal_type="supertrend",
            score=round(min(score, 10.0), 2),
            reason=(f"[슈퍼트렌드] 상승추세"
                    f"{f' (전환 {bars_since}봉 전)' if bars_since is not None else ''} | "
                    f"ST {st_line:.0f} | ATR {atr_v:.0f} ({atr_pct_v*100:.1f}%)"),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(),
            metadata={
                "supertrend": round(st_line, 2),
                "atr": round(atr_v, 2),
                "trend": res.trend[-1],
                "bars_since_flip": bars_since,
                "multiplier": p.multiplier,
                "atr_period": p.atr_period,
            },
        )

    def exit_on_signal(
        self,
        position: Position,
        ctx: AnalysisContext,
        current_price: Decimal,
    ) -> Optional[ExitSignal]:
        """슈퍼트렌드 **sell 시그널**(trend 1→-1 전환 봉) 발생 시 보유 롱 포지션 청산.

        진입(buySignal, trend -1→1)의 정확한 거울상. Pine 의
        `sellSignal = trend == -1 and trend[1] == 1` 이 발생한 봉에서만 청산한다.
        "이미 하락추세인 동안 매 사이클 청산"이 아니라 **전환 이벤트 1회**가 트리거.
        최근 exit_lookback 봉 내 sellSignal 이 있어야 청산(폴링 타이밍 흔들림 흡수).
        데이터 부족 시 None (강제청산 안 함 — 가격 SL 이 안전망).
        """
        p = self.params
        candles = ctx.candles
        if len(candles) < p.min_candles:
            return None
        # 롱 포지션만 대상 (현물 long-only). side 비-long 이면 평가 안 함.
        if (getattr(position, "side", "long") or "long") != "long":
            return None

        res = compute_supertrend(
            candles, period=p.atr_period, multiplier=p.multiplier, source=p.source,
        )
        if not res.sell_signals:
            return None
        # 최근 N봉 내 sell 시그널(전환 봉)이 있어야 청산. 없으면 보유 유지.
        lb = max(1, p.exit_lookback)
        if not any(res.sell_signals[-lb:]):
            return None

        entry = float(position.avg_price)
        cur = float(current_price)
        pnl_pct = (cur - entry) / entry * 100 if entry > 0 else 0.0
        st_line = res.supertrend[-1]

        # 전환 신선도 (몇 봉 전 sell 시그널인지) — 진단용
        flip_bar = len(res.sell_signals) - 1 - res.sell_signals[::-1].index(True)
        bars_since = (len(candles) - 1) - flip_bar

        return ExitSignal(
            symbol=position.symbol,
            name=position.name or position.symbol,
            exit_type="reverse_signal",
            price=cur,
            pnl_pct=pnl_pct,
            reason=(f"[슈퍼트렌드] SELL 시그널 발생 ({bars_since}봉 전 전환) — "
                    f"ST {st_line:.0f} 하향 이탈 → 포지션 정리"),
            market_type=position.market_type,
            timestamp=datetime.now(),
        )

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """Supertrend 라인을 트레일링 스탑으로 근사한 청산.

        하락 추세전환 = 추세선(상승 up밴드) 이탈 → SL. ExitPlan 모델에 라인-트레일링이
        없어, 현재 추세선까지의 하락 여유를 fixed_pct SL 로 환산(clamp)한다.
        """
        p = self.params
        avg = float(position.avg_price)
        res = compute_supertrend(
            ctx.candles, period=p.atr_period, multiplier=p.multiplier, source=p.source,
        )
        st_line = res.supertrend[-1] if res.supertrend else avg * (1 + p.sl_max_pct)
        sl_pct = (st_line - avg) / avg if avg > 0 else p.sl_max_pct
        # 음수 + clamp: [sl_max, sl_min] (예: -8% ~ -1%)
        sl_pct = max(min(sl_pct, p.sl_min_pct), p.sl_max_pct)
        time_exit = p.time_exit if ctx.market_type == MarketType.STOCK else None
        return ExitPlan(
            take_profits=[],
            stop_loss=StopLoss(fixed_pct=Decimal(str(round(sl_pct, 4)))),
            time_exit=time_exit,
        )

    def health_check(self) -> dict[str, Any]:
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": True,
            "timeframe": "5m",
            "backtestable": True,
            "atr_period": self.params.atr_period,
            "multiplier": self.params.multiplier,
            "source": self.params.source,
        }


__all__ = [
    "SupertrendStrategy",
    "SupertrendParams",
    "SupertrendResult",
    "compute_supertrend",
]
