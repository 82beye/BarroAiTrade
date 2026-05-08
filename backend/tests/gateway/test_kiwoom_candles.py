"""BAR-OPS-09 — KiwoomCandleFetcher (12 cases)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_candles import KiwoomCandleFetcher
from backend.core.gateway.kiwoom_oauth import KiwoomOAuth2Manager, TokenInfo


@pytest.fixture
def oauth_mock():
    m = AsyncMock(spec=KiwoomOAuth2Manager)
    m._base_url = "https://openapi.koreainvestment.com:9443"
    m.get_token = AsyncMock(
        return_value=TokenInfo(
            access_token=SecretStr("test-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
        )
    )
    return m


def _daily_response():
    return {
        "rt_cd": "0",
        "msg1": "정상처리",
        "output2": [
            {
                "stck_bsop_date": "20260507",
                "stck_oprc": "70000",
                "stck_hgpr": "70500",
                "stck_lwpr": "69800",
                "stck_clpr": "70300",
                "acml_vol": "12345678",
            },
            {
                "stck_bsop_date": "20260506",
                "stck_oprc": "69500",
                "stck_hgpr": "70100",
                "stck_lwpr": "69300",
                "stck_clpr": "69900",
                "acml_vol": "9876543",
            },
        ],
    }


def _minute_response():
    return {
        "rt_cd": "0",
        "msg1": "정상처리",
        "output2": [
            {
                "stck_bsop_date": "20260508",
                "stck_cntg_hour": "090000",
                "stck_oprc": "70100",
                "stck_hgpr": "70200",
                "stck_lwpr": "70000",
                "stck_prpr": "70150",
                "cntg_vol": "10000",
            },
            {
                "stck_bsop_date": "20260508",
                "stck_cntg_hour": "090100",
                "stck_oprc": "70150",
                "stck_hgpr": "70300",
                "stck_lwpr": "70100",
                "stck_prpr": "70250",
                "cntg_vol": "12000",
            },
        ],
    }


class TestInit:
    def test_credentials_must_be_secretstr(self, oauth_mock):
        with pytest.raises(TypeError):
            KiwoomCandleFetcher(oauth_mock, "plain", SecretStr("s"))  # type: ignore[arg-type]


class TestFetchDaily:
    @pytest.mark.asyncio
    async def test_daily_parses_correctly(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value=_daily_response())
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0,
        )
        candles = await f.fetch_daily(
            symbol="005930", start_date="20260101", end_date="20260507"
        )
        assert len(candles) == 2
        assert candles[0].symbol == "005930"
        assert candles[0].close == 70300
        assert candles[0].timestamp == datetime(2026, 5, 7)

    @pytest.mark.asyncio
    async def test_daily_uses_correct_tr_id(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value={"rt_cd": "0", "output2": []})
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0,
        )
        await f.fetch_daily("005930", "20260101", "20260507")
        kwargs = client.get.await_args.kwargs
        assert kwargs["headers"]["tr_id"] == "FHKST03010100"

    @pytest.mark.asyncio
    async def test_error_response_raises(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(
            return_value={"rt_cd": "1", "msg1": "조회 실패"}
        )
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0,
        )
        with pytest.raises(RuntimeError, match="조회 실패"):
            await f.fetch_daily("005930", "20260101", "20260507")


class TestFetchMinute:
    @pytest.mark.asyncio
    async def test_minute_parses_correctly(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value=_minute_response())
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0,
        )
        candles = await f.fetch_minute("005930")
        assert len(candles) == 2
        assert candles[0].timestamp == datetime(2026, 5, 8, 9, 0)
        assert candles[1].timestamp == datetime(2026, 5, 8, 9, 1)

    @pytest.mark.asyncio
    async def test_minute_invalid_unit(self, oauth_mock):
        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
        )
        with pytest.raises(ValueError, match="time_unit"):
            await f.fetch_minute("005930", time_unit="2")

    @pytest.mark.asyncio
    async def test_minute_uses_correct_tr_id(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value={"rt_cd": "0", "output2": []})
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0,
        )
        await f.fetch_minute("005930")
        kwargs = client.get.await_args.kwargs
        assert kwargs["headers"]["tr_id"] == "FHKST03010200"


class TestHeaders:
    @pytest.mark.asyncio
    async def test_headers_include_bearer_and_appkey(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value={"rt_cd": "0", "output2": []})
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("my-key"),
            app_secret=SecretStr("my-secret"),
            http_client=client,
            rate_limit_seconds=0,
        )
        await f.fetch_daily("005930", "20260101", "20260507")
        headers = client.get.await_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-token"
        assert headers["appkey"] == "my-key"
        assert headers["appsecret"] == "my-secret"


class TestParams:
    @pytest.mark.asyncio
    async def test_daily_params(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value={"rt_cd": "0", "output2": []})
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0,
        )
        await f.fetch_daily("005930", "20260101", "20260507", period="D")
        params = client.get.await_args.kwargs["params"]
        assert params["FID_INPUT_ISCD"] == "005930"
        assert params["FID_INPUT_DATE_1"] == "20260101"
        assert params["FID_INPUT_DATE_2"] == "20260507"
        assert params["FID_PERIOD_DIV_CODE"] == "D"


class TestRateLimit:
    @pytest.mark.asyncio
    async def test_rate_limit_sleep(self, oauth_mock):
        resp = MagicMock()
        resp.json = MagicMock(return_value={"rt_cd": "0", "output2": []})
        resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=resp)

        f = KiwoomCandleFetcher(
            oauth=oauth_mock,
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
            rate_limit_seconds=0.05,
        )
        import time
        t0 = time.perf_counter()
        await f.fetch_daily("005930", "20260101", "20260507")
        elapsed = time.perf_counter() - t0
        assert elapsed >= 0.05
