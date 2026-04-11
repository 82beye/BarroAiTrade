"""
암호화폐 돌파 전략 (Crypto Breakout Strategy)

원리:
  박스권(횡보) 상단 저항선을 거래량 증가와 함께 돌파할 때 진입.
  암호화폐 시장의 높은 변동성에 맞게 파라미터 조정.

  - 박스권 탐지: 최근 N봉의 고점/저점 범위가 좁을 때 (횡보 구간)
  - 돌파 신호: 현재 가격이 박스권 고점 +버퍼% 초과
  - 거래량 확인: 돌파 봉의 거래량이 평균 대비 크게 증가
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import pandas as pd

from backend.models.market import OHLCV, MarketType
from backend.models.signal import EntrySignal

logger = logging.getLogger(__name__)


@dataclass
class CryptoBreakoutParams:
    box_period: int = 20              # 박스권 탐지 기간 (봉 수)
    box_max_range_pct: float = 0.08   # 박스권 최대 범위: 8% (이 이내 = 횡보)
    breakout_buffer_pct: float = 0.01 # 돌파 버퍼: 1%
    volume_ratio: float = 2.5         # 거래량 배율: 평균 대비 250%
    min_candles: int = 40


class CryptoBreakoutStrategy:
    """암호화폐 돌파 전략 엔진"""

    STRATEGY_ID = "crypto_breakout_v1"

    def __init__(self, params: Optional[CryptoBreakoutParams] = None) -> None:
        self.params = params or CryptoBreakoutParams()

    def analyze(
        self,
        symbol: str,
        name: str,
        candles: List[OHLCV],
        market_type: MarketType,
    ) -> Optional[EntrySignal]:
        if market_type != MarketType.CRYPTO:
            return None

        p = self.params
        if len(candles) < p.min_candles:
            return None

        df = self._to_dataframe(candles)
        current = df.iloc[-1]
        avg_volume = df["volume"].iloc[:-1].mean()

        # 박스권 범위: 현재 봉 제외한 최근 box_period 봉의 고/저
        box = df.iloc[-(p.box_period + 1):-1]
        box_high = box["high"].max()
        box_low = box["low"].min()
        box_range_pct = (box_high - box_low) / box_low if box_low > 0 else 999

        is_ranging = box_range_pct <= p.box_max_range_pct
        if not is_ranging:
            return None

        # 돌파 확인
        breakout_level = box_high * (1 + p.breakout_buffer_pct)
        if current["close"] < breakout_level:
            return None

        # 거래량 확인
        vol_ratio = current["volume"] / avg_volume if avg_volume > 0 else 0
        if vol_ratio < p.volume_ratio:
            return None

        breakout_pct = (current["close"] - box_high) / box_high
        score = 5.0 + min(breakout_pct / 0.05, 1.0) * 2.5 + min(vol_ratio / (p.volume_ratio * 2), 1.0) * 2.5

        return EntrySignal(
            symbol=symbol,
            name=name,
            price=current["close"],
            signal_type="crypto_breakout",
            score=round(min(score, 10.0), 2),
            reason=(
                f"[암호화폐 돌파] 박스권({box_low:.0f}~{box_high:.0f}) 상단 돌파 "
                f"+{breakout_pct*100:.1f}% | 거래량 {vol_ratio:.1f}x"
            ),
            market_type=market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(),
            metadata={
                "box_high": round(box_high, 4),
                "box_low": round(box_low, 4),
                "box_range_pct": round(box_range_pct, 4),
                "volume_ratio": round(vol_ratio, 2),
                "breakout_pct": round(breakout_pct, 4),
            },
        )

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
