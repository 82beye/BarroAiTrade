"""BAR-180 — detect_short_term_high_exit 단위 테스트."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core.strategy.short_term_high_exit import detect_short_term_high_exit
from backend.core.risk.holding_evaluator import (
    ExitPolicy,
    PositionContext,
    SellSignal,
    evaluate_holding,
)
from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.models.market import OHLCV
from decimal import Decimal


_TS = datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc)


def _ohlcv(open_: float, high: float, low: float, close: float, ts=_TS) -> OHLCV:
    from backend.models.market import MarketType
    return OHLCV(
        symbol="005930", timestamp=ts,
        open=open_, high=high, low=low, close=close, volume=100.0,
        market_type=MarketType.STOCK,
    )


def _two_candles(prev_open, prev_high, prev_low, prev_close,
                 cur_open, cur_high, cur_low, cur_close) -> list[OHLCV]:
    return [
        _ohlcv(prev_open, prev_high, prev_low, prev_close),
        _ohlcv(cur_open, cur_high, cur_low, cur_close),
    ]


class TestInsufficientData:
    def test_empty_list_returns_no_signal(self):
        result = detect_short_term_high_exit([])
        assert result.signal is False
        assert "데이터 부족" in result.reason

    def test_single_candle_returns_no_signal(self):
        result = detect_short_term_high_exit([_ohlcv(100, 105, 99, 102)])
        assert result.signal is False
        assert "데이터 부족" in result.reason


class TestNotNearPeak:
    def test_current_bar_far_from_peak_returns_no_signal(self):
        # peak=110, cur.high=100 → proximity=(110-100)/110 ≈ 9% > 0.3%
        candles = [
            _ohlcv(100, 110, 99, 105),  # peak=110
            _ohlcv(95, 100, 94, 98),    # cur.high=100, far from 110
        ]
        result = detect_short_term_high_exit(candles)
        assert result.signal is False
        assert "고점 미근접" in result.reason
        assert result.peak_high == 110.0


class TestDojiPattern:
    def test_doji_near_peak_triggers_signal(self):
        # 도지: open≈close, body/range < 0.15
        # cur high ≈ peak (within 0.3%)
        prev = _ohlcv(100, 110, 99, 108)   # peak=110
        cur = _ohlcv(109, 110, 104, 109.5) # body=0.5, range=6 → body_pct≈0.083 < 0.15
        result = detect_short_term_high_exit([prev, cur])
        assert result.signal is True
        assert result.pattern == "doji"
        assert "도지" in result.reason

    def test_normal_body_does_not_doji(self):
        # body/range = 4/6 = 0.67 → not doji
        prev = _ohlcv(100, 110, 99, 108)
        cur = _ohlcv(106, 110, 104, 110)   # near peak, but big body
        result = detect_short_term_high_exit([prev, cur])
        # Should not be DOJI — may be something else or no signal
        if result.signal:
            assert result.pattern != "doji"


class TestUpperWickPattern:
    def test_upper_wick_triggers_signal(self):
        # wick/body ≥ 1.0 and wick/price ≥ 0.5%
        # open=107, close=108, high=110, low=106
        # body=1, upper_wick=110-108=2, wick_ratio=2/1=2.0 ≥ 1.0
        # wick_pct = 2/108 ≈ 1.85% ≥ 0.5%
        prev = _ohlcv(100, 110, 99, 108)
        cur = _ohlcv(107, 110, 106, 108)   # high≈peak, long upper wick
        result = detect_short_term_high_exit([prev, cur])
        assert result.signal is True
        assert result.pattern == "upper_wick"
        assert "위꼬리" in result.reason

    def test_small_upper_wick_does_not_trigger(self):
        # wick_pct too small (wick < 0.5% of price)
        # price≈110, wick < 0.55 → wick_pct < 0.5%
        prev = _ohlcv(100, 110, 99, 108)
        cur = _ohlcv(109.0, 110.0, 108.8, 109.8)  # body=0.8, wick=0.2, ratio=0.25 < 1.0
        result = detect_short_term_high_exit([prev, cur])
        # wick_ratio=0.25 < 1.0 → no upper_wick
        if result.signal:
            assert result.pattern != "upper_wick"


class TestRedFollowPattern:
    def test_red_follow_after_peak_candle(self):
        # prev bar was at peak (high=110), cur bar is red (close < open) and near peak
        # cur.high must be near peak_high too (within 0.3%)
        prev = _ohlcv(107, 110, 106, 109)   # prev.high=110 = peak
        cur = _ohlcv(109, 109.5, 107, 108)  # red (108 < 109), cur.high=109.5 near 110
        # proximity = (110-109.5)/110 = 0.45% > 0.3% → actually NOT near peak
        # Let's adjust: cur.high=110
        cur2 = _ohlcv(109, 110, 107, 108)   # red, cur.high=110=peak
        result = detect_short_term_high_exit([prev, cur2])
        # cur.high=110=peak_high → near_peak=True
        # body=1, range=3, body_pct=0.33 > 0.15 (not doji)
        # wick=110-109=1, wick_ratio=1/1=1.0 ≥ 1.0, wick_pct=1/108≈0.93% ≥ 0.5%
        # → upper_wick should fire first
        assert result.signal is True

    def test_red_follow_prev_was_peak(self):
        # Force RED_FOLLOW by making body_pct ≥ 0.15 and wick_ratio < 1.0
        # prev.high = peak, cur.high ≈ peak, cur is red, prev_was_peak
        # body=3, range=3.5 → body_pct=0.86 > 0.15 (not doji)
        # upper_wick = cur.high - max(cur.open, cur.close) = 110 - 109 = 1
        # body=3, wick_ratio=1/3=0.33 < 1.0 (not upper_wick)
        # → RED_FOLLOW should fire
        prev = _ohlcv(107, 110, 106, 109)   # prev.high=110=peak
        cur = _ohlcv(109, 110, 106, 106)    # red (106 < 109), body=3, range=4
        result = detect_short_term_high_exit([prev, cur])
        assert result.signal is True
        assert result.pattern == "red_follow"
        assert "음봉" in result.reason


class TestNoSignal:
    def test_hold_when_no_pattern_matches(self):
        # near peak, green candle, no long wick, no doji
        prev = _ohlcv(100, 110, 99, 108)
        # cur: green, body large, no doji, short wick
        cur = _ohlcv(108, 110, 107.5, 109.8)  # green, body=1.8/range=2.5=0.72, wick=0.2
        result = detect_short_term_high_exit([prev, cur])
        # wick_ratio=0.2/1.8=0.11 < 1.0 → no upper_wick
        # body_pct=0.72 > 0.15 → no doji
        # green → no red_follow
        assert result.signal is False
        assert "미충족" in result.reason


class TestHoldingEvaluatorIntegration:
    """holding_evaluator.py의 SHORT_TERM_HIGH 경로 통합 테스트."""

    def _holding(self, pnl_rate: float = 4.0) -> HoldingPosition:
        return HoldingPosition(
            symbol="005930", name="삼성전자", qty=10,
            avg_buy_price=Decimal("100000"), cur_price=Decimal("104000"),
            eval_amount=Decimal("1040000"),
            pnl=Decimal("40000"), pnl_rate=Decimal(str(pnl_rate)),
        )

    def test_short_term_high_fires_when_minute_candles_provided(self):
        """minute_candles + doji at peak → SHORT_TERM_HIGH signal."""
        h = self._holding(pnl_rate=4.0)  # ≥ partial_tp_pct(3.5) 조건 충족
        # doji near peak
        prev = _ohlcv(100, 110, 99, 108)
        cur = _ohlcv(109, 110, 104, 109.5)   # doji: body=0.5/range=6=0.083
        ctx = PositionContext(
            peak_pnl_rate=4.5,
            partial_tp_done=False,
            entry_time=None,
            strategy="f_zone",
            minute_candles=[prev, cur],
        )
        policy = ExitPolicy(partial_tp_pct=Decimal("3.5"))
        decision = evaluate_holding(h, policy, ctx)
        assert decision.signal == SellSignal.SHORT_TERM_HIGH
        assert "단기 고점" in decision.reason

    def test_short_term_high_skipped_when_below_partial_tp(self):
        """수익률 < partial_tp_pct 이면 SHORT_TERM_HIGH 경로 스킵."""
        h = self._holding(pnl_rate=1.0)  # 1% < partial_tp_pct 3.5%
        prev = _ohlcv(100, 110, 99, 108)
        cur = _ohlcv(109, 110, 104, 109.5)   # doji
        ctx = PositionContext(
            peak_pnl_rate=2.0,
            partial_tp_done=False,
            entry_time=None,
            strategy="f_zone",
            minute_candles=[prev, cur],
        )
        policy = ExitPolicy(partial_tp_pct=Decimal("3.5"))
        decision = evaluate_holding(h, policy, ctx)
        assert decision.signal != SellSignal.SHORT_TERM_HIGH

    def test_short_term_high_skipped_when_no_minute_candles(self):
        """minute_candles=None 이면 SHORT_TERM_HIGH 경로 스킵."""
        h = self._holding(pnl_rate=4.0)
        ctx = PositionContext(
            peak_pnl_rate=4.5,
            partial_tp_done=False,
            entry_time=None,
            strategy="f_zone",
            minute_candles=None,   # None → skip
        )
        decision = evaluate_holding(h, ExitPolicy(), ctx)
        assert decision.signal != SellSignal.SHORT_TERM_HIGH
