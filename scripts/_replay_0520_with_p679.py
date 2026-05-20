"""5/20 audit 재시뮬 — 실제 vs P6+P7+P9 적용 비교."""
from __future__ import annotations

import asyncio
import csv
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

KST = timezone(timedelta(hours=9))
MIN_HOLD_MIN = 15
HARD_SL_PCT = -5.0
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -4.0
TRAILING_START_PCT = 3.0
TRAILING_OFFSET_PCT = 1.5
BREAKEVEN_TRIGGER_PCT = 2.5
PARTIAL_TP_PCT = 3.5
COMMISSION = 0.00015
TAX = 0.0018


def evaluate(rate, peak, partial_done, elapsed_min):
    in_cd = elapsed_min < MIN_HOLD_MIN
    if peak >= TRAILING_START_PCT and rate < peak - TRAILING_OFFSET_PCT:
        return ("trail", 1.0)
    if rate >= TAKE_PROFIT_PCT:
        return ("tp", 1.0)
    if not partial_done and rate >= PARTIAL_TP_PCT and rate < TAKE_PROFIT_PCT:
        return ("ptp1", 0.5)
    if rate <= HARD_SL_PCT:
        return ("hard_sl", 1.0)
    if in_cd:
        return None
    if peak >= BREAKEVEN_TRIGGER_PCT and rate <= 0:
        return ("be", 1.0)
    if rate <= STOP_LOSS_PCT:
        return ("sl", 1.0)
    return None


def sim_p679(entry_price, qty, day_bars, entry_ts):
    events = []
    remaining = qty
    peak = 0.0
    partial_done = False
    for bar in day_bars:
        if bar.timestamp.astimezone(KST) <= entry_ts or remaining <= 0:
            continue
        cur = float(bar.close)
        rate = (cur - entry_price) / entry_price * 100
        peak = max(peak, rate)
        elapsed = (bar.timestamp.astimezone(KST) - entry_ts).total_seconds() / 60
        sig = evaluate(rate, peak, partial_done, elapsed)
        if not sig:
            continue
        signal, ratio = sig
        sell_qty = max(1, int(remaining * ratio)) if signal == "ptp1" else remaining
        sell_qty = min(sell_qty, remaining)
        events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                       "sig": signal, "qty": sell_qty, "price": cur, "rate": rate})
        remaining -= sell_qty
        if signal == "ptp1":
            partial_done = True
    if remaining > 0:
        last = day_bars[-1]
        rate = (float(last.close) - entry_price) / entry_price * 100
        events.append({"ts": last.timestamp.astimezone(KST).strftime("%H:%M"),
                       "sig": "end", "qty": remaining, "price": float(last.close),
                       "rate": rate})
    return events


def net_of(events, entry_price, entry_qty):
    gross = sum((e["price"] - entry_price) * e["qty"] for e in events)
    comm = entry_price * entry_qty * COMMISSION + sum(e["price"] * e["qty"] * COMMISSION for e in events)
    tax = sum(e["price"] * e["qty"] * TAX for e in events)
    return gross - comm - tax


async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    # 5/20 audit 파싱 (KST)
    trades = defaultdict(list)
    with open("data/order_audit.csv") as f:
        for r in csv.DictReader(f):
            if not r["ts"].startswith("2026-05-20"):
                continue
            if r["action"] not in ("ORDERED", "DRY_RUN"):
                continue
            kst = datetime.fromisoformat(r["ts"].replace("+00:00", "+0000")).astimezone(KST)
            trades[r["symbol"]].append({"ts": kst, "side": r["side"], "qty": int(r["qty"])})

    print("=" * 140)
    print("5/20 실제 vs P6+P7+P9 시뮬 비교")
    print("=" * 140)
    print(f"  {'종목':<8} {'T1 entry':<12} {'qty':>5} {'entry가':>10} "
          f"{'실제 ev':<35} {'실제 net':>11}  {'P6+P7+P9 ev':<35} {'시뮬 net':>11}")
    print("-" * 140)
    grand_real = 0.0
    grand_sim = 0.0
    for sym in sorted(trades):
        ts_list = trades[sym]
        # T1 매수 (첫 buy)
        buys = [t for t in ts_list if t["side"] == "buy"]
        sells = [t for t in ts_list if t["side"] == "sell"]
        if not buys or not sells:
            continue
        bars = await fetcher.fetch_minute_history(symbol=sym, target_business_days=1)
        d520 = [b for b in bars if b.timestamp.strftime("%Y-%m-%d") == "2026-05-20"]
        if not d520:
            continue
        prices = {b.timestamp.astimezone(KST).strftime("%H:%M"): float(b.close) for b in d520}

        # 실제 net (모든 매수·매도 ledger)
        buy_qty_total = sum(b["qty"] for b in buys)
        buy_avg = sum(prices.get(b["ts"].strftime("%H:%M"), 0) * b["qty"] for b in buys) / buy_qty_total
        sell_avg = sum(prices.get(s["ts"].strftime("%H:%M"), 0) * s["qty"] for s in sells) / sum(s["qty"] for s in sells)
        sell_total = sum(s["qty"] for s in sells)
        real_gross = (sell_avg - buy_avg) * sell_total
        real_comm = buy_avg * buy_qty_total * COMMISSION + sell_avg * sell_total * COMMISSION
        real_tax = sell_avg * sell_total * TAX
        real_net = real_gross - real_comm - real_tax
        grand_real += real_net

        # P6+P7+P9 시뮬 (T1 only, 시뮬 매도)
        t1 = buys[0]
        t1_qty = t1["qty"]
        t1_price = prices.get(t1["ts"].strftime("%H:%M"), 0)
        if t1_price <= 0:
            continue
        # T1 진입 후 분봉 시뮬
        entry_dt = t1["ts"]
        events = sim_p679(t1_price, t1_qty, d520, entry_dt)
        sim_net = net_of(events, t1_price, t1_qty)
        grand_sim += sim_net

        real_ev = f"매수{len(buys)} 매도{len(sells)} sell@{sell_avg:,.0f}({(sell_avg-buy_avg)/buy_avg*100:+.1f}%)"
        sim_ev = " → ".join(f"{e['ts']} {e['sig']}({e['qty']}@{int(e['price']):,},{e['rate']:+.1f}%)" for e in events[:2])
        print(f"  {sym:<8} {t1['ts'].strftime('%H:%M'):<12} {t1_qty:>5} {t1_price:>10,.0f}  "
              f"{real_ev:<35} {real_net:>+11,.0f}  {sim_ev:<35} {sim_net:>+11,.0f}")
    print("-" * 140)
    delta = grand_sim - grand_real
    print(f"  {'합계':<82} {grand_real:>+11,.0f}  {'':<35} {grand_sim:>+11,.0f}")
    print(f"\n  P6+P7+P9 시뮬 변화: {delta:>+11,.0f}원 ({delta/abs(grand_real)*100:+.1f}% vs 실제)")


if __name__ == "__main__":
    asyncio.run(main())
