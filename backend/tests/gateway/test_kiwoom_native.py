"""BAR-OPS-10 — Kiwoom 자체 OpenAPI 어댑터 테스트.

검증 (실 mockapi.kiwoom.com 호출에서 fingerprint):
- POST /oauth2/token: body {grant_type, appkey, secretkey} → {return_code, token, expires_dt}
- POST /api/dostk/chart: header api-id={ka10081|ka10080}
  - 응답: stk_dt_pole_chart_qry / stk_min_pole_chart_qry
  - 가격 필드 부호 prefix (`-268500` → 268500 abs 처리)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_candles import (
    KiwoomNativeCandleFetcher,
    _abs_int,
)
from backend.core.gateway.kiwoom_native_oauth import (
    KiwoomNativeOAuth,
    KiwoomNativeToken,
)


def _http_response(status: int, payload: dict) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.json.return_value = payload
    if 400 <= status < 600:
        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=r
        )
    else:
        r.raise_for_status = MagicMock()
    return r


# -- KiwoomNativeOAuth -------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth_secretstr_required():
    with pytest.raises(TypeError, match="SecretStr"):
        KiwoomNativeOAuth(app_key="plain", app_secret=SecretStr("s"))  # type: ignore


@pytest.mark.asyncio
async def test_oauth_https_only():
    with pytest.raises(ValueError, match="https"):
        KiwoomNativeOAuth(
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            base_url="http://insecure.kiwoom.com",
        )


@pytest.mark.asyncio
async def test_oauth_token_issue_success():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(
        return_value=_http_response(
            200,
            {
                "return_code": 0,
                "return_msg": "정상적으로 처리되었습니다",
                "token": "abc.def.ghi",
                "token_type": "bearer",
                "expires_dt": "20260509223018",
            },
        )
    )
    o = KiwoomNativeOAuth(
        app_key=SecretStr("k"),
        app_secret=SecretStr("s"),
        base_url="https://mockapi.kiwoom.com",
        http_client=client,
        use_shared_cache=False,
    )
    t = await o.get_token()
    assert t.access_token.get_secret_value() == "abc.def.ghi"
    assert t.expires_at.year == 2026
    # body schema 검증
    call = client.post.call_args
    body = call.kwargs["json"]
    assert body["grant_type"] == "client_credentials"
    assert body["appkey"] == "k"
    assert body["secretkey"] == "s"


@pytest.mark.asyncio
async def test_oauth_token_cached():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(
        return_value=_http_response(
            200,
            {"return_code": 0, "token": "t", "token_type": "bearer",
             "expires_dt": "20991231235959"},
        )
    )
    o = KiwoomNativeOAuth(
        app_key=SecretStr("k"), app_secret=SecretStr("s"),
        base_url="https://mockapi.kiwoom.com", http_client=client,
        use_shared_cache=False,
    )
    await o.get_token(); await o.get_token()
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_oauth_error_return_code():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(
        return_value=_http_response(
            200, {"return_code": 2, "return_msg": "투자구분 오류"}
        )
    )
    o = KiwoomNativeOAuth(
        app_key=SecretStr("k"), app_secret=SecretStr("s"),
        base_url="https://mockapi.kiwoom.com", http_client=client,
        use_shared_cache=False,
    )
    with pytest.raises(RuntimeError, match="rc=2"):
        await o.get_token()


# -- 공유 토큰 캐시 (BAR-OPS-31) ---------------------------------------------


def _ok_client(token: str = "shared.tok"):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(
        return_value=_http_response(
            200,
            {"return_code": 0, "token": token, "token_type": "bearer",
             "expires_dt": "20991231235959"},
        )
    )
    return client


def _cached_oauth(cache_path, http_client, key="k", secret="s"):
    return KiwoomNativeOAuth(
        app_key=SecretStr(key), app_secret=SecretStr(secret),
        base_url="https://mockapi.kiwoom.com", http_client=http_client,
        use_shared_cache=True, token_cache_path=cache_path,
    )


@pytest.mark.asyncio
async def test_shared_cache_reused_across_instances(tmp_path):
    """프로세스1 발급분을 프로세스2가 채택 — 발급 1회만 발생(폭주 방지)."""
    cache = tmp_path / "tok.json"
    c1 = _ok_client("AAA")
    o1 = _cached_oauth(cache, c1)
    t1 = await o1.get_token()
    assert t1.access_token.get_secret_value() == "AAA"
    assert c1.post.await_count == 1
    assert cache.exists()

    # 별도 인스턴스(다른 프로세스 모사) — 같은 캐시 채택, 발급 호출 0
    c2 = _ok_client("BBB")
    o2 = _cached_oauth(cache, c2)
    t2 = await o2.get_token()
    assert t2.access_token.get_secret_value() == "AAA"   # 캐시 토큰 채택
    assert c2.post.await_count == 0                       # 발급 안 함


@pytest.mark.asyncio
async def test_shared_cache_file_permissions(tmp_path):
    """토큰 캐시 파일은 0600 (CWE-732)."""
    cache = tmp_path / "tok.json"
    await _cached_oauth(cache, _ok_client()).get_token()
    mode = oct(cache.stat().st_mode & 0o777)
    assert mode == "0o600", mode


@pytest.mark.asyncio
async def test_invalidate_forces_reissue_not_cache_readopt(tmp_path):
    """거부된 토큰은 캐시에 남아도 재채택하지 않고 재발급."""
    cache = tmp_path / "tok.json"
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(side_effect=[
        _http_response(200, {"return_code": 0, "token": "OLD",
                             "token_type": "bearer", "expires_dt": "20991231235959"}),
        _http_response(200, {"return_code": 0, "token": "NEW",
                             "token_type": "bearer", "expires_dt": "20991231235959"}),
    ])
    o = _cached_oauth(cache, client)
    t1 = await o.get_token()
    assert t1.access_token.get_secret_value() == "OLD"
    # rc=3 거부 모사
    o.invalidate_token()
    t2 = await o.get_token()
    assert t2.access_token.get_secret_value() == "NEW"   # 캐시 OLD 재채택 X
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_shared_cache_separate_fingerprint(tmp_path):
    """appkey 가 다르면 캐시 항목 분리(자격증명 변경 시 무효화)."""
    cache = tmp_path / "tok.json"
    await _cached_oauth(cache, _ok_client("KEY1TOK"), key="k1").get_token()
    c2 = _ok_client("KEY2TOK")
    t2 = await _cached_oauth(cache, c2, key="k2").get_token()
    assert t2.access_token.get_secret_value() == "KEY2TOK"  # k1 캐시 채택 안 함
    assert c2.post.await_count == 1


# -- _abs_int ---------------------------------------------------------------


def test_abs_int_strips_signs():
    assert _abs_int("-268500") == 268500
    assert _abs_int("+268500") == 268500
    assert _abs_int("268500") == 268500
    assert _abs_int("0") == 0
    assert _abs_int("") == 0


# -- KiwoomNativeCandleFetcher ---------------------------------------------


def _daily_payload():
    return {
        "return_code": 0,
        "return_msg": "정상적으로 처리되었습니다",
        "stk_dt_pole_chart_qry": [
            {"dt": "20260508", "open_pric": "-260000", "high_pric": "-270000",
             "low_pric": "-260000", "cur_prc": "-268500", "trde_qty": "25875880"},
            {"dt": "20260507", "open_pric": "+265000", "high_pric": "+272000",
             "low_pric": "+264000", "cur_prc": "+268000", "trde_qty": "20000000"},
        ],
    }


def _minute_payload():
    return {
        "return_code": 0,
        "stk_min_pole_chart_qry": [
            {"cntr_tm": "20260508153000", "open_pric": "-268500",
             "high_pric": "-268500", "low_pric": "-268500",
             "cur_prc": "-268500", "trde_qty": "1843197"},
            {"cntr_tm": "20260508152900", "open_pric": "-268400",
             "high_pric": "-268500", "low_pric": "-268300",
             "cur_prc": "-268500", "trde_qty": "100000"},
        ],
    }


def _make_fetcher(http: AsyncMock) -> KiwoomNativeCandleFetcher:
    oauth = AsyncMock(spec=KiwoomNativeOAuth)
    oauth.base_url = "https://mockapi.kiwoom.com"
    oauth.get_token = AsyncMock(
        return_value=KiwoomNativeToken(
            access_token=SecretStr("tok"),
            token_type="Bearer",
            expires_at=__import__("datetime").datetime(2099, 1, 1),
        )
    )
    return KiwoomNativeCandleFetcher(oauth=oauth, http_client=http, rate_limit_seconds=0)


@pytest.mark.asyncio
async def test_fetch_daily_parses_and_normalizes_signs():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, _daily_payload()))
    f = _make_fetcher(http)
    out = await f.fetch_daily(symbol="005930", base_dt="20260508")

    assert len(out) == 2
    # 정렬: 오름차순
    assert out[0].timestamp.day == 7
    assert out[1].timestamp.day == 8
    # 부호 정규화
    assert out[1].open == 260000
    assert out[1].close == 268500
    # 헤더 검증
    headers = http.post.call_args.kwargs["headers"]
    assert headers["api-id"] == "ka10081"
    assert headers["authorization"] == "Bearer tok"
    body = http.post.call_args.kwargs["json"]
    assert body == {"stk_cd": "005930", "base_dt": "20260508", "upd_stkpc_tp": "1"}


@pytest.mark.asyncio
async def test_fetch_minute_parses_cntr_tm():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(return_value=_http_response(200, _minute_payload()))
    f = _make_fetcher(http)
    out = await f.fetch_minute(symbol="005930", tic_scope="1")

    assert len(out) == 2
    assert out[0].timestamp.minute == 29
    assert out[1].timestamp.minute == 30
    headers = http.post.call_args.kwargs["headers"]
    assert headers["api-id"] == "ka10080"
    body = http.post.call_args.kwargs["json"]
    assert body["tic_scope"] == "1"


@pytest.mark.asyncio
async def test_fetch_minute_invalid_tic_scope():
    f = _make_fetcher(AsyncMock(spec=httpx.AsyncClient))
    with pytest.raises(ValueError, match="invalid tic_scope"):
        await f.fetch_minute(symbol="005930", tic_scope="2")


@pytest.mark.asyncio
async def test_fetch_error_return_code():
    http = AsyncMock(spec=httpx.AsyncClient)
    http.post = AsyncMock(
        return_value=_http_response(200, {"return_code": 1, "return_msg": "오류"})
    )
    f = _make_fetcher(http)
    with pytest.raises(RuntimeError, match="rc=1"):
        await f.fetch_daily(symbol="005930", base_dt="20260508")
