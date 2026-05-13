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
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

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


# ScalpingConsensusStrategy 의 AnalysisProvider 시그니처 재선언 (순환 import 회피).
# `Callable[[AnalysisContext], Optional[ScalpingAnalysis|dict]]` — _adapter.to_entry_signal
# 가 받을 수 있는 형식이면 됨.
ScalpingProvider = Callable[[AnalysisContext], Optional[Any]]


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


def _build_strategies(
    strategy_ids: Sequence[str],
    scalping_provider: Optional[ScalpingProvider] = None,
):
    """strategy_id → 인스턴스 lazy import.

    scalping_provider 가 주어지면 ScalpingConsensusStrategy 인스턴스에 주입.
    미주입 상태에서 scalping_consensus 가 포함되면 warning 로그를 남김 — analyze()
    가 항상 None 을 반환해 trades=0 이 보장되므로 호출측이 조용히 0건을 받는 것을
    방지하기 위함.
    """
    out = []
    for sid in strategy_ids:
        if sid == "f_zone":
            from backend.core.strategy.f_zone import FZoneParams, FZoneStrategy

            # F1 변동성 필터를 운영·시뮬 진입점에서 명시 적용 (default 는 0.0 으로
            # BAR-44 baseline 회귀 보존, 여기서만 0.035 활성화).
            # 2026-05-14 백테스트 +186k 효과 검증 — LESSON_S1_NORMALIZATION 참조.
            out.append(FZoneStrategy(FZoneParams(min_atr_pct=0.035)))
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

            sc = ScalpingConsensusStrategy()
            provider = scalping_provider
            if provider is None:
                # auto-load: 운영 환경에서는 실제 ScalpingCoordinator wrapper 자동 연결
                # (legacy_scalping 의존성·sys.path 부트스트랩 비용 포함).
                try:
                    from backend.legacy_scalping._provider import (
                        build_scalping_provider,
                    )

                    provider = build_scalping_provider()
                except Exception as exc:
                    logger.warning(
                        "scalping_consensus auto-provider 로드 실패 (%s) — "
                        "analyze() returns None. IntradaySimulator(scalping_provider=...) "
                        "로 명시 주입 가능.",
                        exc,
                    )
            if provider is not None:
                sc.set_analysis_provider(provider)
            out.append(sc)
        else:
            raise ValueError(f"unknown strategy: {sid}")
    return out


class IntradaySimulator:
    """당일 캔들 시뮬레이터. 슬라이딩 윈도우로 5 전략 평가 + ExitEngine 청산.

    BAR-OPS-35 — 트레이딩뷰 등급 정확도 옵션:
    - entry_on_next_open: 시그널 발생 캔들의 close 가 아닌 **다음 캔들 open** 으로 진입
                         (lookahead bias 제거). default True.
    - exit_on_intrabar: 청산 평가 시 close 가 아닌 **bar high/low 터치** 로 체결.
                       TP 터치: high ≥ tp_price → tp_price 체결.
                       SL 터치: low ≤ sl_price → sl_price 체결. default True.
    - commission_pct: 매수·매도 각각 차감 (예: 0.015 = 0.015%). 키움 위탁 표준.
    - tax_pct_on_sell: 매도 시만 차감 (예: 0.18 = 0.18%). 증권거래세+농특세.
    - slippage_pct: 시장가 진입 시 진입가 * (1+slippage_pct/100) 적용. default 0.
    """

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
        *,
        entry_on_next_open: bool = True,
        exit_on_intrabar: bool = True,
        commission_pct: float = 0.0,
        tax_pct_on_sell: float = 0.0,
        slippage_pct: float = 0.0,
        scalping_provider: Optional[ScalpingProvider] = None,
        position_value: Optional[Decimal] = None,
        high_price_threshold: Decimal = Decimal("1000000"),
        high_price_budget: Decimal = Decimal("2000000"),
    ) -> None:
        self._exit = exit_engine or ExitEngine()
        self._warmup = warmup_candles
        self._qty = position_qty
        self._entry_next_open = entry_on_next_open
        self._exit_intrabar = exit_on_intrabar
        self._commission = Decimal(str(commission_pct)) / Decimal("100")
        self._tax = Decimal(str(tax_pct_on_sell)) / Decimal("100")
        self._slippage = Decimal(str(slippage_pct)) / Decimal("100")
        self._scalping_provider = scalping_provider
        # S1: 종목당 명목 가치 기준 진입 — 지정 시 qty = floor(value / entry_price).
        # None 시 position_qty 고정 (기존 동작 보존).
        # 고가주 (price > high_price_threshold) 는 high_price_budget 한도 사용 —
        # 예: 1주당 100만원 초과 시 최대 200만원 한도 (즉 qty 1~2).
        self._position_value = position_value
        self._high_price_threshold = high_price_threshold
        self._high_price_budget = high_price_budget

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
        strategies_obj = _build_strategies(strategy_ids, self._scalping_provider)

        trades: list[TradeRecord] = []
        pnl_by_strategy: dict[str, Decimal] = {sid: Decimal(0) for sid in strategy_ids}
        wins_by_strategy: dict[str, int] = {sid: 0 for sid in strategy_ids}
        completed_by_strategy: dict[str, int] = {sid: 0 for sid in strategy_ids}

        for strategy, sid in zip(strategies_obj, strategy_ids):
            position: Optional[PositionState] = None
            pending_entry: bool = False     # OPS-35: 시그널 캔들 → 다음 open 진입
            # 진입 시점에 1회 결정 → 청산까지 유지. 전략별 분기 (sf_zone=ATR, 그 외=고정).
            current_plan: Optional[ExitPlan] = None
            for i in range(self._warmup, len(candles)):
                window = candles[: i + 1]
                current = candles[i]

                # 1. 지연 진입 처리 (OPS-35 entry_on_next_open) — 이전 캔들 시그널을
                #    현 캔들 open 으로 진입
                if pending_entry and position is None:
                    raw_open = Decimal(str(current.open))
                    entry_price = raw_open * (Decimal("1") + self._slippage)
                    qty = self._compute_entry_qty(entry_price)
                    if qty <= 0:
                        # 명목 가치 < 1주 가격 → 진입 거부 (S1 가드)
                        pending_entry = False
                        continue
                    position = PositionState(
                        symbol=symbol, entry_price=entry_price,
                        qty=qty, initial_qty=qty,
                        entry_time=current.timestamp,
                    )
                    current_plan = _exit_plan_for_strategy(sid, entry_price, window)
                    trades.append(TradeRecord(
                        strategy_id=sid, symbol=symbol, side="buy",
                        qty=qty, price=entry_price,
                        timestamp=current.timestamp, reason="entry",
                    ))
                    pending_entry = False
                    # 진입 직후 캔들에서 즉시 청산 안 함 (다음 캔들부터 평가)
                    continue

                # 2. 진입 평가 (포지션 없을 때만)
                if position is None:
                    ctx = AnalysisContext(
                        symbol=symbol, candles=window, market_type=market_type,
                    )
                    try:
                        signal = strategy.analyze(ctx)
                    except Exception as exc:
                        logger.debug("%s analyze err: %s", sid, exc)
                        signal = None
                    if signal is not None:
                        if self._entry_next_open and i + 1 < len(candles):
                            # 다음 캔들 open 진입 예약 (lookahead 제거)
                            pending_entry = True
                        else:
                            # 즉시 진입 (next_open 비활성 또는 마지막 캔들)
                            raw_close = Decimal(str(current.close))
                            entry_price = raw_close * (Decimal("1") + self._slippage)
                            qty = self._compute_entry_qty(entry_price)
                            if qty <= 0:
                                continue
                            position = PositionState(
                                symbol=symbol, entry_price=entry_price,
                                qty=qty, initial_qty=qty,
                                entry_time=current.timestamp,
                            )
                            current_plan = _exit_plan_for_strategy(sid, entry_price, window)
                            trades.append(TradeRecord(
                                strategy_id=sid, symbol=symbol, side="buy",
                                qty=qty, price=entry_price,
                                timestamp=current.timestamp, reason="entry",
                            ))
                else:
                    # 3. 청산 평가 — bar high/low 터치 (OPS-35 exit_on_intrabar)
                    plan = current_plan or _scaled_exit_plan(position.entry_price)
                    if self._exit_intrabar:
                        # high → TP 평가 / low → SL 평가
                        # 두 평가를 동일 캔들에서 — TP 우선 (보수적 가정: 위로 먼저 갔다고 봄)
                        new_pos, exit_orders = self._evaluate_intrabar(
                            position, plan, current,
                        )
                    else:
                        new_pos, exit_orders = self._exit.evaluate(
                            position, plan,
                            Decimal(str(current.close)),
                            current.timestamp,
                        )
                    for eo in exit_orders:
                        # 매도 시 commission + tax 차감
                        gross = (eo.target_price - position.entry_price) * eo.qty
                        commission = (eo.target_price + position.entry_price) * eo.qty * self._commission
                        tax = eo.target_price * eo.qty * self._tax
                        pnl = gross - commission - tax
                        trades.append(TradeRecord(
                            strategy_id=sid, symbol=symbol, side="sell",
                            qty=eo.qty, price=eo.target_price,
                            timestamp=current.timestamp,
                            reason=eo.reason.value,
                        ))
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


    def _compute_entry_qty(self, entry_price: Decimal) -> Decimal:
        """진입 qty 결정.

        - position_value None: position_qty 그대로 (기존 100주 고정 동작).
        - 일반 종목 (entry_price ≤ high_price_threshold): floor(position_value / entry_price)
        - 고가주 (entry_price > high_price_threshold): floor(high_price_budget / entry_price)
          → 예: 1주당 100만원 초과 종목은 200만원 한도 (qty 1~2주).
            price>budget 이면 qty=0 (진입 거부).
        """
        if self._position_value is None:
            return self._qty
        if entry_price <= 0:
            return Decimal(0)
        budget = (
            self._high_price_budget
            if entry_price > self._high_price_threshold
            else self._position_value
        )
        return (budget / entry_price).quantize(Decimal("1"), rounding=ROUND_DOWN)

    def _evaluate_intrabar(
        self,
        position: PositionState,
        plan: ExitPlan,
        candle: OHLCV,
    ) -> tuple[PositionState, list[ExitOrder]]:
        """OPS-35 — bar high/low 터치 시 체결.

        TP 우선 (위로 먼저 갔다고 가정 — 보수 X / 낙관). 동일 bar 에서
        TP+SL 동시 터치 가능 — 둘 다 발생.
        """
        high = Decimal(str(candle.high))
        low = Decimal(str(candle.low))
        ts = candle.timestamp
        pos = position
        orders: list[ExitOrder] = []

        # TP 평가 — 각 tier 의 절대가격이 high 이하면 체결
        for tier in plan.take_profits:
            tp_price = tier.price                       # _scaled_exit_plan 에서 절대가
            if pos.qty <= 0 or high < tp_price:
                continue
            # ExitEngine.evaluate 호출 — current_price=tp_price 강제 사용
            new_pos, exit_orders = self._exit.evaluate(pos, plan, tp_price, ts)
            for eo in exit_orders:
                if eo.reason in (ExitReason.TP1, ExitReason.TP2, ExitReason.TP3):
                    orders.append(eo)
            pos = new_pos
            if pos.qty <= 0:
                return pos, orders

        # SL 평가 — low 가 SL 이하면 체결
        if pos.qty > 0 and plan.stop_loss is not None:
            sl_price = pos.entry_price * (Decimal("1") + plan.stop_loss.fixed_pct)
            if low <= sl_price:
                new_pos, exit_orders = self._exit.evaluate(pos, plan, sl_price, ts)
                for eo in exit_orders:
                    if eo.reason == ExitReason.STOP_LOSS:
                        orders.append(eo)
                pos = new_pos
                return pos, orders

        # 마지막 — close 기반 breakeven 트리거 등 잔여 평가
        if pos.qty > 0:
            close_p = Decimal(str(candle.close))
            new_pos, exit_orders = self._exit.evaluate(pos, plan, close_p, ts)
            # close 기반 추가 청산 X (위에서 이미 TP/SL 처리). breakeven 만 trigger.
            pos = new_pos

        return pos, orders


def _scaled_exit_plan(
    entry_price: Decimal,
    sl_pct: Decimal = Decimal("-0.015"),
) -> ExitPlan:
    """entry_price 기준 +3/+5/+7% TP, SL(default -1.5%). 고정 정책 — 대부분 전략 default."""
    return ExitPlan(
        take_profits=[
            TakeProfitTier(price=entry_price * Decimal("1.03"), qty_pct=Decimal("0.33")),
            TakeProfitTier(price=entry_price * Decimal("1.05"), qty_pct=Decimal("0.33")),
            TakeProfitTier(price=entry_price * Decimal("1.07"), qty_pct=Decimal("0.34")),
        ],
        stop_loss=StopLoss(fixed_pct=sl_pct),
        breakeven_trigger=Decimal("0.01"),
    )


def _sfzone_atr_exit_plan(
    entry_price: Decimal,
    candles_window: list[OHLCV],
    n: int = 14,
    sl_multiplier: Decimal = Decimal("2.0"),
    tp_multipliers: tuple[Decimal, Decimal, Decimal] = (
        Decimal("1.5"), Decimal("2.5"), Decimal("3.5"),
    ),
    sl_floor_pct: Decimal = Decimal("0.015"),
    sl_cap_pct: Decimal = Decimal("0.08"),
) -> ExitPlan:
    """SF존 전용 ExitPlan — TP·SL 모두 ATR 기반 (R:R 균형).

    종목 변동성에 비례한 동적 TP/SL — fixed +3/+5/+7%와 −1.5%가 변동성 큰 종목에서
    부적합한 문제를 종목별로 해소. sl_floor·sl_cap 으로 극단값 클램프.

    SFZoneStrategy 전용 분기 — 다른 전략은 _scaled_exit_plan 그대로 사용.
    """
    atr_pct = _atr_pct(candles_window, n=n)
    atr_clamped = max(sl_floor_pct, min(atr_pct, sl_cap_pct))
    sl_pct = -atr_clamped * sl_multiplier
    # 클램프: SL 도 [-floor×mult, -cap×mult] 안에. sf-zone 정상 SL 범위 −3~−16%.
    if sl_pct < -sl_cap_pct * Decimal("2"):
        sl_pct = -sl_cap_pct * Decimal("2")

    tp1_pct, tp2_pct, tp3_pct = (atr_clamped * m for m in tp_multipliers)
    return ExitPlan(
        take_profits=[
            TakeProfitTier(price=entry_price * (Decimal("1") + tp1_pct),
                           qty_pct=Decimal("0.33"), condition="SF ATR TP1"),
            TakeProfitTier(price=entry_price * (Decimal("1") + tp2_pct),
                           qty_pct=Decimal("0.33"), condition="SF ATR TP2"),
            TakeProfitTier(price=entry_price * (Decimal("1") + tp3_pct),
                           qty_pct=Decimal("0.34"), condition="SF ATR TP3"),
        ],
        stop_loss=StopLoss(fixed_pct=sl_pct),
        breakeven_trigger=Decimal("0.01"),
    )


def _exit_plan_for_strategy(
    strategy_id: str,
    entry_price: Decimal,
    candles_window: list[OHLCV],
) -> ExitPlan:
    """전략별 ExitPlan 분기 — '100% 중복은 공유, 나머지는 별도' 원칙.

    - sf_zone: ATR 기반 동적 TP·SL (변동성 적응)
    - 그 외 (f_zone, gold_zone, swing_38, scalping_consensus): 고정 +3/+5/+7%, −1.5%
    """
    if strategy_id == "sf_zone":
        return _sfzone_atr_exit_plan(entry_price, candles_window)
    return _scaled_exit_plan(entry_price)


def _atr_pct(candles_window: list[OHLCV], n: int = 14) -> Decimal:
    """최근 n봉의 True Range 평균을 마지막 close 로 나눈 비율 (예: 0.025 = 2.5%).

    분봉/일봉 무관 동일 공식. 종목별 변동성 적응 SL 계산에 사용.
    """
    if len(candles_window) < 2:
        return Decimal("0")
    n = min(n, len(candles_window) - 1)
    trs = []
    for i in range(1, n + 1):
        c = candles_window[-i]
        prev = candles_window[-i - 1]
        tr = max(
            c.high - c.low,
            abs(c.high - prev.close),
            abs(c.low - prev.close),
        )
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else 0.0
    last_close = candles_window[-1].close
    if last_close <= 0:
        return Decimal("0")
    return Decimal(str(atr / last_close))


__all__ = [
    "IntradaySimulator",
    "ScalpingProvider",
    "SimulationResult",
    "TradeRecord",
    "load_csv_candles",
]
