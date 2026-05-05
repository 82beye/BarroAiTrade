"""
BAR-42 통합 환경변수 스키마 테스트 (Plan §4.2 / Design §4).

C1~C6 핵심 케이스 + TestEnvExampleConsistency 보강.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.config.settings import Settings


# 본 테스트가 격리된 환경변수에서 실행되도록 BAR-42 신규 키 일괄 정리 fixture.
_BAR42_NEW_KEYS = [
    "NXT_ENABLED",
    "NXT_BASE_URL",
    "NXT_APP_KEY",
    "NXT_APP_SECRET",
    "POSTGRES_URL",
    "POSTGRES_POOL_SIZE",
    "PGVECTOR_ENABLED",
    "REDIS_URL",
    "REDIS_STREAMS_ENABLED",
    "DART_API_KEY",
    "RSS_FEED_URLS",
    "NEWS_POLLING_INTERVAL_SEC",
    "THEME_EMBEDDING_MODEL",
    "THEME_VECTOR_DB_URL",
    "THEME_CLASSIFIER_THRESHOLD",
    "JWT_SECRET",
    "JWT_ACCESS_TTL_SEC",
    "JWT_REFRESH_TTL_SEC",
    "MFA_ISSUER",
]


@pytest.fixture
def clean_env(monkeypatch):
    for key in _BAR42_NEW_KEYS + ["KIWOOM_APP_KEY"]:
        monkeypatch.delenv(key, raising=False)
    yield monkeypatch


class TestSettings:
    """BAR-42 핵심 6 케이스."""

    def test_c1_no_env_vars_succeeds(self, clean_env):
        """C1: 환경변수 미주입 → Settings() 무에러 + 신규 default 확인."""
        s = Settings(_env_file=None)
        # NXT
        assert s.nxt_enabled is False
        assert s.nxt_base_url is None
        assert s.nxt_app_secret is None
        # Postgres
        assert s.postgres_url is None
        assert s.postgres_pool_size == 5
        assert s.pgvector_enabled is False
        # Redis
        assert s.redis_url is None
        assert s.redis_streams_enabled is False
        # 뉴스
        assert s.dart_api_key is None
        assert s.rss_feed_urls == []
        assert s.news_polling_interval_sec == 60
        # 테마
        assert s.theme_embedding_model == "ko-sbert"
        assert s.theme_vector_db_url is None
        assert s.theme_classifier_threshold == 0.65
        # JWT/MFA
        assert s.jwt_secret is None
        assert s.jwt_access_ttl_sec == 3600
        assert s.jwt_refresh_ttl_sec == 604800
        assert s.mfa_issuer == "BarroAiTrade"

    def test_c2_kiwoom_app_key_injection(self, clean_env):
        """C2: 기존 동작 회귀 — KIWOOM_APP_KEY 주입."""
        clean_env.setenv("KIWOOM_APP_KEY", "test-key-abc")
        s = Settings(_env_file=None)
        assert s.kiwoom_app_key == "test-key-abc"

    def test_c3_nxt_enabled_bool_parsing(self, clean_env):
        """C3: NXT_ENABLED=true → bool True 파싱."""
        clean_env.setenv("NXT_ENABLED", "true")
        s = Settings(_env_file=None)
        assert s.nxt_enabled is True

    def test_c4_rss_feed_urls_list_parsing(self, clean_env):
        """C4: RSS_FEED_URLS JSON 주입 → list 정확 파싱."""
        clean_env.setenv(
            "RSS_FEED_URLS",
            '["https://news.naver.com/rss","https://kr.investing.com/rss"]',
        )
        s = Settings(_env_file=None)
        assert s.rss_feed_urls == [
            "https://news.naver.com/rss",
            "https://kr.investing.com/rss",
        ]

    def test_c5_jwt_secret_repr_masked(self, clean_env):
        """C5: JWT_SECRET → repr() 마스킹 + get_secret_value() 정확."""
        clean_env.setenv("JWT_SECRET", "super-secret-value-xyz")
        s = Settings(_env_file=None)
        assert "super-secret-value-xyz" not in repr(s)
        assert s.jwt_secret is not None
        assert s.jwt_secret.get_secret_value() == "super-secret-value-xyz"

    def test_c6_env_file_none(self, clean_env):
        """C6: _env_file=None → .env 파일 없이도 동작."""
        s = Settings(_env_file=None)
        assert isinstance(s, Settings)


class TestSecretStrAsymmetry:
    """SecretStr 옵션 C — 신규 5 secret 만 적용 검증."""

    def test_new_secrets_are_secret_str(self, clean_env):
        """신규 secret 4건은 SecretStr 타입 (None 일 수 있음)."""
        s = Settings(_env_file=None)
        from pydantic import SecretStr

        # None 또는 SecretStr 인스턴스
        assert s.nxt_app_secret is None or isinstance(s.nxt_app_secret, SecretStr)
        assert s.postgres_url is None or isinstance(s.postgres_url, SecretStr)
        assert s.dart_api_key is None or isinstance(s.dart_api_key, SecretStr)
        assert s.jwt_secret is None or isinstance(s.jwt_secret, SecretStr)

    def test_legacy_secrets_remain_str(self, clean_env):
        """기존 secret 2건은 BAR-67 위임 — str 타입 유지."""
        s = Settings(_env_file=None)
        assert isinstance(s.kiwoom_app_secret, str)
        assert isinstance(s.telegram_bot_token, str)


class TestEnvExampleConsistency:
    """`.env.example` 의 키와 Settings 필드 1:1 검증 (보강)."""

    def test_env_example_keys_match_settings_fields(self):
        """`.env.example` 의 모든 KEY 가 Settings 필드명과 일치."""
        repo_root = Path(__file__).parent.parent.parent.parent
        env_example = repo_root / ".env.example"
        if not env_example.exists():
            pytest.skip(".env.example not found")

        keys: set[str] = set()
        for line in env_example.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" in stripped:
                key = stripped.split("=", 1)[0].strip().lower()
                keys.add(key)

        settings_fields = set(Settings.model_fields.keys())
        unknown = keys - settings_fields
        assert not unknown, f".env.example 의 알 수 없는 키: {unknown}"
