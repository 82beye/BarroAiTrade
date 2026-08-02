# 단타 일시중지 → ai_swing 관측 전환 절차서

**작성일**: 2026-07-31 · **최종 갱신**: 2026-08-02
**대상**: 운영 머신에서 supertrend / 상따(limit_up_chase) / zone 전략(f_zone·sf_zone·gold_zone)을
**일시 중지**하고 `ai_swing` 관측(shadow)으로 전환하려는 경우.
**전제**: 조작 전에 [[ai-swing-runbook]] §1에서 운영 머신의 실제 checkout·브랜치·SHA·clean
상태·절대 Python·KST를 확인한다. 아래 근거는 변하기 쉬운 라인 번호 대신 함수명을 기준으로 한다.

> **결론 먼저**: 단타를 세우는 올바른 방법은 **"진입만 차단, 청산은 유지"** 다.
> `LIVE_TRADING_ENABLED=false` 와 `SUPERTREND_AUTO_ENABLED=0` 은 **정지 방법이 아니다** — 전자는
> 손절까지 막고, 후자는 정지가 아니라 청산 담당자를 조용히 바꾼다. 근거는 §1.

---

## 1. 🔴 절대 쓰면 안 되는 정지 방법 2가지

### 1-1. `LIVE_TRADING_ENABLED=false` — **매도(손절·청산)까지 전면 차단된다**

`LiveOrderGate._preflight()` 의 env 플래그 검사에는 **`side` 분기가 없다.**

```python
# backend/core/risk/live_order_gate.py · LiveOrderGate._preflight
def _preflight(self, side: OrderSide, daily_pnl_pct: Decimal) -> None:
    # 1) ENV flag 강제 (실전 host 의 안전망)
    if self._policy.require_env_flag and not self._executor._dry_run:  # side 조건 없음
        flag = os.environ.get(self._policy.env_flag_name, "").lower()
        if flag not in {"1", "true", "yes", "on"}:
            raise TradingDisabled(...)
```

**대조군 — 같은 함수 안의 다른 두 검사에는 `side == OrderSide.BUY` 가 명시돼 있다:**

| 검사 | 조건 |
|---|---|
| ENV 플래그 | *(side 조건 없음)* |
| 일일 손실 한도 | `if side == OrderSide.BUY and ...` |
| 일일 매수 한도 | `if side == OrderSide.BUY and ...` |

즉 후자 두 개는 의도적으로 매수 한정인데, **ENV 플래그만 양방향**이다.
`place_sell()`도 `_gated(OrderSide.SELL, ...)` → `_preflight()`를 똑같이 통과하므로
**매도가 `TradingDisabled` 로 차단**된다.

> **결과**: 보유분이 손절 없이 묶인다. 2026-05-29 swing_38 비활성 사고
> (장부 동기화 누락 + 보유분 자동 청산 부재 → 잔여 4종목 사용자 수동 청산, 평균 -0.985%)와 **동형**이다.

### 1-2. `SUPERTREND_AUTO_ENABLED=0` — **정지가 아니라 "담당자 교체"다**

**(a) `run_telegram_bot` 프로세스 안에서 supertrend 고유 청산이 통째로 사라진다.**

> ⚠️ **(a) 의 범위는 `run_telegram_bot` 프로세스 한정이다.** 아래 (c) 처럼 데몬 crontab 에
> `--supertrend` 가 남아 있으면 데몬이 트레이더를 인수해 같은 청산 로직이 **데몬 쪽에서
> 계속 살아 있다**. (a) 만 읽고 crontab 을 확인하지 않으면 "보유분 완전 무방어"로 오판한다.
> 실제로 무방어인지 판정하려면 **(a)·(c) 를 함께** 봐야 한다.

```python
# scripts/run_telegram_bot.py · _build_supertrend_auto_trader
    if not _env_truthy("SUPERTREND_AUTO_ENABLED"):
        return None
# run_bot
    auto_trader = _build_supertrend_auto_trader(notifier)  # → None
    if auto_trader is not None:                            # → False
        ...
        if auto_trader is not None:
            tasks.append(asyncio.create_task(auto_trader.run_forever(), ...))
```

트레이더 객체가 `None` 이 되어 `run_forever` 태스크가 만들어지지 않는다. 그러면
**하드손절 / ATR 트레일 / 러너 / 이월갭스탑**(`_trail_hit` · `_hard_stop_hit` ·
`_carry_gap_stop_hit` · `_runner_should_exit`)이 전부 사라진다.

**(b) 대신 청산하는 데몬은 supertrend 프로파일을 갖고 있지 않다.**

- supertrend 포지션의 장부 전략명은 `_STRATEGY_ID = "supertrend"`다.
- `holding_evaluator.STRATEGY_EXIT_PROFILES`에는 `f_zone` · `sf_zone` · `gold_zone` ·
  `swing_38` · `closing_bet` · `ai_swing`만 있고 **`supertrend` 항목이 없다.**
- `resolve_policy()`는 매칭 실패 시 base policy를 반환한다.

→ supertrend 보유분이 로드된 `data/policy.json` base 로 평가된다. 개발 파일은 SL -2%일 수 있지만
파일 부재·파싱 실패 시 `PolicyConfig` 기본 SL은 -4%다. 즉 고유 정책이 운영 머신의 base 값으로
조용히 바뀐다. (운영 머신 policy.json 값은 미검증 — §6 참조)

**(c) ★가장 큰 함정 — env 만 0 으로 두면 데몬이 supertrend 를 "인수"한다.**

```python
# scripts/intraday_buy_daemon.py · _supertrend_yield_to_bot
def _supertrend_yield_to_bot(want_supertrend: bool, env: dict | None = None) -> bool:
    """run_telegram_bot(SupertrendAutoTrader)이 슈퍼트렌드 담당 중이면 데몬은 양보(False)."""
    env = env if env is not None else os.environ
    truthy = (env.get("SUPERTREND_AUTO_ENABLED", "") or "").strip().lower() in {"1","true","yes","on"}
    return False if (want_supertrend and truthy) else want_supertrend
```

`SUPERTREND_AUTO_ENABLED` 가 **truthy 가 아닐 때** 이 함수는 `want_supertrend` 를 그대로 돌려준다
(= 데몬이 supertrend 를 맡는다). `main()`에서 이 함수의 반환값으로 담당자를 결정하고 가드 로그를 남긴다.

→ crontab 에 `--supertrend` 가 남아 있으면 `SUPERTREND_AUTO_ENABLED=0` 은 **정지가 아니라 담당자 교체**다.
데몬 쪽 트레이더는 `_get_supertrend_trader()`로 별도 구성된다.
**정지하려면 crontab 의 `--supertrend` 제거가 반드시 동반돼야 한다.**

---

## 2. ✅ 권장 방법 — "진입만 차단, 청산 유지"

세 트레이더 모두 `run_cycle` 이 **청산을 먼저 전부 수행한 뒤** 진입 직전에만 return 하는 구조여서,
진입 게이트 시각만 뒤로/앞으로 밀면 청산 권한을 그대로 둔 채 진입만 0 으로 만들 수 있다.

| 대상 | 설정 | 근거 |
|---|---|---|
| supertrend | `SUPERTREND_AUTO_ENTRY_CUTOFF=00:01` | `SupertrendAutoTrader.run_cycle()`은 청산 후 `_entry_cutoff_passed()`를 검사 |
| 상따 | `LIMIT_UP_ENTRY_START=23:59` | `LimitUpChaseTrader.run_cycle()`은 청산 후 `_entry_window_open()`을 검사 |
| zone 전략 | `BARRO_DAEMON_STRATEGIES=off` | `_parse_strategies()`가 빈 목록을 만들고 `_scan_and_buy()`만 건너뛰며 `_evaluate_and_sell()`은 유지 |

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

⚠️ `SUPERTREND_AUTO_ENTRY_CUTOFF` 는 상시 봇과 `intraday_buy_daemon`이 같이 읽는다.
어느 쪽이 supertrend 를 맡고 있든 같은 값이 적용되므로 이 방법은 담당자와 무관하게 동작한다.

---

## 3. 정지 **전** 체크리스트

먼저 현재 checkout에서 절대경로와 KST를 고정한다. `git rev-parse`가 가리킨 경로가 실제 운영
checkout인지 확인한 뒤 실행하며, 개발 워크트리 경로를 복사하지 않는다.

```bash
BARRO_REPO="$(git rev-parse --show-toplevel)"
case "$BARRO_REPO" in /*) ;; *) echo "[STOP] repository path is not absolute" >&2; exit 1;; esac
if [ -x "$BARRO_REPO/.venv/bin/python" ]; then
  PYTHON="$BARRO_REPO/.venv/bin/python"
elif [ -x "$BARRO_REPO/venv/bin/python" ]; then
  PYTHON="$BARRO_REPO/venv/bin/python"
else
  echo "[STOP] repository Python not found" >&2; exit 1
fi
test "$(date '+%Z %z')" = "KST +0900" || { echo "[STOP] timezone is not KST" >&2; exit 1; }
cd "$BARRO_REPO"
set -a; . "$BARRO_REPO/.env.local"; set +a
printf 'repo=%s\npython=%s\ntime=%s\n' "$BARRO_REPO" "$PYTHON" "$(date --iso-8601=seconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S%z')"
```

1. **현재 보유 종목·전략 확인** — [[ai-swing-runbook]] §1의 읽기 전용 브로커 잔고 조회를
   먼저 실행하고, 어떤 전략의 포지션이 남아 있는지 장부 파일만 읽는다.
   ```bash
   test ! -f "$BARRO_REPO/data/active_positions.json" || \
     "$PYTHON" -m json.tool "$BARRO_REPO/data/active_positions.json"
   ```
2. **원 env 값을 먼저 기록한다.** 되돌릴 값을 모르면 재개할 수 없다.
   ```bash
   PAUSE_STAMP="$(date +%Y%m%d-%H%M%S)"
   BACKUP_DIR="$BARRO_REPO/data/operations-backups"
   mkdir -p "$BACKUP_DIR"
   for key in SUPERTREND_AUTO_ENTRY_CUTOFF LIMIT_UP_ENTRY_START BARRO_DAEMON_STRATEGIES \
              SUPERTREND_AUTO_ENABLED LIMIT_UP_CHASE_ENABLED LIVE_TRADING_ENABLED; do
     if grep -q "^${key}=" "$BARRO_REPO/.env.local"; then
       grep "^${key}=" "$BARRO_REPO/.env.local"
     else
       printf '%s=<UNSET>\n' "$key"
     fi
   done > "$BACKUP_DIR/pause-env-$PAUSE_STAMP.txt"
   crontab -l > "$BACKUP_DIR/crontab-$PAUSE_STAMP.txt"  # --supertrend / --strategies 원본
   ```
   백업 파일명에는 초 단위 시각이 들어가므로 같은 날 반복해도 이전 백업을 덮어쓰지 않는다.
3. **장부 기반 실매도 작성자가 정확히 하나 살아 있는지 확인한다.** 현재 legacy 포지션만
   운영하는 동안은 기존 작성자를 유지한다. ai_swing 실진입 전에는 반드시 경로 A를 유일한
   작성자로 두고 운영 장부를 보는 경로 B cron 자체를 중지한다.
   - 경로 A: `scripts/intraday_buy_daemon.py`의 `_evaluate_and_sell`
     — `BARRO_DAEMON_STRATEGIES=off`로도 죽지 않으며 ai_swing 주문·체결 수명주기를 지원한다.
   - 경로 B: 매시간 `scripts/evaluate_holdings.py` cron
     — legacy 경로이며 DRY_RUN도 장부를 갱신할 수 있다. ai_swing 라이브 전에는 운영 장부를
     보는 cron 자체를 중지하거나 복사한 `--pos-log`/`--audit-log`로 격리한다.
   ```bash
   crontab -l | grep -E 'intraday_buy_daemon|evaluate_holdings'
   ps aux | grep -E 'intraday_buy_daemon|evaluate_holdings|run_telegram_bot' | grep -v grep
   ```
4. supertrend 를 상시 봇이 맡고 있는지, 데몬이 맡고 있는지 확인한다(§1-2c).
   `crontab -l | grep -- --supertrend` 와 `.env.local` 의 `SUPERTREND_AUTO_ENABLED` 를 같이 본다.

---

## 4. 되돌리기(재개) 절차

**전제: §3-2 의 원값 백업이 있어야 한다.** 없으면 재개하지 말고 먼저 원값을 확정한다.

1. `.env.local` 의 세 키를 백업 파일의 원값으로 되돌린다(원래 미설정이었다면 **줄을 지운다** —
   빈 값으로 두면 `SUPERTREND_AUTO_ENTRY_CUTOFF=""` 는 컷오프 비활성,
   `BARRO_DAEMON_STRATEGIES=""` 는 CLI/default 전략으로 폴백한다. zone 전략을 끄려면 빈 값이
   아니라 반드시 `off` 또는 `none` 을 쓴다).
2. crontab 을 백업본으로 되돌린다(`--supertrend` / `--strategies` 인자 포함).
3. **기존 상시 프로세스를 반드시 재기동**한다. `.env.local` 원복만으로 이미 실행 중인 프로세스의
   설정은 바뀌지 않는다. 재기동 전후 PID가 달라졌는지 확인한 뒤 로그로 실제 적용을 확인한다.
   - supertrend ON: `⚡ 슈퍼트렌드 자동매매 ON (...)` 출력
   - 상따 ON: `🚀 상따(상한가 따라잡기) ON (...)` 출력
   - zone 전략: `[전략] 일반 매수 스캔: f_zone, ...` 또는
     `[전략] 일반 매수 전략 비활성 — 슈퍼트렌드 단독 운영.` 출력
   - 담당자 교체 여부: `[GUARD] SUPERTREND_AUTO_ENABLED 감지 — ...` 출력 여부
4. 재개 후에도 장부 기반 실매도 작성자는 §3과 같이 **정확히 하나**인지 다시 확인한다.

---

## 5. 반영 시점 (재기동이 필요한 경로 / 필요 없는 경로)

| 경로 | env 읽는 시점 | 재기동 |
|---|---|---|
| supertrend·상따 (`run_telegram_bot.py` 상시 프로세스) | **프로세스 기동 시 1회** — 빌더가 `os.environ` 을 읽어 config 를 만든다 | **필요** |
| cron 경로 (`evaluate_holdings` 등) | 매 실행이 새 프로세스 | 불필요 |
| `intraday_buy_daemon` (상시 데몬으로 도는 경우) | `main()` 진입 시 1회 | **필요** |

cron은 다음 실행에 새 환경을 읽지만, 상시 프로세스는 코드 pull·env 변경·env 원복 어느 경우에도
재기동 전까지 이전 값을 유지한다.

재기동은 **포트/PID 기준**으로 한다(CLAUDE.md §4 D5). `pkill -f` 는 프로세스명 불일치로 구프로세스를
남길 수 있다. 봇은 포트를 열지 않으므로 `ps` 로 PID 를 특정해 종료한 뒤 재기동한다.

---

## 6. 운영 머신에서 다시 확인할 값

개발 머신 경로·과거 manifest·문서 예시는 운영 사실의 근거가 아니다. 매 작업 시 아래를 실제 운영
checkout에서 확인하고 변경 기록에 path·branch·full SHA·clean 여부를 남긴다.

```bash
cd "$BARRO_REPO"
printf 'path=%s\nbranch=%s\nsha=%s\n' \
  "$(pwd -P)" "$(git branch --show-current)" "$(git rev-parse HEAD)"
test -z "$(git status --porcelain)" || { echo "[STOP] dirty worktree" >&2; exit 1; }
printf 'KIWOOM_BASE_URL=%s\n' "${KIWOOM_BASE_URL:-<UNSET>}"
crontab -l
ps aux | grep -E 'run_telegram_bot|intraday_buy_daemon|evaluate_holdings' | grep -v grep
```

endpoint가 mock인지 실계좌인지 작업 승인 내용과 대조하고, 읽기 전용 잔고 조회와 KRX 거래일 확인은
[[ai-swing-runbook]] §1 절차를 따른다. 실제 `.env.local`·crontab·`policy.json` 값은 확인 전까지
**미검증**으로 취급한다.

---

## 7. 코드 확인 기준

라인 번호와 해시는 변경 때마다 낡는다. 아래 함수명을 기준으로 현재 코드를 확인한다.

- `intraday_buy_daemon._parse_strategies`, `_scan_and_buy`, `_evaluate_and_sell`
- `supertrend_auto_trader._entry_cutoff_passed`, `run_cycle`
- `limit_up_chase_trader._entry_window_open`, `run_cycle`
- `live_order_gate.LiveOrderGate._preflight`

---

## 8. 관련 문서

- `docs/operations/ai-swing-runbook.md` — ai_swing 전략 자체의 플래그·활성/비활성 순서
- `docs/operations/strategy-restart-toggles.md`
- `RUNBOOK.md` §배포/재기동
- CLAUDE.md §2 (실거래 안전 경계) — 이 문서의 어떤 절차도 §2 S1 경로를 수정하지 않는다
