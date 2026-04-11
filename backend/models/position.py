"""
포지션 및 주문 모델
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel

from backend.models.market import MarketType


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Order(BaseModel):
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None  # market order면 None
    market_type: MarketType
    strategy_id: str
    risk_approved: bool = False


class OrderResult(BaseModel):
    order_id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    filled_quantity: float
    avg_price: float
    market_type: MarketType
    timestamp: datetime


class Position(BaseModel):
    symbol: str
    name: str
    quantity: float
    avg_price: float
    current_price: float
    realized_pnl: float
    unrealized_pnl: float
    pnl_pct: float
    market_type: MarketType
    entry_time: datetime
    strategy_id: str


class Balance(BaseModel):
    total_value: float           # 총 평가금액
    available_cash: float        # 주문 가능 현금
    invested_value: float        # 투자 중 금액
    total_pnl: float             # 총 손익
    total_pnl_pct: float         # 총 수익률
    market_type: MarketType
    updated_at: datetime
