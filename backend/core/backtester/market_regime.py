"""시장 국면 자동 분류 + 국면별 권장 전략 가중치.

주도주들의 최근 N봉 캔들 분포(평균 수익률·양봉 비율)로 시장 국면을 추정하고,
국면별로 검증된 전략 가중치를 매핑한다. PortfolioSimulator 의
strategy_weights 와 그대로 호환.

분류 기준 (lookback 30봉 기본):
- BULL    : 평균 수익률 ≥ +5% AND 양봉 비율 ≥ 55%
- BEARISH : 평균 수익률 ≤ -5% OR  양봉 비율 ≤ 40%
- SIDEWAYS: 그 외 (변동성 박스권 포함)

가중치 출처:
- SIDEWAYS: 2026-05-16 5/16 박스권 종목 검증 — swing_38=0.3·f_zone=0.7 가
  최종 +2.20%, MDD·Sharpe 동시 개선 (`scripts/simulate_portfolio.py` 비교).
- BULL/BEARISH: Backtest-Validation-Report (BAR-29) 의 시장 국면별 전략 선택
  가이드 — 강세장 임펄스 활용, 하락장 F존·과매도 매수 강점.
"""
from __future__ import annotations

from enum import Enum
from typing import Mapping

from backend.models.market import OHLCV


class MarketRegime(str, Enum):
    """시장 국면."""

    BULL = "bull"          # 강세 추세 — 임펄스·눌림목 활용
    SIDEWAYS = "sideways"  # 횡보/변동성 박스권 — 되돌림 매수 강점
    BEARISH = "bearish"    # 하락 — 과매도 매수 + 보수


# 국면별 전략 가중치 매핑 (미지정 전략은 1.0)
REGIME_WEIGHTS: Mapping[MarketRegime, Mapping[str, float]] = {
    MarketRegime.BULL: {
        "swing_38": 1.5,   # 깊은 되돌림 스윙 — 임펄스 빈번한 강세에서 유리
        "f_zone": 1.2,     # 눌림목 반등 — 강세 종목 조정에서 활용
        "sf_zone": 1.2,    # f_zone 강화판
        "gold_zone": 0.5,  # 과매도 매수 — 강세장엔 신호 드뭄, 비중 축소
    },
    MarketRegime.SIDEWAYS: {
        "swing_38": 0.3,   # 깊은 되돌림 패턴 박스권에서 실패 다발 (5/16 검증)
        "f_zone": 0.7,     # 임펄스 약한 박스권에서 신호 빈약
        "sf_zone": 0.8,
        "gold_zone": 1.0,  # 박스권 되돌림/과매도 강점 — 기본 유지
    },
    MarketRegime.BEARISH: {
        "swing_38": 0.5,   # 하락 추세에서 임펄스 후 회복 어려움
        "f_zone": 1.0,     # 하락장 PF 2.76 (BAR-29) — 기본 유지
        "sf_zone": 1.0,
        "gold_zone": 1.5,  # 과매도 매수 — 하락장 반등 활용
    },
}


def classify_regime(
    candles_by_symbol: Mapping[str, list[OHLCV]],
    lookback: int = 30,
) -> MarketRegime:
    """주도주 종목들의 최근 lookback 봉 평균 수익률·양봉 비율로 국면 분류.

    - 종목별: (마지막 close - lookback 전 close) / lookback 전 close + 양봉 비율
    - 집계: 평균
    - 분류:
      BULL    if avg_return ≥ +5% and avg_bull_pct ≥ 55%
      BEARISH if avg_return ≤ -5% or  avg_bull_pct ≤ 40%
      SIDEWAYS else
    """
    if not candles_by_symbol:
        return MarketRegime.SIDEWAYS

    returns: list[float] = []
    bull_pcts: list[float] = []
    for clist in candles_by_symbol.values():
        if len(clist) < lookback + 1:
            continue
        recent = clist[-lookback:]
        start_close = clist[-lookback - 1].close
        end_close = recent[-1].close
        if start_close > 0:
            returns.append((end_close - start_close) / start_close)
        bull = sum(1 for c in recent if c.close > c.open)
        bull_pcts.append(bull / len(recent))

    if not returns:
        return MarketRegime.SIDEWAYS

    avg_return = sum(returns) / len(returns)
    avg_bull = sum(bull_pcts) / len(bull_pcts)

    if avg_return >= 0.05 and avg_bull >= 0.55:
        return MarketRegime.BULL
    if avg_return <= -0.05 or avg_bull <= 0.40:
        return MarketRegime.BEARISH
    return MarketRegime.SIDEWAYS


def regime_weights(regime: MarketRegime) -> dict[str, float]:
    """국면에 대응하는 가중치 dict 반환 (PortfolioSimulator strategy_weights 호환)."""
    return dict(REGIME_WEIGHTS[regime])


# 국면별 f_zone ATR 청산 토글 — F존 ATR 실험(2026-05-16)에서 강세 종목 +10.63M
# 추가 이익, 박스권/변동성 -1.64M 손해 확인. BULL 만 활성화.
# 참조: docs/04-report/features/F-zone-atr-exit-experiment.md
REGIME_F_ZONE_ATR: Mapping[MarketRegime, bool] = {
    MarketRegime.BULL: True,
    MarketRegime.SIDEWAYS: False,
    MarketRegime.BEARISH: False,
}


def regime_f_zone_atr(regime: MarketRegime) -> bool:
    """국면에 대응하는 f_zone_atr_exit 권장값 (BULL 만 True)."""
    return REGIME_F_ZONE_ATR[regime]


__all__ = [
    "MarketRegime",
    "REGIME_WEIGHTS",
    "REGIME_F_ZONE_ATR",
    "classify_regime",
    "regime_weights",
    "regime_f_zone_atr",
]
