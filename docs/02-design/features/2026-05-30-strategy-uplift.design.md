# 매매전략 고도화 설계 — 매매복기/백테스트 도출 문제 종합

**작성일**: 2026-05-30
**근거**: 2026-05-28·29 매매복기 + 대규모 격자 백테스트(`2026-05-29-grid-backtest.md`)
**방법**: 7-에이전트 분석(P1~P7 클러스터) → 적대적 검증(confidence HIGH) → 우선순위 합성
**원칙**: 실거래 머니 시스템 — 변경은 근거·리스크·검증 게이트 엄수. 필터는 **완화 아닌 강화**.

---

## 0. 근본 원인 — 2줄기로 수렴

| # | 근본 원인 | 핵심 증거 |
|---|---|---|
| **A. 진입 정합성 붕괴** | 데몬 진입 경로(`scripts/intraday_buy_daemon.py`)에 전략 `analyze()` 호출이 **0건**(grep 확인). 종목·전략 선정은 **일봉 sim**(`fetch_daily`→`IntradaySimulator.run`, 503-509)으로만, 장중 진입은 P10 시초폭등·P4 고점근접이라는 **가격 휴리스틱**만 통과하면 시장가 매수. → "바닥 매수" gold존이 일봉 과거신호만으로 **장중 고점 진입** | 5/29 018880 시뮬 gold +124k(최선 선정) → 실제 **-13.9만**(5770 고점매수→DCA→5400 손절 -6%) |
| **B. 평가 인프라 단절** | 운영 데몬은 `order_audit.csv`에만 기록(434행 전량 `MKT`), `barro_trade.db` trades 테이블은 **0행**. 모니터는 trades 테이블을 1순위 소스로 + 매칭 시 `price∉{None,MKT}` 요구 → 전략별 승률/손익 KPI가 **구조적으로 항상 null**. DCA buy는 `strategy_id` 미전파로 귀속 흩어짐 | gold 0승2패 같은 손실주범 **자동감지 불가**. `daytrading_daily_monitor` §6.3 알람 구조적 미발화 |

---

## 1. 문제 클러스터 (P1~P7)

| ID | 문제 | severity | 코드 앵커 |
|---|---|---|---|
| **P1-1** | 진입 시 전략 `analyze()` 재검증 부재 (선정-진입 괴리 근본) | **critical** | `intraday_buy_daemon.py:496-573` |
| P1-2 | `momentum_active` 예외가 고점근접 가드 면제 → 바닥전략(gold) 고점 진입 허용 | high | `:552-573` |
| P1-3 | gold `_analyze_v2` 신호가 일봉 마지막 close 고정 — 진입 시점 신선도 미검증 | high | `gold_zone.py:79-132` |
| P1-4 | 일봉 weighted_pnl best 선정이 분봉 실현과 괴리 (018880) | medium | `:509-523` |
| **P2-1** | 선정=일봉, 진입·청산=1분봉 timeframe 구조적 괴리 | **critical** | `:503-516` / `:187` |
| P2-2~5 | 진입 재검증 부재 / 오프라인 평가 일봉청산 / 청산모델 삼중 불일치 / sim≠reality 미측정 | high | 다수 |
| P3-1 | 1분봉 전 전략 적자 — 노이즈·비용 대비 엣지 부재 + 고정폭 TP/SL 부정합 | high | `indicators.py` / `intraday_simulator.py:181·190·200` |
| **P3-2** | `min_atr_pct=0.035` 단일 하드코딩 — 일봉전용, 분봉 신호 전멸. `for_5m/for_intraday` preset은 **dead code(caller 0)** | high | `intraday_simulator.py:181~213` |
| P3-3 | 진입(일봉)·청산(1분봉) timeframe 불일치 + 재검증 없음 | critical | `:503-509,531-573` |
| P4-2 | 일봉 선정 sim 진입필터가 장중 실매수에 게이트로 미작동 | high | `intraday_simulator.py:195-216` |
| **P5-1** | 2분할 DCA가 전략강도·국면 무관 무조건 발동 → 약전략/하락장 물타기 손실 확대 | high | `active_positions.py:109-125` / `:235-247` |
| P5-2 | 청산모델 삼중 불일치 (gold exit_plan +2/+4 vs holding_evaluator TP4 vs simulator +3/+5/+7) | medium | 3파일 |
| **P6-1** | 영문 포함 종목코드(0193W0) `isdigit()` 가정으로 주문 직전 ValueError | medium | `kiwoom_native_orders.py:173,104` |
| **P6-2** | DCA buy `strategy_id` 미전파 → 전략별 실현손익 귀속 왜곡 | high | `intraday_buy_daemon.py:249` |
| **P6-3** | 모니터 주소스(trades 테이블) 0행 + MKT가격 → 전략 KPI 항상 null, 알람 미발화 | high | `daytrading_daily_monitor.py:81` |
| P7-1 | 흑자결론이 변동성 선택편향·in-sample·모의데이터 의존 — OOS 검증 게이트 부재 | medium | `grid-backtest.md:193` |
| P7-2 | sim≠reality 괴리를 측정하는 지표 부재 | high | `:509` |

---

## 2. 고도화 설계 — 우선순위 (근거강도 × 기대효과 × 1/리스크)

| 순위 | 변경 | 유형 | 신뢰 | HITL | 검증법 |
|---:|---|---|---|---|---|
| **1** | **평가 인프라 복원** — trades 테이블 의존 제거, `active_positions.filled_price`+매도 audit로 전략별 승률/손익 보조집계, §6.3 알람 연결 | infra | high | ✕(1차) | 5/29 데이터로 gold 0/2·f 2/4 재현 |
| **2** | **진입 정합성 가시화** — 매수 시 sim기준가·BB/RSI충족·일중위치 스냅샷 + sim-live 괴리(pnl_diff) 리포트 | eval | med | ✕ | 5/29 gold 2건 '적정대역 밖' 플래그 |
| **3** | **DCA strategy_id 전파** — `place_buy(...,strategy_id=pos.strategy)` (메타만, 동작불변) | logic | high | ✕ | DCA audit행에 strategy_id 채워짐 |
| **4** | **종목코드 정규식 교정** — `isdigit()`→`^[0-9A-Z]{6}$`, 공용 헬퍼, FAILED→BLOCKED 일관화 | logic | high | △ | 단위테스트 + 모의서버 0193W0 1건 |
| **5** | **선정 sim 비용 일치** — `IntradaySimulator(commission_pct=0.015, tax_pct_on_sell=0.18)` | param | high | ✓ | 5/29 후보 선정 diff, dry_run |
| **6** | **진입 analyze() 재검증 게이트** ★핵심 — 진입 직전 best_strategy를 분봉 컨텍스트로 재호출, None이면 skip. **분봉용 min_atr 0.01 별도셋 선결** | logic | high | ✓ | 분봉0.01로 5/29 재현: gold skip, f/sf 유지. shadow 1~2주 |
| **7** | **momentum 예외 전략별 분기** — gold(바닥형)는 고점근접 무조건 차단 | logic | high | ✓ | 018880 차단·f/sf 유지 회귀 |
| **8** | **DCA 국면·전략 게이트** — BEARISH/약전략(gold)는 T2 보류, `_build_tranches`에 strategy 전달 | logic | med | ✓ | 3분할 백테스트로 하락장 손실폭 축소 |
| **9** | **청산모델 단일화** — ExitEngine/HoldingEvaluator/simulator 정합. 우선 0-test 통합테스트 추가(저위험), 정합 결정은 HITL | eval | med | ✓ | 동일 OHLC 3모델 비교표 |
| **10** | **정책 + OOS 검증 관문** — gold 인트라데이 비중축소/스윙 분리, 5m 격리 채널(소액), 파라미터 변경 전 랜덤·비변동성 유니버스+train/holdout+비용+부호안정 의무화 | process | med | ✓ | 관문 스크립트가 미달 셀 차단 |

### 로드맵
- **Phase 0 (즉시·무위험, 동작 미변경)**: 1, 2, 3 — "손실차단의 눈"을 먼저 켠다.
- **Phase 1 (저위험, dry-run/test 후 1~2주)**: 4, 5, 9(테스트만).
- **Phase 2 (실거래 hot-path, shadow/HITL 후 2~4주)**: 6, 7, 8.
- **Phase 3 (정책·OOS, 사용자 승인 후)**: 9(정합), 10. **자본증액은 OOS 관문 통과 후.**
  - ✅ **#10 OOS 검증 관문 구현·실행** (`scripts/_oos_validation.py`, 결과 `2026-05-30-oos-validation.md`): 랜덤 유니버스+3분할+실비용+holdout+drop1, 3 seed. **f존·gold존 일봉 3/3 PASS**(+2~3%, holdout 양수, 부호안정) → **일봉 흑자가 선택편향 아님 입증**. gold는 일봉 견고/intraday만 문제(#6~8이 처방). sf존 표본<30 FAIL. 정책(gold 비중·5m·자본증액)은 사용자 결정(잔존 한계: 단일 데이터소스·슬리피지 미반영 → 소액 단계 진입).

---

## 3. 적대적 검증 결과 (confidence HIGH) + ⚠️ 경고

**실현성**: 핵심 변경(진입 재검증, min_atr tf분리, 종목코드, strategy_id, KPI인프라) 모두 **code_feasible=true, contradicts_evidence=false**.

**⚠️ 반드시 지킬 경고**:
1. **과적합 — gold는 "전체"가 아니라 "intraday 한정" 문제**. 일봉 gold는 +1.76%(890거래) 흑자. gold 비중축소는 **timeframe 한정**으로만. 5/29 0승2패(표본 2건)로 gold 전체를 단정하면 검증된 일봉 흑자를 폐기하는 역과적합.
2. **선택편향** — 모든 흑자결론이 변동성 상위 51종목 유니버스. **OOS+랜덤 유니버스 통과 전 자본 반영 금지**.
3. **in-sample 튜닝값** — min_score 4.0·min_atr 0.035·cutoff 14:00은 in-sample 산물. "백테스트 흑자 방향" 추가조정(예: min_score 5.0)은 holdout 부호안정 검증 없이는 과적합.
4. **사실 정정 (P5-3)** — 매수 재진입 쿨다운(`BUY_REENTRY_COOLDOWN_MIN=30`)은 **이미 존재**. 신설 대상은 "쿨다운"이 아니라 **"종목당 일일 진입 횟수 상한"**.
5. **5분봉 마진 취약** — gold 5m +0.14%(슬리피지 후 +0.04%), 단일종목 제거 시 부호반전. 5m 트랙은 표본·부호안정 재확인 전 실거래 금지.
6. **실거래 hot-path 다수**(진입 게이트·DB기록·청산정합·코드정규식·DCA게이트)는 dry-run/shadow/모의서버 검증 선행 필수.

---

## 4. 즉시 착수 (이번 작업)
- **순위 3 (DCA strategy_id 전파)** 구현 — 메타데이터만, 동작 불변, 검증자 hitl=false. 전략별 손익 귀속 왜곡 즉시 해소.
- 나머지(순위 1·2·4~10)는 각 Phase 게이트(dry-run/shadow/HITL/OOS)에 따라 단계 진행 — §5 결정 필요.

## 5. HITL 결정 필요 (다음 단계)
1. **Phase 0 무위험 3건(순위 1·2·3)** 모두 즉시 진행할지 (1·2는 신규 집계/스냅샷 로직).
2. **순위 6(진입 재검증 게이트)** — 가장 큰 손실차단 효과지만 hot-path. 분봉 min_atr 0.01 선결 + shadow 검증 동반.
3. **순위 10 정책** — gold 인트라데이 비중·5m 채널·자본증액 관문 (사용자 결정).
