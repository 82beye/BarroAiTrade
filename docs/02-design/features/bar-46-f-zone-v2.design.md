---
tags: [design, feature/bar-46, status/in_progress, phase/1, area/strategy]
template: design
version: 1.0
---

# BAR-46 F존 v2 리팩터 Design Document

> **관련 문서**: [[../../01-plan/features/bar-46-f-zone-v2.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Summary**: F존 v2 직접 구현 + ExitPlan/PositionSize/HealthCheck override. 6+ 테스트 + V1~V6 + D1~D8.
>
> **Date**: 2026-05-06
> **Status**: Draft

---

## 1. Implementation Spec

### 1.1 `_analyze_v2` 직접 구현 (옵션 A)

```python
def _analyze_v2(self, ctx: AnalysisContext) -> Optional[EntrySignal]:
    p = self.params
    if len(ctx.candles) < p.min_candles:
        return None

    df = self._to_dataframe(ctx.candles)
    analysis = FZoneAnalysis(symbol=ctx.symbol, name=ctx.name or ctx.symbol)
    self._detect_impulse(df, analysis)
    self._detect_pullback(df, analysis)
    self._check_ma_support(df, analysis)
    self._detect_bounce(df, analysis)
    self._score_and_classify(analysis)

    if not analysis.passed:
        return None

    current = df.iloc[-1]
    return EntrySignal(
        symbol=ctx.symbol,
        name=ctx.name or ctx.symbol,
        price=float(current["close"]),
        signal_type=analysis.signal_type or "f_zone",
        score=analysis.score,
        reason=analysis.reason,
        market_type=ctx.market_type,
        strategy_id=self.STRATEGY_ID,
        timestamp=datetime.now(timezone.utc),
        metadata={"f_zone_analysis": analysis.to_dict()},
    )
```

`_analyze_impl` 제거 (BAR-45 backward compat shim 제거).

### 1.2 `exit_plan` override

```python
from datetime import time as dtime
from decimal import Decimal

from backend.models.market import MarketType
from backend.models.strategy import ExitPlan, StopLoss, TakeProfitTier


def exit_plan(self, position: Position, ctx: AnalysisContext) -> ExitPlan:
    """F존 정책 (서희파더): TP1=+3% (50%) / TP2=+5% (50%) / SL=-2% / 14:50 강제."""
    avg = Decimal(str(position.avg_price))

    take_profits = [
        TakeProfitTier(
            price=avg * Decimal("1.03"),
            qty_pct=Decimal("0.5"),
            condition="F존 TP1 +3%",
        ),
        TakeProfitTier(
            price=avg * Decimal("1.05"),
            qty_pct=Decimal("0.5"),
            condition="F존 TP2 +5%",
        ),
    ]

    # KRX 정규장 강제 청산 (14:50). crypto 는 None.
    time_exit = dtime(14, 50) if ctx.market_type == MarketType.STOCK else None

    return ExitPlan(
        take_profits=take_profits,
        stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
        time_exit=time_exit,
        breakeven_trigger=Decimal("0.015"),
    )
```

### 1.3 `position_size` override

```python
def position_size(self, signal: EntrySignal, account: Account) -> Decimal:
    """F존 강도(score) 기반 비중: ≥0.7 → 30%, 0.5~0.7 → 20%, <0.5 → 10%."""
    if account.available <= 0:
        return Decimal(0)

    score = Decimal(str(signal.score))
    if score >= Decimal("0.7"):
        ratio = Decimal("0.30")
    elif score >= Decimal("0.5"):
        ratio = Decimal("0.20")
    else:
        ratio = Decimal("0.10")

    max_invest = account.available * ratio
    price = Decimal(str(signal.price))
    if price <= 0:
        return Decimal(0)
    return (max_invest / price).quantize(Decimal("1"))
```

### 1.4 `health_check` override

```python
def health_check(self) -> dict[str, Any]:
    p = self.params
    return {
        "strategy_id": self.STRATEGY_ID,
        "ready": p.min_candles >= 60 and p.impulse_lookback > 0,
        "min_candles": p.min_candles,
        "params": p.__dict__,
    }
```

---

## 2. 6+ Test Cases (`backend/tests/strategy/test_f_zone.py`)

```python
class TestFZoneV2:
    def test_c1_analyze_v2_callable(self, sample_ctx):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        # candles 5 → min_candles 미달 → None 정상
        result = s._analyze_v2(sample_ctx)
        # 5 candles 라 None 또는 EntrySignal 둘 다 정상

    def test_c2_no_legacy_impl(self):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        assert not hasattr(s, "_analyze_impl")  # 제거 확인


class TestFZoneExitPlan:
    def test_c3_exit_plan_stock_with_time_exit(self, sample_position, sample_ctx):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)
        assert len(plan.take_profits) == 2
        assert plan.take_profits[0].qty_pct == Decimal("0.5")
        assert plan.take_profits[1].qty_pct == Decimal("0.5")
        assert plan.stop_loss.fixed_pct == Decimal("-0.02")
        assert plan.time_exit == dtime(14, 50)
        assert plan.breakeven_trigger == Decimal("0.015")

    def test_exit_plan_crypto_no_time_exit(self, sample_position, sample_ctx_crypto):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx_crypto)
        assert plan.time_exit is None


class TestFZonePositionSize:
    def test_c4_high_score_30pct(self, sample_signal_high_score):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        account = Account(balance=Decimal("10000000"), available=Decimal("10000000"), position_count=0)
        size = s.position_size(sample_signal_high_score, account)  # score=0.85 → 30%
        # 10_000_000 * 0.3 / 72000 = 41.67 → 42
        assert size == Decimal("42")

    def test_c5_mid_score_20pct(self, sample_signal_mid_score):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        account = Account(balance=Decimal("10000000"), available=Decimal("10000000"), position_count=0)
        size = s.position_size(sample_signal_mid_score, account)  # score=0.6 → 20%
        # 10M * 0.2 / 72000 = 27.78 → 28
        assert size == Decimal("28")

    def test_c6_low_score_10pct(self, sample_signal_low_score):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        account = Account(balance=Decimal("10000000"), available=Decimal("10000000"), position_count=0)
        size = s.position_size(sample_signal_low_score, account)  # score=0.4 → 10%
        # 10M * 0.1 / 72000 = 13.89 → 14
        assert size == Decimal("14")


class TestFZoneHealthCheck:
    def test_c7_health_check_ready(self):
        from backend.core.strategy.f_zone import FZoneStrategy
        s = FZoneStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "f_zone_v1"
        assert h["ready"] is True


class TestFZoneBaselineRegression:
    def test_c8_baseline_unchanged(self):
        """BAR-44 베이스라인 회귀 ±5% (4 전략 모두 측정, FZone 검증)."""
        import sys
        sys.path.insert(0, ".")
        from run_baseline import run_baseline
        reports = run_baseline(seed=42, num_candles=250)
        f = reports["f_zone_v1"]
        # 베이스라인: 6 거래 / 33.3% / -0.42%
        assert len(f.trades) == 6  # 거래 수 변동 없음
        assert abs(f.metrics.win_rate - 0.3333) < 0.05
        assert abs(f.metrics.total_return_pct - (-0.0042)) < 0.05
```

---

## 3. Verification Scenarios

| # | 시나리오 | 명령 | 기대 |
|---|---|---|---|
| V1 | `make test-strategy` 통과 | exit 0 | 31 + 8+ = 39+ |
| V2 | 라인 커버리지 ≥ 80% | f_zone.py | ≥ 80% |
| V3 | BAR-44 베이스라인 ±5% (FZone 6/33.3%/-0.42%) | `make baseline` | 동일 |
| V4 | BAR-40~45 회귀 무영향 | 73+ 테스트 | green |
| V5 | `_analyze_impl` 부재 | grep | 0 hit |
| V6 | exit_plan 의 Decimal 정확 | C3 | qty_pct 합계 1.0 |

---

## 4. Implementation Checklist (D1~D8)

- [ ] D1 — `_analyze_impl` 호출처 grep + sample fixtures 추가 (high/mid/low score, ctx_crypto)
- [ ] D2 — `_analyze_v2` 직접 구현 + `_analyze_impl` 제거
- [ ] D3 — `exit_plan` override
- [ ] D4 — `position_size` override
- [ ] D5 — `health_check` override
- [ ] D6 — `tests/strategy/test_f_zone.py` 6+ 케이스
- [ ] D7 — V1~V6 검증 (특히 V3 베이스라인)
- [ ] D8 — PR 생성

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — F존 v2 직접 + 정책 매트릭스 + 6+ 테스트 |
