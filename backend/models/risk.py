"""
리스크 관리 모델
"""
from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, field_validator


class RiskLimits(BaseModel):
    max_position_pct: float = 0.10         # 종목당 최대 10%
    max_concurrent_positions: int = 5       # 동시 최대 5종목
    max_total_exposure_pct: float = 0.50   # 총 투자 50%
    stop_loss_pct: float = -0.02           # -2% 손절
    take_profit_1_pct: float = 0.03        # +3% 1차 익절
    take_profit_1_qty_pct: float = 0.50    # 1차 익절 시 50% 매도
    take_profit_2_pct: float = 0.05        # +5% 전량
    daily_loss_limit_pct: float = -0.03    # -3% 일일 손실 한도 (모의투자 보수적 기본값)
    force_close_time: str = "14:50"        # 강제청산 시간 (주식)

    @field_validator("force_close_time")
    @classmethod
    def validate_force_close_time(cls, v: str) -> str:
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError(f"force_close_time 형식 오류: '{v}' — 'HH:MM' 형식 필요")
        hour, minute = int(v[:2]), int(v[3:])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"force_close_time 범위 오류: '{v}' — 00:00~23:59 범위 필요")
        return v


class RiskStatus(BaseModel):
    current_exposure_pct: float
    daily_pnl_pct: float
    position_count: int
    daily_limit_breached: bool
    new_entry_blocked: bool
    limits: RiskLimits
    timestamp: datetime
