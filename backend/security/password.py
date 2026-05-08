"""BAR-OPS-02 — bcrypt 비밀번호 해시 (bcrypt 직접 사용).

passlib 의 bcrypt 4.x 호환성 이슈 회피 — bcrypt 라이브러리 직접 호출.
운영 — 12 round (cost). 단위 테스트는 4 round (빠름).
bcrypt 72-byte 입력 제한 — pre-hash with sha256 (length-extension 안전).
"""
from __future__ import annotations

import hashlib

import bcrypt


class PasswordHasher:
    """bcrypt — round 가변. 운영 12, 테스트 4."""

    def __init__(self, rounds: int = 12) -> None:
        if rounds < 4 or rounds > 16:
            raise ValueError("rounds must be 4..16")
        self._rounds = rounds

    @staticmethod
    def _prehash(password: str) -> bytes:
        """bcrypt 72-byte 한계 회피 — sha256 prehash."""
        # base64 encode 시 44 chars (44 bytes < 72) — 안전
        digest = hashlib.sha256(password.encode("utf-8")).digest()
        # base64 표준 — 패딩 포함 44 byte
        import base64

        return base64.b64encode(digest)

    def hash(self, password: str) -> str:
        if not password:
            raise ValueError("password required")
        salt = bcrypt.gensalt(rounds=self._rounds)
        hashed = bcrypt.hashpw(self._prehash(password), salt)
        return hashed.decode("utf-8")

    def verify(self, password: str, hashed: str) -> bool:
        if not password or not hashed:
            return False
        try:
            return bcrypt.checkpw(
                self._prehash(password), hashed.encode("utf-8")
            )
        except Exception:
            return False

    def needs_update(self, hashed: str) -> bool:
        # bcrypt cost 추출: $2b$<rounds>$...
        try:
            parts = hashed.split("$")
            current_rounds = int(parts[2]) if len(parts) > 2 else 0
            return current_rounds < self._rounds
        except Exception:
            return True


__all__ = ["PasswordHasher"]
