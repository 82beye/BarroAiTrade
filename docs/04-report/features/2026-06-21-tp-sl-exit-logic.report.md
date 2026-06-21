---
tags: [report, exit-logic, tp-sl, audit, strategy/all, status/final]
---

# 기존 시스템 TP/SL 수익률 청산 로직 — 코드 감사 리포트

> **Project**: BarroAiTrade · **Date**: 2026-06-21 · **Status**: Final (read-only 코드 감사, 파일:라인 근거)
> **목적**: 자동매매 시스템에 이미 구현돼 있는 익절(TP)/손절(SL)·트레일링·시간청산 등 **수익률 청산 로직 전체**를 코드 기준으로 정리. 종베(closing_bet) 고도화·비용 정정 논의의 토대.
> **연관**: [[2026-06-18-closing-bet-auto-execution.design]] · `docs/operations/strategy-restart-toggles.md`

---

## 0. Executive Summary — 한 줄 결론

청산 로직은 **두 세계로 분리**돼 있고, 같은 전략이라도 레이어마다 SL 숫자가 다르다.

| 세계 | 코드 | SL 예(f_zone) | 기준 | 실제 작동 |
|------|------|--------------|------|-----------|
| **백테스트 세계** | `ExitEngine` + 전략 `exit_plan()` | **−2%** | 분봉 close | 시뮬레이터 전용 |
| **실거래 세계** | `HoldingEvaluator`(STRATEGY_EXIT_PROFILES) | **−4%** | 브로커 평가손익률(gross) | ★라이브 매도 지배 |

**핵심 발견 3가지:**
1. **ExitEngine은 라이브에서 호출되지 않는다**(데몬 4개 모두 참조 0건, `live_trading.py:4`에 "설계 구상"으로만 남고 미실장). 백테스트에서 본 SL −2%는 실거래에 적용되지 않는다.
2. **실거래 SL이 백테스트보다 2~2.5%p 더 너그럽다**(f/sf/gold: −4%). 의도된 2차 안전망(`holding_evaluator.py:80-94`)이지만, 백테스트 손절폭을 실거래 손실폭으로 해석하면 안 된다.
3. **TP/SL 임계가 전부 gross(비용 미차감) 기준**이다. 왕복 비용(모델 0.55% / 실측 역산 ~0.9%)은 사후 audit에서만 차감 → 한계 셋업은 gross 흑자·net 적자가 구조적으로 발생.

---

## 1. 청산 트리거 6종 & 평가 우선순위

`ExitEngine.evaluate`(`backend/core/execution/exit_engine.py:24-164`)와 라이브 `HoldingEvaluator.evaluate_all`(`backend/core/risk/holding_evaluator.py:262-417`)이 공유하는 평가 순서:

| 순위 | 트리거 | 동작 |
|------|--------|------|
| 1 | **max_hold_days** 도달 | 전량 강제 청산(TIME_EXIT) |
| 2 | **min_hold_days** 미달 | 모든 청산 평가 **차단**(고점만 갱신) |
| 3 | **time_exit** | 시각 도달 시 전량(예: 14:50 정규장 청산) |
| 4 | **Stop Loss** | trail SL > breakeven SL > 시간단계 SL > 고정 SL 중 가장 보수적 채택 |
| 5 | **Take Profit** | 가격 단계별 **분할** 청산(오름차순) |
| 6 | **breakeven_trigger** | TP1 발동 **후에만** SL을 진입가+α로 상향 |

SL 결정 우선순위 근거: `exit_engine.py:81-102`.

---

## 2. 공통 엔진 구조 — `ExitPlan` dataclass

`backend/models/strategy.py:139-210` (frozen dataclass):

| 필드 | 의미 |
|------|------|
| `take_profits` | `TakeProfitTier(price=절대가, qty_pct=수량비율)` 리스트, 최대 3단 분할익절. 가격 자동 오름차순 정렬(`strategy.py:177`) |
| `stop_loss` | `fixed_pct` + 선택적 `trailing_pct` + `time_stages`(예: 2분 −1.5% / 5분 −2% / 이후 −2.5%) |
| `time_exit` | 강제 시간청산 시각(예: `dtime(14,50)`) |
| `breakeven_trigger` | TP1 도달 후 SL을 `진입가×(1+trigger)`로 상향(이익 잠금) |
| `trail_stages` | 5단계 변동성 트레일링(아래) |
| `min_hold_days` / `max_hold_days` | 보유기간 게이트(swing_38만 활성) |

**트레일링 기본값** `TRAIL_STAGES_AITRADE`(`strategy.py:802-808`) — 수익 클수록 타이트하게 추종:

| peak 수익률 | 청산선 |
|-------------|--------|
| +5% ↑ | peak × 0.99 (−1%) |
| +4% ↑ | peak × 0.988 (−1.2%) |
| +3% ↑ | peak × 0.985 (−1.5%) |
| +2% ↑ | peak × 0.98 (−2%) |
| +1.5% ↑ | peak × 0.975 (−2.5%) |

상태머신: `high_water_mark`(peak)는 상향만, `trail_sl_for_peak()`이 peak 기준 offset 청산가 산출(`strategy.py:148-209`). **백테스트 기본 ON**(`intraday_simulator.py:650-651` `_scaled_exit_plan`).

---

## 3. 전략별 TP/SL 숫자 — 3개 레이어 비교

같은 전략이라도 **레이어마다 SL이 다르다.** ★표시가 **실거래를 실제로 지배**하는 값.

| 전략 | 전략 native `exit_plan()` (백테스트) | ★라이브 HoldingEvaluator | 실거래 청산 주체 |
|------|--------------------------------------|--------------------------|------------------|
| **f_zone** | TP +3%/+5%, **SL −2%**, 14:50, BE +1.5% | TP 5%, partial 3%, **SL −4%**, trail 3.5/1.0, BE 2.5 | HoldingEvaluator |
| **sf_zone** | TP +3%/+5%/+7%(ATR동적), **SL −1.5%**, 14:50, BE +1% | TP 7%, partial 3%, **SL −4%**, trail 3.0/1.5, BE 2.0 | HoldingEvaluator |
| **gold_zone** | TP +2%/+4%, **SL −1.5%**, 14:50, BE +1% | TP 4%, partial 2%, **SL −4%**, trail 3.0/1.0, BE 2.5 | HoldingEvaluator |
| **swing_38** | TP +20%/+50%, **SL −15%**, min3/max20, BE +10% | TP 50%, partial 20%, **SL −15%**, trail 20/5, BE 10 | HoldingEvaluator (값 일치) |
| **supertrend** | TP 없음(트레일만), SL −1~−8% ATR trail | — (HoldingEvaluator 제외) | **자체 트레이더**(시그널 기반) |
| **limit_up_chase** | — | — (HoldingEvaluator 제외) | **자체 트레이더**(Runner+EOD) |
| **closing_bet(종베)** | TP +4.5%(대형 +2%), SL −3% or fib0.618, 익일10:00, max3 | (정의 존재하나 평가 제외) | **알림 전용 / 수동** |
| (보조) ob_scalp | TP=비용커버+2틱, SL −3틱, 15:10 | — | — |
| (보조) scalping_consensus | TP +1.5%/+3%, SL −1%, 14:50 | — | — |

> 파일:라인 근거 — f_zone `f_zone.py:317-338` / sf_zone `sf_zone.py:58-83` / gold_zone `gold_zone.py:232-246` / swing_38 `swing_38.py:260-273` / closing_bet `closing_bet.py:70-77,181,303-332` / supertrend `supertrend.py:267-499` / 라이브 프로파일 `holding_evaluator.py:99-163`.

**핵심 격차**: f/sf/gold는 백테스트에서 −1.5~−2%로 손절되지만 **실거래는 −4%까지 버틴다**. `holding_evaluator.py:80-94`에 "ExitEngine 누락·분봉 fetch 실패 대비 2차 안전망"으로 의도 명시. swing_38만 −15%로 일치.

---

## 4. 실거래를 실제로 지배하는 3개 청산 경로

**ExitEngine은 백테스트 전용**(grep 결과 `intraday_simulator.py:22,330`·테스트만 import; `intraday_buy_daemon.py`·`evaluate_holdings.py`·`run_telegram_bot.py`·`supertrend_auto_trader.py`·`limit_up_chase_trader.py` 모두 참조 0건). 실거래 매도는 아래 3경로:

### 경로 1 — 일반 단타(f/sf/gold/swing_38): HoldingEvaluator
`scripts/evaluate_holdings.py`(cron) → `evaluate_all()`(`holding_evaluator.py:171`) → 브로커 `kt00018` **평가손익률(prft_rt, gross)**로 STRATEGY_EXIT_PROFILES SL(−4%)·TP·트레일·분할익절 판정 → `LiveOrderGate.place_sell`(`evaluate_holdings.py:261-262`).

### 경로 2 — supertrend: 자체 트레이더 (HoldingEvaluator 제외)
`backend/core/supertrend_auto_trader.py:309-395`. 우선순위: Hard Stop(기본 OFF) → Trail(고점−3×ATR) → 이월 갭스톱(−3%) → TP(+5%) → 5분봉 **Supertrend SELL 시그널**(2봉) → Runner. **TP 없는 순수 트레일/시그널 전략.** 제외 근거: `evaluate_holdings.py:310-311` `--exclude-strategy supertrend`.

### 경로 3 — limit_up_chase(상따): 자체 트레이더 (HoldingEvaluator 제외)
`backend/core/limit_up_chase_trader.py:175-233`. EOD 강제청산(15:15) → Trail → Hard Stop → **Runner 강제 ON**(고점추종) → TP → 시초갭 부분익절.

---

## 5. 수익률·비용 반영 — 구조적 한계

- **라이브 TP/SL 임계는 전부 gross(수수료·세금 미차감) 기준.** 브로커 평가손익률을 그대로 비교(`holding_evaluator.py:353`). 시장가 체결 비용은 예측 불가라 임계에 미반영.
- **net 수익률은 사후 audit에서만 계산**: `_daily_strategy_audit.py:44-116` FIFO 왕복 손익에서 차감. 비용 모델 = 편도 수수료 **0.175%**(`trading_costs.py:24` `COMMISSION_RATE`) + 매도세 **0.20%**(`:26`) → **왕복 0.55%**(`ROUND_TRIP_COST_RATE`). env `BARRO_COMMISSION_RATE`로 오버라이드.
- **함의**: TP +3% 익절도 비용 0.55%(실측 역산 ~0.9%) 차감 후 net이 그만큼 줄어든다. **임계가 비용을 고려하지 않아 gross 흑자·net 적자**가 한계 셋업에서 발생(메모리상 "4일 연속 비용이 손익을 결정"한 구조와 동일).
- **LiveOrderGate**(`live_order_gate.py:155-204`): SL 매도는 일일손실 한도를 **무조건 통과**(손절은 막지 않음), 단 `LIVE_TRADING_ENABLED`·dry_run·일일매수 한도는 적용.

---

## 6. 전역 정책 vs 전략별 프로파일

`data/policy.json`(현재 운영값): `stop_loss_pct −2.0%`, `take_profit 5.0%`, `daily_loss_limit −100%`(사실상 OFF), `daily_max_orders 300`, `max_per_position 10%`. `PolicyConfig` 기본값은 SL −4.0%(`policy_config.py:17`)·daily_loss_limit −3.0%. evaluate_holdings는 CLI `--tp/--sl` 미지정 시 policy.json 로드하되, **전략을 알면 STRATEGY_EXIT_PROFILES(−4%)가 우선**하고 policy.json은 force-mode/fallback(`evaluate_holdings.py:123-136`).

---

## 7. 종베(closing_bet)의 위치

종베는 **모든 자동 청산에서 완전 제외**(`evaluate_holdings.py:90-96`이 `closing_bet_positions.json`으로 필터). 현재 **알림 전용**이며, §3의 종베 파라미터(TP +4.5%/대형 +2%, SL −3% or fib0.618, 익일 10:00, max3)는 `closing_bet.py`에 **정의만 존재**·라이브 자동집행 미연결. 자동 종베 설계의 트레일링 청산은 이 기존 인프라가 아니라 **종베 전용 신규 모니터**로 가게 설계됨([[2026-06-18-closing-bet-auto-execution.design]]).

---

## 8. 주목할 점 (시사점)

1. **백테스트 SL ≠ 라이브 SL** (f/sf/gold: −2% vs −4%). 백테스트 손절 결과를 실거래 손실폭으로 읽으면 안 됨 — 실거래는 2배 더 깊게 버팀.
2. **ExitEngine(정밀 분봉 엔진) 미실장.** 실거래는 시간단위 브로커 평가손익률 폴링이라, 장중 분봉 급락 시 −4% 도달 전 실제 체결가가 더 미끄러짐(메모리상 손절슬립 평균 −1.3%p와 일치).
3. **TP/SL이 gross 기준** → 비용(왕복 0.55~0.9%)이 임계에 미반영. 타이트한 TP(+1.5~+3%)일수록 net 잠식 비율 큼.
4. **전략마다 청산 주체가 제각각**(HoldingEvaluator / supertrend 자체 / limit_up_chase 자체 / 종베 수동) — 단일 청산 엔진 아님.

---

## 9. 후속 개선 후보 (참고 — 본 리포트는 감사만, 변경 없음)

| # | 후보 | 분류 | 비고 |
|---|------|------|------|
| 1 | 백테스트−라이브 SL 정합(또는 ExitEngine 라이브 실장) | (d) 실거래 파라미터 | HITL — 손실폭 직접 변경 |
| 2 | TP/SL 임계를 net 기준으로(비용 가산) | (d) 실거래 파라미터 | HITL — 선정/청산 동작 변경 |
| 3 | `COMMISSION_RATE` 실측 2배 과소 정정(0.00175→0.0035) | (d) 실거래 파라미터 | HITL — 메모리 기존 발견, code-surgeon 위임 준비됨 |
| 4 | 청산 주체 분산 구조 문서화(운영 RUNBOOK) | (c) 안전 개선 | 자동 구현 가능 |

> 실거래 숫자 변경(1~3)은 `barrotrade-code-surgeon` 위임 + AskUserQuestion 승인 후에만((d) HITL). 본 리포트는 **감사 보고이며 어떤 청산 파라미터도 변경하지 않음.**
