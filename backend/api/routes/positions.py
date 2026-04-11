"""
포지션 및 계좌 API 라우터

엔드포인트:
  GET /api/accounts/balance       - 잔고 조회
  GET /api/positions              - 포지션 목록 조회
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_gateway():
    """마켓 게이트웨이 인스턴스 반환"""
    gateway = app_state.market_gateway
    if not gateway:
        raise HTTPException(
            status_code=503,
            detail="마켓 게이트웨이 미초기화"
        )
    return gateway


@router.get("/accounts/balance")
async def get_balance() -> dict:
    """
    계좌 잔고 조회

    응답:
    ```json
    {
      "total_value": 10000000,
      "available_cash": 5000000,
      "invested_value": 5000000,
      "total_pnl": 500000,
      "total_pnl_pct": 5.0,
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        balance = await gateway.get_balance()

        logger.info(f"잔고 조회: {balance.total_value}")

        return {
            "total_value": balance.total_value,
            "available_cash": balance.available_cash,
            "invested_value": balance.invested_value,
            "total_pnl": balance.total_pnl,
            "total_pnl_pct": balance.total_pnl_pct,
            "timestamp": balance.updated_at.isoformat(),
        }
    except Exception as e:
        logger.error(f"잔고 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions(
    symbol: str = None,
) -> dict:
    """
    포지션 목록 조회

    응답:
    ```json
    {
      "positions": [
        {
          "symbol": "005930",
          "name": "삼성전자",
          "quantity": 10,
          "avg_price": 75000,
          "current_price": 75500,
          "realized_pnl": 0,
          "unrealized_pnl": 5000,
          "pnl_pct": 0.67,
          "entry_time": "2026-04-11T09:00:00Z"
        }
      ],
      "count": 1
    }
    ```
    """
    try:
        # TODO: 포지션 조회 구현
        # 현재는 모의 데이터 반환
        logger.info("포지션 목록 조회")

        return {
            "positions": [],
            "count": 0,
            "status": "mock",
        }
    except Exception as e:
        logger.error(f"포지션 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
