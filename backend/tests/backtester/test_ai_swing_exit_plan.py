"""ai_swing 시뮬레이터 등록 — 시뮬↔라이브 청산 정책 일치 검증 (2026-07-30 신규).

배경 (이 테스트가 존재하는 이유)
--------------------------------
`_exit_plan_for_strategy` 는 sf_zone 외 모든 전략을 `_scaled_exit_plan`(고정 TP
+3/+5/+7%·SL -1.5%·BE +1%)으로 보내고 **전략의 `exit_plan()` 을 호출하지 않았다**.
그 결과 swing_38 은 라이브(TP +20/+50%·SL -15%·min_hold 3·max_hold 20)와 시뮬 청산
정책이 어긋나, "시뮬 승률 52.5%" 같은 수치가 라이브 정책을 대변하지 못했다.

ai_swing 은 그 재발을 구조적으로 막는다 — `build_exit_plan()` 단일 원천을 라이브와
시뮬이 **둘 다** 호출한다. 아래 테스트는 그 일치를 고정하고,
`ExitEngine` 이 plan 에 실린 min/max_hold_days 를 실제로 소비함까지 증명한다.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from decimal import Decimal

import pytest

from backend.core.backtester.intraday_simulator import (
    IntradaySimulator,
    _build_strategies,
    _exit_plan_for_strategy,
)
from backend.core.execution.exit_engine import ExitEngine
from backend.core.strategy.ai_swing import (
    AiSwingParams,
    AiSwingStrategy,
    build_exit_plan,
)
from backend.models.exit_order import ExitReason, PositionState
from backend.models.market import MarketType, OHLCV

ENTRY = Decimal("10000")


def _window(n: int = 30) -> list[OHLCV]:
    """ATR 계산용 최소 윈도우 (ai_swing 분기는 candles 를 쓰지 않지만 시그니처 충족)."""
    t0 = datetime(2026, 7, 30, 9, 0)
    out: list[OHLCV] = []
    for i in range(n):
        c = 10000 + i * 10
        out.append(OHLCV(
            symbol="005930", timestamp=t0 + timedelta(days=i),
            open=c, high=c + 100, low=c - 80, close=c + 20,
            volume=100000 + i * 100, market_type=MarketType.STOCK,
        ))
    return out


# ─── 1. _build_strategies 등록 ────────────────────────────────────────────────
def test_build_strategies_registers_ai_swing():
    """`strategies=["ai_swing"]` 이 AiSwingStrategy 를 만든다 (미등록이면 ValueError)."""
    out = _build_strategies(["ai_swing"], None)
    assert len(out) == 1
    assert isinstance(out[0], AiSwingStrategy)
    assert out[0].STRATEGY_ID == "ai_swing_v1"


def test_build_strategies_applies_operational_gates():
    """swing_38 분기와 동일한 운영 게이트 + min_score 는 완화 기본값(3.0)."""
    strat = _build_strategies(["ai_swing"], None)[0]
    p = strat.params
    assert p.min_atr_pct == 0.035
    assert p.entry_time_cutoff == dtime(14, 0)
    assert p.require_daily_candles is True
    # 완화 스타트 — swing_38 시뮬 운영값(5.0)보다 느슨하게 시작한다.
    assert p.min_score == 3.0
    # 보유 기간은 Swing38Params 기본값 계승
    assert p.min_hold_days == 3
    assert p.max_hold_days == 20


def test_params_override_enables_grid_search():
    """params_override["ai_swing"] 가 dataclass 필드를 치환한다 — SL×trail 그리드 전제."""
    strat = _build_strategies(
        ["ai_swing"], None,
        params_override={"ai_swing": {"sl_pct": Decimal("-0.08"), "max_hold_days": 10}},
    )[0]
    assert strat.params.sl_pct == Decimal("-0.08")
    assert strat.params.max_hold_days == 10
    # 치환하지 않은 필드는 기본값 유지
    assert strat.params.tp1_pct == Decimal("0.20")


def test_ai_swing_not_in_default_strategies():
    """default-OFF 고정 — DEFAULT_STRATEGIES 에 넣으면 모든 기존 호출부에 영향한다.

    run(strategies=None) 이 이 목록을 쓰므로(:386), ai_swing 은 명시 전달만 허용한다.
    """
    assert "ai_swing" not in IntradaySimulator.DEFAULT_STRATEGIES


# ─── 2. 시뮬 ExitPlan == 라이브 ExitPlan ─────────────────────────────────────
def test_sim_exit_plan_equals_live_build_exit_plan():
    """★핵심★ 시뮬 분기가 build_exit_plan() 산출물을 그대로 쓴다 (F1 해소)."""
    strat = _build_strategies(["ai_swing"], None)[0]
    sim_plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    live_plan = build_exit_plan(ENTRY, strat.params)
    assert sim_plan == live_plan


def test_sim_exit_plan_carries_live_thresholds():
    """시뮬 plan 에 라이브 임계가 실린다 — 고정 +3/+5/+7%·-1.5% 가 아니다."""
    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)

    assert len(plan.take_profits) == 2
    assert plan.take_profits[0].price == ENTRY * Decimal("1.20")   # TP1 +20%
    assert plan.take_profits[0].qty_pct == Decimal("0.5")
    assert plan.take_profits[1].price == ENTRY * Decimal("1.50")   # TP2 +50%
    assert plan.stop_loss.fixed_pct == Decimal("-0.15")            # SL -15%
    assert plan.breakeven_trigger == Decimal("0.10")               # BE +10%
    # ★ 보유 기간 게이트가 plan 에 실려야 ExitEngine 이 소비한다 (_scaled_exit_plan 은 미설정)
    assert plan.min_hold_days == 3
    assert plan.max_hold_days == 20
    # multi-day 스윙이라 당일 강제청산은 두지 않는다
    assert plan.time_exit is None


def test_grid_override_reaches_sim_exit_plan():
    """그리드로 치환한 SL/보유기간이 시뮬 청산 plan 까지 전달된다."""
    strat = _build_strategies(
        ["ai_swing"], None,
        params_override={"ai_swing": {"sl_pct": Decimal("-0.10"), "max_hold_days": 8}},
    )[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    assert plan.stop_loss.fixed_pct == Decimal("-0.10")
    assert plan.max_hold_days == 8


def test_strategy_obj_none_falls_back_safely():
    """strategy_obj 미전달 시 기존 경로로 폴백 — 예외 없이 고정 plan 을 돌려준다."""
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=None)
    assert plan.min_hold_days is None      # _scaled_exit_plan 은 보유기간을 설정하지 않는다
    assert plan.stop_loss.fixed_pct == Decimal("-0.015")


# ─── 3. ExitEngine 이 plan 의 보유기간 게이트를 실제로 소비 ──────────────────
def _pos(entry_time: datetime) -> PositionState:
    return PositionState(
        symbol="005930", entry_price=ENTRY, qty=Decimal("10"),
        initial_qty=Decimal("10"), entry_time=entry_time,
    )


def test_exit_engine_blocks_before_min_hold():
    """min_hold 3일 미달 → SL -15% 를 넘겨도 청산되지 않는다 (게이트 실작동 증명)."""
    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    now = datetime(2026, 7, 30, 10, 0)
    pos = _pos(now - timedelta(days=1))            # 1일 보유 (min 3 미달)
    crashed = ENTRY * Decimal("0.80")              # -20% (SL -15% 초과)

    _, orders = ExitEngine().evaluate(pos, plan, crashed, now)
    assert orders == []


def test_exit_engine_stops_out_after_min_hold():
    """min_hold 경과 후에는 SL 이 정상 평가된다."""
    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    now = datetime(2026, 7, 30, 10, 0)
    pos = _pos(now - timedelta(days=4))            # 4일 보유 (min 3 충족)
    crashed = ENTRY * Decimal("0.80")

    _, orders = ExitEngine().evaluate(pos, plan, crashed, now)
    assert orders, "min_hold 경과 후 SL 이 발동해야 한다"
    assert orders[0].reason == ExitReason.STOP_LOSS


def test_exit_engine_trails_after_peak_drawdown():
    """★사용자 요구 핵심★ peak +20% 도달 후 고점 -5% 하락 → TRAIL_STOP 청산.

    trail_stages 가 plan 에 실리지 않으면 ExitPlan.trail_sl_for_peak() 가 None 을
    돌려주고 트레일링이 통째로 비활성된다(기존 전략들이 실제로 그 상태).
    """
    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    assert plan.trail_stages, "트레일링 단계가 plan 에 실려야 한다"

    now = datetime(2026, 7, 30, 10, 0)
    pos = _pos(now - timedelta(days=5))            # min_hold 경과
    peak = ENTRY * Decimal("1.25")                 # +25% (trail_start +20% 초과)

    # 1) 고점 갱신만 — 아직 청산 없음
    pos2, orders = ExitEngine().evaluate(pos, plan, peak, now)
    assert orders == [] or orders[0].reason != ExitReason.TRAIL_STOP
    assert pos2.high_water_mark == peak

    # 2) 고점 대비 -6% 하락 → trail_sl(peak×0.95) 하회 → TRAIL_STOP
    dropped = peak * Decimal("0.94")
    _, orders2 = ExitEngine().evaluate(pos2, plan, dropped, now)
    assert orders2, "고점 대비 offset 초과 하락 시 청산돼야 한다"
    assert orders2[0].reason == ExitReason.TRAIL_STOP


def test_trail_stages_matches_holding_evaluator_profile():
    """ExitPlan.trail_stages 가 HoldingEvaluator 프로파일과 등가여야 한다.

    라이브는 두 경로(ExitEngine=분봉 plan / HoldingEvaluator=브로커 pnl_rate)로
    청산을 평가하므로, 트레일링 임계가 어긋나면 경로에 따라 청산 시점이 달라진다.
    """
    from backend.core.risk.holding_evaluator import ExitPolicy, resolve_policy

    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    policy = resolve_policy(ExitPolicy(), "ai_swing_v1")

    (start, trail), = plan.trail_stages
    assert start * 100 == policy.trailing_start_pct      # 0.20 ↔ 20.0
    assert -trail * 100 == policy.trailing_offset_pct    # -0.05 ↔ 5.0


def test_trailing_can_be_disabled_by_grid():
    """trail_start=0 그리드로 트레일링을 끌 수 있다 (효과 격리 대조군)."""
    strat = _build_strategies(
        ["ai_swing"], None,
        params_override={"ai_swing": {"trail_start_pct": Decimal("0")}},
    )[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    assert plan.trail_stages is None


def test_exit_engine_time_exits_at_max_hold():
    """max_hold 20일 도달 → 손익 무관 TIME_EXIT 전량."""
    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("ai_swing", ENTRY, _window(), strategy_obj=strat)
    now = datetime(2026, 7, 30, 10, 0)
    pos = _pos(now - timedelta(days=20))
    flat = ENTRY * Decimal("1.01")                 # +1% (TP/SL 미달)

    _, orders = ExitEngine().evaluate(pos, plan, flat, now)
    assert orders, "max_hold 도달 시 강제 청산돼야 한다"
    assert orders[0].reason == ExitReason.TIME_EXIT
    assert orders[0].qty == Decimal("10")


# ─── 4. 기존 전략 회귀 고정 ──────────────────────────────────────────────────
@pytest.mark.parametrize("sid", [
    "f_zone", "gold_zone", "swing_38", "scalping_consensus", "closing_bet",
])
def test_existing_strategies_keep_scaled_plan(sid):
    """ai_swing 분기 추가가 기존 전략 plan 을 바꾸지 않는다 (고정 +3/+5/+7%·-1.5%)."""
    plan = _exit_plan_for_strategy(sid, ENTRY, _window())
    assert plan.stop_loss.fixed_pct == Decimal("-0.015")
    assert len(plan.take_profits) == 3
    assert plan.take_profits[0].price == ENTRY * Decimal("1.03")
    assert plan.breakeven_trigger == Decimal("0.01")
    # 기존 경로는 보유기간 게이트를 설정하지 않는다 (본 변경으로도 그대로)
    assert plan.min_hold_days is None
    assert plan.max_hold_days is None


def test_sf_zone_keeps_atr_branch():
    """sf_zone 은 여전히 ATR 분기를 탄다 (ai_swing 분기가 최상단이어도 무영향)."""
    plan = _exit_plan_for_strategy("sf_zone", ENTRY, _window())
    # ATR 기반이라 고정 -1.5% 와 다르고, floor(1.5%)~cap(8%) 사이여야 한다
    assert Decimal("-0.08") <= plan.stop_loss.fixed_pct <= Decimal("-0.015")


def test_strategy_obj_ignored_for_other_strategies():
    """strategy_obj 를 넘겨도 ai_swing 이 아니면 분기하지 않는다."""
    strat = _build_strategies(["ai_swing"], None)[0]
    plan = _exit_plan_for_strategy("swing_38", ENTRY, _window(), strategy_obj=strat)
    assert plan.stop_loss.fixed_pct == Decimal("-0.015")
    assert plan.min_hold_days is None
