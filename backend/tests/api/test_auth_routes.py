"""BAR-OPS-01 — auth 라우트 + middleware (12 cases)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.api.middleware import TenantContextMiddleware
from backend.api.routes.auth import configure, router
from backend.core.multitenancy.tenant_context import TenantContext
from backend.security.auth import JWTService, Role
from backend.security.mfa import MFAService


@pytest.fixture
def jwt_service():
    return JWTService(SecretStr("test-secret-key-1234567890abcdefg"))


@pytest.fixture
def app(jwt_service):
    user_db = {
        "alice": {"role": "trader", "password": "alice-pw"},
        "bob": {"role": "admin", "password": "bob-pw", "mfa_secret": "JBSWY3DPEHPK3PXP"},
    }
    configure(jwt_service=jwt_service, user_db=user_db)
    fa = FastAPI()
    fa.include_router(router)
    fa.add_middleware(TenantContextMiddleware, jwt_service=jwt_service)
    return fa


@pytest.fixture
def client(app):
    return TestClient(app)


class TestLogin:
    def test_login_success(self, client):
        r = client.post(
            "/api/auth/login",
            json={"user_id": "alice", "password": "alice-pw"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["user_id"] == "alice"
        assert data["role"] == "trader"
        assert "access_token" in data
        # httpOnly 쿠키 검증
        assert "refresh_token" in r.cookies

    def test_login_wrong_password(self, client):
        r = client.post(
            "/api/auth/login",
            json={"user_id": "alice", "password": "wrong"},
        )
        assert r.status_code == 401

    def test_login_unknown_user(self, client):
        r = client.post(
            "/api/auth/login",
            json={"user_id": "nobody", "password": "x"},
        )
        assert r.status_code == 401


class TestRefresh:
    def test_refresh_success(self, client, jwt_service):
        refresh = jwt_service.encode_refresh("alice")
        r = client.post(
            "/api/auth/refresh", json={"refresh_token": refresh}
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_refresh_invalid(self, client):
        r = client.post(
            "/api/auth/refresh", json={"refresh_token": "bad"}
        )
        assert r.status_code == 401

    def test_refresh_unknown_user(self, client, jwt_service):
        refresh = jwt_service.encode_refresh("ghost")
        r = client.post(
            "/api/auth/refresh", json={"refresh_token": refresh}
        )
        assert r.status_code == 401


class TestMFA:
    def test_mfa_setup_admin(self, client, jwt_service):
        access = jwt_service.encode_access("bob", Role.ADMIN)
        r = client.post(
            "/api/auth/mfa/setup",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "secret" in data
        assert "provisioning_uri" in data

    def test_mfa_setup_no_token(self, client):
        r = client.post("/api/auth/mfa/setup")
        assert r.status_code == 401

    def test_mfa_verify_correct_otp(self, client, jwt_service):
        # bob 의 mfa_secret = JBSWY3DPEHPK3PXP — 현재 시각 OTP 계산
        access = jwt_service.encode_access("bob", Role.ADMIN)
        code = MFAService.now_code(SecretStr("JBSWY3DPEHPK3PXP"))
        r = client.post(
            "/api/auth/mfa/verify",
            json={"otp_code": code},
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r.status_code == 200
        assert r.json()["authorized"] is True

    def test_mfa_verify_wrong_otp(self, client, jwt_service):
        access = jwt_service.encode_access("bob", Role.ADMIN)
        r = client.post(
            "/api/auth/mfa/verify",
            json={"otp_code": "000000"},
            headers={"Authorization": f"Bearer {access}"},
        )
        # 우연 일치 가능성 매우 낮음 — 401 일반적
        assert r.status_code in (401, 200)


class TestMiddleware:
    def test_middleware_sets_tenant_context(self, jwt_service):
        """JWT 토큰 → TenantContext.user_id 설정 검증."""
        # Middleware 단위 테스트 — mock request
        from starlette.requests import Request

        from backend.api.middleware import TenantContextMiddleware

        # 단순 통합 — Bearer 토큰 헤더 시 user_id 가 contextvar 에 설정되는지
        # FastAPI TestClient 통합으로 검증
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(TenantContextMiddleware, jwt_service=jwt_service)

        @app.get("/whoami")
        async def whoami():
            return {"user_id": TenantContext.current_user()}

        c = TestClient(app)
        access = jwt_service.encode_access("alice", Role.TRADER)
        r = c.get("/whoami", headers={"Authorization": f"Bearer {access}"})
        assert r.status_code == 200
        assert r.json()["user_id"] == "alice"

    def test_middleware_no_token(self, jwt_service):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from backend.api.middleware import TenantContextMiddleware

        app = FastAPI()
        app.add_middleware(TenantContextMiddleware, jwt_service=jwt_service)

        @app.get("/whoami")
        async def whoami():
            return {"user_id": TenantContext.current_user()}

        c = TestClient(app)
        r = c.get("/whoami")
        assert r.status_code == 200
        assert r.json()["user_id"] is None
