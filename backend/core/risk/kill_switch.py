"""BAR-64 — KillSwitch + CircuitBreaker.

치명 위험 발생 시 자동 매매 즉시 중단.
- 일일 -3% 누적 손실
- 슬리피지 5분 3회
- 시세 단절 30초

발동 시 신규 진입 차단 (RiskEngine.is_active=False) + 운영자 알림 (BAR-64b).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Deque, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class KillSwitchReason(str, Enum):
    DAILY_LOSS = "daily_loss"
    SLIPPAGE = "slippage"
    GATEWAY_DISCONNECT = "gateway_disconnect"
    MANUAL = "manual"


class KillSwitchState(BaseModel):
    """현재 상태 — frozen."""

    model_config = ConfigDict(frozen=True)

    is_active: bool = False                  # True = 매매 차단
    triggered_at: Optional[datetime] = None
    reason: Optional[KillSwitchReason] = None
    cooldown_until: Optional[datetime] = None


class CircuitBreaker:
    """N events / window minutes 슬라이딩 윈도우. 발동 시 trip()."""

    def __init__(self, threshold: int = 3, window_seconds: int = 300) -> None:
        self._threshold = threshold
        self._window = timedelta(seconds=window_seconds)
        self._events: Deque[datetime] = deque()

    def record(self, now: datetime) -> bool:
        """이벤트 기록. 임계 도달 시 True 반환."""
        self._events.append(now)
        cutoff = now - self._window
        while self._events and self._events[0] < cutoff:
            self._events.popleft()
        return len(self._events) >= self._threshold

    def reset(self) -> None:
        self._events.clear()

    @property
    def count(self) -> int:
        return len(self._events)


class KillSwitch:
    """치명 위험 자동 차단 + 수동 발동."""

    DAILY_LOSS_PCT = Decimal("-0.03")        # -3%
    GATEWAY_DISCONNECT_SECONDS = 30
    COOLDOWN_HOURS = 4

    def __init__(self) -> None:
        self._state = KillSwitchState()
        self._daily_loss = Decimal("0")
        self._daily_pnl_base: Optional[Decimal] = None
        self._slippage_breaker = CircuitBreaker(threshold=3, window_seconds=300)
        self._gateway_disconnect_since: Optional[datetime] = None

    @property
    def state(self) -> KillSwitchState:
        return self._state

    def set_account_base(self, base_balance: Decimal) -> None:
        """일일 시작 잔고 기준."""
        self._daily_pnl_base = base_balance

    def record_loss(self, current_balance: Decimal, now: datetime) -> bool:
        """현 잔고 기준 일일 손실율 평가. 임계 도달 시 trip + True."""
        if self._daily_pnl_base is None or self._daily_pnl_base <= 0:
            return False
        loss_pct = (current_balance - self._daily_pnl_base) / self._daily_pnl_base
        if loss_pct <= self.DAILY_LOSS_PCT:
            self.trip(KillSwitchReason.DAILY_LOSS, now)
            return True
        return False

    def record_slippage_event(self, now: datetime) -> bool:
        if self._slippage_breaker.record(now):
            self.trip(KillSwitchReason.SLIPPAGE, now)
            return True
        return False

    def record_gateway_event(self, connected: bool, now: datetime) -> bool:
        if connected:
            self._gateway_disconnect_since = None
            return False
        if self._gateway_disconnect_since is None:
            self._gateway_disconnect_since = now
            return False
        elapsed = (now - self._gateway_disconnect_since).total_seconds()
        if elapsed >= self.GATEWAY_DISCONNECT_SECONDS:
            self.trip(KillSwitchReason.GATEWAY_DISCONNECT, now)
            return True
        return False

    def trip(self, reason: KillSwitchReason, now: datetime) -> None:
        cooldown = now + timedelta(hours=self.COOLDOWN_HOURS)
        self._state = KillSwitchState(
            is_active=True,
            triggered_at=now,
            reason=reason,
            cooldown_until=cooldown,
        )
        logger.warning("KillSwitch tripped: %s at %s", reason.value, now)

    def reset(self, now: datetime) -> bool:
        """cooldown 경과 시 reset 가능. 반환 = 성공 여부."""
        if not self._state.is_active:
            return True
        if self._state.cooldown_until and now < self._state.cooldown_until:
            return False
        self._state = KillSwitchState()
        self._slippage_breaker.reset()
        self._gateway_disconnect_since = None
        return True


__all__ = [
    "KillSwitchReason",
    "KillSwitchState",
    "CircuitBreaker",
    "KillSwitch",
]
