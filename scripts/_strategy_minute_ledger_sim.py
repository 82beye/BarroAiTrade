"""일봉 시그널 + 1분봉 진입·청산 하이브리드 시뮬 — Trade Ledger.

- 일봉 600봉으로 strategy.analyze 평가 (warmup=31)
- 신호 발생일 D → 다음 영업일 D+1 1분봉 첫 봉 시가 진입
- D+1 1분봉 high/low 로 SL/TP/breakeven 평가 (IntradaySimulator._evaluate_intrabar)
- 14:50 도달 시 time_exit (분봉 close), 미청산 시 day_end (15:30 close)
- 부분 청산 지원 — TP1·TP2·TP3·TRAIL_STOP·SL·time_exit 각 event 기록
- ledger: 종목/전략/매수시각/수량/매수가 → 매도 events 요약

옵션 (모두 default OFF, --enable-X 명시 시 적용):
  --trail        : 5단계 변동성 트레일링 (B, ai-trade 패턴)
  --time-sl      : 시간별 단계 SL (A, 0~2분-1.5%/5분-2%/5분+-2.5%)
  --high-mom-sl-mult M  : 진입 직전 일봉 change_pct ≥ 15% 시 SL 폭 ×M 완화 (C)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import warnings
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore", category=DeprecationWarning)

from pydantic import SecretStr

from backend.core.backtester.intraday_simulator import (
    IntradaySimulator,
    TRAIL_STAGES_AITRADE,
    _build_strategies,
    _exit_plan_for_strategy,
)
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import KiwoomNativeLeaderPicker
from backend.models.exit_order import PositionState
from backend.models.market import MarketType
from backend.models.strategy import AnalysisContext

STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38"]
WARMUP = 31
QTY = Decimal("100")
COMMISSION_RATE = Decimal("0.00015")
TAX_RATE = Decimal("0.0018")
TIME_EXIT_H, TIME_EXIT_M = 14, 50

TIME_SL_STAGES = [
    (120, Decimal("-0.015")),
    (300, Decimal("-0.020")),
    (99999, Decimal("-0.025")),
]


def slice_by_date(bars):
    out: dict[date, list] = {}
    for b in bars:
        out.setdefault(b.timestamp.date(), []).append(b)
    return out


def simulate_one(
    daily, minutes_by_date, sym, name, strat_objs, sim_helper, days_window,
    *, use_trail=False, use_time_sl=False, high_mom_sl_mult=None,
):
    available_days = sorted(minutes_by_date.keys())
    if not available_days:
        return []
    last_day = available_days[-1]
    window_start = last_day - timedelta(days=days_window)
    trades = []
    last_exit_ts = None
    for i in range(WARMUP, len(daily) - 1):
        signal_day = daily[i].timestamp.date()
        future = [d for d in available_days if d > signal_day]
        if not future:
            continue
        next_day = future[0]
        if next_day < window_start:
            continue
        day_min = minutes_by_date.get(next_day) or []
        if not day_min:
            continue
        if last_exit_ts and day_min[0].timestamp <= last_exit_ts:
            continue
        window = daily[: i + 1]
        for sid, strat in zip(STRATEGIES, strat_objs):
            ctx = AnalysisContext(
                symbol=sym, name=name, candles=window,
                market_type=MarketType.STOCK,
            )
            try:
                signal = strat.analyze(ctx)
            except Exception:
                signal = None
            if signal is None:
                continue
            entry_bar = day_min[0]
            entry_price = Decimal(str(entry_bar.open))
            plan = _exit_plan_for_strategy(
                sid, entry_price, window, f_zone_atr=False,
                trail_stages=TRAIL_STAGES_AITRADE if use_trail else None,
                time_stages=TIME_SL_STAGES if use_time_sl else None,
                high_momentum_sl_mult=high_mom_sl_mult,
            )
            pos = PositionState(
                symbol=sym, entry_price=entry_price, qty=QTY, initial_qty=QTY,
                entry_time=entry_bar.timestamp,
            )
            events: list[tuple] = []  # (ts, qty, price, reason)
            for m in day_min:
                if m.timestamp <= entry_bar.timestamp:
                    continue
                pos, orders = sim_helper._evaluate_intrabar(pos, plan, m)
                for eo in orders:
                    events.append((m.timestamp, eo.qty, eo.target_price, eo.reason.value))
                if pos.qty <= 0:
                    break
                if (m.timestamp.hour > TIME_EXIT_H or
                        (m.timestamp.hour == TIME_EXIT_H
                         and m.timestamp.minute >= TIME_EXIT_M)):
                    events.append((m.timestamp, pos.qty, Decimal(str(m.close)), "time_exit"))
                    pos = pos.model_copy(update={"qty": Decimal("0")})
                    break
            if pos.qty > 0:
                last = day_min[-1]
                events.append((last.timestamp, pos.qty, Decimal(str(last.close)), "day_end"))
            if not events:
                continue
            buy_comm = (entry_price * QTY * COMMISSION_RATE).quantize(Decimal("1"))
            sell_comm_sum = Decimal("0")
            tax_sum = Decimal("0")
            gross = Decimal("0")
            for ts, qty, price, _r in events:
                gross += (price - entry_price) * qty
                sell_comm_sum += (price * qty * COMMISSION_RATE).quantize(Decimal("1"))
                tax_sum += (price * qty * TAX_RATE).quantize(Decimal("1"))
            net = gross - buy_comm - sell_comm_sum - tax_sum
            trades.append({
                "symbol": sym, "name": name, "strategy": sid,
                "buy_ts": entry_bar.timestamp, "buy_qty": int(QTY),
                "buy_price": entry_price,
                "events": events,
                "gross": gross, "buy_comm": buy_comm, "sell_comm": sell_comm_sum,
                "tax": tax_sum, "net": net,
            })
            last_exit_ts = events[-1][0]
            break
    return trades


def fmt_events(events):
    """events 압축 요약 — 'tp1@104,trail_stop@105.5,...'"""
    return ",".join(f"{r}:{int(q)}@{float(p):,.0f}" for _t, q, p, r in events)


async def main():
    ap = argparse.ArgumentParser(description="일봉 시그널 + 1분봉 진입·청산 ledger")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--min-flu", type=float, default=1.0)
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--trail", action="store_true",
                    help="B. 5단계 트레일링 (TRAIL_STAGES_AITRADE)")
    ap.add_argument("--time-sl", dest="time_sl", action="store_true",
                    help="A. 시간별 단계 SL (0~2분-1.5%/2~5분-2%/5분+-2.5%)")
    ap.add_argument("--high-mom-sl-mult", type=float, default=None,
                    help="C. 진입일 change_pct≥15% 시 SL 폭 ×배율 (예: 1.3)")
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
    sim_helper = IntradaySimulator()
    strat_objs = _build_strategies(STRATEGIES)
    high_mom = Decimal(str(args.high_mom_sl_mult)) if args.high_mom_sl_mult else None

    all_trades = []
    for c in leaders:
        try:
            daily = await fetcher.fetch_daily(symbol=c.symbol)
            minutes = await fetcher.fetch_minute_history(
                symbol=c.symbol, target_business_days=args.days,
            )
        except Exception as e:
            print(f"[SKIP] {c.symbol} {c.name} fetch 실패: {e}")
            continue
        if len(daily) < WARMUP + 1 or not minutes:
            print(f"[SKIP] {c.symbol} {c.name} 데이터 부족")
            continue
        trades = simulate_one(
            daily, slice_by_date(minutes), c.symbol, c.name,
            strat_objs, sim_helper, args.days,
            use_trail=args.trail, use_time_sl=args.time_sl,
            high_mom_sl_mult=high_mom,
        )
        all_trades.extend(trades)

    print()
    print(
        f"옵션: trail={args.trail}  time_sl={args.time_sl}  "
        f"high_mom_sl_mult={args.high_mom_sl_mult}"
    )
    if not all_trades:
        print("[결과] 매매 신호 없음")
        return

    print("=" * 170)
    print(f"Trade Ledger ({args.days}일, {len(all_trades)}건)")
    print("=" * 170)
    print(
        f"  {'종목':<22} {'전략':<10} {'매수시각':<17} {'수량':>4} {'매수가':>9}  →  "
        f"{'청산 events':<70}  {'gross':>10} {'comm':>7} {'tax':>7} {'net':>10}"
    )
    print("-" * 170)
    total_gross = Decimal("0")
    total_comm = Decimal("0")
    total_tax = Decimal("0")
    total_net = Decimal("0")
    by_strategy: dict[str, Decimal] = {}
    by_reason: dict[str, int] = {}
    win_count = 0
    for t in sorted(all_trades, key=lambda x: x["buy_ts"]):
        nm = (t["name"] or "")[:10]
        print(
            f"  {t['symbol']} {nm:<14} {t['strategy']:<10} "
            f"{t['buy_ts'].strftime('%m-%d %H:%M'):<17} {t['buy_qty']:>4} "
            f"{float(t['buy_price']):>9,.0f}  →  "
            f"{fmt_events(t['events']):<70}  "
            f"{float(t['gross']):>+10,.0f} "
            f"{float(t['buy_comm']+t['sell_comm']):>7,.0f} "
            f"{float(t['tax']):>7,.0f} "
            f"{float(t['net']):>+10,.0f}"
        )
        total_gross += t["gross"]
        total_comm += t["buy_comm"] + t["sell_comm"]
        total_tax += t["tax"]
        total_net += t["net"]
        by_strategy[t["strategy"]] = by_strategy.get(t["strategy"], Decimal("0")) + t["net"]
        for _ts, _q, _p, r in t["events"]:
            by_reason[r] = by_reason.get(r, 0) + 1
        if t["net"] > 0:
            win_count += 1
    print("-" * 170)
    print(
        f"  {'합계':<111}  "
        f"{float(total_gross):>+10,.0f} {float(total_comm):>7,.0f} "
        f"{float(total_tax):>7,.0f} {float(total_net):>+10,.0f}"
    )

    print(f"\n전략별 net PnL:")
    for sid, pnl in sorted(by_strategy.items(), key=lambda kv: kv[1], reverse=True):
        print(f"  {sid:<12}: {float(pnl):>+12,.0f}")
    print(f"\n청산 event 사유 (총):")
    for r, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"  {r:<12}: {n} 건")
    n = len(all_trades)
    wr = win_count / n * 100 if n else 0
    print(f"\n승률: {win_count}/{n} ({wr:.1f}%)  |  net 평균/거래: "
          f"{float(total_net / n):>+,.0f} 원")


if __name__ == "__main__":
    asyncio.run(main())
