"""BAR-OPS-08 — IntradaySimulator.

당일 캔들 데이터 → 5 전략 시뮬레이션. 진입/청산 시점 + PnL.

사용 예:
    candles = load_csv_candles("data/005930_2026-05-08.csv")
    sim = IntradaySimulator()
    result = sim.run(candles, symbol="005930", strategies=["f_zone", "sf_zone"])
    print(result.summary())
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.core.execution.exit_engine import ExitEngine
from backend.models.exit_order import ExitOrder, ExitReason, PositionState
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import (
    AnalysisContext,
    ExitPlan,
    StopLoss,
    TakeProfitTier,
)

logger = logging.getLogger(__name__)


# ─── 결과 모델 ────────────────────────────────────────────


class TradeRecord(BaseModel):
    """단일 매매 기록."""

    model_config = ConfigDict(frozen=True)

    strategy_id: str
    symbol: str
    side: str  # "buy" or "sell"
    qty: Decimal
    price: Decimal
    timestamp: datetime
    reason: str = ""  # entry / tp1 / tp2 / sl / time_exit


class SimulationResult(BaseModel):
    """시뮬레이션 결과 — 전략별 trades + PnL."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    candle_count: int
    strategies_run: list[str]
    trades: list[TradeRecord] = Field(default_factory=list)
    pnl_by_strategy: dict[str, Decimal] = Field(default_factory=dict)
    win_rate_by_strategy: dict[str, float] = Field(default_factory=dict)

    def summary(self) -> str:
        lines = [
            f"=== Simulation: {self.symbol} ({self.candle_count} candles) ===",
            f"Strategies: {', '.join(self.strategies_run)}",
            f"Total trades: {len(self.trades)}",
            "",
            "PnL by strategy:",
        ]
        for sid, pnl in self.pnl_by_strategy.items():
            wr = self.win_rate_by_strategy.get(sid, 0.0)
            lines.append(f"  {sid:<25s}: PnL={pnl:>+10.2f}  WinRate={wr:.1%}")
        return "\n".join(lines)


# ─── CSV 로더 ─────────────────────────────────────────────


def load_csv_candles(
    path: str | Path,
    symbol: Optional[str] = None,
    market_type: MarketType = MarketType.STOCK,
    timestamp_format: str = "%Y-%m-%d %H:%M:%S",
) -> list[OHLCV]:
    """CSV 컬럼: timestamp,open,high,low,close,volume.

    네이버/키움/Investing.com 다운로드 형식 호환.
    """
    full = Path(path)
    if not full.exists():
        raise FileNotFoundError(f"CSV not found: {full}")
    candles: list[OHLCV] = []
    with open(full, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row.get("timestamp") or row.get("time") or row.get("date") or ""
            try:
                ts = datetime.strptime(ts_str, timestamp_format)
            except ValueError:
                # ISO 8601 fallback
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            candles.append(
                OHLCV(
                    symbol=symbol or row.get("symbol") or "UNKNOWN",
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0),
                    market_type=market_type,
                )
            )
    return candles


# ─── Simulator 본체 ───────────────────────────────────────


_DEFAULT_EXIT_PLAN = ExitPlan(
    take_profits=[
        TakeProfitTier(price=Decimal("1.03"), qty_pct=Decimal("0.33")),  # 의미상 — 진입 후 +3%
        TakeProfitTier(price=Decimal("1.05"), qty_pct=Decimal("0.33")),
        TakeProfitTier(price=Decimal("1.07"), qty_pct=Decimal("0.34")),
    ],
    stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
    breakeven_trigger=Decimal("0.01"),
)


def _build_strategies(strategy_ids: Sequence[str]):
    """strategy_id → 인스턴스 lazy import."""
    out = []
    for sid in strategy_ids:
        if sid == "f_zone":
            from backend.core.strategy.f_zone import FZoneStrategy

            out.append(FZoneStrategy())
        elif sid == "sf_zone":
            from backend.core.strategy.sf_zone import SFZoneStrategy

            out.append(SFZoneStrategy())
        elif sid == "gold_zone":
            from backend.core.strategy.gold_zone import GoldZoneStrategy

            out.append(GoldZoneStrategy())
        elif sid == "swing_38":
            from backend.core.strategy.swing_38 import Swing38Strategy

            out.append(Swing38Strategy())
        elif sid == "scalping_consensus":
            from backend.core.strategy.scalping_consensus import (
                ScalpingConsensusStrategy,
            )

            out.append(ScalpingConsensusStrategy())
        else:
            raise ValueError(f"unknown strategy: {sid}")
    return out


class IntradaySimulator:
    """당일 캔들 시뮬레이터. 슬라이딩 윈도우로 5 전략 평가 + ExitEngine 청산."""

    DEFAULT_STRATEGIES = [
        "f_zone",
        "sf_zone",
        "gold_zone",
        "swing_38",
        "scalping_consensus",
    ]

    def __init__(
        self,
        exit_engine: Optional[ExitEngine] = None,
        warmup_candles: int = 30,
        position_qty: Decimal = Decimal("100"),
    ) -> None:
        self._exit = exit_engine or ExitEngine()
        self._warmup = warmup_candles
        self._qty = position_qty

    def run(
        self,
        candles: list[OHLCV],
        symbol: str,
        strategies: Optional[list[str]] = None,
        market_type: MarketType = MarketType.STOCK,
    ) -> SimulationResult:
        """5 전략 (또는 일부) 슬라이딩 윈도우 시뮬레이션."""
        if len(candles) < self._warmup + 1:
            raise ValueError(
                f"need ≥ {self._warmup + 1} candles, got {len(candles)}"
            )
        strategy_ids = strategies or self.DEFAULT_STRATEGIES
        strategies_obj = _build_strategies(strategy_ids)

        trades: list[TradeRecord] = []
        pnl_by_strategy: dict[str, Decimal] = {sid: Decimal(0) for sid in strategy_ids}
        wins_by_strategy: dict[str, int] = {sid: 0 for sid in strategy_ids}
        completed_by_strategy: dict[str, int] = {sid: 0 for sid in strategy_ids}

        for strategy, sid in zip(strategies_obj, strategy_ids):
            position: Optional[PositionState] = None
            for i in range(self._warmup, len(candles)):
                window = candles[: i + 1]
                current = candles[i]

                # 진입 평가 (포지션 없을 때만)
                if position is None:
                    ctx = AnalysisContext(
                        symbol=symbol,
                        candles=window,
                        market_type=market_type,
                    )
                    try:
                        signal = strategy.analyze(ctx)
                    except Exception as exc:
                        logger.debug("%s analyze err: %s", sid, exc)
                        signal = None
                    if signal is not None:
                        entry_price = Decimal(str(current.close))
                        position = PositionState(
                            symbol=symbol,
                            entry_price=entry_price,
                            qty=self._qty,
                            initial_qty=self._qty,
                            entry_time=current.timestamp,
                        )
                        trades.append(
                            TradeRecord(
                                strategy_id=sid, symbol=symbol, side="buy",
                                qty=self._qty, price=entry_price,
                                timestamp=current.timestamp, reason="entry",
                            )
                        )
                else:
                    # 청산 평가 — 동적 ExitPlan (entry 기준 +3/+5/+7%, -1.5%)
                    plan = _scaled_exit_plan(position.entry_price)
                    new_pos, exit_orders = self._exit.evaluate(
                        position, plan,
                        Decimal(str(current.close)),
                        current.timestamp,
                    )
                    for eo in exit_orders:
                        trades.append(
                            TradeRecord(
                                strategy_id=sid, symbol=symbol, side="sell",
                                qty=eo.qty, price=eo.target_price,
                                timestamp=current.timestamp,
                                reason=eo.reason.value,
                            )
                        )
                        pnl = (eo.target_price - position.entry_price) * eo.qty
                        pnl_by_strategy[sid] += pnl
                        completed_by_strategy[sid] += 1
                        if pnl > 0:
                            wins_by_strategy[sid] += 1
                    if new_pos.qty == 0:
                        position = None
                    else:
                        position = new_pos

        win_rate = {
            sid: (
                wins_by_strategy[sid] / completed_by_strategy[sid]
                if completed_by_strategy[sid] > 0 else 0.0
            )
            for sid in strategy_ids
        }

        return SimulationResult(
            symbol=symbol,
            candle_count=len(candles),
            strategies_run=list(strategy_ids),
            trades=trades,
            pnl_by_strategy=pnl_by_strategy,
            win_rate_by_strategy=win_rate,
        )


def _scaled_exit_plan(entry_price: Decimal) -> ExitPlan:
    """entry_price 기준 +3/+5/+7% TP, -1.5% SL."""
    return ExitPlan(
        take_profits=[
            TakeProfitTier(price=entry_price * Decimal("1.03"), qty_pct=Decimal("0.33")),
            TakeProfitTier(price=entry_price * Decimal("1.05"), qty_pct=Decimal("0.33")),
            TakeProfitTier(price=entry_price * Decimal("1.07"), qty_pct=Decimal("0.34")),
        ],
        stop_loss=StopLoss(fixed_pct=Decimal("-0.015")),
        breakeven_trigger=Decimal("0.01"),
    )


__all__ = [
    "IntradaySimulator",
    "SimulationResult",
    "TradeRecord",
    "load_csv_candles",
]
