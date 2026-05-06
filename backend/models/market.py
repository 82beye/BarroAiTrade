"""
시장 데이터 모델 — 멀티마켓 공통 모델
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


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


# ═════════════════════════════════════════════════════════
# BAR-53 NXT Gateway 시세 데이터 모델 — Decimal 강제, frozen
# ═════════════════════════════════════════════════════════


class Tick(BaseModel):
    """체결가 / 현재가 1건 (BAR-53)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    venue: Exchange
    ts: datetime
    last_price: Decimal
    last_volume: int


class Quote(BaseModel):
    """최우선 호가 1쌍 (BAR-53)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    venue: Exchange
    ts: datetime
    bid: Decimal
    ask: Decimal
    bid_qty: int
    ask_qty: int


class OrderBookL2(BaseModel):
    """L2 호가 — 단계별 (BAR-53).

    bids: 매수 호가 (가격 내림차순)
    asks: 매도 호가 (가격 오름차순)
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    venue: Exchange
    ts: datetime
    bids: List[tuple[Decimal, int]]
    asks: List[tuple[Decimal, int]]


class Trade(BaseModel):
    """체결 1건 (BAR-53)."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    venue: Exchange
    ts: datetime
    price: Decimal
    qty: int
    side: Literal["buy", "sell"]


class HealthStatus(BaseModel):
    """게이트웨이 헬스 상태 (BAR-53)."""

    is_healthy: bool
    last_msg_at: Optional[datetime] = None
    lag_seconds: Optional[float] = None
    error: Optional[str] = None


class GatewayStatus(str, Enum):
    """게이트웨이 매니저 종합 상태 (BAR-53)."""

    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"


# ═════════════════════════════════════════════════════════
# BAR-54 Composite OrderBook — KRX + NXT 호가 병합
# ═════════════════════════════════════════════════════════


class CompositeLevel(BaseModel):
    """단일 가격의 통합 호가 레벨 (BAR-54).

    가격 1개 + 거래소별 잔량 분해 + 합산 잔량.
    """

    model_config = ConfigDict(frozen=True)

    price: Decimal
    total_qty: int
    breakdown: dict[Exchange, int]


class CompositeOrderBookL2(BaseModel):
    """KRX + NXT 호가 단일 가격축 병합 (BAR-54).

    bids: 가격 내림차순
    asks: 가격 오름차순
    venues: 머지에 참여한 거래소 frozenset
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    ts: datetime
    bids: List[CompositeLevel]
    asks: List[CompositeLevel]
    venues: frozenset[Exchange]

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[Decimal]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / Decimal(2)

    @property
    def spread(self) -> Optional[Decimal]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def venue_breakdown(self, price: Decimal) -> dict[Exchange, int]:
        """주어진 가격의 거래소별 잔량 분해 반환. 미발견 시 빈 dict."""
        for level in self.bids:
            if level.price == price:
                return dict(level.breakdown)
        for level in self.asks:
            if level.price == price:
                return dict(level.breakdown)
        return {}
