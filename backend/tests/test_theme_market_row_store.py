from __future__ import annotations

import pytest

from backend.core.scheduler import theme_market_row_jobs as jobs
from backend.core.themes.market_row_store import (
    aggregate_theme_memberships,
    capture_theme_market_rows,
    latest_meta,
    merge_symbol_rows,
    normalize_filters,
)


def test_normalize_filters_dedupes_and_validates():
    assert normalize_filters("value,gainers,value") == ["value", "gainers"]
    with pytest.raises(ValueError):
        normalize_filters("value,bad")


def test_merge_symbol_rows_preserves_source_ranks_and_values():
    rows = [
        {
            "source": "value",
            "source_rank": 1,
            "symbol": "005930",
            "name": "삼성전자",
            "price": 70000,
            "change_pct": 1.2,
            "value_traded": 1000,
        },
        {
            "source": "gainers",
            "source_rank": 4,
            "symbol": "005930",
            "name": "삼성전자",
            "price": 70100,
            "change_pct": 1.4,
            "value_traded": 900,
        },
    ]

    merged = merge_symbol_rows(rows)

    assert merged["005930"]["sources"] == "gainers|value"
    assert merged["005930"]["value_rank"] == 1
    assert merged["005930"]["gainers_rank"] == 4
    assert merged["005930"]["price"] == 70100
    assert merged["005930"]["change_pct"] == 1.4
    assert merged["005930"]["value_traded"] == 1000


def test_aggregate_theme_memberships_by_value_and_change():
    rows = [
        {
            "source": "value",
            "source_rank": 1,
            "symbol": "000001",
            "name": "A",
            "price": 1000,
            "change_pct": 5.0,
            "value_traded": 100.0,
        },
        {
            "source": "losers",
            "source_rank": 3,
            "symbol": "000002",
            "name": "B",
            "price": 2000,
            "change_pct": -2.0,
            "value_traded": 50.0,
        },
        {
            "source": "gainers",
            "source_rank": 2,
            "symbol": "000003",
            "name": "C",
            "price": 3000,
            "change_pct": 8.0,
            "value_traded": 20.0,
        },
    ]
    memberships = [
        {"theme_id": 1, "theme_name": "테마A", "symbol": "000001", "score": 0},
        {"theme_id": 1, "theme_name": "테마A", "symbol": "000002", "score": 0},
        {"theme_id": 1, "theme_name": "테마A", "symbol": "999999", "score": 0},
        {"theme_id": 2, "theme_name": "테마B", "symbol": "000003", "score": 0},
    ]

    agg = aggregate_theme_memberships(
        rows,
        memberships,
        captured_at="2026-07-08T09:00:00+09:00",
        trade_date="2026-07-08",
    )

    by_id = {row["theme_id"]: row for row in agg}
    assert by_id[1]["stock_count"] == 3
    assert by_id[1]["matched_count"] == 2
    assert by_id[1]["avg_change_pct"] == 1.5
    assert by_id[1]["sum_value_traded"] == 150.0
    assert by_id[1]["positive_count"] == 1
    assert by_id[1]["negative_count"] == 1
    assert by_id[1]["rank_by_value"] == 1
    assert by_id[2]["rank_by_change"] == 1


def test_weighted_change_uses_only_rows_with_both_value_and_change():
    rows = [
        {
            "source": "value",
            "source_rank": 1,
            "symbol": "000001",
            "name": "A",
            "change_pct": 10.0,
            "value_traded": 100.0,
        },
        {
            "source": "value",
            "source_rank": 2,
            "symbol": "000002",
            "name": "B",
            "change_pct": None,
            "value_traded": 900.0,
        },
    ]
    memberships = [
        {"theme_id": 1, "theme_name": "테마A", "symbol": "000001", "score": 0},
        {"theme_id": 1, "theme_name": "테마A", "symbol": "000002", "score": 0},
    ]

    result = aggregate_theme_memberships(rows, memberships)

    assert result[0]["sum_value_traded"] == 1000.0
    assert result[0]["value_weighted_change_pct"] == 10.0


@pytest.mark.asyncio
async def test_empty_capture_preserves_latest_snapshot(monkeypatch, tmp_path):
    class _Quotes:
        def __init__(self) -> None:
            self.rows = [
                {
                    "symbol": "005930",
                    "name": "삼성전자",
                    "price": 70000,
                    "change_pct": 1.0,
                    "value_traded": 1000.0,
                }
            ]

        async def ranking(self, **kwargs):
            return self.rows

    async def _memberships():
        return [
            {
                "theme_id": 1,
                "theme_name": "반도체",
                "symbol": "005930",
                "score": 1.0,
            }
        ]

    monkeypatch.setenv("BARRO_THEME_MARKET_ROWS_DIR", str(tmp_path))
    monkeypatch.setattr(
        "backend.core.themes.market_row_store.load_theme_memberships", _memberships
    )
    quotes = _Quotes()

    first = await capture_theme_market_rows(quotes=quotes, filters="value")
    first_meta = latest_meta()
    quotes.rows = []
    second = await capture_theme_market_rows(quotes=quotes, filters="value")

    assert first["status"] == "ok"
    assert second["status"] == "no_rows"
    assert second["latest_preserved"] is True
    assert latest_meta() == first_meta


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "kwargs": kwargs})


def test_theme_market_row_job_registers_by_default(monkeypatch):
    monkeypatch.delenv("BARRO_THEME_MARKET_ROWS_ENABLED", raising=False)
    sched = _FakeScheduler()

    ids = jobs.register_theme_market_row_jobs(sched)

    assert ids == ["theme_market_rows_capture"]
    assert len(sched.jobs) == 1
    assert sched.jobs[0]["kwargs"]["max_instances"] == 1


def test_theme_market_row_job_can_be_disabled(monkeypatch):
    monkeypatch.setenv("BARRO_THEME_MARKET_ROWS_ENABLED", "0")
    sched = _FakeScheduler()

    assert jobs.register_theme_market_row_jobs(sched) == []
    assert sched.jobs == []


@pytest.mark.asyncio
async def test_theme_market_row_job_skips_outside_regular_market(monkeypatch):
    from backend.core.scheduler import market_hours
    from backend.core.themes import market_row_store

    called = False

    async def _capture(**kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(market_hours, "is_regular_market", lambda: False)
    monkeypatch.setattr(market_row_store, "capture_theme_market_rows", _capture)

    await jobs._capture_theme_market_rows_job()

    assert called is False
