"""BAR-OPS-10 — INTRADAY_ONLY_STRATEGIES 상수 + daily_backtest 경고 검증."""
from __future__ import annotations

import logging

from backend.core.backtester import INTRADAY_ONLY_STRATEGIES
from backend.core.backtester.intraday_simulator import _build_strategies


def test_intraday_only_strategies_constant():
    assert "scalping_consensus" in INTRADAY_ONLY_STRATEGIES
    assert "swing_38" not in INTRADAY_ONLY_STRATEGIES
    assert "f_zone" not in INTRADAY_ONLY_STRATEGIES


def test_intraday_only_is_frozenset():
    assert isinstance(INTRADAY_ONLY_STRATEGIES, frozenset)


def test_build_strategies_daily_backtest_logs_info(caplog):
    with caplog.at_level(logging.INFO, logger="backend.core.backtester.intraday_simulator"):
        _build_strategies(["swing_38", "scalping_consensus"], daily_backtest=True)
    msgs = [r.message for r in caplog.records]
    assert any("scalping_consensus" in m and "INTRADAY_ONLY_STRATEGIES" in m for m in msgs)


def test_build_strategies_no_log_without_flag(caplog):
    with caplog.at_level(logging.INFO, logger="backend.core.backtester.intraday_simulator"):
        _build_strategies(["swing_38", "scalping_consensus"], daily_backtest=False)
    msgs = [r.message for r in caplog.records if "INTRADAY_ONLY_STRATEGIES" in r.message]
    assert not msgs


def test_build_strategies_non_intraday_no_log(caplog):
    with caplog.at_level(logging.INFO, logger="backend.core.backtester.intraday_simulator"):
        _build_strategies(["swing_38", "gold_zone"], daily_backtest=True)
    msgs = [r.message for r in caplog.records if "INTRADAY_ONLY_STRATEGIES" in r.message]
    assert not msgs
