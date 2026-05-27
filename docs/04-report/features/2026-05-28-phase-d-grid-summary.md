# BAR-OPS-09 Phase D 그리드 서치 시뮬 시리즈 종합 리포트

**기간**: 2026-05-27 ~ 2026-05-28
**브랜치**: BAR-OPS-09
**최종 커밋**: (S8 패치 커밋 — 본 문서 작성 시점 push 예정)
**스코프**: swing_38 전략 청산 정책 + 진입 필터 그리드 서치, 통합 8단계(S0~S8)

---

## 1. Executive Summary

| 항목 | Phase D (5/27 시작값) | **Phase D2 (5/28 권장)** | 시뮬 자본가중 |
|---|---:|---:|---:|
| TP1 (50% 매도) | +20% | **+20% (유지)** | — |
| TP2 (전량 매도) | +50% | **+50% (유지)** | — |
| SL | **−10%** | **−15%** ← 변경 | +203% (자본가중 +0.597 → +1.808%) |
| breakeven_trigger | +10% | +10% (유지) | — |
| min_hold_days | 3 | 3 (유지) | — |
| max_hold_days | **8** | **20** ← 변경 | (위 결합 포함) |
| **min_atr_pct (진입 필터)** | **0.0** | **0.03** ← 활성 | +2.7% 추가 (자본가중 +1.808 → +1.857%) |

**최종 시뮬 자본가중**: **+1.857%** (Phase D baseline +0.597% 대비 **+211%, 약 3.1배**)

---

## 2. 시뮬 시리즈 단계별 진행 (S0~S8)

| # | 단계 | 핵심 발견 | 산출물 |
|---:|---|---|---|
| **S0** | 5/27 운영 로그 분석 | 메인 백엔드(PID 68238) 4/26부터 미재시작, 텔레그램봇(PID 3638) 5/26부터 미재시작 → 5/27 매매 0건 | (텍스트 진단) |
| **S1** | Phase D 코드 패치 | swing_38 TP1+20/TP2+50/SL−10/BE+10 + `add_on_signal()`(D+1~D+5 기준봉 지지 2차 분할 진입) | commit `a518762`, BAR-OPS-09 push |
| **S2** | Phase D 기본 시뮬 (2,952종 × 일봉 400봉) | 8,279 trades, **1차 +0.645%·승률 41.1%·자본가중 +0.785%** / 2차 미발생 그룹 평균 −8.54%·승률 3.43% (사전 손절 필터 가치 발견) | `swing38_phase_d_sim_*.{json,md}` |
| **S3** | TP1 그리드 (10/15/20%) | TP1↑ → 수익↑·승률↓ 단조. **TP1=+20%가 자본가중 +0.787% 최대** (Phase D 적용값 일치) | `..._grid_tp1_*.{json,md}` |
| **S4** | TP2 / SL / max_hold 1D 그리드 (14셀) | TP2 변경 거의 무영향 / **SL=−5% 단독 최적 +0.864%** / **max_hold=D+20 단독 최적 +1.096%** | `..._grid_all_*.{json,md}` |
| **S5** | SL × max_hold 2D 결합 (5×4=20셀) | **결합 최적 = SL=−15% × D+20 = +1.808%** (baseline +203%) — **단일 최적 결합과 정반대 방향**, 강한 interaction | `..._grid_sl_maxhold_*.{json,md}` |
| **S6** | TP1 × SL × max_hold 3D 결합 (3×5×4=60셀) | TP1=+20%×SL=−15%×D+20=**+1.808% 전역 자본가중 최대** / TP1=+10%×SL=−15%×D+20=**승률 53.40%** (시리즈 최고) — TP1이 두 KPI의 다이얼 | `..._grid_3d_*.{json,md}` |
| **S7** | 진입 필터 시뮬 (11 시나리오) | 결합 최대 자본가중 +1.909% (score≥5+atr≥5%+early) / **간결 최적 = ATR≥3% 단독 = +1.857%** (운영 복잡도 ↑ 없음) / **승률 80% KPI 도달 불가 확정** (모든 셀 41.6~43.2%) | `..._filters_*.{json,md}` |
| **S8 ← 현재** | 결합 최적으로 swing_38 Phase D2 코드 패치 + 본 종합 리포트 + paper trading 설계 | swing_38.py default `max_hold_days=20`·`min_atr_pct=0.03`, exit_plan SL=−15%, STRATEGY_EXIT_PROFILES 동기화, IntradaySimulator 동기, 회귀 1053 passed | (본 문서 + 커밋 예정) |

---

## 3. 결정적 발견 7개

### 3-1. 결합 최적 ≠ 단일 변수 최적의 결합 (S5)

| 시나리오 | 자본가중 |
|---|---:|
| Phase D baseline (SL=−10% × D+8) | +0.597% |
| SL 단일 최적 (SL=−5% × D+8) | +0.864% (+45%) |
| max_hold 단일 최적 (SL=−10% × D+20) | +1.096% (+84%) |
| **단일 최적 단순 결합** (SL=−5% × D+20) | +0.942% (+58%) ⚠ 예상 못 미침 |
| **2D 결합 최적** (SL=−15% × D+20) | **+1.808% (+203%)** ★ |

> SL=−5%로 빠르게 컷한 trade는 D+20 시간을 활용할 기회 자체가 없음 → SL 강화(−15%)로 큰 손실까지 견뎌야 max_hold 확대 이점이 발동.

### 3-2. TP1이 수익률·승률 두 KPI의 다이얼 (S6)

| TP1 면 | (SL=−15%, D+20) 자본가중 | 승률 |
|---:|---:|---:|
| TP1=+10% | +1.231% | **53.40%** ★ 시리즈 최고 |
| TP1=+15% | +1.497% | 46.98% |
| TP1=+20% | **+1.808%** ★ | 43.15% |

> **모든 TP1 면에서 (SL=−15%, D+20) 셀이 자본가중·승률 동시 면별 최대** — robust한 결합점 확정. TP1만 페르소나에 맞게 선택.

### 3-3. TP2 변화는 거의 무영향 (S4)

TP2 +40%~+100% 5셀 자본가중 +0.586~+0.616% (Δ 5% 이내). 승률 모든 셀 동일(40.97%). TP1·TP2 도달 합산이 3% 미만이라 TP2 임계 변경 효과 미미.

### 3-4. min_score 강화는 모든 KPI 악화 (S7)

| min_score | n | 1차 평균 | 자본가중 |
|---:|---:|---:|---:|
| 3.0 (baseline) | 6,397 | +0.857% | +1.808% |
| 5.0 | 5,710 | +0.786% | +1.637% (−9.5%) |
| 6.0 | 4,688 | +0.646% | +1.333% (−26.3%) |
| 7.0 | 3,118 | +0.601% | +0.967% (−46.5%) |

> **default min_score=3.0 이 이미 정보 손실 없는 충분히 좋은 임계**. 강화하면 약한 시그널의 우연한 익절 trade까지 함께 제거되어 평균이 하락.

### 3-5. ATR≥3% 단독 추가가 cost-effective 최적 (S7)

자본가중 +1.808% → +1.857% (+2.71%), 진입 수 6,397 → 6,095 (−4.7%), 승률 43.15 → 42.87% (거의 무변화). 운영 복잡도 ↑ 없이 진입 품질만 향상.

### 3-6. 2차 미발생 D+2 사전 손절 효과 marginal (S7)

자본가중 +1.808% → +1.791% (−0.94%). 영향 받는 trade가 6,397건 중 250건뿐(3.9%)이라 운영 통합 가치 낮음.

### 3-7. 승률 80% KPI는 swing_38 단독으로 달성 불가 확정

모든 그리드(TP1/TP2/SL/max_hold/min_score/min_atr/조기 손절) 결합에서 1차 승률 최대 53.40% (TP1=+10%×SL=−15%×D+20, S6). 사용자 메모리의 "수익률 우선 + 승률 80%" 중 승률 KPI는 **다른 전략 앙상블** 또는 **별도 진입 게이트 BAR 작업**으로 풀어야 함.

---

## 4. Phase D2 권장 셀 — 페르소나별

| 페르소나 | 권장 셀 | 자본가중 | 1차 승률 | Phase D 대비 |
|---|---|---:|---:|---|
| **간결 (default 권장)** ★ | TP1+20% · SL−15% · D+20 · **ATR≥3%** | **+1.857%** | 42.87% | 자본 **+211%**, 승률 +1.90%p |
| 수익률 최우선 (복잡) | TP1+20% · SL−15% · D+20 · ATR≥5% + score≥5 + early-exit | +1.909% | 41.82% | 자본 +220%, 운영 복잡 ↑ |
| 균형 | TP1+15% · SL−15% · D+20 · ATR≥3% | ~+1.55% (추정) | ~46% (추정) | S6+S7 결합 추가 시뮬 가치 |
| 승률 우선 | TP1+10% · SL−15% · D+20 · ATR≥3% | ~+1.27% (추정) | ~53% (추정) | S6+S7 결합 추가 시뮬 가치 |

**본 패치는 "간결" 페르소나 적용** — `Swing38Params.min_atr_pct=0.03 / max_hold_days=20`, `exit_plan` SL=−15%, `STRATEGY_EXIT_PROFILES['swing_38']` 동기화. TP1/TP2/BE/min_hold/2차 진입 룰은 Phase D 유지.

---

## 5. Phase D2 코드 변경 (S8 패치)

### 5-1. `backend/core/strategy/swing_38.py`

| 필드 | Phase D | Phase D2 |
|---|---:|---:|
| `Swing38Params.min_atr_pct` | 0.0 | **0.03** |
| `Swing38Params.max_hold_days` | 8 | **20** |
| `exit_plan().stop_loss.fixed_pct` | −0.10 | **−0.15** |
| `exit_plan().breakeven_trigger` | 0.10 (유지) | 0.10 |
| `exit_plan().take_profits` | TP1×1.20 / TP2×1.50 (유지) | TP1×1.20 / TP2×1.50 |

### 5-2. `backend/core/risk/holding_evaluator.py`

`STRATEGY_EXIT_PROFILES['swing_38']`:
| 필드 | Phase D | Phase D2 |
|---|---:|---:|
| `stop_loss_pct` | −10.0 | **−15.0** |
| `tightened_sl_pct` | −5.0 | **−15.0** (시뮬 동기화 — 강화 미발동) |
| `max_hold_days` | 8 | **20** |
| (그 외) | (TP/partial/trailing/BE 유지) | 유지 |

### 5-3. `backend/core/backtester/intraday_simulator.py`

`_build_strategies('swing_38')`: `max_hold_days=8 → 20`.

### 5-4. 테스트 갱신

- `test_swing_38.py`: `test_default_filter_disabled` → `test_default_filter_enabled_d2`, exit_plan 값 검증 3건 (`-0.10→-0.15`, `max_hold 8→20`), `Swing38Params` default 검증 갱신
- `test_holding_evaluator.py`: profile 검증 `stop_loss −10→−15`, `tightened_sl −5→−15`, `max_hold 8→20`
- `test_signal_scanner_phase_c.py`: SignalScanner.swing_38 default `max_hold 8→20`

### 5-5. 회귀

**1053 passed / 10 skipped / 0 failed** — Phase D (1053) 결과와 동일, 회귀 깨끗.

---

## 6. Paper Trading 6주 Forward Test 설계

### 6-1. 목적
시뮬상 자본가중 +1.857% (Phase D2 예상)이 향후 시장 regime 변화에도 견고할지 라이브 일봉 데이터로 검증.

### 6-2. 기간
2026-05-29 (목, 익일) ~ 2026-07-10 (금, 6주). 약 30 영업일.

### 6-3. 시뮬 vs 실제 비교 KPI

| KPI | 시뮬 기대값 (S7 ATR≥3%) | 라이브 측정 |
|---|---:|---:|
| 1차 평균 PnL% | +0.820% | 측정 |
| 1차 승률 | 42.87% | 측정 |
| 합산 평균 PnL% | +0.600% | 측정 |
| 자본가중 PnL% | +1.857% | 측정 |
| TP2 도달률 | 3.0% (S2 기준) | 측정 |
| SL hit률 | 28.0% (S5 기준) | 측정 |
| Max Hold 비중 | 65.5% (S2 기준) | 측정 |
| 평균 보유 일수 | ~10일 (D+20 가정) | 측정 |

### 6-4. 운영 절차

1. **사전**: BAR-OPS-09 → main 머지 → 운영 머신 git pull → **PID 68238 + PID 3638 재기동** (S0 인시던트 해결 — Phase D2 메모리 반영)
2. **일별**: 새 시그널 발생 종목 logs/decisions/<date>.jsonl 기록, ExitEngine 청산 사유별 추적
3. **주별**: 통계 집계 → 시뮬 기대값과 Δ 비교
4. **6주 후**: 최종 KPI 비교 표 + 회귀 분석 + Phase D3 결정

### 6-5. Stop conditions (조기 종료)

- 자본가중 누적 −3% 도달 시 즉시 일시 정지 (daily risk budget 보호)
- 첫 2주 1차 승률 <30% 또는 자본가중 <−1% 시 시뮬 가정 깨짐 → 재진단

### 6-6. 시뮬 한계 — Forward Test로 검증할 항목

- entries dedupe (max_hold_max=20) → 실제 거래 빈도와 차이
- 슬리피지·수수료 미반영 (시뮬 가격 = 일봉 종가)
- 갭 갭다운 시 SL=−15% 초과 손실 가능성 (S2의 −34% min 셀)
- D+20 자본 회전율 ↓ → 동시 보유 종목 수 ↑ (`max_concurrent_positions` 충돌 가능)

---

## 7. 다음 단계 (S9 후보)

1. **승률 KPI 별도 작업** — F존/Blue Line 등 다른 전략과 swing_38 앙상블 또는 진입 게이트 강화 (별도 BAR)
2. **균형/승률 페르소나 그리드** (TP1=10/15 × ATR≥3% 결합 시뮬) — 정확한 자본가중·승률 측정
3. **S5 결합 최적의 다른 종목군 검증** — KOSPI200 only, KOSDAQ small-cap only 등 segment별 시뮬
4. **2차 진입 dispatch 운영 통합** (별도 BAR) — orchestrator/signal_scanner가 `Swing38Strategy.add_on_signal()` 호출 + `base_candle_low` 주입 ProgressBar
5. **min_hold_days 그리드** (1/2/3/5일) — S2의 D+3 SL 집중 회피 가능성

---

## 8. 산출물 목록

| 파일 | 단계 |
|---|---|
| `analysis/imports/2026-05-27/swing38_phase_d_sim.py` | S2 |
| `analysis/imports/2026-05-27/analyze_trades.py` | S2 |
| `analysis/imports/2026-05-27/grid_tp1.py` | S3 |
| `analysis/imports/2026-05-27/grid_all.py` | S4 |
| `analysis/imports/2026-05-27/grid_sl_maxhold.py` | S5 |
| `analysis/imports/2026-05-27/grid_3d.py` | S6 |
| `analysis/imports/2026-05-27/grid_filters.py` | S7 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_sim_20260527_225255.{json,md}` | S2 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_split_analysis.txt` | S2 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_grid_tp1_20260527_230827.{json,md}` | S3 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_grid_all_20260527_231853.{json,md}` | S4 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_grid_sl_maxhold_20260527_232730.{json,md}` | S5 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_grid_3d_20260527_233740.{json,md}` | S6 |
| `analysis/imports/2026-05-27/reports/swing38_phase_d_filters_20260527_234827.{json,md}` | S7 |
| `docs/04-report/features/2026-05-28-phase-d-grid-summary.md` (본 문서) | S8 |

---

## 9. 운영 적용 절차 (S0 인시던트 해결과 함께)

1. `git push origin BAR-OPS-09` (S8 commit) — 본 패치는 BAR-OPS-09 브랜치
2. PR `BAR-OPS-09 → main` 작성 및 머지
3. **운영 머신 git pull** — main 브랜치 동기화 (S0에서 발견한 1개월 미적용 상태 해소)
4. **메인 백엔드 PID 68238 정상 종료** (uvicorn `:8000`) → workspace `.venv`로 재기동
5. **텔레그램 봇 PID 3638 정상 종료** → main 브랜치 코드로 재기동 (Telegram API token 검증 선행 — 22시간 retry 인시던트 별개 해결)
6. Phase D2 적용 첫 영업일 = 2026-05-29 (목)부터 Forward Test 6주 시작
