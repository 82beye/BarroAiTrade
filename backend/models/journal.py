"""BAR-65 — TradeNote 모델 (Pydantic v2 frozen + Decimal)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class Emotion(str, Enum):
    PROUD = "proud"
    REGRET = "regret"
    NEUTRAL = "neutral"


class TradeNote(BaseModel):
    """매매 일지 1건. frozen — 외부 변조 차단."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trade_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: str = Field(pattern="^(buy|sell)$")
    qty: Decimal = Field(gt=0)
    entry_price: Decimal = Field(gt=0)
    exit_price: Optional[Decimal] = None
    pnl: Optional[Decimal] = None
    entry_time: datetime
    exit_time: Optional[datetime] = None
    emotion: Emotion = Emotion.NEUTRAL
    note: str = Field(default="", max_length=2000)
    tags: tuple[str, ...] = ()


__all__ = ["Emotion", "TradeNote"]
