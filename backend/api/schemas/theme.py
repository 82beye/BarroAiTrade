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
