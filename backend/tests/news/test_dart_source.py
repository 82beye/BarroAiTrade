"""BAR-57 — DARTSource 검증 (5 cases)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr

from backend.core.news.sources import DARTSource
from backend.models.news import NewsSource


@pytest.fixture
def http_mock() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


class TestDARTSource:
    def test_api_key_must_be_secretstr(self, http_mock):
        with pytest.raises(TypeError, match="SecretStr"):
            DARTSource("plain_key", http_mock)  # type: ignore[arg-type]

    def test_api_key_secretstr_constructs(self, http_mock):
        src = DARTSource(SecretStr("secret-abc"), http_mock)
        assert src.name == NewsSource.DART

    @pytest.mark.asyncio
    async def test_params_dict_used(self, http_mock):
        """crtfc_key 가 params dict 로 전달되는지 (URL 평문 차단)."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            return_value={"status": "000", "list": []}
        )
        resp.raise_for_status = MagicMock()
        http_mock.get = AsyncMock(return_value=resp)

        src = DARTSource(SecretStr("secret-abc"), http_mock)
        await src.fetch()

        # 호출 인자 검증
        call_kwargs = http_mock.get.await_args.kwargs
        assert "params" in call_kwargs
        assert call_kwargs["params"]["crtfc_key"] == "secret-abc"

    @pytest.mark.asyncio
    async def test_401_returns_empty(self, http_mock):
        resp = MagicMock()
        resp.status_code = 401
        resp.raise_for_status = MagicMock()
        http_mock.get = AsyncMock(return_value=resp)

        src = DARTSource(SecretStr("bad-key"), http_mock)
        items = await src.fetch()
        assert items == []

    @pytest.mark.asyncio
    async def test_corp_name_prepended_to_title(self, http_mock):
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            return_value={
                "status": "000",
                "list": [
                    {
                        "rcept_no": "20260507000001",
                        "report_nm": "분기보고서",
                        "corp_name": "삼성전자",
                        "rcept_dt": "20260506",
                    }
                ],
            }
        )
        resp.raise_for_status = MagicMock()
        http_mock.get = AsyncMock(return_value=resp)

        src = DARTSource(SecretStr("secret-abc"), http_mock)
        items = await src.fetch()
        assert len(items) == 1
        assert items[0].title.startswith("[삼성전자]")
        assert items[0].source_id == "20260507000001"

    def test_mask_query_string(self):
        masked = DARTSource._mask(
            "GET https://x.com?crtfc_key=abc123&page=1 failed"
        )
        assert "abc123" not in masked
        assert "***" in masked
