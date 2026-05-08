"""BAR-OPS-13 — 시뮬 누적 history 분석 CLI.

사용:
    python scripts/show_simulation_history.py data/simulation_log.csv
    python scripts/show_simulation_history.py data/simulation_log.csv --by run
    python scripts/show_simulation_history.py data/simulation_log.csv --by strategy
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.core.journal.simulation_log import (
    SimulationLogger,
    summarize_by_run,
    summarize_by_strategy,
)


def _print_strategy(entries) -> None:
    summary = summarize_by_strategy(entries)
    print(f"== 전략별 누적 ({len(summary)} 전략) ==")
    print(f"  {'strategy':<25s} {'runs':>4} {'trades':>7} {'win%':>6} {'total_pnl':>14}")
    rows = sorted(summary.items(), key=lambda x: -x[1]["total_pnl"])
    for sid, s in rows:
        print(
            f"  {sid:<25s} {s['runs']:>4} {s['total_trades']:>7} "
            f"{s['win_rate']*100:>5.1f}% {s['total_pnl']:>+14,.0f}"
        )


def _print_run(entries) -> None:
    runs = summarize_by_run(entries)
    print(f"== 실행별 시계열 ({len(runs)} runs) ==")
    print(f"  {'run_at':<26s} {'mode':<7s} {'symbols':>7} {'trades':>7} {'total_pnl':>14}")
    for r in runs:
        print(
            f"  {r['run_at']:<26s} {r['mode']:<7s} {r['symbol_count']:>7} "
            f"{r['total_trades']:>7} {r['total_pnl']:>+14,.0f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="시뮬 누적 history 분석 (BAR-OPS-13)")
    ap.add_argument("path", help="시뮬 로그 CSV 경로")
    ap.add_argument(
        "--by", choices=["strategy", "run", "both"], default="both",
        help="집계 기준 (기본 both)",
    )
    args = ap.parse_args()

    logger = SimulationLogger(args.path)
    entries = logger.read_all()
    if not entries:
        print(f"비어있음: {args.path}")
        return
    print(f"총 {len(entries)} entries\n")
    if args.by in ("strategy", "both"):
        _print_strategy(entries)
        print()
    if args.by in ("run", "both"):
        _print_run(entries)


if __name__ == "__main__":
    main()
