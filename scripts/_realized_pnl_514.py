"""5/14 게이트 추천 종목 — best_strategy 실현손익 + 성과지표 (P1 갭6 적용).

simulate_leaders.py(--check-balance, top 15) 게이트 통과 10종목을 추천 qty 로
매수했다고 가정. 각 종목 best_strategy(5전략 중 최고 누적 PnL)로 시뮬.

P1 갭6: minute 모드에서 '전체 분봉으로 시뮬 → 마지막 거래일만 집계'
(compute_metrics period 슬라이스). 당일 분봉만 잘라 시뮬하면 앞 ~60봉이
지표 워밍업으로 죽는 문제를 해소 — 워밍업은 데이터 시작부터, 집계만 당일.

--mode daily : 600봉 일봉 누적
--mode minute: 키움 분봉 — 전체로 시뮬, --today-only 면 마지막 거래일만 집계

사용:
    set -a; . ./.env.local; set +a
    venv/bin/python scripts/_realized_pnl_514.py --mode minute
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

from backend.core.backtester import IntradaySimulator, compute_metrics
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

# 2026-05-14 자금 게이트(균등 분배, top 15) 통과 10종목 + 추천 qty
RECOMMENDED = [
    ("005930", "삼성전자", 10),
    ("047040", "대우건설", 92),
    ("069500", "KODEX 200", 24),
    ("122630", "KODEX 레버리지", 17),
    ("034220", "LG디스플레이", 196),
    ("009830", "한화솔루션", 63),
    ("088350", "한화생명", 548),
    ("233740", "KODEX 코스닥150레버리지", 181),
    ("322000", "HD현대에너지솔루션", 12),
    ("196170", "알테오젠", 7),
]
STRATEGIES = ["f_zone", "sf_zone", "gold_zone", "swing_38", "scalping_consensus"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "minute"], default="minute")
    ap.add_argument("--tic-scope", default="1")
    ap.add_argument(
        "--today-only", action="store_true", default=True,
        help="minute 모드 — 전체 시뮬 후 마지막 거래일만 집계 (갭6)",
    )
    ap.add_argument("--no-today-only", action="store_false", dest="today_only")
    args = ap.parse_args()

    oauth = KiwoomNativeOAuth(
        app_key=SecretStr(os.environ["KIWOOM_APP_KEY"]),
        app_secret=SecretStr(os.environ["KIWOOM_APP_SECRET"]),
        base_url=os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com"),
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    if args.mode == "daily":
        label = "일봉 600봉 누적"
    elif args.today_only:
        label = f"{args.tic_scope}분봉 — 전체 시뮬 + 마지막 거래일만 집계 (갭6)"
    else:
        label = f"{args.tic_scope}분봉 — 전체 집계"

    print("=" * 104)
    print(f"5/14 게이트 추천 10종목 — best_strategy 실현손익 + 성과지표 ({label})")
    print("(SL 우선 체결 + 수수료 0.015%/leg + 매도세 0.18%)")
    print("=" * 104)
    print(
        f"  {'종목':<16} {'qty':>5}  {'best_strategy':<16} {'거래':>5} "
        f"{'승률':>6} {'PF':>7} {'MDD':>13} {'실현손익':>14}"
    )
    print("-" * 104)

    total = Decimal("0")
    rows = []
    for sym, name, qty in RECOMMENDED:
        if args.mode == "daily":
            candles = await fetcher.fetch_daily(symbol=sym)
        else:
            candles = await fetcher.fetch_minute(symbol=sym, tic_scope=args.tic_scope)

        if len(candles) < 32:
            print(f"  {name:<16} {qty:>5}  캔들 부족 ({len(candles)}봉) — 스킵")
            continue

        sim = IntradaySimulator(
            warmup_candles=31,
            position_qty=Decimal(str(qty)),
            entry_on_next_open=True,
            exit_on_intrabar=True,
            commission_pct=0.015,
            tax_pct_on_sell=0.18,
        )
        result = sim.run(candles, symbol=sym, strategies=STRATEGIES)

        # best_strategy = 전체 누적 PnL 최고 (운영 로직과 일치)
        pnls = {s: float(result.pnl_by_strategy.get(s, 0)) for s in STRATEGIES}
        best = max(pnls, key=pnls.get)
        best_trades = [t for t in result.trades if t.strategy_id == best]

        # 갭6: minute + today_only → 전체 시뮬 후 마지막 거래일만 집계
        period = None
        if args.mode == "minute" and args.today_only:
            last_date = candles[-1].timestamp.date()
            period = (last_date, last_date)

        m = compute_metrics(best_trades, period=period)
        total += m.total_pnl
        rows.append((name, qty, best, m))

        pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
        print(
            f"  {name:<16} {qty:>5}  {best:<16} {m.total_trades:>5} "
            f"{m.win_rate * 100:>5.0f}% {pf:>7} {float(m.max_drawdown):>13,.0f} "
            f"{float(m.total_pnl):>+14,.0f}"
        )

    print("-" * 104)
    print(f"  {'합계 실현손익':<60}{float(total):>+14,.0f}")
    if rows:
        win = sum(1 for *_, m in rows if m.total_pnl > 0)
        print(f"  수익 종목 {win}/{len(rows)}")
    print("=" * 104)


if __name__ == "__main__":
    asyncio.run(main())
