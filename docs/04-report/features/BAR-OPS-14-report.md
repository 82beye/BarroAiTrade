# BAR-OPS-14 — 키움 자체 OpenAPI 주문 어댑터

## 검증 spec (mockapi.kiwoom.com, 2026-05-08)

| 항목 | 값 |
|------|-----|
| 매수 | POST `/api/dostk/ordr` header `api-id: kt10000` |
| 매도 | POST `/api/dostk/ordr` header `api-id: kt10001` |
| body | `{dmst_stex_tp, stk_cd, ord_qty, ord_uv, trde_tp, cond_uv}` |
| 시장가 | `trde_tp: "3"`, `ord_uv: ""` |
| 지정가 | `trde_tp: "0"`, `ord_uv: "<price>"` |
| 응답 (성공) | `{return_code: 0, return_msg: "정상", ord_no: "..."}` |
| 응답 (장 외) | `{return_code: 20, return_msg: "[2000](RC4058:모의투자 장종료)"}` |

## 산출
- `backend/core/gateway/kiwoom_native_orders.py`:
  - `KiwoomNativeOrderExecutor` — `place_buy(symbol, qty, price=None)` / `place_sell(...)`
  - `OrderSide` enum, `OrderResult` frozen dataclass
  - **dry_run 모드** — HTTP 호출 X, 의도만 로그
  - 강제 검증: 6자리 숫자 symbol / qty>0 / price>0 if specified / market in {KRX, NXT, SOR}
- `backend/tests/gateway/test_kiwoom_native_orders.py` — 8 cases

## 보안
- ✅ SecretStr (CWE-798), https-only (CWE-918)
- ✅ 입력 검증 (symbol fingerprint, qty/price 양수 강제)
- ✅ rate limit 0.25s
- ✅ DRY_RUN 옵션 — 실거래 진입 전 검증 가능
- ✅ 에러 raise 시 ord_no=None — 부분 성공 상태 X

## 실 검증

```python
# DRY_RUN
exec = KiwoomNativeOrderExecutor(oauth=o, dry_run=True)
r = await exec.place_buy(symbol='005930', qty=1)
# → OrderResult(side=BUY, symbol='005930', qty=1, order_no='DRY_RUN', dry_run=True)

# 실 모의 호출 (장 종료 시간)
exec = KiwoomNativeOrderExecutor(oauth=o)
r = await exec.place_buy(symbol='005930', qty=1)
# → RuntimeError: rc=20 [2000](RC4058:모의투자 장종료)
# → 평일 09:00~15:30 호출 시 정상 ord_no 발급
```

## ⚠️ 운영 주의
- `mockapi.kiwoom.com` 에서는 모의 잔고로만 거래 (실 손실 X)
- `api.kiwoom.com` 으로 base_url 변경 시 **실거래** 발생 — 별도 안전 게이트 필수:
  - DRY_RUN 옵션
  - 자금 한도 체크 (BAR-66 RiskEngine 통합)
  - Kill Switch (BAR-64 통합)
  - MFA 강제 (BAR-68)
  - 감사 로그 (BAR-68 audit_repo)

## Tests
- 8 신규 / 회귀 **689 → 697 (+8)**, 0 fail

## 다음
- BAR-OPS-15: KiwoomNativeOrderExecutor 를 LiveTradingOrchestrator(BAR-OPS-03) 와 결합 → 시뮬에서 실 주문 트리거
- BAR-OPS-16: 잔고/포지션 조회 API (kt10004/kt10005?) 어댑터
- BAR-OPS-17: 주문 체결 WebSocket (실시간 fill 추적)
