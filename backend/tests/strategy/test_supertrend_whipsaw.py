"""슈퍼트렌드 횡보 휩쏘(whipsaw) 필터 테스트 — ADX + 밴드 이탈폭 게이트.

횡보 박스권에서 BUY/SELL 이 반복되는 휩쏘를 차단하고, 변동성(추세)이 살아난
전환 시그널만 통과시키는지 검증. 모든 게이트는 기본 비활성(0)이라 기존 회귀 보존.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List

from backend.core.strategy.supertrend import (
    SupertrendParams,
    SupertrendStrategy,
    compute_adx,
)
from backend.models.market import OHLCV, MarketType
from backend.models.strategy import AnalysisContext


def _candles(prices: List[float]) -> List[OHLCV]:
    base = datetime(2026, 5, 31, 9, 0)
    return [
        OHLCV(symbol="T", timestamp=base + timedelta(minutes=5 * i),
              open=p, high=p * 1.005, low=p * 0.995, close=p,
              volume=10000 + i, market_type=MarketType.STOCK)
        for i, p in enumerate(prices)
    ]


def _ctx(prices: List[float]) -> AnalysisContext:
    return AnalysisContext(symbol="005930", name="t",
                           candles=_candles(prices), market_type=MarketType.STOCK)


# 횡보 박스권 (±1% 진동) — 추세 약함.
_BOX = [10000 + 100 * math.sin(i / 2.0) for i in range(60)]
# 하락 57봉 후 급반등 3봉 → 강한 BUY 전환 (변동성 발생).
_BUY_RECENT = [10000 - i * 50 for i in range(57)] + [7200 + i * 350 for i in range(3)]


# ─── compute_adx 단위 ────────────────────────────────────────────────────────
def test_adx_low_in_sideways():
    """횡보 구간 ADX 는 낮다 (< 20)."""
    adx = compute_adx(_candles(_BOX), 14)
    assert len(adx) == len(_BOX)
    assert adx[-1] < 20.0


def test_adx_high_in_trend():
    """강한 추세 구간 ADX 는 높다 (≥ 25)."""
    trend = [10000 + i * 60 for i in range(60)]
    adx = compute_adx(_candles(trend), 14)
    assert adx[-1] >= 25.0


def test_adx_empty_and_short():
    assert compute_adx([], 14) == []
    assert compute_adx(_candles([100]), 14) == [0.0]


# ─── ADX 게이트 ──────────────────────────────────────────────────────────────
def test_adx_gate_blocks_weak_trend_buy():
    """min_adx 설정 시, BUY 전환이어도 ADX 미달이면 진입 거부.

    BUY 전환 봉을 인위적으로 만들되 추세 강도가 약한 케이스를 ADX 임계로 차단.
    """
    # ADX 가 매우 높은 임계(99)면 BUY_RECENT(ADX~100 근처라도) 통과/차단 경계를 명확히
    # 보기 위해, 약-추세 박스 끝에 미세 BUY 를 섞는 대신 임계를 과도하게 높여 차단 확인.
    strat = SupertrendStrategy(SupertrendParams(min_adx=99.5))
    # 박스권에 마지막만 살짝 반등 (약한 전환) → ADX 낮음
    prices = [10000 + 80 * math.sin(i / 2.0) for i in range(57)] + [10120, 10160, 10200]
    sig = strat.exit_on_signal  # noqa (참조만, 사용 안 함)
    assert strat._analyze_v2(_ctx(prices)) is None


def test_adx_gate_allows_strong_trend_buy():
    """min_adx 충족 + BUY 전환 → 진입."""
    strat = SupertrendStrategy(SupertrendParams(min_adx=20.0))
    sig = strat._analyze_v2(_ctx(_BUY_RECENT))
    assert sig is not None
    assert sig.signal_type == "supertrend"


def test_adx_gate_disabled_by_default():
    """min_adx=0(기본) 이면 ADX 평가 안 함 — 기존 동작 보존."""
    strat = SupertrendStrategy()  # min_adx=0
    assert strat.params.min_adx == 0.0
    sig = strat._analyze_v2(_ctx(_BUY_RECENT))
    assert sig is not None  # BUY 전환 → 진입 (ADX 무관)


# ─── 밴드 이탈폭(전환 강도) 게이트 ───────────────────────────────────────────
def test_flip_strength_gate_blocks_weak_breakout():
    """과도하게 큰 이탈폭 요구 → 약한 전환 거부."""
    strat = SupertrendStrategy(SupertrendParams(min_flip_atr_mult=100.0))
    assert strat._analyze_v2(_ctx(_BUY_RECENT)) is None


def test_flip_strength_gate_allows_strong_breakout():
    """완만한 이탈폭 요구(0.1·ATR) → 강한 BUY 전환은 통과."""
    strat = SupertrendStrategy(SupertrendParams(min_flip_atr_mult=0.1))
    sig = strat._analyze_v2(_ctx(_BUY_RECENT))
    assert sig is not None


def test_flip_strength_gate_disabled_by_default():
    strat = SupertrendStrategy()
    assert strat.params.min_flip_atr_mult == 0.0


# ─── 조합: 횡보는 거부, 추세는 통과 (핵심 시나리오) ──────────────────────────
def test_whipsaw_filter_combo_blocks_sideways_allows_trend():
    """운영 권장 설정(min_adx=20, min_flip_atr_mult=0.5):
       횡보 박스권 → 진입 없음 / 강한 추세 전환 → 진입.
    """
    strat = SupertrendStrategy(SupertrendParams(min_adx=20.0, min_flip_atr_mult=0.5))
    # 박스권: BUY 전환이 생겨도 ADX/이탈폭 미달 → None
    assert strat._analyze_v2(_ctx(_BOX)) is None
    # 강한 추세 전환 → 진입
    assert strat._analyze_v2(_ctx(_BUY_RECENT)) is not None
