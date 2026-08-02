"""AiSwingStrategy (단테 AI 스윙) 테스트 — 반복 2.

검증 축:
- build_exit_plan 단일 원천이 swing_38 계승 임계를 정확히 싣는지
- SL env(`BARRO_AI_SWING_SL_PCT`) override 와 swing_38 env 로부터의 **분리**
- 진입 판정이 부모(Swing38Strategy) 와 동일하고 라벨만 ai_swing 인지
- 등록 4지점(HoldingEvaluator 프로파일 / round_figure 스윙 상한 / REGIME_WEIGHTS /
  EntrySignal Literal) 이 "ai_swing" 을 인식하는지
- ★회귀★ swing_38 의 청산·프로파일·RF 상한이 변하지 않았는지
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.core.backtester.market_regime import REGIME_WEIGHTS, MarketRegime
from backend.core.risk.holding_evaluator import (
    STRATEGY_EXIT_PROFILES,
    ExitPolicy,
    resolve_policy,
)
from backend.core.strategy.ai_swing import (
    STRATEGY_ID,
    AiSwingParams,
    AiSwingStrategy,
    build_exit_plan,
)
from backend.core.strategy.base import Strategy
from backend.core.strategy.round_figure import _max_stop_for
from backend.core.strategy.swing_38 import Swing38Params, Swing38Strategy
from backend.models.market import MarketType, OHLCV
from backend.models.position import Position
from backend.models.strategy import AnalysisContext
from backend.tests.strategy.test_swing_38 import _make_swing_candles

# 개발/운영 셸의 env 오염으로 판정이 흔들리지 않게 관련 env 를 전부 비운다.
_TOUCHED_ENV = (
    "BARRO_AI_SWING_SL_PCT",
    "BARRO_SWING38_SL_PCT",
    "RF_STOP_ENABLED",
    "RF_STOP_DRY_RUN",
    "RF_MAX_STOP_PCT_SWING",
    "RF_MAX_STOP_PCT_INTRADAY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _TOUCHED_ENV:
        monkeypatch.delenv(name, raising=False)


def _ai_swing_candles(n: int = 70) -> list[OHLCV]:
    """임펄스(+8%, vol 5x) → 되돌림 → Fib 0.382 지점 반등 양봉 일봉 시나리오.

    default 게이트(일봉 강제 / ATR% ≥ 3% / min_score) 를 모두 통과하도록 실측 조정.
    (`test_swing_38._make_swing_candles` 는 ATR 1.7% 로 default 필터에 걸려 None 이다 —
     아래 parity 테스트에서 그 케이스도 함께 고정한다.)
    """
    out: list[OHLCV] = []
    t0 = datetime(2026, 3, 1, 9, 0)
    imp_i = n - 12
    imp_high, imp_low = 1085.0, 998.0
    target = imp_high - 0.382 * (imp_high - imp_low)   # Fib 0.382 되돌림 종가
    for i in range(n):
        if i == imp_i:
            o, c, h, l, v = 1000.0, 1080.0, imp_high, imp_low, 5_000_000.0
        elif i == n - 1:
            c = target
            o = c / 1.025                              # body +2.5% → bounce_score 1.0
            h, l, v = c * 1.002, o * 0.998, 1_500_000.0
        elif i > imp_i:
            base = 1080.0 - (1080.0 - target) * ((i - imp_i) / (n - 1 - imp_i))
            o, c = base * 1.004, base                  # 음봉 되돌림
            h, l, v = o * 1.004, c * 0.982, 1_000_000.0
        else:
            o = c = 1000.0
            h, l, v = 1018.0, 983.0, 1_000_000.0       # ATR ≈ 3.5%
        out.append(OHLCV(
            symbol="TEST", timestamp=t0 + timedelta(days=i),
            open=o, high=h, low=l, close=c, volume=v,
            market_type=MarketType.STOCK,
        ))
    return out


def _ctx(candles: list[OHLCV], symbol: str = "TEST") -> AnalysisContext:
    return AnalysisContext(
        symbol=symbol, name=symbol, candles=candles, market_type=MarketType.STOCK,
    )


def _position(avg_price: float = 10_000.0, strategy_id: str = "ai_swing_v1") -> Position:
    return Position(
        symbol="005930", name="삼성전자", quantity=10, avg_price=avg_price,
        current_price=avg_price, realized_pnl=0.0, unrealized_pnl=0.0,
        pnl_pct=0.0, market_type=MarketType.STOCK,
        entry_time=datetime(2026, 3, 1, tzinfo=timezone.utc),
        strategy_id=strategy_id,
    )


# ─── 1) build_exit_plan — 단일 원천 임계 ────────────────────────────────────

class TestBuildExitPlan:
    def test_default_thresholds(self):
        """2026-07-30 그리드 최적: TP1 +20%(0.5) / TP2 +50%(0.5) / SL -5% / BE +10% / 3~20일."""
        plan = build_exit_plan(Decimal("10000"), AiSwingParams())
        assert len(plan.take_profits) == 2
        assert plan.take_profits[0].price == Decimal("10000") * Decimal("1.20")
        assert plan.take_profits[0].qty_pct == Decimal("0.5")
        assert plan.take_profits[1].price == Decimal("10000") * Decimal("1.50")
        assert plan.take_profits[1].qty_pct == Decimal("0.5")
        assert plan.stop_loss.fixed_pct == Decimal("-0.05")
        assert plan.breakeven_trigger == Decimal("0.10")
        assert plan.min_hold_days == 3
        assert plan.max_hold_days == 20
        assert plan.time_exit is None, "multi-day 스윙 — 당일 강제청산 없음"

    def test_params_are_grid_searchable(self):
        """청산 임계가 리터럴이 아닌 파라미터 — 그리드 서치 스윕 가능."""
        p = AiSwingParams(
            sl_pct=Decimal("-0.08"), tp1_pct=Decimal("0.10"), tp1_qty=Decimal("0.3"),
            tp2_pct=Decimal("0.30"), tp2_qty=Decimal("0.7"), be_pct=Decimal("0.05"),
            min_hold_days=1, max_hold_days=10,
        )
        plan = build_exit_plan(Decimal("20000"), p)
        assert plan.take_profits[0].price == Decimal("20000") * Decimal("1.10")
        assert plan.take_profits[0].qty_pct == Decimal("0.3")
        assert plan.take_profits[1].price == Decimal("20000") * Decimal("1.30")
        assert plan.stop_loss.fixed_pct == Decimal("-0.08")
        assert plan.breakeven_trigger == Decimal("0.05")
        assert plan.min_hold_days == 1
        assert plan.max_hold_days == 10

    def test_strategy_exit_plan_delegates_to_single_source(self):
        """AiSwingStrategy.exit_plan == build_exit_plan (단일 원천 위임)."""
        s = AiSwingStrategy()
        pos = _position(avg_price=12_345.0)
        plan = s.exit_plan(pos, _ctx(_ai_swing_candles()))
        expected = build_exit_plan(Decimal("12345.0"), s.params, symbol="005930")
        assert plan.take_profits[0].price == expected.take_profits[0].price
        assert plan.take_profits[1].price == expected.take_profits[1].price
        assert plan.stop_loss.fixed_pct == expected.stop_loss.fixed_pct
        assert plan.breakeven_trigger == expected.breakeven_trigger
        assert (plan.min_hold_days, plan.max_hold_days) == (3, 20)

    def test_exit_plan_ctx_optional(self):
        """ctx 없이도 호출 가능 (시뮬 진입점이 position 만 들고 부를 수 있게)."""
        plan = AiSwingStrategy().exit_plan(_position())
        assert plan.stop_loss.fixed_pct == Decimal("-0.05")


# ─── 2) SL env override + swing_38 env 로부터의 분리 ────────────────────────

class TestSlEnvSeparation:
    def test_env_override_applies(self, monkeypatch):
        """BARRO_AI_SWING_SL_PCT=-8.0 (percent) → fraction -0.08."""
        monkeypatch.setenv("BARRO_AI_SWING_SL_PCT", "-8.0")
        plan = build_exit_plan(Decimal("10000"), AiSwingParams())
        assert plan.stop_loss.fixed_pct == Decimal("-0.08")

    def test_swing38_env_does_not_leak(self, monkeypatch):
        """★분리★ BARRO_SWING38_SL_PCT 를 바꿔도 ai_swing SL 은 영향 없음."""
        monkeypatch.setenv("BARRO_SWING38_SL_PCT", "-3.0")
        plan = build_exit_plan(Decimal("10000"), AiSwingParams())
        assert plan.stop_loss.fixed_pct == Decimal("-0.05")

    def test_ai_swing_env_does_not_leak_into_swing38(self, monkeypatch):
        """역방향 분리 — ai_swing env 가 swing_38 exit_plan 을 건드리지 않음."""
        monkeypatch.setenv("BARRO_AI_SWING_SL_PCT", "-3.0")
        plan = Swing38Strategy().exit_plan(_position(strategy_id="swing_38_v1"),
                                          _ctx(_ai_swing_candles()))
        assert plan.stop_loss.fixed_pct == Decimal("-0.15")

    @pytest.mark.parametrize(
        "raw", ["", "   ", "abc", "0", "5.0", "+2", "NaN", "-Infinity", "-100", "-150"],
    )
    def test_invalid_env_absorbed(self, monkeypatch, raw):
        """외부 입력 예외 전량 흡수 (§2 S3) — 무효값은 param default 로 폴백."""
        monkeypatch.setenv("BARRO_AI_SWING_SL_PCT", raw)
        plan = build_exit_plan(Decimal("10000"), AiSwingParams())
        assert plan.stop_loss.fixed_pct == Decimal("-0.05")


# ─── 3) 시그널 라벨 + 진입 판정 상속 ───────────────────────────────────────

class TestAiSwingSignal:
    def test_inherits_swing_38(self):
        assert issubclass(AiSwingStrategy, Swing38Strategy)
        assert issubclass(AiSwingStrategy, Strategy)
        assert AiSwingStrategy.STRATEGY_ID == "ai_swing_v1" == STRATEGY_ID

    def test_signal_labels(self):
        """strategy_id="ai_swing_v1" + signal_type="ai_swing"."""
        sig = AiSwingStrategy()._analyze_v2(_ctx(_ai_swing_candles()))
        assert sig is not None, "합성 임펄스+Fib+반등 시나리오에서 시그널 미발화"
        assert sig.strategy_id == "ai_swing_v1"
        assert sig.signal_type == "ai_swing"

    def test_entry_decision_identical_to_parent(self):
        """진입 판정(_detect_impulse/_fib_score/_bounce_score)은 부모와 동일 — 라벨만 다름."""
        candles = _ai_swing_candles()
        ai = AiSwingStrategy()._analyze_v2(_ctx(candles))
        sw = Swing38Strategy()._analyze_v2(_ctx(candles))
        assert ai is not None and sw is not None
        assert ai.score == sw.score
        assert ai.price == sw.price
        assert ai.reason == sw.reason
        assert (ai.signal_type, sw.signal_type) == ("ai_swing", "swing_38")

    def test_parent_reject_is_inherited(self):
        """부모가 거부하는 입력(ATR 1.7% < 3%)은 ai_swing 도 거부 — 게이트 상속 확인."""
        candles = _make_swing_candles(num=100, seed=7)   # test_swing_38 헬퍼 재사용
        assert Swing38Strategy()._analyze_v2(_ctx(candles)) is None
        assert AiSwingStrategy()._analyze_v2(_ctx(candles)) is None

    def test_default_params_inherit_swing_hold_window(self):
        """min_hold/max_hold/일봉강제/ATR 필터는 Swing38Params 계승, min_score 만 완화 스타트."""
        p = AiSwingParams()
        assert (p.min_hold_days, p.max_hold_days) == (3, 20)
        assert p.require_daily_candles is True
        assert p.min_atr_pct == 0.03
        assert p.min_score == 3.0

    def test_add_on_signal_relabelled(self):
        """2차 분할진입도 signal_type="ai_swing" 라벨 (strategy_id 와 불일치 방지)."""
        s = AiSwingStrategy()
        pos = Position(
            symbol="TEST", name="TEST", quantity=10, avg_price=10_000.0,
            current_price=9_850.0, realized_pnl=0.0, unrealized_pnl=0.0,
            pnl_pct=0.0, market_type=MarketType.STOCK,
            entry_time=datetime.now(timezone.utc) - timedelta(days=1),
            strategy_id="ai_swing_v1",
        )
        candles = [
            OHLCV(symbol="TEST", timestamp=datetime(2026, 3, 1) + timedelta(days=i),
                  open=9_850.0, high=9_900.0, low=9_800.0, close=9_850.0,
                  volume=10_000, market_type=MarketType.STOCK)
            for i in range(70)
        ]
        sig = s.add_on_signal(pos, _ctx(candles), base_candle_low=Decimal("9800"))
        assert sig is not None
        assert sig.signal_type == "ai_swing"
        assert sig.strategy_id == "ai_swing_v1"
        assert sig.metadata["entry_round"] == 2

    def test_health_check_reports_ai_swing_id(self):
        h = AiSwingStrategy().health_check()
        assert h["strategy_id"] == "ai_swing_v1"
        assert h["ready"] is True


# ─── 4) 등록 지점: HoldingEvaluator / round_figure / REGIME_WEIGHTS ─────────

class TestRegistrationPoints:
    def test_resolve_policy_matches_ai_swing_v1(self):
        """resolve_policy 가 "_v1" 제거 후 "ai_swing" 프로파일로 매칭."""
        pol = resolve_policy(ExitPolicy(), "ai_swing_v1")
        # 2026-07-30 그리드 최적 (사용자 승인) — 초기 swing_38 계승값에서 교체됨
        assert pol.stop_loss_pct == Decimal("-5.0")
        assert pol.take_profit_pct == Decimal("50.0")
        assert pol.partial_tp_pct == Decimal("20.0")
        assert pol.partial_tp_ratio == Decimal("0.5")
        assert pol.trailing_start_pct == Decimal("10.0")
        assert pol.trailing_offset_pct == Decimal("3.0")
        assert pol.breakeven_trigger_pct == Decimal("10.0")
        assert pol.tightened_sl_pct == Decimal("-5.0")
        assert pol.min_hold_days == 3
        assert pol.max_hold_days == 20

    def test_profile_equals_params_across_both_exit_paths(self):
        """★두 청산 경로 등가성★ HoldingEvaluator 프로파일 == AiSwingParams.

        라이브는 ExitEngine(분봉 plan)과 HoldingEvaluator(브로커 pnl_rate) 두 경로로
        청산을 평가한다. 임계가 어긋나면 경로에 따라 청산 시점이 달라지므로,
        한쪽만 바꾸는 실수를 이 테스트가 잡는다.
        """
        prof = STRATEGY_EXIT_PROFILES["ai_swing"]
        p = AiSwingParams()
        assert prof["stop_loss_pct"] == p.sl_pct * 100
        assert prof["trailing_start_pct"] == p.trail_start_pct * 100
        assert prof["trailing_offset_pct"] == p.trail_offset_pct * 100
        assert prof["breakeven_trigger_pct"] == p.be_pct * 100
        assert prof["partial_tp_pct"] == p.tp1_pct * 100
        assert prof["partial_tp_ratio"] == p.tp1_qty
        assert prof["take_profit_pct"] == p.tp2_pct * 100
        assert prof["min_hold_days"] == p.min_hold_days
        assert prof["max_hold_days"] == p.max_hold_days

    def test_profile_diverged_from_swing_38(self):
        """ai_swing 은 더 이상 swing_38 복제가 아니다 — 실측으로 분기했음을 고정."""
        ai = STRATEGY_EXIT_PROFILES["ai_swing"]
        sw = STRATEGY_EXIT_PROFILES["swing_38"]
        assert ai["stop_loss_pct"] != sw["stop_loss_pct"]
        assert ai["trailing_start_pct"] != sw["trailing_start_pct"]
        # 그리드 미검증 항목은 여전히 계승값과 같다
        assert ai["take_profit_pct"] == sw["take_profit_pct"]
        assert (ai["min_hold_days"], ai["max_hold_days"]) == (
            sw["min_hold_days"], sw["max_hold_days"])

    def test_max_stop_for_ai_swing_is_swing_tier(self):
        """★결함 수정★ "ai_swing_v1" 이 인트라데이 상한(0.04)으로 조여지지 않음."""
        assert _max_stop_for("ai_swing_v1") == 0.15
        assert _max_stop_for("ai_swing") == 0.15

    def test_regime_weights_registered_all_three(self):
        """3국면 전부 명시 등록 — 미등록 시 1.0 로 BEARISH 필터를 통과하는 결함 차단."""
        for regime in (MarketRegime.BULL, MarketRegime.SIDEWAYS, MarketRegime.BEARISH):
            assert "ai_swing" in REGIME_WEIGHTS[regime], f"{regime} 미등록"
        assert REGIME_WEIGHTS[MarketRegime.BEARISH]["ai_swing"] < 1.0, (
            "BEARISH 가중치가 1.0 이상이면 하락장 차단 필터를 통과한다"
        )
        assert REGIME_WEIGHTS[MarketRegime.SIDEWAYS]["ai_swing"] == 0.3
        assert REGIME_WEIGHTS[MarketRegime.BULL]["ai_swing"] <= 1.5

    def test_signal_type_literal_accepts_ai_swing(self):
        """EntrySignal Literal 에 "ai_swing" 등록 (pydantic 검증 통과)."""
        from backend.models.signal import EntrySignal
        sig = EntrySignal(
            symbol="005930", name="삼성전자", price=1000.0, signal_type="ai_swing",
            score=9.2, reason="test", market_type=MarketType.STOCK,
            strategy_id="ai_swing_v1", timestamp=datetime.now(timezone.utc),
        )
        assert sig.signal_type == "ai_swing"


# ─── 5) ★회귀★ swing_38 무변경 고정 ────────────────────────────────────────

class TestSwing38Regression:
    def test_swing_38_exit_plan_unchanged(self):
        """swing_38 청산: TP +20/+50%, SL -15%, BE +10%, 3~20일, time_exit None."""
        plan = Swing38Strategy().exit_plan(
            _position(avg_price=10_000.0, strategy_id="swing_38_v1"),
            _ctx(_ai_swing_candles()),
        )
        assert plan.take_profits[0].price == Decimal("10000") * Decimal("1.20")
        assert plan.take_profits[0].qty_pct == Decimal("0.5")
        assert plan.take_profits[1].price == Decimal("10000") * Decimal("1.50")
        assert plan.stop_loss.fixed_pct == Decimal("-0.15")   # ★swing_38 값 — ai_swing(-0.05)과 무관
        assert plan.breakeven_trigger == Decimal("0.10")
        assert plan.min_hold_days == 3
        assert plan.max_hold_days == 20
        assert plan.time_exit is None

    def test_swing_38_profile_unchanged(self):
        pol = resolve_policy(ExitPolicy(), "swing_38_v1")
        assert pol.stop_loss_pct == Decimal("-15.0")
        assert pol.take_profit_pct == Decimal("50.0")
        assert pol.min_hold_days == 3
        assert pol.max_hold_days == 20

    def test_swing_38_max_stop_unchanged(self):
        assert _max_stop_for("swing_38_v1") == 0.15
        assert _max_stop_for("f_zone_v1") == 0.04, "인트라데이 상한 회귀"
        assert _max_stop_for("") == 0.04

    def test_swing_38_params_unchanged(self):
        """AiSwingParams 추가가 Swing38Params 기본값을 오염시키지 않음."""
        p = Swing38Params()
        assert p.min_score == 3.0
        assert p.min_hold_days == 3
        assert p.max_hold_days == 20
        assert not hasattr(p, "sl_pct"), "청산 파라미터는 AiSwingParams 에만 존재"

    def test_regime_weights_swing_38_unchanged(self):
        assert REGIME_WEIGHTS[MarketRegime.BULL]["swing_38"] == 1.5
        assert REGIME_WEIGHTS[MarketRegime.SIDEWAYS]["swing_38"] == 0.3
        assert REGIME_WEIGHTS[MarketRegime.BEARISH]["swing_38"] == 0.5
