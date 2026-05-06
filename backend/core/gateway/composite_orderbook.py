"""
BAR-54 — CompositeOrderBookService.

KRX + NXT 의 OrderBookL2 를 단일 가격축에 병합.

Reference:
- Plan: docs/01-plan/features/bar-54-composite-orderbook.plan.md
- Design: docs/02-design/features/bar-54-composite-orderbook.design.md
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Iterable, Optional

from backend.core.market_session.service import KST
from backend.models.market import (
    CompositeLevel,
    CompositeOrderBookL2,
    Exchange,
    OrderBookL2,
)


class CompositeOrderBookService:
    """KRX + NXT 호가 병합 서비스 (stateless)."""

    def merge(
        self,
        krx: Optional[OrderBookL2],
        nxt: Optional[OrderBookL2],
        symbol: str,
        ts: Optional[datetime] = None,
    ) -> CompositeOrderBookL2:
        """KRX + NXT OrderBookL2 → CompositeOrderBookL2.

        한쪽이 None 이면 다른 한쪽만으로 변환.
        둘 다 None 이면 빈 CompositeOrderBookL2 반환.
        """
        ts = ts or datetime.now(KST)
        venues: set[Exchange] = set()

        bid_acc: dict[Decimal, dict[Exchange, int]] = {}
        ask_acc: dict[Decimal, dict[Exchange, int]] = {}

        if krx is not None:
            venues.add(Exchange.KRX)
            self._accumulate(krx.bids, Exchange.KRX, bid_acc)
            self._accumulate(krx.asks, Exchange.KRX, ask_acc)

        if nxt is not None:
            venues.add(Exchange.NXT)
            self._accumulate(nxt.bids, Exchange.NXT, bid_acc)
            self._accumulate(nxt.asks, Exchange.NXT, ask_acc)

        bids = self._to_levels(bid_acc, descending=True)
        asks = self._to_levels(ask_acc, descending=False)

        return CompositeOrderBookL2(
            symbol=symbol,
            ts=ts,
            bids=bids,
            asks=asks,
            venues=frozenset(venues),
        )

    @staticmethod
    def _accumulate(
        levels: Iterable[tuple[Decimal, int]],
        venue: Exchange,
        acc: dict[Decimal, dict[Exchange, int]],
    ) -> None:
        for price, qty in levels:
            entry = acc.setdefault(price, {})
            entry[venue] = entry.get(venue, 0) + qty

    @staticmethod
    def _to_levels(
        acc: dict[Decimal, dict[Exchange, int]],
        descending: bool,
    ) -> list[CompositeLevel]:
        levels = [
            CompositeLevel(
                price=price,
                total_qty=sum(breakdown.values()),
                breakdown=dict(breakdown),
            )
            for price, breakdown in acc.items()
        ]
        levels.sort(key=lambda lv: lv.price, reverse=descending)
        return levels


__all__ = ["CompositeOrderBookService"]
