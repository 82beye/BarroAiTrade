"""5/19 audit + 1분봉 재시뮬 — P6+P7+P8 fix 적용.

P6: 동일 종목 30분 cooldown → 추매 차단 (T1만 매수)
P7: 매도 cooldown 15분 + hard SL(-5%) 우회
P8: SIDEWAYS = max_buy 1 (사이클당 1건만 → 진입 종목 선별)

5/19 실제 9 종목 → P8 적용 시 약 3~4 종목만 진입 가정.
"""
from __future__ import annotations

import asyncio
import csv
import os
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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


def evaluate(rate: float, peak: float, partial_done: bool, elapsed_min: float):
    """ExitPolicy + P7+P9 cooldown 평가 → ('signal', sell_ratio) 또는 None.

    cooldown 안 (elapsed < 15분):
      - 익절(trail/tp/ptp1)은 허용 (P9)
      - 방어(breakeven/sl)는 차단. 단 hard SL(-5%) 만 우회.
    """
    in_cooldown = elapsed_min < MIN_HOLD_MIN

    # 1. trailing — 익절성, cooldown 우회
    if peak >= TRAILING_START_PCT and rate < peak - TRAILING_OFFSET_PCT:
        return ("trail", 1.0)
    # 2. full TP — 익절성, cooldown 우회
    if rate >= TAKE_PROFIT_PCT:
        return ("tp", 1.0)
    # 3. partial_tp — 익절성, cooldown 우회
    if not partial_done and rate >= PARTIAL_TP_PCT and rate < TAKE_PROFIT_PCT:
        return ("ptp1", 0.5)
    # 4. hard SL — cooldown 우회
    if rate <= HARD_SL_PCT:
        return ("hard_sl", 1.0)
    # cooldown 안 — defensive 차단
    if in_cooldown:
        return None
    # 5. breakeven (cooldown 후)
    if peak >= BREAKEVEN_TRIGGER_PCT and rate <= 0:
        return ("breakeven", 1.0)
    # 6. SL (cooldown 후)
    if rate <= STOP_LOSS_PCT:
        return ("sl", 1.0)
    return None


def simulate(entry_price: float, qty: int, day_bars: list, entry_ts: datetime):
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
        elapsed_min = (bar.timestamp.astimezone(KST) - entry_ts).total_seconds() / 60
        sig = evaluate(rate, peak, partial_done, elapsed_min)
        if not sig:
            continue
        signal, ratio = sig
        sell_qty = max(1, int(remaining * ratio)) if signal == "ptp1" else remaining
        sell_qty = min(sell_qty, remaining)
        events.append({
            "ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
            "sig": signal, "qty": sell_qty, "price": cur, "rate": rate, "peak": peak,
        })
        remaining -= sell_qty
        if signal == "ptp1":
            partial_done = True
    if remaining > 0:
        last = day_bars[-1]
        events.append({
            "ts": last.timestamp.astimezone(KST).strftime("%H:%M"),
            "sig": "end", "qty": remaining, "price": float(last.close),
            "rate": (float(last.close) - entry_price) / entry_price * 100,
            "peak": peak,
        })
    return events


def net_of(events, entry_price, qty):
    gross = sum((e["price"] - entry_price) * e["qty"] for e in events)
    comm = entry_price * qty * COMMISSION + sum(e["price"] * e["qty"] * COMMISSION for e in events)
    tax = sum(e["price"] * e["qty"] * TAX for e in events)
    return gross - comm - tax


async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    # 5/19 audit 기반 (entry_price = 1분봉 close at entry_ts, qty = audit 합)
    # P6: 추매 차단 → T1 (audit 첫 매수) 만 사용
    cases = [
        # symbol,    name,             entry_ts,  qty (T1)
        ("001430", "세아베스틸지주",   "09:05", 68),
        ("005500", "SK증권",          "14:33", 276),
        ("027360", "아주IB투자",       "09:16", 358),
        ("036930", "주성엔지",         "09:05", 36),
        ("274090", "덕산테코피아",     "09:05", 232),
        ("356680", "엑스게이트",       "09:05", 324),
        ("080220", "제주반도체",       "09:05", 27),
        ("069500", "KODEX 200",      "09:05", 22),  # DCA T2
    ]

    print("=" * 130)
    print("5/19 P6+P7 적용 재시뮬 — T1만 매수 + 15분 cooldown + hard SL 우회")
    print("=" * 130)
    print(f"  {'종목':<22} {'entry':<6} {'qty':>4} {'entry가':>9} {'청산 events':<55} {'net':>11}")
    print("-" * 130)
    grand = 0.0
    for sym, name, ts_str, qty in cases:
        bars = await fetcher.fetch_minute_history(symbol=sym, target_business_days=1)
        d519 = [b for b in bars if b.timestamp.strftime("%Y-%m-%d") == "2026-05-19"]
        if not d519:
            print(f"  {sym} {name[:10]:<10}  분봉 없음")
            continue
        entry_dt = datetime.strptime(f"2026-05-19 {ts_str}:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        entry_bar = next((b for b in d519 if b.timestamp.astimezone(KST).strftime("%H:%M") == ts_str), None)
        if not entry_bar:
            print(f"  {sym} entry bar 없음")
            continue
        entry_price = float(entry_bar.close)
        events = simulate(entry_price, qty, d519, entry_bar.timestamp.astimezone(KST))
        n = net_of(events, entry_price, qty)
        grand += n
        ev_brief = " → ".join(f"{e['ts']} {e['sig']}({e['qty']}@{int(e['price']):,},{e['rate']:+.1f}%,peak{e['peak']:+.1f}%)" for e in events[:3])
        print(f"  {sym} {name[:10]:<14} {ts_str:<6} {qty:>4} {entry_price:>9,.0f}  {ev_brief:<55} {n:>+11,.0f}")
    print("-" * 130)
    print(f"  P6+P7 합계 (T1만, 8 종목 모두 매수)  {grand:>+11,.0f}")
    print()

    # P8 시나리오 — SIDEWAYS=1 가정, 매 사이클 1건만 매수
    # 첫 신호 발생 종목만 진입. 5/19 timestamp 순서:
    #   09:05 - 001430·005500이 14:33이라 09:05에 274090·036930·356680·080220·069500 동시 매수 시도
    #   사이클당 1건 → 가장 강한 신호 1건만 (best_pnl 기준 — 추정 274090 또는 080220)
    print("=" * 130)
    print("P8 시나리오 — SIDEWAYS = max_buy 1, 09:05 첫 사이클 1건 + 다음 사이클들 1건씩")
    print("=" * 130)
    selected = [
        ("274090", "덕산테코피아", "09:05", 232),  # 강한 모멘텀 (best_pnl 가정 최고)
        ("080220", "제주반도체",   "09:05", 27),   # 다음 사이클 (or DCA 우선)
        ("027360", "아주IB투자",   "09:16", 358),  # 09:16 새 신호
        ("005500", "SK증권",      "14:33", 276),  # 14:33 새 신호
        ("069500", "KODEX 200",  "09:05", 22),   # DCA (사이클당 1건 외)
    ]
    grand_p8 = 0.0
    for sym, name, ts_str, qty in selected:
        bars = await fetcher.fetch_minute_history(symbol=sym, target_business_days=1)
        d519 = [b for b in bars if b.timestamp.strftime("%Y-%m-%d") == "2026-05-19"]
        if not d519: continue
        entry_bar = next((b for b in d519 if b.timestamp.astimezone(KST).strftime("%H:%M") == ts_str), None)
        if not entry_bar: continue
        entry_price = float(entry_bar.close)
        events = simulate(entry_price, qty, d519, entry_bar.timestamp.astimezone(KST))
        n = net_of(events, entry_price, qty)
        grand_p8 += n
        ev_brief = " → ".join(f"{e['ts']} {e['sig']}({e['qty']}@{int(e['price']):,},{e['rate']:+.1f}%)" for e in events[:3])
        print(f"  {sym} {name[:10]:<14} {ts_str:<6} {qty:>4} {entry_price:>9,.0f}  {ev_brief:<55} {n:>+11,.0f}")
    print("-" * 130)
    print(f"  P6+P7+P8 합계 (5 종목 선별)  {grand_p8:>+11,.0f}")
    print()
    print(f"비교:")
    print(f"  5/19 실제                       net  -1,029,626")
    print(f"  P6+P7 (T1만, 8 종목)            net {grand:>+11,.0f}  변화 {grand + 1029626:>+11,.0f}")
    print(f"  P6+P7+P8 (사이클당 1건, 5종목)  net {grand_p8:>+11,.0f}  변화 {grand_p8 + 1029626:>+11,.0f}")


if __name__ == "__main__":
    asyncio.run(main())
