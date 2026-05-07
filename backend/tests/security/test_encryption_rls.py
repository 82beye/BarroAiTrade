"""BAR-69 — ColumnEncryptor + RLSPolicy (10 cases)."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from backend.security.encryption import ColumnEncryptor, RLSPolicy


class TestColumnEncryptor:
    def test_key_must_be_secretstr(self):
        with pytest.raises(TypeError):
            ColumnEncryptor("plain")  # type: ignore[arg-type]

    def test_round_trip(self):
        key = ColumnEncryptor.generate_key()
        enc = ColumnEncryptor(key)
        ct = enc.encrypt("kiwoom_app_secret_xyz")
        assert ct != "kiwoom_app_secret_xyz"
        assert enc.decrypt(ct) == "kiwoom_app_secret_xyz"

    def test_korean_text(self):
        key = ColumnEncryptor.generate_key()
        enc = ColumnEncryptor(key)
        plain = "한국어 비밀번호 abc123"
        assert enc.decrypt(enc.encrypt(plain)) == plain

    def test_invalid_ciphertext_raises(self):
        key = ColumnEncryptor.generate_key()
        enc = ColumnEncryptor(key)
        with pytest.raises(ValueError, match="invalid"):
            enc.decrypt("not-base64-token")

    def test_different_keys_fail(self):
        k1 = ColumnEncryptor.generate_key()
        k2 = ColumnEncryptor.generate_key()
        e1 = ColumnEncryptor(k1)
        e2 = ColumnEncryptor(k2)
        ct = e1.encrypt("secret")
        with pytest.raises(ValueError):
            e2.decrypt(ct)

    def test_none_rejected(self):
        key = ColumnEncryptor.generate_key()
        enc = ColumnEncryptor(key)
        with pytest.raises(ValueError):
            enc.encrypt(None)  # type: ignore[arg-type]


class TestRLSPolicy:
    def test_enable_rls_sql(self):
        sql = RLSPolicy.enable_rls("audit_log")
        assert "ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY" in sql

    def test_user_owns_row_default_column(self):
        sql = RLSPolicy.policy_user_owns_row("trades")
        assert "current_setting('app.user_id')" in sql
        assert "user_id =" in sql

    def test_user_owns_row_custom_column(self):
        sql = RLSPolicy.policy_user_owns_row("audit_log", "owner_id")
        assert "owner_id = current_setting('app.user_id')" in sql

    def test_admin_bypass(self):
        sql = RLSPolicy.admin_bypass("trades")
        assert "current_setting('app.role') = 'admin'" in sql
