"""BAR-63 — ExitOrder + PositionState (Pydantic v2 frozen + Decimal)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ExitReason(str, Enum):
    TP1 = "tp1"
    TP2 = "tp2"
    TP3 = "tp3"
    STOP_LOSS = "stop_loss"
    TIME_EXIT = "time_exit"


class ExitOrder(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    qty: Decimal = Field(gt=0)
    target_price: Decimal = Field(gt=0)
    reason: ExitReason


class PositionState(BaseModel):
    """포지션 + ExitPlan 누적 상태. frozen — ExitEngine 가 새 인스턴스 반환."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    entry_price: Decimal = Field(gt=0)
    qty: Decimal = Field(ge=0)            # 잔여 수량 (전량 청산 시 0)
    entry_time: datetime
    initial_qty: Decimal = Field(gt=0)    # 진입 시점 qty (TP 비율 계산 기준)
    tp_filled: int = Field(default=0, ge=0, le=3)
    sl_at: Optional[Decimal] = None       # breakeven 후 갱신


__all__ = ["ExitReason", "ExitOrder", "PositionState"]
