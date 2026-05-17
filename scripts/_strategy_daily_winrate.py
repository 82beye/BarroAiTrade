"""일별 매매 승률 분석.

picker 10종목 + IntradaySimulator 600봉 → trades 짝 매칭(entry-exit) →
일자별 그룹 → 일별 매매수·승·패·승률·net PnL·누적net.

일별 승률 = 그날 매도 거래 중 net > 0 비율. (entry-exit 한 짝 = 1 trade)
trail default ON 적용 (commit 9193f19).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.backtester import IntradaySimulator
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


def pair_trades(trades):
    """동일 종목·전략의 buy → sell 순차 매칭 (분할 청산은 마지막 청산 timestamp 기준 1 trade)."""
    paired = []
    open_entries: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trades:
        key = (t.symbol, t.strategy_id)
        if t.side == "buy":
            open_entries[key].append({"entry": t, "exits": [], "pnl": Decimal("0")})
        else:  # sell
            if not open_entries[key]:
                continue
            cur = open_entries[key][0]
            cur["exits"].append(t)
            cur["pnl"] += t.pnl
            if not hasattr(cur["entry"], "_remaining_qty"):
                cur["entry"]._remaining_qty = cur["entry"].qty
            cur["entry"]._remaining_qty -= t.qty
            if cur["entry"]._remaining_qty <= 0:
                last_exit = cur["exits"][-1]
                paired.append({
                    "symbol": cur["entry"].symbol,
                    "strategy": cur["entry"].strategy_id,
                    "buy_ts": cur["entry"].timestamp,
                    "sell_ts": last_exit.timestamp,
                    "pnl": cur["pnl"],
                })
                open_entries[key].pop(0)
    return paired


async def main():
    ap = argparse.ArgumentParser(description="일별 매매 승률 분석")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--days", type=int, default=30, help="집계 일수 (기본 30)")
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

    all_paired = []
    per_symbol_paired: dict[str, list[dict]] = {}
    for c in leaders:
        candles = await fetcher.fetch_daily(symbol=c.symbol)
        if len(candles) < 32:
            print(f"[SKIP] {c.symbol} {c.name} 캔들 부족")
            continue
        sim = IntradaySimulator(
            warmup_candles=31, position_qty=Decimal("100"),
            entry_on_next_open=True, exit_on_intrabar=True,
            commission_pct=0.015, tax_pct_on_sell=0.18,
        )
        result = sim.run(candles, symbol=c.symbol, strategies=STRATEGIES)
        paired = pair_trades(result.trades)
        for p in paired:
            p["name"] = c.name
        per_symbol_paired[c.symbol] = paired
        all_paired.extend(paired)

    if not all_paired:
        print("매매 없음")
        return

    # 윈도우 — 마지막 sell_ts 기준 -days
    last_date = max(p["sell_ts"].date() for p in all_paired)
    cutoff = last_date - timedelta(days=args.days)
    window = [p for p in all_paired if p["sell_ts"].date() >= cutoff]

    # 일자별 그룹 (sell_ts 기준)
    by_day: dict[date, list[dict]] = defaultdict(list)
    for p in window:
        by_day[p["sell_ts"].date()].append(p)

    print()
    print("=" * 95)
    print(
        f"일별 매매 승률 — picker {len(leaders)}종목, 윈도우 {args.days}일 "
        f"({cutoff} ~ {last_date}), trail default ON"
    )
    print("=" * 95)
    print(
        f"  {'일자':<12} {'매매':>4} {'승':>3} {'패':>3} {'승률':>6} "
        f"{'net PnL':>14} {'누적net':>14}  종목"
    )
    print("-" * 95)
    cum_net = Decimal("0")
    total_trades = 0
    total_wins = 0
    win_days = 0
    loss_days = 0
    even_days = 0
    for d in sorted(by_day.keys()):
        items = by_day[d]
        n = len(items)
        wins = sum(1 for p in items if p["pnl"] > 0)
        losses = sum(1 for p in items if p["pnl"] < 0)
        wr = wins / n * 100 if n else 0
        day_net = sum((p["pnl"] for p in items), Decimal("0"))
        cum_net += day_net
        total_trades += n
        total_wins += wins
        if day_net > 0:
            win_days += 1
        elif day_net < 0:
            loss_days += 1
        else:
            even_days += 1
        syms = ",".join(sorted({p["symbol"] for p in items}))[:35]
        print(
            f"  {d.isoformat():<12} {n:>4} {wins:>3} {losses:>3} "
            f"{wr:>5.0f}% {float(day_net):>+14,.0f} {float(cum_net):>+14,.0f}  {syms}"
        )
    print("-" * 95)
    n_total = len(window)
    overall_wr = total_wins / n_total * 100 if n_total else 0
    n_days = len(by_day)
    day_wr = win_days / n_days * 100 if n_days else 0
    print(
        f"  {'합계':<12} {n_total:>4} {total_wins:>3} {n_total - total_wins:>3} "
        f"{overall_wr:>5.1f}% {float(cum_net):>+14,.0f}"
    )
    print()
    print(f"일별 승률 (각 trade 기준)  : {total_wins}/{n_total} = {overall_wr:.1f}%")
    print(
        f"수익 일 / 손실 일 / 본전 일: "
        f"{win_days}/{loss_days}/{even_days} = "
        f"수익 일 비율 {day_wr:.1f}%"
    )
    print(f"활동 일수                  : {n_days}일 (윈도우 {args.days}일 중)")

    # 전략별 일별 승률 분해
    print()
    print("=" * 95)
    print("전략별 일별 매매 승률")
    print("=" * 95)
    print(
        f"  {'전략':<22} {'거래':>5} {'승':>4} {'패':>4} {'승률':>6} {'net':>14}"
    )
    print("-" * 95)
    by_strat: dict[str, list[dict]] = defaultdict(list)
    for p in window:
        by_strat[p["strategy"]].append(p)
    for sid in sorted(by_strat.keys()):
        items = by_strat[sid]
        n = len(items)
        wins = sum(1 for p in items if p["pnl"] > 0)
        net = sum((p["pnl"] for p in items), Decimal("0"))
        wr = wins / n * 100 if n else 0
        print(
            f"  {sid:<22} {n:>5} {wins:>4} {n - wins:>4} "
            f"{wr:>5.1f}% {float(net):>+14,.0f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
