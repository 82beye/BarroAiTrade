"""BAR-OPS-09 Phase 3 — BlueLine 변동성 필터 단위 테스트.

f_zone TestFZoneVolatilityFilter 와 동일 패턴 — ATR% < min_atr_pct 종목 거부.

운영 진입점(SignalScanner via orchestrator·signals API)에서 명시 override
`BlueLineParams(min_atr_pct=0.035)` 적용 시 저변동·고가주 가짜 시그널 차단.
default 0.0 은 기존 회귀 보존.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta

from backend.core.strategy.blue_line import BlueLineParams, BlueLineStrategy
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import AnalysisContext


class TestBlueLineVolatilityFilter:
    """Phase 3 변동성 필터 — ATR% < min_atr_pct 종목 거부."""

    def _candles(self, atr_target_pct: float, n: int = 70):
        """원하는 ATR% 가 나오도록 합성 캔들 생성."""
        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        base = 1000
        tr = base * atr_target_pct  # high-low 폭 = target ATR%
        for i in range(n):
            out.append(OHLCV(
                symbol="TEST",
                timestamp=t0 + timedelta(days=i),
                open=base, high=base + tr / 2, low=base - tr / 2, close=base,
                volume=10000, market_type=MarketType.STOCK,
            ))
        return out

    def test_atr_pct_static_computation(self):
        """_atr_pct 정적 계산 — 합성 캔들 ATR% 약 5% 예상."""
        candles = self._candles(0.05)
        atr = BlueLineStrategy._atr_pct(candles, n=14)
        assert 0.04 <= atr <= 0.06, f"atr={atr}, ~5% 예상"

    def test_low_atr_rejected_when_filter_enabled(self):
        """명시 override min_atr_pct=0.035 시 ATR% < 3.5% 종목 진입 거부.

        운영 진입점(orchestrator.py:255, signals.py:67)에서 명시 적용 — Phase 3 효과 유지.
        default 는 0.0 (기존 회귀 보존).
        """
        s = BlueLineStrategy(BlueLineParams(min_atr_pct=0.035))
        # ATR% 약 2% — 명시 임계 3.5% 미만
        candles = self._candles(0.02, n=70)
        ctx = AnalysisContext(symbol="LOW_VOL", candles=candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "저변동 종목 진입 거부 실패 (명시 override)"

    def test_default_filter_disabled(self):
        """default min_atr_pct=0.0 — 기존 회귀 보존."""
        s = BlueLineStrategy()
        assert s.params.min_atr_pct == 0.0, (
            "default min_atr_pct 가 0 이 아님 — 기존 회귀 깨질 위험"
        )

    def test_default_atr_n_is_14(self):
        """default atr_n=14 — f_zone 과 동일 표준."""
        s = BlueLineStrategy()
        assert s.params.atr_n == 14


class TestBlueLineEntryTimeGate:
    """BAR-OPS-09 Phase 8f — BlueLine 진입 시간 게이트 (Phase 8c/8d/8e 동일 패턴)."""

    def _candles_at(self, hour: int, minute: int, n: int = 70):
        out = []
        t0 = datetime(2026, 5, 22, hour, minute)
        for i in range(n):
            out.append(OHLCV(
                symbol="TEST",
                timestamp=t0 + timedelta(minutes=i),
                open=1000, high=1010, low=990, close=1000,
                volume=10000, market_type=MarketType.STOCK,
            ))
        return out

    def test_default_no_time_gate(self):
        """default entry_time_cutoff=None — 기존 회귀 보존."""
        s = BlueLineStrategy()
        assert s.params.entry_time_cutoff is None

    def test_late_entry_blocked_with_cutoff_14_00(self):
        """cutoff=14:00 시 마지막 candle 시각 >= 14:00 입력 차단."""
        s = BlueLineStrategy(BlueLineParams(entry_time_cutoff=dtime(14, 0)))
        late_candles = self._candles_at(13, 0, 70)
        assert late_candles[-1].timestamp.time() >= dtime(14, 0)
        ctx = AnalysisContext(symbol="LATE", candles=late_candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "장 후반 진입 차단 실패"
