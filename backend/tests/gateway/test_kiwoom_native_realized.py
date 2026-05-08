"""BAR-OPS-28 — fetch_realized_pnl (ka10073) 테스트."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_account import (
    KiwoomNativeAccountFetcher,
    RealizedPnLEntry,
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
async def test_invalid_date_format_raises():
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="invalid start_date"):
        await f.fetch_realized_pnl(start_date="2026-04-01", end_date="20260508")
    with pytest.raises(ValueError, match="invalid end_date"):
        await f.fetch_realized_pnl(start_date="20260401", end_date="bad")


@pytest.mark.asyncio
async def test_fetch_realized_pnl_parses_rows():
    http = AsyncMock(spec=httpx.AsyncClient)
    payload = {
        "return_code": 0,
        "dt_stk_rlzt_pl": [
            {
                "dt": "20260410", "stk_cd": "010820", "stk_nm": "퍼스텍",
                "cntr_qty": "1", "buy_uv": "11246.22", "cntr_pric": "11410",
                "tdy_sel_pl": "71.78", "pl_rt": "+0.64",
                "tdy_trde_cmsn": "70", "tdy_trde_tax": "22",
            },
            {
                "dt": "20260411", "stk_cd": "A005930", "stk_nm": "삼성전자",
                "cntr_qty": "10", "buy_uv": "260000", "cntr_pric": "276500",
                "tdy_sel_pl": "165000", "pl_rt": "+6.35",
                "tdy_trde_cmsn": "150", "tdy_trde_tax": "830",
            },
            {
                "dt": "20260412", "stk_cd": "000660", "stk_nm": "SK하이닉스",
                "cntr_qty": "5", "buy_uv": "210000", "cntr_pric": "203000",
                "tdy_sel_pl": "-35000", "pl_rt": "-3.33",
                "tdy_trde_cmsn": "70", "tdy_trde_tax": "300",
            },
        ],
    }
    http.post = AsyncMock(return_value=_http_response(payload))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)

    rows = await f.fetch_realized_pnl(start_date="20260401", end_date="20260508")
    assert len(rows) == 3
    # 첫번째: 양수 손익
    assert rows[0].symbol == "010820"
    assert rows[0].pnl == Decimal("71.78")
    # 두번째: A prefix strip
    assert rows[1].symbol == "005930"
    assert rows[1].pnl == Decimal("165000")
    assert rows[1].pnl_rate == Decimal("6.35")
    # 세번째: 음수 손익
    assert rows[2].pnl == Decimal("-35000")
    assert rows[2].pnl_rate == Decimal("-3.33")
    # 헤더 검증
    assert http.post.call_args.kwargs["headers"]["api-id"] == "ka10073"
    body = http.post.call_args.kwargs["json"]
    assert body["strt_dt"] == "20260401"
    assert body["end_dt"] == "20260508"


@pytest.mark.asyncio
async def test_fetch_realized_pnl_empty_returns_empty():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 0, "dt_stk_rlzt_pl": [],
    }))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    assert await f.fetch_realized_pnl("20260401", "20260508") == []


@pytest.mark.asyncio
async def test_fetch_realized_pnl_error_raises():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response({
        "return_code": 2, "return_msg": "오류",
    }))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    with pytest.raises(RuntimeError, match="rc=2"):
        await f.fetch_realized_pnl("20260401", "20260508")
