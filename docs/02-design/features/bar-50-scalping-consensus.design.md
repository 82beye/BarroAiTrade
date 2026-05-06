---
tags: [design, feature/bar-50, status/in_progress, phase/1, area/strategy]
template: design
version: 1.0
---

# BAR-50 ScalpingConsensus Design Document

> **관련 문서**: [[../../01-plan/features/bar-50-scalping-consensus.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Date**: 2026-05-06 / **Status**: Draft

---

## 1. Implementation Spec

### 1.1 ScalpingConsensusStrategy 클래스 (옵션 B)

```python
# backend/core/strategy/scalping_consensus.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from decimal import Decimal
from typing import Any, Callable, Optional

from backend.core.strategy.base import Strategy
from backend.legacy_scalping import to_entry_signal
from backend.models.market import MarketType
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import (
    Account, AnalysisContext, ExitPlan, StopLoss, TakeProfitTier,
)


@dataclass
class ScalpingConsensusParams:
    threshold: float = 0.65   # score ≥ threshold 만 통과
    consensus_min: float = 0.5  # 합의 강도 임계 (옵션)


# 분석 결과 provider 타입
AnalysisProvider = Callable[[AnalysisContext], Optional[Any]]


class ScalpingConsensusStrategy(Strategy):
    """12 legacy_scalping 에이전트 가중합 — provider injection 패턴."""

    STRATEGY_ID = "scalping_consensus_v1"

    def __init__(self, params: Optional[ScalpingConsensusParams] = None) -> None:
        self.params = params or ScalpingConsensusParams()
        self._provider: Optional[AnalysisProvider] = None

    def set_analysis_provider(self, provider: AnalysisProvider) -> None:
        """ScalpingAnalysis 또는 dict 를 반환하는 콜러블 등록.

        외부 (legacy ScalpingCoordinator wrapper, 모킹) 가 ctx 받아 분석 결과 반환.
        """
        self._provider = provider

    def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
        if self._provider is None:
            return None  # provider 미등록 시 silent None

        legacy_data = self._provider(ctx)
        if legacy_data is None:
            return None

        try:
            signal = to_entry_signal(legacy_data, fallback_market_type=ctx.market_type)
        except (TypeError, ValueError):
            return None  # adapter 실패 시 silent None

        if signal.score < self.params.threshold:
            return None  # threshold 0.65 미달

        return signal.model_copy(update={"strategy_id": self.STRATEGY_ID})

    def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
        """단타: TP1=+1.5% (50%), TP2=+3% (50%), SL=-1%, breakeven=+0.5%."""
        avg = Decimal(str(position.avg_price))
        return ExitPlan(
            take_profits=[
                TakeProfitTier(price=avg * Decimal("1.015"), qty_pct=Decimal("0.5"), condition="단타 TP1 +1.5%"),
                TakeProfitTier(price=avg * Decimal("1.03"), qty_pct=Decimal("0.5"), condition="단타 TP2 +3%"),
            ],
            stop_loss=StopLoss(fixed_pct=Decimal("-0.01")),
            time_exit=dtime(14, 50) if ctx.market_type == MarketType.STOCK else None,
            breakeven_trigger=Decimal("0.005"),
        )

    def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
        """단타 보수적: ≥0.7 → 25%, 0.5~0.7 → 15%, <0.5 → 8%.

        threshold 0.65 가 진입 자체 차단하므로 실질 진입은 score ≥ 0.65 인 케이스만 (대부분 25% 분기).
        """
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
        return {
            "strategy_id": self.STRATEGY_ID,
            "ready": self._provider is not None,
            "provider_registered": self._provider is not None,
            "threshold": self.params.threshold,
        }


__all__ = ["ScalpingConsensusStrategy", "ScalpingConsensusParams"]
```

### 1.2 BAR-41 어댑터 활용

`to_entry_signal(legacy_data)` 가 dict / ScalpingAnalysis 모두 처리. 본 Strategy 가 *얇은 wrapper* 로서 threshold 만 적용.

---

## 2. Test Cases (`tests/strategy/test_scalping_consensus.py`, 8+)

```python
class TestScalpingConsensusStrategy:
    def test_c1_inherits(self):
        assert issubclass(ScalpingConsensusStrategy, Strategy)
        assert ScalpingConsensusStrategy.STRATEGY_ID == "scalping_consensus_v1"

    def test_c2_no_provider_returns_none(self, sample_ctx):
        assert ScalpingConsensusStrategy()._analyze_v2(sample_ctx) is None

    def test_c3_high_score_passes(self, sample_ctx, sample_legacy_dict):
        s = ScalpingConsensusStrategy()
        # total_score=85 → score=0.85 → threshold 0.65 통과
        s.set_analysis_provider(lambda ctx: sample_legacy_dict)
        result = s._analyze_v2(sample_ctx)
        assert result is not None
        assert result.strategy_id == "scalping_consensus_v1"
        assert result.score >= 0.65

    def test_c4_low_score_blocked(self, sample_ctx, sample_legacy_dict):
        s = ScalpingConsensusStrategy()
        # total_score=50 → score=0.5 → threshold 미달
        low = {**sample_legacy_dict, "total_score": 50.0}
        s.set_analysis_provider(lambda ctx: low)
        assert s._analyze_v2(sample_ctx) is None

    def test_provider_returns_none(self, sample_ctx):
        s = ScalpingConsensusStrategy()
        s.set_analysis_provider(lambda ctx: None)
        assert s._analyze_v2(sample_ctx) is None

    def test_provider_invalid_data(self, sample_ctx):
        """provider 잘못된 데이터 → silent None."""
        s = ScalpingConsensusStrategy()
        s.set_analysis_provider(lambda ctx: "not a dict")
        assert s._analyze_v2(sample_ctx) is None


class TestScalpingConsensusExitPlan:
    def test_c5_exit_plan(self, sample_position, sample_ctx):
        plan = ScalpingConsensusStrategy().exit_plan(sample_position, sample_ctx)
        assert plan.take_profits[0].price == Decimal("72000") * Decimal("1.015")
        assert plan.take_profits[1].price == Decimal("72000") * Decimal("1.03")
        assert plan.stop_loss.fixed_pct == Decimal("-0.01")
        assert plan.breakeven_trigger == Decimal("0.005")
        assert plan.time_exit == dtime(14, 50)


class TestScalpingConsensusPositionSize:
    def _account(self):
        return Account(balance=Decimal("10000000"), available=Decimal("10000000"), position_count=0)

    def test_c6_high_25pct(self, sample_signal_high_score):
        # 10M * 0.25 / 72000 = 34.72 → 35
        size = ScalpingConsensusStrategy().position_size(sample_signal_high_score, self._account())
        assert size == Decimal("35")


class TestScalpingConsensusHealthCheck:
    def test_c7_ready_after_provider(self):
        s = ScalpingConsensusStrategy()
        assert s.health_check()["ready"] is False
        s.set_analysis_provider(lambda ctx: None)
        assert s.health_check()["ready"] is True


class TestScalpingConsensusBaseline:
    def test_c8_baseline_unchanged(self):
        import sys
        sys.path.insert(0, ".")
        from run_baseline import run_baseline
        reports = run_baseline(seed=42, num_candles=250)
        assert len(reports["f_zone_v1"].trades) == 6
```

---

## 3. Verification (V1~V6)

| # | 시나리오 |
|---|---|
| V1 | make test-strategy 통과 |
| V2 | cov ≥ 80% |
| V3 | BAR-44 베이스라인 (F존 6 / BlueLine 12) |
| V4 | BAR-40~49 회귀 무영향 |
| V5 | threshold 0.65 차단 동작 |
| V6 | provider injection 동작 |

---

## 4. Implementation Checklist (D1~D5)

1. D1 — scalping_consensus.py
2. D2 — test_scalping_consensus.py 8+
3. D3 — V1~V6
4. D4 — PR + Phase 1 종합 회고

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — 옵션 B provider injection, BAR-41 어댑터 위임 |
