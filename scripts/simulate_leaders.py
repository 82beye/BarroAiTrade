"""BAR-OPS-11 — 당일 주도주 자동 선정 + 다종목 시뮬.

단일 종목 지정 X. 키움 자체 OpenAPI 의 거래대금/등락률 ranking 으로 당일
주도주 top N 자동 선정 → 각 종목 일봉/분봉 시뮬 → 통합 리포트.

환경변수 (.env.local):
    KIWOOM_APP_KEY, KIWOOM_APP_SECRET, KIWOOM_BASE_URL

사용:
    python scripts/simulate_leaders.py                          # daily, top 5
    python scripts/simulate_leaders.py --top 10 --mode daily
    python scripts/simulate_leaders.py --mode minute --tic-scope 1
    python scripts/simulate_leaders.py --min-flu 3.0 --top 3    # 등락률 ≥3%
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timezone

from pydantic import SecretStr

from backend.core.backtester import IntradaySimulator
from backend.core.gateway.kiwoom_native_candles import KiwoomNativeCandleFetcher
from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.core.gateway.kiwoom_native_rank import (
    KiwoomNativeLeaderPicker,
    LeaderCandidate,
)
from backend.core.journal.simulation_log import SimulationLogEntry, SimulationLogger


def _build_oauth() -> KiwoomNativeOAuth:
    app_key = os.environ.get("KIWOOM_APP_KEY", "")
    app_secret = os.environ.get("KIWOOM_APP_SECRET", "")
    base_url = os.environ.get("KIWOOM_BASE_URL", "https://mockapi.kiwoom.com")
    if not app_key or not app_secret:
        raise SystemExit(
            "KIWOOM_APP_KEY / KIWOOM_APP_SECRET 환경변수 필요.\n"
            "예: set -a; . ./.env.local; set +a"
        )
    return KiwoomNativeOAuth(
        app_key=SecretStr(app_key),
        app_secret=SecretStr(app_secret),
        base_url=base_url,
    )


async def _run(args) -> int:
    oauth = _build_oauth()
    picker = KiwoomNativeLeaderPicker(
        oauth=oauth,
        min_flu_rate=args.min_flu,
        min_score=args.min_score,
    )
    fetcher = KiwoomNativeCandleFetcher(oauth=oauth)

    print(f"== 당일 주도주 선정 (mode={args.mode}, top={args.top}, min_flu={args.min_flu}%, min_score={args.min_score}) ==")
    leaders: list[LeaderCandidate] = await picker.pick(top_n=args.top)
    if not leaders:
        print("주도주 후보 없음. --min-flu 또는 --min-score 낮춰서 재시도.")
        return 1

    print(f"\n선정된 주도주 {len(leaders)} 종목 (3-factor: 거래대금·등락률·거래량):")
    print(f"  {'rank':>4} {'symbol':<8} {'name':<16} {'price':>10} {'flu%':>7} {'TVrk':>5} {'FRrk':>5} {'VOLrk':>6} {'score':>6}")
    for i, c in enumerate(leaders, 1):
        print(
            f"  {i:>4} {c.symbol:<8} {c.name:<16} {c.cur_price:>10,.0f} "
            f"{c.flu_rate:>+7.2f} {str(c.rank_trade_value or '-'):>5} "
            f"{str(c.rank_flu_rate or '-'):>5} {str(c.rank_volume or '-'):>6} {c.score:>6.3f}"
        )

    # 각 종목 시뮬
    sim = IntradaySimulator()
    strategies = args.strategies.split(",")
    total_pnl = 0.0
    total_trades = 0
    per_strategy_pnl: dict[str, float] = {s: 0.0 for s in strategies}
    log_entries: list[SimulationLogEntry] = []
    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    print(f"\n== 시뮬 실행 ({len(leaders)} 종목 × {len(strategies)} 전략) ==")
    for c in leaders:
        try:
            if args.mode == "daily":
                candles = await fetcher.fetch_daily(symbol=c.symbol)
            else:
                candles = await fetcher.fetch_minute(symbol=c.symbol, tic_scope=args.tic_scope)
        except Exception as e:
            print(f"  {c.symbol} {c.name:<16} fetch failed: {e}")
            continue

        if len(candles) < 31:
            print(f"  {c.symbol} {c.name:<16} 캔들 부족 ({len(candles)} < 31), 스킵")
            continue

        result = sim.run(candles, symbol=c.symbol, strategies=strategies)
        sym_pnl = float(sum(result.pnl_by_strategy.values()))
        total_pnl += sym_pnl
        total_trades += len(result.trades)
        for sid, pnl in result.pnl_by_strategy.items():
            pnl_f = float(pnl)
            per_strategy_pnl[sid] = per_strategy_pnl.get(sid, 0.0) + pnl_f
            sid_trades = [t for t in result.trades if t.strategy_id == sid]
            wr = result.win_rate_by_strategy.get(sid, 0.0)
            log_entries.append(SimulationLogEntry(
                run_at=run_at, mode=args.mode,
                symbol=c.symbol, name=c.name, strategy=sid,
                candle_count=len(candles), trades=len(sid_trades),
                pnl=pnl_f, win_rate=wr, score=c.score, flu_rate=c.flu_rate,
            ))
        print(
            f"  {c.symbol} {c.name:<16} candles={len(candles):>4} "
            f"trades={len(result.trades):>2}  PnL={sym_pnl:>+12,.0f}"
        )

    print(f"\n== 통합 결과 ==")
    print(f"  총 거래   : {total_trades} 건")
    print(f"  총 PnL    : {total_pnl:+,.0f} 원")
    print(f"  전략별 합산:")
    for sid in strategies:
        print(f"    {sid:<25s}: {per_strategy_pnl.get(sid, 0):+,.0f}")

    if args.log and log_entries:
        logger = SimulationLogger(args.log)
        n = logger.append(log_entries)
        total = len(logger.read_all())
        print(f"\n📝 {n}개 entry → {args.log} 영속화 (누적 {total} rows)")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="당일 주도주 자동 선정 시뮬 (BAR-OPS-11)")
    ap.add_argument("--top", type=int, default=5, help="주도주 top N (기본 5)")
    ap.add_argument(
        "--mode", choices=["daily", "minute"], default="daily",
        help="캔들 모드 (기본 daily)",
    )
    ap.add_argument("--tic-scope", default="1", help="minute 분 단위 (1/3/5/10/15/30/45/60)")
    ap.add_argument(
        "--min-flu", type=float, default=1.0,
        help="최소 등락률 필터 %% (기본 1.0)",
    )
    ap.add_argument(
        "--min-score", type=float, default=0.0,
        help="최소 절대 점수 threshold 0~1 (기본 0.0, 강한 시그널만은 0.7+)",
    )
    ap.add_argument(
        "--strategies",
        default="f_zone,sf_zone,gold_zone,swing_38,scalping_consensus",
        help="실행 전략 (comma-separated)",
    )
    ap.add_argument(
        "--log",
        help="시뮬 결과 CSV 영속화 경로 (예: data/simulation_log.csv)",
    )
    args = ap.parse_args()
    sys.exit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
