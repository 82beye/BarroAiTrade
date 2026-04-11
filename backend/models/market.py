"""
시장 데이터 모델 — 멀티마켓 공통 모델
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel


class MarketType(str, Enum):
    STOCK = "stock"
    CRYPTO = "crypto"


class OHLCV(BaseModel):
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    market_type: MarketType


class Ticker(BaseModel):
    symbol: str
    name: str
    price: float
    volume: float
    change_pct: float
    timestamp: datetime
    market_type: MarketType


class OrderBook(BaseModel):
    symbol: str
    asks: List[tuple[float, float]]  # (price, qty)
    bids: List[tuple[float, float]]
    timestamp: datetime
    market_type: MarketType


class MarketCondition(BaseModel):
    status: Literal["NORMAL", "CAUTION", "WARNING", "EXTREME"]
    index_value: float       # KOSPI 수치 또는 BTC 도미넌스
    volatility: float        # ATR 비율
    description: str
    market_type: MarketType
    updated_at: datetime
