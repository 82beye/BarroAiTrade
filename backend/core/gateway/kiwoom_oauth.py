"""BAR-OPS-06 — Kiwoom OAuth2 토큰 매니저.

POST /oauth2/token — app_key/app_secret → access_token (24h TTL).
캐시 + 만료 30분 전 자동 refresh.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

logger = logging.getLogger(__name__)


class TokenInfo(BaseModel):
    """OAuth2 token + 만료 시각 (frozen)."""

    model_config = ConfigDict(frozen=True)

    access_token: SecretStr
    expires_at: datetime
    token_type: str = "Bearer"

    def is_valid(self, now: datetime, refresh_margin_seconds: int = 1800) -> bool:
        """만료 30분 전부터 갱신 필요."""
        margin = timedelta(seconds=refresh_margin_seconds)
        return now + margin < self.expires_at


class KiwoomOAuth2Manager:
    """app_key/app_secret → access_token. token caching + auto refresh."""

    DEFAULT_TTL_SECONDS = 24 * 3600

    def __init__(
        self,
        base_url: str,
        app_key: SecretStr,
        app_secret: SecretStr,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        if not isinstance(app_key, SecretStr) or not isinstance(app_secret, SecretStr):
            raise TypeError("credentials must be SecretStr (CWE-798)")
        if not base_url.startswith("https://"):
            raise ValueError("base_url must be https (CWE-918 SSRF)")
        self._base_url = base_url
        self._app_key = app_key
        self._app_secret = app_secret
        self._http = http_client
        self._token: Optional[TokenInfo] = None
        self._lock = asyncio.Lock()

    async def get_token(self, now: Optional[datetime] = None) -> TokenInfo:
        """캐시된 토큰 — 만료 임박 시 refresh."""
        now = now or datetime.now(timezone.utc)
        async with self._lock:
            if self._token is not None and self._token.is_valid(now):
                return self._token
            self._token = await self._issue_token(now)
            return self._token

    async def _issue_token(self, now: datetime) -> TokenInfo:
        client = self._http or httpx.AsyncClient(timeout=10)
        owns_client = self._http is None
        try:
            resp = await client.post(
                f"{self._base_url}/oauth2/token",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self._app_key.get_secret_value(),
                    "appsecret": self._app_secret.get_secret_value(),
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            # security: API key 노출 차단 — 메시지에서 secret 마스킹
            logger.error(
                "kiwoom oauth issue failed: %s", type(exc).__name__
            )
            raise
        finally:
            if owns_client:
                await client.aclose()

        if "access_token" not in data:
            raise ValueError("missing access_token in response")
        ttl = int(data.get("expires_in", self.DEFAULT_TTL_SECONDS))
        return TokenInfo(
            access_token=SecretStr(str(data["access_token"])),
            expires_at=now + timedelta(seconds=ttl),
            token_type=data.get("token_type", "Bearer"),
        )

    def reset_cache(self) -> None:
        """수동 토큰 invalidate (키 회전 시)."""
        self._token = None


__all__ = ["TokenInfo", "KiwoomOAuth2Manager"]
