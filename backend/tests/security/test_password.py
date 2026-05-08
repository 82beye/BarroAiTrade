"""BAR-OPS-02 — PasswordHasher (8 cases)."""
from __future__ import annotations

import pytest

from backend.security.password import PasswordHasher


@pytest.fixture
def hasher() -> PasswordHasher:
    # 테스트 — 빠른 4 round
    return PasswordHasher(rounds=4)


class TestPasswordHasher:
    def test_invalid_rounds(self):
        with pytest.raises(ValueError):
            PasswordHasher(rounds=2)
        with pytest.raises(ValueError):
            PasswordHasher(rounds=20)

    def test_hash_then_verify(self, hasher):
        h = hasher.hash("alice-pw")
        assert hasher.verify("alice-pw", h) is True

    def test_verify_wrong(self, hasher):
        h = hasher.hash("alice-pw")
        assert hasher.verify("wrong", h) is False

    def test_empty_password_rejected(self, hasher):
        with pytest.raises(ValueError):
            hasher.hash("")

    def test_verify_empty_inputs(self, hasher):
        assert hasher.verify("", "x") is False
        assert hasher.verify("x", "") is False

    def test_korean_password(self, hasher):
        h = hasher.hash("한국어비밀번호")
        assert hasher.verify("한국어비밀번호", h) is True
        assert hasher.verify("틀린암호", h) is False

    def test_different_hashes_for_same_input(self, hasher):
        # bcrypt salt — 같은 password 도 hash 다름
        h1 = hasher.hash("same")
        h2 = hasher.hash("same")
        assert h1 != h2
        assert hasher.verify("same", h1)
        assert hasher.verify("same", h2)

    def test_invalid_hash_returns_false(self, hasher):
        assert hasher.verify("any", "not-a-bcrypt-hash") is False
