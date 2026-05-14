"""BAR-OPS-16 — balance_gate 테스트."""
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


def test_qty_calculation_per_position_limit():
    # cash 50M, per 30% = 15M, 가격 100k → 150 주
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=_balance(),
        candidates=[("005930", "삼성전자", Decimal("100000"))],
    )
    r = gate.recommendations[0]
    assert r.recommended_qty == 150
    assert r.blocked is False
    assert gate.max_per_position == Decimal("15000000")
    assert gate.max_total_position == Decimal("45000000")
    assert gate.available == Decimal("45000000")


def test_qty_distributes_evenly_across_candidates():
    # cash 10M, per 30% = 3M, total 90% = 9M, available = 9M
    # 후보 4개 → per_slot = 9M / 4 = 2.25M → 종목당 2주 (2.25M/1M ROUND_DOWN)
    # 순차 그리디(앞 종목 예산 독식) 대신 균등 분배 — 4종목 모두 진입
    gate = evaluate_risk_gate(
        deposit=_deposit(10_000_000), balance=_balance(),
        candidates=[
            ("000001", "A", Decimal("1000000")),
            ("000002", "B", Decimal("1000000")),
            ("000003", "C", Decimal("1000000")),
            ("000004", "D", Decimal("1000000")),
        ],
    )
    qtys = [r.recommended_qty for r in gate.recommendations]
    assert qtys == [2, 2, 2, 2]
    assert all(not r.blocked for r in gate.recommendations)
    assert sum(q * 1_000_000 for q in qtys) <= 9_000_000


def test_existing_holdings_reduce_available():
    # cash 50M, current_eval 30M → available = 50*0.9 - 30 = 15M
    bal = _balance(eval_amt=30_000_000)
    gate = evaluate_risk_gate(
        deposit=_deposit(50_000_000), balance=bal,
        candidates=[("005930", "삼성전자", Decimal("100000"))],
    )
    assert gate.available == Decimal("15000000")
    # per 30% = 15M, available 15M → min = 15M, qty = 150
    r = gate.recommendations[0]
    assert r.recommended_qty == 150


def test_price_higher_than_slot_blocked():
    # cash 1M, per 30% = 300k, 가격 500k → 0 주 + blocked
    gate = evaluate_risk_gate(
        deposit=_deposit(1_000_000), balance=_balance(),
        candidates=[("000001", "고가주", Decimal("500000"))],
    )
    r = gate.recommendations[0]
    assert r.recommended_qty == 0
    assert r.blocked is True
    assert "한도" in r.reason


def test_invalid_price_blocked():
    gate = evaluate_risk_gate(
        deposit=_deposit(), balance=_balance(),
        candidates=[("000001", "X", Decimal("0"))],
    )
    r = gate.recommendations[0]
    assert r.blocked is True
    assert "invalid price" in r.reason


def test_full_holdings_no_room():
    # cash 10M, current_eval 10M → available = 10*0.9 - 10 = -1 → 0 (clamp)
    gate = evaluate_risk_gate(
        deposit=_deposit(10_000_000), balance=_balance(eval_amt=10_000_000),
        candidates=[("000001", "X", Decimal("100"))],
    )
    assert gate.available == Decimal("0")
    r = gate.recommendations[0]
    assert r.recommended_qty == 0
    assert r.blocked is True
