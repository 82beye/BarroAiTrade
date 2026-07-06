"""테마 뉴스발굴 스케줄 잡 테스트 (default-OFF 패턴, theme_live_refresh_jobs 와 동일)."""
from __future__ import annotations

from backend.core.scheduler import theme_discovery_jobs as tdj


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


def test_disabled_registers_nothing():
    sched = _FakeScheduler()
    ids = tdj.register_theme_discovery_jobs(sched, enabled=False)
    assert ids == []
    assert sched.jobs == []


def test_enabled_registers_one_interval_job():
    from apscheduler.triggers.interval import IntervalTrigger

    sched = _FakeScheduler()
    ids = tdj.register_theme_discovery_jobs(sched, enabled=True)

    assert ids == ["theme_discovery"]
    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    assert isinstance(job["trigger"], IntervalTrigger)
    assert job["kwargs"]["id"] == "theme_discovery"
    assert job["kwargs"]["max_instances"] == 1


def test_flag_env_default_off(monkeypatch):
    monkeypatch.delenv("BARRO_THEME_DISCOVERY_ENABLED", raising=False)
    sched = _FakeScheduler()
    assert tdj.register_theme_discovery_jobs(sched) == []
    assert sched.jobs == []


def test_flag_env_on(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_DISCOVERY_ENABLED", "1")
    sched = _FakeScheduler()
    ids = tdj.register_theme_discovery_jobs(sched)
    assert ids == ["theme_discovery"]


def test_custom_interval_env(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_DISCOVERY_INTERVAL_SEC", "60")
    sched = _FakeScheduler()
    tdj.register_theme_discovery_jobs(sched, enabled=True)
    assert sched.jobs[0]["kwargs"]["name"] == "테마 뉴스발굴 (60s)"
