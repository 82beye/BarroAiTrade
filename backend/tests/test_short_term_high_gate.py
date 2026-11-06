"""단기 고점 매도 발동선 분리 — BARRO_AI_SWING_STH_MIN_PCT."""
import importlib
from decimal import Decimal

import pytest

import backend.core.risk.holding_evaluator as he


def _reload(monkeypatch, val=None):
    if val is None:
        monkeypatch.delenv("BARRO_AI_SWING_STH_MIN_PCT", raising=False)
    else:
        monkeypatch.setenv("BARRO_AI_SWING_STH_MIN_PCT", val)
    return importlib.reload(he)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(he)


def test_default_is_none_keeps_partial_tp_gate(monkeypatch):
    """미설정이면 None → 기존처럼 partial_tp_pct(+20%)가 발동선 (거동 불변)."""
    m = _reload(monkeypatch)
    assert m.STRATEGY_EXIT_PROFILES["ai_swing"]["short_term_high_min_pct"] is None
    pol = m.resolve_policy(m.ExitPolicy(), "ai_swing")
    assert pol.short_term_high_min_pct is None
    assert pol.partial_tp_pct == Decimal("20.00")


def test_env_sets_gate(monkeypatch):
    m = _reload(monkeypatch, "3.0")
    pol = m.resolve_policy(m.ExitPolicy(), "ai_swing")
    assert pol.short_term_high_min_pct == Decimal("3.0")
    # partial_tp 는 그대로 — 부분익절 비중/시점은 건드리지 않는다
    assert pol.partial_tp_pct == Decimal("20.00")


@pytest.mark.parametrize("raw", ["", "  ", "bogus", "nan"])
def test_malformed_env_falls_back_to_none(monkeypatch, raw):
    m = _reload(monkeypatch, raw)
    assert m.STRATEGY_EXIT_PROFILES["ai_swing"]["short_term_high_min_pct"] is None


def test_zone_profiles_unaffected(monkeypatch):
    """존3는 이 env 의 영향을 받지 않는다(각자 partial_tp_pct 로 평가)."""
    m = _reload(monkeypatch, "3.0")
    for z in ("f_zone", "sf_zone", "gold_zone"):
        assert m.STRATEGY_EXIT_PROFILES[z].get("short_term_high_min_pct") is None
