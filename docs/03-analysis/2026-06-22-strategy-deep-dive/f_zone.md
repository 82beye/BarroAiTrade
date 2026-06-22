# BarroAiTrade 매매전략 심층 리포트 — f_zone (F존)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: 🟢 활성 (default ON) · 분류: 1분봉 단타 · 컨셉: 급등(기준봉) 후 눌림목에서 이평선 지지+반등 캔들 확인 후 매수하는 5단계 추세 추종 단타

## 1. 요약 (TL;DR)

- **컨셉**: "급등(기준봉) → 눌림목 조정 → 이평선 지지 → 반등 캔들 → 진입"의 5단계 패턴. 서희파더(thetrading2021 이재상) 특허 매매기법 기반 구현 (`f_zone.py:1-16`).
- **진입 핵심**: 5단계를 순차 통과하고 0~10점 종합점수가 **≥ 4.0** 이어야 신호 발생. 임펄스·눌림·반등 셋 중 하나라도 미충족이면 즉시 None 반환 (`f_zone.py:599`, `294-309`).
- **청산 핵심**: 라이브 ExitPlan = TP1 +3%(50%) / TP2 +5%(50%), SL -2% 고정, 14:50 강제청산, 본전(breakeven) +1.5% (`f_zone.py:342-370`). 2차 안전망(HoldingEvaluator)은 SL -4%·TP +5%·트레일링·시간강화로 더 너그러움 (`holding_evaluator.py:104-114`).
- **주요 파라미터**: `impulse_min_gain_pct=0.03`, `impulse_max_gain_pct=1.0`(사실상 무제한), `impulse_volume_ratio=2.0`, `pullback_min_pct=-0.03`, `bounce_min_gain_pct=0.005`, `min_atr_pct=0.0`(default 비활성) (`f_zone.py:51-94`).
- **리스크**: 비용 후 검증된 timeframe 은 **일봉뿐**(f_zone 일봉 +3.42%/거래). 운영 기본인 **1분봉은 비용 후 -0.45%/거래로 명확 적자**이며, OOS·랜덤 유니버스 미검증 (변동성 상위 유니버스 선택편향) (`docs/04-report/features/2026-05-29-grid-backtest.md:169,182-184,196`).

## 2. 전략 개요 (컨셉·기원·어떤 셋업을 노리는가)

F존은 **급등(기준봉)이 한 번 나온 종목**을 대상으로, 그 직후 거래량이 줄며 살짝 조정(눌림목)된 뒤 이동평균선에서 지지받고 다시 반등 캔들이 나올 때 진입하는 **추세 추종형 눌림목 매수** 전략이다. 모듈 docstring(`f_zone.py:1-16`)에 따르면 출처는 thetrading2021(서희파더 이재상) 특허 매매기법이며, 분할 익절(+3%, +5%)과 고정 손절(-2%)로 관리한다.

F존과 SF존(슈퍼존)의 관계 (`f_zone.py:9-11`):
- **F존** — 기준봉 + 눌림목 + 이평선 지지 확인
- **SF존** — F존 조건 + 추가 강도(거래량 재증가, 강한 기준봉). 코드상 **F존의 가장 강한 부분집합**으로, `score≥7.0` AND `impulse_gain≥5%` AND `vol_ratio≥3x` 일 때만 SF존으로 격상된다 (`f_zone.py:601-607`). SFZoneStrategy 는 FZoneStrategy 를 내부 delegate 로 보유하고 `signal_type=="sf_zone"` 만 통과시킨다 (`sf_zone.py:36-50`).

엔진 진입점은 `FZoneStrategy._analyze_v2(ctx)` (`f_zone.py:244`). `Strategy.analyze()` 가 AnalysisContext 로 dispatch 한다 (`base.py:42,52,62`). 분석은 OHLCV→pandas DataFrame(오래된→최신) 변환 후 단계별로 수행된다 (`f_zone.py:289-290`, `_to_dataframe` `621-636`).

## 3. 진입 로직 (조건·점수·게이트·필터)

`_analyze_v2` 의 순서 (`f_zone.py:244-338`):

### 사전 게이트
1. **캔들 수**: `len(candles) < min_candles(60)` 이면 None (`f_zone.py:257-259`).
2. **변동성 필터 (F1)**: `min_atr_pct > 0` 일 때만 작동. ATR%(14봉) < 임계면 진입 거부. default `0.0`=비활성 (`f_zone.py:262-269`). ATR% 는 `indicators.atr_pct` 단일 소스 (`f_zone.py:614-618`, `indicators.py:21-51`).
3. **트랩 가드**: `_trap_guard_config().any_enabled()` 일 때만. 과확장·윗꼬리·고갭 ATR화 차단. default 모든 임계 0=비활성(byte-identical) (`f_zone.py:271-277`, `trap_guard.py:47-53,83-84`).
4. **진입 시간 cutoff**: `entry_time_cutoff` 설정 시 마지막 candle 시각이 cutoff 이상이면 거부. default None (`f_zone.py:280-287`).

### 5단계 진입 조건

| 단계 | 메서드(file:line) | 조건 | 미충족 시 |
|---|---|---|---|
| 1. 기준봉 | `_detect_impulse` (`393-437`) | 최근 `impulse_lookback(5)`봉 중 `impulse_min_gain_pct(3%) ≤ (close-open)/open ≤ impulse_max_gain_pct(1.0)` AND `volume ≥ impulse_volume_ratio(2.0)×avg_volume`. 최대 gain 봉 선택 | None (`296-297`) |
| 2. 눌림목 | `_detect_pullback` (`439-476`) | 기준봉 다음~현재 직전 구간 최저가의 하락률이 `pullback_min_pct(-0.03) ≤ pct ≤ pullback_max_pct(-0.005)` AND 구간 평균거래량/기준봉거래량 `≤ pullback_volume_ratio(0.7)` | None (`299-300`) |
| 3. 이평선 지지 | `_check_ma_support` (`478-506`) | 현재봉 low 가 `ma_periods[5,20,60]` 중 하나의 `±ma_support_tolerance(1%)` 이내. **선택적**(미충족해도 진행, 점수만 손실) | 계속 진행 |
| 4. 반등 캔들 | `_detect_bounce` (`508-542`) | 현재(마지막) 봉 `(close-open)/open ≥ bounce_min_gain_pct(0.5%)` AND `current.volume / 눌림평균거래량 ≥ bounce_volume_ratio(1.2)` | None (`308-309`) |
| 5. 점수·분류 | `_score_and_classify` (`544-610`) | impulse·pullback·bounce 모두 True 전제, 합산 score(0~10) ≥ **4.0** → `is_f_zone` | None (`313-314`) |

### 점수 산식 (`_score_and_classify`, `f_zone.py:558-598`)

| 요소 | 만점 | 산식(file:line) |
|---|---:|---|
| 기준봉 gain | 2.0 | `min(impulse_gain / sf_impulse_min_gain_pct(0.05), 1.0)×2.0` (`559`) |
| 기준봉 volume | 1.0 | `min(impulse_vol_ratio / sf_volume_ratio(3.0), 1.0)×1.0` (`560`) |
| 눌림목 | 2.0 | `max(0, 1.0 - |pullback| / 0.05)×2.0` — 얕을수록 ↑ (`567-568`) |
| 이평선 지지 | 2.0 | `2.0 - touch_pct / ma_support_tolerance(0.01)` (미지지 시 0) (`573-576`) |
| 반등 gain | 1.5 | `min(bounce_gain / 0.02, 1.0)×1.5` (`581`) |
| 반등 volume | 1.5 | `min(bounce_vol_ratio / 2.0, 1.0)×1.5` (`582`) |
| 수박지표 가산 | +bonus | `use_watermelon_bonus=True` 시 최근 N봉 내 신호 발생 시 +1.0 (default OFF) (`589-596`) |
| **총합** | **10.0** | `min(score, 10.0)` (`598`) |

- **F존 판정**: `score ≥ 4.0` (`f_zone.py:599`).
- **SF존 격상**: `is_f_zone` AND `impulse_gain ≥ 0.05` AND `impulse_vol_ratio ≥ 3.0` AND `score ≥ 7.0` (`f_zone.py:602-607`).
- 신호의 `signal_type` 은 `"sf_zone"` 또는 `"f_zone"`, score 는 round(score, 2) (`f_zone.py:316,324`).

> ⚠ 변수명 혼동 주의(`f_zone.py:65`): `pullback_min_pct`=눌림 **최대** 하락(-3%, 깊은 한계), `pullback_max_pct`=눌림 **최소** 하락(-0.5%, 얕은 시작점). 부등호 `pullback_max_pct ≥ pullback_pct ≥ pullback_min_pct` (`f_zone.py:463`).

## 4. 청산 로직 (SL/TP/부분익절/트레일링/본전/보유기간 + 특수 청산)

### 4.1 라이브 ExitPlan (1차 방어선, 분봉 close 기반) — `f_zone.py:342-370`

| 항목 | 값 | 인용 |
|---|---|---|
| TP1 | avg×1.03 (+3%), 50% 청산 | `346-350` |
| TP2 | avg×1.05 (+5%), 50% 청산 | `351-356` |
| SL | -2% 고정 (`resolve_sl_pct` 라운드피겨 보정 가능, default OFF) | `365-367` |
| time_exit | 14:50 (KRX STOCK), crypto 는 None | `360` |
| breakeven_trigger | +1.5% | `369` |

- SL 은 `resolve_sl_pct(STRATEGY_ID, avg, -0.02, symbol)` 경유 — RF_STOP_ENABLED env 가 OFF 면 base(-2%) 그대로 (`round_figure.py:201-202`). default OFF/DRY_RUN (`round_figure.py:39-46,145-150`).

### 4.2 적응형 청산 (2차 안전망, HoldingEvaluator, 브로커 pnl_rate 기반) — `holding_evaluator.py`

`STRATEGY_EXIT_PROFILES["f_zone"]` (`holding_evaluator.py:104-114`):

| 항목 | 값 |
|---|---|
| stop_loss_pct | **-4.0%** |
| take_profit_pct | +5.0% |
| partial_tp_pct / ratio | +3.0% / 0.5 (50%) |
| trailing_start_pct / offset | +3.5% / 1.0% |
| breakeven_trigger_pct | +2.5% |
| tightened_sl_pct | -2.5% (보유 `hold_days_tighten`=5일 이상 시) |

평가 우선순위 (`evaluate_holding`, `holding_evaluator.py:263-461`): 보유기간 게이트(f_zone 은 min/max_hold_days 미정의 → 미적용, `104-114`에 키 없음) → distribution 청산(default OFF) → 단기고점 캔들(SHORT_TERM_HIGH, 1분봉·익절구간 도달 시) → 트레일링 → 브레이크이븐 → 분할익절 → 전량익절 → 시간기반 SL → SL → HOLD.

> **SL 격차 의도** (`holding_evaluator.py:86-100`): intraday 단타(f/sf/gold)의 HoldingEvaluator SL(-4%)은 exit_plan() SL(-2%)보다 의도적으로 2%p 너그럽다. ExitEngine(1차) 누락 시(데몬 다운·분봉 fetch 실패·시그널 누락) 2차 fallback 매도 + broker pnl_rate 노이즈 흡수용. 상세: `docs/04-report/features/2026-05-28-sl-gap-decision.md`.

### 4.3 특수 청산
- **SHORT_TERM_HIGH** (`holding_evaluator.py:330-346`): 1분봉 시퀀스 + `rate ≥ partial_tp_pct` 도달 시 도지·윗꼬리·연속음봉 패턴 인식 → 전량 청산. 운영 1분봉 fetch 필수.
- **DISTRIBUTION** (`holding_evaluator.py:317-324`): 세력 이탈(정배열 확장구간 거래량 ×3 장대음봉) → 전량 청산. default OFF (JD-R13, 2026-06-22).
- **백테스트 청산은 별도**: IntradaySimulator 는 전략 exit_plan() 을 무시하고 `_scaled_exit_plan`(f_zone) / `_sfzone_atr_exit_plan`(sf_zone) 사용 (`docs/03-analysis/bar-46-47-f-sf-zone-deep-analysis.md` §3) — 라이브와 백테스트 청산이 다름.

## 5. 파라미터 표 (FZoneParams, `f_zone.py:46-118`)

| 필드 | 기본값 | 의미 | env/override |
|---|---|---|---|
| impulse_min_gain_pct | 0.03 | 기준봉 최소 상승률 3% | params override |
| impulse_max_gain_pct | 1.0 | 기준봉 최대 상승률(무제한) — max 7% 적용 시 winning 시그널까지 죽임 확인(LESSON_FZONE_MAX_GAIN, 2026-05-14) | params override(예 0.07) |
| impulse_volume_ratio | 2.0 | 기준봉 거래량 배율(평균 대비 200%) | params override |
| impulse_lookback | 5 | 기준봉 탐색 과거 봉 수 | params override |
| pullback_min_pct | -0.03 | 눌림 **최대** 하락(was -0.05, Phase D2.4 자본가중 +25%) | params override |
| pullback_max_pct | -0.005 | 눌림 **최소** 하락 -0.5% | params override |
| pullback_volume_ratio | 0.7 | 눌림 거래량 감소비(기준봉 대비) | params override |
| pullback_max_candles | 10 | 눌림 최대 허용 봉 수 | params override |
| ma_periods | [5,20,60] | 이평선 지지 후보 | params override |
| ma_support_tolerance | 0.01 | 이평선 ±1% 접근=지지 | params override |
| bounce_min_gain_pct | 0.005 | 반등 최소 상승 0.5% | params override |
| bounce_volume_ratio | 1.2 | 반등 거래량 증가비(눌림 평균 대비) | params override |
| sf_impulse_min_gain_pct | 0.05 | SF존 기준봉 최소 상승 5% | params override |
| sf_volume_ratio | 3.0 | SF존 거래량 배율 300% | params override |
| min_candles | 60 | MA 계산용 최소 캔들 | params override |
| min_atr_pct | 0.0 | 변동성 필터(0=비활성) | 진입점 명시 override(권장 0.035; 데몬 분봉 0.01) |
| atr_n | 14 | ATR 봉 수 | params override |
| use_watermelon_bonus | False | 수박지표 가산 | params override |
| entry_time_cutoff | None | 진입 시간 차단(예 14:00) | SignalScanner/IntradaySimulator override |
| trap_over_ext_k_atr 외 5종 | 0.0 / "ma" / 20 | 트랩 가드 임계(default 전부 OFF) | env BARRO_TRAP_* (daemon `_apply_trap_env`) |

- 프리셋: `FZoneParams.for_intraday()`(1분봉, `f_zone.py:166-193`) — impulse_min 1.0%, lookback 15, ma[20,60,120], min_candles 120. `for_5min()`(`f_zone.py:132-164`).
- `min_atr_pct` 는 timeframe 의존: 일봉 0.035 / 분봉 0.01 (`grid-backtest.md:19`; daemon `_REVAL_MIN_ATR=0.01` `intraday_buy_daemon.py:625`).

## 6. 활성·운영 상태

- **default 활성**: `_DEFAULT_ENABLED["f_zone"]=True` (`signal_scanner.py:41-44`). 함께 활성: sf_zone, gold_zone, swing_38(BAR-OPS-33).
- **dispatch 우선순위**: intraday 는 SF > F > Gold > Blue > Crypto 순으로 첫 시그널 반환 (`signal_scanner.py:148,156-165`). 최종 정렬은 점수 1차(내림차순), `STRATEGY_PRIORITY` 2차 tiebreaker — **f_zone=3** (swing_38=1, gold_zone=2, f_zone=3, sf_zone=4) (`signal_scanner.py:59-62,137`).
- **토글**: `SignalScanner(..., enabled_strategies={"f_zone": False})` (`signal_scanner.py:91,99-102`).
- **포지션 사이징**: `even_position_size` 균등 8% (`f_zone.py:372-379`, `position_sizing.py:23`) — max_total 0.80 / 10슬롯. score 차등은 진입 게이트에서만 사용(5/22 비중 6배 편차 → 균등 전환).
- **데몬 배선** (`scripts/intraday_buy_daemon.py`):
  - `DEFAULT_ZONE_STRATEGIES=["swing_38","f_zone","sf_zone","gold_zone"]` (`L85`), BUY_START 09:05 게이트 (`L60`).
  - **gap-guard**: `_GAP_GUARD_STRATEGIES` 기본 `{gold_zone, f_zone}` — 시초갭(flu_rate) ≥ `_ZONE_MAX_FLU`(15%) 시 진입 금지 (`L658-659,1005`, env `BARRO_GAP_GUARD_STRATEGIES`).
  - **진입 cutoff**: `_ZONE_ENTRY_CUTOFF`="14:30" (swing_38 면제) — 6/11 f_zone 14:33/14:38 현대무벡스 진입 이월 사례 봉합 (`L661-666`).
  - **DCA(물타기)**: f_zone 은 `_NO_DCA_STRATEGIES`(swing_38, supertrend)·`_MEANREV_STRATEGIES`(gold_zone)에 미포함 → DCA 대상 (`L627,634`).
  - **재검증(reval)**: `_build_reval_strategy("f_zone")` 는 `for_intraday()` + `min_atr_pct=0.01` + 트랩env 주입 (`L738-747`).
  - **EOD 강제청산 면제 아님**: f_zone 은 `_FORCE_CLOSE_EXEMPT_STRATEGIES`(swing_38)에 미포함 → carry-limit 트림 대상 (`L668-685`).

## 7. 비용·손익분기 관점

거래비용 (`trading_costs.py:29-33`): 편도 수수료 `COMMISSION_RATE=0.0035`(0.35%), 매도세 `TAX_RATE_SELL=0.0020`(0.20%), **왕복 `ROUND_TRIP_COST_RATE = 0.0035×2 + 0.0020 = 0.0090`(0.90%)**. env override `BARRO_COMMISSION_RATE`/`BARRO_TAX_RATE_SELL`.

- f_zone TP1(+3%)에서 50% 익절하면 그 절반은 왕복 0.90% 차감 후 순익 약 **+2.1%**, TP2(+5%)는 약 **+4.1%**. SL(-2%)은 비용 포함 약 **-2.9%**. 즉 TP/SL 폭은 왕복 0.90% 대비 충분히 크다(TP1 손익분기 0.90% 초과).
- 그러나 **1분봉 단타는 거래 빈도가 비용을 잠식한다**: 비용 후 f_zone 1m -0.45%/거래, 5m -0.04%/거래(거의 손익분기), **일봉만 +3.42%/거래로 견고** (`grid-backtest.md:167-169`).
- ⚠ **caveat**: 위 그리드 백테스트는 **구(舊) 비용 가정**(편도 0.015%×2 + 세 0.18% ≈ 왕복 0.21%, `grid-backtest.md:162`)으로 계산됐다. 현재 `ROUND_TRIP_COST_RATE`=0.90%(약 4배)를 반영하면 **단타 적자는 더 심화**된다. 1분봉 운영의 비용 후 손익은 코드 기준 현 비용으로 재검증 필요(미수행 — 미검증).

## 8. 백테스트·OOS 근거 / 한계·리스크

근거: `docs/04-report/features/2026-05-29-grid-backtest.md` (51종목 실데이터, 3전략×3tf×3min_atr=27셀, 멀티에이전트 적대적 검증 confidence HIGH, 3분할 TP+트레일링+비용 반영).

| 전략 | tf | min_atr | gross% | 비용후(ct)% | win% | 거래 |
|---|---|---:|---:|---:|---:|---:|
| f_zone | **일봉** | 0.035 | +3.57 | **+3.42** | 52.8 | 72 |
| f_zone | 5m | 0.0 | +0.16 | **-0.04** | 39.6 | 313 |
| f_zone | 1m | 0.0 | -0.23 | **-0.45** | 31.3 | 482 |

(`grid-backtest.md:167-169`)

**최종 결론** (`grid-backtest.md:181-185`):
1. 🟢 비용 후 견고한 양수는 **일봉(스윙)만** — f +3.42% / sf +3.76%.
2. 🟠 5분봉 엣지는 비용 후 거의 소멸(f 5m 적자 전환).
3. 🔴 1분봉은 비용 후 전 전략 명확 적자.
4. 구조적: 이 시스템의 backtest 엣지는 단타가 아니라 일봉에 있다 — **단타 전용 모드(현 default)가 엣지 가장 약한 영역을 택한 셈**.

**한계·리스크**:
- **운영 default 가 1분봉 단타** (`signal_scanner.py:87`, daemon)인데 1분봉은 비용 후 적자. F존의 검증된 흑자는 일봉이며, 현재 일봉 트랙(swing_38)은 별개 전략.
- **OOS 미검증**: 유니버스가 변동성 상위 선정 = 최대 낙관 편향(트레일링이 큰 추세 회수). in-sample·모의 API·단일 기간. out-of-sample + 랜덤 유니버스 검증 선행 필수 (`grid-backtest.md:193,196-197`).
- **변동성 필터 default OFF**: 저변동·고가주(LG전자 ATR% 2.94%, win 0%, -627k)에서 SL 노이즈 발동 위험 — 운영은 진입점에서 명시 override 필요 (`f_zone.py:86-94`).
- **min_atr 단일상수 금지**: 0.035 는 분봉서 신호 거의 전멸(f 1m+0.035 → 표본 1건 노이즈) (`grid-backtest.md:18,36,64`).
- **비용 재검증 부재**: 현 왕복 0.90%(구 가정 대비 약 4배)로 그리드 백테스트 미재실행 (문서 부재 — 미검증).

## 9. 관련 파일·테스트

- `backend/core/strategy/f_zone.py` — FZoneParams + FZoneStrategy (636줄, 메인)
- `backend/core/strategy/sf_zone.py` — SFZoneStrategy (delegate)
- `backend/core/strategy/trap_guard.py` — 진입 트랩 가드(default OFF)
- `backend/core/strategy/round_figure.py` — resolve_sl_pct(라운드피겨 SL 보정, default OFF)
- `backend/core/strategy/position_sizing.py` — even_position_size(균등 8%)
- `backend/core/strategy/indicators.py` — atr_pct(변동성 필터)
- `backend/core/risk/holding_evaluator.py` — STRATEGY_EXIT_PROFILES["f_zone"] 적응형 청산
- `backend/core/scanner/signal_scanner.py` — dispatch·_DEFAULT_ENABLED·STRATEGY_PRIORITY
- `scripts/intraday_buy_daemon.py` — gap-guard/cutoff/DCA/reval/trap env 배선
- 테스트: `backend/tests/strategy/test_f_zone.py`(v2·exit_plan·position_size·F1 변동성·entry_time_gate·Phase D2.4 pullback·baseline 회귀), `test_sf_zone.py`, `test_trap_guard.py`
- 분석/리포트: `docs/03-analysis/bar-46-47-f-sf-zone-deep-analysis.md`, `docs/04-report/bar-46-f-zone-v2.report.md`, `docs/04-report/features/2026-05-29-grid-backtest.md`, `docs/04-report/features/2026-05-28-daytrading-strategies-analysis.md`, `docs/04-report/features/2026-05-28-sl-gap-decision.md`

---
*진실원천: 본 리포트의 모든 수치는 위 인용 코드(file:line) 기준. 미검증 항목은 명시.*
