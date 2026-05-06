"""BAR-61 — Market event models (Pydantic v2 frozen)."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventType(str, Enum):
    EARNINGS = "earnings"
    IPO = "ipo"
    DIVIDEND = "dividend"
    POLICY = "policy"
    OTHER = "other"


class MarketEvent(BaseModel):
    """시장 이벤트 1건."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: EventType
    symbol: Optional[str] = None
    event_date: date
    title: str = Field(min_length=1, max_length=512)
    source: str = "manual"
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["EventType", "MarketEvent"]
