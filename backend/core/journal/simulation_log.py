"""BAR-OPS-13 — 시뮬 결과 영속화.

매일 주도주 시뮬 결과를 CSV append → 시계열 추적·비교 가능.

CSV 컬럼:
    run_at, mode, symbol, name, strategy, candle_count, trades, pnl, win_rate, score, flu_rate

영속화 정책:
- 같은 (run_at-date, symbol, strategy) 중복 X (덮어쓰기)
- 한 시뮬 실행 = 1 run_at timestamp 공유 → 같은 실행분 분석 용이
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

_HEADERS = [
    "run_at", "mode", "symbol", "name", "strategy",
    "candle_count", "trades", "pnl", "win_rate", "score", "flu_rate",
]


@dataclass(frozen=True)
class SimulationLogEntry:
    run_at: str               # ISO 8601
    mode: str                 # daily / minute
    symbol: str
    name: str
    strategy: str
    candle_count: int
    trades: int
    pnl: float
    win_rate: float
    score: float
    flu_rate: float


class SimulationLogger:
    """CSV append 기반. 멀티 프로세스 동시 쓰기는 가정 X (단일 시뮬 실행)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entries: Iterable[SimulationLogEntry]) -> int:
        rows = list(entries)
        if not rows:
            return 0
        new_file = not self.path.exists()
        with open(self.path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(_HEADERS)
            for e in rows:
                w.writerow([
                    e.run_at, e.mode, e.symbol, e.name, e.strategy,
                    e.candle_count, e.trades, f"{e.pnl:.2f}",
                    f"{e.win_rate:.4f}", f"{e.score:.4f}", f"{e.flu_rate:.2f}",
                ])
        return len(rows)

    def read_all(self) -> list[SimulationLogEntry]:
        if not self.path.exists():
            return []
        out: list[SimulationLogEntry] = []
        with open(self.path, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                out.append(SimulationLogEntry(
                    run_at=row["run_at"],
                    mode=row["mode"],
                    symbol=row["symbol"],
                    name=row["name"],
                    strategy=row["strategy"],
                    candle_count=int(row["candle_count"]),
                    trades=int(row["trades"]),
                    pnl=float(row["pnl"]),
                    win_rate=float(row["win_rate"]),
                    score=float(row["score"]),
                    flu_rate=float(row["flu_rate"]),
                ))
        return out


def summarize_by_strategy(entries: list[SimulationLogEntry]) -> dict[str, dict]:
    """전략별 집계 — total_pnl, total_trades, weighted_win_rate, run 수."""
    out: dict[str, dict] = {}
    for e in entries:
        s = out.setdefault(
            e.strategy,
            {"runs": 0, "total_pnl": 0.0, "total_trades": 0, "weighted_wr_num": 0.0, "weighted_wr_den": 0.0},
        )
        s["runs"] += 1
        s["total_pnl"] += e.pnl
        s["total_trades"] += e.trades
        s["weighted_wr_num"] += e.win_rate * e.trades
        s["weighted_wr_den"] += e.trades
    for s in out.values():
        d = s["weighted_wr_den"]
        s["win_rate"] = (s["weighted_wr_num"] / d) if d > 0 else 0.0
        del s["weighted_wr_num"]
        del s["weighted_wr_den"]
    return out


def summarize_by_run(entries: list[SimulationLogEntry]) -> list[dict]:
    """실행(run_at)별 집계 — 시계열 추세."""
    grouped: dict[str, dict] = {}
    for e in entries:
        g = grouped.setdefault(
            e.run_at,
            {"run_at": e.run_at, "mode": e.mode, "total_pnl": 0.0, "total_trades": 0, "symbols": set()},
        )
        g["total_pnl"] += e.pnl
        g["total_trades"] += e.trades
        g["symbols"].add(e.symbol)
    out = []
    for g in grouped.values():
        g["symbol_count"] = len(g["symbols"])
        del g["symbols"]
        out.append(g)
    out.sort(key=lambda x: x["run_at"])
    return out


__all__ = [
    "SimulationLogEntry",
    "SimulationLogger",
    "summarize_by_strategy",
    "summarize_by_run",
]
