"""BAR-OPS-06 — Kiwoom OAuth2 + LiveOrderExecutor (15 cases)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from backend.core.execution.kiwoom_executor import KiwoomLiveOrderExecutor
from backend.core.gateway.kiwoom_oauth import KiwoomOAuth2Manager, TokenInfo
from backend.models.market import Exchange
from backend.models.order import (
    OrderRequest,
    OrderSide,
    OrderType,
    RoutingDecision,
    RoutingReason,
)


def _decision(side="buy", qty=10, price="70100", order_type="limit"):
    return RoutingDecision(
        request=OrderRequest(
            symbol="005930",
            side=OrderSide(side),
            qty=qty,
            order_type=OrderType(order_type),
            limit_price=Decimal(price),
        ),
        venue=Exchange.KRX,
        expected_price=Decimal(price),
        expected_qty=qty,
        reason=RoutingReason.PRICE_FIRST,
    )


# ─── TokenInfo ────────────────────────────────────────────


class TestTokenInfo:
    def test_valid_when_far_from_expiry(self):
        now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        info = TokenInfo(
            access_token=SecretStr("tkn"),
            expires_at=now + timedelta(hours=23),
        )
        assert info.is_valid(now) is True

    def test_invalid_within_30min_margin(self):
        now = datetime(2026, 5, 8, 10, 0, tzinfo=timezone.utc)
        info = TokenInfo(
            access_token=SecretStr("tkn"),
            expires_at=now + timedelta(minutes=10),
        )
        assert info.is_valid(now) is False

    def test_frozen(self):
        info = TokenInfo(
            access_token=SecretStr("x"),
            expires_at=datetime.now(timezone.utc),
        )
        with pytest.raises(Exception):
            info.token_type = "Mac"  # type: ignore[misc]


# ─── OAuth2Manager ─────────────────────────────────────────


class TestOAuth2Manager:
    def test_credentials_must_be_secretstr(self):
        with pytest.raises(TypeError):
            KiwoomOAuth2Manager(
                "https://x.com", "plain", SecretStr("s")  # type: ignore[arg-type]
            )

    def test_https_only(self):
        with pytest.raises(ValueError, match="https"):
            KiwoomOAuth2Manager(
                "http://x.com", SecretStr("k"), SecretStr("s")
            )

    @pytest.mark.asyncio
    async def test_get_token_issues_and_caches(self):
        fake_resp = MagicMock()
        fake_resp.json = MagicMock(
            return_value={"access_token": "abc-token", "expires_in": 86400}
        )
        fake_resp.raise_for_status = MagicMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=fake_resp)

        m = KiwoomOAuth2Manager(
            "https://x.com",
            SecretStr("k"),
            SecretStr("s"),
            http_client=client,
        )
        t1 = await m.get_token()
        assert t1.access_token.get_secret_value() == "abc-token"
        # 두 번째 호출 — 캐시 사용 (post 1번만)
        t2 = await m.get_token()
        assert client.post.await_count == 1
        assert t2 == t1

    @pytest.mark.asyncio
    async def test_get_token_refresh_when_expired(self):
        fake_resp = MagicMock()
        fake_resp.json = MagicMock(
            return_value={"access_token": "tkn", "expires_in": 60}
        )
        fake_resp.raise_for_status = MagicMock()

        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=fake_resp)

        m = KiwoomOAuth2Manager(
            "https://x.com",
            SecretStr("k"),
            SecretStr("s"),
            http_client=client,
        )
        # TTL 60초 → 즉시 만료 임박 → 매번 refresh
        await m.get_token(datetime.now(timezone.utc))
        await m.get_token(datetime.now(timezone.utc) + timedelta(hours=1))
        assert client.post.await_count == 2

    @pytest.mark.asyncio
    async def test_missing_access_token_raises(self):
        fake_resp = MagicMock()
        fake_resp.json = MagicMock(return_value={"err": "x"})
        fake_resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=fake_resp)
        m = KiwoomOAuth2Manager(
            "https://x.com",
            SecretStr("k"),
            SecretStr("s"),
            http_client=client,
        )
        with pytest.raises(ValueError, match="missing access_token"):
            await m.get_token()

    def test_reset_cache(self):
        m = KiwoomOAuth2Manager(
            "https://x.com", SecretStr("k"), SecretStr("s")
        )
        m._token = TokenInfo(
            access_token=SecretStr("x"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
        )
        m.reset_cache()
        assert m._token is None


# ─── KiwoomLiveOrderExecutor ──────────────────────────────


@pytest.fixture
def oauth_mock():
    m = AsyncMock(spec=KiwoomOAuth2Manager)
    m._base_url = "https://x.com"
    m.get_token = AsyncMock(
        return_value=TokenInfo(
            access_token=SecretStr("test-token"),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=23),
        )
    )
    return m


class TestLiveExecutor:
    def test_credentials_required(self, oauth_mock):
        with pytest.raises(TypeError):
            KiwoomLiveOrderExecutor(
                oauth_mock, "1234567", "plain", SecretStr("s")  # type: ignore[arg-type]
            )

    def test_account_no_required(self, oauth_mock):
        with pytest.raises(ValueError):
            KiwoomLiveOrderExecutor(
                oauth_mock, "", SecretStr("k"), SecretStr("s")
            )

    @pytest.mark.asyncio
    async def test_submit_buy_filled(self, oauth_mock):
        fake_resp = MagicMock()
        fake_resp.json = MagicMock(
            return_value={
                "rt_cd": "0",
                "msg_cd": "OK",
                "msg1": "주문이 완료되었습니다",
                "output": {"ODNO": "0000001"},
            }
        )
        fake_resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=fake_resp)

        ex = KiwoomLiveOrderExecutor(
            oauth=oauth_mock,
            account_no="1234567-01",
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
        )
        result = await ex.submit(_decision())
        assert result["status"] == "filled"
        assert result["tr_id"] == "TTTC0802U"
        assert result["order_id"] == "0000001"

    @pytest.mark.asyncio
    async def test_submit_sell_uses_different_tr_id(self, oauth_mock):
        fake_resp = MagicMock()
        fake_resp.json = MagicMock(return_value={"rt_cd": "0"})
        fake_resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=fake_resp)

        ex = KiwoomLiveOrderExecutor(
            oauth=oauth_mock,
            account_no="1234567",
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
        )
        result = await ex.submit(_decision(side="sell"))
        assert result["tr_id"] == "TTTC0801U"

    @pytest.mark.asyncio
    async def test_submit_rejected(self, oauth_mock):
        fake_resp = MagicMock()
        fake_resp.json = MagicMock(
            return_value={"rt_cd": "1", "msg_cd": "ERR", "msg1": "주문 실패"}
        )
        fake_resp.raise_for_status = MagicMock()
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=fake_resp)

        ex = KiwoomLiveOrderExecutor(
            oauth=oauth_mock,
            account_no="1234567",
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
            http_client=client,
        )
        result = await ex.submit(_decision())
        assert result["status"] == "rejected"

    @pytest.mark.asyncio
    async def test_submit_unrouted_decision(self, oauth_mock):
        ex = KiwoomLiveOrderExecutor(
            oauth=oauth_mock,
            account_no="1234567",
            app_key=SecretStr("k"),
            app_secret=SecretStr("s"),
        )
        unrouted = RoutingDecision(
            request=OrderRequest(symbol="x", side=OrderSide.BUY, qty=1),
            venue=None,
            expected_price=None,
            expected_qty=0,
            reason=RoutingReason.NO_LIQUIDITY,
        )
        result = await ex.submit(unrouted)
        assert result["status"] == "skipped"
