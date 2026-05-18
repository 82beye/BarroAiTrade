"""5/18 잔여 holding 정책 보강 옵션 시뮬 — A/B/C/D 비교.

A. baseline (fix 후, partial 1번 + trailing 1.5%)
B. opt1: 2차 partial @5% 추가
C. opt2: trailing_offset 1.5% → 1.0% (잔여 holding 시 적용)
D. opt3: trailing_start 3.0% → 2.0% (1차 partial 후 완화)

각 종목별 1분봉 시퀀스 + ExitPolicy 평가. 잔여 holding 의 net 변화 측정.
"""
from __future__ import annotations

import asyncio
import os
import sys
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

KST = timezone(timedelta(hours=9))


@dataclass
class ExitParams:
    take_profit_pct: float = 5.0
    stop_loss_pct: float = -4.0
    trailing_start_pct: float = 3.0
    trailing_offset_pct: float = 1.5
    breakeven_trigger_pct: float = 2.5
    partial_tp_pct: float = 3.5
    partial_tp_ratio: float = 0.5
    # opt1: 2차 partial
    partial_tp2_pct: float = 0.0          # 0 = disabled
    partial_tp2_ratio: float = 0.0
    # opt2: 잔여 trailing offset 축소
    trailing_offset_after_partial: float = 0.0  # 0 = same
    # opt3: trailing_start 동적
    trailing_start_after_partial: float = 0.0   # 0 = same


def replay(entry_price: float, qty: int, bars: list, params: ExitParams) -> list[dict]:
    events = []
    remaining = qty
    peak = 0.0
    partial1_done = False
    partial2_done = False
    for bar in bars:
        if remaining <= 0:
            break
        cur = float(bar.close)
        rate = (cur - entry_price) / entry_price * 100
        if rate > peak:
            peak = rate

        # 잔여 holding 시 동적 trailing 파라미터 적용
        eff_trail_start = params.trailing_start_pct
        eff_trail_offset = params.trailing_offset_pct
        if partial1_done:
            if params.trailing_start_after_partial > 0:
                eff_trail_start = params.trailing_start_after_partial
            if params.trailing_offset_after_partial > 0:
                eff_trail_offset = params.trailing_offset_after_partial

        # 1. trailing
        if peak >= eff_trail_start and rate < peak - eff_trail_offset:
            events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                           "sig": "trail", "qty": remaining, "price": cur, "rate": rate})
            remaining = 0
            break
        # 2. breakeven
        if peak >= params.breakeven_trigger_pct and rate <= 0:
            events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                           "sig": "break", "qty": remaining, "price": cur, "rate": rate})
            remaining = 0
            break
        # 3. partial TP2 (opt1) — peak 기반
        if (params.partial_tp2_pct > 0
                and not partial2_done and partial1_done
                and rate >= params.partial_tp2_pct
                and rate < params.take_profit_pct):
            sell_qty = max(1, int(remaining * params.partial_tp2_ratio))
            events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                           "sig": "ptp2", "qty": sell_qty, "price": cur, "rate": rate})
            remaining -= sell_qty
            partial2_done = True
            continue
        # 4. partial TP1
        if (not partial1_done and rate >= params.partial_tp_pct
                and rate < params.take_profit_pct):
            sell_qty = max(1, int(remaining * params.partial_tp_ratio))
            events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                           "sig": "ptp1", "qty": sell_qty, "price": cur, "rate": rate})
            remaining -= sell_qty
            partial1_done = True
            continue
        # 5. full TP
        if rate >= params.take_profit_pct:
            events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                           "sig": "tp", "qty": remaining, "price": cur, "rate": rate})
            remaining = 0
            break
        # 6. SL
        if rate <= params.stop_loss_pct:
            events.append({"ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
                           "sig": "sl", "qty": remaining, "price": cur, "rate": rate})
            remaining = 0
            break

    if remaining > 0:
        last = bars[-1]
        events.append({"ts": last.timestamp.astimezone(KST).strftime("%H:%M"),
                       "sig": "end", "qty": remaining, "price": float(last.close),
                       "rate": (float(last.close) - entry_price) / entry_price * 100})
    return events


def net_of(events: list[dict], entry_price: float, qty: int) -> float:
    gross = sum((e["price"] - entry_price) * e["qty"] for e in events)
    comm = entry_price * qty * 0.00015 + sum(e["price"] * e["qty"] * 0.00015 for e in events)
    tax = sum(e["price"] * e["qty"] * 0.0018 for e in events)
    return gross - comm - tax


async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    cases = [
        ("100790", "미래에셋벤처", "09:08", 132),
        ("005930", "삼성전자",    "09:56", 18),
        ("080220", "제주반도체",   "12:58", 54),
    ]

    options = {
        "A. baseline (P1 fix 후)": ExitParams(),
        "B. opt1 2차 PTP@5% (25%)": ExitParams(partial_tp2_pct=5.0, partial_tp2_ratio=0.5),
        "C. opt2 trail_offset 1.0%(잔여)": ExitParams(trailing_offset_after_partial=1.0),
        "D. opt3 trail_start 2.0%(잔여)": ExitParams(trailing_start_after_partial=2.0),
        "E. opt1+2+3 결합": ExitParams(
            partial_tp2_pct=5.0, partial_tp2_ratio=0.5,
            trailing_offset_after_partial=1.0,
            trailing_start_after_partial=2.0,
        ),
    }

    summary: dict[str, dict[str, float]] = {opt: {} for opt in options}
    print("=" * 110)
    print("5/18 잔여 holding 정책 옵션 비교")
    print("=" * 110)

    for sym, name, ts_str, qty in cases:
        bars = await fetcher.fetch_minute_history(symbol=sym, target_business_days=2)
        d518 = [b for b in bars if b.timestamp.strftime("%Y-%m-%d") == "2026-05-18"]
        entry_bar = next((b for b in d518 if b.timestamp.astimezone(KST).strftime("%H:%M") == ts_str), d518[0])
        entry_price = float(entry_bar.close)
        after = [b for b in d518 if b.timestamp > entry_bar.timestamp]
        day_high = max(b.high for b in d518)
        day_high_pct = (day_high - entry_price) / entry_price * 100

        print(f"\n[{sym} {name}]  entry {ts_str} @{entry_price:,.0f} ({qty}주)  "
              f"일중 H {day_high:,.0f} (+{day_high_pct:.1f}%)")
        for label, params in options.items():
            ev = replay(entry_price, qty, after, params)
            n = net_of(ev, entry_price, qty)
            summary[label][sym] = n
            ev_brief = " → ".join(f"{e['ts']} {e['sig']}({e['qty']}@{e['price']:,.0f},{e['rate']:+.1f}%)" for e in ev)
            print(f"  {label:<40} net {n:>+12,.0f}  {ev_brief}")

    # 종합
    print("\n" + "=" * 110)
    print("종합 — 옵션별 3종목 net 합계")
    print("=" * 110)
    base = sum(summary["A. baseline (P1 fix 후)"].values())
    for label, syms in summary.items():
        total = sum(syms.values())
        delta = total - base
        print(f"  {label:<40} {total:>+12,.0f}  (vs baseline {delta:>+10,.0f})")


if __name__ == "__main__":
    asyncio.run(main())
