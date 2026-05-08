"""BAR-OPS-15 — KiwoomNativeAccountFetcher 테스트."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_account import (
    KiwoomNativeAccountFetcher,
    _abs_decimal,
    _signed_decimal,
)
from backend.core.gateway.kiwoom_native_oauth import (
    KiwoomNativeOAuth,
    KiwoomNativeToken,
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


# -- helpers ----------------------------------------------------------------


def test_abs_decimal():
    assert _abs_decimal("+1000.50") == Decimal("1000.50")
    assert _abs_decimal("-2000") == Decimal("2000")
    assert _abs_decimal("0") == Decimal("0")
    assert _abs_decimal("") == Decimal("0")
    assert _abs_decimal("not-a-num") == Decimal("0")


def test_signed_decimal():
    assert _signed_decimal("+500") == Decimal("500")
    assert _signed_decimal("-1500.25") == Decimal("-1500.25")
    assert _signed_decimal("0") == Decimal("0")
    assert _signed_decimal("") == Decimal("0")


# -- balance ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_balance_invalid_exchange():
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock())
    with pytest.raises(ValueError, match="invalid exchange"):
        await f.fetch_balance(exchange="NYSE")


@pytest.mark.asyncio
async def test_fetch_balance_parses_top_and_holdings():
    http = AsyncMock(spec=httpx.AsyncClient)
    payload = {
        "return_code": 0,
        "tot_pur_amt": "+10000000",
        "tot_evlt_amt": "+10500000",
        "tot_evlt_pl": "+500000",
        "tot_prft_rt": "+5.00",
        "prsm_dpst_aset_amt": "+50000000",
        "tot_loan_amt": "0",
        "tot_crd_loan_amt": "0",
        "tot_crd_ls_amt": "0",
        "acnt_evlt_remn_indv_tot": [
            {
                "stk_cd": "A005930",
                "stk_nm": "삼성전자",
                "rmnd_qty": "10",
                "pur_pric": "+260000",
                "cur_prc": "+276500",
                "evlt_amt": "+2765000",
                "evltv_prft": "+165000",
                "prft_rt": "+6.35",
            },
        ],
    }
    http.post = AsyncMock(return_value=_http_response(200, payload))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)

    bal = await f.fetch_balance()
    assert bal.total_purchase == Decimal("10000000")
    assert bal.total_eval == Decimal("10500000")
    assert bal.total_pnl == Decimal("500000")
    assert bal.total_pnl_rate == Decimal("5.00")
    assert bal.estimated_deposit == Decimal("50000000")
    assert len(bal.holdings) == 1
    h = bal.holdings[0]
    assert h.symbol == "005930"     # 'A' prefix 제거
    assert h.name == "삼성전자"
    assert h.qty == 10
    assert h.avg_buy_price == Decimal("260000")
    assert h.cur_price == Decimal("276500")
    assert h.eval_amount == Decimal("2765000")
    assert h.pnl == Decimal("165000")
    assert h.pnl_rate == Decimal("6.35")
    # 헤더 검증
    assert http.post.call_args.kwargs["headers"]["api-id"] == "kt00018"
    assert http.post.call_args.kwargs["json"]["dmst_stex_tp"] == "KRX"


@pytest.mark.asyncio
async def test_fetch_balance_empty_holdings():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, {
        "return_code": 0,
        "tot_pur_amt": "0", "tot_evlt_amt": "0", "tot_evlt_pl": "0",
        "tot_prft_rt": "0", "prsm_dpst_aset_amt": "+50000000",
        "acnt_evlt_remn_indv_tot": [],
    }))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    bal = await f.fetch_balance()
    assert bal.holdings == []
    assert bal.estimated_deposit == Decimal("50000000")


# -- deposit ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_deposit_parses():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, {
        "return_code": 0,
        "entr": "+50000000",
        "profa_ch": "+1000000",
        "bncr_profa_ch": "+500000",
        "nxdy_bncr_sell_exct": "0",
    }))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    d = await f.fetch_deposit()
    assert d.cash == Decimal("50000000")
    assert d.margin_cash == Decimal("1000000")
    assert d.bond_margin_cash == Decimal("500000")
    assert d.next_day_settlement == Decimal("0")
    assert http.post.call_args.kwargs["headers"]["api-id"] == "kt00001"


# -- error -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_balance_error_return_code():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, {"return_code": 2, "return_msg": "오류"}))
    f = KiwoomNativeAccountFetcher(oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0)
    with pytest.raises(RuntimeError, match="rc=2"):
        await f.fetch_balance()
