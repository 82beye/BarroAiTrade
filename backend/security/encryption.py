"""BAR-69 — 컬럼 암호화 (Fernet AES128-CBC + HMAC-SHA256).

키움 자격증명 / OAuth 토큰 / Anthropic API 키 등 민감 컬럼 암호화.
"""
from __future__ import annotations

from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr


class ColumnEncryptor:
    """Fernet 기반 컬럼 단위 암호화."""

    def __init__(self, key: SecretStr) -> None:
        if not isinstance(key, SecretStr):
            raise TypeError("key must be SecretStr (CWE-798)")
        self._fernet = Fernet(key.get_secret_value())

    @staticmethod
    def generate_key() -> SecretStr:
        """32바이트 랜덤 → base64 url-safe 키."""
        return SecretStr(Fernet.generate_key().decode("ascii"))

    def encrypt(self, plaintext: str) -> str:
        """plaintext (str) → ciphertext (base64 str)."""
        if plaintext is None:
            raise ValueError("plaintext is None")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise ValueError(f"invalid ciphertext: {e}")


class RLSPolicy:
    """Row-Level Security — Postgres 정책 SQL 생성기 (BAR-69b 적용).

    worktree 단계는 SQL 문자열 빌더만 검증.
    """

    @staticmethod
    def enable_rls(table: str) -> str:
        return f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;"

    @staticmethod
    def policy_user_owns_row(
        table: str, user_id_column: str = "user_id"
    ) -> str:
        """current_setting('app.user_id') 와 비교 — 본인 row 만 접근."""
        return (
            f"CREATE POLICY {table}_user_owns ON {table} "
            f"USING ({user_id_column} = current_setting('app.user_id'));"
        )

    @staticmethod
    def admin_bypass(table: str) -> str:
        return (
            f"CREATE POLICY {table}_admin_bypass ON {table} "
            f"USING (current_setting('app.role') = 'admin');"
        )


__all__ = ["ColumnEncryptor", "RLSPolicy"]
