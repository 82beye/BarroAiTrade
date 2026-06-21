"""regime_exit 단위테스트 — default-OFF parity(동일 객체) + 국면별 조정.

핵심: enabled=False / regime=None / 배수 default 1.0 → 입력 policy 그대로(byte-identical).
활성 + 배수 지정 시에만 SL/TP/보유 조정.
"""
from __future__ import annotations

from decimal import Decimal

from backend.core.backtester.market_regime import MarketRegime
from backend.core.risk.holding_evaluator import ExitPolicy
from backend.core.risk.regime_exit import adjust_policy_for_regime


class TestDisabledParity:
    def test_disabled_returns_same_object(self):
        p = ExitPolicy()
        assert adjust_policy_for_regime(p, MarketRegime.SIDEWAYS, enabled=False) is p

    def test_none_regime_returns_same_object(self):
        p = ExitPolicy()
        assert adjust_policy_for_regime(p, None, enabled=True) is p

    def test_mults_default_identity_returns_same_object(self):
        """enabled=True 라도 모든 배수 default 1.0 → 동일 객체(이중 안전판)."""
        p = ExitPolicy()
        for regime in (MarketRegime.SIDEWAYS, MarketRegime.BULL, MarketRegime.BEARISH):
            assert adjust_policy_for_regime(p, regime, enabled=True) is p


class TestSidewaysTighten:
    def test_sideways_tightens_sl(self):
        """SIDEWAYS + sl_mult 0.75 → -4.0 → -3.0."""
        p = ExitPolicy()  # SL -4.0
        q = adjust_policy_for_regime(
            p, MarketRegime.SIDEWAYS, enabled=True, sideways_sl_mult=0.75,
        )
        assert q is not p
        assert q.stop_loss_pct == Decimal("-3.0")
        assert q.take_profit_pct == p.take_profit_pct  # tp_mult 1.0 → 불변

    def test_sideways_tightens_tightened_sl_too(self):
        """SL 배수는 tightened_sl_pct 에도 비례 적용(일관성)."""
        p = ExitPolicy(tightened_sl_pct=Decimal("-2.0"))
        q = adjust_policy_for_regime(
            p, MarketRegime.SIDEWAYS, enabled=True, sideways_sl_mult=0.5,
        )
        assert q.tightened_sl_pct == Decimal("-1.0")

    def test_sideways_max_hold_override(self):
        """6월 변동성장 보유 단축: max_hold 20 → 2."""
        p = ExitPolicy(max_hold_days=20)
        q = adjust_policy_for_regime(
            p, MarketRegime.SIDEWAYS, enabled=True, sideways_max_hold_days=2,
        )
        assert q.max_hold_days == 2


class TestBull:
    def test_bull_expands_tp(self):
        """BULL + tp_mult 1.4 → 5.0 → 7.0."""
        p = ExitPolicy()  # TP 5.0
        q = adjust_policy_for_regime(
            p, MarketRegime.BULL, enabled=True, bull_tp_mult=1.4,
        )
        assert q.take_profit_pct == Decimal("7.0")
        assert q.stop_loss_pct == p.stop_loss_pct  # sl_mult 1.0 → 불변


class TestBearish:
    def test_bearish_widens_sl(self):
        """BEARISH + sl_mult 1.5 → -4.0 → -6.0 (하락장 더 너그럽게 잡거나, 보수면 <1)."""
        p = ExitPolicy()
        q = adjust_policy_for_regime(
            p, MarketRegime.BEARISH, enabled=True, bearish_sl_mult=1.5,
        )
        assert q.stop_loss_pct == Decimal("-6.0")


class TestRegimeExitConfig:
    def test_default_config_apply_is_noop(self):
        from backend.core.risk.regime_exit import RegimeExitConfig
        p = ExitPolicy()
        assert RegimeExitConfig().apply(p, MarketRegime.SIDEWAYS) is p

    def test_config_apply_tightens(self):
        from backend.core.risk.regime_exit import RegimeExitConfig
        p = ExitPolicy()
        q = RegimeExitConfig(enabled=True, sideways_sl_mult=0.75).apply(p, MarketRegime.SIDEWAYS)
        assert q.stop_loss_pct == Decimal("-3.0")

    def test_from_policy_config_default_off(self):
        from backend.core.journal.policy_config import PolicyConfig
        from backend.core.risk.regime_exit import RegimeExitConfig
        rx = RegimeExitConfig.from_policy_config(PolicyConfig())
        assert rx.enabled is False
        assert rx.sideways_sl_mult == 1.0

    def test_from_policy_config_reads_fields(self):
        from backend.core.journal.policy_config import PolicyConfig
        from backend.core.risk.regime_exit import RegimeExitConfig
        cfg = PolicyConfig(
            regime_exit_enabled=True, regime_sideways_sl_mult=0.5,
            regime_bull_tp_mult=1.4, regime_sideways_max_hold_days=2,
        )
        rx = RegimeExitConfig.from_policy_config(cfg)
        assert rx.enabled is True
        assert rx.sideways_sl_mult == 0.5
        assert rx.bull_tp_mult == 1.4
        assert rx.sideways_max_hold_days == 2


class TestEvaluateHoldingIntegration:
    """evaluate_holding 이 조정된 정책을 실제로 사용하는지(end-to-end)."""

    def _h(self, pnl_rate: str):
        from backend.core.gateway.kiwoom_native_account import HoldingPosition
        return HoldingPosition(
            symbol="005930", name="삼성전자", qty=10,
            avg_buy_price=Decimal("260000"), cur_price=Decimal("253500"),
            eval_amount=Decimal("2535000"), pnl=Decimal("-65000"),
            pnl_rate=Decimal(pnl_rate),
        )

    def test_sideways_tighten_triggers_stop_loss(self):
        """-2.5% 보유: f_zone(SL -4%)는 미발동, SIDEWAYS sl_mult 0.5(→ -2%)는 STOP_LOSS."""
        from backend.core.risk.holding_evaluator import (
            ExitPolicy as EP, PositionContext, SellSignal, evaluate_holding,
        )
        from backend.core.risk.regime_exit import RegimeExitConfig

        h = self._h("-2.5")
        plain = PositionContext(strategy="f_zone")
        assert evaluate_holding(h, EP(), plain).signal != SellSignal.STOP_LOSS

        tight = PositionContext(
            strategy="f_zone", regime=MarketRegime.SIDEWAYS,
            regime_exit=RegimeExitConfig(enabled=True, sideways_sl_mult=0.5),
        )
        assert evaluate_holding(h, EP(), tight).signal == SellSignal.STOP_LOSS

    def test_no_regime_exit_is_byte_identical(self):
        """regime_exit 미주입 시 기존 평가와 동일(회귀 보존)."""
        from backend.core.risk.holding_evaluator import (
            ExitPolicy as EP, PositionContext, evaluate_holding,
        )
        h = self._h("-2.5")
        d1 = evaluate_holding(h, EP(), PositionContext(strategy="f_zone"))
        d2 = evaluate_holding(
            h, EP(),
            PositionContext(strategy="f_zone", regime=MarketRegime.SIDEWAYS, regime_exit=None),
        )
        assert d1.signal == d2.signal == evaluate_holding(h, EP(), PositionContext(strategy="f_zone")).signal
