# BarroAiTrade 매매전략 심층 리포트 — closing_bet (종가베팅)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: ⚪ 구현됨·비활성 (default OFF, 자동매수 BARRO_CB_AUTOEXEC opt-in) · 분류: 일봉 진입+오버나잇(D1~D3) · 컨셉: 장 막판 15:00~15:20 신고가 돌파 장대양봉 주도주를 종가에 잡아 익일 아침 슈팅(+4~5%)에 파는 오버나잇 1박 단타

## 1. 요약 (TL;DR)

- **무엇**: "더트레이딩 방법론"의 시그니처 기법. 당일 주도주를 장 막판 종가 근처에 매수해 익일 9~10시 +4~5% 슈팅에 매도하는 **오버나잇 1박 단타** (`closing_bet.py:4-5`).
- **활성 상태**: `SignalScanner._DEFAULT_ENABLED["closing_bet"] = False` (`signal_scanner.py:54`) — 라이브 스캐너에서 **비활성 스캐폴딩**. 코드는 완비됐으나 자동 dispatch 안 됨.
- **자동매수 executor**: `closing_bet_alert_daemon.py` 의 `BARRO_CB_AUTOEXEC`(default `"0"`=OFF, `alert_daemon:107-108`). opt-in 으로 켜야 BUY 자동 송출. SELL 은 여전히 신호 전용. 라이브 실거래는 `AUTOEXEC=1` + `--no-dry-run` 동시 충족 시에만 → ★HITL 게이트(`eod-auto-execution.design.md:99`).
- **검증 결과 양면성**: 공식 OOS 관문 3 seed 전부 PASS(avg_ret +3.48~3.89%, `validation.report.md:27-29`)지만, 그 수익은 **"익일 시초 진입" 가정에 의존** — '종가 진입'으로 측정하면 **net 브레이크이븐**(`backtest.report.md:47`). 이격도(disparity_yellow) 게이트 ON 시 net @0.90 +0.405%로 개선되며 robustness 통과(`disparity-gate-robustness.json`).
- **핵심 리스크**: 오버나잇 갭 + 왕복 비용 0.90%. +4.5% TP 마진의 약 20%를 비용이 잠식 → 비용/체결가가 종베 손익을 결정.

## 2. 전략 개요 (종가베팅 컨셉·진입 시간창)

종베는 **EOD(End-Of-Day) 선정 + 오버나잇 캐리** 구조다 (`closing_bet.py:11-17`):

- **진입 시간창**: KST **15:00~15:20** (`ClosingBetParams.entry_window_start=dtime(15,0)`, `entry_window_end=dtime(15,20)`, `closing_bet.py:56-57`). 인트라데이 데몬 진입 cutoff 14:30 밖이라 자동진입은 별도 EOD dispatch 가 필요 (`eod-auto-execution.design.md:18`).
- **진입 대상**: 당일 주도주 중 **신고가 돌파 장대양봉**.
- **보유**: 오버나잇 1박 (D1), 최대 D3 (`max_hold_days=3`, `closing_bet.py:102`).
- **청산**: 익일 아침 슈팅 익절(+4.5%, 대형주 +2%) / 0.618 이탈 SL / 익일 10:00 시간청산 (`closing_bet.py:96-103`).

구현 범위는 **Increment 1 = 일봉 컨텍스트 + ctx.timestamp 진입창 자기완결 스캐폴드** (`closing_bet.py:12-13`). 분봉 자금유입·존(F/골드존) 진입가·거래대금 rank/시총 hard-cut 은 **전부 default-OFF 옵션**으로 두고, 라이브 활성(별도 HITL)에서 intraday/선정 컨텍스트를 주입해 켜는 구조 (`closing_bet.py:14-16`).

## 3. 진입 로직 (시간창·신고가·장대양봉·자금유입·이격도 게이트)

진입 판정은 `ClosingBetStrategy._analyze_v2()` (`closing_bet.py:140-261`). 게이트는 순서대로 평가되며, 어느 하나라도 실패하면 `None`(미진입):

**① 캔들 수 / ② 진입 시간창 (핵심 차별점)** — `closing_bet.py:143-149`
```python
if len(candles) < p.min_candles:          # min_candles=70 (closing_bet.py:87)
    return None
if p.require_eod_window and not self._in_entry_window(ctx.timestamp):
    return None
```
`_in_entry_window` 은 ctx.timestamp 를 KST 로 변환해 `[15:00, 15:20]` 안인지 검사 (`closing_bet.py:265-271`).

**③ 신고가 돌파 (일봉)** — `closing_bet.py:156-163`
```python
prior = candles[-(p.new_high_lookback + 1):-1]   # new_high_lookback=60 (closing_bet.py:62)
prior_high = max(c.high for c in prior)
if today.high < prior_high * (1.0 - p.new_high_tolerance):   # tolerance default 0.0 = 완전 돌파
    return None
```
`new_high_tolerance>0`(default 0.0) 이면 '전고점 이격(near-high)' 모드로, 신고가 미돌파라도 전고점 근접 종목까지 허용 (`closing_bet.py:63-66`).

**④ 기준봉 = 장대양봉** — `closing_bet.py:174-182`
```python
body = (today.close - today.open) / today.open
if body < p.base_min_gain_pct:            # 몸통 ≥ 5% (base_min_gain_pct=0.05, closing_bet.py:81)
    return None
upper_wick_ratio = (today.high - today.close) / body_abs
if upper_wick_ratio > p.base_upper_wick_max:   # 윗꼬리/몸통 상한 1.0 (closing_bet.py:82)
    return None
```

**④-b 이격도 노란불 게이트 (옵션, default OFF)** — `closing_bet.py:184-189`
```python
if p.require_disparity_yellow and not disparity_yellow(
        candles, threshold=p.disparity_yellow_threshold):   # 5일선 이격 ≥ +14.25%
    return None
```
`disparity_yellow()`(`closing_bet_filters.py:229-236`)는 `(종가-SMA5)/SMA5 ≥ 0.1425` 이면 True. 종베 net edge 의 핵심 동인 (`closing_bet.py:90-92`).

**그 외 옵션 게이트 (전부 default OFF, 회귀 byte-identical)**:
- ③-b 기간조정 `consolidation_ok` (`closing_bet.py:165-172`, `consolidation_min_days=0`)
- ④-c 상대 거래대금 급증 `rel_volume_surge` (`closing_bet.py:191-195`, `rel_volume_lookback=0`)
- ⑤ 거래대금 rank/시총 hard-cut (`closing_bet.py:199-209`, `require_leader_meta=False`)
- ⑤-b 외인/기관 양매수 (`closing_bet.py:211-217`, `require_dual_net_buy=False`)
- ⑥ 존(골드존) 0.5~0.618 되돌림 (`closing_bet.py:219-223`, `require_zone=False`)
- ⑦ 분봉 자금유입 BLOCK 판정 (`closing_bet.py:225-228`, `require_money_flow=False`)

**자금유입 등급** `_money_flow_grade` (`closing_bet.py:278-304`): 분봉에서 오전(09:00~11:30)/오후(13:00~15:20) 거래대금 Σ(close×volume)을 집계해 BOTH/PM_ONLY/BLOCK 판정. "오전 유입 후 오후 급감"(오후 < 오전×0.3)은 BLOCK (`closing_bet.py:120,293`).

진입 시 `EntrySignal` 발행 — `signal_type="closing_bet"`, `metadata`에 `overnight=True`, `stop_fib_price`(고점-범위×0.618), `max_hold_days` 포함 (`closing_bet.py:239-261`).

## 4. 청산 로직 (SL-5 / TP+4.5 / 부분2.7 / trailing3.5 / D1~D3)

청산은 **두 레이어**로 구성:

**(A) Strategy.exit_plan (1차 방어선, 분봉 close 기반)** — `closing_bet.py:348-391`
- TP1: avg × (1 + tp×0.6) × 50% 분할 (1차 저항) (`closing_bet.py:373-377`)
- TP2: avg × (1 + tp) × 50% — `tp_shoot_pct=0.045`(+4.5%), 대형주 `tp_shoot_pct_largecap=0.02`(+2%) (`closing_bet.py:96-98`, `:361`, `:378-382`)
- SL: `stop_loss_pct=-0.03`(-3%)와 0.618 이탈가(`stop_fib_price`) 중 **더 보수적(가까운)** 쪽 (`closing_bet.py:364-369`)
- `time_exit=morning_exit_time`(익일 10:00) (`closing_bet.py:99`, `:387`)
- `breakeven_trigger=0.02`(+2%), `min_hold_days=None`(익일 즉시 청산 허용), `max_hold_days=3` (`closing_bet.py:388-390`)

⚠️ 오버나잇 의미론 주의: `time_exit` 은 *익일* 아침인데 ExitEngine 이 '당일 그 시각'으로 해석하면 진입일 즉시청산 버그 → 라이브 활성 시 overnight 플래그로 skip 필요. 현 increment 는 비활성이라 latent (`closing_bet.py:351-356`).

**(B) STRATEGY_EXIT_PROFILES["closing_bet"] (2차 안전망, broker pnl_rate 기반)** — `holding_evaluator.py:158-169`
```python
"closing_bet": {
    "stop_loss_pct": Decimal("-5.0"),      # 0.618 이탈 + 익일 갭하락 흡수 (ExitPlan -3%보다 너그러운 fallback)
    "take_profit_pct": Decimal("4.5"),
    "partial_tp_pct": Decimal("2.7"), "partial_tp_ratio": Decimal("0.5"),
    "trailing_start_pct": Decimal("3.5"), "trailing_offset_pct": Decimal("1.0"),
    "breakeven_trigger_pct": Decimal("2.0"), "tightened_sl_pct": Decimal("-3.0"),
    "min_hold_days": 1, "max_hold_days": 3,   # D1(익일 청산 허용)~D3(4일차 = 5일선 붕괴 청산)
}
```
- **min_hold 1 / max_hold 3** = 익일 D1~D3. `evaluate_holding` 에서 max_hold 도달 시 손익 무관 강제 매도(`TIME_TIGHTENED_SL`), min_hold 미달 시 청산 평가 차단 (`holding_evaluator.py:296-311`).
- 이 프로파일은 `strategy_id="closing_bet_v1"` 포지션이 생겨야 매칭 → 그 전엔 **inert(라이브 무영향)** (`holding_evaluator.py:154`). `resolve_policy("closing_bet_v1")` 가 버전 접미사 제거 후 매핑 (`holding_evaluator.py:173-195`).
- SL -5%(profile) vs -3%(exit_plan) 격차는 **의도된 fallback 안전망** — ExitEngine 누락(데몬 다운/분봉 fetch 실패) 시 broker pnl 기반 2차 청산 (`holding_evaluator.py:85-101`).

라운드피겨(round_figure) 손절 보정: 두 레이어 모두 `resolve_sl_pct()` 경유 (`closing_bet.py:32,384`; `holding_evaluator.py:415`). 단 `RF_STOP_ENABLED` default 0 이라 base_pct 그대로 반환 — **완전 무영향**(`round_figure.py:201-202`).

## 5. 파라미터 표

### ClosingBetParams (`closing_bet.py:47-129`)
| 파라미터 | 기본값 | 의미 | line |
|---|---|---|---|
| entry_window_start/end | 15:00 / 15:20 | 진입 시간창(KST) | 56-57 |
| require_eod_window | True | 시간창 밖 진입 거부 | 58 |
| require_new_high | True | 신고가 돌파 요구 | 61 |
| new_high_lookback | 60 | 직전 N봉 고점 비교 | 62 |
| new_high_tolerance | 0.0 | 전고점 이격 허용폭(0=완전돌파) | 63 |
| base_min_gain_pct | 0.05 | 장대양봉 몸통 ≥5% | 81 |
| base_upper_wick_max | 1.0 | 윗꼬리/몸통 상한 | 82 |
| min_atr_pct | 0.0 | ATR 필터(0=비활성) | 85 |
| min_candles | 70 | 최소 캔들 | 87 |
| require_disparity_yellow | **False** | 이격도 게이트(옵션) | 92 |
| disparity_yellow_threshold | 0.1425 | 5일선 이격 +14.25% | 93 |
| tp_shoot_pct / _largecap | 0.045 / 0.02 | 익일 슈팅 익절 | 96-97 |
| largecap_market_cap | 5.0e12 | 대형주 판정 시총(5조) | 98 |
| morning_exit_time | 10:00 | 익일 시간청산 | 99 |
| max_hold_days | 3 | D1~D3 | 102 |
| stop_loss_pct | -0.03 | 보조 고정 SL | 103 |
| require_zone / gold_fib_low/high | False / 0.5 / 0.618 | 골드존 되돌림(옵션) | 109,111-112 |
| require_money_flow | False | 분봉 자금유입 게이트(옵션) | 114 |
| flow_min_value / flow_death_ratio | 1.0e9 / 0.3 | 유입 하한 10억 / 오후死 비율 | 119-120 |
| require_leader_meta / max_trade_value_rank / min_trade_value | False / 5 / 3.0e10 | 선정 hard-cut(옵션) | 123-125 |

### holding_evaluator 프로파일 (`holding_evaluator.py:158-169`)
SL -5.0 / TP +4.5 / partial 2.7@0.5 / trailing start 3.5 offset 1.0 / breakeven 2.0 / tightened -3.0 / min_hold 1 / max_hold 3.

### closing_bet_filters 임계 (관측 전용 순수함수, 호출처 inert — `closing_bet_filters.py:6-8`)
| 함수 | 임계 | line |
|---|---|---|
| body_new_high | lookback 60(몸통 종가 기준 신고가) | 20-36 |
| overheat_warning | 5일 전 종가 ×1.6(+60%) | 39-58 |
| liquidity_ok | 1분봉 15억 AND 당일/전일 ×3 | 61-85 |
| rel_volume_surge | 평균 ×2.0 | 88-111 |
| consolidation_ok | min_days 10, lookback 60 | 114-153 |
| disparity_5ma / disparity_yellow | (종가-SMA5)/SMA5 ≥ 0.1425 | 212-236 |
| envelope_upper_break | SMA20 × 1.20 돌파 | 191-209 |
| triple_factor_buy | 엔벨로프∧이격노란불∧거래대금≥1000억 | 239-270 |

### round_figure (`round_figure.py`) — 가격대별 라운드 지지/저항·SL 보정
env 토글 `RF_STOP_ENABLED`(default 0)·`RF_STOP_DRY_RUN`(default 1)·`RF_MAX_STOP_PCT_INTRADAY`(0.04)·`RF_BUFFER_PCT`(0.003) (`round_figure.py:40-46`). closing_bet 에서 `resolve_sl_pct(STRATEGY_ID, avg, sl_pct, ...)` 로 호출되나 default OFF 라 base SL 그대로 (`round_figure.py:201-202`).

## 6. 활성·운영 상태 ★

### 6.1 스캐너 — 비활성 스캐폴딩
- `_DEFAULT_ENABLED["closing_bet"] = False` (`signal_scanner.py:54`) — **라이브 동작 무영향**.
- `STRATEGY_PRIORITY["closing_bet"] = 8`(최하위 tiebreaker) (`signal_scanner.py:61`).
- 인스턴스는 비활성이라도 생성됨(`self.closing_bet = ClosingBetStrategy(...)`, `signal_scanner.py:114`).
- dispatch 경로: **일봉**. `if self._enabled["swing_38"] or self._enabled.get("closing_bet", False)` 일 때만 일봉 fetch 후 `closing_bet.analyze()` 호출 (`signal_scanner.py:184-211`). 활성화 = `SignalScanner(..., enabled_strategies={"closing_bet": True})` (`signal_scanner.py:52-53`).

### 6.2 ★자동매수 executor — BARRO_CB_AUTOEXEC (default-OFF, BUY 한정)
최근 커밋 `0a1f056` "feat(closing_bet): 종베 자동매수 executor (BARRO_CB_AUTOEXEC, default-OFF) — BUY 한정". `closing_bet_alert_daemon.py` 의 alert daemon 승격(설계 B안).

발동 조건/경로:
```python
def _cb_autoexec() -> bool:                       # alert_daemon:107-108
    return os.environ.get("BARRO_CB_AUTOEXEC", "0").strip().lower() in {"1","true","yes","on"}
```
`scan_buy` 에서 신호 발생 시 `if _cb_autoexec() and not from_cache:` → `_cb_auto_buy()` (`alert_daemon:278-283`). 그 외(default)는 "※ 자동매수 안 함 — 직접 매수 판단" 알림만 (`alert_daemon:284-289`).

`_cb_auto_buy` 가드 (`alert_daemon:116-155`):
- 이미 보유분 중복 매수 방지 (`:121-122`)
- 동시 보유 `BARRO_CB_MAX_POS`(default 2) 한도 (`:110,123-124`)
- 종목당 비중 `BARRO_CB_MAX_PCT`(default 0.10) — orderable_cash 기준 사이징 (`:111,132`)
- 단일 트랜치, `LiveOrderGate.place_buy(strategy_id="closing_bet")` (`:142-147`)
- `daily_max_orders` = `BARRO_CB_MAX_ORDERS`(default 10) (`:144`)
- 실체결 시 즉시 `closing_bet_positions.json` 등록(TP 4.5 / SL 5.0) (`:112-113,155`)

**SELL 은 자동화 범위 밖** — `scan_sell` 은 TP/SL/MORNING/D3 신호 텔레그램 알림만, "※ 자동매도 안 함" (`alert_daemon:223-233`). 매도 신호 판정은 `sell_signals()`(TP/SL/익일10시 MORNING/D3 달력일) (`alert_daemon:171-185`).

### 6.3 EOD dispatch 설계 / dry-run 통과기준
설계 `eod-auto-execution.design.md`: 인트라데이 데몬에 EOD 블록을 넣지 않고 **alert daemon 에 executor 부착**(B안, `:24-28`). ★결정적 리스크 = 인트라데이 데몬 `_eod_carry_limit`(15:10~15:19 오버나잇 축소)이 종베(의도적 오버나잇)를 청산 → **종베 포지션 제외 필수**(`:63-66`).

**1차 dry-run 통과 기준 4개**(이격도 게이트 ON 기준, `design.md:105-113`):
| # | 기준 | 합격선 |
|---|---|---|
| 1 | 표본 | 1~2주 매수신호 ≥ 10건 |
| 2 | net 수익 | 익일가 평균 net > 0(왕복 0.90% 차감), 이상적 ≥ +0.4% |
| 3 | 적중·whipsaw | 익일 슈팅(09~10시) 도달이 손실신호 대비 우위 |
| 4 | 운영 무사고 | 알림·포지션 등록 정상(중복/누락 없음) |

HITL 순서: ① dry-run(알림/페이퍼) → ② `AUTOEXEC=1 --dry-run`(주문 로직만, 미체결) → ③ sim-live 정합 → ④ 라이브 `--no-dry-run` ★HITL 소액 1종목 (`design.md:97`). 롤백 = `BARRO_CB_AUTOEXEC=0` 즉시 (`design.md:100`).

### 6.4 수동 페이퍼·알림 스크립트
- `closing_bet_paper_scan.py` — 페이퍼 스캐너(CSV `data/closing_bet_paper.csv` append, 주문 0). 파라미터: `require_eod_window=False, require_money_flow=True, min_atr_pct=0.035, require_disparity_yellow=_CB_DISPARITY, threshold=0.1425` (`paper_scan:47-50`).
- `closing_bet_alert_daemon.py` — 텔레그램 알림 데몬. dry-run 파라미터 동일(`alert_daemon:72-74`).
- 두 스크립트 모두 `BARRO_CB_DISPARITY_YELLOW`(default "0"=OFF) 토글로 이격도 게이트 제어 (`alert_daemon:69-71`, `paper_scan:46`).

## 7. 비용·손익분기 + 오버나잇 갭 리스크

- **비용**: 편도 0.35% / 매도세 0.20% / 왕복 **0.90%**. 종베는 익일 갭/슈팅을 노리는 오버나잇 보유 → **갭 리스크가 핵심**.
- **손익분기**: 종베 full gross +0.895%/트립에 왕복 0.90% 차감 시 net **-0.005% (브레이크이븐)** (`backtest.report.md:43-49`). +4.5% TP 마진의 약 20%를 비용이 잠식 (`backtest.report.md:55`).
- **비용 시나리오**: 우대 0.40% → net +0.495%, 모델 0.55% → +0.345%, 실측 0.90% → -0.005% (`backtest.report.md:43-48`).
- **오버나잇 갭**: SL -5%(0.618 이탈 + 익일 갭하락 흡수)와 익일 10:00 시간청산(MORNING)이 2차망 (`design.md:77`). 설계는 6/10 -845K 사례를 구조적 갭 리스크로 명시, 동시 1~2종·비중 10% 상한 엄수 권고 (`design.md:118`).
- **이격도 게이트 효과**: 게이트 ON 시 baseline net +0.008% → +0.52% (`disparity-gate-robustness.json` A_full). 임계 0.1425 에서 trips 3427→1586, 승률 54.2%→62.3%.

## 8. 백테스트·OOS·dry-run 근거 / 한계·리스크

**일봉 백테스트 (2026-06-17, `backtest.report.md`)**:
- 종베 full: trips 3,531 · 승률 55.6% · gross +0.895% · PF 1.82 · 평균보유 0.17봉(대부분 익일 청산) (`:34`).
- ablation: 신고가 필터가 엣지를 더하지 않음(full gross 0.895 < 장대양봉-only 0.985) → 신고가 게이트 재검토 대상 (`:57`).

**분봉 게이트 ablation (2026-06-18, `validation.report.md:46-54`)**:
- money_flow: 미세 개선(gross +0.02%, 트립 39%↓), 약한 양(+).
- **zone: 오히려 악화**(net @0.90 -0.189%). 장대양봉 신고가 종가(고점 근처)와 골드존 0.5~0.618 되돌림은 **같은 날 양립 불가**(개념 충돌, 단위테스트로도 재현) → **현 형태 zone 게이트 폐기/재설계** (`:54`).

**공식 OOS 관문 3/3 PASS (실측비용 0.0035 반영, `validation.report.md:23-40`)**:
- seed 42/7/123 → avg_ret +3.48 / +3.89 / +3.66%, holdout +3.9~4.7%, drop1 부호안정.
- ⚠️ ★핵심 caveat: 이 수익은 **"익일 시가 진입"**(entry_on_next_open) 가정에 의존. '종가 진입'으로 재측정하면 **브레이크이븐**으로 회귀 (`:62-68`). 라이브 진입 체결가가 이 가정과 일치해야 OOS 수익 재현.

**이격도 robustness (2026-06-21, `disparity-gate-robustness.json`)**: 5 seed 전부 게이트 delta_net90 +0.50~0.55%p, 기간분할(early/late) 모두 +0.45~0.57%p, 임계 sweep 전 구간 net>0 → **verdict "ROBUST"**.

**삼박자(triple_factor) shadow (2026-06-21, `closing-bet-triple-shadow.json`)**: 삼박자 통과 net @0.90 +0.286% vs baseline +0.107%(Δ+0.179%p), disp_pass 단독 +0.405%(승률 61.8%) → 엣지 기여 확인.

**한계·리스크 종합**:
1. TP-우선 intrabar 낙관 — 백테스터가 D+1에서 TP(고가)를 SL(저가)보다 먼저 평가 → 승률/gross 상향 편향 (`backtest.report.md:23`).
2. 종가 동시호가 체결가 불확실(단일가) → sim-live 괴리, 분할매수 미체결 시 평단 왜곡 (`design.md:117`).
3. 차별화 게이트(intraday) 효과 일부만 검증 — zone 폐기, money_flow 약한 양(+).
4. 2026-06-18 "수동관리 전용" 결정 → 자동매매 전환은 사용자 명시 승인 후 (`design.md:116`).

## 9. 관련 파일·테스트

**코드**:
- `/Users/beye/workspace/BarroAiTrade/backend/core/strategy/closing_bet.py` (메인 — Params + analyze + exit_plan, 407줄)
- `/Users/beye/workspace/BarroAiTrade/backend/core/strategy/closing_bet_filters.py` (보조 필터, 관측 전용 inert, 270줄)
- `/Users/beye/workspace/BarroAiTrade/backend/core/strategy/round_figure.py` (라운드피겨 SL 보정, default OFF)
- `/Users/beye/workspace/BarroAiTrade/backend/core/risk/holding_evaluator.py:158-169` (STRATEGY_EXIT_PROFILES["closing_bet"])
- `/Users/beye/workspace/BarroAiTrade/backend/core/scanner/signal_scanner.py:41-62,184-211` (default OFF 등록 + 일봉 dispatch)
- `/Users/beye/workspace/BarroAiTrade/scripts/closing_bet_alert_daemon.py` (★자동매수 executor BARRO_CB_AUTOEXEC + 알림)
- `/Users/beye/workspace/BarroAiTrade/scripts/closing_bet_paper_scan.py` (수동 페이퍼 스캐너)

**설계/리포트**:
- `/Users/beye/workspace/BarroAiTrade/docs/02-design/features/2026-06-22-closing-bet-eod-auto-execution.design.md`
- `/Users/beye/workspace/BarroAiTrade/docs/04-report/features/2026-06-22-closing-bet-dryrun-disparity.report.md`
- `/Users/beye/workspace/BarroAiTrade/docs/04-report/features/2026-06-17-closing-bet-backtest.report.md`
- `/Users/beye/workspace/BarroAiTrade/docs/04-report/features/2026-06-18-closing-bet-validation.report.md`
- `/Users/beye/workspace/BarroAiTrade/docs/04-report/features/2026-06-21-disparity-gate-robustness.json`
- `/Users/beye/workspace/BarroAiTrade/docs/04-report/features/2026-06-21-closing-bet-triple-shadow.json`

**테스트**: `/Users/beye/workspace/BarroAiTrade/backend/tests/strategy/test_closing_bet.py` — 상속/캔들부족/진입창/신고가·장대양봉/이격도 게이트/신정재 필터/ExitPlan/inert 프로파일/스캐너 default OFF(`:254-257`)/money_flow/zone 단위 검증.

---
*진실원천 주석: 모든 수치는 origin/main 코드·문서의 file:line 인용. ClosingBetParams/holding_evaluator 프로파일/signal_scanner default OFF/alert_daemon BARRO_CB_AUTOEXEC 직접 확인. 백테스트·OOS·robustness·shadow 수치는 docs/04-report 산출물 인용. 라이브 자동매매는 현재 default OFF·opt-in·HITL 단계 — 본 리포트는 이를 과장 없이 명시한다.*
