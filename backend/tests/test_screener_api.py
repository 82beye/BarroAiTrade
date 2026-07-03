"""전략 스크리너 / 기준가 사다리 API 테스트 (티마 앱 벤치마킹 P0)."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import screener as screener_module
from backend.api.routes.screener import router
from backend.api.schemas.screener import DISCLAIMER
from backend.core.strategy.reference_levels import compute_levels


# ── reference_levels 단위 테스트 ──────────────────────────────────────────────

class TestReferenceLevelsFallback:
    """캔들 없는 폴백 경로 (신호가 배율)."""

    def test_f_zone_set_labels_and_order(self):
        levels = compute_levels("f_zone", signal_price=10000, current_price=10000)
        assert [lv["label"] for lv in levels] == ["SF", "B1", "B2", "B3"]
        # SF anchor, B* support
        assert levels[0]["kind"] == "anchor"
        assert all(lv["kind"] == "support" for lv in levels[1:])
        # 가격 서열 SF > B1 > B2 > B3
        prices = [lv["price"] for lv in levels]
        assert prices == sorted(prices, reverse=True)
        assert len(set(prices)) == 4  # 엄격 내림차순 (중복 없음)

    def test_sf_zone_same_shape_as_f_zone(self):
        levels = compute_levels("sf_zone", signal_price=10000)
        assert [lv["label"] for lv in levels] == ["SF", "B1", "B2", "B3"]

    def test_f_zone_active_is_highest_support_below_current(self):
        # 현재가 10000 → B1(9900) 이 현재가 아래 최상단
        levels = compute_levels("f_zone", signal_price=10000, current_price=10000)
        active = [lv for lv in levels if lv["active"]]
        assert len(active) == 1
        assert active[0]["label"] == "B1"

    def test_gold_zone_targets(self):
        levels = compute_levels("gold_zone", signal_price=10000, current_price=10000)
        assert [lv["label"] for lv in levels] == ["G1", "G2", "G3"]
        assert all(lv["kind"] == "target" for lv in levels)
        prices = [lv["price"] for lv in levels]
        assert prices == [10300, 10500, 10800]
        # 현재가 위 최하단 = G1 active
        active = [lv for lv in levels if lv["active"]]
        assert len(active) == 1 and active[0]["label"] == "G1"

    def test_swing_38_targets(self):
        levels = compute_levels("swing_38", signal_price=10000, current_price=10000)
        assert [lv["label"] for lv in levels] == ["J1", "J2", "J3"]
        prices = [lv["price"] for lv in levels]
        assert prices == [10500, 11000, 11500]
        assert prices == sorted(prices)

    def test_unsupported_strategy_returns_empty(self):
        assert compute_levels("blue_line", signal_price=10000) == []

    def test_invalid_price_returns_empty(self):
        assert compute_levels("f_zone", signal_price=0) == []

    def test_gold_zone_active_when_current_above_g1(self):
        # 현재가가 G1(10300) 위, G2(10500) 아래 → G2 active
        levels = compute_levels("gold_zone", signal_price=10000, current_price=10400)
        active = [lv for lv in levels if lv["active"]]
        assert len(active) == 1 and active[0]["label"] == "G2"


class TestReferenceLevelsWithCandles:
    """캔들 있는 MA 경로."""

    def _candles(self, closes):
        return [{"close": c} for c in closes]

    def test_f_zone_uses_moving_averages(self):
        # 상승 추세 종가 → MA5 > MA10 > MA20, 모두 신호가(마지막 종가)보다 낮음
        closes = [float(9000 + i * 20) for i in range(30)]  # 9000..9580
        signal_price = closes[-1]
        levels = compute_levels(
            "f_zone", signal_price=signal_price, current_price=signal_price,
            candles=self._candles(closes),
        )
        prices = [lv["price"] for lv in levels]
        # SF > B1 > B2 > B3 서열 유지
        assert prices == sorted(prices, reverse=True)
        # B1(MA5) 은 폴백 배율(0.99)과 다른 실제 MA 값이어야 함
        fallback = compute_levels("f_zone", signal_price=signal_price)
        assert prices[1] != fallback[1]["price"]

    def test_f_zone_partial_candles_falls_back_per_band(self):
        # 캔들 7개 → MA5 가능, MA10/MA20 폴백. 서열은 여전히 강제.
        closes = [float(9000 + i * 10) for i in range(7)]
        signal_price = closes[-1]
        levels = compute_levels(
            "f_zone", signal_price=signal_price,
            candles=self._candles(closes),
        )
        prices = [lv["price"] for lv in levels]
        assert prices == sorted(prices, reverse=True)
        assert len(set(prices)) == 4


# ── 스크리너 API 테스트 ───────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    # refined_signals.json 경로를 tmp 로 격리 (dev 머신 실제 파일 무관하게)
    refined = tmp_path / "refined_signals.json"
    monkeypatch.setattr(screener_module, "_refined_path", lambda: refined)
    # gateway 미초기화 상태 보장 (온디맨드/보강 경로 no-op)
    from backend.core.state import app_state
    monkeypatch.setattr(app_state, "market_gateway", None, raising=False)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app), refined


class TestScreenerStrategies:
    def test_list_tabs(self, client):
        c, _ = client
        r = c.get("/api/screener/strategies")
        assert r.status_code == 200
        data = r.json()
        assert [t["key"] for t in data] == ["f_zone", "sf_zone", "gold_zone", "swing_38"]


class TestScreenerStrategy:
    def test_no_data_when_file_missing(self, client):
        c, _ = client
        r = c.get("/api/screener/f_zone")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "no_data"
        assert data["count"] == 0
        assert data["items"] == []
        assert data["disclaimer"] == DISCLAIMER

    def test_invalid_strategy_404(self, client):
        c, _ = client
        r = c.get("/api/screener/not_a_strategy")
        assert r.status_code == 404

    def test_filters_by_signal_type_and_attaches_levels(self, client):
        c, refined = client
        refined.write_text(
            json.dumps(
                {
                    "signals": [
                        {
                            "symbol": "005930", "name": "삼성전자", "price": 70000,
                            "signal_type": "f_zone", "score": 8.0, "reason": "test f",
                            "timestamp": "2026-07-04T10:00:00+09:00",
                        },
                        {
                            "symbol": "035720", "name": "카카오", "price": 50000,
                            "signal_type": "gold_zone", "score": 6.0, "reason": "test g",
                            "timestamp": "2026-07-04T10:01:00+09:00",
                        },
                    ],
                    "regime": "sideways",
                    "timestamp": "2026-07-04T10:02:00+09:00",
                }
            ),
            encoding="utf-8",
        )
        r = c.get("/api/screener/f_zone")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["count"] == 1
        item = data["items"][0]
        assert item["symbol"] == "005930"
        assert item["detected_at"] == "2026-07-04T10:00:00+09:00"
        # 기준가 사다리 부착 확인
        assert [lv["label"] for lv in item["levels"]] == ["SF", "B1", "B2", "B3"]
        assert item["levels"][0]["price"] == 70000
        # gateway 없음 → change_pct/value_traded None
        assert item["change_pct"] is None
        assert item["value_traded"] is None

    def test_gold_zone_filters_out_others(self, client):
        c, refined = client
        refined.write_text(
            json.dumps(
                {
                    "signals": [
                        {
                            "symbol": "035720", "name": "카카오", "price": 50000,
                            "signal_type": "gold_zone", "score": 6.0, "reason": "g",
                            "timestamp": "2026-07-04T10:00:00+09:00",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        r = c.get("/api/screener/gold_zone")
        data = r.json()
        assert data["count"] == 1
        assert [lv["label"] for lv in data["items"][0]["levels"]] == ["G1", "G2", "G3"]


class TestChartLevels:
    def test_no_signal_returns_empty(self, client):
        c, _ = client
        r = c.get("/api/chart/levels?symbol=005930")
        assert r.status_code == 200
        data = r.json()
        assert data["symbol"] == "005930"
        assert data["strategy"] is None
        assert data["levels"] == []
        assert data["disclaimer"] == DISCLAIMER

    def test_attaches_levels_from_refined(self, client):
        c, refined = client
        refined.write_text(
            json.dumps(
                {
                    "signals": [
                        {
                            "symbol": "005930", "name": "삼성전자", "price": 70000,
                            "signal_type": "swing_38", "score": 7.0, "reason": "sw",
                            "timestamp": "2026-07-04T10:00:00+09:00",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        r = c.get("/api/chart/levels?symbol=005930")
        data = r.json()
        assert data["strategy"] == "swing_38"
        assert [lv["label"] for lv in data["levels"]] == ["J1", "J2", "J3"]


# ── ThemeStockOut 하위호환 (gateway 없음 경로) ────────────────────────────────

class TestThemeStockBackwardCompat:
    def test_optional_quote_fields_default_none(self):
        from backend.api.schemas.theme import ThemeStockOut

        stock = ThemeStockOut(symbol="005930", score=0.9, theme_id=1)
        assert stock.price is None
        assert stock.change_pct is None
        assert stock.value_traded is None
        assert stock.name is None

    @pytest.mark.asyncio
    async def test_enrich_is_noop_without_gateway(self, monkeypatch):
        from backend.api.routes import themes_calendar_news as tcn
        from backend.api.schemas.theme import ThemeStockOut
        from backend.core.state import app_state

        monkeypatch.setattr(app_state, "market_gateway", None, raising=False)
        stocks = [ThemeStockOut(symbol="005930", score=0.9, theme_id=1)]
        await tcn._enrich_theme_stocks(stocks)
        assert stocks[0].price is None
