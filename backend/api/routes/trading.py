"""
매매 API 라우터

엔드포인트:
  POST /api/trading/start                    - 매매 시스템 시작
  POST /api/trading/stop                     - 매매 시스템 중지
  POST /api/trading/order                    - 주문 실행
  DELETE /api/trading/order/:order_id        - 주문 취소
  GET /api/trading/order/:order_id           - 주문 상태 조회
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Path, Body, HTTPException, Query

from backend.core.gateway.base import MarketGateway
from backend.models.position import Order, OrderSide, OrderType
from backend.models.market import MarketType
from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/trading/start")
async def start_trading(
    mode: str = Query("simulation", description="매매 모드: simulation | live"),
    market: str = Query("stock", description="마켓: stock | crypto"),
) -> dict:
    """매매 시스템 시작"""
    if app_state.trading_state == "running":
        return {"success": False, "message": "이미 실행 중", "state": app_state.trading_state}

    try:
        from backend.core.orchestrator import orchestrator
        await orchestrator.start(mode=mode, market=market)
        logger.info("매매 시스템 시작: mode=%s, market=%s", mode, market)
        return {
            "success": True,
            "message": f"매매 시스템 시작 ({mode}/{market})",
            "state": app_state.trading_state,
        }
    except Exception as e:
        logger.error("매매 시스템 시작 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trading/stop")
async def stop_trading(
    reason: str = Query("수동 중지", description="중지 사유"),
) -> dict:
    """매매 시스템 중지"""
    if app_state.trading_state not in ("running", "error"):
        return {"success": False, "message": "실행 중이 아님", "state": app_state.trading_state}

    try:
        from backend.core.orchestrator import orchestrator
        await orchestrator.stop(reason=reason)
        logger.info("매매 시스템 중지: %s", reason)
        return {
            "success": True,
            "message": "매매 시스템 중지 완료",
            "state": app_state.trading_state,
        }
    except Exception as e:
        logger.error("매매 시스템 중지 실패: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def _get_gateway() -> MarketGateway:
    """마켓 게이트웨이 인스턴스 반환"""
    gateway = app_state.market_gateway
    if not gateway:
        raise HTTPException(
            status_code=503,
            detail="마켓 게이트웨이 미초기화"
        )
    return gateway


@router.post("/trading/order")
async def place_order(
    order_data: dict = Body(..., example={
        "symbol": "005930",
        "side": "buy",
        "order_type": "limit",
        "quantity": 10,
        "price": 75000,
        "strategy_id": "manual"
    }),
) -> dict:
    """
    주문 실행

    요청 본문:
    ```json
    {
      "symbol": "005930",
      "side": "buy",
      "order_type": "limit",
      "quantity": 10,
      "price": 75000,
      "strategy_id": "manual"
    }
    ```

    응답:
    ```json
    {
      "order_id": "ORD123456",
      "symbol": "005930",
      "side": "buy",
      "status": "pending",
      "filled_quantity": 0,
      "avg_price": 0,
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        # 요청 데이터 검증
        side = OrderSide(order_data["side"])
        order_type = OrderType(order_data["order_type"])

        order = Order(
            symbol=order_data["symbol"],
            side=side,
            order_type=order_type,
            quantity=float(order_data["quantity"]),
            price=float(order_data.get("price", 0)) if order_type == OrderType.LIMIT else None,
            market_type=MarketType.STOCK,
            strategy_id=order_data.get("strategy_id", "manual"),
            risk_approved=True,
        )

        gateway = _get_gateway()
        result = await gateway.place_order(order)

        logger.info(f"주문 실행: {result.symbol} {result.side.value} {result.filled_quantity}/{order.quantity}")

        return {
            "order_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.value,
            "status": result.status.value,
            "filled_quantity": result.filled_quantity,
            "avg_price": result.avg_price,
            "timestamp": result.timestamp.isoformat(),
        }
    except ValueError as e:
        logger.error(f"주문 파라미터 오류: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"주문 실행 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/trading/order/{order_id}")
async def cancel_order(
    order_id: str = Path(..., description="주문 ID"),
) -> dict:
    """
    주문 취소

    응답:
    ```json
    {
      "order_id": "ORD123456",
      "cancelled": true,
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        success = await gateway.cancel_order(order_id)

        if not success:
            raise HTTPException(
                status_code=400,
                detail=f"주문 취소 실패: {order_id}"
            )

        logger.info(f"주문 취소: {order_id}")

        return {
            "order_id": order_id,
            "cancelled": True,
            "timestamp": None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"주문 취소 실패: {order_id}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trading/order/{order_id}")
async def get_order_status(
    order_id: str = Path(..., description="주문 ID"),
) -> dict:
    """
    주문 상태 조회

    응답:
    ```json
    {
      "order_id": "ORD123456",
      "symbol": "005930",
      "side": "buy",
      "status": "filled",
      "filled_quantity": 10,
      "avg_price": 75000,
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        result = await gateway.get_order_status(order_id)

        return {
            "order_id": result.order_id,
            "symbol": result.symbol,
            "side": result.side.value,
            "status": result.status.value,
            "filled_quantity": result.filled_quantity,
            "avg_price": result.avg_price,
            "timestamp": result.timestamp.isoformat(),
        }
    except Exception as e:
        logger.error(f"주문 상태 조회 실패: {order_id}, {e}")
        raise HTTPException(status_code=500, detail=str(e))
