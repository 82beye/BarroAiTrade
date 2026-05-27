# 활성 단타 3 전략 상세 분석 (sf_zone · f_zone · gold_zone)

**작성일**: 2026-05-28
**브랜치**: BAR-OPS-09 (Phase D2.1 단타 전용 모드 적용 후)
**대상**: SignalScanner default 활성 전략 3개
**비활성 (참조)**: blue_line · crypto_breakout · swing_38

---

## Executive Summary

BAR-OPS-09 Phase D2.1 (2026-05-28) 머지로 단타 전용 모드 전환 — **sf_zone(1), f_zone(2), gold_zone(6)** 만 SignalScanner default 활성. 본 리포트는 단타 전략 완성 BAR 작업의 의사결정 근거 정리.

| 전략 | 진입 빈도 | 시그널 신뢰도 | live 검증 | TP 폭 | SL 폭 (exit_plan) |
|---|---|---|---|---:|---:|
| **f_zone** | 중 | 중-상 | 9개월+ | +3% / +5% | −2% |
| **sf_zone** | **최저** | **최상** | 5건 100% win | +3% / +5% / **+7%** | **−1.5%** |
| **gold_zone** | 최고 (예상) | 중 | **0건** ★ D2.1 데뷔 | +2% / +4% | −1.5% |

**핵심 발견 4가지**:
1. **모든 전략에서 `exit_plan` SL(−1.5~−2%) 이 `holding_evaluator` SL(−4%) 보다 빡빡 → 먼저 발동**
2. **sf_zone 시뮬 진입점에 `entry_time_cutoff` 누락** (f_zone, gold_zone과 비일관)
3. **gold_zone live 노출 0건** — D2.1 첫 데뷔라 첫 영업일 집중 모니터링 필수
4. **gold_zone `min_score=2.5`**, sf_zone 7.0, f_zone 4.0 — 진입 게이트 임계가 큰 차이

---

## 1. f_zone (FZone Strategy) — 가장 정교한 4단계 진입

**원리**: 급등 기준봉 → 눌림목 → 이평선 지지 → 반등 (서희파더 특허 기법, `thetrading2021`)

### 1-1. 진입 4단계 (모두 충족 필수)

| 단계 | 조건 | 기본값 (`FZoneParams` default) |
|---|---|---|
| ① 기준봉 | 최근 N봉 내 양봉 + 거래량 폭증 | `impulse_min_gain=3%`, `max=100%` (무제한), `vol_ratio=2.0x`, `lookback=5봉` |
| ② 눌림목 | 기준봉 이후 하락 + 거래량 감소 | `pullback −0.5%~−5%`, `vol_ratio≤0.7x`, `max_candles=10` |
| ③ 이평 지지 | 현재 저점이 MA(5/20/60) 근접 | `ma_periods=[5,20,60]`, `tolerance=0.01` (±1%) |
| ④ 반등 | 마지막 봉 양봉 + 거래량 재증가 | `bounce_min_gain=0.5%`, `bounce_vol_ratio=1.2x` |

### 1-2. 점수 계산 (0~10점)

```
score = gain_score (max 2)                           # min(impulse_gain/sf_min_gain, 1) × 2
      + vol_score (max 1)                            # min(impulse_vol/sf_vol_ratio, 1) × 1
      + pullback_score (max 2)                       # max(0, 1 - depth/0.05) × 2
      + ma_score (max 2)                             # 2 - (touch_pct / tolerance)
      + bounce_score (max 1.5)                       # min(bounce_gain/0.02, 1) × 1.5
      + bounce_vol_score (max 1.5)                   # min(bounce_vol/2.0, 1) × 1.5
      [+ watermelon_bonus (1.0)  옵션, default OFF]

F존 판정 :  score >= 4.0
SF존 판정 : F존 + impulse_gain ≥ 5% + vol ≥ 3.0x + score ≥ 7.0
```

### 1-3. 청산 — 두 경로

| 경로 | TP1 | TP2 | SL | BE trigger | trailing | time_exit |
|---|---:|---:|---:|---:|---|---:|
| **exit_plan** (ExitEngine 가격 기반) | +3% (50%) | +5% (50%) | **−2%** | +1.5% | — | 14:50 |
| **holding_evaluator** (pnl_rate 적응형) | partial 3% (50%) | full 5% | **−4%** | +2.5% | start 3.5% / off −1.0% | — |

> SL: exit_plan(−2%) 가 더 빡빡 → 먼저 발동. holding_evaluator(−4%) 는 fallback.

### 1-4. 필터 / 시뮬 override

| 필터 | default | 시뮬 override (`intraday_simulator._build_strategies`) |
|---|---|---|
| `min_atr_pct` | 0.0 | **0.035** ✓ |
| `entry_time_cutoff` | None | **dtime(14, 0)** ✓ (Phase 8e, 14시 이후 진입 차단) |

### 1-5. 프리셋 (다중 timeframe)

| 프리셋 | timeframe | 주요 조정 |
|---|---|---|
| `FZoneParams.for_5min()` | 5분봉 | impulse +2%, lookback 12봉, MA [12,36,72], min_candles 72 |
| `FZoneParams.for_intraday()` | 1분봉 | impulse +1%, lookback 15봉, pullback_vol 0.85, min_candles 120 |

### 1-6. 강·약점 / 튜닝 후보

| 강점 | 약점 |
|---|---|
| 4단계 순차 검증 → false positive 최소 | 진입 빈도 낮음 (4단 모두 통과 필요) |
| 운영 9개월+ 실 검증된 패턴 | `impulse_max_gain_pct=1.0` (무제한) — 과열 진입 위험 |
| score 4점 이상만 시그널 → 약한 신호 차단 | `bounce_volume_ratio=1.2` 약함 — 가짜 반등 통과 |
| 5분/1분봉 프리셋 보유 | `pullback_max −5%` 깊은 눌림 허용 — 손실 trade 비율 ↑ |

**단타 완성 시 튜닝 후보**:
- `pullback_max_pct: -0.05 → -0.03` 강화 (얕은 눌림만)
- `bounce_volume_ratio: 1.2 → 1.5` 강화
- `impulse_max_gain_pct: 1.0 → 0.07` (단 `LESSON_FZONE_MAX_GAIN.md` 우려 — winning까지 동시 죽임)
- `ma_support_tolerance: 0.01 → 0.005`
- default `min_atr_pct: 0.0 → 0.035` 활성화

---

## 2. sf_zone (SF존, "슈퍼존") — F존 + 강화 필터, delegate 패턴

**원리**: F존 모든 조건 + 강한 기준봉 + 높은 점수만 통과 (내부에 `FZoneStrategy` 인스턴스 보유, BAR-47)

### 2-1. 진입 조건

F존 4단계 + **추가 게이트**:
- `impulse_gain ≥ 5%` (F존 3% 의 1.67배)
- `impulse_volume_ratio ≥ 3.0x` (F존 2.0x 의 1.5배)
- **F존 종합 점수 ≥ 7.0**

→ 즉 F존 시그널 중 상위 ~20% 만 sf_zone 으로 통과

### 2-2. 청산 — 3단 분할 (sf_zone 만의 특징 ★)

| 경로 | TP1 | TP2 | TP3 | SL | BE | trailing |
|---|---:|---:|---:|---:|---:|---|
| **exit_plan** | +3% (33%) | +5% (33%) | **+7% (34%)** ⭐ | **−1.5%** (단타 중 최저) | +1.0% | — |
| **holding_evaluator** | partial 3% (33%) | — | full 7% | −4% | +2.0% | 3.0% / −1.5% |

### 2-3. 시뮬 override (⚠ entry_time_cutoff 누락)

```python
# backend/core/backtester/intraday_simulator.py
SFZoneStrategy(FZoneParams(min_atr_pct=0.035))
# f_zone 은 entry_time_cutoff=dtime(14, 0) 있는데 sf_zone 누락
```

### 2-4. 강·약점 / 튜닝 후보

| 강점 | 약점 |
|---|---|
| BAR-OPS-09 누적 발동 5건 100% win, flu% ≥10.2% | 진입 빈도 매우 낮음 (F존 의 ~20%) |
| 3단 분할로 +7% 까지 추적 | delegate 패턴 — 디버깅 시 두 클래스 추적 필요 |
| SL −1.5% 가장 타이트 → 손실 폭 작음 | `entry_time_cutoff` 시뮬 override 누락 (f_zone과 비일관) |
| 강한 기준봉 + 높은 점수 → 신뢰도 최상 | F존 default 변경 시 자동 영향 받음 (delegate 결과) |

**단타 완성 시 튜닝 후보**:
- 시뮬 override 에 `entry_time_cutoff=dtime(14, 0)` 추가 (f_zone과 일관성) ← **우선순위 ★**
- `score ≥ 7.0` 임계 조정 시뮬 (6.5 / 7.0 / 7.5 grid)
- TP3 비중 34% → 25/30/40% grid 비교
- live 실거래 시그널 발동 빈도 모니터링 (현재 5건뿐)

---

## 3. gold_zone (골드존) — 보수적 되돌림 매수 (D2.1 신규 SignalScanner 등록 ★)

**원리**: BB 하단 + Fib zone + RSI 과매도 회복 (3 조건 중 2/3 충족, BAR-48)

### 3-1. 진입 3 조건 (`min_conditions=2`)

| 조건 | 임계 | 점수 [0, 1] 계산 |
|---|---|---|
| ① BB 하단 | 1% 이내 (`bb_proximity_pct=0.03`) | 가까울수록 높음, `close ≤ lower` 면 1.0 |
| ② Fib zone | 0.236 ~ 0.786 (최근 30봉 고저 기준) | 중심(0.511) 가까울수록 높음 |
| ③ RSI 회복 | 최근 10봉 min ≤ 35 + 현재 ≥ 38 | `(rsi_now - 35) / (38 - 35)` 정규화 |

### 3-2. 점수 → 진입 게이트

```
raw   = bb_score × 0.4 + fib_score × 0.3 + rsi_score × 0.3
score = raw × 10                                  # 0-10 스케일 정규화
조건 충족 ≥ 2/3  AND  score ≥ 2.5  →  진입
```

> `min_score=2.5` 가 단타 3 중 가장 낮음 (f_zone 4.0, sf_zone 7.0 대비)

### 3-3. 청산 (가장 보수적, TP 폭 최소)

| 경로 | TP1 | TP2 | SL | BE | trailing |
|---|---:|---:|---:|---:|---|
| **exit_plan** | +2% (50%) | +4% (50%) | **−1.5%** | +1.0% | — |
| **holding_evaluator** | partial 2% (50%) | full 4% | −4% | +2.5% | 3.0% / −1.0% |

### 3-4. 시뮬 override

```python
GoldZoneStrategy(GoldZoneParams(
    min_atr_pct=0.035,
    entry_time_cutoff=dtime(14, 0),   # 5/22 379800 KODEX S&P500 15:01 위험 진입 차단
))
```

### 3-5. 강·약점 / 튜닝 후보

| 강점 | 약점 |
|---|---|
| 보수적 진입 — 바닥권에서만 매수 | live 노출 0건 (D2.1 첫 데뷔) — 실거래 미검증 |
| 3 조건 중 2 충족 유연성 | `min_conditions=2` 가 너무 유연 — 2/3와 3/3 동등 처리 |
| RSI 과매도 회복 → 모멘텀 전환 포착 | `min_score=2.5` 낮음 — 약한 시그널 통과 가능성 |
| TP 폭 +2~+4% 짧음 → 빈번한 실현 | TP +4% 짧음 → 큰 상승 trade 놓침 |

**단타 완성 시 튜닝 후보 (D2.1 신규 도입이라 중요도 ↑)**:
- **첫 영업일부터 trade 빈도·승률·자본가중 집중 모니터링** ← **최우선**
- `min_conditions: 2 → 3` (모든 조건 충족만) 시뮬
- `min_score: 2.5 → 4.0` (다른 v2 전략과 일치) 시뮬
- TP2: +4% → +5%/+7% 확대 grid
- BB-Fib-RSI 가중치 0.4/0.3/0.3 → 0.5/0.25/0.25 등 그리드

---

## 4. 통합 비교

### 4-1. 진입 빈도·게이트 비교

| 항목 | f_zone | sf_zone | gold_zone |
|---|---:|---:|---:|
| 진입 단계 | **4단 순차** | F존 + 강화 필터 | **3 중 2/3** (유연) |
| score 게이트 | ≥ 4.0 | ≥ 7.0 | ≥ 2.5 |
| 진입 빈도 (상대) | 중 | **최저** | 최고 (예상) |
| 시그널 신뢰도 | 중-상 | **최상** | 중 (live 미검증) |
| live 검증 | 9개월+ | 5건 100% win | **0건** (D2.1 데뷔) |

### 4-2. 청산 비교 (exit_plan 기준)

| 항목 | f_zone | sf_zone | gold_zone |
|---|---:|---:|---:|
| TP1 | +3% (50%) | +3% (33%) | +2% (50%) |
| TP2 | +5% (50%) | +5% (33%) | +4% (50%) |
| TP3 | — | **+7% (34%)** ★ | — |
| SL | −2% | **−1.5%** (가장 타이트) | −1.5% |
| BE trigger | +1.5% | +1.0% | +1.0% |
| time_exit | 14:50 | 14:50 | 14:50 |

### 4-3. exit_plan vs holding_evaluator SL 격차 (운영 의도 점검 필요)

| 전략 | exit_plan SL | holding_evaluator SL | 격차 |
|---|---:|---:|---:|
| f_zone | −2% | −4% | 2%p |
| sf_zone | −1.5% | −4% | 2.5%p |
| gold_zone | −1.5% | −4% | 2.5%p |

> 모든 전략에서 **exit_plan SL 이 빡빡 → 먼저 발동**. holding_evaluator(−4%) 는 사실상 fallback.
>
> - ExitEngine: 분봉 close 기반, 진입 시 ExitPlan 생성
> - HoldingEvaluator: HoldingPosition `pnl_rate` 적응형 갱신 (peak 추적, trailing 발동)
>
> 두 경로 동시 사용 — 빠른 발동 우선. 의도된 fallback 인지, 일관성 위해 통일할지 별도 결정 필요.

### 4-4. 시뮬 override 일관성 (⚠ 비일관 발견)

| 전략 | `min_atr_pct` | `entry_time_cutoff` |
|---|---:|---|
| f_zone | 0.035 | dtime(14, 0) ✓ |
| sf_zone | 0.035 | **누락** ⚠ |
| gold_zone | 0.035 | dtime(14, 0) ✓ |

> sf_zone 시뮬 진입점에 `entry_time_cutoff` 누락 — 다른 두 전략과 일관성 깨짐.
> 단타 완성 BAR 작업 시 작은 수정으로 큰 일관성 가치 회복.

### 4-5. Phase 9 균등 진입 (5/23 머지)

세 전략 모두 `position_size()` 가 `even_position_size()` 호출 — score 차등 무력화, 종목당 동일 비율 (default 0.08, `max_per_position`).

### 4-6. 우선순위 (`SignalScanner._analyze_symbol`)

```
SF > F > Gold > Blue > Crypto > Swing38
```

활성 단타 3개만 → **SF → F → Gold** 순으로 dispatch. 첫 시그널 반환 시 다음 전략 skip.

---

## 5. 단타 완성 BAR 작업 우선순위

| 순위 | 작업 | 예상 효과 |
|---:|---|---|
| 1 | **gold_zone live 데뷔 첫 주 모니터링** | live 미검증 전략 검증, 비정상 시 즉시 비활성 |
| 2 | sf_zone 시뮬 `entry_time_cutoff` 일관성 수정 | 운영 일관성 (작은 작업, 큰 가치) |
| 3 | 3 전략 동일 종목 시뮬 — 자본가중·승률 비교 | 어느 전략에 자본 배분 비중 늘릴지 결정 |
| 4 | gold_zone `min_conditions` 2→3 grid | 시그널 정밀도 ↑, 진입 빈도 ↓ trade-off |
| 5 | f_zone `pullback_max` 강화 grid | 손실 trade 비율 감소 시도 |
| 6 | `exit_plan` vs `holding_evaluator` SL 격차 통일 검토 | 운영 의도 명확화 (의도된 fallback 인지 결정) |
| 7 | sf_zone TP3 비중 (34%) grid 서치 | +7% 도달 trade 의 자본 회수율 최적화 |

---

## 6. 운영 모니터링 가이드 (Phase D2.1 머지 후 첫 주)

### 6-1. 시작 확인

운영 데몬 재기동 후 INFO 로그에 다음 출력 확인:
```
SignalScanner 활성 전략: ['sf_zone', 'f_zone', 'gold_zone']
```

### 6-2. 일별 측정 KPI

| KPI | 측정 방법 | 기대 |
|---|---|---|
| 시그널 발동 빈도 | `logs/barro.log` `grep "신호 발생"` 일별 카운트 | f_zone > gold_zone > sf_zone 예상 |
| 전략별 trade 수 | `data/order_audit.csv` `strategy_id` 별 | f_zone 다수, sf_zone 희소 |
| 전략별 평균 PnL% | order_audit + active_positions 매칭 | sf_zone 최대 (시뮬상 100% win) |
| 전략별 승률 | trade close 결과 | gold_zone 첫 주는 보수적 추정 |
| 청산 사유 분포 | ExitEngine vs HoldingEvaluator 발동 비율 | exit_plan SL 우세 예상 |

### 6-3. 알람 기준

| 조건 | 대응 |
|---|---|
| gold_zone 일 trade > 20건 + 승률 < 30% | gold_zone 즉시 비활성 (`enabled_strategies={"gold_zone": False}`) |
| 단일 전략 자본가중 누적 −3% 도달 | 해당 전략 비활성 + 사후 분석 |
| ExitEngine SL 발동 vs HoldingEvaluator 발동 비율 < 5% | exit_plan SL 너무 빡빡 → 완화 검토 |
| sf_zone 일주일 trade 0건 | 진입 임계 (score≥7.0) 완화 시뮬 |

---

## 7. 참고

- 비활성 전략 (`enabled_strategies` override 로 재활성화 가능):
  - `blue_line` (3번) — 5EMA × 20EMA 골든크로스 + 거래량 1.5x
  - `crypto_breakout` (4번) — 박스권(20봉) 돌파 + 1% 버퍼
  - `swing_38` (5번) — 일봉 multi-day 스윙, Phase D2 결합 최적 보존 (TP+20/+50/SL−15/max_hold=20)

- 관련 문서:
  - `docs/04-report/features/2026-05-28-phase-d-grid-summary.md` — swing_38 Phase D 그리드 시리즈
  - `backend/core/scanner/signal_scanner.py` — Phase D2.1 단타 전용 모드
  - `backend/core/strategy/{f_zone,sf_zone,gold_zone}.py` — 본 분석 대상 코드
  - `backend/core/risk/holding_evaluator.py` — `STRATEGY_EXIT_PROFILES` 운영 청산 정책

- 관련 BAR 이력:
  - BAR-47: SF존 클래스 분리 (delegate 패턴)
  - BAR-48: gold_zone 신규 포팅
  - BAR-49: swing_38 신규 포팅
  - BAR-OPS-09 Phase 4/5/6: 변동성 필터 `min_atr_pct` 시뮬 활성
  - BAR-OPS-09 Phase 8c/8d/8e: 진입 시간 cutoff (14:00)
  - BAR-OPS-09 Phase 9: 균등 진입 (`even_position_size`)
  - BAR-OPS-09 Phase D2.1: SignalScanner 단타 전용 모드 + gold_zone 등록 (PR #176)
