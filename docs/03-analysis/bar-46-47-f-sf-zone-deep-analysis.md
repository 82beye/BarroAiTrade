# BAR-46/47 — F존·SF존 매매 로직 Deep Analysis (2026-05-16)

## 목적

[bar-46-f-zone-v2.analysis.md](bar-46-f-zone-v2.analysis.md)·[bar-47-sf-zone-split.analysis.md](bar-47-sf-zone-split.analysis.md)의 후속 — **현재 구현된 진입 5단계·점수 산식·청산 분기를 코드 라인 단위로 정리**하고 백테스트/라이브 차이를 명시한다. F존 ATR 청산 적용 실험의 사전 문서.

## 분석 대상 파일

- `backend/core/strategy/f_zone.py` — `FZoneStrategy` + `FZoneParams` + `FZoneAnalysis`
- `backend/core/strategy/sf_zone.py` — `SFZoneStrategy` (delegate)
- `backend/core/backtester/intraday_simulator.py` — `_exit_plan_for_strategy` / `_sfzone_atr_exit_plan` / `_scaled_exit_plan`
- `backend/core/execution/exit_engine.py` — `ExitEngine.evaluate` (TP/SL/breakeven 순서)
- 출처: thetrading2021 (서희파더 이재상) 특허 매매기법 (f_zone.py 모듈 docstring)

---

## 1. F존 — 5단계 진입 조건

"급등(기준봉) → 눌림목 조정 → 이평선 지지 → 반등 캔들 → 진입" 패턴. `FZoneStrategy._analyze_v2` (f_zone.py:138-214)이 단계별 평가 후 점수 ≥ 4.0이면 신호.

### 단계 1: 기준봉(impulse) 탐지 (`_detect_impulse`, f_zone.py:276-320)

- 탐색 범위: `impulse_lookback=5` 봉 (현재봉 직전 5봉)
- 조건: `gain_pct ∈ [3%, 100%]` AND `volume ≥ 2× avg_volume`
- `impulse_max_gain_pct=1.0` default = 무제한 — 과거 max 7% 적용 시 winning 시그널까지 죽임이 검증됨 ([LESSON_FZONE_MAX_GAIN.md](../../analysis/imports/2026-05-13/LESSON_FZONE_MAX_GAIN.md), 2026-05-14)
- 최고 gain 봉을 best로 선택, `impulse_bar_idx` 저장

### 단계 2: 눌림목(pullback) 확인 (`_detect_pullback`, f_zone.py:322-359)

- 범위: 기준봉 이후 ~ 현재봉 이전 (`df.iloc[imp_idx+1 : n-1]`)
- 조건: `pullback_pct = (lowest_low - impulse_close) / impulse_close ∈ [-5%, -0.5%]`
- 거래량 조건: `avg_pullback_volume / impulse_volume ≤ 0.7` (조정 시 거래량 감소 확인)

### 단계 3: 이평선 지지 (`_check_ma_support`, f_zone.py:361-389)

- MA 기간: [5, 20, 60]
- 조건: 현재봉 `low`가 어느 이평선의 `±1%` 이내 (`ma_support_tolerance=0.01`)
- **선택적** — 미충족 시에도 단계 4~5 진행하지만 점수에서 2점 손실

### 단계 4: 반등 캔들 (`_detect_bounce`, f_zone.py:391-425)

- 현재봉(마지막 봉) 조건: `bounce_gain_pct ≥ 0.5%` AND `current.volume / avg_pullback_volume ≥ 1.2`
- 양봉 + 거래량 회복으로 반등 확인

### 단계 5: 점수 계산·분류 (`_score_and_classify`, f_zone.py:427-478)

| 요소 | 만점 | 산식 |
|------|-----:|------|
| 기준봉 gain | 2.0 | `min(impulse_gain / 0.05, 1.0) × 2.0` (sf_impulse_min_gain_pct=0.05 기준) |
| 기준봉 volume | 1.0 | `min(impulse_vol_ratio / 3.0, 1.0) × 1.0` (sf_volume_ratio=3.0 기준) |
| 눌림목 | 2.0 | `max(0, 1.0 - |pullback| / 0.05) × 2.0` (얕을수록 ↑) |
| 이평선 지지 | 2.0 | `2.0 - touch_pct / 0.01` (가까울수록 ↑, 미지지 시 0) |
| 반등 gain | 1.5 | `min(bounce_gain / 0.02, 1.0) × 1.5` |
| 반등 volume | 1.5 | `min(bounce_vol_ratio / 2.0, 1.0) × 1.5` |
| **총합** | **10.0** | |

- **F존 조건** (`is_f_zone`): `score ≥ 4.0` AND (impulse·pullback·bounce 모두 `has=True`)
- **SF존 조건** (`is_sf_zone`): F존 + `impulse_gain ≥ 0.05` + `impulse_vol_ratio ≥ 3.0` + `score ≥ 7.0`
- 신호의 `signal_type`은 `"sf_zone"` 또는 `"f_zone"`

### 변동성 필터 (F1, f_zone.py:78-87, 155-163)

- `FZoneParams.min_atr_pct` default `0.0` (비활성, BAR-44 baseline 회귀 보존)
- **`IntradaySimulator._build_strategies`에서 f_zone만 0.035 활성화** (intraday_simulator.py:180) — 운영 권장값
- ATR%(14봉) < 임계 시 진입 거부 — 단계 1~5 평가 전 차단
- 목적: SL 폭(-1.5~-2%)이 정상 일중 변동의 절반 이하인 저변동·고가주(LG전자급)에서 SL 노이즈 발동 위험 차단. 600봉 백테스트에서 +186k 효과 검증 ([LESSON_S1_NORMALIZATION.md](../../analysis/imports/2026-05-13/LESSON_S1_NORMALIZATION.md))

---

## 2. SF존 — F존 강화판 (delegate, sf_zone.py:32-110)

### 진입 로직: F존과 100% 동일 (재사용)

- `SFZoneStrategy._inner = FZoneStrategy()` — delegate 패턴 (Option A)
- F존 분석 결과 중 `signal.signal_type == "sf_zone"`만 통과 → `strategy_id`를 `"sf_zone_v1"`로 재라벨
- 즉 **SF존은 F존의 가장 강한 부분집합** — score 7.0+ AND 기준봉 5%+ AND 거래량 3x+
- 변동성 필터 **OFF** (`SFZoneStrategy()`가 `FZoneParams()` 기본 — `min_atr_pct=0.0`) — 점수 7.0+ 조건이 이미 빡빡해 추가 필터 불필요

### 청산 정책 차별화 (`SFZoneStrategy.exit_plan`, sf_zone.py:51-80)

라이브 청산 정책이 F존 대비 강화:

| 항목 | F존 | SF존 |
|------|-----|-----|
| TP1 | +3% (50%) | +3% (33%) |
| TP2 | +5% (50%) | +5% (33%) |
| TP3 | — | **+7% (34%) 추가** |
| SL | -2% 고정 | **-1.5% 고정** (0.5%p 타이트) |
| breakeven_trigger | +1.5% | **+1.0%** (빠른 BE 전환) |
| time_exit (KRX) | 14:50 | 14:50 |

### 포지션 사이징 (`position_size`)

| score | F존 비중 | SF존 비중 |
|-------|---:|---:|
| ≥ 0.7 | 30% | **35%** (+5%p 공격적) |
| ≥ 0.5 | 20% | **25%** (+5%p) |
| < 0.5 | 10% | 10% |

---

## 3. ⚠️ 백테스트 vs 라이브 청산 분기

**중요**: `IntradaySimulator`는 전략 자체의 `exit_plan()` 메서드를 **무시**하고 백테스트 전용 plan을 사용한다 (`_exit_plan_for_strategy`, intraday_simulator.py:525-537).

```python
def _exit_plan_for_strategy(strategy_id, entry_price, candles_window):
    if strategy_id == "sf_zone":
        return _sfzone_atr_exit_plan(entry_price, candles_window)
    return _scaled_exit_plan(entry_price)
```

| 전략 | 백테스트 청산 | 라이브 청산 (`exit_plan()` 메서드) |
|------|---|---|
| F존 | `_scaled_exit_plan` 공유 (고정 TP +3/+5/+7%, SL -1.5%, breakeven +1.0%) | TP+3%·TP+5%, SL -2%, 14:50, breakeven +1.5% |
| **SF존** | **`_sfzone_atr_exit_plan` (ATR 동적)** | TP+3%·TP+5%·TP+7%, SL -1.5%, breakeven +1.0% |
| gold_zone / swing_38 / scalping | `_scaled_exit_plan` 공유 | 각 전략 자체 정책 |

### SF존 ATR 청산 상세 (`_sfzone_atr_exit_plan`, intraday_simulator.py:485-522)

- ATR%(14봉) 측정 → `atr_clamped = clamp(ATR%, 0.015, 0.08)`
- **SL = -atr_clamped × 2.0** (정상 범위 -3% ~ -16%, sl_cap × 2 클램프)
- TP1 = atr_clamped × 1.5, TP2 = × 2.5, TP3 = × 3.5 (R:R 균형)
- 종목 변동성에 비례 → F존 고정 ±1.5%~+7% 가 변동성 큰 종목에 부적합한 문제 해소

### 시사점

- 백테스트 PnL이 라이브에 그대로 매핑되지 않음
- SF존만 ATR 동적 청산을 백테스트에 적용받아 백테스트가 라이브와 가까움
- F존은 백테스트(`_scaled_exit_plan`) ↔ 라이브(`exit_plan`) 차이가 큼 — 백테스트에서 F존 PnL이 라이브와 어긋날 가능성

---

## 4. F존 vs SF존 핵심 차이 요약

| 측면 | F존 | SF존 |
|------|-----|-----|
| 진입 점수 임계 | ≥ 4.0 | ≥ 7.0 (F존의 강한 부분집합) |
| 기준봉 강도 | gain ≥3%, vol ≥2x | gain ≥5%, vol ≥3x |
| 신호 빈도 | 보통 | 매우 적음 (F존의 ~30%) |
| 백테스트 청산 | 고정 +3/+5/+7%, SL -1.5% | **ATR 기반 동적** |
| 라이브 청산 (전략 메서드) | TP+3%/TP+5%, SL -2% | TP+3%·+5%·**+7%**, SL -1.5% |
| 변동성 필터(운영) | 0.035 ON | OFF |
| 포지션 비중 | 30/20/10% | **35/25/10%** |

---

## 5. 실측 회고 (2026-05-14 ~ 05-16 세션)

### 5/14 종가 진단 (운영 종목 8개)

- **F존: 1건 발화** (한화생명 088350, score 8.66)
  - 기준봉 +3.3% / 거래량 4.7x
  - 눌림 -1.4% (얕음, 점수 ↑)
  - 5일 이평선 지지
  - 반등 +10.6% / 거래량 5.2x
- **SF존: 0건** — 점수 7.0+ 충족 종목 없음 (한화생명 score 8.66도 기준봉 +3.3%로 SF존 5% 미달)

### 600봉 백테스트 (5/14 기준, P0 SL 우선 + 청산 누락 버그 수정 적용)

| 전략 | trades | win% | total_pnl | **pnl/trade** |
|------|---:|---:|---:|---:|
| F존 | 32 | 51.9% | -53,006 | -1,656 |
| **SF존** | **9** | **72.7%** | **+336,988** | **+37,443** |

- **SF존이 신호는 1/3 수준이지만 거래당 효율 22배** — 강도 조건 필터링 + ATR 동적 청산 시너지
- F존은 P0(SL우선·청산 누락) 적용 후 적자 — 백테스트 `_scaled_exit_plan`이 라이브 `exit_plan`과 다른 영향 가능 (TP 폭은 같지만 SL은 -1.5% vs -2%, breakeven은 +1.0% vs +1.5%)

### 강세 종목 600봉 (5/16, 강세 10종목 — 159010 등)

- F존: **+1,723,978** (전략 2위, gold_zone 다음) — 강세장에서 눌림목 매수 잘 작동
- SF존: 0건 — 강세장에서도 강도 조건 충족 종목 없음 — 신호 희소성이 강세장에서도 유지

---

## 6. 시사점 및 후속 검토

1. **F존은 신호 빈번·효율 낮음, SF존은 신호 희소·효율 최고**
   - 포트폴리오에서 SF존 비중 ↑ 고려 가치 (단 신호 자체가 안 나서 실효 제한적)
   - SF존 강도 조건 일부 완화(예: score ≥ 6.5, 기준봉 ≥ 4.5%) 검토 가치

2. **F존의 백테스트 청산을 ATR화하면 효율 ↑ 가능성** ★ **다음 실험**
   - SF존이 ATR로 효율 본 패턴을 F존에 적용
   - `_exit_plan_for_strategy(strategy_id, ..., f_zone_atr=True)` 옵션 추가 + 비교 실험
   - 결과는 [F-zone-atr-exit-experiment](../04-report/features/F-zone-atr-exit-experiment.md) 에 기록

3. **변동성 필터 0.035는 운영 검증 완료** — LG전자급 저변동·고가주 SL 노이즈 차단 효과 ([LESSON_S1_NORMALIZATION.md](../../analysis/imports/2026-05-13/LESSON_S1_NORMALIZATION.md))

4. **점수 산식의 비대칭** — 이평선 지지(2점, 선택)는 미지지 시 0점이지만 진입 임계 4.0 영향이 큼. 가중치 재검토 가치

5. **백테스트·라이브 청산 분기는 의도적이지만 인지 필요**
   - 운영자 측: 실거래 시 라이브 청산(`exit_plan`)
   - 검증 측: 시뮬 결과는 백테스트 청산(`_exit_plan_for_strategy`)이 적용됨
   - 둘의 갭을 좁히려면 `_exit_plan_for_strategy`를 전략별 `exit_plan()`을 호출하도록 통합 (대대적 리팩터)

---

## 참조

- 진입 5단계 패턴 원본: [thetrading2021 (서희파더 이재상)](https://cafe.naver.com/thetrading2021)
- 선행: [BAR-46 F존 v2 분석](bar-46-f-zone-v2.analysis.md), [BAR-47 SF존 분리 분석](bar-47-sf-zone-split.analysis.md)
- 600봉 백테스트 리포트: [analysis/imports/2026-05-13/REPORT_600BARS_*.md](../../analysis/imports/2026-05-13/)
- 시뮬레이터 트레이딩뷰 정확도: [BAR-OPS-35 report](../04-report/features/BAR-OPS-35-report.md)
- P0~P3 + market_regime 메모리: `~/.claude/projects/-Users-beye-workspace-BarroAiTrade/memory/project_simulator_roadmap.md`
