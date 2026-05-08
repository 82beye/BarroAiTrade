# BAR-OPS-15 — 키움 자체 OpenAPI 계좌·잔고 조회 어댑터

## 검증 spec (mockapi.kiwoom.com, 2026-05-08)

| API | TR-ID | path | 핵심 응답 필드 |
|-----|-------|------|----------------|
| 계좌평가현황 | kt00018 | /api/dostk/acnt | tot_pur_amt / tot_evlt_amt / tot_evlt_pl / tot_prft_rt / prsm_dpst_aset_amt / acnt_evlt_remn_indv_tot[] |
| 예수금상세 | kt00001 | /api/dostk/acnt | entr / profa_ch / bncr_profa_ch / nxdy_bncr_sell_exct |

## 산출
- `backend/core/gateway/kiwoom_native_account.py`:
  - `KiwoomNativeAccountFetcher.fetch_balance(exchange="KRX")` → `AccountBalance`
  - `KiwoomNativeAccountFetcher.fetch_deposit()` → `AccountDeposit`
  - `HoldingPosition` (frozen dataclass) — 보유 종목별 평가
  - `_abs_decimal` / `_signed_decimal` — 부호 정규화 + Decimal 보장
  - 종목코드 정규화 — `A005930` (계좌 응답) → `005930` (`A` prefix strip)
- `backend/tests/gateway/test_kiwoom_native_account.py` — 7 cases

## 실 검증

```
-- 잔고/평가 (kt00018) --
  매입금액      :               0
  평가금액      :               0
  평가손익      :              +0 (+0.00%)
  추정예수자산   :      48,930,069
  보유 종목 수   : 0

-- 예수금 (kt00001) --
  예수금        :      48,930,069
  증거금현금     :               0
  보증금현금     :               0
  익일정산금     :               0
```

→ 모의 계좌 4,893만원 활용 가능 — OPS-11 주도주 시뮬 검증된 종목 모의 매수 가능.

## 보안
- ✅ SecretStr / https-only / 토큰 캐시 / 마스킹
- ✅ Decimal 강제 (area:money — float 정밀도 손실 방지)
- ✅ exchange 검증 (KRX/NXT/SOR)

## RiskEngine 통합 시나리오 (BAR-66)
```python
deposit = await account.fetch_deposit()
balance = await account.fetch_balance()

# 자금 한도 체크 (BAR-66)
max_per_position = deposit.cash * Decimal("0.30")        # 종목당 30%
max_total_open = deposit.cash * Decimal("0.90")           # 총 90% 이내
current_open = balance.total_eval

if current_open + new_position_value > max_total_open:
    raise RiskLimitExceeded(...)
```

## Tests
- 7 신규 / 회귀 **697 → 704 (+7)**, 0 fail

## 다음
- BAR-OPS-16: 미체결 주문 조회(kt00004 body 조정 — 현재 rc=2)
- BAR-OPS-17: 시뮬 → 잔고 조회 → RiskEngine → 실 주문 (Live 통합)
- BAR-OPS-18: 일자별 실현손익 (ka10073)
