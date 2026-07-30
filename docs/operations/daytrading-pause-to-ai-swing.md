# 단타 일시중지 → ai_swing 관측 전환 절차서

**작성일**: 2026-07-31
**대상**: 운영 머신에서 supertrend / 상따(limit_up_chase) / zone 전략(f_zone·sf_zone·gold_zone)을
**일시 중지**하고 `ai_swing` 관측(shadow)으로 전환하려는 경우.
**전제**: 이 문서의 모든 코드 인용은 워크트리
`/Users/beye/workspace/BarroAiTrade/.claude/worktrees/ai-swing` (브랜치 `feat/ai-swing-dante-bridge`)
에서 **직접 파일을 열어 실측**한 것이다. 파일 해시는 §7 에 적었다.

> **결론 먼저**: 단타를 세우는 올바른 방법은 **"진입만 차단, 청산은 유지"** 다.
> `LIVE_TRADING_ENABLED=false` 와 `SUPERTREND_AUTO_ENABLED=0` 은 **정지 방법이 아니다** — 전자는
> 손절까지 막고, 후자는 정지가 아니라 청산 담당자를 조용히 바꾼다. 근거는 §1.

---

## 1. 🔴 절대 쓰면 안 되는 정지 방법 2가지

### 1-1. `LIVE_TRADING_ENABLED=false` — **매도(손절·청산)까지 전면 차단된다**

`LiveOrderGate._preflight()` 의 env 플래그 검사에는 **`side` 분기가 없다.**

```python
# backend/core/risk/live_order_gate.py:158-166
def _preflight(self, side: OrderSide, daily_pnl_pct: Decimal) -> None:
    # 1) ENV flag 강제 (실전 host 의 안전망)
    if self._policy.require_env_flag and not self._executor._dry_run:   # :160  ← side 조건 없음
        flag = os.environ.get(self._policy.env_flag_name, "").lower()   # :161
        if flag not in {"1", "true", "yes", "on"}:                      # :162
            raise TradingDisabled(...)                                  # :163
```

**대조군 — 같은 함수 안의 다른 두 검사에는 `side == OrderSide.BUY` 가 명시돼 있다:**

| 검사 | 라인 | 조건 | 주석 |
|---|---|---|---|
| ENV 플래그 | `live_order_gate.py:160` | *(side 조건 없음)* | — |
| 일일 손실 한도 | `live_order_gate.py:169` | `if side == OrderSide.BUY and ...` | `:168` "매도는 원래도 손절 가능해야 한다" |
| 일일 매수 한도 | `live_order_gate.py:201` | `if side == OrderSide.BUY and ...` | `:197` "매수만 차단 (매도는 손절 가능해야)" |

즉 후자 두 개는 의도적으로 매수 한정인데, **ENV 플래그만 양방향**이다.
`place_sell()`(`:220-229`)도 `_gated(OrderSide.SELL, ...)` → `_gated` 안의
`self._preflight(side, daily_pnl_pct)`(`:275`)를 똑같이 통과하므로 **매도가 `TradingDisabled` 로 차단**된다.

> **결과**: 보유분이 손절 없이 묶인다. 2026-05-29 swing_38 비활성 사고
> (장부 동기화 누락 + 보유분 자동 청산 부재 → 잔여 4종목 사용자 수동 청산, 평균 -0.985%)와 **동형**이다.

### 1-2. `SUPERTREND_AUTO_ENABLED=0` — **정지가 아니라 "담당자 교체"다**

**(a) `run_telegram_bot` 프로세스 안에서 supertrend 고유 청산이 통째로 사라진다.**

> ⚠️ **(a) 의 범위는 `run_telegram_bot` 프로세스 한정이다.** 아래 (c) 처럼 데몬 crontab 에
> `--supertrend` 가 남아 있으면 데몬이 트레이더를 인수해 같은 청산 로직이 **데몬 쪽에서
> 계속 살아 있다**. (a) 만 읽고 crontab 을 확인하지 않으면 "보유분 완전 무방어"로 오판한다.
> 실제로 무방어인지 판정하려면 **(a)·(c) 를 함께** 봐야 한다.

```python
# scripts/run_telegram_bot.py:584-585
    if not _env_truthy("SUPERTREND_AUTO_ENABLED"):
        return None
# scripts/run_telegram_bot.py:809-810 / :833-834
    auto_trader = _build_supertrend_auto_trader(notifier)     # :809  → None
    if auto_trader is not None:                               # :810  → False
        ...
        if auto_trader is not None:                           # :833
            tasks.append(asyncio.create_task(auto_trader.run_forever(), ...))   # :834  ← 태스크 미생성
```

트레이더 객체가 `None` 이 되어 `run_forever` 태스크가 만들어지지 않는다. 그러면
**하드손절 / ATR 트레일 / 러너 / 이월갭스탑**(`supertrend_auto_trader.py:345-364` 의
`_trail_hit` · `_hard_stop_hit` · `_carry_gap_stop_hit` · `_runner_should_exit`)이 전부 사라진다.

**(b) 대신 청산하는 데몬은 supertrend 프로파일을 갖고 있지 않다.**

- supertrend 포지션의 장부 전략명: `backend/core/supertrend_auto_trader.py:45`
  → `_STRATEGY_ID = "supertrend"`
- `STRATEGY_EXIT_PROFILES`(`backend/core/risk/holding_evaluator.py:106-196`)의 키는
  `f_zone`(:107) · `sf_zone`(:117) · `gold_zone`(:127) · `swing_38`(:137) · `closing_bet`(:160) ·
  `ai_swing`(:184) — **`supertrend` 항목이 없다.**
- `resolve_policy()`(`holding_evaluator.py:199-205`)는 매칭 실패 시 `return base`(`:205`).

→ supertrend 보유분이 `data/policy.json` base 로 평가된다.
개발 머신 `/Users/beye/workspace/BarroAiTrade/data/policy.json` 실측값:
`"stop_loss_pct": -2.0`, `"take_profit_pct": 5.0`.
**청산 기준이 아무 경고 없이 -2% / +5% 로 바뀐다.** (운영 머신 policy.json 값은 미검증 — §6 참조)

**(c) ★가장 큰 함정 — env 만 0 으로 두면 데몬이 supertrend 를 "인수"한다.**

```python
# scripts/intraday_buy_daemon.py:141-149
def _supertrend_yield_to_bot(want_supertrend: bool, env: dict | None = None) -> bool:
    """run_telegram_bot(SupertrendAutoTrader)이 슈퍼트렌드 담당 중이면 데몬은 양보(False)."""
    env = env if env is not None else os.environ
    truthy = (env.get("SUPERTREND_AUTO_ENABLED", "") or "").strip().lower() in {"1","true","yes","on"}  # :148
    return False if (want_supertrend and truthy) else want_supertrend                                    # :149
```

`SUPERTREND_AUTO_ENABLED` 가 **truthy 가 아닐 때** 이 함수는 `want_supertrend` 를 그대로 돌려준다
(= 데몬이 supertrend 를 맡는다). 호출부는 `scripts/intraday_buy_daemon.py:2177`
(`args.supertrend = _supertrend_yield_to_bot(args.supertrend)`), 양보 시 가드 로그는 `:2179-2180`.

→ crontab 에 `--supertrend` 가 남아 있으면 `SUPERTREND_AUTO_ENABLED=0` 은 **정지가 아니라 담당자 교체**다.
데몬 쪽 트레이더는 `_get_supertrend_trader()`(`intraday_buy_daemon.py:1677`)로 별도 구성된다.
**정지하려면 crontab 의 `--supertrend` 제거가 반드시 동반돼야 한다.**

---

## 2. ✅ 권장 방법 — "진입만 차단, 청산 유지"

세 트레이더 모두 `run_cycle` 이 **청산을 먼저 전부 수행한 뒤** 진입 직전에만 return 하는 구조여서,
진입 게이트 시각만 뒤로/앞으로 밀면 청산 권한을 그대로 둔 채 진입만 0 으로 만들 수 있다.

| 대상 | 설정 | 근거 (직접 확인한 파일:라인) |
|---|---|---|
| supertrend | `SUPERTREND_AUTO_ENTRY_CUTOFF=00:01` | `backend/core/supertrend_auto_trader.py:299` 주석 "1 사이클 (청산 먼저, 그 다음 진입)" · `:300-301` `run_cycle` docstring "청산 평가 → 진입 평가 1회" · 청산 루프 `:326-412` · 진입 컷오프 `:421-425` (`if self._entry_cutoff_passed(): return result`) · 구현 `:637-653`, 비교식 `:651` `return now >= dtime(int(hh), int(mm))` · docstring `:641` "빈 문자열이면 비활성. **청산은 시각 무관.**" · env 배선 `scripts/run_telegram_bot.py:658` |
| 상따 | `LIMIT_UP_ENTRY_START=23:59` | `backend/core/limit_up_chase_trader.py:68` 주석 "1 사이클 (청산 먼저, 그 다음 상따 진입)" · 청산 `:82` `await self._run_exit_cycle(...)` · 진입창 `:89-90` `if not self._entry_window_open(): return result` · `_entry_window_open` `:245-260` 이 먼저 부모 `_entry_time_open()` 호출 `:250` · 부모 구현 `backend/core/supertrend_auto_trader.py:620-635`, 비교식 `:633` `return now >= dtime(int(hh), int(mm))` · env 배선 `scripts/run_telegram_bot.py:734` |
| zone 전략 | `BARRO_DAEMON_STRATEGIES=off` | `scripts/intraday_buy_daemon.py:128-138` `_parse_strategies`, `:136-137` `if r in {"", "none", "off"}: return []` · env 오버라이드 `:2173-2175` · `_scan_and_buy` 즉시 return `:1117-1120` (`if not zone_strategies: return 0`) · `_evaluate_and_sell` 는 루프에서 계속 호출 `:2055` (매수 스캔은 `:2064`) |

`_entry_cutoff_passed()` 는 `now >= cutoff` 이므로 `00:01` 로 두면 정규장(09:00~15:30) 내내 항상 True
→ **진입만** 차단된다. `_entry_time_open()` 은 `now >= start` 이므로 상따는 `23:59` 로 두면 항상 False
→ 진입창이 열리지 않는다. 두 경우 모두 청산 코드는 그보다 **위**에서 이미 실행된 뒤다.

### 2-1. 적용 예 (운영 머신 `.env.local`)

```bash
# ★ 실행 전 §3 의 "원값 백업"을 먼저 하라 ★
SUPERTREND_AUTO_ENTRY_CUTOFF=00:01     # supertrend 진입만 차단 (청산 유지)
LIMIT_UP_ENTRY_START=23:59             # 상따 진입만 차단 (청산 유지)
BARRO_DAEMON_STRATEGIES=off            # zone 전략 스캔만 차단 (매도평가 유지)
# LIVE_TRADING_ENABLED 은 건드리지 않는다 (§1-1)
# SUPERTREND_AUTO_ENABLED 도 건드리지 않는다 (§1-2)
```

⚠️ `SUPERTREND_AUTO_ENTRY_CUTOFF` 는 **두 프로세스가 같이 읽는다** —
`scripts/run_telegram_bot.py:658` (상시 봇) 와 `scripts/intraday_buy_daemon.py:1750` (데몬).
어느 쪽이 supertrend 를 맡고 있든 같은 값이 적용되므로 이 방법은 담당자와 무관하게 동작한다.

---

## 3. 정지 **전** 체크리스트

1. **현재 보유 종목·전략 확인** — 어떤 전략의 포지션이 남아 있는지 먼저 본다.
   ```bash
   # 운영 머신에서. 실주문 없음(조회/평가만).
   ./.venv/bin/python scripts/evaluate_holdings.py         # 기본 DRY_RUN (scripts/evaluate_holdings.py:350)
   cat data/active_positions.json                          # 장부의 strategy 필드 확인
   ```
2. **원 env 값을 먼저 기록한다.** 되돌릴 값을 모르면 재개할 수 없다.
   ```bash
   grep -E '^(SUPERTREND_AUTO_ENTRY_CUTOFF|LIMIT_UP_ENTRY_START|BARRO_DAEMON_STRATEGIES|SUPERTREND_AUTO_ENABLED|LIMIT_UP_CHASE_ENABLED|LIVE_TRADING_ENABLED)=' .env.local \
     | tee ~/pause-backup-$(date +%F).env
   crontab -l > ~/crontab-backup-$(date +%F).txt          # --supertrend / --strategies 인자 원본 보존
   ```
   미설정 키는 grep 에 안 잡힌다 — **그 경우 "미설정이었다"고 백업 파일에 손으로 적어 둔다.**
   코드 기본값: `SUPERTREND_AUTO_ENTRY_CUTOFF=14:30`(`run_telegram_bot.py:658`),
   `LIMIT_UP_ENTRY_START=09:05`(`:734`), `LIMIT_UP_ENTRY_END=14:00`(`:735`),
   `SUPERTREND_AUTO_ENTRY_START=09:30`(`:622`).
3. **청산 경로가 최소 하나 살아 있는지 확인한다.** 둘 다 죽어 있으면 진입만 막아도 보유분이 방치된다.
   - 경로 A: `scripts/intraday_buy_daemon.py` `_evaluate_and_sell`
     (`:344`, 루프 호출 `:2055`, 09:01 이후 `SELL_START` 게이트 `:70`·`:347-348`)
     — `BARRO_DAEMON_STRATEGIES=off` 로도 **이 경로는 죽지 않는다**(매수 스캔만 죽는다).
   - 경로 B: 매시간 `scripts/evaluate_holdings.py --auto-sell` cron
     (실매도는 `--no-dry-run` + `LIVE_TRADING_ENABLED` 필요 — `evaluate_holdings.py:347-351`)
   ```bash
   crontab -l | grep -E 'intraday_buy_daemon|evaluate_holdings'
   ps aux | grep -E 'intraday_buy_daemon|run_telegram_bot' | grep -v grep
   ```
4. supertrend 를 상시 봇이 맡고 있는지, 데몬이 맡고 있는지 확인한다(§1-2c).
   `crontab -l | grep -- --supertrend` 와 `.env.local` 의 `SUPERTREND_AUTO_ENABLED` 를 같이 본다.

---

## 4. 되돌리기(재개) 절차

**전제: §3-2 의 원값 백업이 있어야 한다.** 없으면 재개하지 말고 먼저 원값을 확정한다.

1. `.env.local` 의 세 키를 백업 파일의 원값으로 되돌린다(원래 미설정이었다면 **줄을 지운다** —
   빈 값으로 두면 `SUPERTREND_AUTO_ENTRY_CUTOFF=""` 는 컷오프 비활성(`supertrend_auto_trader.py:643-645`),
   `BARRO_DAEMON_STRATEGIES=""` 는 zone 전략 비활성(`intraday_buy_daemon.py:136-137`)으로
   **기본값과 다르게** 해석된다).
2. crontab 을 백업본으로 되돌린다(`--supertrend` / `--strategies` 인자 포함).
3. 재기동(§5) 후 로그로 실제 적용을 확인한다.
   - supertrend ON: `run_telegram_bot.py:812-813` 이 `⚡ 슈퍼트렌드 자동매매 ON (...)` 출력
   - 상따 ON: `:827-829` 가 `🚀 상따(상한가 따라잡기) ON (...)` 출력
   - zone 전략: `:2181-2184` 가 `[전략] 일반 매수 스캔: f_zone, ...` 또는
     `[전략] 일반 매수 전략 비활성 — 슈퍼트렌드 단독 운영.` 출력
   - 담당자 교체 여부: `[GUARD] SUPERTREND_AUTO_ENABLED 감지 — ...`(`:2179-2180`) 출력 여부

---

## 5. 반영 시점 (재기동이 필요한 경로 / 필요 없는 경로)

| 경로 | env 읽는 시점 | 재기동 |
|---|---|---|
| supertrend·상따 (`run_telegram_bot.py` 상시 프로세스) | **프로세스 기동 시 1회** — `_build_supertrend_auto_trader()`(`:577-588`)·`_build_limit_up_chase_trader()`(`:689-695`)가 기동 시 `os.environ` 을 읽어 config 를 만든다 | **필요** |
| cron 경로 (`evaluate_holdings` 등) | 매 실행이 새 프로세스 | 불필요 |
| `intraday_buy_daemon` (상시 데몬으로 도는 경우) | `main()` 진입 시 1회 (`:2173-2177`) | **필요** |

RUNBOOK 근거 인용 (`RUNBOOK.md:258-261`):

> - **cron 경로**(09:30 simulate_leaders / 매시간 evaluate_holdings)는 pull 만으로
>   다음 실행부터 최신 코드 적용 — **별도 재기동 불필요**.
> - **supertrend 를 상시 프로세스**(run_telegram_bot, `SUPERTREND_AUTO_ENABLED=1`)로
>   돌리는 경우, 그 프로세스만 재기동해야 새 코드가 적용된다.

재기동은 **포트/PID 기준**으로 한다(CLAUDE.md §4 D5). `pkill -f` 는 프로세스명 불일치로 구프로세스를
남길 수 있다. 봇은 포트를 열지 않으므로 `ps` 로 PID 를 특정해 종료한 뒤 재기동한다.

---

## 6. (관찰) 이 개발 머신의 실측 상태 — 운영 머신은 미검증

2026-07-31, `/Users/beye/workspace/BarroAiTrade` 개발 머신에서 실측:

| 항목 | 실측 결과 |
|---|---|
| `crontab -l` | `crontab: no crontab for beye` (**crontab 없음**) |
| 관련 env (`SUPERTREND_*`/`LIMIT_UP_*`/`BARRO_DAEMON_*`/`LIVE_TRADING_*`/`BARRO_AI_*`) | 셸 환경에 **0건** |
| 관련 프로세스 (`run_telegram_bot`/`intraday_buy_daemon`/`evaluate_holdings`) | **0건** |
| `.env.local` 키 목록 | `KIWOOM_*`, `TELEGRAM_*`, `GRAFANA_*`, `LOG_*`, `TRADING_MODE`, `TRADING_MARKET` 뿐 — 이 문서가 다루는 플래그는 **하나도 없다** |

→ **이 문서의 절차는 개발 머신에서 실행할 대상이 없다.** 전부 운영 머신용이다.

운영 머신 식별 (`data/kiwoom_trade_history_1y.manifest.json` 실측):

- `path`: `/Users/beye82/Workspace/BarroAiTrade/data/kiwoom_trade_history_1y.db` → 계정 **beye82**, 경로 **~/Workspace/BarroAiTrade**
- `expected_environment` / `metadata.environment`: **`mock`** (모의투자)
- mock 호스트는 `https://mockapi.kiwoom.com` (`KIWOOM_BASE_URL` 기본값 —
  `backend/core/themes/market_row_store.py:184` 등)
- `metadata.last_success_at_utc`: `2026-07-12T14:28:58+00:00`

⚠️ `RUNBOOK.md:253` 은 운영 머신 경로를 `cd /Users/beye/BarroAiTrade` 로 적고 있어 manifest 의
`/Users/beye82/Workspace/BarroAiTrade` 와 **불일치**한다. 어느 쪽이 현재 운영 경로인지는 **미검증** —
작업 전 운영 머신에서 직접 확인할 것.

**실제 env 값·crontab 내용·policy.json 값은 전부 운영 머신에서 확인해야 한다 (미검증).**

---

## 7. 인용 기준 파일 (실측 시점 고정)

| 파일 | md5 |
|---|---|
| `backend/core/risk/live_order_gate.py` | `e7b37e9876c680aac2b6ca917ccbe77c` |
| `backend/core/risk/holding_evaluator.py` | `616516671fe81d3ddaafe1db36755acd` |
| `backend/core/supertrend_auto_trader.py` | `47ce3b2d9d0c27bae944f7d02dee26b0` |
| `backend/core/limit_up_chase_trader.py` | `6971cb13347c44f6f938a889794de143` |
| `scripts/run_telegram_bot.py` | `f6201b4cb80235bfe323063e76236ce3` |
| `scripts/intraday_buy_daemon.py` | `6247180b836b8451b322619d82a8c7d4` |

⚠️ `scripts/intraday_buy_daemon.py` 는 이번 라운드에 편집 중인 파일이다(ai_swing 진입 훅 추가).
**라인 번호가 이동할 수 있으므로** 위 md5 와 다르면 라인 대신 함수명
(`_parse_strategies` · `_supertrend_yield_to_bot` · `_scan_and_buy` · `_evaluate_and_sell`)으로 찾을 것.

---

## 8. 관련 문서

- `docs/operations/ai-swing-runbook.md` — ai_swing 전략 자체의 플래그·활성/비활성 순서
- `docs/operations/strategy-restart-toggles.md`
- `RUNBOOK.md` §배포/재기동
- CLAUDE.md §2 (실거래 안전 경계) — 이 문서의 어떤 절차도 §2 S1 경로를 수정하지 않는다
