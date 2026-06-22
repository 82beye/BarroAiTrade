# BarroAiTrade 매매전략 심층 리포트 — limit_up_chase (상한가 추격)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: 🟡 별도경로(opt-in, default OFF·dry_run ON) — SignalScanner 미등록·전용 루프 배선 존재이나 환경변수 미설정 시 비가동 · 분류: 인트라데이~오버나잇(상한가 추격) · 컨셉: 등락률 +20~27% 모멘텀 밴드 + 호가 매수벽으로 상한가 직전 진입 → RUNNER(캡상승 수익극대화)로 청산

## 1. 요약 (TL;DR)

- **상따(상한가 따라잡기)** = `SupertrendAutoTrader` 를 상속한 **별도 트레이더**(`backend/core/limit_up_chase_trader.py:58`). 진입만 상따 전용으로 교체하고, 청산은 부모의 RUNNER(캡상승) 헬퍼를 100% 재사용한다(`limit_up_chase_trader.py:2-4`).
- **진입 2-게이트**: ① 등락률 밴드 `entry_flu_min(20)~entry_flu_max(27)`% (`limit_up_chase_trader.py:271-278`) → ② ka10004 호가 매수벽(매수1호가 잔량금액 ≥ 1억원 AND top-3 매수/매도 비율 ≥ 3배) (`limit_up_chase_trader.py:280-320`). 데몬 글로벌 급등가드와 **독립 경로**.
- **청산**: 당일 RUNNER(상한가 홀딩→수익잠금 floor→고점되돌림) + 오버나잇 모드 시 익일 시가갭 부분익절. `daily` 모드면 `eod_close_time(15:15)` 강제청산 (`limit_up_chase_trader.py:175-233`, `257-269`).
- **실사용 판정 = 🟡 별도경로(미가동 기본값)**: `scripts/run_telegram_bot.py:811-824` 에 전용 루프가 배선돼 있으나 `LIMIT_UP_CHASE_ENABLED` truthy 일 때만 구성되고(`run_telegram_bot.py:684`), default OFF·`LIMIT_UP_CHASE_DRYRUN` default ON(`run_telegram_bot.py:688`). `.env.example` 에 해당 키 자체가 없어(SUPERTREND 항목만 존재, `.env.example:136`) 배포 템플릿상 **비활성**이다. 실 가동 여부는 운영 셸의 환경변수에 달려 코드만으로는 "현재 ON" 단정 불가 — **미검증(운영 env 의존)**.
- **비용**: 왕복 0.90% (편도 0.35%×2 + 매도세 0.20%, `backend/core/trading_costs.py:29-33`). 상한가 갭·유동성 리스크가 큰 전략이라 손익분기 위 마진 확보가 RUNNER 의 핵심 과제.

## 2. 전략 개요 (상한가 추격 컨셉·리스크 특성)

상한가(전일종가 +29~30%)에 근접한 **강모멘텀 종목을 상한가 직전 구간(+20~27%)에서 매수**하고, 상한가 락(매도벽 소진) 시 홀딩하여 익일 점상한가 갭까지 노리는 "캡상승 수익극대화" 전략이다. 설계 의도가 헤더 docstring 에 명시됨(`limit_up_chase_trader.py:1-15`):

- 진입은 모멘텀 밴드 + 호가 매수벽, 청산은 당일 RUNNER + 오버나잇 갭 부분익절.
- 포지션은 `strategy="limit_up_chase"` 로 태깅돼 supertrend/zone 포지션과 **상호 배타**(더블셀 방지) — `_STRATEGY_ID = "limit_up_chase"` (`limit_up_chase_trader.py:35`).
- **보수 안전장치**: 마스터 토글 default OFF, dry_run 우선, 동시 1~2종·소액·일일한도.

리스크 특성(코드 주석 근거):
- **백테스트 불가**: "호가 L2 이력이 없어 백테스트 불가 → 라이브 dry_run 검증 위주(ob_scalp.py 한계와 동일)" (`limit_up_chase_trader.py:15`). 즉 진입 게이트(호가 매수벽)는 사후 검증이 구조적으로 안 된다.
- **상한가 갭 리스크**: 상한가 미달 시 익일 갭하락 노출. 오버나잇 모드는 이 갭을 부분익절로 일부만 회수.

## 3. 진입 로직 (조건·게이트) — 코드 인용

`run_cycle()` 은 **청산을 먼저 수행한 뒤 진입**한다(`limit_up_chase_trader.py:68-81`). 진입 게이트 순서:

1. **세션 가드**: `market_hours_only` 이고 비정규장이면 사이클 스킵 (`limit_up_chase_trader.py:72-76`).
2. **진입 시간창**: `_entry_window_open()` — 부모 `_entry_time_open()`(start `09:05` default) AND `entry_end_time(14:00)` 이전 (`limit_up_chase_trader.py:84`, `240-255`). 장막판 추격(익일갭 리스크) 차단.
3. **슬롯 계산**: `max_positions - 보유 상따수`. 0 이하면 스킵 (`limit_up_chase_trader.py:87-92`).
4. **유니버스 루프** (`limit_up_chase_trader.py:94-125`): `_universe_provider()` → `universe_max` 컷. 종목별로:
   - 이미 보유/재진입 차단(`_reentry_blocked`)/레버리지·인버스/ETF·ETN 제외 (`:99-106`).
   - **(B) 모멘텀 밴드**: `_momentum_band_pass(cand)` — `entry_flu_min ≤ flu_rate ≤ entry_flu_max` (`:108`, `271-278`). 등락률은 picker 랭킹 메타에서 읽어 **추가 TR 호출 없이** 검사(429 절감).
   - 캔들 fetch → 현재가 `< min_price` 차단 (`:111-115`).
   - **(C) 호가 매수벽**: `_passes_orderbook_wall(symbol, bars)` — 모멘텀 통과 후보만 호가 1회 fetch (`:117-118`).
5. **리스크 게이트**: `evaluate_risk_gate(...)` 로 `max_per_position_ratio`/`max_total_position_ratio`/`max_concurrent_positions` 적용 (`limit_up_chase_trader.py:130-137`).
6. **주문**: `gate.recommendations` 순회 → `_cap_qty` → `place_buy(strategy_id="limit_up_chase")` → `create_from_order(..., single_tranche=True)` (sync-loss 방지) (`limit_up_chase_trader.py:140-168`).

### 3-1. 모멘텀 밴드 (`_momentum_band_pass`, `limit_up_chase_trader.py:271-278`)
```python
flu = float(getattr(cand, "flu_rate", 0) or 0)
return self.config.entry_flu_min <= flu <= self.config.entry_flu_max
```
- 상한 27%로 **이미 +30% 락 추격을 차단**(`:44`). 하한 20%로 상한가 근접 모멘텀만 (`:43`).

### 3-2. 호가 매수벽 (`_passes_orderbook_wall`, `limit_up_chase_trader.py:280-320`)
AND 2조건 (호가 미가용/실패 시 **보수적 False**, `:292-298`):
- **① 매수1호가 잔량금액** `= 매수1가 × 매수1잔량 ≥ wall_min_top_value(1억원)` — 거래대금 기준(고가주 대응) (`:304-309`).
- **② top-N 매수/매도 잔량 비율** `bq/aq ≥ wall_bid_ask_ratio(3.0)`, `wall_levels(3)` 단계. **매도 잔량 전무 시 통과**(상한가 락 임박) (`:310-320`).
- ⚠️ **회귀 주석**: 옛 '상한가 근접' 게이트(`wall_near_pct`)는 밴드 진입(+20~27%)이 상한가가격(+29~30%)에 닿을 수 없어 **모든 밴드 종목을 영구 탈락 → 상따 진입 0건**을 유발해 제거됨(원익IPS 2026-06-12 사례, `:286-290`). 따라서 `wall_near_pct`/`wall_min_top_qty` 는 **deprecated·미사용**(`:46-48`).

## 4. 청산 로직

`_run_exit_cycle()` 는 **limit_up_chase 태그 포지션만** 평가(`limit_up_chase_trader.py:175-178`). 종목별 우선순위(`:189-212`):

1. **오버나잇 갭 부분익절**: `_maybe_gap_partial(...)` 성사 시 이번 사이클 청산평가 스킵 (`:190`, `323-377`).
2. **EOD 강제청산**: `daily` 모드에서 `eod_close_time(15:15)` 이후면 `exit_now=True, "EOD청산(당일모드)"` — 상한가 락이라도 당일 정리 (`:193-194`, `257-269`). `overnight` 모드는 항상 False(`:259`).
3. **트레일/하드손절**: `_trail_hit`(부모) / `_hard_stop_hit`(`hard_stop_pct -4%`) (`:196-197`).
4. **RUNNER**: `_runner_triggered`(TP도달 | 상한가 | 시초갭) → `_runner_should_exit`(`supertrend_auto_trader.py:926-951`):
   - 상한가권이면 **"상한가 홀딩"**(되돌림 무시) (`supertrend_auto_trader.py:937-938`).
   - 진입가×(1+`runner_profit_lock_pct 2%`) floor 이탈 시 "러너 수익잠금" (`:940-942`).
   - 최고점 −`runner_giveback_pct 3%` 이탈 시 "추세이탈(고점되돌림)" (`:944-950`).
5. **고정 익절 보완**: runner 미트리거 + `take_profit_trail_only` 아닐 때만 `take_profit_pct(5%)` (`limit_up_chase_trader.py:204-207`).

청산 실행: 전량 `place_sell(strategy_id="limit_up_chase")` → 포지션 remove → 진입가 대비 종가 하락 시 `_loss_locked` 등록 (재진입 차단) (`:215-231`).

### 4-1. 익일 시가갭 부분익절 (`_maybe_gap_partial`, `limit_up_chase_trader.py:323-377`)
- 조건(전부): `runner_enabled` + `runner_gap_partial_ratio>0`, 부분익절 미완료, 개장 후 `runner_gap_partial_window_bars(6봉=30분)` 이내, **오버나잇 보유**(진입일 < 오늘, `:340-345`), 시가갭 ≥ `runner_gap_partial_min_pct(3%)`, 현재가 > 진입가.
- 동작: `held × ratio(0.5)` 매도 → 잔량 갱신 → `partial_tp_done=True` 마킹(멱등) (`:359-368`). place_sell 의 `strategy_id` 만 `limit_up_chase` 로 귀속(부모 로직과 동일, `:326-327`).

## 5. 파라미터 표

| 파라미터 | dataclass default | run_telegram_bot env default | env 키 | 인용 |
|---|---|---|---|---|
| entry_flu_min | 20.0 | 20 | LIMIT_UP_ENTRY_FLU_MIN | trader:43 / bot:726 |
| entry_flu_max | 27.0 | 27 | LIMIT_UP_ENTRY_FLU_MAX | trader:44 / bot:727 |
| wall_min_top_value | 100,000,000원 | 100000000 | LIMIT_UP_WALL_MIN_TOP_VALUE | trader:48 / bot:730 |
| wall_bid_ask_ratio | 3.0 | 3.0 | LIMIT_UP_WALL_BID_ASK_RATIO | trader:49 / bot:731 |
| wall_levels | 3 | (env 없음) | — | trader:50 |
| wall_near_pct / wall_min_top_qty | 1.0 / 50,000 | 1.0 / 50000 | (deprecated·미사용) | trader:46-47 |
| entry_start_time | 09:30(부모) | 09:05 | LIMIT_UP_ENTRY_START | parent:75 / bot:723 |
| entry_end_time | 14:00 | 14:00 | LIMIT_UP_ENTRY_END | trader:52 / bot:724 |
| overnight_mode | daily | daily | LIMIT_UP_OVERNIGHT_MODE | trader:54 / bot:742 |
| eod_close_time | 15:15 | 15:15 | LIMIT_UP_EOD_CLOSE | trader:55 / bot:743 |
| max_positions | 10(부모) | 1 | LIMIT_UP_MAX_POS | parent:54 / bot:715 |
| min_price | 1000(부모) | 2000 | LIMIT_UP_MIN_PRICE | parent:61 / bot:716 |
| max_per_position_ratio | — | 0.03 | LIMIT_UP_MAX_PER_POS_RATIO | bot:717 |
| max_order_qty / value | — | 5000 / 5,000,000 | LIMIT_UP_MAX_ORDER_QTY/VALUE | bot:718-719 |
| interval_sec | — | 90 | LIMIT_UP_INTERVAL_SEC | bot:714 |
| hard_stop_pct | — | -4.0 | LIMIT_UP_HARD_STOP | bot:733 |
| trail_atr_mult | — | 2.0 | LIMIT_UP_TRAIL_ATR | bot:734 |
| take_profit_pct | — | 5.0 | LIMIT_UP_TAKE_PROFIT | bot:735 |
| runner_limit_up_pct | 29.0(부모) | 29 | LIMIT_UP_RUNNER_LIMIT_UP | parent:190 / bot:738 |
| runner_profit_lock_pct | 2.0(부모) | 2 | LIMIT_UP_RUNNER_LOCK | parent:197 / bot:739 |
| runner_giveback_pct | 3.0(부모) | 3 | LIMIT_UP_RUNNER_GIVEBACK | parent:194 / bot:740 |
| runner_gap_partial_ratio | 0.0(부모) | 0.5 | LIMIT_UP_GAP_PARTIAL | parent:201 / bot:744 |
| runner_gap_partial_min_pct | 3.0(부모) | 3 | LIMIT_UP_GAP_PARTIAL_MIN | parent:202 / bot:745 |
| runner_gap_partial_window_bars | 6(부모) | 6 | LIMIT_UP_GAP_PARTIAL_WINDOW | parent:203 / bot:746 |
| daily_loss_limit_pct | — | -2.0 | LIMIT_UP_DAILY_LOSS_LIMIT | bot:696 |
| daily_max_orders | — | 6 | LIMIT_UP_MAX_ORDERS | bot:697 |
| 유니버스 picker min_flu_rate | — | 15 | LIMIT_UP_MIN_FLU | bot:705 |
| universe top_n | — | 15 | LIMIT_UP_UNIVERSE_TOP | bot:709 |

비고: 트레이더 `__init__` 이 `runner_enabled = True` 를 **강제 ON**(`limit_up_chase_trader.py:64-65`) — 청산은 항상 RUNNER 기반.

## 6. 활성·운영 상태 (★SignalScanner 미등록 — 별도 트레이더 경로)

- **SignalScanner 미등록(확정)**: `_DEFAULT_ENABLED` 에는 `sf_zone/f_zone/gold_zone/swing_38=True`, `blue_line/crypto_breakout/closing_bet=False` 만 있고 `limit_up_chase` 키가 **없다** (`backend/core/scanner/signal_scanner.py:41-55`). 즉 단타 스캐너 파이프라인으로는 절대 진입하지 않는다.
- **전용 루프 배선(존재)**: `run_telegram_bot.main()` 이 `_build_limit_up_chase_trader(notifier)` 를 호출하고, None 이 아니면 `asyncio.create_task(lu_trader.run_forever(), name="limit_up_chase")` 로 supertrend 루프와 **병렬** 가동(`run_telegram_bot.py:812-824`).
- **가동 게이트(default OFF)**: `_build_limit_up_chase_trader` 첫 줄이 `if not _env_truthy("LIMIT_UP_CHASE_ENABLED"): return None` (`run_telegram_bot.py:684`). 또 `dry_run = LIMIT_UP_CHASE_DRYRUN 미설정 시 truthy`(즉 **기본 dry_run**, `:688`).
- **배포 템플릿상 비활성**: `.env.example` 에 `LIMIT_UP_*` 활성 키가 **하나도 없다**(grep 결과 SUPERTREND 주석 1줄만, `.env.example:136`). 코드 grep 상 `LIMIT_UP_CHASE_ENABLED` 는 `run_telegram_bot.py` 3곳에서만 참조되고 설정 파일엔 부재.
- **강제청산 격리**: `scripts/evaluate_holdings.py` 의 `--exclude-strategy` default = `"supertrend,limit_up_chase"` (`:346-347`) → 다른 평가/강제청산 경로가 상따 포지션을 건드리지 않고 **자기 루프만이 관리**. 테스트도 이 default 를 명시 검증(`test_limit_up_chase_trader.py:354-368`).

→ **판정: 🟡 별도경로**. 배선은 완비됐으나 환경변수 미설정 시 객체 자체가 생성되지 않으므로(`return None`), **운영 셸에서 `LIMIT_UP_CHASE_ENABLED` 가 truthy 인지 코드만으로는 확인 불가 = 미검증**. 배포 템플릿(.env.example) 기준으로는 OFF 이며, 켜더라도 default dry_run 이라 실주문은 별도 `LIMIT_UP_CHASE_DRYRUN=0` 가 필요하다.

## 7. 비용·손익분기 + 상한가 갭/유동성 리스크

- **왕복 비용 0.90%**: `ROUND_TRIP_COST_RATE = COMMISSION_RATE*2 + TAX_RATE_SELL = 0.0035×2 + 0.0020 = 0.0090` (`backend/core/trading_costs.py:29-33`). 편도 0.35%·매도세 0.20% (`COMMISSION_PCT/TAX_PCT_ON_SELL`, `:36-37`).
  - ⚠️ **주석 불일치(코드가 진실)**: `trading_costs.py:32` 주석은 "트립 손익분기 기준선(≈0.55%)" 이라 적혀 있으나, 실제 계산값은 **0.90%**. 0.55% 는 옛 가정 잔재로 보이며 손익분기는 0.90% 로 봐야 한다(미검증 주석 vs 코드 — 코드 채택).
- **손익분기**: 상따는 진입가 대비 최소 +0.90% 초과 상승해야 비용 차감 후 흑자. 실 슬리피지(상한가 직전 빠른 호가 변동)는 별도 — 코드상 명시 모델 없음(미검증).
- **상한가 갭 리스크**: 진입 밴드(+20~27%)가 상한가 미달 시 익일 시가갭하락에 노출. 완화책 = `entry_end_time(14:00)` 장막판 차단(`:52`), `daily` 모드 EOD 강제청산(`:257-269`), `overnight` 모드 익일갭 부분익절(0.5 비율, `:323-377`).
- **유동성 리스크**: 진입 게이트가 호가 매수벽(잔량금액 1억·매수/매도 3배)으로 매수세 우위를 요구하나, **매도 잔량 전무 = 통과**(`:314-315`) 설계라 상한가 락 직전 진입은 청산 유동성이 사라진 구간에서 매수하는 셈 — 락이 풀릴 때 급반락 위험. 호가 L2 백테스트 부재로 이 진입의 사전 검증이 불가(`:15`).

## 8. 백테스트·근거 / 한계·리스크

- **백테스트 없음(구조적)**: "호가 L2 이력이 없어 백테스트 불가 → 라이브 dry_run 검증 위주" (`limit_up_chase_trader.py:15`). 따라서 승률·기대값 같은 정량 근거가 **부재**(미검증).
- **검증 = 단위 테스트**(`backend/tests/test_limit_up_chase_trader.py`): 네트워크 없이 가짜 협력자 주입으로 결정적 검증. 호가벽 6케이스, 모멘텀 밴드 in/out, 진입 통합(태깅), wall/momentum 거부, 상한가 판정 상속, 오버나잇 상한가 홀딩(미청산), 하드손절 청산, **전략 격리(supertrend 포지션 미건드림)**, 익일갭 부분익절 멱등, 시간 게이트, EOD 모드, evaluate_holdings exclude default — 총 ~18 테스트.
- **운영 이력(코드 주석 근거)**: 2026-06-12 '상한가 근접' 게이트가 진입 0건 버그를 일으켜 제거(`limit_up_chase_trader.py:286-290`, commit `82b0fe5`). 06-11 호가 잔량 임계를 거래대금(원) 기준으로 전환(commit `a427428`). 06-10 최초 도입(`f61dcec`).
- **핵심 한계**: ① 진입 게이트(호가) 사후검증 불가, ② 비용 주석(0.55%)과 실값(0.90%) 불일치, ③ 실가동 여부 운영 env 의존(코드 미확정), ④ 상한가 락 풀림 시 급반락 — RUNNER 의 수익잠금 floor(2%)·고점되돌림(3%)이 유일한 방어.

## 9. 관련 파일·테스트

- 메인: `backend/core/limit_up_chase_trader.py` (LimitUpChaseTrader / LimitUpChaseConfig)
- 부모(청산 RUNNER 헬퍼): `backend/core/supertrend_auto_trader.py:895-951` (`_is_limit_up`, `_runner_triggered`, `_runner_should_exit`, `_maybe_gap_partial`)
- 배선: `scripts/run_telegram_bot.py:678-757`(_build_limit_up_chase_trader), `:811-824`(루프 가동)
- 비용: `backend/core/trading_costs.py:26-37`
- 격리: `scripts/evaluate_holdings.py:346-347` (exclude default)
- 스캐너 미등록 근거: `backend/core/scanner/signal_scanner.py:41-55`
- 테스트: `backend/tests/test_limit_up_chase_trader.py`

---
*진실원천 주석: 본 리포트의 모든 수치·동작은 origin/main(HEAD `013d54b`) 코드 인용(file:line) 기반이다. "현재 라이브 ON" 여부는 운영 셸의 `LIMIT_UP_CHASE_ENABLED`/`LIMIT_UP_CHASE_DRYRUN` 환경변수에 의존하며 코드만으로 단정 불가 → 미검증. `trading_costs.py:32` 의 "≈0.55%" 주석은 실 계산값 0.90%와 불일치하며 코드값을 채택했다.*
