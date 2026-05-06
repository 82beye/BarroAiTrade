---
tags: [design, feature/bar-49, status/in_progress, phase/1, area/strategy]
template: design
version: 1.0
---

# BAR-49 38스윙 신규 Design Document

> **관련 문서**: [[../../01-plan/features/bar-49-swing-38.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Date**: 2026-05-06 / **Status**: Draft

---

## 1. Implementation Spec (`backend/core/strategy/swing_38.py`)

### 1.1 Swing38Params

```python
@dataclass
class Swing38Params:
    impulse_lookback: int = 30           # 임펄스 탐색 과거 봉
    impulse_min_gain_pct: float = 0.05   # 임펄스 최소 상승률 5%
    impulse_volume_ratio: float = 2.0    # 거래량 평균 2x
    fib_target: float = 0.382            # Fib 되돌림 타겟
    fib_tolerance: float = 0.075         # ±7.5% 허용
    bounce_lookback: int = 5             # 반등 캔들 탐색 봉
    min_candles: int = 60
```

### 1.2 _analyze_v2 (3 단계 순차)

```python
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
        signal_type="blue_line",
        score=round(float(score), 2),
        reason=f"38스윙: 임펄스 {impulse['gain_pct']*100:.1f}% + Fib0.382 + 반등",
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
```

### 1.3 helper

```python
def _detect_impulse(self, df: pd.DataFrame) -> Optional[dict]:
    """최근 lookback 봉 내 +5% 이상 + 거래량 2x 양봉 탐색."""
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
            "idx": int(recent.index[i]),
        }
    return None


def _fib_score(self, df: pd.DataFrame, impulse: dict) -> float:
    """임펄스 고점-저점 기준 0.382 ± 7.5% zone 안 → [0, 1]."""
    p = self.params
    high, low = impulse["high"], impulse["low"]
    if high == low:
        return 0.0
    close = float(df["close"].iloc[-1])
    # 되돌림 비율: 고점 대비 하락 정도
    retrace = (high - close) / (high - low)
    distance = abs(retrace - p.fib_target)
    if distance > p.fib_tolerance:
        return 0.0
    return float(1.0 - distance / p.fib_tolerance)


def _bounce_score(self, df: pd.DataFrame) -> float:
    """최근 봉 양봉 + 마감 강도 → [0, 1]."""
    p = self.params
    recent = df.tail(p.bounce_lookback)
    last = recent.iloc[-1]
    if last["close"] <= last["open"]:
        return 0.0
    body = (last["close"] - last["open"]) / last["open"]
    return float(min(1.0, body / 0.02))  # +2% 양봉 → 1.0
```

### 1.4 Override

```python
def exit_plan(self, position, ctx) -> ExitPlan:
    avg = Decimal(str(position.avg_price))
    return ExitPlan(
        take_profits=[
            TakeProfitTier(price=avg * Decimal("1.025"), qty_pct=Decimal("0.5"), condition="38스윙 TP1 +2.5%"),
            TakeProfitTier(price=avg * Decimal("1.05"), qty_pct=Decimal("0.5"), condition="38스윙 TP2 +5%"),
        ],
        stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
        time_exit=dtime(14, 50) if ctx.market_type == MarketType.STOCK else None,
        breakeven_trigger=Decimal("0.012"),
    )

def position_size(self, signal, account) -> Decimal:
    if account.available <= 0: return Decimal(0)
    score = Decimal(str(signal.score))
    if score >= Decimal("0.7"): ratio = Decimal("0.28")
    elif score >= Decimal("0.5"): ratio = Decimal("0.18")
    else: ratio = Decimal("0.08")
    max_invest = account.available * ratio
    price = Decimal(str(signal.price))
    if price <= 0: return Decimal(0)
    return (max_invest / price).quantize(Decimal("1"))

def health_check(self) -> dict:
    p = self.params
    return {
        "strategy_id": self.STRATEGY_ID,
        "ready": p.impulse_min_gain_pct >= 0.05 and p.min_candles >= 60,
        "impulse_min_gain_pct": p.impulse_min_gain_pct,
        "fib_target": p.fib_target,
    }
```

---

## 2. Test Cases (`tests/strategy/test_swing_38.py`, 8+)

| # | 케이스 |
|---|---|
| C1 | Strategy 상속 |
| C2 | min_candles 미달 None |
| C3 | 합성 임펄스 + 되돌림 시나리오 |
| C4 | exit_plan TP1=+2.5%, TP2=+5%, SL=-1.5%, breakeven=+1.2% |
| C5 | position_size 28%/18%/8% |
| C6 | health_check ready |
| C7 | BAR-44 베이스라인 보존 |
| C8 | crypto time_exit None |

---

## 3. Verification (V1~V6)

| # | 시나리오 |
|---|---|
| V1 | make test-strategy 통과 |
| V2 | cov ≥ 80% |
| V3 | BAR-44 베이스라인 (F존 6 / BlueLine 12) |
| V4 | BAR-40~48 회귀 |
| V5 | exit_plan qty 합 1.0 |
| V6 | metadata.swing_38_subtype |

---

## 4. Implementation Checklist (D1~D5)

1. D1 — swing_38.py
2. D2 — test_swing_38.py 8+
3. D3 — V1~V6
4. D4 — PR

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — 임펄스+Fib+반등 가중합, 8+ 테스트 |
