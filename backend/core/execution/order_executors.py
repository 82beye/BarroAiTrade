"""BAR-OPS-04 — OrderExecutor 운영 어댑터 stub (BAR-63b).

LiveTradingOrchestrator 의 OrderExecutor Protocol 구현체.
worktree: paper trading mock. 운영: 실 API 어댑터로 교체.
"""
from __future__ import annotations

import logging
from typing import Optional

from pydantic import SecretStr

from backend.models.order import RoutingDecision

logger = logging.getLogger(__name__)


class PaperOrderExecutor:
    """페이퍼 트레이딩 — 모든 주문을 mock filled 로 처리."""

    name = "paper"

    def __init__(self) -> None:
        self.submitted: list[RoutingDecision] = []

    async def submit(self, decision: RoutingDecision) -> dict:
        self.submitted.append(decision)
        return {
            "executor": self.name,
            "venue": decision.venue.value if decision.venue else None,
            "qty": decision.expected_qty,
            "price": float(decision.expected_price) if decision.expected_price else None,
            "status": "paper_filled",
        }


class KiwoomOrderExecutor:
    """BAR-63b — 키움 OpenAPI 실 주문 어댑터 stub.

    운영: HTTP/WebSocket 통합. 본 구현은 시그니처만.
    """

    name = "kiwoom"

    def __init__(
        self,
        app_key: SecretStr,
        app_secret: SecretStr,
        account_no: str,
        mock: bool = True,
    ) -> None:
        if not isinstance(app_key, SecretStr) or not isinstance(app_secret, SecretStr):
            raise TypeError("credentials must be SecretStr (CWE-798)")
        if not account_no:
            raise ValueError("account_no required")
        self._app_key = app_key
        self._app_secret = app_secret
        self._account_no = account_no
        self._mock = mock

    async def submit(self, decision: RoutingDecision) -> dict:
        if self._mock:
            return {
                "executor": self.name,
                "venue": decision.venue.value if decision.venue else None,
                "qty": decision.expected_qty,
                "status": "mock_filled",
            }
        # 운영: 실 API 호출 (BAR-63b 정식)
        raise NotImplementedError("KiwoomOrderExecutor live mode — BAR-63b")


class IBKROrderExecutor:
    """BAR-76b — IBKR 미국/홍콩 주식 stub."""

    name = "ibkr"

    def __init__(self, api_key: SecretStr, mock: bool = True) -> None:
        if not isinstance(api_key, SecretStr):
            raise TypeError("api_key must be SecretStr")
        self._api_key = api_key
        self._mock = mock

    async def submit(self, decision: RoutingDecision) -> dict:
        if self._mock:
            return {"executor": self.name, "status": "mock_filled"}
        raise NotImplementedError("IBKR live mode — BAR-76b")


class UpbitOrderExecutor:
    """BAR-77b — Upbit 코인 stub."""

    name = "upbit"

    def __init__(self, access_key: SecretStr, secret_key: SecretStr, mock: bool = True) -> None:
        if not isinstance(access_key, SecretStr) or not isinstance(secret_key, SecretStr):
            raise TypeError("keys must be SecretStr")
        self._access_key = access_key
        self._secret_key = secret_key
        self._mock = mock

    async def submit(self, decision: RoutingDecision) -> dict:
        if self._mock:
            return {"executor": self.name, "status": "mock_filled"}
        raise NotImplementedError("Upbit live mode — BAR-77b")


__all__ = [
    "PaperOrderExecutor",
    "KiwoomOrderExecutor",
    "IBKROrderExecutor",
    "UpbitOrderExecutor",
]
