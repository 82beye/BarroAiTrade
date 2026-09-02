"""ai_swing 라이브 진입 게이트 env 정렬 (2026-09-02).

배경 — 시뮬↔라이브 진입 조건 괴리:
  `IntradaySimulator` 의 ai_swing 분기는 `min_atr_pct=0.035` + `entry_time_cutoff=14:00`
  으로 백테스트를 돌리는데, 라이브는 `AiSwingStrategy()` 를 무인자로 만들어
  Swing38Params 기본값(0.03 / 컷오프 없음)을 썼다. 즉 백테스트가 보고한 수익률은
  **라이브보다 엄격한 진입**에서 나온 값이고, 라이브는 시뮬이 모델링하지 않은 느슨한
  진입까지 집어갔다.

`live_params()` 는 env 로 이 간극을 메우되 **기본값은 현재 라이브값 그대로**다 —
env 미설정이면 동작이 바이트 동일해야 한다(§2 S3). 이 파일이 그 계약을 고정한다.
"""
from __future__ import annotations

from datetime import time as dtime

from backend.core.strategy.ai_swing import (
    AiSwingParams,
    AiSwingStrategy,
    live_params,
)


# ── 기본값 = 현재 라이브값 (env 미설정 시 바이트 동일) ──────────────────────

def test_no_env_returns_base_untouched():
    """env 가 하나도 없으면 base 와 값이 같아야 한다 — 기존 동작 보존."""
    base = AiSwingParams()
    p = live_params(base, env={})
    assert p.min_atr_pct == base.min_atr_pct
    assert p.min_score == base.min_score
    assert p.entry_time_cutoff == base.entry_time_cutoff


def test_strategy_without_params_uses_live_params(monkeypatch):
    """무인자 생성(=라이브 경로)은 env 를 반영한다."""
    monkeypatch.setenv("BARRO_AI_SWING_MIN_SCORE", "7")
    assert AiSwingStrategy().params.min_score == 7.0


def test_strategy_with_explicit_params_ignores_env(monkeypatch):
    """★ 시뮬 보호 — params 를 명시로 넣으면 env 를 타지 않는다.

    그리드 스윕이 env 에 오염되면 결과가 통째로 무의미해진다.
    """
    monkeypatch.setenv("BARRO_AI_SWING_MIN_SCORE", "9")
    explicit = AiSwingParams(min_score=3.0, min_atr_pct=0.035)
    s = AiSwingStrategy(explicit)
    assert s.params.min_score == 3.0
    assert s.params.min_atr_pct == 0.035


# ── 시뮬과 같은 값으로 올리기 ───────────────────────────────────────────────

def test_env_aligns_entry_gates_to_simulator():
    """시뮬 분기와 동일한 값(ATR 3.5% · 컷오프 14:00)으로 정렬된다."""
    p = live_params(env={
        "BARRO_AI_SWING_MIN_ATR_PCT": "3.5",
        "BARRO_AI_SWING_ENTRY_CUTOFF": "14:00",
    })
    assert p.min_atr_pct == 0.035
    assert p.entry_time_cutoff == dtime(14, 0)


def test_min_score_from_env():
    assert live_params(env={"BARRO_AI_SWING_MIN_SCORE": "7"}).min_score == 7.0
    assert live_params(env={"BARRO_AI_SWING_MIN_SCORE": "0"}).min_score == 0.0


# ── 잘못된 입력은 전부 흡수 (라이브가 env 오타로 죽지 않는다, §2 S3) ────────

def test_invalid_values_fall_back_to_default():
    base = AiSwingParams()
    for raw in ("", "  ", "abc", "-1", "0", "100", "3.5%"):
        p = live_params(base, env={"BARRO_AI_SWING_MIN_ATR_PCT": raw})
        assert p.min_atr_pct == base.min_atr_pct, raw


def test_invalid_min_score_falls_back():
    base = AiSwingParams()
    for raw in ("abc", "-1", "11", "9999"):
        assert live_params(base, env={"BARRO_AI_SWING_MIN_SCORE": raw}).min_score == base.min_score, raw


def test_invalid_cutoff_falls_back():
    base = AiSwingParams(entry_time_cutoff=dtime(13, 30))
    for raw in ("nope", "25:00", "14-00", "14:zz"):
        assert live_params(base, env={"BARRO_AI_SWING_ENTRY_CUTOFF": raw}).entry_time_cutoff == dtime(13, 30), raw


def test_empty_cutoff_means_explicit_disable():
    """빈 문자열은 '기본값 유지'가 아니라 **명시적 비활성**이다 — 롤백 레버."""
    base = AiSwingParams(entry_time_cutoff=dtime(14, 0))
    assert live_params(base, env={"BARRO_AI_SWING_ENTRY_CUTOFF": ""}).entry_time_cutoff is None


# ── 청산 파라미터는 건드리지 않는다 ─────────────────────────────────────────

def test_exit_params_unchanged_by_live_params():
    """진입 게이트 전용 — TP/SL/트레일링은 그대로 둔다(별도 env·그리드 소관)."""
    base = AiSwingParams()
    p = live_params(base, env={"BARRO_AI_SWING_MIN_SCORE": "7"})
    assert (p.sl_pct, p.tp1_pct, p.tp2_pct) == (base.sl_pct, base.tp1_pct, base.tp2_pct)
    assert (p.trail_start_pct, p.trail_offset_pct) == (base.trail_start_pct, base.trail_offset_pct)
    assert (p.min_hold_days, p.max_hold_days) == (base.min_hold_days, base.max_hold_days)
