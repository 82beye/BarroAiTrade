"""
RiskEngine — 실시간 리스크 계산 및 주문 승인

모든 주문은 RiskEngine.approve() 통과 필수.
리스크 한도 초과 시 주문 거부.
종목별 손절/익절 조건 체크 및 자동 청산 명령 생성.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from backend.models.position import Order, Position
from backend.models.risk import RiskLimits, RiskStatus

logger = logging.getLogger(__name__)


class RiskEngine:
    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self._daily_pnl_pct: float = 0.0
        self._total_value: float = 0.0
        self._risk_events: List[Dict[str, Any]] = []  # 인메모리 이벤트 로그

    # ── 주문 승인 ──────────────────────────────────────────────────────────────

    def approve(self, order: Order, positions: Dict[str, Position], balance: Any) -> Tuple[bool, str]:
        """주문 승인 여부 판단

        Returns:
            (approved, reason) — False 시 이유 포함
        """
        # 일일 손실 한도 체크
        if self._daily_pnl_pct <= self.limits.daily_loss_limit_pct:
            reason = f"일일 손실 한도 초과: {self._daily_pnl_pct:.1%}"
            self._log_risk_event("order_blocked_daily_loss", reason=reason)
            return False, reason

        # 매수 주문만 추가 검사
        if order.side.value == "buy":
            # 동시 포지션 수 체크
            if len(positions) >= self.limits.max_concurrent_positions:
                reason = f"동시 포지션 한도 초과: {len(positions)}/{self.limits.max_concurrent_positions}"
                self._log_risk_event("order_blocked_max_positions", symbol=order.symbol, reason=reason)
                return False, reason

            # 종목별 비중 체크
            order_value = order.quantity * (order.price or 0)
            if self._total_value > 0:
                position_pct = order_value / self._total_value
                if position_pct > self.limits.max_position_pct:
                    reason = f"종목 비중 초과: {position_pct:.1%} > {self.limits.max_position_pct:.1%}"
                    self._log_risk_event("order_blocked_position_size", symbol=order.symbol, reason=reason)
                    return False, reason

            # 총 익스포저 체크
            invested = sum(p.quantity * p.current_price for p in positions.values())
            new_exposure_pct = (invested + order_value) / self._total_value if self._total_value > 0 else 1.0
            if new_exposure_pct > self.limits.max_total_exposure_pct:
                reason = f"총 익스포저 한도 초과: {new_exposure_pct:.1%} > {self.limits.max_total_exposure_pct:.1%}"
                self._log_risk_event("order_blocked_exposure", symbol=order.symbol, reason=reason)
                return False, reason

        return True, "승인"

    # ── 종목별 손절/익절 체크 ──────────────────────────────────────────────────

    def check_exit_conditions(self, position: Position) -> Optional[Tuple[str, str, float]]:
        """포지션 청산 조건 체크

        Returns:
            (action, reason, qty_pct) 또는 None
            action: "stop_loss" | "take_profit_1" | "take_profit_2"
            qty_pct: 청산할 비율 (1.0 = 전량)
        """
        pnl_pct = position.pnl_pct

        # 손절 체크
        if pnl_pct <= self.limits.stop_loss_pct:
            reason = f"손절 발동: {pnl_pct:.2%} <= {self.limits.stop_loss_pct:.2%}"
            self._log_risk_event("stop_loss_triggered", symbol=position.symbol, pnl_pct=pnl_pct, reason=reason)
            return ("stop_loss", reason, 1.0)

        # 2차 익절 체크 (전량)
        if pnl_pct >= self.limits.take_profit_2_pct:
            reason = f"2차 익절 발동: {pnl_pct:.2%} >= {self.limits.take_profit_2_pct:.2%}"
            self._log_risk_event("take_profit_2_triggered", symbol=position.symbol, pnl_pct=pnl_pct, reason=reason)
            return ("take_profit_2", reason, 1.0)

        # 1차 익절 체크 (부분)
        if pnl_pct >= self.limits.take_profit_1_pct:
            reason = f"1차 익절 발동: {pnl_pct:.2%} >= {self.limits.take_profit_1_pct:.2%}"
            self._log_risk_event("take_profit_1_triggered", symbol=position.symbol, pnl_pct=pnl_pct, reason=reason)
            return ("take_profit_1", reason, self.limits.take_profit_1_qty_pct)

        return None

    # ── 강제청산 ───────────────────────────────────────────────────────────────

    def get_force_close_symbols(self, positions: Dict[str, Position]) -> List[str]:
        """일일 손실 한도 초과 또는 강제청산 시간 도달 시 청산 대상 목록 반환"""
        symbols_to_close: List[str] = []

        # 일일 손실 한도 초과 시 전량 청산
        if self._daily_pnl_pct <= self.limits.daily_loss_limit_pct:
            symbols_to_close = list(positions.keys())
            if symbols_to_close:
                self._log_risk_event(
                    "force_close_daily_limit",
                    reason=f"일일 손실 한도 초과로 전량 청산: {self._daily_pnl_pct:.1%}",
                    symbols=symbols_to_close,
                )
            return symbols_to_close

        # 강제청산 시간 체크
        now = datetime.now()
        force_close_hour, force_close_min = map(int, self.limits.force_close_time.split(":"))
        force_close_threshold = now.replace(
            hour=force_close_hour,
            minute=force_close_min,
            second=0,
            microsecond=0,
        )
        if now >= force_close_threshold:
            symbols_to_close = list(positions.keys())
            if symbols_to_close:
                self._log_risk_event(
                    "force_close_time",
                    reason=f"강제청산 시간 도달: {self.limits.force_close_time}",
                    symbols=symbols_to_close,
                )

        return symbols_to_close

    # ── 상태 업데이트 ──────────────────────────────────────────────────────────

    def update_daily_pnl(self, pnl_pct: float) -> None:
        self._daily_pnl_pct = pnl_pct

    def update_total_value(self, value: float) -> None:
        self._total_value = value

    def update_limits(self, new_limits: RiskLimits) -> None:
        """리스크 한도 동적 변경"""
        old = self.limits
        self.limits = new_limits
        self._log_risk_event(
            "limits_updated",
            reason="리스크 한도 변경",
            old_limits=old.model_dump(),
            new_limits=new_limits.model_dump(),
        )
        logger.info("리스크 한도 변경: %s", new_limits.model_dump())

    # ── 상태 조회 ──────────────────────────────────────────────────────────────

    def get_status(self, positions: Dict[str, Position]) -> RiskStatus:
        invested = sum(p.quantity * p.current_price for p in positions.values()) if positions else 0
        exposure_pct = invested / self._total_value if self._total_value > 0 else 0.0
        daily_limit_breached = self._daily_pnl_pct <= self.limits.daily_loss_limit_pct
        return RiskStatus(
            current_exposure_pct=exposure_pct,
            daily_pnl_pct=self._daily_pnl_pct,
            position_count=len(positions),
            daily_limit_breached=daily_limit_breached,
            new_entry_blocked=daily_limit_breached,
            limits=self.limits,
            timestamp=datetime.now(),
        )

    def get_recent_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """최근 리스크 이벤트 반환"""
        return self._risk_events[-limit:]

    # ── 내부 헬퍼 ─────────────────────────────────────────────────────────────

    def _log_risk_event(self, event_type: str, symbol: Optional[str] = None,
                        pnl_pct: Optional[float] = None, reason: Optional[str] = None,
                        **kwargs: Any) -> None:
        """인메모리 리스크 이벤트 기록"""
        entry: Dict[str, Any] = {
            "event_type": event_type,
            "symbol": symbol,
            "pnl_pct": pnl_pct,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            **kwargs,
        }
        self._risk_events.append(entry)
        # 최대 1000개 유지
        if len(self._risk_events) > 1000:
            self._risk_events = self._risk_events[-1000:]
        logger.warning("[RISK] %s", entry)
