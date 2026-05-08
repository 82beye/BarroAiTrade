# BAR-OPS-26 — 텔레그램 confirm 패턴 매수 (`/sim_execute` → `/confirm`)

## 목적
텔레그램 한 줄 명령으로 실 주문이 즉시 발동되면 위험.
**6자리 token + 5분 TTL** 로 한 번 더 검증.

## 흐름

```
/sim_execute
  → 주도주 시뮬 + 자금 정책 평가
  → 추천 매수 종목 표시
  → 6자리 토큰 발급 (5분 TTL, chat_id 별 1개)
  → 메시지에 토큰 포함

/confirm <TOKEN>
  → token 일치 + 만료 X 검증
  → LiveOrderGate.place_buy 실행
  → 결과 응답 (DRY_RUN / ORDERED / 차단)
  → 토큰 폐기 (재사용 차단)

/cancel
  → 발급된 토큰 즉시 폐기
```

## 산출
- `backend/core/notify/order_confirm.py`:
  - `OrderConfirmStore(ttl_seconds=300)` — 메모리 기반
  - `issue(chat_id, orders)` — 6자리 alphanumeric token (256^6 ≈ 16억 조합)
  - `consume(chat_id, token)` — 일치 + TTL 검증 + 일회용 폐기
  - `cancel(chat_id)` — 즉시 폐기
  - `gc()` — 만료 토큰 일괄 정리
- `scripts/run_telegram_bot.py`:
  - `_cmd_sim_execute / _cmd_confirm / _cmd_cancel` 3 핸들러 추가
  - register 호출
- `backend/tests/notify/test_order_confirm.py` — 10 cases

## 보안
| Layer | 방어 |
|-------|------|
| 1 | TelegramBot chat_id whitelist (외부인 봇 사용 X) |
| 2 | 6자리 token 무작위 (secrets.choice — CSPRNG) |
| 3 | 5분 TTL — 분실/지연 시 자동 무효 |
| 4 | 일회용 — consume 후 즉시 폐기 |
| 5 | chat_id 매칭 — 다른 chat 의 token 사용 X |
| 6 | LiveOrderGate 4중 안전 (BAR-OPS-17) — 실 주문 전 또 검증 |
| 7 | LIVE_TRADING_ENABLED 미설정 시 강제 DRY_RUN |

→ 7중 보호. 외부 누출되도 실제 매수 어려움.

## 실 검증

```
사용자: /sim_execute
봇:    🔐 매수 토큰 발급 (TTL 5분)
       토큰: `4Q08PW`
       *예정 주문*
       319400 현대무벡스 → 389주
       001440 대한전선 → 203주
       005380 현대차 → 23주
       확인: /confirm 4Q08PW  /  취소: /cancel

사용자: /confirm 4Q08PW
봇:    🚀 매수 실행 (dry_run=True)
       🧪 DRY_RUN 319400 qty=389
       🧪 DRY_RUN 001440 qty=203
       🧪 DRY_RUN 005380 qty=23

사용자: /confirm 4Q08PW   (재시도)
봇:    ❌ 토큰 무효/만료/이미 사용됨
```

## 텔레그램 봇 명령 누적 (10개)

| 명령 | 카테고리 |
|------|----------|
| /help /ping | 메타 |
| /balance /history /sim /eval /audit | 조회 |
| **/sim_execute /confirm /cancel** | **실행 (OPS-26)** |

## Tests
- 10 신규 (token 발급/소비/만료/취소/대체/GC)
- 회귀 **769 → 779 (+10)**, 0 fail

## 다음
- BAR-OPS-27 — `/sell <symbol>` 명령 (개별 매도 confirm)
- BAR-OPS-28 — 실현손익 (ka10073) 누적
- BAR-OPS-29 — WebSocket 실시간 시세
