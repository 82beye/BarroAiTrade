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


def compute_adx(candles, period: int = 14) -> List[float]:
    """Wilder ADX(14) 시계열 — 추세 강도 측정 (방향 무관, 0~100).

    횡보(추세 약함)와 추세 발생을 구분하는 핵심 지표. Pine `ta.adx` 와 동일 정의:
      +DM = up_move if (up_move>down_move and up_move>0) else 0   (up_move = high-high[1])
      -DM = down_move if (down_move>up_move and down_move>0) else 0 (down_move = low[1]-low)
      +DI = 100 * RMA(+DM)/RMA(TR), -DI = 100 * RMA(-DM)/RMA(TR)
      DX  = 100 * |+DI - -DI| / (+DI + -DI)
      ADX = RMA(DX)
    통상 ADX ≥ 20~25 면 추세 형성, < 20 이면 횡보. 초기 구간/데이터 부족은 0.0.

    Returns:
        candles 와 동일 길이 list[float] (ADX 값, 산출 불가 구간 0.0).
    """
    n = len(candles)
    if n < 2:
        return [0.0] * n

    tr = _true_ranges(candles)
    plus_dm: List[float] = [0.0] * n
    minus_dm: List[float] = [0.0] * n
    for i in range(1, n):
        up_move = float(candles[i].high) - float(candles[i - 1].high)
        down_move = float(candles[i - 1].low) - float(candles[i].low)
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0.0

    atr_s = _rma(tr, period)
    plus_s = _rma(plus_dm, period)
    minus_s = _rma(minus_dm, period)

    dx: List[float] = [float("nan")] * n
    for i in range(n):
        a = atr_s[i]
        if a != a or a <= 0:   # nan 또는 0
            continue
        pdi = 100.0 * (plus_s[i] / a) if plus_s[i] == plus_s[i] else 0.0
        mdi = 100.0 * (minus_s[i] / a) if minus_s[i] == minus_s[i] else 0.0
        denom = pdi + mdi
        dx[i] = 100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0

    # ADX = RMA(DX) — nan 구간은 평활에서 제외하기 위해 0 으로 두되, seed 안정화 위해
    # DX 가 유효해진 이후부터 RMA 적용 (앞쪽 nan → 0.0 반환).
    dx_clean = [v if v == v else 0.0 for v in dx]
    adx_raw = _rma(dx_clean, period)
    return [v if v == v else 0.0 for v in adx_raw]


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

    # ─── 횡보 휩쏘(whipsaw) 필터 — 변동성 발생 시점의 시그널만 캐치 ───────────
    # 슈퍼트렌드는 횡보 박스권에서 BUY/SELL 이 반복돼 비용만 소모(차트 27~28일 구간).
    # 추세 강도(ADX)와 전환 봉의 밴드 이탈 폭으로 "추세가 살아난 전환"만 통과시킨다.
    #
    # (1) ADX 게이트 — min_adx > 0 이면 ADX(adx_period) < min_adx 인 봉은 진입 거부.
    #     횡보(추세 약함) 구간 차단. 통상 20~25 권장. 0 이면 비활성(기존 회귀 보존).
    min_adx: float = 0.0
    adx_period: int = 14
    # (2) 전환 강도(밴드 이탈 폭) 게이트 — BUY 전환 봉의 종가가 추세선(밴드)을
    #     ATR 의 min_flip_atr_mult 배 이상 돌파했을 때만 진입. 박스권 미세 전환 차단.
    #     예: 0.5 → 종가가 밴드를 +0.5·ATR 이상 넘어선 강한 전환만. 0 이면 비활성.
    min_flip_atr_mult: float = 0.0

    # ── 멀티 타임프레임 RSI 확인 필터 (BAR-OPS-10, 2026-06-03) ────────────────
    # 네이버 차트 관찰: 상위 타임프레임(예 10분) RSI 골든크로스 ≈ 5분봉 슈퍼트렌드 BUY,
    # 데드크로스 ≈ SELL. 상위 TF RSI 를 5분봉 진입의 '확인(regime)' 필터로 써 휩쏘를
    # 줄인다. rsi_enabled=False 면 전부 no-op(기존 회귀 보존).
    #   - 진입: BUY 전환 + 최근 lookback HTF봉 내 RSI 골든크로스(signal_cross/centerline)
    #           또는 RSI≥min_level(level). 미확정이면 진입 거부.
    #   - 청산: rsi_exit_enabled=True 면 RSI 데드크로스/레짐붕괴를 추가 OR 청산 트리거로.
    # 기본 후보값 = 백테스트(2026-06-03 RSI_TF_SWEEP) 데이터-최선: 10m · centerline · p14.
    #   sweep 결과 수익률 1위는 NO_RSI 베이스라인이라 활성은 OFF(아래) — RSI 는 거래수↓·
    #   위험조정수익↑(Sharpe·MDD 개선)를 원할 때만 opt-in 하는 품질 필터. 모드는 사용자가 본
    #   골든/데드크로스(signal_cross)보다 RSI 50 기준선 돌파(centerline)가 안정적이었음.
    rsi_enabled: bool = False
    rsi_timeframe_mult: int = 2        # 5분봉 기준 배수 (1=5m, 2=10m, 3=15m, 6=30m)
    rsi_period: int = 14
    rsi_signal_period: int = 9         # 시그널선 SMA 기간 (signal_cross 모드)
    rsi_mode: str = "centerline"       # signal_cross | centerline | level (sweep: centerline 최선)
    rsi_cross_lookback: int = 2        # 최근 N HTF봉 내 크로스 이벤트 (level 모드 무시)
    rsi_min_level: float = 50.0        # level 모드 진입 하한 / 데드(레짐붕괴) 기준
    rsi_max_level: float = 100.0       # level 모드 과매수 상한(이 위면 진입 안 함)
    rsi_exit_enabled: bool = False     # RSI 데드크로스/레짐붕괴를 추가 OR 청산 트리거로

    # 진입 시간 게이트 (운영 override) — last candle.time() >= cutoff 면 차단. None 비활성.
    entry_time_cutoff: Optional[dtime] = None

    # 청산 SL clamp (Supertrend 라인 트레일링 근사) — 음수 비율.
    sl_min_pct: float = -0.01     # 최소 손절 폭
    sl_max_pct: float = -0.08     # 최대 손절 폭
    # 장마감 강제청산 비활성(2026-05-31): SELL 시그널 발생 시에만 매도.
    #   None = 장 종료 시 자동매도 안 함. 운영에서 강제청산 원하면 dtime 으로 override.
    time_exit: Optional[dtime] = None


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

        # ── 횡보 휩쏘 필터 (1): ADX 추세강도 게이트 ──────────────────────────
        # 횡보(추세 약함) 구간의 BUY 전환은 거짓 신호일 확률이 높음 → ADX 로 차단.
        if p.min_adx > 0:
            adx_series = compute_adx(candles, period=p.adx_period)
            adx_now = adx_series[-1] if adx_series else 0.0
            if adx_now < p.min_adx:
                logger.debug("%s: ADX %.1f < %.1f (횡보) — supertrend 진입 거부",
                             ctx.symbol, adx_now, p.min_adx)
                return None

        # ── 횡보 휩쏘 필터 (2): 전환 강도(밴드 이탈 폭) 게이트 ────────────────
        # BUY 전환 봉이 "방금 돌파한 저항"(전환 직전 dn밴드)을 ATR×mult 이상 넘은
        # "강한 전환"만 통과. 박스권에서 저항을 살짝 넘는 미세 전환(휩쏘)을 차단.
        #
        # 2026-06-01 정정: 종전엔 `close − supertrend(=현재 up밴드)`로 측정했으나,
        # BUY 전환 직후 up밴드는 방금 src−mult·ATR 로 새로 생겨 이탈폭이 거의 항상
        # ~mult·ATR(=3·ATR) 가 되어 0.5·ATR 문턱은 99%+ 무조건 통과 → 게이트 무력화.
        # BUY 전환 조건 자체가 `close > dn₁`(직전 dn밴드=저항 상향 돌파)이므로,
        # 전환 봉 종가가 그 저항(dn₁)을 얼마나 큰 폭으로 넘었는지로 강도를 본다.
        if p.min_flip_atr_mult > 0:
            if True in res.buy_signals:
                # 가장 최근 BUY 전환 봉 (entry_lookback 게이트가 최근 봉 내 존재 보장)
                flip_bar = len(res.buy_signals) - 1 - res.buy_signals[::-1].index(True)
                atr_ref = res.atr[flip_bar]
                # 방금 돌파한 저항 = 전환 직전 봉의 dn밴드. (buy_signal 은 i≥1 보장)
                resist = res.dn[flip_bar - 1] if flip_bar >= 1 else res.supertrend[flip_bar]
                breakout = float(candles[flip_bar].close) - resist
            else:
                # 전환 봉이 없는 추세추종 모드(entry_lookback=None, 시작부터 상승추세)
                # → 현재 종가의 추세선 위 여유로 폴백 평가.
                atr_ref = res.atr[-1]
                breakout = float(candles[-1].close) - res.supertrend[-1]
            if atr_ref <= 0 or breakout < p.min_flip_atr_mult * atr_ref:
                logger.debug("%s: 전환 이탈폭 %.1f < %.2f·ATR(%.1f) — supertrend 진입 거부",
                             ctx.symbol, breakout, p.min_flip_atr_mult, atr_ref)
                return None

        # ── 휩쏘 필터 (3): 멀티 타임프레임 RSI 확인 게이트 (BAR-OPS-10) ────────
        # 상위 타임프레임 RSI 골든크로스(또는 레짐)로 5분봉 BUY 전환을 '확인'. 미확정 거부.
        if p.rsi_enabled:
            from backend.core.strategy.indicators import htf_rsi_confirms_long
            if not htf_rsi_confirms_long(
                candles, i=len(candles) - 1, tf_mult=p.rsi_timeframe_mult,
                period=p.rsi_period, signal_period=p.rsi_signal_period,
                mode=p.rsi_mode, lookback=p.rsi_cross_lookback,
                min_level=p.rsi_min_level, max_level=p.rsi_max_level,
            ):
                logger.debug("%s: HTF(%d×5m) RSI 미확정 — supertrend 진입 거부",
                             ctx.symbol, p.rsi_timeframe_mult)
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

        BAR-OPS-10: rsi_exit_enabled=True 면 슈퍼트렌드 SELL **또는** 상위 TF RSI
        데드크로스/레짐붕괴(OR) 로 조기청산. RSI-only 청산은 exit_type="rsi_break".
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
        # 슈퍼트렌드 SELL 전환 (최근 N봉 내) — 기존 트리거.
        lb = max(1, p.exit_lookback)
        st_exit = bool(res.sell_signals) and any(res.sell_signals[-lb:])

        # RSI 데드크로스/레짐붕괴 (OR 조기청산) — rsi_exit_enabled 일 때만.
        rsi_exit = False
        if p.rsi_exit_enabled:
            from backend.core.strategy.indicators import htf_rsi_confirms_exit
            rsi_exit = htf_rsi_confirms_exit(
                candles, i=len(candles) - 1, tf_mult=p.rsi_timeframe_mult,
                period=p.rsi_period, signal_period=p.rsi_signal_period,
                mode=p.rsi_mode, lookback=p.rsi_cross_lookback,
                min_level=p.rsi_min_level, max_level=p.rsi_max_level,
            )

        if not (st_exit or rsi_exit):
            return None

        entry = float(position.avg_price)
        cur = float(current_price)
        pnl_pct = (cur - entry) / entry * 100 if entry > 0 else 0.0
        st_line = res.supertrend[-1] if res.supertrend else cur

        if st_exit:
            # 전환 신선도 (몇 봉 전 sell 시그널인지) — 진단용
            flip_bar = len(res.sell_signals) - 1 - res.sell_signals[::-1].index(True)
            bars_since = (len(candles) - 1) - flip_bar
            exit_type = "reverse_signal"
            reason = (f"[슈퍼트렌드] SELL 시그널 발생 ({bars_since}봉 전 전환) — "
                      f"ST {st_line:.0f} 하향 이탈 → 포지션 정리")
        else:
            # ExitSignal.exit_type 은 Literal — RSI 조기청산도 reverse_signal(모멘텀 반전)로
            # 분류하고, 원인은 reason 문구로 구분(rsi_break).
            exit_type = "reverse_signal"
            reason = (f"[슈퍼트렌드][rsi_break] HTF({p.rsi_timeframe_mult}×5m) RSI 데드크로스 "
                      f"조기청산 — ST {st_line:.0f} | RSI 약세 전환")

        return ExitSignal(
            symbol=position.symbol,
            name=position.name or position.symbol,
            exit_type=exit_type,
            price=cur,
            pnl_pct=pnl_pct,
            reason=reason,
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
    "compute_adx",
]
