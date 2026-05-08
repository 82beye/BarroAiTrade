# BAR-OPS-25 — 텔레그램 봇 명령 확장 (/sim /eval /audit)

## 추가된 명령

| 명령 | 응답 |
|------|------|
| `/sim` | 당일 주도주 top 3 + 자금 기반 추천 qty |
| `/eval` | 보유 종목 매도 시그널 (TP +5% / SL -2%) |
| `/audit` | 최근 audit log 5건 (timestamp / action / side / symbol / qty) |

## 산출
- `scripts/run_telegram_bot.py` — 3 신규 핸들러 + register 호출
- 기존 `_build_oauth()` 헬퍼 추출 (코드 중복 제거)

## 실 검증

```
$ /sim
📈 주도주 top 3 (자금 48,930,069)
319400 현대무벡스 +21.61% → 389주 ✅
001440 대한전선 +12.79% → 203주 ✅
005380 현대차 +8.04% → 23주 ✅

$ /eval
보유 종목 없음

$ /audit
📝 최근 audit 3/3 건
14:54:37 DRY_RUN buy 319400 qty=389
14:54:39 DRY_RUN buy 001440 qty=203
14:54:40 DRY_RUN buy 005380 qty=23
```

## 모바일 풀 운영 가능 명령

| 명령 | 용도 |
|------|------|
| `/help` | 사용 가능 명령 |
| `/ping` | 봇 동작 확인 |
| `/balance` | 잔고/예수금/보유 종목 수 |
| **`/sim`** | 주도주 + 추천 qty (수동 검토) |
| **`/eval`** | 매도 시그널 (TP/SL 도달 종목) |
| **`/audit`** | 최근 거래 감사 |
| `/history` | 시뮬 누적 PnL |

→ 운영자 외출 중에도 **모바일 텔레그램만으로** 시장 모니터링 + 의사결정 보조.

## 보안 (변경 없음)
- ✅ chat_id whitelist 강제
- ✅ 모든 명령 read-only (실 매수/매도 트리거는 별도 BAR)
- ✅ audit log 변조 X (CSV append-only)

## Tests
- 추가 unit test 0 (CLI 핸들러는 직접 호출 검증으로 충분)
- 회귀 **769 passed**, 0 fail (변경 없음)

## 다음
- BAR-OPS-26 — `/sim_execute` 명령 (텔레그램 명시 confirm 후 실 매수 트리거)
- BAR-OPS-27 — 실현손익 (ka10073) 누적
- BAR-OPS-28 — WebSocket 실시간 시세
