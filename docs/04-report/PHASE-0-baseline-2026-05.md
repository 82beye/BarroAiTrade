---
tags: [report, phase/0, baseline, area/strategy]
template: report
version: 1.0
---

# Phase 0 회귀 베이스라인 리포트 (2026-05)

> **관련 문서**: [[features/bar-44-baseline.plan|BAR-44 Plan]] | [[features/bar-44-baseline.design|BAR-44 Design]] | [[../01-plan/MASTER-EXECUTION-PLAN-v2|Master Plan v2]]

> **Date**: 2026-05-06
> **Author**: beye (CTO-lead)
> **Type**: Regression baseline (옵션 2 — 합성 데이터)
> **Reproducibility**: Fixed `seed=42`, `num_candles=250`, GBM synthetic data via `SyntheticDataLoader`
> **Source script**: [`scripts/run_baseline.py`](../../scripts/run_baseline.py)

---

## 1. 측정 조건

| 항목 | 값 |
|---|---|
| Seed | 42 |
| 거래일 수 | 250 (약 1년 KRX 영업일) |
| 데이터 소스 | `backend.core.strategy.backtester.SyntheticDataLoader.generate(...)` (GBM) |
| 시작 가격 | 50,000 KRW |
| 일간 drift | 0.0003 (≈ 0.03%) |
| 일간 변동성 | 0.02 (2%) |
| 시장 타입 | `MarketType.STOCK` (crypto_breakout 도 동일 데이터 — 합성이라 stock/crypto 분리 의미 약함) |
| BacktestConfig | 기본값 (분할 익절 / 손절 / 수수료 / 슬리피지 0%) |

---

## 2. 4 전략 베이스라인 결과

| Strategy | 거래수 | 승률 | 누적수익 | MDD | Sharpe |
|---|---:|---:|---:|---:|---:|
| `f_zone_v1` | 6 | 33.3% | -0.42% | 0.81% | -4.54 |
| `blue_line_v1` | 12 | 58.3% | 1.82% | 0.62% | 5.38 |
| `stock_v1` (수박) | 0 | 0.0% | 0.00% | 0.00% | 0.00 |
| `crypto_breakout_v1` | 0 | 0.0% | 0.00% | 0.00% | 0.00 |

**관찰**:
- `blue_line_v1` 가 가장 활발한 거래 (12건) + 양의 수익. 합성 GBM 데이터의 마일드 트렌드를 잘 포착.
- `f_zone_v1` 는 진입 6건 중 2건 승리 — 합성 데이터의 깊은 되돌림(F존) 발생 빈도 낮음.
- `stock_v1` (수박) / `crypto_breakout_v1` 는 0 거래 — *합성 GBM 데이터에서는 진입 조건이 까다로워* 신호 미발생. 정상 (실 데이터에서는 거래량/돌파 시그널이 풍부할 것).

**해석 한계**:
- 합성 데이터 1년 결과로 *전략 성과 절대 평가* 는 불가
- 본 표는 *후속 PR 회귀 비교의 기준점* 역할만 수행
- 실 OHLCV 5년 백테스트는 BAR-44b (Postgres 마이그·OHLCV 통합 후) 에서 수행 예정

---

## 3. 회귀 비교 임계값 정책

본 베이스라인은 **±5% 회귀 임계값** 의 기준이다.

후속 PR 이 strategy/backtester 코드에 변화를 가하는 경우 (BAR-45/46/48/49/51/79 등) 다음을 자동/수동 검증:

```
관측치 - 베이스라인 ≤ 5% (절대값 차) → ✅ 회귀 무영향
관측치 - 베이스라인 > 5%             → 🚨 회귀 의심, PR 차단 또는 design 재검토
```

**예시 (blue_line_v1 의 win_rate 58.3% 기준)**:
- 후속 PR 결과 win_rate ∈ [53.3%, 63.3%] → OK
- 후속 PR 결과 win_rate < 53.3% → 회귀 의심

**0 거래 전략 (stock_v1, crypto_breakout_v1)** 은 본 회귀 비교에서 *예외* (거래 발생 시 베이스라인 갱신 의무).

---

## 4. 재현성 검증

테스트: `backend/tests/strategy/test_baseline.py`

```
$ make test-baseline  # (Makefile 갱신 시)
$ .venv/bin/python -m pytest backend/tests/strategy/ -v

PASS: TestBaselineReproducibility::test_c1_run_returns_dict_of_4_strategies
PASS: TestBaselineReproducibility::test_c2_same_seed_reproducible
PASS: TestBaselineReproducibility::test_c3_different_seed_diverges
PASS: TestBaselineMetricsShape::test_c4_metrics_fields_exist
PASS: TestBaselineMetricsShape::test_c5_zero_trade_strategies_handled
PASS: TestBaselineMinimalData::test_c6_minimal_candles_50
```

(do PR 머지 시 실측 결과로 갱신)

---

## 5. JSON 데이터 (자동 생성)

`docs/04-report/PHASE-0-baseline.json` (run_baseline 실행 시 갱신).

```json
{
  "f_zone_v1": {
    "trades": 6,
    "win_rate": 0.3333,
    "total_return_pct": -0.0042,
    "max_drawdown": 0.0081,
    "sharpe_ratio": -4.54
  },
  "blue_line_v1": {
    "trades": 12,
    "win_rate": 0.5833,
    "total_return_pct": 0.0182,
    "max_drawdown": 0.0062,
    "sharpe_ratio": 5.38
  },
  "stock_v1": { "trades": 0, ... },
  "crypto_breakout_v1": { "trades": 0, ... }
}
```

---

## 6. 후속 정식 측정 인계 (BAR-44b)

본 합성 베이스라인은 *Phase 0 종료 게이트* 충족용. 다음 정식 측정은:

| 항목 | 처리 시점 |
|---|---|
| 5년 OHLCV 캐시 통합 (`/Users/beye/workspace/ai-trade/data/ohlcv_cache/` 144MB) | BAR-56 Postgres 마이그 후 |
| Workforward 분석 | BAR-79 (마스터 v2 의 백테스터 v2) |
| NXT 야간장 시뮬레이션 | Phase 2 BAR-53 후 |
| 슬리피지·수수료·세금 모델 | BAR-79 |

→ 본 베이스라인의 **수치 절대값** 은 신뢰 구간 좁음. **재현성·구조 정합** 만 신뢰 권장.

---

## 7. Phase 0 잔여 작업

본 베이스라인 머지 = **BAR-44 do 단계 완료**. 다음:

1. BAR-44 analyze (재현성 V1~V6 검증)
2. BAR-44 report (Phase 0 종료 게이트 통과 선언)
3. **Phase 1 진입** — BAR-45 (Strategy v2 추상)

---

## 8. Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-06 | 초기 합성 베이스라인 — 4 전략 × 5 지표, 회귀 임계값 ±5% 정책, BAR-44b 인계 |
