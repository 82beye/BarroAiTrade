# BAR-OPS-08 — 당일 캔들 IntradaySimulator

## 목적
사용자 요청: "당일 캔들 데이터 기준으로 5 전략 매매 시뮬레이션".

## 산출
- `backend/core/backtester/intraday_simulator.py`:
  - `IntradaySimulator.run(candles, symbol, strategies)` — 슬라이딩 윈도우 5 전략 평가 + ExitEngine 청산
  - `SimulationResult` (frozen) + `summary()` 텍스트 리포트
  - `TradeRecord` (frozen)
  - `load_csv_candles(path)` — CSV (timestamp, OHLCV) 로더
- `scripts/simulate_intraday.py` — CLI:
  - `--csv path` (수동 다운로드)
  - `--synthetic` (즉시, BAR-44 SyntheticDataLoader)
  - `--pykrx --start --end` (자동, pykrx 설치 시)
- 10 신규 tests + 회귀 652

## 사용 방법

### A. CSV (즉시)
```bash
# 네이버/키움/Investing.com 에서 다운받은 CSV
python scripts/simulate_intraday.py --symbol 005930 --csv data/005930.csv
```

### B. 합성 (즉시)
```bash
python scripts/simulate_intraday.py --symbol 005930 --synthetic
```

### C. pykrx 자동 다운로드
```bash
pip install pykrx
python scripts/simulate_intraday.py --symbol 005930 --pykrx --start 2026-01-01 --end 2026-05-08
```

## ExitPlan
- TP1 +3% (33%), TP2 +5% (33%), TP3 +7% (34%)
- SL -1.5%
- breakeven_trigger +1% 도달 시 SL 을 entry+1%로 이동
