"""
블루라인 전략 (Blue Line Strategy)

원리:
  단기 이동평균선(5일 EMA) + 중기 이동평균선(20일 EMA)이 골든크로스되고
  거래량이 증가할 때 진입하는 추세 추종 전략.

  "블루라인" = 5일 EMA. 주가가 블루라인 위에서 지지받고 재상승할 때 매수.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import List, Optional

import pandas as pd

from backend.core.strategy.base import Strategy
from backend.models.market import OHLCV, MarketType
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext

logger = logging.getLogger(__name__)


@dataclass
class BlueLineParams:
    short_period: int = 5         # 단기 이평선 (블루라인)
    long_period: int = 20         # 중기 이평선
    volume_ratio: float = 1.5     # 거래량 배율 기준
    min_gain_pct: float = 0.005   # 최소 상승률: 0.5%
    min_candles: int = 60

    # BAR-OPS-09 Phase 3 — 변동성 필터: ATR% < min_atr_pct 시 진입 거부.
    # default 0.0 (필터 비활성) — 기존 회귀 보존. 운영 적용은 SignalScanner 명시 override.
    min_atr_pct: float = 0.0
    atr_n: int = 14

    # BAR-OPS-09 Phase 8f — 진입 시간 게이트: 마지막 candle.time() >= entry_time_cutoff 시 차단.
    # default None (비활성) — 기존 회귀 보존. 운영(SignalScanner) 진입점에서 dtime(14, 0) override.
    # Phase 8c/8d/8e 동일 패턴 — 장 후반 진입 손실 패턴 차단.
    entry_time_cutoff: Optional[dtime] = None


class BlueLineStrategy(Strategy):
    """블루라인 전략 엔진"""

    STRATEGY_ID = "blue_line_v1"

    def __init__(self, params: Optional[BlueLineParams] = None) -> None:
        self.params = params or BlueLineParams()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        # BAR-45: Strategy v2 진입점.
        return self._analyze_impl(ctx.symbol, ctx.name or ctx.symbol, ctx.candles, ctx.market_type)

    def _analyze_impl(
        self,
        symbol: str,
        name: str,
        candles: List[OHLCV],
        market_type: MarketType,
    ) -> Optional[EntrySignal]:
        p = self.params
        if len(candles) < p.min_candles:
            return None

        # BAR-OPS-09 Phase 3: 변동성 필터 — ATR% < min_atr_pct 시 진입 거부 (저변동·고가주 가짜 시그널 방지)
        if p.min_atr_pct > 0:
            atr_pct = self._atr_pct(candles, n=p.atr_n)
            if atr_pct < p.min_atr_pct:
                logger.debug(
                    "%s: ATR%% 임계 미달 (%.3f < %.3f) — blue_line 진입 거부",
                    symbol, atr_pct, p.min_atr_pct,
                )
                return None

        # BAR-OPS-09 Phase 8f: 진입 시간 게이트 — 장 후반 진입 차단 (청산 여유 부족 손실 방지).
        if p.entry_time_cutoff is not None:
            last_ts = candles[-1].timestamp
            if last_ts.time() >= p.entry_time_cutoff:
                logger.debug(
                    "%s: 진입 시간 cutoff 도달 (%s >= %s) — blue_line 진입 거부",
                    symbol, last_ts.time(), p.entry_time_cutoff,
                )
                return None

        df = self._to_dataframe(candles)

        ema_short = df["close"].ewm(span=p.short_period, adjust=False).mean()
        ema_long = df["close"].ewm(span=p.long_period, adjust=False).mean()
        avg_volume = df["volume"].mean()

        current = df.iloc[-1]
        prev = df.iloc[-2]

        # 골든크로스: 전봉은 데드크로스, 현재봉은 골든크로스
        golden_cross = (
            ema_short.iloc[-2] <= ema_long.iloc[-2]
            and ema_short.iloc[-1] > ema_long.iloc[-1]
        )

        # 블루라인 위 지지 반등
        on_blue_line = (
            current["low"] <= ema_short.iloc[-1] * 1.005  # 블루라인 근접
            and current["close"] > ema_short.iloc[-1]      # 블루라인 위 마감
        )

        gain_pct = (current["close"] - current["open"]) / current["open"] if current["open"] > 0 else 0
        volume_ok = current["volume"] >= avg_volume * p.volume_ratio

        if (golden_cross or on_blue_line) and gain_pct >= p.min_gain_pct and volume_ok:
            trigger = "골든크로스" if golden_cross else "블루라인 지지 반등"
            score = 6.0 + min(gain_pct / 0.03, 1.0) * 2.0 + min(current["volume"] / (avg_volume * p.volume_ratio), 1.0) * 2.0

            return EntrySignal(
                symbol=symbol,
                name=name,
                price=current["close"],
                signal_type="blue_line",
                score=round(min(score, 10.0), 2),
                reason=f"[블루라인] {trigger} | 상승 +{gain_pct*100:.1f}% | 거래량 {current['volume']/avg_volume:.1f}x",
                market_type=market_type,
                strategy_id=self.STRATEGY_ID,
                timestamp=datetime.now(),
                metadata={
                    "ema_short": round(ema_short.iloc[-1], 2),
                    "ema_long": round(ema_long.iloc[-1], 2),
                    "volume_ratio": round(current["volume"] / avg_volume, 2),
                    "trigger": trigger,
                },
            )
        return None

    @staticmethod
    def _to_dataframe(candles: List[OHLCV]) -> pd.DataFrame:
        rows = [
            {"timestamp": c.timestamp, "open": c.open, "high": c.high,
             "low": c.low, "close": c.close, "volume": c.volume}
            for c in reversed(candles)
        ]
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        return df

    @staticmethod
    def _atr_pct(candles: List[OHLCV], n: int = 14) -> float:
        """ATR% wrapper — see backend.core.strategy.indicators.atr_pct (Phase 7 refactor)."""
        from backend.core.strategy.indicators import atr_pct
        return atr_pct(candles, n=n)
