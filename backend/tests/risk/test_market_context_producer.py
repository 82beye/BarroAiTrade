"""시장-맥락 생산자(writer) → 소비자(market_context) 라운드트립.

writer 가 market_snapshot.json + theme_map 으로 advisory.json 섹션을 생산하고,
데몬 소비자(load_market_advisory + apply_*)가 동일 계약으로 읽어 동작하는지 검증.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.core.risk.market_context import (
    PortfolioThemeConfig,
    apply_theme_guard,
    load_market_advisory,
)
from scripts.agent_advisory_writer import produce_market_sections, run_once

_NOW = datetime(2026, 6, 23, 5, 30, tzinfo=timezone.utc)
_TM = {"005930": ["반도체"], "000660": ["반도체"], "373220": ["2차전지"]}


def _snapshot():
    return {
        "ts": _NOW.isoformat(), "regime": "bearish",
        "leaders": [{"symbol": "005930", "trade_value": 6e11},
                    {"symbol": "000660", "trade_value": 4e11},
                    {"symbol": "373220", "trade_value": 1e11}],
        "positions": [{"symbol": "005930", "eval_value": 50},
                      {"symbol": "373220", "eval_value": 50}],
    }


def test_produce_market_sections_deterministic():
    s = produce_market_sections(_snapshot(), _TM, _NOW)
    # 시장국면
    assert s["market_context"]["regime"] == "bearish"
    assert s["market_context"]["risk_on"] is False
    # 거래대금 집중 — 반도체 1위
    assert s["sector_themes"]["hot"][0]["theme"] == "반도체"
    # 포트폴리오 테마 노출 — 반도체 0.5, 2차전지 0.5
    exp = s["portfolio_signals"]["theme_exposure"]
    assert abs(exp["반도체"] - 0.5) < 1e-6 and abs(exp["2차전지"] - 0.5) < 1e-6
    assert s["portfolio_signals"]["concentration_pct"] == 0.5


def test_produce_empty_snapshot():
    assert produce_market_sections({}, _TM, _NOW) != {}        # 빈 섹션이라도 구조 반환
    assert produce_market_sections({"regime": "bull", "leaders": [], "positions": []},
                                   _TM, _NOW)["sector_themes"]["hot"] == []


def test_writer_produces_sections_into_advisory(tmp_path):
    """run_once 가 market_snapshot 으로 advisory.json 섹션을 채운다."""
    data, logs = tmp_path / "data", tmp_path / "logs"
    data.mkdir(parents=True)
    (data / "refined_signals.json").write_text(json.dumps({"signals": []}), encoding="utf-8")
    (data / "market_snapshot.json").write_text(json.dumps(_snapshot()), encoding="utf-8")
    (data / "theme_map.json").write_text(json.dumps({"map": _TM}), encoding="utf-8")
    run_once(backend="mock", data_dir=data, logs_dir=logs, ttl_sec=180, keep_sec=900, top=0, now=_NOW)
    adv = json.loads((data / "advisory.json").read_text(encoding="utf-8"))
    assert adv["market_context"]["regime"] == "bearish"
    assert adv["portfolio_signals"]["theme_exposure"]["반도체"] == 0.5


def test_producer_to_consumer_roundtrip(tmp_path):
    """writer 생산 advisory.json → 소비자 apply_theme_guard 가 과다 테마 차단(hard)."""
    data, logs = tmp_path / "data", tmp_path / "logs"
    data.mkdir(parents=True)
    (data / "refined_signals.json").write_text(json.dumps({"signals": []}), encoding="utf-8")
    # 반도체 노출 100%(005930만 보유) → cap 30% 초과
    snap = {"ts": _NOW.isoformat(), "regime": "bull",
            "leaders": [{"symbol": "005930", "trade_value": 1e11}],
            "positions": [{"symbol": "005930", "eval_value": 100}]}
    (data / "market_snapshot.json").write_text(json.dumps(snap), encoding="utf-8")
    (data / "theme_map.json").write_text(json.dumps({"map": _TM}), encoding="utf-8")
    run_once(backend="mock", data_dir=data, logs_dir=logs, ttl_sec=180, keep_sec=900, top=0, now=_NOW)

    adv = load_market_advisory(data / "advisory.json")
    assert adv.portfolio_signals.theme_exposure["반도체"] == 1.0
    # 반도체 신규 매수 신호 → hard 차단
    cfg = PortfolioThemeConfig(enabled=True, mode="hard", max_theme_pct=0.30)
    sigs = [(SimpleNamespace(symbol="005930", name="삼성"), "f_zone", 1.0),
            (SimpleNamespace(symbol="373220", name="LG엔솔"), "f_zone", 1.0)]
    kept, skipped, _ = apply_theme_guard(sigs, cfg, adv.portfolio_signals, _TM, _NOW)
    assert [s[0].symbol for s in kept] == ["373220"]
    assert skipped[0][0] == "005930"
