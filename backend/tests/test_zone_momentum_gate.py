"""존3 과확장 게이트 — BARRO_ZONE_MAX_MOM20_PCT (default-OFF)."""
import pytest

from backend.core.strategy.zone_entry_gates import (
    ZoneGateConfig, config_from_env, evaluate_momentum_gate, evaluate_zone_gates,
)


def _candles(seq):
    return [{"close": float(c)} for c in seq]


def _run(mom_pct: float, cap: float):
    """직전 봉이 그 20봉 전 대비 mom_pct% 오른 시퀀스 + 당일 미완성 봉 1개.

    게이트는 당일 봉(마지막)을 쓰지 않고 **직전 봉**을 기준으로 삼는다 —
    측정(진입 전일 종가)과 라이브 입력(당일 봉 포함)을 일치시키기 위함.
    """
    base = 10000.0
    seq = [base] * 20 + [base * (1 + mom_pct / 100)] + [base * 9]  # 마지막=당일(무시돼야 함)
    return evaluate_momentum_gate(_candles(seq), ZoneGateConfig(max_mom20_pct=cap))


def test_off_by_default(monkeypatch):
    for k in ("BARRO_ZONE_MAX_MOM20_PCT",):
        monkeypatch.delenv(k, raising=False)
    cfg = config_from_env()
    assert cfg.max_mom20_pct == 0.0
    blocked, _ = evaluate_momentum_gate(_candles([10000] * 21 + [30000]), cfg)
    assert blocked is False, "OFF 인데 차단했다"


def test_env_enables_and_any_enabled(monkeypatch):
    monkeypatch.setenv("BARRO_ZONE_MAX_MOM20_PCT", "30")
    cfg = config_from_env()
    assert cfg.max_mom20_pct == 30.0 and cfg.any_enabled() is True


@pytest.mark.parametrize("mom,cap,blocked", [
    (50.0, 30.0, True),    # 과확장 → 차단
    (31.0, 30.0, True),
    (30.0, 30.0, False),   # 경계는 통과 (> 비교)
    (10.0, 30.0, False),
    (-5.0, 30.0, False),   # 하락 종목은 통과
])
def test_threshold(mom, cap, blocked):
    got, reason = _run(mom, cap)
    assert got is blocked, f"mom={mom} cap={cap} → {got} ({reason})"
    if blocked:
        assert "overext" in reason


def test_insufficient_data_is_fail_open():
    """21봉 미만은 차단하지 않는다 — 조회 실패가 매매를 멈추면 안 된다."""
    cfg = ZoneGateConfig(max_mom20_pct=30.0)
    for n in (0, 1, 21):
        blocked, _ = evaluate_momentum_gate(_candles([10000] * n), cfg)
        assert blocked is False, f"{n}봉에서 차단했다"


def test_bad_close_is_fail_open():
    cfg = ZoneGateConfig(max_mom20_pct=30.0)
    bad = [{"close": None}] * 22
    assert evaluate_momentum_gate(bad, cfg)[0] is False
    zero = [{"close": 0.0}] * 21 + [{"close": 10000.0}]
    assert evaluate_momentum_gate(zero, cfg)[0] is False


def test_non_zone_strategy_unaffected():
    cfg = ZoneGateConfig(max_mom20_pct=30.0)
    c = _candles([10000] * 20 + [20000, 20000])
    assert evaluate_zone_gates("ai_swing", "005930", c, [], cfg)[0] is False
    assert evaluate_zone_gates("gold_zone", "005930", c, [], cfg)[0] is True


def test_shadow_reports_without_blocking():
    cfg = ZoneGateConfig(max_mom20_pct=30.0, shadow=True)
    c = _candles([10000] * 20 + [20000, 20000])
    blocked, reason = evaluate_zone_gates("gold_zone", "005930", c, [], cfg)
    assert blocked is False and "overext" in reason


# ── 2026-11-06 Codex QA 회귀 ───────────────────────────────────────────────
def _down_candles(n=25):
    """MA5 < MA20 인 하락 시퀀스 — 정배열 게이트가 차단해야 하는 입력."""
    return [{"close": float(12000 - i * 20)} for i in range(n)]


def test_global_shadow_also_releases_trend_gate_documented():
    """전역 shadow 는 종전대로 ①②③ 전부 미차단 — 하위호환이므로 유지한다."""
    cfg = ZoneGateConfig(trend_enabled=True, max_mom20_pct=30.0, shadow=True)
    blocked, reason = evaluate_zone_gates("gold_zone", "X", _down_candles(), [], cfg)
    assert blocked is False and "trend" in reason


def test_mom_shadow_keeps_trend_gate_enforcing():
    """★핵심★ 모멘텀만 측정하려 할 때 정배열 차단은 살아 있어야 한다.

    전역 shadow 로 이를 하면 정배열까지 해제돼 라이브에서 차단이 사라진다.
    """
    cfg = ZoneGateConfig(trend_enabled=True, max_mom20_pct=30.0, mom_shadow=True)
    blocked, reason = evaluate_zone_gates("gold_zone", "X", _down_candles(), [], cfg)
    assert blocked is True, f"정배열 차단이 풀렸다: {reason}"
    assert "trend" in reason


def test_mom_shadow_alone_does_not_block():
    """모멘텀만 걸리고 mom_shadow 면 차단하지 않되 사유는 남긴다."""
    cfg = ZoneGateConfig(trend_enabled=False, max_mom20_pct=30.0, mom_shadow=True)
    c = _candles([10000] * 20 + [20000, 20000])
    blocked, reason = evaluate_zone_gates("gold_zone", "X", c, [], cfg)
    assert blocked is False
    assert "overext" in reason and "[측정]" in reason


def test_today_partial_candle_is_ignored():
    """당일 봉이 급등해도 판정은 직전 봉 기준이다(시뮬↔라이브 일치)."""
    cfg = ZoneGateConfig(max_mom20_pct=30.0)
    # 직전 봉은 +10%(통과), 당일 봉만 +100%
    c = _candles([10000] * 20 + [11000] + [20000])
    assert evaluate_momentum_gate(c, cfg)[0] is False
