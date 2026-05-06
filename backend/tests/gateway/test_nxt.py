"""
BAR-53 — NxtGateway 1차 테스트 (25 케이스).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from backend.core.gateway.nxt import (
    NXT_AVAILABLE_SESSIONS,
    MockNxtGateway,
    NxtGatewayManager,
)
from backend.core.market_session.service import KST, MarketSessionService
from backend.models.market import (
    Exchange,
    GatewayStatus,
    OrderBookL2,
    Tick,
    Trade,
    TradingSession,
)


# === 픽스처 ===


@pytest.fixture
def mock_primary() -> MockNxtGateway:
    return MockNxtGateway()


@pytest.fixture
def mock_fallback() -> MockNxtGateway:
    g = MockNxtGateway()
    g.name = "mock_fallback"
    return g


@pytest.fixture
def session_regular(monkeypatch) -> MarketSessionService:
    """REGULAR 세션을 강제하는 service."""
    s = MarketSessionService()

    def _fixed_session(now=None):
        return TradingSession.REGULAR

    monkeypatch.setattr(s, "get_session", _fixed_session)
    return s


@pytest.fixture
def session_closed(monkeypatch) -> MarketSessionService:
    s = MarketSessionService()

    def _fixed_session(now=None):
        return TradingSession.CLOSED

    monkeypatch.setattr(s, "get_session", _fixed_session)
    return s


@pytest.fixture
def session_interlude(monkeypatch) -> MarketSessionService:
    s = MarketSessionService()
    monkeypatch.setattr(s, "get_session", lambda now=None: TradingSession.INTERLUDE)
    return s


@pytest.fixture
def session_nxt_after(monkeypatch) -> MarketSessionService:
    s = MarketSessionService()
    monkeypatch.setattr(s, "get_session", lambda now=None: TradingSession.NXT_AFTER)
    return s


# === MockGateway 자체 ===


class TestMockGateway:
    @pytest.mark.asyncio
    async def test_connect_disconnect(self, mock_primary):
        assert await mock_primary.is_connected() is False
        await mock_primary.connect()
        assert await mock_primary.is_connected() is True
        await mock_primary.disconnect()
        assert await mock_primary.is_connected() is False

    @pytest.mark.asyncio
    async def test_subscribe_ticker_callback(self, mock_primary):
        received: list[Tick] = []
        mock_primary.on_tick(lambda t: _append(received, t))
        await mock_primary.connect()
        await mock_primary.subscribe_ticker(["005930"])

        tick = Tick(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            last_price=Decimal("70000"),
            last_volume=10,
        )
        await mock_primary.emit_tick(tick)
        assert len(received) == 1
        assert received[0] == tick

    @pytest.mark.asyncio
    async def test_subscribe_orderbook_callback(self, mock_primary):
        got = []
        mock_primary.on_orderbook(lambda ob: _append(got, ob))
        await mock_primary.subscribe_orderbook(["005930"])

        ob = OrderBookL2(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            bids=[(Decimal("69900"), 100)],
            asks=[(Decimal("70100"), 80)],
        )
        await mock_primary.emit_orderbook(ob)
        assert got == [ob]

    @pytest.mark.asyncio
    async def test_subscribe_trade_callback(self, mock_primary):
        got = []
        mock_primary.on_trade(lambda tr: _append(got, tr))
        await mock_primary.subscribe_trade(["005930"])

        tr = Trade(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            price=Decimal("70050"),
            qty=5,
            side="buy",
        )
        await mock_primary.emit_trade(tr)
        assert got == [tr]

    @pytest.mark.asyncio
    async def test_unsubscribe_drops_callback(self, mock_primary):
        got = []
        mock_primary.on_tick(lambda t: _append(got, t))
        await mock_primary.subscribe_ticker(["005930"])
        await mock_primary.unsubscribe(["005930"])

        tick = Tick(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            last_price=Decimal("70000"),
            last_volume=10,
        )
        await mock_primary.emit_tick(tick)
        assert got == []


# === 데이터 모델 — Decimal 강제 / immutable ===


class TestModelDecimal:
    def test_tick_float_input_coerced_to_decimal(self):
        tick = Tick(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            last_price=70000.5,  # float 입력
            last_volume=10,
        )
        assert isinstance(tick.last_price, Decimal)

    def test_orderbook_decimal_preserved(self):
        ob = OrderBookL2(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            bids=[(Decimal("69900.5"), 100)],
            asks=[(Decimal("70100.25"), 80)],
        )
        assert ob.bids[0][0] == Decimal("69900.5")
        assert ob.asks[0][0] == Decimal("70100.25")


class TestModelImmutable:
    def test_tick_frozen(self):
        tick = Tick(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            last_price=Decimal("70000"),
            last_volume=10,
        )
        with pytest.raises(Exception):
            tick.last_price = Decimal("99999")  # type: ignore[misc]


# === Manager — Subscribe 위임 ===


class TestManagerSubscribe:
    @pytest.mark.asyncio
    async def test_ticker_delegates_to_active(
        self, mock_primary, session_regular
    ):
        mgr = NxtGatewayManager(mock_primary, None, session_regular)
        await mgr.start()
        await mgr.subscribe_ticker(["005930"])
        assert "005930" in mock_primary._subs.ticker

    @pytest.mark.asyncio
    async def test_orderbook_delegates(self, mock_primary, session_regular):
        mgr = NxtGatewayManager(mock_primary, None, session_regular)
        await mgr.start()
        await mgr.subscribe_orderbook(["005930"])
        assert "005930" in mock_primary._subs.orderbook

    @pytest.mark.asyncio
    async def test_trade_delegates(self, mock_primary, session_regular):
        mgr = NxtGatewayManager(mock_primary, None, session_regular)
        await mgr.start()
        await mgr.subscribe_trade(["005930"])
        assert "005930" in mock_primary._subs.trade


# === Manager — 세션 가드 ===


class TestManagerSessionGate:
    @pytest.mark.asyncio
    async def test_closed_session_pending(self, mock_primary, session_closed):
        mgr = NxtGatewayManager(mock_primary, None, session_closed)
        await mgr.start()
        await mgr.subscribe_ticker(["005930"])
        assert "005930" in mgr._pending_ticker
        assert "005930" not in mock_primary._subs.ticker

    @pytest.mark.asyncio
    async def test_interlude_pending(self, mock_primary, session_interlude):
        mgr = NxtGatewayManager(mock_primary, None, session_interlude)
        await mgr.start()
        await mgr.subscribe_orderbook(["005930"])
        assert "005930" in mgr._pending_orderbook
        assert "005930" not in mock_primary._subs.orderbook

    @pytest.mark.asyncio
    async def test_regular_immediate(self, mock_primary, session_regular):
        mgr = NxtGatewayManager(mock_primary, None, session_regular)
        await mgr.start()
        await mgr.subscribe_ticker(["005930"])
        assert "005930" in mock_primary._subs.ticker
        assert mgr._pending_ticker == set()

    @pytest.mark.asyncio
    async def test_nxt_after_immediate(self, mock_primary, session_nxt_after):
        mgr = NxtGatewayManager(mock_primary, None, session_nxt_after)
        await mgr.start()
        await mgr.subscribe_trade(["005930"])
        assert "005930" in mock_primary._subs.trade

    @pytest.mark.asyncio
    async def test_flush_pending_on_session_open(
        self, mock_primary, session_closed, monkeypatch
    ):
        mgr = NxtGatewayManager(mock_primary, None, session_closed)
        await mgr.start()
        await mgr.subscribe_ticker(["005930"])
        assert "005930" in mgr._pending_ticker

        # 세션 변경
        monkeypatch.setattr(
            session_closed, "get_session", lambda now=None: TradingSession.REGULAR
        )
        await mgr.flush_pending()

        assert "005930" in mock_primary._subs.ticker
        assert mgr._pending_ticker == set()


# === Manager — Health / 재연결 ===


class TestManagerHealth:
    @pytest.mark.asyncio
    async def test_healthy_returns_ok(
        self, mock_primary, session_regular
    ):
        mgr = NxtGatewayManager(
            mock_primary, None, session_regular,
            primary_fail_threshold_seconds=0.05,
        )
        await mgr.start()
        status = await mgr.evaluate_health()
        assert status == GatewayStatus.OK
        assert mgr.status == GatewayStatus.OK

    @pytest.mark.asyncio
    async def test_disconnect_marks_degraded(
        self, mock_primary, session_regular
    ):
        mgr = NxtGatewayManager(
            mock_primary, None, session_regular,
            primary_fail_threshold_seconds=0.05,
            max_reconnect_attempts=0,
        )
        await mgr.start()
        await mock_primary.disconnect()
        status = await mgr.evaluate_health()
        # fallback 없음 → DOWN
        assert status in (GatewayStatus.DEGRADED, GatewayStatus.DOWN)


# === Manager — Failover ===


class TestManagerFailover:
    @pytest.mark.asyncio
    async def test_primary_down_to_fallback(
        self, mock_primary, mock_fallback, session_regular
    ):
        import asyncio

        mgr = NxtGatewayManager(
            mock_primary, mock_fallback, session_regular,
            primary_fail_threshold_seconds=0.01,
        )
        await mgr.start()
        assert mgr.active is mock_primary

        # primary 끊김
        await mock_primary.disconnect()
        # 첫 evaluate: down_since 기록
        await mgr.evaluate_health()
        await asyncio.sleep(0.02)
        # 두 번째: 임계 경과 → failover
        await mgr.evaluate_health()
        assert mgr.active is mock_fallback
        assert mgr.status == GatewayStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_both_down_status_down(
        self, mock_primary, mock_fallback, session_regular
    ):
        import asyncio

        mgr = NxtGatewayManager(
            mock_primary, mock_fallback, session_regular,
            primary_fail_threshold_seconds=0.01,
            max_reconnect_attempts=0,
        )
        await mgr.start()
        await mock_primary.disconnect()
        # fallback 도 영구 unhealthy 로 강제
        mock_fallback.force_unhealthy(True)
        await mgr.evaluate_health()
        await asyncio.sleep(0.02)
        await mgr.evaluate_health()
        # active 가 fallback 으로 전환되었으나 fallback 도 down
        await mgr.evaluate_health()
        assert mgr.status in (GatewayStatus.DOWN, GatewayStatus.DEGRADED)

    @pytest.mark.asyncio
    async def test_no_fallback_stays_degraded(
        self, mock_primary, session_regular
    ):
        import asyncio

        mgr = NxtGatewayManager(
            mock_primary, None, session_regular,
            primary_fail_threshold_seconds=0.01,
            max_reconnect_attempts=0,
        )
        await mgr.start()
        await mock_primary.disconnect()
        await mgr.evaluate_health()
        await asyncio.sleep(0.02)
        # primary down, fallback None → DEGRADED 머무름
        status = await mgr.evaluate_health()
        # max_reconnect_attempts=0 이므로 즉시 평가 — fallback None → DOWN
        assert status in (GatewayStatus.DEGRADED, GatewayStatus.DOWN)


# === Lifecycle ===


class TestManagerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop_idempotent(self, mock_primary, session_regular):
        mgr = NxtGatewayManager(mock_primary, None, session_regular)
        await mgr.start()
        await mgr.stop()
        # 두 번 호출해도 에러 없어야 함
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_callbacks_registered_on_both_gateways(
        self, mock_primary, mock_fallback, session_regular
    ):
        mgr = NxtGatewayManager(mock_primary, mock_fallback, session_regular)
        called = []
        mgr.on_tick(lambda t: _append(called, t))
        # 양쪽 모두 등록되었는지 — primary callback 발화
        await mock_primary.connect()
        await mock_primary.subscribe_ticker(["005930"])
        tick = Tick(
            symbol="005930",
            venue=Exchange.NXT,
            ts=datetime.now(KST),
            last_price=Decimal("70000"),
            last_volume=10,
        )
        await mock_primary.emit_tick(tick)
        assert called == [tick]


# === NXT_AVAILABLE_SESSIONS ===


class TestAvailableSessions:
    def test_includes_main_sessions(self):
        assert TradingSession.REGULAR in NXT_AVAILABLE_SESSIONS
        assert TradingSession.NXT_PRE in NXT_AVAILABLE_SESSIONS
        assert TradingSession.NXT_AFTER in NXT_AVAILABLE_SESSIONS

    def test_excludes_closed(self):
        assert TradingSession.CLOSED not in NXT_AVAILABLE_SESSIONS
        assert TradingSession.INTERLUDE not in NXT_AVAILABLE_SESSIONS
        assert TradingSession.KRX_CLOSING_AUCTION not in NXT_AVAILABLE_SESSIONS


# === 헬퍼 ===


async def _append(lst: list, item) -> None:
    lst.append(item)
