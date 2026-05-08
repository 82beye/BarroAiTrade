# BAR-OPS-33 — 미체결 주문 (kt00004) + `/orders` 명령

## fingerprint (mockapi)
- POST `/api/dostk/acnt`, `api-id: kt00004`
- 필수 body:
  - `dmst_stex_tp`: KRX/NXT/SOR (국내거래소구분, 누락 시 rc=2)
  - `qry_tp`: 1 (전체)
  - `all_stk_tp`: 1 (전체 종목)
  - `trde_tp`: 0/1/2 (전체/매수/매도)
  - `stk_cd`: 종목코드 (전체 시 빈)
  - `stex_tp`: 0
- 응답 list_key: 환경별 다름 — `stk_acnt_evlt_prst` (모의 0건) / `open_ordr` (실 미체결) — 동적 탐색

## 산출
- `backend/core/gateway/kiwoom_native_account.py`:
  - `OpenOrder` (frozen) — order_no/symbol/name/side/order_qty/filled_qty/pending_qty/order_price/order_date
  - `fetch_open_orders(exchange, trade_type)` — 매수/매도 측 필터, side 자동 분류
- `scripts/run_telegram_bot.py`:
  - `/orders` — 🟢BUY / 🔴SELL / 미체결수량/주문수량 표시
- `backend/tests/gateway/test_kiwoom_native_open_orders.py` — 5 cases

## 실 검증

```
$ /orders
📭 미체결 주문 없음
```
모의 환경 0건 — API 자체 정상 동작 (rc=0). 실 미체결 발생 시 자동 표시.

## 사용 키움 API (10 TR-ID)

| TR-ID | path | 용도 |
|-------|------|------|
| oauth2/token | /oauth2 | 토큰 |
| ka10081/80 | /chart | 일봉/분봉 |
| ka10032/27/30 | /rkinfo | 거래대금/등락률/거래량 상위 |
| kt00018/00001 | /acnt | 잔고/예수금 |
| ka10073 | /acnt | 실현손익 (OPS-28) |
| **kt00004** | **/acnt** | **미체결** ← OPS-33 |
| kt10000/01 | /ordr | 매수/매도 |

## 텔레그램 명령 누적 (18개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit /pnl /diff /tune /policy **/orders** |
| 매수 | /sim_execute → /confirm |
| 매도 | /sell_execute → /confirm_sell |
| 정책 | /tune apply |
| 공통 | /cancel |

## 운영 가치
- 매수/매도 confirm 후 미체결 즉시 확인
- 장중 주가 변동으로 지정가 미체결 발생 시 운영자 즉시 인지
- audit.csv (OPS-17) 의 ORDERED 와 대조 → 체결률 추적

## Tests
- 5 신규 / 회귀 **817 → 822 (+5)**, 0 fail

## 다음
- BAR-OPS-34 — 미체결 자동 취소 (kt10003 추정) + `/cancel_order` 명령
- BAR-OPS-35 — WebSocket 실시간 시세
- BAR-OPS-36 — A/B 정책 테스트
