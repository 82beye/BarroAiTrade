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
from datetime import datetime
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
