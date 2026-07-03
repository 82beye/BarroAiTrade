"""알림내역 / Push 설정 API + event_log 테스트 (티마 앱 벤치마킹 P1)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import alerts as alerts_module
from backend.api.routes.alerts import router
from backend.core.alerts import event_log


# ── event_log 순수 유틸 ───────────────────────────────────────────────────────


class TestEventLog:
    def _patch_path(self, monkeypatch, tmp_path):
        p = tmp_path / "alert_events.jsonl"
        monkeypatch.setattr(event_log, "_events_path", lambda: p)
        return p

    def test_read_missing_file_returns_empty(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        assert event_log.read_alert_events() == []

    def test_append_then_read_roundtrip(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        ev = event_log.append_alert_event(
            strategy="sf_zone",
            symbol="112040",
            name="위메이드",
            message="[SF존] SF존 위메이드 B1도달",
            level_label="B1",
            occurred_at="2026-07-02T17:59:00+09:00",
        )
        assert ev["occurred_at"] == "2026-07-02T17:59:00+09:00"
        events = event_log.read_alert_events()
        assert len(events) == 1
        assert events[0]["symbol"] == "112040"
        assert events[0]["level_label"] == "B1"
        assert events[0]["id"] == 1

    def test_append_default_occurred_at(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        ev = event_log.append_alert_event(strategy="f_zone", symbol="005930")
        assert ev["occurred_at"]  # 자동 채움
        assert ev["name"] == "005930"  # name 없으면 symbol

    def test_read_filters_by_strategy(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        event_log.append_alert_event(strategy="sf_zone", symbol="A", occurred_at="2026-07-02T10:00:00+09:00")
        event_log.append_alert_event(strategy="gold_zone", symbol="B", occurred_at="2026-07-02T11:00:00+09:00")
        sf = event_log.read_alert_events(strategy="sf_zone")
        assert [e["symbol"] for e in sf] == ["A"]

    def test_read_newest_first_and_limit(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        for i in range(5):
            event_log.append_alert_event(
                strategy="f_zone", symbol=f"S{i}",
                occurred_at=f"2026-07-02T1{i}:00:00+09:00",
            )
        events = event_log.read_alert_events(limit=3)
        assert len(events) == 3
        # 최신순 (14:00 > 13:00 > 12:00)
        assert [e["symbol"] for e in events] == ["S4", "S3", "S2"]

    def test_read_since_filter(self, monkeypatch, tmp_path):
        self._patch_path(monkeypatch, tmp_path)
        event_log.append_alert_event(strategy="f_zone", symbol="OLD", occurred_at="2026-07-02T10:00:00+09:00")
        event_log.append_alert_event(strategy="f_zone", symbol="NEW", occurred_at="2026-07-02T12:00:00+09:00")
        got = event_log.read_alert_events(since="2026-07-02T11:00:00+09:00")
        assert [e["symbol"] for e in got] == ["NEW"]


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch, tmp_path):
    events = tmp_path / "alert_events.jsonl"
    settings = tmp_path / "alert_settings.json"
    monkeypatch.setattr(event_log, "_events_path", lambda: events)
    monkeypatch.setattr(alerts_module, "_settings_path", lambda: settings)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestAlertHistory:
    def test_no_data_when_empty(self, client):
        r = client.get("/api/alerts/history")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "no_data"
        assert data["count"] == 0
        assert data["items"] == []

    def test_history_returns_events_newest_first(self, client, monkeypatch, tmp_path):
        event_log.append_alert_event(strategy="sf_zone", symbol="A", message="a", occurred_at="2026-07-02T10:00:00+09:00")
        event_log.append_alert_event(strategy="gold_zone", symbol="B", message="b", occurred_at="2026-07-02T11:00:00+09:00")
        r = client.get("/api/alerts/history")
        data = r.json()
        assert data["status"] == "ok"
        assert data["count"] == 2
        assert data["items"][0]["symbol"] == "B"  # 최신순

    def test_history_strategy_filter(self, client):
        event_log.append_alert_event(strategy="sf_zone", symbol="A", occurred_at="2026-07-02T10:00:00+09:00")
        event_log.append_alert_event(strategy="gold_zone", symbol="B", occurred_at="2026-07-02T11:00:00+09:00")
        r = client.get("/api/alerts/history?strategy=sf_zone")
        data = r.json()
        assert data["count"] == 1
        assert data["items"][0]["symbol"] == "A"

    def test_history_limit(self, client):
        for i in range(10):
            event_log.append_alert_event(strategy="f_zone", symbol=f"S{i}", occurred_at=f"2026-07-02T{i:02d}:00:00+09:00")
        r = client.get("/api/alerts/history?limit=3")
        assert r.json()["count"] == 3


class TestAlertSettings:
    def test_default_all_on(self, client):
        r = client.get("/api/alerts/settings")
        assert r.status_code == 200
        assert r.json() == {"f_zone": True, "sf_zone": True, "gold_zone": True, "swing_38": True}

    def test_partial_update_roundtrip(self, client):
        r = client.put("/api/alerts/settings", json={"sf_zone": False})
        assert r.status_code == 200
        body = r.json()
        assert body["sf_zone"] is False
        # 나머지는 유지
        assert body["f_zone"] is True and body["gold_zone"] is True
        # 재조회로 영속 확인
        r2 = client.get("/api/alerts/settings")
        assert r2.json()["sf_zone"] is False

    def test_update_multiple_and_none_ignored(self, client):
        client.put("/api/alerts/settings", json={"f_zone": False, "swing_38": False})
        # None (미지정) 필드는 기존값 유지
        r = client.put("/api/alerts/settings", json={"gold_zone": False})
        body = r.json()
        assert body["f_zone"] is False  # 이전 업데이트 유지
        assert body["swing_38"] is False
        assert body["gold_zone"] is False
        assert body["sf_zone"] is True
