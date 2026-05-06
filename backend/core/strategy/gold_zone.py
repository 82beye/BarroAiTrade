"""
골드존 전략 (Gold Zone Strategy) — 보수적 되돌림 매수.

진입 3 조건 동시 충족:
  - BB(20, 2σ) 하단 1% 이내 진입
  - Fib 0.382~0.618 zone 안 (최근 30봉 고점-저점 기준)
  - RSI(14) 30 이하 후 40 돌파 회복 (oversold → neutral)

BAR-48: 신규 포팅 (Plan §1.1, Design §1.1).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from decimal import Decimal
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from backend.core.strategy.base import Strategy
from backend.models.market import MarketType, OHLCV
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)


@dataclass
class GoldZoneParams:
    """골드존 파라미터."""

    bb_period: int = 20
    bb_std: float = 2.0
    fib_lookback: int = 30
    fib_min: float = 0.382
    fib_max: float = 0.618
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_recovery: float = 40.0
    bb_proximity_pct: float = 0.01
    min_candles: int = 60


class GoldZoneStrategy(Strategy):
    """골드존 — BB 하단 + Fib 0.382~0.618 + RSI 회복."""

    STRATEGY_ID = "gold_zone_v1"

    def __init__(self, params: Optional[GoldZoneParams] = None) -> None:
        self.params = params or GoldZoneParams()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        p = self.params
        if len(ctx.candles) < p.min_candles:
            return None

        df = self._to_dataframe(ctx.candles)

        bb_score = self._bb_score(df)
        fib_score = self._fib_score(df)
        rsi_score = self._rsi_score(df)

        # 3 조건 모두 충족 (각 score > 0)
        if bb_score == 0 or fib_score == 0 or rsi_score == 0:
            return None

        score = bb_score * 0.4 + fib_score * 0.3 + rsi_score * 0.3
        if score < 0.3:
            return None

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=float(df["close"].iloc[-1]),
            signal_type="blue_line",  # 5 enum 제약 — gold_zone subtype 은 metadata
            score=round(float(score), 2),
            reason=(
                f"골드존: BB하단({bb_score:.2f}) + Fib({fib_score:.2f}) + RSI회복({rsi_score:.2f})"
            ),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "gold_zone_subtype": "gold_zone",
                "bb_score": round(float(bb_score), 3),
                "fib_score": round(float(fib_score), 3),
                "rsi_score": round(float(rsi_score), 3),
            },
        )

    # === BB / Fib / RSI 계산 ===

    def _bb_score(self, df: pd.DataFrame) -> float:
        """BB 하단 1% 이내 → [0, 1]. 더 가까울수록 높음."""
        p = self.params
        sma = df["close"].rolling(p.bb_period).mean().iloc[-1]
        std = df["close"].rolling(p.bb_period).std().iloc[-1]
        if pd.isna(sma) or pd.isna(std) or std == 0:
            return 0.0
        lower = sma - p.bb_std * std
        close = df["close"].iloc[-1]
        if close <= lower:
            return 1.0
        if lower <= 0:
            return 0.0
        distance = (close - lower) / lower
        if distance > p.bb_proximity_pct:
            return 0.0
        return float(1.0 - distance / p.bb_proximity_pct)

    def _fib_score(self, df: pd.DataFrame) -> float:
        """Fib 0.382~0.618 zone 안 → [0, 1]. 0.5 중심 가까울수록 높음."""
        p = self.params
        recent = df.tail(p.fib_lookback)
        high = recent["high"].max()
        low = recent["low"].min()
        if high == low:
            return 0.0
        close = df["close"].iloc[-1]
        retrace = (close - low) / (high - low)
        if retrace < p.fib_min or retrace > p.fib_max:
            return 0.0
        center = (p.fib_min + p.fib_max) / 2
        max_distance = (p.fib_max - p.fib_min) / 2
        distance = abs(retrace - center)
        return float(1.0 - distance / max_distance)

    def _rsi_score(self, df: pd.DataFrame) -> float:
        """RSI(14) — 최근 10봉 중 oversold(≤30) 진입 후 현재 ≥40 회복 시 점수."""
        p = self.params
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1.0 / p.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / p.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)

        rsi_now = rsi.iloc[-1]
        if pd.isna(rsi_now):
            return 0.0
        rsi_now = float(rsi_now)

        rsi_min_recent = rsi.tail(10).min()
        if pd.isna(rsi_min_recent):
            return 0.0
        rsi_min_recent = float(rsi_min_recent)

        if rsi_min_recent > p.rsi_oversold:
            return 0.0  # 과매도 진입 안 됨
        if rsi_now < p.rsi_recovery:
            return 0.0  # 아직 회복 안 됨
        # 회복 강도 — recovery 도달 직후 가장 높음
        return float(min(1.0, (rsi_now - p.rsi_oversold) / (p.rsi_recovery - p.rsi_oversold)))

    @staticmethod
    def _to_dataframe(candles: List[OHLCV]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "open": [c.open for c in candles],
                "high": [c.high for c in candles],
                "low": [c.low for c in candles],
                "close": [c.close for c in candles],
                "volume": [c.volume for c in candles],
            }
        )

    # === Strategy v2 override ===

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """골드존 보수적 정책: TP1=+2% (50%), TP2=+4% (50%), SL=-1.5%, breakeven=+1.0%."""
        avg = Decimal(str(position.avg_price))
        return ExitPlan(
            take_profits=[
                TakeProfitTier(
                    price=avg * Decimal("1.02"),
                    qty_pct=Decimal("0.5"),
                    condition="골드존 TP1 +2%",
                ),
                TakeProfitTier(
                    price=avg * Decimal("1.04"),
                    qty_pct=Decimal("0.5"),
                    condition="골드존 TP2 +4%",
                ),
            ],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
            time_exit=dtime(14, 50) if ctx.market_type == MarketType.STOCK else None,
            breakeven_trigger=Decimal("0.01"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """골드존 보수적: ≥0.7 → 25%, 0.5~0.7 → 15%, <0.5 → 8%."""
        if account.available <= 0:
            return Decimal(0)

        score = Decimal(str(signal.score))
        if score >= Decimal("0.7"):
            ratio = Decimal("0.25")
        elif score >= Decimal("0.5"):
            ratio = Decimal("0.15")
        else:
            ratio = Decimal("0.08")

        max_invest = account.available * ratio
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        """골드존 health_check — BB period ≥20, RSI period ≥14."""
        p = self.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": p.bb_period >= 20 and p.rsi_period >= 14 and p.min_candles >= 60,
            "bb_period": p.bb_period,
            "rsi_period": p.rsi_period,
            "min_candles": p.min_candles,
        }


__all__ = ["GoldZoneStrategy", "GoldZoneParams"]
