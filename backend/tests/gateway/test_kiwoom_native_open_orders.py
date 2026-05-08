"""BAR-OPS-33 — fetch_open_orders (kt00004) 테스트."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_account import (
    KiwoomNativeAccountFetcher,
    OpenOrder,
)
from backend.core.gateway.kiwoom_native_oauth import (
    KiwoomNativeOAuth,
    KiwoomNativeToken,
)


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
async def test_invalid_exchange_raises():
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="invalid exchange"):
        await f.fetch_open_orders(exchange="NYSE")


@pytest.mark.asyncio
async def test_invalid_trade_type_raises():
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="invalid trade_type"):
        await f.fetch_open_orders(trade_type="9")


@pytest.mark.asyncio
async def test_fetch_open_orders_empty():
    """모의 환경 0건 — stk_acnt_evlt_prst 빈 list 응답."""
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 0, "stk_acnt_evlt_prst": [],
    }))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    out = await f.fetch_open_orders()
    assert out == []
    headers = http.post.call_args.kwargs["headers"]
    assert headers["api-id"] == "kt00004"
    body = http.post.call_args.kwargs["json"]
    assert body["dmst_stex_tp"] == "KRX"
    assert body["qry_tp"] == "1"


@pytest.mark.asyncio
async def test_fetch_open_orders_parses_buy_sell_unknown():
    http = AsyncMock(spec=httpx.AsyncClient)
    payload = {
        "return_code": 0,
        "open_ordr": [
            {"ord_no": "0001234", "stk_cd": "A005930", "stk_nm": "삼성전자",
             "trde_tp": "1", "ord_qty": "10", "cntr_qty": "3",
             "ord_uv": "276500", "ord_dt": "20260508"},
            {"ord_no": "0001235", "stk_cd": "000660", "stk_nm": "SK하이닉스",
             "trde_tp": "2", "ord_qty": "5", "cntr_qty": "0",
             "ord_uv": "203000", "ord_dt": "20260508"},
            {"ord_no": "0001236", "stk_cd": "319400", "stk_nm": "현대무벡스",
             "trde_tp": "X", "ord_qty": "100", "cntr_qty": "100",
             "ord_uv": "37700", "ord_dt": "20260508"},
        ],
    }
    http.post = AsyncMock(return_value=_http_response(payload))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    out = await f.fetch_open_orders()
    assert len(out) == 3
    # 매수
    assert out[0].side == "buy"
    assert out[0].symbol == "005930"            # A prefix strip
    assert out[0].order_qty == 10
    assert out[0].filled_qty == 3
    assert out[0].pending_qty == 7
    assert out[0].order_price == Decimal("276500")
    # 매도
    assert out[1].side == "sell"
    assert out[1].pending_qty == 5
    # unknown trade_type
    assert out[2].side == "unknown"
    # 전량 체결 → pending=0 (max clamp)
    assert out[2].pending_qty == 0


@pytest.mark.asyncio
async def test_fetch_open_orders_error_raises():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 2, "return_msg": "오류",
    }))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    with pytest.raises(RuntimeError, match="rc=2"):
        await f.fetch_open_orders()
