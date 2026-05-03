"""
Settings — pydantic-settings 기반 애플리케이션 설정

.env 파일과 환경변수를 병합하여 타입 안전한 설정 객체를 제공.
민감 정보(API 키, 토큰)는 환경변수로만 관리.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal, Optional

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    _PYDANTIC_SETTINGS = True
except ImportError:
    from pydantic import BaseModel as BaseSettings
    _PYDANTIC_SETTINGS = False


class Settings(BaseSettings):
    # 매매 기본 설정
    trading_mode: Literal["simulation", "live"] = "simulation"
    trading_market: Literal["stock", "crypto"] = "stock"
    scan_interval_sec: int = 3

    # 키움 API (모의투자 기본)
    kiwoom_base_url: str = "https://openapi.koreainvestment.com:9443"
    kiwoom_app_key: str = ""
    kiwoom_app_secret: str = ""
    kiwoom_account_no: str = ""
    kiwoom_mock: bool = True

    # DB
    db_path: str = "data/barro_trade.db"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # 로깅
    log_json: bool = False
    log_level: str = "INFO"

    # 서버
    host: str = "0.0.0.0"
    port: int = 8000

    if _PYDANTIC_SETTINGS:
        model_config = SettingsConfigDict(
            env_file=".env",
            env_file_encoding="utf-8",
            case_sensitive=False,
            extra="ignore",
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """캐시된 설정 싱글톤 반환"""
    return Settings()


# 편의용 전역 인스턴스
settings = get_settings()
