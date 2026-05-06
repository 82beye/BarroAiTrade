"""
Settings — pydantic-settings 기반 애플리케이션 설정

.env 파일과 환경변수를 병합하여 타입 안전한 설정 객체를 제공.
민감 정보(API 키, 토큰)는 환경변수로만 관리.

BAR-42 확장 (Phase 0): NXT/Postgres/Redis/뉴스/테마/JWT 그룹 placeholder 추가.
모든 신규 필드는 Optional 또는 default 값을 가지므로 환경변수 미주입 시
`Settings()` 가 무에러로 인스턴스화된다 (동작 변화 없음).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

try:
    from pydantic import Field, SecretStr
    from pydantic_settings import BaseSettings, SettingsConfigDict

    _PYDANTIC_SETTINGS = True
except ImportError:  # pragma: no cover — fallback when pydantic-settings 미설치
    from pydantic import BaseModel as BaseSettings  # type: ignore
    from pydantic import Field  # type: ignore

    SecretStr = str  # type: ignore
    _PYDANTIC_SETTINGS = False


class Settings(BaseSettings):
    # === Trading 기본 (기존) ===
    trading_mode: Literal["simulation", "live"] = "simulation"
    trading_market: Literal["stock", "crypto"] = "stock"
    scan_interval_sec: int = 3

    # === Kiwoom API (기존, BAR-67 에서 SecretStr 일괄 변환 예정) ===
    kiwoom_base_url: str = "https://openapi.koreainvestment.com:9443"
    kiwoom_app_key: str = ""
    kiwoom_app_secret: str = ""  # TODO(BAR-67): SecretStr
    kiwoom_account_no: str = ""
    kiwoom_mock: bool = True

    # === DB (기존 + Postgres 신규, BAR-56) ===
    db_path: str = "data/barro_trade.db"
    postgres_url: Optional[SecretStr] = None
    postgres_user: str = "barro"
    postgres_password: SecretStr = SecretStr("barro")
    postgres_db: str = "barro"
    postgres_pool_size: int = 5
    pgvector_enabled: bool = False

    # === NXT (신규, BAR-53) ===
    nxt_enabled: bool = False
    nxt_base_url: Optional[str] = None
    nxt_app_key: Optional[str] = None
    nxt_app_secret: Optional[SecretStr] = None

    # === Redis (신규, BAR-57) — SecretStr 승격 (CWE-522) ===
    redis_url: Optional[SecretStr] = None
    redis_streams_enabled: bool = False

    # === 뉴스/공시 (신규, BAR-57) ===
    dart_api_key: Optional[SecretStr] = None
    rss_feed_urls: list[str] = Field(default_factory=list)
    news_polling_interval_sec: int = 60
    news_dedup_backend: Literal["memory", "redis"] = "memory"
    news_stream_backend: Literal["memory", "redis"] = "memory"
    news_dedup_ttl_hours: int = 24                 # InMemory
    news_dedup_ttl_hours_redis: int = 72           # Redis
    news_inmemory_queue_max: int = 10_000
    news_fetch_timeout_seconds: int = 30

    # === 임베딩 (BAR-58) — security 권고 ===
    news_embedding_backend: Literal["fake", "ko_sbert", "openai"] = "fake"
    news_embedding_model: str = "jhgan/ko-sroberta-multitask"
    news_embedding_dim: int = 768
    news_embedding_batch_size: int = Field(default=16, ge=1, le=64)
    news_embedding_revision: Optional[str] = None  # 운영 시 SHA pin (CWE-494)
    openai_api_key: Optional[SecretStr] = None      # CWE-798
    anthropic_api_key: Optional[SecretStr] = None   # BAR-58b (CWE-798)

    # === 테마 (신규, BAR-58/59) ===
    theme_embedding_model: str = "ko-sbert"
    theme_vector_db_url: Optional[str] = None
    theme_classifier_threshold: float = 0.65

    # === 보안 (신규, BAR-67/68) ===
    jwt_secret: Optional[SecretStr] = None
    jwt_access_ttl_sec: int = 3600
    jwt_refresh_ttl_sec: int = 604800
    mfa_issuer: str = "BarroAiTrade"

    # === Telegram (기존, BAR-67 에서 SecretStr 일괄 변환 예정) ===
    telegram_bot_token: str = ""  # TODO(BAR-67): SecretStr
    telegram_chat_id: str = ""

    # === 로깅 (기존) ===
    log_json: bool = False
    log_level: str = "INFO"

    # === 서버 (기존) ===
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
