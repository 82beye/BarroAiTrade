"""BAR-OPS-17 — LiveOrderGate 테스트."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth, KiwoomNativeToken
from backend.core.gateway.kiwoom_native_orders import (
    KiwoomNativeOrderExecutor,
    OrderResult,
    OrderSide,
)
from backend.core.risk.live_order_gate import (
    DailyLossLimitExceeded,
    DailyOrderLimitExceeded,
    GatePolicy,
    InvalidOrderQty,
    LiveOrderGate,
    TradingDisabled,
)


def _oauth_mock() -> AsyncMock:
    o = AsyncMock(spec=KiwoomNativeOAuth)
    o.base_url = "https://mockapi.kiwoom.com"
    o.get_token = AsyncMock(
        return_value=KiwoomNativeToken(
            access_token=SecretStr("tok"), token_type="Bearer",
            expires_at=datetime(2099, 1, 1),
        )
    )
    return o


def _make_gate(tmp_path, *, dry_run: bool, policy=None):
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=dry_run)
    return LiveOrderGate(
        executor=exec,
        audit_path=tmp_path / "audit.csv",
        policy=policy,
    )


# -- env flag enforcement ---------------------------------------------------


@pytest.mark.asyncio
async def test_env_flag_required_when_not_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    gate = _make_gate(tmp_path, dry_run=False)
    with pytest.raises(TradingDisabled, match="LIVE_TRADING_ENABLED"):
        await gate.place_buy(symbol="005930", qty=1)
    # audit BLOCKED 기록 확인
    audit = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert "BLOCKED" in audit
    assert "LIVE_TRADING_ENABLED" in audit


@pytest.mark.asyncio
async def test_env_flag_not_required_when_dry_run(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    gate = _make_gate(tmp_path, dry_run=True)
    r = await gate.place_buy(symbol="005930", qty=1)
    assert r.dry_run is True


@pytest.mark.asyncio
async def test_env_flag_truthy_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    # KiwoomNativeOrderExecutor 의 dry_run=False 인데 mock 호출 X — 직접 _executor patch
    gate = _make_gate(tmp_path, dry_run=False)
    gate._executor.place_buy = AsyncMock(return_value=OrderResult(
        side=OrderSide.BUY, symbol="005930", qty=1, price=None,
        order_no="0001", return_code=0, return_msg="ok",
    ))
    r = await gate.place_buy(symbol="005930", qty=1)
    assert r.order_no == "0001"


# -- daily loss limit -------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_loss_limit_blocks_buy(tmp_path):
    gate = _make_gate(tmp_path, dry_run=True, policy=GatePolicy(daily_loss_limit_pct=Decimal("-3.0")))
    with pytest.raises(DailyLossLimitExceeded):
        await gate.place_buy(symbol="005930", qty=1, daily_pnl_pct=Decimal("-3.5"))


@pytest.mark.asyncio
async def test_daily_loss_limit_allows_sell_for_stop_loss(tmp_path):
    gate = _make_gate(tmp_path, dry_run=True, policy=GatePolicy(daily_loss_limit_pct=Decimal("-3.0")))
    r = await gate.place_sell(symbol="005930", qty=1, daily_pnl_pct=Decimal("-5.0"))
    assert r.dry_run is True


# -- daily order count limit ------------------------------------------------


@pytest.mark.asyncio
async def test_daily_max_orders(tmp_path):
    policy = GatePolicy(daily_max_orders=2, require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    await gate.place_buy(symbol="005930", qty=1)
    await gate.place_buy(symbol="005930", qty=1)
    with pytest.raises(DailyOrderLimitExceeded):
        await gate.place_buy(symbol="005930", qty=1)


# -- audit log -------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_logs_dry_run_orders(tmp_path):
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    await gate.place_buy(symbol="005930", qty=10)
    await gate.place_sell(symbol="005930", qty=5, price=Decimal("280000"))

    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert "DRY_RUN,buy,005930,10" in content
    assert "DRY_RUN,sell,005930,5,280000" in content


@pytest.mark.asyncio
async def test_audit_logs_blocked_with_reason(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    gate = _make_gate(tmp_path, dry_run=False)
    with pytest.raises(TradingDisabled):
        await gate.place_buy(symbol="005930", qty=1)
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    # blocked=1, reason 포함
    assert ",BLOCKED,buy,005930,1," in content
    assert ",1,LIVE_TRADING_ENABLED" in content


# -- qty<=0 사전 차단 (2026-05-13 DCA ValueError 회귀 방지) ------------------


@pytest.mark.asyncio
async def test_qty_zero_buy_blocked_before_executor(tmp_path):
    """qty=0 매수 시도 → InvalidOrderQty + audit BLOCKED, executor 미호출."""
    policy = GatePolicy(require_env_flag=False)
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=True)
    exec.place_buy = AsyncMock()  # 호출되면 안 됨
    gate = LiveOrderGate(executor=exec, audit_path=tmp_path / "audit.csv", policy=policy)

    with pytest.raises(InvalidOrderQty, match="qty must be > 0"):
        await gate.place_buy(symbol="012860", qty=0)

    exec.place_buy.assert_not_called()
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert ",BLOCKED,buy,012860,0," in content
    assert "qty must be > 0" in content


@pytest.mark.asyncio
async def test_qty_negative_sell_blocked(tmp_path):
    """qty=-1 매도 시도 → 동일하게 InvalidOrderQty."""
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    with pytest.raises(InvalidOrderQty):
        await gate.place_sell(symbol="005930", qty=-1)
    content = (tmp_path / "audit.csv").read_text(encoding="utf-8")
    assert ",BLOCKED,sell,005930,-1," in content


@pytest.mark.asyncio
async def test_qty_positive_passes(tmp_path):
    """정상 qty 는 게이트 통과 후 executor 호출 — 회귀 방지."""
    policy = GatePolicy(require_env_flag=False)
    gate = _make_gate(tmp_path, dry_run=True, policy=policy)
    result = await gate.place_buy(symbol="005930", qty=1)
    assert result.dry_run is True
