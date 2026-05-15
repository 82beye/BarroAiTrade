"""P2 (갭4) — PortfolioSimulator.

여러 종목이 단일 자본 풀에서 자금을 두고 경쟁하는 포트폴리오 레벨 백테스터.

IntradaySimulator(단일 종목)와 달리:
- 모든 종목 캔들을 시간축 합집합으로 정렬해 동시 진행
- 단일 cash 풀 — 진입 시 차감, 청산 시 가산
- 진입 신호 발생 종목들에 균등 분배(balance_gate 정책) + 종목당 한도 캡
- 동시 보유 종목 수 한도 (max_concurrent)
- 종목당 1포지션 — 실제 계좌 모사 (IntradaySimulator 는 전략별 독립 포지션)
- equity curve = cash + 보유 포지션 평가액

IntradaySimulator 는 수정하지 않는다 — 헬퍼 인스턴스를 보유해 `_evaluate_intrabar`
(pos/plan/candle 만 받고 stateless ExitEngine 만 읽음)를 외부 호출.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Optional

from backend.core.backtester.intraday_simulator import (
    IntradaySimulator,
    ScalpingProvider,
    TradeRecord,
    _build_strategies,
    _exit_plan_for_strategy,
)
from backend.core.backtester.performance import PerformanceMetrics, compute_metrics
from backend.models.exit_order import PositionState
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import AnalysisContext, ExitPlan


@dataclass(frozen=True)
class _OpenPosition:
    """포트폴리오 보유 포지션 — 종목당 1개."""

    strategy_id: str
    pos: PositionState        # _evaluate_intrabar 입력 (qty/sl_at/tp_filled 갱신)
    plan: ExitPlan            # 진입 시 1회 결정 — 청산까지 유지


@dataclass(frozen=True)
class PortfolioResult:
    """포트폴리오 시뮬 결과."""

    initial_capital: Decimal
    final_capital: Decimal                              # 종료 시 cash + 잔여 보유 평가액
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    cash_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)
    pnl_by_symbol: dict[str, Decimal] = field(default_factory=dict)
    pnl_by_strategy: dict[str, Decimal] = field(default_factory=dict)
    metrics: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    symbols: list[str] = field(default_factory=list)
    timeline_length: int = 0
    open_positions_count: int = 0                       # 타임라인 종료 시 미청산 잔량

    def summary(self) -> str:
        ret_pct = (
            (self.final_capital - self.initial_capital) / self.initial_capital * 100
            if self.initial_capital > 0
            else Decimal("0")
        )
        lines = [
            f"=== PortfolioSimulator: {len(self.symbols)} 종목 / {self.timeline_length} 타임스텝 ===",
            f"초기자본 : {self.initial_capital:>15,.0f}",
            f"최종자본 : {self.final_capital:>15,.0f}  ({ret_pct:+.2f}%)",
            f"총 거래  : {len(self.trades)} (미청산 {self.open_positions_count})",
            f"승률     : {self.metrics.win_rate * 100:.1f}%  PF={self.metrics.profit_factor:.2f}  "
            f"MDD={self.metrics.max_drawdown:,.0f}",
            "종목별 PnL:",
        ]
        for sym, pnl in sorted(
            self.pnl_by_symbol.items(), key=lambda kv: kv[1], reverse=True
        ):
            lines.append(f"  {sym:<10}: {pnl:>+14,.0f}")
        return "\n".join(lines)


def _realize_pnl(
    entry_price: Decimal,
    exit_price: Decimal,
    qty: Decimal,
    commission_rate: Decimal,
    tax_rate: Decimal,
) -> tuple[Decimal, Decimal, Decimal]:
    """청산 1건의 실현손익 — IntradaySimulator.run() 359-364 공식 복제.

    반환: (pnl, commission, tax). pnl = gross - commission - tax.
    """
    gross = (exit_price - entry_price) * qty
    commission = (exit_price + entry_price) * qty * commission_rate
    tax = exit_price * qty * tax_rate
    return gross - commission - tax, commission, tax


class PortfolioSimulator:
    """포트폴리오 레벨 백테스터 — 단일 자본 풀 + 종목 간 자금 경쟁."""

    def __init__(
        self,
        initial_capital: Decimal,
        *,
        max_per_position: Decimal = Decimal("0.10"),
        max_total_position: Decimal = Decimal("0.90"),
        max_concurrent: int = 10,
        warmup_candles: int = 31,
        commission_pct: float = 0.015,
        tax_pct_on_sell: float = 0.18,
        slippage_pct: float = 0.0,
        entry_on_next_open: bool = True,
        exit_on_intrabar: bool = True,
        scalping_provider: Optional[ScalpingProvider] = None,
        strategy_weights: Optional[dict[str, float]] = None,
        f_zone_atr_exit: bool = False,
        strategy_universe: Optional[dict[str, set[str]]] = None,
    ) -> None:
        if initial_capital <= 0:
            raise ValueError(f"initial_capital must be > 0, got {initial_capital}")
        self._initial_capital = initial_capital
        self._max_per = max_per_position
        self._max_total = max_total_position
        self._max_concurrent = max_concurrent
        self._warmup = warmup_candles
        self._commission = Decimal(str(commission_pct)) / Decimal("100")
        self._tax = Decimal(str(tax_pct_on_sell)) / Decimal("100")
        self._slippage = Decimal(str(slippage_pct)) / Decimal("100")
        self._entry_next_open = entry_on_next_open
        self._scalping_provider = scalping_provider
        # 전략별 자금 비중 — slot * weight. weight 0 = 진입 제외. 미지정 = 1.0.
        self._strategy_weights: dict[str, Decimal] = {
            k: Decimal(str(v)) for k, v in (strategy_weights or {}).items()
        }
        # f_zone 백테스트 청산 — True 시 sf_zone 과 동일 ATR plan 사용 (BULL 권장)
        self._f_zone_atr = f_zone_atr_exit
        # 전략별 종목 후보군 — {strategy_id: set(symbol)}. None 이면 모든 종목 모든 전략.
        self._universe: Optional[dict[str, set[str]]] = strategy_universe
        # 청산 평가 헬퍼 — _evaluate_intrabar 호출 전용. 수수료는 0 (PortfolioSimulator
        # 가 cash 연동해 자체 계산). run() 루프는 사용하지 않음.
        self._exec_helper = IntradaySimulator(
            warmup_candles=warmup_candles,
            entry_on_next_open=entry_on_next_open,
            exit_on_intrabar=exit_on_intrabar,
            commission_pct=0.0,
            tax_pct_on_sell=0.0,
        )

    def run(
        self,
        candles_by_symbol: dict[str, list[OHLCV]],
        strategies: list[str],
        market_type: MarketType = MarketType.STOCK,
    ) -> PortfolioResult:
        """여러 종목 캔들 → 단일 자본 풀 포트폴리오 시뮬레이션."""
        if not candles_by_symbol:
            raise ValueError("candles_by_symbol is empty")

        symbols = sorted(candles_by_symbol.keys())
        timeline = self._build_timeline(candles_by_symbol)
        strat_pairs = list(zip(strategies, _build_strategies(strategies, self._scalping_provider)))

        cash = self._initial_capital
        positions: dict[str, _OpenPosition] = {}
        pending: dict[str, tuple[str, object]] = {}     # sym -> (strategy_id, EntrySignal)
        pending_slot: dict[str, Decimal] = {}

        trades: list[TradeRecord] = []
        equity_curve: list[tuple[datetime, Decimal]] = []
        cash_curve: list[tuple[datetime, Decimal]] = []
        pnl_by_symbol: dict[str, Decimal] = {s: Decimal("0") for s in symbols}
        pnl_by_strategy: dict[str, Decimal] = {sid: Decimal("0") for sid in strategies}

        ptr: dict[str, int] = {s: -1 for s in symbols}

        def has_candle(sym: str) -> bool:
            idx = ptr[sym]
            return idx >= 0 and candles_by_symbol[sym][idx].timestamp == t

        for t in timeline:
            # 종목별 인덱스 포인터 advance — clist[ptr+1].timestamp <= t 인 동안
            for sym in symbols:
                clist = candles_by_symbol[sym]
                while ptr[sym] + 1 < len(clist) and clist[ptr[sym] + 1].timestamp <= t:
                    ptr[sym] += 1

            # ── a. 청산 평가 — 그 t 캔들이 있는 보유 종목만 ──────────────────
            for sym in list(positions.keys()):
                if not has_candle(sym):
                    continue
                candle = candles_by_symbol[sym][ptr[sym]]
                op = positions[sym]
                new_pos, exit_orders = self._exec_helper._evaluate_intrabar(
                    op.pos, op.plan, candle
                )
                for eo in exit_orders:
                    # 청산 슬리피지 — 매도는 불리하게 낮은 가격 (양방향, P3 갭8)
                    exit_price = eo.target_price * (Decimal("1") - self._slippage)
                    pnl, _comm, tax = _realize_pnl(
                        op.pos.entry_price, exit_price, eo.qty,
                        self._commission, self._tax,
                    )
                    # cash 가산: 매도 대금 - 매도수수료 - 세금.
                    # _realize_pnl 의 _comm 은 매수+매도 양쪽 합산(pnl 계산용) — 매수
                    # 수수료는 진입 시 이미 cash 에서 차감했으므로 매도분만 뺀다.
                    sell_comm = exit_price * eo.qty * self._commission
                    cash += eo.qty * exit_price - sell_comm - tax
                    trades.append(TradeRecord(
                        strategy_id=op.strategy_id, symbol=sym, side="sell",
                        qty=eo.qty, price=exit_price, timestamp=t,
                        reason=eo.reason.value, pnl=pnl,
                    ))
                    pnl_by_symbol[sym] += pnl
                    pnl_by_strategy[op.strategy_id] += pnl
                if new_pos.qty == 0:
                    del positions[sym]
                else:
                    positions[sym] = _OpenPosition(op.strategy_id, new_pos, op.plan)

            # ── b. pending 진입 — 그 t 캔들 open 으로 체결 (next-open) ────────
            for sym in list(pending.keys()):
                if not has_candle(sym):
                    continue  # 거래정지 등 — 다음 캔들 등장 t 까지 대기
                candle = candles_by_symbol[sym][ptr[sym]]
                sid, signal = pending.pop(sym)
                slot = pending_slot.pop(sym)
                entry_price = Decimal(str(candle.open)) * (Decimal("1") + self._slippage)
                if entry_price <= 0:
                    continue
                budget = min(slot, cash * Decimal("0.999"))
                qty = (budget / entry_price).quantize(Decimal("1"), rounding=ROUND_DOWN)
                if qty <= 0:
                    continue
                buy_comm = entry_price * qty * self._commission
                cost = entry_price * qty + buy_comm
                if cost > cash:
                    continue  # cash 가드 — 갭상승 등으로 추정 초과 시 거부
                cash -= cost
                window = candles_by_symbol[sym][: ptr[sym] + 1]
                plan = _exit_plan_for_strategy(
                    sid, entry_price, window, f_zone_atr=self._f_zone_atr,
                )
                positions[sym] = _OpenPosition(
                    strategy_id=sid,
                    pos=PositionState(
                        symbol=sym, entry_price=entry_price,
                        qty=qty, initial_qty=qty, entry_time=t,
                    ),
                    plan=plan,
                )
                trades.append(TradeRecord(
                    strategy_id=sid, symbol=sym, side="buy",
                    qty=qty, price=entry_price, timestamp=t,
                    reason="entry", pnl=Decimal("0"),
                ))

            # ── c. 진입 신호 평가 — 미보유·미pending 종목, warmup 충족 ────────
            signals: dict[str, tuple[str, object]] = {}
            for sym in symbols:
                if sym in positions or sym in pending:
                    continue
                if not has_candle(sym):
                    continue
                idx = ptr[sym]
                if idx < self._warmup:
                    continue
                window = candles_by_symbol[sym][: idx + 1]
                ctx = AnalysisContext(
                    symbol=sym, candles=window, market_type=market_type,
                )
                best_sid: Optional[str] = None
                best_sig = None
                for sid, strat in strat_pairs:
                    # universe 필터 — 전략별 후보군에 없으면 analyze() 호출 안 함
                    if (
                        self._universe is not None
                        and sym not in self._universe.get(sid, set())
                    ):
                        continue
                    try:
                        sig = strat.analyze(ctx)
                    except Exception:  # noqa: BLE001 — 전략 분석 실패는 신호 없음으로
                        sig = None
                    if sig is None:
                        continue
                    if best_sig is None or sig.score > best_sig.score:
                        best_sig, best_sid = sig, sid
                if best_sig is not None:
                    signals[sym] = (best_sid, best_sig)

            # ── d. 자금 배분 — score 우선순위 + 균등 분배 + 한도 ───────────────
            if signals:
                ranked = sorted(
                    signals.items(), key=lambda kv: (-kv[1][1].score, kv[0])
                )
                pos_value = self._current_position_value(positions, ptr, candles_by_symbol)
                alloc = self._allocate(
                    ranked, cash, pos_value, len(positions) + len(pending)
                )
                for sym, slot in alloc.items():
                    pending[sym] = signals[sym]
                    pending_slot[sym] = slot

            # ── e. equity 기록 ───────────────────────────────────────────────
            pos_value = self._current_position_value(positions, ptr, candles_by_symbol)
            equity_curve.append((t, cash + pos_value))
            cash_curve.append((t, cash))

        # ── 종료 — 미청산 포지션은 마지막 close 로 평가 (실현 X) ──────────────
        final_pos_value = self._current_position_value(positions, ptr, candles_by_symbol)
        return PortfolioResult(
            initial_capital=self._initial_capital,
            final_capital=cash + final_pos_value,
            trades=trades,
            equity_curve=equity_curve,
            cash_curve=cash_curve,
            pnl_by_symbol=pnl_by_symbol,
            pnl_by_strategy=pnl_by_strategy,
            metrics=compute_metrics(trades),
            symbols=symbols,
            timeline_length=len(timeline),
            open_positions_count=len(positions),
        )

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_timeline(candles_by_symbol: dict[str, list[OHLCV]]) -> list[datetime]:
        """모든 종목 timestamp 의 합집합 정렬 — 공통 타임라인."""
        ts: set[datetime] = set()
        for clist in candles_by_symbol.values():
            for c in clist:
                ts.add(c.timestamp)
        return sorted(ts)

    @staticmethod
    def _current_position_value(
        positions: dict[str, _OpenPosition],
        ptr: dict[str, int],
        candles_by_symbol: dict[str, list[OHLCV]],
    ) -> Decimal:
        """보유 포지션 평가액 — 각 종목 마지막 알려진 close 기준."""
        total = Decimal("0")
        for sym, op in positions.items():
            idx = ptr[sym]
            if idx >= 0:
                close = Decimal(str(candles_by_symbol[sym][idx].close))
            else:
                close = op.pos.entry_price
            total += op.pos.qty * close
        return total

    def _allocate(
        self,
        ranked: list[tuple[str, tuple[str, object]]],
        cash: Decimal,
        current_position_value: Decimal,
        occupied_count: int,
    ) -> dict[str, Decimal]:
        """진입 후보에 자금 배분 — balance_gate 균등 분배 로직 복제 + cash 가드.

        - free_slots = max_concurrent - 현재 보유/pending 수
        - available = max(0, initial*max_total - 현재 보유 평가액)
        - per_slot = available / 후보 수, slot = min(max_per, per_slot, 잔여, cash 잔여)
        """
        free_slots = self._max_concurrent - occupied_count
        if free_slots <= 0:
            return {}
        eligible = ranked[:free_slots]
        if not eligible:
            return {}

        max_total = self._initial_capital * self._max_total
        max_per = self._initial_capital * self._max_per
        available = max(Decimal("0"), max_total - current_position_value)
        if available <= 0:
            return {}
        per_slot = available / Decimal(len(eligible))

        consumed = Decimal("0")
        out: dict[str, Decimal] = {}
        for sym, (sid, sig) in eligible:
            # 전략별 가중치 — weight 0 이면 이 전략 신호 진입 제외.
            weight = self._strategy_weights.get(sid, Decimal("1"))
            if weight <= 0:
                continue
            est_price = Decimal(str(sig.price)) * (Decimal("1") + self._slippage)
            if est_price <= 0:
                continue
            # cash - consumed 가드 — balance_gate 대비 추가: 손실로 cash 가 줄면
            # available(initial 기준)만으로는 실제 현금 초과 매수가 가능해짐.
            # base_slot 에 max_per 포함 cap → weight<1.0 도 정상 축소. weight 적용
            # 후 max_per 로 다시 cap → weight>1.0 이 종목당 한도를 깨지 못하게.
            base_slot = min(max_per, per_slot, available - consumed, cash - consumed)
            slot = min(base_slot * weight, max_per)
            if slot <= 0:
                continue
            qty = (slot / est_price).quantize(Decimal("1"), rounding=ROUND_DOWN)
            if qty <= 0:
                continue
            consumed += qty * est_price
            out[sym] = slot
        return out


__all__ = ["PortfolioSimulator", "PortfolioResult"]
