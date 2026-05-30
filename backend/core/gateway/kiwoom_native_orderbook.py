"""키움 자체 OpenAPI 호가 조회 — ka10004 주식호가요청 (2026-05-30 신규).

POST {base}/api/dostk/mrkcond, api-id=ka10004, body {stk_cd}.
응답: sel_{N}th_pre_bid/req (매도 N단계 가격/잔량), buy_{N}th_pre_bid/req (매수),
      bid_req_base_tm (호가시각). N=1..10.

호가창 초단타 스캘핑(backend/core/strategy/ob_scalp.py) 의 실시간 입력원.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import httpx

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth
from backend.models.market import MarketType, OrderBook

_TR_ORDERBOOK = "ka10004"
_PATH = "/api/dostk/mrkcond"


def _abs_int(s) -> int:
    """'+1,234' / '-1234' / '' → 1234 (절댓값 정수)."""
    if s is None:
        return 0
    digits = re.sub(r"[^0-9]", "", str(s))
    return int(digits) if digits else 0


def parse_orderbook(data: dict, symbol: str, levels: int = 10) -> OrderBook:
    """ka10004 응답 dict → OrderBook (qty>0 단계만)."""
    asks: list[tuple[float, float]] = []
    bids: list[tuple[float, float]] = []
    for i in range(1, levels + 1):
        ap = _abs_int(data.get(f"sel_{i}th_pre_bid"))
        aq = _abs_int(data.get(f"sel_{i}th_pre_req"))
        if ap > 0 and aq > 0:
            asks.append((float(ap), float(aq)))
        bp = _abs_int(data.get(f"buy_{i}th_pre_bid"))
        bq = _abs_int(data.get(f"buy_{i}th_pre_req"))
        if bp > 0 and bq > 0:
            bids.append((float(bp), float(bq)))
    asks.sort(key=lambda x: x[0])         # 매도 오름차순(best=최저)
    bids.sort(key=lambda x: -x[0])        # 매수 내림차순(best=최고)
    return OrderBook(symbol=symbol, asks=asks, bids=bids,
                     timestamp=datetime.now(), market_type=MarketType.STOCK)


class KiwoomNativeOrderbookFetcher:
    """ka10004 호가 조회 → OrderBook."""

    def __init__(self, oauth: KiwoomNativeOAuth,
                 http_client: Optional[httpx.AsyncClient] = None) -> None:
        self._oauth = oauth
        self._http = http_client

    async def fetch_orderbook(self, symbol: str) -> OrderBook:
        token = await self._oauth.get_token()
        client = self._http or httpx.AsyncClient(timeout=10)
        owns = self._http is None
        try:
            resp = await client.post(
                f"{self._oauth.base_url}{_PATH}",
                headers={
                    "authorization": f"Bearer {token.access_token.get_secret_value()}",
                    "content-type": "application/json;charset=UTF-8",
                    "api-id": _TR_ORDERBOOK,
                },
                json={"stk_cd": symbol},
            )
            resp.raise_for_status()
            data = resp.json()
            rc = data.get("return_code")
            if rc != 0:
                raise RuntimeError(f"ka10004 error rc={rc} msg={data.get('return_msg')}")
            return parse_orderbook(data, symbol)
        finally:
            if owns:
                await client.aclose()
