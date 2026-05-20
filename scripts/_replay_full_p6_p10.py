"""5/19 + 5/20 audit 통합 — P6~P10 + SHORT_TERM_HIGH 매도 종합 시뮬.

각 일자 종목별:
1. P6: T1 매수만 (audit 첫 buy 만 인정, 추매 차단)
2. P10: 시초가 폭등 (+15%) 종목 차단
3. ExitPolicy 평가 (1분봉 시퀀스 bar-by-bar):
   - P7: cooldown 15분 안 defensive 차단, 익절(P9) 통과, hard SL(-5%) 우회
   - SHORT_TERM_HIGH: 익절 구간(+3%) 도달 + 1분봉 패턴 인식
"""
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
from backend.core.strategy.short_term_high_exit import detect_short_term_high_exit

KST = timezone(timedelta(hours=9))

MIN_HOLD_MIN = 15
HARD_SL_PCT = -5.0
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -4.0
TRAILING_START_PCT = 3.0
TRAILING_OFFSET_PCT = 1.5
BREAKEVEN_TRIGGER_PCT = 2.5
PARTIAL_TP_PCT = 3.5
MAX_INTRADAY_CHANGE_PCT = 15.0
COMMISSION = 0.00015
TAX = 0.0018


def evaluate(rate, peak, partial_done, elapsed_min, candles_for_ste=None):
    """매도 평가 — P7+P9+SHORT_TERM_HIGH 통합."""
    in_cd = elapsed_min < MIN_HOLD_MIN

    # SHORT_TERM_HIGH (P10+) — 익절 구간 + 1분봉 패턴 인식
    if candles_for_ste and rate >= PARTIAL_TP_PCT and len(candles_for_ste) >= 2:
        ste = detect_short_term_high_exit(candles_for_ste)
        if ste.signal:
            return (f"short_term_high_{ste.pattern}", 1.0)

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


def simulate(entry_price, qty, day_bars, entry_ts):
    events = []
    remaining = qty
    peak = 0.0
    partial_done = False
    bars_window = []
    for bar in day_bars:
        if bar.timestamp.astimezone(KST) < entry_ts:
            bars_window.append(bar)
            continue
        if bar.timestamp.astimezone(KST) == entry_ts:
            bars_window.append(bar)
            continue
        bars_window.append(bar)
        if remaining <= 0:
            break
        cur = float(bar.close)
        rate = (cur - entry_price) / entry_price * 100
        peak = max(peak, rate)
        elapsed = (bar.timestamp.astimezone(KST) - entry_ts).total_seconds() / 60
        # 단기 고점 매도용 1분봉 윈도우 (최근 30봉)
        ste_window = bars_window[-30:] if len(bars_window) >= 2 else None
        sig = evaluate(rate, peak, partial_done, elapsed, ste_window)
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
                       "sig": "end", "qty": remaining, "price": float(last.close), "rate": rate})
    return events


def net_of(events, entry_price, qty):
    gross = sum((e["price"] - entry_price) * e["qty"] for e in events)
    comm = entry_price * qty * COMMISSION + sum(e["price"] * e["qty"] * COMMISSION for e in events)
    tax = sum(e["price"] * e["qty"] * TAX for e in events)
    return gross - comm - tax


async def analyze_day(fetcher, date_str: str):
    """audit 에서 해당 일자 종목별 T1 매수 추출 + P10 차단 + 시뮬."""
    trades = defaultdict(list)
    with open("data/order_audit.csv") as f:
        for r in csv.DictReader(f):
            if not r["ts"].startswith(date_str):
                continue
            if r["action"] not in ("ORDERED", "DRY_RUN"):
                continue
            kst = datetime.fromisoformat(r["ts"].replace("+00:00", "+0000")).astimezone(KST)
            trades[r["symbol"]].append({"ts": kst, "side": r["side"], "qty": int(r["qty"])})

    grand = 0.0
    grand_real = 0.0
    p10_blocked = []
    rows = []
    for sym in sorted(trades):
        buys = [t for t in trades[sym] if t["side"] == "buy"]
        sells = [t for t in trades[sym] if t["side"] == "sell"]
        if not buys or not sells:
            continue
        bars = await fetcher.fetch_minute_history(symbol=sym, target_business_days=2)
        d_bars = [b for b in bars if b.timestamp.strftime("%Y-%m-%d") == date_str]
        if not d_bars:
            continue
        prices = {b.timestamp.astimezone(KST).strftime("%H:%M"): float(b.close) for b in d_bars}

        # 실제 net
        buy_qty_total = sum(b["qty"] for b in buys)
        sell_total = sum(s["qty"] for s in sells)
        buy_avg = sum(prices.get(b["ts"].strftime("%H:%M"), 0) * b["qty"] for b in buys) / buy_qty_total if buy_qty_total else 0
        sell_avg = sum(prices.get(s["ts"].strftime("%H:%M"), 0) * s["qty"] for s in sells) / sell_total if sell_total else 0
        real_gross = (sell_avg - buy_avg) * sell_total
        real_comm = buy_avg * buy_qty_total * COMMISSION + sell_avg * sell_total * COMMISSION
        real_tax = sell_avg * sell_total * TAX
        real_net = real_gross - real_comm - real_tax
        grand_real += real_net

        # T1
        t1 = buys[0]
        t1_price = prices.get(t1["ts"].strftime("%H:%M"), 0)
        if t1_price <= 0:
            continue

        # P10 — 시초가 폭등 차단
        first_open = d_bars[0].open
        intraday_change = (t1_price - first_open) / first_open * 100 if first_open else 0
        if intraday_change >= MAX_INTRADAY_CHANGE_PCT:
            p10_blocked.append((sym, intraday_change))
            rows.append((sym, t1["ts"].strftime("%H:%M"), t1["qty"], t1_price,
                         "P10-BLOCKED", f"시초가 +{intraday_change:.1f}%", real_net, 0.0))
            continue

        events = simulate(t1_price, t1["qty"], d_bars, t1["ts"])
        sim_net = net_of(events, t1_price, t1["qty"])
        grand += sim_net
        ev_brief = " → ".join(f"{e['ts']} {e['sig']}({e['qty']}@{int(e['price']):,},{e['rate']:+.1f}%)" for e in events[:2])
        rows.append((sym, t1["ts"].strftime("%H:%M"), t1["qty"], t1_price,
                     events[0]["sig"] if events else "—", ev_brief[:50], real_net, sim_net))
    return rows, grand_real, grand, p10_blocked


async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    summary = {}
    for date_str in ["2026-05-19", "2026-05-20"]:
        rows, real, sim, p10 = await analyze_day(fetcher, date_str)
        summary[date_str] = (real, sim, p10)
        print("=" * 150)
        print(f"{date_str}  종목별 시뮬 (T1만 + P10 차단 + P7+P9+SHORT_TERM_HIGH)")
        print("=" * 150)
        print(f"  {'종목':<8} {'T1':<6} {'qty':>5} {'entry':>9} {'signal':<25} {'events':<52} {'실제 net':>11} {'sim net':>11}")
        print("-" * 150)
        for r in rows:
            print(f"  {r[0]:<8} {r[1]:<6} {r[2]:>5} {r[3]:>9,.0f} {r[4]:<25} {r[5]:<52} {r[6]:>+11,.0f} {r[7]:>+11,.0f}")
        print("-" * 150)
        print(f"  합계  실제 {real:>+12,.0f}  | 시뮬 {sim:>+12,.0f}  | 차이 {sim - real:>+12,.0f}")
        if p10:
            print(f"  P10 차단: {p10}")
        print()

    # 총합
    total_real = sum(s[0] for s in summary.values())
    total_sim = sum(s[1] for s in summary.values())
    print("=" * 150)
    print(f"2일 누적 (5/19 + 5/20)")
    print("=" * 150)
    for d, (r, s, p) in summary.items():
        print(f"  {d}: 실제 {r:>+12,.0f} | P6~P10+SHORT_TERM_HIGH 시뮬 {s:>+12,.0f} | 차이 {s - r:>+12,.0f}")
    print(f"  ────────────────────────────────────────────────────────────")
    print(f"  합계      : 실제 {total_real:>+12,.0f} | 시뮬 {total_sim:>+12,.0f} | 차이 {total_sim - total_real:>+12,.0f}")


if __name__ == "__main__":
    asyncio.run(main())
