---
tags: [design, feature/thetrading-uplift, status/draft]
---

# 더트레이딩 방법론 기반 BarroAiTrade 매매전략 분석·설계 문서

> **연관 분석**: [[../../03-analysis/2026-06-17-thetrading-methodology-extract|방법론 추출 부록(99편)]]
> **선행 Design**: [[2026-05-30-strategy-uplift.design|2026-05-30 strategy-uplift]]
>
> **Summary**: 유튜브 '더트레이딩' 99편 방법론을 현행 코드와 대조하여 갭을 식별하고, 운영 실측 손실 패턴과 교차검증한 뒤, 실거래 안전(HITL)을 보존하는 고도화 로드맵 + 종가베팅 신규 전략 스펙을 제시한다.
>
> **Project**: BarroAiTrade (개발 레포 `~/workspace/BarroAiTrade`)
> **Date**: 2026-06-17
> **Status**: Draft (문서 산출물 — **코드 변경 없음**, 구현/배포는 별도 승인 단계)
> **Scope note**: 본 문서는 청사진이다. 실제 구현은 항목별 HITL 분류(§5·§7)를 따른다. 실거래 숫자 파라미터 변경은 `barrotrade-code-surgeon` 위임 + AskUserQuestion 승인 후에만.

---

## 0. 배경과 근거

### 0.1 핵심 발견
BarroAiTrade의 전략명·로직은 유튜브 채널 '더트레이딩'의 방법론을 코드화한 것이다. 채널 용어가 코드 전략명과 1:1 대응한다:

| 방법론 용어 | 코드 전략 | 위치 |
|-------------|-----------|------|
| F존(돌파후 1차눌림 반등) | `f_zone` | `backend/core/strategy/f_zone.py` |
| 골드존(기준봉 0.5~0.618) | `gold_zone` | `backend/core/strategy/gold_zone.py` |
| SF존(F존+추가강도) | `sf_zone` | `backend/core/strategy/sf_zone.py` |
| 대형주 추세추종(5일선) | `supertrend` | `backend/core/strategy/supertrend.py` |
| 상한가 따라잡기 | `limit_up_chase` | `backend/core/limit_up_chase_trader.py` |

### 0.2 두 근거의 수렴
본 설계의 신뢰도는 **독립된 두 출처가 같은 결론을 가리킨다**는 데서 나온다:
1. **방법론(99편)**: 트레이더가 의도한 규칙 — [[../../03-analysis/2026-06-17-thetrading-methodology-extract|추출 부록]].
2. **운영 실측(매매복기 6/9~6/16, 3일 브로커 fill_audit)**: gross +701K인데 순손익 −543K(비용 1,244K = gross의 1.77배). 매일 최대 손실원 = 전일比 +11~22% **고갭 추격**. 동일종목 **고점진입 패 / 눌림진입 승**.

→ 방법론이 "하라"는 것을 코드가 안 해서 생긴 손실이 운영 실측에 그대로 찍혔다. 본 설계는 그 갭을 메우는 청사진이다.

---

## 1. 방법론 정수 (5 규칙군)

상세·원문 인용은 [[../../03-analysis/2026-06-17-thetrading-methodology-extract|부록]]. 요약:

- **① 존 체계**: F존(얕은 눌림 반등 = 무조건 매수) / 골드존(기준봉 0.5~0.618) / SF존·0.618 이탈 손절. **철학 = "불나방(고점추격 개미) 손절 구간에 역매수"** → 진입가 위치가 1차 변수.
- **② 분봉 자금유입**: 오전1+오후1=최상 / 오후만=OK / **오전유입후 죽음=금지**.
- **③ 대장주 선정**: 거래대금 **1~5위 강제** + **신고가 필수** + 거래대금 ≥300억 + 이슈/섹터. 신고가=가점=비중상향. 호가밀집 A급.
- **④ 종가베팅/추세추종**: 15:00~15:20 종가 진입 → 익일 9~10시 +4~5% 슈팅, **D1~D3**(4일차=5일선 청산). 대형주 추세추종은 5일선 이탈부터, +2% 익절.
- **⑤ 리스크/심리**: 종목압축 1~2개, 한 가지 매매만, 신용미수 상승장만, 연속손실 3회 STOP, 수익 보존, FOMO 금지.

---

## 2. 코드 현황 (As-Is)

### 2.1 활성 전략 매트릭스
`backend/core/scanner/signal_scanner.py` `_DEFAULT_ENABLED` / `STRATEGY_PRIORITY`:

| 전략 | TF | 활성 | 진입 핵심 | 청산(ExitPlan) |
|------|----|----|-----------|----------------|
| `sf_zone` | 1분 | ✓ | F존+추가강도(기준봉≥5%, vol 300%) | TP +3/+5/+7%, SL −1.5%, 14:50 |
| `f_zone` | 1분 | ✓ | 급등→눌림→이평지지→반등 | TP +3/+5%, SL −2%, 14:50 |
| `gold_zone` | 1분 | ✓ | BB하단+Fib(0.236~0.786)+RSI회복 | TP +2/+4%, SL −1.5%, 14:50 |
| `swing_38` | 1일 | ✓ | 임펄스→Fib0.382 되돌림 | TP +20/+50%, SL −10/−15%, max_hold 20일 |
| `supertrend` | 5분 | (신호) | ATR밴드 추세전환 | 추세역전 |
| `limit_up_chase` | - | (라이브) | 등락률 20~27% + 호가벽 | overnight_mode(daily/overnight) |

### 2.2 선정 파이프라인
- `backend/core/gateway/kiwoom_native_rank.py` — 3-factor `score = 0.4·(거래대금rank) + 0.3·(등락률rank) + 0.3·(거래량rank)`, 등락률 ≥ +1% 필터. **top_n으로 후보를 자르되 "5위 밖 강제배제"는 아님**.
- `scripts/simulate_leaders.py` — 등락률 ≥ 25% 차단, 가격 < 5,000원 차단, 당일 SL/보유중 제외.

### 2.3 청산 프로파일 (운영 2차 안전망)
`backend/core/risk/holding_evaluator.py` `STRATEGY_EXIT_PROFILES` — 전략별 stop/tp/trailing/breakeven + **`min_hold_days`/`max_hold_days`**. 현재 멀티데이 보유 게이트는 `swing_38`(min 3 / max 20)만 정의.

### 2.4 리스크 / 비용 / 갭가드
- `PolicyConfig`(`backend/core/journal/policy_config.py`): `stop_loss_pct=-4.0`, `daily_loss_limit=-3.0`, `daily_max_orders=50`, `max_per_position=0.10`, `max_concurrent_positions=10`.
- 비용(`backend/core/trading_costs.py`): `COMMISSION_RATE=0.00175`(편도 명명), `TAX_RATE_SELL=0.0020`, `ROUND_TRIP_COST_RATE=COMMISSION_RATE*2+TAX`.
- 갭가드(`scripts/intraday_buy_daemon.py`): `_MEANREV_STRATEGIES={gold_zone}`, `_GAP_GUARD_STRATEGIES=_MEANREV ∪ {f_zone}`, `_ZONE_MAX_FLU=15.0`(장중 등락률), `_ZONE_ENTRY_CUTOFF=14:30`, `_CUTOFF_EXEMPT_STRATEGIES={swing_38}`. supertrend는 별도 `max_open_gap_pct`(시초가 갭).

---

## 3. 추적성 매트릭스 (방법론 ↔ 코드)

상태 집계: **implemented 1 · partial 8 · missing 4**. partial 8건 중 5건이 기존 파라미터/프로파일 튜닝으로 처리 가능.

| # | 규칙군 | 방법론 규칙 | 코드 매핑 | 상태 | 갭 / 확장슬롯 |
|---|--------|------------|-----------|------|---------------|
| R1 | 존 | F존 얕은눌림 반등=매수 | `f_zone.py` `_detect_impulse/_detect_pullback/_detect_bounce` | **impl** | 없음 |
| R2 | 존 | 골드존 = 0.5~0.618 코어 | `gold_zone.py` fib 0.236/0.786 | partial | 코어존 정렬 옵션 |
| R3 | 존 | 0.618 이탈 = 손절 | 고정% SL(−1.5/−2) | partial | **가격기반 0.618 SL 부재** |
| R4 | 분봉 | 오전1+오후1 / 오전후死 금지 | `AnalysisContext.candles`만 | **missing** | 신규 MoneyFlow 신호 → `AnalysisContext` |
| R5 | 대장주 | 거래대금 1~5위 **강제컷** | `kiwoom_native_rank.py` 점수 top_n | partial | hard-cut(rank≤5 배제) 인자 |
| R6 | 대장주 | 신고가 필수+가점 | LeaderCandidate 신고가 필드 없음 | **missing** | `is_new_high` 플래그+가중 |
| R7 | 대장주 | 거래대금 ≥300억 | 절대액 필터 없음 | **missing** | `min_trade_value_won` |
| R8 | 대장주 | 호가밀집 A급 | `limit_up_chase`만 L2 | partial | `orderbook` 슬롯 일반화(라이브) |
| R9 | 종베 | 15:00~15:20→익일 슈팅 | **없음** | **missing** | 신규 `ClosingBetStrategy`(§6) |
| R10 | 종베 | D1~D3, 4일차 5일선 청산 | `STRATEGY_EXIT_PROFILES` swing_38만 | partial | 종베 프로파일 1개(easy) |
| R11 | 진입위치 | 불나방 역매수=고점추격 금지 | 데몬 갭가드(일부 전략) | partial | **진입가-고점 거리 게이트** |
| R12 | 리스크 | 종목압축 1~2개 | `max_concurrent=10` | partial | config 튜닝 |
| R13 | 리스크 | 연속손실 3회 STOP/수익보존 | daily_loss/주문수 한도만 | partial | 연속손실 카운터 부재 |

---

## 4. 갭 분석 + 검증된 정정 2건

### 4.1 갭 → 운영 실손실 1:1 매핑
| 갭(매트릭스) | 운영 실측 손실 패턴 |
|--------------|---------------------|
| R11 진입가 위치 게이트 부재 | 동일종목 고점진입 패 / 눌림진입 승, 고갭 추격 최대손실 |
| R6/R5 신고가·top5 강제 부재 | 5위 밖·신고가 없는 종목 진입 손실 |
| R10 보유한도 부재(swing 20일) | 회전·이월 갭 리스크 |
| 비용 갭(4.2) | 비용 1.77배 → gross 흑자를 적자 전환 |

### 4.2 검증된 정정 ① — 비용 모델 2배 과소 (직접 코드 확인)
`backend/core/trading_costs.py:22-23`의 자체 유도:
```
145,520 / (매수 20.79M + 매도 20.86M) = 0.3494%   ← 분모가 "양다리 합산"
```
이 분모는 매수+매도를 합한 금액이므로 `commission/(buy+sell)=0.3494%` 는 **편도(per-leg) 요율 그 자체**다. 그런데 파일은 이를 "왕복"으로 해석 → 절반(`COMMISSION_RATE=0.00175`=0.175%)으로 설정했다. 소비처 3곳이 모두 **양다리 곱**:
- `scripts/_daily_evening_pipeline.py:80` — `(buy_avg+sell_avg)*qty*COMMISSION_RATE`
- `scripts/_daily_strategy_audit.py:103` — `(sval_m+cost)*commission_rate`
- `backend/core/backtester/intraday_simulator.py:285,327` — 매수·매도 각 per-leg 차감(`COMMISSION_PCT=0.175`)

→ 셋 다 **실측의 절반만 계상**. 정정값 `COMMISSION_RATE=0.0035`(편도 0.35%)로 하면 3곳 모두 동시 정합(왕복 수수료 0.70%, 운영 fill_audit 186건 역산 0.3497%와 일치).
- **검증 메모**: 설계 과정의 일부 초안은 "일괄 0.0035 치환이 4배 과대"라 우려했으나, 위 소비처 전수 확인 결과 **모두 per-leg 단위라 4배가 되지 않는다**. 정정 방향은 메모리(실측 역산)와 일치.
- **영향 분리**: 실현 매매복기(`_daily_strategy_audit`)는 fill_audit 실측 우선이라 영향 미미. **시뮬·선정 레이어**는 비용 2배 과소 → 손익분기 낮게 보여 한계셋업 과매매. 따라서 **분류 (d) HITL**(선정 동작 변경).

### 4.3 검증된 정정 ② — 갭가드 측정축 차이 (직접 코드 확인)
- `supertrend`엔 갭가드가 **있다**: `backend/core/supertrend_auto_trader.py:173` `max_open_gap_pct`, `scripts/intraday_buy_daemon.py:1167`·`scripts/run_telegram_bot.py:645`에서 `SUPERTREND_AUTO_MAX_OPEN_GAP=15.0` wiring. 단 이는 **시초가 갭(open vs 전일종가)**.
- zone 가드 `_ZONE_MAX_FLU`(`intraday_buy_daemon.py:602`)는 **장중 등락률(flu_rate)**.
- → 시초 보합 출발 후 **장중 +20% 추격**은 supertrend가 못 막는다(측정축이 다름). 정밀 결론:

| 전략 | 시초 open-gap 가드 | 장중 flu 가드 | 결론 |
|------|:--:|:--:|------|
| `gold_zone`, `f_zone` | - | ✓ | OK |
| `supertrend` | ✓(15%) | ✗ | 장중 추격 미차단 |
| `sf_zone` | ✗ | ✗ | **갭가드 전무 — 진짜 구멍** |
| `limit_up_chase` | 의도적 제외(갭이 엣지) | - | 제외 |

---

## 5. 고도화 로드맵 (P0/P1/P2 · HITL 분류)

> **HITL 4분류**: (a)운영수동 (b)이미구현=배포문제 (c)안전=자동가능 (d)실거래파라미터=AskUserQuestion 후 config-gated. 실거래 숫자변경은 `barrotrade-code-surgeon` 위임(AST검증·25%변경한도·HITL강제).

### P0 — 운영 손실 직격 + 즉시 안전
- **P0-1 진입가 위치 결합 갭가드 일반화** (R11) — `sf_zone`을 갭가드 집합에 편입 + **「갭% AND 진입가 위치」 조합 게이트**(고점추격만 차단, 눌림진입은 보존). `intraday_buy_daemon.py:878` 레이어. 분류 **c→(활성 d)**. 백테스트 ✓(`_daily_strategy_audit` gap_records). 상세 §5.1.
- **P0-2 종베 D1~D3 보유 프로파일** (R10) — `holding_evaluator.py` `STRATEGY_EXIT_PROFILES`에 swing_38 패턴 복제(min 1 / max 3). 분류 **d**(swing_38 20→3은 25%한도 초과 → 점진). 백테스트 ✓.
- **P0-3 대장주 top5 hard-cut + 신고가** (R5/R6) — `kiwoom_native_rank.py` 생성자 인자. 분류 **d**(유니버스 변경). 백테스트 ⚠️(과거 순위 메타 의존).

### P1 — 고레버리지·중난이도(신규 신호)
- **P1-1 분봉 자금유입 신호** (R4) — 신규 계산기 → `AnalysisContext` 경유 게이트, default OFF. 분류 **c(shadow)→d(게이트)**. 백테스트 ✓(분봉 캐시 의존).
- **P1-2 골드존 코어존 + Fib 가격 SL** (R2/R3) — `GoldZoneParams` fib 0.5/0.618 옵션 + 0.618 가격기반 SL. 분류 **d**. 백테스트 ✓.
- **P1-3 종베 전용 모듈** (R9) — §6. SignalScanner OFF 등록(c) → 라이브(d). 백테스트 ⚠️.

### P2 — 보강(데이터/라이브 의존)
- P2-1 호가강도 일반화(R8, 라이브 전용 ❌백테스트) / P2-2 연속손실 STOP+수익보존(R13, ✓) / P2-3 거래대금 절대임계(R7, ⚠️).

### 5.1 진입가 위치 결합 갭가드 (양날 회피 설계)
일괄 등락률 차단은 "한온 sf +22%갭이지만 **눌림진입으로 흑자**" 같은 케이스까지 죽인다. 해법은 갭%와 진입가 위치의 **결합**:

```
entry_pos = (현재가 − 일중저점)/(일중고점 − 일중저점)*100   # 0=저점, 100=고점
if flu_rate >= ZONE_MAX_FLU:                    # 갭 급등 구간
    if strategy in MEANREV (gold_zone):  BLOCK          # 바닥전략은 위치 무관 차단
    elif entry_pos >= ENTRY_POS_MAX:     BLOCK          # 고점 추격 차단
    elif entry_pos <  PULLBACK_POS and strategy in {f_zone, sf_zone}: ALLOW   # 눌림진입 보존
    else:                                BLOCK (기본 deny)
else: ALLOW
```
- 임계 3개(`ZONE_MAX_FLU`/`ENTRY_POS_MAX`/`PULLBACK_POS`) 모두 env, 보수 기본값. shadow 로그로 `gap_records`+위치 분포 N일 누적 후 (d) 승인.
- `entry_pos`는 `_daily_strategy_audit.py §B`의 진입품질 측정(`entry_position_pct`)과 동일 계산 → 검증 일관성.

---

## 6. 신규 전략 스펙 — `ClosingBetStrategy` (종가베팅)

### 6.1 포지셔닝 (왜 별도 모듈인가)
종베는 **진입(장막판)·청산(익일아침)이 다른 거래일**에 걸쳐 모든 기존 단타 전략과 구조적으로 다르다. f_zone/gold_zone은 `time_exit=14:50`(당일 강제청산)이고 데몬은 `_ZONE_ENTRY_CUTOFF=14:30`으로 **장막판 진입 자체를 차단**한다 → 종베 진입창(15:00~15:20)과 **정면충돌**. swing_38은 진입로직(깊은 Fib0.382)·시간창이 무관하므로 종베화 부적합.

**권고: 신규 `ClosingBetStrategy`(`STRATEGY_ID="closing_bet_v1"`) + 청산 인프라 재사용.** 선례: `limit_up_chase_trader`가 `SupertrendAutoTrader`를 상속해 **진입만 교체, 청산(overnight/갭부분익절) 100% 재사용**. 종베도 진입 신규 + `limit_up_chase`의 `overnight_mode`/`_maybe_gap_partial`/`_prev_close`/`_today_open` 재사용.

### 6.2 `ClosingBetParams` (요지)
| 영역 | 필드(기본값) |
|------|--------------|
| 진입창 | `entry_window_start=15:00`, `entry_window_end=15:20`, `require_eod_window=True` |
| 선정컷 | `max_trade_value_rank=5`, `min_trade_value=3.0e10`, `min_flu_rate=3.0`, `max_flu_rate=27.0`, `require_new_high=True`, `new_high_lookback=60` |
| 분봉유입 | `flow_am_window=(09:00,11:30)`, `flow_pm_window=(13:00,15:20)`, `flow_min_vol_ratio=1.5`, `flow_require_pm=True`, `flow_block_am_only=True` |
| 기준봉 | `base_min_gain_pct=0.05`, `base_min_vol_ratio=2.0`, `base_upper_wick_max=0.5` |
| 존진입가 | `zone_mode="gold"`, `gold_fib_low=0.5`, `gold_fib_high=0.618`, `fzone_pullback_min/max=-0.03/-0.005`, `stop_fib_break=0.618` |
| 분할매수 | `tranche_count=3`, `tranche_ratios=[0.4,0.35,0.25]` |
| 익절/청산 | `tp_shoot_pct=0.045`, `tp_shoot_pct_largecap=0.02`, `largecap_market_cap=5.0e12`, `morning_exit_time=10:00` |
| 보유한도 | `max_hold_days=3`, `ma5_break_exit=True`, `ma5_period=5` |
| 손절 | `stop_loss_pct=-0.03`, `overnight_gap_stop_pct=-0.05` |
| 필터 | `min_atr_pct=0.035`, `atr_n=14`, `min_candles=60` |
| 토글 | `enable_ipo_mode=False`(상장1~2일), `enable_down_mode=False`(음봉종베, 지수−3%·3·5일선 미붕괴) |

### 6.3 진입 분석 (의사코드)
```
def _analyze_v2(ctx):
    if not (entry_window_start <= now <= entry_window_end): return None   # ① 시간창
    lc = ctx.leader                                                       # ② 선정컷
    if lc.rank_trade_value > 5 or lc.trade_value < 300억: return None
    if not (3.0 <= lc.flu_rate <= 27.0): return None
    if atr_pct(daily) < min_atr_pct: return None
    if today_high < prior_high(lookback=60): return None                  # ③ 신고가
    if base_body < 5% or upper_wick_ratio > 0.5: return None              # ④ 기준봉(장대양봉)
    am, pm = inflow(am_window), inflow(pm_window)                         # ⑤ 분봉 자금유입
    if am and not pm: return None            # 오전후死 금지
    if flow_require_pm and not pm: return None
    if zone_mode=="gold" and not (0.5 <= retrace <= 0.618): return None   # ⑥ 존 진입가
    return EntrySignal(signal_type="closing_bet", score=..., metadata={
        tranche_ratios, base_high, base_low,
        stop_fib_price = base_high - (base_high-base_low)*0.618,
        is_largecap, overnight=True })
```

### 6.4 청산 / `exit_plan`
- `ExitPlan`: TP1(저항 50%)·TP2(슈팅 +4.5%/대형주 +2%), SL=`max(stop_loss_pct, (stop_fib_price-avg)/avg)`, `time_exit=10:00`(익일), `max_hold_days=3`.
- **버그 주의**: `time_exit=10:00`을 ExitEngine이 "당일 그 시각"으로 해석하면 진입일 당일 즉시청산된다 → **overnight 플래그로 진입일 당일 time_exit 평가 skip**(limit_up_chase `_maybe_gap_partial`이 `entry_time.date() >= cur_date`로 당일 제외하는 패턴 차용).
- 운영 2차망: `STRATEGY_EXIT_PROFILES["closing_bet"]` 추가(min_hold 0 / max_hold 3, broker pnl_rate 기반).

### 6.5 config-gating (3곳, 격리 안전)
1. `backend/models/signal.py` — `EntrySignal.signal_type` Literal에 `"closing_bet"` 추가(미추가 시 ValidationError).
2. `backend/core/scanner/signal_scanner.py` — `_DEFAULT_ENABLED["closing_bet"]=False` + `STRATEGY_PRIORITY` 등록 + **별도 EOD dispatch 블록**(15:00~15:20 fetch, intraday 디스패치와 분리).
3. `backend/core/risk/holding_evaluator.py` — `STRATEGY_EXIT_PROFILES["closing_bet"]` 등록.
4. 데몬 `_CUTOFF_EXEMPT_STRATEGIES`에 `closing_bet` 추가(15:00 진입 허용) — 단 그만큼 오버나잇 carry-limit 노출.

### 6.6 리스크 (실증 포함)
1. **오버나잇 갭 — 이 코드베이스에서 이미 실손**: `_eod_carry_limit` 주석상 6/9 이월 21.8M(계좌 43.7%)이 6/10 갭하락 −845K의 주성분. 종베는 설계상 오버나잇 필수 → **종베 전용 carry 한도(계좌 10%)+동시 1~2종** 필요.
2. **운영 가드와 철학 충돌**: cutoff 14:30·flu 가드 15%·당일 14:50 청산 — 모두 종베와 반대. 면제 시 운영 일관성 부담.
3. **데이터 한계**: 호가 A급=백테스트 불가, 분봉 자금유입=거래량 proxy, 종가체결·익일갭=시뮬 가정 → **라이브 dry_run 1~2주 필수**.
4. **분할매수 미체결**: 종가 동시호가 분할은 회차별 부분체결 → 평단 왜곡(limit_up_chase가 `single_tranche`로 회피한 문제).

---

## 7. 검증 & HITL

### 7.1 백테스트 가능 / 불가
| 가능(일봉 grid + OOS) | 불가/제한(sim-live 괴리) |
|---|---|
| 비용 정정(소비처 단위테스트), 보유한도, 종베 청산(IntradaySimulator 다일 윈도우), top5 선정 | 분봉 자금유입(분봉 데이터·체결가 추정), 호가 A급/wall(L2 이력 부재), 종가·익일갭 슬리피지 |

### 7.2 과최적화 방지 (3중)
1. **OOS/holdout** — `scripts/_oos_validation.py` 관문(active≥15 & trades≥30 & avg_ret>0 & drop1 부호안정 & holdout avg_ret>0). ★PASS 전 실거래 자본증액 금지.
2. **walk-forward** — 그리드 최적치가 인접 구간 유지.
3. **파라미터 민감도** — 임계 ±1 스텝에 손익 **부호 유지**(절벽형 최적치 거부).
- baseline ±5% 회귀 게이트(전략 base) 유지. gold_zone min_score(6월 약세에 4.0→5.0 재튜닝)가 in/out 분리 선례.

### 7.3 config-gating 키 (기본 OFF + env override)
| 항목 | 키 | default |
|------|----|---------|
| 갭가드 전략집합 | `BARRO_GAP_GUARD_STRATEGIES`(csv) | 기존 `gold_zone,f_zone` |
| 진입위치 상한 | `BARRO_ENTRY_POS_MAX_PCT` | 0=off |
| 갭+위치 결합 | `BARRO_GAP_POS_COMBINE` | 0=off |
| 분봉유입 | `BARRO_INFLOW_SIGNAL` | 0=shadow |
| top5/신고가 | `BARRO_LEADER_TOP5_ONLY`, `BARRO_LEADER_NEWHIGH_BLOCK` | 0=off |
| 비용 | `BARRO_COMMISSION_RATE` | 0.00175(→정정 0.0035, HITL) |

### 7.4 단계적 배포 순서
1. **비용 정정(d) + 측정 정합** — 최고 레버리지·저위험. 모든 후속 판정의 기반(낙관 편향의 주성분). 선정 임계는 별도.
2. **supertrend 장중 flu 가드 정렬(b)** — 측정축 보완.
3. **진입가 위치 게이트 shadow(c)** — 차단 X, 텔레메트리만.
4. **갭가드 sf_zone 편입 + 진입가 결합(d)** — OOS·shadow 통과 후.
5. **선정컷 top5/신고가(d)** → **swing_38 다단계 20→…→3(d)** → **종베 모듈(d)**.

### 7.5 롤백·관측
- 관측: `scripts/verify_eod_data.py`(EOD 무결성, 종료코드=NG수) + `scripts/_daily_strategy_audit.py`(§A 전략별 승률·자본가중, §B 진입품질·고점편향, §C 청산슬립).
- 롤백: 모든 게이트가 env 기반 → **코드변경 없이 `env=0` 즉시 무력화**. param 변경(비용·max_hold)은 git revert + 동기 위치 동시 복원.
- 악화 판정 사전고정: 예) 전략 승률 30% 미만 & 트레이드 임계 초과 시 비활성 검토(`_daily_strategy_audit`의 gold_zone 알람 패턴 차용).

### 7.6 HITL 게이트(인간 승인 필수)
①갭가드 임계 조정 ②종베 라이브 활성 ③top5 hard-cut 활성(주도주 정의 변경) ④비용 정정의 선정 반영 ⑤신용미수("상승장만") ⑥연속손실 STOP 재개 — 모두 자동화 금지.

---

## 8. 다음 단계 (이 문서 이후)

본 문서는 **청사진**이다. 실제 구현은 다음 순서의 별도 승인 단계로 진행:
1. **P0-1 진입가 위치 결합 갭가드** shadow 구현(c) — `BARRO_*` env, 차단 X.
2. shadow N일 누적 → §5.1 임계 캘리브레이션 → AskUserQuestion(d) → 활성.
3. 비용 정정(d)은 `barrotrade-code-surgeon` 위임(단일 상수, 단 선정영향 → HITL).
4. 종베 모듈(P1-3)은 OFF 등록 후 OOS PASS 시에만 라이브.

> 각 단계는 `barro-trade-review` 스킬의 매매복기 사이클로 효과를 측정·환류한다.
