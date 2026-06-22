"""에이전트 자문(advisory) 매수 게이트 테스트 — config-gated default-OFF.

거버넌스 핵심 검증:
  · enabled=False(default) → 신호 무변경(byte-identical). NO-GO verdict 가 있어도 통과.
  · verdict 없음 / TTL stale / 저신뢰 / 파싱 실패 → fail-open(매수 허용).
  · enabled + fresh NO-GO(또는 block_wait 시 WAIT) → 해당 신호만 차단.
설계: backend/core/risk/agent_advisory.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.core.journal.policy_config import PolicyConfig
from backend.core.risk.agent_advisory import (
    AdvisoryVerdict,
    AgentAdvisoryConfig,
    AgentAdvisoryStore,
    apply_buy_advisory,
    load_advisory,
)

_NOW = datetime(2026, 6, 22, 5, 30, tzinfo=timezone.utc)


def _leader(symbol, name="종목"):
    return SimpleNamespace(symbol=symbol, name=name)


def _signals(*symbols):
    """[(leader, strategy, pnl), ...] 형태 — 데몬 signals 구조 모사."""
    return [(_leader(s), "f_zone", 1000.0) for s in symbols]


def _verdict(symbol, action, *, conf=1.0, age_sec=10):
    return AdvisoryVerdict(
        symbol=symbol, action=action, confidence=conf, reason=f"{action} 사유",
        ts=_NOW - timedelta(seconds=age_sec), strategy="f_zone",
    )


def _store(*verdicts):
    return AgentAdvisoryStore({v.symbol: v for v in verdicts})


# ── default-OFF: byte-identical ───────────────────────────────────────────────

def test_default_config_disabled():
    assert AgentAdvisoryConfig.from_policy_config(PolicyConfig()).enabled is False


def test_disabled_returns_signals_unchanged_even_with_nogo():
    """enabled=False → NO-GO verdict 가 있어도 신호 그대로(byte-identical)."""
    cfg = AgentAdvisoryConfig(enabled=False)
    sigs = _signals("005930", "035720")
    store = _store(_verdict("005930", "NO-GO"), _verdict("035720", "NO-GO"))
    kept, skipped = apply_buy_advisory(sigs, cfg, store, _NOW)
    assert kept is sigs            # 입력 객체 그대로 반환
    assert skipped == []


# ── enabled: 차단/통과 ────────────────────────────────────────────────────────

def test_enabled_blocks_fresh_nogo():
    cfg = AgentAdvisoryConfig(enabled=True)
    sigs = _signals("005930", "035720")
    store = _store(_verdict("005930", "NO-GO"), _verdict("035720", "GO"))
    kept, skipped = apply_buy_advisory(sigs, cfg, store, _NOW)
    assert [s[0].symbol for s in kept] == ["035720"]
    assert skipped[0][0] == "005930" and skipped[0][2] == "NO-GO"


def test_enabled_go_passes():
    cfg = AgentAdvisoryConfig(enabled=True)
    sigs = _signals("005930")
    kept, skipped = apply_buy_advisory(sigs, cfg, _store(_verdict("005930", "GO")), _NOW)
    assert len(kept) == 1 and skipped == []


def test_wait_passes_by_default_blocks_when_configured():
    sigs = _signals("005930")
    store = _store(_verdict("005930", "WAIT"))
    # 기본: WAIT 통과
    kept, _ = apply_buy_advisory(sigs, AgentAdvisoryConfig(enabled=True), store, _NOW)
    assert len(kept) == 1
    # block_wait=True: WAIT 차단
    kept2, skipped2 = apply_buy_advisory(
        sigs, AgentAdvisoryConfig(enabled=True, block_wait=True), store, _NOW)
    assert kept2 == [] and skipped2[0][2] == "WAIT"


# ── fail-open ─────────────────────────────────────────────────────────────────

def test_failopen_when_no_verdict():
    cfg = AgentAdvisoryConfig(enabled=True)
    sigs = _signals("005930")
    kept, skipped = apply_buy_advisory(sigs, cfg, AgentAdvisoryStore(), _NOW)
    assert len(kept) == 1 and skipped == []


def test_failopen_when_verdict_stale():
    cfg = AgentAdvisoryConfig(enabled=True, ttl_sec=180)
    sigs = _signals("005930")
    store = _store(_verdict("005930", "NO-GO", age_sec=600))   # 10분 전 → TTL 초과
    kept, skipped = apply_buy_advisory(sigs, cfg, store, _NOW)
    assert len(kept) == 1 and skipped == []


def test_failopen_when_below_min_confidence():
    cfg = AgentAdvisoryConfig(enabled=True, min_confidence=0.7)
    sigs = _signals("005930")
    store = _store(_verdict("005930", "NO-GO", conf=0.3))
    kept, skipped = apply_buy_advisory(sigs, cfg, store, _NOW)
    assert len(kept) == 1 and skipped == []


def test_future_ts_not_fresh():
    """미래 ts(시계 오차) → 신선치 않음 → fail-open."""
    cfg = AgentAdvisoryConfig(enabled=True)
    sigs = _signals("005930")
    store = _store(_verdict("005930", "NO-GO", age_sec=-30))    # 30초 미래
    kept, _ = apply_buy_advisory(sigs, cfg, store, _NOW)
    assert len(kept) == 1


# ── load_advisory: fail-open 로딩 ─────────────────────────────────────────────

def test_load_missing_file(tmp_path):
    assert load_advisory(tmp_path / "nope.json").verdicts == {}


def test_load_bad_json(tmp_path):
    p = tmp_path / "advisory.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_advisory(p).verdicts == {}


def test_load_valid(tmp_path):
    p = tmp_path / "advisory.json"
    p.write_text(json.dumps({
        "updated_at": "2026-06-22T05:30:00Z",
        "verdicts": [
            {"symbol": "005930", "action": "NO-GO", "confidence": 0.8,
             "reason": "과열", "ts": "2026-06-22T05:29:55Z", "strategy": "f_zone"},
            {"symbol": "035720", "action": "go", "ts": "2026-06-22T05:29:50Z"},  # 소문자 정규화
            {"symbol": "BAD", "action": "MAYBE"},     # 잘못된 action → 무시
            {"action": "GO"},                          # symbol 없음 → 무시
        ],
    }), encoding="utf-8")
    store = load_advisory(p)
    assert set(store.verdicts) == {"005930", "035720"}
    assert store.verdicts["005930"].action == "NO-GO"
    assert store.verdicts["035720"].action == "GO"


def test_load_then_gate_end_to_end(tmp_path):
    """파일 로드 → 게이트 적용 end-to-end."""
    p = tmp_path / "advisory.json"
    p.write_text(json.dumps({
        "verdicts": [
            {"symbol": "005930", "action": "NO-GO", "confidence": 1.0,
             "ts": _NOW.isoformat().replace("+00:00", "Z")},
        ],
    }), encoding="utf-8")
    cfg = AgentAdvisoryConfig(enabled=True)
    kept, skipped = apply_buy_advisory(_signals("005930", "035720"), cfg, load_advisory(p), _NOW)
    assert [s[0].symbol for s in kept] == ["035720"]
    assert skipped[0][0] == "005930"
