"""dante_filters 순수함수 단위테스트 (주식단테 방법론 게이트 JD-R).

모듈은 관측 전용·inert(호출처 없음)이므로 라이브 무영향. 본 테스트는 각 게이트의
판정 로직과 경계/데이터부족 안전성, 그리고 inert 보장만 검증한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.core.strategy.dante_filters import (
    above_ma224,
    accumulation_candle,
    distribution_alert,
    ma_alignment,
    odori_cross,
    rr_ratio_ok,
    saucer_third_zone,
    sr_flip,
)
from backend.models.market import MarketType, OHLCV

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _c(
    close: float,
    *,
    open: float | None = None,
    high: float | None = None,
    low: float | None = None,
    vol: float = 1.0,
) -> OHLCV:
    o = open if open is not None else close
    return OHLCV(
        symbol="000000",
        timestamp=_TS,
        open=o,
        high=high if high is not None else max(o, close),
        low=low if low is not None else min(o, close),
        close=close,
        volume=vol,
        market_type=MarketType.STOCK,
    )


# ── ma_alignment / above_ma224 (JD-R1/R4) ──
def test_ma_alignment_jeongbaeyeol():
    # 단조 상승 → 단기 EMA > 중기 > 장기 (정배열)
    candles = [_c(100 + i) for i in range(500)]
    assert ma_alignment(candles) == "정배열"


def test_ma_alignment_yeokbaeyeol():
    # 단조 하락 → 역배열
    candles = [_c(700 - i) for i in range(500)]
    assert ma_alignment(candles) == "역배열"


def test_ma_alignment_insufficient_none():
    assert ma_alignment([_c(100)] * 100) is None  # 448 미달


def test_above_ma224_true_false():
    up = [_c(100 + i) for i in range(300)]
    assert above_ma224(up) is True            # 상승 → 종가 > EMA224
    down = [_c(400 - i) for i in range(300)]
    assert above_ma224(down) is False
    assert above_ma224([_c(100)] * 50) is None  # 데이터부족


# ── sr_flip (JD-R7 공구리) ──
def test_sr_flip_detects_breakout():
    # 전고 120 형성 → 하락 100 → 최신봉 종가 125로 전고 상향 돌파 (총 22봉 ≥ lookback+2)
    pre = [_c(110), _c(120, high=120)]               # 언덕(전고 120)
    drop = [_c(108), _c(104), _c(100, low=100)]       # 하락
    base = [_c(102) for _ in range(16)]               # 횡보(직전봉 종가 102 ≤ 120)
    last = _c(125, open=121, high=125)                # 돌파봉
    res = sr_flip(pre + drop + base + [last], pivot_lookback=20)
    assert res is not None
    assert abs(res["flip_price"] - 120) < 1e-9
    assert res["break_open"] == 121
    # target = 120 + (120 - 100) = 140
    assert abs(res["target"] - 140) < 1e-9


def test_sr_flip_none_when_no_breakout():
    # 전고 120 이 lookback 윈도우 안에 있고 최신봉 종가 110 < 120 → 미돌파
    candles = (
        [_c(100) for _ in range(5)]
        + [_c(120, high=120)]
        + [_c(100) for _ in range(15)]
        + [_c(110)]
    )  # 총 22봉
    assert sr_flip(candles, pivot_lookback=20) is None


def test_sr_flip_insufficient_none():
    assert sr_flip([_c(100), _c(101)], pivot_lookback=20) is None


# ── saucer_third_zone (JD-R5 밥그릇 3번) ──
def test_saucer_third_zone_true():
    # EMA224 위로 안착한 추세(첫 224봉 상승) → 이후 224 아래로 깊게 빠져 80봉 횡보 → 강한 돌파
    rise = [_c(50 + i * 0.5) for i in range(224)]      # EMA224 ≈ 100 근처로 끌어올림
    base = [_c(60, low=58) for _ in range(80)]          # 224 한참 아래 횡보
    prev = _c(60)                                       # 직전봉 미돌파
    # ema224는 대략 base 구간서 천천히 내려오지만 여전히 base(60)보다 높음 → 큰 갭업 돌파
    breakout = _c(200, open=190, high=200)
    candles = rise + base + [prev, breakout]
    assert saucer_third_zone(candles, base_min_days=80, breakout_mult=2.0) is True


def test_saucer_third_zone_false_no_base_below():
    candles = [_c(100 + i) for i in range(320)]  # 계속 상승(224 아래 횡보 없음)
    assert saucer_third_zone(candles, base_min_days=80) is False


def test_saucer_third_zone_insufficient_false():
    assert saucer_third_zone([_c(100)] * 100, base_min_days=80) is False


# ── accumulation_candle (JD-R6 매집봉) ──
def test_accumulation_candle_true():
    base = [_c(100, vol=100) for _ in range(20)]
    # 위꼬리 길게: high=130, close=103 → 되돌림 (130-103)/(130-99)=0.87 ; 대량거래
    spike = _c(103, open=100, high=130, low=99, vol=400)
    assert accumulation_candle(base + [spike], retrace_min=0.7, vol_mult=3.0) is True


def test_accumulation_candle_false_low_volume():
    base = [_c(100, vol=100) for _ in range(20)]
    spike = _c(103, open=100, high=130, low=99, vol=150)  # 거래량 1.5배 < 3배
    assert accumulation_candle(base + [spike], vol_mult=3.0) is False


def test_accumulation_candle_false_small_wick():
    base = [_c(100, vol=100) for _ in range(20)]
    spike = _c(128, open=100, high=130, low=99, vol=400)  # 종가 고가 근처 → 위꼬리 짧음
    assert accumulation_candle(base + [spike], retrace_min=0.7) is False


# ── distribution_alert (JD-R13 장대음봉) ──
def test_distribution_alert_true():
    prev = _c(100, vol=100)
    drop = _c(95, open=100, vol=400)  # 음봉 몸통 5%, 거래량 4배
    assert distribution_alert([prev, drop], vol_mult=3.0, body_min=0.03) is True


def test_distribution_alert_false_small_body():
    prev = _c(100, vol=100)
    drop = _c(99, open=100, vol=400)  # 몸통 1% < 3%
    assert distribution_alert([prev, drop], body_min=0.03) is False


def test_distribution_alert_false_low_volume():
    prev = _c(100, vol=100)
    drop = _c(95, open=100, vol=200)  # 거래량 2배 < 3배
    assert distribution_alert([prev, drop], vol_mult=3.0) is False


def test_distribution_alert_false_on_bullish():
    prev = _c(100, vol=100)
    up = _c(105, open=100, vol=400)  # 양봉
    assert distribution_alert([prev, up]) is False


# ── odori_cross (JD-R20 5/15 골든크로스) ──
def test_odori_cross_true():
    # 직전까지 5MA ≤ 15MA, 당봉에서 5MA > 15MA 가 되도록 최근 급등
    candles = [_c(100) for _ in range(15)] + [_c(100), _c(100), _c(100), _c(140)]
    assert odori_cross(candles, short=5, long=15) is True


def test_odori_cross_false_flat():
    candles = [_c(100) for _ in range(20)]
    assert odori_cross(candles) is False


def test_odori_cross_insufficient_false():
    assert odori_cross([_c(100)] * 10, long=15) is False


# ── rr_ratio_ok (JD-R21 손익비) ──
def test_rr_ratio_ok_pass_fail():
    # entry 100, stop 98(위험2), target 106(보상6) → R:R=3 ≥ 2
    assert rr_ratio_ok(100, 98, 106, min_rr=2.0) is True
    # target 103(보상3) → R:R=1.5 < 2
    assert rr_ratio_ok(100, 98, 103, min_rr=2.0) is False


def test_rr_ratio_ok_invalid_risk_none():
    assert rr_ratio_ok(100, 100, 110) is None   # 손절폭 0
    assert rr_ratio_ok(100, 102, 110) is None   # 손절가 > 진입(음수 위험)


# ── 경계 보장: 스캐너(매수 경로)는 dante_filters 를 직접 참조하지 않음 ──
# (distribution 게이트는 청산 경로(holding_evaluator)에만 연결 — 매수 스캐너와 분리)
def test_scanner_does_not_import_dante_filters():
    import backend.core.scanner.signal_scanner as sc
    with open(sc.__file__, encoding="utf-8") as fh:
        assert "dante_filters" not in fh.read(), (
            "스캐너가 dante_filters 를 직접 참조 — 매수 경로와 분리되어야 함"
        )


# ── (d) 배선 default-OFF 보장: 데몬은 DistributionExitConfig 를 import 하나
#    신규 PolicyConfig → 게이트 비활성 → 라이브 청산 무변경(byte-identical) ──
def test_distribution_gate_default_off_via_policy_config():
    from backend.core.journal.policy_config import PolicyConfig
    from backend.core.strategy.dante_filters import DistributionExitConfig
    cfg = DistributionExitConfig.from_policy_config(PolicyConfig())
    assert cfg.enabled is False, "신규 PolicyConfig 는 distribution 게이트 default-OFF 여야 함"
