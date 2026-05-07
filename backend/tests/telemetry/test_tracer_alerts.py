"""BAR-73 — Tracer + AlertPolicy (10 cases)."""
from __future__ import annotations

import pytest

from backend.core.telemetry.alerts import AlertPolicy, AlertSeverity, build_alerts_yaml
from backend.core.telemetry.tracer import Tracer


class TestTracer:
    def test_single_span(self):
        t = Tracer()
        with t.start_span("op1", {"k": "v"}):
            pass
        assert len(t.spans) == 1
        s = t.spans[0]
        assert s.operation == "op1"
        assert s.attributes == {"k": "v"}
        assert s.duration_ms >= 0

    def test_nested_spans_share_trace_id(self):
        t = Tracer()
        with t.start_span("parent") as parent:
            with t.start_span("child") as child:
                assert child.trace_id == parent.trace_id
                assert child.parent_span_id == parent.span_id
        assert len(t.spans) == 2

    def test_unique_trace_ids_for_unrelated_spans(self):
        t = Tracer()
        with t.start_span("a") as a:
            pass
        with t.start_span("b") as b:
            pass
        assert a.trace_id != b.trace_id

    def test_reset(self):
        t = Tracer()
        with t.start_span("x"):
            pass
        t.reset()
        assert t.spans == []


class TestAlertPolicy:
    def test_severity_enum(self):
        assert AlertSeverity.CRITICAL.value == "critical"

    def test_policy_frozen(self):
        p = AlertPolicy(
            name="HighLoss",
            severity=AlertSeverity.CRITICAL,
            expr="daily_pnl < -0.03",
            summary="일일 손실 한도",
        )
        with pytest.raises(Exception):
            p.name = "x"  # type: ignore[misc]

    def test_to_yaml_dict(self):
        p = AlertPolicy(
            name="StaleData",
            severity=AlertSeverity.WARNING,
            expr="time() - last_data > 300",
            for_duration="1m",
        )
        d = p.to_yaml_dict()
        assert d["alert"] == "StaleData"
        assert d["expr"] == "time() - last_data > 300"
        assert d["labels"]["severity"] == "warning"
        assert d["for"] == "1m"

    def test_required_fields(self):
        with pytest.raises(Exception):
            AlertPolicy(name="", severity=AlertSeverity.INFO, expr="x")

    def test_build_alerts_yaml(self):
        policies = [
            AlertPolicy(name="A", severity=AlertSeverity.CRITICAL, expr="x"),
            AlertPolicy(name="B", severity=AlertSeverity.WARNING, expr="y"),
        ]
        out = build_alerts_yaml(policies)
        assert out["groups"][0]["name"] == "barro_ai_trade"
        assert len(out["groups"][0]["rules"]) == 2

    def test_empty_alerts(self):
        out = build_alerts_yaml([])
        assert out["groups"][0]["rules"] == []
