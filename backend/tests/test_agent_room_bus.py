"""에이전트 협업 방 버스 테스트 — default-OFF·fail-open·무주문 단언 (2026-06-23)."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def bus(tmp_path, monkeypatch):
    monkeypatch.setenv("BARRO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("BARRO_AGENTS_BOT_TOKEN", raising=False)
    monkeypatch.delenv("BARRO_AGENTS_CHAT_ID", raising=False)
    import backend.core.agents.room_bus as rb
    importlib.reload(rb)
    return rb


def test_post_disabled_is_noop(bus, monkeypatch):
    monkeypatch.setenv("BARRO_AGENT_ROOM_ENABLED", "0")
    assert bus.post("quant", "finding", "test", {"text": "hi"}) is None
    assert bus.read_today() == []


def test_post_enabled_appends_and_returns_id(bus, monkeypatch):
    monkeypatch.setenv("BARRO_AGENT_ROOM_ENABLED", "1")
    mid = bus.post("quant", "finding", "regime", {"text": "SIDEWAYS"}, priority="high", symbol="005930")
    assert mid and len(mid) == 12
    msgs = bus.read_today()
    assert len(msgs) == 1
    m = msgs[0]
    assert m.from_agent == "quant" and m.type == "finding" and m.symbol == "005930"
    assert m.priority == "high" and m.payload["text"] == "SIDEWAYS" and m.id == mid


def test_tail_filters_since_and_type(bus, monkeypatch):
    monkeypatch.setenv("BARRO_AGENT_ROOM_ENABLED", "1")
    bus.post("a", "finding", "t1", {"text": "1"})
    mid2 = bus.post("b", "proposal", "t2", {"text": "2"})
    after = bus.read_today()[0].ts
    later = [m for m in bus.tail(since_ts=after)]
    assert all(m.ts > after for m in later)
    props = bus.tail(types={"proposal"})
    assert [m.id for m in props] == [mid2]


def test_failopen_skips_corrupt_lines(bus, monkeypatch):
    monkeypatch.setenv("BARRO_AGENT_ROOM_ENABLED", "1")
    bus.post("a", "finding", "ok", {"text": "good"})
    f = bus._room_file()
    with f.open("a", encoding="utf-8") as fh:
        fh.write("{corrupt json\n")
    msgs = bus.read_today()  # 손상 라인 skip, 예외 없음
    assert len(msgs) == 1 and msgs[0].payload["text"] == "good"


def test_invalid_type_priority_coerced(bus, monkeypatch):
    monkeypatch.setenv("BARRO_AGENT_ROOM_ENABLED", "1")
    bus.post("a", "bogus_type", "t", {}, priority="bogus")
    m = bus.read_today()[0]
    assert m.type == "finding" and m.priority == "normal"


def test_cursor_roundtrip(bus, monkeypatch):
    monkeypatch.setenv("BARRO_AGENT_ROOM_ENABLED", "1")
    assert bus.load_cursor("coord") is None
    bus.save_cursor("coord", "2026-06-23T00:00:00.000Z")
    assert bus.load_cursor("coord") == "2026-06-23T00:00:00.000Z"


def test_no_order_execution_imports():
    """★안전: room_bus 는 주문 실행 경로를 일절 호출/임포트하지 않는다.★

    (docstring 의 설명용 'place_buy/place_sell' 언급은 오탐이므로 호출형(괄호)·
    클래스명·모듈 import 패턴으로 검사한다.)"""
    src = Path(__file__).resolve().parents[1] / "core" / "agents" / "room_bus.py"
    text = src.read_text(encoding="utf-8")
    for forbidden in ("place_buy(", "place_sell(", "LiveOrderGate",
                      "KiwoomNativeOrderExecutor", "live_order_gate", "kiwoom_native_orders"):
        assert forbidden not in text, f"room_bus 가 주문 실행({forbidden}) 참조 — 금지"


def test_coordinator_no_order_execution():
    """★안전: 코디네이터도 주문 실행 경로를 호출/임포트하지 않는다.★"""
    src = Path(__file__).resolve().parents[2] / "scripts" / "agent_room_coordinator.py"
    text = src.read_text(encoding="utf-8")
    for forbidden in ("place_buy(", "place_sell(", "LiveOrderGate",
                      "KiwoomNativeOrderExecutor", "live_order_gate", "kiwoom_native_orders"):
        assert forbidden not in text, f"coordinator 가 주문 실행({forbidden}) 참조 — 금지"


def test_discuss_no_order_execution():
    """★안전: 다자토론 엔진도 주문 실행 경로를 호출/임포트하지 않는다.★"""
    src = Path(__file__).resolve().parents[2] / "scripts" / "agent_room_discuss.py"
    text = src.read_text(encoding="utf-8")
    for forbidden in ("place_buy(", "place_sell(", "LiveOrderGate",
                      "KiwoomNativeOrderExecutor", "live_order_gate", "kiwoom_native_orders"):
        assert forbidden not in text, f"discuss 가 주문 실행({forbidden}) 참조 — 금지"
