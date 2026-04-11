"""
매매 신호 모델
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from backend.models.market import MarketType


class EntrySignal(BaseModel):
    symbol: str
    name: str
    price: float
    signal_type: Literal["blue_line", "watermelon", "crypto_breakout", "f_zone", "sf_zone"]
    score: float
    reason: str
    market_type: MarketType
    strategy_id: str
    timestamp: datetime
    risk_approved: bool = False
    metadata: dict = {}


class ExitSignal(BaseModel):
    symbol: str
    name: str
    exit_type: Literal["take_profit_1", "take_profit_2", "stop_loss", "forced", "manual"]
    price: float
    pnl_pct: float
    reason: str
    market_type: MarketType
    timestamp: datetime
