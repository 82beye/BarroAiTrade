"""BAR-OPS-26 — OrderConfirmStore 테스트."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.core.notify.order_confirm import (
    OrderConfirmStore,
    PendingOrder,
)


def _orders() -> list[PendingOrder]:
    return [
        PendingOrder(symbol="319400", name="현대무벡스", qty=389),
        PendingOrder(symbol="001440", name="대한전선", qty=203),
    ]


def test_issue_returns_token_with_expected_format():
    s = OrderConfirmStore(ttl_seconds=300)
    b = s.issue(chat_id="123", orders=_orders())
    assert len(b.token) == 6
    assert b.token.isupper()
    assert all(c.isalnum() for c in b.token)
    assert b.chat_id == "123"
    assert len(b.orders) == 2


def test_issue_empty_orders_raises():
    s = OrderConfirmStore()
    with pytest.raises(ValueError, match="orders required"):
        s.issue(chat_id="123", orders=[])


def test_consume_valid_token_returns_batch_and_clears():
    s = OrderConfirmStore()
    b = s.issue(chat_id="123", orders=_orders())
    result = s.consume(chat_id="123", token=b.token)
    assert result is not None
    assert result.token == b.token
    # 두번째 consume → None (이미 폐기)
    assert s.consume(chat_id="123", token=b.token) is None


def test_consume_wrong_token_returns_none():
    s = OrderConfirmStore()
    s.issue(chat_id="123", orders=_orders())
    assert s.consume(chat_id="123", token="WRONG1") is None
    # 원본은 유효 — 다시 cancel 시 True
    assert s.cancel(chat_id="123") is True


def test_consume_other_chat_returns_none():
    s = OrderConfirmStore()
    b = s.issue(chat_id="123", orders=_orders())
    assert s.consume(chat_id="999", token=b.token) is None


def test_consume_token_case_insensitive():
    s = OrderConfirmStore()
    b = s.issue(chat_id="123", orders=_orders())
    # 소문자 입력도 허용
    result = s.consume(chat_id="123", token=b.token.lower())
    assert result is not None


def test_consume_expired_token_returns_none():
    s = OrderConfirmStore(ttl_seconds=300)
    b = s.issue(chat_id="123", orders=_orders())
    # 강제로 expires_at 과거로
    b.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert s.consume(chat_id="123", token=b.token) is None
    # 만료 토큰은 즉시 삭제 → cancel 도 False
    assert s.cancel(chat_id="123") is False


def test_cancel_removes_pending():
    s = OrderConfirmStore()
    s.issue(chat_id="123", orders=_orders())
    assert s.cancel(chat_id="123") is True
    assert s.cancel(chat_id="123") is False        # 이미 없음


def test_issue_overwrites_previous_batch_for_same_chat():
    """동일 chat 의 새 issue 가 이전 토큰 대체."""
    s = OrderConfirmStore()
    b1 = s.issue(chat_id="123", orders=[PendingOrder("A", "A", 1)])
    b2 = s.issue(chat_id="123", orders=[PendingOrder("B", "B", 1)])
    # 이전 토큰 invalid
    assert s.consume(chat_id="123", token=b1.token) is None
    # 새 토큰만 valid
    result = s.consume(chat_id="123", token=b2.token)
    assert result is not None
    assert result.orders[0].symbol == "B"


def test_gc_removes_only_expired():
    s = OrderConfirmStore()
    b_active = s.issue(chat_id="111", orders=_orders())
    b_expired = s.issue(chat_id="222", orders=_orders())
    b_expired.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert s.gc() == 1
    assert s.consume(chat_id="111", token=b_active.token) is not None
    assert s.consume(chat_id="222", token=b_expired.token) is None
