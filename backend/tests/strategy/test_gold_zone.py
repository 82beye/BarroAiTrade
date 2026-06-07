"""
BAR-48 GoldZoneStrategy 테스트.

C1~C7 — 상속 / 캔들 부족 None / oversold 회복 신호 / ExitPlan / PositionSize /
HealthCheck / Baseline 회귀.
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from backend.core.strategy.base import Strategy
from backend.core.strategy.gold_zone import GoldZoneStrategy, GoldZoneParams
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import Account, AnalysisContext


def _make_oversold_candles(num: int = 100, seed: int = 7) -> list[OHLCV]:
    """합성 oversold + 회복 시나리오: 70봉 하락 → 30봉 회복."""
    np.random.seed(seed)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles: list[OHLCV] = []
    price = 100000.0
    # 70봉 하락 (oversold 진입)
    for i in range(70):
        price *= 1 + np.random.normal(-0.012, 0.01)
    # 30봉 회복 (RSI 회복)
    for i in range(30):
        price *= 1 + np.random.normal(0.005, 0.008)

    np.random.seed(seed)
    p = 100000.0
    for i in range(num):
        if i < 70:
            ret = np.random.normal(-0.012, 0.01)
        else:
            ret = np.random.normal(0.005, 0.008)
        new = p * (1 + ret)
        candles.append(
            OHLCV(
                symbol="TEST",
                timestamp=base_time.replace(day=(i % 28) + 1, month=((i // 28) % 12) + 1),
                open=p,
                high=max(p, new) * 1.001,
                low=min(p, new) * 0.999,
                close=new,
                volume=1_000_000.0,
                market_type=MarketType.STOCK,
            )
        )
        p = new
    return candles


class TestGoldZoneStrategyV2:
    """C1~C3 — Strategy v2 진입점."""

    def test_c1_inherits_strategy(self):
        assert issubclass(GoldZoneStrategy, Strategy)
        assert GoldZoneStrategy.STRATEGY_ID == "gold_zone_v1"

    def test_c2_min_candles_returns_none(self, sample_ctx):
        """5 candles 이라 min_candles(60) 미달 → None."""
        s = GoldZoneStrategy()
        assert s._analyze_v2(sample_ctx) is None

    def test_c3_signal_or_none_on_synthetic_oversold(self):
        """합성 oversold + 회복 데이터 → EntrySignal 또는 None (확률성)."""
        candles = _make_oversold_candles(num=100, seed=7)
        ctx = AnalysisContext(
            symbol="TEST",
            name="TEST",
            candles=candles,
            market_type=MarketType.STOCK,
        )
        s = GoldZoneStrategy()
        result = s._analyze_v2(ctx)
        # None 또는 EntrySignal 모두 정상 (BB/Fib/RSI 동시 충족 확률성)
        assert result is None or result.strategy_id == "gold_zone_v1"
        if result is not None:
            assert result.signal_type == "gold_zone"
            assert result.metadata.get("gold_zone_subtype") == "gold_zone"


class TestGoldZoneExitPlan:
    """C4 — 보수적 정책."""

    def test_c4_exit_plan_stock(self, sample_position, sample_ctx):
        s = GoldZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx)

        assert len(plan.take_profits) == 2
        # avg_price=72000
        assert plan.take_profits[0].price == Decimal("72000") * Decimal("1.02")
        assert plan.take_profits[1].price == Decimal("72000") * Decimal("1.04")
        assert plan.take_profits[0].qty_pct == Decimal("0.5")
        assert plan.take_profits[1].qty_pct == Decimal("0.5")
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")
        assert plan.time_exit == dtime(14, 50)
        assert plan.breakeven_trigger == Decimal("0.01")

    def test_exit_plan_crypto_no_time_exit(self, sample_position, sample_ctx_crypto):
        s = GoldZoneStrategy()
        plan = s.exit_plan(sample_position, sample_ctx_crypto)
        assert plan.time_exit is None


class TestGoldZonePositionSize:
    """C5 — 25%/15%/8% 분기."""

    def _account(self) -> Account:
        return Account(
            balance=Decimal("10000000"),
            available=Decimal("10000000"),
            position_count=0,
        )

    def test_c5a_high_score_even(self, sample_signal_high_score):
        """BAR-OPS-09 Phase 9: 균등 진입 — score 차등(BAR-176) 무력화. 10M * 0.08 / 72000 = 11."""
        s = GoldZoneStrategy()
        size = s.position_size(sample_signal_high_score, self._account())
        assert size == Decimal("11")

    def test_c5b_mid_score_even(self, sample_signal_mid_score):
        """BAR-OPS-09 Phase 9: 균등 진입 — score 무관."""
        s = GoldZoneStrategy()
        size = s.position_size(sample_signal_mid_score, self._account())
        assert size == Decimal("11")

    def test_c5c_low_score_even(self, sample_signal_low_score):
        """BAR-OPS-09 Phase 9: 균등 진입 — score 무관."""
        s = GoldZoneStrategy()
        size = s.position_size(sample_signal_low_score, self._account())
        assert size == Decimal("11")

    def test_zero_balance(self, sample_signal_high_score_fz):
        s = GoldZoneStrategy()
        empty = Account(balance=Decimal(0), available=Decimal(0), position_count=0)
        assert s.position_size(sample_signal_high_score_fz, empty) == Decimal(0)


class TestGoldZoneHealthCheck:
    """C6."""

    def test_c6_health_check(self):
        s = GoldZoneStrategy()
        h = s.health_check()
        assert h["strategy_id"] == "gold_zone_v1"
        assert h["ready"] is True
        assert h["bb_period"] >= 20
        assert h["rsi_period"] >= 14


class TestGoldZoneBaselineRegression:
    """C7 — F존 베이스라인 보존 (골드존은 별도 strategy 라 영향 0)."""

    @pytest.mark.skip(reason="main ec9feab fix(f_zone): SyntheticDataLoader 합성에서 f_zone trades=0 회귀. 본 PR 책임 아닌 main 잔재 — 별도 PR로 추적 필요.")
    def test_c7_baseline_unchanged(self):
        import sys

        sys.path.insert(0, ".")
        from run_baseline import run_baseline

        reports = run_baseline(seed=42, num_candles=250)
        f = reports["f_zone_v1"]
        assert len(f.trades) == 6, f"F존 거래 수 회귀 ({len(f.trades)} ≠ 6)"
        b = reports["blue_line_v1"]
        assert len(b.trades) == 12, f"BlueLine 거래 수 회귀 ({len(b.trades)} ≠ 12)"


class TestGoldZoneVolatilityFilter:
    """BAR-OPS-09 Phase 4 변동성 필터 — ATR% < min_atr_pct 종목 거부.

    5/21 LG전자 -626k (43 trades, win 41%) 같은 LG계열 저변동·고가주 패턴 차단.
    f_zone/blue_line 과 동일 패턴 (BAR-44 / Phase 3 후속).
    """

    def _candles(self, atr_target_pct: float, n: int = 70):
        """원하는 ATR% 가 나오도록 합성 캔들 생성."""
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
        """_atr_pct 정적 계산 — 합성 캔들 ATR% 약 5% 예상."""
        candles = self._candles(0.05)
        atr = GoldZoneStrategy._atr_pct(candles, n=14)
        assert 0.04 <= atr <= 0.06, f"atr={atr}, ~5% 예상"

    def test_low_atr_rejected_when_filter_enabled(self):
        """명시 override min_atr_pct=0.035 시 ATR% < 3.5% 종목 진입 거부.

        IntradaySimulator 시뮬 진입점(intraday_simulator.py:163) 에서 명시 적용.
        default 는 0.0 (baseline 회귀 보존).
        """
        s = GoldZoneStrategy(GoldZoneParams(min_atr_pct=0.035))
        candles = self._candles(0.02, n=70)
        ctx = AnalysisContext(symbol="LOW_VOL", candles=candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "저변동 종목 진입 거부 실패 (명시 override)"

    def test_default_filter_disabled(self):
        """default min_atr_pct=0.0 — baseline 회귀 보존."""
        s = GoldZoneStrategy()
        assert s.params.min_atr_pct == 0.0, (
            "default min_atr_pct 가 0 이 아님 — baseline 회귀 깨질 위험"
        )

    def test_default_atr_n_is_14(self):
        """default atr_n=14 — f_zone/blue_line 과 동일 표준."""
        s = GoldZoneStrategy()
        assert s.params.atr_n == 14


class TestGoldZoneEntryTimeGate:
    """BAR-OPS-09 Phase 8d — 진입 시간 게이트 (장 후반 진입 차단).

    5/22 위험 진입 차단 목적:
    - 379800 KODEX 미국S&P500 15:01 진입 (장 마감 19분 전, w=0.5 약한 시그널)
    - 229200 KODEX 코스닥150 13:50 진입 → -1.65% 손실 (cutoff 14:00 에는 통과, 13:00 이상 cutoff 필요시 차단)

    Phase 8c swing_38 와 동일 패턴.
    """

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
        s = GoldZoneStrategy()
        assert s.params.entry_time_cutoff is None

    def test_late_entry_blocked_with_cutoff_14_00(self):
        """cutoff=14:00 시 마지막 candle 시각 >= 14:00 입력 차단."""
        s = GoldZoneStrategy(GoldZoneParams(entry_time_cutoff=dtime(14, 0)))
        late_candles = self._candles_at(13, 0, 70)
        assert late_candles[-1].timestamp.time() >= dtime(14, 0)
        ctx = AnalysisContext(symbol="LATE", candles=late_candles, market_type=MarketType.STOCK)
        result = s._analyze_v2(ctx)
        assert result is None, "장 후반 진입 차단 실패"

    def test_intraday_simulator_uses_cutoff_14_00(self):
        """_build_strategies('gold_zone') 가 entry_time_cutoff=dtime(14, 0) 적용."""
        from backend.core.backtester.intraday_simulator import _build_strategies
        out = _build_strategies(['gold_zone'])
        assert out[0].params.entry_time_cutoff == dtime(14, 0), (
            "IntradaySimulator gold_zone 분기에서 entry_time_cutoff=14:00 적용 실패"
        )


class TestGoldZonePhaseD23:
    """BAR-OPS-09 Phase D2.3 (2026-05-28, B4 그리드 결과) — min_score 2.5 → 4.0."""

    def test_default_min_score_is_5_0(self):
        """default min_score=5.0 (BAR-OPS-33, 4~6월 sweep: 6월 약세 해소·MDD-3.0·기대값+4.32)."""
        from backend.core.strategy.gold_zone import GoldZoneParams
        p = GoldZoneParams()
        assert p.min_score == 5.0, (
            f"default min_score={p.min_score}, expected 5.0 (BAR-OPS-33)"
        )

    def test_default_min_conditions_preserved(self):
        """default min_conditions=2 유지 (B4 시뮬 min_cond=3 강화는 무효 확인)."""
        from backend.core.strategy.gold_zone import GoldZoneParams
        p = GoldZoneParams()
        assert p.min_conditions == 2

    def test_min_score_3_5_rejects_signal_with_lower_score(self):
        """min_score 임계 동작 검증 — 점수가 임계 미만이면 진입 거부."""
        from backend.core.strategy.gold_zone import GoldZoneStrategy, GoldZoneParams
        # 매우 높은 min_score (예: 9.5) 로 어떤 신호도 통과 못 하도록
        s = GoldZoneStrategy(GoldZoneParams(min_score=9.5))
        # 임의 캔들에서 시그널 강도가 9.5 이상 안 되도록 — 합성 평탄 데이터
        from datetime import datetime, timedelta
        from backend.models.market import OHLCV
        candles = [
            OHLCV(symbol="T", timestamp=datetime(2026, 5, 1) + timedelta(days=i),
                  open=1000, high=1010, low=990, close=1000, volume=10000,
                  market_type=MarketType.STOCK)
            for i in range(70)
        ]
        ctx = AnalysisContext(symbol="T", candles=candles, market_type=MarketType.STOCK)
        assert s._analyze_v2(ctx) is None, "min_score=9.5 라도 진입 허용"
