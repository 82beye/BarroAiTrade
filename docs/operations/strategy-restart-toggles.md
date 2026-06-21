# 전략 재시작 / 중지 토글 (운영)

> 2026-06-21. "종베만 운영 → 기존 전략 재시작" 등 전략 on/off 를 안전하게 하기 위한 설정 레퍼런스.
> 운영 머신 `.env.local` + crontab + 텔레그램 봇 데몬 재기동으로 제어한다.

## 1. 전략별 토글 (2층 구조)

| 전략 | ① 활성 토글 | ② dry-run 해제(실거래) | 가동 위치 |
|------|------------|------------------------|-----------|
| zone (sf_zone·f_zone·gold_zone·swing_38) | crontab 매수 `--strategies` 에 나열 | **`LIVE_TRADING_ENABLED=true`**(마스터, 미설정/false=전 주문 차단) | 09:30 cron `simulate_leaders` / `intraday_buy_daemon` |
| supertrend | `SUPERTREND_AUTO_ENABLED=1` | `SUPERTREND_AUTO_DRYRUN=0` (기본 1=dry) | 텔레그램 봇 데몬 |
| limit_up_chase (상따) | `LIMIT_UP_CHASE_ENABLED=1` | `LIMIT_UP_CHASE_DRYRUN=0` (기본 1=dry) | 텔레그램 봇 데몬 |
| 종가베팅(종베) | `closing_bet_alert_daemon.py` (별도) | **주문 없음 — 알림 전용** | 종베 알림 cron |

- `LIVE_TRADING_ENABLED` 는 **모든 zone 매수/청산 실주문의 마스터 게이트**(`live_order_gate.py`). false/미설정이면 스캔·시그널은 돌지만 실주문은 `BLOCKED`(6/19 처럼).
- supertrend·limit_up_chase 는 각자 `*_ENABLED`(가동) + `*_DRYRUN`(실송출) 2단.

## 2. 다른 전략 전체 재시작 (종베와 병행)

### (1) `.env.local` 설정
```bash
LIVE_TRADING_ENABLED=true          # 마스터 — zone 실주문 활성
SUPERTREND_AUTO_ENABLED=1          # 추세추종 가동
SUPERTREND_AUTO_DRYRUN=0          # 추세추종 실송출
# 상따도 켤 경우만:
LIMIT_UP_CHASE_ENABLED=1
LIMIT_UP_CHASE_DRYRUN=0
```

### (2) crontab 매수 라인 — `--strategies` 에 전략 나열 (예: 4종 전부)
```cron
30 9 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  python scripts/simulate_leaders.py --top 5 --check-balance --execute --telegram \
  --strategies swing_38,f_zone,sf_zone,gold_zone \
  --log data/simulation_log.csv --audit-log data/order_audit.csv >> logs/morning.log 2>&1
```
(intraday_buy_daemon 도 동일하게 `--strategies swing_38,f_zone,sf_zone,gold_zone`.)

### (3) 텔레그램 봇 데몬 재기동 (supertrend/limit_up_chase 가 env 반영)
```bash
cd $REPO
kill "$(cat logs/telegram_bot.pid 2>/dev/null)" 2>/dev/null
nohup bash -c 'set -a; . ./.env.local; set +a; .venv/bin/python scripts/run_telegram_bot.py' \
  > logs/telegram_bot.log 2>&1 & echo $! > logs/telegram_bot.pid
```

## 3. ★안전 절차 (실거래 재개 전)
1. **먼저 DRY-RUN 확인**: `LIVE_TRADING_ENABLED` 제외(또는 false) + `SUPERTREND_AUTO_DRYRUN=1`로 1일 가동 → 로그에서 `DRY_RUN` 시그널이 정상인지 확인.
2. 정상 확인 후 `LIVE_TRADING_ENABLED=true` + `*_DRYRUN=0` 로 실거래 전환.
3. **묶인 보유분 주의**: `LIVE_TRADING_ENABLED=true` 순간, 그동안 BLOCKED 되던 **보유분 매도(예: 122640 557주)가 즉시 실행**될 수 있음 — 의도 확인.
4. **종베는 영향 없음**(알림 전용). 종베 보유 종목은 `closing_bet_positions.json` 으로 **타 전략 재진입이 차단**됨(중복 진입 방지 — 이미 적용).
5. 재개 후 `verify_eod_data.sh`·`_daily_strategy_audit.py` 로 실측 모니터.

## 4. 다시 중지 (전체/개별)
- **전체 즉시 중지(마스터)**: `LIVE_TRADING_ENABLED=false` → zone 실주문 전면 차단(스캔은 계속).
- **개별 중지**: `SUPERTREND_AUTO_ENABLED=0` / `LIMIT_UP_CHASE_ENABLED=0` / crontab `--strategies` 에서 제거.
- 봇 데몬 재기동해야 supertrend/limit_up_chase 토글이 반영됨.
