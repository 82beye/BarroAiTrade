# BAR-OPS-21 — Telegram 통보 자동화

## 목적
운영자 실시간 알림. Slack 대신 사용자 선호한 Telegram Bot.

## API
- POST `https://api.telegram.org/bot<TOKEN>/sendMessage`
- body: `{chat_id, text, parse_mode: "Markdown", disable_web_page_preview}`
- 응답: `{ok: true, result: {message_id, ...}}`

## 산출
- `backend/core/notify/telegram.py`:
  - `TelegramNotifier(bot_token: SecretStr, chat_id, ...)`
  - `TelegramNotifier.from_env()` — 환경변수 자동 로드
  - SecretStr 강제 (CWE-798) / 4096 char 자동 truncate / parse_mode 검증
  - 5 formatters: `format_buy_alert / sell_alert / simulation_summary / blocked_alert`
- `scripts/simulate_leaders.py` — `--telegram` 옵션
  - 시뮬 완료 시 요약 알림
  - 매수 실행 시 종목별 알림
- `scripts/evaluate_holdings.py` — `--telegram` 옵션
  - TP/SL 매도 실행 시 종목별 알림 (✅TP / 🛑SL)
- `backend/tests/notify/test_telegram.py` — 14 cases

## 실 검증 (사용자 chat_id=6035865441)

```bash
$ python scripts/simulate_leaders.py --top 3 --min-score 0.5 --execute --telegram
...
== 주문 실행 (LiveOrderGate, dry_run=True) ==
  [DRY_RUN] 319400 현대무벡스    qty=389  order_no=DRY_RUN
  [DRY_RUN] 001440 대한전선      qty=203  order_no=DRY_RUN
  [DRY_RUN] 005380 현대차       qty=23   order_no=DRY_RUN
```

→ 사용자 텔레그램 도착:
- 📊 시뮬 결과 (총 110 trades / PnL +7,505,290)
- 🧪 DRY_RUN 매수 #1 — 현대무벡스 389주
- 🧪 DRY_RUN 매수 #2 — 대한전선 203주
- 🧪 DRY_RUN 매수 #3 — 현대차 23주

(message_id 8626~8629 발급 확인)

## 운영 시나리오

### cron full automation + 알림
```bash
# 09:30 시뮬 + 매수 + 알림
30 9 * * 1-5 python scripts/simulate_leaders.py --top 5 --min-score 0.5 \
  --check-balance --execute --telegram \
  --log data/simulation_log.csv --audit-log data/order_audit.csv

# 매시간 평가 + 매도 + 알림
0 10-15 * * 1-5 python scripts/evaluate_holdings.py --tp 5.0 --sl -2.0 \
  --auto-sell --telegram

# 16:00 일일 리포트
0 16 * * 1-5 python scripts/generate_daily_report.py data/simulation_log.csv \
  --output "reports/$(date +%F).md"
```

→ 모든 매수·매도·차단 즉시 텔레그램 도착. 외부에서도 확인 가능.

## 보안
- ✅ SecretStr 토큰 (CWE-798)
- ✅ https-only Telegram API
- ✅ 4096 char 자동 truncate (DoS 방어)
- ✅ parse_mode whitelist
- ✅ 알림 실패는 주문 자체에 영향 X (try/except 격리)

## ⚠️ 사용자 보안 경고
- chat 에 평문 토큰 노출됨 (`8704522743:AAGHZbQ...`)
- BotFather 의 `/revoke` 후 재발급 권장
- chat_id 도 명시적 제공 — 다른 chat 으로 변경 시 .env.local 만 갱신

## Tests
- 14 신규 / 회귀 **735 → 749 (+14)**, 0 fail

## 다음
- BAR-OPS-22 — 차단 알림 (LiveOrderGate 의 BLOCKED 사유 즉시 통보)
- BAR-OPS-23 — 일일 markdown 리포트 텔레그램 본문 첨부
- BAR-OPS-24 — 양방향 봇 (텔레그램 명령 → 시뮬/주문 트리거)
