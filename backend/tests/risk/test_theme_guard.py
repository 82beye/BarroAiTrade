"""BAR-66 — ThemeAwareRiskGuard (10 cases)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.core.risk.theme_guard import (
    ThemeAwareRiskGuard,
    ThemeExposurePolicy,
)


class TestPolicy:
    def test_default(self):
        p = ThemeExposurePolicy()
        assert p.max_theme_exposure_pct == 0.40
        assert p.max_concurrent_positions == 3
        assert p.max_position_pct == 0.30

    def test_invalid_theme_pct(self):
        with pytest.raises(ValueError):
            ThemeExposurePolicy(max_theme_exposure_pct=1.5)

    def test_invalid_concurrent(self):
        with pytest.raises(ValueError):
            ThemeExposurePolicy(max_concurrent_positions=0)

    def test_invalid_position_pct(self):
        with pytest.raises(ValueError):
            ThemeExposurePolicy(max_position_pct=0)


class TestThemeExposure:
    def test_within_limit(self):
        g = ThemeAwareRiskGuard()
        ok, _ = g.check_theme_exposure(
            order_value=Decimal("1000"),
            order_theme_id=1,
            total_value=Decimal("10000"),
            current_theme_exposure={1: Decimal("2000")},
        )
        # 2000 + 1000 = 3000 / 10000 = 30% ≤ 40%
        assert ok is True

    def test_above_limit(self):
        g = ThemeAwareRiskGuard()
        ok, reason = g.check_theme_exposure(
            order_value=Decimal("3000"),
            order_theme_id=1,
            total_value=Decimal("10000"),
            current_theme_exposure={1: Decimal("2000")},
        )
        # 2000 + 3000 = 5000 / 10000 = 50% > 40%
        assert ok is False
        assert "합산" in reason

    def test_new_theme_exposure(self):
        g = ThemeAwareRiskGuard()
        ok, _ = g.check_theme_exposure(
            order_value=Decimal("1000"),
            order_theme_id=99,
            total_value=Decimal("10000"),
            current_theme_exposure={},
        )
        assert ok is True


class TestConcurrent:
    def test_within(self):
        g = ThemeAwareRiskGuard()
        ok, _ = g.check_concurrent_positions(2)
        assert ok is True

    def test_at_limit(self):
        g = ThemeAwareRiskGuard()
        ok, _ = g.check_concurrent_positions(3)
        assert ok is False


class TestPositionSize:
    def test_within(self):
        g = ThemeAwareRiskGuard()
        ok, _ = g.check_position_size(Decimal("2000"), Decimal("10000"))
        # 20% ≤ 30%
        assert ok is True

    def test_above(self):
        g = ThemeAwareRiskGuard()
        ok, _ = g.check_position_size(Decimal("4000"), Decimal("10000"))
        assert ok is False
