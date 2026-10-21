"""포지션 목표가 — 산출·영속·API 노출. 청산 판정 무영향이 핵심 계약."""
from decimal import Decimal

import pytest

from backend.core.strategy.position_targets import (
    DEFAULT_DCA_DROPS,
    PositionTargets,
    build_targets,
    progress,
)

PROF = {
    "ai_swing": {
        "stop_loss_pct": Decimal("-8.0"),
        "take_profit_pct": Decimal("50.0"),
        "partial_tp_pct": Decimal("20.0"),
        "partial_tp_ratio": Decimal("0.5"),
        "trailing_start_pct": Decimal("10.0"),
        "trailing_offset_pct": Decimal("3.0"),
        "breakeven_trigger_pct": Decimal("10.0"),
        "min_hold_days": 3,
        "max_hold_days": 20,
    },
    "gold_zone": {
        "stop_loss_pct": Decimal("-4.0"),
        "take_profit_pct": Decimal("4.0"),
        "partial_tp_pct": Decimal("3.0"),
        "partial_tp_ratio": Decimal("0.7"),
        "trailing_start_pct": Decimal("3.0"),
        "trailing_offset_pct": Decimal("1.0"),
        "breakeven_trigger_pct": Decimal("2.5"),
    },
}


# ── 산출 ──────────────────────────────────────────────────────────────
def test_builds_all_targets():
    t = build_targets("009470", "ai_swing_v1", 10_000, profiles=PROF,
                      entry_time="2026-10-15T00:00:00+00:00")
    assert t.strategy == "ai_swing"          # _v1 접미사 제거
    assert t.entry == 10_000
    assert t.stop_loss == 9_200              # −8%
    assert t.partial_tp == 12_000            # +20%
    assert t.partial_tp_ratio == 0.5
    assert t.take_profit == 15_000           # +50%
    assert t.trailing_start == 11_000        # +10%
    assert t.trailing_offset_pct == 3.0
    assert t.breakeven_trigger == 11_000
    assert t.min_hold_days == 3 and t.max_hold_days == 20


def test_dca_prices():
    t = build_targets("A", "ai_swing", 10_000, profiles=PROF)
    assert [d["tranche"] for d in t.dca_prices] == [2, 3]
    assert [d["drop_pct"] for d in t.dca_prices] == list(DEFAULT_DCA_DROPS)
    assert [d["price"] for d in t.dca_prices] == [9_800, 9_600]   # −2% · −4%


def test_hold_window_dates():
    t = build_targets("A", "ai_swing", 10_000, profiles=PROF,
                      entry_time="2026-10-15T05:00:00+00:00")   # KST 10-15 14:00
    assert t.min_hold_until == "2026-10-18"
    assert t.max_hold_until == "2026-11-04"


def test_zone_profile_differs():
    t = build_targets("A", "gold_zone", 10_000, profiles=PROF)
    assert t.stop_loss == 9_600              # −4% (ai_swing 과 다름)
    assert t.partial_tp == 10_300            # +3%
    assert t.partial_tp_ratio == 0.7
    assert t.min_hold_until == ""            # min_hold 없음


def test_trailing_disabled_when_nonpositive():
    prof = {"x": {"stop_loss_pct": Decimal("-5"), "trailing_start_pct": Decimal("0")}}
    t = build_targets("A", "x", 10_000, profiles=prof)
    assert t.trailing_start == 0.0


# ── 실패 처리 — 진입을 막아선 안 된다 ──────────────────────────────────
@pytest.mark.parametrize("px", [0, -1, None, "abc"])
def test_invalid_entry_returns_none(px):
    assert build_targets("A", "ai_swing", px, profiles=PROF) is None


def test_unknown_strategy_returns_none():
    assert build_targets("A", "없는전략", 10_000, profiles=PROF) is None


def test_missing_fields_do_not_raise():
    t = build_targets("A", "x", 10_000, profiles={"x": {}})
    assert t is not None
    assert t.stop_loss == 0.0 and t.take_profit == 0.0


# ── 진행도 (표시 전용) ────────────────────────────────────────────────
def test_progress():
    t = build_targets("A", "ai_swing", 10_000, profiles=PROF)
    p = progress(t, 11_000)
    assert p["pnl_pct"] == pytest.approx(10.0)
    assert p["to_partial_tp_pct"] == pytest.approx(9.09, abs=0.01)   # 12000/11000-1
    assert p["to_stop_loss_pct"] < 0


def test_progress_invalid():
    t = build_targets("A", "ai_swing", 10_000, profiles=PROF)
    assert progress(t, 0) == {}
    assert progress(PositionTargets(), 100) == {}


# ── 영속 — load 후에도 살아남아야 한다 (2026-05-19 필드 유실 버그 유형) ──
def test_targets_persist_across_load(tmp_path, monkeypatch):
    from backend.core.journal.active_positions import ActivePositionStore
    path = tmp_path / "ap.json"
    store = ActivePositionStore(path)
    pos = store.create_from_order(
        symbol="009470", name="삼화전기", strategy="ai_swing",
        entry_price=10_000.0, total_recommended_qty=100, order_no="X1",
        sl_pct=-8.0, single_tranche=True)
    assert pos.targets, "진입 시 목표가가 산출돼야 한다"
    entry_sl = pos.targets.get("stop_loss")

    reloaded = ActivePositionStore(path).load_all()
    assert "009470" in reloaded
    assert reloaded["009470"].targets.get("stop_loss") == entry_sl, \
        "load 후 targets 가 사라지면 화면에서 목표가를 볼 수 없다"


def test_targets_failure_does_not_block_entry(tmp_path, monkeypatch):
    """목표가 산출이 터져도 포지션 생성은 성공해야 한다."""
    import backend.core.strategy.position_targets as pt
    from backend.core.journal.active_positions import ActivePositionStore

    def boom(*a, **k):
        raise RuntimeError("의도적 실패")
    monkeypatch.setattr(pt, "build_targets", boom)

    store = ActivePositionStore(tmp_path / "ap2.json")
    pos = store.create_from_order(
        symbol="000001", name="테스트", strategy="ai_swing",
        entry_price=5_000.0, total_recommended_qty=10, order_no="X2",
        single_tranche=True)
    assert pos is not None and pos.entry_price == 5_000.0
    assert pos.targets == {}


def test_single_tranche_position_has_no_dca_targets(tmp_path):
    """single_tranche 진입(ai_swing·supertrend)은 pending 트랜치가 없어 DCA 가 돌지 않는다.
    실행되지 않는 추매가를 목표가에 넣으면 오정보다(§8)."""
    from backend.core.journal.active_positions import ActivePositionStore

    store = ActivePositionStore(path=str(tmp_path / "ap.json"))
    single = store.create_from_order(
        symbol="300080", name="플리토", strategy="ai_swing",
        entry_price=11480.0, total_recommended_qty=217,
        order_no="PENDING:300080", single_tranche=True,
    )
    assert len(single.tranches) == 1
    assert single.targets.get("dca_prices") == []
    assert single.targets.get("stop_loss", 0) > 0  # 나머지 목표가는 그대로 산출

    multi = store.create_from_order(
        symbol="101360", name="에코앤드림", strategy="f_zone",
        entry_price=11480.0, total_recommended_qty=217,
        order_no="PENDING:101360", single_tranche=False,
    )
    # 장부의 실제 pending 트랜치는 T2(-1.5%) 하나뿐 — 목표가도 그것만 표시해야 한다.
    # (이전에는 실재하지 않는 T3 까지 표시했다.)
    dca = multi.targets["dca_prices"]
    assert [d["tranche"] for d in dca] == [2]
    assert dca[0]["drop_pct"] == -1.5


def test_ai_swing_two_tranche_equal_amount(tmp_path):
    """ai_swing 2분할 50/50 '같은 금액' — T2 는 발동가에서 T1 과 동일 금액이 되는 수량."""
    from backend.core.journal.active_positions import ActivePositionStore

    store = ActivePositionStore(path=str(tmp_path / "ap.json"))
    entry, total, drop = 11480.0, 216, -3.0
    pos = store.create_from_order(
        symbol="300080", name="플리토", strategy="ai_swing",
        entry_price=entry, total_recommended_qty=total,
        order_no="PENDING:300080",
        single_tranche=False, split=(0.5, 0.5),
        trigger_drop_pct=drop, equal_amount=True,
    )
    t1, t2 = pos.tranches
    assert t1.status == "filled" and t1.qty == 108      # 정확히 절반
    assert t2.status == "pending" and t2.trigger_drop_pct == drop

    # 같은 금액: 발동가 × T2수량 ≈ 진입가 × T1수량 (1% 이내)
    trig = entry * (1 + drop / 100.0)
    assert abs(t2.qty * trig - t1.qty * entry) / (t1.qty * entry) < 0.01
    assert t2.qty > t1.qty                              # 싸졌으니 수량은 더 많다

    # 목표가의 추매가는 실제 pending 트랜치와 같은 발동가를 써야 한다(표시=실행).
    dca = pos.targets["dca_prices"]
    assert len(dca) == 1 and dca[0]["drop_pct"] == drop
    assert dca[0]["price"] == float(int(entry * (1 + drop / 100.0)))
