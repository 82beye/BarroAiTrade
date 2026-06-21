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
