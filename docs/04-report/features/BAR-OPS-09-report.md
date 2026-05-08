# BAR-OPS-09 — KiwoomCandleFetcher Report

## 핵심
- KIS Open Trading API 일봉/분봉 다운로드
- BAR-OPS-06 OAuth2Manager 재사용 (token cache + auto-refresh)
- CLI 통합: `scripts/simulate_intraday.py --kiwoom daily --start ... --end ...`
- 레이트 리밋 (1초 4건 안전)

## 사용
```bash
export KIWOOM_APP_KEY='...'
export KIWOOM_APP_SECRET='...'

# 일봉 (기간)
python scripts/simulate_intraday.py --symbol 005930 --kiwoom daily \\
  --start 2026-04-01 --end 2026-05-08

# 분봉 (당일 1분 단위)
python scripts/simulate_intraday.py --symbol 005930 --kiwoom minute \\
  --end 20260508 --time-unit 1
```

## Tests
- 10 신규 / 회귀 662 (652→662, +10)
