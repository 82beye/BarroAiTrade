"""cache_quotes / stock_names / ohlcv 라우트 캐시 폴백 테스트."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.market_data import cache_quotes, stock_names


def _write_cache(base, symbol, rows):
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{symbol}.json").write_text(
        json.dumps({"data": rows}, ensure_ascii=False), encoding="utf-8"
    )


_ROWS = [
    {"date": "20260617", "open": 332000, "high": 348000, "low": 331500,
     "close": 340000, "volume": 18000000},
    {"date": "20260618", "open": 345000, "high": 363000, "low": 344500,
     "close": 357000, "volume": 32000000},
]


def test_get_daily_candles(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", str(tmp_path))
    _write_cache(tmp_path, "005930", _ROWS)
    rows = cache_quotes.get_daily_candles("005930", limit=10)
    assert len(rows) == 2
    assert rows[-1]["close"] == 357000
    # 정규화 심볼 (_AL 마커)
    rows2 = cache_quotes.get_daily_candles("005930_AL", limit=1)
    assert len(rows2) == 1


def test_get_quote(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", str(tmp_path))
    _write_cache(tmp_path, "005930", _ROWS)
    q = cache_quotes.get_quote("005930")
    assert q["price"] == 357000
    # change_pct = (357000-340000)/340000*100 = 5.0
    assert q["change_pct"] == 5.0
    assert q["day_high"] == 363000
    assert q["source"] == "cache"
    assert q["as_of"] == "20260618"
    # value_traded 억: 357000*32000000/1e8 = 114240.0
    assert q["value_traded"] == pytest.approx(114240.0, rel=1e-3)


def test_get_quote_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", str(tmp_path))
    assert cache_quotes.get_quote("999999") is None


def test_stock_names_loader(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_DATA_DIR", str(tmp_path))
    (tmp_path / "stock_names.json").write_text(
        json.dumps({"005930": "삼성전자", "000660": "SK하이닉스"}), encoding="utf-8"
    )
    # refined_signals 병합
    (tmp_path / "refined_signals.json").write_text(
        json.dumps({"signals": [{"symbol": "475150", "name": "SK이터닉스"}]}),
        encoding="utf-8",
    )
    stock_names.load_names(force=True)
    assert stock_names.resolve("005930") == "삼성전자"
    assert stock_names.resolve("475150") == "SK이터닉스"
    # 미발견 → 코드 그대로
    assert stock_names.resolve("111111") == "111111"
    assert stock_names.resolve("005930_AL") == "삼성전자"
    stock_names.load_names(force=True)  # 원복(다른 테스트 오염 방지)


def test_stock_names_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_DATA_DIR", str(tmp_path))
    stock_names.load_names(force=True)
    assert stock_names.resolve("005930") == "005930"
    stock_names.load_names(force=True)


@pytest.fixture
def market_client(monkeypatch, tmp_path):
    from backend.api.routes.market import router
    from backend.core.state import app_state

    monkeypatch.setattr(app_state, "market_gateway", None, raising=False)
    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", str(tmp_path))
    _write_cache(tmp_path, "005930", _ROWS)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_ohlcv_route_cache_fallback_200(market_client):
    r = market_client.get("/api/market/ohlcv?symbol=005930&timeframe=1d&limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "cache"
    assert body["as_of"] == "20260618"
    assert len(body["data"]) == 2
    assert body["data"][-1]["close"] == 357000
    assert body["data"][-1]["timestamp"] == "2026-06-18T00:00:00"


def test_ohlcv_route_intraday_no_gateway_503(market_client):
    # 게이트웨이 없고 일봉 아님 → 503(기존 동작 유지)
    r = market_client.get("/api/market/ohlcv?symbol=005930&timeframe=5m")
    assert r.status_code == 503


def test_ticker_route_cache_fallback(market_client, monkeypatch):
    # kiwoom_quotes 비활성(키 없음) 보장
    monkeypatch.delenv("KIWOOM_APP_KEY", raising=False)
    monkeypatch.delenv("KIWOOM_APP_SECRET", raising=False)
    import backend.api.routes.market as m
    m._quotes = None
    m._quotes_tried = False
    r = market_client.get("/api/market/ticker/005930")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "cache"
    assert body["price"] == 357000
    assert body["change_pct"] == 5.0
