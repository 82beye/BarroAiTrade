"""BAR-OPS-10 — 키움 자체 OpenAPI 네이티브 OAuth Manager.

KIS Open Trading API 와 별개로 키움증권이 운영하는 자체 REST OpenAPI.

엔드포인트 (공식): https://api.kiwoom.com/oauth2/token
요청 바디: {"grant_type": "client_credentials", "appkey": "...", "secretkey": "..."}
응답:
  - return_code: int (0=성공)
  - return_msg: str
  - token: str (Bearer token)
  - token_type: str ("bearer")
  - expires_dt: str (만료일시 YYYYMMDDHHMMSS)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KiwoomNativeToken:
    access_token: SecretStr
    token_type: str
    expires_at: datetime


class KiwoomNativeOAuth:
    """키움 자체 OpenAPI OAuth (api.kiwoom.com)."""

    DEFAULT_BASE_URL = "https://api.kiwoom.com"

    def __init__(
        self,
        app_key: SecretStr,
        app_secret: SecretStr,
        base_url: str = DEFAULT_BASE_URL,
        http_client: Optional[httpx.AsyncClient] = None,
        refresh_margin_seconds: int = 1800,
    ) -> None:
        if not isinstance(app_key, SecretStr) or not isinstance(app_secret, SecretStr):
            raise TypeError("credentials must be SecretStr (CWE-798)")
        if not base_url.startswith("https://"):
            raise ValueError("base_url must be https-only (CWE-918)")
        self._app_key = app_key
        self._app_secret = app_secret
        self._base_url = base_url.rstrip("/")
        self._http = http_client
        self._margin = refresh_margin_seconds
        self._token: Optional[KiwoomNativeToken] = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    async def get_token(self) -> KiwoomNativeToken:
        async with self._lock:
            now = datetime.now()
            if self._token and (self._token.expires_at - now).total_seconds() > self._margin:
                return self._token
            self._token = await self._issue(now)
            return self._token

    async def _issue(self, now: datetime) -> KiwoomNativeToken:
        url = f"{self._base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self._app_key.get_secret_value(),
            "secretkey": self._app_secret.get_secret_value(),
        }
        owns = self._http is None
        client = self._http or httpx.AsyncClient(timeout=15)
        try:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json;charset=UTF-8"},
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "kiwoom-native token issue failed: status=%s url=%s",
                exc.response.status_code, url,
            )
            raise
        except Exception as exc:
            logger.error("kiwoom-native token issue error: %s", type(exc).__name__)
            raise
        finally:
            if owns:
                await client.aclose()

        rc = data.get("return_code")
        if rc != 0:
            raise RuntimeError(
                f"kiwoom-native token error: rc={rc} msg={data.get('return_msg')}"
            )

        token = data.get("token")
        if not token:
            raise RuntimeError("kiwoom-native: token field missing in response")

        expires_dt_str = data.get("expires_dt", "")
        try:
            expires_at = datetime.strptime(expires_dt_str, "%Y%m%d%H%M%S")
        except (ValueError, TypeError):
            expires_at = now + timedelta(hours=24)

        return KiwoomNativeToken(
            access_token=SecretStr(token),
            token_type=data.get("token_type", "Bearer"),
            expires_at=expires_at,
        )


__all__ = ["KiwoomNativeOAuth", "KiwoomNativeToken"]
