"""BAR-OPS-02 — UserRepository (text() + dialect 분기 패턴)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from backend.db.database import get_db
from backend.models.user import User
from backend.security.auth import Role

logger = logging.getLogger(__name__)


class UserRepository:
    """users 테이블 CRUD."""

    async def insert(self, user: User) -> Optional[int]:
        try:
            async with get_db() as db:
                if db is None:
                    return None
                is_sqlite = db.engine.dialect.name == "sqlite"
                if is_sqlite:
                    sql = text(
                        """
                        INSERT OR IGNORE INTO users
                            (user_id, email, role, password_hash, mfa_secret, created_at)
                        VALUES (:uid, :email, :role, :pw, :mfa, :now)
                        """
                    )
                else:
                    sql = text(
                        """
                        INSERT INTO users
                            (user_id, email, role, password_hash, mfa_secret)
                        VALUES (:uid, :email, :role, :pw, :mfa)
                        ON CONFLICT (user_id) DO NOTHING
                        RETURNING id
                        """
                    )
                params = {
                    "uid": user.user_id,
                    "email": user.email,
                    "role": user.role.value,
                    "pw": user.password_hash,
                    "mfa": user.mfa_secret,
                }
                if is_sqlite:
                    params["now"] = datetime.now(timezone.utc).isoformat()
                res = await db.execute(sql, params)
                if (res.rowcount or 0) != 1:
                    return None
                if is_sqlite:
                    res2 = await db.execute(
                        text("SELECT last_insert_rowid() AS id")
                    )
                    row = res2.mappings().first()
                    return int(row["id"]) if row else None
                row = res.mappings().first()
                return int(row["id"]) if row else None
        except Exception as exc:
            logger.error("user insert 실패: %s", exc)
            return None

    async def find_by_user_id(self, user_id: str) -> Optional[User]:
        try:
            async with get_db() as db:
                if db is None:
                    return None
                res = await db.execute(
                    text("SELECT * FROM users WHERE user_id = :uid"),
                    {"uid": user_id},
                )
                row = res.mappings().first()
                if not row:
                    return None
                return User(
                    user_id=row["user_id"],
                    email=row["email"],
                    role=Role(row["role"]),
                    password_hash=row["password_hash"],
                    mfa_secret=row.get("mfa_secret"),
                )
        except Exception as exc:
            logger.error("find_by_user_id 실패: %s", exc)
            return None

    async def update_password(self, user_id: str, new_hash: str) -> bool:
        try:
            async with get_db() as db:
                if db is None:
                    return False
                res = await db.execute(
                    text(
                        "UPDATE users SET password_hash = :pw WHERE user_id = :uid"
                    ),
                    {"pw": new_hash, "uid": user_id},
                )
                return (res.rowcount or 0) == 1
        except Exception as exc:
            logger.error("update_password 실패: %s", exc)
            return False

    async def update_mfa_secret(
        self, user_id: str, mfa_secret: Optional[str]
    ) -> bool:
        try:
            async with get_db() as db:
                if db is None:
                    return False
                res = await db.execute(
                    text(
                        "UPDATE users SET mfa_secret = :mfa WHERE user_id = :uid"
                    ),
                    {"mfa": mfa_secret, "uid": user_id},
                )
                return (res.rowcount or 0) == 1
        except Exception as exc:
            logger.error("update_mfa_secret 실패: %s", exc)
            return False


user_repo = UserRepository()


__all__ = ["UserRepository", "user_repo"]
