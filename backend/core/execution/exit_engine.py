"""BAR-63 — ExitEngine.

ExitPlan + PositionState + current_price/now → 새 PositionState + list[ExitOrder].

평가 순서:
1. time_exit (잔여 전량)
2. SL (잔여 전량) — sl_at_effective = pos.sl_at or entry_price * (1 + plan.stop_loss.fixed_pct)
3. TP 단계 (가격 임계 도달 + 미발동 단계만)
4. breakeven: TP1 발동 후 sl_at 갱신
"""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Optional

from backend.models.exit_order import ExitOrder, ExitReason, PositionState
from backend.models.strategy import ExitPlan


_TP_REASONS = (ExitReason.TP1, ExitReason.TP2, ExitReason.TP3)


class ExitEngine:
    """ExitPlan 평가 — 함수형 (frozen state in/out)."""

    def evaluate(
        self,
        pos: PositionState,
        plan: ExitPlan,
        current_price: Decimal,
        now: datetime,
    ) -> tuple[PositionState, list[ExitOrder]]:
        if pos.qty <= 0:
            return pos, []   # 전량 청산 완료

        orders: list[ExitOrder] = []
        new_qty = pos.qty
        new_tp = pos.tp_filled
        new_sl = pos.sl_at
        # high_water_mark 갱신 (current_price > 기존 hwm 이면 update)
        new_hwm = pos.high_water_mark
        if new_hwm is None or current_price > new_hwm:
            new_hwm = current_price

        # 0. BAR-OPS-09 Phase C — 보유 기간 게이트 (swing 전략용, 2026-05-27)
        # max_hold_days 도달 시 강제 TIME_EXIT (손익 무관, 우선순위 최고)
        # min_hold_days 미달 시 모든 청산 평가 차단 (단 max_hold 트리거는 예외)
        if (plan.max_hold_days is not None or plan.min_hold_days is not None) \
                and pos.entry_time is not None:
            days_held = (now - pos.entry_time).days
            # max 도달 강제 청산 (우선)
            if plan.max_hold_days is not None and days_held >= plan.max_hold_days:
                orders.append(ExitOrder(
                    symbol=pos.symbol, qty=new_qty,
                    target_price=current_price, reason=ExitReason.TIME_EXIT,
                ))
                return self._with_state(
                    pos, qty=Decimal(0), tp_filled=new_tp, sl_at=new_sl, hwm=new_hwm,
                ), orders
            # min 미달 시 청산 평가 차단 (hwm 갱신만 유지)
            if plan.min_hold_days is not None and days_held < plan.min_hold_days:
                return self._with_state(
                    pos, qty=new_qty, tp_filled=new_tp, sl_at=new_sl, hwm=new_hwm,
                ), orders

        # 1. time_exit
        if plan.time_exit is not None and now.time() >= plan.time_exit:
            orders.append(
                ExitOrder(
                    symbol=pos.symbol,
                    qty=new_qty,
                    target_price=current_price,
                    reason=ExitReason.TIME_EXIT,
                )
            )
            return self._with_state(
                pos, qty=Decimal(0), tp_filled=new_tp, sl_at=new_sl, hwm=new_hwm,
            ), orders

        # 2. SL — 효과적 SL 가격 계산 (breakeven sl_at > trail_stages > time_stages > fixed)
        # trail_sl 은 peak 기반 — sl_at(breakeven) 보다 더 높으면 갱신
        trail_sl = plan.trail_sl_for_peak(pos.entry_price, new_hwm)
        if trail_sl is not None and (new_sl is None or trail_sl > new_sl):
            new_sl = trail_sl
        sl_eff = self._effective_sl(pos, plan, now, override_sl_at=new_sl)
        if sl_eff is not None and current_price <= sl_eff:
            orders.append(
                ExitOrder(
                    symbol=pos.symbol,
                    qty=new_qty,
                    target_price=current_price,
                    reason=(
                        ExitReason.TRAIL_STOP
                        if (trail_sl is not None and sl_eff == trail_sl)
                        else ExitReason.STOP_LOSS
                    ),
                )
            )
            return self._with_state(
                pos, qty=Decimal(0), tp_filled=new_tp, sl_at=new_sl, hwm=new_hwm,
            ), orders

        # 3. TP 단계 (현재 미발동 단계만)
        for idx, tier in enumerate(plan.take_profits):
            if idx + 1 <= new_tp:
                continue   # 이미 발동된 단계
            if current_price < tier.price:
                break      # 가격 미달 — 더 높은 단계 평가 X (take_profits 오름차순 정렬 보장: ExitPlan._qty_sum_le_one)
            # 발동: tier.qty_pct * initial_qty 만큼 청산
            qty = pos.initial_qty * tier.qty_pct
            qty = min(qty, new_qty)   # 잔여 초과 방지
            if qty > 0:
                orders.append(
                    ExitOrder(
                        symbol=pos.symbol,
                        qty=qty,
                        target_price=current_price,
                        reason=_TP_REASONS[min(idx, 2)],
                    )
                )
                new_qty = new_qty - qty
                new_tp = idx + 1

        # 4. breakeven_trigger — TP1 발동 후 sl_at 갱신 (trail 로 이미 갱신된 경우 더 높은 쪽)
        if (
            plan.breakeven_trigger is not None
            and new_tp >= 1
        ):
            be_sl = pos.entry_price * (Decimal(1) + plan.breakeven_trigger)
            if new_sl is None or be_sl > new_sl:
                new_sl = be_sl

        return self._with_state(
            pos, qty=new_qty, tp_filled=new_tp, sl_at=new_sl, hwm=new_hwm,
        ), orders

    @staticmethod
    def _effective_sl(
        pos: PositionState, plan: ExitPlan, now: Optional[datetime] = None,
        override_sl_at: Optional[Decimal] = None,
    ) -> Optional[Decimal]:
        # 우선순위: override_sl_at(=trail 또는 breakeven 갱신) > pos.sl_at > time_stages > fixed
        if override_sl_at is not None:
            return override_sl_at
        if pos.sl_at is not None:
            return pos.sl_at
        sl = plan.stop_loss
        if sl.time_stages and now is not None and pos.entry_time is not None:
            elapsed = (now - pos.entry_time).total_seconds()
            pct = sl.sl_pct_at_elapsed(elapsed)
        else:
            pct = sl.fixed_pct
        return pos.entry_price * (Decimal(1) + pct)

    @staticmethod
    def _with_state(
        pos: PositionState, qty: Decimal, tp_filled: int,
        sl_at: Optional[Decimal], hwm: Optional[Decimal] = None,
    ) -> PositionState:
        update = {"qty": qty, "tp_filled": tp_filled, "sl_at": sl_at}
        if hwm is not None:
            update["high_water_mark"] = hwm
        return pos.model_copy(update=update)


__all__ = ["ExitEngine"]
