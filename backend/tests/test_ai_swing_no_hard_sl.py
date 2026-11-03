"""ai_swing 하드 손절 OFF 플래그 — BARRO_AI_SWING_HARD_SL_ENABLED."""
from decimal import Decimal

import pytest

from backend.core.risk import holding_evaluator as he
from backend.core.gateway.kiwoom_native_account import HoldingPosition
from backend.core.risk.holding_evaluator import (
    ExitPolicy, PositionContext, SellSignal, evaluate_holding, resolve_policy,
)


def _h(rate: str) -> HoldingPosition:
    return HoldingPosition(
        symbol="005870", name="테스트", qty=100,
        avg_buy_price=Decimal("10000"),
        cur_price=Decimal("10000") * (Decimal("1") + Decimal(rate) / 100),
        eval_amount=Decimal("1000000"),
        pnl=Decimal("-1"), pnl_rate=Decimal(rate),
    )


def _eval(rate: str):
    ctx = PositionContext(strategy="ai_swing", entry_time=None)
    return evaluate_holding(_h(rate), resolve_policy(ExitPolicy(), "ai_swing"), ctx=ctx)


def test_default_keeps_hard_stop(monkeypatch):
    """기본값(미설정)은 기존 거동 — -8% 에서 손절한다."""
    monkeypatch.delenv("BARRO_AI_SWING_HARD_SL_ENABLED", raising=False)
    assert he._ai_swing_hard_sl_enabled() is True
    assert _eval("-9.0").signal == SellSignal.STOP_LOSS


def test_flag_off_disables_price_stop(monkeypatch):
    """OFF 면 -9%, -30% 에서도 가격 손절이 발동하지 않는다."""
    monkeypatch.setenv("BARRO_AI_SWING_HARD_SL_ENABLED", "0")
    assert he._ai_swing_hard_sl_enabled() is False
    for r in ("-9.0", "-15.0", "-30.0"):
        assert _eval(r).signal != SellSignal.STOP_LOSS, r
        assert _eval(r).signal != SellSignal.TIME_TIGHTENED_SL, r


def test_flag_off_keeps_take_profit(monkeypatch):
    """OFF 여도 익절 경로는 살아 있어야 한다(min_hold 통과 후)."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("BARRO_AI_SWING_HARD_SL_ENABLED", "0")
    past = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
    ctx = PositionContext(strategy="ai_swing", entry_time=past)
    d = evaluate_holding(_h("+25.0"), resolve_policy(ExitPolicy(), "ai_swing"), ctx=ctx)
    assert d.signal != SellSignal.HOLD
    assert d.sell_qty > 0


def test_flag_off_holds_deep_loss_past_min_hold(monkeypatch):
    """min_hold 를 지난 깊은 손실도 청산하지 않고 보유한다 — OFF 의 핵심 거동."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setenv("BARRO_AI_SWING_HARD_SL_ENABLED", "0")
    past = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
    ctx = PositionContext(strategy="ai_swing", entry_time=past)
    d = evaluate_holding(_h("-30.0"), resolve_policy(ExitPolicy(), "ai_swing"), ctx=ctx)
    assert d.signal == SellSignal.HOLD and d.sell_qty == 0


@pytest.mark.parametrize("raw", ["", "  ", "bogus"])
def test_malformed_env_falls_back_to_enabled(monkeypatch, raw):
    """오타·빈값은 기존 거동(손절 ON)으로 폴백한다 — fail-safe."""
    monkeypatch.setenv("BARRO_AI_SWING_HARD_SL_ENABLED", raw)
    assert he._ai_swing_hard_sl_enabled() is True


def test_other_strategies_unaffected(monkeypatch):
    """OFF 여도 존전략 손절은 그대로."""
    monkeypatch.setenv("BARRO_AI_SWING_HARD_SL_ENABLED", "0")
    ctx = PositionContext(strategy="gold_zone", entry_time=None)
    d = evaluate_holding(_h("-9.0"), resolve_policy(ExitPolicy(), "gold_zone"), ctx=ctx)
    assert d.signal in (SellSignal.STOP_LOSS, SellSignal.TIME_TIGHTENED_SL)
