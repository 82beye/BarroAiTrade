"""IntradaySimulator 성과 지표 + equity curve (P1 — 갭2/갭6).

IntradaySimulator 의 TradeRecord 리스트(pnl 포함)로부터 트레이딩뷰 등급 성과
지표를 산출한다. run() 을 건드리지 않는 사후 분석 레이어.

- 갭2: equity curve + MDD + Profit Factor + expectancy + Sharpe + 승률
- 갭6: period 파라미터로 "전체 시뮬 → 대상 기간만 집계" 슬라이스 지원

position_qty 고정 시뮬 기준 단리 누적 PnL 곡선. 복리·자본곡선은 P2(포트폴리오)에서.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from backend.core.backtester.intraday_simulator import TradeRecord


@dataclass(frozen=True)
class PerformanceMetrics:
    """청산(sell) 거래 기반 성과 지표 — 단리 누적 PnL 기준."""

    total_trades: int = 0                       # 청산(sell) 거래 수
    win_trades: int = 0
    lose_trades: int = 0
    win_rate: float = 0.0                       # 0~1
    total_pnl: Decimal = Decimal("0")
    avg_pnl: Decimal = Decimal("0")             # expectancy — 거래당 기대손익
    avg_win: Decimal = Decimal("0")
    avg_loss: Decimal = Decimal("0")            # 음수
    profit_factor: float = 0.0                  # 총이익 / |총손실|
    max_drawdown: Decimal = Decimal("0")        # 누적 PnL 곡선 peak 대비 최대 낙폭 (금액)
    max_drawdown_pct: float = 0.0               # peak 대비 % (peak>0 일 때만)
    sharpe_ratio: float = 0.0                   # 거래 단위 Sharpe (연율 250)
    equity_curve: list[Decimal] = field(default_factory=list)  # 0 시작 누적 PnL


def compute_metrics(
    trades: list[TradeRecord],
    *,
    period: tuple[date, date] | None = None,
) -> PerformanceMetrics:
    """청산(sell) 거래의 pnl 로부터 성과 지표 산출.

    period=(lo, hi) 지정 시 timestamp.date() 가 [lo, hi] 인 sell 거래만 집계 (갭6).
    buy 거래는 pnl=0 이므로 자동 제외 (side == "sell" 필터).
    """
    sells = [t for t in trades if t.side == "sell"]
    if period is not None:
        lo, hi = period
        sells = [t for t in sells if lo <= t.timestamp.date() <= hi]
    sells.sort(key=lambda t: t.timestamp)

    if not sells:
        return PerformanceMetrics()

    pnls = [t.pnl for t in sells]
    n = len(pnls)
    total = sum(pnls, Decimal("0"))
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_profit = sum(wins, Decimal("0"))
    total_loss = abs(sum(losses, Decimal("0")))
    if total_loss > 0:
        profit_factor = float(total_profit / total_loss)
    else:
        profit_factor = float("inf") if total_profit > 0 else 0.0

    # equity curve (0 시작 단리 누적 PnL) + MDD
    curve: list[Decimal] = []
    running = Decimal("0")
    for p in pnls:
        running += p
        curve.append(running)

    peak = Decimal("0")
    mdd = Decimal("0")
    mdd_pct = 0.0
    for v in curve:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
            mdd_pct = float(dd / peak) if peak > 0 else 0.0

    return PerformanceMetrics(
        total_trades=n,
        win_trades=len(wins),
        lose_trades=len(losses),
        win_rate=len(wins) / n,
        total_pnl=total,
        avg_pnl=total / n,
        avg_win=(total_profit / len(wins)) if wins else Decimal("0"),
        avg_loss=(sum(losses, Decimal("0")) / len(losses)) if losses else Decimal("0"),
        profit_factor=profit_factor,
        max_drawdown=mdd,
        max_drawdown_pct=mdd_pct,
        sharpe_ratio=_sharpe([float(p) for p in pnls]),
        equity_curve=curve,
    )


def _sharpe(returns: list[float], risk_free_rate: float = 0.02) -> float:
    """거래 단위 Sharpe — 연율화(거래일 250 가정). numpy 비의존."""
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return round((mean - risk_free_rate / 250) / std * math.sqrt(250), 4)


__all__ = ["PerformanceMetrics", "compute_metrics"]
