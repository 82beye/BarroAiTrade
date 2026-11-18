"""dante_entry — 파란점선 돌파 판정 테스트.

핵심은 2개다:
  1) default-OFF 로 라이브 거동이 바이트 동일한지 (§2 S3)
  2) pandas 기반 `legacy_scalping/scanner/indicators.py` 와 **수식이 같은지**
     (리스트 기반으로 다시 구현했으므로 교차검증으로 고정한다)
"""
from __future__ import annotations

import math

import pytest

from backend.core.strategy.dante_entry import (
    DanteConfig,
    blue_line_at,
    config_from_env,
    evaluate_breakout,
    status_at,
)

CFG = DanteConfig(enabled=True, lookback=224, multiplier=2.0, band=0.02, min_turnover=0.0)


def _bar(h, l, c, v=1_000_000):
    return {"high": h, "low": l, "close": c, "volume": v}


def _flat(n, price=10_000.0):
    return [_bar(price, price, price) for _ in range(n)]


# ── default-OFF ─────────────────────────────────────────────────────────────
def test_default_off():
    assert config_from_env({}).enabled is False
    sig = evaluate_breakout(_flat(300), config_from_env({}))
    assert sig.signal is False
    assert "비활성" in sig.reason


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_enable_truthy(raw):
    assert config_from_env({"BARRO_DANTE_ENABLED": raw}).enabled is True


@pytest.mark.parametrize("raw", ["0", "false", "", "no", "쓰레기"])
def test_enable_falsy(raw):
    assert config_from_env({"BARRO_DANTE_ENABLED": raw}).enabled is False


def test_env_garbage_falls_back_to_default():
    cfg = config_from_env({
        "BARRO_DANTE_ENABLED": "1",
        "BARRO_DANTE_LOOKBACK": "abc",
        "BARRO_DANTE_MULTIPLIER": "nan",
        "BARRO_DANTE_BAND": "-5",
        "BARRO_DANTE_MIN_TURNOVER": "1e9",
    })
    assert cfg.lookback == 224
    assert cfg.multiplier == 2.0
    assert cfg.band == 0.0          # 음수는 0 으로 클램프
    assert cfg.min_turnover == 1e9


# ── 수식 ────────────────────────────────────────────────────────────────────
def test_blue_line_flat_series_equals_price():
    """변동 없는 시계열은 ATR=0 이므로 파란점선 = 고점."""
    bars = _flat(250)
    assert blue_line_at(bars, CFG, len(bars) - 1) == pytest.approx(10_000.0)


def test_blue_line_formula_by_hand():
    # 고점 10,000 · 매봉 TR=100(종가 중앙) → ATR=100 → Lp = 10,000 - 2×100
    bars = [_bar(10_000, 9_900, 9_950) for _ in range(250)]
    assert blue_line_at(bars, CFG, 249) == pytest.approx(10_000 - 200, rel=1e-6)


@pytest.mark.parametrize("close,expected", [
    (9_700, "below"),    # 0.97 < 0.98
    (9_900, "near"),
    (10_000, "near"),
    (10_200, "near"),    # 경계 1.02 는 near (indicators.py 와 동일)
    (10_300, "above"),
])
def test_status_bands(close, expected):
    assert status_at(close, 10_000.0, 0.02) == expected


def test_cross_validate_against_pandas_indicators():
    """pandas 원본과 소수점까지 일치해야 한다."""
    pd = pytest.importorskip("pandas")
    from backend.legacy_scalping.scanner.indicators import (
        IndicatorConfig,
        calc_blue_dotted_line,
    )

    # 결정적 의사난수(추세+변동) — Math.random 미사용
    bars = []
    price = 10_000.0
    for i in range(400):
        price *= 1.0 + math.sin(i * 0.37) * 0.02 + 0.0005
        hi = price * (1 + abs(math.cos(i * 0.91)) * 0.015)
        lo = price * (1 - abs(math.sin(i * 1.13)) * 0.015)
        bars.append(_bar(hi, lo, price))

    df = pd.DataFrame(bars)
    want = calc_blue_dotted_line(df, IndicatorConfig())

    for idx in (1, 50, 223, 224, 300, 399):
        got = blue_line_at(bars, CFG, idx)
        assert got is not None
        assert got == pytest.approx(float(want.iloc[idx]), rel=1e-9), f"idx={idx}"


# ── 돌파 판정 ───────────────────────────────────────────────────────────────
def _breakout_series():
    """1년 고점에서 ATR 2배 아래로 눌려 있다가 되돌림으로 Lp 를 넘기는 시계열.

    실제 돌파가 성립하려면 **보합 가격이 224일 고점에서 2 ATR 이상 떨어져 있어야**
    한다. 변동폭 없는 시계열은 ATR≈0 이라 Lp 가 고점에 붙어 돌파가 구조적으로
    불가능하다 — 돌파 봉 자신의 고가가 Highest 를 끌어올리기 때문이다.

        고점대 9,950 · 보합 9,000 · 봉당 변동폭 200(≈2%)
        → ATR≈200 → Lp≈9,550 → above 는 9,741 초과
    """
    bars = [_bar(9_950, 9_750, 9_800) for _ in range(41)]      # 고점대
    bars += [_bar(9_100, 8_900, 9_000) for _ in range(209)]     # 긴 보합(below)
    bars.append(_bar(9_830, 9_700, 9_820))                      # 돌파
    return bars


def test_breakout_detected():
    sig = evaluate_breakout(_breakout_series(), CFG)
    assert sig.signal is True
    assert sig.status == "above"
    assert "파(破)" in sig.reason


def test_no_breakout_when_already_above():
    """전일에 이미 above 였으면 신호가 아니다(중복 진입 방지)."""
    bars = _breakout_series()
    bars.append(_bar(9_860, 9_780, 9_850))   # 전일이 이미 above
    sig = evaluate_breakout(bars, CFG)
    assert sig.signal is False
    assert "돌파 아님" in sig.reason


def test_insufficient_data_is_no_signal():
    """fail-closed — 모르면 사지 않는다."""
    sig = evaluate_breakout(_flat(100), CFG)
    assert sig.signal is False
    assert "데이터 부족" in sig.reason


@pytest.mark.parametrize("bad", [None, 0, -1, "x"])
def test_bad_close_is_no_signal(bad):
    bars = _breakout_series()
    bars[-1] = dict(bars[-1], close=bad)
    assert evaluate_breakout(bars, CFG).signal is False


def test_turnover_floor_blocks():
    bars = _breakout_series()
    cfg = DanteConfig(enabled=True, min_turnover=1e12)
    sig = evaluate_breakout(bars, cfg)
    assert sig.signal is False
    assert "거래대금" in sig.reason


def test_turnover_floor_passes_on_liquid_name():
    bars = [dict(b, volume=500_000) for b in _breakout_series()]
    cfg = DanteConfig(enabled=True, min_turnover=1e9)   # 평균 500K × 10,400 = 52억
    assert evaluate_breakout(bars, cfg).signal is True


def test_object_candles_accepted():
    """dict 뿐 아니라 OHLCV 모델 객체도 받는다(존 게이트와 같은 규약)."""
    class Bar:
        def __init__(self, high, low, close, volume):
            self.high, self.low, self.close, self.volume = high, low, close, volume

    bars = [Bar(**b) for b in _breakout_series()]
    assert evaluate_breakout(bars, CFG).signal is True
