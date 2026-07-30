# ai_swing 운영 런북 (단테 산출물 기반 스윙 매매)

**작성일**: 2026-07-30 / **정정**: 2026-07-31 (§1 플래그표 허위 기술 정정 — 아래 §-1)
**전략**: `ai_swing` (`backend/core/strategy/ai_swing.py`, `STRATEGY_ID="ai_swing_v1"`)
**상태**: 🔴 **전 플래그 기본 OFF** — 활성은 사용자 판단 (CLAUDE.md §2 S4)
**관련**: 실측 리포트 `docs/04-report/features/2026-07-30-ai-swing-p0.report.md`
**단타 중지 절차**: `docs/operations/daytrading-pause-to-ai-swing.md`

---

## -1. 🔴 2026-07-31 정정 고지 (이 문서의 이전 판은 틀렸다)

이전 판 §1 플래그표는 **코드에 존재하지 않는 플래그를 실재하는 것처럼** 기술했다.
2026-07-31 재실측으로 확인된 사실:

| 이전 판의 기술 | 실측 결과 |
|---|---|
| `BARRO_AI_SWING_ENABLED` 등 4종이 이미 동작하는 것처럼 표기 | 작성 시점에는 **코드에 없었다**. 같은 워크트리에서 이번 라운드에 신규 구현됐다(§1-A) |
| `BARRO_AI_SWING_DRYRUN` | **코드에 존재하지 않는다** — grep 0건 (§1-B) |
| §2 활성화 순서가 "predictions 산출물이 곧 생긴다"를 전제 | ai-trade 리포에 **저장 코드 자체가 없다** (§0-3) |
| `data/ai_swing_positions.json` 을 조회·해석 대상으로 기술 | **쓰는 코드가 없다** (§5) |
| §6-1 이 `holding_evaluator.py:306-313` 인용 | 실제 위치는 `:319-337` (파일이 이후 수정됨) |

이 판의 모든 인용은 **직접 파일을 열어 재확인**했다. 기준 파일 해시는 §8.

---

## 0. 이 전략의 성격 (활성 전 합의 사항)

**0-1. 다일 보유 스윙** — `min_hold_days=3` / `max_hold_days=20`
(`backend/core/risk/holding_evaluator.py:193-194`, `backend/core/strategy/ai_swing.py:74`).
당일 청산하지 않는다.

**0-2. 승률이 아니라 손익비로 수익을 내는 구조.** 현재 기본값 실측 승률은 **36~39.5%** 다
(트레일링 최적화 전, 손절만 좁혔을 때는 25~28%였다). **승률 80% KPI 와는 방향이 다르다** —
활성 전 이 점을 합의해야 한다.

**0-3. 🔴 `predictions_*.json` 은 아직 한 번도 생성된 적이 없다 — 교집합은 현재 성립 불가.**

실측 (2026-07-31, `/Users/beye/workspace/ai-trade`, remote `github.com/82beye/ai-trade`):

| 확인 항목 | 결과 |
|---|---|
| `predictions` 문자열이 든 소스 파일 | **0건** (`frontend/.next/` 빌드 sourcemap 2건 제외 — 무관) |
| `predictions_*.json` 을 쓰는 저장 코드 | **없음** |
| `logs/` 내 `predictions_*.json` 파일 | **0개** |
| `logs/` 내 `watchlist_*.json` 파일 | **1개** (`watchlist_2026-05-04.json`) |
| 스캔 산출물 저장 코드 | `scanner/daily_screener.py:225-234` `_save_watchlist()` → `./logs/watchlist_{today}.json` |

로더(`backend/core/scanner/ai_trade_universe.py`)는 `watchlist` 만 있고 `predictions` 가 없으면
`status="partial"` 로 강등한다(`:334-349`). 이때 `items` 는
`BARRO_AI_SWING_FALLBACK=scan_only` 일 때만 채워지고(`:336-339`), 아니면 **빈 튜플**이다(`:341-342`).

→ **결론: `BARRO_AI_SWING_FALLBACK=scan_only` 로 "스캔 단독 관측"부터 시작해야 한다.**
예측 산출물이 생기기 전까지 교집합 실측은 불가능하다.

**0-4. ⚠️ 라벨 정직성 (§8) — scan_only 구간의 종목 원천은 "단테 예측"이 아니라 "단테 스캔"이다.**

`scan_only` 로 담긴 `items` 는 교집합이 아니라 **watchlist 단독** 종목이며,
예측 측 필드(`pred_rank` / `pred_score` / `confidence` / `consensus_level`)는 전부 `0` 또는 `""` 다
(`ai_trade_universe.py:75` 주석, `:338` `_build_items(scan_rows, {}, ...)`).
로더 자신도 `intersect_count` docstring 에서 이를 경고한다 — `ai_trade_universe.py:108-109`:

> `intersect_count` 주의: `status="partial"` + `BARRO_AI_SWING_FALLBACK=scan_only` 인 경우
> items 는 교집합이 아니라 **스캔 단독** 종목이다. 반드시 status 와 함께 해석한다.

**리포트·알림·커밋 메시지에 "단테 예측 종목"이라고 쓰지 말 것.**
"단테 스캔 단독(`status=partial`, `fallback=scan_only`)"이라고 쓴다.
`pred_score` 가 0 인 통계를 "예측 점수 평균 0"으로 보고하는 것도 오도다 — **미산출**이라고 쓴다.

---

## 1. 플래그 (전부 기본 OFF)

### 1-A. 코드에 실재하는 플래그 — 실측 2026-07-31

| 플래그 | 기본 | **구현 위치 (실제로 읽히는 곳)** | 역할 |
|---|---|---|---|
| `BARRO_AI_TRADE_DIR` | `""` | `backend/core/scanner/ai_trade_universe.py:54`(상수) → `:311` (`os.environ.get(ENV_DIR, "")`) | ai-trade 산출물 디렉토리. 미설정이면 `:313-314` 에서 즉시 `no_data`("ai_trade_dir_unset") → 전략 완전 inert |
| `BARRO_AI_SWING_FALLBACK` | `""` | `ai_trade_universe.py:55` → `:336-339` | `scan_only` 일 때만 예측 부재 시 스캔 단독 items 허용. 그 외엔 `:341-342` 로 빈 items |
| `BARRO_AI_SWING_MAX_AGE_H` | `12` | `ai_trade_universe.py:56` → `:165-174` `_read_freshness_env()` | ⚠️ **읽어서 debug 로그만 남긴다.** `:319` 주석 "소비자용 힌트 — 로그만 (status 판정에 미사용)". **현재 이 값을 보고 행동을 바꾸는 소비자 코드는 없다** (grep 결과 ai_swing_daemon·intraday_buy_daemon 모두 미사용) → 사실상 **no-op** |
| `BARRO_AI_SWING_ALLOW_STALE` | `0` | ① `ai_trade_universe.py:57` → `:165-174` (로더에서는 로그만) ② `scripts/ai_swing_daemon.py:84` → `:425-429` (**여기서는 실제로 작동** — stale 이면 신호 평가를 건너뛰고 `reason="stale_not_allowed:..."`) | 전일자 산출물 허용 여부. 로더가 아니라 **데몬**이 판단한다 |
| `BARRO_AI_SWING_SL_PCT` | `-5.0` | ① `backend/core/risk/holding_evaluator.py:185` (프로파일 SL) ② `backend/core/strategy/ai_swing.py:45` + `:48-67` `_sl_fraction_from_env()` (percent→fraction, `build_exit_plan` 경로) ③ `scripts/ai_swing_recover.py:221` | 손절률(percent). ⚠️ **③만 default 가 `-15.0` 이다 — ①②(`-5.0`)와 불일치**(§7-3) |
| `BARRO_AI_SWING_ENABLED` | `0` | **이번 라운드 신규** — `scripts/ai_swing_daemon.py:80` → `:407-409` | 관측 데몬 마스터. truthy 아니면 즉시 종료하고 **파일도 쓰지 않는다** |
| `BARRO_AI_SWING_MIN_PRED_SCORE` | `0` | **이번 라운드 신규** — `ai_swing_daemon.py:81` → `:443` | 예측점수 하한 (0=무필터). ※ scan_only 구간엔 `pred_score`=0 이라 **0 이외 값은 전량 탈락**시킨다 |
| `BARRO_AI_SWING_MIN_CONSENSUS` | `""` | **이번 라운드 신규** — `ai_swing_daemon.py:82` → `:444` (라벨 사다리 `:93-99`) | 합의수준 하한 (빈값=무필터). ※ scan_only 구간엔 `consensus_level`=`""` |
| `BARRO_AI_SWING_TOP_N` | `0` | **이번 라운드 신규** — `ai_swing_daemon.py:83` → `:440` (CLI `--top` 우선, `:496`) | 관측 상한 종목수 (0=전체) |
| `BARRO_DATA_DIR` | `<repo>/data` | **이번 라운드 신규** — `ai_swing_daemon.py:85` → `:110-112` `data_dir()` | 산출 JSON 디렉토리 (테스트 격리용) |
| `BARRO_AI_SWING_ENTRY_ENABLED` | `0` | **이번 라운드 신규 (편집 중)** — `scripts/intraday_buy_daemon.py:815-817` `_ai_swing_entry_enabled()` | ai_swing 후보 주입 여부. 0이면 후보 합성 자체를 안 한다 |
| `BARRO_AI_SWING_BUDGET_RATIO` | `0.0` | **이번 라운드 신규 (편집 중)** — `intraday_buy_daemon.py:820-830` `_ai_swing_caps()` | ai_swing 예산 비율. **0이면 진입 0**. 파싱 실패도 0.0 |
| `BARRO_AI_SWING_MAX_POSITIONS` | `3` | **이번 라운드 신규 (편집 중)** — `intraday_buy_daemon.py:820-830` `_ai_swing_caps()` | 동시 보유 슬롯. 파싱 실패는 0(=진입 차단) |
| `BARRO_DAEMON_STRATEGIES` | 미설정 | `intraday_buy_daemon.py:2173-2175` → `_parse_strategies` `:128-138` | 데몬 일반전략 목록. **`ai_swing` 을 여기 포함시켜야** 진입 훅이 산다 |
| `LIVE_TRADING_ENABLED` | 미설정 | `backend/core/risk/live_order_gate.py:112`(기본 이름) → `:158-166` `_preflight` | **상위 마스터 게이트**. 🔴 **side 분기가 없어 매도까지 차단한다** — 정지 용도로 쓰지 말 것 (`daytrading-pause-to-ai-swing.md` §1-1) |

⚠️ `BARRO_AI_SWING_SL_PCT` 는 `BARRO_SWING38_SL_PCT`(`holding_evaluator.py:141`)와 **별개**다.
swing_38 튜닝이 ai_swing 을 오염시키지 않도록 분리했다 (`ai_swing.py:41-45` 주석).

### 1-B. 코드에 존재하지 않는 플래그 (이전 판의 허위 기술)

| 플래그 | 상태 |
|---|---|
| `BARRO_AI_SWING_DRYRUN` | ❌ **grep 0건.** 관측 데몬은 애초에 주문을 안 하고(§4), 진입 훅의 DRY_RUN 은 데몬 공통 `--dry-run`/`--no-dry-run`(`intraday_buy_daemon.py`)과 `LIVE_TRADING_ENABLED` 가 결정한다. 이 이름의 전용 플래그는 없다 |

### 1-C. 실측 방법 (문서를 다시 믿기 전에 직접 확인할 것)

```bash
WT=/Users/beye/workspace/BarroAiTrade/.claude/worktrees/ai-swing
grep -rn 'BARRO_AI_SWING\|BARRO_AI_TRADE_DIR' "$WT/backend" "$WT/scripts" --include='*.py' | sort
```

---

## 2. 활성화 순서 (반드시 이 순서로)

```bash
# 0) 전제 실측 — 무엇이 실제로 있는지부터 본다 (§0-3)
ls -l "$BARRO_AI_TRADE_DIR"/watchlist_*.json     # 스캔 산출물: 존재
ls -l "$BARRO_AI_TRADE_DIR"/predictions_*.json   # 예측 산출물: 현재 0개 — 없는 것이 정상이다
#    predictions 저장 코드가 ai-trade 리포에 아예 없다(§0-3).
#    → 교집합을 기다리지 말고 스캔 단독으로 시작한다.

# 1) shadow — 스캔 단독 관측 (주문 0건) — 1~2주
export BARRO_AI_TRADE_DIR=/path/to/ai-trade/logs
export BARRO_AI_SWING_FALLBACK=scan_only      # ★없으면 items 가 영구 0 (§0-3)
export BARRO_AI_SWING_ENABLED=1
python scripts/ai_swing_daemon.py             # 관측 전용. 주문 경로 import 조차 없다(§4)
#    → data/ai_swing_universe.json (로더 산출 그대로)
#      data/ai_swing_signals.json  (evaluated / 진입 신호 수)
#    ★ 이 구간 산출물의 라벨은 "단테 스캔 단독"이다 — "예측"이라 쓰지 말 것 (§0-4)
#    ★ MIN_PRED_SCORE / MIN_CONSENSUS 는 이 구간에 건드리지 말 것 — pred 필드가 전부
#      0/"" 이라 0 이외 값을 주면 전량 탈락한다 (§1-A)

# 2) 판정 — 실측 표본이 없으면 중단한다 (§0-2)
#    스캔 단독 종목이 주 0~1종목이거나 진입 신호가 0 이면 전략이 성립하지 않는다.
#    → 완화 파라미터(AiSwingParams.min_score, ai_swing.py:81) 재조정 후 shadow 재개.

# 3) 진입 훅 배선 확인  ★BUDGET_RATIO 를 올려야 주문 시도가 생긴다★
export BARRO_DAEMON_STRATEGIES=ai_swing        # ① 후보 목록에 포함 (없으면 영구 진입 0)
export BARRO_AI_SWING_ENTRY_ENABLED=1          # ② 후보 주입
export BARRO_AI_SWING_BUDGET_RATIO=0.10        # ③ 예산 (0 이면 진입 0)
#    ④ BARRO_AI_TRADE_DIR + 산출물 존재는 1) 에서 이미 충족
#    데몬은 dry-run 이 기본 — 실주문은 --no-dry-run + LIVE_TRADING_ENABLED 가 별도로 필요.
grep "ai_swing" data/order_audit.csv | tail    # DRY_RUN 행 확인

# 4) 프로파일 배선 실증 (매도 없이 표만)
python scripts/evaluate_holdings.py
#    → "swing 최소 보유 N일 < min 3일 → 청산 평가 차단" 문구가 보여야 한다
#      (문구 원천: backend/core/risk/holding_evaluator.py:336)

# 5) 고아 0건 확인
python scripts/ai_swing_recover.py --dry-run   # (scripts/ai_swing_recover.py:241 — 기본값)

# 6) 일일손실 게이트 상태 확인 후 실주문 (사용자 판단)
export LIVE_TRADING_ENABLED=true
export BARRO_AI_SWING_MAX_POSITIONS=1          # 1슬롯부터 시작
```

---

## 3. 🔴 비활성화 순서 (2026-05-29 사고 재발 방지)

**보유분이 있는 상태에서 마스터 플래그를 내리면 안 된다.** 5/29 swing_38 비활성 시
장부 동기화 누락 + 보유분 자동 청산 부재로 잔여 4종목을 사용자가 수동 청산해야 했다
(평균 -0.985%).

```bash
# 1) 진입만 차단 — 청산 권한은 유지한다
export BARRO_AI_SWING_ENTRY_ENABLED=0          # 또는 BARRO_AI_SWING_BUDGET_RATIO=0
#    ※ BARRO_DAEMON_STRATEGIES 에서 ai_swing 을 빼도 된다. 어느 쪽이든
#      _evaluate_and_sell(청산)은 계속 돈다 (intraday_buy_daemon.py:2055)

# 2) 보유분이 0 이 될 때까지 기다린다 (max_hold 20일이면 최장 20일)
python scripts/evaluate_holdings.py | grep ai_swing
cat data/active_positions.json                 # ★진실원천 (§5)

# 3) 보유 0 확인 후에만 관측 데몬 마스터를 내린다
export BARRO_AI_SWING_ENABLED=0
```

**즉시 전량 정리가 필요하면** 플래그 OFF 가 아니라 **수동 청산을 먼저** 한다 —
플래그 OFF 는 청산을 유발하지 않고 오히려 관리 주체를 없앤다.
🔴 `LIVE_TRADING_ENABLED=false` 로 세우지 말 것 — **매도까지 막힌다**
(`live_order_gate.py:158-166`, 상세는 `daytrading-pause-to-ai-swing.md` §1-1).

---

## 4. 청산 권한 (경로 2개 — 최소 하나는 반드시 살아 있어야 한다)

| 경로 | 주체 | 조건 |
|---|---|---|
| 주 경로 | `scripts/intraday_buy_daemon.py` `_evaluate_and_sell` (`:344`, 루프 호출 `:2055`) | 데몬이 매 거래일 가동돼야 한다(cron). 전략 필터 없이 브로커 보유 전체를 평가하므로 ai_swing 장부가 있으면 자동 포함 |
| 대체 경로 | `scripts/evaluate_holdings.py --auto-sell` (매시간 cron, `:347-351`) | 동일 `PositionContext(strategy=...)`(`:210`)를 구성해 같은 프로파일을 적용 |

⚠️ 현 운영이 **슈퍼트렌드 단일 집중**이면 `intraday_buy_daemon` 이 꺼져 있을 수 있다.
그 경우 ai_swing 청산의 실경로는 **매시간 `evaluate_holdings` cron** 이다.
ai_swing 을 켤 때 **두 경로 중 어느 것이 살아 있는지 반드시 확인**한다.

⚠️ `scripts/ai_swing_daemon.py` 는 **매도하지 않는다.** 관측 전용이며 매매 체결 경로를
**import 조차 하지 않는다**(`:4-8` docstring, 테스트
`backend/tests/test_ai_swing_daemon.py::test_source_has_no_execution_symbols` 가 소스 텍스트로 고정).
진입 후보 주입은 데몬이 아니라 `intraday_buy_daemon.py` 의 진입 훅(`:796-830`)이 한다.

---

## 5. 고아 포지션 (장부 유실)

**증상**: 브로커에 보유는 있는데 `data/active_positions.json` 에 없다.
**결과**: `HoldingEvaluator` 가 `ctx=None` 경로로 빠져(`backend/core/risk/holding_evaluator.py:297-299`
→ `_evaluate_basic(h, policy)`) 전략 프로파일을 무시하고
`data/policy.json`(SL **-2.0** / TP +5.0, 개발 머신 실측)으로 평가한다 →
**전략 SL(-5%)·트레일링·min_hold 3일이 전부 무시되고 -2% 에서 전량 손절된다.**
(테스트 `backend/tests/test_ai_swing_orphan.py` 로 현상 고정)

```bash
python scripts/ai_swing_recover.py --dry-run     # 차집합 진단 (:241)
python scripts/ai_swing_recover.py --apply       # 원천(order_audit/fill_audit) 기반 복원 (:242)
```

**복원 원천이 없으면 복원하지 않는다** — `entry_time` 을 "지금"으로 채우면
`min_hold_days=3` 이 리셋돼 3일 더 묶인다(`ai_swing_recover.py:219` 주석 "★원천 그대로").
그 경우 `no_data` 로 보고하고 사용자가 수동 판단한다 (§0-2).

**진실원천 규칙**: `data/active_positions.json` 이 진실원천이다
(`backend/core/journal/active_positions.py:145` 기본 경로, 브로커 대사 `_sync_positions` 보유).

⚠️ **정정**: 이전 판은 `data/ai_swing_positions.json` 을 "append-only 감사·복구 참고용 장부"로
기술했으나, **이 파일을 쓰는 코드는 존재하지 않는다**(2026-07-31 grep 0건).
현재 ai_swing 관련 산출 파일은 관측 데몬이 쓰는 두 개뿐이다
(`ai_swing_daemon.py:87-88`): `data/ai_swing_universe.json`, `data/ai_swing_signals.json`.
둘 다 **관측 기록이며 포지션 장부가 아니다.**

---

## 6. ⚠️ 운영 경고 (실측·검증에서 확인된 것)

1. **min_hold 3일 동안 손절이 걸리지 않는다.** `HoldingEvaluator` 의 보유기간 게이트가
   SL/TP/트레일링보다 **먼저** 평가된다 — `holding_evaluator.py:319-337`
   (max_hold 강제매도 `:322-329`, min_hold 차단 `:330-337`).
   진입 후 3일간 SL(-5%)을 초과하는 손실도 청산되지 않는다. 사용자 확정 정책이다.
   ※ SL 을 -15%→-5% 로 좁혔으므로 이 3일 무방어 구간의 실질 위험이 커졌다 —
     `BARRO_AI_SWING_MAX_POSITIONS`/`BUDGET_RATIO` 로 익스포저를 제한할 것.
   *(정정: 이전 판은 `:306-313` 으로 인용했으나 파일 수정으로 위치가 이동했다.)*
2. **hold 일수는 달력일이다** (`_hold_days(ctx.entry_time)`, `holding_evaluator.py:317`).
   금요일 진입 시 min_hold 3일이 거래일 1일 만에 해제되고, max_hold 20일은 거래일 약 14일이다.
3. **데몬 자체 가드 `MIN_HOLD_MINUTES=15`** (`intraday_buy_daemon.py:74`, 적용 `:546-554`) —
   진입 후 15분간 **방어적** 매도(`STOP_LOSS`·`BREAKEVEN_STOP`·`TIME_TIGHTENED_SL`)가
   차단된다. ai_swing 에도 적용된다. 단 코드 주석 `:559-562` 가 명시하듯 예외가 둘 있다:
   - **hard SL `-5%` 는 우회한다**(`HARD_SL_PCT`, `:77`) — 15분 안이라도 손절된다.
   - **익절**(`TRAILING_STOP`·`TAKE_PROFIT`·`PARTIAL_TP`)은 애초에 차단 대상이 아니다.
   ⚠️ ai_swing 의 SL 기본값이 정확히 **-5.0%** 라(`holding_evaluator.py`, `BARRO_AI_SWING_SL_PCT`)
   이 우회 임계와 같다. 즉 "15분간 완전 무방어"가 아니다 — SL 을 -5% 보다 **깊게**
   바꾸면 그때부터 15분 무방어 구간이 실제로 생긴다.
4. **EOD 트림 면제 ≠ 장중 자동매도 면제.** `_FORCE_CLOSE_EXEMPT_STRATEGIES = {"swing_38", "ai_swing"}`
   (`intraday_buy_daemon.py:780`, 판정 `:783-793`)는 EOD carry-limit 트림만 면제한다
   (`:778-779` 주석). 보유 유지의 실체는 exit 프로파일이다.
5. **데몬 미기동일엔 청산 평가가 스킵된다** (KRX 휴장일 캘린더가 코드에 없다).
   cron 누락·장애일엔 그날 평가가 통째로 빠지고 다음 거래일로 이월된다.
6. **ai-trade 스캔이 자정 이전에 돌면 `date=전일`** 이 되어 산출물이 영구 `stale` 이다
   (`ai_trade_universe.py:177-181` `_find_source` — 오늘자 파일이 없으면 최근 날짜 파일을 잡고
   status 가 stale 로 귀결). 운영 crontab 실행 시각을 확인할 것.
7. **`BARRO_AI_SWING_MAX_AGE_H` 는 현재 아무 동작도 하지 않는다** (§1-A). 신선도로 진입을
   막고 싶으면 `BARRO_AI_SWING_ALLOW_STALE=0`(관측 데몬 `ai_swing_daemon.py:425-429`)에 의존한다.

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
CLI 근거: `scripts/backtest_ai_swing.py:228`(`--random`) `:229`(`--universe-from-ai-trade`)
`:233`(`--seeds`) `:234`(`--grid`).

**7-1. 현재 기본값 (2026-07-30 그리드 실측 최적, 사용자 승인 적용)**:
`SL -5% / trail_start +10% / trail_off 3%` — 랜덤 유니버스 120종목 × 3 seed 에서
27/27 PASS · 3-seed 평균 +2.172% · holdout +2.75~4.17% · 승률 36~39.5%.
초기 swing_38 계승값(SL -15%/trail +20%·5%)은 +0.309%/1-of-3 PASS 였다.
코드 위치: `ai_swing.py:92`(`sl_pct=-0.05`) `:107-108`(trail) /
`holding_evaluator.py:185,189-190`.

**7-2. ⚠️ 이 수치는 단테 교집합이 아닌 대조군** 기준이며 캐시 `as_of 2026-06-18`(약 6주 낙후)이다.
게다가 §0-3 에 따라 **교집합 자체가 아직 성립하지 않는다** — 재검토는 scan_only shadow
실측 이후로 미룬다. 롤백은 `BARRO_AI_SWING_SL_PCT=-15.0`(손절만) 또는
`AiSwingParams` + `STRATEGY_EXIT_PROFILES` 양쪽 되돌리기(트레일링 포함).

**7-3. ⚠️ (관찰) SL 기본값 3곳이 어긋나 있다.**

| 위치 | `BARRO_AI_SWING_SL_PCT` 미설정 시 값 |
|---|---|
| `backend/core/risk/holding_evaluator.py:185` | `-5.0` (percent) |
| `backend/core/strategy/ai_swing.py:92` | `-0.05` (fraction, 동일) |
| `scripts/ai_swing_recover.py:221` | **`-15.0`** ← 불일치 |

고아 복원으로 만들어진 장부의 `sl_pct` 만 -15% 가 된다. 실피해 여부는 **미검증**
(복원 장부의 `sl_pct` 필드를 어느 청산 경로가 읽는지 확인 필요).
env 를 명시 설정하면 세 곳이 같아지므로, 복원 작업 시에는
`BARRO_AI_SWING_SL_PCT` 를 **명시적으로 export 한 뒤** 실행할 것.

---

## 8. 인용 기준 파일 (실측 시점 고정 — 2026-07-31)

| 파일 | md5 |
|---|---|
| `backend/core/scanner/ai_trade_universe.py` | `de3d143654e9659256369c2bfb700f5f` |
| `backend/core/strategy/ai_swing.py` | `a7d21e409b616ea1b730c7b53e584616` |
| `backend/core/risk/holding_evaluator.py` | `616516671fe81d3ddaafe1db36755acd` |
| `backend/core/risk/live_order_gate.py` | `e7b37e9876c680aac2b6ca917ccbe77c` |
| `scripts/ai_swing_recover.py` | `85414009af2300ed563cab754de7155a` |
| `scripts/ai_swing_daemon.py` | `27440e4f5f5cb73276fc965f73c4a924` |
| `scripts/intraday_buy_daemon.py` | `6247180b836b8451b322619d82a8c7d4` |

⚠️ `scripts/ai_swing_daemon.py` 와 `scripts/intraday_buy_daemon.py` 는 **이번 라운드에 편집 중**이다.
md5 가 다르면 라인 번호 대신 함수·상수명(`ENV_ENABLED`, `_ai_swing_entry_enabled`,
`_ai_swing_caps`, `_parse_strategies`)으로 찾을 것.
