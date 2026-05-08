"""BAR-OPS-02 — User 모델 (Pydantic v2 frozen)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.security.auth import Role


class User(BaseModel):
    """User 도메인 모델 — frozen."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=254)
    role: Role = Role.VIEWER
    password_hash: str = Field(min_length=1)
    mfa_secret: Optional[str] = None    # base32 — 운영 시 Fernet 암호화 (BAR-69)
    created_at: Optional[datetime] = None


__all__ = ["User"]
