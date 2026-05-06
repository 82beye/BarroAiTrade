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
            return self._with_state(pos, qty=Decimal(0), tp_filled=new_tp, sl_at=new_sl), orders

        # 2. SL — 효과적 SL 가격 계산
        sl_eff = self._effective_sl(pos, plan)
        if sl_eff is not None and current_price <= sl_eff:
            orders.append(
                ExitOrder(
                    symbol=pos.symbol,
                    qty=new_qty,
                    target_price=current_price,
                    reason=ExitReason.STOP_LOSS,
                )
            )
            return self._with_state(pos, qty=Decimal(0), tp_filled=new_tp, sl_at=new_sl), orders

        # 3. TP 단계 (현재 미발동 단계만)
        for idx, tier in enumerate(plan.take_profits):
            if idx + 1 <= new_tp:
                continue   # 이미 발동된 단계
            if current_price < tier.price:
                break      # 가격 미달 — 더 높은 단계 평가 X
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

        # 4. breakeven_trigger — TP1 발동 후 sl_at 갱신
        if (
            plan.breakeven_trigger is not None
            and new_tp >= 1
            and new_sl is None  # 아직 갱신 안 됨
        ):
            # breakeven offset 적용 — entry * (1 + offset)
            new_sl = pos.entry_price * (Decimal(1) + plan.breakeven_trigger)

        return self._with_state(pos, qty=new_qty, tp_filled=new_tp, sl_at=new_sl), orders

    @staticmethod
    def _effective_sl(pos: PositionState, plan: ExitPlan) -> Optional[Decimal]:
        if pos.sl_at is not None:
            return pos.sl_at
        # fixed_pct 는 음수 (-0.02 = -2%)
        return pos.entry_price * (Decimal(1) + plan.stop_loss.fixed_pct)

    @staticmethod
    def _with_state(
        pos: PositionState, qty: Decimal, tp_filled: int, sl_at: Optional[Decimal]
    ) -> PositionState:
        return pos.model_copy(update={"qty": qty, "tp_filled": tp_filled, "sl_at": sl_at})


__all__ = ["ExitEngine"]
