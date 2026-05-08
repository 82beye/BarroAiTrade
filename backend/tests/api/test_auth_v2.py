"""BAR-OPS-04 — auth_v2 (UserRepository + bcrypt 통합) (10 cases)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from backend.api.routes.auth_v2 import configure, router
from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.user_repo import UserRepository
from backend.security.auth import JWTService, Role
from backend.security.password import PasswordHasher


@pytest.fixture
async def app(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "auth_v2.db"
    monkeypatch.setenv("DB_PATH", str(db_file))
    reset_engine_for_test()
    await init_db(str(db_file))
    async with get_db() as db:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL UNIQUE,
                    email TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'viewer',
                    password_hash TEXT NOT NULL,
                    mfa_secret TEXT,
                    created_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
        )
    jwt = JWTService(SecretStr("test-secret-key-1234567890abcdefg"))
    hasher = PasswordHasher(rounds=4)
    repo = UserRepository()
    configure(jwt=jwt, hasher=hasher, repo=repo)

    fa = FastAPI()
    fa.include_router(router)
    yield fa, jwt
    reset_engine_for_test()


@pytest.fixture
def client(app):
    fa, _ = app
    return TestClient(fa)


@pytest.fixture
def jwt_service(app):
    _, jwt = app
    return jwt


class TestRegister:
    def test_register_success(self, client):
        r = client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "alice",
                "email": "alice@x.com",
                "password": "alice-pw-123",
                "role": "trader",
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["created"] is True
        assert data["role"] == "trader"

    def test_register_duplicate(self, client):
        client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "bob",
                "email": "bob@x.com",
                "password": "bob-pw-123",
            },
        )
        r = client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "bob",
                "email": "bob2@x.com",
                "password": "different",
            },
        )
        assert r.status_code == 409

    def test_register_short_password(self, client):
        r = client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "carol",
                "email": "c@x.com",
                "password": "short",   # 8자 미만
            },
        )
        assert r.status_code == 422

    def test_register_invalid_user_id(self, client):
        r = client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "bad user!",
                "email": "x@x.com",
                "password": "valid-pw-123",
            },
        )
        assert r.status_code == 422


class TestLogin:
    def test_login_success_after_register(self, client):
        client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "dan",
                "email": "dan@x.com",
                "password": "dan-pw-123",
            },
        )
        r = client.post(
            "/api/auth/v2/login",
            json={"user_id": "dan", "password": "dan-pw-123"},
        )
        assert r.status_code == 200
        assert "access_token" in r.json()
        assert "refresh_token" in r.cookies

    def test_login_wrong_password(self, client):
        client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "eve",
                "email": "e@x.com",
                "password": "eve-pw-123",
            },
        )
        r = client.post(
            "/api/auth/v2/login",
            json={"user_id": "eve", "password": "wrong"},
        )
        assert r.status_code == 401

    def test_login_unknown_user(self, client):
        r = client.post(
            "/api/auth/v2/login",
            json={"user_id": "ghost", "password": "x"},
        )
        assert r.status_code == 401


class TestRefresh:
    def test_refresh_after_register(self, client, jwt_service):
        client.post(
            "/api/auth/v2/register",
            json={
                "user_id": "frank",
                "email": "f@x.com",
                "password": "frank-pw-123",
            },
        )
        refresh = jwt_service.encode_refresh("frank")
        r = client.post(
            "/api/auth/v2/refresh", json={"refresh_token": refresh}
        )
        assert r.status_code == 200
        assert "access_token" in r.json()

    def test_refresh_unknown_user(self, client, jwt_service):
        refresh = jwt_service.encode_refresh("nobody")
        r = client.post(
            "/api/auth/v2/refresh", json={"refresh_token": refresh}
        )
        assert r.status_code == 401
