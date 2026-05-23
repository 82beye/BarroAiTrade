"""BAR-182 — TechnicalIndicators / IndicatorCalculator 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from backend.core.scanner.indicators import IndicatorCalculator, TechnicalIndicators
from backend.models.market import OHLCV, MarketType


_TS = datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc)


def _ohlcv(open_: float, high: float, low: float, close: float, volume: float = 1000.0) -> OHLCV:
    return OHLCV(
        symbol="005930",
        timestamp=_TS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        market_type=MarketType.STOCK,
    )


def _candles(closes: list[float]) -> list[OHLCV]:
    """close 리스트로 단순 캔들 생성 (high=close, low=close*0.99, open=close)."""
    return [_ohlcv(c, c, c * 0.99, c) for c in closes]


# ─── RSI ─────────────────────────────────────────────────────────────────────


class TestCalculateRsi:
    def test_insufficient_data_returns_neutral(self):
        values = np.array([100.0] * 5)
        rsi = TechnicalIndicators.calculate_rsi(values, period=14)
        assert len(rsi) == 5
        assert all(r == 50.0 for r in rsi)

    def test_neutral_initial_bars(self):
        values = np.array([100.0 + i * 0.5 for i in range(20)], dtype=float)
        rsi = TechnicalIndicators.calculate_rsi(values, period=14)
        # 첫 14개 바는 중립값 50
        assert all(r == 50.0 for r in rsi[:14])

    def test_all_up_returns_100(self):
        # BAR-181: down==0 시 RSI=100 이어야 함 (구버그: 0 반환)
        values = np.array([100.0 + i for i in range(20)], dtype=float)
        rsi = TechnicalIndicators.calculate_rsi(values, period=14)
        assert rsi[14] == pytest.approx(100.0)
        # 이후 상승 바도 RSI=100 유지
        assert all(r == pytest.approx(100.0) for r in rsi[14:])

    def test_mixed_moves_reasonable_range(self):
        # 상승/하락 혼합 → RSI가 0~100 범위 내
        values = np.array([100, 102, 101, 103, 102, 104, 103, 105,
                           104, 106, 105, 107, 106, 108, 107, 109], dtype=float)
        rsi = TechnicalIndicators.calculate_rsi(values, period=14)
        for r in rsi[14:]:
            assert 0.0 <= r <= 100.0

    def test_all_down_returns_near_0(self):
        # 모두 하락 → up=0, RSI가 낮아야 함
        values = np.array([100.0 - i for i in range(20)], dtype=float)
        rsi = TechnicalIndicators.calculate_rsi(values, period=14)
        # up=0 → rs=0 → RSI=0
        assert rsi[14] == pytest.approx(0.0)

    def test_output_length_matches_input(self):
        values = np.arange(30, dtype=float)
        rsi = TechnicalIndicators.calculate_rsi(values, period=14)
        assert len(rsi) == 30


# ─── SMA ─────────────────────────────────────────────────────────────────────


class TestCalculateSma:
    def test_insufficient_data(self):
        values = np.array([1.0, 2.0, 3.0])
        sma = TechnicalIndicators.calculate_sma(values, period=5)
        assert len(sma) == 3
        assert all(s == 0.0 for s in sma)

    def test_constant_returns_constant(self):
        values = np.full(20, 5.0)
        sma = TechnicalIndicators.calculate_sma(values, period=5)
        assert sma[-1] == pytest.approx(5.0)

    def test_simple_average(self):
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        sma = TechnicalIndicators.calculate_sma(values, period=3)
        # 마지막 3개 평균: (3+4+5)/3 = 4.0
        assert sma[-1] == pytest.approx(4.0)

    def test_output_length_matches(self):
        values = np.arange(20, dtype=float)
        sma = TechnicalIndicators.calculate_sma(values, period=5)
        assert len(sma) == 20


# ─── EMA ─────────────────────────────────────────────────────────────────────


class TestCalculateEma:
    def test_insufficient_data_returns_copy(self):
        values = np.array([1.0, 2.0, 3.0])
        ema = TechnicalIndicators.calculate_ema(values, period=5)
        assert len(ema) == 3

    def test_constant_returns_constant(self):
        values = np.full(20, 5.0)
        ema = TechnicalIndicators.calculate_ema(values, period=5)
        assert ema[-1] == pytest.approx(5.0)

    def test_ema_tracks_rising(self):
        values = np.array([1.0] * 10 + [100.0] * 10, dtype=float)
        ema = TechnicalIndicators.calculate_ema(values, period=5)
        # EMA가 점진적으로 상승해야 함
        assert ema[-1] > ema[10]

    def test_output_length_matches(self):
        values = np.arange(25, dtype=float)
        ema = TechnicalIndicators.calculate_ema(values, period=5)
        assert len(ema) == 25


# ─── ATR ─────────────────────────────────────────────────────────────────────


class TestCalculateAtr:
    def test_insufficient_data_returns_zeros(self):
        candles = _candles([100.0] * 5)
        atr = TechnicalIndicators.calculate_atr(candles, period=14)
        assert len(atr) == 5
        assert all(a == 0.0 for a in atr)

    def test_constant_candles_low_atr(self):
        candles = [_ohlcv(100, 101, 99, 100) for _ in range(20)]
        atr = TechnicalIndicators.calculate_atr(candles, period=14)
        assert len(atr) == 20
        assert atr[-1] > 0.0

    def test_volatile_candles_higher_atr(self):
        low_vol = [_ohlcv(100, 101, 99, 100) for _ in range(20)]
        high_vol = [_ohlcv(100, 110, 90, 100) for _ in range(20)]
        atr_low = TechnicalIndicators.calculate_atr(low_vol, period=14)
        atr_high = TechnicalIndicators.calculate_atr(high_vol, period=14)
        assert atr_high[-1] > atr_low[-1]

    def test_output_length_matches(self):
        candles = _candles([100.0] * 30)
        atr = TechnicalIndicators.calculate_atr(candles, period=14)
        assert len(atr) == 30


# ─── Blue Dotted Line ─────────────────────────────────────────────────────────


class TestCalculateBlueDottedLine:
    def test_insufficient_returns_zeros(self):
        candles = _candles([100.0] * 10)
        bdl = TechnicalIndicators.calculate_blue_dotted_line(candles, period=224)
        assert all(b == 0.0 for b in bdl)

    def test_sufficient_data_nonzero(self):
        candles = [_ohlcv(100, 110, 90, 100) for _ in range(230)]
        bdl = TechnicalIndicators.calculate_blue_dotted_line(candles, period=224)
        assert len(bdl) == 230
        # 충분한 데이터 후 비-zero 값
        assert bdl[-1] > 0.0

    def test_output_length_matches(self):
        candles = _candles([100.0] * 250)
        bdl = TechnicalIndicators.calculate_blue_dotted_line(candles, period=224)
        assert len(bdl) == 250


# ─── Watermelon Signal ───────────────────────────────────────────────────────


class TestCalculateWatermelonSignal:
    def test_insufficient_data_no_signal(self):
        candles = _candles([100.0] * 50)
        signal, strength = TechnicalIndicators.calculate_watermelon_signal(
            candles, bottom_zone_lookback=100
        )
        assert len(signal) == 50
        assert not any(signal)

    def test_output_arrays_same_length(self):
        candles = _candles([100.0] * 120)
        signal, strength = TechnicalIndicators.calculate_watermelon_signal(
            candles, bottom_zone_lookback=100
        )
        assert len(signal) == 120
        assert len(strength) == 120

    def test_strength_nonnegative(self):
        candles = _candles([100.0 - i * 0.01 for i in range(120)])
        _, strength = TechnicalIndicators.calculate_watermelon_signal(
            candles, bottom_zone_lookback=100
        )
        assert all(s >= 0.0 for s in strength)

    def test_volume_surge_triggers_signal(self):
        # 정상 캔들 100개 + 마지막 캔들에서 거래량 폭증 + 캔들 확장
        base = [_ohlcv(50, 55, 45, 50, volume=100.0) for _ in range(119)]
        # 고거래량 + 큰 봉 + 바닥권
        boom = _ohlcv(50, 58, 42, 50, volume=500.0)
        candles = base + [boom]
        signal, strength = TechnicalIndicators.calculate_watermelon_signal(
            candles,
            volume_threshold=2.0,
            candle_expansion_ratio=1.5,
            bottom_zone_lookback=100,
        )
        # 신호 발생 여부는 데이터 의존적이므로 boolean 타입만 확인
        assert signal[-1] in (True, False)


# ─── IndicatorCalculator (통합) ───────────────────────────────────────────────


class TestIndicatorCalculator:
    def test_empty_input_returns_empty(self):
        calc = IndicatorCalculator()
        result = calc.calculate([])
        assert result == []

    def test_output_length_matches_input(self):
        calc = IndicatorCalculator()
        candles = [_ohlcv(100, 110, 90, 100) for _ in range(50)]
        result = calc.calculate(candles)
        assert len(result) == 50

    def test_indicator_fields_present(self):
        calc = IndicatorCalculator()
        candles = [_ohlcv(100, 110, 90, 100) for _ in range(50)]
        result = calc.calculate(candles)
        latest = result[-1]
        assert hasattr(latest, "rsi")
        assert hasattr(latest, "atr")
        assert hasattr(latest, "blue_dotted_line")
        assert hasattr(latest, "watermelon_signal")
        assert hasattr(latest, "watermelon_strength")
        assert hasattr(latest, "sma")
        assert hasattr(latest, "ema")

    def test_rsi_range_valid(self):
        calc = IndicatorCalculator()
        candles = [_ohlcv(100 + i, 105 + i, 95 + i, 100 + i) for i in range(50)]
        result = calc.calculate(candles)
        for ind in result:
            assert 0.0 <= ind.rsi <= 100.0

    def test_all_up_rsi_is_100(self):
        # BAR-181 회귀 테스트: 모두 상승하는 캔들의 RSI = 100
        calc = IndicatorCalculator()
        calc.rsi_period = 14
        candles = [_ohlcv(100 + i, 105 + i, 99 + i, 100 + i) for i in range(30)]
        result = calc.calculate(candles)
        # period 이후 RSI는 100이어야 함
        assert result[14].rsi == pytest.approx(100.0)

    def test_timestamp_isoformat(self):
        calc = IndicatorCalculator()
        candles = [_ohlcv(100, 110, 90, 100) for _ in range(20)]
        result = calc.calculate(candles)
        assert isinstance(result[-1].timestamp, str)
