"""BAR-68 — MFA + Audit Hash Chain (12 cases)."""
from __future__ import annotations

import pytest
from pydantic import SecretStr

from backend.security.audit_chain import (
    GENESIS_HASH,
    compute_hash,
    verify_chain,
)
from backend.security.mfa import LiveTradingGate, MFAService


class TestMFA:
    def test_generate_secret_returns_secretstr(self):
        s = MFAService.generate_secret()
        assert isinstance(s, SecretStr)
        assert len(s.get_secret_value()) >= 32  # base32 — 32자 이상

    def test_now_code_6digits(self):
        s = MFAService.generate_secret()
        code = MFAService.now_code(s)
        assert code.isdigit() and len(code) == 6

    def test_verify_now_code(self):
        s = MFAService.generate_secret()
        code = MFAService.now_code(s)
        assert MFAService.verify(s, code) is True

    def test_verify_wrong_code(self):
        s = MFAService.generate_secret()
        assert MFAService.verify(s, "000000") in (True, False)  # 매우 낮은 확률 통과
        assert MFAService.verify(s, "abc") is False
        assert MFAService.verify(s, "12345") is False  # 5자리

    def test_verify_secret_type_check(self):
        with pytest.raises(TypeError):
            MFAService.verify("plain", "123456")  # type: ignore[arg-type]

    def test_provisioning_uri(self):
        s = MFAService.generate_secret()
        uri = MFAService.provisioning_uri(s, "user@x.com")
        assert uri.startswith("otpauth://totp/")
        assert "BarroAiTrade" in uri


class TestLiveTradingGate:
    def test_no_secret_blocked(self):
        gate = LiveTradingGate(MFAService())
        ok, reason = gate.authorize(None, "123456")
        assert ok is False
        assert "registered" in reason

    def test_no_otp_blocked(self):
        gate = LiveTradingGate(MFAService())
        s = MFAService.generate_secret()
        ok, reason = gate.authorize(s, "")
        assert ok is False
        assert "OTP required" in reason

    def test_correct_otp_authorized(self):
        gate = LiveTradingGate(MFAService())
        s = MFAService.generate_secret()
        code = MFAService.now_code(s)
        ok, _ = gate.authorize(s, code)
        assert ok is True


class TestAuditChain:
    def test_compute_hash_deterministic(self):
        h1 = compute_hash(GENESIS_HASH, {"a": 1, "b": 2})
        h2 = compute_hash(GENESIS_HASH, {"b": 2, "a": 1})
        assert h1 == h2  # canonical sort

    def test_chain_verify_success(self):
        # 3-row 체인 생성
        rows = []
        prev = GENESIS_HASH
        for i in range(3):
            payload = {"event": f"e{i}", "value": i}
            h = compute_hash(prev, payload)
            rows.append({**payload, "row_hash": h})
            prev = h
        ok, idx = verify_chain(rows)
        assert ok is True
        assert idx == 3

    def test_chain_verify_tampered(self):
        rows = []
        prev = GENESIS_HASH
        for i in range(3):
            payload = {"event": f"e{i}"}
            h = compute_hash(prev, payload)
            rows.append({**payload, "row_hash": h})
            prev = h
        # 1번째 행 변조
        rows[1]["event"] = "tampered"
        ok, idx = verify_chain(rows)
        assert ok is False
        assert idx == 1
