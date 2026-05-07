"""BAR-OPS-01 — TenantContext FastAPI middleware.

Authorization Bearer 토큰 → JWT decode → TenantContext.set_user.
운영: app.user_id 를 RLS 와 연동.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from backend.core.multitenancy.tenant_context import TenantContext
from backend.security.auth import JWTService


class TenantContextMiddleware(BaseHTTPMiddleware):
    """Authorization 헤더 → JWT → user_id 추출 → TenantContext 설정."""

    def __init__(self, app, jwt_service: JWTService) -> None:
        super().__init__(app)
        self._jwt = jwt_service

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        token: Optional[str] = None
        auth = request.headers.get("Authorization")
        if auth and auth.startswith("Bearer "):
            token = auth[7:]

        ctx_token = None
        if token:
            try:
                payload = self._jwt.decode(token)
                ctx_token = TenantContext.set_user(payload.user_id)
            except ValueError:
                # 잘못된 토큰 — 익명으로 처리 (route-level 가드에서 401)
                pass

        try:
            response = await call_next(request)
        finally:
            if ctx_token is not None:
                TenantContext.reset(ctx_token)
        return response


__all__ = ["TenantContextMiddleware"]
