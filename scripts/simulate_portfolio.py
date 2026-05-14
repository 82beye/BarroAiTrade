"""주도주 N종목 — PortfolioSimulator 검증.

키움 ranking 으로 당일 주도주를 선정해, 단일 자본 풀에서 종목들이 자금을
두고 경쟁하는 포트폴리오 레벨 시뮬을 돌린다. simulate_leaders.py 의 종목
독립 시뮬과 달리 종목 간 자금 경쟁·동시 보유 한도·equity curve 를 반영한다.

사용:
    set -a; . ./.env.local; set +a
    venv/bin/python scripts/simulate_portfolio.py
    venv/bin/python scripts/simulate_portfolio.py --top 15 --capital 100000000
    venv/bin/python scripts/simulate_portfolio.py --max-per 0.2 --max-concurrent 5
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.backtester import PortfolioSimulator
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker


async def main() -> None:
    ap = argparse.ArgumentParser(description="주도주 PortfolioSimulator 검증")
    ap.add_argument("--top", type=int, default=10, help="주도주 top N (기본 10)")
    ap.add_argument("--min-flu", type=float, default=1.0, help="최소 등락률 %% (기본 1.0)")
    ap.add_argument("--min-score", type=float, default=0.5, help="최소 점수 (기본 0.5)")
    ap.add_argument(
        "--capital", type=float, default=45117579.0,
        help="초기 자본 (기본 45,117,579)",
    )
    ap.add_argument("--max-per", type=float, default=0.10, help="종목당 한도 비중 (기본 0.10)")
    ap.add_argument("--max-total", type=float, default=0.90, help="총 보유 한도 비중 (기본 0.90)")
    ap.add_argument("--max-concurrent", type=int, default=10, help="동시 보유 종목 수 (기본 10)")
    ap.add_argument(
        "--slippage", type=float, default=0.0,
        help="슬리피지 %% — 진입·청산 양방향 (기본 0.0, BAR-OPS-35 운영 권장 0.05)",
    )
    ap.add_argument(
        "--strategies",
        default="f_zone,sf_zone,gold_zone,swing_38,scalping_consensus",
        help="실행 전략 (comma-separated)",
    )
    args = ap.parse_args()

    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth, min_flu_rate=args.min_flu, min_score=args.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)
    strategies = args.strategies.split(",")

    leaders = await picker.pick(top_n=args.top)
    candles_by_symbol: dict[str, list] = {}
    for c in leaders:
        candles = await fetcher.fetch_daily(symbol=c.symbol)
        if len(candles) >= 32:
            candles_by_symbol[c.symbol] = candles
        else:
            print(f"[SKIP] {c.symbol} {c.name} 캔들 부족 ({len(candles)}봉)")

    if not candles_by_symbol:
        print("시뮬 대상 종목 없음")
        return

    sim = PortfolioSimulator(
        initial_capital=Decimal(str(args.capital)),
        max_per_position=Decimal(str(args.max_per)),
        max_total_position=Decimal(str(args.max_total)),
        max_concurrent=args.max_concurrent,
        warmup_candles=31,
        commission_pct=0.015,
        tax_pct_on_sell=0.18,
        slippage_pct=args.slippage,
    )
    result = sim.run(candles_by_symbol, strategies=strategies)

    print("=" * 72)
    print(result.summary())
    print()
    print("전략별 PnL:")
    for sid, pnl in sorted(
        result.pnl_by_strategy.items(), key=lambda kv: kv[1], reverse=True
    ):
        print(f"  {sid:<22}: {float(pnl):>+14,.0f}")
    print()
    m = result.metrics
    print("포트폴리오 성과 지표:")
    print(f"  총 청산 거래   : {m.total_trades}")
    print(f"  승률           : {m.win_rate * 100:.1f}%")
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    print(f"  Profit Factor  : {pf}")
    print(f"  expectancy     : {float(m.avg_pnl):>+,.0f} /거래")
    print(
        f"  MDD            : {float(m.max_drawdown):,.0f} "
        f"({m.max_drawdown_pct * 100:.1f}%)"
    )
    print(f"  Sharpe         : {m.sharpe_ratio:.2f}")
    print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
