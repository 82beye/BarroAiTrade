"""BAR-68 — MFA (TOTP) + 실거래 강제 게이트."""
from __future__ import annotations

import secrets
from typing import Optional

import pyotp
from pydantic import SecretStr


class MFAService:
    """RFC 6238 TOTP — 30초 윈도우, 6자리."""

    @staticmethod
    def generate_secret() -> SecretStr:
        """base32 32자 secret 생성."""
        return SecretStr(pyotp.random_base32())

    @staticmethod
    def verify(secret: SecretStr, code: str, valid_window: int = 1) -> bool:
        if not isinstance(secret, SecretStr):
            raise TypeError("secret must be SecretStr")
        if not code or not code.isdigit() or len(code) != 6:
            return False
        totp = pyotp.TOTP(secret.get_secret_value())
        return totp.verify(code, valid_window=valid_window)

    @staticmethod
    def now_code(secret: SecretStr) -> str:
        """현재 시각 코드 (테스트용)."""
        return pyotp.TOTP(secret.get_secret_value()).now()

    @staticmethod
    def provisioning_uri(
        secret: SecretStr, account: str, issuer: str = "BarroAiTrade"
    ) -> str:
        """QR 등록용 URI."""
        return pyotp.TOTP(secret.get_secret_value()).provisioning_uri(
            name=account, issuer_name=issuer
        )


class LiveTradingGate:
    """실거래 모드 진입 시 MFA 강제."""

    def __init__(self, mfa: MFAService) -> None:
        self._mfa = mfa

    def authorize(
        self, user_secret: Optional[SecretStr], otp_code: Optional[str]
    ) -> tuple[bool, str]:
        """OTP 미입력 또는 잘못된 코드 → 차단."""
        if user_secret is None:
            return False, "MFA secret not registered"
        if not otp_code:
            return False, "OTP required for live trading"
        if not self._mfa.verify(user_secret, otp_code):
            return False, "OTP verification failed"
        return True, "authorized"


__all__ = ["MFAService", "LiveTradingGate"]
