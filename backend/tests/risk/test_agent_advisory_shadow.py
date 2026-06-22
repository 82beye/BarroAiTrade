"""advisory shadow 분석기 테스트 — 반사실(counterfactual) 계산 검증.

핵심: 진실원천 realized 로 "게이트했을 매수" 효과를 정확히 산출 — 손실회피(개선) vs
승자제거(악화), TTL 경계, block_wait, 종목단위 집계.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from scripts.agent_advisory_shadow import (
    analyze,
    gating_verdict,
    load_buys,
    load_decisions,
    load_realized,
    parse_iso,
    run,
)

_DATE = "2026-06-15"


def _ts(hh, mm, ss=0):
    return datetime(2026, 6, 15, hh, mm, ss, tzinfo=timezone.utc)


def _decisions(*recs):
    out = []
    for sym, action, t in recs:
        out.append({"symbol": sym, "action": action, "_ts": t, "reason": f"{action}-r"})
    return out


def _buys(*recs):
    return [{"symbol": s, "ts": t, "strategy": "f_zone", "action": "ORDERED"} for s, t in recs]


# ── gating_verdict: TTL / 시점 경계 ───────────────────────────────────────────

def test_gating_fresh_nogo_blocks():
    decs = _decisions(("A", "NO-GO", _ts(0, 29, 50)))
    v = gating_verdict("A", _ts(0, 30, 0), decs, ttl_sec=180, block_wait=False)
    assert v is not None and v["action"] == "NO-GO"


def test_gating_stale_verdict_does_not_block():
    decs = _decisions(("A", "NO-GO", _ts(0, 20, 0)))   # 10분 전 → TTL 180 초과
    assert gating_verdict("A", _ts(0, 30, 0), decs, 180, False) is None


def test_gating_verdict_after_buy_ignored():
    decs = _decisions(("A", "NO-GO", _ts(0, 30, 30)))   # 매수 이후
    assert gating_verdict("A", _ts(0, 30, 0), decs, 180, False) is None


def test_gating_wait_only_blocks_when_block_wait():
    decs = _decisions(("A", "WAIT", _ts(0, 29, 55)))
    assert gating_verdict("A", _ts(0, 30, 0), decs, 180, block_wait=False) is None
    assert gating_verdict("A", _ts(0, 30, 0), decs, 180, block_wait=True) is not None


# ── analyze: 반사실 손익 ──────────────────────────────────────────────────────

def test_analyze_avoided_loss_is_improvement():
    """손실종목 A 를 NO-GO 차단 → improvement 양수(손실 회피)."""
    buys = _buys(("A", _ts(0, 30)), ("B", _ts(0, 31)), ("C", _ts(0, 32)))
    decs = _decisions(("A", "NO-GO", _ts(0, 29, 50)), ("B", "GO", _ts(0, 30, 55)))
    realized = {"A": {"realized": -50000.0, "strategy": "f_zone"},
                "B": {"realized": 30000.0, "strategy": "f_zone"},
                "C": {"realized": 10000.0, "strategy": "gold_zone"}}
    s = analyze(buys, decs, realized, ttl_sec=180, block_wait=False)
    assert s["n_blocked_symbols"] == 1
    assert s["actual_total_realized"] == -10000
    assert s["blocked_realized"] == -50000
    assert s["counterfactual_total_realized"] == 40000   # -10000 - (-50000)
    assert s["improvement"] == 50000                     # 손실 회피
    assert s["avoided_loss"] == -50000 and s["killed_win"] == 0


def test_analyze_killed_winner_is_negative_improvement():
    """승자종목 B 를 NO-GO 차단 → improvement 음수(승자 제거)."""
    buys = _buys(("B", _ts(0, 31)))
    decs = _decisions(("B", "NO-GO", _ts(0, 30, 55)))
    realized = {"B": {"realized": 30000.0, "strategy": "f_zone"}}
    s = analyze(buys, decs, realized, 180, False)
    assert s["improvement"] == -30000 and s["killed_win"] == 30000


def test_analyze_no_verdict_no_block():
    buys = _buys(("C", _ts(0, 32)))
    s = analyze(buys, [], {"C": {"realized": 10000.0, "strategy": "f_zone"}}, 180, False)
    assert s["n_blocked_symbols"] == 0 and s["improvement"] == 0


# ── 로더 fail-safe + 필터 ─────────────────────────────────────────────────────

def test_load_decisions_skips_bad_lines(tmp_path):
    p = tmp_path / "2026-06-15.jsonl"
    p.write_text('{"symbol":"A","action":"NO-GO","ts":"2026-06-15T00:29:50Z"}\n'
                 'not json\n{"action":"GO"}\n', encoding="utf-8")
    decs = load_decisions(p)
    assert len(decs) == 1 and decs[0]["symbol"] == "A" and decs[0]["_ts"] is not None


def test_load_buys_filters_side_and_date(tmp_path):
    p = tmp_path / "order_audit.csv"
    p.write_text(
        "ts,action,side,symbol,qty,price,order_no,return_code,blocked,reason,strategy_id,filled_qty,avg_fill_price\n"
        "2026-06-15T00:30:00+00:00,ORDERED,buy,000111,10,MKT,1,0,0,,f_zone,,\n"
        "2026-06-15T00:31:00+00:00,ORDERED,sell,000222,10,MKT,2,0,0,,f_zone,,\n"   # sell 제외
        "2026-06-14T00:30:00+00:00,ORDERED,buy,000333,10,MKT,3,0,0,,f_zone,,\n",   # 다른 날 제외
        encoding="utf-8")
    buys = load_buys(p, "2026-06-15")
    assert [b["symbol"] for b in buys] == ["000111"]


def test_load_realized(tmp_path):
    p = tmp_path / "strategy_audit_2026-06-15.json"
    p.write_text(json.dumps({"per_symbol": {"A": {"realized": -50000, "strategy": "f_zone"}},
                             "total_realized": -50000}), encoding="utf-8")
    r = load_realized(p)
    assert r["A"]["realized"] == -50000.0


def test_load_missing_files_failsafe(tmp_path):
    assert load_decisions(tmp_path / "x.jsonl") == []
    assert load_buys(tmp_path / "x.csv", _DATE) == []
    assert load_realized(tmp_path / "x.json") == {}


def test_parse_iso_variants():
    assert parse_iso("2026-06-15T00:30:00Z") == _ts(0, 30)
    assert parse_iso("2026-06-15T00:30:00+00:00") == _ts(0, 30)
    assert parse_iso(None) is None and parse_iso("bad") is None


# ── run() end-to-end ──────────────────────────────────────────────────────────

def test_run_end_to_end(tmp_path):
    data, logs, reports = tmp_path / "data", tmp_path / "logs", tmp_path / "reports"
    (logs / "decisions").mkdir(parents=True)
    (logs / "decisions" / f"{_DATE}.jsonl").write_text(
        '{"symbol":"000111","action":"NO-GO","ts":"2026-06-15T00:29:50Z","reason":"과열"}\n',
        encoding="utf-8")
    data.mkdir(parents=True)
    (data / "order_audit.csv").write_text(
        "ts,action,side,symbol,qty,price,order_no,return_code,blocked,reason,strategy_id,filled_qty,avg_fill_price\n"
        "2026-06-15T00:30:00+00:00,ORDERED,buy,000111,10,MKT,1,0,0,,f_zone,,\n",
        encoding="utf-8")
    reports.mkdir(parents=True)
    (reports / f"strategy_audit_{_DATE}.json").write_text(
        json.dumps({"per_symbol": {"000111": {"realized": -40000, "strategy": "f_zone"}}}),
        encoding="utf-8")
    s = run(_DATE, ttl_sec=180, block_wait=False, data_dir=data, logs_dir=logs, reports_dir=reports)
    assert s["n_blocked_symbols"] == 1 and s["improvement"] == 40000
