# BAR-OPS-34 — 미체결 취소 (kt10003) + `/cancel_order`

## fingerprint (mockapi)
- POST `/api/dostk/ordr`, `api-id: kt10003`
- 필수 body: `dmst_stex_tp` + `orig_ord_no` + `stk_cd` + `cncl_qty` (0=전량)
- 응답: `{return_code, return_msg, ord_no}`
- rc=20 RC4032 = "원주문번호 없음" (정상 동작 검증)

## 산출
- `KiwoomNativeOrderExecutor.cancel_order(original_order_no, symbol, cancel_qty=0)`
  - DRY_RUN 모드 — `DRY_CANCEL:<orig>` 응답
  - 검증: orig_no 필수 / symbol 6자리 / cancel_qty ≥ 0
- 봇 `/cancel_order <ORD_NO> <SYMBOL> [<QTY>]` (QTY 생략=전량)
- 7 신규 unit tests

## 실 검증

```
$ /cancel_order
사용법: /cancel_order <ORD_NO> <SYMBOL> [<QTY>]
_QTY 생략 시 전량 취소_

$ /cancel_order 9999999 005930
🧪 DRY_RUN 취소 — `9999999` 005930 qty=전량 → ord_no=`DRY_CANCEL:9999999`
```

## 운영 흐름 (지정가 미체결 처리)

```
/sim_execute → /confirm <TOKEN>     매수 (지정가)
       ↓
/orders                              미체결 발견
       ↓
주가 변동으로 체결 안 될 시
       ↓
/cancel_order <ORD_NO> <SYMBOL>     취소 + audit append
       ↓
/sim_execute (새 가격으로) → /confirm 재진입
```

## 사용 키움 API (11 TR-ID, 운영용 완성)

| 카테고리 | TR-ID |
|---------|-------|
| 인증 | oauth2/token |
| 시세 | ka10081(일봉), ka10080(분봉) |
| 순위 | ka10032/27/30 (거래대금/등락률/거래량) |
| 계좌 | kt00018(잔고), kt00001(예수금), ka10073(실현), kt00004(미체결) |
| 주문 | kt10000(매수), kt10001(매도), **kt10003(취소)** |

## 텔레그램 명령 누적 (19개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit /pnl /diff /tune /policy /orders |
| 매수 | /sim_execute → /confirm |
| 매도 | /sell_execute → /confirm_sell |
| 정책 | /tune apply |
| **취소** | **/cancel_order <ORD_NO> <SYMBOL> [<QTY>]** |
| 공통 | /cancel |

## Tests
- 7 신규 / 회귀 **822 → 829 (+7)**, 0 fail
