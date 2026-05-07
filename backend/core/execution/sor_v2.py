"""BAR-79 — SOR v2 (split 라우팅 + 슬리피지 모델).

BAR-55 SOR v1 의 단일 venue 결정 → 다중 venue 분산 라우팅.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.models.market import CompositeOrderBookL2, Exchange


class VenueSplit(BaseModel):
    """단일 venue 분배."""

    model_config = ConfigDict(frozen=True)

    venue: Exchange
    qty: int = Field(gt=0)
    expected_price: Decimal


class SplitRoutingDecision(BaseModel):
    """다중 venue 분배 결과."""

    model_config = ConfigDict(frozen=True)

    splits: list[VenueSplit]
    total_qty: int = Field(gt=0)
    estimated_slippage_bps: float = 0.0   # basis points (1bp = 0.01%)


class SORv2:
    """가격 우선 + 잔량 비례 분배 + 슬리피지 추정."""

    def split(
        self,
        side: str,                           # "buy" or "sell"
        target_qty: int,
        book: CompositeOrderBookL2,
    ) -> SplitRoutingDecision:
        if side not in ("buy", "sell"):
            raise ValueError("side must be buy/sell")
        if target_qty <= 0:
            raise ValueError("target_qty must be > 0")

        levels = book.asks if side == "buy" else book.bids
        if not levels:
            raise ValueError("no liquidity")

        venue_qty: dict[Exchange, int] = {}
        venue_price_sum: dict[Exchange, Decimal] = {}
        remaining = target_qty
        first_price: Optional[Decimal] = None
        weighted_sum = Decimal(0)
        consumed = 0

        for level in levels:
            if remaining <= 0:
                break
            if first_price is None:
                first_price = level.price
            for venue, lvl_qty in level.breakdown.items():
                if remaining <= 0:
                    break
                take = min(remaining, lvl_qty)
                venue_qty[venue] = venue_qty.get(venue, 0) + take
                venue_price_sum[venue] = (
                    venue_price_sum.get(venue, Decimal(0))
                    + level.price * Decimal(take)
                )
                weighted_sum += level.price * Decimal(take)
                remaining -= take
                consumed += take

        if consumed == 0:
            raise ValueError("no quantity could be routed")

        splits: list[VenueSplit] = []
        for v, q in venue_qty.items():
            avg_price = venue_price_sum[v] / Decimal(q)
            splits.append(VenueSplit(venue=v, qty=q, expected_price=avg_price))

        # 슬리피지 = (평균 체결가 - 최우선 가격) / 최우선 가격 * 10000
        avg_total = weighted_sum / Decimal(consumed)
        slippage_bps = float(
            abs(avg_total - first_price) / first_price * Decimal(10000)
        )
        return SplitRoutingDecision(
            splits=splits,
            total_qty=consumed,
            estimated_slippage_bps=slippage_bps,
        )


__all__ = ["VenueSplit", "SplitRoutingDecision", "SORv2"]
