"""BAR-76/77 — 해외주식 + 코인 거래소 게이트웨이 (skeleton).

운영 진입 시 IBKR / Upbit / Bithumb 어댑터로 교체.
worktree 단계는 인터페이스 + stub.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


@runtime_checkable
class ExtendedExchangeAdapter(Protocol):
    """해외주식 / 코인 공통 인터페이스."""

    name: str
    market_type: str        # "us_stock" / "hk_stock" / "crypto"

    async def fetch_ticker(self, symbol: str) -> dict: ...
    async def submit_order(self, symbol: str, side: str, qty: Decimal) -> dict: ...


class StubUSStockGateway:
    """BAR-76 — 미국 주식 stub. IBKR / 키움 영웅문 통합은 운영."""

    name = "us_stock_stub"
    market_type = "us_stock"

    async def fetch_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "price": Decimal("0"), "venue": self.name}

    async def submit_order(self, symbol: str, side: str, qty: Decimal) -> dict:
        # paper trading mock
        return {
            "symbol": symbol, "side": side, "qty": float(qty),
            "venue": self.name, "status": "paper_filled",
        }


class StubHKStockGateway:
    """BAR-76 — 홍콩 주식 stub."""

    name = "hk_stock_stub"
    market_type = "hk_stock"

    async def fetch_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "price": Decimal("0"), "venue": self.name}

    async def submit_order(self, symbol: str, side: str, qty: Decimal) -> dict:
        return {
            "symbol": symbol, "side": side, "qty": float(qty),
            "venue": self.name, "status": "paper_filled",
        }


class StubUpbitGateway:
    """BAR-77 — Upbit stub."""

    name = "upbit_stub"
    market_type = "crypto"

    async def fetch_ticker(self, symbol: str) -> dict:
        return {"symbol": symbol, "price": Decimal("0"), "venue": self.name}

    async def submit_order(self, symbol: str, side: str, qty: Decimal) -> dict:
        return {
            "symbol": symbol, "side": side, "qty": float(qty),
            "venue": self.name, "status": "paper_filled",
        }


__all__ = [
    "ExtendedExchangeAdapter",
    "StubUSStockGateway",
    "StubHKStockGateway",
    "StubUpbitGateway",
]
