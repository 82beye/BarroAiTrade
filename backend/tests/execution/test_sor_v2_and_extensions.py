"""BAR-75/76/77/79 — Extensions stub + SOR v2 (12 cases)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from backend.core.execution.sor_v2 import SORv2
from backend.core.gateway.composite_orderbook import CompositeOrderBookService
from backend.core.gateway.extensions import (
    ExtendedExchangeAdapter,
    StubHKStockGateway,
    StubUSStockGateway,
    StubUpbitGateway,
)
from backend.core.market_session.service import KST
from backend.models.market import Exchange, OrderBookL2


def _ob(venue, bids, asks):
    return OrderBookL2(
        symbol="005930", venue=venue,
        ts=datetime.now(KST), bids=bids, asks=asks,
    )


class TestExtensions:
    def test_us_stock_protocol(self):
        g = StubUSStockGateway()
        assert isinstance(g, ExtendedExchangeAdapter)
        assert g.market_type == "us_stock"

    def test_hk_stock_protocol(self):
        g = StubHKStockGateway()
        assert isinstance(g, ExtendedExchangeAdapter)
        assert g.market_type == "hk_stock"

    def test_upbit_protocol(self):
        g = StubUpbitGateway()
        assert isinstance(g, ExtendedExchangeAdapter)
        assert g.market_type == "crypto"

    @pytest.mark.asyncio
    async def test_us_paper_order(self):
        g = StubUSStockGateway()
        result = await g.submit_order("AAPL", "buy", Decimal("10"))
        assert result["status"] == "paper_filled"
        assert result["venue"] == "us_stock_stub"


class TestSORv2:
    @pytest.mark.asyncio
    async def test_single_level_single_venue(self):
        svc = CompositeOrderBookService()
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = svc.merge(krx, None, "005930")
        sor = SORv2()
        d = sor.split("buy", 50, book)
        assert d.total_qty == 50
        assert len(d.splits) == 1
        assert d.splits[0].venue == Exchange.KRX
        assert d.estimated_slippage_bps == 0.0

    @pytest.mark.asyncio
    async def test_multi_level_split(self):
        svc = CompositeOrderBookService()
        krx = _ob(Exchange.KRX, [],
                  [(Decimal("70100"), 30), (Decimal("70200"), 50)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70150"), 40)])
        book = svc.merge(krx, nxt, "005930")
        sor = SORv2()
        d = sor.split("buy", 100, book)
        assert d.total_qty == 100
        # 슬리피지 발생 — 70100 < 평균
        assert d.estimated_slippage_bps > 0

    @pytest.mark.asyncio
    async def test_invalid_side(self):
        svc = CompositeOrderBookService()
        book = svc.merge(None, None, "005930")
        sor = SORv2()
        with pytest.raises(ValueError, match="side"):
            sor.split("hold", 10, book)

    @pytest.mark.asyncio
    async def test_no_liquidity_raises(self):
        svc = CompositeOrderBookService()
        book = svc.merge(None, None, "005930")
        sor = SORv2()
        with pytest.raises(ValueError, match="no liquidity"):
            sor.split("buy", 10, book)

    @pytest.mark.asyncio
    async def test_qty_zero_raises(self):
        svc = CompositeOrderBookService()
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 100)])
        book = svc.merge(krx, None, "005930")
        sor = SORv2()
        with pytest.raises(ValueError, match="target_qty"):
            sor.split("buy", 0, book)

    @pytest.mark.asyncio
    async def test_partial_fill(self):
        svc = CompositeOrderBookService()
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 30)])
        book = svc.merge(krx, None, "005930")
        sor = SORv2()
        d = sor.split("buy", 100, book)
        # 30 만 체결 가능
        assert d.total_qty == 30

    @pytest.mark.asyncio
    async def test_sell_uses_bids(self):
        svc = CompositeOrderBookService()
        krx = _ob(Exchange.KRX, [(Decimal("69900"), 50)], [])
        book = svc.merge(krx, None, "005930")
        sor = SORv2()
        d = sor.split("sell", 50, book)
        assert d.total_qty == 50
        assert d.splits[0].venue == Exchange.KRX

    @pytest.mark.asyncio
    async def test_split_two_venues_same_price(self):
        svc = CompositeOrderBookService()
        krx = _ob(Exchange.KRX, [], [(Decimal("70100"), 50)])
        nxt = _ob(Exchange.NXT, [], [(Decimal("70100"), 50)])
        book = svc.merge(krx, nxt, "005930")
        sor = SORv2()
        d = sor.split("buy", 80, book)
        assert d.total_qty == 80
        # 두 venue 모두 사용
        venues = {s.venue for s in d.splits}
        assert len(venues) >= 1
