# BAR-OPS-27 — 텔레그램 매도 confirm 패턴 (`/sell_execute` → `/confirm_sell`)

## 변경
- `PendingOrder.side: str = "buy"` 필드 추가 (default 호환)
- `/sell_execute` — 보유 종목 TP/SL 평가 → 매도 토큰 발급
- `/confirm_sell <TOKEN>` — token 검증 + side="sell" 검증 → LiveOrderGate.place_sell
- 매수 토큰을 매도로 사용 차단 (`매도 토큰 아님` 에러)

## 흐름

```
모바일: /sell_execute
봇:    🔐 매도 토큰 발급 (TTL 5분)
       토큰: `XYZ123`
       *예정 매도*
       005930 삼성전자 +6.35% qty=10 ✅ TP
       000660 SK하이닉스 -3.20% qty=5 🛑 SL
       확인: /confirm_sell XYZ123  /  취소: /cancel

모바일: /confirm_sell XYZ123
봇:    🚀 매도 실행 (dry_run=True)
       🧪 DRY_RUN 005930 qty=10
       🧪 DRY_RUN 000660 qty=5
```

## 산출
- `backend/core/notify/order_confirm.py` — PendingOrder.side
- `scripts/run_telegram_bot.py` — `_cmd_sell_execute` / `_cmd_confirm_sell`
- `backend/tests/notify/test_order_confirm_sell.py` — 4 신규 cases

## 보안
- ✅ side="sell" 명시 검증 — 매수 토큰 오용 차단
- ✅ OPS-26 의 7중 보안 layer 그대로 적용
- ✅ ExitPolicy(TP +5% / SL -2%) — HOLD 종목은 토큰 발급 X

## 실 검증
```
$ /sell_execute      (모의 보유 0)
→ "보유 종목 없음 — 발급 X" 정확 응답

$ /confirm_sell BADTOK
→ "❌ 토큰 무효/만료/이미 사용됨"
```

운영 시나리오: 매시간 `/eval` 로 점검 → TP/SL 도달 발견 시 `/sell_execute` → `/confirm_sell <TOKEN>` 로 확정 매도.

## 텔레그램 명령 누적 (12개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit |
| 매수 | /sim_execute → /confirm <TOKEN> |
| **매도** | **/sell_execute → /confirm_sell <TOKEN>** |
| 공통 | /cancel |

## Tests
- 4 신규 / 회귀 **779 → 783 (+4)**, 0 fail

## 다음
- BAR-OPS-28 — 실현손익 (ka10073) 누적
- BAR-OPS-29 — WebSocket 실시간 시세
- BAR-OPS-30 — 봇 명령 응답 markdown 포맷 개선 (이모지 / 표)
