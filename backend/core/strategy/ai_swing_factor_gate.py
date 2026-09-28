"""ai_swing 팩터 진입 게이트 — 추천 유니버스 실측에서 도출한 2축 필터.

## 근거 (2026-09-28 실측, 추천 표본 1,450건 · 추천일 38일 · 선행 10거래일)

추천 리스트(스캔 50 + 예측 50)를 추천일 시가에 사서 10거래일 뒤 파는 매매의
기준선은 net **−0.36%** (승률 46% · PF 1.12)로 손실이다. 여기에 두 조건을 걸면
전·후반 부호가 모두 유지되면서 이익으로 전환된다:

| 조건 | n | net평균 | 중앙 | 승률 | PF | 전반 | 후반 |
|---|---|---|---|---|---|---|---|
| 기준선(필터 없음) | 1450 | −0.36% | −0.96% | 46% | 1.12 | −1.87 | +1.30 |
| 20일고가 −5% 이내 | 554 | +0.72% | −0.33% | 48% | 1.56 | +0.13 | +1.52 |
| 거래대금 ≥ 30억 단독 | 467 | +0.34% | −1.50% | 46% | 1.24 | −1.92 | +2.61 |
| **둘 다 (본 게이트)** | **110** | **+4.27%** | **+2.96%** | **60%** | **2.91** | **+3.59** | **+5.33** |

거래대금 단독은 전·후반 부호가 뒤집혀 불안정하고(−1.92 → +2.61), 고가 근접 단독은
효과가 작다. **결합했을 때만** 크고 안정적이다. 근접 임계를 조이면 단조 개선된다
(−8%: 불안정 / −5%: +4.27 / −3%: +5.12 / −1.5%: 표본 30건으로 판정 불가).
최저하락 평균도 −9.24% → −6.99% 로 개선된다.

## 쓰지 않은 축과 그 이유
- **ATR 상한**: 전체 유니버스 연구에서는 가장 강한 요인(IC −0.135)이지만 추천
  유니버스에서는 **부호가 뒤집힌다**(ATR≤5%: net −1.82%). 추천 종목은 이미 급등해
  고변동성으로 쏠려 있어, 저변동성 부분집합이 오히려 나쁘다. 적용하지 않는다.
- **단기 급등률(ROC5)·RSI·정배열·캔들 형태**: 전체 유니버스 연구에서 t값 2 미만
  (예측력 없음). 게이트 축으로 쓰지 않는다.

## 한계 (반드시 함께 읽을 것)
- 표본 110건 · 통과일 25/38일. 약 40개 규칙을 탐색해 고른 최선값이라 **선택 편향**이
  있다. 전·후반 부호 일치는 이를 완화하지만 제거하지 못한다.
- **슬롯 1개로는 이 엣지를 수확할 수 없다.** 통과 종목 중 1개만 고르는 규칙을 12가지
  시험했으나 전부 후반 소수 대박에 좌우돼(예: ROC5 최대 전반 −1.64 / 후반 +23.16)
  n=25일에서는 결정 불가였다. 견고한 부분은 **바스켓 평균**이다. 따라서 이 모듈은
  **후보를 걸러내기만 하고 선택 순서는 바꾸지 않는다**.
- 일봉 기준 · 모의 시세 · 2026-08~09 한 국면.

기본값은 전부 OFF다. 활성화는 사용자 판단이다(§2 S3·S4).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Sequence

ENV_ENABLED = "BARRO_AI_SWING_FACTOR_GATE_ENABLED"
ENV_NEAR20 = "BARRO_AI_SWING_FG_NEAR20_PCT"
ENV_TURNOVER = "BARRO_AI_SWING_FG_MIN_TURNOVER"
ENV_SHADOW = "BARRO_AI_SWING_FG_SHADOW"

# 실측 최적값 — 활성화 시 기본으로 쓰인다.
DEFAULT_NEAR20_PCT = -5.0        # 20일 최고가 대비 −5% 이내
DEFAULT_MIN_TURNOVER = 3.0e9     # 20일 평균 거래대금 30억원
MIN_CANDLES = 20                 # 20일 고가·거래대금 산출에 필요한 최소 봉수


def _truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _num(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class FactorGateConfig:
    """게이트 설정. enabled=False(기본)면 모든 후보를 통과시킨다."""

    enabled: bool = False
    near20_pct: float = DEFAULT_NEAR20_PCT
    min_turnover: float = DEFAULT_MIN_TURNOVER
    shadow: bool = False

    @classmethod
    def from_env(cls) -> "FactorGateConfig":
        return cls(
            enabled=_truthy(ENV_ENABLED, "0"),
            near20_pct=_num(ENV_NEAR20, DEFAULT_NEAR20_PCT),
            min_turnover=_num(ENV_TURNOVER, DEFAULT_MIN_TURNOVER),
            shadow=_truthy(ENV_SHADOW, "0"),
        )


def compute_factors(candles: Sequence[Any]) -> tuple[float, float] | None:
    """(20일고가 근접% , 20일 평균 거래대금) 산출. 자료 부족·이상치면 None.

    근접%는 `종가/20일최고가 − 1`을 %로 돌려준다 — 0이면 신고가, 음수면 그만큼 아래다.
    """
    if candles is None or len(candles) < MIN_CANDLES:
        return None
    w = list(candles)[-MIN_CANDLES:]
    try:
        highs = [float(c.high) for c in w]
        closes = [float(c.close) for c in w]
        vols = [float(c.volume) for c in w]
    except (AttributeError, TypeError, ValueError):
        return None
    hh = max(highs)
    last = closes[-1]
    if hh <= 0 or last <= 0:
        return None
    near20 = (last / hh - 1.0) * 100.0
    turnover = sum(c * v for c, v in zip(closes, vols)) / len(w)
    return near20, turnover


def evaluate_factor_gate(
    candles: Sequence[Any],
    cfg: FactorGateConfig | None = None,
) -> tuple[bool, str]:
    """(통과여부, 사유). 비활성·자료부족은 fail-open 으로 통과시킨다.

    shadow=True 면 항상 통과시키되 사유에 would-block 내용을 담아
    호출자가 측정 로그만 남기게 한다.
    """
    cfg = cfg or FactorGateConfig.from_env()
    if not cfg.enabled:
        return True, ""
    f = compute_factors(candles)
    if f is None:
        # 자료가 없어 판정 못 하는 것을 차단 사유로 쓰지 않는다(§8 fail-open).
        return True, "factor:자료부족 — 판정 불가로 통과"
    near20, turnover = f
    fails = []
    if near20 < cfg.near20_pct:
        fails.append(f"20일고가 {near20:+.1f}% < {cfg.near20_pct:+.1f}%")
    if turnover < cfg.min_turnover:
        fails.append(f"거래대금 {turnover/1e8:.0f}억 < {cfg.min_turnover/1e8:.0f}억")
    if not fails:
        return True, ""
    reason = "factor:" + " + ".join(fails)
    if cfg.shadow:
        return True, f"SHADOW {reason}"
    return False, reason
