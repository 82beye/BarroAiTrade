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

    # ── 이격도 노란불 게이트 (D-R43, default-OFF) — thetrading-uplift 301delta ──
    def test_disparity_gate_off_is_parity(self):
        """default(require_disparity_yellow=False): 저이격(+5.5%) 신고가 장대양봉도 신호 → 현행 보존."""
        s = ClosingBetStrategy()  # 기본 파라미터
        assert s._analyze_v2(_ctx(_candles(80, 0.07, True))) is not None

    def test_disparity_gate_on_rejects_low_disparity(self):
        """게이트 ON: 이격 +5.5%(<14.25%)면 진입 거부."""
        s = ClosingBetStrategy(ClosingBetParams(require_disparity_yellow=True))
        assert s._analyze_v2(_ctx(_candles(80, 0.07, True))) is None

    def test_disparity_gate_on_accepts_yellow(self):
        """게이트 ON: 이격 +15.4%(≥14.25%, body 20%)면 신호 유지."""
        s = ClosingBetStrategy(ClosingBetParams(require_disparity_yellow=True))
        sig = s._analyze_v2(_ctx(_candles(80, 0.20, True)))
        assert sig is not None and sig.signal_type == "closing_bet"

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


def _intraday_bars(am_value: float, pm_value: float, price: float = 10_000.0) -> list[OHLCV]:
    """오전(10:00)/오후(14:00) 5분봉. 거래대금=close×volume=value 가 되게 volume 설정."""
    base = datetime(2026, 6, 17)
    bars: list[OHLCV] = []
    if am_value > 0:
        bars.append(OHLCV(symbol="T", timestamp=base.replace(hour=10), open=price, high=price,
                          low=price, close=price, volume=am_value / price, market_type=MarketType.STOCK))
    if pm_value > 0:
        bars.append(OHLCV(symbol="T", timestamp=base.replace(hour=14), open=price, high=price,
                          low=price, close=price, volume=pm_value / price, market_type=MarketType.STOCK))
    if not bars:
        bars.append(OHLCV(symbol="T", timestamp=base.replace(hour=15), open=price, high=price,
                          low=price, close=price, volume=0.0, market_type=MarketType.STOCK))
    return bars


def _flow_ctx(am_value: float, pm_value: float) -> AnalysisContext:
    return AnalysisContext(symbol="T", name="T", candles=_candles(70, 0.07, True),
                           market_type=MarketType.STOCK,
                           intraday_candles=_intraday_bars(am_value, pm_value))


class TestClosingBetMoneyFlow:
    def test_both(self):
        assert ClosingBetStrategy()._money_flow_grade(_flow_ctx(2e9, 2e9)) == "BOTH"

    def test_pm_only(self):
        # 오전 미달(0.5e9<1e9), 오후 충족 → PM_ONLY
        assert ClosingBetStrategy()._money_flow_grade(_flow_ctx(0.5e9, 2e9)) == "PM_ONLY"

    def test_afternoon_death_blocks(self):
        # 오전 유입(2e9) 후 오후 급감(0.3e9 < 0.3×2e9) → BLOCK
        assert ClosingBetStrategy()._money_flow_grade(_flow_ctx(2e9, 0.3e9)) == "BLOCK"

    def test_no_pm_blocks(self):
        assert ClosingBetStrategy()._money_flow_grade(_flow_ctx(2e9, 0.0)) == "BLOCK"

    def test_no_intraday_returns_na(self):
        """intraday_candles 없으면(테마컨텍스트도 없음) N/A=통과(하위호환)."""
        ctx = AnalysisContext(symbol="T", candles=_candles(70, 0.07, True),
                              market_type=MarketType.STOCK)
        assert ClosingBetStrategy()._money_flow_grade(ctx) == "N/A"


class TestClosingBetZone:
    @staticmethod
    def _zone_bars(retrace_down: float, n: int = 30) -> list[OHLCV]:
        base = datetime(2026, 6, 17)
        lo, hi = 100.0, 110.0
        cur = hi - retrace_down * (hi - lo)
        bars = [OHLCV(symbol="T", timestamp=base.replace(day=1 + i % 27), open=lo, high=hi,
                      low=lo, close=(lo + hi) / 2, volume=1.0, market_type=MarketType.STOCK)
                for i in range(n)]
        last = bars[-1]
        bars[-1] = OHLCV(symbol="T", timestamp=last.timestamp, open=lo, high=hi, low=lo,
                         close=cur, volume=1.0, market_type=MarketType.STOCK)
        return bars

    def test_in_zone(self):
        # 되돌림 깊이 0.55 ∈ [0.5, 0.618] → True
        assert ClosingBetStrategy()._in_zone(self._zone_bars(0.55)) is True

    def test_out_of_zone(self):
        # 고점 근처(0.1) → 존 밖
        assert ClosingBetStrategy()._in_zone(self._zone_bars(0.1)) is False

    def test_require_zone_daily_fallback_blocks_breakout(self):
        """존 요구 + 분봉 없음 → 일봉 폴백. 장대양봉 종가는 고점근처라 존 밖 → None.

        (분봉 ablation에서 확인된 '장대양봉 종가 vs 골드존 되돌림' 개념 충돌의 단위 재현.)
        """
        s = ClosingBetStrategy(ClosingBetParams(require_eod_window=False, require_zone=True))
        assert s._analyze_v2(_ctx(_candles(80, 0.07, True), hh=10, mm=10)) is None


class TestClosingBetSimulatorBranch:
    def test_build_strategies_closing_bet(self):
        from backend.core.backtester.intraday_simulator import _build_strategies
        out = _build_strategies(["closing_bet"])
        assert len(out) == 1 and out[0].STRATEGY_ID == "closing_bet_v1"
