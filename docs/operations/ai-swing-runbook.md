# ai_swing 운영 런북 (단테 예측 종목 스윙 매매)

**작성일**: 2026-07-30
**전략**: `ai_swing` (`backend/core/strategy/ai_swing.py`, `STRATEGY_ID="ai_swing_v1"`)
**상태**: 🔴 **전 플래그 기본 OFF** — 활성은 사용자 판단 (CLAUDE.md §2 S4)
**관련**: 실측 리포트 `docs/04-report/features/2026-07-30-ai-swing-p0.report.md`

---

## 0. 이 전략의 성격 (활성 전 합의 사항)

- **다일 보유 스윙** — `min_hold_days=3` / `max_hold_days=20`. 당일 청산하지 않는다.
- **승률이 아니라 손익비로 수익을 내는 구조.** 실측상 손절을 좁힐수록 승률은 떨어지고
  (SL -5% → 승률 25~28%) 평균 수익률은 올라간다. **승률 80% KPI 와는 방향이 반대다.**
- 종목 원천은 운영 머신 ai-trade 봇의 **스캔 ∩ 예측 교집합**이다. 그 산출물이 없으면
  전략은 아무것도 하지 않는다(`status="no_data"` → 진입 0).

---

## 1. 플래그 (전부 기본 OFF)

| 플래그 | 기본 | 역할 |
|---|---|---|
| `BARRO_AI_TRADE_DIR` | `""` | ai-trade 산출물 디렉토리. **미설정이면 로더가 즉시 no_data** → 전략 완전 inert |
| `BARRO_AI_SWING_ENABLED` | `0` | 관측 데몬 마스터. 0이면 즉시 종료 |
| `BARRO_AI_SWING_ENTRY_ENABLED` | `0` | **진입만** 차단. 0이어도 청산은 계속 평가된다 |
| `BARRO_AI_SWING_DRYRUN` | `1` | 1이면 주문 시뮬(audit 에 `DRY_RUN` 기록) |
| `BARRO_AI_SWING_BUDGET_RATIO` | `0.0` | ai_swing 평가액 상한 비율. **0이면 진입 0** |
| `BARRO_AI_SWING_MAX_POSITIONS` | `3` | 동시 보유 슬롯 |
| `BARRO_AI_SWING_SL_PCT` | `-5.0` | 손절률(percent). `HoldingEvaluator` 프로파일과 `build_exit_plan` 양쪽에 적용 |
| `BARRO_AI_SWING_ALLOW_STALE` | `0` | 전일자 산출물 허용 여부 |
| `BARRO_AI_SWING_MAX_AGE_H` | `12` | 신선도 임계(소비자 판단용) |
| `BARRO_AI_SWING_FALLBACK` | `""` | `scan_only` 면 예측 부재 시 스캔 단독 허용 |
| `LIVE_TRADING_ENABLED` | 미설정 | **상위 마스터 게이트** — 미설정이면 모든 실주문 차단 |

⚠️ `BARRO_AI_SWING_SL_PCT` 는 `BARRO_SWING38_SL_PCT` 와 **별개**다. swing_38 튜닝이
ai_swing 을 오염시키지 않도록 분리했다.

---

## 2. 활성화 순서 (반드시 이 순서로)

```bash
# 0) 전제 — ai-trade 측 predictions 산출물이 실제로 생기는지 먼저 확인
ls -l $BARRO_AI_TRADE_DIR/predictions_$(date +%F).json
#    없으면 교집합이 영구 partial → shadow 실측이 시작되지 않는다.
#    ai-trade 측 save_predictions() 훅 적용이 선행 조건.

# 1) shadow (주문 0건) — 1~2주
export BARRO_AI_TRADE_DIR=/path/to/ai-trade/logs
export BARRO_AI_SWING_ENABLED=1
export BARRO_AI_SWING_ENTRY_ENABLED=0
#    → data/ai_swing_universe.json 과 ai_swing_signals.json 에
#      "교집합 종목 수" + "진입 신호 수" 가 누적된다.

# 2) 판정 — 실측 표본이 없으면 중단한다 (§0-2)
#    교집합이 주 0~1종목이거나 진입 신호가 0 이면 전략이 성립하지 않는다.
#    → 완화 파라미터(min_score, fib_tolerance) 재조정 후 shadow 재개.

# 3) DRY_RUN 주문 경로 확인  ★BUDGET_RATIO 를 올려야 주문 시도가 생긴다★
export BARRO_AI_SWING_ENTRY_ENABLED=1
export BARRO_AI_SWING_DRYRUN=1
export BARRO_AI_SWING_BUDGET_RATIO=0.10
grep "ai_swing" data/order_audit.csv | tail    # action=DRY_RUN 행 확인

# 4) 프로파일 배선 실증 (매도 없이 표만)
python scripts/evaluate_holdings.py
#    → "swing 최소 보유 N일 < min 3일 → 청산 평가 차단" 문구가 보여야 한다.

# 5) 고아 0건 확인
python scripts/ai_swing_recover.py --dry-run

# 6) 일일손실 게이트 상태 확인 후 실주문 (사용자 판단)
#    ※ GatePolicy 의 일일손실 매수차단이 TEMP 비활성(2026-07-07) 상태일 수 있다.
export LIVE_TRADING_ENABLED=true
export BARRO_AI_SWING_DRYRUN=0
export BARRO_AI_SWING_MAX_POSITIONS=1        # 1슬롯부터 시작
```

---

## 3. 🔴 비활성화 순서 (2026-05-29 사고 재발 방지)

**보유분이 있는 상태에서 마스터 플래그를 내리면 안 된다.** 5/29 swing_38 비활성 시
장부 동기화 누락 + 보유분 자동 청산 부재로 잔여 4종목을 사용자가 수동 청산해야 했다
(평균 -0.985%).

```bash
# 1) 진입만 차단 — 청산 권한은 유지한다
export BARRO_AI_SWING_ENTRY_ENABLED=0

# 2) 보유분이 0 이 될 때까지 기다린다 (max_hold 20일이면 최장 20일)
python scripts/evaluate_holdings.py | grep ai_swing
cat data/ai_swing_positions.json

# 3) 보유 0 확인 후에만 마스터를 내린다
export BARRO_AI_SWING_ENABLED=0
```

**즉시 전량 정리가 필요하면** `ENABLED=0` 이 아니라 수동 청산을 먼저 한다 —
플래그 OFF 는 청산을 유발하지 않고 오히려 관리 주체를 없앤다.

---

## 4. 청산 권한 (경로 2개 — 최소 하나는 반드시 살아 있어야 한다)

| 경로 | 주체 | 조건 |
|---|---|---|
| 주 경로 | `scripts/intraday_buy_daemon.py` `_evaluate_and_sell` | 데몬이 매 거래일 가동돼야 한다(cron). 전략 필터 없이 브로커 보유 전체를 평가하므로 ai_swing 장부가 있으면 자동 포함 |
| 대체 경로 | `scripts/evaluate_holdings.py --auto-sell` (매시간 cron) | 동일 `PositionContext(strategy=...)` 를 구성해 같은 프로파일을 적용 |

⚠️ 현 운영이 **슈퍼트렌드 단일 집중**이면 `intraday_buy_daemon` 이 꺼져 있을 수 있다.
그 경우 ai_swing 청산의 실경로는 **매시간 `evaluate_holdings` cron** 이다.
ai_swing 을 켤 때 **두 경로 중 어느 것이 살아 있는지 반드시 확인**한다.

⚠️ `ai_swing_daemon` 은 **매도하지 않는다**(이중 매도 방지). 관측·진입 전용이다.

---

## 5. 고아 포지션 (장부 유실)

**증상**: 브로커에 보유는 있는데 `active_positions.json` 에 없다.
**결과**: `HoldingEvaluator` 가 `ctx=None` 경로로 빠져 전략 프로파일을 무시하고
`data/policy.json`(SL **-2.0** / TP +5.0)으로 평가한다 → **SL -15% 와 min_hold 3일이
전부 무시되고 -2% 에서 전량 손절된다.** (테스트 `test_ai_swing_orphan.py` 로 현상 고정)

```bash
python scripts/ai_swing_recover.py --dry-run     # 차집합 진단
python scripts/ai_swing_recover.py --apply       # 원천(order_audit/fill_audit) 기반 복원
```

**복원 원천이 없으면 복원하지 않는다** — `entry_time` 을 "지금"으로 채우면
`min_hold_days=3` 이 리셋돼 3일 더 묶인다. 그 경우 `no_data` 로 보고하고
사용자가 수동 판단한다 (§0-2).

**진실원천 규칙**: `active_positions.json` 이 진실원천이다(브로커 대사 `_sync_positions` 보유).
`data/ai_swing_positions.json` 은 **append-only 감사·복구 참고용**이며 갱신 주체는 진입 훅과
동일 프로세스다. 두 장부가 갈라지면 `active_positions.json` 을 따른다.

---

## 6. ⚠️ 운영 경고 (실측·검증에서 확인된 것)

1. **min_hold 3일 동안 손절이 걸리지 않는다.** `HoldingEvaluator` 의 보유기간 게이트가
   SL/TP/트레일링보다 **먼저** 평가되므로(`holding_evaluator.py:306-313`),
   진입 후 3일간 -15% 를 초과하는 손실도 청산되지 않는다. 사용자 확정 정책이다.
2. **hold 일수는 달력일이다** (`(now - entry_time).days`). 금요일 진입 시 min_hold 3일이
   거래일 1일 만에 해제되고, max_hold 20일은 거래일 약 14일이다.
3. **데몬 자체 가드 `MIN_HOLD_MINUTES=15`** — 진입 후 15분간 방어적 매도(SL·BE·트레일)가
   차단되고 hard SL -5% 만 우회한다. ai_swing 에도 적용된다.
4. **EOD 트림 면제 ≠ 장중 자동매도 면제.** `_FORCE_CLOSE_EXEMPT_STRATEGIES` 는
   15:10~15:19 carry-limit 트림만 면제한다. 보유 유지의 실체는 exit 프로파일이다.
5. **데몬 미기동일엔 청산 평가가 스킵된다** (KRX 휴장일 캘린더가 코드에 없다).
   cron 누락·장애일엔 그날 평가가 통째로 빠지고 다음 거래일로 이월된다.
6. **ai-trade 스캔이 자정 이전에 돌면 `date=전일`** 이 되어 산출물이 영구 `stale` 이다.
   운영 crontab 실행 시각을 확인할 것.

---

## 7. 파라미터 조정

```bash
# 손절 (즉시 롤백 가능한 kill-lever)
export BARRO_AI_SWING_SL_PCT=-5.0

# 백테스트로 근거 확인 (주문 없음, 일봉 캐시만 읽음)
python scripts/backtest_ai_swing.py --random 120 --seeds 42,7,123 \
       --grid "sl=-5,-8,-15 trail_start=0,20"
python scripts/backtest_ai_swing.py --universe-from-ai-trade --grid "sl=-5"
```

**현재 기본값 (2026-07-30 그리드 실측 최적, 사용자 승인 적용)**:
`SL -5% / trail_start +10% / trail_off 3%` — 랜덤 유니버스 120종목 × 3 seed 에서
27/27 PASS · 3-seed 평균 +2.172% · holdout +2.75~4.17% · 승률 36~39.5%.
초기 swing_38 계승값(SL -15%/trail +20%·5%)은 +0.309%/1-of-3 PASS 였다.

⚠️ **단테 교집합이 아닌 대조군** 기준이며 캐시 `as_of 2026-06-18`(약 6주 낙후)이다.
교집합 shadow 실측 후 재검토할 것. 롤백은 `BARRO_AI_SWING_SL_PCT=-15.0`(손절만) 또는
`AiSwingParams` + `STRATEGY_EXIT_PROFILES` 양쪽 되돌리기(트레일링 포함).
