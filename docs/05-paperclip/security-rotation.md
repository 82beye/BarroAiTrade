---
tags: [runbook, security, ops]
---

# 보안 키 회전 가이드

> ⚠️ **chat 평문 노출됨** — 운영 시작 전 회전 필수.

---

## 1. 키움 OpenAPI 키 회전

### Step 1 — 폐기 + 신규 발급
```
1. https://openapi.kiwoom.com 접속
2. "내 정보" → "API Key 관리"
3. 기존 KIWOOM_APP_KEY 폐기 ("FpT8GG2M...")
4. 신규 발급
```

### Step 2 — `.env.local` 갱신
```bash
cp .env.local .env.local.backup    # 백업
vi .env.local
# KIWOOM_APP_KEY=신규
# KIWOOM_APP_SECRET=신규
```

### Step 3 — 검증
```bash
set -a; . ./.env.local; set +a
.venv/bin/python -c "
import asyncio, os
from pydantic import SecretStr
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
async def main():
    o = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ['KIWOOM_APP_KEY']),
        app_secret=SecretStr(os.environ['KIWOOM_APP_SECRET']),
        base_url=os.environ.get('KIWOOM_BASE_URL', 'https://mockapi.kiwoom.com'),
    )
    t = await o.get_token()
    print(f'✅ 키움: token len={len(t.access_token.get_secret_value())}')
asyncio.run(main())
"
```

---

## 2. Telegram Bot Token 회전

### Step 1 — 폐기 + 신규 발급
```
1. 텔레그램 @BotFather 채팅
2. /token → 봇 선택 → 기존 토큰 (8704522743:AAGHZbQ6...) 폐기
3. /revoke 또는 /newtoken (BotFather 가이드)
4. 신규 토큰 발급
```

### Step 2 — `.env.local` 갱신
```bash
vi .env.local
# TELEGRAM_BOT_TOKEN=신규
```

### Step 3 — 검증
```bash
set -a; . ./.env.local; set +a
.venv/bin/python -c "
import asyncio
from backend.core.notify.telegram import TelegramNotifier
asyncio.run(TelegramNotifier.from_env().send('🔄 토큰 회전 검증 — pong'))
print('✅ 텔레그램: 메시지 도착 확인')
"
```

→ 모바일 텔레그램에서 `🔄 토큰 회전 검증 — pong` 메시지 도착 확인.

---

## 3. .gitignore 확인

```bash
git check-ignore -v .env.local
# .gitignore:20:.env.local    .env.local
# → gitignored 확인됨

git status .env.local 2>&1
# → "Untracked / no diff" 만 OK (commit 된 적 X)

# 만약 commit 된 적 있다면 (반드시 확인):
git log --all --full-history -- .env.local 2>&1 | head -5
# 결과 없어야 함. 결과 있으면 git filter-branch 또는 BFG 로 history 청소 필요.
```

---

## 4. 추가 보안 체크리스트

### Secret 노출 검사
```bash
# .env.local 외 파일에 키 평문 없는지
git grep "KIWOOM_APP_KEY=" -- ':!docs/' ':!.env*' 2>&1 | head -5
git grep "TELEGRAM_BOT_TOKEN=" -- ':!docs/' ':!.env*' 2>&1 | head -5
# → 결과 없어야 함

# 키움 노출된 옛 토큰이 코드에 남아있는지
git grep "FpT8GG2M\|AAGHZbQ6" 2>&1 | head -5
# → 결과 없어야 함
```

### Telegram chat_id 화이트리스트 검증
```bash
# scripts/run_telegram_bot.py 가 chat_id 단일 화이트리스트 사용 (OPS-24)
# 본인 외 다른 사람이 봇 발견해도 명령 무시됨
```

### LiveOrderGate 안전 검증
```bash
# 실전 진입 전 LIVE_TRADING_ENABLED 미설정 시 차단 동작
unset LIVE_TRADING_ENABLED
.venv/bin/python -c "
import asyncio, os
from pydantic import SecretStr
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.risk.live_order_gate import LiveOrderGate, TradingDisabled

async def main():
    o = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ['KIWOOM_APP_KEY']),
        app_secret=SecretStr(os.environ['KIWOOM_APP_SECRET']),
        base_url='https://mockapi.kiwoom.com',
    )
    gate = LiveOrderGate(
        executor=KiwoomNativeOrderExecutor(oauth=o),  # dry_run=False
        audit_path='/tmp/test_audit.csv',
    )
    try:
        await gate.place_buy(symbol='005930', qty=1)
        print('❌ 차단 안됨 — 보안 깨짐!')
    except TradingDisabled as e:
        print(f'✅ env flag 차단 정상: {str(e)[:60]}...')

asyncio.run(main())
"
```

---

## 5. 회전 후 운영 시작

→ [[runbook-ops|운영 시작 RUNBOOK]]

---

## 사고 대응 (회전 누락 시)

| 사고 | 즉시 조치 |
|------|----------|
| 키 노출 의심 | 즉시 폐기 + 봇 정지 (`kill $(cat logs/telegram_bot.pid)`) + cron 비활성 |
| 누군가 다른 사람이 봇 명령 시도 | chat_id 로그 확인 (`logs/telegram_bot.log` `unauthorized chat` 검색) |
| 가짜 매수 발생 | `data/order_audit.csv` 검토 → `/cancel_order` 즉시 |
| 자금 한도 초과 | 봇 정지 + 수동 매도 + 사고 분석 |

---

## 관련

- [[runbook-ops|운영 시작 RUNBOOK]]
- [[../00-index/system-flow|시스템 흐름도]] (7중 보안 layer)
- [[../00-index/ops-track-index|OPS 작업 인덱스]]
