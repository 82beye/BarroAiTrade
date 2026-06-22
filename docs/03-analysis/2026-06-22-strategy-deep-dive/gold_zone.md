# BarroAiTrade 매매전략 심층 리포트 — gold_zone (골드존)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: 🟢 활성 (default ON) · 분류: 1분봉 단타(되돌림) · 컨셉: BB 하단 + Fib 되돌림존 + RSI 과매도 회복이 겹치는 "골드존"에서 보수적으로 바닥을 매수하는 mean-revert 전략

## 1. 요약 (TL;DR)

- **무엇**: BB(20,2σ) 하단 근접 + Fib 0.236~0.786 되돌림존 + RSI(14) 과매도(≤35) 후 회복(≥38) — 3개 조건의 가중 점수가 임계 이상이면 진입하는 되돌림(mean-revert) 매수 전략. 진입점은 `GoldZoneStrategy._analyze_v2` (`gold_zone.py:104`).
- **활성 여부**: `_DEFAULT_ENABLED["gold_zone"] = True` (`signal_scanner.py:44`), dispatch 우선순위 `SF > F > Gold` (`signal_scanner.py:160-161`), 슬롯 경합 tiebreaker `STRATEGY_PRIORITY["gold_zone"]=2` (`signal_scanner.py:60`).
- **진입 게이트(다층)**: min_candles 60 → ATR% 필터(옵션) → trap_guard(옵션) → 진입시간 cutoff(옵션) → 조건 2/3 충족 → 가중점수 ≥ `min_score=5.0` (`gold_zone.py:60, 106-144`).
- **청산 이원화**: ExitEngine 1차 방어선 SL −1.5%/TP +2%·+4% (`gold_zone.py:251-275`), HoldingEvaluator 2차 안전망 SL −4.0%/TP +4.0%/부분익절 2.0%@0.5/트레일링 3.0→1.0/본전 2.5/시간강화 −3.0 (`holding_evaluator.py:125-134`).
- **★특수 운영**: gold_zone 은 `_MEANREV_STRATEGIES={"gold_zone"}` (`intraday_buy_daemon.py:627`)로 분류돼 **DCA(물타기) gate** 와 **gap-guard** 대상 — 6번 섹션 상세.

## 2. 전략 개요

- **포지셔닝**: 데몬 default zone 전략 4종(`swing_38`, `f_zone`, `sf_zone`, `gold_zone`) 중 유일한 "되돌림(바닥매수)" 계열. f_zone/sf_zone 가 눌림·추세 계열인 반면, gold_zone 은 하락 후 과매도 반등을 노린다.
- **모듈 헤더 정의** (`gold_zone.py:1-10`): "보수적 되돌림 매수. 진입 3 조건 — BB 하단 1% 이내 / Fib 0.382~0.618 zone / RSI 30 이하 후 40 돌파 회복." (단, 실제 default 파라미터는 헤더 설명보다 완화됨 — Fib 0.236~0.786, RSI 35→38. 헤더는 초기 설계값, 코드 default 가 진실원천.)
- **진입점 구조**: `Strategy.analyze()` (base.py) 가 `_analyze_v2(ctx)` 로 위임 (`base.py:52,62`). 운영/시뮬/재검증 모두 `analyze()` 단일 경로.
- **STRATEGY_ID** = `"gold_zone_v1"` (`gold_zone.py:99`). HoldingEvaluator 의 `resolve_policy` 가 `_v1` 접미사를 제거해 `gold_zone` 키로 프로파일 매칭 (`holding_evaluator.py:175-177`).

## 3. 진입 로직 (조건·점수·게이트)

진입은 `_analyze_v2` (`gold_zone.py:104-164`)에서 **순차 게이트 → 조건 점수화 → 임계 컷**으로 처리된다.

### 3-1. 사전 게이트 (순서대로, 코드 인용)

1. **캔들 수**: `len(ctx.candles) < p.min_candles(60)` → None (`gold_zone.py:106`).
2. **변동성 필터 (옵션)**: `min_atr_pct > 0` 일 때만 `atr_pct < min_atr_pct` → None (`gold_zone.py:110-113`). default `min_atr_pct=0.0` (비활성, `gold_zone.py:66`). 시뮬/재검증에서 명시 override(0.035 / 0.01) — 5번·6번 참조.
3. **trap_guard (옵션)**: `_trap_cfg.any_enabled()` 일 때만 과확장·윗꼬리·고갭 차단 (`gold_zone.py:116-120`). 모든 임계 default 0 → no-op (`gold_zone.py:77-82`).
4. **진입 시간 cutoff (옵션)**: `entry_time_cutoff is not None` 이고 마지막 candle `time() >= cutoff` → None (`gold_zone.py:123-126`). default None (비활성, `gold_zone.py:73`).

### 3-2. 3개 조건 점수 (각 [0,1])

```python
bb_score  = self._bb_score(df)    # gold_zone.py:130, 168-184
fib_score = self._fib_score(df)   # gold_zone.py:131, 186-201
rsi_score = self._rsi_score(df)   # gold_zone.py:132, 203-229
```

- **BB 하단 점수** (`_bb_score`, `gold_zone.py:168-184`): SMA(20)±2σ 하단(`lower`) 기준. `close <= lower` 면 1.0; `bb_proximity_pct(0.03=3%)` 이내면 `1 - distance/0.03`, 초과면 0.0. (헤더의 "1% 이내" 설명과 달리 default 는 **3%**, `gold_zone.py:48`.)
- **Fib 점수** (`_fib_score`, `gold_zone.py:186-201`): 최근 `fib_lookback(30)`봉 고·저 기준 retrace 비율. `fib_min(0.236)~fib_max(0.786)` 밖이면 0.0, 안이면 중심(0.5)에 가까울수록 1.0 (`gold_zone.py:45-46`).
- **RSI 점수** (`_rsi_score`, `gold_zone.py:203-229`): EWM RSI(14). 최근 10봉 최저 RSI 가 `rsi_oversold(35)` 이하로 내려갔고(과매도 경험) **현재 RSI ≥ `rsi_recovery(38)`** (회복)일 때만 점수, `min(1, (rsi_now-35)/(38-35))`. 둘 중 하나라도 불만족이면 0.0 (`gold_zone.py:224-227`).

### 3-3. 조건 수 게이트 + 가중 점수 컷

```python
conditions_met = (bb_score>0)+(fib_score>0)+(rsi_score>0)
if conditions_met < p.min_conditions(2): return None   # gold_zone.py:135-137
raw   = bb_score*0.4 + fib_score*0.3 + rsi_score*0.3    # gold_zone.py:139
score = raw * 10.0                                       # 0~10 정규화, gold_zone.py:141
if score < p.min_score(5.0): return None                # gold_zone.py:143-144
```

- **2/3 조건 충족** 필요 (`min_conditions=2`, `gold_zone.py:50`). 코드 주석상 `min_conditions=3` 강화는 PnL 악화로 무효 확인 → 2 유지 (`gold_zone.py:56`).
- **가중치**: BB 0.4 / Fib 0.3 / RSI 0.3 — BB 하단 근접에 최대 가중.
- **임계**: `min_score=5.0` (`gold_zone.py:60`). 이력: hardcoded 2.5 → 4.0(B4 시뮬, `gold_zone.py:51-56`) → **5.0**(BAR-OPS-33, 4~6월 sweep: 6월 약세 해소·기대값 +4.32·MDD −3.0·승률 57%, `gold_zone.py:57-59`).
- **시그널 생성** (`gold_zone.py:146-164`): `signal_type="gold_zone"`, `strategy_id="gold_zone_v1"`, metadata 에 bb/fib/rsi 개별 점수 기록.

## 4. 청산 로직 (SL−4/TP+4/부분익절2@0.5/트레일링/본전/특수)

청산은 **2계층**으로 의도적으로 분리돼 있다 (`holding_evaluator.py:86-103`, SL 격차 의도).

### 4-1. ExitEngine 1차 방어선 — `GoldZoneStrategy.exit_plan` (`gold_zone.py:251-275`)

분봉 close 기준, 더 엄격:
- **TP1** = avg ×1.02 (+2%), 50% 매도 (`gold_zone.py:259-263`)
- **TP2** = avg ×1.04 (+4%), 50% 매도 (`gold_zone.py:264-268`)
- **SL** = `fixed_pct = resolve_sl_pct(..., Decimal("-0.015"))` → **−1.5%** (라운드피겨 보정 OFF 시 그대로, `gold_zone.py:270-272`, `round_figure.py:201-202`)
- **time_exit** = 14:50 (STOCK 한정, `gold_zone.py:273`)
- **breakeven_trigger** = +1.0% (`gold_zone.py:274`)

### 4-2. HoldingEvaluator 2차 안전망 — `STRATEGY_EXIT_PROFILES["gold_zone"]` (`holding_evaluator.py:125-134`)

브로커 `pnl_rate` 기준, 더 너그러운 fallback (코드로 재확인한 6개 값):

| 항목 | 값 | file:line |
|---|---|---|
| stop_loss_pct | **−4.0** | holding_evaluator.py:126 |
| take_profit_pct | **+4.0** | holding_evaluator.py:127 |
| partial_tp_pct | **2.0** | holding_evaluator.py:128 |
| partial_tp_ratio | **0.5** | holding_evaluator.py:129 |
| trailing_start_pct | **3.0** | holding_evaluator.py:130 |
| trailing_offset_pct | **1.0** | holding_evaluator.py:131 |
| breakeven_trigger_pct | **2.5** | holding_evaluator.py:132 |
| tightened_sl_pct | **−3.0** | holding_evaluator.py:133 |

평가 우선순위 (`evaluate_holding`, `holding_evaluator.py:296-461`): ① max/min_hold_days 게이트(gold_zone 미정의 → skip) → ② distribution(default-OFF) → ③ 단기고점 캔들(SHORT_TERM_HIGH, 1분봉 있고 rate≥partial_tp 시) → ④ 트레일링(peak≥3.0 & rate < peak−1.0) → ⑤ 본전(peak≥2.5 & rate≤0) → ⑥ 부분익절(rate≥2.0 & <4.0, 50%) → ⑦ 전량 TP(rate≥4.0) → ⑧ 시간강화 SL(보유 ≥hold_days_tighten=5일 → −3.0) → ⑨ SL(rate ≤ −4.0).

**SL 격차 의도** (`holding_evaluator.py:86-103`): ExitEngine −1.5% 와 HoldingEvaluator −4.0% 의 2.5%p 격차는 운영 robustness 안전망(데몬 다운·분봉 fetch 실패·시그널 누락 시 fallback + broker pnl 노이즈 흡수). 재검토 트리거: HoldingEvaluator STOP_LOSS 발동 >20% 또는 broker noise false trigger.

## 5. 파라미터 표

`GoldZoneParams` (`gold_zone.py:36-93`) default — **코드 확인값**:

| 파라미터 | default | 의미 | file:line |
|---|---|---|---|
| bb_period | 20 | BB 이동평균 기간 | gold_zone.py:40 |
| bb_std | 2.0 | BB 표준편차 배수 | gold_zone.py:41 |
| bb_proximity_pct | 0.03 (3%) | BB 하단 근접 허용폭 | gold_zone.py:48 |
| fib_lookback | 30 | Fib 고·저 산정 봉수 | gold_zone.py:42 |
| fib_min / fib_max | 0.236 / 0.786 | 되돌림존 경계 | gold_zone.py:43-44 |
| rsi_period | 14 | RSI 기간 | gold_zone.py:44(line 45) |
| rsi_oversold | 35.0 | 과매도 경험 임계 | gold_zone.py:46 |
| rsi_recovery | 38.0 | 회복 확인 임계 | gold_zone.py:47 |
| min_candles | 60 | 최소 캔들 수 | gold_zone.py:49 |
| min_conditions | 2 | 최소 충족 조건(2/3) | gold_zone.py:50 |
| **min_score** | **5.0** | 가중점수 진입 임계 (BAR-OPS-33) | gold_zone.py:60 |
| min_atr_pct | 0.0 (비활성) | ATR% 변동성 필터 | gold_zone.py:66 |
| atr_n | 14 | ATR 기간 | gold_zone.py:67 |
| entry_time_cutoff | None (비활성) | 장후반 진입 차단 | gold_zone.py:73 |
| trap_* (6개) | 0 / "ma" / 20 (비활성) | 트랩가드 임계 | gold_zone.py:77-82 |

**진입점별 override** (default 와 다름):
- IntradaySimulator `gold_zone` 분기: `min_atr_pct=0.035`, `entry_time_cutoff=dtime(14,0)` (`intraday_simulator.py:218-227`). LG계열 저변동 차단 + 15:01 같은 장후반 진입 차단.
- 데몬 진입 재검증: `GoldZoneParams(min_atr_pct=0.01)` (분봉 적정 — 0.035 는 분봉서 신호 전멸, `intraday_buy_daemon.py:748-751`, 주석 `intraday_buy_daemon.py:619-625`).

## 6. 활성·운영 상태 (★mean-revert DCA gate, gap-guard 특수처리 상세)

### 6-1. 활성 상태

- `_DEFAULT_ENABLED["gold_zone"]=True` (`signal_scanner.py:44`) — 1분봉 intraday 단타 default 활성. 주석상 이전엔 SignalScanner 미등록이었고 D2.1 에서 신규 등록.
- dispatch 우선순위: intraday 리스트에서 `sf_zone → f_zone → gold_zone` 순, 첫 시그널 반환 (`signal_scanner.py:156-161,175-182`).
- 슬롯/자본 경합 tiebreaker: 점수 1차 내림차순, `STRATEGY_PRIORITY` 2차 오름차순. `gold_zone=2` (swing_38=1 다음 2순위, `signal_scanner.py:59-62,137`).
- 데몬 default zone 전략 포함: `DEFAULT_ZONE_STRATEGIES=["swing_38","f_zone","sf_zone","gold_zone"]` (`intraday_buy_daemon.py:85`).

### 6-2. ★ mean-revert 분류 → DCA(물타기) gate

gold_zone 은 **유일하게** `_MEANREV_STRATEGIES = {"gold_zone"}` 로 분류됨 (`intraday_buy_daemon.py:627`). 근거 주석(`intraday_buy_daemon.py:626`): "되돌림(바닥매수) 전략 — 고점근접 무조건 차단 + DCA(물타기) 비활성 대상."

- **DCA gate** (`intraday_buy_daemon.py:457-460`): 보유 종목 DCA(분할 추매) 루프에서, `args.dca_strategy_gate` 플래그가 켜져 있고 포지션 전략이 `_MEANREV_STRATEGIES` 에 속하면 **물타기 비활성** (`[DCA-SKIP] ... 되돌림전략 — DCA(물타기) 비활성`).
  - 근거(코드 주석 `intraday_buy_daemon.py:454-456`): "되돌림(바닥) 전략 gold 는 하락 중 DCA(물타기) 비활성 — 약전략의 추세하락 평단 물타기가 손실을 키움(5/29 한온시스템 gold 고점매수→DCA→ −6%)."
  - **상태**: `--dca-strategy-gate` 플래그 기본 off → 기본 동작 불변(주석 `intraday_buy_daemon.py:456`). 즉 게이트 인프라는 코드에 있으나 default 미적용. (참고로 일반 DCA 자체는 pending tranche 가 있을 때만 발동되며, `_NO_DCA_STRATEGIES={"swing_38","supertrend"}` 와 달리 gold_zone 은 *무조건* 차단이 아니라 플래그-조건부 차단이다.)

- **진입 재검증과의 결합** (`_MEANREV_STRATEGIES` 의 또 다른 의도): 주석 `⑦/⑧` (`intraday_buy_daemon.py:626`)상 "고점근접 무조건 차단" 대상. 데몬은 일봉 sim 으로 전략을 선정하나, 진입 직전 분봉 컨텍스트로 `analyze()` 재호출(`_revalidate_entry`, `intraday_buy_daemon.py:755-777`)해 gold(바닥매수)가 장중 고점에 진입하는 것을 차단. (5/29 한온시스템 −6% 회귀 방지, 주석 `intraday_buy_daemon.py:616-621`.) 재검증 전략 빌드 시 gold_zone 은 `min_atr_pct=0.01` 분봉 임계로 생성(`intraday_buy_daemon.py:748-751`).

### 6-3. ★ gap-guard 특수처리

gold_zone 은 `_GAP_GUARD_STRATEGIES` 의 기본 멤버다:

```python
_GAP_GUARD_STRATEGIES = _parse_strategy_set(
    "BARRO_GAP_GUARD_STRATEGIES", _MEANREV_STRATEGIES | {"f_zone"})   # intraday_buy_daemon.py:658-659
```

- 기본값 = `{"gold_zone", "f_zone"}` (mean-revert ∪ f_zone). env `BARRO_GAP_GUARD_STRATEGIES` 로 sf_zone 등 편입 가능(코드배포 없이, 주석 `intraday_buy_daemon.py:653-657`).
- **시초갭 상한 가드** (`_ZONE_MAX_FLU`, `intraday_buy_daemon.py:642`): "되돌림(gold)·눌림(f) 전략 시초갭 상한 — 갭상승 폭등주에는 바닥/눌림 신호가 고점에서 발화한다." 전일종가 대비 등락률(flu_rate)이 임계(env `BARRO_ZONE_MAX_FLU`, default 15.0) 이상이면 해당 전략 진입 금지.
  - 근거 패턴(주석 `intraday_buy_daemon.py:636-641`): 6/10 gold 추격 3종 전패 −461K (SK오션플랜트 시가갭 +22.8% 등 — 5/29·6/8 에 이은 세 번째 'gold 고점매수'). 6/11 실증: 임계 15% 바로 아래(13.1~13.5%) 진입 3건 전패 −353K → 임계 조정은 일일감사 갭분포 누적측정(BAR-OPS-39) 후 데이터 기반.
- **trap_guard 후처리** (`_DAEMON_TRAP`, `intraday_buy_daemon.py:731-735`): 일봉 선정 단계에서 과확장·윗꼬리·시초갭(전 전략 대상, gold_zone 포함). 모든 env 0(default) → `any_enabled()=False` → 무동작. 실제 차단 활성은 HITL(env 설정 자체가 인간 승인 게이트, 주석 `intraday_buy_daemon.py:699-704`).

### 6-4. 그 외 공통 운영 게이트 (gold_zone 도 적용)

- **진입 컷오프**: `_ZONE_ENTRY_CUTOFF=14:30` (`intraday_buy_daemon.py:665`), `_CUTOFF_EXEMPT_STRATEGIES={"swing_38"}` 만 면제 → gold_zone 은 14:30 이후 진입 차단.
- **EOD 강제청산 면제 아님**: `_FORCE_CLOSE_EXEMPT_STRATEGIES={"swing_38"}` (`intraday_buy_daemon.py:672`) — gold_zone 은 EOD carry-limit 트림 대상(swing 처럼 이월 보존 안 됨).
- **포지션 사이징**: `even_position_size` 균등 진입 (score 차등 BAR-176 무력화, `gold_zone.py:277-280`).

## 7. 비용·손익분기 관점

거래 비용 단일 진실원천 `backend/core/trading_costs.py` (브로커 실측 기반):

- **편도 수수료** `COMMISSION_RATE = 0.0035` = **0.35%/leg** (`trading_costs.py:29`, fill_audit 298행 실측 1,768,040 / 505,588,092 = 0.3497%).
- **매도 거래세** `TAX_RATE_SELL = 0.0020` = **0.20%** (`trading_costs.py:31`, 실측 41,687 / 20,858,020 = 0.1999%).
- **왕복 총비용** `ROUND_TRIP_COST_RATE = 0.0035×2 + 0.0020 = 0.0090` = **0.90%** (`trading_costs.py:33`).

**손익분기 해석 (gold_zone 청산값 대입)**:
- ExitEngine TP1 +2% 도달 시: gross +2% − 왕복 0.90% ≈ **순 +1.1%**. 부분익절 50% 만 청산이므로 잔여분은 추가 변동 노출.
- HoldingEvaluator 부분익절 +2.0% 도 동일하게 net 약 +1.1%.
- SL −4.0%(2차망) 발동 시: 순손실 ≈ −4.9% (gross −4% − 비용 0.9%). 1차망 −1.5% 면 순 ≈ −2.4%.
- gold_zone 의 TP +2%/+4% 는 왕복 0.90% 대비 여유가 크지 않다 — 특히 부분익절 2%는 비용 차감 후 +1.1%로, 진입 정밀도(고점 진입 방지)가 수익성에 직결. 6번의 gap-guard·재검증이 "고점 진입 → 미달 청산" 손실을 막는 것이 손익분기 관점의 핵심.

**⚠ 미검증/discrepancy**: `trading_costs.py:32` 코드 주석은 왕복 손익분기를 "≈0.55%" 로 표기하나 실제 계산값은 0.90% 다(주석이 종전 0.00175 시절 잔재로 보임). 본 리포트는 코드 상수 계산값(0.90%)을 진실원천으로 채택.

## 8. 백테스트·OOS 근거 / 한계·리스크

### 8-1. 코드 주석에 박제된 시뮬 근거 (재현은 미실행 — 주석 인용)

- **min_score 튜닝** (`gold_zone.py:51-59`): B4 시뮬(791종목·6셀) — score≥2.5 자본가중 +0.148% → score≥4.0 +0.231%(+56%, sweet spot) → score≥5.5 +0.229%(4.0 과 동일). BAR-OPS-33 4~6월 sweep(거래대금150, in 4~5월/out 6월): score≥4.0 out −7.5(약세) vs score≥5.0 기대값 +4.32·MDD −3.0·승률 57%·out +10.6 ★ → **5.0 채택**.
- **min_conditions=3 무효**: 모든 PnL 악화로 default 2 유지 (`gold_zone.py:56`).
- **변동성 필터 0.035**: LG계열 가짜 시그널 패턴 — 5/21 LG전자 −626k(43 trades, win 41%), 5/14 LG씨엔에스 −190k, 5/15 −150k (`gold_zone.py:62-66`).
- **진입시간 cutoff 14:00**: 5/22 379800 KODEX 미국S&P500 15:01(장 마감 19분 전, w=0.5 약신호) 차단 (`gold_zone.py:69-72`).
- **gold 고점매수 손실 사례**(되돌림 전략의 구조적 약점): 5/29 한온시스템 gold 고점매수→DCA→−6% (`intraday_buy_daemon.py:455`), 6/10 gold 추격 3종 전패 −461K (`intraday_buy_daemon.py:637`), 6/11 갭 13.1~13.5% 진입 3건 전패 −353K (`intraday_buy_daemon.py:640-641`).

### 8-2. 테스트 (`backend/tests/strategy/test_gold_zone.py`)

C1~C7 + 변동성필터/진입시간/PhaseD2.3 그룹:
- 상속·STRATEGY_ID·min_candles None (C1~C2, `test_gold_zone.py:62-69`)
- 합성 oversold→회복 시그널 확률성 (C3, `test_gold_zone.py:71-86`)
- ExitPlan(TP +2/+4, SL −0.015, time_exit 14:50, breakeven 0.01) (C4, `test_gold_zone.py:92-104`) — crypto 는 time_exit None
- position_size 균등(11주) (C5, `test_gold_zone.py:122-143`)
- health_check (C6, `test_gold_zone.py:149-155`)
- 변동성필터 default 0.0 / override 0.035 거부 / atr_n=14 (`test_gold_zone.py:175-225`)
- entry_time_cutoff default None / 14:00 차단 / 시뮬 빌드 14:00 적용 (`test_gold_zone.py:228-270`)
- **default min_score=5.0** 명시 검증 (`test_gold_zone.py:276-282`), min_conditions=2 유지 (`test_gold_zone.py:284-288`)
- C7 baseline 회귀: `@pytest.mark.skip` 상태(main 잔재 회귀 — f_zone trades=0, `test_gold_zone.py:161`).

### 8-3. 한계·리스크 (코드 기반)

1. **헤더 ↔ default 불일치**: 모듈 docstring 은 "BB 1% / Fib 0.382~0.618 / RSI 30→40" 이나 default 는 "3% / 0.236~0.786 / 35→38" — 설계 의도보다 진입조건이 완화됨. 운영 진실원천은 코드 default.
2. **고점 진입 구조적 약점**: 되돌림 전략 특성상 갭상승·폭등주에서 바닥 신호가 고점에서 발화 → DCA gate·gap-guard·진입 재검증이 모두 이 한 가지 리스크를 막기 위한 다중 방어선. 이 방어선들이 **default off / 플래그·env 조건부**라는 점이 운영 리스크(가드 미활성 시 노출).
3. **OOS 자체 미검증**: 본 리포트는 시뮬/백테스트를 재실행하지 않았다 — 8-1 수치는 코드 주석 인용이며 재현 검증은 별도 필요(미검증 명시).
4. **min_atr 일봉/분봉 분리**: 일봉 선정 0.035 vs 분봉 재검증 0.01 — 동일 전략이 단계마다 다른 변동성 임계를 써 신호 일관성 검증이 까다로움(`intraday_buy_daemon.py:625`).
5. **DCA gate 기본 off**: 5/29 한온시스템 교훈에도 `--dca-strategy-gate` 가 default off 라, 명시 활성화 전까지 gold_zone 물타기가 여전히 가능(pending tranche 존재 시).

## 9. 관련 파일·테스트

- 메인 전략: `backend/core/strategy/gold_zone.py` (294줄)
- 베이스: `backend/core/strategy/base.py` (`analyze`→`_analyze_v2` 위임, line 42-62)
- 지표: `backend/core/strategy/indicators.py` (`atr_pct` line 21-51; RSI/HTF 헬퍼)
- 라운드피겨 SL: `backend/core/strategy/round_figure.py` (`resolve_sl_pct` line 187-220)
- 트랩가드: `backend/core/strategy/trap_guard.py`
- 청산 평가: `backend/core/risk/holding_evaluator.py` (`STRATEGY_EXIT_PROFILES["gold_zone"]` line 125-134; `evaluate_holding` line 263-461)
- 스캐너: `backend/core/scanner/signal_scanner.py` (`_DEFAULT_ENABLED` line 41-55; `STRATEGY_PRIORITY` line 59-62; dispatch line 156-182)
- 운영 데몬: `scripts/intraday_buy_daemon.py` (`_MEANREV_STRATEGIES` line 627; DCA gate line 457-460; `_GAP_GUARD_STRATEGIES` line 658-659; `_ZONE_MAX_FLU` line 642; 진입 재검증 line 738-777)
- 시뮬 빌드: `backend/core/backtester/intraday_simulator.py` (gold_zone 분기 line 218-227)
- 비용: `backend/core/trading_costs.py` (line 29-37)
- 테스트: `backend/tests/strategy/test_gold_zone.py` (C1~PhaseD2.3, 306줄)
- 연관 테스트: `test_holding_evaluator.py`, `test_entry_revalidation.py`, `test_intraday_buy_daemon_strategies.py`, `test_force_close_exempt.py`, `test_signal_scanner_phase_c.py`, `test_intraday_only_strategies.py`

---
*진실원천 주석*: 본 리포트의 모든 수치·동작은 origin/main(013d54b) 코드 직접 인용(file:line)으로 확인. min_score=5.0·청산 프로파일 6값·비용 상수(0.35/0.20/0.90%)·DCA/gap-guard 특수처리는 코드 재확인 완료. 8-1 시뮬 수치는 코드 주석 인용(재실행 미수행, OOS 재현 미검증). 모듈 docstring(BB 1%/Fib 0.382~0.618/RSI 30→40)과 default 파라미터(3%/0.236~0.786/35→38)는 불일치 — default 가 운영 진실원천. `trading_costs.py:32` 의 "≈0.55%" 주석은 stale(실제 0.90%) 로 판단.
