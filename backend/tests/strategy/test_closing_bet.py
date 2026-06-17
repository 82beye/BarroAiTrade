"""
종가베팅(종베) ClosingBetStrategy 테스트 — thetrading-uplift Increment 1.

검증: 상속·캔들부족 None·진입창 게이트·신고가/장대양봉 게이트·진입신호·ExitPlan·
inert 청산프로파일·스캐너 기본 OFF 등록.

설계: docs/02-design/features/2026-06-17-thetrading-methodology-uplift.design.md §6
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from backend.core.strategy.base import Strategy
from backend.core.strategy.closing_bet import ClosingBetParams, ClosingBetStrategy
from backend.models.market import MarketType, OHLCV
from backend.models.strategy import AnalysisContext

_KST = timezone(timedelta(hours=9))


def _candles(n: int, today_body_pct: float, today_new_high: bool) -> list[OHLCV]:
    """직전 n-1봉은 평탄(고점 ~101), 마지막 봉은 today_body_pct 양봉.

    today_new_high=True 면 마지막 고점이 직전 고점 초과(신고가 돌파).
    """
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out: list[OHLCV] = []
    price = 100_000.0
    # 신고가 케이스: 직전 고점 낮게(1.01). 비신고가 케이스: 직전 고점 높게(1.12)로 둬
    #   당일 7% 양봉(고점 ~107)이 직전 고점을 못 넘게 만든다.
    prior_high_mult = 1.01 if today_new_high else 1.12
    for i in range(n - 1):
        out.append(OHLCV(
            symbol="TEST",
            timestamp=base_time.replace(day=(i % 27) + 1, month=((i // 27) % 12) + 1),
            open=price, high=price * prior_high_mult, low=price * 0.99,
            close=price, volume=1_000_000.0, market_type=MarketType.STOCK,
        ))
    # 마지막(당일) 캔들
    open_px = price
    close_px = open_px * (1 + today_body_pct)
    high_mult = 1.07 if today_new_high else 1.005   # 신고가면 직전고점(1.01) 초과
    high_px = max(close_px, open_px * high_mult)
    out.append(OHLCV(
        symbol="TEST",
        timestamp=base_time.replace(day=28, month=2),
        open=open_px, high=high_px, low=open_px * 0.99,
        close=close_px, volume=3_000_000.0, market_type=MarketType.STOCK,
    ))
    return out


def _ctx(candles: list[OHLCV], hh: int = 15, mm: int = 10) -> AnalysisContext:
    return AnalysisContext(
        symbol="TEST", name="테스트", candles=candles, market_type=MarketType.STOCK,
        timestamp=datetime(2026, 6, 17, hh, mm, tzinfo=_KST),
    )


class TestClosingBetEntry:
    def test_inherits_strategy(self):
        assert issubclass(ClosingBetStrategy, Strategy)
        assert ClosingBetStrategy.STRATEGY_ID == "closing_bet_v1"

    def test_min_candles_none(self):
        s = ClosingBetStrategy()
        assert s._analyze_v2(_ctx(_candles(10, 0.07, True))) is None

    def test_outside_window_none(self):
        """진입창(15:00~15:20) 밖(10:10)이면 None."""
        s = ClosingBetStrategy()
        assert s._analyze_v2(_ctx(_candles(80, 0.07, True), hh=10, mm=10)) is None

    def test_valid_breakout_emits_signal(self):
        """진입창 + 신고가 + 장대양봉(7%) → closing_bet 신호."""
        s = ClosingBetStrategy()
        sig = s._analyze_v2(_ctx(_candles(80, 0.07, True)))
        assert sig is not None
        assert sig.signal_type == "closing_bet"
        assert sig.strategy_id == "closing_bet_v1"
        assert sig.metadata["overnight"] is True
        assert sig.score > 0

    def test_not_new_high_none(self):
        """신고가 미돌파 → None."""
        s = ClosingBetStrategy()
        assert s._analyze_v2(_ctx(_candles(80, 0.07, False))) is None

    def test_weak_body_none(self):
        """몸통 2% (<5% 장대양봉 기준 미달) → None."""
        s = ClosingBetStrategy()
        assert s._analyze_v2(_ctx(_candles(80, 0.02, True))) is None

    def test_window_disabled_allows_offhours(self):
        """require_eod_window=False 면 시간 무관 평가."""
        s = ClosingBetStrategy(ClosingBetParams(require_eod_window=False))
        sig = s._analyze_v2(_ctx(_candles(80, 0.07, True), hh=10, mm=10))
        assert sig is not None and sig.signal_type == "closing_bet"


class TestClosingBetExit:
    def test_exit_plan_overnight_semantics(self):
        s = ClosingBetStrategy()
        pos = SimpleNamespace(
            avg_price=10_000.0, symbol="TEST",
            metadata={"is_largecap": False, "stop_fib_price": 9_600.0},
        )
        plan = s.exit_plan(pos, _ctx(_candles(80, 0.07, True)))
        assert plan.time_exit == dtime(10, 0)           # 익일 아침 청산
        assert plan.max_hold_days == 3                  # D1~D3
        assert len(plan.take_profits) == 2
        assert plan.stop_loss is not None

    def test_largecap_uses_2pct_tp(self):
        s = ClosingBetStrategy()
        pos_l = SimpleNamespace(avg_price=10_000.0, symbol="T", metadata={"is_largecap": True})
        pos_s = SimpleNamespace(avg_price=10_000.0, symbol="T", metadata={"is_largecap": False})
        tp_l = s.exit_plan(pos_l, _ctx(_candles(80, 0.07, True))).take_profits[-1].price
        tp_s = s.exit_plan(pos_s, _ctx(_candles(80, 0.07, True))).take_profits[-1].price
        assert tp_l < tp_s                              # 대형주 +2% < 일반 +4.5%


class TestClosingBetRegistration:
    def test_exit_profile_inert_present(self):
        from backend.core.risk.holding_evaluator import STRATEGY_EXIT_PROFILES
        prof = STRATEGY_EXIT_PROFILES.get("closing_bet")
        assert prof is not None
        assert prof["min_hold_days"] == 1 and prof["max_hold_days"] == 3

    def test_resolve_policy_maps_versioned_id(self):
        from backend.core.risk.holding_evaluator import resolve_policy, ExitPolicy
        base = ExitPolicy()
        pol = resolve_policy(base, "closing_bet_v1")
        assert pol.max_hold_days == 3 and pol.min_hold_days == 1

    def test_scanner_default_off(self):
        from backend.core.scanner.signal_scanner import _DEFAULT_ENABLED, STRATEGY_PRIORITY
        assert _DEFAULT_ENABLED["closing_bet"] is False     # 라이브 영향 없음
        assert STRATEGY_PRIORITY["closing_bet"] == 8


class TestGapGuardEnvConfigurable:
    def test_default_set_unchanged(self, monkeypatch):
        """BARRO_GAP_GUARD_STRATEGIES 미설정 시 기존과 동일({gold_zone, f_zone})."""
        import importlib
        monkeypatch.delenv("BARRO_GAP_GUARD_STRATEGIES", raising=False)
        import scripts.intraday_buy_daemon as d
        importlib.reload(d)
        assert d._GAP_GUARD_STRATEGIES == {"gold_zone", "f_zone"}

    def test_env_can_add_sf_zone(self, monkeypatch):
        import importlib
        monkeypatch.setenv("BARRO_GAP_GUARD_STRATEGIES", "gold_zone,f_zone,sf_zone")
        import scripts.intraday_buy_daemon as d
        importlib.reload(d)
        assert "sf_zone" in d._GAP_GUARD_STRATEGIES
        # 원복 (다른 테스트 격리)
        monkeypatch.delenv("BARRO_GAP_GUARD_STRATEGIES", raising=False)
        importlib.reload(d)
