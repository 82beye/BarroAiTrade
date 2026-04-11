"""
OrderExecutor — 큐 기반 비동기 주문 실행자

모든 주문은 이 executor를 통해 직렬 처리.
RiskEngine.approve() 통과 필수, 실패 시 최대 3회 재시도.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from backend.models.market import MarketType
from backend.models.position import Order, OrderResult, OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_SEC = 0.5


class OrderExecutor:
    """asyncio.Queue 기반 단일 주문 실행자"""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._on_filled: Optional[Callable[[OrderResult], Any]] = None

    # ── 라이프사이클 ──────────────────────────────────────────────────────────

    async def start(self, on_filled: Optional[Callable[[OrderResult], Any]] = None) -> None:
        """주문 처리 루프 시작"""
        self._on_filled = on_filled
        self._running = True
        self._task = asyncio.create_task(self._process_loop(), name="order_executor")
        logger.info("OrderExecutor 시작")

    async def stop(self) -> None:
        """주문 처리 루프 종료"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("OrderExecutor 중지")

    # ── 주문 제출 ─────────────────────────────────────────────────────────────

    async def submit(
        self,
        order: Order,
        gateway: Any,
        risk_engine: Any,
        positions: Dict,
        balance: Any,
    ) -> Optional[OrderResult]:
        """주문을 큐에 추가하고 결과를 기다림

        Returns:
            OrderResult — 성공 시
            None — 거부 또는 실패 시
        """
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._queue.put((order, gateway, risk_engine, positions, balance, future))
        try:
            return await future
        except Exception as e:
            logger.error("주문 제출 오류: %s", e)
            return None

    # ── 내부 처리 루프 ────────────────────────────────────────────────────────

    async def _process_loop(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                order, gateway, risk_engine, positions, balance, future = item
                result = await self._execute_with_retry(order, gateway, risk_engine, positions, balance)
                if not future.done():
                    future.set_result(result)
                self._queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("주문 처리 루프 오류: %s", e)

    async def _execute_with_retry(
        self,
        order: Order,
        gateway: Any,
        risk_engine: Any,
        positions: Dict,
        balance: Any,
    ) -> Optional[OrderResult]:
        """리스크 승인 후 최대 3회 재시도 주문 실행"""
        # 리스크 승인
        if risk_engine is not None:
            approved, reason = risk_engine.approve(order, positions, balance)
            if not approved:
                logger.warning("주문 거부 [%s]: %s — %s", order.symbol, order.side.value, reason)
                return None

        order.risk_approved = True

        # 재시도 루프
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                result: OrderResult = await gateway.place_order(order)
                logger.info(
                    "주문 체결 [%s] %s %s | id=%s | 시도=%d",
                    order.symbol, order.side.value, order.quantity,
                    result.order_id, attempt,
                )
                if self._on_filled and result.status in (OrderStatus.FILLED, OrderStatus.PARTIAL):
                    await self._on_filled(result)
                return result
            except Exception as e:
                logger.warning("주문 실행 실패 (시도 %d/%d): %s — %s", attempt, _MAX_RETRIES, order.symbol, e)
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_SEC * attempt)

        logger.error("주문 최종 실패: %s %s", order.symbol, order.side.value)
        return None
