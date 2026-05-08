"""BAR-OPS-04 — 인증 REST v2 (UserRepository + bcrypt 통합).

OPS-01 의 _USER_DB stub 을 실 UserRepository 로 교체.
+ /api/auth/register 추가.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field, SecretStr

from backend.db.repositories.user_repo import UserRepository
from backend.models.user import User
from backend.security.auth import JWTService, Role
from backend.security.mfa import LiveTradingGate, MFAService
from backend.security.password import PasswordHasher


router = APIRouter(prefix="/api/auth/v2", tags=["auth-v2"])


# ─── 스키마 ────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    user_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    role: Role = Role.VIEWER


class RegisterResponse(BaseModel):
    user_id: str
    role: str
    created: bool


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    user_id: str
    role: str


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str


class MFAVerifyRequest(BaseModel):
    otp_code: str = Field(min_length=6, max_length=6)


# ─── 의존성 ─────────────────────────────────────────────────


class AuthV2Config:
    jwt: Optional[JWTService] = None
    hasher: Optional[PasswordHasher] = None
    repo: Optional[UserRepository] = None
    mfa: Optional[MFAService] = None
    live_gate: Optional[LiveTradingGate] = None


_CFG = AuthV2Config()


def configure(
    jwt: JWTService,
    hasher: PasswordHasher,
    repo: UserRepository,
    mfa: Optional[MFAService] = None,
    live_gate: Optional[LiveTradingGate] = None,
) -> None:
    _CFG.jwt = jwt
    _CFG.hasher = hasher
    _CFG.repo = repo
    _CFG.mfa = mfa or MFAService()
    _CFG.live_gate = live_gate or LiveTradingGate(_CFG.mfa)


def _require_all() -> None:
    if _CFG.jwt is None or _CFG.hasher is None or _CFG.repo is None:
        raise HTTPException(status_code=500, detail="auth_v2 not configured")


# ─── 라우트 ───────────────────────────────────────────────


@router.post("/register", response_model=RegisterResponse)
async def register(req: RegisterRequest) -> RegisterResponse:
    _require_all()
    existing = await _CFG.repo.find_by_user_id(req.user_id)
    if existing is not None:
        raise HTTPException(status_code=409, detail="user_id already exists")
    pw_hash = _CFG.hasher.hash(req.password)
    user = User(
        user_id=req.user_id,
        email=req.email,
        password_hash=pw_hash,
        role=req.role,
    )
    new_id = await _CFG.repo.insert(user)
    if new_id is None:
        raise HTTPException(status_code=409, detail="user creation failed")
    return RegisterResponse(
        user_id=req.user_id, role=req.role.value, created=True
    )


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, response: Response) -> LoginResponse:
    _require_all()
    user = await _CFG.repo.find_by_user_id(req.user_id)
    if user is None or not _CFG.hasher.verify(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    access = _CFG.jwt.encode_access(user.user_id, user.role)
    refresh = _CFG.jwt.encode_refresh(user.user_id)
    response.set_cookie(
        key="refresh_token",
        value=refresh,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=7 * 24 * 3600,
    )
    return LoginResponse(
        access_token=access, user_id=user.user_id, role=user.role.value
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(req: RefreshRequest) -> RefreshResponse:
    _require_all()
    try:
        data = _CFG.jwt.decode(req.refresh_token, expected_type="refresh")
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = await _CFG.repo.find_by_user_id(data["user_id"])
    if user is None:
        raise HTTPException(status_code=401, detail="user not found")
    return RefreshResponse(
        access_token=_CFG.jwt.encode_access(user.user_id, user.role)
    )


@router.post("/mfa/verify")
async def mfa_verify(
    req: MFAVerifyRequest, authorization: Optional[str] = Header(None)
) -> dict:
    _require_all()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:]
    try:
        payload = _CFG.jwt.decode(token)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))
    user = await _CFG.repo.find_by_user_id(payload.user_id)
    if user is None or not user.mfa_secret:
        raise HTTPException(status_code=400, detail="MFA not registered")
    secret = SecretStr(user.mfa_secret)
    ok, reason = _CFG.live_gate.authorize(secret, req.otp_code)
    if not ok:
        raise HTTPException(status_code=401, detail=reason)
    return {"authorized": True, "user_id": user.user_id}


__all__ = ["router", "configure"]
