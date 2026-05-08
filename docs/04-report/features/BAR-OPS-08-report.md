# BAR-OPS-08 — IntradaySimulator Report

## 핵심
- **IntradaySimulator** — 당일 캔들 → 5 전략 슬라이딩 윈도우 시뮬 + ExitEngine 청산
- **CSV/Synthetic/pykrx 3 입력 방식** 지원 — CLI `scripts/simulate_intraday.py`
- **ExitPlan 자동 스케일** — entry 기준 +3/+5/+7% TP, -1.5% SL, +1% breakeven

## 사용
- 즉시: `python scripts/simulate_intraday.py --symbol 005930 --synthetic`
- 실 데이터: `python scripts/simulate_intraday.py --symbol 005930 --csv data/005930.csv`
- 자동: `pip install pykrx && python scripts/simulate_intraday.py --symbol 005930 --pykrx --start 2026-01-01 --end 2026-05-08`

## Tests
- 10 신규 / 회귀 652 (642→652, +10)

## OPS 누적 (8 BAR / 105 신규 tests)
- OPS-01~07: 95
- **OPS-08**: 10
