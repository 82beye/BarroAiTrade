"""OHLCV 실거래소(키움 KRX) 캔들 조달 배선 테스트.

검증 대상(모두 읽기 전용 조회, 주문/실거래 경로 무관):
  1) market.get_ohlcv 의 candle_fetcher(실거래소)/캐시 폴백/우아 강등(500 금지)
  2) timeframe → ka10080 tic_scope 매핑
  3) ohlcv_sync_jobs 플래그 게이팅(OFF 미등록 / ON 등록)
  4) update_ohlcv_cache(.py/_5m.py) 기본 캐시 경로 해석(env override + repo 폴백)
"""
from __future__ import annotations

from datetime import datetime

import pytest

import backend.api.routes.market as market
from backend.core.state import app_state
from backend.models.market import MarketType, OHLCV


# ══════════════════════════════════════════════════════════
# fakes
# ══════════════════════════════════════════════════════════
def _ohlcv(ts: str, close: float = 100.0) -> OHLCV:
    """'YYYYMMDD'(일봉) 또는 'YYYYMMDDHHMMSS'(분봉) → OHLCV."""
    fmt = "%Y%m%d%H%M%S" if len(ts) == 14 else "%Y%m%d"
    return OHLCV(
        symbol="005930",
        timestamp=datetime.strptime(ts, fmt),
        open=close - 1,
        high=close + 2,
        low=close - 3,
        close=close,
        volume=1000.0,
        market_type=MarketType.STOCK,
    )


class _FakeFetcher:
    """KiwoomNativeCandleFetcher 대역 — fetch_daily / fetch_minute_history."""

    def __init__(self, daily=None, minute=None, raise_daily=False, raise_minute=False):
        self._daily = daily or []
        self._minute = minute or []
        self._raise_daily = raise_daily
        self._raise_minute = raise_minute
        self.calls: list = []

    async def fetch_daily(self, symbol, *a, **k):
        self.calls.append(("daily", symbol))
        if self._raise_daily:
            raise RuntimeError("boom-daily")
        return self._daily

    async def fetch_minute_history(self, symbol, tic_scope="1", *a, **k):
        self.calls.append(("minute", symbol, tic_scope))
        if self._raise_minute:
            raise RuntimeError("boom-minute")
        return self._minute


_CACHE_ROW = {
    "date": "20260702",
    "open": 1.0,
    "high": 2.0,
    "low": 0.0,
    "close": 1.5,
    "volume": 9.0,
}


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    """이 서버는 항상 gateway=None(오케스트레이터 미기동) 경로."""
    monkeypatch.setattr(app_state, "market_gateway", None, raising=False)


# ══════════════════════════════════════════════════════════
# 1) 실거래소 캔들 분기
# ══════════════════════════════════════════════════════════
async def test_ohlcv_1d_exchange(monkeypatch):
    """1d: candle_fetcher 있으면 실거래소 일봉(source=exchange)."""
    fetcher = _FakeFetcher(daily=[_ohlcv("20260701", 100), _ohlcv("20260702", 101)])
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: fetcher)
    res = await market.get_ohlcv(symbol="005930", timeframe="1d", limit=300)
    assert res["source"] == "exchange"
    assert res["timeframe"] == "1d"
    assert len(res["data"]) == 2
    assert res["data"][-1]["close"] == 101
    assert res["data"][0]["timestamp"] == "2026-07-01T00:00:00"


async def test_ohlcv_15m_exchange(monkeypatch):
    """15m: 실거래소 분봉(source=exchange), tic_scope='15' 로 조회."""
    fetcher = _FakeFetcher(
        minute=[_ohlcv("20260701093000", 100), _ohlcv("20260701094500", 102)]
    )
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: fetcher)
    res = await market.get_ohlcv(symbol="005930", timeframe="15m", limit=300)
    assert res["source"] == "exchange"
    assert fetcher.calls[0] == ("minute", "005930", "15")
    assert len(res["data"]) == 2
    assert res["data"][-1]["close"] == 102


async def test_ohlcv_exchange_respects_limit(monkeypatch):
    """limit 초과 시 최근 N개만 반환(꼬리 슬라이스)."""
    daily = [_ohlcv(f"2026070{i}", 100 + i) for i in range(1, 4)]  # 3봉
    fetcher = _FakeFetcher(daily=daily)
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: fetcher)
    res = await market.get_ohlcv(symbol="005930", timeframe="1d", limit=2)
    assert len(res["data"]) == 2
    assert res["data"][-1]["close"] == 103  # 최신 유지


async def test_ohlcv_1d_cache_fallback_when_no_key(monkeypatch):
    """키 부재(candle_fetcher None) → 일봉 캐시 폴백(source=cache)."""
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: None)
    monkeypatch.setattr(
        market.cache_quotes, "get_daily_candles", lambda s, l: [_CACHE_ROW]
    )
    res = await market.get_ohlcv(symbol="005930", timeframe="1d", limit=300)
    assert res["source"] == "cache"
    assert res["as_of"] == "20260702"
    assert res["data"][0]["timestamp"] == "2026-07-02T00:00:00"


async def test_ohlcv_1d_exchange_fail_then_cache(monkeypatch):
    """1d 실거래소 예외 → 캐시 폴백(500 금지)."""
    fetcher = _FakeFetcher(raise_daily=True)
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: fetcher)
    monkeypatch.setattr(
        market.cache_quotes, "get_daily_candles", lambda s, l: [_CACHE_ROW]
    )
    res = await market.get_ohlcv(symbol="005930", timeframe="1d", limit=300)
    assert res["source"] == "cache"


async def test_ohlcv_1d_404_when_no_cache(monkeypatch):
    """1d 실거래소·캐시 모두 없음 → 404."""
    from fastapi import HTTPException

    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: None)
    monkeypatch.setattr(market.cache_quotes, "get_daily_candles", lambda s, l: [])
    with pytest.raises(HTTPException) as exc:
        await market.get_ohlcv(symbol="005930", timeframe="1d", limit=300)
    assert exc.value.status_code == 404


async def test_ohlcv_minute_cache_fallback_no_500(monkeypatch):
    """분봉 실거래소 실패 → 503/500 대신 일봉 캐시(note) 강등."""
    fetcher = _FakeFetcher(raise_minute=True)
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: fetcher)
    monkeypatch.setattr(
        market.cache_quotes, "get_daily_candles", lambda s, l: [_CACHE_ROW]
    )
    res = await market.get_ohlcv(symbol="005930", timeframe="5m", limit=300)
    assert res["source"] == "cache"
    assert "분봉" in res.get("note", "")


async def test_ohlcv_minute_empty_graceful_no_500(monkeypatch):
    """분봉 실패 + 캐시도 없음 → 빈 data + status(500 금지)."""
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: None)
    monkeypatch.setattr(market.cache_quotes, "get_daily_candles", lambda s, l: [])
    res = await market.get_ohlcv(symbol="005930", timeframe="5m", limit=300)
    assert res["data"] == []
    assert res["source"] == "none"
    assert res["status"] == "no_data"  # 지원 tf(5m)이나 데이터 없음


async def test_ohlcv_unsupported_tf_empty_graceful(monkeypatch):
    """매핑 없는 tf(예: 7m) + 캐시 없음 → status=unsupported."""
    fetcher = _FakeFetcher()
    monkeypatch.setattr(market, "_get_candle_fetcher", lambda: fetcher)
    monkeypatch.setattr(market.cache_quotes, "get_daily_candles", lambda s, l: [])
    res = await market.get_ohlcv(symbol="005930", timeframe="7m", limit=300)
    # 매핑 없어 실거래소 분봉 미시도 → 캐시 없음 → unsupported
    assert res["status"] == "unsupported"
    assert res["data"] == []
    assert not fetcher.calls  # 분봉 조회 미시도


# ══════════════════════════════════════════════════════════
# 2) timeframe → tic_scope 매핑
# ══════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "tf,tic",
    [
        ("1m", "1"), ("3m", "3"), ("5m", "5"), ("10m", "10"),
        ("15m", "15"), ("30m", "30"), ("1h", "60"), ("60m", "60"),
    ],
)
def test_timeframe_to_tic_scope(tf, tic):
    assert market.timeframe_to_tic_scope(tf) == tic


@pytest.mark.parametrize("tf", ["1d", "1w", "1M", "7m", ""])
def test_timeframe_to_tic_scope_unsupported(tf):
    assert market.timeframe_to_tic_scope(tf) is None


# ══════════════════════════════════════════════════════════
# 3) ohlcv_sync_jobs 플래그 게이팅
# ══════════════════════════════════════════════════════════
class _FakeScheduler:
    def __init__(self):
        self.jobs: list = []

    def add_job(self, func, trigger, **kw):
        self.jobs.append(kw.get("id"))


def test_ohlcv_sync_jobs_disabled_default(monkeypatch):
    """플래그 미설정(기본 OFF) → 잡 미등록(byte-identical)."""
    from backend.core.scheduler.ohlcv_sync_jobs import register_ohlcv_sync_jobs

    monkeypatch.delenv("BARRO_OHLCV_SYNC_ENABLED", raising=False)
    sched = _FakeScheduler()
    assert register_ohlcv_sync_jobs(sched) == []
    assert sched.jobs == []


def test_ohlcv_sync_jobs_enabled_explicit(monkeypatch):
    """enabled=True 강제 → 잡 1개 등록."""
    from backend.core.scheduler.ohlcv_sync_jobs import register_ohlcv_sync_jobs

    sched = _FakeScheduler()
    assert register_ohlcv_sync_jobs(sched, enabled=True) == ["ohlcv_daily_sync"]
    assert sched.jobs == ["ohlcv_daily_sync"]


def test_ohlcv_sync_jobs_enabled_env(monkeypatch):
    """BARRO_OHLCV_SYNC_ENABLED=1 → 잡 등록."""
    from backend.core.scheduler.ohlcv_sync_jobs import register_ohlcv_sync_jobs

    monkeypatch.setenv("BARRO_OHLCV_SYNC_ENABLED", "1")
    sched = _FakeScheduler()
    assert register_ohlcv_sync_jobs(sched) == ["ohlcv_daily_sync"]


# ══════════════════════════════════════════════════════════
# 4) update_ohlcv_cache 기본 경로 해석
# ══════════════════════════════════════════════════════════
def test_update_ohlcv_cache_default_env_override(monkeypatch):
    import scripts.update_ohlcv_cache as m

    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR", "/tmp/custom_ohlcv")
    assert m._default_cache_dir() == "/tmp/custom_ohlcv"


def test_update_ohlcv_cache_default_repo_fallback(monkeypatch):
    import scripts.update_ohlcv_cache as m

    monkeypatch.delenv("BARRO_OHLCV_CACHE_DIR", raising=False)
    result = m._default_cache_dir()
    assert result.endswith("/data/ohlcv_cache")
    assert "beye82" not in result  # 하드코딩 타 사용자 경로 제거 확인


def test_update_ohlcv_cache_5m_default_paths(monkeypatch):
    import scripts.update_ohlcv_cache_5m as m

    monkeypatch.delenv("BARRO_OHLCV_CACHE_DIR", raising=False)
    monkeypatch.delenv("BARRO_OHLCV_CACHE_DIR_5M", raising=False)
    assert m._default_daily_cache_dir().endswith("/data/ohlcv_cache")
    assert m._default_5m_cache_dir().endswith("/data/ohlcv_cache_5m")
    assert "beye82" not in m._default_daily_cache_dir()
    assert "beye82" not in m._default_5m_cache_dir()

    monkeypatch.setenv("BARRO_OHLCV_CACHE_DIR_5M", "/tmp/c5")
    assert m._default_5m_cache_dir() == "/tmp/c5"
