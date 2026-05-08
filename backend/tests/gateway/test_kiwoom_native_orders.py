"""BAR-OPS-14 — KiwoomNativeOrderExecutor 테스트."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import (
    KiwoomNativeOAuth,
    KiwoomNativeToken,
)
from backend.core.gateway.kiwoom_native_orders import (
    KiwoomNativeOrderExecutor,
    OrderSide,
)


def _http_response(status: int, payload: dict) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _oauth_mock() -> AsyncMock:
    o = AsyncMock(spec=KiwoomNativeOAuth)
    o.base_url = "https://mockapi.kiwoom.com"
    o.get_token = AsyncMock(
        return_value=KiwoomNativeToken(
            access_token=SecretStr("tok"),
            token_type="Bearer",
            expires_at=datetime(2099, 1, 1),
        )
    )
    return o


# -- validation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_market_raises():
    with pytest.raises(ValueError, match="invalid market"):
        KiwoomNativeOrderExecutor(oauth=_oauth_mock(), market="NYSE")


@pytest.mark.asyncio
async def test_invalid_symbol_rejected():
    e = KiwoomNativeOrderExecutor(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="invalid symbol"):
        await e.place_buy(symbol="abc", qty=1)
    with pytest.raises(ValueError, match="invalid symbol"):
        await e.place_buy(symbol="12345", qty=1)
    with pytest.raises(ValueError, match="invalid symbol"):
        await e.place_buy(symbol="005930_AL", qty=1)


@pytest.mark.asyncio
async def test_qty_must_be_positive():
    e = KiwoomNativeOrderExecutor(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="qty must be > 0"):
        await e.place_buy(symbol="005930", qty=0)
    with pytest.raises(ValueError, match="qty must be > 0"):
        await e.place_sell(symbol="005930", qty=-1)


@pytest.mark.asyncio
async def test_price_if_specified_must_be_positive():
    e = KiwoomNativeOrderExecutor(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="price must be > 0"):
        await e.place_buy(symbol="005930", qty=1, price=Decimal("0"))


# -- dry_run ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_call_http():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock()
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, dry_run=True, rate_limit_seconds=0,
    )
    r = await e.place_buy(symbol="005930", qty=10)
    assert r.dry_run is True
    assert r.order_no == "DRY_RUN"
    http.post.assert_not_called()


# -- buy/sell market vs limit ---------------------------------------------


@pytest.mark.asyncio
async def test_buy_market_order_sends_kt10000_with_empty_price():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(
        return_value=_http_response(200, {"return_code": 0, "return_msg": "정상", "ord_no": "0001234"})
    )
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    r = await e.place_buy(symbol="005930", qty=10)
    assert r.order_no == "0001234"
    headers = http.post.call_args.kwargs["headers"]
    assert headers["api-id"] == "kt10000"
    body = http.post.call_args.kwargs["json"]
    assert body["stk_cd"] == "005930"
    assert body["ord_qty"] == "10"
    assert body["ord_uv"] == ""           # 시장가 → 공란
    assert body["trde_tp"] == "3"          # 시장가
    assert body["dmst_stex_tp"] == "KRX"


@pytest.mark.asyncio
async def test_sell_limit_order_sends_kt10001_with_price():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(
        return_value=_http_response(200, {"return_code": 0, "return_msg": "정상", "ord_no": "0009999"})
    )
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    r = await e.place_sell(symbol="005930", qty=5, price=Decimal("280000"))
    assert r.side == OrderSide.SELL
    headers = http.post.call_args.kwargs["headers"]
    assert headers["api-id"] == "kt10001"
    body = http.post.call_args.kwargs["json"]
    assert body["ord_uv"] == "280000"
    assert body["trde_tp"] == "0"          # 지정가


# -- error -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_return_code_raises():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(
        return_value=_http_response(200, {"return_code": 20, "return_msg": "장종료"})
    )
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    with pytest.raises(RuntimeError, match="rc=20"):
        await e.place_buy(symbol="005930", qty=1)
