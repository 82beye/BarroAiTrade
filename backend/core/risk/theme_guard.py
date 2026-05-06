"""BAR-66 — ThemeAwareRiskGuard.

동일 테마 합산 한도 검증. RiskEngine 의 동시 포지션 / 종목 비중 / 총 익스포저
정책에 더해 "동일 테마 합산 ≤ MAX" 게이트.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ThemeExposurePolicy:
    """테마 노출 정책 (frozen-ish — 인스턴스 후 mutate 금지)."""

    def __init__(
        self,
        max_theme_exposure_pct: float = 0.40,   # 동일 테마 합산 ≤ 40%
        max_concurrent_positions: int = 3,       # 동시 보유 ≤ 3 종목
        max_position_pct: float = 0.30,          # 종목당 ≤ 30%
    ) -> None:
        if not (0 < max_theme_exposure_pct <= 1):
            raise ValueError("max_theme_exposure_pct out of range")
        if max_concurrent_positions <= 0:
            raise ValueError("max_concurrent_positions must be > 0")
        if not (0 < max_position_pct <= 1):
            raise ValueError("max_position_pct out of range")
        self.max_theme_exposure_pct = max_theme_exposure_pct
        self.max_concurrent_positions = max_concurrent_positions
        self.max_position_pct = max_position_pct


class ThemeAwareRiskGuard:
    """테마 합산 한도 검증.

    호출자가 theme_repo 결과 (theme_id → list[symbol]) 를 외부에서 조회 후 주입.
    """

    def __init__(self, policy: Optional[ThemeExposurePolicy] = None) -> None:
        self._policy = policy or ThemeExposurePolicy()

    @property
    def policy(self) -> ThemeExposurePolicy:
        return self._policy

    def check_theme_exposure(
        self,
        order_value: Decimal,
        order_theme_id: int,
        total_value: Decimal,
        current_theme_exposure: Dict[int, Decimal],
    ) -> tuple[bool, str]:
        """동일 테마 합산 한도 검사.

        Args:
            order_value: 신규 주문 금액
            order_theme_id: 신규 주문 종목의 테마 ID
            total_value: 계좌 총 자산
            current_theme_exposure: 테마별 현재 노출 금액 dict

        Returns:
            (approved, reason)
        """
        if total_value <= 0:
            return False, "total_value <= 0"
        existing = current_theme_exposure.get(order_theme_id, Decimal(0))
        new_exposure = existing + order_value
        new_pct = float(new_exposure / total_value)
        if new_pct > self._policy.max_theme_exposure_pct:
            return False, (
                f"theme {order_theme_id} 합산 {new_pct:.1%} > "
                f"{self._policy.max_theme_exposure_pct:.1%}"
            )
        return True, "approved"

    def check_concurrent_positions(self, current_count: int) -> tuple[bool, str]:
        if current_count >= self._policy.max_concurrent_positions:
            return False, (
                f"동시 포지션 {current_count} >= "
                f"{self._policy.max_concurrent_positions}"
            )
        return True, "approved"

    def check_position_size(
        self, order_value: Decimal, total_value: Decimal
    ) -> tuple[bool, str]:
        if total_value <= 0:
            return False, "total_value <= 0"
        pct = float(order_value / total_value)
        if pct > self._policy.max_position_pct:
            return False, (
                f"종목 비중 {pct:.1%} > {self._policy.max_position_pct:.1%}"
            )
        return True, "approved"


__all__ = ["ThemeExposurePolicy", "ThemeAwareRiskGuard"]
