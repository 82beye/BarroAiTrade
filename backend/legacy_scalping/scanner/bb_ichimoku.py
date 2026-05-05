"""
역매공파 지표 계산 모듈
- 볼린저밴드 (Bollinger Band)
- 일목균형표 (Ichimoku Kinko Hyo) 선행스팬
- 골든크로스 판정
"""

import numpy as np
import pandas as pd


def calc_bollinger_band(
    close: pd.Series,
    period: int,
    std_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    볼린저밴드 계산

    Args:
        close: 종가 시리즈
        period: 이동평균 기간
        std_mult: 표준편차 배수

    Returns:
        (upper, middle, lower) 시리즈 튜플
    """
    middle = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std * std_mult
    lower = middle - std * std_mult
    return upper, middle, lower


def calc_ichimoku_spans(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    tenkan: int = 9,
    kijun: int = 26,
    senkou_b_period: int = 52,
) -> tuple[pd.Series, pd.Series]:
    """
    일목균형표 선행스팬 계산

    선행스팬1 = (전환선 + 기준선) / 2  → 26봉 앞으로 이동
    선행스팬2 = (52봉 최고 + 52봉 최저) / 2  → 26봉 앞으로 이동

    현재 시점의 구름대 값을 구하려면 26봉 전에 계산된 값을 사용.
    즉, shift(kijun)으로 미래로 보낸 것을 shift(-kijun)으로 되돌리면 원래 위치.
    결론: shift 없이 계산한 값이 현재 위치의 구름대.

    실제로는: 26봉 전에 계산된 선행스팬이 현재 위치의 구름대가 되므로,
    shift(-kijun)을 적용하여 현재 위치에서의 구름대 값을 반환.

    Args:
        high, low, close: OHLCV 시리즈
        tenkan: 전환선 기간 (기본 9)
        kijun: 기준선 기간 (기본 26)
        senkou_b_period: 선행스팬2 기간 (기본 52)

    Returns:
        (senkou_span1, senkou_span2) — 현재 위치의 구름대 값
    """
    # 전환선 = (N봉 최고 + N봉 최저) / 2
    tenkan_line = (
        high.rolling(window=tenkan, min_periods=tenkan).max()
        + low.rolling(window=tenkan, min_periods=tenkan).min()
    ) / 2

    # 기준선 = (N봉 최고 + N봉 최저) / 2
    kijun_line = (
        high.rolling(window=kijun, min_periods=kijun).max()
        + low.rolling(window=kijun, min_periods=kijun).min()
    ) / 2

    # 선행스팬1 = (전환선 + 기준선) / 2 → 26봉 shift
    senkou_span1 = ((tenkan_line + kijun_line) / 2).shift(kijun)

    # 선행스팬2 = (52봉 최고 + 52봉 최저) / 2 → 26봉 shift
    senkou_span2 = (
        (
            high.rolling(window=senkou_b_period, min_periods=senkou_b_period).max()
            + low.rolling(window=senkou_b_period, min_periods=senkou_b_period).min()
        )
        / 2
    ).shift(kijun)

    return senkou_span1, senkou_span2


def check_golden_cross(
    fast: pd.Series,
    slow: pd.Series,
    lookback: int = 4,
) -> bool:
    """
    골든크로스 판정 (lookback 범위 내)

    조건: fast[i] > slow[i] AND fast[i-1] <= slow[i-1]  (i가 lookback 범위 내)

    Args:
        fast: 단기 이동평균 시리즈
        slow: 장기 이동평균 시리즈
        lookback: 최근 N봉 이내 크로스 발생 여부

    Returns:
        True if 골든크로스 발생
    """
    if len(fast) < 2 or len(slow) < 2:
        return False

    # 최근 lookback 범위 검사
    end = len(fast)
    start = max(1, end - lookback)

    for i in range(start, end):
        f_cur = fast.iloc[i]
        f_prev = fast.iloc[i - 1]
        s_cur = slow.iloc[i]
        s_prev = slow.iloc[i - 1]

        if pd.isna(f_cur) or pd.isna(f_prev) or pd.isna(s_cur) or pd.isna(s_prev):
            continue

        if f_cur > s_cur and f_prev <= s_prev:
            return True

    return False
