"""5/18 audit + active_positions 재시뮬 — P1~P4 fix 적용 전/후 비교.

각 종목 entry_time/qty 는 5/18 audit 그대로. 1분봉 시퀀스로 ExitPolicy
(evaluate_holding) 매 60초 평가. fix 전 (partial_tp_done reset, trough X) vs
fix 후 (partial_tp_done 보존, trough 추적) 매도 횟수·시점·net 비교.
"""
from __future__ import annotations

import asyncio
import csv
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
from backend.core.risk.holding_evaluator import (
    ExitPolicy, PositionContext, SellSignal, evaluate_holding, resolve_policy,
)

KST = timezone(timedelta(hours=9))


@dataclass
class FakeHolding:
    symbol: str
    name: str
    qty: int
    avg_buy_price: Decimal
    cur_price: Decimal
    pnl: Decimal
    pnl_rate: Decimal


def replay_one(
    symbol: str, name: str, entry_ts: datetime, entry_price: float, qty: int,
    strategy: str, minute_bars: list, *, fix_applied: bool,
) -> list[dict]:
    """한 종목 5/18 일중 매도 평가 재시뮬. event 리스트 반환."""
    cfg_base = ExitPolicy(
        take_profit_pct=Decimal("5.0"),
        stop_loss_pct=Decimal("-4.0"),
        trailing_start_pct=Decimal("3.0"),
        trailing_offset_pct=Decimal("1.5"),
        breakeven_trigger_pct=Decimal("2.5"),
        partial_tp_pct=Decimal("3.5"),
        partial_tp_ratio=Decimal("0.5"),
        hold_days_tighten=5,
        tightened_sl_pct=Decimal("-2.0"),
    )
    policy = resolve_policy(cfg_base, strategy)

    events = []
    remaining = qty
    peak = 0.0
    trough = 0.0
    partial_tp_done = False

    # 60초 폴링 간격 — entry 이후 분봉만
    for bar in minute_bars:
        if bar.timestamp <= entry_ts or remaining <= 0:
            continue
        cur = float(bar.close)
        pnl_rate = (cur - entry_price) / entry_price * 100
        if pnl_rate > peak:
            peak = pnl_rate
        if pnl_rate < trough:
            trough = pnl_rate

        ctx = PositionContext(
            peak_pnl_rate=peak,
            partial_tp_done=partial_tp_done,
            entry_time=entry_ts.isoformat(),
            strategy=strategy,
        )
        h = FakeHolding(
            symbol=symbol, name=name, qty=remaining,
            avg_buy_price=Decimal(str(entry_price)),
            cur_price=Decimal(str(cur)),
            pnl=Decimal(str((cur - entry_price) * remaining)),
            pnl_rate=Decimal(str(pnl_rate)),
        )
        d = evaluate_holding(h, policy, ctx)
        if d.signal == SellSignal.HOLD:
            continue

        sell_qty = int(d.sell_qty) if d.sell_qty > 0 else remaining
        sell_qty = min(sell_qty, remaining)
        events.append({
            "ts": bar.timestamp.astimezone(KST).strftime("%H:%M"),
            "signal": d.signal.value,
            "qty": sell_qty,
            "price": cur,
            "pnl_rate": pnl_rate,
        })
        remaining -= sell_qty

        if d.signal == SellSignal.PARTIAL_TP:
            if fix_applied:
                partial_tp_done = True  # P1 fix — 보존
            # fix 미적용: partial_tp_done 갱신 안 됨 (다음 사이클 reset 시뮬)
        # 전량 매도(TRAILING/SL/TP/BREAKEVEN) → remaining=0 → 종료

    if remaining > 0:
        # 14:50 또는 day_end 가정
        last = minute_bars[-1]
        events.append({
            "ts": last.timestamp.astimezone(KST).strftime("%H:%M"),
            "signal": "day_end",
            "qty": remaining,
            "price": float(last.close),
            "pnl_rate": (float(last.close) - entry_price) / entry_price * 100,
        })

    return events


def summarize(events: list[dict], entry_price: float, qty: int) -> dict:
    gross = sum((e["price"] - entry_price) * e["qty"] for e in events)
    comm = sum(e["price"] * e["qty"] * 0.00015 for e in events) + entry_price * qty * 0.00015
    tax = sum(e["price"] * e["qty"] * 0.0018 for e in events)
    return {
        "n_events": len(events),
        "gross": gross, "comm": comm, "tax": tax,
        "net": gross - comm - tax,
    }


async def main():
    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    # 5/18 주요 종목 entry (audit 기반)
    cases = [
        # symbol, name, entry_ts_kst, entry_price(분봉 close 추정), qty, strategy
        ("100790", "미래에셋벤처투자", "09:08", 132, "swing_38"),
        ("005930", "삼성전자",        "09:56",  18, "swing_38"),
        ("010170", "신화실업",        "10:47", 318, "swing_38"),
        ("122630", "KODEX 레버리지",  "11:23",  32, "gold_zone"),
        ("069500", "KODEX 200",     "11:31",  44, "gold_zone"),
        ("080220", "제주반도체",      "12:58",  54, "swing_38"),
    ]

    print("=" * 110)
    print("5/18 audit 재시뮬 — P1·P2 fix 적용 전/후 (ExitPolicy 매 60s 평가)")
    print("=" * 110)

    for sym, name, ts_str, qty, strat in cases:
        bars = await fetcher.fetch_minute_history(symbol=sym, target_business_days=2)
        d518 = [b for b in bars if b.timestamp.strftime("%Y-%m-%d") == "2026-05-18"]
        if not d518:
            print(f"\n[{sym} {name}] 5/18 분봉 없음 — skip")
            continue
        entry_dt = datetime.strptime(f"2026-05-18 {ts_str}:00", "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        # entry_price: entry_dt 분봉 close
        entry_bar = next((b for b in d518 if b.timestamp.astimezone(KST).strftime("%H:%M") == ts_str), d518[0])
        entry_price = float(entry_bar.close)

        # fix 미적용 (partial_tp_done 매번 reset)
        ev_before = replay_one(sym, name, entry_bar.timestamp, entry_price, qty, strat, d518, fix_applied=False)
        # fix 적용 (partial_tp_done 보존)
        ev_after = replay_one(sym, name, entry_bar.timestamp, entry_price, qty, strat, d518, fix_applied=True)
        sb = summarize(ev_before, entry_price, qty)
        sa = summarize(ev_after, entry_price, qty)

        print(f"\n[{sym} {name}]  entry {ts_str} @{entry_price:,.0f}원 ({qty}주, {strat})")
        print(f"  fix 전 events: {sb['n_events']}건  net {sb['net']:>+12,.0f}원")
        for e in ev_before[:6]:
            print(f"    {e['ts']} {e['signal']:<12} qty={e['qty']:>3} @{e['price']:,.0f} ({e['pnl_rate']:+.1f}%)")
        if len(ev_before) > 6:
            print(f"    ... ({len(ev_before) - 6}건 더)")
        print(f"  fix 후 events: {sa['n_events']}건  net {sa['net']:>+12,.0f}원   변화 {sa['net'] - sb['net']:>+10,.0f}")
        for e in ev_after:
            print(f"    {e['ts']} {e['signal']:<12} qty={e['qty']:>3} @{e['price']:,.0f} ({e['pnl_rate']:+.1f}%)")


if __name__ == "__main__":
    asyncio.run(main())
