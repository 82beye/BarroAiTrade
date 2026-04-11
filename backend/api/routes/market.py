"""
시장 데이터 API 라우터

엔드포인트:
  GET  /api/market/ohlcv?symbol=005930&timeframe=5m&limit=100  - OHLCV 차트 데이터
  GET  /api/market/ticker/:symbol                               - 종목 시세 조회
  GET  /api/market/order-book/:symbol                          - 호가 조회
  GET  /api/market/universe                                     - 전종목 목록
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Path, Query, HTTPException

from backend.core.gateway.base import MarketGateway
from backend.models.market import OHLCV, Ticker, OrderBook
from backend.core.state import app_state

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_gateway() -> MarketGateway:
    """마켓 게이트웨이 인스턴스 반환"""
    gateway = app_state.market_gateway
    if not gateway:
        raise HTTPException(
            status_code=503,
            detail="마켓 게이트웨이 미초기화"
        )
    return gateway


@router.get("/market/ohlcv")
async def get_ohlcv(
    symbol: str = Query(..., description="종목 코드"),
    timeframe: str = Query("5m", description="봉 주기: 1m, 5m, 15m, 1h, 1d"),
    limit: int = Query(300, ge=1, le=1000, description="캔들 수"),
) -> dict:
    """
    OHLCV 차트 데이터 조회

    응답:
    ```json
    {
      "symbol": "005930",
      "timeframe": "5m",
      "limit": 300,
      "data": [
        {
          "timestamp": "2026-04-11T10:00:00Z",
          "open": 75000,
          "high": 75500,
          "low": 74500,
          "close": 75250,
          "volume": 1000000
        }
      ]
    }
    ```
    """
    try:
        gateway = _get_gateway()
        candles = await gateway.get_ohlcv(symbol, timeframe, limit)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": len(candles),
            "data": [
                {
                    "timestamp": c.timestamp.isoformat(),
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                }
                for c in candles
            ]
        }
    except Exception as e:
        logger.error(f"OHLCV 조회 실패: {symbol}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/ticker/{symbol}")
async def get_ticker(
    symbol: str = Path(..., description="종목 코드"),
) -> dict:
    """
    종목 시세 조회

    응답:
    ```json
    {
      "symbol": "005930",
      "name": "삼성전자",
      "price": 75000,
      "volume": 1000000,
      "change_pct": 0.5,
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        ticker = await gateway.get_ticker(symbol)

        return {
            "symbol": ticker.symbol,
            "name": ticker.name,
            "price": ticker.price,
            "volume": ticker.volume,
            "change_pct": ticker.change_pct,
            "timestamp": ticker.timestamp.isoformat(),
        }
    except Exception as e:
        logger.error(f"Ticker 조회 실패: {symbol}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/order-book/{symbol}")
async def get_order_book(
    symbol: str = Path(..., description="종목 코드"),
) -> dict:
    """
    호가 조회

    응답:
    ```json
    {
      "symbol": "005930",
      "asks": [[75500, 1000], [75600, 2000]],
      "bids": [[75000, 1000], [74900, 2000]],
      "timestamp": "2026-04-11T10:00:00Z"
    }
    ```
    """
    try:
        gateway = _get_gateway()
        order_book = await gateway.get_order_book(symbol)

        return {
            "symbol": order_book.symbol,
            "asks": order_book.asks,
            "bids": order_book.bids,
            "timestamp": order_book.timestamp.isoformat(),
        }
    except Exception as e:
        logger.error(f"Order book 조회 실패: {symbol}, {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/universe")
async def get_universe() -> dict:
    """
    전종목 목록 조회

    응답:
    ```json
    {
      "symbols": ["005930", "000660", "051910", ...],
      "count": 100
    }
    ```
    """
    try:
        gateway = _get_gateway()
        universe = await gateway.get_universe()

        return {
            "symbols": universe,
            "count": len(universe),
        }
    except Exception as e:
        logger.error(f"Universe 조회 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
