"""_signal_decision_audit 단위테스트 — 게이트 replay 판정.

관측성 도구(config 무변경)라 게이트 결정 로직만 검증.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import _signal_decision_audit as sd  # noqa: E402
from backend.core.strategy.trap_guard import TrapGuardConfig
from backend.models.market import MarketType, OHLCV

_KST = timezone(timedelta(hours=9))


def _flat(n=20, price=100_000.0, rng=0.02):
    base = datetime(2026, 6, 1, tzinfo=_KST)
    return [OHLCV(symbol="T", timestamp=base + timedelta(days=i), open=price,
                  high=price * (1 + rng / 2), low=price * (1 - rng / 2), close=price,
                  volume=1e6, market_type=MarketType.STOCK) for i in range(n)]


def _wick_bar():
    return OHLCV(symbol="T", timestamp=datetime(2026, 6, 19, tzinfo=_KST),
                 open=100_000, high=115_000, low=99_000, close=105_000, volume=3e6,
                 market_type=MarketType.STOCK)  # 윗꼬리 2.0x


_TRAP = TrapGuardConfig(over_ext_k_atr=2.5, upper_wick_max=1.0, gap_atr_mult=3.0, gap_abs_max_pct=15.0)


def test_skip_gap_high_flu_fzone():
    m = {"flu": 20.0, "candles": _flat()}
    verdict, reason = sd.gate_replay(m, "f_zone", _TRAP)
    assert verdict == "SKIP-GAP"
    assert "15.0%" in reason


def test_skip_trap_upper_wick():
    # flu 7% (갭가드 미발동) + 윗꼬리 봉 → 트랩
    m = {"flu": 7.0, "candles": _flat() + [_wick_bar()]}
    verdict, reason = sd.gate_replay(m, "gold_zone", _TRAP)
    assert verdict == "SKIP-TRAP"
    assert "upper_wick" in reason


def test_pass_clean():
    # 정상: 낮은 flu, 윗꼬리 없는 봉, 과확장 아님
    clean = _flat() + [OHLCV(symbol="T", timestamp=datetime(2026, 6, 19, tzinfo=_KST),
                             open=100_000, high=100_600, low=99_500, close=100_500, volume=3e6,
                             market_type=MarketType.STOCK)]
    m = {"flu": 4.0, "candles": clean}
    verdict, _ = sd.gate_replay(m, "f_zone", _TRAP)
    assert verdict == "PASS"


def test_trap_off_is_pass():
    # 트랩 임계 전부 0 → any_enabled False → 갭가드만 (저flu면 PASS)
    off = TrapGuardConfig()
    m = {"flu": 7.0, "candles": _flat() + [_wick_bar()]}
    verdict, _ = sd.gate_replay(m, "swing_38", off)  # swing_38은 갭가드 제외
    assert verdict == "PASS"


def test_gap_guard_only_for_zone_strategies():
    # swing_38·sf_zone 등은 _GAP_GUARD_STRATEGIES(gold_zone,f_zone) 밖 → 고flu여도 갭가드 미발동
    m = {"flu": 20.0, "candles": _flat()}
    verdict, _ = sd.gate_replay(m, "swing_38", TrapGuardConfig())
    assert verdict == "PASS"
