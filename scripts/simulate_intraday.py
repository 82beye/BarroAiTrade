"""BAR-OPS-08 — 당일 캔들 시뮬 CLI.

사용:
    # CSV 입력
    python scripts/simulate_intraday.py --symbol 005930 --csv data/005930.csv

    # 합성 데이터 (즉시 실행 가능)
    python scripts/simulate_intraday.py --symbol 005930 --synthetic --strategies f_zone,sf_zone

    # pykrx 자동 다운로드 (선택, pykrx 설치 필요)
    python scripts/simulate_intraday.py --symbol 005930 --pykrx --start 2026-01-01 --end 2026-05-07
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# repo root 를 path 에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.backtester import IntradaySimulator, load_csv_candles
from backend.models.market import MarketType


def _load_synthetic(symbol: str):
    """합성 데이터 — BAR-44 SyntheticDataLoader 사용."""
    from backend.tests.strategy.test_baseline import SyntheticDataLoader  # type: ignore

    return SyntheticDataLoader(seed=42).load(symbol, count=250)


def _load_pykrx(symbol: str, start: str, end: str):
    """pykrx 일봉 다운로드 (선택)."""
    try:
        from pykrx import stock
    except ImportError:
        raise SystemExit(
            "pykrx 미설치. pip install pykrx 후 재실행."
        )
    from datetime import datetime

    df = stock.get_market_ohlcv_by_date(start, end, symbol)
    from backend.models.market import OHLCV

    candles = []
    for date_idx, row in df.iterrows():
        candles.append(
            OHLCV(
                symbol=symbol,
                timestamp=date_idx.to_pydatetime(),
                open=float(row["시가"]),
                high=float(row["고가"]),
                low=float(row["저가"]),
                close=float(row["종가"]),
                volume=float(row["거래량"]),
                market_type=MarketType.STOCK,
            )
        )
    return candles


def main() -> None:
    ap = argparse.ArgumentParser(description="당일 캔들 시뮬레이션 (BAR-OPS-08)")
    ap.add_argument("--symbol", required=True, help="종목코드 (예: 005930)")
    ap.add_argument("--csv", help="OHLCV CSV 파일 경로")
    ap.add_argument(
        "--synthetic", action="store_true", help="합성 데이터 사용"
    )
    ap.add_argument("--pykrx", action="store_true", help="pykrx 자동 다운로드")
    ap.add_argument("--start", help="pykrx start (YYYY-MM-DD)")
    ap.add_argument("--end", help="pykrx end (YYYY-MM-DD)")
    ap.add_argument(
        "--strategies",
        default="f_zone,sf_zone,gold_zone,swing_38,scalping_consensus",
        help="실행할 전략 (comma-separated)",
    )
    args = ap.parse_args()

    if args.csv:
        candles = load_csv_candles(args.csv, symbol=args.symbol)
    elif args.synthetic:
        candles = _load_synthetic(args.symbol)
    elif args.pykrx:
        if not args.start or not args.end:
            raise SystemExit("--pykrx 사용 시 --start 와 --end 필수")
        candles = _load_pykrx(args.symbol, args.start, args.end)
    else:
        raise SystemExit("--csv / --synthetic / --pykrx 중 하나 필요")

    if len(candles) < 31:
        raise SystemExit(f"캔들이 부족합니다 (≥ 31 필요, 받은 수={len(candles)})")

    sim = IntradaySimulator()
    result = sim.run(
        candles,
        symbol=args.symbol,
        strategies=args.strategies.split(","),
    )
    print(result.summary())
    print()
    print(f"총 매매 기록: {len(result.trades)} 건")
    if result.trades:
        print("최근 5 건:")
        for t in result.trades[-5:]:
            print(
                f"  {t.timestamp} {t.side:>4s} {t.strategy_id:<25s} "
                f"qty={t.qty} price={t.price} reason={t.reason}"
            )


if __name__ == "__main__":
    main()
