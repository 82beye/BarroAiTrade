"""일별 매매 승률 분석 — 동적 universe (옵션 B).

기본 _strategy_daily_winrate.py 는 picker top N 을 1회 고정 → 30일 시뮬.
실전 daemon 은 매 interval(180s)마다 picker 재조회 → 종목 교체.

본 스크립트는 절충:
- picker top K (확장 후보군, 기본 10) 가져옴
- 각 종목 일봉 600봉 fetch + IntradaySimulator 시뮬 (모든 trades 수집)
- 각 trade 의 entry_date 전일 시점에서 후보군 내 3-factor ranking 재계산
- top N (기본 5) + min_flu_rate(1.0%) 필터 통과 trade 만 인정 → 일별 승률

3-factor 점수 (picker 알고리즘 동일):
  score = 0.4 × tv_score + 0.3 × fr_score + 0.3 × vol_score
  각 score = 1 - (rank - 1) / 후보군크기
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
    paired = []
    open_entries: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trades:
        key = (t.symbol, t.strategy_id)
        if t.side == "buy":
            open_entries[key].append({"entry": t, "exits": [], "pnl": Decimal("0")})
        else:
            if not open_entries[key]:
                continue
            cur = open_entries[key][0]
            cur["exits"].append(t)
            cur["pnl"] += t.pnl
            if not hasattr(cur["entry"], "_rem"):
                cur["entry"]._rem = cur["entry"].qty
            cur["entry"]._rem -= t.qty
            if cur["entry"]._rem <= 0:
                last = cur["exits"][-1]
                paired.append({
                    "symbol": cur["entry"].symbol,
                    "strategy": cur["entry"].strategy_id,
                    "buy_ts": cur["entry"].timestamp,
                    "sell_ts": last.timestamp,
                    "pnl": cur["pnl"],
                })
                open_entries[key].pop(0)
    return paired


def compute_rank_at(
    target_date: date, daily_by_symbol: dict[str, list],
) -> dict[str, tuple[float, float]]:
    """target_date 시점 후보군 내 3-factor ranking score + flu_pct 반환.

    각 종목의 target_date(또는 가장 가까운 직전 영업일) 일봉으로 계산.
    return: {symbol: (3-factor score, flu_pct)}
    """
    # 각 종목의 target_date 시점 (close, prev_close, volume) 추출
    snapshot: dict[str, tuple[float, float, float]] = {}
    for sym, daily in daily_by_symbol.items():
        idx = None
        for i, c in enumerate(daily):
            if c.timestamp.date() <= target_date:
                idx = i
            else:
                break
        if idx is None or idx == 0:
            continue
        cur = daily[idx]
        prev = daily[idx - 1]
        if prev.close <= 0:
            continue
        snapshot[sym] = (cur.close, prev.close, cur.volume)

    if not snapshot:
        return {}

    n = len(snapshot)
    tv = {s: c * v for s, (c, _p, v) in snapshot.items()}            # 거래대금
    fr = {s: (c - p) / p * 100 for s, (c, p, _v) in snapshot.items()}  # 등락률 %
    vol = {s: v for s, (_c, _p, v) in snapshot.items()}              # 거래량

    def ranks(d):
        sorted_syms = sorted(d.items(), key=lambda x: -x[1])
        return {s: i + 1 for i, (s, _) in enumerate(sorted_syms)}

    tv_rank, fr_rank, vol_rank = ranks(tv), ranks(fr), ranks(vol)
    out: dict[str, tuple[float, float]] = {}
    for sym in snapshot:
        tv_s = 1 - (tv_rank[sym] - 1) / n
        fr_s = 1 - (fr_rank[sym] - 1) / n
        vol_s = 1 - (vol_rank[sym] - 1) / n
        score = 0.4 * tv_s + 0.3 * fr_s + 0.3 * vol_s
        out[sym] = (score, fr[sym])
    return out


async def main():
    ap = argparse.ArgumentParser(description="동적 universe 일별 승률 시뮬")
    ap.add_argument("--candidates", type=int, default=10,
                    help="picker 후보군 크기 (기본 10)")
    ap.add_argument("--top", type=int, default=5,
                    help="일별 동적 top N (기본 5, daemon 기본값)")
    ap.add_argument("--days", type=int, default=30)
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
    leaders = await picker.pick(top_n=args.candidates)
    name_by = {c.symbol: c.name for c in leaders}

    daily_by_symbol: dict[str, list] = {}
    trades_by_symbol: dict[str, list] = {}
    for c in leaders:
        candles = await fetcher.fetch_daily(symbol=c.symbol)
        if len(candles) < 32:
            continue
        daily_by_symbol[c.symbol] = candles
        sim = IntradaySimulator(
            warmup_candles=31, position_qty=Decimal("100"),
            entry_on_next_open=True, exit_on_intrabar=True,
            commission_pct=0.015, tax_pct_on_sell=0.18,
        )
        result = sim.run(candles, symbol=c.symbol, strategies=STRATEGIES)
        trades_by_symbol[c.symbol] = result.trades

    # 후보군 trade 모두 짝 매칭 + 동적 universe 필터 적용
    accepted: list[dict] = []
    rejected: list[dict] = []
    for sym, trades in trades_by_symbol.items():
        for p in pair_trades(trades):
            entry_date = p["buy_ts"].date()
            # picker 는 D-1 종가 기준 → entry 전일 ranking
            rank_date = entry_date - timedelta(days=1)
            rank_map = compute_rank_at(rank_date, daily_by_symbol)
            if sym not in rank_map:
                p["reject_reason"] = "no_rank"
                rejected.append(p)
                continue
            score, flu = rank_map[sym]
            if flu < args.min_flu:
                p["reject_reason"] = f"flu {flu:.1f}<min"
                rejected.append(p)
                continue
            sorted_syms = sorted(rank_map.items(), key=lambda kv: -kv[1][0])
            top_set = {s for s, _ in sorted_syms[: args.top]}
            if sym not in top_set:
                # sym의 ranking 위치
                rank_pos = next(
                    (i + 1 for i, (s, _) in enumerate(sorted_syms) if s == sym), -1
                )
                p["reject_reason"] = f"rank {rank_pos}>top"
                rejected.append(p)
                continue
            p["name"] = name_by.get(sym, "")
            p["rank_score"] = score
            p["rank_flu"] = flu
            accepted.append(p)

    # 일별 통계
    last_date = max(
        (p["sell_ts"].date() for p in accepted + rejected),
        default=date.today(),
    )
    cutoff = last_date - timedelta(days=args.days)
    accepted_in_win = [p for p in accepted if p["sell_ts"].date() >= cutoff]

    by_day: dict[date, list[dict]] = defaultdict(list)
    for p in accepted_in_win:
        by_day[p["sell_ts"].date()].append(p)

    print()
    print("=" * 110)
    print(
        f"동적 universe 일별 승률 — 후보군 {args.candidates}/일별 top {args.top}, "
        f"윈도우 {args.days}일 ({cutoff} ~ {last_date}), trail default ON"
    )
    print("=" * 110)
    print(
        f"  {'일자':<12} {'매매':>4} {'승':>3} {'패':>3} {'승률':>6} "
        f"{'net PnL':>14} {'누적net':>14}  종목"
    )
    print("-" * 110)
    cum_net = Decimal("0")
    total_trades = 0
    total_wins = 0
    win_days = 0
    loss_days = 0
    for d in sorted(by_day.keys()):
        items = by_day[d]
        n = len(items)
        wins = sum(1 for p in items if p["pnl"] > 0)
        day_net = sum((p["pnl"] for p in items), Decimal("0"))
        cum_net += day_net
        total_trades += n
        total_wins += wins
        if day_net > 0:
            win_days += 1
        elif day_net < 0:
            loss_days += 1
        wr = wins / n * 100 if n else 0
        syms = ",".join(sorted({p["symbol"] for p in items}))[:35]
        print(
            f"  {d.isoformat():<12} {n:>4} {wins:>3} {n - wins:>3} "
            f"{wr:>5.0f}% {float(day_net):>+14,.0f} {float(cum_net):>+14,.0f}  {syms}"
        )
    print("-" * 110)
    n_total = len(accepted_in_win)
    n_days = len(by_day)
    if n_total:
        overall_wr = total_wins / n_total * 100
        day_wr = win_days / n_days * 100 if n_days else 0
        print(
            f"  {'합계':<12} {n_total:>4} {total_wins:>3} {n_total - total_wins:>3} "
            f"{overall_wr:>5.1f}% {float(cum_net):>+14,.0f}"
        )
        print()
        print(f"일별 승률 (각 trade)        : {total_wins}/{n_total} = {overall_wr:.1f}%")
        print(
            f"수익 일 / 손실 일           : {win_days}/{loss_days} "
            f"(수익 일 비율 {day_wr:.1f}%)"
        )
        print(f"활동 일수                   : {n_days}일 (윈도우 {args.days}일 중)")
    else:
        print("  진입 trade 없음")

    # rejected 통계 — 어떤 이유로 후보군 trade가 top N 안에 안 들어왔나
    rejected_in_win = [p for p in rejected if p["sell_ts"].date() >= cutoff]
    print()
    print(f"  rejected trades (top {args.top} 밖): {len(rejected_in_win)}건")
    by_reason: dict[str, int] = defaultdict(int)
    by_reason_pnl: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for p in rejected_in_win:
        r = p.get("reject_reason", "?")
        # rank 숫자 grouping
        if r.startswith("rank"):
            key = "rank>top"
        else:
            key = r
        by_reason[key] += 1
        by_reason_pnl[key] += p["pnl"]
    for k, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<18}: {n:>3}건  (놓친 net {float(by_reason_pnl[k]):>+,.0f})")


if __name__ == "__main__":
    asyncio.run(main())
