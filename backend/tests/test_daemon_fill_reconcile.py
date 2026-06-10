"""BAR-OPS-38 P0#5 — 데몬 체결 대사(_reconcile_position_qty) + P0#3 가드 상수 테스트.

근거: reports/2026-06-10/2026-06-10_매매복기.md 인시던트 1·2.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.core.journal.active_positions import ActivePositionStore  # noqa: E402
from scripts.intraday_buy_daemon import (  # noqa: E402
    _GAP_GUARD_STRATEGIES,
    _NO_DCA_STRATEGIES,
    _reconcile_position_qty,
)


def _pos(tmp_path, qty=23, single=False):
    store = ActivePositionStore(str(tmp_path / "pos.json"))
    return store.create_from_order(
        symbol="319660", name="피에스케이", strategy="supertrend",
        entry_price=152000.0, total_recommended_qty=qty, order_no="0039988",
        single_tranche=single,
    )


def test_reconcile_increases_to_broker_qty(tmp_path):
    """6/10 319660 재현 — 장부 23(60/40: filled 14 + pending 9) vs 브로커 32."""
    pos = _pos(tmp_path, qty=23, single=False)
    filled_before = sum(t.qty for t in pos.tranches if t.status == "filled")
    assert filled_before == 14   # round(23*0.6)

    changed = _reconcile_position_qty(pos, 32)
    assert changed is True
    filled_after = sum(t.qty for t in pos.tranches if t.status == "filled")
    assert filled_after == 32
    # 청산 전량 수량 소스(total_recommended_qty)도 브로커 보유로 일치 — 9주 고아 방지
    assert pos.total_recommended_qty == 32
    # pending(미래 DCA 의도)은 유지
    assert any(t.status == "pending" for t in pos.tranches)


def test_reconcile_decreases_on_partial_fill(tmp_path):
    """부분체결 — 장부 filled 49 vs 브로커 10 (475150 식 상한가 부분체결 시나리오)."""
    pos = _pos(tmp_path, qty=49, single=True)   # 단일 filled 49
    changed = _reconcile_position_qty(pos, 10)
    assert changed is True
    assert sum(t.qty for t in pos.tranches if t.status == "filled") == 10
    assert pos.total_recommended_qty == 10


def test_reconcile_noop_when_matched(tmp_path):
    pos = _pos(tmp_path, qty=32, single=True)
    assert _reconcile_position_qty(pos, 32) is False


def test_reconcile_ignores_zero_broker_qty(tmp_path):
    """브로커 0 은 SYNC 퍼지 영역(미체결/매도완료) — 보정 대상 아님."""
    pos = _pos(tmp_path, qty=23, single=False)
    assert _reconcile_position_qty(pos, 0) is False


def test_supertrend_in_no_dca_strategies():
    """[P0#2 이중방어] 데몬 DCA 워처가 supertrend 가짜 tranche2 를 발사하지 못하게."""
    assert "supertrend" in _NO_DCA_STRATEGIES


def test_gap_guard_covers_meanrev_and_f():
    """[P0#3] 갭상승 추격 가드 대상 — gold(되돌림) + f_zone(눌림)."""
    assert "gold_zone" in _GAP_GUARD_STRATEGIES
    assert "f_zone" in _GAP_GUARD_STRATEGIES
