"""KRX 휴장일 캘린더 — 판정 정확성 + fail-open 보증."""
import json
from datetime import date

import pytest

from backend.core.market_session import market_calendar as mc


@pytest.fixture(autouse=True)
def _clear_caches():
    mc._overrides.cache_clear()
    mc._public_holidays.cache_clear()
    yield
    mc._overrides.cache_clear()
    mc._public_holidays.cache_clear()


# ── 활성 스위치 (기본 ON) ────────────────────────────────────────────
def test_skip_enabled_default_on():
    assert mc.skip_enabled({}) is True


@pytest.mark.parametrize("v", ["0", "false", "no", "off", "OFF", "False"])
def test_skip_disabled_by_falsy(v):
    assert mc.skip_enabled({mc.ENV_ENABLED: v}) is False


@pytest.mark.parametrize("v", ["1", "true", "yes", "on", ""])
def test_skip_stays_on_otherwise(v):
    assert mc.skip_enabled({mc.ENV_ENABLED: v}) is True


# ── 주말 ─────────────────────────────────────────────────────────────
def test_saturday_and_sunday_are_holidays():
    assert mc.is_market_holiday(date(2026, 9, 26)) == (True, "주말")   # 토
    assert mc.is_market_holiday(date(2026, 9, 27)) == (True, "주말")   # 일


# ── 공휴일 (음력 명절 포함) ───────────────────────────────────────────
@pytest.mark.skipif(not mc.calendar_available(), reason="holidays 미설치")
@pytest.mark.parametrize("d", [
    date(2026, 9, 24),   # 추석 전날 (실사례 — 이 날 RC4010 발생)
    date(2026, 9, 25),   # 추석
    date(2026, 2, 17),   # 설날
    date(2026, 1, 1),    # 신정
    date(2026, 5, 5),    # 어린이날
    date(2026, 12, 25),  # 성탄절
])
def test_public_holidays_detected(d):
    hol, why = mc.is_market_holiday(d)
    assert hol is True
    assert why and why != "주말"


# ── 증시 전용 보정 (패키지가 놓치는 것) ───────────────────────────────
def test_labor_day_is_market_holiday():
    """근로자의날 — 증시 휴장. 실측에서 패키지 누락분으로 확인됐다."""
    hol, why = mc.is_market_holiday(date(2027, 5, 1))   # 2027-05-01 = 토요일이 아닌 연도 주의
    assert hol is True


def test_year_end_close_is_market_holiday():
    """12/31 연말 폐장 — 공휴일이 아니지만 증시 휴장."""
    hol, why = mc.is_market_holiday(date(2027, 12, 31))
    assert hol is True
    assert "폐장" in why or why == "주말"


# ── 개장일 ───────────────────────────────────────────────────────────
@pytest.mark.skipif(not mc.calendar_available(), reason="holidays 미설치")
@pytest.mark.parametrize("d", [
    date(2026, 9, 28),   # 추석 연휴 다음 월요일 — 개장
    date(2026, 9, 23),   # 실제 거래일(진입 4건 발생)
    date(2026, 9, 22),
])
def test_trading_days_not_flagged(d):
    assert mc.is_market_holiday(d) == (False, "")


# ── 다음 개장일 ──────────────────────────────────────────────────────
@pytest.mark.skipif(not mc.calendar_available(), reason="holidays 미설치")
def test_next_trading_day_skips_chuseok_and_weekend():
    # 9/24(목) 다음 개장일 → 9/25 추석·9/26~27 주말 건너 9/28(월)
    assert mc.next_trading_day(date(2026, 9, 24)) == date(2026, 9, 28)


def test_next_trading_day_returns_none_when_unbounded():
    assert mc.next_trading_day(date(2026, 9, 24), limit=0) is None


# ── 오버라이드 ───────────────────────────────────────────────────────
def test_override_adds_closed_day(tmp_path, monkeypatch):
    f = tmp_path / "ov.json"
    f.write_text(json.dumps({"closed": ["2026-09-28"]}), encoding="utf-8")
    monkeypatch.setenv(mc.ENV_OVERRIDE_PATH, str(f))
    hol, why = mc.is_market_holiday(date(2026, 9, 28))
    assert hol is True and "override" in why


def test_override_forces_open_wins_over_holiday(tmp_path, monkeypatch):
    """오판 정정 — 캘린더가 휴장이라 해도 open 목록이 이긴다."""
    f = tmp_path / "ov.json"
    f.write_text(json.dumps({"open": ["2026-09-25"]}), encoding="utf-8")
    monkeypatch.setenv(mc.ENV_OVERRIDE_PATH, str(f))
    assert mc.is_market_holiday(date(2026, 9, 25)) == (False, "")


def test_override_open_beats_weekend(tmp_path, monkeypatch):
    f = tmp_path / "ov.json"
    f.write_text(json.dumps({"open": ["2026-09-26"]}), encoding="utf-8")
    monkeypatch.setenv(mc.ENV_OVERRIDE_PATH, str(f))
    assert mc.is_market_holiday(date(2026, 9, 26)) == (False, "")


# ── fail-open ────────────────────────────────────────────────────────
def test_broken_override_file_is_ignored(tmp_path, monkeypatch):
    f = tmp_path / "ov.json"
    f.write_text("{ not json", encoding="utf-8")
    monkeypatch.setenv(mc.ENV_OVERRIDE_PATH, str(f))
    # 깨진 파일은 무시하고 정상 판정
    assert mc.is_market_holiday(date(2026, 9, 28)) == (False, "")


def test_missing_override_file_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv(mc.ENV_OVERRIDE_PATH, str(tmp_path / "nope.json"))
    assert mc.is_market_holiday(date(2026, 9, 28)) == (False, "")


def test_override_garbage_dates_skipped(tmp_path, monkeypatch):
    f = tmp_path / "ov.json"
    f.write_text(json.dumps({"closed": ["not-a-date", "2026-09-28"]}), encoding="utf-8")
    monkeypatch.setenv(mc.ENV_OVERRIDE_PATH, str(f))
    assert mc.is_market_holiday(date(2026, 9, 28))[0] is True


def test_fails_open_when_holidays_package_absent(monkeypatch):
    """패키지 미설치 시 주말만 판정하고 평일은 개장으로 본다."""
    monkeypatch.setattr(mc, "_public_holidays", lambda y: {})
    assert mc.is_market_holiday(date(2026, 9, 25)) == (False, "")   # 추석인데 판정 불가
    assert mc.is_market_holiday(date(2026, 9, 26)) == (True, "주말")  # 주말은 여전히 잡는다


def test_fails_open_on_unexpected_exception(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(mc, "_overrides", _boom)
    assert mc.is_market_holiday(date(2026, 9, 25)) == (False, "")


# ── 데몬 통합: 휴장일에 실매매만 막고 드라이런은 통과 ────────────────
def test_daemon_skips_real_trading_on_holiday(monkeypatch):
    """휴장일 + --no-dry-run → 스캔 없이 즉시 종료."""
    import asyncio
    from types import SimpleNamespace
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_d_hol", "scripts/intraday_buy_daemon.py")
    d = importlib.util.module_from_spec(spec)
    sys.modules["_d_hol"] = d
    spec.loader.exec_module(d)

    calls: list[str] = []

    async def _scan(*_a):
        calls.append("scan")
        return 0

    monkeypatch.setattr(d, "_build_oauth", lambda: object())
    monkeypatch.setattr(d, "_scan_and_buy", _scan)
    monkeypatch.setattr(d, "_is_market_holiday", lambda *_a, **_k: (True, "테스트 휴장"))
    monkeypatch.setattr(d, "_holiday_skip_enabled", lambda *_a, **_k: True)

    asyncio.run(d._daemon(SimpleNamespace(
        interval=1, top=5, telegram=False, entry_only_once=True, dry_run=False)))
    assert calls == [], "휴장일에 실매매 스캔이 실행됐다"


def test_daemon_allows_dry_run_on_holiday(monkeypatch):
    """휴장일이라도 dry_run 은 통과 — 배선 검증 경로를 막지 않는다."""
    import asyncio
    from types import SimpleNamespace
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_d_hol2", "scripts/intraday_buy_daemon.py")
    d = importlib.util.module_from_spec(spec)
    sys.modules["_d_hol2"] = d
    spec.loader.exec_module(d)

    calls: list[str] = []

    async def _scan(*_a):
        calls.append("scan")
        return 1

    monkeypatch.setattr(d, "_build_oauth", lambda: object())
    monkeypatch.setattr(d, "_scan_and_buy", _scan)
    monkeypatch.setattr(d, "_is_market_holiday", lambda *_a, **_k: (True, "테스트 휴장"))
    monkeypatch.setattr(d, "_holiday_skip_enabled", lambda *_a, **_k: True)

    asyncio.run(d._daemon(SimpleNamespace(
        interval=1, top=5, telegram=False, entry_only_once=True, dry_run=True)))
    assert calls == ["scan"], "드라이런이 휴장일 판정에 막혔다"
