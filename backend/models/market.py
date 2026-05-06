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


class Exchange(str, Enum):
    """거래소 — KRX 본장, NXT 대체거래소, COMPOSITE 통합 뷰 (BAR-52)."""

    KRX = "krx"
    NXT = "nxt"
    COMPOSITE = "composite"


class TradingSession(str, Enum):
    """거래 세션 (한국 시간 기준, BAR-52).

    08:00 ─ NXT_PRE ─ 08:30 ─ KRX_PRE ─ 09:00 ─ REGULAR ─
    15:20 ─ KRX_CLOSING_AUCTION ─ 15:30 ─ INTERLUDE ─ 15:40 ─
    KRX_AFTER ─ 18:00 ─ NXT_AFTER ─ 20:00 ─ CLOSED
    """

    CLOSED = "closed"
    NXT_PRE = "nxt_pre"
    KRX_PRE = "krx_pre"
    REGULAR = "regular"
    KRX_CLOSING_AUCTION = "krx_closing_auction"
    INTERLUDE = "interlude"
    KRX_AFTER = "krx_after"
    NXT_AFTER = "nxt_after"


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
