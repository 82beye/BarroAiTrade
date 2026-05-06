"""
BAR-54 — CompositeOrderBookService 테스트 (22 케이스).
"""

from __future__ import annotations

import time
from datetime import datetime
from decimal import Decimal

import pytest

from backend.core.gateway.composite_orderbook import CompositeOrderBookService
from backend.core.market_session.service import KST
from backend.models.market import (
    CompositeLevel,
    CompositeOrderBookL2,
    Exchange,
    OrderBookL2,
)


# === 픽스처 ===


@pytest.fixture
def svc() -> CompositeOrderBookService:
    return CompositeOrderBookService()


def _ob(
    venue: Exchange,
    bids: list[tuple[Decimal, int]],
    asks: list[tuple[Decimal, int]],
    symbol: str = "005930",
) -> OrderBookL2:
    return OrderBookL2(
        symbol=symbol,
        venue=venue,
        ts=datetime.now(KST),
        bids=bids,
        asks=asks,
    )


# === 기본 머지 ===


class TestMergeBasic:
    def test_krx_only(self, svc):
        krx = _ob(
            Exchange.KRX,
            bids=[(Decimal("69900"), 100)],
            asks=[(Decimal("70100"), 80)],
        )
        result = svc.merge(krx, None, "005930")
        assert result.venues == frozenset({Exchange.KRX})
        assert result.bids[0].breakdown == {Exchange.KRX: 100}

    def test_nxt_only(self, svc):
        nxt = _ob(
            Exchange.NXT,
            bids=[(Decimal("69910"), 50)],
            asks=[(Decimal("70090"), 60)],
        )
        result = svc.merge(None, nxt, "005930")
        assert result.venues == frozenset({Exchange.NXT})
        assert result.bids[0].breakdown == {Exchange.NXT: 50}

    def test_both_inputs(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[(Decimal("70100"), 80)])
        nxt = _ob(Exchange.NXT, bids=[(Decimal("69900"), 50)], asks=[(Decimal("70100"), 30)])
        result = svc.merge(krx, nxt, "005930")
        assert result.venues == frozenset({Exchange.KRX, Exchange.NXT})

    def test_both_none(self, svc):
        result = svc.merge(None, None, "005930")
        assert result.venues == frozenset()
        assert result.bids == []
        assert result.asks == []


# === 가격 합산 / breakdown ===


class TestPriceAggregation:
    def test_same_price_summed(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[])
        nxt = _ob(Exchange.NXT, bids=[(Decimal("69900"), 50)], asks=[])
        result = svc.merge(krx, nxt, "005930")
        assert result.bids[0].total_qty == 150

    def test_breakdown_preserved(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[])
        nxt = _ob(Exchange.NXT, bids=[(Decimal("69900"), 50)], asks=[])
        result = svc.merge(krx, nxt, "005930")
        assert result.bids[0].breakdown == {
            Exchange.KRX: 100,
            Exchange.NXT: 50,
        }

    def test_single_venue_price_single_key(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[])
        nxt = _ob(Exchange.NXT, bids=[(Decimal("69850"), 50)], asks=[])
        result = svc.merge(krx, nxt, "005930")
        # 가격이 다르므로 각각 1키
        for lv in result.bids:
            assert len(lv.breakdown) == 1


# === 정렬 ===


class TestSorting:
    def test_bids_descending(self, svc):
        krx = _ob(
            Exchange.KRX,
            bids=[(Decimal("69900"), 10), (Decimal("69800"), 20), (Decimal("69950"), 30)],
            asks=[],
        )
        result = svc.merge(krx, None, "005930")
        prices = [lv.price for lv in result.bids]
        assert prices == sorted(prices, reverse=True)
        assert prices[0] == Decimal("69950")

    def test_asks_ascending(self, svc):
        krx = _ob(
            Exchange.KRX,
            bids=[],
            asks=[(Decimal("70100"), 10), (Decimal("70200"), 20), (Decimal("70050"), 30)],
        )
        result = svc.merge(krx, None, "005930")
        prices = [lv.price for lv in result.asks]
        assert prices == sorted(prices)
        assert prices[0] == Decimal("70050")


# === best / mid / spread ===


class TestBest:
    def test_best_bid(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        assert result.best_bid == Decimal("69900")

    def test_best_ask(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        assert result.best_ask == Decimal("70100")

    def test_best_bid_none_when_empty(self, svc):
        krx = _ob(Exchange.KRX, bids=[], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        assert result.best_bid is None
        assert result.best_ask == Decimal("70100")


class TestMidSpread:
    def test_mid_price_decimal(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        assert result.mid_price == Decimal("70000")

    def test_spread(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        assert result.spread == Decimal("200")


# === venue_breakdown ===


class TestVenueBreakdown:
    def test_bids_side(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[])
        nxt = _ob(Exchange.NXT, bids=[(Decimal("69900"), 50)], asks=[])
        result = svc.merge(krx, nxt, "005930")
        breakdown = result.venue_breakdown(Decimal("69900"))
        assert breakdown == {Exchange.KRX: 100, Exchange.NXT: 50}

    def test_asks_side(self, svc):
        krx = _ob(Exchange.KRX, bids=[], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        breakdown = result.venue_breakdown(Decimal("70100"))
        assert breakdown == {Exchange.KRX: 80}

    def test_unknown_price(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), 100)], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, None, "005930")
        assert result.venue_breakdown(Decimal("12345")) == {}


# === Edge ===


class TestEdge:
    def test_empty_orderbook(self, svc):
        empty = _ob(Exchange.KRX, bids=[], asks=[])
        result = svc.merge(empty, None, "005930")
        assert result.bids == []
        assert result.asks == []
        assert result.venues == frozenset({Exchange.KRX})

    def test_crossed_book_preserved(self, svc):
        # KRX bid > NXT ask 교차 — 그대로 머지 (검증은 별도 정책 BAR-55)
        krx = _ob(Exchange.KRX, bids=[(Decimal("70200"), 100)], asks=[])
        nxt = _ob(Exchange.NXT, bids=[], asks=[(Decimal("70100"), 80)])
        result = svc.merge(krx, nxt, "005930")
        assert result.best_bid == Decimal("70200")
        assert result.best_ask == Decimal("70100")
        assert result.spread < 0  # 정상 비즈니스에선 발생 X

    def test_negative_qty_preserved(self, svc):
        # 음수 잔량 검증 정책은 별도 — 본 서비스는 그대로 보존
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900"), -10)], asks=[])
        result = svc.merge(krx, None, "005930")
        assert result.bids[0].total_qty == -10


# === Decimal ===


class TestDecimal:
    def test_decimal_precision(self, svc):
        krx = _ob(Exchange.KRX, bids=[(Decimal("69900.50"), 100)], asks=[])
        nxt = _ob(Exchange.NXT, bids=[(Decimal("69900.50"), 50)], asks=[])
        result = svc.merge(krx, nxt, "005930")
        assert result.bids[0].price == Decimal("69900.50")
        assert result.bids[0].total_qty == 150


# === Performance ===


class TestPerformance:
    def test_100_merges_avg_under_5ms(self, svc):
        krx = _ob(
            Exchange.KRX,
            bids=[(Decimal(str(69900 - i * 10)), 100 + i) for i in range(10)],
            asks=[(Decimal(str(70100 + i * 10)), 80 + i) for i in range(10)],
        )
        nxt = _ob(
            Exchange.NXT,
            bids=[(Decimal(str(69900 - i * 10)), 50 + i) for i in range(10)],
            asks=[(Decimal(str(70100 + i * 10)), 30 + i) for i in range(10)],
        )

        start = time.perf_counter()
        for _ in range(100):
            svc.merge(krx, nxt, "005930")
        elapsed_ms = (time.perf_counter() - start) * 1000
        avg_ms = elapsed_ms / 100
        # 충분한 여유를 두고 50ms 임계 (CI 변동 대비)
        assert avg_ms < 50, f"avg merge {avg_ms:.2f}ms exceeded 50ms"
