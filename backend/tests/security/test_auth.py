"""BAR-67 — JWT + RBAC (12 cases)."""
from __future__ import annotations

import time

import pytest
from pydantic import SecretStr

from backend.security.auth import (
    AccessTokenPayload,
    JWTService,
    RBACPolicy,
    Role,
)


@pytest.fixture
def jwt_service() -> JWTService:
    return JWTService(SecretStr("test-secret-key-1234567890"))


class TestJWTServiceInit:
    def test_secret_must_be_secretstr(self):
        with pytest.raises(TypeError, match="SecretStr"):
            JWTService("plain")  # type: ignore[arg-type]

    def test_secret_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            JWTService(SecretStr("short"))


class TestEncodeDecode:
    def test_access_token_round_trip(self, jwt_service):
        token = jwt_service.encode_access("user1", Role.TRADER)
        assert isinstance(token, str)
        payload = jwt_service.decode(token)
        assert isinstance(payload, AccessTokenPayload)
        assert payload.user_id == "user1"
        assert payload.role == Role.TRADER

    def test_refresh_token_decode(self, jwt_service):
        token = jwt_service.encode_refresh("user1")
        data = jwt_service.decode(token, expected_type="refresh")
        assert data["user_id"] == "user1"

    def test_invalid_token_raises(self, jwt_service):
        with pytest.raises(ValueError, match="invalid"):
            jwt_service.decode("not-a-token")

    def test_wrong_type_raises(self, jwt_service):
        access = jwt_service.encode_access("u", Role.VIEWER)
        with pytest.raises(ValueError, match="type mismatch"):
            jwt_service.decode(access, expected_type="refresh")

    def test_tampered_token_raises(self, jwt_service):
        token = jwt_service.encode_access("u", Role.VIEWER)
        # 마지막 글자 변경
        tampered = token[:-1] + ("X" if token[-1] != "X" else "Y")
        with pytest.raises(ValueError):
            jwt_service.decode(tampered)

    def test_different_secret_fails(self):
        s1 = JWTService(SecretStr("secret-1-1234567890"))
        s2 = JWTService(SecretStr("secret-2-1234567890"))
        token = s1.encode_access("u", Role.VIEWER)
        with pytest.raises(ValueError):
            s2.decode(token)


class TestRBAC:
    def test_admin_has_all_permissions(self):
        assert RBACPolicy.has_permission(Role.ADMIN, Role.VIEWER) is True
        assert RBACPolicy.has_permission(Role.ADMIN, Role.TRADER) is True
        assert RBACPolicy.has_permission(Role.ADMIN, Role.ADMIN) is True

    def test_viewer_cannot_trade(self):
        assert RBACPolicy.has_permission(Role.VIEWER, Role.TRADER) is False

    def test_trader_can_view(self):
        assert RBACPolicy.has_permission(Role.TRADER, Role.VIEWER) is True

    def test_require_role_raises(self):
        with pytest.raises(PermissionError, match="insufficient"):
            RBACPolicy.require_role(Role.VIEWER, Role.ADMIN)

    def test_require_role_passes(self):
        # 같은 레벨도 허용
        RBACPolicy.require_role(Role.TRADER, Role.TRADER)
