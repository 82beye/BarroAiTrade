"""테마보드 캐시 갱신 스케줄 잡 테스트 (default-ON 패턴).

theme_live_refresh_jobs 테스트와 동일한 _FakeScheduler 패턴이나, 이 잡은
화면표시 직접 데이터소스라 기본 ON — 다른 테마 잡들과 반대 극성 검증.
"""
from __future__ import annotations

import pytest

from backend.core.scheduler import theme_board_cache_jobs as tbc


class _FakeScheduler:
    """add_job 호출을 기록만 하는 스케줄러 대역."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


def test_disabled_registers_nothing():
    sched = _FakeScheduler()
    ids = tbc.register_theme_board_cache_jobs(sched, enabled=False)
    assert ids == []
    assert sched.jobs == []


def test_enabled_registers_one_interval_job():
    from apscheduler.triggers.interval import IntervalTrigger

    sched = _FakeScheduler()
    ids = tbc.register_theme_board_cache_jobs(sched, enabled=True)

    assert ids == ["theme_board_cache_refresh"]
    assert len(sched.jobs) == 1
    job = sched.jobs[0]
    assert isinstance(job["trigger"], IntervalTrigger)
    assert job["kwargs"]["id"] == "theme_board_cache_refresh"
    assert job["kwargs"]["max_instances"] == 1


def test_flag_env_default_on(monkeypatch):
    monkeypatch.delenv("BARRO_THEME_BOARD_CACHE_ENABLED", raising=False)
    sched = _FakeScheduler()
    ids = tbc.register_theme_board_cache_jobs(sched)
    assert ids == ["theme_board_cache_refresh"]
    assert len(sched.jobs) == 1


def test_flag_env_off(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_BOARD_CACHE_ENABLED", "0")
    sched = _FakeScheduler()
    assert tbc.register_theme_board_cache_jobs(sched) == []
    assert sched.jobs == []


def test_custom_interval_env(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_BOARD_CACHE_INTERVAL_SEC", "5")
    sched = _FakeScheduler()
    tbc.register_theme_board_cache_jobs(sched, enabled=True)
    assert sched.jobs[0]["kwargs"]["name"] == "테마보드 캐시 갱신 (5s)"


class _FakeTheme:
    def __init__(self, id_: int) -> None:
        self.id = id_


@pytest.mark.asyncio
async def test_refresh_all_themes_populates_cache(monkeypatch):
    from backend.api.routes import themes_calendar_news as tcn

    tcn._THEME_STOCKS_CACHE.clear()

    async def _fake_fetch_themes():
        return [_FakeTheme(1), _FakeTheme(2)]

    async def _fake_fetch_theme_stocks(theme_id, *, enrich=True):
        if theme_id == 2:
            raise RuntimeError("boom")
        return [f"stock-for-{theme_id}"]

    monkeypatch.setattr(tcn, "fetch_themes", _fake_fetch_themes)
    monkeypatch.setattr(tcn, "fetch_theme_stocks", _fake_fetch_theme_stocks)

    await tbc._refresh_all_themes()

    assert 1 in tcn._THEME_STOCKS_CACHE
    assert tcn._THEME_STOCKS_CACHE[1][1] == ["stock-for-1"]
    # 실패한 테마(2)는 캐시에 없어야 함(이전 값 유지 또는 미기록, 날조 금지)
    assert 2 not in tcn._THEME_STOCKS_CACHE

    tcn._THEME_STOCKS_CACHE.clear()
