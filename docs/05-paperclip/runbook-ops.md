---
tags: [runbook, ops, deployment]
---

# OPS 운영 시작 RUNBOOK

> 26 OPS BAR 누적 → 즉시 운영 가능 단계.
> [[../00-index/system-flow|시스템 흐름도]] | [[../00-index/ops-track-index|작업 인덱스]]

---

## 사전 조건

- [ ] `.venv` 활성 + 의존성 설치 (`pip install -r requirements.txt`)
- [ ] `set -a; . ./.env.local; set +a` 후 `KIWOOM_APP_KEY`, `TELEGRAM_BOT_TOKEN` 정상
- [ ] 회귀 통과: `pytest backend/tests/ -q` → 830 passed

## 1. 보안 회전 (선결)

[[security-rotation|→ 키 + 토큰 회전 가이드]]

## 2. cron 4건 등록

```bash
crontab -e
```

```cron
SHELL=/bin/bash
REPO=/Users/beye/workspace/BarroAiTrade

# 09:30 매수 사이클 (시뮬→추천→실행→알림)
30 9 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  python scripts/simulate_leaders.py --top 5 --check-balance --execute --telegram \
  --log data/simulation_log.csv --audit-log data/order_audit.csv \
  >> logs/morning.log 2>&1

# 매시간 10~15 매도 평가
0 10-15 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  python scripts/evaluate_holdings.py --auto-sell --telegram \
  --audit-log data/order_audit.csv >> logs/eval.log 2>&1

# 15:20 강제 청산
20 15 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  python scripts/evaluate_holdings.py --tp -100 --sl 100 --auto-sell --telegram \
  --audit-log data/order_audit.csv >> logs/closing.log 2>&1

# 16:00 일일 리포트
0 16 * * 1-5 cd $REPO && set -a; . ./.env.local; set +a; \
  python scripts/generate_daily_report.py data/simulation_log.csv \
  --output "reports/$(date +\%F).md" --telegram >> logs/report.log 2>&1
```

```bash
mkdir -p logs reports data
```

## 3. 텔레그램 봇 데몬 시작

```bash
cd $REPO
nohup bash -c 'set -a; . ./.env.local; set +a; .venv/bin/python scripts/run_telegram_bot.py' \
  > logs/telegram_bot.log 2>&1 &
echo $! > logs/telegram_bot.pid

# 동작 확인
tail -f logs/telegram_bot.log
# → "🤖 봇 시작 — chat_id=..., 명령=[/help, ...]"

# 모바일에서:
# /ping → pong 🏓
# /balance → 💰 잔고 4,893만원
```

### 봇 종료
```bash
kill $(cat logs/telegram_bot.pid)
```

### systemd 등록 (선택, 영구 데몬)
```bash
sudo tee /etc/systemd/system/barroai-telegram-bot.service <<EOF
[Unit]
Description=BarroAiTrade Telegram Bot
After=network.target

[Service]
Type=simple
User=beye
WorkingDirectory=/Users/beye/workspace/BarroAiTrade
EnvironmentFile=/Users/beye/workspace/BarroAiTrade/.env.local
ExecStart=/Users/beye/workspace/BarroAiTrade/.venv/bin/python scripts/run_telegram_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable barroai-telegram-bot
sudo systemctl start barroai-telegram-bot
sudo systemctl status barroai-telegram-bot
```

## 4. 1~2주 모의 검증

| Day | 액션 | 확인 |
|-----|------|------|
| 1~3 | cron 자동 실행 모니터링 | 텔레그램 알림 정상 도착 |
| 4   | `/diff` 호출 | 시뮬 vs 실현 비교 표 |
| 7   | `/tune apply` | 정책 1차 학습 → policy.json update |
| 10~14 | 안정성 + 학습 결과 분석 | 실전 host 전환 결정 |

## 5. 실전 host 전환 (선택, 2주 후)

```bash
# .env.local
KIWOOM_BASE_URL=https://api.kiwoom.com   # mockapi → api
LIVE_TRADING_ENABLED=true                  # OPS-17 안전 게이트 활성

# 보수적 정책으로 시작
echo '{"min_score":0.7,"stop_loss_pct":-1.0,"max_per_position":0.05,
        "max_total_position":0.20,"daily_loss_limit":-1.0,"daily_max_orders":5}' \
  > data/policy.json
```

→ 작은 자금으로 1주 검증 후 점진 확대.

## 트러블슈팅

| 증상 | 원인 | 조치 |
|------|------|------|
| `RC4058: 모의투자 장종료` | 장 외 시간 호출 | 09:00~15:30 정상 |
| `429 Too Many Requests` | rate limit (5분 cool down) | 자동 재시도 또는 대기 |
| 텔레그램 메시지 X | bot token 만료 | `/revoke` → 재발급 |
| `RC4032: 원주문번호 X` | 가짜 ord_no | `/orders` 로 정확한 ord_no 확인 |
| `LIVE_TRADING_ENABLED` 차단 | env flag 미설정 | `.env.local` 또는 export 후 재실행 |

## 관련 문서

- [[../00-index/system-flow|시스템 흐름도]]
- [[../00-index/ops-track-index|OPS 작업 인덱스]]
- [[security-rotation|보안 키 회전]]
