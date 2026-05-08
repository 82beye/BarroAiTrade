# BAR-OPS-09 — KiwoomCandleFetcher (실 OpenAPI 캔들)

## 목적
사용자 요청: 키움 OpenAPI 로 캔들 다운로드 → 5 전략 시뮬.

## FR
- KiwoomCandleFetcher (HTTP):
  - fetch_daily(symbol, start, end, period D/W/M/Y) — TR_ID FHKST03010100
  - fetch_minute(symbol, target_date, time_unit 1/3/5/10/15/30/60) — TR_ID FHKST03010200
- 자동 OAuth2 토큰 refresh (BAR-OPS-06 재사용)
- 레이트 리밋 (default 0.25s = 1초 4건)
- SecretStr 강제 (CWE-798) + https-only (BAR-OPS-06 OAuth2Manager 통해)
- CLI 통합 (`scripts/simulate_intraday.py --kiwoom daily/minute`)

## DoD
- 10 신규 tests + 회귀 662 (652→662)
- KIWOOM_APP_KEY/SECRET 환경변수로 실 호출 가능
