"""BAR-OPS-01 — 인증 REST 통합 (BAR-67b + BAR-68b 운영 트랙).

/api/auth/login + /refresh + /mfa/setup + /mfa/verify
- httpOnly Secure 쿠키 (refresh)
- access token bearer header
- MFA TOTP 검증 + live trading 활성화
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr

from backend.security.auth import JWTService, RBACPolicy, Role
from backend.security.mfa import LiveTradingGate, MFAService


router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── Request/Response 스키마 ──────────────────────────────


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1)
    password: str = Field(min_length=1)    # 운영: bcrypt hash 비교 (BAR-OPS-02)


class LoginResponse(BaseModel):
    access_token: str
    user_id: str
    role: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str


class MFASetupResponse(BaseModel):
    secret: str
    provisioning_uri: str


class MFAVerifyRequest(BaseModel):
    otp_code: str = Field(min_length=6, max_length=6)


# ─── 의존성 주입 (테스트 친화) ─────────────────────────────


_JWT_SERVICE: Optional[JWTService] = None
_MFA_SERVICE: Optional[MFAService] = None
_LIVE_GATE: Optional[LiveTradingGate] = None
_USER_DB: dict[str, dict] = {}  # user_id → {role, password_hash, mfa_secret}


def configure(
    jwt_service: JWTService,
    mfa_service: Optional[MFAService] = None,
    live_gate: Optional[LiveTradingGate] = None,
    user_db: Optional[dict] = None,
) -> None:
    """앱 부팅 시 호출. 테스트가 mock 주입 시 호출."""
    global _JWT_SERVICE, _MFA_SERVICE, _LIVE_GATE, _USER_DB
    _JWT_SERVICE = jwt_service
    _MFA_SERVICE = mfa_service or MFAService()
    _LIVE_GATE = live_gate or LiveTradingGate(_MFA_SERVICE)
    if user_db is not None:
        _USER_DB = user_db


def _require_jwt() -> JWTService:
    if _JWT_SERVICE is None:
        raise HTTPException(status_code=500, detail="auth not configured")
    return _JWT_SERVICE


# ─── 라우트 ───────────────────────────────────────────────


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, response: Response) -> LoginResponse:
    user = _USER_DB.get(req.user_id)
    if user is None or user.get("password") != req.password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    jwt_svc = _require_jwt()
    role = Role(user.get("role", "viewer"))
    access = jwt_svc.encode_access(req.user_id, role)
    refresh = jwt_svc.encode_refresh(req.user_id)
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 3600,
    )
    return LoginResponse(access_token=access, user_id=req.user_id, role=role.value)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(req: RefreshRequest) -> RefreshResponse:
    jwt_svc = _require_jwt()
    try:
        data = jwt_svc.decode(req.refresh_token, expected_type="refresh")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user_id = data["user_id"]
    user = _USER_DB.get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    role = Role(user.get("role", "viewer"))
    new_access = jwt_svc.encode_access(user_id, role)
    return RefreshResponse(access_token=new_access)


@router.post("/mfa/setup", response_model=MFASetupResponse)
async def mfa_setup(authorization: Optional[str] = Header(None)) -> MFASetupResponse:
    """admin 권한 사용자가 자기 secret 발급. 운영: per-user secret 저장."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    try:
        payload = _require_jwt().decode(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    if _MFA_SERVICE is None:
        raise HTTPException(status_code=500, detail="mfa not configured")
    secret = _MFA_SERVICE.generate_secret()
    uri = _MFA_SERVICE.provisioning_uri(secret, payload.user_id)
    # 운영: _USER_DB[payload.user_id]["mfa_secret"] = secret
    return MFASetupResponse(
        secret=secret.get_secret_value(), provisioning_uri=uri
    )


@router.post("/mfa/verify")
async def mfa_verify(
    req: MFAVerifyRequest, authorization: Optional[str] = Header(None)
) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    try:
        payload = _require_jwt().decode(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = _USER_DB.get(payload.user_id)
    if user is None or "mfa_secret" not in user:
        raise HTTPException(status_code=400, detail="MFA not registered")
    if _LIVE_GATE is None:
        raise HTTPException(status_code=500, detail="live gate not configured")
    secret = SecretStr(user["mfa_secret"])
    ok, reason = _LIVE_GATE.authorize(secret, req.otp_code)
    if not ok:
        raise HTTPException(status_code=401, detail=reason)
    return {"authorized": True, "user_id": payload.user_id}


__all__ = ["router", "configure"]
