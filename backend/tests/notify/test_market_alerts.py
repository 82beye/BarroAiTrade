"""시장-맥락 add-on 텔레그램 표시 함수 테스트 (순수 — 네트워크 없음).

format_macro/sector/portfolio_alert: 빈 섹션 → ''(표시 생략), 채워지면 핵심 정보 포함.
build_market_message: advisory.json 섹션 → 결합 메시지(빈 섹션 제외).
"""
from __future__ import annotations

import json

from backend.core.notify.telegram import (
    format_macro_alert,
    format_portfolio_alert,
    format_sector_alert,
)
from scripts.agent_advisory_writer import build_market_message


# ── format_macro_alert ────────────────────────────────────────────────────────

def test_macro_empty_or_unknown_is_blank():
    assert format_macro_alert(None) == ""
    assert format_macro_alert({}) == ""
    assert format_macro_alert({"regime": "unknown"}) == ""


def test_macro_riskoff_with_gate_and_llm():
    out = format_macro_alert({"regime": "bearish", "risk_on": False, "confidence": 0.8,
                              "strategy_gates": {"f_zone": True, "gold_zone": False},
                              "reason": "거래대금 쏠림 과열", "source": "snapshot+llm"})
    assert "BEARISH" in out and "risk-off" in out and "80%" in out
    # _escape_md 가 '_' 를 이스케이프(gold\_zone) — Telegram Markdown 안전
    assert "전략 차단" in out and "gold" in out and "🤖" in out


def test_macro_riskon():
    out = format_macro_alert({"regime": "bull", "risk_on": True, "confidence": 0.6})
    assert "BULL" in out and "risk-on" in out and "🤖" not in out


# ── format_sector_alert ───────────────────────────────────────────────────────

def test_sector_empty_is_blank():
    assert format_sector_alert(None) == ""
    assert format_sector_alert({"hot": []}) == ""


def test_sector_lists_hot_themes():
    out = format_sector_alert({"hot": [{"theme": "반도체", "turnover_pct": 0.45},
                                       {"theme": "2차전지", "turnover_pct": 0.30},
                                       {"theme": "바이오", "turnover_pct": 0.10},
                                       {"theme": "원전", "turnover_pct": 0.05}]}, top=3)
    assert "반도체 45%" in out and "2차전지 30%" in out
    assert "원전" not in out                            # top=3 truncation


# ── format_portfolio_alert ────────────────────────────────────────────────────

def test_portfolio_empty_is_blank():
    assert format_portfolio_alert(None) == ""
    assert format_portfolio_alert({"theme_exposure": {}, "leverage_warn": False}) == ""


def test_portfolio_exposure_over_and_leverage():
    out = format_portfolio_alert({"theme_exposure": {"반도체": 0.5, "2차전지": 0.1},
                                  "concentration_pct": 0.5, "leverage_warn": True},
                                 theme_cap=0.30)
    assert "집중도 50%" in out and "반도체 50%" in out
    assert "과다: 반도체" in out and "leverage" in out


def test_portfolio_leverage_only():
    out = format_portfolio_alert({"theme_exposure": {}, "leverage_warn": True})
    assert "leverage" in out


# ── build_market_message ──────────────────────────────────────────────────────

def test_build_message_missing_file(tmp_path):
    assert build_market_message(tmp_path) == ""


def test_build_message_combines_sections(tmp_path):
    (tmp_path / "advisory.json").write_text(json.dumps({
        "verdicts": [],
        "market_context": {"regime": "bearish", "risk_on": False, "confidence": 0.7},
        "sector_themes": {"hot": [{"theme": "반도체", "turnover_pct": 0.4}]},
        "portfolio_signals": {"theme_exposure": {"반도체": 0.5}, "concentration_pct": 0.5},
    }), encoding="utf-8")
    msg = build_market_message(tmp_path)
    assert "시장국면" in msg and "거래대금 집중 테마" in msg and "포트폴리오" in msg


def test_build_message_skips_empty_sections(tmp_path):
    (tmp_path / "advisory.json").write_text(json.dumps({
        "market_context": {"regime": "unknown"},        # 빈 → 생략
        "sector_themes": {"hot": [{"theme": "반도체", "turnover_pct": 0.4}]},
    }), encoding="utf-8")
    msg = build_market_message(tmp_path)
    assert "시장국면" not in msg and "거래대금 집중 테마" in msg
