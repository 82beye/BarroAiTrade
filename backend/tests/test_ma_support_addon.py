"""MA 지지 반등 추가매수 판정 — default-OFF 및 게이트 검증."""
import pytest

from backend.core.strategy.ma_support_addon import (
    MaSupportConfig, evaluate_ma_support,
)


def _closes(n=70, base=10000.0):
    return [base] * n


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BARRO_MA_SUPPORT_ADDON_ENABLED", raising=False)
    assert MaSupportConfig.from_env().enabled is False
    r = evaluate_ma_support(_closes(), low=9000, close=10100)
    assert r.signal is False and "비활성" in r.reason


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("BARRO_MA_SUPPORT_ADDON_ENABLED", "1")
    return MaSupportConfig.from_env()


def test_support_bounce_detected(on):
    c = _closes()
    r = evaluate_ma_support(c, low=9900, close=10050, cfg=on)   # 저가<MA20<=종가
    assert r.signal is True and "지지 반등" in r.reason


def test_no_signal_when_low_above_ma(on):
    r = evaluate_ma_support(_closes(), low=10050, close=10100, cfg=on)
    assert r.signal is False


def test_no_signal_below_ma60(on):
    c = [10000.0] * 60 + [8000.0] * 10          # 최근 급락 → 종가 < MA60
    r = evaluate_ma_support(c, low=7000, close=8000, cfg=on)
    assert r.signal is False


def test_momentum_cap_blocks_post_surge(on):
    """실측: 20일 모멘텀 50%+ 구간 기대값 -2.66% → 상한으로 배제."""
    c = [10000.0] * 50 + [float(10000 + i * 300) for i in range(20)]   # 급등
    r = evaluate_ma_support(c, low=min(c[-1] * 0.9, 1), close=c[-1], cfg=on)
    assert r.signal is False
    assert "모멘텀" in r.reason or "지지확인 실패" in r.reason


def test_insufficient_data_is_safe(on):
    r = evaluate_ma_support([10000.0] * 10, low=9000, close=10000, cfg=on)
    assert r.signal is False and "부족" in r.reason


def test_bad_input_absorbed(on):
    r = evaluate_ma_support(None, low=1, close=1, cfg=on)  # type: ignore[arg-type]
    assert r.signal is False
