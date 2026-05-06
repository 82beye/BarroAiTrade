"""BAR-67 — JWT + RBAC 골격.

토큰 발행/검증 + Role 기반 라우트 가드.
실 /login 엔드포인트 통합은 BAR-67b.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import jwt
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Role(str, Enum):
    VIEWER = "viewer"
    TRADER = "trader"
    ADMIN = "admin"


# Role 권한 계층 — 상위 role 은 하위 role 의 모든 권한 포함
_ROLE_HIERARCHY: dict[Role, int] = {
    Role.VIEWER: 1,
    Role.TRADER: 2,
    Role.ADMIN: 3,
}


class AccessTokenPayload(BaseModel):
    """JWT payload 검증용 모델 (frozen)."""

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    role: Role
    exp: int                    # epoch seconds
    iat: int


class JWTService:
    """HS256 JWT — access (1h) / refresh (7d)."""

    ALGORITHM = "HS256"
    ACCESS_TTL = timedelta(hours=1)
    REFRESH_TTL = timedelta(days=7)

    def __init__(self, secret: SecretStr) -> None:
        if not isinstance(secret, SecretStr):
            raise TypeError("secret must be SecretStr (CWE-798)")
        if len(secret.get_secret_value()) < 16:
            raise ValueError("secret too short (≥ 16 chars)")
        self._secret = secret

    def encode_access(self, user_id: str, role: Role) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "user_id": user_id,
            "role": role.value,
            "iat": int(now.timestamp()),
            "exp": int((now + self.ACCESS_TTL).timestamp()),
            "type": "access",
        }
        return jwt.encode(
            payload, self._secret.get_secret_value(), algorithm=self.ALGORITHM
        )

    def encode_refresh(self, user_id: str) -> str:
        now = datetime.now(timezone.utc)
        payload = {
            "user_id": user_id,
            "iat": int(now.timestamp()),
            "exp": int((now + self.REFRESH_TTL).timestamp()),
            "type": "refresh",
        }
        return jwt.encode(
            payload, self._secret.get_secret_value(), algorithm=self.ALGORITHM
        )

    def decode(self, token: str, expected_type: str = "access") -> AccessTokenPayload:
        """signature + exp 검증 + type 일치."""
        try:
            data = jwt.decode(
                token,
                self._secret.get_secret_value(),
                algorithms=[self.ALGORITHM],
            )
        except jwt.ExpiredSignatureError:
            raise ValueError("token expired")
        except jwt.InvalidTokenError as e:
            raise ValueError(f"invalid token: {e}")

        if data.get("type") != expected_type:
            raise ValueError(
                f"token type mismatch: expected {expected_type}, got {data.get('type')}"
            )
        if expected_type == "access":
            return AccessTokenPayload(
                user_id=data["user_id"],
                role=Role(data["role"]),
                exp=data["exp"],
                iat=data["iat"],
            )
        # refresh 는 별도 처리 — payload 클래스 미반환
        return data  # type: ignore[return-value]


class RBACPolicy:
    """role 계층 — has_permission(user_role, required_role)."""

    @staticmethod
    def has_permission(user_role: Role, required_role: Role) -> bool:
        return _ROLE_HIERARCHY[user_role] >= _ROLE_HIERARCHY[required_role]

    @staticmethod
    def require_role(user_role: Role, required_role: Role) -> None:
        """미달 시 PermissionError raise."""
        if not RBACPolicy.has_permission(user_role, required_role):
            raise PermissionError(
                f"user role {user_role.value} insufficient for {required_role.value}"
            )


__all__ = ["Role", "AccessTokenPayload", "JWTService", "RBACPolicy"]
