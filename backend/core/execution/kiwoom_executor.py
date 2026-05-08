"""BAR-OPS-06 — KiwoomOrderExecutor live mode.

OAuth2 token + HTTP 주문. BAR-63b 정식 어댑터.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from pydantic import SecretStr

from backend.core.gateway.kiwoom_oauth import KiwoomOAuth2Manager
from backend.models.order import RoutingDecision

logger = logging.getLogger(__name__)


class KiwoomLiveOrderExecutor:
    """실 키움 OpenAPI 주문 어댑터.

    POST /uapi/domestic-stock/v1/trading/order-cash
    Authorization: Bearer <access_token>
    appkey / appsecret 헤더.
    """

    name = "kiwoom_live"

    def __init__(
        self,
        oauth: KiwoomOAuth2Manager,
        account_no: str,
        app_key: SecretStr,
        app_secret: SecretStr,
        http_client: Optional[httpx.AsyncClient] = None,
        order_path: str = "/uapi/domestic-stock/v1/trading/order-cash",
    ) -> None:
        if not isinstance(app_key, SecretStr) or not isinstance(app_secret, SecretStr):
            raise TypeError("credentials must be SecretStr")
        if not account_no:
            raise ValueError("account_no required")
        self._oauth = oauth
        self._account_no = account_no
        self._app_key = app_key
        self._app_secret = app_secret
        self._http = http_client
        self._order_path = order_path

    async def submit(self, decision: RoutingDecision) -> dict:
        """라우팅 결정 → 실 주문 송신."""
        if not decision.is_routed:
            return {"executor": self.name, "status": "skipped", "reason": "not_routed"}

        token = await self._oauth.get_token()
        client = self._http or httpx.AsyncClient(timeout=10)
        owns = self._http is None

        # 매수/매도 transaction id (키움 — TTTC0802U/TTTC0801U)
        side = decision.request.side.value
        tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"

        payload = {
            "CANO": self._account_no.split("-")[0] if "-" in self._account_no else self._account_no,
            "ACNT_PRDT_CD": self._account_no.split("-")[1] if "-" in self._account_no else "01",
            "PDNO": decision.request.symbol,
            "ORD_DVSN": "00" if decision.request.order_type.value == "limit" else "01",
            "ORD_QTY": str(decision.expected_qty),
            "ORD_UNPR": str(decision.expected_price) if decision.expected_price else "0",
        }

        headers = {
            "Authorization": f"Bearer {token.access_token.get_secret_value()}",
            "appkey": self._app_key.get_secret_value(),
            "appsecret": self._app_secret.get_secret_value(),
            "tr_id": tr_id,
            "Content-Type": "application/json; charset=utf-8",
        }

        try:
            url = f"{self._oauth._base_url}{self._order_path}"
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            # security: 헤더에 secret 포함 — 로그에 절대 출력 X
            logger.error(
                "kiwoom order failed: err=%s symbol=%s qty=%d",
                type(exc).__name__,
                decision.request.symbol,
                decision.expected_qty,
            )
            raise
        finally:
            if owns:
                await client.aclose()

        return {
            "executor": self.name,
            "venue": decision.venue.value if decision.venue else None,
            "tr_id": tr_id,
            "qty": decision.expected_qty,
            "rt_cd": data.get("rt_cd"),       # 0 = 성공
            "msg_cd": data.get("msg_cd"),
            "msg1": data.get("msg1"),
            "order_id": data.get("output", {}).get("ODNO"),
            "status": "filled" if data.get("rt_cd") == "0" else "rejected",
        }


__all__ = ["KiwoomLiveOrderExecutor"]
