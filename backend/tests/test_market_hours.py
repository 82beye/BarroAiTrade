"""개장 직후 유예(open-rush-yield) 판정 테스트."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

from backend.core.scheduler.market_hours import is_open_rush, is_regular_market

_KST = timezone(timedelta(hours=9))


def _kst(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=_KST)


class TestIsOpenRush:
    def test_within_default_window_weekday(self):
        # 2026-07-08 수요일 09:02 KST
        assert is_open_rush(_kst(2026, 7, 8, 9, 2)) is True

    def test_before_window(self):
        assert is_open_rush(_kst(2026, 7, 8, 8, 59)) is False

    def test_at_end_boundary_exclusive(self):
        assert is_open_rush(_kst(2026, 7, 8, 9, 5)) is False

    def test_after_window(self):
        assert is_open_rush(_kst(2026, 7, 8, 10, 0)) is False

    def test_weekend_never_rush(self):
        # 2026-07-11 토요일 09:02 KST
        assert is_open_rush(_kst(2026, 7, 11, 9, 2)) is False

    def test_flag_disabled_returns_false(self, monkeypatch):
        monkeypatch.setenv("BARRO_OPEN_RUSH_YIELD_ENABLED", "0")
        assert is_open_rush(_kst(2026, 7, 8, 9, 2)) is False

    def test_custom_window_env(self, monkeypatch):
        monkeypatch.setenv("BARRO_OPEN_RUSH_START_HHMM", "1000")
        monkeypatch.setenv("BARRO_OPEN_RUSH_END_HHMM", "1010")
        assert is_open_rush(_kst(2026, 7, 8, 9, 2)) is False
        assert is_open_rush(_kst(2026, 7, 8, 10, 5)) is True

    def test_invalid_custom_window_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("BARRO_OPEN_RUSH_START_HHMM", "9960")
        monkeypatch.setenv("BARRO_OPEN_RUSH_END_HHMM", "2400")
        assert is_open_rush(_kst(2026, 7, 8, 9, 2)) is True

    def test_aware_datetime_is_converted_to_kst(self):
        utc = timezone.utc
        assert is_open_rush(datetime(2026, 7, 8, 0, 2, tzinfo=utc)) is True


class TestIsRegularMarket:
    def test_regular_and_closing_auction(self):
        assert is_regular_market(_kst(2026, 7, 8, 9, 0)) is True
        assert is_regular_market(_kst(2026, 7, 8, 15, 29)) is True

    def test_outside_or_weekend(self):
        assert is_regular_market(_kst(2026, 7, 8, 8, 59)) is False
        assert is_regular_market(_kst(2026, 7, 8, 15, 30)) is False
        assert is_regular_market(_kst(2026, 7, 11, 10, 0)) is False
