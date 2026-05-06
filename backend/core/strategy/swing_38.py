"""
38스윙 전략 (Swing-38 Strategy) — 임펄스 후 Fib 0.382 되돌림 매수.

진입 조건 (3 단계 순차):
  1. 임펄스 탐지: 최근 lookback 봉 내 gain ≥ 5% + 거래량 평균 2x 이상 양봉
  2. 0.382 되돌림: 임펄스 고점-저점 기준 retrace ≈ 0.382 (±7.5%)
  3. 반등 확인: 직전 양봉 (close > open)

BAR-49: 신규 포팅. F존 (-2~-5% 눌림) 보다 깊은 되돌림 (~-30%) 노리는 스윙 매매.

Reference:
- Plan: docs/01-plan/features/bar-49-swing-38.plan.md
- Design: docs/02-design/features/bar-49-swing-38.design.md
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from decimal import Decimal
from typing import Any, List, Optional

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
class Swing38Params:
    """38스윙 파라미터."""

    impulse_lookback: int = 30
    impulse_min_gain_pct: float = 0.05
    impulse_volume_ratio: float = 2.0
    fib_target: float = 0.382
    fib_tolerance: float = 0.075
    bounce_lookback: int = 5
    min_candles: int = 60


class Swing38Strategy(Strategy):
    """38스윙 — 임펄스 + Fib 0.382 되돌림 + 반등."""

    STRATEGY_ID = "swing_38_v1"

    def __init__(self, params: Optional[Swing38Params] = None) -> None:
        self.params = params or Swing38Params()

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        p = self.params
        if len(ctx.candles) < p.min_candles:
            return None

        df = self._to_dataframe(ctx.candles)

        # 1. 임펄스 탐지
        impulse = self._detect_impulse(df)
        if impulse is None:
            return None

        # 2. Fib 0.382 되돌림 검증
        fib_score = self._fib_score(df, impulse)
        if fib_score == 0:
            return None

        # 3. 반등 확인
        bounce_score = self._bounce_score(df)
        if bounce_score == 0:
            return None

        impulse_score = min(1.0, impulse["gain_pct"] / 0.10)  # 5%~10% 정규화
        score = impulse_score * 0.4 + fib_score * 0.4 + bounce_score * 0.2
        if score < 0.3:
            return None

        return EntrySignal(
            symbol=ctx.symbol,
            name=ctx.name or ctx.symbol,
            price=float(df["close"].iloc[-1]),
            signal_type="blue_line",  # 5 enum 제약
            score=round(float(score), 2),
            reason=(
                f"38스윙: 임펄스 {impulse['gain_pct']*100:.1f}% + "
                f"Fib0.382({fib_score:.2f}) + 반등({bounce_score:.2f})"
            ),
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "swing_38_subtype": "swing_38",
                "impulse_gain_pct": round(impulse["gain_pct"], 4),
                "fib_score": round(float(fib_score), 3),
                "bounce_score": round(float(bounce_score), 3),
            },
        )

    # === 3 단계 helper ===

    def _detect_impulse(self, df: pd.DataFrame) -> Optional[dict]:
        """최근 lookback 봉 내 +gain≥5% + volume≥2x avg 양봉 탐색."""
        p = self.params
        avg_volume = df["volume"].mean()
        if avg_volume == 0:
            return None
        recent = df.tail(p.impulse_lookback)
        for i in range(len(recent) - 1, -1, -1):
            row = recent.iloc[i]
            if row["close"] <= row["open"]:
                continue
            gain = (row["close"] - row["open"]) / row["open"]
            if gain < p.impulse_min_gain_pct:
                continue
            if row["volume"] < p.impulse_volume_ratio * avg_volume:
                continue
            return {
                "high": float(row["high"]),
                "low": float(row["low"]),
                "open": float(row["open"]),
                "close": float(row["close"]),
                "gain_pct": float(gain),
            }
        return None

    def _fib_score(self, df: pd.DataFrame, impulse: dict) -> float:
        """임펄스 고점-저점 기준 0.382 ± tolerance zone → [0, 1]."""
        p = self.params
        high, low = impulse["high"], impulse["low"]
        if high == low:
            return 0.0
        close = float(df["close"].iloc[-1])
        retrace = (high - close) / (high - low)
        distance = abs(retrace - p.fib_target)
        if distance > p.fib_tolerance:
            return 0.0
        return float(1.0 - distance / p.fib_tolerance)

    def _bounce_score(self, df: pd.DataFrame) -> float:
        """직전 봉 양봉 + 마감 강도 → [0, 1]."""
        p = self.params
        recent = df.tail(p.bounce_lookback)
        last = recent.iloc[-1]
        if last["close"] <= last["open"]:
            return 0.0
        body = (last["close"] - last["open"]) / last["open"]
        return float(min(1.0, body / 0.02))  # +2% 양봉 → 1.0

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
        """38스윙: TP1=+2.5% (50%), TP2=+5% (50%), SL=-1.5%, breakeven=+1.2%."""
        avg = Decimal(str(position.avg_price))
        return ExitPlan(
            take_profits=[
                TakeProfitTier(
                    price=avg * Decimal("1.025"),
                    qty_pct=Decimal("0.5"),
                    condition="38스윙 TP1 +2.5%",
                ),
                TakeProfitTier(
                    price=avg * Decimal("1.05"),
                    qty_pct=Decimal("0.5"),
                    condition="38스윙 TP2 +5%",
                ),
            ],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
            time_exit=dtime(14, 50) if ctx.market_type == MarketType.STOCK else None,
            breakeven_trigger=Decimal("0.012"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """38스윙: ≥0.7 → 28%, 0.5~0.7 → 18%, <0.5 → 8%."""
        if account.available <= 0:
            return Decimal(0)

        score = Decimal(str(signal.score))
        if score >= Decimal("0.7"):
            ratio = Decimal("0.28")
        elif score >= Decimal("0.5"):
            ratio = Decimal("0.18")
        else:
            ratio = Decimal("0.08")

        max_invest = account.available * ratio
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        p = self.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": p.impulse_min_gain_pct >= 0.05 and p.min_candles >= 60,
            "impulse_min_gain_pct": p.impulse_min_gain_pct,
            "fib_target": p.fib_target,
            "fib_tolerance": p.fib_tolerance,
        }


__all__ = ["Swing38Strategy", "Swing38Params"]
