"""BAR-OPS-19 — 시뮬 결과 / 누적 history markdown 변환.

운영자 일일 모니터링용. CSV 보다 가독성 높은 markdown 표 + 시계열 표시.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Optional

from backend.core.gateway.kiwoom_native_rank import LeaderCandidate
from backend.core.journal.simulation_log import (
    SimulationLogEntry,
    summarize_by_run,
    summarize_by_strategy,
)
from backend.core.risk.balance_gate import RiskGateResult


def _fmt_int(v: int | float | Decimal) -> str:
    return f"{int(v):,}"


def _fmt_signed(v: int | float | Decimal) -> str:
    n = int(v)
    return f"{n:+,}"


def _fmt_pct(v: float | Decimal, decimals: int = 2) -> str:
    return f"{float(v):+.{decimals}f}%"


def render_leader_table(leaders: list[LeaderCandidate]) -> str:
    """주도주 선정 결과 → markdown 표."""
    lines = [
        "| rank | symbol | name | price | flu% | TVrk | FRrk | VOLrk | score |",
        "|------|--------|------|------:|-----:|-----:|-----:|------:|------:|",
    ]
    for i, c in enumerate(leaders, 1):
        lines.append(
            f"| {i} | {c.symbol} | {c.name} | "
            f"{_fmt_int(c.cur_price)} | {_fmt_pct(c.flu_rate)} | "
            f"{c.rank_trade_value or '-'} | {c.rank_flu_rate or '-'} | "
            f"{c.rank_volume or '-'} | {c.score:.3f} |"
        )
    return "\n".join(lines)


def render_simulation_summary(
    total_trades: int,
    total_pnl: float,
    per_strategy_pnl: dict[str, float],
) -> str:
    lines = [
        "| 항목 | 값 |",
        "|------|---:|",
        f"| 총 거래 | {_fmt_int(total_trades)} |",
        f"| 총 PnL | {_fmt_signed(total_pnl)} 원 |",
        "",
        "**전략별 PnL**",
        "",
        "| 전략 | PnL |",
        "|------|---:|",
    ]
    for sid, pnl in per_strategy_pnl.items():
        lines.append(f"| {sid} | {_fmt_signed(pnl)} |")
    return "\n".join(lines)


def render_gate_recommendations(gate: RiskGateResult) -> str:
    lines = [
        "| 항목 | 값 |",
        "|------|---:|",
        f"| 예수금 | {_fmt_int(gate.cash)} 원 |",
        f"| 현재 평가금액 | {_fmt_int(gate.current_eval)} 원 |",
        f"| 진입 가능액 | {_fmt_int(gate.available)} 원 |",
        f"| 종목당 한도 | {_fmt_int(gate.max_per_position)} 원 |",
        f"| 총 보유 한도 | {_fmt_int(gate.max_total_position)} 원 |",
        "",
        "**추천 매수 qty**",
        "",
        "| symbol | name | price | rec_qty | value | 비고 |",
        "|--------|------|------:|--------:|------:|------|",
    ]
    for r in gate.recommendations:
        value = Decimal(r.recommended_qty) * r.cur_price
        tag = r.reason if r.blocked else "OK"
        lines.append(
            f"| {r.symbol} | {r.name} | {_fmt_int(r.cur_price)} | "
            f"{r.recommended_qty:,} | {_fmt_signed(value)} | {tag} |"
        )
    return "\n".join(lines)


def render_history_by_strategy(entries: list[SimulationLogEntry]) -> str:
    summary = summarize_by_strategy(entries)
    lines = [
        "| strategy | runs | trades | win% | total_pnl |",
        "|----------|-----:|-------:|-----:|----------:|",
    ]
    rows = sorted(summary.items(), key=lambda x: -x[1]["total_pnl"])
    for sid, s in rows:
        lines.append(
            f"| {sid} | {s['runs']} | {s['total_trades']} | "
            f"{s['win_rate']*100:.1f}% | {_fmt_signed(s['total_pnl'])} |"
        )
    return "\n".join(lines)


def render_history_by_run(entries: list[SimulationLogEntry]) -> str:
    runs = summarize_by_run(entries)
    lines = [
        "| run_at | mode | symbols | trades | total_pnl |",
        "|--------|------|--------:|-------:|----------:|",
    ]
    for r in runs:
        lines.append(
            f"| {r['run_at']} | {r['mode']} | {r['symbol_count']} | "
            f"{r['total_trades']} | {_fmt_signed(r['total_pnl'])} |"
        )
    return "\n".join(lines)


def render_daily_report(
    *,
    title: str,
    leaders: list[LeaderCandidate],
    total_trades: int,
    total_pnl: float,
    per_strategy_pnl: dict[str, float],
    gate: Optional[RiskGateResult] = None,
    history_entries: Optional[list[SimulationLogEntry]] = None,
    executed_orders: Optional[list[dict]] = None,
) -> str:
    """일일 종합 리포트 markdown."""
    sections = [
        f"# {title}",
        f"_생성: {datetime.now(timezone.utc).isoformat(timespec='seconds')}_",
        "",
        "## 1. 당일 주도주 (3-factor)",
        render_leader_table(leaders),
        "",
        "## 2. 시뮬 결과",
        render_simulation_summary(total_trades, total_pnl, per_strategy_pnl),
    ]
    if gate:
        sections += ["", "## 3. 잔고 + 추천 매수", render_gate_recommendations(gate)]
    if executed_orders:
        sections += ["", "## 4. 주문 실행 결과", _render_execution(executed_orders)]
    if history_entries:
        sections += [
            "",
            "## 5. 누적 history",
            "",
            "### 전략별",
            render_history_by_strategy(history_entries),
            "",
            "### 실행별 시계열",
            render_history_by_run(history_entries),
        ]
    return "\n".join(sections) + "\n"


def _render_execution(orders: Iterable[dict]) -> str:
    lines = [
        "| symbol | name | qty | order_no | status |",
        "|--------|------|----:|----------|--------|",
    ]
    for o in orders:
        lines.append(
            f"| {o['symbol']} | {o.get('name','')} | {o['qty']:,} | "
            f"{o.get('order_no','')} | {o.get('status','')} |"
        )
    return "\n".join(lines)


__all__ = [
    "render_leader_table", "render_simulation_summary",
    "render_gate_recommendations", "render_history_by_strategy",
    "render_history_by_run", "render_daily_report",
]
