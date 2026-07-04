"""티마 P1 운영 배선 테스트 — 테마 스냅숏 잡 등록 + 알림 포착 이벤트 기록.

- register_theme_snapshot_jobs: 플래그 OFF 시 미등록 / ON 시 3개 cron 잡 등록(스케줄러 mock).
- record_signal_capture_events: 정제 시그널 → alert_events.jsonl append(tmp_path).
- 데몬/모듈 임포트 무결성.
"""
from __future__ import annotations

import importlib

import pytest

from backend.core.scheduler import theme_snapshot_jobs as tsj


class _FakeScheduler:
    """add_job 호출을 기록만 하는 스케줄러 대역."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


# ── 1. 테마 스냅숏 잡 등록 ────────────────────────────────────────────────────

def test_register_snapshot_jobs_disabled_registers_nothing():
    sched = _FakeScheduler()
    ids = tsj.register_theme_snapshot_jobs(sched, enabled=False)
    assert ids == []
    assert sched.jobs == []


def test_register_snapshot_jobs_enabled_registers_three_slots():
    sched = _FakeScheduler()
    ids = tsj.register_theme_snapshot_jobs(sched, enabled=True)

    assert len(ids) == 3
    assert len(sched.jobs) == 3
    # slot ↔ id 매핑 확인
    assert set(ids) == {"theme_snapshot_1000", "theme_snapshot_1230", "theme_snapshot_1535"}
    # 각 잡은 slot 을 args 로 전달
    passed_slots = {j["kwargs"]["args"][0] for j in sched.jobs}
    assert passed_slots == set(tsj.SLOT_CRON)


def test_register_snapshot_jobs_flag_env_default_off(monkeypatch):
    monkeypatch.delenv("BARRO_THEME_SNAPSHOT_ENABLED", raising=False)
    sched = _FakeScheduler()
    assert tsj.register_theme_snapshot_jobs(sched) == []


def test_register_snapshot_jobs_flag_env_on(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_SNAPSHOT_ENABLED", "1")
    sched = _FakeScheduler()
    assert len(tsj.register_theme_snapshot_jobs(sched)) == 3


# ── 2. 알림 포착 이벤트 기록 ──────────────────────────────────────────────────

@pytest.fixture()
def event_log_tmp(tmp_path, monkeypatch):
    """event_log 의 data 디렉토리를 tmp_path 로 격리."""
    from backend.core.alerts import event_log

    monkeypatch.setattr(event_log, "_data_dir", lambda: tmp_path)
    return event_log


def test_record_signal_capture_events_appends(event_log_tmp):
    signals = [
        {"strategy": "sf_zone", "symbol": "112040", "name": "위메이드"},
        {"strategy": "f_zone", "symbol": "005930", "name": "삼성전자"},
    ]
    written = event_log_tmp.record_signal_capture_events(
        signals, occurred_at="2026-07-04T10:00:00+09:00"
    )
    assert len(written) == 2

    events = event_log_tmp.read_alert_events()
    assert len(events) == 2
    by_symbol = {e["symbol"]: e for e in events}
    assert by_symbol["112040"]["message"] == "[SF존] 위메이드 포착"
    assert by_symbol["112040"]["strategy"] == "sf_zone"
    assert by_symbol["112040"]["level_label"] is None
    assert by_symbol["005930"]["message"] == "[F존] 삼성전자 포착"


def test_record_signal_capture_events_skips_bad_rows(event_log_tmp):
    signals = [
        {"strategy": "gold_zone", "symbol": "", "name": "빈코드"},  # symbol 없음 → skip
        {"strategy": "swing_38", "symbol": "000660"},                # name 없음 → symbol 대체
    ]
    written = event_log_tmp.record_signal_capture_events(signals)
    assert len(written) == 1
    events = event_log_tmp.read_alert_events()
    assert len(events) == 1
    assert events[0]["message"] == "[38스윙] 000660 포착"
    assert events[0]["name"] == "000660"


def test_record_signal_capture_events_empty(event_log_tmp):
    assert event_log_tmp.record_signal_capture_events([]) == []
    assert event_log_tmp.read_alert_events() == []


def test_strategy_label_fallback(event_log_tmp):
    assert event_log_tmp.strategy_label("supertrend") == "슈퍼트렌드"
    assert event_log_tmp.strategy_label("unknown_key") == "unknown_key"


# ── 3. 임포트 무결성 ─────────────────────────────────────────────────────────

def test_daemon_module_imports():
    mod = importlib.import_module("scripts.intraday_buy_daemon")
    assert hasattr(mod, "_record_alert_events")
    assert hasattr(mod, "_ALERT_EVENTS_ENABLED")


def test_scheduler_module_imports():
    mod = importlib.import_module("scripts.finance.telegram_integration.scheduler")
    assert hasattr(mod, "start_scheduler")
