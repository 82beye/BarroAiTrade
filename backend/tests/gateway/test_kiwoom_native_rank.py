"""BAR-OPS-11 — KiwoomNativeLeaderPicker 테스트."""
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
from backend.core.gateway.kiwoom_native_rank import (
    KiwoomNativeLeaderPicker,
    _normalize_symbol,
    _parse_signed_pct,
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


def test_normalize_symbol_strips_market_suffix():
    assert _normalize_symbol("005930_AL") == "005930"
    assert _normalize_symbol("319400_NX") == "319400"
    assert _normalize_symbol("012330") == "012330"


def test_parse_signed_pct():
    assert _parse_signed_pct("+1.84") == 1.84
    assert _parse_signed_pct("-2.00") == -2.0
    assert _parse_signed_pct("0.00") == 0.0
    assert _parse_signed_pct("") == 0.0
    assert _parse_signed_pct("not-a-num") == 0.0


# -- weight validation ------------------------------------------------------


@pytest.mark.asyncio
async def test_picker_weight_validation():
    o = _oauth_mock()
    with pytest.raises(ValueError, match="weights must sum to 1.0"):
        KiwoomNativeLeaderPicker(oauth=o, weight_trade_value=0.7, weight_flu_rate=0.2)


# -- pick logic -------------------------------------------------------------


def _trading_value_payload():
    return {
        "return_code": 0,
        "trde_prica_upper": [
            {"stk_cd": "005930_AL", "stk_nm": "삼성전자", "cur_prc": "+276500", "flu_rt": "+1.84"},
            {"stk_cd": "319400_AL", "stk_nm": "현대무벡스", "cur_prc": "+37700", "flu_rt": "+21.61"},
            {"stk_cd": "307950_AL", "stk_nm": "현대오토에버", "cur_prc": "+592000", "flu_rt": "+29.97"},
            {"stk_cd": "000660_AL", "stk_nm": "SK하이닉스", "cur_prc": "+205000", "flu_rt": "+0.50"},
        ],
    }


def _fluct_payload():
    return {
        "return_code": 0,
        "pred_pre_flu_rt_upper": [
            {"stk_cd": "307950_AL", "stk_nm": "현대오토에버", "cur_prc": "+592000", "flu_rt": "+29.97"},
            {"stk_cd": "319400_AL", "stk_nm": "현대무벡스", "cur_prc": "+37700", "flu_rt": "+21.61"},
            {"stk_cd": "005930_AL", "stk_nm": "삼성전자", "cur_prc": "+276500", "flu_rt": "+1.84"},
            {"stk_cd": "000660_AL", "stk_nm": "SK하이닉스", "cur_prc": "+205000", "flu_rt": "+0.50"},
        ],
    }


@pytest.mark.asyncio
async def test_pick_combines_rankings_and_filters_low_flu():
    """ka10032 + ka10027 결합. 등락률 < 1% 필터링."""
    http = AsyncMock(spec=httpx.AsyncClient)

    async def post_side(url, headers, json):
        api_id = headers["api-id"]
        if api_id == "ka10032":
            return _http_response(200, _trading_value_payload())
        if api_id == "ka10027":
            return _http_response(200, _fluct_payload())
        raise AssertionError(f"unexpected api-id {api_id}")

    http.post = AsyncMock(side_effect=post_side)
    picker = KiwoomNativeLeaderPicker(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    leaders = await picker.pick(top_n=5)

    syms = [c.symbol for c in leaders]
    # SK하이닉스 (+0.50%) 는 min_flu_rate=1.0 필터로 제외
    assert "000660" not in syms
    # 점수 계산 (n=4, 가중 0.6/0.4):
    #  005930 TV1·FR3 → 0.6×1.00 + 0.4×0.50 = 0.80
    #  319400 TV2·FR2 → 0.6×0.75 + 0.4×0.75 = 0.75
    #  307950 TV3·FR1 → 0.6×0.50 + 0.4×1.00 = 0.70
    # → 거래대금 가중이 높아서 005930 (TV 1위) 이 1위.
    assert leaders[0].symbol == "005930"
    assert abs(leaders[0].score - 0.80) < 1e-6
    # 종목코드 정규화 (suffix 제거)
    for c in leaders:
        assert "_" not in c.symbol


@pytest.mark.asyncio
async def test_pick_score_is_descending():
    http = AsyncMock(spec=httpx.AsyncClient)

    async def post_side(url, headers, json):
        api_id = headers["api-id"]
        if api_id == "ka10032":
            return _http_response(200, _trading_value_payload())
        return _http_response(200, _fluct_payload())

    http.post = AsyncMock(side_effect=post_side)
    picker = KiwoomNativeLeaderPicker(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    leaders = await picker.pick(top_n=10)
    scores = [c.score for c in leaders]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_pick_top_n_caps_results():
    http = AsyncMock(spec=httpx.AsyncClient)

    async def post_side(url, headers, json):
        api_id = headers["api-id"]
        if api_id == "ka10032":
            return _http_response(200, _trading_value_payload())
        return _http_response(200, _fluct_payload())

    http.post = AsyncMock(side_effect=post_side)
    picker = KiwoomNativeLeaderPicker(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    leaders = await picker.pick(top_n=2)
    assert len(leaders) == 2


@pytest.mark.asyncio
async def test_pick_high_min_flu_returns_empty():
    http = AsyncMock(spec=httpx.AsyncClient)

    async def post_side(url, headers, json):
        api_id = headers["api-id"]
        if api_id == "ka10032":
            return _http_response(200, _trading_value_payload())
        return _http_response(200, _fluct_payload())

    http.post = AsyncMock(side_effect=post_side)
    picker = KiwoomNativeLeaderPicker(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
        min_flu_rate=50.0,                 # 50% 이상 → 통과 종목 0
    )
    leaders = await picker.pick(top_n=5)
    assert leaders == []


@pytest.mark.asyncio
async def test_pick_error_return_code():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(
        return_value=_http_response(200, {"return_code": 1, "return_msg": "오류"})
    )
    picker = KiwoomNativeLeaderPicker(
        oauth=_oauth_mock(), http_client=http, rate_limit_seconds=0,
    )
    with pytest.raises(RuntimeError, match="rc=1"):
        await picker.pick(top_n=5)
