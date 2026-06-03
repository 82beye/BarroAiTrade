"""RSI / HTF 리샘플 헬퍼 단위 테스트 (BAR-OPS-10, 2026-06-03).

핵심 검증:
  - compute_rsi 가 기존 TechnicalIndicators.calculate_rsi 와 **수치 동일**(단일 소스).
  - resample_htf 가 장마감 격자 불연속(…151500→153000)에서도 벽시계 버킷으로 정확.
  - htf_rsi_at 가 형성 중 HTF봉을 drop → 룩어헤드 없음(같은 버킷 내 값 불변, 닫힐 때만 전진).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import pytest

from backend.core.scanner.indicators import TechnicalIndicators
from backend.core.strategy.indicators import (
    compute_rsi,
    htf_rsi_at,
    htf_rsi_confirms_exit,
    htf_rsi_confirms_long,
    resample_htf,
    rsi_cross_state,
    rsi_signal_line,
)
from backend.models.market import MarketType, OHLCV


# ── 빌더 ──────────────────────────────────────────────────────────────────────
def _bars(closes, *, start=datetime(2026, 5, 14, 9, 0), step_min=5, symbol="005930"):
    """close 리스트 → 5분봉 OHLCV(연속 step_min 분 간격)."""
    out = []
    for i, c in enumerate(closes):
        out.append(OHLCV(
            symbol=symbol, timestamp=start + timedelta(minutes=step_min * i),
            open=c, high=c * 1.002, low=c * 0.998, close=c,
            volume=1000 + i, market_type=MarketType.STOCK,
        ))
    return out


def _bar_at(t: str, o, h, lo, c, v=1000, *, date="20260514", symbol="005930"):
    """HHMMSS 시각 지정 단일 봉."""
    ts = datetime.strptime(date + t, "%Y%m%d%H%M%S")
    return OHLCV(symbol=symbol, timestamp=ts, open=o, high=h, low=lo, close=c,
                 volume=v, market_type=MarketType.STOCK)


# ── compute_rsi parity (단일 소스 보장) ──────────────────────────────────────
def test_compute_rsi_parity_with_calculate_rsi():
    closes = [10000 + 200 * math.sin(i / 3.0) + 5 * i for i in range(80)]
    mine = compute_rsi(_bars(closes), period=14)
    ref = TechnicalIndicators.calculate_rsi(np.array(closes), period=14)
    assert len(mine) == len(ref) == 80
    for a, b in zip(mine, ref):
        assert abs(a - b) < 1e-9


def test_compute_rsi_parity_period9():
    closes = [5000 + 80 * math.cos(i / 2.0) - 3 * i for i in range(60)]
    mine = compute_rsi(_bars(closes), period=9)
    ref = TechnicalIndicators.calculate_rsi(np.array(closes), period=9)
    for a, b in zip(mine, ref):
        assert abs(a - b) < 1e-9


def test_compute_rsi_boundaries():
    # 데이터 부족 → 전부 50
    assert compute_rsi(_bars([100, 101, 102]), period=14) == [50.0, 50.0, 50.0]
    # 첫 period 봉은 50.0
    rsi = compute_rsi(_bars([100 + i for i in range(40)]), period=14)
    assert all(v == 50.0 for v in rsi[:14])
    # 단조 상승 → RSI 100 수렴
    assert rsi[-1] == pytest.approx(100.0, abs=1e-6)
    # 평탄 → ~50 (변화 없음 → up=down=0 → down==0 → 100? 평탄은 delta=0 전부 → seed up=0,down=0)
    flat = compute_rsi(_bars([7000] * 40), period=14)
    # delta 전부 0: up>=0 에 0 포함되어 up=0, down=0 → down==0 → rsi=100 (분모 0 규약)
    assert flat[-1] in (50.0, 100.0)  # 규약상 down==0 → 100; calculate_rsi 와 동일해야


def test_compute_rsi_flat_matches_reference():
    flat = [7000] * 40
    mine = compute_rsi(_bars(flat), period=14)
    ref = TechnicalIndicators.calculate_rsi(np.array(flat, dtype=float), period=14)
    for a, b in zip(mine, ref):
        assert abs(a - b) < 1e-9


# ── 시그널선 ──────────────────────────────────────────────────────────────────
def test_rsi_signal_line_sma():
    rsi = [float(x) for x in range(1, 11)]   # 1..10
    sig = rsi_signal_line(rsi, signal_period=3)
    assert math.isnan(sig[0]) and math.isnan(sig[1])
    assert sig[2] == pytest.approx((1 + 2 + 3) / 3)
    assert sig[3] == pytest.approx((2 + 3 + 4) / 3)
    assert sig[-1] == pytest.approx((8 + 9 + 10) / 3)


# ── 크로스 판정 ───────────────────────────────────────────────────────────────
def test_rsi_cross_state_signal_cross():
    # rsi 가 signal 을 인덱스 2 에서 상향 돌파, 인덱스 4 에서 하향 돌파
    rsi = [40.0, 45.0, 55.0, 60.0, 48.0]
    sig = [50.0, 50.0, 50.0, 50.0, 50.0]
    golden, dead = rsi_cross_state(rsi, sig, mode="signal_cross")
    assert golden == [False, False, True, False, False]
    assert dead == [False, False, False, False, True]


def test_rsi_cross_state_centerline():
    rsi = [48.0, 49.0, 51.0, 52.0, 47.0]
    golden, dead = rsi_cross_state(rsi, mode="centerline", centerline=50.0)
    assert golden[2] is True and dead[4] is True
    assert golden[0] is False and golden[1] is False


def test_rsi_cross_state_level():
    rsi = [40.0, 55.0, 70.0, 95.0]
    golden, dead = rsi_cross_state(rsi, mode="level", min_level=50.0, max_level=90.0)
    # golden = 50<=rsi<=90 : idx1(55),idx2(70) True; idx0(40)<50 False; idx3(95)>90 False
    assert golden == [False, True, True, False]
    # dead = rsi<50 : idx0 True
    assert dead == [True, False, False, False]


# ── 리샘플 (장마감 격자 불연속 회귀) ─────────────────────────────────────────
def test_resample_htf_10m_eod_discontinuity():
    # 연속장 끝(151000,151500) → 단일가 점프(153000,153500). 152000/152500 없음.
    bars = [
        _bar_at("150000", 100, 105, 99, 101),
        _bar_at("150500", 101, 106, 100, 102),   # bucket36 = {150000,150500}
        _bar_at("151000", 102, 108, 101, 103),
        _bar_at("151500", 103, 110, 102, 104),   # bucket37 = {151000,151500}
        _bar_at("153000", 104, 111, 103, 105),
        _bar_at("153500", 105, 112, 104, 107),   # bucket39 = {153000,153500}, bucket38 공백
    ]
    htf = resample_htf(bars, tf_mult=2)
    assert len(htf) == 3   # bucket36, 37, 39 (38 은 단일가 갭으로 없음)
    # 마지막 10분봉(종가 단일가 2프린트 병합) 검증
    last = htf[-1]
    assert last.timestamp == datetime(2026, 5, 14, 15, 35)   # 버킷 마지막 5분봉 ts
    assert last.open == 104           # 153000.open
    assert last.close == 107          # 153500.close
    assert last.high == 112           # max(111,112)
    assert last.low == 103            # min(103,104)
    assert last.volume == 2000        # 합
    # 중간 버킷(37) 도 정확
    assert htf[1].timestamp == datetime(2026, 5, 14, 15, 15)
    assert htf[1].open == 102 and htf[1].close == 104


def test_resample_htf_passthrough():
    bars = _bars([100, 101, 102, 103])
    out = resample_htf(bars, tf_mult=1)
    assert len(out) == 4
    assert [b.close for b in out] == [100, 101, 102, 103]


def test_resample_htf_full_day_bucket_count():
    # 정상일 78봉(09:00~151500 연속 76봉 + 153000,153500 종가 2봉) → 10분 39버킷
    times = []
    t = datetime(2026, 5, 14, 9, 0)
    while t <= datetime(2026, 5, 14, 15, 15):
        times.append(t)
        t += timedelta(minutes=5)
    times += [datetime(2026, 5, 14, 15, 30), datetime(2026, 5, 14, 15, 35)]
    bars = [OHLCV(symbol="005930", timestamp=ts, open=100, high=101, low=99,
                  close=100, volume=10, market_type=MarketType.STOCK) for ts in times]
    assert len(bars) == 78
    htf = resample_htf(bars, tf_mult=2)
    assert len(htf) == 39   # 버킷 0~37(38개) + 버킷 39(1개), 버킷 38 공백


# ── 룩어헤드 제거 (형성 중 봉 drop / 안정성) ──────────────────────────────────
def test_htf_rsi_at_no_lookahead_stability():
    # 09:00 부터 5분봉. 10분(tf_mult=2) HTF 정렬.
    closes = [100 + i for i in range(12)]   # 09:00~09:55
    bars = _bars(closes)   # idx0=09:00, idx1=09:05, idx2=09:10, idx3=09:15 ...

    # idx1 (09:05): bucket0 닫힘 → 마지막 HTF봉 ts=09:05
    htf_1 = htf_rsi_at(bars, 1, 2)
    assert htf_1[-1].timestamp == datetime(2026, 5, 14, 9, 5)
    assert len(htf_1) == 1

    # idx2 (09:10): bucket1 형성 중 → drop → 마지막 HTF봉 여전히 09:05 (깜빡 없음)
    htf_2 = htf_rsi_at(bars, 2, 2)
    assert htf_2[-1].timestamp == datetime(2026, 5, 14, 9, 5)
    assert len(htf_2) == 1
    assert htf_2[-1].close == htf_1[-1].close   # 값 불변(룩어헤드 없음)

    # idx3 (09:15): bucket1 닫힘 → HTF봉 1개 전진 → ts=09:15
    htf_3 = htf_rsi_at(bars, 3, 2)
    assert htf_3[-1].timestamp == datetime(2026, 5, 14, 9, 15)
    assert len(htf_3) == 2


def test_htf_rsi_at_passthrough_5m():
    bars = _bars([100 + i for i in range(6)])
    out = htf_rsi_at(bars, 4, 1)   # tf_mult=1 → passthrough, 항상 완성
    assert len(out) == 5
    assert out[-1].timestamp == bars[4].timestamp


# ── confirms 래퍼 (데이터 부족 = 보수적 False) ───────────────────────────────
def test_confirms_long_insufficient_data_is_false():
    bars = _bars([100, 101, 102])
    assert htf_rsi_confirms_long(
        bars, i=2, tf_mult=2, period=14, signal_period=9,
        mode="signal_cross", lookback=2, min_level=50.0, max_level=100.0,
    ) is False


def test_confirms_long_centerline_uptrend_true():
    # 충분히 긴 상승 → HTF RSI > 50 → centerline golden 발생
    closes = [100 + i * 2 for i in range(80)]
    bars = _bars(closes)
    ok = htf_rsi_confirms_long(
        bars, i=len(bars) - 1, tf_mult=2, period=14, signal_period=9,
        mode="centerline", lookback=200, min_level=50.0, max_level=100.0,
    )
    assert ok is True


def test_confirms_exit_downtrend_centerline_true():
    # 상승 후 급락 → HTF RSI 50 하향 데드크로스
    closes = [100 + i * 2 for i in range(50)] + [200 - i * 3 for i in range(40)]
    bars = _bars(closes)
    ex = htf_rsi_confirms_exit(
        bars, i=len(bars) - 1, tf_mult=2, period=14, signal_period=9,
        mode="centerline", lookback=200, min_level=50.0, max_level=100.0,
    )
    assert ex is True
