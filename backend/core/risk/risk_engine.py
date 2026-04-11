"""
RiskEngine — 실시간 리스크 계산 및 주문 승인

모든 주문은 RiskEngine.approve() 통과 필수.
리스크 한도 초과 시 주문 거부.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Any

from backend.models.position import Order, Position
from backend.models.risk import RiskLimits, RiskStatus

logger = logging.getLogger(__name__)


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self._daily_pnl_pct: float = 0.0
        self._total_value: float = 0.0

    def approve(self, order: Order, positions: Dict[str, Position], balance: Any) -> tuple[bool, str]:
        """주문 승인 여부 판단

        Returns:
            (approved, reason) — False 시 이유 포함
        """
        # 일일 손실 한도 체크
        if self._daily_pnl_pct <= self.limits.daily_loss_limit_pct:
            return False, f"일일 손실 한도 초과: {self._daily_pnl_pct:.1%}"

        # 매수 주문만 추가 검사
        if order.side.value == "buy":
            # 동시 포지션 수 체크
            if len(positions) >= self.limits.max_concurrent_positions:
                return False, f"동시 포지션 한도 초과: {len(positions)}/{self.limits.max_concurrent_positions}"

            # 종목별 비중 체크
            order_value = order.quantity * (order.price or 0)
            if self._total_value > 0:
                position_pct = order_value / self._total_value
                if position_pct > self.limits.max_position_pct:
                    return False, f"종목 비중 초과: {position_pct:.1%} > {self.limits.max_position_pct:.1%}"

            # 총 익스포저 체크
            invested = sum(p.quantity * p.current_price for p in positions.values())
            new_exposure_pct = (invested + order_value) / self._total_value if self._total_value > 0 else 1.0
            if new_exposure_pct > self.limits.max_total_exposure_pct:
                return False, f"총 익스포저 한도 초과: {new_exposure_pct:.1%}"

        return True, "승인"

    def update_daily_pnl(self, pnl_pct: float) -> None:
        self._daily_pnl_pct = pnl_pct

    def update_total_value(self, value: float) -> None:
        self._total_value = value

    def get_status(self, positions: Dict[str, Position]) -> RiskStatus:
        invested = sum(p.quantity * p.current_price for p in positions.values()) if positions else 0
        exposure_pct = invested / self._total_value if self._total_value > 0 else 0.0
        return RiskStatus(
            current_exposure_pct=exposure_pct,
            daily_pnl_pct=self._daily_pnl_pct,
            position_count=len(positions),
            daily_limit_breached=self._daily_pnl_pct <= self.limits.daily_loss_limit_pct,
            new_entry_blocked=self._daily_pnl_pct <= self.limits.daily_loss_limit_pct,
            limits=self.limits,
            timestamp=datetime.now(),
        )
