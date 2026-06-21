"""closing_bet_filters 순수함수 단위테스트 (더트레이딩 v2 델타 게이트).

모듈은 관측 전용·inert(호출처 없음)이므로 라이브 무영향. 본 테스트는 각 게이트의
판정 로직과 경계/데이터부족 안전성만 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.core.strategy.closing_bet_filters import (
    body_new_high,
    disparity_5ma,
    disparity_yellow,
    envelope_upper_break,
    liquidity_ok,
    overheat_warning,
    remaining_upside_ratio,
    triple_factor_buy,
)
from backend.models.market import MarketType, OHLCV

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _c(close: float, *, high: float | None = None, vol: float = 1.0) -> OHLCV:
    return OHLCV(
        symbol="000000",
        timestamp=_TS,
        open=close,
        high=high if high is not None else close,
        low=close,
        close=close,
        volume=vol,
        market_type=MarketType.STOCK,
    )


# ── body_new_high (R6: 몸통 신고가) ──
def test_body_new_high_true_on_close_breakout():
    candles = [_c(100 + i * 0.0) for i in range(60)] + [_c(101)]  # 60 prior @100, today 101
    assert body_new_high(candles, lookback=60) is True


def test_body_new_high_false_when_wick_high_but_body_not():
    # 직전 봉이 윗꼬리로 high=200이지만 종가는 100 → 오늘 종가 150이면 몸통 신고가 True
    prior = [_c(100, high=200) for _ in range(60)]
    assert body_new_high(prior + [_c(150)], lookback=60) is True
    # 오늘 종가 90이면 몸통 신고가 아님
    assert body_new_high(prior + [_c(90)], lookback=60) is False


def test_body_new_high_insufficient_data_false():
    assert body_new_high([_c(100)] * 10, lookback=60) is False


# ── overheat_warning (D-R24: 5일전 종가 × 1.6) ──
def test_overheat_warning_true():
    # 5일전 종가 100, 오늘 종가 170 (>160)
    candles = [_c(100), _c(110), _c(120), _c(130), _c(140), _c(170)]
    assert overheat_warning(candles, mult=1.6, lookback=5) is True


def test_overheat_warning_false_below_threshold():
    candles = [_c(100), _c(110), _c(120), _c(130), _c(140), _c(150)]  # 150 < 160
    assert overheat_warning(candles, mult=1.6, lookback=5) is False


def test_overheat_warning_insufficient_data_false():
    assert overheat_warning([_c(100), _c(200)], lookback=5) is False


# ── liquidity_ok (D-R29/30: 1분봉 ≥15억 ∧ 거래량 ≥ 전일×3) ──
def test_liquidity_ok_both_pass():
    assert liquidity_ok(2.0e9, day_volume=300, prev_day_volume=100) is True


def test_liquidity_ok_fails_on_low_min1_value():
    assert liquidity_ok(1.0e9, day_volume=300, prev_day_volume=100) is False


def test_liquidity_ok_fails_on_low_volume_ratio():
    assert liquidity_ok(2.0e9, day_volume=200, prev_day_volume=100) is False


def test_liquidity_ok_prev_zero_false():
    assert liquidity_ok(2.0e9, day_volume=300, prev_day_volume=0) is False


# ── remaining_upside_ratio (D-R14) ──
def test_remaining_upside_ratio_mid():
    # 저점 100, 고점 200, 현재 150 → 잔존 0.5
    assert remaining_upside_ratio(150, target_high=200, base_low=100) == 0.5


def test_remaining_upside_ratio_clips():
    assert remaining_upside_ratio(50, target_high=200, base_low=100) == 1.0   # below base → clip 1.0
    assert remaining_upside_ratio(250, target_high=200, base_low=100) == 0.0  # above target → clip 0.0


def test_remaining_upside_ratio_invalid_span_none():
    assert remaining_upside_ratio(150, target_high=100, base_low=100) is None


# ── envelope_upper_break (D-R42: 20MA ±20% 상단 돌파) ──
def test_envelope_upper_break_true():
    candles = [_c(100) for _ in range(19)] + [_c(121)]  # SMA20=101.05, upper=121.26? → use 130
    candles = [_c(100) for _ in range(20)] + [_c(130)]  # SMA20=100, upper=120, 130>120
    assert envelope_upper_break(candles, ma_period=20, env_pct=0.20) is True


def test_envelope_upper_break_false_below_band():
    candles = [_c(100) for _ in range(20)] + [_c(119)]  # SMA20≈100.9, upper≈121, 119<121
    assert envelope_upper_break(candles, ma_period=20, env_pct=0.20) is False


def test_envelope_upper_break_insufficient_data_false():
    assert envelope_upper_break([_c(100)] * 5, ma_period=20) is False


# ── disparity_5ma / disparity_yellow (D-R43) ──
def test_disparity_5ma_value():
    candles = [_c(100), _c(100), _c(100), _c(100), _c(130)]  # SMA5=106, (130-106)/106
    d = disparity_5ma(candles, ma_period=5)
    assert d is not None and abs(d - (130 - 106) / 106) < 1e-9


def test_disparity_5ma_insufficient_none():
    assert disparity_5ma([_c(100), _c(100)], ma_period=5) is None


def test_disparity_yellow_true_and_false():
    hot = [_c(100), _c(100), _c(100), _c(100), _c(130)]   # ~+22.6%
    cool = [_c(100), _c(100), _c(100), _c(100), _c(105)]  # ~+4.0%
    assert disparity_yellow(hot, threshold=0.1425) is True
    assert disparity_yellow(cool, threshold=0.1425) is False


# ── triple_factor_buy (D-R44: 엔벨 ∧ 이격 ∧ 거래대금) ──
def _triple_candles():
    return [_c(100) for _ in range(19)] + [_c(130)]  # SMA20=101.5→upper122.85? 130>; SMA5=106→+22.6%


def test_triple_factor_buy_all_pass():
    assert triple_factor_buy(_triple_candles(), day_value_won=2.0e11) is True


def test_triple_factor_buy_fails_on_value_floor():
    assert triple_factor_buy(_triple_candles(), day_value_won=5.0e10) is False


def test_triple_factor_buy_fails_when_not_strong():
    flat = [_c(100) for _ in range(20)] + [_c(101)]  # 엔벨·이격 모두 미달
    assert triple_factor_buy(flat, day_value_won=2.0e11) is False


# ── inert 보장: 모듈이 어떤 전략/스캐너에도 import 되지 않음 ──
def test_module_is_inert_not_imported_by_live_path():
    import backend.core.scanner.signal_scanner as sc
    import backend.core.strategy.closing_bet as cb
    src = (sc.__file__, cb.__file__)
    for f in src:
        with open(f, encoding="utf-8") as fh:
            assert "closing_bet_filters" not in fh.read(), (
                "closing_bet_filters 가 라이브 경로에 연결됨 — inert 위반"
            )
