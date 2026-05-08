"""BAR-OPS-34 — cancel_order (kt10003) 테스트."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import (
    KiwoomNativeOAuth,
    KiwoomNativeToken,
)
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor


def _http_response(payload: dict) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = 200
    r.json.return_value = payload
    r.raise_for_status = MagicMock()
    return r


def _oauth_mock() -> AsyncMock:
    o = AsyncMock(spec=KiwoomNativeOAuth)
    o.base_url = "https://mockapi.kiwoom.com"
    o.get_token = AsyncMock(
        return_value=KiwoomNativeToken(
            access_token=SecretStr("tok"), token_type="Bearer",
            expires_at=datetime(2099, 1, 1),
        )
    )
    return o


@pytest.mark.asyncio
async def test_cancel_requires_order_no():
    e = KiwoomNativeOrderExecutor(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="original_order_no"):
        await e.cancel_order(original_order_no="", symbol="005930")
    with pytest.raises(ValueError, match="original_order_no"):
        await e.cancel_order(original_order_no="   ", symbol="005930")


@pytest.mark.asyncio
async def test_cancel_invalid_symbol():
    e = KiwoomNativeOrderExecutor(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="invalid symbol"):
        await e.cancel_order(original_order_no="0001", symbol="abc")


@pytest.mark.asyncio
async def test_cancel_negative_qty_rejected():
    e = KiwoomNativeOrderExecutor(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="cancel_qty"):
        await e.cancel_order(original_order_no="0001", symbol="005930", cancel_qty=-1)


@pytest.mark.asyncio
async def test_cancel_dry_run_no_http():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock()
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, dry_run=True, rate_limit_seconds=0,
    )
    r = await e.cancel_order(original_order_no="0001234", symbol="005930", cancel_qty=10)
    assert r.dry_run is True
    assert r.order_no == "DRY_CANCEL:0001234"
    http.post.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_full_qty_zero_sent():
    """cancel_qty=0 → 전량 취소 의도."""
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 0, "return_msg": "정상", "ord_no": "0001234",
    }))
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    r = await e.cancel_order(original_order_no="0001234", symbol="005930", cancel_qty=0)
    assert r.return_code == 0
    body = http.post.call_args.kwargs["json"]
    assert body["orig_ord_no"] == "0001234"
    assert body["stk_cd"] == "005930"
    assert body["cncl_qty"] == "0"
    assert body["dmst_stex_tp"] == "KRX"
    headers = http.post.call_args.kwargs["headers"]
    assert headers["api-id"] == "kt10003"


@pytest.mark.asyncio
async def test_cancel_partial_qty():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 0, "ord_no": "0001235",
    }))
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    await e.cancel_order(original_order_no="0001234", symbol="005930", cancel_qty=5)
    assert http.post.call_args.kwargs["json"]["cncl_qty"] == "5"


@pytest.mark.asyncio
async def test_cancel_error_raises():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 20, "return_msg": "RC4032:원주문번호 없음",
    }))
    e = KiwoomNativeOrderExecutor(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    with pytest.raises(RuntimeError, match="rc=20"):
        await e.cancel_order(original_order_no="9999999", symbol="005930")
