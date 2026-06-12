"""round_figure 모듈 테스트 — 라운드 지지/저항 + 손절 보정 + env 토글(2026-06-12)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.core.strategy.round_figure import (
    floor_to_tick,
    nearest_round_resistance,
    nearest_round_support,
    resolve_sl_pct,
    round_figure_stop_pct,
)


# ════════════════════════════ 라운드 지지/저항 ═══════════════════════════════
@pytest.mark.parametrize("price,support", [
    (153_900, 150_000),     # 50k-200k: minor 5k (major 150k는 1.5% 밖)
    (51_200, 50_000),       # minor 5k
    (201_500, 200_000),     # 200k-500k: major 100k 가 0.74%≤1.5% 근접 → 200k
    (8_700, 8_000),         # 5k-10k: minor 1k
    (350_761, 350_000),     # 200k-500k: minor 10k (major 300k 밖)
    (999, 900),             # <1k: minor 100
    (4_800, 4_500),         # 1k-5k: minor 500
    (1_270_000, 1_250_000), # ≥500k: minor 50k (1.25M, major 1.2M는 1.5% 밖)
])
def test_nearest_round_support(price, support):
    assert nearest_round_support(price) == support


@pytest.mark.parametrize("price,resistance", [
    (153_900, 155_000),     # minor 5k 위
    (51_200, 55_000),       # minor 5k 위
    (8_700, 9_000),         # minor 1k 위
    (50_000, 55_000),       # 정확히 라운드선 위 → 다음 minor
])
def test_nearest_round_resistance(price, resistance):
    assert nearest_round_resistance(price) == resistance


def test_support_on_exact_level():
    # 정확히 라운드선이면 그 선이 지지(=가격) → 손절은 그 바로 아래.
    assert nearest_round_support(150_000) == 150_000


def test_floor_to_tick():
    # 149,550 → 호가단위 100(가격<200k) 격자 정렬 → 149,500
    assert floor_to_tick(149_550) == 149_500
    # 8,000 (가격<10k → tick 10) → 8,000
    assert floor_to_tick(8_000) == 8_000


# ════════════════════════════ 손절률 산출 ════════════════════════════════════
def test_round_figure_stop_widens_to_support():
    # entry 153,900, base -2% (-0.02). 지지 150,000, buffer=max(450, 100)=450,
    # raw_stop=floor_to_tick(149,550)=149,500. rf=(149500-153900)/153900≈-0.0286.
    # looser(base -0.02, rf -0.0286)=-0.0286, max_stop 0.04 이내 → -0.0286.
    sl = round_figure_stop_pct(153_900, base_pct=-0.02, max_stop_pct=0.04)
    assert sl == pytest.approx(-0.0286, abs=1e-3)
    assert sl < 0


def test_round_figure_stop_clamped_to_max():
    # 지지선이 매우 먼 경우라도 max_stop 보다 깊지 않게 클램프.
    sl = round_figure_stop_pct(160_000, base_pct=-0.02, max_stop_pct=0.04)
    assert sl >= -0.04
    assert sl < 0


def test_round_figure_stop_keeps_base_when_tighter():
    # rf 가 base 보다 타이트하면 base 유지(넉넉한 쪽 채택).
    # entry 152,000, 지지 150,000 매우 근접 → rf 얕음(>-0.02) → base -0.02 유지.
    sl = round_figure_stop_pct(152_000, base_pct=-0.02, max_stop_pct=0.04)
    assert sl == pytest.approx(-0.02, abs=1e-9)


def test_round_figure_stop_always_negative():
    for entry in (8_700, 51_200, 153_900, 201_500, 350_761, 1_250_000):
        sl = round_figure_stop_pct(entry, base_pct=-0.02, max_stop_pct=0.04)
        assert sl < 0


def test_round_figure_stop_bad_entry_returns_base():
    assert round_figure_stop_pct(0, base_pct=-0.02, max_stop_pct=0.04) == -0.02
    assert round_figure_stop_pct(-5, base_pct=-0.02, max_stop_pct=0.04) == -0.02


# ════════════════════════════ resolve_sl_pct (env 토글) ══════════════════════
def test_resolve_disabled_returns_base(monkeypatch):
    monkeypatch.delenv("RF_STOP_ENABLED", raising=False)
    out = resolve_sl_pct("f_zone", 153_900, Decimal("-0.02"))
    assert out == Decimal("-0.02")
    assert isinstance(out, Decimal)


def test_resolve_dry_run_returns_base_but_computes(monkeypatch):
    monkeypatch.setenv("RF_STOP_ENABLED", "1")
    monkeypatch.setenv("RF_STOP_DRY_RUN", "1")
    out = resolve_sl_pct("f_zone", 153_900, Decimal("-0.02"))
    assert out == Decimal("-0.02")   # dry-run: base 유지


def test_resolve_applied_changes_stop(monkeypatch):
    monkeypatch.setenv("RF_STOP_ENABLED", "1")
    monkeypatch.setenv("RF_STOP_DRY_RUN", "0")
    out = resolve_sl_pct("f_zone", 153_900, Decimal("-0.02"))
    assert out != Decimal("-0.02")
    assert Decimal(str(out)) < Decimal("0")
    assert float(out) == pytest.approx(-0.0286, abs=1e-3)


def test_resolve_percent_unit(monkeypatch):
    # HoldingEvaluator 단위(percent): -4.0 base, swing max_stop 0.15.
    monkeypatch.setenv("RF_STOP_ENABLED", "1")
    monkeypatch.setenv("RF_STOP_DRY_RUN", "0")
    out = resolve_sl_pct("swing_38", 153_900, Decimal("-4.0"), unit="percent")
    # rf fraction ≈ -0.0286 → percent ≈ -2.86; looser(base -4%, rf -2.86%)=-4% (base 더 넉넉)
    assert float(out) == pytest.approx(-4.0, abs=1e-6)


def test_resolve_percent_unit_widens(monkeypatch):
    # base -1.5% (percent), 지지 멀면 rf 가 더 넉넉 → 넓혀짐, swing max 15% 이내.
    monkeypatch.setenv("RF_STOP_ENABLED", "1")
    monkeypatch.setenv("RF_STOP_DRY_RUN", "0")
    out = resolve_sl_pct("swing_38", 153_900, Decimal("-1.5"), unit="percent")
    assert float(out) == pytest.approx(-2.86, abs=0.1)


def test_resolve_max_stop_tier(monkeypatch):
    # intraday(f_zone)는 RF_MAX_STOP_PCT_INTRADAY(0.04) 로 클램프.
    monkeypatch.setenv("RF_STOP_ENABLED", "1")
    monkeypatch.setenv("RF_STOP_DRY_RUN", "0")
    monkeypatch.setenv("RF_MAX_STOP_PCT_INTRADAY", "0.04")
    out = resolve_sl_pct("f_zone", 160_000, Decimal("-0.02"))
    assert float(out) >= -0.04
