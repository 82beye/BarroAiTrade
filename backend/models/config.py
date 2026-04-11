"""
설정 모델
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from backend.models.risk import RiskLimits


class KiwoomConfig(BaseModel):
    base_url: str = "https://openapi.koreainvestment.com:9443"
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    mock: bool = True  # 모의투자 여부


class TelegramConfig(BaseModel):
    token: str = ""
    chat_id: str = ""
    enabled: bool = False


class TradingConfig(BaseModel):
    mode: Literal["simulation", "live"] = "simulation"
    market: Literal["stock", "crypto"] = "stock"
    risk_limits: RiskLimits = RiskLimits()
    kiwoom: KiwoomConfig = KiwoomConfig()
    telegram: TelegramConfig = TelegramConfig()
    scan_interval_sec: int = 3
    market_check_interval_sec: int = 1800  # 30분
