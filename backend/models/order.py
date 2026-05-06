"""
BAR-55 — 주문 / 라우팅 결정 모델.

Reference:
- Plan: docs/01-plan/features/bar-55-sor-v1.plan.md
- Design: docs/02-design/features/bar-55-sor-v1.design.md §1
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.models.market import Exchange


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class RoutingReason(str, Enum):
    PRICE_FIRST = "price_first"
    QTY_FIRST = "qty_first"
    FORCED = "forced"
    SESSION_BLOCKED = "session_blocked"
    NO_LIQUIDITY = "no_liquidity"
    LIMIT_UNFILLABLE = "limit_unfillable"


class OrderRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    side: OrderSide
    qty: int = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[Decimal] = None
    force_venue: Optional[Exchange] = None
    requested_at: Optional[datetime] = None


class RoutingDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    request: OrderRequest
    venue: Optional[Exchange]
    expected_price: Optional[Decimal]
    expected_qty: int
    reason: RoutingReason

    @property
    def is_routed(self) -> bool:
        return self.venue is not None


__all__ = [
    "OrderSide",
    "OrderType",
    "RoutingReason",
    "OrderRequest",
    "RoutingDecision",
]
