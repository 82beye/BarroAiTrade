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


def _load_kiwoom_native(symbol: str, mode: str, base_dt: str, tic_scope: str):
    """키움 자체 OpenAPI (api.kiwoom.com / mockapi.kiwoom.com) 다운로드.

    환경변수: KIWOOM_APP_KEY / KIWOOM_APP_SECRET / KIWOOM_BASE_URL.
    base_url 미지정 시 모의(mockapi) 기본.
    """
    import asyncio
    import os

    from pydantic import SecretStr

    from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
    from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com")

    if not app_key or not app_secret:
        raise SystemExit(
            "KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요.\n"
            "예: export KIWOOM_APP_KEY='...' KIWOOM_APP_SECRET='...'"
        )

    async def run():
        oauth = KiwoomNativeOAuth(
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
            base_url=base_url,
        )
        fetcher = KiwoomNativeCandleFetcher(oauth=oauth)
        if mode == "daily":
            return await fetcher.fetch_daily(symbol=symbol, base_dt=base_dt or None)
        if mode == "minute":
            return await fetcher.fetch_minute(symbol=symbol, tic_scope=tic_scope)
        raise SystemExit(f"unknown kiwoom-native mode: {mode}")

    return asyncio.run(run())


def _load_kiwoom(symbol: str, mode: str, start: str, end: str, time_unit: str = "1"):
    """키움 OpenAPI 다운로드 — KIWOOM_APP_KEY / SECRET / BASE_URL 환경변수 필요."""
    import asyncio
    import os

    from pydantic import SecretStr

    from backend.core.gateway.kiwoom_candles import KiwoomCandleFetcher
    from backend.core.gateway.kiwoom_oauth import KiwoomOAuth2Manager

    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get(
        "KIWOOM_BASE_URL", "https://openapi.koreainvestment.com:9443"
    )

    if not app_key or not app_secret:
        raise SystemExit(
            "KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요.\n"
            "예: export KIWOOM_APP_KEY='...' KIWOOM_APP_SECRET='...'"
        )

    async def run():
        oauth = KiwoomOAuth2Manager(
            base_url=base_url,
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
        )
        fetcher = KiwoomCandleFetcher(
            oauth=oauth,
            app_key=SecretStr(app_key),
            app_secret=SecretStr(app_secret),
        )
        if mode == "daily":
            return await fetcher.fetch_daily(
                symbol=symbol,
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
            )
        if mode == "minute":
            target = end.replace("-", "") if end else None
            return await fetcher.fetch_minute(
                symbol=symbol,
                target_date=target,
                time_unit=time_unit,
            )
        raise SystemExit(f"unknown kiwoom mode: {mode}")

    return asyncio.run(run())


def main() -> None:
    ap = argparse.ArgumentParser(description="당일 캔들 시뮬레이션 (BAR-OPS-08)")
    ap.add_argument("--symbol", required=True, help="종목코드 (예: 005930)")
    ap.add_argument("--csv", help="OHLCV CSV 파일 경로")
    ap.add_argument(
        "--synthetic", action="store_true", help="합성 데이터 사용"
    )
    ap.add_argument("--pykrx", action="store_true", help="pykrx 자동 다운로드")
    ap.add_argument(
        "--kiwoom",
        choices=["daily", "minute"],
        help="(KIS API) 다운로드 — 키움이 KIS 호환일 때만",
    )
    ap.add_argument(
        "--kiwoom-native",
        choices=["daily", "minute"],
        help="키움 자체 OpenAPI(api.kiwoom.com/mockapi.kiwoom.com). 키움 직접 발급 키 사용.",
    )
    ap.add_argument(
        "--base-dt",
        help="kiwoom-native daily 기준일 (YYYYMMDD, 기본=오늘)",
    )
    ap.add_argument(
        "--tic-scope",
        default="1",
        help="kiwoom-native minute 분 단위 (1/3/5/10/15/30/45/60)",
    )
    ap.add_argument("--start", help="pykrx/kiwoom-daily start (YYYY-MM-DD)")
    ap.add_argument("--end", help="pykrx/kiwoom-daily end (YYYY-MM-DD), kiwoom-minute target")
    ap.add_argument(
        "--time-unit", default="1",
        help="kiwoom-minute 시간 단위 (1/3/5/10/15/30/60 분)",
    )
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
    elif args.kiwoom:
        if args.kiwoom == "daily" and (not args.start or not args.end):
            raise SystemExit("--kiwoom daily 사용 시 --start 와 --end 필수")
        candles = _load_kiwoom(
            args.symbol, args.kiwoom,
            args.start or "", args.end or "",
            args.time_unit,
        )
    elif args.kiwoom_native:
        candles = _load_kiwoom_native(
            args.symbol, args.kiwoom_native,
            args.base_dt or "", args.tic_scope,
        )
    else:
        raise SystemExit("--csv / --synthetic / --pykrx / --kiwoom / --kiwoom-native 중 하나 필요")

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
