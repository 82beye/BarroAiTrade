"""sector 핫테마 우선순위 배선 테스트 — apply_sector_priority (soft 재정렬).

거버넌스: off/stale/핫테마 없음 → 신호 무변경(byte-identical). 핫테마(거래대금 집중) 중
under-exposed 신호를 앞으로 당김. 이미 과다 노출 테마는 가점 제외(쏠림 가드가 처리).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.core.journal.policy_config import PolicyConfig
from backend.core.risk.market_context import (
    PortfolioSignals,
    SectorThemes,
    SectorThemesConfig,
    apply_sector_priority,
)

_NOW = datetime(2026, 6, 23, 5, 30, tzinfo=timezone.utc)
_TM = {"005930": ["반도체"], "000660": ["반도체"], "373220": ["2차전지"], "207940": ["바이오"]}


def _sig(symbol):
    return (SimpleNamespace(symbol=symbol, name="종목"), "f_zone", 1000.0)


def _sector(hot, age=10):
    return SectorThemes(hot=tuple(hot), ts=_NOW - timedelta(seconds=age))


def _psig(exposure):
    return PortfolioSignals(theme_exposure=exposure, ts=_NOW - timedelta(seconds=10))


def _syms(signals):
    return [s[0].symbol for s in signals]


def test_default_config_disabled():
    assert SectorThemesConfig.from_policy_config(PolicyConfig()).enabled is False


def test_disabled_noop():
    cfg = SectorThemesConfig(enabled=False)
    sigs = [_sig("373220"), _sig("005930")]
    out, boosted = apply_sector_priority(sigs, cfg, _sector([{"theme": "반도체", "turnover_pct": 0.5}]),
                                         _psig({}), _TM, _NOW)
    assert out is sigs and boosted == []


def test_hot_underexposed_moves_to_front():
    """반도체가 거래대금 집중 핫테마이고 미보유 → 반도체 신호가 앞으로."""
    cfg = SectorThemesConfig(enabled=True)
    sigs = [_sig("373220"), _sig("207940"), _sig("005930")]   # 005930=반도체 맨 뒤
    sector = _sector([{"theme": "반도체", "turnover_pct": 0.45}])
    out, boosted = apply_sector_priority(sigs, cfg, sector, _psig({}), _TM, _NOW)
    assert _syms(out)[0] == "005930"                          # 핫테마 신호가 선두로
    assert _syms(out)[1:] == ["373220", "207940"]             # 나머지 원순서 유지(stable)
    assert boosted == [("005930", 0.45)]


def test_overexposed_hot_theme_not_boosted():
    """반도체가 핫테마지만 이미 과다 노출(0.5 ≥ 0.30) → 가점 제외(쏠림 가드 영역)."""
    cfg = SectorThemesConfig(enabled=True, underexposed_max_pct=0.30)
    sigs = [_sig("373220"), _sig("005930")]
    sector = _sector([{"theme": "반도체", "turnover_pct": 0.5}])
    out, boosted = apply_sector_priority(sigs, cfg, sector, _psig({"반도체": 0.5}), _TM, _NOW)
    assert _syms(out) == ["373220", "005930"] and boosted == []   # 무변경


def test_stale_or_no_hot_noop():
    cfg = SectorThemesConfig(enabled=True, ttl_sec=600)
    sigs = [_sig("373220"), _sig("005930")]
    # stale
    out, _ = apply_sector_priority(sigs, cfg, _sector([{"theme": "반도체", "turnover_pct": 0.5}], age=9999),
                                   _psig({}), _TM, _NOW)
    assert out is sigs
    # 핫테마 없음
    out2, b2 = apply_sector_priority(sigs, cfg, _sector([]), _psig({}), _TM, _NOW)
    assert out2 is sigs and b2 == []


def test_min_turnover_filter():
    """min_turnover_pct 미만 핫테마는 무시 → 가점 없음."""
    cfg = SectorThemesConfig(enabled=True, min_turnover_pct=0.20)
    sigs = [_sig("373220"), _sig("005930")]
    sector = _sector([{"theme": "반도체", "turnover_pct": 0.10}])   # 0.10 < 0.20
    out, boosted = apply_sector_priority(sigs, cfg, sector, _psig({}), _TM, _NOW)
    assert _syms(out) == ["373220", "005930"] and boosted == []


def test_multiple_hot_themes_ranked_by_turnover():
    """여러 핫테마 → turnover 큰 테마 신호가 더 앞으로."""
    cfg = SectorThemesConfig(enabled=True)
    sigs = [_sig("207940"), _sig("373220"), _sig("005930")]   # 바이오/2차전지/반도체
    sector = _sector([{"theme": "반도체", "turnover_pct": 0.5},
                      {"theme": "2차전지", "turnover_pct": 0.3}])   # 바이오는 핫 아님
    out, _ = apply_sector_priority(sigs, cfg, sector, _psig({}), _TM, _NOW)
    assert _syms(out) == ["005930", "373220", "207940"]      # 반도체 > 2차전지 > 바이오(가점0)
