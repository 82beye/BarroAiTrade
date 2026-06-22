# BarroAiTrade 매매전략 심층 리포트 — supertrend (슈퍼트렌드)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: 🟡 opt-in (--supertrend, standalone 경로) · 분류: 추세추종(ATR supertrend) · 컨셉: TradingView Pine "Supertrend"의 5분봉 결정적 이식 — ATR 밴드 추세전환을 진입(BUY 전환)·청산(SELL 전환)의 대칭 이벤트로 매매

## 1. 요약 (TL;DR)

- **무엇**: TradingView Pine Script "Supertrend"를 결정적으로 이식한 5분봉 추세추종 전략. `src=hl2`, `ATR(10) RMA(Wilder)`, `multiplier 3.0` 기본값(Pine 원본 충실 이식, `supertrend.py:1-18`, `:211-213`).
- **진입**: 추세 전환(trend −1→+1) **BUY 시그널** + 현재 상승추세(trend==1) + 최근 `entry_lookback(=2)`봉 내 전환 (`supertrend.py:311-318`). 횡보 휩쏘 방어로 ADX·FLIP·(opt-in) HTF RSI 게이트.
- **청산**: 다른 단타 전략과 **근본적으로 다른 메커니즘**. 가격 TP/SL(ExitEngine)이나 브로커 PnL(HoldingEvaluator)이 아니라, **전용 `SupertrendExitWatcher`** 가 SELL 전환(trend +1→−1) 이벤트를 보고 청산 시그널을 낸다(`supertrend_exit_watcher.py`). standalone 트레이더에서는 추가로 ATR 트레일·하드손절·익절·러너·이월갭스탑이 가격기반 OR로 동작(`supertrend_auto_trader.py:325-347`).
- **아키텍처**: SignalScanner의 `_DEFAULT_ENABLED`에 **없다**(`signal_scanner.py:41-55`). `SupertrendScanner`(진입) + `SupertrendExitWatcher`(청산) + `SupertrendAutoTrader`(자동매매)의 별도 경로로만 동작하며, intraday 데몬에서 `--supertrend` opt-in 플래그로만 가동된다(`intraday_buy_daemon.py:1709-1713`).
- **상태**: 코드 주석 기준 **약전략(weak strategy)**. out-of-sample 기대값이 여전히 음수라고 명시(`supertrend_auto_trader.py:81-82`). 이 때문에 SignalScanner `STRATEGY_PRIORITY`에서 supertrend=5(거의 최하위)로 비중을 억제(`signal_scanner.py:59-62`) + DCA(물타기) 제외(`intraday_buy_daemon.py:634`).

## 2. 전략 개요 (supertrend 지표·추세추종 컨셉)

`compute_supertrend`(`supertrend.py:143-205`)가 핵심 순수함수다. Pine 원본 충실 이식:

- `src = hl2 = (high+low)/2` (`_src_series`, `:86-92`).
- `ATR(period)` 는 `_rma`(Wilder RMA, seed=첫 period개 SMA)로 평활(`:51-67`, `:166`). 초기 nan 구간은 0.0으로 안정화(`:168`).
- 상승밴드 `up = src − mult·atr`, 직전 종가 `close[i−1] > up1` 이면 `up = max(up, up1)`로 lock-up(`:175`, `:185`).
- 하락밴드 `dn = src + mult·atr`, `close[i−1] < dn1` 이면 `dn = min(dn, dn1)`로 lock-down(`:176`, `:186`).
- 추세 전환: `prev==-1 and close[i] > dn1 → trend=+1`(BUY), `prev==1 and close[i] < up1 → trend=-1`(SELL) (`:188-194`).
- `buy_signals[i]` = trend −1→+1 전환 봉, `sell_signals[i]` = trend +1→−1 전환 봉(`:198-202`). `supertrend` 라인 = trend==1이면 up밴드, 아니면 dn밴드(`:204`).

보조 지표 `compute_adx`(Wilder ADX(14), `:95-140`)는 추세 강도(횡보 vs 추세) 측정용이며 진입 게이트에서 사용된다.

종목 유니버스(스캔 대상)는 **본 전략이 정하지 않는다** — 별도 모듈(최근 7일 거래대금 선별)이 담당하고, 전략은 주어진 종목의 5분봉에 신호만 산출하는 signal-only 설계(`supertrend.py:14-15`, `supertrend_scanner.py:3-9`).

## 3. 진입 로직 (밴드 전환·ATR·게이트·whipsaw 방어)  ← 코드 인용

`SupertrendStrategy._analyze_v2`(`supertrend.py:288-408`):

1. **최소 봉수**: `len(candles) < min_candles(=30)` 이면 None(`:291-292`).
2. **변동성 필터(운영 override)**: `min_atr_pct > 0` 면 `atr_pct(candles, n=atr_n=14) < min_atr_pct` 진입 거부(`:295-298`). 기본 0(비활성, `:228`).
3. **진입 시간 게이트(운영 override)**: `entry_time_cutoff` 이후 봉이면 거부(`:302-304`). 기본 None.
4. **현재 상승추세 확인**: `res.trend[-1] != 1` 이면 None — BUY 직후 추세 유지 안전판(`:311`).
5. **진입 트리거(이벤트성)**: `entry_lookback(=2)` 봉 내 BUY 시그널이 있어야 진입(`:315-318`). None이면 추세 지속 중 매봉 진입(스크리너 모드). "상승추세 동안 매 사이클 매수"가 아니라 "BUY 전환 이벤트 1회"로 청산과 대칭(`:216-219`).

**횡보 휩쏘(whipsaw) 방어** — 슈퍼트렌드는 횡보 박스권에서 BUY/SELL이 반복돼 비용만 소모하므로(주석: "차트 27~28일 구간", `:232-233`) 세 게이트가 있다(모두 기본 0=비활성, 기존 회귀 보존):

- **(1) ADX 게이트** (`:322-328`): `min_adx > 0` 면 `compute_adx(...)[-1] < min_adx` 봉 거부. 통상 20~25 권장(`:235-237`).
- **(2) 전환 강도(밴드 이탈폭) 게이트** (`:339-355`): BUY 전환 봉 종가가 "방금 돌파한 저항(전환 직전 봉 dn밴드)"을 `min_flip_atr_mult·ATR` 이상 넘어야 통과. 주석에 2026-06-01 정정 내역 명시 — 종전 `close − supertrend(현재 up밴드)` 측정은 BUY 전환 직후 up밴드가 ~3·ATR로 거의 무조건 통과해 게이트가 무력화됐던 버그를 `close − dn₁(직전 저항)` 기준으로 교정(`:333-338`, `:345-346`).
- **(3) 멀티 타임프레임 RSI 확인 게이트** (`:359-369`): `rsi_enabled` 면 상위 TF(기본 10분=`rsi_timeframe_mult 2`) RSI 골든크로스(`signal_cross` 모드)가 5분봉 BUY를 '확인(AND)'해야 진입. RSI 단독 진입 없음(`:244-261`). 기본 OFF.

**점수 산정**(`:376-380`): `6.0 + min(dist_pct/0.02,1)·2.0 + min(atr_pct/0.02,1)·2.0` → 추세선 대비 종가 여유 + 변동성 가산으로 6~10점. EntrySignal `signal_type="supertrend"`, `strategy_id="supertrend_v1"`(`:283`, `:392`, `:398`).

## 4. 청산 로직 (★전용 supertrend_exit_watcher — 다른 전략과 다른 청산 메커니즘)

### 4-1. 전략 단위 — `exit_on_signal` (`supertrend.py:410-480`)

진입(buySignal)의 정확한 거울상. Pine `sellSignal = trend == -1 and trend[1] == 1` 봉에서만 청산(`:416-420`).

- 롱(현물) 포지션만 대상(`:432`).
- `exit_lookback(=2)` 봉 내 SELL 시그널이 있어야 청산(`:439-441`). "하락추세 동안 매 사이클 청산"이 아니라 "SELL 전환 이벤트 1회"가 트리거(테스트 `test_supertrend_exit_watcher.py:84-92`가 stale SELL 미청산 검증).
- `rsi_exit_enabled` 면 SELL을 RSI 데드크로스가 '확인(AND)'해야 청산(`:447-457`). 기본 OFF.
- ExitSignal `exit_type="reverse_signal"`(`:466`, `:471-480`).

### 4-2. 전용 워처 — `SupertrendExitWatcher` (`supertrend_exit_watcher.py`)

★ **다른 단타 4종(sf/f/gold/swing_38)과 가장 다른 점**. 일반 전략은 가격 TP/SL(ExitEngine)·브로커 PnL(HoldingEvaluator)로 청산되지만, supertrend는 **지표 기반 청산 트리거**를 별도 모듈로 분리했다(`:1-15`).

- 입력: 현재 보유 Position 리스트. `strategy_id`가 `supertrend` prefix인 포지션만 대상, 다른 전략엔 개입 안 함(`:32`, `:63-66`, `:73`).
- 각 대상 종목 5분봉 재조회 → `strategy.exit_on_signal()` 호출(`:91-105`).
- signal-only — 실제 매도 주문은 상위 실행 레이어가 담당(`:14`).
- 검증: `test_watcher_ignores_other_strategy_positions`는 f_zone/swing_38 포지션이면 `get_ohlcv` 호출조차 안 함을 확인(`test_supertrend_exit_watcher.py:160-169`).

**중요(혼동 금지)**: `holding_evaluator.py`는 supertrend를 **전혀 참조하지 않는다**(grep 결과 0건). `STRATEGY_EXIT_PROFILES`(`holding_evaluator.py:104`)에 supertrend 키가 없다. supertrend 청산은 위 전용 경로가 책임진다.

### 4-3. standalone 자동매매 청산 (`supertrend_auto_trader.py:309-393`)

`SupertrendAutoTrader.run_cycle`은 청산을 먼저 평가한다(`:282-283`). SELL 전환 외에 **가격기반 리스크 청산이 OR로 우선** 작동(진입 불변·청산개선 효과, 2026-06-08 손익귀속 분석 근거 `:110-118`):

- **ATR 트레일(샹들리에)** `_trail_hit`: 진입 후 고점종가 − `trail_atr_mult(=3.0)`×ATR 이탈 시(`:328`, `:698-726`).
- **하드손절** `_hard_stop_hit`: `hard_stop_pct(음수)` 이하 손실 시(`:330`, `:789-798`). 데몬 기본 −6.0%(`intraday_buy_daemon.py:1300`).
- **이월갭스탑** `_carry_gap_stop_hit`: 이월(오버나잇) 포지션이 전일종가 대비 `carry_gap_stop_pct(=-3.0)` 이하 시(`:332`, `:603-630`).
- **러너 모드**(opt-in): 상한가/시초갭/TP도달 시 고정 익절 대신 최고점 추적 청산(`:336-341`, `:905-951`).
- **고정 익절** `_take_profit_hit`: `take_profit_pct(=5.0)` 도달 시(`:343`, `:728-737`).
- 위 가격기반이 잡지 않으면 **ST SELL 전환**(기준·필수, `:350-351`).

## 5. 파라미터 표 (ATR period, multiplier 등)

전략 파라미터 `SupertrendParams`(`supertrend.py:209-277`):

| 파라미터 | 기본값 | 의미 | 인용 |
|---|---|---|---|
| `atr_period` | 10 | Pine ATR Period | `:211` |
| `multiplier` | 3.0 | Pine Multiplier | `:212` |
| `source` | "hl2" | 밴드 중심선 | `:213` |
| `min_candles` | 30 | ATR 안정화 최소 봉수 | `:214` |
| `entry_lookback` | 2 | BUY 전환 확인창(봉) | `:220` |
| `exit_lookback` | 2 | SELL 전환 확인창(봉) | `:225` |
| `min_atr_pct` | 0.0 (비활성) | 변동성 필터 | `:228` |
| `atr_n` | 14 | atr_pct 산정 기간 | `:229` |
| `min_adx` | 0.0 (비활성) | ADX 횡보 게이트 | `:237` |
| `adx_period` | 14 | ADX 기간 | `:238` |
| `min_flip_atr_mult` | 0.0 (비활성) | 전환 이탈폭 게이트 | `:242` |
| `rsi_enabled` | False | HTF RSI 확인 마스터 | `:253` |
| `rsi_timeframe_mult` | 2 (=10분) | 상위 TF 배수 | `:254` |
| `rsi_period` / `rsi_signal_period` | 14 / 9 | RSI·시그널선 | `:255-256` |
| `rsi_mode` | "signal_cross" | 교합 모드 | `:257` |
| `trail_atr_mult` | 0.0 (전략 default 비활성) | ATR 트레일 | `:267` |
| `sl_min_pct` / `sl_max_pct` | −0.01 / −0.08 | exit_plan SL clamp | `:273-274` |
| `time_exit` | None | 장마감 강제청산(비활성) | `:277` |

**standalone 운영 기본값**(`SupertrendAutoConfig`, `supertrend_auto_trader.py:47-216`)은 전략 default와 다르게 휩쏘 게이트가 **ON**:

| 운영 파라미터 | 기본값 | 인용 |
|---|---|---|
| `max_positions` | 10 | `:54` |
| `min_price` | 1000원 (저가주 제외) | `:61` |
| `market_hours_only` | True (정규장만) | `:69` |
| `entry_start_time` | "09:30" (장초반 진입 차단) | `:75` |
| `min_adx` | **30.0** (BAR-OPS-33) | `:83` |
| `min_flip_atr_mult` | **1.5** | `:85` |
| `trail_atr_mult` | **3.0** | `:118` |
| `take_profit_pct` | **5.0** | `:122` |
| `max_intraday_range_pos` | 0.90 | `:130` |
| `single_tranche` | True (sync-loss 방지) | `:166` |
| `entry_cutoff_time` | "14:30" | `:211` |
| `carry_gap_stop_pct` | −3.0 | `:215` |

> ⚠️ 미검증: 데몬 `_get_supertrend_trader`(`intraday_buy_daemon.py:1280-1327`)는 `SupertrendAutoConfig`만 구성하고 내부 `config.params`(SupertrendParams)는 **override하지 않는다** → atr_period=10, multiplier=3.0, entry/exit_lookback=2는 default 그대로 사용. 휩쏘 게이트는 `SupertrendParams`(전략 경로)가 아니라 `SupertrendAutoConfig._whipsaw_pass`(`:632-696`)가 적용한다(이중 정의 — 혼동 주의).

## 6. 활성·운영 상태 (★standalone 아키텍처 / --supertrend opt-in / DCA 제외)

### 6-1. SignalScanner 경로 vs standalone 경로 (★혼동 금지)

다른 단타 4종(sf_zone/f_zone/gold_zone/swing_38)은 **SignalScanner 1분봉 경로**에서 `_DEFAULT_ENABLED=True`로 매매된다(`signal_scanner.py:41-55`). supertrend는 이 dict에 **키 자체가 없다** — SignalScanner는 supertrend를 스캔하지 않는다.

supertrend의 동작 경로는 완전히 분리되어 있다(설계 이유: SignalScanner는 1분봉, supertrend는 5분봉 → timeframe 충돌 회피, `supertrend_scanner.py:6-9`):

- `SupertrendScanner`(진입 신호, `supertrend_scanner.py`)
- `SupertrendExitWatcher`(청산 신호, `supertrend_exit_watcher.py`)
- `SupertrendAutoTrader`(자동 진입+청산 루프, `supertrend_auto_trader.py`) — intraday 데몬이 실제 운영에 쓰는 경로

`STRATEGY_PRIORITY`에 supertrend=5가 있는 것(`signal_scanner.py:61`)은 **슬롯/자본 경합 시 점수 동률 tiebreaker**용 정의일 뿐, SignalScanner가 supertrend를 발행한다는 의미가 아니다. 거의 최하위(blue_line 6, crypto 7, closing_bet 8만 아래)로 둬 비중을 억제한다(약전략 처방, `supertrend_auto_trader.py:81-82`).

### 6-2. --supertrend opt-in 배선 (`intraday_buy_daemon.py`)

- 플래그: `--supertrend`(action store_true, 기본 OFF), `--supertrend-top(=10)`, `--supertrend-max-pos(=10)`, `--supertrend-interval(=300s)`(`:1709-1723`).
- 가동 시각: `--supertrend` 활성 시 데몬 개시를 `SUPERTREND_OPEN=09:00`으로 앞당김(일반 전략은 `MARKET_OPEN=09:05`)(`:58`, `:64`, `:1586`).
- 사이클 순서: ①매도 평가 → ②일반 전략 매수 → ③supertrend 사이클(`:1617-1648`). 5분봉이므로 `supertrend_interval(300s)`마다 throttle(429 회피, `:1596-1640`).
- **이중 주문 방지**: `run_telegram_bot`의 `SupertrendAutoTrader`가 담당 중(`SUPERTREND_AUTO_ENABLED` truthy)이면 데몬 `--supertrend`를 강제 OFF(`_supertrend_yield_to_bot`, `:101-109`, `:1736-1740`).
- 단독 운영: `--strategies '' --supertrend`로 일반 전략 끄고 슈퍼트렌드만 운영 가능(`:1730`).

### 6-3. DCA(물타기) 제외

`_NO_DCA_STRATEGIES = {"swing_38", "supertrend"}`(`intraday_buy_daemon.py:634`). 트레이더가 전량 단일주문 진입인데 장부가 60/40 트랜치로 기록돼, 데몬 DCA가 가짜 tranche2를 실주문으로 추가 발사한 사고(6/10 319660: 장부 23 vs 실보유 32주 = 권고의 139%)를 막기 위한 방어선(`:630-634`). 1차 처방은 `single_tranche=True`(`supertrend_auto_trader.py:164-166`).

## 7. 비용·손익분기 관점 (whipsaw 빈도 vs 비용)

- 거래비용(공통 사실): 편도 0.35% / 매도세 0.20% / 왕복 0.90%. (※ supertrend 코드 내 비용 상수 인용은 없음 — 미검증, 외부 공통 가정.)
- supertrend의 구조적 약점은 **횡보 휩쏘**다. 코드 주석 자체가 "횡보 박스권에서 BUY/SELL이 반복돼 비용만 소모"라고 명시(`supertrend.py:232-233`). 왕복 0.90% 비용을 감안하면 박스권 가짜 전환의 반복은 직접적 손실원이다.
- 처방: ADX≥30 + FLIP≥1.5 게이트로 거짓 전환을 차단. 코드 주석의 sweep 근거(BAR-OPS-33, 4~6월): "adx≥30·flip≥1.5 시 거래 125→30건, MDD −41→−22, 전체 기대값 −0.08→+0.20"(`supertrend_auto_trader.py:78-83`). 거래 빈도를 1/4로 줄여 휩쏘·비용 드래그를 최소화하는 방향.

## 8. 백테스트·OOS 근거 / 한계·리스크 (whipsaw)

코드 주석에 기록된 백테스트/복기 수치(별도 백테스트 산출물 파일은 미발견 — 주석 인용에 한함):

- BAR-OPS-33 sweep: adx≥30·flip≥1.5 → 거래 125→30, MDD −41→−22, 기대값 −0.08→+0.20. **단 out-of-sample은 여전히 음수** → "약전략"으로 명시(`supertrend_auto_trader.py:80-83`).
- 휩쏘 백테스트(전략 주석): ADX≥25/FLIP≥1.0이 PF 1.76→2.00, 승률 35→44%(`:78-79`).
- 2026-06-08 손익귀속(`:110-118`): 패배거래 평균 MFE +2.5~3.9%(수익 거쳤다 손실 청산)·청산후 +2.8% 반등(저점매도) → 손실 1차 원인이 "청산 지연". 트레일3.0+익절5%+고점위치≤90% 진입 시 22일 −3.27%→+2.73%.
- 트레일 단독: 22일 −3.27%→+0.93% 흑자전환(`:117-118`).
- ATR 트레일 백테스트 권장 4.0: 최악손실 −25%→−11.5% 캡(단 총수익 일부 희생)(`supertrend.py:265-266`).

**한계·리스크**:
1. 횡보 휩쏘(구조적) — 비용 드래그.
2. OOS 기대값 음수 — priority 최하위 + DCA 제외 + 비중 억제로 관리(처방이지 해결 아님).
3. 청산 지연(SELL 전환 지각) — 가격기반 트레일/하드손절/익절 OR로 보완.
4. 이월(오버나잇) 갭하락 — 6/9 막판 진입 5종이 6/10 갭하락 −845K(6/9 +1,585K의 37% 반납), entry_cutoff 14:30 + carry_gap_stop −3% 처방(`supertrend_auto_trader.py:205-215`).
5. 저가주/고변동주 수량 폭주 — min_price 1000원 + max_order_qty/value 하드캡(6/2 252670 38,219주 RuntimeError, `:87-90`, `:739-756`).

## 9. 관련 파일·테스트

- 메인 전략: `backend/core/strategy/supertrend.py` (522줄, `compute_supertrend`/`compute_adx`/`SupertrendStrategy`/`SupertrendParams`)
- standalone 자동 트레이더: `backend/core/supertrend_auto_trader.py` (1065줄, `SupertrendAutoTrader`/`SupertrendAutoConfig`)
- 진입 스캐너: `backend/core/scanner/supertrend_scanner.py`
- 전용 청산 워처: `backend/core/scanner/supertrend_exit_watcher.py`
- 데몬 배선: `scripts/intraday_buy_daemon.py` (`--supertrend` 플래그 `:1709-1723`, `_get_supertrend_trader` `:1280-1327`, `_run_supertrend_cycle` `:1331+`, `_NO_DCA_STRATEGIES` `:634`)
- SignalScanner(미등록 확인): `backend/core/scanner/signal_scanner.py` (`_DEFAULT_ENABLED` `:41-55`, `STRATEGY_PRIORITY` `:59-62`)
- HoldingEvaluator(미참조 확인): `backend/core/risk/holding_evaluator.py` (supertrend 0건)
- 테스트:
  - `backend/tests/scanner/test_supertrend_exit_watcher.py` (청산 워처·exit_on_signal 14케이스 — SELL 전환 청산, stale SELL 미청산, 타 전략 무개입)
  - `backend/tests/strategy/test_supertrend_whipsaw.py` (ADX·FLIP 게이트 — 횡보 거부/추세 통과)

---
*진실원천 주석*: 본 리포트의 모든 수치·로직은 origin/main(`013d54b`) 기준 코드 인용(file:line)으로 검증. 거래비용(0.35/0.20/0.90%)은 외부 공통 가정으로 코드 상수 인용 없음(미검증). 별도 백테스트 산출물 파일은 발견되지 않았으며, 백테스트/OOS 수치는 전부 코드 주석(BAR-OPS-33/BAR-OPS-10/2026-06-08~10 복기)에 기록된 내용을 인용한 것이다. SupertrendParams의 휩쏘 게이트 기본값(min_adx=0 등)과 SupertrendAutoConfig 운영 기본값(min_adx=30 등)이 다르며, 데몬은 후자만 구성하고 전자(config.params)는 override하지 않음을 확인.
