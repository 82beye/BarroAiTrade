"""BAR-OPS-17 — LiveOrderGate: 실전 진입 안전 게이트.

KiwoomNativeOrderExecutor 위 wrapper. 실전(api.kiwoom.com) 호출 전:
1. LIVE_TRADING_ENABLED 환경변수 검증 (없으면 강제 DRY_RUN)
2. 일일 손실 한도 (-N% 도달 시 신규 매수 차단)
3. 일일 거래수 한도 (N 건 초과 시 차단)
4. audit log append (감사 무결성)

BAR-64 Kill Switch / BAR-68 audit log 의 경량 통합 버전.
정식 BAR-64/68 머지 시 이 게이트는 제거 또는 보강.
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

from backend.core.gateway.kiwoom_native_orders import (
    KiwoomNativeOrderExecutor,
    OrderResult,
    OrderSide,
)

logger = logging.getLogger(__name__)


_AUDIT_HEADERS = [
    "ts", "action", "side", "symbol", "qty", "price",
    "order_no", "return_code", "blocked", "reason",
]


class TradingDisabled(RuntimeError):
    """LIVE_TRADING_ENABLED 미설정 + dry_run=False 시도."""


class DailyLossLimitExceeded(RuntimeError):
    """일일 손실 한도 도달 — 신규 매수 차단."""


class DailyOrderLimitExceeded(RuntimeError):
    """일일 거래수 한도 초과."""


@dataclass(frozen=True)
class GatePolicy:
    daily_loss_limit_pct: Decimal = Decimal("-3.0")     # -3% 도달 시 차단
    daily_max_orders: int = 50                           # 일 50건
    require_env_flag: bool = True                        # LIVE_TRADING_ENABLED 강제
    env_flag_name: str = "LIVE_TRADING_ENABLED"


class LiveOrderGate:
    """주문 실행 전 안전 검증 + audit log."""

    def __init__(
        self,
        executor: KiwoomNativeOrderExecutor,
        audit_path: str | Path,
        policy: Optional[GatePolicy] = None,
    ) -> None:
        self._executor = executor
        self._audit_path = Path(audit_path)
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._policy = policy or GatePolicy()

    def _preflight(self, side: OrderSide, daily_pnl_pct: Decimal) -> None:
        # 1) ENV flag 강제 (실전 host 의 안전망)
        if self._policy.require_env_flag and not self._executor._dry_run:
            flag = os.environ.get(self._policy.env_flag_name, "").lower()
            if flag not in {"1", "true", "yes", "on"}:
                raise TradingDisabled(
                    f"{self._policy.env_flag_name}=truthy 필요 (현재: {flag!r}). "
                    f"DRY_RUN 모드는 ok. 실전 진입 시 명시적 활성화 필수."
                )

        # 2) 일일 손실 한도 — 매수만 차단 (매도는 손절 가능해야)
        if side == OrderSide.BUY and daily_pnl_pct <= self._policy.daily_loss_limit_pct:
            raise DailyLossLimitExceeded(
                f"일일 손실 한도 도달: {daily_pnl_pct}% ≤ {self._policy.daily_loss_limit_pct}%. 신규 매수 차단."
            )

        # 3) 일일 거래수 한도
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        count = self._count_today_orders(today)
        if count >= self._policy.daily_max_orders:
            raise DailyOrderLimitExceeded(
                f"일일 거래수 한도 초과: {count} ≥ {self._policy.daily_max_orders}"
            )

    async def place_buy(
        self, symbol: str, qty: int,
        price: Optional[Decimal] = None,
        daily_pnl_pct: Decimal = Decimal("0.0"),
    ) -> OrderResult:
        return await self._gated(OrderSide.BUY, symbol, qty, price, daily_pnl_pct)

    async def place_sell(
        self, symbol: str, qty: int,
        price: Optional[Decimal] = None,
        daily_pnl_pct: Decimal = Decimal("0.0"),
    ) -> OrderResult:
        return await self._gated(OrderSide.SELL, symbol, qty, price, daily_pnl_pct)

    async def _gated(
        self, side: OrderSide, symbol: str, qty: int,
        price: Optional[Decimal], daily_pnl_pct: Decimal,
    ) -> OrderResult:
        try:
            self._preflight(side, daily_pnl_pct)
        except (TradingDisabled, DailyLossLimitExceeded, DailyOrderLimitExceeded) as e:
            self._audit("BLOCKED", side, symbol, qty, price, None, None, blocked=True, reason=str(e))
            raise

        try:
            if side == OrderSide.BUY:
                result = await self._executor.place_buy(symbol, qty, price)
            else:
                result = await self._executor.place_sell(symbol, qty, price)
        except Exception as e:
            self._audit("FAILED", side, symbol, qty, price, None, None,
                        blocked=False, reason=type(e).__name__)
            raise

        self._audit(
            "ORDERED" if not result.dry_run else "DRY_RUN",
            side, symbol, qty, price,
            result.order_no, result.return_code, blocked=False,
        )
        return result

    def _audit(
        self, action: str, side: OrderSide, symbol: str, qty: int,
        price: Optional[Decimal], order_no: Optional[str],
        return_code: Optional[int], blocked: bool, reason: str = "",
    ) -> None:
        new_file = not self._audit_path.exists()
        with open(self._audit_path, "a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(_AUDIT_HEADERS)
            w.writerow([
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                action, side.value, symbol, qty,
                str(price) if price is not None else "MKT",
                order_no or "",
                str(return_code) if return_code is not None else "",
                "1" if blocked else "0",
                reason,
            ])

    def _count_today_orders(self, today: str) -> int:
        if not self._audit_path.exists():
            return 0
        n = 0
        with open(self._audit_path, "r", encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            for row in r:
                if row["ts"].startswith(today) and row["action"] in {"ORDERED", "DRY_RUN"}:
                    n += 1
        return n


__all__ = [
    "LiveOrderGate", "GatePolicy",
    "TradingDisabled", "DailyLossLimitExceeded", "DailyOrderLimitExceeded",
]
