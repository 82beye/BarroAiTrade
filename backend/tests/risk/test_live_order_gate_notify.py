"""BAR-OPS-22 — LiveOrderGate notifier 통합 테스트."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from backend.core.gateway.kiwoom_native_oauth import KiwoomNativeOAuth, KiwoomNativeToken
from backend.core.gateway.kiwoom_native_orders import KiwoomNativeOrderExecutor
from backend.core.notify.telegram import TelegramNotifier
from backend.core.risk.live_order_gate import (
    GatePolicy,
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


def _notifier_mock() -> AsyncMock:
    n = AsyncMock(spec=TelegramNotifier)
    n.send = AsyncMock(return_value={"message_id": 1})
    return n


@pytest.mark.asyncio
async def test_blocked_triggers_notifier_send(tmp_path, monkeypatch):
    """env-flag 차단 시 텔레그램 send 호출."""
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    notifier = _notifier_mock()
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=False)
    gate = LiveOrderGate(
        executor=exec, audit_path=tmp_path / "audit.csv", notifier=notifier,
    )
    with pytest.raises(TradingDisabled):
        await gate.place_buy(symbol="005930", qty=1)
    notifier.send.assert_awaited_once()
    sent_text = notifier.send.call_args.args[0]
    assert "차단" in sent_text
    assert "005930" in sent_text
    # Markdown escape 적용 — `_` → `\_`
    assert "LIVE\\_TRADING\\_ENABLED" in sent_text


@pytest.mark.asyncio
async def test_no_notifier_no_send(tmp_path, monkeypatch):
    """notifier=None 이면 알림 X (기존 OPS-17 동작)."""
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=False)
    gate = LiveOrderGate(executor=exec, audit_path=tmp_path / "audit.csv")
    with pytest.raises(TradingDisabled):
        await gate.place_buy(symbol="005930", qty=1)


@pytest.mark.asyncio
async def test_notifier_send_failure_does_not_propagate(tmp_path, monkeypatch):
    """텔레그램 전송 실패가 차단 동작 자체를 깨뜨리면 안 됨."""
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    notifier = AsyncMock(spec=TelegramNotifier)
    notifier.send = AsyncMock(side_effect=RuntimeError("network down"))
    exec = KiwoomNativeOrderExecutor(oauth=_oauth_mock(), dry_run=False)
    gate = LiveOrderGate(
        executor=exec, audit_path=tmp_path / "audit.csv", notifier=notifier,
    )
    with pytest.raises(TradingDisabled):       # 차단 raise 는 정상
        await gate.place_buy(symbol="005930", qty=1)
    notifier.send.assert_awaited_once()        # 시도는 함


@pytest.mark.asyncio
async def test_executor_failure_triggers_notifier(tmp_path):
    """주문 실행 실패도 텔레그램 알림."""
    exec = AsyncMock(spec=KiwoomNativeOrderExecutor)
    exec._dry_run = False
    exec.place_buy = AsyncMock(side_effect=RuntimeError("network error"))
    notifier = _notifier_mock()
    gate = LiveOrderGate(
        executor=exec, audit_path=tmp_path / "audit.csv",
        policy=GatePolicy(require_env_flag=False),
        notifier=notifier,
    )
    with pytest.raises(RuntimeError):
        await gate.place_buy(symbol="005930", qty=1)
    notifier.send.assert_awaited_once()
    sent_text = notifier.send.call_args.args[0]
    assert "RuntimeError" in sent_text
