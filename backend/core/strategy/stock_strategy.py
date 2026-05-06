"""
StockStrategy — 파란점선 + 수박지표 통합 한국 주식 매매 전략

진입 조건:
  1. 파란점선 돌파 (Highest(High,224) - ATR(224)×2.0 위로 종가 돌파)
  2. 수박지표 확인 (거래량폭증 + 캔들확장 + 바닥권 3중 조건)

청산 조건 (RiskEngine 위임):
  - 1차 익절: +3% 도달 시 50% 매도
  - 2차 익절: +5% 도달 시 전량 매도
  - 손절: -2% 하락 시 전량 매도
  - 강제청산: 14:50 도달 시 전량 매도
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

import numpy as np

from backend.core.strategy.base import Strategy
from backend.models.market import OHLCV, MarketType
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext

logger = logging.getLogger(__name__)

# ── 파란점선 파라미터 ──────────────────────────────────────────────────────────
_BLUE_LINE_PERIOD = 224       # Highest(High, 224)
_ATR_PERIOD = 224             # ATR(224)
_ATR_MULTIPLIER = 2.0         # 파란점선 = Highest(High,224) - ATR(224)×2.0

# ── 수박지표 파라미터 ─────────────────────────────────────────────────────────
_VOL_PERIOD = 20              # 거래량 이동평균 기간
_VOL_RATIO = 2.0              # 거래량 폭증 기준 (평균의 2배)
_CANDLE_BODY_RATIO = 0.6      # 캔들 실체 비율 기준
_RSI_PERIOD = 14              # RSI 기간
_RSI_OVERSOLD = 35.0          # 바닥권 RSI 기준


def _true_range(df_high, df_low, df_close):
    """True Range 계산"""
    hl = df_high - df_low
    hc = (df_high - np.roll(df_close, 1)).clip(min=0)
    lc = (np.roll(df_close, 1) - df_low).clip(min=0)
    return np.maximum(np.maximum(hl, hc), lc)


def _atr(high, low, close, period):
    tr = _true_range(high, low, close)
    result = np.full_like(tr, np.nan)
    result[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        result[i] = (result[i - 1] * (period - 1) + tr[i]) / period
    return result


def _rsi(close, period):
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(close), np.nan)
    avg_loss = np.full(len(close), np.nan)
    avg_gain[period] = gain[:period].mean()
    avg_loss[period] = loss[:period].mean()
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i - 1]) / period
    rs = np.where(avg_loss == 0, np.inf, avg_gain / avg_loss)
    return np.where(np.isnan(avg_gain), np.nan, 100 - 100 / (1 + rs))


class StockStrategy(Strategy):
    """파란점선 + 수박지표 통합 한국 주식 전략"""

    STRATEGY_ID = "stock_v1"
    MIN_CANDLES = max(_BLUE_LINE_PERIOD, _ATR_PERIOD) + 10

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        # BAR-45: Strategy v2 진입점.
        return self._analyze_impl(ctx.symbol, ctx.name or ctx.symbol, ctx.candles, ctx.market_type)

    def _analyze_impl(
        self,
        symbol: str,
        name: str,
        candles: List[OHLCV],
        market_type: MarketType = MarketType.STOCK,
    ) -> Optional[EntrySignal]:
        if len(candles) < self.MIN_CANDLES:
            return None

        high = np.array([c.high for c in candles], dtype=float)
        low = np.array([c.low for c in candles], dtype=float)
        close = np.array([c.close for c in candles], dtype=float)
        volume = np.array([c.volume for c in candles], dtype=float)

        # ── 파란점선 계산 ──────────────────────────────────────────────────────
        rolling_high = np.array([
            high[max(0, i - _BLUE_LINE_PERIOD + 1):i + 1].max()
            for i in range(len(high))
        ])
        atr = _atr(high, low, close, _ATR_PERIOD)
        blue_line = rolling_high - atr * _ATR_MULTIPLIER

        # 파란점선 돌파: 이전 종가가 파란점선 아래, 현재 종가가 파란점선 위
        prev_close = close[-2]
        curr_close = close[-1]
        curr_blue = blue_line[-1]
        prev_blue = blue_line[-2]

        if np.isnan(curr_blue) or np.isnan(prev_blue):
            return None

        blue_breakout = prev_close <= prev_blue and curr_close > curr_blue

        if not blue_breakout:
            return None

        # ── 수박지표 계산 ─────────────────────────────────────────────────────
        # 1. 거래량 폭증
        avg_vol = volume[-_VOL_PERIOD - 1:-1].mean()
        vol_surge = volume[-1] >= avg_vol * _VOL_RATIO if avg_vol > 0 else False

        # 2. 캔들 확장 (실체/전체 범위 비율)
        curr_high = high[-1]
        curr_low = low[-1]
        candle_range = curr_high - curr_low
        candle_body = abs(curr_close - candles[-1].open)
        body_ratio = candle_body / candle_range if candle_range > 0 else 0
        strong_candle = body_ratio >= _CANDLE_BODY_RATIO

        # 3. 바닥권 (RSI 기반)
        rsi_values = _rsi(close, _RSI_PERIOD)
        curr_rsi = rsi_values[-1]
        near_bottom = np.isnan(curr_rsi) or curr_rsi <= _RSI_OVERSOLD

        watermelon = vol_surge and strong_candle and near_bottom

        # ── 신호 점수 계산 ────────────────────────────────────────────────────
        score = 70.0  # 파란점선 돌파 기본 점수
        if vol_surge:
            score += 10.0
        if strong_candle:
            score += 10.0
        if near_bottom:
            score += 10.0

        breakout_pct = (curr_close - curr_blue) / curr_blue * 100 if curr_blue > 0 else 0

        reason_parts = [f"파란점선 돌파 (+{breakout_pct:.2f}%)"]
        if watermelon:
            reason_parts.append("수박지표 확인")
        if vol_surge:
            reason_parts.append(f"거래량 폭증 ({volume[-1] / avg_vol:.1f}x)")

        logger.info(
            "StockStrategy 신호: %s %s | score=%.1f | watermelon=%s",
            symbol, name, score, watermelon,
        )

        return EntrySignal(
            symbol=symbol,
            name=name,
            price=curr_close,
            signal_type="blue_line",
            score=score,
            reason=" | ".join(reason_parts),
            market_type=market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(),
            risk_approved=False,
            metadata={
                "blue_line": float(curr_blue),
                "breakout_pct": float(breakout_pct),
                "watermelon": watermelon,
                "vol_surge": vol_surge,
                "strong_candle": strong_candle,
                "near_bottom": near_bottom,
                "rsi": float(curr_rsi) if not np.isnan(curr_rsi) else None,
            },
        )
