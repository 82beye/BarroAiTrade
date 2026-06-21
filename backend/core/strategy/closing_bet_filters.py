"""종가베팅 사전필터 — 더트레이딩 방법론 v2(301편 델타) 정량 게이트.

분석: docs/03-analysis/2026-06-21-thetrading-methodology-extract-v2-301편.md
설계: docs/02-design/features/2026-06-21-thetrading-uplift-301delta.design.md §3.1

⚠️ 관측 전용 순수함수. 호출처 없음(inert) → **라이브 매매 동작 무영향**.
   향후 종베/선정 레이어가 (d) HITL 단계에서 import 해 게이트로 켠다. 그 전까지는
   shadow 측정(로그)·백테스트 비교용으로만 호출한다. 부작용 없음(반환만).

캔들 규약(indicators.py 계승): List[OHLCV] 오래된→최신, candles[-1]=최신.
거래대금 필드는 OHLCV에 없으므로 close*volume(원)로 산출.
"""
from __future__ import annotations

from typing import List, Optional

from backend.models.market import OHLCV


def body_new_high(candles: List[OHLCV], lookback: int = 60) -> bool:
    """R6 — 몸통(종가) 기준 신고가 여부. 꼬리(high) 제외.

    더트레이딩: "신고가는 꼬리까지 보는 게 아니고 몸통만"(2026-04, V41).
    최신 종가가 직전 `lookback`봉의 종가 최고치를 초과하면 True.

    Args:
        candles: OHLCV 리스트(오래된→최신). 최소 lookback+1 필요.
        lookback: 비교 구간(직전 N봉, 기본 60).
    Returns:
        몸통 신고가면 True. 데이터 부족 시 False(보수적).
    """
    if len(candles) < lookback + 1:
        return False
    last_close = candles[-1].close
    prior_max_close = max(c.close for c in candles[-lookback - 1:-1])
    return last_close > prior_max_close


def overheat_warning(candles: List[OHLCV], mult: float = 1.6, lookback: int = 5) -> bool:
    """D-R24 — 단기과열 예고 추정. `종가 > N거래일 전 종가 × mult`.

    더트레이딩: "5일 전 종가보다 60% 이상이면 내일 경고"(2026-02, V38).
    한국거래소 단기과열 예고의 근사(정확한 거래소 공식이 아닌 방법론 발화 기반 추정).
    종베 진입 전 회피 신호(경고/투자주의와 함께 2종 체크의 한 축).

    Args:
        candles: OHLCV(오래된→최신). 최소 lookback+1 필요.
        mult: 배수(기본 1.6 = +60%).
        lookback: 기준 과거 거래일 수(기본 5).
    Returns:
        과열 예고 추정 시 True. 데이터 부족 시 False.
    """
    if len(candles) < lookback + 1:
        return False
    base = candles[-(lookback + 1)].close
    if base <= 0:
        return False
    return candles[-1].close > base * mult


def liquidity_ok(
    min1_value_won: float,
    day_volume: float,
    prev_day_volume: float,
    *,
    min1_floor_won: float = 1.5e9,
    vol_mult: float = 3.0,
) -> bool:
    """D-R29/30 — 유동성·활성도 게이트.

    더트레이딩: "1분봉 거래대금 15억 미만은 안 한다"(2024-09, V17),
    "당일 거래량 전일比 300%+"(2024-10). 두 조건 AND.

    Args:
        min1_value_won: 1분봉 거래대금(원). close*volume.
        day_volume: 당일 누적 거래량.
        prev_day_volume: 전일 거래량.
        min1_floor_won: 1분봉 거래대금 하한(기본 15억).
        vol_mult: 당일/전일 거래량 배율 하한(기본 3.0배).
    Returns:
        둘 다 충족 시 True. 전일 거래량 0/음수면 거래량 조건만 통과로 보지 않고 False.
    """
    if prev_day_volume <= 0:
        return False
    return (min1_value_won >= min1_floor_won) and (day_volume >= prev_day_volume * vol_mult)


def rel_volume_surge(
    candles: List[OHLCV], lookback: int = 20, min_mult: float = 2.0
) -> bool:
    """신정재 — 상대 거래대금 급증. 당일 거래대금 ≥ 직전 lookback봉 평균 × min_mult.

    신정재(종베 종목선정 2): "거래대금은 절대값이 아니라 상대값 — 최근 일 대비 눈에
    띄는!!!". 절대 하한(min_trade_value)과 달리 종목별 평소 대비 급증을 정량화한다.
    `liquidity_ok`(전일比 ×3) 의 N봉 평균 일반화 버전.

    Args:
        candles: 일봉 OHLCV(오래된→최신). 최소 lookback+1 필요.
        lookback: 평균 산출 구간(직전 N봉, 기본 20).
        min_mult: 평균 대비 배율 하한(기본 2.0배).
    Returns:
        당일 거래대금이 평균의 min_mult배 이상이면 True. 데이터 부족/평균 ≤0 시 False.
    """
    if len(candles) < lookback + 1:
        return False
    prior = candles[-lookback - 1:-1]
    avg = sum(c.close * c.volume for c in prior) / len(prior)
    if avg <= 0:
        return False
    today_value = candles[-1].close * candles[-1].volume
    return today_value >= avg * min_mult


def consolidation_ok(
    candles: List[OHLCV],
    min_days: int = 10,
    lookback: int = 60,
    require_higher_lows: bool = False,
) -> bool:
    """신정재 — 충분한 기간 조정 + (옵션) 조정구간 저점 우상향.

    신정재(종베 종목선정 5): "전고점 찍고 시간이 흐를수록 매물대가 얕아진다 — 충분한
    기간 조정 필요". + "조정 구간에서 저점을 높여가는 종목 선호(higher lows)".
    조정 짧은 과열주(일동제약류)를 거른다.

    직전 `lookback`봉(당일 제외) 중 최고가(전고점) 발생 봉 이후 ~ 어제까지를 '조정구간'
    으로 보고, 그 길이 ≥ min_days 이어야 통과. require_higher_lows 면 조정구간 후반부
    저점이 전반부 저점보다 높아야 한다.

    Args:
        candles: 일봉 OHLCV(오래된→최신). 최소 lookback+1 필요.
        min_days: 전고점 이후 최소 조정 경과봉(기본 10).
        lookback: 전고점 탐색 구간(직전 N봉, 기본 60).
        require_higher_lows: 조정구간 저점 우상향 요구 여부.
    Returns:
        조건 충족 시 True. 데이터 부족/조정 부족 시 False(보수적).
    """
    if len(candles) < lookback + 1:
        return False
    window = candles[-lookback - 1:-1]                  # 직전 lookback봉(당일 제외)
    peak_i = max(range(len(window)), key=lambda i: window[i].high)
    seg = window[peak_i + 1:]                           # 전고점 이후 ~ 어제 = 조정구간
    if len(seg) < min_days:
        return False
    if require_higher_lows:
        half = len(seg) // 2
        if half < 1:
            return False
        first_low = min(b.low for b in seg[:half])
        second_low = min(b.low for b in seg[half:])
        if second_low <= first_low:
            return False
    return True


def remaining_upside_ratio(
    current_price: float,
    target_high: float,
    base_low: float,
) -> Optional[float]:
    """D-R14 — 잔존 기대수익 비율 = (목표고점-현재)/(목표고점-기준저점).

    더트레이딩: "먹을 공간이 얼마나 남았나"(2022). 1.0=바닥(최대 잔존),
    0.0=목표 도달(잔존 없음). 호출측이 임계(예: <0.5면 고점추격 → NO-GO)와 비교.

    Args:
        current_price: 현재가.
        target_high: 목표 고점(기준봉 고점 등).
        base_low: 기준 저점.
    Returns:
        잔존 비율(0~1 클립). 분모 ≤0 등 산출 불가 시 None.
    """
    span = target_high - base_low
    if span <= 0:
        return None
    ratio = (target_high - current_price) / span
    if ratio < 0.0:
        return 0.0
    if ratio > 1.0:
        return 1.0
    return ratio


def _sma(candles: List[OHLCV], period: int) -> Optional[float]:
    """최근 `period`봉 종가 단순이동평균. 데이터 부족 시 None."""
    if period <= 0 or len(candles) < period:
        return None
    return sum(c.close for c in candles[-period:]) / period


def envelope_upper_break(
    candles: List[OHLCV], ma_period: int = 20, env_pct: float = 0.20
) -> bool:
    """D-R42 — 엔벨로프 상단(천장) 돌파 = 초강세.

    더트레이딩(V13): "20일선 엔벨로프 ±20% 상단을 뚫고 올라가는 종목 = 크게 될 놈".
    최신 종가가 `SMA(ma_period) × (1 + env_pct)` 를 초과하면 True.

    Args:
        candles: OHLCV(오래된→최신). 최소 ma_period 필요.
        ma_period: 이동평균 기간(기본 20).
        env_pct: 엔벨로프 상단 비율(기본 0.20 = +20%).
    Returns:
        상단 돌파면 True. 데이터 부족/이평 ≤0 시 False(보수적).
    """
    sma = _sma(candles, ma_period)
    if sma is None or sma <= 0:
        return False
    return candles[-1].close > sma * (1.0 + env_pct)


def disparity_5ma(candles: List[OHLCV], ma_period: int = 5) -> Optional[float]:
    """D-R43 — 5일선 이격도 = (종가 - SMA5) / SMA5.

    양수=5일선 위(과열·반등탄력), 음수=아래. 호출측이 임계와 비교.

    Args:
        candles: OHLCV(오래된→최신). 최소 ma_period 필요.
        ma_period: 기준 이동평균(기본 5).
    Returns:
        이격도 비율. 데이터 부족/이평 ≤0 시 None.
    """
    sma = _sma(candles, ma_period)
    if sma is None or sma <= 0:
        return None
    return (candles[-1].close - sma) / sma


def disparity_yellow(candles: List[OHLCV], threshold: float = 0.1425) -> bool:
    """D-R43 — 이격도 "노란불"(5일선 +14.25% 이상) 여부.

    더트레이딩(V13): "종가가 5일선 대비 14% 이상 벌어지면 노란색 표시 → 높은 곳서
    떨어질수록 반등 큼". 이격도가 임계 이상이면 True.
    """
    d = disparity_5ma(candles)
    return d is not None and d >= threshold


def triple_factor_buy(
    candles: List[OHLCV],
    day_value_won: float,
    *,
    value_floor_won: float = 1.0e11,
    ma_period: int = 20,
    env_pct: float = 0.20,
    disparity_threshold: float = 0.1425,
) -> bool:
    """D-R44 — 삼박자 동시충족 매수 신호 (관측 전용).

    더트레이딩(V13, 영상13): **엔벨로프 상단돌파(D-R42) ∧ 이격도 노란불(D-R43)
    ∧ 거래대금 ≥1000억(D-R44)** 세 조건 AND. "삼박자 고루 갖춘 종목만 거래".

    ⚠️ inert — 판정만 반환. 호출처가 shadow 로그/백테스트 비교축으로만 사용.

    Args:
        candles: 일봉 OHLCV(오래된→최신). 최소 ma_period 필요.
        day_value_won: 당일 거래대금(원). 보통 Σ(close×volume) 또는 외부 산출치.
        value_floor_won: 거래대금 하한(기본 1000억=1.0e11).
        ma_period/env_pct: 엔벨로프 파라미터(기본 20일·+20%).
        disparity_threshold: 이격도 임계(기본 0.1425).
    Returns:
        세 조건 모두 충족 시 True. 하나라도 미달/산출불가 시 False(보수적).
    """
    if day_value_won < value_floor_won:
        return False
    if not envelope_upper_break(candles, ma_period=ma_period, env_pct=env_pct):
        return False
    if not disparity_yellow(candles, threshold=disparity_threshold):
        return False
    return True
