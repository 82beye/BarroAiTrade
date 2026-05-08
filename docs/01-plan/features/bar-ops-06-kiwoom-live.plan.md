# BAR-OPS-06 — Kiwoom OAuth2 + Live Order Executor

## 흡수 b 트랙
- BAR-63b 정식 — KiwoomLiveOrderExecutor (OAuth2 + HTTP 주문 송수신)

## FR
- KiwoomOAuth2Manager — POST /oauth2/token + 캐시 + 30분 margin auto-refresh + asyncio.Lock
- KiwoomLiveOrderExecutor — Authorization Bearer + tr_id 분기 (TTTC0802U buy / TTTC0801U sell)
- SecretStr 강제 (CWE-798) + https-only (CWE-918)
- 에러 로그 secret 마스킹 (CWE-532)
- 15 신규 tests + 회귀 630 (615→630)
