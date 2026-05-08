"""BAR-OPS-27 — PendingOrder.side 필드 + 매도 토큰 분리 테스트."""
from __future__ import annotations

from backend.core.notify.order_confirm import (
    OrderConfirmStore,
    PendingOrder,
)


def test_pending_order_default_side_buy():
    o = PendingOrder(symbol="005930", name="삼성전자", qty=10)
    assert o.side == "buy"


def test_pending_order_explicit_side_sell():
    o = PendingOrder(symbol="005930", name="삼성전자", qty=10, side="sell")
    assert o.side == "sell"


def test_buy_and_sell_orders_can_coexist_via_chat_isolation():
    """다른 chat_id 의 매수/매도 토큰 동시 유지."""
    s = OrderConfirmStore()
    buy_batch = s.issue(chat_id="111", orders=[
        PendingOrder("A", "A", 1, side="buy"),
    ])
    sell_batch = s.issue(chat_id="222", orders=[
        PendingOrder("B", "B", 1, side="sell"),
    ])
    # 각각 독립 동작
    r1 = s.consume(chat_id="111", token=buy_batch.token)
    r2 = s.consume(chat_id="222", token=sell_batch.token)
    assert r1 is not None and r1.orders[0].side == "buy"
    assert r2 is not None and r2.orders[0].side == "sell"


def test_same_chat_buy_token_replaced_by_sell():
    """동일 chat 의 매도 issue 가 매수 token 대체 (한 번에 한 개만)."""
    s = OrderConfirmStore()
    buy_batch = s.issue(chat_id="123", orders=[
        PendingOrder("A", "A", 1, side="buy"),
    ])
    sell_batch = s.issue(chat_id="123", orders=[
        PendingOrder("B", "B", 1, side="sell"),
    ])
    # 매수 토큰 무효
    assert s.consume(chat_id="123", token=buy_batch.token) is None
    # 매도 토큰만 유효
    result = s.consume(chat_id="123", token=sell_batch.token)
    assert result is not None
    assert result.orders[0].side == "sell"
