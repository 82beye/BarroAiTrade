---
tags: [design, feature/bar-48, status/in_progress, phase/1, area/strategy]
template: design
version: 1.0
---

# BAR-48 골드존 신규 Design Document

> **관련 문서**: [[../../01-plan/features/bar-48-gold-zone.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Date**: 2026-05-06 / **Status**: Draft

---

## 1. Implementation Spec

### 1.1 GoldZoneStrategy 클래스

```python
# backend/core/strategy/gold_zone.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from decimal import Decimal
from typing import Any, Optional

import numpy as np
import pandas as pd

from backend.core.strategy.base import Strategy
from backend.models.market import MarketType, OHLCV
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account, AnalysisContext, ExitPlan, StopLoss, TakeProfitTier,
)


@dataclass
class GoldZoneParams:
    bb_period: int = 20
    bb_std: float = 2.0
    fib_lookback: int = 30
    fib_min: float = 0.382
    fib_max: float = 0.618
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_recovery: float = 40.0
    bb_proximity_pct: float = 0.01   # BB 하단 1% 이내
    min_candles: int = 60


class GoldZoneStrategy(Strategy):
    """골드존 — BB 하단 + Fib 0.382~0.618 + RSI 30→40 회복."""

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
            signal_type="blue_line",   # 5 enum 제약 — gold_zone subtype 은 metadata 보존
            score=round(score, 2),
            reason=f"골드존: BB하단 + Fib({fib_score:.2f}) + RSI 회복",
            market_type=ctx.market_type,
            strategy_id=self.STRATEGY_ID,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "gold_zone_subtype": "gold_zone",
                "bb_score": round(bb_score, 3),
                "fib_score": round(fib_score, 3),
                "rsi_score": round(rsi_score, 3),
            },
        )

    # === BB / Fib / RSI 계산 ===

    def _bb_score(self, df: pd.DataFrame) -> float:
        """BB 하단 1% 이내 → 1.0, 더 가까울수록 높음."""
        p = self.params
        sma = df["close"].rolling(p.bb_period).mean().iloc[-1]
        std = df["close"].rolling(p.bb_period).std().iloc[-1]
        lower = sma - p.bb_std * std
        close = df["close"].iloc[-1]
        if close <= lower:
            return 1.0
        distance = (close - lower) / lower
        if distance > p.bb_proximity_pct:
            return 0.0
        return float(1.0 - distance / p.bb_proximity_pct)

    def _fib_score(self, df: pd.DataFrame) -> float:
        """Fib 0.382~0.618 zone 안 → 1.0, 0.5 중심 가까울수록 높음."""
        p = self.params
        recent = df.tail(p.fib_lookback)
        high = recent["high"].max()
        low = recent["low"].min()
        if high == low:
            return 0.0
        close = df["close"].iloc[-1]
        # 되돌림 비율 (저점 대비 회복 정도)
        retrace = (close - low) / (high - low)
        if retrace < p.fib_min or retrace > p.fib_max:
            return 0.0
        # 0.5 중심 가까울수록 점수 높음 (zone 중앙)
        center = (p.fib_min + p.fib_max) / 2
        distance = abs(retrace - center)
        max_distance = (p.fib_max - p.fib_min) / 2
        return float(1.0 - distance / max_distance)

    def _rsi_score(self, df: pd.DataFrame) -> float:
        """RSI(14) 30 이하에서 40 돌파 회복 시 1.0."""
        p = self.params
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1.0 / p.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / p.rsi_period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - 100 / (1 + rs)
        rsi_now = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0
        rsi_min = float(rsi.tail(10).min()) if not pd.isna(rsi.tail(10).min()) else 50.0

        if rsi_min > p.rsi_oversold or rsi_now < p.rsi_oversold:
            return 0.0  # 과매도 진입 안 됨 또는 아직 회복 안 됨
        if rsi_now < p.rsi_recovery:
            return 0.0
        # 회복 강도 — rsi_recovery 도달 직후 가장 높음
        return float(min(1.0, (rsi_now - p.rsi_oversold) / (p.rsi_recovery - p.rsi_oversold)))

    @staticmethod
    def _to_dataframe(candles: list[OHLCV]) -> pd.DataFrame:
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
        """골드존 보수적: TP1=+2% (50%), TP2=+4% (50%), SL=-1.5%, breakeven=+1.0%."""
        avg = Decimal(str(position.avg_price))
        return ExitPlan(
            take_profits=[
                TakeProfitTier(price=avg * Decimal("1.02"), qty_pct=Decimal("0.5"), condition="골드존 TP1 +2%"),
                TakeProfitTier(price=avg * Decimal("1.04"), qty_pct=Decimal("0.5"), condition="골드존 TP2 +4%"),
            ],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
            time_exit=dtime(14, 50) if ctx.market_type == MarketType.STOCK else None,
            breakeven_trigger=Decimal("0.01"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """보수적: ≥0.7 → 25%, 0.5~0.7 → 15%, <0.5 → 8%."""
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
        p = self.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": p.bb_period >= 20 and p.rsi_period >= 14 and p.min_candles >= 60,
            "bb_period": p.bb_period,
            "rsi_period": p.rsi_period,
        }
```

---

## 2. Test Cases (`tests/strategy/test_gold_zone.py`, 8+)

```python
class TestGoldZoneStrategy:
    def test_c1_inherits_strategy(self):
        from backend.core.strategy.gold_zone import GoldZoneStrategy
        from backend.core.strategy.base import Strategy
        assert issubclass(GoldZoneStrategy, Strategy)
        assert GoldZoneStrategy.STRATEGY_ID == "gold_zone_v1"

    def test_c2_min_candles_returns_none(self, sample_ctx):
        from backend.core.strategy.gold_zone import GoldZoneStrategy
        assert GoldZoneStrategy()._analyze_v2(sample_ctx) is None  # 5 candles 미달

    def test_c3_oversold_recovery_returns_signal(self):
        """oversold + Fib zone + BB 하단 합성 시나리오 → EntrySignal."""
        # 60+ candle 합성 — 하락 후 회복
        ...


class TestGoldZoneExitPlan:
    def test_c4_exit_plan(self, sample_position, sample_ctx):
        from backend.core.strategy.gold_zone import GoldZoneStrategy
        plan = GoldZoneStrategy().exit_plan(sample_position, sample_ctx)
        assert len(plan.take_profits) == 2
        assert plan.take_profits[0].price == Decimal("72000") * Decimal("1.02")
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")
        assert plan.time_exit == dtime(14, 50)


class TestGoldZonePositionSize:
    def _account(self):
        return Account(balance=Decimal("10000000"), available=Decimal("10000000"), position_count=0)

    def test_c5a_high_25pct(self, sample_signal_high_score):
        # 10M * 0.25 / 72000 = 34.72 → 35
        assert GoldZoneStrategy().position_size(sample_signal_high_score, self._account()) == Decimal("35")

    def test_c5b_mid_15pct(self, sample_signal_mid_score):
        # 10M * 0.15 / 72000 = 20.83 → 21
        assert GoldZoneStrategy().position_size(sample_signal_mid_score, self._account()) == Decimal("21")

    def test_c5c_low_8pct(self, sample_signal_low_score):
        # 10M * 0.08 / 72000 = 11.11 → 11
        assert GoldZoneStrategy().position_size(sample_signal_low_score, self._account()) == Decimal("11")


class TestGoldZoneHealthCheck:
    def test_c6_ready(self):
        from backend.core.strategy.gold_zone import GoldZoneStrategy
        h = GoldZoneStrategy().health_check()
        assert h["strategy_id"] == "gold_zone_v1"
        assert h["ready"] is True


class TestGoldZoneBaselineRegression:
    def test_c7_baseline_unchanged(self):
        import sys
        sys.path.insert(0, ".")
        from run_baseline import run_baseline
        reports = run_baseline(seed=42, num_candles=250)
        # F존 베이스라인 보존 (골드존은 별도 strategy 라 영향 0)
        assert len(reports["f_zone_v1"].trades) == 6
```

---

## 3. Verification (V1~V6)

| # | 시나리오 |
|---|---|
| V1 | make test-strategy 통과 |
| V2 | cov ≥ 80% |
| V3 | BAR-44 베이스라인 4 전략 ±5% |
| V4 | BAR-40~47 회귀 무영향 |
| V5 | exit_plan TP qty_pct 합 1.0 |
| V6 | metadata.gold_zone_subtype 보존 |

---

## 4. Implementation Checklist (D1~D7)

- [ ] D1 — gold_zone.py 작성 (계산 helper + _analyze_v2 + 3 override)
- [ ] D2 — test_gold_zone.py 8+
- [ ] D3 — V1~V6
- [ ] D4 — PR

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — BB+Fib+RSI 가중합, 보수적 ExitPlan |
