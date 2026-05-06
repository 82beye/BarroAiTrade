---
tags: [design, feature/bar-47, status/in_progress, phase/1, area/strategy]
template: design
version: 1.0
---

# BAR-47 SF존 별도 클래스 분리 Design Document

> **관련 문서**: [[../../01-plan/features/bar-47-sf-zone-split.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Date**: 2026-05-06 / **Status**: Draft

---

## 1. Implementation Spec

### 1.1 SFZoneStrategy 클래스 (옵션 A — Delegate)

```python
# backend/core/strategy/sf_zone.py
from __future__ import annotations

from datetime import time as dtime
from decimal import Decimal
from typing import Any, Optional

from backend.core.strategy.base import Strategy
from backend.core.strategy.f_zone import FZoneStrategy, FZoneParams
from backend.models.market import MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account,
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)


class SFZoneStrategy(Strategy):
    """SF존 (슈퍼존) 전략 — F존 + 추가 강도 (거래량 재증가, 강한 기준봉, 테마 연속성)."""

    STRATEGY_ID = "sf_zone_v1"

    def __init__(self, params: Optional[FZoneParams] = None) -> None:
        # FZoneStrategy 인스턴스 보유 (delegate)
        self._inner = FZoneStrategy(params=params)

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        """F존 분석 후 sf_zone 신호만 통과."""
        signal = self._inner._analyze_v2(ctx)
        if signal is None:
            return None
        if signal.signal_type != "sf_zone":
            return None
        # SF존 strategy_id 로 재라벨
        return signal.model_copy(update={"strategy_id": self.STRATEGY_ID})

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """SF존 정책: TP1=+3% (33%) + TP2=+5% (33%) + TP3=+7% (34%) + SL=-1.5% + breakeven +1.0%."""
        avg = Decimal(str(position.avg_price))

        take_profits = [
            TakeProfitTier(price=avg * Decimal("1.03"), qty_pct=Decimal("0.33"), condition="SF존 TP1 +3%"),
            TakeProfitTier(price=avg * Decimal("1.05"), qty_pct=Decimal("0.33"), condition="SF존 TP2 +5%"),
            TakeProfitTier(price=avg * Decimal("1.07"), qty_pct=Decimal("0.34"), condition="SF존 TP3 +7%"),
        ]

        time_exit = dtime(14, 50) if ctx.market_type == MarketType.STOCK else None

        return ExitPlan(
            take_profits=take_profits,
            stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
            time_exit=time_exit,
            breakeven_trigger=Decimal("0.01"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """SF존 강도(score) 기반 비중: ≥0.7 → 35%, 0.5~0.7 → 25%, <0.5 → 10%."""
        if account.available <= 0:
            return Decimal(0)
        score = Decimal(str(signal.score))
        if score >= Decimal("0.7"):
            ratio = Decimal("0.35")
        elif score >= Decimal("0.5"):
            ratio = Decimal("0.25")
        else:
            ratio = Decimal("0.10")
        max_invest = account.available * ratio
        price = Decimal(str(signal.price))
        if price <= 0:
            return Decimal(0)
        return (max_invest / price).quantize(Decimal("1"))

    def health_check(self) -> dict[str, Any]:
        inner_h = self._inner.health_check()
        p = self._inner.params
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": inner_h["ready"] and p.sf_impulse_min_gain_pct >= 0.05,
            "inner_ready": inner_h["ready"],
            "sf_impulse_min_gain_pct": p.sf_impulse_min_gain_pct,
        }
```

---

## 2. Test Cases (`backend/tests/strategy/test_sf_zone.py`, 8+)

```python
class TestSFZoneStrategy:
    def test_c1_inherits_strategy(self):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        from backend.core.strategy.base import Strategy
        assert issubclass(SFZoneStrategy, Strategy)

    def test_c2_min_candles_returns_none(self, sample_ctx):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        assert s._analyze_v2(sample_ctx) is None  # 5 candles 미달

    def test_filters_non_sf_signal(self, monkeypatch, sample_ctx, sample_signal):
        """C3: F존 신호는 None 반환 (SF존 만 통과)."""
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        # F존 (sf_zone 아님) 모킹
        f_signal = sample_signal.model_copy(update={"signal_type": "f_zone"})
        monkeypatch.setattr(s._inner, "_analyze_v2", lambda ctx: f_signal)
        assert s._analyze_v2(sample_ctx) is None

    def test_passes_sf_signal(self, monkeypatch, sample_ctx, sample_signal):
        """sf_zone 신호 통과 + strategy_id 재라벨."""
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        sf_signal = sample_signal.model_copy(update={"signal_type": "sf_zone"})
        monkeypatch.setattr(s._inner, "_analyze_v2", lambda ctx: sf_signal)
        result = s._analyze_v2(sample_ctx)
        assert result is not None
        assert result.signal_type == "sf_zone"
        assert result.strategy_id == "sf_zone_v1"


class TestSFZoneExitPlan:
    def test_c4_three_take_profits(self, sample_position, sample_ctx):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)
        assert len(plan.take_profits) == 3
        # qty_pct 합계 = 0.33+0.33+0.34 = 1.0
        assert sum(t.qty_pct for t in plan.take_profits) == Decimal("1.00")
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")
        assert plan.breakeven_trigger == Decimal("0.01")
        assert plan.time_exit == dtime(14, 50)

    def test_exit_plan_crypto_no_time_exit(self, sample_position, sample_ctx_crypto):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx_crypto)
        assert plan.time_exit is None


class TestSFZonePositionSize:
    def _account(self):
        return Account(balance=Decimal("10000000"), available=Decimal("10000000"), position_count=0)

    def test_c5_high_score_35pct(self, sample_signal_high_score):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        size = s.position_size(sample_signal_high_score, self._account())
        # 10M * 0.35 / 72000 = 48.61 → quantize ROUND_HALF_EVEN → 49
        assert size == Decimal("49")

    def test_c5_mid_score_25pct(self, sample_signal_mid_score):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        size = s.position_size(sample_signal_mid_score, self._account())
        # 10M * 0.25 / 72000 = 34.72 → 35
        assert size == Decimal("35")

    def test_c5_low_score_10pct(self, sample_signal_low_score):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        size = s.position_size(sample_signal_low_score, self._account())
        # 10M * 0.1 / 72000 = 13.89 → 14
        assert size == Decimal("14")


class TestSFZoneHealthCheck:
    def test_c6_health_check(self):
        from backend.core.strategy.sf_zone import SFZoneStrategy
        s = SFZoneStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "sf_zone_v1"
        assert h["ready"] is True


class TestSFZoneBaselineRegression:
    def test_c7_baseline_unchanged(self):
        """BAR-44 F존 베이스라인 회귀 (SF존은 0 거래라 기존 베이스라인 변동 없음)."""
        import sys
        sys.path.insert(0, ".")
        from run_baseline import run_baseline
        reports = run_baseline(seed=42, num_candles=250)
        f = reports["f_zone_v1"]
        # F존 베이스라인 보존
        assert len(f.trades) == 6
```

---

## 3. Verification

| # | 시나리오 |
|---|---|
| V1 | make test-strategy 통과 (이전 41 + 신규 8) |
| V2 | 라인 커버리지 ≥ 80% |
| V3 | BAR-44 F존 6 거래 보존 |
| V4 | BAR-40~46 회귀 무영향 |
| V5 | exit_plan qty_pct 합계 1.0 |
| V6 | strategy_id="sf_zone_v1" 재라벨 |

---

## 4. Implementation Checklist (D1~D7)

- [ ] D1 — sf_zone.py 신규 (delegate 옵션 A)
- [ ] D2 — exit_plan/position_size/health_check override
- [ ] D3 — test_sf_zone.py 8+
- [ ] D4 — V1~V6 검증
- [ ] D5 — PR

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — delegate 옵션 A, 8+ 테스트 |
