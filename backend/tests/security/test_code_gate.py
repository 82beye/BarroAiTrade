"""BAR-70 — Code Gate 정책 (10 cases)."""
from __future__ import annotations

import pytest

from backend.security.code_gate import (
    AREA_LABELS,
    PHASE_LABELS,
    PRIORITY_LABELS,
    REQUIRED_AI_LABEL,
    detect_float_money,
    is_ai_generated,
    validate_pr_labels,
)


class TestLabels:
    def test_ai_generated_detected(self):
        assert is_ai_generated(["ai-generated", "phase:3"]) is True
        assert is_ai_generated(["phase:3"]) is False

    def test_required_label_constant(self):
        assert REQUIRED_AI_LABEL == "ai-generated"

    def test_area_labels_complete(self):
        assert "area:money" in AREA_LABELS
        assert "area:security" in AREA_LABELS

    def test_phase_labels_0_to_6(self):
        for i in range(7):
            assert f"phase:{i}" in PHASE_LABELS

    def test_priority_labels(self):
        assert PRIORITY_LABELS == {"priority:p0", "priority:p1", "priority:p2"}


class TestValidation:
    def test_all_required(self):
        ok, errors = validate_pr_labels(
            ["area:money", "phase:4", "priority:p1", "ai-generated"]
        )
        assert ok is True
        assert errors == []

    def test_missing_area(self):
        ok, errors = validate_pr_labels(["phase:4", "priority:p1"])
        assert ok is False
        assert any("area:" in e for e in errors)

    def test_missing_phase(self):
        ok, errors = validate_pr_labels(["area:strategy", "priority:p2"])
        assert ok is False
        assert any("phase:" in e for e in errors)


class TestFloatMoneyDetector:
    def test_detects_float_money(self):
        src = """
class Order:
    price: float = 0.0
    qty: float = 0
    name: str = "x"
"""
        hits = detect_float_money(src)
        assert len(hits) == 2

    def test_no_false_positive(self):
        src = """
class Order:
    price: Decimal = Decimal('0')
    name: str = "x"
"""
        hits = detect_float_money(src)
        assert hits == []
