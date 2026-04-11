"""
기술적 분석 지표 — 파란점선, 수박, 기본 지표

구현:
- 파란점선 (Blue Dotted Line): Highest(High,224) - ATR(224)×2.0
- 수박 (Watermelon): 거래량폭증 + 캔들확장 + 바닥권 3중조건
- ATR, EMA, 이동평균 등 기본 지표
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
import pandas as pd

from backend.models.market import OHLCV


@dataclass
class IndicatorValues:
    """지표 계산 결과"""

    timestamp: str
    close: float
    high: float
    low: float
    volume: float

    # 기본 지표
    sma: dict  # {period: value}
    ema: dict  # {period: value}
    atr: float
    rsi: float

    # 파란점선
    blue_dotted_line: float

    # 수박
    watermelon_signal: bool
    watermelon_strength: float


class TechnicalIndicators:
    """기술적 분석 지표 계산기"""

    @staticmethod
    def calculate_atr(candles: List[OHLCV], period: int = 14) -> np.ndarray:
        """ATR (Average True Range) 계산"""
        if len(candles) < period:
            return np.array([0.0] * len(candles))

        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])
        closes = np.array([c.close for c in candles])

        tr1 = highs - lows
        tr2 = np.abs(highs - np.roll(closes, 1))
        tr3 = np.abs(lows - np.roll(closes, 1))

        tr = np.maximum(tr1, np.maximum(tr2, tr3))
        atr = np.convolve(tr, np.ones(period) / period, mode="valid")

        # Pad 배열 크기 맞추기
        padding = len(candles) - len(atr)
        atr = np.concatenate([np.full(padding, atr[0] if len(atr) > 0 else 0), atr])

        return atr

    @staticmethod
    def calculate_sma(values: np.ndarray, period: int) -> np.ndarray:
        """SMA (Simple Moving Average) 계산"""
        if len(values) < period:
            return np.array([0.0] * len(values))

        sma = np.convolve(values, np.ones(period) / period, mode="valid")
        padding = len(values) - len(sma)
        sma = np.concatenate([np.full(padding, sma[0] if len(sma) > 0 else 0), sma])

        return sma

    @staticmethod
    def calculate_ema(values: np.ndarray, period: int) -> np.ndarray:
        """EMA (Exponential Moving Average) 계산"""
        if len(values) < period:
            return values.copy()

        ema = np.zeros(len(values))
        multiplier = 2 / (period + 1)
        ema[period - 1] = values[:period].mean()

        for i in range(period, len(values)):
            ema[i] = values[i] * multiplier + ema[i - 1] * (1 - multiplier)

        return ema

    @staticmethod
    def calculate_rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
        """RSI (Relative Strength Index) 계산"""
        if len(values) < period + 1:
            return np.full(len(values), 50.0)

        rsi = np.zeros(len(values))
        deltas = np.diff(values)

        # 초기값
        seed = deltas[:period]
        up = seed[seed >= 0].sum() / period
        down = -seed[seed < 0].sum() / period

        rsi[period] = 100.0 - 100.0 / (1.0 + up / down if down != 0 else 1.0)

        # EMA 방식 계산
        for i in range(period + 1, len(values)):
            delta = deltas[i - 1]
            if delta > 0:
                up = (up * (period - 1) + delta) / period
                down = (down * (period - 1)) / period
            else:
                up = (up * (period - 1)) / period
                down = (down * (period - 1) - delta) / period

            rsi[i] = 100.0 - 100.0 / (1.0 + up / down if down != 0 else 1.0)

        # 초기값 채우기 (중립권 50)
        rsi[:period] = 50.0

        return rsi

    @staticmethod
    def calculate_blue_dotted_line(candles: List[OHLCV], period: int = 224) -> np.ndarray:
        """
        파란점선 지표: Highest(High, 224) - ATR(224)×2.0

        이 지표는 종목의 기술적 저항선으로 활용.
        주가가 이 선 아래로 내려오면 강한 지지대로 인식.
        """
        if len(candles) < period:
            return np.array([0.0] * len(candles))

        highs = np.array([c.high for c in candles])

        # Highest(High, 224) 계산
        highest = np.zeros(len(candles))
        for i in range(len(candles)):
            start = max(0, i - period + 1)
            highest[i] = np.max(highs[start:i+1])

        # ATR(224) 계산
        atr = TechnicalIndicators.calculate_atr(candles, period)

        # 파란점선 = Highest(High, 224) - ATR(224) × 2.0
        blue_dotted_line = highest - (atr * 2.0)

        return blue_dotted_line

    @staticmethod
    def calculate_watermelon_signal(
        candles: List[OHLCV],
        volume_threshold: float = 2.0,
        candle_expansion_ratio: float = 1.5,
        bottom_zone_lookback: int = 100,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        수박 지표: 3중 조건 신호

        조건:
        1. 거래량폭증: 현재 거래량 > 평균 거래량 × threshold
        2. 캔들확장: 현재 캔들의 바디 > 최근 평균 캔들의 바디 × ratio
        3. 바닥권: 현재가가 일정 기간의 저가 근처

        Returns:
            (signal_array, strength_array)
            - signal_array: bool, 모든 조건 충족 여부
            - strength_array: float, 신호 강도 (0.0 ~ 1.0)
        """
        if len(candles) < bottom_zone_lookback:
            return np.zeros(len(candles), dtype=bool), np.zeros(len(candles))

        closes = np.array([c.close for c in candles])
        volumes = np.array([c.volume for c in candles])
        highs = np.array([c.high for c in candles])
        lows = np.array([c.low for c in candles])

        # 1. 거래량폭증 조건
        avg_volume = pd.Series(volumes).rolling(window=20).mean().values
        volume_surge = volumes > (avg_volume * volume_threshold)

        # 2. 캔들확장 조건
        candle_bodies = np.abs(closes - np.array([c.open for c in candles]))
        avg_candle_body = pd.Series(candle_bodies).rolling(window=20).mean().values
        candle_expansion = candle_bodies > (avg_candle_body * candle_expansion_ratio)

        # 3. 바닥권 조건
        period_low = pd.Series(lows).rolling(window=bottom_zone_lookback).min().values
        period_high = pd.Series(highs).rolling(window=bottom_zone_lookback).max().values

        # 바닥권: 현재가가 (최저 ~ 최저+전체폭의 20%)
        bottom_zone_range = (period_high - period_low) * 0.2
        in_bottom_zone = closes <= (period_low + bottom_zone_range)

        # 모든 조건 충족
        watermelon_signal = volume_surge & candle_expansion & in_bottom_zone

        # 강도 계산
        strength = np.zeros(len(candles))
        for i in range(len(candles)):
            if watermelon_signal[i]:
                volume_score = min((volumes[i] / avg_volume[i] - volume_threshold) / 2, 1.0)
                candle_score = min((candle_bodies[i] / avg_candle_body[i] - candle_expansion_ratio) / 2, 1.0)
                bottom_score = 1.0 - ((closes[i] - period_low[i]) / (bottom_zone_range[i] + 1e-10))
                strength[i] = (volume_score + candle_score + bottom_score) / 3

        return watermelon_signal, strength


class IndicatorCalculator:
    """지표 일괄 계산기"""

    def __init__(self):
        self.sma_periods = [5, 20, 60]
        self.ema_periods = [5, 20, 60]
        self.atr_period = 14
        self.rsi_period = 14
        self.blue_dotted_period = 224
        self.watermelon_lookback = 100

    def calculate(self, candles: List[OHLCV]) -> List[IndicatorValues]:
        """모든 지표 계산"""
        if not candles:
            return []

        df = self._to_dataframe(candles)
        closes = df["close"].values
        volumes = df["volume"].values

        # 기본 지표 계산
        sma_dict = {}
        for period in self.sma_periods:
            sma = TechnicalIndicators.calculate_sma(closes, period)
            sma_dict[period] = sma

        ema_dict = {}
        for period in self.ema_periods:
            ema = TechnicalIndicators.calculate_ema(closes, period)
            ema_dict[period] = ema

        atr = TechnicalIndicators.calculate_atr(candles, self.atr_period)
        rsi = TechnicalIndicators.calculate_rsi(closes, self.rsi_period)

        # 파란점선 계산
        blue_dotted_line = TechnicalIndicators.calculate_blue_dotted_line(
            candles, self.blue_dotted_period
        )

        # 수박 신호 계산
        watermelon_signal, watermelon_strength = TechnicalIndicators.calculate_watermelon_signal(
            candles, bottom_zone_lookback=self.watermelon_lookback
        )

        # IndicatorValues 생성
        result = []
        for i in range(len(candles)):
            candle = candles[i]

            sma_values = {period: sma_dict[period][i] for period in self.sma_periods}
            ema_values = {period: ema_dict[period][i] for period in self.ema_periods}

            indicator = IndicatorValues(
                timestamp=candle.timestamp.isoformat(),
                close=candle.close,
                high=candle.high,
                low=candle.low,
                volume=candle.volume,
                sma=sma_values,
                ema=ema_values,
                atr=atr[i],
                rsi=rsi[i],
                blue_dotted_line=blue_dotted_line[i],
                watermelon_signal=bool(watermelon_signal[i]),
                watermelon_strength=float(watermelon_strength[i]),
            )
            result.append(indicator)

        return result

    @staticmethod
    def _to_dataframe(candles: List[OHLCV]) -> pd.DataFrame:
        """OHLCV를 DataFrame으로 변환"""
        return pd.DataFrame(
            {
                "open": [c.open for c in candles],
                "high": [c.high for c in candles],
                "low": [c.low for c in candles],
                "close": [c.close for c in candles],
                "volume": [c.volume for c in candles],
            }
        )
