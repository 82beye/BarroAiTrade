# BAR-OPS-22 — 차단/실패 즉시 텔레그램 알림

## 변경
- `LiveOrderGate(notifier=Optional[TelegramNotifier])` 파라미터 추가
- preflight `BLOCKED` (TradingDisabled / DailyLossLimitExceeded / DailyOrderLimitExceeded) → 텔레그램 즉시 알림
- 실행 `FAILED` (executor exception) → 텔레그램 즉시 알림
- 텔레그램 send 실패는 차단 raise 동작에 영향 X (격리)
- `format_blocked_alert` 에 Markdown escape (`_` `*` `[` `]` ` `` `) + 200 char truncate 추가

## 산출
- `backend/core/risk/live_order_gate.py` — notifier 통합 + `_notify_blocked` 헬퍼
- `backend/core/notify/telegram.py` — `_escape_md` + truncate
- `scripts/simulate_leaders.py` — LiveOrderGate 에 notifier 자동 주입
- `scripts/evaluate_holdings.py` — 동일
- `backend/tests/risk/test_live_order_gate_notify.py` — 4 신규 cases
- `backend/tests/notify/test_telegram.py` — escape/truncate 테스트 1건 추가

## 실 검증

```python
# LIVE_TRADING_ENABLED 미설정 + dry_run=False → 차단 + 알림
gate = LiveOrderGate(executor=exec, audit_path=..., notifier=notifier)
await gate.place_buy(symbol='005930', qty=1)
# → TradingDisabled raise
# → 텔레그램 도착 (message_id=8631):
#    ⚠️ 주문 차단
#    방향: buy
#    종목: 005930
#    사유: LIVE\_TRADING\_ENABLED=truthy 필요 (현재: ''). DRY\_RUN 모드는 ok...
```

## 보안
- ✅ Markdown escape (Telegram parse_mode 호환) — `_`/`*`/`[`/`]`/`` ` `` 자동 escape
- ✅ 사유 200 char truncate (DoS / parse 폭탄 방어)
- ✅ 알림 실패 격리 — try/except 차단 raise 우선
- ✅ notifier=None 시 기존 OPS-17 동작 그대로

## Tests
- 5 신규 / 회귀 **749 → 754 (+5)**, 0 fail

## 운영 효과
이전 (OPS-21): 매수·매도 성공 알림만 도착
**현재 (OPS-22)**: 차단·실패도 즉시 알림 → 운영 사고 시점 즉각 인지

## 다음
- BAR-OPS-23 — 양방향 봇 (텔레그램 명령 → 시뮬/주문 트리거)
- BAR-OPS-24 — 일자별 실현손익 (ka10073)
- BAR-OPS-25 — WebSocket 실시간 시세
