"""market_context LLM 오버레이 테스트 — 결정적 base 위 LLM 판단(opt-in, fail-open).

실 claude 호출은 llm_fn 주입으로 회피. 핵심: 오버레이 성공 시 risk_on/confidence/
strategy_gates/reason 덮어쓰기(결정적 regime·ts 보존), 실패/None → base 그대로(fail-open).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.agent_advisory_writer import (
    _extract_json,
    market_context_llm_overlay,
    produce_market_sections,
    run_once,
)

_NOW = datetime(2026, 6, 23, 5, 30, tzinfo=timezone.utc)
_TM = {"005930": ["반도체"], "373220": ["2차전지"]}


def _snapshot(regime="bearish"):
    return {"ts": _NOW.isoformat(), "regime": regime,
            "leaders": [{"symbol": "005930", "trade_value": 9e11},
                        {"symbol": "373220", "trade_value": 1e11}],
            "positions": [{"symbol": "005930", "eval_value": 80},
                          {"symbol": "373220", "eval_value": 20}]}


def _base():
    return {"regime": "bearish", "risk_on": False, "confidence": 0.5,
            "strategy_gates": {}, "reason": "regime=bearish(결정적)", "ts": "T", "source": "snapshot"}


# ── _extract_json (공통 추출부 회귀) ──────────────────────────────────────────

def test_extract_json_wrapper_and_bare():
    assert _extract_json(json.dumps({"result": '결정: {"risk_on": true}'}))["risk_on"] is True
    assert _extract_json('{"a": 1}')["a"] == 1
    assert _extract_json("그냥 텍스트") is None
    assert _extract_json("") is None


# ── overlay 성공/실패 ─────────────────────────────────────────────────────────

def test_overlay_merges_llm_fields():
    llm = lambda p: {"risk_on": True, "confidence": 0.9,
                     "strategy_gates": {"f_zone": True, "gold_zone": False}, "reason": "회복 신호"}
    out = market_context_llm_overlay(_base(), hot=[{"theme": "반도체", "turnover_pct": 0.9}],
                                     exposure={"반도체": 0.8}, regime="bearish", llm_fn=llm)
    assert out["risk_on"] is True and out["confidence"] == 0.9
    assert out["strategy_gates"]["gold_zone"] is False
    assert out["reason"] == "회복 신호" and out["source"] == "snapshot+llm"
    assert out["regime"] == "bearish"            # 결정적 regime 보존


def test_overlay_failopen_on_none():
    out = market_context_llm_overlay(_base(), hot=[], exposure={}, regime="bearish",
                                     llm_fn=lambda p: None)
    assert out == _base()                         # 변경 없음(fail-open)


def test_overlay_failopen_on_nondict():
    out = market_context_llm_overlay(_base(), hot=[], exposure={}, regime="bearish",
                                     llm_fn=lambda p: "not a dict")
    assert out == _base()


def test_overlay_partial_fields_kept_base():
    """LLM 이 일부 필드만 주면 나머지는 base 유지."""
    out = market_context_llm_overlay(_base(), hot=[], exposure={}, regime="bearish",
                                     llm_fn=lambda p: {"risk_on": True})
    assert out["risk_on"] is True and out["strategy_gates"] == {}   # gates 미제공 → base


# ── produce_market_sections llm on/off ────────────────────────────────────────

def test_produce_llm_off_is_deterministic():
    s = produce_market_sections(_snapshot(), _TM, _NOW, llm=False)
    assert s["market_context"]["source"] == "snapshot"
    assert s["market_context"]["confidence"] == 0.5


def test_produce_llm_on_applies_overlay():
    llm = lambda p: {"risk_on": True, "confidence": 0.85,
                     "strategy_gates": {"gold_zone": False}, "reason": "핫테마 과열 경계"}
    s = produce_market_sections(_snapshot(), _TM, _NOW, llm=True, llm_fn=llm)
    assert s["market_context"]["source"] == "snapshot+llm"
    assert s["market_context"]["risk_on"] is True
    assert s["market_context"]["strategy_gates"]["gold_zone"] is False
    # 결정적 섹션(테마)은 그대로
    assert s["sector_themes"]["hot"][0]["theme"] == "반도체"


# ── run_once market_llm 통합 ──────────────────────────────────────────────────

def test_run_once_market_llm_injected(tmp_path):
    data, logs = tmp_path / "data", tmp_path / "logs"
    data.mkdir(parents=True)
    (data / "refined_signals.json").write_text(json.dumps({"signals": []}), encoding="utf-8")
    (data / "market_snapshot.json").write_text(json.dumps(_snapshot()), encoding="utf-8")
    (data / "theme_map.json").write_text(json.dumps({"map": _TM}), encoding="utf-8")
    llm = lambda p: {"risk_on": True, "confidence": 0.8, "strategy_gates": {}, "reason": "LLM 판단"}
    run_once(backend="mock", data_dir=data, logs_dir=logs, ttl_sec=180, keep_sec=900,
             top=0, now=_NOW, market_llm=True, market_llm_fn=llm)
    adv = json.loads((data / "advisory.json").read_text(encoding="utf-8"))
    assert adv["market_context"]["source"] == "snapshot+llm"
    assert adv["market_context"]["risk_on"] is True
