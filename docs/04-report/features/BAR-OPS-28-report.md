# BAR-OPS-28 — 일자별 실현손익 (ka10073) + `/pnl` 명령

## 검증 spec (mockapi.kiwoom.com, 2026-05-08)
- POST `/api/dostk/acnt`, header `api-id: ka10073`
- body: `{strt_dt: "YYYYMMDD", end_dt: "YYYYMMDD"}`
- 응답: `dt_stk_rlzt_pl[]` 각 row:
  - `dt` 일자 / `stk_cd` `stk_nm` / `cntr_qty` 체결수량
  - `buy_uv` 매입단가 / `cntr_pric` 체결가
  - `tdy_sel_pl` 매도손익 (signed) / `pl_rt` 손익률
  - `tdy_trde_cmsn` 수수료 / `tdy_trde_tax` 세금

## 산출
- `backend/core/gateway/kiwoom_native_account.py`:
  - `RealizedPnLEntry` (frozen dataclass)
  - `KiwoomNativeAccountFetcher.fetch_realized_pnl(start_date, end_date)` → list
  - YYYYMMDD 형식 검증 / `A` prefix strip / 부호 정규화
- `scripts/run_telegram_bot.py`:
  - `_cmd_pnl` 핸들러 — 최근 30일 실현손익 + 승률 + 수수료/세금
  - register `/pnl`
- `backend/tests/gateway/test_kiwoom_native_realized.py` — 4 cases

## 실 검증

```
$ /pnl
💵 실현손익 (04/09 ~ 05/09)
거래: 37건 / 승률: 5.4%
순손익: -284,487 원
수수료/세금: 123,260 / 35,107

*최근 5건*
20260409 상지건설 qty=194 -62,265 (-2.70%) 🛑
20260409 이루온 qty=20 -1,048 (-1.11%) 🛑
...
```

→ mockapi 환경의 누적 거래 history 정확히 파싱 + 집계 + 표시.

## 텔레그램 명령 누적 (13개)

| 카테고리 | 명령 |
|----------|------|
| 메타 | /help /ping |
| 조회 | /balance /history /sim /eval /audit **/pnl** |
| 매수 | /sim_execute → /confirm |
| 매도 | /sell_execute → /confirm_sell |
| 공통 | /cancel |

## 사용 키움 API (9 TR-ID)

| TR-ID | path | 용도 |
|-------|------|------|
| oauth2/token | /oauth2 | 토큰 |
| ka10081/80 | /chart | 일봉/분봉 |
| ka10032/27/30 | /rkinfo | 거래대금/등락률/거래량 상위 |
| kt00018/00001 | /acnt | 잔고/예수금 |
| **ka10073** | **/acnt** | **실현손익** ← 신규 |
| kt10000/01 | /ordr | 매수/매도 |

## Tests
- 4 신규 / 회귀 **783 → 787 (+4)**, 0 fail

## 운영 효과
- 매도 후 실 손익 누적 즉시 확인
- 승률·수수료 비중 추적
- 매일 16시 cron 에 `/pnl` 호출 추가 가능 (텔레그램 자동 발송)

## 다음
- BAR-OPS-29 — WebSocket 실시간 시세
- BAR-OPS-30 — 시뮬 결과 vs 실현손익 비교 분석
- BAR-OPS-31 — Frontend 대시보드
