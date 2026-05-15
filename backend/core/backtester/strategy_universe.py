"""전략별 종목 후보군(universe) 계산 — 진입 종목 사전 필터.

각 전략의 강점에 맞는 종목 풀을 분리해, picker 결과 종목에 5전략을 일괄
적용하는 대신 전략별 후보군에만 analyze() 호출. 시간축 충돌·전략 무관 신호
발화를 줄이고 각 전략의 강점 발휘를 돕는다.

### 전략별 후보군 규칙 (lookback 30봉 기본)

- **swing_38** — 강세 추세: `ret_30d ≥ +5%` AND `bull_pct ≥ 55%`.
  깊은 되돌림(Fib 0.382) 후 임펄스 회복이 의미 있는 강세 종목.
- **gold_zone** — 박스권/약세: `-5% ≤ ret_30d ≤ +5%`. BB하단 도달·RSI 과매도가
  발화하는 변동 박스권. 강세장에선 신호 자체가 안 남 (BAR-46/47 deep analysis §5).
- **f_zone / sf_zone** — 강세 후 단기 조정: `ret_30d ≥ +5%` AND `고점대비 -5%~-2%`.
  눌림목 매수 패턴이 발화 가능한 상태.
- **scalping_consensus** — 변동성: `atr_pct(14) ≥ 3%`. ScalpingCoordinator 가 의미
  있게 평가할 수 있는 단기 변동성.

설계 근거:
- 5/16 15일 시뮬에서 swing_38 +420k (강세) vs gold_zone 0건 (picker 종목이 강세라
  BB하단 미발화) 확인 — 전략별 강점 종목군이 다름.
- BAR-29 Backtest-Validation-Report 시장 국면별 전략 선택 가이드와 일치.
"""
from __future__ import annotations

from typing import Mapping

from backend.models.market import OHLCV


STRATEGY_IDS = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


def _atr_pct(candles: list[OHLCV], n: int = 14) -> float:
    """True Range 평균 / 마지막 close — 14봉 ATR%."""
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
    return atr / last_close if last_close > 0 else 0.0


def compute_universe(
    candles_by_symbol: Mapping[str, list[OHLCV]],
    lookback: int = 30,
) -> dict[str, set[str]]:
    """종목별 특성으로 전략별 후보군 분류.

    반환: `{strategy_id: set(symbol, ...)}`. 미충족 전략은 빈 set.
    캔들 부족 종목은 어느 universe 에도 포함 안 됨 (안전).
    """
    universe: dict[str, set[str]] = {sid: set() for sid in STRATEGY_IDS}
    for sym, clist in candles_by_symbol.items():
        if len(clist) < lookback + 1:
            continue
        recent = clist[-lookback:]
        start_close = clist[-lookback - 1].close
        end_close = recent[-1].close
        if start_close <= 0:
            continue
        ret = (end_close - start_close) / start_close
        bull_pct = sum(1 for c in recent if c.close > c.open) / lookback
        max_high = max(c.high for c in recent)
        drawdown = (end_close - max_high) / max_high if max_high > 0 else 0.0
        atr_pct = _atr_pct(clist[-15:]) if len(clist) >= 15 else 0.0

        # 강세 추세 — swing_38
        if ret >= 0.05 and bull_pct >= 0.55:
            universe["swing_38"].add(sym)
        # 박스권/약세 — gold_zone
        if -0.05 <= ret <= 0.05:
            universe["gold_zone"].add(sym)
        # 강세 후 단기 조정 — f_zone / sf_zone
        if ret >= 0.05 and -0.05 <= drawdown <= -0.02:
            universe["f_zone"].add(sym)
            universe["sf_zone"].add(sym)
        # 변동성 — scalping_consensus
        if atr_pct >= 0.03:
            universe["scalping_consensus"].add(sym)
    return universe


__all__ = ["STRATEGY_IDS", "compute_universe"]
