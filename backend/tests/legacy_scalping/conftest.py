"""
backend/tests/legacy_scalping/conftest.py — BAR-41 어댑터 전용 fixture.

ai-trade의 ScalpingAnalysis dataclass 와 dict 형태 시그널의 sample 데이터.
무거운 pandas/numpy 의존을 격리하기 위해 root conftest 에서 분리 (BAR-45 시점).

Reference: backend/legacy_scalping/strategy/scalping_team/base_agent.py:57
"""

from __future__ import annotations

import pytest

from backend.legacy_scalping.strategy.scalping_team.base_agent import (
    ScalpingAnalysis,
    StockSnapshot,
)


@pytest.fixture
def sample_stock_snapshot() -> StockSnapshot:
    return StockSnapshot(
        code="005930",
        name="삼성전자",
        price=72000.0,
        open=71500.0,
        high=72500.0,
        low=71000.0,
        prev_close=71200.0,
        volume=15_000_000,
        change_pct=1.12,
        trade_value=1_080_000_000_000.0,
        volume_ratio=1.5,
        category="강세주",
        score=85.0,
    )


@pytest.fixture
def sample_scalping_analysis(sample_stock_snapshot: StockSnapshot) -> ScalpingAnalysis:
    return ScalpingAnalysis(
        code="005930",
        name="삼성전자",
        rank=1,
        total_score=85.0,
        confidence=0.78,
        timing="즉시",
        consensus_level="다수합의",
        optimal_entry_price=72000.0,
        scalp_tp_pct=3.0,
        scalp_sl_pct=-3.0,
        hold_minutes=15,
        top_reasons=["VWAP 돌파", "거래량 폭증", "골든타임 진입"],
        surge_type="intraday",
        intraday_atr=850.0,
        snapshot=sample_stock_snapshot,
    )


@pytest.fixture
def sample_legacy_dict() -> dict:
    return {
        "code": "005930",
        "name": "삼성전자",
        "price": 72000.0,
        "total_score": 85.0,
        "timing": "즉시",
        "consensus_level": "다수합의",
        "confidence": 0.78,
        "scalp_tp_pct": 3.0,
        "scalp_sl_pct": -3.0,
        "hold_minutes": 15,
        "top_reasons": ["VWAP 돌파", "거래량 폭증"],
    }
