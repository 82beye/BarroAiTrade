"""ai_swing 종목당 주문금액 상한 — 기본 무제한 · env 상한 · 예산과의 상호작용."""
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
ENV = "BARRO_AI_SWING_MAX_VALUE_PER_POS"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location(
        "_daemon_poscap", _REPO / "scripts" / "intraday_buy_daemon.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["_daemon_poscap"] = m
    spec.loader.exec_module(m)
    return m


# ── 기본 동작 (상한 없음) ─────────────────────────────────────────────
def test_no_cap_by_default(mod, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    # 예산 1억 · 주가 1만원 · 요청 500주 → 요청 그대로
    assert mod._ai_swing_order_qty(500, 10_000.0, 100_000_000.0) == 500


def test_budget_still_caps_without_env(mod, monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    # 예산 300만원 · 주가 1만원 → 300주
    assert mod._ai_swing_order_qty(500, 10_000.0, 3_000_000.0) == 300


# ── 종목당 상한 ───────────────────────────────────────────────────────
def test_cap_limits_qty(mod, monkeypatch):
    monkeypatch.setenv(ENV, "2500000")
    # 상한 250만 · 주가 1만원 → 250주 (요청 500·예산 1억이어도)
    assert mod._ai_swing_order_qty(500, 10_000.0, 100_000_000.0) == 250


def test_cap_rounds_down(mod, monkeypatch):
    monkeypatch.setenv(ENV, "2500000")
    # 주가 18,600원 → 250만/18,600 = 134.4 → 134주
    assert mod._ai_swing_order_qty(400, 18_600.0, 100_000_000.0) == 134


def test_tighter_of_budget_and_cap_wins(mod, monkeypatch):
    monkeypatch.setenv(ENV, "2500000")
    # 예산 100만이 상한 250만보다 작다 → 예산이 이긴다
    assert mod._ai_swing_order_qty(500, 10_000.0, 1_000_000.0) == 100


def test_cap_does_not_raise_qty(mod, monkeypatch):
    monkeypatch.setenv(ENV, "100000000")
    # 상한이 요청보다 크면 요청 그대로 — 상한은 올리는 방향으로 작동하지 않는다
    assert mod._ai_swing_order_qty(50, 10_000.0, 100_000_000.0) == 50


def test_expensive_stock_yields_zero_under_cap(mod, monkeypatch):
    monkeypatch.setenv(ENV, "2500000")
    # 주가 1,399,000원 > 상한 250만 → 1주는 가능 (250만/139.9만 = 1.78 → 1주)
    assert mod._ai_swing_order_qty(10, 1_399_000.0, 100_000_000.0) == 1
    # 주가가 상한보다 크면 0주 → 호출부가 AI-SWING-CAP 으로 건너뛴다
    assert mod._ai_swing_order_qty(10, 3_000_000.0, 100_000_000.0) == 0


# ── env 파싱 ──────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["", "  ", "0", "-1", "아니오", "abc"])
def test_malformed_or_zero_env_means_unlimited(mod, monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    assert mod._ai_swing_max_value_per_pos() == 0.0
    assert mod._ai_swing_order_qty(500, 10_000.0, 100_000_000.0) == 500


def test_scientific_notation_env(mod, monkeypatch):
    monkeypatch.setenv(ENV, "2.5e6")
    assert mod._ai_swing_max_value_per_pos() == pytest.approx(2_500_000.0)
    assert mod._ai_swing_order_qty(500, 10_000.0, 100_000_000.0) == 250


# ── 경계 ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q,p,b", [(0, 10_000.0, 1e8), (-5, 10_000.0, 1e8),
                                   (100, 0.0, 1e8), (100, 10_000.0, 0.0),
                                   (100, 10_000.0, -1.0)])
def test_invalid_inputs_return_zero(mod, monkeypatch, q, p, b):
    monkeypatch.setenv(ENV, "2500000")
    assert mod._ai_swing_order_qty(q, p, b) == 0
