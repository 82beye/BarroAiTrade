"""BAR-OPS-07 — PenTestSuite 자동 침투 시나리오 (10 cases)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from backend.core.news.sources import RSSSource
from backend.models.news import NewsSource
from backend.security.auth import JWTService, Role
from backend.security.pen_test import (
    AttackResult,
    AttackVector,
    PenTestSuite,
)


@pytest.fixture
def jwt_service():
    return JWTService(SecretStr("super-secret-key-32-chars-long-x"))


# ─── SQL Injection ────────────────────────────────────────


class TestSQLInjection:
    def test_named_param_escapes_malicious(self):
        """SQLAlchemy named param 으로 ' OR 1=1-- 차단."""
        # repo mock
        repo = MagicMock()
        repo.find_by_user_id = AsyncMock(return_value=None)
        result = PenTestSuite.try_sql_injection_in_user_id(
            repo, "alice' OR '1'='1"
        )
        assert result.is_secure is True
        assert result.vector == AttackVector.SQL_INJECTION


# ─── JWT 변조 ─────────────────────────────────────────────


class TestJWTTampering:
    def test_tampered_token_rejected(self, jwt_service):
        result = PenTestSuite.try_jwt_tampering(
            jwt_service, "alice", "admin"
        )
        assert result.is_secure is True

    def test_none_alg_rejected(self, jwt_service):
        result = PenTestSuite.try_jwt_none_alg(jwt_service)
        assert result.is_secure is True


# ─── RBAC ─────────────────────────────────────────────────


class TestRBAC:
    def test_viewer_cannot_admin(self):
        result = PenTestSuite.try_rbac_bypass(Role.VIEWER, Role.ADMIN)
        assert result.is_secure is True

    def test_trader_cannot_admin(self):
        result = PenTestSuite.try_rbac_bypass(Role.TRADER, Role.ADMIN)
        assert result.is_secure is True

    def test_admin_passes(self):
        # 정상 권한 — succeeded=True 가 정상 (보안 실패 X)
        result = PenTestSuite.try_rbac_bypass(Role.ADMIN, Role.TRADER)
        assert result.succeeded is True
        assert result.blocked is False


# ─── SSRF ─────────────────────────────────────────────────


class TestSSRF:
    def test_file_scheme_rejected(self):
        http = MagicMock()
        result = PenTestSuite.try_ssrf(
            lambda url: RSSSource(NewsSource.RSS_HANKYUNG, url, http),
            "file:///etc/passwd",
        )
        assert result.is_secure is True

    def test_internal_ip_rejected(self):
        http = MagicMock()
        result = PenTestSuite.try_ssrf(
            lambda url: RSSSource(NewsSource.RSS_HANKYUNG, url, http),
            "https://169.254.169.254/latest/meta-data/",
        )
        assert result.is_secure is True

    def test_allowed_host_passes(self):
        http = MagicMock()
        result = PenTestSuite.try_ssrf(
            lambda url: RSSSource(NewsSource.RSS_HANKYUNG, url, http),
            "https://rss.hankyung.com/feed",
        )
        # 허용된 host — succeeded=True (정상 흐름)
        assert result.succeeded is True


# ─── PII / Log ────────────────────────────────────────────


class TestPIILeak:
    def test_secret_in_log_caught(self):
        log_text = "kiwoom_app_secret=plain-secret-value sent"
        result = PenTestSuite.try_pii_leak_in_log(
            log_text, "plain-secret-value"
        )
        assert result.succeeded is True   # 누설 발견 = 침투 성공 = 보안 실패

    def test_masked_log_safe(self):
        log_text = "kiwoom_app_secret=*** sent"
        result = PenTestSuite.try_pii_leak_in_log(
            log_text, "plain-secret-value"
        )
        assert result.is_secure is True


# ─── 검증 — 모든 시나리오 통과 (보안 회귀) ──────────────


class TestSecurityRegression:
    """전체 침투 시나리오 — 보안 정책 변경 시 회귀 게이트."""

    def test_all_critical_vectors_blocked(self, jwt_service):
        results = [
            PenTestSuite.try_jwt_tampering(jwt_service, "u", "admin"),
            PenTestSuite.try_jwt_none_alg(jwt_service),
            PenTestSuite.try_rbac_bypass(Role.VIEWER, Role.ADMIN),
        ]
        # 모든 critical attack 차단됨
        assert all(r.is_secure for r in results), [r.details for r in results]
