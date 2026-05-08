"""BAR-OPS-04 — OrderExecutor 운영 어댑터 stub (10 cases)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from backend.core.execution.order_executors import (
    IBKROrderExecutor,
    KiwoomOrderExecutor,
    PaperOrderExecutor,
    UpbitOrderExecutor,
)
from backend.models.market import Exchange
from backend.models.order import (
    OrderRequest,
    OrderSide,
    RoutingDecision,
    RoutingReason,
)


def _decision() -> RoutingDecision:
    return RoutingDecision(
        request=OrderRequest(symbol="005930", side=OrderSide.BUY, qty=10),
        venue=Exchange.KRX,
        expected_price=Decimal("70100"),
        expected_qty=10,
        reason=RoutingReason.PRICE_FIRST,
    )


class TestPaper:
    @pytest.mark.asyncio
    async def test_paper_filled(self):
        ex = PaperOrderExecutor()
        result = await ex.submit(_decision())
        assert result["status"] == "paper_filled"
        assert result["executor"] == "paper"
        assert ex.submitted == [_decision()]


class TestKiwoom:
    def test_secretstr_required(self):
        with pytest.raises(TypeError):
            KiwoomOrderExecutor("plain", SecretStr("s"), "1234")  # type: ignore[arg-type]

    def test_account_no_required(self):
        with pytest.raises(ValueError):
            KiwoomOrderExecutor(SecretStr("k"), SecretStr("s"), "")

    @pytest.mark.asyncio
    async def test_mock_mode(self):
        ex = KiwoomOrderExecutor(SecretStr("k"), SecretStr("s"), "1234567", mock=True)
        result = await ex.submit(_decision())
        assert result["status"] == "mock_filled"

    @pytest.mark.asyncio
    async def test_live_mode_raises(self):
        ex = KiwoomOrderExecutor(SecretStr("k"), SecretStr("s"), "1234567", mock=False)
        with pytest.raises(NotImplementedError):
            await ex.submit(_decision())


class TestIBKR:
    def test_secretstr_required(self):
        with pytest.raises(TypeError):
            IBKROrderExecutor("plain")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_mock(self):
        ex = IBKROrderExecutor(SecretStr("k"))
        result = await ex.submit(_decision())
        assert result["executor"] == "ibkr"
        assert result["status"] == "mock_filled"


class TestUpbit:
    def test_secretstr_required(self):
        with pytest.raises(TypeError):
            UpbitOrderExecutor("a", SecretStr("s"))  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            UpbitOrderExecutor(SecretStr("a"), "s")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_mock(self):
        ex = UpbitOrderExecutor(SecretStr("a"), SecretStr("s"))
        result = await ex.submit(_decision())
        assert result["executor"] == "upbit"
