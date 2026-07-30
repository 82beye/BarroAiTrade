"""ai_swing 고아 포지션 방어 — 2026-05-29 swing_38 사고 재발 방지 (2026-07-30 신규).

사고 메커니즘 (실제 발생)
------------------------
swing_38 비활성 시 `active_positions.json` 동기화가 누락되고 보유분 자동 청산이
없어, 잔여 4종목을 사용자가 수동 청산해야 했다(평균 -0.985%).

코드 상 경로 (F2)
-----------------
`HoldingEvaluator.evaluate_holding()` 은 `ctx is None` 이면 `_evaluate_basic()` 으로
빠지고, 그 함수는 **전략 프로파일을 통째로 무시**하고 넘겨받은 policy(운영 데몬 기준
`data/policy.json` = SL **-2.0** / TP +5.0)만 평가한다. 데몬은 장부(`active_positions`)에
있는 종목만 `contexts` 를 채우므로(`intraday_buy_daemon.py:391-393`), **장부가 유실된
스윙 포지션은 min_hold 3일도 전략 SL(-5%) 도 적용받지 못하고 -2% 에서 전량 손절된다.**

이 파일은 그 현상을 **현상 그대로 고정**한다 — 방어(2단 플래그·이중 장부·recover)가
깨지면 즉시 red 가 되게 하는 것이 목적이다. "-2% 손절이 옳다"는 뜻이 아니다.
"""
from __future__ import annotations

from decimal import Decimal

from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.risk.holding_evaluator import (
    STRATEGY_EXIT_PROFILES,
    ExitPolicy,
    PositionContext,
    SellSignal,
    evaluate_holding,
    resolve_policy,
)
from scripts.intraday_buy_daemon import (
    DEFAULT_ZONE_STRATEGIES,
    _CUTOFF_EXEMPT_STRATEGIES,
    _FORCE_CLOSE_EXEMPT_STRATEGIES,
    _NO_DCA_STRATEGIES,
    _force_close_skip,
)

# 운영 데몬이 넘기는 policy.json 값 (2026-07-30 실측: SL -2.0 / TP +5.0)
_LIVE_POLICY = ExitPolicy(stop_loss_pct=Decimal("-2.0"), take_profit_pct=Decimal("5.0"))


def _holding(pnl_rate: float) -> HoldingPosition:
    cur = Decimal("10000") * (Decimal("1") + Decimal(str(pnl_rate)) / Decimal("100"))
    return HoldingPosition(
        symbol="005930", name="삼성전자", qty=10,
        avg_buy_price=Decimal("10000"),
        cur_price=cur,
        eval_amount=cur * 10,
        pnl=(cur - Decimal("10000")) * 10,
        pnl_rate=Decimal(str(pnl_rate)),
    )


# ─── 1. 고아 경로 현상 고정 (F2) ──────────────────────────────────────────
def test_orphan_ai_swing_stops_out_at_minus_2pct():
    """★현상 고정★ 장부 유실(ctx=None) 시 -3% 에서 손절된다 — 전략 SL(-5%)이 무시된다.

    이 테스트가 green 인 동안 고아 포지션은 위험하다. 방어는 코드가 아니라
    운영 절차(2단 플래그 + 이중 장부 + recover)로 한다 — runbook 참조.
    """
    decision = evaluate_holding(_holding(-3.0), _LIVE_POLICY, ctx=None)
    assert decision.signal == SellSignal.STOP_LOSS
    assert decision.sell_qty == Decimal("10")


def test_ai_swing_with_context_survives_minus_3pct():
    """장부가 있으면(ctx 주입) min_hold 게이트가 먼저 걸려 -3% 로는 청산되지 않는다."""
    ctx = PositionContext(strategy="ai_swing", entry_time=None, peak_pnl_rate=0.0)
    decision = evaluate_holding(_holding(-3.0), _LIVE_POLICY, ctx=ctx)
    assert decision.signal == SellSignal.HOLD
    assert decision.sell_qty == 0
    # min_hold 미달 사유가 명시돼야 한다 (배선 실증)
    assert "최소 보유" in decision.reason


def test_ai_swing_profile_overrides_live_policy():
    """resolve_policy 가 policy.json(-2%)을 ai_swing 프로파일(-5%)로 덮는다.

    -5% 는 2026-07-30 그리드 최적값. policy.json 의 -2% 보다 넉넉해야 스윙이
    노이즈에 털리지 않는다 — 고아 경로(ctx=None)에서는 이 override 가 적용되지 않는다.
    """
    resolved = resolve_policy(_LIVE_POLICY, "ai_swing")
    assert resolved.stop_loss_pct == Decimal("-5.0")
    assert resolved.min_hold_days == 3
    assert resolved.max_hold_days == 20
    # 버전 접미사가 붙은 형태도 동일하게 매칭 (시그널 strategy_id 경로)
    assert resolve_policy(_LIVE_POLICY, "ai_swing_v1").stop_loss_pct == Decimal("-5.0")


def test_profile_registered_for_daemon_tag_form():
    """데몬은 버전 없는 sid("ai_swing")를 장부에 저장한다 — 그 키로 등록돼 있어야 한다."""
    assert "ai_swing" in STRATEGY_EXIT_PROFILES


# ─── 2. 데몬 면제 세트 ────────────────────────────────────────────────────
def test_ai_swing_exempt_from_eod_force_close():
    """다일보유 설계 — EOD carry-limit 트림에서 제외."""
    assert "ai_swing" in _FORCE_CLOSE_EXEMPT_STRATEGIES
    assert _force_close_skip("005930", "ai_swing", cb_skip=set()) is True


def test_ai_swing_exempt_from_entry_cutoff():
    """14:30 진입 컷오프 면제 (전략 자체 cutoff 14:00 은 별도로 적용된다)."""
    assert "ai_swing" in _CUTOFF_EXEMPT_STRATEGIES


def test_ai_swing_excluded_from_daemon_dca():
    """자체 2차 분할진입(add_on_signal 상속)이 있어 데몬 tranche DCA 와 겹치면 이중 분할."""
    assert "ai_swing" in _NO_DCA_STRATEGIES


def test_ai_swing_not_in_default_zone_strategies():
    """★default-OFF★ 데몬 기본 전략 집합에 넣지 않는다 — 활성은 사용자 판단 (§2 S4)."""
    assert "ai_swing" not in DEFAULT_ZONE_STRATEGIES


def test_existing_exemptions_unchanged():
    """회귀 — swing_38/supertrend 면제가 그대로 유지된다."""
    assert "swing_38" in _FORCE_CLOSE_EXEMPT_STRATEGIES
    assert "swing_38" in _CUTOFF_EXEMPT_STRATEGIES
    assert {"swing_38", "supertrend"} <= _NO_DCA_STRATEGIES
    for strat in ("f_zone", "sf_zone", "gold_zone"):
        assert _force_close_skip("005930", strat, cb_skip=set()) is False
