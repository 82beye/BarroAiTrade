"""전략별 최근 N일 매매 시뮬 — picker 주도주 + IntradaySimulator 600봉 +
compute_metrics period 슬라이스로 N일 거래만 집계.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.backtester import IntradaySimulator, compute_metrics
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.core.trading_costs import COMMISSION_PCT, TAX_PCT_ON_SELL  # [BAR-OPS-39] 실측

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


async def main() -> None:
    ap = argparse.ArgumentParser(description="전략별 최근 N일 매매 시뮬")
    ap.add_argument("--top", type=int, default=10, help="주도주 top N (기본 10)")
    ap.add_argument("--days", type=int, default=15, help="집계 일수 (기본 15)")
    ap.add_argument("--min-flu", type=float, default=1.0)
    ap.add_argument("--min-score", type=float, default=0.5)
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

    leaders = await picker.pick(top_n=args.top)
    rows = []
    for c in leaders:
        candles = await fetcher.fetch_daily(symbol=c.symbol)
        if len(candles) < 32:
            print(f"[SKIP] {c.symbol} {c.name} 캔들 부족")
            continue
        sim = IntradaySimulator(
            warmup_candles=31, position_qty=Decimal("100"),
            entry_on_next_open=True, exit_on_intrabar=True,
            commission_pct=COMMISSION_PCT, tax_pct_on_sell=TAX_PCT_ON_SELL,
        )
        result = sim.run(candles, symbol=c.symbol, strategies=STRATEGIES)
        last_date = candles[-1].timestamp.date()
        lo = last_date - timedelta(days=args.days)
        rows.append((c.symbol, c.name, result.trades, lo, last_date))

    if not rows:
        print("시뮬 대상 없음")
        return

    period_label = f"{rows[0][3]} ~ {rows[0][4]}"
    print("=" * 90)
    print(f"전략별 최근 {args.days}일 매매 시뮬  ({len(rows)} 종목, {period_label})")
    print("(IntradaySimulator 600봉 + compute_metrics period 슬라이스)")
    print("=" * 90)
    print(
        f"  {'전략':<22} {'active':>8} {'거래':>5} {'승률':>6} {'PF':>7} "
        f"{'PnL':>14} {'pnl/trade':>12}"
    )

    grand_pnl = Decimal("0")
    for sid in STRATEGIES:
        total_pnl = Decimal("0")
        total_trades = 0
        total_wins = 0
        active = 0
        for sym, name, trades, lo, hi in rows:
            sid_trades = [t for t in trades if t.strategy_id == sid]
            m = compute_metrics(sid_trades, period=(lo, hi))
            if m.total_trades > 0:
                active += 1
            total_pnl += m.total_pnl
            total_trades += m.total_trades
            total_wins += m.win_trades
        wr = total_wins / total_trades if total_trades else 0
        ppt = total_pnl / total_trades if total_trades else Decimal("0")
        wins_sum = Decimal("0")
        losses_sum = Decimal("0")
        for _sym, _name, trades, lo, hi in rows:
            sid_trades = [t for t in trades if t.strategy_id == sid]
            for t in sid_trades:
                if t.side == "sell" and lo <= t.timestamp.date() <= hi:
                    if t.pnl > 0:
                        wins_sum += t.pnl
                    else:
                        losses_sum += abs(t.pnl)
        pf = (
            float("inf")
            if losses_sum == 0 and wins_sum > 0
            else (float(wins_sum / losses_sum) if losses_sum > 0 else 0.0)
        )
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        grand_pnl += total_pnl
        print(
            f"  {sid:<22} {active:>5}/{len(rows):>2}  {total_trades:>5} "
            f"{wr * 100:>5.0f}% {pf_s:>7} {float(total_pnl):>+14,.0f} "
            f"{float(ppt):>+12,.0f}"
        )

    print("-" * 90)
    print(f"  {'전 전략 합계':<46} {float(grand_pnl):>+14,.0f}")

    print("\n=== 종목별 (전 전략 합산) ===")
    print(f"  {'종목':<18} {'거래':>5} {'승률':>6} {'PnL':>14}")
    for sym, name, trades, lo, hi in rows:
        m = compute_metrics(trades, period=(lo, hi))
        print(
            f"  {sym} {name:<10} {m.total_trades:>5} "
            f"{m.win_rate * 100:>5.0f}% {float(m.total_pnl):>+14,.0f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
