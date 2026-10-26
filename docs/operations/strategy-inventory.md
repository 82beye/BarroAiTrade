# 매매전략 인벤토리 (운영 레퍼런스)

> **작성일** 2026-07-31 · **기준 커밋** `ed6aee2` (main) · **작성 방식** 전 항목 코드 직접 실측
> **자매 문서** `strategy-restart-toggles.md`(on/off 조작) · `daytrading-pause-to-ai-swing.md`(일시중지 절차)
>
> 이 문서는 **"무엇이 있고 무엇이 켜져 있나"** 의 진실원천이다. 조작 절차는 자매 문서를 본다.
> 수치는 전부 실측이며, 재현 명령을 §7 에 뒀다. 미검증 항목은 §8 에 분리했다.

## 요약

코드에 전략 **13개**(main 12 + 미머지 1)가 있고 **실주문 경로가 있는 건 7개**다.
현재 방침은 **슈퍼트렌드 단일 집중**이다.

**⚠️ 즉시 알아야 할 것 3가지**
1. `supertrend`·`limit_up_chase` 에 **청산 프로파일이 없다** → 자체 트레이더가 죽으면 `policy.json` base(**SL -2%**)로 청산된다 (§3-1).
2. 백테스트 수치 대부분이 **전략 자신의 청산으로 산출된 게 아니다** — 시뮬레이터 고정 SL -1.5% 다 (§5-1).
3. 실체결 데이터는 **전부 mock 계좌**다 (§5-2).

---

## 1. 전략 목록

### A. 실주문 경로 있음 (7)

| 전략 | 클래스 / `STRATEGY_ID` | 진입 요지 | 실행 주체 | 활성 스위치 |
|---|---|---|---|---|
| `supertrend` | `SupertrendStrategy` / `supertrend_v1`<br>`supertrend.py:280,283` | Supertrend(ATR 10 · ×3.0 · src=hl2) 추세 −1→+1 전환 + 최근 `entry_lookback`(2)봉 내 buy_signal. **5분봉**. 선택 필터 ADX·전환강도·상위TF RSI 는 전부 default 0/False | ① `run_telegram_bot.py:809` 상시<br>② `intraday_buy_daemon --supertrend` | `SUPERTREND_AUTO_ENABLED` |
| `limit_up_chase` | `LimitUpChaseTrader` / `limit_up_chase`<br>`limit_up_chase_trader.py:36` | 등락률 20~27% + 매수벽 1억 · 매수/매도 비율 3.0. 진입만 override, 청산은 `SupertrendAutoTrader` 러너 상속 | `run_telegram_bot.py:824` 상시 | `LIMIT_UP_CHASE_ENABLED` |
| `f_zone` | `FZoneStrategy` / `f_zone_v1`<br>`f_zone.py:226,237` | ① 기준봉(+3% 이상 · 거래량 2.0배) → ② 눌림(-0.5~-3% · 거래량 0.7배 이하) → ③ 이평(5/20/60) 지지 → ④ 반등(+0.5% · 거래량 1.2배). **score ≥ 4.0** | `intraday_buy_daemon` | `BARRO_DAEMON_STRATEGIES` |
| `sf_zone` | `SFZoneStrategy` / `sf_zone_v1`<br>`sf_zone.py:33,36` | **f_zone 상위 부분집합** — 독립 로직 없이 `FZoneStrategy` 위임(`sf_zone.py:43`). 임펄스 +5% · 거래량 3.0배 · **score ≥ 7.0** 이면 sf 로 재라벨 | 〃 | 〃 |
| `gold_zone` | `GoldZoneStrategy` / `gold_zone_v1`<br>`gold_zone.py:96,99` | ① BB(20,2.0) 하단 근접 ② Fib 되돌림 0.236~0.786 ③ RSI(14) 35 이하 후 38 회복 — **3중 2 이상** 충족 시 가중합(0.4/0.3/0.3), **score ≥ 5.0** | 〃 | 〃 |
| `swing_38` | `Swing38Strategy` / `swing_38_v1`<br>`swing_38.py:121,124` | ① 임펄스(30봉 내 +5% 양봉 · 거래량 2.0배) → ② Fib **0.382 ± 0.075** 되돌림 → ③ 반등 양봉. score ≥ 3.0(시뮬 진입점 5.0). **일봉 전용**(`require_daily_candles=True`) · ATR ≥ 3% | 〃 | 〃 |
| `closing_bet` | `ClosingBetStrategy` / `closing_bet_v1`<br>`closing_bet.py:135` | 기준봉 0.618 되돌림가 종가 베팅 | `closing_bet_alert_daemon.py` | `BARRO_CB_AUTOEXEC` |

### B. 시뮬 등록 · 실발화 0 (1)

| 전략 | 클래스 / ID | 상태 |
|---|---|---|
| `scalping_consensus` | `ScalpingConsensusStrategy` / `scalping_consensus_v1`<br>`scalping_consensus.py:50` | 12-에이전트 합의 임계 6.5. 시뮬 `DEFAULT_STRATEGIES` 에 **포함돼 있으나** 누적 **64 runs / 체결 0건** — 한 번도 발화한 적 없다 (`docs/04-report/daily/2026-07-08.md:12`). ⚠️ §3-2 참조 |

### C. 미배선 inert (4)

| 전략 | 클래스 / ID | 왜 inert 인가 |
|---|---|---|
| `blue_line` | `BlueLineStrategy` / `blue_line_v1` `blue_line.py:46,49` | EMA5/20 골든크로스 or 블루라인 지지 + 거래량 1.5배. `SignalScanner` 기본 **False** |
| `stock_v1` | `StockStrategy` / `stock_v1` `stock_strategy.py:74,77` | 파란점선(High224 − ATR224×2.0) 돌파 + 수박지표 가점. ⚠️ `signal_type` 을 `"blue_line"` 로 발행(`:167`) — ID 와 불일치 |
| `crypto_breakout` | `CryptoBreakoutStrategy` / `crypto_breakout_v1` `crypto_breakout.py:41` | 암호화폐 대상 — 주식 운영과 무관 |
| `ob_scalp` | `ObScalpStrategy` / `ob_scalp_v1` `ob_scalp.py:150` | 호가 불균형 0.55 · 스프레드 2.0 · 깊이 100 · BE 4.0. 운영 배선 없음 |

### D. 미머지 (1)

| 전략 | 위치 | 상태 |
|---|---|---|
| `ai_swing` | `ai_swing_v1` — 워크트리 `feat/ai-swing-dante-bridge`, **PR #215** | 단테 스캔∩예측 교집합 → `Swing38Strategy` 진입 상속. **4중 default-OFF**. main 미반영 |

### E. 전략이 아닌 것 (집계에 섞이니 구분할 것)

| 값 | 정체 | 위치 |
|---|---|---|
| `sr_flip` · `distribution` · `odori_cross` · `saucer_third_zone` · `accumulation_candle` | 단테 **필터 함수** (진입 전략 아님) | `strategy/dante_filters.py` |
| `screener_v1` | 스캐너 기본 태그. 운영 호출처 0건 | `scanner/stock_screener.py:41` |
| `legacy_scalping_consensus` | legacy 어댑터가 찍는 ID | `legacy_scalping/_adapter.py:46` |
| `force_close` · `manual` | 강제청산 / 수동주문 **귀속 태그** | `orchestrator.py:201` · `run_telegram_bot.py:246` |

---

## 2. 청산 정책

### 2-1. `HoldingEvaluator` 프로파일 (운영 2차 방어선)

`backend/core/risk/holding_evaluator.py` `STRATEGY_EXIT_PROFILES` — **실측 덤프**:

| 전략 | SL | TP | 부분익절 | 트레일링 | BE | 보유일 |
|---|---:|---:|---|---|---:|---|
| `f_zone` | -4.0% | +5.0% | +3.0% × 0.5 | +3.5% → 1.0% | +2.5% | — |
| `sf_zone` | -4.0% | +7.0% | +3.0% × 0.33 | +3.0% → 1.5% | +2.0% | — |
| `gold_zone` | -4.0% | +4.0% | +2.0% × 0.5 | +3.0% → 1.0% | +2.5% | — |
| `swing_38` | **-15.0%** | +50.0% | +20.0% × 0.5 | +20.0% → 5.0% | +10.0% | **3~20일** |
| `closing_bet` | -5.0% | +4.5% | +2.7% × 0.5 | +3.5% → 1.0% | +2.0% | 1~3일 |
| `ai_swing` (PR #215) | -5.0% | +50.0% | +20.0% × 0.5 | +10.0% → 3.0% | +10.0% | 3~20일 |
| **base** (`data/policy.json`) | **-2.0%** | +5.0% | — | — | — | — |

- `resolve_policy()` 가 `_v1`/`_v2` 접미사를 제거해 매칭한다 → `swing_38_v1` → `swing_38`.
- env 오버라이드: `BARRO_SWING38_SL_PCT`, `BARRO_AI_SWING_SL_PCT`(PR #215). 나머지는 리터럴.

### 2-2. ⚠️ SL 은 2계층이다

전략 자체 `ExitPlan`(1차, ExitEngine 분봉 경로)과 프로파일(2차, HoldingEvaluator 경로)의 값이 **다르다**.

| 전략 | ExitPlan SL (1차) | 프로파일 SL (2차) | 방향 |
|---|---:|---:|---|
| `f_zone` | -2.0% | -4.0% | 1차가 타이트 |
| `sf_zone` | -1.5% | -4.0% | 1차가 타이트 |
| `gold_zone` | -1.5% | -4.0% | 1차가 타이트 |
| `closing_bet` | -3.0% | -5.0% | 1차가 타이트 |

`f_zone.py:362` 주석이 이를 명시한다 — *"ExitEngine 1차 방어선 SL. HoldingEvaluator(-4%) 가 2차 fallback"*.
**단일 수치로 "f_zone SL 은 -4%" 라고 쓰면 오독이다.** 반드시 계층을 붙인다.

### 2-3. 트레이더 자체 청산 (프로파일 미등록 전략)

`supertrend` / `limit_up_chase` 는 프로파일이 없고 **트레이더 내부**에서 청산한다.
`run_cycle` 이 **청산을 먼저 전부 수행한 뒤** 진입을 평가하는 구조다
(`supertrend_auto_trader.py:299` · `limit_up_chase_trader.py:68`). 하드손절 · ATR 트레일 ·
러너 · 이월갭스탑을 자체 보유한다. → **이 트레이더가 죽으면 그 청산 로직이 통째로 사라진다** (§3-1).

---

## 3. 🔴 등록 갭

전략은 6개 지점에 등록돼야 완전체다. **실측 매트릭스**:

| 전략 | 시뮬 분기 | 시뮬 기본 | 청산 프로파일 | 국면 가중 | 데몬 기본 | signal Literal |
|---|:-:|:-:|:-:|:-:|:-:|:-:|
| `f_zone` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `sf_zone` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `gold_zone` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `swing_38` | ✔ | ✔ | ✔ | ✔ | ✔ | ✔ |
| `closing_bet` | ✔ | — | ✔ | **—** | — | ✔ |
| `scalping_consensus` | ✔ | ✔ | **—** | **—** | — | — |
| `supertrend` | — | — | **—** | — | — | ✔ |
| `limit_up_chase` | — | — | **—** | — | — | — |

### 3-1. `supertrend` · `limit_up_chase` — 청산 프로파일 없음 ★가장 중요

자체 트레이더가 도는 동안은 문제없다. 그러나 **트레이더를 끄고 데몬/`evaluate_holdings` 가
대신 청산하면 `policy.json` base(SL **-2.0%** / TP +5.0%)가 적용된다.**
하드손절 -6% · ATR 트레일 · 러너가 조용히 사라지고 **-2% 에서 털린다.**

→ 이것이 `daytrading-pause-to-ai-swing.md` §1-2 가 `SUPERTREND_AUTO_ENABLED=0` 을
금지하는 이유다. 정지는 **진입 컷오프**로 한다.

### 3-2. `scalping_consensus` — 시뮬 기본에 있는데 프로파일·국면가중 둘 다 없음

발화하면 ① base -2% 로 청산되고 ② `REGIME_WEIGHTS.get(s, 1.0)` = 1.0 이라 **BEARISH 국면
필터를 우회**한다(`intraday_buy_daemon` 의 하락장 가중치 < 1.0 배제 로직을 통과).
현재는 체결 0건이라 실피해가 없지만, **잠재 지뢰**다.

### 3-3. `closing_bet` — 국면가중 없음

위와 같은 이유로 하락장 필터를 우회한다. 단 종베는 자체 데몬이 운영하므로 데몬 경로의
국면 필터에는 애초에 안 걸린다 — 영향 범위가 3-2 보다 좁다.

---

## 4. 실행 주체 · 활성 상태

| 주체 | 성격 | 담당 전략 | 재기동 필요? |
|---|---|---|---|
| `scripts/run_telegram_bot.py` | **상시 프로세스** | supertrend, limit_up_chase | **예** (기동 시 env 1회 읽음) |
| `scripts/intraday_buy_daemon.py` | 상시(cron 기동) | zone 4종 매수 + **전 보유분 매도** + supertrend 대행 + EOD 청산 | 예 |
| `scripts/evaluate_holdings.py` | **cron 1회성** | 매도 전용(전 보유분) | 아니오 (매 실행이 새 프로세스) |
| `scripts/simulate_leaders.py` | cron 1회성 | 매수만 | 아니오 |
| `scripts/closing_bet_alert_daemon.py` | 상시 | closing_bet | 예 |

### 개발 머신 실측 (2026-07-31)

```
crontab -l                          → no crontab for beye
ps aux | grep run_telegram_bot      → 0건
ps aux | grep intraday_buy_daemon   → 0건
.env.local 전략 스위치 키           → 0건 (전부 미설정)
```

**이 머신에서는 아무 전략도 돌지 않는다.** 운영 머신은 별도이며
`data/kiwoom_trade_history_1y.manifest.json` 기준 **계정 `beye82` / 경로 `~/Workspace/BarroAiTrade`**,
환경 **mock**(`mockapi.kiwoom.com`)이다. 운영 머신의 실제 env 값은 이 문서로 확인할 수 없다(§8).

---

## 5. 실측 성과 — ★전제를 반드시 함께 읽을 것

| 전략 | 백테스트 | 실체결(mock) | 표본 |
|---|---|---|---|
| `supertrend` | 승률 35.4% / PF 1.762 / 손익비 3.21 | 승률 34.1% | 1,042거래 / 44건 |
| `f_zone` | OOS +1.90~3.08% | 승률 38.5% | / 13건 |
| `gold_zone` | OOS 승률 35~42% | 승률 33.3% | / 21건 |
| `sf_zone` | **표본 미달 — 6 seed 전부 FAIL**(17~21거래 < 기준 30) | 승률 33.3% | / 6건 |
| `swing_38` | OOS +2.36~2.78% | 주문 0건 | — |
| `closing_bet` | 왕복 0.90% 비용에서 사실상 브레이크이븐 | — | — |
| `limit_up_chase` | **성과지표 없음** | 41건 | — |
| `scalping_consensus` | 시뮬 64 runs **체결 0** | 0건 | — |
| `ai_swing` (PR #215) | 27/27 PASS · 3-seed 평균 +2.172% · 승률 36~39.5% | 미실행 | 120종목×3seed |

**계좌 전체 실측** (`data/kiwoom_trade_history_1y.db`, mock, 2026-06-05~07-10, n=162):
승률 **29.0%** / 평균익 **+3.74%** / 평균손 **-3.82%** / **손익비 0.98** / 자본가중 **-1.72%**.
비용 환입 전(gross)은 승률 37.0% — **수수료·세가 승률 8%p 를 먹는다.**

### 5-1. ★백테스트 수치는 대부분 전략 자신의 청산이 아니다

`intraday_simulator.py` `_exit_plan_for_strategy()` 는 **어떤 전략의 `exit_plan()` 도 호출하지 않는다.**
분기는 둘뿐이다:
- `sf_zone`(및 `f_zone` + `f_zone_atr=True`) → ATR 기반 동적 TP/SL
- **그 외 전부**(f_zone 기본 · gold_zone · **swing_38** · scalping_consensus) → 고정
  **TP +3/+5/+7% · SL -1.5% · BE +1%**, 그리고 `min/max_hold_days` 를 **설정하지 않는다**.

**영향이 가장 큰 곳이 `swing_38` 이다.** 라이브는 TP +20/+50% · SL -15% · min_hold 3 · max_hold 20 인데,
OOS 리포트 수치는 **SL -1.5% · 보유기간 게이트 없음**으로 산출됐다.
→ **"swing_38 OOS +2.36%" 는 SL -15% 의 근거가 아니다.**

⚠️ 게다가 `2026-06-22-swing38-oos-validation.report.md:15` 와 `intraday_simulator.py:236` 주석은
"exit_plan 보유기간 게이트 작동"이라고 **반대로 기술**한다(오기술, 미정정).

**PR #215 의 `ai_swing` 만 이 문제가 해소돼 있고 main 은 그대로다.**

부수 결함: `backend/core/strategy/` 전체에 `trail_stages` 설정이 **0건**이라
ExitEngine 분봉 경로에서는 모든 전략이 **트레일링 없이** 돌고 있었다.
HoldingEvaluator 경로에만 트레일링이 별도 구현돼 있어 **두 청산 경로가 어긋난다.**
(PR #215 가 ai_swing 에 한해 수정)

### 5-2. 실체결은 전부 mock 계좌다

DB `schema_meta.environment = mock`. 비용 상수(`trading_costs.py`, 왕복 **0.886%**)도
mock 체결에서 역산한 값이라 **실거래 요율보다 높다.** 보수적이라는 뜻이지만,
`closing_bet` 처럼 "왕복 0.90% 에서 브레이크이븐" 결론이 난 전략은 **요율이 내려가면 결론이 뒤집힌다.**
이 상수를 실거래 요율로 바꿔 재측정하지 않은 채 전략을 채택/폐기하면 안 된다.

### 5-3. `supertrend` 는 공식 OOS 관문을 통과한 적이 없다

```python
# scripts/_oos_validation.py:33
STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "closing_bet"]
```

`swing_38` 은 여기 없지만 전용 오버라이드 스크립트가 있다
(`scripts/oos_validation_swing38.py:35` — `oos.STRATEGIES = [SID]`).
`supertrend` 는 **관문 목록에도 없고 전용 스크립트도 없다.**
**현재 유일한 라이브 전략인데 그렇다.** (`limit_up_chase` 도 동일)

### 5-4. 약세장 OOS 는 어느 전략에도 없다

여러 리포트가 공통으로 자인한다 — 평가 구간(warmup 후 잔여)이 불장이라 진정한 베어 검증 불가.

### 5-5. 승률 80% KPI 는 달성 불가 판정이 나 있다

`2026-05-30-winrate-optimization.md` 와 `2026-05-28-phase-d-grid-summary.md` 가
**독립적으로** 같은 결론에 도달했다 — 기대값 > 0 조건에서 **최대 승률 ≈ 42%**.
`ai_swing` 리포트도 동일(승률이 아니라 **손익비**로 버는 구조).
→ KPI 를 손익비 / 기대수익률 기준으로 재정의할지 판단 필요.

### 5-6. 리포트 간 모순 (미해소)

- **`gold_zone` 승률**: OOS 35~42% vs 실체결 54.8% vs 재집계 33.3% vs 시뮬누적 47.9% — 네 값이 전부 다르다.
- **`2026-05-29-grid-backtest.md`** 는 같은 문서 안에서 결론이 두 번 뒤집힌다(§1~3 → §6 → §7).
  **§7 이 최종**이다. §1~3 표를 인용하면 안 된다.
- **`docs/04-report/daily/2026-07-08.md` 시뮬 누적표**는 표본기간·유니버스·비용·청산 전제가
  문서에 없다. **승률만 인용 가능**하고 `total_pnl` 은 금액이라 사용 불가.

---

## 6. 전략 귀속 데이터의 한계

| 데이터 | strategy 컬럼 | 기간 | 비고 |
|---|:-:|---|---|
| `data/kiwoom_trade_history_1y.db` | **없음** | 2026-06-05~07-10 | 브로커 원천 — 직접 귀속 불가 |
| `data/order_audit.csv` | 있음 | 2026-05-12~06-19 | 6/19 에 멈춤. 공란 776행 중 359건이 `BLOCKED|sell` |
| `data/fill_audit.csv` | **없음** | 2026-06-11~06-19 | date+symbol 조인 필요 |

→ 전략별 실체결 성과는 **`order_audit` 와 `realized_pnl` 을 (일자, 종목)으로 조인**해야 나온다.
이 머신 데이터로 매칭되는 건 **2026-06-05~06-18 의 84건**뿐이다.
`swing_38` · `closing_bet` · `ai_swing` · `scalping_consensus` 는 이 머신에서 **주문 0건** → 귀속 불가.

---

## 7. 재현 명령 (읽기 전용)

```bash
cd /Users/beye/workspace/BarroAiTrade
VENV=/Users/beye/workspace/BarroAiTrade/venv/bin/python

# 전략 클래스 · ID 전수
grep -rn 'STRATEGY_ID = "' --include="*.py" backend scripts | grep -v "/tests/"

# 청산 프로파일 실측 덤프
$VENV -c "
from backend.core.risk.holding_evaluator import STRATEGY_EXIT_PROFILES as P
for k,v in P.items(): print(k, dict(v))"

# 등록 갭 매트릭스
grep -n 'sid == \"' backend/core/backtester/intraday_simulator.py     # 시뮬 분기
grep -n 'DEFAULT_STRATEGIES' -A 7 backend/core/backtester/intraday_simulator.py
grep -n 'REGIME_WEIGHTS' -A 22 backend/core/backtester/market_regime.py
grep -n 'DEFAULT_ZONE_STRATEGIES' scripts/intraday_buy_daemon.py
grep -n 'signal_type: Literal' backend/models/signal.py

# 계좌 전체 실측 (mock)
sqlite3 -readonly data/kiwoom_trade_history_1y.db \
  "SELECT COUNT(*), ROUND(AVG(pnl_rate),2) FROM realized_pnl;"
```

---

## 8. 미검증 (§8 정직성)

1. **운영 머신의 실제 env 값 · crontab** — 개발 머신에서 확인 불가. `--supertrend` 인자 유무,
   `BARRO_DAEMON_STRATEGIES` 실제 값, 청산 cron 생존 여부 전부 미확인.
2. **실거래(real) 계좌 성과 0건** — 이 머신 데이터는 전부 mock.
3. **`backend/legacy_scalping/main.py`(104KB)** 는 module-level side effect 우려로 정독하지 않았다.
   그 안의 `EntrySignal` 생성부 2곳에 추가 `strategy_id` 가 있을 가능성을 배제하지 못한다.
4. **§5 성과 수치는 리포트 인용값**이며 이번에 재실행하지 않았다
   (계좌 전체 실측 n=162 와 등록 갭 매트릭스·청산 프로파일 덤프만 이번에 직접 실측).
5. **§3 갭의 실피해 여부** — `scalping_consensus` 는 체결 0건이라 잠재 위험에 그친다.
   `supertrend` 프로파일 부재의 실피해는 트레이더를 끄기 전까지는 발생하지 않는다.

---

## 부록. 6개 등록 지점

| # | 지점 | 위치 | 빠지면 |
|---|---|---|---|
| ① | 시뮬 분기 | `intraday_simulator.py` `_build_strategies` | 데몬이 호출 시 `raise ValueError` 크래시 |
| ② | 시뮬 기본 | `intraday_simulator.py` `DEFAULT_STRATEGIES` | 명시 지정해야만 백테스트됨 |
| ③ | 청산 프로파일 | `holding_evaluator.py` `STRATEGY_EXIT_PROFILES` | **base SL -2% 적용** |
| ④ | 국면 가중 | `market_regime.py` `REGIME_WEIGHTS` | `.get(s,1.0)`=1.0 → **BEARISH 필터 우회** |
| ⑤ | 데몬 기본 | `intraday_buy_daemon.py` `DEFAULT_ZONE_STRATEGIES` | 데몬이 스캔하지 않음 |
| ⑥ | 신호 타입 | `models/signal.py` `signal_type: Literal` | pydantic 검증 실패 |
