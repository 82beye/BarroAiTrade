"""주식단테 방법론 정량 게이트 — 장기선·바닥반전·운영 필터 (JD-R).

분석: docs/03-analysis/2026-06-21-dante-methodology-extract.md
설계: docs/02-design/features/2026-06-21-dante-uplift.design.md §6

⚠️ 순수함수 게이트. distribution_alert 만 `DistributionExitConfig`(config-gated,
   **default-OFF**)를 통해 라이브 청산(holding_evaluator)에 연결된다 — enabled=False
   default 라 라이브 무변경(byte-identical). 나머지 함수는 여전히 관측 전용(inert).
   OOS 검증: docs/04-report/features/2026-06-22-dante-oos-validation.report.md.

캔들 규약(indicators.py·closing_bet_filters.py 계승): List[OHLCV] 오래된→최신,
candles[-1]=최신. 거래대금 필드는 OHLCV에 없으므로 호출측이 close*volume(원)로 산출.

주식단테 시그니처는 더트레이딩(단기 5·20·60 돌파후 눌림 연속형)과 직교한다:
**장기 112/224/448선 기반 바닥·반전 셋업**. 본 모듈은 그 정량화 가능 부분만 담는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from backend.models.market import OHLCV


def _ema(candles: List[OHLCV], period: int) -> Optional[float]:
    """종가 EMA(지수이동평균) 최신값. adjust=False(HTS 동일). 데이터 부족 시 None.

    주식단테: 장기선은 "지수로 고치세요"(KHiuCH2GEDw) → 단순이평 아닌 EMA.
    """
    if period <= 0 or len(candles) < period:
        return None
    k = 2.0 / (period + 1.0)
    ema = candles[0].close
    for c in candles[1:]:
        ema = c.close * k + ema * (1.0 - k)
    return ema


def _sma(candles: List[OHLCV], period: int, *, end: Optional[int] = None) -> Optional[float]:
    """종가 단순이동평균. end=배타적 끝 인덱스(기본 최신까지). 데이터 부족 시 None."""
    n = len(candles) if end is None else end
    if period <= 0 or n < period:
        return None
    return sum(c.close for c in candles[n - period:n]) / period


# ─── 그룹 A: 장기 EMA 골격 (112/224/448) ─────────────────────────────────────
def ma_alignment(
    candles: List[OHLCV], periods: Tuple[int, int, int] = (112, 224, 448)
) -> Optional[str]:
    """JD-R1/R4 — 장기 EMA 배열 레짐.

    주식단테(JQJMyTkl_uo): "112/224/448 세 선의 규칙성…전부 다 지지 저항".
    반환: '정배열'(p1>p2>p3) | '역배열'(p1<p2<p3) | '혼조' | None(데이터부족).
    종목별 추세구조 레짐 — macro `market_regime.py`(지수 국면)와는 다른 차원.
    """
    e1 = _ema(candles, periods[0])
    e2 = _ema(candles, periods[1])
    e3 = _ema(candles, periods[2])
    if e1 is None or e2 is None or e3 is None:
        return None
    if e1 > e2 > e3:
        return "정배열"
    if e1 < e2 < e3:
        return "역배열"
    return "혼조"


def above_ma224(candles: List[OHLCV], period: int = 224) -> Optional[bool]:
    """JD-R1 — 종가가 224일 EMA('세력선') 위인가. True=상승레짐. 데이터부족 None.

    주식단테(KHiuCH2GEDw): 224선 위=지지, 아래=저항.
    """
    e = _ema(candles, period)
    if e is None:
        return None
    return candles[-1].close >= e


# ─── 그룹 B: 바닥축적 가격구조 ────────────────────────────────────────────────
def sr_flip(candles: List[OHLCV], pivot_lookback: int = 20) -> Optional[dict]:
    """JD-R7 — 공구리(Support-Resistance Flip): 하락 파동 중 직전 전고 상향돌파.

    주식단테(3zWm0BoSnMI): "직전 언덕을 뚫어올린 봉의 시가를 던질 라인 잡고 매매".
    직전 swing-high(언덕)를 최신봉 종가가 상향 돌파(직전봉은 미돌파)하면:
      flip_price = 돌파한 전고점(이탈 시 손절선),
      break_open = 돌파봉 시가(진입 라인),
      target     = flip_price + (직전 하락폭)  ← 대칭이론.
    조건 미충족/데이터부족 시 None.

    Args:
        candles: OHLCV(오래된→최신). 최소 pivot_lookback+2 필요.
        pivot_lookback: 직전 전고 탐색 구간(기본 20봉).
    """
    if len(candles) < pivot_lookback + 2:
        return None
    prev, last = candles[-2], candles[-1]
    window = candles[-(pivot_lookback + 1):-1]          # 최신봉 제외 직전 구간
    swing_high = max(c.high for c in window)
    # 돌파 확정: 직전봉 종가는 전고 이하, 최신봉 종가가 전고 상향 돌파
    if not (prev.close <= swing_high < last.close):
        return None
    # 전고 형성 후 저점(직전 하락 저점) → 대칭 하락폭
    hi_idx = max(range(len(window)), key=lambda i: window[i].high)
    after_peak = window[hi_idx + 1:]
    if not after_peak:
        return None
    intervening_low = min(c.low for c in after_peak)
    down_move = swing_high - intervening_low
    if down_move <= 0:
        return None
    return {
        "flip_price": swing_high,
        "break_open": last.open,
        "target": swing_high + down_move,
    }


def saucer_third_zone(
    candles: List[OHLCV], base_min_days: int = 80, breakout_mult: float = 2.0
) -> bool:
    """JD-R5 — 밥그릇 3번 자리(원형바닥): 224 아래 장기 횡보 후 강한 상향 돌파.

    주식단테(fshUGuAWrho/IZ_qmX_sKII): "224 밑 4개월+ 횡보 → 다시 224 뚫어 올리는
    3번 자리…이격보다 2배 이상 뚫어야". 근사 정량화:
      ① 직전 base_min_days 구간의 ≥90%가 EMA224 아래(역배열 바닥),
      ② 최신봉이 EMA224 상향 돌파(직전봉 미돌파),
      ③ 돌파 도달가 ≥ base_low + below_dist × breakout_mult (이격 대비 돌파 강도).
    데이터 부족/미충족 False(보수적).
    """
    need = 224 + base_min_days
    if len(candles) < need:
        return False
    ema = _ema(candles, 224)
    if ema is None or ema <= 0:
        return False
    base = candles[-(base_min_days + 1):-1]
    below = sum(1 for c in base if c.close < ema)
    if below < 0.9 * len(base):
        return False
    prev, last = candles[-2], candles[-1]
    if not (prev.close <= ema < last.close):
        return False
    base_low = min(c.low for c in base)
    below_dist = ema - base_low
    if below_dist <= 0:
        return False
    return last.close >= base_low + below_dist * breakout_mult


def accumulation_candle(
    candles: List[OHLCV],
    retrace_min: float = 0.7,
    vol_mult: float = 3.0,
    lookback: int = 20,
) -> bool:
    """JD-R6 — 매집봉(흡수 캔들): 대량거래 + 긴 위꼬리 되돌림.

    주식단테(IZ_qmX_sKII): "개미 물량 흡수…25%나 상한가 근처 갔다가 쭉 빠짐".
    근사: 위꼬리 되돌림율 (high-close)/(high-low) ≥ retrace_min
          AND 거래량 ≥ 직전 lookback 평균 × vol_mult.
    데이터부족/레인지0 False.
    """
    if len(candles) < lookback + 1:
        return False
    last = candles[-1]
    rng = last.high - last.low
    if rng <= 0:
        return False
    retrace = (last.high - last.close) / rng
    avg_vol = sum(c.volume for c in candles[-(lookback + 1):-1]) / lookback
    if avg_vol <= 0:
        return False
    return retrace >= retrace_min and last.volume >= avg_vol * vol_mult


# ─── 그룹 F: 청산/회피 ────────────────────────────────────────────────────────
def distribution_alert(
    candles: List[OHLCV], vol_mult: float = 3.0, body_min: float = 0.03
) -> bool:
    """JD-R13 — distribution(세력 이탈) 경보: 전일比 거래량 300% 장대음봉.

    주식단테(-PLoE2xLUPU): "전일比 300%+ 장대음봉 출현=당분간 하락". 청산/회피 신호.
    조건: 음봉 + 몸통 (open-close)/open ≥ body_min + 거래량 ≥ 전일 × vol_mult.
    ※ 호출측은 정배열 확장구간에서만 적용 권장(맥락 게이트는 호출측 책임).
    데이터부족 False.
    """
    if len(candles) < 2:
        return False
    prev, last = candles[-2], candles[-1]
    if last.close >= last.open or last.open <= 0:
        return False
    body = (last.open - last.close) / last.open
    if body < body_min:
        return False
    if prev.volume <= 0:
        return False
    return last.volume >= prev.volume * vol_mult


# ─── 그룹 H: 단기 타점 ────────────────────────────────────────────────────────
def odori_cross(candles: List[OHLCV], short: int = 5, long: int = 15) -> bool:
    """JD-R20 — 오돌리: 5일선이 15일선을 상향 돌파(당봉 골든크로스).

    주식단테(tQ0eW3Qj74g): "오도리가 나오면 매매 가능". blue_line(5/20)의 5/15 변형.
    데이터부족/미발생 False.
    """
    if len(candles) < long + 1:
        return False
    s_now = _sma(candles, short)
    l_now = _sma(candles, long)
    s_prev = _sma(candles, short, end=len(candles) - 1)
    l_prev = _sma(candles, long, end=len(candles) - 1)
    if None in (s_now, l_now, s_prev, l_prev):
        return False
    return s_prev <= l_prev and s_now > l_now


# ─── 그룹 I: 운영 게이트 ──────────────────────────────────────────────────────
def rr_ratio_ok(
    entry: float, stop: float, target: float, min_rr: float = 2.0
) -> Optional[bool]:
    """JD-R21 — 최소 손익비 게이트: (목표폭/손절폭) ≥ min_rr.

    주식단테(Pc5P3RTntF0): "적어도 1대3 정도는 나와야". 진입 전 손절/목표 설정 후
    비율 미달이면 NO-GO. 손절폭 ≤0(잘못된 입력)이면 None.
    """
    risk = entry - stop
    reward = target - entry
    if risk <= 0:
        return None
    return (reward / risk) >= min_rr


@dataclass(frozen=True)
class DistributionExitConfig:
    """JD-R13 — distribution(세력 이탈 장대음봉) 청산 게이트 설정. config-gated, default-OFF.

    `enabled=False`(default) → `fires()` 항상 False → 라이브 청산 무변경(byte-identical).
    `holding_evaluator.evaluate_holding` 이 `PositionContext.distribution_exit` 로 받아
    duck-typing(`.fires()`)으로 호출(strategy↔risk 순환 회피). 데몬이 일봉을 주입.

    OOS 검증(2026-06-22): IS/OOS 모두 baseline 하회·음수, 임계 강화에 회피효과 단조 증가.
    사용자 확정(2026-06-22): 액션=전량 청산, 임계=거래량 3.0배·몸통 3%(표준).
    리포트: docs/04-report/features/2026-06-22-dante-oos-validation.report.md.
    ★활성화(enabled=True)는 약세장 dry-run 후 별도 HITL.

    Args(필드):
        enabled: 게이트 활성(default False).
        vol_mult: 전일 대비 거래량 배율 하한(기본 3.0).
        body_min: 음봉 몸통 비율 하한(기본 0.03=3%).
        require_uptrend: 정배열 확장구간(종가>SMA(ma_period))에서만 발동(OOS gate와 동일).
        ma_period: 추세 확인 이동평균(기본 60).
    """

    enabled: bool = False
    vol_mult: float = 3.0
    body_min: float = 0.03
    require_uptrend: bool = True
    ma_period: int = 60

    def fires(self, candles: Optional[List[OHLCV]]) -> bool:
        """distribution 청산 발동 여부. default(enabled=False) → 항상 False."""
        if not self.enabled or not candles:
            return False
        if self.require_uptrend:
            ma = _sma(candles, self.ma_period)
            if ma is None or candles[-1].close <= ma:
                return False
        return distribution_alert(candles, vol_mult=self.vol_mult, body_min=self.body_min)

    @classmethod
    def from_policy_config(cls, cfg) -> "DistributionExitConfig":
        """PolicyConfig(또는 동등 객체)에서 조립. 필드 부재 시 default(비활성)."""
        return cls(
            enabled=bool(getattr(cfg, "distribution_exit_enabled", False)),
            vol_mult=float(getattr(cfg, "distribution_exit_vol_mult", 3.0)),
            body_min=float(getattr(cfg, "distribution_exit_body_min", 0.03)),
        )


__all__ = [
    "ma_alignment",
    "above_ma224",
    "sr_flip",
    "saucer_third_zone",
    "accumulation_candle",
    "distribution_alert",
    "odori_cross",
    "rr_ratio_ok",
    "DistributionExitConfig",
]
