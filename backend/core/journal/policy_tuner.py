"""BAR-OPS-30 — diff 결과 기반 정책 자동 튜닝 추천.

OPS-29 diff 결과 (양호 / 과대 시뮬 / 과소 시뮬 / 신호 없음 비율) → 다음 운영의
min_score / SL / max_per_position 조정 추천.

자동 적용 X — 사용자가 검토 후 simulate_leaders --min-score 등 옵션으로 반영.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyRecommendation:
    """정책 조정 추천."""
    field: str               # min_score / stop_loss / max_per_position
    current: float
    recommended: float
    reason: str
    severity: str            # info / warn / critical


def _ratio(counts: dict[str, int], key: str) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    return counts.get(key, 0) / total


def recommend_min_score(
    bias_counts: dict[str, int],
    current: float,
    *, signal_threshold: float = 0.5, step: float = 0.1, cap_high: float = 0.9,
) -> PolicyRecommendation | None:
    """과대 시뮬 비율 높을 시 min_score 상향. 양호 비율 충분 시 동결/소폭 하향.

    over/good_ratio 는 **신호 종목 기준** (신호 없음 제외).
    """
    signal_total = bias_counts.get("양호", 0) + bias_counts.get("과대 시뮬", 0) + bias_counts.get("과소 시뮬", 0)
    if signal_total == 0:
        return None
    over_ratio = bias_counts.get("과대 시뮬", 0) / signal_total
    good_ratio = bias_counts.get("양호", 0) / signal_total

    if over_ratio >= signal_threshold:
        new = min(round(current + step, 2), cap_high)
        if new > current:
            return PolicyRecommendation(
                field="min_score",
                current=current, recommended=new,
                reason=f"과대 시뮬 비율 {over_ratio*100:.0f}% — 시뮬 점수 임계 상향",
                severity="warn",
            )
    elif good_ratio >= 0.80 and current >= 0.30:
        new = round(current - step, 2)
        if new < current:
            return PolicyRecommendation(
                field="min_score",
                current=current, recommended=max(new, 0.0),
                reason=f"양호 비율 {good_ratio*100:.0f}% — 진입 후보 확대 가능",
                severity="info",
            )
    return None


def recommend_stop_loss(
    bias_counts: dict[str, int],
    current: float,
    *, threshold: float = 0.30, step: float = 0.5,
) -> PolicyRecommendation | None:
    """과소 시뮬 비율 높을 시 SL 보수화 (절대값 작게). 신호 종목 기준."""
    signal_total = (
        bias_counts.get("양호", 0)
        + bias_counts.get("과대 시뮬", 0)
        + bias_counts.get("과소 시뮬", 0)
    )
    if signal_total == 0:
        return None
    under_ratio = bias_counts.get("과소 시뮬", 0) / signal_total

    if under_ratio >= threshold:
        new = round(current + step, 2)         # -2.0 → -1.5
        if new < 0 and new > current:
            return PolicyRecommendation(
                field="stop_loss",
                current=current, recommended=new,
                reason=f"과소 시뮬 비율 {under_ratio*100:.0f}% — 손절 한도 보수화",
                severity="critical",
            )
    return None


def recommend_max_per_position(
    bias_counts: dict[str, int],
    current: float,
    *, good_threshold: float = 0.80, step: float = 0.05, cap_high: float = 0.50,
) -> PolicyRecommendation | None:
    """양호 비율 ≥80% + 신호 충분 → 자금 한도 확대 가능.

    good_ratio 는 신호 종목 중 비율 (신호 없음 제외).
    """
    signal_total = (
        bias_counts.get("양호", 0)
        + bias_counts.get("과대 시뮬", 0)
        + bias_counts.get("과소 시뮬", 0)
    )
    if signal_total < 5:
        return None
    good_ratio = bias_counts.get("양호", 0) / signal_total

    if good_ratio >= good_threshold:
        new = round(min(current + step, cap_high), 2)
        if new > current:
            return PolicyRecommendation(
                field="max_per_position",
                current=current, recommended=new,
                reason=f"양호 비율 {good_ratio*100:.0f}% (n={signal_total}) — 종목당 한도 확대 가능",
                severity="info",
            )
    return None


def tune_all(
    bias_counts: dict[str, int],
    *,
    current_min_score: float = 0.5,
    current_stop_loss: float = -2.0,
    current_max_per_position: float = 0.30,
) -> list[PolicyRecommendation]:
    """3가지 정책 조정 추천 일괄."""
    recs: list[PolicyRecommendation] = []
    for fn, cur in [
        (recommend_min_score, current_min_score),
        (recommend_stop_loss, current_stop_loss),
        (recommend_max_per_position, current_max_per_position),
    ]:
        r = fn(bias_counts, cur)
        if r:
            recs.append(r)
    return recs


__all__ = [
    "PolicyRecommendation",
    "recommend_min_score", "recommend_stop_loss", "recommend_max_per_position",
    "tune_all",
]
