---
tags: [design, feature/bar-44, status/in_progress, phase/0, area/strategy]
template: design
version: 1.0
---

# BAR-44 회귀 베이스라인 + 마스터 플랜 v2 Design Document

> **관련 문서**: [[../../01-plan/features/bar-44-baseline.plan|Plan]] | [[../../01-plan/MASTER-EXECUTION-PLAN-v1|Master Plan v1]]

> **Summary**: 옵션 2 (합성 데이터 베이스라인) 구체화 — `SyntheticDataLoader` + `run_multi_strategy_backtest` 활용. 4 전략 × 250일, fixed seed=42. 마스터 플랜 v2 변경 매트릭스 + Phase 0 회고 항목 정의
>
> **Project**: BarroAiTrade
> **Feature**: BAR-44
> **Phase**: 0 (종료 게이트)
> **Date**: 2026-05-06
> **Status**: Draft

---

## 1. Architecture

### 1.1 베이스라인 측정 흐름

```
SyntheticDataLoader.generate(
    symbol="TEST",
    market_type=MarketType.STOCK,
    num_candles=250,
    seed=42,
)
   ↓ List[OHLCV]
run_multi_strategy_backtest(
    strategies=[FZoneStrategy(), BlueLineStrategy(),
                StockStrategy(), CryptoBreakoutStrategy()],
    candles=...,
    symbol="TEST",
    market_type=MarketType.STOCK,
)
   ↓ Dict[strategy_id, BacktestReport]
docs/04-report/PHASE-0-baseline-2026-05.md (4 전략 표)
```

### 1.2 모듈 위치

```
scripts/run_baseline.py             ← 신규 (실행 스크립트, ~80 LOC)
backend/tests/strategy/             ← 신규 디렉터리 (BAR-44 시동)
├── __init__.py
└── test_baseline.py                ← 재현성 검증 3+ 케이스
docs/04-report/
├── PHASE-0-baseline-2026-05.md     ← 4 전략 표 + 회귀 임계값
├── PHASE-0-summary.md              ← 26 PR / 5 BAR 회고
└── ...
docs/01-plan/
└── MASTER-EXECUTION-PLAN-v2.md     ← 신규 (v1 supersede)
```

### 1.3 4 전략 매핑

| Strategy | 위치 | STRATEGY_ID |
|---|---|---|
| FZoneStrategy | `backend/core/strategy/f_zone.py` | `f_zone` |
| BlueLineStrategy | `backend/core/strategy/blue_line.py` | `blue_line` |
| StockStrategy (수박) | `backend/core/strategy/stock_strategy.py` | (확인) |
| CryptoBreakoutStrategy | `backend/core/strategy/crypto_breakout.py` | `crypto_breakout` |

본 design 단계에서 `STRATEGY_ID` 를 do 시 import 후 정확히 확인.

---

## 2. Implementation Spec

### 2.1 `scripts/run_baseline.py` 골격

```python
"""BAR-44: 4 전략 합성 베이스라인 측정."""
from backend.core.strategy.backtester import (
    SyntheticDataLoader,
    run_multi_strategy_backtest,
    BacktestConfig,
)
from backend.core.strategy.f_zone import FZoneStrategy
from backend.core.strategy.blue_line import BlueLineStrategy
from backend.core.strategy.stock_strategy import StockStrategy  # 또는 정확한 import
from backend.core.strategy.crypto_breakout import CryptoBreakoutStrategy
from backend.models.market import MarketType


def run_baseline(seed: int = 42, num_candles: int = 250) -> dict:
    candles = SyntheticDataLoader.generate(
        symbol="TEST",
        market_type=MarketType.STOCK,
        num_candles=num_candles,
        seed=seed,
    )

    strategies = [
        FZoneStrategy(),
        BlueLineStrategy(),
        StockStrategy(),
        CryptoBreakoutStrategy(),
    ]

    reports = run_multi_strategy_backtest(
        strategies=strategies,
        candles=candles,
        symbol="TEST",
        market_type=MarketType.STOCK,
    )
    return reports


if __name__ == "__main__":
    reports = run_baseline()
    for sid, r in reports.items():
        m = r.metrics
        print(f"{sid}: 거래={len(r.trades)}, 승률={m.win_rate:.2%}, "
              f"수익={m.total_return_pct:.2%}, MDD={m.max_drawdown:.2%}")
```

### 2.2 마스터 플랜 v2 발행

`docs/01-plan/MASTER-EXECUTION-PLAN-v2.md` 신규.
헤더에 *"v1 supersede, BAR-51 → BAR-79 재할당"* 명시.
v1 파일은 *보존* (역사 추적). `_index.md` 의 v1 표기를 ✅ 완료, v2 가 🚧 진행으로 갱신.

변경 매트릭스 (Plan §6.2):

| # | 항목 | v1 | v2 |
|---|------|-----|-----|
| 1 | BAR-51 (Phase 1) | 백테스터 v2 확장 | 🔁 BAR-79 로 재할당 (Phase 6 마지막 묶음) |
| 2 | zero-modification 정의 (BAR-40 §3.3) | "코드 무수정" | "외부 동작 보존, 진입점 격리만" |
| 3 | `_adapter.py` LOC (BAR-41 Plan §4.3) | ≤ 200 | ≤ 250 |
| 4 | `LegacySignalSchema.extra` (BAR-41 Design §3.1) | `forbid` | `ignore` |
| 5 | metrics fixture (BAR-43 Design §3.3) | `importlib.reload` | Singleton (reload 제거) |
| 6 | fallback 검증 정책 | env 종속 | `PROM_FORCE_NOOP=1` 권고 |
| 7 | NFR 성능 측정 위치 (BAR-42/43) | "선택" 표기 | BAR-44 베이스라인에 통합 명시 |
| 8 | BAR-44b 신설 | (부재) | 후순위 (정식 5년 OHLCV 백테스트, Postgres 마이그 후) |
| 9 | 운영 원칙 §0 | (없음) | "단순 docs PR 묶기 검토 (BAR-78 회귀 자동화 시)" 추가 |

### 2.3 Phase 0 종합 회고 (`docs/04-report/PHASE-0-summary.md`)

- **5 BAR × 5 PDCA PR + 거버넌스 1 + 본 Phase 0 종료 1 = 27 PR** (예상)
- Match Rate: BAR-40 95% / BAR-41 96% / BAR-42 98% / BAR-43 97% / BAR-44 (예상 95+%) → 평균 96%
- 총 신규 LOC: 코드 ~600 + 테스트 ~700 + 문서 ~3,500
- 후속 BAR 의존 해소: 15+ BAR (BAR-45/50/52/53/55/56/57/58/59/63/64/66/67/68/69 ...)
- Lessons (통합):
  - Zero-modification 일관 적용 (BAR-40/41/43)
  - gap-detector 우회 정책 (단순 인프라 ticket)
  - prometheus_client REGISTRY Singleton
  - extra="ignore" + TestEnvExampleConsistency drift 방지
  - SecretStr 옵션 C (BAR-67 인계)

### 2.4 6+ 테스트 (`tests/strategy/test_baseline.py`)

| # | 케이스 |
|---|--------|
| C1 | `run_baseline(seed=42)` 무에러 + 4 전략 결과 dict 반환 |
| C2 | 동일 seed 두 번 호출 → 결과 동일 (재현성) |
| C3 | 다른 seed → 다른 결과 (확률성 작동) |
| C4 | 각 전략 결과의 `metrics.total_return_pct`, `win_rate`, `max_drawdown` 필드 존재 |
| C5 | 거래 0건 전략 fallback (skip 또는 0 값 처리) |
| C6 | num_candles=50 (최소 데이터) → 무에러 |

---

## 3. Verification Scenarios

| # | 시나리오 |
|---|---|
| V1 | `python scripts/run_baseline.py` 실행 → 4 전략 결과 출력 (≤ 30초) |
| V2 | `make test-baseline` (또는 통합 `make test`) 6+ 케이스 통과 |
| V3 | BAR-40/41/42/43 회귀 무영향 |
| V4 | 라인 커버리지 ≥ 80% (`tests/strategy/`) |
| V5 | 동일 seed 시 베이스라인 표 셀 값 ±0.001% 이내 동일 |
| V6 | `PHASE-0-baseline-2026-05.md` 생성 + wikilink 검증 |

---

## 4. Implementation Checklist (D1~D10)

- [ ] D1 — backtester import 무에러 확인 (numpy/pandas .venv 의존성)
- [ ] D2 — 4 전략 STRATEGY_ID 정확 확인 (실 import)
- [ ] D3 — `scripts/run_baseline.py` 작성 + 실행
- [ ] D4 — 결과를 표로 정리 → `PHASE-0-baseline-2026-05.md`
- [ ] D5 — `tests/strategy/test_baseline.py` 6+ 케이스
- [ ] D6 — 마스터 플랜 v2 작성 (`MASTER-EXECUTION-PLAN-v2.md`) + v1 보존
- [ ] D7 — Phase 0 종합 회고 (`PHASE-0-summary.md`)
- [ ] D8 — `Makefile` `test-baseline` 또는 `test` 통합
- [ ] D9 — V1~V6 검증
- [ ] D10 — PR 생성 (라벨: `area:strategy` `phase:0` `priority:p0`)

---

## 5. Version History

| Version | Date | Changes |
|---|---|---|
| 0.1 | 2026-05-06 | 초기 design — backtester API, v2 변경 매트릭스, Phase 0 회고 항목, 6+ 테스트 |
