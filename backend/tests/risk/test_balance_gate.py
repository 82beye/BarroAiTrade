"""BAR-OPS-16 / BAR-OPS-09 Phase 9 — balance_gate 테스트 (균등 분배).

5/23 균등 분배 정책 도입 후 재작성. 핵심:
- default max_per_position_ratio=0.10, max_total_position_ratio=0.80,
  max_concurrent_positions=10 → 종목당 슬롯 = cash * 0.80 / 10 = cash * 8%.
- 시그널 수가 max_concurrent 미만이어도 슬롯 고정 (잔여 cash 보존).
- 가격 > 슬롯 시 차단.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.core.gateway.kiwoom_native_account import (
    AccountBalance,
    AccountDeposit,
    HoldingPosition,
)
from backend.core.risk.balance_gate import evaluate_risk_gate


def _deposit(cash: int = 50_000_000) -> AccountDeposit:
    return AccountDeposit(
        cash=Decimal(str(cash)),
        margin_cash=Decimal("0"),
        bond_margin_cash=Decimal("0"),
        next_day_settlement=Decimal("0"),
    )


def _balance(eval_amt: int = 0, holdings: list[HoldingPosition] | None = None) -> AccountBalance:
    return AccountBalance(
        total_purchase=Decimal(str(eval_amt)),
        total_eval=Decimal(str(eval_amt)),
        total_pnl=Decimal("0"),
        total_pnl_rate=Decimal("0"),
        estimated_deposit=Decimal("0"),
        holdings=holdings or [],
    )


# -- 입력 검증 ----------------------------------------------------------------


def test_invalid_ratio_raises():
    with pytest.raises(ValueError, match="max_per_position_ratio"):
        evaluate_risk_gate(
            deposit=_deposit(), balance=_balance(),
            candidates=[],
            max_per_position_ratio=Decimal("1.5"),
        )
    with pytest.raises(ValueError, match="max_total_position_ratio"):
        evaluate_risk_gate(
            deposit=_deposit(), balance=_balance(),
            candidates=[],
            max_total_position_ratio=Decimal("0"),
        )


def test_invalid_concurrent_positions_raises():
    """BAR-OPS-09 Phase 9: max_concurrent_positions < 1 차단."""
    with pytest.raises(ValueError, match="max_concurrent_positions"):
        evaluate_risk_gate(
            deposit=_deposit(), balance=_balance(),
            candidates=[],
            max_concurrent_positions=0,
        )


# -- 균등 분배 동작 -----------------------------------------------------------


def test_even_slot_default_policy():
    """default: cash 50M × 0.80 / 10 = 4M, 가격 100k → 40 주."""
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=_balance(),
        candidates=[("005930", "삼성전자", Decimal("100000"))],
    )
    r = gate.recommendations[0]
    assert r.recommended_qty == 40
    assert r.blocked is False
    assert gate.max_per_position == Decimal("4000000")  # 균등 슬롯 4M
    assert gate.max_total_position == Decimal("40000000")  # 50M*0.80
    assert gate.available == Decimal("40000000")
    assert gate.max_concurrent_positions == 10


def test_qty_distributes_evenly_across_candidates():
    """4 종목 후보 모두 동일 슬롯 적용 — 순차 그리디 회귀 방지.

    cash 50M × 0.80 / 10 = 4M, 가격 1M → 종목당 4 주. 4 종목 모두 4 주씩 균등.
    """
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=_balance(),
        candidates=[
            ("000001", "A", Decimal("1000000")),
            ("000002", "B", Decimal("1000000")),
            ("000003", "C", Decimal("1000000")),
            ("000004", "D", Decimal("1000000")),
        ],
    )
    qtys = [r.recommended_qty for r in gate.recommendations]
    assert qtys == [4, 4, 4, 4], f"균등 분배 깨짐: {qtys}"
    assert all(not r.blocked for r in gate.recommendations)


def test_signals_fewer_than_max_concurrent_keeps_slot_size():
    """시그널 3개 < max_concurrent 10 인 경우에도 slot 크기 1/10 고정 (옵션 a).

    잔여 cash 는 다음 영업일 또는 추가 시그널 대비 보존.
    """
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=_balance(),
        candidates=[
            ("000001", "A", Decimal("100000")),
            ("000002", "B", Decimal("100000")),
            ("000003", "C", Decimal("100000")),
        ],
    )
    # 50M*0.80/10 = 4M / 100k = 40주 각각 (= 1/10 슬롯 유지)
    qtys = [r.recommended_qty for r in gate.recommendations]
    assert qtys == [40, 40, 40], f"슬롯 크기 비균등화: {qtys}"
    # 총 사용 자금 12M < available 40M — 잔여 28M 보존 검증
    total_consumed = sum(r.recommended_qty * 100_000 for r in gate.recommendations)
    assert total_consumed == 12_000_000
    assert gate.available == Decimal("40000000")


def test_max_per_position_cap_applied_when_concurrent_too_low():
    """max_concurrent_positions=2 일 때 even_slot 50M*0.80/2 = 20M 가 cap 5M(10%) 초과 → cap 5M 적용."""
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=_balance(),
        candidates=[("000001", "A", Decimal("100000"))],
        max_concurrent_positions=2,
    )
    # cap = 50M * 0.10 = 5M (even_slot 20M 보다 작음) → slot = 5M, 가격 100k → 50주
    assert gate.max_per_position == Decimal("5000000")
    assert gate.recommendations[0].recommended_qty == 50


def test_existing_holdings_reduce_available():
    """cash 50M, current_eval 30M → available = 50*0.80 - 30 = 10M.
    even_slot 4M < available 10M → 슬롯 그대로 적용.
    """
    bal = _balance(eval_amt=30_000_000)
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=bal,
        candidates=[("005930", "삼성전자", Decimal("100000"))],
    )
    assert gate.available == Decimal("10000000")
    r = gate.recommendations[0]
    assert r.recommended_qty == 40  # 4M / 100k = 40


def test_available_smaller_than_slot_blocks():
    """available 1M < even_slot 4M 인 경우 첫 종목만 가능, 두 번째부터 차단.

    cash 50M, eval 39M → available = 50*0.80 - 39 = 1M.
    slot = min(4M, 1M - 0) = 1M, 가격 100k → 10주. 1M 소진 후 두 번째 종목 차단.
    """
    bal = _balance(eval_amt=39_000_000)
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=bal,
        candidates=[
            ("000001", "A", Decimal("100000")),
            ("000002", "B", Decimal("100000")),
        ],
    )
    assert gate.available == Decimal("1000000")
    assert gate.recommendations[0].recommended_qty == 10
    assert gate.recommendations[1].blocked is True


def test_price_higher_than_slot_blocked():
    """cash 1M, even_slot = 1M*0.80/10 = 80k, 가격 500k → 0 주 + blocked."""
    gate = evaluate_risk_gate(
        deposit=_deposit(1_000_000), balance=_balance(),
        candidates=[("000001", "고가주", Decimal("500000"))],
    )
    r = gate.recommendations[0]
    assert r.recommended_qty == 0
    assert r.blocked is True
    assert "슬롯" in r.reason


def test_invalid_price_blocked():
    gate = evaluate_risk_gate(
        deposit=_deposit(), balance=_balance(),
        candidates=[("000001", "X", Decimal("0"))],
    )
    r = gate.recommendations[0]
    assert r.blocked is True
    assert "invalid price" in r.reason


def test_full_holdings_no_room():
    """cash 10M, eval 10M → available = 10*0.80 - 10 = -2 → 0 (clamp)."""
    gate = evaluate_risk_gate(
        deposit=_deposit(10_000_000), balance=_balance(eval_amt=10_000_000),
        candidates=[("000001", "X", Decimal("100"))],
    )
    assert gate.available == Decimal("0")
    r = gate.recommendations[0]
    assert r.recommended_qty == 0
    assert r.blocked is True


def test_custom_concurrent_positions_override():
    """max_concurrent_positions=20 → 50M*0.80/20 = 2M 슬롯."""
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=_balance(),
        candidates=[("000001", "A", Decimal("100000"))],
        max_concurrent_positions=20,
    )
    assert gate.max_per_position == Decimal("2000000")
    assert gate.recommendations[0].recommended_qty == 20
