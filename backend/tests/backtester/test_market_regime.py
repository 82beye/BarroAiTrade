"""market_regime — 시장 국면 자동 분류 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.core.backtester import MarketRegime, classify_regime, regime_weights
from backend.models.market import MarketType, OHLCV


def _candles(n: int, return_pct: float = 0.0, bull_ratio: float = 0.5) -> list[OHLCV]:
    """합성 캔들 — n봉, 누적 수익률·양봉 비율 제어."""
    out: list[OHLCV] = []
    t0 = datetime(2026, 4, 1, 9, 0)
    start = 1000.0
    end = start * (1.0 + return_pct)
    step = (end - start) / max(n - 1, 1)
    # 양봉 패턴: 앞쪽 bull_n 개는 양봉, 나머지 음봉
    bull_n = int(n * bull_ratio)
    for i in range(n):
        close = start + step * i
        open_ = close - step * 0.5 if i < bull_n else close + abs(step) * 0.5
        out.append(OHLCV(
            symbol="TEST", timestamp=t0 + timedelta(days=i),
            open=open_, high=max(open_, close) + 1, low=min(open_, close) - 1,
            close=close, volume=1000, market_type=MarketType.STOCK,
        ))
    return out


def test_classify_bull():
    """평균 수익률 +5%+ AND 양봉 비율 55%+ → BULL."""
    candles_by = {f"S{i}": _candles(31, return_pct=0.10, bull_ratio=0.7) for i in range(3)}
    assert classify_regime(candles_by, lookback=30) == MarketRegime.BULL


def test_classify_bearish_by_return():
    """평균 수익률 -5% 이하 → BEARISH."""
    candles_by = {f"S{i}": _candles(31, return_pct=-0.10, bull_ratio=0.5) for i in range(3)}
    assert classify_regime(candles_by, lookback=30) == MarketRegime.BEARISH


def test_classify_bearish_by_bull_pct():
    """양봉 비율 40% 이하 → BEARISH (수익률 무관)."""
    candles_by = {f"S{i}": _candles(31, return_pct=0.02, bull_ratio=0.3) for i in range(3)}
    assert classify_regime(candles_by, lookback=30) == MarketRegime.BEARISH


def test_classify_sideways():
    """수익률 작고 양봉 비율 중간 → SIDEWAYS."""
    candles_by = {f"S{i}": _candles(31, return_pct=0.02, bull_ratio=0.5) for i in range(3)}
    assert classify_regime(candles_by, lookback=30) == MarketRegime.SIDEWAYS


def test_classify_empty_input():
    """빈 입력 → SIDEWAYS (안전 기본)."""
    assert classify_regime({}) == MarketRegime.SIDEWAYS


def test_classify_insufficient_candles():
    """캔들 부족 종목만 → SIDEWAYS."""
    candles_by = {"S0": _candles(10, return_pct=0.10, bull_ratio=0.9)}
    assert classify_regime(candles_by, lookback=30) == MarketRegime.SIDEWAYS


def test_regime_weights_bull():
    w = regime_weights(MarketRegime.BULL)
    assert w["swing_38"] == 1.5
    assert w["gold_zone"] == 0.5  # 과매도 매수 비중 축소


def test_regime_weights_sideways():
    w = regime_weights(MarketRegime.SIDEWAYS)
    assert w["swing_38"] == 0.3  # 5/16 검증값
    assert w["f_zone"] == 0.7
    assert w["gold_zone"] == 1.0


def test_regime_weights_bearish():
    w = regime_weights(MarketRegime.BEARISH)
    assert w["gold_zone"] == 1.5  # 하락장 과매도 매수 확대
    assert w["f_zone"] == 1.0


def test_regime_weights_returns_copy():
    """regime_weights 가 반환한 dict 수정이 REGIME_WEIGHTS 원본을 변경 안 함."""
    w = regime_weights(MarketRegime.SIDEWAYS)
    w["swing_38"] = 99.0
    fresh = regime_weights(MarketRegime.SIDEWAYS)
    assert fresh["swing_38"] == 0.3
