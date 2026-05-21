"""공통 지표 계산 helper.

BAR-OPS-09 Phase 7: 4 strategy (f_zone, blue_line, gold_zone, swing_38) 와
IntradaySimulator 에 복제됐던 `_atr_pct` 본문을 단일 함수로 통합.

- 4 strategy 의 `_atr_pct` staticmethod 는 호환 wrapper 로 유지 (float 반환).
- IntradaySimulator 의 module-level `_atr_pct` 도 wrapper 로 유지 (Decimal 반환).
- 신규 strategy 는 직접 이 모듈을 import 해서 사용.
"""
from __future__ import annotations

from typing import List

from backend.models.market import OHLCV


def atr_pct(candles: List[OHLCV], n: int = 14) -> float:
    """최근 n봉의 True Range 평균 / 마지막 close 비율 (예: 0.025 = 2.5%).

    종목별 변동성 측정. 분봉/일봉 무관 동일 공식.
    저변동·고가주 가짜 시그널 차단(변동성 필터) 의 핵심 지표.

    Args:
        candles: OHLCV 리스트 (오래된 → 최신 순).
        n: True Range 평균 봉 수 (기본 14).

    Returns:
        ATR% 값 (0.0 ~ 1.0 범위 일반적). 데이터 부족 또는 last_close <= 0 시 0.0.
    """
    if len(candles) < 2:
        return 0.0
    n = min(n, len(candles) - 1)
    trs: list[float] = []
    for i in range(1, n + 1):
        c = candles[-i]
        prev = candles[-i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close),
        )
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else 0.0
    last_close = candles[-1].close
    if last_close <= 0:
        return 0.0
    return atr / last_close


__all__ = ["atr_pct"]
