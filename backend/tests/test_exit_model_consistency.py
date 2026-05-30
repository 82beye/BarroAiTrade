"""고도화 Phase 1 #9 — 청산모델 정합성 가시화 테스트 (0-test 해소).

배경(design §P5-2): 청산 임계가 세 곳에서 불일치한다.
  (1) 백테스트 검증 모델  intraday_simulator._scaled_exit_plan: SL -1.5%, TP +3/+5/+7%, trailing ON
  (2) 운영 적응형 청산     holding_evaluator.STRATEGY_EXIT_PROFILES['gold_zone']: SL -4%, TP +4%, partial +2%
  (3) gold 1차 청산        gold_zone.exit_plan(): SL -1.5%, TP +2/+4%

본 테스트는 (1)이 '흑자가 검증된 모델' 임을 회귀 보호하고, (1)↔(2) 격차를
명시적으로 캡처한다 — 한쪽만 바꾸면 테스트가 깨져 삼중 불일치가 가시화된다.
정합(단일화) 결정 자체는 별도 HITL(design rank 9 Phase 3).
"""
from __future__ import annotations

from decimal import Decimal

from backend.core.backtester.intraday_simulator import _scaled_exit_plan, TRAIL_STAGES_AITRADE
from backend.core.risk.holding_evaluator import STRATEGY_EXIT_PROFILES


class TestValidatedExitModel:
    """백테스트 흑자 결론(grid-backtest §6/§7)을 낸 _scaled_exit_plan 회귀 보호."""

    def test_three_tier_tp(self):
        plan = _scaled_exit_plan(Decimal("10000"))
        prices = [tp.price for tp in plan.take_profits]
        qtys = [tp.qty_pct for tp in plan.take_profits]
        assert prices == [Decimal("10300"), Decimal("10500"), Decimal("10700")]  # +3/+5/+7%
        assert qtys == [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")]

    def test_sl_and_breakeven(self):
        plan = _scaled_exit_plan(Decimal("10000"))
        assert plan.stop_loss.fixed_pct == Decimal("-0.015")  # -1.5%
        assert plan.breakeven_trigger == Decimal("0.01")      # +1%

    def test_trailing_on_by_default(self):
        plan = _scaled_exit_plan(Decimal("10000"))
        assert plan.trail_stages == TRAIL_STAGES_AITRADE  # default ON (5단계 변동성 트레일링)
        assert plan.trail_stages is not None and len(plan.trail_stages) == 5

    def test_trailing_off_when_none(self):
        plan = _scaled_exit_plan(Decimal("10000"), trail_stages=None)
        assert plan.trail_stages is None


class TestOperationalGoldProfile:
    """운영 적응형 청산(HoldingEvaluator) gold 프로파일 — 현재값 캡처."""

    def test_gold_profile_values(self):
        p = STRATEGY_EXIT_PROFILES["gold_zone"]
        assert p["stop_loss_pct"] == Decimal("-4.0")
        assert p["take_profit_pct"] == Decimal("4.0")
        assert p["partial_tp_pct"] == Decimal("2.0")


class TestModelDivergence:
    """삼중 불일치 명시 캡처 — 한쪽만 바꾸면 깨져 가시화. (정합은 별도 HITL)"""

    def test_sl_divergence_simulator_vs_operational(self):
        sim_sl = _scaled_exit_plan(Decimal("10000")).stop_loss.fixed_pct  # -0.015
        op_sl = STRATEGY_EXIT_PROFILES["gold_zone"]["stop_loss_pct"] / Decimal("100")  # -0.04
        # 의도된 2차 안전망 격차(sl-gap-decision.md): sim -1.5% vs 운영 -4%
        assert sim_sl != op_sl
        assert sim_sl == Decimal("-0.015") and op_sl == Decimal("-0.04")

    def test_tp_divergence_simulator_vs_operational(self):
        sim_tp_top = _scaled_exit_plan(Decimal("10000")).take_profits[-1].price  # 10700 (+7%)
        op_tp = Decimal("10000") * (Decimal("1") + STRATEGY_EXIT_PROFILES["gold_zone"]["take_profit_pct"] / Decimal("100"))  # 10400 (+4%)
        # 검증 흑자 모델은 +7%까지 회수, 운영 익절은 +4% — 우측꼬리 회수 격차
        assert sim_tp_top != op_tp
        assert sim_tp_top == Decimal("10700") and op_tp == Decimal("10400.0")
