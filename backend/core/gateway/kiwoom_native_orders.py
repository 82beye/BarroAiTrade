"""BAR-OPS-14 — 키움 자체 OpenAPI 주문 어댑터.

검증 (2026-05-08, mockapi.kiwoom.com):
- POST /api/dostk/ordr
- kt10000: 주식 매수 주문 (Buy)
- kt10001: 주식 매도 주문 (Sell)
- body: {dmst_stex_tp(KRX), stk_cd, ord_qty, ord_uv, trde_tp, cond_uv}
- 응답: {return_code, return_msg, ord_no}
- 장 외 시간: rc=20 [2000](RC4058:모의투자 장종료)

trde_tp:
  0: 보통(지정가) - ord_uv 필수
  3: 시장가     - ord_uv 공란

⚠️ 모의 환경(mockapi) 외 실전(api.kiwoom.com)에서는 실제 주문 발생.
    DRY_RUN 모드 + 강제 검증 + audit log 권장.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Optional

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth

logger = logging.getLogger(__name__)


_ORDER_PATH = "/api/dostk/ordr"
_TR_BUY = "kt10000"
_TR_SELL = "kt10001"

_TRDE_TP_LIMIT = "0"        # 보통(지정가)
_TRDE_TP_MARKET = "3"       # 시장가


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class OrderResult:
    side: OrderSide
    symbol: str
    qty: int
    price: Optional[Decimal]      # None=시장가
    order_no: str
    return_code: int
    return_msg: str
    dry_run: bool = False


class KiwoomNativeOrderExecutor:
    """키움 자체 OpenAPI 주식 주문 실행자."""

    def __init__(
        self,
        oauth: KiwoomNativeOAuth,
        http_client: Optional[httpx.AsyncClient] = None,
        rate_limit_seconds: float = 0.25,
        dry_run: bool = False,
        market: str = "KRX",      # KRX / NXT / SOR
    ) -> None:
        if market not in {"KRX", "NXT", "SOR"}:
            raise ValueError(f"invalid market: {market}, must be KRX/NXT/SOR")
        self._oauth = oauth
        self._http = http_client
        self._rate = rate_limit_seconds
        self._dry_run = dry_run
        self._market = market

    async def place_buy(
        self,
        symbol: str,
        qty: int,
        price: Optional[Decimal] = None,
    ) -> OrderResult:
        return await self._place(OrderSide.BUY, symbol, qty, price)

    async def place_sell(
        self,
        symbol: str,
        qty: int,
        price: Optional[Decimal] = None,
    ) -> OrderResult:
        return await self._place(OrderSide.SELL, symbol, qty, price)

    async def _place(
        self,
        side: OrderSide,
        symbol: str,
        qty: int,
        price: Optional[Decimal],
    ) -> OrderResult:
        if not symbol or not symbol.isdigit() or len(symbol) != 6:
            raise ValueError(f"invalid symbol: {symbol!r} (expected 6-digit)")
        if qty <= 0:
            raise ValueError(f"qty must be > 0, got {qty}")
        if price is not None and price <= 0:
            raise ValueError(f"price must be > 0 if specified, got {price}")

        tr_id = _TR_BUY if side == OrderSide.BUY else _TR_SELL
        trde_tp = _TRDE_TP_LIMIT if price is not None else _TRDE_TP_MARKET
        body = {
            "dmst_stex_tp": self._market,
            "stk_cd": symbol,
            "ord_qty": str(qty),
            "ord_uv": str(int(price)) if price is not None else "",
            "trde_tp": trde_tp,
            "cond_uv": "",
        }

        if self._dry_run:
            logger.info(
                "DRY_RUN order: side=%s symbol=%s qty=%d price=%s tr=%s",
                side.value, symbol, qty, str(price) if price else "MKT", tr_id,
            )
            return OrderResult(
                side=side, symbol=symbol, qty=qty, price=price,
                order_no="DRY_RUN", return_code=0, return_msg="dry_run", dry_run=True,
            )

        token = await self._oauth.get_token()
        client = self._http or httpx.AsyncClient(timeout=15)
        owns = self._http is None
        url = f"{self._oauth.base_url}{_ORDER_PATH}"
        try:
            resp = await client.post(
                url,
                headers={
                    "authorization": f"Bearer {token.access_token.get_secret_value()}",
                    "content-type": "application/json;charset=UTF-8",
                    "cont-yn": "N",
                    "next-key": "",
                    "api-id": tr_id,
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error("kiwoom-native order failed: side=%s sym=%s err=%s",
                         side.value, symbol, type(exc).__name__)
            raise
        finally:
            if owns:
                await client.aclose()
            await asyncio.sleep(self._rate)

        rc = data.get("return_code")
        if rc != 0:
            raise RuntimeError(
                f"kiwoom-native order error: side={side.value} symbol={symbol} "
                f"rc={rc} msg={data.get('return_msg')}"
            )

        return OrderResult(
            side=side, symbol=symbol, qty=qty, price=price,
            order_no=data.get("ord_no", ""),
            return_code=rc, return_msg=data.get("return_msg", ""),
        )


__all__ = ["KiwoomNativeOrderExecutor", "OrderResult", "OrderSide"]
