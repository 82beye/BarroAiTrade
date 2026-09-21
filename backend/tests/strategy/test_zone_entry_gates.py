"""존 진입 게이트 — default-OFF 보증 + 판정 정확성."""
from types import SimpleNamespace

import pytest

from backend.core.strategy.zone_entry_gates import (
    ZoneGateConfig, config_from_env, evaluate_reentry_gate, evaluate_trend_gate,
    evaluate_zone_gates, is_zone_strategy, ZONE_STRATEGIES,
)


def _candles(closes):
    return [{"close": c} for c in closes]


def _obj_candles(closes):
    return [SimpleNamespace(close=c) for c in closes]


# ── default-OFF 보증 ────────────────────────────────────────────────
def test_default_config_is_fully_off():
    cfg = ZoneGateConfig()
    assert cfg.any_enabled() is False
    assert cfg.trend_enabled is False and cfg.reentry_enabled is False


def test_empty_env_yields_off():
    assert config_from_env({}).any_enabled() is False


def test_off_config_never_blocks():
    cfg = ZoneGateConfig()
    # 역배열 + 재진입 — 켜져 있었다면 둘 다 차단될 입력
    blocked, reason = evaluate_zone_gates(
        "f_zone_v1", "005930", _candles([100] * 19 + [50]), {"005930"}, cfg)
    assert blocked is False and reason == ""


# ── 환경변수 파싱 ───────────────────────────────────────────────────
def test_reentry_lookback_default_is_bounded():
    """무제한이면 존 전략이 건드린 종목이 영구 차단돼 유니버스가 고갈된다."""
    cfg = ZoneGateConfig()
    assert cfg.reentry_lookback_days == 60
    assert cfg.reentry_lookback_days > 0


def test_env_parses_reentry_lookback():
    cfg = config_from_env({"BARRO_ZONE_REENTRY_LOOKBACK_DAYS": "30"})
    assert cfg.reentry_lookback_days == 30


def test_env_reentry_lookback_zero_means_unbounded():
    cfg = config_from_env({"BARRO_ZONE_REENTRY_LOOKBACK_DAYS": "0"})
    assert cfg.reentry_lookback_days == 0


def test_env_reentry_lookback_rejects_negative():
    cfg = config_from_env({"BARRO_ZONE_REENTRY_LOOKBACK_DAYS": "-5"})
    assert cfg.reentry_lookback_days == 0


def test_env_reentry_lookback_falls_back_on_garbage():
    cfg = config_from_env({"BARRO_ZONE_REENTRY_LOOKBACK_DAYS": "abc"})
    assert cfg.reentry_lookback_days == 60


def test_env_parses_flags_and_margin():
    cfg = config_from_env({
        "BARRO_ZONE_TREND_GATE_ENABLED": "1",
        "BARRO_ZONE_TREND_GATE_MARGIN": "2.5",
        "BARRO_ZONE_REENTRY_GUARD_ENABLED": "true",
    })
    assert cfg.trend_enabled and cfg.reentry_enabled
    assert cfg.trend_margin_pct == 2.5
    assert cfg.any_enabled()


def test_env_margin_ignores_inline_comment():
    """`.env.local` 인라인 주석이 값에 섞여도 죽지 않는다 (2026-09-22 실사례)."""
    cfg = config_from_env({
        "BARRO_ZONE_TREND_GATE_ENABLED": "1",
        "BARRO_ZONE_TREND_GATE_MARGIN": "2.0       # 정배열 최소 이격",
    })
    assert cfg.trend_margin_pct == 2.0


def test_env_margin_falls_back_on_garbage():
    cfg = config_from_env({"BARRO_ZONE_TREND_GATE_MARGIN": "not-a-number"})
    assert cfg.trend_margin_pct == 0.0


# ── 추세(정배열) 게이트 ─────────────────────────────────────────────
def test_trend_gate_passes_on_golden_alignment():
    cfg = ZoneGateConfig(trend_enabled=True)
    # 우상향 → MA5 > MA20
    blocked, _ = evaluate_trend_gate(_candles(list(range(100, 130))), cfg)
    assert blocked is False


def test_trend_gate_blocks_on_dead_cross():
    cfg = ZoneGateConfig(trend_enabled=True)
    # 우하향 → MA5 < MA20
    blocked, reason = evaluate_trend_gate(_candles(list(range(130, 100, -1))), cfg)
    assert blocked is True and "MA5/MA20" in reason


def test_trend_gate_margin_tightens():
    cfg_zero = ZoneGateConfig(trend_enabled=True, trend_margin_pct=0.0)
    cfg_tight = ZoneGateConfig(trend_enabled=True, trend_margin_pct=50.0)
    candles = _candles(list(range(100, 130)))
    assert evaluate_trend_gate(candles, cfg_zero)[0] is False
    assert evaluate_trend_gate(candles, cfg_tight)[0] is True


def test_trend_gate_fails_open_on_short_history():
    cfg = ZoneGateConfig(trend_enabled=True)
    blocked, reason = evaluate_trend_gate(_candles([100] * 10), cfg)
    assert blocked is False and "fail-open" in reason


def test_trend_gate_accepts_object_candles():
    cfg = ZoneGateConfig(trend_enabled=True)
    assert evaluate_trend_gate(_obj_candles(list(range(100, 130))), cfg)[0] is False
    assert evaluate_trend_gate(_obj_candles(list(range(130, 100, -1))), cfg)[0] is True


def test_trend_gate_off_returns_pass():
    assert evaluate_trend_gate(_candles(list(range(130, 100, -1))), ZoneGateConfig()) == (False, "")


# ── 재진입 게이트 ───────────────────────────────────────────────────
def test_reentry_gate_blocks_known_symbol():
    cfg = ZoneGateConfig(reentry_enabled=True)
    blocked, reason = evaluate_reentry_gate("005930", {"005930", "000660"}, cfg)
    assert blocked is True and "005930" in reason


def test_reentry_gate_allows_first_entry():
    cfg = ZoneGateConfig(reentry_enabled=True)
    assert evaluate_reentry_gate("035420", {"005930"}, cfg) == (False, "")


def test_reentry_gate_off_returns_pass():
    assert evaluate_reentry_gate("005930", {"005930"}, ZoneGateConfig()) == (False, "")


# ── 전략 범위 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("sid", ["f_zone", "f_zone_v1", "sf_zone_v1", "gold_zone_v1"])
def test_is_zone_strategy_true(sid):
    assert is_zone_strategy(sid) is True


@pytest.mark.parametrize("sid", ["ai_swing_v1", "supertrend_v1", "swing_38_v1", "", "closing_bet_v1"])
def test_is_zone_strategy_false(sid):
    assert is_zone_strategy(sid) is False


def test_non_zone_strategy_is_never_blocked():
    cfg = ZoneGateConfig(trend_enabled=True, reentry_enabled=True)
    blocked, reason = evaluate_zone_gates(
        "supertrend_v1", "005930", _candles(list(range(130, 100, -1))), {"005930"}, cfg)
    assert blocked is False and reason == ""


def test_zone_strategy_set_is_exactly_three():
    assert ZONE_STRATEGIES == {"f_zone", "sf_zone", "gold_zone"}


# ── 종합 판정 · shadow ──────────────────────────────────────────────
def test_combined_blocks_and_reports_both_reasons():
    cfg = ZoneGateConfig(trend_enabled=True, reentry_enabled=True)
    blocked, reason = evaluate_zone_gates(
        "gold_zone_v1", "005930", _candles(list(range(130, 100, -1))), {"005930"}, cfg)
    assert blocked is True
    assert "trend:" in reason and "reentry:" in reason


def test_combined_passes_when_both_clear():
    cfg = ZoneGateConfig(trend_enabled=True, reentry_enabled=True)
    blocked, reason = evaluate_zone_gates(
        "gold_zone_v1", "035420", _candles(list(range(100, 130))), {"005930"}, cfg)
    assert blocked is False and reason == ""


def test_shadow_mode_reports_without_blocking():
    cfg = ZoneGateConfig(trend_enabled=True, reentry_enabled=True, shadow=True)
    blocked, reason = evaluate_zone_gates(
        "f_zone_v1", "005930", _candles(list(range(130, 100, -1))), {"005930"}, cfg)
    assert blocked is False          # 측정 전용 — 진입을 막지 않는다
    assert "trend:" in reason        # 사유는 남긴다
