"""
backend/tests 공통 fixture (BAR-45).

BAR-45: AnalysisContext / OHLCV / Position / EntrySignal sample.
BAR-41 의 ScalpingAnalysis fixture 는 backend/tests/legacy_scalping/conftest.py 로 분리
(무거운 pandas/numpy 의존 격리).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.models.market import MarketType, OHLCV
from backend.models.position import Position
from backend.models.signal import EntrySignal
from backend.models.strategy import AnalysisContext


# === BAR-45: Strategy v2 fixtures ===

@pytest.fixture
def sample_candles() -> list[OHLCV]:
    """간단한 OHLCV 캔들 5개 (KRX, MarketType.STOCK)."""
    base_time = datetime(2026, 5, 1, tzinfo=timezone.utc)
    prices = [70000.0, 70500.0, 71000.0, 71500.0, 72000.0]
    return [
        OHLCV(
            symbol="005930",
            timestamp=base_time.replace(day=i + 1),
            open=p - 100,
            high=p + 200,
            low=p - 200,
            close=p,
            volume=1_000_000.0 + i * 100_000,
            market_type=MarketType.STOCK,
        )
        for i, p in enumerate(prices)
    ]


@pytest.fixture
def sample_ctx(sample_candles: list[OHLCV]) -> AnalysisContext:
    """Strategy v2 진입점 컨텍스트."""
    return AnalysisContext(
        symbol="005930",
        name="삼성전자",
        candles=sample_candles,
        market_type=MarketType.STOCK,
    )


@pytest.fixture
def sample_signal() -> EntrySignal:
    return EntrySignal(
        symbol="005930",
        name="삼성전자",
        price=72000.0,
        signal_type="f_zone",
        score=0.85,
        reason="test",
        market_type=MarketType.STOCK,
        strategy_id="test_v1",
        timestamp=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_position() -> Position:
    return Position(
        symbol="005930",
        name="삼성전자",
        quantity=100.0,
        avg_price=72000.0,
        current_price=72500.0,
        realized_pnl=0.0,
        unrealized_pnl=50000.0,
        pnl_pct=0.0069,
        market_type=MarketType.STOCK,
        entry_time=datetime.now(timezone.utc),
        strategy_id="test_v1",
    )
