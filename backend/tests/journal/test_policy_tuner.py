"""BAR-OPS-30 — policy_tuner 테스트."""
from __future__ import annotations

from backend.core.journal.policy_tuner import (
    recommend_max_per_position,
    recommend_min_score,
    recommend_stop_loss,
    tune_all,
)


# -- min_score --------------------------------------------------------------


def test_min_score_raises_when_overshoot_ratio_high():
    """과대 시뮬 ≥50% → min_score +0.1."""
    bias = {"과대 시뮬": 5, "양호": 1, "과소 시뮬": 0, "신호 없음": 0}
    r = recommend_min_score(bias, current=0.5)
    assert r is not None
    assert r.field == "min_score"
    assert r.recommended == 0.6
    assert r.severity == "warn"


def test_min_score_lowers_when_good_ratio_high():
    """양호 ≥80% → min_score -0.1."""
    bias = {"양호": 8, "과대 시뮬": 1, "과소 시뮬": 0, "신호 없음": 1}
    # 신호 = 9, 양호 = 8/9 ≈ 89%
    r = recommend_min_score(bias, current=0.7)
    assert r is not None
    assert r.recommended == 0.6
    assert r.severity == "info"


def test_min_score_no_change_in_balanced():
    bias = {"양호": 3, "과대 시뮬": 2, "과소 시뮬": 1, "신호 없음": 4}
    # 과대 = 2/6 = 33% (< 50%), 양호 = 3/6 = 50% (< 80%)
    assert recommend_min_score(bias, current=0.5) is None


def test_min_score_no_signal_returns_none():
    assert recommend_min_score({"신호 없음": 5}, current=0.5) is None


def test_min_score_caps_at_0_9():
    bias = {"과대 시뮬": 10}
    r = recommend_min_score(bias, current=0.85)
    assert r is not None
    assert r.recommended == 0.9          # cap


def test_min_score_does_not_lower_below_0_3():
    bias = {"양호": 10}
    r = recommend_min_score(bias, current=0.2)
    # current 0.2 < 0.30 → 하향 추천 없음
    assert r is None


# -- stop_loss --------------------------------------------------------------


def test_stop_loss_tightens_when_undershoot_ratio_high():
    """과소 시뮬 ≥30% → SL -2 → -1.5."""
    bias = {"과소 시뮬": 4, "양호": 3, "과대 시뮬": 3, "신호 없음": 0}
    # 과소 = 4/10 = 40%
    r = recommend_stop_loss(bias, current=-2.0)
    assert r is not None
    assert r.field == "stop_loss"
    assert r.recommended == -1.5
    assert r.severity == "critical"


def test_stop_loss_no_change_when_undershoot_low():
    bias = {"과소 시뮬": 1, "양호": 8, "과대 시뮬": 1, "신호 없음": 0}
    # 과소 = 10%
    assert recommend_stop_loss(bias, current=-2.0) is None


def test_stop_loss_no_signal_returns_none():
    assert recommend_stop_loss({"신호 없음": 5}, current=-2.0) is None


# -- max_per_position -------------------------------------------------------


def test_max_per_position_expands_when_good_ratio_high():
    """양호 ≥80% + 신호 ≥5 → 종목당 한도 확대."""
    bias = {"양호": 10, "과대 시뮬": 1, "과소 시뮬": 1, "신호 없음": 3}
    r = recommend_max_per_position(bias, current=0.30)
    assert r is not None
    assert r.field == "max_per_position"
    assert r.recommended == 0.35
    assert r.severity == "info"


def test_max_per_position_caps_at_0_50():
    bias = {"양호": 20, "과대 시뮬": 0, "과소 시뮬": 0}
    r = recommend_max_per_position(bias, current=0.48)
    assert r is not None
    assert r.recommended == 0.50


def test_max_per_position_too_few_signals_returns_none():
    bias = {"양호": 3, "과대 시뮬": 0, "과소 시뮬": 0, "신호 없음": 2}
    # 신호 = 3 < 5 → 표본 부족
    assert recommend_max_per_position(bias, current=0.30) is None


# -- tune_all -------------------------------------------------------------


def test_tune_all_returns_only_actionable():
    bias = {"과대 시뮬": 5, "과소 시뮬": 4, "양호": 1, "신호 없음": 0}
    recs = tune_all(bias)
    fields = {r.field for r in recs}
    assert "min_score" in fields            # 과대 50% → 상향
    assert "stop_loss" in fields            # 과소 40% → 보수화
    # 양호 10% < 80% → max_per_position 추천 없음
    assert "max_per_position" not in fields


def test_tune_all_no_signals_returns_empty():
    assert tune_all({"신호 없음": 5}) == []
