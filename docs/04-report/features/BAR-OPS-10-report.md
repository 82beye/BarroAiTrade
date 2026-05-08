# BAR-OPS-10 — Kiwoom 자체 OpenAPI 네이티브 어댑터

## 배경
사용자가 키움증권 OpenAPI 키 발급 → 시뮬 시도 → BAR-OPS-09 (KIS API 표준) 의 OAuth 토큰 발급 404. 키움 자체 OpenAPI(`api.kiwoom.com`)는 KIS API와 **완전히 다른 스펙**.

## 핵심 차이 (KIS vs Kiwoom 자체)

| 구분 | KIS API (BAR-OPS-09) | 키움 자체 (BAR-OPS-10) |
|------|----------------------|------------------------|
| Host (실전/모의) | `openapi.koreainvestment.com:9443` / 29443 | `api.kiwoom.com` / `mockapi.kiwoom.com` |
| 토큰 endpoint | POST `/oauth2/tokenP` | POST `/oauth2/token` |
| 토큰 body 키 | `appkey`, `appsecret` | `appkey`, `secretkey` |
| 토큰 응답 키 | `access_token`, `expires_in` | `token`, `expires_dt` (YYYYMMDDHHMMSS) |
| 응답 성공 코드 | `rt_cd: "0"` | `return_code: 0` (int) |
| 차트 endpoint | `/uapi/domestic-stock/v1/quotations/inquire-{daily,time}-itemchartprice` | POST `/api/dostk/chart` |
| 차트 식별 | header `tr_id` (FHKST03010100) | header `api-id` (ka10081) |
| 일봉 list 키 | `output2[]` | `stk_dt_pole_chart_qry[]` |
| 분봉 list 키 | `output2[]` | `stk_min_pole_chart_qry[]` |
| 가격 필드 부호 | (없음) | `-268500`/`+268500` (등락 prefix, abs 정규화) |
| 분봉 파라미터 | `time_unit` (string) | `tic_scope` (1/3/5/10/15/30/45/60) |

## 산출
- `backend/core/gateway/kiwoom_native_oauth.py` — KiwoomNativeOAuth (https-only, SecretStr, 30분 margin auto-refresh, asyncio.Lock)
- `backend/core/gateway/kiwoom_native_candles.py` — KiwoomNativeCandleFetcher (ka10081/ka10080, 부호 정규화, 시간순 오름차순 정렬)
- `scripts/simulate_intraday.py` — `--kiwoom-native daily/minute` + `--base-dt` + `--tic-scope`
- `backend/tests/gateway/test_kiwoom_native.py` — 10 cases (SecretStr 강제 / https-only / 토큰 캐싱 / 부호 정규화 / 응답 파싱 / 시간 정렬 / 헤더·바디 검증 / 에러)

## 실 검증 (2026-05-08, mockapi.kiwoom.com)

### 일봉 시뮬 (600 캔들, 2023-11-17 ~ 2026-05-08)
```
=== Simulation: 005930 (600 candles) ===
Strategies: f_zone, sf_zone, gold_zone, swing_38, scalping_consensus
Total trades: 4

PnL by strategy:
  swing_38                 : PnL=+337,600  WinRate=100.0%
  (그 외 진입 시그널 없음)

거래:
  2024-12-20 buy  swing_38 qty=100 price=53,000  reason=entry
  2025-01-06 sell swing_38 qty=33   price=55,900  reason=tp1
  2025-01-06 sell swing_38 qty=33   price=55,900  reason=tp2
  2025-01-08 sell swing_38 qty=34   price=57,300  reason=tp3
```

### 분봉 시뮬 (1분봉 900건, 2026-05-06 13:05 ~ 2026-05-08 15:30)
```
=== Simulation: 005930 (900 candles) ===
Total trades: 4

PnL by strategy:
  gold_zone                : PnL=+130,000  WinRate=50.0%

거래:
  2026-05-07 12:15 buy  gold_zone qty=100 price=264,500  reason=entry
  2026-05-07 14:31 sell gold_zone qty=33   price=272,500  reason=tp1
  2026-05-08 09:00 sell gold_zone qty=67   price=262,500  reason=stop_loss
  2026-05-08 11:09 buy  gold_zone qty=100 price=264,250  reason=entry  (보유 중)
```

## CLI

```bash
# .env.local 에 KIWOOM_APP_KEY / KIWOOM_APP_SECRET / KIWOOM_BASE_URL=https://mockapi.kiwoom.com
set -a; . ./.env.local; set +a

# 일봉 (오늘 기준 600건 자동 다운로드)
python scripts/simulate_intraday.py --symbol 005930 --kiwoom-native daily

# 일봉 (특정 기준일)
python scripts/simulate_intraday.py --symbol 005930 --kiwoom-native daily --base-dt 20260101

# 1분봉 (직전 900분)
python scripts/simulate_intraday.py --symbol 005930 --kiwoom-native minute --tic-scope 1

# 5분봉 / 30분봉 등
python scripts/simulate_intraday.py --symbol 005930 --kiwoom-native minute --tic-scope 5
```

## 보안
- ✅ SecretStr 강제 (CWE-798)
- ✅ https-only base_url 검증 (CWE-918 SSRF)
- ✅ 에러 로그 토큰/키 마스킹 (CWE-532)
- ✅ 24h 토큰 캐시 + 30분 margin auto-refresh (asyncio.Lock 동시성)
- ✅ `.env.local` gitignored 검증

## Tests
- 10 신규 / 회귀 **662 → 672 (+10)**, 0 fail

## 다음
- 실전 host(`api.kiwoom.com`) 키 받으면 동일 코드로 동작 (base_url 만 변경)
- 키움 자체 주문 API(`/api/dostk/order` 추정) — 별도 BAR
