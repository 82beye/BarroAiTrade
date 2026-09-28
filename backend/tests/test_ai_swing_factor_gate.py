"""ai_swing 팩터 게이트 — 기본 OFF · fail-open · 임계 판정 · SHADOW."""
import pytest

from backend.core.strategy.ai_swing_factor_gate import (
    ENV_ENABLED,
    ENV_NEAR20,
    ENV_SHADOW,
    ENV_TURNOVER,
    FactorGateConfig,
    compute_factors,
    evaluate_factor_gate,
)


class Bar:
    def __init__(self, high, close, volume):
        self.high, self.close, self.volume = high, close, volume


def bars(n=20, high=100.0, close=100.0, volume=1_000_000.0):
    return [Bar(high, close, volume) for _ in range(n)]


# ── 기본 OFF (라이브 무영향) ──────────────────────────────────────────
def test_disabled_by_default_passes_everything(monkeypatch):
    for k in (ENV_ENABLED, ENV_NEAR20, ENV_TURNOVER, ENV_SHADOW):
        monkeypatch.delenv(k, raising=False)
    cfg = FactorGateConfig.from_env()
    assert cfg.enabled is False
    # 두 조건 모두 크게 미달하는 후보도 통과해야 한다
    ok, why = evaluate_factor_gate(bars(high=200.0, close=100.0, volume=1.0), cfg)
    assert ok is True and why == ""


def test_env_activation_and_overrides(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_NEAR20, "-3.5")
    monkeypatch.setenv(ENV_TURNOVER, "5e9")
    monkeypatch.setenv(ENV_SHADOW, "yes")
    cfg = FactorGateConfig.from_env()
    assert cfg.enabled is True and cfg.shadow is True
    assert cfg.near20_pct == pytest.approx(-3.5)
    assert cfg.min_turnover == pytest.approx(5e9)


def test_malformed_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(ENV_ENABLED, "1")
    monkeypatch.setenv(ENV_NEAR20, "아니오")
    cfg = FactorGateConfig.from_env()
    assert cfg.near20_pct == pytest.approx(-5.0)


# ── fail-open ────────────────────────────────────────────────────────
@pytest.mark.parametrize("candles", [None, [], bars(n=19)])
def test_insufficient_candles_fail_open(candles):
    cfg = FactorGateConfig(enabled=True)
    ok, why = evaluate_factor_gate(candles, cfg)
    assert ok is True, "자료 부족을 차단 사유로 쓰면 안 된다"
    if candles:
        assert "자료부족" in why


def test_broken_candle_objects_fail_open():
    cfg = FactorGateConfig(enabled=True)
    ok, _ = evaluate_factor_gate([object()] * 20, cfg)
    assert ok is True


def test_zero_price_fail_open():
    cfg = FactorGateConfig(enabled=True)
    ok, _ = evaluate_factor_gate(bars(high=0.0, close=0.0), cfg)
    assert ok is True


# ── 팩터 계산 ─────────────────────────────────────────────────────────
def test_compute_factors_values():
    b = bars(high=100.0, close=100.0, volume=1_000_000.0)
    b[-1] = Bar(high=100.0, close=96.0, volume=1_000_000.0)
    near, turn = compute_factors(b)
    assert near == pytest.approx(-4.0)          # 96/100-1
    # 거래대금 = 종가×거래량 평균 (마지막 봉만 96원)
    assert turn == pytest.approx((19 * 100e6 + 96e6) / 20)


def test_compute_factors_at_new_high_is_zero():
    b = bars(high=100.0, close=100.0)
    near, _ = compute_factors(b)
    assert near == pytest.approx(0.0)


# ── 임계 판정 ─────────────────────────────────────────────────────────
def test_passes_when_both_conditions_met():
    # 근접 −2% · 거래대금 98원×5,000만주 = 49억 (> 30억)
    b = bars(high=100.0, close=98.0, volume=50_000_000.0)
    ok, why = evaluate_factor_gate(b, FactorGateConfig(enabled=True))
    assert ok is True and why == ""


def test_blocks_on_far_from_high():
    b = bars(high=100.0, close=90.0, volume=50_000_000.0)  # 근접 −10% · 거래대금은 충족
    ok, why = evaluate_factor_gate(b, FactorGateConfig(enabled=True))
    assert ok is False and "20일고가" in why


def test_blocks_on_low_turnover():
    b = bars(high=100.0, close=99.0, volume=1_000.0)       # 거래대금 약 10만원
    ok, why = evaluate_factor_gate(b, FactorGateConfig(enabled=True))
    assert ok is False and "거래대금" in why


def test_reason_lists_both_failures():
    b = bars(high=100.0, close=80.0, volume=1_000.0)
    ok, why = evaluate_factor_gate(b, FactorGateConfig(enabled=True))
    assert ok is False
    assert "20일고가" in why and "거래대금" in why


# ── SHADOW ───────────────────────────────────────────────────────────
def test_shadow_never_blocks_but_reports():
    b = bars(high=100.0, close=80.0, volume=1_000.0)
    ok, why = evaluate_factor_gate(b, FactorGateConfig(enabled=True, shadow=True))
    assert ok is True, "SHADOW 는 측정 전용이라 차단하지 않는다"
    assert why.startswith("SHADOW ")


def test_shadow_silent_when_candidate_passes():
    b = bars(high=100.0, close=98.0, volume=50_000_000.0)
    ok, why = evaluate_factor_gate(b, FactorGateConfig(enabled=True, shadow=True))
    assert ok is True and why == ""
