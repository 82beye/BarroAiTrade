"""GET /api/market/ticker/{symbol} — 시세 전체 실패 시 name-only 강등 검증.

회귀 배경: 라이브+캐시 시세가 전부 실패하면 이름까지 버리고 404 를 던져
프론트가 종목명 대신 종목코드를 그대로 표시하는 문제가 있었다(2026-07-08).
이름은 로컬 stock_names 마스터로 항상 조회 가능하므로, 시세만 없을 뿐
이름을 아는 경우엔 name-only 로 강등해서 반환해야 한다.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import market as market_module
from backend.api.routes.market import router
from backend.core.state import app_state


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_state, "market_gateway", None)
    monkeypatch.setattr(market_module, "_get_quotes", lambda: None)
    monkeypatch.setattr(
        market_module.cache_quotes, "get_quote", lambda symbol, base_dir=None: None
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestTickerAllSourcesFail:
    def test_known_symbol_degrades_to_name_only(self, client, monkeypatch):
        monkeypatch.setattr(
            market_module.stock_names, "resolve", lambda symbol: "삼성전자"
        )
        res = client.get("/api/market/ticker/005930")
        assert res.status_code == 200
        body = res.json()
        assert body["symbol"] == "005930"
        assert body["name"] == "삼성전자"
        assert body["price"] is None
        assert body["source"] == "name_only"

    def test_unknown_symbol_still_404s(self, client, monkeypatch):
        # resolve() 는 미발견 시 정규화된 코드 그대로 반환(stock_names.py 계약)
        monkeypatch.setattr(
            market_module.stock_names, "resolve", lambda symbol: symbol
        )
        res = client.get("/api/market/ticker/999999")
        assert res.status_code == 404
