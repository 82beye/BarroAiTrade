# 카테고리 B 시리즈 종합 리포트 — 단타 완성 작업 7개 진행 결과

**작성일**: 2026-05-28
**스코프**: Phase D2.1 단타 전용 모드 머지 후 분석 리포트 §5 권고 작업 7개 (B1~B7)
**관련 PR**: #176 (Phase D2.1) · #178 (B1+B2) · (예정) B 시리즈 종합

---

## Executive Summary

| 작업 | 핵심 결과 | 상태 |
|---|---|---|
| **B1** 모니터링 스크립트 | `scripts/daytrading_daily_monitor.py` — 전략별 KPI + 알람 3종 | ✅ PR #178 머지 |
| **B2** sf_zone cutoff 일관성 | `intraday_simulator` sf_zone 에 `entry_time_cutoff=14:00` 추가 | ✅ PR #178 머지 |
| **B3** 3 전략 비교 시뮬 | **sf_zone 모든 KPI 최고** (자본가중 +0.850%·승률 47.01%) | ✅ 결과 도출 |
| **B4** gold_zone 그리드 | **`min_conditions=3` 강화는 PnL 악화** / **`min_score=4.0` 강화는 +56% 개선** | ✅ 결과 도출 |
| **B5** f_zone 그리드 | **`pullback_min=-0.03` × `bounce_vol=1.2` 자본가중 +0.94%** (baseline +25%) / bounce_vol 강화는 무효 | ✅ 결과 도출 |
| **B6** SL 격차 결정 | 의도된 fallback 유지 + 명시화 — Forward Test 1주 후 재검토 | ✅ 리포트 |
| **B7** sf_zone TP3 비중 | **TP3 비중 변경 거의 무영향** — baseline 34% 유지 (TP3↓ 시 자본가중 미세 ↑) | ✅ 결과 도출 |

### 핵심 발견 5가지

1. **자본 배분 우선순위 명확**: sf_zone > f_zone > gold_zone (자본가중 +0.85% / +0.74% / +0.23%)
2. **gold_zone `min_conditions=3` 강화는 오히려 손실** — 분석 리포트 권고 일부 무효 확인
3. **gold_zone `min_score=4.0` 강화 효과 큼** — 자본가중 +0.148% → +0.231% (+56%)
4. **sf_zone TP3 비중 변경 거의 무영향** — 빈도가 작아 평균 영향 미미
5. **f_zone `FZoneParams` 변수명 confusion** (`pullback_max_pct`/`pullback_min_pct` 의미 반대) — 코드 리팩토링 후보

---

## B1: 단타 일일 모니터링 스크립트

### 산출물
`scripts/daytrading_daily_monitor.py` (357줄)

### 핵심 기능
- 데이터 소스 통합: `barro_trade.db trades` (strategy_id) + `active_positions.json` + `barro.log` ("신호 발생 [strategy]" JSON line)
- 전략별 시그널·매수/매도·closed 페어 카운트
- buy→sell FIFO 매칭으로 PnL% 계산
- 알람 조건 3종 (분석 리포트 §6.3):
  - `gold_zone` 일 trade > 20 + 승률 < 30% → 비활성 권고
  - 단일 전략 누적 PnL% ≤ −3% → 사후 분석 권고
  - `sf_zone` 일주일 누적 시그널 0건 → 임계 완화 권고
- HIGH 알람 시 exit code 1 (cron 알림 활용)

### 사용
```bash
venv/bin/python scripts/daytrading_daily_monitor.py
venv/bin/python scripts/daytrading_daily_monitor.py --date 2026-05-29 --save-md
```

---

## B2: sf_zone 시뮬 entry_time_cutoff 일관성 수정

`backend/core/backtester/intraday_simulator.py` `_build_strategies('sf_zone')` 에 `entry_time_cutoff=dtime(14, 0)` 추가.

f_zone (Phase 8e) / gold_zone (Phase 8d) 와 일관성 회복 (3줄 변경).

---

## B3: 3 전략 동일 종목 비교 시뮬

### 시뮬 조건
- 2,952종목 × 일봉 600봉 (no_data 15)
- 각 전략 default `Params` + `exit_plan()` 정책
- 한계: 단타 본래 1분봉 운영, 일봉 시뮬은 상대 비교만 유효
- 실행 시간: 1,151.9초 (≈ 19분)

### 결과

| 전략 | trades | 평균 PnL% | 중앙값 | min | max | 승률 | **자본가중** |
|---|---:|---:|---:|---:|---:|---:|---:|
| **sf_zone** | 887 | **+1.076%** | +0.000% | −1.500% | +5.020% | **47.01%** | **+0.8497%** |
| **f_zone** | 3,692 | +0.692% | +0.000% | −2.000% | +4.000% | 43.80% | +0.7392% |
| **gold_zone** | **30,469** | +0.189% | +0.000% | −1.500% | +3.000% | 37.62% | +0.2303% |

### 청산 사유 분포

| 전략 | be_stop | sl | tp2 | tp3 | max_hold |
|---|---:|---:|---:|---:|---:|
| sf_zone | 476 (54%) | 226 (25%) | — | 185 (21%) | 0 |
| f_zone | 1,436 (39%) | 1,222 (33%) | 1,029 (28%) | — | 5 |
| gold_zone | 13,603 (45%) | 9,724 (32%) | 4,665 (15%) | — | 2,477 (8%) |

### 핵심 통찰

1. **sf_zone 평균·승률·자본가중 모두 최고** — 분석 리포트 예상 일치 ("최상 신뢰도")
2. **gold_zone 진입 빈도 압도적** — sf_zone의 **34배** (30,469 vs 887), 평균 PnL은 sf_zone의 **1/6**
3. **자본 배분 우선순위 명확**: sf_zone > f_zone > gold_zone
4. **sf_zone TP3 도달 21%** — 3단 분할 익절의 가치 검증 (분석 리포트 §2-4)
5. **gold_zone max_hold 도달 8%** — 다른 전략보다 강제 청산 비중 높음

---

## B4: gold_zone `min_conditions` × `min_score` 그리드

### 시뮬 조건
- 791종목 × 일봉 600봉 (limit 800)
- 6 셀: `min_conditions ∈ [2, 3]` × `min_score ∈ [2.5, 4.0, 5.5]`
- 실행 시간: 769.5초 (≈ 12.8분)

### 결과

| `min_cond` | `min_score` | trades | 평균 | 승률 | **자본가중** | vs baseline |
|---:|---:|---:|---:|---:|---:|---:|
| **2** (baseline) | **2.5** (baseline) | 7,999 | +0.078% | 33.14% | +0.148% | — |
| 2 | **4.0** ★ | 5,506 | +0.090% | 33.71% | **+0.231%** | **+56%** |
| 2 | 5.5 | 2,521 | +0.107% | 33.68% | +0.229% | +54% |
| **3** | 2.5 | 1,132 | **−0.142%** | **26.33%** | **−0.054%** | **−136%** |
| 3 | 4.0 | 996 | −0.144% | 25.80% | −0.039% | −126% |
| 3 | 5.5 | 540 | −0.221% | 22.78% | −0.205% | −238% |

### 결정적 통찰 (분석 리포트 권고 검증)

| 분석 리포트 권고 | 시뮬 결과 | 권고 |
|---|---|---|
| `min_conditions: 2 → 3` | ❌ **무효** — 모든 PnL 악화 (평균/승률/자본가중 모두 ↓) | **default 2 유지** |
| `min_score: 2.5 → 4.0` | ✅ **유효** — 자본가중 +56% 개선, 승률 +0.57%p | **default 4.0 변경 권고** |
| `min_score: 4.0 → 5.5` | ⚠ marginal — 4.0 와 거의 동일 (+0.231 vs +0.229%) | **4.0 가 sweet spot** |

### 원인 분석

- **`min_conditions=3` 실패**: BB 하단 + Fib zone + RSI 회복 3 조건 동시 충족이 시장에서 드물고, 그런 셋업이 통계적으로 더 좋은 것도 아님. 셋업 정밀도와 성과 비상관.
- **`min_score=4.0` 성공**: 약한 시그널(score 2.5~4.0)이 평균을 끌어내림 — 4.0 이상만 받으면 노이즈 제거.

### Phase D2.3 권장 변경 (별도 PR)
```python
# backend/core/strategy/gold_zone.py
@dataclass
class GoldZoneParams:
    ...
    min_conditions: int = 2     # 유지 (분석 권고 무효)
    # 새 default — min_score 임계 (현재 코드는 2.5 hardcoded in _analyze_v2)
    # 또는 _analyze_v2 안의 `if score < 2.5` 임계를 4.0 으로 강화
```

---

## B5: f_zone `pullback_min` × `bounce_volume` 그리드

### 첫 시도의 실패 (변수명 confusion)
- `FZoneParams.pullback_max_pct` = "눌림 **최소** 하락 (−0.5%)"
- `FZoneParams.pullback_min_pct` = "눌림 **최대** 하락 (−5%)"
- → 변수명 의미 반대. 첫 스크립트가 `pullback_max_pct=-0.05` 설정 → 범위 충돌로 entries 0건
- **수정**: `pullback_min_pct` 로 변경 후 재실행

### 시뮬 조건
- 791종목 × 일봉 600봉 (limit 800)
- 9 셀: `pullback_min ∈ [-0.05, -0.04, -0.03]` × `bounce_vol ∈ [1.2, 1.5, 1.8]`
- 실행 시간: 833.2초 (≈ 13.9분)

### 결과

| `pullback_min` | `bounce_vol` | trades | 평균 | 승률 | **자본가중** | vs baseline |
|---:|---:|---:|---:|---:|---:|---:|
| **−0.05** (baseline) | **1.2** (baseline) | 993 | +0.479% | 39.88% | +0.7501% | — |
| −0.05 | 1.5 | 783 | +0.536% | 41.63% | +0.6798% | −9% |
| −0.05 | 1.8 | 633 | +0.628% | **43.44%** | +0.7365% | −2% |
| −0.04 | 1.2 | 829 | +0.451% | 39.81% | +0.7615% | +2% |
| −0.04 | 1.5 | 661 | +0.486% | 41.15% | +0.6508% | −13% |
| −0.04 | 1.8 | 536 | +0.560% | 43.10% | +0.6786% | −10% |
| **−0.03** ★ | **1.2** ★ | 597 | +0.490% | 39.36% | **+0.9382%** ★ | **+25%** |
| −0.03 | 1.5 | 478 | +0.489% | 40.17% | +0.8171% | +9% |
| −0.03 | 1.8 | 390 | +0.498% | 40.51% | +0.7807% | +4% |

### 결정적 통찰 (분석 리포트 §1-6 권고 검증)

| 분석 리포트 권고 | 시뮬 결과 | 권고 |
|---|---|---|
| `pullback_min_pct: -0.05 → -0.03` 강화 (얕은 눌림만) | ✅ **유효** — 자본가중 +25% (+0.75 → +0.94%) | **default 변경 권고** |
| `bounce_volume_ratio: 1.2 → 1.5` 강화 | ❌ **무효** — 자본가중 단조 ↓ (모든 pullback 면에서) | **default 1.2 유지** |

### 패턴 관찰

1. **수익률 vs 승률 trade-off** (bounce_vol 축):
   - bounce_vol ↑ → 진입 빈도 ↓, 평균 PnL ↑, 승률 ↑, **자본가중 ↓**
   - 정밀도 ↑는 좋은 시그널도 함께 제거 → 자본가중 손실
2. **pullback_min 강화는 일관된 우위** (-0.03 행 모두 자본가중 +0.78% 이상)
3. **최적 셀 (-0.03, 1.2) 의 의미**: "얕은 눌림만 받되, 반등 확인 정밀도는 default 유지" — 진입 정확성보다 신호 빈도 보존이 자본가중에 유리

### 코드 리팩토링 제안 (별도 BAR)
`FZoneParams` 변수명 직관화 — 본 시뮬에서 confusion 으로 첫 시도 실패한 경험에서 도출:
```python
pullback_shallow_pct: float = -0.005   # was pullback_max_pct (의미: 눌림 최소 깊이)
pullback_deep_pct: float = -0.03       # was pullback_min_pct (의미: 눌림 최대 깊이)
```
역호환성 위해 별도 BAR 작업으로 분리.

---

## B6: `exit_plan` vs `holding_evaluator` SL 격차 결정

### 격차 측정 (분석 리포트 §4-3 재확인)

| 전략 | `exit_plan` SL | `holding_evaluator` SL | 격차 |
|---|---:|---:|---:|
| f_zone | −2% | −4% | 2.0%p |
| sf_zone | −1.5% | −4% | 2.5%p |
| gold_zone | −1.5% | −4% | 2.5%p |

### 결정: 옵션 B (의도된 fallback 유지) + 명시화

**근거**:
- 두 경로가 다른 데이터 소스 (분봉 close vs broker pnl_rate) — 빠른 발동 우선 + 안전망 구조
- ExitEngine = 1차 방어선 (−1.5~−2%), HoldingEvaluator = 2차 안전망 (−4%)
- 통일 시 ExitEngine 누락 (데몬 다운, 분봉 fetch 실패 등) 대응 불가

### 후속 작업 (별도 BAR)
1. `STRATEGY_EXIT_PROFILES` 에 격차 의도 docstring 추가
2. `Strategy.exit_plan()` SL 부근 짧은 코멘트
3. Forward Test 1주 후 ExitEngine vs HoldingEvaluator 발동 비율 측정
4. `barro_trade.db trades` 에 매도 발동 경로 필드 추가 (별도 BAR)

### 재검토 트리거
- HoldingEvaluator SL 발동 비율 > 20% (ExitEngine 누락 잦음) → 옵션 A 통일 검토
- broker pnl_rate noise 로 false trigger 발견 → 옵션 C 조정 검토

---

## B7: sf_zone TP3 비중 그리드

### 시뮬 조건
- 791종목 × 일봉 600봉 (limit 800)
- 6 셀: `TP3_ratio ∈ [0.20, 0.25, 0.30, 0.34, 0.40, 0.45]` (TP1=TP2=(1−TP3)/2)
- 실행 시간: 574.7초 (≈ 9.6분)

### 결과

| `TP3_ratio` | TP1=TP2 | trades | 평균 | 승률 | **자본가중** |
|---:|---:|---:|---:|---:|---:|
| 0.20 | 0.400 | 217 | +0.868% | 43.32% | **+0.7295%** ★ |
| 0.25 | 0.375 | 217 | +0.869% | 43.32% | +0.7189% |
| 0.30 | 0.350 | 217 | +0.870% | 43.32% | +0.7082% |
| **0.34** (baseline) | 0.330 | 217 | +0.870% | 43.32% | +0.6997% |
| 0.40 | 0.300 | 217 | +0.871% | 43.32% | +0.6869% |
| 0.45 | 0.275 | 217 | +0.872% | 43.32% | +0.6763% |

### 핵심 통찰

1. **모든 셀 trades 수 동일 217건, 승률 43.32% 동일** — TP3 비중은 진입 결정과 무관 (당연)
2. **평균 PnL 거의 동일** (0.868~0.872%, 차이 0.004%p) — TP3 비중 효과 미미
3. **자본가중은 TP3 ↓ 일수록 ↑** — TP3=20% 시 +0.7295%, TP3=45% 시 +0.6763% (8% 우위)
4. **차이 작음** — baseline 34% vs 20% 차이 약 4% — 운영 복잡도 변경 가치 미미

### 결론
- **baseline TP3=34% 유지** 권고
- 추가 튜닝이 의미 있으려면 TP1/TP2 자체 임계 조정 필요 (TP3 비중만으론 효과 제한적)

---

## 통합 권고

### 즉시 적용 (별도 PR 권장)

| 항목 | 변경 | 효과 |
|---|---|---|
| `GoldZoneStrategy._analyze_v2` `min_score` 임계 | 2.5 → **4.0** | 자본가중 +56%, 진입 빈도 −31% (좀 더 정밀) |
| `FZoneParams.pullback_min_pct` default | −0.05 → **−0.03** | 자본가중 +25% (얕은 눌림만) |
| `STRATEGY_EXIT_PROFILES` SL 격차 docstring | 의도 명시 코멘트 추가 | 운영 코드 추론 명확성 |

### 운영 진행 (사용자 머신)

| 항목 | 시점 |
|---|---|
| `daytrading_daily_monitor.py` 매일 실행 (cron) | 2026-05-29 (목) 첫 영업일부터 |
| ExitEngine vs HoldingEvaluator 발동 비율 측정 (B6 후속) | Forward Test 1주 후 |
| gold_zone live 데뷔 첫 주 trade·승률·자본가중 추적 | 5/29 ~ 6/4 |

### 보류 작업 (별도 BAR)

1. **`FZoneParams` 변수명 리팩토링** — `pullback_max`/`pullback_min` 직관화
2. **`barro_trade.db trades`에 매도 발동 경로 필드** — B6 후속 측정 위해
3. **gold_zone live 1주 결과 기반 추가 그리드** — TP/SL 자체 임계 조정

### 보류 — 단타 완성 후 재개

- swing_38 Phase D2 (Forward Test 6주 후 결정) — 코드 보존
- blue_line / crypto_breakout 재활성화 (단타 완성 후 토글 한 줄)
- 승률 80% KPI 별도 BAR (앙상블 또는 진입 게이트 강화)

---

## 산출물 인덱스

| 작업 | 파일 |
|---|---|
| B1 스크립트 | `scripts/daytrading_daily_monitor.py` |
| B2 패치 | `backend/core/backtester/intraday_simulator.py` |
| B3 시뮬 코드 | `analysis/imports/2026-05-28/strategies_compare.py` |
| B3 JSON | `analysis/imports/2026-05-28/reports/strategies_compare_20260528_020358.json` |
| B4 시뮬 코드 | `analysis/imports/2026-05-28/gold_zone_grid.py` |
| B4 JSON | `analysis/imports/2026-05-28/reports/gold_zone_grid_20260528_021821.json` |
| B5 시뮬 코드 | `analysis/imports/2026-05-28/f_zone_grid.py` (재실행 결과 반영) |
| B6 결정 리포트 | `docs/04-report/features/2026-05-28-sl-gap-decision.md` |
| B7 시뮬 코드 | `analysis/imports/2026-05-28/sf_zone_tp3_grid.py` |
| B7 JSON | `analysis/imports/2026-05-28/reports/sf_zone_tp3_grid_20260528_022521.json` |
| 본 종합 리포트 | `docs/04-report/features/2026-05-28-b-series-summary.md` |
