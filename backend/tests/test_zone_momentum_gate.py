"""존3 과확장 게이트 — BARRO_ZONE_MAX_MOM20_PCT (default-OFF)."""
import pytest

from backend.core.strategy.zone_entry_gates import (
    ZoneGateConfig, config_from_env, evaluate_momentum_gate, evaluate_zone_gates,
)


def _candles(seq):
    return [{"close": float(c)} for c in seq]


def _run(mom_pct: float, cap: float):
    """20일 전 대비 mom_pct% 오른 21봉 시퀀스."""
    base = 10000.0
    seq = [base] * 20 + [base * (1 + mom_pct / 100)]
    return evaluate_momentum_gate(_candles(seq), ZoneGateConfig(max_mom20_pct=cap))


def test_off_by_default(monkeypatch):
    for k in ("BARRO_ZONE_MAX_MOM20_PCT",):
        monkeypatch.delenv(k, raising=False)
    cfg = config_from_env()
    assert cfg.max_mom20_pct == 0.0
    blocked, _ = evaluate_momentum_gate(_candles([10000] * 20 + [30000]), cfg)
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
    for n in (0, 1, 20):
        blocked, _ = evaluate_momentum_gate(_candles([10000] * n), cfg)
        assert blocked is False, f"{n}봉에서 차단했다"


def test_bad_close_is_fail_open():
    cfg = ZoneGateConfig(max_mom20_pct=30.0)
    bad = [{"close": None}] * 21
    assert evaluate_momentum_gate(bad, cfg)[0] is False
    zero = [{"close": 0.0}] * 20 + [{"close": 10000.0}]
    assert evaluate_momentum_gate(zero, cfg)[0] is False


def test_non_zone_strategy_unaffected():
    cfg = ZoneGateConfig(max_mom20_pct=30.0)
    c = _candles([10000] * 20 + [20000])
    assert evaluate_zone_gates("ai_swing", "005930", c, [], cfg)[0] is False
    assert evaluate_zone_gates("gold_zone", "005930", c, [], cfg)[0] is True


def test_shadow_reports_without_blocking():
    cfg = ZoneGateConfig(max_mom20_pct=30.0, shadow=True)
    c = _candles([10000] * 20 + [20000])
    blocked, reason = evaluate_zone_gates("gold_zone", "005930", c, [], cfg)
    assert blocked is False and "overext" in reason
