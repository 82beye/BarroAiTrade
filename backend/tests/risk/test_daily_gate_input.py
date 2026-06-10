"""BAR-OPS-38 — 일일손실 게이트 입력(당일 실현+평가) + latch 영속 테스트.

근거: reports/2026-06-10/2026-06-10_매매복기.md P0#1.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.core.risk.daily_gate_input import (
    DailyGateStateStore,
    compute_daily_gate_input,
)


def _balance(total_pnl="0", est="50000000", total_eval="0"):
    return SimpleNamespace(
        total_pnl=Decimal(total_pnl),
        estimated_deposit=Decimal(est),
        total_eval=Decimal(total_eval),
    )


def _daily_entry(net, date=None):
    from datetime import datetime, timezone
    return SimpleNamespace(
        date=date or datetime.now(timezone.utc).strftime("%Y%m%d"),
        net_pnl=Decimal(str(net)),
    )


def _account(daily=None, balance=None, daily_exc=None, balance_exc=None):
    acc = AsyncMock()
    if daily_exc:
        acc.fetch_daily_pnl = AsyncMock(side_effect=daily_exc)
    else:
        acc.fetch_daily_pnl = AsyncMock(return_value=daily or [])
    if balance_exc:
        acc.fetch_balance = AsyncMock(side_effect=balance_exc)
    else:
        acc.fetch_balance = AsyncMock(return_value=balance or _balance())
    return acc


# ── compute_daily_gate_input ────────────────────────────────────────────────

async def test_compute_realized_plus_eval_over_estimated_asset():
    """당일 실현 -1,000,000 + 평가 -500,000 / 5천만 = -3.00%."""
    acc = _account(
        daily=[_daily_entry(-1_000_000)],
        balance=_balance(total_pnl="-500000", est="50000000"),
    )
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("-3.00")


async def test_compute_holdings_empty_keeps_realized_loss():
    """[6/10 결함 재현 방지] 보유 0 이어도 당일 실현손실이 입력에 남는다 — 0% 리셋 금지."""
    acc = _account(
        daily=[_daily_entry(-1_600_000)],
        balance=_balance(total_pnl="0", est="50000000"),
    )
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("-3.20")   # 종전 total_pnl_rate 방식이면 0% 였을 상황


async def test_compute_reuses_caller_balance():
    """호출부가 이미 조회한 balance 재사용 — fetch_balance 추가 호출 없음."""
    acc = _account(daily=[_daily_entry(0)])
    bal = _balance(total_pnl="250000", est="50000000")
    pct = await compute_daily_gate_input(acc, bal)
    assert pct == Decimal("0.50")
    acc.fetch_balance.assert_not_awaited()


async def test_compute_other_day_entries_excluded():
    """ka10074 응답에 과거 일자 행이 섞여도 당일분만 합산."""
    acc = _account(
        daily=[_daily_entry(-1_000_000, date="20200101"), _daily_entry(-500_000)],
        balance=_balance(total_pnl="0", est="50000000"),
    )
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("-1.00")


async def test_compute_fail_open_on_realized_error():
    """ka10074 실패 → 실현분 0 처리(평가분만) — 과차단 방지(5/29·6/1·6/2 교훈)."""
    acc = _account(
        daily_exc=RuntimeError("kiwoom down"),
        balance=_balance(total_pnl="-500000", est="50000000"),
    )
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("-1.00")


async def test_compute_fail_open_on_balance_error():
    acc = _account(daily=[_daily_entry(-1_000_000)], balance_exc=RuntimeError("down"))
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("0.0")


async def test_compute_zero_base_fail_open():
    acc = _account(daily=[_daily_entry(-1_000_000)],
                   balance=_balance(total_pnl="-1", est="0", total_eval="0"))
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("0.0")


async def test_compute_base_fallback_to_total_eval():
    """추정예탁자산 미제공 시 평가총액 폴백."""
    acc = _account(daily=[_daily_entry(0)],
                   balance=_balance(total_pnl="-300000", est="0", total_eval="10000000"))
    pct = await compute_daily_gate_input(acc)
    assert pct == Decimal("-3.00")


# ── DailyGateStateStore (latch 영속) ────────────────────────────────────────

def test_latch_roundtrip(tmp_path):
    store = DailyGateStateStore(tmp_path / "state.json")
    assert not store.is_latched()
    store.set_latched("일일 손실 한도 도달: -3.5%")
    assert store.is_latched()
    assert "한도" in store.latch_reason()
    # 새 인스턴스(데몬 사이클 재생성 시뮬레이션)에서도 latch 유지
    store2 = DailyGateStateStore(tmp_path / "state.json")
    assert store2.is_latched()


def test_latch_rolls_over_next_day(tmp_path, monkeypatch):
    store = DailyGateStateStore(tmp_path / "state.json")
    store.set_latched("latch")
    assert store.is_latched()
    # 일자가 바뀌면 자동 무효 (파일은 그대로여도 date 불일치 → 빈 상태)
    monkeypatch.setattr(
        "backend.core.risk.daily_gate_input._utc_today", lambda: "2099-01-01"
    )
    assert not store.is_latched()


def test_latch_corrupt_file_safe(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{{{{not json", encoding="utf-8")
    store = DailyGateStateStore(p)
    assert not store.is_latched()      # 손상 → 빈 상태 (예외 없음)
    store.set_latched("ok")            # 손상 파일 위에 정상 기록
    assert store.is_latched()
