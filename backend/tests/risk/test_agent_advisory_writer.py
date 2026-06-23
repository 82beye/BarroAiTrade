"""advisory writer(생산자) 테스트 + 생산자→소비자 라운드트립.

핵심: writer 가 쓴 advisory.json 을 소비자(load_advisory + apply_buy_advisory)가 읽어
동일 계약으로 동작하는지(round-trip), mock 결정성, merge TTL/dedup, claude 출력 파싱.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.core.risk.agent_advisory import (
    AgentAdvisoryConfig,
    apply_buy_advisory,
    load_advisory,
)
from scripts.agent_advisory_writer import (
    merge_advisory,
    mock_verdict,
    read_refined_signals,
    run_once,
    _parse_verdict_text,
    _claude_bin,
)

_NOW = datetime(2026, 6, 22, 5, 30, tzinfo=timezone.utc)


def _write_refined(data_dir, signals):
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "refined_signals.json").write_text(
        json.dumps({"regime": "BULL", "timestamp": _NOW.isoformat(), "signals": signals},
                   ensure_ascii=False), encoding="utf-8")


# ── mock 결정성 ───────────────────────────────────────────────────────────────

def test_mock_verdict_rules():
    assert mock_verdict({"flu_rate": 27, "score": 9})["action"] == "NO-GO"   # 과열
    assert mock_verdict({"flu_rate": 5, "score": 2})["action"] == "WAIT"     # 저점수
    assert mock_verdict({"flu_rate": 5, "score": 8})["action"] == "GO"


# ── read_refined_signals fail-safe ────────────────────────────────────────────

def test_read_refined_missing(tmp_path):
    assert read_refined_signals(tmp_path / "nope.json") == []


def test_read_refined_bad_json(tmp_path):
    p = tmp_path / "refined_signals.json"
    p.write_text("{bad", encoding="utf-8")
    assert read_refined_signals(p) == []


# ── run_once: 생산 + 기록 ─────────────────────────────────────────────────────

def test_run_once_writes_advisory_and_decisions(tmp_path):
    data_dir, logs_dir = tmp_path / "data", tmp_path / "logs"
    _write_refined(data_dir, [
        {"symbol": "005930", "name": "삼성", "strategy": "f_zone", "score": 9, "flu_rate": 27},   # NO-GO
        {"symbol": "035720", "name": "카카오", "strategy": "f_zone", "score": 8, "flu_rate": 5},   # GO
    ])
    verdicts = run_once(backend="mock", data_dir=data_dir, logs_dir=logs_dir,
                        ttl_sec=180, keep_sec=900, top=0, now=_NOW)
    assert {v["symbol"]: v["action"] for v in verdicts} == {"005930": "NO-GO", "035720": "GO"}
    # advisory.json 생성됨
    adv = json.loads((data_dir / "advisory.json").read_text(encoding="utf-8"))
    assert {v["symbol"] for v in adv["verdicts"]} == {"005930", "035720"}
    # decisions jsonl append 됨
    dec_files = list((logs_dir / "decisions").glob("*.jsonl"))
    assert len(dec_files) == 1 and dec_files[0].read_text().count("\n") == 2


# ── 생산자 → 소비자 라운드트립 (핵심) ─────────────────────────────────────────

def test_producer_consumer_roundtrip(tmp_path):
    """writer 가 쓴 advisory.json → 데몬 소비자가 읽어 NO-GO 종목 차단."""
    data_dir, logs_dir = tmp_path / "data", tmp_path / "logs"
    _write_refined(data_dir, [
        {"symbol": "005930", "name": "삼성", "strategy": "f_zone", "score": 9, "flu_rate": 27},   # NO-GO
        {"symbol": "035720", "name": "카카오", "strategy": "f_zone", "score": 8, "flu_rate": 5},   # GO
    ])
    run_once(backend="mock", data_dir=data_dir, logs_dir=logs_dir,
             ttl_sec=180, keep_sec=900, top=0, now=_NOW)
    # 소비자 측 — 데몬과 동일 경로
    store = load_advisory(data_dir / "advisory.json")
    cfg = AgentAdvisoryConfig(enabled=True, ttl_sec=180)
    sigs = [(SimpleNamespace(symbol="005930", name="삼성"), "f_zone", 1.0),
            (SimpleNamespace(symbol="035720", name="카카오"), "f_zone", 1.0)]
    kept, skipped = apply_buy_advisory(sigs, cfg, store, _NOW + timedelta(seconds=20))
    assert [s[0].symbol for s in kept] == ["035720"]
    assert skipped[0][0] == "005930" and skipped[0][2] == "NO-GO"


def test_roundtrip_disabled_is_noop(tmp_path):
    """enabled=False → writer 가 NO-GO 를 써도 소비자는 무변경(byte-identical)."""
    data_dir, logs_dir = tmp_path / "data", tmp_path / "logs"
    _write_refined(data_dir, [{"symbol": "005930", "name": "삼성", "strategy": "f_zone",
                               "score": 9, "flu_rate": 27}])
    run_once(backend="mock", data_dir=data_dir, logs_dir=logs_dir,
             ttl_sec=180, keep_sec=900, top=0, now=_NOW)
    store = load_advisory(data_dir / "advisory.json")
    sigs = [(SimpleNamespace(symbol="005930", name="삼성"), "f_zone", 1.0)]
    kept, skipped = apply_buy_advisory(sigs, AgentAdvisoryConfig(enabled=False), store, _NOW)
    assert kept is sigs and skipped == []


# ── merge_advisory: TTL prune + dedup ─────────────────────────────────────────

def test_merge_dedup_latest_and_prune_stale():
    old = {"verdicts": [
        {"symbol": "005930", "action": "GO", "ts": (_NOW - timedelta(seconds=30)).isoformat()},
        {"symbol": "000660", "action": "GO", "ts": (_NOW - timedelta(seconds=2000)).isoformat()},  # stale
    ]}
    new = [{"symbol": "005930", "action": "NO-GO", "ts": _NOW.isoformat()}]  # 최신으로 갱신
    merged = merge_advisory(old, new, _NOW, keep_sec=900)
    by = {v["symbol"]: v["action"] for v in merged["verdicts"]}
    assert by == {"005930": "NO-GO"}          # 000660 stale 제거, 005930 최신 갱신


# ── claude 출력 파싱 ──────────────────────────────────────────────────────────

def test_parse_claude_json_wrapper():
    out = json.dumps({"result": '여기 결정: {"action":"NO-GO","confidence":0.9,"reason":"과열"}'})
    v = _parse_verdict_text(out)
    assert v["action"] == "NO-GO" and v["confidence"] == 0.9


def test_parse_bare_json_and_normalize():
    v = _parse_verdict_text('{"action":"nogo","confidence":0.5,"reason":"x"}')
    assert v["action"] == "NO-GO"


def test_parse_invalid_action_returns_none():
    assert _parse_verdict_text('{"action":"MAYBE"}') is None
    assert _parse_verdict_text("그냥 텍스트") is None


def test_claude_bin_prefers_env_absolute_path(tmp_path, monkeypatch):
    """headless(launchd/cron) 회귀 가드: CLAUDE_CLI_BIN(절대경로·실행가능)을
    PATH 상 cmux 래퍼보다 우선 해석한다. (래퍼는 nvm 미포함 PATH 에서 실 바이너리를
    못 찾아 exit 127 → 합성/verdict 실패하던 버그의 회귀 방지.)"""
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 0\n")
    fake.chmod(0o755)
    monkeypatch.setenv("CLAUDE_CLI_BIN", str(fake))
    assert _claude_bin() == str(fake)
    # 비실행/부재 경로 → env 무시하고 PATH 폴백(예외 없음·truthy)
    bad = tmp_path / "nope"
    bad.write_text("x")
    monkeypatch.setenv("CLAUDE_CLI_BIN", str(bad))
    assert _claude_bin() != str(bad) and _claude_bin()
    # env 미설정 → 'claude' 또는 PATH 해석(truthy)
    monkeypatch.delenv("CLAUDE_CLI_BIN", raising=False)
    assert _claude_bin()
