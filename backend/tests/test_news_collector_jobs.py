"""뉴스/공시 수집기 가동 스케줄 잡 테스트 (default-OFF 패턴)."""
from __future__ import annotations

import pytest

from backend.core.scheduler import news_collector_jobs as ncj


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


def test_disabled_registers_nothing():
    sched = _FakeScheduler()
    ids = ncj.register_news_collector_jobs(sched, enabled=False)
    assert ids == []
    assert sched.jobs == []


def test_enabled_registers_one_interval_job():
    from apscheduler.triggers.interval import IntervalTrigger

    sched = _FakeScheduler()
    ids = ncj.register_news_collector_jobs(sched, enabled=True)

    assert ids == ["news_collector_tick"]
    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    assert isinstance(job["trigger"], IntervalTrigger)
    assert job["kwargs"]["max_instances"] == 1


def test_flag_env_default_off(monkeypatch):
    monkeypatch.delenv("BARRO_NEWS_COLLECTOR_ENABLED", raising=False)
    sched = _FakeScheduler()
    assert ncj.register_news_collector_jobs(sched) == []
    assert sched.jobs == []


def test_flag_env_on(monkeypatch):
    monkeypatch.setenv("BARRO_NEWS_COLLECTOR_ENABLED", "1")
    sched = _FakeScheduler()
    ids = ncj.register_news_collector_jobs(sched)
    assert ids == ["news_collector_tick"]


class TestBuildCollector:
    @pytest.mark.asyncio
    async def test_excludes_dart_when_no_key(self, monkeypatch):
        from backend.config.settings import get_settings

        get_settings.cache_clear()
        monkeypatch.delenv("DART_API_KEY", raising=False)
        collector = ncj._build_collector()
        try:
            source_names = [type(s).__name__ for s in collector._sources]
            assert "DARTSource" not in source_names
            assert len(collector._sources) == 4  # RSS 4종만
        finally:
            await collector._http.aclose()
        get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_includes_dart_when_key_present(self, monkeypatch):
        from backend.config.settings import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("DART_API_KEY", "test-key-123")
        collector = ncj._build_collector()
        try:
            source_names = [type(s).__name__ for s in collector._sources]
            assert "DARTSource" in source_names
            assert len(collector._sources) == 5  # RSS 4종 + DART
        finally:
            await collector._http.aclose()
        get_settings.cache_clear()
