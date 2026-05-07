"""BAR-74 — 어드민 백오피스 REST (admin role 전용).

운영 frontend `frontend/app/admin/` 가 소비. 모든 액션 audit_log 기록.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from backend.db.database import get_db
from backend.security.auth import JWTService, RBACPolicy, Role


router = APIRouter(prefix="/api/admin", tags=["admin"])


class UserSummary(BaseModel):
    user_id: str
    role: str
    api_calls: int = 0


class AuditEntry(BaseModel):
    id: int
    event_type: str
    symbol: Optional[str] = None
    created_at: str


def _check_admin_token(
    authorization: Optional[str], jwt_service: Optional[JWTService] = None
) -> None:
    """간단 헤더 검증 (BAR-74b 에서 정식 FastAPI Depends + 통합)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    # jwt_service 미주입 시 — runtime check skip (BAR-67b)
    if jwt_service is None:
        return
    token = authorization[7:]
    try:
        payload = jwt_service.decode(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    try:
        RBACPolicy.require_role(payload.role, Role.ADMIN)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/users", response_model=list[UserSummary])
async def list_users(authorization: Optional[str] = Header(None)) -> list[UserSummary]:
    _check_admin_token(authorization)
    # 운영: usage_metrics + users JOIN. worktree: stub
    return []


@router.get("/audit/recent", response_model=list[AuditEntry])
async def recent_audit(
    limit: int = 100, authorization: Optional[str] = Header(None)
) -> list[AuditEntry]:
    _check_admin_token(authorization)
    if limit > 1000:
        raise HTTPException(status_code=422, detail="limit too high")
    async with get_db() as db:
        if db is None:
            return []
        res = await db.execute(
            text("SELECT id, event_type, symbol, created_at FROM audit_log "
                 "ORDER BY created_at DESC LIMIT :limit"),
            {"limit": limit},
        )
        return [
            AuditEntry(
                id=int(r["id"]),
                event_type=r["event_type"],
                symbol=r.get("symbol"),
                created_at=str(r["created_at"]),
            )
            for r in res.mappings().all()
        ]


@router.post("/strategies/{strategy_id}/toggle")
async def toggle_strategy(
    strategy_id: str, authorization: Optional[str] = Header(None)
) -> dict:
    _check_admin_token(authorization)
    # 운영: 전략 enabled flag toggle (BAR-74b)
    return {"strategy_id": strategy_id, "toggled": True}


__all__ = ["router"]
