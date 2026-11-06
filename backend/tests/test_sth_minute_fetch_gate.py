"""단기고점 분봉 fetch 게이트가 evaluator 발동선과 일치하는지.

[2026-11-06] evaluator 의 short_term_high_min_pct 만 낮추고 데몬의 분봉 fetch
조건을 partial_tp_pct 로 두면 분봉이 없어 평가가 스킵된다 — 설정이 무효가 된다.
두 값이 같은 소스를 보는지 소스 수준에서 고정한다.
"""
import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "scripts" / "intraday_buy_daemon.py"


def _fetch_gate_block() -> str:
    s = _SRC.read_text(encoding="utf-8")
    i = s.find("SHORT_TERM_HIGH 평가용 1분봉")
    assert i > 0, "분봉 fetch 블록을 찾지 못했다"
    return s[i:i + 1200]


def test_fetch_gate_reads_short_term_high_min_pct():
    blk = _fetch_gate_block()
    assert "short_term_high_min_pct" in blk, (
        "분봉 fetch 조건이 short_term_high_min_pct 를 보지 않는다 — "
        "evaluator 발동선을 낮춰도 분봉이 없어 평가가 스킵된다"
    )


def test_fetch_gate_falls_back_to_partial_tp():
    """미설정(None)이면 종전 partial_tp_pct 로 폴백 — 거동 불변."""
    blk = _fetch_gate_block()
    assert "partial_tp_pct" in blk
    assert re.search(r"_sth_min\s+is\s+not\s+None", blk), "None 폴백 분기가 없다"


def test_fetch_still_gated_not_unconditional():
    """항상 fetch 하면 TR 호출이 폭증한다 — 조건부여야 한다."""
    blk = _fetch_gate_block()
    assert re.search(r"if\s+cur_rate\s*>=\s*_strat_partial_tp", blk)
