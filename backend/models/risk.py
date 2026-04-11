"""
리스크 관리 모델
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RiskLimits(BaseModel):
    max_position_pct: float = 0.10         # 종목당 최대 10%
    max_concurrent_positions: int = 5       # 동시 최대 5종목
    max_total_exposure_pct: float = 0.50   # 총 투자 50%
    stop_loss_pct: float = -0.02           # -2% 손절
    take_profit_1_pct: float = 0.03        # +3% 1차 익절
    take_profit_1_qty_pct: float = 0.50    # 1차 익절 시 50% 매도
    take_profit_2_pct: float = 0.05        # +5% 전량
    daily_loss_limit_pct: float = -0.05    # -5% 일일 손실 한도
    force_close_time: str = "14:50"        # 강제청산 시간 (주식)


class RiskStatus(BaseModel):
    current_exposure_pct: float
    daily_pnl_pct: float
    position_count: int
    daily_limit_breached: bool
    new_entry_blocked: bool
    limits: RiskLimits
    timestamp: datetime
