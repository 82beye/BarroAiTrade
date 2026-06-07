"""BAR-OPS-34 — 장마감 강제청산 일시 해제 토글 (evaluate_holdings)."""
from __future__ import annotations

from scripts.evaluate_holdings import _eod_force_close_disabled


def test_disabled_only_when_force_mode_and_env_truthy():
    # force_mode + env truthy → 해제(True)
    assert _eod_force_close_disabled(True, {"EOD_FORCE_CLOSE_DISABLED": "1"}) is True
    assert _eod_force_close_disabled(True, {"EOD_FORCE_CLOSE_DISABLED": "true"}) is True
    assert _eod_force_close_disabled(True, {"EOD_FORCE_CLOSE_DISABLED": "ON"}) is True


def test_not_disabled_when_env_off_or_missing():
    assert _eod_force_close_disabled(True, {}) is False
    assert _eod_force_close_disabled(True, {"EOD_FORCE_CLOSE_DISABLED": "0"}) is False
    assert _eod_force_close_disabled(True, {"EOD_FORCE_CLOSE_DISABLED": ""}) is False


def test_normal_mode_unaffected():
    # force_mode=False(일반 TP/SL 자동매도) 는 env 와 무관하게 해제 안 함
    assert _eod_force_close_disabled(False, {"EOD_FORCE_CLOSE_DISABLED": "1"}) is False
    assert _eod_force_close_disabled(False, {}) is False
