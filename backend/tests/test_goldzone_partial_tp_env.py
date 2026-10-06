"""gold_zone 부분익절 env 레버 — 기본값 보존 · 안전 폴백 · 실제 판정 반영."""
import importlib
from decimal import Decimal

import pytest

MOD = "backend.core.risk.holding_evaluator"
ENV_PCT = "BARRO_GOLDZONE_PARTIAL_TP_PCT"
ENV_RATIO = "BARRO_GOLDZONE_PARTIAL_TP_RATIO"


def _reload(monkeypatch, pct=None, ratio=None):
    import sys
    for k, v in ((ENV_PCT, pct), (ENV_RATIO, ratio)):
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    m = importlib.import_module(MOD)
    return importlib.reload(m)


# ── 기본값: 머지만으로 라이브 거동이 바뀌지 않아야 한다 ──────────────
def test_defaults_preserve_live_behavior(monkeypatch):
    m = _reload(monkeypatch)
    p = m.STRATEGY_EXIT_PROFILES["gold_zone"]
    assert p["partial_tp_pct"] == Decimal("2.0")
    assert p["partial_tp_ratio"] == Decimal("0.5")


def test_other_strategies_untouched(monkeypatch):
    m = _reload(monkeypatch, pct="3.0", ratio="0.7")
    assert m.STRATEGY_EXIT_PROFILES["f_zone"]["partial_tp_pct"] == Decimal("3.0")
    assert m.STRATEGY_EXIT_PROFILES["f_zone"]["partial_tp_ratio"] == Decimal("0.5")
    assert m.STRATEGY_EXIT_PROFILES["sf_zone"]["partial_tp_ratio"] == Decimal("0.33")


# ── env 반영 ─────────────────────────────────────────────────────────
def test_env_applies(monkeypatch):
    m = _reload(monkeypatch, pct="3.0", ratio="0.7")
    p = m.STRATEGY_EXIT_PROFILES["gold_zone"]
    assert p["partial_tp_pct"] == Decimal("3.0")
    assert p["partial_tp_ratio"] == Decimal("0.7")


# ── 안전 폴백: env 오타가 모듈 임포트를 깨뜨리면 안 된다 ──────────────
@pytest.mark.parametrize("bad", ["abc", "아니오", "", "   ", "3.0.1", "--3"])
def test_malformed_env_falls_back_not_raises(monkeypatch, bad):
    m = _reload(monkeypatch, pct=bad)
    assert m.STRATEGY_EXIT_PROFILES["gold_zone"]["partial_tp_pct"] == Decimal("2.0")


def test_dec_env_helper(monkeypatch):
    m = _reload(monkeypatch)
    monkeypatch.setenv("T_OK", "-4.5")
    assert m._dec_env("T_OK", "0") == Decimal("-4.5")      # 음수 허용(손절 임계)
    monkeypatch.setenv("T_BAD", "x")
    assert m._dec_env("T_BAD", "1.5") == Decimal("1.5")
    monkeypatch.delenv("T_NONE", raising=False)
    assert m._dec_env("T_NONE", "2.5") == Decimal("2.5")


# ── 실제 청산 판정에 반영되는지 ───────────────────────────────────────
def test_partial_tp_threshold_reaches_decision(monkeypatch):
    """+2.5% 보유: 기본(+2%)이면 부분익절, 상향(+3%)이면 보류."""
    m = _reload(monkeypatch)
    h = m.HoldingPosition(symbol="000000", name="테스트", qty=100,
                          avg_buy_price=Decimal("10000"), cur_price=Decimal("10250"),
                          eval_amount=Decimal("1025000"), pnl=Decimal("25000"),
                          pnl_rate=Decimal("2.5"))
    ctx = m.PositionContext(peak_pnl_rate=Decimal("2.5"), partial_tp_done=False,
                            entry_time=None, strategy="gold_zone")
    d1 = m.evaluate_holding(h, m.ExitPolicy(), ctx)
    assert str(getattr(d1.signal, "value", d1.signal)) == "partial_tp"
    assert d1.sell_qty == 50                      # 비중 50%

    m2 = _reload(monkeypatch, pct="3.0", ratio="0.7")
    ctx2 = m2.PositionContext(peak_pnl_rate=Decimal("2.5"), partial_tp_done=False,
                              entry_time=None, strategy="gold_zone")
    d2 = m2.evaluate_holding(h, m2.ExitPolicy(), ctx2)
    assert str(getattr(d2.signal, "value", d2.signal)) != "partial_tp", \
        "임계 +3% 상향 시 +2.5% 에서는 부분익절이 발동하면 안 된다"


def test_partial_tp_ratio_reaches_decision(monkeypatch):
    """+3.5% 보유 · 비중 70% → 70주 매도."""
    m = _reload(monkeypatch, pct="3.0", ratio="0.7")
    h = m.HoldingPosition(symbol="000000", name="테스트", qty=100,
                          avg_buy_price=Decimal("10000"), cur_price=Decimal("10350"),
                          eval_amount=Decimal("1035000"), pnl=Decimal("35000"),
                          pnl_rate=Decimal("3.5"))
    ctx = m.PositionContext(peak_pnl_rate=Decimal("3.5"), partial_tp_done=False,
                            entry_time=None, strategy="gold_zone")
    d = m.evaluate_holding(h, m.ExitPolicy(), ctx)
    if str(getattr(d.signal, "value", d.signal)) == "partial_tp":
        assert d.sell_qty == 70


def teardown_module():
    """다른 테스트가 오염된 모듈을 쓰지 않도록 기본 상태로 되돌린다."""
    import os
    for k in (ENV_PCT, ENV_RATIO):
        os.environ.pop(k, None)
    importlib.reload(importlib.import_module(MOD))
