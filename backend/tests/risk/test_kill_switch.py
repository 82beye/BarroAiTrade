"""BAR-64 — KillSwitch + CircuitBreaker (12 cases)."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from backend.core.risk.kill_switch import (
    CircuitBreaker,
    KillSwitch,
    KillSwitchReason,
    KillSwitchState,
)


class TestState:
    def test_state_frozen_default(self):
        s = KillSwitchState()
        assert s.is_active is False
        with pytest.raises(Exception):
            s.is_active = True  # type: ignore[misc]


class TestCircuitBreaker:
    def test_threshold_triggers(self):
        cb = CircuitBreaker(threshold=3, window_seconds=300)
        now = datetime(2026, 5, 7, 10, 0)
        assert cb.record(now) is False
        assert cb.record(now + timedelta(seconds=10)) is False
        assert cb.record(now + timedelta(seconds=20)) is True

    def test_window_expiry(self):
        cb = CircuitBreaker(threshold=3, window_seconds=60)
        now = datetime(2026, 5, 7, 10, 0)
        cb.record(now)
        cb.record(now + timedelta(seconds=10))
        # 윈도우 초과 — 이전 이벤트 만료
        assert cb.record(now + timedelta(seconds=120)) is False
        assert cb.count == 1

    def test_reset(self):
        cb = CircuitBreaker(threshold=2)
        cb.record(datetime.now())
        cb.reset()
        assert cb.count == 0


class TestDailyLoss:
    def test_loss_below_threshold(self):
        ks = KillSwitch()
        ks.set_account_base(Decimal("1000000"))
        # -2% loss — 미발동
        ok = ks.record_loss(Decimal("980000"), datetime(2026, 5, 7, 10, 0))
        assert ok is False
        assert ks.state.is_active is False

    def test_loss_above_threshold(self):
        ks = KillSwitch()
        ks.set_account_base(Decimal("1000000"))
        # -3% loss — 발동
        ok = ks.record_loss(Decimal("970000"), datetime(2026, 5, 7, 10, 0))
        assert ok is True
        assert ks.state.is_active is True
        assert ks.state.reason == KillSwitchReason.DAILY_LOSS

    def test_no_base_returns_false(self):
        ks = KillSwitch()
        ok = ks.record_loss(Decimal("100"), datetime(2026, 5, 7, 10, 0))
        assert ok is False


class TestSlippage:
    def test_3_events_trips(self):
        ks = KillSwitch()
        now = datetime(2026, 5, 7, 10, 0)
        assert ks.record_slippage_event(now) is False
        assert ks.record_slippage_event(now + timedelta(seconds=10)) is False
        assert ks.record_slippage_event(now + timedelta(seconds=20)) is True
        assert ks.state.reason == KillSwitchReason.SLIPPAGE


class TestGatewayDisconnect:
    def test_30s_disconnect_trips(self):
        ks = KillSwitch()
        now = datetime(2026, 5, 7, 10, 0)
        assert ks.record_gateway_event(False, now) is False
        # 30초 경과
        assert ks.record_gateway_event(False, now + timedelta(seconds=30)) is True
        assert ks.state.reason == KillSwitchReason.GATEWAY_DISCONNECT

    def test_reconnect_clears(self):
        ks = KillSwitch()
        now = datetime(2026, 5, 7, 10, 0)
        ks.record_gateway_event(False, now)
        ks.record_gateway_event(True, now + timedelta(seconds=10))
        ks.record_gateway_event(False, now + timedelta(seconds=20))
        # 재연결 후 다시 disconnect 시 timer 새로 시작
        assert ks.record_gateway_event(False, now + timedelta(seconds=25)) is False


class TestManualReset:
    def test_manual_trip(self):
        ks = KillSwitch()
        ks.trip(KillSwitchReason.MANUAL, datetime(2026, 5, 7, 10, 0))
        assert ks.state.is_active is True
        assert ks.state.reason == KillSwitchReason.MANUAL

    def test_reset_before_cooldown_fails(self):
        ks = KillSwitch()
        now = datetime(2026, 5, 7, 10, 0)
        ks.trip(KillSwitchReason.MANUAL, now)
        ok = ks.reset(now + timedelta(minutes=10))   # < 4h
        assert ok is False

    def test_reset_after_cooldown_succeeds(self):
        ks = KillSwitch()
        now = datetime(2026, 5, 7, 10, 0)
        ks.trip(KillSwitchReason.MANUAL, now)
        ok = ks.reset(now + timedelta(hours=5))
        assert ok is True
        assert ks.state.is_active is False
