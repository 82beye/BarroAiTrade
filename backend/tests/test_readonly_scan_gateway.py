"""ReadOnlyScanGateway 단위 테스트 — 스크리너 온디맨드 스캔의 읽기 전용 폴백."""
from __future__ import annotations

import json

import pytest

from backend.core.gateway.readonly_scan_gateway import ReadOnlyScanGateway


@pytest.fixture
def gw():
    return ReadOnlyScanGateway()


class TestReadOnlyRefusal:
    """주문·계좌 경로는 어떤 방식으로도 호출 불가해야 한다 (§2 안전 경계)."""

    @pytest.mark.asyncio
    async def test_get_balance_refused(self, gw):
        with pytest.raises(NotImplementedError):
            await gw.get_balance()

    @pytest.mark.asyncio
    async def test_place_order_refused(self, gw):
        with pytest.raises(NotImplementedError):
            await gw.place_order(object())

    @pytest.mark.asyncio
    async def test_cancel_order_refused(self, gw):
        with pytest.raises(NotImplementedError):
            await gw.cancel_order("x")

    @pytest.mark.asyncio
    async def test_get_order_status_refused(self, gw):
        with pytest.raises(NotImplementedError):
            await gw.get_order_status("x")


class TestGetOhlcvCacheFallback:
    @pytest.mark.asyncio
    async def test_no_key_uses_cache(self, gw, tmp_path, monkeypatch):
        monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
        monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
        cache_dir = tmp_path / "ohlcv_cache"
        cache_dir.mkdir()
        (cache_dir / "005930.json").write_text(
            json.dumps({"data": [
                {"date": "20260701", "open": 100, "high": 110, "low": 95, "close": 105, "volume": 1000},
                {"date": "20260702", "open": 105, "high": 115, "low": 100, "close": 112, "volume": 1200},
            ]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "backend.core.market_data.cache_quotes.cache_dir", lambda: cache_dir
        )
        candles = await gw.get_ohlcv("005930", "1d", limit=10)
        assert len(candles) == 2
        assert candles[-1].close == 112

    @pytest.mark.asyncio
    async def test_no_key_no_cache_returns_empty(self, gw, tmp_path, monkeypatch):
        monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
        monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.setattr(
            "backend.core.market_data.cache_quotes.cache_dir", lambda: empty_dir
        )
        candles = await gw.get_ohlcv("999999", "1d", limit=10)
        assert candles == []


class TestGetTickerCacheFallback:
    @pytest.mark.asyncio
    async def test_no_key_uses_cache_quote(self, gw, tmp_path, monkeypatch):
        monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
        monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
        cache_dir = tmp_path / "ohlcv_cache"
        cache_dir.mkdir()
        (cache_dir / "005930.json").write_text(
            json.dumps({"data": [
                {"date": "20260701", "open": 100, "high": 110, "low": 95, "close": 100, "volume": 1000},
                {"date": "20260702", "open": 105, "high": 115, "low": 100, "close": 112, "volume": 1200},
            ]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "backend.core.market_data.cache_quotes.cache_dir", lambda: cache_dir
        )
        ticker = await gw.get_ticker("005930")
        assert ticker.price == 112
        assert ticker.symbol == "005930"

    @pytest.mark.asyncio
    async def test_no_data_raises(self, gw, tmp_path, monkeypatch):
        monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
        monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.setattr(
            "backend.core.market_data.cache_quotes.cache_dir", lambda: empty_dir
        )
        with pytest.raises(ValueError):
            await gw.get_ticker("999999")


class TestUniverse:
    @pytest.mark.asyncio
    async def test_loads_theme_map_symbols(self, gw, monkeypatch):
        from backend.core.risk import theme_map as theme_map_module

        monkeypatch.setattr(
            theme_map_module, "load_theme_map",
            lambda path: {"005930": ["반도체"], "000660": ["반도체", "HBM"]},
        )
        universe = await gw.get_universe()
        assert universe == ["000660", "005930"]

    @pytest.mark.asyncio
    async def test_missing_file_returns_empty(self, gw, monkeypatch):
        from backend.core.risk import theme_map as theme_map_module

        monkeypatch.setattr(
            theme_map_module, "load_theme_map", lambda path: {}
        )
        universe = await gw.get_universe()
        assert universe == []


class TestGetPrices:
    @pytest.mark.asyncio
    async def test_partial_failure_skips_symbol(self, gw, tmp_path, monkeypatch):
        monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
        monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
        cache_dir = tmp_path / "ohlcv_cache"
        cache_dir.mkdir()
        (cache_dir / "005930.json").write_text(
            json.dumps({"data": [{"date": "20260701", "open": 1, "high": 1, "low": 1, "close": 100, "volume": 1}]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "backend.core.market_data.cache_quotes.cache_dir", lambda: cache_dir
        )
        prices = await gw.get_prices(["005930", "999999"])
        assert prices == {"005930": 100.0}


class TestMarketStatus:
    def test_is_market_open_returns_bool(self, gw):
        assert isinstance(gw.is_market_open(), bool)

    @pytest.mark.asyncio
    async def test_health_check_true_with_cache(self, gw, tmp_path, monkeypatch):
        monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
        monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
        monkeypatch.setattr(
            "backend.core.market_data.cache_quotes.cache_dir", lambda: tmp_path
        )
        assert await gw.health_check() is True
