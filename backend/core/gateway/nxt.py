"""
BAR-53 — NxtGateway 1차 (시세 read-only).

Reference:
- Plan: docs/01-plan/features/bar-53-nxt-gateway.plan.md
- Design: docs/02-design/features/bar-53-nxt-gateway.design.md

핵심:
- INxtGateway Protocol — 표준 시그니처
- NxtGatewayManager — primary + fallback orchestrator + 세션 가드 + 헬스 루프
- MockNxtGateway — 1차 테스트/dev 용 구현체
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Protocol

from backend.core.market_session.service import KST, MarketSessionService
from backend.models.market import (
    Exchange,
    GatewayStatus,
    HealthStatus,
    OrderBookL2,
    Tick,
    Trade,
    TradingSession,
)


# NXT 가용 세션 (NXT 가용 거래소 매트릭스의 부분집합)
NXT_AVAILABLE_SESSIONS: frozenset[TradingSession] = frozenset(
    {
        TradingSession.NXT_PRE,
        TradingSession.KRX_PRE,
        TradingSession.REGULAR,
        TradingSession.KRX_AFTER,
        TradingSession.NXT_AFTER,
    }
)


TickCallback = Callable[[Tick], Awaitable[None]]
OrderBookCallback = Callable[[OrderBookL2], Awaitable[None]]
TradeCallback = Callable[[Trade], Awaitable[None]]


class INxtGateway(Protocol):
    """NXT 시세 게이트웨이 표준 인터페이스 (read-only)."""

    name: str

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_connected(self) -> bool: ...
    async def health_check(self) -> HealthStatus: ...

    async def subscribe_ticker(self, symbols: list[str]) -> None: ...
    async def subscribe_orderbook(self, symbols: list[str]) -> None: ...
    async def subscribe_trade(self, symbols: list[str]) -> None: ...
    async def unsubscribe(self, symbols: list[str]) -> None: ...

    def on_tick(self, callback: TickCallback) -> None: ...
    def on_orderbook(self, callback: OrderBookCallback) -> None: ...
    def on_trade(self, callback: TradeCallback) -> None: ...


# ─────────────────────────────────────────────
# 1차 구현: MockNxtGateway (테스트/dev)
# ─────────────────────────────────────────────


@dataclass
class _Subscriptions:
    ticker: set[str] = field(default_factory=set)
    orderbook: set[str] = field(default_factory=set)
    trade: set[str] = field(default_factory=set)


class MockNxtGateway:
    """테스트·개발용 in-memory 게이트웨이.

    실 게이트웨이(키움/KOSCOM) 는 BAR-53.5 후속 BAR 에서 동일 인터페이스로 plug-in.
    """

    name = "mock"

    def __init__(self) -> None:
        self._connected = False
        self._subs = _Subscriptions()
        self._tick_callbacks: list[TickCallback] = []
        self._orderbook_callbacks: list[OrderBookCallback] = []
        self._trade_callbacks: list[TradeCallback] = []
        self._last_msg_at: Optional[datetime] = None
        self._fail_health: bool = False  # 테스트 토글

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def is_connected(self) -> bool:
        return self._connected

    async def health_check(self) -> HealthStatus:
        if self._fail_health or not self._connected:
            return HealthStatus(is_healthy=False, error="disconnected")
        lag = None
        if self._last_msg_at is not None:
            lag = (datetime.now(KST) - self._last_msg_at).total_seconds()
        return HealthStatus(
            is_healthy=True,
            last_msg_at=self._last_msg_at,
            lag_seconds=lag,
        )

    async def subscribe_ticker(self, symbols: list[str]) -> None:
        self._subs.ticker.update(symbols)

    async def subscribe_orderbook(self, symbols: list[str]) -> None:
        self._subs.orderbook.update(symbols)

    async def subscribe_trade(self, symbols: list[str]) -> None:
        self._subs.trade.update(symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        for s in symbols:
            self._subs.ticker.discard(s)
            self._subs.orderbook.discard(s)
            self._subs.trade.discard(s)

    def on_tick(self, callback: TickCallback) -> None:
        self._tick_callbacks.append(callback)

    def on_orderbook(self, callback: OrderBookCallback) -> None:
        self._orderbook_callbacks.append(callback)

    def on_trade(self, callback: TradeCallback) -> None:
        self._trade_callbacks.append(callback)

    # === 테스트 도우미 — 실제 외부 메시지 시뮬 ===

    async def emit_tick(self, tick: Tick) -> None:
        if tick.symbol not in self._subs.ticker:
            return
        self._last_msg_at = datetime.now(KST)
        for cb in self._tick_callbacks:
            await cb(tick)

    async def emit_orderbook(self, ob: OrderBookL2) -> None:
        if ob.symbol not in self._subs.orderbook:
            return
        self._last_msg_at = datetime.now(KST)
        for cb in self._orderbook_callbacks:
            await cb(ob)

    async def emit_trade(self, tr: Trade) -> None:
        if tr.symbol not in self._subs.trade:
            return
        self._last_msg_at = datetime.now(KST)
        for cb in self._trade_callbacks:
            await cb(tr)

    # 테스트용 토글
    def force_unhealthy(self, value: bool = True) -> None:
        self._fail_health = value


# ─────────────────────────────────────────────
# Manager — primary + fallback + 세션 가드
# ─────────────────────────────────────────────


class NxtGatewayManager:
    """primary + fallback 오케스트레이터.

    - 30초 누적 disconnect → fallback 전환
    - 5분 무수신 → 재연결 (exponential backoff)
    - 재연결 실패 3회 → DEGRADED, fallback 도 실패 시 DOWN
    - TradingSession 가용 외 → subscribe pending
    """

    def __init__(
        self,
        primary: INxtGateway,
        fallback: Optional[INxtGateway],
        session_service: MarketSessionService,
        primary_fail_threshold_seconds: float = 30.0,
        msg_lag_threshold_seconds: float = 300.0,
        max_reconnect_attempts: int = 3,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._session = session_service
        self._primary_fail_threshold = primary_fail_threshold_seconds
        self._msg_lag_threshold = msg_lag_threshold_seconds
        self._max_reconnect = max_reconnect_attempts

        self._active: INxtGateway = primary
        self._status: GatewayStatus = GatewayStatus.OK
        self._reconnect_attempts = 0
        self._primary_down_since: Optional[datetime] = None

        # pending subscription buffers (세션 가드)
        self._pending_ticker: set[str] = set()
        self._pending_orderbook: set[str] = set()
        self._pending_trade: set[str] = set()

    @property
    def active(self) -> INxtGateway:
        return self._active

    @property
    def status(self) -> GatewayStatus:
        return self._status

    async def start(self) -> None:
        await self._active.connect()
        self._status = GatewayStatus.OK

    async def stop(self) -> None:
        await self._primary.disconnect()
        if self._fallback is not None:
            try:
                await self._fallback.disconnect()
            except Exception:
                pass

    # === Subscribe (세션 가드 포함) ===

    def _is_nxt_available(self) -> bool:
        return self._session.get_session() in NXT_AVAILABLE_SESSIONS

    async def subscribe_ticker(self, symbols: list[str]) -> None:
        if not self._is_nxt_available():
            self._pending_ticker.update(symbols)
            return
        await self._active.subscribe_ticker(symbols)

    async def subscribe_orderbook(self, symbols: list[str]) -> None:
        if not self._is_nxt_available():
            self._pending_orderbook.update(symbols)
            return
        await self._active.subscribe_orderbook(symbols)

    async def subscribe_trade(self, symbols: list[str]) -> None:
        if not self._is_nxt_available():
            self._pending_trade.update(symbols)
            return
        await self._active.subscribe_trade(symbols)

    async def unsubscribe(self, symbols: list[str]) -> None:
        await self._active.unsubscribe(symbols)
        for s in symbols:
            self._pending_ticker.discard(s)
            self._pending_orderbook.discard(s)
            self._pending_trade.discard(s)

    async def flush_pending(self) -> None:
        """세션 진입 시 보류된 subscribe 적용 (외부 스케줄러에서 주기 호출)."""
        if not self._is_nxt_available():
            return
        if self._pending_ticker:
            await self._active.subscribe_ticker(list(self._pending_ticker))
            self._pending_ticker.clear()
        if self._pending_orderbook:
            await self._active.subscribe_orderbook(list(self._pending_orderbook))
            self._pending_orderbook.clear()
        if self._pending_trade:
            await self._active.subscribe_trade(list(self._pending_trade))
            self._pending_trade.clear()

    def on_tick(self, callback: TickCallback) -> None:
        self._primary.on_tick(callback)
        if self._fallback is not None:
            self._fallback.on_tick(callback)

    def on_orderbook(self, callback: OrderBookCallback) -> None:
        self._primary.on_orderbook(callback)
        if self._fallback is not None:
            self._fallback.on_orderbook(callback)

    def on_trade(self, callback: TradeCallback) -> None:
        self._primary.on_trade(callback)
        if self._fallback is not None:
            self._fallback.on_trade(callback)

    # === Health / Failover ===

    async def evaluate_health(self) -> GatewayStatus:
        """1 회 헬스 평가 (외부 스케줄러에서 5초마다 호출). 상태 갱신 후 반환."""
        primary_health = await self._primary.health_check()

        # primary down 추적
        if not primary_health.is_healthy:
            now = datetime.now(KST)
            if self._primary_down_since is None:
                self._primary_down_since = now
            elapsed = (now - self._primary_down_since).total_seconds()
            if (
                elapsed >= self._primary_fail_threshold
                and self._active is self._primary
            ):
                await self._failover()
        else:
            self._primary_down_since = None

        # 메시지 lag 평가
        active_health = await self._active.health_check()
        if active_health.is_healthy and (
            active_health.lag_seconds is not None
            and active_health.lag_seconds > self._msg_lag_threshold
        ):
            await self._reconnect_active()

        # 종합 상태
        if active_health.is_healthy:
            # fallback 운용 중이면 OK 가 아니라 DEGRADED 유지
            self._status = (
                GatewayStatus.OK
                if self._active is self._primary
                else GatewayStatus.DEGRADED
            )
            self._reconnect_attempts = 0
        else:
            if self._reconnect_attempts >= self._max_reconnect:
                # fallback 도 실패하면 DOWN
                if self._fallback is None or self._active is self._fallback:
                    fb_health = (
                        await self._fallback.health_check()
                        if self._fallback is not None
                        else HealthStatus(is_healthy=False)
                    )
                    self._status = (
                        GatewayStatus.DOWN
                        if (self._fallback is None or not fb_health.is_healthy)
                        else GatewayStatus.DEGRADED
                    )
                else:
                    self._status = GatewayStatus.DEGRADED
            else:
                self._status = GatewayStatus.DEGRADED

        return self._status

    async def _failover(self) -> None:
        if self._fallback is None:
            self._status = GatewayStatus.DEGRADED
            return
        try:
            await self._fallback.connect()
            self._active = self._fallback
            self._status = GatewayStatus.DEGRADED
        except Exception:
            self._status = GatewayStatus.DOWN

    async def _reconnect_active(self) -> None:
        """exponential backoff (1, 2, 4, 8, 16, 32s)."""
        for i in range(self._max_reconnect):
            self._reconnect_attempts += 1
            try:
                await self._active.disconnect()
                await self._active.connect()
                hc = await self._active.health_check()
                if hc.is_healthy:
                    self._reconnect_attempts = 0
                    return
            except Exception:
                pass
            await asyncio.sleep(min(2 ** i, 32))
        # 3회 실패 → fallback 시도
        if self._active is self._primary and self._fallback is not None:
            await self._failover()
        else:
            self._status = GatewayStatus.DOWN


__all__ = [
    "NXT_AVAILABLE_SESSIONS",
    "INxtGateway",
    "MockNxtGateway",
    "NxtGatewayManager",
]
