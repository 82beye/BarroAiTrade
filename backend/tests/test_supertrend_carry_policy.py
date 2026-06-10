"""BAR-OPS-38 — 이월(오버나이트) 정책 + single_tranche 기본 ON 테스트.

근거: reports/2026-06-10/2026-06-10_매매복기.md P0#2/#4.
"""
from __future__ import annotations

import types as _types
from datetime import datetime, timedelta, timezone

from backend.core.supertrend_auto_trader import (
    SupertrendAutoConfig,
    SupertrendAutoTrader,
)
from backend.models.market import OHLCV, MarketType


def _bars_two_days(prev_close: float, today_closes: list[float], symbol="005930"):
    """전일 마지막 봉 1개 + 당일 봉들 — _prev_close/_carry_gap_stop_hit 평가용 (KST naive)."""
    out = [OHLCV(symbol=symbol, timestamp=datetime(2026, 6, 9, 15, 15),
                 open=prev_close, high=prev_close, low=prev_close, close=prev_close,
                 volume=1000, market_type=MarketType.STOCK)]
    base = datetime(2026, 6, 10, 9, 0)
    for i, p in enumerate(today_closes):
        out.append(OHLCV(symbol=symbol, timestamp=base + timedelta(minutes=5 * i),
                         open=p, high=p, low=p, close=p,
                         volume=1000, market_type=MarketType.STOCK))
    return out


def _trader(config) -> SupertrendAutoTrader:
    async def _universe():
        return []
    return SupertrendAutoTrader(
        candle_fetcher=None, account_fetcher=None, order_gate=None,
        pos_store=None, universe_provider=_universe, config=config,
    )


def _carried_pos(entry_price=10000.0):
    """전일(6/9 KST) 진입 포지션 — entry_time 은 UTC ISO(시스템 규약)."""
    return _types.SimpleNamespace(
        entry_price=entry_price,
        entry_time="2026-06-09T04:23:23+00:00",   # 6/9 13:23 KST
    )


def _today_pos(entry_price=10000.0):
    return _types.SimpleNamespace(
        entry_price=entry_price,
        entry_time="2026-06-10T00:10:00+00:00",   # 6/10 09:10 KST (당일)
    )


# ── P0#4③ 이월 갭하락 스탑 ──────────────────────────────────────────────────

def test_carry_gap_stop_hits_on_gap_down():
    tr = _trader(SupertrendAutoConfig(carry_gap_stop_pct=-3.0))
    bars = _bars_two_days(prev_close=10000, today_closes=[9650])   # -3.5%
    assert tr._carry_gap_stop_hit(_carried_pos(), bars) is True


def test_carry_gap_stop_ignores_small_gap():
    tr = _trader(SupertrendAutoConfig(carry_gap_stop_pct=-3.0))
    bars = _bars_two_days(prev_close=10000, today_closes=[9800])   # -2.0%
    assert tr._carry_gap_stop_hit(_carried_pos(), bars) is False


def test_carry_gap_stop_not_applied_to_today_entry():
    """당일 진입엔 미적용 — 하드손절(-6%)이 담당."""
    tr = _trader(SupertrendAutoConfig(carry_gap_stop_pct=-3.0))
    bars = _bars_two_days(prev_close=10000, today_closes=[9000])   # -10%
    assert tr._carry_gap_stop_hit(_today_pos(), bars) is False


def test_carry_gap_stop_disabled_when_zero():
    tr = _trader(SupertrendAutoConfig(carry_gap_stop_pct=0.0))
    bars = _bars_two_days(prev_close=10000, today_closes=[9000])
    assert tr._carry_gap_stop_hit(_carried_pos(), bars) is False


def test_carry_gap_stop_no_prev_day_bars_safe():
    """전일 봉이 없으면(데이터 부족) 발동하지 않음 — 보수적."""
    tr = _trader(SupertrendAutoConfig(carry_gap_stop_pct=-3.0))
    base = datetime(2026, 6, 10, 9, 0)
    bars = [OHLCV(symbol="005930", timestamp=base, open=9000, high=9000,
                  low=9000, close=9000, volume=1, market_type=MarketType.STOCK)]
    assert tr._carry_gap_stop_hit(_carried_pos(), bars) is False


# ── P0#4① 진입 컷오프 ───────────────────────────────────────────────────────

def test_entry_cutoff_disabled_when_empty():
    tr = _trader(SupertrendAutoConfig(entry_cutoff_time=""))
    assert tr._entry_cutoff_passed() is False


def test_entry_cutoff_default_present():
    """기본 14:30 — 6/9 막판(13:23~14:09) 이월 진입 5종의 꼬리(14:09)를 차단하는 정책 기본값."""
    c = SupertrendAutoConfig()
    assert c.entry_cutoff_time == "14:30"
    assert c.carry_gap_stop_pct == -3.0


def test_entry_cutoff_time_comparison(monkeypatch):
    tr = _trader(SupertrendAutoConfig(entry_cutoff_time="14:30"))
    import backend.core.supertrend_auto_trader as mod

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 10, 14, 31, tzinfo=timezone(timedelta(hours=9)))

    # _entry_cutoff_passed 는 함수 내부에서 datetime 을 import — 전역 datetime 패치로 주입
    monkeypatch.setattr("datetime.datetime", _FakeDT)
    assert tr._entry_cutoff_passed() is True

    class _FakeDT2(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 6, 10, 13, 0, tzinfo=timezone(timedelta(hours=9)))

    monkeypatch.setattr("datetime.datetime", _FakeDT2)
    assert tr._entry_cutoff_passed() is False


# ── P0#2 single_tranche 기본 ON ─────────────────────────────────────────────

def test_single_tranche_default_on():
    """6/10 319660 이중매수(장부 23 vs 실보유 32) 처방 — 트레이더 기본값 True."""
    assert SupertrendAutoConfig().single_tranche is True
