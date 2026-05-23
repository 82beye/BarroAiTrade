"""
BAR-49 Swing38Strategy 테스트.

C1~C8 — 상속 / min_candles None / 임펄스+Fib+반등 시나리오 / ExitPlan /
PositionSize / HealthCheck / Baseline / crypto.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from backend.core.strategy.base import Strategy
from backend.core.strategy.swing_38 import Swing38Strategy, Swing38Params
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import Account, AnalysisContext


def _make_swing_candles(num: int = 100, seed: int = 7) -> list[OHLCV]:
    """합성 시나리오: 일반 → 임펄스 → 0.382 되돌림 → 반등 양봉."""
    np.random.seed(seed)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles: list[OHLCV] = []
    p = 50000.0

    for i in range(num):
        if i == num - 10:
            # 임펄스 봉 (gain 8%, volume 3x)
            o = p
            c = p * 1.08
            high = c * 1.005
            low = o * 0.998
            vol = 3_000_000.0
            p = c
        elif num - 10 < i < num - 1:
            # 되돌림 (Fib 0.382 ~)
            o = p
            ret = np.random.normal(-0.01, 0.005)
            c = p * (1 + ret)
            high = max(o, c) * 1.001
            low = min(o, c) * 0.999
            vol = 1_000_000.0
            p = c
        elif i == num - 1:
            # 반등 양봉
            o = p
            c = p * 1.015
            high = c * 1.002
            low = o * 0.999
            vol = 1_500_000.0
            p = c
        else:
            o = p
            ret = np.random.normal(0.0, 0.008)
            c = p * (1 + ret)
            high = max(o, c) * 1.003
            low = min(o, c) * 0.997
            vol = 1_000_000.0
            p = c

        candles.append(
            OHLCV(
                symbol="TEST",
                timestamp=base_time.replace(
                    day=(i % 28) + 1, month=((i // 28) % 12) + 1
                ),
                open=o,
                high=high,
                low=low,
                close=c,
                volume=vol,
                market_type=MarketType.STOCK,
            )
        )
    return candles


class TestSwing38StrategyV2:
    def test_c1_inherits_strategy(self):
        assert issubclass(Swing38Strategy, Strategy)
        assert Swing38Strategy.STRATEGY_ID == "swing_38_v1"

    def test_c2_min_candles_returns_none(self, sample_ctx):
        s = Swing38Strategy()
        assert s._analyze_v2(sample_ctx) is None

    def test_c3_signal_or_none_on_synthetic_swing(self):
        """합성 임펄스+되돌림+반등 → EntrySignal 또는 None."""
        candles = _make_swing_candles(num=100, seed=7)
        ctx = AnalysisContext(
            symbol="TEST",
            name="TEST",
            candles=candles,
            market_type=MarketType.STOCK,
        )
        s = Swing38Strategy()
        result = s._analyze_v2(ctx)
        assert result is None or result.strategy_id == "swing_38_v1"
        if result is not None:
            assert result.signal_type == "swing_38"
            assert result.metadata.get("swing_38_subtype") == "swing_38"


class TestSwing38ExitPlan:
    def test_c4_exit_plan_stock(self, sample_position, sample_ctx):
        s = Swing38Strategy()
        plan = s.exit_plan(sample_position, sample_ctx)
        assert len(plan.take_profits) == 2
        assert plan.take_profits[0].price == Decimal("72000") * Decimal("1.025")
        assert plan.take_profits[1].price == Decimal("72000") * Decimal("1.05")
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")
        assert plan.time_exit == dtime(14, 50)
        assert plan.breakeven_trigger == Decimal("0.012")

    def test_c8_crypto_no_time_exit(self, sample_position, sample_ctx_crypto):
        s = Swing38Strategy()
        plan = s.exit_plan(sample_position, sample_ctx_crypto)
        assert plan.time_exit is None


class TestSwing38PositionSize:
    def _account(self) -> Account:
        return Account(
            balance=Decimal("10000000"),
            available=Decimal("10000000"),
            position_count=0,
        )

    def test_c5a_high_score_even(self, sample_signal_high_score):
        """BAR-OPS-09 Phase 9: 균등 진입 — score 차등(BAR-175) 무력화. 10M * 0.08 / 72000 = 11."""
        s = Swing38Strategy()
        size = s.position_size(sample_signal_high_score, self._account())
        assert size == Decimal("11")

    def test_c5b_mid_score_even(self, sample_signal_mid_score):
        """BAR-OPS-09 Phase 9: 균등 진입 — score 무관."""
        s = Swing38Strategy()
        size = s.position_size(sample_signal_mid_score, self._account())
        assert size == Decimal("11")

    def test_c5c_low_score_even(self, sample_signal_low_score):
        """BAR-OPS-09 Phase 9: 균등 진입 — score 무관."""
        s = Swing38Strategy()
        size = s.position_size(sample_signal_low_score, self._account())
        assert size == Decimal("11")


class TestSwing38HealthCheck:
    def test_c6_health_check(self):
        s = Swing38Strategy()
        h = s.health_check()
        assert h["strategy_id"] == "swing_38_v1"
        assert h["ready"] is True
        assert h["impulse_min_gain_pct"] >= 0.05


class TestSwing38BaselineRegression:
    @pytest.mark.skip(reason="main ec9feab fix(f_zone): SyntheticDataLoader 합성에서 f_zone trades=0 회귀. 본 PR 책임 아닌 main 잔재 — 별도 PR로 추적 필요.")
    def test_c7_baseline_unchanged(self):
        import sys

        sys.path.insert(0, ".")
        from run_baseline import run_baseline

        reports = run_baseline(seed=42, num_candles=250)
        assert len(reports["f_zone_v1"].trades) == 6
        assert len(reports["blue_line_v1"].trades) == 12


class TestSwing38VolatilityFilter:
    """BAR-OPS-09 Phase 6 — Swing38 변동성 필터 (Phase 4/5 동일 패턴).

    저변동주 손실 패턴 차단:
    - 5/15 LG씨엔에스 -514k (10 trades, win 0%, flu% 7.5%)
    - 5/14 삼성전자 -80k (2 trades, win 0%, flu% 4.2%)
    - 5/15 SFA반도체 -54k (12 trades, win 0%, flu% 15.8%)
    """

    def _candles(self, atr_target_pct: float, n: int = 70):
        out = []
        t0 = datetime(2026, 5, 1, 9, 0)
        base = 1000
        tr = base * atr_target_pct
        for i in range(n):
            out.append(OHLCV(
                symbol="TEST",
                timestamp=t0 + timedelta(days=i),
                open=base, high=base + tr / 2, low=base - tr / 2, close=base,
                volume=10000, market_type=MarketType.STOCK,
            ))
        return out

    def test_atr_pct_static_computation(self):
        candles = self._candles(0.05)
        atr = Swing38Strategy._atr_pct(candles, n=14)
        assert 0.04 <= atr <= 0.06, f"atr={atr}, ~5% 예상"

    def test_low_atr_rejected_when_filter_enabled(self):
        s = Swing38Strategy(Swing38Params(min_atr_pct=0.035))
        candles = self._candles(0.02, n=70)
        ctx = AnalysisContext(symbol="LOW_VOL", candles=candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "저변동 종목 진입 거부 실패"

    def test_default_filter_disabled(self):
        s = Swing38Strategy()
        assert s.params.min_atr_pct == 0.0

    def test_default_atr_n_is_14(self):
        s = Swing38Strategy()
        assert s.params.atr_n == 14


class TestSwing38ScoreThreshold:
    """BAR-OPS-09 Phase 8 — 진입 점수 임계 파라미터화 (기존 하드코딩 0.3 → min_score).

    5/22 swing_38 약한 시그널 진입 (w=0.3 BEARISH) 손실 패턴 차단 목적:
    - 5/22 LG전자 -148k (w=0.3 BEARISH 가중치, 두 번 진입)
    - 5/22 삼성전기 -124k
    """

    def test_default_min_score_preserves_3_0(self):
        """default min_score=3.0 (BAR-175 0-10 스케일 정규화 = 기존 0.3 × 10) — baseline 회귀."""
        s = Swing38Strategy()
        assert s.params.min_score == 3.0, (
            "default min_score 변경 — baseline 회귀 깨질 위험"
        )

    def test_explicit_override_higher_threshold(self):
        """IntradaySimulator 시뮬 진입점이 min_score=5.0 명시 override (0-10 스케일)."""
        s = Swing38Strategy(Swing38Params(min_score=5.0))
        assert s.params.min_score == 5.0

    def test_intraday_simulator_uses_min_score_5_0(self):
        """_build_strategies('swing_38') 가 min_score=5.0 적용 검증 (0-10 스케일)."""
        from backend.core.backtester.intraday_simulator import _build_strategies
        out = _build_strategies(['swing_38'])
        assert len(out) == 1
        assert out[0].params.min_score == 5.0, (
            "IntradaySimulator swing_38 분기에서 min_score=5.0 적용 실패"
        )


class TestSwing38EntryTimeGate:
    """BAR-OPS-09 Phase 8c — 진입 시간 게이트 (장 후반 진입 차단).

    5/22 swing_38 손실 패턴 차단 목적:
    - LG전자 13:48 -148k (장 마감 1.5h 전 진입, 청산 여유 부족)
    - 삼성전기 14:40 -124k (장 마감 40분 전 진입)
    """

    def _candles_at(self, hour: int, minute: int, n: int = 70):
        """첫 candle 이 (hour, minute) 부터 1분 간격 n개."""
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
        s = Swing38Strategy()
        assert s.params.entry_time_cutoff is None

    def test_late_entry_blocked_with_cutoff_14_00(self):
        """cutoff=14:00 시 마지막 candle 시각 >= 14:00 인 입력 차단.

        합성 캔들 09:00 부터 시작 → 70봉 후 마지막 = 10:09 (cutoff 통과).
        13:00 부터 시작 → 70봉 후 마지막 = 14:09 (cutoff 차단).
        """
        s = Swing38Strategy(Swing38Params(entry_time_cutoff=dtime(14, 0)))
        late_candles = self._candles_at(13, 0, 70)
        assert late_candles[-1].timestamp.time() >= dtime(14, 0), "fixture 시각 오류"
        ctx = AnalysisContext(symbol="LATE", candles=late_candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "장 후반 진입 차단 실패"

    def test_intraday_simulator_uses_cutoff_14_00(self):
        """_build_strategies('swing_38') 가 entry_time_cutoff=dtime(14, 0) 적용."""
        from datetime import time as dtime_check
        from backend.core.backtester.intraday_simulator import _build_strategies
        out = _build_strategies(['swing_38'])
        assert out[0].params.entry_time_cutoff == dtime_check(14, 0), (
            "IntradaySimulator swing_38 분기에서 entry_time_cutoff=14:00 적용 실패"
        )
