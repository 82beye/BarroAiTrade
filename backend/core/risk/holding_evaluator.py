"""BAR-OPS-20 — 보유 종목 매도 시그널 평가.

OPS-15 잔고 조회로 가져온 holdings 의 손익률을 검사:
- pnl_rate >= take_profit_pct → TP (익절 추천)
- pnl_rate <= stop_loss_pct  → SL (손절 추천)
- 그 외 → HOLD

OPS-17 LiveOrderGate 와 결합 시 자동 매도 가능.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from backend.core.gateway.kiwoom_native_account import HoldingPosition


class SellSignal(str, Enum):
    HOLD = "hold"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"


@dataclass(frozen=True)
class HoldingDecision:
    symbol: str
    name: str
    qty: int
    avg_buy_price: Decimal
    cur_price: Decimal
    pnl: Decimal
    pnl_rate: Decimal               # 수익률 % (signed)
    signal: SellSignal
    reason: str


@dataclass(frozen=True)
class ExitPolicy:
    take_profit_pct: Decimal = Decimal("5.0")
    stop_loss_pct: Decimal = Decimal("-2.0")


def evaluate_holding(
    h: HoldingPosition,
    policy: ExitPolicy = ExitPolicy(),
) -> HoldingDecision:
    rate = h.pnl_rate
    if rate >= policy.take_profit_pct:
        signal = SellSignal.TAKE_PROFIT
        reason = f"수익률 {rate}% ≥ TP {policy.take_profit_pct}% → 익절 추천"
    elif rate <= policy.stop_loss_pct:
        signal = SellSignal.STOP_LOSS
        reason = f"수익률 {rate}% ≤ SL {policy.stop_loss_pct}% → 손절 추천"
    else:
        signal = SellSignal.HOLD
        reason = f"수익률 {rate}% — 보유 유지 ({policy.stop_loss_pct}% < x < {policy.take_profit_pct}%)"
    return HoldingDecision(
        symbol=h.symbol, name=h.name, qty=h.qty,
        avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
        pnl=h.pnl, pnl_rate=h.pnl_rate,
        signal=signal, reason=reason,
    )


def evaluate_all(
    holdings: list[HoldingPosition],
    policy: ExitPolicy = ExitPolicy(),
) -> list[HoldingDecision]:
    return [evaluate_holding(h, policy) for h in holdings]


def render_decisions_table(decisions: list[HoldingDecision]) -> str:
    """markdown 표."""
    lines = [
        "| symbol | name | qty | avg | cur | pnl_rate | signal |",
        "|--------|------|----:|----:|----:|---------:|--------|",
    ]
    for d in decisions:
        sig_label = {
            SellSignal.HOLD: "HOLD",
            SellSignal.TAKE_PROFIT: "✅ TP",
            SellSignal.STOP_LOSS: "🛑 SL",
        }[d.signal]
        lines.append(
            f"| {d.symbol} | {d.name} | {d.qty:,} | "
            f"{int(d.avg_buy_price):,} | {int(d.cur_price):,} | "
            f"{float(d.pnl_rate):+.2f}% | {sig_label} |"
        )
    return "\n".join(lines)


__all__ = [
    "ExitPolicy", "HoldingDecision", "SellSignal",
    "evaluate_holding", "evaluate_all", "render_decisions_table",
]
