"""BAR-71 — TenantContext + UsageMetricsRecorder (10 cases)."""
from __future__ import annotations

import pytest

from backend.core.multitenancy.tenant_context import (
    TenantContext,
    UsageMetrics,
    UsageMetricsRecorder,
)


class TestTenantContext:
    def test_default_none(self):
        # 격리: 새 ContextVar 가 default None
        token = TenantContext.set_user("user-x")
        TenantContext.reset(token)
        # reset 후 default
        assert TenantContext.current_user() is None

    def test_set_and_get(self):
        token = TenantContext.set_user("user-1")
        try:
            assert TenantContext.current_user() == "user-1"
        finally:
            TenantContext.reset(token)

    def test_require_raises_when_unset(self):
        with pytest.raises(PermissionError, match="user_id required"):
            TenantContext.require_user()

    def test_require_returns_when_set(self):
        token = TenantContext.set_user("u-2")
        try:
            assert TenantContext.require_user() == "u-2"
        finally:
            TenantContext.reset(token)


class TestUsageMetrics:
    def test_record_increments(self):
        r = UsageMetricsRecorder()
        r.record("u1", 10.0)
        r.record("u1", 5.0)
        m = r.get("u1")
        assert m.api_calls == 2
        assert m.total_latency_ms == 15.0
        assert m.last_seen is not None

    def test_isolation_between_users(self):
        r = UsageMetricsRecorder()
        r.record("u1", 10.0)
        r.record("u2", 20.0)
        assert r.get("u1").api_calls == 1
        assert r.get("u2").api_calls == 1
        assert r.get("u3").api_calls == 0

    def test_record_invalid_user(self):
        r = UsageMetricsRecorder()
        with pytest.raises(ValueError):
            r.record("", 10.0)

    def test_record_negative_latency(self):
        r = UsageMetricsRecorder()
        with pytest.raises(ValueError):
            r.record("u1", -1.0)

    def test_reset_specific_user(self):
        r = UsageMetricsRecorder()
        r.record("u1", 10.0)
        r.record("u2", 20.0)
        r.reset("u1")
        assert r.get("u1").api_calls == 0
        assert r.get("u2").api_calls == 1

    def test_time_call_context_manager(self):
        r = UsageMetricsRecorder()
        with r.time_call("u1"):
            pass
        m = r.get("u1")
        assert m.api_calls == 1
        assert m.total_latency_ms >= 0
