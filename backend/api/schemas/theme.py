"""BAR-62 — Theme/Calendar/News API schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ThemeOut(BaseModel):
    id: int
    name: str
    description: str = ""


class ThemeStockOut(BaseModel):
    symbol: str
    score: float
    theme_id: int
    theme_name: Optional[str] = None
    # tima P0: 시세 확장 (모두 Optional, 기본 None — 하위호환).
    # gateway 가용 시 ticker 조회로 채움, 실패/미초기화 시 None 유지.
    name: Optional[str] = None
    price: Optional[float] = None
    change_pct: Optional[float] = None
    day_open: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    value_traded: Optional[float] = None  # 억원


class EventOut(BaseModel):
    id: int
    event_type: str
    symbol: Optional[str] = None
    event_date: str
    title: str
    source: str = "manual"


class NewsOut(BaseModel):
    id: int
    source: str
    source_id: str
    title: str
    url: str
    published_at: str
    tags: list[str] = Field(default_factory=list)


__all__ = ["ThemeOut", "ThemeStockOut", "EventOut", "NewsOut"]
