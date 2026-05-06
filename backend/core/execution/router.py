"""
BAR-55 — SmartOrderRouter (SOR v1).

Reference:
- Design: docs/02-design/features/bar-55-sor-v1.design.md §2
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from backend.core.market_session.service import MarketSessionService
from backend.models.market import (
    CompositeOrderBookL2,
    Exchange,
)
from backend.models.order import (
    OrderRequest,
    OrderSide,
    OrderType,
    RoutingDecision,
    RoutingReason,
)


class SmartOrderRouter:
    """가격 우선 → 동가격 잔량 우선 → 세션 가드 → force_venue 우회."""

    def __init__(self, session_service: MarketSessionService) -> None:
        self._session = session_service

    def route(
        self,
        req: OrderRequest,
        book: CompositeOrderBookL2,
        now: Optional[datetime] = None,
    ) -> RoutingDecision:
        session = self._session.get_session(now)
        available = set(self._session.available_exchanges(session))

        # Step 1. force_venue
        if req.force_venue is not None:
            if req.force_venue not in available:
                return self._blocked(req, RoutingReason.SESSION_BLOCKED)
            expected_price = self._best_for_venue(req, book)
            return RoutingDecision(
                request=req,
                venue=req.force_venue,
                expected_price=expected_price,
                expected_qty=req.qty,
                reason=RoutingReason.FORCED,
            )

        # Step 2. 가격 측 결정
        if req.side == OrderSide.BUY:
            target_price = book.best_ask
        else:
            target_price = book.best_bid

        if target_price is None:
            return self._blocked(req, RoutingReason.NO_LIQUIDITY)

        # Step 3. limit 호환성
        if req.order_type == OrderType.LIMIT:
            assert req.limit_price is not None
            if req.side == OrderSide.BUY and req.limit_price < target_price:
                return self._blocked(req, RoutingReason.LIMIT_UNFILLABLE)
            if req.side == OrderSide.SELL and req.limit_price > target_price:
                return self._blocked(req, RoutingReason.LIMIT_UNFILLABLE)

        # Step 4. venue_breakdown 조회
        breakdown = book.venue_breakdown(target_price)
        available_breakdown = {
            v: q for v, q in breakdown.items() if v in available
        }

        if not available_breakdown:
            return self._blocked(req, RoutingReason.SESSION_BLOCKED)

        # Step 5/6. 단일 vs 다중 venue
        if len(available_breakdown) == 1:
            venue, qty = next(iter(available_breakdown.items()))
            return RoutingDecision(
                request=req,
                venue=venue,
                expected_price=target_price,
                expected_qty=min(req.qty, qty),
                reason=RoutingReason.PRICE_FIRST,
            )

        # 동가격 잔량 우선 (deterministic — qty desc, KRX 우선)
        venue = max(
            available_breakdown.items(),
            key=lambda kv: (kv[1], 0 if kv[0] == Exchange.KRX else -1),
        )[0]
        qty = available_breakdown[venue]
        return RoutingDecision(
            request=req,
            venue=venue,
            expected_price=target_price,
            expected_qty=min(req.qty, qty),
            reason=RoutingReason.QTY_FIRST,
        )

    @staticmethod
    def _blocked(req: OrderRequest, reason: RoutingReason) -> RoutingDecision:
        return RoutingDecision(
            request=req,
            venue=None,
            expected_price=None,
            expected_qty=0,
            reason=reason,
        )

    @staticmethod
    def _best_for_venue(
        req: OrderRequest,
        book: CompositeOrderBookL2,
    ) -> Optional[Decimal]:
        """force_venue 의 best 가격을 book 에서 조회."""
        levels = book.asks if req.side == OrderSide.BUY else book.bids
        for lv in levels:
            if req.force_venue in lv.breakdown:
                return lv.price
        return None


__all__ = ["SmartOrderRouter"]
