"""scripts/ai_swing_recover.py — 고아 진단·복구 로직 테스트 (2026-07-30 신규).

주문 없음 · 장부(JSON)만 다루는 도구다. 브로커 조회는 --balance-file 로 대체해
API 키 없이 전 경로를 검증한다.

핵심 불변식 (깨지면 2026-05-29 사고가 재발한다):
  1. 원천(order_audit 의 ts/price)이 없으면 **복원하지 않는다** — entry_time 을 "지금"으로
     채우면 min_hold_days=3 이 리셋돼 3일 더 묶인다.
  2. 장부에 이미 있는 심볼은 **절대 덮어쓰지 않는다** — 심볼 단일 키 구조라 덮어쓰면
     다른 전략 포지션이 소실된다.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
rec = importlib.import_module("ai_swing_recover")


def _audit(ts, symbol, *, action="ORDERED", side="buy", sid="ai_swing",
           price="10000", avg_fill="", order_no="A1") -> dict:
    return {
        "ts": ts, "action": action, "side": side, "symbol": symbol,
        "qty": "10", "price": price, "order_no": order_no, "return_code": "0",
        "blocked": "0", "reason": "", "strategy_id": sid,
        "filled_qty": "10", "avg_fill_price": avg_fill,
    }


# ─── parse_audit_buys ─────────────────────────────────────────────────────
def test_parse_audit_picks_ai_swing_buys_only():
    rows = [
        _audit("2026-07-30T01:00:00+00:00", "005930"),
        _audit("2026-07-30T02:00:00+00:00", "035720", sid="f_zone"),
        _audit("2026-07-30T03:00:00+00:00", "000660", side="sell"),
    ]
    assert set(rec.parse_audit_buys(rows)) == {"005930"}


def test_parse_audit_accepts_versioned_strategy_id():
    """audit 의 strategy_id 는 ai_swing / ai_swing_v1 둘 다 올 수 있다."""
    rows = [_audit("2026-07-30T01:00:00+00:00", "005930", sid="ai_swing_v1")]
    assert "005930" in rec.parse_audit_buys(rows)


def test_parse_audit_excludes_blocked_and_failed():
    """BLOCKED/FAILED 는 체결이 아니다 — 복원 근거로 쓰지 않는다."""
    rows = [
        _audit("2026-07-30T01:00:00+00:00", "005930", action="BLOCKED"),
        _audit("2026-07-30T02:00:00+00:00", "000660", action="FAILED"),
    ]
    assert rec.parse_audit_buys(rows) == {}


def test_parse_audit_accepts_dry_run():
    """DRY_RUN 은 모의 체결이라 장부 정합 확인 목적으로 인정한다."""
    rows = [_audit("2026-07-30T01:00:00+00:00", "005930", action="DRY_RUN")]
    assert "005930" in rec.parse_audit_buys(rows)


def test_parse_audit_keeps_latest_ts():
    """같은 종목 여러 건이면 가장 최근 ts (재진입 후 장부 유실 케이스)."""
    rows = [
        _audit("2026-07-28T01:00:00+00:00", "005930", price="9000", order_no="OLD"),
        _audit("2026-07-30T01:00:00+00:00", "005930", price="11000", order_no="NEW"),
    ]
    assert rec.parse_audit_buys(rows)["005930"]["order_no"] == "NEW"


# ─── find_orphans ─────────────────────────────────────────────────────────
def test_find_orphans_skips_symbols_in_ledger():
    holdings = {"005930": {"name": "삼성전자", "qty": 10}}
    buys = rec.parse_audit_buys([_audit("2026-07-30T01:00:00+00:00", "005930")])
    recoverable, unknown = rec.find_orphans(holdings, {"005930"}, buys)
    assert recoverable == [] and unknown == []


def test_find_orphans_prefers_avg_fill_price():
    holdings = {"005930": {"name": "삼성전자", "qty": 10}}
    buys = rec.parse_audit_buys(
        [_audit("2026-07-30T01:00:00+00:00", "005930", price="10000", avg_fill="10250")])
    recoverable, _ = rec.find_orphans(holdings, set(), buys)
    assert recoverable[0].entry_price == 10250.0
    assert recoverable[0].entry_time == "2026-07-30T01:00:00+00:00"


def test_find_orphans_falls_back_to_price_when_no_fill():
    holdings = {"005930": {"name": "삼성전자", "qty": 10}}
    buys = rec.parse_audit_buys([_audit("2026-07-30T01:00:00+00:00", "005930", avg_fill="")])
    recoverable, _ = rec.find_orphans(holdings, set(), buys)
    assert recoverable[0].entry_price == 10000.0


def test_find_orphans_reports_unknown_without_audit_trace():
    """★불변식 1★ ai_swing 흔적 없는 보유는 복원 대상이 아니다."""
    holdings = {"999999": {"name": "미지", "qty": 3}}
    recoverable, unknown = rec.find_orphans(holdings, set(), {})
    assert recoverable == []
    assert unknown == ["999999"]


def test_find_orphans_rejects_market_price_only_row():
    """price 가 'MKT' 뿐이면 진입가 원천이 없다 → 복원 금지."""
    holdings = {"005930": {"name": "삼성전자", "qty": 10}}
    buys = rec.parse_audit_buys(
        [_audit("2026-07-30T01:00:00+00:00", "005930", price="MKT", avg_fill="")])
    recoverable, unknown = rec.find_orphans(holdings, set(), buys)
    assert recoverable == []
    assert unknown == ["005930"]


def test_find_orphans_rejects_missing_ts():
    """entry_time 원천이 없으면 복원 금지 (min_hold 리셋 방지)."""
    holdings = {"005930": {"name": "삼성전자", "qty": 10}}
    buys = {"005930": _audit("", "005930", avg_fill="10100")}
    recoverable, unknown = rec.find_orphans(holdings, set(), buys)
    assert recoverable == []
    assert unknown == ["005930"]


# ─── apply_recovery (실제 장부 쓰기) ──────────────────────────────────────
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """임시 장부 경로로 격리 — 실제 data/active_positions.json 을 건드리지 않는다."""
    path = tmp_path / "active_positions.json"
    monkeypatch.setattr(rec, "POSITIONS_PATH", path)
    return path


def test_apply_recovery_restores_with_source_entry_time(ledger):
    """복원된 장부가 원천 entry_time·진입가를 그대로 보존한다."""
    from backend.core.journal.active_positions import ActivePositionStore

    cand = rec.OrphanCandidate(
        symbol="005930", name="삼성전자", broker_qty=7,
        entry_time="2026-07-25T01:00:00+00:00", entry_price=10250.0,
        order_no="A1", source="test",
    )
    assert rec.apply_recovery([cand]) == 1

    pos = ActivePositionStore(ledger).get("005930")
    assert pos is not None
    assert pos.strategy == "ai_swing"
    assert pos.entry_time == "2026-07-25T01:00:00+00:00"   # ★"지금"으로 덮지 않는다
    assert pos.entry_price == 10250.0
    assert pos.total_recommended_qty == 7
    # 전량 단일 filled 트랜치 — pending 이 남으면 데몬 DCA 가 가짜 추가매수를 낼 수 있다
    assert len(pos.tranches) == 1
    assert pos.tranches[0].status == "filled"
    assert pos.tranches[0].qty == 7
    assert pos.pending_tranches() == []


def test_apply_recovery_never_overwrites_existing(ledger):
    """★불변식 2★ 이미 있는 심볼은 건드리지 않는다 (다른 전략 포지션 소실 방지)."""
    from backend.core.journal.active_positions import ActivePositionStore

    store = ActivePositionStore(ledger)
    store.create_from_order(
        symbol="005930", name="삼성전자", strategy="supertrend",
        entry_price=9000.0, total_recommended_qty=5, order_no="ST1",
        single_tranche=True,
    )
    cand = rec.OrphanCandidate(
        symbol="005930", name="삼성전자", broker_qty=7,
        entry_time="2026-07-25T01:00:00+00:00", entry_price=10250.0,
        order_no="A1", source="test",
    )
    assert rec.apply_recovery([cand]) == 0          # 추가 0건

    pos = ActivePositionStore(ledger).get("005930")
    assert pos.strategy == "supertrend"             # 기존 전략 보존
    assert pos.entry_price == 9000.0


def test_apply_recovery_uses_ai_swing_sl_env(ledger, monkeypatch):
    """복원 장부의 sl_pct 가 ai_swing 전용 env 를 따른다."""
    from backend.core.journal.active_positions import ActivePositionStore

    monkeypatch.setenv("BARRO_AI_SWING_SL_PCT", "-5.0")
    cand = rec.OrphanCandidate(
        symbol="000660", name="하이닉스", broker_qty=3,
        entry_time="2026-07-25T01:00:00+00:00", entry_price=200000.0,
        order_no="A2", source="test",
    )
    rec.apply_recovery([cand])
    assert ActivePositionStore(ledger).get("000660").sl_pct == -5.0


# ─── read helpers ─────────────────────────────────────────────────────────
def test_read_balance_file_accepts_list_and_dict(tmp_path):
    p1 = tmp_path / "list.json"
    p1.write_text(json.dumps([{"symbol": "005930", "name": "삼성", "qty": 10}]), encoding="utf-8")
    assert rec.read_balance_file(str(p1))["005930"]["qty"] == 10

    p2 = tmp_path / "dict.json"
    p2.write_text(json.dumps({"000660": {"name": "하이닉스", "qty": 5}}), encoding="utf-8")
    assert rec.read_balance_file(str(p2))["000660"]["qty"] == 5


def test_read_audit_rows_absent_file_returns_empty(tmp_path):
    """audit 부재 시 예외 대신 빈 목록 — 도구가 죽지 않는다."""
    assert rec.read_audit_rows(tmp_path / "nope.csv") == []
