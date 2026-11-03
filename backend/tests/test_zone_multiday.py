"""존3 다일보유(C안) — SL·보유기간 env + EOD 강제청산 제외."""
import importlib
import subprocess
from decimal import Decimal

import pytest

import backend.core.risk.holding_evaluator as he


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return importlib.reload(he)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(he)


def test_defaults_preserve_current_behavior(monkeypatch):
    """미설정 기본값은 기존 계약 — SL -4.0 · 보유기간 None(당일매매)."""
    m = _reload(monkeypatch, BARRO_ZONE_SL_PCT=None,
                BARRO_ZONE_MIN_HOLD_DAYS=None, BARRO_ZONE_MAX_HOLD_DAYS=None,
                BARRO_ZONE_TIGHTENED_SL_PCT=None)
    for z, tight in (("f_zone", "-2.5"), ("sf_zone", "-2.5"), ("gold_zone", "-3.0")):
        p = m.STRATEGY_EXIT_PROFILES[z]
        assert p["stop_loss_pct"] == Decimal("-4.0"), z
        assert p["tightened_sl_pct"] == Decimal(tight), z   # 전략별 기존값 보존
        assert p["min_hold_days"] is None and p["max_hold_days"] is None, z


def test_env_widens_stop_and_sets_hold(monkeypatch):
    m = _reload(monkeypatch, BARRO_ZONE_SL_PCT="-7.0",
                BARRO_ZONE_TIGHTENED_SL_PCT="-5.0",
                BARRO_ZONE_MIN_HOLD_DAYS="1", BARRO_ZONE_MAX_HOLD_DAYS="5")
    for z in ("f_zone", "sf_zone", "gold_zone"):
        p = m.STRATEGY_EXIT_PROFILES[z]
        assert p["stop_loss_pct"] == Decimal("-7.0"), z
        assert p["tightened_sl_pct"] == Decimal("-5.0"), z
        assert p["min_hold_days"] == 1 and p["max_hold_days"] == 5, z


@pytest.mark.parametrize("raw", ["0", "", "bogus", "-3"])
def test_hold_days_malformed_is_none(monkeypatch, raw):
    """0·빈값·오타·음수는 미설정(None) — 기존 당일매매 계약으로 폴백."""
    m = _reload(monkeypatch, BARRO_ZONE_MAX_HOLD_DAYS=raw)
    assert m.STRATEGY_EXIT_PROFILES["f_zone"]["max_hold_days"] is None


def test_ai_swing_profile_untouched(monkeypatch):
    """존3 env 는 ai_swing 프로파일을 건드리지 않는다."""
    m = _reload(monkeypatch, BARRO_ZONE_SL_PCT="-7.0")
    assert m.STRATEGY_EXIT_PROFILES["ai_swing"]["stop_loss_pct"] != Decimal("-7.0")


def _exclude_from_sh(env_val: str) -> str:
    """eod_force_close.sh 의 EXCLUDE 산출만 떼어내 검증."""
    script = (
        'EXCLUDE="supertrend,limit_up_chase,swing_38,ai_swing"; '
        f'BARRO_ZONE_MULTIDAY_ENABLED="{env_val}"; '
        'case "${BARRO_ZONE_MULTIDAY_ENABLED:-0}" in '
        '1|true|TRUE|yes|on) EXCLUDE="$EXCLUDE,f_zone,sf_zone,gold_zone" ;; esac; '
        'printf %s "$EXCLUDE"'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True).stdout


def test_eod_exclude_list_off_by_default():
    assert "f_zone" not in _exclude_from_sh("0")
    assert "f_zone" not in _exclude_from_sh("")


def test_eod_exclude_list_on():
    out = _exclude_from_sh("1")
    for z in ("f_zone", "sf_zone", "gold_zone"):
        assert z in out
