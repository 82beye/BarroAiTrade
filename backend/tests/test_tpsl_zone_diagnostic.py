"""_tpsl_zone_diagnostic 단위테스트 — net 변환·regime 효과·프로파일 인용.

관측성 도구(config 무변경)라 계산 정확성만 검증.
"""
from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import _tpsl_zone_diagnostic as tz  # noqa: E402


def test_net_after_cost():
    assert tz.net_after_cost(5.0, 0.55) == 4.45
    assert round(tz.net_after_cost(2.0, 0.90), 2) == 1.10


def test_profile_policy_matches_source():
    # STRATEGY_EXIT_PROFILES 진실원천 인용 — f_zone -4/5
    p = tz.profile_policy("f_zone")
    assert float(p.stop_loss_pct) == -4.0
    assert float(p.take_profit_pct) == 5.0


def test_gross_net_table_erosion():
    rows = {r["strategy"]: r for r in tz.gross_net_table()}
    # gold_zone partial +2% → 0.90% 비용이 45% 잠식
    assert rows["gold_zone"]["partial_erosion%[실측]"] == 45.0
    # net TP(정정전 0.55%) 는 gross 보다 비용만큼 작다
    assert rows["f_zone"]["net_tp[정정전]"] == 4.45
    # net TP(실측 0.90%)
    assert rows["f_zone"]["net_tp[실측]"] == 4.10


def test_regime_effect_sideways_tightens():
    rows = {r["strategy"]: r for r in tz.regime_effect_table()}
    # EXAMPLE_REX sideways_sl_mult=0.75 → f_zone -4 → -3
    assert rows["f_zone"]["sideways_sl"] == -3.0
    # BULL tp_mult 1.3 → f_zone 5 → 6.5
    assert rows["f_zone"]["bull_tp"] == 6.5


def test_cost_models_two_assumptions():
    # 정정전 0.55% vs 실측(정정후) ~0.90% 병행
    assert round(tz.COST_MODELS["정정전"], 2) == 0.55
    assert 0.85 < tz.COST_MODELS["실측"] < 0.95
