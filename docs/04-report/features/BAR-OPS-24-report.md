# BAR-OPS-24 — 텔레그램 양방향 봇 (`/balance` `/history` `/ping` `/help`)

## 목적
운영자 외부에서 모바일 텔레그램으로 잔고·상태 즉시 조회.

## 산출
- `backend/core/notify/telegram_bot.py`:
  - `TelegramBot(bot_token: SecretStr, notifier, allowed_chat_ids, ...)`
  - `getUpdates` long-polling (timeout 30s)
  - **chat_id whitelist 강제** — 인가된 chat 외 무시
  - `register(command, handler)` — 명령 등록
  - `poll_once()` / `run()` (무한 루프)
  - 핸들러 에러 격리 (실패도 재시도)
- `scripts/run_telegram_bot.py` — 데몬 실행 + 4 핸들러 등록
- `backend/tests/notify/test_telegram_bot.py` — 11 cases

## 명령

| 명령 | 응답 |
|------|------|
| `/help` | 사용 가능 명령 |
| `/ping` | `pong 🏓` |
| `/balance` | 예수금 + 평가금액 + 보유 종목 수 |
| `/history` | 시뮬 누적 (전략별 PnL) top 10 |

## 실 검증

```python
# 핸들러 직접 호출 (mockapi)
$ python -c "from run_telegram_bot import ...; await _cmd_balance(bot, {})"

💰 잔고
예수금: 48,930,069 원
평가금액: 0 원
평가손익: +0 (+0.00%)
보유 종목: 0 개

$ await _cmd_history(bot, {})
📊 시뮬 누적 (15 entries)
swing_38: 3 runs / 66 trades / +7,505,290
f_zone: 3 runs / 0 trades / +0
...
```

## 운영
```bash
# 데몬 실행 (백그라운드)
nohup python scripts/run_telegram_bot.py > logs/telegram_bot.log 2>&1 &

# 또는 systemd 등록
# [Service]
# ExecStart=/path/to/.venv/bin/python /path/to/scripts/run_telegram_bot.py
# Restart=always
# EnvironmentFile=/path/to/.env.local
```

→ 사용자 모바일에서 `/balance` 입력 → 즉시 응답.

## 보안
- ✅ Bot token SecretStr (CWE-798)
- ✅ chat_id whitelist 강제 — 외부 사용자 명령 무시
- ✅ 핸들러 에러 격리 — 봇 다운 X
- ✅ `Bearer` 같은 secret 누출 X (응답에 token 포함 X)
- ✅ poll_cycle 실패 시 5s 후 재시도 (network 단절 복구)

## ⚠️ 운영 주의
- 봇이 키움 API 호출 → 키움 키도 데몬 환경변수에 노출 (.env.local 안전 보관)
- `/balance` 호출 시 키움 API 호출 (rate limit 0.25s × 2 호출)
- 명령 1건당 약 1초 소요

## Tests
- 11 신규 / 회귀 **758 → 769 (+11)**, 0 fail

## 다음
- BAR-OPS-25 — `/sim`, `/sell` 명령 (시뮬·매도 트리거)
- BAR-OPS-26 — 실현손익 (ka10073) 누적
- BAR-OPS-27 — WebSocket 실시간 시세
