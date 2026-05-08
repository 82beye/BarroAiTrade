"""BAR-OPS-29 — 시뮬(예측) PnL vs 실현 PnL 비교.

simulation_log.csv 의 예측 PnL 과 ka10073 실현 PnL 을 종목별로 비교 →
전략 정확도 + bias (양호/과대/과소) 측정.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from backend.core.gateway.kiwoom_native_account import RealizedPnLEntry
from backend.core.journal.simulation_log import SimulationLogEntry


@dataclass(frozen=True)
class SymbolDiff:
    """종목별 시뮬 vs 실현."""
    symbol: str
    name: str
    sim_pnl: Decimal             # 시뮬 누적 (전략별 합산)
    real_pnl: Decimal            # 실현 누적
    diff: Decimal                # real - sim
    diff_pct: Optional[Decimal]  # diff / |sim| * 100 (sim==0 시 None)
    sim_trades: int
    real_trades: int
    bias: str                    # "양호" / "과대 시뮬" / "과소 시뮬" / "신호 없음"


def _bias(sim: Decimal, real: Decimal) -> str:
    """예측 정확도 판정.

    양수 시뮬 (이익 예측):
      real ≥ sim*0.80 → 양호
      real < sim*0.80 → 과대 시뮬 (예측이 너무 낙관)

    음수 시뮬 (손실 예측):
      real ≥ sim*1.20 → 양호 (실 손실 ≤ 예측 손실 +20%)
      real < sim*1.20 → 과소 시뮬 (실 손실이 예측보다 큼)

    sim == 0 → 신호 없음
    """
    if sim == 0:
        return "신호 없음"
    if sim > 0:
        threshold = sim * Decimal("0.80")
        return "양호" if real >= threshold else "과대 시뮬"
    # sim < 0 — 손실 예측
    threshold = sim * Decimal("1.20")             # 예: -100 * 1.2 = -120
    return "양호" if real >= threshold else "과소 시뮬"


def compare(
    sim_entries: list[SimulationLogEntry],
    real_entries: list[RealizedPnLEntry],
) -> list[SymbolDiff]:
    """종목별 비교 — diff_pct 절대값 내림차순 정렬."""
    sim_pnl: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    sim_count: dict[str, int] = defaultdict(int)
    sim_name: dict[str, str] = {}
    for e in sim_entries:
        sim_pnl[e.symbol] += Decimal(str(e.pnl))
        sim_count[e.symbol] += e.trades
        if e.symbol not in sim_name and e.name:
            sim_name[e.symbol] = e.name

    real_pnl: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    real_count: dict[str, int] = defaultdict(int)
    real_name: dict[str, str] = {}
    for r in real_entries:
        real_pnl[r.symbol] += r.pnl
        real_count[r.symbol] += 1
        if r.symbol not in real_name and r.name:
            real_name[r.symbol] = r.name

    out: list[SymbolDiff] = []
    for sym in set(sim_pnl) | set(real_pnl):
        sim_v = sim_pnl[sym]
        real_v = real_pnl[sym]
        diff = real_v - sim_v
        if sim_v != 0:
            diff_pct = (diff / abs(sim_v)) * 100
        else:
            diff_pct = None
        out.append(SymbolDiff(
            symbol=sym,
            name=sim_name.get(sym) or real_name.get(sym, ""),
            sim_pnl=sim_v, real_pnl=real_v,
            diff=diff, diff_pct=diff_pct,
            sim_trades=sim_count[sym],
            real_trades=real_count[sym],
            bias=_bias(sim_v, real_v),
        ))
    out.sort(key=lambda d: -abs(d.diff))
    return out


def summarize(diffs: list[SymbolDiff]) -> dict:
    """전체 집계."""
    total_sim = sum((d.sim_pnl for d in diffs), Decimal("0"))
    total_real = sum((d.real_pnl for d in diffs), Decimal("0"))
    counts = defaultdict(int)
    for d in diffs:
        counts[d.bias] += 1
    return {
        "n_symbols": len(diffs),
        "total_sim": total_sim,
        "total_real": total_real,
        "total_diff": total_real - total_sim,
        "bias_counts": dict(counts),
    }


__all__ = ["SymbolDiff", "compare", "summarize"]
