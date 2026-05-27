# exit_plan vs holding_evaluator SL 격차 결정 리포트 (B6)

**작성일**: 2026-05-28
**스코프**: 단타 3 전략의 두 청산 경로 SL 격차 분석 + 통일/유지/조정 옵션 + 권고
**관련**: `docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md` §4-3

---

## 1. 현재 격차 (관측된 비일관성)

| 전략 | exit_plan SL (Strategy.exit_plan) | holding_evaluator SL (STRATEGY_EXIT_PROFILES) | 격차 |
|---|---:|---:|---:|
| f_zone | −2% | −4% | 2.0%p |
| sf_zone | −1.5% | −4% | 2.5%p |
| gold_zone | −1.5% | −4% | 2.5%p |
| swing_38 (참조) | −15% (Phase D2) | −15% | 0%p (Phase D2 통일됨) |

→ 모든 intraday 단타에서 **`holding_evaluator` SL 이 `exit_plan` SL 보다 2~2.5%p 더 너그러움**.

---

## 2. 두 경로의 구조적 차이

### 2-1. ExitEngine (`backend/core/execution/exit_engine.py`)

- **입력**: `Strategy.exit_plan()` 결과 (`ExitPlan` 객체)
- **데이터**: OHLCV 분봉 candle (close/high/low)
- **평가 시점**: 분봉 close (1m 또는 5m)
- **우선순위**: TP1 → SL → TP2 → breakeven_trigger
- **`sl_at` 동적 갱신**: `_effective_sl(override_sl_at > pos.sl_at > time_stages > fixed_pct)`
- **호출 위치**: 운영 분봉 데몬 (`scripts/intraday_sell_daemon`)

### 2-2. HoldingEvaluator (`backend/core/risk/holding_evaluator.py`)

- **입력**: `HoldingPosition` (broker 잔고 조회 결과) + `ExitPolicy` (`STRATEGY_EXIT_PROFILES` override)
- **데이터**: `pnl_rate` (broker 의 실시간 손익률)
- **평가 시점**: 잔고 polling 주기 (보통 30초~분 단위)
- **우선순위** (`evaluate_holding`):
  1. swing 보유 기간 게이트 (`min_hold_days` / `max_hold_days`)
  2. 단기 고점 캔들 패턴 (`SHORT_TERM_HIGH`, P10)
  3. 트레일링 스톱 (`peak ≥ trailing_start` AND `rate < peak - offset`)
  4. 브레이크이븐 보호 (`peak ≥ trigger` AND `rate ≤ 0`)
  5. partial TP (`partial_tp_pct`)
  6. full TP (`take_profit_pct`)
  7. **시간 기반 SL** (`hold_days_tighten` 도달 시 `tightened_sl_pct`)
  8. **fallback SL** (`stop_loss_pct`)
- **호출 위치**: 잔고 모니터링 (`scripts/holding_check`)

### 2-3. 격차의 운영 의미

| 시나리오 | ExitEngine | HoldingEvaluator | 결과 |
|---|---|---|---|
| 정상 운영 | 분봉 −2% close → 매도 | broker pnl_rate −2% → HOLD (−4% 아님) | **ExitEngine 우선 매도** ✓ |
| ExitEngine 데몬 다운 | (작동 X) | broker pnl_rate −4% → 매도 | **HoldingEvaluator 안전망** ✓ |
| 분봉 fetch 실패 | exit_plan 평가 누락 | broker pnl_rate −4% → 매도 | HoldingEvaluator fallback |
| broker 갱신 지연 | 분봉 −2% close → 매도 | (pnl_rate 아직 −1.8%) | ExitEngine 우선 매도 (정상) |
| 갭다운 −5% 시작 | 분봉 close −5% → 매도 (이미 −2% 초과) | broker −5% → 매도 | 양쪽 동시 발동 (먼저 발동한 쪽 우선) |

→ **현재 격차는 의도된 2단 안전망**:
- 1차 방어선: ExitEngine (분봉 close 기반, 더 빠르고 더 빡빡 −2%)
- 2차 안전망: HoldingEvaluator (pnl_rate 기반, 더 너그러운 −4%로 ExitEngine 누락 시 백업)

---

## 3. 옵션 분석

### 옵션 A — 격차 제거 (통일, −2%로 일치)

| 장점 | 단점 |
|---|---|
| 운영 일관성 ↑ — 두 경로 동일 동작 | ExitEngine 누락 시 fallback 사라짐 |
| 코드 추론 단순화 | broker pnl_rate 노이즈로 false trigger 가능 (1m 분봉 close 가 −2% 안 가도 broker 갱신 시점 noise 로 일시적 −2.1% 가능) |
| 시뮬과 운영 더 가까움 | 안전망 의도 무력화 — 인시던트 발생 시 대응 가능성 ↓ |

### 옵션 B — 의도된 fallback 유지 (현재 상태) ⭐ 권고

| 장점 | 단점 |
|---|---|
| 2단 안전망 유지 — 운영 robustness ↑ | 코드 추론 시 두 경로 모두 검토 필요 |
| ExitEngine 실패 시 백업 작동 | 격차 의도 코드에 명시 안 됨 (현재) |
| broker pnl_rate 노이즈 흡수 가능 | 시뮬과 운영 SL 가정이 다름 |

### 옵션 C — 격차 축소 (조정, −2.5%로 좁힘)

| 장점 | 단점 |
|---|---|
| fallback 유지 + 격차 명확화 | 운영 데이터 없이 임의값 결정 |
| 시뮬-운영 격차 절반으로 축소 | broker 노이즈 흡수 폭 축소 |

---

## 4. 권고 — 옵션 B (의도된 fallback 유지) + 명시화

### 4-1. 즉시 작업 (의도 명시화 — 코드 변경 작음)

1. `holding_evaluator.py` `STRATEGY_EXIT_PROFILES` 에 격차 의도 docstring 추가:
   ```python
   # NOTE (2026-05-28, B6): stop_loss_pct 는 ExitEngine SL (exit_plan, -2% 단타)
   # 보다 2~2.5%p 더 너그러운 fallback. ExitEngine 누락 시 안전망 역할.
   # 의도된 격차이며 통일 검토는 Forward Test 1주 발동 비율 측정 후.
   ```
2. `Strategy.exit_plan()` SL 부근에도 짧은 코멘트:
   ```python
   stop_loss=StopLoss(fixed_pct=Decimal("-0.02")),
   # ExitEngine 1차 방어선. HoldingEvaluator(-4%) 가 fallback.
   ```

### 4-2. Forward Test 1주 후 결정

운영 머신에서 **B1 모니터링 스크립트의 추가 KPI** 측정:
- `ExitEngine SL 발동 건수` (분봉 close 기반)
- `HoldingEvaluator STOP_LOSS 발동 건수` (pnl_rate 기반)
- `발동 시간 차이` (ExitEngine 발동 시각 − HoldingEvaluator 발동 시각)
- `TIME_TIGHTENED_SL` 빈도 (시간 기반 SL 강화 발동)

판단 기준:

| 측정 결과 | 결정 |
|---|---|
| ExitEngine SL 발동 비율 > 95% (HoldingEvaluator 보다 항상 먼저) | **옵션 B 유지** — fallback 거의 작동 안 함 = 의도대로 |
| HoldingEvaluator SL 발동 비율 > 20% (ExitEngine 누락 잦음) | **옵션 A 통일** 또는 ExitEngine 안정성 보강 우선 |
| 두 경로 동시 발동 (같은 trade에서 양쪽 다 호출) | 매도 중복 방지 로직 점검 (별도 BAR) |
| broker pnl_rate noise 로 인한 false HoldingEvaluator 발동 발견 | **옵션 C 조정** (−2.5%) + noise 흡수 폭 유지 |

### 4-3. B1 모니터링 스크립트 KPI 확장 작업 (별도 task)

`scripts/daytrading_daily_monitor.py` 에 ExitEngine vs HoldingEvaluator 발동 비율 측정 추가 — 본 결정에 필요한 데이터 수집. 단, 현재 두 경로 모두 trade 종료 시점에 `barro_trade.db trades` 에 sell 행 1개만 남기므로 발동 경로 구분이 어려움.

**선결 작업**: `barro_trade.db trades` 스키마 또는 별도 audit 로그에 매도 발동 경로(`exit_engine` / `holding_evaluator`) 필드 추가 (별도 BAR).

---

## 5. 결정 요약

| 항목 | 결정 |
|---|---|
| **즉시 변경** | 없음 (현재 격차 유지) |
| **코드 수정** | 격차 의도 docstring 추가만 (옵션 B 명시화) |
| **데이터 수집** | Forward Test 1주 모니터링 KPI 확장 |
| **재검토 트리거** | HoldingEvaluator SL 발동 비율 > 20% 또는 broker noise 발견 시 |

본 리포트는 코드 변경 없이 **결정 + 의도 명시화** 가 핵심. 격차 자체는 운영 robustness 안전망으로 유지.

---

## 6. 관련 코드 파일

- `backend/core/execution/exit_engine.py:81-101` — SL 평가 로직
- `backend/core/risk/holding_evaluator.py:327-353` — effective_sl + 시간 SL 단계
- `backend/core/risk/holding_evaluator.py:78-124` — `STRATEGY_EXIT_PROFILES` 전략별 SL
- `backend/core/strategy/{f_zone,sf_zone,gold_zone}.py` — `exit_plan()` SL 정의
