# BarroAiTrade 매매전략 심층 리포트 — swing_38 (38스윙)

> 생성: 2026-06-22 · 진실원천: 코드 인용(file:line) · origin/main 기준
> 상태: 🟢 활성 (default ON, BAR-OPS-33) · 분류: 일봉 멀티데이 스윙(3~20일) · 컨셉: 임펄스(급등) 후 Fib 0.382 되돌림을 받아 반등을 확인하고 진입하는 추세 눌림목 스윙

## 1. 요약 (TL;DR)
- **유일한 일봉/멀티데이 보유 활성 전략.** 1분봉 단타 3종(sf/f/gold)과 달리 일봉만 받아 3~20일 보유한다 (`require_daily_candles=True`, swing_38.py:78 / signal_scanner.py:11).
- **진입 = 3단계 순차 AND**: ① 임펄스(`gain≥5% + 거래량 2x` 양봉, swing_38.py:208-231) → ② Fib 0.382 되돌림(±7.5%, swing_38.py:233-244) → ③ 직전 양봉 반등(swing_38.py:246-254). 가중점수 `impulse*0.4+fib*0.4+bounce*0.2 ×10`이 `min_score=3.0` 미만이면 차단(swing_38.py:180-183).
- **청산 = 비대칭 큰 승자 프로파일**: SL −15% / TP1 +20%(50%) / TP2 +50%(50%) / 트레일링 peak−5% / 본전 +10% / min_hold 3일·max_hold 20일 (swing_38.py:285-304, holding_evaluator.py:135-152). Phase D2(2026-05-28) 그리드 서치(S6 SL×max_hold 2D + S7 진입필터) 결과 자본가중 **+1.808%** (baseline Phase D −10%/8일 +0.597% 대비 +203%).
- **운영 특수처리 3종**: 14:30 진입컷오프 면제, EOD 강제청산(이월한도 트림) 면제, 데몬 DCA(물타기) 제외 — 모두 "다일보유가 설계"이기 때문(intraday_buy_daemon.py:666/672/634).
- **OOS 5/5 seed PASS** (2026-06-22 검증): avg_ret 평균 +2.562%, holdout(최근 40%) 평균 +3.371%(full 상회 → 과최적화 없음). 단 불장 편향 한계 — 진정한 약세장 OOS는 아님.

## 2. 전략 개요 (38 의미·일봉 셋업)
"38"은 **피보나치 되돌림 0.382(=38.2%)**를 뜻한다. 큰 임펄스(급등 양봉) 이후 가격이 그 폭의 약 38.2%를 되돌렸을 때(즉 추세는 살아있으나 일시 눌림인 지점) 반등을 확인하고 매수한다. 모듈 docstring(swing_38.py:1-13):

> "38스윙 전략 (Swing-38 Strategy) — 임펄스 후 Fib 0.382 되돌림 매수. … BAR-49: 신규 포팅. F존 (-2~-5% 눌림) 보다 깊은 되돌림 (~-30%) 노리는 스윙 매매."

F존(단타)이 −2~−5% 얕은 눌림을 노린다면 swing_38은 **임펄스 고점 대비 ~−30%대의 깊은 되돌림**을 노리고 수일에 걸쳐 회복을 기다린다. 따라서 입력 데이터부터 일봉으로 강제(분봉/5분봉 노이즈 제거)되고, 청산도 일(day) 단위 게이트로 작동한다.

`STRATEGY_ID = "swing_38_v1"` (swing_38.py:123), 신호 `signal_type="swing_38"` / 2차분할 subtype `"swing_38_add"`.

## 3. 진입 로직 (조건·점수·게이트) — 코드 인용
`_analyze_v2(ctx)` (swing_38.py:128-204) 순서:

**선행 게이트 (순차 거부)**
1. **최소 캔들 수**: `len(candles) < min_candles(60)` → None (swing_38.py:130).
2. **일봉 강제** (`require_daily_candles=True`): 마지막 두 캔들 timestamp 간격 `< 12h` 이면 분봉/시간봉으로 판단해 거부 (swing_38.py:134-139).
   ```python
   interval_hours = (ts2 - ts1).total_seconds() / 3600
   if interval_hours < 12:  # 12h 미만 = 분봉/5분봉/시간봉 → 거부
       return None
   ```
3. **변동성 필터** (Phase 6 / D2): `ATR% < min_atr_pct(0.03)` 이면 거부 (swing_38.py:142-145, atr_n=14). default가 `0.0→0.03`으로 활성(D2, 2026-05-28). S7 시뮬에서 ATR≥3% 단독 추가가 자본가중 +2.71% 우위, 진입 수 6,397→6,095(−4.7%, 저변동주만 차단) (swing_38.py:51-57).
4. **트랩 가드** (6월 가짜상승 방어): default-OFF (모든 임계 0). 활성 시 과확장·윗꼬리·시초갭 차단 (swing_38.py:99-117, 147-153).
5. **진입 시간 게이트** (Phase 8c): `entry_time_cutoff` default None(비활성). 시뮬은 14:00 override. 일봉은 `.time()=00:00`이라 항상 통과 (swing_38.py:66-70, 154-159).

**3단계 시그널 (모두 AND)**
- **① 임펄스** `_detect_impulse` (swing_38.py:208-231): 최근 `impulse_lookback(30)`봉 역순 탐색 — 양봉(`close>open`) + `gain≥impulse_min_gain_pct(5%)` + `volume≥impulse_volume_ratio(2.0)×평균거래량`. 없으면 None.
- **② Fib 0.382** `_fib_score` (swing_38.py:233-244): `retrace = (high−close)/(high−low)`, `|retrace − 0.382| ≤ fib_tolerance(0.075)` 이어야 score>0. 점수 = `1 − distance/tolerance` ∈ [0,1].
- **③ 반등** `_bounce_score` (swing_38.py:246-254): 직전봉 양봉이어야 하고, body% / 0.02로 정규화(+2% 양봉=1.0).

**점수 합성·임계** (swing_38.py:178-183):
```python
impulse_score = min(1.0, impulse["gain_pct"] / 0.10)  # 5%~10% 정규화
raw = impulse_score * 0.4 + fib_score * 0.4 + bounce_score * 0.2
score = raw * 10.0          # BAR-175 0-10 스케일
if score < p.min_score:     # default 3.0 (=기존 0.3×10), 시뮬 진입점 5.0 override
    return None
```
약한 swing 시그널(예: 5/22 LG전자 −148k / 삼성전기 −124k의 w=0.3 BEARISH) 진입을 min_score로 차단(swing_38.py:59-64).

**일봉 dispatch 경로** (signal_scanner.py:184-199): intraday 전략 미시그널 시 fallback으로 일봉을 별도 fetch해 `swing_38.analyze`를 호출. swing_38 비활성 시 1d fetch 자체를 skip(cost 절약). `STRATEGY_PRIORITY["swing_38"]=1`(최우선) — 점수 동률 시 정렬 tiebreaker로 최우선 슬롯 확보(signal_scanner.py:59-62, 137).

**2차 분할 진입** `add_on_signal` (swing_38.py:306-398, Phase D): "일별 매수 1번, 다음날 추적 후 기준봉 지지하면 2차 매수" 요구 반영. 조건 AND — `second_entry_enabled`, 일봉 간격 검증, `second_entry_min_days(1) ≤ 경과일 ≤ second_entry_max_days(5)`, 현재가 `≥ 기준봉low × (1 − second_entry_support_tolerance(0.005))`. 충족 시 `entry_round=2`, `size_ratio=0.5` metadata와 함께 신호 반환(수량은 운영이 round1×0.5로 적용).

**포지션 사이징** `position_size` (swing_38.py:400-405): Phase 9 — `even_position_size`(균등 비율 0.08). score 차등 무력화(5/22 비중 편차 제거).

## 4. 청산 로직
청산은 두 경로가 동일 임계로 동기화된다 — ① `Strategy.exit_plan()`(ExitEngine, 분봉 close 기반 1차 방어선) ② `STRATEGY_EXIT_PROFILES["swing_38"]`(HoldingEvaluator, 브로커 pnl_rate 기반 2차 안전망). intraday 단타는 두 경로 SL을 의도적으로 2~2.5%p 벌려두지만(holding_evaluator.py:86-103), **swing_38은 ExitEngine SL=−15% = profile SL=−15%로 동일**(의도된 격차는 intraday에만 적용, holding_evaluator.py:102-103).

`exit_plan()` (swing_38.py:285-304):
- **TP1 = avg × 1.20 (+20%), qty 50%** "38스윙 TP1 +20%"
- **TP2 = avg × 1.50 (+50%), qty 50%** "38스윙 TP2 +50%"
- **SL = −15%** (`resolve_sl_pct(STRATEGY_ID, avg, Decimal("-0.15"), symbol=...)` — 라운드피겨 보정 wrapper 경유)
- **breakeven_trigger = +10%**
- **min_hold_days = 3, max_hold_days = 20** (time_exit는 제거)

`STRATEGY_EXIT_PROFILES["swing_38"]` (holding_evaluator.py:135-152):
| 항목 | 값 | 출처 line |
|---|---|---|
| stop_loss_pct | −15.0 | :139 (Phase D −10 → −15) |
| take_profit_pct | +50.0 | :140 |
| partial_tp_pct / ratio | +20.0 / 0.5 | :141-142 |
| trailing_start_pct | +20.0 | :143 (TP1 발동 후 trail 가동) |
| trailing_offset_pct | 5.0 | :144 (peak −5% 시 청산) |
| breakeven_trigger_pct | +10.0 | :145 |
| tightened_sl_pct | −15.0 | :148 (SL과 동일 → hold_days_tighten=5에서 강화 미발동, 시뮬 단일단계 동기화) |
| min_hold_days / max_hold_days | 3 / 20 | :150-151 |

**HoldingEvaluator 평가 순서** (holding_evaluator.py:293-461): max_hold_days 도달 시 손익무관 최우선 강제매도(TIME_TIGHTENED_SL, :296-303) → min_hold_days 미달 시 모든 청산평가 차단(HOLD, :304-311, "단기 노이즈 SL/TP 발동 방지") → (distribution default-OFF) → 단기고점 → 트레일링 → 본전 → 분할익절 → 전량익절 → 시간기반 SL → SL → HOLD.

**Phase D2 최적화 맥락** (swing_38.py:271-282 docstring, holding_evaluator.py:136-138):
> "S6 SL×max_hold 2D + S7 진입 필터 그리드 결과: … SL=−15% × D+20 = 자본가중 +1.808% (baseline SL=−10%×D+8 +0.597% 대비 +203%)." min_atr_pct=0.03 활성으로 추가 +2.71%.

`resolve_policy`가 `strategy_id`에서 `_v1` 제거 후 매칭(holding_evaluator.py:173-195) — `swing_38_v1 → swing_38`.

## 5. 파라미터 표
`Swing38Params` (swing_38.py:39-106). 모든 값 코드 default 인용.

| 파라미터 | default | 의미 | line |
|---|---|---|---|
| impulse_lookback | 30 | 임펄스 탐색 봉 수 | :43 |
| impulse_min_gain_pct | 0.05 | 임펄스 최소 양봉 폭 5% | :44 |
| impulse_volume_ratio | 2.0 | 임펄스 거래량 ≥ 평균×2 | :45 |
| fib_target | 0.382 | 목표 되돌림 비율 | :46 |
| fib_tolerance | 0.075 | 0.382 ±7.5% zone | :47 |
| bounce_lookback | 5 | 반등 평가 봉 수 | :48 |
| min_candles | 60 | 분석 최소 캔들 | :49 |
| min_atr_pct | 0.03 | 변동성 필터(D2 활성) | :56 |
| atr_n | 14 | ATR 기간 | :57 |
| min_score | 3.0 | 진입 점수 임계(시뮬 5.0) | :64 |
| entry_time_cutoff | None | 진입 시간 게이트(비활성) | :70 |
| require_daily_candles | True | 일봉 강제 | :78 |
| min_hold_days | 3 | 최소 보유일 | :79 |
| max_hold_days | 20 | 최대 보유일(D2: 8→20) | :83 |
| second_entry_enabled | True | 2차 분할 진입 | :93 |
| second_entry_min_days | 1 | 2차 진입 최소 경과일 | :94 |
| second_entry_max_days | 5 | 2차 진입 시한 | :95 |
| second_entry_size_ratio | 0.5 | 2차 수량 비율 | :96 |
| second_entry_support_tolerance | 0.005 | 기준봉 지지 허용 | :97 |
| trap_* (6종) | 0.0 / "ma" / 20 | 트랩가드(default-OFF) | :101-106 |

청산 파라미터(exit_plan / profile): SL −15% · TP1 +20%(50%) · TP2 +50%(50%) · trailing 20/5 · breakeven +10% · min/max hold 3/20.

## 6. 활성·운영 상태 (★일봉 전용 특수처리)
- **활성 플래그**: `_DEFAULT_ENABLED["swing_38"]=True` (signal_scanner.py:50, BAR-OPS-33 2026-06-08). 데몬도 `DEFAULT_ZONE_STRATEGIES = ["swing_38", "f_zone", "sf_zone", "gold_zone"]`(intraday_buy_daemon.py:85), CLI default `--strategies "swing_38,f_zone,sf_zone,gold_zone"`(intraday_buy_daemon.py:1728).
- **우선순위 최상위**: `STRATEGY_PRIORITY["swing_38"]=1`(signal_scanner.py:60). 슬롯/자본 경합 시 점수 동률이면 최우선(:57-58, :137).

**★ 일봉 전용 특수처리 3종** (intraday_buy_daemon.py):
1. **진입 컷오프 면제** `_CUTOFF_EXEMPT_STRATEGIES = {"swing_38"}` (:666). 일반 전략은 14:30(`BARRO_ZONE_ENTRY_CUTOFF`, :665) 이후 진입 차단되나, **다일보유가 설계인 swing_38은 면제** — "이월이 의도(이월 총액 한도 20%가 별도 캡)"(:664). BAR-OPS-39 P0에서 컷오프 경과 + swing_38 미운영이면 스캔 자체 생략(:845).
2. **EOD 강제청산(이월한도 트림) 면제** `_FORCE_CLOSE_EXEMPT_STRATEGIES = {"swing_38"}` (:672). `_force_close_skip()`(:675-685)이 종베(수동관리)와 swing_38을 'EOD 강제 트림'에서 제외. 단 **장중 보유평가의 자체 손절·시간청산(min/max hold)은 그대로 적용** — 면제는 carry-limit 트림에만 한정(:670-671, :1406).
3. **데몬 DCA(물타기) 제외** `_NO_DCA_STRATEGIES = {"swing_38", "supertrend"}` (:634). swing_38은 자체 2차분할(`add_on_signal`)을 쓰므로 데몬 tranche DCA와 겹치면 이중분할 → 데몬 DCA 무조건 스킵(:449, :628-633).

**전 전략 공통 적용**: 데몬 후처리 트랩필터는 일봉 선정 단계에서 swing_38 포함 enforcement — reval(5분봉)이 swing_38 미지원이라 일봉 단계 보완(:727-728, :1014).

## 7. 비용·손익분기 관점 (멀티데이 — 단타와 비용감도 차이)
공통 비용(`trading_costs.py`): 매수 수수료 편도 **0.35%**(COMMISSION_RATE 0.0035, :29/:36 — 실측 0.3497%/leg), 매도 거래세 **0.20%**(TAX_RATE_SELL 0.0020, :31/:37), **왕복 ≈ 0.90%**(매수 0.35 + 매도 0.35 + 세금 0.20). docstring(:10,:14,:27): 종전 0.00175(절반)는 2배 과소 오류로 0.0035 정정.

**단타 대비 비용 감도가 낮다.** swing_38은 1회 진입으로 수일~수주 보유하므로 왕복비용 0.90%가 한 번만 발생한다. 반면 1분봉 단타(f/sf/gold)는 같은 기간 다회전 → 0.90%가 누적되어 net 침식이 크다. 또한 swing_38은 **비대칭 큰 승자**(TP +20/+50% vs SL −15%) 구조라 한 트레이드의 그로스 폭이 0.90%를 압도 — OOS avg_ret +2.36~+2.78%/라운드트립이 이미 브로커 실측비용 차감 후 net 양수(OOS 리포트 §2, swing38-oos:15,:23). 승률 43%<50%라도 손익비가 커서 기대값 양수(OOS §3:32). 따라서 비용은 단타보다 약한 제약이며, 큰 손실 흡수(SL −15%, max_hold 20)로 회복시간을 확보하는 설계가 비용 회수의 핵심이다.

## 8. 백테스트·OOS 근거 / 한계·리스크
**활성 근거 (백테스트 안정성 1위)** — signal_scanner.py:47-49 / intraday_buy_daemon.py:82-83:
> "BAR-OPS-33 (2026-06-08): swing_38 활성화 — 4~6월 백테스트 안정성 1위 (승률55%·손익비7.36·기대값+5.34·MDD−7.6)."

**OOS 검증 PASS** — docs/04-report/features/2026-06-22-swing38-oos-validation.report.md:
- 기존 OOS 관문(`_oos_validation.py`) 재사용, `STRATEGIES=["swing_38"]` override, 실제 `Swing38Strategy`(require_daily_candles=True·max_hold_days=20) + 브로커 실측비용 + 실일봉(swing38-oos:14-16).
- **5/5 seed PASS** (swing38-oos:18-28): active 106~114·trades 649~789·승률 42.7~44.4%·avg_ret +2.36~+2.78%·holdout +2.93~+3.75%. **전체 avg_ret 평균 +2.562%, holdout 평균 +3.371%** (:28).
- 해석(:30-33): holdout(최근 40%)이 full보다 높음 → 시간 OOS 열화 없음(과최적화 아님). drop1 부호 안정 → outlier 의존 아님. 기존 튜닝(SL −15% × max_hold 20, S6 그리드 자본가중 +1.808%)이 OOS·실측비용에서도 유지.

**한계·리스크** (swing38-oos:35-38):
- **불장 편향**: holdout ≈ 2025~2026 강세 구간 → 진정한 약세장 OOS 아님. holdout>full은 안심 신호이나 베어 검증은 미수행. 약세장 데이터 확보 시 재확인 권장(:41).
- **랜덤 유니버스 vs 라이브**: OOS는 임의 n=120 유니버스, 라이브는 거래대금 주도주(`kiwoom_native_rank`) → 본 검증은 엣지의 *범용성* 확인이지 라이브 유니버스 재현 아님.
- 슬리피지·동시보유 한도·자본곡선 MDD는 OOS 평균기준 밖.
- **구조적 리스크**: 깊은 되돌림(~−30%) 매수 + SL −15% + max_hold 20일 → 추세가 무너진 종목을 장기 보유할 손실 꼬리. min_hold 3일이 단기 노이즈 청산을 막는 대신 초기 급락 손실을 그대로 흡수.
- **승률 43%**: 비대칭 큰 승자에 의존 → 큰 승자 미실현(TP 미도달) 구간이 길면 자본효율 저하.

## 9. 관련 파일·테스트
- 메인 전략: `backend/core/strategy/swing_38.py` (424줄, Swing38Params + Swing38Strategy)
- 청산 프로파일: `backend/core/risk/holding_evaluator.py:135-152` (STRATEGY_EXIT_PROFILES["swing_38"]) + 평가 로직 :263-461
- 스캐너 활성/dispatch: `backend/core/scanner/signal_scanner.py:41-62`(활성 flag·priority), :184-199(일봉 dispatch)
- 데몬 운영 특수처리: `scripts/intraday_buy_daemon.py` — :634(NO_DCA), :666(CUTOFF_EXEMPT), :672(FORCE_CLOSE_EXEMPT), :85/:1728(default 전략집합)
- 비용 상수: `backend/core/trading_costs.py:29-37` (COMMISSION 0.35% / TAX 0.20%)
- 라운드피겨 SL: `backend/core/strategy/round_figure.py` (resolve_sl_pct)
- OOS 리포트: `docs/04-report/features/2026-06-22-swing38-oos-validation.report.md`
- 테스트: `backend/tests/strategy/test_swing_38.py` (36 테스트 — C1~C8 상속/min_candles/3단계 시너리오/ExitPlan/PositionSize/HealthCheck/Baseline, + ATR 필터·min_score·entry_time_cutoff·일봉강제·Phase D exit·add_on_signal 2차분할 케이스)

---
*진실원천 주석*: 본 리포트의 모든 수치는 origin/main 기준 코드/문서를 직접 인용(file:line)했다. 진입 3단계·점수합성·게이트는 `swing_38.py:128-254`, 청산 임계는 `swing_38.py:285-304` 및 `holding_evaluator.py:135-152`(두 경로 동일 동기화)에서 확인. 운영 특수처리(컷오프 면제·강제청산 면제·DCA 제외)는 `intraday_buy_daemon.py:634/666/672`. 백테스트 4~6월 수치(승률55%·손익비7.36·기대값+5.34·MDD−7.6)는 `signal_scanner.py:47-49` 주석 인용이며 원본 백테스트 산출 로그는 본 리포트 범위 밖(미재현). OOS 수치는 2026-06-22 검증 리포트 인용. 추측 없이 코드 default·문서 기재값만 반영했다.
