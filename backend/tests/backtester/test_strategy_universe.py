"""strategy_universe — 전략별 종목 후보군 분류 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.core.backtester.strategy_universe import compute_universe
from backend.models.market import MarketType, OHLCV


def _candles(n: int = 31, return_pct: float = 0.0, bull_ratio: float = 0.5,
             high_factor: float = 1.0, vol_factor: float = 1.0) -> list[OHLCV]:
    """합성 캔들 — 누적 수익률·양봉 비율·변동성 제어.

    high_factor: 1.0 = 변동 작음, 1.05 = 최근 고점이 종가보다 5% 위.
    vol_factor:  1.0 = TR ≈ 2 (low ATR), 5.0 = TR ≈ 10 (high ATR).
    """
    out: list[OHLCV] = []
    t0 = datetime(2026, 4, 1, 9, 0)
    start = 1000.0
    end = start * (1.0 + return_pct)
    step = (end - start) / max(n - 1, 1)
    bull_n = int(n * bull_ratio)
    for i in range(n):
        close = start + step * i
        open_ = close - step * 0.5 if i < bull_n else close + abs(step) * 0.5
        vol_range = vol_factor
        out.append(OHLCV(
            symbol="TEST", timestamp=t0 + timedelta(days=i),
            open=open_, high=max(open_, close) * high_factor + vol_range,
            low=min(open_, close) - vol_range,
            close=close, volume=1000, market_type=MarketType.STOCK,
        ))
    return out


def test_swing_38_universe_bullish():
    """강세 추세(ret ≥+5%, bull ≥55%) → swing_38 universe."""
    cs = {"S0": _candles(31, return_pct=0.10, bull_ratio=0.65)}
    u = compute_universe(cs, lookback=30)
    assert "S0" in u["swing_38"]


def test_swing_38_excludes_sideways():
    """박스권(ret 0%) → swing_38 제외."""
    cs = {"S0": _candles(31, return_pct=0.0, bull_ratio=0.5)}
    u = compute_universe(cs, lookback=30)
    assert "S0" not in u["swing_38"]


def test_gold_zone_universe_sideways():
    """박스권(ret ∈ [-5%, +5%]) → gold_zone universe."""
    cs = {"S0": _candles(31, return_pct=0.02, bull_ratio=0.5)}
    u = compute_universe(cs, lookback=30)
    assert "S0" in u["gold_zone"]


def test_gold_zone_excludes_strong_bull():
    """강한 강세(ret +10%) → gold_zone 제외."""
    cs = {"S0": _candles(31, return_pct=0.10, bull_ratio=0.65)}
    u = compute_universe(cs, lookback=30)
    assert "S0" not in u["gold_zone"]


def test_f_zone_universe_bullish_with_pullback():
    """강세 + 고점대비 -2~-5% → f_zone/sf_zone universe."""
    # 강세 ret +10% + high_factor 1.04 → 고점이 종가보다 4% 위 (drawdown -4%)
    cs = {"S0": _candles(31, return_pct=0.10, bull_ratio=0.6, high_factor=1.04)}
    u = compute_universe(cs, lookback=30)
    assert "S0" in u["f_zone"]
    assert "S0" in u["sf_zone"]


def test_f_zone_excludes_no_pullback():
    """강세 + 고점대비 ~0% (조정 없음) → f_zone 제외."""
    cs = {"S0": _candles(31, return_pct=0.10, bull_ratio=0.6, high_factor=1.0)}
    u = compute_universe(cs, lookback=30)
    # high_factor 1.0 + vol_factor → 종가가 고점 근처. drawdown ~ 0%
    # f_zone 조건(-5~-2%) 안 맞음
    assert "S0" not in u["f_zone"]


def test_scalping_universe_volatile():
    """ATR% ≥3% → scalping_consensus universe."""
    cs = {"S0": _candles(31, return_pct=0.0, bull_ratio=0.5, vol_factor=50)}
    u = compute_universe(cs, lookback=30)
    assert "S0" in u["scalping_consensus"]


def test_scalping_excludes_low_volatility():
    """ATR% < 3% → scalping_consensus 제외."""
    cs = {"S0": _candles(31, return_pct=0.0, bull_ratio=0.5, vol_factor=0.5)}
    u = compute_universe(cs, lookback=30)
    assert "S0" not in u["scalping_consensus"]


def test_insufficient_candles_excluded():
    """캔들 부족 → 어느 universe 에도 포함 안 됨."""
    cs = {"S0": _candles(10, return_pct=0.10, bull_ratio=0.7)}
    u = compute_universe(cs, lookback=30)
    assert all(len(v) == 0 for v in u.values())


def test_empty_input():
    u = compute_universe({}, lookback=30)
    assert all(len(v) == 0 for v in u.values())


def test_all_strategies_have_set():
    """반환 dict 에 5전략 모두 키 존재 (빈 set 이라도)."""
    u = compute_universe({}, lookback=30)
    assert set(u.keys()) == {
        "f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus",
    }
