"""BAR-60 — 대장주 점수 모델 (Pydantic v2 frozen + Decimal)."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class StockMetrics(BaseModel):
    """일일 거래량 + 시총 fixture (운영 KIS API 시 갱신)."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    daily_volume: int = Field(ge=0)
    market_cap: Decimal = Field(ge=0)


class LeaderScore(BaseModel):
    """대장주 점수 — 가중합 결과."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    theme_id: int = Field(gt=0)
    score: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)


__all__ = ["StockMetrics", "LeaderScore"]
