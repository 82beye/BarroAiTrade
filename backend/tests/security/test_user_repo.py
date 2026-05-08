"""BAR-OPS-02 — UserRepository (8 cases)."""
from __future__ import annotations

import pytest
from sqlalchemy import text

from backend.db.database import get_db, init_db, reset_engine_for_test
from backend.db.repositories.user_repo import UserRepository
from backend.models.user import User
from backend.security.auth import Role
from backend.security.password import PasswordHasher


@pytest.fixture
async def isolated_db(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    db_file = tmp_path / "users.db"
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
    yield db_file
    reset_engine_for_test()


@pytest.fixture
def hasher():
    return PasswordHasher(rounds=4)


def _user(uid="alice", email="alice@x.com", pw_hash="$2b$04$xxx", role=Role.TRADER, mfa=None) -> User:
    return User(
        user_id=uid, email=email, password_hash=pw_hash, role=role, mfa_secret=mfa
    )


class TestUserModel:
    def test_user_id_pattern(self):
        with pytest.raises(Exception):
            User(
                user_id="bad user!",  # 공백/!
                email="x@x.com",
                password_hash="h",
            )

    def test_email_min_length(self):
        with pytest.raises(Exception):
            User(user_id="a", email="x", password_hash="h")

    def test_role_default(self):
        u = User(user_id="a", email="a@a.com", password_hash="h")
        assert u.role == Role.VIEWER


class TestUserRepo:
    @pytest.mark.asyncio
    async def test_insert_and_find(self, isolated_db, hasher):
        repo = UserRepository()
        h = hasher.hash("pw")
        new_id = await repo.insert(_user(pw_hash=h))
        assert new_id is not None and new_id > 0
        found = await repo.find_by_user_id("alice")
        assert found is not None
        assert found.email == "alice@x.com"
        assert found.role == Role.TRADER
        assert hasher.verify("pw", found.password_hash) is True

    @pytest.mark.asyncio
    async def test_insert_duplicate_user_id(self, isolated_db, hasher):
        repo = UserRepository()
        h = hasher.hash("pw")
        await repo.insert(_user(uid="dup", email="d1@x.com", pw_hash=h))
        again = await repo.insert(_user(uid="dup", email="d2@x.com", pw_hash=h))
        assert again is None

    @pytest.mark.asyncio
    async def test_find_unknown_returns_none(self, isolated_db):
        repo = UserRepository()
        found = await repo.find_by_user_id("ghost")
        assert found is None

    @pytest.mark.asyncio
    async def test_update_password(self, isolated_db, hasher):
        repo = UserRepository()
        await repo.insert(_user(pw_hash=hasher.hash("old")))
        new_hash = hasher.hash("new")
        ok = await repo.update_password("alice", new_hash)
        assert ok is True
        found = await repo.find_by_user_id("alice")
        assert hasher.verify("new", found.password_hash) is True

    @pytest.mark.asyncio
    async def test_update_mfa_secret(self, isolated_db, hasher):
        repo = UserRepository()
        await repo.insert(_user(pw_hash=hasher.hash("pw")))
        ok = await repo.update_mfa_secret("alice", "JBSWY3DPEHPK3PXP")
        assert ok is True
        found = await repo.find_by_user_id("alice")
        assert found.mfa_secret == "JBSWY3DPEHPK3PXP"
