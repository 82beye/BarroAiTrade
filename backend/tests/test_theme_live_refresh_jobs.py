"""테마 라이브 갱신 스케줄 잡 등록 테스트 (default-OFF 패턴).

theme_snapshot_jobs 테스트(test_tima_ops_wiring)와 동일한 _FakeScheduler 패턴:
- 플래그 OFF 시 미등록(빈 리스트), 스케줄러 무접촉.
- 플래그 ON 시 CronTrigger 잡 1개 등록.
"""
from __future__ import annotations

from backend.core.scheduler import theme_live_refresh_jobs as tlr


class _FakeScheduler:
    """add_job 호출을 기록만 하는 스케줄러 대역."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


def test_disabled_registers_nothing():
    sched = _FakeScheduler()
    ids = tlr.register_theme_live_refresh_jobs(sched, enabled=False)
    assert ids == []
    assert sched.jobs == []


def test_enabled_registers_one_cron_job():
    from apscheduler.triggers.cron import CronTrigger

    sched = _FakeScheduler()
    ids = tlr.register_theme_live_refresh_jobs(sched, enabled=True)

    assert ids == ["theme_live_refresh"]
    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    assert isinstance(job["trigger"], CronTrigger)
    assert job["kwargs"]["id"] == "theme_live_refresh"


def test_flag_env_default_off(monkeypatch):
    monkeypatch.delenv("BARRO_THEME_LIVE_REFRESH_ENABLED", raising=False)
    sched = _FakeScheduler()
    assert tlr.register_theme_live_refresh_jobs(sched) == []
    assert sched.jobs == []


def test_flag_env_on(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_LIVE_REFRESH_ENABLED", "1")
    sched = _FakeScheduler()
    ids = tlr.register_theme_live_refresh_jobs(sched)
    assert ids == ["theme_live_refresh"]
    assert len(sched.jobs) == 1
