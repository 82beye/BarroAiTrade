"""시장-맥락 add-on 테스트 — theme_map 집계 + market_context 로더/config/apply.

거버넌스 핵심: 각 add-on enabled=False(default) → apply 무변경(byte-identical),
섹션 부재/stale/미매핑 → fail-open. 결정적 집계(테마 turnover/노출%)는 정확.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.core.journal.policy_config import PolicyConfig
from backend.core.risk.market_context import (
    HARD,
    MarketContext,
    MarketContextConfig,
    PortfolioRiskConfig,
    PortfolioSignals,
    PortfolioThemeConfig,
    apply_market_context,
    apply_portfolio_risk,
    apply_theme_guard,
    load_market_advisory,
)
from backend.core.risk.theme_map import (
    hot_themes,
    load_theme_map,
    theme_exposure,
    themes_of,
)

_NOW = datetime(2026, 6, 23, 5, 30, tzinfo=timezone.utc)
_TM = {"005930": ["반도체"], "000660": ["반도체", "HBM"], "373220": ["2차전지"]}


def _sig(symbol, strategy="f_zone"):
    return (SimpleNamespace(symbol=symbol, name="종목"), strategy, 1000.0)


# ── theme_map ─────────────────────────────────────────────────────────────────

def test_load_theme_map_forms(tmp_path):
    p = tmp_path / "theme_map.json"
    p.write_text(json.dumps({"map": {"005930": ["반도체"], "X": "AI", "Y": []}}), encoding="utf-8")
    tm = load_theme_map(p)
    assert tm["005930"] == ["반도체"] and tm["X"] == ["AI"] and "Y" not in tm
    # flat dict 형식도 허용
    p.write_text(json.dumps({"005930": ["반도체"]}), encoding="utf-8")
    assert load_theme_map(p)["005930"] == ["반도체"]


def test_load_theme_map_failsafe(tmp_path):
    assert load_theme_map(tmp_path / "nope.json") == {}
    bad = tmp_path / "b.json"; bad.write_text("{bad", encoding="utf-8")
    assert load_theme_map(bad) == {}


def test_themes_of():
    assert themes_of("000660", _TM) == ["반도체", "HBM"]
    assert themes_of("999999", _TM) == []


def test_hot_themes_turnover_concentration():
    """거래대금 집계 + 테마 share. 005930·000660(반도체) 거래대금 합산이 1위."""
    leaders = [{"symbol": "005930", "trade_value": 6e11},
               {"symbol": "000660", "trade_value": 4e11},
               {"symbol": "373220", "trade_value": 1e11}]
    hot = hot_themes(leaders, _TM)
    assert hot[0]["theme"] == "반도체" and hot[0]["rank"] == 1
    assert hot[0]["turnover"] == 1e12                      # 6e11+4e11
    assert hot[0]["turnover_pct"] == round(1e12 / 1.1e12, 4)   # 소수 4자리 반올림
    assert set(hot[0]["symbols"]) == {"005930", "000660"}
    # HBM(000660 단독) 도 등장(복수테마 중복 가산)
    assert any(h["theme"] == "HBM" for h in hot)


def test_hot_themes_empty():
    assert hot_themes([], _TM) == []
    assert hot_themes([{"symbol": "005930", "trade_value": 0}], _TM) == []


def test_theme_exposure():
    """보유 평가액 → 테마 노출 비중."""
    positions = [{"symbol": "005930", "eval_value": 30}, {"symbol": "000660", "eval_value": 50},
                 {"symbol": "373220", "eval_value": 20}]
    exp = theme_exposure(positions, _TM)
    assert abs(exp["반도체"] - 0.8) < 1e-6           # (30+50)/100
    assert abs(exp["2차전지"] - 0.2) < 1e-6
    assert theme_exposure([], _TM) == {}


# ── load_market_advisory (backward-compat) ────────────────────────────────────

def test_load_market_advisory_full(tmp_path):
    p = tmp_path / "advisory.json"
    p.write_text(json.dumps({
        "verdicts": [{"symbol": "005930", "action": "GO"}],         # 기존 — 무시되지 않음(공존)
        "market_context": {"regime": "BEARISH", "risk_on": False, "confidence": 0.8,
                           "strategy_gates": {"f_zone": True, "gold_zone": False},
                           "ts": "2026-06-23T05:29:50Z", "source": "macro"},
        "sector_themes": {"hot": [{"theme": "반도체", "turnover_pct": 0.4}],
                          "ts": "2026-06-23T05:29:50Z"},
        "portfolio_signals": {"theme_exposure": {"반도체": 0.42}, "concentration_pct": 0.42,
                              "leverage_warn": False, "ts": "2026-06-23T05:29:50Z"},
    }), encoding="utf-8")
    adv = load_market_advisory(p)
    assert adv.market_context.regime == "bearish" and adv.market_context.risk_on is False
    assert adv.market_context.strategy_gates["gold_zone"] is False
    assert adv.sector_themes.hot_names() == {"반도체"}
    assert adv.portfolio_signals.theme_exposure["반도체"] == 0.42


def test_load_market_advisory_failopen(tmp_path):
    # 부재/구버전(verdicts만) → 빈 기본값(fail-open)
    assert load_market_advisory(tmp_path / "x.json").market_context.regime == "unknown"
    p = tmp_path / "old.json"; p.write_text(json.dumps({"verdicts": []}), encoding="utf-8")
    adv = load_market_advisory(p)
    assert adv.market_context.regime == "unknown" and adv.sector_themes.hot == ()


# ── config 기본값(전부 OFF) ───────────────────────────────────────────────────

def test_configs_default_disabled():
    cfg = PolicyConfig()
    assert MarketContextConfig.from_policy_config(cfg).enabled is False
    assert PortfolioThemeConfig.from_policy_config(cfg).enabled is False
    assert PortfolioRiskConfig.from_policy_config(cfg).enabled is False
    assert PortfolioThemeConfig.from_policy_config(cfg).max_theme_pct == 0.30


# ── apply_market_context ──────────────────────────────────────────────────────

def _mc(regime="bearish", risk_on=False, gates=None, age=10):
    return MarketContext(regime=regime, risk_on=risk_on, confidence=0.8,
                         strategy_gates=gates or {}, ts=_NOW - timedelta(seconds=age))


def test_market_context_disabled_noop():
    cfg = MarketContextConfig(enabled=False)
    mb, kept, notes = apply_market_context(2, [_sig("005930")], cfg, _mc(), _NOW)
    assert mb == 2 and len(kept) == 1 and notes == []


def test_market_context_riskoff_reduces_max_buy():
    cfg = MarketContextConfig(enabled=True)
    mb, kept, notes = apply_market_context(2, [_sig("005930")], cfg, _mc(), _NOW)
    assert mb == 1 and notes                            # 2→1
    # risk-on 이면 무변경
    mb2, _, _ = apply_market_context(2, [_sig("005930")], cfg, _mc(regime="bull", risk_on=True), _NOW)
    assert mb2 == 2


def test_market_context_hard_strategy_gate():
    cfg = MarketContextConfig(enabled=True, mode=HARD)
    sigs = [_sig("005930", "f_zone"), _sig("000660", "gold_zone")]
    _, kept, _ = apply_market_context(2, sigs, cfg, _mc(gates={"f_zone": True, "gold_zone": False}), _NOW)
    assert [s[0].symbol for s in kept] == ["005930"]   # gold_zone 차단


def test_market_context_stale_noop():
    cfg = MarketContextConfig(enabled=True, ttl_sec=600)
    mb, _, _ = apply_market_context(2, [_sig("005930")], cfg, _mc(age=9999), _NOW)
    assert mb == 2


# ── apply_theme_guard ─────────────────────────────────────────────────────────

def _psig(exposure, age=10, conc=0.0, lev=False):
    return PortfolioSignals(theme_exposure=exposure, concentration_pct=conc,
                            leverage_warn=lev, ts=_NOW - timedelta(seconds=age))


def test_theme_guard_disabled_noop():
    cfg = PortfolioThemeConfig(enabled=False)
    sigs = [_sig("005930")]
    kept, sk, sf = apply_theme_guard(sigs, cfg, _psig({"반도체": 0.5}), _TM, _NOW)
    assert kept is sigs and sk == [] and sf == {}


def test_theme_guard_hard_blocks_over_theme():
    cfg = PortfolioThemeConfig(enabled=True, mode=HARD, max_theme_pct=0.30)
    sigs = [_sig("005930"), _sig("373220")]               # 반도체 과다(0.5), 2차전지 정상
    kept, sk, sf = apply_theme_guard(sigs, cfg, _psig({"반도체": 0.5, "2차전지": 0.1}), _TM, _NOW)
    assert [s[0].symbol for s in kept] == ["373220"]
    assert sk[0][0] == "005930" and "반도체" in sk[0][1]


def test_theme_guard_soft_reduces_sizing():
    cfg = PortfolioThemeConfig(enabled=True, mode="soft", max_theme_pct=0.30, soft_size_factor=0.5)
    kept, sk, sf = apply_theme_guard([_sig("005930")], cfg, _psig({"반도체": 0.5}), _TM, _NOW)
    assert len(kept) == 1 and sk == [] and sf == {"005930": 0.5}   # 차단 아님, 사이징만 축소


def test_theme_guard_no_over_or_unmapped_noop():
    cfg = PortfolioThemeConfig(enabled=True, mode=HARD, max_theme_pct=0.30)
    # 노출이 cap 미만 → 무변경
    kept, sk, _ = apply_theme_guard([_sig("005930")], cfg, _psig({"반도체": 0.2}), _TM, _NOW)
    assert len(kept) == 1 and sk == []
    # 미매핑 종목 → fail-open(통과)
    kept2, sk2, _ = apply_theme_guard([_sig("999999")], cfg, _psig({"반도체": 0.9}), _TM, _NOW)
    assert len(kept2) == 1 and sk2 == []


# ── apply_portfolio_risk ──────────────────────────────────────────────────────

def test_portfolio_risk_disabled_noop():
    f, note = apply_portfolio_risk(PortfolioRiskConfig(enabled=False), _psig({}, conc=0.9), _NOW)
    assert f == 1.0 and note is None


def test_portfolio_risk_throttle_on_concentration_or_leverage():
    cfg = PortfolioRiskConfig(enabled=True, max_concentration_pct=0.40, throttle_factor=0.5)
    f, note = apply_portfolio_risk(cfg, _psig({}, conc=0.5), _NOW)
    assert f == 0.5 and note
    f2, _ = apply_portfolio_risk(cfg, _psig({}, conc=0.1, lev=True), _NOW)
    assert f2 == 0.5
    f3, note3 = apply_portfolio_risk(cfg, _psig({}, conc=0.1), _NOW)
    assert f3 == 1.0 and note3 is None


def test_apply_market_context_shadow_logs_only():
    """[6/23] SHADOW 모드: bearish 라도 max_buy·signals 불변, 로그(notes)만. enforce 전 측정."""
    from datetime import datetime, timezone
    from backend.core.risk.market_context import (
        MarketContext, MarketContextConfig, apply_market_context, SHADOW,
    )
    now = datetime(2026, 6, 23, 5, 0, tzinfo=timezone.utc)
    ctx = MarketContext(regime="bearish", risk_on=False, ts=now,
                        strategy_gates={"swing_38": False})
    cfg = MarketContextConfig(enabled=True, mode=SHADOW, ttl_sec=3600)
    sigs = [("A", "005930", "swing_38"), ("B", "000660", "f_zone")]
    new_max, kept, notes = apply_market_context(5, sigs, cfg, ctx, now)
    assert new_max == 5            # max_buy 불변(shadow)
    assert kept == sigs            # signals 불변(shadow)
    assert any("SHADOW" in n for n in notes)   # would-block 측정 로그 존재
