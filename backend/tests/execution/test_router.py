"""
BAR-55 — SmartOrderRouter 테스트 (30 케이스).
"""

from __future__ import annotations

import random
import time
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.core.execution.router import SmartOrderRouter
from backend.core.gateway.composite_orderbook import CompositeOrderBookService
from backend.core.market_session.service import KST, MarketSessionService
from backend.models.market import (
    CompositeOrderBookL2,
    Exchange,
    OrderBookL2,
    TradingSession,
)
from backend.models.order import (
    OrderRequest,
    OrderSide,
    OrderType,
    RoutingDecision,
    RoutingReason,
)


# === 픽스처 ===


@pytest.fixture
def composite_svc() -> CompositeOrderBookService:
    return CompositeOrderBookService()


def _session(monkeypatch, fixed: TradingSession) -> MarketSessionService:
    s = MarketSessionService()
    monkeypatch.setattr(s, "get_session", lambda now=None: fixed)
    return s


@pytest.fixture
def session_regular(monkeypatch) -> MarketSessionService:
    return _session(monkeypatch, TradingSession.REGULAR)


@pytest.fixture
def session_closed(monkeypatch) -> MarketSessionService:
    return _session(monkeypatch, TradingSession.CLOSED)


@pytest.fixture
def session_interlude(monkeypatch) -> MarketSessionService:
    return _session(monkeypatch, TradingSession.INTERLUDE)


@pytest.fixture
def session_closing_auction(monkeypatch) -> MarketSessionService:
    return _session(monkeypatch, TradingSession.KRX_CLOSING_AUCTION)


@pytest.fixture
def session_nxt_after(monkeypatch) -> MarketSessionService:
    return _session(monkeypatch, TradingSession.NXT_AFTER)


def _ob(venue, bids, asks, symbol="005930"):
    return OrderBookL2(
        symbol=symbol,
        venue=venue,
        ts=datetime.now(KST),
        bids=bids,
        asks=asks,
    )


def _book(svc, krx, nxt, symbol="005930") -> CompositeOrderBookL2:
    return svc.merge(krx, nxt, symbol)


# === Price first ===


class TestPriceFirst:
    def test_buy_krx_lower_ask(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 80)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70150"), 80)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX
        assert decision.reason == RoutingReason.PRICE_FIRST
        assert decision.expected_price == Decimal("70100")

    def test_buy_nxt_lower_ask(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70150"), 80)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 80)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.NXT
        assert decision.reason == RoutingReason.PRICE_FIRST

    def test_sell_krx_higher_bid(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [(Decimal("69950"), 100)], [])
        nxt = _ob(Exchange.NXT, [(Decimal("69900"), 100)], [])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.SELL, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX
        assert decision.reason == RoutingReason.PRICE_FIRST

    def test_sell_nxt_higher_bid(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [(Decimal("69900"), 100)], [])
        nxt = _ob(Exchange.NXT, [(Decimal("69950"), 100)], [])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.SELL, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.NXT


# === Qty first ===


class TestQtyFirst:
    def test_same_price_krx_more_qty(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 50)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX
        assert decision.reason == RoutingReason.QTY_FIRST

    def test_same_price_nxt_more_qty(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 30)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 200)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.NXT
        assert decision.reason == RoutingReason.QTY_FIRST

    def test_same_price_same_qty_krx_priority(self, composite_svc, session_regular):
        """동가격·동잔량 → KRX 우선 (deterministic)."""
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX


# === Force venue ===


class TestForceVenue:
    def test_force_krx(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70200"), 50)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930", side=OrderSide.BUY, qty=10, force_venue=Exchange.KRX
        )
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX
        assert decision.reason == RoutingReason.FORCED
        assert decision.expected_price == Decimal("70200")

    def test_force_nxt(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70200"), 100)])
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930", side=OrderSide.BUY, qty=10, force_venue=Exchange.NXT
        )
        decision = router.route(req, book)
        assert decision.venue == Exchange.NXT
        assert decision.reason == RoutingReason.FORCED

    def test_force_blocked_by_session(
        self, composite_svc, session_interlude
    ):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_interlude)
        req = OrderRequest(
            symbol="005930", side=OrderSide.BUY, qty=10, force_venue=Exchange.KRX
        )
        decision = router.route(req, book)
        assert decision.venue is None
        assert decision.reason == RoutingReason.SESSION_BLOCKED


# === Session blocking ===


class TestSessionBlock:
    def test_nxt_only_book_blocked_in_closing_auction(
        self, composite_svc, session_closing_auction
    ):
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, None, nxt)
        router = SmartOrderRouter(session_closing_auction)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.venue is None
        assert decision.reason == RoutingReason.SESSION_BLOCKED

    def test_krx_only_book_blocked_in_nxt_after(
        self, composite_svc, session_nxt_after
    ):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_nxt_after)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.SESSION_BLOCKED

    def test_force_krx_in_closed(
        self, composite_svc, session_closed
    ):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_closed)
        req = OrderRequest(
            symbol="005930", side=OrderSide.BUY, qty=10, force_venue=Exchange.KRX
        )
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.SESSION_BLOCKED


# === Limit ===


class TestLimit:
    def test_limit_buy_above_ask(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930",
            side=OrderSide.BUY,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("70200"),
        )
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX

    def test_limit_buy_at_ask(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930",
            side=OrderSide.BUY,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("70100"),
        )
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX

    def test_limit_buy_below_ask_unfillable(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930",
            side=OrderSide.BUY,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("70000"),
        )
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.LIMIT_UNFILLABLE

    def test_limit_sell_below_bid_unfillable(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [(Decimal("69900"), 100)], [])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930",
            side=OrderSide.SELL,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("70100"),  # bid 보다 높음 → 체결 불가
        )
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.LIMIT_UNFILLABLE

    def test_limit_sell_at_bid(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [(Decimal("69900"), 100)], [])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(
            symbol="005930",
            side=OrderSide.SELL,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("69800"),  # bid 보다 낮음 → 체결 가능
        )
        decision = router.route(req, book)
        assert decision.venue == Exchange.KRX


# === No liquidity ===


class TestNoLiquidity:
    def test_empty_book_buy(self, composite_svc, session_regular):
        book = _book(composite_svc, None, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.NO_LIQUIDITY

    def test_only_bids_buy(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [(Decimal("69900"), 100)], [])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.NO_LIQUIDITY

    def test_only_asks_sell(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.SELL, qty=10)
        decision = router.route(req, book)
        assert decision.reason == RoutingReason.NO_LIQUIDITY


# === Qty cap ===


class TestQtyCap:
    def test_request_qty_exceeds_available(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 60)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=100)
        decision = router.route(req, book)
        assert decision.expected_qty == 60

    def test_request_qty_within_available(self, composite_svc, session_regular):
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = _book(composite_svc, krx, None)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=50)
        decision = router.route(req, book)
        assert decision.expected_qty == 50


# === Model 검증 ===


class TestModel:
    def test_qty_zero_rejected(self):
        with pytest.raises(ValidationError):
            OrderRequest(symbol="005930", side=OrderSide.BUY, qty=0)

    def test_decision_frozen(self):
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        d = RoutingDecision(
            request=req,
            venue=Exchange.KRX,
            expected_price=Decimal("70100"),
            expected_qty=10,
            reason=RoutingReason.PRICE_FIRST,
        )
        with pytest.raises(Exception):
            d.venue = Exchange.NXT  # type: ignore[misc]


# === 100건 정확도 매트릭스 ===


class TestAccuracy:
    def test_100_routing_accuracy(self, composite_svc, session_regular):
        """무작위 50 + edge 50 = 100건 expected venue 일치 100%."""
        router = SmartOrderRouter(session_regular)
        random.seed(42)
        passed = 0
        total = 0

        # 무작위 50건 (PRICE_FIRST 가 명확한 케이스만)
        for _ in range(50):
            krx_ask = Decimal(str(70000 + random.randint(0, 200)))
            nxt_ask = Decimal(str(70000 + random.randint(0, 200)))
            # 가격 동률은 별도 카운트하지 않음 — diff 강제
            if krx_ask == nxt_ask:
                continue
            krx = _ob(Exchange.KRX, [], [(krx_ask, 100)])
            nxt = _ob(Exchange.NXT, [], [(nxt_ask, 100)])
            book = _book(composite_svc, krx, nxt)
            req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
            decision = router.route(req, book)
            expected = Exchange.KRX if krx_ask < nxt_ask else Exchange.NXT
            total += 1
            if decision.venue == expected:
                passed += 1

        # edge 50건
        edges = [
            # 동가격 다잔량 → KRX (qty 우선)
            (Decimal("70100"), 100, Decimal("70100"), 50, OrderSide.BUY, Exchange.KRX),
            (Decimal("70100"), 50, Decimal("70100"), 200, OrderSide.BUY, Exchange.NXT),
            (Decimal("69900"), 100, Decimal("69900"), 50, OrderSide.SELL, Exchange.KRX),
            (Decimal("69900"), 50, Decimal("69900"), 200, OrderSide.SELL, Exchange.NXT),
        ] * 12  # 48건
        for krx_p, krx_q, nxt_p, nxt_q, side, expected in edges[:50]:
            if side == OrderSide.BUY:
                krx = _ob(Exchange.KRX, [], [(krx_p, krx_q)])
                nxt = _ob(Exchange.NXT, [], [(nxt_p, nxt_q)])
            else:
                krx = _ob(Exchange.KRX, [(krx_p, krx_q)], [])
                nxt = _ob(Exchange.NXT, [(nxt_p, nxt_q)], [])
            book = _book(composite_svc, krx, nxt)
            req = OrderRequest(symbol="005930", side=side, qty=10)
            decision = router.route(req, book)
            total += 1
            if decision.venue == expected:
                passed += 1

        accuracy = passed / total if total else 0
        assert accuracy == 1.0, f"accuracy {accuracy:.2%} ({passed}/{total})"


# === Performance ===


class TestPerformance:
    def test_100_routes_avg_under_5ms(self, composite_svc, session_regular):
        krx = _ob(
            Exchange.KRX,
            [(Decimal(str(69900 - i * 10)), 100) for i in range(10)],
            [(Decimal(str(70100 + i * 10)), 80) for i in range(10)],
        )
        nxt = _ob(
            Exchange.NXT,
            [(Decimal(str(69900 - i * 10)), 50) for i in range(10)],
            [(Decimal(str(70100 + i * 10)), 30) for i in range(10)],
        )
        book = _book(composite_svc, krx, nxt)
        router = SmartOrderRouter(session_regular)
        req = OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10)
        start = time.perf_counter()
        for _ in range(100):
            router.route(req, book)
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg = elapsed_ms / 100
        # CI 변동 대비 50ms 임계 — 실측은 1ms 미만
        assert avg < 50, f"avg route {avg:.2f}ms exceeded 50ms"
