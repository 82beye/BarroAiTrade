"""BAR-OPS-20 — 보유 종목 매도 시그널 평가 (적응형).

OPS-15 잔고 조회로 가져온 holdings 의 손익률을 검사:
- 트레일링 스톱: 고점 대비 offset% 하락 시 매도
- 브레이크이븐: 수익률이 trigger 도달 후 0% 이하 복귀 시 매도
- 분할 익절: partial_tp_pct 도달 시 일부 매도
- 시간 기반: N일 이상 보유 시 SL 강화
- 기본 TP/SL: 고정 임계값

OPS-17 LiveOrderGate 와 결합 시 자동 매도 가능.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from backend.core.gateway.kiwoom_native_account import HoldingPosition


class SellSignal(str, Enum):
    HOLD = "hold"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    BREAKEVEN_STOP = "breakeven_stop"
    PARTIAL_TP = "partial_tp"
    TIME_TIGHTENED_SL = "time_tightened_sl"


@dataclass(frozen=True)
class HoldingDecision:
    symbol: str
    name: str
    qty: int
    sell_qty: int                         # 실제 매도 수량 (분할 익절 시 qty의 일부)
    avg_buy_price: Decimal
    cur_price: Decimal
    pnl: Decimal
    pnl_rate: Decimal                     # 수익률 % (signed)
    signal: SellSignal
    reason: str


@dataclass(frozen=True)
class ExitPolicy:
    take_profit_pct: Decimal = Decimal("5.0")
    stop_loss_pct: Decimal = Decimal("-4.0")
    # 적응형 매도
    trailing_start_pct: Decimal = Decimal("3.0")
    trailing_offset_pct: Decimal = Decimal("1.5")
    breakeven_trigger_pct: Decimal = Decimal("2.5")
    partial_tp_pct: Decimal = Decimal("3.5")
    partial_tp_ratio: Decimal = Decimal("0.5")
    hold_days_tighten: int = 5
    tightened_sl_pct: Decimal = Decimal("-2.0")


# ── 전략별 매도 프로파일 ─────────────────────────────────────────
# 각 전략의 exit_plan() 과 동일한 기준을 적응형 매도에 매핑
STRATEGY_EXIT_PROFILES: dict[str, dict] = {
    "f_zone": {
        "stop_loss_pct": Decimal("-4.0"),
        "take_profit_pct": Decimal("5.0"),
        "partial_tp_pct": Decimal("3.0"),
        "partial_tp_ratio": Decimal("0.5"),
        "trailing_start_pct": Decimal("3.5"),
        "trailing_offset_pct": Decimal("1.0"),
        "breakeven_trigger_pct": Decimal("2.5"),
        "tightened_sl_pct": Decimal("-2.5"),
    },
    "sf_zone": {
        "stop_loss_pct": Decimal("-4.0"),
        "take_profit_pct": Decimal("7.0"),
        "partial_tp_pct": Decimal("3.0"),
        "partial_tp_ratio": Decimal("0.33"),
        "trailing_start_pct": Decimal("3.0"),
        "trailing_offset_pct": Decimal("1.5"),
        "breakeven_trigger_pct": Decimal("2.0"),
        "tightened_sl_pct": Decimal("-2.5"),
    },
    "gold_zone": {
        "stop_loss_pct": Decimal("-4.0"),
        "take_profit_pct": Decimal("4.0"),
        "partial_tp_pct": Decimal("2.0"),
        "partial_tp_ratio": Decimal("0.5"),
        "trailing_start_pct": Decimal("3.0"),
        "trailing_offset_pct": Decimal("1.0"),
        "breakeven_trigger_pct": Decimal("2.5"),
        "tightened_sl_pct": Decimal("-3.0"),
    },
    "swing_38": {
        "stop_loss_pct": Decimal("-5.0"),
        "take_profit_pct": Decimal("5.0"),
        "partial_tp_pct": Decimal("3.0"),
        "partial_tp_ratio": Decimal("0.5"),
        "trailing_start_pct": Decimal("4.0"),
        "trailing_offset_pct": Decimal("1.5"),
        "breakeven_trigger_pct": Decimal("3.0"),
        "tightened_sl_pct": Decimal("-3.0"),
    },
}


def resolve_policy(base: ExitPolicy, strategy: str) -> ExitPolicy:
    """전략명으로 ExitPolicy override. 매칭 안 되면 base 그대로 반환."""
    # strategy_id 에서 버전 제거 (gold_zone_v1 → gold_zone)
    key = strategy.replace("_v1", "").replace("_v2", "")
    profile = STRATEGY_EXIT_PROFILES.get(key)
    if not profile:
        return base
    overrides = {k: v for k, v in profile.items()}
    # hold_days_tighten 은 profile 에 없으면 base 유지
    return ExitPolicy(
        take_profit_pct=overrides.get("take_profit_pct", base.take_profit_pct),
        stop_loss_pct=overrides.get("stop_loss_pct", base.stop_loss_pct),
        trailing_start_pct=overrides.get("trailing_start_pct", base.trailing_start_pct),
        trailing_offset_pct=overrides.get("trailing_offset_pct", base.trailing_offset_pct),
        breakeven_trigger_pct=overrides.get("breakeven_trigger_pct", base.breakeven_trigger_pct),
        partial_tp_pct=overrides.get("partial_tp_pct", base.partial_tp_pct),
        partial_tp_ratio=overrides.get("partial_tp_ratio", base.partial_tp_ratio),
        hold_days_tighten=base.hold_days_tighten,
        tightened_sl_pct=overrides.get("tightened_sl_pct", base.tightened_sl_pct),
    )


@dataclass
class PositionContext:
    """ActivePosition 에서 추출한 평가용 컨텍스트."""
    peak_pnl_rate: float = 0.0
    partial_tp_done: bool = False
    entry_time: Optional[str] = None
    strategy: str = ""


def _hold_days(entry_time: Optional[str]) -> int:
    """진입 시점으로부터 경과 일수."""
    if not entry_time:
        return 0
    try:
        et = datetime.fromisoformat(entry_time)
        if et.tzinfo is None:
            et = et.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - et).days
    except (ValueError, TypeError):
        return 0


def evaluate_holding(
    h: HoldingPosition,
    policy: ExitPolicy = ExitPolicy(),
    ctx: Optional[PositionContext] = None,
) -> HoldingDecision:
    rate = h.pnl_rate
    qty = h.qty

    # 컨텍스트가 없으면 기본(레거시) 평가
    if ctx is None:
        return _evaluate_basic(h, policy)

    # 전략별 정책 override
    if ctx.strategy:
        policy = resolve_policy(policy, ctx.strategy)

    peak = Decimal(str(ctx.peak_pnl_rate))
    days = _hold_days(ctx.entry_time)

    # ── 1. 트레일링 스톱 (고점 추적 매도) ──────────────────────────
    # peak이 trailing_start 이상 도달했었고,
    # 현재 수익률이 peak에서 offset만큼 하락했으면 매도
    if peak >= policy.trailing_start_pct and rate < peak - policy.trailing_offset_pct:
        return HoldingDecision(
            symbol=h.symbol, name=h.name, qty=qty, sell_qty=qty,
            avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
            pnl=h.pnl, pnl_rate=rate,
            signal=SellSignal.TRAILING_STOP,
            reason=(
                f"트레일링 스톱: 고점 {float(peak):.1f}% → 현재 {float(rate):.1f}% "
                f"(하락폭 {float(peak - rate):.1f}% > 허용 {float(policy.trailing_offset_pct):.1f}%)"
            ),
        )

    # ── 2. 브레이크이븐 보호 ──────────────────────────────────────
    # peak이 breakeven_trigger 도달 경험 → 수익률 0% 이하 복귀 시 매도
    if peak >= policy.breakeven_trigger_pct and rate <= Decimal("0"):
        return HoldingDecision(
            symbol=h.symbol, name=h.name, qty=qty, sell_qty=qty,
            avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
            pnl=h.pnl, pnl_rate=rate,
            signal=SellSignal.BREAKEVEN_STOP,
            reason=(
                f"브레이크이븐: 고점 {float(peak):.1f}% 도달 후 "
                f"수익률 {float(rate):.1f}% → 본전 이하 복귀 방어"
            ),
        )

    # ── 3. 분할 익절 (1차) ────────────────────────────────────────
    # partial_tp_pct 도달 + 아직 1차 분할 미실행 → 비율만큼 매도
    if (
        not ctx.partial_tp_done
        and rate >= policy.partial_tp_pct
        and rate < policy.take_profit_pct
    ):
        sell_qty = max(1, int(qty * float(policy.partial_tp_ratio)))
        return HoldingDecision(
            symbol=h.symbol, name=h.name, qty=qty, sell_qty=sell_qty,
            avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
            pnl=h.pnl, pnl_rate=rate,
            signal=SellSignal.PARTIAL_TP,
            reason=(
                f"분할 익절: 수익률 {float(rate):.1f}% >= {float(policy.partial_tp_pct):.1f}% "
                f"→ {int(float(policy.partial_tp_ratio) * 100)}% 매도 ({sell_qty}주)"
            ),
        )

    # ── 4. 전량 익절 (TP) ─────────────────────────────────────────
    if rate >= policy.take_profit_pct:
        return HoldingDecision(
            symbol=h.symbol, name=h.name, qty=qty, sell_qty=qty,
            avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
            pnl=h.pnl, pnl_rate=rate,
            signal=SellSignal.TAKE_PROFIT,
            reason=f"익절: 수익률 {float(rate):.1f}% >= TP {float(policy.take_profit_pct):.1f}%",
        )

    # ── 5. 시간 기반 SL 강화 ──────────────────────────────────────
    effective_sl = policy.stop_loss_pct
    time_note = ""
    if days >= policy.hold_days_tighten:
        effective_sl = policy.tightened_sl_pct
        time_note = f" (보유 {days}일 → SL 강화 {float(effective_sl):.1f}%)"

    # ── 6. 손절 (SL) ─────────────────────────────────────────────
    if rate <= effective_sl:
        signal = SellSignal.TIME_TIGHTENED_SL if days >= policy.hold_days_tighten else SellSignal.STOP_LOSS
        return HoldingDecision(
            symbol=h.symbol, name=h.name, qty=qty, sell_qty=qty,
            avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
            pnl=h.pnl, pnl_rate=rate,
            signal=signal,
            reason=(
                f"손절: 수익률 {float(rate):.1f}% <= SL {float(effective_sl):.1f}%"
                f"{time_note}"
            ),
        )

    # ── 7. HOLD ───────────────────────────────────────────────────
    sl_label = f"{float(effective_sl):.1f}%"
    tp_label = f"{float(policy.take_profit_pct):.1f}%"
    extras = []
    if peak > Decimal("0"):
        extras.append(f"고점 {float(peak):.1f}%")
    if days > 0:
        extras.append(f"보유 {days}일")
    extra_str = f" [{', '.join(extras)}]" if extras else ""
    return HoldingDecision(
        symbol=h.symbol, name=h.name, qty=qty, sell_qty=0,
        avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
        pnl=h.pnl, pnl_rate=rate,
        signal=SellSignal.HOLD,
        reason=f"보유 유지 ({sl_label} < {float(rate):.1f}% < {tp_label}){extra_str}",
    )


def _evaluate_basic(
    h: HoldingPosition,
    policy: ExitPolicy,
) -> HoldingDecision:
    """레거시 호환 — PositionContext 없이 기본 TP/SL만 평가."""
    rate = h.pnl_rate
    if rate >= policy.take_profit_pct:
        signal = SellSignal.TAKE_PROFIT
        reason = f"수익률 {rate}% >= TP {policy.take_profit_pct}% → 익절 추천"
    elif rate <= policy.stop_loss_pct:
        signal = SellSignal.STOP_LOSS
        reason = f"수익률 {rate}% <= SL {policy.stop_loss_pct}% → 손절 추천"
    else:
        signal = SellSignal.HOLD
        reason = f"수익률 {rate}% — 보유 유지 ({policy.stop_loss_pct}% < x < {policy.take_profit_pct}%)"
    return HoldingDecision(
        symbol=h.symbol, name=h.name, qty=h.qty, sell_qty=h.qty if signal != SellSignal.HOLD else 0,
        avg_buy_price=h.avg_buy_price, cur_price=h.cur_price,
        pnl=h.pnl, pnl_rate=h.pnl_rate,
        signal=signal, reason=reason,
    )


def evaluate_all(
    holdings: list[HoldingPosition],
    policy: ExitPolicy = ExitPolicy(),
    contexts: Optional[dict[str, PositionContext]] = None,
) -> list[HoldingDecision]:
    return [
        evaluate_holding(h, policy, contexts.get(h.symbol) if contexts else None)
        for h in holdings
    ]


def render_decisions_table(decisions: list[HoldingDecision]) -> str:
    """markdown 표."""
    lines = [
        "| symbol | name | qty | sell | avg | cur | pnl_rate | signal | reason |",
        "|--------|------|----:|-----:|----:|----:|---------:|--------|--------|",
    ]
    sig_labels = {
        SellSignal.HOLD: "HOLD",
        SellSignal.TAKE_PROFIT: "TP",
        SellSignal.STOP_LOSS: "SL",
        SellSignal.TRAILING_STOP: "TRAIL",
        SellSignal.BREAKEVEN_STOP: "BE",
        SellSignal.PARTIAL_TP: "P-TP",
        SellSignal.TIME_TIGHTENED_SL: "T-SL",
    }
    for d in decisions:
        sig_label = sig_labels.get(d.signal, d.signal.value)
        lines.append(
            f"| {d.symbol} | {d.name} | {d.qty:,} | {d.sell_qty:,} | "
            f"{int(d.avg_buy_price):,} | {int(d.cur_price):,} | "
            f"{float(d.pnl_rate):+.2f}% | {sig_label} | {d.reason[:50]} |"
        )
    return "\n".join(lines)


__all__ = [
    "ExitPolicy", "HoldingDecision", "PositionContext", "SellSignal",
    "STRATEGY_EXIT_PROFILES", "resolve_policy",
    "evaluate_holding", "evaluate_all", "render_decisions_table",
]
